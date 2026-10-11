# ADR 0011 — Renewal patterns: learning from finished renewal cycles

Date: 2026-10-12. Status: accepted (the user's idea, built 2026-10-12; numbers below are from that day).

## Context

The user's idea: take every row of a client's history as a chain of activity, cut the chain where the client
decided (renewed, downgraded, left), learn what the pieces that ended badly looked like, and compare every
client's current, unfinished piece with them, in a separate, visual module that works for the demo company
(Lumora) and for any newly signed-up company, trained when the company's data arrives.

## Decisions

1. **A cycle is a renewal term with a known ending.** Endings are the renewal outcomes already derived from CRM
   deal history (`backtest/outcomes.py`: renewed, late, downgraded, churned); a cycle starts at the previous
   decision. Lost = churned or downgraded, the same definition as the back-test.
2. **Each finished cycle is described at 12 checkpoints** (30, 60 … 360 days before its decision) through the
   as-of snapshots, signals and scoring the live queue uses (rule R3), with the same exclusion of AI-label
   signals when past email was not read. Nine numbers per checkpoint (`patterns/features.py`), relative to the
   client where size would dominate: rules risk score, active users vs 6 months earlier and vs the 3 months
   before, seats in use, days since the last meeting, meetings in 90 days, customer emails in 60 days, renewal
   next step missing, economic buyer met. A checkpoint needs 90 days of data before it (else "no meetings"
   only means "no data yet"); cycles that cannot be described are counted with the reason.
3. **The model is nearest-neighbour matching on the path, trained per company** (`patterns/matching.py`). The
   client's pattern now and at the two checkpoints before (weights 1, 0.6, 0.36) is compared with finished
   cycles at the same distance from their decision; the 9 nearest are shown and counted. Training learns, per
   stage, how much each number separated renewals from losses **beyond chance**:
   `2 × max(0, |AUC − 0.5| − 2.77 × SE)` (Hanley & McNeil SE; 2.77 = 95% Bonferroni-corrected for nine numbers).
   A stage where nothing clears that bar gives **no verdict** rather than one made of noise. This was added after
   the first version matched at 120–300 days on weights that were pure noise.
4. **What is shown:** "N of 9 most similar past cycles were lost", the Wilson range, the usual rate at that
   stage, the nine cycles (each opens the account in the time machine at that moment), and the numbers that
   separated outcomes at that stage with where the client stands. Verdict words: *looks like lost renewals*
   (≥ 3 lost and at least twice the usual rate and a third), *looks like kept renewals* (none lost), else
   *mixed*. It is evidence beside the rules: it does **not** reorder the queue or open cases.
5. **Tested with no look-ahead** (`matching.evaluate`): each cycle in the later half is scored using only
   cycles whose outcome was known at that moment (`library_at`, decision date, not planned date) and weights
   learned from them; compared with the rules score at the same moment; the AUC difference has a paired
   bootstrap range; a winner is named only with ≥ 10 lost renewals tested (as the back-test's headline rule).
6. **Logistic regression was tried and parked.** In the prototype (same time-respecting test, Lumora) it did
   not beat the rules (AUC 0.82 vs 0.83 at 30 days) and was unstable further out (0.31 at 180 days); the
   rule of thumb of ~10 lost outcomes per input means ~50+ lost renewals per company. Revisit when a company
   has that much history.
7. **States for every company:** `no_history` (no describable finished cycle: clients are still placed in
   their cycle, with the reason), `learning` (fewer than 20 described cycles or 5 lost: paths without
   verdicts, progress meters), `ready`. No AI, so it runs in setup mode too: a new company sees its own
   history on day 0.
8. **Training triggers:** `casefile train-patterns [--org]`, "Learn again" (admins), a worker job queued after a
   sync with new records from usage, CRM or fixture sources (2-minute delay so a burst trains once), and a
   daily retrain (clients move along their cycles). About 10 s for 200 accounts; the last 3 trainings are kept.
9. **Storage:** `pattern_model` (weights, spreads, base rates, test), `cycle_pattern` (the numbers per
   checkpoint, no text), `pattern_match` (one per client). Tenant tables with row-level security
   (migration `a3f9c2d7e410`), deleted with the organisation. The page's activity chain is weekly counts only.
10. **Labels:** a model is `simulated` for synthetic (fixture) companies, `observed` otherwise. ONEWHO's product
    usage is generated (ADR 0010), so its usage-based patterns must not be called observed in reports.

## Results (2026-10-12)

Lumora (**simulated**): 197 decisions, 176 described (34 lost), 1,590 patterns, 9.5 s.
- Renewals and losses separate beyond chance only **30 and 60 days** before the decision; earlier stages give
  no verdict. 43 of 200 clients compared: 10 look like lost renewals, 27 mixed, 6 look like kept ones.
- Held-out test (89 renewals, 16 lost): 30 days — matching AUC 0.84 (0.70–0.96) vs rules 0.81 (0.67–0.94),
  difference +0.04 (−0.04 to +0.12): **no clear difference**. 60 days — 0.67 vs 0.73, no clear difference.
  90 days — too few lost renewals to tell (4 of 43).
- What separated outcomes at 30 days: rules risk score, users vs 6 months earlier, users vs the 3 months
  before, missing next step (weights 0.34–0.43). The synthetic generator plants these stories, so this says
  the method finds planted patterns, not that real renewals behave this way.

Other companies on the dev machine (observed): `northwind-test` learning (13 of 32 decisions describable,
4 lost; 19 ended before the data had 90 days of history); `onewho` no_history (9 decisions, none describable:
its CRM was created on 2026-10-11, so nothing existed before those decisions); new sign-ups no_history.

## Not done

Logistic regression (see 6); pooling patterns across companies (needs written consent in each data agreement);
any validation on a real company's renewal history; automated visual tests for the page.
