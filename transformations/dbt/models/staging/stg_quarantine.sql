-- One row per (quarantined record, reason).
select batch_id, line_no, unnest(reasons) as reason, quarantined_at
from {{ source('lakehouse', 'quarantine_events') }}
