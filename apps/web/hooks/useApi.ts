"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, apiGet, type Params } from "@/lib/api";

interface Settled<T> {
  key: string;
  data?: T;
  error?: ApiError;
}

export interface ApiState<T> {
  /** Latest successfully loaded data (kept while a newer request is in flight - "stale while revalidating"). */
  data: T | undefined;
  error: ApiError | undefined;
  /** First load: nothing to show yet. */
  loading: boolean;
  /** A newer request is in flight and `data` belongs to the previous one. */
  refreshing: boolean;
  reload: () => void;
}

const stableKey = (params?: Params) =>
  JSON.stringify(Object.entries(params ?? {}).filter(([, v]) => v != null && v !== "").sort());

/**
 * Core async state machine. Loading/refreshing are DERIVED from which request key last settled, so nothing is set
 * synchronously inside the effect, and out-of-order responses are dropped via AbortController.
 */
function useAsync<T>(enabled: boolean, baseKey: string, run: (signal: AbortSignal) => Promise<T>): ApiState<T> {
  const [nonce, setNonce] = useState(0);
  const key = enabled ? `${baseKey}|${nonce}` : "";
  const [settled, setSettled] = useState<Settled<T>>({ key: "" });

  useEffect(() => {
    if (!enabled) return;
    const ac = new AbortController();
    run(ac.signal)
      .then((data) => setSettled({ key, data }))
      .catch((e: unknown) => {
        if (ac.signal.aborted || (e as Error).name === "AbortError") return;
        setSettled((prev) => ({ key, data: prev.data, error: e instanceof ApiError ? e : new ApiError(0, String(e)) }));
      });
    return () => ac.abort();
    // `run` is derived from baseKey
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const current = settled.key === key;
  return useMemo(
    () => ({
      data: settled.data,
      error: current ? settled.error : undefined,
      loading: enabled && settled.data === undefined && !(current && settled.error),
      refreshing: enabled && !current && settled.data !== undefined,
      reload,
    }),
    [settled, current, enabled, reload],
  );
}

export function useApi<T>(path: string | null, params?: Params): ApiState<T> {
  return useAsync<T>(!!path, `${path}|${stableKey(params)}`, (signal) => apiGet<T>(path as string, params, signal));
}

export interface Request {
  path: string;
  params?: Params;
}

/** Several GETs fetched together; resolves to an array in request order. */
export function useApiAll<T>(requests: Request[] | null): ApiState<T[]> {
  const baseKey = requests ? requests.map((r) => `${r.path}|${stableKey(r.params)}`).join("&") : "";
  return useAsync<T[]>(!!requests, baseKey, (signal) =>
    Promise.all((requests as Request[]).map((r) => apiGet<T>(r.path, r.params, signal))),
  );
}
