import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Building, CircleCheck, Contact, Handshake, ListTree, Plus, RefreshCw, Save, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api } from "../api";
import { Badge, Button, Callout, Card, Checkbox, ErrorNote, Field, Input, Loading, SectionLabel, Select, useToast } from "./ui";

type ValueMap = Record<string, string>;
type Mapping = {
  hubspot: {
    company: { seats_purchased: string | null; renewal_date: string | null; arr: string | null };
    contact: { role_property: string; role_values: ValueMap; status_property: string; status_values: ValueMap };
    deal: { type_property: string; type_values: ValueMap; won_stages: string[]; lost_stages: string[]; next_step_property: string; next_step_date_property: string | null };
    engagement_types: string[];
  };
};
type MappingResponse = { mapping: Mapping; version: number; saved_at: string | null; live_crm: boolean };
type Suggestion = { name: string; label: string };
type Check = { ok: boolean; problems: string[]; checked_against_crm: boolean; note?: string; suggestions?: Record<string, Suggestion[]> };

// Fields the check can suggest a match for, from the client's own CRM (names and labels, no records).
const SUGGESTED: Record<string, { label: string; apply: (h: Mapping["hubspot"], picks: string[]) => void; all?: boolean }> = {
  "company.renewal_date": { label: "Renewal date", apply: (h, [v]) => { h.company.renewal_date = v; } },
  "company.seats_purchased": { label: "Seats purchased", apply: (h, [v]) => { h.company.seats_purchased = v; } },
  "company.arr": { label: "Annual recurring revenue", apply: (h, [v]) => { h.company.arr = v; } },
  "contact.role_property": { label: "Contact role", apply: (h, [v]) => { h.contact.role_property = v; } },
  "contact.status_property": { label: "Contact status", apply: (h, [v]) => { h.contact.status_property = v; } },
  "deal.won_stages": { label: "Won stages", apply: (h, v) => { h.deal.won_stages = v; }, all: true },
  "deal.lost_stages": { label: "Lost stages", apply: (h, v) => { h.deal.lost_stages = v; }, all: true },
};

const ROLES = ["champion", "economic_buyer", "user", "unknown"];
const STATUSES = ["active", "left"];
const DEAL_TYPES = ["renewal", "new", "expansion", "other"];
const ENGAGEMENTS = ["notes", "tasks", "calls", "emails", "meetings"];

function Prop({ label, hint, value, onChange, optional }: { label: string; hint?: string; value: string | null; onChange: (v: string | null) => void; optional?: boolean }) {
  return (
    <Field label={label} hint={hint} optional={optional}>
      <Input
        inputSize="sm"
        className="[&_input]:font-mono [&_input]:text-[12.5px]"
        value={value ?? ""}
        placeholder={optional ? "not used" : ""}
        onChange={(e) => onChange(e.target.value.trim() === "" && optional ? null : e.target.value)}
      />
    </Field>
  );
}

function Values({ label, values, options, onChange }: { label: string; values: ValueMap; options: string[]; onChange: (v: ValueMap) => void }) {
  const rows = Object.entries(values);
  const set = (i: number, key: string, meaning: string) => onChange(Object.fromEntries(rows.map((r, j) => (j === i ? [key, meaning] : r))));
  return (
    <div>
      <div className="mb-1.5 text-[13px] font-medium text-ink">{label}</div>
      <ul className="space-y-1.5">
        {rows.map(([k, v], i) => (
          <li key={i} className="flex items-center gap-1.5">
            <Input inputSize="sm" className="min-w-0 flex-1 [&_input]:font-mono [&_input]:text-[12.5px]" value={k} onChange={(e) => set(i, e.target.value, v)} aria-label="CRM value" />
            <ArrowRight className="size-3.5 shrink-0 text-faint" aria-hidden />
            <Select selectSize="sm" className="w-36 shrink-0" value={v} onChange={(e) => set(i, k, e.target.value)} aria-label="Meaning">
              {options.map((o) => <option key={o} value={o}>{o.replace("_", " ")}</option>)}
            </Select>
            <button onClick={() => onChange(Object.fromEntries(rows.filter((_, j) => j !== i)))} aria-label="Remove" className="rounded-md p-1 text-faint transition-colors hover:bg-risk-soft hover:text-risk-fg">
              <X className="size-3.5" />
            </button>
          </li>
        ))}
      </ul>
      <button onClick={() => onChange({ ...values, "": options[options.length - 1] })} className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-accent-fg hover:underline">
        <Plus className="size-3.5" /> Add a CRM value
      </button>
    </div>
  );
}

function Column({ icon: Icon, title, children }: { icon: typeof Building; title: string; children: ReactNode }) {
  return (
    <div className="space-y-3.5">
      <SectionLabel className="flex items-center gap-1.5">
        <Icon className="size-3.5" aria-hidden /> {title}
      </SectionLabel>
      {children}
    </div>
  );
}

// Which CRM properties mean what, for this company. Saving keeps every old version; nothing in stored
// data changes until "Re-apply" rebuilds it from the raw records already fetched.
export default function FieldMappingEditor() {
  const qc = useQueryClient();
  const toast = useToast();
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
    onSuccess: (r) => {
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["field-mapping"] });
      qc.invalidateQueries({ queryKey: ["config"] });
      toast({ tone: "ok", title: `Saved as version ${r.version}`, description: "Stored data still uses the old meanings until you re-apply." });
    },
  });
  const reapply = useMutation({
    mutationFn: () => api<{ queued: boolean }>("/admin/field-mapping/reapply", { json: {} }),
    onSuccess: () => toast({ tone: "ok", title: "Re-apply queued", description: "The worker rebuilds stored data from the raw records (no re-fetch, no AI) and rescores every account." }),
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorNote error={q.error} />;
  if (!m) return null;
  const h = m.hubspot;
  const noChampion = !Object.values(h.contact.role_values).includes("champion");
  const splitList = (v: string | null) => (v ?? "").split(",").map((s) => s.trim()).filter(Boolean);

  return (
    <Card
      title="CRM field mapping"
      description="Which HubSpot properties mean what for your company. Every save is a new version."
      icon={ListTree}
      actions={
        <>
          {draft && <Badge tone="warn" dot>Unsaved changes</Badge>}
          <Badge>{q.data?.version ? `Version ${q.data.version}` : "Defaults"}</Badge>
        </>
      }
    >
      <div className="grid gap-8 md:grid-cols-2 xl:grid-cols-3">
        <Column icon={Building} title="Company">
          <Prop label="Seats purchased" optional value={h.company.seats_purchased} onChange={(v) => edit((x) => { x.company.seats_purchased = v; })} />
          <Prop label="Renewal date" optional hint="Empty: use the open renewal deal's close date." value={h.company.renewal_date} onChange={(v) => edit((x) => { x.company.renewal_date = v; })} />
          <Prop label="ARR" optional hint="Empty: derive from renewal deal amounts." value={h.company.arr} onChange={(v) => edit((x) => { x.company.arr = v; })} />
        </Column>
        <Column icon={Contact} title="Contact">
          <Prop label="Role property" value={h.contact.role_property} onChange={(v) => edit((x) => { x.contact.role_property = v ?? ""; })} />
          <Values label="Role values" values={h.contact.role_values} options={ROLES} onChange={(v) => edit((x) => { x.contact.role_values = v; })} />
          {noChampion && (
            <Callout tone="warn">No CRM value means “champion”. Casefile will infer a likely champion per account (the contact who writes and meets most), labelled as inferred.</Callout>
          )}
          <Prop label="Status property" value={h.contact.status_property} onChange={(v) => edit((x) => { x.contact.status_property = v ?? ""; })} />
          <Values label="Status values" values={h.contact.status_values} options={STATUSES} onChange={(v) => edit((x) => { x.contact.status_values = v; })} />
        </Column>
        <Column icon={Handshake} title="Deal">
          <Prop label="Type property" value={h.deal.type_property} onChange={(v) => edit((x) => { x.deal.type_property = v ?? ""; })} />
          <Values label="Type values" values={h.deal.type_values} options={DEAL_TYPES} onChange={(v) => edit((x) => { x.deal.type_values = v; })} />
          <Prop label="Won stages" hint="Comma separated" value={h.deal.won_stages.join(", ")} onChange={(v) => edit((x) => { x.deal.won_stages = splitList(v); })} />
          <Prop label="Lost stages" hint="Comma separated" value={h.deal.lost_stages.join(", ")} onChange={(v) => edit((x) => { x.deal.lost_stages = splitList(v); })} />
          <Prop label="Next step property" value={h.deal.next_step_property} onChange={(v) => edit((x) => { x.deal.next_step_property = v ?? ""; })} />
          <Prop label="Next step date property" optional value={h.deal.next_step_date_property} onChange={(v) => edit((x) => { x.deal.next_step_date_property = v; })} />
        </Column>
      </div>

      <div className="mt-6 rounded-xl border border-line bg-subtle px-4 py-3">
        <div className="mb-2 text-[13px] font-medium text-ink">Activity to read from the CRM</div>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {ENGAGEMENTS.map((e) => (
            <Checkbox
              key={e}
              label={<span className="capitalize">{e}</span>}
              checked={h.engagement_types.includes(e)}
              onChange={(on) => edit((x) => { x.engagement_types = on ? [...x.engagement_types, e] : x.engagement_types.filter((y) => y !== e); })}
            />
          ))}
        </div>
      </div>

      {check.data && (
        <Callout className="mt-4" tone={check.data.ok ? "ok" : "risk"} title={check.data.ok ? "Looks right" : "Problems found"} onDismiss={() => check.reset()}>
          {check.data.problems.length > 0 && <ul className="mt-1 list-disc space-y-0.5 pl-4">{check.data.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
          {!check.data.checked_against_crm && <div className="mt-1">{check.data.note ?? "Not checked against a live CRM."}</div>}
          {check.data.suggestions && Object.keys(check.data.suggestions).length > 0 && (
            <div className="mt-3">
              <div className="font-medium text-ink">Possible matches in your CRM</div>
              <ul className="mt-1 space-y-1.5">
                {Object.entries(check.data.suggestions).map(([key, options]) => {
                  const rule = SUGGESTED[key];
                  if (!rule) return null;
                  return (
                    <li key={key} className="flex flex-wrap items-center gap-1.5">
                      <span className="text-muted">{rule.label}:</span>
                      {rule.all ? (
                        <Button size="sm" onClick={() => edit((x) => rule.apply(x, options.map((o) => o.name)))}>
                          Use {options.map((o) => o.label || o.name).join(", ")}
                        </Button>
                      ) : (
                        options.map((o) => (
                          <Button key={o.name} size="sm" onClick={() => edit((x) => rule.apply(x, [o.name]))}>
                            {o.label || o.name} <span className="font-mono text-[11px] text-faint">{o.name}</span>
                          </Button>
                        ))
                      )}
                    </li>
                  );
                })}
              </ul>
              <div className="mt-1 text-xs text-muted">Picking one changes the form only; check again, then save as a new version.</div>
            </div>
          )}
        </Callout>
      )}
      {save.error && <div className="mt-4"><ErrorNote error={save.error} /></div>}

      <div className="sticky bottom-3 z-10 mt-5 flex flex-wrap items-center gap-2 rounded-xl border border-line bg-panel/90 p-2.5 shadow-md backdrop-blur">
        <Button icon={CircleCheck} loading={check.isPending} onClick={() => check.mutate(m)}>Check</Button>
        <Button variant="primary" icon={Save} disabled={!draft} loading={save.isPending} onClick={() => save.mutate(m)}>Save as a new version</Button>
        {draft && <Button variant="ghost" onClick={() => setDraft(null)}>Discard changes</Button>}
        <Button className="ml-auto" icon={RefreshCw} loading={reapply.isPending} onClick={() => reapply.mutate()}>Re-apply to stored data</Button>
      </div>
    </Card>
  );
}
