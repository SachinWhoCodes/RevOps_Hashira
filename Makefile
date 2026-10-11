# Casefile developer commands. Run `make help`.
COMPOSE ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo docker-compose)
UV := cd backend && uv run
TODAY ?= $(shell date +%Y-%m-%d)

.PHONY: help up down db migrate reset synth seed bootstrap sync api worker executor executor-once slackbot sandbox-setup evals backtest web test lint fmt typecheck check e2e load

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-12s %s\n", $$1, $$2}'

up: db migrate ## Start Postgres and apply migrations
down: ## Stop containers
	$(COMPOSE) down

db: ## Start Postgres (port 5433) and wait until ready
	$(COMPOSE) up -d db
	@until $(COMPOSE) exec -T db pg_isready -U casefile -d casefile >/dev/null 2>&1; do sleep 1; done

migrate: ## Apply migrations to dev and test databases
	$(UV) alembic upgrade head
	$(UV) alembic -x url=postgresql+psycopg://casefile:casefile@localhost:5433/casefile_test upgrade head

reset: ## DEV ONLY: drop and recreate the dev database schema (all data is lost)
	$(UV) alembic downgrade base
	$(UV) alembic upgrade head

synth: ## Generate the synthetic company relative to TODAY (default: today)
	$(UV) casefile synth --today $(TODAY)

bootstrap: ## Create the org, an admin login and the fixture connections
	$(UV) casefile bootstrap

seed: synth bootstrap ## Generate synthetic data and load it through the fixture connectors
	$(UV) casefile sync --all

sync: ## Run every active read connection once
	$(UV) casefile sync --all

api: ## Run the API with reload on :8000
	$(UV) uvicorn casefile.api.main:app --reload --port 8000

worker: ## Run the background worker (scheduled syncs, job queue)
	$(UV) python -m casefile.worker

executor: ## Run the executor (write zone; reads .env.executor). SHADOW=1 records would_execute only
	$(UV) python -m casefile.executor run $(if $(SHADOW),--shadow,)

executor-once: ## Process approved lines once and print the outcomes
	$(UV) python -m casefile.executor once

slackbot: ## Listen for Slack approval-button clicks (Socket Mode; reads only .env.slackbot)
	$(UV) python -m casefile.slackbot run

sandbox-setup: ## Connect sandbox write tools and mirror open-case deals into the HubSpot test portal
	$(UV) python -m casefile.executor sandbox-setup $(if $(OWNER),--owner-id $(OWNER),)

web: ## Run the frontend dev server on :5173
	cd frontend && pnpm dev

test: ## Run the backend test suite
	$(UV) pytest

lint: ## Lint backend and frontend
	$(UV) ruff check .
	$(UV) ruff format --check .
	cd frontend && pnpm lint

fmt: ## Auto-format backend
	$(UV) ruff check --fix .
	$(UV) ruff format .

typecheck: ## Type-check backend and frontend
	$(UV) mypy casefile
	cd frontend && pnpm typecheck

check: lint typecheck test ## Everything CI runs

evals: ## Run every free evaluation suite and refresh docs/RESULTS.md (AI suites: make evals AI=1)
	$(UV) casefile evals --out ../docs/RESULTS.md $(if $(AI),--ai,)

backtest: ## Time-machine back-test over past renewals (labels derived from deal history)
	$(UV) casefile backtest

load: ## Load test: 1,000 accounts x 90 days through the full sync pipeline
	$(UV) python -m casefile.tools.load_test --accounts 1000 --days 90
