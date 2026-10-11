import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Replay, type ReplaySaid } from "../api";
import { useEvidence } from "../components/EvidenceDrawer";
import { Badge, ErrorNote, Loading, PageHeader, Panel } from "../components/ui";
import { date, label } from "../format";

function Chart({ r, selected, onSelect }: { r: Replay; selected: number; onSelect: (i: number) => void }) {
  const w = 720;
  const h = 180;
  const pts = r.points;
  const x = (i: number) => (pts.length > 1 ? (i / (pts.length - 1)) * (w - 20) + 10 : w / 2);
  const y = (v: number) => h - 10 - (v / 100) * (h - 30);
  const first = new Date(pts[0].date).getTime();
  const last = new Date(pts[pts.length - 1].date).getTime();
  const xDate = (d: string) => 10 + ((new Date(d).getTime() - first) / Math.max(last - first, 1)) * (w - 20);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.risk).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h + 16}`} className="w-full" role="img" aria-label="Risk score over time">
      <line x1={10} x2={w - 10} y1={y(r.flag_at)} y2={y(r.flag_at)} strokeDasharray="4 4" className="stroke-risk/60" />
      <text x={w - 10} y={y(r.flag_at) - 4} textAnchor="end" className="fill-risk text-[10px]">case opens at {r.flag_at}</text>
      {r.outcomes
        .filter((o) => o.renewal_date >= pts[0].date && o.renewal_date <= pts[pts.length - 1].date)
        .map((o) => (
          <g key={o.renewal_date}>
            <line x1={xDate(o.renewal_date)} x2={xDate(o.renewal_date)} y1={14} y2={h - 10}
              className={o.outcome === "churned" || o.outcome === "downgraded" ? "stroke-risk" : "stroke-ok"} />
            <text x={xDate(o.renewal_date)} y={10} textAnchor="middle" className="fill-muted text-[10px]">{o.outcome}</text>
          </g>
        ))}
      <path d={path} fill="none" strokeWidth={2} className="stroke-accent" />
      {pts.map((p, i) => (
        <circle key={p.date} cx={x(i)} cy={y(p.risk)} r={i === selected ? 5 : 3}
          className={`cursor-pointer ${p.flagged ? "fill-risk" : "fill-accent"}`} onClick={() => onSelect(i)}>
          <title>{`${p.date}: risk ${p.risk}${p.signals.length ? ` · ${p.signals.join(", ")}` : ""}`}</title>
        </circle>
      ))}
      <text x={10} y={h + 12} className="fill-faint text-[10px]">{pts[0].date}</text>
      <text x={w - 10} y={h + 12} textAnchor="end" className="fill-faint text-[10px]">{pts[pts.length - 1].date}</text>
    </svg>
  );
}

export default function ReplayPage() {
  const { accountId } = useParams();
  const evidence = useEvidence();
  const replay = useQuery({ queryKey: ["replay", accountId], queryFn: () => api<Replay>(`/backtest/replay/${accountId}?weeks=104`) });
  const r = replay.data;
  const [picked, setSelected] = useState<number | null>(null);
  const selected = picked ?? Math.max((r?.points.length ?? 1) - 1, 0); // latest week until one is picked
  const point = r?.points[selected];
  const said = useQuery({
    queryKey: ["replay-said", accountId, point?.date],
    queryFn: () => api<ReplaySaid>(`/backtest/replay/${accountId}/explain?as_of=${point!.date}`),
    enabled: !!point,
  });
  return (
    <>
      <div className="mb-1 text-sm">
        <Link to="/backtest" className="text-accent hover:underline">← Back-test</Link>
      </div>
      <PageHeader title={`Replay · ${r?.account.name ?? ""}`} subtitle="Weekly risk score using only what was known on each date. Click a point or move the slider." />
      {replay.isLoading && <Loading />}
      {replay.error && <ErrorNote error={replay.error} />}
      {r && r.points.length > 0 && point && (
        <div className="space-y-4">
          <Panel>
            <Chart r={r} selected={selected} onSelect={setSelected} />
            <input type="range" min={0} max={r.points.length - 1} value={selected} onChange={(e) => setSelected(Number(e.target.value))}
              className="mt-2 w-full" aria-label="Pick a date" />
            {r.excluded_signals.length > 0 && (
              <p className="mt-1 text-xs text-faint">Same rules as the back-test: {r.excluded_signals.map(label).join(", ")} left out (AI email labels exist only for recent emails).</p>
            )}
          </Panel>
          <Panel
            title={`What Casefile would have said on ${date(point.date)}`}
            actions={<Link to={`/accounts/${r.account.id}?as_of=${point.date}`} className="text-xs text-accent hover:underline">Open the account as of this date</Link>}
          >
            <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
              <Badge tone={point.flagged ? "risk" : "neutral"}>risk {point.risk}</Badge>
              {point.opportunity > 0 && <Badge tone="ok">opportunity {point.opportunity}</Badge>}
              {point.flagged ? <span className="text-risk">a risk case would have been open</span> : <span className="text-muted">no case</span>}
            </div>
            {said.isLoading && <Loading />}
            {said.data && (
              <>
                <div className="font-medium">{said.data.headline}</div>
                <ul className="mt-1 space-y-1 text-sm">
                  {said.data.claims.map((c, i) => (
                    <li key={i}>
                      {c.text}
                      {c.evidence.map((e) => (
                        <button key={e.eid} onClick={() => evidence.open(e)} title={e.label}
                          className="ml-1 rounded border border-line bg-canvas px-1 text-[11px] text-muted hover:border-accent hover:text-accent">{e.eid}</button>
                      ))}
                    </li>
                  ))}
                  {!said.data.claims.length && <li className="text-muted">No signals were firing.</li>}
                </ul>
                <p className="mt-2 text-xs text-faint">{said.data.source}</p>
              </>
            )}
          </Panel>
        </div>
      )}
    </>
  );
}
