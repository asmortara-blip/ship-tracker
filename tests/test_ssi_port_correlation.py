"""Defining-property tests for processing/ssi_port_correlation.py and its backtester."""
from __future__ import annotations

import math

import pytest

from processing.ssi_port_correlation import (
    CrossCorrelationResult,
    LeadingIndicatorReport,
    SSI_PORT_CORRELATION_SOURCE,
    analyze_leading_indicator_relationship,
    compute_lag_correlation,
    generate_synthetic_paired_series,
)
from processing.ssi_port_correlation_backtest import (
    SSI_PORT_CORRELATION_BACKTEST_SOURCE,
    validate_leading_indicator_recovery,
)


# ── 1. Pearson self-correlation identities ────────────────────────────────


def test_pearson_r_of_series_with_itself_is_one() -> None:
    """Pearson r(x, x) must equal 1.0 (modulo float jitter)."""
    series = [float(i) + 0.1 * (i % 3) for i in range(50)]
    r = compute_lag_correlation(series, series, lag_days=0)
    assert r.n_pairs == len(series)
    assert math.isclose(r.pearson_r, 1.0, abs_tol=1e-9)
    assert math.isclose(r.spearman_r, 1.0, abs_tol=1e-9)


def test_pearson_r_of_series_with_negated_itself_is_minus_one() -> None:
    """Pearson r(x, -x) must equal -1.0."""
    series = [float(i) for i in range(40)]
    negated = [-float(x) for x in series]
    r = compute_lag_correlation(series, negated, lag_days=0)
    assert math.isclose(r.pearson_r, -1.0, abs_tol=1e-9)
    assert math.isclose(r.spearman_r, -1.0, abs_tol=1e-9)


# ── 2. Lag alignment ──────────────────────────────────────────────────────


def test_lag_zero_on_identical_series_returns_r_one() -> None:
    """Lag=0 with two identical (non-constant) series → r=1.0."""
    series = [float(i) + 0.05 * (i % 5) for i in range(30)]
    r = compute_lag_correlation(series, series, lag_days=0)
    assert math.isclose(r.pearson_r, 1.0, abs_tol=1e-9)
    assert r.lag_days == 0
    assert r.n_pairs == 30


def test_lag_n_on_shifted_series_recovers_r_one() -> None:
    """If deficit[t] = ssi[t - N] (deficit lags SSI by N), then lag=N
    must produce r=1.0 — that's the defining property of the analyzer.

    Construction: ssi is a monotone-ish sequence; deficit is ssi shifted
    right (its first N elements duplicate ssi[0], from position N
    onwards deficit[t] = ssi[t - N]). At lag=N we align ssi[0:30-N]
    with deficit[N:30] which is exactly ssi[0:30-N] — perfect r=1.
    """
    n = 30
    shift = 5
    ssi = [0.1 * i + 0.01 * (i % 7) for i in range(n)]
    deficit = [ssi[0]] * shift + ssi[:-shift]
    assert len(deficit) == n
    r = compute_lag_correlation(ssi, deficit, lag_days=shift)
    assert math.isclose(r.pearson_r, 1.0, abs_tol=1e-9)
    assert r.lag_days == shift
    assert r.n_pairs == n - shift


# ── 3. Synthetic generator: determinism + lag truth ───────────────────────


def test_synthetic_generator_is_deterministic_for_seed() -> None:
    a_ssi, a_def = generate_synthetic_paired_series(
        n_days=60, ssi_lead_days=3, noise=0.1, seed=42,
    )
    b_ssi, b_def = generate_synthetic_paired_series(
        n_days=60, ssi_lead_days=3, noise=0.1, seed=42,
    )
    assert a_ssi == b_ssi
    assert a_def == b_def


def test_synthetic_generator_outputs_have_requested_length() -> None:
    ssi, deficit = generate_synthetic_paired_series(
        n_days=75, ssi_lead_days=4, noise=0.0, seed=1,
    )
    assert len(ssi) == 75
    assert len(deficit) == 75


def test_synthetic_generator_clamps_short_n_days() -> None:
    """Requesting n_days < 4 must still return at least 4 entries so
    downstream analysis has enough overlap to compute a correlation."""
    ssi, deficit = generate_synthetic_paired_series(
        n_days=2, ssi_lead_days=0, noise=0.0, seed=1,
    )
    assert len(ssi) >= 4
    assert len(deficit) >= 4


def test_synthetic_generator_clamps_negative_lead() -> None:
    """Negative ssi_lead_days must be coerced to 0 (no exception)."""
    ssi, deficit = generate_synthetic_paired_series(
        n_days=20, ssi_lead_days=-5, noise=0.0, seed=1,
    )
    assert len(ssi) == 20
    assert len(deficit) == 20


# ── 4. End-to-end: analyzer recovers a known lag in zero-noise synth ──────


def test_analyzer_recovers_true_lag_with_zero_noise() -> None:
    """The defining backtest-equivalent: true_lag=3 + noise=0 + sweep
    up to lag=14 must return best_lag_days == 3 (within ±2)."""
    ssi, deficit = generate_synthetic_paired_series(
        n_days=90, ssi_lead_days=3, noise=0.0, seed=99,
    )
    report = analyze_leading_indicator_relationship(
        ssi, deficit, max_lag_days=14,
    )
    assert isinstance(report, LeadingIndicatorReport)
    assert report.best_lag_days == 3
    assert report.best_lag_r > 0.9   # near-perfect on zero noise
    assert "lead" in report.interpretation.lower() or "lag" in report.interpretation.lower()


def test_analyzer_emits_one_correlation_per_lag() -> None:
    ssi, deficit = generate_synthetic_paired_series(
        n_days=80, ssi_lead_days=2, noise=0.0, seed=1,
    )
    report = analyze_leading_indicator_relationship(
        ssi, deficit, max_lag_days=10,
    )
    assert len(report.lag_correlations) == 11   # 0..10 inclusive
    for r in report.lag_correlations:
        assert isinstance(r, CrossCorrelationResult)
        assert 0 <= r.lag_days <= 10


# ── 5. Empty / degenerate input handling ──────────────────────────────────


def test_empty_inputs_return_empty_report() -> None:
    report = analyze_leading_indicator_relationship([], [], max_lag_days=14)
    assert report.best_lag_days == 0
    assert report.best_lag_r == 0.0
    assert report.lag_correlations == []
    assert "insufficient" in report.interpretation.lower()


def test_mismatched_lengths_return_empty_report() -> None:
    report = analyze_leading_indicator_relationship(
        [1.0, 2.0, 3.0, 4.0, 5.0],
        [1.0, 2.0],
        max_lag_days=2,
    )
    assert report.lag_correlations == []
    assert "insufficient" in report.interpretation.lower()


def test_max_lag_clamps_to_series_length() -> None:
    """Requesting lag=20 on a 10-point series must clamp the sweep so
    every CrossCorrelationResult has n_pairs >= 3 (or the report
    returns empty cleanly with an 'insufficient data' interpretation)."""
    ssi = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    deficit = [0.5, 0.4, 0.3, 0.2, 0.1, 0.6, 0.7, 0.8, 0.9, 1.0]
    report = analyze_leading_indicator_relationship(
        ssi, deficit, max_lag_days=20,
    )
    # Either we got a non-empty sweep with every entry meeting n_pairs >= 3,
    # or we got a clean empty report. Both are defensible — pin neither
    # specifically, but reject the failure mode (sweep with junk results).
    if report.lag_correlations:
        for r in report.lag_correlations:
            assert r.n_pairs >= 3
            # Max lag in the sweep must be <= len(series) - 3 = 7.
            assert r.lag_days <= len(ssi) - 3


def test_compute_lag_correlation_with_lag_exceeding_length() -> None:
    """Single-lag call with lag >= len(series) returns neutral defaults."""
    series = [1.0, 2.0, 3.0, 4.0, 5.0]
    r = compute_lag_correlation(series, series, lag_days=10)
    assert r.n_pairs == 0
    assert r.pearson_r == 0.0
    assert r.spearman_r == 0.0
    assert r.p_value == 1.0


def test_constant_series_returns_neutral_result() -> None:
    """Zero-variance input → r=0 + n_pairs=0 (not NaN)."""
    flat = [0.5] * 20
    varied = [float(i) for i in range(20)]
    r = compute_lag_correlation(flat, varied, lag_days=0)
    assert r.pearson_r == 0.0
    assert r.spearman_r == 0.0


# ── 6. Provenance markers present ────────────────────────────────────────


def test_source_markers_present() -> None:
    assert SSI_PORT_CORRELATION_SOURCE is not None
    assert "SSI" in getattr(SSI_PORT_CORRELATION_SOURCE, "name", "")
    assert SSI_PORT_CORRELATION_BACKTEST_SOURCE is not None
    assert "Lag Recovery" in getattr(SSI_PORT_CORRELATION_BACKTEST_SOURCE, "name", "")


# ── 7. Backtester: defining noise→recovery contract ──────────────────────


def test_backtest_zero_noise_yields_perfect_recovery() -> None:
    """The headline contract: with noise=0, every run recovers the
    true lag exactly within tolerance → recovery_rate = 1.0."""
    out = validate_leading_indicator_recovery(
        n_runs=8, noise=0.0, true_lag_days=3, seed=7, tolerance_days=2,
    )
    assert out["recovery_rate"] == 1.0
    assert out["passed"] is True
    assert out["recoveries"] == out["n_runs"]


def test_backtest_high_noise_degrades_recovery() -> None:
    """The complementary contract: with noise=1.0, the synthetic signal
    is buried in noise and recovery_rate must fall well below the
    noise=0.0 baseline. The exact threshold depends on the random
    seed — using a wider gap (clean-recovery minus noisy-recovery)
    avoids brittle pin-to-exact-number assertions on synth data."""
    clean = validate_leading_indicator_recovery(
        n_runs=12, noise=0.0, true_lag_days=3, seed=11, tolerance_days=2,
    )
    noisy = validate_leading_indicator_recovery(
        n_runs=12, noise=1.0, true_lag_days=3, seed=11, tolerance_days=2,
    )
    # Noise should knock recovery down by at least 25 percentage points
    # vs clean — that's the signal-vs-noise contract this test pins.
    assert (clean["recovery_rate"] - noisy["recovery_rate"]) >= 0.25


def test_backtest_is_deterministic_for_seed() -> None:
    """Same seed + same inputs → byte-identical recovery rate + per_run."""
    a = validate_leading_indicator_recovery(
        n_runs=5, noise=0.5, true_lag_days=2, seed=42, tolerance_days=1,
    )
    b = validate_leading_indicator_recovery(
        n_runs=5, noise=0.5, true_lag_days=2, seed=42, tolerance_days=1,
    )
    assert a["recovery_rate"] == b["recovery_rate"]
    assert a["mean_abs_lag_error"] == b["mean_abs_lag_error"]
    assert [r["detected_lag"] for r in a["per_run"]] == [
        r["detected_lag"] for r in b["per_run"]
    ]


def test_backtest_returns_canonical_shape() -> None:
    out = validate_leading_indicator_recovery(
        n_runs=3, noise=0.0, true_lag_days=2,
    )
    required_keys = {
        "n_runs", "noise", "true_lag_days", "tolerance_days",
        "n_days", "max_lag_days", "recoveries", "recovery_rate",
        "mean_abs_lag_error", "mean_best_r", "passed", "per_run",
        "source", "summary",
    }
    assert required_keys <= set(out.keys())
    assert isinstance(out["per_run"], list)
    assert len(out["per_run"]) == out["n_runs"]
    for run in out["per_run"]:
        assert {"run_index", "true_lag", "detected_lag",
                "abs_error", "best_r", "recovered"} <= set(run.keys())


def test_backtest_clamps_max_lag_to_be_larger_than_true_lag() -> None:
    """If the caller requests max_lag_days < true_lag_days, the backtester
    must defensively widen the sweep so recovery is even possible."""
    out = validate_leading_indicator_recovery(
        n_runs=4, noise=0.0, true_lag_days=8, max_lag_days=2, seed=3,
    )
    # The analyzer's sweep was widened to >= true_lag + 2 = 10, so
    # recovery should still succeed at zero noise.
    assert out["recovery_rate"] >= 0.75
