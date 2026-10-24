# Deployment (Phase 8)

**Live:** site https://gopulse-web-nu.vercel.app · API https://gopulse-api.vercel.app (docs at `/docs`) · all synthetic data, free tiers.

```
Browser ──HTTPS──▶ Vercel: Next.js site            (project gopulse-web, region iad1)
   │                      │ fetch (CORS allow-list)
   └──────HTTPS───────────▶ Vercel: FastAPI as a Python function   (project gopulse-api, Python 3.12, Fluid compute)
                                  │ transaction pooler (port 6543), IPv4
                                  ▼
                           Supabase PostgreSQL 17 (us-east-1)  schema `serving`
                                  ▲
        pipeline + dbt + publish (run from a developer machine / CI with the OWNER credential; never from the web)
```

## Where this differs from the original plan - and why
The plan said Vercel for the site, **Render** (a Docker host) for the API, a managed Postgres. The API runs as a **Vercel Python function**
instead, which was chosen so the whole thing can be deployed and re-deployed from one CLI without another account. The cost of that choice, and what was
done about it:

| Consequence of serverless | What the code does |
|---|---|
| Many short-lived instances → an in-memory rate limiter would not limit anything | the demo-run limiter is in PostgreSQL (`serving.demo_hits`), serialised by an advisory lock; the quota hit is committed *before* the expensive work. Verified live: burst of 5 → 1 allowed, 4 blocked with `Retry-After` |
| "Only one demo run at a time" cannot be a process lock | transaction-scoped Postgres advisory lock |
| Client IP is the proxy's | `X-Forwarded-For` is trusted **only** when `TRUST_PROXY_HEADERS=1` (Vercel overwrites it); addresses are stored only as **salted hashes** (privacy note in the site footer) |
| Startup hooks may not run; cold starts | connection pools are created lazily on first use; no work at import time |
| Transaction poolers reject prepared statements | `prepare_threshold=None`, `min_size=0`, connection health check on checkout |
| IPv4-only runtime vs IPv6-only direct database address | runtime uses Supabase's **transaction pooler**; admin tasks use the direct connection |
| Read-only filesystem | the demo run works entirely in `/tmp` (tested with a read-only root) |

The Docker image and `docker compose` stack still exist and are tested; they are the portable alternative (single worker, in-memory limiter).

## What lives where
| Item | Location | Secret? |
|---|---|---|
| Owner (admin) database credential | git-ignored `.env.deploy` on the developer machine only | **yes** |
| `gopulse_api_ro` / `gopulse_api_rw` passwords, rate-limit salt | `.env.deploy` and Vercel *sensitive* (write-only) env vars | **yes** |
| `DATABASE_URL_RO/RW`, `RATE_LIMIT_*`, `TRUST_PROXY_HEADERS`, `DB_POOL_MAX`, `CORS_ORIGINS`, `DEMO_RUNS_*` | Vercel project `gopulse-api` (production) | URLs and salt yes; rest config |
| `NEXT_PUBLIC_API_URL` | Vercel project `gopulse-web` (inlined into the browser bundle at build time) | no |
| `PUBLIC_API_URL`, `PUBLIC_WEB_URL` | GitHub repository variables (used by the keep-alive and deploy workflows) | no |

The web tier never holds a database credential. The API uses its own two limited roles, **not** the owner password, so rotating the owner password
never affects the running site.

## Reproduce it from scratch
```bash
# 1. Supabase: create a project (East US), copy the DIRECT connection string into .env.deploy as SUPABASE_DIRECT_URL=...
#    (the file is git-ignored; never paste it anywhere). Find the pooler host once (aws-0-us-east-1.pooler.supabase.com for East US).
python scripts/setup_remote_db.py roles            # two least-privilege roles, strong random passwords saved to .env.deploy
python scripts/setup_remote_db.py urls             # runtime URLs (transaction pooler) saved to .env.deploy
export DATABASE_URL_OWNER="$SUPABASE_DIRECT_URL"; sh scripts/seed.sh   # generate -> pipeline -> dbt -> publish to Supabase
python scripts/setup_remote_db.py verify           # what each role can / cannot do; Supabase's public roles see nothing

# 2. Vercel (free Hobby team): two projects
python scripts/stage_api.py                         # self-contained API bundle in deploy/api (generates its pyproject.toml)
cd deploy/api && vercel link --project gopulse-api
#   set the project's framework preset to fastapi (a project created from the CLI has none, so nothing is built)
#   vercel env add ... (DATABASE_URL_RO / _RW / RATE_LIMIT_SALT as --sensitive; the rest plain)
vercel deploy --prod
cd ../../apps/web && vercel link --project gopulse-web   # framework nextjs; NEXT_PUBLIC_API_URL=<api url>
vercel deploy --prod
#   then set CORS_ORIGINS on the API to the site URL and redeploy the API

# 3. Prove it
python scripts/smoke_api.py https://<api> --origin https://<web> --demo
E2E_BASE_URL=https://<web> npx playwright test      # from apps/web
```

## Operating it
* **Refresh the data:** re-run the seed (step 1) - `publish` replaces the metrics tables in one transaction and **keeps** demo-run history and rate-limit rows.
* **Redeploy the API / site:** `python scripts/stage_api.py && (cd deploy/api && vercel deploy --prod)`; `(cd apps/web && vercel deploy --prod)`.
* **Keep Supabase awake:** the free tier pauses a project after a week without activity. `.github/workflows/keepalive.yml` reads one table through the API every 3 days
  (GitHub disables scheduled workflows after 60 days of repository inactivity - push occasionally, or re-enable it in the Actions tab).
* **Rotate credentials:** owner password - change it in Supabase and update `.env.deploy` (nothing live depends on it). API role passwords - delete the
  `GOPULSE_API_*_PASSWORD` lines, run `roles` then `urls`, update the two sensitive Vercel variables, redeploy.
* **CI-driven deploys** (`.github/workflows/deploy.yml`) are written but **off**: they need a `VERCEL_TOKEN` and the project IDs as secrets in a protected
  `production` environment and `DEPLOY_ENABLED=true`. Until then deployments are done with the CLI as above.
* **Limits to know:** Vercel Hobby is for personal, non-commercial use; function duration 300 s (a demo run takes ~1-2 s); Supabase free is 500 MB (this data is a few MB).

## Verified on the live deployment
| Check | Result |
|---|---|
| `scripts/smoke_api.py ... --origin <site> --demo` (read-only API checks, CORS allow/deny, input rejection, real demo run, history) | all pass |
| Shared rate limit on the live API | burst of 5 → 1 allowed, 4 × `429` with accurate `Retry-After` |
| Full Playwright suite against the **live site** (54 tests: every page, axe WCAG A/AA light + dark, phone layout, filters, error/empty/stale states, a real demo run, keyboard) | 54 / 54 |
| Database roles via the real pooled URLs | read-only role cannot write; write role cannot touch metrics or other schemas; Supabase's `anon`/`authenticated`/`PUBLIC` have no access to `serving` |
| Latency from a developer machine (see [benchmarks](benchmarks.md)) | ~47 ms p50 for every endpoint (≈ network round trip); first request after a fresh deploy 1.9 s (idle cold start not separately measured) |
| Page load on the live site | LCP 236-388 ms, CLS ≤ 0.07 |

**Not verified:** a deployment triggered by GitHub Actions (no token configured); uptime or behaviour over time; recovery after Supabase pauses a
project (mitigated by the keep-alive job, not tested); a custom domain; traffic beyond a handful of concurrent requests.
