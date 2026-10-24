"""POST /api/v1/demo/run - a bounded, safe demo workflow.

Security properties (documented in docs/api.md):
  * The request body is a closed schema (enum scenario + small integer seed); unknown fields are rejected.
    There are NO parameters for commands, SQL, notebook/file paths or credentials.
  * The workflow is fixed: generate a 100-user synthetic batch -> run the real local pipeline in a throw-away
    temp directory/DuckDB file -> record the run. It never touches the published metrics tables.
  * Rate limited per client and globally, one run at a time, history capped, hard-bounded data size.
  * Writes go through a separate Postgres role that can only INSERT into the three run-history tables.
"""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Literal

import duckdb
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pipelines.generators.generate import Config, generate, write_jsonl
from pipelines.jobs.run import run_pipeline
from pipelines.serving.publish import _log_excerpt
from services.api.app.ratelimit import LOCK_DEMO_RUN, client_key

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])
_LOCK = threading.Lock()
DEFECT_RATES = {"clean": 0.0, "light_defects": 0.03, "heavy_defects": 0.40}
DEMO_USERS = 100


class DemoRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Literal["clean", "light_defects", "heavy_defects"] = Field(
        "clean", description="clean = no defects; light_defects = ~3% (published with warnings); heavy_defects = ~40% (blocked by the quality gate)")
    seed: int = Field(1, ge=1, le=9999, description="Deterministic generator seed")


def _rows(con, sql: str, params: list) -> list[dict]:
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _execute(req: DemoRunRequest) -> dict:
    with tempfile.TemporaryDirectory(prefix="gopulse_demo_") as tmp:
        root = Path(tmp)
        users, accounts, features, events, _ = generate(
            Config(users=DEMO_USERS, accounts=12, days=30, seed=req.seed, defect_rate=DEFECT_RATES[req.scenario]))
        batch = root / "batch"
        batch.mkdir()
        for name, rows in (("users", users), ("accounts", accounts), ("features", features), ("events", events)):
            write_jsonl(batch / f"{name}.jsonl", rows)
        summary = run_pipeline(batch, root / "demo.duckdb", None)
        con = duckdb.connect(str(root / "demo.duckdb"), read_only=True)
        try:
            rid = [summary["run_id"]]
            run = _rows(con, "SELECT * FROM pipeline_runs WHERE run_id = ?", rid)[0]
            tasks = _rows(con, "SELECT * FROM pipeline_task_runs WHERE run_id = ? ORDER BY started_at", rid)
            quality = _rows(con, "SELECT * FROM quality_check_results WHERE run_id = ?", rid)
        finally:
            con.close()
    return {"summary": summary, "run": run, "tasks": tasks, "quality": quality}


def _record_run(conn, app, body: DemoRunRequest) -> dict:
    """Run the workflow and record it - all on `conn`, which is inside an open transaction."""
    n = conn.execute("SELECT count(*) AS n FROM serving.pipeline_runs WHERE source = 'demo-live'").fetchone()["n"]
    if n >= app.state.settings.demo_history_cap:
        raise HTTPException(429, "Demo run history is full; it is cleared when the demo data is republished.")

    out = _execute(body)
    run, summary = out["run"], out["summary"]
    run_id = f"demo_{summary['run_id'][4:]}"
    log = _log_excerpt({**run, "run_id": run_id}, out["tasks"])
    conn.execute(
        """INSERT INTO serving.pipeline_runs (run_id, pipeline, batch_id, source, status, started_at, ended_at, duration_ms,
               input_count, accepted_count, quarantined_count, duplicate_count, silver_total, retry_count, source_watermark,
               freshness_lag_hours, error_summary, log_excerpt)
           VALUES (%s,%s,%s,'demo-live',%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,NULL,%s,%s)""",
        [run_id, run["pipeline"], run["batch_id"], run["status"], run["started_at"], run["ended_at"], run["duration_ms"],
         run["input_count"], run["accepted_count"], run["quarantined_count"], run["duplicate_count"], run["silver_total"],
         run["source_watermark"], run["error_summary"], log])
    for t in out["tasks"]:
        conn.execute("INSERT INTO serving.pipeline_task_runs (run_id, task, status, started_at, ended_at, duration_ms, input_count, "
                     "output_count, retry_count, error_summary) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,%s)",
                     [run_id, t["task"], t["status"], t["started_at"], t["ended_at"], t["duration_ms"], t["input_count"],
                      t["output_count"], t["error_summary"]])
    for c in out["quality"]:
        conn.execute("INSERT INTO serving.quality_check_results (run_id, batch_id, source, layer, check_name, severity, status, "
                     "affected_records, observed, threshold, detail, remediation_status, sample_ref, checked_at) "
                     "VALUES (%s,%s,'demo-live',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     [run_id, c["batch_id"], c["layer"], c["check_name"], c["severity"], c["status"], c["affected_records"],
                      c["observed"], c["threshold"], c["detail"], c["remediation_status"], c["sample_ref"], c["checked_at"]])
    return {
        "run_id": run_id, "source": "demo-live", "status": run["status"], "scenario": body.scenario, "seed": body.seed,
        "users": DEMO_USERS, "counts": {k: run[k] for k in ("input_count", "accepted_count", "quarantined_count", "duplicate_count", "silver_total")},
        "error_summary": run["error_summary"], "quality": summary["quality"],
        "notice": "A real execution of the pipeline on a small synthetic batch in an isolated sandbox. "
                  "It is recorded as 'demo-live' history and does not change the published dashboard metrics.",
    }


def _too_many(wait: int) -> HTTPException:
    return HTTPException(429, "Demo run rate limit reached. Please try again later.", headers={"Retry-After": str(wait)})


@router.post("/run", status_code=201, summary="Run a bounded demo workflow (rate limited)")
def demo_run(body: DemoRunRequest, request: Request):
    app = request.app
    settings = app.state.settings
    pool = app.state.pools.rw
    if pool is None:
        raise HTTPException(503, "Demo runs are not enabled on this deployment.")
    key = client_key(request, settings.trust_proxy_headers, app.state.rate_limit_salt)

    if settings.rate_limit_backend == "postgres":
        # Shared across serverless instances. The quota hit is COMMITTED before the expensive work, so a run that later fails
        # (or is retried in a loop) still counts.
        with pool.connection() as conn, conn.transaction():
            wait = app.state.limiter.check(conn, key)
        if wait is not None:
            raise _too_many(wait)
        with pool.connection() as conn, conn.transaction():
            # Transaction-scoped advisory lock = "one demo run at a time" across ALL instances; released at commit/rollback.
            if not conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", [LOCK_DEMO_RUN]).fetchone()["ok"]:
                raise HTTPException(429, "Another demo run is in progress. Please try again in a few seconds.", headers={"Retry-After": "5"})
            return _record_run(conn, app, body)

    wait = app.state.limiter.check(key)
    if wait is not None:
        raise _too_many(wait)
    if not _LOCK.acquire(blocking=False):
        raise HTTPException(429, "Another demo run is in progress. Please try again in a few seconds.", headers={"Retry-After": "5"})
    try:
        with pool.connection() as conn, conn.transaction():
            return _record_run(conn, app, body)
    finally:
        _LOCK.release()
