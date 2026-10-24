import hashlib
import json

import pytest

from pipelines.generators.generate import Config, generate, write_jsonl
from pipelines.jobs.lakehouse import connect
from pipelines.jobs.run import run_pipeline

GOLD = ["dim_users", "dim_accounts", "fct_product_events", "fct_agent_interactions", "fct_daily_user_activity",
        "fct_user_lifecycle", "fct_account_adoption", "agg_product_metrics_daily", "agg_activation_funnel_daily",
        "agg_retention_cohorts"]


def write_batch(path, cfg, events=None):
    users, accounts, features, evs, truth = generate(cfg)
    path.mkdir(parents=True, exist_ok=True)
    write_jsonl(path / "users.jsonl", users)
    write_jsonl(path / "accounts.jsonl", accounts)
    write_jsonl(path / "features.jsonl", features)
    write_jsonl(path / "events.jsonl", evs if events is None else events)
    return evs, truth


def fingerprint(db):
    con = connect(db)
    out = {}
    for t in GOLD:
        rows = con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall()
        out[t] = hashlib.sha256(repr(rows).encode()).hexdigest()
    con.close()
    return out


@pytest.fixture
def cfg():
    return Config(users=200, accounts=25, days=30, seed=11)


def test_clean_run_reconciles(tmp_path, cfg):
    evs, _ = write_batch(tmp_path / "b", cfg)
    s = run_pipeline(tmp_path / "b", tmp_path / "db.duckdb", None)
    assert s["status"] == "success"
    assert s["ingest"]["quarantined"] == 0 and s["silver"]["duplicates"] == 0
    assert s["silver"]["silver_total"] == len(evs) == s["gold"]["fct_product_events"]


def test_dirty_batch_quarantined_and_deduped(tmp_path):
    cfg = Config(users=200, accounts=25, days=30, seed=11, defect_rate=0.03)
    evs, truth = write_batch(tmp_path / "b", cfg)
    s = run_pipeline(tmp_path / "b", tmp_path / "db.duckdb", None)
    n_dup = sum(1 for k in truth.values() if k == "duplicate_event_id")
    n_bad = len(truth) - n_dup
    assert s["ingest"]["quarantined"] == n_bad
    assert s["silver"]["duplicates"] == n_dup
    assert s["ingest"]["input"] == len(evs)
    con = connect(tmp_path / "db.duckdb")
    reasons = {r[0] for r in con.execute("SELECT unnest(reasons) FROM quarantine_events").fetchall()}
    assert reasons == {"missing_user_id", "invalid_timestamp", "unknown_event_name", "orphaned_account",
                       "schema_mismatch", "negative_value"}
    assert con.execute("SELECT count(*) - count(DISTINCT event_id) FROM silver_events").fetchone()[0] == 0


def test_rerun_is_idempotent(tmp_path):
    cfg = Config(users=150, accounts=20, days=30, seed=3, defect_rate=0.02)
    write_batch(tmp_path / "b", cfg)
    db = tmp_path / "db.duckdb"
    run_pipeline(tmp_path / "b", db, None)
    first = fingerprint(db)
    run_pipeline(tmp_path / "b", db, None)
    assert fingerprint(db) == first
    con = connect(db)
    assert con.execute("SELECT count(*) FROM pipeline_runs").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM bronze_events").fetchone()[0] == con.execute(
        "SELECT accepted_count FROM pipeline_runs LIMIT 1").fetchone()[0]


def test_incremental_batches_equal_single_load(tmp_path, cfg):
    """Splitting events across two batches (with overlap duplicates and late arrivals) matches a single load."""
    users, accounts, features, evs, _ = generate(cfg)
    mid = len(evs) // 2
    overlap = evs[mid - 200: mid]  # re-delivered in batch 2 => cross-batch duplicates
    b1, b2 = evs[:mid], overlap + evs[mid:]
    for name, part in (("b1", b1), ("b2", b2)):
        write_batch(tmp_path / name, cfg, events=part)
    write_batch(tmp_path / "all", cfg)

    run_pipeline(tmp_path / "b1", tmp_path / "inc.duckdb", None)
    s2 = run_pipeline(tmp_path / "b2", tmp_path / "inc.duckdb", None)
    run_pipeline(tmp_path / "all", tmp_path / "full.duckdb", None)

    assert s2["status"] == "success" and s2["silver"]["duplicates"] == 200
    a, b = fingerprint(tmp_path / "inc.duckdb"), fingerprint(tmp_path / "full.duckdb")
    for t in ("fct_product_events", "fct_daily_user_activity", "fct_agent_interactions", "agg_product_metrics_daily"):
        assert a[t] == b[t], t


def test_late_events_rebuild_old_partition(tmp_path, cfg):
    users, accounts, features, evs, _ = generate(cfg)
    on_day = lambda e: e["event_timestamp"][:10]
    day = on_day(evs[len(evs) // 2])
    held = [e for e in evs if on_day(e) == day][:5]
    held_ids = {e["event_id"] for e in held}
    write_batch(tmp_path / "b1", cfg, events=[e for e in evs if e["event_id"] not in held_ids])
    write_batch(tmp_path / "b2", cfg, events=held)
    db = tmp_path / "db.duckdb"
    run_pipeline(tmp_path / "b1", db, None)
    con = connect(db)
    before = con.execute("SELECT count(*) FROM silver_events WHERE activity_date = ?", [day]).fetchone()[0]
    con.close()
    s = run_pipeline(tmp_path / "b2", db, None)
    con = connect(db)
    after = con.execute("SELECT count(*) FROM silver_events WHERE activity_date = ?", [day]).fetchone()[0]
    assert s["status"] == "success" and after == before + 5


def test_failed_run_rolls_back_and_keeps_previous_gold(tmp_path, cfg):
    write_batch(tmp_path / "b1", cfg)
    db = tmp_path / "db.duckdb"
    run_pipeline(tmp_path / "b1", db, None)
    good = fingerprint(db)

    cfg2 = Config(users=200, accounts=25, days=30, seed=99)
    write_batch(tmp_path / "b2", cfg2)
    s = run_pipeline(tmp_path / "b2", db, None, fail_after="gold")
    assert s["status"] == "failed"
    assert fingerprint(db) == good
    con = connect(db)
    run = con.execute("SELECT status, error_summary FROM pipeline_runs WHERE status='failed'").fetchone()
    assert run and "simulated" in run[1]
    tasks = {r[0]: r[1] for r in con.execute(
        "SELECT task, status FROM pipeline_task_runs WHERE run_id = ?", [s["run_id"]]).fetchall()}
    assert set(tasks.values()) <= {"rolled_back", "failed"}
    assert con.execute("SELECT count(*) FROM bronze_events WHERE batch_id = ?", [s["batch_id"]]).fetchone()[0] == 0


def test_malformed_json_line_is_quarantined(tmp_path, cfg):
    _, _ = write_batch(tmp_path / "b", cfg)
    with (tmp_path / "b" / "events.jsonl").open("a") as f:
        f.write("{not json\n[1,2]\n")
    s = run_pipeline(tmp_path / "b", tmp_path / "db.duckdb", None)
    assert s["status"] == "success" and s["ingest"]["quarantined"] == 2


def test_metric_sanity(tmp_path, cfg):
    write_batch(tmp_path / "b", cfg)
    run_pipeline(tmp_path / "b", tmp_path / "db.duckdb", None)
    con = connect(tmp_path / "db.duckdb")
    # Retention numerators never exceed denominators; rates in [0,1]
    assert con.execute("SELECT count(*) FROM agg_retention_cohorts WHERE retained_users > cohort_users").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM agg_activation_funnel_daily WHERE activated_7d > signups "
                       "OR converted_30d > signups").fetchone()[0] == 0
    # DAU reconciles with the activity fact
    total = con.execute("SELECT sum(dau) FROM agg_product_metrics_daily").fetchone()[0]
    assert total == con.execute("SELECT count(*) FROM fct_daily_user_activity").fetchone()[0]
    # success + failure == runs; one row per execution
    assert con.execute("SELECT count(*) - count(DISTINCT execution_id) FROM fct_agent_interactions").fetchone()[0] == 0
    # signups in the aggregate == users
    assert con.execute("SELECT sum(new_signups) FROM agg_product_metrics_daily").fetchone()[0] == 200
    json.loads(con.execute("SELECT output_counts FROM pipeline_runs").fetchone()[0])
