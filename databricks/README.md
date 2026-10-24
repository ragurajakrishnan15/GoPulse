# Databricks implementation (PySpark + Delta Lake)

> **Status, stated plainly.** The Spark/Delta code in `pipelines/spark/spark_pipeline.py` **was executed** - in local Spark mode inside a Linux
> container - and is tested for **row-for-row parity** with the DuckDB pipeline. The job definition (`databricks.yml`) and notebooks
> were **only checked structurally**: this project has **not** deployed or run anything on a Databricks workspace, so there is no
> cloud execution history, runtime, cost or scale figure to report.

## Layout
| Path | Purpose |
|---|---|
| `pipelines/spark/spark_pipeline.py` | `Lake` (path mode for tests, Unity Catalog mode for Databricks), `ingest`, `build_silver`, `build_gold`, `promote_gold`, `reconcile`, `run` |
| `databricks/notebooks/01..04_*.py` | thin notebook wrappers, one per task |
| `databricks/databricks.yml` | Asset Bundle: 4-task job, job cluster, paused schedule |
| `databricks/tests/test_spark_parity.py` | Spark vs DuckDB parity suite |

(The Python code lives under `pipelines/`, not `databricks/`, because a top-level `databricks` package would shadow the runtime's own
`databricks` namespace.)

## Design
- **Bronze** `bronze.events` / `bronze.quarantine_events`, written with `replaceWhere batch_id = …` (idempotent per batch).
- **Silver** `silver.events`, partitioned by `activity_date`; each run rebuilds only the partitions the batch touches (`replaceWhere`), which also handles late events.
- **Gold** built into `gold_staging`, then promoted to `gold` by the last task only after reconciliation passes. Delta is atomic *per table*, not
  across tables, so the quarantine-rate gate runs **before any write** and promotion is last: a failed run never exposes a half-refreshed Gold.
- Gold coverage vs the local pipeline: the 3 dimensions, `fct_product_events`, `fct_agent_interactions`, `fct_daily_user_activity`, `fct_user_lifecycle`,
  and the activation / retention / daily-metrics aggregates. Not ported: `fct_account_adoption` and the dbt marts (run dbt-databricks on top - see below).
- Validation reuses `pipelines.ingestion.validate` row-wise in Python workers (single source of truth for reason codes). At production volume,
  port the rules to vectorised Spark expressions.

## Parity tests (run locally, Linux container)
```bash
docker build -t gopulse-spark infrastructure/docker/spark
docker run --rm -v "$PWD:/opt/gopulse" -w /opt/gopulse gopulse-spark \
  python -m pytest databricks/tests/test_spark_parity.py -q -c /dev/null --rootdir /opt/gopulse     # ~9 min
```
Result: all 5 tests pass - 3%-defect batch (counts, quarantine reasons, Silver rows, every Gold table), 7 daily batches with late events,
idempotent rerun, toxic batch blocked before any write with Gold untouched, malformed lines quarantined.

## Deploying (untested here)
```bash
databricks bundle validate -t dev && databricks bundle deploy -t dev
databricks bundle run gopulse_daily -t dev --params batch_path=/Volumes/<catalog>/landing/events/2026-01-05
```
Before enabling the schedule: create the catalog/schemas and a landing Volume, install the repo's `pipelines` package on the cluster (wheel or
Repos) so workers can import the validator, check node-type pricing for your cloud, and keep the job's cron **PAUSED** if Airflow triggers it.
dbt on Databricks needs `dbt-databricks` and some dialect changes (`quantile_cont`, `FILTER` usage, date arithmetic); the dbt models here target DuckDB.
