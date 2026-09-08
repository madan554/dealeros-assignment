#!/usr/bin/env bash
# Drops and recreates the application database. Used by `make reset`, and by
# the walkthrough to rebuild with and without row level security.
set -euo pipefail

cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi

DB="${POSTGRES_DB:-dealeros}"
ADMIN_DSN="${ADMIN_DSN:-postgres://${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT:-5432}/postgres}"

echo "Dropping database ${DB}"
psql "$ADMIN_DSN" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS ${DB} WITH (FORCE);"
exec ./scripts/bootstrap_db.sh
