"""Tests for processing.effective_capacity (supply-destruction-by-friction)."""
from __future__ import annotations

from processing.effective_capacity import effective_supply, friction_read


def test_effective_supply_drag_math() -> None:
    es = effective_supply(0.4, 0.6, congestion_weight=0.5, diversion_weight=0.5)
    assert es.congestion_drag == 0.2     # 0.5 × 0.4
    assert es.diversion_drag == 0.3      # 0.5 × 0.6
    assert es.drag_pct == 0.5
    assert es.effective_supply_pct == 0.5


def test_effective_supply_is_capped() -> None:
    es = effective_supply(1.0, 1.0, max_drag=0.6)
    assert es.drag_pct == 0.6            # capped, not 1.0
    assert es.effective_supply_pct == 0.4


def test_effective_supply_clamps_inputs() -> None:
    es = effective_supply(-0.5, 2.0)     # clamp to 0 and 1
    assert es.congestion_drag == 0.0
    assert es.diversion_drag == 0.5


def test_friction_read_supply_destruction_is_bullish() -> None:
    r = friction_read(drag_pct=0.4, freight_change_pct=8.0)
    assert r.label == "Supply destruction by friction"
    assert r.bullish_carriers is True


def test_friction_read_demand_driven_when_low_drag() -> None:
    assert friction_read(0.05, 8.0).label == "Demand-driven tightness"


def test_friction_read_oversupply_when_freight_down_low_drag() -> None:
    r = friction_read(0.05, -8.0)
    assert r.label == "Oversupply / soft demand"
    assert r.bullish_carriers is False


def test_friction_read_friction_easing_when_freight_down_high_drag() -> None:
    assert friction_read(0.4, -8.0).label == "Friction easing into soft demand"


def test_friction_read_balanced_on_flat_freight() -> None:
    assert friction_read(0.05, 0.5).label == "Balanced"
