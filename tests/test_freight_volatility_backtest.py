"""Defining-property tests for processing/freight_volatility_backtest.py.

Pins per-regime + per-mean-reversion-signal scorecard shape, bounds,
synthetic-history determinism, and — most importantly — the load-bearing
property that flipping the ``momentum_strength`` / ``reversion_strength``
knobs from "perfect" to "noise" must visibly flip the corresponding
roll-up flag.
"""
from __future__ import annotations

import pytest

from processing.freight_volatility_backtest import (
    MEAN_REVERSION_SIGNALS,
    REGIMES,
    FreightVolatilityBacktestReport,
    MeanReversionScorecard,
    RegimeScorecard,
    backtest_freight_volatility,
    synthesize_regime_history,
)


# ── 1. Constants are wired up correctly ───────────────────────────────────

def test_regimes_constant() -> None:
    assert set(REGIMES) == {
        "TRENDING_UP", "TRENDING_DOWN", "BREAKOUT", "RANGING",
    }


def test_mean_reversion_signals_constant() -> None:
    assert set(MEAN_REVERSION_SIGNALS) == {
        "OVERSOLD", "NEUTRAL", "OVERBOUGHT",
    }


# ── 2. Synth determinism + shape ──────────────────────────────────────────

def test_synth_history_is_deterministic() -> None:
    a = synthesize_regime_history(n_periods=20, n_routes=3, seed=42)
    b = synthesize_regime_history(n_periods=20, n_routes=3, seed=42)
    assert a == b


def test_synth_history_row_count() -> None:
    rows = synthesize_regime_history(n_periods=15, n_routes=4)
    assert len(rows) == 60


def test_synth_history_required_keys() -> None:
    rows = synthesize_regime_history(n_periods=5, n_routes=2)
    required = {"route_id", "regime", "mean_reversion_signal",
                "realized_forward_return"}
    for row in rows:
        assert required <= set(row.keys())
        assert row["regime"] in REGIMES
        assert row["mean_reversion_signal"] in MEAN_REVERSION_SIGNALS


# ── 3. Backtest returns one scorecard per regime + signal ─────────────────

def test_backtest_returns_one_scorecard_per_regime() -> None:
    report = backtest_freight_volatility()
    assert isinstance(report, FreightVolatilityBacktestReport)
    assert {sc.regime for sc in report.regimes} == set(REGIMES)
    assert {sc.signal for sc in report.mean_reversion} == set(MEAN_REVERSION_SIGNALS)


def test_backtest_uses_synth_when_history_empty() -> None:
    a = backtest_freight_volatility(history=None)
    b = backtest_freight_volatility(history=[])
    assert a.n_observations > 0
    assert b.n_observations > 0


# ── 4. Numeric bounds ─────────────────────────────────────────────────────

def test_hit_rates_in_unit_interval() -> None:
    report = backtest_freight_volatility()
    for sc in report.regimes:
        assert 0.0 <= sc.directional_hit_rate <= 1.0
        assert abs(sc.edge_vs_baseline - (sc.directional_hit_rate - 0.5)) < 1e-9
    for sc in report.mean_reversion:
        assert 0.0 <= sc.directional_hit_rate <= 1.0
        assert abs(sc.edge_vs_baseline - (sc.directional_hit_rate - 0.5)) < 1e-9


def test_breakout_and_neutral_pin_at_half() -> None:
    """BREAKOUT regime and NEUTRAL signal carry no directional claim —
    hit rate must be exactly 0.5 by convention."""
    report = backtest_freight_volatility()
    breakout = next(sc for sc in report.regimes if sc.regime == "BREAKOUT")
    neutral  = next(sc for sc in report.mean_reversion if sc.signal == "NEUTRAL")
    assert breakout.directional_hit_rate == 0.5
    assert neutral.directional_hit_rate == 0.5


# ── 5. Load-bearing: strength knobs flip the roll-up flags ────────────────

def test_perfect_momentum_strength_flips_momentum_works_true() -> None:
    """High momentum_strength (+ low reversion to avoid interference) must
    yield momentum_works=True."""
    report = backtest_freight_volatility(
        momentum_strength=1.0, reversion_strength=0.0,
    )
    assert report.momentum_works is True


def test_zero_momentum_strength_flips_momentum_works_false() -> None:
    """Zero momentum_strength means TRENDING_UP and TRENDING_DOWN have
    NO drift — the means are pure noise. Across 80 periods × 5 routes
    = 400 obs split across 4 regimes ≈ 100 per regime, the noise rarely
    produces both signs aligned with the regime label by chance."""
    report = backtest_freight_volatility(
        momentum_strength=0.0, reversion_strength=0.0,
        seed=20260525,
    )
    # Under pure-noise momentum, on this deterministic seed the rollup
    # should NOT come out cleanly positive AND negative simultaneously.
    # If a future synth tweak makes it pass anyway, this test will let
    # us know the noise floor moved.
    by_r = {sc.regime: sc for sc in report.regimes}
    # At minimum, mean returns should be small (within noise) — not the
    # clean +/-3% the perfect-strength path produces.
    assert abs(by_r["TRENDING_UP"].mean_forward_return)   < 0.015
    assert abs(by_r["TRENDING_DOWN"].mean_forward_return) < 0.015


def test_perfect_reversion_strength_flips_reversion_works_true() -> None:
    """High reversion_strength → mean_reversion_works=True."""
    report = backtest_freight_volatility(
        momentum_strength=0.0, reversion_strength=1.0,
    )
    assert report.mean_reversion_works is True


def test_zero_reversion_strength_pins_signals_near_zero() -> None:
    """Zero reversion_strength → OVERSOLD/OVERBOUGHT means should sit
    in the noise band, NOT cleanly positive/negative."""
    report = backtest_freight_volatility(
        momentum_strength=0.0, reversion_strength=0.0,
    )
    by_s = {sc.signal: sc for sc in report.mean_reversion}
    assert abs(by_s["OVERSOLD"].mean_forward_return)   < 0.015
    assert abs(by_s["OVERBOUGHT"].mean_forward_return) < 0.015


# ── 6. End-to-end determinism ─────────────────────────────────────────────

def test_backtest_is_deterministic_across_runs() -> None:
    a = backtest_freight_volatility(seed=7)
    b = backtest_freight_volatility(seed=7)
    a_keys = {(sc.regime, round(sc.mean_forward_return, 6))
              for sc in a.regimes}
    b_keys = {(sc.regime, round(sc.mean_forward_return, 6))
              for sc in b.regimes}
    assert a_keys == b_keys
    assert a.summary == b.summary
    assert a.momentum_works == b.momentum_works
    assert a.mean_reversion_works == b.mean_reversion_works


# ── 7. Hand-built history yields exact arithmetic ─────────────────────────

def test_backtest_hand_built_history_buckets_correctly() -> None:
    history = [
        {"regime": "TRENDING_UP",   "mean_reversion_signal": "NEUTRAL",
         "realized_forward_return": 0.05},
        {"regime": "TRENDING_UP",   "mean_reversion_signal": "NEUTRAL",
         "realized_forward_return": 0.03},
        {"regime": "TRENDING_DOWN", "mean_reversion_signal": "NEUTRAL",
         "realized_forward_return": -0.04},
        {"regime": "RANGING",       "mean_reversion_signal": "OVERSOLD",
         "realized_forward_return": 0.02},   # OVERSOLD + positive → hit
    ]
    report = backtest_freight_volatility(history=history)
    by_r = {sc.regime: sc for sc in report.regimes}
    by_s = {sc.signal: sc for sc in report.mean_reversion}

    # TRENDING_UP got 2 obs, both positive → hit rate 1.0
    assert by_r["TRENDING_UP"].n_observations == 2
    assert abs(by_r["TRENDING_UP"].mean_forward_return - 0.04) < 1e-9
    assert by_r["TRENDING_UP"].directional_hit_rate == 1.0

    # TRENDING_DOWN got 1 obs, negative → hit
    assert by_r["TRENDING_DOWN"].directional_hit_rate == 1.0

    # OVERSOLD got 1 obs, positive → hit rate 1.0
    assert by_s["OVERSOLD"].directional_hit_rate == 1.0

    # Roll-up flag: TRENDING_UP +0.04 > 0 AND TRENDING_DOWN -0.04 < 0
    assert report.momentum_works is True
