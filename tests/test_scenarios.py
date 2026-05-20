"""Tests for state.scenarios.

Covers:
  - Schema sanity on every catalog entry (target string well-formed,
    multipliers finite and positive, etc.)
  - Target-key matching (exact, wildcard, mismatched namespace/field)
  - Apply logic (multiplier, addend, multi-shock compounding)
  - Bulk overlay across an iterable of (id, value) pairs
  - The catalog's pre-built scenarios produce sensible effects on
    representative inputs (Suez closure raises Asia-Europe rate, etc.)
  - Session round-trip when Streamlit is unavailable falls through silently
"""
from __future__ import annotations

import math

import pytest

from state.scenarios import (
    SCENARIO_CATALOG,
    Scenario,
    ScenarioShock,
    _aggregate_shocks,
    _split_target,
    _target_matches,
    active_scenario,
    get_scenario,
    list_scenarios,
    overlay_addend,
    overlay_iterable,
    overlay_multiplier,
    overlay_value,
    set_active_scenario,
)


# ─── _split_target ──────────────────────────────────────────────────────────

def test_split_target_basic() -> None:
    assert _split_target("route:asia_europe.rate") == ("route", "asia_europe", "rate")
    assert _split_target("ticker:ZIM.return") == ("ticker", "ZIM", "return")
    assert _split_target("commodity:wti.spot") == ("commodity", "wti", "spot")
    assert _split_target("macro:bdi.level") == ("macro", "bdi", "level")


def test_split_target_with_wildcard_id() -> None:
    assert _split_target("route:*.rate") == ("route", "*", "rate")


def test_split_target_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        _split_target("route_no_colon.rate")
    with pytest.raises(ValueError):
        _split_target("route:no_dot")
    with pytest.raises(ValueError):
        _split_target(":empty_ns.rate")
    with pytest.raises(ValueError):
        _split_target("route:.empty_id")


# ─── _target_matches ────────────────────────────────────────────────────────

def test_target_matches_exact() -> None:
    assert _target_matches("route:asia_europe.rate", "route:asia_europe.rate") is True
    assert _target_matches("route:asia_europe.rate", "route:transpacific_eb.rate") is False


def test_target_matches_wildcard_in_id_only() -> None:
    # Wildcard in <id> matches any id with same namespace+field.
    assert _target_matches("route:*.rate", "route:asia_europe.rate") is True
    assert _target_matches("route:*.rate", "route:transpacific_eb.rate") is True
    # Wildcard does NOT bridge namespaces.
    assert _target_matches("route:*.rate", "ticker:ZIM.rate") is False
    # Wildcard does NOT bridge fields.
    assert _target_matches("route:*.rate", "route:asia_europe.transit_days") is False


def test_target_matches_handles_invalid_input() -> None:
    # Malformed targets should be treated as no-match, not raise.
    assert _target_matches("bad", "route:asia_europe.rate") is False
    assert _target_matches("route:asia_europe.rate", "also_bad") is False


# ─── _aggregate_shocks ──────────────────────────────────────────────────────

def test_aggregate_shocks_no_scenario_is_identity() -> None:
    assert _aggregate_shocks(None, "route:asia_europe.rate") == (1.0, 0.0)


def test_aggregate_shocks_compounds_multiplicatively() -> None:
    scenario = Scenario(
        id="t", name="t", summary="", category="Test",
        shocks=(
            ScenarioShock("route:asia_europe.rate", multiplier=1.2),
            ScenarioShock("route:*.rate", multiplier=1.05),  # also matches
        ),
    )
    mult, add = _aggregate_shocks(scenario, "route:asia_europe.rate")
    assert mult == pytest.approx(1.2 * 1.05)
    assert add == 0.0


def test_aggregate_shocks_sums_addends() -> None:
    scenario = Scenario(
        id="t", name="t", summary="", category="Test",
        shocks=(
            ScenarioShock("route:*.transit_days", addend=10.0),
            ScenarioShock("route:asia_europe.transit_days", addend=2.0),
        ),
    )
    mult, add = _aggregate_shocks(scenario, "route:asia_europe.transit_days")
    assert mult == 1.0
    assert add == pytest.approx(12.0)


def test_aggregate_shocks_ignores_non_matching() -> None:
    scenario = Scenario(
        id="t", name="t", summary="", category="Test",
        shocks=(
            ScenarioShock("ticker:ZIM.return", multiplier=1.5),
        ),
    )
    mult, add = _aggregate_shocks(scenario, "route:asia_europe.rate")
    assert (mult, add) == (1.0, 0.0)


# ─── overlay_* helpers ──────────────────────────────────────────────────────

def test_overlay_value_with_no_scenario_returns_base() -> None:
    assert overlay_value("route:asia_europe.rate", 2000.0, scenario=None) == 2000.0


def test_overlay_value_applies_multiplier_then_addend() -> None:
    scenario = Scenario(
        id="t", name="t", summary="", category="Test",
        shocks=(
            ScenarioShock("route:asia_europe.rate", multiplier=1.2, addend=50.0),
        ),
    )
    # 2000 * 1.2 + 50 = 2450
    assert overlay_value("route:asia_europe.rate", 2000.0, scenario) == pytest.approx(2450.0)


def test_overlay_multiplier_returns_one_when_no_match() -> None:
    scenario = SCENARIO_CATALOG["suez_closure"]
    # Suez closure doesn't touch tanker rates by direct target name.
    assert overlay_multiplier("ticker:DHT.return", scenario) == 1.0


def test_overlay_addend_returns_zero_when_no_match() -> None:
    scenario = SCENARIO_CATALOG["suez_closure"]
    assert overlay_addend("ticker:MATX.return", scenario) == 0.0


# ─── overlay_iterable bulk apply ────────────────────────────────────────────

def test_overlay_iterable_applies_to_each_item() -> None:
    scenario = Scenario(
        id="t", name="t", summary="", category="Test",
        shocks=(
            ScenarioShock("route:*.rate", multiplier=1.10),
            ScenarioShock("route:asia_europe.rate", multiplier=1.20),  # compounds with wildcard
        ),
    )
    base = [
        ("transpacific_eb", 2500.0),
        ("asia_europe", 2000.0),
        ("med_hub_to_asia", 1800.0),
    ]
    out = overlay_iterable("route:{id}.rate", base, scenario)
    # transpacific_eb gets only the wildcard: 2500 * 1.10 = 2750
    assert out["transpacific_eb"] == pytest.approx(2750.0)
    # asia_europe gets both: 2000 * 1.10 * 1.20 = 2640
    assert out["asia_europe"] == pytest.approx(2640.0)
    # med_hub_to_asia gets only wildcard: 1800 * 1.10 = 1980
    assert out["med_hub_to_asia"] == pytest.approx(1980.0)


# ─── Catalog sanity — every prebuilt scenario is well-formed ────────────────

def test_catalog_is_non_empty() -> None:
    assert len(SCENARIO_CATALOG) >= 5


def test_catalog_each_entry_is_internally_consistent() -> None:
    """Every prebuilt scenario must pass these basic sanity checks."""
    valid_categories = {"Geopolitical", "Weather", "Macro", "Operational", "Demand"}
    for sid, scen in SCENARIO_CATALOG.items():
        assert isinstance(scen, Scenario)
        assert scen.id == sid, f"scenario id mismatch: dict key={sid} vs .id={scen.id}"
        assert scen.name.strip(), f"scenario {sid} has empty name"
        assert scen.summary.strip(), f"scenario {sid} has empty summary"
        assert scen.category in valid_categories, (
            f"scenario {sid} has unknown category {scen.category!r}"
        )
        assert len(scen.shocks) >= 1, f"scenario {sid} has no shocks"
        for shock in scen.shocks:
            assert isinstance(shock, ScenarioShock)
            # Target must parse.
            _split_target(shock.target)
            assert math.isfinite(shock.multiplier)
            assert math.isfinite(shock.addend)
            assert shock.multiplier > 0.0, (
                f"scenario {sid} has non-positive multiplier on {shock.target}"
            )


# ─── Catalog-specific effects ───────────────────────────────────────────────

def test_suez_closure_raises_asia_europe_rate() -> None:
    scen = SCENARIO_CATALOG["suez_closure"]
    base_rate = 2000.0
    shocked = overlay_value("route:asia_europe.rate", base_rate, scen)
    assert shocked > base_rate * 1.30   # at least +30%


def test_suez_closure_adds_transit_days() -> None:
    scen = SCENARIO_CATALOG["suez_closure"]
    # +10 day reroute via Cape on the asia_europe lane.
    assert overlay_addend("route:asia_europe.transit_days", scen) == pytest.approx(10.0)


def test_us_china_tariff_lifts_tp_eb_pulls_down_tp_wb() -> None:
    scen = SCENARIO_CATALOG["us_china_tariff_25"]
    assert overlay_multiplier("route:transpacific_eb.rate", scen) > 1.0
    assert overlay_multiplier("route:transpacific_wb.rate", scen) < 1.0


def test_demand_recession_drags_all_rates_and_returns() -> None:
    scen = SCENARIO_CATALOG["demand_recession"]
    for route_id in ("transpacific_eb", "asia_europe", "north_africa_to_europe"):
        assert overlay_multiplier(f"route:{route_id}.rate", scen) < 1.0
    for tkr in ("ZIM", "MATX", "SBLK"):
        assert overlay_multiplier(f"ticker:{tkr}.return", scen) < 1.0


def test_oil_spike_lifts_all_route_rates_via_wildcard() -> None:
    scen = SCENARIO_CATALOG["oil_spike_30"]
    # No explicit per-route shock — the +6% is a wildcard on route:*.rate.
    for route_id in ("transpacific_eb", "asia_europe", "longbeach_to_asia"):
        mult = overlay_multiplier(f"route:{route_id}.rate", scen)
        assert mult == pytest.approx(1.06)


def test_houthi_escalation_lifts_med_routes_more_than_asia_routes() -> None:
    scen = SCENARIO_CATALOG["houthi_escalation"]
    middle_east_eu = overlay_multiplier("route:middle_east_to_europe.rate", scen)
    asia_eu = overlay_multiplier("route:asia_europe.rate", scen)
    # Middle East ↔ Europe is the most directly affected lane.
    assert middle_east_eu > asia_eu


# ─── list_scenarios / get_scenario ──────────────────────────────────────────

def test_list_scenarios_is_sorted_and_complete() -> None:
    listed = list_scenarios()
    assert [s.id for s in listed] == sorted(SCENARIO_CATALOG)
    assert len(listed) == len(SCENARIO_CATALOG)


def test_get_scenario_returns_none_for_unknown() -> None:
    assert get_scenario("does_not_exist") is None
    assert get_scenario("suez_closure") is SCENARIO_CATALOG["suez_closure"]


# ─── active_scenario / set_active_scenario — session binding ────────────────

def test_active_scenario_round_trips_through_session() -> None:
    """In the test harness Streamlit IS importable (it's in requirements), so
    set/get must round-trip through state.session.get_session().

    state.session.get_session() falls back to an in-process default when no
    Streamlit runtime is running — perfect for tests."""
    # Start clean.
    set_active_scenario(None)
    assert active_scenario() is None

    # Set a real scenario.
    set_active_scenario("suez_closure")
    got = active_scenario()
    assert got is not None
    assert got.id == "suez_closure"

    # Setting an unknown id clears it.
    set_active_scenario("nonexistent_scenario_xyz")
    assert active_scenario() is None

    # Setting None clears it explicitly.
    set_active_scenario("us_china_tariff_25")
    assert active_scenario() is not None
    set_active_scenario(None)
    assert active_scenario() is None
