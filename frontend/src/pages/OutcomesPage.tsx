import { useQuery } from "@tanstack/react-query";
import { Activity, Info } from "lucide-react";
import { api, type PlaysTable } from "../api";
import { StackedBar } from "../components/charts";
import { Badge, Callout, Card, EmptyState, ErrorNote, PageHeader, Skeleton } from "../components/ui";
import { label, plural } from "../format";
import { cn } from "../lib/cn";

type Checkpoint = PlaysTable["plays"][number]["checkpoints"][number];

function CheckpointCell({ c }: { c: Checkpoint }) {
  if (c.measured === 0) {
    return (
      <div>
        <StackedBar segments={[{ key: "p", value: c.pending, tone: "neutral", label: "pending" }]} />
        <div className="mt-1.5 text-xs text-faint">{c.pending ? `${c.pending} pending` : "—"}</div>
      </div>
    );
  }
  const change = c.median_risk_change ?? 0;
  return (
    <div>
      <StackedBar
        segments={[
          { key: "i", value: c.improved, tone: "ok", label: "improved" },
          { key: "n", value: c.no_change, tone: "neutral", label: "no change" },
          { key: "w", value: c.worse, tone: "risk", label: "worse" },
          { key: "p", value: c.pending, tone: "neutral", label: "pending" },
        ]}
      />
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
        <Badge tone="ok">{c.improved} improved</Badge>
        {c.no_change > 0 && <Badge>{c.no_change} no change</Badge>}
        {c.worse > 0 && <Badge tone="risk">{c.worse} worse</Badge>}
      </div>
      <dl className="mt-2 space-y-0.5 text-xs text-muted">
        <div>
          Median risk <span className={cn("font-medium tabular-nums", change < 0 ? "text-ok-fg" : change > 0 ? "text-risk-fg" : "text-ink")}>{change > 0 ? "+" : ""}{c.median_risk_change ?? "—"}</span>
          <span className="text-faint"> · n={c.measured}</span>
        </div>
        <div>Champion replied {c.champion_replied}/{c.measured} · meeting booked {c.meeting_booked}/{c.measured}</div>
        {c.pending > 0 && <div className="text-faint">{c.pending} still pending</div>}
      </dl>
    </div>
  );
}

// Plays that work: how executed change sets turned out at each checkpoint, with n shown.
export default function OutcomesPage() {
  const q = useQuery({ queryKey: ["plays"], queryFn: () => api<PlaysTable>("/plays") });
  const d = q.data;
  return (
    <>
      <PageHeader
        title="Plays that work"
        description="What happened after approved plays were carried out, checked at 7, 14 and 30 days through the same rules that opened the case."
      />
      <Callout className="mb-4" tone="neutral" icon={Info} title="Measured after, not proof of cause">
        Customers act for their own reasons. A checkpoint shows what changed after a play, not that the play caused it. On the synthetic company
        most checkpoints read “no change” — only what is read back from the sandbox HubSpot portal moves.
      </Callout>
      {q.error && <ErrorNote error={q.error} />}
      {q.isLoading && <Skeleton className="h-64 w-full rounded-xl" />}
      {d && !d.plays.length && (
        <Card>
          <EmptyState icon={Activity} title="No play has been carried out yet" description="Outcomes appear here once an approved change set is executed." />
        </Card>
      )}
      {d && d.plays.length > 0 && (
        <section className="overflow-hidden rounded-xl border border-line bg-panel shadow-xs animate-fade-up">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[46rem] text-[13.5px]">
              <thead>
                <tr className="border-b border-line bg-subtle text-left text-[12px] text-muted">
                  <th className="px-5 py-2.5 font-medium">Play</th>
                  {d.check_days.map((day) => (
                    <th key={day} className="px-5 py-2.5 font-medium">After {day} days</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.plays.map((p) => (
                  <tr key={p.play} className="border-b border-line align-top last:border-0">
                    <td className="px-5 py-4">
                      <div className="font-semibold text-ink capitalize">{label(p.play)}</div>
                      <div className="mt-0.5 text-xs text-muted">{plural(p.sets, "change set")} executed</div>
                    </td>
                    {p.checkpoints.map((c) => (
                      <td key={c.day} className="w-[26%] px-5 py-4">
                        <CheckpointCell c={c} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
