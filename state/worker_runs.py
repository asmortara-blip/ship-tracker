"""state.worker_runs — per-job execution telemetry for the worker scheduler.

The ``worker/scheduler.py`` module runs ~10 background jobs in one daily cron
pass (briefing, pruning, health pings, source-health alerting, perf-budget
checks, etc). Each job's return value (counts, deleted-row totals, fired-
alert tallies) is logged to stdout and then dropped on the floor — there's
no visible record of "did the bulk-export prune fire last night, and what
did it delete?". Operators today have to grep ``logs/scheduler.log``.

This module is the persistence layer behind the **Worker Health** tab.
Every wrapped job records:

  - ``job_name``        — the function name (e.g. ``run_alert_prune_job``)
  - ``started_at``      — ISO-8601 UTC timestamp at decorator entry
  - ``finished_at``     — ISO-8601 UTC timestamp at decorator exit
  - ``status``          — ``'ok'`` (no exception) or ``'error'`` (raised)
  - ``result_json``     — JSON-serialized return value; coerced to a
                          ``{"_repr": str(result)}`` dict when the value
                          isn't JSON-serializable so persistence is never
                          blocked by an exotic return type
  - ``error_message``   — the exception message when status='error', else
                          ``None``
  - ``duration_seconds`` — wall-clock elapsed seconds (monotonic clock so
                          NTP corrections during the run don't poison the
                          measurement)

Storage
-------
A single ``kv_state`` row at key ``'worker_runs'`` holds a rolling list of
the most recent 200 runs across all jobs (oldest dropped). No schema bump
required — kv_state is the documented zero-migration extension point for
free-form operator metadata.

200 runs comfortably covers ~20 days of a 10-job daily worker, or several
weeks of a quieter cadence — enough for the operator question "what
happened last week?" without unbounded growth. The whole blob is at most
tens of KB even at the cap.

Defensive contract
------------------
Every public helper:

* NEVER raises on caller-side bad input. ``record_run`` returns ``False``
  on a persistence failure; the list-returning helpers degrade to an
  empty list; ``get_last_run`` degrades to ``None``.
* Per-test DB isolation via ``state.db.DB_PATH`` monkeypatching + the
  standard ``state.db.reset_for_tests()`` invocation.
* No dependency on ``streamlit``. This module is imported from the
  scheduler decorator (which runs OUTSIDE the Streamlit process) and
  from the dashboard tab — the same code path serves both.

What this module does NOT do
----------------------------
* No per-job retention policy. The single 200-cap covers every job
  uniformly. If a single job spammed the worker we'd still see at most
  200 of its runs.
* No external alerting. The dashboard tab reads this data; alerting on
  job failures is a separate concern handled (today) by inspecting logs.
* No backfill. We start recording from the first decorated invocation.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger


# Storage key inside the ``kv_state`` table. Single source of truth.
_KV_KEY: str = "worker_runs"

# Rolling cap on persisted runs. Sized to comfortably cover ~20 days of a
# 10-job daily worker (200 / 10 = 20 days) — enough for the operator
# question "what happened last week?" without unbounded growth.
_MAX_RUNS: int = 200


# Canonical list of jobs the worker is known to run. Used by
# :func:`summarize_jobs` so a job that has never executed still shows up
# in the dashboard with ``last_status='NEVER'`` rather than being silently
# omitted (an operator needs to see "this prune never ran" loud and
# clear, not just "no row for that job").
KNOWN_JOBS: tuple[str, ...] = (
    "run_daily_briefing_job",
    "run_telemetry_prune_job",
    "run_perf_prune_job",
    "run_snapshot_prune_job",
    "run_health_ping_job",
    "run_health_prune_job",
    "run_source_health_alert_job",
    "run_perf_budget_check_job",
    "run_bulk_export_prune_job",
    "run_alert_prune_job",
    "run_silence_cleanup_job",
    "run_audit_prune_job",
    "run_report_prune_job",
    "run_operator_digest_job",
    "run_report_scheduler_job",
    "run_alert_escalation_job",
    "run_delivery_retry_job",
)


@dataclass
class WorkerJobRun:
    """One persisted invocation of a worker job."""

    run_id: str
    job_name: str
    started_at: str             # ISO-8601 UTC
    finished_at: str            # ISO-8601 UTC
    status: str                 # 'ok' | 'error'
    result_json: str            # JSON-encoded dict (always parseable)
    error_message: Optional[str]
    duration_seconds: float


# ─── Internal storage helpers ──────────────────────────────────────────────


def _now_iso() -> str:
    """Wall-clock UTC timestamp for record-keeping."""
    return datetime.now(timezone.utc).isoformat()


def _coerce_result_json(result: Any) -> str:
    """Serialize ``result`` to JSON; never raises.

    A non-JSON-serializable result (e.g. a dataclass or a list of
    ``HealthPing`` objects from ``run_health_ping_job``) is wrapped as
    ``{"_repr": str(result), "_unencodable": True}`` so the row is still
    persisted and the dashboard can show something useful instead of
    silently dropping the run.
    """
    if result is None:
        return json.dumps({})
    try:
        return json.dumps(result, default=str)
    except (TypeError, ValueError):
        try:
            return json.dumps({"_repr": str(result), "_unencodable": True})
        except Exception:
            return json.dumps({"_repr": "<unrepresentable>", "_unencodable": True})


def _load_blob() -> list[dict[str, Any]]:
    """Read the rolling run-list from kv_state.

    Returns an empty list when the row is missing, the JSON fails to
    parse, or the underlying DB call fails. NEVER raises — callers expect
    this to always return a list.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?", (_KV_KEY,)
        ).fetchone()
        if row is None:
            return []
        raw = row["value"] if hasattr(row, "keys") else row[0]
        if not raw:
            return []
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []
        # Filter to dict entries only — drops any malformed entries that
        # may have crept in via a hand-edited DB row.
        return [r for r in parsed if isinstance(r, dict)]
    except Exception as exc:
        logger.debug(f"state.worker_runs._load_blob: read failed: {exc}")
        return []


def _save_blob(rows: list[dict[str, Any]]) -> bool:
    """Persist ``rows`` to kv_state. Returns success."""
    try:
        from state.db import get_connection

        payload = json.dumps(rows, default=str)
        now = _now_iso()
        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_KV_KEY, payload, now),
            )
        return True
    except Exception as exc:
        logger.debug(f"state.worker_runs._save_blob: write failed: {exc}")
        return False


def _row_to_run(row: dict[str, Any]) -> Optional[WorkerJobRun]:
    """Convert a raw dict row to a typed WorkerJobRun; None on bad shape."""
    try:
        return WorkerJobRun(
            run_id=str(row.get("run_id", "") or ""),
            job_name=str(row.get("job_name", "") or ""),
            started_at=str(row.get("started_at", "") or ""),
            finished_at=str(row.get("finished_at", "") or ""),
            status=str(row.get("status", "") or ""),
            result_json=str(row.get("result_json", "") or "{}"),
            error_message=row.get("error_message"),
            duration_seconds=float(row.get("duration_seconds", 0.0) or 0.0),
        )
    except (TypeError, ValueError):
        return None


# ─── Public API ────────────────────────────────────────────────────────────


def record_run(
    job_name: str,
    *,
    started_at: str,
    finished_at: str,
    status: str,
    result: Any = None,
    error_message: Optional[str] = None,
) -> bool:
    """Persist one job run. NEVER raises.

    Computes ``duration_seconds`` from the two ISO timestamps when both
    parse cleanly; otherwise stores 0.0 (the dashboard treats 0.0 as
    "unknown duration" and falls back to "—").

    Returns ``True`` on successful persistence, ``False`` on any failure
    (bad input, DB error, etc). The decorator in
    ``worker/scheduler.py`` ignores the return value — recording is
    best-effort and a recording failure must NEVER block the job itself.
    """
    try:
        if not isinstance(job_name, str) or not job_name:
            return False

        # Compute duration from the two ISO timestamps. Bad timestamps
        # degrade to 0.0 instead of failing the record — the rest of
        # the row is still useful even without duration.
        duration = 0.0
        try:
            t0 = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
            duration = max(0.0, (t1 - t0).total_seconds())
        except (TypeError, ValueError):
            duration = 0.0

        status_norm = str(status).lower() if status else "ok"
        if status_norm not in {"ok", "error"}:
            status_norm = "ok"

        row: dict[str, Any] = {
            "run_id": str(uuid.uuid4()),
            "job_name": str(job_name),
            "started_at": str(started_at or ""),
            "finished_at": str(finished_at or ""),
            "status": status_norm,
            "result_json": _coerce_result_json(result),
            "error_message": (str(error_message) if error_message else None),
            "duration_seconds": float(duration),
        }

        rows = _load_blob()
        rows.append(row)
        # Roll the window — keep the most-recent _MAX_RUNS. Sorting by
        # started_at would also work, but list-append ordering is the
        # natural insertion order and the consumer always re-sorts on
        # read anyway.
        if len(rows) > _MAX_RUNS:
            rows = rows[-_MAX_RUNS:]
        return _save_blob(rows)
    except Exception as exc:
        logger.debug(f"state.worker_runs.record_run: failed for {job_name!r}: {exc}")
        return False


def list_recent_runs(
    *, limit: int = 50, job_name: Optional[str] = None
) -> list[WorkerJobRun]:
    """Return the most-recent runs in descending ``started_at`` order.

    Parameters
    ----------
    limit:
        Caps the result count. Clamped to a sensible range [1, _MAX_RUNS].
    job_name:
        When provided, only runs of this job are returned (after sorting
        and limit). ``None`` means "all jobs".

    NEVER raises — degrades to an empty list on any failure.
    """
    try:
        # Clamp the limit to keep callers from accidentally paging the
        # entire blob (and to avoid negative limits silently returning
        # everything via Python's slice semantics).
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 50
        n = max(1, min(n, _MAX_RUNS))

        rows = _load_blob()
        runs: list[WorkerJobRun] = []
        for r in rows:
            wj = _row_to_run(r)
            if wj is None:
                continue
            if job_name is not None and wj.job_name != job_name:
                continue
            runs.append(wj)

        runs.sort(key=lambda x: x.started_at, reverse=True)
        return runs[:n]
    except Exception as exc:
        logger.debug(f"state.worker_runs.list_recent_runs: failed: {exc}")
        return []


def get_last_run(job_name: str) -> Optional[WorkerJobRun]:
    """Return the most-recent run for ``job_name``, or None if never run."""
    try:
        if not isinstance(job_name, str) or not job_name:
            return None
        runs = list_recent_runs(limit=_MAX_RUNS, job_name=job_name)
        return runs[0] if runs else None
    except Exception as exc:
        logger.debug(f"state.worker_runs.get_last_run: failed for {job_name!r}: {exc}")
        return None


def _summarize_result(result_json: str) -> str:
    """Render a one-line summary of the JSON result for the dashboard.

    Reduces a result like ``{"fired": 3, "skipped_cooldown": 2}`` to
    ``"fired=3 · skipped_cooldown=2"``. Non-dict results (lists, ints,
    or the ``_unencodable`` fallback) render their repr or count.
    """
    try:
        parsed = json.loads(result_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return "—"
    if parsed is None:
        return "—"
    if isinstance(parsed, dict):
        if not parsed:
            return "—"
        if parsed.get("_unencodable"):
            return parsed.get("_repr", "<unrepresentable>")[:80]
        # k=v pairs joined with " · " — readable in the dashboard table.
        pieces = [f"{k}={v}" for k, v in parsed.items()]
        return " · ".join(pieces)[:120]
    if isinstance(parsed, list):
        return f"list[{len(parsed)}]"
    return str(parsed)[:80]


def summarize_jobs() -> list[dict]:
    """Return one summary row per known job — the dashboard's top table.

    Each row carries:

      * ``job_name``
      * ``last_status``           — 'ok' | 'error' | 'NEVER'
      * ``last_run_at``           — ISO of the most-recent started_at, or ''
      * ``last_duration``         — float seconds (0.0 when never run)
      * ``last_result_summary``   — pretty one-liner (e.g. 'fired=3 ...')
      * ``runs_in_window``        — count of runs in the last 24h
      * ``success_rate_24h``      — float in [0.0, 1.0]; 1.0 when no runs

    A success_rate of 1.0 with runs_in_window=0 means "no data for the
    window" — the dashboard distinguishes that from real successes.

    Rows are sorted alphabetically by job_name so the dashboard reads
    deterministically across renders. Unknown job names (jobs we never
    listed in ``KNOWN_JOBS`` — e.g. a future addition that was deployed
    before the constant was updated) ARE included so an operator never
    misses a row; they're appended after the known set.

    NEVER raises — degrades to an empty list on any failure.
    """
    try:
        all_runs = list_recent_runs(limit=_MAX_RUNS)

        # Bucket by job name for O(N) lookups below.
        by_job: dict[str, list[WorkerJobRun]] = {}
        for r in all_runs:
            by_job.setdefault(r.job_name, []).append(r)

        # Names: every KNOWN_JOBS entry, plus any unknown names we've
        # actually seen runs of — so the operator never misses a row.
        # Known jobs sorted alphabetically, then any unknowns appended in
        # alphabetical order.
        known_set = set(KNOWN_JOBS)
        unknown_names = sorted(n for n in by_job.keys() if n not in known_set)
        names = sorted(KNOWN_JOBS) + unknown_names

        cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)

        out: list[dict] = []
        for name in names:
            runs = by_job.get(name, [])
            if not runs:
                out.append({
                    "job_name": name,
                    "last_status": "NEVER",
                    "last_run_at": "",
                    "last_duration": 0.0,
                    "last_result_summary": "",
                    "runs_in_window": 0,
                    "success_rate_24h": 1.0,
                })
                continue

            # Most-recent run (runs is already sorted desc by started_at).
            last = runs[0]

            # 24h window stats — count runs in the window AND success
            # rate over those runs. A job that has runs older than 24h
            # but none in-window reports runs_in_window=0 with the same
            # 1.0 "no data" success rate.
            in_window: list[WorkerJobRun] = []
            for r in runs:
                try:
                    ts = datetime.fromisoformat(
                        r.started_at.replace("Z", "+00:00")
                    )
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts >= cutoff_24h:
                        in_window.append(r)
                except (TypeError, ValueError):
                    continue

            if in_window:
                ok_count = sum(1 for r in in_window if r.status == "ok")
                success_rate = ok_count / len(in_window)
            else:
                success_rate = 1.0

            out.append({
                "job_name": name,
                "last_status": last.status,
                "last_run_at": last.started_at,
                "last_duration": last.duration_seconds,
                "last_result_summary": _summarize_result(last.result_json),
                "runs_in_window": len(in_window),
                "success_rate_24h": success_rate,
            })
        return out
    except Exception as exc:
        logger.debug(f"state.worker_runs.summarize_jobs: failed: {exc}")
        return []


def clear_all_runs() -> bool:
    """Drop every persisted run. Used by tests for isolation; safe in prod
    as a manual "reset" (no historical implications — the data is purely
    operational telemetry).

    Returns success.
    """
    return _save_blob([])


__all__ = [
    "WorkerJobRun",
    "KNOWN_JOBS",
    "record_run",
    "list_recent_runs",
    "get_last_run",
    "summarize_jobs",
    "clear_all_runs",
]
