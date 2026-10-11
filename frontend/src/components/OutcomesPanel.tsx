import type { OutcomeEvents, PlayOutcomeView } from "../api";
import { date, label } from "../format";
import { Badge } from "./ui";

const tone = { improved: "ok", no_change: "neutral", worse: "risk", pending: "neutral" } as const;
const resultText = { improved: "improved", no_change: "no change", worse: "worse", pending: "pending" } as const;

const EVENTS: [keyof OutcomeEvents, string][] = [
  ["champion_replied", "Champion replied"],
  ["buyer_engaged", "Economic buyer engaged"],
  ["meeting_booked", "Meeting booked"],
  ["meeting_held", "Meeting held"],
  ["next_step_set", "Dated next step on the renewal"],
];

function Events({ e }: { e: OutcomeEvents }) {
  return (
    <ul className="grid gap-x-4 gap-y-0.5 text-xs sm:grid-cols-2">
      {EVENTS.map(([k, text]) => (
        <li key={k} className={e[k] ? "text-ok" : "text-faint"}>
          {e[k] ? "✓" : "–"} {text}
        </li>
      ))}
      {e.usage_change_pct != null && (
        <li className={e.usage_change_pct > 0 ? "text-ok" : e.usage_change_pct < 0 ? "text-risk" : "text-faint"}>
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
    <section className="mb-4 rounded-lg border border-line bg-panel">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <h2 className="text-sm font-semibold">After the play</h2>
        <Badge title="Each checkpoint compares the account as it was just before the play with the account as of the checkpoint date, through the same rules. Customers act for their own reasons: this shows what changed, not that the play caused it.">
          measured after · not proof of cause
        </Badge>
      </header>
      <div className="space-y-4 p-4">
        {outcomes.map((o) => (
          <div key={o.change_set_id} className="space-y-2">
            <div className="text-sm">
              <span className="font-medium">{label(o.play)}</span>
              <span className="text-muted"> · executed {date(o.executed_at)} ({o.days_since} days ago)</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {o.checkpoints.map((c) => (
                <Badge key={c.day} tone={tone[c.result]}
                  title={c.after && c.before ? `Risk ${c.before.risk} → ${c.after.risk}` : `Measured on ${date(c.due)}`}>
                  day {c.day} · {c.result === "pending" ? `due ${date(c.due)}` : resultText[c.result]}
                  {c.after && c.before ? ` · risk ${c.before.risk} → ${c.after.risk}` : ""}
                </Badge>
              ))}
            </div>
            {o.so_far && (
              <div className="rounded-md bg-canvas px-3 py-2">
                <div className="mb-1 text-xs text-muted">
                  So far (live, not a checkpoint): risk <span className="font-medium text-ink">{o.so_far.before.risk} → {o.so_far.after.risk}</span>
                  {o.so_far.after.cleared.length > 0 && <> · cleared {o.so_far.after.cleared.map(label).join(", ")}</>}
                  {o.so_far.after.new.length > 0 && <> · new {o.so_far.after.new.map(label).join(", ")}</>}
                </div>
                <Events e={o.so_far.after.events} />
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
