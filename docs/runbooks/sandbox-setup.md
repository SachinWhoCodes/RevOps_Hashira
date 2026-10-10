# Sandbox tool accounts — setup guide

These are accounts **the team owns**, used for development and for the live demo when the real
company only grants read access. Everything in them is synthetic. Results measured here are
labelled **simulated**.

A person on the team must create these accounts; Claude cannot create accounts or handle passwords.
Put every token in the repo-root `.env` (never committed). Read tokens and write tokens are kept
separate on purpose (plan rule R1).

> Scope names below come from vendor documentation as we know it. Spike S2/S1/S3 confirms them
> against the live APIs; update this file with what you find.

## 1. HubSpot developer test account

1. Create a free HubSpot developer account, then a **developer test account** inside it.
2. Create **two** apps with static access tokens (Settings → Integrations → Private Apps; newer
   portals may label these "Legacy apps"):
   - **casefile-read** — read scopes only: companies, contacts, deals, owners, and the engagement
     objects (notes, tasks, calls, meetings, emails). Put its token in `READ_HUBSPOT_TOKEN`.
   - **casefile-write** — only what the executor needs (Phase 4): create tasks and notes, update
     deals. Put its token in `.env.executor` as `WRITE_HUBSPOT_TOKEN`. Never in `.env`.
3. Create these custom properties (Settings → Properties) so the synthetic data maps 1:1:

   | Object | Internal name | Type |
   |---|---|---|
   | Company | `seats_purchased` | Number |
   | Company | `renewal_date` | Date picker |
   | Contact | `employment_status` | Dropdown: `active`, `left` |

   `hs_buying_role` (contact) and `hs_next_step` (deal) are standard properties.

## 2. Google (Gmail + Calendar)

Preferred: a Google Workspace domain the team controls, with an **Internal** OAuth app (no Google
verification needed, tokens don't expire weekly).

Fallback: a test Gmail account with an **External** OAuth app in *Testing* mode. Testing mode is
capped at 100 test users and refresh tokens expire after 7 days, so refresh them the morning of any
demo.

1. Google Cloud console → new project → enable **Gmail API** and **Google Calendar API**.
2. OAuth consent screen → Internal (or External + Testing, adding the sandbox mailbox as a test user).
3. Credentials → OAuth client ID → *Desktop app*. Put the ID and secret in `GOOGLE_CLIENT_ID` and
   `GOOGLE_CLIENT_SECRET`.
4. Run `cd backend && uv run casefile google-auth --zone read` and sign in as the sandbox mailbox.
   This grants `gmail.readonly` + `calendar.readonly` and prints the refresh token for
   `READ_GOOGLE_REFRESH_TOKEN`.
5. Phase 4 only: run it again with `--zone write` (`gmail.compose`, for drafts) and store the result
   in `.env.executor`.

## 3. Slack (Phase 4)

1. Create a Slack workspace for the team and a Slack app in it, with **Socket Mode** on (no public
   URL needed).
2. Bot scopes: `chat:write`, `im:write`, `users:read`, `users:read.email`. App-level token scope:
   `connections:write`.
3. Install the app to the workspace. Bot token → `.env.executor` as `SLACK_BOT_TOKEN`; app token →
   `SLACK_APP_TOKEN`.

## 4. Seeding the sandbox with the synthetic company

Once the tokens above exist, the seeders (written after spikes S1/S2 confirm the APIs) push the
synthetic company into the sandbox:

- HubSpot companies, contacts, deals and notes.
- Backdated Gmail messages via `users.messages.insert` with `internalDateSource=dateHeader` (needs
  the `gmail.insert` scope, used only by the seeder).
- Past and future Calendar events.

HubSpot cannot backdate property history, so back-tests on sandbox data use the fixture loader's
history, not HubSpot's.
