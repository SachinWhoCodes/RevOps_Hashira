import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type User } from "../api";
import { ErrorNote, Loading } from "../components/ui";

const field = "mt-1 w-full rounded-md border border-line px-3 py-2 outline-none focus:border-accent";
const primary = "w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-60";

export function AuthCard({ title, subtitle, children, footer }: { title: string; subtitle?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-8">
      <div className="w-full max-w-sm">
        <div className="mb-4 text-center">
          <div className="text-base font-semibold tracking-tight">Casefile</div>
          <div className="text-xs text-faint">Evidence-first revenue agent</div>
        </div>
        <div className="space-y-4 rounded-lg border border-line bg-panel p-6">
          <div>
            <h1 className="text-lg font-semibold">{title}</h1>
            {subtitle && <p className="text-sm text-muted">{subtitle}</p>}
          </div>
          {children}
        </div>
        {footer && <div className="mt-3 text-center text-sm text-muted">{footer}</div>}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="text-muted">{label}</span>
      {children}
    </label>
  );
}

type SignupConfig = { enabled: boolean; mail: "brevo" | "outbox" | "none" };

// Anyone can create a company; nothing exists until the emailed link is confirmed.
export function RegisterPage() {
  const config = useQuery({ queryKey: ["signup-config"], queryFn: () => api<SignupConfig>("/auth/signup-config") });
  const [form, setForm] = useState({ company: "", name: "", email: "", password: "", again: "" });
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const register = useMutation({
    mutationFn: () =>
      api<{ sent: boolean; email: string; mail: SignupConfig["mail"] }>("/auth/register", {
        json: { company: form.company, name: form.name, email: form.email, password: form.password, timezone },
      }),
  });
  const mismatch = form.again.length > 0 && form.again !== form.password;
  const submit = (e: FormEvent) => { e.preventDefault(); if (!mismatch) register.mutate(); };
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  if (register.data) {
    return (
      <AuthCard title="Check your email" footer={<Link to="/login" className="text-accent hover:underline">Back to sign in</Link>}>
        <p className="text-sm">
          We sent a link to <span className="font-medium">{register.data.email}</span>. Open it within 24 hours to create {form.company}.
          Nothing is created until you confirm.
        </p>
        {register.data.mail === "outbox" && (
          <p className="rounded-md bg-warn-soft px-3 py-2 text-xs text-warn">
            Local development: no email service is configured, so the message was written to <span className="font-mono">data/outbox</span> on the server.
          </p>
        )}
        <p className="text-xs text-faint">No email? Check spam, or register again to get a new link (the old one stops working).</p>
      </AuthCard>
    );
  }
  if (config.data && !config.data.enabled) {
    return <AuthCard title="Registration is closed" footer={<Link to="/login" className="text-accent hover:underline">Sign in</Link>}><p className="text-sm text-muted">Ask your Casefile admin for an invite.</p></AuthCard>;
  }
  return (
    <AuthCard
      title="Create your company on Casefile"
      subtitle="You'll confirm your email, then connect your CRM, Google and Slack yourself. Nothing is written to your tools until you switch to live."
      footer={<>Already have an account? <Link to="/login" className="text-accent hover:underline">Sign in</Link></>}
    >
      <form onSubmit={submit} className="space-y-3">
        <Field label="Company name"><input required value={form.company} onChange={set("company")} className={field} autoComplete="organization" /></Field>
        <Field label="Your name"><input value={form.name} onChange={set("name")} className={field} autoComplete="name" /></Field>
        <Field label="Work email"><input type="email" required value={form.email} onChange={set("email")} className={field} autoComplete="username" /></Field>
        <Field label="Password (12+ characters)">
          <input type="password" required minLength={12} value={form.password} onChange={set("password")} className={field} autoComplete="new-password" />
        </Field>
        <Field label="Password again"><input type="password" required value={form.again} onChange={set("again")} className={field} autoComplete="new-password" /></Field>
        {mismatch && <p className="text-xs text-risk">The two passwords differ.</p>}
        {register.error && <ErrorNote error={register.error} />}
        {config.data?.mail === "none" && <p className="text-xs text-risk">Email is not configured on this server, so registration cannot finish.</p>}
        <button disabled={register.isPending || mismatch || config.data?.mail === "none"} className={primary}>
          {register.isPending ? "Sending…" : "Create account"}
        </button>
        <p className="text-xs text-faint">Time zone: {timezone} (change it later on the Setup page).</p>
      </form>
    </AuthCard>
  );
}

// The emailed link lands here; confirming is a button press, so mail scanners can't create accounts.
export function VerifyPage() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const details = useQuery({
    queryKey: ["verify", token],
    queryFn: () => api<{ email: string; company: string; name: string }>(`/auth/verify/${token}`),
    retry: false,
  });
  const confirm = useMutation({
    mutationFn: () => api<User>(`/auth/verify/${token}`, { method: "POST", json: {} }),
    onSuccess: (user) => { qc.setQueryData(["me"], user); navigate("/"); },
  });
  return (
    <AuthCard title="Confirm your email" footer={<Link to="/login" className="text-accent hover:underline">Sign in instead</Link>}>
      {details.isLoading && <Loading />}
      {details.error && <ErrorNote error={details.error} />}
      {details.data && (
        <>
          <p className="text-sm">
            Create <span className="font-medium">{details.data.company}</span> with <span className="font-medium">{details.data.email}</span> as its admin?
          </p>
          {confirm.error && <ErrorNote error={confirm.error} />}
          <button className={primary} disabled={confirm.isPending} onClick={() => confirm.mutate()}>
            {confirm.isPending ? "Creating…" : `Confirm and create ${details.data.company}`}
          </button>
        </>
      )}
    </AuthCard>
  );
}

export function ForgotPage() {
  const [email, setEmail] = useState("");
  const send = useMutation({ mutationFn: () => api<{ sent: boolean }>("/auth/forgot", { json: { email } }) });
  return (
    <AuthCard title="Reset your password" footer={<Link to="/login" className="text-accent hover:underline">Back to sign in</Link>}>
      {send.data ? (
        <p className="text-sm">If <span className="font-medium">{email}</span> has an account, a reset link is on its way. It works once and expires in 72 hours.</p>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); send.mutate(); }} className="space-y-3">
          <Field label="Work email"><input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={field} autoComplete="username" /></Field>
          {send.error && <ErrorNote error={send.error} />}
          <button disabled={send.isPending} className={primary}>{send.isPending ? "Sending…" : "Email me a reset link"}</button>
        </form>
      )}
    </AuthCard>
  );
}
