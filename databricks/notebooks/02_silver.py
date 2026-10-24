# Databricks notebook source
# MAGIC %md
# MAGIC ## 02 - Silver: type, deduplicate (earliest `ingested_at` wins), flag late events
# MAGIC Rebuilds only the `activity_date` partitions touched by this batch (`replaceWhere`), so reruns and late arrivals are safe.

# COMMAND ----------
dbutils.widgets.text("catalog", "gopulse")
dbutils.widgets.text("batch_id", "")
dbutils.widgets.text("input", "0")
dbutils.widgets.text("accepted", "0")
dbutils.widgets.text("quarantined", "0")

# COMMAND ----------
from pipelines.spark.spark_pipeline import Lake, build_silver

lake = Lake(spark, catalog=dbutils.widgets.get("catalog"))
silver = build_silver(lake, dbutils.widgets.get("batch_id"))
print(silver)
for k, v in silver.items():
    dbutils.jobs.taskValues.set(key=k, value=v)
