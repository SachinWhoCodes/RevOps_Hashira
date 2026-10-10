import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Queue } from "../api";
import ScoreBreakdown from "../components/ScoreBreakdown";
import { Badge, ErrorNote, Loading, PageHeader } from "../components/ui";
import { money } from "../format";
import { chipText, polarityTone } from "../signals";

const confidenceTone = { high: "ok", medium: "warn", low: "risk" } as const;

export default function QueuePage() {
  const [kind, setKind] = useState<"risk" | "expansion">("risk");
  const [owner, setOwner] = useState("all");
  const queue = useQuery({
    queryKey: ["queue", kind, owner],
    queryFn: () => api<Queue>(`/queue?kind=${kind}&owner=${owner}`),
  });
  const q = queue.data;
  return (
    <>
      <PageHeader
        title="Queue"
        subtitle={
          q
            ? `${q.items.length} open ${kind} cases · ${money(q.revenue_at_stake)} ARR ${kind === "risk" ? "at stake" : "in scope"} · ranked by ${kind === "risk" ? "risk × ARR × urgency" : "opportunity × ARR"}`
            : undefined
        }
      >
        <div className="flex items-center gap-2">
          <div className="flex rounded-md border border-line bg-panel p-0.5 text-sm">
            {(["risk", "expansion"] as const).map((k) => (
              <button key={k} onClick={() => setKind(k)}
                className={`rounded px-3 py-1 capitalize ${kind === k ? "bg-accent-soft font-medium text-accent" : "text-muted"}`}>
                {k}
              </button>
            ))}
          </div>
          <select value={owner} onChange={(e) => setOwner(e.target.value)} className="rounded-md border border-line bg-panel px-2 py-1.5 text-sm">
            <option value="all">All owners</option>
            <option value="me">My accounts</option>
            {q?.owners.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        </div>
      </PageHeader>
      {queue.isLoading && <Loading />}
      {queue.error && <ErrorNote error={queue.error} />}
      {q && !q.items.length && (
        <p className="rounded-lg border border-line bg-panel px-4 py-8 text-center text-sm text-muted">
          No open {kind} cases{owner !== "all" ? " for this owner" : ""}.
          {kind === "expansion" && " An expansion case needs at least two opportunity signals and no open risk case."}
        </p>
      )}
      <ol className="space-y-2">
        {q?.items.map((item) => {
          const score = kind === "risk" ? item.risk : item.opportunity;
          const shown = item.signals.filter((s) => (kind === "risk" ? s.polarity !== "opportunity" : s.polarity === "opportunity"));
          return (
            <li key={item.case_id} className="rounded-lg border border-line bg-panel px-4 py-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-3">
                  <div className="w-6 pt-0.5 text-right text-sm font-semibold text-faint tabular-nums">{item.rank}</div>
                  <div className="min-w-0">
                    <Link to={`/accounts/${item.account.id}`} className="font-semibold hover:text-accent">{item.account.name}</Link>
                    {item.headline && <div className="text-sm text-ink">{item.headline}</div>}
                    <div className="text-xs text-muted">
                      {item.owner ?? "unassigned"} · ARR {money(item.arr)}{item.arr_assumed ? " (assumed)" : ""} ·{" "}
                      {item.days_to_renewal == null ? "renewal date unknown" : item.days_to_renewal < 0 ? "renewal overdue" : `renewal in ${item.days_to_renewal} days`}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone={confidenceTone[item.confidence]} title={item.sources_missing.length ? `Missing: ${item.sources_missing.join(", ")}` : "All sources present"}>
                    {item.confidence} confidence
                  </Badge>
                  <div className={`rounded-md px-2.5 py-1 text-sm font-semibold tabular-nums ${kind === "risk" ? "bg-risk-soft text-risk" : "bg-ok-soft text-ok"}`}>
                    {score}/100
                  </div>
                  <ScoreBreakdown
                    label={kind === "risk" ? "Risk" : "Opportunity"}
                    score={score}
                    lines={kind === "risk" ? item.risk_lines : item.opportunity_lines}
                    arr={item.arr}
                    arrAssumed={item.arr_assumed}
                    urgency={kind === "risk" ? item.urgency_factor : 1}
                    daysToRenewal={item.days_to_renewal}
                    priority={kind === "risk" ? item.priority : item.opportunity_priority}
                  />
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5 pl-9">
                {shown.map((s, i) => (
                  <Badge key={i} tone={polarityTone[s.polarity]} title={s.summary}>{chipText(s)}</Badge>
                ))}
                {item.blind_spots.length > 0 && (
                  <Badge title={item.blind_spots.map((b) => b.text).join("\n")}>{item.blind_spots.length} blind spot{item.blind_spots.length > 1 ? "s" : ""}</Badge>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </>
  );
}
