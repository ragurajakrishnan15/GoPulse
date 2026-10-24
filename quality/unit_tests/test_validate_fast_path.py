"""The ingest fast path must never be MORE permissive than the JSON Schema (and must agree end to end on reasons)."""
import copy
import random
from datetime import UTC, datetime

from pipelines.generators.generate import Config, generate
from pipelines.ingestion.validate import _VALIDATOR, _fast_valid, validate_event

NOW = datetime(2026, 12, 31, tzinfo=UTC)
JUNK = [None, True, False, 0, 1, -1, 1.0, 2.5, "", "x", "usr_000001", "evt_0123456789abcdef", "2026-01-01T00:00:00Z", [], {}, [1], {"a": 1},
        "usr_000001\n", "evt_0123456789abcdef\n", "success", "failed", "cancelled", "nope", 10**12, float("inf")]


def _events(seed=3, defect_rate=0.05):
    return generate(Config(users=120, accounts=15, days=25, seed=seed, defect_rate=defect_rate))[3]


def _walk(obj, path=()):
    yield path
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, (*path, k))


def _set(obj, path, value):
    for k in path[:-1]:
        obj = obj[k]
    obj[path[-1]] = value


def _delete(obj, path):
    for k in path[:-1]:
        obj = obj[k]
    obj.pop(path[-1], None)


def test_fast_path_accepts_the_overwhelming_majority_of_clean_events():
    ev = _events(defect_rate=0.0)
    assert sum(_fast_valid(e) for e in ev) / len(ev) > 0.999


def test_fast_valid_implies_schema_valid_on_real_and_defective_events():
    for e in _events():
        if _fast_valid(e):
            assert _VALIDATOR.is_valid(e), e


def test_fast_valid_implies_schema_valid_under_30000_random_mutations():
    rng = random.Random(1234)
    base = [e for e in _events(defect_rate=0.0)[:400]]
    accepted = rejected = 0
    for i in range(30_000):
        e = copy.deepcopy(rng.choice(base))
        for _ in range(rng.randint(1, 3)):
            paths = [p for p in _walk(e) if p]
            path = rng.choice(paths)
            op = rng.random()
            try:
                if op < 0.55:
                    _set(e, path, rng.choice(JUNK))
                elif op < 0.8:
                    _delete(e, path)
                else:
                    _set(e, (*path[:-1], f"extra_{i}"), rng.choice(JUNK))
            except (TypeError, KeyError, IndexError):
                pass
        fast = _fast_valid(e)
        if fast:
            accepted += 1
            assert _VALIDATOR.is_valid(e), f"fast path accepted a schema-invalid event: {e}"
        else:
            rejected += 1
    assert accepted > 500 and rejected > 5000  # the mutations exercised both outcomes


def test_validate_event_reasons_do_not_depend_on_the_fast_path(monkeypatch):
    """Disable the fast path and compare the full validator's output on every event."""
    ev = _events(seed=8, defect_rate=0.08)
    known = {f"acc_{i:04d}" for i in range(1, 16)}
    with_fast = [validate_event(e, known_accounts=known, now=NOW) for e in ev]
    monkeypatch.setattr("pipelines.ingestion.validate._fast_valid", lambda e: False)
    without = [validate_event(e, known_accounts=known, now=NOW) for e in ev]
    assert with_fast == without
