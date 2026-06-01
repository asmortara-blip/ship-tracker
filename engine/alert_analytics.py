"""alert_analytics.py — acknowledgment metrics over the alerts SQLite table.

Companion module to ``engine.alert_engine_v2``. Where the v2 engine
detects + persists alerts (writes), this module reads + aggregates
the same SQLite table to answer questions about HOW the alert engine
is being used over time: ack rate, severity breakdown, time-to-ack,
unack'd CRITICALs older than X.

Decision flow
-------------
1. UI or ops dashboard imports ``compute_alert_metrics`` (window in
   days) and renders the resulting ``AlertMetrics`` dataclass.
2. The function runs a small handful of SELECT statements against the
   ``alerts`` table — all read-only, no transactions needed.
3. Every code path swallows exceptions and returns a zeroed
   ``AlertMetrics`` so the UI's analytics tab can never crash a render.

Median time-to-ack
------------------
Requires both ``created_at`` AND ``acknowledged_at`` columns to be
non-empty on a row. Pre-v4 alerts (acked before the schema bump) have
an empty ``acknowledged_at`` and are excluded from the median. When NO
acked rows in the window have a timestamp, the metric is ``None`` —
the UI can render "—" rather than a misleading "0 hours".

Why a separate module
---------------------
``alert_engine_v2`` already carries write-path code (detection,
persistence, acknowledgement) and is imported at app startup. Mixing
read-only analytics in there muddies the surface and pulls in
``statistics`` on every import. Keeping analytics here also means the
UI/CLI can mock the engine module in tests without touching this
module's tests, and vice versa.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger


# ─── Dataclass ─────────────────────────────────────────────────────────────

@dataclass
class AlertMetrics:
    """Aggregated metrics over the ``alerts`` table for a rolling window.

    Three metric breakdowns ride on this shape:

    1. **Totals** — ``total_alerts`` / ``acknowledged_count`` /
       ``unacknowledged_count`` / ``ack_rate`` give the headline view.
       ``ack_rate`` is the ratio acknowledged_count / total_alerts (or
       0 when total is 0 — no divide-by-zero exposure).
    2. **By severity** — ``by_severity`` is a dict keyed by severity
       string (CRITICAL / HIGH / MEDIUM / LOW) with per-severity
       {total, ack_count, ack_rate}. Only severities that appear in
       the window show up here — an empty CRITICAL bucket is absent,
       not present-with-zeros.
    3. **By day** — ``by_day`` is a chronologically-ascending list of
       {date, total, ack_count} dicts, one entry per day that had at
       least one alert in the window. Useful for sparkline rendering.

    ``median_time_to_ack_hours`` is computed only over rows where both
    ``created_at`` and ``acknowledged_at`` are non-empty. Pre-v4 acked
    rows have an empty ``acknowledged_at`` and are excluded. When no
    acked rows in the window have a timestamp, the value is ``None``.
    """
    total_alerts: int = 0
    acknowledged_count: int = 0
    unacknowledged_count: int = 0
    ack_rate: float = 0.0
    by_severity: dict[str, dict[str, Any]] = field(default_factory=dict)
    median_time_to_ack_hours: Optional[float] = None
    by_day: list[dict[str, Any]] = field(default_factory=list)


# ─── Internal helpers ──────────────────────────────────────────────────────

def _parse_iso(s: str) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp; tolerate trailing 'Z' shorthand.

    Returns None on any parse failure (empty string, malformed, None).
    Callers use this to skip rows without crashing the aggregation.
    """
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _empty_metrics() -> AlertMetrics:
    """Return a zeroed AlertMetrics — used on empty DB or any error path."""
    return AlertMetrics()


# ─── Public API ────────────────────────────────────────────────────────────

def compute_alert_metrics(window_days: int = 30) -> AlertMetrics:
    """Aggregate acknowledgment metrics over the last ``window_days`` days.

    Never raises. Returns a zeroed ``AlertMetrics`` on any DB failure
    (missing table, schema drift, connection error) so the UI's
    analytics panel can render unconditionally.

    Parameters
    ----------
    window_days:
        Look-back window in days. Default 30. A non-positive or
        non-coercible value short-circuits to the empty shape — without
        this guard the cutoff would slide into the future and silently
        mask every row in the table.
    """
    try:
        try:
            window_days = int(window_days) if window_days is not None else 30
        except (TypeError, ValueError):
            window_days = 30
        if window_days <= 0:
            return _empty_metrics()

        from state.db import get_connection
        conn = get_connection()

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=window_days)
        ).isoformat()

        # Totals — one round-trip.
        totals_row = conn.execute(
            """
            SELECT
                COUNT(*)                                       AS n,
                COALESCE(SUM(CASE WHEN acknowledged = 1 THEN 1 ELSE 0 END), 0) AS n_ack
            FROM alerts
            WHERE created_at >= ?
            """,
            (cutoff,),
        ).fetchone()

        if totals_row is None:
            return _empty_metrics()

        total = int(totals_row["n"])
        ack_count = int(totals_row["n_ack"])
        unack_count = total - ack_count
        ack_rate = (ack_count / total) if total > 0 else 0.0

        # By severity.
        by_sev_rows = conn.execute(
            """
            SELECT
                severity,
                COUNT(*)                                       AS n,
                COALESCE(SUM(CASE WHEN acknowledged = 1 THEN 1 ELSE 0 END), 0) AS n_ack
            FROM alerts
            WHERE created_at >= ?
            GROUP BY severity
            ORDER BY severity
            """,
            (cutoff,),
        ).fetchall()
        by_severity: dict[str, dict[str, Any]] = {}
        for r in by_sev_rows:
            sev = str(r["severity"])
            sev_total = int(r["n"])
            sev_ack = int(r["n_ack"])
            by_severity[sev] = {
                "total":    sev_total,
                "ack_count": sev_ack,
                "ack_rate": (sev_ack / sev_total) if sev_total > 0 else 0.0,
            }

        # By day — substr(created_at, 1, 10) → ISO YYYY-MM-DD prefix.
        by_day_rows = conn.execute(
            """
            SELECT
                substr(created_at, 1, 10)                      AS day,
                COUNT(*)                                       AS n,
                COALESCE(SUM(CASE WHEN acknowledged = 1 THEN 1 ELSE 0 END), 0) AS n_ack
            FROM alerts
            WHERE created_at >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (cutoff,),
        ).fetchall()
        by_day = [
            {
                "date":      str(r["day"]),
                "total":     int(r["n"]),
                "ack_count": int(r["n_ack"]),
            }
            for r in by_day_rows
        ]

        # Median time-to-ack — fetch (created_at, acknowledged_at) for
        # acked rows where BOTH timestamps are non-empty, parse, compute
        # delta in hours, take the median. Pre-v4 rows have an empty
        # acknowledged_at and are excluded from this metric — we treat
        # them as "ack timestamp not available" rather than imputing
        # a fake value.
        ack_pairs = conn.execute(
            """
            SELECT created_at, acknowledged_at
            FROM alerts
            WHERE created_at >= ?
              AND acknowledged = 1
              AND acknowledged_at IS NOT NULL
              AND acknowledged_at != ''
            """,
            (cutoff,),
        ).fetchall()
        durations_hours: list[float] = []
        for r in ack_pairs:
            t0 = _parse_iso(r["created_at"])
            t1 = _parse_iso(r["acknowledged_at"])
            if t0 is None or t1 is None:
                continue
            delta = (t1 - t0).total_seconds() / 3600.0
            # Negative deltas (created_at after acknowledged_at — corrupted
            # row or clock skew) are kept; medians are robust to outliers
            # and excluding them would hide a real data-quality issue.
            durations_hours.append(delta)

        median_hours: Optional[float] = (
            float(statistics.median(durations_hours))
            if durations_hours
            else None
        )

        return AlertMetrics(
            total_alerts=total,
            acknowledged_count=ack_count,
            unacknowledged_count=unack_count,
            ack_rate=ack_rate,
            by_severity=by_severity,
            median_time_to_ack_hours=median_hours,
            by_day=by_day,
        )
    except Exception as exc:
        logger.debug(f"alert_analytics: compute_alert_metrics failed: {exc}")
        return _empty_metrics()


def get_unacknowledged_critical(window_days: int = 30) -> list:
    """Return unacknowledged CRITICAL alerts within the window.

    The UI surfaces this list in a "you have N unacked critical alerts
    older than X" prompt — these are the alerts most likely to be
    operationally important and most embarrassing to miss.

    Returns ``list[ShippingAlert]``; the engine module's dataclass is
    re-exported here via lazy import to avoid a circular dependency at
    module load time. Never raises; returns ``[]`` on any error.

    Parameters
    ----------
    window_days:
        Look-back window in days. Default 30. Non-positive → ``[]``.
    """
    try:
        try:
            window_days = int(window_days) if window_days is not None else 30
        except (TypeError, ValueError):
            window_days = 30
        if window_days <= 0:
            return []

        # Lazy import — alert_engine_v2 imports state.db, which imports
        # nothing from this module. Importing the dataclass at module
        # load time here would not actually create a cycle today, but
        # the lazy import keeps the analytics module light to import
        # (no pandas pull-through from the engine module).
        from engine.alert_engine_v2 import _row_to_alert
        from state.db import get_connection

        conn = get_connection()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=window_days)
        ).isoformat()

        rows = conn.execute(
            """
            SELECT * FROM alerts
            WHERE severity = 'CRITICAL'
              AND acknowledged = 0
              AND created_at >= ?
            ORDER BY created_at DESC
            """,
            (cutoff,),
        ).fetchall()
        return [_row_to_alert(r) for r in rows]
    except Exception as exc:
        logger.debug(
            f"alert_analytics: get_unacknowledged_critical failed: {exc}"
        )
        return []
