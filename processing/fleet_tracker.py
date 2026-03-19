"""Global container fleet capacity, orderbook, and supply-side shipping dynamics.

Data sourced from Clarksons/Alphaliner 2025 estimates (hardcoded baseline).
In production this module would query live APIs for updated orderbook data.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── FleetSnapshot dataclass ───────────────────────────────────────────────────

@dataclass
class FleetSnapshot:
    total_teu_capacity_m: float        # millions TEU currently deployed
    orderbook_teu_m: float             # TEU on order (future supply)
    orderbook_pct: float               # orderbook / existing fleet %
    deliveries_next_12m_teu_m: float   # expected deliveries next 12 months
    scrapping_rate_annual_pct: float   # % of fleet being scrapped/year
    net_supply_growth_pct: float       # deliveries - scrapping expressed as %
    demand_growth_estimate_pct: float  # based on macro outlook
    supply_demand_balance: float       # demand_growth - supply_growth (positive = tight)
    market_tightness: str              # "VERY_TIGHT" | "TIGHT" | "BALANCED" | "LOOSE" | "OVERSUPPLIED"
    tightness_color: str
    implications: list[str] = field(default_factory=list)  # 3 bullet points for traders
    data_vintage: str = ""             # source / vintage label


# ── Vessel size categories ────────────────────────────────────────────────────

VESSEL_CATEGORIES: list[dict] = [
    {"name": "Ultra Large (>18K TEU)", "fleet_share": 0.22, "orderbook_share": 0.45, "avg_age": 4.2},
    {"name": "Very Large (12-18K TEU)", "fleet_share": 0.28, "orderbook_share": 0.30, "avg_age": 7.1},
    {"name": "Large (8-12K TEU)",       "fleet_share": 0.20, "orderbook_share": 0.15, "avg_age": 10.3},
    {"name": "Medium (4-8K TEU)",       "fleet_share": 0.15, "orderbook_share": 0.07, "avg_age": 14.2},
    {"name": "Feeder (<4K TEU)",        "fleet_share": 0.15, "orderbook_share": 0.03, "avg_age": 16.8},
]


# ── Hardcoded 2025 baseline (Clarksons / Alphaliner estimates) ────────────────

FLEET_2025 = FleetSnapshot(
    total_teu_capacity_m=28.5,
    orderbook_teu_m=8.2,
    orderbook_pct=28.8,
    deliveries_next_12m_teu_m=3.1,
    scrapping_rate_annual_pct=0.8,
    net_supply_growth_pct=10.1,
    demand_growth_estimate_pct=3.5,
    supply_demand_balance=-6.6,   # negative = oversupplied
    market_tightness="LOOSE",
    tightness_color="#ef4444",
    implications=[
        "Record orderbook at 28.8% of fleet signals structural oversupply through 2026-2027",
        "Net supply growth ~10% vs demand growth ~3.5% = bearish for long-term freight rates",
        "Short-term rate spikes possible from geopolitical disruptions (Suez/Panama)",
    ],
    data_vintage="2025 estimates (Clarksons/Alphaliner baseline)",
)


# ── Public API ────────────────────────────────────────────────────────────────

def get_fleet_data() -> FleetSnapshot:
    """Return the current fleet snapshot.

    Returns FLEET_2025 static baseline.  In production this would query
    Clarksons Research / Alphaliner APIs for live orderbook data.
    """
    return FLEET_2025


def get_supply_pressure_score() -> float:
    """Return a supply-pressure score in [0, 1].

    0 = severe oversupply, 1 = very tight market.
    Formula: 1 - clamp((net_supply_growth - demand_growth + 5) / 10, 0, 1)
    """
    fleet = get_fleet_data()
    raw = (fleet.net_supply_growth_pct - fleet.demand_growth_estimate_pct + 5.0) / 10.0
    clamped = min(1.0, max(0.0, raw))
    return 1.0 - clamped
