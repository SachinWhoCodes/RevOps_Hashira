# ADR 0004 — Acting safely: change sets, approvals, executor (Phase 4)

Status: accepted (2026-10-11). Playbooks `v1`; prompt `email_drafter.v1`.

1. **Plans are rule-based; AI words one field.** `actions/playbooks.yaml` maps signal combinations to
   a play and an ordered list of actions. Code fills every field from the case's *verified* claims
   and current records. Claude Opus 5.5 only writes the customer email's subject and body.
2. **The approval binds the exact change.** The approved hash covers action type, target IDs, the
   prior value the person saw (`before`) and the final payload. The executor recomputes it and
   refuses on any difference (tests tamper with each of payload, target and before).
3. **Money talk stays human.** Any price, discount, refund, credit, contract, invoice, "% off" or
   free-month wording in an AI draft blocks the line (no retry: it is a signal for a person). Other
   draft problems (unknown names, numbers other than the renewal date, internal jargon, promises)
   get one retry with the reasons, then a plain template.
4. **Tiers.** Tier 1 (CRM task, note, next step, Slack DM to the owner) can be approved as a batch;
   Tier 2 (customer email draft) never can. Deal amount and stage exist as action types but are
   disabled by default. Only the case owner or an admin can decide.
5. **Separate executor process.** Only it loads `.env.executor`. The API and worker cannot import
   it (a test checks the module graph). It claims `queued` lines with `SKIP LOCKED` and writes the
   audit "started" record and commits *before* calling the tool; an audit failure means no write.
6. **Exactly once, observed live.** Tasks and notes carry a `cf<item-id>` token that HubSpot's
   `CONTAINS_TOKEN` search finds (verified against the real portal); drafts carry an
   `X-Casefile-Ref` header (verified live: Gmail *replaces* our Message-ID on drafts, and
   `messages.get` is outside `gmail.compose`, so the lookup uses `drafts.get`); Slack messages carry
   metadata. Writes are never retried blindly:
   outcomes are classified as done / rejected (nothing written) / unknown (search the marker first).
   A line whose outcome cannot be verified is left for a person.
7. **Conflicts go back to people.** For field updates the executor compares the live value with the
   approved `before`; a difference becomes `conflict`. "Re-read" refreshes `before`, which changes
   the hash, so a new approval is required.
8. **Least privilege everywhere.** The HubSpot write app holds create/update scopes only; checks and
   searches use the read token. The `casefile_app` database role can INSERT/SELECT the audit log but
   not UPDATE, DELETE or TRUNCATE it (tested; TRUNCATE is not caught by row triggers).
9. **Sandbox honesty.** Analysis runs on the synthetic company (simulated). For the write path its
   open-case renewal deals are mirrored into a throwaway HubSpot portal; every mirrored deal says so
   in its description, and every Slack DM routed to the workspace owner says whom it was meant for.
10. **Interruptions are capped, not the queue.** The morning digest sends each owner their top 3
    open cases at 09:00 local time, at most once a day (checked against the audit log).
11. **Draft name check: strict mid-sentence, lenient at sentence starts.** The first strict version
    rejected two good Claude drafts over "Introducing" and "Account". Now any other known person
    (contacts, our team) is always flagged, unknown capitalised words mid-sentence are flagged, and
    ordinary English at the start of a sentence or in a title-case subject is allowed. Both real
    drafts are regression tests.
