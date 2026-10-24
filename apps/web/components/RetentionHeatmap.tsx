import { fmtDate, fmtInt, fmtPct } from "@/lib/format";
import type { RetentionResponse } from "@/lib/types";

/** Cohort x day-N table. Colour is a redundant cue: every cell prints its rate and exposes retained/eligible counts. */
export function RetentionHeatmap({ data }: { data: RetentionResponse }) {
  const days = data.day_offsets;
  return (
    <div role="region" aria-label="Retention cohorts table" tabIndex={0} className="relative overflow-auto rounded-lg border border-line">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">
          Retention by signup {data.granularity} cohort. Each cell is the share of eligible cohort users active exactly N days after signup.
        </caption>
        <thead className="bg-surface2 text-xs text-muted">
          <tr>
            <th scope="col" className="px-3 py-2 text-left font-semibold">Cohort ({data.granularity} of)</th>
            {days.map((d) => (<th key={d} scope="col" className="px-3 py-2 text-right font-semibold">Day {d}</th>))}
          </tr>
        </thead>
        <tbody>
          {data.cohorts.map((c) => (
            <tr key={c.cohort_start} className="border-t border-line">
              <th scope="row" className="whitespace-nowrap px-3 py-1.5 text-left font-medium text-ink">{fmtDate(c.cohort_start)}</th>
              {days.map((d) => {
                const cell = c.retention[String(d)];
                const rate = cell?.rate;
                const ready = rate != null && cell.eligible_users > 0;
                const strength = ready ? Math.min(45, Math.round(rate * 90)) : 0;
                return (
                  <td key={d} className="px-3 py-1.5 text-right tabular-nums text-ink"
                    style={ready ? { background: `color-mix(in oklab, var(--accent) ${strength}%, transparent)` } : undefined}
                    title={ready ? `${fmtInt(cell.retained_users)} of ${fmtInt(cell.eligible_users)} eligible users` : "Observation window not complete yet"}>
                    {ready ? fmtPct(rate, 0) : <span className="text-muted" aria-label="not yet observable">–</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="border-t border-line bg-surface px-3 py-2 text-xs text-muted">
        “–” means the day-N window has not fully elapsed for that cohort; those users are excluded from both numerator and denominator.
      </p>
    </div>
  );
}
