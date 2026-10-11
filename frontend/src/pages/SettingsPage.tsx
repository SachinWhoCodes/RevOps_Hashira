import { useQuery } from "@tanstack/react-query";
import { useMe } from "../App";
import { api, type AiUsage } from "../api";
import FieldMappingEditor from "../components/FieldMappingEditor";
import { Badge, ErrorNote, Loading, PageHeader, Panel } from "../components/ui";
import { polarityTone } from "../signals";

type Config = {
  engine_version: string;
  signals: { name: string; polarity: keyof typeof polarityTone; source: string; description: string }[];
  thresholds: Record<string, unknown>;
  scoring: Record<string, unknown>;
  field_mapping: Record<string, unknown>;
};

const Yaml = ({ value }: { value: unknown }) => (
  <pre className="max-h-96 overflow-auto rounded-md bg-canvas p-3 text-xs leading-relaxed text-muted">{JSON.stringify(value, null, 2)}</pre>
);

export default function SettingsPage() {
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => api<Config>("/config") });
  const usage = useQuery({ queryKey: ["ai-usage"], queryFn: () => api<AiUsage>("/ai/usage") });
  const me = useMe();
  const c = cfg.data;
  return (
    <>
      <PageHeader title="Settings" subtitle="Rules and points are starting values from the blueprint (the back-test tunes them). Admins can edit the CRM field mapping." />
      {cfg.isLoading && <Loading />}
      {cfg.error && <ErrorNote error={cfg.error} />}
      {c && (
        <div className="space-y-4">
          {usage.data && (
            <Panel title="AI usage (observed)">
              <p className="mb-2 text-sm text-muted">
                AI {usage.data.enabled ? "on" : "off (templates only)"} · spent today ${usage.data.spent_today_usd.toFixed(4)} of a $
                {usage.data.budget_usd_per_day.toFixed(2)} daily limit
              </p>
              <table className="w-full text-sm">
                <thead className="text-xs text-muted"><tr><th className="text-left font-medium">Purpose</th><th className="text-left font-medium">Model</th>
                  <th className="text-right font-medium">Calls</th><th className="text-right font-medium">Failed</th><th className="text-right font-medium">Total $</th><th className="text-right font-medium">Avg $ per call</th></tr></thead>
                <tbody>
                  {usage.data.by_purpose.map((r) => (
                    <tr key={r.purpose + r.model} className="border-t border-line">
                      <td className="py-1">{r.purpose.replace(/_/g, " ")}</td><td className="py-1 font-mono text-xs">{r.model}</td>
                      <td className="py-1 text-right tabular-nums">{r.calls}</td><td className="py-1 text-right tabular-nums">{r.failed}</td>
                      <td className="py-1 text-right tabular-nums">{r.cost_usd.toFixed(4)}</td><td className="py-1 text-right tabular-nums">{r.avg_cost_usd.toFixed(5)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          )}
          <Panel title={`Signals (${c.engine_version})`}>
            <table className="w-full text-sm">
              <tbody>
                {c.signals.map((s) => (
                  <tr key={s.name} className="border-b border-line last:border-0">
                    <td className="py-1.5 pr-3 font-mono text-xs">{s.name}</td>
                    <td className="py-1.5 pr-3"><Badge tone={polarityTone[s.polarity]}>{s.polarity}</Badge></td>
                    <td className="py-1.5 pr-3 text-xs text-faint">{s.source}</td>
                    <td className="py-1.5 text-muted">{s.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Signal thresholds"><Yaml value={c.thresholds} /></Panel>
            <Panel title="Scoring"><Yaml value={c.scoring} /></Panel>
          </div>
          {me.data?.role === "admin" ? <FieldMappingEditor /> : <Panel title="CRM field mapping"><Yaml value={c.field_mapping} /></Panel>}
        </div>
      )}
    </>
  );
}
