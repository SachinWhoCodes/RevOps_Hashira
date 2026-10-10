import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ConnectionRow } from "../api";
import { useMe } from "../App";
import { Badge, ErrorNote, Loading, PageHeader } from "../components/ui";
import { ago, dateTime, label } from "../format";

const freshTone = { fresh: "ok", stale: "warn", never_synced: "neutral" } as const;

export default function ConnectionsPage() {
  const me = useMe();
  const qc = useQueryClient();
  const conns = useQuery({
    queryKey: ["connections"],
    queryFn: () => api<ConnectionRow[]>("/connections"),
    refetchInterval: 10_000,
  });
  const sync = useMutation({
    mutationFn: (id: string) => api(`/connections/${id}/sync`, { json: {} }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["connections"] }),
  });
  return (
    <>
      <PageHeader title="Connections" subtitle="Read-only data sources. Write access lives in a separate executor (Phase 4)." />
      {conns.isLoading && <Loading />}
      {conns.error && <ErrorNote error={conns.error} />}
      <div className="grid gap-3 md:grid-cols-2">
        {conns.data?.map((c) => (
          <div key={c.id} className="rounded-lg border border-line bg-panel p-4">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="font-medium">{label(c.kind)} · {c.name}</div>
                <div className="text-xs text-faint">{c.zone} zone · every {c.interval_minutes} min · {c.raw_records.toLocaleString()} raw records</div>
              </div>
              <Badge tone={c.status === "error" ? "risk" : freshTone[c.freshness.state]}>
                {c.status === "error" ? "error" : c.freshness.state === "stale" ? `stale since ${ago(c.freshness.stale_since)}` : label(c.freshness.state)}
              </Badge>
            </div>
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              <dt className="text-muted">Last success</dt><dd>{dateTime(c.last_success_at)}</dd>
              {c.last_run && (
                <>
                  <dt className="text-muted">Last run</dt>
                  <dd>{c.last_run.status} · fetched {c.last_run.fetched.toLocaleString()} · new {c.last_run.new.toLocaleString()}</dd>
                </>
              )}
              {c.last_error && (
                <>
                  <dt className="text-muted">Last error</dt>
                  <dd className="text-risk">{c.last_error} ({c.consecutive_failures}×)</dd>
                </>
              )}
            </dl>
            {me.data?.role === "admin" && (
              <button onClick={() => sync.mutate(c.id)} disabled={sync.isPending}
                className="mt-3 rounded-md border border-line px-3 py-1 text-sm hover:bg-canvas disabled:opacity-60">
                Sync now
              </button>
            )}
          </div>
        ))}
      </div>
      {sync.isSuccess && <p className="mt-3 text-sm text-muted">Queued. The worker picks it up within a few seconds.</p>}
    </>
  );
}
