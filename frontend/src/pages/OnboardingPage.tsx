import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMe } from "../App";
import { api, apiUpload, type Metric, type OnboardingConnection, type OnboardingStatus, type StepState } from "../api";
import { Badge, ErrorNote, Loading, PageHeader } from "../components/ui";
import { dateTime, label } from "../format";

type Step = OnboardingStatus["steps"][number];
type StepOf<K extends Step["key"]> = Extract<Step, { key: K }>;

const stateTone = { done: "ok", warn: "warn", todo: "neutral", optional: "neutral", active: "accent" } as const;
const stateText = { done: "done", warn: "done, with warnings", todo: "to do", optional: "optional", active: "running" } as const;
const modeText = {
  setup: "Setup: reading and scoring your data. No AI runs and nothing is written to your tools.",
  shadow: "Shadow: the full loop runs and people approve, but approved lines are only recorded as “would execute”.",
  live: "Live: approved lines are written to your tools.",
} as const;
const HUBSPOT_SCOPES = [
  "crm.objects.companies.read",
  "crm.objects.contacts.read",
  "crm.objects.deals.read",
  "crm.objects.owners.read",
  "sales-email-read (emails; optional)",
];

const input = "rounded-md border border-line bg-panel px-3 py-1.5 text-sm";
const primary = "rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60";
const secondary = "rounded-md border border-line px-3 py-1.5 text-sm hover:bg-canvas disabled:opacity-60";

function pick<K extends Step["key"]>(s: OnboardingStatus, key: K): StepOf<K> {
  return s.steps.find((x) => x.key === key) as StepOf<K>;
}

function num(m: Metric | null | undefined, digits = 2) {
  if (!m || m.value == null) return "—";
  const ci = m.ci95 ? ` (${m.ci95[0].toFixed(digits)}–${m.ci95[1].toFixed(digits)})` : "";
  return `${m.value.toFixed(digits)}${ci}`;
}

function Card({ n, title, state, children }: { n: number; title: string; state: StepState; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <h2 className="text-sm font-semibold">
          <span className="mr-2 text-faint">{n}.</span>
          {title}
        </h2>
        <Badge tone={stateTone[state]}>{stateText[state]}</Badge>
      </header>
      <div className="space-y-3 p-4 text-sm">{children}</div>
    </section>
  );
}

function ConnectionLine({ c }: { c: OnboardingConnection }) {
  const run = c.last_run;
  return (
    <div className="rounded-md border border-line px-3 py-2 text-xs">
      <div className="font-medium">
        {label(c.kind)} · {c.name}
        {typeof c.config.portal_id === "string" && <span className="text-faint"> · portal {c.config.portal_id}</span>}
        {typeof c.config.filename === "string" && <span className="text-faint"> · {c.config.filename}</span>}
      </div>
      <div className="mt-0.5 text-muted">
        {run ? `last run ${run.status}, ${run.fetched.toLocaleString()} records fetched, ${dateTime(run.finished_at ?? run.started_at)}` : "waiting for the worker to run the first sync"}
        {c.last_success_at && ` · last success ${dateTime(c.last_success_at)}`}
      </div>
      {c.last_error && <div className="mt-0.5 text-risk">{c.last_error}</div>}
    </div>
  );
}

function CompanyStep({ step, onDone }: { step: StepOf<"company">; onDone: () => void }) {
  const [form, setForm] = useState({ name: step.data.name, timezone: step.data.timezone, domains: step.data.internal_domains.join(", ") });
  const zones = Intl.supportedValuesOf("timeZone");
  const save = useMutation({
    mutationFn: () =>
      api("/onboarding/company", {
        method: "PUT",
        json: { name: form.name, timezone: form.timezone, internal_domains: form.domains.split(/[\s,]+/).filter(Boolean) },
      }),
    onSuccess: onDone,
  });
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(); };
  return (
    <form onSubmit={submit} className="grid gap-2 md:grid-cols-[10rem_1fr]">
      <label className="text-muted md:pt-1.5" htmlFor="ob-name">Company name</label>
      <input id="ob-name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={input} />
      <label className="text-muted md:pt-1.5" htmlFor="ob-tz">Time zone</label>
      <select id="ob-tz" value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} className={input}>
        {!zones.includes(form.timezone) && <option value={form.timezone}>{form.timezone}</option>}
        {zones.map((z) => <option key={z} value={z}>{z}</option>)}
      </select>
      <label className="text-muted md:pt-1.5" htmlFor="ob-domains">Your email domains</label>
      <div>
        <input id="ob-domains" required value={form.domains} onChange={(e) => setForm({ ...form, domains: e.target.value })} className={`${input} w-full`} />
        <p className="mt-1 text-xs text-faint">Your own staff's domains, comma separated. People at these domains are never matched to a customer.</p>
      </div>
      <div className="md:col-start-2">
        <button disabled={save.isPending} className={secondary}>Save company details</button>
        {save.data !== undefined && !save.isPending && <span className="ml-2 text-xs text-ok">Saved</span>}
      </div>
      {save.error && <div className="md:col-span-2"><ErrorNote error={save.error} /></div>}
    </form>
  );
}

function PermissionStep({ step, onDone }: { step: StepOf<"permission">; onDone: () => void }) {
  const [checked, setChecked] = useState<boolean[]>(step.data.statements.map(() => false));
  const record = useMutation({
    mutationFn: () => api("/onboarding/permission", { json: { statements: step.data.statements } }),
    onSuccess: onDone,
  });
  const rec = step.data.recorded;
  return (
    <>
      <p className="text-muted">Casefile needs a record that someone allowed to decide has agreed to how your data is used. Confirm each statement.</p>
      <ul className="space-y-2">
        {step.data.statements.map((s, i) => (
          <li key={s}>
            <label className="flex gap-2">
              <input type="checkbox" className="mt-0.5" disabled={!!rec} checked={!!rec || checked[i]}
                onChange={(e) => setChecked(checked.map((c, j) => (j === i ? e.target.checked : c)))} />
              <span>{s}</span>
            </label>
          </li>
        ))}
      </ul>
      {rec ? (
        <p className="text-xs text-muted">Recorded by {rec.by_email} on {dateTime(rec.at)} (wording version {rec.version}). It is in the audit log.</p>
      ) : (
        <button className={primary} disabled={!checked.every(Boolean) || record.isPending} onClick={() => record.mutate()}>
          Record permission
        </button>
      )}
      {record.error && <ErrorNote error={record.error} />}
    </>
  );
}

function CrmStep({ step, allowed, onDone }: { step: StepOf<"crm">; allowed: boolean; onDone: () => void }) {
  const [token, setToken] = useState("");
  const connect = useMutation({
    mutationFn: () => api<{ portal_id: string; warnings: string[] }>("/onboarding/hubspot", { json: { token } }),
    onSuccess: () => { setToken(""); onDone(); },
  });
  const sync = useMutation({ mutationFn: (id: string) => api(`/connections/${id}/sync`, { json: {} }), onSuccess: onDone });
  const conns = step.data.connections;
  return (
    <>
      {step.data.synthetic && <p className="rounded-md bg-warn-soft px-3 py-2 text-xs text-warn">This organisation runs on synthetic data: every result here is simulated.</p>}
      {conns.map((c) => (
        <div key={c.id} className="flex items-start gap-2">
          <div className="min-w-0 flex-1"><ConnectionLine c={c} /></div>
          <button className={secondary} onClick={() => sync.mutate(c.id)} disabled={sync.isPending}>Sync now</button>
        </div>
      ))}
      {!step.data.synthetic && (
        <details open={conns.length === 0} className="rounded-md border border-line px-3 py-2">
          <summary className="cursor-pointer font-medium">{conns.length ? "Replace the HubSpot token" : "Connect HubSpot (read only)"}</summary>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-muted">
            <li>In HubSpot: Settings → Integrations → Private apps (or Legacy apps) → Create an app named “Casefile (read)”.</li>
            <li>
              Give it read scopes only: <span className="font-mono text-xs">{HUBSPOT_SCOPES.join(", ")}</span>, plus read access to notes, tasks, calls and meetings where
              your HubSpot lists them. The check below names anything still missing.
            </li>
            <li>Paste its access token here. Casefile checks it can read each kind of record (one record id each, nothing kept), then stores it encrypted and starts the first sync.</li>
          </ol>
          <form className="mt-2 flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); connect.mutate(); }}>
            <input type="password" autoComplete="off" spellCheck={false} placeholder="pat-…" value={token} onChange={(e) => setToken(e.target.value)}
              className={`${input} min-w-0 flex-1 font-mono`} aria-label="HubSpot access token" />
            <button className={primary} disabled={!allowed || !token || connect.isPending}>{connect.isPending ? "Checking…" : "Check and connect"}</button>
          </form>
          {!allowed && <p className="mt-1 text-xs text-faint">Record the data permission first.</p>}
          {connect.error && <div className="mt-2"><ErrorNote error={connect.error} /></div>}
          {connect.data?.warnings.map((w) => <p key={w} className="mt-2 text-xs text-warn">{w}</p>)}
        </details>
      )}
      <p className="text-xs text-muted">
        Gmail and Google Calendar, Slack, and write access for approved actions are connected on the{" "}
        <Link to="/connections" className="text-accent hover:underline">Connections page</Link>, each with a step-by-step guide.
      </p>
    </>
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
    <>
      <p className="text-muted">
        Daily product usage per customer makes the strongest early-warning signals. Upload a CSV (one row per customer per day; the key is their domain or CRM company id).{" "}
        <a href="/api/onboarding/usage/template" className="text-accent hover:underline">Download the template</a>.
      </p>
      {step.data.connections.map((c) => <ConnectionLine key={c.id} c={c} />)}
      {step.data.connections.length === 0 && step.data.usage_days > 0 && (
        <p className="text-xs text-muted">{step.data.usage_days.toLocaleString()} days of usage already arrive through your data source.</p>
      )}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="min-w-0 text-sm" aria-label="Usage CSV" />
        <button className={primary} disabled={!allowed || !file || upload.isPending} onClick={() => upload.mutate()}>Upload</button>
        {!step.data.skipped && step.data.connections.length === 0 && step.data.usage_days === 0 && (
          <button className={secondary} disabled={skip.isPending} onClick={() => skip.mutate()}>We have no usage data</button>
        )}
      </div>
      {upload.data && (
        <p className="text-xs text-muted">
          {upload.data.rows.toLocaleString()} rows, {upload.data.first_day} to {upload.data.last_day}; {upload.data.matched_now} of {upload.data.accounts_in_file} customer keys match a company already synced (the rest are matched again after every CRM sync, or by hand under Unmatched records).
        </p>
      )}
      {step.data.skipped && <p className="text-xs text-faint">Skipped: usage signals will show as blind spots.</p>}
      {(upload.error || skip.error) && <ErrorNote error={upload.error ?? skip.error} />}
    </>
  );
}

function DataStep({ step }: { step: StepOf<"data"> }) {
  const c = step.data.counts;
  const n = (k: string) => (c[k] ?? 0).toLocaleString();
  const rows: [string, string][] = [
    ["Customer companies", n("accounts")],
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
    <>
      <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 sm:max-w-md">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd className="text-right tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      {step.data.warnings.map((w) => <p key={w} className="rounded-md bg-warn-soft px-3 py-2 text-xs text-warn">{w}</p>)}
      <p className="text-xs text-muted">
        Wrong meanings? Fix them in the <Link to="/settings" className="text-accent hover:underline">field mapping</Link> and re-apply it; match stray records under{" "}
        <Link to="/identity" className="text-accent hover:underline">Unmatched records</Link>.
      </p>
    </>
  );
}

function BacktestStep({ step, onDone }: { step: StepOf<"backtest">; onDone: () => void }) {
  const run = useMutation({ mutationFn: () => api("/backtest/run", { json: {} }), onSuccess: onDone });
  const b = step.data;
  return (
    <>
      <p className="text-muted">Replays your history week by week with the same rules Casefile uses live, and checks which past renewals it would have flagged in time. No AI is used.</p>
      {b && (
        <div className="rounded-md border border-line px-3 py-2">
          <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
            <Badge tone={b.label === "simulated" ? "warn" : "ok"}>{b.label}</Badge>
            <span className="text-faint">run {dateTime(b.finished_at)}</span>
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm">
            <dt className="text-muted">Past renewals scored</dt><dd>{b.events?.scored ?? 0} (lost or downgraded within 30 days: {b.positives_30d})</dd>
            <dt className="text-muted">Ranking quality (AUC, 30 days)</dt><dd>{num(b.auc_30d)}; on renewals it was not tuned on: {num(b.held_out_auc_30d)}</dd>
            <dt className="text-muted">Lost renewals flagged in time</dt><dd>{b.recall_30d?.value != null ? `${Math.round(b.recall_30d.value * 100)}%` : "—"}</dd>
            <dt className="text-muted">Best single signal</dt><dd>{b.best_single_signal ? `${label(b.best_single_signal)}: AUC ${num(b.best_single_signal_auc)}` : "—"}</dd>
          </dl>
          {!b.enough_history && (
            <p className="mt-2 rounded-md bg-warn-soft px-3 py-2 text-xs text-warn">
              {(b.note ?? "Too few lost renewals for a headline number").replace(/\.?$/, ".")} Treat this as a sanity check, not proof; the per-renewal replays are still useful.
            </p>
          )}
          <Link to="/backtest" className="mt-2 inline-block text-xs text-accent hover:underline">Full report, per-renewal labels and replays</Link>
        </div>
      )}
      <button className={b ? secondary : primary} disabled={run.isPending} onClick={() => run.mutate()}>
        {run.isPending ? "Running (about 15 seconds per 200 companies)…" : b ? "Run again" : "Run the back-test"}
      </button>
      {run.error && <ErrorNote error={run.error} />}
    </>
  );
}

function ShadowStep({ s, step, onDone }: { s: OnboardingStatus; step: StepOf<"shadow">; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const move = useMutation({ mutationFn: (to: string) => api("/onboarding/mode", { json: { to, reason: reason || null } }), onSuccess: onDone });
  const st = step.data;
  const est = s.ai_estimate;
  return (
    <>
      <p className="text-muted">
        In shadow mode Casefile reads customer email, writes explanations and proposes actions; your team approves or rejects them as if for real,
        but nothing is written to your tools. Run it for two to three weeks and compare its cases with what your team already knew.
      </p>
      {st && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5">
          <dt className="text-muted">Running for</dt><dd>{st.days} days (since {dateTime(st.started_at)}{st.ended_at ? `, ended ${dateTime(st.ended_at)}` : ""})</dd>
          <dt className="text-muted">Cases opened</dt><dd>{st.cases_opened}</dd>
          <dt className="text-muted">Lines decided by people</dt><dd>{st.lines_decided} ({st.lines_rejected} rejected)</dd>
          <dt className="text-muted">Recorded as “would execute”</dt><dd>{st.would_execute}</dd>
        </dl>
      )}
      {s.mode === "setup" && (
        <>
          {est && (
            <p className="rounded-md bg-canvas px-3 py-2 text-xs text-muted">
              <Badge>estimated</Badge> Starting shadow mode first reads {est.emails_to_read.toLocaleString()} recent customer emails and explains {est.cases_to_explain} open cases:
              about ${est.usd.toFixed(2)} of AI, spread over {est.days_at_cap || 1} day(s) by the ${est.daily_cap_usd} daily cap.
            </p>
          )}
          {s.start_shadow_blockers.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-warn">{s.start_shadow_blockers.map((b) => <li key={b}>{b}</li>)}</ul>
          )}
          <button className={primary} disabled={s.start_shadow_blockers.length > 0 || move.isPending} onClick={() => move.mutate("shadow")}>Start shadow mode</button>
        </>
      )}
      {s.mode === "shadow" && (
        <div className="flex flex-col gap-2 sm:flex-row">
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="reason (optional)" className={`${input} min-w-0 flex-1`} />
          <button className={secondary} disabled={move.isPending} onClick={() => move.mutate("setup")}>Back to setup (stops AI work)</button>
        </div>
      )}
      {move.error && <ErrorNote error={move.error} />}
    </>
  );
}

function LiveStep({ s, step, onDone }: { s: OnboardingStatus; step: StepOf<"live">; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const move = useMutation({ mutationFn: (to: string) => api("/onboarding/mode", { json: { to, reason: reason || null } }), onSuccess: () => { setReason(""); onDone(); } });
  const checks = step.data.checks;
  const override = checks.some((c) => !c.required && !c.ok);
  const blocked = checks.some((c) => c.required && !c.ok);
  if (s.mode === "live") {
    return (
      <>
        <p className="text-muted">
          {s.mode_since ? `Live since ${dateTime(s.mode_since)}.` : "Live since before the setup checklist existed."} Approved lines are written to your tools by the executor.
        </p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="reason (optional)" className={`${input} min-w-0 flex-1`} />
          <button className="rounded-md border border-line px-3 py-1.5 text-sm text-risk hover:bg-risk-soft disabled:opacity-60" disabled={move.isPending} onClick={() => move.mutate("shadow")}>
            Pause writing (back to shadow)
          </button>
        </div>
        <p className="text-xs text-faint">Takes effect from the next line; a line already being written finishes.</p>
        {move.error && <ErrorNote error={move.error} />}
      </>
    );
  }
  return (
    <>
      <ul className="space-y-1">
        {checks.map((c) => (
          <li key={c.key} className="flex gap-2">
            <span className={c.ok ? "text-ok" : c.required ? "text-risk" : "text-warn"}>{c.ok ? "✓" : "✗"}</span>
            <span>{c.text}{!c.required && !c.ok && <span className="text-faint"> (recommended; can be overridden with a reason)</span>}</span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted">Write access (CRM, Gmail drafts, Slack) is set up by your operator on the executor, never pasted here: the app that reads your data never holds a write credential.</p>
      {s.mode === "shadow" && (
        <div className="flex flex-col gap-2 sm:flex-row">
          {override && <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="why go live now? (required, 20+ characters)" className={`${input} min-w-0 flex-1`} />}
          <button className={primary} disabled={blocked || (override && reason.trim().length < 20) || move.isPending} onClick={() => move.mutate("live")}>Go live</button>
        </div>
      )}
      {move.error && <ErrorNote error={move.error} />}
    </>
  );
}

// The admin's path from first login to live: connect → check → back-test → shadow → live.
export default function OnboardingPage() {
  const me = useMe();
  const qc = useQueryClient();
  const isAdmin = me.data?.role === "admin";
  const q = useQuery({
    queryKey: ["onboarding"],
    queryFn: () => api<OnboardingStatus>("/onboarding"),
    enabled: isAdmin,
    refetchInterval: 10_000,
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["onboarding"] });
    qc.invalidateQueries({ queryKey: ["me"] });
  };
  if (me.data && !isAdmin) return <p className="text-sm text-muted">Only admins can set up Casefile.</p>;
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorNote error={q.error} />;
  const s = q.data;
  if (!s) return null;
  const permitted = !!pick(s, "permission").data.recorded;
  return (
    <>
      <PageHeader title="Set up Casefile" subtitle={modeText[s.mode]}>
        <Badge tone={s.mode === "live" ? "ok" : s.mode === "shadow" ? "accent" : "warn"}>{s.mode} mode</Badge>
      </PageHeader>
      <div className="space-y-4">
        <Card n={1} title="Company details" state={pick(s, "company").state}><CompanyStep key={JSON.stringify(pick(s, "company").data)} step={pick(s, "company")} onDone={refresh} /></Card>
        <Card n={2} title="Data permission" state={pick(s, "permission").state}><PermissionStep step={pick(s, "permission")} onDone={refresh} /></Card>
        <Card n={3} title="Connect your CRM" state={pick(s, "crm").state}><CrmStep step={pick(s, "crm")} allowed={permitted} onDone={refresh} /></Card>
        <Card n={4} title="Product usage" state={pick(s, "usage").state}><UsageStep step={pick(s, "usage")} allowed={permitted} onDone={refresh} /></Card>
        <Card n={5} title="Check what was found" state={pick(s, "data").state}><DataStep step={pick(s, "data")} /></Card>
        <Card n={6} title="Back-test report" state={pick(s, "backtest").state}><BacktestStep step={pick(s, "backtest")} onDone={refresh} /></Card>
        <Card n={7} title="Shadow mode" state={pick(s, "shadow").state}><ShadowStep s={s} step={pick(s, "shadow")} onDone={refresh} /></Card>
        <Card n={8} title="Go live" state={pick(s, "live").state}><LiveStep s={s} step={pick(s, "live")} onDone={refresh} /></Card>
      </div>
    </>
  );
}
