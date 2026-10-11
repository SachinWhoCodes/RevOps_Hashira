import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ChangeItem, type ChangeSet, type ItemStatus } from "../api";
import { date, dateTime, label } from "../format";
import { useEvidence } from "./EvidenceDrawer";
import { Badge, ErrorNote, Loading } from "./ui";

type Tone = "neutral" | "accent" | "risk" | "warn" | "ok";

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
  proposed: "waiting for you",
  edited_approved: "edited & approved",
  queued: "approved · queued",
  executing: "writing…",
  succeeded: "done",
  would_execute: "shadow: would write",
};

const systemName: Record<string, string> = { hubspot: "HubSpot", gmail: "Gmail", slack: "Slack", casefile: "Casefile" };

// Task and note bodies are stored as simple HTML for HubSpot; show them as text (never as markup).
function htmlText(html: string): string {
  const spaced = html.replace(/<\/(p|li)>/g, "\n").replace(/<li>/g, "• ");
  return (new DOMParser().parseFromString(spaced, "text/html").body.textContent ?? "").trim();
}

function str(v: unknown): string {
  return v == null || v === "" ? "" : String(v);
}

function Target({ item }: { item: ChangeItem }) {
  const r = item.target_ref;
  if (item.target_system === "slack") {
    return <span>DM to {r.substituted ? <>workspace owner <span className="text-faint">(sandbox, meant for {str(r.intended_for)})</span></> : str(r.intended_for)}</span>;
  }
  if (item.target_system === "gmail") {
    return <span>draft in {str(r.mailbox) || "the connected mailbox"} · never sent</span>;
  }
  if (item.target_system === "hubspot") {
    return (
      <span className="space-x-2">
        {r.company ? item.links.company ? <a className="text-accent hover:underline" href={item.links.company} target="_blank" rel="noreferrer">company {str(r.company)}</a> : <span>company {str(r.company)}</span> : null}
        {r.deal ? item.links.deal ? <a className="text-accent hover:underline" href={item.links.deal} target="_blank" rel="noreferrer">deal {str(r.deal)}</a> : <span>deal {str(r.deal)}</span> : null}
        {r.mirror ? <span className="text-faint">(sandbox copy)</span> : null}
      </span>
    );
  }
  return <span>inside Casefile</span>;
}

function Content({ item, payload }: { item: ChangeItem; payload: Record<string, unknown> }) {
  switch (item.action_type) {
    case "crm_task":
      return (
        <div className="space-y-1">
          <div className="font-medium">{str(payload.subject)}</div>
          <div className="text-xs text-muted">due {date(str(payload.due_date))} · priority {label(str(payload.priority)).toLowerCase()}</div>
          <details>
            <summary className="cursor-pointer text-xs text-muted">Task body</summary>
            <p className="mt-1 whitespace-pre-wrap text-xs text-muted">{htmlText(str(payload.body))}</p>
          </details>
        </div>
      );
    case "crm_note":
      return <p className="whitespace-pre-wrap text-xs text-muted">{htmlText(str(payload.body))}</p>;
    case "crm_next_step": {
      const before = str(item.before.hs_next_step);
      return (
        <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-sm">
          <span className="text-xs text-faint">now</span>
          <span className={before ? "text-muted line-through" : "text-faint italic"}>{before || "(empty)"}</span>
          <span className="text-xs text-faint">after</span>
          <span className="font-medium">{str(payload.value)}</span>
          <span />
          <span className="text-xs text-faint">
            current value read {item.before.source === "hubspot_live" ? "live from HubSpot" : "from Casefile's copy"} {dateTime(str(item.before.read_at))}; the write is refused if it has changed
          </span>
        </div>
      );
    }
    case "gmail_draft":
      return (
        <div className="rounded-md border border-line bg-canvas p-2 text-sm">
          <div className="text-xs text-muted">To {(payload.to as string[] | undefined)?.join(", ")}</div>
          <div className="font-medium">{str(payload.subject)}</div>
          <p className="mt-1 whitespace-pre-wrap">{str(payload.body)}</p>
        </div>
      );
    default:
      return <p className="whitespace-pre-wrap text-sm">{str(payload.text)}</p>;
  }
}

function Editor({ item, onSave, onCancel, busy }: {
  item: ChangeItem;
  onSave: (edits: Record<string, unknown>) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [values, setValues] = useState<Record<string, unknown>>(() =>
    Object.fromEntries(item.editable.map((f) => [f, item.proposed[f]])),
  );
  const set = (f: string, v: unknown) => setValues((x) => ({ ...x, [f]: v }));
  const input = "w-full rounded-md border border-line bg-panel px-2 py-1 text-sm";
  return (
    <div className="space-y-2">
      {item.editable.map((f) => (
        <label key={f} className="block text-xs text-muted">
          {label(f)}
          {f === "body" || f === "text" ? (
            <textarea className={`${input} mt-0.5 min-h-32 font-mono text-xs`} value={str(values[f])} onChange={(e) => set(f, e.target.value)} />
          ) : f === "due_date" ? (
            <input type="date" className={`${input} mt-0.5`} value={str(values[f])} onChange={(e) => set(f, e.target.value)} />
          ) : f === "priority" ? (
            <select className={`${input} mt-0.5`} value={str(values[f])} onChange={(e) => set(f, e.target.value)}>
              {["LOW", "MEDIUM", "HIGH"].map((p) => <option key={p}>{p}</option>)}
            </select>
          ) : (
            <input className={`${input} mt-0.5`} value={str(values[f])} onChange={(e) => set(f, e.target.value)} />
          )}
        </label>
      ))}
      <div className="flex gap-2">
        <button disabled={busy} onClick={() => onSave(values)} className="rounded-md bg-accent px-3 py-1 text-sm text-white disabled:opacity-60">Approve edited version</button>
        <button onClick={onCancel} className="rounded-md border border-line px-3 py-1 text-sm hover:bg-canvas">Cancel</button>
      </div>
    </div>
  );
}

function ItemRow({ item, canDecide, act, busy }: {
  item: ChangeItem;
  canDecide: boolean;
  act: (path: string, json?: unknown) => void;
  busy: boolean;
}) {
  const evidence = useEvidence();
  const [editing, setEditing] = useState(false);
  const payload = item.final ?? item.proposed;
  const lastApproval = item.approvals.filter((a) => a.decision === "approve").at(-1);
  const result = item.result as { id?: string; error?: string; retryable?: boolean; refused?: string; conflict?: { expected: string | null; found: string | null } };
  return (
    <li className="rounded-lg border border-line bg-panel p-3">
      <div className="flex flex-wrap items-center gap-1.5 text-sm">
        <span className="text-faint tabular-nums">{item.seq}.</span>
        <span className="font-medium">{item.label}</span>
        <Badge title={item.tier === 2 ? "Customer-facing: approved one by one" : item.tier === 0 ? "Inside Casefile only" : "Can be approved with the batch"}>
          Tier {item.tier}
        </Badge>
        <Badge tone={statusTone[item.status]}>{statusText[item.status] ?? label(item.status)}</Badge>
        <span className="text-xs text-muted">{systemName[item.target_system] ?? item.target_system} · <Target item={item} /></span>
      </div>

      <div className="mt-2">
        {editing ? (
          <Editor item={item} busy={busy} onCancel={() => setEditing(false)} onSave={(edits) => { act(`/change-items/${item.id}/approve`, { edits }); setEditing(false); }} />
        ) : (
          <Content item={item} payload={payload} />
        )}
      </div>

      {item.blocked_reason && <p className="mt-2 rounded-md bg-risk-soft px-2 py-1 text-xs text-risk">Blocked: {item.blocked_reason}</p>}
      {item.status === "failed" && (
        <p className="mt-2 rounded-md bg-risk-soft px-2 py-1 text-xs text-risk">
          {result.refused ? `Refused by the executor: ${result.refused}` : `Failed: ${result.error ?? "unknown error"}`}
        </p>
      )}
      {item.status === "conflict" && result.conflict && (
        <p className="mt-2 rounded-md bg-warn-soft px-2 py-1 text-xs text-warn">
          The CRM changed after you approved: expected "{result.conflict.expected ?? "(empty)"}", found "{result.conflict.found ?? "(empty)"}". Nothing was written.
        </p>
      )}

      {item.why.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-muted">Why · {item.why.length} verified claim{item.why.length > 1 ? "s" : ""} · {item.evidence_count} records</summary>
          <ul className="mt-1 space-y-1">
            {item.why.map((c, i) => (
              <li key={i} className="text-xs">
                <span>{c.text}</span>
                {c.evidence.map((e) => (
                  <button key={e.eid} onClick={() => evidence.open(e)} title={e.label}
                    className="ml-1 rounded border border-line bg-canvas px-1 text-[11px] text-muted hover:border-accent hover:text-accent">{e.eid}</button>
                ))}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-faint">
        {lastApproval && <span>approved by {lastApproval.by}{lastApproval.edited ? " (edited)" : ""} {dateTime(lastApproval.at)} · hash {lastApproval.hash}</span>}
        {item.approvals.some((a) => a.decision === "reject") && <span>rejected by {item.approvals.find((a) => a.decision === "reject")?.by}</span>}
        {item.status === "succeeded" && result.id && <span>· created {systemName[item.target_system]} id {result.id}</span>}
        {item.links.drafts && item.status === "succeeded" && <a className="text-accent hover:underline" href={item.links.drafts} target="_blank" rel="noreferrer">open Drafts</a>}
        {item.attempts.length > 0 && <span>· {item.attempts.length} attempt{item.attempts.length > 1 ? "s" : ""}</span>}
      </div>

      {canDecide && (
        <div className="mt-2 flex flex-wrap gap-2">
          {item.status === "proposed" && !editing && (
            <>
              <button disabled={busy} onClick={() => act(`/change-items/${item.id}/approve`, {})} className="rounded-md bg-accent px-3 py-1 text-sm text-white disabled:opacity-60">Approve</button>
              {item.editable.length > 0 && <button disabled={busy} onClick={() => setEditing(true)} className="rounded-md border border-line px-3 py-1 text-sm hover:bg-canvas">Edit</button>}
              <button disabled={busy} onClick={() => act(`/change-items/${item.id}/reject`, {})} className="rounded-md border border-line px-3 py-1 text-sm text-risk hover:bg-risk-soft">Reject</button>
            </>
          )}
          {item.status === "blocked" && (
            <button disabled={busy} onClick={() => act(`/change-items/${item.id}/reject`, {})} className="rounded-md border border-line px-3 py-1 text-sm hover:bg-canvas">Dismiss</button>
          )}
          {item.status === "failed" && result.retryable !== false && !result.refused && (
            <button disabled={busy} onClick={() => act(`/change-items/${item.id}/retry`, {})} className="rounded-md border border-line px-3 py-1 text-sm hover:bg-canvas">Retry this line</button>
          )}
          {item.status === "conflict" && (
            <button disabled={busy} onClick={() => act(`/change-items/${item.id}/refresh`, {})} className="rounded-md border border-line px-3 py-1 text-sm hover:bg-canvas">Re-read from CRM and decide again</button>
          )}
        </div>
      )}
    </li>
  );
}

export default function PlanPanel({ caseId, accountId }: { caseId: string; accountId: string }) {
  const qc = useQueryClient();
  const key = ["plan", caseId];
  const plan = useQuery({
    queryKey: key,
    queryFn: () => api<ChangeSet | null>(`/cases/${caseId}/plan`),
    refetchInterval: (q) => (q.state.data?.items.some((i) => i.status === "queued" || i.status === "executing") ? 2000 : false),
  });
  const mutate = useMutation({
    mutationFn: ({ path, json }: { path: string; json?: unknown }) => api<ChangeSet>(path, { method: "POST", json: json ?? {} }),
    onSuccess: (data) => {
      qc.setQueryData(key, data);
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["timeline", accountId] });
    },
  });
  const act = (path: string, json?: unknown) => mutate.mutate({ path, json });
  const cs = plan.data;
  const tier1 = cs?.items.filter((i) => i.status === "proposed" && i.tier <= 1).length ?? 0;

  return (
    <section id="plan" className="mb-4 rounded-lg border border-line bg-panel">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <h2 className="text-sm font-semibold">
          Proposed actions
          {cs && <span className="ml-2 font-normal text-faint">play {label(cs.play)} · playbook {cs.playbook_version}</span>}
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          {cs && <Badge tone={cs.status === "done" ? "ok" : cs.status === "failed" ? "risk" : "accent"}>{label(cs.status)}</Badge>}
          {cs?.can_decide && tier1 > 0 && (
            <button disabled={mutate.isPending} onClick={() => act(`/change-sets/${cs.id}/approve-tier1`)}
              className="rounded-md bg-accent px-3 py-1 text-sm text-white disabled:opacity-60"
              title="Approves CRM and Slack lines as proposed. Customer emails are never included.">
              Approve all Tier 1 ({tier1})
            </button>
          )}
          {(!cs || cs.can_decide) && (
            <button disabled={mutate.isPending} onClick={() => act(`/cases/${caseId}/plan${cs ? "?force=true" : ""}`)}
              className="rounded-md border border-line px-3 py-1 text-sm hover:bg-canvas disabled:opacity-60">
              {cs ? "Re-plan" : "Propose actions"}
            </button>
          )}
        </div>
      </header>
      <div className="space-y-3 p-4">
        {plan.isLoading && <Loading />}
        {plan.error && <ErrorNote error={plan.error} />}
        {mutate.error && <ErrorNote error={mutate.error} />}
        {!plan.isLoading && !cs && (
          <p className="text-sm text-muted">No actions proposed yet. Casefile proposes them once the case explanation is written, or you can ask now.</p>
        )}
        {cs && (
          <>
            {cs.notes.purpose && <p className="text-sm text-muted">{cs.notes.purpose}</p>}
            <p className="text-xs text-faint">
              Nothing is written until you approve a line. Approved lines are performed by a separate executor exactly as approved
              {cs.notes.draft_source === "ai" && " · the email was worded by Claude and checked by code"}
              {cs.notes.draft_source === "template" && " · the email is a plain template"}
              .
            </p>
            {cs.notes.skipped && cs.notes.skipped.length > 0 && (
              <p className="text-xs text-faint">Not proposed: {cs.notes.skipped.map((s) => `${label(s.type)} (${s.reason})`).join("; ")}</p>
            )}
            <ul className="space-y-2">
              {cs.items.map((i) => (
                <ItemRow key={i.id} item={i} canDecide={cs.can_decide} act={act} busy={mutate.isPending} />
              ))}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}
