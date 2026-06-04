"""Book-level factor exposure aggregation (rec R106)."""

from __future__ import annotations

import pytest

from engine.carrier_factor_model import (
    DEFAULT_FACTORS,
    BookFactorExposure,
    portfolio_factor_exposures,
)


def _betas(**kw):
    """A raw beta dict over DEFAULT_FACTORS (missing factors -> 0)."""
    return {f: float(kw.get(f, 0.0)) for f in DEFAULT_FACTORS}


def test_single_name_full_weight_passes_through_betas() -> None:
    exp = portfolio_factor_exposures(
        {"ZIM": 1.0}, {"ZIM": _betas(dBDI=1.5, VIX=-0.4)}
    )
    assert exp.exposures["dBDI"] == pytest.approx(1.5)
    assert exp.exposures["VIX"] == pytest.approx(-0.4)
    assert exp.n_names == 1
    assert exp.coverage == pytest.approx(1.0)


def test_exposure_is_weighted_sum_of_betas() -> None:
    weights = {"ZIM": 0.6, "SBLK": 0.4}
    fits = {"ZIM": _betas(dBDI=1.0), "SBLK": _betas(dBDI=2.0)}
    exp = portfolio_factor_exposures(weights, fits)
    # 0.6*1.0 + 0.4*2.0 = 1.4
    assert exp.exposures["dBDI"] == pytest.approx(1.4)


def test_long_short_book_nets_out() -> None:
    weights = {"ZIM": 1.0, "SBLK": -1.0}
    fits = {"ZIM": _betas(dBDI=1.2), "SBLK": _betas(dBDI=1.2)}
    exp = portfolio_factor_exposures(weights, fits)
    assert exp.exposures["dBDI"] == pytest.approx(0.0, abs=1e-9)


def test_missing_fit_lowers_coverage_and_is_not_treated_as_zero() -> None:
    weights = {"ZIM": 0.5, "MYSTERY": 0.5}
    fits = {"ZIM": _betas(dBDI=2.0)}  # no fit for MYSTERY
    exp = portfolio_factor_exposures(weights, fits)
    assert exp.n_names == 1
    assert exp.coverage == pytest.approx(0.5)
    # only the covered name contributes (0.5 * 2.0)
    assert exp.exposures["dBDI"] == pytest.approx(1.0)


def test_accepts_carrier_factor_fit_objects() -> None:
    class _Fit:
        betas = {"dBDI": 0.8}
    exp = portfolio_factor_exposures({"ZIM": 1.0}, {"ZIM": _Fit()})
    assert exp.exposures["dBDI"] == pytest.approx(0.8)


def test_dollar_tilt_scales_by_book_value() -> None:
    exp = portfolio_factor_exposures({"ZIM": 1.0}, {"ZIM": _betas(dBDI=1.5)})
    tilt = exp.dollar_tilt(1_000_000)
    assert tilt["dBDI"] == pytest.approx(1_500_000)


def test_empty_book_is_zero_exposure_zero_coverage() -> None:
    exp = portfolio_factor_exposures({}, {})
    assert isinstance(exp, BookFactorExposure)
    assert all(v == 0.0 for v in exp.exposures.values())
    assert exp.n_names == 0
    assert exp.coverage == 0.0


def test_exposures_cover_all_default_factors() -> None:
    exp = portfolio_factor_exposures({"ZIM": 1.0}, {"ZIM": _betas(dBDI=1.0)})
    assert set(exp.exposures.keys()) == set(DEFAULT_FACTORS)
