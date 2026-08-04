.PHONY: backend-install backend-audit backend-build backend-lock backend-lock-check backend-sync backend-contracts backend-schema-check backend-schema-upgrade backend-test backend-quality backend-run backend-worker reference-analyzer-test reference-analyzer-run evaluation-example-check evaluation-example-run-dev-check evaluation-example-evidence-check frontend-install frontend-build frontend-run postgres-acceptance-up postgres-acceptance-run postgres-acceptance-down postgres-acceptance-static postgres-contention-observe check

PYTHON ?= python3
UV ?= uv
EVALUATION_EXAMPLE_RUN_PLAN_SHA256 := 1950c7887c560c3f8d494417fbdc4353a0b918ce9a4d5ea7995bd79c38faa739
EVALUATION_EXAMPLE_EVIDENCE_MANIFEST_SHA256 := ef91b6f5062f8bc7718f0fec2fe31eccc666be4d73be11eca3a029637bbec5de
POSTGRES_ACCEPTANCE_COMPOSE ?= compose.postgres-acceptance.yaml

backend-lock:
	cd backend && $(UV) lock --no-python-downloads

backend-lock-check:
	cd backend && $(UV) lock --check --no-python-downloads
	cd backend && $(PYTHON) scripts/verify_dependency_lock.py

backend-sync:
	cd backend && $(UV) sync --extra dev --locked --no-python-downloads

backend-install: backend-sync

backend-build:
	cd backend && $(UV) build --build-constraints build-constraints.txt --require-hashes --no-python-downloads

backend-audit:
	cd backend && $(UV) audit --locked --preview-features audit-command --no-python-downloads

backend-test:
	cd backend && $(PYTHON) -m pytest -W error --cov=app --cov-report=term-missing --cov-fail-under=90

backend-quality:
	cd backend && $(PYTHON) -m compileall -q app scripts
	cd backend && $(PYTHON) -m pip check
	cd backend && $(PYTHON) -m app.schema sql --dialect sqlite > /dev/null
	cd backend && $(PYTHON) -m app.schema sql --dialect postgresql > /dev/null

backend-schema-check:
	cd backend && $(PYTHON) -m app.schema check

backend-schema-upgrade:
	cd backend && $(PYTHON) -m app.schema upgrade

backend-contracts:
	cd backend && $(PYTHON) scripts/export_openapi.py --check
	cd backend && $(PYTHON) scripts/export_remote_contract.py --check

backend-run:
	cd backend && FENGMOU_ALLOW_DEMO_ANALYZER=true FENGMOU_OPERATOR_API_KEY=local-operator-change-me FENGMOU_REVIEWER_API_KEY=local-reviewer-change-me FENGMOU_AUDITOR_API_KEY=local-auditor-change-me $(PYTHON) -m uvicorn app.main:app --reload --port 8000

backend-worker:
	cd backend && FENGMOU_VERIFICATION_EXECUTION_MODE=external $(PYTHON) -m app.worker

reference-analyzer-test:
	cd backend && $(PYTHON) -m pytest tests/test_reference_remote_analyzer.py -W error

reference-analyzer-run:
	@test -n "$$FENGMOU_REFERENCE_ANALYZER_BEARER_TOKEN" || (echo "Set FENGMOU_REFERENCE_ANALYZER_BEARER_TOKEN first" >&2; exit 2)
	cd backend && $(PYTHON) -m uvicorn app.reference_analyzer:app --host 127.0.0.1 --port 8010

evaluation-example-check:
	cd examples/evaluation-v0-nonformal && shasum -a 256 -c CHECKSUMS.sha256
	cd backend && $(PYTHON) scripts/evaluate.py validate --manifest ../examples/evaluation-v0-nonformal/dataset.manifest.json
	cd backend && $(PYTHON) scripts/evaluate.py score --manifest ../examples/evaluation-v0-nonformal/dataset.manifest.json --predictions ../examples/evaluation-v0-nonformal/runs/predictions.validation.jsonl --model-statement ../examples/evaluation-v0-nonformal/model/model-statement.json --split validation

evaluation-example-run-dev-check:
	cd backend && $(PYTHON) scripts/evaluate.py run-dev --plan ../examples/evaluation-v0-nonformal/run-plan.json --expected-run-plan-sha256 $(EVALUATION_EXAMPLE_RUN_PLAN_SHA256)

evaluation-example-evidence-check:
	cd backend && $(PYTHON) scripts/evaluate.py verify-dev-bundle --bundle ../examples/evaluation-v0-nonformal/development-evidence --expected-manifest-sha256 $(EVALUATION_EXAMPLE_EVIDENCE_MANIFEST_SHA256)

frontend-install:
	cd frontend && npm ci

frontend-build:
	cd frontend && npm run verify

frontend-run:
	cd frontend && npm run dev

# Alpha17 PostgreSQL acceptance: static unit gates never skip for a missing URL.
# Live up/run require Docker and an explicit FENGMOU_POSTGRES_ACCEPTANCE_URL.
# Missing URL or docker fails hard with exit 2; do not treat Compose presence as proof.
postgres-acceptance-static:
	cd backend && $(PYTHON) -m pytest tests/test_postgres_acceptance.py -W error

postgres-acceptance-up:
	@command -v docker >/dev/null 2>&1 || (echo "docker is required for postgres-acceptance-up; refuse to pretend PostgreSQL is available" >&2; exit 2)
	docker compose -f $(POSTGRES_ACCEPTANCE_COMPOSE) up -d --wait

postgres-acceptance-run:
	@test -n "$$FENGMOU_POSTGRES_ACCEPTANCE_URL" || (echo "Set FENGMOU_POSTGRES_ACCEPTANCE_URL to postgresql+psycopg://USER:PASS@127.0.0.1:55432/fengmou_acceptance before postgres-acceptance-run; the target never falls back to the app database" >&2; exit 2)
	cd backend && $(PYTHON) scripts/postgres_acceptance.py

postgres-acceptance-down:
	@command -v docker >/dev/null 2>&1 || (echo "docker is required for postgres-acceptance-down" >&2; exit 2)
	docker compose -f $(POSTGRES_ACCEPTANCE_COMPOSE) down -v

# Contention observation only. Does not implement SKIP LOCKED. Missing URL => exit 2.
postgres-contention-observe:
	@test -n "$$FENGMOU_POSTGRES_ACCEPTANCE_URL" || (echo "Set FENGMOU_POSTGRES_ACCEPTANCE_URL before postgres-contention-observe" >&2; exit 2)
	cd backend && $(PYTHON) scripts/postgres_contention_observe.py

check: backend-lock-check backend-contracts backend-quality backend-test evaluation-example-check evaluation-example-run-dev-check evaluation-example-evidence-check frontend-build
