from __future__ import annotations

import pandas as pd
from loguru import logger

from routes.route_registry import ROUTES, ROUTES_BY_ID
from utils.helpers import trend_label


def compute_rate_momentum(
    route_id: str,
    freight_data: dict[str, pd.DataFrame],
    lookback_days: int = 90,
) -> float:
    """Compute rate momentum score [0, 1] for a route.

    Score > 0.5: current rate above rolling average (bullish)
    Score < 0.5: current rate below rolling average (bearish)
    Score = 0.5: at average or no data
    """
    df = freight_data.get(route_id)
    if df is None or df.empty or len(df) < 2:
        return 0.5

    df = df.sort_values("date")
    rates = df["rate_usd_per_feu"].dropna()

    if len(rates) < 2:
        return 0.5

    current_rate = float(rates.iloc[-1])
    rolling_avg = float(rates.tail(lookback_days).mean())

    if rolling_avg == 0:
        return 0.5

    # Map ratio [0.5, 1.5] → [0, 1]
    ratio = current_rate / rolling_avg
    score = (ratio - 0.5) / 1.0
    return max(0.0, min(1.0, score))


def compute_rate_pct_change(
    route_id: str,
    freight_data: dict[str, pd.DataFrame],
    days: int = 30,
) -> float:
    """Return percentage rate change over the last N days."""
    df = freight_data.get(route_id)
    if df is None or len(df) < 2:
        return 0.0

    df = df.sort_values("date")
    recent = df.tail(days + 1)

    if len(recent) < 2:
        return 0.0

    start = recent["rate_usd_per_feu"].iloc[0]
    end = recent["rate_usd_per_feu"].iloc[-1]

    if start == 0:
        return 0.0

    return (end - start) / start


def get_all_route_rates(
    freight_data: dict[str, pd.DataFrame],
) -> dict[str, dict]:
    """Return a summary dict for all routes: {route_id: {rate, pct_30d, trend, momentum}}."""
    summary = {}
    for route in ROUTES:
        df = freight_data.get(route.id)
        if df is not None and not df.empty and "rate_usd_per_feu" in df.columns:
            _rates = df["rate_usd_per_feu"].dropna()
            current_rate = float(_rates.iloc[-1]) if not _rates.empty else 0.0
        else:
            current_rate = 0.0
        pct_30d = compute_rate_pct_change(route.id, freight_data, 30)
        momentum = compute_rate_momentum(route.id, freight_data)
        summary[route.id] = {
            "current_rate": current_rate,
            "pct_30d": pct_30d,
            "trend": trend_label(pct_30d),
            "momentum_score": momentum,
        }
    return summary
