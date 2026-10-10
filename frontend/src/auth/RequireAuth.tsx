/**
 * The auth guard for `requiresAuth` routes (ADR-006, ADR-008).
 *
 * The current user comes from the `/auth/me` query that `AuthProvider` (Story 2.5)
 * puts into `AuthContext`. While it is `loading`, protected pages show a loading state
 * (no flash of protected content). `unknown` (no provider, or `/auth/me` failed for a
 * reason other than 401) renders the page: the server enforces access, and any 401
 * from the API client redirects to /login.
 */

import { createContext, use, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { LOGIN_PATH } from "@/api/client";
import type { Role } from "@/routes";
import { NotFoundPage } from "@/routes/not-found/NotFoundPage";

export interface CurrentUser {
  id: string;
  username: string;
  role: Role;
}

export type AuthState =
  | { status: "unknown" }
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: CurrentUser };

export const AuthContext = createContext<AuthState>({ status: "unknown" });

export function useAuth(): AuthState {
  return use(AuthContext);
}

export function loginPath(next: string): string {
  return `${LOGIN_PATH}?next=${encodeURIComponent(next)}`;
}

export interface RequireAuthProps {
  roles?: readonly Role[] | undefined;
  children: ReactNode;
}

export function RequireAuth({ roles, children }: RequireAuthProps) {
  const auth = useAuth();
  const location = useLocation();

  if (auth.status === "loading") {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading…
      </p>
    );
  }
  if (auth.status === "anonymous") {
    return <Navigate to={loginPath(`${location.pathname}${location.search}${location.hash}`)} replace />;
  }
  // Role-restricted pages look like they don't exist (404, never "forbidden").
  if (roles && (auth.status !== "authenticated" || !roles.includes(auth.user.role))) {
    return <NotFoundPage />;
  }
  return <>{children}</>;
}
