# Casefile — Phase-wise Implementation Plan

Companion to *Autonomous RevOps Agent — Product Blueprint* (Oct 10, 2026). The blueprint says **what** to build and **why**. This plan says **how**, **in what order**, and **how we prove each step is done**.

---

## 0. How to use this plan

- **Phases end at gates, not dates.** A phase is done when its exit gate passes with evidence.
- **Every phase ends with something that runs end to end.** We never carry five half-built modules.
- **Work items are tagged by track:** **[D]** Data & SQL · **[A]** Agent & backend · **[I]** Integrations · **[F]** Frontend & demo.
- **Every number we report carries one label:** **observed** (real company data), **simulated** (synthetic or sandbox data), **estimated** (calculated, not measured) or **untested**.
- **Scope:** Phases 0–7 are the hackathon build (blueprint Levels 1 and 2). Phases 8–10 are the production roadmap (blueprint section 14).

---

## 1. Architecture rules every phase must respect

These rules are the trust foundation buyers pay for. No phase trades them away for speed.

| # | Rule | How the code enforces it |
|---|---|---|
| R1 | The AI reads and proposes; it never writes | Write credentials live only in the `executor` process's environment. The `worker` that calls Claude has read tokens only. The executor has no Anthropic key. |
| R2 | Numbers come from code; AI text is checked | Signals and scores are pure Python. Every AI claim must cite evidence IDs and may only use numbers from a computed facts table. The checker deletes anything else. |
| R3 | Live and back-test share one code path | Timeline, signals and scoring all take an `as_of` timestamp. Live runs use `as_of = now`; the back-test uses the same code with a past date. |
| R4 | Nothing changes without approval of the exact payload | The approval stores a SHA-256 hash of the canonical payload. The executor refuses on hash mismatch, runs each item once (idempotency key), and re-checks the target record before writing. |
| R5 | Every step is recorded permanently | `audit_log` is append-only: a DB trigger blocks UPDATE and DELETE, and rows are hash-chained. If the audit write fails, the action does not run. |
| R6 | Customer text is data, never instructions | Emails reach Claude inside delimited data blocks. An injection detector flags instruction-like text. The action planner is rule-based, so email text cannot invent an action. |
| R7 | Multi-company from day one | Every table has `org_id` now. Postgres Row-Level Security is turned on in Phase 8. |
| R8 | Real tools from day one; honest labels always | We build against real HubSpot, Gmail, Calendar and Slack sandbox accounts that we own. Mocked behaviour is never shown as a feature. |

---

## 2. Where this plan changes the blueprint, and why

Each change is reversible. Veto any you disagree with.

| Blueprint said | This plan does | Why |
|---|---|---|
| LangGraph runs the workflow and pauses for approval with a checkpointer | A Postgres state machine (`change_item.status`) plus a polling executor. The Claude SDK tool runner powers the one truly agentic feature, account Q&A. | Approvals arrive hours later, from the web app or Slack, and the approval *is* a DB row the audit log points at. A LangGraph checkpoint would be a second copy of that state. The pipeline (sync → signals → score → write → check → plan) is deterministic, so a graph framework adds a dependency, not a capability. Revisit if plans become model-driven and multi-step. |
| Official MCP servers carry out approved writes | The executor calls each tool's REST API directly, behind a `Writer` interface. MCP is used where it adds value: Casefile's own read-only MCP server (Phase 6) and optional read tools in Q&A. | The executor is deterministic code. It needs stable request shapes, narrowly scoped write tokens, and idempotency markers. An MCP session from a background job adds an OAuth 2.1 flow and a tool schema that can change under us. The `Writer` interface keeps an MCP-backed writer one class away. |
| Synthetic mirror as a fallback | A synthetic company plus **sandbox tool accounts we own** (HubSpot developer test account, Google test mailbox and calendar, Slack workspace) are the primary development target from day one. Real company data is layered on when permission arrives. | Takes risk #1 (data permission) off the critical path. All six workflow steps run in real tools even if the company only grants read access. Sandbox results are labelled **simulated**. |
| Back-test is Level 2; start its data work early | `as_of` reconstruction is built into the Phase 1 data model through a field-history table. Phase 5 then mostly runs and reports. | Back-tests usually fail from data leakage. Designing for "as of a date" from the start costs far less than retrofitting it. |
| Append-only audit table | Append-only **plus** a hash chain and a "Verify chain" button | About half a day of work. Turns "trust us" into "check it yourself" for judges and security reviewers. |
| Executor writes; failures are reported | Adds a **shadow mode** switch: everything is planned and approved, but the executor only records what it *would* write | Matches the blueprint's own adoption path (weeks 2–3 in shadow mode). It is also the safe default for the first contact with real company data. |

---

## 3. Technology stack

| Layer | Choice | Notes |
|---|---|---|
| Runtime | Python 3.12 with `uv` | `ruff` (lint and format), `mypy`, `pytest`, `hypothesis` |
| API | FastAPI + Pydantic v2 | The OpenAPI schema generates typed frontend API types |
| Database | PostgreSQL 16 | SQLAlchemy 2.0 + Alembic + psycopg 3; JSONB for evidence and payloads |
| Background work | One `worker` process. APScheduler runs schedules; a Postgres job table (`SELECT … FOR UPDATE SKIP LOCKED`) holds queued work. | No Redis. Move to Temporal or Celery in Phase 8 only if metrics demand it. |
| Executor | Separate `executor` process from the same codebase, with its own env file | The only holder of write tokens |
| AI | Anthropic Python SDK (`anthropic`) | **`claude-haiku-5-5`** for per-email reading (effort `low`). **`claude-opus-5-5`** for case writing, email drafts and Q&A; set effort explicitly, because its default is `medium`. Structured output via `client.messages.parse(..., output_format=<Pydantic model>)`. Q&A via `client.beta.messages.tool_runner` with `@beta_tool` read-only tools. Prompt caching on the stable system prompt and examples. Opus calls use the server-side refusal fallback (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`); every call checks `stop_reason == "refusal"`. Historical email backfill uses the Message Batches API (50% cheaper). |
| Integrations | `hubspot-api-client`, `google-api-python-client` + `google-auth-oauthlib`, `slack_bolt` in Socket Mode (no public URL needed), plain SQL for usage | Each sits behind our own `Reader` and `Writer` interfaces |
| Frontend | React + TypeScript + Vite + Tailwind; TanStack Query, React Router, shadcn/ui; `openapi-typescript` | |
| Observability | `structlog` JSON logs. An `llm_call` table records every AI call: model, prompt version, tokens incl. cache, cost, latency, stop reason. | OpenTelemetry and Sentry arrive in Phase 8 |
| Local dev | `docker-compose` (db, api, worker, executor, slackbot, web) + `Makefile` | `make up`, `make seed`, `make test`, `make demo-reset` |
| Hackathon hosting | One VM or container host running the same compose file, in an Indian region if available; Caddy for HTTPS; nightly `pg_dump` | |

---

## 4. System layout

```
                ┌──────────────── READ ZONE · no write credentials ────────────────┐
  HubSpot ──┐   │  worker:                                                         │
  Gmail ────┤   │   sync → normalize → identity match → timeline(as_of)            │
  Calendar ─┼──►│   → signals (code) → score (code) → evidence pack                │
  Usage DB ─┘   │   → case writer (Claude) → claim checker (code)                  │
                │   → planner (playbook rules; Claude fills text only) → change set│
                └───────────────────────────────┬──────────────────────────────────┘
                                                │ change_item rows (proposed)
                     web app / Slack ──────►  api: approve · edit · reject
                                                │ (hash of the exact payload)
                ┌──────────────── WRITE ZONE · no AI key ──────────────────────────┐
                │  executor: approval hash ok? → run-once key → record unchanged?  │
                │            → audit "started" → write → audit "succeeded/failed"  │
                └──► HubSpot task / note / next step · Gmail draft · Slack DM ─────┘
                                     results flow back in through the next sync
```

| Process | Zone | Holds | Does |
|---|---|---|---|
| `api` | Read + approvals | DB credentials, session secret | Serves the UI; records approvals and edits |
| `worker` | Read | Read tokens, Anthropic key | Sync, match, timeline, signals, scoring, case writing, planning |
| `executor` | Write | Write tokens only | Executes approved items; reconciles after crashes |
| `slackbot` | Notify | Slack bot and app tokens | Morning digests; Slack buttons that create approval rows (Phase 6) |
| `web` | — | Nothing | Static React app |

### Repository layout

```
RevOps_Hashira/
├── docker-compose.yml   Makefile   .env.example   README.md
├── docs/                IMPLEMENTATION_PLAN.md, adr/, spikes/, runbooks/
├── backend/
│   ├── pyproject.toml   alembic/
│   ├── casefile/
│   │   ├── config.py          db/            (models, session, migrations helpers)
│   │   ├── connectors/        base.py (Reader/Writer protocols)
│   │   │   ├── fixture/       hubspot/   google/ (gmail, calendar)   slack/   usage/
│   │   ├── ingest/            raw store, normalizer, sync runner, field mapping
│   │   ├── identity/          resolver, freemail list, manual overrides
│   │   ├── timeline/          Snapshot + as_of loader (the only door to history)
│   │   ├── signals/           one module per signal + registry + thresholds.yaml
│   │   ├── scoring/           points model + scoring.yaml
│   │   ├── ai/                gateway, prompts/ (versioned), email_reader, injection,
│   │   │                      evidence_pack, case_writer, checker, templates, qa_agent
│   │   ├── planner/           playbooks.yaml, planner, money_filter
│   │   ├── changes/           state machine, canonical hashing, approvals
│   │   ├── executor/          runner, reconcile, writers/   ← WRITE ZONE
│   │   ├── audit/             append + hash chain + verify
│   │   ├── backtest/          outcomes, runner, baselines, metrics
│   │   ├── outcomes/          post-action tracker (Phase 6)
│   │   ├── demo/              masking
│   │   ├── api/               FastAPI app + routers
│   │   └── worker/            scheduler, job queue
│   └── tests/
├── frontend/            Vite React app
└── evals/               datasets/, run_evals.py, results/
(the synthetic company generator lives in backend/casefile/synth/)
```

---

## 5. Data model v1 (created in Phase 0, filled in by later phases)

Every table has `org_id`. Timestamps are stored in UTC; day boundaries are computed in the account's time zone (default `Asia/Kolkata`).

| Group | Table | Key columns |
|---|---|---|
| Identity | `org`, `app_user` | user: email, role (owner/admin/leader), `slack_user_id`, `crm_owner_id` |
| Connections | `connection` | kind, zone (read/write), status, scopes, secret ref, `cursor` JSONB, `last_success_at`, `last_error` |
| Raw | `raw_record` | source, object type, external id, payload JSONB, payload hash, fetched_at (dedupe on hash) |
| Entities | `account` | crm_id, name, `domains[]`, owner, ARR, renewal_date, seats_purchased, segment |
| | `contact` | account, email, name, title, role (champion/economic_buyer/user/unknown), status (active/left/bounced) |
| | `deal` | account, type (new/renewal/expansion), stage, amount, close_date, next_step, next_step_date |
| **As-of backbone** | `field_history` | entity type and id, field, value, **`valid_from`** (when the value became true or known), source ref |
| Activity | `activity` | account (nullable), contacts, type (email/meeting/call/note/task), direction, `occurred_at`, source id, subject, snippet (≤500 chars), match method and confidence, `injection_flag`, injection reason |
| | `email_label` | activity, label, confidence, verbatim quote, status (accepted/possible/quarantined), model, prompt version |
| | `usage_daily` | account, date, active_users, key_actions, department breakdown JSONB |
| Analysis | `signal` | account, type, polarity (risk/opportunity/urgency/positive), value, `facts` JSONB, window, evidence refs, `as_of`, engine version |
| | `assessment` | account, `as_of`, risk, opportunity, confidence, priority, breakdown, claims JSONB, status (verified/fallback), checker report, model, prompt version |
| | `case` | account, kind (risk/expansion), status (open/snoozed/resolved/marked wrong), owner, feedback |
| Action | `change_set` | case, playbook id and version, derived status |
| | `change_item` | seq, action type, target system and ref, `before`, proposed payload, final payload, payload hash, tier, evidence ids, status, idempotency key, external result |
| | `approval` | change item, user, decision, edited?, **approved payload hash**, channel (web/slack), time |
| | `execution_attempt` | change item, attempt number, status, error, external id, timings |
| Trust | `audit_log` | seq, ts, actor, action, entity, evidence ids, approval id, details, model and prompt version, `prev_hash`, `hash` |
| | `llm_call` | purpose, model, prompt version, input/output/cache tokens, cost, latency, stop reason, account |
| Proof | `renewal_outcome` | account, renewal date, outcome (renewed/downgraded/churned/late), ARR before and after, label source |
| | `backtest_run`, `backtest_result` | config; per account × horizon: score, rank, flagged?, outcome, baseline scores |
| | `eval_run` | suite, metrics JSONB, label (observed/simulated), commit SHA |
| Config | `policy`, `config_version` | action → tier and enabled; versioned thresholds, points and playbooks |
| Level 2 | `outcome` | change set, signals before and after, result (improved/no change/worse/pending), measured at |

---

## 6. The phases

### Phase 0 — Foundations and access gate

**Goal.** Everyone can run the whole stack locally against sandbox tools we own. The data-permission request is out. The riskiest unknowns are answered by short experiments.

**Build**
1. **[A] Repo scaffold.** `git init`; monorepo layout from section 4; `uv`, ruff, mypy, pytest; `pnpm` + Vite + TypeScript; docker-compose with Postgres 16; Makefile; GitHub Actions CI for lint and tests; `.env.example`. `.gitignore` covers `data/`, `.env*` and exports. Add a gitleaks pre-commit hook so no secret or customer file is ever committed.
2. **[D] Schema v1 + first Alembic migration** with every table in section 5, even empty ones. The schema is the contract between tracks.
3. **[D] Synthetic company generator (`synth/`).** Seeded and reproducible: 200–300 accounts, contacts with roles, 24 months of renewal deals, email threads, meetings and daily usage. It writes JSONL in exactly the shape the normalizer expects, plus a ground-truth file. Scenario library:
   - *Silent churn* (the Acme story): champion goes quiet, usage slides, no next step.
   - *Champion departure* (bounce or "I have left" auto-reply).
   - *Usage cliff*, *late renewal*, *downgrade*.
   - *Expansion*: seat saturation and a new team adopting.
   - *Healthy controls*.
   - *Noisy big account*, to test bias from email volume.
   - *Messy identity*: personal Gmail addresses, a reseller domain, two accounts sharing a parent domain.
   - *Injection emails*.
   
   Patterns carry noise so the agent can't score perfectly.
4. **[I] Sandbox tool stack.**
   - HubSpot developer test account, with a read-scoped and a write-scoped app token.
   - Google: either a Workspace test domain with an **Internal** OAuth app (no Google verification needed), or a test Gmail account with an External app in Testing mode. Testing mode allows up to 100 test users, and its tokens expire every 7 days.
   - Slack workspace plus a Slack app in Socket Mode.
5. **[I] Sandbox seeders.** Push the synthetic company into the sandbox tools:
   - HubSpot companies, contacts, deals and engagements.
   - Backdated Gmail messages via `users.messages.insert` with `internalDateSource=dateHeader`.
   - Past and future Calendar events.
   
   HubSpot can't backdate property history, so the sandbox's *history* for back-tests comes from the fixture loader (Phase 1).
6. **Real-data access gate.**
   - One-page request: which data, purpose, duration, who sees it, deletion promise.
   - Week-0 questionnaire: CRM and edition; Google Workspace or not; Slack; where usage lives; renewal and churn count over 12–24 months; which records demo writes may touch.
   - Start the historical export request now; the back-test needs it.
7. **Spikes.** Each is time-boxed, answers one question, and is written up in `docs/spikes/`.

   | Spike | Question | Decides |
   |---|---|---|
   | S1 [I] | Can we read customer threads incrementally (`history.list`) through an Internal Workspace app? | Gmail route or fallback (export / forwarding address) |
   | S2 [I] | HubSpot: do `propertiesWithHistory`, email and meeting engagements (given the "sensitive data" setting), task and note creation, and the deal next-step update all work with our tokens? Same operations via the HubSpot remote MCP server, for comparison. | Final property names; REST vs MCP for each operation |
   | S3 [I] | Can a Slack Socket Mode bot send a DM with a button and receive the click? | Notification and approval channel |
   | S4 [D] | How accurate is domain-based matching on 50 real or sandbox emails? | Effort needed in identity resolution |
   | S5 [A] | Round-trip `client.messages.parse` with a Pydantic schema on Haiku 5.5 and Opus 5.5. Log tokens and latency. | Gateway design; first cost numbers |
   | S6 [D] | What shape will product usage arrive in (read-only view, warehouse export, CSV)? | Usage connector |
   | S7 [I] | Can a Gmail draft be found again by a Message-ID we set, for crash recovery? | Executor reconcile strategy |

**Exit gate**
- `make up && make seed` starts the stack with the synthetic company loaded in the DB *and* in the sandbox tools.
- CI is green.
- All seven spikes are answered in writing.
- The data request has been sent.
- **Real-data sub-gate** (does not block Phase 1): we can read one real account's CRM record, emails, meetings and usage.

---

### Phase 1 — Data spine: connectors, identity, as-of timeline

**Goal.** One ordered, as-of-capable timeline per account, built from every source, with freshness and match accuracy measured.

**Build**
1. **[I] Connector framework.**
   - `Reader.sync(cursor) -> (records, next_cursor)`, with cursors stored in `connection`.
   - Retries with backoff that honours `Retry-After` on 429s.
   - A "stale since" status when a source keeps failing.
   - Raw payloads stored with a hash, so re-syncing never duplicates.
2. **[I] Readers:**
   - **fixture** (synthetic JSONL, carrying `valid_from`).
   - **HubSpot**: companies, contacts, deals with `propertiesWithHistory` on stage, amount, close date, next step, renewal date and contact status; emails, meetings, notes and tasks; owners.
   - **Gmail**: only threads involving known customer domains or contacts, filtered by query, so we fetch the minimum. Snippets are stored; full bodies are kept only for a limited retention window.
   - **Google Calendar**: events with external attendees, synced incrementally via `syncToken`.
   - **Usage**: read-only SQL view, with a CSV import fallback.
3. **[D] Normalizer + field mapping.** Converts raw records into canonical entities and `field_history` rows. Mapping lives in YAML: which CRM property means renewal date, ARR, champion role, seats. Phase 2 adds an admin screen for it.
4. **[D] Identity resolver.** Ordered rules; each match stores its method and confidence:

   | Rule | Confidence |
   |---|---|
   | Manual override | 1.0 |
   | Exact contact email | 1.0 |
   | Thread inheritance (reply in a matched thread) | 0.95 |
   | Account domain, excluding a free-mail list and our own domain | 0.9 |
   | Meeting-attendee majority | 0.8 |
   
   A domain that maps to two accounts (resellers, subsidiaries) is marked **unmatched with candidates**. Unmatched records go to the blind-spot list.
5. **[D] Timeline + `Snapshot` (the only door to history).** `load_snapshot(account_id, as_of)` returns only records with `occurred_at ≤ as_of`, and field values with `valid_from ≤ as_of`. Signals receive a `Snapshot` and never query the DB directly, which makes leakage structurally hard.
6. **[A] Sync scheduler.** Calendar and CRM poll every 15 minutes; Gmail polls every 5 minutes through incremental history. Only accounts whose data changed are marked "dirty" for recompute.
7. **[F] UI v0.** Login; accounts list; account timeline (raw, filterable); Connections page with last sync and "stale since".

**Tests**
- Normalizer tests per source on recorded payloads.
- Resolver tests: free-mail, subdomains, shared domains, reseller.
- **Leakage property test** (`hypothesis`): records with random times; a snapshot never contains anything after `as_of`.
- Re-sync idempotency.

**Exit gate**
- A timeline exists for every synthetic account.
- Load: 1,000 synthetic accounts × 90 days refresh in under 15 minutes (simulated).
- Real data: identity resolution is at least 95% correct on 200 hand-checked activities, and every unmatched record is listed (observed).

---

### Phase 2 — Signals, scoring and the queue

**Goal.** A ranked account list with a transparent score that a CSM agrees is sensible.

**Build**
1. **[D] Signal framework.**
   - Each signal is a pure function `(Snapshot, thresholds) -> list[SignalHit]`.
   - A hit carries: value, `facts` (every number the explanation may later use, e.g. `usage_drop_pct=38`, `days_since_last_meeting=21`), window and evidence refs.
   - Thresholds live in versioned YAML; the engine version is stamped on every row.
2. **[D] Deterministic signals.** Blueprint starting rules; the back-test tunes them later.

   | Signal | Rule | Type |
   |---|---|---|
   | `meeting_gap` | 21+ days since the last customer meeting and nothing booked | Risk |
   | `missing_next_step` | Renewal deal next step is empty, or its date has passed | Risk (hygiene) |
   | `champion_silence` | 2+ outbound emails to the champion over 10+ days with no reply | Risk |
   | `usage_decline` | Active users or key actions down 25%+ (last 28 days vs the 28 before), with a minimum baseline so tiny accounts don't flap | Risk |
   | `renewal_proximity` | Renewal within 90 days | Urgency |
   | `champion_departure` | Bounce, CRM contact status "left", or an auto-reply that matches a regex (Claude confirms in Phase 3) | High risk |
   | `seat_saturation` | 90%+ of purchased seats in use | Opportunity |
   | `new_team_adoption` | New active users from a new department (only if usage carries department data) | Opportunity |
   | `executive_engagement` | Economic buyer attended a meeting in the last 60 days | Positive |

3. **[D] Scoring v1** (points model, config in YAML):
   - `risk = clamp(Σ risk points − Σ positive points, 0, 100)`; `opportunity` is scored separately.
   - `confidence` comes from source coverage: which sources have data for this account. Low coverage shows "low confidence".
   - `priority = risk/100 × ARR × urgency(days_to_renewal)`.
   - The full breakdown is stored with the score.
4. **[A] Case lifecycle.** Open a case when risk or opportunity crosses its threshold. Re-assess only when signals change. Keep the assessment history. Cap new cases per owner per day to prevent alert fatigue.
5. **[D] Blind-spot report.** Per account: missing usage, no champion, no renewal date, no meetings ever, unmatched activities, stale connectors. Also an org-level rollup.
6. **[F] Queue UI.** Ranked cases per owner showing revenue at stake, days to renewal and top signals; score-breakdown popover; filters. Admin pages for field mapping and thresholds, read-only for now.

**Tests**
- One unit test per signal, plus the blueprint's edge cases: exactly 21 days, time-zone boundaries, empty data, a tiny-baseline usage drop.
- Golden tests for scoring.
- Synthetic scenario test: planted risk stories rank near the top; healthy controls don't.

**Exit gate** (revised in Phase 2, see ADR 0002)
- On synthetic data, among accounts whose planted decline started at least 14 days ago: precision@10, recall@k and AUC are reported, and cases opened at the threshold have high precision (simulated). The original wording, "all planted at-risk accounts in the top decile", could not hold: 44 of 180 accounts are planted at-risk and the top decile holds 18.
- On real data, a CSM reviews the top 20 and calls the ranking sensible (observed, qualitative).

---

### Phase 3 — AI layer: reading emails, writing and checking explanations

**Goal.** Every case has an explanation in which every sentence links to evidence a person can click. Nothing unverified is ever shown.

**Build**
1. **[A] LLM gateway (`ai/gateway.py`).**
   - One function: `structured(purpose, role, system, data_blocks, schema, prompt_version)`. Roles: `reader` = `claude-haiku-5-5`; `writer` = `claude-opus-5-5`.
   - Uses `client.messages.parse` with Pydantic schemas and explicit effort.
   - Handles refusals: server-side fallback on Opus; on Haiku, a refusal leaves the email unlabelled.
   - Logs every call to `llm_call` with cost from a price table.
   - Caches results by hash of (prompt version, model, input), so unchanged accounts cost nothing.
   - Uses prompt caching on the frozen system prompt and examples.
   - Masks phone numbers and similar details before sending.
   - Kill switch: `AI_ENABLED=false` switches to template-only output.
2. **[A] Email reader (Haiku).**
   - Output per email: `labels[{label, confidence, quote}]` plus `injection{suspected, quote}`.
   - Labels: complaint, frustration, competitor_mention, upgrade_interest, pricing_question, seat_request, champion_left, out_of_office, none.
   - **Verification:** each `quote` must appear verbatim in the email, or the label is dropped. Confidence below 0.7 is shown as "possible" and adds no points.
   - New signals: `negative_tone`, `upgrade_interest`, and confirmation of `champion_departure`.
   - Historical backfill runs through the Message Batches API.
3. **[A] Injection defence.**
   - (a) A rule detector covers text addressed to an AI or assistant; "ignore previous instructions"; imperative CRM verbs ("mark … healthy", "set … score"); discount offers aimed at the agent; and hidden text (white-on-white, zero-width characters, `display:none`), which is stripped *and* flagged.
   - (b) The Haiku flag; the union of (a) and (b) sets `activity.injection_flag`.
   - (c) Labels from flagged emails are quarantined and add no points.
   - (d) Customer text always travels inside `<customer_email id=… untrusted="true">` blocks.
   - (e) The case is shown with a warning banner that quotes the flagged text.
4. **[A] Evidence pack builder.**
   - For an account's signals, gather the cited records and give them short IDs `E1…En`.
   - Each entry carries type, date, a masked snippet of at most 280 characters, and a source link.
   - Plus a **facts table**: every number and date the writer may use.
5. **[A] Case writer (Opus 5.5).**
   - Input: account header, signals with facts, evidence pack, blind spots.
   - Output schema: `{headline, claims:[{text, evidence_ids[], fact_keys[]}], blind_spot_note}`.
   - Instructions: one idea per claim, only the given facts, cite everything.
6. **[A] Claim checker (pure code).** A claim passes only if all of these hold:
   - It cites at least one evidence ID, and every ID exists in the pack.
   - Every number, percentage, day count and date in the text, extracted by regex and date parser, matches a value in its cited facts (within stated rounding).
   - Every fact key exists.
   - Every named person is a contact in the pack.
   - It contains no money promises.
   
   Failing claims are removed and logged. If no claims survive, or the second attempt also fails, a **deterministic template explanation** is built from the signals (one cited sentence per signal type). The case shows "verified" or "template".
7. **[F] Case screen.** Headline, claims with **evidence chips**, and an evidence drawer that opens the original email, meeting, usage chart or CRM field history. Also: signals with points, blind spots, injection banner, and a "This was wrong" button.

**Tests and evals**
- Checker unit tests: **20 planted false claims** (wrong number, fake E-ID, uncited, wrong date, invented person) — all must be removed.
- Injection suite: 30 crafted emails — all flagged or harmless.
- Email-label eval vs a keyword baseline: synthetic set now (simulated); 200 hand-labelled real emails once permitted (observed).

**Exit gate**
- 20 generated cases, with 100% of shown claims passing the checker. *(Run on the 8 open cases so far to keep test spend minimal; ~$0.25 more covers 20.)*
- All planted claims removed.
- Cost per case and per email measured from `llm_call` (observed).

---

### Phase 4 — Act safely: change sets, approvals, executor, audit, Slack

**Goal.** All six workflow steps happen in the real tools after human approval, exactly once, with a complete record of why. **This completes blueprint Level 1.**

**Build**
1. **[A] Action policy (tiers from the blueprint).** Stored in `policy`, editable by admins.

   | Tier | Covers | Approval |
   |---|---|---|
   | 0 | Reading and drafting inside Casefile | Automatic |
   | 1 | CRM task, CRM note, next-step field, Slack DM to the owner | Required; can be approved as a batch |
   | 2 | Gmail draft to a customer | Approved one by one; never sent |
   | — | Deal amount or stage | Blocked unless explicitly enabled |
2. **[A] Playbooks (versioned YAML).** Rules map signal combinations to a play and an ordered list of action templates. Starting plays: `champion_silent_renewal_near`, `usage_decline`, `meeting_gap`, `champion_departure` (reach the economic buyer), `missing_next_step` (hygiene only), `expansion_seats`. If nothing fits, the play is a single "review manually" item.
3. **[A] Planner.**
   - Builds `change_item`s with: `before` (re-read live at proposal time for field updates), proposed payload, tier, evidence IDs and the claims behind it.
   - Claude fills **only text slots**: email subject and body (Opus 5.5, with tone rules, from the verified claims and the play's purpose).
   - Note bodies are assembled from verified claims by code.
   - **Money filter** on drafts (discount, refund, price, credit, "% off", free months, contract, invoice): a hit blocks the line with "money talk stays human".
4. **[A] State machine + hashing.**
   - Item states: `proposed → approved | edited_approved | rejected → queued → executing → succeeded | failed | conflict`.
   - Payloads are canonical JSON (sorted keys) hashed with SHA-256. An edit creates a new final payload, and the approval hashes that.
5. **[F] Approval inbox.**
   - Per-line diff (before → after) with the "why" column linking evidence; full editor for email drafts.
   - Approve, edit or reject each line; "Approve all Tier 1" excludes Tier 2 by design.
   - Only the account owner or an admin can approve.
6. **[I] Executor (write zone, separate process).**
   - Claims `queued` items with `SKIP LOCKED`, then checks in order:
     1. The approval exists.
     2. `sha256(final_payload)` equals the approval hash.
     3. No earlier success exists for this idempotency key.
     4. For field updates, the live value still equals `before`; otherwise the item becomes `conflict` and goes back for re-approval.
   - Writes audit "started" (abort if that fails) → calls the writer → writes audit "succeeded" or "failed".
   - **Writers:** HubSpot task (associated to company and deal, assigned to the owner), HubSpot note, deal next-step update, Gmail draft (`drafts.create` only — there is no send path in the code, and a test asserts it), Slack DM.
   - **Exactly-once across crashes:** each external object carries a marker — `[cf:<item_id>]` in task and note bodies, a Message-ID we set on drafts. On restart, items stuck in `executing` are reconciled by searching for the marker before any retry.
   - Partial runs report which lines succeeded and offer per-line retry.
   - A **shadow-mode** switch records `would_execute` instead of writing.
7. **[A] Audit log.**
   - The DB role can only INSERT and SELECT; a trigger rejects UPDATE and DELETE.
   - `hash = sha256(prev_hash || canonical(row))`.
   - Events: case opened, assessment (model + prompt version), change set proposed, edit, approve, reject, execution started/succeeded/failed/conflict, injection flagged, config changed.
   - **[F]** Audit screen with filters, a "Verify chain" button, and JSON/CSV export.
8. **[I] Slack.**
   - Morning digest DM per owner at 09:00 local time: top 3 cases by priority, with an "Open case" link.
   - The `notify_owner` action posts the case summary after approval.

**Tests (blueprint executor-safety plan)**
- Approve twice → one write.
- Kill the executor between the external call and the DB commit → reconcile finds the marker, no duplicate.
- Change the CRM record after proposal → `conflict`.
- Execute an unapproved item → refused and logged.
- Tamper with the payload in the DB after approval → hash mismatch, refused and logged.
- Audit insert fails → no write.
- Run the injection suite end to end → zero unapproved writes.

**Exit gate (Level 1 complete)**
- One approved change set appears correctly in every target tool in the sandbox: CRM task, next step, note, Gmail draft, Slack DM. The same holds on agreed real or test records when permitted.
- The audit chain verifies.
- All safety tests pass.

---

### Phase 5 — Prove it: back-test and evaluation suite

**Goal.** A results table we can defend line by line in front of judges and buyers.

**Build**
1. **[D] Outcome labels.** Derived from renewal-deal history:

   | Renewal deal | Label |
   |---|---|
   | Won, amount ≥ previous | renewed |
   | Won, amount lower | downgraded |
   | Lost | churned |
   | Won after the renewal date | late |
   
   **[F]** A review screen lets a person correct labels; the label source is stored.
2. **[D] Snapshot reuse.** No new code path: the back-test calls the same `load_snapshot` and signal and scoring functions (rule R3). Extra **leakage tests**:
   - Canary future records must never change a past score.
   - A deal that later closed lost must show its *then* stage.
   - A contact marked "left" later must look active before that date.
3. **[D] Back-test runner.**
   - *Horizon mode:* score each past renewal at 30, 60 and 90 days before its date.
   - *Rolling mode:* every week over 12–24 months, rank all accounts.
   - Metrics: precision@10, recall at threshold, lead time (first flag before the outcome), each with bootstrap confidence intervals and **n stated**.
   - Baselines: (a) renewal date alone; (b) the company's existing health score as of then, if it has history; (c) best single signal.
4. **[D] Honest tuning.** Tune thresholds and points on an earlier time period; report on a later held-out period. This avoids "tuned on the test".
5. **[F] Back-test screen.**
   - Results table vs baselines, each number labelled.
   - Per-account **replay**: a time slider showing score, signals and "what Casefile would have said on this date". The case writer runs as of that date; results are cached.
6. **[A] `evals/run_evals.py`.**
   - One command runs every suite: signals, planted claims, email labels vs keywords, injection, matching sample, executor safety.
   - Results are written to `eval_run` and to a markdown table with labels.
   - CI runs the non-AI suites on every change; AI suites run on demand.

**Exit gate**
- A results table with n, confidence ranges and baselines, every number labelled.
- If there are too few churns, use case-by-case replays instead of a headline number (blueprint fallback).

---

### Phase 6 — Level 2 differentiators

Build in this order and stop wherever time runs out. Each feature ships whole or not at all.

| # | Feature | How it works | Done when |
|---|---|---|---|
| 6.1 | **Outcome tracker** | After execution, checks at +7, +14 and +30 days: champion replied? meeting booked? usage recovered? score change? Stored per play type. The case updates itself ("champion replied, meeting booked, risk 72 → 41"). Leaders see a plays-that-work table. | Outcomes recorded for every executed set (pending allowed) |
| 6.2 | **"What would lower this risk?"** | Pure code from the scoring config: points removed if each signal resolves, and which play addresses it | Shown on every case |
| 6.3 | **Relationship map** | Contacts with touch counts, last contact, role. Adds a `single_threaded` signal when only one person was active in the last 60 days. | Rendered; signal unit-tested |
| 6.4 | **Ask about an account** | Opus 5.5 via the SDK tool runner with read-only `@beta_tool` functions over our DB: `get_timeline`, `search_activities`, `get_usage_series`, `get_contacts`, `get_signals`. The answer schema cites evidence IDs and goes through the same checker. Demo: "Who else at this company have we spoken to?" | 20 scripted questions answered with verified citations |
| 6.5 | **Slack approval buttons** | "Approve all Tier 1" from the digest creates approval rows with channel `slack`. Tier 2 items only offer "Open in Casefile". | Approval from Slack executes and is audited |
| 6.6 | **Expansion cases** | Opportunity score opens growth-play cases | Expansion scenario produces a correct case |
| 6.7 | **Product-issue routing** | When evidence points at a bug, a Tier 1 item drafts a Linear issue with anonymised evidence | Issue appears in Linear after approval |
| 6.8 | **Casefile MCP server** (if time) | Read-only `list_cases`, `get_case`, `get_evidence` tools, so Claude or ChatGPT users can query Casefile under the same permissions | Works from an MCP client |

---

### Phase 7 — Demo hardening

**[F] with everyone**
- **Demo mode:** deterministic pseudonyms for people, companies and emails, applied at the API serializer and in Slack messages, so nothing real is on screen.
- `make demo-reset`: restore a known DB snapshot plus sandbox state in one command.
- The 7-minute run from blueprint section 16, rehearsed at least 3 times:
  - The "time machine" uses the real lead time from Phase 5.
  - The change set lands live in HubSpot, Gmail and Slack.
  - The attack email is sent live and nothing executes.
- A **recorded backup run**, labelled as recorded if used.
- Fresh Google tokens the morning of the demo.
- An offline cached snapshot in case of network failure.
- One architecture slide (section 4 diagram) and one results slide generated from `eval_run`.
- A judge Q&A drill using blueprint section 16's question list.

**Exit gate:** two clean full rehearsals in a row, on the demo machine, on venue-like Wi-Fi.

---

### Phase 8 — Design-partner pilots (production, after blueprint Gate A)

Starts once the demo has been judged, the back-test beats the baseline, and there have been zero unapproved writes.

- **Tenant isolation:** Postgres Row-Level Security on `org_id`, tenant-scoped DB sessions, and automated cross-tenant tests in CI.
- **Sign-in:** SSO via OIDC (Google Workspace, Microsoft Entra) through a provider such as WorkOS; roles: owner, admin, leader, viewer.
- **Secrets:** cloud secrets manager, envelope-encrypted OAuth tokens, rotation runbook.
- **Push-based sync:** HubSpot webhooks, Gmail `watch` via Pub/Sub, Calendar push channels, plus a nightly full reconcile.
- **Reliability:** staging and production environments, CI/CD, point-in-time DB recovery, incident runbook (revoke tokens → pause executor → notify → review audit). Re-evaluate Temporal for the executor if retry and wait logic grows.
- **Observability:** OpenTelemetry traces including every AI call, Sentry, and dashboards for flag rate, approval rate, checker rejection rate and "wrong" feedback, with drift alerts.
- **Data handling:** retention jobs (raw bodies purged after N days, snippets kept), deletion on request, DPDP processor documentation, and a zero-data-retention arrangement with the AI provider if needed.
- **Adoption path per pilot:** read-only connect → back-test report → 2–3 weeks shadow mode → Tier 1 approvals → outcome review.

**Gate B:** owners rate most cases useful; at least one documented save; a security review passed.

### Phase 9 — Paid launch

- **Connectors:** Salesforce (REST + field history; DX MCP server for Q&A), Outlook and Calendar via Microsoft Graph, and help desks (Zendesk, Freshdesk, HubSpot tickets), which enable the `support_strain` signal.
- **Marketplace listings:** HubSpot and Slack. Slack's marketplace requires HTTP endpoints, so `slackbot` moves from Socket Mode to HTTP.
- **Billing:** by accounts monitored, with a free tier. Self-serve onboarding that ends in a free back-test report.
- **Compliance:** the DPDP programme is ready before the 2027 duties apply; admin analytics.

**Gate C:** pilots convert to paid and renew; outcome data covers the main plays.

### Phase 10 — Scale and intelligence

- **Per-company learned model** (logistic regression or gradient-boosted trees, calibrated, with SHAP breakdowns). Trained only once enough labelled renewals exist, and promoted only if it beats the points model in the back-test (champion/challenger).
- **Play recommendations** from accumulated outcome data.
- **Casefile MCP server** generally available.
- **SOC 2** Type I, then Type II.
- **`pgvector`**, only if Q&A over long histories needs semantic search.

---

## 7. Sequencing and team

### Critical path

```
schema v1 → normalizer → identity → Snapshot(as_of) → signals → scoring
          → evidence pack → case writer + checker → planner → approvals → executor → audit
                                   └──────────── back-test (reuses Snapshot + signals) ┘
historical-data request (Phase 0) ─────────────────────────────────────────► back-test
```

### Who does what (team of four; merge tracks if fewer)

| Phase | [D] Data & SQL | [A] Agent & backend | [I] Integrations | [F] Frontend & demo |
|---|---|---|---|---|
| 0 | Schema, synthetic generator | Repo, CI, S5 | Sandbox stack, seeders, S1–S3, S7 | Vite scaffold, login shell, data request |
| 1 | Normalizer, resolver, Snapshot | Scheduler, job queue | HubSpot, Gmail, Calendar, usage readers | Accounts, timeline, connections pages |
| 2 | Signals, scoring, blind spots | Case lifecycle | Field-mapping reads | Queue, breakdowns |
| 3 | Facts tables, label eval sets | Gateway, reader, writer, checker, injection | Backfill via Batches | Case screen, evidence drawer |
| 4 | Audit chain | Policy, playbooks, planner, state machine | Executor, writers, reconcile, Slack | Approval inbox, audit screen |
| 5 | Outcomes, runner, metrics | `run_evals.py` | Historical export ingest | Back-test and replay screens |
| 6–7 | Outcome tracker | Q&A agent, what-lowers-risk | Slack buttons, Linear | Relationship map, demo mode, rehearsals |

### Indicative calendar (assumes about 4 weeks; adjust once dates are known)

| Week | Phases | Must be true at the end of the week |
|---|---|---|
| 0 | Phase 0 | Stack runs on synthetic + sandbox; spikes answered; data request out |
| 1 | Phase 1, Phase 2 starts | Timeline for every account; match accuracy measured |
| 2 | Phase 2, Phase 3 | Ranked queue; verified case explanations |
| 3 | Phase 4, Phase 5 starts | **Level 1 complete:** an approved change set lands in every tool |
| 4 | Phase 5, Phase 6 (6.1–6.4), Phase 7 | Results table; rehearsed demo |

### If the hackathon is only 48–72 hours

Keep R1–R8 and cut breadth:
- Synthetic + sandbox only; real data is not attempted.
- Readers: fixture, HubSpot, Gmail. Usage comes from the fixture.
- Five signals: meeting gap, missing next step, champion silence, usage decline, renewal proximity.
- Case writer + checker + rule-based injection detector.
- Executor for task, next step, note, Gmail draft and Slack DM.
- Hash-chained audit log.
- Back-test on synthetic history, labelled **simulated**.
- Phase 6 is skipped.

---

## 8. Cross-cutting

### Testing layers

| Layer | Tooling | Runs |
|---|---|---|
| Unit (signals, scoring, checker, hashing, money filter, resolver) | pytest, hypothesis | Every commit |
| Connector contracts (recorded payloads) | pytest + recorded HTTP fixtures | Every commit |
| Leakage and as-of property tests | hypothesis | Every commit |
| Executor safety suite (crash, double-approve, tamper, conflict) | pytest with fault-injecting fake writers | Every commit |
| Sandbox end to end (real HubSpot, Gmail, Slack test accounts) | `make e2e` | Daily and before demos |
| AI evals (labels, planted claims, injection, Q&A) | `evals/run_evals.py` | On prompt or model change |
| Load (1,000 accounts × 90 days) | Synthetic generator + timer | Before Phase 4 exit |
| User acceptance (2–3 owners, a week in shadow mode) | Feedback buttons + interview | Phase 8, or earlier if real data arrives |

### Cost model (estimated; replace with measured numbers from `llm_call` in Phase 3)

| Job | Model | Tokens (in / out, incl. thinking) | Cost each |
|---|---|---|---|
| Read one email | `claude-haiku-5-5` ($0.10 / $0.50 per M) | ~1,000 / ~200 | ≈ $0.0002 |
| Write one case | `claude-opus-5-5` ($4 / $20 per M) | ~4,000 / ~1,500 | ≈ $0.05 |
| Draft one email | `claude-opus-5-5` | ~2,500 / ~800 | ≈ $0.03 |

Example: 500 accounts, 50 changed cases a day, 2,000 emails a day → ≈ **$4 per day** (estimated). Levers, in order:
1. Re-run only on change.
2. Result cache.
3. Prompt caching.
4. Batches for backfill.
5. Measure Sonnet 5.5 for drafts against the eval before switching.

### Hackathon security checklist

- [ ] Written data permission on file before any real-data sync
- [ ] Read-only scopes first; write scopes only for agreed records or test records
- [ ] Read and write tokens in separate env files; executor container has no AI key; worker has no write tokens
- [ ] Shadow mode on by default for real data
- [ ] Demo mode on for any screen shown publicly
- [ ] No customer data in git, slides or screenshots (gitleaks + `.gitignore`)
- [ ] All copies deleted after the event, with written confirmation to the company

---

## 9. Decisions needed from you

| # | Question | Default if unanswered |
|---|---|---|
| 1 | Hackathon dates, demo length, judging criteria | Plan for about 4 weeks and a 7-minute demo |
| 2 | Team size and who takes each track | Four people, tracks as in section 7 |
| 3 | Whose real data, and who can sign the permission? | Synthetic + sandbox until signed |
| 4 | Which CRM (and edition), email/calendar provider, chat tool? | HubSpot + Google Workspace + Slack |
| 5 | Where does product usage live, and can we get a read-only aggregate view? | Fixture usage; real accounts without usage appear as blind spots |
| 6 | How many renewals, churns and downgrades exist over 12–24 months? | Case-by-case replays if fewer than ~10 churns |
| 7 | May demo writes touch real records, or only agreed test records? | Test records only |
| 8 | Do you accept the deviations in section 2 (no LangGraph, REST executor, sandbox-first)? | Yes |
| 9 | Hosting for the demo | One VM running docker-compose, Indian region if available |

---

## 10. Level 1 definition of done (mapped to blueprint acceptance tests)

- [ ] **End to end:** for a flagged account, all six steps (identify → explain → task → email draft → CRM update → notify owner) complete in one approval session and appear in the real tools and in the audit log.
- [ ] **Evidence:** 100% of shown claims cite at least one record; the checker removes every planted false claim.
- [ ] **Safety:** zero changes without an approval record; zero actions triggered by planted injection emails.
- [ ] **Exactly once:** the crash, double-approve, tamper and conflict tests all pass.
- [ ] **Freshness:** a new email or meeting shows up in the case within 15 minutes.
- [ ] **Honesty:** every number on the results slide carries observed / simulated / estimated / untested.
