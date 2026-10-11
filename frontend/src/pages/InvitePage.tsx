import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type User } from "../api";
import { AuthHeading, AuthLayout } from "../components/auth/AuthLayout";
import { PasswordStrength } from "../components/auth/PasswordStrength";
import { Button, ErrorNote, Field, PasswordInput, Skeleton } from "../components/ui";

// Public page behind a one-time invite (or reset) link: choose a password and you're signed in.
export default function InvitePage() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const details = useQuery({
    queryKey: ["invite", token],
    queryFn: () => api<{ email: string; name: string; organisation: string; reset: boolean }>(`/auth/invite/${token}`),
    retry: false,
  });
  const accept = useMutation({
    mutationFn: () => api<User>(`/auth/invite/${token}`, { json: { password } }),
    onSuccess: (user) => {
      qc.setQueryData(["me"], user);
      navigate("/");
    },
  });
  const mismatch = again.length > 0 && again !== password;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!mismatch) accept.mutate();
  };
  const d = details.data;
  return (
    <AuthLayout>
      {details.isLoading && (
        <div className="space-y-4">
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="mt-6 h-11 w-full rounded-lg" />
        </div>
      )}
      {details.error && (
        <>
          <AuthHeading title="This link can't be used" />
          <ErrorNote error={details.error} title="Invite link problem" />
          <p className="mt-6 text-sm text-muted">
            Ask your Casefile admin for a new link, or <Link to="/forgot" className="font-medium text-accent-fg hover:underline">reset your password</Link>.
          </p>
        </>
      )}
      {d && (
        <>
          <AuthHeading
            title={d.reset ? "Choose a new password" : `Join ${d.organisation}`}
            subtitle={d.reset ? `For ${d.email} at ${d.organisation}.` : `You've been invited as ${d.email}. Choose a password to finish.`}
          />
          <form onSubmit={submit} className="space-y-4">
            <input type="email" autoComplete="username" value={d.email} readOnly hidden />
            <Field label="Password" htmlFor="pw">
              <PasswordInput id="pw" inputSize="lg" autoFocus autoComplete="new-password" required minLength={12} value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            <PasswordStrength password={password} />
            <Field label="Confirm password" htmlFor="pw2" error={mismatch ? "The two passwords differ." : undefined}>
              <PasswordInput id="pw2" inputSize="lg" autoComplete="new-password" required value={again} onChange={(e) => setAgain(e.target.value)} aria-invalid={mismatch || undefined} />
            </Field>
            {accept.error && <ErrorNote error={accept.error} />}
            <Button type="submit" variant="primary" size="lg" className="w-full" disabled={mismatch} loading={accept.isPending} iconRight={accept.isPending ? undefined : ArrowRight}>
              {accept.isPending ? "Saving" : d.reset ? "Save and sign in" : "Join and sign in"}
            </Button>
          </form>
        </>
      )}
    </AuthLayout>
  );
}
