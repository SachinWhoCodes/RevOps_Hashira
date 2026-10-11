import { Check, Minus, Activity } from "lucide-react";
import type { OutcomeEvents, PlayOutcomeView } from "../api";
import { date, label } from "../format";
import { cn } from "../lib/cn";
import { Badge, Card, Tooltip } from "./ui";

const resultTone = { improved: "ok", no_change: "neutral", worse: "risk", pending: "neutral" } as const;
const resultText = { improved: "Improved", no_change: "No change", worse: "Worse", pending: "Pending" } as const;
const node = { improved: "bg-ok border-ok", no_change: "bg-faint border-faint", worse: "bg-risk border-risk", pending: "bg-panel border-line-strong" } as const;

const EVENTS: [keyof OutcomeEvents, string][] = [
  ["champion_replied", "Champion replied"],
  ["buyer_engaged", "Economic buyer engaged"],
  ["meeting_booked", "Meeting booked"],
  ["meeting_held", "Meeting held"],
  ["next_step_set", "Dated next step on the renewal"],
];

function Events({ e }: { e: OutcomeEvents }) {
  return (
    <ul className="grid gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-2">
      {EVENTS.map(([k, text]) => (
        <li key={k} className={cn("flex items-center gap-2", e[k] ? "text-ink" : "text-faint")}>
          <span className={cn("grid size-4 place-items-center rounded-full", e[k] ? "bg-ok-soft text-ok-fg" : "bg-hover")}>
            {e[k] ? <Check className="size-3" strokeWidth={3} /> : <Minus className="size-3" />}
          </span>
          {text}
        </li>
      ))}
      {e.usage_change_pct != null && (
        <li className={cn("flex items-center gap-2", e.usage_change_pct > 0 ? "text-ok-fg" : e.usage_change_pct < 0 ? "text-risk-fg" : "text-faint")}>
          <span className="w-4 text-center font-semibold">{e.usage_change_pct > 0 ? "↑" : e.usage_change_pct < 0 ? "↓" : "–"}</span>
          Usage {e.usage_change_pct > 0 ? "+" : ""}{e.usage_change_pct}% (14-day average)
        </li>
      )}
    </ul>
  );
}

// What happened after each executed play: checkpoints at +7/+14/+30 days and a live "so far" reading.
export default function OutcomesPanel({ outcomes }: { outcomes: PlayOutcomeView[] }) {
  if (!outcomes.length) return null;
  return (
    <Card
      title="After the play"
      description="Checked at 7, 14 and 30 days after the first write"
      icon={Activity}
      actions={
        <Tooltip content="Each checkpoint compares the account just before the play with the account on the checkpoint date, through the same rules. Customers act for their own reasons: this shows what changed, not that the play caused it.">
          <Badge>Measured after · not proof of cause</Badge>
        </Tooltip>
      }
    >
      <div className="space-y-6">
        {outcomes.map((o) => (
          <div key={o.change_set_id}>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-[14px] font-semibold text-ink capitalize">{label(o.play)}</span>
              <span className="text-xs text-muted">Executed {date(o.executed_at)} · {o.days_since} days ago</span>
            </div>
            <ol className="relative mt-4 grid grid-cols-3">
              <span className="absolute top-[7px] right-[16.6%] left-[16.6%] h-px bg-line" aria-hidden />
              {o.checkpoints.map((c) => (
                <li key={c.day} className="relative flex flex-col items-center text-center">
                  <span className={cn("relative z-10 size-3.5 rounded-full border-2", node[c.result])} />
                  <span className="mt-2 text-[12.5px] font-medium text-ink">Day {c.day}</span>
                  <span className="mt-1">
                    <Badge tone={resultTone[c.result]}>{resultText[c.result]}</Badge>
                  </span>
                  <span className="mt-1 text-[11.5px] text-faint">
                    {c.result === "pending" ? `Due ${date(c.due)}` : c.before && c.after ? `Risk ${c.before.risk} → ${c.after.risk}` : date(c.due)}
                  </span>
                </li>
              ))}
            </ol>
            {o.so_far && (
              <div className="mt-5 rounded-xl border border-line bg-subtle p-4">
                <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
                  <span className="font-medium text-ink">So far</span>
                  <span className="text-muted">(live, not a checkpoint)</span>
                  <span className="tabular-nums">
                    Risk <span className="font-semibold text-ink">{o.so_far.before.risk} → {o.so_far.after.risk}</span>
                  </span>
                  {o.so_far.after.cleared.length > 0 && <Badge tone="ok">Cleared {o.so_far.after.cleared.map(label).join(", ")}</Badge>}
                  {o.so_far.after.new.length > 0 && <Badge tone="risk">New {o.so_far.after.new.map(label).join(", ")}</Badge>}
                </div>
                <Events e={o.so_far.after.events} />
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}
