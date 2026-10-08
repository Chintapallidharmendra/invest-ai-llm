import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("the shell loads with header, navigation and content", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("banner")).toContainText("invest-ai-llm");
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
  await expect(page.getByRole("main").getByRole("heading", { name: "Home" })).toBeVisible();
});

test("an unknown route shows the 404 page", async ({ page }) => {
  await page.goto("/no/such/page");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await page.getByRole("link", { name: "Go to home" }).click();
  await expect(page).toHaveURL(/\/$/);
});

test("the skip link is the first focus stop and is visible on focus", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
});

for (const path of ["/", "/no/such/page"]) {
  test(`no axe violations (WCAG 2.1 AA) on ${path}`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole("main")).toBeVisible();
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations).toEqual([]);
  });
}
