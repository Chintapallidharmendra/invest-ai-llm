/**
 * The signed-in user, from the `GET /auth/me` query (ADR-008), as `AuthContext`.
 *
 * - pending → `loading`; a 401 → `anonymous`; a user → `authenticated`;
 * - any other failure → `unknown` (pages render; the server still enforces access).
 *
 * Nothing about the session is stored in the browser: the HttpOnly cookie carries it,
 * and this query is the only source of the current user.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, type ReactNode } from "react";

import { api } from "@/api/client";
import { isProblemError } from "@/api/problem";
import { queryKeys } from "@/api/queryKeys";
import type { components } from "@/api/schema";
import { AuthContext, type AuthState } from "@/auth/RequireAuth";

export type Me = components["schemas"]["Me"];

export const ME_PATH = "/auth/me";

export async function fetchMe(): Promise<Me | null> {
  try {
    return await api.get<Me>(ME_PATH, { redirectOn401: false });
  } catch (error) {
    if (isProblemError(error) && error.status === 401) return null;
    throw error;
  }
}

export function useMe() {
  return useQuery({ queryKey: queryKeys.auth.me(), queryFn: fetchMe });
}

export function authStateOf(query: ReturnType<typeof useMe>): AuthState {
  if (query.isPending) return { status: "loading" };
  if (query.isError) return { status: "unknown" };
  return query.data ? { status: "authenticated", user: query.data } : { status: "anonymous" };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const state = authStateOf(useMe());
  return <AuthContext value={state}>{children}</AuthContext>;
}

/** Full page loads (tests replace these with spies). */
export const pageNavigation = {
  replace(path: string): void {
    window.location.replace(path);
  },
};

/**
 * Sign out: end the session, clear the query cache, then load /login afresh.
 *
 * A full page load rather than a client-side navigation: nothing of the previous
 * user's state survives in memory, and the protected page never re-renders as
 * "signed out" (which would bounce through /login?next=…).
 */
export function useSignOut() {
  const queryClient = useQueryClient();
  return useCallback(async () => {
    try {
      await api.post("/auth/logout", undefined, { redirectOn401: false });
    } catch {
      // Already signed out (or unreachable): the local state is cleared either way.
    }
    await queryClient.cancelQueries();
    queryClient.clear();
    pageNavigation.replace("/login");
  }, [queryClient]);
}
