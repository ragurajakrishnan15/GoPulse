import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiGet, apiPost, buildQuery } from "@/lib/api";

afterEach(() => vi.unstubAllGlobals());
/** Await a promise that must reject and return the error as an ApiError. */
async function rejection(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (e) {
    return e as ApiError;
  }
  throw new Error("expected the promise to reject");
}
const json = (body: unknown, init: ResponseInit = {}) => new Response(JSON.stringify(body), { headers: { "content-type": "application/json" }, ...init });

describe("api client", () => {
  it("builds queries skipping empty values", () => {
    expect(buildQuery({ a: "1", b: "", c: undefined, d: null, e: 0 })).toBe("?a=1&e=0");
    expect(buildQuery()).toBe("");
  });
  it("turns the API error envelope into ApiError with details", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ error: { code: 422, message: "Invalid request parameters", details: [{ field: "tier", message: "bad" }] } }, { status: 422 })));
    const e = await rejection(apiGet("/x"));
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(422);
    expect(e.friendly).toContain("tier - bad");
  });
  it("exposes Retry-After on 429", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ error: { code: 429, message: "slow down" } }, { status: 429, headers: { "retry-after": "12" } })));
    const e = await rejection(apiPost("/x", {}));
    expect(e.status).toBe(429);
    expect(e.retryAfter).toBe(12);
  });
  it("maps network failures to a friendly status-0 error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const e = await rejection(apiGet("/x"));
    expect(e.status).toBe(0);
    expect(e.friendly).toMatch(/unreachable/);
  });
  it("hides server internals on 5xx", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ error: { code: 500, message: "stack trace here" } }, { status: 500 })));
    const e = await rejection(apiGet("/x"));
    expect(e.friendly).not.toContain("stack trace");
  });
});
