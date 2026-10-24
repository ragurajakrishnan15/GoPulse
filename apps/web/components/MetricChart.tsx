"use client";

import { useMemo } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { ChartCard } from "@/components/ChartCard";
import { SeriesChart, type Series } from "@/components/SeriesChart";
import type { Column } from "@/components/DataTable";
import { useApiAll } from "@/hooks/useApi";
import type { FilterValues } from "@/lib/filters";
import { fmtDate } from "@/lib/format";
import type { TimeseriesResponse } from "@/lib/types";

type Row = Record<string, string | number | null>;

/** Fetches one or more /metrics/timeseries metrics for the current filters and plots them together. */
export function MetricChart({ title, metrics, series, filters, leftFormat, rightFormat, note, ariaLabel }: {
  title: string; metrics: string[]; series: Series[]; filters: FilterValues; leftFormat?: (v: number) => string;
  rightFormat?: (v: number) => string; note?: string; ariaLabel?: string;
}) {
  const state = useApiAll<TimeseriesResponse>(
    metrics.map((m) => ({ path: "/api/v1/metrics/timeseries", params: { metric: m, ...filters } })),
  );
  const rows = useMemo<Row[]>(() => {
    const byDate = new Map<string, Row>();
    state.data?.forEach((r) => {
      r.points.forEach((p) => {
        const row = byDate.get(p.date) ?? { date: p.date };
        row[r.metric] = p.value;
        byDate.set(p.date, row);
      });
    });
    return [...byDate.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  }, [state.data]);

  const columns: Column<Row>[] = [
    { key: "date", header: "Date", render: (r) => fmtDate(String(r.date)) },
    ...series.map((s) => ({
      key: s.key, header: s.label, align: "right" as const,
      render: (r: Row) => (s.format ? s.format(r[s.key] as number | null) : String(r[s.key] ?? "–")),
    })),
  ];

  return (
    <AsyncBoundary state={state} label={title} variant="chart" isEmpty={(d) => d.every((r) => r.points.length === 0)}>
      {(data) => (
        <ChartCard title={title} columns={columns} rows={rows}
          meta={{ from: data[0].filters.start, to: data[0].filters.end, definition: data[0].definition?.definition ?? null, note }}>
          <SeriesChart data={rows} series={series} leftFormat={leftFormat} rightFormat={rightFormat}
            ariaLabel={ariaLabel ?? `${title}, ${fmtDate(data[0].filters.start)} to ${fmtDate(data[0].filters.end)}`} />
        </ChartCard>
      )}
    </AsyncBoundary>
  );
}
