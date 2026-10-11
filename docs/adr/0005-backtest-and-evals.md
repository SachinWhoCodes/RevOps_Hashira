# ADR 0005 — Proving it: back-test and evaluation suite (Phase 5)

Status: accepted (2026-10-11). Results: [docs/RESULTS.md](../RESULTS.md) (generated, do not edit).

1. **Outcome labels come from CRM renewal history, not from the synthetic generator.** Lost →
   churned; won for less than the previous won deal → downgraded; won more than 14 days after the
   first planned close date → late; otherwise renewed. This works unchanged on a real CRM. People can
   correct labels on the back-test screen; corrected labels (`label_source = human`) survive re-runs
   and the change is audited.
2. **One code path.** The back-test rebuilds each past date with `AccountHistory.as_of` and scores
   it with the live `run_signals` and `score` (rule R3). Leakage is tested three ways: the Phase 1
   property test, canary future records (a "left" status, a lost deal, a cancellation email and zero
   usage, all dated after the scoring time, change nothing), and real synced history (a deal lost
   later shows its earlier stage; a contact marked left later looks active the day before).
3. **Signals with incomplete inputs are left out of the whole run.** Negative tone and upgrade
   interest need AI email labels, and only 0.7% of past customer emails have been read (cost
   control). Scoring the past with them would make early periods look quieter than they were. The
   runner measures coverage and excludes them below 95%; the report says so.
4. **Honest tuning.** The flag threshold and the "best single signal" baseline are chosen on the
   earlier half of renewals only; results are reported on the later half as well as on everything.
   The tuning half preferred a threshold of 25 (live default 40). We did **not** change the live
   default: it was tuned on synthetic data, where false alarms are almost absent, so lowering it
   would look free here and cost owners attention on real data. Revisit with a real back-test.
5. **Ranges that stay honest for small n.** Proportions use Wilson intervals (4 of 4 is reported as
   51–100%, not 100–100% as a naive bootstrap gives). AUC uses a seeded bootstrap. Weekly series
   overlap (each week looks 90 days ahead), so their ranges use a 13-week block bootstrap; a
   per-week bootstrap had made them look three times more certain than they are.
6. **Baselines.** Renewal date alone (AUC 0.50 by construction at a fixed horizon; "nearest renewal
   first" in the weekly ranking), the best single signal (chosen on the tuning half), and the CRM's
   own health score when it has history (the synthetic CRM has none, and the report says so).
7. **What the results say (simulated).** Casefile ranks lost renewals well 30 days out (held-out AUC
   0.82, 0.69–0.94) and its weekly top 10 beats working the renewal calendar (32% vs 19%). It is not
   clearly better than the best single signal (meeting gap, 30%) on this data. At the live threshold
   it is precise but misses most losses (recall 25% held-out at 30 days).
8. **Why it misses (simulated diagnosis, uses the generator's planted stories).** Of 20 lost
   renewals never flagged: 10 are planted "surprise churns" with no warning signs in the data,
   9 are slow usage declines (25–40% over 2–4 months) that fire the usage signal (25 points) but stay
   under the 40-point case threshold on their own, and 1 is a usage cliff.
9. **One command for every suite.** `make evals` runs the test-backed suites (planted claims,
   executor safety, leakage, signals) and the data suites (matching, ranking, rules-only injection,
   back-test), stores `eval_run` rows and regenerates `docs/RESULTS.md`. AI suites run only with
   `AI=1`. CI runs the test-backed suites through the normal test job.
