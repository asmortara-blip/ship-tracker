"""intermodal_analyzer.py — Multi-modal freight comparison engine.

Compares ocean freight vs air freight vs rail (Belt and Road Initiative) vs
trucking for different cargo categories and routes.  Provides the analytical
backbone for the Intermodal tab.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Transport mode dataclass
# ---------------------------------------------------------------------------

@dataclass
class TransportMode:
    """Full specification for a single transport mode."""
    mode: str                          # "OCEAN" | "AIR" | "RAIL" | "TRUCK"
    cost_per_kg_usd: float             # USD per kilogram (average)
    transit_days_min: int              # minimum transit days
    transit_days_max: int              # maximum transit days
    co2_kg_per_kg_cargo: float         # kg CO2 per kg of cargo transported
    reliability_pct: float             # on-time reliability 0-100
    capacity_kg: float                 # per unit capacity (vessel/aircraft/train/truck)
    min_volume_kg: float               # practical minimum shipment
    max_weight_kg: float               # practical maximum per unit
    suitable_cargo: list[str]          # list of HS/cargo category keys
    key_advantage: str                 # primary selling point
    key_disadvantage: str              # primary limitation

    @property
    def transit_days_mid(self) -> float:
        """Midpoint transit estimate."""
        return (self.transit_days_min + self.transit_days_max) / 2.0


# ---------------------------------------------------------------------------
# TRANSPORT_MODES — 2026 realistic benchmark data
# ---------------------------------------------------------------------------

TRANSPORT_MODES: dict[str, TransportMode] = {
    "OCEAN": TransportMode(
        mode="OCEAN",
        cost_per_kg_usd=0.06,
        transit_days_min=20,
        transit_days_max=35,
        co2_kg_per_kg_cargo=0.015,
        reliability_pct=85.0,
        capacity_kg=26_000_000.0,    # large vessel ~26,000 TEU * ~1,000 kg/TEU
        min_volume_kg=500.0,         # LCL threshold
        max_weight_kg=26_000_000.0,
        suitable_cargo=[
            "electronics", "machinery", "automotive", "apparel",
            "chemicals", "agriculture", "metals",
        ],
        key_advantage="Lowest cost per kg by a wide margin; ideal for bulk and heavy cargo",
        key_disadvantage="Slowest transit; port congestion and Suez/Panama Canal risk",
    ),
    "AIR": TransportMode(
        mode="AIR",
        cost_per_kg_usd=4.50,
        transit_days_min=1,
        transit_days_max=3,
        co2_kg_per_kg_cargo=1.80,
        reliability_pct=95.0,
        capacity_kg=100_000.0,       # wide-body freighter ~100 MT
        min_volume_kg=1.0,
        max_weight_kg=100_000.0,
        suitable_cargo=["electronics", "apparel", "agriculture"],
        key_advantage="Fastest door-to-door; essential for perishables and high-value goods",
        key_disadvantage="Highest cost and carbon footprint; weight/volume constraints",
    ),
    "RAIL_CHINA_EUROPE": TransportMode(
        mode="RAIL_CHINA_EUROPE",
        cost_per_kg_usd=0.85,
        transit_days_min=12,
        transit_days_max=18,
        co2_kg_per_kg_cargo=0.08,
        reliability_pct=75.0,
        capacity_kg=2_100_000.0,    # 41-wagon block train * ~50,000 kg per wagon
        min_volume_kg=100.0,
        max_weight_kg=2_100_000.0,
        suitable_cargo=["electronics", "machinery", "automotive", "apparel", "chemicals"],
        key_advantage="Faster than ocean at ~14x lower cost than air; avoids Suez/Malacca chokepoints",
        key_disadvantage="Russia route disrupted post-2022; gauge changes add delays; limited capacity",
    ),
    "TRUCK_EU": TransportMode(
        mode="TRUCK_EU",
        cost_per_kg_usd=0.25,
        transit_days_min=3,
        transit_days_max=7,
        co2_kg_per_kg_cargo=0.20,
        reliability_pct=90.0,
        capacity_kg=24_000.0,       # standard EU 13.6m trailer
        min_volume_kg=50.0,
        max_weight_kg=24_000.0,
        suitable_cargo=["machinery", "automotive", "apparel", "chemicals", "agriculture"],
        key_advantage="Door-to-door flexibility; no port handling; intra-European distribution",
        key_disadvantage="Limited to overland; weight/distance costs escalate; driver shortage",
    ),
    "RAIL_US": TransportMode(
        mode="RAIL_US",
        cost_per_kg_usd=0.15,
        transit_days_min=5,
        transit_days_max=10,
        co2_kg_per_kg_cargo=0.04,
        reliability_pct=82.0,
        capacity_kg=8_000_000.0,   # 200-wagon unit train
        min_volume_kg=500.0,
        max_weight_kg=8_000_000.0,
        suitable_cargo=["agriculture", "chemicals", "metals", "automotive"],
        key_advantage="Cost-effective coast-to-coast; lowest CO2 of any land mode; high payload",
        key_disadvantage="No door-to-door; dependent on intermodal ramp network; slower than truck",
    ),
}

# Convenience alias for transit midpoints (used in scatter chart)
_MODE_TRANSIT_MID: dict[str, float] = {
    k: v.transit_days_mid for k, v in TRANSPORT_MODES.items()
}


# ---------------------------------------------------------------------------
# IntermodalComparison dataclass
# ---------------------------------------------------------------------------

@dataclass
class IntermodalComparison:
    """Result of a single compare_modes() call."""
    cargo_category: str
    weight_kg: float
    origin_city: str
    dest_city: str

    # Ocean
    ocean_cost: float            # total USD
    ocean_days: float            # midpoint transit days

    # Air
    air_cost: float              # total USD
    air_days: float

    # Rail (optional — only some routes)
    rail_cost: Optional[float]   # total USD; None if rail N/A for this route
    rail_days: Optional[float]

    # Truck (optional — only intra-regional)
    truck_cost: Optional[float]
    truck_days: Optional[float]

    # Recommendation
    recommended_mode: str        # "OCEAN" | "AIR" | "RAIL_CHINA_EUROPE" | "TRUCK_EU" | "RAIL_US"
    recommendation_rationale: str

    # Derived metrics
    cost_premium_air_vs_ocean_pct: float       # (air_cost - ocean_cost) / ocean_cost * 100
    co2_comparison: dict[str, float]           # mode -> total kg CO2 for this shipment


# ---------------------------------------------------------------------------
# Route definitions for intermodal analysis
# ---------------------------------------------------------------------------

# Maps route_id -> (origin_city, dest_city, available_modes, is_bri_eligible)
_ROUTE_INTERMODAL: dict[str, tuple[str, str, list[str], bool]] = {
    "transpacific_eb":      ("Shanghai",    "Los Angeles",  ["OCEAN", "AIR"],                       False),
    "asia_europe":          ("Shanghai",    "Rotterdam",    ["OCEAN", "AIR", "RAIL_CHINA_EUROPE"],   True),
    "transpacific_wb":      ("Los Angeles", "Shanghai",     ["OCEAN", "AIR"],                       False),
    "transatlantic":        ("Rotterdam",   "New York",     ["OCEAN", "AIR", "TRUCK_EU"],            False),
    "sea_transpacific_eb":  ("Singapore",   "Los Angeles",  ["OCEAN", "AIR"],                       False),
    "ningbo_europe":        ("Ningbo",      "Antwerp",      ["OCEAN", "AIR", "RAIL_CHINA_EUROPE"],   True),
    "middle_east_to_europe":("Dubai",       "Rotterdam",    ["OCEAN", "AIR"],                       False),
    "middle_east_to_asia":  ("Dubai",       "Shanghai",     ["OCEAN", "AIR"],                       False),
    "south_asia_to_europe": ("Colombo",     "Felixstowe",   ["OCEAN", "AIR"],                       False),
    "intra_asia_china_sea": ("Shanghai",    "Singapore",    ["OCEAN", "AIR"],                       False),
    "intra_asia_china_japan":("Shanghai",   "Yokohama",     ["OCEAN", "AIR"],                       False),
    "china_south_america":  ("Shanghai",    "Santos",       ["OCEAN", "AIR"],                       False),
    "europe_south_america": ("Rotterdam",   "Santos",       ["OCEAN", "AIR"],                       False),
    "med_hub_to_asia":      ("Piraeus",     "Shanghai",     ["OCEAN", "AIR"],                       False),
    "north_africa_to_europe":("Tanger",     "Rotterdam",    ["OCEAN", "AIR", "TRUCK_EU"],            False),
    "us_east_south_america":("Savannah",    "Santos",       ["OCEAN", "AIR"],                       False),
    "longbeach_to_asia":    ("Long Beach",  "Shanghai",     ["OCEAN", "AIR"],                       False),
    # US domestic intermodal
    "us_coast_to_coast":    ("Los Angeles", "Chicago",      ["RAIL_US", "TRUCK_EU", "AIR"],         False),
    # Intra-EU
    "intra_eu":             ("Hamburg",     "Madrid",       ["TRUCK_EU", "RAIL_US", "AIR"],         False),
}

# Cargo categories that are HIGH_VALUE (triggers air-first logic for small shipments)
_HIGH_VALUE_CATEGORIES: set[str] = {"electronics"}

# Cargo categories that are PERISHABLE (air required unless reefer ocean viable)
_PERISHABLE_CATEGORIES: set[str] = {"agriculture"}

# Weight threshold in kg below which air is preferred for HIGH_VALUE + URGENT
_AIR_HIGH_VALUE_THRESHOLD_KG: float = 200.0
_AIR_URGENT_THRESHOLD_KG: float = 100.0
_RAIL_URGENT_THRESHOLD_KG: float = 500.0


# ---------------------------------------------------------------------------
# Core comparison function
# ---------------------------------------------------------------------------

def compare_modes(
    cargo_category: str,
    weight_kg: float,
    route_id: str,
    urgency: str = "NORMAL",
) -> IntermodalComparison:
    """Compare all available transport modes for a given cargo+route combination.

    Parameters
    ----------
    cargo_category:
        One of the HS category keys: "electronics", "machinery", "automotive",
        "apparel", "chemicals", "agriculture", "metals".
    weight_kg:
        Shipment weight in kilograms.
    route_id:
        A key from _ROUTE_INTERMODAL (or any key from the route registry).
        Unmapped routes fall back to OCEAN+AIR only.
    urgency:
        "NORMAL" | "URGENT" | "CRITICAL"

    Returns
    -------
    IntermodalComparison
    """
    logger.debug(
        "compare_modes: cat={} weight={} route={} urgency={}",
        cargo_category, weight_kg, route_id, urgency,
    )

    route_info = _ROUTE_INTERMODAL.get(route_id)
    if route_info is None:
        logger.warning("route_id '{}' not in intermodal registry — defaulting", route_id)
        origin_city, dest_city = "Origin", "Destination"
        available_modes = ["OCEAN", "AIR"]
        is_bri = False
    else:
        origin_city, dest_city, available_modes, is_bri = route_info

    ocean = TRANSPORT_MODES["OCEAN"]
    air   = TRANSPORT_MODES["AIR"]

    # --- Compute costs for all modes present on this route ---
    ocean_cost  = ocean.cost_per_kg_usd * weight_kg
    ocean_days  = ocean.transit_days_mid

    air_cost    = air.cost_per_kg_usd * weight_kg
    air_days    = air.transit_days_mid

    rail_cost: Optional[float] = None
    rail_days: Optional[float] = None
    if "RAIL_CHINA_EUROPE" in available_modes:
        rail_mode = TRANSPORT_MODES["RAIL_CHINA_EUROPE"]
        rail_cost = rail_mode.cost_per_kg_usd * weight_kg
        rail_days = rail_mode.transit_days_mid

    truck_cost: Optional[float] = None
    truck_days: Optional[float] = None
    if "TRUCK_EU" in available_modes:
        truck_mode = TRANSPORT_MODES["TRUCK_EU"]
        truck_cost = truck_mode.cost_per_kg_usd * weight_kg
        truck_days = truck_mode.transit_days_mid

    if "RAIL_US" in available_modes and rail_cost is None:
        rail_us_mode = TRANSPORT_MODES["RAIL_US"]
        rail_cost = rail_us_mode.cost_per_kg_usd * weight_kg
        rail_days = rail_us_mode.transit_days_mid

    # --- Recommendation logic ---
    is_perishable  = cargo_category in _PERISHABLE_CATEGORIES
    is_high_value  = cargo_category in _HIGH_VALUE_CATEGORIES

    recommended_mode: str
    rationale: str

    if is_perishable:
        if weight_kg <= 10_000.0:
            recommended_mode = "AIR"
            rationale = (
                "Perishable cargo requires air freight for shipments under 10 t "
                "to prevent spoilage. Reefer ocean viable for large volumes "
                "(>10,000 kg) with cold-chain management."
            )
        else:
            recommended_mode = "OCEAN"
            rationale = (
                "Large perishable shipment (>10 t): specialized reefer ocean is "
                "the practical choice. Temperature-controlled containers maintain "
                "cold chain at ~14x lower cost than air."
            )

    elif urgency == "CRITICAL":
        recommended_mode = "AIR"
        rationale = (
            "CRITICAL urgency mandates air freight regardless of cost premium. "
            "Air delivers in 1-3 days vs 20-35 days ocean."
        )

    elif urgency == "URGENT":
        if weight_kg <= _AIR_URGENT_THRESHOLD_KG:
            recommended_mode = "AIR"
            rationale = (
                "URGENT + light shipment (<100 kg): air freight balances speed "
                "and acceptable absolute cost."
            )
        elif weight_kg <= _RAIL_URGENT_THRESHOLD_KG and rail_cost is not None:
            recommended_mode = "RAIL_CHINA_EUROPE"
            rationale = (
                "URGENT but moderate weight (100-500 kg): Belt and Road rail "
                "delivers in 12-18 days at ~14x lower cost than air, making "
                "it the optimal middle-ground for this corridor."
            )
        else:
            recommended_mode = "OCEAN"
            rationale = (
                "URGENT but heavy shipment (>500 kg) where rail unavailable: "
                "ocean remains the cost-optimal mode. Consider split-shipping "
                "time-critical components by air."
            )

    elif is_high_value:
        if weight_kg <= _AIR_HIGH_VALUE_THRESHOLD_KG:
            recommended_mode = "AIR"
            rationale = (
                "High-value electronics (<200 kg): air freight minimises "
                "inventory-in-transit carrying cost and theft/damage risk. "
                "The air premium is justified by reduced working capital."
            )
        elif rail_cost is not None and weight_kg <= 5_000.0:
            recommended_mode = "RAIL_CHINA_EUROPE"
            rationale = (
                "High-value electronics (200 kg-5 t) on a BRI corridor: rail "
                "offers a 12-18 day transit — far faster than ocean — at "
                "roughly a fifth of air cost."
            )
        else:
            recommended_mode = "OCEAN"
            rationale = (
                "High-value cargo but large volume (>5 t) or no rail corridor: "
                "ocean is cost-optimal. Use FCL for security and insurance."
            )

    else:
        # NORMAL urgency, standard cargo
        if truck_cost is not None and weight_kg <= 24_000.0:
            recommended_mode = "TRUCK_EU"
            rationale = (
                "Intra-regional route with truck available: door-to-door trucking "
                "offers 3-7 day delivery at moderate cost with no port handling."
            )
        else:
            recommended_mode = "OCEAN"
            rationale = (
                "NORMAL urgency standard cargo: ocean freight is the clear "
                "winner on cost at $0.06/kg vs $4.50/kg for air. "
                "20-35 day transit is acceptable for most supply chains."
            )

    # --- CO2 comparison (total kg CO2 for this shipment) ---
    co2_comparison: dict[str, float] = {
        "OCEAN": ocean.co2_kg_per_kg_cargo * weight_kg,
        "AIR":   air.co2_kg_per_kg_cargo * weight_kg,
    }
    if rail_cost is not None:
        bri_mode = TRANSPORT_MODES.get("RAIL_CHINA_EUROPE") or TRANSPORT_MODES.get("RAIL_US")
        if bri_mode:
            co2_comparison["RAIL"] = bri_mode.co2_kg_per_kg_cargo * weight_kg
    if truck_cost is not None:
        co2_comparison["TRUCK"] = TRANSPORT_MODES["TRUCK_EU"].co2_kg_per_kg_cargo * weight_kg

    cost_premium_air_vs_ocean_pct = (
        (air_cost - ocean_cost) / ocean_cost * 100.0
        if ocean_cost > 0
        else 0.0
    )

    return IntermodalComparison(
        cargo_category=cargo_category,
        weight_kg=weight_kg,
        origin_city=origin_city,
        dest_city=dest_city,
        ocean_cost=ocean_cost,
        ocean_days=ocean_days,
        air_cost=air_cost,
        air_days=air_days,
        rail_cost=rail_cost,
        rail_days=rail_days,
        truck_cost=truck_cost,
        truck_days=truck_days,
        recommended_mode=recommended_mode,
        recommendation_rationale=rationale,
        cost_premium_air_vs_ocean_pct=round(cost_premium_air_vs_ocean_pct, 1),
        co2_comparison=co2_comparison,
    )


# ---------------------------------------------------------------------------
# Belt and Road Initiative special class
# ---------------------------------------------------------------------------

@dataclass
class BRICorridorSpec:
    """Specification for a single BRI rail corridor."""
    name: str                        # human-readable corridor name
    origin_city: str
    dest_city: str
    transit_days: int                # typical door-to-door transit
    cost_vs_ocean_ratio: float       # BRI cost / ocean cost (e.g. 14.0 means 14x more)
    disruption_risk: str             # "LOW" | "MODERATE" | "HIGH"
    disruption_note: str             # geopolitical/operational context
    lat_waypoints: list[float]       # approximate latitudes along route
    lon_waypoints: list[float]       # approximate longitudes along route


class BeltAndRoad:
    """China-Europe rail corridor analysis via the Belt and Road Initiative.

    Covers 7 main rail corridors, their transit times, cost ratios versus
    ocean, market share, and growth trajectory.
    """

    # 7 main BRI rail corridors (2026 operational data)
    CORRIDORS: list[BRICorridorSpec] = [
        BRICorridorSpec(
            name="Yiwu-Madrid",
            origin_city="Yiwu",
            dest_city="Madrid",
            transit_days=21,
            cost_vs_ocean_ratio=14.2,
            disruption_risk="HIGH",
            disruption_note=(
                "Routed via Russia/Belarus — heavily disrupted since 2022 "
                "Ukraine sanctions.  Services now detoured via Kazakhstan-Caspian "
                "Sea ferry (Middle Corridor), adding 5-7 days."
            ),
            lat_waypoints=[29.3, 40.0, 51.2, 53.9, 52.0, 50.5, 40.4],
            lon_waypoints=[120.1, 75.0, 71.5, 27.6, 16.0,  6.5, -3.7],
        ),
        BRICorridorSpec(
            name="Chongqing-Duisburg",
            origin_city="Chongqing",
            dest_city="Duisburg",
            transit_days=16,
            cost_vs_ocean_ratio=13.5,
            disruption_risk="HIGH",
            disruption_note=(
                "Russia transit route — post-2022 diversions via Middle Corridor "
                "increased transit to 18-22 days.  Chongqing-Europe volumes fell "
                "significantly; European importers pivoting to ocean or southern route."
            ),
            lat_waypoints=[29.6, 44.0, 51.2, 53.9, 52.2, 51.4],
            lon_waypoints=[106.6, 80.0, 71.5, 28.0, 16.4,  6.8],
        ),
        BRICorridorSpec(
            name="Xi'an-Hamburg",
            origin_city="Xi'an",
            dest_city="Hamburg",
            transit_days=15,
            cost_vs_ocean_ratio=13.0,
            disruption_risk="MODERATE",
            disruption_note=(
                "Mixed routing available: northern (Russia) and Middle Corridor "
                "(Caspian ferry).  Middle route adds cost but remains operational "
                "and avoids EU/UK sanctions exposure."
            ),
            lat_waypoints=[34.3, 44.0, 43.5, 45.0, 52.0, 53.6],
            lon_waypoints=[108.9, 80.0, 52.5, 35.0, 14.0,  9.9],
        ),
        BRICorridorSpec(
            name="Zhengzhou-Hamburg",
            origin_city="Zhengzhou",
            dest_city="Hamburg",
            transit_days=17,
            cost_vs_ocean_ratio=13.8,
            disruption_risk="HIGH",
            disruption_note=(
                "Primarily Russia-routed; service frequency has dropped sharply. "
                "Trains are now re-routed via Turkey-Georgia Middle Corridor."
            ),
            lat_waypoints=[34.7, 44.0, 43.5, 41.7, 52.0, 53.6],
            lon_waypoints=[113.6, 80.0, 52.5, 44.8, 14.0,  9.9],
        ),
        BRICorridorSpec(
            name="Wuhan-Lyon (Hanxin)",
            origin_city="Wuhan",
            dest_city="Lyon",
            transit_days=18,
            cost_vs_ocean_ratio=14.5,
            disruption_risk="MODERATE",
            disruption_note=(
                "France-China rail established 2021.  Service adjusted to Middle "
                "Corridor via Turkey.  Strong automotive and industrial goods flow."
            ),
            lat_waypoints=[30.6, 44.0, 43.5, 41.7, 47.5, 45.7],
            lon_waypoints=[114.3, 80.0, 52.5, 44.8, 15.0,  4.8],
        ),
        BRICorridorSpec(
            name="Chengdu-Lodz (Poland Hub)",
            origin_city="Chengdu",
            dest_city="Lodz",
            transit_days=14,
            cost_vs_ocean_ratio=12.8,
            disruption_risk="HIGH",
            disruption_note=(
                "Lodz is a major BRI distribution hub for Central/Eastern Europe. "
                "Post-2022 Russian transit banned; now via Kazakhstan-Caspian-Turkey "
                "or via Mongolia-Russia (frozen).  Service disrupted."
            ),
            lat_waypoints=[30.6, 44.0, 51.2, 53.9, 51.8],
            lon_waypoints=[104.0, 80.0, 71.5, 27.6, 19.5],
        ),
        BRICorridorSpec(
            name="Yiwu-London (UK Extension)",
            origin_city="Yiwu",
            dest_city="London",
            transit_days=22,
            cost_vs_ocean_ratio=15.0,
            disruption_risk="MODERATE",
            disruption_note=(
                "Post-Brexit UK leg adds Channel crossing complexity.  "
                "Service via Channel Tunnel from Paris.  Growing but niche; "
                "still a fraction of TP ocean volumes."
            ),
            lat_waypoints=[29.3, 40.0, 43.5, 41.7, 48.9, 51.5],
            lon_waypoints=[120.1, 75.0, 52.5, 44.8,  2.3, -0.1],
        ),
    ]

    # Market share (% of Asia-Europe total trade volume by weight/TEU)
    MARKET_SHARE_PCT: float = 5.0        # ~5% as of 2026

    # Growth trajectory (indexed to 2015=100)
    GROWTH_INDEX: dict[int, float] = {
        2015: 100.0,
        2016: 145.0,
        2017: 240.0,
        2018: 390.0,
        2019: 590.0,
        2020: 820.0,   # COVID drove surge
        2021: 1150.0,
        2022: 920.0,   # Russia sanctions disruption
        2023: 780.0,
        2024: 850.0,
        2025: 910.0,   # Middle Corridor ramp-up
        2026: 960.0,
    }

    @classmethod
    def get_corridor_summary(cls) -> list[dict]:
        """Return list of corridor summary dicts for display."""
        summaries: list[dict] = []
        for c in cls.CORRIDORS:
            summaries.append({
                "name": c.name,
                "origin": c.origin_city,
                "destination": c.dest_city,
                "transit_days": c.transit_days,
                "cost_vs_ocean_ratio": c.cost_vs_ocean_ratio,
                "disruption_risk": c.disruption_risk,
                "disruption_note": c.disruption_note,
            })
        return summaries

    @classmethod
    def ocean_transit_days(cls) -> float:
        """Reference ocean transit for Asia-Europe (Shanghai-Rotterdam midpoint)."""
        return TRANSPORT_MODES["OCEAN"].transit_days_mid

    @classmethod
    def get_growth_series(cls) -> tuple[list[int], list[float]]:
        """Return (years, index_values) for growth trend chart."""
        years = sorted(cls.GROWTH_INDEX.keys())
        values = [cls.GROWTH_INDEX[y] for y in years]
        return years, values


# ---------------------------------------------------------------------------
# Key air freight routes and monitor data
# ---------------------------------------------------------------------------

AIR_KEY_ROUTES: list[dict] = [
    {
        "route": "Shanghai (PVG) → Los Angeles (LAX)",
        "route_id": "PVG-LAX",
        "normal_rate_usd_kg": 4.20,
        "peak_rate_usd_kg": 9.80,
        "utilization_pct": 82.0,
        "dominant_cargo": "Electronics, e-commerce, garments",
        "note": "Highest-volume air cargo lane globally; mirrors transpacific ocean lane",
    },
    {
        "route": "Frankfurt (FRA) → New York (JFK)",
        "route_id": "FRA-JFK",
        "normal_rate_usd_kg": 3.80,
        "peak_rate_usd_kg": 7.20,
        "utilization_pct": 79.0,
        "dominant_cargo": "Pharmaceuticals, automotive parts, machinery",
        "note": "Key transatlantic air freight corridor; Pharma sensitivity to temp",
    },
    {
        "route": "Hong Kong (HKG) → London (LHR)",
        "route_id": "HKG-LHR",
        "normal_rate_usd_kg": 4.60,
        "peak_rate_usd_kg": 10.50,
        "utilization_pct": 85.0,
        "dominant_cargo": "Luxury goods, electronics, perishables",
        "note": "Historically strong route; HKG belly capacity supplement via Cathay",
    },
]

# Historical surge events for annotation
AIR_SURGE_EVENTS: list[dict] = [
    {
        "year": 2020,
        "label": "COVID-19 PPE Rush",
        "description": (
            "Passenger belly capacity evaporated instantly; air cargo rates tripled "
            "as governments competed for PPE.  Shanghai-US rates hit $15/kg."
        ),
        "peak_rate_multiplier": 3.5,
    },
    {
        "year": 2021,
        "label": "Electronics / Chip Shortage",
        "description": (
            "Semiconductor shortage drove emergency air shipments of IC components. "
            "Air vs ocean ratio peaked at 90x as ocean was congested simultaneously."
        ),
        "peak_rate_multiplier": 2.2,
    },
    {
        "year": 2024,
        "label": "Red Sea Diversion Spike",
        "description": (
            "Houthi attacks rerouted ocean via Cape of Good Hope, adding 2+ weeks. "
            "Time-sensitive shippers shifted to air, pushing rates 40% above normal."
        ),
        "peak_rate_multiplier": 1.4,
    },
]

# Historical air-vs-ocean rate ratio (approximate; normally 50-100x)
AIR_OCEAN_RATIO_HISTORY: dict[int, float] = {
    2018: 58.0,
    2019: 55.0,
    2020: 165.0,   # COVID surge
    2021: 110.0,   # chip shortage + congestion
    2022: 85.0,
    2023: 70.0,
    2024: 78.0,    # Red Sea effect
    2025: 72.0,
    2026: 75.0,
}


# ---------------------------------------------------------------------------
# Mode split forecast
# ---------------------------------------------------------------------------

# Baseline mode split by route and cargo category (share of volume)
_ROUTE_MODE_SPLIT_BASELINE: dict[str, dict[str, dict[str, float]]] = {
    "transpacific_eb": {
        "electronics":  {"OCEAN": 0.58, "AIR": 0.42, "RAIL": 0.00, "TRUCK": 0.00},
        "machinery":    {"OCEAN": 0.90, "AIR": 0.05, "RAIL": 0.00, "TRUCK": 0.05},
        "apparel":      {"OCEAN": 0.96, "AIR": 0.04, "RAIL": 0.00, "TRUCK": 0.00},
        "chemicals":    {"OCEAN": 0.98, "AIR": 0.02, "RAIL": 0.00, "TRUCK": 0.00},
        "agriculture":  {"OCEAN": 0.85, "AIR": 0.15, "RAIL": 0.00, "TRUCK": 0.00},
        "metals":       {"OCEAN": 0.99, "AIR": 0.01, "RAIL": 0.00, "TRUCK": 0.00},
        "automotive":   {"OCEAN": 0.97, "AIR": 0.02, "RAIL": 0.00, "TRUCK": 0.01},
    },
    "asia_europe": {
        "electronics":  {"OCEAN": 0.62, "AIR": 0.28, "RAIL": 0.10, "TRUCK": 0.00},
        "machinery":    {"OCEAN": 0.82, "AIR": 0.06, "RAIL": 0.10, "TRUCK": 0.02},
        "apparel":      {"OCEAN": 0.88, "AIR": 0.06, "RAIL": 0.05, "TRUCK": 0.01},
        "chemicals":    {"OCEAN": 0.90, "AIR": 0.04, "RAIL": 0.05, "TRUCK": 0.01},
        "agriculture":  {"OCEAN": 0.80, "AIR": 0.18, "RAIL": 0.02, "TRUCK": 0.00},
        "metals":       {"OCEAN": 0.95, "AIR": 0.01, "RAIL": 0.04, "TRUCK": 0.00},
        "automotive":   {"OCEAN": 0.87, "AIR": 0.03, "RAIL": 0.08, "TRUCK": 0.02},
    },
}

# Generic fallback split when no route-specific data exists
_GENERIC_MODE_SPLIT: dict[str, dict[str, float]] = {
    "electronics":  {"OCEAN": 0.62, "AIR": 0.35, "RAIL": 0.02, "TRUCK": 0.01},
    "machinery":    {"OCEAN": 0.88, "AIR": 0.06, "RAIL": 0.04, "TRUCK": 0.02},
    "apparel":      {"OCEAN": 0.93, "AIR": 0.05, "RAIL": 0.01, "TRUCK": 0.01},
    "chemicals":    {"OCEAN": 0.94, "AIR": 0.03, "RAIL": 0.02, "TRUCK": 0.01},
    "agriculture":  {"OCEAN": 0.82, "AIR": 0.16, "RAIL": 0.01, "TRUCK": 0.01},
    "metals":       {"OCEAN": 0.97, "AIR": 0.01, "RAIL": 0.01, "TRUCK": 0.01},
    "automotive":   {"OCEAN": 0.91, "AIR": 0.03, "RAIL": 0.04, "TRUCK": 0.02},
}


def compute_mode_split_forecast(route_id: str) -> dict:
    """Estimate ocean/air/rail/truck mode split for a route by cargo category.

    Returns
    -------
    dict of cargo_category -> {"OCEAN": float, "AIR": float,
                                "RAIL": float, "TRUCK": float}
    where each value is a share in [0, 1] summing to 1.0.
    """
    logger.debug("compute_mode_split_forecast: route_id={}", route_id)

    route_split = _ROUTE_MODE_SPLIT_BASELINE.get(route_id, {})
    from processing.cargo_analyzer import HS_CATEGORIES  # avoid circular at module level
    categories = list(HS_CATEGORIES.keys())

    result: dict[str, dict[str, float]] = {}
    for cat in categories:
        if cat in route_split:
            split = dict(route_split[cat])
        else:
            split = dict(_GENERIC_MODE_SPLIT.get(cat, {"OCEAN": 0.95, "AIR": 0.04, "RAIL": 0.01, "TRUCK": 0.00}))

        # Normalise so shares sum to exactly 1.0
        total = sum(split.values())
        if total > 0:
            result[cat] = {k: round(v / total, 4) for k, v in split.items()}
        else:
            result[cat] = split

    logger.debug("mode_split_forecast for {} complete — {} categories", route_id, len(result))
    return result


# ---------------------------------------------------------------------------
# Carbon cost helper (EU ETS at $80/tonne)
# ---------------------------------------------------------------------------

_EU_ETS_USD_PER_TONNE_CO2: float = 80.0


def compute_carbon_costs(weight_kg: float) -> dict[str, dict[str, float]]:
    """Compute total cost and carbon offset cost per mode for a given weight.

    Returns
    -------
    dict of mode_key -> {
        "freight_cost_usd":  float,
        "co2_kg":            float,
        "carbon_offset_usd": float,
        "total_cost_usd":    float,
    }
    """
    results: dict[str, dict[str, float]] = {}
    for mode_key, mode in TRANSPORT_MODES.items():
        freight = mode.cost_per_kg_usd * weight_kg
        co2_kg  = mode.co2_kg_per_kg_cargo * weight_kg
        co2_tonne = co2_kg / 1_000.0
        carbon_offset = co2_tonne * _EU_ETS_USD_PER_TONNE_CO2
        results[mode_key] = {
            "freight_cost_usd":  round(freight, 2),
            "co2_kg":            round(co2_kg, 4),
            "carbon_offset_usd": round(carbon_offset, 2),
            "total_cost_usd":    round(freight + carbon_offset, 2),
        }
    return results
