"""Data-quality checks. Run inside the pipeline transaction, after Gold is built and before commit.

Severity
  error -> failing blocks publish (the run rolls back; previous Gold stays live)
  warn  -> recorded and surfaced, run still publishes

Every result carries affected_records, observed vs threshold and a remediation status:
  none | quarantined | deduplicated | flagged | blocked_publish | skipped
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

# Thresholds (documented in docs/quality.md)
QUARANTINE_RATE_WARN = 0.02
QUARANTINE_RATE_ERROR = 0.20
LATE_RATE_WARN = 0.05
VOLUME_DEVIATION_WARN = 0.50
FRESHNESS_HOURS_WARN = 24

QUARANTINE_REASON_CHECKS = {  # reason code -> (check name, human title)
    "missing_user_id": "Events with missing user_id",
    "invalid_timestamp": "Events with invalid timestamps",
    "unknown_event_name": "Events with unknown event names",
    "orphaned_account": "Events referencing unknown accounts",
    "schema_mismatch": "Events failing schema/type validation",
    "negative_value": "Events with negative durations/tokens/costs",
}


class QualityGateError(RuntimeError):
    def __init__(self, failed: list[QualityResult]):
        self.failed = failed
        super().__init__("; ".join(f"{r.check_name}: {r.detail}" for r in failed)[:480])


@dataclass
class QualityResult:
    layer: str
    check_name: str
    severity: str  # error | warn
    status: str  # pass | fail | skipped
    affected_records: int
    observed: float | None
    threshold: float | None
    detail: str
    remediation_status: str = "none"
    sample_ref: str | None = None  # how the UI can fetch failed-row samples (table/filter)


def _one(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]


def _rate(check_name, layer, num, den, threshold, severity, detail_fmt, remediation, sample_ref=None):
    rate = (num / den) if den else 0.0
    failed = rate > threshold
    return QualityResult(layer, check_name, severity, "fail" if failed else "pass", num, round(rate, 6), threshold,
                         detail_fmt.format(num=num, den=den, rate=rate), remediation if failed else "none", sample_ref)


def run_checks(con, batch_id: str, ing: dict, sil: dict, *, reference_time: datetime | None = None) -> list[QualityResult]:
    out: list[QualityResult] = []

    # ---- Ingestion layer ------------------------------------------------------------------------------
    out.append(_rate("quarantine_rate_blocking", "ingestion", ing["quarantined"], ing["input"], QUARANTINE_RATE_ERROR,
                     "error", "{num}/{den} records quarantined ({rate:.1%}); blocking above 20%", "blocked_publish",
                     f"quarantine_events:{batch_id}"))
    out.append(_rate("quarantine_rate", "ingestion", ing["quarantined"], ing["input"], QUARANTINE_RATE_WARN, "warn",
                     "{num}/{den} records quarantined ({rate:.1%})", "quarantined", f"quarantine_events:{batch_id}"))
    counts = dict(con.execute(
        "SELECT r, count(*) FROM (SELECT unnest(reasons) AS r FROM quarantine_events WHERE batch_id = ?) GROUP BY r",
        [batch_id]).fetchall())
    for reason, title in QUARANTINE_REASON_CHECKS.items():
        n = counts.get(reason, 0)
        out.append(QualityResult("ingestion", reason, "warn", "fail" if n else "pass", n,
                                 round(n / ing["input"], 6) if ing["input"] else 0.0, 0,
                                 f"{title}: {n} of {ing['input']} records" + (" (quarantined)" if n else ""),
                                 "quarantined" if n else "none", f"quarantine_events:{batch_id}:{reason}"))
    d = sil["duplicates"]
    out.append(QualityResult("ingestion", "duplicate_event_ids", "warn", "fail" if d else "pass", d,
                             round(d / ing["accepted"], 6) if ing["accepted"] else 0.0, 0,
                             f"{d} duplicate event_ids among {ing['accepted']} accepted records "
                             f"({(d / ing['accepted'] if ing['accepted'] else 0):.2%}); earliest copy kept",
                             "deduplicated" if d else "none", f"bronze_events:{batch_id}:duplicates"))
    prior = [r[0] for r in con.execute(
        "SELECT accepted_count FROM pipeline_runs WHERE status = 'success' AND batch_id <> ? AND accepted_count > 0 "
        "ORDER BY started_at DESC LIMIT 5", [batch_id]).fetchall()]
    if len(prior) >= 2:
        med = sorted(prior)[len(prior) // 2]
        dev = abs(ing["accepted"] - med) / med
        out.append(QualityResult("ingestion", "volume_anomaly", "warn", "fail" if dev > VOLUME_DEVIATION_WARN else "pass",
                                 abs(ing["accepted"] - med), round(dev, 4), VOLUME_DEVIATION_WARN,
                                 f"accepted {ing['accepted']} vs median {med} of last {len(prior)} runs ({dev:.0%} deviation)",
                                 "flagged" if dev > VOLUME_DEVIATION_WARN else "none"))
    else:
        out.append(QualityResult("ingestion", "volume_anomaly", "warn", "skipped", 0, None, VOLUME_DEVIATION_WARN,
                                 f"needs >=2 prior successful runs (have {len(prior)})", "skipped"))

    # ---- Silver layer ---------------------------------------------------------------------------------
    def silver_check(name, sql, title, severity="error", remediation="blocked_publish"):
        n = _one(con, sql)
        out.append(QualityResult("silver", name, severity, "fail" if n else "pass", n, float(n), 0,
                                 f"{title}: {n}", remediation if n else "none"))

    silver_check("silver_event_id_unique", "SELECT count(*) - count(DISTINCT event_id) FROM silver_events",
                 "duplicate event_ids remaining in Silver")
    silver_check("silver_user_id_not_null", "SELECT count(*) FROM silver_events WHERE user_id IS NULL OR user_id = ''",
                 "Silver events with null user_id")
    silver_check("silver_non_negative_values",
                 "SELECT count(*) FROM silver_events WHERE duration_ms < 0 OR retry_count < 0 OR input_tokens < 0 "
                 "OR output_tokens < 0 OR cost_usd_estimate < 0", "Silver rows with negative measures")
    silver_check("silver_user_exists",
                 "SELECT count(*) FROM silver_events e ANTI JOIN raw_users u USING (user_id)",
                 "Silver events whose user is not in dim_users", "warn", "flagged")
    silver_check("silver_feature_exists",
                 "SELECT count(*) FROM silver_events e WHERE feature_id IS NOT NULL AND feature_id NOT IN "
                 "(SELECT feature_id FROM raw_features)", "Silver events with unknown feature_id", "warn", "flagged")
    late = _one(con, "SELECT count(*) FROM silver_events WHERE is_late AND batch_id = ?", [batch_id])
    out.append(_rate("late_arrival_rate", "silver", late, max(sil["kept_from_batch"], 0), LATE_RATE_WARN, "warn",
                     "{num}/{den} events in this batch arrived late ({rate:.1%}); older partitions rebuilt", "flagged"))

    # Stale/missing partitions: gaps in the daily series between first and last activity date.
    gaps = con.execute("""
        SELECT count(*) FROM (SELECT unnest(generate_series(min(activity_date), max(activity_date), INTERVAL 1 DAY))::DATE AS d
                              FROM silver_events) s
        WHERE d NOT IN (SELECT DISTINCT activity_date FROM silver_events)""").fetchone()[0]
    out.append(QualityResult("silver", "missing_partitions", "warn", "fail" if gaps else "pass", gaps, float(gaps), 0,
                             f"{gaps} calendar days without any events between first and last activity date",
                             "flagged" if gaps else "none", "silver_events:partition_gaps"))

    wm = _one(con, "SELECT max(ingested_at) FROM silver_events")
    if reference_time is None or wm is None:
        out.append(QualityResult("silver", "freshness", "warn", "skipped", 0, None, FRESHNESS_HOURS_WARN,
                                 "no reference_time supplied (synthetic data has no live clock)", "skipped"))
    else:
        ref = reference_time.astimezone(UTC).replace(tzinfo=None)
        lag_h = (ref - wm).total_seconds() / 3600
        out.append(QualityResult("silver", "freshness", "warn", "fail" if lag_h > FRESHNESS_HOURS_WARN else "pass", 0,
                                 round(lag_h, 2), FRESHNESS_HOURS_WARN,
                                 f"latest ingested event is {lag_h:.1f}h older than reference time",
                                 "flagged" if lag_h > FRESHNESS_HOURS_WARN else "none"))

    # ---- Gold layer -----------------------------------------------------------------------------------
    n = _one(con, "SELECT (SELECT count(*) FROM silver_events) - (SELECT count(*) FROM fct_product_events)")
    out.append(QualityResult("gold", "gold_reconciles_to_silver", "error", "fail" if n else "pass", abs(n), float(abs(n)), 0,
                             f"silver_events minus fct_product_events = {n}", "blocked_publish" if n else "none"))
    n = _one(con, "SELECT count(*) FROM agg_product_metrics_daily a WHERE dau > (SELECT count(*) FROM dim_users)")
    out.append(QualityResult("gold", "dau_not_above_user_count", "error", "fail" if n else "pass", n, float(n), 0,
                             f"{n} daily rows with DAU above total users", "blocked_publish" if n else "none"))
    n = _one(con, "SELECT count(*) FROM fct_agent_interactions WHERE is_success AND status <> 'success'")
    out.append(QualityResult("gold", "agent_status_consistent", "error", "fail" if n else "pass", n, float(n), 0,
                             f"{n} agent rows where is_success contradicts status", "blocked_publish" if n else "none"))
    return out


def blocking(results: list[QualityResult]) -> list[QualityResult]:
    return [r for r in results if r.severity == "error" and r.status == "fail"]


def persist(con, run_id: str, batch_id: str, results: list[QualityResult], *, blocked: bool) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    for r in results:
        d = asdict(r)
        if blocked and r.status == "fail" and r.severity == "error":
            d["remediation_status"] = "blocked_publish"
        elif blocked and r.status == "fail":
            d["remediation_status"] = "not_published"  # run was rolled back; nothing from it is live
        con.execute(
            "INSERT INTO quality_check_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [run_id, batch_id, d["layer"], d["check_name"], d["severity"], d["status"], d["affected_records"],
             d["observed"], d["threshold"], d["detail"], d["remediation_status"], d["sample_ref"], now])
