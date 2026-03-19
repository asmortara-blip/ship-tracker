"""Carbon emissions and ESG sustainability calculations for shipping routes.

Uses IMO-standard HFO emission factors and Poseidon Principles alignment thresholds.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from routes.route_registry import ROUTES


# ── Nautical mile distances per route ────────────────────────────────────────
ROUTE_DISTANCES: dict[str, float] = {
    "transpacific_eb":       5500.0,   # Shanghai → LA
    "transpacific_wb":       5500.0,   # LA → Shanghai
    "asia_europe":          10800.0,   # Shanghai → Rotterdam via Suez
    "transatlantic":         3600.0,   # Rotterdam → NYC
    "sea_transpacific_eb":   8000.0,   # Singapore → LA
    "ningbo_europe":        11000.0,   # Ningbo → Antwerp via Suez
    "middle_east_to_europe": 8500.0,   # Dubai → Rotterdam
    "middle_east_to_asia":   3800.0,   # Dubai → Shanghai
    "south_asia_to_europe":  9500.0,   # Colombo → Felixstowe
    "intra_asia_china_sea":  1800.0,   # Shanghai → Singapore
    "intra_asia_china_japan":  600.0,  # Shanghai → Yokohama
    "china_south_america":   9800.0,   # Shanghai → Santos
    "europe_south_america":  5200.0,   # Rotterdam → Santos
    "med_hub_to_asia":      13500.0,   # Piraeus → Shanghai via Cape
    "north_africa_to_europe": 1200.0,  # Tanger → Rotterdam
    "us_east_south_america": 4800.0,   # Savannah → Santos
    "longbeach_to_asia":     5400.0,   # Long Beach → Shanghai
}

# ── Physical / regulatory constants ──────────────────────────────────────────
_FUEL_CONSUMPTION_MT_PER_DAY: float = 85.0   # large container vessel at sea speed
_CO2_FACTOR_KG_PER_KG_FUEL: float = 3.114    # IMO HFO standard (kg CO2 / kg fuel)
_TEU_CAPACITY: int = 8_000                    # assumed large vessel
_LOAD_FACTOR: float = 0.85                    # typical utilization
_EEDI_BENCHMARK: float = 0.05                 # MT CO2/TEU — excellent benchmark
_EU_ETS_PRICE_USD: float = 80.0              # $/tonne CO2
_VESSEL_TYPE: str = "large_container"


@dataclass
class RouteEmissions:
    route_id: str
    route_name: str
    transit_days: int
    distance_nm: float               # nautical miles (estimated from lat/lon)
    vessel_type: str                 # "large_container" for all routes
    teu_capacity: int                # assumed 8000 TEU for large vessel
    fuel_consumption_mt_per_day: float   # metric tons per day
    total_fuel_mt: float
    co2_emissions_mt: float          # total voyage CO2 in metric tons
    co2_per_teu_mt: float            # CO2 per TEU (key ESG metric)
    co2_vs_air_freight_pct: float    # how much less CO2 than air freight (sea ~50x less)
    eedi_score: float                # Energy Efficiency Design Index proxy [0-100, higher=cleaner]
    poseidon_compliant: bool         # Poseidon Principles 2050 alignment
    carbon_cost_usd: float           # at $80/tonne EU ETS equivalent
    sustainability_grade: str        # "A"/"B"/"C"/"D" based on efficiency


def calculate_route_emissions(
    route_id: str,
    route_name: str,
    transit_days: int,
) -> RouteEmissions:
    """Calculate carbon emissions and ESG metrics for a single shipping route.

    Parameters
    ----------
    route_id:
        Identifier matching ROUTE_DISTANCES keys.
    route_name:
        Human-readable route label.
    transit_days:
        Typical transit duration in days.

    Returns
    -------
    RouteEmissions
        Fully populated dataclass with all derived metrics.
    """
    distance_nm = ROUTE_DISTANCES.get(route_id, 0.0)

    # Core energy calculations
    total_fuel_mt = _FUEL_CONSUMPTION_MT_PER_DAY * transit_days

    # CO2: 1 MT fuel × 3.114 kg CO2/kg fuel = 3.114 MT CO2/MT fuel
    # (kg/kg is unit-less ratio, so MT fuel × 3.114 = MT CO2)
    total_co2_mt = total_fuel_mt * _CO2_FACTOR_KG_PER_KG_FUEL

    # Per-TEU intensity — normalised by actual loaded TEU count
    loaded_teu = _TEU_CAPACITY * _LOAD_FACTOR
    co2_per_teu_mt = total_co2_mt / loaded_teu

    # Sea freight vs air freight: sea shipping emits ~98% less CO2 per tonne-km
    co2_vs_air_freight_pct = 0.98

    # EEDI proxy: score relative to the excellent benchmark of 0.05 MT CO2/TEU
    # A route at the benchmark scores 100; routes above it score progressively lower.
    eedi_score = max(0.0, 100.0 - (co2_per_teu_mt / _EEDI_BENCHMARK) * 100.0)

    # Poseidon Principles 2050 trajectory alignment
    poseidon_compliant = co2_per_teu_mt < 0.12

    # Carbon cost at EU ETS-equivalent price
    carbon_cost_usd = total_co2_mt * _EU_ETS_PRICE_USD

    # Sustainability grade
    if eedi_score > 80:
        grade = "A"
    elif eedi_score > 60:
        grade = "B"
    elif eedi_score > 40:
        grade = "C"
    else:
        grade = "D"

    return RouteEmissions(
        route_id=route_id,
        route_name=route_name,
        transit_days=transit_days,
        distance_nm=distance_nm,
        vessel_type=_VESSEL_TYPE,
        teu_capacity=_TEU_CAPACITY,
        fuel_consumption_mt_per_day=_FUEL_CONSUMPTION_MT_PER_DAY,
        total_fuel_mt=total_fuel_mt,
        co2_emissions_mt=total_co2_mt,
        co2_per_teu_mt=co2_per_teu_mt,
        co2_vs_air_freight_pct=co2_vs_air_freight_pct,
        eedi_score=eedi_score,
        poseidon_compliant=poseidon_compliant,
        carbon_cost_usd=carbon_cost_usd,
        sustainability_grade=grade,
    )


def calculate_all_routes() -> list[RouteEmissions]:
    """Calculate emissions for all 17 registered routes.

    Returns
    -------
    list[RouteEmissions]
        Sorted ascending by co2_per_teu_mt (cleanest first).
    """
    results: list[RouteEmissions] = []
    for route in ROUTES:
        emissions = calculate_route_emissions(
            route_id=route.id,
            route_name=route.name,
            transit_days=route.transit_days,
        )
        results.append(emissions)

    results.sort(key=lambda r: r.co2_per_teu_mt)
    return results


def compare_to_alternatives(route_emissions: RouteEmissions) -> dict:
    """Compare a route's emissions against alternative transport modes.

    Parameters
    ----------
    route_emissions:
        The RouteEmissions instance to benchmark.

    Returns
    -------
    dict with keys:
        vs_air_freight_co2_ratio  — how many times more CO2 air freight emits (50x)
        vs_road_estimate          — approximate road freight CO2 ratio (3x)
        trees_to_offset           — number of trees needed to offset the voyage CO2
        carbon_offset_cost_usd    — estimated voluntary carbon offset cost at $15/tonne
    """
    vs_air_freight_co2_ratio: float = 50.0   # sea ~50x less CO2 per tonne-km than air
    vs_road_estimate: float = 3.0            # road is ~3x more carbon-intensive per tonne-km

    # One mature tree absorbs ~21 kg CO2/year; offset over 20 years ~ 420 kg = 0.42 MT
    co2_per_tree_mt: float = 0.42
    trees_to_offset: int = math.ceil(route_emissions.co2_emissions_mt / co2_per_tree_mt)

    # Voluntary carbon market ~$15/tonne
    carbon_offset_cost_usd: float = route_emissions.co2_emissions_mt * 15.0

    return {
        "vs_air_freight_co2_ratio": vs_air_freight_co2_ratio,
        "vs_road_estimate": vs_road_estimate,
        "trees_to_offset": trees_to_offset,
        "carbon_offset_cost_usd": carbon_offset_cost_usd,
    }
