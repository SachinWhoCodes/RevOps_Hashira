import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, ChartSpline, MessageSquareQuote } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api, type Replay, type ReplaySaid } from "../api";
import { RiskTimeline } from "../components/charts";
import { EvidenceChip } from "../components/domain";
import { Badge, ButtonLink, Card, EmptyState, ErrorNote, PageHeader, ScoreRing, Skeleton } from "../components/ui";
import { date, dateShort, label } from "../format";

export default function ReplayPage() {
  const { accountId } = useParams();
  const replay = useQuery({ queryKey: ["replay", accountId], queryFn: () => api<Replay>(`/backtest/replay/${accountId}?weeks=104`) });
  const r = replay.data;
  const [picked, setPicked] = useState<number | null>(null);
  const selected = picked ?? Math.max((r?.points.length ?? 1) - 1, 0); // latest week until one is picked
  const point = r?.points[selected];
  const said = useQuery({
    queryKey: ["replay-said", accountId, point?.date],
    queryFn: () => api<ReplaySaid>(`/backtest/replay/${accountId}/explain?as_of=${point!.date}`),
    enabled: !!point,
  });
  return (
    <>
      <PageHeader
        back={{ to: "/backtest", label: "Back-test" }}
        title={r ? `Replay · ${r.account.name}` : "Replay"}
        description="The risk score each week, computed only from what was known on that date. Hover to inspect, click or drag the slider to choose a week."
      />
      {replay.isLoading && <Skeleton className="h-72 w-full rounded-xl" />}
      {replay.error && <ErrorNote error={replay.error} />}
      {r && !r.points.length && (
        <Card>
          <EmptyState icon={ChartSpline} title="Not enough history to replay" />
        </Card>
      )}
      {r && r.points.length > 0 && point && (
        <div className="space-y-4">
          <Card title="Risk over time" icon={ChartSpline} description={`${r.points.length} weeks · case threshold ${r.flag_at}`}>
            <RiskTimeline points={r.points} flagAt={r.flag_at} outcomes={r.outcomes} selected={selected} onSelect={setPicked} />
            <div className="mt-4 flex items-center gap-4">
              <span className="w-16 text-xs text-faint">{dateShort(r.points[0].date)}</span>
              <input
                type="range"
                min={0}
                max={r.points.length - 1}
                value={selected}
                onChange={(e) => setPicked(Number(e.target.value))}
                aria-label="Choose a week"
                className="h-1.5 flex-1 cursor-pointer accent-[var(--c-accent)]"
              />
              <span className="w-16 text-right text-xs text-faint">{dateShort(r.points[r.points.length - 1].date)}</span>
            </div>
            {r.excluded_signals.length > 0 && (
              <p className="mt-3 text-xs text-faint">Same rules as the back-test: {r.excluded_signals.map(label).join(", ")} left out (AI email labels exist only for recent emails).</p>
            )}
          </Card>

          <Card
            title={`What Casefile would have said on ${date(point.date)}`}
            icon={MessageSquareQuote}
            actions={
              <ButtonLink to={`/accounts/${r.account.id}?as_of=${point.date}`} size="xs" variant="ghost" iconRight={ArrowUpRight}>
                Open the account as of this date
              </ButtonLink>
            }
          >
            <div className="flex gap-5">
              <ScoreRing value={point.risk} size={64} stroke={5} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  {point.flagged ? <Badge tone="risk" dot>A risk case would have been open</Badge> : <Badge>No case</Badge>}
                  {point.opportunity > 0 && <Badge tone="ok">Opportunity {point.opportunity}</Badge>}
                </div>
                {said.isLoading && (
                  <div className="mt-3 space-y-2">
                    <Skeleton className="h-4 w-2/3" />
                    <Skeleton className="h-3.5 w-full" />
                    <Skeleton className="h-3.5 w-5/6" />
                  </div>
                )}
                {said.data && (
                  <div key={point.date} className="animate-fade-in">
                    <div className="mt-3 text-[15px] font-semibold text-ink">{said.data.headline}</div>
                    <ul className="mt-2 space-y-1.5">
                      {said.data.claims.map((c, i) => (
                        <li key={i} className="text-[13.5px] leading-relaxed text-ink">
                          {c.text}
                          {c.evidence.map((e) => <EvidenceChip key={e.eid} e={e} />)}
                        </li>
                      ))}
                      {!said.data.claims.length && <li className="text-[13.5px] text-muted">No signals were firing.</li>}
                    </ul>
                    <p className="mt-3 text-xs text-faint">{said.data.source}</p>
                  </div>
                )}
              </div>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
