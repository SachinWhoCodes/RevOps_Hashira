type Week = { week: string; active_users_avg: number; seats_used: number };

// Weekly average active users as bars, purchased seats as a dashed line.
export default function UsageChart({ weeks, seats }: { weeks: Week[]; seats: number | null | undefined }) {
  if (!weeks.length) return <p className="text-sm text-faint">No usage data for this account (blind spot).</p>;
  const shown = weeks.slice(-26);
  const max = Math.max(seats ?? 0, ...shown.map((w) => w.active_users_avg), 1);
  const w = 520;
  const h = 120;
  const bar = w / shown.length;
  const y = (v: number) => h - (v / max) * (h - 8);
  return (
    <figure>
      <svg viewBox={`0 0 ${w} ${h + 18}`} className="w-full" role="img" aria-label="Weekly active users">
        {shown.map((wk, i) => (
          <rect key={wk.week} x={i * bar + 1} y={y(wk.active_users_avg)} width={Math.max(bar - 2, 1)}
            height={h - y(wk.active_users_avg)} rx={1.5} className="fill-accent/70">
            <title>{`Week of ${wk.week}: ${wk.active_users_avg} avg daily active users`}</title>
          </rect>
        ))}
        {seats ? (
          <line x1={0} x2={w} y1={y(seats)} y2={y(seats)} strokeDasharray="4 4" className="stroke-faint" />
        ) : null}
        <text x={0} y={h + 14} className="fill-faint text-[10px]">{shown[0].week}</text>
        <text x={w} y={h + 14} textAnchor="end" className="fill-faint text-[10px]">{shown[shown.length - 1].week}</text>
      </svg>
      <figcaption className="mt-1 text-xs text-muted">
        Avg daily active users per week (last {shown.length} weeks){seats ? ` · dashed line = ${seats} seats purchased` : ""}
      </figcaption>
    </figure>
  );
}
