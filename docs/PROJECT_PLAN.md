# GoPulse — Project Completion Plan

Source of truth for scope is `README.md`. This file turns it into ordered, shippable phases.
Strategy: **thin vertical slice first, cloud integrations last.** Every phase ends with something runnable and tested.

Status legend: ⬜ not started · 🟡 in progress · ✅ done · ⏭ skipped by choice

| # | Phase | Outcome | Est. | Status |
|---|---|---|---|---|
| 0 | Foundation | Repo layout, tooling, decisions, plan | 0.5d | ✅ |
| 1 | Contract + synthetic data | Event schema, metric docs, deterministic generator with controlled defects, tests | 2d | ✅ |
| 2 | Local lakehouse pipeline | Ingestion/validation/quarantine → Bronze → Silver → Gold (DuckDB + Parquet), idempotent + incremental | 3d | ✅ |
| 3 | dbt models + data quality | Staging/marts, schema tests, freshness, reconciliation checks, run metadata | 2d | ✅ |
| 4 | Serving DB + FastAPI | Postgres serving tables, all README endpoints, validation, rate-limited demo run | 3d | ✅ |
| 5 | Next.js website | Six views, filters, definitions, loading/empty/error states, responsive + a11y | 4d | ✅ |
| 6 | Orchestration | Airflow DAG (deps, retries, bounded backfill); Databricks Workflow definition + PySpark notebooks | 2d | ✅ |
| 7 | CI/CD + Docker | GitHub Actions, Dockerfiles, docker-compose full stack | 1.5d | ✅ |
| 8 | Deployment | Vercel + API host + managed Postgres, HTTPS, clean-browser test | 1.5d | ✅ |
| 9 | Portfolio polish | Diagrams, genuine screenshots, benchmarks (measured only), walkthrough, final README | 1.5d | ✅ |

## Phase 0 — Foundation
- Repo skeleton per README; `.gitignore`; `git init`.
- `docs/decisions.md` records engine choices (see below).

## Phase 1 — Contract + synthetic data
- `docs/event_contract.md` and `pipelines/ingestion/event_schema.json` (JSON Schema).
- `docs/metrics.md`: numerator/denominator/window/time zone (UTC) for every metric.
- `pipelines/generators/generate.py`: users, accounts, features, sessions, product + agent events; seeded; configurable volume; defect injection with a ground-truth manifest.
- `pipelines/ingestion/validate.py`: structural + business-rule validation returning quarantine reason codes.
- Tests: determinism, schema validity of clean data, every defect type detected, manifest matches validator.
- **Exit:** `python -m pipelines.generators.generate --users 500 --seed 42` produces reproducible output; `pytest` green.

## Phase 2 — Local lakehouse pipeline
- Ingestion: validate → accepted vs quarantine (with reason) → Bronze (raw payload + batch_id, ingested_at).
- Silver: type normalization, dedupe by `event_id` (policy: earliest `ingested_at` wins), late-arrival lookback (default 3 days), referential checks.
- Gold: `fct_product_events`, `fct_agent_interactions`, `dim_*`, `fct_daily_user_activity`, `fct_account_adoption`, `agg_product_metrics_daily`.
- Watermark table + run records (input/accepted/quarantined/output counts). Reruns idempotent (partition overwrite).
- **Exit:** two consecutive runs give identical Gold; reconciliation holds (input = accepted + quarantined).

## Phase 3 — dbt + quality
- dbt-duckdb project mirroring Gold; unique/not_null/accepted_values/relationships tests; source freshness; docs.
- Custom pytest checks: duplicate-rate threshold, volume anomaly, stale partitions.
- Quality results persisted for the API (check, severity, affected rows, remediation).
- **Exit:** `dbt build` green; failure demo (inject defects → check fails → quarantine → rerun → metric delta).

## Phase 4 — Serving DB + FastAPI
- Postgres schema (runs, quality_checks, catalog, aggregates); loader from Gold.
- Endpoints per README; Pydantic-validated params, pagination, limits; `POST /demo/run` bounded + rate-limited, fixed workflow only.
- Contract tests against fixtures.
- **Exit:** OpenAPI docs complete; integration tests green.

## Phase 5 — Website
- Next.js + TS + Tailwind + Recharts. Views: Overview, Pipeline Ops, Data Quality, Product Analytics, Agent Insights, Catalog.
- "Synthetic demo data" banner, last-refreshed stamp, live-vs-seeded run badge, metric definitions beside KPIs.
- **Exit:** all views work at desktop+mobile; keyboard navigable; Lighthouse a11y ≥ 90.

## Phase 6 — Orchestration
- Airflow DAG: ingest → silver → gold → quality → publish; retries; bounded backfill. Doc: Airflow triggers Databricks job; Databricks owns compute.
- Databricks: Workflow JSON + PySpark/Delta notebooks equivalent to local logic (documented as separate cloud integration).

## Phase 7 — CI/CD + Docker
- Actions: ruff, pytest, dbt parse/build, API tests, `next build`, docker build. Deploy only from `main`.
- Compose: postgres, api, web, (airflow optional profile).

## Phase 8 — Deployment
- Vercel (web), Render/Fly (API), managed Postgres (Neon/Supabase). Secrets in provider stores. Precomputed data served; clearly labeled.

## Phase 9 — Polish
- Mermaid architecture + lineage, trade-offs, genuine screenshots, measured benchmark table, walkthrough, fill README links.

## Cross-cutting rules
- Synthetic data only; no credentials committed; every number in docs is measured.
- Each phase: tests first-class, docs updated, Definition-of-Done boxes in README ticked only when verified.
