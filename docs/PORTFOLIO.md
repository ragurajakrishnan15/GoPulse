# Presenting GoPulse

Lead with engineering decisions and evidence, not the UI. Everything below is something this repository can show; where a claim would go
beyond that (deployment, cloud scale, real users), it is deliberately absent.

## Short description (accurate)
> GoPulse is an end-to-end product-analytics platform for a fictional AI assistant, built on synthetic data. It validates and deduplicates raw
> events into a Bronze/Silver/Gold lakehouse (DuckDB locally, with a PySpark/Delta implementation proven equivalent), models them with tested dbt
> marts, orchestrates them with Airflow, serves them through PostgreSQL and FastAPI, and presents activation, retention, agent reliability, cost
> estimates, data quality and pipeline health in an accessible Next.js dashboard. It runs as a containerised stack and has 189 automated tests.

## The five things to emphasise

### 1. Reliability - "how do duplicates, late events, retries and partial failures behave?"
| Claim | Evidence to show |
|---|---|
| Bad records are quarantined with a reason, never dropped silently | Quality page → *Inspect rows*; `quarantine_events`; malformed-JSON test |
| A failed run cannot half-publish | one-transaction run; test: toxic batch → Gold fingerprint unchanged, run recorded as failed |
| Reruns/backfills are idempotent | 14 daily batches incl. late events == one full load, row for row; replay tests |
| Retries only where they can help | real Airflow run: transient error → `try_number=2`; quality-gate failure → 1 try, downstream `upstream_failed` |
| Late events are handled | Silver rebuilds the *event-date* partition a late event belongs to (no fixed lookback) |

### 2. Modelling - "how do raw events become consistent metrics?"
Eleven Gold tables each with stated grain/key, eight dbt marts, and a [metric spec](metrics.md) naming numerator, denominator, window, eligibility and
time zone. Rates are `NULL` (not 0) until a window has elapsed. API results are tested to equal the marts. See [data dictionary](data_dictionary.md) and [lineage](lineage.md).

### 3. Observability - "can a user trace a failed check to affected data and downstream results?"
Yes: failed check → affected-record count → sample rows → run history → [failure demonstration](quality.md) with the metric deltas
(naive vs cleaned vs corrected). Real, demo and seeded runs are labelled differently so history is never overstated.

### 4. Performance and cost - "what did you measure and change?"
Profiled first: ingestion was ~93% of runtime. Added a validation fast path (JSON Schema stays authoritative; fuzz-tested over 30,000 mutations, which
caught a real bug) and bulk dimension loading: **5,000 users / 298,731 events went from 46.6 s to ~10.5-11.7 s (about 4×)** with identical outputs; scaling
is linear to 600,078 events in 23.9 s. The same approach found `/overview` at 63.5 ms (5× a typical endpoint) because it built a whole rolling series to show one number: **now 20.8 ms, and 51 → 108 req/s under 20 concurrent clients**,
with an equivalence test proving the numbers are unchanged. On the front end, layout-shift (CLS) was near the 0.1 limit because skeletons didn't match real content; **sizing them correctly brought every page to ≤ 0.07
(Pipelines 0.099 → 0.012)**. Be upfront about the caveats in [benchmarks.md](benchmarks.md) (one machine, ±25% variance, localhost without throttling, no cloud). **No cost
figure is claimed** because nothing ran in the cloud; agent "cost" in the app is an explicit *estimate* from documented token pricing.

### 5. Product thinking - "how do these metrics help decisions?"
Activation, retention and conversion are cohort-based with complete-window eligibility; funnels show each stage's denominator and carry a
"association, not causation" caveat; agent success/latency/failure categories show where reliability work would pay off; account-level adoption is kept separate from user-level.

## Engineering judgement worth discussing
* **Why DuckDB locally and Spark for the cloud path?** Fast, credential-free iteration - and the Spark implementation must match it row for row, so the cloud path is not an untested promise.
* **Why one transaction and a gate before writes?** So "published" always means "reconciled and checked".
* **Least privilege in practice:** the dashboard database role cannot write (tested); the demo endpoint's role can only insert run history; the demo accepts no commands, SQL, paths or credentials.
* **Tests that can fail:** a dbt test is shown to fail on a corrupted table; the fuzz test found a genuine crash; axe runs in light *and* dark.
* **Things caught by verifying instead of assuming** (good interview stories): a Windows-generated npm lockfile that would have broken Linux CI; the API returning a 500 on a fresh database before the first publish; a Postgres float-sum ordering flake in a test (fixed by rounding like the Gold layer); `sr-only` text inside scroll containers causing mobile horizontal scroll; the missing `Retry-After` CORS exposure.

## Resume bullets (every figure is measured here)
* Built an end-to-end lakehouse + analytics platform on synthetic AI-product events (Python, DuckDB, dbt, PostgreSQL, FastAPI, Next.js/TypeScript) with **189 automated tests**, including a PySpark/Delta implementation verified row-for-row against the local pipeline.
* Cut pipeline runtime ~4× (46.6 s → ~11 s for 299k events) by profiling, adding a fuzz-tested validation fast path and bulk loading, with outputs verified identical; throughput ~25k events/s to 600k events. Cut the slowest API endpoint 3.1× (63.5 → 20.8 ms) and reduced layout shift on every page (worst page 0.099 → 0.070, best 0.099 → 0.012) by measuring first.
* Designed reliability controls - quarantine with reason codes, dedup, transactional runs with a pre-write quality gate, idempotent backfills - and verified in Airflow that transient failures retry while data-quality failures do not.
* Delivered a WCAG A/AA-clean (light and dark) dashboard with explicit metric definitions, stale/empty/error states and least-privilege API roles; containerised the stack and wrote CI workflows (linted; commands run locally).

## What *not* to claim
Live uptime, a deployed URL, Databricks or cloud execution, cloud cost, real-user impact, or that the synthetic analytics are findings.

## Before sharing publicly
* Done: pushed to https://github.com/ragurajakrishnan15/GoPulse; CI is green (the first run found four environment-specific defects - a good story: see [ci.md](ci.md)).
* Dependabot is configured for monthly, grouped, minor/patch-only updates (the first weekly setup flooded the repo with 14 PRs); merge its PRs when CI is green.
* Choose a licence (none is included).
* If you deploy later, follow the gated workflow in [ci.md](ci.md) and add the live link to the README.
