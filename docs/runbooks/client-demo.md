# Runbook — a client demo on your laptop, with the client's real data

The demo is day 2 of a short pilot, never the first time their data meets Casefile. Real CRMs and
mailboxes always differ from synthetic data; find that out privately on day 0, not in front of them.
Your synthetic demo company stays available throughout: sign out, sign in as `admin@lumora.test`.

## Before (a few days ahead)

1. **Written permission.** Use `docs/runbooks/data-access-request.md`; keep the client's reply. In the
   app, their admin also records the data permission on the Setup page (audited).
2. **Google test user.** Ask for the mailbox(es) they will connect and add each one in Google Cloud →
   Google Auth Platform → Audience → Test users. (Our Google app is in testing: only listed mailboxes
   can connect, Google shows an "unverified app" screen with Continue, and a connection lasts 7 days.)
   A Google Workspace client can instead use its own Google app (guide on the Connections page).
3. **Server keys present** (names only; check with `grep -c '^NAME=' .env`):
   `.env`: `SECRETS_KEY`, `EXECUTOR_SEAL_PUBLIC_KEY`, `BREVO_API_KEY`, `MAIL_FROM_EMAIL` (an *active,
   verified* Brevo sender), `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`;
   `.env.executor`: `EXECUTOR_SEAL_PRIVATE_KEY`.
4. **Decide what they connect.** HubSpot read app + one mailbox is enough for a strong demo. Leave
   Slack and write access out of a first demo: Slack messages reach real people, and writes change
   their CRM. Both can be added later in minutes.

## The terminals (each from the repo root)

| # | What | Command | When |
|---|------|---------|------|
| 1 | API | `make api` | always |
| 2 | Web | `make web` | always |
| 3 | Worker: syncs, scoring, AI reading/explanations | `make worker` | day 0 onwards |
| 4 | Executor, shadow only: records "would execute", writes nothing | `make executor SHADOW=1` | demo day |
| 5 | Slack listener | `make slackbot` | only if Slack is connected |

Stop 3–5 (Ctrl-C) when you are done. Run the executor *without* `SHADOW=1` only if the client said
yes to a specific live write during the demo.

## Day 0 — private session with the client (about 30 minutes)

1. The client registers at http://localhost:5173/register with their work email and confirms the
   email (Brevo). Or: `cd backend && uv run casefile create-org --slug <slug> --name "<Company>"
   --admin-email <their email> > ../data/invite.txt` and send them the link from that file.
2. Setup page: company details (their staff email domains), data permission.
3. Connections page: HubSpot read-only token (guide in the app), then **Connect a mailbox**.
4. With the worker running, watch the Setup page: first sync, then "Check what was found". If renewal
   dates, seats or roles are missing, open Settings → field mapping → **Check** (compares with their
   live CRM) and map their own property names, then re-apply. The organisation stays in setup mode:
   nothing is spent on AI yet.
5. Run the back-test (Setup page). With little renewal history it says so plainly; that is fine.

## Day 1 — you, alone

1. The Setup page's "Shadow mode" card shows the **estimated** AI cost of starting (emails to read,
   cases to explain). Approve it yourself, then give the organisation a cap:
   `cd backend && uv run casefile set-ai-cap --org <slug> --usd 5`.
2. Start shadow mode. The worker reads email, writes explanations and proposes plans.
3. Read every open case. Anything that looks wrong is better fixed (mapping, unmatched records) or
   explained before the demo.

## Demo day (7–10 minutes)

Overview → Queue (their top risk) → the account page: brief, evidence (click a citation), the time
machine (what Casefile knew a month ago), "what would lower this risk" → proposed actions: approve a
line and show it recorded as *would execute* (shadow) → Back-test → Audit log (verify the chain).
Keep the synthetic demo company as a fallback if their data is thin.

## After

- Stop the worker and executor.
- If the client wants their data removed:
  `cd backend && uv run casefile delete-org --slug <slug> --confirm <slug> --reason "requested after demo"`
  (needs the database owner, which is the default locally). It deletes every row, upload and audit
  entry of that organisation, revokes the Google read grant, and prints a receipt with counts and the
  verified final hash of their audit chain (no names or content). It also lists what the client must
  remove on their side: the HubSpot private apps, the Slack app, Google drafts access.
- `uv run casefile list-orgs` shows what is on the machine.
