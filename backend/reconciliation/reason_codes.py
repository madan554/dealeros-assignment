"""Reason codes for the exceptions list.

The brief asks for codes "a non-engineer could act on without a glossary".
Two consequences for the design here:

* the ``label`` is what the UI shows, and it is a sentence fragment in
  business English, not a snake-case constant;
* every exception also carries a ``summary`` built at reconciliation time with
  the actual identifiers and amounts substituted in, so the row is readable on
  its own without cross-referencing anything.

``NOTE_*`` codes are the other half of the answer: disagreements that turned
out not to be errors. They are recorded so the work is visible and auditable,
but they never appear in the exceptions list.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ReasonCode:
    code: str
    label: str
    what_it_means: str
    what_to_do: str


MISSING_IN_SYSTEM_B = "MISSING_IN_SYSTEM_B"
UNKNOWN_RECORD_IN_SYSTEM_B = "UNKNOWN_RECORD_IN_SYSTEM_B"
UNREADABLE_REFERENCE_IN_SYSTEM_B = "UNREADABLE_REFERENCE_IN_SYSTEM_B"
DUPLICATE_ENTRY_IN_SYSTEM_B = "DUPLICATE_ENTRY_IN_SYSTEM_B"
AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
ADJUSTMENT_MISSING_IN_SYSTEM_B = "ADJUSTMENT_MISSING_IN_SYSTEM_B"
AMOUNT_MISSING_IN_SYSTEM_B = "AMOUNT_MISSING_IN_SYSTEM_B"
SPLIT_ENTRIES_DO_NOT_ADD_UP = "SPLIT_ENTRIES_DO_NOT_ADD_UP"
EVENT_DATE_MISMATCH = "EVENT_DATE_MISMATCH"
LOCATION_MISMATCH = "LOCATION_MISMATCH"
VOIDED_RECORD_STILL_IN_SYSTEM_B = "VOIDED_RECORD_STILL_IN_SYSTEM_B"

REASON_CODES = [
    ReasonCode(
        MISSING_IN_SYSTEM_B,
        "Missing from System B",
        "System A recorded this event but System B has no entry for it at all.",
        "Find out whether the event really happened. If it did, get it into System B.",
    ),
    ReasonCode(
        UNKNOWN_RECORD_IN_SYSTEM_B,
        "System B entry for an unknown record",
        "A System B entry points at a System A record number that does not exist.",
        "Check whether the entry was keyed against the wrong record number.",
    ),
    ReasonCode(
        UNREADABLE_REFERENCE_IN_SYSTEM_B,
        "System B reference cannot be read",
        "A System B entry's record reference is too damaged to match to anything.",
        "Have someone re-key the reference on this entry.",
    ),
    ReasonCode(
        DUPLICATE_ENTRY_IN_SYSTEM_B,
        "Recorded more than once in System B",
        "System B holds the same amount for this record more than once, so the "
        "value is being counted twice.",
        "Cancel the duplicate entries and keep one.",
    ),
    ReasonCode(
        AMOUNT_MISMATCH,
        "Amounts disagree",
        "The two systems hold different amounts for the same event, and the "
        "difference is not explained by the adjustment.",
        "Confirm the correct amount with the location and fix the wrong system.",
    ),
    ReasonCode(
        ADJUSTMENT_MISSING_IN_SYSTEM_B,
        "System B is missing the adjustment",
        "System B recorded the amount before the adjustment was applied. The "
        "shortfall equals the adjustment exactly.",
        "Re-post these entries with the adjustment included. Likely one broken "
        "export rather than a per-record mistake.",
    ),
    ReasonCode(
        AMOUNT_MISSING_IN_SYSTEM_B,
        "System B has no amount",
        "The System B entry exists but its amount is blank, so it cannot be "
        "checked against System A.",
        "Get the amount filled in on the System B entry.",
    ),
    ReasonCode(
        SPLIT_ENTRIES_DO_NOT_ADD_UP,
        "Split entries do not add up",
        "System B split this record across several entries, but they do not "
        "add up to the System A total.",
        "Work out which part is wrong or missing from the split.",
    ),
    ReasonCode(
        EVENT_DATE_MISMATCH,
        "Dates disagree",
        "The two systems hold different dates for the same event, which moves "
        "it between reporting periods.",
        "Confirm the real date. Check it especially where the two dates fall in "
        "different months.",
    ),
    ReasonCode(
        LOCATION_MISMATCH,
        "Locations disagree",
        "The two systems disagree about which location this event belongs to.",
        "Confirm the correct location. Re-point the System B entry.",
    ),
    ReasonCode(
        VOIDED_RECORD_STILL_IN_SYSTEM_B,
        "Voided record still live in System B",
        "System A voided this record, but System B still holds an entry for "
        "it, so a cancelled amount is still counted.",
        "Void the matching entry in System B.",
    ),
]

REASON_CODES_BY_CODE = {r.code: r for r in REASON_CODES}


# --- Non-errors: recorded, never reported as problems ---------------------

NOTE_REFERENCE_NORMALISED = "REFERENCE_NORMALISED"
NOTE_AMOUNT_FORMAT_NORMALISED = "AMOUNT_FORMAT_NORMALISED"
NOTE_SPLIT_ENTRIES_RECONCILED = "SPLIT_ENTRIES_RECONCILED"
NOTE_VOIDED_AND_ABSENT = "VOIDED_AND_ABSENT_AS_EXPECTED"
NOTE_ROUNDING_WITHIN_TOLERANCE = "ROUNDING_WITHIN_TOLERANCE"

NOTE_CODES = {
    NOTE_REFERENCE_NORMALISED: "The System B reference was formatted differently but "
    "identified one record unambiguously, so it was matched.",
    NOTE_AMOUNT_FORMAT_NORMALISED: "The System B amount used a different number "
    "format. The format was normalised before comparing.",
    NOTE_SPLIT_ENTRIES_RECONCILED: "System B split the record across several entries "
    "that add up to the System A total. That is a valid way to record it.",
    NOTE_VOIDED_AND_ABSENT: "System A voided the record and System B has no entry. "
    "That is the correct outcome, not a gap.",
    NOTE_ROUNDING_WITHIN_TOLERANCE: "The amounts differ by less than one cent, which "
    "is rounding, not a disagreement.",
}
