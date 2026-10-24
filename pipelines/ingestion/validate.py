"""Event validation. Returns quarantine reason codes; an empty list means accepted.

Reason codes (stable, used by quarantine tables and the quality UI):
  schema_mismatch, missing_user_id, invalid_timestamp, unknown_event_name,
  negative_value, orphaned_account
Duplicate detection is a cross-row concern and lives in the Silver job.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).with_name("event_schema.json")
_VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))

EVENT_NAMES = frozenset(
    {
        "user_signed_up",
        "onboarding_started",
        "onboarding_completed",
        "feature_opened",
        "suggestion_shown",
        "suggestion_accepted",
        "agent_started",
        "agent_completed",
        "agent_failed",
        "subscription_started",
    }
)
AGENT_EVENTS = frozenset({"agent_started", "agent_completed", "agent_failed"})
NON_NEGATIVE_PROPS = ("duration_ms", "retry_count", "input_tokens", "output_tokens", "cost_usd_estimate")

MIN_TS = datetime(2020, 1, 1, tzinfo=UTC)
MAX_FUTURE_SKEW = timedelta(hours=1)


# --------------------------------------------------------------------------------------------------------------------
# Fast path. jsonschema is the source of truth, but walking the schema per event dominated ingest time (~70% in profiling).
# `_fast_valid` is a STRICTER-than-schema check: True means "certainly valid"; False means "ask jsonschema". It must never
# accept something the schema rejects - quality/unit_tests/test_validate_fast_path.py checks this differentially.
_RE_EVENT = re.compile(r"evt_[0-9a-f]{16}")
_RE_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z")
_RE_USER = re.compile(r"usr_\d{6}")
_RE_ACCOUNT = re.compile(r"acc_\d{4}")
_RE_SESSION = re.compile(r"ses_[0-9a-f]{12}")
_RE_FEATURE = re.compile(r"feat_[a-z_]+")
_TOP_KEYS = frozenset({"event_id", "event_name", "event_timestamp", "ingested_at", "user_id", "account_id", "session_id",
                       "feature_id", "schema_version", "properties"})
_REQUIRED = ("event_id", "event_name", "event_timestamp", "ingested_at", "user_id", "session_id", "properties")
_STATUSES = frozenset({"success", "failed", "cancelled"})
_INT_PROPS = ("duration_ms", "retry_count", "input_tokens", "output_tokens")
_STR_PROPS = ("execution_id", "agent_type", "plan", "suggestion_id")


def _str_match(v, rx) -> bool:
    return type(v) is str and rx.fullmatch(v) is not None


def _fast_valid(e: dict) -> bool:
    if type(e) is not dict or not _TOP_KEYS.issuperset(e) or any(k not in e for k in _REQUIRED):
        return False
    if type(e["event_name"]) is not str:
        return False
    if not (_str_match(e["event_id"], _RE_EVENT) and _str_match(e["event_timestamp"], _RE_TS) and _str_match(e["ingested_at"], _RE_TS)
            and _str_match(e["user_id"], _RE_USER) and _str_match(e["session_id"], _RE_SESSION)):
        return False
    acc, feat = e.get("account_id"), e.get("feature_id")
    if acc is not None and not _str_match(acc, _RE_ACCOUNT):
        return False
    if feat is not None and not _str_match(feat, _RE_FEATURE):
        return False
    if "schema_version" in e and not (type(e["schema_version"]) is int and e["schema_version"] == 1):
        return False
    props = e["properties"]
    if type(props) is not dict:
        return False
    for k in _INT_PROPS:
        if k in props and type(props[k]) is not int:
            return False
    for k in _STR_PROPS:
        if k in props and type(props[k]) is not str:
            return False
    if "status" in props and not (type(props["status"]) is str and props["status"] in _STATUSES):  # str first: lists are unhashable
        return False
    if "failure_category" in props and props["failure_category"] is not None and type(props["failure_category"]) is not str:
        return False
    # (explicit guard-clause chain kept for readability: each line mirrors one rule in event_schema.json)
    if "cost_usd_estimate" in props and type(props["cost_usd_estimate"]) not in (int, float):  # noqa: SIM103
        return False
    return True


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_event(
    event: dict[str, Any],
    *,
    known_accounts: set[str] | None = None,
    now: datetime | None = None,
) -> list[str]:
    now = now or datetime.now(UTC)
    reasons: list[str] = []

    if not event.get("user_id"):
        reasons.append("missing_user_id")

    if event.get("event_name") not in EVENT_NAMES:
        reasons.append("unknown_event_name")

    # Timestamp semantics (checked separately from schema pattern so the reason is precise).
    ts, ing = parse_ts(event.get("event_timestamp")), parse_ts(event.get("ingested_at"))
    if ts is None or ing is None or ts < MIN_TS or ts > now + MAX_FUTURE_SKEW:
        reasons.append("invalid_timestamp")

    props = event.get("properties")
    if isinstance(props, dict):
        for key in NON_NEGATIVE_PROPS:
            v = props.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0:
                reasons.append("negative_value")
                break

    acc = event.get("account_id")
    if known_accounts is not None and acc is not None and acc not in known_accounts:
        reasons.append("orphaned_account")

    if _fast_valid(event):  # certainly schema-valid: skip the (slow) jsonschema walk
        return list(dict.fromkeys(reasons))

    # Structural schema errors not already explained by a more specific reason.
    explained = {"user_id": "missing_user_id", "event_timestamp": "invalid_timestamp",
                 "ingested_at": "invalid_timestamp"}
    for err in _VALIDATOR.iter_errors(event):
        field = err.path[0] if err.path else None
        if field in explained and explained[field] in reasons:
            continue
        if field == "account_id" and "orphaned_account" in reasons:
            continue
        if field == "properties" and len(err.path) > 1 and "negative_value" in reasons:
            continue
        reasons.append("schema_mismatch")
        break

    return list(dict.fromkeys(reasons))


def split_events(
    events: Iterable[dict[str, Any]],
    *,
    known_accounts: set[str] | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (accepted, quarantined). Quarantined rows keep the raw payload plus reasons."""
    accepted, quarantined = [], []
    for e in events:
        reasons = validate_event(e, known_accounts=known_accounts, now=now)
        if reasons:
            quarantined.append({"payload": e, "reasons": reasons})
        else:
            accepted.append(e)
    return accepted, quarantined
