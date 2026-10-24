# Benchmarks

Measured on one developer machine (Windows 11, 12 logical cores, 7.7 GB RAM that was also running a browser and Docker Desktop, Python 3.11.9,
DuckDB 2.0.0). They show **relative** change and scaling shape, not what production hardware would do. Raw results are in
[`docs/benchmarks/`](benchmarks/); reproduce with `python scripts/benchmark_pipeline.py --users 500 2000 5000 10000`.
Data is synthetic; no cloud compute was used, so **no cloud runtime or cost figure exists**.

## 1. Local pipeline (ingest → Silver → Gold → reconcile → quality gate)

Each size: fixed seed, 2% injected defects, fresh DuckDB file, whole run in one transaction.

| Users | Events | Before | After (run 1) | After (run 2) | Speed-up |
|---:|---:|---:|---:|---:|---:|
| 500 | 28,710 | 5.06 s | 1.61 s | 1.89 s | 2.7 - 3.1× |
| 2,000 | 119,173 | 20.18 s | 5.25 s | 6.65 s | 3.0 - 3.8× |
| 5,000 | 298,731 | 46.62 s | 10.46 s | 11.66 s | 4.0 - 4.5× |
| 10,000 | 600,078 | not measured | - | 23.90 s | - |

* **Outputs are unchanged by the optimisation:** quarantined (490 / 2,036 / 5,106) and duplicate (82 / 340 / 851) counts are identical before and after, and
  all 102 Python tests passed at the time of the change (103 now).
* **Variance is real.** The two "after" runs differ by up to ~25% because the machine was under memory pressure; "before" is a single run. Treat
  speed-ups as "roughly 3-4×", not a precise figure.
* **Scaling is linear:** ~25,000 events/s end to end from 5,000 users upward. At 10,000 users: ingest 16.0 s, Silver 4.5 s, Gold 1.1 s, quality 0.05 s.

### What was slow and what changed
Profiling (cProfile, 57,904 events) showed `validate_event` was ~70% of ingest time, almost all of it `jsonschema` walking the schema for every
event, plus ~4 s per 1,000 users spent loading dimensions with a Python `executemany` loop. Ingestion was 93% of the 5,000-user run.

1. **Validation fast path.** A strict hand-written check accepts events that are *certainly* valid; anything else falls through to the full
   JSON-Schema check, which still produces the exact reason codes. JSON Schema remains the source of truth.
   *Safety:* a differential test mutates real events 30,000 times (wrong types, deleted/extra keys, junk values) and asserts the fast path never
   accepts something the schema rejects, and the full validator's output is identical with the fast path disabled. **This fuzzing found a real bug**
   in the first version (an unhashable value in `status` crashed ingestion instead of quarantining) before it shipped.
2. **Dimension loading** now lets DuckDB read the JSONL directly instead of a row-by-row Python loop.

After the change ingest still dominates (~67% at 10,000 users), so the next lever would be vectorised or parallel validation - not done, because the pipeline
is already comfortably faster than the demo needs.

*Earlier, during development (not a controlled benchmark):* replacing row-at-a-time `executemany` with a staged bulk load took event ingestion of a
29,264-event batch from ~25 s to ~5 s.

## 2. Spark/Delta vs DuckDB
Parity, not speed, is what is tested: the PySpark/Delta implementation produces identical Silver and Gold to the DuckDB pipeline. The 5-test suite takes
~8 minutes in a Linux container, mostly Spark session start-up and local-mode shuffles on tiny data; **no speed comparison is claimed** - local-mode
Spark on a few hundred thousand rows says nothing about a cluster.

## 3. API latency
Containerised stack (`docker compose`), single uvicorn worker, PostgreSQL 16 in a sibling container, the 500-user demo dataset. Client-side wall time over
localhost (includes HTTP), 100 timed requests per endpoint after 10 warm-ups; reproduce with `python scripts/benchmark_api.py`. Raw: [`api_before.json`](benchmarks/api_before.json), [`api.json`](benchmarks/api.json).

| Endpoint | p50 | p95 | Response |
|---|---:|---:|---:|
| `/health` | 1.6 ms | 1.9 ms | 0.1 KB |
| `/overview` | **20.8 ms** (was 63.5) | 26.9 ms (was 66.8) | 4.3 KB |
| `/overview` with tier + plan filter | 8.0 ms | 10.2 ms | 4.2 KB |
| `/metrics/timeseries` DAU | 12.4 ms | 14.0 ms | 4.8 KB |
| `/metrics/timeseries` WAU (rolling 7-day window) | 46.4 ms | 50.3 ms | 4.8 KB |
| `/metrics/timeseries` P95 latency | 6.4 ms | 8.1 ms | 5.4 KB |
| `/funnels/activation` | 3.0 ms | 3.7 ms | 1.2 KB |
| `/retention/cohorts` | 6.8 ms | 8.6 ms | 3.6 KB |
| `/agents/summary` | 16.7 ms | 19.9 ms | 6.8 KB |
| `/features/adoption` | 3.8 ms | 4.4 ms | 12.3 KB |
| `/pipelines/runs` (25 rows) | 4.3 ms | 5.4 ms | 19.7 KB |
| `/quality/checks` | 5.2 ms | 5.9 ms | 27.0 KB |
| `/catalog/models` | 3.1 ms | 3.9 ms | 11.5 KB |

**Concurrent load** (20 clients × 25 requests, one worker): `/overview` **50.9 → 108.4 req/s** and p95 **435 → 164 ms**; `/metrics/timeseries?metric=dau` ~145 req/s,
p95 ~111 ms. No errors in any run.

**What was slow and what changed.** `/overview` was the slowest endpoint (63.5 ms, 5× a typical one). It built the full 60-day rolling-WAU series
only to show its last value. It now counts that single window directly - **3.1× faster at the median** - and a test proves the KPI still equals the
last point of the series and the dbt mart, unfiltered and under four filter combinations. The remaining hot spot is the WAU *time series* itself
(~46 ms; a rolling-window self-join). It is fine at this size and would be the first thing to pre-aggregate if the dataset grew.

## 4. Page-load metrics
Production build in the web container, headless Chromium 156, **no network or CPU throttling** (localhost), cold cache per run, median of 5 runs.
Raw: [`web_vitals_before.json`](benchmarks/web_vitals_before.json), [`web_vitals.json`](benchmarks/web_vitals.json); reproduce with `node apps/web/scripts/capture_media.mjs`.

| Page | LCP | FCP | TTFB | CLS before | **CLS after** | JS (encoded) | Requests |
|---|---:|---:|---:|---:|---:|---:|---:|
| Overview | 60 ms | 60 ms | 6 ms | 0.0985 | **0.0526** | 327 KB | 38 |
| Pipelines | 144 ms | 52 ms | 5 ms | 0.0990 | **0.0118** | 327 KB | 29 |
| Data quality | 76 ms | 76 ms | 6 ms | 0.0435 | **0.0229** | 327 KB | 30 |
| Analytics | 56 ms | 56 ms | 6 ms | 0.0769 | **0.0440** | 327 KB | 34 |
| Agents | 56 ms | 56 ms | 6 ms | 0.0605 | **0.0219** | 327 KB | 32 |
| Catalog | 56 ms | 56 ms | 6 ms | 0.0916 | **0.0697** | 327 KB | 35 |
| Catalog model | 76 ms | 76 ms | 8 ms | 0.0532 | **0.0248** | 327 KB | 30 |

**What was wrong and what changed.** Cumulative layout shift was close to the 0.1 "good" limit on Overview and Pipelines: the loading skeletons did not match the
real content (the Pipelines summary showed eight placeholder cards for a four-card row, and chart placeholders were 256 px against ~370 px real cards), so
everything jumped when data arrived, and the footer was pushed down. Skeletons now use the real heights and counts and the main area reserves space.
All pages are now at or below 0.07, and Pipelines improved ~8×.

**Caveats.** LCP here measures the app's own cost on localhost; real visitors add network latency, and data is fetched client-side after the shell
renders, so on a slow link the *content* appears later than these numbers suggest. CLS varies run to run (the probe saw 0 on one Overview load and 0.0985 on
another before the fix) - treat it as a range. These are not Lighthouse scores.

## 5. The deployed system
Measured against the live deployment (Vercel Python function in Virginia → Supabase PostgreSQL 17 through the transaction pooler), from a developer machine
over the public internet. Raw: [`api_live.json`](benchmarks/api_live.json), [`web_vitals_live.json`](benchmarks/web_vitals_live.json).

**API** - 40 timed requests per endpoint after 10 warm-ups, one persistent connection:

| Endpoint | p50 | p95 |
|---|---:|---:|
| `/health` (no database work) | 50.8 ms | 65.2 ms |
| `/overview` | 47.7 ms | 62.8 ms |
| `/metrics/timeseries` DAU / WAU / P95 | 46.1 / 46.4 / 47.7 ms | 127.3 / 57.8 / 60.0 ms |
| `/funnels/activation`, `/retention/cohorts` | 47.2 / 47.7 ms | 58.4 / 62.2 ms |
| `/agents/summary` | 47.0 ms | 59.9 ms |
| `/pipelines/runs` (25 rows, 19.7 KB) | 47.6 ms | 67.4 ms |
| `/quality/checks` (27 KB) | 47.4 ms | 57.9 ms |
| `/catalog/models` | 48.0 ms | 72.7 ms |

Read this carefully: every endpoint is ~47 ms because that is the **network round trip** - the do-nothing `/health` takes the same. Query time is a few
milliseconds and not separable from client-side measurement at this resolution (locally the same queries took 3-17 ms, except the rolling-WAU series at ~46 ms). Concurrent load was deliberately small
(6 clients × 8 requests; ~33-47 req/s, no errors) to avoid hammering a shared free service - this is **not** a capacity test.
A new TCP+TLS connection per request costs ~150-300 ms (observed with `curl`). The first request after a fresh deploy took **1.9 s** (cold start: import
+ database connection); behaviour after a long idle period was not separately measured.

**Demo run on the live service** - `POST /demo/run` executes the real pipeline inside the function: 1.1-1.5 s end to end, including the database writes.
Rate-limit behaviour (verified live): a burst of 5 requests → 1 allowed, 4 × `429` with `Retry-After` in ~280 ms each.

**Web** - same method as section 4 (headless Chromium, cold cache, median of 5), but over the real network to the deployed site:

| Page | LCP | FCP | TTFB | CLS | Requests |
|---|---:|---:|---:|---:|---:|
| Overview | 276 ms | 276 ms | 133 ms | 0.0526 | 38 |
| Pipelines | 388 ms | 340 ms | 128 ms | 0.0118 | 29 |
| Data quality | 236 ms | 236 ms | 98 ms | 0.0229 | 30 |
| Analytics | 292 ms | 292 ms | 131 ms | 0.0438 | 34 |
| AI agents | 320 ms | 320 ms | 128 ms | 0.0219 | 32 |
| Catalog | 284 ms | 284 ms | 133 ms | 0.0697 | 35 |
| Catalog model | 260 ms | 260 ms | 104 ms | 0.0248 | 30 |

Layout shift on the live site matches the local container to four decimals, so the CLS fixes carry over to production. LCP is the shell's first paint; the
data (fetched client-side) arrives afterwards, so content is visible later than these numbers. These are not Lighthouse scores, and one client location is not a CDN-wide picture.

## What these numbers do not show
Production data volumes, sustained or high concurrent load, uptime over time, idle cold-start behaviour, several client locations, Databricks/cloud performance or cost, or behaviour on real (non-synthetic) event
distributions.
