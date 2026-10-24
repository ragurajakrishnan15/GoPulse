-- Check results from the most recent pipeline run only.
select q.*
from {{ source('lakehouse', 'quality_check_results') }} q
where q.run_id = (
    select run_id from {{ source('lakehouse', 'pipeline_runs') }} order by started_at desc limit 1)
