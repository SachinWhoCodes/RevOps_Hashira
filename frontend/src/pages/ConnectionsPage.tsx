import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type ConnView, type ConnectOverview, type ConnectionRow } from "../api";
import { useMe } from "../App";
import { Badge, ErrorNote, Loading, Modal, PageHeader } from "../components/ui";
import { ago, dateTime, label } from "../format";

const freshTone = { fresh: "ok", stale: "warn", never_synced: "neutral" } as const;
const input = "min-w-0 flex-1 rounded-md border border-line bg-panel px-3 py-1.5 font-mono text-sm";
const primary = "rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60";
const secondary = "rounded-md border border-line px-3 py-1.5 text-sm hover:bg-canvas disabled:opacity-60";
const linkish = "text-accent hover:underline";

// ---------------------------------------------------------------- guides (shown in a modal)

function Steps({ children }: { children: ReactNode }) {
  return <ol className="list-decimal space-y-2 pl-5">{children}</ol>;
}
function Code({ children }: { children: ReactNode }) {
  return <code className="rounded bg-canvas px-1 py-0.5 font-mono text-xs">{children}</code>;
}
function Note({ children }: { children: ReactNode }) {
  return <p className="rounded-md bg-canvas px-3 py-2 text-xs text-muted">{children}</p>;
}

function HubSpotReadGuide() {
  return (
    <>
      <p>Casefile reads companies, contacts, deals and the emails, meetings, notes, tasks and calls logged on them. It needs a HubSpot app with <b>read</b> permissions only.</p>
      <Steps>
        <li>In HubSpot open <b>Settings</b> (gear icon) → <b>Integrations</b> → <b>Private Apps</b>. In newer accounts this is <b>Development</b> → <b>Legacy apps</b> → <b>Create</b> → <b>Private</b>. You need to be a super admin.</li>
        <li>Name the app <b>Casefile (read)</b>.</li>
        <li>On the <b>Scopes</b> tab tick only: <Code>crm.objects.companies.read</Code> <Code>crm.objects.contacts.read</Code> <Code>crm.objects.deals.read</Code> <Code>crm.objects.owners.read</Code>, and <Code>sales-email-read</Code> so logged emails can be read (optional).</li>
        <li>Click <b>Create app</b>, then <b>Show token</b> and copy it (it starts with <Code>pat-</Code>).</li>
        <li>Paste it here and press <b>Check and connect</b>.</li>
      </Steps>
      <Note>Casefile checks the token for real before saving: it reads your portal id, asks for one record id of each kind (nothing is kept), and refuses a token that can also change records. The token is stored encrypted and never shown again.</Note>
    </>
  );
}

function HubSpotWriteGuide() {
  return (
    <>
      <p>Approved actions create a task or a note on the deal and update the deal's next step. That needs a <b>second</b> app, so the part of Casefile that reads your data never holds write access.</p>
      <Steps>
        <li>Create another private app the same way, named <b>Casefile (write)</b>.</li>
        <li>On <b>Scopes</b> tick <Code>crm.objects.deals.write</Code>. HubSpot accepts it for creating tasks and notes and for updating deals; nothing else is needed.</li>
        <li>Create the app, copy its token (<Code>pat-…</Code>) and paste it here.</li>
      </Steps>
      <Note>The check sends empty batches (zero records), so nothing is created; HubSpot answers whether the token may create tasks and notes and update deals. The token is then sealed so that only Casefile's executor can open it, and nothing is written until you switch to live mode and someone approves a line.</Note>
    </>
  );
}

function GoogleGuide({ redirect, platform }: { redirect: string; platform: boolean }) {
  return (
    <>
      <p>Casefile reads customer email and meetings from a sales mailbox (read-only), and can save follow-up emails as <b>drafts</b> for a person to send. It never sends email.</p>
      {platform ? (
        <>
          <h3 className="font-semibold">The quick way</h3>
          <Steps>
            <li>Press <b>Connect a mailbox</b> and sign in with the Google account your team sells from.</li>
            <li>Google lists what Casefile asks for (read Gmail, read Calendar). Leave every box ticked and press <b>Continue</b>.</li>
            <li>For drafts, press <b>Allow drafts</b> and grant “manage drafts” (a separate, smaller permission).</li>
          </Steps>
          <Note>If Google shows “Google hasn't verified this app”, Casefile's Google app is still waiting for Google's review: choose <b>Advanced</b> → <b>Go to Casefile</b> only if you trust this Casefile server. Until the review is done Google limits the app to 100 users in total. Prefer the dedicated way below if your company uses Google Workspace.</Note>
        </>
      ) : (
        <Note>This server has no shared Google app, so connect with your company's own app (below).</Note>
      )}
      <h3 className="font-semibold">The dedicated way (Google Workspace, about 10 minutes)</h3>
      <p className="text-muted">Your own Google app, visible only inside your company: no Google review, no user limit, and the connection does not expire.</p>
      <Steps>
        <li>Open <a className={linkish} href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noreferrer">Google Cloud console</a> signed in as a Workspace admin and create a project named <b>Casefile</b>.</li>
        <li><b>APIs &amp; Services</b> → <b>Library</b>: enable <b>Gmail API</b> and <b>Google Calendar API</b>.</li>
        <li><b>Google Auth Platform</b> (OAuth consent screen) → <b>Get started</b>: app name Casefile, your support email, audience <b>Internal</b>.</li>
        <li><b>Data access</b> → <b>Add scopes</b>: <Code>gmail.readonly</Code> <Code>calendar.readonly</Code> <Code>gmail.compose</Code>.</li>
        <li><b>Clients</b> → <b>Create client</b> → type <b>Web application</b>. Under <b>Authorised redirect URIs</b> add exactly:<div className="mt-1"><Code>{redirect}</Code></div></li>
        <li>Copy the <b>Client ID</b> and <b>Client secret</b>, paste them under “Use your company's own Google app” and save. Then press <b>Connect a mailbox</b>.</li>
      </Steps>
      <Note>“Internal” is only available to Google Workspace organisations. Personal @gmail.com accounts can use the quick way.</Note>
    </>
  );
}

function SlackGuide({ onCopy, copied }: { onCopy: () => void; copied: boolean }) {
  return (
    <>
      <p>Casefile sends each account owner a direct message with their cases, a morning digest, and buttons to approve routine actions. You create a small Slack app from a ready-made definition (a manifest), so no permissions need picking by hand.</p>
      <Steps>
        <li>
          Copy the manifest: <button className={secondary} onClick={onCopy}>{copied ? "Copied" : "Copy manifest"}</button>{" "}
          or <a className={linkish} href="/api/connect/slack/manifest">download it</a>.
        </li>
        <li>Open <a className={linkish} href="https://api.slack.com/apps?new_app=1" target="_blank" rel="noreferrer">api.slack.com/apps</a> → <b>Create New App</b> → <b>From a manifest</b>, pick your workspace, paste it, and create the app.</li>
        <li><b>Install App</b> (left menu) → <b>Install to Workspace</b> → <b>Allow</b>. Copy the <b>Bot User OAuth Token</b> (<Code>xoxb-…</Code>).</li>
        <li><b>Basic Information</b> → <b>App-Level Tokens</b> → <b>Generate Token and Scopes</b>: name it <b>casefile-socket</b>, add the scope <Code>connections:write</Code>, generate, and copy it (<Code>xapp-…</Code>).</li>
        <li>Paste both tokens here and press <b>Check and connect</b>.</li>
      </Steps>
      <Note>Casefile matches your team to Slack people by email. Owners it can't find get their messages through you, marked “meant for …”. The bot token is sealed for the executor (it posts messages); the app-level token only receives button clicks.</Note>
    </>
  );
}

// ---------------------------------------------------------------- building blocks

function Status({ c }: { c: ConnView }) {
  const ex = c.executor;
  return (
    <div className="rounded-md border border-line px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{c.zone === "write" ? "Write" : "Read"} · {c.name}</span>
        {typeof c.config.portal_id === "string" && <span className="text-faint">portal {c.config.portal_id}</span>}
        {typeof c.config.team === "string" && <span className="text-faint">workspace {c.config.team}</span>}
        {typeof c.config.mailbox === "string" && c.config.mailbox !== c.name && <span className="text-faint">{c.config.mailbox}</span>}
        <Badge>{c.credential}</Badge>
        {ex && (
          <Badge tone={ex.ready ? "ok" : ex.seen ? "risk" : "neutral"} title="Only the executor can open write credentials; this is what it last reported.">
            {ex.ready ? "executor ready" : ex.seen ? "executor cannot use it" : "executor not seen"}
          </Badge>
        )}
      </div>
      {c.zone === "read" && (
        <div className="mt-0.5 text-muted">{c.last_success_at ? `last sync ${dateTime(c.last_success_at)}` : "waiting for the first sync"}</div>
      )}
      {typeof c.config.linked_users === "number" && <div className="mt-0.5 text-muted">{c.config.linked_users} team members matched to Slack</div>}
      {(c.last_error || ex?.error) && <div className="mt-0.5 text-risk">{ex?.error ?? c.last_error}</div>}
    </div>
  );
}

function TokenForm({ placeholder, label: aria, button, disabled, onSubmit, pending, fields = 1, placeholders }: {
  placeholder: string; label: string; button: string; disabled?: boolean; pending: boolean;
  onSubmit: (values: string[]) => void; fields?: number; placeholders?: string[];
}) {
  const [values, setValues] = useState<string[]>(Array.from({ length: fields }, () => ""));
  return (
    <form className="flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); onSubmit(values); setValues(values.map(() => "")); }}>
      {values.map((v, i) => (
        <input key={i} type="password" autoComplete="off" spellCheck={false} value={v} aria-label={`${aria} ${i + 1}`}
          placeholder={placeholders?.[i] ?? placeholder} className={input}
          onChange={(e) => setValues(values.map((x, j) => (j === i ? e.target.value : x)))} />
      ))}
      <button className={primary} disabled={disabled || pending || values.some((v) => !v.trim())}>{pending ? "Checking…" : button}</button>
    </form>
  );
}

function Card({ title, subtitle, guide, children }: { title: string; subtitle: string; guide: () => void; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="text-xs text-muted">{subtitle}</p>
        </div>
        <button className={secondary} onClick={guide}>How to get these</button>
      </header>
      <div className="space-y-3 p-4 text-sm">{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------- page

type Guide = "hubspot-read" | "hubspot-write" | "google" | "slack" | null;

export default function ConnectionsPage() {
  const me = useMe();
  const qc = useQueryClient();
  const isAdmin = me.data?.role === "admin";
  const [params, setParams] = useSearchParams();
  const [guide, setGuide] = useState<Guide>(null);
  const [copied, setCopied] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const overview = useQuery({ queryKey: ["connect"], queryFn: () => api<ConnectOverview>("/connect"), enabled: isAdmin });
  const conns = useQuery({ queryKey: ["connections"], queryFn: () => api<ConnectionRow[]>("/connections"), refetchInterval: 10_000 });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["connect"] }); qc.invalidateQueries({ queryKey: ["connections"] }); qc.invalidateQueries({ queryKey: ["onboarding"] }); };
  const call = useMutation({
    mutationFn: ({ path, method, json }: { path: string; method?: string; json?: unknown }) => api<Record<string, unknown>>(path, { method, json: json ?? {} }),
    onSuccess: (data, v) => {
      refresh();
      if (v.path === "/connect/slack") setNotice(`Slack connected: ${data.linked} team members matched.`);
      else if (v.path.startsWith("/connect/hubspot")) setNotice("HubSpot connected.");
      else if (v.method === "DELETE") setNotice("Disconnected. Data already synced is kept.");
      else if (v.path === "/connect/google/client") setNotice("Your Google app is saved. Now connect a mailbox.");
    },
  });
  const google = useMutation({
    mutationFn: (purpose: "read" | "drafts") => api<{ url: string }>("/connect/google/start", { json: { purpose } }),
    onSuccess: ({ url }) => { window.location.href = url; },
  });
  const sync = useMutation({ mutationFn: (id: string) => api(`/connections/${id}/sync`, { json: {} }), onSuccess: refresh });

  const googleBack = params.get("google");
  const googleError = params.get("google_error");
  const o = overview.data;
  const blocked = !!o && !o.permission_recorded;
  const copyManifest = () => {
    if (!o) return;
    navigator.clipboard.writeText(JSON.stringify(o.slack_manifest, null, 2)).then(() => setCopied(true));
  };
  const disconnect = (c: ConnView) => {
    if (window.confirm(`Disconnect ${label(c.kind)} (${c.name})? Casefile forgets the credential; data already synced is kept.`)) {
      call.mutate({ path: `/connect/${c.id}`, method: "DELETE" });
    }
  };

  return (
    <>
      <PageHeader title="Connections" subtitle="Connect your tools yourself. Read access feeds Casefile; write access is used only for lines a person approved, and only in live mode." />
      {(googleBack || googleError) && (
        <div className={`mb-4 flex items-start justify-between gap-2 rounded-md px-3 py-2 text-sm ${googleError ? "bg-risk-soft text-risk" : "bg-ok-soft text-ok"}`}>
          <span>{googleError ? `Google was not connected: ${googleError}` : googleBack === "drafts" ? `Drafts allowed in ${params.get("mailbox")}.` : `${params.get("mailbox")} connected; the first sync has started.`}</span>
          <button onClick={() => setParams({})} aria-label="Dismiss">×</button>
        </div>
      )}
      {notice && (
        <div className="mb-4 flex items-start justify-between gap-2 rounded-md bg-ok-soft px-3 py-2 text-sm text-ok">
          <span>{notice}</span><button onClick={() => setNotice(null)} aria-label="Dismiss">×</button>
        </div>
      )}
      {call.error && <div className="mb-4"><ErrorNote error={call.error} /></div>}
      {google.error && <div className="mb-4"><ErrorNote error={google.error} /></div>}

      {isAdmin && overview.isLoading && <Loading />}
      {isAdmin && o && (
        <div className="mb-6 space-y-4">
          {blocked && (
            <p className="rounded-md bg-warn-soft px-3 py-2 text-sm text-warn">
              Record the data permission on the <Link to="/setup" className="underline">Setup page</Link> before connecting anything.
            </p>
          )}
          {(!o.server.can_store_credentials || !o.server.can_seal_for_executor) && (
            <p className="rounded-md bg-risk-soft px-3 py-2 text-sm text-risk">
              This server is not ready to store credentials ({!o.server.can_store_credentials && "SECRETS_KEY"}{!o.server.can_store_credentials && !o.server.can_seal_for_executor && ", "}{!o.server.can_seal_for_executor && "executor sealing key"} missing). Ask your operator.
            </p>
          )}
          {o.synthetic.length > 0 && <p className="rounded-md bg-warn-soft px-3 py-2 text-xs text-warn">This organisation also reads a synthetic data set: results from it are simulated.</p>}

          <Card title="HubSpot" subtitle="Your CRM: companies, contacts, deals and logged activity." guide={() => setGuide("hubspot-read")}>
            {o.hubspot.read.map((c) => (
              <div key={c.id} className="flex items-start gap-2"><div className="min-w-0 flex-1"><Status c={c} /></div><button className={secondary} onClick={() => disconnect(c)}>Disconnect</button></div>
            ))}
            <div>
              <div className="mb-1 text-xs font-medium text-muted">{o.hubspot.read.length ? "Replace the read token" : "1. Read-only app token"}</div>
              <TokenForm placeholder="pat-… (read app)" label="HubSpot read token" button="Check and connect" disabled={blocked} pending={call.isPending}
                onSubmit={([token]) => call.mutate({ path: "/connect/hubspot/read", json: { token } })} />
            </div>
            <div className="border-t border-line pt-3">
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs font-medium text-muted">
                <span>2. Write app token (for approved tasks, notes and next steps)</span>
                <button className={linkish} onClick={() => setGuide("hubspot-write")}>How to get it</button>
              </div>
              {o.hubspot.write.map((c) => (
                <div key={c.id} className="mb-2 flex items-start gap-2"><div className="min-w-0 flex-1"><Status c={c} /></div><button className={secondary} onClick={() => disconnect(c)}>Disconnect</button></div>
              ))}
              <TokenForm placeholder="pat-… (write app)" label="HubSpot write token" button="Check and connect" pending={call.isPending}
                disabled={blocked || o.hubspot.read.length === 0}
                onSubmit={([token]) => call.mutate({ path: "/connect/hubspot/write", json: { token } })} />
              {o.hubspot.read.length === 0 && <p className="mt-1 text-xs text-faint">Connect the read app first.</p>}
            </div>
          </Card>

          <Card title="Google: Gmail and Calendar" subtitle="Customer emails and meetings (read-only), and drafts for follow-ups. Casefile never sends email." guide={() => setGuide("google")}>
            {o.google.mailboxes.map((m) => (
              <div key={m.mailbox} className="space-y-1">
                {m.gmail && <div className="flex items-start gap-2"><div className="min-w-0 flex-1"><Status c={m.gmail} /></div><button className={secondary} onClick={() => m.gmail && disconnect(m.gmail)}>Disconnect</button></div>}
                {m.calendar && <Status c={m.calendar} />}
              </div>
            ))}
            {o.google.drafts.map((c) => (
              <div key={c.id} className="flex items-start gap-2"><div className="min-w-0 flex-1"><Status c={c} /></div><button className={secondary} onClick={() => disconnect(c)}>Disconnect</button></div>
            ))}
            <div className="flex flex-wrap gap-2">
              <button className={primary} disabled={blocked || o.google.client === "none" || google.isPending} onClick={() => google.mutate("read")}>
                {o.google.mailboxes.length ? "Connect another mailbox" : "Connect a mailbox"}
              </button>
              <button className={secondary} disabled={blocked || o.google.client === "none" || google.isPending} onClick={() => google.mutate("drafts")}>
                {o.google.drafts.length ? "Re-allow drafts" : "Allow drafts"}
              </button>
            </div>
            <p className="text-xs text-faint">
              Signing in with {o.google.client === "own" ? `your company's own Google app (${o.google.own_client_id})` : o.google.client === "platform" ? "Casefile's Google app" : "no Google app yet: add your own below"}.
            </p>
            <details className="rounded-md border border-line px-3 py-2">
              <summary className="cursor-pointer text-xs font-medium">Use your company's own Google app (recommended for Google Workspace)</summary>
              <p className="mt-2 text-xs text-muted">Redirect URI to register in Google Cloud: <Code>{o.server.google_redirect_uri}</Code></p>
              <div className="mt-2">
                <TokenForm placeholder="" label="Google client" button="Save" pending={call.isPending} disabled={blocked} fields={2}
                  placeholders={["Client ID (…apps.googleusercontent.com)", "Client secret"]}
                  onSubmit={([client_id, client_secret]) => call.mutate({ path: "/connect/google/client", method: "PUT", json: { client_id, client_secret } })} />
              </div>
              {o.google.client === "own" && (
                <button className={`${linkish} mt-2 text-xs`} onClick={() => call.mutate({ path: "/connect/google/client", method: "DELETE" })}>Stop using our own app</button>
              )}
            </details>
          </Card>

          <Card title="Slack" subtitle="Direct messages to account owners, the morning digest, and approval buttons." guide={() => setGuide("slack")}>
            {o.slack.map((c) => (
              <div key={c.id} className="flex items-start gap-2"><div className="min-w-0 flex-1"><Status c={c} /></div><button className={secondary} onClick={() => disconnect(c)}>Disconnect</button></div>
            ))}
            <TokenForm placeholder="" label="Slack token" button="Check and connect" fields={2} pending={call.isPending} disabled={blocked}
              placeholders={["Bot token xoxb-…", "App-level token xapp-…"]}
              onSubmit={([bot_token, app_token]) => call.mutate({ path: "/connect/slack", json: { bot_token, app_token } })} />
          </Card>

          <section className="rounded-lg border border-line bg-panel p-4 text-sm">
            <h2 className="text-sm font-semibold">Product usage</h2>
            <p className="mt-1 text-muted">Daily usage per customer: upload a CSV on the <Link to="/setup" className={linkish}>Setup page</Link>.</p>
          </section>
        </div>
      )}

      <h2 className="mb-2 text-sm font-semibold">Sync status</h2>
      {conns.isLoading && <Loading />}
      {conns.error && <ErrorNote error={conns.error} />}
      <div className="grid gap-3 md:grid-cols-2">
        {conns.data?.filter((c) => c.status !== "disabled").map((c) => (
          <div key={c.id} className="rounded-lg border border-line bg-panel p-4">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate font-medium">{label(c.kind)} · {c.name}</div>
                <div className="text-xs text-faint">{c.zone} · every {c.interval_minutes} min · {c.raw_records.toLocaleString()} raw records</div>
              </div>
              <Badge tone={c.status === "error" ? "risk" : freshTone[c.freshness.state]}>
                {c.status === "error" ? "error" : c.freshness.state === "stale" ? `stale since ${ago(c.freshness.stale_since)}` : label(c.freshness.state)}
              </Badge>
            </div>
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              <dt className="text-muted">Last success</dt><dd>{dateTime(c.last_success_at)}</dd>
              {c.last_run && (<><dt className="text-muted">Last run</dt><dd>{c.last_run.status} · fetched {c.last_run.fetched.toLocaleString()} · new {c.last_run.new.toLocaleString()}</dd></>)}
              {c.last_error && (<><dt className="text-muted">Last error</dt><dd className="text-risk">{c.last_error} ({c.consecutive_failures}×)</dd></>)}
            </dl>
            {isAdmin && c.zone === "read" && (
              <button onClick={() => sync.mutate(c.id)} disabled={sync.isPending} className={`${secondary} mt-3`}>Sync now</button>
            )}
          </div>
        ))}
      </div>

      {guide === "hubspot-read" && <Modal title="Connect HubSpot (read-only)" onClose={() => setGuide(null)}><HubSpotReadGuide /></Modal>}
      {guide === "hubspot-write" && <Modal title="Give Casefile write access to HubSpot" onClose={() => setGuide(null)}><HubSpotWriteGuide /></Modal>}
      {guide === "google" && o && (
        <Modal title="Connect Gmail and Google Calendar" onClose={() => setGuide(null)}>
          <GoogleGuide redirect={o.server.google_redirect_uri} platform={o.server.google_platform_app} />
        </Modal>
      )}
      {guide === "slack" && <Modal title="Connect Slack" onClose={() => setGuide(null)}><SlackGuide onCopy={copyManifest} copied={copied} /></Modal>}
    </>
  );
}
