import { Suspense } from "react";
import { PageHeader } from "@/components/PageHeader";
import { QualityView } from "@/components/views/QualityView";

export const metadata = { title: "Data quality" };

export default function Page() {
  return (
    <>
      <PageHeader title="Data quality">
        Automated checks at the ingestion, Silver, Gold and dbt layers: what failed, how many records were affected, and whether they were
        quarantined, deduplicated or blocked from publishing.
      </PageHeader>
      <Suspense fallback={<div className="skeleton h-40" role="status" aria-label="Loading data quality" />}>
        <QualityView />
      </Suspense>
    </>
  );
}
