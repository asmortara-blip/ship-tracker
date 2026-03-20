from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from loguru import logger

from ports.demand_analyzer import PortDemandResult, get_port_result
from routes.rate_estimator import compute_rate_momentum, compute_rate_pct_change
from routes.route_registry import ROUTES, ROUTES_BY_ID, ShippingRoute
from utils.helpers import score_to_label, now_iso


@dataclass
class RouteOpportunity:
    route_id: str
    route_name: str
    origin_region: str
    dest_region: str
    origin_locode: str
    dest_locode: str
    transit_days: int
    fbx_index: str

    opportunity_score: float     # [0, 1] composite
    opportunity_label: str       # "Strong" | "Moderate" | "Weak"

    current_rate_usd_feu: float
    rate_trend: str              # "Rising" | "Stable" | "Falling"
    rate_pct_change_30d: float

    demand_imbalance: float      # dest_demand - origin_demand [-1, 1]
    origin_congestion: float     # [0, 1]
    dest_demand_score: float     # [0, 1]

    rate_momentum_component: float
    demand_imbalance_component: float
    congestion_clearance_component: float
    macro_tailwind_component: float

    rationale: str
    generated_at: str


def optimize_all_routes(
    port_results: list[PortDemandResult],
    freight_data: dict[str, pd.DataFrame],
    macro_data: dict[str, pd.DataFrame],
    weights: dict | None = None,
) -> list[RouteOpportunity]:
    """Score all tracked shipping routes for opportunity.

    Returns:
        List of RouteOpportunity sorted by opportunity_score descending.
    """
    w = weights or {
        "rate_momentum": 0.35,
        "demand_imbalance": 0.30,
        "congestion_clearance": 0.20,
        "macro_tailwind": 0.15,
    }

    # Compute macro tailwind once for all routes
    macro_tailwind = _compute_macro_tailwind(macro_data)

    results: list[RouteOpportunity] = []
    for route in ROUTES:
        opp = _score_route(route, port_results, freight_data, macro_tailwind, w)
        results.append(opp)
        logger.debug(f"{route.id}: opportunity_score={opp.opportunity_score:.3f} ({opp.opportunity_label})")

    results.sort(key=lambda r: r.opportunity_score, reverse=True)
    logger.info(f"Route optimization complete: {len(results)} routes scored")
    return results


def _score_route(
    route: ShippingRoute,
    port_results: list[PortDemandResult],
    freight_data: dict[str, pd.DataFrame],
    macro_tailwind: float,
    weights: dict,
) -> RouteOpportunity:
    """Score a single route."""

    # --- Rate momentum component ---
    rate_momentum = compute_rate_momentum(route.id, freight_data)
    pct_30d = compute_rate_pct_change(route.id, freight_data, 30)

    df = freight_data.get(route.id)
    if df is not None and not df.empty and "rate_usd_per_feu" in df.columns:
        _rates = df["rate_usd_per_feu"].dropna()
        current_rate = float(_rates.iloc[-1]) if not _rates.empty else 0.0
    else:
        current_rate = 0.0

    from utils.helpers import trend_label
    rate_trend = trend_label(pct_30d)

    # --- Demand imbalance component ---
    origin_result = get_port_result(route.origin_locode, port_results)
    dest_result = get_port_result(route.dest_locode, port_results)

    origin_demand = origin_result.demand_score if origin_result else 0.5
    dest_demand = dest_result.demand_score if dest_result else 0.5
    origin_congestion = origin_result.congestion_index if origin_result else 0.5

    # Imbalance: positive = destination has more demand (good: ships wanted there)
    demand_imbalance = dest_demand - origin_demand  # range [-1, 1]
    # Normalize to [0, 1]: 0.5 = balanced, 1.0 = strong dest demand
    demand_imbalance_component = (demand_imbalance + 1.0) / 2.0

    # --- Congestion clearance component ---
    # Want LOW congestion at origin port (easy to load and depart)
    congestion_clearance = 1.0 - origin_congestion

    # --- Composite score ---
    opportunity_score = (
        weights["rate_momentum"] * rate_momentum
        + weights["demand_imbalance"] * demand_imbalance_component
        + weights["congestion_clearance"] * congestion_clearance
        + weights["macro_tailwind"] * macro_tailwind
    )
    opportunity_score = max(0.0, min(1.0, opportunity_score))

    # --- Rationale ---
    rationale = _build_rationale(
        route, rate_momentum, pct_30d, demand_imbalance,
        origin_congestion, macro_tailwind, opportunity_score
    )

    return RouteOpportunity(
        route_id=route.id,
        route_name=route.name,
        origin_region=route.origin_region,
        dest_region=route.dest_region,
        origin_locode=route.origin_locode,
        dest_locode=route.dest_locode,
        transit_days=route.transit_days,
        fbx_index=route.fbx_index,
        opportunity_score=opportunity_score,
        opportunity_label=score_to_label(opportunity_score),
        current_rate_usd_feu=current_rate,
        rate_trend=rate_trend,
        rate_pct_change_30d=pct_30d,
        demand_imbalance=demand_imbalance,
        origin_congestion=origin_congestion,
        dest_demand_score=dest_demand,
        rate_momentum_component=rate_momentum,
        demand_imbalance_component=demand_imbalance_component,
        congestion_clearance_component=congestion_clearance,
        macro_tailwind_component=macro_tailwind,
        rationale=rationale,
        generated_at=now_iso(),
    )


def _compute_macro_tailwind(macro_data: dict[str, pd.DataFrame]) -> float:
    """Compute a macro tailwind score [0, 1] from FRED data.

    macro_score = 0.40 * pmi + 0.35 * bdi + 0.25 * fuel_inverse
    """
    from data.fred_feed import compute_bdi_score, get_latest_value

    # BDI component
    bdi_score = compute_bdi_score(macro_data)

    # Industrial production as PMI proxy (normalized)
    ipman_df = macro_data.get("IPMAN")
    if ipman_df is not None and not ipman_df.empty:
        values = ipman_df["value"].dropna()
        current = values.iloc[-1]
        avg = values.tail(90).mean()
        pmi_proxy = min(1.0, max(0.0, (current / avg - 0.9) / 0.2)) if avg > 0 else 0.5
    else:
        pmi_proxy = 0.5

    # Fuel inverse: high oil = lower shipping margins
    wti_df = macro_data.get("DCOILWTICO")
    if wti_df is not None and not wti_df.empty:
        values = wti_df["value"].dropna()
        current = values.iloc[-1]
        # Normalize WTI around [$40, $120] range
        wti_norm = max(0.0, min(1.0, (current - 40) / 80))
        fuel_inverse = 1.0 - wti_norm
    else:
        fuel_inverse = 0.5

    macro_tailwind = 0.40 * pmi_proxy + 0.35 * bdi_score + 0.25 * fuel_inverse
    return max(0.0, min(1.0, macro_tailwind))


def _build_rationale(
    route: ShippingRoute,
    rate_momentum: float,
    pct_30d: float,
    demand_imbalance: float,
    origin_congestion: float,
    macro_tailwind: float,
    score: float,
) -> str:
    """Build a human-readable rationale string for a route opportunity."""
    parts = []

    # Rate signal
    if rate_momentum > 0.65:
        parts.append(f"rates up {pct_30d*100:+.0f}% vs 90d avg")
    elif rate_momentum < 0.40:
        parts.append(f"rates down {pct_30d*100:+.0f}% vs 90d avg")
    else:
        parts.append("rates near average")

    # Demand imbalance
    if demand_imbalance > 0.15:
        parts.append(f"strong demand at {route.dest_locode}")
    elif demand_imbalance < -0.15:
        parts.append(f"weak demand at {route.dest_locode}")

    # Congestion
    if origin_congestion < 0.35:
        parts.append(f"low congestion at {route.origin_locode}")
    elif origin_congestion > 0.65:
        parts.append(f"high congestion at {route.origin_locode} (may delay loading)")

    # Macro
    if macro_tailwind > 0.60:
        parts.append("positive macro environment")
    elif macro_tailwind < 0.40:
        parts.append("weak macro headwinds")

    summary = f"{route.name}: " + "; ".join(parts) + f". Overall score: {score:.0%}."
    return summary
