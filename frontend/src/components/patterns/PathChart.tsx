import { useMemo, useState } from "react";
import type { PatternClient, PatternFeature, PatternStage, PatternUnit } from "../../api";
import { cn } from "../../lib/cn";
import { ChartTip, LineKey, fmtValue, useWidth } from "./shared";

type Pt = { d: number; v: number };
type Band = { d: number; lo: number; hi: number; med: number };

const STAGES = [360, 330, 300, 270, 240, 210, 180, 150, 120, 90, 60, 30];

function domain(unit: PatternUnit, values: number[]): [number, number, number[]] {
  if (unit === "score") return [0, 100, [0, 25, 50, 75, 100]];
  if (unit === "days") return [0, 180, [0, 60, 120, 180]];
  if (unit === "pct") {
    const m = Math.max(0.25, ...values.map(Math.abs));
    const lim = m > 0.5 ? 1 : 0.5;
    return [-lim, lim, lim === 1 ? [-1, -0.5, 0, 0.5, 1] : [-0.5, -0.25, 0, 0.25, 0.5]];
  }
  if (unit === "share") {
    const max = Math.max(1, ...values);
    return [0, max > 1 ? 1.5 : 1, max > 1 ? [0, 0.5, 1, 1.5] : [0, 0.25, 0.5, 0.75, 1]];
  }
  const max = Math.max(4, ...values);
  const step = max > 20 ? 10 : max > 8 ? 4 : 2;
  const top = Math.ceil(max / step) * step;
  return [0, top, Array.from({ length: top / step + 1 }, (_, i) => i * step)];
}

const linePath = (pts: Pt[], x: (d: number) => number, y: (v: number) => number) =>
  pts.map((p, i) => `${i ? "L" : "M"}${x(p.d).toFixed(1)},${y(p.v).toFixed(1)}`).join("");

const bandPath = (b: Band[], x: (d: number) => number, y: (v: number) => number) =>
  b.length < 2
    ? ""
    : `${b.map((p, i) => `${i ? "L" : "M"}${x(p.d).toFixed(1)},${y(p.hi).toFixed(1)}`).join("")}${[...b]
        .reverse()
        .map((p) => `L${x(p.d).toFixed(1)},${y(p.lo).toFixed(1)}`)
        .join("")}Z`;

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
};

// Where a group of paths ran at each checkpoint: lowest to highest, and the middle one.
function envelope(paths: Pt[][]): Band[] {
  return STAGES.flatMap((d) => {
    const vs = paths.map((p) => p.find((q) => q.d === d)?.v).filter((v): v is number => v != null);
    return vs.length ? [{ d, lo: Math.min(...vs), hi: Math.max(...vs), med: median(vs) }] : [];
  });
}

// Paths to the decision: this client's path so far against the finished cycles most like it, lined up on
// "days before the decision". To the right of "now" the past cycles show where paths like this went next.
export function PathChart({
  client,
  stages,
  feature,
  highlight,
  onHighlight,
}: {
  client: PatternClient;
  stages: PatternStage[];
  feature: PatternFeature;
  highlight?: string | null;
  onHighlight?: (id: string | null) => void;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const key = feature.key;
  const fmt = (v: number | null | undefined) => fmtValue(v, feature.unit, { mean: true });

  const mine: Pt[] = useMemo(() => {
    const pts = client.trajectory.map((p) => ({ d: p.days, v: p.f[key] })).filter((p): p is Pt => p.v != null);
    const now = client.now?.f[key];
    if (client.now && now != null) pts.push({ d: Math.max(0, client.now.days), v: now });
    return pts.sort((a, b) => b.d - a.d);
  }, [client, key]);

  const neighbours = useMemo(
    () =>
      client.neighbours.map((n) => ({
        id: n.cycle_id,
        name: n.cycle.name,
        lost: n.cycle.lost,
        pts: n.cycle.points.map((p) => ({ d: p.days, v: p.f[key] })).filter((p): p is Pt => p.v != null).sort((a, b) => b.d - a.d),
      })),
    [client, key],
  );
  const lostBand = useMemo(() => envelope(neighbours.filter((n) => n.lost).map((n) => n.pts)), [neighbours]);
  const keptBand = useMemo(() => envelope(neighbours.filter((n) => !n.lost).map((n) => n.pts)), [neighbours]);

  const typical = useMemo(() => {
    const kept: Pt[] = [];
    const lost: Pt[] = [];
    for (const s of [...stages].sort((a, b) => b.stage - a.stage)) {
      const k = s.means.kept?.[key];
      const l = s.means.lost?.[key];
      if (k != null) kept.push({ d: s.stage, v: k });
      if (l != null) lost.push({ d: s.stage, v: l });
    }
    return { kept, lost };
  }, [stages, key]);

  const all = [...mine, ...neighbours.flatMap((n) => n.pts), ...typical.kept, ...typical.lost].map((p) => p.v);
  const [lo, hi, ticks] = domain(feature.unit, all);
  const compact = width < 620;
  const h = compact ? 300 : 340;
  const padL = 44;
  const padR = compact ? 16 : 132;
  const padT = 14;
  const padB = 46;
  const plotR = width - padR;
  const plotW = Math.max(1, plotR - padL);
  const maxD = 360;
  const x = (d: number) => padL + (1 - Math.min(d, maxD) / maxD) * plotW;
  const y = (v: number) => padT + (h - padT - padB) * (1 - (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo));
  const bottom = h - padB;
  const nowD = client.now ? Math.max(0, client.now.days) : null;
  const pathStages = client.stage != null && client.mix ? [client.stage, client.stage + 30, client.stage + 60].filter((d) => d <= 360) : [];

  const at = (pts: Pt[], d: number) => pts.find((p) => p.d === d)?.v ?? null;
  const clientAt = (d: number) => at(mine, d) ?? (nowD != null && Math.abs(nowD - d) <= 15 ? at(mine, nowD) : null);
  const rows = STAGES.map((d) => ({
    d,
    client: clientAt(d),
    lost: lostBand.find((b) => b.d === d) ?? null,
    kept: keptBand.find((b) => b.d === d) ?? null,
    typicalKept: at(typical.kept, d),
    typicalLost: at(typical.lost, d),
  }));
  const hr = hover != null ? rows[hover] : null;

  // Direct labels in the right margin, each tied to its series end by a leader line (never nudged loose).
  const ends: { key: string; y: number; from: [number, number]; text: string; value: string; cls: string }[] = [];
  const last = mine[mine.length - 1];
  if (last) ends.push({ key: "me", y: y(last.v), from: [x(last.d), y(last.v)], text: client.name, value: fmtValue(last.v, feature.unit), cls: "fill-accent" });
  const lostEnd = lostBand[lostBand.length - 1];
  if (lostEnd) ends.push({ key: "lost", y: y(lostEnd.med), from: [x(lostEnd.d), y(lostEnd.med)], text: "similar · lost", value: fmt(lostEnd.med), cls: "fill-risk" });
  const keptEnd = keptBand[keptBand.length - 1];
  if (keptEnd) ends.push({ key: "kept", y: y(keptEnd.med), from: [x(keptEnd.d), y(keptEnd.med)], text: "similar · renewed", value: fmt(keptEnd.med), cls: "fill-ok" });
  if (!neighbours.length) {
    const tk = typical.kept[typical.kept.length - 1];
    const tl = typical.lost[typical.lost.length - 1];
    if (tk) ends.push({ key: "tk", y: y(tk.v), from: [x(tk.d), y(tk.v)], text: "typical renewed", value: fmt(tk.v), cls: "fill-ok" });
    if (tl) ends.push({ key: "tl", y: y(tl.v), from: [x(tl.d), y(tl.v)], text: "typical lost", value: fmt(tl.v), cls: "fill-risk" });
  }
  ends.sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) ends[i].y = Math.max(ends[i].y, ends[i - 1].y + 26);
  const overflow = ends.length ? ends[ends.length - 1].y - (bottom - 6) : 0;
  if (overflow > 0) ends.forEach((e) => (e.y -= overflow));

  const futureW = nowD != null ? x(0) - x(nowD) : 0;

  return (
    <figure>
      <div ref={ref} className="relative select-none" onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg
            key={`${client.account_id}${key}`}
            width={width}
            height={h}
            role="img"
            aria-label={`${feature.label}: ${client.name}'s path against ${neighbours.length} similar past cycles, by days before the decision`}
            className="cursor-crosshair"
            onMouseMove={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const d = (1 - (e.clientX - rect.left - padL) / plotW) * maxD;
              if (d < -20 || e.clientX - rect.left > plotR + 4) return setHover(null);
              setHover(STAGES.reduce((best, s, idx) => (Math.abs(s - d) < Math.abs(STAGES[best] - d) ? idx : best), 0));
            }}
          >
            <defs>
              <clipPath id="path-plot">
                <rect x={padL} y={padT - 2} width={plotW + 2} height={bottom - padT + 4} />
              </clipPath>
            </defs>
            {/* zones: the past this client has walked, the stretch still ahead */}
            {nowD != null && (
              <rect x={x(nowD)} y={padT} width={Math.max(0, futureW)} height={bottom - padT} className="fill-hover animate-fade-in" />
            )}
            {ticks.map((t) => (
              <g key={t}>
                <line x1={padL} x2={plotR} y1={y(t)} y2={y(t)} className={t === 0 && feature.unit === "pct" ? "stroke-line-strong" : "stroke-line"} strokeWidth={1} />
                <text x={padL - 8} y={y(t) + 3.5} textAnchor="end" className="fill-faint text-[10px] tabular-nums">{fmtValue(t, feature.unit)}</text>
              </g>
            ))}
            {[360, 270, 180, 90].map((d) => (
              <text key={d} x={x(d)} y={bottom + 16} textAnchor={d === 360 ? "start" : "middle"} className="fill-faint text-[10px] tabular-nums">
                −{d} d
              </text>
            ))}
            <line x1={x(0)} x2={x(0)} y1={padT} y2={bottom} className="stroke-ink/50" strokeWidth={1.5} />
            <text x={x(0)} y={bottom + 16} textAnchor="end" className="fill-ink text-[10.5px] font-semibold">Decision</text>
            {/* the three checkpoints the matching compared */}
            {pathStages.map((d, i) => (
              <g key={d} className="animate-fade-in" style={{ animationDelay: `${1700 + i * 120}ms` }}>
                <line x1={x(d)} x2={x(d)} y1={bottom} y2={bottom + 5} className="stroke-accent" strokeWidth={2} />
                <circle cx={x(d)} cy={bottom} r={3} className="fill-accent" />
              </g>
            ))}

            <g clipPath="url(#path-plot)">
              {/* every finished cycle, as two quiet references */}
              {typical.kept.length > 1 && <path d={linePath(typical.kept, x, y)} fill="none" strokeWidth={8} strokeLinecap="round" strokeLinejoin="round" className="stroke-ok/15" />}
              {typical.lost.length > 1 && <path d={linePath(typical.lost, x, y)} fill="none" strokeWidth={8} strokeLinecap="round" strokeLinejoin="round" className="stroke-risk/15" />}
              {/* where the similar cycles ran: their range as a wash, each one as a faint line */}
              {lostBand.length > 1 && <path d={bandPath(lostBand, x, y)} className="fill-risk/10 animate-fade-in" style={{ animationDelay: "900ms" }} />}
              {keptBand.length > 1 && <path d={bandPath(keptBand, x, y)} className="fill-ok/10 animate-fade-in" style={{ animationDelay: "900ms" }} />}
              {neighbours.map((n, i) =>
                n.pts.length > 1 ? (
                  <path
                    key={n.id}
                    d={linePath(n.pts, x, y)}
                    pathLength={1}
                    strokeDasharray={1}
                    fill="none"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={highlight === n.id ? 2.5 : 1.25}
                    className={cn(
                      "animate-draw transition-[opacity,stroke-width]",
                      n.lost ? "stroke-risk" : "stroke-ok",
                      highlight ? (highlight === n.id ? "opacity-100" : "opacity-15") : "opacity-35",
                    )}
                    style={{ animationDelay: `${200 + i * 70}ms` }}
                    onMouseEnter={() => onHighlight?.(n.id)}
                    onMouseLeave={() => onHighlight?.(null)}
                  />
                ) : null,
              )}
              {[lostBand, keptBand].map((b, i) =>
                b.length > 1 ? (
                  <path
                    key={i}
                    d={linePath(b.map((p) => ({ d: p.d, v: p.med })), x, y)}
                    pathLength={1}
                    strokeDasharray={1}
                    fill="none"
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    className={cn("animate-draw", i === 0 ? "stroke-risk" : "stroke-ok", highlight && "opacity-30")}
                    style={{ animationDelay: "1000ms" }}
                  />
                ) : null,
              )}
              {/* this client */}
              {nowD != null && <line x1={x(nowD)} x2={x(nowD)} y1={padT} y2={bottom} className="stroke-accent/50" strokeWidth={1} />}
              {mine.length > 1 && (
                <>
                  <path d={linePath(mine, x, y)} fill="none" strokeWidth={6} strokeLinecap="round" strokeLinejoin="round" className="stroke-panel" />
                  <path
                    d={linePath(mine, x, y)}
                    pathLength={1}
                    strokeDasharray={1}
                    fill="none"
                    strokeWidth={2.5}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    className="stroke-accent animate-draw"
                    style={{ animationDelay: "1300ms", animationDuration: "1.4s" }}
                  />
                  {/* a dot that walks the client's path once, then rests at "now" */}
                  <circle r={4.5} opacity={0} className="fill-accent">
                    <set attributeName="opacity" to="1" begin="1.3s" fill="freeze" />
                    <animateMotion dur="1.4s" begin="1.3s" fill="freeze" path={linePath(mine, x, y)} calcMode="spline" keyTimes="0;1" keySplines="0.16 1 0.3 1" />
                    <set attributeName="opacity" to="0" begin="2.75s" fill="freeze" />
                  </circle>
                </>
              )}
              {mine.map((p, i) => (
                <circle key={i} cx={x(p.d)} cy={y(p.v)} r={i === mine.length - 1 ? 5.5 : 3} strokeWidth={2} className="fill-accent stroke-panel animate-pop [transform-box:fill-box] origin-center" style={{ animationDelay: `${1400 + i * 90}ms` }} />
              ))}
              {last && <circle cx={x(last.d)} cy={y(last.v)} r={5.5} className="fill-accent animate-breathe [transform-box:fill-box] origin-center" style={{ animationDelay: "2700ms" }} />}
            </g>
            {nowD != null && (
              <g className="animate-fade-in" style={{ animationDelay: "2400ms" }}>
                <rect x={x(nowD) - 34} y={bottom - 20} width={68} height={16} rx={8} className="fill-accent" />
                <text x={x(nowD)} y={bottom - 9} textAnchor="middle" className="fill-white text-[10px] font-semibold">
                  now · {nowD} d
                </text>
              </g>
            )}
            {/* direct labels */}
            {!compact &&
              ends.map((e, i) => (
                <g key={e.key} className="animate-fade-in" style={{ animationDelay: `${2100 + i * 120}ms` }}>
                  <path d={`M${e.from[0] + 7},${e.from[1]} L${plotR + 6},${e.y} L${plotR + 12},${e.y}`} fill="none" className="stroke-line-strong" strokeWidth={1} />
                  <circle cx={plotR + 17} cy={e.y} r={3.5} className={e.cls} />
                  <text x={plotR + 25} y={e.y - 2} className="fill-ink text-[11px] font-semibold tabular-nums">{e.value}</text>
                  <text x={plotR + 25} y={e.y + 10} className="fill-muted text-[10px]">{e.text.length > 16 ? `${e.text.slice(0, 15)}…` : e.text}</text>
                </g>
              ))}
            {hr && <line x1={x(hr.d)} x2={x(hr.d)} y1={padT} y2={bottom} className="stroke-ink/30" strokeWidth={1} />}
          </svg>
        )}
        {hr && width > 0 && (
          <ChartTip x={x(hr.d)} width={width} top={padT + 6}>
            <div className="text-faint">{hr.d} days before the decision</div>
            <div className="mt-1 space-y-0.5 tabular-nums">
              <div className="flex items-center gap-2"><LineKey className="stroke-accent" thick /><span className="font-semibold text-ink">{fmtValue(hr.client, feature.unit)}</span><span className="text-muted">{client.name}</span></div>
              {hr.lost && <div className="flex items-center gap-2"><LineKey className="stroke-risk" /><span className="font-semibold text-ink">{fmt(hr.lost.med)}</span><span className="text-muted">similar lost cycles (range {fmt(hr.lost.lo)}–{fmt(hr.lost.hi)})</span></div>}
              {hr.kept && <div className="flex items-center gap-2"><LineKey className="stroke-ok" /><span className="font-semibold text-ink">{fmt(hr.kept.med)}</span><span className="text-muted">similar renewed cycles (range {fmt(hr.kept.lo)}–{fmt(hr.kept.hi)})</span></div>}
              <div className="flex items-center gap-2 text-faint"><LineKey className="stroke-faint/50" thick /><span>every finished cycle: renewed {fmt(hr.typicalKept)} · lost {fmt(hr.typicalLost)}</span></div>
            </div>
          </ChartTip>
        )}
      </div>
      <figcaption className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        <span className="inline-flex items-center gap-1.5"><LineKey className="stroke-accent" thick />{client.name}</span>
        {lostBand.length > 0 && <span className="inline-flex items-center gap-1.5"><LineKey className="stroke-risk" />similar · lost</span>}
        {keptBand.length > 0 && <span className="inline-flex items-center gap-1.5"><LineKey className="stroke-ok" />similar · renewed</span>}
        <span className="inline-flex items-center gap-1.5"><LineKey className="stroke-faint/40" thick />all cycles</span>
      </figcaption>
      <details className="mt-2 text-xs">
        <summary className="text-faint hover:text-ink">Table</summary>
        <div className="mt-2 overflow-auto rounded-lg border border-line">
          <table className="w-full text-left tabular-nums">
            <thead className="bg-subtle text-faint">
              <tr>
                <th className="px-2 py-1 font-medium">Days before</th><th className="px-2 py-1 font-medium">{client.name}</th><th className="px-2 py-1 font-medium">Similar · lost (median)</th>
                <th className="px-2 py-1 font-medium">Similar · renewed (median)</th><th className="px-2 py-1 font-medium">All renewed (avg)</th><th className="px-2 py-1 font-medium">All lost (avg)</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.d} className="border-t border-line">
                  <td className="px-2 py-1 text-muted">{r.d}</td><td className="px-2 py-1">{fmtValue(r.client, feature.unit)}</td><td className="px-2 py-1">{fmt(r.lost?.med)}</td>
                  <td className="px-2 py-1">{fmt(r.kept?.med)}</td><td className="px-2 py-1">{fmt(r.typicalKept)}</td><td className="px-2 py-1">{fmt(r.typicalLost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
