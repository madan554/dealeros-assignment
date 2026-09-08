from django.db import models

from tenancy.models import Org

from .reason_codes import REASON_CODES


class SourceRecordA(models.Model):
    """system_a.csv, stored as loaded so an exception can cite its source row."""

    record_id = models.CharField(primary_key=True, max_length=64)
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="records")
    # Source location ids are held as plain text, not a FK: this is staging
    # data and System B sometimes points at a location in a different org,
    # which a FK to a row-level-secured table could not represent.
    location_id = models.CharField(max_length=32)
    event_date = models.DateField(null=True)
    category_code = models.CharField(max_length=32)
    actor_id = models.CharField(max_length=32, blank=True)
    base_value = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    adjustment = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    total_value = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    state = models.CharField(max_length=32)
    source_row_number = models.IntegerField()

    class Meta:
        db_table = "reconciliation_sourcerecorda"
        ordering = ["record_id"]


class SourceEntryB(models.Model):
    """system_b.csv, keeping both the raw reference and what we read it as."""

    entry_id = models.CharField(primary_key=True, max_length=64)
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="entries")
    raw_record_ref = models.CharField(max_length=64)
    record_ref = models.CharField(max_length=64, blank=True, db_index=True)
    location_id = models.CharField(max_length=32)
    recorded_on = models.DateField(null=True)
    raw_value = models.CharField(max_length=64, blank=True)
    value = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    label = models.CharField(max_length=255, blank=True)
    reference_was_normalised = models.BooleanField(default=False)
    amount_format_was_normalised = models.BooleanField(default=False)
    source_row_number = models.IntegerField()

    class Meta:
        db_table = "reconciliation_sourceentryb"
        ordering = ["entry_id"]


class Exception(models.Model):
    """One actionable disagreement, owned by exactly one org.

    Named `Exception` because that is what the brief calls it and what the
    business calls it; it shadows the builtin only inside this module, and
    nothing here raises exceptions by that name.
    """

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="exceptions")
    record_ref = models.CharField(max_length=64, db_index=True)
    reason_code = models.CharField(
        max_length=64, db_index=True, choices=[(r.code, r.label) for r in REASON_CODES]
    )
    summary = models.TextField()
    location_id = models.CharField(max_length=32, blank=True, db_index=True)
    location_name = models.CharField(max_length=120, blank=True)
    event_date = models.DateField(null=True)
    category_code = models.CharField(max_length=32, blank=True)
    system_a_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    system_b_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    difference = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    entry_ids = models.JSONField(default=list)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reconciliation_exception"
        ordering = ["record_ref", "reason_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["org", "record_ref", "reason_code"],
                name="unique_exception_per_record_and_reason",
            )
        ]

    def __str__(self):
        return f"{self.record_ref} {self.reason_code}"


class MatchNote(models.Model):
    """Disagreements that turned out not to be errors.

    Kept so that "we looked at this and decided it was fine" is auditable
    rather than invisible. Never served as part of the exceptions list.
    """

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="match_notes")
    record_ref = models.CharField(max_length=64, db_index=True)
    code = models.CharField(max_length=64, db_index=True)
    summary = models.TextField()
    entry_ids = models.JSONField(default=list)

    class Meta:
        db_table = "reconciliation_matchnote"
        ordering = ["record_ref", "code"]


class IngestRun(models.Model):
    """Ingest bookkeeping. Global, not tenant data, so no row level security."""

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)
    stats = models.JSONField(default=dict)
    unattributable = models.JSONField(default=list)

    class Meta:
        db_table = "reconciliation_ingestrun"
        ordering = ["-started_at"]
