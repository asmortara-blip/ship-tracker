"""processing.worker_run_query — query + aggregate worker-run telemetry.

Stable, pure-function wrapper over ``state.worker_runs`` for downstream
consumers (operator telemetry tab, end-to-end worker tick test).

The underlying storage layer (``state.worker_runs``) exposes
``list_recent_runs`` + ``get_last_run`` + ``summarize_jobs``, but the
shape of those helpers is geared toward the existing dashboard table:

  * No date-range filter (``since`` / ``until``)
  * No aggregator that returns a typed value object (``summarize_jobs``
    returns one row per known job, mixing presentation columns with
    bookkeeping)
  * No failure-message histogram

This module adds those gaps with two pure functions:

  * :func:`query_runs` — date-range + job_name filter; returns the same
    ``WorkerJobRun`` records as the storage layer (re-export, not a
    re-shape, so callers stay decoupled from any future field changes).
  * :func:`aggregate_job_stats` — pure aggregator over a list of
    ``WorkerJobRun``: pass-rate, mean duration, last-run timestamp, and
    a failure-message histogram. Pure-function in the strict sense — no
    DB access, no clock reads — so callers can compose it with their
    own filtered slices.

The query layer is intentionally thin: it never touches the rolling
cap, never mutates state, and never raises (mirrors the storage layer's
defensive contract). On any failure ``query_runs`` returns an empty
list and ``aggregate_job_stats`` returns a zeroed ``WorkerJobStats``.

This wrapper does NOT replace ``summarize_jobs`` — that helper carries
dashboard-specific presentation columns (``last_result_summary``,
``last_status='NEVER'`` for never-run jobs). Use ``aggregate_job_stats``
when you need the typed per-job stats; use ``summarize_jobs`` when you
need the dashboard rows.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from state.worker_runs import WorkerJobRun, list_recent_runs


# Mirror the storage cap so a date-range query that should return
# "everything in the window" actually does — without this, the default
# ``list_recent_runs(limit=50)`` would silently truncate.
_QUERY_LIMIT: int = 200


@dataclass
class WorkerJobStats:
    """Aggregated stats for a slice of WorkerJobRun rows.

    Attributes
    ----------
    job_name:
        Empty string when the slice spans multiple jobs (or the
        no-rows case). Set to the single job_name when every row in
        the slice carries the same value.
    total_runs:
        Count of rows in the slice.
    ok_runs:
        Count of rows with status == 'ok'.
    error_runs:
        Count of rows with status == 'error'.
    pass_rate:
        ``ok_runs / total_runs``. 1.0 when there are no runs (the
        "no data" convention from summarize_jobs).
    mean_duration_seconds:
        Average duration_seconds across the slice. 0.0 when empty.
    last_run_at:
        ISO timestamp of the most-recent ``started_at`` in the slice,
        empty string when no rows.
    last_status:
        Status of the most-recent run. Empty string when no rows.
    error_message_histogram:
        Mapping of error_message string -> count. Only populated for
        rows with status == 'error' and a non-empty error_message.
    """

    job_name: str = ""
    total_runs: int = 0
    ok_runs: int = 0
    error_runs: int = 0
    pass_rate: float = 1.0
    mean_duration_seconds: float = 0.0
    last_run_at: str = ""
    last_status: str = ""
    error_message_histogram: dict[str, int] = field(default_factory=dict)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _parse_iso(s: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; return None on bad input.

    Mirrors the parsing in state.worker_runs — accepts trailing 'Z'
    and naive timestamps (treated as UTC).
    """
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _to_datetime(value) -> Optional[datetime]:
    """Coerce ``since`` / ``until`` to a tz-aware UTC datetime.

    Accepts ``datetime`` (naive treated as UTC), ``date``, and ISO
    strings. Returns None on bad input — callers treat that as
    "no filter on this side".
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    # date (but not datetime — datetime is a subclass of date)
    from datetime import date as _date
    if isinstance(value, _date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        return _parse_iso(value)
    return None


# ─── Public API ────────────────────────────────────────────────────────────


def query_runs(
    *,
    job_name: Optional[str] = None,
    since=None,
    until=None,
    limit: int = _QUERY_LIMIT,
) -> list[WorkerJobRun]:
    """Query persisted worker runs with optional filters.

    Parameters
    ----------
    job_name:
        When provided, only runs of this job are returned. Exact match
        (the storage layer's contract).
    since:
        Inclusive lower bound on ``started_at``. Accepts a ``datetime``,
        ``date``, or ISO-8601 string. ``None`` means "no lower bound".
        Bad inputs are silently treated as ``None``.
    until:
        Inclusive upper bound on ``started_at``. Same input types as
        ``since``. ``None`` means "no upper bound".
    limit:
        Caps the result count. Defaults to the storage cap so callers
        get every row in the window. Clamped by the storage layer.

    Returns
    -------
    list[WorkerJobRun]
        Newest-first by ``started_at`` (matches the storage layer's
        ordering contract pinned in
        ``tests/test_worker_run_log_contract.py``).

    Never raises — on any failure returns ``[]``.
    """
    try:
        # Get the raw rows from the storage layer. The storage layer
        # already handles job_name filtering + ordering + limit.
        rows = list_recent_runs(limit=limit, job_name=job_name)

        since_dt = _to_datetime(since)
        until_dt = _to_datetime(until)

        if since_dt is None and until_dt is None:
            return rows

        out: list[WorkerJobRun] = []
        for r in rows:
            ts = _parse_iso(r.started_at)
            if ts is None:
                # Bad timestamps are excluded from any date-range
                # query — they can't be compared. They DO still show
                # up in the unfiltered call so the dashboard can flag
                # them.
                continue
            if since_dt is not None and ts < since_dt:
                continue
            if until_dt is not None and ts > until_dt:
                continue
            out.append(r)
        return out
    except Exception as exc:
        logger.debug(f"processing.worker_run_query.query_runs: failed: {exc}")
        return []


def aggregate_job_stats(rows: list[WorkerJobRun]) -> WorkerJobStats:
    """Aggregate a slice of WorkerJobRun rows into a typed stats object.

    Pure function — no DB access, no clock reads. Callers compose it
    with their own filtered slices (e.g. via :func:`query_runs`).

    Behavioural contract:

      * Empty slice -> :class:`WorkerJobStats` with all zeros and
        ``pass_rate = 1.0`` (the "no data" convention from
        ``summarize_jobs``).
      * Single-job slice -> ``job_name`` is set to that job's name.
      * Multi-job slice -> ``job_name`` is empty (caller is responsible
        for grouping).
      * ``last_run_at`` / ``last_status`` reflect the row with the
        largest ``started_at`` (lexicographic comparison works for
        ISO-8601 strings of the same zone).
      * ``error_message_histogram`` only counts rows with
        ``status == 'error'`` AND a non-empty ``error_message``. A
        status='error' row with a missing message is counted in
        ``error_runs`` but not in the histogram.

    Never raises — on any failure returns a zeroed
    :class:`WorkerJobStats`.
    """
    stats = WorkerJobStats()
    try:
        if not rows:
            return stats

        # job_name aggregation: single value when uniform, empty when mixed.
        names = {r.job_name for r in rows}
        if len(names) == 1:
            stats.job_name = next(iter(names))

        stats.total_runs = len(rows)
        stats.ok_runs = sum(1 for r in rows if r.status == "ok")
        stats.error_runs = sum(1 for r in rows if r.status == "error")

        # pass_rate. Guard against rows with statuses outside {ok, error}
        # — those don't count toward either bucket but are still in
        # total_runs. The dashboard's convention is pass_rate over the
        # ok/error union, so we divide by total_runs (which matches the
        # "everything that ran" denominator the operator expects).
        stats.pass_rate = (
            stats.ok_runs / stats.total_runs if stats.total_runs else 1.0
        )

        # mean_duration_seconds — guard against negative or non-numeric
        # values (the storage layer already coerces to float, but
        # belt-and-braces).
        durations = [
            max(0.0, float(r.duration_seconds))
            for r in rows
            if isinstance(r.duration_seconds, (int, float))
        ]
        stats.mean_duration_seconds = (
            sum(durations) / len(durations) if durations else 0.0
        )

        # last_run_at / last_status — newest by started_at (lexicographic
        # works for ISO strings).
        most_recent = max(rows, key=lambda r: r.started_at or "")
        stats.last_run_at = most_recent.started_at
        stats.last_status = most_recent.status

        # error_message_histogram — count by message string. Only
        # rows with status='error' AND a non-empty message.
        histogram: Counter[str] = Counter()
        for r in rows:
            if r.status == "error" and r.error_message:
                histogram[r.error_message] += 1
        stats.error_message_histogram = dict(histogram)

        return stats
    except Exception as exc:
        logger.debug(
            f"processing.worker_run_query.aggregate_job_stats: failed: {exc}"
        )
        return WorkerJobStats()


__all__ = [
    "WorkerJobStats",
    "query_runs",
    "aggregate_job_stats",
]
