"""
Smart Booking Optimizer

Helps importers/exporters find the optimal time and route to book container shipping.
Scores routes by priority (COST / SPEED / RELIABILITY), computes optimal departure
windows, estimates full logistics cost breakdowns, and signals market timing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
from loguru import logger

from routes.route_registry import ROUTES, ROUTES_BY_ID
from routes.rate_estimator import compute_rate_pct_change, compute_rate_momentum
from ports.port_registry import PORTS_BY_LOCODE


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BookingScenario:
    origin_locode: str
    dest_locode: str
    cargo_feu: int
    cargo_category: str       # electronics / machinery / apparel / food / chemicals / other
    desired_arrival: str      # "YYYY-MM-DD"
    flexibility_days: int     # +/- N days on arrival
    priority: str             # "COST" / "SPEED" / "RELIABILITY"


@dataclass
class BookingRecommendation:
    scenario: BookingScenario
    recommended_route_id: str
    recommended_departure: str      # "YYYY-MM-DD"
    estimated_rate_per_feu: float
    total_cost_usd: float
    transit_days: int
    estimated_arrival: str          # "YYYY-MM-DD"
    savings_vs_spot: float          # USD saved vs booking today at spot
    confidence: float               # 0-1
    key_risks: list[str]
    alternative_routes: list[dict]  # up to 2 alternatives {route_id, name, rate, days, total_cost}
    carrier_recommendation: str     # "MSC"/"Maersk"/"COSCO"/"Evergreen"/"ONE"
    booking_urgency: str            # "BOOK_NOW"/"WAIT_1_WEEK"/"WAIT_2_WEEKS"/"FLEXIBLE"


# ---------------------------------------------------------------------------
# Carrier assignments per route (realistic hardcoded mappings)
# ---------------------------------------------------------------------------

_CARRIER_MAP: dict[str, str] = {
    "transpacific_eb":      "Maersk",
    "transpacific_wb":      "Maersk",
    "asia_europe":          "MSC",
    "transatlantic":        "MSC",
    "sea_transpacific_eb":  "ONE",
    "ningbo_europe":        "COSCO",
    "middle_east_to_europe":"Evergreen",
    "middle_east_to_asia":  "COSCO",
    "south_asia_to_europe": "MSC",
    "intra_asia_china_sea": "COSCO",
    "intra_asia_china_japan":"ONE",
    "china_south_america":  "MSC",
    "europe_south_america": "Maersk",
    "med_hub_to_asia":      "Evergreen",
    "north_africa_to_europe":"MSC",
    "us_east_south_america":"ONE",
    "longbeach_to_asia":    "Maersk",
}

# Base rate estimates per route (USD/FEU) — used when live data is unavailable
_BASE_RATES: dict[str, float] = {
    "transpacific_eb":       3800.0,
    "transpacific_wb":       1200.0,
    "asia_europe":           4200.0,
    "transatlantic":         2800.0,
    "sea_transpacific_eb":   3600.0,
    "ningbo_europe":         4000.0,
    "middle_east_to_europe": 2600.0,
    "middle_east_to_asia":   1400.0,
    "south_asia_to_europe":  2900.0,
    "intra_asia_china_sea":   650.0,
    "intra_asia_china_japan": 480.0,
    "china_south_america":   3200.0,
    "europe_south_america":  2200.0,
    "med_hub_to_asia":       2400.0,
    "north_africa_to_europe": 900.0,
    "us_east_south_america": 1800.0,
    "longbeach_to_asia":     1300.0,
}

# Seasonal dip windows (month → typical cheaper month offset in days)
_SEASONAL_DIP_DAYS: dict[int, int] = {
    1: 30,    # Jan — post CNY dip coming in ~30 days
    2: 14,    # Feb — CNY trough, brief dip
    3: 45,    # Mar — Post-CNY recovery means better rates in ~6 weeks
    4: 60,    # Apr — next dip is post-holiday lull, ~2 months away
    5: 75,    # May — heading into peak build; dip is post-peak
    6: 90,    # Jun — peak season approaching; wait will cost more
    7: 120,   # Jul — peak season; dip not until Dec
    8: 105,   # Aug — peak season; dip ~Nov
    9: 75,    # Sep — peak season; dip ~Nov
    10: 45,   # Oct — holiday peak; dip comes Nov 15+
    11: 14,   # Nov — post-holiday lull imminent
    12: 21,   # Dec — post-lull; CNY prep surge soon
}

# Cargo category → risk factor (higher = more risk of damage/theft claims)
_CARGO_RISK: dict[str, float] = {
    "electronics":  0.85,
    "machinery":    0.40,
    "apparel":      0.25,
    "food":         0.60,
    "chemicals":    0.70,
    "other":        0.30,
}

# Cargo insurance rate as fraction of cargo value per FEU
_INSURANCE_RATE: dict[str, float] = {
    "electronics":  0.0055,
    "machinery":    0.0035,
    "apparel":      0.0025,
    "food":         0.0045,
    "chemicals":    0.0060,
    "other":        0.0030,
}

# Approximate cargo value per FEU by category (USD) — for insurance computation
_CARGO_VALUE_PER_FEU: dict[str, float] = {
    "electronics":  320_000.0,
    "machinery":    180_000.0,
    "apparel":       85_000.0,
    "food":          70_000.0,
    "chemicals":    110_000.0,
    "other":         90_000.0,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_current_rate(route_id: str, freight_data: dict[str, pd.DataFrame]) -> float:
    """Return the most recent freight rate for a route, falling back to base rate."""
    df = freight_data.get(route_id)
    if df is not None and not df.empty and "rate_usd_per_feu" in df.columns:
        val = df["rate_usd_per_feu"].dropna()
        if not val.empty:
            return float(val.iloc[-1])
    return _BASE_RATES.get(route_id, 2000.0)


def _get_6m_avg_rate(route_id: str, freight_data: dict[str, pd.DataFrame]) -> float:
    """Return the 6-month rolling average rate for a route."""
    df = freight_data.get(route_id)
    if df is not None and not df.empty and "rate_usd_per_feu" in df.columns:
        rates = df["rate_usd_per_feu"].dropna()
        if len(rates) >= 2:
            tail = rates.tail(180)
            return float(tail.mean())
    return _BASE_RATES.get(route_id, 2000.0)


def _get_52w_range(route_id: str, freight_data: dict[str, pd.DataFrame]) -> tuple[float, float]:
    """Return (min, max) rate over 52 weeks."""
    df = freight_data.get(route_id)
    if df is not None and not df.empty and "rate_usd_per_feu" in df.columns:
        rates = df["rate_usd_per_feu"].dropna().tail(365)
        if len(rates) >= 2:
            return float(rates.min()), float(rates.max())
    base = _BASE_RATES.get(route_id, 2000.0)
    return base * 0.70, base * 1.45


def _congestion_score(route_id: str, route_results: list) -> float:
    """Extract congestion component from route_results (origin_congestion)."""
    for rr in route_results:
        if getattr(rr, "route_id", None) == route_id:
            return float(getattr(rr, "origin_congestion", 0.5))
    return 0.5


def _match_routes(scenario: BookingScenario) -> list:
    """Return routes whose origin or dest region matches scenario ports."""
    origin_port = PORTS_BY_LOCODE.get(scenario.origin_locode)
    dest_port = PORTS_BY_LOCODE.get(scenario.dest_locode)

    if origin_port is None or dest_port is None:
        logger.warning(
            "Unknown locode: origin={} dest={}".format(
                scenario.origin_locode, scenario.dest_locode
            )
        )
        return list(ROUTES)

    origin_region = origin_port.region
    dest_region = dest_port.region

    matched = [
        r for r in ROUTES
        if r.origin_region == origin_region and r.dest_region == dest_region
    ]
    if not matched:
        # Fall back to partial match on either side
        matched = [
            r for r in ROUTES
            if r.origin_region == origin_region or r.dest_region == dest_region
        ]
    if not matched:
        matched = list(ROUTES)

    logger.debug(
        "Matched {} routes for {} -> {}".format(
            len(matched), scenario.origin_locode, scenario.dest_locode
        )
    )
    return matched


def _score_route_for_priority(
    route,
    priority: str,
    rate: float,
    congestion: float,
) -> float:
    """Return a score (higher = better) for a route given the user's priority."""
    if priority == "COST":
        # Lower rate is better; invert and normalize against max plausible rate ($8000)
        return 1.0 - min(rate / 8000.0, 1.0)
    elif priority == "SPEED":
        # Fewer transit days is better; invert against max 40 days
        return 1.0 - min(route.transit_days / 40.0, 1.0)
    else:  # RELIABILITY
        # Lower congestion is better reliability
        return 1.0 - congestion


def _compute_optimal_departure(
    scenario: BookingScenario,
    transit_days: int,
) -> str:
    """Compute optimal departure date within flexibility window."""
    desired = datetime.strptime(scenario.desired_arrival, "%Y-%m-%d").date()
    # Ideal departure = desired_arrival minus transit_days
    ideal_dep = desired - timedelta(days=transit_days)

    today = date.today()
    # Clamp: cannot depart in the past
    if ideal_dep < today + timedelta(days=7):
        ideal_dep = today + timedelta(days=7)

    return ideal_dep.strftime("%Y-%m-%d")


def _compute_estimated_arrival(departure_str: str, transit_days: int) -> str:
    dep = datetime.strptime(departure_str, "%Y-%m-%d").date()
    arr = dep + timedelta(days=transit_days)
    return arr.strftime("%Y-%m-%d")


def _booking_urgency(
    route_id: str,
    freight_data: dict[str, pd.DataFrame],
    macro_data: dict,
) -> str:
    """Determine booking urgency from rate trend signal."""
    pct_30d = compute_rate_pct_change(route_id, freight_data, days=30)
    momentum = compute_rate_momentum(route_id, freight_data)

    if pct_30d > 0.08 or momentum > 0.72:
        return "BOOK_NOW"
    elif pct_30d > 0.03 or momentum > 0.60:
        return "WAIT_1_WEEK"
    elif pct_30d < -0.05 or momentum < 0.38:
        return "WAIT_2_WEEKS"
    else:
        return "FLEXIBLE"


def _build_risks(
    scenario: BookingScenario,
    route,
    rate: float,
    congestion: float,
    urgency: str,
    macro_data: dict,
) -> list[str]:
    """Generate key risk bullet points for the recommendation."""
    risks: list[str] = []

    if congestion > 0.70:
        risks.append(
            "High port congestion at origin ({}) may delay vessel loading".format(
                route.origin_locode
            )
        )
    if scenario.cargo_category == "electronics":
        risks.append("Electronics require temperature-controlled stowage; confirm reefer availability")
    if scenario.cargo_category == "chemicals":
        risks.append("Hazmat documentation (IMDG) required — allow 5+ extra days for compliance")
    if scenario.cargo_category == "food":
        risks.append("Perishable cargo requires pre-cooling inspection; transit delay risk is elevated")

    if urgency == "BOOK_NOW":
        risks.append("Rates are trending upward — waiting will likely increase total cost")
    elif urgency == "WAIT_2_WEEKS":
        risks.append("Rates are softening — early booking may overpay vs. spot in 2 weeks")

    month = date.today().month
    if month in (7, 8, 9, 10):
        risks.append("Peak season (Jul-Oct): container availability tighter; book early to secure space")
    if month in (1, 2):
        risks.append("Chinese New Year period: Asia origin volumes disrupted for 2-4 weeks")

    if route.transit_days > 25:
        risks.append(
            "Long transit ({} days) increases exposure to rate volatility and schedule changes".format(
                route.transit_days
            )
        )

    if not risks:
        risks.append("No significant risks identified for this lane and timing")

    return risks[:5]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def optimize_booking(
    scenario: BookingScenario,
    route_results: list,
    freight_data: dict[str, pd.DataFrame],
    macro_data: dict,
) -> BookingRecommendation:
    """
    Find the optimal route, departure date, carrier, and booking timing for a scenario.

    Args:
        scenario:      BookingScenario with origin/dest, cargo details, priority.
        route_results: List of RouteOpportunity objects from routes.optimizer.
        freight_data:  Dict of route_id -> DataFrame with rate_usd_per_feu column.
        macro_data:    Dict of macro series DataFrames.

    Returns:
        BookingRecommendation with full details.
    """
    logger.info(
        "Optimizing booking: {} -> {} ({} FEU, priority={})".format(
            scenario.origin_locode,
            scenario.dest_locode,
            scenario.cargo_feu,
            scenario.priority,
        )
    )

    matched = _match_routes(scenario)

    # Score each matched route
    scored: list[tuple[float, object]] = []
    for route in matched:
        rate = _get_current_rate(route.id, freight_data)
        cong = _congestion_score(route.id, route_results)
        sc = _score_route_for_priority(route, scenario.priority, rate, cong)
        scored.append((sc, route))

    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        logger.error("No routes available for booking optimization")
        # Return a stub recommendation
        return BookingRecommendation(
            scenario=scenario,
            recommended_route_id="transpacific_eb",
            recommended_departure=date.today().strftime("%Y-%m-%d"),
            estimated_rate_per_feu=3800.0,
            total_cost_usd=3800.0 * scenario.cargo_feu,
            transit_days=14,
            estimated_arrival=(date.today() + timedelta(days=14)).strftime("%Y-%m-%d"),
            savings_vs_spot=0.0,
            confidence=0.3,
            key_risks=["Insufficient route data for full optimization"],
            alternative_routes=[],
            carrier_recommendation="Maersk",
            booking_urgency="FLEXIBLE",
        )

    best_score, best_route = scored[0]

    # Primary route details
    best_rate = _get_current_rate(best_route.id, freight_data)
    spot_rate = best_rate  # spot = current market
    best_congestion = _congestion_score(best_route.id, route_results)

    departure_str = _compute_optimal_departure(scenario, best_route.transit_days)
    arrival_str = _compute_estimated_arrival(departure_str, best_route.transit_days)

    # Estimate forward rate within flexibility window
    pct_trend = compute_rate_pct_change(best_route.id, freight_data, days=30)
    dep_date = datetime.strptime(departure_str, "%Y-%m-%d").date()
    days_out = max(0, (dep_date - date.today()).days)
    # Extrapolate monthly trend linearly over booking horizon
    monthly_trend = pct_trend  # already ~30d
    forecast_rate = best_rate * (1.0 + monthly_trend * (days_out / 30.0))
    forecast_rate = max(forecast_rate, best_rate * 0.60)

    total_cost = forecast_rate * scenario.cargo_feu
    savings_vs_spot = (spot_rate - forecast_rate) * scenario.cargo_feu

    # Confidence: based on data availability and route match quality
    has_live_data = (
        freight_data.get(best_route.id) is not None
        and not freight_data.get(best_route.id, pd.DataFrame()).empty
    )
    route_exact_match = len([
        r for r in matched
        if r.origin_locode == scenario.origin_locode or r.dest_locode == scenario.dest_locode
    ]) > 0
    confidence = 0.75 if has_live_data else 0.45
    if route_exact_match:
        confidence = min(confidence + 0.10, 0.95)
    if scenario.flexibility_days >= 7:
        confidence = min(confidence + 0.05, 0.95)

    urgency = _booking_urgency(best_route.id, freight_data, macro_data)
    risks = _build_risks(scenario, best_route, forecast_rate, best_congestion, urgency, macro_data)
    carrier = _CARRIER_MAP.get(best_route.id, "MSC")

    # Alternative routes (up to 2 runners-up)
    alternatives: list[dict] = []
    for alt_score, alt_route in scored[1:3]:
        alt_rate = _get_current_rate(alt_route.id, freight_data)
        alt_cost = alt_rate * scenario.cargo_feu
        alternatives.append(
            {
                "route_id": alt_route.id,
                "route_name": alt_route.name,
                "rate_per_feu": round(alt_rate, 0),
                "transit_days": alt_route.transit_days,
                "total_cost_usd": round(alt_cost, 0),
                "carrier": _CARRIER_MAP.get(alt_route.id, "MSC"),
                "score": round(alt_score, 3),
            }
        )

    rec = BookingRecommendation(
        scenario=scenario,
        recommended_route_id=best_route.id,
        recommended_departure=departure_str,
        estimated_rate_per_feu=round(forecast_rate, 2),
        total_cost_usd=round(total_cost, 2),
        transit_days=best_route.transit_days,
        estimated_arrival=arrival_str,
        savings_vs_spot=round(savings_vs_spot, 2),
        confidence=round(confidence, 2),
        key_risks=risks,
        alternative_routes=alternatives,
        carrier_recommendation=carrier,
        booking_urgency=urgency,
    )

    logger.info(
        "Recommendation: route={} dep={} rate={:.0f}/FEU urgency={}".format(
            rec.recommended_route_id,
            rec.recommended_departure,
            rec.estimated_rate_per_feu,
            rec.booking_urgency,
        )
    )
    return rec


def estimate_total_logistics_cost(recommendation: BookingRecommendation) -> dict:
    """
    Break down the full door-to-door logistics cost for a recommendation.

    Returns a dict with itemized costs and a total.
    """
    s = recommendation.scenario
    feu = s.cargo_feu
    cat = s.cargo_category.lower()
    ocean = recommendation.estimated_rate_per_feu * feu

    # Port handling: origin + destination terminal handling charges
    port_handling_per_feu = 380.0  # THC both sides combined, USD
    port_handling = port_handling_per_feu * feu

    # Inland drayage (origin): truck from factory/warehouse to port gate
    # Rough estimate: $450-900/FEU depending on region
    origin_port = PORTS_BY_LOCODE.get(s.origin_locode)
    if origin_port and origin_port.region in ("Asia East", "Southeast Asia"):
        drayage_origin_per_feu = 520.0
    elif origin_port and origin_port.region in ("North America West", "North America East"):
        drayage_origin_per_feu = 780.0
    elif origin_port and origin_port.region == "Europe":
        drayage_origin_per_feu = 650.0
    else:
        drayage_origin_per_feu = 600.0
    inland_drayage_origin = drayage_origin_per_feu * feu

    # Inland drayage (destination)
    dest_port = PORTS_BY_LOCODE.get(s.dest_locode)
    if dest_port and dest_port.region in ("North America West", "North America East"):
        drayage_dest_per_feu = 900.0
    elif dest_port and dest_port.region == "Europe":
        drayage_dest_per_feu = 700.0
    elif dest_port and dest_port.region in ("Asia East", "Southeast Asia"):
        drayage_dest_per_feu = 480.0
    else:
        drayage_dest_per_feu = 620.0
    inland_drayage_dest = drayage_dest_per_feu * feu

    # Documentation: B/L, customs, export/import filing
    documentation = (180.0 + 90.0) * feu  # origin + dest filing

    # Insurance
    cargo_value = _CARGO_VALUE_PER_FEU.get(cat, 90_000.0) * feu
    ins_rate = _INSURANCE_RATE.get(cat, 0.0030)
    insurance = cargo_value * ins_rate

    # Carbon offset: ~2.5 tonnes CO2/FEU/10000km; offset at $25/tonne
    route = ROUTES_BY_ID.get(recommendation.recommended_route_id)
    transit = route.transit_days if route else 21
    # Rough proxy: 700nm/day at sea, 0.00035 tCO2/FEU/nm
    estimated_nm = transit * 700
    co2_per_feu = estimated_nm * 0.00035
    carbon_offset = co2_per_feu * 25.0 * feu

    total = (
        ocean
        + port_handling
        + inland_drayage_origin
        + inland_drayage_dest
        + documentation
        + insurance
        + carbon_offset
    )

    breakdown = {
        "ocean_freight": round(ocean, 2),
        "port_handling": round(port_handling, 2),
        "inland_drayage_origin": round(inland_drayage_origin, 2),
        "inland_drayage_dest": round(inland_drayage_dest, 2),
        "documentation": round(documentation, 2),
        "insurance": round(insurance, 2),
        "carbon_offset": round(carbon_offset, 2),
        "total": round(total, 2),
    }

    logger.debug("Logistics cost breakdown: total=${:,.0f}".format(total))
    return breakdown


def get_market_timing_score(
    route_id: str,
    freight_data: dict[str, pd.DataFrame],
) -> dict:
    """
    Return market timing intelligence for a route.

    Returns:
        {
          current_vs_6m_avg: float  (% above/below 6-month avg, e.g. 0.12 = 12% above),
          percentile_52w: float     (where current rate sits in 52wk range, 0-1),
          timing_signal: str        ("GREAT_TIME_TO_BOOK"/"GOOD"/"NEUTRAL"/"EXPENSIVE"/"WAIT"),
          days_until_expected_dip: int,
        }
    """
    current = _get_current_rate(route_id, freight_data)
    avg_6m = _get_6m_avg_rate(route_id, freight_data)
    low_52, high_52 = _get_52w_range(route_id, freight_data)

    if avg_6m > 0:
        current_vs_6m_avg = (current - avg_6m) / avg_6m
    else:
        current_vs_6m_avg = 0.0

    rng = high_52 - low_52
    if rng > 0:
        percentile_52w = (current - low_52) / rng
    else:
        percentile_52w = 0.5
    percentile_52w = max(0.0, min(1.0, percentile_52w))

    # Timing signal
    if percentile_52w <= 0.20 and current_vs_6m_avg <= -0.08:
        timing_signal = "GREAT_TIME_TO_BOOK"
    elif percentile_52w <= 0.40 and current_vs_6m_avg <= 0.02:
        timing_signal = "GOOD"
    elif percentile_52w <= 0.65 or abs(current_vs_6m_avg) <= 0.08:
        timing_signal = "NEUTRAL"
    elif percentile_52w <= 0.85:
        timing_signal = "EXPENSIVE"
    else:
        timing_signal = "WAIT"

    month = date.today().month
    days_until_expected_dip = _SEASONAL_DIP_DAYS.get(month, 60)

    result = {
        "current_vs_6m_avg": round(current_vs_6m_avg, 4),
        "percentile_52w": round(percentile_52w, 3),
        "timing_signal": timing_signal,
        "days_until_expected_dip": days_until_expected_dip,
    }

    logger.debug(
        "Market timing for {}: signal={} p52w={:.1%}".format(
            route_id, timing_signal, percentile_52w
        )
    )
    return result
