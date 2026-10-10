import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { mockFetch, problemResponse } from "@/__tests__/fixtures";

import { CHANGED_NOTICE, ChangePasswordPage } from "../ChangePasswordPage";

function renderPage() {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <ChangePasswordPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function fill(current: string, password: string, confirm = password) {
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: current } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: confirm } });
  fireEvent.click(screen.getByRole("button", { name: "Change password" }));
}

describe("ChangePasswordPage", () => {
  it("changes the password and says other devices were signed out", async () => {
    const fetch = mockFetch(new Response(null, { status: 204 }));
    renderPage();
    fill("old passphrase 123", "a brand new passphrase");
    expect(await screen.findByRole("status")).toHaveTextContent(CHANGED_NOTICE);
    const [url, init] = fetch.mock.calls[0] ?? [];
    expect((url as string)).toBe("/api/v1/auth/change-password");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({
      current_password: "old passphrase 123",
      new_password: "a brand new passphrase",
    });
    expect(screen.getByLabelText("Current password")).toHaveValue("");
  });

  it.each([
    [400, { code: "current_password_invalid" }, "Your current password is incorrect."],
    [422, { code: "password_unchanged" }, "Choose a password different from your current one."],
    [422, { code: "password_rejected", reasons: ["breached"] }, "This password is too common."],
  ])("explains a %s %o", async (status, body, message) => {
    mockFetch(problemResponse(status, { title: "x", ...body }));
    renderPage();
    fill("old passphrase 123", "a brand new passphrase");
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
  });

  it("checks the confirmation first", () => {
    const fetch = mockFetch();
    renderPage();
    fill("old passphrase 123", "a brand new passphrase", "different");
    expect(screen.getByRole("alert")).toHaveTextContent("The passwords don't match.");
    expect(fetch).not.toHaveBeenCalled();
  });
});
