"""Product analytics endpoints. All SQL is parameterised; filters are validated in app.filters."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg import Connection

from services.api.app.db import meta, one, query, ro_conn
from services.api.app.filters import Filters, Tier, get_filters

router = APIRouter(prefix="/api/v1", tags=["analytics"])

Metric = Literal["dau", "wau", "new_signups", "qualifying_events", "agent_runs", "agent_success_rate", "agent_retry_rate",
                 "p50_latency_ms", "p95_latency_ms", "est_cost_usd", "est_cost_per_interaction_usd"]
USER_ONLY = {"new_signups"}  # not filterable by feature
RATE_METRICS = {"agent_success_rate", "agent_retry_rate", "est_cost_per_interaction_usd"}
UNITS = {"dau": "users", "wau": "users", "new_signups": "users", "qualifying_events": "events", "agent_runs": "executions",
         "agent_success_rate": "ratio", "agent_retry_rate": "ratio", "p50_latency_ms": "ms", "p95_latency_ms": "ms",
         "est_cost_usd": "usd", "est_cost_per_interaction_usd": "usd"}
# metric -> catalog_metrics key holding its definition (None when only described inline)
DEFINITION_KEY = {"dau": "dau", "wau": "wau", "new_signups": "registered_users", "agent_success_rate": "agent_success_rate",
                  "p50_latency_ms": "p50_latency_ms", "p95_latency_ms": "p95_latency_ms",
                  "est_cost_per_interaction_usd": "est_cost_per_interaction_usd", "est_cost_usd": "est_cost_per_interaction_usd"}


def _act_source(f: Filters) -> tuple[str, list]:
    uc, up = f.user_clause("u")
    sql = ("(SELECT x.activity_date, x.user_id, x.events FROM serving.user_day_feature x "
           "JOIN serving.users u ON u.user_id = x.user_id WHERE TRUE")
    params: list = []
    if f.feature:
        sql += " AND x.feature_id = %s"
        params.append(f.feature)
    return sql + uc + ")", params + up


def _agent_source(f: Filters) -> tuple[str, list]:
    uc, up = f.user_clause("u")
    sql = "(SELECT r.* FROM serving.agent_runs r JOIN serving.users u ON u.user_id = r.user_id WHERE TRUE"
    params: list = []
    if f.feature:
        sql += " AND r.feature_id = %s"
        params.append(f.feature)
    return sql + uc + ")", params + up


def _definition(conn: Connection, key: str | None) -> dict | None:
    if not key:
        return None
    return one(conn, "SELECT key, label, unit, definition, numerator, denominator, time_window, eligibility, notes "
                     "FROM serving.catalog_metrics WHERE key = %s", [key])


def series(conn: Connection, metric: str, f: Filters) -> list[dict[str, Any]]:
    days = ("(SELECT d::date AS date FROM generate_series(%s::date, %s::date, interval '1 day') d) days")
    dparams: list = [f.start, f.end]
    if metric in ("dau", "wau", "qualifying_events"):
        src, sp = _act_source(f)
        on = "x.activity_date = days.date" if metric != "wau" else "x.activity_date BETWEEN days.date - 6 AND days.date"
        agg = {"dau": "count(DISTINCT x.user_id)", "wau": "count(DISTINCT x.user_id)",
               "qualifying_events": "coalesce(sum(x.events), 0)"}[metric]
        sql = f"SELECT days.date, {agg}::float8 AS value FROM {days} LEFT JOIN {src} x ON {on} GROUP BY days.date ORDER BY days.date"
        return [{"date": r["date"], "value": r["value"], "numerator": None, "denominator": None} for r in query(conn, sql, dparams + sp)]
    if metric == "new_signups":
        uc, up = f.user_clause("u")
        sql = (f"SELECT days.date, count(u.user_id)::float8 AS value FROM {days} "
               f"LEFT JOIN serving.users u ON u.signup_date = days.date{uc} GROUP BY days.date ORDER BY days.date")
        return [{"date": r["date"], "value": r["value"], "numerator": None, "denominator": None} for r in query(conn, sql, dparams + up)]

    src, sp = _agent_source(f)
    exprs = {
        "agent_runs": ("count(x.execution_id)::float8", "NULL::float8", "NULL::float8"),
        "agent_success_rate": ("NULL::float8", "count(*) FILTER (WHERE x.is_success)::float8",
                               "count(*) FILTER (WHERE x.status IN ('success', 'failed'))::float8"),
        "agent_retry_rate": ("NULL::float8", "count(*) FILTER (WHERE x.is_retried)::float8", "count(x.execution_id)::float8"),
        "p50_latency_ms": ("percentile_cont(0.5) WITHIN GROUP (ORDER BY x.duration_ms) FILTER (WHERE x.duration_ms >= 0)", "NULL::float8", "NULL::float8"),
        "p95_latency_ms": ("percentile_cont(0.95) WITHIN GROUP (ORDER BY x.duration_ms) FILTER (WHERE x.duration_ms >= 0)", "NULL::float8", "NULL::float8"),
        "est_cost_usd": ("coalesce(round(sum(x.cost_usd_estimate)::numeric, 6)::float8, 0)::float8", "NULL::float8", "NULL::float8"),
        "est_cost_per_interaction_usd": ("NULL::float8", "round(sum(x.cost_usd_estimate)::numeric, 6)::float8::float8", "count(x.execution_id)::float8"),
    }
    val, num, den = exprs[metric]
    sql = (f"SELECT days.date, {val} AS value, {num} AS numerator, {den} AS denominator FROM {days} "
           f"LEFT JOIN {src} x ON x.activity_date = days.date GROUP BY days.date ORDER BY days.date")
    out = []
    for r in query(conn, sql, dparams + sp):
        value = r["value"]
        if metric in RATE_METRICS:
            value = (r["numerator"] / r["denominator"]) if r["denominator"] else None
        out.append({"date": r["date"], "value": value, "numerator": r["numerator"], "denominator": r["denominator"]})
    return out


@router.get("/features", summary="Feature list (for filter controls)")
def features(conn: Connection = Depends(ro_conn)):
    return {"items": query(conn, "SELECT feature_id, name, category FROM serving.features ORDER BY name"), "meta": meta(conn)}


@router.get("/metrics/timeseries", summary="Daily metric series with validated filters")
def timeseries(metric: Metric = Query(..., description="Metric to chart"), f: Filters = Depends(get_filters),
               conn: Connection = Depends(ro_conn)):
    if f.feature and metric in USER_ONLY:
        raise HTTPException(422, f"feature filter does not apply to metric '{metric}'")
    pts = series(conn, metric, f)
    return {"metric": metric, "unit": UNITS[metric], "filters": f.echo(), "definition": _definition(conn, DEFINITION_KEY.get(metric)),
            "points": pts, "meta": meta(conn)}


def _cohort_window_stats(conn: Connection, f: Filters) -> dict:
    uc, up = f.user_clause("u")
    row = one(conn, f"""
        SELECT count(*) AS signups,
               count(*) FILTER (WHERE u.onboarding_started) AS onboarding_started,
               count(*) FILTER (WHERE u.signup_date + 7 < %s) AS act_eligible,
               count(*) FILTER (WHERE u.signup_date + 7 < %s AND u.activated_7d) AS activated,
               count(*) FILTER (WHERE u.signup_date + 30 < %s) AS conv_eligible,
               count(*) FILTER (WHERE u.signup_date + 30 < %s AND u.converted_30d) AS converted
        FROM serving.users u WHERE u.signup_date BETWEEN %s AND %s{uc}""",
        [f.as_of, f.as_of, f.as_of, f.as_of, f.start, f.end, *up])
    return row


def _retention_rows(conn: Connection, f: Filters, weekly: bool) -> list[dict]:
    uc, up = f.user_clause("u")
    cohort = "date_trunc('week', u.signup_date)::date" if weekly else "u.signup_date"
    return query(conn, f"""
        SELECT {cohort} AS cohort, d.day_n,
               count(*) FILTER (WHERE u.signup_date + d.day_n < %s) AS eligible_users,
               count(*) FILTER (WHERE u.signup_date + d.day_n < %s AND EXISTS (
                   SELECT 1 FROM serving.activity_days a
                   WHERE a.user_id = u.user_id AND a.activity_date = u.signup_date + d.day_n)) AS retained_users
        FROM serving.users u CROSS JOIN (VALUES (1), (7), (14), (30)) AS d(day_n)
        WHERE u.signup_date BETWEEN %s AND %s{uc}
        GROUP BY 1, 2 ORDER BY 1, 2""", [f.as_of, f.as_of, f.start, f.end, *up])


def _ratio(n, d):
    return (n / d) if d else None


@router.get("/overview", summary="KPI summary")
def overview(f: Filters = Depends(get_filters), conn: Connection = Depends(ro_conn)):
    kpis: list[dict[str, Any]] = []

    def add(key: str, value, *, numerator=None, denominator=None, applied=("date", "tier", "segment", "plan"), extra=None):
        d = _definition(conn, key) or {}
        kpis.append({"key": key, "label": d.get("label", key), "value": value, "unit": d.get("unit"),
                     "numerator": numerator, "denominator": denominator, "definition": d.get("definition"),
                     "window": d.get("time_window"), "eligibility": d.get("eligibility"),
                     "applied_filters": list(applied), **(extra or {})})

    uc, up = f.user_clause("u")
    reg = one(conn, f"SELECT count(*) AS n FROM serving.users u WHERE u.signup_date <= %s{uc}", [f.end, *up])["n"]
    add("registered_users", reg, applied=("end", "tier", "segment", "plan"))

    act_filters = ("date", "tier", "segment", "plan", "feature")
    dau = series(conn, "dau", f)
    vals = [p["value"] for p in dau]
    add("dau", dau[-1]["value"] if dau else None, applied=act_filters,
        extra={"average_over_range": round(sum(vals) / len(vals), 2) if vals else None, "as_of": f.end})
    # Only the value at `end` is shown, so count it directly instead of building the whole rolling-window series
    # (that series cost ~48 ms per request - most of /overview's latency).
    act_src, act_params = _act_source(f)
    wau_n = one(conn, f"SELECT count(DISTINCT x.user_id) AS n FROM {act_src} x "
                      f"WHERE x.activity_date BETWEEN %s::date - 6 AND %s::date", [*act_params, f.end, f.end])["n"]
    add("wau", float(wau_n), applied=act_filters, extra={"as_of": f.end})

    st = _cohort_window_stats(conn, f)
    cohort_note = {"note": "Computed per signup cohort; the feature filter does not apply."}
    add("activation_rate", _ratio(st["activated"], st["act_eligible"]), numerator=st["activated"], denominator=st["act_eligible"],
        applied=("date", "tier", "segment", "plan"), extra={"eligible_cohort_users": st["act_eligible"], "cohort_users": st["signups"], **cohort_note})
    add("conversion_rate", _ratio(st["converted"], st["conv_eligible"]), numerator=st["converted"], denominator=st["conv_eligible"],
        applied=("date", "tier", "segment", "plan"), extra={"eligible_cohort_users": st["conv_eligible"], "cohort_users": st["signups"], **cohort_note})
    d7 = [r for r in _retention_rows(conn, f, weekly=False) if r["day_n"] == 7]
    n7, e7 = sum(r["retained_users"] for r in d7), sum(r["eligible_users"] for r in d7)
    add("retention_d7", _ratio(n7, e7), numerator=n7, denominator=e7, applied=("date", "tier", "segment", "plan"), extra=cohort_note)

    src, sp = _agent_source(f)
    ag = one(conn, f"""
        SELECT count(*) FILTER (WHERE x.status IN ('success','failed')) AS terminal,
               count(*) FILTER (WHERE x.is_success) AS ok,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY x.duration_ms) FILTER (WHERE x.duration_ms >= 0) AS p50,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY x.duration_ms) FILTER (WHERE x.duration_ms >= 0) AS p95,
               round(sum(x.cost_usd_estimate)::numeric, 6)::float8 AS cost, count(*) AS runs
        FROM {src} x WHERE x.activity_date BETWEEN %s AND %s""", [*sp, f.start, f.end])
    add("agent_success_rate", _ratio(ag["ok"], ag["terminal"]), numerator=ag["ok"], denominator=ag["terminal"], applied=act_filters)
    add("p50_latency_ms", ag["p50"], applied=act_filters)
    add("p95_latency_ms", ag["p95"], applied=act_filters)
    add("est_cost_per_interaction_usd", _ratio(ag["cost"], ag["runs"]) if ag["runs"] else None, numerator=ag["cost"],
        denominator=ag["runs"], applied=act_filters, extra={"estimate": True})
    return {"filters": f.echo(), "kpis": kpis, "meta": meta(conn)}


def _no_feature(f: Filters, what: str):
    if f.feature:
        raise HTTPException(422, f"feature filter does not apply to {what}")


@router.get("/funnels/activation", summary="Signup-to-activation and conversion funnel")
def activation_funnel(f: Filters = Depends(get_filters), conn: Connection = Depends(ro_conn)):
    _no_feature(f, "the activation funnel")
    s = _cohort_window_stats(conn, f)
    stages = [
        {"stage": "signed_up", "label": "Signed up", "users": s["signups"], "denominator": s["signups"], "rate": 1.0 if s["signups"] else None,
         "denominator_definition": "All users in the signup cohort", "eligibility": "none"},
        {"stage": "onboarding_started", "label": "Started onboarding", "users": s["onboarding_started"], "denominator": s["signups"],
         "rate": _ratio(s["onboarding_started"], s["signups"]), "denominator_definition": "All users in the signup cohort", "eligibility": "none"},
        {"stage": "activated_7d", "label": "Completed onboarding within 7 days", "users": s["activated"], "denominator": s["act_eligible"],
         "rate": _ratio(s["activated"], s["act_eligible"]),
         "denominator_definition": "Cohort users whose 7-day window has fully elapsed", "eligibility": "7-day window complete"},
        {"stage": "converted_30d", "label": "Subscribed within 30 days", "users": s["converted"], "denominator": s["conv_eligible"],
         "rate": _ratio(s["converted"], s["conv_eligible"]),
         "denominator_definition": "Cohort users whose 30-day window has fully elapsed", "eligibility": "30-day window complete"},
    ]
    return {"filters": f.echo(), "stages": stages,
            "caveat": "Stage rates use different denominators (eligible users only). Funnel steps show association, not causation.",
            "meta": meta(conn)}


@router.get("/retention/cohorts", summary="Cohort retention (day 1/7/14/30)")
def retention(granularity: Literal["week", "day"] = Query("week"), f: Filters = Depends(get_filters),
              conn: Connection = Depends(ro_conn)):
    _no_feature(f, "retention")
    rows = _retention_rows(conn, f, weekly=granularity == "week")
    cohorts: dict[date, dict] = {}
    for r in rows:
        c = cohorts.setdefault(r["cohort"], {"cohort_start": r["cohort"], "retention": {}})
        c["retention"][str(r["day_n"])] = {"eligible_users": r["eligible_users"], "retained_users": r["retained_users"],
                                           "rate": _ratio(r["retained_users"], r["eligible_users"])}
    return {"filters": f.echo(), "granularity": granularity, "day_offsets": [1, 7, 14, 30],
            "definition": _definition(conn, "retention_d7"), "cohorts": list(cohorts.values()), "meta": meta(conn)}


@router.get("/features/adoption", summary="Weekly feature adoption among active users")
def feature_adoption(start: date | None = None, end: date | None = None,
                     feature: str | None = Query(None, pattern=r"^feat_[a-z_]{1,40}$"), conn: Connection = Depends(ro_conn)):
    sql, params = "SELECT * FROM serving.mart_feature_adoption_weekly WHERE TRUE", []
    if start:
        sql += " AND week >= %s"
        params.append(start)
    if end:
        sql += " AND week <= %s"
        params.append(end)
    if feature:
        sql += " AND feature_id = %s"
        params.append(feature)
    return {"definition": _definition(conn, "feature_adoption"),
            "note": "User-level adoption among all users; tier/segment/plan filters are not available for this view.",
            "rows": query(conn, sql + " ORDER BY week, feature_id", params), "meta": meta(conn)}


@router.get("/accounts/adoption", summary="Account-level adoption by month and tier")
def account_adoption(tier: Tier | None = None, conn: Connection = Depends(ro_conn)):
    sql, params = "SELECT * FROM serving.mart_account_adoption WHERE TRUE", []
    if tier:
        sql += " AND account_tier = %s"
        params.append(tier)
    return {"definition": _definition(conn, "account_adoption"),
            "note": "Account-level metric: denominators are accounts, never users.",
            "rows": query(conn, sql + " ORDER BY period_start, account_tier", params), "meta": meta(conn)}


@router.get("/agents/summary", summary="Agent success, latency, failure categories and estimated cost")
def agents_summary(f: Filters = Depends(get_filters), conn: Connection = Depends(ro_conn)):
    src, sp = _agent_source(f)
    rng = [f.start, f.end]
    by_agent = query(conn, f"""
        SELECT x.agent_type, count(*) AS executions, count(*) FILTER (WHERE x.is_success) AS successes,
               count(*) FILTER (WHERE x.is_success)::float8 / NULLIF(count(*) FILTER (WHERE x.status IN ('success','failed')), 0) AS success_rate,
               count(*) FILTER (WHERE x.is_retried)::float8 / NULLIF(count(*), 0) AS retry_rate,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY x.duration_ms) FILTER (WHERE x.duration_ms >= 0) AS p50_latency_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY x.duration_ms) FILTER (WHERE x.duration_ms >= 0) AS p95_latency_ms,
               sum(x.input_tokens + x.output_tokens) AS total_tokens, round(sum(x.cost_usd_estimate)::numeric, 6)::float8 AS est_cost_usd,
               round(sum(x.cost_usd_estimate)::numeric, 6)::float8 / NULLIF(count(*), 0) AS est_cost_per_interaction_usd
        FROM {src} x WHERE x.activity_date BETWEEN %s AND %s GROUP BY 1 ORDER BY 1""", [*sp, *rng])
    failures = query(conn, f"""
        SELECT x.failure_category, count(*) AS failures,
               count(*)::float8 / sum(count(*)) OVER () AS share_of_failures
        FROM {src} x WHERE x.activity_date BETWEEN %s AND %s AND NOT x.is_success GROUP BY 1 ORDER BY 2 DESC""", [*sp, *rng])
    volume = series(conn, "agent_runs", f)
    ok = series(conn, "agent_success_rate", f)
    totals = one(conn, f"""
        SELECT count(*) AS executions, count(*) FILTER (WHERE x.is_success) AS successes,
               count(*) FILTER (WHERE x.is_success)::float8 / NULLIF(count(*) FILTER (WHERE x.status IN ('success','failed')), 0) AS success_rate,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY x.duration_ms) FILTER (WHERE x.duration_ms >= 0) AS p50_latency_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY x.duration_ms) FILTER (WHERE x.duration_ms >= 0) AS p95_latency_ms,
               round(sum(x.cost_usd_estimate)::numeric, 6)::float8 AS est_cost_usd, round(sum(x.cost_usd_estimate)::numeric, 6)::float8 / NULLIF(count(*), 0) AS est_cost_per_interaction_usd,
               count(*) FILTER (WHERE x.is_retried)::float8 / NULLIF(count(*), 0) AS retry_rate
        FROM {src} x WHERE x.activity_date BETWEEN %s AND %s""", [*sp, *rng])
    return {"filters": f.echo(), "totals": totals, "by_agent": by_agent, "failure_categories": failures,
            "volume_over_time": [{"date": v["date"], "executions": v["value"], "success_rate": o["value"]} for v, o in zip(volume, ok, strict=True)],
            "cost_disclaimer": "Costs are ESTIMATES from documented synthetic token pricing, not vendor billing.",
            "definition": _definition(conn, "agent_success_rate"), "meta": meta(conn)}
