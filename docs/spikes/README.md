# Spikes — short experiments that answer one risky question each

Each spike gets its own file (`S1-gmail.md`, …) with: question, what we ran, result, decision.

| Spike | Question | Needs | Status |
|---|---|---|---|
| S1 | Can we read customer threads incrementally (`history.list`) through an Internal Workspace app? | Google sandbox + `READ_GOOGLE_REFRESH_TOKEN` | Waiting for credentials. Reader code is written (Phase 1); run `casefile sync --kind gmail` |
| S2 | HubSpot: do `propertiesWithHistory`, engagements (given "sensitive data"), task/note creation and the deal next-step update work with our tokens? Same operations via the HubSpot MCP server? | HubSpot sandbox + read/write tokens | Waiting for credentials. Reader code is written (Phase 1); run `casefile sync --kind hubspot` |
| S3 | Can a Slack Socket Mode bot DM an owner with a button and receive the click? | Slack sandbox app | Waiting for credentials (Phase 4 dependency) |
| S4 | How accurate is domain-based matching? | Synthetic now; 50 real emails once permitted | **Simulated:** 28,670 of 28,673 emails with ground truth matched, 0 wrong; the 3 unmatched are reseller emails. Proves the rules are coded correctly; real-data accuracy still untested |
| S5 | `client.messages.parse` round-trip with a Pydantic schema on Haiku 5.5 and Opus 5.5: tokens, latency | `ANTHROPIC_API_KEY` | **Done 2026-10-11 (observed).** Both accept `output_format` + `output_config.effort`; Opus 5.5 also accepts `betas=[server-side-fallback-2026-07-01]` + `fallbacks="default"` via `beta.messages.parse`. Tiny test: Haiku 324 in / 24 out, $0.00004, 2.8 s; Opus 324 in / 32 out, $0.0019, 1.8 s |
| S6 | What shape will product usage arrive in? | Week-0 questionnaire answer #4 | Waiting for answer. Reader supports a SQL view and CSV |
| S7 | Can a Gmail draft be found again by a Message-ID we set (crash recovery)? | Google sandbox + write token | Waiting for credentials (Phase 4 dependency) |
