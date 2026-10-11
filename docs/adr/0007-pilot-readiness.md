# ADR 0007 — Pilot readiness (Part C, in progress)

Status: in progress (2026-10-11).

## Hosting (done; rehearsed locally)

1. `deploy/docker-compose.prod.yml` runs every process; each reads only its own env file (api/worker:
   app.env; executor: + executor.env, profile `writes`, off by default; slackbot: slackbot.env).
2. App processes log in as `casefile_runtime` (member of `casefile_app`), created by the one-shot
   `migrate` job (`casefile db-roles`); only `migrate` uses the database owner.
3. Caddy serves the app over HTTPS (Let's Encrypt for a real domain), proxies `/api`, sets HSTS, CSP,
   frame and content-type headers. New `/api/health/ready` checks the database.
4. Daily `pg_dump` backups, owner-only files, 14-day retention.
5. Observed in a local rehearsal: migrations to head, runtime role created, readiness over HTTPS,
   SPA + deep links, 401 without login, HTTP→HTTPS redirect, headers present, runtime role denied
   UPDATE on the audit log, a backup restored into a scratch database (33 tables, current head).
   Not yet done: a real VM and domain; copying backups off the server.

## C1 Staff logins and invites (done 2026-10-11; `api/routers/team.py`, Team page)

1. **Admins manage who can log in**: add a person, send a one-time invite link, change a role,
   switch a login off (their session stops on the next request). Admins cannot demote or lock out
   themselves. CRM-synced owners appear with "no login" until invited.
2. **No email service, by design for now:** the invite link is shown once to the admin, who sends
   it. Only a SHA-256 of the token is stored; links work once, expire after 72 hours, and a new
   invite revokes older ones. "Reset password" is a new invite. Every step is audited, never the token.
3. Passwords: Argon2 (existing), 12+ characters, not the email. Tested: one-time use, expiry,
   replacement, admin-only, cross-organisation refusal, deactivation ending a live session.
   Observed in the UI: invite → link → welcome page (the shown link was then revoked).

## C2 Field mapping screen and champion fallback (done 2026-10-11; thresholds v5)

1. **Per-company mapping, versioned:** YAML defaults overlaid with the company's latest saved
   `config_version`; saves are validated strictly (HubSpot-style property names; value maps may only
   point at known meanings; a renewal deal type is required; won/lost stages disjoint), never edited
   in place, audited with the changed keys; an unchanged save creates nothing.
2. **"Check" compares with the real CRM** when one is connected: property names per object and stage
   ids across pipelines (schema only, no records — verified live against the sandbox portal; the
   sandbox read-back connection is never used for this).
3. **Re-apply rebuilds stored data** from the raw records already fetched (no re-fetch, no AI):
   derived history is updated in place, identities re-matched, every account rescored. Tested end
   to end: a custom role value ("SPONSOR") is unknown, then a champion after save + re-apply.
4. **Champion fallback (thresholds v5):** when a CRM never records a champion, the active customer
   contact who wrote or met most in 90 days (3+ interactions; the economic buyer keeps their role)
   becomes the likely champion — labelled "inferred" in every summary and fact, computed from the
   as-of snapshot so the back-test sees the same thing. Effect on the synthetic company: none (all
   200 accounts record a champion); evals unchanged apart from two new tests (observed).

## C3 Database-enforced separation between companies (done 2026-10-11; migration `b7c1d2e3f4a5`)

1. **Postgres row-level security on all 33 tenant tables** (31 with `org_id`, plus `org` by id and
   `backtest_result` through its run). A non-owner role sees a row only when its organisation equals
   the transaction's `app.org_id`, or `app.bypass_rls` is on; with neither it sees nothing (fail
   closed). Writes into another organisation are refused (`WITH CHECK`).
2. **Set per transaction from the session** (`db/tenant.py`, `after_begin` hook, `set_config(..., true)`):
   survives commits within a request, cannot leak through the connection pool.
3. **Who is scoped:** the API (organisation in the signed session cookie; login and invite lookups use
   one explicit bypass each), the slackbot and the MCP server. The worker, executor and CLI run in
   declared system mode — they serve every organisation by design.
4. **Proof:** tests switch to a non-owner role in `casefile_app` (like production's runtime login):
   every tenant table is empty without an organisation; each organisation sees only itself; inserts
   and moves into another organisation fail; and login, queue, accounts, an account page, inbox, team,
   plays, config, blind spots and the audit-chain check all work through the API under RLS.
   Observed with the real `casefile_runtime` login: CLI recompute and the outcome tracker work in
   system mode; a tenant-scoped session with no organisation sees 0 accounts.

## C4 Onboarding flow (done 2026-10-11; migration `c4a8e2f1d903`, `casefile/onboarding.py`, Setup page)

1. **Three modes per organisation:** `setup` (data connected and scored; no AI, no writes), `shadow`
   (full loop and approvals; the executor records `would_execute`), `live`. A missing mode means setup
   (fail safe); existing organisations were marked live by the migration. Allowed moves: setup→shadow,
   shadow→live, live→shadow (the admin's off switch, effective from the next line), shadow→setup.
   Every move is audited with its reason.
2. **The checklist is derived, not stored:** company details, data permission, CRM, usage (optional),
   data check (counts and warnings), back-test, shadow, live — recomputed from the data on every view.
   Only decisions are stored: the permission record (versioned wording + SHA-256, audited), mode
   history, a skipped usage step.
3. **Gates:** shadow needs the permission, a successful first sync and a back-test run; it shows an
   *estimated* AI cost first (pending emails × $0.00005 + open cases × ($0.028 + $0.016), measured unit
   costs). Live needs shadow mode and an executor heartbeat with a write tool (both required), plus 14
   days of shadow and 5 decided lines (recommended; overriding them needs a 20+ character reason,
   recorded in the audit log).
4. **Setup spends nothing:** the worker marks AI jobs (`read_emails`, `write_explanation`, `plan_case`)
   of a setup-mode organisation as skipped; starting shadow re-queues email reading and rescores every
   account, which re-queues explanations for open cases.
5. **HubSpot connect in the app, read-only:** the admin pastes a private-app token. Casefile reads the
   portal id (`/account-info/v3/details`), refuses a portal already used by another organisation or
   registered as a sandbox write target (one explicit RLS bypass for that check), then asks for one
   record id per object type (HubSpot has no scope-introspection endpoint for static app tokens; the
   documented `access-token-info` endpoint returned 404, observed) and refuses if companies, contacts,
   deals, owners, notes, tasks or calls are unreadable; emails and meetings are optional. The token is
   stored Fernet-encrypted (`SECRETS_KEY`, read zone), never in config, audit or responses.
   **Write credentials are never pasted into the app** — they stay in the executor's env file (R1).
6. **No implicit credentials:** connections used to fall back to the server-wide `READ_HUBSPOT_TOKEN`
   etc.; with several companies that would read one company's CRM into another. The fallback is gone;
   the migration wrote the reference down on existing connections.
7. **Executor scoped to one organisation** (superseded by ADR 0008: one executor now serves every organisation, still with writers and lines strictly per organisation): it used to pick up every organisation's approved lines and
   write them with its one set of credentials. Now it only queries, processes and reconciles its own
   organisation's lines, reads the organisation's mode for every line, and (in shadow without any write
   access yet) still records `would_execute`, marked `checked_live: false`. Planner and conflict
   refresh read "before" values with the organisation's own CRM credential. The digest waits for live.
8. **One email, one organisation:** logins are found by email, so adding, inviting or creating an
   organisation with an email that exists in another organisation is refused, and login refuses an
   ambiguous email instead of guessing.
9. **Usage CSV upload:** validated (columns, dates, whole numbers, 50 MB), matched against known
   companies, written atomically to `UPLOAD_DIR/<org id>/usage.csv` (a volume shared by api and worker
   in production), synced by the worker.
10. **Operator path:** `casefile create-org` (setup mode + an admin invite link shown once) and
    `casefile init-secrets-key`. Gmail/Calendar stay operator-assisted: Google requires a security
    assessment for restricted mailbox scopes before self-serve OAuth is possible.

Found and fixed on the way: the back-test crashed for a company with no history (date overflow) and
the back-test page assumed a full report; both now show "nothing to test yet". The back-test run from
the UI was always labelled `simulated`; it is now `observed` unless the data comes from the synthetic
fixture.

Observed live (dev): an invited admin of a new organisation walked the checklist in the browser; a
fake token was rejected by HubSpot; the real read token for the sandbox portal was refused for the
second organisation before any record was read; a usage CSV upload matched 1 of 2 keys; shadow mode
started and was undone; both audit chains verify; queued AI jobs of the setup-mode organisation are
held. Not yet: a real customer's HubSpot token (none available), a production server.

## Still to do

Phase 7 demo hardening; a real server and domain; backups copied off the server; org switching for
people who work with several companies; audit entries for refused connection attempts.
