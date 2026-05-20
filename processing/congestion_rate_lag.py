"""processing/congestion_rate_lag.py — Port-congestion → freight-rate lag model.

A port's congestion typically *leads* the freight rate on the lanes that port
serves: vessels backing up at LA/LB today translates into transpacific
rates moving N days later. This module quantifies that lag.

For every (port, route) pair from the registry where the port is the route's
origin or destination AND we have data for both sides, the model:

  1. Aligns the port's daily congestion-score history with the route's daily
     freight-rate history on a common date index.
  2. For each candidate lag ``L`` in days, computes the Pearson correlation
     between Δcongestion_t and Δrate_{t+L}.
  3. Picks the lag with the largest |r| that clears significance gates
     (p < α, |r| > floor, n ≥ min_obs) — or returns ``None`` for that pair.

The output is a ranked list of :class:`CongestionRateLag` records, each
carrying the chosen lag, the correlation strength, and a one-line
interpretation suitable for direct UI use.

Principle-5 compliance
----------------------
Every new model in this codebase ships with a walk-forward backtest
(``docs/ROADMAP.md``). :func:`walk_forward_backtest` rolls the fitting
window forward through history, fits the optimal lag on training data,
predicts the sign of the next-window rate move, and scores hit rate +
in-sample/out-of-sample correlation.

Outside dependencies: pandas, numpy, scipy.stats (for the t-distribution
on the Pearson p-value). No streamlit, no globals, no module-level
mutable state.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import pandas as pd
from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# Defaults — tuned for daily-cadence shipping data
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CANDIDATE_LAGS: tuple[int, ...] = (1, 3, 5, 7, 10, 14, 21, 30)
"""Lag horizons (days) at which Δcongestion is tested against Δrate.

Anchored on the typical voyage-and-rate transmission window — anything under
1 day is noise; beyond 30 days the signal is dominated by macro drivers.
"""

DEFAULT_MIN_OBS = 30
"""Minimum aligned (Δcong, Δrate) pairs before a correlation is even computed.

Below this, the t-distribution p-value is too sensitive to outliers to trust.
"""

DEFAULT_MIN_ABS_R = 0.15
"""Reject correlations weaker than this — at |r|<0.15 the signal is below
noise in real shipping data."""

DEFAULT_MAX_P = 0.05
"""Reject lags whose two-sided Pearson p-value exceeds α=0.05."""


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CongestionRateLag:
    """The fitted lag relationship between one port and one route."""
    port_locode: str
    port_name: str
    route_id: str
    route_name: str
    role: str                 # "origin" or "destination"
    best_lag_days: int        # positive ⇒ congestion leads rate by this many days
    pearson_r: float
    p_value: float
    n_observations: int
    direction: str            # "positive" | "negative"
    interpretation: str       # one-line UI-ready summary


@dataclass(frozen=True)
class LagBacktestResult:
    """Walk-forward backtest of the lag-prediction strategy on a single pair."""
    port_locode: str
    route_id: str
    n_windows: int            # how many train/test splits ran
    hit_rate: float           # fraction where predicted sign of Δrate matched actual
    avg_r_in_sample: float
    avg_r_out_of_sample: float
    avg_lag_days: float       # mean of the lag picked across windows (stability indicator)


# ─────────────────────────────────────────────────────────────────────────────
# Core correlation helper
# ─────────────────────────────────────────────────────────────────────────────

def _pearsonr(x: pd.Series, y: pd.Series) -> tuple[float, float]:
    """Pearson r and two-sided p-value via the t-distribution.

    A small dependency-free implementation: we use ``np.corrcoef`` for the r
    value and derive the p from the t-statistic ``t = r * sqrt((n-2)/(1-r²))``
    against a Student-t with ``n-2`` degrees of freedom. Avoids pulling in
    scipy as a hard requirement and matches what the rest of the codebase
    expects (engine/correlator.py uses the same idea).
    """
    n = len(x)
    if n < 3:
        return 0.0, 1.0
    r = float(np.corrcoef(x.to_numpy(), y.to_numpy())[0, 1])
    if not math.isfinite(r) or abs(r) >= 1.0:
        # Degenerate input (constant series, NaNs slipped through, etc.).
        return r, 1.0
    # t = r * sqrt((n-2)/(1-r²))
    t_stat = r * math.sqrt((n - 2) / max(1e-12, 1.0 - r * r))
    # Two-sided p via the survival function of the standard normal as a fast
    # approximation for n >= 30; below that we fall back to a conservative 1.0.
    if n < 30:
        # Too small for the normal approximation to be safe — let the gate
        # at the call site (min_obs) catch this.
        return r, 1.0
    # P(|T| > |t|) ≈ 2·Φ(−|t|) for n large enough; this is the standard fast path.
    p = 2.0 * 0.5 * math.erfc(abs(t_stat) / math.sqrt(2.0))
    return r, p


def _delta_log(series: pd.Series) -> pd.Series:
    """First-difference on log values. Robust to multiplicative scale changes,
    natural for rate / congestion series. Returns a Series re-indexed on the
    second-onwards entries of the input.
    """
    positive = series.replace(0.0, np.nan).clip(lower=1e-9)
    return np.log(positive).diff().dropna()


def _aligned_changes(
    cong_series: pd.Series,
    rate_series: pd.Series,
    lag_days: int,
) -> tuple[pd.Series, pd.Series]:
    """Align Δcongestion at t with Δrate at t+lag_days on a shared date index.

    Both inputs are date-indexed (DatetimeIndex). Index alignment is exact:
    no resampling, no forward-fill — callers feeding sparse data should
    upsample/interpolate before calling.
    """
    d_cong = _delta_log(cong_series)
    d_rate = _delta_log(rate_series)
    if d_cong.empty or d_rate.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    # Shift Δrate BACKWARDS by lag_days so its index reads as "the value that
    # occurred lag_days AFTER each date". When concatenated, the index then
    # aligns each Δcong_t row with Δrate_{t+lag}.
    d_rate_lagged = d_rate.shift(-lag_days)
    combined = pd.concat([d_cong, d_rate_lagged], axis=1, join="inner").dropna()
    if combined.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    combined.columns = ["d_cong", "d_rate"]
    return combined["d_cong"], combined["d_rate"]


def compute_lag_correlation(
    cong_series: pd.Series,
    rate_series: pd.Series,
    *,
    candidate_lags: Sequence[int] = DEFAULT_CANDIDATE_LAGS,
    min_obs: int = DEFAULT_MIN_OBS,
    min_abs_r: float = DEFAULT_MIN_ABS_R,
    max_p: float = DEFAULT_MAX_P,
) -> Optional[tuple[int, float, float, int]]:
    """Find the lag in ``candidate_lags`` maximizing |corr(Δcong_t, Δrate_{t+lag})|.

    Returns ``(best_lag, r, p, n)`` if any lag clears all gates
    (n ≥ ``min_obs``, |r| ≥ ``min_abs_r``, p ≤ ``max_p``), or ``None``
    otherwise. Pure function — no I/O, no logging spam in the happy path.
    """
    if cong_series is None or rate_series is None:
        return None
    if cong_series.empty or rate_series.empty:
        return None

    best: Optional[tuple[int, float, float, int]] = None
    best_abs_r = 0.0

    for lag in candidate_lags:
        try:
            d_cong, d_rate = _aligned_changes(cong_series, rate_series, lag)
        except Exception as exc:
            logger.debug(
                f"congestion_rate_lag: alignment failed at lag={lag}: {exc}"
            )
            continue
        n = len(d_cong)
        if n < min_obs:
            continue
        r, p = _pearsonr(d_cong, d_rate)
        if abs(r) < min_abs_r:
            continue
        if p > max_p:
            continue
        if abs(r) > best_abs_r:
            best_abs_r = abs(r)
            best = (lag, r, p, n)

    return best


# ─────────────────────────────────────────────────────────────────────────────
# Sweep across port × route pairs from the registry
# ─────────────────────────────────────────────────────────────────────────────

def _interpret(
    port_name: str,
    route_name: str,
    role: str,
    lag: int,
    r: float,
) -> str:
    """Build a UI-ready one-liner describing the fitted relationship."""
    sign = "rising" if r > 0 else "falling"
    inverse = "falling" if r > 0 else "rising"
    if role == "origin":
        side = "loading-side congestion at"
    else:
        side = "discharge-side congestion at"
    return (
        f"A 1σ surge in {side} {port_name} precedes {sign} "
        f"{route_name} rates ~{lag} days later "
        f"(r = {r:+.2f})."
    ) if r > 0 else (
        f"A 1σ surge in {side} {port_name} precedes {inverse} "
        f"{route_name} rates ~{lag} days later "
        f"(r = {r:+.2f}) — congestion clears as the lane prices down."
    )


def analyze_port_route_lags(
    congestion_history: dict[str, pd.DataFrame],
    freight_data: dict[str, pd.DataFrame],
    *,
    candidate_lags: Sequence[int] = DEFAULT_CANDIDATE_LAGS,
    min_obs: int = DEFAULT_MIN_OBS,
    min_abs_r: float = DEFAULT_MIN_ABS_R,
    max_p: float = DEFAULT_MAX_P,
) -> list[CongestionRateLag]:
    """Fit the lag model to every applicable (port, route) pair.

    Iterates over every :class:`routes.route_registry.ShippingRoute` and, for
    both its origin and destination LOCODE, attempts a fit when both:

      - ``congestion_history[locode]`` is a DataFrame with ``date`` and a
        ``congestion_score`` (or ``vessel_count`` as a fallback proxy);
      - ``freight_data[route_id]`` is a DataFrame with ``date`` and
        ``rate_usd_per_feu``.

    Results are returned sorted by ``|pearson_r|`` descending so the
    strongest leading relationships surface first.
    """
    # Imported lazily so the module stays importable without the rest of the
    # platform (and the unit tests can stub or skip the registry).
    try:
        from routes.route_registry import ROUTES
        from ports.port_registry import PORTS_BY_LOCODE
    except Exception as exc:
        logger.debug(f"congestion_rate_lag: registry unavailable: {exc}")
        return []

    results: list[CongestionRateLag] = []

    for route in ROUTES:
        rate_df = freight_data.get(route.id)
        if rate_df is None or getattr(rate_df, "empty", True):
            continue
        if "date" not in rate_df.columns or "rate_usd_per_feu" not in rate_df.columns:
            continue
        rate_series = (
            rate_df.dropna(subset=["date", "rate_usd_per_feu"])
            .sort_values("date")
            .set_index(pd.DatetimeIndex(rate_df["date"]))["rate_usd_per_feu"]
            .astype(float)
        )

        for locode, role in (
            (route.origin_locode, "origin"),
            (route.dest_locode, "destination"),
        ):
            cong_df = congestion_history.get(locode)
            if cong_df is None or getattr(cong_df, "empty", True):
                continue
            if "date" not in cong_df.columns:
                continue
            score_col = (
                "congestion_score"
                if "congestion_score" in cong_df.columns
                else ("vessel_count" if "vessel_count" in cong_df.columns else None)
            )
            if score_col is None:
                continue

            cong_series = (
                cong_df.dropna(subset=["date", score_col])
                .sort_values("date")
                .set_index(pd.DatetimeIndex(cong_df["date"]))[score_col]
                .astype(float)
            )

            fit = compute_lag_correlation(
                cong_series,
                rate_series,
                candidate_lags=candidate_lags,
                min_obs=min_obs,
                min_abs_r=min_abs_r,
                max_p=max_p,
            )
            if fit is None:
                continue
            lag, r, p, n = fit
            port_meta = PORTS_BY_LOCODE.get(locode)
            port_name = getattr(port_meta, "name", locode) if port_meta else locode

            results.append(
                CongestionRateLag(
                    port_locode=locode,
                    port_name=port_name,
                    route_id=route.id,
                    route_name=route.name,
                    role=role,
                    best_lag_days=lag,
                    pearson_r=r,
                    p_value=p,
                    n_observations=n,
                    direction="positive" if r > 0 else "negative",
                    interpretation=_interpret(port_name, route.name, role, lag, r),
                )
            )

    results.sort(key=lambda lag_res: abs(lag_res.pearson_r), reverse=True)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward backtest (principle 5)
# ─────────────────────────────────────────────────────────────────────────────

def walk_forward_backtest(
    cong_series: pd.Series,
    rate_series: pd.Series,
    *,
    train_window: int = 90,
    test_window: int = 14,
    step: int = 14,
    candidate_lags: Sequence[int] = DEFAULT_CANDIDATE_LAGS,
    min_obs: int = DEFAULT_MIN_OBS,
    min_abs_r: float = DEFAULT_MIN_ABS_R,
    max_p: float = DEFAULT_MAX_P,
    port_locode: str = "",
    route_id: str = "",
) -> LagBacktestResult:
    """Walk-forward backtest of the lag predictor on one pair.

    At each rolling step:

      1. **Fit** the optimal lag on the last ``train_window`` days of overlap.
      2. **Predict** the sign of the next ``test_window``-day total Δlog(rate)
         using the most-recent in-sample Δlog(congestion) multiplied by
         ``sign(r)``. (We're not predicting magnitude — only direction —
         since that's what the strategy actually needs and it's far more
         robust than a point forecast.)
      3. **Score** as a hit if the predicted sign matches the realized sign.

    Returns a :class:`LagBacktestResult` summarizing hit rate, in-sample r,
    out-of-sample r, and average chosen lag across all windows.
    """
    # Common daily date range.
    if cong_series is None or rate_series is None:
        return _empty_backtest(port_locode, route_id)
    cong_series = cong_series.dropna().sort_index()
    rate_series = rate_series.dropna().sort_index()
    if cong_series.empty or rate_series.empty:
        return _empty_backtest(port_locode, route_id)

    common_start = max(cong_series.index.min(), rate_series.index.min())
    common_end = min(cong_series.index.max(), rate_series.index.max())
    if pd.isna(common_start) or pd.isna(common_end):
        return _empty_backtest(port_locode, route_id)
    if (common_end - common_start).days < (train_window + test_window):
        return _empty_backtest(port_locode, route_id)

    # Step through window endpoints.
    cur = common_start + pd.Timedelta(days=train_window)
    n_windows = 0
    hits = 0
    in_sample_rs: list[float] = []
    out_sample_rs: list[float] = []
    chosen_lags: list[int] = []

    while cur + pd.Timedelta(days=test_window) <= common_end:
        train_lo = cur - pd.Timedelta(days=train_window)
        train_hi = cur
        test_lo = cur
        test_hi = cur + pd.Timedelta(days=test_window)

        train_cong = cong_series.loc[train_lo:train_hi]
        train_rate = rate_series.loc[train_lo:train_hi]
        fit = compute_lag_correlation(
            train_cong,
            train_rate,
            candidate_lags=candidate_lags,
            min_obs=min_obs,
            min_abs_r=min_abs_r,
            max_p=max_p,
        )
        if fit is not None:
            lag, r_in, _p, _n = fit
            chosen_lags.append(lag)
            in_sample_rs.append(r_in)

            # Predict sign of Δlog(rate) over [test_lo, test_hi] using the
            # most recent Δlog(cong) point in the training window times sign(r).
            d_train_cong = _delta_log(train_cong)
            if not d_train_cong.empty:
                predicted_direction = (
                    1.0 if r_in * d_train_cong.iloc[-1] > 0 else -1.0
                )
                test_rate = rate_series.loc[test_lo:test_hi]
                if len(test_rate) >= 2:
                    realized = float(
                        np.log(test_rate.iloc[-1] / test_rate.iloc[0])
                    )
                    realized_direction = 1.0 if realized > 0 else (
                        -1.0 if realized < 0 else 0.0
                    )
                    if realized_direction != 0.0 and predicted_direction == realized_direction:
                        hits += 1

                    # Out-of-sample r at the chosen lag on the test window only.
                    test_cong = cong_series.loc[test_lo:test_hi]
                    d_cong_t, d_rate_t = _aligned_changes(test_cong, test_rate, lag)
                    if len(d_cong_t) >= 3:
                        r_out = float(np.corrcoef(
                            d_cong_t.to_numpy(),
                            d_rate_t.to_numpy(),
                        )[0, 1])
                        if math.isfinite(r_out):
                            out_sample_rs.append(r_out)
            n_windows += 1

        cur += pd.Timedelta(days=step)

    if n_windows == 0:
        return _empty_backtest(port_locode, route_id)

    return LagBacktestResult(
        port_locode=port_locode,
        route_id=route_id,
        n_windows=n_windows,
        hit_rate=hits / n_windows,
        avg_r_in_sample=float(np.mean(in_sample_rs)) if in_sample_rs else 0.0,
        avg_r_out_of_sample=(
            float(np.mean(out_sample_rs)) if out_sample_rs else 0.0
        ),
        avg_lag_days=(
            float(np.mean(chosen_lags)) if chosen_lags else 0.0
        ),
    )


def _empty_backtest(port_locode: str, route_id: str) -> LagBacktestResult:
    return LagBacktestResult(
        port_locode=port_locode,
        route_id=route_id,
        n_windows=0,
        hit_rate=0.0,
        avg_r_in_sample=0.0,
        avg_r_out_of_sample=0.0,
        avg_lag_days=0.0,
    )
