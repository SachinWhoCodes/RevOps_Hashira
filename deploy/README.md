# Running Casefile for a pilot (one VM)

Rehearsed end to end on 2026-10-11 (local Docker, `CASEFILE_DOMAIN=localhost`): migrations, the
least-privilege database login, HTTPS through Caddy, security headers, readiness, a nightly backup
and a restore into a scratch database all worked. Not yet done on a real server or domain.

## What runs

| Service | Secrets it can read | Notes |
|---|---|---|
| `db` | — | Postgres 16, not published to the host |
| `migrate` | database owner password | one-shot: `alembic upgrade head`, then creates/updates the `casefile_runtime` login |
| `api`, `worker` | `env/app.env` | read tokens, Anthropic key, session secret; log in as `casefile_runtime` |
| `executor` | `env/app.env` + `env/executor.env` | the only holder of write tokens; profile `writes`, off by default |
| `slackbot` | `env/slackbot.env` | Slack app token only; profile `slack` |
| `web` | — | Caddy: HTTPS (Let's Encrypt for a real domain), serves the app, proxies `/api` |
| `backup` | database owner password | `pg_dump` daily into `deploy/backups/` (owner-only files), kept 14 days |

`casefile_runtime` is a member of `casefile_app`: it can read and write the tables but cannot UPDATE,
DELETE or TRUNCATE the audit log (verified in the rehearsal: "permission denied").

## First deploy

1. A VM with Docker (an Indian region if the customer's data must stay in India), DNS for your domain
   pointing at it, ports 80 and 443 open.
2. Clone the repo, then in `deploy/`:
   - `.env` (compose variables), all generated, never reused:
     `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` (URL-safe, 16+ characters — e.g. `python3 -c "import secrets; print(secrets.token_urlsafe(24))"`),
     `CASEFILE_DOMAIN=casefile.example.com`.
   - `env/app.env`: `SESSION_SECRET`, `APP_BASE_URL=https://<domain>`, `ANTHROPIC_API_KEY`,
     `AI_DAILY_BUDGET_USD`, read tokens (`READ_HUBSPOT_TOKEN`, Google client and read refresh token).
   - `env/executor.env` (only when writes are agreed): write tokens, `EXECUTOR_SHADOW_MODE=true` first.
   - `env/slackbot.env` (optional): `SLACK_APP_TOKEN`.
   - `chmod 600 .env env/*.env`.
3. `docker compose -f docker-compose.prod.yml up -d --build`
   (add `--profile slack` and, after the customer signs off on writes, `--profile writes`).
4. Create the organisation and admin:
   `docker compose -f docker-compose.prod.yml exec api casefile bootstrap --no-fixture --internal-domain <your-domain>`,
   then give the admin a password you choose with `... exec api casefile set-password` (bootstrap's
   generated password lands in a file inside the container, which you cannot easily read). Then
   connect the CRM read token and run a first sync. (Not yet rehearsed: step 4 on a real server.)
5. Check: `curl https://<domain>/api/health/ready` → `{"status":"ready"}`.

## Restore drill (do it once before real data arrives)

```
docker compose -f docker-compose.prod.yml exec backup sh -c 'export PGPASSWORD=$POSTGRES_PASSWORD;
  createdb -h db -U casefile restore_drill &&
  pg_restore -h db -U casefile -d restore_drill /backups/<latest>.dump &&
  psql -h db -U casefile -d restore_drill -c "select version_num from alembic_version" &&
  dropdb -h db -U casefile restore_drill'
```

Copy `deploy/backups/` off the VM (object storage with encryption) on a schedule; a backup that lives
only on the same disk is not a backup.

## Rotating a token

Edit the one env file that holds it, then restart only that service
(`docker compose ... up -d executor`). Revoke the old token at the provider. Write it in the audit
notes. Google refresh tokens in Testing mode expire after 7 days; an Internal Workspace app does not.

## Incident: something wrote what it should not have

1. Stop writes: `docker compose -f docker-compose.prod.yml stop executor` (approvals keep queueing).
2. Revoke the write tokens at HubSpot / Google / Slack.
3. Tell the customer contact named in the data-use agreement.
4. Review `Audit log` → filter `execution_*`; "Verify chain" must pass. Every write has an approval,
   the approver and the exact payload hash.
5. Fix, re-issue tokens, restart with `EXECUTOR_SHADOW_MODE=true` until the customer agrees.
