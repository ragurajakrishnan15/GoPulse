# Event Contract v1

Machine-readable schema: `pipelines/ingestion/event_schema.json`. Validator: `pipelines/ingestion/validate.py`.
All data is **synthetic**.

## Envelope
| Field | Type | Req | Notes |
|---|---|---|---|
| `event_id` | string `evt_<16 hex>` | ✔ | Stable unique key; dedup key |
| `event_name` | string | ✔ | One of the 10 names below |
| `event_timestamp` | ISO‑8601 UTC `Z` | ✔ | When it happened (client time) |
| `ingested_at` | ISO‑8601 UTC `Z` | ✔ | When the platform received it |
| `user_id` | `usr_######` | ✔ | Null/missing → quarantine |
| `account_id` | `acc_####` or null | – | Must exist in `dim_accounts` if present |
| `session_id` | `ses_<12 hex>` | ✔ | |
| `feature_id` | `feat_*` or null | – | |
| `schema_version` | int = 1 | – | |
| `properties` | object | ✔ | See below |

## Event names
`user_signed_up`, `onboarding_started`, `onboarding_completed`, `feature_opened`, `suggestion_shown`,
`suggestion_accepted`, `agent_started`, `agent_completed`, `agent_failed`, `subscription_started`.

## Agent properties (`agent_*`)
`execution_id`, `agent_type`, `status` (`success|failed|cancelled`), `failure_category`, `duration_ms`,
`retry_count`, `input_tokens`, `output_tokens`, `cost_usd_estimate` (synthetic estimate; see `metrics.md`).
`agent_started` carries only `execution_id`, `agent_type`, `plan`.

## Quarantine reason codes
| Code | Trigger |
|---|---|
| `missing_user_id` | `user_id` null/empty |
| `invalid_timestamp` | Unparseable, before 2020‑01‑01, or > 1h in the future |
| `unknown_event_name` | Not in the list above |
| `orphaned_account` | `account_id` not in the account dimension |
| `negative_value` | Negative duration/retry/token/cost |
| `schema_mismatch` | Any other JSON Schema violation (wrong type, extra key…) |
| `duplicate_event_id` | *Cross-row*; detected in Silver, not at ingestion |

## Delivery semantics
At‑least‑once. Duplicates share `event_id`; earliest `ingested_at` wins (decision D5). Events whose
`ingested_at − event_timestamp > 3 days` are valid but flagged late (D6).
