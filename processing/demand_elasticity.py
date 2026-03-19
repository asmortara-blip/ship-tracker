"""demand_elasticity.py — Models how shipping demand responds to rate changes,
tariffs, and macro shocks.

Provides traders insight into when rate spikes are sustainable vs self-defeating
by quantifying price elasticity, cross-elasticity with the BDI, and income
elasticity for each trade lane.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from loguru import logger


# ── Elasticity profiles ───────────────────────────────────────────────────────
# Key insight: inelastic routes (essential trade, few alternatives) can sustain
# high rates. Elastic routes (discretionary, many alternatives) see demand
# destruction at high rates.

ROUTE_ELASTICITY_PROFILES: dict[str, dict] = {
    "transpacific_eb": {
        "price_elasticity": -0.45,        # relatively inelastic — US importers need Chinese goods
        "income_elasticity": 1.8,         # highly income-sensitive (consumer goods)
        "bdi_cross": 0.3,
        "breaking_point_multiple": 3.5,   # rates > 3.5x baseline → demand destruction
    },
    "asia_europe": {
        "price_elasticity": -0.60,        # moderate — some production can shift
        "income_elasticity": 1.5,
        "bdi_cross": 0.4,
        "breaking_point_multiple": 3.0,
    },
    "transatlantic": {
        "price_elasticity": -0.35,        # very inelastic — no viable alternative
        "income_elasticity": 1.2,
        "bdi_cross": 0.25,
        "breaking_point_multiple": 4.0,
    },
    # Intra-Asia routes: highly elastic (many alternatives, short distances)
    "intra_asia_china_sea": {
        "price_elasticity": -1.2,
        "income_elasticity": 2.0,
        "bdi_cross": 0.5,
        "breaking_point_multiple": 2.0,
    },
    "intra_asia_china_japan": {
        "price_elasticity": -0.8,
        "income_elasticity": 1.5,
        "bdi_cross": 0.4,
        "breaking_point_multiple": 2.5,
    },
    # Long-haul specialty routes: moderate elasticity
    "china_south_america": {
        "price_elasticity": -0.70,
        "income_elasticity": 1.6,
        "bdi_cross": 0.35,
        "breaking_point_multiple": 2.8,
    },
    "europe_south_america": {
        "price_elasticity": -0.65,
        "income_elasticity": 1.4,
        "bdi_cross": 0.3,
        "breaking_point_multiple": 3.0,
    },
    # Middle East: inelastic (energy/commodity trade)
    "middle_east_to_europe": {
        "price_elasticity": -0.30,
        "income_elasticity": 0.9,
        "bdi_cross": 0.2,
        "breaking_point_multiple": 4.5,
    },
    "middle_east_to_asia": {
        "price_elasticity": -0.25,
        "income_elasticity": 0.8,
        "bdi_cross": 0.2,
        "breaking_point_multiple": 5.0,
    },
    # Default for unlisted routes
    "DEFAULT": {
        "price_elasticity": -0.55,
        "income_elasticity": 1.4,
        "bdi_cross": 0.35,
        "breaking_point_multiple": 3.0,
    },
}

# Baseline rates (USD/FEU) used when live freight data is unavailable.
# Derived from recent multi-year averages per lane.
_BASELINE_RATES_USD: dict[str, float] = {
    "transpacific_eb": 2_200,
    "asia_europe": 2_000,
    "transpacific_wb": 900,
    "transatlantic": 1_800,
    "sea_transpacific_eb": 2_100,
    "ningbo_europe": 2_050,
    "middle_east_to_europe": 1_600,
    "middle_east_to_asia": 1_200,
    "south_asia_to_europe": 1_900,
    "intra_asia_china_sea": 500,
    "intra_asia_china_japan": 400,
    "china_south_america": 2_400,
    "europe_south_america": 1_700,
    "med_hub_to_asia": 1_500,
    "north_africa_to_europe": 800,
    "us_east_south_america": 1_400,
    "longbeach_to_asia": 950,
}

_DEFAULT_BASELINE_USD = 1_500.0


# ── Dataclass ─────────────────────────────────────────────────────────────────


@dataclass
class ElasticityEstimate:
    route_id: str
    route_name: str
    price_elasticity: float          # negative — % volume change per 1% rate increase
    cross_elasticity_bdi: float      # sensitivity to BDI changes
    income_elasticity: float         # sensitivity to GDP/PMI changes
    elasticity_regime: str           # "INELASTIC" | "ELASTIC" | "HIGHLY_ELASTIC"
    current_demand_sensitivity: float  # [0, 1] — how sensitive is demand right now
    rate_sustainability_score: float   # [0, 1] — can current rates hold?
    rate_breaking_point_usd: float     # rate at which demand starts dropping sharply
    volume_at_risk_pct: float          # % of volume that would shift at breaking point
    description: str


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_profile(route_id: str) -> dict:
    """Return the elasticity profile for *route_id*, falling back to DEFAULT."""
    return ROUTE_ELASTICITY_PROFILES.get(route_id, ROUTE_ELASTICITY_PROFILES["DEFAULT"])


def _regime_from_elasticity(price_elasticity: float) -> str:
    abs_e = abs(price_elasticity)
    if abs_e < 0.5:
        return "INELASTIC"
    if abs_e < 1.0:
        return "ELASTIC"
    return "HIGHLY_ELASTIC"


def _get_current_rate(route_id: str, freight_data: dict | None) -> float | None:
    """Extract the most recent rate (USD/FEU) from freight_data for *route_id*."""
    if not freight_data:
        return None
    df = freight_data.get(route_id)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    if "rate_usd_per_feu" not in df.columns:
        return None
    df = df.sort_values("date")
    val = df["rate_usd_per_feu"].iloc[-1]
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _build_description(regime: str, sustainability: float, route_name: str) -> str:
    if sustainability >= 0.75:
        sustain_str = "current rates appear sustainable"
    elif sustainability >= 0.50:
        sustain_str = "rates are elevated but within tolerance"
    elif sustainability >= 0.25:
        sustain_str = "rates are approaching demand-destruction territory"
    else:
        sustain_str = "rates are near or beyond the demand-destruction threshold"

    regime_str = {
        "INELASTIC": "inelastic demand (essential trade, few alternatives)",
        "ELASTIC": "moderately elastic demand (some substitution possible)",
        "HIGHLY_ELASTIC": "highly elastic demand (many alternatives, discretionary)",
    }.get(regime, "unknown elasticity profile")

    return (
        f"{route_name} shows {regime_str}. "
        f"Sustainability score {sustainability:.2f}/1.00 — {sustain_str}."
    )


# ── Core estimation ───────────────────────────────────────────────────────────


def estimate_elasticity(
    route_id: str,
    route_name: str,
    freight_data: dict | None = None,
) -> ElasticityEstimate:
    """Compute an ElasticityEstimate for a single route.

    Parameters
    ----------
    route_id:
        Unique route identifier (must match keys in route_registry.ROUTES).
    route_name:
        Human-readable label used in the description and UI.
    freight_data:
        Optional dict mapping route_id → pd.DataFrame with columns
        ``date`` and ``rate_usd_per_feu``.  When provided, live rates are
        used to compute sustainability metrics; otherwise hardcoded baselines
        are used.
    """
    profile = _get_profile(route_id)
    price_elasticity: float = profile["price_elasticity"]
    income_elasticity: float = profile["income_elasticity"]
    bdi_cross: float = profile["bdi_cross"]
    breaking_point_multiple: float = profile["breaking_point_multiple"]

    regime = _regime_from_elasticity(price_elasticity)

    baseline_rate = _BASELINE_RATES_USD.get(route_id, _DEFAULT_BASELINE_USD)
    rate_breaking_point = baseline_rate * breaking_point_multiple

    current_rate = _get_current_rate(route_id, freight_data)

    if current_rate is not None and current_rate > 0 and baseline_rate > 0:
        rate_ratio = current_rate / baseline_rate
        # Sustainability decays linearly from 1.0 at baseline to 0 at the breaking point.
        rate_sustainability_score = max(
            0.0,
            1.0 - (rate_ratio - 1.0) / (breaking_point_multiple - 1.0),
        )
        excess_ratio = max(0.0, rate_ratio - 1.0)
        volume_at_risk_pct = abs(price_elasticity) * min(1.0, excess_ratio)
        # Demand sensitivity scales with proximity to the breaking point.
        current_demand_sensitivity = min(1.0, excess_ratio / (breaking_point_multiple - 1.0))
    else:
        # No live data — use neutral defaults.
        rate_sustainability_score = 0.7
        volume_at_risk_pct = 0.0
        current_demand_sensitivity = abs(price_elasticity) / 1.5  # normalised proxy

    description = _build_description(regime, rate_sustainability_score, route_name)

    return ElasticityEstimate(
        route_id=route_id,
        route_name=route_name,
        price_elasticity=price_elasticity,
        cross_elasticity_bdi=bdi_cross,
        income_elasticity=income_elasticity,
        elasticity_regime=regime,
        current_demand_sensitivity=current_demand_sensitivity,
        rate_sustainability_score=rate_sustainability_score,
        rate_breaking_point_usd=rate_breaking_point,
        volume_at_risk_pct=volume_at_risk_pct,
        description=description,
    )


# ── All-routes analysis ───────────────────────────────────────────────────────

# Canonical mapping of every registered route_id to its display name.
# Matches routes/route_registry.py ROUTES list (17 routes total).
_ROUTE_NAMES: dict[str, str] = {
    "transpacific_eb": "Trans-Pacific Eastbound",
    "asia_europe": "Asia-Europe",
    "transpacific_wb": "Trans-Pacific Westbound",
    "transatlantic": "Transatlantic",
    "sea_transpacific_eb": "Southeast Asia Eastbound",
    "ningbo_europe": "Asia-Europe via Suez (Ningbo)",
    "middle_east_to_europe": "Middle East Hub to Europe",
    "middle_east_to_asia": "Middle East Hub to Asia",
    "south_asia_to_europe": "South Asia to Europe",
    "intra_asia_china_sea": "Intra-Asia: China to SE Asia",
    "intra_asia_china_japan": "Intra-Asia: China to Japan/Korea",
    "china_south_america": "China to South America",
    "europe_south_america": "Europe to South America",
    "med_hub_to_asia": "Mediterranean Hub to Asia",
    "north_africa_to_europe": "North Africa/Med to Europe",
    "us_east_south_america": "US East Coast to South America",
    "longbeach_to_asia": "US West Coast (Long Beach) to Asia",
}


def analyze_all_elasticities(freight_data: dict) -> list[ElasticityEstimate]:
    """Return ElasticityEstimate for all 17 routes, sorted by sustainability
    score ascending (most at risk first).

    Parameters
    ----------
    freight_data:
        Dict mapping route_id → pd.DataFrame with ``date`` and
        ``rate_usd_per_feu`` columns.
    """
    results: list[ElasticityEstimate] = []
    for route_id, route_name in _ROUTE_NAMES.items():
        try:
            est = estimate_elasticity(route_id, route_name, freight_data)
            results.append(est)
        except Exception as exc:
            logger.debug(f"Elasticity estimation failed for {route_id}: {exc}")

    results.sort(key=lambda e: e.rate_sustainability_score)
    return results


# ── Rate-shock simulation ─────────────────────────────────────────────────────


def simulate_rate_shock(
    route_id: str,
    rate_change_pct: float,
    freight_data: dict,
) -> dict:
    """Simulate the demand and rate impact of an instantaneous rate shock.

    Parameters
    ----------
    route_id:
        Route to shock.
    rate_change_pct:
        Fractional rate change (e.g. 0.20 = +20 %).
    freight_data:
        Live freight data dict (same format as elsewhere).

    Returns
    -------
    dict with keys:
        ``volume_change_pct``  — expected demand volume change (fraction).
        ``adjusted_rate``      — rate after demand feedback (USD/FEU).
        ``sustainability``     — qualitative label.
        ``breakeven_time_weeks`` — rough estimate of weeks until the market
                                   re-equilibrates.
    """
    profile = _get_profile(route_id)
    price_elasticity: float = profile["price_elasticity"]
    breaking_point_multiple: float = profile["breaking_point_multiple"]

    baseline_rate = _BASELINE_RATES_USD.get(route_id, _DEFAULT_BASELINE_USD)
    current_rate = _get_current_rate(route_id, freight_data)
    if current_rate is None or current_rate <= 0:
        current_rate = baseline_rate

    # Step 1: volume response to the shock.
    expected_volume_change = price_elasticity * rate_change_pct

    # Step 2: rate after partial demand-feedback correction.
    # The 0.3 dampening factor reflects that only ~30 % of demand changes
    # translate back into spot-rate corrections within the analysis horizon.
    raw_shocked_rate = current_rate * (1.0 + rate_change_pct)
    expected_rate_after_adjustment = raw_shocked_rate * (
        1.0 + expected_volume_change * 0.3
    )
    expected_rate_after_adjustment = max(0.0, expected_rate_after_adjustment)

    # Step 3: sustainability assessment.
    if baseline_rate > 0:
        post_shock_ratio = expected_rate_after_adjustment / baseline_rate
    else:
        post_shock_ratio = 1.0

    if post_shock_ratio >= breaking_point_multiple:
        sustainability = "UNSUSTAINABLE — demand destruction likely"
        # Estimate breakeven: more elastic routes re-price faster.
        breakeven_time_weeks = max(2, int(4 / abs(price_elasticity)))
    elif post_shock_ratio >= breaking_point_multiple * 0.75:
        sustainability = "AT RISK — approaching demand-destruction zone"
        breakeven_time_weeks = max(4, int(8 / abs(price_elasticity)))
    elif rate_change_pct > 0:
        sustainability = "SUSTAINABLE — within tolerable range"
        breakeven_time_weeks = max(8, int(16 / abs(price_elasticity)))
    else:
        sustainability = "RATE DECLINING — demand stimulus expected"
        breakeven_time_weeks = max(4, int(6 / abs(price_elasticity)))

    return {
        "volume_change_pct": round(expected_volume_change, 4),
        "adjusted_rate": round(expected_rate_after_adjustment, 2),
        "sustainability": sustainability,
        "breakeven_time_weeks": breakeven_time_weeks,
    }
