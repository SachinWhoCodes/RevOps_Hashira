import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Inbox, Zap } from "lucide-react";
import { api, type ExecutorStatus, type InboxRow, type ItemStatus } from "../api";
import { StackedBar } from "../components/charts";
import { DataTable, type Column } from "../components/table/DataTable";
import { AccountMark, Badge, PageHeader, Segmented, StatusDot, Tooltip, type Tone } from "../components/ui";
import { ago, dateTime, label } from "../format";
import { useUrlBatch, useUrlState } from "../lib/hooks";
import { useMe } from "../lib/session";

const STATUS: { s: ItemStatus; text: string; tone: Tone }[] = [
  { s: "proposed", text: "to decide", tone: "accent" },
  { s: "queued", text: "queued", tone: "warn" },
  { s: "executing", text: "writing", tone: "warn" },
  { s: "succeeded", text: "done", tone: "ok" },
  { s: "would_execute", text: "shadow", tone: "ok" },
  { s: "failed", text: "failed", tone: "risk" },
  { s: "conflict", text: "conflict", tone: "risk" },
  { s: "blocked", text: "blocked", tone: "risk" },
  { s: "rejected", text: "rejected", tone: "neutral" },
];

type View = "decide" | "progress" | "problems" | "done" | "all";
const n = (r: InboxRow, ...s: ItemStatus[]) => s.reduce((t, k) => t + (r.counts[k] ?? 0), 0);
const viewOf = (r: InboxRow): Exclude<View, "all"> =>
  n(r, "proposed") ? "decide" : n(r, "failed", "conflict", "blocked") ? "problems" : n(r, "queued", "executing", "approved", "edited_approved") ? "progress" : "done";

function ExecutorPanel() {
  const s = useQuery({ queryKey: ["executor"], queryFn: () => api<ExecutorStatus>("/executor/status"), refetchInterval: 15_000 });
  const d = s.data;
  if (!d) return null;
  const tone: Tone = !d.targets.length ? "neutral" : d.running ? (d.shadow ? "accent" : "ok") : "warn";
  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-line bg-panel px-4 py-3 shadow-xs animate-fade-up">
      <div className="flex items-center gap-2.5">
        <span className="grid size-8 place-items-center rounded-lg bg-subtle text-muted ring-1 ring-line"><Zap className="size-4" aria-hidden /></span>
        <div>
          <div className="flex items-center gap-2 text-[13px] font-medium">
            <StatusDot tone={tone} pulse={d.running && !d.shadow} />
            {!d.targets.length ? "No write tools connected" : d.running ? (d.shadow ? "Executor running in shadow mode" : "Executor online") : "Executor offline"}
          </div>
          <div className="text-xs text-muted">
            {d.last_heartbeat ? `Heartbeat ${ago(d.last_heartbeat)}` : "Never seen"} · the only process holding write credentials; it writes exactly what was approved
          </div>
        </div>
      </div>
      {d.targets.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {d.targets.map((t) => (
            <Badge key={`${t.kind}${t.name}`} tone={t.writer_loaded ? "ok" : "warn"} dot>
              {label(t.kind)} · {t.name}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}

export default function InboxPage() {
  const me = useMe();
  const inbox = useQuery({ queryKey: ["inbox"], queryFn: () => api<InboxRow[]>("/inbox"), refetchInterval: 10_000 });
  const [view] = useUrlState("view", "decide") as [View, (v: string) => void];
  const batch = useUrlBatch();
  const rows = inbox.data;
  const count = (v: View) => (rows ?? []).filter((r) => v === "all" || viewOf(r) === v).length;
  const filtered = rows?.filter((r) => view === "all" || viewOf(r) === view);

  const columns: Column<InboxRow>[] = [
    {
      id: "account",
      header: "Account",
      sortValue: (r) => r.account.name,
      csv: (r) => r.account.name,
      cell: (r) => (
        <div className="flex min-w-0 items-center gap-3">
          <AccountMark name={r.account.name} />
          <div className="min-w-0 max-w-md">
            <div className="truncate font-medium text-ink">{r.account.name}</div>
            <div className="truncate text-xs text-muted">{r.headline ?? "Signals to review"}</div>
          </div>
        </div>
      ),
    },
    {
      id: "case",
      header: "Case",
      sortValue: (r) => r.score ?? -1,
      firstSort: "desc",
      csv: (r) => `${r.case_kind} ${r.score ?? ""}`,
      cell: (r) => (
        <Badge tone={r.case_kind === "risk" ? "risk" : "ok"} dot>
          {r.case_kind} {r.score ?? ""}
        </Badge>
      ),
    },
    { id: "play", header: "Play", sortValue: (r) => r.play, csv: (r) => r.play, cell: (r) => <span className="text-muted capitalize">{label(r.play)}</span> },
    {
      id: "lines",
      header: "Lines",
      csv: (r) => STATUS.filter((x) => r.counts[x.s]).map((x) => `${r.counts[x.s]} ${x.text}`).join("; "),
      cell: (r) => {
        const present = STATUS.filter((x) => r.counts[x.s]);
        return (
          <Tooltip content={present.map((x) => `${r.counts[x.s]} ${x.text}`).join(" · ") || "No lines"}>
            <div className="w-40">
              <StackedBar segments={present.map((x) => ({ key: x.s, value: r.counts[x.s] ?? 0, tone: x.tone, label: x.text }))} />
              <div className="mt-1.5 truncate text-xs text-muted">
                {present.slice(0, 2).map((x) => `${r.counts[x.s]} ${x.text}`).join(" · ")}
                {present.length > 2 && " …"}
              </div>
            </div>
          </Tooltip>
        );
      },
    },
    {
      id: "created",
      header: "Proposed",
      sortValue: (r) => r.created_at,
      firstSort: "desc",
      csv: (r) => r.created_at,
      cell: (r) => <span className="whitespace-nowrap text-muted" title={dateTime(r.created_at)}>{ago(r.created_at)}</span>,
    },
    {
      id: "go",
      header: "",
      hideable: false,
      align: "right",
      cell: (r) => (
        <span className="inline-flex items-center gap-1 text-[13px] font-medium text-accent-fg">
          {viewOf(r) === "decide" ? "Review" : "Open"} <ArrowRight className="size-3.5" aria-hidden />
        </span>
      ),
    },
  ];

  const notLive = me.data && me.data.org_mode === "setup";
  return (
    <>
      <PageHeader
        title="Approvals"
        description="Proposed actions, highest-priority case first. Nothing is written to your tools until a person approves the line."
      />
      <ExecutorPanel />
      <DataTable
        id="inbox"
        rows={filtered}
        loading={inbox.isLoading}
        error={inbox.error}
        columns={columns}
        rowKey={(r) => r.id}
        rowHref={(r) => `/accounts/${r.account.id}#plan`}
        search={{ placeholder: "Search accounts and plays", text: (r) => `${r.account.name} ${r.play} ${r.headline ?? ""}` }}
        filters={
          <Segmented<View>
            size="xs"
            label="Status"
            value={view}
            onChange={(v) => batch({ view: v === "decide" ? null : v, page: null })}
            options={[
              { value: "decide", label: "To decide", count: rows ? count("decide") : undefined },
              { value: "progress", label: "In progress", count: rows ? count("progress") : undefined },
              { value: "problems", label: "Problems", count: rows ? count("problems") : undefined },
              { value: "done", label: "Done", count: rows ? count("done") : undefined },
              { value: "all", label: "All", count: rows?.length },
            ]}
          />
        }
        csvName="casefile-approvals"
        noun="change sets"
        empty={{
          icon: Inbox,
          title: view === "decide" ? "You're all caught up" : "Nothing here",
          description: notLive
            ? "Proposals start once the company is in shadow mode."
            : view === "decide"
              ? "New proposals appear when a case opens and its explanation is written."
              : "No change sets in this state.",
        }}
      />
    </>
  );
}
