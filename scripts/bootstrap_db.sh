#!/usr/bin/env bash
# Creates the database and the application role.
#
# The role is deliberately NOT a superuser and deliberately does NOT have
# BYPASSRLS. Postgres lets both of those sail straight past row level security,
# so an app connected as either would have no tenant boundary at all while
# still looking like it did. `manage.py rls_status` checks this at runtime and
# the isolation tests assert it.
#
# CREATEDB is granted only so that Django's test runner can build its own
# test database.
set -euo pipefail

cd "$(dirname "$0")/.."
. ./scripts/load_env.sh
load_env_defaults .env

DB="${POSTGRES_DB:-dealeros}"
APP_USER="${POSTGRES_USER:-dealeros_app}"
APP_PASSWORD="${POSTGRES_PASSWORD:-dealeros_app}"
TEST_DB="${POSTGRES_TEST_DB:-test_dealeros}"

# Connect as an administrative role to do the setup. Override with
# ADMIN_DSN=... if your local Postgres is set up differently.
ADMIN_DSN="${ADMIN_DSN:-postgres://${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT:-5432}/postgres}"

echo "Bootstrapping role '${APP_USER}' and database '${DB}' via ${ADMIN_DSN}"

psql "$ADMIN_DSN" -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${APP_USER}') THEN
    CREATE ROLE ${APP_USER} LOGIN PASSWORD '${APP_PASSWORD}' CREATEDB;
  ELSE
    ALTER ROLE ${APP_USER} LOGIN PASSWORD '${APP_PASSWORD}' CREATEDB;
  END IF;
END
\$\$;

-- Belt and braces: make it impossible for these to be true by accident.
ALTER ROLE ${APP_USER} NOSUPERUSER NOBYPASSRLS;
SQL

if ! psql "$ADMIN_DSN" -tAc "SELECT 1 FROM pg_database WHERE datname = '${DB}'" | grep -q 1; then
  psql "$ADMIN_DSN" -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${DB} OWNER ${APP_USER};"
  echo "Created database ${DB}"
else
  echo "Database ${DB} already exists"
fi

psql "$ADMIN_DSN" -v ON_ERROR_STOP=1 \
  -c "ALTER DATABASE ${DB} OWNER TO ${APP_USER};" >/dev/null
psql "postgres://${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT:-5432}/${DB}" -v ON_ERROR_STOP=1 \
  -c "GRANT ALL ON SCHEMA public TO ${APP_USER};" >/dev/null

echo "Done. Test database '${TEST_DB}' will be created by the test runner."
