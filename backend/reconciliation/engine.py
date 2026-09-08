"""The reconciliation engine.

Deliberately pure: dataclasses in, dataclasses out, no Django and no database.
That means the interesting logic can be tested against tiny hand-written
fixtures as well as against the real CSVs, and the golden test in
``tests/test_reconciliation_golden.py`` reads as a specification.

Two ideas drive the classification:

1. Normalise first, then compare. A reference written ``rec1034`` and an amount
   written ``1,25,400.00`` are formatting problems, not disagreements. Fixing
   the format is our job; if a real difference survives normalisation, *that*
   is the exception.

2. A disagreement is only an exception if a human would have to do something
   about it. System B splitting a record into two entries that add up to the
   System A total is a different shape, not a different answer, so it is
   recorded as a note and kept out of the exceptions list.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from . import reason_codes as rc

# Amounts are money to two decimal places. Anything smaller than a cent is
# rounding noise from one of the exports, not a disagreement worth a human's
# afternoon.
AMOUNT_TOLERANCE = Decimal("0.01")

STATE_CONFIRMED = "CONFIRMED"


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass
class RecordA:
    record_id: str
    location_id: str
    event_date: Optional[date]
    category_code: str
    actor_id: str
    base_value: Optional[Decimal]
    adjustment: Optional[Decimal]
    total_value: Optional[Decimal]
    state: str
    row_number: int = 0
    parse_notes: list[str] = field(default_factory=list)


@dataclass
class EntryB:
    entry_id: str
    raw_record_ref: str
    record_ref: Optional[str]  # normalised, or None if unreadable
    location_id: str
    recorded_on: Optional[date]
    raw_value: str
    value: Optional[Decimal]
    label: str
    row_number: int = 0
    reference_was_normalised: bool = False
    amount_format_was_normalised: bool = False
    parse_notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------


@dataclass
class Exception_:
    """One actionable problem, owned by exactly one org."""

    org_id: str
    record_ref: str
    reason_code: str
    summary: str
    location_id: str = ""
    location_name: str = ""
    event_date: Optional[date] = None
    category_code: str = ""
    system_a_amount: Optional[Decimal] = None
    system_b_amount: Optional[Decimal] = None
    difference: Optional[Decimal] = None
    entry_ids: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


@dataclass
class Note:
    """A disagreement we looked at and decided was not a problem."""

    org_id: str
    record_ref: str
    code: str
    summary: str
    entry_ids: list[str] = field(default_factory=list)


@dataclass
class Unattributable:
    """A row we cannot tie to any org.

    It cannot go in an exceptions list: every list belongs to a tenant, and
    guessing would mean showing one tenant another tenant's row. Surfaced in
    the ingest report for an operator instead.
    """

    kind: str
    identifier: str
    reason: str


@dataclass
class ReconciliationResult:
    exceptions: list[Exception_] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    unattributable: list[Unattributable] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

_REF_WITH_PREFIX = re.compile(r"^REC-?(\d+)$")
_REF_BARE_NUMBER = re.compile(r"^(\d+)$")


def normalise_reference(raw: str) -> tuple[Optional[str], bool]:
    """Turn a System B ``record_ref`` into ``REC-<digits>``.

    Handles the damage present in the export: case (``rec1034``), stray
    whitespace inside and around the token (``" REC - 1070 "``), a bare number
    with the prefix dropped (``1112``), and underscores in place of the hyphen.

    Returns ``(normalised_or_None, was_changed)``. Anything that does not
    reduce to exactly one record number returns None rather than a guess: a
    wrong match is worse than an unmatched row, because a wrong match can
    attribute a row to the wrong tenant.
    """
    original = raw or ""
    collapsed = re.sub(r"\s+", "", original).upper().replace("_", "-")
    normalised = None
    if match := _REF_WITH_PREFIX.match(collapsed):
        normalised = f"REC-{match.group(1)}"
    elif match := _REF_BARE_NUMBER.match(collapsed):
        # A bare number is only meaningful because System A record ids are
        # uniformly REC-<number>. Recorded as a note so the assumption is
        # visible rather than buried.
        normalised = f"REC-{match.group(1)}"
    if normalised is None:
        return None, False
    return normalised, normalised != original


def normalise_amount(raw: str) -> tuple[Optional[Decimal], bool]:
    """Parse a System B amount, tolerating grouping separators.

    ``1,25,400.00`` is Indian digit grouping; stripping separators is the only
    safe reading. Returns ``(value_or_None, was_reformatted)``.
    """
    original = (raw or "").strip()
    if not original:
        return None, False
    cleaned = original.replace(",", "").replace("\u00a0", "").replace(" ", "")
    cleaned = cleaned.lstrip("$£€")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None, False
    return value, cleaned != original


def normalise_date(raw: str) -> Optional[date]:
    text = (raw or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def money(value: Optional[Decimal]) -> str:
    if value is None:
        return "no amount"
    return f"{value:,.2f}"


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------


def _duplicate_groups(entries: list[EntryB]) -> list[list[EntryB]]:
    """Entries that are indistinguishable from each other: same amount, same
    date, same location. Two of those are a double-count, not a split."""
    buckets: dict[tuple, list[EntryB]] = defaultdict(list)
    for entry in entries:
        buckets[(entry.value, entry.recorded_on, entry.location_id)].append(entry)
    return [group for group in buckets.values() if len(group) > 1]


def reconcile(
    records: Iterable[RecordA],
    entries: Iterable[EntryB],
    location_org: dict[str, str],
    location_names: Optional[dict[str, str]] = None,
) -> ReconciliationResult:
    """Match System A records to System B entries and classify what is left.

    ``location_org`` is the locations.csv mapping and is the only source of
    tenant ownership. An org is never inferred from anything else.
    """
    location_names = location_names or {}
    records = list(records)
    entries = list(entries)
    result = ReconciliationResult()

    records_by_id: dict[str, RecordA] = {}
    for record in records:
        records_by_id[record.record_id] = record

    entries_by_ref: dict[Optional[str], list[EntryB]] = defaultdict(list)
    for entry in entries:
        entries_by_ref[entry.record_ref].append(entry)

    def org_of(location_id: str) -> Optional[str]:
        return location_org.get(location_id)

    def name_of(location_id: str) -> str:
        return location_names.get(location_id, location_id)

    def add(exception: Exception_) -> None:
        result.exceptions.append(exception)

    matched_entry_ids: set[str] = set()

    # ---- System A's side of the join ------------------------------------
    for record in records:
        org_id = org_of(record.location_id)
        if org_id is None:
            result.unattributable.append(
                Unattributable(
                    kind="system_a_record",
                    identifier=record.record_id,
                    reason=f"location {record.location_id} is not in locations.csv, "
                    "so the record cannot be attributed to an org",
                )
            )
            continue

        record_entries = entries_by_ref.get(record.record_id, [])
        for entry in record_entries:
            matched_entry_ids.add(entry.entry_id)

        base = dict(
            org_id=org_id,
            record_ref=record.record_id,
            location_id=record.location_id,
            location_name=name_of(record.location_id),
            event_date=record.event_date,
            category_code=record.category_code,
            system_a_amount=record.total_value,
            entry_ids=[e.entry_id for e in record_entries],
        )

        # A voided record with no entry is the system working correctly.
        if not record_entries:
            if record.state != STATE_CONFIRMED:
                result.notes.append(
                    Note(
                        org_id=org_id,
                        record_ref=record.record_id,
                        code=rc.NOTE_VOIDED_AND_ABSENT,
                        summary=f"{record.record_id} is {record.state.lower()} in System A "
                        "and System B has no entry for it, which is correct.",
                    )
                )
                continue
            add(
                Exception_(
                    **base,
                    reason_code=rc.MISSING_IN_SYSTEM_B,
                    summary=(
                        f"System A recorded {record.record_id} for {money(record.total_value)} "
                        f"on {record.event_date} at {name_of(record.location_id)}, but System B "
                        "has no entry for it."
                    ),
                    detail={"system_a_state": record.state},
                )
            )
            continue

        # A voided record that still has an entry is one problem, and reporting
        # the amount and date drift on top would bury it.
        if record.state != STATE_CONFIRMED:
            add(
                Exception_(
                    **base,
                    reason_code=rc.VOIDED_RECORD_STILL_IN_SYSTEM_B,
                    summary=(
                        f"System A voided {record.record_id}, but System B still holds "
                        f"{len(record_entries)} entry for it worth "
                        f"{money(sum((e.value or Decimal(0)) for e in record_entries))}."
                    ),
                    system_b_amount=sum((e.value or Decimal(0)) for e in record_entries),
                    detail={"system_a_state": record.state},
                )
            )
            continue

        # ---- Location -----------------------------------------------------
        for entry in record_entries:
            if entry.location_id == record.location_id:
                continue
            entry_org = org_of(entry.location_id)
            crosses = entry_org is not None and entry_org != org_id
            add(
                Exception_(
                    **{**base, "entry_ids": [entry.entry_id]},
                    reason_code=rc.LOCATION_MISMATCH,
                    summary=(
                        f"System A puts {record.record_id} at {name_of(record.location_id)} "
                        f"but System B entry {entry.entry_id} puts it at "
                        f"{name_of(entry.location_id)}."
                        + (
                            " Those two locations belong to different organisations, so one "
                            "of the systems has this event on the wrong side of the boundary."
                            if crosses
                            else ""
                        )
                    ),
                    detail={
                        "system_a_location": record.location_id,
                        "system_b_location": entry.location_id,
                        "crosses_org_boundary": crosses,
                    },
                )
            )

        # ---- Date ---------------------------------------------------------
        for entry in record_entries:
            if entry.recorded_on is None:
                continue
            if entry.recorded_on == record.event_date:
                continue
            days = (
                abs((entry.recorded_on - record.event_date).days)
                if record.event_date
                else None
            )
            different_month = record.event_date is not None and (
                (entry.recorded_on.year, entry.recorded_on.month)
                != (record.event_date.year, record.event_date.month)
            )
            add(
                Exception_(
                    **{**base, "entry_ids": [entry.entry_id]},
                    reason_code=rc.EVENT_DATE_MISMATCH,
                    summary=(
                        f"System A dates {record.record_id} {record.event_date} but "
                        f"System B entry {entry.entry_id} dates it {entry.recorded_on}"
                        + (
                            f", {days} days later. The two dates fall in different months, "
                            "so the two systems will report this event in different periods."
                            if different_month
                            else f", {days} days apart."
                        )
                    ),
                    detail={
                        "system_a_date": str(record.event_date),
                        "system_b_date": str(entry.recorded_on),
                        "days_apart": days,
                        "different_reporting_month": different_month,
                    },
                )
            )

        # ---- Amounts ------------------------------------------------------
        blank = [e for e in record_entries if e.value is None]
        if blank:
            add(
                Exception_(
                    **{**base, "entry_ids": [e.entry_id for e in blank]},
                    reason_code=rc.AMOUNT_MISSING_IN_SYSTEM_B,
                    summary=(
                        f"System B entry {blank[0].entry_id} for {record.record_id} has no "
                        f"amount, so the {money(record.total_value)} System A recorded cannot "
                        "be checked."
                    ),
                    detail={"blank_entry_ids": [e.entry_id for e in blank]},
                )
            )
            continue

        if len(record_entries) == 1:
            entry = record_entries[0]
            _classify_single_amount(record, entry, base, add, result, name_of)
        else:
            _classify_multi_amount(record, record_entries, base, add, result)

        if any(e.reference_was_normalised for e in record_entries):
            changed = [e for e in record_entries if e.reference_was_normalised]
            result.notes.append(
                Note(
                    org_id=org_id,
                    record_ref=record.record_id,
                    code=rc.NOTE_REFERENCE_NORMALISED,
                    summary="; ".join(
                        f"{e.entry_id} referenced this record as {e.raw_record_ref!r}, "
                        f"read as {record.record_id}"
                        for e in changed
                    ),
                    entry_ids=[e.entry_id for e in changed],
                )
            )
        if any(e.amount_format_was_normalised for e in record_entries):
            changed = [e for e in record_entries if e.amount_format_was_normalised]
            result.notes.append(
                Note(
                    org_id=org_id,
                    record_ref=record.record_id,
                    code=rc.NOTE_AMOUNT_FORMAT_NORMALISED,
                    summary="; ".join(
                        f"{e.entry_id} wrote the amount as {e.raw_value!r}, read as "
                        f"{money(e.value)}"
                        for e in changed
                    ),
                    entry_ids=[e.entry_id for e in changed],
                )
            )

    # ---- System B's side of the join ------------------------------------
    for ref, ref_entries in entries_by_ref.items():
        if ref is not None and ref in records_by_id:
            continue
        for entry in ref_entries:
            org_id = org_of(entry.location_id)
            if org_id is None:
                result.unattributable.append(
                    Unattributable(
                        kind="system_b_entry",
                        identifier=entry.entry_id,
                        reason=f"location {entry.location_id} is not in locations.csv, "
                        "so the entry cannot be attributed to an org",
                    )
                )
                continue
            if ref is None:
                add(
                    Exception_(
                        org_id=org_id,
                        record_ref=entry.raw_record_ref.strip() or entry.entry_id,
                        reason_code=rc.UNREADABLE_REFERENCE_IN_SYSTEM_B,
                        summary=(
                            f"System B entry {entry.entry_id} for {money(entry.value)} at "
                            f"{name_of(entry.location_id)} has the record reference "
                            f"{entry.raw_record_ref!r}, which does not identify any record."
                        ),
                        location_id=entry.location_id,
                        location_name=name_of(entry.location_id),
                        event_date=entry.recorded_on,
                        system_b_amount=entry.value,
                        entry_ids=[entry.entry_id],
                        detail={"raw_record_ref": entry.raw_record_ref},
                    )
                )
            else:
                add(
                    Exception_(
                        org_id=org_id,
                        record_ref=ref,
                        reason_code=rc.UNKNOWN_RECORD_IN_SYSTEM_B,
                        summary=(
                            f"System B entry {entry.entry_id} claims {money(entry.value)} "
                            f"against record {ref} at {name_of(entry.location_id)}, but "
                            "System A has no such record."
                        ),
                        location_id=entry.location_id,
                        location_name=name_of(entry.location_id),
                        event_date=entry.recorded_on,
                        system_b_amount=entry.value,
                        entry_ids=[entry.entry_id],
                        detail={"raw_record_ref": entry.raw_record_ref},
                    )
                )

    result.stats = {
        "system_a_records": len(records),
        "system_b_entries": len(entries),
        "matched_entries": len(matched_entry_ids),
        "exceptions": len(result.exceptions),
        "notes": len(result.notes),
        "unattributable": len(result.unattributable),
    }
    return result


def _classify_single_amount(record, entry, base, add, result, name_of):
    difference = entry.value - record.total_value
    if abs(difference) < AMOUNT_TOLERANCE:
        if difference != 0:
            result.notes.append(
                Note(
                    org_id=base["org_id"],
                    record_ref=record.record_id,
                    code=rc.NOTE_ROUNDING_WITHIN_TOLERANCE,
                    summary=f"{entry.entry_id} differs from System A by {money(difference)}, "
                    "which is rounding.",
                    entry_ids=[entry.entry_id],
                )
            )
        return

    # The difference being exactly the adjustment is a much more useful thing
    # to tell someone than "the numbers differ": it names the cause and it
    # points at the export rather than at the record.
    if record.base_value is not None and entry.value == record.base_value:
        add(
            Exception_(
                **{**base, "entry_ids": [entry.entry_id]},
                reason_code=rc.ADJUSTMENT_MISSING_IN_SYSTEM_B,
                summary=(
                    f"System B entry {entry.entry_id} records {money(entry.value)} for "
                    f"{record.record_id}, which is the amount before the "
                    f"{money(record.adjustment)} adjustment. System A's total is "
                    f"{money(record.total_value)}."
                ),
                system_b_amount=entry.value,
                difference=difference,
                detail={
                    "system_a_base": str(record.base_value),
                    "system_a_adjustment": str(record.adjustment),
                    "shortfall_equals_adjustment": True,
                },
            )
        )
        return

    add(
        Exception_(
            **{**base, "entry_ids": [entry.entry_id]},
            reason_code=rc.AMOUNT_MISMATCH,
            summary=(
                f"System A has {money(record.total_value)} for {record.record_id} but "
                f"System B entry {entry.entry_id} has {money(entry.value)}, a difference "
                f"of {money(difference)}."
            ),
            system_b_amount=entry.value,
            difference=difference,
            detail={
                "system_a_base": str(record.base_value),
                "system_a_adjustment": str(record.adjustment),
                "system_b_raw_value": entry.raw_value,
            },
        )
    )


def _classify_multi_amount(record, record_entries, base, add, result):
    """More than one System B entry against one System A record.

    Order matters here. The same amount recorded twice is checked first,
    because two entries that each carry the full total also happen to be
    "more than one entry", and calling that a split would hide a double-count.
    """
    total = sum((e.value for e in record_entries), Decimal(0))
    duplicates = _duplicate_groups(record_entries)
    full_amount_duplicates = [
        group for group in duplicates if group[0].value == record.total_value
    ]

    if full_amount_duplicates:
        group = full_amount_duplicates[0]
        add(
            Exception_(
                **{**base, "entry_ids": [e.entry_id for e in group]},
                reason_code=rc.DUPLICATE_ENTRY_IN_SYSTEM_B,
                summary=(
                    f"System B holds {len(group)} identical entries of {money(group[0].value)} "
                    f"for {record.record_id} ({', '.join(e.entry_id for e in group)}), so the "
                    f"amount is counted {len(group)} times instead of once."
                ),
                system_b_amount=total,
                difference=total - record.total_value,
                detail={"duplicate_entry_ids": [e.entry_id for e in group]},
            )
        )
        return

    if abs(total - record.total_value) < AMOUNT_TOLERANCE:
        result.notes.append(
            Note(
                org_id=base["org_id"],
                record_ref=record.record_id,
                code=rc.NOTE_SPLIT_ENTRIES_RECONCILED,
                summary=(
                    f"System B split {record.record_id} across {len(record_entries)} entries "
                    f"({', '.join(e.entry_id for e in record_entries)}) which add up to "
                    f"{money(total)}, matching System A. Not an error."
                ),
                entry_ids=[e.entry_id for e in record_entries],
            )
        )
        return

    if duplicates:
        group = duplicates[0]
        add(
            Exception_(
                **{**base, "entry_ids": [e.entry_id for e in group]},
                reason_code=rc.DUPLICATE_ENTRY_IN_SYSTEM_B,
                summary=(
                    f"System B holds {len(group)} identical entries of {money(group[0].value)} "
                    f"for {record.record_id} ({', '.join(e.entry_id for e in group)})."
                ),
                system_b_amount=total,
                difference=total - record.total_value,
                detail={"duplicate_entry_ids": [e.entry_id for e in group]},
            )
        )
        return

    add(
        Exception_(
            **{**base, "entry_ids": [e.entry_id for e in record_entries]},
            reason_code=rc.SPLIT_ENTRIES_DO_NOT_ADD_UP,
            summary=(
                f"System B split {record.record_id} across {len(record_entries)} entries "
                f"adding up to {money(total)}, but System A has "
                f"{money(record.total_value)}, a difference of "
                f"{money(total - record.total_value)}."
            ),
            system_b_amount=total,
            difference=total - record.total_value,
            detail={
                "entry_amounts": {e.entry_id: str(e.value) for e in record_entries},
            },
        )
    )
