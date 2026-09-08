"""The only question surface that exists.

Everything a question can ask about is declared here. The planner (LLM or
otherwise) is allowed to emit references to these names and nothing else, and
``plan.validate`` rejects anything outside them. That is what makes the
endpoint hard to talk into lying: a question that needs a field which is not
in this table cannot be turned into a query at all, so the only available
behaviour is refusal.

Field descriptions give *format* examples (``REC-0000``) rather than examples
taken from the data. The prompt is the one place a real identifier could ride
along to a third-party model without belonging to the caller: `REC-1015` is
an ORG-A record, and it was in this file until a test asserted that nothing
resembling a row is ever sent. Allowed *values* still reach the model, but
those come from ``vocabulary()``, which is built per caller.
"""
from dataclasses import dataclass

from reconciliation.reason_codes import REASON_CODES

NUMERIC_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte"})
TEXT_OPS = frozenset({"eq", "ne", "in", "contains"})
DATE_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte"})


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str  # enum | text | number | date | bool
    ops: frozenset
    description: str
    # Django lookup path on reconciliation.Exception.
    column: str


FIELDS = {
    f.name: f
    for f in [
        FieldSpec(
            "reason_code",
            "enum",
            TEXT_OPS,
            "Why the row is an exception.",
            "reason_code",
        ),
        FieldSpec(
            "location_id",
            "enum",
            TEXT_OPS,
            "The location the exception belongs to. Format: LOC-000.",
            "location_id",
        ),
        FieldSpec(
            "location_name",
            "text",
            TEXT_OPS,
            "Human name of the location. Format: 'Location 000'.",
            "location_name",
        ),
        FieldSpec(
            "category_code",
            "enum",
            TEXT_OPS,
            "The System A category code. Format: CAT-00.",
            "category_code",
        ),
        FieldSpec(
            "record_ref",
            "text",
            TEXT_OPS,
            "The System A record id. Format: REC-0000.",
            "record_ref",
        ),
        FieldSpec(
            "event_date",
            "date",
            DATE_OPS,
            "Date of the event as System A recorded it (YYYY-MM-DD).",
            "event_date",
        ),
        FieldSpec(
            "system_a_amount",
            "number",
            NUMERIC_OPS,
            "The total System A recorded.",
            "system_a_amount",
        ),
        FieldSpec(
            "system_b_amount",
            "number",
            NUMERIC_OPS,
            "The total System B recorded.",
            "system_b_amount",
        ),
        FieldSpec(
            "difference",
            "number",
            NUMERIC_OPS,
            "System B amount minus System A amount. Negative means System B is short.",
            "difference",
        ),
        FieldSpec(
            "absolute_difference",
            "number",
            NUMERIC_OPS,
            "Size of the money gap regardless of direction.",
            "absolute_difference",
        ),
        FieldSpec(
            "crosses_org_boundary",
            "bool",
            frozenset({"eq"}),
            "True when the two systems put the event in different organisations.",
            "detail__crosses_org_boundary",
        ),
    ]
}

INTENTS = frozenset(
    {"count", "list", "sum", "average", "group_count", "group_sum", "max_by", "min_by"}
)

GROUPABLE = frozenset({"reason_code", "location_id", "category_code", "event_month"})

METRICS = frozenset(
    {"system_a_amount", "system_b_amount", "difference", "absolute_difference"}
)

METRIC_LABELS = {
    "system_a_amount": "System A amount",
    "system_b_amount": "System B amount",
    "difference": "difference (System B minus System A)",
    "absolute_difference": "absolute difference",
}

GROUP_LABELS = {
    "reason_code": "reason code",
    "location_id": "location",
    "category_code": "category",
    "event_month": "month",
}

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


def vocabulary(location_ids, category_codes):
    """The concrete values a plan is allowed to mention, for this caller.

    ``location_ids`` comes from the caller's own org, so a plan naming another
    org's location fails validation rather than quietly returning zero rows.
    """
    return {
        "reason_code": sorted(r.code for r in REASON_CODES),
        "location_id": sorted(location_ids),
        "category_code": sorted(category_codes),
    }


def describe_for_prompt(vocab):
    """Schema description handed to the LLM planner.

    Only names and allowed values go in here. No exception rows, no amounts,
    no counts: the model never sees the data it is writing a query about, so
    it has nothing to paraphrase and nothing to get wrong numerically.
    """
    lines = ["Fields you may filter on:"]
    for field in FIELDS.values():
        allowed = vocab.get(field.name)
        suffix = f" Allowed values: {', '.join(allowed)}." if allowed else ""
        lines.append(
            f"- {field.name} ({field.kind}, operators: "
            f"{', '.join(sorted(field.ops))}). {field.description}{suffix}"
        )
    lines += [
        "",
        f"Intents: {', '.join(sorted(INTENTS))}.",
        f"group_by options: {', '.join(sorted(GROUPABLE))}.",
        f"metric options (required for sum, average, max_by, min_by, group_sum): "
        f"{', '.join(sorted(METRICS))}.",
    ]
    return "\n".join(lines)
