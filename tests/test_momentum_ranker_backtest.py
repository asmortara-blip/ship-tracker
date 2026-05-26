"""Defining-property tests for engine/momentum_ranker_backtest.py.

Pins the per-signal-class scorecard contract: deterministic, bounded,
one scorecard per signal class, monotonic ladder on high-quality
synthetic history.
"""
from __future__ import annotations

import pytest

from engine.momentum_ranker_backtest import (
    MOMENTUM_BACKTEST_SOURCE,
    SIGNAL_CLASSES,
    MomentumBacktestReport,
    MomentumSignalScorecard,
    backtest_momentum_signals,
    synthesize_momentum_history,
)


# ── 1. Signal-class constant matches engine.momentum_ranker._signal() ──────

def test_signal_classes_exposes_five_canonical_labels() -> None:
    """The five signal classes must match the live ranker's labels."""
    assert set(SIGNAL_CLASSES) == {
        "STRONG_SELL", "SELL", "NEUTRAL", "BUY", "STRONG_BUY",
    }
    # Ordering matters — weakest → strongest for the monotonicity check.
    assert SIGNAL_CLASSES[0]  == "STRONG_SELL"
    assert SIGNAL_CLASSES[-1] == "STRONG_BUY"


# ── 2. Synthetic history shape + determinism ───────────────────────────────

def test_synth_history_is_deterministic() -> None:
    a = synthesize_momentum_history(n_periods=10, n_entities=3, seed=42)
    b = synthesize_momentum_history(n_periods=10, n_entities=3, seed=42)
    assert a == b


def test_synth_history_row_count() -> None:
    rows = synthesize_momentum_history(n_periods=12, n_entities=4)
    assert len(rows) == 48


def test_synth_history_required_keys() -> None:
    rows = synthesize_momentum_history(n_periods=5, n_entities=2)
    required = {"entity_id", "momentum_7d", "momentum_30d", "momentum_90d",
                "composite", "realized_forward_return"}
    for row in rows:
        assert required <= set(row.keys())


def test_synth_quality_zero_yields_no_correlation() -> None:
    """signal_quality=0 means the realized return is pure noise — the
    backtest should NOT see a meaningful spread between STRONG_BUY and
    STRONG_SELL (within sampling tolerance)."""
    report = backtest_momentum_signals(signal_quality=0.0)
    # On a 60-period × 8-entity noise-only sample the spread will be
    # small but not exactly zero. Cap it at a generous threshold —
    # 5pp is well within sampling noise for n=480 random observations.
    assert abs(report.spread_strong_vs_weak) < 0.05


# ── 3. Backtest returns one scorecard per signal class ─────────────────────

def test_backtest_returns_one_scorecard_per_signal_class() -> None:
    report = backtest_momentum_signals()
    assert isinstance(report, MomentumBacktestReport)
    by_sig = {sc.signal for sc in report.scorecards}
    assert by_sig == set(SIGNAL_CLASSES)


def test_backtest_uses_synthetic_history_when_none_given() -> None:
    """Empty / None history must not crash — synth generator backfills."""
    report = backtest_momentum_signals(history=None)
    assert report.n_observations > 0

    report_empty = backtest_momentum_signals(history=[])
    assert report_empty.n_observations > 0


# ── 4. Numeric bounds ─────────────────────────────────────────────────────

def test_directional_hit_rate_in_unit_interval() -> None:
    """Hit rates must sit in [0, 1] for every class."""
    report = backtest_momentum_signals()
    for sc in report.scorecards:
        assert 0.0 <= sc.directional_hit_rate <= 1.0
        assert abs(sc.edge_vs_baseline - (sc.directional_hit_rate - 0.5)) < 1e-9


def test_neutral_class_hit_rate_is_exactly_half() -> None:
    """NEUTRAL has no directional claim — hit rate by convention is 0.5."""
    report = backtest_momentum_signals()
    neutral = next(sc for sc in report.scorecards if sc.signal == "NEUTRAL")
    assert neutral.directional_hit_rate == 0.5
    assert neutral.edge_vs_baseline == 0.0


# ── 5. High-quality synth produces a monotonic ladder ─────────────────────

def test_high_quality_synth_yields_monotonic_signal_ladder() -> None:
    """signal_quality=0.95 means realized returns closely track the
    composite signal. The mean forward return must rise monotonically
    from STRONG_SELL → STRONG_BUY — this is the load-bearing property
    that proves the backtest engine is working."""
    report = backtest_momentum_signals(signal_quality=0.95)
    assert report.monotonic_by_signal is True
    # And the STRONG_BUY → STRONG_SELL spread must be meaningfully positive
    assert report.spread_strong_vs_weak > 0.10  # at least 10pp


def test_high_quality_synth_strong_buy_outscores_strong_sell_on_hit_rate() -> None:
    """STRONG_BUY's directional hit rate (positive returns hit) should be
    well above STRONG_SELL's (negative returns hit) at high quality."""
    report = backtest_momentum_signals(signal_quality=0.95)
    by_sig = {sc.signal: sc for sc in report.scorecards}
    # Both are far better than the 0.5 baseline when the signal is good
    assert by_sig["STRONG_BUY"].directional_hit_rate  > 0.65
    assert by_sig["STRONG_SELL"].directional_hit_rate > 0.65


# ── 6. End-to-end determinism ─────────────────────────────────────────────

def test_backtest_is_deterministic_across_runs() -> None:
    a = backtest_momentum_signals(seed=7)
    b = backtest_momentum_signals(seed=7)
    a_keys = {(sc.signal, round(sc.mean_forward_return, 6),
               round(sc.directional_hit_rate, 6))
              for sc in a.scorecards}
    b_keys = {(sc.signal, round(sc.mean_forward_return, 6),
               round(sc.directional_hit_rate, 6))
              for sc in b.scorecards}
    assert a_keys == b_keys
    assert a.summary == b.summary
    assert a.monotonic_by_signal == b.monotonic_by_signal


# ── 7. Hand-built history yields exact arithmetic ─────────────────────────

def test_backtest_hand_built_history_groups_correctly_by_signal() -> None:
    """A small hand-built history must bucket cleanly into signal classes
    using the live signal thresholds (composite > 0.15 → STRONG_BUY, etc).
    Each bucket's mean_forward_return must equal the input mean."""
    history = [
        # STRONG_BUY (composite > 0.15) with positive realized
        {"composite": 0.20, "realized_forward_return": 0.10},
        {"composite": 0.25, "realized_forward_return": 0.08},
        # STRONG_SELL (composite < -0.15) with negative realized
        {"composite": -0.22, "realized_forward_return": -0.07},
        {"composite": -0.18, "realized_forward_return": -0.05},
        # NEUTRAL (|composite| <= 0.05) with mild noise
        {"composite":  0.01, "realized_forward_return": 0.01},
        {"composite": -0.02, "realized_forward_return": -0.01},
    ]
    report = backtest_momentum_signals(history=history)
    by_sig = {sc.signal: sc for sc in report.scorecards}

    # STRONG_BUY: mean of (0.10, 0.08) = 0.09
    assert by_sig["STRONG_BUY"].n_observations == 2
    assert abs(by_sig["STRONG_BUY"].mean_forward_return - 0.09) < 1e-9
    # STRONG_BUY hit rate: both realized > 0 → 100% hit rate
    assert by_sig["STRONG_BUY"].directional_hit_rate == 1.0

    # STRONG_SELL: mean of (-0.07, -0.05) = -0.06
    assert by_sig["STRONG_SELL"].n_observations == 2
    assert abs(by_sig["STRONG_SELL"].mean_forward_return - (-0.06)) < 1e-9
    # STRONG_SELL hit rate: both realized < 0 → 100% hit rate
    assert by_sig["STRONG_SELL"].directional_hit_rate == 1.0

    # NEUTRAL: by convention always 0.5 hit rate
    assert by_sig["NEUTRAL"].n_observations == 2
    assert by_sig["NEUTRAL"].directional_hit_rate == 0.5

    # And the spread is strong_buy_mean - strong_sell_mean
    assert abs(report.spread_strong_vs_weak - (0.09 - (-0.06))) < 1e-9
