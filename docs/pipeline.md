# Local Pipeline (Phase 2)

```
data/raw/<batch>/{events,users,accounts,features}.jsonl
   │ ingest.py   validate → accepted | quarantine (reason codes)
   ▼
bronze_events (raw payload, duplicates kept)     quarantine_events (payload + reasons)
   │ silver.py   type, dedupe (earliest ingested_at wins), flag late, rebuild touched activity_date partitions
   ▼
silver_events
   │ gold.py     dims, facts, aggregates (CREATE OR REPLACE, same transaction)
   ▼
dim_* fct_* agg_*  ──► data/lake/gold/*.parquet   +   pipeline_runs / pipeline_task_runs / pipeline_watermark
```

Run: `python -m pipelines.jobs.run --batch data/raw/clean [--db data/gopulse.duckdb] [--lake data/lake]`

## Gold models
| Model | Grain |
|---|---|
| `dim_users` / `dim_accounts` / `dim_features` | user / account / feature |
| `fct_product_events` | accepted unique event |
| `fct_agent_interactions` | agent execution (terminal event + start time) |
| `fct_daily_user_activity` | user × UTC activity date (qualifying events only) |
| `fct_user_lifecycle` | user (onboarding/subscription milestones, `activated_7d`, `converted_30d`) |
| `fct_account_adoption` | account × month |
| `agg_product_metrics_daily` | date × account tier (DAU, signups, agent runs/successes/retries, P50/P95, tokens, est. cost) |
| `agg_activation_funnel_daily` | signup date × tier (+ `*_window_complete` flags) |
| `agg_retention_cohorts` | signup date × tier × day_n ∈ {1,7,14,30} (+ `window_complete`) |

Rates are computed by consumers as `sum(numerator)/sum(denominator)` over rows with `*_window_complete = true`.

## Reconciliation (fails the run → rollback)
1. `input = accepted + quarantined`
2. `accepted = silver rows kept from this batch + duplicates dropped`
3. `fct_product_events = silver_events`

## Observability tables
`pipeline_runs` (status, counts, duration, watermark, error), `pipeline_task_runs` (per task), `pipeline_watermark`.
All rows written by this runner have `mode='local-real'`; the website will label seeded demo history separately.
