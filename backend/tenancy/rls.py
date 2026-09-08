"""Postgres row level security plumbing.

The tenant boundary lives here and nowhere else. Application code does not
filter by org; it pins the *database session* to an org and then queries
normally. A view that forgets to filter returns nothing, rather than returning
everything.

Two Postgres behaviours make that safe:

* ``ENABLE ROW LEVEL SECURITY`` plus a policy comparing ``org_id`` against the
  ``app.current_org`` session variable.
* ``FORCE ROW LEVEL SECURITY``, which subjects the *table owner* to the policy
  too. Without it, Django owns the tables and would sail straight past them.

``current_setting('app.current_org', true)`` returns NULL when the variable was
never set, and ``org_id = NULL`` is NULL, not true. So the failure mode of
forgetting to establish context is an empty result set, not a leak.
"""
from contextlib import contextmanager

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, connections, transaction

ORG_GUC = "app.current_org"
POLICY_NAME = "org_isolation"


def _tenant_tables():
    return list(settings.TENANT_TABLES)


def enable_rls_sql(table):
    """SQL that puts one table behind the org boundary."""
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;",
        # Without FORCE, the table owner (Django's own role) is exempt.
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;",
        f"""
        CREATE POLICY {POLICY_NAME} ON {table}
            USING (org_id = current_setting('{ORG_GUC}', true))
            WITH CHECK (org_id = current_setting('{ORG_GUC}', true));
        """,
    ]


def disable_rls_sql(table):
    return [
        f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table};",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;",
    ]


def apply_rls(using=DEFAULT_DB_ALIAS, tables=None):
    tables = tables or _tenant_tables()
    with connections[using].cursor() as cursor:
        for table in tables:
            for statement in enable_rls_sql(table):
                cursor.execute(statement)


def remove_rls(using=DEFAULT_DB_ALIAS, tables=None):
    tables = tables or _tenant_tables()
    with connections[using].cursor() as cursor:
        for table in tables:
            for statement in disable_rls_sql(table):
                cursor.execute(statement)


def current_org(using=DEFAULT_DB_ALIAS):
    """Whatever org this database session is currently pinned to."""
    with connections[using].cursor() as cursor:
        cursor.execute(f"SELECT current_setting('{ORG_GUC}', true)")
        return cursor.fetchone()[0] or None


def _set_org(org_id, using=DEFAULT_DB_ALIAS):
    # SET LOCAL cannot take a bind parameter, so use its function form. The
    # third argument is `is_local`: the value dies with the transaction, which
    # is what stops a pooled connection from carrying one tenant's context
    # into the next tenant's request.
    with connections[using].cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [ORG_GUC, org_id or ""])


@contextmanager
def org_context(org_id, using=DEFAULT_DB_ALIAS):
    """Pin this database session to ``org_id`` for the duration of the block.

    Opens a transaction because ``set_config(..., is_local => true)`` is a
    no-op outside one. The previous value is restored on the way out so that
    nesting (ingest walking org by org) behaves.
    """
    if not org_id:
        raise ValueError("org_context requires an org id; use reset_org_context() to clear")
    with transaction.atomic(using=using):
        previous = current_org(using=using)
        _set_org(org_id, using=using)
        try:
            yield
        finally:
            _set_org(previous, using=using)


@contextmanager
def reset_org_context(using=DEFAULT_DB_ALIAS):
    """Explicitly *unset* the org, i.e. simulate a caller with no context."""
    with transaction.atomic(using=using):
        previous = current_org(using=using)
        _set_org(None, using=using)
        try:
            yield
        finally:
            _set_org(previous, using=using)


def rls_status(using=DEFAULT_DB_ALIAS):
    """Report, per tenant table, what the live catalog actually says.

    Used by the isolation tests and by `manage.py rls_status`. Reading the
    catalog rather than trusting our own migration is the whole point.
    """
    tables = _tenant_tables()
    with connections[using].cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname,
                   c.relrowsecurity,
                   c.relforcerowsecurity,
                   (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = ANY(%s) AND n.nspname = current_schema()
            """,
            [tables],
        )
        found = {
            row[0]: {"enabled": row[1], "forced": row[2], "policies": row[3]}
            for row in cursor.fetchall()
        }
    return {table: found.get(table) for table in tables}


def connection_privileges(using=DEFAULT_DB_ALIAS):
    """Superuser / BYPASSRLS flags for the role the app is connected as.

    Both must be false or the policies above are decoration.
    """
    with connections[using].cursor() as cursor:
        cursor.execute(
            "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        name, is_super, bypasses = cursor.fetchone()
    return {"role": name, "is_superuser": is_super, "bypasses_rls": bypasses}
