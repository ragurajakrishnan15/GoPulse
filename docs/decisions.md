# Architecture Decisions

## D1 — Local engine: DuckDB + Parquet (not local Spark)
Local Spark/Delta on Windows needs JVM + Hadoop shims and is slow to iterate. DuckDB runs the same SQL, works with `dbt-duckdb`, and needs no cloud credentials. PySpark/Delta versions live under `databricks/` as a separate, documented cloud integration; they are not claimed to have run unless they actually did.

## D2 — dbt adapter
`dbt-duckdb` locally. Models avoid engine-specific syntax where practical; differences with `dbt-databricks` are listed in docs when they arise.

## D3 — Scheduler ownership
Databricks Workflows owns the cloud pipeline's tasks. Airflow only triggers/monitors it and demonstrates retries and bounded backfills. For local runs, Airflow runs the local job modules directly. The two never schedule the same production run.

## D4 — Time zone
All timestamps are UTC, stored ISO‑8601 with `Z`. Activity date = UTC calendar date of `event_timestamp`.

## D5 — Dedup policy
Duplicate `event_id`: keep the row with the earliest `ingested_at`; later copies are counted as `duplicate` in run metrics (dropped from Silver, retained in Bronze).

## D6 — Late events
Events with `ingested_at - event_timestamp > 3 days` are "late". Silver is partitioned by *event* activity date and each run rebuilds every partition touched by the incoming batch, so a late event of any age rebuilds the (older) partition it belongs to; no fixed lookback is needed. Late events are flagged `is_late`. (`pipelines/jobs/silver.py`; tested in `test_late_events_rebuild_old_partition`.)

## D7 — Public demo serves precomputed data
Cloud compute is not run continuously. The site reads Postgres tables loaded from the last pipeline run and labels runs as live vs seeded.

## D8 — Transactional runs
Ingest, Silver, Gold and reconciliation execute in a single DuckDB transaction. Any failure (including a failed count reconciliation) rolls back, leaving the previous Gold untouched; the run and task records are written afterwards so failures remain visible (`status='failed'`, tasks `rolled_back`/`failed`).

## D9 — Content-addressed batch ids
`batch_id = sha256(events+dimension files)[:12]`. Re-ingesting identical files replaces that batch's Bronze/quarantine rows, so reruns are idempotent. Measured: two runs of the same batch give byte-identical Gold fingerprints.

## D10 — Deterministic floats
Sums of floating-point costs vary in the last digit with parallel execution order, which broke rerun-equality. Money and P95 are rounded (6 and 3 decimals) in aggregates.

## D11 - Retry only what retrying can fix
Failures are classified (`orchestration/tasks.py`): transient infrastructure errors retry with exponential backoff; quality-gate, reconciliation, dbt and input errors fail immediately (`AirflowFailException`) and block downstream tasks. Verified in a real Airflow run (retry -> `try_number=2`; gate failure -> no retry, downstream `upstream_failed`).

## D12 - Spark implementation is parity-tested, not deployed
The PySpark/Delta pipeline (`pipelines/spark`) is executed locally and must match the DuckDB pipeline row-for-row. The Databricks job/notebooks are structure-checked only; no Databricks run is claimed.

## D13 - Gold promotion order on Delta
Delta lacks multi-table transactions, so: quality gate before any write -> Silver partition `replaceWhere` -> Gold into a staging schema -> promote after reconciliation.

## D14 - Containers: one API worker, build-time public API URL, standalone web
The API runs a single uvicorn worker because the demo-run limiter is in-memory; scale only after introducing a shared limiter. `NEXT_PUBLIC_API_URL` is inlined into the browser bundle, so it is a Docker build argument, not a runtime variable. The web image uses Next.js `standalone` output (`node server.js`); CI runs the identical launcher.

## D15 - Deploy gate
Deployment runs only from `main`, only after CI succeeded on that commit, and only when `DEPLOY_ENABLED=true`, through a protected `production` environment. Default is off.

## D16 - Hosting: Vercel for site and API, Supabase for PostgreSQL (supersedes the plan's Render API host)
Chosen so the whole system can be deployed and redeployed from one CLI on free tiers. Consequences handled in code: the rate limiter and the "one run at a time" guard moved into PostgreSQL (advisory locks, salted-hash client identity); pools are lazy; connections are pooler-safe; the API runs on Python 3.12 (also tested in CI). The runtime uses Supabase's transaction pooler because Vercel is IPv4-only; admin work uses the direct connection. D14's single-worker/in-memory choice still applies to the Docker image.

## D17 - Secrets never go through chat, the repo or the web tier
Deployment secrets live in a git-ignored `.env.deploy`; Vercel receives them as write-only sensitive variables; the running API uses two generated, least-privilege database roles (never the owner credential), so the owner password can be rotated without touching production. (When a secret was nevertheless pasted into chat, the safeguard was exactly this: rotate it; nothing live depends on it.)
