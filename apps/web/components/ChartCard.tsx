"use client";

import { useId, useState, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtDate } from "@/lib/format";

export interface ChartMeta {
  from?: string;
  to?: string;
  /** Short metric definition shown under the chart (README: every chart shows its date range and definition). */
  definition?: string | null;
  note?: string | null;
}

/** Card wrapper: title, date range, definition, and a chart <-> table switch (the table is the accessible alternative). */
export function ChartCard<T extends Record<string, unknown>>({ title, meta, columns, rows, children, className = "" }: {
  title: string; meta: ChartMeta; columns: Column<T>[]; rows: T[]; children: ReactNode; className?: string;
}) {
  const id = useId();
  const [table, setTable] = useState(false);
  return (
    <section aria-labelledby={`${id}-t`} className={`rounded-xl border border-line bg-surface p-4 ${className}`}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 id={`${id}-t`} className="text-base font-semibold text-ink">{title}</h3>
          {meta.from && meta.to && <p className="text-xs text-muted">{fmtDate(meta.from)} – {fmtDate(meta.to)} (UTC)</p>}
        </div>
        <button type="button" aria-pressed={table} onClick={() => setTable((t) => !t)}
          className="rounded-md border border-line px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface2">
          {table ? "View as chart" : "View as table"}
        </button>
      </div>
      {table ? <DataTable caption={`${title} - data table`} columns={columns} rows={rows} rowKey={(_, i) => String(i)} maxHeight="16rem" dense /> : children}
      {(meta.definition || meta.note) && (
        <p className="mt-3 text-xs text-muted">
          {meta.definition && <><span className="font-semibold text-ink">Definition: </span>{meta.definition} </>}
          {meta.note}
        </p>
      )}
    </section>
  );
}
