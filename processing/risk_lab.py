"""processing/risk_lab.py — VaR, CVaR, scenario stress, regime detection.

Three analytical pieces, all pure functions:

  • Value-at-Risk and Conditional Value-at-Risk via historical and
    parametric methods.
  • Portfolio-level scenario stress: take a weights vector + the
    canonical Scenario catalog from state.scenarios, apply each
    scenario's ``ticker:*.return`` shocks, return per-scenario P&L.
  • Market regime detection: classify current state as Bull / Bear /
    Sideways / Crisis based on rolling return, vol, and drawdown.

Builds on (does not duplicate) the existing tab_risk_matrix UI — which
shows a factor-level risk dashboard. This module focuses on the
portfolio-level questions tab_risk_matrix doesn't cover.

No streamlit, no globals, no module-level mutable state.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

TRADING_DAYS_PER_YEAR: int = 252
"""Standard equity convention for annualization scaling."""


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VaRResult:
    """Value-at-Risk and Conditional VaR for a return series.

    var_pct is the return at the (1-confidence) percentile — always
    non-positive for a loss interpretation. cvar_pct is the mean of
    returns at or below var_pct (the expected loss conditional on
    being in the tail). Both are *fractions* (−0.025 = −2.5%).
    """
    confidence: float
    horizon_days: int
    method: str                  # "historical" | "parametric" | "ewma"
    var_pct: float               # ≤ 0
    cvar_pct: float              # ≤ var_pct ≤ 0
    portfolio_value: float = 0.0
    var_dollar: float = 0.0      # |var_pct| × portfolio_value
    cvar_dollar: float = 0.0
    n_observations: int = 0


@dataclass(frozen=True)
class ScenarioStressResult:
    """Per-scenario P&L impact on a portfolio."""
    scenario_id: str
    scenario_name: str
    category: str
    portfolio_value_before: float
    portfolio_value_after: float
    pnl_dollar: float
    pnl_pct: float
    per_ticker_pnl: dict[str, float] = field(default_factory=dict)  # ticker → $ P&L


@dataclass(frozen=True)
class MarketRegime:
    """Classification of the current market state."""
    label: str                   # "Bull" | "Bear" | "Sideways" | "Crisis"
    confidence: float            # [0, 1] — how certain the classifier is
    indicators: dict[str, float] = field(default_factory=dict)
    interpretation: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# VaR / CVaR
# ─────────────────────────────────────────────────────────────────────────────

# RiskMetrics daily EWMA decay for the vol-adaptive ("ewma") VaR method.
EWMA_LAMBDA = 0.94

# Degrees of freedom for the Student-t tail used by the "ewma_t" method. The
# Gaussian EWMA VaR is coverage-tested OK at 95% but is REJECTED at 99% on real
# 2021-2026 shipping-equity returns (it under-states the fat tail). A Student-t
# tail with nu=6 (excess kurtosis 6/(nu-4)=3.0 — a realistic daily-equity fat
# tail) restores 99% coverage while still passing 95%. nu was picked on the
# real cache: it is the value that keeps BOTH confidences un-rejected by Kupiec
# with the most margin (nu=4 over-breaches the 95% body; nu>=8 starts to
# under-cover the 99% tail again). See ``var_coverage_backtest`` for the test.
EWMA_T_DOF = 6.0


def _student_t_var_es_multipliers(alpha: float, nu: float) -> tuple[float, float]:
    """Left-tail VaR & ES multipliers (per unit sigma) for a *standardized*
    (unit-variance) Student-t with ``nu`` degrees of freedom, at level ``alpha``.

    Returns ``(q, es)`` — both NEGATIVE (left tail), to be multiplied by the
    volatility forecast sigma. The raw Student-t has variance nu/(nu-2), so we
    rescale by ``sqrt((nu-2)/nu)`` to make sigma the true return std (matching
    the Gaussian path, where sigma is the std and the multiplier is z_alpha).

    VaR:  q  = sqrt((nu-2)/nu) * t_ppf(alpha, nu)
    ES :  es = sqrt((nu-2)/nu) * -( f_nu(t_alpha) / alpha ) * (nu + t_alpha^2)/(nu-1)

    Falls back to the Gaussian multipliers when scipy is unavailable.
    """
    if nu <= 2.0:                       # variance undefined for nu<=2 -> Gaussian
        z = _z_alpha(alpha)
        phi = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
        return z, -(phi / alpha)
    try:
        from scipy.stats import t as _t
        scale = math.sqrt((nu - 2.0) / nu)
        t_a = float(_t.ppf(alpha, nu))
        q = scale * t_a
        f = float(_t.pdf(t_a, nu))
        es = scale * (-(f / alpha) * (nu + t_a * t_a) / (nu - 1.0))
        return q, es
    except Exception:
        z = _z_alpha(alpha)
        phi = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
        return z, -(phi / alpha)


def historical_var(
    returns: pd.Series,
    *,
    confidence: float = 0.95,
    horizon_days: int = 1,
    portfolio_value: float = 0.0,
) -> VaRResult:
    """Empirical VaR from the return distribution.

    Uses the (1-confidence) percentile of the historical returns. For
    horizons > 1, the percentile is scaled by sqrt(horizon) — a standard
    parametric scaling that's exact for i.i.d. normal returns and a fine
    approximation for moderate sample sizes.

    Returns a zero-valued result on empty / fewer-than-10-obs input so
    callers don't need to guard.
    """
    if returns is None:
        return _empty_var(confidence, horizon_days, "historical")
    series = pd.Series(returns).dropna()
    if len(series) < 10:
        return _empty_var(confidence, horizon_days, "historical")

    alpha = 1.0 - confidence
    var_pct_1d = float(np.percentile(series.to_numpy(), alpha * 100))
    var_pct = min(0.0, var_pct_1d * math.sqrt(horizon_days))
    # CVaR: mean of returns at or below var_pct_1d (then scaled identically).
    tail = series[series <= var_pct_1d]
    cvar_pct_1d = float(tail.mean()) if not tail.empty else var_pct_1d
    cvar_pct = min(0.0, cvar_pct_1d * math.sqrt(horizon_days))

    return VaRResult(
        confidence=confidence,
        horizon_days=horizon_days,
        method="historical",
        var_pct=round(var_pct, 6),
        cvar_pct=round(cvar_pct, 6),
        portfolio_value=float(portfolio_value),
        var_dollar=round(abs(var_pct) * portfolio_value, 2),
        cvar_dollar=round(abs(cvar_pct) * portfolio_value, 2),
        n_observations=len(series),
    )


def parametric_var(
    returns: pd.Series,
    *,
    confidence: float = 0.95,
    horizon_days: int = 1,
    portfolio_value: float = 0.0,
) -> VaRResult:
    """Gaussian (delta-normal) VaR: μ + z_α × σ scaled to the horizon.

    z_α uses the inverse-normal at α = 1 - confidence. We compute it via
    scipy.stats.norm.ppf when scipy is available; otherwise fall back to
    a Cornish-Fisher-style approximation.
    """
    if returns is None:
        return _empty_var(confidence, horizon_days, "parametric")
    series = pd.Series(returns).dropna()
    if len(series) < 10:
        return _empty_var(confidence, horizon_days, "parametric")

    mu = float(series.mean())
    sigma = float(series.std(ddof=0))
    if sigma <= 0 or not math.isfinite(sigma):
        return _empty_var(confidence, horizon_days, "parametric")

    z = _z_alpha(1.0 - confidence)
    var_pct_1d = mu + z * sigma
    var_pct = min(0.0, var_pct_1d * math.sqrt(horizon_days))
    # Closed-form Gaussian CVaR: mu - sigma * phi(z)/alpha (z ≤ 0 for left tail).
    alpha = 1.0 - confidence
    phi_z = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    cvar_pct_1d = mu - sigma * (phi_z / alpha)
    cvar_pct = min(0.0, cvar_pct_1d * math.sqrt(horizon_days))

    return VaRResult(
        confidence=confidence,
        horizon_days=horizon_days,
        method="parametric",
        var_pct=round(var_pct, 6),
        cvar_pct=round(cvar_pct, 6),
        portfolio_value=float(portfolio_value),
        var_dollar=round(abs(var_pct) * portfolio_value, 2),
        cvar_dollar=round(abs(cvar_pct) * portfolio_value, 2),
        n_observations=len(series),
    )


def _ewma_vol(r: np.ndarray, lam: float) -> float:
    """Latest RiskMetrics EWMA volatility forecast (causal — uses every return).

    ``sigma^2 = lam*sigma^2 + (1-lam)*r^2`` iterated over the series; returns the
    one-step-ahead forecast (the current vol regime), seeded with the sample
    variance of the first (up to) 30 returns. The short ~1/(1-lam) memory makes
    the band track regime shifts a flat sample sigma cannot.
    """
    n = len(r)
    if n == 0:
        return 0.0
    seed = float(np.var(r[:min(30, n)]))
    var = seed if seed > 0.0 else (float(np.var(r)) or 0.0)
    for x in r:
        var = lam * var + (1.0 - lam) * float(x) * float(x)
    return math.sqrt(var) if var > 0.0 else 0.0


def ewma_var(
    returns: pd.Series,
    *,
    confidence: float = 0.95,
    horizon_days: int = 1,
    portfolio_value: float = 0.0,
    lam: float = EWMA_LAMBDA,
    nu: Optional[float] = None,
) -> VaRResult:
    """RiskMetrics EWMA VaR: a zero-mean band scaled by the EWMA volatility
    forecast (responsive to the current vol regime).

    Unlike the flat sample-sigma parametric VaR, the EWMA vol weights recent
    returns more, so the band widens as volatility builds and narrows as it
    subsides. Confirmed on REAL 2021-2026 shipping-equity returns to hold VaR
    breaches both at-nominal AND de-clustered across regimes, where the flat
    historical/parametric VaR is statistically rejected (see
    ``var_coverage_backtest``). Zero-mean by convention — daily drift is
    negligible and noisy at the VaR horizon, and it matches the
    coverage-tested estimator exactly.

    ``nu`` selects the tail distribution: ``None`` (default) is the Gaussian
    band (method ``"ewma"``); a finite ``nu`` uses a *standardized* Student-t
    tail with that many degrees of freedom (method ``"ewma_t"``), which is
    coverage-correct at 99% where the Gaussian under-states the fat tail. The
    sigma scaling is identical, so the t-band only differs in the quantile
    shape — wider in the deep tail, marginally narrower in the body.
    """
    method_name = "ewma" if nu is None else "ewma_t"
    if returns is None:
        return _empty_var(confidence, horizon_days, method_name)
    series = pd.Series(returns).dropna()
    if len(series) < 10:
        return _empty_var(confidence, horizon_days, method_name)

    sigma = _ewma_vol(series.to_numpy(dtype=float), lam)
    # sigma == 0 is a VALID input, NOT "no data": a perfectly-hedged book nets to
    # an identically-zero return series, whose honest VaR/CVaR is exactly 0 (real
    # zero risk). Only a non-finite or negative sigma is degenerate -> empty.
    # (Returning "empty" on sigma==0 was a regression vs historical_var, which
    # correctly reports a real 0 here; it manifested only where the hedge nets to
    # EXACTLY zero — e.g. CI — vs a tiny float residual locally.)
    if not math.isfinite(sigma) or sigma < 0.0:
        return _empty_var(confidence, horizon_days, method_name)

    alpha = 1.0 - confidence
    if nu is None:
        z = _z_alpha(alpha)
        phi_z = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
        q, es = z, -(phi_z / alpha)              # Gaussian VaR & CVaR multipliers
    else:
        q, es = _student_t_var_es_multipliers(alpha, float(nu))
    var_pct_1d = q * sigma                        # zero-mean band (0 if sigma==0)
    var_pct = min(0.0, var_pct_1d * math.sqrt(horizon_days))
    cvar_pct_1d = es * sigma                      # zero-mean tail mean (CVaR)
    cvar_pct = min(0.0, cvar_pct_1d * math.sqrt(horizon_days))

    return VaRResult(
        confidence=confidence,
        horizon_days=horizon_days,
        method=method_name,
        var_pct=round(var_pct, 6),
        cvar_pct=round(cvar_pct, 6),
        portfolio_value=float(portfolio_value),
        var_dollar=round(abs(var_pct) * portfolio_value, 2),
        cvar_dollar=round(abs(cvar_pct) * portfolio_value, 2),
        n_observations=len(series),
    )


def portfolio_var(
    returns_df: pd.DataFrame,
    weights: dict,
    *,
    confidence: float = 0.95,
    horizon_days: int = 1,
    portfolio_value: float = 0.0,
    method: str = "historical",
) -> VaRResult:
    """Portfolio-level VaR via the weighted-return series.

    Builds the portfolio's historical return = Σ w_i · r_{i,t} for each
    period in ``returns_df`` (columns aligned to ``weights`` keys), then
    runs the chosen VaR method on the resulting series.
    """
    if returns_df is None or returns_df.empty or not weights:
        return _empty_var(confidence, horizon_days, method)

    common = [c for c in returns_df.columns if c in weights]
    if not common:
        return _empty_var(confidence, horizon_days, method)

    w_vec = np.array([float(weights[c]) for c in common])
    sub = returns_df[common].dropna()
    if sub.empty:
        return _empty_var(confidence, horizon_days, method)

    portfolio_returns = pd.Series(sub.to_numpy() @ w_vec, index=sub.index)
    if method == "parametric":
        return parametric_var(
            portfolio_returns,
            confidence=confidence, horizon_days=horizon_days,
            portfolio_value=portfolio_value,
        )
    if method == "ewma":
        return ewma_var(
            portfolio_returns,
            confidence=confidence, horizon_days=horizon_days,
            portfolio_value=portfolio_value,
        )
    if method == "ewma_t":
        return ewma_var(
            portfolio_returns,
            confidence=confidence, horizon_days=horizon_days,
            portfolio_value=portfolio_value, nu=EWMA_T_DOF,
        )
    return historical_var(
        portfolio_returns,
        confidence=confidence, horizon_days=horizon_days,
        portfolio_value=portfolio_value,
    )


def _empty_var(confidence: float, horizon_days: int, method: str) -> VaRResult:
    return VaRResult(
        confidence=confidence, horizon_days=horizon_days, method=method,
        var_pct=0.0, cvar_pct=0.0,
        portfolio_value=0.0, var_dollar=0.0, cvar_dollar=0.0,
        n_observations=0,
    )


def _z_alpha(alpha: float) -> float:
    """Inverse normal CDF at ``alpha``. Negative for α<0.5 (left tail).

    Uses scipy when available; otherwise a Beasley-Springer-Moro approximation
    that's accurate enough for the typical VaR confidence levels (0.90/0.95/0.99).
    """
    try:
        from scipy.stats import norm
        return float(norm.ppf(alpha))
    except Exception:
        # Beasley-Springer-Moro algorithm (good to ~6 decimal places).
        if alpha <= 0 or alpha >= 1:
            return 0.0
        a = [-3.969683028665376e+01, 2.209460984245205e+02,
             -2.759285104469687e+02, 1.383577518672690e+02,
             -3.066479806614716e+01, 2.506628277459239e+00]
        b = [-5.447609879822406e+01, 1.615858368580409e+02,
             -1.556989798598866e+02, 6.680131188771972e+01,
             -1.328068155288572e+01]
        c = [-7.784894002430293e-03, -3.223964580411365e-01,
             -2.400758277161838e+00, -2.549732539343734e+00,
             4.374664141464968e+00, 2.938163982698783e+00]
        d = [7.784695709041462e-03, 3.224671290700398e-01,
             2.445134137142996e+00, 3.754408661907416e+00]
        plow = 0.02425
        phigh = 1.0 - plow
        if alpha < plow:
            q = math.sqrt(-2 * math.log(alpha))
            return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                   ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        if alpha <= phigh:
            q = alpha - 0.5
            r = q * q
            return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
                   (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
        q = math.sqrt(-2 * math.log(1 - alpha))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


# ─────────────────────────────────────────────────────────────────────────────
# Scenario stress
# ─────────────────────────────────────────────────────────────────────────────

def stress_test_scenario(
    weights: dict,
    scenario,
    *,
    portfolio_value: float = 1_000_000.0,
) -> ScenarioStressResult:
    """Apply one Scenario's ``ticker:*.return`` shocks to a weights vector.

    For each ticker in ``weights``, look up the active scenario's overlay
    multiplier on ``ticker:<T>.return``. The multiplier represents the
    scenario's effect on that ticker's expected return — we treat
    ``(multiplier - 1.0)`` as the immediate P&L impact (e.g., 1.18 → +18%).

    Returns a :class:`ScenarioStressResult` with per-ticker dollar P&L.
    """
    try:
        from state.scenarios import overlay_multiplier
    except Exception:
        overlay_multiplier = lambda target, scenario=None: 1.0  # type: ignore

    per_ticker_pnl: dict[str, float] = {}
    portfolio_pnl_pct = 0.0
    for ticker, weight in weights.items():
        w = float(weight)
        mult = overlay_multiplier(f"ticker:{ticker}.return", scenario)
        position_pnl_pct = (mult - 1.0)  # the shock applied to the ticker
        contribution = w * position_pnl_pct
        portfolio_pnl_pct += contribution
        per_ticker_pnl[ticker] = round(contribution * portfolio_value, 2)

    portfolio_value_after = portfolio_value * (1.0 + portfolio_pnl_pct)

    return ScenarioStressResult(
        scenario_id=getattr(scenario, "id", "unknown"),
        scenario_name=getattr(scenario, "name", "Unknown"),
        category=getattr(scenario, "category", ""),
        portfolio_value_before=round(portfolio_value, 2),
        portfolio_value_after=round(portfolio_value_after, 2),
        pnl_dollar=round(portfolio_value_after - portfolio_value, 2),
        pnl_pct=round(portfolio_pnl_pct, 6),
        per_ticker_pnl=per_ticker_pnl,
    )


def stress_test_all_scenarios(
    weights: dict,
    *,
    portfolio_value: float = 1_000_000.0,
) -> list[ScenarioStressResult]:
    """Run :func:`stress_test_scenario` against every scenario in the catalog.

    Returns results sorted by ``pnl_pct`` ascending (worst loss first) so
    the tail risk is at the top of the list.
    """
    try:
        from state.scenarios import list_scenarios
        scenarios = list_scenarios()
    except Exception:
        return []
    results = [
        stress_test_scenario(weights, scen, portfolio_value=portfolio_value)
        for scen in scenarios
    ]
    results.sort(key=lambda r: r.pnl_pct)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Regime detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_regime(
    returns: pd.Series,
    *,
    short_window: int = 21,
    long_window: int = 63,
    crisis_drawdown: float = -0.15,
    bull_return: float = 0.10,
    bear_return: float = -0.08,
) -> MarketRegime:
    """Classify the current market state from a return series.

    Decision tree:

      1. **Crisis** — trailing 3-month max drawdown ≤ ``crisis_drawdown``
         AND realized vol > 1.5× long-run vol.
      2. **Bull** — annualized return over the long window ≥ ``bull_return``
         AND vol is not elevated.
      3. **Bear** — annualized return ≤ ``bear_return``.
      4. **Sideways** — everything else.

    Indicators are returned alongside the label so the UI can show
    *why* the regime call was made.

    Empty / insufficient input returns ``label="Unknown"`` with confidence 0.
    """
    if returns is None:
        return _unknown_regime()
    series = pd.Series(returns).dropna()
    if len(series) < long_window:
        return _unknown_regime()

    # Realized return (annualized) over the long window.
    long_tail = series.iloc[-long_window:]
    short_tail = series.iloc[-short_window:]
    ann_return = float(long_tail.mean() * TRADING_DAYS_PER_YEAR)
    ann_vol = float(long_tail.std(ddof=0) * math.sqrt(TRADING_DAYS_PER_YEAR))

    # Long-run vol from the whole series for the elevation check.
    long_run_vol = float(series.std(ddof=0) * math.sqrt(TRADING_DAYS_PER_YEAR))
    vol_ratio = (ann_vol / long_run_vol) if long_run_vol > 1e-9 else 1.0

    # Drawdown over the short window.
    equity = (1.0 + short_tail).cumprod()
    peak = equity.cummax()
    drawdown = ((equity - peak) / peak).min()
    drawdown = float(drawdown) if not math.isnan(drawdown) else 0.0

    indicators = {
        "ann_return_long": round(ann_return, 4),
        "ann_vol_long": round(ann_vol, 4),
        "vol_ratio": round(vol_ratio, 3),
        "short_drawdown": round(drawdown, 4),
    }

    # Classification.
    if drawdown <= crisis_drawdown and vol_ratio > 1.5:
        label = "Crisis"
        confidence = min(1.0, max(0.3, abs(drawdown) / abs(crisis_drawdown)))
        interp = (
            f"Trailing {short_window}-day drawdown {drawdown*100:+.1f}% with "
            f"vol {vol_ratio:.1f}× normal — crisis-regime signature."
        )
    elif ann_return >= bull_return and vol_ratio <= 1.3:
        label = "Bull"
        confidence = min(1.0, max(0.3, ann_return / max(bull_return, 1e-6)))
        interp = (
            f"Annualized return {ann_return*100:+.1f}% over the long window "
            f"with vol contained at {vol_ratio:.1f}× normal."
        )
    elif ann_return <= bear_return:
        label = "Bear"
        confidence = min(1.0, max(0.3, abs(ann_return) / abs(bear_return)))
        interp = (
            f"Annualized return {ann_return*100:+.1f}% over the long window "
            f"— consistent with a sustained bear regime."
        )
    else:
        label = "Sideways"
        confidence = 0.5
        interp = (
            f"Annualized return {ann_return*100:+.1f}% with vol "
            f"{vol_ratio:.1f}× normal — no clear directional regime."
        )

    return MarketRegime(
        label=label, confidence=round(confidence, 3),
        indicators=indicators, interpretation=interp,
    )


def _unknown_regime() -> MarketRegime:
    return MarketRegime(
        label="Unknown", confidence=0.0,
        indicators={}, interpretation="Insufficient data for regime classification.",
    )


__all__ = [
    "VaRResult",
    "ScenarioStressResult",
    "MarketRegime",
    "historical_var",
    "parametric_var",
    "ewma_var",
    "portfolio_var",
    "EWMA_LAMBDA",
    "EWMA_T_DOF",
    "stress_test_scenario",
    "stress_test_all_scenarios",
    "detect_regime",
]
