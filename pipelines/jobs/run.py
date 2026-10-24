"""Pipeline orchestrator (local path): ingest -> silver -> gold -> reconcile -> publish.

    python -m pipelines.jobs.run --batch data/raw/clean
    python -m pipelines.jobs.run --batch data/raw/dirty --db data/gopulse.duckdb

All data work happens in ONE transaction: if any task or the reconciliation fails, nothing is published and the
previous Gold remains intact. Run + task records are written afterwards in their own transaction so failures
stay observable. Runs recorded here are real local executions (mode='local-real').
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pipelines.jobs.gold import build_gold
from pipelines.jobs.ingest import batch_id_for, ingest_batch
from pipelines.jobs.lakehouse import DEFAULT_DB, DEFAULT_LAKE, connect
from pipelines.jobs.quality import QualityGateError, QualityResult, blocking, persist, run_checks
from pipelines.jobs.silver import build_silver

PIPELINE = "gopulse_daily"
EXPORT_TABLES = ["dim_users", "dim_accounts", "dim_features", "fct_product_events", "fct_agent_interactions",
                 "fct_daily_user_activity", "fct_user_lifecycle", "fct_account_adoption",
                 "agg_product_metrics_daily", "agg_activation_funnel_daily", "agg_retention_cohorts"]


class ReconciliationError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def run_pipeline(batch_dir: Path | str, db_path: Path | str = DEFAULT_DB, lake_dir: Path | str | None = DEFAULT_LAKE,
                 *, fail_after: str | None = None, reference_time: datetime | None = None) -> dict:
    """fail_after: test hook ('ingest'|'silver'|'gold') to simulate a crash mid-run."""
    batch_dir = Path(batch_dir)
    con = connect(db_path)
    run_id, batch_id = f"run_{uuid.uuid4().hex[:12]}", batch_id_for(batch_dir)
    started, t0 = _now(), time.perf_counter()
    tasks: list[dict] = []
    summary: dict = {"run_id": run_id, "batch_id": batch_id}
    status, error = "success", None
    results: list[QualityResult] = []

    def task(name, fn, count_in=None):
        ts, tt = _now(), time.perf_counter()
        rec = {"task": name, "status": "success", "started_at": ts, "input_count": count_in,
               "output_count": None, "error_summary": None}
        tasks.append(rec)
        try:
            out = fn()
            rec["output_count"] = out.pop("_out", None) if isinstance(out, dict) else None
            return out
        except Exception as e:
            rec["status"], rec["error_summary"] = "failed", f"{type(e).__name__}: {e}"[:500]
            raise
        finally:
            rec["ended_at"], rec["duration_ms"] = _now(), int((time.perf_counter() - tt) * 1000)

    try:
        con.begin()
        ing = task("ingest", lambda: {**(r := ingest_batch(con, batch_dir, batch_id)), "_out": r["accepted"]})
        ing.pop("_out", None)
        tasks[-1]["input_count"] = ing["input"]
        if fail_after == "ingest":
            raise RuntimeError("simulated failure after ingest")
        sil = task("silver", lambda: {**(r := build_silver(con, batch_id)), "_out": r["silver_total"]}, ing["accepted"])
        if fail_after == "silver":
            raise RuntimeError("simulated failure after silver")
        gold_counts: dict = {}

        def _gold():
            gold_counts.update(build_gold(con))
            return {"_out": sum(gold_counts.values())}

        task("gold", _gold, sil["silver_total"])
        if fail_after == "gold":
            raise RuntimeError("simulated failure after gold")

        def _reconcile():
            if ing["input"] != ing["accepted"] + ing["quarantined"]:
                raise ReconciliationError(f"input {ing['input']} != accepted {ing['accepted']} + quarantined {ing['quarantined']}")
            if ing["accepted"] != sil["kept_from_batch"] + sil["duplicates"]:
                raise ReconciliationError(
                    f"accepted {ing['accepted']} != silver kept {sil['kept_from_batch']} + duplicates {sil['duplicates']}")
            n_fct = con.execute("SELECT count(*) FROM fct_product_events").fetchone()[0]
            if n_fct != sil["silver_total"]:
                raise ReconciliationError(f"fct_product_events {n_fct} != silver_total {sil['silver_total']}")
            return {"_out": n_fct}

        task("reconcile", _reconcile, ing["input"])

        def _quality():
            results.extend(run_checks(con, batch_id, ing, sil, reference_time=reference_time))
            bad = blocking(results)
            if bad:
                raise QualityGateError(bad)
            return {"_out": len(results)}

        task("quality", _quality, sil["silver_total"])
        wm = con.execute("SELECT max(ingested_at) FROM silver_events").fetchone()[0]
        wm = wm.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if wm else None
        con.execute("INSERT OR REPLACE INTO pipeline_watermark VALUES ('silver_max_ingested_at', ?, ?)", [wm, _now()])
        con.commit()
        summary.update(ingest=ing, silver=sil, gold=gold_counts, watermark=wm)
    except QualityGateError as e:
        con.rollback()
        status, error = "failed", f"QualityGateError: {e}"[:500]
        summary["error"] = error
        summary.update(ingest=ing, silver=sil)
        for t in tasks:
            if t["status"] == "success":
                t["status"] = "rolled_back"
    except Exception as e:
        con.rollback()
        status, error = "failed", f"{type(e).__name__}: {e}"[:500]
        summary["error"] = error
        for t in tasks:  # tasks that ran inside the rolled-back txn did not publish anything
            if t["status"] == "success":
                t["status"] = "rolled_back"

    ended = _now()
    ing_s, sil_s = summary.get("ingest") or {}, summary.get("silver") or {}
    con.begin()
    con.execute(
        "INSERT INTO pipeline_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [run_id, PIPELINE, batch_id, "local-real", status, started, ended, int((time.perf_counter() - t0) * 1000),
         ing_s.get("input"), ing_s.get("accepted"), ing_s.get("quarantined"), sil_s.get("duplicates"),
         sil_s.get("silver_total"), 0, summary.get("watermark"), json.dumps(summary.get("gold") or {}), error])
    persist(con, run_id, batch_id, results, blocked=status == "failed")
    for t in tasks:
        con.execute("INSERT INTO pipeline_task_runs VALUES (?,?,?,?,?,?,?,?,?)",
                    [run_id, t["task"], t["status"], t["started_at"], t.get("ended_at"), t.get("duration_ms"),
                     t["input_count"], t["output_count"], t["error_summary"]])
    con.commit()

    if status == "success" and lake_dir:
        out = Path(lake_dir) / "gold"
        out.mkdir(parents=True, exist_ok=True)
        for tbl in EXPORT_TABLES:
            con.execute(f"COPY {tbl} TO '{(out / (tbl + '.parquet')).as_posix()}' (FORMAT PARQUET)")
    con.close()
    summary["status"] = status
    summary["quality"] = {"checks": len(results), "failed": sum(r.status == "fail" for r in results),
                          "blocking": [r.check_name for r in blocking(results)]}
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--batch", type=Path, required=True)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--lake", type=lambda v: Path(v) if v else None, default=DEFAULT_LAKE)
    a = p.parse_args(argv)
    s = run_pipeline(a.batch, a.db, a.lake or None)
    print(json.dumps(s, indent=2, default=str))
    raise SystemExit(0 if s["status"] == "success" else 1)


if __name__ == "__main__":
    main()
