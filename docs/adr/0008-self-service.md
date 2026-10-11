# ADR 0008 — Self-service sign-up and connections

Date: 2026-10-11. Status: accepted (decisions confirmed by the user the same day).

## Context

The user wants a company to arrive, register, connect HubSpot, Gmail/Calendar and Slack with
credentials it obtains itself (with guides in the app), and use every feature without the operator.
Until now an operator created organisations (`casefile create-org`) and wired write tools in env files.

## Decisions (user's choices in brackets)

1. **Open registration with email confirmation [open + Brevo].** `/register` stores a *pending*
   sign-up (`signup` table: email, company, Argon2 hash, SHA-256 of a 24-hour token) and emails a link.
   The organisation and admin are created only when the link is confirmed by a button press (POST), so
   mail scanners that open links cannot create accounts and an unconfirmed sign-up cannot squat an email.
   A newer registration supersedes older links. No answer reveals whether an email has an account (an
   existing account gets an "already registered" email; "forgot password" always says sent). Limits: 3
   sign-ups per email and 20 per network per hour. Personal mailbox domains (gmail.com, …) are not used
   as the company's internal domain.
2. **Email through Brevo's transactional API** (`casefile/mailer.py`): platform mail to our own users
   only (confirmation, reset, invites); customer email remains a Gmail *draft* written by the executor.
   Without a key, a localhost deployment writes `.eml` files to `data/outbox`; a public deployment
   refuses to pretend it sent anything. Admins can now email invites; "forgot password" reuses the
   one-time invite link.
3. **Write credentials pasted in the app, sealed for the executor [yes].** Rule R1 now reads: *the app
   may receive a write credential once, while checking it, and stores only a copy sealed with the
   executor's public key; only the executor holds the private key.* Sealing is an ECIES-style sealed
   box (X25519 + HKDF-SHA256 + ChaCha20-Poly1305, `casefile/sealing.py`); opening lives in
   `casefile/executor/unseal.py`, which the API, worker and slackbot never import (tested). Read
   credentials stay Fernet-encrypted with `SECRETS_KEY`.
4. **One executor for every organisation** (supersedes ADR 0007 C4 item 7's "one executor per
   organisation"). For each organisation it builds writers from that organisation's own credentials
   only (sealed, or an operator's env reference such as the sandbox's `WRITE_HUBSPOT_TOKEN`), runs
   `Executor(org_id=…)` so lines and credentials never cross, rebuilds writers when the organisation's
   write connections change, and reports per-connection readiness and errors in its heartbeat (the app
   cannot open sealed credentials, so the executor is the only one that can say "this works").
5. **Checks are real calls that change nothing**: HubSpot portal id plus *empty* batches (zero records)
   that HubSpot authorises (200/201) or refuses for a missing scope (403) — observed on the sandbox
   portal; a "read" token that can write is refused. Slack `auth.test` (scopes from `x-oauth-scopes`),
   `apps.connections.open` for the app-level token, `users.lookupByEmail` to match the team. Google's
   granted-scope list (a user can untick boxes) and the mailbox's profile. The same portal, Slack
   workspace or mailbox can never be connected to two organisations.
6. **Google [whatever is easy, fast and reliable].** Default: one-click "Connect a mailbox" on our Google
   app (web flow, PKCE, `include_granted_scopes=false` so the read grant and the drafts grant stay
   separate; encrypted state naming the user, and the callback also requires that user's session, which
   blocks cross-account linking). Its reliability depends on our Google app's publishing status: in
   *Testing* tokens expire after 7 days and only listed test users can sign in; *In production* but
   unverified shows a warning screen and caps the app at 100 users; full verification of restricted
   Gmail scopes needs Google's review and a security assessment (Google's docs, read 2026-10-11).
   Alternative in the same screen: the company's own Google app ("Internal", Workspace only) — no review,
   no user cap, no expiry; its secret is stored Fernet (worker) and sealed (executor).
7. **Slack from a manifest**: the app offers a manifest (bot scopes `chat:write, im:write, users:read,
   users:read.email`, Socket Mode, interactivity). The bot token (posts) is sealed for the executor;
   the app-level token (receives clicks) is encrypted for the slackbot, which now keeps one Socket Mode
   connection per connected workspace and handles a click only for the organisation it came from.
8. **AI cost [our key, per-company cap].** Each organisation has its own daily cap (default
   `AI_DAILY_BUDGET_USD`, an operator can set another with `casefile set-ai-cap`), plus a platform-wide
   daily backstop (`AI_PLATFORM_DAILY_BUDGET_USD`, default $25) because strangers now spend our key.
   New organisations start in setup mode, which spends nothing.

## Found and fixed

- `system_scope(session, False)` switched a system-mode session (worker, CLI) to tenant mode; under
  row-level security that would have left it seeing nothing after any helper widened scope for one
  query. It now restores the previous state.
- Row-level-security tests created a role inside each test transaction; two open transactions adding
  members to `casefile_app` wait on each other (observed as a hang). One committed, login-less probe
  role is created per test session instead.

## Evidence

Observed in dev: a stranger registered in the browser, the confirmation email landed in the outbox, the
confirm button created the organisation in setup mode and signed the admin in; the Connections page and
its guides rendered; Slack rejected fake tokens with its own reason; Google accepted the callback address
and showed its sign-in page for our app; one executor pass served three organisations (the sandbox kept
its four writers, the new ones only the internal writer, no credential errors). HubSpot's empty-batch
scope probe and its 403 scope hints were read live from the sandbox portal (nothing written).
Tested: 341 backend tests, including sign-up and connections as the runtime database role.
Not yet: a real email through Brevo (needs the user's key and a verified sender), a completed Google
consent (needs a person to sign in), a real customer's HubSpot or Slack.
