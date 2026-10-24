"use client";

import { Fragment, useId, useState } from "react";
import { AsyncBoundary, ErrorBox } from "@/components/AsyncBoundary";
import { SourceBadge, StatusBadge } from "@/components/Badges";
import { DataTable } from "@/components/DataTable";
import { SectionTitle } from "@/components/PageHeader";
import { useApi } from "@/hooks/useApi";
import { ApiError, apiPost } from "@/lib/api";
import { fmtAgo, fmtDateTime, fmtInt, fmtMs, titleCase } from "@/lib/format";
import type { DemoRunResponse, PipelineRun, RunDetailResponse, RunSource, RunsResponse } from "@/lib/types";

const selectCls = "rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink";

function RunDetail({ runId }: { runId: string }) {
  const state = useApi<RunDetailResponse>(`/api/v1/pipelines/runs/${runId}`);
  return (
    <AsyncBoundary state={state} label="run details" variant="table">
      {(d) => (
        <div className="space-y-3">
          <DataTable caption={`Tasks for run ${runId}`} dense rowKey={(t) => t.task} rows={d.tasks}
            columns={[
              { key: "task", header: "Task", render: (t) => <span className="font-medium">{t.task}</span> },
              { key: "status", header: "Status", render: (t) => <StatusBadge status={t.status} /> },
              { key: "duration_ms", header: "Duration", align: "right", render: (t) => fmtMs(t.duration_ms) },
              { key: "input_count", header: "Input", align: "right", render: (t) => fmtInt(t.input_count) },
              { key: "output_count", header: "Output", align: "right", render: (t) => fmtInt(t.output_count) },
              { key: "retry_count", header: "Retries", align: "right" },
              { key: "error_summary", header: "Error", render: (t) => t.error_summary ?? "–" },
            ]} />
          {d.run.error_summary && <p className="rounded-md bg-badbg p-2 text-sm text-ink"><strong>Error summary:</strong> {d.run.error_summary}</p>}
          <div>
            <p className="mb-1 text-xs font-semibold text-muted">Log excerpt{d.run.source === "seeded-example" ? " (seeded example text)" : ""}</p>
            <pre tabIndex={0} className="max-h-48 overflow-auto rounded-md border border-line bg-surface2 p-3 font-mono text-xs text-ink">{d.run.log_excerpt}</pre>
          </div>
        </div>
      )}
    </AsyncBoundary>
  );
}

function DemoRunPanel({ onDone }: { onDone: () => void }) {
  const uid = useId();
  const [scenario, setScenario] = useState("clean");
  const [seed, setSeed] = useState("1");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DemoRunResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await apiPost<DemoRunResponse>("/api/v1/demo/run", { scenario, seed: Number(seed) });
      setResult(res);
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "Network error"));
    } finally {
      setBusy(false);
    }
  }
  const seedOk = /^\d{1,4}$/.test(seed) && Number(seed) >= 1;

  return (
    <section aria-labelledby={`${uid}-h`} className="rounded-xl border border-line bg-surface p-4">
      <h3 id={`${uid}-h`} className="text-base font-semibold text-ink">Run demo pipeline</h3>
      <p className="mt-1 max-w-3xl text-sm text-muted">
        Executes the <strong>real</strong> ingest → Silver → Gold → quality-gate workflow on a small synthetic batch (100 users) in an isolated
        sandbox, then records it below as a “Real · demo run”. It cannot run commands, SQL or notebooks, and it never changes the published
        dashboard numbers. Rate limited.
      </p>
      <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); if (seedOk && !busy) void run(); }}>
        <div>
          <label htmlFor={`${uid}-sc`} className="mb-1 block text-xs font-medium text-muted">Scenario</label>
          <select id={`${uid}-sc`} className={selectCls} value={scenario} onChange={(e) => setScenario(e.target.value)}>
            <option value="clean">Clean batch</option>
            <option value="light_defects">Light defects (~3%) - published with warnings</option>
            <option value="heavy_defects">Heavy defects (~40%) - blocked by quality gate</option>
          </select>
        </div>
        <div>
          <label htmlFor={`${uid}-seed`} className="mb-1 block text-xs font-medium text-muted">Seed (1–9999)</label>
          <input id={`${uid}-seed`} inputMode="numeric" className={`${selectCls} w-24`} value={seed} aria-invalid={!seedOk}
            onChange={(e) => setSeed(e.target.value)} />
        </div>
        <button type="submit" disabled={busy || !seedOk}
          className="rounded-md bg-accent px-4 py-1.5 text-sm font-semibold text-accentfg hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50">
          {busy ? "Running…" : "Run demo pipeline"}
        </button>
      </form>
      <div aria-live="polite" className="mt-3">
        {busy && <p className="text-sm text-muted" role="status">Running the pipeline - usually a few seconds…</p>}
        {error && (
          <ErrorBox message={error.status === 429 ? `${error.message}${error.retryAfter ? ` (try again in ~${error.retryAfter}s)` : ""}` : error.friendly} />
        )}
        {result && (
          <div className="rounded-lg border border-line bg-surface2 p-3 text-sm text-ink" data-testid="demo-result">
            <p className="flex flex-wrap items-center gap-2">
              <SourceBadge source={result.source} /> <StatusBadge status={result.status} />
              <code className="text-xs">{result.run_id}</code>
            </p>
            <p className="mt-2 tabular-nums">
              Input {fmtInt(result.counts.input_count)} → accepted {fmtInt(result.counts.accepted_count)}, quarantined {fmtInt(result.counts.quarantined_count)},
              duplicates removed {fmtInt(result.counts.duplicate_count)}. Quality checks: {result.quality.failed} of {result.quality.checks} flagged
              {result.quality.blocking.length > 0 && <> - <strong>blocked by {result.quality.blocking.join(", ")}</strong>; nothing was published</>}.
            </p>
            {result.error_summary && <p className="mt-1 text-bad">{result.error_summary}</p>}
            <p className="mt-2 text-xs text-muted">{result.notice}</p>
          </div>
        )}
      </div>
    </section>
  );
}

export function PipelinesView() {
  const [source, setSource] = useState<RunSource | "">("");
  const [status, setStatus] = useState<"success" | "failed" | "">("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | null>(null);
  const limit = 10;
  const runs = useApi<RunsResponse>("/api/v1/pipelines/runs", { source, status, page, limit });
  const uid = useId();

  const cols = ["Run", "Type", "Status", "Started", "Duration", "Records (in → accepted / quarantined)", "Retries", "Last success", "Freshness lag", ""];
  return (
    <div className="space-y-6">
      <AsyncBoundary state={runs} label="pipeline summary" variant="cards" skeleton={{ count: 4, height: 106, grid: "grid-cols-2 gap-3 lg:grid-cols-4" }}>
        {(d) => (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Last real successful run" value={fmtAgo(d.summary.last_real_success_at)} sub={fmtDateTime(d.summary.last_real_success_at)} />
            <Stat label="Real pipeline runs" value={fmtInt(d.summary.local_real_runs)} sub="executed by this repo's pipeline" />
            <Stat label="Real demo runs" value={fmtInt(d.summary.demo_live_runs)} sub="triggered from this page" />
            <Stat label="Seeded example runs" value={fmtInt(d.summary.seeded_example_runs)} sub="illustrative history, not real" warn />
          </div>
        )}
      </AsyncBoundary>

      <div role="note" className="rounded-lg border border-dashed border-warn bg-warnbg p-3 text-sm text-ink">
        <strong>Real vs seeded:</strong> rows labelled <em>Seeded example</em> are deterministic illustrations (including failures and retries) so a
        month of history can be browsed. Only <em>Real</em> rows were executed by this project&apos;s code.
      </div>

      <DemoRunPanel onDone={() => { setPage(1); runs.reload(); }} />

      <section aria-labelledby={`${uid}-h`}>
        <SectionTitle id={`${uid}-h`}>Run history</SectionTitle>
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor={`${uid}-src`} className="mb-1 block text-xs font-medium text-muted">Type</label>
            <select id={`${uid}-src`} className={selectCls} value={source} onChange={(e) => { setSource(e.target.value as RunSource | ""); setPage(1); }}>
              <option value="">All</option>
              <option value="local-real">Real · pipeline run</option>
              <option value="demo-live">Real · demo run</option>
              <option value="seeded-example">Seeded example</option>
            </select>
          </div>
          <div>
            <label htmlFor={`${uid}-st`} className="mb-1 block text-xs font-medium text-muted">Status</label>
            <select id={`${uid}-st`} className={selectCls} value={status} onChange={(e) => { setStatus(e.target.value as "success" | "failed" | ""); setPage(1); }}>
              <option value="">All</option>
              <option value="success">Success</option>
              <option value="failed">Failed</option>
            </select>
          </div>
        </div>
        <AsyncBoundary state={runs} label="run history" variant="table" isEmpty={(d) => d.items.length === 0} emptyMessage="No runs match these filters.">
          {(d) => (
            <>
              <div role="region" aria-label="Pipeline run history" tabIndex={0} className="relative overflow-auto rounded-lg border border-line">
                <table className="w-full border-collapse text-left text-sm">
                  <caption className="sr-only">Pipeline run history, newest first</caption>
                  <thead className="bg-surface2 text-xs text-muted">
                    <tr>{cols.map((c, i) => <th key={i} scope="col" className="whitespace-nowrap px-3 py-2 font-semibold">{c || <span className="sr-only">Details</span>}</th>)}</tr>
                  </thead>
                  <tbody>
                    {d.items.map((r) => <RunRow key={r.run_id} r={r} open={open === r.run_id} onToggle={() => setOpen(open === r.run_id ? null : r.run_id)} ncols={cols.length} />)}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-xs text-muted">
                Freshness lag = run end − latest ingested event. The synthetic event stream ends in March 2026, so real runs made today show a large lag by
                design; seeded example runs are dated within the data&apos;s own time span.
              </p>
              <nav aria-label="Run history pages" className="mt-3 flex items-center justify-between text-sm">
                <span className="text-muted" aria-live="polite">Page {d.page} · {fmtInt(d.total)} runs</span>
                <span className="flex gap-2">
                  <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="rounded-md border border-line px-3 py-1 hover:bg-surface2 disabled:opacity-50">Previous</button>
                  <button type="button" disabled={!d.has_next} onClick={() => setPage((p) => p + 1)} className="rounded-md border border-line px-3 py-1 hover:bg-surface2 disabled:opacity-50">Next</button>
                </span>
              </nav>
            </>
          )}
        </AsyncBoundary>
      </section>
    </div>
  );
}

function Stat({ label, value, sub, warn }: { label: string; value: string; sub: string; warn?: boolean }) {
  return (
    <div className={`rounded-xl border bg-surface p-4 ${warn ? "border-dashed border-warn" : "border-line"}`}>
      <p className="text-sm font-medium text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-ink tabular-nums">{value}</p>
      <p className="text-xs text-muted">{sub}</p>
    </div>
  );
}

function RunRow({ r, open, onToggle, ncols }: { r: PipelineRun; open: boolean; onToggle: () => void; ncols: number }) {
  return (
    <Fragment>
      <tr className="border-t border-line align-top">
        <td className="px-3 py-2"><code className="text-xs">{r.run_id}</code><div className="text-xs text-muted">{titleCase(r.pipeline)}</div></td>
        <td className="px-3 py-2"><SourceBadge source={r.source} /></td>
        <td className="px-3 py-2"><StatusBadge status={r.status} />{r.error_summary && <div className="mt-1 max-w-xs text-xs text-bad">{r.error_summary}</div>}</td>
        <td className="whitespace-nowrap px-3 py-2 text-xs">{fmtDateTime(r.started_at)}</td>
        <td className="whitespace-nowrap px-3 py-2 tabular-nums">{fmtMs(r.duration_ms)}</td>
        <td className="whitespace-nowrap px-3 py-2 tabular-nums">{fmtInt(r.input_count)} → {fmtInt(r.accepted_count)} / {fmtInt(r.quarantined_count)}</td>
        <td className="px-3 py-2 tabular-nums">{r.retry_count}</td>
        <td className="whitespace-nowrap px-3 py-2 text-xs">{fmtAgo(r.last_successful_run_at)}</td>
        <td className="whitespace-nowrap px-3 py-2 tabular-nums">{r.freshness_lag_hours == null ? "–" : `${r.freshness_lag_hours} h`}</td>
        <td className="px-3 py-2">
          <button type="button" aria-expanded={open} aria-controls={`detail-${r.run_id}`} onClick={onToggle}
            className="rounded-md border border-line px-2 py-1 text-xs font-medium hover:bg-surface2">
            {open ? "Hide" : "Details"}<span className="sr-only"> for {r.run_id}</span>
          </button>
        </td>
      </tr>
      {open && (
        <tr id={`detail-${r.run_id}`} className="border-t border-line bg-surface2/40">
          <td colSpan={ncols} className="p-3"><RunDetail runId={r.run_id} /></td>
        </tr>
      )}
    </Fragment>
  );
}
