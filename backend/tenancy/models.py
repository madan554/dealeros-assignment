from django.conf import settings
from django.db import models


class Org(models.Model):
    """A tenant. Deliberately *not* behind row level security: resolving which
    org the caller belongs to has to happen before any org context exists."""

    id = models.CharField(primary_key=True, max_length=32)
    name = models.CharField(max_length=120)

    class Meta:
        db_table = "tenancy_org"
        ordering = ["id"]

    def __str__(self):
        return self.id


class Location(models.Model):
    """locations.csv is the only place the location-to-org mapping exists, so
    this table is the root of the boundary."""

    id = models.CharField(primary_key=True, max_length=32)
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="locations")
    name = models.CharField(max_length=120)

    class Meta:
        db_table = "tenancy_location"
        ordering = ["id"]

    def __str__(self):
        return f"{self.id} ({self.org_id})"


class OrgMembership(models.Model):
    """A user belongs to exactly one org, per the brief."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="org_membership"
    )
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="memberships")

    class Meta:
        db_table = "tenancy_orgmembership"

    def __str__(self):
        return f"{self.user} -> {self.org_id}"
