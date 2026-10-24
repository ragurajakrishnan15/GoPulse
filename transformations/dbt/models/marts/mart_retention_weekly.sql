-- Grain: signup week x account_tier x day_n. Only complete observation windows contribute, per docs/metrics.md.
-- Cohort users whose day-n has not elapsed are excluded from BOTH numerator and denominator.
select
    date_trunc('week', signup_date) as cohort_week,
    account_tier,
    day_n,
    sum(cohort_users) filter (where window_complete) as eligible_users,
    sum(retained_users) filter (where window_complete) as retained_users,
    {{ safe_rate('sum(retained_users) filter (where window_complete)',
                 'sum(cohort_users) filter (where window_complete)') }} as retention_rate
from {{ source('lakehouse', 'agg_retention_cohorts') }}
group by 1, 2, 3
