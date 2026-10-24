-- Qualifying activity flag in one place; downstream marts must not redefine it (docs/metrics.md).
select
    event_id, event_name, event_ts, activity_date, user_id, account_id, session_id, feature_id, is_late,
    event_name in ('feature_opened', 'suggestion_shown', 'suggestion_accepted',
                   'agent_started', 'agent_completed', 'agent_failed') as is_qualifying_activity
from {{ source('lakehouse', 'silver_events') }}
