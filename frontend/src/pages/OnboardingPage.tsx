import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Download, Eye, FileUp, Flag, Play, Radio, Settings2, TriangleAlert, Upload } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, apiUpload, type Metric, type OnboardingConnection, type OnboardingStatus, type OrgMode, type StepState } from "../api";
import { HonestyLabel } from "../components/domain";
import { Badge, Button, ButtonAnchor, Callout, Checkbox, ErrorNote, Field, Input, Loading, PageHeader, Select, Tooltip, useToast, type Tone } from "../components/ui";
import { dateTime, label } from "../format";
import { cn } from "../lib/cn";
import { useMe } from "../lib/session";

type Step = OnboardingStatus["steps"][number];
type StepOf<K extends Step["key"]> = Extract<Step, { key: K }>;

const stateTone: Record<StepState, Tone> = { done: "ok", warn: "warn", todo: "neutral", optional: "neutral", active: "accent" };
const stateText: Record<StepState, string> = { done: "Done", warn: "Done, with warnings", todo: "To do", optional: "Optional", active: "Running" };
const TITLES: Record<Step["key"], [string, string]> = {
  company: ["Company details", "Name, time zone and your own email domains"],
  permission: ["Data permission", "A record that someone allowed to decide agreed"],
  crm: ["Connect your CRM", "Read-only HubSpot access"],
  usage: ["Product usage", "Daily usage per customer (optional)"],
  data: ["Check what was found", "Counts and warnings from the first sync"],
  backtest: ["Back-test report", "How Casefile would have done on your past renewals"],
  shadow: ["Shadow mode", "The full loop, with nothing written"],
  live: ["Go live", "Approved lines are written to your tools"],
};
const HUBSPOT_SCOPES = ["crm.objects.companies.read", "crm.objects.contacts.read", "crm.objects.deals.read", "crm.objects.owners.read", "sales-email-read (optional)"];

function pick<K extends Step["key"]>(s: OnboardingStatus, key: K): StepOf<K> {
  return s.steps.find((x) => x.key === key) as StepOf<K>;
}

function num(m: Metric | null | undefined, digits = 2) {
  if (!m || m.value == null) return "—";
  const ci = m.ci95 ? ` (${m.ci95[0].toFixed(digits)}–${m.ci95[1].toFixed(digits)})` : "";
  return `${m.value.toFixed(digits)}${ci}`;
}

function ConnectionLine({ c }: { c: OnboardingConnection }) {
  const run = c.last_run;
  return (
    <div className="rounded-xl border border-line bg-subtle px-3.5 py-2.5 text-[13px]">
      <div className="font-medium text-ink capitalize">
        {label(c.kind)} · <span className="normal-case">{c.name}</span>
        {typeof c.config.portal_id === "string" && <span className="font-normal text-faint"> · portal {c.config.portal_id}</span>}
        {typeof c.config.filename === "string" && <span className="font-normal text-faint"> · {c.config.filename}</span>}
      </div>
      <div className="mt-0.5 text-xs text-muted">
        {run ? `Last run ${run.status} · ${run.fetched.toLocaleString("en-IN")} records · ${dateTime(run.finished_at ?? run.started_at)}` : "Waiting for the worker to run the first sync"}
      </div>
      {c.last_error && <div className="mt-0.5 text-xs text-risk-fg">{c.last_error}</div>}
    </div>
  );
}

function CompanyStep({ step, onDone }: { step: StepOf<"company">; onDone: () => void }) {
  const toast = useToast();
  const [form, setForm] = useState({ name: step.data.name, timezone: step.data.timezone, domains: step.data.internal_domains.join(", ") });
  const zones = Intl.supportedValuesOf("timeZone");
  const save = useMutation({
    mutationFn: () => api("/onboarding/company", { method: "PUT", json: { name: form.name, timezone: form.timezone, internal_domains: form.domains.split(/[\s,]+/).filter(Boolean) } }),
    onSuccess: () => {
      onDone();
      toast({ tone: "ok", title: "Company details saved" });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  return (
    <form onSubmit={submit} className="grid max-w-2xl gap-4 sm:grid-cols-2">
      <Field label="Company name" htmlFor="ob-name">
        <Input id="ob-name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </Field>
      <Field label="Time zone" htmlFor="ob-tz">
        <Select id="ob-tz" value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })}>
          {!zones.includes(form.timezone) && <option value={form.timezone}>{form.timezone}</option>}
          {zones.map((z) => <option key={z} value={z}>{z}</option>)}
        </Select>
      </Field>
      <Field className="sm:col-span-2" label="Your email domains" htmlFor="ob-domains" hint="Your own staff's domains, comma separated. People at these domains are never matched to a customer.">
        <Input id="ob-domains" required value={form.domains} onChange={(e) => setForm({ ...form, domains: e.target.value })} />
      </Field>
      <div className="sm:col-span-2">
        <Button type="submit" loading={save.isPending}>Save company details</Button>
      </div>
      {save.error && <div className="sm:col-span-2"><ErrorNote error={save.error} /></div>}
    </form>
  );
}

function PermissionStep({ step, onDone }: { step: StepOf<"permission">; onDone: () => void }) {
  const [checked, setChecked] = useState<boolean[]>(step.data.statements.map(() => false));
  const record = useMutation({ mutationFn: () => api("/onboarding/permission", { json: { statements: step.data.statements } }), onSuccess: onDone });
  const rec = step.data.recorded;
  return (
    <div className="max-w-2xl space-y-4">
      <p className="text-muted">Casefile needs a record that someone allowed to decide has agreed to how your data is used. Confirm each statement.</p>
      <ul className="space-y-2.5 rounded-xl border border-line p-4">
        {step.data.statements.map((s, i) => (
          <li key={s}>
            <Checkbox disabled={!!rec} checked={!!rec || checked[i]} onChange={(v) => setChecked(checked.map((c, j) => (j === i ? v : c)))} label={s} />
          </li>
        ))}
      </ul>
      {rec ? (
        <p className="text-xs text-muted">Recorded by {rec.by_email} on {dateTime(rec.at)} (wording version {rec.version}). It is in the audit log.</p>
      ) : (
        <Button variant="primary" icon={Check} disabled={!checked.every(Boolean)} loading={record.isPending} onClick={() => record.mutate()}>
          Record permission
        </Button>
      )}
      {record.error && <ErrorNote error={record.error} />}
    </div>
  );
}

function CrmStep({ step, allowed, onDone }: { step: StepOf<"crm">; allowed: boolean; onDone: () => void }) {
  const [token, setToken] = useState("");
  const [open, setOpen] = useState(step.data.connections.length === 0);
  const connect = useMutation({
    mutationFn: () => api<{ portal_id: string; warnings: string[] }>("/onboarding/hubspot", { json: { token } }),
    onSuccess: () => {
      setToken("");
      onDone();
    },
  });
  const sync = useMutation({ mutationFn: (id: string) => api(`/connections/${id}/sync`, { json: {} }), onSuccess: onDone });
  const conns = step.data.connections;
  return (
    <div className="max-w-2xl space-y-3">
      {step.data.synthetic && <Callout tone="warn" title="Synthetic data">This organisation runs on synthetic data: every result here is simulated.</Callout>}
      {conns.map((c) => (
        <div key={c.id} className="flex items-start gap-2">
          <div className="min-w-0 flex-1"><ConnectionLine c={c} /></div>
          <Button size="sm" loading={sync.isPending && sync.variables === c.id} onClick={() => sync.mutate(c.id)}>Sync now</Button>
        </div>
      ))}
      {!step.data.synthetic && (
        <div className="rounded-xl border border-line">
          <button onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center justify-between px-4 py-3 text-left text-[13.5px] font-medium">
            {conns.length ? "Replace the HubSpot token" : "Connect HubSpot (read-only)"}
            <ChevronDown className={cn("size-4 text-faint transition-transform", open && "rotate-180")} />
          </button>
          {open && (
            <div className="space-y-3 border-t border-line px-4 py-4 animate-fade-in">
              <ol className="list-decimal space-y-1.5 pl-5 text-[13px] text-muted">
                <li>In HubSpot: Settings → Integrations → Private apps (or Legacy apps) → create an app named “Casefile (read)”.</li>
                <li>Give it read scopes only: <span className="font-mono text-xs text-ink">{HUBSPOT_SCOPES.join(", ")}</span>, plus read access to notes, tasks, calls and meetings where listed.</li>
                <li>Paste its access token. Casefile checks it can read each kind of record (nothing kept), stores it encrypted and starts the first sync.</li>
              </ol>
              <form className="flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); connect.mutate(); }}>
                <Input type="password" autoComplete="off" spellCheck={false} placeholder="pat-…" value={token} onChange={(e) => setToken(e.target.value)} className="min-w-0 flex-1 [&_input]:font-mono" inputSize="sm" aria-label="HubSpot access token" />
                <Button type="submit" variant="primary" size="sm" disabled={!allowed || !token} loading={connect.isPending}>Check and connect</Button>
              </form>
              {!allowed && <p className="text-xs text-faint">Record the data permission first.</p>}
              {connect.error && <ErrorNote error={connect.error} />}
              {connect.data?.warnings.map((w) => <Callout key={w} tone="warn">{w}</Callout>)}
            </div>
          )}
        </div>
      )}
      <p className="text-[13px] text-muted">
        Gmail and Calendar, Slack, and write access for approved actions are connected on the{" "}
        <Link to="/connections" className="font-medium text-accent-fg hover:underline">Connections page</Link>, each with a step-by-step guide.
      </p>
    </div>
  );
}

function UsageStep({ step, allowed, onDone }: { step: StepOf<"usage">; allowed: boolean; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const upload = useMutation({
    mutationFn: () => apiUpload<{ rows: number; accounts_in_file: number; matched_now: number; first_day: string; last_day: string }>("/onboarding/usage", file as File),
    onSuccess: onDone,
  });
  const skip = useMutation({ mutationFn: () => api("/onboarding/usage/skip", { json: {} }), onSuccess: onDone });
  return (
    <div className="max-w-2xl space-y-3">
      <p className="text-muted">Daily product usage per customer makes the strongest early-warning signals. One row per customer per day; the key is their domain or CRM company id.</p>
      {step.data.connections.map((c) => <ConnectionLine key={c.id} c={c} />)}
      {step.data.connections.length === 0 && step.data.usage_days > 0 && (
        <p className="text-xs text-muted">{step.data.usage_days.toLocaleString("en-IN")} days of usage already arrive through your data source.</p>
      )}
      <label className={cn("flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-6 text-center transition-colors", file ? "border-accent/50 bg-accent-soft/40" : "border-line hover:border-line-strong hover:bg-subtle")}>
        <FileUp className={cn("size-6", file ? "text-accent-fg" : "text-faint")} aria-hidden />
        <span className="text-[13px] font-medium text-ink">{file ? file.name : "Choose a usage CSV"}</span>
        <span className="text-xs text-faint">{file ? `${(file.size / 1024).toFixed(0)} KB · ready to upload` : "Up to 50 MB"}</span>
        <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="sr-only" aria-label="Usage CSV" />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" icon={Upload} disabled={!allowed || !file} loading={upload.isPending} onClick={() => upload.mutate()}>Upload</Button>
        <ButtonAnchor variant="ghost" icon={Download} href="/api/onboarding/usage/template">Template</ButtonAnchor>
        {!step.data.skipped && step.data.connections.length === 0 && step.data.usage_days === 0 && (
          <Button variant="ghost" loading={skip.isPending} onClick={() => skip.mutate()} className="ml-auto">We have no usage data</Button>
        )}
      </div>
      {upload.data && (
        <Callout tone="ok" title={`${upload.data.rows.toLocaleString("en-IN")} rows uploaded`}>
          {upload.data.first_day} to {upload.data.last_day}; {upload.data.matched_now} of {upload.data.accounts_in_file} customer keys match a synced company. The rest are matched again after every CRM sync, or by hand under Unmatched records.
        </Callout>
      )}
      {step.data.skipped && <p className="text-xs text-faint">Skipped: usage signals will show as blind spots.</p>}
      {(upload.error || skip.error) && <ErrorNote error={upload.error ?? skip.error} />}
    </div>
  );
}

function DataStep({ step }: { step: StepOf<"data"> }) {
  const c = step.data.counts;
  const n = (k: string) => (c[k] ?? 0).toLocaleString("en-IN");
  const rows: [string, string][] = [
    ["Companies in your CRM", n("accounts")],
    ["… customers (with a deal), watched by Casefile", n("customers")],
    ["… with a renewal date", n("accounts_with_renewal_date")],
    ["… with an owner who can log in", n("accounts_with_owner")],
    ["… with a champion recorded", n("accounts_with_recorded_champion")],
    ["Contacts", n("contacts")],
    ["Deals (of which renewals)", `${n("deals")} (${n("renewal_deals")})`],
    ["Emails, meetings, notes, tasks, calls", n("activities")],
    ["… not matched to a company", n("activities_unmatched")],
    ["Usage days (not matched)", `${n("usage_days")} (${n("usage_days_unmatched")})`],
  ];
  return (
    <div className="max-w-2xl space-y-3">
      <dl className="divide-y divide-line rounded-xl border border-line">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between px-4 py-2 text-[13px]">
            <dt className={k.startsWith("…") ? "pl-3 text-muted" : "text-ink"}>{k}</dt>
            <dd className="font-medium tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      {step.data.warnings.map((w) => <Callout key={w} tone="warn">{w}</Callout>)}
      <p className="text-[13px] text-muted">
        Wrong meanings? Fix them in the <Link to="/settings?tab=mapping" className="font-medium text-accent-fg hover:underline">field mapping</Link> and re-apply it; map stray records under{" "}
        <Link to="/identity" className="font-medium text-accent-fg hover:underline">Unmatched records</Link>.
      </p>
    </div>
  );
}

function BacktestStep({ step, onDone }: { step: StepOf<"backtest">; onDone: () => void }) {
  const run = useMutation({ mutationFn: () => api("/backtest/run", { json: {} }), onSuccess: onDone });
  const b = step.data;
  return (
    <div className="max-w-2xl space-y-3">
      <p className="text-muted">Replays your history week by week with the same rules Casefile uses live, and checks which past renewals it would have flagged in time. No AI is used.</p>
      {b && (
        <div className="rounded-xl border border-line p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
            <HonestyLabel label={b.label} />
            <span className="text-faint">Run {dateTime(b.finished_at)}</span>
          </div>
          <dl className="grid gap-x-6 gap-y-2 text-[13px] sm:grid-cols-[auto_1fr]">
            <dt className="text-muted">Past renewals scored</dt>
            <dd>{b.events?.scored ?? 0} · lost or downgraded within 30 days: {b.positives_30d}</dd>
            <dt className="text-muted">Ranking quality (AUC, 30 days)</dt>
            <dd>{num(b.auc_30d)} · on renewals it was not tuned on: {num(b.held_out_auc_30d)}</dd>
            <dt className="text-muted">Lost renewals flagged in time</dt>
            <dd>{b.recall_30d?.value != null ? `${Math.round(b.recall_30d.value * 100)}%` : "—"}</dd>
            <dt className="text-muted">Best single signal</dt>
            <dd className="capitalize">{b.best_single_signal ? `${label(b.best_single_signal)} · AUC ${num(b.best_single_signal_auc)}` : "—"}</dd>
          </dl>
          {!b.enough_history && (
            <Callout className="mt-3" tone="warn">
              {(b.note ?? "Too few lost renewals for a headline number").replace(/\.?$/, ".")} Treat this as a sanity check, not proof; the per-renewal replays are still useful.
            </Callout>
          )}
          <Link to="/backtest" className="mt-3 inline-block text-[13px] font-medium text-accent-fg hover:underline">Full report, labels and replays →</Link>
        </div>
      )}
      <Button variant={b ? "secondary" : "primary"} icon={Play} loading={run.isPending} onClick={() => run.mutate()}>
        {run.isPending ? "Running (about 15 s per 200 companies)" : b ? "Run again" : "Run the back-test"}
      </Button>
      {run.error && <ErrorNote error={run.error} />}
    </div>
  );
}

function ShadowStep({ s, step, onDone }: { s: OnboardingStatus; step: StepOf<"shadow">; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const move = useMutation({ mutationFn: (to: string) => api("/onboarding/mode", { json: { to, reason: reason || null } }), onSuccess: onDone });
  const st = step.data;
  const est = s.ai_estimate;
  return (
    <div className="max-w-2xl space-y-3">
      <p className="text-muted">
        In shadow mode Casefile reads customer email, writes explanations and proposes actions; your team approves or rejects them as if for real, but nothing is written to your tools. Run it for two to three weeks and compare with what your team already knew.
      </p>
      {st && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[
            ["Running", `${st.days} days`],
            ["Cases opened", st.cases_opened],
            ["Lines decided", `${st.lines_decided} (${st.lines_rejected} rejected)`],
            ["Would execute", st.would_execute],
          ].map(([k, v]) => (
            <div key={k as string} className="rounded-xl border border-line px-3 py-2.5">
              <div className="text-xs text-muted">{k}</div>
              <div className="mt-0.5 text-[15px] font-semibold tabular-nums">{v}</div>
            </div>
          ))}
        </div>
      )}
      {s.mode === "setup" && (
        <>
          {est && (
            <div className="flex items-start gap-2.5 rounded-xl bg-subtle px-4 py-3 text-[13px] text-muted">
              <HonestyLabel label="estimated" />
              <span>
                Starting reads {est.emails_to_read.toLocaleString("en-IN")} recent customer emails and explains {est.cases_to_explain} open cases: about{" "}
                <span className="font-medium text-ink">${est.usd.toFixed(2)}</span> of AI, spread over {est.days_at_cap || 1} day(s) by the ${est.daily_cap_usd} daily cap.
              </span>
            </div>
          )}
          {s.start_shadow_blockers.length > 0 && (
            <ul className="space-y-1 text-[13px] text-warn-fg">
              {s.start_shadow_blockers.map((b) => (
                <li key={b} className="flex gap-2"><TriangleAlert className="mt-0.5 size-4 shrink-0" />{b}</li>
              ))}
            </ul>
          )}
          <Button variant="primary" icon={Eye} disabled={s.start_shadow_blockers.length > 0} loading={move.isPending} onClick={() => move.mutate("shadow")}>
            Start shadow mode
          </Button>
        </>
      )}
      {s.mode === "shadow" && (
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (optional)" inputSize="sm" className="min-w-0 flex-1" />
          <Button size="sm" loading={move.isPending} onClick={() => move.mutate("setup")}>Back to setup (stops AI work)</Button>
        </div>
      )}
      {move.error && <ErrorNote error={move.error} />}
    </div>
  );
}

function LiveStep({ s, step, onDone }: { s: OnboardingStatus; step: StepOf<"live">; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const move = useMutation({
    mutationFn: (to: string) => api("/onboarding/mode", { json: { to, reason: reason || null } }),
    onSuccess: () => {
      setReason("");
      onDone();
    },
  });
  const checks = step.data.checks;
  const override = checks.some((c) => !c.required && !c.ok);
  const blocked = checks.some((c) => c.required && !c.ok);
  if (s.mode === "live") {
    return (
      <div className="max-w-2xl space-y-3">
        <p className="text-muted">{s.mode_since ? `Live since ${dateTime(s.mode_since)}.` : "Live since before the setup checklist existed."} Approved lines are written to your tools by the executor.</p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (optional)" inputSize="sm" className="min-w-0 flex-1" />
          <Button size="sm" variant="dangerSoft" loading={move.isPending} onClick={() => move.mutate("shadow")}>Pause writing (back to shadow)</Button>
        </div>
        <p className="text-xs text-faint">Takes effect from the next line; a line already being written finishes.</p>
        {move.error && <ErrorNote error={move.error} />}
      </div>
    );
  }
  return (
    <div className="max-w-2xl space-y-3">
      <ul className="divide-y divide-line rounded-xl border border-line">
        {checks.map((c) => (
          <li key={c.key} className="flex items-start gap-3 px-4 py-2.5 text-[13px]">
            <span className={cn("mt-0.5 grid size-5 shrink-0 place-items-center rounded-full", c.ok ? "bg-ok-soft text-ok-fg" : c.required ? "bg-risk-soft text-risk-fg" : "bg-warn-soft text-warn-fg")}>
              {c.ok ? <Check className="size-3" strokeWidth={3} /> : <span className="text-[11px] font-bold">!</span>}
            </span>
            <span className="text-ink">
              {c.text}
              {!c.required && !c.ok && <span className="text-faint"> · recommended; can be overridden with a reason</span>}
            </span>
          </li>
        ))}
      </ul>
      {s.mode === "shadow" && (
        <div className="flex flex-col gap-2 sm:flex-row">
          {override && <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why go live now? (required, 20+ characters)" inputSize="sm" className="min-w-0 flex-1" />}
          <Button variant="primary" icon={Radio} disabled={blocked || (override && reason.trim().length < 20)} loading={move.isPending} onClick={() => move.mutate("live")}>
            Go live
          </Button>
        </div>
      )}
      {move.error && <ErrorNote error={move.error} />}
    </div>
  );
}

function ModeTrack({ mode }: { mode: OrgMode }) {
  const stages: { key: OrgMode; title: string; text: string; icon: typeof Settings2 }[] = [
    { key: "setup", title: "Setup", text: "Reading and scoring. No AI, no writes.", icon: Settings2 },
    { key: "shadow", title: "Shadow", text: "Full loop and approvals. Nothing written.", icon: Eye },
    { key: "live", title: "Live", text: "Approved lines written to your tools.", icon: Flag },
  ];
  const at = stages.findIndex((s) => s.key === mode);
  return (
    <ol className="grid gap-2 sm:grid-cols-3">
      {stages.map((s, i) => (
        <li
          key={s.key}
          className={cn(
            "relative flex items-start gap-3 rounded-xl border p-4 transition-colors",
            i === at ? "border-accent/40 bg-accent-soft shadow-sm" : i < at ? "border-line bg-panel" : "border-dashed border-line bg-transparent",
          )}
        >
          <span className={cn("grid size-8 shrink-0 place-items-center rounded-lg", i === at ? "bg-accent text-white" : i < at ? "bg-ok-soft text-ok-fg" : "bg-hover text-faint")}>
            {i < at ? <Check className="size-4" strokeWidth={3} /> : <s.icon className="size-4" />}
          </span>
          <div>
            <div className={cn("text-[13.5px] font-semibold", i > at ? "text-muted" : "text-ink")}>
              {s.title}
              {i === at && <span className="ml-2 text-[11px] font-medium text-accent-fg uppercase">You are here</span>}
            </div>
            <div className="mt-0.5 text-xs text-muted">{s.text}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}

function StepCard({ n, state, stepKey, open, onToggle, children }: { n: number; state: StepState; stepKey: Step["key"]; open: boolean; onToggle: () => void; children: ReactNode }) {
  const [title, subtitle] = TITLES[stepKey];
  const done = state === "done" || state === "warn";
  return (
    <li className="relative pl-12">
      <span
        className={cn(
          "absolute top-4 left-0 z-10 grid size-8 place-items-center rounded-full text-[13px] font-semibold ring-4 ring-canvas",
          done ? (state === "warn" ? "bg-warn text-white" : "bg-ok text-white") : state === "active" ? "bg-accent text-white" : "bg-panel text-muted ring-offset-0 outline outline-1 outline-line",
        )}
      >
        {done ? <Check className="size-4" strokeWidth={3} /> : n}
      </span>
      <section className={cn("rounded-xl border bg-panel shadow-xs transition-shadow", open ? "border-line shadow-sm" : "border-line")}>
        <button onClick={onToggle} aria-expanded={open} className="flex w-full items-center gap-3 px-5 py-4 text-left">
          <div className="min-w-0 flex-1">
            <div className="text-[14.5px] font-semibold tracking-[-0.01em] text-ink">{title}</div>
            <div className="text-[12.5px] text-muted">{subtitle}</div>
          </div>
          <Badge tone={stateTone[state]} dot>{stateText[state]}</Badge>
          <ChevronDown className={cn("size-4 shrink-0 text-faint transition-transform", open && "rotate-180")} aria-hidden />
        </button>
        {open && <div className="border-t border-line px-5 py-5 text-sm animate-fade-in">{children}</div>}
      </section>
    </li>
  );
}

// The admin's path from first login to live: connect → check → back-test → shadow → live.
export default function OnboardingPage() {
  const me = useMe();
  const qc = useQueryClient();
  const isAdmin = me.data?.role === "admin";
  const q = useQuery({ queryKey: ["onboarding"], queryFn: () => api<OnboardingStatus>("/onboarding"), enabled: isAdmin, refetchInterval: 10_000 });
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["onboarding"] });
    qc.invalidateQueries({ queryKey: ["me"] });
  };
  if (me.data && !isAdmin) return <Callout tone="neutral" title="Only admins can set up Casefile" />;
  if (q.isLoading) return <Loading rows={6} />;
  if (q.error) return <ErrorNote error={q.error} />;
  const s = q.data;
  if (!s) return null;
  const permitted = !!pick(s, "permission").data.recorded;
  const doneCount = s.steps.filter((x) => x.state === "done" || x.state === "warn").length;
  const firstOpen = s.steps.find((x) => x.state === "todo" || x.state === "active")?.key;
  const isOpen = (k: Step["key"]) => toggled[k] ?? (k === firstOpen || pick(s, k).state === "warn");
  const toggle = (k: Step["key"]) => setToggled((t) => ({ ...t, [k]: !isOpen(k) }));

  const body: Record<Step["key"], ReactNode> = {
    company: <CompanyStep key={JSON.stringify(pick(s, "company").data)} step={pick(s, "company")} onDone={refresh} />,
    permission: <PermissionStep step={pick(s, "permission")} onDone={refresh} />,
    crm: <CrmStep step={pick(s, "crm")} allowed={permitted} onDone={refresh} />,
    usage: <UsageStep step={pick(s, "usage")} allowed={permitted} onDone={refresh} />,
    data: <DataStep step={pick(s, "data")} />,
    backtest: <BacktestStep step={pick(s, "backtest")} onDone={refresh} />,
    shadow: <ShadowStep s={s} step={pick(s, "shadow")} onDone={refresh} />,
    live: <LiveStep s={s} step={pick(s, "live")} onDone={refresh} />,
  };

  return (
    <>
      <PageHeader
        title="Set up Casefile"
        description="From first connection to live. Each step is checked against your real data; nothing is written to your tools until you go live."
        actions={<HonestyLabel label={s.label} />}
      />
      <ModeTrack mode={s.mode} />
      <div className="mt-6 mb-5 flex items-center gap-4">
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-hover">
          <div className="h-full origin-left rounded-full bg-gradient-to-r from-indigo-500 via-violet-500 to-cyan-400 transition-[width] duration-700" style={{ width: `${(doneCount / s.steps.length) * 100}%` }} />
        </div>
        <Tooltip content="Steps marked done or done with warnings">
          <span className="text-[13px] font-medium tabular-nums text-muted">{doneCount} of {s.steps.length} done</span>
        </Tooltip>
      </div>
      <ol className="relative space-y-3 before:absolute before:top-6 before:bottom-6 before:left-[15px] before:w-px before:bg-line">
        {s.steps.map((step, i) => (
          <StepCard key={step.key} n={i + 1} state={step.state} stepKey={step.key} open={isOpen(step.key)} onToggle={() => toggle(step.key)}>
            {body[step.key]}
          </StepCard>
        ))}
      </ol>
    </>
  );
}
