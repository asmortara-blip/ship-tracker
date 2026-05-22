"""perf_telemetry.py — record + summarize per-tab render durations.

This module is the single source of truth for "which tabs are slow?". Any
Streamlit tab can opt into the telemetry by wrapping its ``render(...)``
body in:

.. code-block:: python

    from engine.perf_telemetry import track_render

    def render(...):
        with track_render("overview"):
            ...

The context manager measures wall-clock duration via ``time.perf_counter``
and records exactly one row in the ``tab_render_events`` SQLite table
(schema v8) on exit. A successful exit writes ``success=1`` with an empty
``error_msg``; an exception writes ``success=0`` with ``error_msg=str(exc)``
and re-raises so the tab's existing error handling still triggers.

Decision flow
-------------
1. Tab opens the ``with track_render(tab_name)`` block.
2. Block records start time, yields control to the wrapped code.
3. On clean exit: ``record_render(tab_name, elapsed_ms, success=True)``.
   On Exception:   ``record_render(tab_name, elapsed_ms, success=False,
   error_msg=str(exc))`` then re-raise.
4. ``record_render`` itself swallows every error — telemetry MUST NEVER
   raise into the caller's hot path.

Why a context manager rather than wrapping every ``render(...)``?
There are ~65 tabs. Decorating each one is invasive and intrusive on
diffs; opt-in via a ``with`` block keeps the surface area to a single
edit per tab and makes the wrapped region explicit at the call site.

Aggregation
-----------
``get_perf_summary(window_hours)`` returns a dict with totals,
success_rate, and a per-tab breakdown (count / median / p95 / error_count)
suitable for direct rendering. ``prune_old_events(retention_days)`` is
the retention pass, run from the worker scheduler alongside the LLM-call
prune.

Why milliseconds, not seconds?
A render that takes 12 ms is interesting; a render that takes 12.000 s
is alarming. Storing as INTEGER ms keeps both readable in the same
column and avoids float-precision drift in aggregation queries.
"""
from __future__ import annotations

import sqlite3
import statistics
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from loguru import logger


# ─── Public API ────────────────────────────────────────────────────────────

def record_render(
    tab_name: str,
    duration_ms: int,
    success: bool = True,
    error_msg: str = "",
) -> None:
    """Persist one render event to the ``tab_render_events`` table.

    Best-effort — any exception (DB disconnect, schema drift, bad input)
    is caught and logged at debug level so a telemetry write failure
    never breaks the calling tab. Callers should ALSO wrap this in
    try/except for belt-and-braces; ``record_render`` itself guarantees
    not to raise.

    Parameters
    ----------
    tab_name:
        Short identifier for the tab — typically the suffix of the
        module name (``"overview"`` for ``ui.tab_overview``). Stored
        verbatim; the by-tab aggregation groups on this column so caller
        conventions flow straight into the summary breakdown.
    duration_ms:
        Wall-clock duration in milliseconds. Stored as INTEGER; non-int
        inputs are coerced via ``int()``. Negative values are clamped to
        0 — a clock skew during the measurement window shouldn't
        produce nonsensical analytics.
    success:
        True iff the wrapped render completed without raising. Stored
        as 1/0 in the INTEGER ``success`` column.
    error_msg:
        Short error string when ``success=False``; empty when
        ``success=True``. The column type is TEXT NOT NULL DEFAULT ''.
    """
    try:
        from state.db import get_connection

        event_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()

        # Coerce duration. Bad input (None / non-numeric / negative) →
        # 0 so the row is still recordable; the aggregation will count
        # it but it won't skew the median upward.
        try:
            d_ms = int(duration_ms)
        except (TypeError, ValueError):
            d_ms = 0
        if d_ms < 0:
            d_ms = 0

        success_int = 1 if success else 0

        conn = get_connection()
        conn.execute(
            """
            INSERT INTO tab_render_events
              (event_id, tab_name, started_at, duration_ms, success, error_msg)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                str(tab_name or ""),
                started_at,
                d_ms,
                success_int,
                str(error_msg or ""),
            ),
        )
    except Exception as exc:
        # Telemetry failure must never propagate. Debug-level is
        # intentional — if every render logs at warn, a hot-path tab
        # floods the log on a transient DB hiccup.
        logger.debug(f"perf_telemetry: record_render failed: {exc}")


@contextmanager
def track_render(tab_name: str) -> Iterator[None]:
    """Context manager that records a single render event on exit.

    Measures wall-clock duration via ``time.perf_counter`` and records
    one row in ``tab_render_events`` regardless of success or failure.

    On clean exit:    success=True,  error_msg=""
    On Exception:     success=False, error_msg=str(exc), then re-raises

    SystemExit / KeyboardInterrupt are NOT caught — those are control-flow
    signals (process termination, Ctrl-C) and the caller usually wants
    them to propagate without an extra telemetry row muddying the trail.
    Only ``Exception`` triggers the failure-path record.

    Usage
    -----
    .. code-block:: python

        def render(...):
            with track_render("overview"):
                # tab body — any exception still raises, but its
                # duration + error message are now in the DB.
                ...

    Parameters
    ----------
    tab_name:
        Short identifier for the tab; passed through to ``record_render``.
    """
    start = time.perf_counter()
    try:
        yield
    except Exception as exc:
        # Failure path — record duration AND error, then re-raise so the
        # tab's existing error handling still fires. We deliberately do
        # NOT catch SystemExit / KeyboardInterrupt: those are not
        # measurable rendering failures, they're process-level signals.
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        record_render(
            tab_name=tab_name,
            duration_ms=elapsed_ms,
            success=False,
            error_msg=str(exc),
        )
        raise
    else:
        # Success path — record duration with success=True. The record
        # call is best-effort and never raises, so a telemetry write
        # failure cannot leak from this context manager.
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        record_render(
            tab_name=tab_name,
            duration_ms=elapsed_ms,
            success=True,
            error_msg="",
        )


def _empty_summary(window_hours: int) -> dict[str, Any]:
    """Return a zeroed summary dict — used when the DB has no rows in window."""
    return {
        "window_hours":   int(window_hours),
        "total_renders":  0,
        "success_rate":   0.0,
        "by_tab":         {},
        "top_slow_tabs":  [],
    }


def get_perf_summary(window_hours: int = 24) -> dict[str, Any]:
    """Aggregate render telemetry over the last ``window_hours`` hours.

    Returns a dict shaped for direct rendering in a tab or CLI:

    .. code-block:: python

        {
            "window_hours":  24,
            "total_renders": 42,
            "success_rate":  0.97,                  # 0.0..1.0
            "by_tab": {
                "overview":  {"count": 30, "median_ms": 18, "p95_ms": 42, "error_count": 0},
                "portfolio": {"count": 12, "median_ms": ...},
                ...
            },
            "top_slow_tabs": [
                # Top 10 by median_ms desc, each entry mirrors the
                # by_tab payload plus the tab_name itself for direct
                # rendering without needing to zip the dict.
                {"tab_name": "rates", "count": 5, "median_ms": 320, ...},
                ...
            ],
        }

    Empty DB → zeroed dict with the same keys. Never raises; on any
    DB failure returns the empty shape and logs at debug.

    Parameters
    ----------
    window_hours:
        Look-back window in hours. Default 24 (rolling daily). Pass 1
        for the last hour, 168 for a weekly view.
    """
    try:
        # Treat None (or anything that doesn't cast cleanly) as the default
        # window. A literal 0 / negative MUST short-circuit to the empty
        # shape — without this check the cutoff would be "now or later",
        # which silently masks any older rows.
        if window_hours is None:
            window_hours = 24
        try:
            window_hours = int(window_hours)
        except (TypeError, ValueError):
            window_hours = 24
        if window_hours <= 0:
            return _empty_summary(window_hours)

        from state.db import get_connection
        conn = get_connection()

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=window_hours)
        ).isoformat()

        # Totals — one round-trip, narrow projection.
        totals_row = conn.execute(
            """
            SELECT
                COUNT(*)            AS n,
                COALESCE(SUM(success), 0) AS sum_success
            FROM tab_render_events
            WHERE started_at >= ?
            """,
            (cutoff,),
        ).fetchone()

        if totals_row is None or int(totals_row["n"]) == 0:
            return _empty_summary(window_hours)

        total_renders = int(totals_row["n"])
        success_rate = float(totals_row["sum_success"]) / total_renders
        # Clamp to [0,1] for downstream safety — values outside the range
        # would surprise the caller; SUM/COUNT can never naturally exceed
        # it, but we round-trip via float so a defensive clamp is cheap.
        if success_rate > 1.0:
            success_rate = 1.0
        elif success_rate < 0.0:
            success_rate = 0.0

        # Per-tab durations + error counts. Pull all rows in one query
        # and bucket in Python — the platform has ~65 tabs and a single
        # day's window will be small (a few thousand rows at the high
        # end), so the bucket-in-Python approach is simpler than per-tab
        # SQL aggregation while remaining fast enough.
        tab_rows = conn.execute(
            """
            SELECT tab_name, duration_ms, success
            FROM tab_render_events
            WHERE started_at >= ?
            """,
            (cutoff,),
        ).fetchall()

        durations_by_tab: dict[str, list[int]] = {}
        errors_by_tab: dict[str, int] = {}
        for r in tab_rows:
            name = str(r["tab_name"])
            durations_by_tab.setdefault(name, []).append(int(r["duration_ms"]))
            if int(r["success"]) == 0:
                errors_by_tab[name] = errors_by_tab.get(name, 0) + 1

        by_tab: dict[str, dict[str, Any]] = {}
        for name, durations in durations_by_tab.items():
            by_tab[name] = {
                "count":       len(durations),
                "median_ms":   _median_int(durations),
                "p95_ms":      _p95_int(durations),
                "error_count": int(errors_by_tab.get(name, 0)),
            }

        # Top 10 slowest by median duration desc. Entries carry the
        # tab_name explicitly so a caller can iterate the list without
        # re-zipping against ``by_tab``.
        top_slow_tabs = sorted(
            (
                {"tab_name": name, **payload}
                for name, payload in by_tab.items()
            ),
            key=lambda entry: entry["median_ms"],
            reverse=True,
        )[:10]

        return {
            "window_hours":  window_hours,
            "total_renders": total_renders,
            "success_rate":  round(success_rate, 6),
            "by_tab":        by_tab,
            "top_slow_tabs": top_slow_tabs,
        }
    except Exception as exc:
        logger.debug(f"perf_telemetry: get_perf_summary failed: {exc}")
        return _empty_summary(window_hours if isinstance(window_hours, int) else 24)


# ─── Aggregation helpers ──────────────────────────────────────────────────

def _median_int(values: list[int]) -> int:
    """Median of an integer list, rounded back to int.

    ``statistics.median`` returns a float for even-length inputs (it
    averages the two middle values); we round to the nearest int to keep
    the column type consistent with ``duration_ms``. An empty list
    short-circuits to 0 — the aggregation should never call this with
    an empty bucket, but the guard keeps us from raising on a malformed
    DB row that left a tab with zero durations.
    """
    if not values:
        return 0
    return int(round(statistics.median(values)))


def _p95_int(values: list[int]) -> int:
    """95th percentile of an integer list, rounded back to int.

    For < 2 samples the percentile is degenerate — we return the only
    value (or 0 for an empty list) rather than raising. The
    ``statistics.quantiles`` API needs at least 2 data points; below
    that we just emit the underlying observation.

    For >= 2 samples we use ``statistics.quantiles(n=100)`` and pick
    index 94 (the 95th percentile boundary). This matches the
    "type-7" linear-interpolation definition that numpy / pandas use,
    so cross-tool comparisons remain consistent.
    """
    if not values:
        return 0
    if len(values) == 1:
        return int(values[0])
    qs = statistics.quantiles(values, n=100)
    # quantiles(n=100) returns 99 cutoffs; index 94 is the 95th percentile.
    return int(round(qs[94]))


# ─── Retention ─────────────────────────────────────────────────────────────

def prune_old_events(retention_days: int = 30) -> int:
    """Delete ``tab_render_events`` rows older than ``retention_days`` days.

    A hard cutoff retention pass: any row whose ``started_at`` is older
    than ``now - retention_days`` is removed. Returns the number of rows
    deleted. Wrapped in a single transaction (``with conn:``) so a
    failure mid-way rolls back rather than leaving a partial prune.

    Best-effort — any exception (DB disconnect, schema drift, bad input)
    is caught and logged at debug level so a retention failure never
    breaks the calling worker. Returns ``0`` on any error.

    Parameters
    ----------
    retention_days:
        Keep rows newer than this many days. Default 30 — render events
        are higher-volume than LLM calls and most operational questions
        only need the recent picture, so the default window is tighter
        than the 90-day llm_telemetry default. A value of ``0`` means
        "delete everything" (cutoff is now). A negative value is treated
        as a no-op and returns ``0`` — protects against an accidental
        nuke from a CLI typo like ``--retention-days -1``.

    Returns
    -------
    int
        Number of rows deleted. ``0`` when nothing matched, when the
        input was a negative no-op, or when an exception was caught.
    """
    try:
        # Coerce to int; bad input → no-op.
        try:
            retention_days = int(retention_days)
        except (TypeError, ValueError):
            logger.debug(
                f"perf_telemetry: prune_old_events got non-int "
                f"retention_days={retention_days!r}, treating as no-op"
            )
            return 0

        # Negative window is explicitly a no-op — guards against a CLI
        # typo deleting the whole table.
        if retention_days < 0:
            logger.debug(
                f"perf_telemetry: prune_old_events retention_days="
                f"{retention_days} < 0, treating as no-op"
            )
            return 0

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()

        from state.db import get_connection
        conn = get_connection()

        # Single transaction — `with conn` commits on clean exit, rolls
        # back on exception. The outer try/except below still catches
        # anything that escapes.
        with conn:
            cur = conn.execute(
                "DELETE FROM tab_render_events WHERE started_at < ?",
                (cutoff,),
            )
            deleted = int(cur.rowcount or 0)
        logger.debug(
            f"perf_telemetry: prune_old_events deleted={deleted} rows "
            f"(retention_days={retention_days}, cutoff={cutoff})"
        )
        return deleted
    except Exception as exc:
        logger.debug(f"perf_telemetry: prune_old_events failed: {exc}")
        return 0
