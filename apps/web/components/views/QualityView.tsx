"use client";

import { useId, useState } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { LayerBadge, SeverityBadge, SourceBadge, StatusBadge } from "@/components/Badges";
import { DataTable } from "@/components/DataTable";
import { SectionTitle } from "@/components/PageHeader";
import { useApi } from "@/hooks/useApi";
import { fmtDateTime, fmtInt, fmtMs, fmtPct, fmtUsd, titleCase } from "@/lib/format";
import type { FailureDemoReport, Metrics, QualityCheck, QualityResponse, SamplesResponse } from "@/lib/types";

const SAMPLE_REASON: Record<string, string> = {
  missing_user_id: "missing_user_id", invalid_timestamp: "invalid_timestamp", unknown_event_name: "unknown_event_name",
  orphaned_account: "orphaned_account", schema_mismatch: "schema_mismatch", negative_value: "negative_value",
  duplicate_event_ids: "duplicate_event_id",
};
const REMEDIATION: Record<string, string> = {
  quarantined: "Quarantined (kept for audit, excluded from metrics)", deduplicated: "Deduplicated (earliest copy kept)",
  blocked_publish: "Publish blocked - previous data stays live", flagged: "Flagged for review", not_published: "Run rolled back - nothing published",
  open: "Open", none: "-", skipped: "Skipped",
};
const selectCls = "rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink";

function Samples({ reason, onClose }: { reason: string; onClose: () => void }) {
  const state = useApi<SamplesResponse>("/api/v1/quality/samples", { reason, limit: 5 });
  return (
    <div className="mt-3 rounded-lg border border-line bg-surface p-3" data-testid="samples">
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-sm font-semibold text-ink">Failed rows: {titleCase(reason)} (synthetic samples)</h4>
        <button type="button" onClick={onClose} className="rounded-md border border-line px-2 py-1 text-xs hover:bg-surface2">Close</button>
      </div>
      <AsyncBoundary state={state} label="failed row samples" variant="table" isEmpty={(d) => d.items.length === 0}>
        {(d) => (
          <ul className="space-y-2">
            {d.items.map((s) => (
              <li key={`${s.batch_id}-${s.line_no}`}>
                <p className="text-xs text-muted">Line {s.line_no} · reasons: {s.reasons.join(", ")}</p>
                <pre tabIndex={0} className="max-h-40 overflow-auto rounded-md border border-line bg-surface2 p-2 font-mono text-xs text-ink">{JSON.stringify(s.payload, null, 2)}</pre>
              </li>
            ))}
          </ul>
        )}
      </AsyncBoundary>
    </div>
  );
}

function Checks() {
  const uid = useId();
  const [layer, setLayer] = useState("");
  const [status, setStatus] = useState("");
  const [sample, setSample] = useState<string | null>(null);
  const state = useApi<QualityResponse>("/api/v1/quality/checks", { layer, status });

  return (
    <AsyncBoundary state={state} label="quality checks" variant="table">
      {(d) => (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {[["Checks run", d.summary.checks], ["Passed", d.summary.passed], ["Failed / flagged", d.summary.failed], ["Records affected", d.summary.affected_records]].map(([l, v]) => (
              <div key={l as string} className="rounded-xl border border-line bg-surface p-4">
                <p className="text-sm font-medium text-muted">{l}</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">{fmtInt(v as number)}</p>
              </div>
            ))}
          </div>
          {d.run && (
            <p className="flex flex-wrap items-center gap-2 text-sm text-muted">
              Results for run <code className="text-xs text-ink">{d.run.run_id}</code> <SourceBadge source={d.run.source} /> <StatusBadge status={d.run.status} /> started {fmtDateTime(d.run.started_at)}
            </p>
          )}
          <div className="flex flex-wrap gap-3">
            <div>
              <label htmlFor={`${uid}-l`} className="mb-1 block text-xs font-medium text-muted">Layer</label>
              <select id={`${uid}-l`} className={selectCls} value={layer} onChange={(e) => setLayer(e.target.value)}>
                <option value="">All</option><option value="ingestion">Ingestion</option><option value="silver">Silver</option><option value="gold">Gold</option><option value="dbt">dbt tests</option>
              </select>
            </div>
            <div>
              <label htmlFor={`${uid}-s`} className="mb-1 block text-xs font-medium text-muted">Result</label>
              <select id={`${uid}-s`} className={selectCls} value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">All</option><option value="fail">Failed / flagged</option><option value="pass">Passed</option><option value="skipped">Skipped</option>
              </select>
            </div>
          </div>
          <DataTable<QualityCheck> caption="Data quality check results" maxHeight="28rem" rowKey={(c) => `${c.layer}-${c.check_name}`} rows={d.items} dense
            columns={[
              { key: "check_name", header: "Check", render: (c) => <span className="font-medium">{titleCase(c.check_name)}</span> },
              { key: "layer", header: "Layer", render: (c) => <LayerBadge layer={c.layer} /> },
              { key: "severity", header: "Severity", render: (c) => <SeverityBadge severity={c.severity} /> },
              { key: "status", header: "Result", render: (c) => <StatusBadge status={c.status} /> },
              { key: "affected_records", header: "Affected", align: "right", render: (c) => fmtInt(c.affected_records) },
              { key: "remediation_status", header: "Remediation", render: (c) => REMEDIATION[c.remediation_status] ?? c.remediation_status },
              { key: "detail", header: "Detail", render: (c) => (
                <span>{c.detail}{c.status === "fail" && SAMPLE_REASON[c.check_name] && (
                  <button type="button" onClick={() => setSample(SAMPLE_REASON[c.check_name])}
                    className="ml-2 rounded-md border border-line px-2 py-0.5 text-xs font-medium text-accent hover:bg-surface2">
                    Inspect rows<span className="sr-only"> for {titleCase(c.check_name)}</span>
                  </button>)}</span>
              ) },
            ]} />
          {sample && <Samples reason={sample} onClose={() => setSample(null)} />}
        </div>
      )}
    </AsyncBoundary>
  );
}

const ROWS: { key: string; label: string; fmt: (v: number | null | undefined) => string }[] = [
  { key: "events", label: "Events counted", fmt: fmtInt },
  { key: "agent_runs", label: "Agent executions", fmt: fmtInt },
  { key: "agent_success_rate", label: "Agent success rate", fmt: (v) => fmtPct(v, 2) },
  { key: "p95_latency_ms", label: "P95 latency", fmt: fmtMs },
  { key: "est_cost_usd", label: "Estimated cost", fmt: (v) => fmtUsd(v, 2) },
];

function FailureDemo() {
  const state = useApi<{ report: FailureDemoReport }>("/api/v1/quality/failure-demo");
  return (
    <AsyncBoundary state={state} label="failure demonstration" variant="table">
      {({ report: r }) => {
        const s1 = r.step_1_dirty_batch, s2 = r.step_2_toxic_batch, s3 = r.step_3_recovery_rerun;
        const get = (m: Metrics, k: string) => m[k] as number | null | undefined;
        type Row = (typeof ROWS)[number];
        return (
          <div className="space-y-4">
            <ol className="grid grid-cols-1 gap-3 lg:grid-cols-3">
              <li className="rounded-xl border border-line bg-surface p-4">
                <h3 className="font-semibold text-ink">1. Batch with ~3% bad records</h3>
                <p className="mt-1 text-sm text-ink"><StatusBadge status={s1.status} /> published with warnings</p>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted">
                  <li><strong className="text-ink">{fmtInt(s1.ingest.quarantined)}</strong> of {fmtInt(s1.ingest.input)} records quarantined (invalid IDs, timestamps, names, schema…)</li>
                  <li><strong className="text-ink">{fmtInt(s1.duplicates_removed)}</strong> duplicate event IDs deduplicated</li>
                  <li>{s1.failed_checks.length} checks flagged; none blocking</li>
                </ul>
              </li>
              <li className="rounded-xl border border-line bg-surface p-4">
                <h3 className="font-semibold text-ink">2. Batch with ~40% bad records</h3>
                <p className="mt-1 text-sm text-ink"><StatusBadge status={s2.status} /> blocked by <code className="text-xs">{s2.blocking_checks.join(", ")}</code></p>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted">
                  <li>{s2.ingest ? <><strong className="text-ink">{fmtInt(s2.ingest.quarantined)}</strong> records would have been quarantined</> : "Run stopped"}</li>
                  <li>Run rolled back; previously published data {s2.gold_unchanged_after_block ? <strong className="text-ink">unchanged</strong> : "changed"}</li>
                </ul>
              </li>
              <li className="rounded-xl border border-line bg-surface p-4">
                <h3 className="font-semibold text-ink">3. Corrected batch re-delivered</h3>
                <p className="mt-1 text-sm text-ink"><StatusBadge status={s3.status} /> rerun succeeded</p>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted">
                  <li>All metrics {s3.converged_to_reference ? <strong className="text-ink">match the clean reference exactly</strong> : "do not yet match the reference"}</li>
                </ul>
              </li>
            </ol>
            <div>
              <h3 className="mb-2 text-base font-semibold text-ink">How the downstream numbers change</h3>
              <DataTable<Row> caption="Downstream metrics before and after cleaning" rows={ROWS} rowKey={(x) => x.key}
                columns={[
                  { key: "label", header: "Metric" },
                  { key: "ref", header: "Clean reference", align: "right", render: (x) => x.fmt(get(r.reference_metrics_clean, x.key)) },
                  { key: "naive", header: "Naive: no validation or dedup", align: "right", render: (x) => x.fmt(get(s1.naive_metrics_no_cleaning, x.key)) },
                  { key: "clean", header: "Published after cleaning (step 1)", align: "right", render: (x) => x.fmt(get(s1.published_metrics_after_cleaning, x.key)) },
                  { key: "rerun", header: "After corrected rerun (step 3)", align: "right", render: (x) => x.fmt(get(s3.published_metrics, x.key)) },
                ]} />
              <p className="mt-2 text-xs text-muted">{s1.note} Naive counts overstate duplicated events and include records with no user. Synthetic data, {fmtInt(r.scenario.users)} users, seed {r.scenario.seed}.</p>
            </div>
          </div>
        );
      }}
    </AsyncBoundary>
  );
}

export function QualityView() {
  return (
    <div>
      <SectionTitle id="checks" sub="Latest real pipeline run. Failed checks show how many records were affected and what happened to them.">Check results</SectionTitle>
      <Checks />
      <SectionTitle id="demo" sub="A controlled demonstration: bad batches are quarantined or blocked, and the rerun restores the numbers.">Failure demonstration</SectionTitle>
      <FailureDemo />
    </div>
  );
}
