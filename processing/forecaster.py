"""
Freight Rate Forecaster

Uses linear trend extrapolation with seasonal adjustment to project
freight rates 30/60/90 days forward for each route.

This is intentionally simple and transparent — we show the methodology
so users can judge the forecast, not just trust a black box.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from loguru import logger


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
    upper_30d: float             # Upper bound (1 std dev)
    lower_30d: float             # Lower bound
    data_points: int


def forecast_all_routes(
    freight_data: dict[str, pd.DataFrame],
    seasonal_adjustments: dict[str, float] | None = None,
) -> list[RateForecast]:
    """Generate rate forecasts for all routes that have sufficient history.

    Args:
        freight_data: dict route_id → DataFrame from freight_scraper
        seasonal_adjustments: optional dict route_id → adjustment [-0.15, +0.15]

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

        seasonal_adj = (seasonal_adjustments or {}).get(route.id, 0.0)
        forecast = _forecast_route(route.id, route.name, df, seasonal_adj)
        if forecast:
            results.append(forecast)

    results.sort(key=lambda f: abs(f.forecast_30d - f.current_rate), reverse=True)
    logger.info(f"Forecaster: {len(results)} route forecasts generated")
    return results


def _forecast_route(
    route_id: str,
    route_name: str,
    df: pd.DataFrame,
    seasonal_adj: float = 0.0,
) -> RateForecast | None:
    """Generate a forecast for a single route."""
    try:
        df = df.sort_values("date").copy()
        df = df[df["rate_usd_per_feu"] > 0]

        # Skip fallback-only data
        if "source" in df.columns and (df["source"] == "fallback").all():
            return None

        rates = df["rate_usd_per_feu"].values
        n = len(rates)

        if n < 3:
            return None

        current_rate = float(rates[-1])

        # Linear regression on rate vs time index
        x = np.arange(n, dtype=float)
        x_mean = x.mean()
        y_mean = rates.mean()

        ss_xy = np.sum((x - x_mean) * (rates - y_mean))
        ss_xx = np.sum((x - x_mean) ** 2)

        slope = float(ss_xy / ss_xx) if ss_xx > 0 else 0.0
        intercept = float(y_mean - slope * x_mean)

        # R-squared
        y_pred = slope * x + intercept
        ss_res = np.sum((rates - y_pred) ** 2)
        ss_tot = np.sum((rates - y_mean) ** 2)
        r_squared = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
        r_squared = max(0.0, min(1.0, r_squared))

        # Residual std dev for confidence intervals
        residuals = rates - y_pred
        std_dev = float(np.std(residuals))

        # Forecasts: extrapolate from end of series
        # Apply seasonal adjustment to slope (not level)
        adjusted_slope = slope * (1 + seasonal_adj * 2)

        forecast_30 = current_rate + adjusted_slope * 30
        forecast_60 = current_rate + adjusted_slope * 60
        forecast_90 = current_rate + adjusted_slope * 90

        # Cap forecasts at reasonable bounds (don't let linear trend go to zero or infinity)
        min_rate = current_rate * 0.30
        max_rate = current_rate * 3.0
        forecast_30 = max(min_rate, min(max_rate, forecast_30))
        forecast_60 = max(min_rate, min(max_rate, forecast_60))
        forecast_90 = max(min_rate, min(max_rate, forecast_90))

        # Confidence based on R² and data points
        if r_squared >= 0.60 and n >= 30:
            confidence = "High"
        elif r_squared >= 0.30 and n >= 10:
            confidence = "Medium"
        else:
            confidence = "Low"

        # Plain-English methodology
        pct_30d = (forecast_30 - current_rate) / current_rate * 100
        if abs(slope) < 0.5:
            methodology = f"Rate trend is flat. Projecting near-stable rates ({pct_30d:+.0f}% 30d)."
        elif slope > 0:
            methodology = (
                f"Rate trending up ~${slope*7:.0f}/FEU per week. "
                f"Projecting ${forecast_30:,.0f}/FEU in 30d ({pct_30d:+.0f}%). "
                f"Confidence: {confidence} (R²={r_squared:.2f}, {n} data points)."
            )
        else:
            methodology = (
                f"Rate trending down ~${abs(slope)*7:.0f}/FEU per week. "
                f"Projecting ${forecast_30:,.0f}/FEU in 30d ({pct_30d:+.0f}%). "
                f"Confidence: {confidence} (R²={r_squared:.2f}, {n} data points)."
            )

        if seasonal_adj != 0:
            methodology += f" Seasonal adjustment applied: {seasonal_adj:+.0%}."

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
            upper_30d=forecast_30 + std_dev,
            lower_30d=max(0, forecast_30 - std_dev),
            data_points=n,
        )

    except Exception as exc:
        logger.error(f"Forecast failed for {route_id}: {exc}")
        return None
