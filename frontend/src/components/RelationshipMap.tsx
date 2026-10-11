import type { RelationshipMap as Map, RelationshipPerson } from "../api";
import { date, label } from "../format";
import { Badge, Panel } from "./ui";

const roleTone = (role: string) => (role === "champion" ? "accent" : role === "economic_buyer" ? "ok" : "neutral");

function Heard({ p, windowDays }: { p: RelationshipPerson; windowDays: number }) {
  if (p.days_since_heard == null) return <span className="text-warn">never heard from</span>;
  if (p.days_since_heard > windowDays) return <span className="text-warn">not heard from in {windowDays}+ days</span>;
  return <span>heard from {p.days_since_heard === 0 ? "today" : `${p.days_since_heard}d ago`}</span>;
}

// Who at the customer we actually hear from. One active person is a single thread (a risk signal).
export default function RelationshipMap({ m }: { m: Map }) {
  const thin = m.active_people <= 1;
  return (
    <div id="people-panel" className="rounded-lg transition-shadow">
    <Panel
      title={`Relationship map (${m.people.length})`}
      actions={
        <Badge tone={thin ? "warn" : "ok"} title={`People who wrote to us or met us in the last ${m.active_days} days`}>
          {m.active_people} of {m.known_people} active · {m.active_days}d
        </Badge>
      }
    >
      <ul className="space-y-2 text-sm">
        {m.people.map((p) => (
          <li key={p.id} id={`person-${p.id}`} className="flex items-start justify-between gap-2 rounded-md">
            <div className="flex min-w-0 items-start gap-2">
              <span
                className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${p.active ? "bg-ok" : "bg-line"}`}
                title={p.active ? `Active in the last ${m.active_days} days` : "Not active recently"}
              />
              <div className="min-w-0">
                <div className="truncate">{p.name || p.email}</div>
                <div className="truncate text-xs text-faint">{p.title ?? p.email}</div>
                <div className="mt-0.5 text-xs text-muted">
                  <Heard p={p} windowDays={m.window_days} />
                  <span title={`Last ${m.window_days} days: ${p.emails_from} emails from them, ${p.emails_to} emails to them, ${p.meetings} meetings held`}>
                    {" · "}{p.emails_from} from · {p.emails_to} to · {p.meetings} mtg
                  </span>
                  {p.next_meeting && <> · <span className="text-accent">meeting {date(p.next_meeting)}</span></>}
                </div>
              </div>
            </div>
            <div className="flex shrink-0 gap-1">
              {p.status !== "active" && <Badge tone="risk">{p.status}</Badge>}
              {p.origin === "inferred" ? <Badge title="Seen in email/calendar, not in the CRM">not in CRM</Badge> : <Badge tone={roleTone(p.role)}>{label(p.role)}</Badge>}
            </div>
          </li>
        ))}
        {!m.people.length && <p className="text-faint">No contacts on record.</p>}
      </ul>
    </Panel>
    </div>
  );
}
