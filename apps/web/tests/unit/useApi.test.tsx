import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useApi } from "@/hooks/useApi";

afterEach(() => vi.unstubAllGlobals());
const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
const fail = (status: number) => new Response(JSON.stringify({ error: { code: status, message: "nope" } }), { status });

describe("useApi", () => {
  it("goes loading -> data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok({ v: 1 })));
    const { result } = renderHook(() => useApi<{ v: number }>("/a"));
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.data).toEqual({ v: 1 }));
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeUndefined();
  });

  it("surfaces errors and can retry", async () => {
    const f = vi.fn().mockResolvedValueOnce(fail(500)).mockResolvedValueOnce(ok({ v: 2 }));
    vi.stubGlobal("fetch", f);
    const { result } = renderHook(() => useApi<{ v: number }>("/a"));
    await waitFor(() => expect(result.current.error?.status).toBe(500));
    expect(result.current.loading).toBe(false);
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.data).toEqual({ v: 2 }));
    expect(result.current.error).toBeUndefined();
  });

  it("keeps previous data while refreshing for new params (stale-while-revalidate)", async () => {
    let release!: (r: Response) => void;
    const f = vi.fn()
      .mockResolvedValueOnce(ok({ v: "first" }))
      .mockImplementationOnce(() => new Promise<Response>((res) => { release = res; }));
    vi.stubGlobal("fetch", f);
    const { result, rerender } = renderHook(({ p }) => useApi<{ v: string }>("/a", { tier: p }), { initialProps: { p: "free" } });
    await waitFor(() => expect(result.current.data).toEqual({ v: "first" }));
    rerender({ p: "pro" });
    await waitFor(() => expect(result.current.refreshing).toBe(true));
    expect(result.current.data).toEqual({ v: "first" });
    expect(result.current.loading).toBe(false);
    await act(async () => release(ok({ v: "second" })));
    await waitFor(() => expect(result.current.data).toEqual({ v: "second" }));
    expect(result.current.refreshing).toBe(false);
  });

  it("ignores a slow response that was superseded", async () => {
    const resolvers: ((r: Response) => void)[] = [];
    vi.stubGlobal("fetch", vi.fn().mockImplementation((_u: string, init: RequestInit) => new Promise<Response>((res, rej) => {
      resolvers.push(res);
      init.signal?.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
    })));
    const { result, rerender } = renderHook(({ p }) => useApi<{ v: string }>("/a", { tier: p }), { initialProps: { p: "a" } });
    rerender({ p: "b" });
    await waitFor(() => expect(resolvers.length).toBe(2));
    await act(async () => resolvers[1](ok({ v: "b" })));
    await act(async () => resolvers[0](ok({ v: "a" })));
    await waitFor(() => expect(result.current.data).toEqual({ v: "b" }));
  });

  it("does nothing when the path is null", () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const { result } = renderHook(() => useApi<unknown>(null));
    expect(result.current.loading).toBe(false);
    expect(f).not.toHaveBeenCalled();
  });
});
