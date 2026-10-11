import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Box,
  Briefcase,
  Check,
  ChevronDown,
  ExternalLink,
  Hash,
  Mail,
  MessageSquare,
  Pencil,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  WandSparkles,
  X,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { api, type ChangeItem, type ChangeSet, type ItemStatus } from "../api";
import { date, dateTime, label, sentence } from "../format";
import { cn } from "../lib/cn";
import { StackedBar } from "./charts";
import { EvidenceChip } from "./domain";
import { Badge, Button, Callout, Card, EmptyState, ErrorNote, Field, Input, Select, Skeleton, Textarea, Tooltip, useToast, type Tone } from "./ui";

const statusTone: Record<ItemStatus, Tone> = {
  proposed: "accent",
  approved: "accent",
  edited_approved: "accent",
  queued: "warn",
  executing: "warn",
  succeeded: "ok",
  would_execute: "ok",
  failed: "risk",
  conflict: "risk",
  blocked: "risk",
  rejected: "neutral",
  superseded: "neutral",
};

const statusText: Partial<Record<ItemStatus, string>> = {
  proposed: "Waiting for you",
  edited_approved: "Edited & approved",
  queued: "Approved · queued",
  executing: "Writing…",
  succeeded: "Done",
  would_execute: "Shadow: would write",
};

const systems: Record<string, { name: string; icon: LucideIcon; tile: string }> = {
  hubspot: { name: "HubSpot", icon: Briefcase, tile: "bg-orange-500/12 text-orange-600 dark:text-orange-300" },
  gmail: { name: "Gmail", icon: Mail, tile: "bg-rose-500/12 text-rose-600 dark:text-rose-300" },
  slack: { name: "Slack", icon: MessageSquare, tile: "bg-violet-500/12 text-violet-600 dark:text-violet-300" },
  casefile: { name: "Casefile", icon: Box, tile: "bg-accent-soft text-accent-fg" },
};

// Task and note bodies are stored as simple HTML for HubSpot; show them as text (never as markup).
function htmlText(html: string): string {
  const spaced = html.replace(/<\/(p|li)>/g, "\n").replace(/<li>/g, "• ");
  return (new DOMParser().parseFromString(spaced, "text/html").body.textContent ?? "").trim();
}

const str = (v: unknown) => (v == null || v === "" ? "" : String(v));

function Target({ item }: { item: ChangeItem }) {
  const r = item.target_ref;
  if (item.target_system === "slack") {
    return <>DM to {r.substituted ? <>workspace owner <span className="text-faint">(sandbox, meant for {str(r.intended_for)})</span></> : str(r.intended_for)}</>;
  }
  if (item.target_system === "gmail") return <>Draft in {str(r.mailbox) || "the connected mailbox"} · never sent</>;
  if (item.target_system === "hubspot") {
    const link = (key: "company" | "deal") =>
      r[key] ? (
        item.links[key] ? (
          <a className="inline-flex items-center gap-0.5 text-accent-fg hover:underline" href={item.links[key]} target="_blank" rel="noreferrer">
            {key} {str(r[key])} <ExternalLink className="size-3" />
          </a>
        ) : (
          <span>{key} {str(r[key])}</span>
        )
      ) : null;
    return (
      <span className="inline-flex flex-wrap items-center gap-x-2">
        {link("company")}
        {link("deal")}
        {r.mirror ? <span className="text-faint">(sandbox copy)</span> : null}
      </span>
    );
  }
  return <>Inside Casefile</>;
}

function Content({ item, payload }: { item: ChangeItem; payload: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  switch (item.action_type) {
    case "crm_task":
      return (
        <div>
          <div className="text-[14px] font-medium text-ink">{str(payload.subject)}</div>
          <div className="mt-0.5 text-xs text-muted">
            Due {date(str(payload.due_date))} · {label(str(payload.priority)).toLowerCase()} priority
          </div>
          {str(payload.body) && (
            <>
              <button onClick={() => setOpen(!open)} className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-muted hover:text-ink">
                Task body <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
              </button>
              {open && <p className="mt-2 rounded-lg bg-subtle p-3 text-[13px] leading-relaxed whitespace-pre-wrap text-muted animate-fade-in">{htmlText(str(payload.body))}</p>}
            </>
          )}
        </div>
      );
    case "crm_note":
      return <p className="rounded-lg bg-subtle p-3 text-[13px] leading-relaxed whitespace-pre-wrap text-ink">{htmlText(str(payload.body))}</p>;
    case "crm_next_step": {
      const before = str(item.before.hs_next_step);
      return (
        <div>
          <div className="grid items-center gap-2 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
            <div className="rounded-lg border border-dashed border-line px-3 py-2">
              <div className="text-[10.5px] font-semibold tracking-wide text-faint uppercase">Now</div>
              <div className={cn("mt-0.5 text-[13px]", before ? "text-muted line-through decoration-risk/40" : "text-faint italic")}>{before || "(empty)"}</div>
            </div>
            <ArrowRight className="mx-auto size-4 rotate-90 text-faint sm:rotate-0" aria-hidden />
            <div className="rounded-lg border border-ok/30 bg-ok-soft px-3 py-2">
              <div className="text-[10.5px] font-semibold tracking-wide text-ok-fg uppercase">After</div>
              <div className="mt-0.5 text-[13px] font-medium text-ink">{str(payload.value)}</div>
            </div>
          </div>
          <p className="mt-2 text-xs text-faint">
            Current value read {item.before.source === "hubspot_live" ? "live from HubSpot" : "from Casefile's copy"} {dateTime(str(item.before.read_at))}. The write is refused if it has changed since.
          </p>
        </div>
      );
    }
    case "gmail_draft":
      return (
        <div className="overflow-hidden rounded-xl border border-line">
          <div className="space-y-1 border-b border-line bg-subtle px-4 py-2.5 text-[12.5px]">
            <div className="flex gap-2"><span className="w-12 text-faint">To</span><span className="text-ink">{(payload.to as string[] | undefined)?.join(", ")}</span></div>
            <div className="flex gap-2"><span className="w-12 text-faint">Subject</span><span className="font-medium text-ink">{str(payload.subject)}</span></div>
          </div>
          <p className="px-4 py-3 text-[13.5px] leading-relaxed whitespace-pre-wrap text-ink">{str(payload.body)}</p>
        </div>
      );
    default:
      return <p className="text-[13.5px] leading-relaxed whitespace-pre-wrap text-ink">{str(payload.text)}</p>;
  }
}

function Editor({ item, onSave, onCancel, busy }: { item: ChangeItem; onSave: (edits: Record<string, unknown>) => void; onCancel: () => void; busy: boolean }) {
  const [values, setValues] = useState<Record<string, unknown>>(() => Object.fromEntries(item.editable.map((f) => [f, item.proposed[f]])));
  const set = (f: string, v: unknown) => setValues((x) => ({ ...x, [f]: v }));
  return (
    <div className="space-y-3 rounded-xl border border-accent/30 bg-accent-soft/40 p-4 animate-fade-in">
      {item.editable.map((f) => (
        <Field key={f} label={sentence(label(f))}>
          {f === "body" || f === "text" ? (
            <Textarea className="min-h-36 text-[13px]" value={str(values[f])} onChange={(e) => set(f, e.target.value)} />
          ) : f === "due_date" ? (
            <Input type="date" inputSize="sm" value={str(values[f])} onChange={(e) => set(f, e.target.value)} />
          ) : f === "priority" ? (
            <Select selectSize="sm" value={str(values[f])} onChange={(e) => set(f, e.target.value)}>
              {["LOW", "MEDIUM", "HIGH"].map((p) => <option key={p}>{p}</option>)}
            </Select>
          ) : (
            <Input inputSize="sm" value={str(values[f])} onChange={(e) => set(f, e.target.value)} />
          )}
        </Field>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" icon={Check} loading={busy} onClick={() => onSave(values)}>Approve edited version</Button>
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
        <span className="text-xs text-muted">The approval binds the exact edited text.</span>
      </div>
    </div>
  );
}

function ItemRow({ item, canDecide, act, busy }: { item: ChangeItem; canDecide: boolean; act: (path: string, json?: unknown) => void; busy: boolean }) {
  const [editing, setEditing] = useState(false);
  const [why, setWhy] = useState(false);
  const payload = item.final ?? item.proposed;
  const sys = systems[item.target_system] ?? systems.casefile;
  const lastApproval = item.approvals.filter((a) => a.decision === "approve").at(-1);
  const rejection = item.approvals.find((a) => a.decision === "reject");
  const result = item.result as { id?: string; error?: string; retryable?: boolean; refused?: string; conflict?: { expected: string | null; found: string | null } };
  const decided = !["proposed", "blocked"].includes(item.status);
  return (
    <li className={cn("overflow-hidden rounded-xl border bg-panel transition-shadow", item.status === "proposed" ? "border-line shadow-xs hover:shadow-sm" : "border-line/70")}>
      <div className="flex gap-3.5 p-4">
        <span className={cn("grid size-9 shrink-0 place-items-center rounded-lg", sys.tile)}>
          <sys.icon className="size-[18px]" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-[14px] font-semibold text-ink">{item.label}</span>
            <Tooltip content={item.tier === 2 ? "Customer-facing: approved one at a time, never in a batch" : item.tier === 0 ? "Inside Casefile only" : "Routine: can be approved with the batch"}>
              <Badge>Tier {item.tier}</Badge>
            </Tooltip>
            <Badge tone={statusTone[item.status]} dot>{statusText[item.status] ?? sentence(label(item.status))}</Badge>
          </div>
          <div className="mt-0.5 text-xs text-muted">
            {sys.name} · <Target item={item} />
          </div>

          <div className={cn("mt-3", decided && item.status !== "conflict" && "opacity-90")}>
            {editing ? (
              <Editor item={item} busy={busy} onCancel={() => setEditing(false)} onSave={(edits) => { act(`/change-items/${item.id}/approve`, { edits }); setEditing(false); }} />
            ) : (
              <Content item={item} payload={payload} />
            )}
          </div>

          {item.blocked_reason && <Callout className="mt-3" tone="risk" title="Blocked">{item.blocked_reason}</Callout>}
          {item.status === "failed" && (
            <Callout className="mt-3" tone="risk" title={result.refused ? "Refused by the executor" : "The write failed"}>
              {result.refused ?? result.error ?? "Unknown error"}
            </Callout>
          )}
          {item.status === "conflict" && result.conflict && (
            <Callout className="mt-3" tone="warn" title="The CRM changed after you approved">
              Expected “{result.conflict.expected ?? "(empty)"}”, found “{result.conflict.found ?? "(empty)"}”. Nothing was written.
            </Callout>
          )}

          {item.why.length > 0 && (
            <div className="mt-3">
              <button onClick={() => setWhy(!why)} className="inline-flex items-center gap-1 text-xs font-medium text-muted hover:text-ink" aria-expanded={why}>
                <Sparkles className="size-3.5" aria-hidden />
                Why · {item.why.length} verified claim{item.why.length > 1 ? "s" : ""} · {item.evidence_count} records
                <ChevronDown className={cn("size-3.5 transition-transform", why && "rotate-180")} />
              </button>
              {why && (
                <ul className="mt-2 space-y-1.5 border-l-2 border-accent/30 pl-3 animate-fade-in">
                  {item.why.map((c, i) => (
                    <li key={i} className="text-[13px] leading-relaxed text-ink">
                      {c.text}
                      {c.evidence.map((e) => <EvidenceChip key={e.eid} e={e} />)}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {(lastApproval || rejection || item.attempts.length > 0) && (
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-faint">
              {lastApproval && (
                <span className="inline-flex items-center gap-1">
                  <ShieldCheck className="size-3.5 text-ok-fg" aria-hidden />
                  Approved by {lastApproval.by}{lastApproval.edited ? " (edited)" : ""} {dateTime(lastApproval.at)}
                  {lastApproval.channel === "slack" && " via Slack"}
                </span>
              )}
              {lastApproval && (
                <Tooltip content="SHA-256 of the exact change that was approved. The executor refuses anything that doesn't match.">
                  <span className="inline-flex cursor-help items-center gap-0.5 font-mono"><Hash className="size-3" />{lastApproval.hash.slice(0, 10)}</span>
                </Tooltip>
              )}
              {rejection && <span>Rejected by {rejection.by}</span>}
              {item.status === "succeeded" && result.id && <span>Created {sys.name} id {result.id}</span>}
              {item.links.drafts && item.status === "succeeded" && (
                <a className="inline-flex items-center gap-0.5 text-accent-fg hover:underline" href={item.links.drafts} target="_blank" rel="noreferrer">
                  Open Drafts <ExternalLink className="size-3" />
                </a>
              )}
              {item.attempts.length > 0 && <span>{item.attempts.length} attempt{item.attempts.length > 1 ? "s" : ""}</span>}
            </div>
          )}
        </div>
      </div>

      {canDecide && !editing && ["proposed", "blocked", "failed", "conflict"].includes(item.status) && (
        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-subtle px-4 py-2.5">
          {item.status === "proposed" && (
            <>
              <Button size="sm" variant="ghost" icon={X} disabled={busy} onClick={() => act(`/change-items/${item.id}/reject`, {})} className="hover:text-risk-fg">Reject</Button>
              {item.editable.length > 0 && <Button size="sm" icon={Pencil} disabled={busy} onClick={() => setEditing(true)}>Edit</Button>}
              <Button size="sm" variant="primary" icon={Check} disabled={busy} onClick={() => act(`/change-items/${item.id}/approve`, {})}>Approve</Button>
            </>
          )}
          {item.status === "blocked" && <Button size="sm" disabled={busy} onClick={() => act(`/change-items/${item.id}/reject`, {})}>Dismiss</Button>}
          {item.status === "failed" && result.retryable !== false && !result.refused && (
            <Button size="sm" icon={RotateCcw} disabled={busy} onClick={() => act(`/change-items/${item.id}/retry`, {})}>Retry this line</Button>
          )}
          {item.status === "conflict" && (
            <Button size="sm" icon={RefreshCw} disabled={busy} onClick={() => act(`/change-items/${item.id}/refresh`, {})}>Re-read from the CRM and decide again</Button>
          )}
        </div>
      )}
    </li>
  );
}

const verb: Record<string, string> = { approve: "Line approved", reject: "Line rejected", retry: "Retry queued", refresh: "Re-read from the CRM", "approve-tier1": "Tier 1 lines approved", plan: "Actions proposed" };

export default function PlanPanel({ caseId, accountId }: { caseId: string; accountId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const key = ["plan", caseId];
  const plan = useQuery({
    queryKey: key,
    queryFn: () => api<ChangeSet | null>(`/cases/${caseId}/plan`),
    refetchInterval: (q) => (q.state.data?.items.some((i) => i.status === "queued" || i.status === "executing") ? 2000 : false),
  });
  const mutate = useMutation({
    mutationFn: ({ path, json }: { path: string; json?: unknown }) => api<ChangeSet>(path, { method: "POST", json: json ?? {} }),
    onSuccess: (data, { path }) => {
      qc.setQueryData(key, data);
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["timeline", accountId] });
      const action = path.split("?")[0].split("/").pop() ?? "";
      toast({
        tone: action === "reject" ? "neutral" : "ok",
        title: verb[action] ?? "Saved",
        description: action === "approve" || action === "approve-tier1" ? "The executor writes it exactly as approved, and records every step." : undefined,
      });
    },
    onError: (e) => toast({ tone: "risk", title: "That didn't go through", description: e instanceof Error ? e.message : undefined }),
  });
  const act = (path: string, json?: unknown) => mutate.mutate({ path, json });
  const cs = plan.data;
  const tier1 = cs?.items.filter((i) => i.status === "proposed" && i.tier <= 1).length ?? 0;
  const counts = (cs?.items ?? []).reduce<Record<string, number>>((m, i) => ({ ...m, [i.status]: (m[i.status] ?? 0) + 1 }), {});
  const done = (counts.succeeded ?? 0) + (counts.would_execute ?? 0);

  return (
    <Card
      id="plan"
      title="Proposed actions"
      description={cs ? <span className="capitalize">{label(cs.play)} · playbook {cs.playbook_version}</span> : "What Casefile suggests doing about this case"}
      icon={WandSparkles}
      actions={
        <>
          {cs?.can_decide && tier1 > 0 && (
            <Tooltip content="Approves the CRM and Slack lines as proposed. Customer emails are never included in a batch.">
              <Button variant="primary" icon={Check} loading={mutate.isPending && mutate.variables?.path.endsWith("approve-tier1")} onClick={() => act(`/change-sets/${cs.id}/approve-tier1`)}>
                Approve all Tier 1 ({tier1})
              </Button>
            </Tooltip>
          )}
          {(!cs || cs.can_decide) && (
            <Button icon={RefreshCw} disabled={mutate.isPending} onClick={() => act(`/cases/${caseId}/plan${cs ? "?force=true" : ""}`)}>
              {cs ? "Re-plan" : "Propose actions"}
            </Button>
          )}
        </>
      }
    >
      {plan.isLoading && (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-24 w-full rounded-xl" />)}
        </div>
      )}
      {plan.error && <ErrorNote error={plan.error} />}
      {!plan.isLoading && !cs && (
        <EmptyState
          compact
          icon={WandSparkles}
          title="No actions proposed yet"
          description="Casefile proposes them once the case explanation is written, or you can ask now."
          action={<Button variant="primary" icon={Sparkles} loading={mutate.isPending} onClick={() => act(`/cases/${caseId}/plan`)}>Propose actions</Button>}
        />
      )}
      {cs && (
        <div className="space-y-4">
          {cs.notes.purpose && <p className="text-[14px] leading-relaxed text-ink">{cs.notes.purpose}</p>}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-xl border border-line bg-subtle px-4 py-3">
            <div className="flex min-w-48 flex-1 items-center gap-3">
              <StackedBar
                className="flex-1"
                segments={[
                  { key: "done", value: done, tone: "ok", label: "done" },
                  { key: "q", value: (counts.queued ?? 0) + (counts.executing ?? 0), tone: "warn", label: "in progress" },
                  { key: "p", value: counts.proposed ?? 0, tone: "accent", label: "waiting" },
                  { key: "x", value: (counts.failed ?? 0) + (counts.conflict ?? 0) + (counts.blocked ?? 0), tone: "risk", label: "problems" },
                  { key: "r", value: (counts.rejected ?? 0) + (counts.superseded ?? 0), tone: "neutral", label: "rejected" },
                ]}
              />
              <span className="text-xs text-muted tabular-nums">{done}/{cs.items.length} done</span>
            </div>
            <p className="flex items-center gap-1.5 text-xs text-muted">
              <ShieldCheck className="size-3.5 text-ok-fg" aria-hidden />
              Nothing is written until you approve a line
              {cs.notes.draft_source === "ai" && " · email worded by Claude, checked by code"}
              {cs.notes.draft_source === "template" && " · email is a plain template"}
            </p>
          </div>
          {cs.notes.skipped && cs.notes.skipped.length > 0 && (
            <p className="text-xs text-faint">Not proposed: {cs.notes.skipped.map((s) => `${label(s.type)} (${s.reason})`).join("; ")}</p>
          )}
          <ul className="space-y-3">
            {cs.items.map((i) => <ItemRow key={i.id} item={i} canDecide={cs.can_decide} act={act} busy={mutate.isPending} />)}
          </ul>
        </div>
      )}
    </Card>
  );
}
