import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Brain, GitCompareArrows, Layers, RefreshCw, Scale, Scissors, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  api,
  type PatternClient,
  type PatternClientRow,
  type PatternFeature,
  type PatternMapView,
  type PatternNeighbour,
  type PatternsOverview,
  type PatternStrand,
} from "../api";
import { StackedBar } from "../components/charts";
import { HonestyLabel } from "../components/domain";
import { CycleCompare } from "../components/patterns/CycleCompare";
import { PathChart } from "../components/patterns/PathChart";
import { PatternMap } from "../components/patterns/PatternMap";
import { PrecedentDots, outcomeText, outcomeTone, verdictText, verdictTone } from "../components/patterns/shared";
import { WhatMatters } from "../components/patterns/WhatMatters";
import { DataTable, type Column } from "../components/table/DataTable";
import { AccountMark, Badge, Button, Card, EmptyState, ErrorNote, Meter, PageHeader, Segmented, Select, Skeleton, StatTile, Tabs, useToast } from "../components/ui";
import { date, dateShort, dateTime, money } from "../format";
import { cn } from "../lib/cn";
import { useUrlState } from "../lib/hooks";

const VERDICT_RANK: Record<string, number> = { lost_like: 0, mixed: 1, renewed_like: 2 };
const PATH_METRICS = ["risk", "usage.users_vs_6mo", "eng.meetings_90d", "eng.days_since_meeting"];
const SHORT: Record<string, string> = {
  risk: "Risk",
  "usage.users_vs_6mo": "Usage",
  "eng.meetings_90d": "Meetings",
  "eng.days_since_meeting": "Last meeting",
};

function pickDefault(rows: PatternClientRow[] | undefined): PatternClientRow | undefined {
  if (!rows?.length) return undefined;
  const placed = rows.filter((r) => r.stage != null);
  return (
    [...placed].sort(
      (a, b) =>
        (VERDICT_RANK[a.verdict ?? ""] ?? 3) - (VERDICT_RANK[b.verdict ?? ""] ?? 3) ||
        (b.lost ?? 0) - (a.lost ?? 0) ||
        (a.days_to_renewal ?? 9999) - (b.days_to_renewal ?? 9999),
    )[0] ?? rows[0]
  );
}

// ---------------------------------------------------------------- learning / no history: progress only

function LibraryStatus({ overview }: { overview: PatternsOverview }) {
  const m = overview.model;
  const r = overview.readiness;
  const s = overview.stats;
  if (!m || !r || !s || m.status === "ready") return null;
  const reasons = Object.entries(s.undescribed ?? {});
  return (
    <Card className="mb-6" bodyClassName="p-5">
      <div className="flex flex-wrap items-center gap-x-8 gap-y-4">
        <div className="flex items-center gap-2.5">
          <span className="grid size-8 place-items-center rounded-lg bg-warn-soft text-warn-fg ring-1 ring-warn/25">
            <Brain aria-hidden className="size-4" />
          </span>
          <span className="text-[14px] font-semibold text-ink">{m.status === "learning" ? "Still learning" : "Nothing finished to learn from yet"}</span>
        </div>
        <div className="grid min-w-64 flex-1 gap-3 sm:grid-cols-2">
          <div>
            <div className="mb-1 flex justify-between text-xs text-muted"><span>Finished cycles</span><span className="tabular-nums">{r.cycles} / {r.min_cycles}</span></div>
            <Meter value={Math.min(r.cycles, r.min_cycles)} max={r.min_cycles} tone="accent" />
          </div>
          <div>
            <div className="mb-1 flex justify-between text-xs text-muted"><span>Lost</span><span className="tabular-nums">{r.lost} / {r.min_lost}</span></div>
            <Meter value={Math.min(r.lost, r.min_lost)} max={r.min_lost} tone="risk" />
          </div>
        </div>
      </div>
      {reasons.length > 0 && <div className="mt-3 text-xs text-faint">Not usable: {reasons.map(([why, n]) => `${n} ${why}`).join(" · ")}</div>}
    </Card>
  );
}

// ---------------------------------------------------------------- one client

function PrecedentMeter({ share, ci, base }: { share: number; ci: [number, number] | null; base: number }) {
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  return (
    <div className="relative h-9 min-w-48 flex-1">
      <div className="absolute inset-x-0 top-3 h-2.5 rounded-full bg-hover" />
      <div className="absolute top-3 left-0 h-2.5 origin-left rounded-full bg-risk animate-grow-x" style={{ width: pct(share), animationDuration: "1.2s", animationDelay: "250ms" }} />
      {ci && (
        <div className="absolute top-[17px] h-px bg-ink/50 animate-fade-in" style={{ left: pct(ci[0]), width: `calc(${pct(ci[1])} - ${pct(ci[0])})`, animationDelay: "1200ms" }}>
          <span className="absolute -top-[4px] left-0 h-[9px] w-px bg-ink/50" />
          <span className="absolute -top-[4px] right-0 h-[9px] w-px bg-ink/50" />
        </div>
      )}
      <div className="absolute top-1 h-[26px] w-0.5 rounded bg-ink animate-fade-in" style={{ left: pct(base), animationDelay: "800ms" }} />
      <span className="absolute top-[26px] -translate-x-1/2 text-[10.5px] whitespace-nowrap text-muted animate-fade-in" style={{ left: `max(28px, ${pct(base)})`, animationDelay: "800ms" }}>
        usual {pct(base)}
      </span>
    </div>
  );
}

function CycleChip({ n, stage, onHover, index }: { n: PatternNeighbour; stage: number | null; onHover: (id: string | null) => void; index: number }) {
  const c = n.cycle;
  const point = c.points.find((p) => p.days === stage) ?? c.points[c.points.length - 1];
  return (
    <li className="animate-scale-in" style={{ animationDelay: `${index * 45}ms` }}>
      <Link
        to={point ? `/accounts/${c.account_id}?as_of=${point.at.slice(0, 10)}` : `/accounts/${c.account_id}`}
        onMouseEnter={() => onHover(n.cycle_id)}
        onMouseLeave={() => onHover(null)}
        onFocus={() => onHover(n.cycle_id)}
        onBlur={() => onHover(null)}
        className="group inline-flex max-w-full items-center gap-2 rounded-full border border-line bg-panel py-1.5 pr-2.5 pl-2 transition-[border-color,box-shadow] hover:border-line-strong hover:shadow-sm"
        title={`${c.name} · ${outcomeText[c.outcome] ?? c.outcome} ${date(c.cycle_end)} · opens as it was then`}
      >
        <span aria-hidden className={cn("size-2 shrink-0 rounded-full", outcomeTone(c.outcome) === "risk" ? "bg-risk" : "bg-ok")} />
        <span className="min-w-0 truncate text-[12.5px] font-medium text-ink">{c.name}</span>
        <span className="shrink-0 text-[11px] text-faint tabular-nums">{dateShort(c.cycle_end)}</span>
      </Link>
    </li>
  );
}

function ClientDetail({ detail, overview, rows, onPick }: { detail: PatternClient; overview: PatternsOverview; rows: PatternClientRow[]; onPick: (id: string) => void }) {
  const features = overview.features ?? [];
  const available = PATH_METRICS.map((k) => features.find((f) => f.key === k)).filter((f): f is PatternFeature => {
    if (!f) return false;
    const has = (pts: { f: Record<string, number | null> }[]) => pts.some((p) => p.f[f.key] != null);
    return has(detail.trajectory) || (detail.now ? detail.now.f[f.key] != null : false) || detail.neighbours.some((n) => has(n.cycle.points));
  });
  const [metricParam, setMetric] = useUrlState("metric", "risk");
  const metric = available.find((f) => f.key === metricParam) ?? available[0];
  const [highlight, setHighlight] = useState<string | null>(null);
  const mix = detail.mix;
  const sorted = useMemo(
    () => [...rows].sort((a, b) => (VERDICT_RANK[a.verdict ?? ""] ?? 3) - (VERDICT_RANK[b.verdict ?? ""] ?? 3) || a.name.localeCompare(b.name)),
    [rows],
  );

  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          {detail.name}
          {detail.verdict && <Badge tone={verdictTone[detail.verdict]} dot>{verdictText[detail.verdict]}</Badge>}
        </span>
      }
      description={
        detail.renewal_date
          ? `Decision ${date(detail.renewal_date)} · ${detail.days_to_renewal != null && detail.days_to_renewal >= 0 ? `${detail.days_to_renewal} days` : "overdue"}`
          : undefined
      }
      actions={
        <>
          <Select selectSize="sm" aria-label="Client" value={detail.account_id} onChange={(e) => onPick(e.target.value)} className="w-52">
            {sorted.map((r) => (
              <option key={r.account_id} value={r.account_id}>
                {r.name}
                {r.verdict ? ` — ${r.lost}/${r.k}` : ""}
              </option>
            ))}
          </Select>
          <Link to={`/accounts/${detail.account_id}`} aria-label="Open account" title="Open account" className="grid size-8 place-items-center rounded-lg text-muted hover:bg-hover hover:text-ink">
            <ArrowUpRight aria-hidden className="size-4" />
          </Link>
        </>
      }
    >
      <div key={detail.account_id} className="space-y-6">
        {mix && mix.lost_share ? (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3 animate-fade-up">
            <div className="flex items-baseline gap-1">
              <span className="text-[44px] leading-none font-semibold tracking-[-0.03em] text-ink">{mix.lost}</span>
              <span className="text-[18px] font-medium text-faint">/{mix.k}</span>
            </div>
            <div className="space-y-1.5">
              <div className="text-[13px] font-medium text-ink">similar past cycles lost</div>
              <PrecedentDots outcomes={detail.neighbours.map((n) => n.lost)} size="md" animate />
            </div>
            <PrecedentMeter share={mix.lost_share.value ?? 0} ci={mix.lost_share.ci95} base={mix.base_rate ?? 0} />
          </div>
        ) : (
          detail.note && <div className="rounded-lg bg-subtle px-3 py-2 text-[12.5px] text-muted">{detail.note}</div>
        )}

        {metric && (
          <div>
            <Segmented
              size="xs"
              label="Number"
              value={metric.key}
              onChange={setMetric}
              options={available.map((f) => ({ value: f.key, label: SHORT[f.key] ?? f.label }))}
              className="mb-3 w-full"
            />
            <PathChart client={detail} stages={overview.stages ?? []} feature={metric} highlight={highlight} onHighlight={setHighlight} />
          </div>
        )}

        {detail.neighbours.length > 0 && (
          <ul className="flex flex-wrap gap-2">
            {detail.neighbours.map((n, i) => (
              <CycleChip key={n.cycle_id} n={n} stage={detail.stage} onHover={setHighlight} index={i} />
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- accuracy: two scores on one scale

function Duel({ rows, delay = 0 }: { rows: { key: string; label: string; value: number | null; ci: [number, number] | null; strong: boolean }[]; delay?: number }) {
  const lo = 0.3;
  const pos = (v: number) => `${((Math.max(lo, Math.min(1, v)) - lo) / (1 - lo)) * 100}%`;
  return (
    <div>
      <div className="relative h-12">
        <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-hover" />
        <div className="absolute top-1 bottom-1 w-px bg-ink/40" style={{ left: pos(0.5) }} />
        <span className="absolute -top-1.5 -translate-x-1/2 text-[10px] text-faint" style={{ left: pos(0.5) }}>coin flip</span>
        <span className="absolute -top-1.5 right-0 text-[10px] text-faint">perfect</span>
        {rows.map((r, i) =>
          r.value == null ? null : (
            <div key={r.key}>
              {r.ci && <div className={cn("absolute h-px", r.strong ? "bg-accent" : "bg-ink/50")} style={{ left: pos(r.ci[0]), width: `calc(${pos(r.ci[1])} - ${pos(r.ci[0])})`, top: i === 0 ? "38%" : "64%" }} />}
              <div
                className={cn("absolute size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-panel animate-pop", r.strong ? "bg-accent" : "bg-ink/60")}
                style={{ left: pos(r.value), top: i === 0 ? "38%" : "64%", animationDelay: `${delay + i * 150}ms` }}
                title={`${r.label} ${r.value.toFixed(2)}${r.ci ? ` (${r.ci[0].toFixed(2)}–${r.ci[1].toFixed(2)})` : ""}`}
              />
            </div>
          ),
        )}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[12px] tabular-nums">
        {rows.map((r) => (
          <span key={r.key} className="inline-flex items-center gap-1.5">
            <span aria-hidden className={cn("size-2.5 rounded-full", r.strong ? "bg-accent" : "bg-ink/60")} />
            <span className="text-muted">{r.label}</span>
            <span className="font-semibold text-ink">{r.value == null ? "—" : r.value.toFixed(2)}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function Accuracy({ overview }: { overview: PatternsOverview }) {
  const stages = Object.entries(overview.evaluation?.stages ?? {}).sort((a, b) => Number(a[0]) - Number(b[0]));
  if (!stages.length) return <p className="py-6 text-center text-[13px] text-faint">Not tested yet</p>;
  return (
    <div className="grid gap-6 md:grid-cols-3">
      {stages.map(([d, s], i) => {
        const better = s.finding.startsWith("matching");
        const worse = s.finding.startsWith("the rules");
        return (
          <div key={d}>
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-[13px] font-semibold text-ink">{d} days before</span>
              <span className="text-[11px] text-faint tabular-nums">{s.lost} lost / {s.n}</span>
            </div>
            <Duel
              delay={i * 250}
              rows={[
                { key: "m", label: "Matching", value: s.matching_auc.value, ci: s.matching_auc.ci95, strong: true },
                { key: "r", label: "Rules", value: s.rules_auc.value, ci: s.rules_auc.ci95, strong: false },
              ]}
            />
            <Badge tone={better ? "ok" : worse ? "warn" : "neutral"} className="mt-2">
              {better ? "matching better" : worse ? "rules better" : s.finding.startsWith("too few") ? "too few losses to tell" : "no clear difference"}
            </Badge>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- the page

export default function PatternsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const [params] = useSearchParams();
  const overview = useQuery({ queryKey: ["patterns", "overview"], queryFn: () => api<PatternsOverview>("/patterns") });
  const version = overview.data?.model?.version;
  const clients = useQuery({ queryKey: ["patterns", "clients", version], queryFn: () => api<PatternClientRow[]>("/patterns/clients"), enabled: version != null });
  const [clientParam, setClient] = useUrlState("client", "");
  const fallback = useMemo(() => pickDefault(clients.data), [clients.data]);
  const selected = clients.data?.some((r) => r.account_id === clientParam) ? clientParam : (fallback?.account_id ?? "");
  const detail = useQuery({
    queryKey: ["patterns", "client", selected, version],
    queryFn: () => api<PatternClient>(`/patterns/clients/${selected}`),
    enabled: !!selected && version != null,
    placeholderData: (prev) => prev,
  });
  // The story is told on the most similar finished cycle next to the selected client's open cycle; with no
  // comparison yet, on the selected client's own history.
  const current = detail.data?.account_id === selected ? detail.data : null;
  const nearestCycle = current?.neighbours[0] ?? null;
  const focusId = current ? (nearestCycle?.cycle.account_id ?? selected) : null;
  const focusStrand = useQuery({ queryKey: ["patterns", "strand", focusId], queryFn: () => api<PatternStrand>(`/patterns/strand/${focusId}`), enabled: !!focusId });
  const clientStrand = useQuery({ queryKey: ["patterns", "strand", selected], queryFn: () => api<PatternStrand>(`/patterns/strand/${selected}`), enabled: !!nearestCycle });
  const ownLast = current?.past_cycles.length ? current.past_cycles[current.past_cycles.length - 1] : null;

  const ready = (overview.data?.stages ?? []).filter((s) => s.ready).map((s) => s.stage);
  const [stageParam, setStage] = useUrlState("stage", "");
  const mapStage = ready.includes(Number(stageParam)) ? Number(stageParam) : current?.stage != null && ready.includes(current.stage) ? current.stage : ready[0];
  const map = useQuery({
    queryKey: ["patterns", "map", mapStage, version],
    queryFn: () => api<PatternMapView>(`/patterns/map?stage=${mapStage}`),
    enabled: mapStage != null && version != null,
    placeholderData: (prev) => prev,
  });
  const [tab, setTab] = useState<"matters" | "accuracy">("matters");
  const train = useMutation({
    mutationFn: () => api<PatternsOverview>("/patterns/train", { method: "POST" }),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["patterns"] });
      toast({ tone: "ok", title: `Learned from ${d.stats?.described_cycles ?? 0} cycles`, description: `${d.stats?.matched_clients ?? 0} clients compared · ${d.stats?.seconds ?? 0} s` });
    },
    onError: (e) => toast({ tone: "risk", title: "Could not learn the patterns", description: String((e as Error).message) }),
  });

  // Picking a client from the table scrolls up to it (not on the first load).
  const detailRef = useRef<HTMLDivElement>(null);
  const lastPick = useRef(clientParam);
  useEffect(() => {
    if (clientParam && clientParam !== lastPick.current) detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    lastPick.current = clientParam;
  }, [clientParam]);

  const o = overview.data;
  const model = o?.model;
  const s = o?.stats;
  const ev30 = o?.evaluation?.stages?.["30"];

  const columns: Column<PatternClientRow>[] = [
    {
      id: "name",
      header: "Client",
      sortValue: (r) => r.name,
      cell: (r) => (
        <span className="flex min-w-0 items-center gap-2.5">
          <AccountMark name={r.name} size="sm" />
          <span className={cn("truncate font-medium", r.account_id === selected ? "text-accent-fg" : "text-ink")}>{r.name}</span>
        </span>
      ),
      csv: (r) => r.name,
    },
    {
      id: "renewal",
      header: "Decision",
      sortValue: (r) => r.days_to_renewal,
      cell: (r) => <span className="text-muted tabular-nums">{r.days_to_renewal == null ? "—" : r.days_to_renewal < 0 ? "overdue" : `${r.days_to_renewal} d`}</span>,
      csv: (r) => r.renewal_date,
    },
    {
      id: "precedent",
      header: "Similar past cycles",
      sortValue: (r) => (r.k ? (r.lost ?? 0) / r.k : null),
      firstSort: "desc",
      cell: (r) => (r.neighbours.length ? <PrecedentDots outcomes={r.neighbours} /> : <span className="text-[12px] text-faint">—</span>),
      csv: (r) => (r.k ? `${r.lost}/${r.k}` : ""),
    },
    { id: "risk", header: "Risk", align: "right", sortValue: (r) => r.risk_now, firstSort: "desc", cell: (r) => <span className="tabular-nums">{r.risk_now ?? "—"}</span>, csv: (r) => r.risk_now },
    { id: "arr", header: "ARR", align: "right", sortValue: (r) => r.arr, firstSort: "desc", cell: (r) => <span className="tabular-nums">{money(r.arr)}</span>, csv: (r) => r.arr, hideable: true },
  ];
  const href = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("client", id);
    return `/patterns?${next.toString()}`;
  };

  return (
    <div>
      <PageHeader
        title="Renewal patterns"
        eyebrow={model ? <><HonestyLabel label={model.label} /><span>v{model.version} · {dateTime(model.trained_at)}</span></> : undefined}
        actions={o?.can_train && model ? <Button icon={RefreshCw} loading={train.isPending} onClick={() => train.mutate()}>Learn again</Button> : undefined}
      />

      {overview.isLoading && <Skeleton className="h-72 rounded-xl" />}
      {overview.error && <ErrorNote error={overview.error} />}
      {o && !model && (
        <Card>
          <EmptyState
            icon={Sparkles}
            title="Not learned yet"
            action={o.can_train ? <Button variant="primary" icon={Brain} loading={train.isPending} onClick={() => train.mutate()}>Learn from my history</Button> : undefined}
          />
        </Card>
      )}

      {o && model && s && (
        <>
          <LibraryStatus overview={o} />

          <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile label="Cycles learned" value={s.described_cycles} icon={Layers} tone="accent">
              <StackedBar
                className="mt-3"
                segments={[
                  { key: "renewed", value: (s.outcomes.renewed ?? 0) + (s.outcomes.late ?? 0), tone: "ok", label: "renewed" },
                  { key: "lost", value: (s.outcomes.churned ?? 0) + (s.outcomes.downgraded ?? 0), tone: "risk", label: "lost" },
                ]}
              />
            </StatTile>
            <StatTile label="Lost" value={s.lost_cycles} icon={Scissors} tone="risk" delay={60} />
            <StatTile label="Clients compared" value={s.matched_clients} icon={GitCompareArrows} delay={120} hint={`of ${s.clients}`} />
            <StatTile
              label="Matching, 30 d"
              value={ev30?.matching_auc.value != null ? ev30.matching_auc.value * 100 : null}
              format={(n) => (n / 100).toFixed(2)}
              icon={Scale}
              delay={180}
              hint={ev30?.rules_auc.value != null ? `rules ${ev30.rules_auc.value.toFixed(2)}` : undefined}
            />
          </div>

          <Card className="mb-6" bodyClassName="p-5">
            {focusStrand.data && current && (!nearestCycle || clientStrand.data) ? (
              <CycleCompare
                key={`${focusId}-${selected}`}
                focus={focusStrand.data}
                focusEnd={nearestCycle?.cycle.cycle_end ?? ownLast?.renewal_date ?? null}
                client={nearestCycle ? (clientStrand.data ?? null) : null}
                daysToRenewal={current.days_to_renewal}
                stage={current.stage}
                snapshotDays={nearestCycle?.cycle.points.map((p) => p.days)}
              />
            ) : (
              <Skeleton className="h-72 rounded-lg" />
            )}
          </Card>

          <div className="mb-6 grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
            <div ref={detailRef} className="scroll-mt-24">
              {detail.data ? (
                <ClientDetail detail={detail.data} overview={o} rows={clients.data ?? []} onPick={setClient} />
              ) : selected ? (
                <Skeleton className="h-[560px] rounded-xl" />
              ) : (
                <Card><EmptyState icon={GitCompareArrows} title="No clients yet" compact /></Card>
              )}
            </div>
            <Card
              title="Pattern map"
              actions={ready.length > 1 ? <Segmented size="xs" label="Stage" value={String(mapStage)} onChange={setStage} options={ready.map((d) => ({ value: String(d), label: `${d} d` }))} /> : undefined}
            >
              {map.data?.ready ? (
                <PatternMap map={map.data} selected={selected} neighbourIds={new Set(current?.stage === map.data.stage ? current.neighbours.map((n) => n.cycle_id) : [])} onSelect={setClient} />
              ) : (
                <EmptyState icon={Sparkles} compact title="Not enough history yet" />
              )}
            </Card>
          </div>

          <div className="mb-6">
            <DataTable
              id="pattern-clients"
              urlPrefix="t"
              title="Clients"
              rows={clients.data}
              loading={clients.isLoading}
              error={clients.error}
              columns={columns}
              rowKey={(r) => r.account_id}
              rowHref={(r) => href(r.account_id)}
              rowClassName={(r) => (r.account_id === selected ? "bg-accent-soft/50" : undefined)}
              search={{ placeholder: "Search", text: (r) => r.name }}
              defaultSort={{ id: "precedent", dir: "desc" }}
              defaultPageSize={10}
              noun="clients"
              csvName="renewal-patterns"
              empty={{ icon: GitCompareArrows, title: "No clients yet" }}
            />
          </div>

          <Card flush>
            <div className="flex items-center justify-between gap-3 border-b border-line px-5 pt-2">
              <Tabs value={tab} onChange={setTab} tabs={[{ value: "matters", label: "What matters" }, { value: "accuracy", label: "Accuracy" }]} />
              <HonestyLabel label={tab === "accuracy" && !Object.keys(o.evaluation?.stages ?? {}).length ? "untested" : model.label} />
            </div>
            <div key={tab} className="p-5 animate-fade-in">{tab === "matters" ? <WhatMatters stages={o.stages ?? []} features={o.features ?? []} /> : <Accuracy overview={o} />}</div>
          </Card>
        </>
      )}
    </div>
  );
}
