import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { api, ApiError, type User } from "./api";
import { EvidenceDrawerProvider } from "./components/EvidenceDrawer";
import { Loading } from "./components/ui";
import AccountPage from "./pages/AccountPage";
import AccountsPage from "./pages/AccountsPage";
import AuditPage from "./pages/AuditPage";
import BlindSpotsPage from "./pages/BlindSpotsPage";
import ConnectionsPage from "./pages/ConnectionsPage";
import IdentityPage from "./pages/IdentityPage";
import LoginPage from "./pages/LoginPage";
import QueuePage from "./pages/QueuePage";
import SettingsPage from "./pages/SettingsPage";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<User>("/auth/me"), retry: false });
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
          <NavLink to="/queue" className={link}>Queue</NavLink>
          <NavLink to="/accounts" className={link}>Accounts</NavLink>
          <NavLink to="/blind-spots" className={link}>Blind spots</NavLink>
          <NavLink to="/identity" className={link}>Unmatched records</NavLink>
          <NavLink to="/connections" className={link}>Connections</NavLink>
          <NavLink to="/audit" className={link}>Audit log</NavLink>
          <NavLink to="/settings" className={link}>Settings</NavLink>
        </nav>
        <div className="hidden px-4 py-4 text-xs text-muted md:block">
          <div className="truncate">{me.data?.email}</div>
          <button className="mt-1 text-accent hover:underline" onClick={() => logout.mutate()}>Sign out</button>
        </div>
      </aside>
      <main className="mx-auto w-full max-w-6xl px-4 py-6 md:px-8">
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
      <Route element={<Shell />}>
        <Route path="/" element={<Navigate to="/queue" replace />} />
        <Route path="/queue" element={<QueuePage />} />
        <Route path="/blind-spots" element={<BlindSpotsPage />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/accounts" element={<AccountsPage />} />
        <Route path="/accounts/:id" element={<AccountPage />} />
        <Route path="/connections" element={<ConnectionsPage />} />
        <Route path="/identity" element={<IdentityPage />} />
      </Route>
    </Routes>
  );
}
