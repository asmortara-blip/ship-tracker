"""Pure-function tests for processing.chokepoint_analyzer.

The chokepoint analyzer models 9 critical maritime passages. These tests pin:

  * the CHOKEPOINTS registry shape and field invariants;
  * ``compute_chokepoint_risk_score`` — every score in [0, 1], deterministic,
    and a worse risk level / disruption strictly raising the composite;
  * ``simulate_chokepoint_closure`` — output schema, monotone growth of rate
    impact with duration, the documented 150% rate-impact cap, and graceful
    handling of an unknown chokepoint;
  * ``get_current_active_disruptions`` — only non-NONE disruptions surface.

No Streamlit, no live feed.
"""
from __future__ import annotations

import pytest

from processing.chokepoint_analyzer import (
    CHOKEPOINTS,
    Chokepoint,
    compute_chokepoint_risk_score,
    get_current_active_disruptions,
    risk_color,
    simulate_chokepoint_closure,
)


_VALID_RISK_LEVELS = {"CRITICAL", "HIGH", "MODERATE", "LOW"}
_VALID_DISRUPTION_TYPES = {
    "NONE", "ACTIVE_CONFLICT", "WEATHER", "DIPLOMATIC", "CONGESTION",
}


# ── Registry shape ──────────────────────────────────────────────────────────


def test_registry_has_nine_chokepoints() -> None:
    """The module docstring promises 9 critical maritime passages."""
    assert len(CHOKEPOINTS) == 9


def test_every_registry_entry_is_a_chokepoint() -> None:
    assert all(isinstance(cp, Chokepoint) for cp in CHOKEPOINTS.values())


def test_registry_field_invariants() -> None:
    """Physical / economic descriptors are non-negative and labels are valid."""
    for key, cp in CHOKEPOINTS.items():
        assert cp.width_km > 0.0, key
        assert cp.daily_vessels > 0, key
        assert cp.daily_teu_m >= 0.0, key
        assert 0.0 <= cp.pct_global_trade <= 100.0, key
        assert cp.rerouting_cost_per_voyage_usd >= 0, key
        assert cp.extra_days_if_closed >= 0, key
        assert cp.current_risk_level in _VALID_RISK_LEVELS, key
        assert cp.current_disruption_type in _VALID_DISRUPTION_TYPES, key
        assert isinstance(cp.affected_routes, list)
        assert isinstance(cp.strategic_alternatives, list)


def test_disruption_since_present_iff_disrupted() -> None:
    """A chokepoint with no disruption has no disruption_since date, and vice versa."""
    for key, cp in CHOKEPOINTS.items():
        if cp.current_disruption_type == "NONE":
            assert cp.disruption_since is None, key
        else:
            assert cp.disruption_since is not None, key


# ── risk_color ──────────────────────────────────────────────────────────────


def test_risk_color_returns_hex_for_known_levels() -> None:
    for level in _VALID_RISK_LEVELS:
        color = risk_color(level)
        assert isinstance(color, str) and color.startswith("#") and len(color) == 7


def test_risk_color_unknown_level_returns_fallback_hex() -> None:
    color = risk_color("NOT_A_LEVEL")
    assert color.startswith("#") and len(color) == 7


# ── compute_chokepoint_risk_score ───────────────────────────────────────────


def test_risk_score_covers_every_chokepoint() -> None:
    scores = compute_chokepoint_risk_score()
    assert set(scores) == set(CHOKEPOINTS)


def test_risk_score_in_unit_interval() -> None:
    for key, value in compute_chokepoint_risk_score().items():
        assert 0.0 <= value <= 1.0, key


def test_risk_score_is_deterministic() -> None:
    """No randomness — repeated calls return identical scores."""
    assert compute_chokepoint_risk_score() == compute_chokepoint_risk_score()


def test_critical_active_conflict_outranks_low_calm_passage() -> None:
    """A CRITICAL chokepoint under active conflict scores above a LOW/NONE one.

    Suez (CRITICAL, ACTIVE_CONFLICT) vs Dover (LOW, NONE) — the model must
    rank the disrupted critical passage as the higher composite risk.
    """
    scores = compute_chokepoint_risk_score()
    assert scores["suez"] > scores["dover"]
    assert scores["bab_el_mandeb"] > scores["gibraltar"]


# ── simulate_chokepoint_closure: schema ─────────────────────────────────────


def test_closure_simulation_schema() -> None:
    result = simulate_chokepoint_closure("suez", duration_weeks=4)
    expected_keys = {
        "chokepoint_name", "duration_weeks", "affected_routes",
        "rate_impact_pct", "global_trade_impact_pct", "rerouting_cost_total_usd",
        "alternative_routes", "extra_days_if_closed", "feasibility_note",
    }
    assert expected_keys <= set(result)
    assert "error" not in result


def test_closure_simulation_accepts_name_and_key() -> None:
    """Lookup works by registry key and by case-insensitive display name."""
    by_key = simulate_chokepoint_closure("panama", duration_weeks=3)
    by_name = simulate_chokepoint_closure("PANAMA CANAL", duration_weeks=3)
    assert by_key["chokepoint_name"] == by_name["chokepoint_name"]
    assert by_key["rate_impact_pct"] == by_name["rate_impact_pct"]


def test_closure_simulation_numeric_invariants() -> None:
    result = simulate_chokepoint_closure("malacca", duration_weeks=6)
    assert result["rate_impact_pct"] >= 0.0
    assert result["global_trade_impact_pct"] >= 0.0
    assert result["rerouting_cost_total_usd"] >= 0
    assert result["duration_weeks"] == 6


# ── simulate_chokepoint_closure: monotonicity & cap ─────────────────────────


def test_closure_rate_impact_grows_with_duration() -> None:
    """A longer closure cannot reduce the projected rate impact."""
    short = simulate_chokepoint_closure("suez", duration_weeks=2)
    long = simulate_chokepoint_closure("suez", duration_weeks=10)
    assert long["rate_impact_pct"] >= short["rate_impact_pct"]


def test_closure_rerouting_cost_grows_with_duration() -> None:
    short = simulate_chokepoint_closure("panama", duration_weeks=2)
    long = simulate_chokepoint_closure("panama", duration_weeks=12)
    assert long["rerouting_cost_total_usd"] >= short["rerouting_cost_total_usd"]


def test_closure_rate_impact_respects_150pct_cap() -> None:
    """An extreme closure duration is capped at the documented 150% ceiling."""
    result = simulate_chokepoint_closure("suez", duration_weeks=520)
    assert result["rate_impact_pct"] <= 150.0


# ── simulate_chokepoint_closure: graceful degradation ───────────────────────


def test_closure_unknown_chokepoint_returns_error() -> None:
    """An unknown chokepoint name yields an error dict, never a crash."""
    result = simulate_chokepoint_closure("Bermuda Triangle", duration_weeks=4)
    assert result.get("error") == "Unknown chokepoint"


# ── get_current_active_disruptions ──────────────────────────────────────────


def test_active_disruptions_excludes_none_type() -> None:
    active = get_current_active_disruptions()
    assert all(cp.current_disruption_type != "NONE" for cp in active)


def test_active_disruptions_subset_of_registry() -> None:
    active = get_current_active_disruptions()
    registry_names = {cp.name for cp in CHOKEPOINTS.values()}
    assert all(cp.name in registry_names for cp in active)


def test_active_disruptions_matches_manual_count() -> None:
    """The helper count equals the registry's non-NONE disruption count."""
    expected = sum(
        1 for cp in CHOKEPOINTS.values()
        if cp.current_disruption_type != "NONE"
    )
    assert len(get_current_active_disruptions()) == expected
