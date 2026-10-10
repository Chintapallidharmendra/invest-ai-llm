import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { mockFetch, problemResponse } from "@/__tests__/fixtures";

import { reasonMessages } from "../passwordPolicy";
import { INVALID_LINK, SetPasswordPage, tokenFromHash } from "../SetPasswordPage";

function LoginProbe() {
  const location = useLocation();
  return <p>login page: {(location.state as { notice?: string } | null)?.notice}</p>;
}

function renderAt(url: string) {
  window.history.replaceState(null, "", url);
  const router = createMemoryRouter(
    [
      { path: "/set-password", element: <SetPasswordPage /> },
      { path: "/login", element: <LoginProbe /> },
    ],
    { initialEntries: ["/set-password"] },
  );
  render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

function fill(username: string, password: string, confirm = password) {
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: username } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: confirm } });
  fireEvent.click(screen.getByRole("button", { name: "Set password" }));
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("tokenFromHash", () => {
  it("reads t from the fragment only", () => {
    expect(tokenFromHash("#t=abc")).toBe("abc");
    expect(tokenFromHash("#x=1&t=abc")).toBe("abc");
    expect(tokenFromHash("")).toBeNull();
    expect(tokenFromHash("#t=")).toBeNull();
  });
});

describe("SetPasswordPage", () => {
  it("reads the token from the fragment, removes it, and sets the password", async () => {
    const fetch = mockFetch(new Response(null, { status: 204 }));
    renderAt("/set-password#t=secret-token");
    expect(window.location.hash).toBe("");
    expect(window.location.href).not.toContain("secret-token");
    expect(screen.getByText(/at least 12 characters, and not a common password/i)).toBeInTheDocument();
    fill("asha", "a long new passphrase");
    expect(await screen.findByText(/login page: Your password is set/)).toBeInTheDocument();
    const [url, init] = fetch.mock.calls[0] ?? [];
    expect((url as string)).toBe("/api/v1/auth/set-password");
    expect((url as string)).not.toContain("secret-token");
    expect(JSON.parse(init?.body as string)).toEqual({
      token: "secret-token",
      username: "asha",
      new_password: "a long new passphrase",
    });
  });

  it("ignores a token in the query string", () => {
    renderAt("/set-password?t=from-query");
    expect(screen.getByRole("alert")).toHaveTextContent(INVALID_LINK);
    expect(screen.queryByLabelText("Username")).toBeNull();
  });

  it("shows the invalid-link message for a 400", async () => {
    mockFetch(problemResponse(400, { code: "link_invalid", title: "x" }));
    renderAt("/set-password#t=old");
    fill("asha", "a long new passphrase");
    expect(await screen.findByRole("alert")).toHaveTextContent(INVALID_LINK);
  });

  it("maps every policy reason to a plain message", async () => {
    mockFetch(
      problemResponse(422, {
        code: "password_rejected",
        title: "x",
        reasons: ["too_short", "breached", "contains_username"],
      }),
    );
    renderAt("/set-password#t=tok");
    fill("asha", "asha123", "asha123");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Use at least 12 characters.");
    expect(alert).toHaveTextContent("This password is too common.");
    expect(alert).toHaveTextContent("Don't include your username in the password.");
  });

  it("checks the confirmation before sending anything", () => {
    const fetch = mockFetch();
    renderAt("/set-password#t=tok");
    fill("asha", "a long new passphrase", "something else entirely");
    expect(screen.getByRole("alert")).toHaveTextContent("The passwords don't match.");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("falls back for unknown reasons", () => {
    expect(reasonMessages(["too_long", "new_rule"])).toEqual([
      "Use at most 1024 characters.",
      "Choose a stronger password.",
    ]);
    expect(reasonMessages(undefined)).toEqual(["Choose a stronger password."]);
  });
});
