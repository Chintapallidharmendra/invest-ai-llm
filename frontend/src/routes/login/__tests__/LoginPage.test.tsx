import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { queryKeys } from "@/api/queryKeys";
import { AuthProvider } from "@/auth/AuthProvider";
import { jsonResponse, mockFetch, problemResponse } from "@/__tests__/fixtures";

import { GENERIC_ERROR, LoginPage } from "../LoginPage";

const ME = { id: "u1", username: "asha", role: "user" };

function renderLogin(path = "/login") {
  const queryClient = createQueryClient();
  const router = createMemoryRouter(
    [
      { path: "/login", element: <LoginPage /> },
      { path: "/", element: <p>home page</p> },
      { path: "/spaces/:id", element: <p>space page</p> },
    ],
    { initialEntries: [path] },
  );
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  );
  return { router, queryClient };
}

function submit(username = "asha", password = "a long passphrase 12") {
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: username } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

const anonymous = () => problemResponse(401, { code: "not_authenticated", title: "Not signed in" });

afterEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
});

describe("LoginPage", () => {
  it("signs in and goes to a safe next path", async () => {
    const fetch = mockFetch(anonymous(), jsonResponse(ME));
    const { router, queryClient } = renderLogin("/login?next=%2Fspaces%2F7");
    await screen.findByLabelText("Username");
    submit();
    await screen.findByText("space page");
    expect(router.state.location.pathname).toBe("/spaces/7");
    const [, init] = fetch.mock.calls[1] ?? [];
    expect(JSON.parse(init?.body as string)).toEqual({ username: "asha", password: "a long passphrase 12" });
    expect(queryClient.getQueryData(queryKeys.auth.me())).toEqual(ME);
    // Nothing about the session is kept in browser storage.
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("ignores an unsafe next and goes home", async () => {
    mockFetch(anonymous(), jsonResponse(ME));
    const { router } = renderLogin("/login?next=%2F%2Fevil.com");
    await screen.findByLabelText("Username");
    submit();
    await screen.findByText("home page");
    expect(router.state.location.pathname).toBe("/");
  });

  it.each([
    [401, "invalid_credentials"],
    [503, "rate_limit_unavailable"],
    [500, "internal_error"],
  ])("shows one generic error for a %s", async (status, code) => {
    mockFetch(anonymous(), problemResponse(status, { code, title: "x" }));
    renderLogin();
    await screen.findByLabelText("Username");
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent(GENERIC_ERROR);
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("shows the rate-limit message for a 429", async () => {
    mockFetch(
      anonymous(),
      problemResponse(429, { code: "rate_limited", title: "Too many", retry_after_s: 90 }, { "Retry-After": "90" }),
    );
    renderLogin();
    await screen.findByLabelText("Username");
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many sign-in attempts. Try again in 2 minutes.",
    );
  });

  it("tells users to contact their administrator for a forgotten password", async () => {
    mockFetch(anonymous());
    renderLogin();
    expect(await screen.findByText("Forgot password? Contact your administrator.")).toBeInTheDocument();
  });

  it("sends a signed-in user straight on", async () => {
    mockFetch(jsonResponse(ME));
    const { router } = renderLogin("/login?next=%2Fspaces%2F1");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/spaces/1");
    });
  });
});
