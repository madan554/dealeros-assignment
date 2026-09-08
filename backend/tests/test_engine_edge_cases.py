"""Unit tests for normalisation and for the classes the supplied CSVs do not
contain.

The dataset exercises eight of the eleven reason codes. The other three, and
the "cannot be attributed to an org" path, are claimed in the reason code
catalogue and in the README, so they are tested here against synthetic rows
rather than left as untested assertions. The brief says to assume real exports
are worse than this one.
"""
from datetime import date
from decimal import Decimal

import pytest

from reconciliation import reason_codes as rc
from reconciliation.engine import (
    EntryB,
    RecordA,
    normalise_amount,
    normalise_date,
    normalise_reference,
    reconcile,
)

LOCATION_ORG = {"LOC-101": "ORG-A", "LOC-201": "ORG-B"}
LOCATION_NAMES = {"LOC-101": "Location 101", "LOC-201": "Location 201"}


# --------------------------------------------------------------------------
# normalise_reference
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("REC-1001", "REC-1001"),
        ("rec1034", "REC-1034"),  # in the dataset
        (" REC - 1070 ", "REC-1070"),  # in the dataset
        ("1112", "REC-1112"),  # in the dataset
        ("REC_1200", "REC-1200"),
        ("rec-1201", "REC-1201"),
        ("REC1202", "REC-1202"),
        ("\tREC-1203\n", "REC-1203"),
    ],
)
def test_recoverable_references_are_recovered(raw, expected):
    normalised, _ = normalise_reference(raw)
    assert normalised == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "REC-",
        "REC-12A4",
        "REF-1001",  # a different prefix could mean a different system
        "REC-1001/REC-1002",  # two candidates, so no single answer
        "N/A",
        "see attached",
        "REC-1001; REC-1002",
    ],
)
def test_unrecoverable_references_are_not_guessed(raw):
    """A wrong match is worse than no match: it can file a row under the wrong
    tenant, which is the one failure this system is built to avoid."""
    normalised, changed = normalise_reference(raw)
    assert normalised is None
    assert changed is False


def test_a_clean_reference_is_not_reported_as_normalised():
    """Otherwise every row would carry a note and the notes would be worthless."""
    assert normalise_reference("REC-1001") == ("REC-1001", False)
    assert normalise_reference("rec1034")[1] is True


# --------------------------------------------------------------------------
# normalise_amount
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("88969.92", Decimal("88969.92")),
        ("1,25,400.00", Decimal("125400.00")),  # in the dataset: Indian grouping
        ("125,400.00", Decimal("125400.00")),
        (" 1234.50 ", Decimal("1234.50")),
        ("-500.25", Decimal("-500.25")),
        ("(500.25)", Decimal("-500.25")),  # accounting negative
        ("$1,000.00", Decimal("1000.00")),
        ("0", Decimal("0")),
    ],
)
def test_amount_formats_are_normalised_before_comparing(raw, expected):
    value, _ = normalise_amount(raw)
    assert value == expected


@pytest.mark.parametrize("raw", ["", "   ", None, "n/a", "TBC", "--", "1.2.3"])
def test_unparseable_amounts_become_none_rather_than_zero(raw):
    """Zero is a number someone might act on. Absent is not."""
    value, _ = normalise_amount(raw)
    assert value is None


def test_normalise_date_accepts_the_common_export_formats():
    assert normalise_date("2026-04-03") == date(2026, 4, 3)
    assert normalise_date("03/04/2026") == date(2026, 4, 3)  # day first
    assert normalise_date("03-Apr-2026") == date(2026, 4, 3)
    assert normalise_date("") is None
    assert normalise_date("not a date") is None


# --------------------------------------------------------------------------
# Helpers for synthetic scenarios
# --------------------------------------------------------------------------


def record(record_id="REC-1", location="LOC-101", base="100.00", adj="10.00",
           total="110.00", state="CONFIRMED", event=date(2026, 3, 1)):
    return RecordA(
        record_id=record_id,
        location_id=location,
        event_date=event,
        category_code="CAT-01",
        actor_id="USR-1",
        base_value=Decimal(base) if base is not None else None,
        adjustment=Decimal(adj) if adj is not None else None,
        total_value=Decimal(total) if total is not None else None,
        state=state,
    )


def entry(entry_id="ENT-1", ref="REC-1", location="LOC-101", value="110.00",
          recorded=date(2026, 3, 1), raw_ref=None, label="Entry"):
    return EntryB(
        entry_id=entry_id,
        raw_record_ref=raw_ref if raw_ref is not None else (ref or ""),
        record_ref=ref,
        location_id=location,
        recorded_on=recorded,
        raw_value=value or "",
        value=Decimal(value) if value else None,
        label=label,
    )


def run(records, entries):
    return reconcile(records, entries, LOCATION_ORG, LOCATION_NAMES)


def codes(outcome):
    return sorted(x.reason_code for x in outcome.exceptions)


# --------------------------------------------------------------------------
# The classes the dataset does not contain
# --------------------------------------------------------------------------


def test_an_unreadable_reference_is_reported_against_its_own_location():
    """No record to attach it to, so the org comes from the entry's location
    and the raw text is preserved for whoever has to re-key it."""
    outcome = run([record()], [entry(), entry("ENT-2", ref=None, raw_ref="see attached")])
    unreadable = [
        x for x in outcome.exceptions if x.reason_code == rc.UNREADABLE_REFERENCE_IN_SYSTEM_B
    ]
    assert len(unreadable) == 1
    assert unreadable[0].org_id == "ORG-A"
    assert unreadable[0].detail["raw_record_ref"] == "see attached"
    assert unreadable[0].entry_ids == ["ENT-2"]


def test_split_entries_that_do_not_add_up_are_reported():
    """The other half of the split logic: same shape as REC-1055, wrong total."""
    outcome = run(
        [record(total="110.00")],
        [entry("ENT-1", value="60.00"), entry("ENT-2", value="40.00")],
    )
    assert codes(outcome) == [rc.SPLIT_ENTRIES_DO_NOT_ADD_UP]
    exception = outcome.exceptions[0]
    assert exception.system_b_amount == Decimal("100.00")
    assert exception.difference == Decimal("-10.00")
    assert set(exception.entry_ids) == {"ENT-1", "ENT-2"}


def test_partial_duplicates_are_still_called_duplicates():
    """Two identical half-amount entries plus a third. They do not sum to the
    total and they are not a clean split, so the duplicate is the useful thing
    to say."""
    outcome = run(
        [record(total="110.00")],
        [
            entry("ENT-1", value="50.00"),
            entry("ENT-2", value="50.00"),
            entry("ENT-3", value="20.00"),
        ],
    )
    assert codes(outcome) == [rc.DUPLICATE_ENTRY_IN_SYSTEM_B]
    assert set(outcome.exceptions[0].entry_ids) == {"ENT-1", "ENT-2"}


def test_a_row_whose_location_is_not_in_the_mapping_is_never_guessed_into_an_org():
    """locations.csv is the only place the mapping exists, so a location that
    is not in it cannot be attributed. Putting the row in a plausible org's
    list would be showing one tenant another tenant's data on a hunch."""
    outcome = run(
        [record(record_id="REC-9", location="LOC-999")],
        [entry("ENT-9", ref="REC-8", location="LOC-999")],
    )
    assert outcome.exceptions == []
    assert {u.kind for u in outcome.unattributable} == {"system_a_record", "system_b_entry"}
    assert {u.identifier for u in outcome.unattributable} == {"REC-9", "ENT-9"}


# --------------------------------------------------------------------------
# Non-errors
# --------------------------------------------------------------------------


def test_a_sub_cent_difference_is_rounding_and_not_an_exception():
    outcome = run([record(total="110.00")], [entry(value="110.005")])
    assert outcome.exceptions == []
    assert [n.code for n in outcome.notes] == [rc.NOTE_ROUNDING_WITHIN_TOLERANCE]


def test_a_one_cent_difference_is_an_exception():
    """The boundary of the tolerance, asserted so that widening it later is a
    deliberate act rather than a side effect."""
    outcome = run([record(total="110.00")], [entry(value="110.01")])
    assert codes(outcome) == [rc.AMOUNT_MISMATCH]


def test_a_voided_record_with_no_entry_is_correct_behaviour():
    outcome = run([record(state="VOIDED")], [])
    assert outcome.exceptions == []
    assert [n.code for n in outcome.notes] == [rc.NOTE_VOIDED_AND_ABSENT]


def test_a_fully_matching_record_produces_nothing_at_all():
    """The commonest case. Most of the 120 rows land here, and an exceptions
    list that reported them would be useless."""
    outcome = run([record()], [entry()])
    assert outcome.exceptions == []
    assert outcome.notes == []


# --------------------------------------------------------------------------
# Judgement calls, pinned so they cannot drift silently
# --------------------------------------------------------------------------


def test_a_voided_record_is_reported_once_even_when_the_amount_also_disagrees():
    """The void is what needs doing something about. Adding an amount mismatch
    on a cancelled record is noise that pushes the real finding down the list."""
    outcome = run([record(state="VOIDED")], [entry(value="999.00", recorded=date(2026, 5, 5))])
    assert codes(outcome) == [rc.VOIDED_RECORD_STILL_IN_SYSTEM_B]


def test_a_blank_amount_suppresses_the_amount_comparison_but_not_the_date_one():
    """A blank amount cannot be compared, so reporting a mismatch as well
    would be inventing a disagreement. The date is still checkable."""
    outcome = run([record()], [entry(value=None, recorded=date(2026, 4, 9))])
    assert codes(outcome) == [rc.AMOUNT_MISSING_IN_SYSTEM_B, rc.EVENT_DATE_MISMATCH]


def test_one_record_can_carry_more_than_one_reason():
    """Location and date are independent facts. Collapsing them into a single
    "this row is wrong" would lose the instruction of what to fix."""
    outcome = run([record()], [entry(location="LOC-201", recorded=date(2026, 4, 9))])
    assert codes(outcome) == [rc.EVENT_DATE_MISMATCH, rc.LOCATION_MISMATCH]
    location = next(
        x for x in outcome.exceptions if x.reason_code == rc.LOCATION_MISMATCH
    )
    assert location.detail["crosses_org_boundary"] is True
    assert location.org_id == "ORG-A"


def test_a_within_org_location_mismatch_is_not_flagged_as_crossing_the_boundary():
    """Same class of error, materially different severity."""
    outcome = run(
        [record(location="LOC-101")],
        [entry(location="LOC-102")],
    )
    location = next(
        x for x in outcome.exceptions if x.reason_code == rc.LOCATION_MISMATCH
    )
    # LOC-102 is not in this test's mapping at all, so it cannot be said to
    # cross into a known org.
    assert location.detail["crosses_org_boundary"] is False


def test_reconciling_nothing_is_not_an_error():
    outcome = run([], [])
    assert outcome.exceptions == []
    assert outcome.stats["system_a_records"] == 0
