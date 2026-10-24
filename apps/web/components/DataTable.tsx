import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => ReactNode;
  align?: "left" | "right";
}

/** Accessible table: caption, scoped headers and a focusable scroll region so keyboard users can pan on small screens. */
export function DataTable<T>({ caption, columns, rows, rowKey, maxHeight, dense }: {
  caption: string; columns: Column<T>[]; rows: T[]; rowKey: (row: T, i: number) => string; maxHeight?: string; dense?: boolean;
}) {
  return (
    <div role="region" aria-label={caption} tabIndex={0} className="relative overflow-auto rounded-lg border border-line" style={maxHeight ? { maxHeight } : undefined}>
      <table className="w-full border-collapse text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 bg-surface2 text-xs text-muted">
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={`whitespace-nowrap px-3 ${dense ? "py-1.5" : "py-2"} font-semibold ${c.align === "right" ? "text-right" : ""}`}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={rowKey(r, i)} className="border-t border-line">
              {columns.map((c) => (
                <td key={c.key} className={`px-3 ${dense ? "py-1.5" : "py-2"} align-top tabular-nums ${c.align === "right" ? "text-right" : ""}`}>
                  {c.render ? c.render(r) : String((r as Record<string, unknown>)[c.key] ?? "–")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
