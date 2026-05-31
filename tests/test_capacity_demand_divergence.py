"""Defining-property tests for processing/capacity_demand_divergence.py."""
from __future__ import annotations

import pytest

from processing.capacity_demand_divergence import (
    DIVERGENCE_BANDS,
    RouteDivergencePoint,
    RouteDivergenceReport,
    compute_route_divergence,
    summarize_persistent_divergence,
)


# ── Per-day divergence math ──────────────────────────────────────────────


def test_equal_capacity_and_demand_is_balanced() -> None:
    p = compute_route_divergence(
        route_id="x", date_iso="2026-05-26",
        capacity_teu=1000.0, demand_teu=1000.0,
    )
    assert p.divergence == pytest.approx(0.0)


def test_pure_capacity_surplus_is_plus_one() -> None:
    """Demand=0 → divergence=+1.0 (pure capacity sitting idle)."""
    p = compute_route_divergence(
        route_id="x", date_iso="2026-05-26",
        capacity_teu=1000.0, demand_teu=0.0,
    )
    assert p.divergence == pytest.approx(1.0)


def test_pure_demand_surplus_is_minus_one() -> None:
    """Capacity=0 → divergence=-1.0 (pure demand)."""
    p = compute_route_divergence(
        route_id="x", date_iso="2026-05-26",
        capacity_teu=0.0, demand_teu=1000.0,
    )
    assert p.divergence == pytest.approx(-1.0)


def test_both_zero_returns_zero() -> None:
    """No flow on either side → 0 (not div-by-zero)."""
    p = compute_route_divergence(
        route_id="x", date_iso="2026-05-26",
        capacity_teu=0.0, demand_teu=0.0,
    )
    assert p.divergence == 0.0


def test_negative_inputs_clamped_to_zero() -> None:
    """Bad sensor data with negative values → treated as 0, no exception."""
    p = compute_route_divergence(
        route_id="x", date_iso="2026-05-26",
        capacity_teu=-100.0, demand_teu=1000.0,
    )
    assert p.capacity_teu == 0.0
    assert p.divergence == -1.0   # pure demand after clamping


def test_partial_surplus_proportional() -> None:
    """1500 capacity vs 1000 demand → (1500-1000)/1500 = 0.333"""
    p = compute_route_divergence(
        route_id="x", date_iso="2026-05-26",
        capacity_teu=1500.0, demand_teu=1000.0,
    )
    assert p.divergence == pytest.approx(500.0 / 1500.0)


# ── Persistent-divergence summarisation ─────────────────────────────────


def _const_history(
    route_id: str, n: int, capacity: float, demand: float,
) -> list[RouteDivergencePoint]:
    return [
        compute_route_divergence(
            route_id=route_id,
            date_iso=f"2026-05-{(i + 1):02d}",
            capacity_teu=capacity, demand_teu=demand,
        )
        for i in range(n)
    ]


def test_summary_on_empty_points_returns_defensible_report() -> None:
    r = summarize_persistent_divergence([])
    assert isinstance(r, RouteDivergenceReport)
    assert r.n_points == 0
    assert r.direction == "balanced"
    assert r.is_alert_worthy is False
    assert "(no data)" in r.summary


def test_summary_persistent_surplus_flagged() -> None:
    """10 days of constant +0.33 divergence → mean +0.33, persistence 100%,
    band 'stretched' → alert worthy."""
    points = _const_history("x", 10, capacity=1500.0, demand=1000.0)
    r = summarize_persistent_divergence(points)
    assert r.mean_divergence == pytest.approx(1/3)
    assert r.persistence_rate == pytest.approx(1.0)
    assert r.direction == "capacity_surplus"
    assert r.divergence_band == "stretched"
    assert r.is_alert_worthy is True


def test_summary_persistent_demand_flagged() -> None:
    """Demand-side mirror — 10 days of -0.33 → demand_surplus + alert."""
    points = _const_history("x", 10, capacity=1000.0, demand=1500.0)
    r = summarize_persistent_divergence(points)
    assert r.mean_divergence == pytest.approx(-1/3)
    assert r.direction == "demand_surplus"
    assert r.is_alert_worthy is True


def test_summary_balanced_window_not_alert_worthy() -> None:
    points = _const_history("x", 10, capacity=1000.0, demand=1000.0)
    r = summarize_persistent_divergence(points)
    assert r.mean_divergence == 0.0
    assert r.direction == "balanced"
    assert r.divergence_band == "balanced"
    assert r.is_alert_worthy is False


def test_summary_alternating_signs_low_persistence() -> None:
    """Alternating +0.5 / -0.5 days → mean ~0, persistence 0% → no alert."""
    points = []
    for i in range(10):
        capacity = 2000.0 if i % 2 == 0 else 1000.0
        demand = 1000.0 if i % 2 == 0 else 2000.0
        points.append(compute_route_divergence(
            route_id="x", date_iso=f"d{i}",
            capacity_teu=capacity, demand_teu=demand,
        ))
    r = summarize_persistent_divergence(points)
    assert abs(r.mean_divergence) < 0.01
    # Even if mean magnitude dressed up, persistence kills the alert.
    assert r.is_alert_worthy is False


def test_summary_high_magnitude_low_persistence_no_alert() -> None:
    """One day at -0.9, rest balanced → high single-day magnitude but
    persistence is 1/1 = 100% for the one nonzero day → mean ~-0.09
    which is 'balanced' band → no alert. This is the noise filter."""
    points = []
    for i in range(10):
        if i == 5:
            points.append(compute_route_divergence(
                route_id="x", date_iso=f"d{i}",
                capacity_teu=100.0, demand_teu=1000.0,   # -0.9
            ))
        else:
            points.append(compute_route_divergence(
                route_id="x", date_iso=f"d{i}",
                capacity_teu=1000.0, demand_teu=1000.0,
            ))
    r = summarize_persistent_divergence(points)
    # Mean magnitude is tiny, band lands at balanced/loose
    assert r.divergence_band in ("balanced", "loose")
    assert r.is_alert_worthy is False


def test_persistence_threshold_override_respected() -> None:
    """A 60% persistence window won't meet the default 0.70 persistence gate
    but does once the threshold is lowered to 0.50."""
    # 6 surplus days + 4 balanced days → persistence = 6 of 10 = 0.60. Balanced
    # (zero-divergence) days count in the WINDOW denominator (the documented
    # "fraction of days" / "7 of 10 days" contract), so this is 0.60, NOT the
    # 6/6=1.0 you'd get by dividing only by the nonzero days.
    points = []
    for i in range(10):
        if i < 6:
            points.append(compute_route_divergence(
                route_id="x", date_iso=f"d{i}",
                capacity_teu=2000.0, demand_teu=1000.0,
            ))
        else:
            points.append(compute_route_divergence(
                route_id="x", date_iso=f"d{i}",
                capacity_teu=1000.0, demand_teu=1000.0,
            ))
    r = summarize_persistent_divergence(points)                 # default 0.70
    assert r.persistence_rate == pytest.approx(0.6)
    # 0.60 < 0.70 → the persistence gate fails, so the route can't alert
    # regardless of band.
    assert r.is_alert_worthy is False
    # Lower the threshold below the observed persistence → the gate is now met.
    r2 = summarize_persistent_divergence(points, persistence_threshold=0.50)
    assert r2.persistence_rate == pytest.approx(0.6)
    assert r2.persistence_rate >= 0.50


# ── Summary string ──────────────────────────────────────────────────────


def test_summary_string_includes_route_id_and_direction() -> None:
    points = _const_history("transpacific_eb", 10, 1500.0, 1000.0)
    r = summarize_persistent_divergence(points)
    assert "transpacific_eb" in r.summary
    assert "capacity_surplus" in r.summary
    assert "alert=YES" in r.summary
