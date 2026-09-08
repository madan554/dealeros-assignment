# Django 5.2 needs Python 3.11+, and on a lot of macOS machines plain
# `python3` is still the 3.9 that ships with the command line tools. So pick
# the first interpreter that is actually new enough rather than assuming.
# Override with `make setup PYTHON=/path/to/python3.12`.
PYTHON ?= $(shell for p in python3.13 python3.12 python3.11 python3; do \
	  if command -v $$p >/dev/null 2>&1 && \
	     $$p -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; \
	  then echo $$p; break; fi; done)

PY := .venv/bin/python
PIP := .venv/bin/pip
MANAGE := cd backend && ../$(PY) manage.py

.PHONY: help check-tools setup db migrate seed ingest backend frontend test \
        test-isolation prove-isolation prove-isolation-broken rls-status clean reset

help:
	@echo "make setup                    install deps, create the database, migrate, seed, ingest"
	@echo "make backend                  run the API on :8000"
	@echo "make frontend                 run the UI on :5173"
	@echo "make test                     run the whole backend suite"
	@echo "make test-isolation           run just the tenant isolation suite"
	@echo "make prove-isolation          isolation suite with the boundary IN PLACE (passes)"
	@echo "make prove-isolation-broken   isolation suite with the boundary REMOVED (fails)"
	@echo "make rls-status               ask Postgres what it thinks the boundary is"
	@echo "make reset                    drop and rebuild the database from scratch"

check-tools:
	@if [ -z "$(PYTHON)" ]; then \
	  echo "No Python 3.11+ found. Install one (brew install python@3.12) or run:"; \
	  echo "  make setup PYTHON=/path/to/python3.12"; exit 1; fi
	@command -v psql >/dev/null 2>&1 || { \
	  echo "psql not found. Install Postgres client tools, e.g. brew install postgresql@18"; exit 1; }
	@command -v npm  >/dev/null 2>&1 || { \
	  echo "npm not found. Install Node 20 or newer."; exit 1; }
	@pg_isready -q -h $${POSTGRES_HOST:-127.0.0.1} -p $${POSTGRES_PORT:-5432} 2>/dev/null || { \
	  echo "No Postgres answering on $${POSTGRES_HOST:-127.0.0.1}:$${POSTGRES_PORT:-5432}."; \
	  echo "Start it (brew services start postgresql@18) or set POSTGRES_HOST / POSTGRES_PORT in .env."; \
	  exit 1; }
	@echo "Using $(PYTHON) ($$($(PYTHON) -V 2>&1)), $$(node -v), $$(psql --version)"

setup: check-tools
	$(PYTHON) -m venv .venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements.txt
	test -f .env || cp .env.example .env
	./scripts/bootstrap_db.sh
	$(MAKE) migrate seed ingest
	cd frontend && npm install
	@echo ""
	@echo "Ready. Run 'make backend' and 'make frontend' in two terminals,"
	@echo "then sign in at http://localhost:5173 as alice / demo-password."

db:
	./scripts/bootstrap_db.sh

migrate:
	$(MANAGE) migrate

seed:
	$(MANAGE) seed_users

ingest:
	$(MANAGE) ingest

backend:
	$(MANAGE) runserver 8000

frontend:
	cd frontend && npm run dev

test:
	cd backend && ../$(PY) -m pytest

test-isolation:
	cd backend && ../$(PY) -m pytest tests/test_tenant_isolation.py -v

# The two halves of the walkthrough. See WALKTHROUGH.md.
prove-isolation:
	@echo "== Boundary in place: these should pass =="
	cd backend && ../$(PY) -m pytest tests/test_tenant_isolation.py -q

prove-isolation-broken:
	@echo "== Boundary removed: these should FAIL =="
	cd backend && DEALEROS_DISABLE_RLS=1 ../$(PY) -m pytest tests/test_tenant_isolation.py -q || true
	@echo ""
	@echo "That was the database protection removed. 'make prove-isolation' puts it back."

rls-status:
	$(MANAGE) rls_status

reset:
	./scripts/reset_db.sh
	$(MAKE) migrate seed ingest

clean:
	rm -rf .venv frontend/node_modules frontend/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
