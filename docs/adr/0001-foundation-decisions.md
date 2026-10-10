# ADR 0001 — Foundation decisions

Status: accepted (2026-10-11). Context: `docs/IMPLEMENTATION_PLAN.md` section 2.

1. **No LangGraph.** The approval workflow is a Postgres state machine on `change_item.status`,
   executed by a separate polling executor. Approvals arrive later from web or Slack and are rows the
   audit log references; a graph checkpoint would duplicate that state.
2. **Executor writes through REST APIs**, behind a `Writer` interface. MCP is used for Casefile's own
   read-only MCP server and optional Q&A read tools.
3. **Sandbox-first.** Synthetic company + team-owned HubSpot/Google/Slack sandbox accounts are the
   primary development target. Real company data is layered on when permission is signed.
4. **As-of from day one.** `field_history` and `activity.known_at` exist in schema v1. Analysis code
   reads history only through the snapshot loader.
5. **Connectors use `httpx` directly** instead of `hubspot-api-client` / `google-api-python-client`
   / `google-auth`. One HTTP stack gives uniform retries, `Retry-After` handling and `respx`-based
   contract tests. Google OAuth (refresh and the one-time loopback consent with PKCE) is ~100 lines
   in `connectors/google/auth.py`.
6. **Identifiers.** UUID primary keys for entities; big-integer keys for high-volume append tables
   (raw records, field history, usage, audit). Constraint names follow a naming convention so
   migrations stay deterministic.
7. **Synthetic data uses reserved `.test` domains** for every customer and the vendor, so no real
   company's addresses appear in fixtures or the sandbox.
8. **Known limitation:** the audit-log trigger can be disabled by the database owner. Phase 4 adds a
   least-privilege application role (INSERT/SELECT on `audit_log` only) for the app processes.
9. **Identity overrides have four scopes:** one record, an email address, a whole domain, or a usage
   key. "One record" is the default in the UI because a reseller or shared inbox can write about
   several customers; mapping the address would mis-file all of them.
10. **Gmail data minimisation:** headers are fetched first; a full message is fetched only when a
   participant is a known customer domain or contact, the thread is already kept, or it is a bounce.
   Consequence: emails from unknown outside domains (e.g. a reseller) are not seen on live Gmail
   at all, so they cannot appear in the unmatched list. They do appear in synthetic data.
