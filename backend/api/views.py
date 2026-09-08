"""HTTP surface.

Note what is missing from every queryset below: a filter on org. That is not
an oversight, it is the design. ``OrgContextMiddleware`` pins the database
session to the caller's org and Postgres row level security does the filtering,
so ``Exception.objects.all()`` already means "all of this org's exceptions".
An org filter here would be a second, weaker copy of the rule, and the moment
the two disagree the wrong one is the one people trust.

``tests/test_tenant_isolation.py`` is what makes that claim checkable.
"""
from django.contrib.auth import authenticate
from django.db.models import Count, Q
from django.http import Http404
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from grounded.service import answer_question
from reconciliation.models import Exception as ReconciliationException
from reconciliation.models import IngestRun, MatchNote, SourceEntryB, SourceRecordA
from reconciliation.reason_codes import NOTE_CODES, REASON_CODES
from tenancy.models import Location, OrgMembership

from .serializers import (
    ExceptionSerializer,
    LocationSerializer,
    MatchNoteSerializer,
    SourceEntryBSerializer,
    SourceRecordASerializer,
)


def _org_payload(user):
    membership = OrgMembership.objects.filter(user=user).select_related("org").first()
    if membership is None:
        return None
    return {"org_id": membership.org_id, "org_name": membership.org.name}


@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    username = (request.data or {}).get("username", "")
    password = (request.data or {}).get("password", "")
    user = authenticate(username=username, password=password)
    if user is None:
        return Response(
            {"detail": "Incorrect username or password."},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    org = _org_payload(user)
    if org is None:
        return Response(
            {"detail": "This user does not belong to an organisation."},
            status=status.HTTP_403_FORBIDDEN,
        )
    token, _ = Token.objects.get_or_create(user=user)
    return Response({"token": token.key, "username": user.username, **org})


@api_view(["GET"])
def me(request):
    org = _org_payload(request.user)
    if org is None:
        return Response(
            {"detail": "This user does not belong to an organisation."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return Response({"username": request.user.username, **org})


@api_view(["GET"])
def reason_codes(request):
    return Response(
        {
            "reason_codes": [
                {
                    "code": reason.code,
                    "label": reason.label,
                    "what_it_means": reason.what_it_means,
                    "what_to_do": reason.what_to_do,
                }
                for reason in REASON_CODES
            ]
        }
    )


@api_view(["GET"])
def locations(request):
    return Response({"locations": LocationSerializer(Location.objects.all(), many=True).data})


@api_view(["GET"])
def exceptions(request):
    queryset = ReconciliationException.objects.all()

    reason = request.query_params.getlist("reason_code") or None
    location = request.query_params.getlist("location_id") or None
    search = (request.query_params.get("search") or "").strip()

    if reason:
        queryset = queryset.filter(reason_code__in=reason)
    if location:
        queryset = queryset.filter(location_id__in=location)
    if search:
        queryset = queryset.filter(
            Q(record_ref__icontains=search)
            | Q(summary__icontains=search)
            | Q(location_name__icontains=search)
        )

    ordering = request.query_params.get("ordering") or "record_ref"
    allowed_ordering = {
        "record_ref",
        "-record_ref",
        "reason_code",
        "-reason_code",
        "event_date",
        "-event_date",
        "difference",
        "-difference",
        "location_id",
        "-location_id",
    }
    if ordering in allowed_ordering:
        queryset = queryset.order_by(ordering)

    # Counts come from the unfiltered (but still org-scoped) set so the filter
    # chips can show totals without a second round trip.
    all_for_org = ReconciliationException.objects.all()
    by_reason = {
        row["reason_code"]: row["n"]
        for row in all_for_org.values("reason_code").annotate(n=Count("id"))
    }
    by_location = {
        row["location_id"]: row["n"]
        for row in all_for_org.values("location_id").annotate(n=Count("id"))
    }

    return Response(
        {
            "count": queryset.count(),
            "total_for_org": all_for_org.count(),
            "counts_by_reason_code": by_reason,
            "counts_by_location_id": by_location,
            "results": ExceptionSerializer(queryset, many=True).data,
        }
    )


@api_view(["GET"])
def exception_detail(request, pk):
    # No org filter, and no need for one: if this id belongs to another org the
    # database does not return the row and Django raises a 404 for us.
    try:
        exception = ReconciliationException.objects.get(pk=pk)
    except ReconciliationException.DoesNotExist:
        raise Http404

    record = SourceRecordA.objects.filter(record_id=exception.record_ref).first()
    entries = SourceEntryB.objects.filter(entry_id__in=exception.entry_ids or [])
    return Response(
        {
            "exception": ExceptionSerializer(exception).data,
            "system_a_record": SourceRecordASerializer(record).data if record else None,
            "system_b_entries": SourceEntryBSerializer(entries, many=True).data,
        }
    )


@api_view(["GET"])
def summary(request):
    """Reconciliation summary, including the disagreements we judged benign.

    Exposed because "which non-errors did you correctly leave alone" is a
    question about this system that the exceptions list cannot answer.
    """
    run = IngestRun.objects.first()
    notes = MatchNote.objects.all()
    note_counts = {row["code"]: row["n"] for row in notes.values("code").annotate(n=Count("id"))}
    return Response(
        {
            "org_id": request.org_id,
            "exception_count": ReconciliationException.objects.count(),
            "system_a_records": SourceRecordA.objects.count(),
            "system_b_entries": SourceEntryB.objects.count(),
            "counts_by_reason_code": {
                row["reason_code"]: row["n"]
                for row in ReconciliationException.objects.values("reason_code").annotate(
                    n=Count("id")
                )
            },
            "not_errors": {
                "counts_by_code": note_counts,
                "explanations": {code: NOTE_CODES[code] for code in note_counts},
                "notes": MatchNoteSerializer(notes, many=True).data,
            },
            # When, and nothing else. IngestRun is the one table here that is
            # deliberately global — one run covers every org — so its stats
            # are cross-tenant totals: `exceptions: 12` tells a caller who
            # can see 5 that somebody else has 7, and `unattributable` is an
            # operator report about rows that belong to no org at all. Row
            # level security cannot catch this, because the table genuinely
            # has no org column to filter on. So the boundary here has to be
            # "do not serialise it", and the counts a caller is entitled to
            # are the org-scoped ones above.
            "last_ingest": {"finished_at": run.finished_at} if run else None,
        }
    )


@api_view(["POST"])
def ask(request):
    question = ((request.data or {}).get("question") or "").strip()
    if not question:
        return Response(
            {"detail": "Provide a 'question'."}, status=status.HTTP_400_BAD_REQUEST
        )
    result = answer_question(question, org_id=request.org_id)
    return Response(result, status=status.HTTP_200_OK)
