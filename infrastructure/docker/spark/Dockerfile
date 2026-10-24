# Local Spark + Delta Lake test image (Linux). Used to run the PySpark implementation for real, outside Databricks.
FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jre-headless procps \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "pyspark==3.5.3" "delta-spark==3.2.1" "duckdb>=1.1,<3" "jsonschema>=4.21,<5" "pyyaml>=6,<7" "pytest>=8,<9"
ENV PYTHONPATH=/opt/gopulse JAVA_TOOL_OPTIONS="-Duser.timezone=UTC" PYSPARK_PYTHON=python
# Warm the Ivy cache so tests don't download the Delta jars at run time.
RUN python - <<'PY'
from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession
b = (SparkSession.builder.master("local[1]").config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
     .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog").config("spark.ui.enabled", "false"))
s = configure_spark_with_delta_pip(b).getOrCreate()
s.range(3).write.format("delta").save("/tmp/warm")
print("delta ok", s.read.format("delta").load("/tmp/warm").count())
PY
WORKDIR /opt/gopulse
