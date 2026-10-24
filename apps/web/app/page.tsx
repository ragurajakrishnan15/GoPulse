import { Suspense } from "react";
import { PageHeader } from "@/components/PageHeader";
import { OverviewView } from "@/components/views/OverviewView";

export const metadata = { title: "Overview" };

export default function Page() {
  return (
    <>
      <PageHeader title="Product overview">
        Adoption, engagement, reliability and cost for a fictional AI writing assistant. Every number comes from a documented definition and a
        synthetic event stream that runs through a real Bronze → Silver → Gold pipeline.
      </PageHeader>
      <Suspense fallback={<div className="skeleton h-40" role="status" aria-label="Loading overview" />}>
        <OverviewView />
      </Suspense>
    </>
  );
}
