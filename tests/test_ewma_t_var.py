"""Tests for the Student-t fat-tailed EWMA VaR ("ewma_t").

The Gaussian EWMA VaR is coverage-tested OK at 95% but is Kupiec-REJECTED at
99% on real 2021-2026 shipping-equity returns — it under-states the fat tail.
The ``ewma_t`` method keeps the identical EWMA vol forecast but replaces the
Gaussian quantile with a *standardized* Student-t one (nu = EWMA_T_DOF),
restoring 99% coverage while still passing 95%.

Four groups:
  1. Student-t VaR/ES multipliers — deterministic shape properties
  2. risk_lab.ewma_var(nu=...) — the live-engine tail
  3. var_coverage_backtest "ewma_t" — the walk-forward defining property
  4. Shared-tail consistency — backtest and engine use the identical nu

All synthetic inputs are deterministic (explicit int seeds, never salted hash()).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from processing.risk_lab import (
    EWMA_T_DOF,
    _student_t_var_es_multipliers,
    _z_alpha,
    ewma_var,
    portfolio_var,
)


# ── Group 1: standardized Student-t multipliers ──────────────────────────────

def test_t_tail_is_deeper_than_gaussian_at_99() -> None:
    """The whole point: at 99% the t quantile is strictly MORE negative than the
    Gaussian z, so the VaR band is wider in the deep tail."""
    q, es = _student_t_var_es_multipliers(0.01, EWMA_T_DOF)
    z = _z_alpha(0.01)
    assert q < z < 0.0           # t VaR multiplier deeper than Gaussian
    assert es < q < 0.0          # ES (tail mean) deeper still than VaR


def test_t_body_is_shallower_than_gaussian_at_95() -> None:
    """The crossover that makes nu matter: a unit-variance t has THINNER
    shoulders, so at 95% it is marginally LESS negative than Gaussian. Picking
    nu too small would over-breach the body — this documents the trade-off."""
    q, _ = _student_t_var_es_multipliers(0.05, EWMA_T_DOF)
    z = _z_alpha(0.05)
    assert z < q < 0.0           # t body multiplier shallower than Gaussian


def test_t_multiplier_matches_standardized_closed_form() -> None:
    """VaR multiplier == t_ppf(alpha, nu) * sqrt((nu-2)/nu) (unit-variance scaling)."""
    t = pytest.importorskip("scipy.stats").t
    for alpha in (0.05, 0.01):
        q, _ = _student_t_var_es_multipliers(alpha, EWMA_T_DOF)
        expect = float(t.ppf(alpha, EWMA_T_DOF)) * math.sqrt(
            (EWMA_T_DOF - 2.0) / EWMA_T_DOF)
        assert q == pytest.approx(expect, rel=1e-9)


def test_t_converges_to_gaussian_as_nu_grows() -> None:
    """nu -> infinity is the normal distribution — both VaR and ES multipliers
    converge to the Gaussian values."""
    q, es = _student_t_var_es_multipliers(0.01, 1e7)
    z = _z_alpha(0.01)
    phi = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    gauss_es = -(phi / 0.01)
    assert q == pytest.approx(z, abs=1e-3)
    assert es == pytest.approx(gauss_es, abs=1e-3)


def test_t_falls_back_to_gaussian_when_variance_undefined() -> None:
    """nu <= 2 has no finite variance (sigma is undefined as a scale) -> Gaussian."""
    q, es = _student_t_var_es_multipliers(0.01, 2.0)
    z = _z_alpha(0.01)
    assert q == pytest.approx(z, abs=1e-9)


# ── Group 2: live engine — risk_lab.ewma_var(nu=...) ─────────────────────────

def _series(seed: int, n: int = 500, scale: float = 0.02) -> pd.Series:
    return pd.Series(np.random.default_rng(seed).standard_normal(n) * scale)


def test_ewma_var_method_label_tracks_nu() -> None:
    s = _series(1)
    assert ewma_var(s, confidence=0.99).method == "ewma"
    assert ewma_var(s, confidence=0.99, nu=EWMA_T_DOF).method == "ewma_t"


def test_ewma_t_is_more_conservative_in_the_99_tail() -> None:
    s = _series(2)
    g = ewma_var(s, confidence=0.99)
    t = ewma_var(s, confidence=0.99, nu=EWMA_T_DOF)
    assert t.var_pct < g.var_pct < 0.0          # t VaR deeper
    assert t.cvar_pct <= t.var_pct <= 0.0        # CVaR at least as deep as VaR


def test_ewma_t_body_marginally_shallower_at_95() -> None:
    s = _series(3)
    g = ewma_var(s, confidence=0.95)
    t = ewma_var(s, confidence=0.95, nu=EWMA_T_DOF)
    assert g.var_pct < t.var_pct < 0.0          # t body less deep than Gaussian


def test_ewma_t_hedged_book_is_real_zero_not_empty() -> None:
    """sigma == 0 (a perfectly hedged book) is a VALID real-zero VaR, not 'no
    data' — same invariant the Gaussian path guarantees."""
    z = ewma_var(pd.Series([0.0] * 50), confidence=0.99, nu=EWMA_T_DOF)
    assert z.var_pct == 0.0 and z.cvar_pct == 0.0
    assert z.n_observations == 50 and z.method == "ewma_t"


def test_ewma_t_too_short_history_is_empty() -> None:
    z = ewma_var(pd.Series([0.01] * 5), confidence=0.99, nu=EWMA_T_DOF)
    assert z.n_observations == 0 and z.method == "ewma_t"


def test_ewma_var_nu_converges_to_gaussian() -> None:
    s = _series(4)
    big = ewma_var(s, confidence=0.99, nu=1e9)
    g = ewma_var(s, confidence=0.99)
    assert big.var_pct == pytest.approx(g.var_pct, rel=1e-4)
    assert big.cvar_pct == pytest.approx(g.cvar_pct, rel=1e-4)


def test_portfolio_var_dispatches_ewma_t() -> None:
    rng = np.random.default_rng(5)
    df = pd.DataFrame({"A": rng.standard_normal(300) * 0.02,
                       "B": rng.standard_normal(300) * 0.02})
    w = {"A": 0.5, "B": 0.5}
    pv = portfolio_var(df, w, confidence=0.99, method="ewma_t")
    assert pv.method == "ewma_t"
    # Identical to running ewma_var(nu=EWMA_T_DOF) on the weighted series.
    weighted = pd.Series(df[["A", "B"]].to_numpy() @ np.array([0.5, 0.5]),
                         index=df.index)
    direct = ewma_var(weighted, confidence=0.99, nu=EWMA_T_DOF)
    assert pv.var_pct == pytest.approx(direct.var_pct, rel=1e-12)


# ── Group 3: walk-forward defining property (CI-safe synthetic) ───────────────

def _fat_tailed_panel(seed: int, *, n: int = 2600, nu: float = 4.0,
                      scale: float = 0.02, n_tickers: int = 3) -> pd.DataFrame:
    """Deterministic FAT-TAILED return panel: standardized Student-t draws (nu=4,
    fatter than the model's nu=6) scaled to a realistic daily vol. The Gaussian
    EWMA VaR systematically under-covers the 99% tail of such data."""
    t = pytest.importorskip("scipy.stats").t
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    cols = {}
    for i in range(n_tickers):
        raw = t.rvs(nu, size=n, random_state=rng)
        cols[f"T{i}"] = raw * math.sqrt((nu - 2.0) / nu) * scale
    return pd.DataFrame(cols, index=idx)


def test_gaussian_ewma_is_rejected_at_99_on_fat_tails() -> None:
    """Baseline failure mode that motivates ewma_t: the Gaussian band is Kupiec-
    rejected at 99% on fat-tailed data (it breaches far more than 1%)."""
    from processing.var_coverage_backtest import backtest_var_coverage
    g = backtest_var_coverage(_fat_tailed_panel(3), confidence=0.99,
                              window=120, method="ewma")
    assert g.basis == "real"
    assert g.rejected and g.breach_rate > 0.01


def test_ewma_t_restores_99_coverage_on_fat_tails() -> None:
    """The fix: same panel, same window — the Student-t tail is NOT rejected at
    99% and lands the breach rate near the 1% nominal."""
    from processing.var_coverage_backtest import backtest_var_coverage
    t = backtest_var_coverage(_fat_tailed_panel(3), confidence=0.99,
                              window=120, method="ewma_t")
    assert t.basis == "real"
    assert not t.rejected


def test_ewma_t_breaches_fewer_and_closer_than_gaussian_at_99() -> None:
    """Structural guarantee that holds on ANY fat-tailed panel (deeper band):
    fewer breaches than Gaussian, and a breach rate closer to the 1% nominal."""
    from processing.var_coverage_backtest import backtest_var_coverage
    panel = _fat_tailed_panel(3)
    g = backtest_var_coverage(panel, confidence=0.99, window=120, method="ewma")
    t = backtest_var_coverage(panel, confidence=0.99, window=120, method="ewma_t")
    assert t.n_breaches < g.n_breaches
    assert abs(t.breach_rate - 0.01) <= abs(g.breach_rate - 0.01)


def test_ewma_t_does_not_break_the_calibrated_95_case() -> None:
    """ewma_t must not over-tighten where Gaussian was already fine: on the same
    fat-tailed panel, the 95% ewma_t coverage is still not rejected."""
    from processing.var_coverage_backtest import backtest_var_coverage
    t95 = backtest_var_coverage(_fat_tailed_panel(3), confidence=0.95,
                                window=120, method="ewma_t")
    assert t95.basis == "real" and not t95.rejected


# ── Group 4: shared-tail consistency ─────────────────────────────────────────

def test_backtest_and_engine_share_the_same_tail() -> None:
    """The backtest and the deployed engine must use the IDENTICAL Student-t
    tail, or a passing backtest would not vouch for the live number. The backtest
    imports BOTH the nu constant and the multiplier helper from risk_lab — one
    implementation, no second copy to drift — so the only real risk is the
    constant diverging. Lock it."""
    import processing.var_coverage_backtest as bt
    assert bt._EWMA_T_DOF == EWMA_T_DOF == 6.0

    # The shared deep tail is strictly wider than Gaussian — the property the
    # backtest relies on when it stops rejecting at 99%.
    q_engine, _ = _student_t_var_es_multipliers(0.01, EWMA_T_DOF)
    assert q_engine < _z_alpha(0.01) < 0.0
