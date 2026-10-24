from datetime import UTC, datetime

from pipelines.generators.generate import DEFECTS, Config, generate
from pipelines.ingestion.validate import EVENT_NAMES, split_events, validate_event

NOW = datetime(2026, 12, 31, tzinfo=UTC)


def run(**kw):
    cfg = Config(users=150, accounts=20, days=30, seed=7, **kw)
    return cfg, generate(cfg)


def test_deterministic():
    _, a = run()
    _, b = run()
    assert a[3] == b[3] and a[0] == b[0]


def test_different_seed_differs():
    _, a = run()
    b = generate(Config(users=150, accounts=20, days=30, seed=8))
    assert a[3] != b[3]


def test_clean_data_fully_valid():
    _, (_, accounts, _, events, truth) = run()
    assert truth == {} and len(events) > 1000
    assert {e["event_name"] for e in events} <= EVENT_NAMES
    known = {a["account_id"] for a in accounts}
    acc, quar = split_events(events, known_accounts=known, now=NOW)
    assert quar == [] and len(acc) == len(events)
    assert len({e["event_id"] for e in events}) == len(events)


def test_late_events_are_valid_but_late():
    _, (_, accounts, _, events, _) = run(late_rate=0.2)
    late = [e for e in events if (datetime.fromisoformat(e["ingested_at"].replace("Z", "+00:00"))
                                   - datetime.fromisoformat(e["event_timestamp"].replace("Z", "+00:00"))).days >= 3]
    assert late
    assert split_events(late, known_accounts={a["account_id"] for a in accounts}, now=NOW)[1] == []


def test_every_defect_type_injected_and_detected():
    _, (_, accounts, _, events, truth) = run(defect_rate=0.05)
    assert set(truth.values()) == set(DEFECTS)
    known = {a["account_id"] for a in accounts}
    expected_reason = {
        "missing_user_id": "missing_user_id",
        "invalid_timestamp": "invalid_timestamp",
        "unknown_event_name": "unknown_event_name",
        "orphaned_account": "orphaned_account",
        "schema_mismatch": "schema_mismatch",
        "negative_value": "negative_value",
    }
    seen_ids: set[str] = set()
    for e in events:
        reasons = validate_event(e, known_accounts=known, now=NOW)
        kind = truth.get(e["event_id"])
        if kind in expected_reason:
            assert expected_reason[kind] in reasons, (kind, reasons, e)
        elif kind is None:
            assert reasons == [], (reasons, e)
        seen_ids.add(e["event_id"])


def test_duplicates_present_with_later_ingestion():
    _, (_, _, _, events, truth) = run(defect_rate=0.05)
    dup_ids = [i for i, k in truth.items() if k == "duplicate_event_id"]
    assert dup_ids
    for i in dup_ids:
        copies = sorted(e["ingested_at"] for e in events if e["event_id"] == i)
        assert len(copies) == 2 and copies[0] < copies[1]


def test_quarantine_keeps_payload_and_reasons():
    _, (_, accounts, _, events, _) = run(defect_rate=0.05)
    acc, quar = split_events(events, known_accounts={a["account_id"] for a in accounts}, now=NOW)
    assert len(acc) + len(quar) == len(events)
    assert all(q["reasons"] and q["payload"] for q in quar)


def test_agent_cost_non_negative_and_status_consistent():
    _, (_, _, _, events, _) = run()
    for e in events:
        p = e["properties"]
        if e["event_name"] == "agent_failed":
            assert p["status"] == "failed" and p["failure_category"]
        if e["event_name"] == "agent_completed":
            assert p["status"] == "success"
        if "cost_usd_estimate" in p:
            assert p["cost_usd_estimate"] > 0 and p["duration_ms"] > 0
