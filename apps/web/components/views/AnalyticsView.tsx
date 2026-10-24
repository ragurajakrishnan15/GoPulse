"use client";

import { useMemo } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { ChartCard } from "@/components/ChartCard";
import type { Column } from "@/components/DataTable";
import { FilterBar } from "@/components/FilterBar";
import { MetricChart } from "@/components/MetricChart";
import { SectionTitle } from "@/components/PageHeader";
import { RetentionHeatmap } from "@/components/RetentionHeatmap";
import { SeriesChart, type Series } from "@/components/SeriesChart";
import { useApi } from "@/hooks/useApi";
import { useFilters } from "@/hooks/useFilters";
import { fmtDate, fmtInt, fmtPct, titleCase } from "@/lib/format";
import type { AccountAdoptionRow, FeatureAdoptionRow, FunnelResponse, RetentionResponse } from "@/lib/types";

type Row = Record<string, string | number | null>;

function Funnel({ filters }: { filters: Record<string, string | undefined> }) {
  const state = useApi<FunnelResponse>("/api/v1/funnels/activation", filters);
  return (
    <AsyncBoundary state={state} label="activation funnel" variant="chart" isEmpty={(d) => d.stages[0]?.users === 0} emptyMessage="No signups match these filters.">
      {(d) => (
        <section aria-labelledby="funnel-h" className="rounded-xl border border-line bg-surface p-4">
          <h3 id="funnel-h" className="text-base font-semibold text-ink">Signup → activation → conversion</h3>
          <p className="text-xs text-muted">Signup cohort {fmtDate(d.filters.start)} – {fmtDate(d.filters.end)} (UTC)</p>
          <ol className="mt-4 space-y-3">
            {d.stages.map((s) => (
              <li key={s.stage} data-testid={`stage-${s.stage}`}>
                <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
                  <span className="font-medium text-ink">{s.label}</span>
                  <span className="tabular-nums text-ink"><strong>{fmtPct(s.rate)}</strong> <span className="text-muted">({fmtInt(s.users)} of {fmtInt(s.denominator)})</span></span>
                </div>
                <div className="mt-1 h-3 overflow-hidden rounded-full bg-surface2" role="meter" aria-label={`${s.label} rate`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((s.rate ?? 0) * 100)}>
                  <div className="h-full rounded-full bg-accent" style={{ width: `${Math.round((s.rate ?? 0) * 100)}%` }} />
                </div>
                <p className="mt-1 text-xs text-muted">Denominator: {s.denominator_definition}.</p>
              </li>
            ))}
          </ol>
          <p className="mt-4 text-xs text-muted"><span className="font-semibold text-ink">Read with care: </span>{d.caveat}</p>
        </section>
      )}
    </AsyncBoundary>
  );
}

function Retention({ filters }: { filters: Record<string, string | undefined> }) {
  const state = useApi<RetentionResponse>("/api/v1/retention/cohorts", { ...filters, granularity: "week" });
  return (
    <AsyncBoundary state={state} label="retention cohorts" variant="table" isEmpty={(d) => d.cohorts.length === 0} emptyMessage="No cohorts match these filters.">
      {(d) => (
        <section aria-labelledby="ret-h" className="rounded-xl border border-line bg-surface p-4">
          <h3 id="ret-h" className="text-base font-semibold text-ink">Retention by weekly signup cohort</h3>
          <p className="mb-3 text-xs text-muted">{fmtDate(d.filters.start)} – {fmtDate(d.filters.end)} (UTC). <span className="font-semibold text-ink">Definition: </span>{d.definition?.definition}</p>
          <RetentionHeatmap data={d} />
        </section>
      )}
    </AsyncBoundary>
  );
}

function FeatureAdoption({ start, end }: { start?: string; end?: string }) {
  const state = useApi<{ rows: FeatureAdoptionRow[]; note: string; definition: { definition: string } | null }>("/api/v1/features/adoption", { start, end });
  const { rows, names, series, columns } = useMemo(() => {
    const names = new Map<string, string>();
    const byWeek = new Map<string, Row>();
    (state.data?.rows ?? []).forEach((r) => {
      names.set(r.feature_id, r.feature_name);
      const row = byWeek.get(r.week) ?? { date: r.week };
      row[r.feature_id] = r.adoption_rate;
      byWeek.set(r.week, row);
    });
    const ids = [...names.keys()];
    const series: Series[] = ids.map((id, i) => ({ key: id, label: names.get(id) ?? id, color: (i % 8) + 1, format: (v: number | null) => fmtPct(v) }));
    const columns: Column<Row>[] = [{ key: "date", header: "Week of", render: (r) => fmtDate(String(r.date)) },
      ...series.map((s) => ({ key: s.key, header: s.label, align: "right" as const, render: (r: Row) => fmtPct(r[s.key] as number | null) }))];
    return { rows: [...byWeek.values()].sort((a, b) => String(a.date).localeCompare(String(b.date))), names, series, columns };
  }, [state.data]);

  return (
    <AsyncBoundary state={state} label="feature adoption" variant="chart" isEmpty={(d) => d.rows.length === 0}>
      {(d) => (
        <ChartCard title="Feature adoption by week" columns={columns} rows={rows}
          meta={{ from: rows[0]?.date as string, to: rows[rows.length - 1]?.date as string, definition: d.definition?.definition, note: d.note }}>
          <SeriesChart data={rows} series={series} leftFormat={(v) => fmtPct(v, 0)} ariaLabel={`Weekly adoption rate for ${names.size} features`} />
        </ChartCard>
      )}
    </AsyncBoundary>
  );
}

function AccountAdoption({ tier }: { tier?: string }) {
  const state = useApi<{ rows: AccountAdoptionRow[]; note: string; definition: { definition: string } | null }>("/api/v1/accounts/adoption", { tier });
  const { rows, series, columns } = useMemo(() => {
    const tiers = [...new Set((state.data?.rows ?? []).map((r) => r.account_tier))];
    const byMonth = new Map<string, Row>();
    (state.data?.rows ?? []).forEach((r) => {
      const row = byMonth.get(r.period_start) ?? { date: r.period_start };
      row[r.account_tier] = r.adoption_rate;
      byMonth.set(r.period_start, row);
    });
    const series: Series[] = tiers.map((t, i) => ({ key: t, label: titleCase(t), type: "bar", color: i + 1, format: (v: number | null) => fmtPct(v) }));
    const columns: Column<Row>[] = [{ key: "date", header: "Month", render: (r) => fmtDate(String(r.date)) },
      ...series.map((s) => ({ key: s.key, header: s.label, align: "right" as const, render: (r: Row) => fmtPct(r[s.key] as number | null) }))];
    return { rows: [...byMonth.values()].sort((a, b) => String(a.date).localeCompare(String(b.date))), series, columns };
  }, [state.data]);

  return (
    <AsyncBoundary state={state} label="account adoption" variant="chart" isEmpty={(d) => d.rows.length === 0}>
      {(d) => (
        <ChartCard title="Account-level adoption by month and tier" columns={columns} rows={rows}
          meta={{ from: rows[0]?.date as string, to: rows[rows.length - 1]?.date as string, definition: d.definition?.definition, note: d.note }}>
          <SeriesChart data={rows} series={series} leftFormat={(v) => fmtPct(v, 0)} xFormat={(s) => fmtDate(s).replace(/ \d+,/, "")} ariaLabel="Share of accounts with at least one active user, by month and account tier" />
        </ChartCard>
      )}
    </AsyncBoundary>
  );
}

export function AnalyticsView() {
  const { filters } = useFilters();
  const f = filters as Record<string, string | undefined>;
  return (
    <div className="space-y-6">
      <FilterBar hide={["feature"]} />
      <SectionTitle id="funnel" sub="Funnel and retention follow the signup cohort and respond to date, tier, segment and plan filters.">Activation, conversion and retention</SectionTitle>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Funnel filters={f} />
        <Retention filters={f} />
      </div>
      <SectionTitle id="trends" sub="Active-user trends respond to all filters.">Active users</SectionTitle>
      <MetricChart title="Daily and weekly active users" metrics={["dau", "wau"]} filters={f}
        series={[{ key: "dau", label: "DAU", color: 1, format: fmtInt }, { key: "wau", label: "WAU (trailing 7 days)", color: 2, format: fmtInt }]} leftFormat={fmtInt} />
      <SectionTitle id="adoption" sub="These two views use pre-aggregated marts: only the date range (features) or tier (accounts) applies.">Feature and account adoption</SectionTitle>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <FeatureAdoption start={f.start} end={f.end} />
        <AccountAdoption tier={f.tier} />
      </div>
    </div>
  );
}
