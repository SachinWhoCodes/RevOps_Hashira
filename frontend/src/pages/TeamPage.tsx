import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, EllipsisVertical, KeyRound, Link2, Mail, MessageSquare, Power, UserPlus, UsersRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, type TeamMember } from "../api";
import { DataTable, type Column } from "../components/table/DataTable";
import { Avatar, Badge, Button, Callout, Checkbox, ErrorNote, Field, Input, Menu, Modal, PageHeader, Select, Tooltip, useToast } from "../components/ui";
import { ago, date } from "../format";
import { useMe } from "../lib/session";

const statusTone = { active: "ok", invited: "accent", no_login: "neutral" } as const;
const statusText = { active: "Can log in", invited: "Invited", no_login: "No login" } as const;
const ROLES = [
  { value: "owner", label: "Account owner", hint: "Works their own cases and approves their own plans" },
  { value: "leader", label: "Leader", hint: "Sees the whole book; read-only" },
  { value: "admin", label: "Admin", hint: "Everything, including setup, connections and the team" },
];

type InviteResult = { link: string | null; expires_at: string; emailed: boolean };

function LinkReveal({ link, expires }: { link: string; expires: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <Input readOnly value={link} onFocus={(e) => e.target.select()} className="flex-1 font-mono" inputSize="sm" />
        <Button icon={Copy} onClick={() => navigator.clipboard.writeText(link).then(() => setCopied(true))}>{copied ? "Copied" : "Copy"}</Button>
      </div>
      <p className="text-xs text-muted">Shown once. Works once and expires {date(expires)}. Sending a new invite cancels this one.</p>
    </div>
  );
}

function InviteModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({ email: "", name: "", role: "owner" });
  const [send, setSend] = useState(true);
  const [result, setResult] = useState<InviteResult | null>(null);
  const create = useMutation({
    mutationFn: async () => {
      const member = await api<TeamMember>("/team", { json: form });
      return api<InviteResult>(`/team/${member.id}/invite`, { method: "POST", json: { send_email: send } });
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["team"] });
      if (r.emailed) {
        toast({ tone: "ok", title: `Invite emailed to ${form.email}`, description: "It works once and expires in 72 hours." });
        onClose();
      } else setResult(r);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <Modal title={result ? "Share this invite link" : "Invite a person"} description={result ? `For ${form.email}` : "They choose their own password from the link."} icon={UserPlus} onClose={onClose} size="sm">
      {result?.link ? (
        <LinkReveal link={result.link} expires={result.expires_at} />
      ) : (
        <form id="invite-form" onSubmit={submit} className="space-y-4">
          <Field label="Work email" htmlFor="inv-email">
            <Input id="inv-email" type="email" required autoFocus value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="name@company.com" />
          </Field>
          <Field label="Name" htmlFor="inv-name" optional>
            <Input id="inv-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="Role" htmlFor="inv-role" hint={ROLES.find((r) => r.value === form.role)?.hint}>
            <Select id="inv-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </Select>
          </Field>
          <Checkbox checked={send} onChange={setSend} label="Email the invite now" description="Otherwise you get a link to send yourself." />
          {create.error && <ErrorNote error={create.error} title="Could not invite" />}
        </form>
      )}
      {!result && (
        <div className="flex justify-end gap-2 border-t border-line pt-4">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" form="invite-form" variant="primary" icon={send ? Mail : Link2} loading={create.isPending}>
            {send ? "Send invite" : "Create link"}
          </Button>
        </div>
      )}
    </Modal>
  );
}

// Admins decide who can log in: invite people, change roles, switch logins off.
export default function TeamPage() {
  const me = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const team = useQuery({ queryKey: ["team"], queryFn: () => api<TeamMember[]>("/team"), enabled: me.data?.role === "admin" });
  const [inviting, setInviting] = useState(false);
  const [reveal, setReveal] = useState<{ who: string; r: InviteResult } | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["team"] });
  const invite = useMutation({
    mutationFn: ({ m, send }: { m: TeamMember; send: boolean }) => api<InviteResult>(`/team/${m.id}/invite`, { method: "POST", json: { send_email: send } }),
    onSuccess: (r, { m }) => {
      refresh();
      if (r.link) setReveal({ who: m.email, r });
      else toast({ tone: "ok", title: `${m.status === "active" ? "Reset link" : "Invite"} emailed to ${m.email}`, description: "Works once, expires in 72 hours." });
    },
    onError: (e) => toast({ tone: "risk", title: "Could not send", description: e instanceof Error ? e.message : undefined }),
  });
  const change = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { role?: string; active?: boolean } }) => api<TeamMember>(`/team/${id}`, { method: "PATCH", json: body }),
    onSuccess: (m, { body }) => {
      refresh();
      toast({ tone: "ok", title: body.active === false ? `${m.email} can no longer log in` : `${m.email} is now ${body.role}` });
    },
    onError: (e) => toast({ tone: "risk", title: "Could not change", description: e instanceof Error ? e.message : undefined }),
  });

  if (me.data && me.data.role !== "admin") return <Callout tone="neutral" title="Only admins can manage the team" />;

  const columns: Column<TeamMember>[] = [
    {
      id: "person",
      header: "Person",
      sortValue: (m) => m.name || m.email,
      csv: (m) => m.email,
      cell: (m) => (
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={m.name || m.email} size="md" />
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 truncate font-medium text-ink">
              {m.name || m.email}
              {m.id === me.data?.id && <Badge>You</Badge>}
            </div>
            <div className="truncate text-xs text-faint">{m.email}</div>
          </div>
        </div>
      ),
    },
    {
      id: "role",
      header: "Role",
      sortValue: (m) => m.role,
      csv: (m) => m.role,
      cell: (m) =>
        m.id === me.data?.id ? (
          <span className="text-muted capitalize">{m.role}</span>
        ) : (
          <Select selectSize="sm" className="w-40" value={m.role} onChange={(e) => change.mutate({ id: m.id, body: { role: e.target.value } })} aria-label={`Role for ${m.email}`}>
            {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </Select>
        ),
    },
    {
      id: "status",
      header: "Status",
      sortValue: (m) => m.status,
      csv: (m) => m.status,
      cell: (m) => (
        <div>
          <Badge tone={statusTone[m.status]} dot>{statusText[m.status]}</Badge>
          {m.status === "invited" && m.invite_expires_at && <div className="mt-1 text-xs text-faint">Link expires {date(m.invite_expires_at)}</div>}
        </div>
      ),
    },
    {
      id: "links",
      header: "Linked",
      cell: (m) => (
        <div className="flex items-center gap-1.5">
          {m.crm_owner_id && <Badge>CRM owner</Badge>}
          {m.slack_linked && (
            <Tooltip content="Linked to a Slack user: their approvals from Slack count">
              <Badge icon={MessageSquare}>Slack</Badge>
            </Tooltip>
          )}
          {!m.crm_owner_id && !m.slack_linked && <span className="text-faint">—</span>}
        </div>
      ),
    },
    {
      id: "login",
      header: "Last login",
      firstSort: "desc",
      sortValue: (m) => m.last_login_at,
      csv: (m) => m.last_login_at,
      cell: (m) => <span className="whitespace-nowrap text-muted">{m.last_login_at ? ago(m.last_login_at) : <span className="text-faint">Never</span>}</span>,
    },
    {
      id: "actions",
      header: "",
      hideable: false,
      align: "right",
      cell: (m) =>
        m.id === me.data?.id ? null : (
          <Menu
            width={232}
            items={[
              { label: m.status === "active" ? "Email a reset link" : "Email the invite", icon: Mail, onSelect: () => invite.mutate({ m, send: true }) },
              { label: m.status === "active" ? "Create a reset link" : m.status === "invited" ? "Create a new link" : "Create an invite link", icon: KeyRound, onSelect: () => invite.mutate({ m, send: false }) },
              ...(m.status === "active" ? (["divider", { label: "Switch off login", icon: Power, tone: "risk", onSelect: () => change.mutate({ id: m.id, body: { active: false } }) }] as const) : []),
            ]}
            trigger={(p) => (
              <button {...p} aria-label={`Actions for ${m.email}`} className="grid size-8 place-items-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink">
                <EllipsisVertical className="size-4" />
              </button>
            )}
          />
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Team"
        description="Who can log in. Account owners synced from the CRM appear here without a login until you invite them."
        actions={<Button variant="primary" icon={UserPlus} onClick={() => setInviting(true)}>Invite people</Button>}
      />
      <DataTable
        id="team"
        rows={team.data}
        loading={team.isLoading}
        error={team.error}
        columns={columns}
        rowKey={(m) => m.id}
        defaultSort={{ id: "person", dir: "asc" }}
        search={{ placeholder: "Search people", text: (m) => `${m.name} ${m.email} ${m.role}` }}
        csvName="casefile-team"
        noun="people"
        defaultPageSize={25}
        empty={{ icon: UsersRound, title: "No one here yet", action: <Button variant="primary" icon={UserPlus} onClick={() => setInviting(true)}>Invite people</Button> }}
      />
      {inviting && <InviteModal onClose={() => setInviting(false)} />}
      {reveal?.r.link && (
        <Modal title="Share this link" description={`For ${reveal.who}`} icon={Link2} onClose={() => setReveal(null)} size="sm">
          <LinkReveal link={reveal.r.link} expires={reveal.r.expires_at} />
        </Modal>
      )}
    </>
  );
}
