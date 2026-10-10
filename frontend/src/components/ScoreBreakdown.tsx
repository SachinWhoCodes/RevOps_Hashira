import { money } from "../format";
import type { ScoreLine } from "../api";

// The score is never a black box: every point is listed with the signal that earned it.
export default function ScoreBreakdown({
  label,
  score,
  lines,
  arr,
  arrAssumed,
  urgency,
  daysToRenewal,
  priority,
}: {
  label: string;
  score: number;
  lines: ScoreLine[];
  arr?: number | null;
  arrAssumed?: boolean;
  urgency?: number;
  daysToRenewal?: number | null;
  priority?: number;
}) {
  return (
    <details className="group relative inline-block">
      <summary className="cursor-pointer list-none rounded-md px-1 text-xs text-accent hover:underline">how is this scored?</summary>
      <div className="absolute right-0 z-20 mt-1 w-80 rounded-lg border border-line bg-panel p-3 text-xs shadow-lg">
        <div className="mb-1.5 font-semibold text-ink">{label}: {score}/100</div>
        <table className="w-full">
          <tbody>
            {lines.map((l) => (
              <tr key={l.signal} className="align-top">
                <td className="py-0.5 pr-2 text-muted">{l.signal.replace(/_/g, " ")}</td>
                <td className={`py-0.5 text-right tabular-nums ${l.points < 0 ? "text-ok" : "text-ink"}`}>
                  {l.points > 0 ? `+${l.points}` : l.points}
                </td>
              </tr>
            ))}
            {!lines.length && (
              <tr>
                <td className="text-faint">No signals firing.</td>
              </tr>
            )}
          </tbody>
        </table>
        {priority !== undefined && (
          <p className="mt-2 border-t border-line pt-2 text-muted">
            Priority = {score}% × {money(arr ?? 0)}
            {arrAssumed ? " (median ARR, actual unknown)" : " ARR"} × urgency {urgency}
            {daysToRenewal != null ? ` (renewal in ${daysToRenewal}d)` : " (renewal date unknown)"} ={" "}
            <span className="font-medium text-ink">{money(priority)}</span>
          </p>
        )}
        <p className="mt-1.5 text-faint">Points are starting values from the blueprint; the back-test tunes them.</p>
      </div>
    </details>
  );
}
