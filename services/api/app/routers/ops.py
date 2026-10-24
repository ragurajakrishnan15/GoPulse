"""Pipeline operations, data quality and catalog endpoints (read-only)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from psycopg import Connection

from services.api.app.db import meta, one, query, ro_conn

router = APIRouter(prefix="/api/v1", tags=["operations"])

Source = Literal["local-real", "demo-live", "seeded-example"]
RunId = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")
LABELS = {"local-real": "Real execution (local pipeline run)", "demo-live": "Real execution (bounded demo run)",
          "seeded-example": "Seeded example history - not a real execution"}


def _label(row: dict) -> dict:
    row["execution_label"] = LABELS[row["source"]]
    row["is_real_execution"] = row["source"] != "seeded-example"
    return row


@router.get("/pipelines/runs", summary="Paginated pipeline-run history")
def pipeline_runs(source: Source | None = None, status: Literal["success", "failed"] | None = None,
                  limit: int = Query(25, ge=1, le=100), page: int = Query(1, ge=1, le=1000),
                  conn: Connection = Depends(ro_conn)):
    where, params = "WHERE TRUE", []
    if source:
        where += " AND r.source = %s"
        params.append(source)
    if status:
        where += " AND r.status = %s"
        params.append(status)
    total = one(conn, f"SELECT count(*) AS n FROM serving.pipeline_runs r {where}", params)["n"]
    items = query(conn, f"""
        SELECT r.*, (SELECT max(s.ended_at) FROM serving.pipeline_runs s
                     WHERE s.status = 'success' AND s.source = r.source AND s.ended_at <= r.ended_at) AS last_successful_run_at
        FROM serving.pipeline_runs r {where} ORDER BY r.started_at DESC, r.run_id LIMIT %s OFFSET %s""",
        [*params, limit, (page - 1) * limit])
    summary = one(conn, """
        SELECT max(ended_at) FILTER (WHERE status = 'success' AND source <> 'seeded-example') AS last_real_success_at,
               count(*) FILTER (WHERE source = 'local-real') AS local_real_runs,
               count(*) FILTER (WHERE source = 'demo-live') AS demo_live_runs,
               count(*) FILTER (WHERE source = 'seeded-example') AS seeded_example_runs
        FROM serving.pipeline_runs""")
    return {"items": [_label(i) for i in items], "page": page, "limit": limit, "total": total,
            "has_next": page * limit < total, "summary": summary, "labels": LABELS, "meta": meta(conn)}


@router.get("/pipelines/runs/{run_id}", summary="One run with its tasks")
def pipeline_run(run_id: str = RunId, conn: Connection = Depends(ro_conn)):
    run = one(conn, "SELECT * FROM serving.pipeline_runs WHERE run_id = %s", [run_id])
    if not run:
        raise HTTPException(404, f"run '{run_id}' not found")
    tasks = query(conn, "SELECT * FROM serving.pipeline_task_runs WHERE run_id = %s ORDER BY started_at", [run_id])
    return {"run": _label(run), "tasks": tasks, "meta": meta(conn)}


@router.get("/quality/checks", summary="Quality check results for a run")
def quality_checks(run_id: str | None = Query(None, pattern=r"^[A-Za-z0-9_-]{1,64}$"), source: Source | None = None,
                   layer: Literal["ingestion", "silver", "gold", "dbt"] | None = None,
                   severity: Literal["error", "warn"] | None = None, status: Literal["pass", "fail", "skipped"] | None = None,
                   conn: Connection = Depends(ro_conn)):
    if not run_id:
        run = one(conn, "SELECT run_id FROM serving.pipeline_runs WHERE source = %s AND status = 'success' "
                        "ORDER BY started_at DESC LIMIT 1", [source or "local-real"])
        if not run:
            raise HTTPException(404, "no matching run found")
        run_id = run["run_id"]
    where, params = "WHERE run_id = %s", [run_id]
    for col, val in (("layer", layer), ("severity", severity), ("status", status)):
        if val:
            where += f" AND {col} = %s"
            params.append(val)
    items = query(conn, f"SELECT * FROM serving.quality_check_results {where} ORDER BY "
                        f"(status = 'fail') DESC, (severity = 'error') DESC, layer, check_name", params)
    summary = one(conn, """
        SELECT count(*) AS checks, count(*) FILTER (WHERE status = 'pass') AS passed,
               count(*) FILTER (WHERE status = 'fail') AS failed, count(*) FILTER (WHERE status = 'skipped') AS skipped,
               coalesce(sum(affected_records) FILTER (WHERE status = 'fail'), 0) AS affected_records
        FROM serving.quality_check_results WHERE run_id = %s""", [run_id])
    run = one(conn, "SELECT run_id, source, status, started_at, ended_at, batch_id FROM serving.pipeline_runs WHERE run_id = %s", [run_id])
    return {"run": _label(run) if run else None, "summary": summary, "items": items, "meta": meta(conn)}


SampleReason = Literal["missing_user_id", "invalid_timestamp", "unknown_event_name", "orphaned_account", "schema_mismatch",
                       "negative_value", "duplicate_event_id"]


@router.get("/quality/samples", summary="Sample of failed rows (synthetic payloads)")
def quality_samples(reason: SampleReason, limit: int = Query(10, ge=1, le=25), conn: Connection = Depends(ro_conn)):
    rows = query(conn, "SELECT reason, batch_id, line_no, reasons, payload FROM serving.quality_samples "
                       "WHERE reason = %s ORDER BY line_no LIMIT %s", [reason, limit])
    return {"reason": reason, "items": rows, "note": "Synthetic records; shown for demonstration only.", "meta": meta(conn)}


@router.get("/quality/failure-demo", summary="Controlled failure demonstration: before/after impact")
def failure_demo(conn: Connection = Depends(ro_conn)):
    row = one(conn, "SELECT report FROM serving.failure_demo_report WHERE id = 1")
    if not row:
        raise HTTPException(404, "failure demo report has not been generated (python -m pipelines.jobs.failure_demo)")
    return {"report": row["report"], "meta": meta(conn)}


@router.get("/catalog/models", summary="Model catalog")
def catalog_models(layer: Literal["silver", "gold", "mart"] | None = None, conn: Connection = Depends(ro_conn)):
    sql = ("SELECT name, layer, description, purpose, grain, primary_key, upstream, downstream, refresh_cadence, "
           "last_refresh, source_link, test_status, row_count FROM serving.catalog_models")
    params: list = []
    if layer:
        sql += " WHERE layer = %s"
        params.append(layer)
    return {"items": query(conn, sql + " ORDER BY layer, name", params), "meta": meta(conn)}


@router.get("/catalog/models/{name}", summary="One model with columns, lineage and tests")
def catalog_model(name: str = Path(pattern=r"^[a-z_0-9]{1,64}$"), conn: Connection = Depends(ro_conn)):
    row = one(conn, "SELECT * FROM serving.catalog_models WHERE name = %s", [name])
    if not row:
        raise HTTPException(404, f"model '{name}' not found")
    row["metrics"] = query(conn, "SELECT key, label FROM serving.catalog_metrics WHERE source_model = %s", [name])
    return {"model": row, "meta": meta(conn)}


@router.get("/catalog/metrics", summary="Metric definitions")
def catalog_metrics(conn: Connection = Depends(ro_conn)):
    return {"items": query(conn, "SELECT * FROM serving.catalog_metrics ORDER BY key"), "meta": meta(conn)}
