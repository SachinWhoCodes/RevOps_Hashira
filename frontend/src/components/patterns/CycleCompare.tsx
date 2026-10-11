import { RotateCcw } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import type { PatternStrand } from "../../api";
import { date, dateShort } from "../../format";
import { cn } from "../../lib/cn";
import { IconButton } from "../ui";
import { useWidth } from "./shared";

const DAY = 86_400_000;
const LANES = ["meetings", "emails_in", "emails_out"] as const;
type Lane = (typeof LANES)[number];
const LANE_LABEL: Record<Lane, string> = { meetings: "Meetings", emails_in: "From them", emails_out: "From us" };
const LANE_COLOR: Record<Lane, string> = { meetings: "text-ch-meet", emails_in: "text-ch-received", emails_out: "text-ch-sent" };
const BEFORE = 395 * DAY; // the year (and a bit) before each decision
const AFTER = 18 * DAY;

type Week = PatternStrand["weeks"][number] & { t: number };
type Cut = { t: number; lost: boolean };

const weeksOf = (s: PatternStrand, today: number): Week[] =>
  s.weeks.map((w) => ({ ...w, t: new Date(w.week).getTime() + 3.5 * DAY })).filter((w) => w.t <= today);

const checkPath = "M-3,0.2 L-1,2.4 L3.2,-2.2";
const crossPath = "M-2.4,-2.4 L2.4,2.4 M2.4,-2.4 L-2.4,2.4";

// Icons drawn once and reused: a video-call badge for meetings, and a paper plane that points up-right
// for email we sent and down-right for email that came in (same shape, its own colour).
function IconDefs() {
  return (
    <defs>
      <symbol id="cc-meet" viewBox="0 0 24 24">
        <rect x="1" y="1" width="22" height="22" rx="6.5" fill="currentColor" />
        <rect x="5" y="8" width="10" height="8" rx="2" fill="white" />
        <path d="M15.5 11 L19.5 8.4 V15.6 L15.5 13 Z" fill="white" />
      </symbol>
      <symbol id="cc-plane" viewBox="0 0 24 24">
        <path d="M22 2 L2.7 9.1 Q1.6 9.6 2.7 10.2 L10.4 13.6 L13.8 21.3 Q14.4 22.4 14.9 21.3 Z" fill="currentColor" />
        <path d="M22 2 L10.4 13.6" stroke="var(--c-panel)" strokeWidth="1.6" strokeLinecap="round" />
      </symbol>
    </defs>
  );
}

function icon(lane: Lane, cx: number, cy: number, size: number): ReactNode {
  const x = cx - size / 2;
  const y = cy - size / 2;
  if (lane === "meetings") return <use href="#cc-meet" x={x} y={y} width={size} height={size} />;
  // received mail: the same plane turned a quarter clockwise, so it points down-right
  return <use href="#cc-plane" x={x} y={y} width={size} height={size} transform={lane === "emails_in" ? `rotate(90 ${cx} ${cy})` : undefined} />;
}

// One finished cycle and one open cycle, each lined up on its own renewal decision, with the three
// snapshots the matching compares joined across. Without a comparison: the client's own history, cut
// at each decision.
export function CycleCompare({
  focus,
  focusEnd,
  client,
  daysToRenewal,
  stage,
  snapshotDays,
}: {
  focus: PatternStrand;
  focusEnd: string | null;
  client: PatternStrand | null;
  daysToRenewal: number | null;
  stage: number | null;
  snapshotDays?: number[];
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [run, setRun] = useState(0);
  const today = new Date(focus.today).getTime();
  const since = new Date(focus.since).getTime();
  const aWeeks = useMemo(() => weeksOf(focus, today), [focus, today]);
  const bWeeks = useMemo(() => (client ? weeksOf(client, today) : []), [client, today]);
  const aCuts = useMemo<Cut[]>(
    () => focus.decisions.map((d) => ({ t: new Date(d.date).getTime(), lost: d.lost })).filter((c) => c.t >= since && c.t <= today),
    [focus, since, today],
  );
  const focusT = focusEnd ? new Date(focusEnd).getTime() : (aCuts[aCuts.length - 1]?.t ?? null);
  const focusCut = aCuts.find((c) => c.t === focusT) ?? aCuts[aCuts.length - 1] ?? null;
  const prevCut = focusCut ? [...aCuts].reverse().find((c) => c.t < focusCut.t) : undefined;
  const clientDecision = client?.renewal_date ? new Date(client.renewal_date).getTime() : null;
  const aligned = !!(client && clientDecision && focusCut);

  const padL = width < 560 ? 84 : 100;
  const padR = 12;
  const plotW = Math.max(1, width - padL - padR);
  const calEnd = Math.max(today, focusCut?.t ?? today) + 30 * DAY;
  const xCal = (t: number) => padL + ((t - since) / (calEnd - since)) * plotW;
  const xRel = (decision: number) => (t: number) => padL + ((t - (decision - BEFORE)) / (BEFORE + AFTER)) * plotW;

  const firstData = aWeeks.find((w) => w.meetings || w.emails_in || w.emails_out || w.users != null)?.t ?? since;
  const snaps = focusCut
    ? (snapshotDays ?? Array.from({ length: 12 }, (_, i) => (i + 1) * 30))
        .map((days) => ({ days, t: focusCut.t - days * DAY }))
        .filter((c) => (snapshotDays ? true : c.t >= Math.max(prevCut?.t ?? since, firstData + 90 * DAY)))
    : [];
  const compared = stage != null ? [stage, stage + 30, stage + 60] : [];

  // One row: three icon lanes and the usage line beneath them.
  const row = (weeks: Week[], x: (t: number) => number, y0: number, gap: number, usageH: number, base: number, iconSize: number, from = -Infinity, to = Infinity) => {
    const shown = weeks.filter((w) => w.t >= from && w.t <= to);
    const uTop = y0 + gap * 3 - 4;
    const maxU = Math.max(1, ...shown.map((w) => w.users ?? 0));
    const uy = (v: number) => uTop + usageH - (v / maxU) * (usageH - 6);
    const u = shown.filter((w) => w.users != null);
    const line = u.map((w, i) => `${i ? "L" : "M"}${x(w.t).toFixed(1)},${uy(w.users ?? 0).toFixed(1)}`).join("");
    const area = u.length ? `${line}L${x(u[u.length - 1].t).toFixed(1)},${uTop + usageH}L${x(u[0].t).toFixed(1)},${uTop + usageH}Z` : "";
    const start = from === -Infinity ? since : from;
    const span = Math.max(1, (to === Infinity ? calEnd : to) - start);
    return (
      <g>
        {LANES.map((lane, i) => (
          <g key={lane}>
            <g className={LANE_COLOR[lane]}>{icon(lane, 14, y0 + i * gap, 13)}</g>
            <text x={27} y={y0 + i * gap + 3.5} className="fill-muted text-[10.5px]">{LANE_LABEL[lane]}</text>
            <line x1={padL} x2={width - padR} y1={y0 + i * gap} y2={y0 + i * gap} className="stroke-line" strokeWidth={1} />
          </g>
        ))}
        <text x={26} y={uTop + usageH - 3} className="fill-muted text-[10.5px]">Usage</text>
        <line x1={padL} x2={width - padR} y1={uTop + usageH} y2={uTop + usageH} className="stroke-line" strokeWidth={1} />
        {area && <path d={area} className="fill-accent/10 animate-fade-in" style={{ animationDelay: `${base + 400}ms` }} />}
        {line && <path d={line} pathLength={1} strokeDasharray={1} fill="none" strokeWidth={1.75} strokeLinejoin="round" className="stroke-accent animate-draw" style={{ animationDuration: "1.5s", animationDelay: `${base + 100}ms` }} />}
        {shown.map((w) =>
          LANES.map((lane, li) => {
            const n = w[lane];
            if (!n) return null;
            const size = iconSize + Math.min(6, 2.5 * Math.sqrt(n - 1));
            return (
              <g key={`${w.week}${lane}`} className={cn(LANE_COLOR[lane], "animate-pop [transform-box:fill-box] origin-center")} style={{ animationDelay: `${base + ((w.t - start) / span) * 1100}ms` }}>
                {icon(lane, x(w.t), y0 + li * gap, size)}
                <title>{`${date(w.week)} · ${n} ${lane === "meetings" ? (n === 1 ? "meeting" : "meetings") : n === 1 ? "email" : "emails"} ${lane === "emails_in" ? "from them" : lane === "emails_out" ? "from us" : ""}`}</title>
              </g>
            );
          }),
        )}
      </g>
    );
  };

  const badge = (cx: number, cy: number, lost: boolean) => (
    <g transform={`translate(${cx},${cy})`}>
      <circle r={8} className={cn(lost ? "fill-risk" : "fill-ok", "stroke-panel")} strokeWidth={2} />
      <path d={lost ? crossPath : checkPath} fill="none" className="stroke-white" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />
    </g>
  );

  const al = { a: 36, b: 192, gap: 26, usage: 34 };
  const H = aligned ? 310 : 236;
  const cal = { y0: 40, gap: 34, usage: 60 };

  return (
    <div className="relative">
      <div className="absolute -top-1 right-0 z-10">
        <IconButton icon={RotateCcw} label="Replay" size="sm" onClick={() => setRun((r) => r + 1)} />
      </div>
      <div ref={ref} className="relative select-none">
        {width > 0 && aligned && focusCut && client && clientDecision && (
          <svg key={`al${run}`} width={width} height={H} role="img" aria-label={`${focus.name} and ${client.name}, lined up on their renewal decisions`}>
            <IconDefs />
            {/* finished cycle */}
            <g className="animate-fade-up">
              <text x={padL} y={al.a - 18} className="fill-ink text-[12px] font-semibold">{focus.name}</text>
              <rect
                x={xRel(focusCut.t)(Math.max(prevCut?.t ?? since, focusCut.t - BEFORE))}
                y={al.a - 12}
                width={Math.max(0, xRel(focusCut.t)(focusCut.t) - xRel(focusCut.t)(Math.max(prevCut?.t ?? since, focusCut.t - BEFORE)))}
                height={al.gap * 3 + al.usage + 2}
                rx={8}
                className={focusCut.lost ? "fill-risk/[0.08]" : "fill-ok/[0.08]"}
              />
              {snaps.map((c) => {
                const used = compared.includes(c.days);
                return (
                  <line key={c.days} x1={xRel(focusCut.t)(c.t)} x2={xRel(focusCut.t)(c.t)} y1={al.a - 12} y2={al.a + al.gap * 3 + al.usage - 10} className={used ? "stroke-accent" : "stroke-accent/20"} strokeWidth={used ? 2 : 1} />
                );
              })}
              {row(aWeeks, xRel(focusCut.t), al.a, al.gap, al.usage, 100, 14, focusCut.t - BEFORE, focusCut.t + AFTER)}
            </g>
            {/* open cycle */}
            <g className="animate-fade-up" style={{ animationDelay: "300ms" }}>
              <text x={padL} y={al.b - 18} className="fill-ink text-[12px] font-semibold">{client.name}</text>
              <rect
                x={xRel(clientDecision)(Math.max(since, clientDecision - BEFORE))}
                y={al.b - 12}
                width={Math.max(0, xRel(clientDecision)(today) - xRel(clientDecision)(Math.max(since, clientDecision - BEFORE)))}
                height={al.gap * 3 + al.usage + 2}
                rx={8}
                className="fill-accent/[0.07]"
              />
              {row(bWeeks, xRel(clientDecision), al.b, al.gap, al.usage, 400, 14, clientDecision - BEFORE, today)}
            </g>
            {/* the three compared snapshots, joined across */}
            {compared.map((d, i) => {
              const ta = focusCut.t - d * DAY;
              const tb = i === 0 && daysToRenewal != null ? clientDecision - Math.max(0, daysToRenewal) * DAY : clientDecision - d * DAY;
              const xa = xRel(focusCut.t)(ta);
              const xb = xRel(clientDecision)(tb);
              const y1 = al.a + al.gap * 3 + al.usage - 10;
              const y2 = al.b - 12;
              return (
                <g key={d}>
                  <path d={`M${xa},${y1} C${xa},${(y1 + y2) / 2} ${xb},${(y1 + y2) / 2} ${xb},${y2}`} pathLength={1} strokeDasharray={1} fill="none" className="stroke-accent animate-draw" strokeWidth={1.5} style={{ animationDelay: `${1300 + i * 160}ms`, animationDuration: "0.7s" }} />
                  <circle cx={xa} cy={y1} r={3} className="fill-accent animate-pop [transform-box:fill-box] origin-center" style={{ animationDelay: `${1200 + i * 160}ms` }} />
                  <circle cx={xb} cy={y2} r={3} className="fill-accent animate-pop [transform-box:fill-box] origin-center" style={{ animationDelay: `${1900 + i * 160}ms` }} />
                </g>
              );
            })}
            {/* decisions, lined up */}
            <line x1={xRel(focusCut.t)(focusCut.t)} x2={xRel(focusCut.t)(focusCut.t)} y1={al.a - 12} y2={al.b + al.gap * 3 + al.usage - 10} className="stroke-ink/25" strokeWidth={1} />
            {badge(xRel(focusCut.t)(focusCut.t), al.a - 12, focusCut.lost)}
            <g className="animate-fade-in" style={{ animationDelay: "800ms" }}>
              <circle cx={xRel(clientDecision)(clientDecision)} cy={al.b - 12} r={8} className="fill-panel stroke-accent" strokeWidth={2} strokeDasharray="3 2" />
              <text x={xRel(clientDecision)(clientDecision)} y={al.b - 8.5} textAnchor="middle" className="fill-accent-fg text-[10px] font-bold">?</text>
            </g>
            <g className="animate-fade-in" style={{ animationDelay: "1600ms" }}>
              <line x1={xRel(clientDecision)(today)} x2={xRel(clientDecision)(today)} y1={al.b - 12} y2={al.b + al.gap * 3 + al.usage - 10} className="stroke-accent" strokeWidth={1.5} />
              <circle cx={xRel(clientDecision)(today)} cy={al.b + al.gap * 3 + al.usage - 10} r={4} className="fill-accent animate-breathe [transform-box:fill-box] origin-center" />
            </g>
            {[360, 270, 180, 90].map((d) => (
              <text key={d} x={xRel(focusCut.t)(focusCut.t - d * DAY)} y={H - 6} textAnchor="middle" className="fill-faint text-[10px] tabular-nums">−{d} d</text>
            ))}
            <text x={xRel(focusCut.t)(focusCut.t)} y={H - 6} textAnchor="middle" className="fill-ink text-[10.5px] font-semibold">decision</text>
          </svg>
        )}

        {width > 0 && !aligned && (
          <svg key={`cal${run}`} width={width} height={H} role="img" aria-label={`${focus.name}: history cut at each renewal decision`}>
            <IconDefs />
            <text x={padL} y={18} className="fill-ink text-[12px] font-semibold">{focus.name}</text>
            {aCuts.map((c, i) => {
              const start = i ? aCuts[i - 1].t : since;
              return (
                <rect key={c.t} x={xCal(start)} y={cal.y0 - 12} width={Math.max(0, xCal(c.t) - xCal(start))} height={cal.gap * 3 + cal.usage + 2} rx={8} className={cn("animate-fade-in", c.lost ? "fill-risk/[0.08]" : "fill-ok/[0.08]")} />
              );
            })}
            {snaps.map((c) => (
              <line key={c.days} x1={xCal(c.t)} x2={xCal(c.t)} y1={cal.y0 - 12} y2={cal.y0 + cal.gap * 3 + cal.usage - 10} className="stroke-accent/30" strokeWidth={1} />
            ))}
            {row(aWeeks, xCal, cal.y0, cal.gap, cal.usage, 0, 9)}
            {aCuts.map((c) => (
              <g key={c.t}>
                <line x1={xCal(c.t)} x2={xCal(c.t)} y1={cal.y0 - 12} y2={cal.y0 + cal.gap * 3 + cal.usage - 10} strokeWidth={2} className={c.lost ? "stroke-risk" : "stroke-ok"} />
                {badge(xCal(c.t), cal.y0 - 12, c.lost)}
                <text x={xCal(c.t)} y={H - 6} textAnchor="middle" className="fill-muted text-[10.5px] font-medium">{dateShort(new Date(c.t).toISOString())}</text>
              </g>
            ))}
            <line x1={xCal(today)} x2={xCal(today)} y1={cal.y0 - 12} y2={cal.y0 + cal.gap * 3 + cal.usage - 10} className="stroke-ink/25" strokeWidth={1} />
            <text x={xCal(today)} y={H - 6} textAnchor="end" className="fill-faint text-[10px]">today</text>
          </svg>
        )}
      </div>
    </div>
  );
}
