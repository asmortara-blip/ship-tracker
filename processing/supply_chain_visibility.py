"""supply_chain_visibility.py — Supply chain path tracking and disruption analysis.

Provides the data layer for the "Track Your Supply Chain" visibility module.
Covers five major product categories with real-world node coordinates, risk
modelling, bottleneck detection, and disruption simulation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Node type and status constants
# ---------------------------------------------------------------------------

NODE_TYPES = ("FACTORY", "PORT", "RAIL", "WAREHOUSE", "DISTRIBUTION")
NODE_STATUSES = ("OPERATIONAL", "DELAYED", "DISRUPTED", "CLOSED")

# Transport mode between consecutive nodes
TRANSPORT_MODES = ("OCEAN", "RAIL", "TRUCK", "PIPELINE", "INLAND")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SupplyChainNode:
    """A single node (facility, port, hub) in a supply chain path."""
    node_id: str
    node_type: str                  # one of NODE_TYPES
    location_name: str
    country: str
    lat: float
    lon: float
    capacity_teu_m: float           # capacity in thousand TEU per month
    current_utilization: float      # 0.0 – 1.0
    status: str                     # one of NODE_STATUSES
    delay_days: int                 # current delay (0 when OPERATIONAL)
    risk_score: float               # 0.0 – 1.0


@dataclass
class SupplyChainPath:
    """An end-to-end supply chain path for a product category."""
    path_id: str
    product_category: str           # human-readable label
    nodes: List[SupplyChainNode]
    transit_modes: List[str]        # len == len(nodes) - 1; mode between node[i] and node[i+1]
    total_transit_days: int
    total_cost_usd: float           # per standard container (40ft)
    risk_score: float               # 0.0 – 1.0; computed by compute_path_risk
    bottleneck_node: str            # node_id of highest-risk node
    resilience_score: float         # 0.0 – 1.0; inverse of risk with diversification bonus


# ---------------------------------------------------------------------------
# Node library — reusable nodes shared across paths
# ---------------------------------------------------------------------------

def _node(
    node_id: str,
    node_type: str,
    location_name: str,
    country: str,
    lat: float,
    lon: float,
    capacity_teu_m: float,
    utilization: float,
    status: str,
    delay_days: int,
    risk_score: float,
) -> SupplyChainNode:
    return SupplyChainNode(
        node_id=node_id,
        node_type=node_type,
        location_name=location_name,
        country=country,
        lat=lat,
        lon=lon,
        capacity_teu_m=capacity_teu_m,
        current_utilization=utilization,
        status=status,
        delay_days=delay_days,
        risk_score=risk_score,
    )


# Shared port nodes (referenced from multiple paths)
PORT_CNSHA = _node(
    "PORT_CNSHA", "PORT", "Port of Shanghai (CNSHA)", "China",
    31.23, 121.47, 4500.0, 0.87, "OPERATIONAL", 0, 0.22,
)
PORT_USLAX = _node(
    "PORT_USLAX", "PORT", "Port of Los Angeles (USLAX)", "USA",
    33.73, -118.26, 900.0, 0.79, "DELAYED", 3, 0.38,
)
PORT_USLGB = _node(
    "PORT_USLGB", "PORT", "Port of Long Beach (USLGB)", "USA",
    33.75, -118.22, 850.0, 0.76, "OPERATIONAL", 0, 0.34,
)
PORT_NLRTM = _node(
    "PORT_NLRTM", "PORT", "Port of Rotterdam (NLRTM)", "Netherlands",
    51.92, 4.47, 1200.0, 0.82, "OPERATIONAL", 0, 0.18,
)
PORT_SGSIN = _node(
    "PORT_SGSIN", "PORT", "Port of Singapore (SGSIN)", "Singapore",
    1.26, 103.82, 3300.0, 0.90, "OPERATIONAL", 0, 0.15,
)
PORT_USSAV = _node(
    "PORT_USSAV", "PORT", "Port of Savannah (USSAV)", "USA",
    32.08, -81.09, 450.0, 0.83, "OPERATIONAL", 0, 0.28,
)
PORT_LKCMB = _node(
    "PORT_LKCMB", "PORT", "Port of Colombo (LKCMB)", "Sri Lanka",
    6.95, 79.85, 700.0, 0.74, "OPERATIONAL", 0, 0.31,
)
PORT_JPYOK = _node(
    "PORT_JPYOK", "PORT", "Port of Yokohama (JPYOK)", "Japan",
    35.43, 139.65, 380.0, 0.68, "OPERATIONAL", 0, 0.20,
)


# ---------------------------------------------------------------------------
# Path 1 — Electronics (iPhone-like): Foxconn → Memphis Retail
# ---------------------------------------------------------------------------

def _build_electronics_path() -> SupplyChainPath:
    factory = _node(
        "FACTORY_FOXCONN_TIANJIN", "FACTORY",
        "Foxconn Tianjin Assembly (CNTXG)", "China",
        39.34, 117.36, 220.0, 0.92, "OPERATIONAL", 0, 0.30,
    )
    rail_inland_cn = _node(
        "RAIL_TIANJIN_SHANGHAI", "RAIL",
        "Tianjin–Shanghai Inland Rail", "China",
        33.50, 119.50, 180.0, 0.70, "OPERATIONAL", 0, 0.18,
    )
    port_cnsha = _node(
        "PORT_CNSHA", "PORT", "Port of Shanghai (CNSHA)", "China",
        31.23, 121.47, 4500.0, 0.87, "OPERATIONAL", 0, 0.22,
    )
    port_uslax = _node(
        "PORT_USLAX_ELEC", "PORT", "Port of Los Angeles (USLAX)", "USA",
        33.73, -118.26, 900.0, 0.79, "DELAYED", 3, 0.38,
    )
    rail_us = _node(
        "RAIL_LAX_MEMPHIS", "RAIL",
        "BNSF Rail — LA to Memphis", "USA",
        35.50, -100.00, 500.0, 0.65, "OPERATIONAL", 0, 0.20,
    )
    dist_memphis = _node(
        "DIST_MEMPHIS", "DISTRIBUTION",
        "FedEx/Apple Distribution Center, Memphis TN", "USA",
        35.15, -90.05, 120.0, 0.88, "OPERATIONAL", 0, 0.22,
    )
    retail = _node(
        "DIST_RETAIL_US", "DISTRIBUTION",
        "US Retail Network", "USA",
        37.09, -95.71, 80.0, 0.75, "OPERATIONAL", 0, 0.12,
    )

    nodes = [factory, rail_inland_cn, port_cnsha, port_uslax, rail_us, dist_memphis, retail]
    modes = ["RAIL", "RAIL", "OCEAN", "RAIL", "TRUCK", "TRUCK"]
    path = SupplyChainPath(
        path_id="electronics_iphone",
        product_category="Electronics (iPhone-style)",
        nodes=nodes,
        transit_modes=modes,
        total_transit_days=28,
        total_cost_usd=4_200.0,
        risk_score=0.0,
        bottleneck_node="PORT_USLAX_ELEC",
        resilience_score=0.0,
    )
    path.risk_score = compute_path_risk(path)
    path.resilience_score = _compute_resilience(path, alt_routes=2)
    return path


# ---------------------------------------------------------------------------
# Path 2 — Apparel (Fast Fashion): Dhaka → Rotterdam → Retail
# ---------------------------------------------------------------------------

def _build_apparel_path() -> SupplyChainPath:
    factory = _node(
        "FACTORY_DHAKA", "FACTORY",
        "Garment Factory, Dhaka EPZ", "Bangladesh",
        23.81, 90.41, 90.0, 0.95, "OPERATIONAL", 0, 0.40,
    )
    truck_dhaka_cmb = _node(
        "TRUCK_DHAKA_COLOMBO", "WAREHOUSE",
        "Chittagong Port Feeder Hub", "Bangladesh",
        22.33, 91.80, 60.0, 0.80, "OPERATIONAL", 0, 0.33,
    )
    port_lkcmb = _node(
        "PORT_LKCMB_APP", "PORT",
        "Port of Colombo (LKCMB) — Transshipment", "Sri Lanka",
        6.95, 79.85, 700.0, 0.74, "OPERATIONAL", 0, 0.31,
    )
    port_nlrtm = _node(
        "PORT_NLRTM_APP", "PORT",
        "Port of Rotterdam (NLRTM)", "Netherlands",
        51.92, 4.47, 1200.0, 0.82, "OPERATIONAL", 0, 0.18,
    )
    truck_eu = _node(
        "TRUCK_RTM_DIST", "WAREHOUSE",
        "European Road Distribution Hub, Venlo NL", "Netherlands",
        51.37, 6.17, 100.0, 0.78, "DELAYED", 2, 0.27,
    )
    dist_eu = _node(
        "DIST_EU_APPAREL", "DISTRIBUTION",
        "H&M / Zara EU Distribution Centre", "Germany",
        51.22, 6.78, 85.0, 0.82, "OPERATIONAL", 0, 0.20,
    )
    retail_eu = _node(
        "RETAIL_EU", "DISTRIBUTION",
        "European Retail Network", "Europe",
        50.00, 10.00, 60.0, 0.70, "OPERATIONAL", 0, 0.12,
    )

    nodes = [factory, truck_dhaka_cmb, port_lkcmb, port_nlrtm, truck_eu, dist_eu, retail_eu]
    modes = ["TRUCK", "OCEAN", "OCEAN", "TRUCK", "TRUCK", "TRUCK"]
    path = SupplyChainPath(
        path_id="apparel_fast_fashion",
        product_category="Apparel (Fast Fashion)",
        nodes=nodes,
        transit_modes=modes,
        total_transit_days=35,
        total_cost_usd=3_100.0,
        risk_score=0.0,
        bottleneck_node="FACTORY_DHAKA",
        resilience_score=0.0,
    )
    path.risk_score = compute_path_risk(path)
    path.resilience_score = _compute_resilience(path, alt_routes=1)
    return path


# ---------------------------------------------------------------------------
# Path 3 — Automotive Parts: Toyota Yokohama → Kentucky Assembly
# ---------------------------------------------------------------------------

def _build_automotive_path() -> SupplyChainPath:
    factory = _node(
        "FACTORY_TOYOTA_YOKOHAMA", "FACTORY",
        "Toyota Motomachi Plant, Yokohama", "Japan",
        35.47, 139.67, 150.0, 0.85, "OPERATIONAL", 0, 0.22,
    )
    port_jpyok = _node(
        "PORT_JPYOK_AUTO", "PORT",
        "Port of Yokohama (JPYOK)", "Japan",
        35.43, 139.65, 380.0, 0.68, "OPERATIONAL", 0, 0.20,
    )
    port_uslgb = _node(
        "PORT_USLGB_AUTO", "PORT",
        "Port of Long Beach (USLGB)", "USA",
        33.75, -118.22, 850.0, 0.76, "OPERATIONAL", 0, 0.34,
    )
    rail_us_auto = _node(
        "RAIL_LGB_KENTUCKY", "RAIL",
        "UP/BNSF Rail — Long Beach to Kentucky", "USA",
        36.00, -98.00, 420.0, 0.62, "OPERATIONAL", 0, 0.22,
    )
    assembly_ky = _node(
        "ASSEMBLY_KENTUCKY", "WAREHOUSE",
        "Toyota Georgetown Assembly Plant, KY", "USA",
        38.21, -84.56, 110.0, 0.91, "OPERATIONAL", 0, 0.18,
    )
    dealer = _node(
        "DEALER_US", "DISTRIBUTION",
        "US Toyota Dealership Network", "USA",
        37.09, -95.71, 70.0, 0.65, "OPERATIONAL", 0, 0.10,
    )

    nodes = [factory, port_jpyok, port_uslgb, rail_us_auto, assembly_ky, dealer]
    modes = ["TRUCK", "OCEAN", "RAIL", "TRUCK", "TRUCK"]
    path = SupplyChainPath(
        path_id="automotive_parts_toyota",
        product_category="Automotive Parts (Toyota)",
        nodes=nodes,
        transit_modes=modes,
        total_transit_days=22,
        total_cost_usd=5_800.0,
        risk_score=0.0,
        bottleneck_node="PORT_USLGB_AUTO",
        resilience_score=0.0,
    )
    path.risk_score = compute_path_risk(path)
    path.resilience_score = _compute_resilience(path, alt_routes=2)
    return path


# ---------------------------------------------------------------------------
# Path 4 — Agriculture (Soybeans): Iowa Farm → Shanghai Processing
# ---------------------------------------------------------------------------

def _build_agriculture_path() -> SupplyChainPath:
    farm = _node(
        "FARM_IOWA", "FACTORY",
        "Soybean Farms, Iowa", "USA",
        42.00, -93.50, 600.0, 0.70, "OPERATIONAL", 0, 0.25,
    )
    rail_iowa_sav = _node(
        "RAIL_IOWA_SAVANNAH", "RAIL",
        "Rail Corridor — Iowa to Savannah GA", "USA",
        38.50, -88.00, 350.0, 0.68, "OPERATIONAL", 0, 0.20,
    )
    port_ussav = _node(
        "PORT_USSAV_AG", "PORT",
        "Port of Savannah (USSAV)", "USA",
        32.08, -81.09, 450.0, 0.83, "OPERATIONAL", 0, 0.28,
    )
    port_cnsha_ag = _node(
        "PORT_CNSHA_AG", "PORT",
        "Port of Shanghai (CNSHA) — Import", "China",
        31.23, 121.47, 4500.0, 0.87, "OPERATIONAL", 0, 0.22,
    )
    processing_cn = _node(
        "PROCESSING_JIANGSU", "WAREHOUSE",
        "ADM / COFCO Soy Processing, Jiangsu", "China",
        32.07, 118.80, 200.0, 0.88, "OPERATIONAL", 0, 0.24,
    )
    dist_cn = _node(
        "DIST_CHINA_AG", "DISTRIBUTION",
        "China Agricultural Distribution Network", "China",
        35.00, 113.00, 150.0, 0.72, "OPERATIONAL", 0, 0.16,
    )

    nodes = [farm, rail_iowa_sav, port_ussav, port_cnsha_ag, processing_cn, dist_cn]
    modes = ["RAIL", "RAIL", "OCEAN", "TRUCK", "TRUCK"]
    path = SupplyChainPath(
        path_id="agriculture_soybeans",
        product_category="Agriculture (Soybeans)",
        nodes=nodes,
        transit_modes=modes,
        total_transit_days=30,
        total_cost_usd=2_400.0,
        risk_score=0.0,
        bottleneck_node="PORT_USSAV_AG",
        resilience_score=0.0,
    )
    path.risk_score = compute_path_risk(path)
    path.resilience_score = _compute_resilience(path, alt_routes=2)
    return path


# ---------------------------------------------------------------------------
# Path 5 — Chemicals: Rotterdam Plant → Singapore Distribution
# ---------------------------------------------------------------------------

def _build_chemicals_path() -> SupplyChainPath:
    plant = _node(
        "PLANT_ROTTERDAM_CHEM", "FACTORY",
        "BASF / Shell Chemical Plant, Rotterdam", "Netherlands",
        51.95, 4.14, 300.0, 0.78, "OPERATIONAL", 0, 0.28,
    )
    pipeline_truck = _node(
        "PIPELINE_RTM_PORT", "WAREHOUSE",
        "Rotterdam Europoort Pipeline/Truck Terminal", "Netherlands",
        51.93, 4.10, 180.0, 0.74, "OPERATIONAL", 0, 0.22,
    )
    port_nlrtm_chem = _node(
        "PORT_NLRTM_CHEM", "PORT",
        "Port of Rotterdam (NLRTM) — Chemical Berth", "Netherlands",
        51.92, 4.47, 1200.0, 0.82, "OPERATIONAL", 0, 0.18,
    )
    port_sgsin_chem = _node(
        "PORT_SGSIN_CHEM", "PORT",
        "Port of Singapore (SGSIN) — Chemical Terminal", "Singapore",
        1.26, 103.82, 3300.0, 0.90, "OPERATIONAL", 0, 0.15,
    )
    dist_asia_chem = _node(
        "DIST_ASIA_CHEM", "DISTRIBUTION",
        "Jurong Island Chemical Distribution Hub, SG", "Singapore",
        1.27, 103.70, 140.0, 0.85, "DELAYED", 1, 0.20,
    )
    retail_asia_chem = _node(
        "RETAIL_ASIA_CHEM", "DISTRIBUTION",
        "Asia-Pacific Industrial Customers", "Asia",
        10.00, 110.00, 80.0, 0.68, "OPERATIONAL", 0, 0.12,
    )

    nodes = [plant, pipeline_truck, port_nlrtm_chem, port_sgsin_chem, dist_asia_chem, retail_asia_chem]
    modes = ["PIPELINE", "TRUCK", "OCEAN", "TRUCK", "TRUCK"]
    path = SupplyChainPath(
        path_id="chemicals_rotterdam",
        product_category="Chemicals (Rotterdam–Asia)",
        nodes=nodes,
        transit_modes=modes,
        total_transit_days=25,
        total_cost_usd=6_500.0,
        risk_score=0.0,
        bottleneck_node="PORT_NLRTM_CHEM",
        resilience_score=0.0,
    )
    path.risk_score = compute_path_risk(path)
    path.resilience_score = _compute_resilience(path, alt_routes=3)
    return path


# ---------------------------------------------------------------------------
# Risk computation helpers
# ---------------------------------------------------------------------------

# Weight of node risk vs. transit-segment risk in the aggregate path score
_NODE_WEIGHT   = 0.65
_TRANSIT_WEIGHT = 0.35

# Transit segment base risk by mode
_MODE_RISK: dict[str, float] = {
    "OCEAN":    0.25,
    "RAIL":     0.12,
    "TRUCK":    0.18,
    "PIPELINE": 0.08,
    "INLAND":   0.14,
}

# Additional risk contribution from node status
_STATUS_RISK_ADD: dict[str, float] = {
    "OPERATIONAL": 0.00,
    "DELAYED":     0.15,
    "DISRUPTED":   0.35,
    "CLOSED":      0.60,
}


def compute_path_risk(path: SupplyChainPath) -> float:
    """Compute aggregate risk score [0, 1] for a supply chain path.

    Weights node risk scores (utilisation-adjusted) and transit-segment
    inherent mode risks.  Status penalties are additive.
    """
    if not path.nodes:
        return 0.0

    # Node component — utilisation above 85% inflates risk
    node_risks: list[float] = []
    for node in path.nodes:
        base = node.risk_score
        utilisation_penalty = max(0.0, (node.current_utilization - 0.85) * 0.5)
        status_add = _STATUS_RISK_ADD.get(node.status, 0.0)
        effective = min(1.0, base + utilisation_penalty + status_add)
        node_risks.append(effective)

    avg_node_risk = sum(node_risks) / len(node_risks)

    # Transit component
    segment_risks: list[float] = []
    for mode in path.transit_modes:
        segment_risks.append(_MODE_RISK.get(mode, 0.20))

    avg_transit_risk = (
        sum(segment_risks) / len(segment_risks) if segment_risks else 0.0
    )

    composite = (
        _NODE_WEIGHT * avg_node_risk
        + _TRANSIT_WEIGHT * avg_transit_risk
    )
    result = round(min(1.0, composite), 4)
    logger.debug(
        "Path {} risk={} (node={}, transit={})",
        path.path_id,
        result,
        round(avg_node_risk, 3),
        round(avg_transit_risk, 3),
    )
    return result


def _compute_resilience(path: SupplyChainPath, alt_routes: int = 0) -> float:
    """Compute resilience score [0, 1].  Higher = more resilient."""
    base = 1.0 - path.risk_score
    diversity_bonus = min(0.20, alt_routes * 0.07)
    # Penalise single points of failure (CLOSED or DISRUPTED nodes)
    spof_penalty = sum(
        0.08
        for n in path.nodes
        if n.status in ("DISRUPTED", "CLOSED")
    )
    return round(max(0.0, min(1.0, base + diversity_bonus - spof_penalty)), 4)


# ---------------------------------------------------------------------------
# Example supply chain paths registry
# ---------------------------------------------------------------------------

def build_example_paths() -> list[SupplyChainPath]:
    """Build and return the five canonical example supply chain paths."""
    paths = [
        _build_electronics_path(),
        _build_apparel_path(),
        _build_automotive_path(),
        _build_agriculture_path(),
        _build_chemicals_path(),
    ]
    logger.info("Built {} supply chain example paths", len(paths))
    return paths


# Module-level cache (built once on first import)
EXAMPLE_PATHS: list[SupplyChainPath] = build_example_paths()

PATHS_BY_ID: dict[str, SupplyChainPath] = {p.path_id: p for p in EXAMPLE_PATHS}


# ---------------------------------------------------------------------------
# Public API: identify_bottlenecks
# ---------------------------------------------------------------------------

def identify_bottlenecks(paths: list[SupplyChainPath]) -> list[str]:
    """Return node_ids that appear in more than one path (single points of failure).

    Nodes are matched by their base location name to catch paths that create
    separate node objects for the same real-world facility.
    """
    from collections import Counter

    location_to_paths: dict[str, list[str]] = {}
    node_id_counter: Counter = Counter()

    for path in paths:
        seen_in_path: set[str] = set()
        for node in path.nodes:
            key = node.location_name
            if key not in location_to_paths:
                location_to_paths[key] = []
            if path.path_id not in location_to_paths[key]:
                location_to_paths[key].append(path.path_id)
            if key not in seen_in_path:
                node_id_counter[node.node_id] += 1
                seen_in_path.add(key)

    # Also check raw node_id duplicates
    bottlenecks: list[str] = []
    for node_id, count in node_id_counter.items():
        if count > 1:
            bottlenecks.append(node_id)

    # Add location-based duplicates (different node_ids, same real place)
    for location, path_ids in location_to_paths.items():
        if len(path_ids) > 1:
            # Find a representative node_id for this location
            for path in paths:
                for node in path.nodes:
                    if node.location_name == location and node.node_id not in bottlenecks:
                        bottlenecks.append(node.node_id)
                        break

    unique_bottlenecks = list(dict.fromkeys(bottlenecks))
    logger.info("Identified {} bottleneck nodes across {} paths", len(unique_bottlenecks), len(paths))
    return unique_bottlenecks


def get_bottleneck_details(paths: list[SupplyChainPath]) -> list[dict]:
    """Return enriched bottleneck data: node info + count of affected paths + risk.

    Returns a list of dicts sorted by path_count descending.
    """
    from collections import defaultdict

    location_paths: dict[str, list[str]] = defaultdict(list)
    location_node: dict[str, SupplyChainNode] = {}

    for path in paths:
        for node in path.nodes:
            key = node.location_name
            if path.path_id not in location_paths[key]:
                location_paths[key].append(path.path_id)
            location_node[key] = node  # last write wins (nodes are canonical)

    details: list[dict] = []
    for location, pids in location_paths.items():
        if len(pids) > 1:
            node = location_node[location]
            details.append(
                {
                    "node_id": node.node_id,
                    "location_name": location,
                    "node_type": node.node_type,
                    "path_count": len(pids),
                    "affected_paths": pids,
                    "risk_score": node.risk_score,
                    "status": node.status,
                    "pct_affected": round(len(pids) / len(paths) * 100, 1),
                }
            )

    details.sort(key=lambda d: d["path_count"], reverse=True)
    return details


# ---------------------------------------------------------------------------
# Public API: simulate_disruption
# ---------------------------------------------------------------------------

# Alternative routing cost/day offsets per disrupted node type
_ALT_ROUTE_DAYS: dict[str, int] = {
    "FACTORY":      14,
    "PORT":         7,
    "RAIL":         5,
    "WAREHOUSE":    3,
    "DISTRIBUTION": 2,
}

_ALT_ROUTE_COST: dict[str, float] = {
    "FACTORY":      18_000.0,
    "PORT":         6_500.0,
    "RAIL":         2_800.0,
    "WAREHOUSE":    1_200.0,
    "DISTRIBUTION":  800.0,
}

# Suggested alternative port pairs when a port is disrupted
_PORT_ALTERNATIVES: dict[str, list[str]] = {
    "PORT_USLAX":       ["Port of Long Beach (USLGB)", "Port of Seattle (USSEA)"],
    "PORT_USLAX_ELEC":  ["Port of Long Beach (USLGB)", "Port of Seattle (USSEA)"],
    "PORT_USLGB":       ["Port of Los Angeles (USLAX)", "Port of Seattle (USSEA)"],
    "PORT_USLGB_AUTO":  ["Port of Los Angeles (USLAX)", "Port of Oakland (USOAK)"],
    "PORT_CNSHA":       ["Port of Ningbo (CNNBO)", "Port of Tianjin (CNTXG)"],
    "PORT_CNSHA_AG":    ["Port of Tianjin (CNTXG)", "Port of Qingdao (CNTAO)"],
    "PORT_NLRTM":       ["Port of Antwerp (BEANR)", "Port of Hamburg (DEHAM)"],
    "PORT_NLRTM_APP":   ["Port of Antwerp (BEANR)", "Port of Hamburg (DEHAM)"],
    "PORT_NLRTM_CHEM":  ["Port of Antwerp (BEANR)", "Port of Hamburg (DEHAM)"],
    "PORT_SGSIN":       ["Port of Tanjung Pelepas (MYTPP)", "Port of Klang (MYPKG)"],
    "PORT_SGSIN_CHEM":  ["Port of Tanjung Pelepas (MYTPP)", "Port of Klang (MYPKG)"],
    "PORT_LKCMB_APP":   ["Port of Singapore (SGSIN)", "Port of Port Klang (MYPKG)"],
    "PORT_USSAV_AG":    ["Port of New Orleans (USNOL)", "Port of Houston (USHOU)"],
    "PORT_JPYOK_AUTO":  ["Port of Nagoya (JPNGO)", "Port of Osaka (JPOSK)"],
}


def simulate_disruption(path_id: str, disrupted_node_id: str, duration_days: int = 7) -> dict:
    """Simulate what happens when a specific node in a path fails.

    Parameters
    ----------
    path_id:
        The supply chain path to analyse.
    disrupted_node_id:
        The node_id of the disrupted node within the path.
    duration_days:
        Duration of disruption in days (1-30).

    Returns
    -------
    dict with keys:
        path_id, disrupted_node, duration_days, affected, alternative_routes,
        additional_transit_days, additional_cost_usd, cascading_nodes,
        severity, recommendation.
    """
    path = PATHS_BY_ID.get(path_id)
    if path is None:
        logger.warning("simulate_disruption: unknown path_id {}", path_id)
        return {"error": "Unknown path_id: " + path_id}

    disrupted_node: Optional[SupplyChainNode] = None
    for n in path.nodes:
        if n.node_id == disrupted_node_id:
            disrupted_node = n
            break

    if disrupted_node is None:
        logger.warning(
            "simulate_disruption: node {} not found in path {}", disrupted_node_id, path_id
        )
        return {"error": "Node " + disrupted_node_id + " not in path " + path_id}

    node_type = disrupted_node.node_type
    extra_days_base = _ALT_ROUTE_DAYS.get(node_type, 5)
    extra_cost_base = _ALT_ROUTE_COST.get(node_type, 3_000.0)

    # Scale with duration
    scale = 1.0 + max(0.0, (duration_days - 7) * 0.05)
    additional_transit_days = max(1, int(extra_days_base * scale))
    additional_cost_usd = round(extra_cost_base * scale, 0)

    # Alternative routes
    alt_routes = _PORT_ALTERNATIVES.get(disrupted_node_id, [])
    if not alt_routes:
        if node_type == "RAIL":
            alt_routes = ["Road freight (truck) diversion", "Air freight for priority cargo"]
        elif node_type == "FACTORY":
            alt_routes = ["Alternative supplier sourcing", "Inventory draw-down from buffer stock"]
        else:
            alt_routes = ["Alternative transport mode", "Reroute via nearest hub"]

    # Cascading: downstream nodes after the disrupted one face delays
    disrupted_idx = next(
        (i for i, n in enumerate(path.nodes) if n.node_id == disrupted_node_id), -1
    )
    cascading_nodes: list[str] = []
    if disrupted_idx >= 0:
        cascading_nodes = [
            n.location_name for n in path.nodes[disrupted_idx + 1:]
        ]

    # Severity classification
    if disrupted_node.risk_score > 0.35 or duration_days > 14:
        severity = "CRITICAL"
    elif disrupted_node.risk_score > 0.20 or duration_days > 7:
        severity = "HIGH"
    else:
        severity = "MODERATE"

    # Recommendation
    if severity == "CRITICAL":
        recommendation = (
            "Immediate escalation required. Activate contingency supplier/port agreements. "
            "Consider air freight for high-value/time-sensitive cargo."
        )
    elif severity == "HIGH":
        recommendation = (
            "Expedite rerouting via alternative hub. Notify downstream assembly plants "
            "to adjust production schedules. Draw on buffer stock."
        )
    else:
        recommendation = (
            "Monitor situation. Shift non-urgent shipments to alternative route. "
            "Buffer stock should cover short-term demand."
        )

    result = {
        "path_id": path_id,
        "product_category": path.product_category,
        "disrupted_node": disrupted_node.location_name,
        "disrupted_node_id": disrupted_node_id,
        "disrupted_node_type": node_type,
        "duration_days": duration_days,
        "affected": True,
        "alternative_routes": alt_routes,
        "additional_transit_days": additional_transit_days,
        "additional_cost_usd": float(additional_cost_usd),
        "cascading_nodes": cascading_nodes,
        "severity": severity,
        "recommendation": recommendation,
        "new_total_transit_days": path.total_transit_days + additional_transit_days,
        "new_total_cost_usd": path.total_cost_usd + additional_cost_usd,
    }

    logger.info(
        "Disruption sim: path={} node={} duration={}d -> +{}d +${} severity={}",
        path_id,
        disrupted_node_id,
        duration_days,
        additional_transit_days,
        additional_cost_usd,
        severity,
    )
    return result


# ---------------------------------------------------------------------------
# Convenience: buffer stock recommendation
# ---------------------------------------------------------------------------

def recommended_buffer_days(path: SupplyChainPath) -> int:
    """Return recommended buffer stock days based on transit time and risk."""
    base = path.total_transit_days // 7  # ~1 week per week of transit
    risk_add = int(path.risk_score * 15)  # up to 15 extra days for high risk
    return max(7, base + risk_add)
