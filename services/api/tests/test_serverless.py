"""Behaviour needed for a serverless deployment: a rate limiter shared through PostgreSQL, hashed client identities, lazy pools
(no lifespan hook), and pooler-safe connections. Runs against a real PostgreSQL like the rest of the API tests."""
import re
import threading

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from services.api.app.config import Settings
from services.api.app.main import create_app
from services.api.app.ratelimit import PgRateLimiter, client_key


@pytest.fixture
def owner(urls, lake):
    with psycopg.connect(urls["owner"], autocommit=True, row_factory=dict_row) as c:
        c.execute("DELETE FROM serving.demo_hits")
        yield c
        c.execute("DELETE FROM serving.demo_hits")


def rw_check(urls, limiter, key):
    with psycopg.connect(urls["rw"], row_factory=dict_row) as conn, conn.transaction():
        return limiter.check(conn, key)


def pg_settings(urls, **kw):
    base = {"ro_url": urls["ro"], "rw_url": urls["rw"], "rate_limit_backend": "postgres", "rate_limit_salt": "unit-test-salt",
            "demo_per_minute": 50, "demo_per_hour": 500, "demo_global_per_hour": 1000}
    return Settings(**{**base, **kw})


# ---------------------------------------------------------------- the shared limiter itself
def test_minute_limit_is_per_client(urls, owner):
    lim = PgRateLimiter(per_minute=2, per_hour=50, global_per_hour=500)
    assert rw_check(urls, lim, "client-a") is None and rw_check(urls, lim, "client-a") is None
    wait = rw_check(urls, lim, "client-a")
    assert wait is not None and 1 <= wait <= 61
    assert rw_check(urls, lim, "client-b") is None  # another client is unaffected


def test_hour_limit_counts_older_hits(urls, owner):
    owner.execute("INSERT INTO serving.demo_hits (client_hash, at) SELECT 'client-a', now() - interval '30 minutes' FROM generate_series(1, 3)")
    lim = PgRateLimiter(per_minute=50, per_hour=3, global_per_hour=500)
    wait = rw_check(urls, lim, "client-a")
    assert wait is not None and 29 * 60 <= wait <= 31 * 60  # the oldest hit leaves the window in ~30 minutes
    assert rw_check(urls, lim, "client-b") is None


def test_global_limit_applies_across_clients(urls, owner):
    owner.execute("INSERT INTO serving.demo_hits (client_hash, at) SELECT 'client-' || g, now() - interval '5 minutes' FROM generate_series(1, 4) g")
    lim = PgRateLimiter(per_minute=50, per_hour=50, global_per_hour=4)
    assert rw_check(urls, lim, "brand-new-client") is not None


def test_rows_older_than_an_hour_are_pruned(urls, owner):
    owner.execute("INSERT INTO serving.demo_hits (client_hash, at) VALUES ('stale', now() - interval '2 hours')")
    rw_check(urls, PgRateLimiter(5, 50, 500), "client-a")
    left = owner.execute("SELECT count(*) AS n FROM serving.demo_hits WHERE client_hash = 'stale'").fetchone()["n"]
    assert left == 0


def test_concurrent_checks_cannot_exceed_the_limit(urls, owner):
    """Eight 'instances' race for a limit of 3: exactly 3 may pass (the advisory lock serialises the check-then-insert)."""
    lim = PgRateLimiter(per_minute=3, per_hour=50, global_per_hour=500)
    results, barrier = [], threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(rw_check(urls, lim, "racer"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(r is None for r in results) == 3 and sum(r is not None for r in results) == 5


# ---------------------------------------------------------------- client identity
class _Req:
    def __init__(self, host="10.0.0.1", xff=None):
        self.client = type("C", (), {"host": host})()
        self.headers = {"x-forwarded-for": xff} if xff else {}


def test_client_key_is_a_salted_hash_and_honours_proxy_header_only_when_trusted():
    a = client_key(_Req("10.0.0.1"), False, "salt")
    assert re.fullmatch(r"[0-9a-f]{32}", a) and "10.0.0.1" not in a
    assert client_key(_Req("10.0.0.1"), False, "other-salt") != a            # salted
    assert client_key(_Req("10.0.0.1", xff="203.0.113.7"), False, "salt") == a  # header ignored unless trusted (spoofing)
    trusted = client_key(_Req("10.0.0.1", xff="203.0.113.7, 10.0.0.1"), True, "salt")
    assert trusted == client_key(_Req("99.99.99.99", xff="203.0.113.7"), True, "salt") != a  # first hop is the client


# ---------------------------------------------------------------- the endpoint with the shared limiter
def test_demo_endpoint_limits_per_client_across_app_instances_and_stores_no_ip(urls, owner):
    body = {"scenario": "clean", "seed": 21}
    s = pg_settings(urls, trust_proxy_headers=True, demo_per_minute=1)
    first_instance, second_instance = TestClient(create_app(s)), TestClient(create_app(s))  # two "serverless instances"
    ip_a, ip_b = {"x-forwarded-for": "203.0.113.9"}, {"x-forwarded-for": "198.51.100.4"}

    assert first_instance.post("/api/v1/demo/run", json=body, headers=ip_a).status_code == 201
    blocked = second_instance.post("/api/v1/demo/run", json=body, headers=ip_a)  # a DIFFERENT instance still knows
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1
    assert second_instance.post("/api/v1/demo/run", json=body, headers=ip_b).status_code == 201

    rows = [r["client_hash"] for r in owner.execute("SELECT client_hash FROM serving.demo_hits").fetchall()]
    assert len(rows) == 2 and all(re.fullmatch(r"[0-9a-f]{32}", h) for h in rows)
    assert not any(ip in h for h in rows for ip in ("203.0.113.9", "198.51.100.4"))


def test_untrusted_proxy_header_cannot_dodge_the_limit(urls, owner):
    s = pg_settings(urls, trust_proxy_headers=False, demo_per_minute=1)
    c = TestClient(create_app(s))
    body = {"scenario": "clean", "seed": 22}
    assert c.post("/api/v1/demo/run", json=body, headers={"x-forwarded-for": "1.1.1.1"}).status_code == 201
    assert c.post("/api/v1/demo/run", json=body, headers={"x-forwarded-for": "2.2.2.2"}).status_code == 429


def test_invalid_requests_do_not_consume_quota(urls, owner):
    c = TestClient(create_app(pg_settings(urls, demo_per_minute=1)))
    assert c.post("/api/v1/demo/run", json={"cmd": "x"}).status_code == 422  # rejected before any work: no quota used
    assert owner.execute("SELECT count(*) AS n FROM serving.demo_hits").fetchone()["n"] == 0


# ---------------------------------------------------------------- configuration and runtime shape
def test_postgres_backend_requires_a_salt_and_unknown_backends_are_rejected(urls):
    with pytest.raises(RuntimeError, match="RATE_LIMIT_SALT"):
        create_app(Settings(rate_limit_backend="postgres"))
    with pytest.raises(RuntimeError, match="RATE_LIMIT_BACKEND"):
        create_app(Settings(rate_limit_backend="redis"))


def test_app_works_without_lifespan_events(urls, lake):
    """Serverless hosts may never run ASGI lifespan hooks: pools must be created lazily on first use."""
    c = TestClient(create_app(pg_settings(urls)))  # deliberately NOT used as a context manager -> no startup event
    assert c.get("/health").json()["database"] == "ok"
    assert len(c.get("/api/v1/features").json()["items"]) == 8


def test_pooler_safe_connection_settings(urls, lake):
    from services.api.app.db import make_pool
    pool = make_pool(urls["ro"], max_size=1)
    try:
        with pool.connection() as conn:
            assert conn.prepare_threshold is None  # no server-side prepared statements (transaction poolers reject them)
            for _ in range(8):  # more than the default prepare threshold of 5
                assert conn.execute("SELECT 1 AS x").fetchone()["x"] == 1
        assert pool.min_size == 0  # nothing held open while idle
    finally:
        pool.close()


def test_missing_database_url_gives_503_not_a_crash(urls):
    c = TestClient(create_app(Settings()))
    assert c.get("/api/v1/features").status_code == 503
    assert c.get("/health").json()["database"] == "not_configured"


# ---------------------------------------------------------------- least privilege for the new table
def test_rate_limit_table_permissions(urls, lake):
    with psycopg.connect(urls["rw"]) as conn:
        conn.execute("INSERT INTO serving.demo_hits (client_hash) VALUES ('perm-test')")
        conn.execute("DELETE FROM serving.demo_hits WHERE client_hash = 'perm-test'")
        conn.commit()
        for sql in ("UPDATE serving.demo_hits SET client_hash = 'x'", "DELETE FROM serving.pipeline_runs", "TRUNCATE serving.demo_hits"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)
            conn.rollback()
    with psycopg.connect(urls["ro"]) as conn:
        for sql in ("INSERT INTO serving.demo_hits (client_hash) VALUES ('x')", "DELETE FROM serving.demo_hits"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)
            conn.rollback()
