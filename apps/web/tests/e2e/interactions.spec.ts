import { expect, test } from "@playwright/test";
import { gotoReady, settled } from "./helpers";

const kpiValue = async (page: import("@playwright/test").Page, key: string) =>
  (await page.getByTestId(`kpi-${key}`).locator("p").first().innerText()).trim();

test.describe("filters", () => {
  test("update the URL and every KPI/chart, and Reset restores the full view", async ({ page }) => {
    await gotoReady(page, "/");
    const all = await kpiValue(page, "registered_users");
    await page.getByLabel("Account tier", { exact: true }).selectOption("pro");
    await expect(page).toHaveURL(/tier=pro/);
    await expect.poll(async () => Number((await kpiValue(page, "registered_users")).replace(/,/g, ""))).toBeLessThan(Number(all.replace(/,/g, "")));
    await expect(page.getByText("Showing: tier pro.")).toBeVisible();

    await page.getByLabel("Current plan", { exact: true }).selectOption("pro");
    await expect(page).toHaveURL(/plan=pro/);
    await page.getByRole("button", { name: "Reset" }).click();
    await expect(page).not.toHaveURL(/tier=|plan=/);
    await expect.poll(() => kpiValue(page, "registered_users")).toBe(all);
  });

  test("are restored from a shared URL", async ({ page }) => {
    await gotoReady(page, "/?segment=smb&start=2026-02-01&end=2026-02-10");
    await expect(page.getByLabel("Account segment", { exact: true })).toHaveValue("smb");
    await expect(page.getByLabel("Start date (UTC)")).toHaveValue("2026-02-01");
    await expect(page.getByText(/Feb 1, 2026 – Feb 10, 2026 \(UTC\)/).first()).toBeVisible();
  });

  test("feature filter narrows activity and tells the user which KPIs it does not apply to", async ({ page }) => {
    await gotoReady(page, "/");
    const dauAll = Number((await kpiValue(page, "dau")).replace(/,/g, ""));
    await page.getByLabel("Feature", { exact: true }).selectOption({ label: "Semantic Search" });
    await expect(page.getByTestId("kpi-activation_rate")).toContainText("Not filtered by: feature");
    await expect.poll(async () => Number((await kpiValue(page, "dau")).replace(/,/g, ""))).toBeLessThan(dauAll);
  });

  test("keyboard users can change a filter without a mouse", async ({ page }) => {
    await gotoReady(page, "/");
    await page.getByLabel("Account tier", { exact: true }).focus();
    await page.keyboard.press("ArrowDown");
    await expect(page).toHaveURL(/tier=/);
  });

  test("an inapplicable combination shows the API's message and recovers", async ({ page }) => {
    await gotoReady(page, "/?start=2026-03-01&end=2026-01-01");
    const alert = page.locator("main").getByRole("alert").first();
    await expect(alert).toContainText(/start must be on or before end|Invalid/i);
    await page.getByRole("button", { name: "Reset" }).click();
    await settled(page);
    await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
    await expect(page.getByTestId("kpi-dau")).toBeVisible();
  });
});

test.describe("loading, empty, error and stale states", () => {
  test("shows a loading skeleton while the API is slow", async ({ page }) => {
    await page.route("**/api/v1/overview**", async (route) => { await new Promise((r) => setTimeout(r, 1500)); await route.continue(); });
    await page.goto("/");
    await expect(page.getByRole("status", { name: /loading headline metrics/i })).toBeVisible();
    await expect(page.getByTestId("kpi-dau")).toBeVisible();
  });

  test("shows an error with Retry when the API is unreachable, then recovers", async ({ page }) => {
    let fail = true;
    await page.route("**/api/v1/overview**", (route) => (fail ? route.abort("connectionrefused") : route.continue()));
    await page.goto("/");
    const alert = page.locator("main").getByRole("alert").first();
    await expect(alert).toContainText("unreachable");
    fail = false;
    await alert.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByTestId("kpi-dau")).toBeVisible();
  });

  test("shows an empty state when nothing matches", async ({ page }) => {
    await gotoReady(page, "/agents?start=2030-01-01&end=2030-01-05");
    await expect(page.getByText("No agent executions match these filters.")).toBeVisible();
  });

  test("keeps showing the last data and flags an error when a refresh fails", async ({ page }) => {
    await gotoReady(page, "/");
    await page.route("**/api/v1/overview**", (route) => route.fulfill({ status: 500, json: { error: { code: 500, message: "boom" } } }));
    await page.getByLabel("Account tier", { exact: true }).selectOption("team");
    const alert = page.locator("main").getByRole("alert").first();
    await expect(alert).toContainText("Showing the last loaded data");
    await expect(page.getByTestId("kpi-dau")).toBeVisible();
    await expect(alert).not.toContainText("boom"); // internals are not leaked
  });

  test("flags a stale snapshot", async ({ page }) => {
    const old = new Date(Date.now() - 3 * 86_400_000).toISOString();
    await page.route("**/api/v1/features", (route) => route.fulfill({ json: { items: [], meta: { synthetic_data: true, last_refreshed: old, as_of_date: "2026-03-01" } } }));
    await page.goto("/");
    await expect(page.getByTestId("data-status")).toContainText("Stale");
    await expect(page.getByTestId("data-status")).toContainText("3 days ago");
  });
});

test.describe("pipeline operations", () => {
  test("distinguishes real executions from seeded example history", async ({ page }) => {
    await gotoReady(page, "/pipelines");
    await expect(page.getByText("Seeded example runs", { exact: true })).toBeVisible();
    await page.getByLabel("Type").selectOption("seeded-example");
    await settled(page);
    const rows = page.getByRole("table", { name: /Pipeline run history/ }).locator("tbody tr");
    await expect(rows).toHaveCount(10);
    for (const t of await rows.locator("td:nth-child(2)").allInnerTexts()) expect(t).toContain("Seeded example");
    await page.getByLabel("Type").selectOption("local-real");
    await settled(page);
    for (const t of await rows.locator("td:nth-child(2)").allInnerTexts()) expect(t).toMatch(/Real/);
  });

  test("paginates and expands a failed seeded run with tasks, error and log excerpt", async ({ page }) => {
    await gotoReady(page, "/pipelines");
    await page.getByLabel("Type").selectOption("seeded-example");
    await page.getByLabel("Status").selectOption("failed");
    await settled(page);
    const first = page.getByRole("table", { name: /Pipeline run history/ }).locator("tbody tr").first();
    await expect(first).toContainText("failed");
    await expect(first).toContainText("QualityGateError");
    await first.getByRole("button", { name: /Details/ }).click();
    await expect(page.getByRole("table", { name: /Tasks for run seed_/ })).toBeVisible();
    await expect(page.getByText("Log excerpt")).toBeVisible();
    await page.getByLabel("Status").selectOption("");
    await settled(page);
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect(page.getByText(/Page 2/)).toBeVisible();
  });

  test("runs the bounded demo workflow, shows the real result and records it in history", async ({ page }) => {
    await gotoReady(page, "/pipelines");
    await page.getByLabel("Scenario").selectOption("heavy_defects");
    await page.getByLabel(/Seed/).fill("11");
    await page.getByRole("button", { name: "Run demo pipeline" }).click();
    const result = page.getByTestId("demo-result");
    await expect(result).toBeVisible({ timeout: 30_000 });
    await expect(result).toContainText("Real · demo run");
    await expect(result).toContainText("failed");
    await expect(result).toContainText("quarantine_rate_blocking");
    await expect(result).toContainText("nothing was published");
    await page.getByLabel("Type").selectOption("demo-live");
    await settled(page);
    await expect(page.getByRole("table", { name: /Pipeline run history/ }).locator("tbody tr").first()).toContainText("demo_");
  });

  test("rejects an invalid seed on the client and shows a rate-limit message from the server", async ({ page }) => {
    await gotoReady(page, "/pipelines");
    await page.getByLabel(/Seed/).fill("abc");
    await expect(page.getByRole("button", { name: "Run demo pipeline" })).toBeDisabled();
    await page.getByLabel(/Seed/).fill("5");
    await page.route("**/api/v1/demo/run", (route) =>
      route.fulfill({ status: 429, headers: { "retry-after": "42", "access-control-allow-origin": "*", "access-control-expose-headers": "retry-after" }, json: { error: { code: 429, message: "Demo run rate limit reached. Please try again later." } } }));
    await page.getByRole("button", { name: "Run demo pipeline" }).click();
    await expect(page.locator("main").getByRole("alert").first()).toContainText("rate limit reached");
    await expect(page.locator("main").getByRole("alert").first()).toContainText("42s");
  });
});

test.describe("data quality", () => {
  test("lists failed checks with remediation and lets users inspect the failed rows", async ({ page }) => {
    await gotoReady(page, "/quality");
    const table = page.getByRole("table", { name: "Data quality check results" });
    await expect(table).toContainText("Missing user id");
    await expect(table).toContainText("Quarantined");
    await expect(table).toContainText("Deduplicated");
    await page.getByRole("button", { name: /Inspect rows for Missing user id/ }).click();
    const samples = page.getByTestId("samples");
    await expect(samples).toContainText("missing_user_id");
    await expect(samples.locator("pre").first()).toContainText('"user_id": null');
    await samples.getByRole("button", { name: "Close" }).click();
    await expect(samples).toHaveCount(0);
  });

  test("filters checks by layer and result", async ({ page }) => {
    await gotoReady(page, "/quality");
    await page.getByLabel("Result", { exact: true }).selectOption("fail");
    const table = page.getByRole("table", { name: "Data quality check results" });
    await expect.poll(async () => (await table.locator("tbody tr td:nth-child(4)").allInnerTexts()).every((t) => t.includes("fail"))).toBe(true);
    expect(await table.locator("tbody tr").count()).toBeGreaterThan(3);
    await page.getByLabel("Layer", { exact: true }).selectOption("dbt");
    await settled(page);
    await expect(page.getByText("No data for the current selection.").or(page.getByRole("table", { name: "Data quality check results" }))).toBeVisible();
  });

  test("explains the failure demo: what failed, how many, quarantined/blocked, rerun, downstream change", async ({ page }) => {
    await gotoReady(page, "/quality");
    await expect(page.getByRole("heading", { name: "1. Batch with ~3% bad records" })).toBeVisible();
    await expect(page.getByText(/blocked by/)).toContainText("quarantine_rate_blocking");
    await expect(page.getByText("match the clean reference exactly")).toBeVisible();
    const t = page.getByRole("table", { name: "Downstream metrics before and after cleaning" });
    await expect(t.getByRole("columnheader")).toHaveCount(5);
    await expect(t.getByRole("row", { name: /Events counted/ })).toContainText("29,180");
  });
});

test.describe("product analytics", () => {
  test("shows funnel stages with distinct denominators and a retention table with unobservable cells", async ({ page }) => {
    await gotoReady(page, "/analytics");
    await expect(page.getByTestId("stage-signed_up")).toContainText("100.0%");
    const act = page.getByTestId("stage-activated_7d");
    await expect(act).toContainText(/of \d+\)/);
    await expect(act).toContainText("7-day window has fully elapsed");
    await expect(page.getByTestId("stage-converted_30d")).toContainText("30-day window");
    const ret = page.getByRole("table", { name: /Retention by signup week cohort/ });
    await expect(ret.getByLabel("not yet observable").first()).toBeVisible();
    await expect(ret.locator("td", { hasText: "%" }).first()).toHaveAttribute("title", /of \d+ eligible users/);
  });

  test("funnel responds to filters and hides the feature filter where it does not apply", async ({ page }) => {
    await gotoReady(page, "/analytics");
    await expect(page.getByLabel("Feature", { exact: true })).toHaveCount(0);
    const before = await page.getByTestId("stage-signed_up").innerText();
    await page.getByLabel("Account tier", { exact: true }).selectOption("enterprise");
    await expect.poll(() => page.getByTestId("stage-signed_up").innerText()).not.toBe(before);
  });
});

test.describe("catalog", () => {
  test("searches, opens a model and follows lineage links", async ({ page }) => {
    await gotoReady(page, "/catalog");
    await page.getByLabel("Search").fill("retention");
    const table = page.getByRole("table", { name: "Data catalog models" });
    await expect(table).toContainText("agg_retention_cohorts");
    await expect(table).not.toContainText("dim_users");
    await page.getByLabel("Search").fill("no-such-model-xyz");
    await expect(page.getByText("No models match your search.")).toBeVisible();
    await page.getByLabel("Search").fill("");
    await table.getByRole("link", { name: "fct_agent_interactions" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("fct_agent_interactions");
    await expect(page.getByText("One row per execution_id").filter({ visible: true })).toBeVisible();
    await expect(page.getByRole("table", { name: "Columns of fct_agent_interactions" })).toContainText("duration_ms");
    await expect(page.getByRole("table", { name: /Tests on fct_agent_interactions/ })).toContainText("Dbt");
    await page.getByRole("link", { name: "silver_events" }).first().click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("silver_events");
    await expect(page.getByRole("link", { name: "fct_agent_interactions" }).filter({ visible: true }).first()).toBeVisible(); // downstream
  });

  test("lists metric definitions with numerator, denominator and eligibility", async ({ page }) => {
    await gotoReady(page, "/catalog");
    const t = page.getByRole("table", { name: "Metric definitions" });
    await expect(t.getByRole("row", { name: /Activation rate/ })).toContainText("users in the signup cohort");
    await expect(t.getByRole("row", { name: /Estimated cost per interaction/ })).toContainText("ESTIMATE");
  });

  test("unknown model shows a not-found message", async ({ page }) => {
    await page.goto("/catalog/does_not_exist");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Model not found");
    await expect(page.locator("main").getByRole("alert").first()).toContainText("does_not_exist");
  });
});
