"""PySpark/Delta implementation vs the DuckDB pipeline: same input -> same Silver and Gold, row for row.

Runs for real in local Spark mode (Linux container; see databricks/README.md). NOT executed on Databricks.
"""
from datetime import UTC, date, datetime
from decimal import Decimal

import duckdb
import pytest

from orchestration.tasks import Config, prepare_batch
from pipelines.generators.generate import Config as GenConfig
from pipelines.generators.generate import generate, write_jsonl
from pipelines.jobs.daily_batches import available_days
from pipelines.jobs.run import run_pipeline
from pipelines.spark import spark_pipeline as sp

NOW = datetime(2026, 12, 31, tzinfo=UTC)  # fixed clock so the "future timestamp" rule is deterministic
TABLES = ["dim_accounts", "dim_features", "dim_users", "fct_product_events", "fct_agent_interactions", "fct_daily_user_activity",
          "fct_user_lifecycle", "agg_activation_funnel_daily", "agg_retention_cohorts", "agg_product_metrics_daily"]


@pytest.fixture(scope="session")
def spark():
    s = sp.get_spark()
    yield s
    s.stop()


def norm(v):
    if isinstance(v, (Decimal, float)):
        return round(float(v), 3)
    if isinstance(v, bool) or v is None or isinstance(v, (str, date, datetime)):
        return v
    if isinstance(v, int):
        return float(v)
    return v


def rows(sorted_rows):
    return sorted((tuple(norm(x) for x in r) for r in sorted_rows), key=lambda t: tuple((x is None, str(x)) for x in t))


def spark_rows(lake, schema, table, cols):
    return rows([tuple(r[c] for c in cols) for r in lake.read(schema, table).select(*cols).collect()])


def duck_rows(db, table, source_schema="main"):
    con = duckdb.connect(str(db), read_only=True)
    cur = con.execute(f"SELECT * FROM {source_schema}.{table}")
    cols = [d[0] for d in cur.description]
    out = rows(cur.fetchall())
    con.close()
    return cols, out


def assert_same(lake, db, tables=TABLES):
    for t in tables:
        cols, expected = duck_rows(db, t)
        got = spark_rows(lake, "gold", t, cols)
        assert len(got) == len(expected), f"{t}: {len(got)} rows vs {len(expected)}"
        assert got == expected, f"{t} differs"


def write_batch(path, cfg: GenConfig):
    u, a, f, e, _ = generate(cfg)
    path.mkdir(parents=True, exist_ok=True)
    for name, data in (("users", u), ("accounts", a), ("features", f), ("events", e)):
        write_jsonl(path / f"{name}.jsonl", data)
    return e


def test_dirty_batch_matches_duckdb_end_to_end(spark, tmp_path):
    write_batch(tmp_path / "b", GenConfig(users=120, accounts=15, days=30, seed=21, defect_rate=0.03))
    duck = run_pipeline(tmp_path / "b", tmp_path / "d.duckdb", None)
    lake = sp.Lake(spark, root=str(tmp_path / "lake"))
    s = sp.run(lake, tmp_path / "b", now=NOW)

    # ingestion + silver counters agree
    assert (s["ingest"]["input"], s["ingest"]["accepted"], s["ingest"]["quarantined"]) == (
        duck["ingest"]["input"], duck["ingest"]["accepted"], duck["ingest"]["quarantined"])
    assert s["silver"]["duplicates"] == duck["silver"]["duplicates"] > 0
    assert s["silver"]["silver_total"] == duck["silver"]["silver_total"]

    # quarantine reasons agree
    con = duckdb.connect(str(tmp_path / "d.duckdb"), read_only=True)
    expected = dict(con.execute("SELECT r, count(*) FROM (SELECT unnest(reasons) r FROM quarantine_events) GROUP BY r").fetchall())
    from pyspark.sql import functions as F
    got = {r["r"]: r["n"] for r in lake.read("bronze", "quarantine_events").select(F.explode("reasons").alias("r")).groupBy("r").agg(F.count("*").alias("n")).collect()}
    assert got == expected and len(expected) == 6

    # silver row-for-row, then every gold table
    cols, silver_expected = duck_rows(tmp_path / "d.duckdb", "silver_events")
    assert spark_rows(lake, "silver", "events", cols) == silver_expected
    assert_same(lake, tmp_path / "d.duckdb")


def test_five_daily_batches_with_late_events_match_duckdb(spark, tmp_path):
    """Incremental processing parity, including partition rebuilds caused by late arrivals."""
    c = Config(work_dir=tmp_path / "w", users=120, seed=3, days=30, run_dbt=False)
    days = available_days(c.work_dir / "dataset_cache", users=120, seed=3, days=30)[:7]
    lake = sp.Lake(spark, root=str(tmp_path / "lake"))
    for d in days:
        batch = prepare_batch(d, c)["batch_dir"]
        assert run_pipeline(batch, tmp_path / "d.duckdb", None)["status"] == "success"
        sp.run(lake, batch, now=NOW)
    assert_same(lake, tmp_path / "d.duckdb")
    from pyspark.sql import functions as F
    assert lake.read("silver", "events").filter(F.col("is_late")).count() > 0


def test_rerun_is_idempotent(spark, tmp_path):
    write_batch(tmp_path / "b", GenConfig(users=80, accounts=10, days=20, seed=5, defect_rate=0.02))
    lake = sp.Lake(spark, root=str(tmp_path / "lake"))
    sp.run(lake, tmp_path / "b", now=NOW)
    first = {t: spark_rows(lake, "gold", t, lake.read("gold", t).columns) for t in TABLES}
    bronze = lake.read("bronze", "events").count()
    sp.run(lake, tmp_path / "b", now=NOW)
    assert lake.read("bronze", "events").count() == bronze
    assert {t: spark_rows(lake, "gold", t, lake.read("gold", t).columns) for t in TABLES} == first


def test_toxic_batch_is_blocked_before_any_write_and_gold_is_untouched(spark, tmp_path):
    write_batch(tmp_path / "ok", GenConfig(users=80, accounts=10, days=20, seed=5))
    lake = sp.Lake(spark, root=str(tmp_path / "lake"))
    sp.run(lake, tmp_path / "ok", now=NOW)
    before = {t: spark_rows(lake, "gold", t, lake.read("gold", t).columns) for t in TABLES}
    bronze_before = lake.read("bronze", "events").count()

    write_batch(tmp_path / "bad", GenConfig(users=80, accounts=10, days=20, seed=5, defect_rate=0.5))
    with pytest.raises(sp.QualityGateError, match="quarantine_rate_blocking"):
        sp.run(lake, tmp_path / "bad", now=NOW)
    assert lake.read("bronze", "events").count() == bronze_before
    assert {t: spark_rows(lake, "gold", t, lake.read("gold", t).columns) for t in TABLES} == before


def test_malformed_lines_are_quarantined(spark, tmp_path):
    write_batch(tmp_path / "b", GenConfig(users=60, accounts=8, days=15, seed=2))
    with (tmp_path / "b" / "events.jsonl").open("a") as f:
        f.write("{not json\n[1,2]\n")
    lake = sp.Lake(spark, root=str(tmp_path / "lake"))
    s = sp.ingest(lake, tmp_path / "b", now=NOW)
    assert s["quarantined"] == 2
