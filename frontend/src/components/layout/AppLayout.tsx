import { useState } from "react";
import { Outlet } from "react-router";

import type { AppRoute } from "@/routes";

import { Header } from "./Header";
import { SideNav } from "./SideNav";
import { MAIN_CONTENT_ID, SkipLink } from "./SkipLink";

export interface AppLayoutProps {
  routes: readonly AppRoute[];
}

/** Header, left navigation and the content area that route pages render into. */
export function AppLayout({ routes }: AppLayoutProps) {
  const [navOpen, setNavOpen] = useState(false);
  return (
    <div className="flex min-h-screen flex-col">
      <SkipLink />
      <Header
        navOpen={navOpen}
        onToggleNav={() => {
          setNavOpen((open) => !open);
        }}
      />
      <div className="flex flex-1">
        <SideNav
          routes={routes}
          open={navOpen}
          onNavigate={() => {
            setNavOpen(false);
          }}
        />
        <main id={MAIN_CONTENT_ID} tabIndex={-1} className="min-w-0 flex-1 p-6 focus:outline-none">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
