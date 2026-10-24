"""Deterministic synthetic data generator for GoPulse.

Usage:
    python -m pipelines.generators.generate --users 500 --seed 42 --out data/raw/batch_001
    python -m pipelines.generators.generate --users 500 --seed 42 --defect-rate 0.02 --out data/raw/dirty

Outputs (JSONL): users, accounts, features, events, plus manifest.json containing counts and the
ground truth of injected defects (event_id -> defect type) used by tests and the quality demo.
"""
from __future__ import annotations

import argparse
import json
import random
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

FEATURES = [
    ("feat_inbox_triage", "Inbox Triage", "email"),
    ("feat_smart_compose", "Smart Compose", "writing"),
    ("feat_auto_reply", "Auto Reply", "writing"),
    ("feat_summarize", "Thread Summarize", "email"),
    ("feat_scheduler", "Meeting Scheduler", "calendar"),
    ("feat_search", "Semantic Search", "search"),
    ("feat_followups", "Follow-up Reminders", "email"),
    ("feat_snippets", "Snippets", "writing"),
]
AGENT_TYPES = ["triage_agent", "compose_agent", "scheduler_agent", "search_agent"]
FAILURE_CATEGORIES = ["timeout", "model_error", "tool_error", "rate_limited", "invalid_input"]
TIERS = [("free", 0.55), ("pro", 0.25), ("team", 0.15), ("enterprise", 0.05)]
SEGMENTS = ["smb", "mid_market", "enterprise"]

# Synthetic pricing assumptions (USD per 1M tokens). Documented in docs/metrics.md; NOT vendor billing.
PRICE_IN_PER_M = 3.0
PRICE_OUT_PER_M = 15.0

DEFECTS = [
    "duplicate_event_id",
    "missing_user_id",
    "invalid_timestamp",
    "unknown_event_name",
    "orphaned_account",
    "schema_mismatch",
    "negative_value",
]


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _weighted(rng: random.Random, pairs):
    r, acc = rng.random(), 0.0
    for v, w in pairs:
        acc += w
        if r <= acc:
            return v
    return pairs[-1][0]


@dataclass
class Config:
    users: int = 500
    accounts: int = 60
    days: int = 60
    seed: int = 42
    start: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    defect_rate: float = 0.0
    late_rate: float = 0.02  # fraction of clean events delivered late (valid, but late)


class Generator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self._ids: set[str] = set()

    # -- ids ---------------------------------------------------------------
    def _event_id(self) -> str:
        while True:
            eid = f"evt_{self.rng.getrandbits(64):016x}"
            if eid not in self._ids:
                self._ids.add(eid)
                return eid

    def _session_id(self) -> str:
        return f"ses_{self.rng.getrandbits(48):012x}"

    # -- dimensions --------------------------------------------------------
    def dimensions(self):
        rng, cfg = self.rng, self.cfg
        accounts = []
        for i in range(1, cfg.accounts + 1):
            tier = _weighted(rng, TIERS)
            accounts.append(
                {
                    "account_id": f"acc_{i:04d}",
                    "tier": tier,
                    "segment": rng.choice(SEGMENTS) if tier != "enterprise" else "enterprise",
                    "created_at": iso(cfg.start - timedelta(days=rng.randint(30, 400))),
                }
            )
        users = []
        for i in range(1, cfg.users + 1):
            signup = cfg.start + timedelta(days=rng.random() * (cfg.days - 1), seconds=rng.randint(0, 86399))
            signup = signup.replace(microsecond=0)
            has_acc = rng.random() < 0.7
            users.append(
                {
                    "user_id": f"usr_{i:06d}",
                    "account_id": rng.choice(accounts)["account_id"] if has_acc else None,
                    "signup_at": iso(signup),
                    "plan": "free",
                    "country": rng.choice(["US", "GB", "DE", "IN", "CA", "BR", "AU"]),
                }
            )
        features = [{"feature_id": f, "name": n, "category": c} for f, n, c in FEATURES]
        return users, accounts, features

    # -- events ------------------------------------------------------------
    def _mk(self, name, user, ts, session=None, feature=None, props=None, late=False):
        rng = self.rng
        delay = timedelta(seconds=rng.uniform(0.2, 5))
        if late:
            delay = timedelta(days=rng.uniform(3.1, 6))
        return {
            "event_id": self._event_id(),
            "event_name": name,
            "event_timestamp": iso(ts),
            "ingested_at": iso(ts + delay),
            "user_id": user["user_id"],
            "account_id": user["account_id"],
            "session_id": session or self._session_id(),
            "feature_id": feature,
            "schema_version": 1,
            "properties": props or {},
        }

    def _agent_events(self, user, ts, session, feature, plan, late):
        rng = self.rng
        exec_id = f"exe_{rng.getrandbits(40):010x}"
        agent = rng.choice(AGENT_TYPES)
        base = {"execution_id": exec_id, "agent_type": agent, "plan": plan}
        out = [self._mk("agent_started", user, ts, session, feature, dict(base), late)]
        failed = rng.random() < (0.12 if plan == "free" else 0.07)
        retries = min(rng.choices([0, 1, 2, 3], [0.8, 0.12, 0.06, 0.02])[0] + (1 if failed else 0), 3)
        dur = int(rng.lognormvariate(7.2, 0.6)) + retries * 800
        tin, tout = rng.randint(300, 4000), rng.randint(50, 1500)
        cost = round((tin * PRICE_IN_PER_M + tout * PRICE_OUT_PER_M) / 1e6 * (1 + retries), 6)
        end = ts + timedelta(milliseconds=dur)
        props = dict(base, duration_ms=dur, retry_count=retries, input_tokens=tin,
                     output_tokens=tout, cost_usd_estimate=cost)
        if failed:
            props.update(status="failed", failure_category=rng.choice(FAILURE_CATEGORIES))
            out.append(self._mk("agent_failed", user, end, session, feature, props, late))
        else:
            props.update(status="success", failure_category=None)
            out.append(self._mk("agent_completed", user, end, session, feature, props, late))
        return out

    def user_events(self, user) -> list[dict[str, Any]]:
        rng, cfg = self.rng, self.cfg
        end = cfg.start + timedelta(days=cfg.days)
        signup = datetime.fromisoformat(user["signup_at"].replace("Z", "+00:00"))
        ev = [self._mk("user_signed_up", user, signup, props={"plan": "free"})]

        t = signup + timedelta(minutes=rng.randint(1, 30))
        activated = False
        if rng.random() < 0.9:
            sid = self._session_id()
            ev.append(self._mk("onboarding_started", user, t, sid))
            if rng.random() < 0.68:
                t += timedelta(minutes=rng.randint(2, 40))
                ev.append(self._mk("onboarding_completed", user, t, sid))
                activated = True

        # Retention: engaged users return with decaying probability.
        p_return = 0.55 if activated else 0.18
        plan = "free"
        for day in range(1, cfg.days):
            ts0 = signup + timedelta(days=day, hours=rng.uniform(-2, 10))
            if ts0 >= end:
                break
            if rng.random() < p_return * (0.97 ** day) + 0.02:
                sid = self._session_id()
                ts = ts0
                for _ in range(rng.randint(1, 4)):
                    feat = rng.choice(FEATURES)[0]
                    ts += timedelta(seconds=rng.randint(5, 300))
                    late = rng.random() < cfg.late_rate
                    ev.append(self._mk("feature_opened", user, ts, sid, feat, late=late))
                    if rng.random() < 0.7:
                        ts += timedelta(seconds=rng.randint(1, 20))
                        sugg = {"suggestion_id": f"sug_{rng.getrandbits(32):08x}"}
                        ev.append(self._mk("suggestion_shown", user, ts, sid, feat, sugg, late))
                        if rng.random() < 0.45:
                            ts += timedelta(seconds=rng.randint(1, 30))
                            ev.append(self._mk("suggestion_accepted", user, ts, sid, feat, sugg, late))
                    if rng.random() < 0.5:
                        ts += timedelta(seconds=rng.randint(1, 10))
                        ev.extend(self._agent_events(user, ts, sid, feat, plan, late))
                        ts = datetime.fromisoformat(ev[-1]["event_timestamp"].replace("Z", "+00:00"))
                if plan == "free" and rng.random() < (0.012 if activated else 0.002):
                    plan = "pro"
                    ev.append(self._mk("subscription_started", user, ts + timedelta(minutes=1), sid,
                                       props={"plan": "pro"}))
        return [e for e in ev if datetime.fromisoformat(e["event_timestamp"].replace("Z", "+00:00")) < end]

    # -- defects -----------------------------------------------------------
    def inject_defects(self, events: list[dict[str, Any]]) -> dict[str, str]:
        """Mutate ~defect_rate of events in place / append duplicates. Returns ground truth map.

        Keys are event_ids of the *dirty* record (duplicates map to their shared id, flagged
        'duplicate_event_id' — the first copy stays clean, the later copy is the defect).
        """
        rng, rate = self.rng, self.cfg.defect_rate
        truth: dict[str, str] = {}
        if rate <= 0:
            return truth
        n = int(len(events) * rate)
        idxs = rng.sample(range(len(events)), n)
        extra = []
        for k, i in enumerate(idxs):
            kind = DEFECTS[k % len(DEFECTS)]
            e = events[i]
            if kind == "duplicate_event_id":
                d = deepcopy(e)
                d["ingested_at"] = iso(datetime.fromisoformat(e["ingested_at"].replace("Z", "+00:00"))
                                       + timedelta(seconds=rng.randint(1, 600)))
                extra.append(d)
                truth.setdefault(e["event_id"], kind)
                continue
            if kind == "missing_user_id":
                e["user_id"] = None
            elif kind == "invalid_timestamp":
                e["event_timestamp"] = rng.choice(["not-a-date", "2026-13-45T99:99:99Z", "1999-01-01T00:00:00.000Z",
                                                   "2099-01-01T00:00:00.000Z"])
            elif kind == "unknown_event_name":
                e["event_name"] = rng.choice(["user_signup", "clicked_button_v2", "AGENT_DONE"])
            elif kind == "orphaned_account":
                e["account_id"] = f"acc_{rng.randint(9000, 9999):04d}"
            elif kind == "schema_mismatch":
                e["properties"] = dict(e["properties"], duration_ms="slow")
                if e["event_name"] not in ("agent_completed", "agent_failed"):
                    e["session_id"] = 12345  # wrong type for non-agent events
            elif kind == "negative_value":
                e["properties"] = dict(e["properties"], duration_ms=-1 * rng.randint(1, 5000))
            truth[e["event_id"]] = kind
        events.extend(extra)
        return truth


def generate(cfg: Config):
    g = Generator(cfg)
    users, accounts, features = g.dimensions()
    events: list[dict[str, Any]] = []
    for u in users:
        events.extend(g.user_events(u))
    events.sort(key=lambda e: (e["ingested_at"], e["event_id"]))
    truth = g.inject_defects(events)
    events.sort(key=lambda e: (e["ingested_at"], e["event_id"]))
    return users, accounts, features, events, truth


def write_jsonl(path: Path, rows) -> int:
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
            n += 1
    return n


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--users", type=int, default=500)
    p.add_argument("--accounts", type=int, default=60)
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--defect-rate", type=float, default=0.0)
    p.add_argument("--out", type=Path, default=Path("data/raw/batch_001"))
    a = p.parse_args(argv)

    cfg = Config(users=a.users, accounts=a.accounts, days=a.days, seed=a.seed, defect_rate=a.defect_rate)
    users, accounts, features, events, truth = generate(cfg)
    a.out.mkdir(parents=True, exist_ok=True)
    counts = {
        "users": write_jsonl(a.out / "users.jsonl", users),
        "accounts": write_jsonl(a.out / "accounts.jsonl", accounts),
        "features": write_jsonl(a.out / "features.jsonl", features),
        "events": write_jsonl(a.out / "events.jsonl", events),
    }
    defect_counts: dict[str, int] = {}
    for k in truth.values():
        defect_counts[k] = defect_counts.get(k, 0) + 1
    manifest = {
        "seed": a.seed, "defect_rate": a.defect_rate, "start": iso(cfg.start), "days": a.days,
        "counts": counts, "injected_defects": defect_counts, "defect_truth": truth,
        "pricing_assumption_usd_per_1m_tokens": {"input": PRICE_IN_PER_M, "output": PRICE_OUT_PER_M},
        "synthetic": True,
    }
    (a.out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(json.dumps({"out": str(a.out), **counts, "injected_defects": defect_counts}, indent=2))


if __name__ == "__main__":
    main()
