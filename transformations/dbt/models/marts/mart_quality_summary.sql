-- Latest-run quality results rolled up by layer and severity (feeds the Data Quality view).
select layer, severity,
       count(*) as checks,
       count(*) filter (where status = 'pass') as passed,
       count(*) filter (where status = 'fail') as failed,
       count(*) filter (where status = 'skipped') as skipped,
       sum(affected_records) filter (where status = 'fail') as affected_records
from {{ ref('stg_quality_latest') }}
group by 1, 2
