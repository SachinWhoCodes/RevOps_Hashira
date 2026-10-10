import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { api, ApiError, type User } from "./api";
import { Loading } from "./components/ui";
import AccountPage from "./pages/AccountPage";
import AccountsPage from "./pages/AccountsPage";
import ConnectionsPage from "./pages/ConnectionsPage";
import IdentityPage from "./pages/IdentityPage";
import LoginPage from "./pages/LoginPage";

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
        <nav className="flex gap-1 px-2 pb-3 md:block md:space-y-0.5">
          <NavLink to="/accounts" className={link}>Accounts</NavLink>
          <NavLink to="/connections" className={link}>Connections</NavLink>
          <NavLink to="/identity" className={link}>Unmatched records</NavLink>
        </nav>
        <div className="hidden px-4 py-4 text-xs text-muted md:block">
          <div className="truncate">{me.data?.email}</div>
          <button className="mt-1 text-accent hover:underline" onClick={() => logout.mutate()}>Sign out</button>
        </div>
      </aside>
      <main className="mx-auto w-full max-w-6xl px-4 py-6 md:px-8">
        <Outlet />
      </main>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Shell />}>
        <Route path="/" element={<Navigate to="/accounts" replace />} />
        <Route path="/accounts" element={<AccountsPage />} />
        <Route path="/accounts/:id" element={<AccountPage />} />
        <Route path="/connections" element={<ConnectionsPage />} />
        <Route path="/identity" element={<IdentityPage />} />
      </Route>
    </Routes>
  );
}
