import { useQuery } from "@tanstack/react-query";
import { api, type PlaysTable } from "../api";
import { Badge, ErrorNote, Loading, PageHeader, Panel } from "../components/ui";
import { label } from "../format";

// Plays that work: how executed change sets turned out at each checkpoint, with n shown.
export default function OutcomesPage() {
  const q = useQuery({ queryKey: ["plays"], queryFn: () => api<PlaysTable>("/plays") });
  return (
    <>
      <PageHeader
        title="Plays that work"
        subtitle="What happened after approved plays were carried out, measured at 7, 14 and 30 days. Measured after, not proof of cause."
      />
      {q.isLoading && <Loading />}
      {q.error && <ErrorNote error={q.error} />}
      {q.data && (
        <Panel>
          {!q.data.plays.length ? (
            <p className="text-sm text-muted">No play has been carried out yet. Outcomes appear once an approved change set is executed.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[40rem] text-sm">
                <thead>
                  <tr className="text-left text-xs text-faint">
                    <th className="pb-2 font-normal">Play</th>
                    <th className="pb-2 font-normal">Sets</th>
                    {q.data.check_days.map((d) => (
                      <th key={d} className="pb-2 font-normal">Day {d}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {q.data.plays.map((p) => (
                    <tr key={p.play} className="border-t border-line align-top">
                      <td className="py-2 pr-3 font-medium">{label(p.play)}</td>
                      <td className="py-2 pr-3 tabular-nums">{p.sets}</td>
                      {p.checkpoints.map((c) => (
                        <td key={c.day} className="py-2 pr-3 text-xs">
                          {c.measured === 0 ? (
                            <span className="text-faint">{c.pending} pending</span>
                          ) : (
                            <div className="space-y-0.5">
                              <div className="flex flex-wrap gap-1">
                                <Badge tone="ok">{c.improved} improved</Badge>
                                <Badge>{c.no_change} no change</Badge>
                                {c.worse > 0 && <Badge tone="risk">{c.worse} worse</Badge>}
                              </div>
                              <div className="text-muted">
                                n={c.measured} · median risk {c.median_risk_change! > 0 ? "+" : ""}{c.median_risk_change}
                                {" · "}champion replied {c.champion_replied}/{c.measured} · meeting booked {c.meeting_booked}/{c.measured}
                                {c.pending > 0 && ` · ${c.pending} pending`}
                              </div>
                            </div>
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="mt-3 text-xs text-faint">
            Label: observed on the sandbox tools. The synthetic company does not change after a play except for what is read
            back from the sandbox HubSpot portal, so most checkpoints will read "no change" until real data is connected.
          </p>
        </Panel>
      )}
    </>
  );
}
