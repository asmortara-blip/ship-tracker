"""Tests for engine.scorer — InsightScorer.

Covers:
  - InsightScorer construction (defaults + custom cfg)
  - score_all empty-input handling
  - Port-insight thresholds (high vs low demand)
  - Route-insight threshold (opportunity_score >= min_score)
  - Output sorting (descending by score)
  - Output cap (max 20 insights)
  - Deduplication by title

Stand-in dataclasses mirror the attributes the scorer reads off the
real PortDemandResult / RouteOpportunity types. We don't need the full
types — just the fields actually accessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from engine.insight import Insight
from engine.scorer import InsightScorer


# ─── Stand-in dataclasses ──────────────────────────────────────────────────

@dataclass
class _FakePort:
    """Mirrors the fields InsightScorer._port_insight() reads."""
    locode: str
    port_name: str = "Some Port"
    region: str = "Asia East"
    has_real_data: bool = True
    demand_score: float = 0.5
    demand_trend: str = "Stable"
    trade_flow_component: float = 0.5
    congestion_component: float = 0.5
    throughput_component: float = 0.5
    import_value_usd: float = 1_000_000_000
    vessel_count: int = 50
    throughput_teu_m: float = 10.0
    top_products: list[dict] = field(default_factory=lambda: [{"category": "electronics"}])


@dataclass
class _FakeRoute:
    """Mirrors the fields InsightScorer._route_insight() reads."""
    route_id: str
    route_name: str = "Some Route"
    origin_locode: str = "CNSHA"
    dest_locode: str = "USLAX"
    opportunity_score: float = 0.5
    opportunity_label: str = "Moderate"
    rationale: str = "Some rationale"
    current_rate_usd_feu: float = 2500.0
    rate_pct_change_30d: float = 0.05
    rate_momentum_component: float = 0.5
    demand_imbalance_component: float = 0.5
    congestion_clearance_component: float = 0.5
    macro_tailwind_component: float = 0.5
    dest_demand_score: float = 0.5
    origin_congestion: float = 0.5


# ─── Construction ──────────────────────────────────────────────────────────

def test_scorer_default_construction() -> None:
    s = InsightScorer()
    assert s.high_threshold == 0.70
    assert s.low_threshold == 0.35
    assert s.min_score == 0.55


def test_scorer_construction_with_none_cfg() -> None:
    """None cfg falls through to {} → all defaults."""
    s = InsightScorer(None)
    assert s.high_threshold == 0.70


def test_scorer_custom_thresholds() -> None:
    cfg = {
        "engine": {
            "high_demand_threshold": 0.80,
            "low_demand_threshold": 0.25,
            "insight_min_score": 0.65,
        },
    }
    s = InsightScorer(cfg)
    assert s.high_threshold == 0.80
    assert s.low_threshold == 0.25
    assert s.min_score == 0.65


def test_scorer_partial_cfg_uses_defaults_for_missing() -> None:
    cfg = {"engine": {"high_demand_threshold": 0.85}}
    s = InsightScorer(cfg)
    assert s.high_threshold == 0.85
    assert s.low_threshold == 0.35    # default
    assert s.min_score == 0.55        # default


# ─── score_all: empty inputs ───────────────────────────────────────────────

def test_score_all_empty_inputs_returns_empty_or_just_macro() -> None:
    """Empty port + route + macro. The result is usually empty (no macro
    insight either since macro_data is empty) — but at most the macro
    insight may surface depending on its own threshold logic."""
    s = InsightScorer()
    out = s.score_all([], [], {})
    assert isinstance(out, list)
    assert len(out) <= 1   # at most a macro insight
    for ins in out:
        assert isinstance(ins, Insight)


# ─── Port-insight thresholds ───────────────────────────────────────────────

def test_score_all_emits_high_demand_port_insight() -> None:
    """A port with demand_score >= high_threshold gets a HIGH insight."""
    s = InsightScorer()
    ports = [_FakePort(locode="USLAX", port_name="LA",
                      demand_score=0.80, region="North America West")]
    out = s.score_all(ports, [], {})
    port_insights = [i for i in out if i.category == "PORT_DEMAND"]
    assert len(port_insights) == 1
    assert port_insights[0].score == pytest.approx(0.80)
    assert "High demand" in port_insights[0].title


def test_score_all_emits_low_demand_port_insight() -> None:
    """A port with demand_score <= low_threshold gets a LOW insight.
    The score is reported as (1 - demand_score) — the conviction in the
    'slack capacity' read."""
    s = InsightScorer()
    ports = [_FakePort(locode="NLRTM", port_name="Rotterdam",
                      demand_score=0.20, region="Europe")]
    out = s.score_all(ports, [], {})
    port_insights = [i for i in out if i.category == "PORT_DEMAND"]
    assert len(port_insights) == 1
    assert port_insights[0].score == pytest.approx(0.80)   # 1 - 0.20
    assert "Low demand" in port_insights[0].title


def test_score_all_skips_mid_range_demand() -> None:
    """A port in the middle band (0.35 < demand < 0.70) gets no insight."""
    s = InsightScorer()
    ports = [_FakePort(locode="USNYC", demand_score=0.55)]
    out = s.score_all(ports, [], {})
    assert all(i.category != "PORT_DEMAND" for i in out)


def test_score_all_skips_port_with_no_real_data() -> None:
    """Ports lacking real data → _port_insight returns None."""
    s = InsightScorer()
    ports = [_FakePort(locode="USLAX", demand_score=0.85, has_real_data=False)]
    out = s.score_all(ports, [], {})
    assert all(i.category != "PORT_DEMAND" for i in out)


# ─── Route-insight threshold ───────────────────────────────────────────────

def test_score_all_emits_route_insight_above_min_score() -> None:
    """A route with opportunity_score >= min_score (default 0.55)
    produces a ROUTE insight."""
    s = InsightScorer()
    routes = [_FakeRoute(
        route_id="transpacific_eb", route_name="Trans-Pacific Eastbound",
        opportunity_score=0.75,
    )]
    out = s.score_all([], routes, {})
    route_insights = [i for i in out if i.category == "ROUTE"]
    assert len(route_insights) == 1
    assert route_insights[0].score == pytest.approx(0.75)


def test_score_all_skips_route_below_min_score() -> None:
    s = InsightScorer()
    routes = [_FakeRoute(route_id="r1", opportunity_score=0.40)]
    out = s.score_all([], routes, {})
    assert all(i.category != "ROUTE" for i in out)


# ─── Sorting + cap ─────────────────────────────────────────────────────────

def test_score_all_results_sorted_by_score_desc() -> None:
    """Multiple insights — output sorted descending by score."""
    s = InsightScorer()
    ports = [
        _FakePort(locode="USLAX", port_name="LA", demand_score=0.95,
                 region="North America West"),
        _FakePort(locode="USNYC", port_name="NYC", demand_score=0.75,
                 region="North America East"),
        _FakePort(locode="NLRTM", port_name="Rotterdam", demand_score=0.15,
                 region="Europe"),
    ]
    out = s.score_all(ports, [], {})
    scores = [i.score for i in out]
    assert scores == sorted(scores, reverse=True)


def test_score_all_caps_at_20_insights() -> None:
    """With 30 high-demand ports the output is capped at 20."""
    s = InsightScorer()
    ports = [
        _FakePort(locode=f"P{i:03d}", port_name=f"Port {i}",
                 demand_score=0.95)
        for i in range(30)
    ]
    out = s.score_all(ports, [], {})
    assert len(out) <= 20


# ─── Deduplication ─────────────────────────────────────────────────────────

def test_score_all_dedupes_by_title() -> None:
    """If two distinct upstream inputs produce the same title (e.g., the
    convergence detector and the port detector both flag the same port),
    only one copy survives."""
    s = InsightScorer()
    # Two identical ports → identical titles → dedup keeps one.
    ports = [
        _FakePort(locode="USLAX", port_name="Same Port Name",
                 demand_score=0.85),
        _FakePort(locode="USLAX", port_name="Same Port Name",
                 demand_score=0.85),
    ]
    out = s.score_all(ports, [], {})
    titles = [i.title for i in out]
    assert len(titles) == len(set(titles))


# ─── Output shape ──────────────────────────────────────────────────────────

def test_score_all_returns_list_of_insight() -> None:
    s = InsightScorer()
    ports = [_FakePort(locode="USLAX", demand_score=0.85)]
    out = s.score_all(ports, [], {})
    for ins in out:
        assert isinstance(ins, Insight)
        assert ins.score == pytest.approx(0.85) or 0.0 <= ins.score <= 1.0
        assert ins.category in {"PORT_DEMAND", "ROUTE", "MACRO", "CONVERGENCE"}
