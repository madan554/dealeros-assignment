"""Runs a validated plan and attaches the rows behind every number.

Numbers come out of Postgres, not out of a model. Each one is returned as a
``Figure`` carrying the identifiers of the rows that produced it, and for
aggregates, each row's own contribution, so the total can be re-added by hand.

Rows the metric is null for are excluded from aggregates *and* counted
separately, because "the total is X across 4 of the 7 matching exceptions" is
a true statement and "the total is X" on its own is not.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Avg, Count, Sum
from django.db.models.functions import Abs, TruncMonth

from reconciliation.models import Exception as ReconciliationException
from reconciliation.reason_codes import REASON_CODES_BY_CODE

from . import schema

MAX_CITATIONS = 100


@dataclass
class Figure:
    label: str
    value: object
    unit: str  # "exceptions" or "currency"
    citations: list = field(default_factory=list)
    citation_note: str = ""
    # "primary"   the number the question asked for
    # "breakdown" one group of a grouped answer
    # "context"   a row count the answer sentence mentions in passing
    #
    # The roles exist because the brief requires *every* number in the answer
    # to carry its rows, including the ones that only appear as context, like
    # "4 of the 7 exceptions". Without this they would be prose with no
    # citations behind them.
    role: str = "primary"

    def as_dict(self):
        value = self.value
        if isinstance(value, Decimal):
            value = str(value)
        return {
            "label": self.label,
            "value": value,
            "unit": self.unit,
            "role": self.role,
            "citations": self.citations,
            "citation_note": self.citation_note,
        }


@dataclass
class Execution:
    figures: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    matched_count: int = 0
    metric_null_count: int = 0
    groups: list = field(default_factory=list)


def _cite(row, contribution=None):
    citation = {
        "exception_id": row.id,
        "record_ref": row.record_ref,
        "reason_code": row.reason_code,
        "location_id": row.location_id,
        "entry_ids": row.entry_ids or [],
    }
    if contribution is not None:
        citation["contribution"] = str(contribution)
    return citation


def _row(row):
    return {
        "exception_id": row.id,
        "record_ref": row.record_ref,
        "reason_code": row.reason_code,
        "location_id": row.location_id,
        "location_name": row.location_name,
        "event_date": row.event_date.isoformat() if row.event_date else None,
        "system_a_amount": str(row.system_a_amount) if row.system_a_amount is not None else None,
        "system_b_amount": str(row.system_b_amount) if row.system_b_amount is not None else None,
        "difference": str(row.difference) if row.difference is not None else None,
        "entry_ids": row.entry_ids or [],
        "summary": row.summary,
    }


def build_queryset(plan):
    """No org filter here either: row level security has already narrowed the
    table to the caller's org before this queryset runs."""
    queryset = ReconciliationException.objects.annotate(
        absolute_difference=Abs("difference")
    )
    for item in plan.filters:
        column = schema.FIELDS[item.field].column
        if item.op == "eq":
            queryset = queryset.filter(**{column: item.value})
        elif item.op == "ne":
            queryset = queryset.exclude(**{column: item.value})
        elif item.op == "in":
            queryset = queryset.filter(**{f"{column}__in": item.value})
        elif item.op == "contains":
            queryset = queryset.filter(**{f"{column}__icontains": item.value})
        else:
            queryset = queryset.filter(**{f"{column}__{item.op}": item.value})
    return queryset


def _citations_for(rows, metric=None):
    citations = [
        _cite(row, getattr(row, metric) if metric else None) for row in rows[:MAX_CITATIONS]
    ]
    note = ""
    if len(rows) > MAX_CITATIONS:
        note = f"Showing the first {MAX_CITATIONS} of {len(rows)} rows."
    return citations, note


def execute(plan):
    queryset = build_queryset(plan)
    execution = Execution()

    if plan.intent in {"count", "list"}:
        rows = list(queryset.order_by("record_ref", "reason_code"))
        execution.matched_count = len(rows)
        execution.rows = [_row(row) for row in rows[: plan.limit]]
        citations, note = _citations_for(rows)
        execution.figures = [
            Figure(
                label="Matching exceptions",
                value=len(rows),
                unit="exceptions",
                citations=citations,
                citation_note=note,
            )
        ]
        return execution

    if plan.intent in {"sum", "average"}:
        metric = plan.metric
        all_rows = list(queryset.order_by("record_ref", "reason_code"))
        execution.matched_count = len(all_rows)
        contributing = [r for r in all_rows if getattr(r, metric) is not None]
        execution.metric_null_count = len(all_rows) - len(contributing)
        execution.rows = [_row(row) for row in all_rows[: plan.limit]]
        aggregate = queryset.aggregate(
            value=Sum(metric) if plan.intent == "sum" else Avg(metric)
        )["value"]
        if aggregate is None:
            aggregate = Decimal("0.00")
        aggregate = Decimal(aggregate).quantize(Decimal("0.01"))
        citations, note = _citations_for(contributing, metric=metric)
        label = schema.METRIC_LABELS[metric]
        execution.figures = [
            Figure(
                label=f"{'Total' if plan.intent == 'sum' else 'Average'} {label}",
                value=aggregate,
                unit="currency",
                citations=citations,
                citation_note=note,
            ),
            Figure(
                label=f"Exceptions included in the {'total' if plan.intent == 'sum' else 'average'}",
                value=len(contributing),
                unit="exceptions",
                citations=[_cite(row) for row in contributing[:MAX_CITATIONS]],
                role="context",
            ),
        ]
        if execution.metric_null_count:
            blanks = [r for r in all_rows if getattr(r, metric) is None]
            execution.figures.append(
                Figure(
                    label=f"Exceptions with no {label}",
                    value=len(blanks),
                    unit="exceptions",
                    citations=[_cite(row) for row in blanks[:MAX_CITATIONS]],
                    role="context",
                )
            )
        execution.figures.append(
            Figure(
                label="Matching exceptions",
                value=len(all_rows),
                unit="exceptions",
                citations=[_cite(row) for row in all_rows[:MAX_CITATIONS]],
                role="context",
            )
        )
        return execution

    if plan.intent in {"max_by", "min_by"}:
        metric = plan.metric
        ordering = f"-{metric}" if plan.intent == "max_by" else metric
        all_rows = list(queryset.filter(**{f"{metric}__isnull": False}).order_by(ordering))
        execution.matched_count = queryset.count()
        execution.metric_null_count = execution.matched_count - len(all_rows)
        if not all_rows:
            execution.figures = []
            return execution
        winner = all_rows[0]
        execution.rows = [_row(row) for row in all_rows[: plan.limit]]
        execution.figures = [
            Figure(
                label=f"{'Largest' if plan.intent == 'max_by' else 'Smallest'} "
                f"{schema.METRIC_LABELS[metric]}",
                value=getattr(winner, metric),
                unit="currency",
                citations=[_cite(winner, getattr(winner, metric))],
            )
        ]
        return execution

    # group_count / group_sum
    group = plan.group_by
    if group == "event_month":
        queryset = queryset.annotate(event_month=TruncMonth("event_date"))
        group_column = "event_month"
    else:
        group_column = schema.FIELDS[group].column

    aggregation = Count("id") if plan.intent == "group_count" else Sum(plan.metric)
    grouped = list(
        queryset.values(group_column).annotate(value=aggregation).order_by(group_column)
    )
    reverse = plan.order == "desc"
    grouped.sort(key=lambda item: (item["value"] is None, item["value"] or 0), reverse=reverse)

    all_rows = list(queryset.order_by("record_ref", "reason_code"))
    execution.matched_count = len(all_rows)
    execution.rows = [_row(row) for row in all_rows[: plan.limit]]

    rows_by_group = {}
    for row in all_rows:
        key = getattr(row, group_column, None)
        rows_by_group.setdefault(key, []).append(row)

    figures = []
    for item in grouped[: plan.limit]:
        key = item[group_column]
        member_rows = rows_by_group.get(key, [])
        metric = plan.metric if plan.intent == "group_sum" else None
        contributing = (
            [r for r in member_rows if getattr(r, metric) is not None] if metric else member_rows
        )
        citations, note = _citations_for(contributing, metric=metric)
        value = item["value"]
        if plan.intent == "group_sum":
            value = Decimal(value or 0).quantize(Decimal("0.01"))
        figures.append(
            Figure(
                label=_group_label(group, key),
                value=value,
                unit="exceptions" if plan.intent == "group_count" else "currency",
                citations=citations,
                citation_note=note,
                role="breakdown",
            )
        )
    execution.groups = [f.label for f in figures]
    # The answer sentence leads with the overall count, so it gets a figure of
    # its own rather than being an uncited number in prose.
    figures.append(
        Figure(
            label="Matching exceptions",
            value=len(all_rows),
            unit="exceptions",
            citations=[_cite(row) for row in all_rows[:MAX_CITATIONS]],
            role="context",
        )
    )
    execution.figures = figures
    return execution


def _group_label(group, key):
    """Group headings are user-facing, so a reason code becomes its plain
    English label. The raw code still travels on every citation for anyone
    who wants to filter on it."""
    if key in (None, ""):
        return "not set"
    if group == "event_month":
        return key.strftime("%B %Y")
    if group == "reason_code":
        reason = REASON_CODES_BY_CODE.get(key)
        return reason.label if reason else str(key)
    return str(key)
