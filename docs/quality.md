# Data Quality & dbt (Phase 3)

## Two layers of checks
| Layer | Where | Runs | Blocks publish? |
|---|---|---|---|
| Python checks | `pipelines/jobs/quality.py` | inside the pipeline transaction, before commit | `error` severity: **yes** (rollback) |
| dbt tests | `transformations/dbt` | after a successful run: `python -m pipelines.jobs.dbt_runner` | reported (`quality_check_results`, layer `dbt`) |

All results land in `quality_check_results` (run, layer, check, severity, status, affected_records, observed vs
threshold, remediation_status, sample_ref) — the single source for the Data Quality view and API.

## Python checks
| Check | Sev | Threshold | Remediation shown |
|---|---|---|---|
| `quarantine_rate_blocking` | error | > 20% of input | `blocked_publish` |
| `quarantine_rate` | warn | > 2% | `quarantined` |
| `missing_user_id`, `invalid_timestamp`, `unknown_event_name`, `orphaned_account`, `schema_mismatch`, `negative_value` | warn | any | `quarantined` |
| `duplicate_event_ids` | warn | any | `deduplicated` (earliest copy kept) |
| `volume_anomaly` | warn | ±50% vs median of last ≤5 successful runs (needs ≥2) | `flagged` |
| `silver_event_id_unique`, `silver_user_id_not_null`, `silver_non_negative_values` | error | any | `blocked_publish` |
| `silver_user_exists`, `silver_feature_exists` | warn | any | `flagged` |
| `late_arrival_rate` | warn | > 5% of batch | `flagged` |
| `missing_partitions` | warn | any date gap | `flagged` |
| `freshness` | warn | > 24h behind `reference_time` | `flagged` (skipped when no reference time — synthetic data has no live clock) |
| `gold_reconciles_to_silver`, `dau_not_above_user_count`, `agent_status_consistent` | error | any | `blocked_publish` |

When a run is blocked, its checks are still persisted; non-blocking failures are marked `not_published`
because that run was rolled back. Quarantined rows of a *blocked* batch are rolled back with it (nothing from the
batch is published); published batches keep their rows in `quarantine_events` for inspection.

## dbt project
- **Sources** (`models/staging/_sources.yml`): the Python-built Silver/Gold tables, with uniqueness, not-null,
  accepted-values and relationship tests, plus pipeline freshness (`pipeline_runs.ended_at`, warn 24h / error 72h).
- **Staging**: `stg_events` (single definition of "qualifying activity"), `stg_quarantine`, `stg_quality_latest`.
- **Marts** (rates computed once, NULL until the window has elapsed): `mart_kpi_daily`, `mart_activation_rates`,
  `mart_retention_weekly`, `mart_agent_summary`, `mart_agent_failures`, `mart_feature_adoption_weekly`,
  `mart_account_adoption`, `mart_quality_summary`.
- **Custom tests**: `rate_between_0_and_1`, `non_negative`, and `assert_reconciliations` (mart totals == facts).
- Adapter: `dbt-duckdb`. SQL uses DuckDB features (`FILTER`, `quantile_cont`); porting to `dbt-databricks`
  would need small dialect changes (documented when that integration is built).

Run: `dbt build --project-dir transformations/dbt --profiles-dir transformations/dbt` (from repo root; set
`GOPULSE_DB` to point at another database file), or `python -m pipelines.jobs.dbt_runner` to also persist results.

## Failure demonstration
`python -m pipelines.jobs.failure_demo` → `data/demo/failure_demo.json`. Measured on 500 users, seed 42:

| Step | Outcome |
|---|---|
| Clean batch (reference) | 29,180 events, 4,608 agent runs |
| ~3% defects | **published**: 750 rows quarantined, 125 duplicates removed; naive count over the raw file = 29,305 events vs 28,430 after cleaning |
| ~40% defects | **blocked** by `quarantine_rate_blocking` (10,004 records); Gold unchanged (fingerprint equal) |
| Corrected redelivery | success; every reference metric restored exactly |

Naive (no validation/dedup) counts overstate duplicated events and include null users; cleaned counts are
slightly below the reference until quarantined rows are corrected and redelivered — which the last step shows.
