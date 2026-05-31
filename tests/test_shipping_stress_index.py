"""Pure-function tests for processing.shipping_stress_index.

The Shipping Stress Index fuses chokepoint, congestion, weather, rate and
vulnerability signals into a per-route ``stress_score`` and a fleet-wide
``overall_ssi``. These tests pin the weight invariant, the [0, 1] bounds on
every score/component, graceful degradation on empty inputs, and the
report shape — no Streamlit, no live feed.
"""
from __future__ import annotations

import pytest

from data.voyage_dataset import build_voyage_fleet
from processing.shipping_stress_index import (
    COMPONENT_WEIGHTS,
    RouteStress,
    ShippingStressReport,
    compute_shipping_stress,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def report() -> ShippingStressReport:
    """An SSI report computed from empty market inputs + a seeded voyage fleet."""
    fleet = build_voyage_fleet(seed=20260518)
    return compute_shipping_stress({}, {}, [], [], voyage_fleet=fleet)


# ── Weight invariant ────────────────────────────────────────────────────────


def test_component_weights_sum_to_one() -> None:
    assert abs(sum(COMPONENT_WEIGHTS.values()) - 1.0) < 1e-9


def test_component_weights_all_non_negative() -> None:
    assert all(w >= 0.0 for w in COMPONENT_WEIGHTS.values())


def test_component_weights_expected_keys() -> None:
    assert set(COMPONENT_WEIGHTS) == {
        "chokepoint", "congestion", "weather", "rate", "vulnerability",
        "anomaly",   # added when the SSI gained a drift-detector component
    }


# ── Shape / sanity ──────────────────────────────────────────────────────────


def test_compute_returns_report(report: ShippingStressReport) -> None:
    assert isinstance(report, ShippingStressReport)


def test_report_has_non_empty_route_stress(report: ShippingStressReport) -> None:
    assert len(report.route_stress) > 0
    assert all(isinstance(rs, RouteStress) for rs in report.route_stress)


def test_report_has_timestamp_and_label(report: ShippingStressReport) -> None:
    assert isinstance(report.data_timestamp, str) and report.data_timestamp
    assert report.ssi_label in {"Calm", "Elevated", "Stressed", "Severe"}
    assert isinstance(report.ssi_color, str) and report.ssi_color.startswith("#")


def test_route_stress_sorted_worst_first(report: ShippingStressReport) -> None:
    scores = [rs.stress_score for rs in report.route_stress]
    assert scores == sorted(scores, reverse=True)


def test_component_scores_keys_match_weights(report: ShippingStressReport) -> None:
    assert set(report.component_scores) == set(COMPONENT_WEIGHTS)


def test_top_disruptions_capped(report: ShippingStressReport) -> None:
    assert isinstance(report.top_disruptions, list)
    assert len(report.top_disruptions) <= 6


# ── Bounds ──────────────────────────────────────────────────────────────────


def test_overall_ssi_in_unit_interval(report: ShippingStressReport) -> None:
    assert 0.0 <= report.overall_ssi <= 1.0


def test_every_route_stress_score_in_unit_interval(
    report: ShippingStressReport,
) -> None:
    for rs in report.route_stress:
        assert 0.0 <= rs.stress_score <= 1.0, rs.route_id


def test_every_component_in_unit_interval(report: ShippingStressReport) -> None:
    """Each of the five per-route components stays within [0, 1]."""
    for rs in report.route_stress:
        for component in (
            rs.chokepoint_stress,
            rs.congestion_stress,
            rs.weather_stress,
            rs.rate_stress,
            rs.vulnerability,
        ):
            assert 0.0 <= component <= 1.0, rs.route_id


def test_component_scores_in_unit_interval(report: ShippingStressReport) -> None:
    for key, value in report.component_scores.items():
        assert 0.0 <= value <= 1.0, key


def test_delayed_voyage_count_non_negative(report: ShippingStressReport) -> None:
    assert all(rs.delayed_voyage_count >= 0 for rs in report.route_stress)


def test_dominant_driver_is_known_label(report: ShippingStressReport) -> None:
    valid = {
        "Chokepoint disruption", "Port congestion", "Weather risk",
        "Freight-rate dislocation", "Structural vulnerability",
    }
    assert all(rs.dominant_driver in valid for rs in report.route_stress)


# ── Graceful degradation ────────────────────────────────────────────────────


def test_all_empty_inputs_produce_valid_report() -> None:
    """Empty dicts/lists and no voyage fleet → a valid report, never a crash."""
    report = compute_shipping_stress({}, {}, [], [], voyage_fleet=None)
    assert isinstance(report, ShippingStressReport)
    assert 0.0 <= report.overall_ssi <= 1.0
    assert len(report.route_stress) > 0


def test_none_inputs_produce_valid_report() -> None:
    """Even ``None`` in place of every collection argument must not raise."""
    report = compute_shipping_stress(None, None, None, None, voyage_fleet=None)
    assert isinstance(report, ShippingStressReport)
    assert 0.0 <= report.overall_ssi <= 1.0
    assert len(report.route_stress) > 0


def test_empty_voyage_fleet_zero_delayed_counts() -> None:
    """An empty voyage fleet means every route reports 0 delayed voyages."""
    report = compute_shipping_stress({}, {}, [], [], voyage_fleet=[])
    assert all(rs.delayed_voyage_count == 0 for rs in report.route_stress)


def test_garbage_freight_data_does_not_raise() -> None:
    """A freight_data dict with unparseable values degrades to neutral rate stress."""
    junk = {"transpacific_eb": "not-a-frame", "asia_europe": None}
    report = compute_shipping_stress(junk, {}, [], [], voyage_fleet=None)
    assert isinstance(report, ShippingStressReport)
    assert 0.0 <= report.overall_ssi <= 1.0


# ── Determinism (within a session) ──────────────────────────────────────────


def test_compute_is_repeatable_for_same_inputs() -> None:
    """Identical inputs yield an identical SSI within a session."""
    fleet = build_voyage_fleet(seed=77)
    a = compute_shipping_stress({}, {}, [], [], voyage_fleet=fleet)
    b = compute_shipping_stress({}, {}, [], [], voyage_fleet=fleet)
    assert a.overall_ssi == b.overall_ssi
    assert [rs.route_id for rs in a.route_stress] == [
        rs.route_id for rs in b.route_stress
    ]


def test_route_congestion_dict_path_honors_zero_reading() -> None:
    """Regression: a dict port-result reporting a legitimate 0.0 congestion
    (the calmest, genuinely-uncongested port) must be honored, not coalesced
    away by an `or`-chain to the neutral 0.5 default (which inflates the
    congestion component + headline SSI). The dict branch must agree with the
    correct object branch, and a real 0.0 must NOT be overridden by a stale
    secondary field."""
    from types import SimpleNamespace as NS
    from processing.shipping_stress_index import (
        _route_congestion_stress, ROUTES_BY_ID,
    )

    rid, route = next(iter(ROUTES_BY_ID.items()))
    dest = route.dest_locode
    dict_stress = _route_congestion_stress(
        rid, [{"locode": dest, "current_congestion": 0.0}])
    obj_stress = _route_congestion_stress(
        rid, [NS(locode=dest, current_congestion=0.0)])
    shadowed = _route_congestion_stress(
        rid, [{"locode": dest, "current_congestion": 0.0, "congestion_index": 0.9}])
    assert dict_stress == obj_stress    # dict branch honors 0.0 like the object branch
    assert shadowed == obj_stress       # real 0.0 not overridden by the 0.9 secondary


def test_component_scores_decompose_overall_ssi() -> None:
    """#8: component_scores are prominence-weighted with the SAME per-route
    weights as overall_ssi, so Σ_k COMPONENT_WEIGHTS[k]·component_scores[k]
    reconciles to the displayed overall_ssi — the breakdown decomposes the
    headline number, not a separate equal-weighted blend."""
    from processing.shipping_stress_index import (
        compute_shipping_stress, COMPONENT_WEIGHTS,
    )
    r = compute_shipping_stress({}, {}, [], [])
    recon = sum(COMPONENT_WEIGHTS[k] * r.component_scores.get(k, 0.0)
                for k in COMPONENT_WEIGHTS)
    assert recon == pytest.approx(r.overall_ssi, abs=1e-3)
