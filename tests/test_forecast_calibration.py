"""Forecast calibration scoring — CRPS / PIT / coverage / Brier (rec R029)."""

from __future__ import annotations

import numpy as np
import pytest

from processing.forecast_accuracy_tracker import (
    AccuracyRow,
    brier_score,
    coverage_rate,
    crps_gaussian,
    pit_histogram,
    pit_value,
    reliability_curve,
    summarize_calibration,
)


def _row(predicted, actual, sigma=0.0, baseline=0.0, horizon=7):
    err = predicted - actual
    return AccuracyRow(
        forecast_date_iso="2026-01-01", target_date_iso="2026-01-08",
        horizon_days=horizon, lane_id="L", predicted=predicted, actual=actual,
        error=err, abs_error=abs(err), signed_error=err,
        predicted_sigma=sigma, baseline=baseline,
    )


# ── CRPS ─────────────────────────────────────────────────────────────────────

def test_crps_point_forecast_is_abs_error() -> None:
    assert crps_gaussian(0.5, 0.0, 0.7) == pytest.approx(0.2)
    assert crps_gaussian(0.5, 0.0, 0.5) == pytest.approx(0.0)


def test_crps_nonnegative_and_minimised_at_mean() -> None:
    # For fixed sigma, CRPS is smallest when the actual equals the mean.
    at_mean = crps_gaussian(0.5, 0.1, 0.5)
    off = crps_gaussian(0.5, 0.1, 0.9)
    assert at_mean >= 0 and off >= 0 and off > at_mean


def test_crps_sigma_zero_limit_matches_small_sigma() -> None:
    # As sigma -> 0 the Gaussian CRPS approaches |y - mu|.
    near = crps_gaussian(0.5, 1e-6, 0.8)
    assert near == pytest.approx(0.3, abs=1e-3)


def test_crps_sharper_calibrated_forecast_scores_better() -> None:
    # Two forecasts centred on the actual: the tighter (well-calibrated) one wins.
    tight = crps_gaussian(0.5, 0.05, 0.5)
    wide = crps_gaussian(0.5, 0.50, 0.5)
    assert tight < wide


# ── PIT ──────────────────────────────────────────────────────────────────────

def test_pit_value_centre_is_half_and_none_for_point() -> None:
    assert pit_value(0.5, 0.1, 0.5) == pytest.approx(0.5)
    assert pit_value(0.5, 0.0, 0.5) is None         # point forecast -> no PIT


def test_pit_histogram_flat_for_calibrated_draws() -> None:
    # Draw actuals from the predictive N(0.5, 0.1) -> PIT ~ Uniform -> ~flat.
    rng = np.random.default_rng(0)
    rows = [_row(0.5, float(rng.normal(0.5, 0.1)), sigma=0.1) for _ in range(2000)]
    hist = pit_histogram(rows, n_bins=10)
    assert sum(hist) == 2000
    # no bin wildly over/under the expected 200 (loose uniformity check)
    assert max(hist) < 320 and min(hist) > 100


def test_pit_histogram_u_shaped_when_overconfident() -> None:
    # Actuals drawn with TRUE sigma 0.3 but forecast claims 0.05 -> intervals
    # too narrow -> PIT piles into the extreme bins (U-shape).
    rng = np.random.default_rng(1)
    rows = [_row(0.5, float(rng.normal(0.5, 0.3)), sigma=0.05) for _ in range(2000)]
    hist = pit_histogram(rows, n_bins=10)
    edges = hist[0] + hist[-1]
    middle = sum(hist[3:7])
    assert edges > middle                            # mass at the tails


# ── coverage ─────────────────────────────────────────────────────────────────

def test_coverage_tracks_nominal_for_calibrated() -> None:
    rng = np.random.default_rng(2)
    rows = [_row(0.5, float(rng.normal(0.5, 0.1)), sigma=0.1) for _ in range(3000)]
    assert coverage_rate(rows, z=1.0) == pytest.approx(0.68, abs=0.04)
    assert coverage_rate(rows, z=1.96) == pytest.approx(0.95, abs=0.03)


def test_coverage_none_without_intervals() -> None:
    assert coverage_rate([_row(0.5, 0.6, sigma=0.0)]) is None


# ── Brier + reliability ──────────────────────────────────────────────────────

def test_brier_perfect_and_worst() -> None:
    assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == pytest.approx(0.0)
    assert brier_score([0.0, 1.0], [1, 0]) == pytest.approx(1.0)


def test_reliability_curve_on_diagonal_when_calibrated() -> None:
    # probs equal observed frequencies by construction -> near the diagonal.
    probs = [0.1] * 100 + [0.9] * 100
    rng = np.random.default_rng(3)
    outcomes = ([1 if rng.random() < 0.1 else 0 for _ in range(100)] +
                [1 if rng.random() < 0.9 else 0 for _ in range(100)])
    curve = reliability_curve(probs, outcomes, n_bins=10)
    for b in curve:
        assert abs(b["mean_prob"] - b["observed_freq"]) < 0.15


# ── summarize_calibration ────────────────────────────────────────────────────

def test_summarize_calibration_schema_and_skill() -> None:
    rng = np.random.default_rng(4)
    rows = [_row(0.5, float(rng.normal(0.5, 0.1)), sigma=0.1, baseline=0.4)
            for _ in range(500)]
    s = summarize_calibration(rows, threshold=0.6)
    assert s["n_pairs"] == 500 and s["n_interval"] == 500
    assert s["coverage_68"] == pytest.approx(0.68, abs=0.06)
    assert s["mean_pit"] == pytest.approx(0.5, abs=0.05)
    assert "brier_score" in s and "reliability_curve" in s
    assert s["crps_skill"] is not None              # baseline was logged


def test_summarize_calibration_empty_is_stable() -> None:
    s = summarize_calibration([])
    assert s["n_pairs"] == 0 and s["pit_histogram"] == []
    assert s["coverage_68"] is None and s["crps_skill"] is None
