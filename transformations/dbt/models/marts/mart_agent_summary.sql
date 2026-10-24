-- Grain: agent_type. Cost is an ESTIMATE from synthetic pricing assumptions (docs/metrics.md), not vendor billing.
select
    agent_type,
    count(*) as executions,
    count(*) filter (where is_success) as successes,
    {{ safe_rate('count(*) filter (where is_success)', 'count(*)') }} as success_rate,
    {{ safe_rate('count(*) filter (where is_retried)', 'count(*)') }} as retry_rate,
    round(quantile_cont(duration_ms, 0.5), 1) as p50_latency_ms,
    round(quantile_cont(duration_ms, 0.95), 1) as p95_latency_ms,
    sum(input_tokens + output_tokens) as total_tokens,
    round(sum(cost_usd_estimate), 6) as est_cost_usd,
    round(sum(cost_usd_estimate) / count(*), 6) as est_cost_per_interaction_usd
from {{ source('lakehouse', 'fct_agent_interactions') }}
where duration_ms >= 0
group by 1
