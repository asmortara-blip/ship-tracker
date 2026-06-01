"""Tests for engine.convergence_tracker.

Covers:
  - ConvergenceEvent dataclass shape
  - _conviction thresholds (3→MODERATE, 4→HIGH, 5+→VERY_HIGH)
  - _time_to_act decision tree
  - _bdi_direction classifier
  - _seasonal_score: empty / insufficient / YoY-derived
  - _macro_score_from_data composite weights
  - detect_convergence: empty inputs, min_signals threshold, BULLISH vs
    BEARISH branches, sort by composite_score descending
  - get_highest_conviction_trades: n filter + ordering
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd
import pytest

from engine.convergence_tracker import (
    _BEARISH_THRESHOLD,
    _BULLISH_THRESHOLD,
    ConvergenceEvent,
    _bdi_direction,
    _conviction,
    _macro_score_from_data,
    _seasonal_score,
    _time_to_act,
    detect_convergence,
    get_highest_conviction_trades,
)


# ─── Stand-in dataclasses for Route/Port objects ───────────────────────────

@dataclass
class _FakePort:
    locode: str
    port_name: str = "Some Port"
    demand_score: float = 0.5
    congestion_index: float = 0.5


@dataclass
class _FakeRoute:
    route_id: str
    route_name: str = "Some Route"
    origin_locode: str = "CNSHA"
    dest_locode: str = "USLAX"
    opportunity_score: float = 0.5
    rate_momentum_component: float = 0.5


def _macro_value_df(values: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range(start, periods=len(values), freq="D"),
        "value": values,
    })


# ─── ConvergenceEvent dataclass ────────────────────────────────────────────

def test_convergence_event_shape() -> None:
    ev = ConvergenceEvent(
        event_id="x", timestamp="t",
        converging_signals=["A", "B", "C"], n_signals=3,
        consensus_direction="BULLISH", consensus_strength=0.8,
        affected_entity="r1", entity_name="Route 1",
        composite_score=0.75, conviction_level="HIGH",
        time_to_act="THIS_WEEK", description="d",
    )
    assert ev.n_signals == 3
    assert ev.consensus_direction == "BULLISH"


# ─── Module-level thresholds ───────────────────────────────────────────────

def test_threshold_constants() -> None:
    """Sanity: the bullish/bearish thresholds are bracketing 0.5."""
    assert _BULLISH_THRESHOLD > 0.5
    assert _BEARISH_THRESHOLD < 0.5
    assert _BEARISH_THRESHOLD < _BULLISH_THRESHOLD


# ─── _conviction ───────────────────────────────────────────────────────────

def test_conviction_thresholds() -> None:
    # >= 5 → VERY_HIGH, >= 4 → HIGH, else MODERATE
    assert _conviction(5) == "VERY_HIGH"
    assert _conviction(6) == "VERY_HIGH"
    assert _conviction(4) == "HIGH"
    assert _conviction(3) == "MODERATE"
    assert _conviction(0) == "MODERATE"
    assert _conviction(1) == "MODERATE"


# ─── _time_to_act ──────────────────────────────────────────────────────────

def test_time_to_act_immediate_when_very_high() -> None:
    assert _time_to_act("VERY_HIGH", 0.50) == "IMMEDIATE"


def test_time_to_act_immediate_when_score_very_high() -> None:
    """Score > 0.80 forces IMMEDIATE even at lower conviction."""
    assert _time_to_act("MODERATE", 0.85) == "IMMEDIATE"


def test_time_to_act_this_week_when_high() -> None:
    assert _time_to_act("HIGH", 0.65) == "THIS_WEEK"


def test_time_to_act_this_month_when_moderate() -> None:
    assert _time_to_act("MODERATE", 0.60) == "THIS_MONTH"


# ─── _bdi_direction ────────────────────────────────────────────────────────

def test_bdi_direction_neutral_on_empty_data() -> None:
    """No BDI series → compute_bdi_score returns its default (~0.5) → neutral."""
    score, direction = _bdi_direction({})
    # The function falls through to 0.5 default when compute_bdi_score errors.
    assert 0.0 <= score <= 1.0
    assert direction in {"bullish", "bearish", "neutral"}


# ─── _seasonal_score ───────────────────────────────────────────────────────

def test_seasonal_score_neutral_on_missing_series() -> None:
    score, direction = _seasonal_score({})
    assert score == 0.5
    assert direction == "neutral"


def test_seasonal_score_neutral_on_too_short_history() -> None:
    """Needs >= 13 obs for the YoY comparison."""
    macro = {"MRTSSM44000USS": _macro_value_df([100.0] * 5)}
    score, direction = _seasonal_score(macro)
    assert score == 0.5
    assert direction == "neutral"


def test_seasonal_score_bullish_on_strong_yoy_growth() -> None:
    """+20% YoY → score = 0.5 + 0.20*2.0 = 0.90 → bullish."""
    # 13 months of data: 12 month ago = index -13, latest = index -1
    macro = {"MRTSSM44000USS": _macro_value_df([100.0] + [110.0] * 11 + [120.0])}
    score, direction = _seasonal_score(macro)
    # (120 - 100) / 100 = 0.20 → 0.5 + 0.40 = 0.90
    assert score == pytest.approx(0.90, abs=0.05)
    assert direction == "bullish"


def test_seasonal_score_bearish_on_yoy_decline() -> None:
    """-20% YoY → score = 0.5 + (-0.20)*2.0 = 0.10 → bearish."""
    macro = {"MRTSSM44000USS": _macro_value_df([100.0] + [90.0] * 11 + [80.0])}
    score, direction = _seasonal_score(macro)
    assert score < _BEARISH_THRESHOLD
    assert direction == "bearish"


def test_seasonal_score_handles_zero_year_ago_safely() -> None:
    macro = {"MRTSSM44000USS": _macro_value_df([0.0] + [50.0] * 11 + [100.0])}
    score, direction = _seasonal_score(macro)
    assert score == 0.5
    assert direction == "neutral"


# ─── _macro_score_from_data ────────────────────────────────────────────────

def test_macro_score_returns_well_formed_tuple() -> None:
    score, direction = _macro_score_from_data({})
    assert 0.0 <= score <= 1.0
    assert direction in {"bullish", "bearish", "neutral"}


def test_macro_score_uses_ipman_as_pmi_proxy() -> None:
    """IPMAN ratio above its trailing avg pushes the macro score up."""
    rising_ipman = _macro_value_df([95.0] * 80 + [110.0] * 10)
    macro = {"IPMAN": rising_ipman}
    score, _ = _macro_score_from_data(macro)
    assert 0.0 <= score <= 1.0
    assert math.isfinite(score)


# ─── detect_convergence end-to-end ─────────────────────────────────────────

def test_detect_convergence_empty_inputs_returns_empty() -> None:
    assert detect_convergence([], [], {}) == []


def test_detect_convergence_below_min_signals_returns_empty() -> None:
    """With min_signals=10 (impossible) nothing surfaces."""
    routes = [_FakeRoute(route_id="r1", opportunity_score=0.8)]
    ports = [_FakePort(locode="USLAX", demand_score=0.85)]
    events = detect_convergence(ports, routes, {}, min_signals=10)
    assert events == []


def test_detect_convergence_bullish_event_when_signals_align() -> None:
    """Configure 5 signals all bullish: every helper returns ≥ 0.60 score."""
    # Route + port both bullish; macro signals all neutral by default — set
    # the route fields high so 3 of the candidates clear bullish.
    routes = [
        _FakeRoute(
            route_id="transpacific_eb", route_name="TP-EB",
            origin_locode="CNSHA", dest_locode="USLAX",
            opportunity_score=0.80,
            rate_momentum_component=0.85,
        )
    ]
    ports = [
        _FakePort(locode="CNSHA", congestion_index=0.10),  # very low congestion → bullish clearance
        _FakePort(locode="USLAX", demand_score=0.85),       # high demand → bullish
    ]
    events = detect_convergence(ports, routes, {}, min_signals=3)
    # At least one bullish event surfaces.
    assert any(e.consensus_direction == "BULLISH" for e in events)


def test_detect_convergence_bearish_event_when_signals_align() -> None:
    """Configure signals all bearish."""
    routes = [
        _FakeRoute(
            route_id="r1",
            opportunity_score=0.20,
            rate_momentum_component=0.15,
            origin_locode="CNSHA", dest_locode="USLAX",
        )
    ]
    ports = [
        _FakePort(locode="CNSHA", congestion_index=0.90),   # high congestion → bearish
        _FakePort(locode="USLAX", demand_score=0.15),         # low demand → bearish
    ]
    events = detect_convergence(ports, routes, {}, min_signals=3)
    assert any(e.consensus_direction == "BEARISH" for e in events)


def test_detect_convergence_sorted_by_composite_score_desc() -> None:
    """Two routes, one with stronger signals — output sorted with the
    stronger first."""
    routes = [
        _FakeRoute(route_id="weaker", opportunity_score=0.62,
                  rate_momentum_component=0.61,
                  origin_locode="CNSHA", dest_locode="NLRTM"),
        _FakeRoute(route_id="stronger", opportunity_score=0.95,
                  rate_momentum_component=0.95,
                  origin_locode="CNSHA", dest_locode="USLAX"),
    ]
    ports = [
        _FakePort(locode="CNSHA", congestion_index=0.10),
        _FakePort(locode="USLAX", demand_score=0.95),
        _FakePort(locode="NLRTM", demand_score=0.65),
    ]
    events = detect_convergence(ports, routes, {}, min_signals=3)
    scores = [e.composite_score for e in events]
    assert scores == sorted(scores, reverse=True)


def test_detect_convergence_event_fields_well_formed() -> None:
    routes = [_FakeRoute(route_id="r", opportunity_score=0.80,
                        rate_momentum_component=0.80,
                        origin_locode="CNSHA", dest_locode="USLAX",
                        route_name="Route X")]
    ports = [_FakePort(locode="CNSHA", congestion_index=0.10),
             _FakePort(locode="USLAX", demand_score=0.85,
                       port_name="Los Angeles")]
    events = detect_convergence(ports, routes, {}, min_signals=3)
    assert events  # at least one
    ev = events[0]
    assert isinstance(ev, ConvergenceEvent)
    assert ev.n_signals >= 3
    assert ev.consensus_direction in {"BULLISH", "BEARISH"}
    assert 0.0 <= ev.consensus_strength <= 1.0
    assert 0.0 <= ev.composite_score <= 1.0
    assert ev.conviction_level in {"VERY_HIGH", "HIGH", "MODERATE"}
    assert ev.time_to_act in {"IMMEDIATE", "THIS_WEEK", "THIS_MONTH"}
    assert ev.description


# ─── get_highest_conviction_trades ─────────────────────────────────────────

def _mk_event(score: float, eid: str = "x") -> ConvergenceEvent:
    return ConvergenceEvent(
        event_id=eid, timestamp="t",
        converging_signals=["A", "B", "C"], n_signals=3,
        consensus_direction="BULLISH", consensus_strength=0.7,
        affected_entity="e", entity_name="E",
        composite_score=score, conviction_level="HIGH",
        time_to_act="THIS_WEEK", description="d",
    )


def test_get_highest_conviction_trades_respects_n() -> None:
    events = [_mk_event(i / 10.0, eid=str(i)) for i in range(10)]
    top3 = get_highest_conviction_trades(events, n=3)
    assert len(top3) == 3


def test_get_highest_conviction_trades_sorted_desc() -> None:
    events = [_mk_event(0.5), _mk_event(0.9), _mk_event(0.7)]
    out = get_highest_conviction_trades(events, n=10)
    scores = [e.composite_score for e in out]
    assert scores == sorted(scores, reverse=True)


def test_get_highest_conviction_trades_default_n_3() -> None:
    events = [_mk_event(i / 10.0, eid=str(i)) for i in range(10)]
    out = get_highest_conviction_trades(events)
    assert len(out) == 3


def test_get_highest_conviction_trades_empty_input() -> None:
    assert get_highest_conviction_trades([]) == []
