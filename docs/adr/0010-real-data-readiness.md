# ADR 0010 — Ready for a client's real data (on the operator's laptop)

Date: 2026-10-11. Status: accepted. Context: the user will demo Casefile to real clients, on their own
laptop, with each client's real CRM and mailbox. Self-service exists (ADR 0008); this closes the gaps
between "built" and "safe to point at someone's real data".

## Decisions

1. **The demo is day 2 of a pilot** (runbook `docs/runbooks/client-demo.md`): day 0 private connect +
   data check + back-test in setup mode (no AI), day 1 shadow mode after the operator approves the
   estimated AI spend, demo day in shadow (the executor runs with `--shadow`). Live writes only on an
   explicit yes from the client. The synthetic demo company (`admin@lumora.test`) stays as a fallback.
2. **Google: our shared app, Testing mode, client mailboxes added as test users** (decided by the
   assistant at the user's request). Works today with one click; costs an "unverified app" screen and a
   7-day connection. Not "in production unverified" (keeps the warning, may block Gmail scopes, opens it
   to anyone). Google's review starts once there is a domain and a privacy policy. Companies on Google
   Workspace can use their own Google app instead.
3. **Expired Google access is explained, not shown raw.** Connections carry `problem` (plain language)
   and `needs_reconnect`; the Connections page shows a Reconnect button. The executor now checks the
   drafts grant on its heartbeat (the access token is cached for an hour, so about one Google call an
   hour) and reports it, because the app cannot open sealed credentials to check them itself.
4. **Deleting a client's data on request** (`casefile delete-org --slug X --confirm X`): every row,
   upload and the organisation's own audit chain go; the Google read grant is revoked at Google; a
   receipt (`org_deletion`) keeps counts and the verified final hash of the deleted chain, with no names
   or content; the command prints what the client must remove on their side (HubSpot apps, Slack app,
   Google drafts access). Needs the database owner. The demo organisation needs `--allow-demo`.
   *Changed from the earlier proposal* ("keep audit entries, strip names"): editing entries would break
   their hashes and need the append-only guard lifted for updates; removing the one organisation's whole
   chain, after verifying it and recording its head hash, honours the request fully and leaves every
   other organisation's chain untouched and verifiable.
5. **Append-only guard per row** (migration `f7d1b5c8e326`): UPDATE/DELETE of any existing audit entry is
   still refused, TRUNCATE still refused per statement; a cascade that deletes zero audit rows no longer
   trips it. Deleting a chain uses `session_replication_role = replica` for that single statement.
6. **Field-mapping suggestions from the client's own CRM**: the Check reads property labels/types and
   each pipeline's closed stages with their probability (schema only, no records) and suggests matches
   for missing fields (e.g. HubSpot's built-in "Next Renewal Date", "Buying Role") and won/lost stages
   (probability 1 / 0). One click fills the form; nothing is saved until the admin saves a version.
7. **Tests can never reach Brevo or Anthropic**: `tests/conftest.py` blanks `BREVO_API_KEY`,
   `MAIL_FROM_EMAIL` and `ANTHROPIC_API_KEY` for the whole run (environment variables override the
   developer's `.env`). Found when the user added a Brevo key: one sign-up test would have sent a real
   email.

8. **Gmail and Calendar readers reworked after the first live run** (rehearsal A, the user's own
   mailbox, 30-day window, a rehearsal organisation with no CRM):
   - *Before:* the Gmail reader listed every message in the window and read each one's headers, one
     at a time: 116 messages took 52 s (~0.44 s each; observed) — about 2.5 hours for a 20,000-message
     mailbox — and it read the sender and subject of every private email to decide relevance.
   - *Now:* it never lists the mailbox. It searches Gmail for *threads* to or from customer domains and
     known contacts (ten addresses per search), fetches matching threads whole (a reply from a personal
     address stays with its conversation), eight in parallel; spam, trash and drafts are skipped. With
     no customers it reads nothing at all. New mail still arrives through `history.list`, headers first.
     Observed: 1 search + 4 thread fetches, 6 messages, 5.7 s.
   - *Customers added after the mailbox* (likely on day 0: Google connected before HubSpot) are caught
     up: the cursor keeps short hashes of the addresses already searched for, and new ones are searched
     on the next run. Before, their past email was never read.
   - Automated senders (no-reply@, postmaster@ …) no longer make a message relevant — they never
     count as engagement, yet were being stored (observed: Google security alerts). Bounce notices are
     read only once there are customer contacts.
   - *Calendar* keeps only events with a customer attendee (it kept every event with any outsider:
     private appointments, vendors, candidates) and lists its window again when new customers appear.

9. **"Us" includes the organisation's own addresses**, not only its email domains: its users' emails and
   every connected mailbox. A team on personal Gmail has no company domain (gmail.com can never be
   one), and its own sent mail was being filed as a customer writing in. Applied to Gmail, Calendar,
   CRM-logged activity and identity matching.
10. **A company is a customer once it has a deal.** Real CRMs hold prospects, vendors and companies the
    CRM auto-creates from whoever emailed (observed: the user's new HubSpot portal created 20 such
    companies — Naukri, Quora, IRCTC… — from the inbox HubSpot was connected to). Companies with no
    deal are not scored and their mail and meetings are never searched for. Every synthetic account
    has a deal, so the demo company and the back-test are unchanged (checked).
11. **Google per-user quota errors are retried.** Gmail reports "Quota exceeded … per minute per user"
    as HTTP 403; the shared HTTP layer now backs off and retries those (a real missing-scope 403 still
    fails at once), and the reader fetches four threads at a time instead of eight (eight exceeded
    the quota on the first real run).

12. **Approved CRM tasks and notes attach to the deal, not the company.** HubSpot refuses to link any
    record to a company unless the key may *change* companies (`USER_DOES_NOT_HAVE_PERMISSION`, observed
    on a real portal even with companies read access). Casefile's write key keeps
    `crm.objects.deals.write` only; tasks and notes go on the customer's renewal deal (HubSpot shows the
    deal on the company), and the approval screen still links to the company record. An account without
    a deal falls back to the company and needs `crm.objects.companies.write` — documented in the guide,
    not requested by default. Before this, every task and note would have failed for a real client
    whose write key followed our guide. The guides now describe HubSpot's Service Keys.
13. **Seeding a rehearsal CRM uses a separate, temporary key** (`SEED_HUBSPOT_TOKEN` in `.env.seed`,
    companies/contacts/deals write), checked against the expected portal before any write, with a ledger
    of every created record (`data/seed/onewho-hubspot-ledger.json`). Casefile's own keys stay narrow.

14. **AI reads customers' email only** (accounts with a deal): mail from prospects, vendors and
    newsletters the CRM linked to companies is never sent to the model. On ONEWHO this cut the shadow-mode
    estimate from 226 emails to 36. Setup warnings are computed over customers, not every CRM company.

## Evidence (all observed on 2026-10-11 unless marked)

- Demo login `admin@lumora.test`: live mode, 200 accounts, queues, back-test, audit chain intact; every
  page's endpoint answers for the demo company, a 30-account company in setup, and an empty new company.
- HubSpot ignores properties a portal does not have (200, field absent) — read on one synthetic deal in
  the sandbox portal; suggestions produced against that portal's real schema.
- Brevo: key valid once the user authorised this machine's IP; two sandbox sends accepted (Brevo drops
  sandbox messages: nothing delivered); `MAIL_FROM_EMAIL` now an active verified sender. A real delivery
  has not been observed yet.
- Tested: deletion (all rows gone, other chain unchanged, guard still on, truncate refused, demo guard),
  reconnect detection and the executor's Google check, mapping suggestions.
- Rehearsal A: a real person completed Google's consent through the app (web flow, PKCE, our Google
  app accepted the `localhost:5173` callback); Gmail and Calendar readers ran live (profile, search,
  thread fetch, history, calendar listing and sync token) — see decision 8.
- Brevo: a real confirmation email was delivered to the user's inbox through Brevo (sign-up "ONEWHO").
- First live run of the full HubSpot reader on a real portal (the user's new free portal): 279 records
  (20 companies, 28 contacts, 224 logged emails, meetings, calls, tasks) in 12.8 s, all but 3
  internal-only items matched to a company; no deals yet, so no customers to watch.
- Not yet: an email from a *human* customer matched to its company live (needs a real CRM: day 0 of
  the first pilot); the full HubSpot reader against a real client portal.
