"""Measure API latency against a running stack.

    python scripts/benchmark_api.py --base http://localhost:8000 --out docs/benchmarks/api.json

Sequential phase: per endpoint, 10 warm-up requests then N timed requests (client-side wall time, includes localhost HTTP).
Concurrent phase: C clients x R requests against /overview and /metrics/timeseries on the single-worker API.
Results depend on the machine and on the dataset size; the output records both.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ENDPOINTS = {
    "health": "/health",
    "overview": "/api/v1/overview",
    "overview (tier+plan filter)": "/api/v1/overview?tier=pro&plan=pro",
    "timeseries dau": "/api/v1/metrics/timeseries?metric=dau",
    "timeseries wau (rolling window)": "/api/v1/metrics/timeseries?metric=wau",
    "timeseries p95 latency": "/api/v1/metrics/timeseries?metric=p95_latency_ms",
    "funnel": "/api/v1/funnels/activation",
    "retention cohorts": "/api/v1/retention/cohorts",
    "agents summary": "/api/v1/agents/summary",
    "feature adoption": "/api/v1/features/adoption",
    "pipeline runs (page of 25)": "/api/v1/pipelines/runs?limit=25",
    "quality checks": "/api/v1/quality/checks",
    "catalog models": "/api/v1/catalog/models",
}


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))]


def timed(client: httpx.Client, url: str) -> tuple[float, int, int]:
    t = time.perf_counter()
    r = client.get(url)
    return (time.perf_counter() - t) * 1000, r.status_code, len(r.content)


def sequential(base: str, n: int) -> list[dict]:
    out = []
    with httpx.Client(base_url=base, timeout=30) as c:
        for name, path in ENDPOINTS.items():
            for _ in range(10):
                timed(c, path)
            samples, statuses, size = [], set(), 0
            for _ in range(n):
                ms, st, size = timed(c, path)
                samples.append(ms)
                statuses.add(st)
            out.append({"endpoint": name, "path": path, "n": n, "status": sorted(statuses), "response_kb": round(size / 1024, 1),
                        "p50_ms": round(pct(samples, 50), 1), "p95_ms": round(pct(samples, 95), 1), "max_ms": round(max(samples), 1),
                        "mean_ms": round(statistics.mean(samples), 1)})
    return out


def concurrent(base: str, path: str, clients: int, per_client: int) -> dict:
    def worker(_):
        with httpx.Client(base_url=base, timeout=60) as c:
            return [timed(c, path) for _ in range(per_client)]

    t0 = time.perf_counter()
    with ThreadPoolExecutor(clients) as ex:
        results = [r for chunk in ex.map(worker, range(clients)) for r in chunk]
    wall = time.perf_counter() - t0
    lat = [r[0] for r in results]
    errors = sum(1 for r in results if r[1] != 200)
    return {"path": path, "clients": clients, "requests": len(results), "errors": errors, "wall_s": round(wall, 2),
            "throughput_rps": round(len(results) / wall, 1), "p50_ms": round(pct(lat, 50), 1), "p95_ms": round(pct(lat, 95), 1),
            "max_ms": round(max(lat), 1)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--out", type=Path, default=Path("docs/benchmarks/api.json"))
    a = p.parse_args(argv)
    with httpx.Client(base_url=a.base, timeout=30) as c:
        ds = c.get("/api/v1/overview").json()
        counts = {"users": next(k["value"] for k in ds["kpis"] if k["key"] == "registered_users")}
    seq = sequential(a.base, a.n)
    conc = [concurrent(a.base, "/api/v1/overview", 20, 25), concurrent(a.base, "/api/v1/metrics/timeseries?metric=dau", 20, 25)]
    res = {"machine": {"platform": platform.platform(), "python": platform.python_version()}, "dataset": counts, "api_workers": 1,
           "sequential": seq, "concurrent": conc}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(f"{'endpoint':36s} {'p50':>7s} {'p95':>7s} {'max':>7s} {'KB':>6s}")
    for r in seq:
        print(f"{r['endpoint']:36s} {r['p50_ms']:7.1f} {r['p95_ms']:7.1f} {r['max_ms']:7.1f} {r['response_kb']:6.1f}")
    for r in conc:
        print(f"concurrent {r['clients']}x{r['requests'] // r['clients']} {r['path']}: {r['throughput_rps']} req/s, p95 {r['p95_ms']} ms, errors {r['errors']}")


if __name__ == "__main__":
    main()
