# ADR 0002 — Signals, scoring and cases (Phase 2)

Status: accepted (2026-10-11). Rules: `signals/thresholds.yaml` v2, `scoring/scoring.yaml` v2.

1. **Signals are pure functions of an as-of snapshot.** No database, clock or AI inside a signal, so the
   live queue, the "view as of" time machine and the Phase 5 back-test run the same code.
2. **Every hit carries `facts` and `evidence`.** Facts are the only numbers an explanation may quote
   (Phase 3's checker enforces this); evidence points at the exact records. Summaries quote the
   numbers of the metric they name — a test caught a sentence naming key actions but quoting users.
3. **Usage decline has two comparisons (v2).** The blueprint rule (last 28 days vs the 28 before)
   misses slow slides: consecutive months look alike while usage is far below last quarter. We also
   compare the last 28 days with the 90 days before them (fires at a 30% drop).
4. **One opportunity signal does not open a case (v2).** Seat saturation alone opened 51 expansion
   cases out of 180 accounts on synthetic data — the alert fatigue the blueprint warns about.
   Saturation now scores 30 (case opens at 40), so a case needs two opportunity signals, and no
   expansion case opens while the account is at risk.
5. **Hysteresis only within one rule version.** Cases open at 40 and close below 30. After a rules
   change, an open case must meet the new opening threshold again (a test simulates this).
6. **"New cases per owner per day" cap moves to the Phase 4 digest.** The queue is ranked; capping it
   would only hide information. The cap belongs where people are interrupted.
7. **Audit log pulled forward from Phase 4.** Assessments and case openings/resolutions are already
   recorded in the hash-chained log; a test proves an edited row is detected even with the trigger off.
8. **Phase 2 exit gate corrected.** "All planted at-risk accounts in the top decile" cannot hold
   (44 of 180 accounts are planted at-risk; the top decile holds 18), and many planted declines start
   in the future. The gate is now measured on accounts whose decline started at least 14 days ago:
   precision@10, recall@k, AUC, and precision/recall at the case threshold — all labelled simulated.
9. **We do not tune thresholds to the synthetic generator.** Misses on mild, early declines
   (e.g. −14% vs the prior quarter) are reported, not "fixed" by lowering thresholds. Real thresholds
   come from the Phase 5 back-test on real history.
