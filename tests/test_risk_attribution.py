"""Tests for engine.carrier_factor_model.risk_attribution (R124).

Ex-ante RISK attribution: decompose forecast portfolio VARIANCE into per-factor
(systematic) + per-name (specific) contributions. The defining properties are
the three additive identities:

    factor_variance + specific_variance == total_variance
    sum(per_factor[*].variance)          == factor_variance
    sum(per_name_specific[*].variance)   == specific_variance

We pin them on a hand-built 2-factor / 3-name example with known B, Ω, D, w,
compute σ_p² independently with numpy, and assert the decomposition matches.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from engine.carrier_factor_model import RiskAttribution, risk_attribution


# ── A minimal fit stand-in (only .betas + .residual_std are read) ────────────


@dataclass
class _Fit:
    betas: dict
    residual_std: float


def _cov(factors, mat) -> pd.DataFrame:
    """Build a factor-cov DataFrame indexed/columned by factor name."""
    return pd.DataFrame(np.asarray(mat, dtype=float), index=factors, columns=factors)


# ── Hand-built 2-factor / 3-name example ─────────────────────────────────────


def _known_example():
    """Return (weights, fits, factor_cov, B, omega, D, w, names, factors)."""
    factors = ["F1", "F2"]
    names = ["AAA", "BBB", "CCC"]

    # Per-name factor loadings (names × factors).
    betas = {
        "AAA": {"F1": 1.0, "F2": 0.2},
        "BBB": {"F1": 0.5, "F2": -0.3},
        "CCC": {"F1": -0.4, "F2": 0.8},
    }
    # Per-name specific (idiosyncratic) per-period vol.
    spec_std = {"AAA": 0.10, "BBB": 0.20, "CCC": 0.05}
    # Signed weights (long/short; need not sum to 1).
    weights = {"AAA": 0.5, "BBB": 0.3, "CCC": -0.2}

    fits = {n: _Fit(betas=betas[n], residual_std=spec_std[n]) for n in names}

    # A genuinely off-diagonal PSD factor covariance (annualized already).
    omega_mat = [[0.04, 0.01], [0.01, 0.09]]
    factor_cov = _cov(factors, omega_mat)

    B = np.array([[betas[n][f] for f in factors] for n in names], dtype=float)
    omega = np.array(omega_mat, dtype=float)
    D = np.array([spec_std[n] ** 2 for n in names], dtype=float)
    w = np.array([weights[n] for n in names], dtype=float)
    return weights, fits, factor_cov, B, omega, D, w, names, factors


# ── Defining identities ──────────────────────────────────────────────────────


def test_buckets_sum_to_total_variance() -> None:
    weights, fits, factor_cov, *_ = _known_example()
    a = risk_attribution(weights, fits, factor_cov, periods_per_year=1)
    assert a.factor_variance + a.specific_variance == pytest.approx(
        a.total_variance, abs=1e-12
    )


def test_per_factor_sums_to_factor_variance() -> None:
    weights, fits, factor_cov, *_ = _known_example()
    a = risk_attribution(weights, fits, factor_cov, periods_per_year=1)
    s = sum(d["variance"] for d in a.per_factor.values())
    assert s == pytest.approx(a.factor_variance, abs=1e-12)


def test_per_name_specific_sums_to_specific_variance() -> None:
    weights, fits, factor_cov, *_ = _known_example()
    a = risk_attribution(weights, fits, factor_cov, periods_per_year=1)
    s = sum(d["variance"] for d in a.per_name_specific.values())
    assert s == pytest.approx(a.specific_variance, abs=1e-12)


def test_matches_independent_numpy_quadratic_form() -> None:
    """σ_p² = wᵀ(BΩBᵀ + D)w computed independently must match total_variance,
    and the factor / specific split must match the numpy buckets exactly."""
    weights, fits, factor_cov, B, omega, D, w, names, factors = _known_example()
    a = risk_attribution(weights, fits, factor_cov, periods_per_year=1)

    Dmat = np.diag(D)
    cov_total = B @ omega @ B.T + Dmat
    total_np = float(w @ cov_total @ w)
    factor_np = float(w @ (B @ omega @ B.T) @ w)
    spec_np = float(w @ Dmat @ w)

    assert a.total_variance == pytest.approx(total_np, abs=1e-12)
    assert a.factor_variance == pytest.approx(factor_np, abs=1e-12)
    assert a.specific_variance == pytest.approx(spec_np, abs=1e-12)

    # Per-name specific contributions are exactly wᵢ²Dᵢ.
    for i, n in enumerate(names):
        assert a.per_name_specific[n]["variance"] == pytest.approx(
            (w[i] ** 2) * D[i], abs=1e-12
        )

    # Per-factor contributions are the row split xₖ·(Ωx)ₖ with x = Bᵀw.
    x = B.T @ w
    omega_x = omega @ x
    for k, f in enumerate(factors):
        assert a.per_factor[f]["variance"] == pytest.approx(
            float(x[k] * omega_x[k]), abs=1e-12
        )

    # total_vol is sqrt(total_variance).
    assert a.total_vol == pytest.approx(math.sqrt(total_np), abs=1e-12)

    # pct fields sum to 1 across factors + specific.
    pct_sum = sum(d["pct"] for d in a.per_factor.values()) + a.pct_specific
    assert pct_sum == pytest.approx(1.0, abs=1e-12)


def test_annualization_scales_variance() -> None:
    """periods_per_year scales BOTH buckets by the same factor (factor_cov is
    already annualized, so it scales only the specific bucket here — assert the
    specific bucket scales while the factor bucket does not)."""
    weights, fits, factor_cov, *_ = _known_example()
    a1 = risk_attribution(weights, fits, factor_cov, periods_per_year=1)
    a52 = risk_attribution(weights, fits, factor_cov, periods_per_year=52)
    assert a52.factor_variance == pytest.approx(a1.factor_variance, abs=1e-12)
    assert a52.specific_variance == pytest.approx(
        a1.specific_variance * 52, rel=1e-9
    )


# ── Degenerate: single name / single factor ──────────────────────────────────


def test_single_name_single_factor() -> None:
    factors = ["F1"]
    fit = _Fit(betas={"F1": 2.0}, residual_std=0.3)
    weights = {"X": 1.0}
    fits = {"X": fit}
    factor_cov = _cov(factors, [[0.05]])

    a = risk_attribution(weights, fits, factor_cov, periods_per_year=1)
    # x = B'w = 2.0; factor_var = x²·0.05 = 4·0.05 = 0.20
    assert a.factor_variance == pytest.approx(0.20, abs=1e-12)
    # specific = w²σ² = 1·0.09 = 0.09
    assert a.specific_variance == pytest.approx(0.09, abs=1e-12)
    assert a.total_variance == pytest.approx(0.29, abs=1e-12)
    assert a.per_factor["F1"]["variance"] == pytest.approx(0.20, abs=1e-12)
    assert a.per_name_specific["X"]["variance"] == pytest.approx(0.09, abs=1e-12)
    assert a.n_names == 1
    assert a.n_names_missing_fit == 0


# ── Empty input → zeros (no crash) ───────────────────────────────────────────


def test_empty_weights_returns_zeros() -> None:
    a = risk_attribution({}, {}, _cov(["F1"], [[0.05]]))
    assert isinstance(a, RiskAttribution)
    assert a.total_variance == 0.0
    assert a.total_vol == 0.0
    assert a.factor_variance == 0.0
    assert a.specific_variance == 0.0
    assert a.per_factor == {}
    assert a.per_name_specific == {}
    assert a.pct_factor == 0.0
    assert a.pct_specific == 0.0
    assert a.n_names == 0


def test_empty_fits_returns_zeros() -> None:
    a = risk_attribution({"X": 1.0}, {}, _cov(["F1"], [[0.05]]))
    assert a.total_variance == 0.0
    assert a.n_names == 0
    assert a.n_names_missing_fit == 1


def test_none_factor_cov_specific_only() -> None:
    """No factor covariance → factor bucket zero, specific-only is still a valid
    (and additive) decomposition."""
    fit = _Fit(betas={"F1": 2.0}, residual_std=0.3)
    a = risk_attribution({"X": 1.0}, {"X": fit}, None, periods_per_year=1)
    assert a.factor_variance == 0.0
    assert a.specific_variance == pytest.approx(0.09, abs=1e-12)
    assert a.total_variance == pytest.approx(0.09, abs=1e-12)
    assert a.pct_specific == pytest.approx(1.0, abs=1e-12)
    # Identity still holds.
    assert a.factor_variance + a.specific_variance == pytest.approx(
        a.total_variance, abs=1e-12
    )


# ── Name missing its fit → skipped + counted (documented choice) ─────────────


def test_name_missing_fit_is_skipped_and_counted() -> None:
    """A weighted name with no fit contributes NOTHING (factor or specific) and
    is reported via n_names_missing_fit — never silently treated as zero-risk."""
    factors = ["F1"]
    fit = _Fit(betas={"F1": 1.0}, residual_std=0.1)
    weights = {"HAS_FIT": 0.6, "NO_FIT": 0.4}
    fits = {"HAS_FIT": fit}  # NO_FIT absent
    factor_cov = _cov(factors, [[0.04]])

    a = risk_attribution(weights, fits, factor_cov, periods_per_year=1)
    assert a.n_names == 1
    assert a.n_names_missing_fit == 1
    # NO_FIT never appears in the specific breakdown.
    assert set(a.per_name_specific.keys()) == {"HAS_FIT"}
    # Variance reflects ONLY the covered name: x = 0.6·1 = 0.6; factor = 0.36·0.04
    assert a.factor_variance == pytest.approx(0.6 ** 2 * 0.04, abs=1e-12)
    assert a.specific_variance == pytest.approx(0.6 ** 2 * 0.1 ** 2, abs=1e-12)


# ── sqrt-clamp on a float-noise-negative factor variance ─────────────────────


def test_sqrt_clamp_on_tiny_negative_factor_variance() -> None:
    """A non-PSD Ω can drive factor_var slightly negative. total_vol must clamp
    to a real sqrt (no NaN / no ValueError), and the per-factor split is dropped
    so the documented sum-identity can't break."""
    factors = ["F1", "F2"]
    # Indefinite "covariance" with a large off-diagonal → can yield negative
    # quadratic form for some exposures.
    factor_cov = _cov(factors, [[1.0, 5.0], [5.0, 1.0]])
    # Choose loadings/weights so x = B'w hits the negative eigen-direction.
    fit = _Fit(betas={"F1": 1.0, "F2": -1.0}, residual_std=0.0)
    weights = {"X": 1.0}
    fits = {"X": fit}

    a = risk_attribution(weights, fits, factor_cov, periods_per_year=1)
    # x = (1, -1); xᵀΩx = 1 - 5 - 5 + 1 = -8 < 0 → clamped to 0.
    assert a.factor_variance == 0.0
    assert a.total_vol == 0.0  # real number, not NaN
    assert math.isfinite(a.total_vol)
    # Per-factor split dropped on clamp so the identity still holds vacuously.
    assert a.per_factor == {}
    assert sum(d["variance"] for d in a.per_factor.values()) == pytest.approx(
        a.factor_variance, abs=1e-12
    )


def test_returns_dataclass_type() -> None:
    weights, fits, factor_cov, *_ = _known_example()
    a = risk_attribution(weights, fits, factor_cov)
    assert isinstance(a, RiskAttribution)
