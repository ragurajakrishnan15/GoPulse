"""Orchestration behaviour, tested without Airflow (the DAG only wires these functions together)."""
import json
from datetime import date

import pytest

from orchestration.tasks import (
    Config,
    NonRetryableError,
    TransientError,
    check_window,
    classify,
    prepare_batch,
    run_lakehouse,
)
from pipelines.generators.generate import Config as GenConfig
from pipelines.generators.generate import generate
from pipelines.jobs.daily_batches import available_days, ensure_dataset, write_daily_batch
from pipelines.jobs.lakehouse import connect
from quality.integration_tests.test_pipeline import fingerprint

SMALL = {"users": 150, "seed": 9, "days": 30}


def cfg(tmp_path, **kw) -> Config:
    return Config(work_dir=tmp_path, window_start=date(2026, 1, 1), window_end=date(2026, 3, 8), run_dbt=False, **{**SMALL, **kw})


def run_day(c: Config, ds: str) -> dict:
    out = prepare_batch(ds, c)
    return run_lakehouse(out["batch_dir"], c)


def test_daily_batches_partition_the_stream_by_ingestion_date(tmp_path):
    root = ensure_dataset(tmp_path, **SMALL)
    _, _, _, events, _ = generate(GenConfig(users=SMALL["users"], days=SMALL["days"], seed=SMALL["seed"]))
    days = available_days(tmp_path, **SMALL)
    total = 0
    late_in_later_batch = 0
    for d in days:
        for line in (root / "by_day" / f"{d}.jsonl").read_text().splitlines():
            e = json.loads(line)
            assert e["ingested_at"][:10] == d
            total += 1
            late_in_later_batch += e["event_timestamp"][:10] < d
    assert total == len(events)
    assert late_in_later_batch > 0, "late events must arrive in a later day's batch"
    assert ensure_dataset(tmp_path, **SMALL) == root  # cached, not regenerated


def test_empty_day_yields_empty_batch(tmp_path):
    out = write_daily_batch("2030-01-01", tmp_path / "b", cache_dir=tmp_path / "c", **SMALL)
    assert out["events"] == 0


def test_bounded_window_rejects_dates_outside_the_dataset(tmp_path):
    c = cfg(tmp_path)
    assert check_window("2026-01-05", c)["ds"] == "2026-01-05"
    for bad in ("2025-12-31", "2026-03-09", "2030-01-01"):
        with pytest.raises(NonRetryableError, match="outside the allowed window"):
            check_window(bad, c)
    with pytest.raises(ValueError):
        check_window("not-a-date", c)


def test_backfill_day_by_day_equals_one_full_load(tmp_path):
    """The core claim: processing 14 daily deliveries (incl. late arrivals) == loading their union at once."""
    c = cfg(tmp_path / "inc")
    days = available_days(c.work_dir / "dataset_cache", **SMALL)[:14]
    for d in days:
        assert run_day(c, d)["silver_total"] > 0

    root = ensure_dataset(c.work_dir / "dataset_cache", **SMALL)
    full = tmp_path / "full_batch"
    full.mkdir()
    for n in ("users", "accounts", "features"):
        (full / f"{n}.jsonl").write_text((root / f"{n}.jsonl").read_text())
    (full / "events.jsonl").write_text("".join((root / "by_day" / f"{d}.jsonl").read_text() for d in days))
    from pipelines.jobs.run import run_pipeline
    assert run_pipeline(full, tmp_path / "full.duckdb", None)["status"] == "success"

    a, b = fingerprint(c.db), fingerprint(tmp_path / "full.duckdb")
    for t in ("fct_product_events", "fct_daily_user_activity", "fct_agent_interactions", "fct_user_lifecycle",
              "agg_product_metrics_daily", "agg_activation_funnel_daily", "agg_retention_cohorts", "dim_users"):
        assert a[t] == b[t], t

    con = connect(c.db)
    runs = con.execute("SELECT count(*) FROM pipeline_runs WHERE status = 'success'").fetchone()[0]
    assert runs == 14
    assert con.execute("SELECT count(*) FROM silver_events WHERE is_late").fetchone()[0] > 0


def test_rerunning_a_day_is_idempotent_and_does_not_disturb_other_days(tmp_path):
    c = cfg(tmp_path)
    days = available_days(c.work_dir / "dataset_cache", **SMALL)[:6]
    for d in days:
        run_day(c, d)
    before = fingerprint(c.db)
    run_day(c, days[2])  # replay a middle day
    assert fingerprint(c.db) == before


def test_replaying_a_backfill_range_twice_is_a_no_op(tmp_path):
    c = cfg(tmp_path)
    days = available_days(c.work_dir / "dataset_cache", **SMALL)[:5]
    for d in days:
        run_day(c, d)
    once = fingerprint(c.db)
    for d in days:
        run_day(c, d)
    assert fingerprint(c.db) == once


def test_quality_gate_failure_is_not_retryable_and_leaves_gold_untouched(tmp_path):
    good = cfg(tmp_path)
    days = available_days(good.work_dir / "dataset_cache", **SMALL)
    run_day(good, days[0])
    before = fingerprint(good.db)
    # Same stream but 50% defective deliveries -> blocked by the quarantine gate.
    bad = cfg(tmp_path, defect_rate=0.5)
    out = prepare_batch(days[1], bad)
    with pytest.raises(NonRetryableError, match="QualityGateError"):
        run_lakehouse(out["batch_dir"], bad)
    assert fingerprint(good.db) == before


def test_transient_failure_is_retryable_and_succeeds_on_second_attempt(tmp_path, monkeypatch):
    c = cfg(tmp_path)
    monkeypatch.setenv("GOPULSE_CHAOS", f"transient_once:run_lakehouse:{tmp_path / 'flag'}")
    day = available_days(c.work_dir / "dataset_cache", **SMALL)[0]
    out = prepare_batch(day, c)
    with pytest.raises(TransientError, match="injected"):
        run_lakehouse(out["batch_dir"], c)
    assert run_lakehouse(out["batch_dir"], c)["silver_total"] > 0  # the retry


@pytest.mark.parametrize("exc,expected", [
    (TimeoutError("t"), TransientError),
    (ConnectionError("c"), TransientError),
    (type("IOException", (Exception,), {})("Could not set lock on file"), TransientError),
    (type("OperationalError", (Exception,), {})("server closed the connection"), TransientError),
    (ValueError("bad"), NonRetryableError),
    (KeyError("x"), NonRetryableError),
    (RuntimeError("QualityGateError: nope"), NonRetryableError),
])
def test_exception_classification(exc, expected):
    assert classify(exc) is expected


def test_config_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GOPULSE_WORK_DIR", str(tmp_path))
    monkeypatch.setenv("GOPULSE_USERS", "77")
    monkeypatch.setenv("GOPULSE_RUN_DBT", "0")
    monkeypatch.delenv("DATABASE_URL_OWNER", raising=False)
    c = Config.from_env()
    assert (c.users, c.run_dbt, c.publish_url, c.db) == (77, False, None, tmp_path / "gopulse.duckdb")
