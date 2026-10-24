import pytest

from pipelines.generators.generate import Config
from pipelines.jobs.dbt_runner import persist_dbt_results, run_dbt_build
from pipelines.jobs.failure_demo import run_demo
from pipelines.jobs.lakehouse import connect
from pipelines.jobs.run import run_pipeline
from quality.integration_tests.test_pipeline import write_batch


def results(db, run_id):
    con = connect(db)
    rows = con.execute("SELECT check_name, severity, status, affected_records, remediation_status "
                       "FROM quality_check_results WHERE run_id = ?", [run_id]).fetchall()
    con.close()
    return {r[0]: r for r in rows}


def test_clean_batch_has_no_failed_checks(tmp_path):
    write_batch(tmp_path / "b", Config(users=150, accounts=20, days=30, seed=5))
    s = run_pipeline(tmp_path / "b", tmp_path / "db.duckdb", None)
    res = results(tmp_path / "db.duckdb", s["run_id"])
    assert s["status"] == "success" and len(res) >= 20
    assert [k for k, v in res.items() if v[2] == "fail"] == []
    assert res["freshness"][2] == "skipped" and res["volume_anomaly"][2] == "skipped"


def test_dirty_batch_checks_match_injected_defects(tmp_path):
    cfg = Config(users=200, accounts=25, days=30, seed=5, defect_rate=0.03)
    _, truth = write_batch(tmp_path / "b", cfg)
    s = run_pipeline(tmp_path / "b", tmp_path / "db.duckdb", None)
    res = results(tmp_path / "db.duckdb", s["run_id"])
    by_kind = {}
    for k in truth.values():
        by_kind[k] = by_kind.get(k, 0) + 1
    assert s["status"] == "success" and s["quality"]["blocking"] == []
    for reason in ("missing_user_id", "invalid_timestamp", "unknown_event_name", "orphaned_account",
                   "schema_mismatch", "negative_value"):
        assert res[reason][2] == "fail" and res[reason][3] == by_kind[reason] and res[reason][4] == "quarantined"
    assert res["duplicate_event_ids"][3] == by_kind["duplicate_event_id"]
    assert res["duplicate_event_ids"][4] == "deduplicated"


def test_toxic_batch_is_blocked_and_gold_is_untouched(tmp_path):
    db = tmp_path / "db.duckdb"
    write_batch(tmp_path / "ok", Config(users=150, accounts=20, days=30, seed=5))
    run_pipeline(tmp_path / "ok", db, None)
    con = connect(db)
    before = con.execute("SELECT count(*), max(event_ts) FROM fct_product_events").fetchone()
    con.close()

    write_batch(tmp_path / "bad", Config(users=150, accounts=20, days=30, seed=5, defect_rate=0.5))
    s = run_pipeline(tmp_path / "bad", db, None)
    assert s["status"] == "failed" and s["quality"]["blocking"] == ["quarantine_rate_blocking"]
    assert "QualityGateError" in s["error"]

    con = connect(db)
    assert con.execute("SELECT count(*), max(event_ts) FROM fct_product_events").fetchone() == before
    run = con.execute("SELECT status, error_summary FROM pipeline_runs WHERE run_id = ?", [s["run_id"]]).fetchone()
    assert run[0] == "failed" and "quarantine" in run[1]
    r = results(db, s["run_id"])
    assert r["quarantine_rate_blocking"][4] == "blocked_publish"
    con.close()


def test_volume_anomaly_flags_small_batch(tmp_path):
    db = tmp_path / "db.duckdb"
    for i, seed in enumerate((1, 2, 3)):
        write_batch(tmp_path / f"b{i}", Config(users=200, accounts=20, days=30, seed=seed))
        run_pipeline(tmp_path / f"b{i}", db, None)
    write_batch(tmp_path / "tiny", Config(users=20, accounts=20, days=30, seed=9))
    s = run_pipeline(tmp_path / "tiny", db, None)
    assert results(db, s["run_id"])["volume_anomaly"][2] == "fail"


def test_freshness_check_uses_reference_time(tmp_path):
    from datetime import UTC, datetime
    write_batch(tmp_path / "b", Config(users=100, accounts=10, days=20, seed=2))
    s = run_pipeline(tmp_path / "b", tmp_path / "db.duckdb", None, reference_time=datetime(2027, 1, 1, tzinfo=UTC))
    assert results(tmp_path / "db.duckdb", s["run_id"])["freshness"][2] == "fail"


@pytest.mark.slow
def test_dbt_build_passes_and_results_persist(tmp_path):
    db = tmp_path / "db.duckdb"
    write_batch(tmp_path / "b", Config(users=200, accounts=25, days=40, seed=5, defect_rate=0.02))
    run_pipeline(tmp_path / "b", db, None)
    out = run_dbt_build(db)
    assert out["returncode"] == 0, out["stdout_tail"]
    statuses = {r["status"] for r in out["results"]}
    assert statuses <= {"success", "pass"}
    n = persist_dbt_results(db, out["results"])
    assert n >= 40
    con = connect(db)
    assert con.execute("SELECT count(*) FROM quality_check_results WHERE layer='dbt' AND status='fail'").fetchone()[0] == 0
    # rates are NULL (not 0) where the observation window has not elapsed
    assert con.execute("SELECT count(*) FROM marts.mart_activation_rates WHERE NOT activation_window_complete "
                       "AND activation_rate IS NOT NULL").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM marts.mart_activation_rates WHERE activation_window_complete "
                       "AND activation_rate IS NOT NULL").fetchone()[0] > 0
    con.close()


@pytest.mark.slow
def test_dbt_tests_actually_catch_corruption(tmp_path):
    db = tmp_path / "db.duckdb"
    write_batch(tmp_path / "b", Config(users=100, accounts=10, days=30, seed=5))
    run_pipeline(tmp_path / "b", db, None)
    con = connect(db)
    con.execute("INSERT INTO dim_users SELECT * FROM dim_users LIMIT 1")  # duplicate PK in a published table
    con.close()
    out = run_dbt_build(db)
    assert out["returncode"] != 0
    assert any(r["status"] in ("fail", "error") for r in out["results"])


def test_failure_demo_end_to_end(tmp_path):
    r = run_demo(tmp_path, users=150, seed=7)
    s1, s2, s3 = r["step_1_dirty_batch"], r["step_2_toxic_batch"], r["step_3_recovery_rerun"]
    assert s1["status"] == "success" and s1["duplicates_removed"] > 0 and s1["ingest"]["quarantined"] > 0
    assert s1["naive_metrics_no_cleaning"]["events"] > s1["published_metrics_after_cleaning"]["events"]
    assert s2["status"] == "failed" and s2["gold_unchanged_after_block"]
    assert s3["status"] == "success" and s3["converged_to_reference"]
