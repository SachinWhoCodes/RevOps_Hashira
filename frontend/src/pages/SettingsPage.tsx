import { useQuery } from "@tanstack/react-query";
import { Braces, Cpu, GitBranch, ListTree, Sparkles, Wallet } from "lucide-react";
import { api, type AiUsage } from "../api";
import FieldMappingEditor from "../components/FieldMappingEditor";
import { HonestyLabel } from "../components/domain";
import { Badge, Card, ErrorNote, Loading, Meter, PageHeader, StatTile, StatusDot, Tabs } from "../components/ui";
import { label, sentence } from "../format";
import { useUrlBatch, useUrlState } from "../lib/hooks";
import { useMe } from "../lib/session";
import { polarityTone } from "../signals";
import { SignalIcon } from "../components/domain";

type Config = {
  engine_version: string;
  signals: { name: string; polarity: keyof typeof polarityTone; source: string; description: string }[];
  thresholds: Record<string, unknown>;
  scoring: Record<string, unknown>;
  field_mapping: Record<string, unknown>;
};

function Json({ value }: { value: unknown }) {
  return <pre className="max-h-[28rem] overflow-auto rounded-xl border border-line bg-subtle p-4 font-mono text-[12px] leading-relaxed text-ink">{JSON.stringify(value, null, 2)}</pre>;
}

function AiTab() {
  const usage = useQuery({ queryKey: ["ai-usage"], queryFn: () => api<AiUsage>("/ai/usage") });
  const u = usage.data;
  if (usage.isLoading) return <Loading variant="cards" rows={3} />;
  if (usage.error) return <ErrorNote error={usage.error} />;
  if (!u) return null;
  const share = u.budget_usd_per_day ? u.spent_today_usd / u.budget_usd_per_day : 0;
  const totalCalls = u.by_purpose.reduce((n, r) => n + r.calls, 0);
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-xl border border-line bg-panel p-5 shadow-xs animate-fade-up">
          <div className="flex items-center gap-2 text-[13px] font-medium text-muted">
            <span className="grid size-7 place-items-center rounded-lg bg-accent-soft text-accent-fg ring-1 ring-accent/20">
              <Sparkles className="size-[15px]" aria-hidden />
            </span>
            AI
          </div>
          <div className="mt-3 flex items-center gap-2.5 text-[28px] leading-none font-semibold tracking-[-0.03em]">
            <StatusDot tone={u.enabled ? "ok" : "warn"} pulse={u.enabled} className="size-2.5" />
            {u.enabled ? "On" : "Off"}
          </div>
          <div className="mt-2 text-[12.5px] text-muted">{u.enabled ? "Claude writes; code checks every sentence" : "Templates only"}</div>
        </div>
        <StatTile label="Spent today" icon={Wallet} tone={share > 0.8 ? "warn" : "accent"} value={u.spent_today_usd} format={(n) => `$${n.toFixed(4)}`} delay={60}>
          <div className="mt-3">
            <Meter value={u.spent_today_usd} max={u.budget_usd_per_day || 1} tone={share > 0.8 ? "warn" : "accent"} />
            <div className="mt-1.5 text-xs text-muted">of a ${u.budget_usd_per_day.toFixed(2)} daily cap · {Math.round(share * 100)}%</div>
          </div>
        </StatTile>
        <StatTile label="Calls on record" icon={Cpu} value={totalCalls} delay={120} hint="Every AI call is logged with model, tokens and cost" />
      </div>
      <Card title="By purpose" icon={ListTree} actions={<HonestyLabel label="observed" />} flush>
        <div className="overflow-x-auto">
          <table className="w-full text-[13.5px]">
            <thead>
              <tr className="border-b border-line bg-subtle text-left text-[12px] text-muted">
                <th className="px-5 py-2.5 font-medium">Purpose</th>
                <th className="px-5 py-2.5 font-medium">Model</th>
                <th className="px-5 py-2.5 text-right font-medium">Calls</th>
                <th className="px-5 py-2.5 text-right font-medium">Failed</th>
                <th className="px-5 py-2.5 text-right font-medium">Total</th>
                <th className="px-5 py-2.5 text-right font-medium">Per call</th>
              </tr>
            </thead>
            <tbody>
              {u.by_purpose.map((r) => (
                <tr key={r.purpose + r.model} className="border-b border-line last:border-0">
                  <td className="px-5 py-3 font-medium text-ink">{sentence(label(r.purpose))}</td>
                  <td className="px-5 py-3 font-mono text-xs text-muted">{r.model}</td>
                  <td className="px-5 py-3 text-right tabular-nums">{r.calls.toLocaleString("en-IN")}</td>
                  <td className="px-5 py-3 text-right tabular-nums">{r.failed ? <Badge tone="risk">{r.failed}</Badge> : <span className="text-faint">0</span>}</td>
                  <td className="px-5 py-3 text-right font-medium tabular-nums">${r.cost_usd.toFixed(4)}</td>
                  <td className="px-5 py-3 text-right text-muted tabular-nums">${r.avg_cost_usd.toFixed(5)}</td>
                </tr>
              ))}
              {!u.by_purpose.length && (
                <tr>
                  <td colSpan={6} className="px-5 py-8 text-center text-sm text-faint">No AI calls yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function RulesTab({ c }: { c: Config }) {
  return (
    <div className="space-y-4">
      <Card title="Signals" description={`Engine ${c.engine_version} · each rule is a pure function of the account as of a date`} icon={GitBranch} flush>
        <ul className="divide-y divide-line">
          {c.signals.map((s) => {
            return (
              <li key={s.name} className="flex items-start gap-4 px-5 py-3.5">
                <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-subtle text-muted ring-1 ring-line">
                  <SignalIcon type={s.name} className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[12.5px] font-medium text-ink">{s.name}</span>
                    <Badge tone={polarityTone[s.polarity]} dot>{s.polarity}</Badge>
                    <span className="text-xs text-faint">{s.source}</span>
                  </div>
                  <p className="mt-1 text-[13px] leading-relaxed text-muted">{s.description}</p>
                </div>
              </li>
            );
          })}
        </ul>
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Signal thresholds" icon={Braces} description="Starting values; the back-test tunes them">
          <Json value={c.thresholds} />
        </Card>
        <Card title="Scoring" icon={Braces} description="Points per signal and case thresholds">
          <Json value={c.scoring} />
        </Card>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => api<Config>("/config") });
  const me = useMe();
  const [tab] = useUrlState("tab", "ai");
  const batch = useUrlBatch();
  const c = cfg.data;
  const admin = me.data?.role === "admin";
  return (
    <>
      <PageHeader title="Settings" description="Rules, scoring and AI usage. Admins can edit the CRM field mapping." />
      <Tabs
        className="mb-5"
        value={tab}
        onChange={(v) => batch({ tab: v === "ai" ? null : v })}
        tabs={[
          { value: "ai", label: "AI usage", icon: Sparkles },
          { value: "rules", label: "Rules & scoring", icon: GitBranch },
          { value: "mapping", label: "CRM field mapping", icon: ListTree },
        ]}
      />
      <div key={tab} className="animate-fade-in">
        {tab === "ai" && <AiTab />}
        {tab !== "ai" && cfg.isLoading && <Loading />}
        {tab !== "ai" && cfg.error && <ErrorNote error={cfg.error} />}
        {tab === "rules" && c && <RulesTab c={c} />}
        {tab === "mapping" && c && (admin ? <FieldMappingEditor /> : <Card title="CRM field mapping" icon={ListTree} description="Read-only: only admins can change it"><Json value={c.field_mapping} /></Card>)}
      </div>
    </>
  );
}
