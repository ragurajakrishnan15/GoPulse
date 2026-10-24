"""GoPulse daily pipeline (Airflow).

Scheduler ownership (docs/decisions.md D3): in the LOCAL implementation Airflow owns the schedule and runs the job
modules directly. In the Databricks implementation, Databricks Workflows owns the job; Airflow would only *trigger and
monitor* it (see `databricks/README.md`). The two never schedule the same production run.

Flow:  check_window -> prepare_batch -> run_lakehouse -> dbt_build -> publish_serving
  * run_lakehouse performs ingest -> Silver -> Gold -> reconcile -> quality gate in ONE transaction, so Gold is
    never published half-refreshed. Its failure modes are classified:
        - quality gate / reconciliation / bad input  -> AirflowFailException (NO retry, downstream blocked)
        - transient infra (file lock, connectivity)  -> normal retry with exponential backoff
  * Idempotent per logical date: re-running a day replaces that day's Bronze/Silver/Gold contributions.

Bounded backfill (never catch-up by accident):
  * catchup=False: enabling the DAG does not enqueue history.
  * end_date caps the dataset window; check_window rejects dates outside it.
  * max_active_runs=1, max_active_tasks=1 (DuckDB is single-writer) and depends_on_past=True keep days strictly ordered.
  * Backfill only with an explicit range, e.g.
        airflow dags backfill -s 2026-01-01 -e 2026-01-07 gopulse_daily
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException, AirflowSkipException

from orchestration.tasks import (
    Config,
    NonRetryableError,
    check_window,
    prepare_batch,
    publish_serving,
    run_dbt,
    run_lakehouse,
)

RETRY_DELAY_S = int(os.environ.get("GOPULSE_RETRY_DELAY_S", "60"))

default_args = {
    "owner": "gopulse",
    "retries": 2,
    "retry_delay": timedelta(seconds=RETRY_DELAY_S),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=10),
    "depends_on_past": True,
}


def _guard(fn, *args):
    """NonRetryableError -> AirflowFailException (Airflow will not retry it). Anything else keeps normal retry semantics."""
    try:
        return fn(*args)
    except NonRetryableError as e:
        raise AirflowFailException(str(e)) from e


@dag(
    dag_id="gopulse_daily",
    description="Daily GoPulse lakehouse run: batch -> Bronze/Silver/Gold -> quality gate -> dbt -> serving",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    end_date=datetime(2026, 3, 8),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    default_args=default_args,
    tags=["gopulse", "synthetic-data"],
    doc_md=__doc__,
)
def gopulse_daily():
    @task
    def check_window_task(ds=None) -> dict:
        return _guard(check_window, ds, Config.from_env())

    @task
    def prepare_batch_task(window: dict, ds=None) -> dict:
        out = _guard(prepare_batch, window["ds"], Config.from_env())
        if out["events"] == 0:
            raise AirflowSkipException(f"no events delivered for {ds}")
        return out

    @task
    def run_lakehouse_task(batch: dict) -> dict:
        return _guard(run_lakehouse, batch["batch_dir"], Config.from_env())

    @task
    def dbt_build_task(lakehouse: dict) -> dict:
        return _guard(run_dbt, Config.from_env()) | {"after_run": lakehouse["run_id"]}

    @task
    def publish_serving_task(dbt: dict) -> dict:
        return _guard(publish_serving, Config.from_env())

    publish_serving_task(dbt_build_task(run_lakehouse_task(prepare_batch_task(check_window_task()))))


gopulse_daily()
