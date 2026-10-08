import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { buildRouteObjects } from "@/App";
import type { AppRoute } from "@/routes";

const routes: AppRoute[] = [
  { path: "/", element: <h1>Home page</h1>, requiresAuth: true, nav: { label: "Home", order: 0 } },
  { path: "/demo", element: <h1>Demo page</h1>, requiresAuth: true, nav: { label: "Demo", order: 1 } },
  { path: "/login", element: <h1>Sign in</h1>, requiresAuth: false, layout: "bare" },
  { path: "*", element: <h1>Page not found</h1>, requiresAuth: false },
];

function renderAt(path: string) {
  const router = createMemoryRouter(buildRouteObjects(routes), { initialEntries: [path] });
  render(<RouterProvider router={router} />);
}

describe("app shell", () => {
  it("renders header, navigation and the page in the content area", () => {
    renderAt("/demo");
    expect(screen.getByRole("banner")).toHaveTextContent("invest-ai-llm");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toHaveTextContent("Home");
    expect(screen.getByRole("link", { name: "Demo" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("main")).toHaveTextContent("Demo page");
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main-content");
  });

  it("renders bare routes without the layout", () => {
    renderAt("/login");
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("renders the 404 page for unknown paths inside the layout", () => {
    renderAt("/nope");
    expect(screen.getByRole("main")).toHaveTextContent("Page not found");
  });
});
