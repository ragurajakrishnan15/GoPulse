import { describe, expect, it } from "vitest";
import { describeFilters, filtersFromParams } from "@/lib/filters";

const sp = (q: string) => new URLSearchParams(q);

describe("filtersFromParams", () => {
  it("keeps valid values", () => {
    expect(filtersFromParams(sp("tier=pro&segment=smb&plan=free&feature=feat_search&start=2026-01-01&end=2026-02-01"))).toEqual({
      tier: "pro", segment: "smb", plan: "free", feature: "feat_search", start: "2026-01-01", end: "2026-02-01",
    });
  });
  it("drops unknown or malformed values so they never reach the API", () => {
    expect(filtersFromParams(sp("tier=platinum&feature=x%27%3Bdrop&start=yesterday&plan=PRO&evil=1"))).toEqual({});
  });
  it("describes the active selection", () => {
    expect(describeFilters({})).toBe("all users");
    expect(describeFilters({ tier: "pro", plan: "free" })).toBe("tier pro, plan free");
  });
});
