"""Query plans, refusals, and the validator that stands between them.

The validator is the security boundary of this feature. A planner is untrusted
input: the LLM one because it is a language model, the deterministic one
because it is code someone will edit later. Nothing reaches the database until
it has been checked, field by field and value by value, against
``grounded.schema``.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from . import schema

# Refusal codes. Distinct on purpose: "I could not understand you" and "your
# question is about data I do not hold" call for different follow-up.
CROSS_TENANT = "CROSS_TENANT"
NOT_UNDERSTOOD = "NOT_UNDERSTOOD"
UNSUPPORTED_FIELD = "UNSUPPORTED_FIELD"
UNSUPPORTED_VALUE = "UNSUPPORTED_VALUE"
UNSUPPORTED_INTENT = "UNSUPPORTED_INTENT"
NOT_IN_DATA = "NOT_IN_DATA"
PLANNER_UNAVAILABLE = "PLANNER_UNAVAILABLE"
MALFORMED_PLAN = "MALFORMED_PLAN"


@dataclass
class Refusal:
    code: str
    message: str


@dataclass
class Filter:
    field: str
    op: str
    value: object

    def as_dict(self):
        value = self.value
        if isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, list):
            value = [str(v) if isinstance(v, Decimal) else v for v in value]
        return {"field": self.field, "op": self.op, "value": value}


@dataclass
class QueryPlan:
    intent: str
    filters: list = field(default_factory=list)
    group_by: str = None
    metric: str = None
    order: str = "desc"
    limit: int = schema.DEFAULT_LIMIT

    def as_dict(self):
        return {
            "intent": self.intent,
            "filters": [f.as_dict() for f in self.filters],
            "group_by": self.group_by,
            "metric": self.metric,
            "order": self.order,
            "limit": self.limit,
        }


def _coerce(spec, raw):
    """Turn a planner-supplied value into the right Python type, or fail."""
    if spec.kind == "number":
        try:
            return Decimal(str(raw).replace(",", "").strip())
        except (InvalidOperation, ValueError, AttributeError):
            raise ValueError(f"{raw!r} is not a number")
    if spec.kind == "date":
        text = str(raw).strip()
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"{raw!r} is not a date in YYYY-MM-DD form")
    if spec.kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in {"true", "yes", "1"}:
            return True
        if text in {"false", "no", "0"}:
            return False
        raise ValueError(f"{raw!r} is not true or false")
    return str(raw).strip()


def validate(plan: QueryPlan, vocab: dict):
    """Returns ``(plan, None)`` or ``(None, Refusal)``.

    Values for enum fields are checked against ``vocab``, which is built from
    the caller's own org. A plan naming another org's location is refused
    rather than executed, because executing it would return an empty result
    and "0" reads like an answer when it is really a boundary.
    """
    if not isinstance(plan, QueryPlan):
        return None, Refusal(MALFORMED_PLAN, "The planner did not return a usable query.")

    if plan.intent not in schema.INTENTS:
        return None, Refusal(
            UNSUPPORTED_INTENT,
            f"I cannot do '{plan.intent}' over the exceptions list.",
        )

    if plan.group_by is not None and plan.group_by not in schema.GROUPABLE:
        return None, Refusal(
            UNSUPPORTED_FIELD,
            f"I cannot group the exceptions by '{plan.group_by}'. I can group by "
            f"{', '.join(sorted(schema.GROUPABLE))}.",
        )

    needs_metric = plan.intent in {"sum", "average", "group_sum", "max_by", "min_by"}
    if needs_metric:
        if plan.metric not in schema.METRICS:
            return None, Refusal(
                UNSUPPORTED_FIELD,
                f"To answer that I would need to total a field I do not hold "
                f"({plan.metric!r}). I hold {', '.join(sorted(schema.METRICS))}.",
            )
    elif plan.metric is not None and plan.metric not in schema.METRICS:
        return None, Refusal(
            UNSUPPORTED_FIELD, f"I do not hold a field called {plan.metric!r}."
        )

    if plan.intent in {"group_count", "group_sum"} and plan.group_by is None:
        return None, Refusal(
            MALFORMED_PLAN, "A breakdown was requested without saying what to break it down by."
        )

    if plan.order not in {"asc", "desc"}:
        plan.order = "desc"

    try:
        plan.limit = max(1, min(int(plan.limit), schema.MAX_LIMIT))
    except (TypeError, ValueError):
        plan.limit = schema.DEFAULT_LIMIT

    checked = []
    for item in plan.filters:
        if not isinstance(item, Filter):
            return None, Refusal(MALFORMED_PLAN, "A filter in the query was malformed.")
        spec = schema.FIELDS.get(item.field)
        if spec is None:
            return None, Refusal(
                UNSUPPORTED_FIELD,
                f"The exceptions list has no field called {item.field!r}, so I cannot "
                "answer that from this data.",
            )
        if item.op not in spec.ops:
            return None, Refusal(
                UNSUPPORTED_FIELD,
                f"I cannot apply '{item.op}' to {item.field}.",
            )

        values = item.value if item.op == "in" else [item.value]
        if not isinstance(values, list):
            values = [values]
        if not values:
            return None, Refusal(MALFORMED_PLAN, f"No value was given for {item.field}.")

        coerced = []
        for raw in values:
            try:
                value = _coerce(spec, raw)
            except ValueError as error:
                return None, Refusal(UNSUPPORTED_VALUE, f"For {item.field}: {error}.")
            allowed = vocab.get(item.field)
            if spec.kind == "enum" and allowed is not None and value not in allowed:
                return None, Refusal(
                    UNSUPPORTED_VALUE,
                    f"{value!r} is not a {item.field} I can see. Available: "
                    f"{', '.join(allowed) if allowed else 'none'}.",
                )
            coerced.append(value)

        checked.append(
            Filter(item.field, item.op, coerced if item.op == "in" else coerced[0])
        )

    plan.filters = checked
    return plan, None
