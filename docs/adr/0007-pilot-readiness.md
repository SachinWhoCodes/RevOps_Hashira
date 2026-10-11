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

## Still to do

Staff logins and invites; editable field mapping with a champion fallback for CRMs without buying
roles; database-enforced tenant isolation (RLS); onboarding flow; Phase 7 demo hardening.
