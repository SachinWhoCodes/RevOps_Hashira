import { useQuery } from "@tanstack/react-query";
import {
  ArrowDownLeft,
  ArrowUpRight,
  Briefcase,
  Calendar,
  ChartColumn,
  Clock3,
  Database,
  GitCommitHorizontal,
  Rewind,
  Mail,
  Phone,
  ShieldAlert,
  SquareCheck,
  StickyNote,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { api, type Timeline, type TimelineEvent } from "../api";
import AskPanel from "../components/AskPanel";
import AssessmentPanel from "../components/AssessmentPanel";
import { UsageChart } from "../components/charts";
import OutcomesPanel from "../components/OutcomesPanel";
import PlanPanel from "../components/PlanPanel";
import RelationshipMap from "../components/RelationshipMap";
import RiskLevers from "../components/RiskLevers";
import { AccountMark, Avatar, Badge, Button, Callout, Card, ErrorNote, Input, Menu, Segmented, Skeleton, Tooltip, type Tone } from "../components/ui";
import { ago, date, dateTime, label, money, renewalText } from "../format";
import { cn } from "../lib/cn";

type Filter = "all" | "email" | "meeting" | "note" | "crm_change";
const STEP = 25;

const kindIcon: Record<string, LucideIcon> = { email: Mail, meeting: Calendar, note: StickyNote, task: SquareCheck, call: Phone, crm_change: GitCommitHorizontal };

function EventItem({ e, asOf, last }: { e: TimelineEvent; asOf: Date; last: boolean }) {
  const Icon = kindIcon[e.kind] ?? Mail;
  const flagged = e.kind !== "crm_change" && e.injection_flag;
  return (
    <li id={e.kind !== "crm_change" ? `ev-${e.id}` : undefined} className="relative flex scroll-mt-24 gap-4 rounded-xl transition-shadow">
      {!last && <span className="absolute top-9 bottom-0 left-[15px] w-px bg-line" aria-hidden />}
      <span className={cn("relative z-10 mt-0.5 grid size-8 shrink-0 place-items-center rounded-full ring-4 ring-panel", flagged ? "bg-risk-soft text-risk-fg" : "bg-subtle text-muted ring-offset-0")}>
        <Icon className="size-4" aria-hidden />
      </span>
      <div className="min-w-0 flex-1 pb-6">
        {e.kind === "crm_change" ? (
          <>
            <div className="text-xs text-faint">{dateTime(e.at)} · CRM change</div>
            <ul className="mt-1.5 space-y-1">
              {e.changes.map((c, i) => (
                <li key={i} className="text-[13px] text-muted">
                  <span className="text-ink">{c.label}</span> · {label(c.field)} →{" "}
                  <span className="font-medium text-ink">{c.value === "" ? "(empty)" : String(c.value)}</span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <div className={cn("rounded-xl border p-3.5", flagged ? "border-risk/40 bg-risk-soft/40" : "border-line bg-panel")}>
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-faint">
              <span>{dateTime(e.at)}</span>
              {e.kind === "email" && (
                <span className="inline-flex items-center gap-0.5">
                  {e.direction === "outbound" ? <ArrowUpRight className="size-3" /> : <ArrowDownLeft className="size-3" />}
                  {e.direction === "outbound" ? "Sent" : "Received"}
                </span>
              )}
              {e.scheduled && <Badge tone="accent">Scheduled · {ago(e.at, asOf)}</Badge>}
              {e.cancelled && <Badge tone="warn">Cancelled</Badge>}
              {e.auto_reply && <Badge tone="warn">Auto-reply</Badge>}
              {e.bounce && <Badge tone="risk">Bounced</Badge>}
              {e.via === "sandbox_mirror" && (
                <Tooltip content="Read back from the sandbox HubSpot portal Casefile writes to">
                  <Badge tone="accent">From sandbox HubSpot</Badge>
                </Tooltip>
              )}
              {flagged && (
                <Tooltip content={e.injection_reason ?? "Instruction-like text aimed at an AI"}>
                  <Badge tone="risk" icon={ShieldAlert}>Instructions aimed at software</Badge>
                </Tooltip>
              )}
              {e.labels.map((l, i) => (
                <Tooltip key={i} content={`“${l.quote}” · confidence ${l.confidence}`}>
                  <Badge tone={l.status === "accepted" ? "accent" : l.status === "quarantined" ? "risk" : "neutral"}>
                    {label(l.label)}
                    {l.status !== "accepted" ? ` · ${l.status}` : ""}
                  </Badge>
                </Tooltip>
              ))}
              <Tooltip content="How this record was linked to the account" className="ml-auto">
                <span className="text-[11px] text-faint">
                  {label(e.match.method)}
                  {e.match.confidence < 1 ? ` · ${Math.round(e.match.confidence * 100)}%` : ""}
                </span>
              </Tooltip>
            </div>
            {e.subject && <div className="mt-1.5 text-[14px] font-medium text-ink">{e.subject}</div>}
            {e.snippet && <p className="mt-1 line-clamp-3 text-[13px] leading-relaxed text-muted">{e.snippet}</p>}
            <div className="mt-2 truncate text-xs text-faint">
              {e.from && <>From {e.from} · </>}
              {e.people.length ? e.people.join(", ") : e.participants.join(", ")}
            </div>
          </div>
        )}
      </div>
    </li>
  );
}

function TimelineCard({ t }: { t: Timeline }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [shown, setShown] = useState(STEP);
  const all = t.events ?? [];
  const count = (f: Filter) => all.filter((e) => f === "all" || e.kind === f).length;
  const events = all.filter((e) => filter === "all" || e.kind === filter);
  const asOf = new Date(t.as_of);
  return (
    <Card
      title="Timeline"
      description={`${(t.event_count ?? 0).toLocaleString("en-IN")} records, newest first`}
      icon={Clock3}
      actions={
        <Segmented<Filter>
          size="xs"
          label="Record type"
          value={filter}
          onChange={(v) => {
            setFilter(v);
            setShown(STEP);
          }}
          options={[
            { value: "all", label: "All", count: count("all") },
            { value: "email", label: "Email", count: count("email") },
            { value: "meeting", label: "Meetings", count: count("meeting") },
            { value: "note", label: "Notes", count: count("note") },
            { value: "crm_change", label: "CRM", count: count("crm_change") },
          ]}
        />
      }
    >
      {!events.length ? (
        <p className="py-6 text-center text-sm text-faint">Nothing here.</p>
      ) : (
        <>
          <ol key={filter} className="animate-fade-in">
            {events.slice(0, shown).map((e, i) => (
              <EventItem key={e.kind === "crm_change" ? `c${e.at}${i}` : e.id} e={e} asOf={asOf} last={i === Math.min(shown, events.length) - 1} />
            ))}
          </ol>
          <div className="flex items-center justify-between gap-3 border-t border-line pt-3 text-xs text-muted">
            <span className="tabular-nums">Showing {Math.min(shown, events.length)} of {events.length}</span>
            {shown < events.length && (
              <Button size="xs" onClick={() => setShown(shown + STEP)}>
                Show {Math.min(STEP, events.length - shown)} more
              </Button>
            )}
          </div>
        </>
      )}
    </Card>
  );
}

// Called from click handlers only (a date "now" must never be read during render).
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10);

function TimeMachine({ asOf, onChange }: { asOf: string; onChange: (v: string) => void }) {
  return (
    <div className="flex items-center gap-1.5">
      <Tooltip content="See this account exactly as it was known at the end of a past day">
        <Input type="date" inputSize="sm" icon={Rewind} value={asOf} onChange={(e) => onChange(e.target.value)} aria-label="View as of date" className="w-44" />
      </Tooltip>
      <Menu
        width={200}
        items={[
          { label: "30 days ago", onSelect: () => onChange(daysAgo(30)) },
          { label: "90 days ago", onSelect: () => onChange(daysAgo(90)) },
          { label: "6 months ago", onSelect: () => onChange(daysAgo(182)) },
          { label: "1 year ago", onSelect: () => onChange(daysAgo(365)) },
        ]}
        trigger={(p) => (
          <Button {...p} size="sm">
            Jump back
          </Button>
        )}
      />
    </div>
  );
}

export default function AccountPage() {
  const { id } = useParams();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const asOfParam = params.get("as_of") ?? "";
  const timeline = useQuery({
    queryKey: ["timeline", id, asOfParam],
    queryFn: () => api<Timeline>(`/accounts/${id}?limit=1000${asOfParam ? `&as_of=${asOfParam}` : ""}`),
  });
  const t = timeline.data;
  const loaded = !!t;

  // Links like /accounts/:id#plan land on the plan once the page has data to scroll to.
  useEffect(() => {
    if (!loaded || !location.hash) return;
    const el = document.getElementById(location.hash.slice(1));
    if (el) window.setTimeout(() => el.scrollIntoView({ behavior: "smooth", block: "start" }), 150);
  }, [loaded, location.hash]);

  const setAsOf = (v: string) => setParams(v ? { as_of: v } : {});
  const renewal = t?.deals?.find((d) => d.type === "renewal" && !d.closed);
  const openCases = (t?.cases ?? []).filter((c) => c.status === "open");
  const d = t?.account.days_to_renewal;
  const renewTone: Tone = d == null ? "neutral" : d <= 30 ? "risk" : d <= 90 ? "warn" : "neutral";

  return (
    <>
      <Link to="/accounts" className="group mb-4 inline-flex items-center gap-1.5 text-[13px] text-muted transition-colors hover:text-ink">
        <span className="transition-transform group-hover:-translate-x-0.5">←</span> Accounts
      </Link>

      <div className="mb-6 flex flex-wrap items-start justify-between gap-4 animate-fade-up">
        <div className="flex min-w-0 items-center gap-4">
          {t ? <AccountMark name={t.account.name} size="xl" className="rounded-xl" /> : <Skeleton className="size-12 rounded-xl" />}
          <div className="min-w-0">
            {t ? (
              <h1 className="truncate text-[24px] leading-tight font-semibold tracking-[-0.025em] sm:text-[26px]">{t.account.name}</h1>
            ) : (
              <Skeleton className="h-7 w-56" />
            )}
            {t?.exists && (
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[13px] text-muted">
                <span>{t.account.domains?.join(", ")}</span>
                <span className="inline-flex items-center gap-1.5">
                  <Avatar name={t.account.owner ?? "Unassigned"} size="xs" />
                  {t.account.owner ?? "Unassigned"}
                </span>
                <span className="font-medium text-ink">{money(t.account.arr)} ARR</span>
                <Badge tone={renewTone} dot>{renewalText(d)}</Badge>
                {!asOfParam &&
                  openCases.map((c) => (
                    <Badge key={c.id} tone={c.kind === "risk" ? "risk" : "ok"}>Open {c.kind} case</Badge>
                  ))}
              </div>
            )}
          </div>
        </div>
        <TimeMachine asOf={asOfParam} onChange={setAsOf} />
      </div>

      {asOfParam && (
        <div className="relative mb-5 overflow-hidden rounded-xl border border-accent/30 bg-accent-soft px-4 py-3 animate-fade-in">
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-r from-indigo-500/10 via-violet-500/10 to-cyan-400/10" aria-hidden />
          <div className="relative flex flex-wrap items-center gap-3">
            <Rewind className="size-5 text-accent-fg" aria-hidden />
            <div className="min-w-0 flex-1 text-[13.5px]">
              <span className="font-semibold text-ink">Time machine: {date(asOfParam)}.</span>{" "}
              <span className="text-muted">You're seeing this account exactly as it was known at the end of that day. Anything recorded later is hidden.</span>
            </div>
            <Button size="sm" onClick={() => setAsOf("")}>Back to today</Button>
          </div>
        </div>
      )}

      {timeline.isLoading && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="space-y-5">
            <Skeleton className="h-72 w-full rounded-xl" />
            <Skeleton className="h-52 w-full rounded-xl" />
          </div>
          <div className="space-y-5">
            <Skeleton className="h-40 w-full rounded-xl" />
            <Skeleton className="h-64 w-full rounded-xl" />
          </div>
        </div>
      )}
      {timeline.error && <ErrorNote error={timeline.error} />}
      {t && !t.exists && <Callout tone="neutral" title="Nothing yet on this date">This account did not exist in Casefile's data on {date(asOfParam)}.</Callout>}

      {t?.exists && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-w-0 space-y-5">
            {(t.flagged_emails?.length ?? 0) > 0 && (
              <Callout tone="risk" icon={ShieldAlert} title={`${t.flagged_emails!.length} email${t.flagged_emails!.length > 1 ? "s" : ""} contained instructions aimed at software`}>
                They were not followed, and nothing they said counts towards the score.
                <ul className="mt-2 space-y-1">
                  {t.flagged_emails!.map((f) => (
                    <li key={f.id} className="text-[12.5px]">
                      <span className="text-ink">{date(f.at)} · {f.subject}</span> — <span className="italic">“{f.quote ?? f.reason}”</span>
                    </li>
                  ))}
                </ul>
              </Callout>
            )}
            {t.assessment && <AssessmentPanel a={t.assessment} cases={t.cases} explanation={t.explanation} asOfParam={asOfParam} accountId={t.account.id} />}
            {!asOfParam && openCases.map((c) => <PlanPanel key={c.id} caseId={c.id} accountId={t.account.id} />)}
            {t.assessment?.levers && (t.assessment.risk.score > 0 || (!asOfParam && openCases.length > 0)) && <RiskLevers v={t.assessment.levers} />}
            {!asOfParam && <OutcomesPanel outcomes={t.outcomes ?? []} />}
            {!asOfParam && <AskPanel key={t.account.id} accountId={t.account.id} />}
            <TimelineCard t={t} />
          </div>

          <aside className="min-w-0 space-y-5">
            <Card id="deal-panel" title="Renewal deal" icon={Briefcase}>
              {renewal ? (
                <dl className="space-y-3 text-[13px]">
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-muted">Stage</dt>
                    <dd className="font-medium text-ink capitalize">{label(renewal.stage ?? "—")}</dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-muted">Amount</dt>
                    <dd className="font-medium tabular-nums">{money(renewal.amount)}</dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-muted">Close date</dt>
                    <dd>{date(renewal.close_date)}</dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-muted">Seats</dt>
                    <dd className="tabular-nums">{t.account.seats_purchased ?? "—"}</dd>
                  </div>
                  <div className="border-t border-line pt-3">
                    <dt className="text-muted">Next step</dt>
                    <dd className="mt-1">
                      {renewal.next_step ? <span className="text-ink">{renewal.next_step}</span> : <Badge tone="warn" dot>None recorded</Badge>}
                      {renewal.next_step_since && <div className="mt-1 text-xs text-faint">Since {date(renewal.next_step_since)}</div>}
                    </dd>
                  </div>
                </dl>
              ) : (
                <p className="text-sm text-faint">No open renewal deal.</p>
              )}
            </Card>
            {t.relationships && <RelationshipMap m={t.relationships} />}
            <Card id="usage-panel" title="Product usage" icon={ChartColumn}>
              <UsageChart weeks={t.usage_weekly ?? []} seats={t.account.seats_purchased} />
            </Card>
            <Card title="Data sources" icon={Database} description="Where this account's picture comes from">
              <ul className="space-y-2">
                {["crm", "hubspot", "gmail", "gcal", "usage"].map((s) => {
                  const ok = t.sources?.includes(s);
                  return (
                    <li key={s} className="flex items-center justify-between text-[13px]">
                      <span className="text-ink">{{ crm: "CRM", hubspot: "HubSpot", gmail: "Gmail", gcal: "Google Calendar", usage: "Product usage" }[s]}</span>
                      <Badge tone={ok ? "ok" : "warn"} dot>{ok ? "Connected" : "Missing"}</Badge>
                    </li>
                  );
                })}
              </ul>
            </Card>
          </aside>
        </div>
      )}
    </>
  );
}
