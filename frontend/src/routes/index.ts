/**
 * Route auto-discovery (epics.md shared-file conventions).
 *
 * Every `src/routes/<name>/route.tsx` exports `route: AppRoute` and is registered here
 * automatically, so feature stories add screens without editing a shared route table.
 */

import type { ReactNode } from "react";

/** User roles (ADR-023). */
export type Role = "user" | "admin" | "compliance";

export interface AppRoute {
  /** React Router path, e.g. `/spaces/:spaceId`. `*` is the 404 page. */
  path: string;
  element: ReactNode;
  /** Wrap in the auth guard (redirect to /login when signed out). */
  requiresAuth: boolean;
  /** Restrict to these roles; others see the 404 page, never "forbidden". */
  roles?: readonly Role[];
  /** `app` (default) renders inside the header + navigation layout; `bare` alone (login). */
  layout?: "app" | "bare";
  /** Adds a left-navigation entry; lower `order` first. */
  nav?: { label: string; order?: number };
}

export interface RouteModule {
  route?: AppRoute;
}

/** Validate and order discovered modules; throws on a missing export or duplicate path. */
export function collectRoutes(modules: Record<string, RouteModule>): AppRoute[] {
  const seen = new Map<string, string>();
  const routes: AppRoute[] = [];
  for (const [file, mod] of Object.entries(modules).sort(([a], [b]) => a.localeCompare(b))) {
    const { route } = mod;
    if (!route || typeof route.path !== "string") {
      throw new Error(`${file} must export \`route: AppRoute\``);
    }
    const other = seen.get(route.path);
    if (other) throw new Error(`Route path "${route.path}" is declared by both ${other} and ${file}`);
    seen.set(route.path, file);
    routes.push(route);
  }
  // The catch-all goes last so it never shadows a real path in navigation order.
  return routes.sort((a, b) => Number(a.path === "*") - Number(b.path === "*"));
}

/** Navigation entries for these roles; role-restricted ones are hidden when roles are unknown. */
export function navEntries(routes: readonly AppRoute[], roles?: readonly Role[]): AppRoute[] {
  return routes
    .filter((r) => r.nav && !r.path.includes(":") && r.path !== "*")
    .filter((r) => !r.roles || (roles?.some((role) => r.roles?.includes(role)) ?? false))
    .sort((a, b) => (a.nav?.order ?? 100) - (b.nav?.order ?? 100) || a.path.localeCompare(b.path));
}

const modules = import.meta.glob<RouteModule>(["./**/route.tsx", "!./**/__tests__/**"], {
  eager: true,
});

export const appRoutes: readonly AppRoute[] = collectRoutes(modules);
