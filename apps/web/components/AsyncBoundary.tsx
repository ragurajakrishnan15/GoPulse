"use client";

import type { ReactNode } from "react";
import type { ApiState } from "@/hooks/useApi";

type Variant = "cards" | "chart" | "table";

/** Placeholder geometry. Match the real content's size: skeletons that are shorter/taller than the data cause layout shift (CLS). */
export interface SkeletonShape {
  count?: number;
  /** px height of each card placeholder */
  height?: number;
  /** grid classes of the real layout */
  grid?: string;
}

function Skeleton({ variant, label, shape }: { variant: Variant; label: string; shape?: SkeletonShape }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true" aria-label={`Loading ${label}`}>
      <span className="sr-only">Loading {label}…</span>
      {variant === "cards" ? (
        <div className={`grid ${shape?.grid ?? "grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"}`}>
          {Array.from({ length: shape?.count ?? 8 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: shape?.height ?? 112 }} />
          ))}
        </div>
      ) : variant === "chart" ? (
        <div className="skeleton" style={{ height: 370 }} />
      ) : (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="skeleton h-9" />
          ))}
        </div>
      )}
    </div>
  );
}

export function ErrorBox({ message, onRetry, inline = false }: { message: string; onRetry?: () => void; inline?: boolean }) {
  return (
    <div role="alert" className={`flex flex-wrap items-center gap-3 rounded-lg border border-bad bg-badbg p-3 text-sm text-ink ${inline ? "mb-3" : ""}`}>
      <span className="font-semibold text-bad">Something went wrong.</span>
      <span className="min-w-[12rem] flex-1 break-words">{message}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="rounded-md border border-line bg-surface px-3 py-1 font-medium hover:bg-surface2">
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyBox({ message }: { message: string }) {
  return (
    <div role="status" className="rounded-lg border border-dashed border-line p-8 text-center text-sm text-muted">
      {message}
    </div>
  );
}

interface Props<T> {
  state: Pick<ApiState<T>, "data" | "loading" | "error" | "refreshing" | "reload">;
  /** Label used for the loading announcement, e.g. "KPIs". */
  label: string;
  variant?: Variant;
  skeleton?: SkeletonShape;
  isEmpty?: (data: T) => boolean;
  emptyMessage?: string;
  children: (data: T) => ReactNode;
}

/** Renders loading, error (with retry), empty, stale-while-refreshing and loaded states consistently. */
export function AsyncBoundary<T>({ state, label, variant = "chart", skeleton, isEmpty, emptyMessage = "No data for the current selection.", children }: Props<T>) {
  const { data, loading, error, refreshing, reload } = state;
  if (loading) return <Skeleton variant={variant} label={label} shape={skeleton} />;
  if (error && data === undefined) return <ErrorBox message={error.friendly} onRetry={reload} />;
  if (data === undefined) return null;
  return (
    <div aria-busy={refreshing} className={refreshing ? "opacity-60 transition-opacity" : "transition-opacity"}>
      {error && <ErrorBox inline message={`${error.friendly} Showing the last loaded data.`} onRetry={reload} />}
      {refreshing && <span className="sr-only" role="status">Updating {label}…</span>}
      {isEmpty?.(data) ? <EmptyBox message={emptyMessage} /> : children(data)}
    </div>
  );
}
