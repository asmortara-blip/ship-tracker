"""Pure-function tests for engine.supply_chain_health.

The Supply Chain Health Index (SCHI) fuses six sub-dimensions into one
composite score [0, 1]. These tests pin:

  * the dimension-weight invariant (sum to 1.0) and expected keys;
  * SupplyChainHealthReport dataclass shape;
  * [0, 1] bounds on the composite and every dimension score;
  * the label / colour ladder (Healthy / Recovering / Stressed / Critical);
  * directional invariants — port congestion and elevated freight rates lower
    the relevant dimension health;
  * graceful degradation on empty / None / garbage inputs;
  * determinism within a session.

Port inputs are duck-typed stubs (the module reads attributes via getattr).
Some dimension scorers import macro / seasonal helpers; passing empty dicts
keeps them on their documented neutral fallbacks. No Streamlit, no live feed.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from engine.supply_chain_health import (
    _WEIGHTS,
    SupplyChainHealthReport,
    compute_supply_chain_health,
)


# ── Duck-typed port stub ────────────────────────────────────────────────────


@dataclass
class _PortStub:
    """Minimal stand-in for a PortDemandResult."""
    locode: str
    port_name: str
    demand_score: float


def _freight_frame(rate: float, fbx: str = "FBX01") -> pd.DataFrame:
    """A one-route freight frame with a flat rate and a known FBX index."""
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
        "rate_usd_per_feu": [rate, rate, rate],
        "index_name": [fbx, fbx, fbx],
    })


# ── Module-scoped report from neutral inputs ────────────────────────────────


@pytest.fixture(scope="module")
def report() -> SupplyChainHealthReport:
    """An SCHI report from fully empty market inputs (every dimension neutral)."""
    return compute_supply_chain_health([], {}, {}, [])


# ── Weight invariant ────────────────────────────────────────────────────────


def test_dimension_weights_sum_to_one() -> None:
    assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9


def test_dimension_weights_all_non_negative() -> None:
    assert all(w >= 0.0 for w in _WEIGHTS.values())


def test_dimension_weights_expected_keys() -> None:
    assert set(_WEIGHTS) == {
        "port_capacity", "freight_cost_pressure", "macro_environment",
        "chokepoint_risk", "inventory_cycle", "seasonal_factors",
    }


# ── Report shape ────────────────────────────────────────────────────────────


def test_compute_returns_report(report: SupplyChainHealthReport) -> None:
    assert isinstance(report, SupplyChainHealthReport)


def test_report_field_types(report: SupplyChainHealthReport) -> None:
    assert isinstance(report.overall_score, float)
    assert isinstance(report.overall_label, str) and report.overall_label
    assert isinstance(report.overall_color, str) and report.overall_color.startswith("#")
    assert isinstance(report.dimension_scores, dict)
    assert isinstance(report.dimension_labels, dict)
    assert isinstance(report.key_risks, list)
    assert isinstance(report.key_tailwinds, list)
    assert isinstance(report.data_timestamp, str) and report.data_timestamp


def test_dimension_scores_keys_match_weights(report: SupplyChainHealthReport) -> None:
    assert set(report.dimension_scores) == set(_WEIGHTS)


def test_dimension_labels_keys_match_weights(report: SupplyChainHealthReport) -> None:
    assert set(report.dimension_labels) == set(_WEIGHTS)


def test_key_risks_and_tailwinds_capped_at_three(
    report: SupplyChainHealthReport,
) -> None:
    assert len(report.key_risks) <= 3
    assert len(report.key_tailwinds) <= 3


def test_overall_label_in_known_set(report: SupplyChainHealthReport) -> None:
    assert report.overall_label in {"Healthy", "Recovering", "Stressed", "Critical"}


def test_dimension_labels_in_known_set(report: SupplyChainHealthReport) -> None:
    valid = {"Healthy", "Moderate", "Stressed", "Critical"}
    assert all(label in valid for label in report.dimension_labels.values())


# ── Bounds ──────────────────────────────────────────────────────────────────


def test_overall_score_in_unit_interval(report: SupplyChainHealthReport) -> None:
    assert 0.0 <= report.overall_score <= 1.0


def test_every_dimension_score_in_unit_interval(
    report: SupplyChainHealthReport,
) -> None:
    for dim, score in report.dimension_scores.items():
        assert 0.0 <= score <= 1.0, dim


def test_overall_score_is_weighted_average_of_dimensions(
    report: SupplyChainHealthReport,
) -> None:
    """overall_score must equal the documented weighted sum of dimension scores."""
    expected = sum(
        _WEIGHTS[dim] * score for dim, score in report.dimension_scores.items()
    )
    assert report.overall_score == pytest.approx(expected, abs=1e-9)


# ── Label / colour ladder ───────────────────────────────────────────────────


def test_label_ladder_consistent_with_score(report: SupplyChainHealthReport) -> None:
    """The overall label must agree with the documented score thresholds."""
    s = report.overall_score
    if s >= 0.70:
        assert report.overall_label == "Healthy"
    elif s >= 0.50:
        assert report.overall_label == "Recovering"
    elif s >= 0.35:
        assert report.overall_label == "Stressed"
    else:
        assert report.overall_label == "Critical"


# ── Directional invariants ──────────────────────────────────────────────────


def test_port_congestion_lowers_port_capacity_health() -> None:
    """High port demand (congestion) yields a lower port_capacity health score."""
    calm = compute_supply_chain_health(
        [_PortStub("A", "Port A", 0.10), _PortStub("B", "Port B", 0.15)], {}, {}, []
    )
    busy = compute_supply_chain_health(
        [_PortStub("A", "Port A", 0.90), _PortStub("B", "Port B", 0.85)], {}, {}, []
    )
    assert (
        busy.dimension_scores["port_capacity"]
        < calm.dimension_scores["port_capacity"]
    )


def test_elevated_freight_rates_lower_freight_cost_health() -> None:
    """A rate well above the FBX default yields lower freight_cost_pressure health."""
    cheap = compute_supply_chain_health(
        [], {"transpacific_eb": _freight_frame(2000.0, "FBX01")}, {}, []
    )
    pricey = compute_supply_chain_health(
        [], {"transpacific_eb": _freight_frame(6000.0, "FBX01")}, {}, []
    )
    assert (
        pricey.dimension_scores["freight_cost_pressure"]
        < cheap.dimension_scores["freight_cost_pressure"]
    )


def test_empty_port_list_gives_neutral_port_capacity(
    report: SupplyChainHealthReport,
) -> None:
    """No ports → port_capacity defaults to the documented neutral 0.5."""
    assert report.dimension_scores["port_capacity"] == 0.5


# ── Graceful degradation ────────────────────────────────────────────────────


def test_all_empty_inputs_produce_valid_report() -> None:
    rpt = compute_supply_chain_health([], {}, {}, [])
    assert isinstance(rpt, SupplyChainHealthReport)
    assert 0.0 <= rpt.overall_score <= 1.0


def test_none_inputs_do_not_raise() -> None:
    """None in place of every collection argument must still yield a valid report."""
    rpt = compute_supply_chain_health(None, None, None, None)
    assert isinstance(rpt, SupplyChainHealthReport)
    assert 0.0 <= rpt.overall_score <= 1.0
    assert set(rpt.dimension_scores) == set(_WEIGHTS)


def test_garbage_freight_data_degrades_to_neutral() -> None:
    """Unparseable freight frames degrade freight_cost_pressure, never crash."""
    junk = {"transpacific_eb": "not-a-frame", "asia_europe": None}
    rpt = compute_supply_chain_health([], junk, {}, [])
    assert isinstance(rpt, SupplyChainHealthReport)
    assert 0.0 <= rpt.dimension_scores["freight_cost_pressure"] <= 1.0


def test_empty_freight_frame_skipped() -> None:
    """A route whose DataFrame is empty falls back to neutral freight health."""
    rpt = compute_supply_chain_health(
        [], {"transpacific_eb": pd.DataFrame()}, {}, []
    )
    assert rpt.dimension_scores["freight_cost_pressure"] == 0.5


# ── Determinism ─────────────────────────────────────────────────────────────


def test_compute_is_repeatable_for_same_inputs() -> None:
    """Identical inputs yield an identical SCHI within a session."""
    ports = [_PortStub("A", "Port A", 0.4), _PortStub("B", "Port B", 0.6)]
    freight = {"transpacific_eb": _freight_frame(2500.0)}
    a = compute_supply_chain_health(ports, freight, {}, [])
    b = compute_supply_chain_health(ports, freight, {}, [])
    assert a.overall_score == b.overall_score
    assert a.dimension_scores == b.dimension_scores
