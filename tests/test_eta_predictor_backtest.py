"""Defining-property tests for processing/eta_predictor_backtest.py."""
from __future__ import annotations

import pytest

from processing.eta_predictor_backtest import (
    CONGESTION_RISK_LABELS,
    EtaAccuracyReport,
    EtaLabelScorecard,
    backtest_eta_predictor,
    synthesize_eta_history,
)


def test_congestion_risk_labels_constant() -> None:
    """Ordering matters — weakest → strongest for the monotonicity check."""
    assert CONGESTION_RISK_LABELS == ("LOW", "MODERATE", "HIGH", "SEVERE")


def test_synth_history_is_deterministic() -> None:
    a = synthesize_eta_history(n_observations=60, seed=42)
    b = synthesize_eta_history(n_observations=60, seed=42)
    assert a == b


def test_synth_history_required_keys() -> None:
    rows = synthesize_eta_history(n_observations=20)
    required = {"congestion_risk_label", "predicted_delay_days",
                "realized_delay_days"}
    for row in rows:
        assert required <= set(row.keys())
        assert row["congestion_risk_label"] in CONGESTION_RISK_LABELS


def test_backtest_returns_one_scorecard_per_label() -> None:
    report = backtest_eta_predictor()
    assert isinstance(report, EtaAccuracyReport)
    labels = {sc.label for sc in report.label_scorecards}
    assert labels == set(CONGESTION_RISK_LABELS)


def test_backtest_uses_synth_when_history_empty() -> None:
    a = backtest_eta_predictor(history=None)
    b = backtest_eta_predictor(history=[])
    assert a.n_observations > 0
    assert b.n_observations > 0


def test_delay_mae_is_nonnegative() -> None:
    report = backtest_eta_predictor()
    assert report.delay_mae >= 0.0


def test_sign_agreement_in_unit_interval() -> None:
    report = backtest_eta_predictor()
    assert 0.0 <= report.delay_sign_agreement <= 1.0


def test_perfect_prediction_quality_flips_monotonic_true_and_drives_mae_down() -> None:
    """quality=1.0 → label ladder is strict + MAE collapses (noise=0 inside
    the prediction component)."""
    report = backtest_eta_predictor(prediction_quality=1.0)
    assert report.monotonic_by_label is True
    # Spread between SEVERE and LOW should hit the seeded difference
    # (~8.5 days at quality=1.0, with light per-obs gaussian noise)
    assert report.spread_severe_vs_low > 5.0
    # MAE collapses but won't be exactly zero — realized still has gauss(σ=1)
    assert report.delay_mae < 2.0


def test_zero_prediction_quality_collapses_ladder_and_blows_out_mae() -> None:
    """quality=0 → all labels collapse onto baseline mean → spread ≈ 0;
    prediction is uncorrelated with realized → high MAE."""
    report = backtest_eta_predictor(prediction_quality=0.0)
    assert abs(report.spread_severe_vs_low) < 2.0  # tight noise band
    # MAE should be markedly larger than the quality=1.0 case
    assert report.delay_mae > 1.0


def test_backtest_is_deterministic_across_runs() -> None:
    a = backtest_eta_predictor(seed=7)
    b = backtest_eta_predictor(seed=7)
    a_keys = {(sc.label, round(sc.mean_realized_delay_days, 4))
              for sc in a.label_scorecards}
    b_keys = {(sc.label, round(sc.mean_realized_delay_days, 4))
              for sc in b.label_scorecards}
    assert a_keys == b_keys
    assert a.summary == b.summary
    assert a.monotonic_by_label == b.monotonic_by_label


def test_hand_built_history_yields_exact_arithmetic() -> None:
    history = [
        {"congestion_risk_label": "LOW",      "predicted_delay_days": 1.0,
         "realized_delay_days": 1.0},                                     # MAE 0, sign hit
        {"congestion_risk_label": "LOW",      "predicted_delay_days": 0.0,
         "realized_delay_days": 0.0},                                     # skipped (zeros)
        {"congestion_risk_label": "SEVERE",   "predicted_delay_days": 10.0,
         "realized_delay_days": 12.0},                                    # MAE 2, sign hit
    ]
    report = backtest_eta_predictor(history=history)
    by_l = {sc.label: sc for sc in report.label_scorecards}
    # LOW: 2 obs, mean realized = (1.0 + 0.0) / 2 = 0.5
    assert by_l["LOW"].n_observations == 2
    assert abs(by_l["LOW"].mean_realized_delay_days - 0.5) < 1e-9
    # SEVERE: 1 obs, mean = 12.0
    assert abs(by_l["SEVERE"].mean_realized_delay_days - 12.0) < 1e-9
    # MAE = (|1-1| + |0-0| + |10-12|) / 3 = 2/3
    assert abs(report.delay_mae - (2.0 / 3.0)) < 1e-9
    # Sign-agreement: row 2 skipped (both zero); rows 0 + 2 both
    # positive-positive → 2/2 = 1.0
    assert report.delay_sign_agreement == 1.0
    # Monotonic: LOW 0.5 < SEVERE 12.0 → True (MODERATE + HIGH skipped, no obs)
    assert report.monotonic_by_label is True
