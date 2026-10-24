"use client";

import { useId } from "react";
import { useApi } from "@/hooks/useApi";
import { useFilters } from "@/hooks/useFilters";
import { PLANS, SEGMENTS, TIERS, describeFilters, type FilterKey } from "@/lib/filters";
import type { FeatureItem } from "@/lib/types";
import { titleCase } from "@/lib/format";

const field = "w-full rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink";

function Select({ id, label, value, options, onChange }: {
  id: string; label: string; value?: string; options: { value: string; label: string }[]; onChange: (v: string) => void;
}) {
  return (
    <div className="min-w-[8.5rem] flex-1">
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted">{label}</label>
      <select id={id} className={field} value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        <option value="">All</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}

/** URL-backed filters. `hide` removes controls that do not apply to a view (the API rejects inapplicable filters). */
export function FilterBar({ hide = [] }: { hide?: FilterKey[] }) {
  const uid = useId();
  const { filters, setFilters, reset } = useFilters();
  const features = useApi<{ items: FeatureItem[] }>(hide.includes("feature") ? null : "/api/v1/features");
  const active = Object.keys(filters).length > 0;
  const opt = (xs: readonly string[]) => xs.map((x) => ({ value: x, label: titleCase(x) }));

  return (
    <section aria-label="Filters" className="rounded-xl border border-line bg-surface p-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[9.5rem] flex-1">
          <label htmlFor={`${uid}-start`} className="mb-1 block text-xs font-medium text-muted">Start date (UTC)</label>
          <input id={`${uid}-start`} type="date" className={field} value={filters.start ?? ""} onChange={(e) => setFilters({ start: e.target.value || undefined })} />
        </div>
        <div className="min-w-[9.5rem] flex-1">
          <label htmlFor={`${uid}-end`} className="mb-1 block text-xs font-medium text-muted">End date (UTC)</label>
          <input id={`${uid}-end`} type="date" className={field} value={filters.end ?? ""} onChange={(e) => setFilters({ end: e.target.value || undefined })} />
        </div>
        {!hide.includes("tier") && <Select id={`${uid}-tier`} label="Account tier" value={filters.tier} options={opt(TIERS)} onChange={(v) => setFilters({ tier: v || undefined })} />}
        {!hide.includes("segment") && <Select id={`${uid}-seg`} label="Account segment" value={filters.segment} options={opt(SEGMENTS)} onChange={(v) => setFilters({ segment: v || undefined })} />}
        {!hide.includes("plan") && <Select id={`${uid}-plan`} label="Current plan" value={filters.plan} options={opt(PLANS)} onChange={(v) => setFilters({ plan: v || undefined })} />}
        {!hide.includes("feature") && (
          <Select id={`${uid}-feat`} label="Feature" value={filters.feature}
            options={(features.data?.items ?? []).map((f) => ({ value: f.feature_id, label: f.name }))}
            onChange={(v) => setFilters({ feature: v || undefined })} />
        )}
        <button type="button" onClick={reset} disabled={!active}
          className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface2 disabled:cursor-not-allowed disabled:opacity-50">
          Reset
        </button>
      </div>
      <p className="mt-2 text-xs text-muted" aria-live="polite">Showing: {describeFilters(filters)}. “Current plan” is each user’s plan today, not at event time.</p>
    </section>
  );
}
