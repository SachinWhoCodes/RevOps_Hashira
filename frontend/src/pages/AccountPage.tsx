import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, type Timeline, type TimelineEvent } from "../api";
import AskPanel from "../components/AskPanel";
import AssessmentPanel from "../components/AssessmentPanel";
import OutcomesPanel from "../components/OutcomesPanel";
import PlanPanel from "../components/PlanPanel";
import RelationshipMap from "../components/RelationshipMap";
import RiskLevers from "../components/RiskLevers";
import UsageChart from "../components/UsageChart";
import { Badge, ErrorNote, Loading, Panel } from "../components/ui";
import { ago, date, dateTime, label, money } from "../format";

const FILTERS = [
  { key: "all", text: "All" },
  { key: "email", text: "Emails" },
  { key: "meeting", text: "Meetings" },
  { key: "note", text: "Notes" },
  { key: "crm_change", text: "CRM changes" },
] as const;

function EventCard({ e, asOf }: { e: TimelineEvent; asOf: Date }) {
  if (e.kind === "crm_change") {
    return (
      <li className="rounded-md border border-line bg-canvas px-3 py-2 text-sm">
        <div className="text-xs text-faint">{dateTime(e.at)} · CRM change</div>
        <ul className="mt-1 space-y-0.5">
          {e.changes.map((c, i) => (
            <li key={i} className="text-muted">
              <span className="text-ink">{c.label}</span> · {label(c.field)} →{" "}
              <span className="font-medium text-ink">{c.value === "" ? "(empty)" : String(c.value)}</span>
            </li>
          ))}
        </ul>
      </li>
    );
  }
  return (
    <li id={`ev-${e.id}`} className={`rounded-md border px-3 py-2 text-sm transition-shadow ${e.injection_flag ? "border-risk" : "border-line"} bg-panel`}>
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-faint">
        <span>{dateTime(e.at)}</span>
        <span>·</span>
        <span className="capitalize">{e.kind}</span>
        {e.kind === "email" && <span>{e.direction === "outbound" ? "sent" : "received"}</span>}
        {e.scheduled && <Badge tone="accent">scheduled · {ago(e.at, asOf)}</Badge>}
        {e.cancelled && <Badge tone="warn">cancelled</Badge>}
        {e.auto_reply && <Badge tone="warn">auto-reply</Badge>}
        {e.bounce && <Badge tone="risk">bounced</Badge>}
        {e.via === "sandbox_mirror" && (
          <Badge tone="accent" title="Read back from the sandbox HubSpot portal Casefile writes to">from sandbox HubSpot</Badge>
        )}
        {e.injection_flag && <Badge tone="risk" title={e.injection_reason ?? ""}>instructions aimed at software</Badge>}
        {e.labels.map((l, i) => (
          <Badge key={i} tone={l.status === "accepted" ? "accent" : l.status === "quarantined" ? "risk" : "neutral"}
            title={`"${l.quote}" (confidence ${l.confidence})`}>
            {l.label.replace(/_/g, " ")}{l.status !== "accepted" ? ` · ${l.status}` : ""}
          </Badge>
        ))}
        <Badge title="How this record was linked to the account">
          {label(e.match.method)} {e.match.confidence < 1 ? `· ${Math.round(e.match.confidence * 100)}%` : ""}
        </Badge>
      </div>
      {e.subject && <div className="mt-1 font-medium">{e.subject}</div>}
      {e.snippet && <p className="mt-0.5 text-muted">{e.snippet}</p>}
      <div className="mt-1 text-xs text-faint">
        {e.from && <>from {e.from} · </>}
        {e.people.length ? e.people.join(", ") : e.participants.join(", ")}
      </div>
    </li>
  );
}

export default function AccountPage() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const asOfParam = params.get("as_of") ?? "";
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  const timeline = useQuery({
    queryKey: ["timeline", id, asOfParam],
    queryFn: () => api<Timeline>(`/accounts/${id}?limit=1000${asOfParam ? `&as_of=${asOfParam}` : ""}`),
  });
  const t = timeline.data;
  const asOf = t ? new Date(t.as_of) : new Date();
  const events = (t?.events ?? []).filter((e) => filter === "all" || e.kind === filter);
  const renewal = t?.deals?.find((d) => d.type === "renewal" && !d.closed);

  return (
    <>
      <div className="mb-1 text-sm">
        <Link to="/accounts" className="text-accent hover:underline">← Accounts</Link>
      </div>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{t?.account.name ?? "Account"}</h1>
          {t?.exists && (
            <p className="mt-0.5 text-sm text-muted">
              {t.account.domains?.join(", ")} · owner {t.account.owner ?? "unassigned"} · ARR {money(t.account.arr)} ·{" "}
              {t.account.seats_purchased ?? "?"} seats · renewal {date(t.account.renewal_date)}
              {t.account.days_to_renewal != null && ` (${t.account.days_to_renewal} days)`}
            </p>
          )}
        </div>
        <label className="flex items-center gap-2 text-sm text-muted">
          View as of
          <input
            type="date"
            value={asOfParam}
            onChange={(e) => setParams(e.target.value ? { as_of: e.target.value } : {})}
            className="rounded-md border border-line bg-panel px-2 py-1 text-sm"
          />
          {asOfParam && (
            <button className="text-accent hover:underline" onClick={() => setParams({})}>today</button>
          )}
        </label>
      </div>

      {asOfParam && (
        <p className="mb-4 rounded-md bg-accent-soft px-3 py-2 text-sm text-accent">
          Showing this account exactly as it was known at the end of {date(asOfParam)}. Anything recorded later is hidden.
        </p>
      )}
      {timeline.isLoading && <Loading />}
      {timeline.error && <ErrorNote error={timeline.error} />}
      {t && !t.exists && <p className="text-sm text-muted">This account did not exist yet on that date.</p>}
      {t?.exists && (t.flagged_emails?.length ?? 0) > 0 && (
        <div className="mb-4 rounded-md border border-risk bg-risk-soft px-3 py-2 text-sm text-risk">
          <div className="font-medium">
            {t.flagged_emails!.length} email{t.flagged_emails!.length > 1 ? "s" : ""} contained instructions aimed at software.
            They were not followed, and nothing they said counts towards the score.
          </div>
          <ul className="mt-1 space-y-0.5 text-xs">
            {t.flagged_emails!.map((f) => (
              <li key={f.id}>{date(f.at)} · {f.subject} — <span className="italic">"{f.quote ?? f.reason}"</span></li>
            ))}
          </ul>
        </div>
      )}
      {t?.exists && t.assessment && (
        <AssessmentPanel a={t.assessment} cases={t.cases} explanation={t.explanation} asOfParam={asOfParam} accountId={t.account.id} />
      )}
      {t?.exists && t.assessment?.levers &&
        (t.assessment.risk.score > 0 || (!asOfParam && (t.cases ?? []).some((c) => c.status === "open"))) && (
        <RiskLevers v={t.assessment.levers} />
      )}
      {t?.exists && !asOfParam &&
        (t.cases ?? []).filter((c) => c.status === "open").map((c) => <PlanPanel key={c.id} caseId={c.id} accountId={t.account.id} />)}
      {t?.exists && !asOfParam && <OutcomesPanel outcomes={t.outcomes ?? []} />}
      {t?.exists && !asOfParam && <AskPanel key={t.account.id} accountId={t.account.id} />}
      {t?.exists && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
          <div className="space-y-4">
            <Panel title="Data sources">
              <div className="flex flex-wrap gap-1.5">
                {["crm", "gmail", "gcal", "hubspot", "usage"].map((s) => (
                  <Badge key={s} tone={t.sources?.includes(s) ? "ok" : "warn"}>
                    {s}{t.sources?.includes(s) ? "" : " · missing"}
                  </Badge>
                ))}
              </div>
            </Panel>
            <div id="deal-panel" className="rounded-lg transition-shadow">
            <Panel title="Renewal deal">
              {renewal ? (
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                  <dt className="text-muted">Stage</dt><dd>{label(renewal.stage ?? "—")}</dd>
                  <dt className="text-muted">Amount</dt><dd>{money(renewal.amount)}</dd>
                  <dt className="text-muted">Close date</dt><dd>{date(renewal.close_date)}</dd>
                  <dt className="text-muted">Next step</dt>
                  <dd>
                    {renewal.next_step ? renewal.next_step : <Badge tone="warn">none recorded</Badge>}
                    {renewal.next_step_since && <span className="ml-1 text-xs text-faint">since {date(renewal.next_step_since)}</span>}
                  </dd>
                </dl>
              ) : (
                <p className="text-sm text-faint">No open renewal deal.</p>
              )}
            </Panel>
            </div>
            {t.relationships && <RelationshipMap m={t.relationships} />}
            <div id="usage-panel" className="rounded-lg transition-shadow">
              <Panel title="Product usage">
                <UsageChart weeks={t.usage_weekly ?? []} seats={t.account.seats_purchased} />
              </Panel>
            </div>
          </div>
          <Panel
            title={`Timeline · ${t.event_count ?? 0} records`}
            actions={
              <div className="flex gap-1">
                {FILTERS.map((f) => (
                  <button key={f.key} onClick={() => setFilter(f.key)}
                    className={`rounded-md px-2 py-0.5 text-xs ${filter === f.key ? "bg-accent-soft text-accent" : "text-muted hover:text-ink"}`}>
                    {f.text}
                  </button>
                ))}
              </div>
            }
          >
            <ul className="space-y-2">
              {events.map((e, i) => (
                <EventCard key={e.kind === "crm_change" ? `c${e.at}${i}` : e.id} e={e} asOf={asOf} />
              ))}
              {!events.length && <p className="text-sm text-faint">Nothing here.</p>}
            </ul>
          </Panel>
        </div>
      )}
    </>
  );
}
