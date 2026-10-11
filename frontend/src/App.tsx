import { Compass } from "lucide-react";
import { lazy, Suspense, type ReactNode } from "react";
import { Route, Routes } from "react-router-dom";
import AppShell from "./components/shell/AppShell";
import { ButtonLink, EmptyState, Skeleton } from "./components/ui";

// Every page loads on demand, so signing in never downloads the whole product.
const LoginPage = lazy(() => import("./pages/LoginPage"));
const InvitePage = lazy(() => import("./pages/InvitePage"));
const RegisterPage = lazy(() => import("./pages/AuthPages").then((m) => ({ default: m.RegisterPage })));
const VerifyPage = lazy(() => import("./pages/AuthPages").then((m) => ({ default: m.VerifyPage })));
const ForgotPage = lazy(() => import("./pages/AuthPages").then((m) => ({ default: m.ForgotPage })));
const OverviewPage = lazy(() => import("./pages/OverviewPage"));
const OnboardingPage = lazy(() => import("./pages/OnboardingPage"));
const QueuePage = lazy(() => import("./pages/QueuePage"));
const InboxPage = lazy(() => import("./pages/InboxPage"));
const OutcomesPage = lazy(() => import("./pages/OutcomesPage"));
const BacktestPage = lazy(() => import("./pages/BacktestPage"));
const ReplayPage = lazy(() => import("./pages/ReplayPage"));
const BlindSpotsPage = lazy(() => import("./pages/BlindSpotsPage"));
const AuditPage = lazy(() => import("./pages/AuditPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const TeamPage = lazy(() => import("./pages/TeamPage"));
const AccountsPage = lazy(() => import("./pages/AccountsPage"));
const AccountPage = lazy(() => import("./pages/AccountPage"));
const ConnectionsPage = lazy(() => import("./pages/ConnectionsPage"));
const IdentityPage = lazy(() => import("./pages/IdentityPage"));

function PageFallback() {
  return (
    <div className="space-y-6" aria-busy>
      <div className="space-y-2">
        <Skeleton className="h-7 w-56" />
        <Skeleton className="h-4 w-96 max-w-full" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-32 rounded-xl" />)}
      </div>
      <Skeleton className="h-72 rounded-xl" />
    </div>
  );
}

const page = (el: ReactNode) => <Suspense fallback={<PageFallback />}>{el}</Suspense>;

function NotFound() {
  return (
    <EmptyState
      icon={Compass}
      title="This page doesn't exist"
      description="The link may be old, or the page moved. Everything is reachable from the sidebar or the search (Ctrl K)."
      action={<ButtonLink to="/" variant="primary">Go to the overview</ButtonLink>}
    />
  );
}

export default function App() {
  return (
    <Suspense fallback={<div className="min-h-dvh bg-panel" />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/invite/:token" element={<InvitePage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/verify/:token" element={<VerifyPage />} />
        <Route path="/forgot" element={<ForgotPage />} />
        <Route element={<AppShell />}>
          <Route path="/" element={page(<OverviewPage />)} />
          <Route path="/setup" element={page(<OnboardingPage />)} />
          <Route path="/queue" element={page(<QueuePage />)} />
          <Route path="/approvals" element={page(<InboxPage />)} />
          <Route path="/outcomes" element={page(<OutcomesPage />)} />
          <Route path="/backtest" element={page(<BacktestPage />)} />
          <Route path="/backtest/replay/:accountId" element={page(<ReplayPage />)} />
          <Route path="/blind-spots" element={page(<BlindSpotsPage />)} />
          <Route path="/audit" element={page(<AuditPage />)} />
          <Route path="/settings" element={page(<SettingsPage />)} />
          <Route path="/team" element={page(<TeamPage />)} />
          <Route path="/accounts" element={page(<AccountsPage />)} />
          <Route path="/accounts/:id" element={page(<AccountPage />)} />
          <Route path="/connections" element={page(<ConnectionsPage />)} />
          <Route path="/identity" element={page(<IdentityPage />)} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
