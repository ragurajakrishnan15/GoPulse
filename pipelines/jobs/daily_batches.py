"""Split the deterministic synthetic stream into per-day deliveries, so orchestrators can run (and backfill) one day at a time.

A day's batch holds every event whose `ingested_at` falls on that UTC date. Late events therefore arrive in a
LATER day's batch than the one their event time belongs to - exactly the situation Silver's partition rebuild handles.
Dimensions (users/accounts/features) are delivered as a full snapshot with every batch.

    python -m pipelines.jobs.daily_batches --date 2026-01-05 --out data/airflow/batches/2026-01-05
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import date
from pathlib import Path

from pipelines.generators.generate import Config, generate, write_jsonl

DEFAULT_CACHE = Path("data/airflow/dataset_cache")


def dataset_dir(cache_dir: Path, users: int, seed: int, days: int, defect_rate: float) -> Path:
    return cache_dir / f"u{users}_s{seed}_d{days}_x{defect_rate:.3f}"


def ensure_dataset(cache_dir: Path = DEFAULT_CACHE, *, users: int = 500, seed: int = 42, days: int = 60,
                   defect_rate: float = 0.0) -> Path:
    """Generate the full stream once and split it by ingestion date. Idempotent: reuses an existing complete cache."""
    root = dataset_dir(Path(cache_dir), users, seed, days, defect_rate)
    if (root / "_complete").exists():
        return root
    if root.exists():
        shutil.rmtree(root)
    tmp = root.with_name(root.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "by_day").mkdir(parents=True)
    u, a, f, events, _ = generate(Config(users=users, days=days, seed=seed, defect_rate=defect_rate))
    write_jsonl(tmp / "users.jsonl", u)
    write_jsonl(tmp / "accounts.jsonl", a)
    write_jsonl(tmp / "features.jsonl", f)
    by_day: dict[str, list] = {}
    for e in events:
        key = str(e.get("ingested_at", ""))[:10]  # defective rows may lack a usable date; they go to "0000-00-00"
        by_day.setdefault(key if key[:2] == "20" else "0000-00-00", []).append(e)
    for day, rows in by_day.items():
        write_jsonl(tmp / "by_day" / f"{day}.jsonl", rows)
    (tmp / "_complete").write_text(json.dumps({"events": len(events), "days": len(by_day)}))
    tmp.rename(root)
    return root


def write_daily_batch(ds: date | str, out_dir: Path, *, cache_dir: Path = DEFAULT_CACHE, users: int = 500, seed: int = 42,
                      days: int = 60, defect_rate: float = 0.0) -> dict:
    """Materialise the delivery for one UTC date into `out_dir` (the layout run_pipeline expects). Returns counts."""
    ds = str(ds)
    src = ensure_dataset(cache_dir, users=users, seed=seed, days=days, defect_rate=defect_rate)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("users", "accounts", "features"):
        shutil.copyfile(src / f"{name}.jsonl", out_dir / f"{name}.jsonl")
    day_file = src / "by_day" / f"{ds}.jsonl"
    if day_file.exists():
        shutil.copyfile(day_file, out_dir / "events.jsonl")
    else:
        (out_dir / "events.jsonl").write_text("")
    n = sum(1 for _ in (out_dir / "events.jsonl").open(encoding="utf-8"))
    return {"ds": ds, "events": n, "batch_dir": str(out_dir)}


def available_days(cache_dir: Path = DEFAULT_CACHE, **kw) -> list[str]:
    root = ensure_dataset(cache_dir, **kw)
    return sorted(p.stem for p in (root / "by_day").glob("*.jsonl") if p.stem[:2] == "20")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--users", type=int, default=500)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--defect-rate", type=float, default=0.0)
    a = p.parse_args(argv)
    print(json.dumps(write_daily_batch(a.date, a.out, users=a.users, seed=a.seed, defect_rate=a.defect_rate)))


if __name__ == "__main__":
    main()
