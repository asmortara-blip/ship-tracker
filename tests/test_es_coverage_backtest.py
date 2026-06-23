"""Tests for the Acerbi-Szekely (2014) Test 2 ES-coverage backtest (R268).

The VaR quantile is coverage-tested (R070); this validates the ES tail-MEAN on
realized P&L — the only realized-loss test of the number the desk sizes against.

Groups:
  1. Defining property — Z2 is monotone in the ES scale (halved → strongly
     negative, doubled → positive), the load-bearing falsification lever.
  2. Verdict on real-shaped synthetic data — a correctly scaled ES is not
     rejected; an ES that under-states a fat tail IS rejected.
  3. Honesty / plumbing — insufficiency, determinism, shared EWMA-t tail, and a
     CI-safe live entry.

All synthetic inputs are deterministic (explicit int seeds, never salted hash()).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from processing.es_coverage_backtest import (
    backtest_es_coverage,
    run_es_coverage_backtest,
    _es_z2,
    _simulate_z2_critical,
)
from processing.risk_lab import EWMA_T_DOF, _student_t_var_es_multipliers


def _t_panel(nu_data: float, seed: int, *, n: int = 2600, scale: float = 0.02,
             n_tickers: int = 3) -> pd.DataFrame:
    """Deterministic standardized-Student-t return panel with ``nu_data`` dof."""
    t = pytest.importorskip("scipy.stats").t
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    s = math.sqrt((nu_data - 2.0) / nu_data)
    return pd.DataFrame(
        {f"T{i}": t.rvs(nu_data, size=n, random_state=rng) * s * scale
         for i in range(n_tickers)}, index=idx)


# ── Group 1: defining property — Z2 monotone in the ES scale ──────────────────

def test_z2_monotone_halved_negative_doubled_positive() -> None:
    """The load-bearing lever: with the breach set fixed, halving the ES forecast
    drives Z2 strongly negative (realized losses dwarf the forecast) and doubling
    drives it positive. Monotone increasing in the ES scale."""
    pytest.importorskip("scipy.stats")
    nu = EWMA_T_DOF
    rng = np.random.default_rng(0)
    from scipy.stats import t as st
    r = (st.rvs(nu, size=1500, random_state=rng) * math.sqrt((nu - 2) / nu)) * 0.02
    q, es_mult = _student_t_var_es_multipliers(0.05, nu)
    es_mag = -es_mult
    _, _, z_ok, _, _ = _es_z2(r, window=60, q=q, es_mag=es_mag, alpha=0.05)
    _, _, z_half, _, _ = _es_z2(r, window=60, q=q, es_mag=es_mag * 0.5, alpha=0.05)
    _, _, z_dbl, _, _ = _es_z2(r, window=60, q=q, es_mag=es_mag * 2.0, alpha=0.05)
    assert z_half < z_ok < z_dbl            # monotone increasing in ES scale
    assert z_half < -0.5                     # halved ES → strongly negative
    assert z_dbl > 0.0                       # doubled ES → positive


# ── Group 2: verdict on real-shaped synthetic data ────────────────────────────

def test_well_scaled_es_not_rejected() -> None:
    """Returns drawn from the SAME tail the model assumes (nu=6): the ES is
    correctly scaled, so Test 2 does not reject."""
    sc = backtest_es_coverage(_t_panel(6.0, seed=2), confidence=0.95, nu=6.0)
    assert sc.basis == "real" and sc.n_breaches > 0
    assert not sc.rejected and sc.well_scaled


def test_underestimated_es_is_rejected() -> None:
    """Returns drawn from a FATTER tail (nu=3) than the model assumes (nu=6): the
    ES under-states the realized tail mean, so Test 2 rejects."""
    sc = backtest_es_coverage(_t_panel(3.0, seed=3), confidence=0.95, nu=6.0)
    assert sc.basis == "real" and sc.rejected and not sc.well_scaled
    # The economic reason it rejects: realized tail loss exceeds the ES forecast.
    assert -sc.mean_tail_loss > sc.mean_es_forecast
    assert sc.z2 < sc.z2_critical


# ── Group 3: honesty / plumbing ───────────────────────────────────────────────

def test_empty_panel_is_insufficient() -> None:
    sc = backtest_es_coverage(pd.DataFrame())
    assert sc.basis == "insufficient" and sc.n_observations == 0


def test_zero_vol_book_is_insufficient_not_fabricated() -> None:
    """A degenerate zero-vol panel yields no usable forecast days — honest
    'insufficient', never a fabricated verdict."""
    idx = pd.date_range("2020-01-01", periods=400, freq="B")
    flat = pd.DataFrame({"A": [0.0] * 400, "B": [0.0] * 400}, index=idx)
    sc = backtest_es_coverage(flat, confidence=0.95)
    assert sc.basis == "insufficient"


def test_critical_value_is_deterministic() -> None:
    """Same seed → identical simulated critical value (a reproducible verdict)."""
    a = _simulate_z2_critical(1000, 0.05, EWMA_T_DOF, *(
        lambda qe: (qe[0], -qe[1]))(_student_t_var_es_multipliers(0.05, EWMA_T_DOF)),
        n_sims=500, seed=7)
    b = _simulate_z2_critical(1000, 0.05, EWMA_T_DOF, *(
        lambda qe: (qe[0], -qe[1]))(_student_t_var_es_multipliers(0.05, EWMA_T_DOF)),
        n_sims=500, seed=7)
    assert a == b


def test_shares_the_ewma_t_tail() -> None:
    """The ES magnitude the backtest scores against is the live engine's EWMA-t
    ES (same EWMA_T_DOF + multiplier helper), so a pass vouches for the deployed
    number rather than a parallel re-implementation."""
    assert EWMA_T_DOF == 6.0
    q, es_mult = _student_t_var_es_multipliers(0.01, EWMA_T_DOF)
    assert es_mult < q < 0.0                 # ES deeper than VaR, both negative


def test_run_es_coverage_real_or_insufficient_ci_safe() -> None:
    """Live entry must run headless: 'real' with a cache, 'insufficient' on a
    fresh CI checkout — never raise, never fabricate."""
    sc = run_es_coverage_backtest(confidence=0.99)
    assert sc.basis in ("real", "insufficient")
    if sc.basis == "real":
        assert sc.n_breaches > 0 and isinstance(sc.well_scaled, bool)
        assert math.isfinite(sc.z2) and math.isfinite(sc.z2_critical)
