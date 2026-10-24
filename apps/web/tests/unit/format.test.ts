import { describe, expect, it } from "vitest";
import { ageHours, fmtAgo, fmtDate, fmtMs, fmtPct, fmtUnit, fmtUsd, titleCase } from "@/lib/format";

describe("format", () => {
  it("formats ratios, durations and money", () => {
    expect(fmtPct(0.6459)).toBe("64.6%");
    expect(fmtPct(null)).toBe("–");
    expect(fmtMs(850)).toBe("850 ms");
    expect(fmtMs(1656)).toBe("1.66 s");
    expect(fmtUsd(0.025337)).toBe("$0.0253");
    expect(fmtUsd(116.527)).toBe("$117");
  });
  it("dispatches on the API unit", () => {
    expect(fmtUnit(0.5, "ratio")).toBe("50.0%");
    expect(fmtUnit(2000, "ms")).toBe("2.00 s");
    expect(fmtUnit(1234, "count")).toBe("1,234");
    expect(fmtUnit(12.34, "count")).toBe("12.3");
  });
  it("formats dates in UTC regardless of locale", () => {
    expect(fmtDate("2026-03-01")).toBe("Mar 1, 2026");
    expect(fmtDate(null)).toBe("–");
  });
  it("computes relative age", () => {
    const now = Date.parse("2026-01-02T00:00:00Z");
    expect(fmtAgo("2026-01-01T23:59:30Z", now)).toBe("just now");
    expect(fmtAgo("2026-01-01T22:00:00Z", now)).toBe("2 h ago");
    expect(fmtAgo("2025-12-01T00:00:00Z", now)).toBe("32 days ago");
    expect(ageHours("2026-01-01T00:00:00Z", now)).toBe(24);
    expect(ageHours(null)).toBeNull();
  });
  it("title-cases snake case", () => expect(titleCase("no_account")).toBe("No account"));
});
