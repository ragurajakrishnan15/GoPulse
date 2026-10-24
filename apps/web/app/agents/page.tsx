import { Suspense } from "react";
import { PageHeader } from "@/components/PageHeader";
import { AgentsView } from "@/components/views/AgentsView";

export const metadata = { title: "AI agent insights" };

export default function Page() {
  return (
    <>
      <PageHeader title="AI agent insights">
        Success and failure rates, latency percentiles, token usage and estimated cost for synthetic agent executions.
      </PageHeader>
      <Suspense fallback={<div className="skeleton h-40" role="status" aria-label="Loading agent insights" />}>
        <AgentsView />
      </Suspense>
    </>
  );
}
