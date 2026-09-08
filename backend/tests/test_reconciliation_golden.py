"""What the reconciliation is supposed to find in the supplied dataset.

Written as an exhaustive expectation rather than a set of spot checks. The
brief is graded on "which disagreement classes you caught, which you missed,
and whether you correctly left the non-errors alone", and only an exhaustive
assertion can fail when a new class is caught by accident or an old one
silently stops being reported.

Runs the pure engine over the real CSVs, no database involved.
"""
from decimal import Decimal

import pytest
from django.conf import settings

from reconciliation import reason_codes as rc
from reconciliation.engine import reconcile
from reconciliation.loader import load_all

# Every exception the dataset should produce, as (record, reason, org).
EXPECTED_EXCEPTIONS = {
    # System A has it, System B never recorded it.
    ("REC-1015", rc.MISSING_IN_SYSTEM_B, "ORG-A"),
    ("REC-1061", rc.MISSING_IN_SYSTEM_B, "ORG-B"),
    # System B entry pointing at a record System A does not have.
    ("REC-1999", rc.UNKNOWN_RECORD_IN_SYSTEM_B, "ORG-A"),
    # The full amount recorded twice, so it is counted twice.
    ("REC-1042", rc.DUPLICATE_ENTRY_IN_SYSTEM_B, "ORG-A"),
    # System B recorded base_value instead of total_value: the shortfall is
    # exactly the adjustment. One broken export, three records.
    ("REC-1003", rc.ADJUSTMENT_MISSING_IN_SYSTEM_B, "ORG-B"),
    ("REC-1027", rc.ADJUSTMENT_MISSING_IN_SYSTEM_B, "ORG-A"),
    ("REC-1088", rc.ADJUSTMENT_MISSING_IN_SYSTEM_B, "ORG-A"),
    # A genuinely different number, not explained by the adjustment.
    ("REC-1064", rc.AMOUNT_MISMATCH, "ORG-A"),
    # System B entry exists but its amount is blank.
    ("REC-1050", rc.AMOUNT_MISSING_IN_SYSTEM_B, "ORG-B"),
    # Two days apart, and across a month boundary.
    ("REC-1009", rc.EVENT_DATE_MISMATCH, "ORG-B"),
    # System A voided it; System B still holds the entry.
    ("REC-1019", rc.VOIDED_RECORD_STILL_IN_SYSTEM_B, "ORG-B"),
    # The two systems put this event in different organisations.
    ("REC-1077", rc.LOCATION_MISMATCH, "ORG-A"),
}

# Disagreements that are not errors. Reporting any of these would be a false
# positive, and false positives are how an exceptions list gets ignored.
EXPECTED_NOT_ERRORS = {
    # Split across two entries that add up to the System A total exactly.
    ("REC-1055", rc.NOTE_SPLIT_ENTRIES_RECONCILED),
    # References written as 'rec1034', ' REC - 1070 ' and '1112'.
    ("REC-1034", rc.NOTE_REFERENCE_NORMALISED),
    ("REC-1070", rc.NOTE_REFERENCE_NORMALISED),
    ("REC-1112", rc.NOTE_REFERENCE_NORMALISED),
    # Amount written with Indian digit grouping: '1,25,400.00'.
    ("REC-1064", rc.NOTE_AMOUNT_FORMAT_NORMALISED),
}


@pytest.fixture(scope="module")
def result():
    data = load_all(settings.DATA_DIR)
    return (
        reconcile(
            data["records"],
            data["entries"],
            data["org_by_location"],
            data["name_by_location"],
        ),
        data,
    )


def test_the_dataset_is_the_one_the_brief_describes(result):
    """Guards the rest of this file: if the CSVs are swapped, these
    expectations are meaningless and should say so rather than fail obscurely."""
    _, data = result
    assert len(data["records"]) == 120
    assert len(data["entries"]) == 121
    assert data["org_by_location"] == {
        "LOC-101": "ORG-A",
        "LOC-102": "ORG-A",
        "LOC-103": "ORG-A",
        "LOC-201": "ORG-B",
        "LOC-202": "ORG-B",
    }


def test_exactly_the_expected_exceptions_are_reported(result):
    outcome, _ = result
    found = {(x.record_ref, x.reason_code, x.org_id) for x in outcome.exceptions}
    missed = EXPECTED_EXCEPTIONS - found
    spurious = found - EXPECTED_EXCEPTIONS
    assert not missed, f"disagreements the engine failed to catch: {sorted(missed)}"
    assert not spurious, f"false positives the engine reported: {sorted(spurious)}"


def test_one_exception_per_record_and_reason(result):
    outcome, _ = result
    keys = [(x.record_ref, x.reason_code, x.org_id) for x in outcome.exceptions]
    assert len(keys) == len(set(keys))


def test_the_non_errors_are_recorded_but_kept_out_of_the_exceptions_list(result):
    outcome, _ = result
    notes = {(n.record_ref, n.code) for n in outcome.notes}
    assert notes == EXPECTED_NOT_ERRORS

    reported = {x.record_ref for x in outcome.exceptions}
    # REC-1055 split cleanly and REC-1034/1070/1112 matched after normalising,
    # so none of them are anyone's problem.
    for record_ref in ("REC-1055", "REC-1034", "REC-1070", "REC-1112"):
        assert record_ref not in reported, (
            f"{record_ref} is a formatting or shape difference, not a "
            "disagreement, and should not be in the exceptions list"
        )


def test_the_split_record_is_left_alone_because_the_parts_add_up(result):
    """REC-1055: System B recorded 71,950.93 and 107,926.39 against one record
    that System A totals at 179,877.32. Different shape, same answer."""
    outcome, data = result
    entries = [e for e in data["entries"] if e.record_ref == "REC-1055"]
    record = next(r for r in data["records"] if r.record_id == "REC-1055")
    assert len(entries) == 2
    assert sum(e.value for e in entries) == record.total_value
    note = next(n for n in outcome.notes if n.record_ref == "REC-1055")
    assert note.code == rc.NOTE_SPLIT_ENTRIES_RECONCILED
    assert set(note.entry_ids) == {"ENT/2026/4055", "ENT/2026/4903"}


def test_the_duplicate_is_reported_even_though_it_is_also_two_entries(result):
    """REC-1042 also has two System B entries, so it is the case that
    distinguishes "split" from "double counted". Both entries carry the full
    total, which is a double count, not a split."""
    outcome, _ = result
    exception = next(
        x
        for x in outcome.exceptions
        if x.record_ref == "REC-1042" and x.reason_code == rc.DUPLICATE_ENTRY_IN_SYSTEM_B
    )
    assert set(exception.entry_ids) == {"ENT/2026/4042", "ENT/2026/4902"}
    assert exception.system_a_amount == Decimal("112837.06")
    assert exception.system_b_amount == Decimal("225674.12")
    assert exception.difference == Decimal("112837.06")


def test_the_adjustment_shortfall_is_named_rather_than_called_a_mismatch(result):
    """All three of these differ from System A by exactly the adjustment.

    Calling that AMOUNT_MISMATCH would be true but useless: it would send
    someone to check three records one at a time instead of telling them one
    export dropped a column.
    """
    outcome, data = result
    records = {r.record_id: r for r in data["records"]}
    affected = [
        x for x in outcome.exceptions if x.reason_code == rc.ADJUSTMENT_MISSING_IN_SYSTEM_B
    ]
    assert {x.record_ref for x in affected} == {"REC-1003", "REC-1027", "REC-1088"}
    for exception in affected:
        record = records[exception.record_ref]
        assert exception.system_b_amount == record.base_value
        assert exception.difference == -record.adjustment
        assert exception.detail["shortfall_equals_adjustment"] is True


def test_the_real_amount_mismatch_is_not_explained_by_the_adjustment(result):
    """REC-1064 is the control for the test above: its System B amount is
    neither the total nor the base, so it stays a plain mismatch."""
    outcome, data = result
    record = next(r for r in data["records"] if r.record_id == "REC-1064")
    exception = next(
        x for x in outcome.exceptions if x.reason_code == rc.AMOUNT_MISMATCH
    )
    assert exception.record_ref == "REC-1064"
    assert exception.system_b_amount == Decimal("125400.00")
    assert exception.system_b_amount != record.base_value
    assert exception.system_b_amount != record.total_value
    # The '1,25,400.00' formatting was normalised before comparing, so the
    # exception is about the number and not about the punctuation.
    assert exception.detail["system_b_raw_value"] == "1,25,400.00"


def test_the_cross_org_location_mismatch_is_owned_by_system_as_org(result):
    """REC-1077 is at LOC-102 (ORG-A) in System A and LOC-201 (ORG-B) in
    System B. Ownership follows System A, because System A is where the record
    exists at all, and it has to land in exactly one org's list."""
    outcome, _ = result
    matches = [x for x in outcome.exceptions if x.record_ref == "REC-1077"]
    assert len(matches) == 1
    exception = matches[0]
    assert exception.org_id == "ORG-A"
    assert exception.reason_code == rc.LOCATION_MISMATCH
    assert exception.detail["crosses_org_boundary"] is True
    assert exception.detail["system_a_location"] == "LOC-102"
    assert exception.detail["system_b_location"] == "LOC-201"


def test_the_voided_record_is_reported_once_and_not_piled_on(result):
    """REC-1019 is voided in System A but System B still holds an entry.

    The entry also agrees on amount, date and location, so there is exactly
    one thing to say about it. If it had disagreed on amount too, the void is
    still the actionable fact and the amount would be noise.
    """
    outcome, _ = result
    matches = [x for x in outcome.exceptions if x.record_ref == "REC-1019"]
    assert len(matches) == 1
    assert matches[0].reason_code == rc.VOIDED_RECORD_STILL_IN_SYSTEM_B
    assert matches[0].detail["system_a_state"] == "VOIDED"


def test_the_date_mismatch_flags_the_reporting_period_it_moves(result):
    """REC-1009: 2026-03-31 in System A, 2026-04-02 in System B. Two days, but
    across a month end, which is the part a human cares about."""
    outcome, _ = result
    exception = next(
        x for x in outcome.exceptions if x.reason_code == rc.EVENT_DATE_MISMATCH
    )
    assert exception.record_ref == "REC-1009"
    assert exception.detail["days_apart"] == 2
    assert exception.detail["different_reporting_month"] is True


def test_the_orphan_entry_is_attributed_from_its_own_location(result):
    """ENT/2026/4901 points at REC-1999, which does not exist. With no System A
    record to inherit an org from, its own location is the only evidence
    available."""
    outcome, _ = result
    exception = next(
        x for x in outcome.exceptions if x.reason_code == rc.UNKNOWN_RECORD_IN_SYSTEM_B
    )
    assert exception.entry_ids == ["ENT/2026/4901"]
    assert exception.location_id == "LOC-102"
    assert exception.org_id == "ORG-A"
    assert exception.system_b_amount == Decimal("41250.00")


def test_nothing_in_this_dataset_is_unattributable(result):
    """Every row maps to an org. If a future export contains a location that
    is not in locations.csv, it lands here instead of being guessed into
    someone's list."""
    outcome, _ = result
    assert outcome.unattributable == []


def test_every_exception_reads_as_a_sentence_and_names_its_rows(result):
    """The brief asks for reason codes a non-engineer could act on without a
    glossary, so the row has to stand on its own."""
    outcome, _ = result
    for exception in outcome.exceptions:
        assert exception.summary.endswith("."), exception.summary
        assert len(exception.summary) > 40, exception.summary
        assert exception.reason_code in rc.REASON_CODES_BY_CODE
        # It must point at something concrete in one of the two systems.
        assert exception.record_ref in exception.summary or any(
            entry_id in exception.summary for entry_id in exception.entry_ids
        ), exception.summary
        assert exception.entry_ids or exception.reason_code == rc.MISSING_IN_SYSTEM_B


def test_every_reason_code_in_the_catalogue_explains_itself(result):
    for reason in rc.REASON_CODES:
        assert reason.label and not reason.label.isupper()
        assert reason.what_it_means.endswith(".")
        assert reason.what_to_do.endswith(".")


def test_the_exception_total_splits_seven_three_way_and_five(result):
    """A blunt count, so that a change in behaviour cannot slip through as a
    quietly different number."""
    outcome, _ = result
    by_org = {}
    for exception in outcome.exceptions:
        by_org[exception.org_id] = by_org.get(exception.org_id, 0) + 1
    assert by_org == {"ORG-A": 7, "ORG-B": 5}
    assert len(outcome.exceptions) == 12
