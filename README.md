# Casefile

An evidence-first revenue agent: it connects CRM, email, calendar and product-usage signals,
explains revenue risk with checkable evidence, and proposes actions a person approves line by line.

- What and why: *Autonomous RevOps Agent — Product Blueprint* (PDF, Oct 2026)
- How and in what order: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)
- Decisions: [docs/adr/](docs/adr/) · Sandbox setup: [docs/runbooks/sandbox-setup.md](docs/runbooks/sandbox-setup.md)

## Quick start (synthetic company, no credentials needed)

Requirements: Docker (or Colima), Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm.

```bash
make up          # Postgres on :5433 + migrations (dev and test databases)
make seed        # generate the synthetic company and load it through the fixture connector
make api         # API on :8000
make web         # UI on :5173 (proxies /api to :8000)
```

Sign in as `admin@lumora.test`; the generated password is in `.env` (`CASEFILE_ADMIN_PASSWORD`).
`make worker` runs scheduled syncs and "Sync now" requests.

## Useful commands

```bash
make test        # backend test suite (uses the casefile_test database)
make check       # lint + type-check + tests, as CI runs them
make load        # 1,000 accounts x 90 days through the full sync (Phase 1 load gate)
cd backend && uv run casefile match-report   # identity accuracy vs synthetic ground truth
```

## Connecting real tools (read-only)

```bash
cd backend
uv run casefile connect hubspot                     # uses READ_HUBSPOT_TOKEN
uv run casefile google-auth --zone read             # stores READ_GOOGLE_REFRESH_TOKEN in .env
uv run casefile connect gmail && uv run casefile connect gcal
uv run casefile connect usage_csv --config path=data/usage.csv
uv run casefile sync --all
```

Every number this project reports is labelled **observed** (real data), **simulated** (synthetic or
sandbox), **estimated** or **untested**.
