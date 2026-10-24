export interface Meta {
  synthetic_data: boolean;
  last_refreshed: string | null;
  as_of_date: string | null;
  source_run_id?: string;
}

export interface Definition {
  key: string;
  label: string;
  unit: string;
  definition: string;
  numerator: string;
  denominator: string;
  time_window: string;
  eligibility: string;
  notes: string | null;
}

export interface FiltersEcho {
  start: string;
  end: string;
  tier: string | null;
  segment: string | null;
  plan: string | null;
  feature: string | null;
}

export interface Kpi {
  key: string;
  label: string;
  value: number | null;
  unit: string | null;
  numerator: number | null;
  denominator: number | null;
  definition: string | null;
  window: string | null;
  eligibility: string | null;
  applied_filters: string[];
  average_over_range?: number | null;
  as_of?: string;
  estimate?: boolean;
  note?: string;
  eligible_cohort_users?: number;
  cohort_users?: number;
}

export interface OverviewResponse {
  filters: FiltersEcho;
  kpis: Kpi[];
  meta: Meta;
}

export interface SeriesPoint {
  date: string;
  value: number | null;
  numerator: number | null;
  denominator: number | null;
}

export interface TimeseriesResponse {
  metric: string;
  unit: string;
  filters: FiltersEcho;
  definition: Definition | null;
  points: SeriesPoint[];
  meta: Meta;
}

export interface FunnelStage {
  stage: string;
  label: string;
  users: number;
  denominator: number;
  rate: number | null;
  denominator_definition: string;
  eligibility: string;
}

export interface FunnelResponse {
  filters: FiltersEcho;
  stages: FunnelStage[];
  caveat: string;
  meta: Meta;
}

export interface RetentionCell {
  eligible_users: number;
  retained_users: number;
  rate: number | null;
}

export interface RetentionResponse {
  filters: FiltersEcho;
  granularity: string;
  day_offsets: number[];
  definition: Definition | null;
  cohorts: { cohort_start: string; retention: Record<string, RetentionCell> }[];
  meta: Meta;
}

export interface FeatureAdoptionRow {
  week: string;
  feature_id: string;
  feature_name: string;
  category: string;
  feature_users: number;
  active_users: number;
  adoption_rate: number | null;
}

export interface AccountAdoptionRow {
  period_start: string;
  account_tier: string;
  eligible_accounts: number;
  adopted_accounts: number;
  adoption_rate: number | null;
  active_users: number;
}

export interface AgentRow {
  agent_type: string;
  executions: number;
  successes: number;
  success_rate: number | null;
  retry_rate: number | null;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  total_tokens: number | null;
  est_cost_usd: number | null;
  est_cost_per_interaction_usd: number | null;
}

export interface AgentsResponse {
  filters: FiltersEcho;
  totals: Omit<AgentRow, "agent_type" | "total_tokens"> & { retry_rate: number | null };
  by_agent: AgentRow[];
  failure_categories: { failure_category: string | null; failures: number; share_of_failures: number }[];
  volume_over_time: { date: string; executions: number | null; success_rate: number | null }[];
  cost_disclaimer: string;
  definition: Definition | null;
  meta: Meta;
}

export type RunSource = "local-real" | "demo-live" | "seeded-example";

export interface PipelineRun {
  run_id: string;
  pipeline: string;
  batch_id: string;
  source: RunSource;
  status: "success" | "failed";
  started_at: string;
  ended_at: string;
  duration_ms: number;
  input_count: number | null;
  accepted_count: number | null;
  quarantined_count: number | null;
  duplicate_count: number | null;
  silver_total: number | null;
  retry_count: number;
  source_watermark: string | null;
  freshness_lag_hours: number | null;
  error_summary: string | null;
  log_excerpt: string | null;
  last_successful_run_at: string | null;
  execution_label: string;
  is_real_execution: boolean;
}

export interface RunsResponse {
  items: PipelineRun[];
  page: number;
  limit: number;
  total: number;
  has_next: boolean;
  summary: {
    last_real_success_at: string | null;
    local_real_runs: number;
    demo_live_runs: number;
    seeded_example_runs: number;
  };
  labels: Record<RunSource, string>;
  meta: Meta;
}

export interface TaskRun {
  run_id: string;
  task: string;
  status: string;
  started_at: string;
  ended_at: string;
  duration_ms: number;
  input_count: number | null;
  output_count: number | null;
  retry_count: number;
  error_summary: string | null;
}

export interface RunDetailResponse {
  run: PipelineRun;
  tasks: TaskRun[];
  meta: Meta;
}

export interface DemoRunResponse {
  run_id: string;
  source: RunSource;
  status: "success" | "failed";
  scenario: string;
  seed: number;
  users: number;
  counts: Record<string, number | null>;
  error_summary: string | null;
  quality: { checks: number; failed: number; blocking: string[] };
  notice: string;
}

export interface QualityCheck {
  run_id: string;
  layer: string;
  check_name: string;
  severity: "error" | "warn";
  status: "pass" | "fail" | "skipped";
  affected_records: number;
  observed: number | null;
  threshold: number | null;
  detail: string;
  remediation_status: string;
  sample_ref: string | null;
}

export interface QualityResponse {
  run: { run_id: string; source: RunSource; status: string; started_at: string; execution_label: string } | null;
  summary: { checks: number; passed: number; failed: number; skipped: number; affected_records: number };
  items: QualityCheck[];
  meta: Meta;
}

export interface SamplesResponse {
  reason: string;
  items: { reason: string; batch_id: string; line_no: number; reasons: string[]; payload: Record<string, unknown> }[];
  note: string;
}

export type Metrics = Record<string, number | null | undefined>;

export interface FailureDemoReport {
  scenario: { users: number; seed: number };
  reference_metrics_clean: Metrics;
  step_1_dirty_batch: {
    status: string;
    ingest: { input: number; accepted: number; quarantined: number };
    duplicates_removed: number;
    failed_checks: { layer: string; check: string; severity: string; affected_records: number; remediation: string; detail: string }[];
    naive_metrics_no_cleaning: Metrics;
    published_metrics_after_cleaning: Metrics;
    delta_vs_clean_reference: Metrics;
    note: string;
  };
  step_2_toxic_batch: {
    status: string;
    error: string | null;
    ingest: { input: number; accepted: number; quarantined: number } | null;
    blocking_checks: string[];
    failed_checks: { layer: string; check: string; severity: string; affected_records: number; remediation: string; detail: string }[];
    gold_unchanged_after_block: boolean;
  };
  step_3_recovery_rerun: {
    status: string;
    published_metrics: Metrics;
    delta_vs_clean_reference: Metrics;
    converged_to_reference: boolean;
  };
}

export interface CatalogColumn {
  name: string;
  type: string;
  description: string | null;
}

export interface CatalogModel {
  name: string;
  layer: "silver" | "gold" | "mart";
  description: string;
  purpose: string | null;
  grain: string;
  primary_key: string;
  upstream: string[];
  downstream: string[];
  refresh_cadence: string;
  last_refresh: string | null;
  source_link: string;
  test_status: "passing" | "failing" | "partial" | "no_tests";
  row_count: number | null;
  columns?: CatalogColumn[];
  tests?: { name: string; source: string; status: string }[];
  metrics?: { key: string; label: string }[];
}

export interface CatalogMetric {
  key: string;
  label: string;
  unit: string;
  definition: string;
  numerator: string;
  denominator: string;
  time_window: string;
  eligibility: string;
  notes: string | null;
  source_model: string;
}

export interface FeatureItem {
  feature_id: string;
  name: string;
  category: string;
}
