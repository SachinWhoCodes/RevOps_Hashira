import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type AuditRow } from "../api";
import { Badge, ErrorNote, Loading, PageHeader } from "../components/ui";
import { dateTime, label } from "../format";

type Verify = { ok: boolean; checked: number; total?: number; broken_at_seq?: number };

export default function AuditPage() {
  const rows = useQuery({ queryKey: ["audit"], queryFn: () => api<AuditRow[]>("/audit") });
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
                <td className="px-3 py-2 text-muted">{r.actor}</td>
                <td className="px-3 py-2">{label(r.action)}</td>
                <td className="px-3 py-2 font-mono text-xs text-muted">
                  {Object.entries(r.details).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`).join("  ")}
                  {r.evidence_count > 0 && <span className="ml-1 text-faint">· {r.evidence_count} evidence</span>}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-faint">{r.hash}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
