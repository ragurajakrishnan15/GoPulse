# API & Serving Database (Phase 4)

All data is **synthetic**. Every response carries `meta: {synthetic_data, last_refreshed, as_of_date}`.
Interactive docs: `/docs` (Swagger) and `/openapi.json`.

## Run it locally
```bash
cp .env.example .env                      # local-only values
docker compose up -d postgres             # Postgres 16 on :5433, creates the two API roles
python -m pipelines.jobs.run --batch data/raw/clean
python -m pipelines.jobs.dbt_runner       # builds + tests marts, stores dbt results
python -m pipelines.jobs.failure_demo     # optional: failure-demo report
python -m pipelines.serving.publish       # lakehouse -> Postgres `serving` schema (atomic)
uvicorn services.api.app.main:app --env-file .env
```
Tests: `pytest services` (needs the Postgres container; skipped otherwise). A separate `gopulse_test` database
is rebuilt per session, so dev data is never touched.

## Data flow
`DuckDB Gold + dbt marts + run/quality tables` → `pipelines/serving/publish.py` (single transaction, full replace) →
`serving.*` in PostgreSQL → FastAPI. Published: user-level tables (filters), dbt marts (verbatim copies, used to
reconcile the API), run history, quality results + failed-row samples, failure-demo report, catalog.

**Real vs seeded:** `pipeline_runs.source` is `local-real` (executed by this repo's pipeline), `demo-live` (bounded
demo endpoint) or `seeded-example` (30 deterministic illustrative runs). The API labels each row
(`execution_label`, `is_real_execution`). Seeded rows are inventions of `pipelines/serving/seed_history.py`.

## Endpoints
| Endpoint | Notes |
|---|---|
| `GET /health` | service + DB status (503 when the DB is unavailable) |
| `GET /api/v1/overview` | KPIs with numerator, denominator, definition, window, eligibility, `applied_filters` |
| `GET /api/v1/metrics/timeseries?metric=` | dau, wau, new_signups, qualifying_events, agent_runs, agent_success_rate, agent_retry_rate, p50/p95_latency_ms, est_cost_usd, est_cost_per_interaction_usd |
| `GET /api/v1/funnels/activation` | signup → onboarding → activated (7d) → converted (30d); per-stage denominators |
| `GET /api/v1/retention/cohorts` | day 1/7/14/30 by week or day cohort; only elapsed windows count |
| `GET /api/v1/features/adoption`, `GET /api/v1/accounts/adoption` | user-level feature adoption; account-level adoption |
| `GET /api/v1/agents/summary` | totals, per agent type, failure categories, volume over time, cost (estimate) |
| `GET /api/v1/pipelines/runs` (+ `/{run_id}`) | paginated (`limit` ≤ 100, `page`), filter by `source`/`status`; tasks, retries, freshness lag, log excerpt |
| `GET /api/v1/quality/checks` | results for a run (default: latest real run) with affected rows + remediation |
| `GET /api/v1/quality/samples?reason=` | ≤ 25 failed synthetic rows per reason (incl. duplicates) |
| `GET /api/v1/quality/failure-demo` | before/after impact report from `pipelines.jobs.failure_demo` |
| `GET /api/v1/catalog/models` (+ `/{name}`), `GET /api/v1/catalog/metrics` | grain, PK, columns/types, lineage, tests + status, row counts, metric definitions |
| `POST /api/v1/demo/run` | bounded demo workflow (below) |

**Filters** (`start`, `end`, `tier`, `segment`, `plan`, `feature`) are validated enums/regexes, bound as SQL
parameters, and rejected with a clear 422 when not applicable (e.g. `feature` on cohort endpoints; range > 366 days;
unknown feature). `plan` is the user's *current* plan, not their plan at event time. Errors use one shape:
`{"error": {"code", "message", "details?"}}`.

**Reconciliation:** unfiltered API series/cohorts/funnels/agent summaries are tested to equal the dbt marts copied
into Postgres, and mart row counts equal DuckDB.

## Safe demo run
`POST /api/v1/demo/run {"scenario": "clean|light_defects|heavy_defects", "seed": 1..9999}`
- Closed request schema (`extra=forbid`): no commands, SQL, notebook/file paths or credentials can be supplied.
- Fixed workflow: generate a 100-user synthetic batch → run the real pipeline in a throw-away temp dir/DuckDB →
  record as `demo-live`. Published metrics are never modified.
- Limits: 3/min and 20/hour per client IP, 100/hour globally, one run at a time, history capped at 200.
  The limiter is in-memory (single process); put a shared limiter (Redis/edge) in front for multi-instance hosting,
  and set the proxy's trusted client-IP header before relying on per-IP limits.
- Uses a separate Postgres role that can only INSERT into `pipeline_runs`, `pipeline_task_runs`, `quality_check_results`.

## Security notes
- Dashboard queries use `gopulse_api_ro` (SELECT only, 5 s statement timeout); verified by tests that writes/DDL fail.
- Credentials only via environment variables; `.env` is git-ignored; `.env.example` holds placeholders.
- CORS limited to `CORS_ORIGINS`; `X-Content-Type-Options: nosniff`; unexpected errors return a generic 500.
