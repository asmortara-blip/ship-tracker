"""
Freight Rate Forecaster

Projects freight rates 30/60/90 days forward for each route using a
**seasonally-adjusted linear trend** with **horizon-dependent uncertainty**.

This is intentionally simple and transparent — we show the methodology
so users can judge the forecast, not just trust a black box.

Methodology
-----------
1. **Deseasonalize, then fit.** Container freight rates carry strong calendar
   seasonality (the Pre-CNY export surge, the CNY slowdown, the Aug-Oct peak
   season, the post-holiday lull — see ``processing/seasonal.py``). Fitting a
   trend straight through that seasonality lets the season-of-the-moment tilt
   the slope. Instead we first **remove** the seasonal component: each
   historical observation is divided by its date's seasonal factor, giving a
   deseasonalized series. The linear trend is fitted on *that*.

2. **Re-apply seasonality for the forecast date.** The deseasonalized trend is
   extrapolated to the 30/60/90-day horizon, then **multiplied back** by the
   seasonal factor *of the forecast date*. A forecast that lands in peak
   season therefore reflects peak season; one landing in the CNY slowdown
   reflects that softness — regardless of where "today" sits in the calendar.

3. **Horizon-dependent uncertainty.** Forecast error grows with horizon: a
   90-day projection is far less certain than a 30-day one. The confidence
   band is the residual std scaled by ``sqrt(horizon / 30)`` — the standard
   random-walk-style widening — so the 90d band is ~√3× the 30d band.

The seasonal factors come from ``processing.seasonal.get_seasonal_adjustment``,
which returns a date-aware adjustment in ``[-0.15, +0.15]`` (positive = seasonal
tailwind). We convert that to a multiplicative factor ``1 + adjustment``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from loguru import logger


# ── Tuning constants (documented) ───────────────────────────────────────────────

# Reference horizon (days) at which the confidence band equals the raw residual
# std. Shorter horizons get a tighter band, longer horizons a wider one.
_BAND_REFERENCE_HORIZON: float = 30.0

# Hard sanity caps on a forecast, as a multiple of the current rate. A linear
# trend must not be allowed to run to zero or to infinity.
_FORECAST_FLOOR_MULT: float = 0.30
_FORECAST_CEIL_MULT: float = 3.00


@dataclass
class RateForecast:
    route_id: str
    route_name: str
    current_rate: float          # USD/FEU
    forecast_30d: float          # projected rate in 30 days
    forecast_60d: float          # projected rate in 60 days
    forecast_90d: float          # projected rate in 90 days
    trend_slope: float           # USD/FEU per day (positive = rising)
    r_squared: float             # Linear fit quality [0, 1]
    confidence: str              # "High" | "Medium" | "Low"
    methodology: str             # Plain-English explanation
    upper_30d: float             # Upper bound (≈1 std dev, horizon-scaled)
    lower_30d: float             # Lower bound
    data_points: int
    # ── Added fields (90d band so callers see horizon-widened uncertainty) ──────
    upper_90d: float = 0.0       # Upper bound at 90d — wider than the 30d band
    lower_90d: float = 0.0       # Lower bound at 90d
    seasonal_factor_30d: float = 1.0  # seasonal multiplier applied at the 30d horizon


def forecast_all_routes(
    freight_data: dict[str, pd.DataFrame],
    seasonal_adjustments: dict[str, float] | None = None,
) -> list[RateForecast]:
    """Generate rate forecasts for all routes that have sufficient history.

    Args:
        freight_data: dict route_id → DataFrame from freight_scraper
        seasonal_adjustments: optional dict route_id → adjustment [-0.15, +0.15].
            Legacy override: when supplied for a route it is used as that
            route's seasonal factor instead of the date-aware lookup. When
            omitted the forecaster derives proper per-date seasonal factors
            from ``processing.seasonal`` (deseasonalize history, re-apply at
            the forecast horizon).

    Returns:
        List of RateForecast, sorted by |forecast_30d - current_rate| descending
        (largest expected moves first).
    """
    from routes.route_registry import ROUTES

    results = []
    for route in ROUTES:
        df = freight_data.get(route.id)
        if df is None or df.empty or len(df) < 5:
            logger.debug(f"Insufficient data for forecast: {route.id} ({len(df) if df is not None else 0} points)")
            continue

        seasonal_override = (seasonal_adjustments or {}).get(route.id)
        forecast = _forecast_route(route.id, route.name, df, seasonal_override)
        if forecast:
            results.append(forecast)

    results.sort(key=lambda f: abs(f.forecast_30d - f.current_rate), reverse=True)
    logger.info(f"Forecaster: {len(results)} route forecasts generated")
    return results


def _seasonal_factor(route_id: str, ref_date: date, override: float | None) -> float:
    """Return the multiplicative seasonal factor for a route on a given date.

    The seasonal *adjustment* is in ``[-0.15, +0.15]`` (positive = tailwind);
    the multiplicative *factor* is ``1 + adjustment``. When ``override`` is
    supplied (legacy caller path) it is used directly; otherwise the date-aware
    lookup from ``processing.seasonal`` is used. Any failure degrades to a
    neutral factor of 1.0 so the forecast still runs.
    """
    if override is not None:
        adj = float(override)
    else:
        try:
            from processing.seasonal import get_seasonal_adjustment

            adj = get_seasonal_adjustment(route_id, ref_date)
        except Exception as exc:
            logger.debug(f"Seasonal lookup failed for {route_id} @ {ref_date}: {exc}")
            adj = 0.0

    # Clamp to the documented seasonal range, then convert to a factor.
    adj = max(-0.15, min(0.15, adj))
    return 1.0 + adj


def _forecast_route(
    route_id: str,
    route_name: str,
    df: pd.DataFrame,
    seasonal_override: float | None = None,
) -> RateForecast | None:
    """Generate a forecast for a single route.

    Steps: (1) deseasonalize the history, (2) fit a linear trend on the
    deseasonalized series, (3) extrapolate to 30/60/90d and re-apply the
    seasonal factor of each forecast date, (4) build horizon-widened
    confidence bands.
    """
    try:
        df = df.sort_values("date").copy()
        df = df[df["rate_usd_per_feu"] > 0]

        # Skip fallback-only data
        if "source" in df.columns and (df["source"] == "fallback").all():
            return None

        rates = df["rate_usd_per_feu"].values.astype(float)
        n = len(rates)

        if n < 3:
            return None

        current_rate = float(rates[-1])

        # ── Per-observation dates (needed to deseasonalize each point) ─────────
        # Use the DataFrame's own dates when present; otherwise fall back to a
        # daily grid ending today so seasonality still has a calendar to work
        # against.
        if "date" in df.columns:
            obs_dates = pd.to_datetime(df["date"]).dt.date.tolist()
        else:
            today = date.today()
            obs_dates = [today - timedelta(days=(n - 1 - i)) for i in range(n)]

        # ── (1) Deseasonalize ──────────────────────────────────────────────────
        # Divide each observed rate by its date's seasonal factor. The result
        # is what the rate "would be" stripped of calendar effects, so the
        # trend fit is not tilted by the season the history happens to cover.
        hist_factors = np.array(
            [_seasonal_factor(route_id, d, seasonal_override) for d in obs_dates],
            dtype=float,
        )
        hist_factors = np.where(hist_factors > 0, hist_factors, 1.0)
        deseasonalized = rates / hist_factors

        # ── (2) Linear regression on the DESEASONALIZED series vs. time index ──
        x = np.arange(n, dtype=float)
        x_mean = x.mean()
        y_mean = deseasonalized.mean()

        ss_xy = np.sum((x - x_mean) * (deseasonalized - y_mean))
        ss_xx = np.sum((x - x_mean) ** 2)

        slope = float(ss_xy / ss_xx) if ss_xx > 0 else 0.0
        intercept = float(y_mean - slope * x_mean)

        # R-squared of the deseasonalized fit.
        y_pred = slope * x + intercept
        ss_res = np.sum((deseasonalized - y_pred) ** 2)
        ss_tot = np.sum((deseasonalized - y_mean) ** 2)
        r_squared = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
        r_squared = max(0.0, min(1.0, r_squared))

        # Residual std dev of the deseasonalized fit — the base for the bands.
        residuals = deseasonalized - y_pred
        std_dev = float(np.std(residuals))

        # Deseasonalized level at the end of the series — the launch point for
        # extrapolation (kept seasonality-free until we re-apply it below).
        deseason_now = float(slope * (n - 1) + intercept)

        # ── (3) Extrapolate, then RE-APPLY the seasonal factor of the horizon ──
        last_obs_date = obs_dates[-1]

        def _project(horizon_days: int) -> tuple[float, float]:
            """Return (forecast_rate, seasonal_factor) for a forward horizon."""
            deseason_future = deseason_now + slope * horizon_days
            fdate = last_obs_date + timedelta(days=horizon_days)
            factor = _seasonal_factor(route_id, fdate, seasonal_override)
            raw = deseason_future * factor
            # Hard sanity caps — never let the linear trend run away.
            capped = max(
                current_rate * _FORECAST_FLOOR_MULT,
                min(current_rate * _FORECAST_CEIL_MULT, raw),
            )
            return capped, factor

        forecast_30, factor_30 = _project(30)
        forecast_60, _ = _project(60)
        forecast_90, factor_90 = _project(90)

        # ── Horizon-dependent confidence bands ─────────────────────────────────
        # Forecast error grows with horizon. We widen the residual std by
        # sqrt(horizon / 30) — the standard random-walk error-growth law — and
        # scale the band by the horizon's seasonal factor so the band sits
        # around the (seasonally re-applied) forecast level.
        def _band_halfwidth(horizon_days: int, factor: float) -> float:
            scale = np.sqrt(max(horizon_days, 1) / _BAND_REFERENCE_HORIZON)
            return std_dev * scale * factor

        hw_30 = _band_halfwidth(30, factor_30)
        hw_90 = _band_halfwidth(90, factor_90)

        upper_30 = forecast_30 + hw_30
        lower_30 = max(0.0, forecast_30 - hw_30)
        upper_90 = forecast_90 + hw_90
        lower_90 = max(0.0, forecast_90 - hw_90)

        # ── Confidence rating (based on deseasonalized fit quality & sample) ───
        if r_squared >= 0.60 and n >= 30:
            confidence = "High"
        elif r_squared >= 0.30 and n >= 10:
            confidence = "Medium"
        else:
            confidence = "Low"

        # ── Plain-English methodology ──────────────────────────────────────────
        pct_30d = (forecast_30 - current_rate) / current_rate * 100 if current_rate else 0.0
        if abs(slope) < 0.5:
            methodology = (
                f"Deseasonalized trend is flat. Projecting near-stable rates "
                f"({pct_30d:+.0f}% 30d)."
            )
        elif slope > 0:
            methodology = (
                f"Underlying (deseasonalized) rate trending up ~${slope*7:.0f}/FEU per week. "
                f"Projecting ${forecast_30:,.0f}/FEU in 30d ({pct_30d:+.0f}%). "
                f"Confidence: {confidence} (R²={r_squared:.2f}, {n} data points)."
            )
        else:
            methodology = (
                f"Underlying (deseasonalized) rate trending down ~${abs(slope)*7:.0f}/FEU per week. "
                f"Projecting ${forecast_30:,.0f}/FEU in 30d ({pct_30d:+.0f}%). "
                f"Confidence: {confidence} (R²={r_squared:.2f}, {n} data points)."
            )

        # Surface the seasonal re-application so the user sees why a forecast
        # tilts away from the bare trend.
        seasonal_pct_30 = (factor_30 - 1.0) * 100
        if abs(seasonal_pct_30) >= 1.0:
            tilt = "tailwind" if seasonal_pct_30 > 0 else "headwind"
            methodology += (
                f" Seasonal {tilt} of {seasonal_pct_30:+.0f}% re-applied for the 30d horizon."
            )
        methodology += " 90d band widened by √(horizon) for forecast uncertainty."

        return RateForecast(
            route_id=route_id,
            route_name=route_name,
            current_rate=current_rate,
            forecast_30d=forecast_30,
            forecast_60d=forecast_60,
            forecast_90d=forecast_90,
            trend_slope=slope,
            r_squared=r_squared,
            confidence=confidence,
            methodology=methodology,
            upper_30d=upper_30,
            lower_30d=lower_30,
            data_points=n,
            upper_90d=upper_90,
            lower_90d=lower_90,
            seasonal_factor_30d=float(factor_30),
        )

    except Exception as exc:
        logger.error(f"Forecast failed for {route_id}: {exc}")
        return None
