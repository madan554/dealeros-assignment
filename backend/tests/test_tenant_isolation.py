"""Proof that the org boundary is enforced by Postgres and not by our code.

The brief asks for a test that bypasses the application-level filter and shows
the database still refusing. There is no application-level filter to remove
here: no queryset in ``api/views.py`` mentions org at all. So these tests go
further and attack the boundary directly, in the ways a real leak actually
happens:

* raw SQL that skips the ORM entirely
* a connection that never establishes org context, i.e. a forgotten middleware
* a query that explicitly and deliberately asks for the other org's rows
* aggregates, which leak totals without ever returning a row
* writes, which leak by moving a row across the boundary rather than reading it
* the HTTP surface, with a real token

``test_the_policy_is_the_thing_doing_the_work`` is the one to watch: it removes
the database protection mid-test, shows the other org's rows appearing, then
puts it back and shows them disappearing. That is the before/after in the
walkthrough, and it is what stops the rest of this file from being a test of
something incidental.

Running the whole suite with ``DEALEROS_DISABLE_RLS=1`` builds the database
without policies and fails most of this file. See WALKTHROUGH.md.
"""
import psycopg
import pytest
from django.db import connection, transaction

from conftest import ORG_A, ORG_B
from reconciliation.models import Exception as ReconciliationException
from reconciliation.models import MatchNote, SourceEntryB, SourceRecordA
from tenancy.models import Location
from tenancy.rls import (
    POLICY_NAME,
    connection_privileges,
    org_context,
    reset_org_context,
    rls_status,
)

pytestmark = pytest.mark.django_db

EXCEPTIONS_TABLE = "reconciliation_exception"


# --------------------------------------------------------------------------
# The guarantee is in the database, so read it from the database
# --------------------------------------------------------------------------


def test_row_level_security_is_installed_on_every_tenant_table(seeded):
    """Introspects pg_class and pg_policy rather than trusting our migration.

    Also the guard against a future tenant table: settings.TENANT_TABLES is
    what the migration installs policies from and what this reads, so a new
    table registered without policies, or a table added to the models and
    never registered, shows up here.
    """
    status = rls_status()
    assert status, "no tenant tables are registered in settings.TENANT_TABLES"
    for table, state in status.items():
        assert state is not None, f"{table} is registered but does not exist"
        assert state["enabled"], f"{table} does not have row level security enabled"
        assert state["forced"], (
            f"{table} has row level security enabled but not FORCED. Django owns "
            "this table, and a table owner is exempt from its own policies unless "
            "row level security is forced."
        )
        assert state["policies"] >= 1, f"{table} has no policy attached"


def test_every_model_with_an_org_column_is_registered_as_a_tenant_table(seeded):
    """The failure mode this catches: someone adds a model with an org FK,
    ships it, and it is the one table with no boundary on it.

    An exemption is allowed but has to be written down in
    settings.TENANT_BOUNDARY_EXEMPT with a reason, so that leaving a table
    unprotected is a decision someone made rather than something that happened.
    """
    from django.apps import apps
    from django.conf import settings

    accounted_for = set(settings.TENANT_TABLES) | set(settings.TENANT_BOUNDARY_EXEMPT)
    unprotected = []
    for model in apps.get_models():
        if model._meta.app_label not in {"tenancy", "reconciliation"}:
            continue
        if "org" not in {f.name for f in model._meta.fields}:
            continue
        if model._meta.db_table not in accounted_for:
            unprotected.append(model._meta.db_table)
    assert not unprotected, (
        f"These tables have an org column but are neither in "
        f"settings.TENANT_TABLES nor listed as a deliberate exemption, so no "
        f"policy was installed on them: {unprotected}"
    )


def test_the_application_role_cannot_bypass_row_level_security(seeded):
    """A superuser or a BYPASSRLS role walks straight through every policy.

    This test exists because the natural fix for a permissions error during
    development is to grant more privileges, and doing that here would leave
    every other test in this file passing while the boundary was gone.
    """
    privileges = connection_privileges()
    assert not privileges["is_superuser"], (
        f"the app is connected as {privileges['role']}, which is a superuser. "
        "Superusers ignore row level security entirely."
    )
    assert not privileges["bypasses_rls"], (
        f"the app is connected as {privileges['role']}, which has BYPASSRLS."
    )


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


def test_raw_sql_that_skips_the_orm_still_only_sees_one_org(seeded):
    """No manager, no queryset, no Django filtering of any kind."""
    with org_context(ORG_A):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT DISTINCT org_id FROM {EXCEPTIONS_TABLE}")
            orgs = {row[0] for row in cursor.fetchall()}
    assert orgs == {ORG_A}, f"raw SQL under {ORG_A} returned rows for {orgs}"

    with org_context(ORG_B):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT DISTINCT org_id FROM {EXCEPTIONS_TABLE}")
            orgs = {row[0] for row in cursor.fetchall()}
    assert orgs == {ORG_B}


def test_a_connection_with_no_org_context_sees_nothing(seeded, raw_connection):
    """Forgetting to establish context must fail closed, not open.

    Uses a separate connection so that nothing Django has already set can
    influence the result. current_setting('app.current_org', true) is NULL
    here, and org_id = NULL is NULL rather than true, so every row is filtered.
    """
    with raw_connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.current_org', true)")
        assert cursor.fetchone()[0] is None, "this connection already has org context"

        for table in (
            EXCEPTIONS_TABLE,
            "tenancy_location",
            "reconciliation_sourcerecorda",
            "reconciliation_sourceentryb",
            "reconciliation_matchnote",
        ):
            cursor.execute(f"SELECT count(*) FROM {table}")
            assert cursor.fetchone()[0] == 0, (
                f"{table} returned rows to a connection with no org context. "
                "A code path that forgets the middleware would leak everything."
            )


def test_asking_for_the_other_orgs_rows_on_purpose_returns_nothing(seeded):
    """The hostile case: the caller knows the other org's id and asks for it.

    A queryset-level boundary cannot survive this, because the malicious filter
    and the protective filter are the same mechanism.
    """
    with org_context(ORG_A):
        assert ReconciliationException.objects.filter(org_id=ORG_B).count() == 0
        assert SourceRecordA.objects.filter(org_id=ORG_B).count() == 0
        assert SourceEntryB.objects.filter(org_id=ORG_B).count() == 0
        assert MatchNote.objects.filter(org_id=ORG_B).count() == 0
        assert Location.objects.filter(org_id=ORG_B).count() == 0
        assert not ReconciliationException.objects.exclude(org_id=ORG_A).exists()


def test_aggregates_cannot_see_across_the_boundary(seeded):
    """Aggregates leak numbers without ever returning a row, so they get their
    own test. A COUNT or SUM that quietly spans tenants is a data leak that no
    row-shaped assertion would catch."""
    from django.db.models import Count, Sum

    def totals():
        return ReconciliationException.objects.aggregate(
            n=Count("id"), total=Sum("system_a_amount")
        )

    with org_context(ORG_A):
        a = totals()
    with org_context(ORG_B):
        b = totals()
    with reset_org_context():
        nothing = totals()

    assert a["n"] > 0 and b["n"] > 0
    assert nothing["n"] == 0 and nothing["total"] is None

    # Both orgs' rows exist, so a boundary-crossing aggregate would be larger
    # than either. Verified by reaching past the policy on purpose.
    with connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {EXCEPTIONS_TABLE} DISABLE ROW LEVEL SECURITY")
        cursor.execute(f"SELECT count(*), sum(system_a_amount) FROM {EXCEPTIONS_TABLE}")
        everything = cursor.fetchone()
        cursor.execute(f"ALTER TABLE {EXCEPTIONS_TABLE} ENABLE ROW LEVEL SECURITY")

    assert everything[0] == a["n"] + b["n"]
    assert a["n"] < everything[0] and b["n"] < everything[0]


def test_fetching_another_orgs_row_by_primary_key_fails(seeded):
    """Direct object reference. The row's id is not a secret; the row is."""
    with org_context(ORG_B):
        other_id = ReconciliationException.objects.values_list("id", flat=True).first()
    assert other_id is not None

    with org_context(ORG_A):
        with pytest.raises(ReconciliationException.DoesNotExist):
            ReconciliationException.objects.get(pk=other_id)


def test_the_two_orgs_see_disjoint_and_complete_sets(seeded):
    """Isolation is only half the requirement; the other half is that nothing
    is silently dropped on the floor."""
    with org_context(ORG_A):
        a_ids = set(ReconciliationException.objects.values_list("id", flat=True))
    with org_context(ORG_B):
        b_ids = set(ReconciliationException.objects.values_list("id", flat=True))

    assert a_ids and b_ids
    assert not (a_ids & b_ids)

    with connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {EXCEPTIONS_TABLE} DISABLE ROW LEVEL SECURITY")
        cursor.execute(f"SELECT id FROM {EXCEPTIONS_TABLE}")
        all_ids = {row[0] for row in cursor.fetchall()}
        cursor.execute(f"ALTER TABLE {EXCEPTIONS_TABLE} ENABLE ROW LEVEL SECURITY")

    assert a_ids | b_ids == all_ids


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------


def test_cannot_insert_a_row_belonging_to_another_org(seeded):
    """The policy's WITH CHECK clause. Without it, one tenant could plant rows
    in another tenant's list, which is a write leak rather than a read leak."""
    with pytest.raises(Exception) as caught:
        with org_context(ORG_A):
            with transaction.atomic():
                ReconciliationException.objects.create(
                    org_id=ORG_B,
                    record_ref="REC-PLANTED",
                    reason_code="AMOUNT_MISMATCH",
                    summary="planted by a test",
                )
    assert "row-level security" in str(caught.value).lower()


def test_cannot_update_a_row_into_another_org(seeded):
    """Moving a row across the boundary is the same leak wearing a hat."""
    with org_context(ORG_A):
        with pytest.raises(Exception) as caught:
            with transaction.atomic():
                ReconciliationException.objects.update(org_id=ORG_B)
    assert "row-level security" in str(caught.value).lower()


def test_deletes_cannot_reach_across_the_boundary(seeded):
    """A tenant that can delete another tenant's rows has crossed the boundary
    even though nothing was ever displayed."""
    with org_context(ORG_B):
        before = ReconciliationException.objects.count()

    with org_context(ORG_A):
        with transaction.atomic():
            deleted, _ = ReconciliationException.objects.filter(org_id=ORG_B).delete()
            assert deleted == 0
            transaction.set_rollback(True)

    with org_context(ORG_B):
        assert ReconciliationException.objects.count() == before


# --------------------------------------------------------------------------
# The domain-specific leak this dataset sets up
# --------------------------------------------------------------------------


def test_the_cross_org_location_mismatch_is_visible_only_to_the_owning_org(seeded):
    """REC-1077 is the trap in the data.

    System A puts it at LOC-102 (ORG-A); System B puts it at LOC-201, which
    belongs to ORG-B. Whichever way ownership is decided, the row must appear
    in exactly one org's list. Reporting it to both would be a leak dressed up
    as helpfulness, and it is the kind of leak an org filter on a queryset
    would never catch, because the row genuinely mentions both tenants.
    """
    with org_context(ORG_A):
        owned = ReconciliationException.objects.filter(record_ref="REC-1077")
        assert owned.count() == 1
        exception = owned.get()
        assert exception.reason_code == "LOCATION_MISMATCH"
        assert exception.detail["crosses_org_boundary"] is True
        assert exception.detail["system_b_location"] == "LOC-201"

    with org_context(ORG_B):
        # ORG-B owns LOC-201 and has its own exceptions there, so the check is
        # not "ORG-B sees nothing at LOC-201" but "ORG-B sees no trace of this
        # ORG-A record", including inside the free-text and detail fields the
        # UI actually renders.
        assert not ReconciliationException.objects.filter(record_ref="REC-1077").exists()
        assert not ReconciliationException.objects.filter(location_id="LOC-102").exists()
        for row in ReconciliationException.objects.all():
            assert "REC-1077" not in row.summary
            assert "ENT/2026/4077" not in (row.entry_ids or [])
        assert not SourceRecordA.objects.filter(record_id="REC-1077").exists()
        assert not SourceEntryB.objects.filter(entry_id="ENT/2026/4077").exists()


def test_an_exception_only_ever_cites_entries_its_own_reader_can_see(seeded):
    """Citations are a second surface with the same risk.

    An exceptions row that cites a System B entry filed under the other org
    would either leak that entry or render a broken citation. Neither is
    acceptable, so every cited entry must be readable in the same org context.
    """
    for org_id in (ORG_A, ORG_B):
        with org_context(org_id):
            for exception in ReconciliationException.objects.all():
                for entry_id in exception.entry_ids or []:
                    assert SourceEntryB.objects.filter(entry_id=entry_id).exists(), (
                        f"{org_id} exception {exception.record_ref} cites entry "
                        f"{entry_id}, which is not visible to {org_id}"
                    )


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def test_the_api_will_not_serve_another_orgs_exception(seeded, api_client, token_for):
    """End to end with a real token, because the middleware is part of the
    control and a unit test of the policy would not exercise it."""
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token_for('bob')}")
    bob = api_client.get("/api/exceptions").json()
    assert bob["count"] > 0
    bob_ids = [row["id"] for row in bob["results"]]

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token_for('alice')}")
    alice = api_client.get("/api/exceptions").json()
    alice_ids = [row["id"] for row in alice["results"]]
    assert not set(alice_ids) & set(bob_ids)

    for exception_id in bob_ids:
        response = api_client.get(f"/api/exceptions/{exception_id}")
        assert response.status_code == 404, (
            f"alice fetched bob's exception {exception_id} and got "
            f"{response.status_code}"
        )


def test_an_unauthenticated_caller_gets_nothing(seeded, api_client):
    assert api_client.get("/api/exceptions").status_code in (401, 403)


def test_query_parameters_cannot_be_used_to_reach_the_other_org(
    seeded, api_client, token_for
):
    """The filters are user input, so they are also an attack surface."""
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token_for('alice')}")
    response = api_client.get("/api/exceptions", {"location_id": ["LOC-201", "LOC-202"]})
    assert response.status_code == 200
    assert response.json()["count"] == 0
    assert response.json()["results"] == []


# --------------------------------------------------------------------------
# The before/after
# --------------------------------------------------------------------------


def _orgs_visible_to(org_id):
    with org_context(org_id):
        return set(
            ReconciliationException.objects.values_list("org_id", flat=True).distinct()
        )


def test_the_database_protection_is_the_thing_doing_the_work(seeded):
    """Remove the database protection, watch the other org's rows appear, put
    it back, watch them go.

    Everything else in this file shows that the boundary holds. This shows
    *what* is holding it. Without it, every passing assertion above would be
    equally consistent with the isolation coming from somewhere incidental,
    and the README's claim would be unfalsifiable.

    This runs inside the test's own transaction, so the table is restored on
    rollback however the test ends.
    """
    assert _orgs_visible_to(ORG_A) == {ORG_A}, "the boundary is already not holding"

    # Remove the protection. DISABLE, not DROP POLICY: see the next test for
    # why those two are not the same thing.
    with connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {EXCEPTIONS_TABLE} DISABLE ROW LEVEL SECURITY")

    try:
        assert _orgs_visible_to(ORG_A) == {ORG_A, ORG_B}, (
            "turning row level security off did not change the result, so "
            "something other than the database is filtering these rows and this "
            "suite is not testing what it claims to test"
        )
        # Unchanged application code, now leaking. Nothing in the view layer
        # was ever going to catch this, which is the honest trade-off of
        # putting the whole boundary in one place.
        with org_context(ORG_A):
            assert ReconciliationException.objects.filter(org_id=ORG_B).exists()
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"ALTER TABLE {EXCEPTIONS_TABLE} ENABLE ROW LEVEL SECURITY")

    assert _orgs_visible_to(ORG_A) == {ORG_A}


def test_a_table_with_row_level_security_but_no_policy_denies_everything(seeded):
    """Postgres applies a default-deny when a table has row level security on
    and no matching policy, so a half-finished or botched policy migration
    breaks the feature loudly instead of opening the boundary quietly.

    Worth pinning down, because "remove the protection" has two plausible
    readings and only one of them leaks. Dropping the policy is the safe
    failure; disabling row level security is the dangerous one.
    """
    with connection.cursor() as cursor:
        cursor.execute(f"DROP POLICY {POLICY_NAME} ON {EXCEPTIONS_TABLE}")
    try:
        assert _orgs_visible_to(ORG_A) == set(), (
            "expected a table with RLS enabled and no policy to return nothing"
        )
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE POLICY {POLICY_NAME} ON {EXCEPTIONS_TABLE}
                    USING (org_id = current_setting('app.current_org', true))
                    WITH CHECK (org_id = current_setting('app.current_org', true))
                """
            )
    assert _orgs_visible_to(ORG_A) == {ORG_A}


def test_forcing_is_what_makes_the_policy_apply_to_django(seeded):
    """FORCE ROW LEVEL SECURITY is easy to leave out and silent when missing.

    Django owns these tables. Postgres exempts a table owner from its own
    policies unless row level security is forced, so without FORCE the policy
    exists, looks correct in the catalog, and does nothing for this app.
    """
    with connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {EXCEPTIONS_TABLE} NO FORCE ROW LEVEL SECURITY")
    try:
        with org_context(ORG_A):
            leaked = set(
                ReconciliationException.objects.values_list("org_id", flat=True).distinct()
            )
        assert leaked == {ORG_A, ORG_B}, (
            "expected the owner to bypass its own policy without FORCE; if this "
            "fails, the app is no longer connecting as the table owner and this "
            "test needs revisiting"
        )
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"ALTER TABLE {EXCEPTIONS_TABLE} FORCE ROW LEVEL SECURITY")

    with org_context(ORG_A):
        assert set(
            ReconciliationException.objects.values_list("org_id", flat=True).distinct()
        ) == {ORG_A}


def test_org_context_does_not_survive_its_transaction(seeded):
    """set_config(..., is_local => true) rather than a plain SET.

    On a pooled connection a session-lifetime setting would carry one tenant's
    context into whichever request picked the connection up next. This asserts
    the setting is transaction-scoped, which is what makes that impossible.
    """
    from tenancy.rls import current_org

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_org', %s, true)", [ORG_A])
        assert current_org() == ORG_A
        transaction.set_rollback(True)

    # A fresh transaction on the same connection starts with no context.
    with transaction.atomic():
        assert current_org() is None
