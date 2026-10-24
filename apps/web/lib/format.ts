const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
const DASH = "–";

export const fmtInt = (v: number | null | undefined) => (v == null ? DASH : nf0.format(v));
export const fmtNum = (v: number | null | undefined) => (v == null ? DASH : nf1.format(v));
export const fmtPct = (v: number | null | undefined, digits = 1) =>
  v == null ? DASH : `${(v * 100).toFixed(digits)}%`;

export function fmtMs(v: number | null | undefined): string {
  if (v == null) return DASH;
  return v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`;
}

export function fmtUsd(v: number | null | undefined, digits = 4): string {
  if (v == null) return DASH;
  return `$${v.toFixed(v >= 100 ? 0 : digits)}`;
}

/** Format a value according to the API's `unit` string. */
export function fmtUnit(v: number | null | undefined, unit: string | null | undefined): string {
  switch (unit) {
    case "ratio":
      return fmtPct(v);
    case "ms":
      return fmtMs(v);
    case "usd":
      return fmtUsd(v);
    default:
      return Number.isInteger(v ?? 0) ? fmtInt(v) : fmtNum(v);
  }
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const normalized = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toLocaleString("en-US", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} UTC`;
}

export function fmtAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return DASH;
  const normalized = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
  const secs = Math.max(0, Math.round((now - new Date(normalized).getTime()) / 1000));
  if (Number.isNaN(secs)) return DASH;
  if (secs < 90) return "just now";
  if (secs < 5400) return `${Math.round(secs / 60)} min ago`;
  if (secs < 129600) return `${Math.round(secs / 3600)} h ago`;
  return `${Math.round(secs / 86400)} days ago`;
}

export function ageHours(iso: string | null | undefined, now: number = Date.now()): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : (now - t) / 3_600_000;
}

export const titleCase = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
