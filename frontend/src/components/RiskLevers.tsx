import type { Lever, Levers } from "../api";
import { label } from "../format";
import { Badge } from "./ui";

const SIGNAL_NAMES: Record<string, string> = {
  meeting_gap: "meeting gap",
  champion_silence: "champion silence",
  champion_departure: "champion departure",
  usage_decline: "usage decline",
  missing_next_step: "missing next step",
  negative_tone: "negative tone",
  executive_engagement: "buyer engaged",
  single_threaded: "single thread",
};
const name = (s: string) => SIGNAL_NAMES[s] ?? label(s);

function Effect({ l }: { l: Lever }) {
  return (
    <span className="text-xs text-muted">
      {l.clears.length > 0 && <>clears {l.clears.map(name).join(", ")}</>}
      {l.clears.length > 0 && l.adds.length > 0 && " · "}
      {l.adds.length > 0 && <>adds {l.adds.map(name).join(", ")}</>}
    </span>
  );
}

function PlanNote({ l, plan }: { l: Lever; plan: string | null }) {
  if (l.done_by_plan) {
    return (
      <Badge tone="warn" title="The executor wrote this to the CRM, but the score is still built from the CRM data Casefile last read. In this sandbox Casefile reads the synthetic company's CRM, not the HubSpot portal it writes to, so the change is not read back.">
        written to the CRM by this case's plan · not read back yet
      </Badge>
    );
  }
  if (l.in_plan && l.direct_action === "crm_next_step") {
    return <Badge tone="ok" title="Approving the plan's 'Deal next step' line records it once the executor writes it and the CRM syncs back">this case's plan does this</Badge>;
  }
  if (l.in_plan) return <Badge tone="accent" title={`The plan proposed for this case (${label(plan ?? "")}) works towards this`}>this case's plan works on this</Badge>;
  if (l.plays.length) return <span className="text-xs text-faint">play: {label(l.plays[0])}</span>;
  return <span className="text-xs text-faint">no play yet</span>;
}

// Pure rules, no AI: every number here comes from re-scoring the account with the change applied.
export default function RiskLevers({ v }: { v: Levers }) {
  const byKey = Object.fromEntries(v.levers.map((l) => [l.key, l]));
  const close = v.to_close;
  if (v.risk_now === 0) {
    return (
      <section className="mb-4 rounded-lg border border-line bg-panel px-4 py-2.5 text-sm text-muted">
        <span className="font-semibold text-ink">What would lower this risk?</span> Risk is 0 — no risk signals are firing.
      </section>
    );
  }
  return (
    <section className="mb-4 rounded-lg border border-line bg-panel">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <h2 className="text-sm font-semibold">What would lower this risk?</h2>
        <Badge title={`Each change is applied to a copy of this account and re-scored with the live rules (scoring v${v.rules.scoring}, thresholds v${v.rules.thresholds}, playbooks ${v.rules.playbooks}). It says what the rules would do if it happened — not that it will happen, or that the customer will renew.`}>
          re-scored by the rules · not a forecast
        </Badge>
      </header>
      <div className="space-y-3 p-4">
        {close.needed && (
          <div className={`rounded-md px-3 py-2 text-sm ${close.possible ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn"}`}>
            {close.possible ? (
              <>
                <div className="font-medium">
                  Fewest changes that would close this case: risk {v.risk_now} → {close.risk_after}
                  <span className="font-normal"> (a case closes below {v.close_below})</span>
                </div>
                <ol className="mt-1 list-decimal space-y-0.5 pl-5">
                  {close.levers.map((k) => <li key={k}>{byKey[k]?.text ?? k}</li>)}
                </ol>
              </>
            ) : (
              <>
                <span className="font-medium">No combination of these changes closes the case yet:</span> even if all of them
                happen, risk is {close.risk_after} (a case closes below {v.close_below}).
              </>
            )}
          </div>
        )}
        {v.levers.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-faint">
                <th className="pb-1 font-normal">If this happens</th>
                <th className="pb-1 text-right font-normal">Risk</th>
              </tr>
            </thead>
            <tbody>
              {v.levers.map((l) => (
                <tr key={l.key} className="border-t border-line align-top">
                  <td className="py-1.5 pr-3">
                    <div title={l.detail}>{l.text}</div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                      <Effect l={l} />
                      <PlanNote l={l} plan={v.plan_play} />
                    </div>
                  </td>
                  <td className="whitespace-nowrap py-1.5 text-right tabular-nums">
                    <span className="text-faint">{v.risk_now} →</span>{" "}
                    <span className="font-semibold">{l.risk_after}</span>{" "}
                    <span className="text-ok">({l.change})</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {v.expiries.map((e) => (
          <p key={e.signal} className="rounded-md bg-canvas px-3 py-2 text-xs text-muted">
            <span className="font-medium text-ink">{name(e.signal)[0].toUpperCase() + name(e.signal).slice(1)} (+{e.points})</span>: {e.text}
          </p>
        ))}
      </div>
    </section>
  );
}
