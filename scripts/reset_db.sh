#!/usr/bin/env bash
# Drops and recreates the application database. Used by `make reset`, and by
# the walkthrough to rebuild with and without row level security.
set -euo pipefail

cd "$(dirname "$0")/.."
. ./scripts/load_env.sh
load_env_defaults .env

DB="${POSTGRES_DB:-dealeros}"
ADMIN_DSN="${ADMIN_DSN:-postgres://${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT:-5432}/postgres}"

echo "Dropping database ${DB}"
psql "$ADMIN_DSN" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS ${DB} WITH (FORCE);"
exec ./scripts/bootstrap_db.sh
