"""Local lakehouse storage: one DuckDB database holding Bronze/Silver/Gold + run metadata.

Layers
  raw_*            latest dimension snapshots (users, accounts, features)
  bronze_events    accepted raw event payloads, one row per delivered record (duplicates retained)
  quarantine_events rejected payloads with reason codes
  silver_events    cleaned, deduplicated events (partitioned logically by activity_date)
  gold_* / fct_* / dim_* / agg_*   built in gold.py
  pipeline_runs, pipeline_task_runs, pipeline_watermark   observability
"""
from __future__ import annotations

from pathlib import Path

import duckdb

DEFAULT_DB = Path("data/gopulse.duckdb")
DEFAULT_LAKE = Path("data/lake")

DDL = """
CREATE TABLE IF NOT EXISTS raw_users (user_id VARCHAR PRIMARY KEY, account_id VARCHAR, signup_at TIMESTAMP,
    plan VARCHAR, country VARCHAR);
CREATE TABLE IF NOT EXISTS raw_accounts (account_id VARCHAR PRIMARY KEY, tier VARCHAR, segment VARCHAR,
    created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS raw_features (feature_id VARCHAR PRIMARY KEY, name VARCHAR, category VARCHAR);

CREATE TABLE IF NOT EXISTS bronze_events (
    batch_id VARCHAR NOT NULL, line_no BIGINT NOT NULL, event_id VARCHAR NOT NULL,
    ingested_at VARCHAR NOT NULL, payload JSON NOT NULL, loaded_at TIMESTAMP NOT NULL,
    PRIMARY KEY (batch_id, line_no));
CREATE TABLE IF NOT EXISTS quarantine_events (
    batch_id VARCHAR NOT NULL, line_no BIGINT NOT NULL, payload JSON, reasons VARCHAR[] NOT NULL,
    quarantined_at TIMESTAMP NOT NULL, PRIMARY KEY (batch_id, line_no));

CREATE TABLE IF NOT EXISTS silver_events (
    event_id VARCHAR PRIMARY KEY, event_name VARCHAR NOT NULL, event_ts TIMESTAMP NOT NULL,
    ingested_at TIMESTAMP NOT NULL, activity_date DATE NOT NULL, user_id VARCHAR NOT NULL,
    account_id VARCHAR, session_id VARCHAR NOT NULL, feature_id VARCHAR, is_late BOOLEAN NOT NULL,
    batch_id VARCHAR NOT NULL, execution_id VARCHAR, agent_type VARCHAR, status VARCHAR,
    failure_category VARCHAR, duration_ms BIGINT, retry_count INTEGER, input_tokens BIGINT,
    output_tokens BIGINT, cost_usd_estimate DOUBLE, plan VARCHAR);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id VARCHAR PRIMARY KEY, pipeline VARCHAR, batch_id VARCHAR, mode VARCHAR, status VARCHAR,
    started_at TIMESTAMP, ended_at TIMESTAMP, duration_ms BIGINT, input_count BIGINT,
    accepted_count BIGINT, quarantined_count BIGINT, duplicate_count BIGINT, silver_total BIGINT,
    retry_count INTEGER DEFAULT 0, source_watermark VARCHAR, output_counts JSON, error_summary VARCHAR);
CREATE TABLE IF NOT EXISTS pipeline_task_runs (
    run_id VARCHAR, task VARCHAR, status VARCHAR, started_at TIMESTAMP, ended_at TIMESTAMP,
    duration_ms BIGINT, input_count BIGINT, output_count BIGINT, error_summary VARCHAR);
CREATE TABLE IF NOT EXISTS quality_check_results (
    run_id VARCHAR, batch_id VARCHAR, layer VARCHAR, check_name VARCHAR, severity VARCHAR, status VARCHAR,
    affected_records BIGINT, observed DOUBLE, threshold DOUBLE, detail VARCHAR, remediation_status VARCHAR,
    sample_ref VARCHAR, checked_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS pipeline_watermark (name VARCHAR PRIMARY KEY, value VARCHAR, updated_at TIMESTAMP);
"""


def connect(db_path: Path | str = DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute("SET TimeZone='UTC'")
    con.execute(DDL)
    return con
