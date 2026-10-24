import type { ReactNode } from "react";

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-5">
      <h1 className="text-2xl font-bold tracking-tight text-ink">{title}</h1>
      {children && <div className="mt-1 max-w-3xl text-sm text-muted">{children}</div>}
    </div>
  );
}

export function SectionTitle({ id, children, sub }: { id: string; children: ReactNode; sub?: ReactNode }) {
  return (
    <div className="mb-3 mt-8">
      <h2 id={id} className="text-lg font-semibold text-ink">{children}</h2>
      {sub && <p className="text-sm text-muted">{sub}</p>}
    </div>
  );
}
