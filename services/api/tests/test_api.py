import psycopg
import pytest

from pipelines.serving.publish import publish

DAYS = "/api/v1"


def get(client, path, **params):
    r = client.get(DAYS + path, params={k: v for k, v in params.items() if v is not None})
    return r


def series(client, metric, **kw):
    r = get(client, "/metrics/timeseries", metric=metric, **kw)
    assert r.status_code == 200, r.text
    return {p["date"]: p for p in r.json()["points"]}


# ---------------------------------------------------------------- health / basics
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "database": "ok", "synthetic_data": True}


def test_every_response_is_labelled_synthetic(client):
    for path in ("/overview", "/catalog/metrics", "/pipelines/runs", "/agents/summary"):
        body = get(client, path).json()
        assert body["meta"]["synthetic_data"] is True and body["meta"]["last_refreshed"]


def test_cors(client):
    ok = client.options("/api/v1/overview", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    bad = client.options("/api/v1/overview", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    simple = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert "retry-after" in simple.headers.get("access-control-expose-headers", "").lower()
    assert "access-control-allow-origin" not in bad.headers


# ---------------------------------------------------------------- reconciliation: API == dbt marts
def test_overview_has_definitions_and_counts(client, lake):
    body = get(client, "/overview").json()
    kpis = {k["key"]: k for k in body["kpis"]}
    assert kpis["registered_users"]["value"] == lake["users"]
    for k in ("activation_rate", "conversion_rate", "retention_d7", "agent_success_rate"):
        assert kpis[k]["definition"] and 0 <= kpis[k]["value"] <= 1
        assert kpis[k]["denominator"] >= kpis[k]["numerator"]
    assert kpis["est_cost_per_interaction_usd"]["estimate"] is True


def last_point(client, metric, **params):
    s = series(client, metric, **params)
    return s[max(s)]["value"]  # ISO dates sort chronologically


def test_overview_dau_and_wau_equal_the_series_and_the_mart_on_the_last_day(client, pg):
    last = pg.execute("SELECT * FROM serving.mart_kpi_daily ORDER BY date DESC LIMIT 1").fetchone()
    kpis = {k["key"]: k for k in get(client, "/overview").json()["kpis"]}
    assert kpis["dau"]["value"] == last["dau"] and kpis["wau"]["value"] == last["wau"]
    # also under filters: the direct WAU count must equal the last point of the rolling-window series
    for params in ({"tier": "pro"}, {"plan": "pro", "segment": "smb"}, {"feature": "feat_search"}, {"start": "2026-01-10", "end": "2026-02-01"}):
        o = {k["key"]: k for k in get(client, "/overview", **params).json()["kpis"]}
        assert o["wau"]["value"] == last_point(client, "wau", **params), params
        assert o["dau"]["value"] == last_point(client, "dau", **params), params


@pytest.mark.parametrize("metric,col", [("dau", "dau"), ("wau", "wau"), ("new_signups", "new_signups"),
                                        ("agent_runs", "agent_runs"), ("agent_success_rate", "agent_success_rate"),
                                        ("p50_latency_ms", "p50_latency_ms"), ("p95_latency_ms", "p95_latency_ms"),
                                        ("est_cost_per_interaction_usd", "est_cost_per_interaction_usd")])
def test_unfiltered_timeseries_matches_dbt_mart(client, pg, metric, col):
    mart = {str(r["date"]): r for r in pg.execute("SELECT * FROM serving.mart_kpi_daily").fetchall()}
    api = series(client, metric)
    assert set(api) == set(mart)
    for d, p in api.items():
        expected = mart[d][col]
        if expected is None or expected == 0 and metric.startswith("agent") and p["value"] in (None, 0):
            assert p["value"] in (None, 0) or expected is None
        else:
            assert p["value"] == pytest.approx(expected, rel=1e-4, abs=1e-6), (metric, d)


def test_retention_matches_mart(client, pg):
    mart = {(str(r["cohort_week"]), r["day_n"]): r for r in pg.execute(
        "SELECT cohort_week, day_n, sum(eligible_users) AS eligible_users, sum(retained_users) AS retained_users "
        "FROM serving.mart_retention_weekly GROUP BY 1, 2").fetchall()}
    body = get(client, "/retention/cohorts").json()
    seen = 0
    for c in body["cohorts"]:
        for n, cell in c["retention"].items():
            m = mart[(c["cohort_start"], int(n))]
            assert cell["eligible_users"] == (m["eligible_users"] or 0) and cell["retained_users"] == (m["retained_users"] or 0)
            seen += 1
    assert seen == len(mart) and seen > 0


def test_funnel_matches_mart_and_denominators_differ(client, pg):
    m = pg.execute("SELECT sum(signups) s, sum(onboarding_started) o, "
                   "sum(activated_7d) FILTER (WHERE activation_window_complete) a, "
                   "sum(signups) FILTER (WHERE activation_window_complete) ae, "
                   "sum(converted_30d) FILTER (WHERE conversion_window_complete) c, "
                   "sum(signups) FILTER (WHERE conversion_window_complete) ce FROM serving.mart_activation_rates").fetchone()
    st = {s["stage"]: s for s in get(client, "/funnels/activation").json()["stages"]}
    assert (st["signed_up"]["users"], st["onboarding_started"]["users"]) == (m["s"], m["o"])
    assert (st["activated_7d"]["users"], st["activated_7d"]["denominator"]) == (m["a"], m["ae"])
    assert (st["converted_30d"]["users"], st["converted_30d"]["denominator"]) == (m["c"], m["ce"])
    assert st["converted_30d"]["denominator"] < st["signed_up"]["denominator"]


def test_agents_summary_matches_mart(client, pg):
    body = get(client, "/agents/summary").json()
    mart = {r["agent_type"]: r for r in pg.execute("SELECT * FROM serving.mart_agent_summary").fetchall()}
    assert {a["agent_type"] for a in body["by_agent"]} == set(mart)
    for a in body["by_agent"]:
        assert a["executions"] == mart[a["agent_type"]]["executions"]
        assert a["success_rate"] == pytest.approx(mart[a["agent_type"]]["success_rate"], abs=1e-5)
        assert a["p95_latency_ms"] == pytest.approx(mart[a["agent_type"]]["p95_latency_ms"], abs=0.2)
    assert sum(f["share_of_failures"] for f in body["failure_categories"]) == pytest.approx(1)
    assert "ESTIMATES" in body["cost_disclaimer"]


def test_mart_row_counts_match_duckdb(lake, pg):
    import duckdb
    con = duckdb.connect(str(lake["db"]), read_only=True)
    for t in ("mart_kpi_daily", "mart_activation_rates", "mart_retention_weekly", "mart_agent_summary"):
        n = con.execute(f"SELECT count(*) FROM marts.{t}").fetchone()[0]
        assert pg.execute(f"SELECT count(*) AS n FROM serving.{t}").fetchone()["n"] == n
    assert pg.execute("SELECT count(*) AS n FROM serving.agent_runs").fetchone()["n"] == con.execute(
        "SELECT count(*) FROM fct_agent_interactions").fetchone()[0]


# ---------------------------------------------------------------- filters
def test_tier_filters_partition_signups(client):
    total = series(client, "new_signups")
    parts = [series(client, "new_signups", tier=t) for t in ("free", "pro", "team", "enterprise", "no_account")]
    for d, p in total.items():
        assert sum(x[d]["value"] for x in parts) == p["value"]


def test_filters_narrow_results(client):
    base = series(client, "dau")
    pro = series(client, "dau", plan="pro")
    feat = series(client, "dau", feature="feat_search")
    seg = series(client, "dau", segment="smb", tier="free")
    assert all(pro[d]["value"] <= base[d]["value"] and feat[d]["value"] <= base[d]["value"] and seg[d]["value"] <= base[d]["value"] for d in base)
    assert sum(p["value"] for p in feat.values()) < sum(p["value"] for p in base.values())


def test_features_list(client):
    items = get(client, "/features").json()["items"]
    assert len(items) == 8 and all(i["feature_id"].startswith("feat_") for i in items)


def test_date_range_limits_points(client):
    s = series(client, "dau", start="2026-01-10", end="2026-01-12")
    assert list(s) == ["2026-01-10", "2026-01-11", "2026-01-12"]


@pytest.mark.parametrize("params,needle", [
    ({"metric": "dau", "start": "2026-02-01", "end": "2026-01-01"}, "start must be"),
    ({"metric": "dau", "start": "2020-01-01", "end": "2026-01-01"}, "exceed"),
    ({"metric": "dau", "feature": "feat_nope"}, "unknown feature"),
    ({"metric": "dau", "feature": "x'; DROP TABLE serving.users;--"}, "pattern"),
    ({"metric": "dau", "tier": "platinum"}, "tier"),
    ({"metric": "new_signups", "feature": "feat_search"}, "does not apply"),
    ({"metric": "dau", "start": "not-a-date"}, "start"),
])
def test_invalid_requests_are_rejected_with_clear_errors(client, params, needle):
    r = get(client, "/metrics/timeseries", **params)
    assert r.status_code == 422, r.text
    assert needle in r.text
    assert r.json()["error"]["code"] == 422


def test_feature_filter_rejected_for_cohort_endpoints(client):
    assert get(client, "/funnels/activation", feature="feat_search").status_code == 422
    assert get(client, "/retention/cohorts", feature="feat_search").status_code == 422


def test_injection_does_not_damage_data(client, pg):
    get(client, "/metrics/timeseries", metric="dau", feature="'; DROP TABLE serving.users; --")
    assert pg.execute("SELECT count(*) AS n FROM serving.users").fetchone()["n"] == 200


# ---------------------------------------------------------------- pipelines / quality / catalog
def test_pipeline_runs_pagination_and_labels(client):
    p1 = get(client, "/pipelines/runs", limit=5, page=1).json()
    p2 = get(client, "/pipelines/runs", limit=5, page=2).json()
    assert len(p1["items"]) == 5 and p1["has_next"] and p1["total"] >= 31
    assert {i["run_id"] for i in p1["items"]}.isdisjoint({i["run_id"] for i in p2["items"]})
    seeded = get(client, "/pipelines/runs", source="seeded-example", limit=100).json()
    assert seeded["total"] == 30 and all(not i["is_real_execution"] and "Seeded" in i["execution_label"] for i in seeded["items"])
    real = get(client, "/pipelines/runs", source="local-real").json()
    assert real["total"] >= 1 and all(i["is_real_execution"] for i in real["items"])
    assert any(i["status"] == "failed" for i in seeded["items"])
    assert get(client, "/pipelines/runs", limit=101).status_code == 422
    assert get(client, "/pipelines/runs", source="nope").status_code == 422


def test_run_detail_has_tasks_and_log(client):
    run = get(client, "/pipelines/runs", source="local-real").json()["items"][0]
    d = get(client, f"/pipelines/runs/{run['run_id']}").json()
    assert [t["task"] for t in d["tasks"]] == ["ingest", "silver", "gold", "reconcile", "quality"]
    assert "counts:" in d["run"]["log_excerpt"] and d["run"]["freshness_lag_hours"] is not None
    assert get(client, "/pipelines/runs/does_not_exist").status_code == 404
    assert get(client, "/pipelines/runs/bad id!").status_code in (404, 422)


def test_quality_checks_defaults_to_latest_real_run(client):
    body = get(client, "/quality/checks").json()
    assert body["run"]["source"] == "local-real" and body["summary"]["checks"] >= 20
    failed = {i["check_name"]: i for i in body["items"] if i["status"] == "fail"}
    assert failed["missing_user_id"]["remediation_status"] == "quarantined" and failed["missing_user_id"]["affected_records"] > 0
    assert failed["duplicate_event_ids"]["remediation_status"] == "deduplicated"
    assert body["items"][0]["status"] == "fail"  # failures first
    only = get(client, "/quality/checks", layer="dbt").json()
    assert only["items"] and all(i["layer"] == "dbt" for i in only["items"])


@pytest.mark.parametrize("reason", ["missing_user_id", "invalid_timestamp", "unknown_event_name", "orphaned_account",
                                    "schema_mismatch", "negative_value", "duplicate_event_id"])
def test_failed_row_samples(client, reason):
    b = get(client, "/quality/samples", reason=reason, limit=3).json()
    assert 1 <= len(b["items"]) <= 3 and all(i["payload"] for i in b["items"])


def test_samples_limit_and_reason_validated(client):
    assert get(client, "/quality/samples", reason="missing_user_id", limit=26).status_code == 422
    assert get(client, "/quality/samples", reason="drop_table").status_code == 422


def test_failure_demo_report(client):
    r = get(client, "/quality/failure-demo").json()["report"]
    assert r["step_2_toxic_batch"]["gold_unchanged_after_block"] is True
    assert r["step_3_recovery_rerun"]["converged_to_reference"] is True


def test_catalog(client):
    items = get(client, "/catalog/models").json()["items"]
    names = {i["name"] for i in items}
    assert {"silver_events", "dim_users", "fct_agent_interactions", "mart_kpi_daily"} <= names
    d = get(client, "/catalog/models/fct_agent_interactions").json()["model"]
    assert d["grain"] and d["primary_key"] == "execution_id" and any(c["name"] == "duration_ms" and c["type"] for c in d["columns"])
    assert "silver_events" in d["upstream"] and "mart_agent_summary" in d["downstream"]
    assert d["row_count"] > 0 and d["source_link"]
    assert get(client, "/catalog/models/dim_users").json()["model"]["test_status"] == "passing"
    assert get(client, "/catalog/models", layer="mart").json()["items"]
    assert get(client, "/catalog/models/nope").status_code == 404
    keys = {m["key"] for m in get(client, "/catalog/metrics").json()["items"]}
    assert {"dau", "activation_rate", "retention_d7", "agent_success_rate"} <= keys


# ---------------------------------------------------------------- demo run safety + behaviour
def test_demo_rejects_unsafe_or_unknown_input(client):
    for body in ({"scenario": "clean", "cmd": "rm -rf /"}, {"scenario": "drop table"}, {"seed": 0}, {"seed": 10000},
                 {"sql": "select 1"}, {"notebook_path": "/x"}, {"token": "abc"}, {"seed": "1; ls"}):
        r = client.post("/api/v1/demo/run", json=body)
        assert r.status_code == 422, (body, r.text)


def test_demo_clean_run_is_recorded_as_real_demo_live(client):
    before = get(client, "/overview").json()["kpis"]
    r = client.post("/api/v1/demo/run", json={"scenario": "clean", "seed": 3})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["source"] == "demo-live" and b["status"] == "success" and b["counts"]["quarantined_count"] == 0
    runs = get(client, "/pipelines/runs", source="demo-live").json()
    assert any(i["run_id"] == b["run_id"] and i["is_real_execution"] for i in runs["items"])
    d = get(client, f"/pipelines/runs/{b['run_id']}").json()
    assert len(d["tasks"]) == 5
    assert get(client, "/quality/checks", run_id=b["run_id"]).json()["summary"]["checks"] >= 20
    after = get(client, "/overview").json()["kpis"]
    changed = [(a["key"], b["value"], a["value"]) for a, b in zip(after, before, strict=True) if a != b]
    assert not changed, f"published metrics changed by a demo run: {changed}"


def test_demo_heavy_defects_is_blocked_by_quality_gate(client):
    b = client.post("/api/v1/demo/run", json={"scenario": "heavy_defects", "seed": 4}).json()
    assert b["status"] == "failed" and "quarantine_rate_blocking" in b["quality"]["blocking"]
    checks = get(client, "/quality/checks", run_id=b["run_id"], status="fail").json()["items"]
    assert any(c["check_name"] == "quarantine_rate_blocking" and c["remediation_status"] == "blocked_publish" for c in checks)


def test_demo_runs_survive_republish(client, lake, urls, pg):
    client.post("/api/v1/demo/run", json={"scenario": "clean", "seed": 5})
    n_before = pg.execute("SELECT count(*) AS n FROM serving.pipeline_runs WHERE source='demo-live'").fetchone()["n"]
    pg.commit()
    publish(lake["db"], urls["owner"], failure_demo=lake["demo"])
    n_after = pg.execute("SELECT count(*) AS n FROM serving.pipeline_runs WHERE source='demo-live'").fetchone()["n"]
    assert n_before >= 1 and n_after == n_before


def test_demo_rate_limit(lake, urls):
    from fastapi.testclient import TestClient

    from services.api.app.config import Settings
    from services.api.app.main import create_app
    s = Settings(ro_url=urls["ro"], rw_url=urls["rw"], demo_per_minute=1, demo_per_hour=5, demo_global_per_hour=5)
    with TestClient(create_app(s)) as c:
        first = c.post("/api/v1/demo/run", json={"scenario": "clean", "seed": 6})
        second = c.post("/api/v1/demo/run", json={"scenario": "clean", "seed": 7})
    assert first.status_code == 201
    assert second.status_code == 429 and int(second.headers["retry-after"]) >= 1
    assert second.json()["error"]["code"] == 429


# ---------------------------------------------------------------- least privilege
def test_read_only_role_cannot_write(urls):
    with psycopg.connect(urls["ro"]) as conn:
        assert conn.execute("SELECT count(*) FROM serving.users").fetchone()[0] == 200
        for sql in ("DELETE FROM serving.users", "INSERT INTO serving.features VALUES ('x','x','x')",
                    "UPDATE serving.users SET account_tier = 'x'", "DROP TABLE serving.users", "CREATE TABLE serving.t (a int)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)
            conn.rollback()


def test_demo_role_can_only_insert_run_history(urls):
    with psycopg.connect(urls["rw"]) as conn:
        for sql in ("DELETE FROM serving.users", "INSERT INTO serving.features VALUES ('x','x','x')",
                    "DELETE FROM serving.pipeline_runs", "UPDATE serving.pipeline_runs SET status = 'success'",
                    "INSERT INTO serving.mart_kpi_daily (date) VALUES ('2030-01-01')"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)
            conn.rollback()


# ---------------------------------------------------------------- unit: rate limiter
def test_rate_limiter_windows():
    from services.api.app.ratelimit import RateLimiter
    rl = RateLimiter(per_minute=2, per_hour=3, global_per_hour=100)
    assert rl.check("a", 0) is None and rl.check("a", 1) is None
    wait = rl.check("a", 2)
    assert wait is not None and 50 <= wait <= 61
    assert rl.check("b", 2) is None  # other clients unaffected
    assert rl.check("a", 62) is None  # minute window slid
    assert rl.check("a", 125) is not None  # hourly cap (3 in the hour)
    assert rl.check("a", 3700) is None  # hour window slid


def test_rate_limiter_global_cap():
    from services.api.app.ratelimit import RateLimiter
    rl = RateLimiter(per_minute=10, per_hour=10, global_per_hour=2)
    assert rl.check("a", 0) is None and rl.check("b", 1) is None
    assert rl.check("c", 2) is not None


# ---------------------------------------------------------------- fresh deployment (nothing published yet)
def test_empty_database_gives_a_clear_503_not_a_500(urls):
    import psycopg
    from fastapi.testclient import TestClient
    from psycopg.conninfo import make_conninfo

    from services.api.app.config import Settings
    from services.api.app.main import create_app
    with psycopg.connect(make_conninfo(urls["owner"], dbname="postgres"), autocommit=True) as admin:
        admin.execute("DROP DATABASE IF EXISTS gopulse_empty_test WITH (FORCE)")
        admin.execute("CREATE DATABASE gopulse_empty_test")
    s = Settings(ro_url=make_conninfo(urls["ro"], dbname="gopulse_empty_test"), rw_url=make_conninfo(urls["rw"], dbname="gopulse_empty_test"))
    with TestClient(create_app(s)) as c:
        assert c.get("/health").status_code == 200
        for path in ("/api/v1/overview", "/api/v1/pipelines/runs", "/api/v1/catalog/models", "/api/v1/features"):
            r = c.get(path)
            assert r.status_code == 503, (path, r.text)
            assert "No data has been published yet" in r.json()["error"]["message"]
            assert r.headers["retry-after"] == "30"
