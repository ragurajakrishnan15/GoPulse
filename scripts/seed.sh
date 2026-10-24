#!/bin/sh
# Populate the demo: generate synthetic data, run the pipeline + dbt, publish to Postgres.
# Idempotent: re-running replaces the published data (demo-live run history is preserved).
set -eu
USERS="${GOPULSE_USERS:-500}"; SEED="${GOPULSE_SEED:-42}"; DEFECTS="${GOPULSE_DEFECT_RATE:-0.02}"
: "${DATABASE_URL_OWNER:?DATABASE_URL_OWNER must be set}"
echo "[1/6] generate $USERS users (seed $SEED, defect rate $DEFECTS)"
python -m pipelines.generators.generate --users "$USERS" --seed "$SEED" --defect-rate "$DEFECTS" --out data/raw/seed >/dev/null
echo "[2/6] bronze -> silver -> gold (+ quality gate)"
python -m pipelines.jobs.run --batch data/raw/seed --lake "" >/dev/null
echo "[3/6] dbt build"
python -m pipelines.jobs.dbt_runner >/dev/null
echo "[4/6] failure demonstration"
python -m pipelines.jobs.failure_demo --users "$USERS" --seed "$SEED" >/dev/null
echo "[5/6] publish to PostgreSQL"
python -m pipelines.serving.publish >/dev/null
echo "[6/6] done"
