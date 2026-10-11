import { useMemo, useState, type CSSProperties } from "react";
import { useNavigate } from "react-router-dom";
import type { PatternMapPoint, PatternMapView } from "../../api";
import { date } from "../../format";
import { cn } from "../../lib/cn";
import { ChartTip, DotKey, directionWords, outcomeText, useWidth, verdictText } from "./shared";

const DAY = 86_400_000;

type P = { x: number; y: number };

// A 1.6-standard-deviation ellipse around a group of points: where most of that group sits.
function halo(points: P[]): { cx: number; cy: number; rx: number; ry: number; angle: number } | null {
  if (points.length < 4) return null;
  const n = points.length;
  const cx = points.reduce((s, p) => s + p.x, 0) / n;
  const cy = points.reduce((s, p) => s + p.y, 0) / n;
  let sxx = 0;
  let syy = 0;
  let sxy = 0;
  for (const p of points) {
    sxx += (p.x - cx) ** 2;
    syy += (p.y - cy) ** 2;
    sxy += (p.x - cx) * (p.y - cy);
  }
  sxx /= n;
  syy /= n;
  sxy /= n;
  const tr = sxx + syy;
  const det = sxx * syy - sxy * sxy;
  const l1 = tr / 2 + Math.sqrt(Math.max(0, (tr * tr) / 4 - det));
  const l2 = tr / 2 - Math.sqrt(Math.max(0, (tr * tr) / 4 - det));
  const angle = (Math.atan2(l1 - sxx, sxy || 1e-9) * 180) / Math.PI;
  return { cx, cy, rx: 1.6 * Math.sqrt(Math.max(l1, 1e-6)), ry: 1.6 * Math.sqrt(Math.max(l2, 1e-6)), angle };
}

// Every finished cycle and every current client at one stage, placed so that alike patterns sit close
// together (principal components of the same weighted numbers the matching uses; the axes have no units).
export function PatternMap({
  map,
  selected,
  neighbourIds,
  onSelect,
}: {
  map: PatternMapView;
  selected: string | null;
  neighbourIds: Set<string>;
  onSelect: (accountId: string) => void;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<PatternMapPoint | null>(null);
  const navigate = useNavigate();
  const h = Math.max(320, Math.min(420, Math.round(width * 0.78)));
  const pad = { l: 30, r: 16, t: 16, b: 34 };
  const span = (v: number[]) => {
    const lo = Math.min(...v);
    const hi = Math.max(...v);
    const m = (hi - lo || 1) * 0.1;
    return [lo - m, hi + m] as const;
  };
  const xs = map.points.map((p) => p.x);
  const ys = map.points.map((p) => p.y);
  const [x0, x1] = span(xs.length ? xs : [0]);
  const [y0, y1] = span(ys.length ? ys : [0]);
  const sx = (v: number) => pad.l + ((v - x0) / (x1 - x0)) * (width - pad.l - pad.r);
  const sy = (v: number) => h - pad.b - ((v - y0) / (y1 - y0)) * (h - pad.t - pad.b);
  const me = map.points.find((p) => p.kind === "client" && p.account_id === selected);
  const cycles = map.points.filter((p): p is Extract<PatternMapPoint, { kind: "cycle" }> => p.kind === "cycle");
  const clients = map.points.filter((p) => p.kind === "client");
  const centre = { x: (pad.l + width - pad.r) / 2, y: (pad.t + h - pad.b) / 2 };

  // Where each kind of finished cycle gathers, in screen coordinates.
  const halos = useMemo(() => {
    if (!width) return [];
    const w = width - pad.l - pad.r;
    const ht = h - pad.t - pad.b;
    const toX = (v: number) => pad.l + ((v - x0) / (x1 - x0)) * w;
    const toY = (v: number) => h - pad.b - ((v - y0) / (y1 - y0)) * ht;
    return [false, true].flatMap((lost) => {
      const pts = map.points
        .filter((p) => p.kind === "cycle" && p.lost === lost)
        .map((p) => ({ x: toX(p.x), y: toY(p.y) }));
      const e = halo(pts);
      return e ? [{ ...e, lost, n: pts.length }] : [];
    });
  }, [map, width, h, x0, x1, y0, y1, pad.l, pad.r, pad.t, pad.b]);

  const nearest = (mx: number, my: number) => {
    let best: PatternMapPoint | null = null;
    let dist = 16 * 16;
    for (const p of map.points) {
      const d = (sx(p.x) - mx) ** 2 + (sy(p.y) - my) ** 2;
      if (d < dist) {
        dist = d;
        best = p;
      }
    }
    return best;
  };

  const open = (p: PatternMapPoint) => {
    if (p.kind === "client") onSelect(p.account_id);
    else {
      const asOf = new Date(new Date(p.date).getTime() - map.stage * DAY).toISOString().slice(0, 10);
      navigate(`/accounts/${p.account_id}?as_of=${asOf}`);
    }
  };

  // What moving along an axis means, in words, from the numbers that load on it most.
  const axisWords = (i: number) => {
    const l = map.axes[i]?.loadings.filter((v) => Math.abs(v.loading) >= 0.25).slice(0, 1) ?? [];
    return l.map((v) => directionWords[v.feature]?.[v.loading > 0 ? 0 : 1] ?? v.label.toLowerCase()).join(", ") || "a mix of numbers";
  };
  const fly = (p: PatternMapPoint): CSSProperties => ({ "--fx": `${centre.x - sx(p.x)}px`, "--fy": `${centre.y - sy(p.y)}px` }) as CSSProperties;

  return (
    <figure>
      <div ref={ref} className="relative select-none" onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg
            key={map.stage}
            width={width}
            height={h}
            role="img"
            aria-label={`Map of ${cycles.length} finished cycles and ${clients.length} current clients, ${map.stage} days before the decision`}
            className={hover ? "cursor-pointer" : undefined}
            onMouseMove={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              setHover(nearest(e.clientX - r.left, e.clientY - r.top));
            }}
            onClick={() => hover && open(hover)}
          >
            <defs>
              <marker id="map-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L8,4 L0,8 z" className="fill-faint" />
              </marker>
            </defs>
            <rect x={pad.l} y={pad.t} width={width - pad.l - pad.r} height={h - pad.t - pad.b} rx={10} className="fill-subtle" />
            {halos.map((e, i) => (
              <g key={i} className="animate-fade-in" style={{ animationDelay: `${1100 + i * 200}ms` }}>
                <ellipse
                  cx={e.cx}
                  cy={e.cy}
                  rx={e.rx}
                  ry={e.ry}
                  transform={`rotate(${e.angle} ${e.cx} ${e.cy})`}
                  className={e.lost ? "fill-risk/[0.07] stroke-risk/40" : "fill-ok/[0.07] stroke-ok/40"}
                  strokeWidth={1}
                />
                <g transform={`translate(${Math.max(pad.l + 62, Math.min(width - pad.r - 62, e.cx))},${Math.max(pad.t + 14, e.cy - e.ry - 10)})`}>
                  <rect x={-40} y={-10} width={80} height={19} rx={9.5} className={cn("stroke-panel", e.lost ? "fill-risk-soft" : "fill-ok-soft")} strokeWidth={1.5} />
                  <text y={3.5} textAnchor="middle" className={cn("text-[10.5px] font-semibold", e.lost ? "fill-risk-fg" : "fill-ok-fg")}>
                    {e.lost ? `lost · ${e.n}` : `renewed · ${e.n}`}
                  </text>
                </g>
              </g>
            ))}
            {me &&
              cycles
                .filter((c) => neighbourIds.has(c.id))
                .map((c, i) => (
                  <line
                    key={`l${c.id}`}
                    x1={sx(me.x)}
                    y1={sy(me.y)}
                    x2={sx(c.x)}
                    y2={sy(c.y)}
                    pathLength={1}
                    strokeDasharray={1}
                    className="stroke-accent/60 animate-draw"
                    strokeWidth={1.25}
                    style={{ animationDelay: `${1500 + i * 60}ms`, animationDuration: "0.6s" }}
                  />
                ))}
            {cycles.map((c, i) => {
              const near = neighbourIds.has(c.id);
              return (
                <g key={c.id} className="animate-fly-in" style={{ ...fly(c), animationDelay: `${Math.min(700, i * 4)}ms` }}>
                  <circle
                    cx={sx(c.x)}
                    cy={sy(c.y)}
                    r={near || hover?.id === c.id ? 5.5 : 4}
                    strokeWidth={2}
                    className={cn("stroke-subtle transition-opacity", c.lost ? "fill-risk" : "fill-ok", me && !near ? "opacity-30" : "opacity-90")}
                  />
                </g>
              );
            })}
            {clients.map((c, i) => {
              const isMe = c.account_id === selected;
              return (
                <g key={c.id} className="animate-fly-in" style={{ ...fly(c), animationDelay: `${500 + i * 15}ms` }}>
                  <circle cx={sx(c.x)} cy={sy(c.y)} r={isMe ? 7.5 : 5} strokeWidth={2} className={isMe ? "fill-accent stroke-panel" : cn("fill-panel stroke-accent", me && "opacity-60")} />
                </g>
              );
            })}
            {me && (
              <g>
                <circle cx={sx(me.x)} cy={sy(me.y)} r={7.5} className="fill-accent animate-breathe [transform-box:fill-box] origin-center" style={{ animationDelay: "1800ms" }} />
                <g transform={`translate(${Math.min(width - pad.r - me.name.length * 3.4 - 12, Math.max(pad.l + me.name.length * 3.4 + 12, sx(me.x)))},${Math.max(pad.t + 14, sy(me.y) - 20)})`} className="animate-fade-in" style={{ animationDelay: "1600ms" }}>
                  <rect x={-(me.name.length * 3.4 + 10)} y={-11} width={me.name.length * 6.8 + 20} height={20} rx={10} className="fill-ink" />
                  <text y={3.5} textAnchor="middle" className="fill-panel text-[11px] font-semibold">{me.name}</text>
                </g>
              </g>
            )}
            {/* axes, in words */}
            <line x1={pad.l + 4} x2={width - pad.r - 4} y1={h - pad.b + 12} y2={h - pad.b + 12} className="stroke-faint/60" strokeWidth={1} markerEnd="url(#map-arrow)" />
            <text x={width - pad.r - 4} y={h - 6} textAnchor="end" className="fill-muted text-[10.5px]">{axisWords(0)} →</text>
            <line x1={pad.l - 14} x2={pad.l - 14} y1={h - pad.b - 4} y2={pad.t + 4} className="stroke-faint/60" strokeWidth={1} markerEnd="url(#map-arrow)" />
            <text transform={`translate(${pad.l - 19},${pad.t + 6}) rotate(-90)`} textAnchor="end" className="fill-muted text-[10.5px]">{axisWords(1)} →</text>
          </svg>
        )}
        {hover && width > 0 && (
          <ChartTip x={sx(hover.x)} width={width} top={Math.max(4, sy(hover.y) - 70)}>
            <div className="font-semibold text-ink">{hover.name}</div>
            {hover.kind === "cycle" ? (
              <div className="text-muted">
                Finished cycle · {outcomeText[hover.outcome] ?? hover.outcome} {date(hover.date)}
                <div className="text-faint">{neighbourIds.has(hover.id) ? "One of the 9 most similar · " : ""}click to see it as it was then</div>
              </div>
            ) : (
              <div className="text-muted">
                Current client · {hover.verdict ? verdictText[hover.verdict] : "no verdict yet"}
                <div className="text-faint">Click to compare</div>
              </div>
            )}
          </ChartTip>
        )}
      </div>
      <figcaption className="mt-2 space-y-1 text-xs text-muted">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="inline-flex items-center gap-1.5"><DotKey className="bg-ok" />renewed</span>
          <span className="inline-flex items-center gap-1.5"><DotKey className="bg-risk" />revenue lost</span>
          <span className="inline-flex items-center gap-1.5"><span aria-hidden className="size-2.5 rounded-full border-2 border-accent" />current client</span>
          {me && <span className="inline-flex items-center gap-1.5"><span aria-hidden className="h-px w-4 bg-accent" />9 most similar</span>}
        </div>
      </figcaption>
    </figure>
  );
}
