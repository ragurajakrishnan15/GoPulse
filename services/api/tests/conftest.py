"""API integration tests run against a REAL PostgreSQL (docker compose locally, a service container in CI).

A dedicated database (gopulse_test) is (re)built per session: pipeline -> dbt -> publish -> API. Tests are skipped if
no Postgres is reachable. Connection settings come from the environment or the git-ignored .env file.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from pipelines.generators.generate import Config
from pipelines.jobs.dbt_runner import persist_dbt_results, run_dbt_build
from pipelines.jobs.run import run_pipeline
from pipelines.serving.publish import publish
from quality.integration_tests.test_pipeline import write_batch
from services.api.app.config import Settings
from services.api.app.main import create_app

ROOT = Path(__file__).resolve().parents[3]
TEST_DB = "gopulse_test"


def _load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _with_db(url: str, db: str) -> str:
    return make_conninfo(url, dbname=db)


@pytest.fixture(scope="session")
def urls():
    _load_env()
    owner = os.environ.get("DATABASE_URL_OWNER")
    if not owner:
        pytest.skip("DATABASE_URL_OWNER not set")
    try:
        admin = psycopg.connect(owner, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"PostgreSQL not reachable: {e}")
    with admin:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {TEST_DB}")
    return {
        "owner": _with_db(owner, TEST_DB),
        "ro": _with_db(os.environ["DATABASE_URL_RO"], TEST_DB),
        "rw": _with_db(os.environ["DATABASE_URL_RW"], TEST_DB),
    }


@pytest.fixture(scope="session")
def lake(tmp_path_factory, urls):
    """Run the real pipeline + dbt on a small dirty batch and publish it."""
    root = tmp_path_factory.mktemp("lake")
    db = root / "gopulse.duckdb"
    write_batch(root / "batch", Config(users=200, accounts=25, days=45, seed=5, defect_rate=0.03))
    s = run_pipeline(root / "batch", db, None)
    assert s["status"] == "success", s
    out = run_dbt_build(db)
    assert out["returncode"] == 0, out["stdout_tail"]
    persist_dbt_results(db, out["results"])
    publish(db, urls["owner"])
    return {"db": db, "users": 200}


@pytest.fixture(scope="session")
def settings(urls):
    return Settings(ro_url=urls["ro"], rw_url=urls["rw"], cors_origins=["http://localhost:3000"],
                    demo_per_minute=50, demo_per_hour=500, demo_global_per_hour=1000)


@pytest.fixture(scope="session")
def client(lake, settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture(scope="session")
def pg(urls, lake):
    with psycopg.connect(urls["owner"], row_factory=psycopg.rows.dict_row) as conn:
        yield conn
