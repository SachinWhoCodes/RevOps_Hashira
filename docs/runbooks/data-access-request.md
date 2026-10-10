# Real-company data: access request and week-0 questionnaire

Send section 1 to the person who can authorise data use at the company. Send section 2 to whoever
runs their sales tools. Nothing real is synced until section 1 is signed (plan, section 8 checklist).

## 1. Data-use request (one page)

**Project:** Casefile — a hackathon prototype that flags revenue risk and drafts follow-up actions
for account owners, with every action approved by a person.

**What we would read (read-only first):**
- CRM: companies, contacts, deals (including renewal deals and their field history), owners,
  notes and tasks.
- Email: only threads with addresses at known customer domains, from the mailboxes of the account
  owners who opt in. Full text is deleted after 90 days; short snippets are kept for evidence.
- Calendar: meetings with external customer attendees.
- Product usage: daily counts per customer account (active users, key actions). No personal events.

**What we would write:** nothing until you agree in writing which records may be touched. Then only:
tasks, notes and the "next step" field in the CRM, Gmail **drafts** (never sent), and Slack direct
messages to the account owner. Every write needs a named person's approval and is logged.

**Purpose:** build and demonstrate the prototype at [event], [dates].

**Who can see the data:** [team member names]. No customer data in slides, screenshots or code
repositories. Names and emails are masked on any publicly shown screen.

**Duration and deletion:** access ends on [date]. All copies are deleted within 7 days after that,
and we confirm deletion in writing.

**Authorised by:** ____________________  **Role:** ____________  **Date:** ________

## 2. Week-0 questionnaire

| # | Question | Answer |
|---|---|---|
| 1 | Which CRM, and which plan or edition? (Salesforce hosted MCP needs Enterprise Edition or above.) | |
| 2 | Google Workspace or Microsoft 365? Can an admin create an **Internal** OAuth app? | |
| 3 | Is Slack used? Can a workspace admin approve a new app? | |
| 4 | Where does product usage live (database, warehouse, analytics tool)? Can we get a read-only, aggregated view per customer per day? | |
| 5 | How is a customer identified in the product (domain, CRM id, workspace id)? | |
| 6 | How many renewals, churns and downgrades are on record for the last 12–24 months? | |
| 7 | Which CRM fields hold renewal date, ARR, seats, champion and economic buyer? | |
| 8 | Is HubSpot's "sensitive data" setting turned on? (It blocks activities through the MCP server.) | |
| 9 | May demo writes touch real records, or only agreed test records? | |
| 10 | Is there an existing health score, and does it keep history? (Used as a baseline in the back-test.) | |
