import { Suspense } from "react";
import { PageHeader } from "@/components/PageHeader";
import { CatalogListView } from "@/components/views/CatalogView";

export const metadata = { title: "Data catalog" };

export default function Page() {
  return (
    <>
      <PageHeader title="Data catalog">
        Descriptions, grain, keys, lineage, refresh cadence, tests and metric definitions for every dataset behind the dashboards.
      </PageHeader>
      <Suspense fallback={<div className="skeleton h-40" role="status" aria-label="Loading catalog" />}>
        <CatalogListView />
      </Suspense>
    </>
  );
}
