-- Grain: week x feature. Numerator: distinct users who opened the feature that week.
-- Denominator: distinct users with any qualifying activity that week (user-level; never account-level).
with active as (
    select date_trunc('week', activity_date) as week, count(distinct user_id) as active_users
    from {{ source('lakehouse', 'fct_daily_user_activity') }} group by 1
),
used as (
    select date_trunc('week', activity_date) as week, feature_id, count(distinct user_id) as feature_users
    from {{ ref('stg_events') }}
    where event_name = 'feature_opened' and feature_id is not null group by 1, 2
)
select u.week, u.feature_id, f.feature_name, f.category, u.feature_users, a.active_users,
       {{ safe_rate('u.feature_users', 'a.active_users') }} as adoption_rate
from used u
join active a using (week)
join {{ source('lakehouse', 'dim_features') }} f using (feature_id)
