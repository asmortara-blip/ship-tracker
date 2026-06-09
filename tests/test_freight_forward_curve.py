"""Tests for R042 — MODELED freight-forward (FFA) term structure.

Covers the defining properties of ``build_forward_curve``:

  * rallying spot + light orderbook  → BACKWARDATION (forwards < spot),
    positive roll yield, fade signal.
  * weak spot + heavy near-deliveries → CONTANGO (forwards > spot),
    negative roll yield.
  * flat / empty / degenerate inputs → a FLAT curve at spot, no crash.
  * roll_yield + basis arithmetic on a hand example.
  * monotonic tenor structure where the heuristic dictates it.
  * the provenance stamp is MODELED (never live).
  * the ui.tab_derivatives tab still imports / smoke-renders.
"""
from __future__ import annotations

import math

import pytest

from data.quality import DataKind, DataQuality
from processing.freight_forward_curve import (
    ForwardCurve,
    MODELED_CURVE_SOURCE,
    _MAX_MOMENTUM_TILT,
    _MAX_SUPPLY_DRAG,
    build_forward_curve,
    orderbook_pressure_from_fleet,
)


# ── Backwardation: rallying spot + light orderbook ────────────────────────────

def test_rally_light_orderbook_is_backwardation():
    c = build_forward_curve(1847.0, momentum=0.85, orderbook_deliveries=0.05)
    assert c.shape == "BACKWARDATION"
    # Every forward sits BELOW spot.
    assert all(fw < c.spot for fw in c.forwards)
    # Basis (spot − front forward) is positive under backwardation.
    assert c.basis > 0
    # Roll yield positive: a forward rolls UP toward a higher spot.
    assert c.roll_yield > 0
    # Sharp rally → fade signal on the long-spot side.
    assert c.fade_signal is True


def test_backwardation_curve_slopes_down_in_tenor():
    # With momentum dominating and no supply, forwards fall monotonically as
    # tenor lengthens (deeper depth → bigger downward adjustment).
    c = build_forward_curve(2000.0, momentum=0.9, orderbook_deliveries=0.0)
    fwds = list(c.forwards)
    assert fwds == sorted(fwds, reverse=True)
    assert all(a > b for a, b in zip(fwds, fwds[1:]))


# ── Contango: weak spot + heavy near deliveries ───────────────────────────────

def test_weak_spot_heavy_deliveries_is_contango():
    c = build_forward_curve(1847.0, momentum=0.15, orderbook_deliveries=0.9)
    assert c.shape == "CONTANGO"
    # Every forward sits ABOVE spot.
    assert all(fw > c.spot for fw in c.forwards)
    # Basis negative under contango.
    assert c.basis < 0
    # Roll yield negative: a forward rolls DOWN toward a lower spot.
    assert c.roll_yield < 0
    # Contango is not a long-spot fade signal.
    assert c.fade_signal is False


def test_contango_curve_slopes_up_in_tenor():
    c = build_forward_curve(1500.0, momentum=0.1, orderbook_deliveries=1.0)
    fwds = list(c.forwards)
    assert fwds == sorted(fwds)
    assert all(a < b for a, b in zip(fwds, fwds[1:]))


# ── Flat / neutral ────────────────────────────────────────────────────────────

def test_neutral_momentum_no_orderbook_is_flat_at_spot():
    c = build_forward_curve(1847.0, momentum=0.5, orderbook_deliveries=0.0)
    assert c.shape == "FLAT"
    assert all(math.isclose(fw, c.spot) for fw in c.forwards)
    assert c.basis == pytest.approx(0.0)
    assert c.roll_yield == 0.0
    assert c.fade_signal is False
    assert c.degenerate is False


def test_opposing_legs_net_to_flat():
    # A rally (pulls forwards down) into a heavy orderbook (pushes them up) can
    # roughly cancel → an honest FLAT label, not a forced direction.
    c = build_forward_curve(1847.0, momentum=0.85, orderbook_deliveries=0.9)
    assert c.shape == "FLAT"
    assert c.roll_yield == 0.0


# ── Degenerate / empty inputs → flat, no crash ────────────────────────────────

@pytest.mark.parametrize("bad_spot", [0.0, -100.0, float("nan"), float("inf"), None])
def test_bad_spot_returns_flat_degenerate_no_crash(bad_spot):
    c = build_forward_curve(bad_spot, momentum=0.8, orderbook_deliveries=0.5)
    assert c.shape == "FLAT"
    assert c.degenerate is True
    assert c.roll_yield == 0.0
    assert c.basis == 0.0
    # Flat at the (sanitized) spot — all forwards equal to spot.
    assert all(fw == c.spot for fw in c.forwards)
    assert c.fade_signal is False


def test_non_finite_momentum_and_pressure_are_sanitized():
    c = build_forward_curve(1847.0, momentum=float("nan"), orderbook_deliveries=float("nan"))
    # nan momentum → neutral 0.5, nan pressure → 0.0 → flat curve at spot.
    assert c.momentum == 0.5
    assert c.orderbook_pressure == 0.0
    assert c.shape == "FLAT"


def test_out_of_range_inputs_clamped():
    c = build_forward_curve(1000.0, momentum=5.0, orderbook_deliveries=-3.0)
    assert c.momentum == 1.0      # clamped to [0,1]
    assert c.orderbook_pressure == 0.0
    assert c.shape == "BACKWARDATION"  # max rally, no supply → backwardation


def test_empty_tenors_falls_back_to_default():
    c = build_forward_curve(1847.0, 0.8, 0.0, tenors=())
    assert c.tenors_months == (1, 3, 6, 12)


def test_tenors_sorted_and_deduped():
    c = build_forward_curve(1847.0, 0.5, 0.0, tenors=(12, 3, 3, 1, 6))
    assert c.tenors_months == (1, 3, 6, 12)


# ── Arithmetic on a hand example ──────────────────────────────────────────────

def test_basis_and_roll_yield_hand_example():
    # spot=1000, momentum=1.0 (max rally), no supply, tenors (1,3,6,12).
    # momentum_tilt = (1.0-0.5)*2 = 1.0
    # front tenor t=1, T=12 → depth = 1/12.
    # momentum_adj = -1.0 * 0.08 * (1/12) = -0.08/12 = -0.0066667
    # front_fwd = 1000 * (1 - 0.0066667) = 993.3333
    c = build_forward_curve(1000.0, momentum=1.0, orderbook_deliveries=0.0,
                            tenors=(1, 3, 6, 12))
    expected_front = 1000.0 * (1.0 - _MAX_MOMENTUM_TILT * (1.0 / 12.0))
    assert c.forwards[0] == pytest.approx(expected_front, rel=1e-9)

    # basis = spot - front_fwd
    expected_basis = 1000.0 - expected_front
    assert c.basis == pytest.approx(expected_basis, rel=1e-9)

    # roll_yield = (spot/front_fwd - 1) / front_tenor_yr, front_tenor_yr = 1/12.
    expected_roll = (1000.0 / expected_front - 1.0) / (1.0 / 12.0)
    assert c.roll_yield == pytest.approx(expected_roll, rel=1e-9)

    # Long tenor (12m) gets the full cap: 1000 * (1 - 0.08) = 920.
    assert c.forwards[-1] == pytest.approx(1000.0 * (1.0 - _MAX_MOMENTUM_TILT), rel=1e-9)


def test_supply_cap_at_long_tenor():
    # Max supply, neutral momentum → 12m forward lifted by exactly _MAX_SUPPLY_DRAG.
    c = build_forward_curve(1000.0, momentum=0.5, orderbook_deliveries=1.0,
                            tenors=(1, 3, 6, 12))
    assert c.forwards[-1] == pytest.approx(1000.0 * (1.0 + _MAX_SUPPLY_DRAG), rel=1e-9)
    assert c.shape == "CONTANGO"


# ── Orderbook pressure mapping ────────────────────────────────────────────────

def test_orderbook_pressure_ratio_and_saturation():
    # delivery_ratio / 0.125, clamped to [0,1].
    # 3.1 / 28.5 = 0.10877 → /0.125 = 0.870
    assert orderbook_pressure_from_fleet(3.1, 28.5) == pytest.approx(0.870, abs=1e-3)
    # Saturates at a heavy wave.
    assert orderbook_pressure_from_fleet(5.0, 28.5) == 1.0
    # No incoming supply.
    assert orderbook_pressure_from_fleet(0.0, 28.5) == 0.0


@pytest.mark.parametrize("d,c", [(float("nan"), 28.5), (3.1, 0.0), (3.1, -1.0), (1.0, float("inf"))])
def test_orderbook_pressure_bad_inputs_zero(d, c):
    assert orderbook_pressure_from_fleet(d, c) == 0.0


def test_orderbook_pressure_matches_live_fleet_snapshot():
    from processing.fleet_tracker import get_fleet_data
    fleet = get_fleet_data()
    p = orderbook_pressure_from_fleet(
        fleet.deliveries_next_12m_teu_m, fleet.total_teu_capacity_m,
    )
    assert 0.0 <= p <= 1.0


# ── Provenance: MODELED, never live ───────────────────────────────────────────

def test_curve_provenance_is_modeled():
    c = build_forward_curve(1847.0, 0.8, 0.1)
    assert c.source is MODELED_CURVE_SOURCE
    assert c.source.kind == DataKind.MODELED
    assert c.source.quality == DataQuality.MODELED
    # The honesty contract: it must NOT claim to be a live feed.
    assert c.source.kind != DataKind.LIVE
    assert "NOT a live FFA" in c.source.notes


def test_returns_forwardcurve_dataclass():
    c = build_forward_curve(1847.0, 0.6, 0.2)
    assert isinstance(c, ForwardCurve)
    assert len(c.forwards) == len(c.tenors_months)
    assert c.as_points() == list(zip(c.tenors_months, c.forwards))


# ── Tab smoke ─────────────────────────────────────────────────────────────────

def test_tab_derivatives_imports():
    import ui.tab_derivatives as tab
    assert hasattr(tab, "render")
    assert hasattr(tab, "_render_forward_curve")
    # The frozen mock facade is retired.
    assert not hasattr(tab, "_FFA_CURVE")


def test_resolve_curve_inputs_no_data_uses_demo_anchor():
    import ui.tab_derivatives as tab
    spot, momentum, pressure, is_real = tab._resolve_curve_inputs(None, None)
    assert is_real is False
    assert spot == tab._DEMO_SPOT_BDI
    assert momentum == 0.5
    # Orderbook pressure still derives from the (real baseline) fleet snapshot.
    assert 0.0 <= pressure <= 1.0


def test_resolve_curve_inputs_real_bdi(mock_streamlit):
    """A populated, rising BDI series anchors a REAL spot + rally momentum."""
    import numpy as np
    import pandas as pd

    import ui.tab_derivatives as tab

    dates = pd.date_range("2025-01-01", periods=120, freq="D")
    rising = np.linspace(1400.0, 2100.0, 120)
    macro = {"BSXRLM": pd.DataFrame({"date": dates, "value": rising})}

    spot, momentum, pressure, is_real = tab._resolve_curve_inputs(macro, {})
    assert is_real is True
    assert spot == pytest.approx(2100.0, rel=1e-6)   # latest BDI print
    assert momentum > 0.5                            # a rally
    assert 0.0 <= pressure <= 1.0


def test_render_empty_and_populated_do_not_crash(mock_streamlit):
    """Both the honest empty-state and a populated render must not raise."""
    import numpy as np
    import pandas as pd

    import ui.tab_derivatives as tab

    # Empty → honest empty-state path.
    tab.render(None, None, None)

    # Populated with real BDI → modeled curve path.
    dates = pd.date_range("2025-01-01", periods=120, freq="D")
    macro = {"BSXRLM": pd.DataFrame(
        {"date": dates, "value": np.linspace(1400.0, 2100.0, 120)})}
    tab.render(None, {}, macro)
