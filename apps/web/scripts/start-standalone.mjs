// Run the production build the same way the Docker image does: `node .next/standalone/server.js`.
// (`next start` is not supported with `output: "standalone"`.) The standalone folder omits static assets and
// /public by design, so copy them next to it first.
import { cpSync, existsSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const standalone = path.join(root, ".next", "standalone");
if (!existsSync(path.join(standalone, "server.js"))) {
  console.error("No standalone build found. Run `npm run build` first.");
  process.exit(1);
}
cpSync(path.join(root, ".next", "static"), path.join(standalone, ".next", "static"), { recursive: true });
if (existsSync(path.join(root, "public"))) cpSync(path.join(root, "public"), path.join(standalone, "public"), { recursive: true });

const port = process.env.PORT ?? process.argv[2] ?? "3000";
const child = spawn(process.execPath, ["server.js"], {
  cwd: standalone,
  stdio: "inherit",
  env: { ...process.env, PORT: port, HOSTNAME: process.env.HOSTNAME ?? "0.0.0.0" },
});
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
child.on("exit", (code) => process.exit(code ?? 0));
