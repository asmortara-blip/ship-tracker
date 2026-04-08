"""rate_analytics.py — Advanced freight rate analytics and decomposition.

Provides:
  1. Rate regime detection (boom / normal / bust)
  2. Seasonal adjustment factors
  3. Rate volatility clustering
  4. Spread analysis between routes
  5. Rate percentile ranking (current vs historical)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger


def _safe_series(data: dict, key: str) -> pd.Series | None:
    """Extract a clean numeric Series from freight_data."""
    val = data.get(key)
    if val is None:
        return None
    if isinstance(val, pd.DataFrame):
        if val.empty:
            return None
        return val.iloc[:, 0].dropna()
    if isinstance(val, pd.Series):
        return val.dropna()
    return None


def compute_rate_regime(freight_data: dict) -> dict:
    """Detect the current freight rate regime across all routes.

    Returns regime classification and supporting metrics for each route.
    """
    regimes = {}

    for route_key, route_data in freight_data.items():
        if not isinstance(route_data, (pd.DataFrame, pd.Series)):
            continue

        series = _safe_series(freight_data, route_key)
        if series is None or len(series) < 30:
            continue

        try:
            current = float(series.iloc[-1])
            mean_val = float(series.mean())
            std_val = float(series.std())
            median_val = float(series.median())

            # Z-score
            z_score = (current - mean_val) / std_val if std_val > 0 else 0

            # Percentile rank
            percentile = float((series < current).sum() / len(series) * 100)

            # Regime classification
            if z_score > 1.5:
                regime = "Boom"
                regime_desc = "Rates significantly above historical average"
            elif z_score > 0.5:
                regime = "Above Average"
                regime_desc = "Rates moderately elevated"
            elif z_score > -0.5:
                regime = "Normal"
                regime_desc = "Rates within normal trading range"
            elif z_score > -1.5:
                regime = "Below Average"
                regime_desc = "Rates moderately depressed"
            else:
                regime = "Bust"
                regime_desc = "Rates significantly below historical average"

            # Trend (30-day)
            if len(series) >= 30:
                recent = series.iloc[-30:]
                x = np.arange(len(recent))
                slope = np.polyfit(x, recent.values, 1)[0]
                trend_pct = slope / mean_val * 100 * 30 if mean_val != 0 else 0
            else:
                trend_pct = 0

            # Volatility (annualized)
            returns = series.pct_change().dropna()
            vol_daily = float(returns.std()) if len(returns) > 5 else 0
            vol_annual = vol_daily * np.sqrt(252) * 100

            # Min/max in period
            period_min = float(series.min())
            period_max = float(series.max())
            range_position = (current - period_min) / (period_max - period_min) * 100 if period_max != period_min else 50

            regimes[route_key] = {
                "current": current,
                "mean": mean_val,
                "median": median_val,
                "std": std_val,
                "z_score": z_score,
                "percentile": percentile,
                "regime": regime,
                "regime_desc": regime_desc,
                "trend_30d_pct": trend_pct,
                "volatility_annual": vol_annual,
                "period_min": period_min,
                "period_max": period_max,
                "range_position": range_position,
                "n_observations": len(series),
            }
        except Exception as exc:
            logger.warning(f"Rate regime computation failed for {route_key}: {exc}")

    # Overall market regime
    if regimes:
        avg_z = np.mean([r["z_score"] for r in regimes.values()])
        avg_pct = np.mean([r["percentile"] for r in regimes.values()])
        avg_vol = np.mean([r["volatility_annual"] for r in regimes.values()])

        if avg_z > 1.0:
            market_regime = "Bull Market"
        elif avg_z > 0:
            market_regime = "Moderate Growth"
        elif avg_z > -1.0:
            market_regime = "Moderate Contraction"
        else:
            market_regime = "Bear Market"
    else:
        market_regime = "Insufficient Data"
        avg_z = 0
        avg_pct = 50
        avg_vol = 0

    return {
        "routes": regimes,
        "market_regime": market_regime,
        "avg_z_score": avg_z,
        "avg_percentile": avg_pct,
        "avg_volatility": avg_vol,
        "n_routes_analyzed": len(regimes),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def compute_rate_spreads(freight_data: dict) -> list[dict]:
    """Compute rate spreads between route pairs.

    Useful for identifying arbitrage opportunities and market dislocations.
    """
    route_keys = []
    for key, val in freight_data.items():
        series = _safe_series(freight_data, key)
        if series is not None and len(series) >= 10:
            route_keys.append(key)

    spreads = []
    for i, key1 in enumerate(route_keys):
        for key2 in route_keys[i+1:]:
            s1 = _safe_series(freight_data, key1)
            s2 = _safe_series(freight_data, key2)
            if s1 is None or s2 is None:
                continue

            # Align on dates
            combined = pd.concat([s1, s2], axis=1, join="inner")
            if len(combined) < 10:
                continue

            combined.columns = ["route1", "route2"]
            spread = combined["route1"] - combined["route2"]

            current_spread = float(spread.iloc[-1])
            mean_spread = float(spread.mean())
            std_spread = float(spread.std())
            z_spread = (current_spread - mean_spread) / std_spread if std_spread > 0 else 0

            # Correlation
            corr = float(combined["route1"].corr(combined["route2"]))

            spreads.append({
                "route1": key1,
                "route2": key2,
                "current_spread": current_spread,
                "mean_spread": mean_spread,
                "z_score": z_spread,
                "correlation": corr,
                "signal": "Wide" if z_spread > 1.5 else ("Narrow" if z_spread < -1.5 else "Normal"),
            })

    spreads.sort(key=lambda x: abs(x["z_score"]), reverse=True)
    return spreads[:15]  # Top 15 most dislocated


def compute_seasonal_factors(freight_data: dict) -> dict:
    """Compute monthly seasonal factors for each route.

    Returns dict of route -> 12-month seasonal index (100 = average).
    """
    seasonal = {}

    for route_key, route_data in freight_data.items():
        series = _safe_series(freight_data, route_key)
        if series is None or len(series) < 60:  # Need ~2 months minimum
            continue

        try:
            if not isinstance(series.index, pd.DatetimeIndex):
                continue

            monthly = series.groupby(series.index.month).mean()
            overall_mean = series.mean()
            if overall_mean == 0:
                continue

            factors = {}
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            for m in range(1, 13):
                if m in monthly.index:
                    factors[months[m-1]] = float(monthly[m] / overall_mean * 100)
                else:
                    factors[months[m-1]] = 100.0

            seasonal[route_key] = factors
        except Exception as exc:
            logger.debug(f"Seasonal computation failed for {route_key}: {exc}")

    return seasonal
