# ADR 0009 — Product UI: design system, information architecture, every screen rebuilt

Date: 2026-10-11. Status: accepted (asked for by the user: "elegant, industry-level UI for the entire project").

## Context

The UI grew phase by phase as plain Tailwind on raw elements: no shared components beyond a badge and a panel,
no dark mode, no pagination, a flat 13-item sidebar, and the most important screen (the account) as one long column.
It worked; it did not read as a product people would pay for.

## Decisions

1. **One design system, semantic tokens only.** `src/index.css` defines every colour twice (light and dark) as CSS
   variables — `canvas/panel/subtle/hover/line/ink/muted/faint`, `accent`, and `risk/warn/ok` each with `-fg` (text that
   clears contrast) and `-soft` (tinted background). Components never use raw hex. Status colours are reserved for
   status and always come with an icon or label, never colour alone. Inter (self-hosted, works offline for the demo).
2. **Components, not one-off markup** (`src/components/ui/`): Button (6 variants, loading state), Badge/StatusDot,
   Card/PageHeader, Field/Input/Select/Checkbox/Switch/PasswordInput/SearchInput, Callout/EmptyState/Skeleton,
   Modal/Drawer/Popover/Menu/Tooltip (portals, so tables and cards never clip them; focus returns on close; Esc closes),
   Segmented/Tabs, StatTile (count-up), ScoreRing, Meter, Avatar/AccountMark, Toast.
3. **Information architecture by the question a person brings:** *Workspace* (Overview, Queue, Approvals, Accounts),
   *Proof* (Outcomes, Back-test, Audit log), *Data* (Connections, Blind spots, Unmatched records), *Configure* (Setup,
   Team, Settings). Live counts on Queue, Approvals and Setup. Collapsible sidebar; ⌘K / Ctrl K / "/" command palette
   over pages and accounts; light, dark or system theme.
4. **A new Overview as the home page** (was a redirect to the queue): revenue at risk, expansion in play, actions awaiting
   approval, renewals in 90 days; the six cases that need attention; what is waiting for you; system health (sources,
   executor, blind spots); what drives risk across the book; risk by owner; renewals ahead. Every figure comes from an
   existing endpoint — nothing is invented or estimated on the client. Setup-mode admins still land on the checklist.
5. **Every list that can grow is a data table** (`components/table/DataTable.tsx`): search, sortable columns (missing
   values always last), column chooser, density, CSV of the current view (formula-injection safe), page sizes and
   numbered pages — with search, sort, filters and page **in the URL**, so a view can be linked and Back works.
   Applied to Accounts, Approvals, Team, Back-test outcomes, Unmatched records and usage keys. The Queue keeps ranked
   cards (it is worked top-down) with the same pagination. Small fixed tables (a six-row metric table) are not paginated.
6. **Server-side keyset paging for the audit log.** The chain grows without bound, so `/audit` gained `before_seq`
   (newest first, "Newer / Older", no offsets that shift as entries are added). Tested: pages chain with no gaps or
   overlaps. Rows expand to the full details and the hash link to the previous entry.
7. **The account page is a record page:** main column brief → proposed actions → what would lower risk → after the
   play → ask → timeline; a right rail with the renewal deal, people, usage and data sources (the evidence drawer's
   scroll targets). The time machine is a date control with "jump back" presets and an unmistakable banner while viewing
   the past. The timeline is a real vertical timeline with type icons, filters with counts and "show more".
8. **Approving is the most consequential action, so it is the clearest screen:** each line shows the tool it writes to,
   tier, status, a before → after diff for field updates and an email-shaped preview for drafts; approve / edit / reject
   sit on the line; a progress bar shows where the set stands; every decision confirms with a toast. "Approve all Tier 1"
   still never includes customer email.
9. **Auth screens: split layout** — the form on the left; on the right a slow "gradient flow" (blurred light drifting
   on transform-only animations, an aurora sweep, film grain, a faint grid) with a glimpse of the product. Password
   strength guidance; the confirmation and reset screens share the layout.
10. **Motion with restraint:** short decelerating fades and rises, staggered list entrances, rings and bars that grow
    once, a sliding segmented control (pure CSS). All of it is disabled under `prefers-reduced-motion`.
11. **Honesty labels stay first-class:** observed / simulated / estimated badges with explanations; "re-scored by the
    rules · not a forecast", "measured after · not proof of cause" and the shadow/setup mode banner are kept on every
    screen they were on.
12. **Performance:** every page is a lazy route chunk (main bundle 612 kB → 365 kB; pages 2–54 kB). Theme is applied
    before first paint by `public/theme-init.js` (a file, because production's CSP blocks inline scripts).
13. **Resilience:** when the API is unreachable the shell shows one "can't reach Casefile" screen with a retry,
    instead of every widget failing and retrying on its own (found while testing on a stopped server).

## Not done / next

- No automated visual or accessibility test suite yet (checked by hand in the browser, light and dark).
- Charts follow the data-viz marks spec (2px lines, 10% area wash, data-end rounding, hover tooltips); a table view for
  each chart is still missing.
- Demo mode (pseudonyms) belongs to Phase 7 and is not part of this change.
