import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type ExecutorStatus, type InboxRow, type ItemStatus } from "../api";
import { Badge, ErrorNote, Loading, PageHeader } from "../components/ui";
import { dateTime, label } from "../format";

const shown: [ItemStatus, string, "accent" | "warn" | "ok" | "risk"][] = [
  ["proposed", "to decide", "accent"],
  ["queued", "queued", "warn"],
  ["executing", "writing", "warn"],
  ["succeeded", "done", "ok"],
  ["would_execute", "shadow", "ok"],
  ["failed", "failed", "risk"],
  ["conflict", "conflict", "risk"],
  ["blocked", "blocked", "risk"],
];

export function ExecutorBadge() {
  const s = useQuery({ queryKey: ["executor"], queryFn: () => api<ExecutorStatus>("/executor/status"), refetchInterval: 15000 });
  if (!s.data) return null;
  if (!s.data.targets.length) return <Badge tone="warn" title="Run the executor's sandbox-setup">no write tools connected</Badge>;
  return (
    <Badge tone={s.data.running ? (s.data.shadow ? "warn" : "ok") : "warn"} title={s.data.last_heartbeat ? `last heartbeat ${dateTime(s.data.last_heartbeat)}` : "never seen"}>
      executor {s.data.running ? (s.data.shadow ? "running in shadow mode" : "running") : "not running"}
    </Badge>
  );
}

export default function InboxPage() {
  const inbox = useQuery({ queryKey: ["inbox"], queryFn: () => api<InboxRow[]>("/inbox"), refetchInterval: 10000 });
  return (
    <>
      <PageHeader title="Approvals" subtitle="Change sets waiting for a decision, highest-priority case first. Nothing is written until a person approves the line.">
        <ExecutorBadge />
      </PageHeader>
      {inbox.isLoading && <Loading />}
      {inbox.error && <ErrorNote error={inbox.error} />}
      {inbox.data && !inbox.data.length && <p className="text-sm text-muted">Nothing waiting. New proposals appear when a case opens and its explanation is written.</p>}
      <ul className="space-y-2">
        {inbox.data?.map((row) => (
          <li key={row.id} className="rounded-lg border border-line bg-panel px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <Link to={`/accounts/${row.account.id}#plan`} className="font-medium text-accent hover:underline">{row.account.name}</Link>
                <span className="ml-2 text-sm text-muted">{row.headline ?? "signals to review"}</span>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge tone={row.case_kind === "risk" ? "risk" : "ok"}>{row.case_kind} {row.score ?? ""}</Badge>
                <Badge>{label(row.play)}</Badge>
                {shown.filter(([s]) => row.counts[s]).map(([s, text, tone]) => (
                  <Badge key={s} tone={tone}>{row.counts[s]} {text}</Badge>
                ))}
              </div>
            </div>
            <div className="mt-1 text-xs text-faint">proposed {dateTime(row.created_at)} · {label(row.status)}</div>
          </li>
        ))}
      </ul>
    </>
  );
}
