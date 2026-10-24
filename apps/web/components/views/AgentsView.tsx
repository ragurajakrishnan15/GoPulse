"use client";

import { AsyncBoundary } from "@/components/AsyncBoundary";
import { ChartCard } from "@/components/ChartCard";
import { DataTable } from "@/components/DataTable";
import { FilterBar } from "@/components/FilterBar";
import { MetricChart } from "@/components/MetricChart";
import { SectionTitle } from "@/components/PageHeader";
import { SeriesChart } from "@/components/SeriesChart";
import { useApi } from "@/hooks/useApi";
import { useFilters } from "@/hooks/useFilters";
import { fmtDate, fmtInt, fmtMs, fmtPct, fmtUsd, titleCase } from "@/lib/format";
import type { AgentRow, AgentsResponse } from "@/lib/types";

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <p className="text-sm font-medium text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">{value}</p>
      {sub && <p className="text-xs text-muted">{sub}</p>}
    </div>
  );
}

export function AgentsView() {
  const { filters } = useFilters();
  const state = useApi<AgentsResponse>("/api/v1/agents/summary", filters);
  return (
    <div className="space-y-6">
      <FilterBar />
      <p role="note" className="rounded-lg border border-dashed border-warn bg-warnbg p-3 text-sm text-ink">
        <strong>Cost figures are estimates</strong> from documented synthetic token pricing (input $3 / output $15 per million tokens, multiplied by
        attempts) - not vendor billing.
      </p>
      <AsyncBoundary state={state} label="agent insights" variant="cards" skeleton={{ count: 6, height: 106, grid: "grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6" }} isEmpty={(d) => d.totals.executions === 0} emptyMessage="No agent executions match these filters.">
        {(d) => (
          <div className="space-y-6">
            <section aria-labelledby="ag-kpi">
              <SectionTitle id="ag-kpi" sub="Success = completed ÷ (completed + failed). Percentiles use terminal executions with a valid duration.">Reliability, latency and cost</SectionTitle>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
                <Stat label="Executions" value={fmtInt(d.totals.executions)} />
                <Stat label="Success rate" value={fmtPct(d.totals.success_rate)} sub={`${fmtInt(d.totals.successes)} successful`} />
                <Stat label="Retry rate" value={fmtPct(d.totals.retry_rate)} />
                <Stat label="P50 latency" value={fmtMs(d.totals.p50_latency_ms)} />
                <Stat label="P95 latency" value={fmtMs(d.totals.p95_latency_ms)} />
                <Stat label="Est. cost / interaction" value={fmtUsd(d.totals.est_cost_per_interaction_usd)} sub={`≈ ${fmtUsd(d.totals.est_cost_usd, 2)} total (estimate)`} />
              </div>
            </section>

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <ChartCard title="Interaction volume and success rate" meta={{ from: d.filters.start, to: d.filters.end, definition: d.definition?.definition }}
                rows={d.volume_over_time.map((v) => ({ ...v }))}
                columns={[{ key: "date", header: "Date", render: (r) => fmtDate(String(r.date)) },
                  { key: "executions", header: "Executions", align: "right", render: (r) => fmtInt(r.executions as number) },
                  { key: "success_rate", header: "Success rate", align: "right", render: (r) => fmtPct(r.success_rate as number | null) }]}>
                <SeriesChart data={d.volume_over_time.map((v) => ({ ...v }))}
                  series={[{ key: "executions", label: "Executions", type: "bar", color: 2, format: fmtInt }, { key: "success_rate", label: "Success rate", color: 4, axis: "right", format: (v) => fmtPct(v) }]}
                  leftFormat={fmtInt} rightFormat={(v) => fmtPct(v, 0)} ariaLabel="Daily agent executions and success rate" />
              </ChartCard>
              <MetricChart title="Latency over time (P50 / P95)" metrics={["p50_latency_ms", "p95_latency_ms"]} filters={filters}
                series={[{ key: "p50_latency_ms", label: "P50", color: 1, format: fmtMs }, { key: "p95_latency_ms", label: "P95", color: 3, format: fmtMs }]}
                leftFormat={(v) => `${Math.round(v / 100) / 10}s`} />
            </div>

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <section aria-labelledby="fail-h" className="rounded-xl border border-line bg-surface p-4">
                <h3 id="fail-h" className="text-base font-semibold text-ink">Failure categories</h3>
                <p className="mb-3 text-xs text-muted">Share of failed executions (denominator: all failures in range).</p>
                {d.failure_categories.length === 0 ? <p className="text-sm text-muted">No failures in range.</p> : (
                  <ul className="space-y-3">
                    {d.failure_categories.map((f) => (
                      <li key={f.failure_category ?? "unknown"}>
                        <div className="flex justify-between text-sm"><span className="text-ink">{titleCase(f.failure_category ?? "unknown")}</span>
                          <span className="tabular-nums text-ink">{fmtPct(f.share_of_failures)} <span className="text-muted">({fmtInt(f.failures)})</span></span></div>
                        <div className="mt-1 h-2.5 rounded-full bg-surface2" role="meter" aria-label={`${titleCase(f.failure_category ?? "unknown")} share of failures`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(f.share_of_failures * 100)}>
                          <div className="h-full rounded-full bg-accent" style={{ width: `${Math.round(f.share_of_failures * 100)}%` }} />
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <section aria-labelledby="by-agent-h" className="rounded-xl border border-line bg-surface p-4">
                <h3 id="by-agent-h" className="mb-3 text-base font-semibold text-ink">By agent type</h3>
                <DataTable<AgentRow> caption="Agent performance by agent type" dense rows={d.by_agent} rowKey={(a) => a.agent_type}
                  columns={[
                    { key: "agent_type", header: "Agent", render: (a) => <span className="font-medium">{titleCase(a.agent_type)}</span> },
                    { key: "executions", header: "Runs", align: "right", render: (a) => fmtInt(a.executions) },
                    { key: "success_rate", header: "Success", align: "right", render: (a) => fmtPct(a.success_rate) },
                    { key: "p95", header: "P95", align: "right", render: (a) => fmtMs(a.p95_latency_ms) },
                    { key: "tokens", header: "Tokens", align: "right", render: (a) => fmtInt(a.total_tokens) },
                    { key: "cost", header: "Est. cost / run", align: "right", render: (a) => fmtUsd(a.est_cost_per_interaction_usd) },
                  ]} />
              </section>
            </div>
          </div>
        )}
      </AsyncBoundary>
    </div>
  );
}
