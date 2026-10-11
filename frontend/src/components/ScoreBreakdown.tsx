import { Calculator } from "lucide-react";
import type { ScoreLine } from "../api";
import { money } from "../format";
import { cn } from "../lib/cn";
import { nameFor } from "../signals";
import { SignalIcon } from "./domain";
import { Popover } from "./ui";

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
  compact = false,
}: {
  label: string;
  score: number;
  lines: ScoreLine[];
  arr?: number | null;
  arrAssumed?: boolean;
  urgency?: number;
  daysToRenewal?: number | null;
  priority?: number;
  compact?: boolean;
}) {
  const max = Math.max(...lines.map((l) => Math.abs(l.points)), 1);
  return (
    <Popover
      width={340}
      trigger={(p) => (
        <button
          {...p}
          className={cn(
            "inline-flex items-center gap-1 rounded-md text-xs font-medium text-accent-fg transition-colors hover:text-accent-hover",
            compact ? "p-1" : "px-1.5 py-1 hover:bg-accent-soft",
          )}
          aria-label={`How the ${label.toLowerCase()} score is calculated`}
        >
          <Calculator className="size-3.5" aria-hidden />
          {!compact && "How it's scored"}
        </button>
      )}
    >
      <div className="p-2">
        <div className="mb-3 flex items-baseline justify-between">
          <span className="text-[13px] font-semibold text-ink">{label}</span>
          <span className="text-[13px] font-semibold tabular-nums">{score}<span className="text-faint">/100</span></span>
        </div>
        {lines.length ? (
          <ul className="space-y-2">
            {lines.map((l) => {
              return (
                <li key={l.signal} className="text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 text-muted">
                      <SignalIcon type={l.signal} className="size-3.5" /> {nameFor(l.signal)}
                    </span>
                    <span className={cn("font-medium tabular-nums", l.points < 0 ? "text-ok-fg" : "text-ink")}>{l.points > 0 ? `+${l.points}` : l.points}</span>
                  </div>
                  <div className="mt-1 h-1 overflow-hidden rounded-full bg-hover">
                    <div className={cn("h-full rounded-full", l.points < 0 ? "bg-ok" : "bg-ink/40")} style={{ width: `${(Math.abs(l.points) / max) * 100}%` }} />
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-xs text-faint">No signals are firing.</p>
        )}
        {priority !== undefined && (
          <div className="mt-3 rounded-lg bg-subtle p-2.5 text-xs leading-relaxed text-muted">
            <div className="mb-0.5 font-medium text-ink">Priority {money(priority)}</div>
            {score}% risk × {money(arr ?? 0)} ARR{arrAssumed ? " (median — actual unknown)" : ""} × urgency {urgency}
            {daysToRenewal != null ? ` (renewal in ${daysToRenewal}d)` : " (renewal date unknown)"}
          </div>
        )}
        <p className="mt-2 text-[11px] text-faint">Points are starting values; the back-test tunes them.</p>
      </div>
    </Popover>
  );
}
