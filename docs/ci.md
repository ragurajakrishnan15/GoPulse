# CI/CD and containers (Phase 7)

## Run the whole stack locally
```bash
cp .env.example .env                              # local-only placeholder secrets
docker compose up -d --build                      # postgres, api, web (all with health checks)
docker compose --profile seed run --rm seed       # generate -> pipeline -> dbt -> failure demo -> publish  (~70 s, re-runnable)
open http://localhost:3000                        # API: http://localhost:8000/docs
```
Before the first seed the API answers `503 "No data has been published yet"` (not a 500), and the site shows its error state with Retry.

| Image | Dockerfile | Notes |
|---|---|---|
| `gopulse-api` | `infrastructure/docker/api/Dockerfile` | python slim, non-root, healthcheck, **one worker** (the demo-run rate limiter is in-memory), read-only root filesystem in compose |
| `gopulse-web` | `infrastructure/docker/web/Dockerfile` | multi-stage, Next.js `standalone` output, non-root `node`, healthcheck. `NEXT_PUBLIC_API_URL` is a **build** argument (it is inlined into the browser bundle) |
| `gopulse-seed` | `infrastructure/docker/pipeline/Dockerfile` | one-shot data job (pipeline + dbt + publish) |
| `gopulse-airflow`, `gopulse-spark` | `infrastructure/docker/airflow`, `.../spark` | validation images for orchestration (Phase 6), not part of the runtime stack |

No secrets are baked into any image: `.dockerignore` excludes `.env`, data and virtualenvs; credentials arrive as environment variables.

## GitHub Actions (`.github/workflows`)
`ci.yml` runs on pull requests, pushes to `main`, nightly, and manually. Permissions are read-only; PR runs cancel superseded runs.

| Job | What it enforces |
|---|---|
| `python` | `ruff`, then `pytest quality`: generator/validator, pipeline idempotency, quality gate, dbt build + corruption detection, failure demo, orchestration/backfill equivalence |
| `api` | Postgres service container + the same least-privilege roles script as local; `pytest services` (reconciles the API to the dbt marts, checks role permissions, rate limiting, injection safety) |
| `web` | `npm ci`, typecheck, ESLint, 27 Vitest tests, production build |
| `e2e` | seeds a real database, then 54 Playwright tests on a production build: every page, axe WCAG A/AA in light + dark, phone layout, filters, error/empty/loading/stale states, demo run, keyboard |
| `docker` | builds all images, brings up compose, seeds, and checks `/health`, `/overview` (≥ 10 KPIs) and the site |
| `spark`, `airflow` | Spark/Delta parity vs DuckDB (~8 min); DAG import + bounded 2-day backfill. Run on `main`, nightly, manual, or PRs labelled `full-ci` |
| `workflow-lint` | `actionlint` on the workflow files |

`deploy.yml` deploys **only** from `main`, **only** after CI succeeded on that exact commit, and **only** when the repository variable
`DEPLOY_ENABLED=true`. It uses the `production` environment (add required reviewers for a manual gate); credentials are environment secrets.
It is a skeleton until Phase 8 chooses hosts. Dependabot watches pip, npm, Actions and the API base image.

**Recommended repository settings:** protect `main`; require the `python`, `api`, `web`, `e2e` and `docker` checks; require PR review.
For stronger supply-chain hygiene pin third-party actions to commit SHAs (Dependabot can keep them current).

## What was verified (and what was not)
### On GitHub (the real thing)
The repository was pushed to https://github.com/ragurajakrishnan15/GoPulse and CI ran on Linux runners.

| Run | Result | What happened |
|---|---|---|
| First (`5a36295`) | **4 of 8 jobs failed**; Python, Web, Spark parity and workflow lint passed | see the four root causes below |
| Second (`95b0d51`) | **8 of 8 green** | after fixing all four |
| `Deploy` workflow (after each CI run) | gate job succeeded, both deploy jobs **skipped** | `DEPLOY_ENABLED` is unset, so nothing deploys - the gate works as designed |

Root causes of the first run's failures - none were visible from local runs:
1. **API job:** `test_failure_demo_report` passed locally only because earlier runs had left a git-ignored generated file (`data/demo/failure_demo.json`) behind. Fixed by building the report inside the test fixture.
2. **Docker job:** `apps/web/public/` was an empty directory, which git does not track, so `COPY /app/public` failed. Added `robots.txt`.
3. **E2E job:** a 5 px horizontal overflow of `/pipelines` at 375 px - the demo scenario `<select>` is as wide as its longest option, wider in Linux's default font. Let it shrink; the test now names offending elements when it fails.
4. **Airflow job:** dbt exited with code 2 and *no output* because the runner mounts the repo as another user and dbt could not create `./target` and `./logs`; the task's error message was empty. Reproduced locally with a uid-1001-owned volume. `GOPULSE_DBT_TARGET_DIR` now relocates them (set in the Airflow image) and dbt's stdout/stderr are included in the failure message.

Lesson recorded: *passing on the developer's machine is evidence about that machine* - clean-checkout and different-OS runs found four defects that 189 local tests did not.

### Before the push (local verification)

| Check | Result |
|---|---|
| `actionlint` on both workflows | clean (after fixing two shellcheck warnings) |
| Each job's commands run locally | `ruff` + `pytest quality`; `pytest services` against Postgres; `npm ci` → typecheck → lint → 27 unit tests → build; Playwright in `CI=true` mode (production standalone build) 54/54; the Airflow step exactly as written (2 successful backfill runs); Spark parity (Phase 6) |
| Fresh-machine behaviour | whole compose stack on an empty database volume: roles created, 3 images healthy, seed OK, **54/54 Playwright tests against the containers** |
| Container hardening | non-root users, read-only API filesystem (demo run still works via tmpfs), security headers on web and API, `no-new-privileges` |
| Not verified | an actual deployment (no target exists; the deploy jobs are skipped by the gate) |

## Problems this phase surfaced (all fixed)
1. **Lockfile** generated on Windows lacked Linux optional dependencies, so `npm ci` failed in Docker/CI. Regenerated from scratch; verified on Linux and Windows.
2. **Fresh deployment returned a 500** before the first publish (missing schema). Now a clear 503 with `Retry-After`, with a test against an empty database.
3. **`next start` with `output: "standalone"`** emits a warning ("does not work"). CI now runs the same `node .next/standalone/server.js` as the image (`scripts/start-standalone.mjs`).
4. Playwright config assumed a local `.venv`; it now honours `GOPULSE_PYTHON` or falls back to `python` on PATH.
