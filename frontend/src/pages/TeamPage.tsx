import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useMe } from "../App";
import { api, type TeamMember } from "../api";
import { Badge, ErrorNote, Loading, PageHeader, Panel } from "../components/ui";
import { ago, date } from "../format";

const statusTone = { active: "ok", invited: "accent", no_login: "neutral" } as const;
const statusText = { active: "can log in", invited: "invited", no_login: "no login" } as const;

function InviteLink({ link, expires }: { link: string; expires: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="mt-2 rounded-md border border-accent bg-accent-soft p-2 text-xs">
      <div className="mb-1 font-medium text-accent">Invite link — shown once, works once, expires {date(expires)}</div>
      <div className="flex gap-2">
        <input readOnly value={link} className="min-w-0 flex-1 rounded border border-line bg-panel px-2 py-1 font-mono" onFocus={(e) => e.target.select()} />
        <button className="rounded-md border border-line bg-panel px-2" onClick={() => navigator.clipboard.writeText(link).then(() => setCopied(true))}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <p className="mt-1 text-muted">Send it to them yourself (chat or email). Sending a new invite cancels this one.</p>
    </div>
  );
}

// Admins decide who can log in: add people, send one-time invite links, change roles, switch logins off.
export default function TeamPage() {
  const me = useMe();
  const qc = useQueryClient();
  const team = useQuery({ queryKey: ["team"], queryFn: () => api<TeamMember[]>("/team"), enabled: me.data?.role === "admin" });
  const [links, setLinks] = useState<Record<string, { link: string; expires_at: string }>>({});
  const [form, setForm] = useState({ email: "", name: "", role: "owner" });
  const refresh = () => qc.invalidateQueries({ queryKey: ["team"] });
  const add = useMutation({
    mutationFn: () => api<TeamMember>("/team", { json: form }),
    onSuccess: () => { setForm({ email: "", name: "", role: "owner" }); refresh(); },
  });
  const [emailed, setEmailed] = useState<Record<string, boolean>>({});
  const invite = useMutation({
    mutationFn: ({ id, send }: { id: string; send: boolean }) =>
      api<{ link: string; expires_at: string; emailed: boolean }>(`/team/${id}/invite`, { method: "POST", json: { send_email: send } }),
    onSuccess: (r, { id }) => {
      if (r.emailed) setEmailed((e) => ({ ...e, [id]: true }));
      else setLinks((l) => ({ ...l, [id]: r }));
      refresh();
    },
  });
  const change = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { role?: string; active?: boolean } }) =>
      api<TeamMember>(`/team/${id}`, { method: "PATCH", json: body }),
    onSuccess: refresh,
  });
  if (me.data && me.data.role !== "admin") return <p className="text-sm text-muted">Only admins can manage the team.</p>;
  const submit = (e: FormEvent) => { e.preventDefault(); add.mutate(); };
  return (
    <>
      <PageHeader title="Team" subtitle="Who can log in to Casefile. Account owners synced from the CRM appear here; invite them to give them a login." />
      <div className="space-y-4">
        <Panel title="Add a person">
          <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row">
            <input required type="email" placeholder="work email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })}
              className="min-w-0 flex-1 rounded-md border border-line px-3 py-1.5 text-sm" />
            <input placeholder="name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="min-w-0 rounded-md border border-line px-3 py-1.5 text-sm sm:w-48" />
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} className="rounded-md border border-line px-2 py-1.5 text-sm">
              <option value="owner">account owner</option>
              <option value="leader">leader (read)</option>
              <option value="admin">admin</option>
            </select>
            <button disabled={add.isPending} className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60">Add</button>
          </form>
          {add.error && <div className="mt-2"><ErrorNote error={add.error} /></div>}
        </Panel>
        <Panel title={`People (${team.data?.length ?? 0})`}>
          {team.isLoading && <Loading />}
          {team.error && <ErrorNote error={team.error} />}
          {(invite.error || change.error) && <div className="mb-2"><ErrorNote error={invite.error ?? change.error} /></div>}
          <ul className="divide-y divide-line">
            {team.data?.map((m) => (
              <li key={m.id} className="py-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{m.name || m.email}</div>
                    <div className="truncate text-xs text-faint">
                      {m.email}
                      {m.crm_owner_id && " · CRM owner"}
                      {m.slack_linked && " · Slack linked"}
                      {m.last_login_at && ` · last login ${ago(m.last_login_at)}`}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge tone={statusTone[m.status]}>{statusText[m.status]}</Badge>
                    {m.id !== me.data?.id ? (
                      <>
                        <select value={m.role} onChange={(e) => change.mutate({ id: m.id, body: { role: e.target.value } })}
                          className="rounded-md border border-line px-1.5 py-0.5 text-xs" aria-label="Role">
                          <option value="owner">owner</option>
                          <option value="leader">leader</option>
                          <option value="admin">admin</option>
                        </select>
                        <button onClick={() => invite.mutate({ id: m.id, send: true })} className="rounded-md border border-line px-2 py-0.5 text-xs hover:border-accent">
                          {m.status === "active" ? "Email reset link" : "Email invite"}
                        </button>
                        <button onClick={() => invite.mutate({ id: m.id, send: false })} className="rounded-md border border-line px-2 py-0.5 text-xs hover:border-accent">
                          {m.status === "active" ? "Reset link" : m.status === "invited" ? "New link" : "Invite link"}
                        </button>
                        {m.status === "active" && (
                          <button onClick={() => change.mutate({ id: m.id, body: { active: false } })}
                            className="rounded-md border border-line px-2 py-0.5 text-xs text-risk hover:bg-risk-soft">
                            Switch off login
                          </button>
                        )}
                      </>
                    ) : (
                      <Badge>you · {m.role}</Badge>
                    )}
                  </div>
                </div>
                {emailed[m.id] && <p className="mt-2 text-xs text-ok">Invite emailed to {m.email} (works once, expires in 72 hours).</p>}
                {links[m.id] && <InviteLink link={links[m.id].link} expires={links[m.id].expires_at} />}
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </>
  );
}
