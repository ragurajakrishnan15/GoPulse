"""End-to-end smoke test of a RUNNING GoPulse API over HTTP (standard library only, so it runs anywhere).

    python scripts/smoke_api.py http://localhost:8000
    python scripts/smoke_api.py https://<api>.vercel.app --origin https://<web>.vercel.app --demo

--demo additionally runs the real pipeline once (clean scenario) and checks the repeat-request rate limit; leave it off for
production-like probes that must not write run history.
Exit code 0 = every check passed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

FAILS: list[str] = []


def call(base: str, path: str, *, method="GET", body=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Accept": "application/json", **({"Content-Type": "application/json"} if data else {}), **(headers or {})})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status, hdrs = r.read(), r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw, status, hdrs = e.read(), e.code, dict(e.headers)
    ms = (time.perf_counter() - t) * 1000
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = {}
    return status, {k.lower(): v for k, v in hdrs.items()}, parsed, ms


def check(name: str, ok: bool, detail: str = ""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  - ' + detail) if detail and not ok else ''}")
    if not ok:
        FAILS.append(name)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("base")
    p.add_argument("--origin", help="the web origin that must be allowed by CORS")
    p.add_argument("--demo", action="store_true", help="also run the demo workflow and check the rate limit")
    a = p.parse_args(argv)
    base = a.base.rstrip("/")
    print(f"smoke test: {base}")

    st, h, body, ms = call(base, "/health")
    check("health is ok and talks to the database", st == 200 and body.get("database") == "ok", f"{st} {body}")
    check("security headers present", h.get("x-content-type-options") == "nosniff")

    st, _, body, _ = call(base, "/api/v1/features")
    check("features list (database read)", st == 200 and len(body.get("items", [])) == 8, f"{st}")
    check("every response is labelled synthetic", body.get("meta", {}).get("synthetic_data") is True)

    st, _, body, ms = call(base, "/api/v1/overview")
    kpis = {k["key"]: k for k in body.get("kpis", [])}
    check("overview has all 10 KPIs with definitions", st == 200 and len(kpis) == 10 and all(k.get("definition") for k in kpis.values()), f"{st} {len(kpis)}")
    check("activation rate is a probability with numerator <= denominator",
          0 <= (kpis.get("activation_rate", {}).get("value") or -1) <= 1 and kpis["activation_rate"]["numerator"] <= kpis["activation_rate"]["denominator"])
    print(f"        overview answered in {ms:.0f} ms")

    st, _, body, _ = call(base, "/api/v1/metrics/timeseries?metric=dau&tier=pro")
    check("filtered time series", st == 200 and len(body.get("points", [])) > 30, f"{st}")

    st, _, body, _ = call(base, "/api/v1/metrics/timeseries?metric=dau&tier=platinum")
    check("invalid filter is a clear 422", st == 422 and body.get("error", {}).get("code") == 422, f"{st}")

    st, _, body, _ = call(base, "/api/v1/pipelines/runs?limit=5")
    sources = {r["source"] for r in body.get("items", [])}
    check("run history lists real and seeded runs, labelled", st == 200 and "seeded-example" in sources and body["summary"]["seeded_example_runs"] == 30, f"{st} {sources}")

    st, _, body, _ = call(base, "/api/v1/quality/checks")
    check("quality checks for the latest real run", st == 200 and body["summary"]["checks"] >= 20 and body["run"]["source"] == "local-real", f"{st}")

    st, _, body, _ = call(base, "/api/v1/quality/failure-demo")
    check("failure demonstration report", st == 200 and body["report"]["step_3_recovery_rerun"]["converged_to_reference"] is True, f"{st}")

    st, _, body, _ = call(base, "/api/v1/catalog/models/fct_agent_interactions")
    check("catalog model with columns and lineage", st == 200 and body["model"]["columns"] and "silver_events" in body["model"]["upstream"], f"{st}")

    if a.origin:
        st, h, _, _ = call(base, "/api/v1/features", headers={"Origin": a.origin})
        check(f"CORS allows {a.origin}", h.get("access-control-allow-origin") == a.origin, str(h.get("access-control-allow-origin")))
        st, h, _, _ = call(base, "/api/v1/features", headers={"Origin": "https://evil.example"})
        check("CORS does not allow other origins", "access-control-allow-origin" not in h)

    st, _, body, _ = call(base, "/api/v1/demo/run", method="POST", body={"scenario": "clean", "cmd": "whoami"})
    check("demo run rejects unknown/unsafe fields (422)", st == 422, f"{st}")

    if a.demo:
        hdr = {"X-Forwarded-For": f"203.0.113.{int(time.time()) % 200 + 20}"}  # honoured only when the deployment trusts its proxy
        st, _, body, ms = call(base, "/api/v1/demo/run", method="POST", body={"scenario": "light_defects", "seed": 7}, headers=hdr, timeout=120)
        check("demo run executes the real pipeline", st == 201 and body.get("source") == "demo-live" and body.get("status") == "success", f"{st} {body}")
        print(f"        demo run took {ms:.0f} ms")
        st, _, body, _ = call(base, "/api/v1/pipelines/runs?source=demo-live&limit=3")
        check("the demo run appears in history, labelled as a real demo run", st == 200 and body["items"] and body["items"][0]["is_real_execution"] is True, f"{st}")
        st, h, _, _ = call(base, "/api/v1/demo/run", method="POST", body={"scenario": "clean", "seed": 8}, headers=hdr, timeout=120)
        if st == 429:
            check("repeat demo run is rate limited with Retry-After", int(h.get("retry-after", 0)) >= 1)
        else:
            print(f"        (second run returned {st}: this deployment's per-minute limit is above 1; not asserted)")

    print(f"\n{'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' CHECK(S) FAILED: ' + '; '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
