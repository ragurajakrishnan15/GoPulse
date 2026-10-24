"""Measure the local pipeline at several data sizes. Reproducible: fixed seeds, results written as JSON.

    python scripts/benchmark_pipeline.py --users 500 2000 5000 --out data/benchmarks/pipeline.json

For each size it generates a synthetic batch, runs ingest -> Silver -> Gold -> reconcile -> quality on a fresh DuckDB file, and records
wall-clock per task (from pipeline_task_runs) plus throughput. Numbers depend on the machine; the output records it.
"""
from __future__ import annotations

import argparse
import json
import platform
import tempfile
import time
from pathlib import Path

import duckdb

from pipelines.generators.generate import Config, generate, write_jsonl
from pipelines.jobs.run import run_pipeline


def machine() -> dict:
    import os
    return {"platform": platform.platform(), "python": platform.python_version(), "cpu_count": os.cpu_count(),
            "duckdb": duckdb.__version__, "processor": platform.processor()}


def bench(users: int, seed: int = 42, defect_rate: float = 0.02) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        t0 = time.perf_counter()
        u, a, f, events, _ = generate(Config(users=users, accounts=max(10, users // 8), days=60, seed=seed, defect_rate=defect_rate))
        gen_s = time.perf_counter() - t0
        batch = root / "batch"
        batch.mkdir()
        t0 = time.perf_counter()
        for name, rows in (("users", u), ("accounts", a), ("features", f), ("events", events)):
            write_jsonl(batch / f"{name}.jsonl", rows)
        write_s = time.perf_counter() - t0
        size_mb = (batch / "events.jsonl").stat().st_size / 1e6

        t0 = time.perf_counter()
        summary = run_pipeline(batch, root / "bench.duckdb", None)
        wall_s = time.perf_counter() - t0
        con = duckdb.connect(str(root / "bench.duckdb"), read_only=True)
        tasks = {r[0]: r[1] for r in con.execute("SELECT task, duration_ms FROM pipeline_task_runs").fetchall()}
        con.close()
    n = len(events)
    return {
        "users": users, "events": n, "events_jsonl_mb": round(size_mb, 1), "status": summary["status"],
        "quarantined": summary["ingest"]["quarantined"], "duplicates_removed": summary["silver"]["duplicates"],
        "generate_s": round(gen_s, 2), "write_jsonl_s": round(write_s, 2), "pipeline_wall_s": round(wall_s, 2),
        "task_ms": tasks, "events_per_s_end_to_end": round(n / wall_s),
        "ingest_share_pct": round(100 * tasks.get("ingest", 0) / 1000 / wall_s, 1),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--users", type=int, nargs="+", default=[500, 2000, 5000])
    p.add_argument("--out", type=Path, default=Path("data/benchmarks/pipeline.json"))
    a = p.parse_args(argv)
    results = []
    for n in a.users:
        r = bench(n)
        results.append(r)
        print(f"{n:>6} users  {r['events']:>8,} events  pipeline {r['pipeline_wall_s']:>7.2f}s  "
              f"({r['events_per_s_end_to_end']:,} ev/s)  ingest {r['task_ms'].get('ingest')} ms  silver {r['task_ms'].get('silver')} ms  "
              f"gold {r['task_ms'].get('gold')} ms  quality {r['task_ms'].get('quality')} ms")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"machine": machine(), "results": results}, indent=2), encoding="utf-8")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
