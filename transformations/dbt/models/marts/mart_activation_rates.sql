-- Grain: signup_date x account_tier. Rates are NULL until the 7d / 30d observation window has fully elapsed.
-- Numerator: users with onboarding_completed within 7d of signup (activation) / subscription_started within 30d (conversion).
-- Denominator: all users in the signup cohort.
select
    signup_date,
    account_tier,
    signups,
    onboarding_started,
    activated_7d,
    converted_30d,
    activation_window_complete,
    conversion_window_complete,
    {{ safe_rate('activated_7d', 'signups', 'activation_window_complete') }} as activation_rate,
    {{ safe_rate('converted_30d', 'signups', 'conversion_window_complete') }} as conversion_rate
from {{ source('lakehouse', 'agg_activation_funnel_daily') }}
