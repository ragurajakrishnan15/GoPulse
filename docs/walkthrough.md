# Walkthrough script (≈ 3 minutes)

`docs/media/walkthrough.webm` is a silent, captioned screen recording of the **real site**, produced by
`apps/web/scripts/record_walkthrough.mjs` (re-run it whenever the UI changes so it never goes stale). Use this script to narrate over it,
or as speaker notes for a live demo. The captions in the video follow the same order.

**Before you start:** `docker compose up -d --build && docker compose --profile seed run --rm seed`, then open `http://localhost:3000`.

| ~Time | Screen | Say (and why it matters) |
|---|---|---|
| 0:00 | Overview | "GoPulse is an end-to-end product-analytics platform for a fictional AI writing assistant. Everything is **synthetic** - the banner says so and shows how fresh the snapshot is. Behind this page is a real Bronze → Silver → Gold pipeline." |
| 0:20 | KPI card → *Definition* | "Every number shows its numerator and denominator and the exact rule. Activation is 281 of 435 *eligible* users - users whose 7-day window hasn't finished are excluded, not counted as failures." |
| 0:40 | Tier filter, then Feature filter | "Filters live in the URL, so any view is shareable. When a filter doesn't apply to a metric - like feature on a signup cohort - the card says so instead of silently ignoring it." |
| 1:00 | Chart → *View as table* | "Charts show their date range and definition and have a table alternative. The site passes automated WCAG A/AA checks in light and dark mode." |
| 1:15 | Pipeline ops | "Real runs and seeded example history are labelled differently - I never present invented history as real. This button runs the **actual** pipeline on a small batch. It can't take commands, SQL or paths, it's rate-limited, and it uses a database role that can only insert run history." |
| 1:35 | Demo run, ~40% bad records | "40% of this batch is invalid, so the quality gate **blocks** it. Nothing is published and the previous data stays live. Retries are only for recoverable failures - a data problem isn't retried." |
| 1:55 | Failed seeded run → *Details* | "Each run has tasks, counts, retries, freshness lag and a log excerpt." |
| 2:05 | Data quality | "Each failed check says how many records were affected and what happened to them: quarantined, deduplicated or blocked. I can inspect the actual failing rows." |
| 2:25 | Failure demonstration | "A 3%-bad batch is published with warnings: 750 records quarantined, 125 duplicates removed. A 40%-bad batch is blocked. Re-delivering the corrected batch restores every metric exactly. The 'naive' column shows what a dashboard would have shown without validation." |
| 2:45 | Product analytics | "Funnel stages have different denominators and each states its own. In the retention table a dash means the window hasn't elapsed - excluded from both numerator and denominator, not zero." |
| 3:05 | AI agents, Catalog | "Success, retry rate, P50/P95 and failure categories. Cost is labelled an **estimate** from documented token pricing, not vendor billing. The catalog gives grain, key, columns, lineage and test status for every model." |

**Closing line:** "The point wasn't the charts - it was making the numbers trustworthy: bad data is quarantined or blocked and visible, reruns are idempotent,
and every metric has a definition and a test."

## If someone asks
* *Is it deployed?* Yes, on free tiers: the site and API on Vercel, the database on Supabase ([deployment.md](deployment.md)). The same 54 browser tests pass against the live site. A cold start (first request after a deploy) took ~2 s. CI-driven deploys are written but off. No Databricks run and no cloud cost is claimed.
* *Did you run it on Databricks?* No. The Spark/Delta implementation is executed locally and must match the DuckDB pipeline row-for-row (tested); the Databricks job/notebooks are structure-checked only.
* *Why DuckDB locally?* Same SQL, no cloud credentials, fast tests; Spark parity covers the cloud path.
