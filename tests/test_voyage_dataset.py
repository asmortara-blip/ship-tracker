"""Pure-function tests for data.voyage_dataset.

The modeled voyage fleet is synthetic but *deterministic*: a fixed ``seed``
must reproduce an identical fleet. These tests pin that contract, the
per-voyage field bounds, the lookup/search helpers and the fleet summary —
all without importing Streamlit or touching any live feed.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from data.voyage_dataset import (
    Voyage,
    build_voyage_fleet,
    get_voyage,
    search_voyages,
    voyage_fleet_summary,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fleet() -> list[Voyage]:
    """A deterministic, seeded fleet shared across the read-only tests."""
    return build_voyage_fleet(seed=20260518)


# ── Determinism ─────────────────────────────────────────────────────────────


def test_build_voyage_fleet_is_deterministic() -> None:
    """Same seed → byte-for-byte identical fleet (the core contract)."""
    a = build_voyage_fleet(seed=42)
    b = build_voyage_fleet(seed=42)
    assert a == b
    assert len(a) == len(b)


def test_build_voyage_fleet_different_seeds_differ() -> None:
    """Different seeds should produce different fleets (not a frozen constant)."""
    a = build_voyage_fleet(seed=1)
    b = build_voyage_fleet(seed=999_999)
    assert a != b


# ── Realism: serial correlation + delay/congestion coupling ─────────────────


def test_delays_are_serially_correlated_within_route(fleet: list[Voyage]) -> None:
    """Every voyage on a route shares one route-level congestion state.

    Delays are modeled as ``route_congestion_state + idiosyncratic noise``,
    so each route must expose exactly one congestion state across all of its
    voyages — that is the structural signature of serial correlation.
    """
    by_route: dict[str, list[Voyage]] = {}
    for v in fleet:
        by_route.setdefault(v.route_id, []).append(v)

    for route_id, voyages in by_route.items():
        states = {v.route_congestion_state for v in voyages}
        assert len(states) == 1, (
            f"route {route_id} has multiple congestion states {states}"
        )
        if len(voyages) >= 3:
            state = voyages[0].route_congestion_state
            # Each voyage's delay sits near the shared state (noise is small).
            for v in voyages:
                assert abs(v.delay_days - state) < 5.0


def test_delay_and_congestion_are_positively_coupled(fleet: list[Voyage]) -> None:
    """A more-delayed voyage arrives into a more congested port.

    congestion_at_dest is a function of the voyage's own delay, so across the
    fleet the two series must be positively correlated, not independent.
    """
    delays = [v.delay_days for v in fleet]
    cong = [v.congestion_at_dest for v in fleet]
    n = len(delays)
    mean_d = sum(delays) / n
    mean_c = sum(cong) / n
    cov = sum((d - mean_d) * (c - mean_c) for d, c in zip(delays, cong)) / n
    var_d = sum((d - mean_d) ** 2 for d in delays) / n
    var_c = sum((c - mean_c) ** 2 for c in cong) / n
    corr = cov / ((var_d ** 0.5) * (var_c ** 0.5)) if var_d and var_c else 0.0
    assert corr > 0.2, f"delay/congestion correlation too weak: {corr:.3f}"


def test_chokepoint_severity_is_a_known_tier(fleet: list[Voyage]) -> None:
    """The reroute penalty is tiered, not flat: severity ∈ a known set."""
    valid = {"", "minor", "moderate", "severe"}
    assert all(v.chokepoint_severity in valid for v in fleet)
    # A disrupted voyage carries a non-empty tier; a clean one carries "".
    for v in fleet:
        if v.chokepoints_on_route:
            assert v.chokepoint_severity in {"minor", "moderate", "severe"}
        else:
            assert v.chokepoint_severity == ""


def test_build_voyage_fleet_default_seed_runs() -> None:
    """A ``None`` seed falls back to the date-derived seed and still builds."""
    fleet = build_voyage_fleet()
    assert isinstance(fleet, list)
    assert len(fleet) > 0
    assert all(isinstance(v, Voyage) for v in fleet)


# ── Shape / sanity ──────────────────────────────────────────────────────────


def test_fleet_is_non_empty_list_of_voyages(fleet: list[Voyage]) -> None:
    assert isinstance(fleet, list)
    assert len(fleet) > 0
    assert all(isinstance(v, Voyage) for v in fleet)


def test_per_route_band_controls_fleet_size() -> None:
    """A wider per-route band must not yield a smaller fleet than a narrow one."""
    small = build_voyage_fleet(seed=5, per_route=(1, 1))
    large = build_voyage_fleet(seed=5, per_route=(8, 9))
    assert len(large) >= len(small)
    assert len(small) > 0


def test_per_route_reversed_band_tolerated() -> None:
    """A (max, min) band is swapped internally rather than raising."""
    fleet = build_voyage_fleet(seed=3, per_route=(9, 4))
    assert len(fleet) > 0


def test_voyage_ids_are_unique(fleet: list[Voyage]) -> None:
    ids = [v.voyage_id for v in fleet]
    assert len(ids) == len(set(ids))


# ── Bounds ──────────────────────────────────────────────────────────────────


def test_voyage_field_bounds(fleet: list[Voyage]) -> None:
    """Every per-voyage [0, 1] field stays in range; structural fields are sane."""
    for v in fleet:
        assert 0.0 <= v.progress_pct <= 1.0
        assert 0.0 <= v.congestion_at_dest <= 1.0
        assert v.weather_delay_days >= 0.0
        assert v.delay_days >= -2.0           # delay model floor
        assert v.speed_kts > 0.0
        assert v.nominal_transit_days >= 1
        assert -90.0 <= v.current_lat <= 90.0
        assert -180.0 <= v.current_lon <= 180.0
        assert isinstance(v.chokepoints_on_route, list)


def test_voyage_status_is_known_value(fleet: list[Voyage]) -> None:
    valid = {"On Schedule", "Minor Delay", "Major Delay", "Arrived"}
    assert all(v.status in valid for v in fleet)


def test_voyage_dates_consistent(fleet: list[Voyage]) -> None:
    """ETA fields are real dates and the nominal ETA follows the departure."""
    for v in fleet:
        assert isinstance(v.departed_at, _dt.date)
        assert isinstance(v.eta_nominal, _dt.date)
        assert isinstance(v.eta_adjusted, _dt.date)
        assert v.eta_nominal >= v.departed_at


# ── get_voyage ──────────────────────────────────────────────────────────────


def test_get_voyage_found_case_insensitive(fleet: list[Voyage]) -> None:
    target = fleet[0]
    assert get_voyage(target.voyage_id, fleet=fleet) is target
    assert get_voyage(target.voyage_id.lower(), fleet=fleet) is target
    assert get_voyage(f"  {target.voyage_id}  ", fleet=fleet) is target


def test_get_voyage_missing_returns_none(fleet: list[Voyage]) -> None:
    assert get_voyage("VY-DOES-NOT-EXIST-99", fleet=fleet) is None


def test_get_voyage_empty_fleet_returns_none() -> None:
    assert get_voyage("anything", fleet=[]) is None


# ── search_voyages ──────────────────────────────────────────────────────────


def test_search_empty_query_returns_whole_fleet(fleet: list[Voyage]) -> None:
    result = search_voyages("", fleet=fleet)
    assert len(result) == len(fleet)


def test_search_none_query_returns_whole_fleet(fleet: list[Voyage]) -> None:
    result = search_voyages(None, fleet=fleet)  # type: ignore[arg-type]
    assert len(result) == len(fleet)


def test_search_matches_voyage_id(fleet: list[Voyage]) -> None:
    target = fleet[0]
    hits = search_voyages(target.voyage_id, fleet=fleet)
    assert target in hits
    assert all(isinstance(v, Voyage) for v in hits)


def test_search_matches_route_id(fleet: list[Voyage]) -> None:
    """A route_id query returns every voyage on that lane (substring match)."""
    route_id = fleet[0].route_id
    hits = search_voyages(route_id, fleet=fleet)
    assert hits
    # The searched lane's voyages are all present...
    on_route = [v for v in fleet if v.route_id == route_id]
    assert set(id(v) for v in on_route).issubset(set(id(v) for v in hits))
    # ...and every hit matches the query as a substring of some searched field.
    q = route_id.lower()
    for v in hits:
        assert (
            q in v.vessel_name.lower()
            or q in v.voyage_id.lower()
            or q in v.mmsi.lower()
            or q in v.route_id.lower()
            or q in v.origin_locode.lower()
            or q in v.dest_locode.lower()
        )


def test_search_no_match_returns_empty(fleet: list[Voyage]) -> None:
    assert search_voyages("zzz-no-such-token-zzz", fleet=fleet) == []


def test_search_empty_fleet_returns_empty() -> None:
    assert search_voyages("anything", fleet=[]) == []


# ── voyage_fleet_summary ────────────────────────────────────────────────────


def test_summary_empty_fleet_neutral_defaults() -> None:
    """An empty fleet yields a valid all-zero summary, never an error."""
    summary = voyage_fleet_summary([])
    expected_keys = {
        "total", "in_transit", "arrived", "on_schedule", "minor_delay",
        "major_delay", "delayed", "delayed_pct", "avg_delay_days",
        "avg_progress_pct", "avg_speed_kts", "disrupted_routes",
    }
    assert set(summary.keys()) == expected_keys
    assert summary["total"] == 0
    assert summary["delayed_pct"] == 0.0
    assert summary["avg_delay_days"] == 0.0


def test_summary_counts_are_consistent(fleet: list[Voyage]) -> None:
    summary = voyage_fleet_summary(fleet)
    assert summary["total"] == len(fleet)
    # Status buckets must partition the fleet.
    assert (
        summary["on_schedule"]
        + summary["minor_delay"]
        + summary["major_delay"]
        + summary["arrived"]
        == summary["total"]
    )
    assert summary["delayed"] == summary["minor_delay"] + summary["major_delay"]
    assert summary["in_transit"] == summary["total"] - summary["arrived"]


def test_summary_bounds(fleet: list[Voyage]) -> None:
    summary = voyage_fleet_summary(fleet)
    assert 0.0 <= summary["delayed_pct"] <= 100.0
    assert 0.0 <= summary["avg_progress_pct"] <= 100.0
    assert summary["avg_speed_kts"] > 0.0
    assert summary["disrupted_routes"] >= 0
