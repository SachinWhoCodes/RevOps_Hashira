import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, type User } from "../api";
import { ErrorNote, Loading } from "../components/ui";

// Public page behind a one-time invite link: the person chooses their password and is signed in.
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
    onSuccess: (user) => { qc.setQueryData(["me"], user); navigate("/"); },
  });
  const mismatch = again.length > 0 && again !== password;
  const submit = (e: FormEvent) => { e.preventDefault(); if (!mismatch) accept.mutate(); };
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-4 rounded-lg border border-line bg-panel p-6">
        {details.isLoading && <Loading />}
        {details.error && <ErrorNote error={details.error} />}
        {details.data && (
          <form onSubmit={submit} className="space-y-4">
            <div>
              <h1 className="text-lg font-semibold">{details.data.reset ? "Choose a new password" : "Welcome to Casefile"}</h1>
              <p className="text-sm text-muted">
                {details.data.reset
                  ? `For ${details.data.email} at ${details.data.organisation}. 12+ characters.`
                  : `${details.data.organisation} invited ${details.data.email}. Choose a password (12+ characters).`}
              </p>
            </div>
            <input type="email" autoComplete="username" value={details.data.email} readOnly hidden />
            <label className="block text-sm">
              <span className="text-muted">Password</span>
              <input type="password" autoComplete="new-password" required minLength={12} value={password} onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded-md border border-line px-3 py-2 outline-none focus:border-accent" />
            </label>
            <label className="block text-sm">
              <span className="text-muted">Again</span>
              <input type="password" autoComplete="new-password" required value={again} onChange={(e) => setAgain(e.target.value)}
                className="mt-1 w-full rounded-md border border-line px-3 py-2 outline-none focus:border-accent" />
            </label>
            {mismatch && <p className="text-xs text-risk">The two passwords differ.</p>}
            {accept.error && <ErrorNote error={accept.error} />}
            <button disabled={accept.isPending || mismatch} className="w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-60">
              {accept.isPending ? "Saving…" : "Set password and sign in"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
