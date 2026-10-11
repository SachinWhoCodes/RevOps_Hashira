import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { Badge, ErrorNote, Loading, Panel } from "./ui";

type ValueMap = Record<string, string>;
type Mapping = {
  hubspot: {
    company: { seats_purchased: string | null; renewal_date: string | null; arr: string | null };
    contact: { role_property: string; role_values: ValueMap; status_property: string; status_values: ValueMap };
    deal: {
      type_property: string; type_values: ValueMap; won_stages: string[]; lost_stages: string[];
      next_step_property: string; next_step_date_property: string | null;
    };
    engagement_types: string[];
  };
};
type MappingResponse = { mapping: Mapping; version: number; saved_at: string | null; live_crm: boolean };
type Check = { ok: boolean; problems: string[]; checked_against_crm: boolean; note?: string };

const ROLES = ["champion", "economic_buyer", "user", "unknown"];
const STATUSES = ["active", "left"];
const DEAL_TYPES = ["renewal", "new", "expansion", "other"];
const ENGAGEMENTS = ["notes", "tasks", "calls", "emails", "meetings"];

function Field({ label, hint, value, onChange, optional }: {
  label: string; hint?: string; value: string | null; onChange: (v: string | null) => void; optional?: boolean;
}) {
  return (
    <label className="block text-sm">
      <span className="text-muted">{label}{optional && <span className="text-faint"> (optional)</span>}</span>
      <input value={value ?? ""} onChange={(e) => onChange(e.target.value.trim() === "" && optional ? null : e.target.value)}
        className="mt-0.5 w-full rounded-md border border-line px-2 py-1 font-mono text-xs" placeholder={optional ? "not used" : ""} />
      {hint && <span className="text-[11px] text-faint">{hint}</span>}
    </label>
  );
}

function Values({ label, values, options, onChange }: {
  label: string; values: ValueMap; options: string[]; onChange: (v: ValueMap) => void;
}) {
  const rows = Object.entries(values);
  const set = (i: number, key: string, meaning: string) => {
    const next = rows.map((r, j) => (j === i ? [key, meaning] : r));
    onChange(Object.fromEntries(next));
  };
  return (
    <div className="text-sm">
      <div className="text-muted">{label}</div>
      <ul className="mt-1 space-y-1">
        {rows.map(([k, v], i) => (
          <li key={i} className="flex items-center gap-1.5">
            <input value={k} onChange={(e) => set(i, e.target.value, v)} className="min-w-0 flex-1 rounded-md border border-line px-2 py-0.5 font-mono text-xs" aria-label="CRM value" />
            <span className="shrink-0 text-faint">→</span>
            <select value={v} onChange={(e) => set(i, k, e.target.value)} className="w-36 shrink-0 rounded-md border border-line px-1 py-0.5 text-xs" aria-label="Meaning">
              {options.map((o) => <option key={o} value={o}>{o.replace("_", " ")}</option>)}
            </select>
            <button onClick={() => onChange(Object.fromEntries(rows.filter((_, j) => j !== i)))} className="shrink-0 text-xs text-faint hover:text-risk" aria-label="Remove">×</button>
          </li>
        ))}
      </ul>
      <button onClick={() => onChange({ ...values, "": options[options.length - 1] })} className="mt-1 text-xs text-accent hover:underline">+ add a CRM value</button>
    </div>
  );
}

// Which CRM properties mean what, for this company. Saving keeps every old version; nothing in stored
// data changes until "Re-apply" rebuilds it from the raw records already fetched.
export default function FieldMappingEditor() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["field-mapping"], queryFn: () => api<MappingResponse>("/admin/field-mapping") });
  const [draft, setDraft] = useState<Mapping | null>(null);
  const m = draft ?? q.data?.mapping ?? null;
  const edit = (fn: (h: Mapping["hubspot"]) => void) => {
    if (!m) return;
    const next = structuredClone(m);
    fn(next.hubspot);
    setDraft(next);
  };
  const check = useMutation({ mutationFn: (body: Mapping) => api<Check>("/admin/field-mapping/check", { json: body }) });
  const save = useMutation({
    mutationFn: (body: Mapping) => api<{ version: number }>("/admin/field-mapping", { method: "PUT", json: body }),
    onSuccess: () => { setDraft(null); qc.invalidateQueries({ queryKey: ["field-mapping"] }); qc.invalidateQueries({ queryKey: ["config"] }); },
  });
  const reapply = useMutation({ mutationFn: () => api<{ queued: boolean }>("/admin/field-mapping/reapply", { json: {} }) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorNote error={q.error} />;
  if (!m) return null;
  const h = m.hubspot;
  const noChampion = !Object.values(h.contact.role_values).includes("champion");
  return (
    <Panel
      title="CRM field mapping"
      actions={<Badge>{q.data?.version ? `version ${q.data.version}` : "defaults"}</Badge>}
    >
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        <div className="space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-faint">Company</div>
          <Field label="Seats purchased" optional value={h.company.seats_purchased} onChange={(v) => edit((x) => { x.company.seats_purchased = v; })} />
          <Field label="Renewal date" optional hint="Empty: use the open renewal deal's close date." value={h.company.renewal_date} onChange={(v) => edit((x) => { x.company.renewal_date = v; })} />
          <Field label="ARR" optional hint="Empty: derive from renewal deal amounts." value={h.company.arr} onChange={(v) => edit((x) => { x.company.arr = v; })} />
        </div>
        <div className="space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-faint">Contact</div>
          <Field label="Role property" value={h.contact.role_property} onChange={(v) => edit((x) => { x.contact.role_property = v ?? ""; })} />
          <Values label="Role values" values={h.contact.role_values} options={ROLES} onChange={(v) => edit((x) => { x.contact.role_values = v; })} />
          {noChampion && (
            <p className="rounded-md bg-warn-soft px-2 py-1 text-[11px] text-warn">
              No CRM value means "champion". Casefile will infer a likely champion per account (the contact who writes and meets most), labelled as inferred.
            </p>
          )}
          <Field label="Status property" value={h.contact.status_property} onChange={(v) => edit((x) => { x.contact.status_property = v ?? ""; })} />
          <Values label="Status values" values={h.contact.status_values} options={STATUSES} onChange={(v) => edit((x) => { x.contact.status_values = v; })} />
        </div>
        <div className="space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-faint">Deal</div>
          <Field label="Type property" value={h.deal.type_property} onChange={(v) => edit((x) => { x.deal.type_property = v ?? ""; })} />
          <Values label="Type values" values={h.deal.type_values} options={DEAL_TYPES} onChange={(v) => edit((x) => { x.deal.type_values = v; })} />
          <Field label="Won stages (comma separated)" value={h.deal.won_stages.join(", ")} onChange={(v) => edit((x) => { x.deal.won_stages = (v ?? "").split(",").map((s) => s.trim()).filter(Boolean); })} />
          <Field label="Lost stages (comma separated)" value={h.deal.lost_stages.join(", ")} onChange={(v) => edit((x) => { x.deal.lost_stages = (v ?? "").split(",").map((s) => s.trim()).filter(Boolean); })} />
          <Field label="Next step property" value={h.deal.next_step_property} onChange={(v) => edit((x) => { x.deal.next_step_property = v ?? ""; })} />
          <Field label="Next step date property" optional value={h.deal.next_step_date_property} onChange={(v) => edit((x) => { x.deal.next_step_date_property = v; })} />
        </div>
      </div>
      <div className="mt-4 text-sm">
        <span className="text-muted">Read from the CRM: </span>
        {ENGAGEMENTS.map((e) => (
          <label key={e} className="mr-3 inline-flex items-center gap-1 text-xs">
            <input type="checkbox" checked={h.engagement_types.includes(e)}
              onChange={(ev) => edit((x) => { x.engagement_types = ev.target.checked ? [...x.engagement_types, e] : x.engagement_types.filter((y) => y !== e); })} />
            {e}
          </label>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button onClick={() => check.mutate(m)} className="rounded-md border border-line px-3 py-1.5 text-sm hover:border-accent">Check</button>
        <button disabled={!draft || save.isPending} onClick={() => save.mutate(m)} className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          Save as a new version
        </button>
        {draft && <button onClick={() => setDraft(null)} className="text-sm text-muted hover:underline">Discard changes</button>}
        <button onClick={() => reapply.mutate()} disabled={reapply.isPending} className="ml-auto rounded-md border border-line px-3 py-1.5 text-sm hover:border-accent">
          Re-apply to stored data
        </button>
      </div>
      {check.data && (
        <div className={`mt-2 rounded-md px-3 py-2 text-xs ${check.data.ok ? "bg-ok-soft text-ok" : "bg-risk-soft text-risk"}`}>
          {check.data.ok ? "Looks right." : "Problems:"}
          {check.data.problems.map((p) => <div key={p}>• {p}</div>)}
          {!check.data.checked_against_crm && <div className="text-muted">{check.data.note ?? "Not checked against a live CRM."}</div>}
        </div>
      )}
      {save.error && <div className="mt-2"><ErrorNote error={save.error} /></div>}
      {save.isSuccess && <p className="mt-2 text-xs text-ok">Saved. Stored data still uses the old meanings until you re-apply.</p>}
      {reapply.isSuccess && <p className="mt-2 text-xs text-muted">Queued: the worker rebuilds stored data from the raw records (no re-fetch, no AI) and rescores every account.</p>}
    </Panel>
  );
}
