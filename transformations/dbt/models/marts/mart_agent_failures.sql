-- Grain: failure_category. Share is of failed executions.
with f as (
    select failure_category, count(*) as failures, round(avg(retry_count), 3) as avg_retries
    from {{ source('lakehouse', 'fct_agent_interactions') }}
    where not is_success group by 1
)
select failure_category, failures, avg_retries,
       round(failures * 1.0 / sum(failures) over (), 6) as share_of_failures
from f
