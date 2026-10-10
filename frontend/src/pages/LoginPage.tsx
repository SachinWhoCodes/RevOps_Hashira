import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, type User } from "../api";
import { ErrorNote } from "../components/ui";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const navigate = useNavigate();
  const qc = useQueryClient();
  const login = useMutation({
    mutationFn: () => api<User>("/auth/login", { json: { email, password } }),
    onSuccess: (user) => {
      qc.setQueryData(["me"], user);
      navigate("/accounts");
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-line bg-panel p-6">
        <div>
          <h1 className="text-lg font-semibold">Sign in to Casefile</h1>
          <p className="text-sm text-muted">Use your work email.</p>
        </div>
        <label className="block text-sm">
          <span className="text-muted">Email</span>
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-md border border-line px-3 py-2 outline-none focus:border-accent" />
        </label>
        <label className="block text-sm">
          <span className="text-muted">Password</span>
          <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-md border border-line px-3 py-2 outline-none focus:border-accent" />
        </label>
        {login.error && <ErrorNote error={login.error} />}
        <button disabled={login.isPending} className="w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-60">
          {login.isPending ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
