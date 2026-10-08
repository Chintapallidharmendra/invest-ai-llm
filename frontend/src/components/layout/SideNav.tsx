import { NavLink } from "react-router";

import { useAuth } from "@/auth/RequireAuth";
import { cn } from "@/lib/utils";
import { navEntries, type AppRoute } from "@/routes";

export interface SideNavProps {
  routes: readonly AppRoute[];
  open: boolean;
  onNavigate: () => void;
}

export function SideNav({ routes, open, onNavigate }: SideNavProps) {
  const auth = useAuth();
  const roles = auth.status === "authenticated" ? [auth.user.role] : undefined;
  const entries = navEntries(routes, roles);

  return (
    <nav
      id="app-navigation"
      aria-label="Main"
      className={cn(
        "w-56 shrink-0 border-r bg-muted p-3 md:block",
        open ? "block" : "hidden",
      )}
    >
      <ul className="space-y-1">
        {entries.map((r) => (
          <li key={r.path}>
            <NavLink
              to={r.path}
              end={r.path === "/"}
              onClick={onNavigate}
              className={({ isActive }) =>
                cn(
                  "block rounded-md px-3 py-2 text-sm font-medium hover:bg-accent",
                  isActive ? "bg-background text-foreground shadow-sm" : "text-muted-foreground",
                )
              }
            >
              {r.nav?.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
