"""engine/fleet_utilization.py — fleet-utilization composite + rate predictor.

Derives an interpretable utilization score from the modeled voyage fleet
(``data.voyage_dataset.build_voyage_fleet``) and exposes it as a leading
indicator for freight-rate direction.

Why this exists
---------------
Carrier earnings track freight rates; freight rates track capacity tightness;
capacity tightness shows up in *fleet utilization* — the fraction of vessels
under load and the share of fleet-time committed to slow-progress legs —
BEFORE it shows up in a price index. The Phase-3 roadmap line for this
module promises to surface utilization as that leading indicator.

What "utilization" means here
-----------------------------
We don't have AIS-grade laden-vs-ballast telemetry, so we compose four signals
that are jointly tracked by a vessel's :class:`data.voyage_dataset.Voyage`
record:

  - **Active share** — non-arrived voyages / total voyages on the route.
    Carriers running idle ships post-arrival drives this down.

  - **Capacity lock-in** — mean of ``(1 − progress_pct)`` across active
    voyages. High when the fleet is committed early-mid voyage; low when
    most active voyages are about to discharge (freeing capacity soon).

  - **Delay intensity** — mean ``delay_days`` across active voyages,
    normalized. Delays remove effective fleet capacity from the market.

  - **Forward congestion** — mean ``congestion_at_dest`` across active
    voyages. Forecasts further capacity removal as ships queue.

The four are blended into a [0, 1] composite. By construction, **higher
utilization ⇒ tighter capacity ⇒ bullish for rates**.

Principle-5 compliance
----------------------
Like every other model in the repo (``docs/ROADMAP.md``),
:func:`walk_forward_backtest` rolls a fitting window forward through history,
fits the lag between utilization changes and forward rate changes, and scores
hit rate + in-sample/out-of-sample r.

Pure-function module — no streamlit, no globals, no module-level mutable state.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

import numpy as np
import pandas as pd
from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# Blend weights — sum to 1.0 (invariant checked at import per the codebase
# convention; ValueError so it survives ``python -O``).
# ─────────────────────────────────────────────────────────────────────────────

_W_ACTIVE_SHARE: float = 0.30      # fleet is under-rotating → active share matters
_W_LOCK_IN: float = 0.30           # committed capacity locks supply
_W_DELAY: float = 0.20             # delays remove effective tonnage
_W_FWD_CONG: float = 0.20          # destination congestion forecasts more removal

if abs(_W_ACTIVE_SHARE + _W_LOCK_IN + _W_DELAY + _W_FWD_CONG - 1.0) >= 1e-9:
    raise ValueError("fleet_utilization blend weights must sum to 1.0")


# Normalising constants — keep the inputs to the blend on a [0, 1] scale.
_DELAY_SATURATION_DAYS: float = 14.0    # ≥14d of mean delay → full signal
_CLASSIFICATION_TIGHT: float = 0.65     # composite score thresholds
_CLASSIFICATION_SLACK: float = 0.40


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RouteFleetMetrics:
    """Per-route utilization snapshot."""
    route_id: str
    voyages_total: int
    voyages_active: int            # status != "Arrived"
    active_share: float            # voyages_active / max(1, voyages_total)
    mean_progress_pct: float       # 0..1 across active voyages
    capacity_lock_in: float        # mean(1 − progress) across active
    mean_delay_days: float         # mean across active
    mean_dest_congestion: float    # mean(congestion_at_dest) across active
    utilization_score: float       # [0, 1] composite
    classification: str            # "Tight" | "Balanced" | "Slack"


@dataclass(frozen=True)
class FleetUtilizationReport:
    """Fleet-wide rollup across every route."""
    routes: list[RouteFleetMetrics]
    fleet_utilization: float       # voyage-weighted mean of per-route scores
    fleet_classification: str
    voyages_total: int
    voyages_active: int
    computed_at: datetime


@dataclass(frozen=True)
class UtilizationBacktest:
    """Walk-forward backtest of utilization as a rate predictor."""
    n_windows: int
    best_lag_days: int             # modal lag picked across windows
    hit_rate: float                # fraction of windows where sign matched
    avg_r_in_sample: float
    avg_r_out_of_sample: float


# ─────────────────────────────────────────────────────────────────────────────
# Classification helper
# ─────────────────────────────────────────────────────────────────────────────

def classify_utilization(score: float) -> str:
    """Bucket a [0, 1] composite into Tight / Balanced / Slack."""
    if score >= _CLASSIFICATION_TIGHT:
        return "Tight"
    if score < _CLASSIFICATION_SLACK:
        return "Slack"
    return "Balanced"


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot computation
# ─────────────────────────────────────────────────────────────────────────────

def _route_metrics(route_id: str, voyages: list) -> RouteFleetMetrics:
    """Compute metrics for a single route from its voyage list.

    ``voyages`` items are duck-typed against :class:`data.voyage_dataset.Voyage`
    — only the attributes the model reads are required, which keeps the
    function fully testable with lightweight fakes.
    """
    total = len(voyages)
    active = [v for v in voyages if getattr(v, "status", "") != "Arrived"]
    n_active = len(active)

    if n_active == 0:
        return RouteFleetMetrics(
            route_id=route_id,
            voyages_total=total,
            voyages_active=0,
            active_share=0.0,
            mean_progress_pct=0.0,
            capacity_lock_in=0.0,
            mean_delay_days=0.0,
            mean_dest_congestion=0.0,
            utilization_score=0.0,
            classification="Slack",
        )

    progress = np.array(
        [float(getattr(v, "progress_pct", 0.0)) for v in active], dtype=float
    )
    progress = np.clip(progress, 0.0, 1.0)
    delays = np.array(
        [float(getattr(v, "delay_days", 0.0)) for v in active], dtype=float
    )
    dest_cong = np.array(
        [float(getattr(v, "congestion_at_dest", 0.0)) for v in active], dtype=float
    )
    dest_cong = np.clip(dest_cong, 0.0, 1.0)

    active_share = n_active / max(1, total)                       # [0, 1]
    mean_progress = float(progress.mean())                        # [0, 1]
    lock_in = float(np.mean(1.0 - progress))                      # [0, 1]
    mean_delay = float(np.mean(np.maximum(delays, 0.0)))          # ≥0
    fwd_cong = float(dest_cong.mean())                            # [0, 1]
    delay_norm = min(1.0, mean_delay / _DELAY_SATURATION_DAYS)    # [0, 1]

    score = (
        _W_ACTIVE_SHARE * active_share
        + _W_LOCK_IN * lock_in
        + _W_DELAY * delay_norm
        + _W_FWD_CONG * fwd_cong
    )
    score = max(0.0, min(1.0, score))

    return RouteFleetMetrics(
        route_id=route_id,
        voyages_total=total,
        voyages_active=n_active,
        active_share=round(active_share, 4),
        mean_progress_pct=round(mean_progress, 4),
        capacity_lock_in=round(lock_in, 4),
        mean_delay_days=round(mean_delay, 3),
        mean_dest_congestion=round(fwd_cong, 4),
        utilization_score=round(score, 4),
        classification=classify_utilization(score),
    )


def compute_fleet_utilization(fleet: list) -> FleetUtilizationReport:
    """Compute a :class:`FleetUtilizationReport` from a Voyage fleet.

    Pure function — no I/O, no global state, no module-level mutation. Always
    returns a well-formed report, even on empty input.
    """
    if not fleet:
        return FleetUtilizationReport(
            routes=[],
            fleet_utilization=0.0,
            fleet_classification="Slack",
            voyages_total=0,
            voyages_active=0,
            computed_at=datetime.now(timezone.utc),
        )

    # Group by route_id.
    by_route: dict[str, list] = {}
    for v in fleet:
        rid = getattr(v, "route_id", "")
        if not rid:
            continue
        by_route.setdefault(rid, []).append(v)

    routes = [
        _route_metrics(rid, voyages)
        for rid, voyages in sorted(by_route.items())
    ]

    # Fleet rollup: voyage-weighted mean across routes.
    total_voyages = sum(r.voyages_total for r in routes)
    active_voyages = sum(r.voyages_active for r in routes)
    if total_voyages > 0:
        weighted = sum(r.utilization_score * r.voyages_total for r in routes)
        fleet_score = weighted / total_voyages
    else:
        fleet_score = 0.0

    return FleetUtilizationReport(
        routes=routes,
        fleet_utilization=round(fleet_score, 4),
        fleet_classification=classify_utilization(fleet_score),
        voyages_total=total_voyages,
        voyages_active=active_voyages,
        computed_at=datetime.now(timezone.utc),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward backtest (principle 5)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CANDIDATE_LAGS: tuple[int, ...] = (3, 5, 7, 10, 14, 21)
"""Lag horizons in days at which Δutilization is tested against Δrate."""

DEFAULT_MIN_OBS = 25
DEFAULT_MIN_ABS_R = 0.10
DEFAULT_MAX_P = 0.10


def _pearsonr(x: pd.Series, y: pd.Series) -> tuple[float, float]:
    """Pearson r + two-sided p via the normal approximation to the t-stat.

    Mirrors ``processing.congestion_rate_lag._pearsonr`` to keep the codebase
    consistent — same gates, same fallbacks. Inlined here rather than imported
    so engine/ stays independent of processing/.
    """
    n = len(x)
    if n < 3:
        return 0.0, 1.0
    r = float(np.corrcoef(x.to_numpy(), y.to_numpy())[0, 1])
    if not math.isfinite(r) or abs(r) >= 1.0:
        return r, 1.0
    if n < 30:
        return r, 1.0
    t_stat = r * math.sqrt((n - 2) / max(1e-12, 1.0 - r * r))
    p = math.erfc(abs(t_stat) / math.sqrt(2.0))
    return r, p


def _delta_log(series: pd.Series) -> pd.Series:
    """Log-return series. Clips zeros/negatives to a small positive."""
    positive = series.replace(0.0, np.nan).clip(lower=1e-9)
    return np.log(positive).diff().dropna()


def _fit_lag(
    util_series: pd.Series,
    rate_series: pd.Series,
    candidate_lags: Sequence[int],
    min_obs: int,
    min_abs_r: float,
    max_p: float,
) -> Optional[tuple[int, float, float, int]]:
    """Find the best lag among candidates that clears all gates."""
    if util_series is None or rate_series is None:
        return None
    if util_series.empty or rate_series.empty:
        return None

    d_util = _delta_log(util_series)
    if d_util.empty:
        return None

    best: Optional[tuple[int, float, float, int]] = None
    best_abs = 0.0
    for lag in candidate_lags:
        # Shift the rate's Δlog backwards by `lag` so Δutil_t aligns with
        # Δrate_{t+lag}.
        d_rate_lagged = _delta_log(rate_series).shift(-lag)
        combined = pd.concat([d_util, d_rate_lagged], axis=1, join="inner").dropna()
        if len(combined) < min_obs:
            continue
        combined.columns = ["util", "rate"]
        r, p = _pearsonr(combined["util"], combined["rate"])
        if abs(r) < min_abs_r or p > max_p:
            continue
        if abs(r) > best_abs:
            best_abs = abs(r)
            best = (lag, r, p, len(combined))
    return best


def walk_forward_backtest(
    util_series: pd.Series,
    rate_series: pd.Series,
    *,
    train_window: int = 90,
    test_window: int = 14,
    step: int = 14,
    candidate_lags: Sequence[int] = DEFAULT_CANDIDATE_LAGS,
    min_obs: int = DEFAULT_MIN_OBS,
    min_abs_r: float = DEFAULT_MIN_ABS_R,
    max_p: float = DEFAULT_MAX_P,
) -> UtilizationBacktest:
    """Roll a (train_window, test_window, step)-day window through history.

    For each window:

      1. **Fit** the best lag on training data.
      2. **Predict** sign of next-window Δlog(rate) from the latest training
         Δlog(utilization), times sign(r). Directional prediction only —
         magnitudes are noisy and we don't need them for the use case.
      3. **Score** as a hit when predicted sign matches realized sign.

    Returns an :class:`UtilizationBacktest` summarizing window count, modal
    lag (stability indicator), hit rate, and r̄ in/out of sample.
    """
    if util_series is None or rate_series is None:
        return _empty_backtest()

    util_series = util_series.dropna().sort_index()
    rate_series = rate_series.dropna().sort_index()
    if util_series.empty or rate_series.empty:
        return _empty_backtest()

    common_start = max(util_series.index.min(), rate_series.index.min())
    common_end = min(util_series.index.max(), rate_series.index.max())
    if pd.isna(common_start) or pd.isna(common_end):
        return _empty_backtest()
    if (common_end - common_start).days < (train_window + test_window):
        return _empty_backtest()

    cur = common_start + pd.Timedelta(days=train_window)
    n_windows = 0
    hits = 0
    in_rs: list[float] = []
    out_rs: list[float] = []
    chosen_lags: list[int] = []

    while cur + pd.Timedelta(days=test_window) <= common_end:
        train_lo = cur - pd.Timedelta(days=train_window)
        train_hi = cur
        test_lo = cur
        test_hi = cur + pd.Timedelta(days=test_window)

        train_util = util_series.loc[train_lo:train_hi]
        train_rate = rate_series.loc[train_lo:train_hi]
        fit = _fit_lag(
            train_util, train_rate,
            candidate_lags, min_obs, min_abs_r, max_p,
        )
        if fit is not None:
            lag, r_in, _p, _n = fit
            chosen_lags.append(lag)
            in_rs.append(r_in)

            d_train_util = _delta_log(train_util)
            if not d_train_util.empty:
                predicted_dir = 1.0 if r_in * d_train_util.iloc[-1] > 0 else -1.0
                test_rate = rate_series.loc[test_lo:test_hi]
                if len(test_rate) >= 2:
                    realized = float(np.log(test_rate.iloc[-1] / test_rate.iloc[0]))
                    realized_dir = 1.0 if realized > 0 else (-1.0 if realized < 0 else 0.0)
                    if realized_dir != 0.0 and predicted_dir == realized_dir:
                        hits += 1

                    # Out-of-sample r at the picked lag, on the test window only.
                    test_util = util_series.loc[test_lo:test_hi]
                    d_test_util = _delta_log(test_util)
                    d_test_rate = _delta_log(test_rate).shift(-lag)
                    pair = pd.concat([d_test_util, d_test_rate], axis=1, join="inner").dropna()
                    if len(pair) >= 3:
                        r_out = float(
                            np.corrcoef(pair.iloc[:, 0].to_numpy(),
                                        pair.iloc[:, 1].to_numpy())[0, 1]
                        )
                        if math.isfinite(r_out):
                            out_rs.append(r_out)
            n_windows += 1

        cur += pd.Timedelta(days=step)

    if n_windows == 0:
        return _empty_backtest()

    # Modal lag (most-frequently chosen) — robust stability indicator.
    modal_lag = int(pd.Series(chosen_lags).mode().iat[0]) if chosen_lags else 0

    return UtilizationBacktest(
        n_windows=n_windows,
        best_lag_days=modal_lag,
        hit_rate=hits / n_windows,
        avg_r_in_sample=float(np.mean(in_rs)) if in_rs else 0.0,
        avg_r_out_of_sample=float(np.mean(out_rs)) if out_rs else 0.0,
    )


def _empty_backtest() -> UtilizationBacktest:
    return UtilizationBacktest(
        n_windows=0,
        best_lag_days=0,
        hit_rate=0.0,
        avg_r_in_sample=0.0,
        avg_r_out_of_sample=0.0,
    )
