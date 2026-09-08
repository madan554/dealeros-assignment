"""Shared fixtures.

The suite runs against a real Postgres test database built by the real
migrations, which means it runs against the real row level security policies.
Faking the database here would delete the only thing worth testing.
"""
import psycopg
import pytest
from django.core.management import call_command
from django.db import connection

ORG_A = "ORG-A"
ORG_B = "ORG-B"


@pytest.fixture(scope="session")
def reconciled_data(django_db_setup, django_db_blocker):
    """Ingest the real CSVs once, and seed one user per org.

    Session scoped and committed, so each test can start a transaction of its
    own and roll it back without re-ingesting.
    """
    with django_db_blocker.unblock():
        call_command("seed_users", verbosity=0)
        call_command("ingest", verbosity=0)
    return True


@pytest.fixture
def seeded(reconciled_data, db):
    """Use this in place of `db` when a test needs the ingested data."""
    return reconciled_data


@pytest.fixture
def raw_connection():
    """A brand new connection as the application role, with no org context.

    This is the shape of the threat the boundary exists for: some future code
    path, job or console session that talks to the database without going
    through the middleware. It gets its own connection so nothing Django has
    already set on its own connection can help it.
    """
    settings_dict = connection.settings_dict
    conninfo = psycopg.conninfo.make_conninfo(
        dbname=settings_dict["NAME"],
        user=settings_dict["USER"],
        password=settings_dict["PASSWORD"],
        host=settings_dict["HOST"] or "127.0.0.1",
        port=settings_dict["PORT"] or 5432,
    )
    with psycopg.connect(conninfo) as conn:
        yield conn


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def token_for():
    """`token_for('alice')` -> that user's API token key."""
    from django.contrib.auth.models import User
    from rest_framework.authtoken.models import Token

    def _token(username):
        return Token.objects.get(user=User.objects.get(username=username)).key

    return _token
