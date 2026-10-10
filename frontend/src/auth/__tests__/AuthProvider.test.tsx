import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { buildRouteObjects } from "@/App";
import { createQueryClient } from "@/api/queryClient";
import { queryKeys } from "@/api/queryKeys";
import { AuthProvider, pageNavigation } from "@/auth/AuthProvider";
import { jsonResponse, mockFetch, problemResponse } from "@/__tests__/fixtures";
import type { AppRoute } from "@/routes";

const routes: AppRoute[] = [
  { path: "/", element: <h1>Home page</h1>, requiresAuth: true, nav: { label: "Home" } },
  { path: "/secret", element: <h1>Secret page</h1>, requiresAuth: true },
  { path: "/login", element: <h1>Sign in page</h1>, requiresAuth: false, layout: "bare" },
];

function renderAt(path: string) {
  const queryClient = createQueryClient();
  const router = createMemoryRouter(buildRouteObjects(routes), { initialEntries: [path] });
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  );
  return { router, queryClient };
}

describe("AuthProvider", () => {
  it("redirects an anonymous user to /login with next", async () => {
    mockFetch(problemResponse(401, { code: "not_authenticated", title: "x" }));
    const { router } = renderAt("/secret?x=1");
    expect(await screen.findByText("Sign in page")).toBeInTheDocument();
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent("/secret?x=1")}`);
  });

  it("shows a loading state, never the protected page, while /auth/me is pending", () => {
    mockFetch();
    const pending = new Promise<Response>(() => undefined);
    globalThis.fetch = () => pending;
    renderAt("/secret");
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(screen.queryByText("Secret page")).toBeNull();
  });

  it("shows the username and signs out, clearing the cache", async () => {
    const fetch = mockFetch(
      jsonResponse({ id: "u1", username: "asha", role: "user" }),
      new Response(null, { status: 204 }),
    );
    const replace = vi.spyOn(pageNavigation, "replace").mockImplementation(() => undefined);
    const { queryClient } = renderAt("/");
    const menu = await screen.findByRole("button", { name: "Account menu for asha" });
    expect(screen.getByText("Home page")).toBeInTheDocument();
    queryClient.setQueryData(["other", "data"], { cached: true });

    fireEvent.pointerDown(menu, { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Sign out" }));

    await waitFor(() => {
      expect(replace).toHaveBeenCalledWith("/login");
    });
    const [url, init] = fetch.mock.calls[1] ?? [];
    expect((url as string)).toBe("/api/v1/auth/logout");
    expect(init?.method).toBe("POST");
    expect(queryClient.getQueryData(["other", "data"])).toBeUndefined();
    expect(queryClient.getQueryData(queryKeys.auth.me())).toBeUndefined();
  });

  it("renders pages when /auth/me fails for another reason (the server enforces)", async () => {
    mockFetch(problemResponse(500, { code: "internal_error", title: "x" }), problemResponse(500, { code: "internal_error", title: "x" }), problemResponse(500, { code: "internal_error", title: "x" }));
    renderAt("/secret");
    expect(await screen.findByText("Secret page", undefined, { timeout: 10_000 })).toBeInTheDocument();
  });
});
