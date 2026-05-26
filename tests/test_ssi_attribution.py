"""Defining-property tests for processing/ssi_attribution.py."""
from __future__ import annotations

import pytest

from processing.shipping_stress_index import (
    COMPONENT_WEIGHTS, RouteStress, ShippingStressReport,
)
from processing.ssi_attribution import (
    SSIAttributionReport,
    attribute_ssi,
)


# ── Fixture builder ─────────────────────────────────────────────────────


def _make_report(
    overall: float = 0.5,
    label: str = "Elevated",
    component_scores: dict | None = None,
    route_stress: list | None = None,
) -> ShippingStressReport:
    """Hand-built report with sensible defaults; tests override what they need."""
    return ShippingStressReport(
        overall_ssi=overall,
        ssi_label=label,
        ssi_color="#888",
        route_stress=route_stress or [],
        component_scores=component_scores or {k: 0.0 for k in COMPONENT_WEIGHTS},
    )


def _make_route(
    route_id: str = "transpacific_eb",
    route_name: str = "Trans-Pacific EB",
    stress_score: float = 0.5,
    dominant_driver: str = "chokepoint",
) -> RouteStress:
    return RouteStress(
        route_id=route_id, route_name=route_name,
        stress_score=stress_score,
        chokepoint_stress=0.0, congestion_stress=0.0, weather_stress=0.0,
        rate_stress=0.0, vulnerability=0.0, anomaly_stress=0.0,
        dominant_driver=dominant_driver,
    )


# ── Empty input → defensible empty report ───────────────────────────────


def test_empty_report_returns_defensible_attribution() -> None:
    report = _make_report(overall=0.0, route_stress=[])
    out = attribute_ssi(report)
    assert isinstance(out, SSIAttributionReport)
    assert out.ssi_total == 0.0
    assert out.top_route == ""
    assert out.explanation == "(no signal)"
    # Components still listed (one per known weight) — just with zero weighting
    assert len(out.component_contributions) == len(COMPONENT_WEIGHTS)


# ── Contribution sums + shares ─────────────────────────────────────────


def test_component_contributions_sum_to_weighted_total() -> None:
    """sum(weighted) must equal sum(raw_score * weight) for the inputs."""
    scores = {
        "chokepoint":    0.5,
        "congestion":    0.3,
        "weather":       0.2,
        "rate":          0.4,
        "vulnerability": 0.1,
        "anomaly":       0.5,
    }
    report = _make_report(overall=0.4, component_scores=scores)
    out = attribute_ssi(report)
    expected_total = sum(
        scores[k] * COMPONENT_WEIGHTS[k] for k in COMPONENT_WEIGHTS
    )
    actual_total = sum(c.weighted for c in out.component_contributions)
    assert actual_total == pytest.approx(expected_total)


def test_component_shares_sum_to_one() -> None:
    """Per-component pct_share fractions must sum to ~1.0 (modulo rounding)."""
    scores = {k: 0.5 for k in COMPONENT_WEIGHTS}
    report = _make_report(overall=0.5, component_scores=scores)
    out = attribute_ssi(report)
    total_share = sum(c.pct_share for c in out.component_contributions)
    assert total_share == pytest.approx(1.0)


def test_component_sort_order_is_descending_by_weighted() -> None:
    scores = {
        "chokepoint": 0.9, "congestion": 0.1, "weather": 0.5,
        "rate": 0.2, "vulnerability": 0.3, "anomaly": 0.4,
    }
    report = _make_report(overall=0.5, component_scores=scores)
    out = attribute_ssi(report)
    weighted_seq = [c.weighted for c in out.component_contributions]
    assert weighted_seq == sorted(weighted_seq, reverse=True)


def test_top_component_is_first_in_sorted_list() -> None:
    """`top_component` must equal the first entry of the sorted list."""
    scores = {
        "chokepoint": 0.9, "congestion": 0.1, "weather": 0.0,
        "rate": 0.0, "vulnerability": 0.0, "anomaly": 0.0,
    }
    report = _make_report(overall=0.3, component_scores=scores)
    out = attribute_ssi(report)
    assert out.top_component == "chokepoint"
    assert out.component_contributions[0].component == "chokepoint"


# ── Route contributions ────────────────────────────────────────────────


def test_route_contributions_sort_descending_by_weighted() -> None:
    routes = [
        _make_route("a", "Route A", stress_score=0.2),
        _make_route("b", "Route B", stress_score=0.8),
        _make_route("c", "Route C", stress_score=0.5),
    ]
    report = _make_report(route_stress=routes)
    out = attribute_ssi(report)
    weighted_seq = [r.weighted for r in out.route_contributions]
    assert weighted_seq == sorted(weighted_seq, reverse=True)
    # B should come first (highest raw stress, equal route weight)
    assert out.route_contributions[0].route_id == "b"


def test_route_shares_sum_to_one_when_routes_have_stress() -> None:
    routes = [
        _make_route("a", stress_score=0.4),
        _make_route("b", stress_score=0.6),
    ]
    report = _make_report(route_stress=routes)
    out = attribute_ssi(report)
    total = sum(r.pct_share for r in out.route_contributions)
    assert total == pytest.approx(1.0)


def test_prominent_routes_get_extra_weight_in_attribution() -> None:
    """transpacific_eb / asia_europe carry > 1.0 route weight."""
    routes = [
        _make_route("transpacific_eb", "Trans-Pacific EB", stress_score=0.5),
        _make_route("other", "Other Route", stress_score=0.5),
    ]
    report = _make_report(route_stress=routes)
    out = attribute_ssi(report)
    tp = next(r for r in out.route_contributions if r.route_id == "transpacific_eb")
    ot = next(r for r in out.route_contributions if r.route_id == "other")
    # Equal raw stress; tp's weighted should be >= ot's because of prominence.
    assert tp.weighted >= ot.weighted


def test_top_route_explanation_includes_dominant_driver() -> None:
    routes = [
        _make_route("a", "Suez Lane", stress_score=0.9,
                    dominant_driver="weather"),
    ]
    report = _make_report(overall=0.8, route_stress=routes)
    out = attribute_ssi(report)
    assert "Suez Lane" in out.explanation
    # The driver is also explicitly in the top_route field
    assert "weather" in out.top_route


# ── Defensive handling ─────────────────────────────────────────────────


def test_legacy_report_without_anomaly_component_handled() -> None:
    """A report from before the 6th component (anomaly) was added must
    not raise — missing component scores treated as zero."""
    legacy_scores = {
        "chokepoint": 0.3, "congestion": 0.2, "weather": 0.1,
        "rate": 0.1, "vulnerability": 0.1,
        # NO anomaly key
    }
    report = _make_report(overall=0.2, component_scores=legacy_scores)
    out = attribute_ssi(report)
    # Anomaly component appears in output with raw_score=0
    anomaly_entry = next(
        c for c in out.component_contributions if c.component == "anomaly"
    )
    assert anomaly_entry.raw_score == 0.0


def test_zero_ssi_returns_no_signal_explanation() -> None:
    report = _make_report(overall=0.0)
    out = attribute_ssi(report)
    assert out.explanation == "(no signal)"
