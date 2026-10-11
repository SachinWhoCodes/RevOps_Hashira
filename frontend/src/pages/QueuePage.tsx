import { useQuery } from "@tanstack/react-query";
import { ChevronRight, EyeOff, Sparkles, Target, TrendingUp } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { api, type Queue, type QueueItem } from "../api";
import { SignalChip } from "../components/domain";
import ScoreBreakdown from "../components/ScoreBreakdown";
import { Pagination } from "../components/table/Pagination";
import { Avatar, Badge, Card, EmptyState, ErrorNote, PageHeader, ScoreRing, SearchInput, Segmented, Select, Skeleton, Tooltip } from "../components/ui";
import { money, plural } from "../format";
import { cn } from "../lib/cn";
import { useUrlBatch, useUrlState } from "../lib/hooks";

type Kind = "risk" | "expansion";
type Sort = "priority" | "score" | "renewal" | "arr";

const confidenceTone = { high: "ok", medium: "warn", low: "risk" } as const;

function renewalPill(days: number | null) {
  if (days == null) return <span className="text-faint">Renewal unknown</span>;
  if (days < 0) return <span className="font-medium text-risk-fg">Overdue {-days}d</span>;
  return <span className={cn(days <= 30 ? "font-medium text-risk-fg" : days <= 90 ? "font-medium text-warn-fg" : "text-muted")}>Renews in {days}d</span>;
}

function CaseCard({ item, kind, index }: { item: QueueItem; kind: Kind; index: number }) {
  const score = kind === "risk" ? item.risk : item.opportunity;
  const shown = item.signals.filter((s) => (kind === "risk" ? s.polarity !== "opportunity" : s.polarity === "opportunity"));
  return (
    <li className="animate-fade-up" style={{ animationDelay: `${Math.min(index, 10) * 35}ms` }}>
      <div className="group relative rounded-xl border border-line bg-panel shadow-xs transition-[border-color,box-shadow] hover:border-line-strong hover:shadow-md">
        <div className="flex gap-4 p-4 sm:p-5">
          <div className="flex flex-col items-center gap-2">
            <span className="text-[11px] font-semibold text-faint tabular-nums">#{item.rank}</span>
            <ScoreRing value={score} kind={kind === "risk" ? "risk" : "opportunity"} size={48} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
              <div className="min-w-0">
                <Link to={`/accounts/${item.account.id}`} className="text-[15px] font-semibold tracking-[-0.01em] text-ink after:absolute after:inset-0 after:rounded-xl group-hover:text-accent-fg">
                  {item.account.name}
                </Link>
                <div className="mt-0.5 text-xs text-faint">{item.account.domains[0]}</div>
              </div>
              <div className="flex items-center gap-4 text-right">
                <div>
                  <div className="text-[14px] font-semibold tabular-nums text-ink">
                    {money(item.arr)}
                    {item.arr_assumed && <Tooltip content="ARR unknown: the median is used for ranking"><span className="ml-0.5 cursor-help text-faint">*</span></Tooltip>}
                  </div>
                  <div className="text-xs">{renewalPill(item.days_to_renewal)}</div>
                </div>
              </div>
            </div>
            {item.headline && <p className="mt-2 text-[14px] leading-relaxed text-ink">{item.headline}</p>}
            <div className="relative z-10 mt-3 flex w-fit flex-wrap gap-1.5">
              {shown.map((s, i) => <SignalChip key={i} s={s} />)}
              {item.blind_spots.length > 0 && (
                <Tooltip content={item.blind_spots.map((b) => b.text).join(" · ")}>
                  <Badge icon={EyeOff}>{plural(item.blind_spots.length, "blind spot")}</Badge>
                </Tooltip>
              )}
            </div>
            <div className="relative z-10 mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
              <div className="flex items-center gap-2 text-[12.5px] text-muted">
                <Avatar name={item.owner ?? "Unassigned"} size="xs" />
                {item.owner ?? "Unassigned"}
                <span className="text-line-strong">·</span>
                <Tooltip content={item.sources_missing.length ? `Missing data: ${item.sources_missing.join(", ")}` : "Every data source is present for this account"}>
                  <Badge tone={confidenceTone[item.confidence]} dot>
                    {item.confidence} confidence
                  </Badge>
                </Tooltip>
              </div>
              <div className="flex items-center gap-1">
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
                <ChevronRight className="size-4 text-faint transition-transform group-hover:translate-x-0.5" aria-hidden />
              </div>
            </div>
          </div>
        </div>
      </div>
    </li>
  );
}

export default function QueuePage() {
  const [kind] = useUrlState("kind", "risk") as [Kind, (v: string) => void];
  const [owner] = useUrlState("owner", "all");
  const [sort] = useUrlState("sort", "priority") as [Sort, (v: string) => void];
  const [q] = useUrlState("q", "");
  const [pageParam] = useUrlState("page", "1");
  const [sizeParam] = useUrlState("size", "10");
  const batch = useUrlBatch();
  const queue = useQuery({ queryKey: ["queue", kind, owner], queryFn: () => api<Queue>(`/queue?kind=${kind}&owner=${owner}`) });
  const other = useQuery({
    queryKey: ["queue", kind === "risk" ? "expansion" : "risk", owner],
    queryFn: () => api<Queue>(`/queue?kind=${kind === "risk" ? "expansion" : "risk"}&owner=${owner}`),
  });
  const data = queue.data;
  const counts = { [kind]: data?.items.length, [kind === "risk" ? "expansion" : "risk"]: other.data?.items.length } as Record<Kind, number | undefined>;

  const items = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = (data?.items ?? []).filter(
      (i) => !needle || i.account.name.toLowerCase().includes(needle) || (i.owner ?? "").toLowerCase().includes(needle) || (i.headline ?? "").toLowerCase().includes(needle),
    );
    const score = (i: QueueItem) => (kind === "risk" ? i.risk : i.opportunity);
    const by: Record<Sort, (a: QueueItem, b: QueueItem) => number> = {
      priority: (a, b) => a.rank - b.rank,
      score: (a, b) => score(b) - score(a),
      renewal: (a, b) => (a.days_to_renewal ?? 1e9) - (b.days_to_renewal ?? 1e9),
      arr: (a, b) => (b.arr ?? 0) - (a.arr ?? 0),
    };
    return [...list].sort(by[sort] ?? by.priority);
  }, [data, q, sort, kind]);

  const size = [10, 25, 50].includes(Number(sizeParam)) ? Number(sizeParam) : 10;
  const pageCount = Math.max(1, Math.ceil(items.length / size));
  const page = Math.min(Math.max(1, Number(pageParam) || 1), pageCount);
  const shown = items.slice((page - 1) * size, page * size);

  return (
    <>
      <PageHeader
        title="Queue"
        description={
          kind === "risk"
            ? "Open risk cases, ranked by risk × ARR × how soon each account renews. Start at the top."
            : "Accounts showing they want more: seat saturation, new teams adopting, or a request to buy."
        }
        actions={
          <>
            <Segmented<Kind>
              label="Case type"
              value={kind}
              onChange={(v) => batch({ kind: v === "risk" ? null : v, page: null, sort: null })}
              options={[
                { value: "risk", label: "Risk", icon: Target, count: counts.risk },
                { value: "expansion", label: "Expansion", icon: TrendingUp, count: counts.expansion },
              ]}
            />
            <Select selectSize="sm" value={owner} onChange={(e) => batch({ owner: e.target.value === "all" ? null : e.target.value, page: null })} aria-label="Owner" className="w-44">
              <option value="all">All owners</option>
              <option value="me">My accounts</option>
              {data?.owners.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </Select>
          </>
        }
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        {[
          { k: kind === "risk" ? "ARR at stake" : "ARR in scope", v: data ? money(data.revenue_at_stake) : null },
          { k: "Open cases", v: data ? String(data.items.length) : null },
          { k: "Low confidence", v: data ? String(data.items.filter((i) => i.confidence === "low").length) : null, hint: "Missing data sources: read these with care" },
        ].map((s, i) => (
          <div key={s.k} className="flex items-center justify-between rounded-xl border border-line bg-panel px-4 py-3 shadow-xs animate-fade-up" style={{ animationDelay: `${i * 50}ms` }}>
            <span className="text-[13px] text-muted" title={s.hint}>{s.k}</span>
            {s.v == null ? <Skeleton className="h-5 w-16" /> : <span className="text-[17px] font-semibold tracking-[-0.02em] tabular-nums">{s.v}</span>}
          </div>
        ))}
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <SearchInput value={q} onChange={(v) => batch({ q: v || null, page: null })} placeholder="Search accounts, owners, headlines" className="w-full sm:w-80" />
        <div className="ml-auto flex items-center gap-2 text-[13px] text-muted">
          Sort
          <Select selectSize="sm" value={sort} onChange={(e) => batch({ sort: e.target.value === "priority" ? null : e.target.value, page: null })} className="w-44" aria-label="Sort">
            <option value="priority">Priority (default)</option>
            <option value="score">{kind === "risk" ? "Risk score" : "Opportunity score"}</option>
            <option value="renewal">Soonest renewal</option>
            <option value="arr">Largest ARR</option>
          </Select>
        </div>
      </div>

      {queue.error && <ErrorNote error={queue.error} />}
      {queue.isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="flex gap-4 rounded-xl border border-line bg-panel p-5">
              <Skeleton className="size-12 rounded-full" />
              <div className="flex-1 space-y-2.5">
                <Skeleton className="h-4 w-48" />
                <Skeleton className="h-3.5 w-3/4" />
                <div className="flex gap-1.5"><Skeleton className="h-5 w-28" /><Skeleton className="h-5 w-24" /><Skeleton className="h-5 w-20" /></div>
              </div>
            </div>
          ))}
        </div>
      )}
      {data && !items.length && (
        <Card>
          <EmptyState
            icon={q ? undefined : Sparkles}
            title={q ? `No cases match “${q}”` : `No open ${kind} cases${owner !== "all" ? " for this owner" : ""}`}
            description={
              q
                ? "Try another search."
                : kind === "expansion"
                  ? "An expansion case needs two opportunity signals (or an explicit request to buy) and no open risk case."
                  : "Nothing in the book is over the case threshold right now."
            }
          />
        </Card>
      )}
      <ol className="space-y-3" key={`${kind}-${page}-${sort}`}>
        {shown.map((item, i) => <CaseCard key={item.case_id} item={item} kind={kind} index={i} />)}
      </ol>
      {items.length > 0 && (
        <div className="mt-3 rounded-xl border border-line bg-panel shadow-xs">
          <Pagination
            page={page}
            pageCount={pageCount}
            onPage={(p) => {
              batch({ page: p === 1 ? null : String(p) });
              window.scrollTo({ top: 0, behavior: "smooth" });
            }}
            total={items.length}
            filteredFrom={data?.items.length}
            pageSize={size}
            pageSizes={[10, 25, 50]}
            onPageSize={(n) => batch({ size: n === 10 ? null : String(n), page: null })}
            noun="cases"
          />
        </div>
      )}
    </>
  );
}
