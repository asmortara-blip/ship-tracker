"""Tests for processing.carbon_calculator — voyage CO2 + ESG metrics.

Covers:
  - ROUTE_DISTANCES catalog: 17 entries, all positive, all string keys
  - RouteEmissions dataclass: instantiates with all fields, types preserved
  - calculate_route_emissions:
      * core formulas: total_fuel_mt = 85 * days
                       co2_emissions_mt = total_fuel_mt * 3.114
                       co2_per_teu_mt = co2 / (8000 * 0.85)
                       carbon_cost_usd = co2 * 80
      * eedi_score formula = max(0, 100 * (1 - co2_per_teu / 1.15))
        rescaled against a 29-day long-haul reference so all four
        grade bands distribute across catalog transit times (3-35 days)
      * eedi_score clamped to >= 0 for voyages worse than the reference
      * sustainability_grade tiers ('A' / 'B' / 'C' / 'D') exercised via the
        eedi_score thresholds (>80 / >60 / >40 / else); all four reachable
        from the 17 catalog routes (short intra-Asia → A, long-haul → D)
      * poseidon_compliant boundary: True iff co2_per_teu_mt < 0.12
        (d=3 days → compliant; d=4 → not)
      * unknown route_id → distance_nm = 0.0 (other fields still computed)
      * co2_vs_air_freight_pct fixed at 0.98
      * vessel_type / teu_capacity / fuel_consumption echoed from constants
  - calculate_all_routes:
      * returns one RouteEmissions per registered ShippingRoute
      * sorted ascending by co2_per_teu_mt
      * each entry's distance_nm matches ROUTE_DISTANCES[id]
      * all four sustainability grades A/B/C/D appear in the catalog
  - compare_to_alternatives:
      * vs_air_freight_co2_ratio = 50.0; vs_road_estimate = 3.0
      * trees_to_offset = ceil(co2_emissions_mt / 0.42); always an int
      * carbon_offset_cost_usd = co2_emissions_mt * 15
      * ceil rounding: co2 just above 0.42 → 2 trees
      * zero-emission voyage → 0 trees, $0 offset
"""
from __future__ import annotations

import math

import pytest

from processing.carbon_calculator import (
    ROUTE_DISTANCES,
    RouteEmissions,
    calculate_all_routes,
    calculate_route_emissions,
    compare_to_alternatives,
)
from routes.route_registry import ROUTES


# Catalog constants pinned for arithmetic checks
_FUEL_PER_DAY = 85.0
_CO2_FACTOR = 3.114
_TEU = 8_000
_LOAD = 0.85
_LOADED_TEU = _TEU * _LOAD  # 6800
_EEDI_REFERENCE = 1.15  # MT CO2 / TEU at the long-haul reference voyage
_ETS_PRICE = 80.0
_TREE_MT = 0.42
_OFFSET_PRICE = 15.0


# ─── ROUTE_DISTANCES catalog ────────────────────────────────────────────────

def test_route_distances_has_seventeen_routes() -> None:
    assert len(ROUTE_DISTANCES) == 17


def test_route_distances_all_positive() -> None:
    for route_id, dist in ROUTE_DISTANCES.items():
        assert dist > 0, f"{route_id} has non-positive distance"


def test_route_distances_keys_match_route_registry() -> None:
    """Every catalog key must be a registered route id."""
    registered_ids = {r.id for r in ROUTES}
    for route_id in ROUTE_DISTANCES:
        assert route_id in registered_ids, f"{route_id} not in ROUTES"


# ─── RouteEmissions dataclass ───────────────────────────────────────────────

def test_route_emissions_dataclass_shape() -> None:
    em = RouteEmissions(
        route_id="r", route_name="R", transit_days=10, distance_nm=1000.0,
        vessel_type="large_container", teu_capacity=8000,
        fuel_consumption_mt_per_day=85.0, total_fuel_mt=850.0,
        co2_emissions_mt=2647.0, co2_per_teu_mt=0.389,
        co2_vs_air_freight_pct=0.98, eedi_score=22.0,
        poseidon_compliant=False, carbon_cost_usd=211_800.0,
        sustainability_grade="D",
    )
    assert em.route_id == "r"
    assert em.poseidon_compliant is False
    assert em.sustainability_grade == "D"


# ─── calculate_route_emissions: core arithmetic ─────────────────────────────

def test_total_fuel_mt_formula() -> None:
    """total_fuel_mt = 85 * transit_days."""
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=14)
    assert em.total_fuel_mt == pytest.approx(_FUEL_PER_DAY * 14, abs=1e-9)


def test_co2_emissions_mt_formula() -> None:
    """co2 = fuel * 3.114."""
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=14)
    expected = _FUEL_PER_DAY * 14 * _CO2_FACTOR
    assert em.co2_emissions_mt == pytest.approx(expected, abs=1e-9)


def test_co2_per_teu_mt_formula() -> None:
    """co2_per_teu = co2 / (8000 * 0.85)."""
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=14)
    expected = (_FUEL_PER_DAY * 14 * _CO2_FACTOR) / _LOADED_TEU
    assert em.co2_per_teu_mt == pytest.approx(expected, abs=1e-9)


def test_carbon_cost_usd_formula() -> None:
    """carbon_cost = co2_mt * $80."""
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=14)
    expected = (_FUEL_PER_DAY * 14 * _CO2_FACTOR) * _ETS_PRICE
    assert em.carbon_cost_usd == pytest.approx(expected, abs=1e-9)


def test_co2_vs_air_freight_pct_is_constant_98() -> None:
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=14)
    assert em.co2_vs_air_freight_pct == pytest.approx(0.98)


def test_distance_nm_matches_catalog() -> None:
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=14)
    assert em.distance_nm == ROUTE_DISTANCES["transpacific_eb"]


def test_unknown_route_id_distance_zero() -> None:
    """ROUTE_DISTANCES.get(id, 0.0) → unknown id maps to 0.0."""
    em = calculate_route_emissions("not_a_real_route", "X", transit_days=10)
    assert em.distance_nm == 0.0
    # Other fields still computed from transit_days
    assert em.total_fuel_mt == pytest.approx(850.0)


def test_constants_echoed_to_emissions() -> None:
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=14)
    assert em.vessel_type == "large_container"
    assert em.teu_capacity == 8000
    assert em.fuel_consumption_mt_per_day == pytest.approx(85.0)


# ─── calculate_route_emissions: EEDI formula ────────────────────────────────

def test_eedi_score_zero_days_is_one_hundred() -> None:
    """transit_days=0 → no fuel, no CO2, eedi at benchmark = 100."""
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=0)
    assert em.eedi_score == pytest.approx(100.0)


def test_eedi_score_formula_matches_hand_calc() -> None:
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=1)
    expected_co2_per_teu = (_FUEL_PER_DAY * 1 * _CO2_FACTOR) / _LOADED_TEU
    expected_eedi = max(0.0, 100.0 * (1.0 - expected_co2_per_teu / _EEDI_REFERENCE))
    assert em.eedi_score == pytest.approx(expected_eedi, abs=1e-9)


def test_eedi_score_clamps_at_zero_for_long_voyages() -> None:
    """A 35-day voyage produces co2/teu ~1.36 > 1.15 reference → clamps to 0."""
    em = calculate_route_emissions("china_south_america", "CN-SA", transit_days=35)
    assert em.eedi_score == 0.0


# ─── calculate_route_emissions: Poseidon threshold ──────────────────────────

def test_poseidon_compliant_below_threshold() -> None:
    """3 days → co2/teu ≈ 0.117 < 0.12 → compliant."""
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=3)
    assert em.co2_per_teu_mt < 0.12
    assert em.poseidon_compliant is True


def test_poseidon_not_compliant_above_threshold() -> None:
    """4 days → co2/teu ≈ 0.156 ≥ 0.12 → non-compliant."""
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=4)
    assert em.co2_per_teu_mt >= 0.12
    assert em.poseidon_compliant is False


def test_poseidon_compliant_at_zero_days() -> None:
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=0)
    assert em.poseidon_compliant is True


# ─── calculate_route_emissions: sustainability grade ────────────────────────

def test_grade_a_when_eedi_above_eighty() -> None:
    """transit_days=0 gives eedi=100 → grade 'A'."""
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=0)
    assert em.eedi_score > 80
    assert em.sustainability_grade == "A"


def test_grade_b_when_eedi_between_sixty_and_eighty() -> None:
    """transit_days=8 → eedi≈72.9 → grade 'B' (short-to-mid haul)."""
    em = calculate_route_emissions("north_africa_to_europe", "NA-EU", transit_days=8)
    assert 60 < em.eedi_score <= 80
    assert em.sustainability_grade == "B"


def test_grade_c_when_eedi_between_forty_and_sixty() -> None:
    """transit_days=14 → eedi≈52.6 → grade 'C' (mid-haul transpac)."""
    em = calculate_route_emissions("transpacific_eb", "TP-EB", transit_days=14)
    assert 40 < em.eedi_score <= 60
    assert em.sustainability_grade == "C"


def test_grade_d_when_eedi_at_or_below_forty() -> None:
    """transit_days=22 → eedi≈25.5 → grade 'D' (long-haul)."""
    em = calculate_route_emissions("europe_south_america", "EU-SA", transit_days=22)
    assert em.eedi_score <= 40
    assert em.sustainability_grade == "D"


def test_short_intra_asia_routes_grade_a() -> None:
    """Real catalog: the two shortest routes (3 & 5 days) are grade A."""
    em3 = calculate_route_emissions("intra_asia_china_japan", "Intra-CN-JP", transit_days=3)
    em5 = calculate_route_emissions("intra_asia_china_sea", "Intra-CN-SE", transit_days=5)
    assert em3.sustainability_grade == "A"
    assert em5.sustainability_grade == "A"


def test_mid_haul_routes_grade_c() -> None:
    """Real catalog: 12-16 day routes (transpac, transatlantic) are grade C."""
    for route_id, days in (("transatlantic", 12), ("transpacific_eb", 14), ("transpacific_wb", 16)):
        em = calculate_route_emissions(route_id, route_id, transit_days=days)
        assert em.sustainability_grade == "C", (
            f"{route_id} (d={days}) graded {em.sustainability_grade}, expected C"
        )


def test_long_haul_routes_grade_d() -> None:
    """Real catalog: 20+ day routes (asia-europe, china-south-america) are grade D."""
    for route_id, days in (("south_asia_to_europe", 20),
                           ("asia_europe", 25),
                           ("ningbo_europe", 28),
                           ("china_south_america", 35)):
        em = calculate_route_emissions(route_id, route_id, transit_days=days)
        assert em.sustainability_grade == "D", (
            f"{route_id} (d={days}) graded {em.sustainability_grade}, expected D"
        )


def test_all_four_grades_present_in_registered_routes() -> None:
    """With the rescaled EEDI, every grade A/B/C/D is reachable from the catalog."""
    grades = {em.sustainability_grade for em in calculate_all_routes()}
    assert grades == {"A", "B", "C", "D"}


# ─── calculate_all_routes ───────────────────────────────────────────────────

def test_calculate_all_routes_one_per_registered_route() -> None:
    results = calculate_all_routes()
    assert len(results) == len(ROUTES)


def test_calculate_all_routes_sorted_ascending_by_co2_per_teu() -> None:
    results = calculate_all_routes()
    co2_values = [r.co2_per_teu_mt for r in results]
    assert co2_values == sorted(co2_values)


def test_calculate_all_routes_distance_matches_catalog() -> None:
    """Every emitted distance_nm matches the catalog (or 0 if missing)."""
    results = calculate_all_routes()
    for em in results:
        expected = ROUTE_DISTANCES.get(em.route_id, 0.0)
        assert em.distance_nm == expected


def test_calculate_all_routes_co2_positive_for_all() -> None:
    """All registered routes have transit_days >= 1 → positive CO2."""
    for em in calculate_all_routes():
        assert em.co2_emissions_mt > 0
        assert em.total_fuel_mt > 0
        assert em.carbon_cost_usd > 0


# ─── compare_to_alternatives ────────────────────────────────────────────────

def _emissions_with_co2(co2_mt: float) -> RouteEmissions:
    """Stand-in RouteEmissions where only co2_emissions_mt matters for the
    function under test."""
    return RouteEmissions(
        route_id="x", route_name="X", transit_days=1, distance_nm=0.0,
        vessel_type="large_container", teu_capacity=8000,
        fuel_consumption_mt_per_day=85.0, total_fuel_mt=0.0,
        co2_emissions_mt=co2_mt, co2_per_teu_mt=0.0,
        co2_vs_air_freight_pct=0.98, eedi_score=0.0,
        poseidon_compliant=False, carbon_cost_usd=0.0,
        sustainability_grade="D",
    )


def test_compare_alternatives_constant_ratios() -> None:
    out = compare_to_alternatives(_emissions_with_co2(100.0))
    assert out["vs_air_freight_co2_ratio"] == pytest.approx(50.0)
    assert out["vs_road_estimate"] == pytest.approx(3.0)


def test_compare_alternatives_trees_uses_ceil() -> None:
    """trees = ceil(co2_mt / 0.42); 0.42 exactly → 1 tree, 0.43 → 2."""
    out_exact = compare_to_alternatives(_emissions_with_co2(0.42))
    assert out_exact["trees_to_offset"] == 1
    out_just_above = compare_to_alternatives(_emissions_with_co2(0.43))
    assert out_just_above["trees_to_offset"] == 2


def test_compare_alternatives_trees_matches_formula() -> None:
    co2 = 3705.66  # 14-day transpacific value
    out = compare_to_alternatives(_emissions_with_co2(co2))
    assert out["trees_to_offset"] == math.ceil(co2 / _TREE_MT)


def test_compare_alternatives_trees_is_int() -> None:
    out = compare_to_alternatives(_emissions_with_co2(100.0))
    assert isinstance(out["trees_to_offset"], int)


def test_compare_alternatives_offset_cost_formula() -> None:
    out = compare_to_alternatives(_emissions_with_co2(1000.0))
    assert out["carbon_offset_cost_usd"] == pytest.approx(1000.0 * _OFFSET_PRICE)


def test_compare_alternatives_zero_emissions() -> None:
    out = compare_to_alternatives(_emissions_with_co2(0.0))
    assert out["trees_to_offset"] == 0
    assert out["carbon_offset_cost_usd"] == pytest.approx(0.0)


def test_compare_alternatives_keys() -> None:
    out = compare_to_alternatives(_emissions_with_co2(100.0))
    assert set(out.keys()) == {
        "vs_air_freight_co2_ratio",
        "vs_road_estimate",
        "trees_to_offset",
        "carbon_offset_cost_usd",
    }


# ─── Integration with calculate_all_routes ──────────────────────────────────

def test_compare_alternatives_on_each_real_route() -> None:
    """End-to-end: every registered route surfaces sensible offset numbers."""
    for em in calculate_all_routes():
        out = compare_to_alternatives(em)
        assert out["trees_to_offset"] >= 1
        assert out["carbon_offset_cost_usd"] > 0
