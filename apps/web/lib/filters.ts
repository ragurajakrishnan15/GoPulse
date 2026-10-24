export const FILTER_KEYS = ["start", "end", "tier", "segment", "plan", "feature"] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];
export type FilterValues = Partial<Record<FilterKey, string>>;

export const TIERS = ["free", "pro", "team", "enterprise", "no_account"] as const;
export const SEGMENTS = ["smb", "mid_market", "enterprise", "no_account"] as const;
export const PLANS = ["free", "pro"] as const;

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Read filter values from URL params, dropping anything that is not a known, well-formed value. */
export function filtersFromParams(sp: { get(name: string): string | null }): FilterValues {
  const out: FilterValues = {};
  const pick = (key: FilterKey, ok: (v: string) => boolean) => {
    const v = sp.get(key);
    if (v && ok(v)) out[key] = v;
  };
  pick("start", (v) => DATE_RE.test(v));
  pick("end", (v) => DATE_RE.test(v));
  pick("tier", (v) => (TIERS as readonly string[]).includes(v));
  pick("segment", (v) => (SEGMENTS as readonly string[]).includes(v));
  pick("plan", (v) => (PLANS as readonly string[]).includes(v));
  pick("feature", (v) => /^feat_[a-z_]{1,40}$/.test(v));
  return out;
}

export function describeFilters(f: FilterValues): string {
  const parts = [
    f.tier && `tier ${f.tier}`,
    f.segment && `segment ${f.segment}`,
    f.plan && `plan ${f.plan}`,
    f.feature && `feature ${f.feature}`,
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "all users";
}
