-- Mart totals must reconcile to the facts they are built from. Any returned row is a failure.
-- 1) DAU summed over days == user-days in the activity fact
select 'dau_vs_activity' as check_name, a.n as mart_value, b.n as fact_value
from (select sum(dau) as n from {{ ref('mart_kpi_daily') }}) a
cross join (select count(*) as n from {{ source('lakehouse', 'fct_daily_user_activity') }}) b
where a.n <> b.n

union all
-- 2) every user lands in exactly one activation cohort bucket and one signup day
select 'signups_vs_users_activation', a.n, b.n
from (select count(*) as n from {{ source('lakehouse', 'dim_users') }}) a
cross join (select sum(signups) as n from {{ ref('mart_activation_rates') }}) b
where a.n <> b.n

union all
select 'signups_vs_users_kpi', a.n, b.n
from (select count(*) as n from {{ source('lakehouse', 'dim_users') }}) a
cross join (select sum(new_signups) as n from {{ ref('mart_kpi_daily') }}) b
where a.n <> b.n

union all
-- 3) agent mart covers every valid execution
select 'agent_executions', a.n, b.n
from (select sum(executions) as n from {{ ref('mart_agent_summary') }}) a
cross join (select count(*) as n from {{ source('lakehouse', 'fct_agent_interactions') }} where duration_ms >= 0) b
where a.n <> b.n

union all
-- 4) failure shares form a distribution
select 'failure_share_sum', round(s, 4), 1
from (select sum(share_of_failures) as s from {{ ref('mart_agent_failures') }})
where s is not null and abs(s - 1) > 0.001
