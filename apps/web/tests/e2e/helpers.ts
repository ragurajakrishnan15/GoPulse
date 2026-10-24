import { expect, type Page } from "@playwright/test";

export const PAGES = [
  { path: "/", h1: "Product overview" },
  { path: "/pipelines", h1: "Pipeline operations" },
  { path: "/quality", h1: "Data quality" },
  { path: "/analytics", h1: "Product analytics" },
  { path: "/agents", h1: "AI agent insights" },
  { path: "/catalog", h1: "Data catalog" },
  { path: "/catalog/fct_agent_interactions", h1: "fct_agent_interactions" },
];

/** Collects console errors / page errors so a test can assert the page is clean. */
export function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

/** Wait until no skeleton/loading status remains. */
export async function settled(page: Page) {
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 20_000 });
}

export async function gotoReady(page: Page, path: string) {
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  await settled(page);
}
