import { useQuery } from "@tanstack/react-query";
import { Bot, ChevronRight, Download, Link2, MessageSquare, ScrollText, ShieldCheck, ShieldX, Sparkles, User, Zap, type LucideIcon } from "lucide-react";
import { Fragment, useState } from "react";
import { api, type AuditRow } from "../api";
import { CursorPager } from "../components/table/Pagination";
import { Badge, Button, ButtonAnchor, Callout, EmptyState, ErrorNote, PageHeader, SearchInput, Select, Skeleton, Tooltip } from "../components/ui";
import { ago, dateTime, label, sentence } from "../format";
import { cn } from "../lib/cn";
import { useLocalPref, useUrlBatch, useUrlState } from "../lib/hooks";

type Verify = { ok: boolean; checked: number; total?: number; broken_at_seq?: number };

const ACTIONS = [
  ["", "Everything"],
  ["change_", "Change sets & decisions"],
  ["execution_", "Executions"],
  ["case_", "Cases"],
  ["assessment_", "Assessments"],
  ["explanation_", "AI explanations"],
  ["injection", "Injection flags"],
  ["digest_", "Slack digests"],
  ["user_", "People & logins"],
] as const;

const actorIcon: Record<string, LucideIcon> = { user: User, system: Bot, worker: Bot, executor: Zap, ai: Sparkles, slack: MessageSquare };

function detailText(d: Record<string, unknown>) {
  return Object.entries(d)
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : typeof v === "object" && v ? JSON.stringify(v) : String(v)}`)
    .join("  ");
}

export default function AuditPage() {
  const [action] = useUrlState("action", "");
  const [entity] = useUrlState("entity", "");
  const batch = useUrlBatch();
  const [size, setSize] = useLocalPref("casefile.audit.size", 50);
  const [cursors, setCursors] = useState<number[]>([]); // before_seq for each page after the first
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const [verify, setVerify] = useState<Verify | null>(null);
  const [checking, setChecking] = useState(false);

  const before = cursors.at(-1);
  const filters = { ...(action ? { action } : {}), ...(entity.trim() ? { entity_id: entity.trim() } : {}) };
  const query = new URLSearchParams({ limit: String(size + 1), ...filters, ...(before ? { before_seq: String(before) } : {}) }).toString();
  const exportQuery = new URLSearchParams({ limit: "5000", ...filters }).toString();
  const rows = useQuery({ queryKey: ["audit", query], queryFn: () => api<AuditRow[]>(`/audit?${query}`), placeholderData: (prev) => prev });
  const page = rows.data?.slice(0, size) ?? [];
  const hasOlder = (rows.data?.length ?? 0) > size;

  const setFilter = (changes: Record<string, string | null>) => {
    setCursors([]);
    batch(changes);
  };
  const run = async () => {
    setChecking(true);
    try {
      setVerify(await api<Verify>("/audit/verify"));
    } finally {
      setChecking(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Audit log"
        description="A write-once record of what Casefile did and why. Each entry is chained to the one before it by a hash, so any edit to history is detectable."
        actions={
          <>
            <ButtonAnchor href={`/api/audit?${exportQuery}&format=csv`} icon={Download}>CSV</ButtonAnchor>
            <ButtonAnchor href={`/api/audit?${exportQuery}&format=download`} icon={Download}>JSON</ButtonAnchor>
            <Button variant="primary" icon={ShieldCheck} loading={checking} onClick={run}>
              Verify chain
            </Button>
          </>
        }
      />

      {verify && (
        <Callout
          className="mb-4"
          tone={verify.ok ? "ok" : "risk"}
          icon={verify.ok ? ShieldCheck : ShieldX}
          title={verify.ok ? `Chain intact · ${verify.checked.toLocaleString()} entries verified` : `Chain broken at entry #${verify.broken_at_seq}`}
          onDismiss={() => setVerify(null)}
        >
          {verify.ok
            ? "Every entry's hash matches its content and the entry before it. Nothing has been altered or removed."
            : "An entry no longer matches its hash: history was changed after it was written. Investigate before trusting anything after this point."}
        </Callout>
      )}

      <section className="overflow-hidden rounded-xl border border-line bg-panel shadow-xs animate-fade-up">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2.5">
          <Select selectSize="sm" className="w-56" value={action} onChange={(e) => setFilter({ action: e.target.value || null })} aria-label="Kind of entry">
            {ACTIONS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </Select>
          <SearchInput value={entity} onChange={(v) => setFilter({ entity: v || null })} placeholder="Filter by record id (line, case, account…)" className="w-full sm:w-80" />
          {rows.isFetching && <span className="ml-auto text-xs text-faint">Loading…</span>}
        </div>
        {rows.error ? (
          <div className="p-4"><ErrorNote error={rows.error} /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13.5px]">
              <thead>
                <tr className="border-b border-line bg-subtle text-left text-[12px] text-muted">
                  <th className="w-8" />
                  <th className="px-3 py-2.5 font-medium">#</th>
                  <th className="px-3 py-2.5 font-medium">When</th>
                  <th className="px-3 py-2.5 font-medium">Who</th>
                  <th className="px-3 py-2.5 font-medium">What</th>
                  <th className="px-3 py-2.5 font-medium">Details</th>
                  <th className="px-3 py-2.5 font-medium">Hash</th>
                </tr>
              </thead>
              <tbody key={query} className="animate-fade-in">
                {rows.isLoading &&
                  Array.from({ length: 8 }, (_, i) => (
                    <tr key={i} className="border-b border-line">
                      <td colSpan={7} className="px-4 py-3"><Skeleton className="h-4 w-full" /></td>
                    </tr>
                  ))}
                {page.map((r) => {
                  const kind = r.actor.split(":")[0];
                  const Icon = actorIcon[kind] ?? Bot;
                  const isOpen = !!open[r.seq];
                  return (
                    <Fragment key={r.seq}>
                      <tr className={cn("border-b border-line align-top transition-colors hover:bg-hover/60", isOpen && "bg-hover/40")}>
                        <td className="pt-2.5 pl-3">
                          <button
                            onClick={() => setOpen((o) => ({ ...o, [r.seq]: !isOpen }))}
                            aria-expanded={isOpen}
                            aria-label={isOpen ? "Hide entry" : "Show entry"}
                            className="grid size-6 place-items-center rounded-md text-faint hover:bg-hover hover:text-ink"
                          >
                            <ChevronRight className={cn("size-4 transition-transform", isOpen && "rotate-90")} />
                          </button>
                        </td>
                        <td className="px-3 py-3 font-mono text-xs text-faint tabular-nums">{r.seq}</td>
                        <td className="px-3 py-3 whitespace-nowrap text-muted">
                          <Tooltip content={dateTime(r.ts)}>
                            <span>{ago(r.ts)}</span>
                          </Tooltip>
                        </td>
                        <td className="px-3 py-3">
                          <span className="inline-flex items-center gap-1.5 text-muted capitalize">
                            <Icon className="size-3.5" aria-hidden />
                            {kind}
                          </span>
                        </td>
                        <td className="px-3 py-3">
                          <div className="font-medium text-ink">{sentence(label(r.action))}</div>
                          {r.entity_id && (
                            <button onClick={() => setFilter({ entity: r.entity_id })} className="mt-0.5 font-mono text-[11px] text-faint hover:text-accent-fg" title="Show every entry for this record">
                              {r.entity_type} {r.entity_id.slice(0, 8)}
                            </button>
                          )}
                          {r.model && <div className="mt-0.5 text-[11px] text-faint">{r.model} · {r.prompt_version}</div>}
                        </td>
                        <td className="max-w-md px-3 py-3">
                          <div className="truncate font-mono text-xs text-muted">{detailText(r.details) || "—"}</div>
                          {r.evidence_count > 0 && <Badge className="mt-1">{r.evidence_count} evidence</Badge>}
                        </td>
                        <td className="px-3 py-3 font-mono text-xs text-faint">{r.hash.slice(0, 10)}</td>
                      </tr>
                      {isOpen && (
                        <tr className="border-b border-line bg-subtle/60">
                          <td />
                          <td colSpan={6} className="px-3 py-4 animate-fade-in">
                            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
                              <pre className="max-h-72 overflow-auto rounded-lg border border-line bg-panel p-3 font-mono text-xs leading-relaxed text-ink">{JSON.stringify(r.details, null, 2)}</pre>
                              <dl className="space-y-2 text-xs">
                                <div><dt className="text-faint">Actor</dt><dd className="font-mono text-ink break-all">{r.actor}</dd></div>
                                {r.approval_id && <div><dt className="text-faint">Approval</dt><dd className="font-mono text-ink break-all">{r.approval_id}</dd></div>}
                                <div>
                                  <dt className="flex items-center gap-1 text-faint"><Link2 className="size-3" /> Chained to</dt>
                                  <dd className="font-mono break-all text-muted">{r.prev_hash ?? "(first entry)"}</dd>
                                </div>
                                <div><dt className="text-faint">This entry's hash</dt><dd className="font-mono break-all text-ink">{r.hash}</dd></div>
                                <div><dt className="text-faint">Written</dt><dd className="text-ink">{dateTime(r.ts)}</dd></div>
                              </dl>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
            {rows.data && !page.length && <EmptyState compact icon={ScrollText} title="No entries" description="Nothing matches these filters." />}
          </div>
        )}
        {rows.data && page.length > 0 && (
          <div className="border-t border-line">
            <CursorPager
              label={
                <>
                  Entries <span className="font-medium text-ink">#{page[0].seq}</span> to <span className="font-medium text-ink">#{page[page.length - 1].seq}</span>
                  {cursors.length > 0 && <span className="text-faint"> · page {cursors.length + 1}</span>}
                </>
              }
              hasNewer={cursors.length > 0}
              hasOlder={hasOlder}
              onNewer={() => setCursors((c) => c.slice(0, -1))}
              onOlder={() => setCursors((c) => [...c, page[page.length - 1].seq])}
              pageSize={size}
              onPageSize={(n) => {
                setSize(n);
                setCursors([]);
              }}
            />
          </div>
        )}
      </section>
    </>
  );
}
