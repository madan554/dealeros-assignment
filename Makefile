PY := .venv/bin/python
PIP := .venv/bin/pip
MANAGE := cd backend && ../$(PY) manage.py

.PHONY: help setup db migrate seed ingest backend frontend test test-isolation \
        prove-isolation prove-isolation-broken rls-status clean reset

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

setup:
	python3 -m venv .venv 2>/dev/null || true
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements.txt
	test -f .env || cp .env.example .env
	./scripts/bootstrap_db.sh
	$(MAKE) migrate seed ingest
	cd frontend && npm install
	@echo ""
	@echo "Ready. Run 'make backend' and 'make frontend' in two terminals."

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
