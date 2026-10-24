"""Run `dbt build` against the lakehouse DB and persist dbt test outcomes into quality_check_results (layer='dbt').

    python -m pipelines.jobs.dbt_runner [--db data/gopulse.duckdb]

dbt only runs after a successful pipeline run (the models read the freshly published Gold tables).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from pipelines.jobs.lakehouse import DEFAULT_DB, connect

DBT_DIR = Path("transformations/dbt")


def dbt_target_dirs(project_dir: Path = DBT_DIR) -> tuple[Path, Path]:
    """(target dir, log dir). GOPULSE_DBT_TARGET_DIR relocates both - needed when the repo is mounted read-only or owned by
    another user (containers, CI): dbt otherwise exits silently with code 2 because it cannot create ./target and ./logs."""
    base = os.environ.get("GOPULSE_DBT_TARGET_DIR")
    return (Path(base) / "target", Path(base) / "logs") if base else (project_dir / "target", project_dir / "logs")


def run_dbt_build(db_path: Path | str = DEFAULT_DB, project_dir: Path = DBT_DIR, *, select: str | None = None) -> dict:
    env = {**os.environ, "GOPULSE_DB": str(Path(db_path).resolve()), "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
    # GOPULSE_DBT_PYTHON lets orchestrators run dbt from an isolated virtualenv (avoids dependency clashes with Airflow).
    cmd = [os.environ.get("GOPULSE_DBT_PYTHON", sys.executable), "-m", "dbt.cli.main", "build", "--project-dir", str(project_dir), "--profiles-dir", str(project_dir)]
    if select:
        cmd += ["--select", select]
    target_dir, log_dir = dbt_target_dirs(project_dir)
    if os.environ.get("GOPULSE_DBT_TARGET_DIR"):
        cmd += ["--target-path", str(target_dir), "--log-path", str(log_dir)]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    results_file = target_dir / "run_results.json"
    results = json.loads(results_file.read_text(encoding="utf-8"))["results"] if results_file.exists() else []
    return {"returncode": proc.returncode, "results": results, "stdout_tail": proc.stdout[-1500:], "stderr_tail": proc.stderr[-500:]}


def persist_dbt_results(db_path: Path | str, results: list[dict]) -> int:
    """Attach dbt test results to the most recent successful pipeline run."""
    con = connect(db_path)
    row = con.execute("SELECT run_id, batch_id FROM pipeline_runs WHERE status = 'success' ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        con.close()
        return 0
    run_id, batch_id = row
    now = datetime.now(UTC).replace(tzinfo=None)
    con.begin()
    con.execute("DELETE FROM quality_check_results WHERE run_id = ? AND layer = 'dbt'", [run_id])
    n = 0
    for r in results:
        node = r["unique_id"]
        if not node.startswith("test."):
            continue
        status = {"pass": "pass", "fail": "fail", "warn": "fail", "error": "fail", "skipped": "skipped"}.get(r["status"], "fail")
        severity = "warn" if r["status"] == "warn" else "error"
        failures = int(r.get("failures") or 0)
        name = node.split(".")[2]
        con.execute(
            "INSERT INTO quality_check_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [run_id, batch_id, "dbt", name[:120], severity, status, failures, float(failures), 0,
             (r.get("message") or r["status"])[:300], "open" if status == "fail" else "none", f"dbt:{node}", now])
        n += 1
    con.commit()
    con.close()
    return n


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = p.parse_args(argv)
    out = run_dbt_build(a.db)
    n = persist_dbt_results(a.db, out["results"])
    statuses: dict[str, int] = {}
    for r in out["results"]:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    print(json.dumps({"dbt_returncode": out["returncode"], "nodes": statuses, "tests_persisted": n}, indent=2))
    raise SystemExit(out["returncode"])


if __name__ == "__main__":
    main()
