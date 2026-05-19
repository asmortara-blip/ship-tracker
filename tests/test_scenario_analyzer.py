"""Pure-function tests for processing.scenario_analyzer.

The what-if engine applies macro / geopolitical shocks to route and port
results. These tests pin:

  * ScenarioInput / ScenarioResult dataclass shapes;
  * the 8 PREDEFINED_SCENARIOS registry;
  * ``run_scenario`` output schema, [0, 1] clamping of scenario scores, and
    delta consistency (delta == scenario - baseline);
  * directional invariants — a positive demand shock raises opportunity, a
    tariff hike drags Trans-Pacific routes, a Suez closure lifts Asia-Europe;
  * the ``_risk_level`` ladder and graceful degradation on empty inputs;
  * ``run_all_scenarios`` running all 8 and sorting by |delta| descending.

Route / port inputs are duck-typed stubs — the module reads every attribute
via ``getattr``, so a lightweight object is sufficient. No Streamlit, no feed.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from processing.scenario_analyzer import (
    PREDEFINED_SCENARIOS,
    ScenarioInput,
    ScenarioResult,
    run_all_scenarios,
    run_scenario,
)


# ── Duck-typed input stubs ──────────────────────────────────────────────────


@dataclass
class _RouteStub:
    """Minimal stand-in for a RouteOpportunity."""
    route_id: str
    route_name: str
    fbx_index: str
    transit_days: int
    opportunity_score: float


@dataclass
class _PortStub:
    """Minimal stand-in for a PortDemandResult."""
    locode: str
    port_name: str
    demand_score: float


def _sample_routes() -> list[_RouteStub]:
    """A small cross-section: a Trans-Pacific lane, an Asia-Europe lane, a long-haul."""
    return [
        _RouteStub("transpacific_eb", "Trans-Pacific EB", "FBX01", 14, 0.60),
        _RouteStub("asia_europe", "Asia-Europe", "FBX03", 30, 0.55),
        _RouteStub("middle_east_to_europe", "ME-Europe", "FBX04", 22, 0.50),
    ]


def _sample_ports() -> list[_PortStub]:
    return [
        _PortStub("USLAX", "Los Angeles", 0.62),
        _PortStub("NLRTM", "Rotterdam", 0.48),
    ]


# ── Dataclass shapes ────────────────────────────────────────────────────────


def test_scenario_input_defaults_are_neutral() -> None:
    """A bare ScenarioInput carries zero shocks and no canal closures."""
    si = ScenarioInput(name="Empty")
    assert si.bdi_shock == 0.0
    assert si.fuel_shock == 0.0
    assert si.pmi_shock == 0.0
    assert si.demand_shock == 0.0
    assert si.us_china_tariff_hike == 0.0
    assert si.suez_closed is False
    assert si.panama_closed is False


def test_scenario_result_field_presence() -> None:
    result = run_scenario(ScenarioInput(name="Neutral"), _sample_ports(), _sample_routes())
    assert isinstance(result, ScenarioResult)
    assert isinstance(result.scenario, ScenarioInput)
    assert isinstance(result.baseline_avg_opportunity, float)
    assert isinstance(result.scenario_avg_opportunity, float)
    assert isinstance(result.opportunity_delta, float)
    assert isinstance(result.route_impacts, list)
    assert isinstance(result.port_impacts, list)
    assert isinstance(result.summary, str) and result.summary
    assert result.risk_level in {"LOW", "MODERATE", "HIGH", "SEVERE"}


# ── Predefined registry ─────────────────────────────────────────────────────


def test_eight_predefined_scenarios() -> None:
    """The module docstring promises 8 predefined scenarios."""
    assert len(PREDEFINED_SCENARIOS) == 8
    assert all(isinstance(s, ScenarioInput) for s in PREDEFINED_SCENARIOS)


def test_predefined_scenarios_have_names_and_descriptions() -> None:
    for s in PREDEFINED_SCENARIOS:
        assert s.name and isinstance(s.name, str)
        assert s.description and isinstance(s.description, str)


# ── run_scenario: route-impact schema & clamping ────────────────────────────


def test_route_impacts_one_row_per_route() -> None:
    routes = _sample_routes()
    result = run_scenario(ScenarioInput(name="Neutral"), [], routes)
    assert len(result.route_impacts) == len(routes)


def test_route_impact_row_schema() -> None:
    result = run_scenario(PREDEFINED_SCENARIOS[0], _sample_ports(), _sample_routes())
    for row in result.route_impacts:
        assert {
            "route_id", "route_name", "baseline", "scenario_score",
            "delta", "impact_reason",
        } <= set(row)
        assert isinstance(row["impact_reason"], str) and row["impact_reason"]


def test_scenario_scores_clamped_to_unit_interval() -> None:
    """Even an extreme combined shock keeps every scenario_score within [0, 1]."""
    extreme = ScenarioInput(
        name="Extreme", bdi_shock=3.0, demand_shock=0.3, suez_closed=True,
        panama_closed=True,
    )
    result = run_scenario(extreme, _sample_ports(), _sample_routes())
    for row in result.route_impacts:
        assert 0.0 <= row["scenario_score"] <= 1.0, row["route_id"]


def test_route_delta_equals_scenario_minus_baseline() -> None:
    result = run_scenario(PREDEFINED_SCENARIOS[3], _sample_ports(), _sample_routes())
    for row in result.route_impacts:
        assert row["delta"] == pytest.approx(
            row["scenario_score"] - row["baseline"], abs=1e-9
        )


def test_overall_delta_equals_avg_difference() -> None:
    result = run_scenario(PREDEFINED_SCENARIOS[2], _sample_ports(), _sample_routes())
    assert result.opportunity_delta == pytest.approx(
        result.scenario_avg_opportunity - result.baseline_avg_opportunity, abs=1e-9
    )


# ── run_scenario: port-impact schema ────────────────────────────────────────


def test_port_impacts_one_row_per_port_with_clamped_scores() -> None:
    ports = _sample_ports()
    result = run_scenario(PREDEFINED_SCENARIOS[5], ports, _sample_routes())
    assert len(result.port_impacts) == len(ports)
    for row in result.port_impacts:
        assert {"port_locode", "port_name", "baseline", "scenario_score", "delta"} <= set(row)
        assert 0.0 <= row["scenario_score"] <= 1.0, row["port_locode"]


# ── Directional invariants ──────────────────────────────────────────────────


def test_positive_demand_shock_lifts_average_opportunity() -> None:
    """A pure positive demand surge raises average route opportunity."""
    boom = ScenarioInput(name="Demand surge", demand_shock=0.25)
    result = run_scenario(boom, _sample_ports(), _sample_routes())
    assert result.opportunity_delta > 0.0


def test_negative_demand_shock_drags_average_opportunity() -> None:
    bust = ScenarioInput(name="Demand collapse", demand_shock=-0.30)
    result = run_scenario(bust, _sample_ports(), _sample_routes())
    assert result.opportunity_delta < 0.0


def test_tariff_hike_drags_transpacific_route() -> None:
    """A US-China tariff hike must lower the Trans-Pacific route's scenario score."""
    tariff = ScenarioInput(name="Tariff war", us_china_tariff_hike=0.25)
    result = run_scenario(tariff, [], _sample_routes())
    tpeb = next(r for r in result.route_impacts if r["route_id"] == "transpacific_eb")
    assert tpeb["delta"] < 0.0


def test_suez_closure_lifts_asia_europe_route() -> None:
    """A Suez closure raises the Asia-Europe (FBX03) lane's opportunity."""
    suez = ScenarioInput(name="Suez shut", suez_closed=True)
    result = run_scenario(suez, [], _sample_routes())
    ae = next(r for r in result.route_impacts if r["route_id"] == "asia_europe")
    assert ae["delta"] > 0.0


def test_demand_shock_moves_port_scores_in_same_direction() -> None:
    """Port scenario scores rise with a positive demand shock, fall with a negative one."""
    ports = _sample_ports()
    up = run_scenario(ScenarioInput(name="up", demand_shock=0.2), ports, [])
    down = run_scenario(ScenarioInput(name="down", demand_shock=-0.2), ports, [])
    assert all(r["delta"] > 0.0 for r in up.port_impacts)
    assert all(r["delta"] < 0.0 for r in down.port_impacts)


# ── Risk-level ladder ───────────────────────────────────────────────────────


def test_neutral_scenario_is_low_risk() -> None:
    """A zero-shock scenario produces no material delta → LOW risk."""
    result = run_scenario(ScenarioInput(name="Neutral"), _sample_ports(), _sample_routes())
    assert result.opportunity_delta == pytest.approx(0.0, abs=1e-9)
    assert result.risk_level == "LOW"


def test_large_shock_escalates_risk_above_low() -> None:
    """A severe synchronized recession produces a non-LOW risk rating."""
    recession = next(s for s in PREDEFINED_SCENARIOS if s.name == "Global Recession")
    result = run_scenario(recession, _sample_ports(), _sample_routes())
    assert result.risk_level in {"MODERATE", "HIGH", "SEVERE"}


# ── Graceful degradation ────────────────────────────────────────────────────


def test_empty_inputs_produce_neutral_result() -> None:
    """No routes and no ports → baseline/scenario default to 0.5, no crash."""
    result = run_scenario(PREDEFINED_SCENARIOS[0], [], [])
    assert isinstance(result, ScenarioResult)
    assert result.baseline_avg_opportunity == 0.5
    assert result.scenario_avg_opportunity == 0.5
    assert result.route_impacts == []
    assert result.port_impacts == []


def test_route_missing_optional_attrs_uses_defaults() -> None:
    """A route object lacking opportunity_score falls back to the 0.5 default."""

    class _Bare:
        route_id = "mystery_lane"

    result = run_scenario(ScenarioInput(name="Neutral"), [], [_Bare()])
    assert len(result.route_impacts) == 1
    assert result.route_impacts[0]["baseline"] == 0.5


# ── run_all_scenarios ───────────────────────────────────────────────────────


def test_run_all_scenarios_returns_eight_results() -> None:
    results = run_all_scenarios(_sample_ports(), _sample_routes())
    assert len(results) == len(PREDEFINED_SCENARIOS)
    assert all(isinstance(r, ScenarioResult) for r in results)


def test_run_all_scenarios_sorted_by_abs_delta_descending() -> None:
    results = run_all_scenarios(_sample_ports(), _sample_routes())
    magnitudes = [abs(r.opportunity_delta) for r in results]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_run_all_scenarios_is_deterministic() -> None:
    """The engine has no randomness — identical inputs give identical deltas."""
    a = run_all_scenarios(_sample_ports(), _sample_routes())
    b = run_all_scenarios(_sample_ports(), _sample_routes())
    assert [r.opportunity_delta for r in a] == [r.opportunity_delta for r in b]
