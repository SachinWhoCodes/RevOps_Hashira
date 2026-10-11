# ADR 0006 — Level 2 differentiators (Phase 6)

Status: in progress (2026-10-11). Each feature adds its decisions here when it ships.

## 6.2 "What would lower this risk?" (shipped 2026-10-11; `casefile/cases/levers.py`, `levers-1`)

1. **Built before 6.1, out of plan order.** It is pure code, free, and shows on every case. The
   outcome tracker (6.1) cannot show a real result on the current setup (see 5), so building it first
   would have produced a screen of "pending".
2. **Levers are changes in the world, re-scored through the live code — not arithmetic on
   `scoring.yaml`.** Each lever ("a meeting is booked", "the champion replies", "a dated next step is
   recorded", "usage recovers to N", "the economic buyer joins a meeting") is applied to a copy of the
   as-of snapshot and the real `run_signals` + `score` run on it (rule R3). This catches interactions
   a points table misses: a meeting with the economic buyer ends a meeting gap *and* earns the
   executive-engagement credit (−25, not −15). A lever the rules would not move is never shown.
3. **Usage targets are verified, not estimated.** The arithmetic target is stepped up until the real
   `usage_decline` code stops firing; the shown number is the smallest the rule accepts (tested:
   30 users still fires, 31 clears, from 40).
4. **No perverse levers; honest expiries.** Some signals clear by waiting (a departed champion after
   120 days, negative emails after 60), and champion silence would clear if we *stopped* emailing.
   We never offer "do nothing" as a lever. Signals no action clears are listed with the date the rule
   stops counting them and the words "that drop is the rule expiring, not a sign of recovery".
   A champion we believe has left (bounce or "I have left" auto-reply) is never offered as "replies",
   even while the CRM still lists them as active (found in the time machine on Lattice, 15 Aug).
5. **Written is not the same as read back.** When the case's plan has already written a lever's change
   (today only the deal next step), the lever says "written to the CRM by this case's plan · not read
   back yet". In the sandbox the executor writes to the HubSpot portal but Casefile reads CRM data from
   the synthetic company, so an executed next step never clears the signal. Acme shows this today.
   This is the main constraint on 6.1.
6. **"Fewest changes to close the case"** tries every combination of levers (≤ 5, so ≤ 31 re-scores)
   and reports the smallest set that takes risk below the close threshold (30), or says plainly that
   none does. Observed cost: 0–8 ms per account on the dev data.
7. **Labelled "re-scored by the rules · not a forecast".** It says what the current rules would do if
   the change happened, not that it will happen or that the customer will renew.

Resolved in rules v4 (A3 below): a departed champion used to score twice — `champion_silence` (+20)
and `champion_departure` (+30) — until the CRM marked them "left".

## A1 HubSpot-logged emails and meetings (shipped 2026-10-11)

1. **A company can start with one read-only CRM token.** The HubSpot reader now reads emails and
   meetings logged in HubSpot (by its Gmail/Outlook/calendar integrations or by hand), with their
   contact associations. No Google review, no mailbox access. Property names and payload shapes were
   checked against a live portal before writing the parser (observed).
2. **Same activity rows as Gmail/Calendar,** so every signal works unchanged: tested end to end, the
   meeting gap, champion silence and a departure ("I have left" out-of-office) fire from HubSpot
   records alone. HubSpot's automatic company association is trusted at 0.95 (it is a domain rule,
   like ours); records without one go to our identity resolver.
3. **A no-show is not a held meeting.** `CANCELED` and `NO_SHOW` both count as "did not take place";
   counting a no-show as held would hide a meeting gap.
4. **One copy per email or meeting.** With Gmail and HubSpot logging both connected, the same email
   arrives twice. The snapshot loader keeps the mailbox copy: Message-IDs first (HubSpot exposes
   `hs_email_message_id`), else sender + subject + minute; meetings by start minute + a shared
   participant. Records from one source are never collapsed. Re-running every evaluation suite on
   the synthetic data gave identical results (observed).
5. **A missing scope degrades, it doesn't break.** A token without permission to read emails gets a
   403 for that object only: it is skipped, noted in the cursor and retried next run; a revoked
   token (401) still fails the sync loudly.

## A2 Sandbox read-back (shipped 2026-10-11; `connectors/hubspot/mirror.py`)

1. **The write loop is closed, observed live.** A read connection (`hubspot`, `mode=sandbox_mirror`)
   reads the mirrored deals back. On its first run (11 records: 7 deals, 2 tasks, 2 notes) the next
   steps the executor wrote for Acme and Lattice came back with their real write times; "missing next
   step" cleared and risk fell 85 → 75 and 75 → 65. First time any Casefile reader synced a real
   system.
2. **Scope is the mirrored deals only.** The sandbox portal is connected to a real Gmail inbox and
   holds real, auto-logged personal mail, so the reader never lists companies, contacts or emails;
   every request is derived from the deal ids in `external_ref` (a test fails on any other request).
3. **Only changes made after the copy count.** A copy's initial values are our own data echoed back
   (seen live: Wavelength's next step at creation time); they are ignored. Only HubSpot-recorded
   history is used — never a "current value" fallback, which would reset how long a next step has
   gone untouched.
4. **Read-back records say where they came from** (`via: sandbox_mirror`, a "from sandbox HubSpot"
   badge on the timeline).

## A3 Rules v4 and the relationship map (6.3) (shipped 2026-10-11; thresholds v4, scoring v4, levers-2)

1. **A departed champion is scored once.** `champion_silence` skips a champion with any departure sign
   (bounce, "I have left" auto-reply, CRM "left") in the last 120 days
   (`skip_if_departed_within_days`); the departure detection is one shared function. Without the key
   (older rule versions) the old behaviour is reproduced, which a test checks.
2. **`single_threaded` (+10):** exactly one person at the customer emailed us or met us in the last 60
   days (auto-replies, bounces and cancelled meetings don't count; nobody at all is left to the
   silence signals). Measured on the dev data: fires on 18 of 180 active accounts and pushes none over
   the case threshold on its own (observed) — low noise. Its lever names who could break the single
   thread (economic buyer first) or, if nobody else is on record, says a new contact is needed.
3. **Evals delta v3 → v4 (simulated): no measurable change.** Held-out AUC 0.82 → 0.81 (30 days),
   0.75 → 0.73 (60), 0.60 → 0.64 (90) — all inside the ranges; precision, recall and the weekly top 10
   are unchanged. The synthetic generator plants no single-threading stories, so this data cannot
   show whether the signal helps; a real back-test decides. Kept at 10 points as a starting value.
4. **Relationship map:** per person — heard from (they wrote or met us), emails from/to and meetings
   in 90 days, next meeting, active in 60 days. A pure function of the as-of snapshot, so the time
   machine shows the map as it was.
5. **A rules change no longer pays to rewrite unchanged explanations.** A new rule version creates a
   new assessment; when its signals and evidence (signature) are the same, the checked explanation is
   carried forward and re-checked in code (free). Found when v4 queued rewrites for all 8 open cases;
   4 were unchanged. Queued explanation jobs are also idempotent now (an already-verified assessment
   is skipped; the CLI can force), and assessments get an app-set `created_at` so "latest" cannot tie
   inside one transaction.
6. **Live effect on the dev data (observed):** Lattice 65 → 45 and Granite 65 → 45 (departure counted
   once), Atlas 60 → 70 and Trident 55 → 65 (single-threaded); no case opened or closed. Four changed
   explanations rewritten for $0.1256; the other four carried forward at no cost.

## A4 Email retention (shipped 2026-10-11; `ingest/retention.py`)

1. **The 90-day promise is now enforced.** Until now email bodies got a purge date but nothing ever
   deleted them. A worker job (every 6 hours) and `casefile purge-bodies` delete full text past its
   date in both places it lives: the activity row (body and any hidden HTML text) and the raw payload
   archive (Gmail MIME bodies, HubSpot `hs_email_text`/`hs_email_html`).
2. **Kept:** the ≤500-character snippet, subject, addresses, dates, AI labels with their short quotes,
   and the raw record's payload hash — so a re-fetched message is still recognised and not stored
   again (tested). The AI cache holds outputs only, never the email text.
3. **Every purge is audited** with counts only, never content.
4. Retention runs from ingestion (90 days after Casefile first stores the email), so a 2-year backfill
   can still be read once by the AI reader before its text goes.

## 6.1 Outcome tracker (shipped 2026-10-11; `outcomes/tracker.py`)

1. **Checkpoints at +7, +14, +30 days for every executed change set** (one with at least one line
   written), stored as `pending` from the start — the "done when" (outcomes recorded for every
   executed set, pending allowed) holds from the first run. A worker job runs hourly;
   `casefile outcomes` runs it now.
2. **Measured through the time machine.** Each checkpoint compares the account as of one second before
   the first write with the account as of the checkpoint date, with the live signals and scoring. A
   late run measures exactly what an on-time run would have (tested: a departure on day 8 is in the
   day-14 picture and not in day 7's).
3. **What is recorded:** risk before → after, signals cleared and new, and the events a play hopes for
   — champion replied, economic buyer engaged, meeting booked (booked after the play) or held, a dated
   next step, usage change. Risk plays: improved/worse at ±10 points (one signal's worth), else no
   change. Expansion plays: improved when a meeting is booked or the buyer engages.
4. **Labelled "measured after · not proof of cause".** The plays table shows n for every figure.
5. **Observed today:** both executed plays (Acme, Lattice) have pending checkpoints (first due
   18 Oct); Acme's live "so far" reading shows risk 85 → 75 with the next step cleared — the change the
   executor made, read back from HubSpot. The synthetic customers never reply, so on this setup most
   checkpoints will read "no change"; a person can act in the sandbox portal (log a meeting or change
   the next step on a mirrored deal) and the read-back will carry it into the next checkpoint.
6. `play_outcome` gained a unique (change set, check day) constraint so the job is idempotent.

## 6.4 Ask about an account (shipped 2026-10-11; `ai/qa.py`, prompt `account_qa.v1`)

1. **Opus 5.5 through the SDK tool runner** (`client.beta.messages.tool_runner`, `@beta_tool`) with five
   read-only tools over one account's as-of snapshot: `get_signals`, `get_timeline`,
   `search_activities`, `get_usage_series`, `get_contacts`. Effort `medium`, at most 8 turns,
   structured final answer (`output_format=QaAnswer`), server-side refusal fallback
   (`fallbacks="default"`). Nothing in the tools can write (rule R1).
2. **Same checker as case explanations (rule R2).** Every record a tool returns gets an evidence ID and
   every number it computes a fact key; the answer is sentences that cite them, and each sentence must
   pass `check_claim`. Failing sentences are removed and shown as a count; what the data can't answer
   goes in a checked "gaps" line.
3. **Customer text is data (rule R6):** masked, wrapped in `<evidence>` tags, and emails flagged as
   injection attempts are withheld — their text is never sent, and search does not match on it.
4. **Exit gate met (observed mechanism, simulated data): 20/20 scripted questions answered with
   verified citations;** 80 sentences shown, 21 removed by the checker; $0.624 for the run
   (≈ $0.031 a question, 1–4 tools a question). Answers are cached per question and account state
   (keyed by prompt and tools versions), so repeats are free.
5. **What the removals taught (13 of 21 were the checker being too strict for conversation):**
   sentence openers like "Yes"/"Nobody"/"Apart" were read as unknown names; dates embedded in a cited
   fact's key (`usage.week_of_2026-08-10…`) were not allowed; counts equal to the number of cited
   records ("two emails" citing two) were rejected. All three are now allowed — each is verifiable —
   with tests that the limits hold (an unknown name, a wrong week, a count that doesn't match the
   citations are still removed); the 25 planted false claims are still all removed. The other 8
   removals were real catches (an invented fact key, an uncited sentence, unsupported dates, and two
   sentences mentioning "discount", which stays human even in a factual answer).
6. Evidence chips for contacts and CRM fields now jump to the person in the relationship map or the
   renewal-deal panel (before, only emails/meetings and usage opened).

## 6.6 Expansion cases (2026-10-11; scoring v5)

1. **Found:** only 2 expansion cases from 19 planted growth stories. Expansion detection depends on
   the AI email reader, which had read 0.7% of past emails. Reading the last 120 days of inbound
   email (2,845 emails, Batch API, **$0.064 observed**) raised it to 11 cases and also opened 4 risk
   cases, all on planted at-risk accounts (negative tone).
2. **Scoring v5: an explicit, quote-verified request to buy (seat request, upgrade, pricing question)
   opens an expansion case on its own** (`upgrade_interest` 30 → 40). The Phase 2 rule "one
   opportunity signal does not open a case" was written against passive seat saturation (51 noisy
   cases); a customer asking to buy is a different kind of evidence. It fires on 20 of 180 accounts,
   none at risk. Passive usage signals still need corroboration.
3. **New eval suite `expansion_vs_planted_truth` (simulated).** v4 → v5: 11 → 20 open expansion cases,
   recall 47% → 89% (17/19), strict precision 82% → 85%, 0 unexplained (the other 3 sit on accounts
   whose last period was a growth story and whose planted churn starts in 2027 — they really are
   expanding today). Caveat: the gap was found by looking at synthetic data; the rule is product
   logic, not a tuned threshold, but it is validated only on synthetic data.
4. Planning, drafting and approving an expansion play runs through the same path as risk plays; the
   approval itself stays a person's step.

## 6.5 Slack approval buttons (2026-10-11; `actions/slack_approval.py`, `slackbot/`)

1. **"Approve N Tier 1 actions" in the morning digest; Tier 2 (customer email) only "Open in
   Casefile".** A case with a waiting plan gets the button; every case gets the link.
2. **Rule R1 kept with a third process.** The executor (which already had the Slack bot token) puts
   the buttons in the digest. A new `slackbot` process (app zone) holds only the Socket Mode app token,
   in its own `.env.slackbot` (chmod 600) — it can receive clicks but cannot post or touch any other
   tool; it answers through each click's `response_url` (no token). It records the decision with the
   web app's own `approve_item` (`channel=slack`, audited, as the Slack user linked to a Casefile user
   with `casefile link-slack`). The executor still performs every write. The zone test now also
   proves the slackbot never imports the executor.
3. **Rule R4: the button binds the exact lines.** Its value carries a fingerprint of the Tier 1 lines'
   approval hashes as they were when the digest was built; a re-plan, an edit or a line decided
   elsewhere makes the click refuse and point to Casefile (tested). Each Socket Mode event is
   acknowledged before anything is recorded, so a Slack retry can't double-approve.
4. **Observed live:** Socket Mode connection opened with the real app token; two digests sent to the
   workspace owner (Priya's: no waiting plan in her top 3; Vikram's: "Approve 4 Tier 1 actions" for
   Trident Robotics, whose renewal deal is mirrored in the sandbox portal). **The click, observed
   live (2026-10-11):** the user pressed the button; the slackbot recorded 4 approvals
   (`channel=slack`, as the linked admin), the executor wrote the HubSpot task, note and deal next
   step and the owner DM (one attempt each), the Gmail draft stayed proposed (Tier 2), the read-back
   brought the next step home and Trident's risk fell 65 → 55. Audit chain verified 877/877.
5. **Digest ranking fixed while doing this:** it ranked by risk priority only, so expansion cases
   (risk priority 0) could never appear. Now risk cases first (revenue in danger), then expansion
   cases by opportunity priority.

## 6.8 Casefile MCP server (shipped 2026-10-11; `mcp_server/`, official `mcp` SDK 2.3)

1. **Three read-only tools over stdio:** `list_cases`, `get_case` (checked explanation with evidence
   IDs, signals and facts, what would lower the risk, plan lines and status, outcomes), `get_evidence`
   (the record behind an evidence ID). All annotated read-only / non-destructive; the process holds
   no write credentials and nothing in it can approve, plan or write.
2. **Same permissions as the web app:** the server is bound to one active Casefile user
   (`--user` / `CASEFILE_MCP_USER`) and sees only that user's organisation; views reuse the queue's
   `assessment_view`, so MCP and the app say the same thing.
3. **The reader is an AI too (rule R6):** customer text leaves only as masked snippets in fields named
   `untrusted_customer_text`; emails flagged as injection attempts are withheld; the server's
   instructions tell the client to treat that text as data.
4. **Works from an MCP client (observed):** the official client launched
   `python -m casefile.mcp_server` over stdio against the dev data, listed the tools, listed the 10
   open risk cases and read Trident's case including the plan executed from Slack. In-process client
   tests cover the tools, masking/withholding and refusals. stdout is reserved for the protocol, so
   the process logs to stderr.
5. Remote access (streamable HTTP + OAuth) waits for Phase 8 single sign-on.
