import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { createBrowserRouter, type RouteObject } from "react-router";
import { RouterProvider } from "react-router/dom";

import { LOGIN_PATH, setUnauthorizedHandler } from "@/api/client";
import { queryClient as defaultQueryClient } from "@/api/queryClient";
import { loginPath, RequireAuth } from "@/auth/RequireAuth";
import { AppLayout } from "@/components/layout/AppLayout";
import { RouteError } from "@/components/layout/RouteError";
import { Toaster } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { appRoutes, type AppRoute } from "@/routes";

function toRouteObject(route: AppRoute): RouteObject {
  const element = route.requiresAuth ? (
    <RequireAuth roles={route.roles}>{route.element}</RequireAuth>
  ) : (
    route.element
  );
  return { path: route.path, element };
}

/** Bare routes (login) render alone; the rest render inside the app layout. */
export function buildRouteObjects(routes: readonly AppRoute[]): RouteObject[] {
  const bare = routes.filter((r) => r.layout === "bare");
  const inLayout = routes.filter((r) => r.layout !== "bare");
  return [
    ...bare.map((r) => ({ ...toRouteObject(r), errorElement: <RouteError /> })),
    {
      element: <AppLayout routes={routes} />,
      errorElement: <RouteError />,
      children: inLayout.map(toRouteObject),
    },
  ];
}

export interface AppProps {
  routes?: readonly AppRoute[];
  queryClient?: QueryClient;
}

export function App({ routes = appRoutes, queryClient = defaultQueryClient }: AppProps) {
  const router = useMemo(() => createBrowserRouter(buildRouteObjects(routes)), [routes]);

  // A 401 anywhere drops cached server state and goes to /login without a reload.
  useEffect(() => {
    const previous = setUnauthorizedHandler(() => {
      const { pathname, search, hash } = router.state.location;
      if (pathname === LOGIN_PATH) return;
      queryClient.clear();
      void router.navigate(loginPath(`${pathname}${search}${hash}`), { replace: true });
    });
    return () => {
      setUnauthorizedHandler(previous);
    };
  }, [router, queryClient]);

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={300}>
        <Toaster>
          <RouterProvider router={router} />
        </Toaster>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
