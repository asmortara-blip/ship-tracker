"""Tests for processing.risk_monitor — chokepoint risk catalog + lookups."""
from __future__ import annotations

import pytest

from processing.risk_monitor import (
    CHOKEPOINTS,
    CHOKEPOINTS_BY_ID,
    Chokepoint,
    get_color,
    get_high_risk_alerts,
    get_risk_score_for_route,
)


# ─── Chokepoint dataclass + catalog ─────────────────────────────────────────

def test_chokepoint_dataclass_shape() -> None:
    c = Chokepoint(
        id="x", name="X", lat=0.0, lon=0.0, region="r",
        daily_vessels=10, pct_world_trade=1.0, risk_level="LOW",
        risk_summary="s", affected_routes=["r1"],
        reroute_impact_days=5, last_updated="2025-01-01",
    )
    assert c.id == "x"
    assert c.risk_level == "LOW"


def test_chokepoint_catalog_non_empty() -> None:
    """The CHOKEPOINTS catalog has the major waterways."""
    assert len(CHOKEPOINTS) >= 5
    ids = {c.id for c in CHOKEPOINTS}
    # Spot-check the obvious must-haves
    assert "suez" in ids
    assert "panama" in ids
    assert "malacca" in ids


def test_chokepoints_by_id_lookup_matches_catalog() -> None:
    """CHOKEPOINTS_BY_ID is the inverted index of the catalog."""
    for c in CHOKEPOINTS:
        assert CHOKEPOINTS_BY_ID[c.id] is c


def test_every_chokepoint_well_formed() -> None:
    """Sanity: every catalog entry has valid risk_level + non-empty
    affected_routes."""
    valid_levels = {"LOW", "MODERATE", "HIGH", "CRITICAL"}
    for c in CHOKEPOINTS:
        assert c.risk_level in valid_levels
        # Cape of Good Hope is the only intentional exception
        # (no affected_routes — it IS the reroute itself).
        # Others should reference at least one route.
        if c.id != "cape_good_hope":
            assert c.affected_routes, f"{c.id} has no affected routes"


# ─── get_risk_score_for_route ──────────────────────────────────────────────

def test_get_risk_score_zero_for_unmonitored_route() -> None:
    """A route not in any chokepoint's affected_routes → 0.0."""
    assert get_risk_score_for_route("definitely_no_such_route_xyz") == 0.0


def test_get_risk_score_in_unit_interval() -> None:
    """Every score is in [0, 1]."""
    # Sample a known affected route from the catalog.
    score = get_risk_score_for_route("asia_europe")
    assert 0.0 <= score <= 1.0


def test_get_risk_score_elevated_for_asia_europe() -> None:
    """asia_europe transits Suez (HIGH) + Bab-el-Mandeb (HIGH) plus
    Malacca + Cape (LOW). The average lands around 0.45 — clearly elevated
    vs the 0.0 baseline for unmonitored routes."""
    score = get_risk_score_for_route("asia_europe")
    # Comfortably above 0; the exact value depends on which chokepoints
    # claim asia_europe in their affected_routes.
    assert score >= 0.3


def test_get_risk_score_known_routes_consistent_with_chokepoints() -> None:
    """For every chokepoint, the routes it affects have a non-zero risk score."""
    for c in CHOKEPOINTS:
        for route_id in c.affected_routes:
            score = get_risk_score_for_route(route_id)
            assert score > 0.0, (
                f"chokepoint {c.id} lists {route_id} as affected but "
                f"get_risk_score_for_route returned 0"
            )


# ─── get_high_risk_alerts ──────────────────────────────────────────────────

def test_get_high_risk_alerts_returns_high_and_critical_only() -> None:
    alerts = get_high_risk_alerts()
    for c in alerts:
        assert c.risk_level in ("HIGH", "CRITICAL")


def test_get_high_risk_alerts_includes_known_high_risk() -> None:
    """Suez + Bab-el-Mandeb are HIGH per the catalog — both surface."""
    alerts = get_high_risk_alerts()
    ids = {c.id for c in alerts}
    assert "suez" in ids
    assert "bab_el_mandeb" in ids


def test_get_high_risk_alerts_excludes_low_risk() -> None:
    """Malacca is LOW per the catalog — not in alerts."""
    alerts = get_high_risk_alerts()
    ids = {c.id for c in alerts}
    assert "malacca" not in ids


# ─── get_color ─────────────────────────────────────────────────────────────

def test_get_color_for_each_risk_level() -> None:
    for level in ("LOW", "MODERATE", "HIGH", "CRITICAL"):
        color = get_color(level)
        assert isinstance(color, str)
        assert color.startswith("#")
        assert len(color) in (4, 7)   # #abc or #aabbcc


def test_get_color_unknown_level_returns_fallback() -> None:
    color = get_color("UNKNOWN_LEVEL")
    assert color == "#95a5a6"   # the documented default


def test_get_color_critical_distinct_from_high() -> None:
    """CRITICAL and HIGH should be visually distinct in the palette."""
    assert get_color("CRITICAL") != get_color("HIGH")
