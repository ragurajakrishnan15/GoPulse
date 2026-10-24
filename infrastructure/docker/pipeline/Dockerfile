# One-shot data job: synthetic data -> Bronze/Silver/Gold -> dbt -> Postgres serving schema.
#   docker build -f infrastructure/docker/pipeline/Dockerfile -t gopulse-pipeline .
FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
# Pipeline + dbt only (skip the dev-only tools to keep the image lean).
RUN grep -viE '^(pytest|ruff|httpx)' /tmp/requirements.txt > /tmp/runtime.txt && pip install -r /tmp/runtime.txt
COPY pipelines ./pipelines
COPY transformations ./transformations
COPY scripts/seed.sh ./scripts/seed.sh
RUN chmod +x scripts/seed.sh && useradd --system --uid 10001 --home /app gopulse && mkdir -p data && chown -R gopulse /app
USER gopulse
ENTRYPOINT ["./scripts/seed.sh"]
