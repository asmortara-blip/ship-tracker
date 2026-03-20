"""equipment_tracker.py — Container equipment availability, utilization, and
trade-imbalance repositioning costs.

Container equipment availability is a major driver of shipping costs and delays.
The post-COVID shortage has eased for dry containers, but reefer units remain
structurally tight globally (2025/2026 baseline).

Data sourced from: Container xChange, Drewry, Triton International reports
(hardcoded 2025/2026 estimates — replace with live API calls in production).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from loguru import logger


# ── Container type literals ────────────────────────────────────────────────

CONTAINER_TYPES: List[str] = [
    "20FT_DRY",
    "40FT_DRY",
    "40FT_HC",
    "40FT_REEFER",
    "20FT_TANK",
]

REGIONS: List[str] = [
    "Asia Pacific",
    "North America",
    "Europe",
    "South America",
    "Middle East",
    "Africa",
]


# ── EquipmentStatus dataclass ──────────────────────────────────────────────

@dataclass
class EquipmentStatus:
    """Availability snapshot for one region × container-type combination."""

    region: str
    container_type: str            # "20FT_DRY" | "40FT_DRY" | "40FT_HC" | "40FT_REEFER" | "20FT_TANK"
    available_units_k: float       # thousands of units
    utilization_pct: float         # 0–100
    shortage_risk: str             # "CRITICAL" | "HIGH" | "MODERATE" | "LOW"
    daily_lease_rate_usd: float    # USD per container per day
    vs_year_ago_pct: float         # utilization change vs. prior year (pp)
    days_surplus_deficit: int      # positive = surplus days of supply, negative = deficit


# ── REGIONAL_EQUIPMENT_STATUS — 2025/2026 hardcoded baseline ──────────────
#
# Market context (2025/2026):
#  • Post-COVID dry-container surplus — global newbuild surge 2022-2024 restored
#    fleet adequacy; Asia holds large surpluses of 20FT/40FT dry boxes.
#  • North America import-heavy imbalance → chronic dry deficit on arrival ports.
#  • Reefer containers remain structurally tight (long build cycles, high demand
#    from pharma / perishables / e-commerce).
#  • Tank containers moderate: petrochemical trade steady but not surging.
#  • South America / Africa: thin fleets, higher risk ratings despite lower volumes.

REGIONAL_EQUIPMENT_STATUS: List[EquipmentStatus] = [

    # ── Asia Pacific ────────────────────────────────────────────────────────
    # Export-dominant economy → surplus of empties waiting repositioning
    EquipmentStatus(
        region="Asia Pacific",
        container_type="20FT_DRY",
        available_units_k=1420.0,
        utilization_pct=62.0,
        shortage_risk="LOW",
        daily_lease_rate_usd=0.48,
        vs_year_ago_pct=-4.5,
        days_surplus_deficit=28,
    ),
    EquipmentStatus(
        region="Asia Pacific",
        container_type="40FT_DRY",
        available_units_k=2180.0,
        utilization_pct=71.0,
        shortage_risk="LOW",
        daily_lease_rate_usd=0.65,
        vs_year_ago_pct=-3.0,
        days_surplus_deficit=22,
    ),
    EquipmentStatus(
        region="Asia Pacific",
        container_type="40FT_HC",
        available_units_k=980.0,
        utilization_pct=74.0,
        shortage_risk="LOW",
        daily_lease_rate_usd=0.72,
        vs_year_ago_pct=-2.0,
        days_surplus_deficit=18,
    ),
    EquipmentStatus(
        region="Asia Pacific",
        container_type="40FT_REEFER",
        available_units_k=185.0,
        utilization_pct=87.0,
        shortage_risk="HIGH",
        daily_lease_rate_usd=3.20,
        vs_year_ago_pct=+3.5,
        days_surplus_deficit=-12,
    ),
    EquipmentStatus(
        region="Asia Pacific",
        container_type="20FT_TANK",
        available_units_k=42.0,
        utilization_pct=79.0,
        shortage_risk="MODERATE",
        daily_lease_rate_usd=2.10,
        vs_year_ago_pct=+1.5,
        days_surplus_deficit=8,
    ),

    # ── North America ───────────────────────────────────────────────────────
    # Import-heavy: containers pile up inland, empties must be repositioned back
    EquipmentStatus(
        region="North America",
        container_type="20FT_DRY",
        available_units_k=310.0,
        utilization_pct=82.0,
        shortage_risk="MODERATE",
        daily_lease_rate_usd=1.05,
        vs_year_ago_pct=+2.0,
        days_surplus_deficit=-8,
    ),
    EquipmentStatus(
        region="North America",
        container_type="40FT_DRY",
        available_units_k=520.0,
        utilization_pct=83.0,
        shortage_risk="MODERATE",
        daily_lease_rate_usd=1.20,
        vs_year_ago_pct=+1.5,
        days_surplus_deficit=-10,
    ),
    EquipmentStatus(
        region="North America",
        container_type="40FT_HC",
        available_units_k=390.0,
        utilization_pct=85.0,
        shortage_risk="HIGH",
        daily_lease_rate_usd=1.35,
        vs_year_ago_pct=+3.0,
        days_surplus_deficit=-14,
    ),
    EquipmentStatus(
        region="North America",
        container_type="40FT_REEFER",
        available_units_k=68.0,
        utilization_pct=91.0,
        shortage_risk="CRITICAL",
        daily_lease_rate_usd=4.10,
        vs_year_ago_pct=+5.5,
        days_surplus_deficit=-22,
    ),
    EquipmentStatus(
        region="North America",
        container_type="20FT_TANK",
        available_units_k=19.0,
        utilization_pct=80.0,
        shortage_risk="MODERATE",
        daily_lease_rate_usd=2.30,
        vs_year_ago_pct=+2.0,
        days_surplus_deficit=-5,
    ),

    # ── Europe ──────────────────────────────────────────────────────────────
    # Near-balanced; slightly import-heavy; HC strong due to industrial goods
    EquipmentStatus(
        region="Europe",
        container_type="20FT_DRY",
        available_units_k=280.0,
        utilization_pct=73.0,
        shortage_risk="LOW",
        daily_lease_rate_usd=0.78,
        vs_year_ago_pct=-1.0,
        days_surplus_deficit=12,
    ),
    EquipmentStatus(
        region="Europe",
        container_type="40FT_DRY",
        available_units_k=440.0,
        utilization_pct=75.0,
        shortage_risk="LOW",
        daily_lease_rate_usd=0.82,
        vs_year_ago_pct=-0.5,
        days_surplus_deficit=10,
    ),
    EquipmentStatus(
        region="Europe",
        container_type="40FT_HC",
        available_units_k=520.0,
        utilization_pct=76.0,
        shortage_risk="LOW",
        daily_lease_rate_usd=0.85,
        vs_year_ago_pct=+0.5,
        days_surplus_deficit=9,
    ),
    EquipmentStatus(
        region="Europe",
        container_type="40FT_REEFER",
        available_units_k=92.0,
        utilization_pct=88.0,
        shortage_risk="HIGH",
        daily_lease_rate_usd=3.50,
        vs_year_ago_pct=+4.0,
        days_surplus_deficit=-15,
    ),
    EquipmentStatus(
        region="Europe",
        container_type="20FT_TANK",
        available_units_k=28.0,
        utilization_pct=77.0,
        shortage_risk="MODERATE",
        daily_lease_rate_usd=2.05,
        vs_year_ago_pct=+1.0,
        days_surplus_deficit=6,
    ),

    # ── South America ───────────────────────────────────────────────────────
    # Export commodity (soy, iron ore, meat) heavy; reefers in demand for produce
    EquipmentStatus(
        region="South America",
        container_type="20FT_DRY",
        available_units_k=115.0,
        utilization_pct=78.0,
        shortage_risk="MODERATE",
        daily_lease_rate_usd=0.95,
        vs_year_ago_pct=+0.5,
        days_surplus_deficit=-4,
    ),
    EquipmentStatus(
        region="South America",
        container_type="40FT_DRY",
        available_units_k=190.0,
        utilization_pct=76.0,
        shortage_risk="MODERATE",
        daily_lease_rate_usd=1.00,
        vs_year_ago_pct=-1.0,
        days_surplus_deficit=5,
    ),
    EquipmentStatus(
        region="South America",
        container_type="40FT_HC",
        available_units_k=85.0,
        utilization_pct=74.0,
        shortage_risk="LOW",
        daily_lease_rate_usd=0.95,
        vs_year_ago_pct=-1.5,
        days_surplus_deficit=8,
    ),
    EquipmentStatus(
        region="South America",
        container_type="40FT_REEFER",
        available_units_k=55.0,
        utilization_pct=90.0,
        shortage_risk="HIGH",
        daily_lease_rate_usd=3.80,
        vs_year_ago_pct=+5.0,
        days_surplus_deficit=-18,
    ),
    EquipmentStatus(
        region="South America",
        container_type="20FT_TANK",
        available_units_k=12.0,
        utilization_pct=75.0,
        shortage_risk="MODERATE",
        daily_lease_rate_usd=2.00,
        vs_year_ago_pct=+0.5,
        days_surplus_deficit=6,
    ),

    # ── Middle East ─────────────────────────────────────────────────────────
    # Transshipment hub (Jebel Ali); equipment generally available due to hub role
    EquipmentStatus(
        region="Middle East",
        container_type="20FT_DRY",
        available_units_k=95.0,
        utilization_pct=70.0,
        shortage_risk="LOW",
        daily_lease_rate_usd=0.80,
        vs_year_ago_pct=-2.0,
        days_surplus_deficit=15,
    ),
    EquipmentStatus(
        region="Middle East",
        container_type="40FT_DRY",
        available_units_k=130.0,
        utilization_pct=72.0,
        shortage_risk="LOW",
        daily_lease_rate_usd=0.88,
        vs_year_ago_pct=-1.5,
        days_surplus_deficit=12,
    ),
    EquipmentStatus(
        region="Middle East",
        container_type="40FT_HC",
        available_units_k=75.0,
        utilization_pct=73.0,
        shortage_risk="LOW",
        daily_lease_rate_usd=0.90,
        vs_year_ago_pct=-1.0,
        days_surplus_deficit=11,
    ),
    EquipmentStatus(
        region="Middle East",
        container_type="40FT_REEFER",
        available_units_k=22.0,
        utilization_pct=84.0,
        shortage_risk="HIGH",
        daily_lease_rate_usd=3.40,
        vs_year_ago_pct=+3.0,
        days_surplus_deficit=-8,
    ),
    EquipmentStatus(
        region="Middle East",
        container_type="20FT_TANK",
        available_units_k=18.0,
        utilization_pct=82.0,
        shortage_risk="MODERATE",
        daily_lease_rate_usd=2.20,
        vs_year_ago_pct=+2.5,
        days_surplus_deficit=-6,
    ),

    # ── Africa ──────────────────────────────────────────────────────────────
    # Thin fleet, reliant on repositioned empties; higher risk due to limited supply
    EquipmentStatus(
        region="Africa",
        container_type="20FT_DRY",
        available_units_k=62.0,
        utilization_pct=80.0,
        shortage_risk="MODERATE",
        daily_lease_rate_usd=1.10,
        vs_year_ago_pct=+1.0,
        days_surplus_deficit=-5,
    ),
    EquipmentStatus(
        region="Africa",
        container_type="40FT_DRY",
        available_units_k=78.0,
        utilization_pct=78.0,
        shortage_risk="MODERATE",
        daily_lease_rate_usd=1.15,
        vs_year_ago_pct=+0.5,
        days_surplus_deficit=-2,
    ),
    EquipmentStatus(
        region="Africa",
        container_type="40FT_HC",
        available_units_k=35.0,
        utilization_pct=75.0,
        shortage_risk="MODERATE",
        daily_lease_rate_usd=1.10,
        vs_year_ago_pct=-0.5,
        days_surplus_deficit=4,
    ),
    EquipmentStatus(
        region="Africa",
        container_type="40FT_REEFER",
        available_units_k=18.0,
        utilization_pct=86.0,
        shortage_risk="HIGH",
        daily_lease_rate_usd=3.60,
        vs_year_ago_pct=+4.0,
        days_surplus_deficit=-10,
    ),
    EquipmentStatus(
        region="Africa",
        container_type="20FT_TANK",
        available_units_k=8.0,
        utilization_pct=72.0,
        shortage_risk="MODERATE",
        daily_lease_rate_usd=2.15,
        vs_year_ago_pct=+0.5,
        days_surplus_deficit=7,
    ),
]

# Fast lookup: (region, container_type) -> EquipmentStatus
_EQUIP_INDEX: Dict[Tuple[str, str], EquipmentStatus] = {
    (e.region, e.container_type): e for e in REGIONAL_EQUIPMENT_STATUS
}


# ── TradeImbalanceMetrics dataclass ───────────────────────────────────────

@dataclass
class TradeImbalanceMetrics:
    """Empty-container repositioning economics for one shipping route."""

    route_id: str
    origin_region: str
    dest_region: str
    empty_container_repositioning_cost_per_feu: float  # USD per FEU
    imbalance_ratio: float   # exports / imports (>1 = export-heavy origin)
    repositioning_days: int  # days to reposition an empty back


# ── TRADE_IMBALANCE_DATA — all 17 routes ──────────────────────────────────
#
# Repositioning economics explained:
#  • On export-heavy routes (Asia→NA, Asia→EU), carriers must ship empties
#    back westbound at significant cost — this is embedded in WB spot rates.
#  • Imbalance ratio = exports from origin / imports into origin (TEU basis).
#  • Repositioning cost is the net incremental cost per FEU incurred by the
#    carrier to move an empty box back; it flows into eastbound freight rates.
#
# Sources: Drewry Container Freight Insight, Container xChange 2025 data.

TRADE_IMBALANCE_DATA: List[TradeImbalanceMetrics] = [
    # ── Trans-Pacific Eastbound (Asia → NA West) ─────────────────────────
    # 1.8 loaded boxes eastbound for every 1 loaded box westbound → large empty WB flow
    TradeImbalanceMetrics(
        route_id="transpacific_eb",
        origin_region="Asia Pacific",
        dest_region="North America",
        empty_container_repositioning_cost_per_feu=400,
        imbalance_ratio=1.80,
        repositioning_days=18,
    ),
    # ── Asia-Europe (Asia → EU) ──────────────────────────────────────────
    TradeImbalanceMetrics(
        route_id="asia_europe",
        origin_region="Asia Pacific",
        dest_region="Europe",
        empty_container_repositioning_cost_per_feu=350,
        imbalance_ratio=1.60,
        repositioning_days=28,
    ),
    # ── Trans-Pacific Westbound (NA → Asia) ──────────────────────────────
    # Empties carried back; effective surcharge lowers headline WB rate
    TradeImbalanceMetrics(
        route_id="transpacific_wb",
        origin_region="North America",
        dest_region="Asia Pacific",
        empty_container_repositioning_cost_per_feu=200,
        imbalance_ratio=0.56,   # 1/1.8 — import heavy from NA perspective
        repositioning_days=16,
    ),
    # ── Transatlantic (EU → NA East) ────────────────────────────────────
    TradeImbalanceMetrics(
        route_id="transatlantic",
        origin_region="Europe",
        dest_region="North America",
        empty_container_repositioning_cost_per_feu=180,
        imbalance_ratio=0.85,
        repositioning_days=14,
    ),
    # ── SE Asia Eastbound (SE Asia → NA West) ───────────────────────────
    TradeImbalanceMetrics(
        route_id="sea_transpacific_eb",
        origin_region="Asia Pacific",
        dest_region="North America",
        empty_container_repositioning_cost_per_feu=380,
        imbalance_ratio=1.70,
        repositioning_days=16,
    ),
    # ── Ningbo-Europe (Asia → EU via Suez) ──────────────────────────────
    TradeImbalanceMetrics(
        route_id="ningbo_europe",
        origin_region="Asia Pacific",
        dest_region="Europe",
        empty_container_repositioning_cost_per_feu=360,
        imbalance_ratio=1.65,
        repositioning_days=30,
    ),
    # ── Middle East → Europe ─────────────────────────────────────────────
    # Oil/gas equipment outbound; modest imbalance vs Asia lanes
    TradeImbalanceMetrics(
        route_id="middle_east_to_europe",
        origin_region="Middle East",
        dest_region="Europe",
        empty_container_repositioning_cost_per_feu=220,
        imbalance_ratio=1.20,
        repositioning_days=24,
    ),
    # ── Middle East → Asia ───────────────────────────────────────────────
    # Imports from Asia dominate; ME sends back empties + modest exports
    TradeImbalanceMetrics(
        route_id="middle_east_to_asia",
        origin_region="Middle East",
        dest_region="Asia Pacific",
        empty_container_repositioning_cost_per_feu=150,
        imbalance_ratio=0.75,
        repositioning_days=12,
    ),
    # ── South Asia → Europe (Colombo) ───────────────────────────────────
    TradeImbalanceMetrics(
        route_id="south_asia_to_europe",
        origin_region="Asia Pacific",
        dest_region="Europe",
        empty_container_repositioning_cost_per_feu=310,
        imbalance_ratio=1.45,
        repositioning_days=22,
    ),
    # ── Intra-Asia: China → SE Asia ──────────────────────────────────────
    # Short-haul; low repositioning cost; near-balanced
    TradeImbalanceMetrics(
        route_id="intra_asia_china_sea",
        origin_region="Asia Pacific",
        dest_region="Asia Pacific",
        empty_container_repositioning_cost_per_feu=60,
        imbalance_ratio=1.15,
        repositioning_days=6,
    ),
    # ── Intra-Asia: China → Japan/Korea ──────────────────────────────────
    TradeImbalanceMetrics(
        route_id="intra_asia_china_japan",
        origin_region="Asia Pacific",
        dest_region="Asia Pacific",
        empty_container_repositioning_cost_per_feu=45,
        imbalance_ratio=1.10,
        repositioning_days=4,
    ),
    # ── China → South America ─────────────────────────────────────────────
    # Long voyage; SA exports (soy/meat) partly offset; modest deficit on SA side
    TradeImbalanceMetrics(
        route_id="china_south_america",
        origin_region="Asia Pacific",
        dest_region="South America",
        empty_container_repositioning_cost_per_feu=480,
        imbalance_ratio=1.35,
        repositioning_days=38,
    ),
    # ── Europe → South America ─────────────────────────────────────────────
    TradeImbalanceMetrics(
        route_id="europe_south_america",
        origin_region="Europe",
        dest_region="South America",
        empty_container_repositioning_cost_per_feu=260,
        imbalance_ratio=1.25,
        repositioning_days=24,
    ),
    # ── Med Hub → Asia (Piraeus → Shanghai) ───────────────────────────────
    # Return leg on Asia-Europe corridor; empties repositioned at low marginal cost
    TradeImbalanceMetrics(
        route_id="med_hub_to_asia",
        origin_region="Europe",
        dest_region="Asia Pacific",
        empty_container_repositioning_cost_per_feu=290,
        imbalance_ratio=0.62,
        repositioning_days=24,
    ),
    # ── North Africa → Europe ─────────────────────────────────────────────
    # Short feeder; nearshore repositioning cheap
    TradeImbalanceMetrics(
        route_id="north_africa_to_europe",
        origin_region="Africa",
        dest_region="Europe",
        empty_container_repositioning_cost_per_feu=95,
        imbalance_ratio=0.90,
        repositioning_days=9,
    ),
    # ── US East → South America ───────────────────────────────────────────
    TradeImbalanceMetrics(
        route_id="us_east_south_america",
        origin_region="North America",
        dest_region="South America",
        empty_container_repositioning_cost_per_feu=175,
        imbalance_ratio=1.05,
        repositioning_days=14,
    ),
    # ── Long Beach → Asia ─────────────────────────────────────────────────
    # Westbound emptied containers; surcharge represents repositioning subsidy
    TradeImbalanceMetrics(
        route_id="longbeach_to_asia",
        origin_region="North America",
        dest_region="Asia Pacific",
        empty_container_repositioning_cost_per_feu=210,
        imbalance_ratio=0.58,
        repositioning_days=18,
    ),
]

# Fast lookup by route_id
_IMBALANCE_INDEX: Dict[str, TradeImbalanceMetrics] = {
    m.route_id: m for m in TRADE_IMBALANCE_DATA
}


# ── Public API ─────────────────────────────────────────────────────────────

def get_equipment_status(region: str, container_type: str) -> EquipmentStatus | None:
    """Return the EquipmentStatus for a specific region and container type.

    Returns None if the combination does not exist in the baseline data.
    """
    return _EQUIP_INDEX.get((region, container_type))


def get_trade_imbalance(route_id: str) -> TradeImbalanceMetrics | None:
    """Return TradeImbalanceMetrics for a given route_id."""
    return _IMBALANCE_INDEX.get(route_id)


def compute_equipment_adjusted_rate(route_id: str, base_rate: float) -> float:
    """Add empty-container repositioning cost to a base freight rate.

    Parameters
    ----------
    route_id:
        One of the 17 route IDs defined in route_registry.
    base_rate:
        Nominal spot freight rate in USD per FEU.

    Returns
    -------
    Equipment-adjusted rate = base_rate + repositioning_cost_per_feu.
    For export-heavy (imbalance_ratio > 1) routes, the repositioning cost
    represents the embedded carrier cost of moving empties back — this is
    why westbound (empty-heavy) headline rates are structurally lower than
    eastbound rates on the same corridor.

    If route_id is not found, base_rate is returned unchanged and a warning
    is logged.
    """
    metrics = _IMBALANCE_INDEX.get(route_id)
    if metrics is None:
        logger.warning(
            "compute_equipment_adjusted_rate: route_id '{}' not found in "
            "TRADE_IMBALANCE_DATA; returning base_rate unchanged.",
            route_id,
        )
        return base_rate

    adjusted = base_rate + metrics.empty_container_repositioning_cost_per_feu
    logger.debug(
        "Route {}: base={:.0f} + reposition={:.0f} = adjusted={:.0f} USD/FEU",
        route_id,
        base_rate,
        metrics.empty_container_repositioning_cost_per_feu,
        adjusted,
    )
    return adjusted


def get_global_equipment_index() -> float:
    """Compute a weighted average utilization across all equipment types and regions.

    Weights reflect relative market importance (fleet size × trade volume proxy).
    Returns a float in [0, 100].

    Interpretation:
      > 85  → tight market; shortage risk elevated across the board
      70–85 → normal operating range
      < 70  → surplus conditions; rate pressure downward

    All equipment types are weighted equally within each region.  Regions are
    weighted by approximate TEU throughput share.
    """
    # Regional TEU-throughput proxy weights (sum ≈ 1.0)
    region_weights: Dict[str, float] = {
        "Asia Pacific":  0.40,
        "North America": 0.22,
        "Europe":        0.20,
        "South America": 0.08,
        "Middle East":   0.06,
        "Africa":        0.04,
    }

    # Container-type fleet-size proxy weights (sum = 1.0)
    type_weights: Dict[str, float] = {
        "20FT_DRY":    0.25,
        "40FT_DRY":    0.35,
        "40FT_HC":     0.25,
        "40FT_REEFER": 0.10,
        "20FT_TANK":   0.05,
    }

    total_weight = 0.0
    weighted_util = 0.0

    for equip in REGIONAL_EQUIPMENT_STATUS:
        rw = region_weights.get(equip.region, 0.0)
        tw = type_weights.get(equip.container_type, 0.0)
        w = rw * tw
        weighted_util += equip.utilization_pct * w
        total_weight += w

    if total_weight == 0.0:
        logger.error("get_global_equipment_index: total_weight is zero — check data.")
        return 0.0

    index = weighted_util / total_weight
    logger.debug("Global equipment utilization index: {:.2f}%", index)
    return round(index, 2)


def get_reefer_summary() -> Dict:
    """Return aggregated reefer-specific statistics across all regions.

    Keys
    ----
    avg_utilization_pct  : float — capacity-weighted average utilization
    regions_critical     : list[str] — regions at CRITICAL reefer shortage
    regions_high         : list[str] — regions at HIGH reefer shortage
    avg_lease_rate_usd   : float — simple average daily lease rate
    total_units_k        : float — total reefer units tracked (thousands)
    """
    reefers = [e for e in REGIONAL_EQUIPMENT_STATUS if e.container_type == "40FT_REEFER"]
    if not reefers:
        return {}

    total_units = sum(r.available_units_k for r in reefers)
    weighted_util = sum(r.utilization_pct * r.available_units_k for r in reefers)
    avg_util = weighted_util / total_units if total_units > 0 else 0.0
    avg_rate = sum(r.daily_lease_rate_usd for r in reefers) / len(reefers)

    return {
        "avg_utilization_pct": round(avg_util, 1),
        "regions_critical": [r.region for r in reefers if r.shortage_risk == "CRITICAL"],
        "regions_high": [r.region for r in reefers if r.shortage_risk == "HIGH"],
        "avg_lease_rate_usd": round(avg_rate, 2),
        "total_units_k": round(total_units, 1),
    }
