"""Tests for processing.congestion_rate_lag.

Strategy: build synthetic congestion/rate pairs where the lag and sign of the
relationship are *known*, then assert that the model recovers them inside a
reasonable tolerance. All RNG seeding goes through explicit integers so the
tests are reproducible across Python processes (see ``utils.helpers.stable_hash``
notes — Python's built-in hash is process-salted).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from processing.congestion_rate_lag import (
    DEFAULT_CANDIDATE_LAGS,
    CongestionRateLag,
    LagBacktestResult,
    _aligned_changes,
    _delta_log,
    _pearsonr,
    analyze_port_route_lags,
    compute_lag_correlation,
    walk_forward_backtest,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────

def _make_pair(
    n: int = 240,
    true_lag: int = 7,
    r_target: float = 0.7,
    seed: int = 11,
) -> tuple[pd.Series, pd.Series]:
    """Build a (congestion, rate) pair where rate follows congestion with
    a known lag and a controllable amount of noise mixing.

    Construction:
      - congestion: random walk on log-scale, mean 0.5, σ=0.05
      - rate: a convex blend  ``r_target * congestion_{t-lag} + (1-r_target) * noise``
        on the log-return scale, recentered around 2000 USD/FEU.

    With ``r_target=0.7``, the recovered Pearson r should land near 0.7 at the
    true lag and lower elsewhere.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")

    # Log-returns drive the dynamics — keep them small so .clip(min=1e-9) never bites.
    cong_log_returns = rng.normal(0.0, 0.05, size=n)
    cong_log_path = np.cumsum(cong_log_returns)
    cong_values = 0.5 * np.exp(cong_log_path - cong_log_path.mean())

    # Build the rate's log-return as a mix of (lagged cong return) and noise.
    noise = rng.normal(0.0, 0.04, size=n)
    rate_log_returns = np.zeros(n)
    rate_log_returns[:true_lag] = noise[:true_lag]
    rate_log_returns[true_lag:] = (
        r_target * cong_log_returns[:-true_lag] + (1 - r_target) * noise[true_lag:]
    )
    rate_log_path = np.cumsum(rate_log_returns)
    rate_values = 2000.0 * np.exp(rate_log_path - rate_log_path.mean())

    cong = pd.Series(cong_values, index=dates, name="congestion")
    rate = pd.Series(rate_values, index=dates, name="rate")
    return cong, rate


def _wrap_as_df(series: pd.Series, value_col: str) -> pd.DataFrame:
    """Wrap a date-indexed Series back into the (date, value_col) frame the
    public ``analyze_port_route_lags`` API expects."""
    return pd.DataFrame({"date": series.index, value_col: series.to_numpy()})


# ─── _pearsonr ──────────────────────────────────────────────────────────────

def test_pearsonr_perfect_correlation_returns_unit() -> None:
    rng = np.random.default_rng(0)
    x = pd.Series(rng.normal(size=100))
    r, p = _pearsonr(x, x)
    # Identical series → r ≈ 1.0; the function returns degenerate (r, 1.0) when
    # |r| ≥ 1.0 to avoid the divide-by-(1-r²) singularity.
    assert abs(r) == pytest.approx(1.0, abs=1e-9)
    assert p == 1.0  # degenerate fallback


def test_pearsonr_uncorrelated_inputs_have_high_p() -> None:
    rng = np.random.default_rng(42)
    x = pd.Series(rng.normal(size=200))
    y = pd.Series(rng.normal(size=200))
    r, p = _pearsonr(x, y)
    assert abs(r) < 0.25
    assert 0.0 <= p <= 1.0


def test_pearsonr_small_n_returns_conservative_p() -> None:
    # With n < 30 we don't trust the normal-approx p — function returns 1.0.
    x = pd.Series([1.0, 2.0, 3.0])
    y = pd.Series([2.0, 4.0, 5.5])
    r, p = _pearsonr(x, y)
    assert p == 1.0  # conservative fallback when n too small


# ─── _delta_log and _aligned_changes ────────────────────────────────────────

def test_delta_log_handles_zeros_and_negatives() -> None:
    s = pd.Series([1.0, 2.0, 0.0, 4.0, -1.0, 8.0],
                  index=pd.date_range("2025-01-01", periods=6, freq="D"))
    out = _delta_log(s)
    # The clip-and-log path replaces 0 with NaN; .diff().dropna() removes the
    # first row plus any propagated NaN. The output must be finite throughout.
    assert out.notna().all()
    assert np.all(np.isfinite(out.to_numpy()))


def test_aligned_changes_index_alignment() -> None:
    cong, rate = _make_pair(n=120, true_lag=5, r_target=0.6, seed=23)
    d_cong, d_rate = _aligned_changes(cong, rate, lag_days=5)
    # The aligned series must share the same index, be non-empty, and finite.
    assert len(d_cong) == len(d_rate)
    assert len(d_cong) > 0
    assert (d_cong.index == d_rate.index).all()
    assert np.isfinite(d_cong.to_numpy()).all()
    assert np.isfinite(d_rate.to_numpy()).all()


# ─── compute_lag_correlation ────────────────────────────────────────────────

def test_compute_lag_recovers_known_lag() -> None:
    """Inject lag=7 and check the model picks 7 (or very close)."""
    cong, rate = _make_pair(n=300, true_lag=7, r_target=0.75, seed=101)
    fit = compute_lag_correlation(cong, rate, min_abs_r=0.10, max_p=0.10)
    assert fit is not None
    best_lag, r, p, n = fit
    assert best_lag == 7
    # The true blend coefficient is 0.75; recovered r should be on the same order.
    assert r > 0.4
    assert p < 0.05
    assert n > 200


def test_compute_lag_recovers_negative_correlation() -> None:
    """Build an inverse relationship — the model should still find lag=5 with r<0."""
    cong, rate = _make_pair(n=300, true_lag=5, r_target=0.7, seed=202)
    # Flip the rate's relationship: rate now anti-correlates with lagged cong.
    rate_neg = 2000.0 * (4000.0 / rate)
    fit = compute_lag_correlation(cong, rate_neg, min_abs_r=0.10, max_p=0.10)
    assert fit is not None
    best_lag, r, p, n = fit
    assert best_lag == 5
    assert r < 0


def test_compute_lag_returns_none_on_pure_noise() -> None:
    rng = np.random.default_rng(303)
    dates = pd.date_range("2025-01-01", periods=200, freq="D")
    cong = pd.Series(np.exp(rng.normal(0, 0.05, 200).cumsum()), index=dates)
    rate = pd.Series(2000 * np.exp(rng.normal(0, 0.05, 200).cumsum()), index=dates)
    # With independent random walks, no candidate lag should clear |r|>0.15 + p<0.05.
    fit = compute_lag_correlation(cong, rate)
    # Either None (no lag passed) or a weak result; the strict default gates
    # should produce None on truly independent inputs in this seed.
    assert fit is None or abs(fit[1]) < 0.3


def test_compute_lag_empty_inputs() -> None:
    assert compute_lag_correlation(pd.Series(dtype=float), pd.Series(dtype=float)) is None
    cong, rate = _make_pair(n=50, seed=1)
    assert compute_lag_correlation(pd.Series(dtype=float), rate) is None
    assert compute_lag_correlation(cong, pd.Series(dtype=float)) is None


def test_compute_lag_insufficient_obs() -> None:
    # Below min_obs, every lag is rejected → None.
    cong, rate = _make_pair(n=40, true_lag=7, r_target=0.8, seed=404)
    fit = compute_lag_correlation(cong, rate, min_obs=60)
    assert fit is None


def test_compute_lag_custom_candidate_lags_honors_restriction() -> None:
    """The function must only consider lags from the supplied set — even if
    a stronger lag exists outside it.

    Two candidate-set scenarios on the same fixture:

      1) Candidate set ⊇ {true_lag}: the model returns the true lag.
      2) Candidate set ⊉ {true_lag}, but contains a lag that still passes the
         gates: the model returns one from the set, not the true lag.
    """
    cong, rate = _make_pair(n=400, true_lag=7, r_target=0.85, seed=505)

    # 1) True lag in the set → model picks it.
    fit_with_true = compute_lag_correlation(cong, rate, candidate_lags=(3, 7, 14))
    assert fit_with_true is not None
    assert fit_with_true[0] == 7

    # 2) True lag NOT in the set. The function may return None (if no lag in
    # the restricted set clears the gates) OR it may return one of the lags
    # that's in the set — but never the true lag, since it wasn't offered.
    fit_restricted = compute_lag_correlation(
        cong, rate,
        candidate_lags=(2, 12),
    )
    if fit_restricted is not None:
        assert fit_restricted[0] in (2, 12)
        assert fit_restricted[0] != 7


# ─── analyze_port_route_lags ─────────────────────────────────────────────────

def test_analyze_port_route_lags_finds_link_via_registry() -> None:
    """Wire synthetic data into the registry-driven sweep — at least one real
    port↔route pair should produce a fit."""
    cong, rate = _make_pair(n=300, true_lag=7, r_target=0.7, seed=606)
    # transpacific_eb has origin_locode=CNSHA and dest_locode=USLAX.
    # We seed CNSHA (origin) so it gets matched.
    cong_history = {"CNSHA": _wrap_as_df(cong, "congestion_score")}
    freight_data = {"transpacific_eb": _wrap_as_df(rate, "rate_usd_per_feu")}

    results = analyze_port_route_lags(cong_history, freight_data, min_abs_r=0.10)
    assert len(results) >= 1
    hit = results[0]
    assert isinstance(hit, CongestionRateLag)
    assert hit.port_locode == "CNSHA"
    assert hit.route_id == "transpacific_eb"
    assert hit.role == "origin"
    assert hit.best_lag_days in DEFAULT_CANDIDATE_LAGS
    assert hit.interpretation  # non-empty string
    assert hit.n_observations > 0


def test_analyze_port_route_lags_empty_inputs_return_empty_list() -> None:
    assert analyze_port_route_lags({}, {}) == []


def test_analyze_port_route_lags_sorts_by_abs_r() -> None:
    """Two routes with different signal strengths — strongest is first."""
    cong_strong, rate_strong = _make_pair(n=300, true_lag=7, r_target=0.85, seed=707)
    cong_weak, rate_weak = _make_pair(n=300, true_lag=14, r_target=0.30, seed=808)

    cong_history = {
        "CNSHA": _wrap_as_df(cong_strong, "congestion_score"),   # serves transpacific_eb
        "NLRTM": _wrap_as_df(cong_weak, "congestion_score"),     # serves asia_europe (dest)
    }
    freight_data = {
        "transpacific_eb": _wrap_as_df(rate_strong, "rate_usd_per_feu"),
        "asia_europe":     _wrap_as_df(rate_weak,   "rate_usd_per_feu"),
    }

    results = analyze_port_route_lags(cong_history, freight_data, min_abs_r=0.08)
    # First result should be the strong link.
    assert results, "expected at least one fit"
    if len(results) >= 2:
        assert abs(results[0].pearson_r) >= abs(results[1].pearson_r)


def test_analyze_port_route_lags_falls_back_to_vessel_count() -> None:
    """When `congestion_score` is missing but `vessel_count` exists, the model
    should use it as a proxy."""
    cong, rate = _make_pair(n=300, true_lag=7, r_target=0.7, seed=909)
    cong_df = pd.DataFrame({"date": cong.index, "vessel_count": cong.to_numpy() * 100})
    cong_history = {"CNSHA": cong_df}
    freight_data = {"transpacific_eb": _wrap_as_df(rate, "rate_usd_per_feu")}
    results = analyze_port_route_lags(cong_history, freight_data, min_abs_r=0.10)
    assert len(results) >= 1


# ─── walk_forward_backtest ──────────────────────────────────────────────────

def test_walk_forward_backtest_returns_finite_metrics() -> None:
    cong, rate = _make_pair(n=400, true_lag=7, r_target=0.75, seed=1111)
    bt = walk_forward_backtest(
        cong, rate,
        train_window=90, test_window=14, step=14,
        min_abs_r=0.10, max_p=0.10,
        port_locode="CNSHA", route_id="transpacific_eb",
    )
    assert isinstance(bt, LagBacktestResult)
    assert bt.n_windows > 0
    assert 0.0 <= bt.hit_rate <= 1.0
    assert math.isfinite(bt.avg_r_in_sample)
    assert math.isfinite(bt.avg_r_out_of_sample)
    assert math.isfinite(bt.avg_lag_days)
    # With r_target=0.75 the in-sample r should clearly outperform random.
    assert bt.avg_r_in_sample > 0.3


def test_walk_forward_backtest_beats_coin_flip_on_real_signal() -> None:
    """When the signal is genuinely strong, the predictor should beat 0.5 hit
    rate over many windows. The lag-7, r_target=0.8 fixture gives ~67%+ in
    practice; we set a conservative floor at 0.55."""
    cong, rate = _make_pair(n=600, true_lag=7, r_target=0.80, seed=2222)
    bt = walk_forward_backtest(
        cong, rate,
        train_window=120, test_window=14, step=7,
        min_abs_r=0.10, max_p=0.10,
    )
    assert bt.n_windows >= 5
    assert bt.hit_rate >= 0.55


def test_walk_forward_backtest_handles_empty_inputs() -> None:
    bt = walk_forward_backtest(pd.Series(dtype=float), pd.Series(dtype=float))
    assert bt.n_windows == 0
    assert bt.hit_rate == 0.0
    assert bt.avg_lag_days == 0.0


def test_walk_forward_backtest_insufficient_history() -> None:
    """When history is shorter than train + test, backtest returns empty result."""
    cong, rate = _make_pair(n=50, true_lag=7, r_target=0.7, seed=3333)
    bt = walk_forward_backtest(
        cong, rate,
        train_window=90, test_window=14,
    )
    assert bt.n_windows == 0
