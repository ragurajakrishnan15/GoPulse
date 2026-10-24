import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { PAGES, gotoReady, settled, watchErrors } from "./helpers";

for (const p of PAGES) {
  test.describe(`page ${p.path}`, () => {
    test("renders with data, synthetic label and no console errors", async ({ page }) => {
      const errors = watchErrors(page);
      await gotoReady(page, p.path);
      await expect(page.getByRole("heading", { level: 1 })).toContainText(p.h1);
      await expect(page.getByTestId("data-status")).toContainText("Synthetic demo data");
      await expect(page.getByTestId("data-status")).toContainText("Last refreshed");
      // (Next.js' own route announcer also has role=alert, so only look inside <main>.)
      await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
      expect(errors, errors.join("\n")).toEqual([]);
    });

    for (const scheme of ["light", "dark"] as const) {
      test(`has no axe WCAG A/AA violations (${scheme})`, async ({ page }) => {
        await page.emulateMedia({ colorScheme: scheme });
        await gotoReady(page, p.path);
        const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
        expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);
      });
    }

    test("has no horizontal page scroll on a phone", async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 800 });
      await gotoReady(page, p.path);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(1);
      await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
    });
  });
}

test("navigation reaches every view and marks the current page", async ({ page }) => {
  await gotoReady(page, "/");
  for (const [name, h1] of [["Pipeline ops", "Pipeline operations"], ["Data quality", "Data quality"], ["Product analytics", "Product analytics"],
    ["AI agents", "AI agent insights"], ["Data catalog", "Data catalog"], ["Overview", "Product overview"]]) {
    await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name }).click();
    await expect(page.getByRole("heading", { level: 1 }).first()).toContainText(h1);
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name })).toHaveAttribute("aria-current", "page");
  }
});

test("skip link is the first tab stop and moves focus to main content", async ({ page }) => {
  await gotoReady(page, "/");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to main content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#main$/);
});

test("overview shows every KPI with numerator/denominator and a definition", async ({ page }) => {
  await gotoReady(page, "/");
  for (const k of ["registered_users", "dau", "wau", "activation_rate", "conversion_rate", "retention_d7", "agent_success_rate", "p50_latency_ms", "p95_latency_ms", "est_cost_per_interaction_usd"]) {
    await expect(page.getByTestId(`kpi-${k}`)).toBeVisible();
  }
  const activation = page.getByTestId("kpi-activation_rate");
  await expect(activation).toContainText(/\d+(\.\d)?%/);
  await expect(activation).toContainText(/of \d+ eligible/);
  await activation.getByText("Definition").click();
  await expect(activation).toContainText("Eligibility");
  await expect(page.getByTestId("kpi-est_cost_per_interaction_usd")).toContainText("Estimate");
  await settled(page);
  // every chart shows its date range
  await expect(page.getByText(/Jan 1, 2026 – Mar 1, 2026 \(UTC\)/).first()).toBeVisible();
});

test("every chart has an accessible table alternative", async ({ page }) => {
  await gotoReady(page, "/");
  const card = page.getByRole("region", { name: "Daily and weekly active users" });
  await card.getByRole("button", { name: "View as table" }).click();
  await expect(card.getByRole("table", { name: /data table/ })).toBeVisible();
  await expect(card.getByRole("columnheader", { name: "DAU" })).toBeVisible();
  await card.getByRole("button", { name: "View as chart" }).click();
  await expect(card.getByRole("img", { name: /Daily and weekly active users/ })).toBeVisible();
});
