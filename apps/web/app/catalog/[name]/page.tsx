import { Suspense } from "react";
import { CatalogDetailView } from "@/components/views/CatalogView";

export const metadata = { title: "Model details" };

export default function Page({ params }: { params: Promise<{ name: string }> }) {
  // `params` is a Promise in this Next.js version; it is unwrapped in the client component under Suspense.
  return (
    <Suspense fallback={<div className="skeleton h-40" role="status" aria-label="Loading model" />}>
      <CatalogDetailView params={params} />
    </Suspense>
  );
}
