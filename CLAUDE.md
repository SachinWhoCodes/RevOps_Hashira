# CLAUDE.md — Casefile handoff and working manual

You are picking up a project mid-flight. A previous Claude session (Opus 5.5, another account) built
Phases 0–5 with this user between 2026-10-10 and 2026-10-11. You have none of that conversation.
This file is the handoff: read all of it before touching code. Last updated: 2026-10-11, after
Phase S (self-service) and the move to a Windows machine (§0b). Phases 0–6, C and S are committed.

---

## 0. First ten minutes (do these in order)

1. Read: this file → `docs/IMPLEMENTATION_PLAN.md` (§1 rules, §6 phases, esp. Phase 6–7) →
   `docs/adr/0001…0005` → `docs/RESULTS.md`. The product blueprint (46-page PDF the plan is based
   on) is at `/Users/sachinwhocodes/Downloads/Autonomous RevOps Agent — Product Blueprint.pdf`.
2. Bring the stack up. **On the Windows machine (D:\RevOps_Hashira) read §0b first** — no `make`,
   different port and tool paths. macOS (Docker via Colima):
   ```
   colima start                # Docker runtime; `docker compose` plugin is NOT wired → Makefile uses docker-compose
   make up                     # Postgres 16 on port 5433 + migrations on dev and test DBs
   make test                   # expect 341 passed in ~90 s
   ```
3. Dev servers: `make api` (:8000) and `make web` (:5173), or in the Claude desktop app use the
   preview launch configs `api` / `web` in `.claude/launch.json` (absolute paths on purpose; `.claude/`
   is git-ignored, so on a fresh clone recreate it from the two `make` commands).
   Log in at http://localhost:5173 as `admin@lumora.test`; the password is `CASEFILE_ADMIN_PASSWORD`
   in `.env`. Never print it; to give it to the user:
   `grep '^CASEFILE_ADMIN_PASSWORD=' .env | cut -d= -f2- | tr -d '\n' | pbcopy`
4. Token freshness: the Gmail write token is in Google OAuth **Testing** mode and expires ~7 days
   after 2026-10-11. If Gmail writes fail with `invalid_grant`, ask the user to run
   `cd /Users/sachinwhocodes/Documents/Projects/RevOps_Hashira/backend && uv run casefile google-auth --zone write`
   (interactive browser consent; the user must do it).
5. Then ask the user how they want to proceed (Phase 7 demo hardening is next; open decisions in §13).

## 0b. Windows machine notes (from 2026-10-11, second session)

The project moved to a Windows 11 laptop (`D:\RevOps_Hashira`, Git Bash + PowerShell). What differs:
- **Docker = Rancher Desktop** (`C:\Program Files\Rancher Desktop\Rancher Desktop.exe`; start it first,
  the engine takes ~1 min). `docker-compose` v5 works. Another project's container `hawk-postgres`
  holds 127.0.0.1:5433 and auto-starts — never stop it. Casefile's Postgres runs on **5434**:
  `.env` sets `CASEFILE_DB_PORT=5434`, `DATABASE_URL` and `TEST_DATABASE_URL` (compose reads the port).
- **uv** is in `C:\Users\Lenovo\AppData\Roaming\Python\Python312\Scripts` (not on PATH). In Git Bash:
  `export PATH="/c/Users/Lenovo/AppData/Roaming/Python/Python312/Scripts:$PATH"` (a Windows-style
  path in PATH silently fails). **pnpm** is global via npm. **No `make`**: run the recipe's commands
  directly, e.g. `cd backend && uv run alembic upgrade head` and
  `uv run alembic -x url=postgresql+psycopg://casefile:casefile@127.0.0.1:5434/casefile_test upgrade head`.
- **Use `127.0.0.1`, never `localhost`, in database URLs.** Postgres is published on IPv4 loopback only;
  `localhost` tries `::1` first, and on Rancher/WSL that connection is not refused but hangs — psycopg
  has no connect timeout, so tests/alembic wait forever with no error.
- Preview configs `api`/`web` exist in `.claude/launch.json` with Windows paths (git-ignored).
- `git config core.autocrlf=true`: the working tree has CRLF line endings (the repo stays LF). Python,
  ruff and the tests don't care; a shell script copied into a Linux container from this tree would.
- **The dev database here was created fresh** and seeded locally (synth seed 7, today 2026-10-11 →
  identical company: 8 open cases, back-test numbers identical to the Mac's). Not here: the Mac's
  executed change sets, 877-entry audit chain, AI explanations/labels, `llm_cache`, `llm_call` rows;
  no sandbox write connections or read-back (sandbox-setup not re-run). The $2.9137 spend to date is
  no longer queryable here; count new spend on top. No AI has run on this machine.
- A local UI-review admin `design.review@lumora.test` exists in the dev DB (generated password, kept in
  the session scratchpad only). Delete it or reset its password if you need to.
- Preview config `api-no-ai` runs the API with `AI_ENABLED=false` (env file in the session scratchpad;
  recreate it if the scratchpad is gone) — use it when clicking around so nothing can spend.
- **`backend/tests/test_onboarding.py` is missing from git** (never committed on the Mac): suite here
  is 330 not 341, and `test_selfserve::test_a_read_token_that_can_write_is_refused` fails importing
  its `FakeLive`. Bring the file over from the Mac checkout.
- The full test suite is slower here than on the Mac (Postgres goes through the WSL VM, ~9 minutes).

---

## 1. What we are building

**Casefile** — an evidence-first autonomous RevOps agent for a hackathon problem ("Autonomous RevOps
Agent": connect CRM/email/calendar/usage signals, find revenue risks and opportunities, explain the
evidence, and run the workflow *identify → explain → create task → draft email → update CRM → notify
owner*, with human approval before consequential actions and a reviewable record of why). The user
wants to **win the hackathon and turn it into a real commercial product** — "working in real", not
mocked.

Core loop: connectors sync data → identity resolution links every record to an account → as-of
snapshots → rule-based signals → points-based risk/opportunity scores → cases → Claude writes a
checked explanation (every number/name verified in code) → playbook planner proposes a change set →
a person approves line by line → a separate executor writes exactly what was approved to HubSpot,
Gmail (drafts only), Slack → hash-chained audit log. A "time machine" back-test proves it.

---

## 2. How this user works (non-negotiable)

- **Challenge their thinking**; don't just agree. Push back with a better alternative and reasons.
- **Never assume what can be verified.** Check the code, the DB or the real API first.
- **Label every claim/number**: *observed* (measured on our code or real tools), *simulated*
  (synthetic company), *estimated*, *untested*. Never present mocked behaviour as working.
- **Ruthlessly practical** about scope.
- **After every phase, give a detailed report** (what was proven live vs tested vs not yet, what
  was built, decisions made and why, cost, open decisions, what's next). See §15.
- **Ask before ANY Claude API spend in development** (rule from 2026-10-11; give an estimate first).
- **Minimise Anthropic API spend in testing**: automated tests never call the API (fake clients);
  live checks use the smallest useful sample; rely on the `llm_cache`; dev budget
  `AI_DAILY_BUDGET_USD=2`; report observed spend from `llm_call`; **ask before larger runs**.
  Total spend to date: **$2.9137** (observed 2026-10-11, `select sum(cost_usd) from llm_call`).
- **Secrets**: never print, echo, paste or commit tokens/passwords. Never edit `.env*` with shell
  `echo >>` (it once corrupted the API key line — no trailing newline). Use the CLI `_set_env`
  helper or a newline-safe Python script.
- **Git**: the user commits and pushes themselves. Don't commit or push unless asked.
- **External writes** only where the user granted them (§8). Real company data needs written
  permission before syncing (`docs/runbooks/data-access-request.md`).
- The user often sends short messages ("continue", "go ahead", "approved"). Keep momentum, but stop
  and ask when a decision is genuinely theirs (money, scope, credentials, external side effects).

---

## 3. Status by phase (2026-10-11)

| Phase | What | Status |
|---|---|---|
| 0 | Foundations, schema, synthetic company, access gate | done |
| 1 | Connectors (fixture, HubSpot, Gmail, GCal, usage), identity resolution, as-of timeline | done (live Gmail/GCal *readers* never run against a real mailbox: untested) |
| 2 | Signals, scoring, cases, queue, blind spots | done — ADR 0002 |
| 3 | AI layer: Haiku email reader, Opus case writer, claim checker, injection defence | done — ADR 0003 |
| 4 | Change sets, approvals, separate executor, audit, Slack | **done, exit gate met live** — ADR 0004 |
| 5 | Back-test + evaluation suite | **done, exit gate met** — ADR 0005, `docs/RESULTS.md` |
| 6 | Level 2 differentiators (§14) | **done except 6.7** (needs Linear creds) — 6.1–6.6, 6.8; A1 (HubSpot emails/meetings), A2 (sandbox read-back), rules v4, email retention job done (ADR 0006) |
| C | Pilot readiness (ADR 0007): hosting, team/invites, field mapping + champion fallback, row-level security, onboarding (setup → shadow → live) | done |
| S | Self-service (ADR 0008): sign-up + Brevo email confirmation, connections pasted in the app with guides, sealed write credentials, one executor for all orgs, per-org AI caps | done (live email + Google consent need the user) |
| U | Product UI (ADR 0009): design system, light/dark, new Overview, every page rebuilt, data tables with URL-state pagination, audit keyset paging, split-screen auth, ⌘K palette, lazy routes | done 2026-10-11 (checked in the browser on synthetic data; no automated visual tests) |
| 7 | Demo hardening | after C |
| 8–10 | Pilots / paid launch / scale (production) | later |

Phase 4 live proof (observed): one change set (Lattice Logistics, play `champion_departure`)
approved in the UI reached every target — HubSpot task, note and deal next step (read back and
matched), Gmail draft (never sent), Slack DM — one attempt each; audit chain verified 420/420.

Phase 5 headline (simulated): held-out AUC 0.82 (0.69–0.94, n=89) at 30 days; weekly precision@10
32% (27–37%) vs 19% nearest-renewal-first and 30% best single signal (meeting_gap) — **not clearly
better than the best single signal**; 41% (26–58%) of lost renewals flagged ahead, median 75 days.

---

## 4. Architecture rules (plan §1) and accepted deviations (ADR 0001)

- **R1** AI never writes. Write credentials exist only in the executor process (`.env.executor`).
  A test asserts the API/worker never import `casefile.executor`.
- **R2** Numbers come from code; AI text is checked (`ai/checker.py`) before anyone sees it.
- **R3** One code path for live and back-test: everything reads `AccountHistory.as_of(t)`.
- **R4** Approval binds the exact change: hash of (action, target IDs, expected `before`, final
  payload) + idempotency + live stale check before writing.
- **R5** Append-only, hash-chained audit log (DB trigger blocks UPDATE/DELETE; `casefile_app` role
  has INSERT/SELECT only on `audit_log`).
- **R6** Customer text is data, never instructions (injection rules + AI flag + withholding).
- **R7** `org_id` everywhere.
- **R8** Real sandbox tools + honest labels.
- Deviations from the blueprint (accepted by the user): no LangGraph (Postgres state machine on
  `change_item.status`); executor uses REST behind a Writer interface (not MCP); sandbox-first;
  as-of field history from Phase 1; shadow mode; httpx directly (no vendor SDKs, no google-auth lib).

---

## 5. Repo map

```
backend/casefile/
  config.py            Settings (reads .env only — no write creds here, by design)
  db/models.py         all tables; alembic/versions/* migrations (naming convention! see §12)
  connectors/          base, http (retrying reads), fixture/, hubspot/ (reader + live.py read-only),
                       google/ (auth.py OAuth loopback+PKCE, gmail.py, calendar.py), usage/
  ingest/              normalize/*, sync_runner.py, field_mapping.yaml, util (canonical_json, payload_hash)
  identity/            resolver.py (email/domain/thread/overrides), evaluate.py
  timeline/snapshot.py AccountHistory.load(...).as_of(t) → frozen Snapshot (THE door to history)
  signals/             base.py (registry, run_signals, champion fallback), engagement/crm/usage/language, thresholds.yaml v5
  scoring/             points.py, scoring.yaml v5 (cases: open_risk_at 40), evaluate.py (ranking, expansion)
  cases/service.py     assess(), recompute_account (opens/resolves cases, queues explanations)
  ai/                  gateway.py (kill switch, masking, cache, budget, structured output, cost log),
                       email_reader.py, case_writer.py, checker.py, evidence.py, injection.py,
                       qa.py (6.4 ask-about-an-account: tool runner + read-only tools + checker),
                       prompts/{email_reader,case_writer,email_drafter}.v1.md
  actions/             spec.py (action types, tiers, payload schemas, approval_hash, marker),
                       playbooks.yaml v1 + playbooks.py, policy.py, planner.py, drafter.py (AI email
                       + money filter + name/number checks), targets.py (external IDs), approvals.py
  executor/            SEPARATE PROCESS: __main__.py (run/once/reconcile/sandbox-setup/digest),
                       runner.py (checks → audit started → write → audit result; reconcile),
                       writers/{hubspot,gmail,slack,internal,base}.py, digest.py, sandbox.py, settings.py
  backtest/            outcomes.py (labels from deal history), runner.py, metrics.py (Wilson, block
                       bootstrap), report.py
  evals/               ai_evals.py, run_evals.py (`casefile evals`)
  audit/log.py         append (advisory lock + hash chain), verify_chain
  worker/              __main__.py (APScheduler: syncs, job queue incl. plan_case, rescore 6h,
                       outcomes 1h, email purge 6h), jobs.py
  outcomes/            tracker.py (6.1 checkpoints via as-of snapshots, plays table)
  api/                 main.py + routers: auth, accounts, connections, identity, queue, insights,
                       cases, changes (inbox/plan/approve…/executor status), backtest, team (C1),
                       mapping (C2), onboarding (C4)
  onboarding.py        C4: modes setup/shadow/live, derived checklist, HubSpot connect, usage upload
  secretbox.py         Fernet encryption of pasted read tokens (SECRETS_KEY)
  invites.py           one-time invite links; one email = one organisation
  db/tenant.py         C3 row-level security context (set_tenant, system_scope, set_process_system)
  synth/               synthetic company generator (ground_truth.json = planted stories)
  cli.py               `uv run casefile <cmd>` (synth, bootstrap, create-org, init-secrets-key, sync,
                       google-auth, recompute, explain, eval, evals, backtest, derive-outcomes,
                       match-report, set-password, db-roles, reapply-mapping…)
backend/tests/         341 tests; fake_ai.py (FakeClient), fake_writers.py, factories.py, conftest.py
frontend/src/          React 19 + Vite 7 + Tailwind 4 + TanStack Query + React Router 7 + lucide-react (ADR 0009)
  index.css            design tokens (light + dark CSS variables), motion, base — never raw hex in components
  components/ui/       the design system: Button, Badge, Card/PageHeader, Field/Input/Select…, Callout/
                       EmptyState/Skeleton, Modal/Drawer/Popover/Menu/Tooltip (portals), Segmented/Tabs,
                       StatTile/ScoreRing/Meter/Avatar, Toast — pages compose these
  components/table/    DataTable (search/sort/columns/density/CSV/pages, state in the URL), Pagination,
                       CursorPager (audit keyset paging)
  components/shell/    AppShell (grouped sidebar, live counts, topbar, mode banner), CommandPalette (Ctrl K), nav.ts
  components/auth/     AuthLayout (split screen + gradient flow), PasswordStrength
  components/          charts.tsx (usage, risk timeline, bar list, range bars), domain.tsx (SignalChip,
                       EvidenceChip, HonestyLabel), PlanPanel, AssessmentPanel, RiskLevers, EvidenceDrawer, …
  pages/               Overview (home), Queue, Approvals, Accounts, Account, Outcomes, Backtest, Replay,
                       Audit, Connections, BlindSpots, Identity, Setup (Onboarding), Team, Settings, auth pages
  lib/                 theme, hooks (URL state, local prefs, count-up), session (useMe), csv, cn
docs/                  IMPLEMENTATION_PLAN.md, RESULTS.md (generated), adr/0001–0009,
                       runbooks/{sandbox-setup,data-access-request}.md, spikes/README.md
evals/datasets/        injection_suite.jsonl (30 attacks + 30 benign) — evals/results/ is git-ignored
data/synth/default/    generated synthetic company (git-ignored)
```

---

## 6. Commands

```
make up | db | migrate | reset(DEV ONLY) | seed | sync | api | worker | web
make test | lint | fmt | typecheck | check          # check = what CI runs
make executor [SHADOW=1] | executor-once | slackbot | sandbox-setup [OWNER=100856373]
make evals [AI=1]                                    # all free suites → docs/RESULTS.md (~30 s)
make backtest | load
cd backend && uv run casefile --help                 # full CLI
cd backend && uv run python -m casefile.mcp_server --user admin@lumora.test   # read-only MCP (stdio)
cd backend && uv run python -m casefile.slackbot check|run                     # Slack approval buttons
```
Tooling paths: `uv` at `/opt/homebrew/bin/uv`; `pnpm` at `/Users/sachinwhocodes/.npm-global/bin/pnpm`.
Frontend checks: `cd frontend && pnpm lint && pnpm typecheck && pnpm build`.
Backend checks: `cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy casefile && uv run pytest`.
Line length 110; mypy runs on `casefile/` (CI scope), not tests.

---

## 7. Env files and secrets (names only — values live only on the user's machine)

- `.env` (app/worker zone) currently holds: `CASEFILE_ADMIN_PASSWORD`, `SESSION_SECRET`,
  `ANTHROPIC_API_KEY`, `AI_DAILY_BUDGET_USD=2`, `READ_HUBSPOT_TOKEN`, `GOOGLE_CLIENT_ID`,
  `GOOGLE_CLIENT_SECRET`, `SECRETS_KEY` (Fernet key for tokens pasted during onboarding; created by
  `casefile init-secrets-key`, never printed; losing it makes stored tokens unreadable),
  `EXECUTOR_SEAL_PUBLIC_KEY` (`casefile init-executor-keys`; its private half is
  `EXECUTOR_SEAL_PRIVATE_KEY` in `.env.executor`). Not yet set: `BREVO_API_KEY`, `MAIL_FROM_EMAIL`
  (without them sign-up emails go to `data/outbox`). Store secrets with `casefile set-secret KEY`. Database URLs use the defaults in `config.py` (port 5433). There is no
  `READ_GOOGLE_REFRESH_TOKEN` yet (live Gmail/Calendar *reading* has never been connected).
- `.env.executor` (write zone, chmod 600, read ONLY by `python -m casefile.executor`):
  `WRITE_HUBSPOT_TOKEN`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `WRITE_GOOGLE_REFRESH_TOKEN`,
  `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`. The executor also reads `.env` (it uses `READ_HUBSPOT_TOKEN`
  for pre-write checks and marker searches — the write app has no read scopes).
- Read connection `hubspot/sandbox-readback` (`mode=sandbox_mirror`, READ_HUBSPOT_TOKEN) reads back ONLY
  the mirrored deals and records attached to them. Never point a normal HubSpot read connection at
  the sandbox portal: it holds the user's real auto-logged personal email.
- `.env.slackbot` (chmod 600, read ONLY by `python -m casefile.slackbot`): `SLACK_APP_TOKEN` (Socket Mode).
  Admin is linked to the workspace owner's Slack id (`casefile link-slack`) so sandbox clicks count.
- `.env.seed` would hold `SEED_GOOGLE_REFRESH_TOKEN` (gmail.insert, calendar.events) — not created.
- Template with comments: `.env.example`. All `.env*` except the example are git-ignored.

---

## 8. Live sandbox tools and what the user has authorised

| Tool | Details | Authorisation |
|---|---|---|
| HubSpot | Portal **247655945** ("Acadable", USD, US/Eastern); owner id **100856373**. Write app: create tasks/notes/deals + update deals only (no companies/contacts, no reads). Read app: full read. | "Throwaway portal" — Casefile may write to any record in it. |
| Sandbox mirror | Open-case renewal deals copied into the portal (7 deals), mapped in `external_ref`; write connection config `id_source=external_ref`. Tasks/notes attach to the deal (no companies — least privilege). Deal descriptions say "sandbox mirror of synthetic data". | — |
| Slack | Workspace "Acadable"; bot scopes `chat:write, im:write, users:read, users:read.email` (**no `im:history`** → an uncertain DM is marked "check Slack", never re-sent). All DMs go to the workspace primary owner **U0C88ULHUA2**, labelled "meant for <owner>". | Test DMs **only** to the workspace owner. |
| Gmail | Mailbox **acadablelabs@gmail.com** (drafts only, `gmail.compose`; there is no send path and a test enforces it). OAuth app in Testing mode with that address as test user. | Drafts in that mailbox. Recipients are synthetic `.test` addresses. |
| Anthropic | Reader `claude-haiku-5-5` (effort low, no server-side fallback); writer `claude-opus-5-5` (effort medium, `fallbacks="default"`, beta `server-side-fallback-2026-07-01`). SDK `client.messages.parse` / `client.beta.messages.parse`. Measured cost: ~$0.00005/email read, ~$0.028/case explanation, ~$0.016/email draft. | `AI_DAILY_BUDGET_USD=2`; ask before big runs. |

Executor is **not** left running. Start it only when needed (`make executor`) and stop it after —
its loop also sends the 09:00 (Asia/Kolkata) morning digest to the workspace owner.

---

## 9. Data you are looking at

- Dev DB: synthetic company "Lumora" (vendor domain `lumora.test`, tz Asia/Kolkata), seed 7,
  200 accounts, 730 days, generated relative to 2026-10-11 (`data/synth/default`, `ground_truth.json`
  holds the planted stories). Acme Corp is the blueprint's hero case. 8 open cases (6 risk, 2
  expansion). Org slug `lumora`. Users: admin + 6 owners (Priya Sharma, Rahul Verma, …).
- Already executed in the sandbox: Acme (task, note, next step, DM) and Lattice Logistics (all 5).
- Tests use their own DB (`casefile_test`) and a small synth (seed 11, 30 accounts, 400 days,
  TEST_TODAY 2026-10-10); each test's writes are rolled back (savepoint pattern in conftest).
- Back-test labels: 197 renewals (151 renewed, 20 churned, 17 downgraded, 9 late) derived from deal
  history; only 0.7% of past inbound emails have AI labels, so label-based signals are excluded
  from the back-test.

---

## 10. Key mechanisms worth knowing before changing them

- **Claim checker** (`ai/checker.py`): evidence IDs must exist; numbers/dates must match cited facts
  (unit-aware: % vs days); proper nouns must appear in the evidence pack; money/guarantee regex;
  headline/note may use only `account.*` facts. 20 planted false claims are all removed (test).
- **Draft checks** (`actions/drafter.py`): money talk → line **blocked** (no retry); other issues
  (other known people's names, unknown capitalised words mid-sentence, numbers other than the
  renewal date, internal jargon, promises) → one retry with reasons → template. Sentence-start
  plain English and title-case subjects are allowed (two real Claude drafts are regression tests).
- **State machine** (`actions/approvals.py`): proposed → approved/edited_approved/rejected → queued
  → executing → succeeded/failed/conflict; blocked; superseded; shadow → would_execute. Approving
  queues immediately. Only case owner or admin can decide; "Approve all Tier 1" never includes
  Tier 2 (customer email).
- **Executor** (`executor/runner.py`): approval exists → hash matches → no prior success →
  live `before` still matches (else conflict) → audit "started" + commit → write → audit result.
  Outcomes: WriteResult / WriteRejected(retryable?) / WriteUnknown (search marker first) /
  CannotVerify (leave for a person). Markers: HubSpot body token `cf<item-hex>` (CONTAINS_TOKEN
  search, verified live); Gmail header `X-Casefile-Ref` (Gmail rewrites Message-ID on drafts —
  verified live); Slack message metadata.
- **Back-test** (`backtest/runner.py`): same `run_signals`/`score` as live; horizon (30/60/90 d) and
  weekly rolling modes; tune on the earlier half only; Wilson intervals for proportions, seeded
  bootstrap for AUC, 13-week block bootstrap for weekly series.

---

## 11. Testing conventions

- Never call real APIs in tests: `tests/fake_ai.py` (FakeClient; clear `llm_cache` when a test
  needs a fresh AI answer — identical prompts are served from cache), `tests/fake_writers.py`,
  `respx` for HTTP contract tests.
- DB tests use the `db` or module-scoped `synced` fixtures (rolled back). Commits inside code under
  test become savepoints.
- Every phase adds its safety/regression tests; `make evals` reruns them as labelled suites.

---

## 11b. Row-level security (since 2026-10-11)

- Every tenant table has RLS. The DB owner (dev, tests, migrations) bypasses it; production processes
  log in as `casefile_runtime` and are subject to it. API sessions are tenant-scoped (org in the session
  cookie); worker/executor/CLI call `set_process_system()`. A new code path that runs as the runtime role
  without a tenant sees EMPTY tables — use `db/tenant.py` (`set_tenant`, `system_scope`), and extend
  `tests/test_rls.py` (which runs the API as a non-owner role) when adding endpoints.

## 11c. Organisation modes and onboarding (since 2026-10-11, ADR 0007 C4)

- `org.settings["mode"]`: `setup` (no AI jobs — the worker skips them — and no writes), `shadow`
  (executor records `would_execute`), `live`. Missing mode = setup. Lumora (dev) is `live`; the test
  fixture org `test-org` is created `live`. `casefile/onboarding.py` holds the checklist and moves.
- New company: `casefile create-org --slug … --name … --admin-email …` (prints an invite link once —
  redirect it to a file in your session, never into the transcript). The admin finishes in /setup.
- Credentials: no per-kind default any more — a read connection needs `secret_ref` (operator) or
  `secret_enc` (pasted in the app). Executor = one organisation (`Executor(..., org_id=...)`).
- Throwaway dev org `northwind-test` (admin `admin@northwind.test`, synthetic data from
  `data/synth/onboarding-test`) was created to verify onboarding; it is in setup mode, so its queued
  AI jobs are skipped. Its admin password is a generated test value in the session scratchpad only.

## 11d. Self-service (since 2026-10-11, ADR 0008)

- Strangers register at /register → pending `signup` row + email (Brevo, or `data/outbox` on localhost)
  → /verify/<token> button creates the org (setup mode) and admin. /forgot emails a reset link.
- Connections page (/connections): HubSpot read + write tokens, Google "Connect a mailbox"/"Allow
  drafts" (web OAuth, callback `/api/oauth/google/callback`) or the company's own Google app, Slack
  bot + app-level tokens from the manifest. Write credentials are SEALED for the executor
  (`casefile/sealing.py`; only `casefile/executor/unseal.py` opens them). Never log or return them.
- One executor serves all orgs (`executor/tenants.py`); the slackbot keeps one socket per workspace.
- Dev test orgs created for verification: `northwind-test` (operator path) and `hooli-sign-up-test`
  (self-service path, admin gavin@hooli.test); both in setup mode. Test passwords live only in the
  session scratchpad.

## 12. Gotchas learned the hard way

- Alembic + naming convention: pass existing constraint names through `op.f("ck_…")`, or Alembic
  re-prefixes them (`ck_change_item_ck_change_item_…`).
- DB `server_default=now()` is the *transaction start*: several rows in one transaction share it.
  Set timestamps in the app where order matters (`llm_call.created_at`, `approval.decided_at`).
- In-place mutation of a JSONB dict after flush is not saved; assign a new dict.
- pydantic `EmailStr` rejects the reserved `.test` TLD the synthetic data uses → `actions/spec.py`
  has a plain email validator.
- HubSpot: write app lacks read scopes (403 on GET/search) → reads via read token. Association type
  IDs used inline: task→company 192, task→deal 216, note→company 190, note→deal 214 (deal-only in
  sandbox). Mirror uses v4 "default" associations.
- Gmail: drafts.get with `format=metadata` works under `gmail.compose`; messages.get does not.
- Google OAuth "Access blocked" = the mailbox isn't in the app's test users.
- Slack returns HTTP 200 with `ok:false` for errors.
- `docker compose` plugin isn't installed → `docker-compose`; Colima must be running.
- The preview launch config needs absolute paths (getcwd errors otherwise).
- React lint forbids setState inside effects (derive state instead).
- The user's shell may not be in the repo root — give commands with absolute paths.
- `system_scope(session, False)` RESTORES the previous scope (it used to force tenant mode, which blinds
  system-mode processes under RLS). Pair every `system_scope(s)` with `system_scope(s, False)`.
- Creating roles inside concurrent test transactions deadlocks on `casefile_app` membership: tests use one
  committed probe role (`casefile_rls_probe`).
- HubSpot scope check without writing: POST an empty batch (`{"inputs": []}`) → 200/201 allowed, 403 not.
  403 bodies list `requiredGranularScopes` (any-of).

---

## 13. Open decisions waiting on the user (raise them, don't decide alone)

1. **Case threshold**: tuning half preferred 25 (live default 40); doubles held-out recall at 60 days
   on synthetic data. Recommendation given: keep 40 until a real-data back-test.
2. **Read the 13,230 unread past inbound emails with the AI** so label-based signals can join the
   back-test: estimated ~$0.66, or ~$0.33 via the Batch API. Not approved yet.
3. Still open from the plan (§9 of the plan): hackathon dates, team size, whose real data/which CRM.
4. Optional: add Slack `im:history` (full DM reconciliation); Linear credentials if 6.7 is wanted.

---

## 14. Phase 6 — Level 2 differentiators (done except 6.7; kept for reference — see ADR 0006)

1. **6.1 Outcome tracker** — `play_outcome` table already exists. After a change set executes,
   check at +7/+14/+30 days (worker job): champion replied? meeting booked? usage recovered? score
   change? Case updates itself; leaders see a plays-that-work table. Label results honestly.
2. **6.2 "What would lower this risk?"** — **done 2026-10-11** (`cases/levers.py`, `RiskLevers.tsx`,
   ADR 0006). Built first. Levers are re-scored through the live code; executed-but-not-read-back
   lines are labelled (sandbox CRM reads come from synthetic data — the key constraint on 6.1).
3. **6.3 Relationship map** + new `single_threaded` signal (only one person active in 60 days).
   Adding a signal bumps thresholds/scoring versions → re-run `make evals` and report the delta.
4. **6.4 Ask about an account** — Opus 5.5 via the Anthropic SDK tool runner with read-only
   `@beta_tool` functions over our DB (get_timeline, search_activities, get_usage_series,
   get_contacts, get_signals); answers cite evidence IDs and go through the same checker; 20
   scripted questions. Use the `claude-api` skill for current SDK shapes; keep cost tiny in tests.
5. **6.5 Slack approval buttons** — "Approve all Tier 1" from the digest (channel `slack`), Tier 2
   only "Open in Casefile". Socket Mode via `SLACK_APP_TOKEN`; keep R1 (decisions recorded by the
   app zone, writes still only by the executor).
6. **6.6 Expansion cases** — partly there already (expansion cases + `expansion_seats` play); verify
   end to end and close gaps.
7. **6.7 Product-issue routing to Linear** — needs Linear credentials (ask).
8. **6.8 Casefile MCP server** (if time) — read-only list_cases/get_case/get_evidence.

Then **Phase 7** demo hardening (demo-mode pseudonyms, `make demo-reset`, rehearsed 7-minute run,
recorded backup labelled as recorded, fresh Google tokens on demo morning, offline snapshot).

---

## 15. Phase report format the user expects

Lead with the outcome (done / exit gate met?). Then: what was proven **live** (observed, with IDs
read back) vs **tested** (simulated/fakes) vs **not yet**; a short results table with n and labels;
what to challenge (weaknesses, artefacts of synthetic data); what was built (brief, with file
links); decisions made and why; AI spend this phase and total; open decisions for the user; what's
next. Plain language, no hedging when something is verified, explicit when it isn't. Update the
plan's phase status line and add an ADR for the phase's decisions.

---

Deep history (optional, large): the previous session's transcript is at
`/Users/sachinwhocodes/.claude/projects/-Users-sachinwhocodes-Documents-Projects-RevOps-Hashira/386180e3-26cc-426d-b583-8d3ab5f3a794.jsonl`.
Prefer the ADRs and the plan; read the transcript only for something they don't cover.