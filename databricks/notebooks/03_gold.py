# Databricks notebook source
# MAGIC %md
# MAGIC ## 03 - Gold: build dimensions, facts and aggregates into the STAGING schema
# MAGIC Nothing is visible to consumers until task 04 promotes it.

# COMMAND ----------
dbutils.widgets.text("catalog", "gopulse")

# COMMAND ----------
import json

from pipelines.spark.spark_pipeline import Lake, build_gold

lake = Lake(spark, catalog=dbutils.widgets.get("catalog"))
counts = build_gold(lake)
print(counts)
dbutils.jobs.taskValues.set(key="gold_counts", value=json.dumps(counts))
