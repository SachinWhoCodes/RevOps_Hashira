import { CalendarDays, Users } from "lucide-react";
import type { RelationshipMap as Map, RelationshipPerson } from "../api";
import { dateShort, label } from "../format";
import { cn } from "../lib/cn";
import { Avatar, Badge, Card, Tooltip } from "./ui";

const roleTone = (role: string) => (role === "champion" ? "accent" : role === "economic_buyer" ? "ok" : "neutral");

function heard(p: RelationshipPerson, windowDays: number) {
  if (p.days_since_heard == null) return <span className="text-warn-fg">Never heard from</span>;
  if (p.days_since_heard > windowDays) return <span className="text-warn-fg">Quiet {windowDays}+ days</span>;
  return <span>Heard from {p.days_since_heard === 0 ? "today" : `${p.days_since_heard}d ago`}</span>;
}

// Who at the customer we actually hear from. One active person is a single thread (a risk signal).
export default function RelationshipMap({ m }: { m: Map }) {
  const thin = m.active_people <= 1;
  return (
    <Card
      id="people-panel"
      title="People"
      icon={Users}
      bodyClassName="p-2"
      actions={
        <Tooltip content={`People who wrote to us or met us in the last ${m.active_days} days`}>
          <Badge tone={thin ? "warn" : "ok"} dot>
            {m.active_people} of {m.known_people} active
          </Badge>
        </Tooltip>
      }
    >
      {!m.people.length && <p className="px-3 py-6 text-center text-sm text-faint">No contacts on record.</p>}
      <ul>
        {m.people.map((p) => (
          <li key={p.id} id={`person-${p.id}`} className="scroll-mt-24 rounded-lg px-3 py-2.5 transition-colors hover:bg-hover/60">
            <div className="flex items-start gap-3">
              <span className="relative">
                <Avatar name={p.name || p.email} size="md" />
                <span
                  className={cn("absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full ring-2 ring-panel", p.active ? "bg-ok" : "bg-line-strong")}
                  title={p.active ? `Active in the last ${m.active_days} days` : "Not active recently"}
                />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[13.5px] font-medium text-ink">{p.name || p.email}</span>
                  {p.status !== "active" ? (
                    <Badge tone="risk">{p.status}</Badge>
                  ) : p.origin === "inferred" ? (
                    <Tooltip content="Seen in email or the calendar, not in the CRM">
                      <Badge>Not in CRM</Badge>
                    </Tooltip>
                  ) : (
                    <Badge tone={roleTone(p.role)}>{label(p.role)}</Badge>
                  )}
                </div>
                <div className="truncate text-xs text-faint">{p.title ?? p.email}</div>
                <div className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-muted">
                  {heard(p, m.window_days)}
                  <Tooltip content={`Last ${m.window_days} days: ${p.emails_from} emails from them, ${p.emails_to} to them, ${p.meetings} meetings held`}>
                    <span className="cursor-help tabular-nums text-faint">
                      {p.emails_from}↓ {p.emails_to}↑ · {p.meetings} mtg
                    </span>
                  </Tooltip>
                  {p.next_meeting && (
                    <span className="inline-flex items-center gap-1 text-accent-fg">
                      <CalendarDays className="size-3" /> {dateShort(p.next_meeting)}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}
