"""Controlled failure demonstration (README: "Failure demonstration").

    python -m pipelines.jobs.failure_demo [--users 500] [--seed 42] [--out data/demo]

Scenario (all synthetic, each on an isolated DuckDB file so nothing touches the main lakehouse):
  truth     clean batch                      -> reference metrics
  dirty     same events, ~3% defects         -> checks warn, bad rows quarantined, duplicates removed (PUBLISHED)
  toxic     same events, ~40% defects        -> quarantine-rate gate fails, run BLOCKED, previous Gold untouched
  recovery  corrected (clean) re-delivery    -> rerun succeeds, metrics converge back to truth

For the dirty batch we also compute NAIVE metrics straight from the raw file (no validation/dedup) to show what
the cleaning steps change downstream.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pipelines.generators.generate import Config, generate, write_jsonl
from pipelines.jobs.lakehouse import connect
from pipelines.jobs.run import run_pipeline

GOLD_FINGERPRINT_TABLES = ["fct_product_events", "fct_agent_interactions", "fct_daily_user_activity", "dim_users"]


def _write(batch_dir: Path, cfg: Config) -> dict:
    users, accounts, features, events, truth = generate(cfg)
    batch_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(batch_dir / "users.jsonl", users)
    write_jsonl(batch_dir / "accounts.jsonl", accounts)
    write_jsonl(batch_dir / "features.jsonl", features)
    write_jsonl(batch_dir / "events.jsonl", events)
    return {"events": len(events), "injected": len(truth)}


def gold_metrics(db: Path) -> dict:
    con = connect(db)
    q = lambda s: con.execute(s).fetchone()
    m = {
        "events": q("SELECT count(*) FROM fct_product_events")[0],
        "user_days_active": q("SELECT count(*) FROM fct_daily_user_activity")[0],
        "agent_runs": q("SELECT count(*) FROM fct_agent_interactions")[0],
        "agent_success_rate": round(q("SELECT avg(is_success::INT) FROM fct_agent_interactions")[0], 4),
        "p95_latency_ms": round(q("SELECT quantile_cont(duration_ms, 0.95) FROM fct_agent_interactions")[0], 1),
        "est_cost_usd": round(q("SELECT sum(cost_usd_estimate) FROM fct_agent_interactions")[0], 4),
        "users_with_activity": q("SELECT count(DISTINCT user_id) FROM fct_daily_user_activity")[0],
    }
    con.close()
    return m


def naive_metrics(events_file: Path) -> dict:
    """What a dashboard would show if it counted the raw delivery without validation or dedup."""
    n = runs = ok = 0
    users, durations, cost = set(), [], 0.0
    for line in events_file.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        n += 1
        users.add(e.get("user_id"))
        if e.get("event_name") in ("agent_completed", "agent_failed"):
            runs += 1
            ok += e["event_name"] == "agent_completed"
            p = e.get("properties", {})
            if isinstance(p.get("duration_ms"), (int, float)):
                durations.append(p["duration_ms"])
            cost += p.get("cost_usd_estimate", 0) or 0
    durations.sort()
    return {
        "events": n,
        "agent_runs": runs,
        "agent_success_rate": round(ok / runs, 4) if runs else None,
        "p95_latency_ms": durations[int(0.95 * (len(durations) - 1))] if durations else None,
        "est_cost_usd": round(cost, 4),
        "distinct_user_ids_incl_null": len(users),
    }


def _fp(db: Path) -> str:
    con = connect(db)
    h = hashlib.sha256()
    for t in GOLD_FINGERPRINT_TABLES:
        h.update(repr(con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall()).encode())
    con.close()
    return h.hexdigest()[:16]


def _checks(db: Path, run_id: str) -> list[dict]:
    con = connect(db)
    rows = con.execute(
        "SELECT layer, check_name, severity, status, affected_records, remediation_status, detail "
        "FROM quality_check_results WHERE run_id = ? AND status = 'fail' ORDER BY severity, affected_records DESC",
        [run_id]).fetchall()
    con.close()
    keys = ["layer", "check", "severity", "status", "affected_records", "remediation", "detail"]
    return [dict(zip(keys, r, strict=True)) for r in rows]


def _delta(a: dict, b: dict) -> dict:
    return {k: (round(b[k] - a[k], 4) if isinstance(a.get(k), (int, float)) and isinstance(b.get(k), (int, float)) else None)
            for k in a if k in b}


def run_demo(out: Path, users: int = 500, seed: int = 42) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.duckdb*"):
        f.unlink()
    base = {"users": users, "seed": seed}
    _write(out / "clean", Config(**base))
    _write(out / "dirty", Config(**base, defect_rate=0.03))
    _write(out / "toxic", Config(**base, defect_rate=0.40))

    truth_db, demo_db = out / "truth.duckdb", out / "demo.duckdb"
    run_pipeline(out / "clean", truth_db, None)
    truth = gold_metrics(truth_db)

    # Step 1: dirty batch is published with warnings
    s1 = run_pipeline(out / "dirty", demo_db, None)
    after_dirty = gold_metrics(demo_db)
    fp_after_dirty = _fp(demo_db)

    # Step 2: toxic batch is blocked
    s2 = run_pipeline(out / "toxic", demo_db, None)
    fp_after_blocked = _fp(demo_db)

    # Step 3: corrected redelivery
    s3 = run_pipeline(out / "clean", demo_db, None)
    recovered = gold_metrics(demo_db)

    report = {
        "synthetic": True,
        "scenario": {"users": users, "seed": seed},
        "reference_metrics_clean": truth,
        "step_1_dirty_batch": {
            "status": s1["status"], "ingest": s1["ingest"], "duplicates_removed": s1["silver"]["duplicates"],
            "failed_checks": _checks(demo_db, s1["run_id"]),
            "naive_metrics_no_cleaning": naive_metrics(out / "dirty" / "events.jsonl"),
            "published_metrics_after_cleaning": after_dirty,
            "delta_vs_clean_reference": _delta(truth, after_dirty),
            "note": "Invalid rows were quarantined (not silently dropped) and duplicates removed; published counts sit "
                    "slightly below the reference because quarantined events are not yet corrected.",
        },
        "step_2_toxic_batch": {
            "status": s2["status"], "error": s2.get("error"), "ingest": s2.get("ingest"),
            "blocking_checks": s2["quality"]["blocking"], "failed_checks": _checks(demo_db, s2["run_id"]),
            "gold_unchanged_after_block": fp_after_blocked == fp_after_dirty,
        },
        "step_3_recovery_rerun": {
            "status": s3["status"], "ingest": s3["ingest"],
            "published_metrics": recovered,
            "delta_vs_clean_reference": _delta(truth, recovered),
            "converged_to_reference": recovered == truth,
        },
    }
    (out / "failure_demo.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--users", type=int, default=500)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, default=Path("data/demo"))
    a = p.parse_args(argv)
    r = run_demo(a.out, a.users, a.seed)
    s1, s2, s3 = r["step_1_dirty_batch"], r["step_2_toxic_batch"], r["step_3_recovery_rerun"]
    print(f"[1] dirty : {s1['status']}  quarantined={s1['ingest']['quarantined']} dups_removed={s1['duplicates_removed']}")
    print(f"    naive events={s1['naive_metrics_no_cleaning']['events']}  cleaned events={s1['published_metrics_after_cleaning']['events']}"
          f"  reference={r['reference_metrics_clean']['events']}")
    print(f"[2] toxic : {s2['status']}  blocking={s2['blocking_checks']}  gold_unchanged={s2['gold_unchanged_after_block']}")
    print(f"[3] rerun : {s3['status']}  converged_to_reference={s3['converged_to_reference']}")
    print(f"report: {a.out / 'failure_demo.json'}")


if __name__ == "__main__":
    main()
