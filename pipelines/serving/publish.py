"""Publish the lakehouse results to the PostgreSQL serving schema (atomic: one transaction, full replace).

    python -m pipelines.serving.publish [--db data/gopulse.duckdb] [--pg $DATABASE_URL_OWNER]

Prerequisites: a successful pipeline run AND `python -m pipelines.jobs.dbt_runner` (marts must exist).
Real runs are published as source='local-real'; clearly-labelled seeded example history is added as
'seeded-example'. Runs created by the API's demo endpoint ('demo-live') are preserved across republishes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import psycopg
import yaml
from psycopg.types.json import Jsonb

from pipelines.jobs.lakehouse import DEFAULT_DB
from pipelines.serving.seed_history import seeded_history

HERE = Path(__file__).parent
SCHEMA_SQL = HERE / "schema.sql"
CATALOG_YML = HERE.parent / "catalog" / "catalog.yml"
DBT_MANIFEST = Path("transformations/dbt/target/manifest.json")
FAILURE_DEMO = Path("data/demo/failure_demo.json")

MARTS = {
    "mart_kpi_daily": ["date", "dau", "wau", "new_signups", "registered_users", "agent_runs", "agent_success_rate",
                       "agent_retry_rate", "p50_latency_ms", "p95_latency_ms", "est_cost_usd", "est_cost_per_interaction_usd"],
    "mart_activation_rates": ["signup_date", "account_tier", "signups", "onboarding_started", "activated_7d", "converted_30d",
                              "activation_window_complete", "conversion_window_complete", "activation_rate", "conversion_rate"],
    "mart_retention_weekly": ["cohort_week", "account_tier", "day_n", "eligible_users", "retained_users", "retention_rate"],
    "mart_agent_summary": ["agent_type", "executions", "successes", "success_rate", "retry_rate", "p50_latency_ms",
                           "p95_latency_ms", "total_tokens", "est_cost_usd", "est_cost_per_interaction_usd"],
    "mart_agent_failures": ["failure_category", "failures", "avg_retries", "share_of_failures"],
    "mart_feature_adoption_weekly": ["week", "feature_id", "feature_name", "category", "feature_users", "active_users", "adoption_rate"],
    "mart_account_adoption": ["period_start", "account_tier", "eligible_accounts", "adopted_accounts", "adoption_rate", "active_users"],
}

# tables fully replaced on each publish (run history keeps demo-live rows)
REPLACED = ["features", "users", "activity_days", "user_day_feature", "agent_runs", *MARTS, "quality_samples",
            "catalog_models", "catalog_metrics", "failure_demo_report"]


class PublishError(RuntimeError):
    pass


def _insert(cur, table: str, cols: list[str], rows) -> int:
    rows = list(rows)
    if rows:
        cur.executemany(f"INSERT INTO serving.{table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", rows)
    return len(rows)


def _dicts(rows: list[dict], cols: list[str]):
    return [tuple(r[c] for c in cols) for r in rows]


def _log_excerpt(run, tasks) -> str:
    lines = [f"run {run['run_id']} status={run['status']} batch={run['batch_id']}"]
    for t in tasks:
        lines.append(f"  task {t['task']}: {t['status']} in {t['duration_ms']} ms"
                     + (f" -> {t['error_summary']}" if t["error_summary"] else ""))
    lines.append(f"  counts: input={run['input_count']} accepted={run['accepted_count']} "
                 f"quarantined={run['quarantined_count']} duplicates={run['duplicate_count']}")
    if run["error_summary"]:
        lines.append(f"  ERROR: {run['error_summary']}")
    return "\n".join(lines)


def _tested_tables(node: dict) -> list[str]:
    """Tables a dbt test applies to: the generic test's `model` argument, else (singular tests) every dependency."""
    model = (node.get("test_metadata") or {}).get("kwargs", {}).get("model", "")
    m = re.search(r"(?:source\('[^']+',\s*|ref\()'([^']+)'", model)
    if m:
        return [m.group(1)]
    return [d.split(".")[-1] for d in node.get("depends_on", {}).get("nodes", [])]


def _catalog(duck, as_of_run, qrows) -> tuple[list[tuple], list[tuple]]:
    cfg = yaml.safe_load(CATALOG_YML.read_text(encoding="utf-8"))
    models = cfg["models"]
    downstream: dict[str, list[str]] = {}
    for m in models:
        for u in m.get("upstream", []):
            downstream.setdefault(u, []).append(m["name"])

    tests_by_table: dict[str, list[dict]] = {}
    status_by_ref = {r["sample_ref"]: r["status"] for r in qrows if r["layer"] == "dbt"}
    if DBT_MANIFEST.exists():
        manifest = json.loads(DBT_MANIFEST.read_text(encoding="utf-8"))
        for uid, node in manifest["nodes"].items():
            if node.get("resource_type") != "test":
                continue
            tables = _tested_tables(node)
            for t in tables:
                tests_by_table.setdefault(t, []).append(
                    {"name": node["name"], "source": "dbt", "status": status_by_ref.get(f"dbt:{uid}", "not_run")})
    for r in qrows:  # Python checks attach to Silver by layer
        if r["layer"] == "silver" and r["source"] == "local-real":
            tests_by_table.setdefault("silver_events", []).append({"name": r["check_name"], "source": "python", "status": r["status"]})

    schemas = {"mart": "marts"}
    rows = []
    for m in models:
        schema = schemas.get(m["layer"], "main")
        cols = duck.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = ? AND table_name = ? "
            "ORDER BY ordinal_position", [schema, m["name"]]).fetchall()
        try:
            n = duck.execute(f'SELECT count(*) FROM {schema}."{m["name"]}"').fetchone()[0]
        except duckdb.Error:
            n = None
        desc = m.get("columns", {})
        tests = tests_by_table.get(m["name"], [])
        statuses = {t["status"] for t in tests}
        ts = "no_tests" if not tests else "failing" if "fail" in statuses else "passing" if statuses <= {"pass", "skipped"} else "partial"
        rows.append((m["name"], m["layer"], m["description"], m.get("purpose"), m["grain"], m["primary_key"],
                     m.get("upstream", []), sorted(downstream.get(m["name"], [])), cfg["refresh_cadence"], as_of_run,
                     cfg["source_link_base"] + m["source"],
                     Jsonb([{"name": c, "type": t, "description": desc.get(c)} for c, t in cols]),
                     Jsonb(tests), ts, n))
    metrics = [(x["key"], x["label"], x["unit"], x["definition"], x["numerator"], x["denominator"], x["time_window"],
                x["eligibility"], x.get("notes"), x["source_model"]) for x in cfg["metrics"]]
    return rows, metrics


def publish(duck_path: Path | str = DEFAULT_DB, pg_url: str | None = None) -> dict:
    pg_url = pg_url or os.environ.get("DATABASE_URL_OWNER")
    if not pg_url:
        raise PublishError("set DATABASE_URL_OWNER (or pass --pg)")
    duck = duckdb.connect(str(duck_path), read_only=True)
    try:
        duck.execute("SELECT 1 FROM marts.mart_kpi_daily LIMIT 1")
    except duckdb.Error as e:
        raise PublishError("dbt marts not found: run `python -m pipelines.jobs.dbt_runner` after the pipeline") from e
    last = duck.execute("SELECT run_id, batch_id, ended_at FROM pipeline_runs WHERE status='success' "
                        "ORDER BY started_at DESC LIMIT 1").fetchone()
    if not last:
        raise PublishError("no successful pipeline run found")
    run_id, batch_id, ended_at = last
    as_of_ts, as_of_date = duck.execute("SELECT as_of_ts, as_of_date FROM gold_meta").fetchone()

    counts: dict[str, int] = {}
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.autocommit = False
        cur = conn.cursor()
        try:
            cur.execute("TRUNCATE " + ", ".join(f"serving.{t}" for t in REPLACED))
            cur.execute("DELETE FROM serving.pipeline_runs WHERE source <> 'demo-live'")
            cur.execute("DELETE FROM serving.pipeline_task_runs WHERE run_id NOT IN "
                        "(SELECT run_id FROM serving.pipeline_runs)")
            cur.execute("DELETE FROM serving.quality_check_results WHERE source <> 'demo-live'")

            q = lambda sql, p=None: duck.execute(sql, p or []).fetchall()
            counts["features"] = _insert(cur, "features", ["feature_id", "name", "category"],
                                         q("SELECT feature_id, feature_name, category FROM dim_features"))
            counts["users"] = _insert(cur, "users",
                ["user_id", "signup_date", "account_id", "account_tier", "account_segment", "current_plan",
                 "onboarding_started", "activated_7d", "converted_30d"],
                q("""SELECT u.user_id, u.signup_date, u.account_id, u.account_tier, u.account_segment, u.current_plan,
                            l.onboarding_started_at IS NOT NULL, coalesce(l.activated_7d, false), coalesce(l.converted_30d, false)
                     FROM dim_users u JOIN fct_user_lifecycle l USING (user_id)"""))
            counts["activity_days"] = _insert(cur, "activity_days", ["user_id", "activity_date"],
                                              q("SELECT user_id, activity_date FROM fct_daily_user_activity"))
            counts["user_day_feature"] = _insert(cur, "user_day_feature", ["activity_date", "user_id", "feature_id", "events"],
                q("""SELECT activity_date, user_id, coalesce(feature_id, 'none'), count(*) FROM fct_product_events
                     WHERE is_qualifying_activity GROUP BY 1, 2, 3"""))
            counts["agent_runs"] = _insert(cur, "agent_runs",
                ["execution_id", "activity_date", "user_id", "feature_id", "agent_type", "status", "is_success",
                 "failure_category", "duration_ms", "retry_count", "is_retried", "input_tokens", "output_tokens", "cost_usd_estimate"],
                q("""SELECT execution_id, activity_date, user_id, feature_id, agent_type, status, is_success, failure_category,
                            duration_ms, retry_count, is_retried, input_tokens, output_tokens, cost_usd_estimate
                     FROM fct_agent_interactions"""))
            for tbl, cols in MARTS.items():
                counts[tbl] = _insert(cur, tbl, cols, q(f"SELECT {', '.join(cols)} FROM marts.{tbl}"))

            # ---- run history: real + seeded --------------------------------------------------
            task_rows = {}
            for r in q("SELECT run_id, task, status, started_at, ended_at, duration_ms, input_count, output_count, error_summary "
                       "FROM pipeline_task_runs ORDER BY started_at"):
                task_rows.setdefault(r[0], []).append(dict(zip(
                    ["run_id", "task", "status", "started_at", "ended_at", "duration_ms", "input_count", "output_count", "error_summary"], r, strict=True)))
            run_cols = ["run_id", "pipeline", "batch_id", "source", "status", "started_at", "ended_at", "duration_ms", "input_count",
                        "accepted_count", "quarantined_count", "duplicate_count", "silver_total", "retry_count", "source_watermark",
                        "freshness_lag_hours", "error_summary", "log_excerpt"]
            real_runs = []
            for r in q("""SELECT run_id, pipeline, batch_id, mode, status, started_at, ended_at, duration_ms, input_count,
                                 accepted_count, quarantined_count, duplicate_count, silver_total, retry_count, source_watermark,
                                 error_summary FROM pipeline_runs ORDER BY started_at DESC LIMIT 200"""):
                d = dict(zip(["run_id", "pipeline", "batch_id", "source", "status", "started_at", "ended_at", "duration_ms",
                              "input_count", "accepted_count", "quarantined_count", "duplicate_count", "silver_total",
                              "retry_count", "source_watermark", "error_summary"], r, strict=True))
                wm = d["source_watermark"]
                d["freshness_lag_hours"] = round((d["ended_at"] - datetime.fromisoformat(wm.rstrip("Z"))).total_seconds() / 3600, 2) if wm else None
                d["log_excerpt"] = _log_excerpt(d, task_rows.get(d["run_id"], []))
                real_runs.append(d)
            s_runs, s_tasks, s_quality = seeded_history(as_of_date)
            counts["pipeline_runs"] = _insert(cur, "pipeline_runs", run_cols, _dicts(real_runs + s_runs, run_cols))
            task_cols = ["run_id", "task", "status", "started_at", "ended_at", "duration_ms", "input_count", "output_count", "retry_count", "error_summary"]
            real_tasks = [dict(t, retry_count=0) for ts in task_rows.values() for t in ts if any(t["run_id"] == r["run_id"] for r in real_runs)]
            counts["pipeline_task_runs"] = _insert(cur, "pipeline_task_runs", task_cols, _dicts(real_tasks + s_tasks, task_cols))

            qrows = [dict(zip(["run_id", "batch_id", "layer", "check_name", "severity", "status", "affected_records", "observed",
                               "threshold", "detail", "remediation_status", "sample_ref", "checked_at"], r, strict=True), source="local-real")
                     for r in q("""SELECT run_id, batch_id, layer, check_name, severity, status, affected_records, observed, threshold,
                                          detail, remediation_status, sample_ref, checked_at FROM quality_check_results""")]
            qcols = ["run_id", "batch_id", "source", "layer", "check_name", "severity", "status", "affected_records", "observed",
                     "threshold", "detail", "remediation_status", "sample_ref", "checked_at"]
            counts["quality_check_results"] = _insert(cur, "quality_check_results", qcols, _dicts(qrows + s_quality, qcols))

            # ---- failed-row samples (synthetic payloads) --------------------------------------
            samples = []
            for reason in ("missing_user_id", "invalid_timestamp", "unknown_event_name", "orphaned_account", "schema_mismatch", "negative_value"):
                for batch, line_no, payload, reasons in q(
                        "SELECT batch_id, line_no, CAST(payload AS VARCHAR), reasons FROM quarantine_events "
                        "WHERE list_contains(reasons, ?) ORDER BY line_no LIMIT 25", [reason]):
                    samples.append((reason, batch, line_no, list(reasons), Jsonb(json.loads(payload))))
            for batch, line_no, payload in q(
                    """SELECT batch_id, line_no, CAST(payload AS VARCHAR) FROM bronze_events WHERE event_id IN
                       (SELECT event_id FROM bronze_events GROUP BY 1 HAVING count(*) > 1)
                       ORDER BY event_id, line_no LIMIT 25"""):
                samples.append(("duplicate_event_id", batch, line_no, ["duplicate_event_id"], Jsonb(json.loads(payload))))
            counts["quality_samples"] = _insert(cur, "quality_samples", ["reason", "batch_id", "line_no", "reasons", "payload"], samples)

            if FAILURE_DEMO.exists():
                cur.execute("INSERT INTO serving.failure_demo_report (id, report) VALUES (1, %s)",
                            [Jsonb(json.loads(FAILURE_DEMO.read_text(encoding="utf-8")))])
                counts["failure_demo_report"] = 1

            cat_rows, metric_rows = _catalog(duck, ended_at.replace(tzinfo=UTC), qrows)
            counts["catalog_models"] = _insert(cur, "catalog_models",
                ["name", "layer", "description", "purpose", "grain", "primary_key", "upstream", "downstream", "refresh_cadence",
                 "last_refresh", "source_link", "columns", "tests", "test_status", "row_count"], cat_rows)
            counts["catalog_metrics"] = _insert(cur, "catalog_metrics",
                ["key", "label", "unit", "definition", "numerator", "denominator", "time_window", "eligibility", "notes", "source_model"], metric_rows)

            cur.execute("""INSERT INTO serving.publish_meta (id, published_at, source_run_id, source_batch_id, as_of_date, as_of_ts)
                           VALUES (1, now(), %s, %s, %s, %s)
                           ON CONFLICT (id) DO UPDATE SET published_at = EXCLUDED.published_at, source_run_id = EXCLUDED.source_run_id,
                           source_batch_id = EXCLUDED.source_batch_id, as_of_date = EXCLUDED.as_of_date, as_of_ts = EXCLUDED.as_of_ts""",
                        [run_id, batch_id, as_of_date, as_of_ts])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    duck.close()
    return {"published_run_id": run_id, "as_of_date": str(as_of_date), "rows": counts}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--pg", default=None)
    a = p.parse_args(argv)
    print(json.dumps(publish(a.db, a.pg), indent=2))


if __name__ == "__main__":
    main()
