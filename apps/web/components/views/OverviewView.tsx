"use client";

import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FilterBar } from "@/components/FilterBar";
import { KpiCard } from "@/components/KpiCard";
import { MetricChart } from "@/components/MetricChart";
import { SectionTitle } from "@/components/PageHeader";
import { useApi } from "@/hooks/useApi";
import { useFilters } from "@/hooks/useFilters";
import { fmtInt, fmtMs, fmtPct, fmtUsd } from "@/lib/format";
import type { OverviewResponse } from "@/lib/types";

const KPI_ORDER = ["registered_users", "dau", "wau", "activation_rate", "conversion_rate", "retention_d7",
  "agent_success_rate", "p50_latency_ms", "p95_latency_ms", "est_cost_per_interaction_usd"];

export function OverviewView() {
  const { filters } = useFilters();
  const overview = useApi<OverviewResponse>("/api/v1/overview", filters);
  const active = Object.keys(filters).filter((k) => ["tier", "segment", "plan", "feature"].includes(k));

  return (
    <div className="space-y-6">
      <FilterBar />
      <section aria-labelledby="kpis">
        <SectionTitle id="kpis" sub="Each card shows its numerator and denominator; open “Definition” for the exact rule.">Headline metrics</SectionTitle>
        <AsyncBoundary state={overview} label="headline metrics" variant="cards" skeleton={{ count: 10, height: 142 }} isEmpty={(d) => d.kpis.length === 0}>
          {(d) => (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {KPI_ORDER.map((k) => d.kpis.find((x) => x.key === k)).filter((k) => !!k).map((k) => (
                <KpiCard key={k.key} kpi={k} active={active} />
              ))}
            </div>
          )}
        </AsyncBoundary>
      </section>

      <section aria-labelledby="trends">
        <SectionTitle id="trends">Trends</SectionTitle>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <MetricChart title="Daily and weekly active users" metrics={["dau", "wau"]} filters={filters}
            series={[{ key: "dau", label: "DAU", color: 1, format: (v) => fmtInt(v) }, { key: "wau", label: "WAU (trailing 7 days)", color: 2, format: (v) => fmtInt(v) }]}
            leftFormat={fmtInt} />
          <MetricChart title="New signups per day" metrics={["new_signups"]} filters={{ ...filters, feature: undefined }}
            series={[{ key: "new_signups", label: "New signups", type: "bar", color: 5, format: (v) => fmtInt(v) }]} leftFormat={fmtInt}
            note={filters.feature ? "The feature filter does not apply to signups." : undefined} />
          <MetricChart title="Agent executions and success rate" metrics={["agent_runs", "agent_success_rate"]} filters={filters}
            series={[{ key: "agent_runs", label: "Executions", type: "bar", color: 2, format: (v) => fmtInt(v) },
              { key: "agent_success_rate", label: "Success rate", color: 4, axis: "right", format: (v) => fmtPct(v) }]}
            leftFormat={fmtInt} rightFormat={(v) => fmtPct(v, 0)} />
          <MetricChart title="Agent latency (P50 / P95)" metrics={["p50_latency_ms", "p95_latency_ms"]} filters={filters}
            series={[{ key: "p50_latency_ms", label: "P50", color: 1, format: fmtMs }, { key: "p95_latency_ms", label: "P95", color: 3, format: fmtMs }]}
            leftFormat={(v) => `${Math.round(v / 100) / 10}s`} />
          <MetricChart title="Estimated cost per interaction" metrics={["est_cost_per_interaction_usd"]} filters={filters}
            series={[{ key: "est_cost_per_interaction_usd", label: "Est. cost / interaction", color: 6, format: (v) => fmtUsd(v) }]}
            leftFormat={(v) => `$${v.toFixed(3)}`} note="Estimate from documented synthetic token pricing - not vendor billing." />
        </div>
      </section>
    </div>
  );
}
