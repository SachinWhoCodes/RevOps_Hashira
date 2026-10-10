import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type AccountRow, type Unmatched } from "../api";
import { useMe } from "../App";
import { Badge, ErrorNote, Loading, PageHeader, Panel } from "../components/ui";
import { dateTime, label } from "../format";

type Override = { kind: "activity" | "email" | "domain" | "usage_key"; value: string; account_id: string };

function MapControl({ accounts, onMap, options }: {
  accounts: AccountRow[];
  options: { kind: Override["kind"]; value: string; text: string }[];
  onMap: (o: Override) => void;
}) {
  const [target, setTarget] = useState("");
  const [choice, setChoice] = useState(0);
  if (!options.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <select value={choice} onChange={(e) => setChoice(Number(e.target.value))} className="rounded-md border border-line px-1.5 py-1 text-xs">
        {options.map((o, i) => <option key={i} value={i}>{o.text}</option>)}
      </select>
      <select value={target} onChange={(e) => setTarget(e.target.value)} className="max-w-44 rounded-md border border-line px-1.5 py-1 text-xs">
        <option value="">Choose account…</option>
        {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
      </select>
      <button disabled={!target} onClick={() => onMap({ kind: options[choice].kind, value: options[choice].value, account_id: target })}
        className="rounded-md bg-accent px-2 py-1 text-xs font-medium text-white disabled:opacity-40">
        Map
      </button>
    </div>
  );
}

export default function IdentityPage() {
  const me = useMe();
  const qc = useQueryClient();
  const data = useQuery({ queryKey: ["unmatched"], queryFn: () => api<Unmatched>("/identity/unmatched") });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api<AccountRow[]>("/accounts") });
  const map = useMutation({
    mutationFn: (o: Override) => api<{ activities_changed: number; usage_keys_matched: number }>("/identity/overrides", { json: o }),
    onSuccess: () => qc.invalidateQueries(),
  });
  const isAdmin = me.data?.role === "admin";
  const sorted = [...(accounts.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));

  return (
    <>
      <PageHeader
        title="Unmatched records"
        subtitle="Records Casefile could not tie to exactly one customer. They are never guessed into a score; map them here."
      />
      {(data.isLoading || accounts.isLoading) && <Loading />}
      {data.error && <ErrorNote error={data.error} />}
      {map.error && <ErrorNote error={map.error} />}
      {map.data && (
        <p className="mb-3 rounded-md bg-ok-soft px-3 py-2 text-sm text-ok">
          Mapped. {map.data.activities_changed} records and {map.data.usage_keys_matched} usage keys re-linked.
        </p>
      )}
      {data.data && (
        <div className="space-y-4">
          <Panel title={`Emails and meetings (${data.data.activities_total})`}>
            {!data.data.activities.length && <p className="text-sm text-faint">Everything is matched.</p>}
            <ul className="divide-y divide-line">
              {data.data.activities.map((a) => {
                const options = [
                  { kind: "activity" as const, value: a.id, text: "only this record" },
                  ...a.external.map((p) => ({ kind: "email" as const, value: p, text: `everything from ${p}` })),
                  ...[...new Set(a.external.map((p) => p.split("@")[1]))].map((d) => ({ kind: "domain" as const, value: d, text: `everything from @${d}` })),
                ];
                return (
                  <li key={a.id} className="flex flex-wrap items-start justify-between gap-3 py-2.5 text-sm">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 text-xs text-faint">
                        {dateTime(a.at)} · {a.type} <Badge tone={a.method === "ambiguous" ? "warn" : "neutral"}>{label(a.method)}</Badge>
                      </div>
                      <div className="truncate font-medium">{a.subject ?? "(no subject)"}</div>
                      <div className="text-xs text-muted">from {a.from} · outside people: {a.external.join(", ") || "none"}</div>
                      {a.candidates.length > 0 && (
                        <div className="mt-0.5 text-xs text-muted">Could be: {a.candidates.map((c) => c.name).join(" or ")}</div>
                      )}
                    </div>
                    {isAdmin && <MapControl accounts={sorted} options={options} onMap={(o) => map.mutate(o)} />}
                  </li>
                );
              })}
            </ul>
          </Panel>
          <Panel title={`Product usage keys (${data.data.usage_keys.length})`}>
            {!data.data.usage_keys.length && <p className="text-sm text-faint">Every usage key is matched.</p>}
            <ul className="divide-y divide-line">
              {data.data.usage_keys.map((u) => (
                <li key={u.key} className="flex flex-wrap items-center justify-between gap-3 py-2 text-sm">
                  <div>
                    <span className="font-mono text-xs">{u.key}</span>
                    <span className="ml-2 text-xs text-faint">{u.rows} days of usage · last {u.last_date}</span>
                  </div>
                  {isAdmin && (
                    <MapControl accounts={sorted} options={[{ kind: "usage_key", value: u.key, text: "this usage key" }]} onMap={(o) => map.mutate(o)} />
                  )}
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      )}
    </>
  );
}
