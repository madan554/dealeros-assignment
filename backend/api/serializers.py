from rest_framework import serializers

from reconciliation.models import Exception as ReconciliationException
from reconciliation.models import MatchNote, SourceEntryB, SourceRecordA
from reconciliation.reason_codes import REASON_CODES_BY_CODE
from tenancy.models import Location


class ExceptionSerializer(serializers.ModelSerializer):
    reason_label = serializers.SerializerMethodField()
    what_to_do = serializers.SerializerMethodField()

    class Meta:
        model = ReconciliationException
        fields = [
            "id",
            "record_ref",
            "reason_code",
            "reason_label",
            "what_to_do",
            "summary",
            "location_id",
            "location_name",
            "event_date",
            "category_code",
            "system_a_amount",
            "system_b_amount",
            "difference",
            "entry_ids",
            "detail",
        ]

    def get_reason_label(self, obj):
        reason = REASON_CODES_BY_CODE.get(obj.reason_code)
        return reason.label if reason else obj.reason_code

    def get_what_to_do(self, obj):
        reason = REASON_CODES_BY_CODE.get(obj.reason_code)
        return reason.what_to_do if reason else ""


class SourceRecordASerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceRecordA
        fields = [
            "record_id",
            "location_id",
            "event_date",
            "category_code",
            "actor_id",
            "base_value",
            "adjustment",
            "total_value",
            "state",
            "source_row_number",
        ]


class SourceEntryBSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceEntryB
        fields = [
            "entry_id",
            "raw_record_ref",
            "record_ref",
            "location_id",
            "recorded_on",
            "raw_value",
            "value",
            "label",
            "reference_was_normalised",
            "amount_format_was_normalised",
            "source_row_number",
        ]


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ["id", "name"]


class MatchNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchNote
        fields = ["record_ref", "code", "summary", "entry_ids"]
