import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { AuthContext, RequireAuth, type AuthState } from "@/auth/RequireAuth";
import type { Role } from "@/routes";

function renderAt(auth: AuthState, roles?: readonly Role[]) {
  const router = createMemoryRouter(
    [
      { path: "/secret", element: <RequireAuth roles={roles}>secret page</RequireAuth> },
      { path: "/login", element: <LoginProbe /> },
    ],
    { initialEntries: ["/secret?tab=2"] },
  );
  render(
    <AuthContext value={auth}>
      <RouterProvider router={router} />
    </AuthContext>,
  );
  return router;
}

function LoginProbe() {
  return <p>login page</p>;
}

const user = (role: Role): AuthState => ({
  status: "authenticated",
  user: { id: "u1", username: "asha", role },
});

describe("RequireAuth", () => {
  it("renders the page while auth is unknown (server enforces, 401 redirects)", () => {
    renderAt({ status: "unknown" });
    expect(screen.getByText("secret page")).toBeInTheDocument();
  });

  it("redirects anonymous users to /login with the return path", () => {
    const router = renderAt({ status: "anonymous" });
    expect(screen.getByText("login page")).toBeInTheDocument();
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent("/secret?tab=2")}`);
  });

  it("renders for an allowed role", () => {
    renderAt(user("compliance"), ["compliance"]);
    expect(screen.getByText("secret page")).toBeInTheDocument();
  });

  it("shows 404, not forbidden, for other roles", () => {
    renderAt(user("admin"), ["compliance"]);
    expect(screen.queryByText("secret page")).toBeNull();
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/forbidden/i);
  });
});
