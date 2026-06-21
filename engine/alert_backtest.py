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


@dataclass
class ThresholdScore:
    """One candidate threshold's re-scored performance for an alert_type.

    A candidate threshold ``c`` means "only count fires whose predicted
    move magnitude (|change_pct|) was at least ``c``" — i.e. what the
    hit-rate would have been had the alert bar been raised to ``c``.

    Fields:
        threshold:   The candidate threshold value (a % move magnitude).
        fire_count:  How many historical fires of this alert_type clear the
                     candidate (``|predicted_change_pct| >= threshold``).
        hit_count:   How many of those firing alerts were sign-hits.
        hit_rate:    ``hit_count / fire_count`` (0.0 when fire_count == 0).
    """
    threshold: float
    fire_count: int
    hit_count: int
    hit_rate: float


@dataclass
class ThresholdRecommendation:
    """A recommended threshold for an alert_type, or an honest 'no rec'.

    ``recommended`` is None when no candidate clears the min-fire-count
    floor — we do NOT fabricate a recommendation from too-thin evidence
    (see ``recommend_threshold``). In that case ``reason`` explains why
    and the current_* fields are still populated where computable.

    Fields:
        alert_type:        The alert_type this recommendation is for.
        recommended:       The recommended threshold, or None.
        recommended_hit_rate:   Hit-rate at the recommended threshold.
        recommended_fire_count: Fire-count at the recommended threshold.
        current_threshold: The threshold currently in effect (from the
                           override store), or None when no override exists.
        current_hit_rate:  Hit-rate at the current threshold (the candidate
                           nearest-at-or-below the current threshold), or
                           None when not computable.
        current_fire_count: Fire-count at the current threshold, or None.
        min_fire_count:    The floor that was enforced.
        reason:            Human-readable 'why' (or the insufficient-data
                           explanation when recommended is None).
        candidates:        The full swept grid, for display / audit.
    """
    alert_type: str
    recommended: Optional[float]
    recommended_hit_rate: float = 0.0
    recommended_fire_count: int = 0
    current_threshold: Optional[float] = None
    current_hit_rate: Optional[float] = None
    current_fire_count: Optional[int] = None
    min_fire_count: int = 0
    reason: str = ""
    candidates: list[ThresholdScore] = field(default_factory=list)


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
    outcomes, n_skipped = _collect_outcomes(
        stock_data, freight_data, macro_data,
        window_days=window_days, lookback_days=lookback_days,
    )
    if outcomes is None:
        # _collect_outcomes signals a read failure with None.
        return _empty_report()
    return _aggregate(outcomes, n_skipped, window_days)


def _collect_outcomes(
    stock_data: dict,
    freight_data: dict,
    macro_data: dict,
    *,
    window_days: int = 7,
    lookback_days: int = 90,
) -> tuple[Optional[list[AlertOutcome]], int]:
    """Pull the eligible alerts from SQLite and score each one.

    Returns ``(outcomes, n_skipped)``. ``outcomes`` is ``None`` ONLY when the
    SQLite read itself failed — the caller maps that to an empty report.
    An empty list (no eligible rows) returns ``([], 0)``. Shared by
    ``backtest_alerts`` and ``sweep_thresholds`` so the candidate sweep
    re-uses the EXACT real scoring path (no fabricated hit-rates).
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
        logger.warning(f"alert_backtest: SQLite read failed: {exc}")
        return None, 0

    if not rows:
        return [], 0

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

    return outcomes, n_skipped


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


# ─── Self-tuning loop: sweep → recommend → apply (R043) ─────────────────────

# Default candidate threshold grid (% move magnitude). Used when the caller
# does not pass an explicit grid. Spans the typical alert-bar range — a 1%
# move (almost everything fires) up to a 25% move (only the largest fire).
_DEFAULT_CANDIDATES: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0, 25.0)

# Minimum number of firing alerts a candidate must produce before its
# hit-rate is trusted as a recommendation. A threshold that fires twice and
# hits both times shows a 100% hit-rate that is pure small-sample noise — it
# is NOT evidence the bar belongs there. The floor forces the recommender to
# pick a threshold backed by enough fires that the hit-rate means something.
# Documented + enforced in ``recommend_threshold``; callers may raise it.
_DEFAULT_MIN_FIRE_COUNT: int = 5


def _clean_candidates(candidates) -> list[float]:
    """Coerce a candidate grid to a sorted, de-duplicated list of finite
    positive floats. Garbage entries are dropped, never raised on. Empty /
    None input falls back to the default grid."""
    if candidates is None:
        candidates = _DEFAULT_CANDIDATES
    out: set[float] = set()
    try:
        for c in candidates:
            try:
                v = float(c)
            except (TypeError, ValueError):
                continue
            if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
                continue
            if v <= 0:
                continue
            out.add(round(v, 6))
    except TypeError:
        # candidates is not iterable — fall back to the default grid.
        return list(_DEFAULT_CANDIDATES)
    if not out:
        return list(_DEFAULT_CANDIDATES)
    return sorted(out)


def _score_candidate(
    outcomes: list[AlertOutcome], threshold: float,
) -> ThresholdScore:
    """Re-score a list of already-evaluated outcomes at one candidate bar.

    A fire clears the candidate when ``|predicted_change_pct| >= threshold``
    — i.e. had the alert bar been raised to ``threshold``, that alert would
    still have fired. The hit-rate over the firing subset is the candidate's
    score. This is the REAL scoring (outcomes come from ``score_alert``);
    the candidate only re-filters which fires are counted."""
    firing = [o for o in outcomes if abs(o.predicted_change_pct) >= threshold]
    fire_count = len(firing)
    hit_count = sum(1 for o in firing if o.hit)
    hit_rate = (hit_count / fire_count) if fire_count else 0.0
    return ThresholdScore(
        threshold=round(float(threshold), 6),
        fire_count=fire_count,
        hit_count=hit_count,
        hit_rate=round(hit_rate, 4),
    )


def sweep_thresholds(
    alert_type: str,
    candidates=None,
    *,
    stock_data: Optional[dict] = None,
    freight_data: Optional[dict] = None,
    macro_data: Optional[dict] = None,
    outcomes: Optional[list[AlertOutcome]] = None,
    window_days: int = 7,
    lookback_days: int = 90,
) -> list[ThresholdScore]:
    """Re-score ``alert_type`` across a candidate threshold grid.

    Reuses the real backtest scoring: the alert_type's historical fires are
    scored ONCE (via ``score_alert`` / the shared ``_collect_outcomes``), and
    each candidate threshold simply re-filters which of those fires are
    counted (``|predicted_change_pct| >= candidate``). Per-candidate
    hit-rate + fire-count, never fabricated.

    Pass ``outcomes`` directly (the already-scored list for this alert_type)
    to skip the DB pull — used by ``recommend_threshold`` so the two share
    one scoring pass. Otherwise the data dicts + window/lookback drive a
    fresh ``_collect_outcomes`` call.

    Returns one ``ThresholdScore`` per (cleaned) candidate, sorted by
    threshold ascending. Never raises — a bad grid / read failure yields an
    all-zero-fire-count grid or an empty list.
    """
    grid = _clean_candidates(candidates)
    atype = str(alert_type or "").upper()

    try:
        if outcomes is None:
            collected, _skipped = _collect_outcomes(
                stock_data or {}, freight_data or {}, macro_data or {},
                window_days=window_days, lookback_days=lookback_days,
            )
            collected = collected or []
        else:
            collected = list(outcomes)
        # Keep only this alert_type's outcomes.
        relevant = [o for o in collected if str(o.alert_type).upper() == atype]
    except Exception as exc:
        logger.debug(f"sweep_thresholds: collection failed: {exc}")
        relevant = []

    return [_score_candidate(relevant, c) for c in grid]


def recommend_threshold(
    alert_type: str,
    candidates=None,
    *,
    min_fire_count: int = _DEFAULT_MIN_FIRE_COUNT,
    stock_data: Optional[dict] = None,
    freight_data: Optional[dict] = None,
    macro_data: Optional[dict] = None,
    outcomes: Optional[list[AlertOutcome]] = None,
    window_days: int = 7,
    lookback_days: int = 90,
) -> ThresholdRecommendation:
    """Recommend a threshold for ``alert_type`` from the swept candidate grid.

    Objective
    ---------
    Pick the candidate that MAXIMIZES hit-rate, SUBJECT TO a min-fire-count
    floor. A threshold with a dazzling hit-rate that almost never fires is
    useless — a 100%-hit-rate bar that fired twice is small-sample noise, not
    signal. The floor (``min_fire_count``, default
    ``_DEFAULT_MIN_FIRE_COUNT``) is the minimum number of historical fires a
    candidate must produce before its hit-rate is trusted. Candidates below
    the floor are ineligible.

    Tie-break
    ---------
    Among candidates tied on (rounded) hit-rate above the floor, prefer the
    LOWER threshold — it fires more often, catching more real moves at the
    same accuracy. (A secondary tie on threshold cannot happen: the grid is
    de-duplicated.)

    Honesty
    -------
    When NO candidate clears the floor, ``recommended`` is None and ``reason``
    says so — we never fabricate a recommendation from too-thin evidence.

    The 'why' compares the recommended threshold's hit-rate / fire-count to
    the CURRENT threshold (read from the override store, keyed by alert_type;
    falls back to the candidate at-or-below the current bar for an apples-to-
    apples hit-rate). Never raises.
    """
    atype = str(alert_type or "").upper()
    try:
        min_floor = max(1, int(min_fire_count))
    except (TypeError, ValueError):
        min_floor = _DEFAULT_MIN_FIRE_COUNT

    # Score the alert_type's fires ONCE, then sweep — one scoring pass.
    try:
        if outcomes is None:
            collected, _skipped = _collect_outcomes(
                stock_data or {}, freight_data or {}, macro_data or {},
                window_days=window_days, lookback_days=lookback_days,
            )
            collected = collected or []
        else:
            collected = list(outcomes)
        relevant = [o for o in collected if str(o.alert_type).upper() == atype]
    except Exception as exc:
        logger.debug(f"recommend_threshold: collection failed: {exc}")
        relevant = []

    swept = sweep_thresholds(atype, candidates, outcomes=relevant)

    # Current threshold from the override store (keyed by alert_type).
    current_threshold = _current_threshold_for(atype)
    current_hr, current_fc = _score_at_current(relevant, current_threshold, swept)

    # Eligible = candidates clearing the floor. Maximize hit-rate; tie-break
    # to the lower threshold (swept is already threshold-ascending, so the
    # first max under a stable sort is the lowest-threshold winner).
    eligible = [s for s in swept if s.fire_count >= min_floor]
    if not eligible:
        return ThresholdRecommendation(
            alert_type=atype,
            recommended=None,
            current_threshold=current_threshold,
            current_hit_rate=current_hr,
            current_fire_count=current_fc,
            min_fire_count=min_floor,
            reason=(
                f"Insufficient data: no candidate threshold produced at least "
                f"{min_floor} historical fires of {atype or 'this alert type'}. "
                "Not recommending a change."
            ),
            candidates=swept,
        )

    best = max(eligible, key=lambda s: (s.hit_rate, -s.threshold))

    if current_hr is not None:
        delta = best.hit_rate - current_hr
        reason = (
            f"At threshold {best.threshold:g} the hit-rate is "
            f"{best.hit_rate * 100:.1f}% over {best.fire_count} fires "
            f"(vs {current_hr * 100:.1f}% over {current_fc} fires at the "
            f"current {current_threshold:g} bar — "
            f"{'+' if delta >= 0 else ''}{delta * 100:.1f} pts)."
        )
    else:
        reason = (
            f"At threshold {best.threshold:g} the hit-rate is "
            f"{best.hit_rate * 100:.1f}% over {best.fire_count} fires. "
            "No current override on record to compare against."
        )

    return ThresholdRecommendation(
        alert_type=atype,
        recommended=best.threshold,
        recommended_hit_rate=best.hit_rate,
        recommended_fire_count=best.fire_count,
        current_threshold=current_threshold,
        current_hit_rate=current_hr,
        current_fire_count=current_fc,
        min_fire_count=min_floor,
        reason=reason,
        candidates=swept,
    )


def _current_threshold_for(alert_type: str) -> Optional[float]:
    """Read the alert_type's current override threshold from the store, or
    None when no override exists. Best-effort — any failure returns None."""
    try:
        from engine.route_thresholds import load_route_thresholds
        rt = load_route_thresholds().get(str(alert_type).upper())
        return float(rt.threshold_pct) if rt is not None else None
    except Exception:
        return None


def _score_at_current(
    outcomes: list[AlertOutcome],
    current_threshold: Optional[float],
    swept: list[ThresholdScore],
) -> tuple[Optional[float], Optional[int]]:
    """Hit-rate + fire-count at the current bar, for the 'why' delta.

    When a current override exists we score the outcomes directly at that
    exact bar (so the comparison is honest even if the bar is off-grid).
    When no override exists, there is no baseline — return (None, None)."""
    if current_threshold is None:
        return None, None
    try:
        s = _score_candidate(outcomes, float(current_threshold))
        if s.fire_count == 0:
            return None, 0
        return s.hit_rate, s.fire_count
    except Exception:
        return None, None


def apply_recommended_threshold(
    alert_type: str,
    value: float,
    *,
    severity: Optional[str] = None,
):
    """Write a recommended threshold into the override store (the APPLY side).

    Best-effort, validated, reversible. Delegates to
    ``engine.route_thresholds.set_threshold_for_key`` — the same kv_state
    store the rate detector already reads — keying the override by
    ``alert_type`` (upper-cased). The returned ``ThresholdWriteResult``
    carries the prior threshold so the change can be undone.

    This is the side-effecting half of the loop; the pure recommendation
    logic lives entirely in ``recommend_threshold`` and never touches the
    store. Never raises — a validation / write failure returns a result with
    ``ok=False``.
    """
    try:
        from engine.route_thresholds import set_threshold_for_key
        return set_threshold_for_key(
            str(alert_type or "").upper(), value, severity=severity,
        )
    except Exception as exc:
        logger.debug(f"apply_recommended_threshold: write failed: {exc}")
        # Mirror the ThresholdWriteResult contract on a hard failure.
        try:
            from engine.route_thresholds import ThresholdWriteResult
            return ThresholdWriteResult(
                ok=False, key=str(alert_type or "").upper(),
                new_value=float(value) if _is_finite(value) else 0.0,
            )
        except Exception:
            return None


def _is_finite(x) -> bool:
    try:
        v = float(x)
        return v == v and v not in (float("inf"), float("-inf"))
    except (TypeError, ValueError):
        return False
