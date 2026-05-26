"""Defining-property tests for processing/leading_indicators_backtest.py."""
from __future__ import annotations

import pytest

from processing.leading_indicators_backtest import (
    INDICATOR_SIGNALS,
    IndicatorSignalScorecard,
    LeadingIndicatorsBacktestReport,
    backtest_leading_indicators,
    synthesize_indicator_history,
)


def test_indicator_signals_constant() -> None:
    assert set(INDICATOR_SIGNALS) == {"BULLISH", "BEARISH", "NEUTRAL"}


def test_synth_history_is_deterministic() -> None:
    a = synthesize_indicator_history(n_periods=20, n_indicators=3, seed=42)
    b = synthesize_indicator_history(n_periods=20, n_indicators=3, seed=42)
    assert a == b


def test_synth_history_row_count() -> None:
    rows = synthesize_indicator_history(n_periods=15, n_indicators=4)
    assert len(rows) == 60


def test_synth_history_required_keys() -> None:
    rows = synthesize_indicator_history(n_periods=5, n_indicators=2)
    required = {"series_id", "signal", "realized_forward_demand_pct"}
    for row in rows:
        assert required <= set(row.keys())
        assert row["signal"] in INDICATOR_SIGNALS


def test_backtest_returns_one_scorecard_per_signal() -> None:
    report = backtest_leading_indicators()
    assert isinstance(report, LeadingIndicatorsBacktestReport)
    assert {sc.signal for sc in report.scorecards} == set(INDICATOR_SIGNALS)


def test_backtest_uses_synth_when_history_empty() -> None:
    a = backtest_leading_indicators(history=None)
    b = backtest_leading_indicators(history=[])
    assert a.n_observations > 0
    assert b.n_observations > 0


def test_hit_rates_in_unit_interval() -> None:
    report = backtest_leading_indicators()
    for sc in report.scorecards:
        assert 0.0 <= sc.directional_hit_rate <= 1.0
        assert abs(sc.edge_vs_baseline - (sc.directional_hit_rate - 0.5)) < 1e-9


def test_neutral_pins_at_half() -> None:
    """NEUTRAL has no directional claim — hit rate must be exactly 0.5."""
    report = backtest_leading_indicators()
    neutral = next(sc for sc in report.scorecards if sc.signal == "NEUTRAL")
    assert neutral.directional_hit_rate == 0.5


def test_perfect_signal_quality_flips_calibrated_true() -> None:
    """signal_quality=1.0 → signals_calibrated=True + meaningful spread."""
    report = backtest_leading_indicators(signal_quality=1.0)
    assert report.signals_calibrated is True
    assert report.spread_bullish_vs_bearish > 0.03


def test_zero_signal_quality_pins_signals_near_zero() -> None:
    """signal_quality=0 → BULLISH/BEARISH means in the noise band."""
    report = backtest_leading_indicators(signal_quality=0.0)
    by_s = {sc.signal: sc for sc in report.scorecards}
    assert abs(by_s["BULLISH"].mean_forward_demand_pct) < 0.015
    assert abs(by_s["BEARISH"].mean_forward_demand_pct) < 0.015


def test_backtest_is_deterministic_across_runs() -> None:
    a = backtest_leading_indicators(seed=7)
    b = backtest_leading_indicators(seed=7)
    a_keys = {(sc.signal, round(sc.mean_forward_demand_pct, 6))
              for sc in a.scorecards}
    b_keys = {(sc.signal, round(sc.mean_forward_demand_pct, 6))
              for sc in b.scorecards}
    assert a_keys == b_keys
    assert a.summary == b.summary


def test_hand_built_history_yields_exact_arithmetic() -> None:
    history = [
        {"series_id": "X", "signal": "BULLISH",
         "realized_forward_demand_pct":  0.04},
        {"series_id": "X", "signal": "BULLISH",
         "realized_forward_demand_pct":  0.06},
        {"series_id": "X", "signal": "BEARISH",
         "realized_forward_demand_pct": -0.05},
        {"series_id": "X", "signal": "NEUTRAL",
         "realized_forward_demand_pct":  0.01},
    ]
    report = backtest_leading_indicators(history=history)
    by_s = {sc.signal: sc for sc in report.scorecards}

    assert by_s["BULLISH"].n_observations == 2
    assert abs(by_s["BULLISH"].mean_forward_demand_pct - 0.05) < 1e-9
    # Both BULLISH realized positive → hit rate 1.0
    assert by_s["BULLISH"].directional_hit_rate == 1.0

    assert by_s["BEARISH"].directional_hit_rate == 1.0
    assert by_s["NEUTRAL"].directional_hit_rate == 0.5

    # Calibrated: BULLISH +0.05 > 0 AND BEARISH -0.05 < 0
    assert report.signals_calibrated is True
    assert abs(report.spread_bullish_vs_bearish - (0.05 - (-0.05))) < 1e-9
