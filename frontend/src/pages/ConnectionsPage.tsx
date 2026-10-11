import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpenCheck,
  Briefcase,
  ChartColumn,
  ChevronDown,
  Copy,
  Download,
  ExternalLink,
  KeyRound,
  Lock,
  Mail,
  MessageSquare,
  RefreshCw,
  ShieldCheck,
  Unplug,
  type LucideIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type ConnView, type ConnectOverview, type ConnectionRow } from "../api";
import { Badge, Button, ButtonAnchor, Callout, Card, ErrorNote, Input, Loading, Modal, PageHeader, StatusDot, Tooltip, useToast, type Tone } from "../components/ui";
import { ago, dateTime, label } from "../format";
import { cn } from "../lib/cn";
import { useMe } from "../lib/session";

const freshTone = { fresh: "ok", stale: "warn", never_synced: "neutral" } as const;

// ---------------------------------------------------------------- guide building blocks

function Steps({ children }: { children: ReactNode }) {
  return <ol className="space-y-3 [counter-reset:step]">{children}</ol>;
}
function Step({ children }: { children: ReactNode }) {
  return (
    <li className="relative pl-9 [counter-increment:step] before:absolute before:top-0 before:left-0 before:grid before:size-6 before:place-items-center before:rounded-full before:bg-accent-soft before:text-xs before:font-semibold before:text-accent-fg before:content-[counter(step)]">
      {children}
    </li>
  );
}
function Code({ children }: { children: ReactNode }) {
  return <code className="rounded-md border border-line bg-subtle px-1.5 py-0.5 font-mono text-[12px] text-ink">{children}</code>;
}
function Note({ children }: { children: ReactNode }) {
  return (
    <div className="flex gap-2.5 rounded-xl border border-line bg-subtle px-4 py-3 text-[13px] text-muted">
      <ShieldCheck className="mt-0.5 size-4 shrink-0 text-ok-fg" aria-hidden />
      <div>{children}</div>
    </div>
  );
}
const ext = "inline-flex items-center gap-0.5 font-medium text-accent-fg hover:underline";

function HubSpotReadGuide() {
  return (
    <>
      <p>Casefile reads companies, contacts, deals and the emails, meetings, notes, tasks and calls logged on them. It needs a HubSpot app with <b>read</b> permissions only.</p>
      <Steps>
        <Step>In HubSpot open <b>Development</b> → <b>Keys</b> → <b>Service Keys</b> → <b>Create service key</b>. (Older accounts: <b>Settings</b> → <b>Integrations</b> → <b>Private Apps</b>.) You need to be a super admin.</Step>
        <Step>Name it <b>casefile-read</b>.</Step>
        <Step>Add only these scopes: <Code>crm.objects.companies.read</Code> <Code>crm.objects.contacts.read</Code> <Code>crm.objects.deals.read</Code> <Code>crm.objects.owners.read</Code>, and optionally <Code>sales-email-read</Code> so logged emails can be read.</Step>
        <Step>Create it, then <b>Show</b> and copy the key (it starts with <Code>pat-</Code>).</Step>
        <Step>Paste it here and press <b>Check and connect</b>.</Step>
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
        <Step>Create a second service key the same way, named <b>casefile-write</b>.</Step>
        <Step>Add the scope <Code>crm.objects.deals.write</Code>. That is enough: Casefile attaches approved tasks and notes to the customer's renewal deal (HubSpot shows the deal on the company) and updates the deal's next step.</Step>
        <Step>Create it, copy the key (<Code>pat-…</Code>) and paste it here.</Step>
      </Steps>
      <Note>Why not attach to the company too? HubSpot only lets a key link a record to a company if the key may also <i>change</i> companies (<Code>crm.objects.companies.write</Code>). Casefile does not ask for that. Add it only if you want actions filed on companies that have no deal.</Note>
      <Note>The check sends empty batches (zero records), so nothing is created; HubSpot answers whether the token may create tasks and notes and update deals. The token is then sealed so only Casefile's executor can open it, and nothing is written until you switch to live mode and someone approves a line.</Note>
    </>
  );
}

function GoogleGuide({ redirect, platform }: { redirect: string; platform: boolean }) {
  return (
    <>
      <p>Casefile reads customer email and meetings from a sales mailbox (read-only), and can save follow-up emails as <b>drafts</b> for a person to send. It never sends email.</p>
      {platform ? (
        <>
          <h3 className="text-[14px] font-semibold text-ink">The quick way</h3>
          <Steps>
            <Step>Press <b>Connect a mailbox</b> and sign in with the Google account your team sells from.</Step>
            <Step>Google lists what Casefile asks for (read Gmail, read Calendar). Leave every box ticked and press <b>Continue</b>.</Step>
            <Step>For drafts, press <b>Allow drafts</b> and grant “manage drafts” (a separate, smaller permission).</Step>
          </Steps>
          <Note>If Google shows “Google hasn't verified this app”, Casefile's Google app is still waiting for Google's review: choose <b>Advanced</b> → <b>Go to Casefile</b> only if you trust this server. Until the review is done Google limits the app to 100 users. Prefer the dedicated way below if your company uses Google Workspace.</Note>
        </>
      ) : (
        <Note>This server has no shared Google app, so connect with your company's own app (below).</Note>
      )}
      <h3 className="text-[14px] font-semibold text-ink">The dedicated way · Google Workspace, about 10 minutes</h3>
      <p className="text-muted">Your own Google app, visible only inside your company: no Google review, no user limit, and the connection does not expire.</p>
      <Steps>
        <Step>Open the <a className={ext} href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noreferrer">Google Cloud console <ExternalLink className="size-3" /></a> as a Workspace admin and create a project named <b>Casefile</b>.</Step>
        <Step><b>APIs &amp; Services</b> → <b>Library</b>: enable <b>Gmail API</b> and <b>Google Calendar API</b>.</Step>
        <Step><b>Google Auth Platform</b> (OAuth consent screen) → <b>Get started</b>: app name Casefile, your support email, audience <b>Internal</b>.</Step>
        <Step><b>Data access</b> → <b>Add scopes</b>: <Code>gmail.readonly</Code> <Code>calendar.readonly</Code> <Code>gmail.compose</Code>.</Step>
        <Step><b>Clients</b> → <b>Create client</b> → <b>Web application</b>. Under <b>Authorised redirect URIs</b> add exactly:<div className="mt-1.5"><Code>{redirect}</Code></div></Step>
        <Step>Copy the <b>Client ID</b> and <b>Client secret</b>, paste them under “Use your company's own Google app” and save. Then press <b>Connect a mailbox</b>.</Step>
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
        <Step>
          <div className="flex flex-wrap items-center gap-2">
            Copy the manifest:
            <Button size="xs" icon={Copy} onClick={onCopy}>{copied ? "Copied" : "Copy manifest"}</Button>
            <ButtonAnchor size="xs" variant="ghost" icon={Download} href="/api/connect/slack/manifest">Download</ButtonAnchor>
          </div>
        </Step>
        <Step>Open <a className={ext} href="https://api.slack.com/apps?new_app=1" target="_blank" rel="noreferrer">api.slack.com/apps <ExternalLink className="size-3" /></a> → <b>Create New App</b> → <b>From a manifest</b>, pick your workspace, paste it, and create the app.</Step>
        <Step><b>Install App</b> → <b>Install to Workspace</b> → <b>Allow</b>. Copy the <b>Bot User OAuth Token</b> (<Code>xoxb-…</Code>).</Step>
        <Step><b>Basic Information</b> → <b>App-Level Tokens</b> → <b>Generate Token and Scopes</b>: name it <b>casefile-socket</b>, add <Code>connections:write</Code>, generate, and copy it (<Code>xapp-…</Code>).</Step>
        <Step>Paste both tokens here and press <b>Check and connect</b>.</Step>
      </Steps>
      <Note>Casefile matches your team to Slack people by email. Owners it can't find get their messages through you, marked “meant for …”. The bot token is sealed for the executor (it posts messages); the app-level token only receives button clicks.</Note>
    </>
  );
}

// ---------------------------------------------------------------- building blocks

// Which part of a tool this row is: Gmail and Calendar share one sign-in, so name them.
function connTitle(c: ConnView): string {
  if (c.kind === "gmail") return c.zone === "write" ? "Gmail drafts" : "Gmail";
  if (c.kind === "gcal") return "Calendar";
  return c.zone === "write" ? "Write" : "Read";
}

function ConnLine({ c, onDisconnect, onReconnect }: { c: ConnView; onDisconnect?: () => void; onReconnect?: () => void }) {
  const ex = c.executor;
  const error = c.problem ?? ex?.error ?? c.last_error;
  return (
    <div className="flex items-start gap-3 rounded-xl border border-line bg-subtle px-3.5 py-3">
      <StatusDot tone={error ? "risk" : c.zone === "write" ? (ex?.ready ? "ok" : ex?.seen ? "risk" : "neutral") : c.last_success_at ? "ok" : "warn"} className="mt-1.5" />
      <div className="min-w-0 flex-1 text-[13px]">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-medium text-ink">{connTitle(c)} · {c.name}</span>
          {typeof c.config.portal_id === "string" && <span className="text-faint">portal {c.config.portal_id}</span>}
          {typeof c.config.team === "string" && <span className="text-faint">workspace {c.config.team}</span>}
          {typeof c.config.mailbox === "string" && c.config.mailbox !== c.name && <span className="text-faint">{c.config.mailbox}</span>}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <Badge icon={Lock}>{c.credential}</Badge>
          {ex && (
            <Tooltip content="Only the executor can open write credentials; this is what it last reported.">
              <Badge tone={ex.ready ? "ok" : ex.seen ? "risk" : "neutral"}>{ex.ready ? "Executor ready" : ex.seen ? "Executor cannot use it" : "Executor not seen yet"}</Badge>
            </Tooltip>
          )}
          {c.zone === "read" && <span className="text-xs text-muted">{c.last_success_at ? `Synced ${ago(c.last_success_at)}` : "Waiting for the first sync"}</span>}
          {typeof c.config.linked_users === "number" && <span className="text-xs text-muted">{c.config.linked_users} team members matched</span>}
        </div>
        {error && <div className="mt-1.5 text-xs text-risk-fg">{error}</div>}
        {c.needs_reconnect && onReconnect && (
          <Button size="sm" variant="primary" className="mt-2" onClick={onReconnect}>Reconnect</Button>
        )}
      </div>
      {onDisconnect && (
        <Tooltip content="Disconnect: Casefile forgets the credential; synced data is kept">
          <button onClick={onDisconnect} aria-label={`Disconnect ${c.name}`} className="rounded-lg p-1.5 text-faint transition-colors hover:bg-risk-soft hover:text-risk-fg">
            <Unplug className="size-4" />
          </button>
        </Tooltip>
      )}
    </div>
  );
}

function SecretForm({ fields, button, disabled, onSubmit, pending }: {
  fields: { label: string; placeholder: string }[];
  button: string;
  disabled?: boolean;
  pending: boolean;
  onSubmit: (values: string[]) => void;
}) {
  const [values, setValues] = useState<string[]>(fields.map(() => ""));
  return (
    <form
      className="flex flex-col gap-2 sm:flex-row"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(values);
        setValues(values.map(() => ""));
      }}
    >
      {fields.map((f, i) => (
        <Input
          key={i}
          icon={KeyRound}
          type="password"
          autoComplete="off"
          spellCheck={false}
          inputSize="sm"
          value={values[i]}
          aria-label={f.label}
          placeholder={f.placeholder}
          disabled={disabled}
          className="min-w-0 flex-1 [&_input]:font-mono"
          onChange={(e) => setValues(values.map((x, j) => (j === i ? e.target.value : x)))}
        />
      ))}
      <Button type="submit" variant="primary" size="sm" loading={pending} disabled={disabled || values.some((v) => !v.trim())}>
        {pending ? "Checking" : button}
      </Button>
    </form>
  );
}

function Integration({ icon: Icon, tile, title, subtitle, status, onGuide, children, delay = 0 }: {
  icon: LucideIcon;
  tile: string;
  title: string;
  subtitle: string;
  status: { tone: Tone; text: string };
  onGuide?: () => void;
  children: ReactNode;
  delay?: number;
}) {
  return (
    <section className="flex flex-col rounded-xl border border-line bg-panel shadow-xs animate-fade-up" style={{ animationDelay: `${delay}ms` }}>
      <header className="flex items-start gap-3.5 border-b border-line px-5 py-4">
        <span className={cn("grid size-10 shrink-0 place-items-center rounded-xl", tile)}>
          <Icon className="size-5" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-[15px] font-semibold tracking-[-0.01em]">{title}</h2>
            <Badge tone={status.tone} dot>{status.text}</Badge>
          </div>
          <p className="mt-0.5 text-[13px] text-muted">{subtitle}</p>
        </div>
        {onGuide && <Button size="xs" variant="ghost" icon={BookOpenCheck} onClick={onGuide}>Guide</Button>}
      </header>
      <div className="flex-1 space-y-4 px-5 py-4 text-sm">{children}</div>
    </section>
  );
}

function StepLabel({ n, children, action }: { n?: number; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-2 text-[12.5px] font-semibold text-ink">
        {n !== undefined && <span className="grid size-5 place-items-center rounded-full bg-hover text-[11px] text-muted">{n}</span>}
        {children}
      </div>
      {action}
    </div>
  );
}

// ---------------------------------------------------------------- page

type Guide = "hubspot-read" | "hubspot-write" | "google" | "slack" | null;

export default function ConnectionsPage() {
  const me = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const isAdmin = me.data?.role === "admin";
  const [params, setParams] = useSearchParams();
  const [guide, setGuide] = useState<Guide>(null);
  const [copied, setCopied] = useState(false);
  const [ownApp, setOwnApp] = useState(false);
  const overview = useQuery({ queryKey: ["connect"], queryFn: () => api<ConnectOverview>("/connect"), enabled: isAdmin });
  const conns = useQuery({ queryKey: ["connections"], queryFn: () => api<ConnectionRow[]>("/connections"), refetchInterval: 10_000 });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["connect"] });
    qc.invalidateQueries({ queryKey: ["connections"] });
    qc.invalidateQueries({ queryKey: ["onboarding"] });
  };
  const call = useMutation({
    mutationFn: ({ path, method, json }: { path: string; method?: string; json?: unknown }) => api<Record<string, unknown>>(path, { method, json: json ?? {} }),
    onSuccess: (data, v) => {
      refresh();
      const title =
        v.path === "/connect/slack"
          ? `Slack connected · ${data.linked} team members matched`
          : v.path.startsWith("/connect/hubspot")
            ? "HubSpot connected"
            : v.method === "DELETE"
              ? "Disconnected · data already synced is kept"
              : v.path === "/connect/google/client"
                ? "Your Google app is saved — now connect a mailbox"
                : "Saved";
      toast({ tone: "ok", title });
    },
  });
  const google = useMutation({
    mutationFn: (purpose: "read" | "drafts") => api<{ url: string }>("/connect/google/start", { json: { purpose } }),
    onSuccess: ({ url }) => {
      window.location.href = url;
    },
  });
  const sync = useMutation({
    mutationFn: (id: string) => api(`/connections/${id}/sync`, { json: {} }),
    onSuccess: () => {
      refresh();
      toast({ tone: "ok", title: "Sync requested", description: "The worker picks it up within a minute." });
    },
  });

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
  const statusOf = (read: ConnView[], write: ConnView[] = []): { tone: Tone; text: string } => {
    const all = [...read, ...write];
    if (!all.length) return { tone: "neutral", text: "Not connected" };
    if (all.some((c) => c.last_error || c.executor?.error)) return { tone: "warn", text: "Needs attention" };
    return { tone: "ok", text: write.length || !read.length ? "Connected" : "Read-only" };
  };

  return (
    <>
      <PageHeader
        title="Connections"
        description="Connect your tools yourself. Read access feeds Casefile; write access is used only for lines a person approved, and only in live mode."
      />

      <div className="space-y-3">
        {(googleBack || googleError) && (
          <Callout tone={googleError ? "risk" : "ok"} title={googleError ? "Google was not connected" : "Google connected"} onDismiss={() => setParams({})}>
            {googleError ?? (googleBack === "drafts" ? `Drafts allowed in ${params.get("mailbox")}.` : `${params.get("mailbox")} connected; the first sync has started.`)}
          </Callout>
        )}
        {call.error && <ErrorNote error={call.error} title="That credential was not accepted" />}
        {google.error && <ErrorNote error={google.error} />}
      </div>

      {isAdmin && overview.isLoading && <Loading variant="cards" rows={2} />}
      {isAdmin && o && (
        <div className="mt-3 space-y-4">
          {blocked && (
            <Callout tone="warn" title="Record the data permission first" action={<Link to="/setup" className="text-[13px] font-medium text-warn-fg hover:underline">Open setup →</Link>}>
              Casefile needs a record that someone allowed to decide agreed to how your data is used before anything is connected.
            </Callout>
          )}
          {(!o.server.can_store_credentials || !o.server.can_seal_for_executor) && (
            <Callout tone="risk" title="This server is not ready to store credentials">
              Missing: {[!o.server.can_store_credentials && "SECRETS_KEY", !o.server.can_seal_for_executor && "executor sealing key"].filter(Boolean).join(", ")}. Ask your operator.
            </Callout>
          )}
          {o.synthetic.length > 0 && <Callout tone="warn" title="Synthetic data">This organisation also reads a synthetic data set: results from it are simulated.</Callout>}

          <div className="grid gap-4 xl:grid-cols-2">
            <Integration
              icon={Briefcase}
              tile="bg-orange-500/12 text-orange-600 dark:text-orange-300"
              title="HubSpot"
              subtitle="Your CRM: companies, contacts, deals and logged activity."
              status={statusOf(o.hubspot.read, o.hubspot.write)}
              onGuide={() => setGuide("hubspot-read")}
            >
              <div>
                <StepLabel n={1}>Read access</StepLabel>
                <div className="space-y-2">
                  {o.hubspot.read.map((c) => <ConnLine key={c.id} c={c} onDisconnect={() => disconnect(c)} />)}
                  <SecretForm
                    fields={[{ label: "HubSpot read token", placeholder: o.hubspot.read.length ? "Replace the read token · pat-…" : "Read app token · pat-…" }]}
                    button="Check and connect"
                    disabled={blocked}
                    pending={call.isPending && call.variables?.path === "/connect/hubspot/read"}
                    onSubmit={([token]) => call.mutate({ path: "/connect/hubspot/read", json: { token } })}
                  />
                </div>
              </div>
              <div className="border-t border-line pt-4">
                <StepLabel n={2} action={<Button size="xs" variant="ghost" icon={BookOpenCheck} onClick={() => setGuide("hubspot-write")}>How to get it</Button>}>
                  Write access · for approved tasks, notes and next steps
                </StepLabel>
                <div className="space-y-2">
                  {o.hubspot.write.map((c) => <ConnLine key={c.id} c={c} onDisconnect={() => disconnect(c)} />)}
                  <SecretForm
                    fields={[{ label: "HubSpot write token", placeholder: "Write app token · pat-…" }]}
                    button="Check and connect"
                    disabled={blocked || o.hubspot.read.length === 0}
                    pending={call.isPending && call.variables?.path === "/connect/hubspot/write"}
                    onSubmit={([token]) => call.mutate({ path: "/connect/hubspot/write", json: { token } })}
                  />
                  {o.hubspot.read.length === 0 && <p className="text-xs text-faint">Connect read access first.</p>}
                </div>
              </div>
            </Integration>

            <Integration
              icon={Mail}
              tile="bg-rose-500/12 text-rose-600 dark:text-rose-300"
              title="Gmail & Google Calendar"
              subtitle="Customer emails and meetings (read-only), and drafts for follow-ups. Casefile never sends email."
              status={statusOf(o.google.mailboxes.flatMap((m) => [m.gmail, m.calendar].filter((x): x is ConnView => !!x)), o.google.drafts)}
              onGuide={() => setGuide("google")}
              delay={60}
            >
              {(o.google.mailboxes.length > 0 || o.google.drafts.length > 0) && (
                <div className="space-y-2">
                  {o.google.mailboxes.map((m) => (
                    <div key={m.mailbox} className="space-y-2">
                      {m.gmail && <ConnLine c={m.gmail} onDisconnect={() => m.gmail && disconnect(m.gmail)} onReconnect={() => google.mutate("read")} />}
                      {m.calendar && <ConnLine c={m.calendar} onReconnect={m.gmail?.needs_reconnect ? undefined : () => google.mutate("read")} />}
                    </div>
                  ))}
                  {o.google.drafts.map((c) => <ConnLine key={c.id} c={c} onDisconnect={() => disconnect(c)} onReconnect={() => google.mutate("drafts")} />)}
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" icon={Mail} disabled={blocked || o.google.client === "none"} loading={google.isPending && google.variables === "read"} onClick={() => google.mutate("read")}>
                  {o.google.mailboxes.length ? "Connect another mailbox" : "Connect a mailbox"}
                </Button>
                <Button disabled={blocked || o.google.client === "none"} loading={google.isPending && google.variables === "drafts"} onClick={() => google.mutate("drafts")}>
                  {o.google.drafts.length ? "Re-allow drafts" : "Allow drafts"}
                </Button>
              </div>
              <p className="text-xs text-muted">
                Signs in with{" "}
                {o.google.client === "own" ? `your company's own Google app (${o.google.own_client_id})` : o.google.client === "platform" ? "Casefile's Google app" : "no Google app yet — add your own below"}.
              </p>
              <div className="rounded-xl border border-line">
                <button onClick={() => setOwnApp(!ownApp)} aria-expanded={ownApp} className="flex w-full items-center justify-between gap-2 px-3.5 py-2.5 text-left text-[13px] font-medium text-ink">
                  Use your company's own Google app
                  <span className="flex items-center gap-2">
                    <span className="text-xs font-normal text-faint">recommended for Workspace</span>
                    <ChevronDown className={cn("size-4 text-faint transition-transform", ownApp && "rotate-180")} />
                  </span>
                </button>
                {ownApp && (
                  <div className="space-y-2.5 border-t border-line px-3.5 py-3 animate-fade-in">
                    <p className="text-xs text-muted">Redirect URI to register in Google Cloud: <Code>{o.server.google_redirect_uri}</Code></p>
                    <SecretForm
                      fields={[
                        { label: "Google client ID", placeholder: "Client ID (…apps.googleusercontent.com)" },
                        { label: "Google client secret", placeholder: "Client secret" },
                      ]}
                      button="Save"
                      disabled={blocked}
                      pending={call.isPending && call.variables?.path === "/connect/google/client"}
                      onSubmit={([client_id, client_secret]) => call.mutate({ path: "/connect/google/client", method: "PUT", json: { client_id, client_secret } })}
                    />
                    {o.google.client === "own" && (
                      <button className="text-xs font-medium text-risk-fg hover:underline" onClick={() => call.mutate({ path: "/connect/google/client", method: "DELETE" })}>
                        Stop using our own app
                      </button>
                    )}
                  </div>
                )}
              </div>
            </Integration>

            <Integration
              icon={MessageSquare}
              tile="bg-violet-500/12 text-violet-600 dark:text-violet-300"
              title="Slack"
              subtitle="Direct messages to account owners, the morning digest, and approval buttons."
              status={statusOf([], o.slack)}
              onGuide={() => setGuide("slack")}
              delay={120}
            >
              {o.slack.length > 0 && <div className="space-y-2">{o.slack.map((c) => <ConnLine key={c.id} c={c} onDisconnect={() => disconnect(c)} />)}</div>}
              <SecretForm
                fields={[
                  { label: "Slack bot token", placeholder: "Bot token · xoxb-…" },
                  { label: "Slack app-level token", placeholder: "App-level token · xapp-…" },
                ]}
                button="Check and connect"
                disabled={blocked}
                pending={call.isPending && call.variables?.path === "/connect/slack"}
                onSubmit={([bot_token, app_token]) => call.mutate({ path: "/connect/slack", json: { bot_token, app_token } })}
              />
            </Integration>

            <Integration
              icon={ChartColumn}
              tile="bg-sky-500/12 text-sky-600 dark:text-sky-300"
              title="Product usage"
              subtitle="Daily active users and key actions per customer — the strongest early-warning signal."
              status={o.usage.length ? { tone: "ok", text: "Connected" } : { tone: "neutral", text: "Not connected" }}
              delay={180}
            >
              {o.usage.map((c) => <ConnLine key={c.id} c={c} />)}
              <p className="text-[13px] text-muted">Upload a CSV (one row per customer per day) from the setup checklist. A template is provided there.</p>
              <Link to="/setup" className="inline-flex items-center gap-1 text-[13px] font-medium text-accent-fg hover:underline">Upload usage on the setup page →</Link>
            </Integration>
          </div>
        </div>
      )}

      <Card className="mt-6" title="Sync status" description="Every read source, how fresh it is, and what the last run fetched" icon={RefreshCw} flush>
        {conns.isLoading && <div className="p-5"><Loading rows={3} /></div>}
        {conns.error && <div className="p-5"><ErrorNote error={conns.error} /></div>}
        {conns.data && (
          <ul className="divide-y divide-line">
            {conns.data.filter((c) => c.status !== "disabled").map((c) => {
              const tone: Tone = c.status === "error" ? "risk" : freshTone[c.freshness.state];
              return (
                <li key={c.id} className="flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3.5">
                  <div className="flex min-w-56 flex-1 items-center gap-3">
                    <StatusDot tone={tone} pulse={tone === "ok"} />
                    <div className="min-w-0">
                      <div className="truncate text-[14px] font-medium text-ink capitalize">{label(c.kind)} · <span className="normal-case">{c.name}</span></div>
                      <div className="text-xs text-muted">
                        {c.zone} · every {c.interval_minutes} min · {c.raw_records.toLocaleString("en-IN")} raw records
                      </div>
                    </div>
                  </div>
                  <div className="min-w-40 text-xs text-muted">
                    <div>Last success <span className="text-ink">{c.last_success_at ? ago(c.last_success_at) : "never"}</span></div>
                    {c.last_run && <div>Last run {c.last_run.status} · {c.last_run.fetched.toLocaleString("en-IN")} fetched · {c.last_run.new.toLocaleString("en-IN")} new</div>}
                  </div>
                  <Badge tone={tone}>{c.status === "error" ? "Error" : c.freshness.state === "stale" ? `Stale since ${ago(c.freshness.stale_since)}` : label(c.freshness.state)}</Badge>
                  {isAdmin && c.zone === "read" && (
                    <Button size="xs" icon={RefreshCw} loading={sync.isPending && sync.variables === c.id} onClick={() => sync.mutate(c.id)}>Sync now</Button>
                  )}
                  {c.last_error && <div className="w-full text-xs text-risk-fg">{c.last_error} ({c.consecutive_failures}× · {dateTime(c.last_error_at)})</div>}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {guide === "hubspot-read" && <Modal title="Connect HubSpot (read-only)" icon={Briefcase} onClose={() => setGuide(null)}><HubSpotReadGuide /></Modal>}
      {guide === "hubspot-write" && <Modal title="Give Casefile write access to HubSpot" icon={Briefcase} onClose={() => setGuide(null)}><HubSpotWriteGuide /></Modal>}
      {guide === "google" && o && (
        <Modal title="Connect Gmail and Google Calendar" icon={Mail} onClose={() => setGuide(null)}>
          <GoogleGuide redirect={o.server.google_redirect_uri} platform={o.server.google_platform_app} />
        </Modal>
      )}
      {guide === "slack" && <Modal title="Connect Slack" icon={MessageSquare} onClose={() => setGuide(null)}><SlackGuide onCopy={copyManifest} copied={copied} /></Modal>}
    </>
  );
}
