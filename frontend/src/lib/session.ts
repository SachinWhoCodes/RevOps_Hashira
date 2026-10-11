import { useQuery } from "@tanstack/react-query";
import { api, type User } from "../api";

// The signed-in person (and their organisation's mode). 401 means "not signed in", so no retries.
export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<User>("/auth/me"), retry: false, staleTime: 60_000 });
}

export const firstName = (u: Pick<User, "name" | "email"> | undefined) => (u?.name || u?.email || "").split(/[\s@]/)[0];
