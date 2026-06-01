"""Pure-function tests for engine.disruption_explainer.

The explainer is template-based, deterministic, and lazy-imported only by the
UI (no live network calls). These tests pin:

* Template selection by severity band — Calm vs Severe headlines differ
  in the right keywords.
* Headline subject reflects the dominant component value, not a hardcoded
  string.
* Bullet count stays in [2, 4].
* Recommended-focus thresholds bucket correctly.
* Voyage primary-cause attribution maps to the right field.
* NEVER raises — None / garbage input → safe defaults, not a traceback.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from engine.disruption_explainer import (
    RouteExplanation,
    VoyageExplanation,
    explain_delayed_voyages,
    explain_route,
    explain_top_disruptions,
    explain_voyage,
)


# ── Lightweight fakes — independent of platform dataclasses so the test
# locks the contract by attribute names not by import structure.
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class _FakeRouteStress:
    route_id: str
    route_name: str
    stress_score: float
    chokepoint_stress: float = 0.0
    congestion_stress: float = 0.0
    weather_stress: float = 0.0
    rate_stress: float = 0.0
    vulnerability: float = 0.0
    anomaly_stress: float = 0.0
    dominant_driver: str = ""
    affected_chokepoints: list = field(default_factory=list)
    delayed_voyage_count: int = 0


@dataclass
class _FakeVoyage:
    voyage_id: str = "VY-TEST-01"
    vessel_name: str = "EVERGREEN STAR"
    route_id: str = "asia_europe"
    status: str = "Major Delay"
    delay_days: float = 5.0
    weather_delay_days: float = 0.0
    congestion_at_dest: float = 0.4
    chokepoints_on_route: list = field(default_factory=list)


# ── explain_route — headlines ───────────────────────────────────────────────


def test_explain_route_calm_headline_mentions_normally() -> None:
    rs = _FakeRouteStress(
        route_id="dover_hop",
        route_name="Dover Hop",
        stress_score=0.10,
        chokepoint_stress=0.05,
        congestion_stress=0.10,
    )
    out = explain_route(rs)
    assert isinstance(out, RouteExplanation)
    assert "normally" in out.headline.lower()
    assert out.severity_band == "Calm"


def test_explain_route_severe_headline_mentions_severely() -> None:
    rs = _FakeRouteStress(
        route_id="asia_europe",
        route_name="Asia-Europe",
        stress_score=0.85,
        chokepoint_stress=0.95,
        congestion_stress=0.70,
        rate_stress=0.80,
    )
    out = explain_route(rs)
    assert "severely" in out.headline.lower()
    assert out.severity_band == "Severe"


def test_explain_route_headline_includes_dominant_component_label() -> None:
    """The headline {top} placeholder must resolve to a human-readable
    component label, not the raw key."""
    rs = _FakeRouteStress(
        route_id="asia_europe",
        route_name="Asia-Europe",
        stress_score=0.55,
        congestion_stress=0.90,  # dominant
        chokepoint_stress=0.10,
    )
    out = explain_route(rs)
    assert out.severity_band == "Stressed"
    assert "congestion" in out.headline.lower()


# ── explain_route — bullets & body ─────────────────────────────────────────


def test_explain_route_why_has_2_to_4_bullets() -> None:
    rs = _FakeRouteStress(
        route_id="r",
        route_name="R",
        stress_score=0.6,
        chokepoint_stress=0.5,
        congestion_stress=0.4,
        weather_stress=0.3,
        rate_stress=0.2,
        vulnerability=0.1,
        anomaly_stress=0.0,
    )
    out = explain_route(rs)
    assert 2 <= len(out.why) <= 4


def test_explain_route_top_bullet_chokepoint_when_chokepoint_dominant() -> None:
    rs = _FakeRouteStress(
        route_id="asia_europe",
        route_name="Asia-Europe",
        stress_score=0.7,
        chokepoint_stress=1.0,
        congestion_stress=0.2,
        affected_chokepoints=["Suez Canal", "Bab-el-Mandeb"],
    )
    out = explain_route(rs)
    # The top bullet should reference chokepoints.
    assert any("chokepoint" in b.lower() for b in out.why[:1])


def test_explain_route_top_bullet_congestion_when_congestion_dominant() -> None:
    rs = _FakeRouteStress(
        route_id="transpacific_eb",
        route_name="Trans-Pacific Eastbound",
        stress_score=0.6,
        congestion_stress=1.0,
        chokepoint_stress=0.1,
        delayed_voyage_count=7,
    )
    out = explain_route(rs)
    assert any("congestion" in b.lower() for b in out.why[:1])


# ── explain_route — recommended_focus ─────────────────────────────────────


def test_explain_route_recommended_focus_escalate_for_high_score() -> None:
    rs = _FakeRouteStress(
        route_id="r", route_name="R", stress_score=0.80, chokepoint_stress=0.9
    )
    out = explain_route(rs)
    assert out.recommended_focus == "escalate"


def test_explain_route_recommended_focus_monitor_for_low_band() -> None:
    rs = _FakeRouteStress(
        route_id="r", route_name="R", stress_score=0.35, chokepoint_stress=0.3
    )
    out = explain_route(rs)
    assert out.recommended_focus == "monitor"


def test_explain_route_recommended_focus_empty_for_calm() -> None:
    rs = _FakeRouteStress(
        route_id="r", route_name="R", stress_score=0.10, chokepoint_stress=0.1
    )
    out = explain_route(rs)
    assert out.recommended_focus == ""


def test_explain_route_recommended_focus_investigate_for_middle_band() -> None:
    """0.5 < score <= 0.7 should bucket to 'investigate'."""
    rs = _FakeRouteStress(
        route_id="r", route_name="R", stress_score=0.60, chokepoint_stress=0.6
    )
    out = explain_route(rs)
    assert out.recommended_focus == "investigate"


# ── explain_voyage ─────────────────────────────────────────────────────────


def test_explain_voyage_on_schedule_primary_cause_none() -> None:
    v = _FakeVoyage(status="On Schedule", delay_days=0.5)
    out = explain_voyage(v)
    assert isinstance(out, VoyageExplanation)
    assert out.primary_cause == "none"
    assert "schedule" in out.headline.lower()


def test_explain_voyage_weather_primary_cause_when_weather_delay() -> None:
    v = _FakeVoyage(
        status="Major Delay", delay_days=5.0, weather_delay_days=3.0
    )
    out = explain_voyage(v)
    assert out.primary_cause == "weather"
    assert "weather" in out.headline.lower()


def test_explain_voyage_congestion_primary_cause_when_high_dest_congestion() -> None:
    v = _FakeVoyage(
        status="Major Delay",
        delay_days=4.0,
        weather_delay_days=0.0,
        congestion_at_dest=0.85,
    )
    out = explain_voyage(v)
    assert out.primary_cause == "congestion"


def test_explain_voyage_chokepoint_primary_cause_when_chokepoints_on_route() -> None:
    v = _FakeVoyage(
        status="Major Delay",
        delay_days=8.0,
        weather_delay_days=0.0,
        congestion_at_dest=0.4,
        chokepoints_on_route=["Suez Canal"],
    )
    out = explain_voyage(v)
    assert out.primary_cause == "chokepoint"


# ── explain_top_disruptions ────────────────────────────────────────────────


def test_explain_top_disruptions_filters_calm() -> None:
    """Calm routes (score < 0.25) must not appear in the disruption list."""
    routes = [
        _FakeRouteStress("a", "A", 0.10),
        _FakeRouteStress("b", "B", 0.60, chokepoint_stress=0.7),
        _FakeRouteStress("c", "C", 0.05),
        _FakeRouteStress("d", "D", 0.40, congestion_stress=0.5),
    ]
    out = explain_top_disruptions(routes, top_n=5)
    ids = [e.route_id for e in out]
    assert "a" not in ids
    assert "c" not in ids
    assert "b" in ids
    assert "d" in ids


def test_explain_top_disruptions_sorted_desc_by_score() -> None:
    routes = [
        _FakeRouteStress("low", "L", 0.30, chokepoint_stress=0.3),
        _FakeRouteStress("high", "H", 0.80, chokepoint_stress=0.9),
        _FakeRouteStress("mid", "M", 0.55, congestion_stress=0.6),
    ]
    out = explain_top_disruptions(routes, top_n=5)
    ids = [e.route_id for e in out]
    assert ids == ["high", "mid", "low"]


def test_explain_top_disruptions_caps_at_top_n() -> None:
    routes = [
        _FakeRouteStress(f"r{i}", f"R{i}", 0.6, chokepoint_stress=0.7)
        for i in range(10)
    ]
    out = explain_top_disruptions(routes, top_n=3)
    assert len(out) == 3


# ── Safety contracts — NEVER raise ─────────────────────────────────────────


def test_explain_route_never_raises_on_none() -> None:
    out = explain_route(None)
    assert isinstance(out, RouteExplanation)
    assert out.route_id == ""


def test_explain_route_never_raises_on_garbage_attrs() -> None:
    """Object with attributes of the wrong type — explain_route must not raise."""
    class Junk:
        route_id = 12345                  # not a string
        route_name = None
        stress_score = "not-a-number"
        chokepoint_stress = object()
        congestion_stress = None
        weather_stress = None
        rate_stress = None
        vulnerability = None
        anomaly_stress = None
        affected_chokepoints = None
        delayed_voyage_count = "lots"

    out = explain_route(Junk())
    assert isinstance(out, RouteExplanation)


def test_explain_voyage_never_raises_on_none() -> None:
    out = explain_voyage(None)
    assert isinstance(out, VoyageExplanation)
    assert out.voyage_id == ""


def test_explain_voyage_never_raises_on_garbage_attrs() -> None:
    class Junk:
        voyage_id = 99
        vessel_name = None
        route_id = None
        status = None
        delay_days = "huge"
        weather_delay_days = None
        congestion_at_dest = None
        chokepoints_on_route = None

    out = explain_voyage(Junk())
    assert isinstance(out, VoyageExplanation)


# ── top-N empties & determinism ────────────────────────────────────────────


def test_explain_top_disruptions_empty_returns_empty_list() -> None:
    assert explain_top_disruptions(None) == []
    assert explain_top_disruptions([]) == []


def test_explain_delayed_voyages_filters_on_time() -> None:
    """Voyages with delay <= 1.0 day should be filtered out — those are
    On-Schedule, not delayed."""
    voyages = [
        _FakeVoyage(voyage_id="ontime", status="On Schedule", delay_days=0.5),
        _FakeVoyage(voyage_id="minor", status="Minor Delay", delay_days=2.5),
        _FakeVoyage(voyage_id="major", status="Major Delay", delay_days=8.0),
    ]
    out = explain_delayed_voyages(voyages, top_n=5)
    ids = [e.voyage_id for e in out]
    assert "ontime" not in ids
    assert "minor" in ids
    assert "major" in ids
    # Most-delayed first.
    assert ids[0] == "major"
