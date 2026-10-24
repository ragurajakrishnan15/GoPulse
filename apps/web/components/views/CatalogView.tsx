"use client";

import Link from "next/link";
import { use, useId, useState } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { LayerBadge, StatusBadge } from "@/components/Badges";
import { DataTable } from "@/components/DataTable";
import { SectionTitle } from "@/components/PageHeader";
import { useApi } from "@/hooks/useApi";
import { fmtDateTime, fmtInt, titleCase } from "@/lib/format";
import type { CatalogMetric, CatalogModel } from "@/lib/types";

const selectCls = "rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink";
const modelLink = (name: string) => `/catalog/${name}`;

export function CatalogListView() {
  const uid = useId();
  const [layer, setLayer] = useState("");
  const [q, setQ] = useState("");
  const models = useApi<{ items: CatalogModel[] }>("/api/v1/catalog/models", { layer });
  const metrics = useApi<{ items: CatalogMetric[] }>("/api/v1/catalog/metrics");
  const needle = q.trim().toLowerCase();

  return (
    <div className="space-y-6">
      <section aria-labelledby={`${uid}-m`}>
        <SectionTitle id={`${uid}-m`} sub="Every dataset the dashboard reads, with grain, key, refresh cadence and test status.">Datasets and models</SectionTitle>
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor={`${uid}-q`} className="mb-1 block text-xs font-medium text-muted">Search</label>
            <input id={`${uid}-q`} type="search" className={`${selectCls} w-56`} placeholder="e.g. agent, retention" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div>
            <label htmlFor={`${uid}-l`} className="mb-1 block text-xs font-medium text-muted">Layer</label>
            <select id={`${uid}-l`} className={selectCls} value={layer} onChange={(e) => setLayer(e.target.value)}>
              <option value="">All</option><option value="silver">Silver</option><option value="gold">Gold</option><option value="mart">Marts (dbt)</option>
            </select>
          </div>
        </div>
        <AsyncBoundary state={models} label="catalog" variant="table"
          isEmpty={(d) => d.items.filter((m) => `${m.name} ${m.description}`.toLowerCase().includes(needle)).length === 0} emptyMessage="No models match your search.">
          {(d) => (
            <DataTable<CatalogModel> caption="Data catalog models" dense maxHeight="32rem" rowKey={(m) => m.name}
              rows={d.items.filter((m) => `${m.name} ${m.description}`.toLowerCase().includes(needle))}
              columns={[
                { key: "name", header: "Model", render: (m) => <Link href={modelLink(m.name)} className="font-semibold text-accent underline underline-offset-2">{m.name}</Link> },
                { key: "layer", header: "Layer", render: (m) => <LayerBadge layer={m.layer} /> },
                { key: "description", header: "Description" },
                { key: "grain", header: "Grain" },
                { key: "primary_key", header: "Primary key", render: (m) => <code className="text-xs">{m.primary_key}</code> },
                { key: "row_count", header: "Rows", align: "right", render: (m) => fmtInt(m.row_count) },
                { key: "test_status", header: "Tests", render: (m) => <StatusBadge status={m.test_status} /> },
              ]} />
          )}
        </AsyncBoundary>
      </section>

      <section aria-labelledby={`${uid}-d`}>
        <SectionTitle id={`${uid}-d`} sub="Numerator, denominator, window and eligibility for every metric shown on the site.">Metric definitions</SectionTitle>
        <AsyncBoundary state={metrics} label="metric definitions" variant="table">
          {(d) => (
            <DataTable<CatalogMetric> caption="Metric definitions" dense rows={d.items} rowKey={(m) => m.key}
              columns={[
                { key: "label", header: "Metric", render: (m) => <span className="font-semibold">{m.label}</span> },
                { key: "numerator", header: "Numerator" },
                { key: "denominator", header: "Denominator" },
                { key: "time_window", header: "Window" },
                { key: "eligibility", header: "Eligibility / notes", render: (m) => <span>{m.eligibility}{m.notes ? ` ${m.notes}` : ""}</span> },
                { key: "source_model", header: "Source", render: (m) => <Link href={modelLink(m.source_model)} className="text-accent underline underline-offset-2">{m.source_model}</Link> },
              ]} />
          )}
        </AsyncBoundary>
      </section>
    </div>
  );
}

function Links({ names, empty }: { names: string[]; empty: string }) {
  if (names.length === 0) return <span className="text-muted">{empty}</span>;
  return (
    <ul className="flex flex-wrap gap-2">
      {names.map((n) => (
        <li key={n}>
          <Link href={modelLink(n)} className="rounded-md border border-line px-2 py-0.5 font-mono text-xs text-accent underline underline-offset-2">{n}</Link>
        </li>
      ))}
    </ul>
  );
}

export function CatalogDetailView({ params }: { params: Promise<{ name: string }> }) {
  const { name } = use(params);
  const state = useApi<{ model: CatalogModel }>(`/api/v1/catalog/models/${encodeURIComponent(name)}`);
  if (state.error?.status === 404 && !state.data) {
    return (
      <div className="space-y-3">
        <nav aria-label="Breadcrumb" className="text-sm"><Link href="/catalog" className="text-accent underline underline-offset-2">← All models</Link></nav>
        <h1 className="text-2xl font-bold text-ink">Model not found</h1>
        <p role="alert" className="text-sm text-muted">There is no catalog entry named <code>{name}</code>. Pick a model from the catalog list.</p>
      </div>
    );
  }
  return (
    <AsyncBoundary state={state} label="model details" variant="table">
      {({ model: m }) => (
        <div className="space-y-6">
          <nav aria-label="Breadcrumb" className="text-sm"><Link href="/catalog" className="text-accent underline underline-offset-2">← All models</Link></nav>
          <div>
            <h1 className="flex flex-wrap items-center gap-3 text-2xl font-bold text-ink"><code>{m.name}</code> <LayerBadge layer={m.layer} /> <StatusBadge status={m.test_status} /></h1>
            <p className="mt-2 max-w-3xl text-ink">{m.description}</p>
            {m.purpose && <p className="mt-1 max-w-3xl text-sm text-muted"><span className="font-semibold text-ink">Business purpose: </span>{m.purpose}</p>}
          </div>
          <dl className="grid gap-x-8 gap-y-3 rounded-xl border border-line bg-surface p-4 text-sm sm:grid-cols-2">
            <div><dt className="font-semibold text-muted">Grain</dt><dd className="text-ink">{m.grain}</dd></div>
            <div><dt className="font-semibold text-muted">Primary key</dt><dd><code className="text-ink">{m.primary_key}</code></dd></div>
            <div><dt className="font-semibold text-muted">Refresh cadence</dt><dd className="text-ink">{m.refresh_cadence}</dd></div>
            <div><dt className="font-semibold text-muted">Last refresh</dt><dd className="text-ink">{fmtDateTime(m.last_refresh)}</dd></div>
            <div><dt className="font-semibold text-muted">Rows</dt><dd className="tabular-nums text-ink">{fmtInt(m.row_count)}</dd></div>
            <div><dt className="font-semibold text-muted">Source</dt><dd className="break-all"><a href={m.source_link} className="text-accent underline underline-offset-2" rel="noreferrer noopener" target="_blank">{m.source_link.split("/blob/main/")[1] ?? m.source_link}<span className="sr-only"> (opens in a new tab)</span></a></dd></div>
            <div><dt className="font-semibold text-muted">Upstream</dt><dd><Links names={m.upstream} empty="Source data" /></dd></div>
            <div><dt className="font-semibold text-muted">Downstream</dt><dd><Links names={m.downstream} empty="None" /></dd></div>
          </dl>

          <section aria-labelledby="cols"><SectionTitle id="cols">Columns</SectionTitle>
            <DataTable caption={`Columns of ${m.name}`} dense maxHeight="26rem" rows={m.columns ?? []} rowKey={(c) => c.name}
              columns={[{ key: "name", header: "Column", render: (c) => <code className="text-xs">{c.name}</code> }, { key: "type", header: "Type", render: (c) => <code className="text-xs text-muted">{c.type}</code> },
                { key: "description", header: "Description", render: (c) => c.description ?? <span className="text-muted">–</span> }]} />
          </section>

          <section aria-labelledby="tests"><SectionTitle id="tests" sub="Latest status from the Python quality gate and dbt tests.">Tests</SectionTitle>
            {(m.tests ?? []).length === 0 ? <p className="text-sm text-muted">No tests are attached to this model.</p> : (
              <DataTable caption={`Tests on ${m.name}`} dense maxHeight="20rem" rows={m.tests ?? []} rowKey={(t, i) => `${t.name}-${i}`}
                columns={[{ key: "name", header: "Test", render: (t) => <code className="text-xs">{t.name}</code> }, { key: "source", header: "Engine", render: (t) => titleCase(t.source) },
                  { key: "status", header: "Status", render: (t) => <StatusBadge status={t.status} /> }]} />
            )}
          </section>

          {(m.metrics ?? []).length > 0 && (
            <section aria-labelledby="mets"><SectionTitle id="mets">Metrics defined here</SectionTitle>
              <ul className="list-disc pl-5 text-sm text-ink">{(m.metrics ?? []).map((x) => <li key={x.key}>{x.label}</li>)}</ul>
            </section>
          )}
        </div>
      )}
    </AsyncBoundary>
  );
}
