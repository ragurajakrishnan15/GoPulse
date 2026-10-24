-- Grain: month x account tier. ACCOUNT-level metric: adopted accounts / eligible accounts (never mixed with users).
select
    period_start,
    tier as account_tier,
    count(*) as eligible_accounts,
    count(*) filter (where is_adopted) as adopted_accounts,
    {{ safe_rate('count(*) filter (where is_adopted)', 'count(*)') }} as adoption_rate,
    sum(active_users) as active_users
from {{ source('lakehouse', 'fct_account_adoption') }}
group by 1, 2
