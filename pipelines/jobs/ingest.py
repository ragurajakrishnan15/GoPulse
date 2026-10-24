"""Ingestion: validate raw events, route to Bronze (accepted) or quarantine (rejected). Idempotent per batch."""
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pipelines.ingestion.validate import validate_event


def batch_id_for(batch_dir: Path) -> str:
    """Content-addressed batch id: re-ingesting identical files yields the same id (safe reruns)."""
    h = hashlib.sha256()
    for name in ("events.jsonl", "users.jsonl", "accounts.jsonl", "features.jsonl"):
        f = batch_dir / name
        if f.exists():
            h.update(name.encode())
            h.update(f.read_bytes())
    return f"b_{h.hexdigest()[:12]}"


def _read_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield line


def _load_dimensions(con, batch_dir: Path) -> None:
    """Upsert the dimension snapshot. DuckDB reads the JSONL itself (a Python executemany loop was ~4 s per 1,000 users)."""
    def upsert(file: str, table: str, columns: str, select: str) -> None:
        f = batch_dir / file
        if f.exists():
            con.execute(f"INSERT OR REPLACE INTO {table} SELECT {select} FROM "
                        f"read_json('{f.as_posix()}', format='newline_delimited', columns={columns})")

    upsert("accounts.jsonl", "raw_accounts", "{'account_id': 'VARCHAR', 'tier': 'VARCHAR', 'segment': 'VARCHAR', 'created_at': 'VARCHAR'}",
           "account_id, tier, segment, CAST(rtrim(created_at, 'Z') AS TIMESTAMP)")
    upsert("users.jsonl", "raw_users",
           "{'user_id': 'VARCHAR', 'account_id': 'VARCHAR', 'signup_at': 'VARCHAR', 'plan': 'VARCHAR', 'country': 'VARCHAR'}",
           "user_id, account_id, CAST(rtrim(signup_at, 'Z') AS TIMESTAMP), plan, country")
    upsert("features.jsonl", "raw_features", "{'feature_id': 'VARCHAR', 'name': 'VARCHAR', 'category': 'VARCHAR'}",
           "feature_id, name, category")


def _bulk_insert(con, table: str, rows: list[tuple], cols: list[str], col_types: str, select: str) -> None:
    """Row-at-a-time executemany is ~100x slower than a columnar load; stage rows as NDJSON and COPY them in."""
    if not rows:
        return
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "stage.ndjson"
        with f.open("w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                rec = {c: (v.isoformat(sep=" ") if isinstance(v, datetime) else v) for c, v in zip(cols, r, strict=True)}
                fh.write(json.dumps(rec) + "\n")
        columns = "{" + ", ".join("'%s': '%s'" % tuple(c.strip().split(" ", 1)) for c in col_types.split(", ")) + "}"
        con.execute(f"INSERT INTO {table} SELECT {select} FROM read_json('{f.as_posix()}', format='newline_delimited', columns={columns})")


def ingest_batch(con, batch_dir: Path, batch_id: str, now: datetime | None = None) -> dict:
    """Returns {'input','accepted','quarantined','max_ingested_at'}. Caller owns the transaction."""
    now = now or datetime.now(UTC)
    loaded_at = now.replace(tzinfo=None)
    _load_dimensions(con, batch_dir)
    known_accounts = {r[0] for r in con.execute("SELECT account_id FROM raw_accounts").fetchall()}

    con.execute("DELETE FROM bronze_events WHERE batch_id = ?", [batch_id])
    con.execute("DELETE FROM quarantine_events WHERE batch_id = ?", [batch_id])

    bronze, quarantine, total, max_ing = [], [], 0, ""
    for line_no, line in enumerate(_read_jsonl(batch_dir / "events.jsonl"), start=1):
        total += 1
        try:
            event = json.loads(line)
            reasons = ["schema_mismatch"] if not isinstance(event, dict) else validate_event(
                event, known_accounts=known_accounts, now=now)
        except json.JSONDecodeError:
            event, reasons = None, ["schema_mismatch"]
        if reasons:
            payload = line.strip() if event is None else json.dumps(event, sort_keys=True)
            quarantine.append((batch_id, line_no, json.dumps(payload) if event is None else payload, reasons, loaded_at))
        else:
            bronze.append((batch_id, line_no, event["event_id"], event["ingested_at"], json.dumps(event, sort_keys=True), loaded_at))
            max_ing = max(max_ing, event["ingested_at"])

    _bulk_insert(con, "bronze_events", bronze, ["batch_id", "line_no", "event_id", "ingested_at", "payload", "loaded_at"],
                 "batch_id VARCHAR, line_no BIGINT, event_id VARCHAR, ingested_at VARCHAR, payload VARCHAR, loaded_at VARCHAR",
                 "batch_id, line_no, event_id, ingested_at, CAST(payload AS JSON), CAST(loaded_at AS TIMESTAMP)")
    _bulk_insert(con, "quarantine_events", quarantine, ["batch_id", "line_no", "payload", "reasons", "quarantined_at"],
                 "batch_id VARCHAR, line_no BIGINT, payload VARCHAR, reasons VARCHAR[], quarantined_at VARCHAR",
                 "batch_id, line_no, CAST(payload AS JSON), reasons, CAST(quarantined_at AS TIMESTAMP)")
    return {"input": total, "accepted": len(bronze), "quarantined": len(quarantine), "max_ingested_at": max_ing}
