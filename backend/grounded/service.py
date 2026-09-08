"""The grounded answer endpoint, end to end.

Order of operations, and why:

1. **Tenant guard on the raw question.** Asking about another org is refused
   before anything else happens. Row level security would already return zero
   rows, but "0" reads like an answer when it is really a boundary, and a
   confidently wrong zero is the failure mode this endpoint exists to avoid.
2. **Plan.** The question and the schema go to a planner. Data does not.
3. **Validate.** Every field, operator and value is checked against the schema
   and against this caller's vocabulary. See ``plan.validate``.
4. **Execute.** Postgres computes the numbers, under row level security.
5. **Narrate.** The sentence is assembled from templates in this file using
   those numbers. No model output is ever echoed into the answer.

Step 5 is the reason a prompt injection cannot make this endpoint state a
false figure. The most a hostile question can achieve is a differently-shaped
query or a refusal.
"""
import re

from reconciliation.models import Exception as ReconciliationException
from reconciliation.reason_codes import REASON_CODES_BY_CODE
from tenancy.models import Location

from . import schema
from .executor import execute
from .plan import CROSS_TENANT, Refusal, validate
from .planner import build_planner

# Matches an org being *named*: ORG-B, "org b", "organisation B", "orgs 2".
# The captured token is restricted to a single letter or a run of digits so
# that "how many does my org have" does not read "have" as an org id, and
# "cross the organisation boundary" does not read "boundary" as one. Anything
# looser produced false refusals on in-scope questions.
_ORG_REF = re.compile(
    r"\borg(?:anisation|anization)?s?\b[\s\-_:']*(?:id\s*)?([a-z]|\d+)\b",
    re.IGNORECASE,
)
_LOCATION_TOKEN = re.compile(r"\bloc[\s\-_]?(\d{2,})\b", re.IGNORECASE)
_LOCATION_WORD = re.compile(r"\blocation\s+(\d{2,})\b", re.IGNORECASE)

# Phrases that ask to see across the boundary rather than naming an org.
_BOUNDARY_PHRASES = [
    "other org",
    "another org",
    "all orgs",
    "all organisation",
    "all organization",
    "every org",
    "both orgs",
    "other tenant",
    "another tenant",
    "all tenants",
    "every tenant",
    "other dealer",
    "across orgs",
    "across organisation",
    "across organization",
    "company wide",
    "group wide",
    "everyone else",
    "other companies",
    "other company",
]

CROSS_TENANT_MESSAGE = (
    "That question is about data outside your organisation. I only have access to "
    "{org_id}'s exceptions, so rather than answer with a number that looks like "
    "zero I am telling you I cannot see it."
)


def _vocabulary(org_id):
    """What this caller is allowed to name.

    Both queries run under the caller's org context, so the vocabulary is
    itself tenant-scoped: another org's location ids never enter it.
    """
    location_ids = list(Location.objects.values_list("id", flat=True))
    category_codes = [
        code
        for code in ReconciliationException.objects.values_list("category_code", flat=True).distinct()
        if code
    ]
    return schema.vocabulary(location_ids, category_codes)


def tenant_guard(question, org_id, vocab):
    """Refuse questions that reach outside the caller's org.

    Deliberately conservative: it would rather refuse a clumsily-worded
    in-scope question than answer an out-of-scope one.
    """
    lowered = question.lower()

    for phrase in _BOUNDARY_PHRASES:
        if phrase in lowered:
            return Refusal(CROSS_TENANT, CROSS_TENANT_MESSAGE.format(org_id=org_id))

    own_suffix = org_id.split("-")[-1].lower() if "-" in org_id else org_id.lower()
    for match in _ORG_REF.finditer(question):
        token = match.group(1).lower()
        if token == "s":
            continue  # the possessive in "org's", not an identifier
        if token != own_suffix and token != org_id.lower():
            return Refusal(CROSS_TENANT, CROSS_TENANT_MESSAGE.format(org_id=org_id))

    allowed_locations = {loc.lower() for loc in vocab.get("location_id", [])}
    for pattern in (_LOCATION_TOKEN, _LOCATION_WORD):
        for match in pattern.finditer(question):
            candidate = f"loc-{match.group(1)}"
            if candidate not in allowed_locations:
                return Refusal(
                    CROSS_TENANT,
                    f"Location {match.group(1)} is not one of {org_id}'s locations, so I "
                    "cannot see anything about it.",
                )
    return None


# --------------------------------------------------------------------------
# Narration
# --------------------------------------------------------------------------


def _reason_label(code):
    reason = REASON_CODES_BY_CODE.get(code)
    return f"“{reason.label}”" if reason else code


_OP_WORDS = {
    "eq": "is",
    "ne": "is not",
    "in": "is one of",
    "contains": "contains",
    "gt": "is above",
    "gte": "is at least",
    "lt": "is below",
    "lte": "is at most",
}


def describe_filters(plan):
    if not plan.filters:
        return ""
    parts = []
    for item in plan.filters:
        if item.field == "reason_code" and item.op == "eq":
            parts.append(f"with reason code {_reason_label(item.value)}")
        elif item.field == "location_id" and item.op == "eq":
            parts.append(f"at {item.value}")
        elif item.field == "record_ref" and item.op == "eq":
            parts.append(f"for record {item.value}")
        elif item.field == "category_code" and item.op == "eq":
            parts.append(f"in category {item.value}")
        elif item.field == "crosses_org_boundary" and item.op == "eq":
            parts.append(
                "that cross the organisation boundary"
                if item.value
                else "that stay inside one organisation"
            )
        else:
            value = item.value
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            parts.append(
                f"where {item.field.replace('_', ' ')} {_OP_WORDS.get(item.op, item.op)} {value}"
            )
    return " " + " and ".join(parts)


def _money(value):
    return f"{value:,.2f}"


def narrate(plan, execution):
    """Build the answer sentence from executed numbers only."""
    where = describe_filters(plan)
    count = execution.matched_count

    if plan.intent in {"count", "list"}:
        if count == 0:
            return f"No exceptions{where}."
        noun = "exception" if count == 1 else "exceptions"
        sentence = f"{count} {noun}{where}."
        if plan.intent == "list" and count > len(execution.rows):
            sentence += f" The first {len(execution.rows)} are listed below."
        return sentence

    if plan.intent in {"sum", "average"}:
        word = "total" if plan.intent == "sum" else "average"
        if count == 0:
            return f"No exceptions{where}, so there is nothing to {word}."
        figure = execution.figures[0]
        label = schema.METRIC_LABELS[plan.metric]
        contributing = count - execution.metric_null_count
        sentence = (
            f"The {word} {label} across {contributing} of the {count} "
            f"{'exception' if count == 1 else 'exceptions'}{where} is "
            f"{_money(figure.value)}."
        )
        if execution.metric_null_count:
            sentence += (
                f" {execution.metric_null_count} "
                f"{'exception has' if execution.metric_null_count == 1 else 'exceptions have'} "
                f"no {label}, so {'it is' if execution.metric_null_count == 1 else 'they are'} "
                f"excluded from the {word} rather than counted as zero."
            )
        return sentence

    if plan.intent in {"max_by", "min_by"}:
        if not execution.figures:
            return f"No exceptions{where} have a {schema.METRIC_LABELS[plan.metric]}."
        figure = execution.figures[0]
        citation = figure.citations[0]
        word = "largest" if plan.intent == "max_by" else "smallest"
        return (
            f"The {word} {schema.METRIC_LABELS[plan.metric]}{where} is "
            f"{_money(figure.value)}, on {citation['record_ref']} "
            f"({_reason_label(citation['reason_code'])} at {citation['location_id']})."
        )

    # group_count / group_sum
    label = schema.GROUP_LABELS.get(plan.group_by, plan.group_by)
    if count == 0:
        return f"No exceptions{where}, so there is nothing to break down by {label}."
    groups = [f for f in execution.figures if f.role == "breakdown"]
    if plan.intent == "group_count":
        parts = [f"{f.label}: {f.value}" for f in groups]
        lead = f"{count} {'exception' if count == 1 else 'exceptions'}{where}"
        return f"{lead}, by {label} — {'; '.join(parts)}."
    parts = [f"{f.label}: {_money(f.value)}" for f in groups]
    metric_label = schema.METRIC_LABELS[plan.metric]
    return f"{metric_label.capitalize()} by {label}{where} — {'; '.join(parts)}."


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _refusal_response(question, refusal, planner_name, plan=None):
    return {
        "question": question,
        "answered": False,
        "answer": None,
        "refusal": {"code": refusal.code, "message": refusal.message},
        "figures": [],
        "rows": [],
        "plan": plan.as_dict() if plan else None,
        "planner": planner_name,
        "grounding": {
            "source": "reconciliation_exception",
            "note": "No figures were produced, so there is nothing to cite.",
        },
    }


def answer_question(question, org_id):
    planner = build_planner()

    if not org_id:
        return _refusal_response(
            question,
            Refusal(
                CROSS_TENANT,
                "You are not attached to an organisation, so there is no exceptions "
                "list for me to read.",
            ),
            planner.name,
        )

    vocab = _vocabulary(org_id)

    guard = tenant_guard(question, org_id, vocab)
    if guard is not None:
        return _refusal_response(question, guard, planner.name)

    outcome = planner.plan(question, vocab)
    if isinstance(outcome, Refusal):
        return _refusal_response(question, outcome, planner.name)

    plan, refusal = validate(outcome, vocab)
    if refusal is not None:
        return _refusal_response(question, refusal, planner.name, plan=outcome)

    execution = execute(plan)
    answer = narrate(plan, execution)

    return {
        "question": question,
        "answered": True,
        "answer": answer,
        "refusal": None,
        "figures": [figure.as_dict() for figure in execution.figures],
        "rows": execution.rows,
        "plan": plan.as_dict(),
        "planner": planner.name,
        "grounding": {
            "source": "reconciliation_exception",
            "org_id": org_id,
            "matched_rows": execution.matched_count,
            "rows_without_metric": execution.metric_null_count,
            "note": "Every figure above was computed by Postgres from the cited rows. "
            "The wording is assembled from templates; no model output is included "
            "in the answer text.",
        },
    }
