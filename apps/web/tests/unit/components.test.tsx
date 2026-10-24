import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { SourceBadge } from "@/components/Badges";
import { KpiCard } from "@/components/KpiCard";
import { RetentionHeatmap } from "@/components/RetentionHeatmap";
import { ApiError } from "@/lib/api";
import type { Kpi, RetentionResponse } from "@/lib/types";

const base = { data: undefined, loading: false, error: undefined, refreshing: false, reload: () => {} };

describe("AsyncBoundary", () => {
  it("announces loading", () => {
    render(<AsyncBoundary state={{ ...base, loading: true }} label="KPIs">{() => <p>ok</p>}</AsyncBoundary>);
    expect(screen.getByRole("status", { name: /loading kpis/i })).toBeInTheDocument();
  });
  it("shows an error with a working retry", async () => {
    const reload = vi.fn();
    render(<AsyncBoundary state={{ ...base, error: new ApiError(0, "x"), reload }} label="KPIs">{() => <p>ok</p>}</AsyncBoundary>);
    expect(screen.getByRole("alert")).toHaveTextContent(/unreachable/i);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(reload).toHaveBeenCalledOnce();
  });
  it("shows the empty state", () => {
    render(<AsyncBoundary state={{ ...base, data: [] as number[] }} label="x" isEmpty={(d) => d.length === 0} emptyMessage="Nothing here">{() => <p>ok</p>}</AsyncBoundary>);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
  });
  it("keeps stale data visible with an inline error and marks busy while refreshing", () => {
    const { container } = render(<AsyncBoundary state={{ ...base, data: 5, error: new ApiError(500, "x"), refreshing: true }} label="x">{(d) => <p>value {d}</p>}</AsyncBoundary>);
    expect(screen.getByText("value 5")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/last loaded data/i);
    expect(container.firstElementChild).toHaveAttribute("aria-busy", "true");
  });
});

const kpi: Kpi = {
  key: "activation_rate", label: "Activation rate", value: 0.646, unit: "ratio", numerator: 281, denominator: 435,
  definition: "Share who finished onboarding in 7 days.", window: "7 days", eligibility: "Only complete windows.",
  applied_filters: ["date", "tier"], note: "Association, not causation.",
};

describe("KpiCard", () => {
  it("shows value, numerator/denominator and the definition", () => {
    render(<KpiCard kpi={kpi} active={[]} />);
    expect(screen.getByText("64.6%")).toBeInTheDocument();
    expect(screen.getByText(/281 of 435 eligible/)).toBeInTheDocument();
    expect(screen.getByText("Share who finished onboarding in 7 days.")).toBeInTheDocument();
  });
  it("warns when an active filter does not apply to the metric", () => {
    render(<KpiCard kpi={kpi} active={["tier", "feature"]} />);
    expect(screen.getByText(/Not filtered by: feature/)).toBeInTheDocument();
    expect(screen.queryByText(/Not filtered by: tier/)).not.toBeInTheDocument();
  });
  it("flags estimates", () => {
    render(<KpiCard kpi={{ ...kpi, key: "c", unit: "usd", value: 0.0253, estimate: true, denominator: 4527, numerator: 114.7 }} active={[]} />);
    expect(screen.getByText("Estimate")).toBeInTheDocument();
    expect(screen.getByText("$0.0253")).toBeInTheDocument();
  });
});

describe("RetentionHeatmap", () => {
  const data = {
    filters: {}, granularity: "week", day_offsets: [1, 7], definition: null, meta: {},
    cohorts: [{ cohort_start: "2026-01-05", retention: {
      "1": { eligible_users: 10, retained_users: 4, rate: 0.4 },
      "7": { eligible_users: 0, retained_users: 0, rate: null },
    } }],
  } as unknown as RetentionResponse;
  it("prints the rate with counts, and a dash (not 0%) when the window is incomplete", () => {
    render(<RetentionHeatmap data={data} />);
    expect(screen.getByText("40%")).toHaveAttribute("title", "4 of 10 eligible users");
    expect(screen.getByLabelText("not yet observable")).toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });
});

describe("SourceBadge", () => {
  it("labels real and seeded runs with text, not just colour", () => {
    render(<><SourceBadge source="local-real" /><SourceBadge source="seeded-example" /><SourceBadge source="demo-live" /></>);
    expect(screen.getByText("Real · pipeline run")).toBeInTheDocument();
    expect(screen.getByText("Seeded example")).toBeInTheDocument();
    expect(screen.getByText("Real · demo run")).toBeInTheDocument();
  });
});
