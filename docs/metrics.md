# Metric Definitions

Time zone: **UTC**. Activity date = UTC date of `event_timestamp`. Only events that passed validation and
deduplication (Silver) count. Late events are included once ingested; affected daily partitions are rebuilt
within the 3‑day lookback. Missing events are not imputed.

"Qualifying activity" = `feature_opened`, `suggestion_shown`, `suggestion_accepted`, `agent_started`,
`agent_completed`, `agent_failed`. (`user_signed_up`, onboarding and `subscription_started` are lifecycle events and do not count as activity.)

| Metric | Numerator | Denominator | Window / eligibility |
|---|---|---|---|
| **DAU** | distinct `user_id` with ≥1 qualifying event | – | one UTC date |
| **WAU** | same, over trailing 7 UTC days incl. date | – | rolling 7d |
| **Activation rate** | signup‑cohort users with `onboarding_completed` ≤ 7d after `user_signed_up` | users in signup cohort whose 7d window has fully elapsed | cohort = signup date (or week) |
| **Retention Dn** | cohort users with qualifying activity on day `n` after signup (day 0 = signup date) | cohort users whose day `n` has elapsed | n ∈ {1, 7, 14, 30} |
| **Conversion rate** | users with `subscription_started` ≤ 30d after signup | users in signup cohort whose 30d window has elapsed | user‑level |
| **Feature adoption** | distinct users with `feature_opened` for the feature in period | distinct active users in period | user‑level |
| **Account adoption** | accounts with ≥1 active user in period | accounts created on/before period end | **account‑level** (never mixed with user denominators) |
| **Agent success rate** | executions ending in `agent_completed` | executions ending in `agent_completed` or `agent_failed` | `cancelled`/unfinished excluded |
| **P50/P95 latency** | percentile of `duration_ms` | – | terminal agent events with `duration_ms ≥ 0` |
| **Retry rate** | executions with `retry_count ≥ 1` | terminal executions | |
| **Est. cost / interaction** | Σ `cost_usd_estimate` | terminal executions | **estimate** |

## Cost assumptions (synthetic)
`cost = (input_tokens × $3.00 + output_tokens × $15.00) / 1,000,000 × (1 + retry_count)`.
These are illustrative assumptions, not vendor billing.

## Caveats
Funnels show association, not causation (e.g. activated users converting more says nothing about the
effect of onboarding). Cohorts with incomplete windows are excluded from rates, not zero‑filled.
