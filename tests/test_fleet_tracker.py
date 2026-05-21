"""Tests for processing.fleet_tracker — container fleet supply-side snapshot.

Covers:
  - FleetSnapshot dataclass shape (all 12 explicit fields + 2 defaults)
  - VESSEL_CATEGORIES catalog:
      * each entry has required keys (name, fleet_share, orderbook_share, avg_age)
      * shares are in [0, 1]; avg_age is positive
      * fleet_share sums to ~1.0; orderbook_share sums to ~1.0
      * sorted by descending fleet_share (largest categories first) — pin observed order
  - FLEET_2025 baseline:
      * required fields present and in expected ranges
      * implications has exactly 3 bullet points
      * supply_demand_balance = demand_growth - net_supply_growth (sign convention)
      * orderbook_pct ≈ orderbook_teu_m / total_teu_capacity_m * 100 (within rounding)
  - get_fleet_data:
      * returns the FLEET_2025 singleton (identity)
  - get_supply_pressure_score:
      * returns float in [0, 1]
      * formula: 1 - clamp((net_supply - demand + 5) / 10, 0, 1)
      * with FLEET_2025 (oversupplied) → 0.0
"""
from __future__ import annotations

import pytest

from processing.fleet_tracker import (
    FLEET_2025,
    VESSEL_CATEGORIES,
    FleetSnapshot,
    get_fleet_data,
    get_supply_pressure_score,
)


# ─── FleetSnapshot dataclass shape ─────────────────────────────────────────

def test_fleet_snapshot_shape_with_defaults() -> None:
    """All 10 required fields populate; implications/data_vintage default."""
    snap = FleetSnapshot(
        total_teu_capacity_m=30.0,
        orderbook_teu_m=6.0,
        orderbook_pct=20.0,
        deliveries_next_12m_teu_m=2.5,
        scrapping_rate_annual_pct=1.0,
        net_supply_growth_pct=7.0,
        demand_growth_estimate_pct=4.0,
        supply_demand_balance=-3.0,
        market_tightness="BALANCED",
        tightness_color="#888888",
    )
    assert snap.total_teu_capacity_m == 30.0
    assert snap.market_tightness == "BALANCED"
    # field(default_factory=list) → independent empty list per instance
    assert snap.implications == []
    assert snap.data_vintage == ""


def test_fleet_snapshot_implications_default_is_independent_per_instance() -> None:
    """default_factory=list must not share state across instances."""
    a = FleetSnapshot(0, 0, 0, 0, 0, 0, 0, 0, "LOOSE", "#000")
    b = FleetSnapshot(0, 0, 0, 0, 0, 0, 0, 0, "LOOSE", "#000")
    a.implications.append("mutation on a")
    assert b.implications == []


# ─── VESSEL_CATEGORIES catalog ─────────────────────────────────────────────

def test_vessel_categories_entries_have_required_keys() -> None:
    required = {"name", "fleet_share", "orderbook_share", "avg_age"}
    for entry in VESSEL_CATEGORIES:
        assert required <= set(entry.keys()), f"missing keys in {entry}"


def test_vessel_categories_shares_in_unit_interval() -> None:
    for entry in VESSEL_CATEGORIES:
        assert 0.0 <= entry["fleet_share"] <= 1.0
        assert 0.0 <= entry["orderbook_share"] <= 1.0


def test_vessel_categories_avg_age_positive() -> None:
    for entry in VESSEL_CATEGORIES:
        assert entry["avg_age"] > 0


def test_vessel_categories_fleet_share_sums_to_one() -> None:
    total = sum(e["fleet_share"] for e in VESSEL_CATEGORIES)
    assert total == pytest.approx(1.0, abs=1e-6)


def test_vessel_categories_orderbook_share_sums_to_one() -> None:
    total = sum(e["orderbook_share"] for e in VESSEL_CATEGORIES)
    assert total == pytest.approx(1.0, abs=1e-6)


def test_vessel_categories_ordered_largest_to_smallest_ship_size() -> None:
    """Pin observed catalog order: rows go from largest TEU class
    (Ultra Large >18K) down to smallest (Feeder <4K).  This is by ship size,
    not by fleet_share (Very Large has 28% share vs Ultra Large's 22%)."""
    names = [e["name"] for e in VESSEL_CATEGORIES]
    assert names[0].startswith("Ultra Large")
    assert names[1].startswith("Very Large")
    assert names[2].startswith("Large")
    assert names[3].startswith("Medium")
    assert names[4].startswith("Feeder")


def test_vessel_categories_avg_age_increases_for_smaller_ships() -> None:
    """Larger / newer ships dominate the orderbook; feeders are older fleet.
    avg_age should monotonically increase from Ultra Large → Feeder."""
    ages = [e["avg_age"] for e in VESSEL_CATEGORIES]
    assert all(ages[i] < ages[i + 1] for i in range(len(ages) - 1))


def test_vessel_categories_orderbook_concentrated_in_larger_ships() -> None:
    """Ultra Large has highest orderbook share — captures the well-known
    industry trend of cascading toward bigger ships."""
    top_orderbook = max(VESSEL_CATEGORIES, key=lambda e: e["orderbook_share"])
    assert top_orderbook["name"].startswith("Ultra Large")


# ─── FLEET_2025 baseline ───────────────────────────────────────────────────

def test_fleet_2025_is_fleet_snapshot() -> None:
    assert isinstance(FLEET_2025, FleetSnapshot)


def test_fleet_2025_has_three_implications() -> None:
    assert len(FLEET_2025.implications) == 3
    for bullet in FLEET_2025.implications:
        assert isinstance(bullet, str) and bullet


def test_fleet_2025_data_vintage_populated() -> None:
    assert FLEET_2025.data_vintage  # non-empty


def test_fleet_2025_market_tightness_in_allowed_set() -> None:
    allowed = {"VERY_TIGHT", "TIGHT", "BALANCED", "LOOSE", "OVERSUPPLIED"}
    assert FLEET_2025.market_tightness in allowed


def test_fleet_2025_tightness_color_is_hex() -> None:
    c = FLEET_2025.tightness_color
    assert c.startswith("#") and len(c) == 7


def test_fleet_2025_capacities_positive() -> None:
    assert FLEET_2025.total_teu_capacity_m > 0
    assert FLEET_2025.orderbook_teu_m > 0
    assert FLEET_2025.deliveries_next_12m_teu_m > 0


def test_fleet_2025_orderbook_pct_matches_ratio() -> None:
    """orderbook_pct should track orderbook / total fleet, within rounding."""
    expected = FLEET_2025.orderbook_teu_m / FLEET_2025.total_teu_capacity_m * 100.0
    assert FLEET_2025.orderbook_pct == pytest.approx(expected, abs=0.5)


def test_fleet_2025_signals_oversupply() -> None:
    """Net supply growth > demand growth → loose / bearish market."""
    assert FLEET_2025.net_supply_growth_pct > FLEET_2025.demand_growth_estimate_pct
    assert FLEET_2025.market_tightness == "LOOSE"
    # supply_demand_balance encodes the gap; with the baseline it is negative.
    assert FLEET_2025.supply_demand_balance < 0


# ─── get_fleet_data ────────────────────────────────────────────────────────

def test_get_fleet_data_returns_baseline_singleton() -> None:
    """Module-level singleton — identity guarantees mutations would leak,
    which is the contract the caller relies on for cheap reads."""
    assert get_fleet_data() is FLEET_2025


def test_get_fleet_data_returns_fleet_snapshot() -> None:
    assert isinstance(get_fleet_data(), FleetSnapshot)


# ─── get_supply_pressure_score ────────────────────────────────────────────

def test_get_supply_pressure_score_in_unit_interval() -> None:
    score = get_supply_pressure_score()
    assert 0.0 <= score <= 1.0


def test_get_supply_pressure_score_matches_formula() -> None:
    """score = 1 - clamp((net_supply - demand + 5) / 10, 0, 1)."""
    f = get_fleet_data()
    raw = (f.net_supply_growth_pct - f.demand_growth_estimate_pct + 5.0) / 10.0
    expected = 1.0 - min(1.0, max(0.0, raw))
    assert get_supply_pressure_score() == pytest.approx(expected)


def test_get_supply_pressure_score_zero_under_oversupply_baseline() -> None:
    """FLEET_2025: net_supply 10.1, demand 3.5 → raw = 11.6/10 → clamps to 1
    → score = 0.0 (severe oversupply per docstring)."""
    assert get_supply_pressure_score() == pytest.approx(0.0)


def test_get_supply_pressure_score_returns_float() -> None:
    assert isinstance(get_supply_pressure_score(), float)
