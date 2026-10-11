import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronsUpDown, LogOut, Menu as MenuIcon, Monitor, Moon, PanelLeftClose, PanelLeftOpen, Search, Sun, TriangleAlert, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, type ExecutorStatus, type InboxRow, type OnboardingStatus, type OrgMode, type Queue, type User } from "../../api";
import { cn } from "../../lib/cn";
import { useLocalPref } from "../../lib/hooks";
import { useMe } from "../../lib/session";
import { useTheme } from "../../lib/theme";
import { ago } from "../../format";
import { LogoMark } from "../brand";
import { EvidenceDrawerProvider } from "../EvidenceDrawer";
import { Avatar, Kbd, Menu, StatusDot, Tooltip, type Tone } from "../ui";
import { CommandPalette } from "./CommandPalette";
import { GROUPS, NAV, navFor, type NavItem } from "./nav";

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent);
const modeTone: Record<OrgMode, Tone> = { setup: "warn", shadow: "accent", live: "ok" };
const modeLabel: Record<OrgMode, string> = { setup: "Setup", shadow: "Shadow", live: "Live" };

// Counts shown beside nav items. They share cache keys with the pages, so nothing is fetched twice.
function useNavBadges(me: User | undefined) {
  const live = me && me.org_mode !== "setup";
  const queue = useQuery({ queryKey: ["queue", "risk", "all"], queryFn: () => api<Queue>("/queue?kind=risk&owner=all"), enabled: !!me, staleTime: 60_000 });
  const inbox = useQuery({ queryKey: ["inbox"], queryFn: () => api<InboxRow[]>("/inbox"), enabled: !!live, refetchInterval: 30_000 });
  const setup = useQuery({ queryKey: ["onboarding"], queryFn: () => api<OnboardingStatus>("/onboarding"), enabled: me?.role === "admin" && me.org_mode !== "live", staleTime: 60_000 });
  const waiting = (inbox.data ?? []).reduce((n, r) => n + (r.counts.proposed ?? 0), 0);
  const done = setup.data?.steps.filter((s) => s.state === "done" || s.state === "warn").length;
  return {
    queue: queue.data?.items.length || undefined,
    approvals: waiting || undefined,
    setup: setup.data && done !== undefined ? `${done}/${setup.data.steps.length}` : undefined,
  } as Record<NonNullable<NavItem["badge"]>, number | string | undefined>;
}

function NavList({ me, collapsed, onNavigate }: { me: User | undefined; collapsed: boolean; onNavigate?: () => void }) {
  const badges = useNavBadges(me);
  const admin = me?.role === "admin";
  return (
    <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-3" aria-label="Main">
      {GROUPS.map((g) => {
        const items = NAV.filter((n) => n.group === g && (!n.admin || admin));
        if (!items.length) return null;
        return (
          <div key={g}>
            {collapsed ? <div className="mx-2 mb-2 h-px bg-line" /> : <div className="mb-1.5 px-2.5 text-[11px] font-semibold tracking-[0.06em] text-faint uppercase">{g}</div>}
            <ul className="space-y-0.5">
              {items.map((n) => {
                const badge = n.badge ? badges[n.badge] : undefined;
                const link = (
                  <NavLink
                    to={n.to}
                    end={n.to === "/"}
                    onClick={onNavigate}
                    className={({ isActive }) =>
                      cn(
                        "group relative flex h-8 items-center gap-2.5 rounded-lg text-[13.5px] transition-colors",
                        collapsed ? "w-10 justify-center" : "px-2.5",
                        isActive ? "bg-hover font-medium text-ink" : "text-muted hover:bg-hover/70 hover:text-ink",
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <span className={cn("absolute top-1.5 bottom-1.5 left-0 w-[3px] rounded-r-full bg-accent transition-opacity", isActive ? "opacity-100" : "opacity-0")} aria-hidden />
                        <n.icon aria-hidden className={cn("size-4 shrink-0 transition-colors", isActive ? "text-accent-fg" : "text-faint group-hover:text-muted")} />
                        {!collapsed && <span className="flex-1 truncate">{n.label}</span>}
                        {badge !== undefined &&
                          (collapsed ? (
                            <span className="absolute top-1 right-1 size-1.5 rounded-full bg-accent" aria-hidden />
                          ) : (
                            <span
                              className={cn(
                                "rounded-md px-1.5 py-px text-[11px] font-medium tabular-nums",
                                n.badge === "approvals" ? "bg-accent text-white" : "bg-hover text-muted ring-1 ring-line",
                              )}
                            >
                              {badge}
                            </span>
                          ))}
                      </>
                    )}
                  </NavLink>
                );
                return <li key={n.to}>{collapsed ? <Tooltip content={n.label} className="flex">{link}</Tooltip> : link}</li>;
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}

function UserMenu({ me, collapsed, onSignOut }: { me: User; collapsed: boolean; onSignOut: () => void }) {
  const { choice, setChoice } = useTheme();
  const themeItem = (c: typeof choice, label: string, Icon: typeof Sun) => ({
    label,
    icon: Icon,
    onSelect: () => setChoice(c),
    hint: choice === c ? <Check className="size-3.5 text-accent-fg" /> : undefined,
  });
  return (
    <Menu
      align="start"
      width={248}
      header={
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-ink">{me.name || me.email}</div>
          <div className="truncate text-xs text-muted">{me.email}</div>
          <div className="mt-1 text-xs text-faint capitalize">{me.role} · {me.org_name}</div>
        </div>
      }
      items={[themeItem("light", "Light", Sun), themeItem("dark", "Dark", Moon), themeItem("system", "Match system", Monitor), "divider", { label: "Sign out", icon: LogOut, onSelect: onSignOut }]}
      trigger={(p) => (
        <button
          {...p}
          className={cn("flex w-full items-center gap-2.5 rounded-lg p-1.5 text-left transition-colors hover:bg-hover", collapsed && "justify-center")}
          aria-label="Account menu"
        >
          <Avatar name={me.name || me.email} size="md" />
          {!collapsed && (
            <>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium text-ink">{me.name || me.email}</span>
                <span className="block truncate text-xs text-faint capitalize">{me.role}</span>
              </span>
              <ChevronsUpDown className="size-4 text-faint" aria-hidden />
            </>
          )}
        </button>
      )}
    />
  );
}

function SidebarBody({ me, collapsed, onCollapse, onNavigate, onSignOut, onClose }: {
  me: User | undefined;
  collapsed: boolean;
  onCollapse?: () => void;
  onNavigate?: () => void;
  onSignOut: () => void;
  onClose?: () => void;
}) {
  const mode = me?.org_mode;
  return (
    <>
      <div className={cn("flex h-14 shrink-0 items-center gap-2 px-4", collapsed && "justify-center px-0")}>
        <Link to="/" onClick={onNavigate} className="flex min-w-0 items-center gap-2.5 rounded-lg">
          <LogoMark size={26} />
          {!collapsed && <span className="text-[15px] font-semibold tracking-[-0.02em]">Casefile</span>}
        </Link>
        {!collapsed && onCollapse && (
          <button onClick={onCollapse} aria-label="Collapse sidebar" title="Collapse sidebar" className="ml-auto rounded-md p-1 text-faint transition-colors hover:bg-hover hover:text-ink">
            <PanelLeftClose className="size-4" />
          </button>
        )}
        {onClose && (
          <button onClick={onClose} aria-label="Close menu" className="ml-auto rounded-md p-1 text-faint hover:bg-hover hover:text-ink">
            <X className="size-4" />
          </button>
        )}
      </div>
      {me && !collapsed && (
        <div className="mx-3 mb-1 flex items-center gap-2.5 rounded-lg border border-line bg-subtle px-2.5 py-2">
          <span className="grid size-6 place-items-center rounded-md bg-ink text-[11px] font-semibold text-canvas">{me.org_name.trim()[0]?.toUpperCase()}</span>
          <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{me.org_name}</span>
          {mode && (
            <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-muted">
              <StatusDot tone={modeTone[mode]} pulse={mode === "live"} />
              {modeLabel[mode]}
            </span>
          )}
        </div>
      )}
      <NavList me={me} collapsed={collapsed} onNavigate={onNavigate} />
      {collapsed && onCollapse && (
        <div className="flex justify-center pb-1">
          <button onClick={onCollapse} aria-label="Expand sidebar" title="Expand sidebar" className="rounded-md p-1.5 text-faint transition-colors hover:bg-hover hover:text-ink">
            <PanelLeftOpen className="size-4" />
          </button>
        </div>
      )}
      {me && (
        <div className="border-t border-line p-2">
          <UserMenu me={me} collapsed={collapsed} onSignOut={onSignOut} />
        </div>
      )}
    </>
  );
}

function ExecutorChip() {
  const s = useQuery({ queryKey: ["executor"], queryFn: () => api<ExecutorStatus>("/executor/status"), refetchInterval: 15_000 });
  if (!s.data) return null;
  const d = s.data;
  const tone: Tone = !d.targets.length ? "neutral" : d.running ? (d.shadow ? "accent" : "ok") : "warn";
  const text = !d.targets.length ? "No write tools" : d.running ? (d.shadow ? "Executor · shadow" : "Executor online") : "Executor offline";
  const detail = !d.targets.length
    ? "No write tools are connected, so approved lines cannot be written yet."
    : `${d.running ? "Running" : "Not running"} · last heartbeat ${d.last_heartbeat ? ago(d.last_heartbeat) : "never"} · ${d.targets.length} write target${d.targets.length === 1 ? "" : "s"}. It is the only process that holds write credentials.`;
  return (
    <Tooltip content={detail}>
      <span className="hidden h-8 items-center gap-2 rounded-lg border border-line bg-panel px-2.5 text-[12.5px] text-muted shadow-xs md:inline-flex">
        <StatusDot tone={tone} pulse={d.running && !d.shadow} />
        {text}
      </span>
    </Tooltip>
  );
}

function ModeBanner({ mode, admin }: { mode: OrgMode; admin: boolean }) {
  const setup = mode === "setup";
  return (
    <div className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2 text-[13px] sm:px-6 lg:px-8", setup ? "border-warn/25 bg-warn-soft" : "border-accent/20 bg-accent-soft")}>
      <TriangleAlert aria-hidden className={cn("size-4 shrink-0", setup ? "text-warn-fg" : "text-accent-fg")} />
      <span className="text-ink">
        <span className="font-semibold">{setup ? "Setup mode." : "Shadow mode."}</span>{" "}
        <span className="text-muted">
          {setup
            ? "Casefile is reading and scoring your data. No AI runs and nothing is written to your tools."
            : "Approvals are recorded as “would execute”. Nothing is written to your tools."}
        </span>
      </span>
      {admin && (
        <Link to="/setup" className={cn("ml-auto font-medium hover:underline", setup ? "text-warn-fg" : "text-accent-fg")}>
          Setup checklist →
        </Link>
      )}
    </div>
  );
}

export default function AppShell() {
  const me = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const { dark, setChoice } = useTheme();
  const [collapsed, setCollapsed] = useLocalPref("casefile.sidebar.collapsed", false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [palette, setPalette] = useState(false);
  const logout = useMutation({
    mutationFn: () => api("/auth/logout", { method: "POST", json: {} }),
    onSuccess: () => {
      qc.clear();
      navigate("/login");
    },
  });
  const signOut = useCallback(() => logout.mutate(), [logout]);
  const closePalette = useCallback(() => setPalette(false), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement | null)?.closest("input, textarea, select, [contenteditable=true]");
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      } else if (e.key === "/" && !typing) {
        e.preventDefault();
        setPalette(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (me.isLoading) {
    return (
      <div className="grid min-h-dvh place-items-center">
        <div className="flex flex-col items-center gap-4 animate-fade-in">
          <LogoMark size={40} className="animate-pulse" />
          <span className="text-sm text-faint">Loading Casefile…</span>
        </div>
      </div>
    );
  }
  if (me.error instanceof ApiError && me.error.status === 401) return <Navigate to="/login" replace />;
  // Server down or restarting: say so once, instead of a page of widgets all failing and retrying.
  if (me.error) {
    return (
      <div className="grid min-h-dvh place-items-center px-6">
        <div className="max-w-sm text-center animate-fade-up">
          <LogoMark size={40} className="mx-auto opacity-80" />
          <h1 className="mt-5 text-lg font-semibold tracking-[-0.01em]">Can't reach Casefile</h1>
          <p className="mt-1.5 text-sm text-muted">
            The server didn't answer{me.error instanceof ApiError ? ` (${me.error.status})` : ""}. It may be restarting — nothing you did was lost.
          </p>
          <button
            onClick={() => me.refetch()}
            disabled={me.isFetching}
            className="mt-5 inline-flex h-9 items-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60"
          >
            {me.isFetching ? "Trying…" : "Try again"}
          </button>
        </div>
      </div>
    );
  }
  const user = me.data;
  const admin = user?.role === "admin";
  const mode = user?.org_mode;
  const current = navFor(location.pathname);
  const CurrentIcon = current?.icon;

  return (
    <div className="min-h-dvh">
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 hidden flex-col border-r border-line bg-panel transition-[width] duration-300 ease-[cubic-bezier(.16,1,.3,1)] lg:flex",
          collapsed ? "w-[68px]" : "w-[252px]",
        )}
      >
        <SidebarBody me={user} collapsed={collapsed} onCollapse={() => setCollapsed(!collapsed)} onSignOut={signOut} />
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/40 animate-fade-in" onClick={() => setMobileOpen(false)} aria-hidden />
          <aside className="absolute inset-y-0 left-0 flex w-[280px] flex-col border-r border-line bg-panel shadow-lg animate-slide-in-left">
            <SidebarBody me={user} collapsed={false} onNavigate={() => setMobileOpen(false)} onSignOut={signOut} onClose={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      <div className={cn("transition-[padding] duration-300 ease-[cubic-bezier(.16,1,.3,1)]", collapsed ? "lg:pl-[68px]" : "lg:pl-[252px]")}>
        <header className="sticky top-0 z-30 border-b border-line bg-canvas/80 backdrop-blur-xl supports-[backdrop-filter]:bg-canvas/70">
          <div className="flex h-14 items-center gap-3 px-4 sm:px-6 lg:px-8">
            <button onClick={() => setMobileOpen(true)} aria-label="Open menu" className="-ml-1 rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink lg:hidden">
              <MenuIcon className="size-5" />
            </button>
            <div className="flex min-w-0 items-center gap-2 text-[13.5px]">
              {CurrentIcon && <CurrentIcon aria-hidden className="size-4 shrink-0 text-faint" />}
              <span className="truncate font-medium text-ink">{current?.label ?? "Casefile"}</span>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <button
                onClick={() => setPalette(true)}
                className="flex h-8 items-center gap-2 rounded-lg border border-line bg-panel px-2.5 text-[13px] text-faint shadow-xs transition-colors hover:border-line-strong hover:text-muted sm:w-60"
              >
                <Search className="size-4" aria-hidden />
                <span className="hidden flex-1 text-left sm:inline">Search…</span>
                <span className="hidden items-center gap-0.5 sm:flex">
                  <Kbd>{isMac ? "⌘" : "Ctrl"}</Kbd>
                  <Kbd>K</Kbd>
                </span>
              </button>
              <ExecutorChip />
              <button
                onClick={() => setChoice(dark ? "light" : "dark")}
                aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
                title={dark ? "Light theme" : "Dark theme"}
                className="grid size-8 place-items-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink"
              >
                {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
              </button>
            </div>
          </div>
          {mode && mode !== "live" && <ModeBanner mode={mode} admin={admin} />}
        </header>

        <main className="mx-auto w-full max-w-[1320px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          <EvidenceDrawerProvider key={location.pathname}>
            <Outlet />
          </EvidenceDrawerProvider>
        </main>
      </div>

      {palette && <CommandPalette onClose={closePalette} isAdmin={admin} onSignOut={signOut} />}
    </div>
  );
}
