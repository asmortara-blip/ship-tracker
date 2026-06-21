"""Tests for R050 — bunker-adjusted net freight (TCE proxy) anchored on crude.

Covers the arithmetic + units on a hand example, monotonicity in crude, the
'gross up, net down' divergence flag, the offline-safe modeled fallback (with
honest provenance + no raise), and the honest-negative-net case.
"""
from __future__ import annotations

import pytest

from routes.rate_estimator import (
    NetFreight,
    NetFreightDivergence,
    compute_net_freight,
    crude_to_bunker_usd_per_mt,
    net_freight_divergence,
    _FUEL_CONSUMPTION_MT_PER_NM,
    _LOADED_FEU,
    _MODELED_CRUDE_USD_PER_BBL,
)
from processing.carbon_calculator import ROUTE_DISTANCES


# ── Crude → bunker heuristic ──────────────────────────────────────────────────

def test_crude_to_bunker_heuristic_arithmetic() -> None:
    # bunker = crude × 6.35 + 95
    assert crude_to_bunker_usd_per_mt(72.0) == pytest.approx(72.0 * 6.35 + 95.0)
    assert crude_to_bunker_usd_per_mt(100.0) == pytest.approx(100.0 * 6.35 + 95.0)


def test_crude_to_bunker_never_negative() -> None:
    # A nonsensical negative crude must not yield a negative bunker price.
    assert crude_to_bunker_usd_per_mt(-1000.0) == 0.0


# ── compute_net_freight: arithmetic + units (hand example) ───────────────────

def test_net_freight_hand_example_units() -> None:
    """Verify net = gross − fuel_leg on a fully hand-computed example.

    transpacific_eb = 5500 nm; crude $72/bbl; gross $2500/FEU.
      bunker      = 72 × 6.35 + 95           = 552.2 $/MT
      voyage fuel = 5500 × (85/480) × 552.2  $  (whole vessel)
      fuel_leg    = voyage fuel / 3400 FEU   $/FEU
      net         = 2500 − fuel_leg
    """
    dist = ROUTE_DISTANCES["transpacific_eb"]  # 5500
    bunker = 72.0 * 6.35 + 95.0
    expected_fuel_leg = dist * _FUEL_CONSUMPTION_MT_PER_NM * bunker / _LOADED_FEU
    expected_net = 2500.0 - expected_fuel_leg

    nf = compute_net_freight("transpacific_eb", 2500.0, crude_usd_per_bbl=72.0)

    assert isinstance(nf, NetFreight)
    assert nf.distance_nm == dist
    assert nf.bunker_usd_per_mt == pytest.approx(bunker)
    assert nf.fuel_leg_usd_per_feu == pytest.approx(expected_fuel_leg)
    assert nf.net_freight_usd_per_feu == pytest.approx(expected_net)
    # Net = gross − fuel_leg, exactly.
    assert nf.net_freight_usd_per_feu == pytest.approx(
        nf.gross_rate_usd_per_feu - nf.fuel_leg_usd_per_feu
    )
    assert nf.crude_provenance == "real"
    # Margin = net / gross.
    assert nf.net_margin_pct == pytest.approx(expected_net / 2500.0)


def test_net_freight_loaded_feu_is_3400() -> None:
    # 8000 TEU × 0.85 load ÷ 2 TEU/FEU = 3400 FEU.
    assert _LOADED_FEU == pytest.approx(3400.0)


# ── Monotonicity in crude ─────────────────────────────────────────────────────

def test_higher_crude_larger_fuel_leg_smaller_net() -> None:
    lo = compute_net_freight("asia_europe", 4000.0, crude_usd_per_bbl=60.0)
    hi = compute_net_freight("asia_europe", 4000.0, crude_usd_per_bbl=120.0)

    assert hi.bunker_usd_per_mt > lo.bunker_usd_per_mt
    assert hi.fuel_leg_usd_per_feu > lo.fuel_leg_usd_per_feu
    # Gross is held fixed, so a bigger fuel leg means a smaller net.
    assert hi.net_freight_usd_per_feu < lo.net_freight_usd_per_feu


def test_longer_route_larger_fuel_leg() -> None:
    short = compute_net_freight("intra_asia_china_japan", 1000.0, crude_usd_per_bbl=72.0)  # 600 nm
    long = compute_net_freight("med_hub_to_asia", 1000.0, crude_usd_per_bbl=72.0)          # 13500 nm
    assert long.fuel_leg_usd_per_feu > short.fuel_leg_usd_per_feu


# ── Offline-safe modeled fallback ─────────────────────────────────────────────

def test_modeled_fallback_when_crude_missing() -> None:
    nf = compute_net_freight("transatlantic", 1800.0, crude_usd_per_bbl=None)
    assert nf.crude_provenance == "modeled"
    assert nf.crude_usd_per_bbl == pytest.approx(_MODELED_CRUDE_USD_PER_BBL)
    # Still produces a usable net figure, no raise.
    assert nf.fuel_leg_usd_per_feu > 0.0


def test_non_positive_crude_falls_back_to_modeled() -> None:
    # Zero or negative crude must NOT be trusted as a real print.
    for bad in (0.0, -5.0):
        nf = compute_net_freight("transatlantic", 1800.0, crude_usd_per_bbl=bad)
        assert nf.crude_provenance == "modeled"
        assert nf.crude_usd_per_bbl == pytest.approx(_MODELED_CRUDE_USD_PER_BBL)


def test_unknown_route_does_not_raise() -> None:
    # No distance data → fuel leg 0, net == gross. Honest 'no data' outcome.
    nf = compute_net_freight("__nonexistent__", 2000.0, crude_usd_per_bbl=72.0)
    assert nf.distance_nm == 0.0
    assert nf.fuel_leg_usd_per_feu == 0.0
    assert nf.net_freight_usd_per_feu == pytest.approx(2000.0)


# ── Honest-negative net (fuel-eaten route) ────────────────────────────────────

def test_net_can_go_negative_and_is_surfaced() -> None:
    """A long route at high crude with a thin gross rate → negative net.

    The figure must NOT be clamped to zero — a fuel-eaten route is shown
    honestly.
    """
    nf = compute_net_freight("med_hub_to_asia", 500.0, crude_usd_per_bbl=150.0)
    assert nf.net_freight_usd_per_feu < 0.0
    assert nf.fuel_leg_usd_per_feu > nf.gross_rate_usd_per_feu


# ── Divergence: 'gross up, net down' ──────────────────────────────────────────

def test_divergence_flags_fuel_eaten_rally() -> None:
    """Gross rises modestly but crude rallies hard → net falls → diverged."""
    dv = net_freight_divergence(
        "asia_europe", 3000.0, 3200.0,
        crude_start_usd_per_bbl=60.0, crude_end_usd_per_bbl=150.0,
    )
    assert isinstance(dv, NetFreightDivergence)
    assert dv.diverged is True
    assert dv.gross_change_usd_per_feu == pytest.approx(200.0)
    assert dv.net_change_usd_per_feu < 0.0
    assert dv.crude_provenance == "real"


def test_no_divergence_when_crude_flat() -> None:
    """Same rally, flat crude → net rises with gross → NOT diverged."""
    dv = net_freight_divergence(
        "asia_europe", 3000.0, 3200.0,
        crude_start_usd_per_bbl=60.0, crude_end_usd_per_bbl=60.0,
    )
    assert dv.diverged is False
    assert dv.net_change_usd_per_feu == pytest.approx(200.0)


def test_no_divergence_when_gross_below_threshold() -> None:
    """A tiny gross move (below the rally threshold) is never a divergence."""
    dv = net_freight_divergence(
        "asia_europe", 3000.0, 3010.0,  # +$10 < $50 default threshold
        crude_start_usd_per_bbl=60.0, crude_end_usd_per_bbl=200.0,
    )
    assert dv.diverged is False


def test_divergence_provenance_modeled_if_any_endpoint_missing() -> None:
    dv = net_freight_divergence(
        "asia_europe", 3000.0, 3200.0,
        crude_start_usd_per_bbl=60.0, crude_end_usd_per_bbl=None,
    )
    assert dv.crude_provenance == "modeled"


def test_divergence_never_raises_on_unknown_route() -> None:
    dv = net_freight_divergence(
        "__nope__", 3000.0, 3600.0,
        crude_start_usd_per_bbl=60.0, crude_end_usd_per_bbl=150.0,
    )
    # Unknown route → fuel leg 0 both ends → net tracks gross → gross up, net
    # also up → NOT diverged. No raise.
    assert dv.diverged is False
    assert dv.gross_change_usd_per_feu == pytest.approx(600.0)
