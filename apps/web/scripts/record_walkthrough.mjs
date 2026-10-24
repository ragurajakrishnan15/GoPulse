// Record a captioned walkthrough of the REAL site (silent video; narration script in docs/walkthrough.md).
//
//   node scripts/record_walkthrough.mjs       # from apps/web; needs web :3000 + api :8000 with data published
//
// Output: docs/media/walkthrough.webm. Re-run it whenever the UI changes so the video never goes stale.
import { chromium } from "@playwright/test";
import { mkdirSync, readdirSync, renameSync, rmSync } from "node:fs";
import path from "node:path";

const BASE = process.env.WEB_URL ?? "http://localhost:3000";
const root = path.resolve(import.meta.dirname, "../../..");
const outDir = path.join(root, "docs", "media");
const tmp = path.join(outDir, "_rec");
rmSync(tmp, { recursive: true, force: true });
mkdirSync(tmp, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 }, recordVideo: { dir: tmp, size: { width: 1280, height: 720 } } });
const page = await ctx.newPage();

async function caption(text) {
  await page.evaluate((t) => {
    let el = document.getElementById("__cap");
    if (!el) {
      el = document.createElement("div");
      el.id = "__cap";
      el.style.cssText = "position:fixed;left:50%;bottom:18px;transform:translateX(-50%);max-width:1000px;z-index:99999;background:rgba(15,23,42,.94);color:#fff;" +
        "font:600 19px/1.35 system-ui,sans-serif;padding:12px 20px;border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,.35);text-align:center;pointer-events:none";
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
}
const settle = async () => {
  await page.waitForLoadState("networkidle");
  await page.waitForFunction(() => document.querySelectorAll('[aria-busy="true"]').length === 0, null, { timeout: 30000 });
  await page.waitForTimeout(500);
};
const go = async (url, cap, hold = 3500) => { await page.goto(BASE + url); await settle(); await caption(cap); await page.waitForTimeout(hold); };
const scroll = async (y, ms = 1800) => { await page.evaluate((v) => window.scrollTo({ top: v, behavior: "smooth" }), y); await page.waitForTimeout(ms); };

await go("/", "GoPulse: product analytics for an AI assistant, built on synthetic data (banner shows it, and how fresh it is)", 4500);
await page.getByTestId("kpi-activation_rate").getByText("Definition").click();
await caption("Every KPI shows its numerator, denominator and exact definition");
await page.waitForTimeout(3500);
await caption("Filters live in the URL: tier = Pro");
await page.getByLabel("Account tier", { exact: true }).selectOption("pro");
await page.waitForTimeout(2800);
await caption("Feature filter: cohort metrics say when a filter does not apply");
await page.getByLabel("Feature", { exact: true }).selectOption({ label: "Semantic Search" });
await page.waitForTimeout(3200);
await page.getByRole("button", { name: "Reset" }).click();
await settle();
await scroll(700);
await caption("Charts show their date range and definition, and have a table view");
await page.getByRole("region", { name: "Daily and weekly active users" }).getByRole("button", { name: "View as table" }).click();
await page.waitForTimeout(3000);

await go("/pipelines", "Pipeline operations: real runs are labelled, seeded examples are clearly marked", 4500);
await caption("Run the REAL pipeline on a small synthetic batch with ~40% bad records");
await page.getByLabel("Scenario").selectOption("heavy_defects");
await page.getByRole("button", { name: "Run demo pipeline" }).click();
await page.getByTestId("demo-result").waitFor({ timeout: 40000 });
await caption("Blocked by the quality gate: nothing was published");
await page.waitForTimeout(4500);
await scroll(520);
await page.getByLabel("Type").selectOption("seeded-example");
await page.getByLabel("Status").selectOption("failed");
await settle();
await caption("A failed (seeded example) run: tasks, error and log excerpt");
await page.getByRole("button", { name: /Details/ }).first().click();
await page.waitForTimeout(4500);

await go("/quality", "Data quality: what failed, how many records, and what happened to them", 4000);
await caption("Inspect the actual failing rows (synthetic)");
await page.getByRole("button", { name: /Inspect rows for Missing user id/ }).click();
await page.waitForTimeout(3800);
await scroll(900);
await caption("Failure demonstration: naive vs cleaned vs corrected rerun");
await page.waitForTimeout(5000);

await go("/analytics", "Funnel: each stage states its denominator; windows must be complete", 4500);
await scroll(120);
await caption("Retention cohorts: dashes mean the window has not elapsed (excluded, not zero)");
await page.waitForTimeout(4500);

await go("/agents", "AI agents: success, retries, P50/P95 latency, failure categories", 3800);
await caption("Cost is an estimate from documented token pricing, not vendor billing");
await page.waitForTimeout(3500);

await go("/catalog/fct_agent_interactions", "Data catalog: grain, key, columns, lineage and test status for every model", 4500);
await scroll(600);
await page.waitForTimeout(3000);

await ctx.close();
await browser.close();
const f = readdirSync(tmp).find((n) => n.endsWith(".webm"));
renameSync(path.join(tmp, f), path.join(outDir, "walkthrough.webm"));
rmSync(tmp, { recursive: true, force: true });
console.log("wrote", path.join(outDir, "walkthrough.webm"));
