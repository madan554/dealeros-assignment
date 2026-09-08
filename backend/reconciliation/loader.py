"""Reads the three CSVs into the engine's dataclasses.

Kept separate from the engine so the classification logic never has to know
about files, and separate from the ingest command so it can be exercised
without a database.
"""
import csv
from pathlib import Path

from .engine import EntryB, RecordA, normalise_amount, normalise_date, normalise_reference


def _clean(value):
    return (value or "").strip()


def load_locations(path: Path):
    """Returns ``(location_id -> org_id, location_id -> name)``.

    locations.csv is the only place the location-to-org mapping exists, which
    makes it the authority for tenant ownership.
    """
    org_by_location, name_by_location = {}, {}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            location_id = _clean(row["location_id"])
            if not location_id:
                continue
            org_by_location[location_id] = _clean(row["org_id"])
            name_by_location[location_id] = _clean(row.get("location_name")) or location_id
    return org_by_location, name_by_location


def load_system_a(path: Path):
    records = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            record_id = _clean(row["record_id"])
            if not record_id:
                continue
            base, _ = normalise_amount(row.get("base_value"))
            adjustment, _ = normalise_amount(row.get("adjustment"))
            total, _ = normalise_amount(row.get("total_value"))
            records.append(
                RecordA(
                    record_id=record_id,
                    location_id=_clean(row["location_id"]),
                    event_date=normalise_date(row.get("event_date")),
                    category_code=_clean(row.get("category_code")),
                    actor_id=_clean(row.get("actor_id")),
                    base_value=base,
                    adjustment=adjustment,
                    total_value=total,
                    state=_clean(row.get("state")).upper(),
                    row_number=line_number,
                )
            )
    return records


def load_system_b(path: Path):
    entries = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            entry_id = _clean(row["entry_id"])
            if not entry_id:
                continue
            raw_ref = row.get("record_ref") or ""
            record_ref, ref_changed = normalise_reference(raw_ref)
            raw_value = row.get("value") or ""
            value, value_changed = normalise_amount(raw_value)
            entries.append(
                EntryB(
                    entry_id=entry_id,
                    raw_record_ref=raw_ref,
                    record_ref=record_ref,
                    location_id=_clean(row["location_id"]),
                    recorded_on=normalise_date(row.get("recorded_on")),
                    raw_value=raw_value,
                    value=value,
                    label=_clean(row.get("label")),
                    row_number=line_number,
                    reference_was_normalised=ref_changed,
                    amount_format_was_normalised=value_changed,
                )
            )
    return entries


def load_all(data_dir: Path):
    data_dir = Path(data_dir)
    org_by_location, name_by_location = load_locations(data_dir / "locations.csv")
    return {
        "org_by_location": org_by_location,
        "name_by_location": name_by_location,
        "records": load_system_a(data_dir / "system_a.csv"),
        "entries": load_system_b(data_dir / "system_b.csv"),
    }
