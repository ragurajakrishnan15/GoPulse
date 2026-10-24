import type { RunSource } from "@/lib/types";

const base = "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-semibold";

export function SourceBadge({ source }: { source: RunSource }) {
  if (source === "seeded-example")
    return <span className={`${base} border-dashed border-warn bg-warnbg text-warn`} title="Illustrative example history - not a real execution">Seeded example</span>;
  if (source === "demo-live")
    return <span className={`${base} border-accent bg-infobg text-ink`} title="Real execution triggered from this site (bounded demo)">Real · demo run</span>;
  return <span className={`${base} border-good bg-okbg text-ink`} title="Real execution of the local pipeline">Real · pipeline run</span>;
}

export function StatusBadge({ status }: { status: string }) {
  const good = status === "success" || status === "pass" || status === "passing";
  const bad = status === "failed" || status === "fail" || status === "failing";
  const cls = good ? "border-good bg-okbg" : bad ? "border-bad bg-badbg" : "border-line bg-surface2";
  const icon = good ? "✓" : bad ? "✕" : "•";
  return <span className={`${base} ${cls} text-ink`}><span aria-hidden="true">{icon}</span>{status.replace(/_/g, " ")}</span>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  return <span className={`${base} ${severity === "error" ? "border-bad" : "border-warn"} bg-surface text-ink`}>{severity}</span>;
}

export function LayerBadge({ layer }: { layer: string }) {
  return <span className={`${base} border-line bg-surface2 text-ink`}>{layer}</span>;
}
