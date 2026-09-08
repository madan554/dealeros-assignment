"""Installs the tenant boundary in Postgres.

This migration *is* the security control. Everything else in the repo assumes
it ran. It lives in a migration rather than a bootstrap script so that any
database built from this repo, including the throwaway one the test runner
creates, has the boundary in place with no extra step for anyone to skip.

``DEALEROS_DISABLE_RLS=1`` makes it skip installing the policies. That exists
only so the walkthrough can show the isolation tests failing; it prints a
warning loud enough that nobody sets it by accident.
"""
import sys

from django.conf import settings
from django.db import migrations

from tenancy.rls import disable_rls_sql, enable_rls_sql

WARNING = """
!! ROW LEVEL SECURITY NOT INSTALLED !!
DEALEROS_DISABLE_RLS=1 is set, so tenant tables were created without policies.
Every org can now read every other org's rows. The isolation tests will fail,
which is the point of this switch. Unset it and re-run migrations to restore
the boundary.
"""


def apply_row_level_security(apps, schema_editor):
    if settings.DISABLE_RLS:
        print(WARNING, file=sys.stderr)
        return
    for table in settings.TENANT_TABLES:
        for statement in enable_rls_sql(table):
            schema_editor.execute(statement)


def remove_row_level_security(apps, schema_editor):
    for table in settings.TENANT_TABLES:
        for statement in disable_rls_sql(table):
            schema_editor.execute(statement)


class Migration(migrations.Migration):
    # Depends on both apps because the policies are applied to tables owned by
    # each of them; the boundary is not meaningful with only half in place.
    dependencies = [
        ("tenancy", "0001_initial"),
        ("reconciliation", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(apply_row_level_security, remove_row_level_security)
    ]
