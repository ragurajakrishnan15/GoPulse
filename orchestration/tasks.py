"""Framework-independent task logic for the GoPulse DAG.

Keeping this free of Airflow imports means every behaviour (retry classification, bounded windows, chaos injection, the
end-to-end backfill) is unit-testable without a scheduler. The DAG file only wires these functions together.

Failure taxonomy (README: "Use retries only for recoverable failures"):
  TransientError    infrastructure hiccup (locked file, storage/DB connection) -> safe to retry
  NonRetryableError data or logic problem (quality gate, reconciliation, bad input) -> retrying cannot help
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pipelines.jobs.daily_batches import write_daily_batch
from pipelines.jobs.run import run_pipeline


class TransientError(RuntimeError):
    """Recoverable: the DAG retries with backoff."""


class NonRetryableError(RuntimeError):
    """Not recoverable by retrying: the DAG fails the task immediately and blocks downstream tasks."""


NON_RETRYABLE_PREFIXES = ("QualityGateError", "ReconciliationError", "FileNotFoundError", "ValueError")


@dataclass(frozen=True)
class Config:
    work_dir: Path = Path("data/airflow")
    users: int = 500
    seed: int = 42
    days: int = 60
    defect_rate: float = 0.0
    window_start: date = date(2026, 1, 1)
    window_end: date = date(2026, 3, 8)
    run_dbt: bool = True
    publish_url: str | None = None

    @property
    def db(self) -> Path:
        return self.work_dir / "gopulse.duckdb"

    @classmethod
    def from_env(cls) -> Config:
        e = os.environ.get
        return cls(
            work_dir=Path(e("GOPULSE_WORK_DIR", "data/airflow")),
            users=int(e("GOPULSE_USERS", 500)), seed=int(e("GOPULSE_SEED", 42)), days=int(e("GOPULSE_DAYS", 60)),
            defect_rate=float(e("GOPULSE_DEFECT_RATE", 0.0)),
            window_start=date.fromisoformat(e("GOPULSE_WINDOW_START", "2026-01-01")),
            window_end=date.fromisoformat(e("GOPULSE_WINDOW_END", "2026-03-08")),
            run_dbt=e("GOPULSE_RUN_DBT", "1") == "1",
            publish_url=e("DATABASE_URL_OWNER") or None,
        )


def classify(exc: BaseException) -> type[TransientError | NonRetryableError]:
    """Map an exception (or a recorded error summary) to a retry decision."""
    name = type(exc).__name__
    text = f"{name}: {exc}"
    if isinstance(exc, (TransientError, ConnectionError, TimeoutError, BlockingIOError)):
        return TransientError
    if any(k in text for k in ("IOException", "ConnectionException", "OperationalError", "Could not set lock")):
        return TransientError  # DuckDB file lock / Postgres connectivity
    return NonRetryableError


def maybe_inject_chaos(stage: str) -> None:
    """Test hook: GOPULSE_CHAOS="transient_once:<stage>:<flagfile>" fails the first attempt of a stage, then clears itself."""
    spec = os.environ.get("GOPULSE_CHAOS", "")
    if not spec.startswith("transient_once:"):
        return
    _, target, flag = spec.split(":", 2)
    if target == stage and not Path(flag).exists():
        Path(flag).parent.mkdir(parents=True, exist_ok=True)
        Path(flag).write_text("failed once")
        raise TransientError(f"injected transient failure in {stage} (first attempt only)")


def check_window(ds: str, cfg: Config) -> dict:
    """Bounded backfill guard: refuse dates outside the dataset window rather than silently doing nothing."""
    d = date.fromisoformat(ds)
    if not (cfg.window_start <= d <= cfg.window_end):
        raise NonRetryableError(f"{ds} is outside the allowed window {cfg.window_start}..{cfg.window_end}")
    return {"ds": ds, "window": [str(cfg.window_start), str(cfg.window_end)]}


def prepare_batch(ds: str, cfg: Config) -> dict:
    """Stand-in for 'the upstream system delivered today's files'. Returns the batch location and size."""
    maybe_inject_chaos("prepare_batch")
    out = write_daily_batch(ds, cfg.work_dir / "batches" / ds, cache_dir=cfg.work_dir / "dataset_cache", users=cfg.users,
                            seed=cfg.seed, days=cfg.days, defect_rate=cfg.defect_rate)
    return out


def run_lakehouse(batch_dir: str, cfg: Config) -> dict:
    """ingest -> Silver -> Gold -> reconcile -> quality gate, atomically. Failures are classified for the retry policy."""
    maybe_inject_chaos("run_lakehouse")
    try:
        summary = run_pipeline(batch_dir, cfg.db, cfg.work_dir / "lake")
    except Exception as e:
        raise classify(e)(f"{type(e).__name__}: {e}") from e
    if summary["status"] != "success":
        err = summary.get("error", "unknown error")
        kind = NonRetryableError if err.startswith(NON_RETRYABLE_PREFIXES) else classify(RuntimeError(err))
        raise kind(err)
    return {"run_id": summary["run_id"], "batch_id": summary["batch_id"], **summary["ingest"],
            "duplicates": summary["silver"]["duplicates"], "silver_total": summary["silver"]["silver_total"],
            "quality_failed": summary["quality"]["failed"]}


def run_dbt(cfg: Config) -> dict:
    from pipelines.jobs.dbt_runner import persist_dbt_results, run_dbt_build
    if not cfg.run_dbt:
        return {"skipped": "GOPULSE_RUN_DBT=0"}
    maybe_inject_chaos("run_dbt")
    out = run_dbt_build(cfg.db)
    if out["returncode"] != 0:
        # dbt reports its errors on stdout and Python-level noise on stderr: keep the end of both.
        detail = f"{out['stdout_tail'].strip()[-350:]} | stderr: {out['stderr_tail'].strip()[-150:]}"
        raise NonRetryableError(f"dbt build failed (exit {out['returncode']}): {detail or 'no output captured'}")
    return {"tests_persisted": persist_dbt_results(cfg.db, out["results"]), "nodes": len(out["results"])}


def publish_serving(cfg: Config) -> dict:
    if not cfg.publish_url:
        return {"skipped": "DATABASE_URL_OWNER not set"}
    from pipelines.serving.publish import publish
    try:
        return publish(cfg.db, cfg.publish_url)
    except Exception as e:
        raise classify(e)(f"{type(e).__name__}: {e}") from e
