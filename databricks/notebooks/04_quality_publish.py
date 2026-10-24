# Databricks notebook source
# MAGIC %md
# MAGIC ## 04 - Reconcile, then promote Gold
# MAGIC Counts must reconcile (input = accepted + quarantined; accepted = silver kept + duplicates; fct events = silver total).
# MAGIC Only then is the staging Gold copied over the consumer-facing `gold` schema.

# COMMAND ----------
dbutils.widgets.text("catalog", "gopulse")
for name in ("input", "accepted", "quarantined", "kept_from_batch", "duplicates", "silver_total", "gold_counts"):
    dbutils.widgets.text(name, "0")

# COMMAND ----------
import json

from pipelines.spark.spark_pipeline import Lake, promote_gold, reconcile

w = dbutils.widgets.get
lake = Lake(spark, catalog=w("catalog"))
ing = {"input": int(w("input")), "accepted": int(w("accepted")), "quarantined": int(w("quarantined"))}
sil = {"kept_from_batch": int(w("kept_from_batch")), "duplicates": int(w("duplicates")), "silver_total": int(w("silver_total"))}
reconcile(ing, sil, json.loads(w("gold_counts")))
promote_gold(lake)
print("Gold promoted")
