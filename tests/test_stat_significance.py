"""Defining-property tests for the Sharpe significance haircuts (rec R101)."""

from __future__ import annotations

import math

import pytest

from processing.stat_significance import (
    assess_sharpe,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_track_record_length,
    probabilistic_sharpe_ratio,
)


# ── Probabilistic Sharpe Ratio ──────────────────────────────────────────────

def test_psr_is_half_at_the_benchmark() -> None:
    p = probabilistic_sharpe_ratio(0.2, 250, sr_benchmark=0.2)
    assert p == pytest.approx(0.5, abs=1e-9)


def test_psr_rises_with_observed_sharpe() -> None:
    lo = probabilistic_sharpe_ratio(0.05, 250)
    hi = probabilistic_sharpe_ratio(0.20, 250)
    assert hi > lo > 0.5


def test_psr_rises_with_sample_size() -> None:
    short = probabilistic_sharpe_ratio(0.1, 30)
    long = probabilistic_sharpe_ratio(0.1, 2000)
    assert long > short


def test_psr_bounded_unit_interval() -> None:
    for sr in (-1.0, 0.0, 0.3, 5.0):
        p = probabilistic_sharpe_ratio(sr, 500, skew=-0.5, kurt=6.0)
        assert 0.0 <= p <= 1.0


def test_psr_degenerate_sample_returns_agnostic_half() -> None:
    assert probabilistic_sharpe_ratio(0.3, 1) == 0.5  # n_obs < 2 -> undefined SE


# ── expected max Sharpe / deflation benchmark ───────────────────────────────

def test_expected_max_sharpe_zero_for_single_trial() -> None:
    assert expected_max_sharpe(1, 0.5) == 0.0


def test_expected_max_sharpe_grows_with_trials() -> None:
    few = expected_max_sharpe(10, 0.5)
    many = expected_max_sharpe(1000, 0.5)
    assert many > few > 0.0


# ── Deflated Sharpe Ratio ───────────────────────────────────────────────────

def test_deflation_lowers_confidence_vs_psr() -> None:
    psr = probabilistic_sharpe_ratio(0.18, 500, sr_benchmark=0.0)
    dsr = deflated_sharpe_ratio(0.18, 500, n_trials=50, sr_std=0.5)
    assert dsr < psr  # the multiple-testing haircut must reduce confidence


def test_more_trials_means_harsher_haircut() -> None:
    d_few = deflated_sharpe_ratio(0.18, 500, n_trials=5, sr_std=0.5)
    d_many = deflated_sharpe_ratio(0.18, 500, n_trials=500, sr_std=0.5)
    assert d_many < d_few


def test_deflation_uses_trial_sharpe_dispersion() -> None:
    sharpes = [0.05, 0.10, 0.18, -0.02, 0.07, 0.12]
    dsr = deflated_sharpe_ratio(0.18, 500, n_trials=len(sharpes), trial_sharpes=sharpes)
    assert 0.0 <= dsr <= 1.0


# ── minimum track record length ─────────────────────────────────────────────

def test_min_trl_none_when_sharpe_below_benchmark() -> None:
    assert min_track_record_length(0.0, sr_benchmark=0.1) is None


def test_min_trl_decreases_with_higher_sharpe() -> None:
    weak = min_track_record_length(0.05)
    strong = min_track_record_length(0.25)
    assert weak is not None and strong is not None
    assert strong < weak  # a stronger signal needs less data to prove out


# ── bundled assessment ──────────────────────────────────────────────────────

def test_assess_sharpe_flags_selection_bias() -> None:
    # A Sharpe that looks fine raw but was cherry-picked from many trials.
    res = assess_sharpe(0.16, 400, n_trials=200, sr_std=0.6, threshold=0.95)
    assert res.psr > res.deflated_sr
    assert not res.is_significant
    assert "haircut" in res.verdict.lower()


def test_assess_sharpe_strong_single_trial_is_significant() -> None:
    res = assess_sharpe(0.5, 2000, n_trials=1, threshold=0.95)
    assert res.is_significant
    assert math.isfinite(res.psr) and math.isfinite(res.deflated_sr)
