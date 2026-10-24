"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";
import { FILTER_KEYS, filtersFromParams, type FilterKey, type FilterValues } from "@/lib/filters";

/** Filters live in the URL so every view is shareable and survives reloads. Must render under <Suspense>. */
export function useFilters() {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const filters = useMemo(() => filtersFromParams(sp), [sp]);

  const setFilters = useCallback(
    (patch: Partial<Record<FilterKey, string | undefined>>) => {
      const next = new URLSearchParams(sp.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v) next.set(k, v);
        else next.delete(k);
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [sp, router, pathname],
  );

  const reset = useCallback(() => {
    const next = new URLSearchParams(sp.toString());
    FILTER_KEYS.forEach((k) => next.delete(k));
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }, [sp, router, pathname]);

  return { filters: filters as FilterValues, setFilters, reset };
}
