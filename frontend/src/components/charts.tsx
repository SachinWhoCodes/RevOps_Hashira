import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "../lib/cn";
import { dateShort } from "../format";
import type { Tone } from "./ui";

// Width of an element, kept current with a ResizeObserver (charts draw at real pixels, never stretched).
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

// Round tick step for an axis maximum: 1, 2, 5 × 10^n.
function niceMax(max: number, ticks = 4): { max: number; step: number } {
  if (max <= 0) return { max: 1, step: 1 };
  const raw = max / ticks;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  return { max: Math.ceil(max / step) * step, step };
}

// Path for a column with a rounded data end (top) and a square baseline.
function column(x: number, y: number, w: number, h: number, r = 4) {
  if (h <= 0) return "";
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

function ChartTooltip({ x, children, width }: { x: number; children: ReactNode; width: number }) {
  const left = Math.min(Math.max(x, 80), width - 80);
  return (
    <div
      className="pointer-events-none absolute top-1 z-10 -translate-x-1/2 rounded-lg border border-line bg-panel px-2.5 py-1.5 text-xs whitespace-nowrap shadow-md"
      style={{ left }}
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------- usage: weekly active users vs seats

export function UsageChart({ weeks, seats }: { weeks: { week: string; active_users_avg: number }[]; seats: number | null | undefined }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const shown = weeks.slice(-26);
  if (!shown.length) return <p className="py-6 text-center text-sm text-faint">No usage data for this account — a blind spot.</p>;
  const h = 150;
  const padL = 30;
  const padB = 20;
  const { max, step } = niceMax(Math.max(seats ?? 0, ...shown.map((w) => w.active_users_avg), 1));
  const plotW = Math.max(0, width - padL);
  const band = plotW / shown.length;
  const barW = Math.min(18, Math.max(2, band - 2));
  const y = (v: number) => (h - padB) * (1 - v / max) + 4;
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step);
  const hw = hover != null ? shown[hover] : null;
  return (
    <figure>
      <div ref={ref} className="relative" onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg width={width} height={h + 4} role="img" aria-label={`Average daily active users per week, last ${shown.length} weeks`}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={padL} x2={width} y1={y(t)} y2={y(t)} className="stroke-line" strokeWidth={1} />
                <text x={padL - 6} y={y(t) + 3.5} textAnchor="end" className="fill-faint text-[10px] tabular-nums">{t}</text>
              </g>
            ))}
            {shown.map((wk, i) => {
              const x = padL + i * band + (band - barW) / 2;
              const top = y(wk.active_users_avg);
              return (
                <g key={wk.week} onMouseEnter={() => setHover(i)}>
                  <rect x={padL + i * band} y={0} width={band} height={h - padB + 4} fill="transparent" />
                  <path
                    d={column(x, top, barW, y(0) - top)}
                    className={cn("transition-opacity", hover == null || hover === i ? "fill-accent" : "fill-accent opacity-40")}
                  />
                </g>
              );
            })}
            {seats ? (
              <g>
                <line x1={padL} x2={width} y1={y(seats)} y2={y(seats)} className="stroke-ink/50" strokeWidth={1.5} strokeDasharray="5 4" />
                <text x={width - 2} y={y(seats) - 5} textAnchor="end" className="fill-muted text-[10.5px] font-medium">{seats} seats</text>
              </g>
            ) : null}
            <text x={padL} y={h} className="fill-faint text-[10px]">{dateShort(shown[0].week)}</text>
            <text x={width} y={h} textAnchor="end" className="fill-faint text-[10px]">{dateShort(shown[shown.length - 1].week)}</text>
          </svg>
        )}
        {hw && hover != null && (
          <ChartTooltip x={padL + hover * band + band / 2} width={width}>
            <div className="text-faint">Week of {dateShort(hw.week)}</div>
            <div className="font-medium text-ink tabular-nums">{hw.active_users_avg} active users / day</div>
          </ChartTooltip>
        )}
      </div>
      <figcaption className="mt-2 text-xs text-muted">Average daily active users per week · last {shown.length} weeks{seats ? " · dashed line: seats purchased" : ""}</figcaption>
    </figure>
  );
}

// ---------------------------------------------------------------- replay: risk score over time

export function RiskTimeline({
  points,
  flagAt,
  outcomes,
  selected,
  onSelect,
}: {
  points: { date: string; risk: number; flagged: boolean; signals: string[] }[];
  flagAt: number;
  outcomes: { renewal_date: string; outcome: string }[];
  selected: number;
  onSelect: (i: number) => void;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const h = 240;
  const padL = 30;
  const padT = 22;
  const padB = 22;
  const n = points.length;
  const x = (i: number) => padL + (n > 1 ? (i / (n - 1)) * (width - padL - 8) : (width - padL) / 2);
  const y = (v: number) => padT + (h - padT - padB) * (1 - v / 100);
  const first = new Date(points[0].date).getTime();
  const last = new Date(points[n - 1].date).getTime();
  const xDate = (d: string) => padL + ((new Date(d).getTime() - first) / Math.max(last - first, 1)) * (width - padL - 8);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.risk).toFixed(1)}`).join("");
  const area = `${line}L${x(n - 1).toFixed(1)},${y(0)}L${x(0).toFixed(1)},${y(0)}Z`;
  const shownOutcomes = outcomes.filter((o) => o.renewal_date >= points[0].date && o.renewal_date <= points[n - 1].date);
  const active = hover ?? selected;
  const ap = points[active];

  const pick = (clientX: number, rect: DOMRect) => {
    const rel = (clientX - rect.left - padL) / Math.max(width - padL - 8, 1);
    return Math.min(n - 1, Math.max(0, Math.round(rel * (n - 1))));
  };

  return (
    <div ref={ref} className="relative select-none">
      {width > 0 && (
        <svg
          width={width}
          height={h}
          role="img"
          aria-label="Risk score each week, as it would have been computed on that date"
          className="cursor-crosshair"
          onMouseMove={(e) => setHover(pick(e.clientX, e.currentTarget.getBoundingClientRect()))}
          onMouseLeave={() => setHover(null)}
          onClick={(e) => onSelect(pick(e.clientX, e.currentTarget.getBoundingClientRect()))}
        >
          <defs>
            <linearGradient id="risk-wash" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" className="[stop-color:var(--c-accent)]" stopOpacity={0.18} />
              <stop offset="1" className="[stop-color:var(--c-accent)]" stopOpacity={0} />
            </linearGradient>
          </defs>
          {[0, 25, 50, 75, 100].map((t) => (
            <g key={t}>
              <line x1={padL} x2={width - 8} y1={y(t)} y2={y(t)} className="stroke-line" strokeWidth={1} />
              <text x={padL - 6} y={y(t) + 3.5} textAnchor="end" className="fill-faint text-[10px] tabular-nums">{t}</text>
            </g>
          ))}
          <rect x={padL} y={padT} width={width - padL - 8} height={y(flagAt) - padT} className="fill-risk/[0.05]" />
          <line x1={padL} x2={width - 8} y1={y(flagAt)} y2={y(flagAt)} className="stroke-risk/70" strokeWidth={1.5} strokeDasharray="5 4" />
          <text x={width - 10} y={y(flagAt) - 6} textAnchor="end" className="fill-risk-fg text-[10.5px] font-medium">case opens at {flagAt}</text>
          {shownOutcomes.map((o) => {
            const lost = o.outcome === "churned" || o.outcome === "downgraded";
            return (
              <g key={o.renewal_date}>
                <line x1={xDate(o.renewal_date)} x2={xDate(o.renewal_date)} y1={padT - 4} y2={y(0)} className={lost ? "stroke-risk" : "stroke-ok"} strokeWidth={1.5} />
                <text x={xDate(o.renewal_date)} y={11} textAnchor="middle" className="fill-muted text-[10.5px] font-medium">{o.outcome}</text>
              </g>
            );
          })}
          <path d={area} fill="url(#risk-wash)" />
          <path d={line} fill="none" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" className="stroke-accent" />
          {hover != null && <line x1={x(hover)} x2={x(hover)} y1={padT} y2={y(0)} className="stroke-ink/25" strokeWidth={1} />}
          <circle cx={x(selected)} cy={y(points[selected].risk)} r={6} strokeWidth={2} className={cn(points[selected].flagged ? "fill-risk" : "fill-accent", "stroke-panel")} />
          {hover != null && hover !== selected && (
            <circle cx={x(hover)} cy={y(points[hover].risk)} r={4.5} strokeWidth={2} className={cn(points[hover].flagged ? "fill-risk" : "fill-accent", "stroke-panel")} />
          )}
          <text x={padL} y={h - 4} className="fill-faint text-[10px]">{dateShort(points[0].date)}</text>
          <text x={width - 8} y={h - 4} textAnchor="end" className="fill-faint text-[10px]">{dateShort(points[n - 1].date)}</text>
        </svg>
      )}
      {hover != null && ap && width > 0 && (
        <ChartTooltip x={x(active)} width={width}>
          <div className="text-faint">{dateShort(ap.date)}</div>
          <div className="font-medium text-ink">
            Risk <span className="tabular-nums">{ap.risk}</span>
            {ap.flagged && <span className="ml-1.5 text-risk-fg">· case open</span>}
          </div>
          {ap.signals.length > 0 && <div className="max-w-[240px] truncate text-muted">{ap.signals.map((s) => s.replace(/_/g, " ")).join(", ")}</div>}
        </ChartTooltip>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- bar list (ranked magnitudes)

const barTone: Record<Tone, string> = { neutral: "bg-faint/40", accent: "bg-accent", risk: "bg-risk", warn: "bg-warn", ok: "bg-ok" };

export function BarList({
  items,
  format = (n) => String(n),
  tone = "accent",
  empty = "Nothing to show yet.",
}: {
  items: { key: string; label: ReactNode; value: number; to?: string; icon?: ReactNode; sub?: ReactNode }[];
  format?: (n: number) => string;
  tone?: Tone;
  empty?: string;
}) {
  if (!items.length) return <p className="py-6 text-center text-sm text-faint">{empty}</p>;
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <ul className="space-y-3">
      {items.map((it, idx) => {
        const row = (
          <>
            <div className="flex items-center justify-between gap-3 text-[13px]">
              <span className="flex min-w-0 items-center gap-2 text-ink">
                {it.icon}
                <span className="truncate">{it.label}</span>
                {it.sub && <span className="truncate text-faint">{it.sub}</span>}
              </span>
              <span className="shrink-0 font-medium tabular-nums text-ink">{format(it.value)}</span>
            </div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-hover">
              <div
                className={cn("h-full origin-left rounded-full animate-grow-x", barTone[tone])}
                style={{ width: `${(it.value / max) * 100}%`, animationDelay: `${idx * 50}ms` }}
              />
            </div>
          </>
        );
        return (
          <li key={it.key}>
            {it.to ? (
              <Link to={it.to} className="-mx-2 block rounded-lg px-2 py-1 transition-colors hover:bg-hover">
                {row}
              </Link>
            ) : (
              row
            )}
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------- stacked status bar (with 2px surface gaps)

export function StackedBar({ segments, className }: { segments: { key: string; value: number; tone: Tone; label: string }[]; className?: string }) {
  const total = segments.reduce((n, s) => n + s.value, 0);
  if (!total) return <div className={cn("h-1.5 rounded-full bg-hover", className)} />;
  return (
    <div className={cn("flex h-1.5 gap-[2px] overflow-hidden rounded-full", className)} role="img" aria-label={segments.filter((s) => s.value).map((s) => `${s.value} ${s.label}`).join(", ")}>
      {segments
        .filter((s) => s.value > 0)
        .map((s) => (
          <div key={s.key} title={`${s.value} ${s.label}`} className={cn("h-full first:rounded-l-full last:rounded-r-full", barTone[s.tone])} style={{ flexGrow: s.value, flexBasis: 0 }} />
        ))}
    </div>
  );
}

// ---------------------------------------------------------------- comparison with a 95% range (back-test)

export function RangeBars({
  rows,
  format = (v) => `${Math.round(v * 100)}%`,
  max = 1,
}: {
  rows: { key: string; label: ReactNode; value: number | null; ci: [number, number] | null; n?: number; emphasis?: boolean }[];
  format?: (v: number) => string;
  max?: number;
}) {
  return (
    <ul className="space-y-4">
      {rows.map((r, idx) => (
        <li key={r.key}>
          <div className="flex items-baseline justify-between gap-3 text-[13px]">
            <span className={cn("min-w-0 truncate", r.emphasis ? "font-medium text-ink" : "text-muted")}>{r.label}</span>
            <span className="shrink-0 tabular-nums">
              <span className={cn("font-semibold", r.emphasis ? "text-ink" : "text-muted")}>{r.value == null ? "—" : format(r.value)}</span>
              {r.ci && <span className="ml-1.5 text-xs text-faint">{format(r.ci[0])}–{format(r.ci[1])}</span>}
              {r.n !== undefined && <span className="ml-1.5 text-[11px] text-faint">n={r.n}</span>}
            </span>
          </div>
          <div className="relative mt-2 h-2.5">
            <div className="absolute inset-y-[3px] right-0 left-0 rounded-full bg-hover" />
            {r.value != null && (
              <div
                className={cn("absolute inset-y-0 left-0 origin-left rounded-full animate-grow-x", r.emphasis ? "bg-accent" : "bg-faint/50")}
                style={{ width: `${(r.value / max) * 100}%`, animationDelay: `${idx * 70}ms` }}
              />
            )}
            {r.ci && (
              <div
                className="absolute top-1/2 h-px -translate-y-1/2 bg-ink/60"
                style={{ left: `${(r.ci[0] / max) * 100}%`, width: `${((r.ci[1] - r.ci[0]) / max) * 100}%` }}
              >
                <span className="absolute -top-[4px] left-0 h-[9px] w-px bg-ink/60" />
                <span className="absolute -top-[4px] right-0 h-[9px] w-px bg-ink/60" />
              </div>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
