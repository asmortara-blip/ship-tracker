from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from loguru import logger

from engine.signals import SignalComponent, direction_from_score


@dataclass
class FreightVolatilityReport:
    route_id: str
    route_name: str
    volatility_30d: float
    volatility_90d: float
    volatility_percentile: float
    momentum_7d: float
    momentum_30d: float
    momentum_acceleration: float
    zscore_from_mean: float
    mean_reversion_signal: str
    regime: str
    signal_strength: float
    signal_component: SignalComponent


def analyze_freight_volatility(
    freight_data: dict,
    route_id: str,
    route_name: str = "",
) -> FreightVolatilityReport | None:
    df = freight_data.get(route_id)
    if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
        return None
    df = df.sort_values("date").copy()
    if len(df) < 10:
        return None

    rates = df["rate_usd_per_feu"]
    pct_chg = rates.pct_change().dropna()

    vol_30d = float(pct_chg.rolling(30).std().iloc[-1]) if len(pct_chg) >= 30 else float(pct_chg.std())
    vol_90d = float(pct_chg.rolling(90).std().iloc[-1]) if len(pct_chg) >= 90 else float(pct_chg.std())

    # Volatility percentile vs own history
    rolling_vol = pct_chg.rolling(30).std().dropna()
    if len(rolling_vol) > 1:
        from scipy import stats as sp_stats
        vol_pct = float(sp_stats.percentileofscore(rolling_vol, vol_30d)) / 100.0
    else:
        vol_pct = 0.5

    current = float(rates.iloc[-1])
    rate_7d_ago = float(rates.iloc[-8]) if len(rates) >= 8 else current
    rate_30d_ago = float(rates.iloc[-31]) if len(rates) >= 31 else current

    mom_7d = (current - rate_7d_ago) / rate_7d_ago if rate_7d_ago != 0 else 0.0
    mom_30d = (current - rate_30d_ago) / rate_30d_ago if rate_30d_ago != 0 else 0.0
    mom_accel = mom_7d - mom_30d

    mean_90d = float(rates.tail(90).mean())
    std_90d = float(rates.tail(90).std()) if len(rates) >= 5 else 1.0
    zscore = (current - mean_90d) / std_90d if std_90d > 0 else 0.0

    if zscore > 1.5:
        mr_signal = "OVERBOUGHT"
    elif zscore < -1.5:
        mr_signal = "OVERSOLD"
    else:
        mr_signal = "NEUTRAL"

    if abs(zscore) > 2.0:
        regime = "BREAKOUT"
    elif mom_30d > 0.10 and mom_7d > 0:
        regime = "TRENDING_UP"
    elif mom_30d < -0.10 and mom_7d < 0:
        regime = "TRENDING_DOWN"
    else:
        regime = "RANGING"

    sig_strength = min(1.0, abs(mom_30d) * 0.4 + min(abs(zscore) / 3, 1.0) * 0.4 + vol_pct * 0.2)
    direction = "bullish" if mom_30d > 0.03 else ("bearish" if mom_30d < -0.03 else "neutral")

    sc = SignalComponent(
        name=f"Freight Momentum ({route_id})",
        value=sig_strength,
        weight=0.20,
        label=regime,
        direction=direction,
    )

    return FreightVolatilityReport(
        route_id=route_id,
        route_name=route_name or route_id,
        volatility_30d=vol_30d,
        volatility_90d=vol_90d,
        volatility_percentile=vol_pct,
        momentum_7d=mom_7d,
        momentum_30d=mom_30d,
        momentum_acceleration=mom_accel,
        zscore_from_mean=zscore,
        mean_reversion_signal=mr_signal,
        regime=regime,
        signal_strength=sig_strength,
        signal_component=sc,
    )


def analyze_all_routes_volatility(freight_data: dict) -> dict[str, FreightVolatilityReport]:
    results = {}
    for route_id in freight_data:
        try:
            r = analyze_freight_volatility(freight_data, route_id)
            if r:
                results[route_id] = r
        except Exception as exc:
            logger.debug(f"Volatility analysis failed for {route_id}: {exc}")
    return results


def get_breakout_alerts(reports: dict) -> list[FreightVolatilityReport]:
    return sorted(
        [r for r in reports.values() if r.regime == "BREAKOUT"],
        key=lambda r: abs(r.zscore_from_mean),
        reverse=True,
    )


def get_trending_routes(reports: dict, direction: str = "up") -> list[FreightVolatilityReport]:
    regime = "TRENDING_UP" if direction == "up" else "TRENDING_DOWN"
    return sorted(
        [r for r in reports.values() if r.regime == regime],
        key=lambda r: r.signal_strength,
        reverse=True,
    )


def get_volatility_summary(reports: dict) -> dict:
    if not reports:
        return {
            "avg_volatility": 0.0, "high_volatility_routes": [],
            "breakout_count": 0, "trending_up_count": 0, "trending_down_count": 0,
            "market_regime": "RANGING",
        }
    vols = [r.volatility_30d for r in reports.values()]
    avg_vol = float(np.mean(vols)) if vols else 0.0
    high_vol = [r.route_id for r in reports.values() if r.volatility_percentile > 0.75]
    from collections import Counter
    regime_counts = Counter(r.regime for r in reports.values())
    market_regime = regime_counts.most_common(1)[0][0] if regime_counts else "RANGING"
    return {
        "avg_volatility": avg_vol,
        "high_volatility_routes": high_vol,
        "breakout_count": sum(1 for r in reports.values() if r.regime == "BREAKOUT"),
        "trending_up_count": sum(1 for r in reports.values() if r.regime == "TRENDING_UP"),
        "trending_down_count": sum(1 for r in reports.values() if r.regime == "TRENDING_DOWN"),
        "market_regime": market_regime,
    }
