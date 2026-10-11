import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type AuditRow } from "../api";
import { Badge, ErrorNote, Loading, PageHeader } from "../components/ui";
import { dateTime, label } from "../format";

type Verify = { ok: boolean; checked: number; total?: number; broken_at_seq?: number };

const ACTIONS = [
  ["", "Everything"],
  ["change_", "Change sets & decisions"],
  ["execution_", "Executions"],
  ["case_", "Cases"],
  ["assessment_", "Assessments"],
  ["explanation_", "AI explanations"],
  ["injection", "Injection flags"],
  ["digest_", "Slack digests"],
] as const;

export default function AuditPage() {
  const [action, setAction] = useState("");
  const [entity, setEntity] = useState("");
  const query = new URLSearchParams({ limit: "500", ...(action ? { action } : {}), ...(entity.trim() ? { entity_id: entity.trim() } : {}) }).toString();
  const rows = useQuery({ queryKey: ["audit", query], queryFn: () => api<AuditRow[]>(`/audit?${query}`) });
  const [verify, setVerify] = useState<Verify | null>(null);
  const [checking, setChecking] = useState(false);
  const run = async () => {
    setChecking(true);
    try {
      setVerify(await api<Verify>("/audit/verify"));
    } finally {
      setChecking(false);
    }
  };
  return (
    <>
      <PageHeader title="Audit log" subtitle="Write-once record of what Casefile did and why. Each entry is chained to the previous one by a hash.">
        <div className="flex items-center gap-2">
          {verify && (
            <Badge tone={verify.ok ? "ok" : "risk"}>
              {verify.ok ? `chain intact · ${verify.checked} entries verified` : `chain broken at #${verify.broken_at_seq}`}
            </Badge>
          )}
          <button onClick={run} disabled={checking} className="rounded-md border border-line bg-panel px-3 py-1.5 text-sm hover:bg-canvas disabled:opacity-60">
            {checking ? "Verifying…" : "Verify chain"}
          </button>
        </div>
      </PageHeader>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <select value={action} onChange={(e) => setAction(e.target.value)} className="rounded-md border border-line bg-panel px-2 py-1">
          {ACTIONS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
        </select>
        <input value={entity} onChange={(e) => setEntity(e.target.value)} placeholder="Filter by record id (change line, case, account…)"
          className="min-w-64 flex-1 rounded-md border border-line bg-panel px-2 py-1" />
        <a href={`/api/audit?${query}&format=csv`} className="rounded-md border border-line bg-panel px-3 py-1 hover:bg-canvas">Export CSV</a>
        <a href={`/api/audit?${query}&format=download`} className="rounded-md border border-line bg-panel px-3 py-1 hover:bg-canvas">Export JSON</a>
      </div>
      {rows.isLoading && <Loading />}
      {rows.error && <ErrorNote error={rows.error} />}
      <div className="overflow-x-auto rounded-lg border border-line bg-panel">
        <table className="w-full text-sm">
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              <th className="px-3 py-2 text-left font-medium">#</th>
              <th className="px-3 py-2 text-left font-medium">When</th>
              <th className="px-3 py-2 text-left font-medium">Who</th>
              <th className="px-3 py-2 text-left font-medium">What</th>
              <th className="px-3 py-2 text-left font-medium">Details</th>
              <th className="px-3 py-2 text-left font-medium">Hash</th>
            </tr>
          </thead>
          <tbody>
            {rows.data?.map((r) => (
              <tr key={r.seq} className="border-b border-line align-top last:border-0">
                <td className="px-3 py-2 text-faint tabular-nums">{r.seq}</td>
                <td className="px-3 py-2 whitespace-nowrap text-muted">{dateTime(r.ts)}</td>
                <td className="px-3 py-2 text-muted">{r.actor.split(":")[0]}</td>
                <td className="px-3 py-2">
                  {label(r.action)}
                  {r.entity_id && (
                    <button onClick={() => setEntity(r.entity_id ?? "")} className="block font-mono text-[11px] text-faint hover:text-accent" title="Show every entry for this record">
                      {r.entity_type} {r.entity_id.slice(0, 8)}
                    </button>
                  )}
                  {r.model && <div className="text-[11px] text-faint">{r.model} · {r.prompt_version}</div>}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-muted">
                  {Object.entries(r.details).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`).join("  ")}
                  {r.evidence_count > 0 && <span className="ml-1 text-faint">· {r.evidence_count} evidence</span>}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-faint" title={`prev ${r.prev_hash}`}>{r.hash.slice(0, 12)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
