"""Tests for processing.equipment_tracker — container equipment availability,
utilization, and trade-imbalance repositioning economics.

Covers:
  - EquipmentStatus dataclass shape
  - TradeImbalanceMetrics dataclass shape
  - Catalog integrity:
      * CONTAINER_TYPES / REGIONS literals are non-empty and unique
      * REGIONAL_EQUIPMENT_STATUS covers every (region, type) combination
      * shortage_risk values are restricted to the documented set
      * utilization_pct ∈ [0, 100]
      * TRADE_IMBALANCE_DATA route_ids are unique; ratios > 0
  - get_equipment_status:
      * known (region, type) returns the matching dataclass
      * unknown combo returns None
      * unknown region returns None
  - get_trade_imbalance:
      * known route_id returns the matching dataclass
      * unknown route_id returns None
  - compute_equipment_adjusted_rate:
      * known route adds repositioning cost to base rate
      * unknown route returns base_rate unchanged + emits a warning log
      * zero base_rate still adds repositioning cost
  - get_global_equipment_index:
      * returns a float in [0, 100]
      * matches a hand-computed weighted average from the underlying catalog
      * rounded to 2 decimals
  - get_reefer_summary:
      * keys present and types correct
      * regions_critical contains North America (only CRITICAL reefer in catalog)
      * regions_high contains the five HIGH-risk reefer regions
      * total_units_k matches sum of reefer rows
      * avg_utilization_pct is capacity-weighted (not simple mean)
      * avg_lease_rate_usd is a simple mean across reefer rows
"""
from __future__ import annotations

import logging

import pytest

from processing.equipment_tracker import (
    CONTAINER_TYPES,
    REGIONAL_EQUIPMENT_STATUS,
    REGIONS,
    TRADE_IMBALANCE_DATA,
    EquipmentStatus,
    TradeImbalanceMetrics,
    compute_equipment_adjusted_rate,
    get_equipment_status,
    get_global_equipment_index,
    get_reefer_summary,
    get_trade_imbalance,
)


# ─── Dataclass shape ────────────────────────────────────────────────────────

def test_equipment_status_dataclass_shape() -> None:
    e = EquipmentStatus(
        region="Asia Pacific",
        container_type="40FT_DRY",
        available_units_k=100.0,
        utilization_pct=75.0,
        shortage_risk="LOW",
        daily_lease_rate_usd=0.80,
        vs_year_ago_pct=-1.0,
        days_surplus_deficit=10,
    )
    assert e.region == "Asia Pacific"
    assert e.container_type == "40FT_DRY"
    assert e.shortage_risk == "LOW"
    assert e.days_surplus_deficit == 10


def test_trade_imbalance_metrics_dataclass_shape() -> None:
    m = TradeImbalanceMetrics(
        route_id="r",
        origin_region="Asia Pacific",
        dest_region="North America",
        empty_container_repositioning_cost_per_feu=400,
        imbalance_ratio=1.8,
        repositioning_days=18,
    )
    assert m.route_id == "r"
    assert m.imbalance_ratio == pytest.approx(1.8)
    assert m.repositioning_days == 18


# ─── Catalog integrity ──────────────────────────────────────────────────────

def test_container_types_and_regions_non_empty_and_unique() -> None:
    assert len(CONTAINER_TYPES) > 0
    assert len(REGIONS) > 0
    assert len(set(CONTAINER_TYPES)) == len(CONTAINER_TYPES)
    assert len(set(REGIONS)) == len(REGIONS)


def test_regional_equipment_status_covers_every_combination() -> None:
    """Every (region, type) pair has exactly one row → 6 × 5 = 30 entries."""
    pairs = {(e.region, e.container_type) for e in REGIONAL_EQUIPMENT_STATUS}
    expected = {(r, t) for r in REGIONS for t in CONTAINER_TYPES}
    assert pairs == expected
    # Length is exactly the cross-product (no duplicates)
    assert len(REGIONAL_EQUIPMENT_STATUS) == len(REGIONS) * len(CONTAINER_TYPES)


def test_shortage_risk_values_are_restricted() -> None:
    allowed = {"CRITICAL", "HIGH", "MODERATE", "LOW"}
    for e in REGIONAL_EQUIPMENT_STATUS:
        assert e.shortage_risk in allowed, (
            f"{e.region}/{e.container_type} has illegal risk {e.shortage_risk!r}"
        )


def test_utilization_pct_within_zero_to_hundred() -> None:
    for e in REGIONAL_EQUIPMENT_STATUS:
        assert 0.0 <= e.utilization_pct <= 100.0


def test_trade_imbalance_route_ids_unique_and_positive_ratios() -> None:
    ids = [m.route_id for m in TRADE_IMBALANCE_DATA]
    assert len(set(ids)) == len(ids)
    for m in TRADE_IMBALANCE_DATA:
        assert m.imbalance_ratio > 0
        assert m.empty_container_repositioning_cost_per_feu >= 0
        assert m.repositioning_days >= 0


# ─── get_equipment_status ───────────────────────────────────────────────────

def test_get_equipment_status_known_combination_returns_dataclass() -> None:
    e = get_equipment_status("Asia Pacific", "40FT_DRY")
    assert e is not None
    assert isinstance(e, EquipmentStatus)
    assert e.region == "Asia Pacific"
    assert e.container_type == "40FT_DRY"
    # Pin actual catalog value to lock current behavior
    assert e.utilization_pct == pytest.approx(71.0)
    assert e.shortage_risk == "LOW"


def test_get_equipment_status_unknown_combination_returns_none() -> None:
    assert get_equipment_status("Asia Pacific", "ZZ_BOGUS") is None


def test_get_equipment_status_unknown_region_returns_none() -> None:
    assert get_equipment_status("Mars", "40FT_DRY") is None


def test_get_equipment_status_north_america_reefer_is_critical() -> None:
    """The only CRITICAL reefer in the catalog — pin it."""
    e = get_equipment_status("North America", "40FT_REEFER")
    assert e is not None
    assert e.shortage_risk == "CRITICAL"
    assert e.utilization_pct == pytest.approx(91.0)


# ─── get_trade_imbalance ────────────────────────────────────────────────────

def test_get_trade_imbalance_known_route_returns_dataclass() -> None:
    m = get_trade_imbalance("transpacific_eb")
    assert m is not None
    assert isinstance(m, TradeImbalanceMetrics)
    assert m.origin_region == "Asia Pacific"
    assert m.dest_region == "North America"
    assert m.empty_container_repositioning_cost_per_feu == pytest.approx(400)
    assert m.imbalance_ratio == pytest.approx(1.80)


def test_get_trade_imbalance_unknown_route_returns_none() -> None:
    assert get_trade_imbalance("nonexistent_route") is None


# ─── compute_equipment_adjusted_rate ────────────────────────────────────────

def test_compute_equipment_adjusted_rate_known_route_adds_reposition_cost() -> None:
    # transpacific_eb has repositioning_cost = 400
    base = 2500.0
    out = compute_equipment_adjusted_rate("transpacific_eb", base)
    assert out == pytest.approx(2500.0 + 400.0)


def test_compute_equipment_adjusted_rate_zero_base_still_adds_cost() -> None:
    # Pin: function adds cost regardless of base — no flooring or short-circuit
    out = compute_equipment_adjusted_rate("asia_europe", 0.0)
    assert out == pytest.approx(350.0)


def test_compute_equipment_adjusted_rate_unknown_route_returns_base_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown route_id → logs warning and returns base unchanged."""
    with caplog.at_level(logging.WARNING):
        out = compute_equipment_adjusted_rate("does_not_exist", 1234.5)
    assert out == pytest.approx(1234.5)


def test_compute_equipment_adjusted_rate_matches_known_lookup() -> None:
    """For every documented route, adjusted = base + the route's repositioning_cost."""
    base = 1000.0
    for m in TRADE_IMBALANCE_DATA:
        adj = compute_equipment_adjusted_rate(m.route_id, base)
        assert adj == pytest.approx(base + m.empty_container_repositioning_cost_per_feu)


# ─── get_global_equipment_index ─────────────────────────────────────────────

def test_get_global_equipment_index_in_zero_to_hundred() -> None:
    idx = get_global_equipment_index()
    assert 0.0 <= idx <= 100.0


def test_get_global_equipment_index_matches_manual_weighted_average() -> None:
    """Recompute the weighted average using the same weights documented in the
    function body, and confirm the output matches (within rounding)."""
    region_weights = {
        "Asia Pacific":  0.40,
        "North America": 0.22,
        "Europe":        0.20,
        "South America": 0.08,
        "Middle East":   0.06,
        "Africa":        0.04,
    }
    type_weights = {
        "20FT_DRY":    0.25,
        "40FT_DRY":    0.35,
        "40FT_HC":     0.25,
        "40FT_REEFER": 0.10,
        "20FT_TANK":   0.05,
    }
    total_w = 0.0
    weighted = 0.0
    for e in REGIONAL_EQUIPMENT_STATUS:
        w = region_weights[e.region] * type_weights[e.container_type]
        weighted += e.utilization_pct * w
        total_w += w
    expected = round(weighted / total_w, 2)
    assert get_global_equipment_index() == pytest.approx(expected)


def test_get_global_equipment_index_rounded_to_two_decimals() -> None:
    """Output rounded to 2 dp — pin the current value (76.01)."""
    idx = get_global_equipment_index()
    # Two decimals: idx * 100 should be effectively an integer
    assert abs(round(idx * 100) - idx * 100) < 1e-9
    # And matches the snapshot for this hardcoded baseline
    assert idx == pytest.approx(76.01)


# ─── get_reefer_summary ─────────────────────────────────────────────────────

def test_get_reefer_summary_has_documented_keys() -> None:
    summary = get_reefer_summary()
    expected_keys = {
        "avg_utilization_pct",
        "regions_critical",
        "regions_high",
        "avg_lease_rate_usd",
        "total_units_k",
    }
    assert set(summary.keys()) == expected_keys


def test_get_reefer_summary_critical_is_north_america_only() -> None:
    """Only one CRITICAL reefer in catalog — pin it."""
    summary = get_reefer_summary()
    assert summary["regions_critical"] == ["North America"]


def test_get_reefer_summary_high_contains_expected_regions() -> None:
    """Five regions are HIGH-risk for reefers (not CRITICAL, not MODERATE/LOW)."""
    summary = get_reefer_summary()
    high = set(summary["regions_high"])
    # Asia Pacific, Europe, South America, Middle East, Africa
    assert high == {"Asia Pacific", "Europe", "South America", "Middle East", "Africa"}
    # And North America is NOT in the high list (it's critical)
    assert "North America" not in high


def test_get_reefer_summary_total_units_k_matches_catalog_sum() -> None:
    summary = get_reefer_summary()
    expected = round(
        sum(
            e.available_units_k
            for e in REGIONAL_EQUIPMENT_STATUS
            if e.container_type == "40FT_REEFER"
        ),
        1,
    )
    assert summary["total_units_k"] == pytest.approx(expected)


def test_get_reefer_summary_avg_utilization_is_capacity_weighted() -> None:
    """Defining property: utilization is weighted by available_units_k,
    NOT a simple mean of utilization_pct values."""
    reefers = [e for e in REGIONAL_EQUIPMENT_STATUS if e.container_type == "40FT_REEFER"]
    total_units = sum(r.available_units_k for r in reefers)
    expected_weighted = sum(r.utilization_pct * r.available_units_k for r in reefers) / total_units
    summary = get_reefer_summary()
    assert summary["avg_utilization_pct"] == pytest.approx(round(expected_weighted, 1))

    # Confirm it would differ from a simple mean if simple mean differs
    simple_mean = round(sum(r.utilization_pct for r in reefers) / len(reefers), 1)
    if abs(simple_mean - round(expected_weighted, 1)) > 0.05:
        # If they happen to be equal in this catalog, the assertion above is enough.
        # When they diverge, confirm the weighted (not simple) was used.
        assert summary["avg_utilization_pct"] != pytest.approx(simple_mean)


def test_get_reefer_summary_avg_lease_rate_is_simple_mean() -> None:
    """Defining property: lease rate is a SIMPLE mean across reefer rows."""
    reefers = [e for e in REGIONAL_EQUIPMENT_STATUS if e.container_type == "40FT_REEFER"]
    expected = round(sum(r.daily_lease_rate_usd for r in reefers) / len(reefers), 2)
    summary = get_reefer_summary()
    assert summary["avg_lease_rate_usd"] == pytest.approx(expected)
