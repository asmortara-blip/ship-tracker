"""Pure-function tests for processing.weather_risk.

The weather-risk model aggregates a fixed catalogue of 18 climate /
seasonal ``WeatherRiskEvent`` records into per-route ``WeatherRiskIndex``
objects and produces calendar-aware ETA buffers. These tests pin:

  * the WeatherRiskEvent / WeatherRiskIndex dataclass shapes;
  * the 18-event catalogue and its internal consistency (valid risk
    types, levels, probabilities, season months);
  * ``compute_route_weather_risk`` output schema, the [0, 1] score bound,
    and the documented composite-score formula;
  * monotonic / directional invariants — more exposed events never lower
    the annualised delay below zero, an unknown route yields a zero index;
  * ``get_current_season_alerts`` filtering and severity ordering;
  * ``compute_weather_adjusted_eta`` invariants — expected/worst-case never
    fall below nominal, worst-case >= expected, and graceful behaviour for
    routes with no in-season events;
  * determinism — the catalogue is static, so repeated calls match.

The module reads only ``datetime.date.today()`` for the current month; no
Streamlit, no live feed, no randomness.
"""
from __future__ import annotations

import pytest

from processing.weather_risk import (
    ALL_ROUTE_IDS,
    WEATHER_RISK_EVENTS,
    WeatherRiskEvent,
    WeatherRiskIndex,
    compute_route_weather_risk,
    compute_weather_adjusted_eta,
    get_current_season_alerts,
    get_nominal_transit_days,
)

_VALID_RISK_TYPES = {"TYPHOON", "HURRICANE", "FOG", "ICE", "MONSOON", "STORM"}
_VALID_RISK_LEVELS = {"ACTIVE", "ELEVATED", "SEASONAL", "LOW"}


# ── Catalogue integrity ─────────────────────────────────────────────────────


def test_catalogue_has_eighteen_events() -> None:
    """The module docstring promises an 18-event catalogue."""
    assert len(WEATHER_RISK_EVENTS) == 18
    assert all(isinstance(ev, WeatherRiskEvent) for ev in WEATHER_RISK_EVENTS)


def test_every_event_is_internally_well_formed() -> None:
    """Each catalogue row carries valid enum-like fields and sane ranges."""
    for ev in WEATHER_RISK_EVENTS:
        assert ev.event_name and isinstance(ev.event_name, str)
        assert ev.risk_type in _VALID_RISK_TYPES, ev.event_name
        assert ev.current_risk_level in _VALID_RISK_LEVELS, ev.event_name
        assert 0.0 <= ev.probability_pct <= 100.0, ev.event_name
        assert ev.delay_days_if_occurs >= 0.0, ev.event_name
        assert ev.affected_routes, ev.event_name
        assert ev.affected_ports, ev.event_name
        assert ev.season_months, ev.event_name
        assert all(1 <= m <= 12 for m in ev.season_months), ev.event_name
        assert ev.description and ev.mitigation


# ── compute_route_weather_risk: schema ──────────────────────────────────────


def test_weather_risk_index_field_presence() -> None:
    idx = compute_route_weather_risk("transpacific_eb")
    assert isinstance(idx, WeatherRiskIndex)
    assert idx.route_id == "transpacific_eb"
    assert isinstance(idx.current_risk_score, float)
    assert isinstance(idx.annualized_delay_days, float)
    assert isinstance(idx.peak_risk_months, list)
    assert isinstance(idx.primary_risk_type, str)
    assert isinstance(idx.historical_disruption_frequency_pct, float)


def test_risk_score_within_unit_interval_for_every_route() -> None:
    """The composite risk score must stay within [0, 1] for all tracked routes."""
    for route_id in ALL_ROUTE_IDS:
        idx = compute_route_weather_risk(route_id)
        assert 0.0 <= idx.current_risk_score <= 1.0, route_id


def test_annualized_delay_is_non_negative_for_every_route() -> None:
    for route_id in ALL_ROUTE_IDS:
        idx = compute_route_weather_risk(route_id)
        assert idx.annualized_delay_days >= 0.0, route_id


def test_peak_months_are_calendar_valid_and_at_most_three() -> None:
    for route_id in ALL_ROUTE_IDS:
        idx = compute_route_weather_risk(route_id)
        assert len(idx.peak_risk_months) <= 3, route_id
        assert all(1 <= m <= 12 for m in idx.peak_risk_months), route_id
        # peak_risk_months is returned sorted ascending
        assert idx.peak_risk_months == sorted(idx.peak_risk_months), route_id


def test_primary_risk_type_is_one_of_the_known_types() -> None:
    """primary_risk_type is a known type, or "NONE" for a route with no events.

    ``longbeach_to_asia`` is a tracked route ID with no catalogue coverage, so
    "NONE" is a legitimate sentinel here.
    """
    allowed = _VALID_RISK_TYPES | {"NONE"}
    for route_id in ALL_ROUTE_IDS:
        idx = compute_route_weather_risk(route_id)
        assert idx.primary_risk_type in allowed, route_id


# ── compute_route_weather_risk: documented formula ──────────────────────────


def test_composite_score_matches_documented_mean_formula() -> None:
    """score == mean over events of prob * risk_level_score * type_severity, capped at 1.

    Re-derives the score from the published _RISK_LEVEL_SCORE / _RISK_TYPE_SEVERITY
    tables to confirm the formula in the docstring is the formula in the code.
    """
    from processing.weather_risk import (
        _RISK_LEVEL_SCORE,
        _RISK_TYPE_SEVERITY,
        _get_events_for_route,
    )

    for route_id in ALL_ROUTE_IDS:
        events = _get_events_for_route(route_id)
        if not events:
            continue
        accum = 0.0
        for ev in events:
            accum += (
                (ev.probability_pct / 100.0)
                * _RISK_LEVEL_SCORE[ev.current_risk_level]
                * _RISK_TYPE_SEVERITY[ev.risk_type]
            )
        expected = min(1.0, accum / len(events))
        idx = compute_route_weather_risk(route_id)
        assert idx.current_risk_score == pytest.approx(expected, abs=1e-4), route_id


def test_historical_frequency_is_average_event_probability() -> None:
    """historical_disruption_frequency_pct == mean of event probabilities."""
    from processing.weather_risk import _get_events_for_route

    for route_id in ALL_ROUTE_IDS:
        events = _get_events_for_route(route_id)
        if not events:
            continue
        expected = sum(ev.probability_pct for ev in events) / len(events)
        idx = compute_route_weather_risk(route_id)
        assert idx.historical_disruption_frequency_pct == pytest.approx(
            expected, abs=0.1
        ), route_id


# ── compute_route_weather_risk: graceful degradation ────────────────────────


def test_unknown_route_yields_zero_index() -> None:
    """A route with no catalogue events returns an all-zero index, no crash."""
    idx = compute_route_weather_risk("no_such_route_xyz")
    assert idx.route_id == "no_such_route_xyz"
    assert idx.current_risk_score == 0.0
    assert idx.annualized_delay_days == 0.0
    assert idx.peak_risk_months == []
    assert idx.primary_risk_type == "NONE"
    assert idx.historical_disruption_frequency_pct == 0.0


def test_empty_string_route_yields_zero_index() -> None:
    idx = compute_route_weather_risk("")
    assert idx.current_risk_score == 0.0
    assert idx.primary_risk_type == "NONE"


# ── compute_route_weather_risk: determinism ─────────────────────────────────


def test_compute_route_weather_risk_is_deterministic() -> None:
    """The catalogue is static — repeated calls produce identical indices."""
    for route_id in ALL_ROUTE_IDS:
        a = compute_route_weather_risk(route_id)
        b = compute_route_weather_risk(route_id)
        assert a == b, route_id


# ── get_current_season_alerts ───────────────────────────────────────────────


def test_season_alerts_return_weather_risk_events() -> None:
    alerts = get_current_season_alerts()
    assert isinstance(alerts, list)
    assert all(isinstance(ev, WeatherRiskEvent) for ev in alerts)


def test_season_alerts_exclude_low_risk_and_off_season_events() -> None:
    """Every returned alert is in-season for the current month and not LOW."""
    import datetime

    month = datetime.date.today().month
    alerts = get_current_season_alerts()
    for ev in alerts:
        assert ev.current_risk_level != "LOW", ev.event_name
        assert month in ev.season_months, ev.event_name


def test_season_alerts_sorted_by_severity_then_probability() -> None:
    """Alerts are ordered ACTIVE→ELEVATED→SEASONAL, ties broken by probability desc."""
    order = {"ACTIVE": 0, "ELEVATED": 1, "SEASONAL": 2, "LOW": 3}
    alerts = get_current_season_alerts()
    keys = [(order[ev.current_risk_level], -ev.probability_pct) for ev in alerts]
    assert keys == sorted(keys)


def test_season_alerts_is_deterministic() -> None:
    assert get_current_season_alerts() == get_current_season_alerts()


# ── compute_weather_adjusted_eta ────────────────────────────────────────────


def test_weather_adjusted_eta_returns_pair_of_floats() -> None:
    expected, worst = compute_weather_adjusted_eta("transpacific_eb", 14.0)
    assert isinstance(expected, float)
    assert isinstance(worst, float)


def test_weather_adjusted_eta_never_below_nominal() -> None:
    """Weather can only add delay — expected and worst-case stay >= nominal."""
    nominal = 20.0
    for route_id in ALL_ROUTE_IDS:
        expected, worst = compute_weather_adjusted_eta(route_id, nominal)
        assert expected >= nominal, route_id
        assert worst >= nominal, route_id


def test_weather_adjusted_eta_worst_case_at_least_expected() -> None:
    """The worst-case ETA must never be optimistic relative to the expected ETA."""
    for route_id in ALL_ROUTE_IDS:
        expected, worst = compute_weather_adjusted_eta(route_id, 18.0)
        assert worst >= expected, route_id


def test_weather_adjusted_eta_unknown_route_falls_back_to_nominal_band() -> None:
    """An unknown route has no events: expected == nominal, worst == 1.15x nominal."""
    expected, worst = compute_weather_adjusted_eta("no_such_route_xyz", 30.0)
    assert expected == pytest.approx(30.0, abs=1e-9)
    assert worst == pytest.approx(34.5, abs=1e-9)


def test_weather_adjusted_eta_scales_with_nominal_input() -> None:
    """A larger nominal transit produces a larger (or equal) adjusted ETA."""
    for route_id in ALL_ROUTE_IDS:
        small_exp, small_wc = compute_weather_adjusted_eta(route_id, 10.0)
        big_exp, big_wc = compute_weather_adjusted_eta(route_id, 40.0)
        assert big_exp >= small_exp, route_id
        assert big_wc >= small_wc, route_id


def test_weather_adjusted_eta_is_deterministic() -> None:
    for route_id in ALL_ROUTE_IDS:
        assert compute_weather_adjusted_eta(route_id, 14.0) == \
            compute_weather_adjusted_eta(route_id, 14.0)


# ── get_nominal_transit_days ────────────────────────────────────────────────


def test_nominal_transit_days_positive_for_tracked_routes() -> None:
    for route_id in ALL_ROUTE_IDS:
        days = get_nominal_transit_days(route_id)
        assert isinstance(days, int)
        assert days > 0, route_id


def test_nominal_transit_days_unknown_route_uses_default() -> None:
    """An unknown route falls back to the documented 20-day default."""
    assert get_nominal_transit_days("no_such_route_xyz") == 20
