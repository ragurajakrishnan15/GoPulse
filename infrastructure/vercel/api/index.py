"""Vercel entrypoint: exposes the FastAPI app (zero-config Python function). Staged into deploy/api by scripts/stage_api.py."""
from services.api.app.main import app  # noqa: F401
