import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { api, type Explanation, type LiveAssessment, type Timeline } from "../api";
import { date, money } from "../format";
import { chipText, polarityTone } from "../signals";
import { useEvidence } from "./EvidenceDrawer";
import ScoreBreakdown from "./ScoreBreakdown";
import { Badge } from "./ui";

function Tile({ label, value, tone, children }: { label: string; value: string; tone: string; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-line px-3 py-2">
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${tone}`}>{value}</div>
      {children}
    </div>
  );
}

function ExplanationBlock({ e }: { e: Explanation }) {
  const evidence = useEvidence();
  const verified = e.status === "verified";
  return (
    <div className="rounded-md border border-line bg-canvas px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">{e.headline}</h3>
        <Badge tone={verified ? "ok" : "neutral"}
          title={verified ? "Written by AI; every sentence passed the evidence checker" : "Built from the signal rules (AI not used or nothing passed the checker)"}>
          {verified ? "AI-written · every sentence checked" : "rule-based summary"}
        </Badge>
      </div>
      <ol className="mt-2 space-y-1.5 text-sm">
        {e.claims.map((c, i) => (
          <li key={i} className="leading-relaxed">
            {c.text}{" "}
            {c.evidence.map((ev) => (
              <button key={ev.eid} onClick={() => evidence.open(ev)} title={ev.label}
                className="ml-0.5 rounded border border-line bg-panel px-1 py-px align-middle text-[11px] font-medium text-accent hover:border-accent">
                {ev.eid}
              </button>
            ))}
          </li>
        ))}
      </ol>
      {e.blind_spot_note && <p className="mt-2 text-xs text-muted">{e.blind_spot_note}</p>}
      <p className="mt-2 text-[11px] text-faint">
        {verified ? `${e.model} · ${e.prompt_version}` : "template"}
        {e.removed_by_checker > 0 && ` · ${e.removed_by_checker} sentence(s) removed by the checker`}
        {e.stale_removed > 0 && ` · ${e.stale_removed} outdated sentence(s) removed`}
        {" · numbers come from the rules, the AI only words them"}
      </p>
    </div>
  );
}

function Feedback({ caseId, accountId }: { caseId: string; accountId: string }) {
  const qc = useQueryClient();
  const send = useMutation({
    mutationFn: (verdict: "useful" | "wrong") => api(`/cases/${caseId}/feedback`, { json: { verdict } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["timeline", accountId] });
      qc.invalidateQueries({ queryKey: ["queue"] });
    },
  });
  if (send.isSuccess) return <span className="text-xs text-muted">Thanks — recorded in the audit log.</span>;
  return (
    <div className="flex items-center gap-1.5 text-xs">
      <span className="text-muted">Is this case right?</span>
      <button onClick={() => send.mutate("useful")} className="rounded border border-line px-2 py-0.5 hover:bg-canvas">Useful</button>
      <button onClick={() => send.mutate("wrong")} className="rounded border border-line px-2 py-0.5 text-risk hover:bg-risk-soft">This was wrong</button>
    </div>
  );
}

export default function AssessmentPanel({ a, cases, explanation, asOfParam, accountId }: {
  a: LiveAssessment;
  cases: Timeline["cases"];
  explanation?: Explanation | null;
  asOfParam: string;
  accountId: string;
}) {
  const evidence = useEvidence();
  const open = (cases ?? []).filter((c) => c.status === "open");
  const rank = ["risk", "urgency", "positive", "opportunity"];
  const ordered = [...a.signals].sort((x, y) => rank.indexOf(x.polarity) - rank.indexOf(y.polarity));
  return (
    <section className="mb-4 rounded-lg border border-line bg-panel">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <h2 className="text-sm font-semibold">
          What Casefile sees{asOfParam ? ` on ${date(asOfParam)}` : " now"}
          {asOfParam && <span className="ml-2 font-normal text-faint">scored with today's rules</span>}
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          {a.churned && <Badge tone="risk">churned</Badge>}
          {!asOfParam && open.map((c) => <Badge key={c.id} tone={c.kind === "risk" ? "risk" : "ok"}>open {c.kind} case</Badge>)}
          {!asOfParam && open[0] && <Feedback caseId={open[0].id} accountId={accountId} />}
        </div>
      </header>
      <div className="grid gap-4 p-4 md:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]">
        <div className="grid grid-cols-2 gap-2 self-start">
          <Tile label="Risk" value={`${a.risk.score}/100`} tone={a.risk.score >= 40 ? "text-risk" : "text-ink"}>
            <ScoreBreakdown label="Risk" score={a.risk.score} lines={a.risk.lines} arr={a.arr.value} arrAssumed={a.arr.assumed}
              urgency={a.urgency.factor} daysToRenewal={a.urgency.days_to_renewal} priority={a.priority} />
          </Tile>
          <Tile label="Opportunity" value={`${a.opportunity.score}/100`} tone={a.opportunity.score >= 40 ? "text-ok" : "text-ink"}>
            <ScoreBreakdown label="Opportunity" score={a.opportunity.score} lines={a.opportunity.lines} />
          </Tile>
          <Tile label="Confidence" value={a.confidence.level} tone="text-ink">
            <div className="text-xs text-faint">{a.confidence.sources_missing.length ? `missing ${a.confidence.sources_missing.join(", ")}` : "all sources"}</div>
          </Tile>
          <Tile label="Priority" value={money(a.priority)} tone="text-ink">
            <div className="text-xs text-faint">urgency × {a.urgency.factor}</div>
          </Tile>
        </div>
        <div className="space-y-3">
          {explanation && explanation.claims.length > 0 && <ExplanationBlock e={explanation} />}
          {!asOfParam && open.length > 0 && !explanation && (
            <p className="text-xs text-faint">Explanation is being written…</p>
          )}
          {!ordered.length && <p className="text-sm text-muted">No signals are firing for this account.</p>}
          <details open={!explanation}>
            <summary className="cursor-pointer text-xs font-medium text-muted">Signals behind the score ({ordered.length})</summary>
            <ul className="mt-2 space-y-2">
              {ordered.map((s, i) => (
                <li key={i} className="text-sm">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge tone={polarityTone[s.polarity]}>{chipText(s)}</Badge>
                    <span>{s.summary}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1 pl-1">
                    {s.evidence.map((e) => (
                      <button key={e.id} onClick={() => evidence.open(e)} title="Open the original record"
                        className="rounded border border-line bg-canvas px-1.5 py-0.5 text-xs text-muted hover:border-accent hover:text-accent">
                        {e.label}
                      </button>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          </details>
          {a.blind_spots.length > 0 && (
            <div className="rounded-md bg-canvas px-3 py-2 text-xs text-muted">
              <div className="mb-0.5 font-medium text-ink">Blind spots — what Casefile cannot see here</div>
              <ul className="list-disc space-y-0.5 pl-4">
                {a.blind_spots.map((b) => <li key={b.code}>{b.text}</li>)}
              </ul>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
