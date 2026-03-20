"""
Maritime Chokepoint Analyzer

Models the world's 9 critical maritime chokepoints that collectively control
60%+ of global trade flows.  Provides risk scoring, closure simulation, and
active-disruption detection.

Data current as of early 2026.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from loguru import logger


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class Chokepoint:
    """Full descriptor for one maritime chokepoint."""

    name: str
    lat: float
    lon: float
    width_km: float                          # Narrowest navigable width in km
    daily_vessels: int                       # Typical vessels transiting per day
    daily_teu_m: float                       # Daily TEU throughput (millions)
    pct_global_trade: float                  # % of global trade by value / volume
    strategic_alternatives: List[str]        # Named alternative routes
    current_risk_level: str                  # CRITICAL | HIGH | MODERATE | LOW
    current_disruption_type: str             # NONE | ACTIVE_CONFLICT | WEATHER | DIPLOMATIC | CONGESTION
    disruption_since: Optional[str]          # ISO date string or None
    rerouting_cost_per_voyage_usd: int       # Additional cost per voyage if rerouted
    extra_days_if_closed: int               # Additional transit days via best alternative
    affected_routes: List[str]              # Route IDs touched by this chokepoint


# ---------------------------------------------------------------------------
# CHOKEPOINTS registry — 9 critical maritime passages
# ---------------------------------------------------------------------------

CHOKEPOINTS: dict[str, Chokepoint] = {
    "hormuz": Chokepoint(
        name="Strait of Hormuz",
        lat=26.5,
        lon=56.3,
        width_km=33.0,
        daily_vessels=75,
        daily_teu_m=0.18,
        pct_global_trade=20.0,
        strategic_alternatives=[],           # No viable alternative for Gulf oil
        current_risk_level="HIGH",
        current_disruption_type="DIPLOMATIC",
        disruption_since="2024-01-01",
        rerouting_cost_per_voyage_usd=0,     # Cannot reroute; closure = supply shock
        extra_days_if_closed=21,
        affected_routes=[
            "middle_east_to_europe",
            "middle_east_to_asia",
            "persian_gulf_lng",
        ],
    ),

    "suez": Chokepoint(
        name="Suez Canal",
        lat=30.0,
        lon=32.5,
        width_km=205.0,                      # Canal width varies; 205km long
        daily_vessels=50,
        daily_teu_m=0.48,
        pct_global_trade=12.0,
        strategic_alternatives=[
            "Cape of Good Hope (+7-10 days, +$300-500/FEU)",
        ],
        current_risk_level="CRITICAL",
        current_disruption_type="ACTIVE_CONFLICT",
        disruption_since="2023-11-19",
        rerouting_cost_per_voyage_usd=400000,
        extra_days_if_closed=9,
        affected_routes=[
            "asia_europe",
            "ningbo_europe",
            "south_asia_to_europe",
            "middle_east_to_europe",
            "north_africa_to_europe",
        ],
    ),

    "malacca": Chokepoint(
        name="Strait of Malacca",
        lat=2.5,
        lon=101.0,
        width_km=2.8,
        daily_vessels=250,
        daily_teu_m=1.20,
        pct_global_trade=25.0,
        strategic_alternatives=[
            "Lombok Strait (+2-3 days)",
            "Sunda Strait (+1-2 days, depth limited)",
        ],
        current_risk_level="LOW",
        current_disruption_type="NONE",
        disruption_since=None,
        rerouting_cost_per_voyage_usd=85000,
        extra_days_if_closed=2,
        affected_routes=[
            "sea_transpacific_eb",
            "asia_europe",
            "intra_asia_china_sea",
            "china_to_india",
        ],
    ),

    "bab_el_mandeb": Chokepoint(
        name="Bab-el-Mandeb",
        lat=12.5,
        lon=43.5,
        width_km=29.0,
        daily_vessels=48,
        daily_teu_m=0.40,
        pct_global_trade=10.0,
        strategic_alternatives=[
            "Cape of Good Hope (+9-12 days)",
        ],
        current_risk_level="CRITICAL",
        current_disruption_type="ACTIVE_CONFLICT",
        disruption_since="2023-11-19",
        rerouting_cost_per_voyage_usd=380000,
        extra_days_if_closed=10,
        affected_routes=[
            "asia_europe",
            "ningbo_europe",
            "south_asia_to_europe",
            "middle_east_to_europe",
        ],
    ),

    "panama": Chokepoint(
        name="Panama Canal",
        lat=9.0,
        lon=-79.5,
        width_km=55.0,
        daily_vessels=36,
        daily_teu_m=0.22,
        pct_global_trade=5.0,
        strategic_alternatives=[
            "Suez Canal reroute (+18-22 days)",
            "US land bridge (rail)",
            "Cape Horn (+20-25 days)",
        ],
        current_risk_level="MODERATE",
        current_disruption_type="WEATHER",
        disruption_since="2023-10-01",
        rerouting_cost_per_voyage_usd=600000,
        extra_days_if_closed=20,
        affected_routes=[
            "transpacific_eb",
            "us_east_south_america",
            "us_east_coast_asia",
        ],
    ),

    "gibraltar": Chokepoint(
        name="Strait of Gibraltar",
        lat=35.9,
        lon=-5.6,
        width_km=14.3,
        daily_vessels=300,
        daily_teu_m=0.55,
        pct_global_trade=7.0,
        strategic_alternatives=[
            "Suez Canal (for eastbound only)",
        ],
        current_risk_level="LOW",
        current_disruption_type="NONE",
        disruption_since=None,
        rerouting_cost_per_voyage_usd=0,
        extra_days_if_closed=5,
        affected_routes=[
            "asia_europe",
            "transatlantic_eb",
            "north_africa_to_europe",
        ],
    ),

    "danish_straits": Chokepoint(
        name="Danish Straits (Kattegat/Oresund)",
        lat=55.5,
        lon=11.0,
        width_km=4.0,
        daily_vessels=110,
        daily_teu_m=0.08,
        pct_global_trade=1.5,
        strategic_alternatives=[
            "Kiel Canal (small vessels only)",
            "North Cape route (+4-6 days)",
        ],
        current_risk_level="MODERATE",
        current_disruption_type="DIPLOMATIC",
        disruption_since="2022-02-24",
        rerouting_cost_per_voyage_usd=95000,
        extra_days_if_closed=5,
        affected_routes=[
            "baltic_to_north_sea",
            "nordic_exports",
            "russia_europe",
        ],
    ),

    "dover": Chokepoint(
        name="Strait of Dover",
        lat=51.0,
        lon=1.5,
        width_km=34.0,
        daily_vessels=550,
        daily_teu_m=0.35,
        pct_global_trade=3.0,
        strategic_alternatives=[
            "Northern UK route (+1-2 days)",
        ],
        current_risk_level="LOW",
        current_disruption_type="NONE",
        disruption_since=None,
        rerouting_cost_per_voyage_usd=40000,
        extra_days_if_closed=2,
        affected_routes=[
            "transatlantic_eb",
            "north_europe_uk",
            "asia_europe",
        ],
    ),

    "lombok_sunda": Chokepoint(
        name="Lombok / Sunda Strait",
        lat=-8.7,
        lon=115.7,
        width_km=40.0,
        daily_vessels=40,
        daily_teu_m=0.12,
        pct_global_trade=2.0,
        strategic_alternatives=[
            "Strait of Malacca (primary)",
        ],
        current_risk_level="LOW",
        current_disruption_type="NONE",
        disruption_since=None,
        rerouting_cost_per_voyage_usd=55000,
        extra_days_if_closed=3,
        affected_routes=[
            "sea_transpacific_eb",
            "australia_to_asia",
            "intra_asia_china_sea",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Risk level helpers
# ---------------------------------------------------------------------------

_RISK_SCORE: dict[str, float] = {
    "CRITICAL": 1.0,
    "HIGH":     0.75,
    "MODERATE": 0.45,
    "LOW":      0.10,
}

_DISRUPTION_MULTIPLIER: dict[str, float] = {
    "ACTIVE_CONFLICT": 1.40,
    "DIPLOMATIC":      1.15,
    "WEATHER":         1.10,
    "CONGESTION":      1.05,
    "NONE":            1.00,
}

_RISK_COLORS: dict[str, str] = {
    "CRITICAL": "#ef4444",
    "HIGH":     "#f97316",
    "MODERATE": "#f59e0b",
    "LOW":      "#10b981",
}


def risk_color(level: str) -> str:
    """Return hex color string for a given risk level."""
    return _RISK_COLORS.get(level, "#94a3b8")


# ---------------------------------------------------------------------------
# compute_chokepoint_risk_score
# ---------------------------------------------------------------------------

def compute_chokepoint_risk_score() -> dict[str, float]:
    """
    Return a composite risk score [0, 1] for every chokepoint.

    Formula:
        base_score  = _RISK_SCORE[risk_level]
        trade_weight = pct_global_trade / 25.0   (normalised, capped at 1)
        disrupt_mult = _DISRUPTION_MULTIPLIER[disruption_type]
        alt_penalty  = 1 / max(1, len(alternatives))  (fewer alts => higher risk)
        composite    = base_score * disrupt_mult * (1 + trade_weight) * (1 + alt_penalty) / 4
        clamped to [0, 1]
    """
    scores: dict[str, float] = {}
    for key, cp in CHOKEPOINTS.items():
        base = _RISK_SCORE.get(cp.current_risk_level, 0.1)
        trade_w = min(1.0, cp.pct_global_trade / 25.0)
        d_mult = _DISRUPTION_MULTIPLIER.get(cp.current_disruption_type, 1.0)
        n_alts = max(1, len(cp.strategic_alternatives))
        alt_pen = 1.0 / n_alts
        raw = base * d_mult * (1.0 + trade_w) * (1.0 + alt_pen) / 4.0
        scores[key] = min(1.0, raw)
        logger.debug(
            "Chokepoint risk score | {} => {:.3f} "
            "(base={:.2f}, trade_w={:.2f}, d_mult={:.2f}, alt_pen={:.2f})",
            cp.name, scores[key], base, trade_w, d_mult, alt_pen,
        )
    return scores


# ---------------------------------------------------------------------------
# simulate_chokepoint_closure
# ---------------------------------------------------------------------------

def simulate_chokepoint_closure(
    chokepoint_name: str,
    duration_weeks: int,
) -> dict:
    """
    Simulate the effect of closing a chokepoint for *duration_weeks* weeks.

    Returns
    -------
    dict with keys:
        chokepoint_name        : str
        duration_weeks         : int
        affected_routes        : list[str]
        rate_impact_pct        : float   (estimated % rate increase)
        global_trade_impact_pct: float   (% of global trade disrupted)
        rerouting_cost_total_usd: float  (per-voyage cost * daily_vessels * 7 * weeks)
        alternative_routes     : list[str]
        feasibility_note       : str
    """
    # Look up by name (case-insensitive) or key
    cp: Optional[Chokepoint] = None
    for key, candidate in CHOKEPOINTS.items():
        if (
            candidate.name.lower() == chokepoint_name.lower()
            or key.lower() == chokepoint_name.lower()
        ):
            cp = candidate
            break

    if cp is None:
        logger.warning("simulate_chokepoint_closure: unknown chokepoint '{}'", chokepoint_name)
        return {
            "chokepoint_name": chokepoint_name,
            "error": "Unknown chokepoint",
        }

    # Rate impact: base elasticity model
    # Each week of closure of a high-trade-share chokepoint with no alternatives
    # raises spot rates by ~2-8% depending on trade share and lack of alternatives
    alt_factor = max(0.3, 1.0 - 0.2 * len(cp.strategic_alternatives))
    trade_factor = cp.pct_global_trade / 10.0
    risk_factor = _RISK_SCORE.get(cp.current_risk_level, 0.1)
    weekly_rate_impact = 0.03 * trade_factor * alt_factor * (1.0 + risk_factor)
    total_rate_impact_pct = min(1.50, weekly_rate_impact * duration_weeks) * 100.0

    # Rerouting cost: daily voyages * days * per-voyage incremental cost
    daily_voyages_approx = max(1, cp.daily_vessels // 3)
    rerouting_cost_total = (
        daily_voyages_approx * duration_weeks * 7 * cp.rerouting_cost_per_voyage_usd
    )

    # Global trade impact: fraction of trade affected * duration dampener
    duration_dampener = min(1.0, 0.5 + duration_weeks / 20.0)
    trade_impact_pct = cp.pct_global_trade * 0.6 * duration_dampener

    feasibility_note: str
    if not cp.strategic_alternatives:
        feasibility_note = (
            "No viable alternative route exists. Closure would cause severe supply shock."
        )
    elif duration_weeks <= 2:
        feasibility_note = (
            "Short-term closure manageable via buffer stocks and partial rerouting."
        )
    elif duration_weeks <= 8:
        feasibility_note = (
            "Medium-term closure forces sustained rerouting; fleet repositioning required."
        )
    else:
        feasibility_note = (
            "Prolonged closure would restructure trade lanes and drive permanent fleet changes."
        )

    result = {
        "chokepoint_name": cp.name,
        "duration_weeks": duration_weeks,
        "affected_routes": cp.affected_routes,
        "rate_impact_pct": round(total_rate_impact_pct, 1),
        "global_trade_impact_pct": round(trade_impact_pct, 1),
        "rerouting_cost_total_usd": int(rerouting_cost_total),
        "alternative_routes": cp.strategic_alternatives,
        "extra_days_if_closed": cp.extra_days_if_closed,
        "feasibility_note": feasibility_note,
    }

    logger.info(
        "Closure simulation | {} x{} weeks | rate+{:.1f}% | trade impact {:.1f}%",
        cp.name, duration_weeks, total_rate_impact_pct, trade_impact_pct,
    )
    return result


# ---------------------------------------------------------------------------
# get_current_active_disruptions
# ---------------------------------------------------------------------------

def get_current_active_disruptions() -> List[Chokepoint]:
    """
    Return all chokepoints that currently have an active disruption
    (i.e., disruption_type != NONE).
    """
    active = [
        cp for cp in CHOKEPOINTS.values()
        if cp.current_disruption_type != "NONE"
    ]
    logger.debug(
        "Active disruptions: {}",
        [cp.name for cp in active],
    )
    return active
