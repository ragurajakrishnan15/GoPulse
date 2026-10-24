"""Structural checks on the Databricks bundle + notebooks. These do NOT prove the job runs on Databricks."""
import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "databricks" / "databricks.yml"


def job():
    return yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))["resources"]["jobs"]["gopulse_daily"]


def test_task_graph_is_a_linear_acyclic_chain():
    tasks = {t["task_key"]: t for t in job()["tasks"]}
    assert list(tasks) == ["ingest", "silver", "gold", "quality_publish"]
    seen: set[str] = set()
    for key, t in tasks.items():
        deps = [d["task_key"] for d in t.get("depends_on", [])]
        assert set(deps) <= seen, f"{key} depends on a task that has not run yet"
        seen.add(key)


def test_every_notebook_exists_and_is_valid_python():
    for t in job()["tasks"]:
        path = BUNDLE.parent / t["notebook_task"]["notebook_path"]
        assert path.exists(), path
        src = path.read_text(encoding="utf-8")
        assert src.startswith("# Databricks notebook source")
        ast.parse(src)  # syntax only; `spark` / `dbutils` exist only on Databricks


def test_retries_only_where_failures_are_recoverable():
    retries = {t["task_key"]: t.get("max_retries", 0) for t in job()["tasks"]}
    assert retries["ingest"] == 2 and not any(v for k, v in retries.items() if k != "ingest")


def test_cost_and_safety_controls():
    j = job()
    assert j["schedule"]["pause_status"] == "PAUSED"          # never starts spending on deploy
    assert j["max_concurrent_runs"] == 1
    assert all(t["job_cluster_key"] == "small" for t in j["tasks"])  # job clusters terminate with the run
    cluster = j["job_clusters"][0]["new_cluster"]
    assert cluster["num_workers"] <= 2
    assert "autoscale" not in cluster


def test_parameters_flow_between_tasks_via_task_values():
    tasks = {t["task_key"]: t["notebook_task"]["base_parameters"] for t in job()["tasks"]}
    assert tasks["silver"]["batch_id"] == "{{tasks.ingest.values.batch_id}}"
    # every value the publish task reconciles on is produced by an upstream notebook
    produced = {"ingest": {"input", "accepted", "quarantined", "batch_id"}, "silver": {"kept_from_batch", "duplicates", "silver_total"},
                "gold": {"gold_counts"}}
    for name, value in tasks["quality_publish"].items():
        if value.startswith("{{tasks."):
            _, task, _, key = value.strip("{}").split(".")
            assert key in produced[task], (name, value)
    # the notebooks that produce task values actually publish them
    for nb in ("01_ingest.py", "02_silver.py", "03_gold.py"):
        assert "taskValues.set" in (BUNDLE.parent / "notebooks" / nb).read_text(encoding="utf-8"), nb


def test_no_secrets_in_bundle_or_notebooks():
    files = [BUNDLE, *(BUNDLE.parent / "notebooks").glob("*.py")]
    for f in files:
        text = f.read_text(encoding="utf-8").lower()
        for bad in ("dapi", "password", "access_token", "bearer "):
            assert bad not in text, (f.name, bad)
