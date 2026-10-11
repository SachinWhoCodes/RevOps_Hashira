import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, EyeOff, FileSearch, PenLine, X } from "lucide-react";
import { useState } from "react";
import { api, type Explanation, type LiveAssessment, type Timeline } from "../api";
import { date, money } from "../format";
import { cn } from "../lib/cn";
import { useEvidence } from "./EvidenceDrawer";
import { EvidenceChip, RecordChip, SignalChip, VerifiedSeal } from "./domain";
import ScoreBreakdown from "./ScoreBreakdown";
import { Badge, Button, Callout, Card, ScoreRing, Tooltip, useToast } from "./ui";

function ExplanationBlock({ e }: { e: Explanation }) {
  const verified = e.status === "verified";
  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h3 className="text-[17px] leading-snug font-semibold tracking-[-0.015em] text-ink">{e.headline}</h3>
        <VerifiedSeal verified={verified} />
      </div>
      <ol className="mt-3 space-y-2.5">
        {e.claims.map((c, i) => (
          <li key={i} className="flex gap-3 text-[14px] leading-relaxed text-ink animate-fade-up" style={{ animationDelay: `${i * 60}ms` }}>
            <span className="mt-[3px] grid size-5 shrink-0 place-items-center rounded-full bg-subtle text-[11px] font-semibold text-muted ring-1 ring-line">{i + 1}</span>
            <span>
              {c.text}
              {c.evidence.map((ev) => <EvidenceChip key={ev.eid} e={ev} />)}
            </span>
          </li>
        ))}
      </ol>
      {e.blind_spot_note && <p className="mt-3 text-[13px] text-muted">{e.blind_spot_note}</p>}
      <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-faint">
        <PenLine className="size-3" aria-hidden />
        {verified ? `${e.model} · ${e.prompt_version}` : "Template"}
        {e.removed_by_checker > 0 && <span>· {e.removed_by_checker} sentence(s) removed by the checker</span>}
        {e.stale_removed > 0 && <span>· {e.stale_removed} outdated sentence(s) removed</span>}
        <span>· numbers come from the rules; the AI only words them</span>
      </p>
    </div>
  );
}

function Feedback({ caseId, accountId }: { caseId: string; accountId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const send = useMutation({
    mutationFn: (verdict: "useful" | "wrong") => api(`/cases/${caseId}/feedback`, { json: { verdict } }),
    onSuccess: (_, verdict) => {
      qc.invalidateQueries({ queryKey: ["timeline", accountId] });
      qc.invalidateQueries({ queryKey: ["queue"] });
      toast({ tone: "ok", title: verdict === "useful" ? "Marked as useful" : "Marked as wrong", description: "Recorded in the audit log. Thank you." });
    },
  });
  if (send.isSuccess) return <span className="text-xs text-muted">Feedback recorded</span>;
  return (
    <div className="flex items-center gap-1">
      <span className="mr-1 hidden text-xs text-muted sm:inline">Is this right?</span>
      <Button size="xs" icon={Check} onClick={() => send.mutate("useful")} loading={send.isPending && send.variables === "useful"}>Useful</Button>
      <Button size="xs" variant="dangerSoft" icon={X} onClick={() => send.mutate("wrong")} loading={send.isPending && send.variables === "wrong"}>Wrong</Button>
    </div>
  );
}

function Gauge({ label, value, kind, children }: { label: string; value: number; kind: "risk" | "opportunity"; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-line bg-subtle px-3 pt-4 pb-2">
      <ScoreRing value={value} kind={kind} size={76} stroke={6} />
      <div className="mt-2 text-xs font-medium text-muted">{label}</div>
      {children}
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
  // Without a written explanation the signals are the explanation, so they start open.
  const [showSignals, setShowSignals] = useState(!explanation?.claims.length);
  const open = (cases ?? []).filter((c) => c.status === "open");
  const rank = ["risk", "urgency", "positive", "opportunity"];
  const ordered = [...a.signals].sort((x, y) => rank.indexOf(x.polarity) - rank.indexOf(y.polarity));
  return (
    <Card
      title={asOfParam ? `Case brief · as of ${date(asOfParam)}` : "Case brief"}
      description={asOfParam ? "Scored with today's rules on what was known then" : "What Casefile sees, and the evidence for it"}
      icon={FileSearch}
      actions={
        <>
          {a.churned && <Badge tone="risk">Churned</Badge>}
          {!asOfParam && open.map((c) => <Badge key={c.id} tone={c.kind === "risk" ? "risk" : "ok"} dot>Open {c.kind} case</Badge>)}
          {!asOfParam && open[0] && <Feedback caseId={open[0].id} accountId={accountId} />}
        </>
      }
    >
      <div className="grid gap-6 lg:grid-cols-[208px_minmax(0,1fr)]">
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <Gauge label="Risk" value={a.risk.score} kind="risk">
              <ScoreBreakdown compact label="Risk" score={a.risk.score} lines={a.risk.lines} arr={a.arr.value} arrAssumed={a.arr.assumed} urgency={a.urgency.factor} daysToRenewal={a.urgency.days_to_renewal} priority={a.priority} />
            </Gauge>
            <Gauge label="Opportunity" value={a.opportunity.score} kind="opportunity">
              <ScoreBreakdown compact label="Opportunity" score={a.opportunity.score} lines={a.opportunity.lines} />
            </Gauge>
          </div>
          <dl className="space-y-2 rounded-xl border border-line px-3 py-2.5 text-[13px]">
            <div className="flex items-center justify-between gap-2">
              <dt className="text-muted">Confidence</dt>
              <dd>
                <Tooltip content={a.confidence.sources_missing.length ? `Missing: ${a.confidence.sources_missing.join(", ")}` : "Every data source is present"}>
                  <Badge tone={a.confidence.level === "high" ? "ok" : a.confidence.level === "medium" ? "warn" : "risk"} dot>{a.confidence.level}</Badge>
                </Tooltip>
              </dd>
            </div>
            <div className="flex items-center justify-between gap-2">
              <dt className="text-muted">Priority</dt>
              <dd className="font-medium tabular-nums">{money(a.priority)}</dd>
            </div>
            <div className="flex items-center justify-between gap-2">
              <dt className="text-muted">Urgency</dt>
              <dd className="tabular-nums">× {a.urgency.factor}</dd>
            </div>
          </dl>
        </div>

        <div className="min-w-0 space-y-4">
          {explanation && explanation.claims.length > 0 ? (
            <ExplanationBlock e={explanation} />
          ) : !asOfParam && open.length > 0 ? (
            <Callout tone="accent" title="The explanation is being written">
              Casefile writes and checks it after a case opens. Until then, the signals below show exactly what the rules found.
            </Callout>
          ) : !ordered.length ? (
            <p className="text-sm text-muted">No signals are firing for this account.</p>
          ) : null}

          {ordered.length > 0 && (
            <div className="rounded-xl border border-line">
              <button
                onClick={() => setShowSignals((s) => !s)}
                aria-expanded={showSignals}
                className="flex w-full items-center justify-between gap-2 px-4 py-2.5 text-left text-[13px] font-medium text-ink"
              >
                <span className="shrink-0 whitespace-nowrap">
                  Signals behind the score <span className="font-normal text-faint">· {ordered.length}</span>
                </span>
                <span className="flex min-w-0 items-center justify-end gap-2">
                  {!showSignals && (
                    <span className="hidden min-w-0 gap-1 overflow-hidden 2xl:flex">
                      {ordered.slice(0, 3).map((s, i) => <SignalChip key={i} s={s} />)}
                    </span>
                  )}
                  <ChevronDown className={cn("size-4 text-faint transition-transform", showSignals && "rotate-180")} aria-hidden />
                </span>
              </button>
              {showSignals && (
                <ul className="divide-y divide-line border-t border-line animate-fade-in">
                  {ordered.map((s, i) => (
                    <li key={i} className="px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <SignalChip s={s} size="md" />
                      </div>
                      <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink">{s.summary}</p>
                      {s.evidence.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {s.evidence.map((e) => <RecordChip key={e.id} label={e.label} onClick={() => evidence.open(e)} />)}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {a.blind_spots.length > 0 && (
            <Callout tone="neutral" icon={EyeOff} title="Blind spots: what Casefile cannot see here">
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                {a.blind_spots.map((b) => <li key={b.code}>{b.text}</li>)}
              </ul>
            </Callout>
          )}
        </div>
      </div>
    </Card>
  );
}
