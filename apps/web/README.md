# GoPulse web (Next.js)

Public, read-only analytics site for the GoPulse demo. **All data is synthetic.** Next.js 16 (App Router, Cache Components),
React 19, TypeScript, Tailwind CSS 4, Recharts. It talks to the FastAPI service in `services/api` from the browser.

## Run it
```bash
# from the repo root: Postgres + data + API (see docs/api.md), then:
cd apps/web
npm install
npm run dev            # http://localhost:3000   (API at NEXT_PUBLIC_API_URL, default http://localhost:8000)
```

| Script | What it does |
|---|---|
| `npm run typecheck` / `npm run lint` | `tsc --noEmit`, ESLint |
| `npm test` | 27 Vitest unit tests (formatters, filter parsing, API client, `useApi` state machine, components) |
| `npm run test:e2e` | 54 Playwright tests against the real API + Postgres: every page, axe WCAG A/AA in light **and** dark, phone layout, filters, loading/empty/error/stale states, demo run, keyboard |
| `npm run build` | production build (also type-checks the tests) |

`playwright.config.ts` starts the API (loading the repo's git-ignored `.env`) and the site if they are not already running.

## Views
| Route | Content |
|---|---|
| `/` | Headline KPIs (numerator/denominator + definition on each card) and trend charts |
| `/pipelines` | Run history (paginated, filterable), task details + log excerpt, **Real vs Seeded example** labels, bounded "Run demo pipeline" |
| `/quality` | Check results with affected rows and remediation, failed-row inspector, controlled failure demonstration (before/after metrics) |
| `/analytics` | Activation/conversion funnel (distinct denominators), retention cohorts, DAU/WAU, feature adoption, account adoption |
| `/agents` | Success rate, retry rate, P50/P95, failure categories, per-agent table, **estimated** cost |
| `/catalog`, `/catalog/[name]` | Models with grain, key, columns/types, lineage, tests, refresh info, source links; metric definitions |

## Design decisions
- **Filters live in the URL** (`start`, `end`, `tier`, `segment`, `plan`, `feature`): shareable, reload-safe. Values are validated against the
  same enums as the API before being sent; the API re-validates. Filters that do not apply to a view are hidden, and KPIs show
  "Not filtered by: …" when a filter is not applicable to that metric.
- **One async pattern** (`hooks/useApi` + `AsyncBoundary`): skeleton while loading, error with Retry, empty state, and *stale-while-revalidate*
  (previous data stays visible, dimmed, while a new filter loads or when a refresh fails). Loading is derived from request keys, so
  out-of-order responses are dropped via `AbortController` (tested).
- **Honesty UI**: a persistent "Synthetic demo data / precomputed snapshot" strip with last-refreshed time and a stale warning (> 24 h);
  every chart shows its date range and definition; cost is badged "Estimate"; run history labels each row Real / Seeded example.
- **Accessibility**: skip link, landmarks, labelled controls, `aria-current` nav, `aria-live` regions for status, focusable scroll regions,
  charts exposed as images with a **"View as table"** alternative, colour is never the only cue (text + icons), AA contrast tokens for light/dark,
  `prefers-reduced-motion` respected. Verified with axe in CI-style tests.
- **Cache Components**: pages are static shells; data is fetched client-side under `<Suspense>` boundaries (required by `useSearchParams`).
  Routes stay mounted (hidden) across navigation, so e2e selectors scope to visible elements.

## Known limitations
- Charts use client-side fetching, so the first paint shows skeletons rather than data (no SSR of API data).
- Date inputs follow the browser locale display (values are always ISO/UTC).
- The "plan" filter is the user's *current* plan, not the plan at event time (stated in the UI and API docs).
