import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, FlaskConical, Play, Rewind, Target, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type BacktestRunView, type HorizonBlock, type Metric, type OutcomeRow } from "../api";
import { RangeBars } from "../components/charts";
import { HonestyLabel } from "../components/domain";
import { DataTable, type Column } from "../components/table/DataTable";
import { AccountMark, Badge, Button, Callout, Card, EmptyState, ErrorNote, PageHeader, Segmented, Select, Skeleton, Tabs, Tooltip, useToast } from "../components/ui";
import { date, dateTime, label, plural } from "../format";
import { cn } from "../lib/cn";
import { useUrlBatch, useUrlState } from "../lib/hooks";
import { useMe } from "../lib/session";

const fmt = (m: Metric | null | undefined, kind: "pct" | "num" | "days") => {
  if (!m || m.value == null) return "—";
  const f = (v: number) => (kind === "pct" ? `${Math.round(v * 100)}%` : kind === "days" ? String(Math.round(v)) : v.toFixed(2));
  return f(m.value);
};
const ci = (m: Metric | null | undefined, kind: "pct" | "num" | "days") => {
  if (!m?.ci95) return null;
  const f = (v: number) => (kind === "pct" ? `${Math.round(v * 100)}%` : kind === "days" ? String(Math.round(v)) : v.toFixed(2));
  return `${f(m.ci95[0])}–${f(m.ci95[1])}`;
};

function Headline({ title, m, kind, children, icon: Icon, tone }: { title: string; m: Metric | null | undefined; kind: "pct" | "days"; children?: React.ReactNode; icon: typeof Target; tone: string }) {
  return (
    <Card className="animate-fade-up" bodyClassName="p-5">
      <div className="flex items-center gap-2 text-[13px] font-medium text-muted">
        <span className={cn("grid size-7 place-items-center rounded-lg ring-1", tone)}><Icon className="size-[15px]" aria-hidden /></span>
        {title}
      </div>
      <div className="mt-3 flex items-baseline gap-2">
        <span className="text-[32px] leading-none font-semibold tracking-[-0.03em]">{fmt(m, kind)}</span>
        {ci(m, kind) && <span className="text-[13px] text-faint tabular-nums">95% range {ci(m, kind)}</span>}
      </div>
      {m && <div className="mt-1 text-xs text-faint">n = {m.n}</div>}
      {children && <div className="mt-3 text-[13px] leading-relaxed text-muted">{children}</div>}
    </Card>
  );
}

function MetricCell({ m, kind = "num" }: { m?: Metric | null; kind?: "pct" | "num" }) {
  if (!m || m.value == null) return <span className="text-faint">—</span>;
  const pos = (v: number) => `${Math.max(0, Math.min(1, v)) * 100}%`;
  return (
    <div className="min-w-32">
      <div className="flex items-baseline gap-1.5 tabular-nums">
        <span className="font-semibold text-ink">{fmt(m, kind)}</span>
        {m.ci95 && <span className="text-xs text-faint">{ci(m, kind)}</span>}
        <span className="text-[11px] text-faint">n={m.n}</span>
      </div>
      <div className="relative mt-1.5 h-1 rounded-full bg-hover">
        {m.ci95 && <div className="absolute inset-y-0 rounded-full bg-accent/25" style={{ left: pos(m.ci95[0]), width: `calc(${pos(m.ci95[1])} - ${pos(m.ci95[0])})` }} />}
        <div className="absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent ring-2 ring-panel" style={{ left: pos(m.value) }} />
      </div>
    </div>
  );
}

const SETS = [
  ["all", "All renewals"],
  ["held_out", "Held-out (later half)"],
  ["held_out_tuned_threshold", "Held-out, tuned threshold"],
] as const;

function HorizonView({ run }: { run: BacktestRunView }) {
  const days = Object.keys(run.metrics.horizon);
  const [tab, setTab] = useState(days[0]);
  const blocks = run.metrics.horizon[tab];
  const best = blocks?.all.best_single_signal;
  const hs = blocks?.all.health_score;
  return (
    <Card
      title="Scored before each renewal"
      description="Ranked using only what was known that many days ahead"
      icon={CalendarClock}
      flush
    >
      <Tabs className="px-5 pt-2" value={tab} onChange={setTab} tabs={days.map((d) => ({ value: d, label: `${d} days before` }))} />
      <div className="overflow-x-auto">
        <table className="w-full text-[13.5px]">
          <thead>
            <tr className="border-b border-line bg-subtle text-left text-[12px] text-muted">
              <th className="px-5 py-2.5 font-medium">Set</th>
              <th className="px-5 py-2.5 font-medium">Renewals</th>
              <th className="px-5 py-2.5 font-medium">
                <Tooltip content="Chance a lost renewal ranks above a kept one. 0.5 is a coin flip; 1.0 is perfect.">
                  <span className="cursor-help border-b border-dotted border-faint">AUC</span>
                </Tooltip>
              </th>
              <th className="px-5 py-2.5 font-medium">Precision when flagged</th>
              <th className="px-5 py-2.5 font-medium">Lost renewals caught</th>
            </tr>
          </thead>
          <tbody key={tab} className="animate-fade-in">
            {SETS.map(([key, name]) => {
              const b: HorizonBlock | undefined = blocks?.[key];
              if (!b?.n) return null;
              return (
                <tr key={key} className="border-b border-line align-top">
                  <td className="px-5 py-3.5">
                    <div className="font-medium text-ink">{name}</div>
                    {b.flag_at !== run.metrics.flag_threshold.default && <div className="text-xs text-muted">Flag at {b.flag_at}</div>}
                  </td>
                  <td className="px-5 py-3.5 tabular-nums">
                    {b.n} <span className="text-faint">({b.positives} lost)</span>
                  </td>
                  <td className="px-5 py-3.5"><MetricCell m={b.auc} /></td>
                  <td className="px-5 py-3.5"><MetricCell m={b.precision} kind="pct" /></td>
                  <td className="px-5 py-3.5"><MetricCell m={b.recall} kind="pct" /></td>
                </tr>
              );
            })}
            {best && (
              <tr className="border-b border-line bg-subtle/60 align-top text-muted">
                <td className="px-5 py-3.5">
                  <div className="font-medium">Baseline: best single signal</div>
                  <div className="text-xs capitalize">{label(best.signal)}</div>
                </td>
                <td className="px-5 py-3.5 text-faint">same</td>
                <td className="px-5 py-3.5"><MetricCell m={best.auc} /></td>
                <td className="px-5 py-3.5"><MetricCell m={best.precision} kind="pct" /></td>
                <td className="px-5 py-3.5"><MetricCell m={best.recall} kind="pct" /></td>
              </tr>
            )}
            <tr className="bg-subtle/60 text-muted">
              <td className="px-5 py-3.5 font-medium">Baseline: renewal date alone</td>
              <td className="px-5 py-3.5" colSpan={4}>AUC 0.50 by construction — at a fixed horizon every renewal is equally close.</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="px-5 py-3 text-xs text-muted">
        CRM health score baseline: {typeof hs === "string" ? hs : hs?.auc ? `AUC ${fmt(hs.auc, "num")}` : "not available"}
      </p>
    </Card>
  );
}

function Outcomes({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [show] = useUrlState("o_show", "lost");
  const [outcome] = useUrlState("o_outcome", "");
  const batch = useUrlBatch();
  const rows = useQuery({ queryKey: ["bt-outcomes"], queryFn: () => api<OutcomeRow[]>("/backtest/outcomes") });
  const relabel = useMutation({
    mutationFn: ({ id, outcome }: { id: string; outcome: string }) => api(`/backtest/outcomes/${id}`, { method: "PATCH", json: { outcome } }),
    onSuccess: (_, v) => {
      qc.invalidateQueries({ queryKey: ["bt-outcomes"] });
      toast({ tone: "ok", title: `Relabelled as ${v.outcome}`, description: "Corrected labels are kept on re-runs and recorded in the audit log." });
    },
  });
  const filtered = rows.data?.filter((r) => (show === "all" || r.positive) && (!outcome || r.outcome === outcome));
  const columns: Column<OutcomeRow>[] = [
    {
      id: "account",
      header: "Account",
      sortValue: (r) => r.account.name,
      csv: (r) => r.account.name,
      cell: (r) => (
        <div className="flex items-center gap-2.5">
          <AccountMark name={r.account.name} size="sm" />
          <span className="font-medium text-ink">{r.account.name}</span>
        </div>
      ),
    },
    { id: "renewal", header: "Renewal", sortValue: (r) => r.renewal_date, firstSort: "desc", csv: (r) => r.renewal_date, cell: (r) => <span className="whitespace-nowrap text-muted">{date(r.renewal_date)}</span> },
    {
      id: "outcome",
      header: "Outcome",
      sortValue: (r) => r.outcome,
      csv: (r) => r.outcome,
      cell: (r) => (
        <div className="flex items-center gap-2">
          {isAdmin ? (
            <Select selectSize="sm" className="w-32" value={r.outcome} onChange={(e) => relabel.mutate({ id: r.id, outcome: e.target.value })} aria-label={`Outcome for ${r.account.name}`}>
              {["renewed", "downgraded", "churned", "late"].map((o) => <option key={o}>{o}</option>)}
            </Select>
          ) : (
            <Badge tone={r.positive ? "risk" : "neutral"} dot>{r.outcome}</Badge>
          )}
          {r.label_source === "human" && <Badge tone="accent">Corrected</Badge>}
        </div>
      ),
    },
    ...(["90", "60", "30"] as const).map<Column<OutcomeRow>>((h) => ({
      id: `r${h}`,
      header: `Risk ${h}d before`,
      align: "right",
      firstSort: "desc",
      sortValue: (r) => r.risk_before[h],
      csv: (r) => r.risk_before[h],
      cell: (r) => {
        const v = r.risk_before[h];
        return v == null ? <span className="text-faint">–</span> : <span className={cn("font-medium", v >= 40 ? "text-risk-fg" : v >= 30 ? "text-warn-fg" : "text-muted")}>{v}</span>;
      },
    })),
    {
      id: "flag",
      header: "Flagged ahead",
      sortValue: (r) => (r.missed ? 0 : 1),
      csv: (r) => (r.missed ? "never" : "yes"),
      cell: (r) => (r.positive ? r.missed ? <Badge tone="warn">Never flagged</Badge> : <Badge tone="ok">Flagged</Badge> : <span className="text-faint">—</span>),
    },
    {
      id: "replay",
      header: "",
      hideable: false,
      align: "right",
      cell: (r) => (
        <Link to={`/backtest/replay/${r.account.id}`} className="inline-flex items-center gap-1 text-[13px] font-medium text-accent-fg hover:underline">
          <Rewind className="size-3.5" /> Replay
        </Link>
      ),
    },
  ];
  return (
    <div>
      <div className="mb-3 mt-8 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-[16px] font-semibold tracking-[-0.01em]">Renewal outcomes</h2>
          <p className="mt-0.5 text-[13px] text-muted">
            From renewal-deal history: lost → churned; won for less → downgraded; won 14+ days late → late. Correct any label you know better.
          </p>
        </div>
      </div>
      <DataTable
        id="bt"
        urlPrefix="o_"
        rows={filtered}
        loading={rows.isLoading}
        error={rows.error}
        columns={columns}
        rowKey={(r) => r.id}
        defaultSort={{ id: "renewal", dir: "desc" }}
        search={{ placeholder: "Search accounts", text: (r) => r.account.name }}
        csvName="casefile-renewal-outcomes"
        noun="renewals"
        filters={
          <>
            <Segmented
              size="xs"
              label="Which renewals"
              value={show}
              onChange={(v) => batch({ o_show: v === "lost" ? null : v, o_page: null })}
              options={[
                { value: "lost", label: "Lost revenue" },
                { value: "all", label: "All" },
              ]}
            />
            <Select selectSize="sm" className="w-36" value={outcome} onChange={(e) => batch({ o_outcome: e.target.value || null, o_page: null })} aria-label="Outcome">
              <option value="">Any outcome</option>
              {["renewed", "downgraded", "churned", "late"].map((o) => <option key={o} value={o}>{o}</option>)}
            </Select>
          </>
        }
      />
    </div>
  );
}

export default function BacktestPage() {
  const me = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const run = useQuery({ queryKey: ["backtest"], queryFn: () => api<BacktestRunView | null>("/backtest/latest") });
  const rerun = useMutation({
    mutationFn: () => api<BacktestRunView>("/backtest/run", { method: "POST", json: {} }),
    onSuccess: (data) => {
      qc.setQueryData(["backtest"], data);
      qc.invalidateQueries({ queryKey: ["bt-outcomes"] });
      toast({ tone: "ok", title: "Back-test finished", description: `${data.metrics.events.scored} renewals scored.` });
    },
  });
  const isAdmin = me.data?.role === "admin";
  const r = run.data;
  const m = r?.metrics;
  const rolling = m?.rolling;
  return (
    <>
      <PageHeader
        title="Back-test"
        description="Every past renewal, scored with only what was knowable at the time — the same code the live queue uses."
        actions={
          <>
            {r && <HonestyLabel label={r.label} />}
            {isAdmin && (
              <Button variant={r ? "secondary" : "primary"} icon={Play} loading={rerun.isPending} onClick={() => rerun.mutate()}>
                {rerun.isPending ? "Running (~15 s)" : r ? "Run again" : "Run back-test"}
              </Button>
            )}
          </>
        }
      />
      {(run.error || rerun.error) && <ErrorNote error={run.error ?? rerun.error} />}
      {run.isLoading && (
        <div className="grid gap-4 md:grid-cols-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-40 rounded-xl" />)}
        </div>
      )}
      {!run.isLoading && !r && (
        <Card>
          <EmptyState icon={FlaskConical} title="No back-test has been run yet" description="It replays your history week by week with the live rules. No AI is used." />
        </Card>
      )}
      {r && m && m.events.total === 0 && <Callout tone="warn" title="Nothing to test yet">{m.fallback ?? "No past renewals to test."}</Callout>}
      {r && m && m.events.total > 0 && rolling && (
        <div className="space-y-4">
          <Callout tone="warn" icon={TriangleAlert} title="Read these numbers with their labels">
            <ul className="mt-1 list-disc space-y-0.5 pl-4">
              {r.label === "simulated" && <li>Synthetic company: the generator and the rules were written by the same team, so this shows Casefile finds the planted patterns — not real-world accuracy.</li>}
              {r.config.excluded_signals.length > 0 && (
                <li>
                  Left out for the whole run: {r.config.excluded_signals.map(label).join(", ")} — they need AI email labels, and only {Math.round(r.config.label_coverage * 100)}% of past customer emails have been read.
                </li>
              )}
              <li>Thresholds were tuned only on renewals before {date(m.split_date)}; “held-out” rows are renewals after that date.</li>
              {m.fallback && <li className="font-medium text-ink">{m.fallback}</li>}
            </ul>
          </Callout>

          <div className="grid gap-4 lg:grid-cols-3">
            <Headline title="Lost renewals flagged in advance" m={m.lead_time.flagged_before_outcome} kind="pct" icon={Target} tone="bg-ok-soft text-ok-fg ring-ok/20">
              Median warning <span className="font-medium text-ink">{fmt(m.lead_time.median_lead_days, "days")} days</span> before the renewal. {m.lead_time.missed.length} of {m.lead_time.positives} were never flagged.
            </Headline>
            <Card className="animate-fade-up [animation-delay:60ms]" title="Weekly top 10: share really lost" description={`Renewals due in the next 90 days · ${plural(rolling.weeks, "week")}`}>
              <RangeBars
                rows={[
                  { key: "cf", label: "Casefile", value: rolling.precision_at_10.value, ci: rolling.precision_at_10.ci95, n: rolling.precision_at_10.n, emphasis: true },
                  { key: "ss", label: "Best single signal", value: rolling.baseline_best_single_signal.value, ci: rolling.baseline_best_single_signal.ci95 },
                  { key: "nr", label: "Nearest renewal first", value: rolling.baseline_nearest_renewal_first.value, ci: rolling.baseline_nearest_renewal_first.ci95 },
                ]}
              />
            </Card>
            <Headline title="False alarms on kept renewals" m={m.false_alarms.flagged_in_last_90_days} kind="pct" icon={TriangleAlert} tone="bg-warn-soft text-warn-fg ring-warn/25">
              Kept renewals flagged at some point in their last 90 days.
            </Headline>
          </div>

          <p className="text-[13px] text-muted">
            {m.events.scored} of {m.events.total} renewals could be scored
            {Object.entries(m.skipped).length > 0 && ` (${Object.entries(m.skipped).map(([k, v]) => `${v} skipped: ${k}`).join("; ")})`}. Positive = {m.positive_definition.join(" or ")}. Flag threshold {m.flag_threshold.default} (live default); the tuning half preferred {m.flag_threshold.tuned_on_tuning_period}.
          </p>
          <HorizonView run={r} />
          <Outcomes isAdmin={isAdmin} />
          <p className="pt-2 text-xs text-faint">Run {dateTime(r.started_at)} · engine {r.engine_version} · run {r.id.slice(0, 8)}</p>
        </div>
      )}
    </>
  );
}
