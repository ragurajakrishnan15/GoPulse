// Capture REAL screenshots and page-load metrics from a running stack (web :3000 + api :8000 with data published).
//
//   node scripts/capture_media.mjs            # from apps/web
//
// Writes docs/media/screenshots/*.png and docs/benchmarks/web_vitals.json. Metrics are measured on localhost with no network
// or CPU throttling, cold cache per run, median of RUNS: they show the app's own cost, not what a visitor on a slow link sees.
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const BASE = process.env.WEB_URL ?? "http://localhost:3000";
const IS_LIVE = !/localhost|127\.0\.0\.1/.test(BASE);
const SCREENSHOTS = process.env.CAPTURE_SCREENSHOTS !== "0";
const root = path.resolve(import.meta.dirname, "../../..");
const shots = path.join(root, "docs", "media", "screenshots");
mkdirSync(shots, { recursive: true });
mkdirSync(path.join(root, "docs", "benchmarks"), { recursive: true });

const PAGES = [
  ["overview", "/"], ["pipelines", "/pipelines"], ["quality", "/quality"], ["analytics", "/analytics"], ["agents", "/agents"],
  ["catalog", "/catalog"], ["catalog-model", "/catalog/fct_agent_interactions"],
];
const RUNS = 5;

async function settle(page) {
  await page.waitForLoadState("networkidle");
  await page.waitForFunction(() => document.querySelectorAll('[aria-busy="true"]').length === 0, null, { timeout: 30000 });
  await page.waitForTimeout(600);
}

const browser = await chromium.launch();

// ---- screenshots -------------------------------------------------------------------------------------------------
if (SCREENSHOTS) for (const [name, url] of PAGES) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: "light" });
  const page = await ctx.newPage();
  await page.goto(BASE + url);
  await settle(page);
  await page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: true });
  await ctx.close();
}
if (SCREENSHOTS) for (const [name, url] of [["overview", "/"], ["quality", "/quality"]]) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: "dark" });
  const page = await ctx.newPage();
  await page.goto(BASE + url);
  await settle(page);
  await page.screenshot({ path: path.join(shots, `${name}-dark.png`), fullPage: true });
  await ctx.close();
}
if (SCREENSHOTS) for (const [name, url] of [["overview", "/"], ["pipelines", "/pipelines"], ["analytics", "/analytics"]]) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  await page.goto(BASE + url);
  await settle(page);
  await page.screenshot({ path: path.join(shots, `${name}-mobile.png`), fullPage: true });
  await ctx.close();
}

// ---- page-load metrics -------------------------------------------------------------------------------------------
const median = (xs) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
const vitals = [];
for (const [name, url] of PAGES) {
  const runs = [];
  for (let i = 0; i < RUNS; i++) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    await page.addInitScript(() => {
      window.__m = { lcp: 0, cls: 0 };
      new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__m.lcp = e.startTime; }).observe({ type: "largest-contentful-paint", buffered: true });
      new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__m.cls += e.value; }).observe({ type: "layout-shift", buffered: true });
    });
    await page.goto(BASE + url);
    await settle(page);
    const m = await page.evaluate(() => {
      const nav = performance.getEntriesByType("navigation")[0];
      const fcp = performance.getEntriesByName("first-contentful-paint")[0]?.startTime ?? 0;
      const js = performance.getEntriesByType("resource").filter((r) => r.name.endsWith(".js") || r.initiatorType === "script" || r.name.includes("/_next/static"));
      return { lcp: window.__m.lcp, cls: window.__m.cls, fcp, ttfb: nav.responseStart, domContentLoaded: nav.domContentLoadedEventEnd,
        jsKB: js.reduce((s, r) => s + (r.encodedBodySize || 0), 0) / 1024, requests: performance.getEntriesByType("resource").length };
    });
    runs.push(m);
    await ctx.close();
  }
  const pick = (k) => median(runs.map((r) => r[k]));
  vitals.push({ page: name, url, runs: RUNS, lcp_ms: Math.round(pick("lcp")), fcp_ms: Math.round(pick("fcp")), ttfb_ms: Math.round(pick("ttfb")),
    cls: +pick("cls").toFixed(4), js_kb_encoded: Math.round(pick("jsKB")), requests: pick("requests") });
  console.log(vitals.at(-1));
}
writeFileSync(path.join(root, "docs", "benchmarks", IS_LIVE ? "web_vitals_live.json" : "web_vitals.json"), JSON.stringify({ base: BASE, throttling: IS_LIVE ? "none on the client; real network to the deployed site" : "none (localhost)", browser: browser.version(), results: vitals }, null, 2));
await browser.close();
console.log("screenshots ->", shots);
