# ADR 0003 — AI layer (Phase 3)

Status: accepted (2026-10-11). Prompts: `email_reader.v1`, `case_writer.v1`. Injection rules: `inj-rules-3`.

1. **Two models behind one gateway.** `claude-haiku-5-5` (effort `low`) reads each customer email;
   `claude-opus-5-5` (effort `medium`) writes case explanations. Every call goes through
   `ai/gateway.py`: kill switch, masking, cache, daily budget, structured output
   (`messages.parse` + Pydantic), refusal handling, cost log. Opus calls carry the server-side
   refusal fallback (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`); Haiku 5.5 has no
   server-side fallback, so a refusal leaves the email unlabelled.
2. **The AI words; code decides.** Numbers come from signal facts; the AI may only quote them.
   A claim is shown only if it cites existing evidence and every number, date and name in it
   matches what it cites. Numbers are unit-checked (a percentage must match a percentage fact,
   "N days" a days fact) after planted false claims showed that bare number matching let a real
   number be reused with a different meaning (e.g. "90% churn" from a 90-day window).
3. **Two attempts, then template.** A failing first answer gets one retry with the checker's
   reasons; only passing claims are shown; if none pass, a deterministic template built from the
   signal summaries is shown and the case says so. Templates pass the same checker (all 255 on the
   synthetic data).
4. **Explanations go stale; re-check daily for free.** Assessments refresh daily in place; stored
   claims are re-checked in code and stale ones removed. Rewrites (paid) happen only when evidence
   changes or nothing usable is left. The writer is told to prefer dates over "N days ago".
5. **Email labels need verbatim quotes.** A label whose quote is not found word for word in the
   email we sent is dropped. Confidence < 0.7 → "possible" (shown, not scored). Labels on an email
   flagged as an injection attempt are "quarantined" (shown, never scored).
6. **Injection: rules always, AI as a second opinion, structure as the real defence.** Rules run on
   every inbound email at sync time (no AI, no cost). The AI reader can also flag. Flagged email
   text is withheld from the case writer entirely. The planner (Phase 4) is rule-based and the AI
   has no write access, so an injection can at worst add a warning banner.
7. **Cost control.** Only inbound human customer emails are read (no auto-replies, bounces or our own
   mail); explanations are written only for open cases; identical inputs are cached; a daily budget
   stops spending (`AI_DAILY_BUDGET_USD`, $2 in dev). Backfills can use the Batches API at half price.
8. **Timestamps for AI calls are set by the app,** not the database default (which is the transaction
   start and gave every call in a run the same time, breaking per-run cost accounting).
