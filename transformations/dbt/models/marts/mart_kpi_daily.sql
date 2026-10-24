-- Grain: one row per UTC date. Whole-population KPIs (not tiered), so percentiles are computed on raw executions.
with activity as (
    select user_id, activity_date from {{ source('lakehouse', 'fct_daily_user_activity') }}
),
spine as (
    select activity_date as date from activity
    union
    select signup_date from {{ source('lakehouse', 'dim_users') }}
),
dau as (
    select activity_date as date, count(distinct user_id) as dau from activity group by 1
),
wau as (
    select s.date, count(distinct a.user_id) as wau
    from spine s join activity a on a.activity_date between s.date - 6 and s.date
    group by 1
),
signups as (
    select signup_date as date, count(*) as new_signups from {{ source('lakehouse', 'dim_users') }} group by 1
),
agents as (
    select activity_date as date,
           count(*) as agent_runs,
           count(*) filter (where is_success) as agent_successes,
           count(*) filter (where is_retried) as agent_retries,
           round(quantile_cont(duration_ms, 0.5), 1) as p50_latency_ms,
           round(quantile_cont(duration_ms, 0.95), 1) as p95_latency_ms,
           round(sum(cost_usd_estimate), 6) as est_cost_usd
    from {{ source('lakehouse', 'fct_agent_interactions') }}
    where duration_ms >= 0
    group by 1
)
select
    s.date,
    coalesce(d.dau, 0) as dau,
    coalesce(w.wau, 0) as wau,
    coalesce(su.new_signups, 0) as new_signups,
    sum(coalesce(su.new_signups, 0)) over (order by s.date) as registered_users,
    coalesce(a.agent_runs, 0) as agent_runs,
    {{ safe_rate('a.agent_successes', 'a.agent_runs') }} as agent_success_rate,
    {{ safe_rate('a.agent_retries', 'a.agent_runs') }} as agent_retry_rate,
    a.p50_latency_ms,
    a.p95_latency_ms,
    coalesce(a.est_cost_usd, 0) as est_cost_usd,
    case when a.agent_runs > 0 then round(a.est_cost_usd / a.agent_runs, 6) end as est_cost_per_interaction_usd
from spine s
left join dau d using (date)
left join wau w using (date)
left join signups su using (date)
left join agents a using (date)
