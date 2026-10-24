"use client";

import {
  Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

export interface Series {
  key: string;
  label: string;
  /** 1-8: picks --chart-N */
  color?: number;
  type?: "line" | "bar";
  axis?: "left" | "right";
  format?: (v: number | null) => string;
}

type Row = Record<string, string | number | null>;

const tick = { fill: "var(--muted)", fontSize: 12 };
const shortDate = (s: string) => {
  const d = new Date(`${s}T00:00:00Z`);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
};

interface TipProps {
  active?: boolean;
  label?: string | number;
  payload?: { dataKey?: string | number; value?: number | null; color?: string }[];
  series: Series[];
}

function Tip({ active, label, payload, series }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line bg-surface p-2 text-xs text-ink shadow-md">
      <p className="mb-1 font-semibold">{typeof label === "string" && /^\d{4}-\d{2}-\d{2}$/.test(label) ? shortDate(label) : label}</p>
      {payload.map((p) => {
        const s = series.find((x) => x.key === p.dataKey);
        return (
          <p key={String(p.dataKey)} className="flex items-center gap-2">
            <span aria-hidden="true" className="inline-block h-2 w-2 rounded-full" style={{ background: p.color }} />
            <span className="text-muted">{s?.label ?? String(p.dataKey)}:</span>
            <span className="font-semibold tabular-nums">{s?.format ? s.format(p.value ?? null) : (p.value ?? "–")}</span>
          </p>
        );
      })}
    </div>
  );
}

/** Line / bar / combined chart on CSS-variable colours (works in light and dark). Pair it with ChartCard's table view. */
export function SeriesChart({ data, xKey = "date", series, leftFormat, rightFormat, xFormat, ariaLabel, height = 256 }: {
  data: Row[]; xKey?: string; series: Series[]; leftFormat?: (v: number) => string; rightFormat?: (v: number) => string;
  xFormat?: (v: string) => string; ariaLabel: string; height?: number;
}) {
  const hasRight = series.some((s) => s.axis === "right");
  return (
    <div role="img" aria-label={ariaLabel} style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        <ComposedChart data={data} margin={{ top: 8, right: hasRight ? 8 : 16, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--border)" strokeOpacity={0.6} vertical={false} />
          <XAxis dataKey={xKey} tick={tick} tickFormatter={xFormat ?? shortDate} minTickGap={28} stroke="var(--border)" />
          <YAxis yAxisId="left" tick={tick} tickFormatter={leftFormat} width={52} stroke="var(--border)" />
          {hasRight && <YAxis yAxisId="right" orientation="right" tick={tick} tickFormatter={rightFormat} width={48} stroke="var(--border)" />}
          <Tooltip content={<Tip series={series} />} />
          <Legend wrapperStyle={{ fontSize: 12, color: "var(--muted)" }} />
          {series.map((s, i) => {
            const color = `var(--chart-${s.color ?? (i % 8) + 1})`;
            const yAxisId = s.axis ?? "left";
            return s.type === "bar" ? (
              <Bar key={s.key} dataKey={s.key} name={s.label} yAxisId={yAxisId} fill={color} fillOpacity={0.85} isAnimationActive={false} />
            ) : (
              <Line key={s.key} dataKey={s.key} name={s.label} yAxisId={yAxisId} stroke={color} strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
            );
          })}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
