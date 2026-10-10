/**
 * Story 2.5 end to end, against a running backend (the dev API behind the preview
 * server's /api proxy): invite link -> set password -> sign in -> change password ->
 * sign out, with axe on every auth page and a keyboard-only sign-in.
 *
 * Needs a fresh invite link (e.g. from `python -m app.admin.bootstrap`):
 *
 *   E2E_INVITE_URL='https://<site>/set-password#t=<token>' E2E_USERNAME=<user> \
 *     npx playwright test e2e/auth.spec.ts
 *
 * Skipped when E2E_INVITE_URL isn't set (the shell tests run without a backend).
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const inviteUrl = process.env.E2E_INVITE_URL;
const username = process.env.E2E_USERNAME ?? "";
const FIRST = "first e2e passphrase 2026";
const SECOND = "second e2e passphrase 2026";

test.describe.configure({ mode: "serial" });
test.skip(!inviteUrl, "E2E_INVITE_URL not set (needs a running backend and an invite link)");

async function expectNoAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(results.violations).toEqual([]);
}

test("invite link -> set password -> sign in -> change password -> sign out", async ({ page }) => {
  const fragment = new URL(inviteUrl ?? "").hash;

  // Set password: the token is read from the fragment, then removed from the URL.
  await page.goto(`/set-password${fragment}`);
  await expect(page.getByRole("heading", { name: "Set your password" })).toBeVisible();
  expect(new URL(page.url()).hash).toBe("");
  await expectNoAxeViolations(page);
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("New password", { exact: true }).fill(FIRST);
  await page.getByLabel("Confirm new password").fill(FIRST);
  await page.getByRole("button", { name: "Set password" }).click();

  // Sign in, by keyboard only.
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("status")).toContainText("Your password is set");
  await expectNoAxeViolations(page);
  await page.getByLabel("Username").focus();
  await page.keyboard.type(username);
  await page.keyboard.press("Tab");
  await page.keyboard.type(FIRST);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main").getByRole("heading", { name: "Home" })).toBeVisible();
  await expect(page.getByRole("button", { name: `Account menu for ${username}` })).toBeVisible();

  // Nothing about the session in browser storage.
  const stored = await page.evaluate(() => [localStorage.length, sessionStorage.length]);
  expect(stored).toEqual([0, 0]);

  // Change password from the user menu.
  await page.getByRole("button", { name: `Account menu for ${username}` }).click();
  await page.getByRole("menuitem", { name: "Change password" }).click();
  await expect(page.getByRole("heading", { name: "Change password" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByLabel("Current password").fill(FIRST);
  await page.getByLabel("New password", { exact: true }).fill(SECOND);
  await page.getByLabel("Confirm new password").fill(SECOND);
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("status")).toContainText("Other devices were signed out");

  // Sign out: back to /login, and protected pages redirect there with next.
  await page.getByRole("button", { name: `Account menu for ${username}` }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/account/password");
  await expect(page).toHaveURL(/\/login\?next=%2Faccount%2Fpassword$/);

  // The new password works; the old one doesn't.
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(FIRST);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("Invalid username or password.");
  await page.getByLabel("Password").fill(SECOND);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/account\/password$/);
});

test("a used or bad link shows the invalid-link message", async ({ page }) => {
  await page.goto("/set-password#t=not-a-real-token");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("New password", { exact: true }).fill(FIRST);
  await page.getByLabel("Confirm new password").fill(FIRST);
  await page.getByRole("button", { name: "Set password" }).click();
  await expect(page.getByRole("alert")).toContainText("This link is invalid or expired");
});
