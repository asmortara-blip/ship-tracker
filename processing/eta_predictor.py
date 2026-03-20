"""
eta_predictor.py — Smart cargo ETA and delay prediction system.

Predicts expected delays and optimal departure timing for shipping routes
based on port congestion, seasonal patterns, and rate momentum.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
from loguru import logger

from routes.route_registry import ROUTES, ROUTES_BY_ID


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class ShipmentETA:
    route_id: str
    origin_port: str
    dest_port: str
    nominal_transit_days: int
    predicted_delay_days: float       # additional days due to congestion/weather/season
    total_eta_days: float
    confidence: float                 # [0, 1]
    delay_drivers: list[str]          # human readable reasons
    optimal_departure_week: str       # e.g. "Week of Mar 24"
    rate_at_optimal: float            # predicted rate at optimal departure
    congestion_risk: str              # "LOW" / "MODERATE" / "HIGH" / "SEVERE"
    cost_savings_vs_now: float        # USD, positive = savings from waiting


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _get_congestion_for_locode(port_results: list, locode: str) -> float:
    """Extract congestion_index for a given locode from port_results list."""
    for result in port_results:
        if isinstance(result, dict):
            if result.get("locode") == locode or result.get("port_locode") == locode:
                return float(result.get("congestion_index", 0.5))
        else:
            pl = getattr(result, "locode", None) or getattr(result, "port_locode", None)
            if pl == locode:
                return float(getattr(result, "congestion_index", 0.5))
    return 0.5


def _origin_delay(congestion: float) -> float:
    """Map origin congestion index to delay days."""
    if congestion > 0.85:
        return 3.0
    if congestion > 0.70:
        return 1.5
    return 0.0


def _dest_delay(congestion: float) -> float:
    """Map destination congestion index to delay days (scaled by 0.7)."""
    if congestion > 0.85:
        return 3.0 * 0.7
    if congestion > 0.70:
        return 1.5 * 0.7
    return 0.0


def _season_delay(ref_date: date | None = None) -> float:
    """Return delay days from seasonal factors."""
    if ref_date is None:
        ref_date = date.today()
    month = ref_date.month
    # Jul-Sep peak season
    if 7 <= month <= 9:
        return 0.5
    # CNY (February)
    if month == 2:
        return 1.0
    return 0.0


def _rate_momentum_delay(
    route_id: str,
    freight_data: dict[str, pd.DataFrame],
) -> float:
    """Return +0.5 delay if rate momentum > 20% over 30 days (demand surge)."""
    df = freight_data.get(route_id)
    if df is None or df.empty or len(df) < 30:
        return 0.0
    try:
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        recent = df.tail(30)
        if len(recent) < 2:
            return 0.0
        rate_col = "rate_usd_per_feu"
        if rate_col not in df.columns:
            return 0.0
        old_rate = float(recent.iloc[0][rate_col])
        new_rate = float(recent.iloc[-1][rate_col])
        if old_rate <= 0:
            return 0.0
        pct_change = (new_rate - old_rate) / old_rate
        if pct_change > 0.20:
            return 0.5
    except Exception as exc:
        logger.debug("Rate momentum delay error for {}: {}", route_id, exc)
    return 0.0


def _current_rate(route_id: str, freight_data: dict[str, pd.DataFrame]) -> float:
    """Return most recent rate for route, or 0.0 if unavailable."""
    df = freight_data.get(route_id)
    if df is None or df.empty:
        return 0.0
    try:
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        rate_col = "rate_usd_per_feu"
        if rate_col not in df.columns:
            return 0.0
        return float(df.iloc[-1][rate_col])
    except Exception as exc:
        logger.debug("Current rate error for {}: {}", route_id, exc)
        return 0.0


def _projected_rate_in_2w(route_id: str, freight_data: dict[str, pd.DataFrame]) -> float:
    """Estimate rate 2 weeks from now using simple linear trend from last 30 days."""
    df = freight_data.get(route_id)
    if df is None or df.empty or len(df) < 7:
        return 0.0
    try:
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        rate_col = "rate_usd_per_feu"
        if rate_col not in df.columns:
            return 0.0
        recent = df.tail(30)
        if len(recent) < 2:
            return float(df.iloc[-1][rate_col])
        x = list(range(len(recent)))
        y = recent[rate_col].tolist()
        n = len(x)
        sx = sum(x)
        sy = sum(y)
        sxx = sum(xi ** 2 for xi in x)
        sxy = sum(xi * yi for xi, yi in zip(x, y))
        denom = n * sxx - sx ** 2
        if denom == 0:
            return y[-1]
        slope = (n * sxy - sx * sy) / denom
        intercept = (sy - slope * sx) / n
        # 14 trading days ahead (approx)
        projected = intercept + slope * (len(recent) - 1 + 14)
        return max(0.0, projected)
    except Exception as exc:
        logger.debug("Projected rate error for {}: {}", route_id, exc)
        return 0.0


def _congestion_risk_label(origin_cong: float, dest_cong: float) -> str:
    """Map combined congestion levels to a risk label."""
    combined = (origin_cong + dest_cong * 0.7) / 1.7
    if combined > 0.80:
        return "SEVERE"
    if combined > 0.65:
        return "HIGH"
    if combined > 0.45:
        return "MODERATE"
    return "LOW"


def _compute_confidence(
    origin_cong: float,
    dest_cong: float,
    has_freight_data: bool,
    has_port_data: bool,
) -> float:
    """Estimate prediction confidence based on data availability and signal clarity."""
    base = 0.70
    if has_freight_data:
        base += 0.10
    if has_port_data:
        base += 0.10
    # Lower confidence when congestion is very high (more volatile)
    volatility_penalty = max(0.0, (origin_cong + dest_cong) / 2.0 - 0.70) * 0.20
    return _clamp(base - volatility_penalty)


def _optimal_departure_week(
    port_results: list,
    route_id: str,
    freight_data: dict[str, pd.DataFrame],
) -> tuple[str, float]:
    """
    Look ahead 4 weeks. Pick the week with lowest predicted congestion at origin.

    Returns (week_label, predicted_rate_at_optimal).
    """
    from routes.route_registry import ROUTES_BY_ID
    route = ROUTES_BY_ID.get(route_id)
    origin_locode = route.origin_locode if route else ""

    origin_cong = _get_congestion_for_locode(port_results, origin_locode)

    # Simple sinusoidal seasonal model for 4-week forward congestion
    today = date.today()
    best_week = 0
    best_cong = float("inf")
    for week_offset in range(4):
        future_date = today + timedelta(weeks=week_offset)
        month = future_date.month
        # Seasonal modifier: sin wave peaking in Aug-Sep
        day_of_year = future_date.timetuple().tm_yday
        seasonal_wave = 0.05 * math.sin(2 * math.pi * (day_of_year - 60) / 365)
        # Project congestion with slight mean reversion
        projected = origin_cong * (0.92 ** week_offset) + 0.5 * (1 - 0.92 ** week_offset) + seasonal_wave
        projected = _clamp(projected)
        if projected < best_cong:
            best_cong = projected
            best_week = week_offset

    optimal_date = today + timedelta(weeks=best_week)
    # Format as "Week of Mon DD"
    week_label = "Week of " + optimal_date.strftime("%b %-d")

    # Rate at optimal: project rate forward
    cur_rate = _current_rate(route_id, freight_data)
    proj_2w = _projected_rate_in_2w(route_id, freight_data)
    if best_week == 0:
        optimal_rate = cur_rate
    elif best_week <= 2:
        optimal_rate = proj_2w if proj_2w > 0 else cur_rate
    else:
        # Extrapolate further
        if cur_rate > 0 and proj_2w > 0:
            slope_per_week = (proj_2w - cur_rate) / 2.0
            optimal_rate = max(0.0, cur_rate + slope_per_week * best_week)
        else:
            optimal_rate = cur_rate

    return week_label, optimal_rate


def _cost_savings(
    route_id: str,
    freight_data: dict[str, pd.DataFrame],
) -> float:
    """
    If rate is falling, waiting ~2 weeks saves (current_rate - projected_rate) per FEU.
    Returns positive value if savings exist, negative if costs more to wait.
    """
    cur = _current_rate(route_id, freight_data)
    proj = _projected_rate_in_2w(route_id, freight_data)
    if cur <= 0:
        return 0.0
    return cur - proj  # positive = cheaper later


def _build_delay_drivers(
    origin_cong: float,
    dest_cong: float,
    season_delay: float,
    rate_delay: float,
    origin_locode: str,
    dest_locode: str,
) -> list[str]:
    drivers: list[str] = []
    if origin_cong > 0.85:
        drivers.append("Severe origin port congestion at " + origin_locode + " (+3.0 days)")
    elif origin_cong > 0.70:
        drivers.append("Elevated origin port congestion at " + origin_locode + " (+1.5 days)")
    if dest_cong > 0.85:
        drivers.append("Severe destination port congestion at " + dest_locode + " (+2.1 days)")
    elif dest_cong > 0.70:
        drivers.append("Elevated destination port congestion at " + dest_locode + " (+1.05 days)")
    if season_delay > 0:
        if season_delay >= 1.0:
            drivers.append("Chinese New Year slowdown — factory shutdowns add +1.0 day")
        else:
            drivers.append("Peak season demand surge (Jul-Sep) adds +0.5 days")
    if rate_delay > 0:
        drivers.append("Rate momentum >20% — demand surge likely adding +0.5 days")
    if not drivers:
        drivers.append("No significant delay factors detected")
    return drivers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def predict_eta(
    route_id: str,
    port_results: list,
    freight_data: dict[str, pd.DataFrame],
    macro_data: dict,
) -> ShipmentETA:
    """Predict ETA and delay for a single shipping route.

    Parameters
    ----------
    route_id:     Registered route ID (from route_registry).
    port_results: List of port demand/efficiency results with congestion_index.
    freight_data: Dict route_id -> DataFrame with 'date' and 'rate_usd_per_feu'.
    macro_data:   Dict of macro indicators (keys: 'BDI_rising', 'PMI', etc.).

    Returns
    -------
    ShipmentETA dataclass instance.
    """
    route = ROUTES_BY_ID.get(route_id)
    if route is None:
        logger.warning("Route '{}' not found in registry — using fallback values", route_id)
        nominal_days = 14
        origin_locode = "UNKNOWN"
        dest_locode = "UNKNOWN"
    else:
        nominal_days = route.transit_days
        origin_locode = route.origin_locode
        dest_locode = route.dest_locode

    origin_cong = _get_congestion_for_locode(port_results, origin_locode)
    dest_cong = _get_congestion_for_locode(port_results, dest_locode)

    o_delay = _origin_delay(origin_cong)
    d_delay = _dest_delay(dest_cong)
    s_delay = _season_delay()
    r_delay = _rate_momentum_delay(route_id, freight_data)

    total_delay = round(o_delay + d_delay + s_delay + r_delay, 2)
    total_eta = round(nominal_days + total_delay, 2)

    has_freight = (freight_data.get(route_id) is not None
                   and not freight_data.get(route_id, pd.DataFrame()).empty)
    has_port = any(
        (getattr(r, "locode", None) or getattr(r, "port_locode", None)) == origin_locode
        for r in port_results
    )

    confidence = _compute_confidence(origin_cong, dest_cong, has_freight, has_port)
    congestion_risk = _congestion_risk_label(origin_cong, dest_cong)
    delay_drivers = _build_delay_drivers(
        origin_cong, dest_cong, s_delay, r_delay, origin_locode, dest_locode
    )

    opt_week, opt_rate = _optimal_departure_week(port_results, route_id, freight_data)
    savings = _cost_savings(route_id, freight_data)

    logger.debug(
        "ETA predict {}: nominal={}d delay={}d total={}d risk={} confidence={:.2f}",
        route_id, nominal_days, total_delay, total_eta, congestion_risk, confidence,
    )

    return ShipmentETA(
        route_id=route_id,
        origin_port=origin_locode,
        dest_port=dest_locode,
        nominal_transit_days=nominal_days,
        predicted_delay_days=total_delay,
        total_eta_days=total_eta,
        confidence=round(confidence, 3),
        delay_drivers=delay_drivers,
        optimal_departure_week=opt_week,
        rate_at_optimal=round(opt_rate, 2),
        congestion_risk=congestion_risk,
        cost_savings_vs_now=round(savings, 2),
    )


def predict_all_routes(
    port_results: list,
    freight_data: dict[str, pd.DataFrame],
    macro_data: dict,
) -> list[ShipmentETA]:
    """Predict ETA for all registered shipping routes.

    Returns list of ShipmentETA sorted by predicted_delay_days descending.
    """
    results: list[ShipmentETA] = []
    for route in ROUTES:
        try:
            eta = predict_eta(route.id, port_results, freight_data, macro_data)
            results.append(eta)
        except Exception as exc:
            logger.error("ETA prediction failed for route '{}': {}", route.id, exc)

    results.sort(key=lambda e: e.predicted_delay_days, reverse=True)
    logger.info("ETA prediction complete: {} routes processed", len(results))
    return results


def get_best_departure_windows(etas: list[ShipmentETA]) -> list[dict]:
    """Return top 3 routes with significant savings from optimal departure timing.

    Returns list of dicts with keys: route_id, origin_port, dest_port,
    optimal_departure_week, cost_savings_vs_now, congestion_risk.
    Only includes routes where cost_savings_vs_now > 0.
    """
    candidates = [e for e in etas if e.cost_savings_vs_now > 0]
    candidates.sort(key=lambda e: e.cost_savings_vs_now, reverse=True)
    top3 = candidates[:3]

    windows: list[dict] = []
    for eta in top3:
        windows.append({
            "route_id": eta.route_id,
            "origin_port": eta.origin_port,
            "dest_port": eta.dest_port,
            "optimal_departure_week": eta.optimal_departure_week,
            "cost_savings_vs_now": eta.cost_savings_vs_now,
            "rate_at_optimal": eta.rate_at_optimal,
            "congestion_risk": eta.congestion_risk,
            "predicted_delay_days": eta.predicted_delay_days,
        })

    logger.debug("Best departure windows: {} routes with positive savings", len(windows))
    return windows
