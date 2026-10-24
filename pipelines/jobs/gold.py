"""Gold: dimensions, facts and aggregates, rebuilt from Silver in the caller's transaction.

Every table is replaced atomically with the rest of the run (DuckDB DDL is transactional), so readers never
see a partially refreshed Gold. See docs/metrics.md for metric semantics.
"""
from __future__ import annotations

QUALIFYING = "('feature_opened','suggestion_shown','suggestion_accepted','agent_started','agent_completed','agent_failed')"
COHORT_DAYS = (1, 7, 14, 30)

# Order matters: later statements read earlier ones.
GOLD_SQL: dict[str, str] = {
    "gold_meta": """
        SELECT max(event_ts) AS as_of_ts, CAST(max(event_ts) AS DATE) AS as_of_date FROM silver_events""",
    "dim_accounts": """
        SELECT account_id, tier, segment, CAST(created_at AS DATE) AS created_date FROM raw_accounts""",
    "dim_features": "SELECT feature_id, name AS feature_name, category FROM raw_features",
    "dim_users": """
        SELECT u.user_id, u.account_id, u.signup_at, CAST(u.signup_at AS DATE) AS signup_date, u.country,
               coalesce(a.tier, 'no_account') AS account_tier, coalesce(a.segment, 'no_account') AS account_segment,
               CASE WHEN s.user_id IS NOT NULL THEN 'pro' ELSE 'free' END AS current_plan
        FROM raw_users u
        LEFT JOIN raw_accounts a USING (account_id)
        LEFT JOIN (SELECT DISTINCT user_id FROM silver_events WHERE event_name = 'subscription_started') s
               USING (user_id)""",
    "fct_product_events": """
        SELECT event_id, event_name, event_ts, activity_date, ingested_at, is_late, user_id, account_id, session_id,
               feature_id, (event_name IN %(q)s) AS is_qualifying_activity
        FROM silver_events""" % {"q": QUALIFYING},
    "fct_agent_interactions": """
        WITH term AS (
            SELECT *, row_number() OVER (PARTITION BY execution_id ORDER BY event_ts DESC, event_id) AS rn
            FROM silver_events WHERE event_name IN ('agent_completed','agent_failed') AND execution_id IS NOT NULL),
        started AS (
            SELECT execution_id, min(event_ts) AS started_at FROM silver_events
            WHERE event_name = 'agent_started' AND execution_id IS NOT NULL GROUP BY 1)
        SELECT t.execution_id, t.user_id, t.account_id, t.feature_id, t.agent_type, t.activity_date,
               coalesce(s.started_at, t.event_ts - to_milliseconds(coalesce(t.duration_ms, 0))) AS started_at,
               t.event_ts AS ended_at, t.status, (t.status = 'success') AS is_success, t.failure_category,
               t.duration_ms, t.retry_count, (t.retry_count >= 1) AS is_retried, t.input_tokens, t.output_tokens,
               t.cost_usd_estimate
        FROM term t LEFT JOIN started s USING (execution_id) WHERE t.rn = 1""",
    "fct_daily_user_activity": """
        SELECT user_id, activity_date, count(*) AS qualifying_events, count(DISTINCT session_id) AS sessions,
               count(DISTINCT feature_id) AS features_used,
               count(*) FILTER (WHERE event_name = 'suggestion_shown') AS suggestions_shown,
               count(*) FILTER (WHERE event_name = 'suggestion_accepted') AS suggestions_accepted,
               count(*) FILTER (WHERE event_name IN ('agent_completed','agent_failed')) AS agent_runs
        FROM silver_events WHERE event_name IN %(q)s GROUP BY user_id, activity_date""" % {"q": QUALIFYING},
    "fct_user_lifecycle": """
        SELECT u.user_id, u.signup_date, u.account_tier,
               min(e.event_ts) FILTER (WHERE e.event_name = 'onboarding_started') AS onboarding_started_at,
               min(e.event_ts) FILTER (WHERE e.event_name = 'onboarding_completed') AS onboarding_completed_at,
               min(e.event_ts) FILTER (WHERE e.event_name = 'subscription_started') AS subscribed_at,
               CAST(min(e.event_ts) FILTER (WHERE e.event_name = 'onboarding_completed') AS TIMESTAMP)
                   <= u.signup_at + INTERVAL 7 DAY AS activated_7d,
               CAST(min(e.event_ts) FILTER (WHERE e.event_name = 'subscription_started') AS TIMESTAMP)
                   <= u.signup_at + INTERVAL 30 DAY AS converted_30d
        FROM dim_users u LEFT JOIN silver_events e USING (user_id)
        GROUP BY u.user_id, u.signup_date, u.account_tier, u.signup_at""",
    "agg_activation_funnel_daily": """
        SELECT l.signup_date, l.account_tier, count(*) AS signups,
               count(*) FILTER (WHERE onboarding_started_at IS NOT NULL) AS onboarding_started,
               count(*) FILTER (WHERE coalesce(activated_7d, false)) AS activated_7d,
               count(*) FILTER (WHERE coalesce(converted_30d, false)) AS converted_30d,
               l.signup_date + 7 < m.as_of_date AS activation_window_complete,
               l.signup_date + 30 < m.as_of_date AS conversion_window_complete
        FROM fct_user_lifecycle l CROSS JOIN gold_meta m
        GROUP BY l.signup_date, l.account_tier, m.as_of_date""",
    "agg_retention_cohorts": """
        SELECT u.signup_date, u.account_tier, d.day_n, count(*) AS cohort_users,
               count(*) FILTER (WHERE a.user_id IS NOT NULL) AS retained_users,
               u.signup_date + d.day_n < m.as_of_date AS window_complete
        FROM dim_users u
        CROSS JOIN (SELECT unnest([%(days)s]) AS day_n) d
        CROSS JOIN gold_meta m
        LEFT JOIN fct_daily_user_activity a
               ON a.user_id = u.user_id AND a.activity_date = u.signup_date + CAST(d.day_n AS INTEGER)
        GROUP BY u.signup_date, u.account_tier, d.day_n, m.as_of_date""" % {"days": ",".join(map(str, COHORT_DAYS))},
    "agg_product_metrics_daily": """
        WITH act AS (
            SELECT a.activity_date AS date, u.account_tier, count(DISTINCT a.user_id) AS dau,
                   sum(a.qualifying_events) AS qualifying_events
            FROM fct_daily_user_activity a JOIN dim_users u USING (user_id) GROUP BY 1, 2),
        ag AS (
            SELECT i.activity_date AS date, coalesce(a.tier, 'no_account') AS account_tier,
                   count(*) AS agent_runs, count(*) FILTER (WHERE is_success) AS agent_successes,
                   count(*) FILTER (WHERE is_retried) AS agent_retries,
                   quantile_cont(duration_ms, 0.5) FILTER (WHERE duration_ms >= 0) AS p50_latency_ms,
                   round(quantile_cont(duration_ms, 0.95) FILTER (WHERE duration_ms >= 0), 3) AS p95_latency_ms,
                   sum(coalesce(input_tokens, 0) + coalesce(output_tokens, 0)) AS total_tokens,
                   round(sum(cost_usd_estimate), 6) AS est_cost_usd
            FROM fct_agent_interactions i LEFT JOIN dim_accounts a USING (account_id) GROUP BY 1, 2),
        su AS (SELECT signup_date AS date, account_tier, count(*) AS new_signups FROM dim_users GROUP BY 1, 2),
        keys AS (SELECT date, account_tier FROM act UNION SELECT date, account_tier FROM ag
                 UNION SELECT date, account_tier FROM su)
        SELECT k.date, k.account_tier, coalesce(act.dau, 0) AS dau, coalesce(act.qualifying_events, 0) AS qualifying_events,
               coalesce(su.new_signups, 0) AS new_signups, coalesce(ag.agent_runs, 0) AS agent_runs,
               coalesce(ag.agent_successes, 0) AS agent_successes, coalesce(ag.agent_retries, 0) AS agent_retries,
               ag.p50_latency_ms, ag.p95_latency_ms, coalesce(ag.total_tokens, 0) AS total_tokens,
               coalesce(ag.est_cost_usd, 0) AS est_cost_usd
        FROM keys k
        LEFT JOIN act USING (date, account_tier) LEFT JOIN ag USING (date, account_tier)
        LEFT JOIN su USING (date, account_tier)""",
    "fct_account_adoption": """
        WITH months AS (
            SELECT DISTINCT date_trunc('month', activity_date) AS period_start FROM fct_daily_user_activity),
        usage AS (
            SELECT u.account_id, date_trunc('month', a.activity_date) AS period_start,
                   count(DISTINCT a.user_id) AS active_users, sum(a.qualifying_events) AS qualifying_events,
                   sum(a.agent_runs) AS agent_runs
            FROM fct_daily_user_activity a JOIN dim_users u USING (user_id)
            WHERE u.account_id IS NOT NULL GROUP BY 1, 2),
        members AS (
            SELECT u.account_id, m.period_start, count(*) AS member_users
            FROM dim_users u CROSS JOIN months m
            WHERE u.account_id IS NOT NULL AND u.signup_date < m.period_start + INTERVAL 1 MONTH
            GROUP BY 1, 2)
        SELECT a.account_id, m.period_start, a.tier, a.segment, coalesce(mb.member_users, 0) AS member_users,
               coalesce(us.active_users, 0) AS active_users, coalesce(us.qualifying_events, 0) AS qualifying_events,
               coalesce(us.agent_runs, 0) AS agent_runs, coalesce(us.active_users, 0) > 0 AS is_adopted
        FROM dim_accounts a CROSS JOIN months m
        LEFT JOIN members mb ON mb.account_id = a.account_id AND mb.period_start = m.period_start
        LEFT JOIN usage us ON us.account_id = a.account_id AND us.period_start = m.period_start
        WHERE a.created_date < m.period_start + INTERVAL 1 MONTH""",
}


def build_gold(con) -> dict[str, int]:
    """Replace every Gold table. Caller owns the transaction. Returns row counts per table."""
    counts = {}
    for name, sql in GOLD_SQL.items():
        con.execute(f"CREATE OR REPLACE TABLE {name} AS {sql}")
        counts[name] = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
    return counts
