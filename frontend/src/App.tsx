import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { api, ApiError, type OrgMode, type User } from "./api";
import { EvidenceDrawerProvider } from "./components/EvidenceDrawer";
import { Loading } from "./components/ui";
import AccountPage from "./pages/AccountPage";
import AccountsPage from "./pages/AccountsPage";
import AuditPage from "./pages/AuditPage";
import { ForgotPage, RegisterPage, VerifyPage } from "./pages/AuthPages";
import BacktestPage from "./pages/BacktestPage";
import BlindSpotsPage from "./pages/BlindSpotsPage";
import ConnectionsPage from "./pages/ConnectionsPage";
import IdentityPage from "./pages/IdentityPage";
import InboxPage from "./pages/InboxPage";
import InvitePage from "./pages/InvitePage";
import LoginPage from "./pages/LoginPage";
import OnboardingPage from "./pages/OnboardingPage";
import OutcomesPage from "./pages/OutcomesPage";
import QueuePage from "./pages/QueuePage";
import ReplayPage from "./pages/ReplayPage";
import SettingsPage from "./pages/SettingsPage";
import TeamPage from "./pages/TeamPage";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<User>("/auth/me"), retry: false });
}

// Every page says so when the organisation is not live, so nobody mistakes a shadow approval for a write.
function ModeBanner({ mode, admin }: { mode: OrgMode; admin: boolean }) {
  const text =
    mode === "setup"
      ? "Setup mode: Casefile is reading and scoring your data. No AI runs and nothing is written to your tools."
      : "Shadow mode: approvals are recorded as “would execute”. Nothing is written to your tools.";
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-md border border-warn bg-warn-soft px-3 py-2 text-sm text-warn">
      <span>{text}</span>
      {admin && <NavLink to="/setup" className="font-medium underline">Setup checklist</NavLink>}
    </div>
  );
}

// Admins of a company still in setup land on the checklist; everyone else on the queue.
function Home() {
  const me = useMe();
  if (me.isLoading) return <Loading />;
  const setup = me.data?.role === "admin" && me.data.org_mode === "setup";
  return <Navigate to={setup ? "/setup" : "/queue"} replace />;
}

function Shell() {
  const me = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const logout = useMutation({
    mutationFn: () => api("/auth/logout", { method: "POST", json: {} }),
    onSuccess: () => {
      qc.clear();
      navigate("/login");
    },
  });
  if (me.isLoading) return <Loading />;
  if (me.error instanceof ApiError && me.error.status === 401) return <Navigate to="/login" replace />;
  const admin = me.data?.role === "admin";
  const mode = me.data?.org_mode;
  const notLive = mode !== undefined && mode !== "live";
  const link = ({ isActive }: { isActive: boolean }) =>
    `block rounded-md px-3 py-2 text-sm ${isActive ? "bg-accent-soft font-medium text-accent" : "text-muted hover:bg-canvas hover:text-ink"}`;
  return (
    <div className="min-h-screen md:flex">
      <aside className="border-b border-line bg-panel md:min-h-screen md:w-56 md:border-r md:border-b-0">
        <div className="flex items-center justify-between px-4 py-4 md:block">
          <div className="text-base font-semibold tracking-tight">Casefile</div>
          <div className="text-xs text-faint md:mt-0.5">Evidence-first revenue agent</div>
        </div>
        <nav className="flex flex-wrap gap-1 px-2 pb-3 md:block md:space-y-0.5">
          {admin && notLive && <NavLink to="/setup" className={link}>Setup</NavLink>}
          <NavLink to="/queue" className={link}>Queue</NavLink>
          <NavLink to="/approvals" className={link}>Approvals</NavLink>
          <NavLink to="/outcomes" className={link}>Outcomes</NavLink>
          <NavLink to="/accounts" className={link}>Accounts</NavLink>
          <NavLink to="/backtest" className={link}>Back-test</NavLink>
          <NavLink to="/blind-spots" className={link}>Blind spots</NavLink>
          <NavLink to="/identity" className={link}>Unmatched records</NavLink>
          <NavLink to="/connections" className={link}>Connections</NavLink>
          <NavLink to="/audit" className={link}>Audit log</NavLink>
          {admin && <NavLink to="/team" className={link}>Team</NavLink>}
          {admin && !notLive && <NavLink to="/setup" className={link}>Setup</NavLink>}
          <NavLink to="/settings" className={link}>Settings</NavLink>
        </nav>
        <div className="hidden px-4 py-4 text-xs text-muted md:block">
          <div className="truncate">{me.data?.email}</div>
          <button className="mt-1 text-accent hover:underline" onClick={() => logout.mutate()}>Sign out</button>
        </div>
      </aside>
      <main className="mx-auto w-full max-w-6xl px-4 py-6 md:px-8">
        {notLive && <ModeBanner mode={mode} admin={admin} />}
        <EvidenceDrawerProvider>
          <Outlet />
        </EvidenceDrawerProvider>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/invite/:token" element={<InvitePage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/verify/:token" element={<VerifyPage />} />
      <Route path="/forgot" element={<ForgotPage />} />
      <Route element={<Shell />}>
        <Route path="/" element={<Home />} />
        <Route path="/setup" element={<OnboardingPage />} />
        <Route path="/queue" element={<QueuePage />} />
        <Route path="/approvals" element={<InboxPage />} />
        <Route path="/outcomes" element={<OutcomesPage />} />
        <Route path="/backtest" element={<BacktestPage />} />
        <Route path="/backtest/replay/:accountId" element={<ReplayPage />} />
        <Route path="/blind-spots" element={<BlindSpotsPage />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/team" element={<TeamPage />} />
        <Route path="/accounts" element={<AccountsPage />} />
        <Route path="/accounts/:id" element={<AccountPage />} />
        <Route path="/connections" element={<ConnectionsPage />} />
        <Route path="/identity" element={<IdentityPage />} />
      </Route>
    </Routes>
  );
}
