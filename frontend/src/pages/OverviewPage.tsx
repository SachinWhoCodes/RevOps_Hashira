import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CalendarClock, ChevronRight, HeartPulse, Inbox, Plug, ScanEye, ShieldAlert, Sparkles, Target, TrendingUp, Zap } from "lucide-react";
import { Link, Navigate } from "react-router-dom";
import { api, type AccountRow, type BlindSpots, type ConnectionRow, type ExecutorStatus, type InboxRow, type Queue } from "../api";
import { BarList } from "../components/charts";
import { SignalChip, SignalIcon } from "../components/domain";
import { AccountMark, Avatar, ButtonLink, Card, EmptyState, ScoreRing, Skeleton, StatTile, StatusDot, type Tone } from "../components/ui";
import { ago, date, greeting, label, longDate, money, plural, renewalText } from "../format";
import { cn } from "../lib/cn";
import { firstName, useMe } from "../lib/session";
import { nameFor } from "../signals";

function RowSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-4">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="size-10 rounded-full" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-3.5 w-1/3" />
            <Skeleton className="h-3 w-2/3" />
          </div>
        </div>
      ))}
    </div>
  );
}

function HealthRow({ icon: Icon, label: text, tone, value, to }: { icon: typeof Plug; label: string; tone: Tone; value: string; to: string }) {
  return (
    <Link to={to} className="group -mx-2 flex items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-hover">
      <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-subtle text-muted ring-1 ring-line">
        <Icon className="size-4" aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[13px] font-medium text-ink">{text}</span>
        <span className="block truncate text-xs text-muted">{value}</span>
      </span>
      <StatusDot tone={tone} />
      <ChevronRight className="size-4 text-faint opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
    </Link>
  );
}

export default function OverviewPage() {
  const me = useMe();
  const live = me.data && me.data.org_mode !== "setup";
  const risk = useQuery({ queryKey: ["queue", "risk", "all"], queryFn: () => api<Queue>("/queue?kind=risk&owner=all") });
  const exp = useQuery({ queryKey: ["queue", "expansion", "all"], queryFn: () => api<Queue>("/queue?kind=expansion&owner=all") });
  const inbox = useQuery({ queryKey: ["inbox"], queryFn: () => api<InboxRow[]>("/inbox"), enabled: !!live });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api<AccountRow[]>("/accounts") });
  const blind = useQuery({ queryKey: ["blind-spots"], queryFn: () => api<BlindSpots>("/blind-spots") });
  const conns = useQuery({ queryKey: ["connections"], queryFn: () => api<ConnectionRow[]>("/connections") });
  const executor = useQuery({ queryKey: ["executor"], queryFn: () => api<ExecutorStatus>("/executor/status") });

  // A company still in setup has nothing to show here yet: its admin starts on the checklist.
  if (me.data?.role === "admin" && me.data.org_mode === "setup") return <Navigate to="/setup" replace />;

  const r = risk.data;
  const riskItems = r?.items ?? [];
  const atRisk = new Set(riskItems.map((i) => i.account.id));
  const waiting = (inbox.data ?? []).filter((x) => (x.counts.proposed ?? 0) > 0);
  const waitingLines = waiting.reduce((n, x) => n + (x.counts.proposed ?? 0), 0);
  const upcoming = (accounts.data ?? [])
    .filter((a) => a.status === "active" && a.days_to_renewal != null && a.days_to_renewal >= 0 && a.days_to_renewal <= 90)
    .sort((a, b) => (a.days_to_renewal ?? 0) - (b.days_to_renewal ?? 0));
  const upcomingArr = upcoming.reduce((n, a) => n + (a.arr ?? 0), 0);

  // What drives risk across the book: in how many open risk cases each rule fires.
  const drivers = new Map<string, number>();
  for (const it of riskItems) {
    for (const t of new Set(it.signals.filter((s) => s.polarity === "risk").map((s) => s.type))) drivers.set(t, (drivers.get(t) ?? 0) + 1);
  }
  const byOwner = new Map<string, number>();
  for (const it of riskItems) byOwner.set(it.owner ?? "Unassigned", (byOwner.get(it.owner ?? "Unassigned") ?? 0) + (it.arr ?? 0));

  const fresh = (conns.data ?? []).filter((c) => c.status !== "disabled");
  const stale = fresh.filter((c) => c.status === "error" || c.freshness.state !== "fresh");
  const gaps = blind.data ? blind.data.unmatched_records + blind.data.ambiguous_records : 0;
  const ex = executor.data;

  const summary = r
    ? `${plural(riskItems.length, "open risk case")} put ${money(r.revenue_at_stake)} of ARR at stake${live ? ` · ${plural(waitingLines, "action")} waiting for a decision` : ""}.`
    : "Pulling together what needs you today…";

  return (
    <>
      <div className="mb-7 flex flex-wrap items-end justify-between gap-4 animate-fade-up">
        <div>
          <div className="text-[13px] text-muted">{longDate(new Date())}</div>
          <h1 className="mt-1 text-[26px] leading-tight font-semibold tracking-[-0.03em] sm:text-[30px]">
            {greeting()}
            {me.data ? `, ${firstName(me.data)}` : ""}
          </h1>
          <p className="mt-1.5 text-[14.5px] text-muted">{summary}</p>
        </div>
        <div className="flex gap-2">
          {live && <ButtonLink to="/approvals" icon={Inbox}>Approvals</ButtonLink>}
          <ButtonLink to="/queue" variant="primary" iconRight={ArrowRight}>Open the queue</ButtonLink>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Revenue at risk" icon={ShieldAlert} tone="risk" value={r ? r.revenue_at_stake : null} format={money} to="/queue" hint={r ? `ARR of ${plural(riskItems.length, "account")} with an open risk case` : undefined} />
        <StatTile label="Expansion in play" icon={TrendingUp} tone="ok" value={exp.data ? exp.data.revenue_at_stake : null} format={money} to="/queue?kind=expansion" delay={60} hint={exp.data ? `${plural(exp.data.items.length, "expansion case")} open` : undefined} />
        <StatTile
          label="Awaiting approval"
          icon={Inbox}
          tone="accent"
          value={live ? (inbox.data ? waitingLines : null) : 0}
          to="/approvals"
          delay={120}
          hint={live ? (inbox.data ? `${plural(waiting.length, "change set")} need a decision` : undefined) : "Starts in shadow mode"}
        />
        <StatTile
          label="Renewing in 90 days"
          icon={CalendarClock}
          tone="warn"
          value={accounts.data ? upcoming.length : null}
          to="/accounts?renewal=90"
          delay={180}
          hint={accounts.data ? `${money(upcomingArr)} ARR · ${upcoming.filter((a) => atRisk.has(a.id)).length} at risk` : undefined}
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card
          className="lg:col-span-2 animate-fade-up [animation-delay:120ms]"
          title="Needs attention"
          description="Highest priority first: risk × ARR × how soon it renews"
          icon={Target}
          actions={<ButtonLink to="/queue" size="xs" variant="ghost" iconRight={ArrowRight}>All {riskItems.length || ""}</ButtonLink>}
          flush
        >
          {risk.isLoading ? (
            <div className="p-5"><RowSkeleton /></div>
          ) : !riskItems.length ? (
            <EmptyState compact icon={Sparkles} title="No open risk cases" description="Nothing in the book is over the case threshold right now." />
          ) : (
            <ul className="divide-y divide-line">
              {riskItems.slice(0, 6).map((it, i) => {
                const shown = it.signals.filter((s) => s.polarity === "risk").slice(0, 3);
                return (
                  <li key={it.case_id} className="animate-fade-up" style={{ animationDelay: `${160 + i * 40}ms` }}>
                    <Link to={`/accounts/${it.account.id}`} className="group flex items-center gap-4 px-5 py-3.5 transition-colors hover:bg-hover/60">
                      <ScoreRing value={it.risk} size={42} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate font-semibold text-ink group-hover:text-accent-fg">{it.account.name}</span>
                          {it.confidence === "low" && <span className="text-[11px] text-warn-fg">low confidence</span>}
                        </div>
                        <p className="mt-0.5 truncate text-[13px] text-muted">{it.headline ?? shown.map((s) => nameFor(s.type)).join(" · ")}</p>
                        <div className="mt-1.5 hidden flex-wrap gap-1.5 sm:flex">
                          {shown.map((s, j) => <SignalChip key={j} s={s} />)}
                        </div>
                      </div>
                      <div className="hidden shrink-0 text-right sm:block">
                        <div className="text-[13.5px] font-semibold tabular-nums text-ink">{money(it.arr)}</div>
                        <div className={cn("mt-0.5 text-xs", it.days_to_renewal != null && it.days_to_renewal <= 30 ? "font-medium text-risk-fg" : "text-muted")}>
                          {it.days_to_renewal == null ? "renewal unknown" : it.days_to_renewal < 0 ? "overdue" : `${it.days_to_renewal}d to renewal`}
                        </div>
                      </div>
                      <ChevronRight className="size-4 shrink-0 text-faint transition-transform group-hover:translate-x-0.5" aria-hidden />
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <div className="space-y-4">
          <Card
            className="animate-fade-up [animation-delay:180ms]"
            title="Waiting for you"
            icon={Inbox}
            actions={live && <ButtonLink to="/approvals" size="xs" variant="ghost" iconRight={ArrowRight}>Inbox</ButtonLink>}
            bodyClassName="px-5 py-3"
          >
            {!live ? (
              <p className="py-3 text-[13px] text-muted">Proposed actions start once the company is in shadow mode.</p>
            ) : inbox.isLoading ? (
              <RowSkeleton rows={3} />
            ) : !waiting.length ? (
              <p className="py-3 text-[13px] text-muted">Nothing is waiting for a decision.</p>
            ) : (
              <ul className="-mx-2">
                {waiting.slice(0, 5).map((w) => (
                  <li key={w.id}>
                    <Link to={`/accounts/${w.account.id}#plan`} className="flex items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-hover">
                      <AccountMark name={w.account.name} size="md" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] font-medium text-ink">{w.account.name}</span>
                        <span className="block truncate text-xs text-muted capitalize">{label(w.play)}</span>
                      </span>
                      <span className="rounded-md bg-accent-soft px-1.5 py-0.5 text-[11.5px] font-medium text-accent-fg tabular-nums">{w.counts.proposed} to decide</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card className="animate-fade-up [animation-delay:240ms]" title="System health" icon={HeartPulse} bodyClassName="px-5 py-2">
            <HealthRow
              icon={Plug}
              label="Data sources"
              to="/connections"
              tone={!fresh.length ? "neutral" : stale.length ? "warn" : "ok"}
              value={!conns.data ? "Checking…" : !fresh.length ? "Nothing connected yet" : stale.length ? `${stale.length} of ${fresh.length} not fresh: ${stale.map((c) => c.kind).join(", ")}` : `All ${fresh.length} fresh`}
            />
            <HealthRow
              icon={Zap}
              label="Executor"
              to="/approvals"
              tone={!ex ? "neutral" : !ex.targets.length ? "neutral" : ex.running ? "ok" : "warn"}
              value={!ex ? "Checking…" : !ex.targets.length ? "No write tools connected" : ex.running ? `Online · heartbeat ${ago(ex.last_heartbeat)}` : `Offline · last seen ${ago(ex.last_heartbeat)}`}
            />
            <HealthRow
              icon={ScanEye}
              label="Blind spots"
              to="/blind-spots"
              tone={!blind.data ? "neutral" : gaps > 0 ? "warn" : "ok"}
              value={!blind.data ? "Checking…" : `${plural(gaps, "unmatched record")} · ${plural(blind.data.unmatched_usage_keys, "usage key")}`}
            />
          </Card>
        </div>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="animate-fade-up [animation-delay:200ms]" title="What's driving risk" description="Open risk cases where each rule fires" icon={Zap}>
          {risk.isLoading ? (
            <RowSkeleton rows={4} />
          ) : (
            <BarList
              tone="risk"
              items={[...drivers.entries()]
                .sort((a, b) => b[1] - a[1])
                .slice(0, 6)
                .map(([t, n]) => ({ key: t, label: nameFor(t), value: n, icon: <SignalIcon type={t} className="size-3.5 text-muted" /> }))}
              format={(n) => plural(n, "case")}
              empty="No risk rules are firing on open cases."
            />
          )}
        </Card>
        <Card className="animate-fade-up [animation-delay:260ms]" title="Risk by owner" description="ARR in open risk cases" icon={ShieldAlert}>
          {risk.isLoading ? (
            <RowSkeleton rows={4} />
          ) : (
            <BarList
              tone="accent"
              items={[...byOwner.entries()]
                .sort((a, b) => b[1] - a[1])
                .slice(0, 6)
                .map(([o, v]) => ({ key: o, label: o, value: v, icon: <Avatar name={o} size="xs" /> }))}
              format={money}
              empty="No ARR is at risk."
            />
          )}
        </Card>
        <Card
          className="animate-fade-up [animation-delay:320ms]"
          title="Renewals ahead"
          description="Next 90 days, soonest first"
          icon={CalendarClock}
          actions={<ButtonLink to="/accounts?renewal=90" size="xs" variant="ghost" iconRight={ArrowRight}>All</ButtonLink>}
          bodyClassName="px-5 py-3"
        >
          {accounts.isLoading ? (
            <RowSkeleton rows={4} />
          ) : !upcoming.length ? (
            <p className="py-3 text-[13px] text-muted">No renewals in the next 90 days.</p>
          ) : (
            <ul className="-mx-2">
              {upcoming.slice(0, 6).map((a) => (
                <li key={a.id}>
                  <Link to={`/accounts/${a.id}`} className="flex items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-hover" title={renewalText(a.days_to_renewal)}>
                    <AccountMark name={a.name} size="md" />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
                        <span className="truncate">{a.name}</span>
                        {atRisk.has(a.id) && <StatusDot tone="risk" />}
                      </span>
                      <span className="block truncate text-xs text-muted">{date(a.renewal_date)} · {money(a.arr)}</span>
                    </span>
                    <span className={cn("text-xs tabular-nums", (a.days_to_renewal ?? 99) <= 30 ? "font-medium text-warn-fg" : "text-muted")}>{a.days_to_renewal}d</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
