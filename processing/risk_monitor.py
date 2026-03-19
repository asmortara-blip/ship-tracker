"""
Shipping Chokepoint Risk Monitor

Tracks risk levels at critical maritime chokepoints that can disrupt global trade.
Risk levels are manually maintained but structured for future API/news integration.

Key chokepoints:
- Strait of Hormuz: 20% of global oil, Persian Gulf access
- Suez Canal: 12% of world trade, Asia-Europe shortcut
- Panama Canal: 5% of world trade, connects Pacific/Atlantic
- Strait of Malacca: 40% of world trade, SE Asia gateway
- Bab-el-Mandeb (Red Sea): Asia-Europe via Suez access
- Danish Straits: North Europe access
- Bosphorus/Turkish Straits: Black Sea access
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


RISK_LEVELS = {
    "LOW":      {"color": "#2ecc71", "score": 0.1},
    "MODERATE": {"color": "#f39c12", "score": 0.4},
    "HIGH":     {"color": "#e74c3c", "score": 0.8},
    "CRITICAL": {"color": "#c0392b", "score": 1.0},
}


@dataclass
class Chokepoint:
    id: str
    name: str
    lat: float
    lon: float
    region: str
    daily_vessels: int           # Approximate vessels per day
    pct_world_trade: float       # % of world trade passing through
    risk_level: str              # LOW | MODERATE | HIGH | CRITICAL
    risk_summary: str            # Current risk description
    affected_routes: list[str]   # Route IDs affected
    reroute_impact_days: int     # Extra transit days if rerouted
    last_updated: str            # ISO date string


# Current risk assessments (manually maintained — update as geopolitics evolve)
CHOKEPOINTS: list[Chokepoint] = [
    Chokepoint(
        id="suez",
        name="Suez Canal",
        lat=30.42, lon=32.35,
        region="Middle East / North Africa",
        daily_vessels=50,
        pct_world_trade=12.0,
        risk_level="HIGH",
        risk_summary="Houthi attacks in Red Sea continue to deter transits. Many carriers rerouting via Cape of Good Hope (+10-14 days, +$500-800/FEU fuel cost).",
        affected_routes=["asia_europe", "ningbo_europe", "south_asia_to_europe", "middle_east_to_europe", "north_africa_to_europe"],
        reroute_impact_days=12,
        last_updated="2025-03-01",
    ),
    Chokepoint(
        id="panama",
        name="Panama Canal",
        lat=9.08, lon=-79.68,
        region="Central America",
        daily_vessels=36,
        pct_world_trade=5.0,
        risk_level="MODERATE",
        risk_summary="Water levels recovering after 2024 drought restrictions. Draft limits have eased but remain below historical norms. Booking queues shortened.",
        affected_routes=["transpacific_eb", "us_east_south_america"],
        reroute_impact_days=20,
        last_updated="2025-03-01",
    ),
    Chokepoint(
        id="malacca",
        name="Strait of Malacca",
        lat=3.00, lon=101.00,
        region="Southeast Asia",
        daily_vessels=250,
        pct_world_trade=40.0,
        risk_level="LOW",
        risk_summary="Navigating normally. Piracy at historical lows. Traffic volumes high but manageable with vessel scheduling.",
        affected_routes=["sea_transpacific_eb", "asia_europe", "intra_asia_china_sea"],
        reroute_impact_days=7,
        last_updated="2025-03-01",
    ),
    Chokepoint(
        id="hormuz",
        name="Strait of Hormuz",
        lat=26.57, lon=56.25,
        region="Middle East",
        daily_vessels=21,
        pct_world_trade=20.0,  # By oil volume
        risk_level="MODERATE",
        risk_summary="Geopolitical tensions elevated. Tanker traffic monitored closely. Container shipping less directly exposed but oil price risk remains.",
        affected_routes=["middle_east_to_europe", "middle_east_to_asia"],
        reroute_impact_days=15,
        last_updated="2025-03-01",
    ),
    Chokepoint(
        id="bab_el_mandeb",
        name="Bab-el-Mandeb (Red Sea)",
        lat=12.58, lon=43.47,
        region="Horn of Africa",
        daily_vessels=48,
        pct_world_trade=10.0,
        risk_level="HIGH",
        risk_summary="Houthi drone/missile attacks ongoing. Major carriers avoiding Red Sea. Ships rerouting around Cape of Good Hope, adding 2 weeks and significant fuel cost.",
        affected_routes=["asia_europe", "ningbo_europe", "south_asia_to_europe", "middle_east_to_europe"],
        reroute_impact_days=14,
        last_updated="2025-03-01",
    ),
    Chokepoint(
        id="taiwan_strait",
        name="Taiwan Strait",
        lat=24.00, lon=120.00,
        region="Asia East",
        daily_vessels=300,
        pct_world_trade=26.0,
        risk_level="MODERATE",
        risk_summary="Cross-strait tensions elevated. Military exercises periodically restrict vessel routing. 26% of global container trade transits this waterway.",
        affected_routes=["transpacific_eb", "transpacific_wb", "intra_asia_china_sea", "intra_asia_china_japan"],
        reroute_impact_days=5,
        last_updated="2025-03-01",
    ),
    Chokepoint(
        id="cape_good_hope",
        name="Cape of Good Hope",
        lat=-34.35, lon=18.47,
        region="Southern Africa",
        daily_vessels=40,
        pct_world_trade=0.0,  # Alternative route, not normally primary
        risk_level="LOW",
        risk_summary="Currently serving as the de-facto alternative to Suez for Asia-Europe trade. Increased traffic due to Red Sea avoidance. Weather conditions normal for season.",
        affected_routes=["asia_europe", "ningbo_europe"],
        reroute_impact_days=0,  # IS the reroute
        last_updated="2025-03-01",
    ),
]

CHOKEPOINTS_BY_ID: dict[str, Chokepoint] = {c.id: c for c in CHOKEPOINTS}


def get_risk_score_for_route(route_id: str) -> float:
    """Return a composite risk score [0, 1] for a route based on chokepoints.

    Higher score = higher disruption risk = should negatively affect route opportunity.
    """
    relevant = [c for c in CHOKEPOINTS if route_id in c.affected_routes]
    if not relevant:
        return 0.0

    scores = [RISK_LEVELS.get(c.risk_level, {}).get("score", 0.0) for c in relevant]
    return min(1.0, sum(scores) / len(scores))


def get_high_risk_alerts() -> list[Chokepoint]:
    """Return all chokepoints currently at HIGH or CRITICAL risk."""
    return [c for c in CHOKEPOINTS if c.risk_level in ("HIGH", "CRITICAL")]


def get_color(risk_level: str) -> str:
    return RISK_LEVELS.get(risk_level, {}).get("color", "#95a5a6")
