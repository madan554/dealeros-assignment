"""Load both CSVs, reconcile them, and persist the result.

Note what this command does *not* have: a way to switch row level security
off. It writes tenant rows by walking the orgs one at a time and pinning the
session to each in turn. That is more code than a bypass role would be, but it
means there is no privileged path in the codebase for someone to reach for
later, and it exercises the same boundary the API relies on.
"""
from collections import defaultdict

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from tenancy.models import Location, Org
from tenancy.rls import org_context

from ...engine import reconcile
from ...loader import load_all
from ...models import Exception as ReconciliationException
from ...models import IngestRun, MatchNote, SourceEntryB, SourceRecordA

ORG_NAMES = {"ORG-A": "Organisation A", "ORG-B": "Organisation B"}


class Command(BaseCommand):
    help = "Ingest system_a.csv / system_b.csv / locations.csv and reconcile them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--data-dir",
            default=str(settings.DATA_DIR),
            help="Directory holding the three CSVs.",
        )

    def handle(self, *args, **options):
        data = load_all(options["data_dir"])
        org_by_location = data["org_by_location"]
        name_by_location = data["name_by_location"]
        records = data["records"]
        entries = data["entries"]

        result = reconcile(records, entries, org_by_location, name_by_location)

        run = IngestRun.objects.create()
        org_ids = sorted(set(org_by_location.values()))
        for org_id in org_ids:
            Org.objects.update_or_create(
                id=org_id, defaults={"name": ORG_NAMES.get(org_id, org_id)}
            )

        records_by_id = {r.record_id: r for r in records}

        # An entry belongs to the org that owns the record it matched, falling
        # back to its own location. Anchoring it to the record matters: entry
        # ENT/2026/4077 points at a location in the other org, and if it were
        # filed under that org then the exception citing it would cite a row
        # its own reader could not see.
        entry_org = {}
        for entry in entries:
            matched = records_by_id.get(entry.record_ref) if entry.record_ref else None
            org_id = (
                org_by_location.get(matched.location_id)
                if matched
                else org_by_location.get(entry.location_id)
            )
            if org_id:
                entry_org[entry.entry_id] = org_id

        buckets = defaultdict(
            lambda: {
                "locations": [],
                "records": [],
                "entries": [],
                "exceptions": [],
                "notes": [],
            }
        )
        for location_id, org_id in org_by_location.items():
            buckets[org_id]["locations"].append(location_id)
        for record in records:
            org_id = org_by_location.get(record.location_id)
            if org_id:
                buckets[org_id]["records"].append(record)
        for entry in entries:
            org_id = entry_org.get(entry.entry_id)
            if org_id:
                buckets[org_id]["entries"].append(entry)
        for exception in result.exceptions:
            buckets[exception.org_id]["exceptions"].append(exception)
        for note in result.notes:
            buckets[note.org_id]["notes"].append(note)

        for org_id in sorted(buckets):
            bucket = buckets[org_id]
            with org_context(org_id):
                self._replace_org_data(org_id, bucket, name_by_location)

        run.finished_at = timezone.now()
        run.stats = result.stats
        run.unattributable = [
            {"kind": u.kind, "identifier": u.identifier, "reason": u.reason}
            for u in result.unattributable
        ]
        run.save(update_fields=["finished_at", "stats", "unattributable"])

        self._report(result, buckets)

    @transaction.atomic
    def _replace_org_data(self, org_id, bucket, name_by_location):
        """Reload one org's rows. Every statement below is filtered by the
        database to this org, deletes included."""
        ReconciliationException.objects.all().delete()
        MatchNote.objects.all().delete()
        SourceEntryB.objects.all().delete()
        SourceRecordA.objects.all().delete()
        Location.objects.all().delete()

        Location.objects.bulk_create(
            [
                Location(
                    id=location_id,
                    org_id=org_id,
                    name=name_by_location.get(location_id, location_id),
                )
                for location_id in bucket["locations"]
            ]
        )
        SourceRecordA.objects.bulk_create(
            [
                SourceRecordA(
                    record_id=r.record_id,
                    org_id=org_id,
                    location_id=r.location_id,
                    event_date=r.event_date,
                    category_code=r.category_code,
                    actor_id=r.actor_id,
                    base_value=r.base_value,
                    adjustment=r.adjustment,
                    total_value=r.total_value,
                    state=r.state,
                    source_row_number=r.row_number,
                )
                for r in bucket["records"]
            ]
        )
        SourceEntryB.objects.bulk_create(
            [
                SourceEntryB(
                    entry_id=e.entry_id,
                    org_id=org_id,
                    raw_record_ref=e.raw_record_ref,
                    record_ref=e.record_ref or "",
                    location_id=e.location_id,
                    recorded_on=e.recorded_on,
                    raw_value=e.raw_value,
                    value=e.value,
                    label=e.label,
                    reference_was_normalised=e.reference_was_normalised,
                    amount_format_was_normalised=e.amount_format_was_normalised,
                    source_row_number=e.row_number,
                )
                for e in bucket["entries"]
            ]
        )
        ReconciliationException.objects.bulk_create(
            [
                ReconciliationException(
                    org_id=org_id,
                    record_ref=x.record_ref,
                    reason_code=x.reason_code,
                    summary=x.summary,
                    location_id=x.location_id,
                    location_name=x.location_name,
                    event_date=x.event_date,
                    category_code=x.category_code,
                    system_a_amount=x.system_a_amount,
                    system_b_amount=x.system_b_amount,
                    difference=x.difference,
                    entry_ids=x.entry_ids,
                    detail=x.detail,
                )
                for x in bucket["exceptions"]
            ]
        )
        MatchNote.objects.bulk_create(
            [
                MatchNote(
                    org_id=org_id,
                    record_ref=n.record_ref,
                    code=n.code,
                    summary=n.summary,
                    entry_ids=n.entry_ids,
                )
                for n in bucket["notes"]
            ]
        )

    def _report(self, result, buckets):
        write = self.stdout.write
        write(self.style.SUCCESS("Ingest complete."))
        for key, value in result.stats.items():
            write(f"  {key.replace('_', ' '):24} {value}")
        write("")
        write("Exceptions by org and reason:")
        for org_id in sorted(buckets):
            exceptions = buckets[org_id]["exceptions"]
            write(f"  {org_id}: {len(exceptions)}")
            counts = defaultdict(int)
            for exception in exceptions:
                counts[exception.reason_code] += 1
            for code in sorted(counts):
                write(f"    {counts[code]:3}  {code}")
        write("")
        write("Disagreements judged not to be errors (not in any exceptions list):")
        counts = defaultdict(int)
        for note in result.notes:
            counts[note.code] += 1
        for code in sorted(counts):
            write(f"    {counts[code]:3}  {code}")
        if result.unattributable:
            write("")
            write(self.style.WARNING("Rows that cannot be attributed to an org:"))
            for item in result.unattributable:
                write(f"    {item.kind} {item.identifier}: {item.reason}")
