"""Tests for processing.convergence_analyzer.

Synthetic pairs constructed with KNOWN convergence properties:
  - Pair where the short window has stronger correlation than the long
    window → classified as Converging
  - Pair where short < long → Diverging
  - Pair where the sign flipped between long and short → Decoupling
  - Pair where short ≈ long → Stable

All RNG seeded with explicit integers (no Python hash(), which is salted).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from processing.convergence_analyzer import (
    DEFAULT_LONG_WINDOW,
    DEFAULT_MIN_DELTA,
    DEFAULT_SHORT_WINDOW,
    PairConvergence,
    _classify,
    _correlation_on_tail,
    _normalize_series,
    compute_correlation_matrix,
    find_converging,
    find_decoupling,
    find_diverging,
    find_pair_convergence,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────

def _series(values, name: str) -> pd.Series:
    return pd.Series(
        values,
        index=pd.date_range("2025-01-01", periods=len(values), freq="D"),
        name=name,
    )


def _pair_converging(n: int = 200, seed: int = 11) -> tuple[pd.Series, pd.Series]:
    """Long window: noisy/independent; short window: highly correlated.

    Construction: first (n - 60) days are independent noise; last 60 days
    are nearly identical (one is the other × 1.0 + tiny noise). Short-window
    r ≈ 1; long-window r much smaller.
    """
    rng = np.random.default_rng(seed)
    n_early = n - 60
    a_early = rng.normal(0, 1, n_early)
    b_early = rng.normal(0, 1, n_early)
    a_late = rng.normal(0, 1, 60)
    b_late = a_late + rng.normal(0, 0.05, 60)  # near-perfect alignment
    a = _series(np.concatenate([a_early, a_late]), "A")
    b = _series(np.concatenate([b_early, b_late]), "B")
    return a, b


def _pair_diverging(n: int = 200, seed: int = 21) -> tuple[pd.Series, pd.Series]:
    """Long window: strong positive correlation; short window: weaker positive.

    Construction keeps the SAME SIGN (avoids being classified Decoupling)
    while shrinking the magnitude enough that |Δr| clears the min_delta
    threshold. Last 30 days are weakly correlated (~0.3); earlier ~170 days
    are strongly correlated (~0.95).
    """
    rng = np.random.default_rng(seed)
    n_early = n - 30
    a_early = rng.normal(0, 1, n_early)
    b_early = a_early + rng.normal(0, 0.05, n_early)
    a_late = rng.normal(0, 1, 30)
    # Weak same-sign correlation: 25% signal + 75% noise.
    b_late = 0.25 * a_late + 0.75 * rng.normal(0, 1, 30)
    a = _series(np.concatenate([a_early, a_late]), "A")
    b = _series(np.concatenate([b_early, b_late]), "B")
    return a, b


def _pair_decoupling(n: int = 200, seed: int = 31) -> tuple[pd.Series, pd.Series]:
    """Long window: clearly positive correlation; short window: negative.

    Construction: only the last 30 days are negative-correlated. The
    long window (last 90) covers 60 positive + 30 negative days, so the
    long-window correlation stays positive (dominated by the 60 positive
    days) — which then triggers the sign-flip Decoupling classifier when
    the all-negative short window comes in.
    """
    rng = np.random.default_rng(seed)
    n_early = n - 30
    a_early = rng.normal(0, 1, n_early)
    b_early = a_early + rng.normal(0, 0.10, n_early)
    a_late = rng.normal(0, 1, 30)
    b_late = -a_late + rng.normal(0, 0.10, 30)   # SIGN FLIP — full negative
    a = _series(np.concatenate([a_early, a_late]), "A")
    b = _series(np.concatenate([b_early, b_late]), "B")
    return a, b


def _pair_stable(n: int = 200, seed: int = 41) -> tuple[pd.Series, pd.Series]:
    """Both windows have similar moderate correlation — no meaningful shift."""
    rng = np.random.default_rng(seed)
    a_vals = rng.normal(0, 1, n)
    b_vals = 0.6 * a_vals + 0.4 * rng.normal(0, 1, n)
    return _series(a_vals, "A"), _series(b_vals, "B")


# ─── _normalize_series ──────────────────────────────────────────────────────

def test_normalize_handles_none_and_empty() -> None:
    assert _normalize_series(None).empty
    assert _normalize_series(pd.Series(dtype=float)).empty


def test_normalize_drops_nans_and_dedupes() -> None:
    idx = pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-02", "2025-01-03"])
    s = pd.Series([1.0, 2.0, 2.5, np.nan], index=idx)
    out = _normalize_series(s)
    # NaN dropped; duplicate index → last value kept (2.5).
    assert len(out) == 2
    assert out.loc["2025-01-02"] == 2.5


# ─── _classify ──────────────────────────────────────────────────────────────

def test_classify_decoupling_on_sign_flip() -> None:
    # long_r = +0.6 (clearly positive), short_r = -0.4 → Decoupling.
    assert _classify(-0.4, 0.6, min_delta=0.2, decouple_long_r=0.3) == "Decoupling"
    assert _classify(0.4, -0.6, min_delta=0.2, decouple_long_r=0.3) == "Decoupling"


def test_classify_converging_when_magnitude_grows() -> None:
    # |short_r| − |long_r| = 0.30 ≥ min_delta → Converging.
    assert _classify(0.80, 0.50, min_delta=0.2, decouple_long_r=0.3) == "Converging"


def test_classify_diverging_when_magnitude_shrinks() -> None:
    # |short_r| − |long_r| = -0.30 ≤ -min_delta → Diverging.
    assert _classify(0.20, 0.50, min_delta=0.2, decouple_long_r=0.3) == "Diverging"


def test_classify_stable_when_change_below_threshold() -> None:
    assert _classify(0.55, 0.50, min_delta=0.2, decouple_long_r=0.3) == "Stable"
    assert _classify(-0.55, -0.50, min_delta=0.2, decouple_long_r=0.3) == "Stable"


def test_classify_does_not_decouple_when_long_r_weak() -> None:
    # long_r = +0.1 (below decouple_long_r floor) → sign flip ignored.
    assert _classify(-0.20, 0.10, min_delta=0.2, decouple_long_r=0.3) == "Stable"


# ─── _correlation_on_tail ───────────────────────────────────────────────────

def test_correlation_on_tail_returns_none_when_insufficient_overlap() -> None:
    a = _series([1.0, 2.0, 3.0], "A")
    b = _series([2.0, 4.0, 6.0], "B")
    r, n = _correlation_on_tail(a, b, window=60, min_obs=20)
    assert r is None
    assert n == 3


def test_correlation_on_tail_returns_none_on_constant_series() -> None:
    a = _series([1.0] * 50, "A")
    b = _series(list(range(50)), "B")
    r, n = _correlation_on_tail(a, b, window=50, min_obs=20)
    assert r is None
    assert n == 50


def test_correlation_on_tail_computes_known_correlation() -> None:
    # y = 2x + noise → very high positive r.
    rng = np.random.default_rng(7)
    x_vals = rng.normal(0, 1, 100)
    y_vals = 2 * x_vals + rng.normal(0, 0.01, 100)
    a = _series(x_vals, "A")
    b = _series(y_vals, "B")
    r, n = _correlation_on_tail(a, b, window=100, min_obs=20)
    assert r is not None
    assert n == 100
    assert r > 0.99


# ─── compute_correlation_matrix ─────────────────────────────────────────────

def test_correlation_matrix_diagonal_is_one() -> None:
    rng = np.random.default_rng(101)
    series = {f"S{i}": _series(rng.normal(0, 1, 100), f"S{i}") for i in range(4)}
    mat = compute_correlation_matrix(series, window=60, min_obs=20)
    assert mat.shape == (4, 4)
    for name in series:
        assert mat.loc[name, name] == 1.0


def test_correlation_matrix_is_symmetric() -> None:
    rng = np.random.default_rng(102)
    series = {f"S{i}": _series(rng.normal(0, 1, 100), f"S{i}") for i in range(3)}
    mat = compute_correlation_matrix(series, window=60, min_obs=20)
    # Off-diagonal symmetry (NaN-safe via fillna).
    arr = mat.fillna(0).to_numpy()
    assert np.allclose(arr, arr.T)


def test_correlation_matrix_empty_input() -> None:
    assert compute_correlation_matrix({}).empty


# ─── find_pair_convergence — defining properties ────────────────────────────

def test_finds_converging_pair() -> None:
    a, b = _pair_converging(n=200, seed=11)
    pairs = find_pair_convergence({"A": a, "B": b},
                                   short_window=30, long_window=90)
    assert len(pairs) == 1
    p = pairs[0]
    assert isinstance(p, PairConvergence)
    assert p.direction == "Converging"
    assert abs(p.short_r) > abs(p.long_r)  # magnitude grew


def test_finds_diverging_pair() -> None:
    a, b = _pair_diverging(n=200, seed=21)
    pairs = find_pair_convergence({"A": a, "B": b},
                                   short_window=30, long_window=90)
    assert len(pairs) == 1
    assert pairs[0].direction == "Diverging"
    assert abs(pairs[0].short_r) < abs(pairs[0].long_r)


def test_finds_decoupling_pair() -> None:
    a, b = _pair_decoupling(n=200, seed=31)
    # Use a slightly lower decouple_long_r floor to match this fixture's
    # long-window mix (60 positive + 30 negative days → long_r ≈ +0.2,
    # which is below the default 0.3 floor). The classifier's sign-flip
    # check still requires a non-trivial long_r.
    pairs = find_pair_convergence(
        {"A": a, "B": b},
        short_window=30, long_window=90, decouple_long_r=0.20,
    )
    assert len(pairs) == 1
    p = pairs[0]
    assert p.direction == "Decoupling"
    # Long-window r positive, short-window r negative (or vice versa).
    assert (p.long_r > 0 and p.short_r < 0) or (p.long_r < 0 and p.short_r > 0)


def test_stable_pair_classified_as_stable() -> None:
    a, b = _pair_stable(n=200, seed=41)
    pairs = find_pair_convergence({"A": a, "B": b},
                                   short_window=30, long_window=90)
    assert len(pairs) == 1
    assert pairs[0].direction == "Stable"


# ─── find_pair_convergence — sorting + filtering ────────────────────────────

def test_results_sorted_by_abs_delta_descending() -> None:
    # Build 3 pairs with different |delta|: converging > stable > weak-diverging.
    a, b = _pair_converging(n=200, seed=51)   # big positive delta
    c, d = _pair_stable(n=200, seed=52)       # near-zero delta
    pairs = find_pair_convergence(
        {"A": a, "B": b, "C": c, "D": d},
        short_window=30, long_window=90,
    )
    # Should have C(4,2)=6 pairs; the converging one should be first.
    assert len(pairs) == 6
    # The biggest |delta_r| should be at position 0.
    deltas = [abs(p.delta_r) for p in pairs]
    assert deltas == sorted(deltas, reverse=True)


def test_find_pair_convergence_skips_short_overlap_silently() -> None:
    short = _series([1.0] * 10, "SHORT")    # too few obs for any window
    long = _series([1.0] * 200, "LONG")     # constant series anyway
    a, b = _pair_converging(n=200, seed=61)
    pairs = find_pair_convergence(
        {"SHORT": short, "LONG": long, "A": a, "B": b},
        short_window=30, long_window=90, min_obs=20,
    )
    # SHORT pairs and the constant pair both filtered out; A↔B passes.
    names = {(p.name_a, p.name_b) for p in pairs}
    assert ("A", "B") in names or ("B", "A") in names
    # Pair involving SHORT can't appear — overlap < min_obs.
    for p in pairs:
        assert p.name_a != "SHORT" and p.name_b != "SHORT"


def test_find_pair_convergence_requires_long_gt_short_window() -> None:
    with pytest.raises(ValueError):
        find_pair_convergence(
            {"A": _series([1.0] * 100, "A"), "B": _series([2.0] * 100, "B")},
            short_window=60, long_window=30,
        )


def test_find_pair_convergence_empty_input() -> None:
    assert find_pair_convergence({}) == []


def test_find_pair_convergence_single_series_returns_empty() -> None:
    a = _series([1.0] * 100, "A")
    assert find_pair_convergence({"A": a}) == []


# ─── Filter helpers ─────────────────────────────────────────────────────────

def test_filter_helpers_return_only_matching_direction() -> None:
    a, b = _pair_converging(n=200, seed=71)
    c, d = _pair_diverging(n=200, seed=72)
    e, f = _pair_decoupling(n=200, seed=73)
    pairs = find_pair_convergence(
        {"A": a, "B": b, "C": c, "D": d, "E": e, "F": f},
        short_window=30, long_window=90,
    )
    conv = find_converging(pairs)
    div = find_diverging(pairs)
    dec = find_decoupling(pairs)
    # Each filter only contains its own classification.
    assert all(p.direction == "Converging" for p in conv)
    assert all(p.direction == "Diverging" for p in div)
    assert all(p.direction == "Decoupling" for p in dec)
    # And every pair from the constructed fixtures shows up in at least one
    # of the three (Stable would be excluded — these fixtures are not stable).
    # We don't enforce strict counts because cross-pair correlations between
    # different fixtures may produce additional results.
