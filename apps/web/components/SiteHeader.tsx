"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useApi } from "@/hooks/useApi";
import { ageHours, fmtAgo, fmtDate } from "@/lib/format";
import type { FeatureItem, Meta } from "@/lib/types";

export const NAV = [
  { href: "/", label: "Overview" },
  { href: "/pipelines", label: "Pipeline ops" },
  { href: "/quality", label: "Data quality" },
  { href: "/analytics", label: "Product analytics" },
  { href: "/agents", label: "AI agents" },
  { href: "/catalog", label: "Data catalog" },
] as const;

const STALE_AFTER_HOURS = 24;

/** Always-visible honesty strip: synthetic data label, last refresh, and a stale-snapshot warning. */
export function DataStatus() {
  const { data, error } = useApi<{ items: FeatureItem[]; meta: Meta }>("/api/v1/features");
  const meta = data?.meta;
  const age = ageHours(meta?.last_refreshed);
  const stale = age != null && age > STALE_AFTER_HOURS;
  return (
    <div className="border-b border-line bg-infobg text-ink" data-testid="data-status">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-1 px-4 py-1.5 text-xs">
        <strong className="rounded bg-accent px-2 py-0.5 text-accentfg">Synthetic demo data</strong>
        <span>Precomputed snapshot, not a live feed.</span>
        {meta?.last_refreshed && <span>Last refreshed {fmtAgo(meta.last_refreshed)} · data through {fmtDate(meta.as_of_date)}</span>}
        {stale && <span className="rounded bg-warnbg px-2 py-0.5 font-semibold text-warn" role="status">Stale: refreshed over {STALE_AFTER_HOURS} h ago</span>}
        {error && <span className="rounded bg-badbg px-2 py-0.5 font-semibold text-bad" role="status">API unreachable - showing nothing new</span>}
      </div>
    </div>
  );
}

export function SiteHeader() {
  const pathname = usePathname();
  return (
    <header className="border-b border-line bg-surface">
      <DataStatus />
      <div className="mx-auto flex max-w-7xl flex-col gap-2 px-4 py-3 md:flex-row md:items-center md:gap-8">
        <Link href="/" className="flex items-center gap-2 text-lg font-bold tracking-tight text-ink">
          <span aria-hidden="true" className="inline-block h-6 w-6 rounded-md bg-accent" />
          GoPulse
        </Link>
        <nav aria-label="Primary" className="-mx-1 overflow-x-auto">
          <ul className="flex min-w-max gap-1 px-1">
            {NAV.map((n) => {
              const current = n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
              return (
                <li key={n.href}>
                  <Link href={n.href} aria-current={current ? "page" : undefined}
                    className={`block rounded-md px-3 py-1.5 text-sm font-medium ${current ? "bg-accent text-accentfg" : "text-ink hover:bg-surface2"}`}>
                    {n.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>
    </header>
  );
}
