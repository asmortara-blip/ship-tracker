"""Defining-property tests for processing/port_supply_lines_backtest.py."""
from __future__ import annotations

import pytest

from processing.port_supply_lines_backtest import (
    PORT_SUPPLY_LINES_BACKTEST_SOURCE,
    PortStabilityScorecard,
    StabilityReport,
    validate_supply_chain_stability,
)


# ── 1. Output shape ────────────────────────────────────────────────────────

def test_returns_stability_report() -> None:
    r = validate_supply_chain_stability(n_runs=2)
    assert isinstance(r, StabilityReport)
    assert isinstance(r.scorecards, list)
    for sc in r.scorecards:
        assert isinstance(sc, PortStabilityScorecard)
        assert sc.locode and sc.port_name
        assert isinstance(sc.baseline_top_k, list)


def test_one_scorecard_per_port_in_registry() -> None:
    from ports.port_registry import PORTS
    r = validate_supply_chain_stability(n_runs=2)
    assert len(r.scorecards) == len(PORTS)


# ── 2. Numeric ranges ─────────────────────────────────────────────────────

def test_stability_values_in_unit_interval() -> None:
    r = validate_supply_chain_stability(n_runs=4)
    for sc in r.scorecards:
        assert 0.0 <= sc.mean_stability <= 1.0
        assert 0.0 <= sc.min_stability <= 1.0
        # min must be <= mean by definition.
        assert sc.min_stability <= sc.mean_stability + 1e-9
    assert 0.0 <= r.overall_mean_stability <= 1.0
    assert 0.0 <= r.overall_min_stability <= 1.0


# ── 3. Determinism ────────────────────────────────────────────────────────

def test_validator_is_deterministic_across_runs() -> None:
    """Same seed → byte-identical scorecards."""
    a = validate_supply_chain_stability(n_runs=3, seed=42)
    b = validate_supply_chain_stability(n_runs=3, seed=42)
    a_keys = {(sc.locode, sc.mean_stability) for sc in a.scorecards}
    b_keys = {(sc.locode, sc.mean_stability) for sc in b.scorecards}
    assert a_keys == b_keys
    assert a.overall_mean_stability == b.overall_mean_stability
    assert a.summary == b.summary


# ── 4. Noise knob load-bearing properties ────────────────────────────────

def test_zero_noise_yields_perfect_stability() -> None:
    """Zero-noise perturbation must reproduce the baseline exactly →
    every port reads 1.0 stability."""
    r = validate_supply_chain_stability(n_runs=3, noise=0.0)
    for sc in r.scorecards:
        assert sc.mean_stability == 1.0
        assert sc.min_stability == 1.0
    assert r.overall_mean_stability == 1.0
    assert r.stable is True


def test_heavier_noise_degrades_stability() -> None:
    """Higher noise → equal or lower mean stability (monotonicity).

    Empirically the synth-derived join has very high stability even at
    ±15% noise (~99% per-port mean) because the cargo mix is dominated
    by a few large weights. So the relationship is monotonic but the
    differences can be small — this test just pins direction."""
    light = validate_supply_chain_stability(n_runs=4, noise=0.05, seed=42)
    heavy = validate_supply_chain_stability(n_runs=4, noise=0.50, seed=42)
    assert heavy.overall_mean_stability <= light.overall_mean_stability + 1e-9


# ── 5. Roll-up flag thresholding ─────────────────────────────────────────

def test_stable_flag_uses_threshold() -> None:
    """The stable flag is True iff overall_mean_stability >= threshold."""
    # On the default synth the join is very stable, so a tight threshold
    # should still pass and a 99.9% threshold should fail.
    r_pass = validate_supply_chain_stability(
        n_runs=2, stability_threshold=0.65,
    )
    assert r_pass.stable is True

    r_fail = validate_supply_chain_stability(
        n_runs=2, stability_threshold=0.999,
    )
    # On the default synth the overall mean sits at ~0.998 which is
    # below 0.999 → stable should flip to False.
    assert r_fail.stable is False


# ── 6. Scorecards ordered weakest-first ──────────────────────────────────

def test_scorecards_ordered_weakest_first() -> None:
    r = validate_supply_chain_stability(n_runs=3)
    means = [sc.mean_stability for sc in r.scorecards]
    assert means == sorted(means)


# ── 7. top_k + n_runs parameter contracts ───────────────────────────────

def test_top_k_caps_baseline_list() -> None:
    r = validate_supply_chain_stability(n_runs=2, top_k=3)
    for sc in r.scorecards:
        assert len(sc.baseline_top_k) <= 3


def test_n_runs_propagates_into_scorecard() -> None:
    r = validate_supply_chain_stability(n_runs=5)
    for sc in r.scorecards:
        assert sc.n_runs == 5


# ── 8. Provenance marker ─────────────────────────────────────────────────

def test_source_marker_present() -> None:
    assert PORT_SUPPLY_LINES_BACKTEST_SOURCE is not None
    assert getattr(
        PORT_SUPPLY_LINES_BACKTEST_SOURCE, "name", "",
    ) == "Port Supply Lines Backtest"
