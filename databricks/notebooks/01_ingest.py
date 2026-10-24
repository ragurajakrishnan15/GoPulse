# Databricks notebook source
# MAGIC %md
# MAGIC ## 01 - Ingest: validate the batch, quarantine bad records, write Bronze
# MAGIC Fails with `QualityGateError` (no retry) BEFORE writing anything when more than 20% of the batch is invalid.

# COMMAND ----------
dbutils.widgets.text("catalog", "gopulse")
dbutils.widgets.text("batch_path", "")  # e.g. /Volumes/gopulse/landing/events/2026-01-05

# COMMAND ----------
from pipelines.spark.spark_pipeline import Lake, ingest

lake = Lake(spark, catalog=dbutils.widgets.get("catalog"))
counts = ingest(lake, dbutils.widgets.get("batch_path"))
print(counts)

# Downstream tasks read these through {{tasks.ingest.values.*}}
for k, v in counts.items():
    dbutils.jobs.taskValues.set(key=k, value=v)
