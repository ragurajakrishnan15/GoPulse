"""PySpark + Delta Lake implementation of the GoPulse lakehouse pipeline (Bronze -> Silver -> Gold).

The same logic as the local DuckDB pipeline (pipelines/jobs/*), expressed for Spark. It runs in two modes:

  * path mode   Lake(spark, root="/some/dir")      Delta tables under <root>/<schema>/<table>   (local / CI / tests)
  * catalog mode Lake(spark, catalog="gopulse")    Unity Catalog tables  <catalog>.<schema>.<table>   (Databricks)

Design notes
  * Validation reuses pipelines.ingestion.validate (single source of truth for reason codes). It runs row-wise in
    Python workers; at production volume you would port the rules to vectorised Spark expressions.
  * Delta gives atomicity PER TABLE, not across tables. So: the quarantine-rate gate runs BEFORE any write; Silver is
    rebuilt per touched activity_date partition (`replaceWhere`); Gold is built into a staging schema and promoted
    only after reconciliation passes. A failure therefore never leaves a half-published Gold.
  * Idempotent: Bronze `replaceWhere batch_id`, Silver `replaceWhere activity_date IN (...)`, Gold full overwrite.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

from pipelines.ingestion.validate import validate_event
from pipelines.jobs.ingest import batch_id_for

QUARANTINE_RATE_ERROR = 0.20
LATE_THRESHOLD_MS = 3 * 86_400_000
QUALIFYING = "('feature_opened','suggestion_shown','suggestion_accepted','agent_started','agent_completed','agent_failed')"
GOLD_TABLES = ["dim_accounts", "dim_features", "dim_users", "fct_product_events", "fct_agent_interactions",
               "fct_daily_user_activity", "fct_user_lifecycle", "agg_activation_funnel_daily", "agg_retention_cohorts",
               "agg_product_metrics_daily"]


class QualityGateError(RuntimeError):
    pass


class ReconciliationError(RuntimeError):
    pass


@dataclass
class Lake:
    spark: SparkSession
    root: str | None = None
    catalog: str | None = None

    def _path(self, schema: str, name: str) -> str:
        return str(Path(self.root) / schema / name)  # type: ignore[arg-type]

    def fqn(self, schema: str, name: str) -> str:
        return f"{self.catalog}.{schema}.{name}"

    def exists(self, schema: str, name: str) -> bool:
        if self.catalog:
            return self.spark.catalog.tableExists(self.fqn(schema, name))
        from delta.tables import DeltaTable
        return DeltaTable.isDeltaTable(self.spark, self._path(schema, name))

    def read(self, schema: str, name: str) -> DataFrame:
        if self.catalog:
            return self.spark.table(self.fqn(schema, name))
        return self.spark.read.format("delta").load(self._path(schema, name))

    def write(self, df: DataFrame, schema: str, name: str, *, replace_where: str | None = None,
              partition_by: str | None = None) -> None:
        w = df.write.format("delta").mode("overwrite")
        if replace_where and self.exists(schema, name):
            w = w.option("replaceWhere", replace_where)
        else:
            w = w.option("overwriteSchema", "true")
        if partition_by and not self.exists(schema, name):
            w = w.partitionBy(partition_by)
        if self.catalog:
            self.spark.sql(f"CREATE SCHEMA IF NOT EXISTS {self.catalog}.{schema}")
            w.saveAsTable(self.fqn(schema, name))
        else:
            w.save(self._path(schema, name))


def get_spark(app: str = "gopulse", shuffle_partitions: int = 4) -> SparkSession:
    """Local-mode session with Delta enabled (tests/CI). On Databricks use the provided `spark` instead."""
    from delta import configure_spark_with_delta_pip
    b = (SparkSession.builder.master("local[2]").appName(app)
         .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
         .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
         .config("spark.sql.session.timeZone", "UTC")
         .config("spark.sql.shuffle.partitions", str(shuffle_partitions))
         .config("spark.ui.enabled", "false")
         .config("spark.databricks.delta.snapshotPartitions", "2"))
    return configure_spark_with_delta_pip(b).getOrCreate()


def _ts(col: str) -> F.Column:
    return F.to_timestamp(F.regexp_replace(F.col(col), "Z$", ""))


# --------------------------------------------------------------------------------------- ingest

def load_dimensions(lake: Lake, batch_dir: Path) -> None:
    s = lake.spark
    users = s.read.json(str(batch_dir / "users.jsonl")).select(
        "user_id", "account_id", F.regexp_replace("signup_at", "Z$", "").cast("timestamp").alias("signup_at"), "plan", "country")
    accounts = s.read.json(str(batch_dir / "accounts.jsonl")).select(
        "account_id", "tier", "segment", F.regexp_replace("created_at", "Z$", "").cast("timestamp").alias("created_at"))
    features = s.read.json(str(batch_dir / "features.jsonl")).select("feature_id", "name", "category")
    lake.write(users, "silver", "raw_users")
    lake.write(accounts, "silver", "raw_accounts")
    lake.write(features, "silver", "raw_features")


def ingest(lake: Lake, batch_dir: str | Path, batch_id: str | None = None, now: datetime | None = None) -> dict:
    """Validate the batch and (only if it passes the quarantine-rate gate) persist Bronze + quarantine."""
    spark, batch_dir = lake.spark, Path(batch_dir)
    batch_id = batch_id or batch_id_for(batch_dir)
    now = now or datetime.now(UTC)
    load_dimensions(lake, batch_dir)
    known = {r[0] for r in lake.read("silver", "raw_accounts").select("account_id").collect()}
    bc = spark.sparkContext.broadcast(known)

    def classify(item):
        line, idx = item
        line_no = idx + 1
        try:
            event = json.loads(line)
            reasons = ["schema_mismatch"] if not isinstance(event, dict) else validate_event(event, known_accounts=bc.value, now=now)
        except json.JSONDecodeError:
            event, reasons = None, ["schema_mismatch"]
        if reasons:
            payload = json.dumps(line.strip()) if event is None else json.dumps(event, sort_keys=True)
            return (line_no, None, None, payload, reasons)
        return (line_no, event["event_id"], event["ingested_at"], json.dumps(event, sort_keys=True), None)

    lines = spark.read.text(str(batch_dir / "events.jsonl")).rdd.map(lambda r: r.value).filter(lambda x: x.strip())
    rows = lines.zipWithIndex().map(classify)
    schema = T.StructType([T.StructField("line_no", T.LongType()), T.StructField("event_id", T.StringType()),
                           T.StructField("ingested_at", T.StringType()), T.StructField("payload", T.StringType()),
                           T.StructField("reasons", T.ArrayType(T.StringType()))])
    classified = spark.createDataFrame(rows, schema).cache()
    total = classified.count()
    quarantined_df = classified.filter(F.col("reasons").isNotNull())
    accepted_df = classified.filter(F.col("reasons").isNull())
    n_bad = quarantined_df.count()
    counts = {"input": total, "accepted": total - n_bad, "quarantined": n_bad}

    if total and n_bad / total > QUARANTINE_RATE_ERROR:  # gate BEFORE any write: nothing to roll back
        classified.unpersist()
        raise QualityGateError(f"quarantine_rate_blocking: {n_bad}/{total} records quarantined ({n_bad / total:.1%}); blocking above 20%")

    loaded_at = F.lit(now.replace(tzinfo=None))
    bronze = accepted_df.select(F.lit(batch_id).alias("batch_id"), "line_no", "event_id", "ingested_at", "payload", loaded_at.alias("loaded_at"))
    quarantine = quarantined_df.select(F.lit(batch_id).alias("batch_id"), "line_no", "payload", "reasons", loaded_at.alias("quarantined_at"))
    pred = f"batch_id = '{batch_id}'"
    lake.write(bronze, "bronze", "events", replace_where=pred)
    lake.write(quarantine, "bronze", "quarantine_events", replace_where=pred)
    classified.unpersist()
    return counts | {"batch_id": batch_id}


# --------------------------------------------------------------------------------------- silver
def build_silver(lake: Lake, batch_id: str) -> dict:
    """Rebuild every activity_date partition touched by this batch. Dedup: earliest (ingested_at, batch_id, line_no) wins."""
    p = lambda path: F.get_json_object("payload", path)
    parsed = lake.read("bronze", "events").select(
        "batch_id", "line_no", "event_id",
        p("$.event_name").alias("event_name"),
        F.to_timestamp(F.regexp_replace(p("$.event_timestamp"), "Z$", "")).alias("event_ts"),
        F.to_timestamp(F.regexp_replace(p("$.ingested_at"), "Z$", "")).alias("ingested_at"),
        p("$.user_id").alias("user_id"), p("$.account_id").alias("account_id"), p("$.session_id").alias("session_id"),
        p("$.feature_id").alias("feature_id"), p("$.properties.execution_id").alias("execution_id"),
        p("$.properties.agent_type").alias("agent_type"), p("$.properties.status").alias("status"),
        p("$.properties.failure_category").alias("failure_category"),
        p("$.properties.duration_ms").cast("long").alias("duration_ms"),
        p("$.properties.retry_count").cast("int").alias("retry_count"),
        p("$.properties.input_tokens").cast("long").alias("input_tokens"),
        p("$.properties.output_tokens").cast("long").alias("output_tokens"),
        p("$.properties.cost_usd_estimate").cast("double").alias("cost_usd_estimate"),
        p("$.properties.plan").alias("plan"),
    ).withColumn("activity_date", F.to_date("event_ts"))

    dates = [r[0] for r in parsed.filter(F.col("batch_id") == batch_id).select("activity_date").distinct().collect()]
    from pyspark.sql.window import Window
    w = Window.partitionBy("event_id").orderBy("ingested_at", "batch_id", "line_no")
    stage = parsed.filter(F.col("activity_date").isin(dates)).withColumn("rn", F.row_number().over(w)).cache()
    dups = stage.filter((F.col("rn") > 1) & (F.col("batch_id") == batch_id)).count()
    silver = stage.filter(F.col("rn") == 1).select(
        "event_id", "event_name", "event_ts", "ingested_at", "activity_date", "user_id", "account_id", "session_id", "feature_id",
        ((F.unix_millis("ingested_at") - F.unix_millis("event_ts")) > LATE_THRESHOLD_MS).alias("is_late"),
        "batch_id", "execution_id", "agent_type", "status", "failure_category", "duration_ms", "retry_count",
        "input_tokens", "output_tokens", "cost_usd_estimate", "plan")
    cond = "activity_date IN (" + ",".join(f"'{d}'" for d in sorted(dates)) + ")"
    lake.write(silver, "silver", "events", replace_where=cond, partition_by="activity_date")
    stage.unpersist()
    out = lake.read("silver", "events")
    return {"kept_from_batch": out.filter(F.col("batch_id") == batch_id).count(), "duplicates": dups,
            "partitions_rebuilt": len(dates), "silver_total": out.count()}


# --------------------------------------------------------------------------------------- gold
GOLD_SQL: dict[str, str] = {
    "gold_meta": "SELECT max(event_ts) AS as_of_ts, CAST(max(event_ts) AS DATE) AS as_of_date FROM silver_events",
    "dim_accounts": "SELECT account_id, tier, segment, CAST(created_at AS DATE) AS created_date FROM raw_accounts",
    "dim_features": "SELECT feature_id, name AS feature_name, category FROM raw_features",
    "dim_users": """
        SELECT u.user_id, u.account_id, u.signup_at, CAST(u.signup_at AS DATE) AS signup_date, u.country,
               coalesce(a.tier, 'no_account') AS account_tier, coalesce(a.segment, 'no_account') AS account_segment,
               CASE WHEN s.user_id IS NOT NULL THEN 'pro' ELSE 'free' END AS current_plan
        FROM raw_users u
        LEFT JOIN raw_accounts a ON u.account_id = a.account_id
        LEFT JOIN (SELECT DISTINCT user_id FROM silver_events WHERE event_name = 'subscription_started') s ON u.user_id = s.user_id""",
    "fct_product_events": f"""
        SELECT event_id, event_name, event_ts, activity_date, ingested_at, is_late, user_id, account_id, session_id,
               feature_id, (event_name IN {QUALIFYING}) AS is_qualifying_activity
        FROM silver_events""",
    "fct_agent_interactions": """
        WITH term AS (
            SELECT *, row_number() OVER (PARTITION BY execution_id ORDER BY event_ts DESC, event_id) AS rn
            FROM silver_events WHERE event_name IN ('agent_completed','agent_failed') AND execution_id IS NOT NULL),
        started AS (
            SELECT execution_id, min(event_ts) AS started_at FROM silver_events
            WHERE event_name = 'agent_started' AND execution_id IS NOT NULL GROUP BY execution_id)
        SELECT t.execution_id, t.user_id, t.account_id, t.feature_id, t.agent_type, t.activity_date,
               coalesce(s.started_at, timestamp_millis(unix_millis(t.event_ts) - coalesce(t.duration_ms, 0))) AS started_at,
               t.event_ts AS ended_at, t.status, (t.status = 'success') AS is_success, t.failure_category,
               t.duration_ms, t.retry_count, (t.retry_count >= 1) AS is_retried, t.input_tokens, t.output_tokens,
               t.cost_usd_estimate
        FROM term t LEFT JOIN started s ON t.execution_id = s.execution_id WHERE t.rn = 1""",
    "fct_daily_user_activity": f"""
        SELECT user_id, activity_date, count(*) AS qualifying_events, count(DISTINCT session_id) AS sessions,
               count(DISTINCT feature_id) AS features_used,
               count(*) FILTER (WHERE event_name = 'suggestion_shown') AS suggestions_shown,
               count(*) FILTER (WHERE event_name = 'suggestion_accepted') AS suggestions_accepted,
               count(*) FILTER (WHERE event_name IN ('agent_completed','agent_failed')) AS agent_runs
        FROM silver_events WHERE event_name IN {QUALIFYING} GROUP BY user_id, activity_date""",
    "fct_user_lifecycle": """
        SELECT u.user_id, u.signup_date, u.account_tier,
               min(e.event_ts) FILTER (WHERE e.event_name = 'onboarding_started') AS onboarding_started_at,
               min(e.event_ts) FILTER (WHERE e.event_name = 'onboarding_completed') AS onboarding_completed_at,
               min(e.event_ts) FILTER (WHERE e.event_name = 'subscription_started') AS subscribed_at,
               min(e.event_ts) FILTER (WHERE e.event_name = 'onboarding_completed') <= u.signup_at + INTERVAL 7 DAYS AS activated_7d,
               min(e.event_ts) FILTER (WHERE e.event_name = 'subscription_started') <= u.signup_at + INTERVAL 30 DAYS AS converted_30d
        FROM dim_users u LEFT JOIN silver_events e ON u.user_id = e.user_id
        GROUP BY u.user_id, u.signup_date, u.account_tier, u.signup_at""",
    "agg_activation_funnel_daily": """
        SELECT l.signup_date, l.account_tier, count(*) AS signups,
               count(*) FILTER (WHERE onboarding_started_at IS NOT NULL) AS onboarding_started,
               count(*) FILTER (WHERE coalesce(activated_7d, false)) AS activated_7d,
               count(*) FILTER (WHERE coalesce(converted_30d, false)) AS converted_30d,
               date_add(l.signup_date, 7) < m.as_of_date AS activation_window_complete,
               date_add(l.signup_date, 30) < m.as_of_date AS conversion_window_complete
        FROM fct_user_lifecycle l CROSS JOIN gold_meta m
        GROUP BY l.signup_date, l.account_tier, m.as_of_date""",
    "agg_retention_cohorts": """
        SELECT u.signup_date, u.account_tier, d.day_n, count(*) AS cohort_users,
               count(*) FILTER (WHERE a.user_id IS NOT NULL) AS retained_users,
               date_add(u.signup_date, d.day_n) < m.as_of_date AS window_complete
        FROM dim_users u
        CROSS JOIN (SELECT explode(array(1, 7, 14, 30)) AS day_n) d
        CROSS JOIN gold_meta m
        LEFT JOIN fct_daily_user_activity a ON a.user_id = u.user_id AND a.activity_date = date_add(u.signup_date, d.day_n)
        GROUP BY u.signup_date, u.account_tier, d.day_n, m.as_of_date""",
    "agg_product_metrics_daily": """
        WITH act AS (
            SELECT a.activity_date AS `date`, u.account_tier, count(DISTINCT a.user_id) AS dau, sum(a.qualifying_events) AS qualifying_events
            FROM fct_daily_user_activity a JOIN dim_users u ON a.user_id = u.user_id GROUP BY a.activity_date, u.account_tier),
        ag AS (
            SELECT i.activity_date AS `date`, coalesce(a.tier, 'no_account') AS account_tier, count(*) AS agent_runs,
                   count(*) FILTER (WHERE is_success) AS agent_successes, count(*) FILTER (WHERE is_retried) AS agent_retries,
                   percentile(CASE WHEN duration_ms >= 0 THEN duration_ms END, 0.5) AS p50_latency_ms,
                   round(percentile(CASE WHEN duration_ms >= 0 THEN duration_ms END, 0.95), 3) AS p95_latency_ms,
                   sum(coalesce(input_tokens, 0) + coalesce(output_tokens, 0)) AS total_tokens,
                   round(sum(cost_usd_estimate), 6) AS est_cost_usd
            FROM fct_agent_interactions i LEFT JOIN dim_accounts a ON i.account_id = a.account_id
            GROUP BY i.activity_date, coalesce(a.tier, 'no_account')),
        su AS (SELECT signup_date AS `date`, account_tier, count(*) AS new_signups FROM dim_users GROUP BY signup_date, account_tier),
        keys AS (SELECT `date`, account_tier FROM act UNION SELECT `date`, account_tier FROM ag UNION SELECT `date`, account_tier FROM su)
        SELECT k.`date`, k.account_tier, coalesce(act.dau, 0) AS dau, coalesce(act.qualifying_events, 0) AS qualifying_events,
               coalesce(su.new_signups, 0) AS new_signups, coalesce(ag.agent_runs, 0) AS agent_runs,
               coalesce(ag.agent_successes, 0) AS agent_successes, coalesce(ag.agent_retries, 0) AS agent_retries,
               ag.p50_latency_ms, ag.p95_latency_ms, coalesce(ag.total_tokens, 0) AS total_tokens,
               coalesce(ag.est_cost_usd, 0) AS est_cost_usd
        FROM keys k
        LEFT JOIN act ON k.`date` = act.`date` AND k.account_tier = act.account_tier
        LEFT JOIN ag ON k.`date` = ag.`date` AND k.account_tier = ag.account_tier
        LEFT JOIN su ON k.`date` = su.`date` AND k.account_tier = su.account_tier""",
}


def build_gold(lake: Lake, staging_schema: str = "gold_staging") -> dict[str, int]:
    """Compute every Gold table into the staging schema (not yet visible to consumers)."""
    spark = lake.spark
    lake.read("silver", "events").createOrReplaceTempView("silver_events")
    for t in ("raw_users", "raw_accounts", "raw_features"):
        lake.read("silver", t).createOrReplaceTempView(t)
    counts = {}
    for name, sql in GOLD_SQL.items():
        df = spark.sql(sql).cache()
        df.createOrReplaceTempView(name)
        if name != "gold_meta":
            lake.write(df, staging_schema, name)
        counts[name] = df.count()
    return counts


def promote_gold(lake: Lake, staging_schema: str = "gold_staging") -> None:
    for name in GOLD_TABLES:
        lake.write(lake.read(staging_schema, name), "gold", name)


def reconcile(ing: dict, sil: dict, gold_counts: dict) -> None:
    if ing["input"] != ing["accepted"] + ing["quarantined"]:
        raise ReconciliationError(f"input {ing['input']} != accepted {ing['accepted']} + quarantined {ing['quarantined']}")
    if ing["accepted"] != sil["kept_from_batch"] + sil["duplicates"]:
        raise ReconciliationError(f"accepted {ing['accepted']} != silver kept {sil['kept_from_batch']} + duplicates {sil['duplicates']}")
    if gold_counts["fct_product_events"] != sil["silver_total"]:
        raise ReconciliationError(f"fct_product_events {gold_counts['fct_product_events']} != silver_total {sil['silver_total']}")


def run(lake: Lake, batch_dir: str | Path, now: datetime | None = None) -> dict:
    """Whole pipeline in one call (the Databricks job runs the same four steps as separate tasks)."""
    ing = ingest(lake, batch_dir, now=now)
    sil = build_silver(lake, ing["batch_id"])
    gold_counts = build_gold(lake)
    reconcile(ing, sil, gold_counts)
    promote_gold(lake)
    return {"ingest": ing, "silver": sil, "gold": gold_counts, "status": "success"}
