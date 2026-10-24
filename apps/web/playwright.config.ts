import { defineConfig, devices } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const root = path.resolve(__dirname, "../..");

/** Local dev: load the git-ignored .env so the API can reach the Postgres container. CI provides real env vars. */
function loadEnv(): Record<string, string> {
  const f = path.join(root, ".env");
  const out: Record<string, string> = {};
  if (existsSync(f)) {
    for (const line of readFileSync(f, "utf8").split(/\r?\n/)) {
      const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
      if (m) out[m[1]] = m[2];
    }
  }
  return out;
}

// GOPULSE_PYTHON overrides; otherwise prefer the repo's virtualenv, falling back to `python` on PATH (CI).
const venvPython = process.platform === "win32" ? path.join(root, ".venv", "Scripts", "python.exe") : path.join(root, ".venv", "bin", "python");
const python = process.env.GOPULSE_PYTHON ?? (existsSync(venvPython) ? venvPython : "python");

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  // E2E_BASE_URL points the suite at an already-deployed site (no local servers are started).
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.E2E_BASE_URL ? undefined : [
    {
      // API with relaxed demo limits so the suite can run the demo workflow repeatedly.
      command: `"${python}" -m uvicorn services.api.app.main:app --port 8000`,
      cwd: root,
      url: "http://localhost:8000/health",
      reuseExistingServer: true,
      timeout: 60_000,
      env: { ...loadEnv(), ...(process.env as Record<string, string>), DEMO_RUNS_PER_MINUTE: "50", DEMO_RUNS_PER_HOUR: "500" },
    },
    {
      command: process.env.CI ? "npm run build && npm run start:standalone" : "npm run dev -- --port 3000",
      url: "http://localhost:3000",
      reuseExistingServer: true,
      timeout: 240_000,
    },
  ],
});
