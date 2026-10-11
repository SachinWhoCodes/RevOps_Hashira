import { Flag, Hourglass, Route, TrendingDown } from "lucide-react";
import type { Lever, Levers } from "../api";
import { label, sentence } from "../format";
import { nameFor, positiveSignals } from "../signals";
import { Badge, Callout, Card, Tooltip } from "./ui";

function PlanNote({ l, plan }: { l: Lever; plan: string | null }) {
  if (l.done_by_plan) {
    return (
      <Tooltip content="The executor wrote this to the CRM, but the score is built from the CRM data Casefile last read. Until that change is read back, the rules still see the old value.">
        <Badge tone="warn" dot>Written by this plan · not read back yet</Badge>
      </Tooltip>
    );
  }
  if (l.in_plan && l.direct_action === "crm_next_step") return <Badge tone="ok" dot>This case's plan does this</Badge>;
  if (l.in_plan) return <Badge tone="accent" dot>Plan works on this{plan ? ` · ${label(plan)}` : ""}</Badge>;
  if (l.plays.length) return <span className="text-xs text-faint capitalize">Play: {label(l.plays[0])}</span>;
  return <span className="text-xs text-faint">No play yet</span>;
}

// Pure rules, no AI: every number comes from re-scoring the account with the change applied.
export default function RiskLevers({ v }: { v: Levers }) {
  const byKey = Object.fromEntries(v.levers.map((l) => [l.key, l]));
  const close = v.to_close;
  const rulesNote = `Each change is applied to a copy of this account and re-scored with the live rules (scoring v${v.rules.scoring}, thresholds v${v.rules.thresholds}, playbooks ${v.rules.playbooks}). It says what the rules would do, not that it will happen or that the customer will renew.`;
  return (
    <Card
      title="What would lower this risk?"
      description="Changes in the world, re-scored through the live rules"
      icon={TrendingDown}
      actions={
        <Tooltip content={rulesNote}>
          <Badge>Re-scored by the rules · not a forecast</Badge>
        </Tooltip>
      }
    >
      {v.risk_now === 0 ? (
        <p className="text-sm text-muted">Risk is 0 — no risk signals are firing.</p>
      ) : (
        <div className="space-y-4">
          {close.needed &&
            (close.possible ? (
              <Callout tone="ok" icon={Route} title={`Fewest changes to close this case: risk ${v.risk_now} → ${close.risk_after}`}>
                A case closes below {v.close_below}.
                <ol className="mt-2 space-y-1">
                  {close.levers.map((k, i) => (
                    <li key={k} className="flex gap-2 text-ink">
                      <span className="grid size-5 shrink-0 place-items-center rounded-full bg-ok/15 text-[11px] font-semibold text-ok-fg">{i + 1}</span>
                      {byKey[k]?.text ?? k}
                    </li>
                  ))}
                </ol>
              </Callout>
            ) : (
              <Callout tone="warn" icon={Flag} title="No combination of these changes closes the case yet">
                Even if all of them happen, risk is {close.risk_after}; a case closes below {v.close_below}.
              </Callout>
            ))}

          {v.levers.length > 0 && (
            <ul className="divide-y divide-line rounded-xl border border-line">
              {v.levers.map((l, i) => (
                <li key={l.key} className="grid gap-3 px-4 py-3.5 sm:grid-cols-[minmax(0,1fr)_200px] sm:items-center animate-fade-up" style={{ animationDelay: `${i * 50}ms` }}>
                  <div className="min-w-0">
                    <Tooltip content={l.detail}>
                      <div className="text-[14px] font-medium text-ink">{l.text}</div>
                    </Tooltip>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      {l.clears.map((s) => <Badge key={s} tone="ok">Clears {nameFor(s).toLowerCase()}</Badge>)}
                      {l.adds.map((s) => <Badge key={s} tone={positiveSignals.has(s) ? "ok" : "risk"}>Adds {nameFor(s).toLowerCase()}</Badge>)}
                      <PlanNote l={l} plan={v.plan_play} />
                    </div>
                  </div>
                  <div>
                    <div className="flex items-baseline justify-end gap-1.5 text-[13px] tabular-nums">
                      <span className="text-faint">{v.risk_now}</span>
                      <span className="text-faint">→</span>
                      <span className="text-[15px] font-semibold text-ink">{l.risk_after}</span>
                      <span className="font-medium text-ok-fg">({l.change})</span>
                    </div>
                    <div className="relative mt-1.5 h-1.5 overflow-hidden rounded-full bg-hover">
                      <div className="absolute inset-y-0 left-0 rounded-full bg-risk/25" style={{ width: `${v.risk_now}%` }} />
                      <div className="absolute inset-y-0 left-0 origin-left rounded-full bg-risk animate-grow-x" style={{ width: `${l.risk_after}%` }} />
                      <div className="absolute inset-y-0 w-px bg-ink/50" style={{ left: `${v.open_at}%` }} title={`Case opens at ${v.open_at}`} />
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {v.expiries.map((e) => (
            <div key={e.signal} className="flex gap-3 rounded-xl bg-subtle px-4 py-3 text-[13px]">
              <Hourglass className="mt-0.5 size-4 shrink-0 text-faint" aria-hidden />
              <p className="text-muted">
                <span className="font-medium text-ink">{sentence(nameFor(e.signal).toLowerCase())} (+{e.points})</span>: {e.text}
              </p>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
