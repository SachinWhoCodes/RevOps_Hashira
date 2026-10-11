import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Building, Globe, MailCheck, ShieldCheck, type LucideIcon } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type User } from "../api";
import { AuthHeading, AuthLayout } from "../components/auth/AuthLayout";
import { PasswordStrength } from "../components/auth/PasswordStrength";
import { Button, Callout, ErrorNote, Field, Input, PasswordInput, Skeleton } from "../components/ui";

type SignupConfig = { enabled: boolean; mail: "brevo" | "outbox" | "none" };

function BackToSignIn({ children = "Back to sign in" }: { children?: ReactNode }) {
  return (
    <Link to="/login" className="group mt-8 inline-flex items-center gap-1.5 text-sm font-medium text-muted transition-colors hover:text-ink">
      <ArrowLeft className="size-4 transition-transform group-hover:-translate-x-0.5" />
      {children}
    </Link>
  );
}

// A calm confirmation screen: a lit icon, a headline, what to do next.
function Done({ icon: Icon, title, children }: { icon: LucideIcon; title: string; children: ReactNode }) {
  return (
    <div>
      <div className="relative mb-6 w-fit">
        <div className="absolute inset-0 rounded-2xl bg-gradient-to-br from-indigo-500 to-cyan-400 opacity-40 blur-xl" aria-hidden />
        <div className="relative grid size-14 place-items-center rounded-2xl bg-gradient-to-br from-indigo-500 via-violet-500 to-cyan-400 text-white shadow-lg">
          <Icon className="size-6" />
        </div>
      </div>
      <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.03em]">{title}</h1>
      <div className="mt-3 space-y-4 text-[14.5px] leading-relaxed text-muted">{children}</div>
    </div>
  );
}

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
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!mismatch) register.mutate();
  };
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  if (register.data) {
    return (
      <AuthLayout>
        <Done icon={MailCheck} title="Check your inbox">
          <p>
            We sent a confirmation link to <span className="font-medium text-ink">{register.data.email}</span>. Open it within 24 hours to
            create <span className="font-medium text-ink">{form.company}</span> — nothing is created until you confirm.
          </p>
          {register.data.mail === "outbox" && (
            <Callout tone="warn" title="Local development">
              No email service is configured, so the message was written to <code className="font-mono text-xs">data/outbox</code> on the server.
            </Callout>
          )}
          <p className="text-[13px] text-faint">No email? Check spam, or register again for a new link (the old one stops working).</p>
        </Done>
        <BackToSignIn />
      </AuthLayout>
    );
  }
  if (config.data && !config.data.enabled) {
    return (
      <AuthLayout>
        <AuthHeading title="Registration is closed" subtitle="This Casefile server only admits invited people. Ask your Casefile admin for an invite link." />
        <BackToSignIn>Sign in</BackToSignIn>
      </AuthLayout>
    );
  }
  return (
    <AuthLayout>
      <AuthHeading
        title="Set up your company"
        subtitle="Confirm your email, then connect your CRM, inbox and Slack. Casefile only reads until you decide to go live."
      />
      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Company" htmlFor="company">
            <Input id="company" icon={Building} required autoFocus value={form.company} onChange={set("company")} autoComplete="organization" placeholder="Acme Inc." />
          </Field>
          <Field label="Your name" htmlFor="name">
            <Input id="name" value={form.name} onChange={set("name")} autoComplete="name" placeholder="Priya Sharma" />
          </Field>
        </div>
        <Field label="Work email" htmlFor="email">
          <Input id="email" type="email" required value={form.email} onChange={set("email")} autoComplete="username" placeholder="you@company.com" />
        </Field>
        <Field label="Password" htmlFor="password">
          <PasswordInput id="password" required minLength={12} value={form.password} onChange={set("password")} autoComplete="new-password" />
        </Field>
        <PasswordStrength password={form.password} />
        <Field label="Confirm password" htmlFor="again" error={mismatch ? "The two passwords differ." : undefined}>
          <PasswordInput id="again" required value={form.again} onChange={set("again")} autoComplete="new-password" aria-invalid={mismatch || undefined} />
        </Field>
        {register.error && <ErrorNote error={register.error} title="Could not register" />}
        {config.data?.mail === "none" && (
          <Callout tone="risk" title="Email is not configured on this server">
            Registration cannot finish until the operator sets up email delivery.
          </Callout>
        )}
        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full"
          loading={register.isPending}
          iconRight={register.isPending ? undefined : ArrowRight}
          disabled={mismatch || config.data?.mail === "none"}
        >
          {register.isPending ? "Sending confirmation" : "Create account"}
        </Button>
        <p className="flex items-center gap-1.5 text-xs text-faint">
          <Globe className="size-3.5" /> Time zone {timezone} — change it later in setup.
        </p>
      </form>
      <div className="mt-8 border-t border-line pt-6 text-sm text-muted">
        Already have an account?{" "}
        <Link to="/login" className="font-medium text-accent-fg hover:underline">
          Sign in
        </Link>
      </div>
    </AuthLayout>
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
    onSuccess: (user) => {
      qc.setQueryData(["me"], user);
      navigate("/");
    },
  });
  const d = details.data;
  return (
    <AuthLayout>
      <AuthHeading title="Confirm your company" subtitle="One last step. You'll be signed in as its first admin." />
      {details.isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-20 w-full rounded-xl" />
          <Skeleton className="h-11 w-full rounded-lg" />
        </div>
      )}
      {details.error && <ErrorNote error={details.error} title="This link can't be used" />}
      {d && (
        <div className="space-y-5">
          <div className="flex items-center gap-4 rounded-xl border border-line bg-subtle p-4">
            <span className="grid size-11 place-items-center rounded-xl bg-accent-soft text-lg font-semibold text-accent-fg">{d.company.trim()[0]?.toUpperCase()}</span>
            <div className="min-w-0">
              <div className="truncate font-semibold">{d.company}</div>
              <div className="truncate text-[13px] text-muted">Admin: {d.name ? `${d.name} · ` : ""}{d.email}</div>
            </div>
          </div>
          {confirm.error && <ErrorNote error={confirm.error} />}
          <Button variant="primary" size="lg" className="w-full" loading={confirm.isPending} icon={confirm.isPending ? undefined : ShieldCheck} onClick={() => confirm.mutate()}>
            {confirm.isPending ? "Creating your company" : `Create ${d.company}`}
          </Button>
          <p className="text-xs text-faint">Your company starts in setup mode: Casefile reads and scores data, runs no AI and writes nothing to your tools.</p>
        </div>
      )}
      <BackToSignIn>Sign in instead</BackToSignIn>
    </AuthLayout>
  );
}

export function ForgotPage() {
  const [email, setEmail] = useState("");
  const send = useMutation({ mutationFn: () => api<{ sent: boolean }>("/auth/forgot", { json: { email } }) });
  if (send.data) {
    return (
      <AuthLayout>
        <Done icon={MailCheck} title="Check your inbox">
          <p>
            If <span className="font-medium text-ink">{email}</span> has a Casefile account, a reset link is on its way. It works once and
            expires in 72 hours.
          </p>
        </Done>
        <BackToSignIn />
      </AuthLayout>
    );
  }
  return (
    <AuthLayout>
      <AuthHeading title="Reset your password" subtitle="Enter your work email and we'll send you a link to choose a new one." />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send.mutate();
        }}
        className="space-y-5"
      >
        <Field label="Work email" htmlFor="email">
          <Input id="email" type="email" inputSize="lg" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" placeholder="you@company.com" />
        </Field>
        {send.error && <ErrorNote error={send.error} />}
        <Button type="submit" variant="primary" size="lg" className="w-full" loading={send.isPending}>
          {send.isPending ? "Sending" : "Email me a reset link"}
        </Button>
      </form>
      <BackToSignIn />
    </AuthLayout>
  );
}
