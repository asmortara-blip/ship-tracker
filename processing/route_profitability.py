"""route_profitability.py — Real-world route economics for container shipping lanes.

Calculates per-FEU cost breakdowns, gross margins, and carrier recommendations
for each route registered in route_registry, using a baseline 8000 TEU vessel
at 85% load factor and $550/mt fuel (HFO).
"""
from __future__ import annotations

from dataclasses import dataclass

from routes.route_registry import ROUTES


# ── Baseline assumptions ──────────────────────────────────────────────────────
_BASELINE_FUEL_PRICE_USD_PER_MT: float = 550.0


# ── Hardcoded cost bases per FEU per voyage ───────────────────────────────────
# Derived from: 8000 TEU vessel, 85% load factor, $550/mt HFO.
# Components: fuel, port fees (origin + dest), crew/overhead, canal fees, carbon.
ROUTE_COST_BASES: dict[str, dict] = {
    "transpacific_eb": {
        "fuel": 280, "port": 160, "crew": 95, "canal": 0,   "carbon": 38, "total": 573,
    },
    "asia_europe": {
        "fuel": 550, "port": 175, "crew": 95, "canal": 85,  "carbon": 75, "total": 980,
    },
    "transpacific_wb": {
        "fuel": 280, "port": 155, "crew": 95, "canal": 0,   "carbon": 38, "total": 568,
    },
    "transatlantic": {
        "fuel": 185, "port": 170, "crew": 95, "canal": 0,   "carbon": 25, "total": 475,
    },
    "sea_transpacific_eb": {
        "fuel": 410, "port": 165, "crew": 95, "canal": 0,   "carbon": 55, "total": 725,
    },
    "ningbo_europe": {
        "fuel": 565, "port": 165, "crew": 95, "canal": 85,  "carbon": 78, "total": 988,
    },
    "middle_east_to_europe": {
        "fuel": 435, "port": 175, "crew": 95, "canal": 85,  "carbon": 60, "total": 850,
    },
    "middle_east_to_asia": {
        "fuel": 195, "port": 155, "crew": 95, "canal": 0,   "carbon": 26, "total": 471,
    },
    "south_asia_to_europe": {
        "fuel": 485, "port": 165, "crew": 95, "canal": 85,  "carbon": 65, "total": 895,
    },
    "intra_asia_china_sea": {
        "fuel": 92,  "port": 140, "crew": 85, "canal": 0,   "carbon": 12, "total": 329,
    },
    "intra_asia_china_japan": {
        "fuel": 30,  "port": 135, "crew": 85, "canal": 0,   "carbon": 4,  "total": 254,
    },
    "china_south_america": {
        "fuel": 502, "port": 160, "crew": 95, "canal": 120, "carbon": 68, "total": 945,
    },
    "europe_south_america": {
        "fuel": 266, "port": 165, "crew": 95, "canal": 0,   "carbon": 36, "total": 562,
    },
    "med_hub_to_asia": {
        "fuel": 690, "port": 170, "crew": 95, "canal": 0,   "carbon": 93, "total": 1048,
    },
    "north_africa_to_europe": {
        "fuel": 61,  "port": 150, "crew": 85, "canal": 0,   "carbon": 8,  "total": 304,
    },
    "us_east_south_america": {
        "fuel": 246, "port": 160, "crew": 95, "canal": 0,   "carbon": 33, "total": 534,
    },
    "longbeach_to_asia": {
        "fuel": 276, "port": 155, "crew": 95, "canal": 0,   "carbon": 37, "total": 563,
    },
}


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class RouteProfitability:
    route_id: str
    route_name: str
    current_revenue_usd_per_feu: float   # latest freight rate from freight_data
    total_cost_usd_per_feu: float        # sum of all cost components
    gross_margin_usd: float              # revenue - cost
    gross_margin_pct: float              # margin / revenue  (may be negative)
    cost_breakdown: dict                 # {"fuel": x, "port": x, "crew": x, "canal": x, "carbon": x}
    breakeven_rate_usd: float            # minimum rate to cover costs (== total_cost)
    profitability_signal: str            # "HIGHLY_PROFITABLE" | "PROFITABLE" | "BREAKEVEN" | "LOSS_MAKING"
    signal_color: str                    # green / blue / amber / red
    carrier_recommendation: str          # operational guidance


# ── Signal classification helpers ─────────────────────────────────────────────

def _classify_signal(margin_pct: float) -> tuple[str, str]:
    """Return (profitability_signal, signal_color) for a given margin percentage."""
    if margin_pct > 0.40:
        return "HIGHLY_PROFITABLE", "green"
    if margin_pct > 0.15:
        return "PROFITABLE", "blue"
    if margin_pct > 0.0:
        return "BREAKEVEN", "amber"
    return "LOSS_MAKING", "red"


_CARRIER_RECOMMENDATIONS: dict[str, str] = {
    "HIGHLY_PROFITABLE": (
        "Maximize capacity deployment; consider adding extra loaders"
    ),
    "PROFITABLE": (
        "Normal operations; optimize vessel rotation"
    ),
    "BREAKEVEN": (
        "Review slow steaming options; minimize ballast legs"
    ),
    "LOSS_MAKING": (
        "Blank sailing candidates; reduce capacity immediately"
    ),
}


# ── Core calculation ──────────────────────────────────────────────────────────

def _latest_rate(freight_data: dict, route_id: str) -> float | None:
    """Extract the most recent freight rate (USD/FEU) for a route from freight_data.

    freight_data is expected to be a dict keyed by route_id whose values are
    either a numeric rate directly, a list/sequence of numeric values (latest
    = last element), or a dict with a "rate" or "latest_rate" key.
    """
    entry = freight_data.get(route_id)
    if entry is None:
        return None

    # Plain number
    if isinstance(entry, (int, float)):
        return float(entry)

    # List / sequence — take the last element
    if isinstance(entry, (list, tuple)) and len(entry) > 0:
        val = entry[-1]
        if isinstance(val, (int, float)):
            return float(val)
        # Could be a dict row
        if isinstance(val, dict):
            for key in ("rate", "latest_rate", "value", "price"):
                if key in val:
                    return float(val[key])

    # Dict with a named rate key
    if isinstance(entry, dict):
        for key in ("rate", "latest_rate", "value", "price"):
            if key in entry:
                return float(entry[key])

    return None


def calculate_profitability(
    route_id: str,
    route_name: str,
    freight_data: dict,
    fuel_price_override: float | None = None,
) -> RouteProfitability | None:
    """Calculate profitability for a single route.

    Parameters
    ----------
    route_id:
        Must match a key in ROUTE_COST_BASES.
    route_name:
        Human-readable label for the route.
    freight_data:
        Mapping of route_id -> rate data (see _latest_rate for accepted shapes).
    fuel_price_override:
        If provided, scales the fuel cost component proportionally from the
        $550/mt baseline.  E.g. 660 => fuel cost * 1.2.

    Returns
    -------
    RouteProfitability or None if the route has no cost data or no rate data.
    """
    cost_base = ROUTE_COST_BASES.get(route_id)
    if cost_base is None:
        return None

    revenue = _latest_rate(freight_data, route_id)
    if revenue is None:
        return None

    # Copy cost breakdown so we don't mutate the module-level dict
    breakdown = dict(cost_base)

    # Apply fuel price override (scale fuel component only)
    if fuel_price_override is not None and fuel_price_override > 0:
        fuel_scalar = fuel_price_override / _BASELINE_FUEL_PRICE_USD_PER_MT
        breakdown["fuel"] = round(breakdown["fuel"] * fuel_scalar, 2)
        # Recompute total from components (exclude "total" key)
        breakdown["total"] = round(
            breakdown["fuel"]
            + breakdown["port"]
            + breakdown["crew"]
            + breakdown["canal"]
            + breakdown["carbon"],
            2,
        )

    total_cost = float(breakdown["total"])

    gross_margin_usd = revenue - total_cost
    gross_margin_pct = gross_margin_usd / revenue if revenue != 0 else 0.0

    signal, color = _classify_signal(gross_margin_pct)

    # Expose only the five named components (not "total") in cost_breakdown
    cost_breakdown = {
        k: breakdown[k] for k in ("fuel", "port", "crew", "canal", "carbon")
    }

    return RouteProfitability(
        route_id=route_id,
        route_name=route_name,
        current_revenue_usd_per_feu=revenue,
        total_cost_usd_per_feu=total_cost,
        gross_margin_usd=gross_margin_usd,
        gross_margin_pct=gross_margin_pct,
        cost_breakdown=cost_breakdown,
        breakeven_rate_usd=total_cost,
        profitability_signal=signal,
        signal_color=color,
        carrier_recommendation=_CARRIER_RECOMMENDATIONS[signal],
    )


def calculate_all_routes(freight_data: dict) -> list[RouteProfitability]:
    """Calculate profitability for every registered route.

    Routes with no cost data or no freight rate are silently skipped.

    Returns
    -------
    list[RouteProfitability]
        Sorted descending by gross_margin_pct (most profitable first).
    """
    results: list[RouteProfitability] = []
    for route in ROUTES:
        result = calculate_profitability(
            route_id=route.id,
            route_name=route.name,
            freight_data=freight_data,
        )
        if result is not None:
            results.append(result)

    results.sort(key=lambda r: r.gross_margin_pct, reverse=True)
    return results
