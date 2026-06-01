"""Backtest harness for the alert engine itself.

Given the historical alerts persisted in the SQLite ``alerts`` table
plus the underlying time-series data (stock_data / freight_data /
macro_data dicts), evaluate each alert against what happened in the
window AFTER it fired. Hit-rate = fraction of alerts where the realized
move had the same sign as the alert's change_pct (i.e. the alert
predicted +5% and the realized was positive).

This is the alert-engine's equivalent of a backtest for trading
signals. Useful for tuning thresholds — if STOCK_MOVE alerts at 5%
are hitting 40% of the time but 8% alerts are hitting 65%, that's
meaningful signal about where to set the bar.

Public surface
--------------
``AlertOutcome``         per-alert evaluation row
``AlertBacktestReport``  aggregate report across an evaluation window
``score_alert``          score one alert in isolation
``backtest_alerts``      pull alerts from SQLite, score each, aggregate

Design notes
------------
- Alert types supported: STOCK_MOVE, BDI_MOVE, RATE_SURGE. Others
  (SIGNAL_FIRE, MACRO, CONGESTION) are SKIPPED with a None outcome
  — counted in n_skipped but not in hit-rate denominators.
- Realized window default: 7 days from the alert's created_at.
- Lookback default: 90 days, but only score alerts where the realized
  window is fully in the past (created_at < now - window_days).
- Sign-based hit: ``(predicted > 0 and realized > 0)`` OR
  ``(predicted < 0 and realized < 0)``. If predicted == 0, the alert
  is skipped (no direction to compare).
- Magnitude ratio: realized / predicted (only when both nonzero).
- Date matching: the time series are indexed by 'date'; we find the
  row at or just before ``created_at`` and the row at or just after
  ``created_at + window_days``. Tolerant of partial coverage —
  insufficient data returns None.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Optional

from loguru import logger


# ─── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class AlertOutcome:
    """Per-alert evaluation against the realized post-fire window."""
    alert_id: str
    alert_type: str
    severity: str
    created_at: str
    predicted_change_pct: float
    realized_change_pct: float
    realized_window_days: int
    hit: bool
    magnitude_ratio: float  # realized / predicted; 0.0 when either side is 0


@dataclass
class AlertBacktestReport:
    """Aggregate report across all evaluated alerts in the window."""
    n_alerts_evaluated: int
    n_skipped: int
    hit_rate: float                                 # 0.0 when n_evaluated == 0
    by_alert_type: dict[str, dict[str, float]] = field(default_factory=dict)
    by_severity: dict[str, dict[str, float]] = field(default_factory=dict)
    avg_predicted_pct: float = 0.0
    avg_realized_pct: float = 0.0
    median_magnitude_ratio: float = 0.0


# ─── Internal helpers ──────────────────────────────────────────────────────

def _parse_iso(s: str) -> Optional[datetime]:
    """Parse an ISO timestamp; tolerate trailing-Z and tz-naive."""
    if not s:
        return None
    try:
        # Replace trailing Z so fromisoformat (3.9+) parses cleanly
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _series_for_stock(stock_data: dict, ticker: str):
    """Return the ticker's DataFrame, or None if absent/empty."""
    if not ticker or not isinstance(stock_data, dict):
        return None
    df = stock_data.get(ticker)
    if df is None or getattr(df, "empty", True):
        return None
    return df


def _bdi_series(macro_data: dict):
    """Return the BDI DataFrame. Tries the canonical BSXRLM first, then
    legacy aliases (matches the alert_engine_v2._bdi_series resolution)."""
    if not isinstance(macro_data, dict):
        return None
    for key in ("BSXRLM", "BDIY", "BDI", "bdi"):
        df = macro_data.get(key)
        if df is not None and not getattr(df, "empty", True):
            return df
    return None


def _route_series(freight_data: dict, route_id: str):
    if not route_id or not isinstance(freight_data, dict):
        return None
    df = freight_data.get(route_id)
    if df is None or getattr(df, "empty", True):
        return None
    return df


def _value_at(df, date_col: str, value_col: str, target_dt: datetime) -> Optional[float]:
    """Pull the value column at-or-just-before target_dt. Returns None
    if no row in the frame is <= target_dt."""
    try:
        import pandas as pd
        # Normalize dates to comparable datetimes
        dates = pd.to_datetime(df[date_col], errors="coerce")
        mask = dates <= pd.Timestamp(target_dt)
        eligible = df[mask]
        if eligible.empty:
            return None
        latest = eligible.iloc[-1]
        v = latest[value_col]
        return float(v) if v is not None else None
    except Exception:
        return None


def _value_after(df, date_col: str, value_col: str, target_dt: datetime) -> Optional[float]:
    """Pull the value column at-or-just-after target_dt. Returns None
    if no row in the frame is >= target_dt (i.e. the window extends past
    the available data)."""
    try:
        import pandas as pd
        dates = pd.to_datetime(df[date_col], errors="coerce")
        mask = dates >= pd.Timestamp(target_dt)
        eligible = df[mask]
        if eligible.empty:
            return None
        first = eligible.iloc[0]
        v = first[value_col]
        return float(v) if v is not None else None
    except Exception:
        return None


def _hit(predicted: float, realized: float) -> bool:
    """Sign-based hit: predicted and realized must share a sign. Zero
    on either side → not a hit (no direction to compare)."""
    if predicted == 0 or realized == 0:
        return False
    return (predicted > 0) == (realized > 0)


def _realized_change(
    df,
    date_col: str,
    value_col: str,
    created_at: datetime,
    window_days: int,
) -> Optional[float]:
    """Compute the % change between the value at created_at (or the
    closest prior row) and the value at created_at + window_days (or
    the closest subsequent row). Returns None when either side is
    unavailable or the base value is zero."""
    base = _value_at(df, date_col, value_col, created_at)
    if base is None or base == 0:
        return None
    future = _value_after(df, date_col, value_col, created_at + timedelta(days=window_days))
    if future is None:
        return None
    return (future - base) / base * 100.0


# ─── Per-alert scoring ─────────────────────────────────────────────────────

def score_alert(
    alert,
    stock_data: dict,
    freight_data: dict,
    macro_data: dict,
    *,
    window_days: int = 7,
) -> Optional[AlertOutcome]:
    """Score one alert against its post-fire window. Returns the
    AlertOutcome on success, or None when:

      - The alert_type is one we don't backtest (SIGNAL_FIRE, MACRO,
        CONGESTION, etc.)
      - The underlying data series for this alert is missing
      - The realized window extends past the end of the available data
      - predicted_change_pct (alert.change_pct) is zero — no direction
        to compare against

    Never raises — any exception in the data lookup or arithmetic
    returns None.
    """
    try:
        alert_type = str(getattr(alert, "alert_type", "")).upper()
        predicted = float(getattr(alert, "change_pct", 0.0) or 0.0)
        if predicted == 0:
            return None

        created_at = _parse_iso(str(getattr(alert, "created_at", "")))
        if created_at is None:
            return None

        # Dispatch on alert_type → which series + which value column
        if alert_type == "STOCK_MOVE":
            ticker = str(getattr(alert, "ticker", "") or "")
            df = _series_for_stock(stock_data, ticker)
            if df is None:
                return None
            value_col = "close"
        elif alert_type == "BDI_MOVE":
            df = _bdi_series(macro_data)
            if df is None:
                return None
            value_col = "value"
        elif alert_type == "RATE_SURGE":
            route_id = str(getattr(alert, "route_id", "") or "")
            df = _route_series(freight_data, route_id)
            if df is None:
                return None
            value_col = "rate_usd_per_feu"
        else:
            return None  # SIGNAL_FIRE / MACRO / CONGESTION / unknown — skip

        if "date" not in getattr(df, "columns", []):
            return None
        if value_col not in df.columns:
            return None

        realized = _realized_change(df, "date", value_col, created_at, window_days)
        if realized is None:
            return None

        # Magnitude ratio: realized / predicted when both non-zero.
        # Predicted zero already handled above; realized zero → ratio 0.
        magnitude_ratio = (realized / predicted) if realized != 0 else 0.0

        return AlertOutcome(
            alert_id=str(getattr(alert, "alert_id", "") or ""),
            alert_type=alert_type,
            severity=str(getattr(alert, "severity", "") or ""),
            created_at=str(getattr(alert, "created_at", "") or ""),
            predicted_change_pct=round(predicted, 4),
            realized_change_pct=round(realized, 4),
            realized_window_days=window_days,
            hit=_hit(predicted, realized),
            magnitude_ratio=round(magnitude_ratio, 4),
        )
    except Exception as exc:
        logger.debug(f"alert_backtest.score_alert: scoring failed for {getattr(alert, 'alert_id', '?')}: {exc}")
        return None


# ─── Aggregate report ──────────────────────────────────────────────────────

def _empty_report() -> AlertBacktestReport:
    return AlertBacktestReport(
        n_alerts_evaluated=0,
        n_skipped=0,
        hit_rate=0.0,
        by_alert_type={},
        by_severity={},
        avg_predicted_pct=0.0,
        avg_realized_pct=0.0,
        median_magnitude_ratio=0.0,
    )


def backtest_alerts(
    stock_data: dict,
    freight_data: dict,
    macro_data: dict,
    *,
    window_days: int = 7,
    lookback_days: int = 90,
) -> AlertBacktestReport:
    """Pull alerts from SQLite where the realized window is fully in
    the past, score each, and aggregate.

    Caller supplies stock_data / freight_data / macro_data dicts (same
    shape used everywhere else in the codebase). Empty dicts are valid
    — every alert in those categories will just be skipped.

    Window math:
      - lookback_days: how far back to consider firing alerts
      - window_days: how far forward to measure realized move
      - eligible = alerts where created_at >= (now - lookback_days)
                   AND   created_at <= (now - window_days)
        (the second condition ensures the realized window is in the past)
    """
    try:
        from state.db import get_connection

        now = datetime.now(timezone.utc)
        lookback_cutoff = (now - timedelta(days=lookback_days)).isoformat()
        future_cutoff = (now - timedelta(days=window_days)).isoformat()

        conn = get_connection()
        rows = conn.execute(
            """
            SELECT * FROM alerts
            WHERE created_at >= ? AND created_at <= ?
            ORDER BY created_at ASC
            """,
            (lookback_cutoff, future_cutoff),
        ).fetchall()
    except Exception as exc:
        logger.warning(f"backtest_alerts: SQLite read failed: {exc}")
        return _empty_report()

    if not rows:
        return _empty_report()

    # Lazy import — keep this module pure-function at import time.
    from engine.alert_engine_v2 import _row_to_alert

    outcomes: list[AlertOutcome] = []
    n_skipped = 0
    for row in rows:
        alert = _row_to_alert(row)
        outcome = score_alert(
            alert, stock_data, freight_data, macro_data,
            window_days=window_days,
        )
        if outcome is None:
            n_skipped += 1
            continue
        outcomes.append(outcome)

    return _aggregate(outcomes, n_skipped, window_days)


def _aggregate(
    outcomes: list[AlertOutcome],
    n_skipped: int,
    window_days: int,
) -> AlertBacktestReport:
    """Roll up a list of AlertOutcomes into an AlertBacktestReport."""
    if not outcomes:
        out = _empty_report()
        out.n_skipped = n_skipped
        return out

    total = len(outcomes)
    hits = sum(1 for o in outcomes if o.hit)
    hit_rate = hits / total

    # by_alert_type
    by_type: dict[str, dict[str, float]] = {}
    for o in outcomes:
        bucket = by_type.setdefault(o.alert_type, {"count": 0, "hit_count": 0})
        bucket["count"] += 1
        bucket["hit_count"] += int(o.hit)
    for k, b in by_type.items():
        b["hit_rate"] = round(b["hit_count"] / b["count"], 4) if b["count"] else 0.0

    # by_severity
    by_sev: dict[str, dict[str, float]] = {}
    for o in outcomes:
        bucket = by_sev.setdefault(o.severity, {"count": 0, "hit_count": 0})
        bucket["count"] += 1
        bucket["hit_count"] += int(o.hit)
    for k, b in by_sev.items():
        b["hit_rate"] = round(b["hit_count"] / b["count"], 4) if b["count"] else 0.0

    # Aggregate stats
    preds = [o.predicted_change_pct for o in outcomes]
    reals = [o.realized_change_pct for o in outcomes]
    mags = [o.magnitude_ratio for o in outcomes if o.magnitude_ratio != 0]

    return AlertBacktestReport(
        n_alerts_evaluated=total,
        n_skipped=n_skipped,
        hit_rate=round(hit_rate, 4),
        by_alert_type=by_type,
        by_severity=by_sev,
        avg_predicted_pct=round(sum(preds) / total, 4),
        avg_realized_pct=round(sum(reals) / total, 4),
        median_magnitude_ratio=round(median(mags), 4) if mags else 0.0,
    )
