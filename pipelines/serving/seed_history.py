"""Deterministic SEEDED example run history.

The website must distinguish real executions from example history (README). Every row produced here has
source='seeded-example'; real local runs are 'local-real' and bounded demo executions are 'demo-live'.
Numbers are illustrative inventions of this generator, not measurements of any system.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

TASKS = ["ingest", "silver", "gold", "reconcile", "quality"]
SCENARIOS = {  # day offset from the start of the window -> scenario
    9: "gate_blocked",
    17: "transient_retry",
    24: "volume_warning",
}


def seeded_history(as_of: date, days: int = 30, seed: int = 2026):
    """Returns (runs, tasks, quality) lists of dict rows, oldest first, ending the day before `as_of`."""
    rng = random.Random(seed)
    runs, tasks, quality = [], [], []
    start_day = as_of - timedelta(days=days)
    base_in = 29000
    for i in range(days):
        day = start_day + timedelta(days=i)
        started = datetime.combine(day, time(2, 0)) + timedelta(seconds=rng.randint(0, 600))
        scenario = SCENARIOS.get(i, "ok")
        n_in = int(base_in * rng.uniform(0.93, 1.07))
        if scenario == "volume_warning":
            n_in = int(base_in * 0.42)
        quar = int(n_in * rng.uniform(0.004, 0.02))
        dups = int(n_in * rng.uniform(0.001, 0.004))
        retries = 0
        status, err = "success", None
        durations = {"ingest": rng.randint(3800, 5200), "silver": rng.randint(250, 420), "gold": rng.randint(90, 160),
                     "reconcile": rng.randint(1, 4), "quality": rng.randint(30, 80)}
        task_status = dict.fromkeys(TASKS, "success")
        log = [f"[{started:%H:%M:%S}] run started (seeded example)"]
        if scenario == "gate_blocked":
            quar = int(n_in * 0.31)
            status, err = "failed", f"QualityGateError: quarantine_rate_blocking: {quar}/{n_in} records quarantined (31.0%); blocking above 20%"
            task_status.update(quality="failed", ingest="rolled_back", silver="rolled_back", gold="rolled_back", reconcile="rolled_back")
        elif scenario == "transient_retry":
            retries = 2
            durations["ingest"] += 2 * 30000
            log.append("WARN ingest: storage read timeout; retry 1/3 after 30s")
            log.append("WARN ingest: storage read timeout; retry 2/3 after 30s")
        elif scenario == "volume_warning":
            log.append(f"WARN volume_anomaly: accepted {n_in - quar} vs median ~{base_in} (58% deviation)")
        total_ms = sum(durations.values())
        ended = started + timedelta(milliseconds=total_ms)
        accepted = n_in - quar
        silver_total = None if status == "failed" else accepted - dups
        wm = None if status == "failed" else (ended - timedelta(minutes=rng.randint(20, 90))).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        run_id = f"seed_{i + 1:03d}"
        log += [f"[{ended:%H:%M:%S}] ingest: input={n_in} accepted={accepted} quarantined={quar}",
                f"[{ended:%H:%M:%S}] silver: duplicates removed={dups}"]
        log.append(f"[{ended:%H:%M:%S}] run {'FAILED: ' + err if err else 'completed'}")
        runs.append({
            "run_id": run_id, "pipeline": "gopulse_daily", "batch_id": f"seed_batch_{i + 1:03d}", "source": "seeded-example",
            "status": status, "started_at": started, "ended_at": ended, "duration_ms": total_ms, "input_count": n_in,
            "accepted_count": accepted, "quarantined_count": quar, "duplicate_count": dups, "silver_total": silver_total,
            "retry_count": retries, "source_watermark": wm,
            "freshness_lag_hours": None if wm is None else round((ended - datetime.fromisoformat(wm.rstrip("Z"))).total_seconds() / 3600, 2),
            "error_summary": err, "log_excerpt": "\n".join(log)})
        t = started
        for name in TASKS:
            te = t + timedelta(milliseconds=durations[name])
            tasks.append({"run_id": run_id, "task": name, "status": task_status[name], "started_at": t, "ended_at": te,
                          "duration_ms": durations[name], "input_count": n_in if name == "ingest" else accepted,
                          "output_count": accepted if name == "ingest" else silver_total,
                          "retry_count": retries if name == "ingest" else 0,
                          "error_summary": err if task_status[name] == "failed" else None})
            t = te
        checks = [("ingestion", "quarantine_rate", "warn", quar / n_in > 0.02, quar, quar / n_in, 0.02, "quarantined"),
                  ("ingestion", "duplicate_event_ids", "warn", dups > 0, dups, dups / accepted, 0.0, "deduplicated"),
                  ("ingestion", "quarantine_rate_blocking", "error", scenario == "gate_blocked", quar, quar / n_in, 0.2, "blocked_publish"),
                  ("ingestion", "volume_anomaly", "warn", scenario == "volume_warning", 0, None, 0.5, "flagged")]
        for layer, name, sev, failed, n, obs, thr, rem in checks:
            quality.append({"run_id": run_id, "batch_id": f"seed_batch_{i + 1:03d}", "source": "seeded-example",
                            "layer": layer, "check_name": name, "severity": sev, "status": "fail" if failed else "pass",
                            "affected_records": n if failed else 0, "observed": obs, "threshold": thr,
                            "detail": f"seeded example: {name}", "remediation_status": rem if failed else "none",
                            "sample_ref": None, "checked_at": ended})
    return runs, tasks, quality
