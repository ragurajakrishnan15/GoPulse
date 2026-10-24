import { Suspense } from "react";
import { PageHeader } from "@/components/PageHeader";
import { PipelinesView } from "@/components/views/PipelinesView";

export const metadata = { title: "Pipeline operations" };

export default function Page() {
  return (
    <>
      <PageHeader title="Pipeline operations">
        Run history for the ingest → Silver → Gold → quality-gate workflow: status, record counts, retries, last success and data freshness.
      </PageHeader>
      <Suspense fallback={<div className="skeleton h-40" role="status" aria-label="Loading pipeline operations" />}>
        <PipelinesView />
      </Suspense>
    </>
  );
}
