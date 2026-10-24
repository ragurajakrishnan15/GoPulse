import { Suspense } from "react";
import { PageHeader } from "@/components/PageHeader";
import { AnalyticsView } from "@/components/views/AnalyticsView";

export const metadata = { title: "Product analytics" };

export default function Page() {
  return (
    <>
      <PageHeader title="Product analytics">
        Funnels, cohorts, feature adoption and account-level adoption. Denominators are stated for every rate, and the data shows association,
        not causation.
      </PageHeader>
      <Suspense fallback={<div className="skeleton h-40" role="status" aria-label="Loading product analytics" />}>
        <AnalyticsView />
      </Suspense>
    </>
  );
}
