import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, type User } from "../api";
import { AuthHeading, AuthLayout } from "../components/auth/AuthLayout";
import { Button, Callout, Field, Input, PasswordInput } from "../components/ui";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const navigate = useNavigate();
  const qc = useQueryClient();
  const login = useMutation({
    mutationFn: () => api<User>("/auth/login", { json: { email, password } }),
    onSuccess: (user) => {
      qc.setQueryData(["me"], user);
      navigate("/");
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };
  const err = login.error;
  return (
    <AuthLayout>
      <AuthHeading title="Welcome back" subtitle="Sign in to see which accounts need you today, and why." />
      <form onSubmit={submit} className="space-y-5">
        <Field label="Work email" htmlFor="email">
          <Input id="email" type="email" inputSize="lg" autoComplete="username" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" />
        </Field>
        <Field
          label="Password"
          htmlFor="password"
          action={<Link to="/forgot" className="text-[13px] font-medium text-accent-fg hover:underline">Forgot password?</Link>}
        >
          <PasswordInput id="password" inputSize="lg" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {err && (
          <Callout tone="risk" title={err instanceof ApiError && err.status === 401 ? "That email and password don't match" : "Could not sign in"}>
            {err instanceof ApiError && err.status === 401 ? "Check both and try again, or reset your password." : err.message}
          </Callout>
        )}
        <Button type="submit" variant="primary" size="lg" className="w-full" loading={login.isPending} iconRight={login.isPending ? undefined : ArrowRight}>
          {login.isPending ? "Signing in" : "Sign in"}
        </Button>
      </form>
      <div className="mt-8 border-t border-line pt-6 text-sm text-muted">
        New to Casefile?{" "}
        <Link to="/register" className="font-medium text-accent-fg hover:underline">
          Set up your company
        </Link>
      </div>
    </AuthLayout>
  );
}
