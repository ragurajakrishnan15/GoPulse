"""Silver: typed, deduplicated events.

Partitioning is by *event* activity date, so a late event automatically triggers a rebuild of the (older)
partition it belongs to — this is how late arrivals are handled (see docs/decisions.md D6).
Dedup policy (D5): per event_id the row with the earliest (ingested_at, batch_id, line_no) wins.
"""
from __future__ import annotations

LATE_THRESHOLD_DAYS = 3


def _ts(expr: str) -> str:
    return f"CAST(rtrim({expr}, 'Z') AS TIMESTAMP)"


def build_silver(con, batch_id: str) -> dict:
    """Rebuild every activity_date partition touched by this batch. Caller owns the transaction."""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _affected_dates AS
        SELECT DISTINCT CAST({_ts("json_extract_string(payload, '$.event_timestamp')")} AS DATE) AS d
        FROM bronze_events WHERE batch_id = ?""", [batch_id])

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _silver_stage AS
        WITH parsed AS (
            SELECT batch_id, line_no, event_id,
                   json_extract_string(payload, '$.event_name') AS event_name,
                   {_ts("json_extract_string(payload, '$.event_timestamp')")} AS event_ts,
                   {_ts("json_extract_string(payload, '$.ingested_at')")} AS ingested_at,
                   json_extract_string(payload, '$.user_id') AS user_id,
                   json_extract_string(payload, '$.account_id') AS account_id,
                   json_extract_string(payload, '$.session_id') AS session_id,
                   json_extract_string(payload, '$.feature_id') AS feature_id,
                   json_extract_string(payload, '$.properties.execution_id') AS execution_id,
                   json_extract_string(payload, '$.properties.agent_type') AS agent_type,
                   json_extract_string(payload, '$.properties.status') AS status,
                   json_extract_string(payload, '$.properties.failure_category') AS failure_category,
                   CAST(json_extract(payload, '$.properties.duration_ms') AS BIGINT) AS duration_ms,
                   CAST(json_extract(payload, '$.properties.retry_count') AS INTEGER) AS retry_count,
                   CAST(json_extract(payload, '$.properties.input_tokens') AS BIGINT) AS input_tokens,
                   CAST(json_extract(payload, '$.properties.output_tokens') AS BIGINT) AS output_tokens,
                   CAST(json_extract(payload, '$.properties.cost_usd_estimate') AS DOUBLE) AS cost_usd_estimate,
                   json_extract_string(payload, '$.properties.plan') AS plan
            FROM bronze_events)
        SELECT *, CAST(event_ts AS DATE) AS activity_date,
               row_number() OVER (PARTITION BY event_id ORDER BY ingested_at, batch_id, line_no) AS rn
        FROM parsed WHERE CAST(event_ts AS DATE) IN (SELECT d FROM _affected_dates)""")

    con.execute("DELETE FROM silver_events WHERE activity_date IN (SELECT d FROM _affected_dates)")
    con.execute(f"""
        INSERT INTO silver_events
        SELECT event_id, event_name, event_ts, ingested_at, activity_date, user_id, account_id, session_id,
               feature_id, (ingested_at - event_ts) > INTERVAL {LATE_THRESHOLD_DAYS} DAY AS is_late, batch_id,
               execution_id, agent_type, status, failure_category, duration_ms, retry_count, input_tokens,
               output_tokens, cost_usd_estimate, plan
        FROM _silver_stage WHERE rn = 1""")

    kept = con.execute("SELECT count(*) FROM silver_events WHERE batch_id = ?", [batch_id]).fetchone()[0]
    dups = con.execute("SELECT count(*) FROM _silver_stage WHERE rn > 1 AND batch_id = ?", [batch_id]).fetchone()[0]
    partitions = con.execute("SELECT count(*) FROM _affected_dates").fetchone()[0]
    total = con.execute("SELECT count(*) FROM silver_events").fetchone()[0]
    return {"kept_from_batch": kept, "duplicates": dups, "partitions_rebuilt": partitions, "silver_total": total}
