import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api, type BacktestRunView, type HorizonBlock, type Metric, type OutcomeRow } from "../api";
import { useMe } from "../App";
import { Badge, ErrorNote, Loading, PageHeader, Panel } from "../components/ui";
import { date, dateTime, label } from "../format";

function M({ m, pct = false, whole = false }: { m?: Metric | null; pct?: boolean; whole?: boolean }) {
  if (!m || m.value == null) return <span className="text-faint">—</span>;
  const f = (v: number) => (pct ? `${Math.round(v * 100)}%` : whole ? String(Math.round(v)) : v.toFixed(2));
  return (
    <span className="tabular-nums">
      {f(m.value)}
      {m.ci95 && <span className="text-faint"> ({f(m.ci95[0])}–{f(m.ci95[1])})</span>}
      <span className="ml-1 text-[11px] text-faint">n={m.n}</span>
    </span>
  );
}

const SETS = [
  ["all", "All renewals"],
  ["held_out", "Held-out (later half)"],
  ["held_out_tuned_threshold", "Held-out, tuned threshold"],
] as const;

function HorizonTable({ run }: { run: BacktestRunView }) {
  const h = run.metrics.horizon;
  const td = "px-3 py-2";
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-line text-xs text-muted">
          <tr>
            <th className={`${td} text-left font-medium`}>Scored</th>
            <th className={`${td} text-left font-medium`}>Renewals</th>
            <th className={`${td} text-left font-medium`} title="Chance that a lost renewal ranks above a kept one (0.5 = coin flip)">AUC</th>
            <th className={`${td} text-left font-medium`}>Precision when flagged</th>
            <th className={`${td} text-left font-medium`}>Recall (lost renewals caught)</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(h).map(([days, blocks]) => (
            <Fragment key={days}>
              {SETS.map(([key, name]) => {
                const b: HorizonBlock = blocks[key];
                if (!b?.n) return null;
                return (
                  <tr key={`${days}-${key}`} className="border-b border-line align-top">
                    <td className={td}>
                      <div className="font-medium">{days} days before</div>
                      <div className="text-xs text-muted">{name}{b.flag_at !== run.metrics.flag_threshold.default ? ` · flag at ${b.flag_at}` : ""}</div>
                    </td>
                    <td className={`${td} tabular-nums`}>{b.n} <span className="text-faint">({b.positives} lost)</span></td>
                    <td className={td}><M m={b.auc} /></td>
                    <td className={td}><M m={b.precision} pct /></td>
                    <td className={td}><M m={b.recall} pct /></td>
                  </tr>
                );
              })}
              <tr key={`${days}-baselines`} className="border-b-2 border-line bg-canvas/60 align-top text-xs text-muted">
                <td className={td}>Baselines · {days} days</td>
                <td className={td} colSpan={4}>
                  Renewal date alone: AUC 0.50 (every renewal is equally close at a fixed horizon) ·{" "}
                  {blocks.all.best_single_signal && (
                    <>
                      best single signal <span className="text-ink">{label(blocks.all.best_single_signal.signal)}</span>: AUC <M m={blocks.all.best_single_signal.auc} />, precision <M m={blocks.all.best_single_signal.precision} pct />, recall <M m={blocks.all.best_single_signal.recall} pct /> ·{" "}
                    </>
                  )}
                  health score: {typeof blocks.all.health_score === "string" ? blocks.all.health_score : <M m={blocks.all.health_score?.auc} />}
                </td>
              </tr>
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Outcomes({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<"lost" | "all">("lost");
  const rows = useQuery({ queryKey: ["bt-outcomes"], queryFn: () => api<OutcomeRow[]>("/backtest/outcomes") });
  const relabel = useMutation({
    mutationFn: ({ id, outcome }: { id: string; outcome: string }) =>
      api(`/backtest/outcomes/${id}`, { method: "PATCH", json: { outcome } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bt-outcomes"] }),
  });
  const shown = (rows.data ?? []).filter((r) => filter === "all" || r.positive);
  return (
    <Panel
      title={`Renewal outcomes (${rows.data?.length ?? 0})`}
      actions={
        <div className="flex gap-1 text-xs">
          {(["lost", "all"] as const).map((f) => (
            <button key={f} onClick={() => setFilter(f)} className={`rounded-md px-2 py-0.5 ${filter === f ? "bg-accent-soft text-accent" : "text-muted"}`}>
              {f === "lost" ? "Lost revenue" : "All"}
            </button>
          ))}
        </div>
      }
    >
      <p className="mb-2 text-xs text-muted">
        Labels come from renewal-deal history: lost → churned; won for less → downgraded; won more than 14 days late → late. Correct any label you know better; corrected labels are kept on re-runs.
      </p>
      {rows.isLoading && <Loading />}
      <div className="max-h-[28rem] overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 border-b border-line bg-panel text-xs text-muted">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium">Account</th>
              <th className="px-2 py-1.5 text-left font-medium">Renewal</th>
              <th className="px-2 py-1.5 text-left font-medium">Outcome</th>
              <th className="px-2 py-1.5 text-left font-medium">Risk 90 / 60 / 30 days before</th>
              <th className="px-2 py-1.5" />
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.id} className="border-b border-line last:border-0">
                <td className="px-2 py-1.5">{r.account.name}</td>
                <td className="px-2 py-1.5 whitespace-nowrap text-muted">{date(r.renewal_date)}</td>
                <td className="px-2 py-1.5">
                  {isAdmin ? (
                    <select value={r.outcome} onChange={(e) => relabel.mutate({ id: r.id, outcome: e.target.value })}
                      className="rounded border border-line bg-panel px-1 py-0.5 text-xs">
                      {["renewed", "downgraded", "churned", "late"].map((o) => <option key={o}>{o}</option>)}
                    </select>
                  ) : (
                    <Badge tone={r.positive ? "risk" : "neutral"}>{r.outcome}</Badge>
                  )}
                  {r.label_source === "human" && <span className="ml-1 text-[11px] text-faint">corrected</span>}
                </td>
                <td className="px-2 py-1.5 tabular-nums text-muted">
                  {["90", "60", "30"].map((h) => r.risk_before[h] ?? "–").join(" / ")}
                  {r.missed && <Badge tone="warn">never flagged</Badge>}
                </td>
                <td className="px-2 py-1.5 text-right">
                  <Link to={`/backtest/replay/${r.account.id}`} className="text-xs text-accent hover:underline">Replay</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

export default function BacktestPage() {
  const me = useMe();
  const qc = useQueryClient();
  const run = useQuery({ queryKey: ["backtest"], queryFn: () => api<BacktestRunView | null>("/backtest/latest") });
  const rerun = useMutation({
    mutationFn: () => api<BacktestRunView>("/backtest/run", { method: "POST", json: {} }),
    onSuccess: (data) => {
      qc.setQueryData(["backtest"], data);
      qc.invalidateQueries({ queryKey: ["bt-outcomes"] });
    },
  });
  const isAdmin = me.data?.role === "admin";
  const r = run.data;
  const m = r?.metrics;
  return (
    <>
      <PageHeader
        title="Back-test"
        subtitle="Every past renewal, scored with only what was knowable at the time — the same code the live queue uses."
      >
        <div className="flex items-center gap-2">
          {r && <Badge tone={r.label === "observed" ? "ok" : "warn"} title="observed = real data; simulated = synthetic company">{r.label}</Badge>}
          {isAdmin && (
            <button disabled={rerun.isPending} onClick={() => rerun.mutate()}
              className="rounded-md border border-line bg-panel px-3 py-1.5 text-sm hover:bg-canvas disabled:opacity-60">
              {rerun.isPending ? "Running… (~15 s)" : r ? "Run again" : "Run back-test"}
            </button>
          )}
        </div>
      </PageHeader>
      {run.isLoading && <Loading />}
      {(run.error || rerun.error) && <ErrorNote error={run.error ?? rerun.error} />}
      {!run.isLoading && !r && <p className="text-sm text-muted">No back-test has been run yet.</p>}
      {r && m && m.events.total === 0 && (
        <p className="rounded-md border border-warn bg-warn-soft px-3 py-2 text-sm text-warn">{m.fallback ?? "No past renewals to test yet."}</p>
      )}
      {r && m && m.events.total > 0 && (
        <div className="space-y-4">
          <div className="rounded-md border border-warn bg-warn-soft px-3 py-2 text-sm text-warn">
            <div className="font-medium">Read these numbers with their labels</div>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs">
              {r.label === "simulated" && (
                <li>Synthetic company: the data generator and the rules were written by the same team, so this measures whether Casefile finds the patterns planted in the data, not real-world accuracy.</li>
              )}
              {r.config.excluded_signals.length > 0 && (
                <li>
                  Left out for the whole back-test: {r.config.excluded_signals.map(label).join(", ")} — they need AI email labels, and only {Math.round(r.config.label_coverage * 100)}% of past customer emails have been read, so early periods would look quieter than they were.
                </li>
              )}
              <li>Thresholds were tuned only on renewals before {date(m.split_date)}; "held-out" rows are renewals after that date.</li>
              {m.fallback && <li className="font-medium">{m.fallback}</li>}
            </ul>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <Panel title="Lost renewals flagged in advance">
              <div className="text-2xl font-semibold"><M m={m.lead_time.flagged_before_outcome} pct /></div>
              <p className="mt-1 text-xs text-muted">
                Median warning: <M m={m.lead_time.median_lead_days} whole /> days before the renewal. {m.lead_time.missed.length} of {m.lead_time.positives} were never flagged.
              </p>
            </Panel>
            <Panel title="Weekly top 10 (renewals in the next 90 days)">
              <div className="text-2xl font-semibold"><M m={m.rolling.precision_at_10} pct /></div>
              <p className="mt-1 text-xs text-muted">
                share of the top 10 that were really lost · nearest renewal first: <M m={m.rolling.baseline_nearest_renewal_first} pct /> · best single signal: <M m={m.rolling.baseline_best_single_signal} pct />
              </p>
            </Panel>
            <Panel title="False alarms on kept renewals">
              <div className="text-2xl font-semibold"><M m={m.false_alarms.flagged_in_last_90_days} pct /></div>
              <p className="mt-1 text-xs text-muted">kept renewals flagged at some point in their last 90 days</p>
            </Panel>
          </div>

          <Panel title={`Scored ${r.config.horizons.join(" / ")} days before each renewal`}>
            <p className="mb-2 text-xs text-muted">
              {m.events.scored} of {m.events.total} renewals could be scored ({Object.entries(m.skipped).map(([k, v]) => `${v} skipped: ${k}`).join("; ") || "none skipped"}).
              Positive = {m.positive_definition.join(" or ")}. Flag threshold {m.flag_threshold.default} (live default); the tuning half preferred {m.flag_threshold.tuned_on_tuning_period}.
            </p>
            <HorizonTable run={r} />
          </Panel>

          <Outcomes isAdmin={isAdmin} />
          <p className="text-xs text-faint">Run {dateTime(r.started_at)} · engine {r.engine_version} · run id {r.id.slice(0, 8)}</p>
        </div>
      )}
    </>
  );
}
