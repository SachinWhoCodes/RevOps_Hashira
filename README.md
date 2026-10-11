# Casefile

**An evidence-first revenue agent.** Casefile connects a company's CRM, mailboxes, calendars, Slack and
product-usage data, finds the renewals it is about to lose (and the accounts ready to grow), explains each
case with evidence a person can click, and prepares the whole follow-up — CRM task, customer email draft,
CRM update, message to the owner — as a change set that a person approves line by line. A separate process
then carries out exactly what was approved, once, and every step lands in a tamper-evident audit log.
A "time machine" back-test proves on the company's own history how early it would have warned them.

> **Plain code for numbers, AI for language, humans for decisions.**

📄 **Full product and technical document (32 pages):**
[docs/Casefile-Product-and-Technical-Document.pdf](docs/Casefile-Product-and-Technical-Document.pdf)

---

## Why it exists

Renewals are lost because the warning signs — a champion who stops replying, usage that slides, meetings
that stop, a renewal with no next step — sit in different tools that never talk to each other, and nobody can
watch every account. Tools that raise alerts can't show their evidence, leave the follow-up to be done by
hand, and can't prove they ever saved a renewal; AI agents that try invent facts and act without
accountability. **The unsolved problem is not detecting risk — it is turning scattered evidence into the
right, approved action, on time, with a record of why and proof that it worked.**

## What makes it different

| | How |
|---|---|
| **Evidence you can check** | Every sentence cites the email, meeting, usage figure or CRM field behind it. Numbers come from code; a claim checker deletes any AI sentence whose evidence ID, number, date or name doesn't match before anyone sees it. |
| **Actions as a reviewable diff** | The follow-up arrives as before → after lines. Approval binds a SHA-256 hash of the exact change; only the executor holds write keys and it writes exactly that, once — or refuses. Customer email is only ever a Gmail *draft*. |
| **Proof on your own history** | A back-test rebuilds every past renewal as it looked 30/60/90 days before and scores it with the same code as today — with sample sizes, ranges and baselines. An outcome tracker measures what happened after each approved play. |
| **Renewal patterns** | Every finished renewal becomes a learned pattern; each client's open cycle is compared with the finished cycles that looked most alike at the same distance from their decision. |
| **Safe by structure** | The AI never writes (rule R1); customer text is data, never instructions; setup → shadow → live modes; Postgres row-level security between companies; hash-chained, append-only audit log. |

## The loop

```
  HubSpot ─┐   READ ZONE (worker: read keys + AI key)                    WRITE ZONE (executor: write keys, no AI)
  Gmail ───┤   sync → identity → timeline (as of any date) → 12 signals   approval exists? hash matches? not done?
  Calendar ┼─► → points score → case → Claude explains → claim checker    record unchanged? → audit → write once
  Usage ───┘   → rule-based plan → change set ──► PEOPLE APPROVE ──────►  HubSpot task·note·next step · Gmail draft · Slack DM
                                                   (web or Slack)          → read back → outcome at +7/+14/+30 days
```

Five processes from one codebase: **api** (FastAPI), **worker** (syncs, scoring, AI, plans), **executor**
(the only writer), **slackbot** (approval clicks), **web** (React). See the document's Part 4 for depth.

---

## Quick start (synthetic company, no credentials needed)

Requirements: Docker (or Colima on macOS), Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm.

```bash
make up          # Postgres 16 on :5433 + migrations (dev and test databases)
make seed        # generate the synthetic company "Lumora" and load it through the fixture connector
make api         # API on :8000
make web         # UI on :5173 (proxies /api to :8000)
```

Open http://localhost:5173 and sign in as `admin@lumora.test`; the generated password is
`CASEFILE_ADMIN_PASSWORD` in `.env`. Run `make worker` for scheduled syncs, AI jobs and pattern training.

Nothing above calls the AI or writes to any external tool. AI features need `ANTHROPIC_API_KEY` in `.env`
(capped by `AI_DAILY_BUDGET_USD`); `AI_ENABLED=false` switches everything to template output.

> On Windows (no `make`, Postgres on another port): see `CLAUDE.md` §0b.

## Using it with real tools

**Self-service (the normal path).** Register at `/register`, confirm the email, then follow the **Setup**
page: record the data permission, paste a read-only HubSpot key on **Connections** (guides in the app),
connect a mailbox with one click, optionally add Slack and a usage CSV. The company starts in **setup** mode
(read and score only, no AI, no writes), moves to **shadow** (everything proposed and approved, nothing
written) and finally **live**. Every move is audited.

**Operator path (CLI):**

```bash
cd backend
# a new company: prints a one-time admin invite link (keep it out of logs); the admin then uses the Setup page
uv run casefile create-org --slug acme --name "Acme" --admin-email admin@acme.com > ../data/invite.txt
uv run casefile list-orgs
uv run casefile delete-org --slug acme --confirm acme --reason "requested"   # removes all data, with a receipt

# connections from env-var secrets, for the server's default organisation (ORG_SLUG, default "lumora")
uv run casefile connect hubspot --secret-ref READ_HUBSPOT_TOKEN
uv run casefile google-auth --zone read           # interactive Google consent
uv run casefile sync --all
```

**Writing to tools** happens only in the executor: `make executor SHADOW=1` records "would execute";
`make executor` writes approved lines. Sandbox setup: [docs/runbooks/sandbox-setup.md](docs/runbooks/sandbox-setup.md).
Demoing on a client's real data: [docs/runbooks/client-demo.md](docs/runbooks/client-demo.md).

## Useful commands

```bash
make test            # backend tests (377; its own casefile_test database, never real APIs)
make check           # lint + type-check + tests, as CI runs them
make evals           # every free evaluation suite → regenerates docs/RESULTS.md
make backtest        # time-machine back-test over past renewals
make load            # 1,000 accounts × 90 days through the full sync
cd backend && uv run casefile train-patterns --org lumora   # renewal patterns (no AI, ~10 s)
cd backend && uv run casefile --help                        # every CLI command
cd frontend && pnpm lint && pnpm typecheck && pnpm build    # frontend checks
```

## Status

| Area | Status |
|---|---|
| Six-step workflow in real HubSpot, Gmail (drafts) and Slack after one approval | done — **observed live** |
| Checked explanations, injection defence, exactly-once executor, audit chain | done — tested (28/28 planted false claims removed, 53/53 executor-safety tests) |
| Back-test and evaluation suites | done — results on the synthetic company are **simulated** ([docs/RESULTS.md](docs/RESULTS.md)) |
| Level 2: outcome tracker, risk levers, relationship map, account Q&A, Slack approvals, expansion cases, MCP server, renewal patterns | done (Linear routing waits for credentials) |
| Multi-company: row-level security, self-service sign-up, sealed write keys, setup/shadow/live | done |
| Demo hardening (pseudonyms on screen, one-command reset, rehearsals) | next |
| A real customer's data under signed permission; production server | not yet |

Honest headline (simulated): the weekly top 10 beats working the renewal calendar (32% vs 19%) but is
**not clearly better than the best single signal** (30%); lost renewals are flagged a median 75 days ahead
when flagged. Real-data accuracy is untested until a design partner's back-test.

Every number this project reports is labelled **observed** (measured on our code or real tools),
**simulated** (synthetic company), **estimated** or **untested**.

## Repository

```
backend/casefile/   connectors · ingest · identity · timeline (as-of snapshots) · signals · scoring · cases
                    ai (gateway, reader, writer, checker, injection, Q&A) · actions (playbooks, planner,
                    approvals) · executor (write zone) · audit · backtest · evals · outcomes · patterns · api · worker
backend/tests/      377 tests: fakes for AI and writers, recorded HTTP contracts, property-based leakage tests
frontend/src/       React 19 + Vite + Tailwind 4; design system in components/ui, pages in pages/
docs/               implementation plan, ADRs 0001–0011, results, runbooks, the product and technical document
deploy/             production Docker Compose, Caddy, backups
```

## Documentation

- [Product and technical document (PDF)](docs/Casefile-Product-and-Technical-Document.pdf) — problem, solution, user journey, architecture, evidence, business
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md) — phases, exit gates and the eight architecture rules
- [Decision records](docs/adr/) — one ADR per phase (0001 foundations … 0011 renewal patterns)
- [Results](docs/RESULTS.md) — generated by `make evals`; do not edit by hand
- Runbooks: [sandbox setup](docs/runbooks/sandbox-setup.md) · [client demo](docs/runbooks/client-demo.md) · [data-access request](docs/runbooks/data-access-request.md)
- [CLAUDE.md](CLAUDE.md) — the working manual and handoff notes for development sessions

## Team

Sachin Verma · Krishna Dixit — built for the *Autonomous RevOps Agent* hackathon, October 2026.
