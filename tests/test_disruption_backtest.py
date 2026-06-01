"""Pure-function tests for processing.disruption_backtest.

The backtester is honest about its synthetic-but-event-aware methodology: we
test that SSI inputs *shaped like* a historical event drive a stressed
per-route SSI for that event's affected routes. We do NOT test real historical
replay; the tests below pin the math, not the calendar.

Key invariants the suite locks in:

* ``synthesize_event_inputs`` builds chokepoint disruption keyed on the event's
  ``affected_chokepoints`` (Suez → ``chokepoint_disruption['suez']`` non-zero;
  Panama → ``chokepoint_disruption['panama']`` non-zero).
* ``backtest_event`` detects ``Stressed`` band on every well-formed event in the
  registry and reports the right dominant component.
* Bounds: ``max_score_in_window`` in [0, 1], ``hit_rate`` / ``early_rate`` in
  [0, 1], ``per_component_contribution`` sums to a sensible total.
* Determinism: same call → same result.
* NEVER raises on bad input.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from data.historical_events import EVENTS, EVENTS_BY_ID, HistoricalEvent
from processing.disruption_backtest import (
    BacktestResult,
    BacktestSummary,
    backtest_all_events,
    backtest_event,
    synthesize_event_inputs,
)
from processing.shipping_stress_index import COMPONENT_WEIGHTS


# ── synthesize_event_inputs ────────────────────────────────────────────────


def test_synthesize_suez_elevates_suez_chokepoint() -> None:
    """The Suez 2021 event names suez in affected_chokepoints — the synthesis
    must echo that into chokepoint_disruption[suez] as a non-empty severity."""
    event = EVENTS_BY_ID["suez_2021"]
    bundle = synthesize_event_inputs(event)
    assert "suez" in bundle["chokepoint_disruption"]
    assert bundle["chokepoint_disruption"]["suez"]  # non-empty severity label


def test_synthesize_panama_elevates_panama_chokepoint() -> None:
    """Same shape, different chokepoint — panama drought 2023."""
    event = EVENTS_BY_ID["panama_drought_2023"]
    bundle = synthesize_event_inputs(event)
    assert "panama" in bundle["chokepoint_disruption"]
    assert bundle["chokepoint_disruption"]["panama"]


def test_synthesize_covid_has_no_chokepoint_but_has_port_results() -> None:
    """COVID 2020 is a demand shock, not a chokepoint event — affected_chokepoints
    is empty, but every affected route's destination port should appear in
    port_results so the SSI's congestion component still fires."""
    event = EVENTS_BY_ID["covid_2020"]
    bundle = synthesize_event_inputs(event)
    assert bundle["chokepoint_disruption"] == {}
    assert len(bundle["port_results"]) > 0


def test_synthesize_none_event_returns_empty_bundle() -> None:
    """A None event must not raise — must yield a valid empty bundle."""
    bundle = synthesize_event_inputs(None)
    assert bundle["chokepoint_disruption"] == {}
    assert bundle["port_results"] == []
    assert bundle["freight_data"] == {}
    assert bundle["weather_alerts"] == []


def test_synthesize_freight_data_shape() -> None:
    """Affected routes must appear as freight_data entries with at least a
    30-point rate_usd_per_feu series — that's what the SSI's rate component
    consumes. Synthesis produces pandas DataFrames when pandas is
    available (the normal case) and falls back to list-of-dicts otherwise."""
    event = EVENTS_BY_ID["suez_2021"]
    bundle = synthesize_event_inputs(event)
    # asia_europe is an affected route AND a registered route, so it should
    # appear with a 60-point series.
    series = bundle["freight_data"].get("asia_europe")
    assert series is not None
    # Tolerate either shape: DataFrame (real path) or list-of-dicts (fallback).
    if hasattr(series, "columns"):
        assert "rate_usd_per_feu" in series.columns
        assert len(series) >= 30
    else:
        assert isinstance(series, list)
        assert len(series) >= 30
        assert all("rate_usd_per_feu" in row for row in series)


# ── backtest_event ──────────────────────────────────────────────────────────


def test_backtest_suez_2021_detected_chokepoint_dominant() -> None:
    """Suez 2021 is the canonical chokepoint event — the SSI must light up
    its chokepoint component on the affected routes."""
    event = EVENTS_BY_ID["suez_2021"]
    result = backtest_event(event)
    assert isinstance(result, BacktestResult)
    assert result.detected, (
        f"Expected detected=True for Suez 2021 with default Stressed threshold; "
        f"got max_score={result.max_score_in_window}, band={result.detection_band}"
    )
    assert result.dominant_component == "chokepoint"


def test_backtest_panama_drought_2023_detected_chokepoint_dominant() -> None:
    """Panama drought event mirror — chokepoint component must dominate."""
    event = EVENTS_BY_ID["panama_drought_2023"]
    result = backtest_event(event)
    assert result.detected
    assert result.dominant_component == "chokepoint"


def test_backtest_covid_2020_detected_many_routes() -> None:
    """COVID 2020 names many affected routes — the per_route_scores dict
    should reflect that breadth even though no chokepoint is involved."""
    event = EVENTS_BY_ID["covid_2020"]
    result = backtest_event(event)
    assert result.detected
    # Many routes synthesised; at least 5 should show up as per-route scores.
    assert len(result.per_route_scores) >= 5


def test_backtest_event_never_raises_on_garbage_event() -> None:
    """A malformed event object must downgrade to detected=False, not raise."""
    @dataclass
    class _Junk:
        event_id: str = "junk"
        name: str = "junk"
        start_date: str = "not-a-date"
        severity: str = "moderate"
        affected_routes: list = field(default_factory=list)
        affected_chokepoints: list = field(default_factory=list)
        expected_ssi_band: str = "Stressed"
        expected_lead_time_days: int = 0

    result = backtest_event(_Junk())
    assert isinstance(result, BacktestResult)
    assert result.detected is False
    assert 0.0 <= result.max_score_in_window <= 1.0


def test_backtest_event_with_narrow_window_still_works() -> None:
    """evaluation_window_days bounds lead_time_days, but does not break
    detection — a 1-day window still returns a valid result."""
    event = EVENTS_BY_ID["suez_2021"]
    result = backtest_event(event, evaluation_window_days=1)
    assert isinstance(result, BacktestResult)
    # Lead-time credit is capped at the window.
    assert result.lead_time_days <= 1


def test_backtest_event_max_score_in_unit_interval() -> None:
    """For every event, max_score_in_window must stay in [0, 1]."""
    for event in EVENTS:
        r = backtest_event(event)
        assert 0.0 <= r.max_score_in_window <= 1.0, event.event_id


def test_backtest_event_severe_threshold_stricter() -> None:
    """A Severe threshold detects strictly fewer events than Stressed —
    a stricter threshold cannot detect more."""
    n_stressed = sum(
        backtest_event(e, threshold_band="Stressed").detected for e in EVENTS
    )
    n_severe = sum(
        backtest_event(e, threshold_band="Severe").detected for e in EVENTS
    )
    assert n_severe <= n_stressed


def test_backtest_event_none_returns_empty_result() -> None:
    """None event yields a valid no-data BacktestResult."""
    r = backtest_event(None)
    assert isinstance(r, BacktestResult)
    assert r.detected is False
    assert r.event_id == ""


def test_backtest_event_lead_time_zero_when_undetected() -> None:
    """When not detected, lead_time_days must be 0 (we cannot credit a lead
    time for a non-detection)."""
    @dataclass
    class _NotDetectable:
        event_id: str = "tiny"
        name: str = "tiny"
        start_date: str = "2025-01-01"
        severity: str = "moderate"
        affected_routes: list = field(default_factory=list)
        affected_chokepoints: list = field(default_factory=list)
        expected_ssi_band: str = "Stressed"
        expected_lead_time_days: int = 7

    r = backtest_event(_NotDetectable())
    assert r.detected is False
    assert r.lead_time_days == 0


def test_backtest_event_deterministic() -> None:
    """Same call → identical result. Locks the SSI's determinism contract
    through the backtest path."""
    event = EVENTS_BY_ID["suez_2021"]
    a = backtest_event(event)
    b = backtest_event(event)
    assert a.detected == b.detected
    assert a.max_score_in_window == b.max_score_in_window
    assert a.dominant_component == b.dominant_component
    assert a.per_route_scores == b.per_route_scores


def test_backtest_event_restores_chokepoint_state() -> None:
    """The context-managed chokepoint elevation MUST restore original state
    even after a successful backtest — otherwise the test order would
    contaminate other tests via module-state leakage."""
    from processing.chokepoint_analyzer import CHOKEPOINTS

    before_level = CHOKEPOINTS["suez"].current_risk_level
    before_type = CHOKEPOINTS["suez"].current_disruption_type

    _ = backtest_event(EVENTS_BY_ID["suez_2021"])

    assert CHOKEPOINTS["suez"].current_risk_level == before_level
    assert CHOKEPOINTS["suez"].current_disruption_type == before_type


# ── backtest_all_events ─────────────────────────────────────────────────────


def test_backtest_all_events_summary_shape() -> None:
    summary = backtest_all_events()
    assert isinstance(summary, BacktestSummary)
    assert summary.total_events == len(EVENTS)
    assert 0.0 <= summary.hit_rate <= 1.0
    assert 0.0 <= summary.early_rate <= 1.0


def test_backtest_all_events_empty_list_zero_counts() -> None:
    summary = backtest_all_events(events=[])
    assert summary.total_events == 0
    assert summary.detected == 0
    assert summary.early == 0
    assert summary.hit_rate == 0.0
    assert summary.early_rate == 0.0
    assert summary.mean_lead_time_days == 0.0


def test_backtest_all_events_per_component_keys_match_weights() -> None:
    """per_component_contribution must carry every key the SSI weights have,
    so a downstream consumer can render it next to the weights without
    handling missing keys."""
    summary = backtest_all_events()
    assert set(summary.per_component_contribution) == set(COMPONENT_WEIGHTS)


def test_backtest_all_events_per_component_values_sum_to_at_most_one() -> None:
    """Each result contributes 1.0 to its dominant component's tally, and the
    tally is divided by total_events. The sum across components should be
    exactly 1.0 (every result has exactly one dominant component) — or 0.0
    when there are zero events. Floating-point tolerance allowed."""
    summary = backtest_all_events()
    total = sum(summary.per_component_contribution.values())
    # When every event has a dominant component, the sum is 1.0; if some
    # results have an empty dominant_component (e.g. no per_route_scores),
    # the sum is < 1.0 but never > 1.0.
    assert 0.0 <= total <= 1.0 + 1e-9


def test_backtest_all_events_mean_lead_time_non_negative_when_detected() -> None:
    """When at least one event detects, the mean lead time across events is
    non-negative (detected lead-times are >= 0 and undetected are 0)."""
    summary = backtest_all_events()
    if summary.detected > 0:
        assert summary.mean_lead_time_days >= 0.0


def test_backtest_all_events_results_match_total() -> None:
    summary = backtest_all_events()
    assert len(summary.results) == summary.total_events


def test_backtest_all_events_threshold_severe_stricter() -> None:
    """The Severe threshold must produce a hit_rate <= the Stressed hit_rate."""
    a = backtest_all_events(threshold_band="Stressed")
    b = backtest_all_events(threshold_band="Severe")
    assert b.hit_rate <= a.hit_rate
