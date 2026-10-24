import { fmtInt, fmtNum, fmtUnit, fmtUsd } from "@/lib/format";
import type { Kpi } from "@/lib/types";

const NOT_APPLIED_LABEL: Record<string, string> = { tier: "tier", segment: "segment", plan: "plan", feature: "feature" };

export function KpiCard({ kpi, active }: { kpi: Kpi; active: string[] }) {
  const id = `kpi-${kpi.key}`;
  const value = fmtUnit(kpi.value, kpi.unit === "users" || kpi.unit === null ? "count" : kpi.unit);
  const notApplied = active.filter((f) => NOT_APPLIED_LABEL[f] && !kpi.applied_filters.includes(f));
  const secondary =
    kpi.denominator != null
      ? kpi.unit === "usd"
        ? `${fmtUsd(kpi.numerator, 2)} over ${fmtInt(kpi.denominator)} interactions`
        : `${fmtInt(kpi.numerator)} of ${fmtInt(kpi.denominator)} eligible`
      : kpi.average_over_range != null
        ? `avg ${fmtNum(kpi.average_over_range)} over range`
        : kpi.as_of
          ? `as of ${kpi.as_of}`
          : null;

  return (
    <article aria-labelledby={id} className="flex flex-col rounded-xl border border-line bg-surface p-4" data-testid={`kpi-${kpi.key}`}>
      <h3 id={id} className="text-sm font-medium text-muted">
        {kpi.label}
        {kpi.estimate && <span className="ml-2 rounded bg-warnbg px-1.5 py-0.5 text-xs font-semibold text-warn">Estimate</span>}
      </h3>
      <p className="mt-1 text-3xl font-semibold tracking-tight text-ink tabular-nums">{value}</p>
      {secondary && <p className="mt-1 text-xs text-muted tabular-nums">{secondary}</p>}
      {notApplied.length > 0 && <p className="mt-1 text-xs text-warn">Not filtered by: {notApplied.join(", ")}</p>}
      <details className="mt-auto pt-3 text-xs text-muted">
        <summary className="cursor-pointer font-medium text-accent">Definition</summary>
        <dl className="mt-2 space-y-1">
          <div><dt className="inline font-semibold text-ink">What: </dt><dd className="inline">{kpi.definition ?? "n/a"}</dd></div>
          {kpi.window && <div><dt className="inline font-semibold text-ink">Window: </dt><dd className="inline">{kpi.window}</dd></div>}
          {kpi.eligibility && <div><dt className="inline font-semibold text-ink">Eligibility: </dt><dd className="inline">{kpi.eligibility}</dd></div>}
          {kpi.note && <div><dt className="inline font-semibold text-ink">Note: </dt><dd className="inline">{kpi.note}</dd></div>}
        </dl>
      </details>
    </article>
  );
}
