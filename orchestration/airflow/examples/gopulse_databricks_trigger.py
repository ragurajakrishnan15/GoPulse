"""EXAMPLE (not loaded by default): Airflow triggers and monitors the Databricks job instead of running the work itself.

Scheduler ownership for the cloud implementation (docs/decisions.md D3): Databricks Workflows owns the job definition,
cluster and task graph (databricks/databricks.yml). Airflow only passes the day's batch path, waits, and propagates
success/failure to the wider platform. Do NOT also enable the job's own cron schedule while this DAG is active.

Requires: apache-airflow-providers-databricks and an Airflow connection `databricks_default`.
STATUS: import-checked in a container; never run against a real workspace by this project.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.databricks.operators.databricks import DatabricksRunNowOperator

with DAG(
    dag_id="gopulse_databricks_trigger",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    end_date=datetime(2026, 3, 8),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=5), "depends_on_past": True},
    tags=["gopulse", "databricks", "example"],
) as dag:
    DatabricksRunNowOperator(
        task_id="run_gopulse_daily_job",
        databricks_conn_id="databricks_default",
        job_name="gopulse_daily",
        job_parameters={"batch_path": "/Volumes/gopulse/landing/events/{{ ds }}"},
        wait_for_termination=True,
        execution_timeout=timedelta(hours=1),
    )
