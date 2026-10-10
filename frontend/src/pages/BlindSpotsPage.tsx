import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type BlindSpots } from "../api";
import { Badge, ErrorNote, Loading, PageHeader, Panel } from "../components/ui";
import { label } from "../format";

export default function BlindSpotsPage() {
  const data = useQuery({ queryKey: ["blind-spots"], queryFn: () => api<BlindSpots>("/blind-spots") });
  const d = data.data;
  return (
    <>
      <PageHeader title="Blind spots" subtitle="What Casefile cannot see. A quiet account with no data is not a healthy account." />
      {data.isLoading && <Loading />}
      {data.error && <ErrorNote error={data.error} />}
      {d && (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <Panel title="Unmatched records">
              <div className="text-2xl font-semibold">{d.unmatched_records + d.ambiguous_records}</div>
              <div className="text-xs text-muted">{d.unmatched_records} unmatched · {d.ambiguous_records} ambiguous ·{" "}
                <Link to="/identity" className="text-accent hover:underline">map them</Link></div>
            </Panel>
            <Panel title="Unmatched usage keys">
              <div className="text-2xl font-semibold">{d.unmatched_usage_keys}</div>
              <div className="text-xs text-muted">product accounts not tied to a customer</div>
            </Panel>
            <Panel title="Connections not fresh">
              <div className="text-2xl font-semibold">{d.connections_not_fresh.length}</div>
              <div className="text-xs text-muted">{d.connections_not_fresh.map((c) => `${c.kind}/${c.name}`).join(", ") || "all sources fresh"}</div>
            </Panel>
          </div>
          <Panel title={`Accounts with gaps (of ${d.accounts_assessed} assessed)`}>
            <ul className="divide-y divide-line">
              {Object.entries(d.by_code).map(([code, entry]) => (
                <li key={code} className="py-2.5 text-sm">
                  <details>
                    <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
                      <span>
                        <span className="font-medium">{label(code)}</span>
                        <span className="ml-2 text-muted">{entry.text}</span>
                      </span>
                      <Badge tone={entry.count > 10 ? "warn" : "neutral"}>{entry.count} accounts</Badge>
                    </summary>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {entry.accounts.map((a) => (
                        <Link key={a.id} to={`/accounts/${a.id}`} className="rounded border border-line px-1.5 py-0.5 text-xs text-muted hover:text-accent">{a.name}</Link>
                      ))}
                    </div>
                  </details>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      )}
    </>
  );
}
