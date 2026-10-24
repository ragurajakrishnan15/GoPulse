-- Serving schema (PostgreSQL). Owned by the publish job; the API only reads it (plus run-history inserts for demo runs).
-- Everything here is SYNTHETIC demo data.
CREATE SCHEMA IF NOT EXISTS serving;
SET search_path = serving;

CREATE TABLE IF NOT EXISTS publish_meta (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    published_at TIMESTAMPTZ NOT NULL,
    source_run_id TEXT, source_batch_id TEXT, as_of_date DATE, as_of_ts TIMESTAMP,
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE);

CREATE TABLE IF NOT EXISTS features (feature_id TEXT PRIMARY KEY, name TEXT, category TEXT);

-- User-level tables back every filterable endpoint (tier / segment / plan / feature).
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY, signup_date DATE NOT NULL, account_id TEXT,
    account_tier TEXT NOT NULL, account_segment TEXT NOT NULL, current_plan TEXT NOT NULL,
    onboarding_started BOOLEAN NOT NULL, activated_7d BOOLEAN NOT NULL, converted_30d BOOLEAN NOT NULL);
CREATE INDEX IF NOT EXISTS users_signup_idx ON users (signup_date);

CREATE TABLE IF NOT EXISTS activity_days (user_id TEXT NOT NULL, activity_date DATE NOT NULL, PRIMARY KEY (user_id, activity_date));
CREATE INDEX IF NOT EXISTS activity_days_date_idx ON activity_days (activity_date);

CREATE TABLE IF NOT EXISTS user_day_feature (
    activity_date DATE NOT NULL, user_id TEXT NOT NULL, feature_id TEXT NOT NULL, events INT NOT NULL,
    PRIMARY KEY (activity_date, user_id, feature_id));
CREATE INDEX IF NOT EXISTS udf_feature_idx ON user_day_feature (feature_id, activity_date);

CREATE TABLE IF NOT EXISTS agent_runs (
    execution_id TEXT PRIMARY KEY, activity_date DATE NOT NULL, user_id TEXT NOT NULL, feature_id TEXT,
    agent_type TEXT NOT NULL, status TEXT NOT NULL, is_success BOOLEAN NOT NULL, failure_category TEXT,
    duration_ms BIGINT, retry_count INT, is_retried BOOLEAN, input_tokens BIGINT, output_tokens BIGINT,
    cost_usd_estimate DOUBLE PRECISION);
CREATE INDEX IF NOT EXISTS agent_runs_date_idx ON agent_runs (activity_date);

-- dbt marts, copied verbatim (used for unfiltered fast paths and for reconciliation tests)
CREATE TABLE IF NOT EXISTS mart_kpi_daily (
    date DATE PRIMARY KEY, dau INT, wau INT, new_signups INT, registered_users BIGINT, agent_runs INT,
    agent_success_rate DOUBLE PRECISION, agent_retry_rate DOUBLE PRECISION, p50_latency_ms DOUBLE PRECISION,
    p95_latency_ms DOUBLE PRECISION, est_cost_usd DOUBLE PRECISION, est_cost_per_interaction_usd DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS mart_activation_rates (
    signup_date DATE, account_tier TEXT, signups INT, onboarding_started INT, activated_7d INT, converted_30d INT,
    activation_window_complete BOOLEAN, conversion_window_complete BOOLEAN, activation_rate DOUBLE PRECISION,
    conversion_rate DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS mart_retention_weekly (
    cohort_week DATE, account_tier TEXT, day_n INT, eligible_users BIGINT, retained_users BIGINT,
    retention_rate DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS mart_agent_summary (
    agent_type TEXT PRIMARY KEY, executions INT, successes INT, success_rate DOUBLE PRECISION,
    retry_rate DOUBLE PRECISION, p50_latency_ms DOUBLE PRECISION, p95_latency_ms DOUBLE PRECISION,
    total_tokens BIGINT, est_cost_usd DOUBLE PRECISION, est_cost_per_interaction_usd DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS mart_agent_failures (
    failure_category TEXT, failures INT, avg_retries DOUBLE PRECISION, share_of_failures DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS mart_feature_adoption_weekly (
    week DATE, feature_id TEXT, feature_name TEXT, category TEXT, feature_users INT, active_users INT,
    adoption_rate DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS mart_account_adoption (
    period_start DATE, account_tier TEXT, eligible_accounts INT, adopted_accounts INT,
    adoption_rate DOUBLE PRECISION, active_users BIGINT);

-- Observability. `source` distinguishes REAL executions from SEEDED example history.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY, pipeline TEXT, batch_id TEXT, source TEXT NOT NULL
        CHECK (source IN ('local-real', 'demo-live', 'seeded-example')),
    status TEXT NOT NULL, started_at TIMESTAMP, ended_at TIMESTAMP, duration_ms BIGINT, input_count BIGINT,
    accepted_count BIGINT, quarantined_count BIGINT, duplicate_count BIGINT, silver_total BIGINT,
    retry_count INT DEFAULT 0, source_watermark TEXT, freshness_lag_hours DOUBLE PRECISION,
    error_summary TEXT, log_excerpt TEXT);
CREATE INDEX IF NOT EXISTS pipeline_runs_started_idx ON pipeline_runs (started_at DESC);
CREATE TABLE IF NOT EXISTS pipeline_task_runs (
    run_id TEXT NOT NULL, task TEXT NOT NULL, status TEXT, started_at TIMESTAMP, ended_at TIMESTAMP,
    duration_ms BIGINT, input_count BIGINT, output_count BIGINT, retry_count INT DEFAULT 0, error_summary TEXT,
    PRIMARY KEY (run_id, task));
CREATE TABLE IF NOT EXISTS quality_check_results (
    run_id TEXT NOT NULL, batch_id TEXT, source TEXT NOT NULL DEFAULT 'local-real', layer TEXT NOT NULL,
    check_name TEXT NOT NULL, severity TEXT, status TEXT, affected_records BIGINT, observed DOUBLE PRECISION,
    threshold DOUBLE PRECISION, detail TEXT, remediation_status TEXT, sample_ref TEXT, checked_at TIMESTAMP);
CREATE INDEX IF NOT EXISTS qcr_run_idx ON quality_check_results (run_id);
CREATE TABLE IF NOT EXISTS quality_samples (
    reason TEXT NOT NULL, batch_id TEXT, line_no BIGINT, reasons TEXT[], payload JSONB);
CREATE INDEX IF NOT EXISTS qs_reason_idx ON quality_samples (reason);
CREATE TABLE IF NOT EXISTS failure_demo_report (id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1), report JSONB NOT NULL);

-- Catalog
CREATE TABLE IF NOT EXISTS catalog_models (
    name TEXT PRIMARY KEY, layer TEXT, description TEXT, purpose TEXT, grain TEXT, primary_key TEXT,
    upstream TEXT[], downstream TEXT[], refresh_cadence TEXT, last_refresh TIMESTAMPTZ, source_link TEXT,
    columns JSONB, tests JSONB, test_status TEXT, row_count BIGINT);
CREATE TABLE IF NOT EXISTS catalog_metrics (
    key TEXT PRIMARY KEY, label TEXT, unit TEXT, definition TEXT, numerator TEXT, denominator TEXT,
    time_window TEXT, eligibility TEXT, notes TEXT, source_model TEXT);

-- Least-privilege grants (roles are created by infrastructure/docker/postgres/init.sh or the hosting provider).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gopulse_api_ro') THEN
        GRANT USAGE ON SCHEMA serving TO gopulse_api_ro;
        GRANT SELECT ON ALL TABLES IN SCHEMA serving TO gopulse_api_ro;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gopulse_api_rw') THEN
        GRANT USAGE ON SCHEMA serving TO gopulse_api_rw;
        GRANT SELECT ON ALL TABLES IN SCHEMA serving TO gopulse_api_rw;
        GRANT INSERT ON serving.pipeline_runs, serving.pipeline_task_runs, serving.quality_check_results TO gopulse_api_rw;
    END IF;
END $$;
