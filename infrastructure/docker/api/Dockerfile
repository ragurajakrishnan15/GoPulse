# GoPulse API. Build from the repository root:  docker build -f infrastructure/docker/api/Dockerfile -t gopulse-api .
FROM python:3.11-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY services/api/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# Only what the API imports: the pipeline package (demo run + catalog/schema assets) and the API app.
COPY pipelines ./pipelines
COPY services/__init__.py ./services/__init__.py
COPY services/api/__init__.py ./services/api/__init__.py
COPY services/api/app ./services/api/app

RUN useradd --system --uid 10001 --home /app gopulse && chown -R gopulse /app
USER gopulse
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"
# ONE worker: the demo-run rate limiter is in-memory (see docs/api.md). Scale with a shared limiter before adding workers.
CMD ["uvicorn", "services.api.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]
