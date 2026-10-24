# Orchestration (Phase 6)

## Who owns what (decision D3)
| Implementation | Scheduler | What it runs |
|---|---|---|
| **Local / demo** (DuckDB) | **Apache Airflow** (`orchestration/airflow/dags/gopulse_daily.py`) | the repo's job modules directly |
| **Cloud** (Databricks + Delta) | **Databricks Workflows** (`databricks/databricks.yml`) | PySpark notebooks (`databricks/notebooks`) |
| Cloud + Airflow | Airflow *triggers and monitors* the Databricks job (`orchestration/airflow/examples/gopulse_databricks_trigger.py`) | nothing itself |

The same production run is never scheduled by both. If the example trigger DAG is used, keep the Databricks job's own cron paused.

## The Airflow DAG: `gopulse_daily`
```
check_window -> prepare_batch -> run_lakehouse -> dbt_build -> publish_serving
```
| Task | Does | On failure |
|---|---|---|
| `check_window` | rejects dates outside the dataset window | non-retryable |
| `prepare_batch` | materialises the day's delivery (events whose `ingested_at` is that UTC date, plus a dimension snapshot) | retry |
| `run_lakehouse` | ingest → Silver → Gold → reconcile → quality gate, **one transaction** | see below |
| `dbt_build` | `dbt build` (isolated virtualenv), persists test results | non-retryable |
| `publish_serving` | loads Postgres (skipped when `DATABASE_URL_OWNER` is unset) | retry on connectivity |

**Retries only for recoverable failures.** `orchestration/tasks.py` classifies errors:
`TransientError` (file lock, connectivity, timeout) → Airflow retries 2× with exponential backoff;
`NonRetryableError` (quality gate, reconciliation, bad input, dbt failure) → `AirflowFailException`: no retry, downstream tasks
become `upstream_failed`, and the previous Gold stays live because the run rolled back.

**Late events.** Deliveries are split by *ingestion* date, so a late event arrives in a later day's batch than its event date.
Silver rebuilds the older partition it belongs to.

**Bounded backfill - never accidental catch-up.**
`catchup=False` (enabling the DAG enqueues nothing); `end_date` caps the window and `check_window` enforces it;
`max_active_runs=1` and `max_active_tasks=1` (DuckDB is single-writer); `depends_on_past=True` keeps days ordered. Backfill with an
explicit range:
```bash
airflow dags backfill -s 2026-01-01 -e 2026-01-07 gopulse_daily
```
Backfilling is idempotent: replaying a day, or the whole range, leaves Gold byte-identical (tested).

## Run it
```bash
docker build -t gopulse-airflow infrastructure/docker/airflow
docker run --rm -v "$PWD:/opt/gopulse" -e GOPULSE_WORK_DIR=/tmp/gp -e GOPULSE_USERS=150 -e GOPULSE_DAYS=30 gopulse-airflow \
  bash -c 'airflow db migrate && airflow dags backfill -s 2026-01-02 -e 2026-01-04 gopulse_daily'
```
Config is environment-driven (`GOPULSE_WORK_DIR`, `GOPULSE_USERS`, `GOPULSE_SEED`, `GOPULSE_DAYS`, `GOPULSE_DEFECT_RATE`, `GOPULSE_RUN_DBT`,
`GOPULSE_RETRY_DELAY_S`, `DATABASE_URL_OWNER`). `GOPULSE_CHAOS=transient_once:<stage>:<flagfile>` injects one transient failure for demos/tests.

## What was actually verified
| Claim | How | Result |
|---|---|---|
| DAG imports, 5-task chain | `airflow dags list-import-errors` / `tasks list` in Airflow 2.10.5 container | no errors |
| Bounded backfill runs end to end | `airflow dags backfill -s 2026-01-02 -e 2026-01-04` (includes dbt build) | 3/3 runs `success` |
| Transient failure is retried | injected one failure in `run_lakehouse`; read task instances from Airflow's DB | `run_lakehouse_task` `try_number=2`, all others 1, run succeeded |
| Quality failure is **not** retried | 50%-defect delivery | `run_lakehouse_task` `failed` after `try_number=1`; `dbt_build`, `publish_serving` = `upstream_failed` |
| Day-by-day backfill == one full load | pytest `test_backfill_day_by_day_equals_one_full_load` (14 daily batches incl. late events) | identical Gold fingerprints |
| Replays are no-ops | pytest (replay a day; replay the range) | identical fingerprints |
| Retry classification | pytest (transient / non-retryable / injected) | passes |
| Databricks trigger DAG imports | container with `apache-airflow-providers-databricks` (constraints applied) | no import errors |

**Not verified:** `publish_serving` *inside Airflow* against Postgres (skipped in those runs; `publish` itself is tested in Phase 4);
scheduler-driven runs and the Airflow web UI (only the CLI backfill path was exercised); anything on a real Databricks workspace.
