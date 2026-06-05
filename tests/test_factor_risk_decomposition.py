"""Factor covariance + factor-vs-specific risk decomposition (rec R107)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.carrier_factor_model import (
    factor_covariance,
    factor_risk_decomposition,
)


class _Fit:
    def __init__(self, betas, residual_std):
        self.betas = betas
        self.residual_std = residual_std


# ── factor_covariance ───────────────────────────────────────────────────────

def test_factor_covariance_is_annualized() -> None:
    rng = np.random.default_rng(1)
    f = pd.DataFrame({"dBDI": rng.normal(0, 0.04, 300),
                      "dBrent": rng.normal(0, 0.03, 300)})
    cov = factor_covariance(f, periods_per_year=52)
    assert list(cov.columns) == ["dBDI", "dBrent"]
    assert cov.loc["dBDI", "dBDI"] == pytest.approx(f["dBDI"].var() * 52, rel=1e-9)


def test_factor_covariance_empty() -> None:
    assert factor_covariance(pd.DataFrame()).empty
    assert factor_covariance(pd.DataFrame({"x": [1.0]})).empty  # <2 obs


# ── factor_risk_decomposition ───────────────────────────────────────────────

def _diag_cov(factors, var):
    m = np.diag([var] * len(factors))
    return pd.DataFrame(m, index=factors, columns=factors)


def test_zero_residual_is_all_factor_risk() -> None:
    factors = ["dBDI", "dBrent"]
    cov = _diag_cov(factors, 0.04)
    fits = {"ZIM": _Fit({"dBDI": 1.5, "dBrent": 0.5}, 0.0),
            "MATX": _Fit({"dBDI": 0.5, "dBrent": 1.0}, 0.0)}
    d = factor_risk_decomposition({"ZIM": 0.5, "MATX": 0.5}, fits, cov, periods_per_year=1)
    assert d.specific_vol == pytest.approx(0.0)
    assert d.pct_factor == pytest.approx(1.0)
    assert d.total_vol > 0
    # per-factor contributions sum to the factor variance
    assert sum(d.per_factor_var.values()) == pytest.approx(d.factor_vol ** 2, rel=1e-9)


def test_zero_beta_is_all_specific_risk() -> None:
    factors = ["dBDI"]
    cov = _diag_cov(factors, 0.04)
    fits = {"ZIM": _Fit({"dBDI": 0.0}, 0.2)}
    d = factor_risk_decomposition({"ZIM": 1.0}, fits, cov, periods_per_year=1)
    assert d.factor_vol == pytest.approx(0.0)
    assert d.pct_specific == pytest.approx(1.0)
    assert d.specific_vol == pytest.approx(0.2)  # sqrt(1^2 * 0.2^2 * 1)


def test_pcts_sum_to_one_for_mixed_risk() -> None:
    factors = ["dBDI"]
    cov = _diag_cov(factors, 0.04)
    fits = {"ZIM": _Fit({"dBDI": 1.0}, 0.1)}
    d = factor_risk_decomposition({"ZIM": 1.0}, fits, cov, periods_per_year=1)
    assert d.pct_factor + d.pct_specific == pytest.approx(1.0)
    assert 0.0 < d.pct_factor < 1.0
    # total variance = factor + specific
    assert d.total_vol ** 2 == pytest.approx(d.factor_vol ** 2 + d.specific_vol ** 2, rel=1e-9)


def test_empty_inputs_return_zero() -> None:
    d = factor_risk_decomposition({}, {}, pd.DataFrame())
    assert d.total_vol == 0.0 and d.n_names == 0
    d2 = factor_risk_decomposition({"ZIM": 1.0}, {}, _diag_cov(["dBDI"], 0.04))
    assert d2.n_names == 0  # no fit for ZIM


def test_names_without_fits_are_skipped() -> None:
    factors = ["dBDI"]
    cov = _diag_cov(factors, 0.04)
    fits = {"ZIM": _Fit({"dBDI": 1.0}, 0.1)}  # only ZIM has a fit
    d = factor_risk_decomposition({"ZIM": 0.5, "GHOST": 0.5}, fits, cov, periods_per_year=1)
    assert d.n_names == 1
