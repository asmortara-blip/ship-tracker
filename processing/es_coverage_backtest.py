"""Acerbi-Szekely (2014) Test 2 — Expected-Shortfall coverage on realized P&L (R268).

The VaR QUANTILE is coverage-tested against realized returns (``var_coverage_backtest``,
R070). But ES — the "coherent" tail measure the desk actually sizes against
(``risk_lab`` CVaR, the cascade Stress-ES, the sized-book ES) — was never validated
on realized losses. A VaR can pass Kupiec while the ES is badly mis-scaled. This is
the only realized-P&L test of the tail MEAN.

Acerbi-Szekely Test 2 (the "unconditional ES" Z-statistic):

    Z2 = ( 1 / (N·alpha) ) · Σ_t [ X_t · I_t / ES_t ]  +  1

with ``X_t`` the realized return, ``I_t = 1{X_t < VaR_t}`` the DEPLOYED VaR's breach
indicator, ``ES_t`` the Expected-Shortfall forecast (a positive loss magnitude),
``N`` the out-of-sample day count and ``alpha = 1 − confidence``. Under a correctly
scaled ES, ``E[Z2] = 0``; realized tail losses LARGER than the ES forecast drive Z2
negative (ES underestimated), smaller drive it positive (overestimated). One-sided:
reject when Z2 falls below a simulated critical value.

Because Z2 reuses the deployed VaR's breach indicator and the ``1/(N·alpha)``
normalization, it INHERITS the VaR's calibration error — it is reported NEXT TO the
Kupiec result, never standalone. The ES forecast reuses the live engine's EWMA-t
tail (``risk_lab``, ``EWMA_T_DOF``), so a passing backtest vouches for the deployed
ES. NETWORK-FREE / headless: reads the same real cached closes as the VaR backtest.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from processing.risk_lab import EWMA_T_DOF, _student_t_var_es_multipliers
from processing.var_coverage_backtest import (
    _DEFAULT_TICKERS, _EWMA_LAMBDA, _ewma_sigma, _load_cached_stock_data,
)


@dataclass
class ESCoverageScorecard:
    """One Acerbi-Szekely ES-coverage run, normalised for the backtest report."""
    confidence: float
    nu: float
    window: int
    n_observations: int
    n_breaches: int
    z2: float                       # Acerbi-Szekely Test 2 statistic (~0 if well-scaled)
    z2_critical: float              # one-sided 5% critical value (simulated under H0)
    rejected: bool                  # z2 < z2_critical → ES underestimates the tail
    well_scaled: bool
    mean_tail_loss: float           # realized mean return over breach days (negative)
    mean_es_forecast: float         # mean ES forecast over breach days (positive)
    basis: str                      # "real" | "insufficient"
    summary: str
    tickers: list = field(default_factory=list)


def _es_z2(r: np.ndarray, *, window: int, q: float, es_mag: float, alpha: float):
    """Acerbi-Szekely Test 2 over a return array with a causal EWMA sigma path.

    ``q`` is the (negative) standardized VaR multiplier and ``es_mag`` the POSITIVE
    standardized ES magnitude (per unit sigma); the day's forecasts are ``q·sigma_t``
    and ``es_mag·sigma_t``. Returns ``(n_obs, n_breaches, z2, mean_tail_loss,
    mean_es_forecast)`` over the OOS span (every day after the first full window).
    Degenerate zero-vol days (sigma ≤ 0) are skipped. ``z2`` is NaN when n_obs == 0.
    """
    n = len(r)
    if n <= window + 1:
        return 0, 0, float("nan"), 0.0, 0.0
    ewma = _ewma_sigma(r, _EWMA_LAMBDA, window)
    obs = nb = 0
    z2_sum = tl_sum = es_sum = 0.0
    for t in range(window, n):
        sig = ewma[t]
        if not (sig > 0.0):                 # degenerate zero-vol forecast — skip
            continue
        var_t = q * sig                     # negative VaR threshold
        es_t = es_mag * sig                 # positive ES forecast
        obs += 1
        if r[t] < var_t:                    # the deployed VaR's breach indicator
            nb += 1
            z2_sum += r[t] / es_t
            tl_sum += r[t]
            es_sum += es_t
    if obs == 0:
        return 0, 0, float("nan"), 0.0, 0.0
    z2 = z2_sum / (obs * alpha) + 1.0
    mean_tl = (tl_sum / nb) if nb else 0.0
    mean_es = (es_sum / nb) if nb else 0.0
    return obs, nb, float(z2), float(mean_tl), float(mean_es)


def _simulate_z2_critical(n_obs: int, alpha: float, nu: float, q: float,
                          es_mag: float, *, n_sims: int = 2000, seed: int = 0,
                          level: float = 0.05) -> float:
    """One-sided ``level`` critical value for Z2 under H0 (returns ~ standardized-t).

    Under the null the volatility cancels — ``X_t/ES_t = z_t/es_mag`` and the breach
    is ``z_t < q`` — so the Z2 distribution depends only on ``(n_obs, alpha, nu, q,
    es_mag)``, NOT on the realized sigma path. Simulated with a fixed seed, so the
    critical value is deterministic. Reject the deployed ES when the realized Z2 is
    below this (realized tail losses systematically exceed the ES forecast).
    """
    try:
        from scipy.stats import t as student_t
    except Exception:
        return float("nan")
    rng = np.random.default_rng(seed)
    scale = math.sqrt((nu - 2.0) / nu)
    z2s = np.empty(n_sims)
    for s in range(n_sims):
        z = student_t.rvs(nu, size=n_obs, random_state=rng) * scale
        tail = z[z < q]
        z2s[s] = (tail.sum() / (n_obs * alpha * es_mag)) + 1.0
    return float(np.percentile(z2s, level * 100.0))


def backtest_es_coverage(
    returns_panel: pd.DataFrame,
    weights: Optional[dict] = None,
    *,
    confidence: float = 0.99,
    window: int = 60,
    nu: float = EWMA_T_DOF,
    min_oos: int = 25,
    n_sims: int = 2000,
    seed: int = 0,
) -> ESCoverageScorecard:
    """Acerbi-Szekely Test 2 ES coverage over an injected returns panel.

    ``returns_panel`` is a per-ticker daily-returns frame (e.g. from
    ``book_pnl.returns_panel``); ``weights`` defaults to equal-weight. Reports
    ``basis="insufficient"`` (no verdict) when the panel can't yield at least
    ``min_oos`` OOS days OR yields zero VaR breaches (the tail mean is untestable
    with no realized tail).
    """
    alpha = 1.0 - float(confidence)
    insufficient = ESCoverageScorecard(
        confidence=confidence, nu=nu, window=window, n_observations=0,
        n_breaches=0, z2=float("nan"), z2_critical=float("nan"), rejected=False,
        well_scaled=False, mean_tail_loss=0.0, mean_es_forecast=0.0,
        basis="insufficient",
        summary="No real return history available — ES coverage not evaluated.",
    )
    if returns_panel is None or returns_panel.empty:
        return insufficient

    cols = list(returns_panel.columns)
    if weights:
        cols = [c for c in cols if c in weights]
        w = np.array([float(weights[c]) for c in cols], dtype=float)
    else:
        w = np.full(len(cols), 1.0 / max(1, len(cols)))
    if not cols:
        return insufficient

    sub = returns_panel[cols].dropna(how="any")
    port = pd.Series(sub.to_numpy() @ w, index=sub.index)
    r = pd.to_numeric(port, errors="coerce").dropna().to_numpy()

    q, es_mult = _student_t_var_es_multipliers(alpha, nu)
    es_mag = -es_mult                                   # positive ES magnitude / sigma
    n_obs, n_breaches, z2, mean_tl, mean_es = _es_z2(
        r, window=window, q=q, es_mag=es_mag, alpha=alpha)

    if n_obs < min_oos or n_breaches == 0 or es_mag <= 0.0:
        why = ("zero VaR breaches — the tail mean is untestable"
               if n_obs >= min_oos else f"only {n_obs} OOS day(s) (need >= {min_oos})")
        return ESCoverageScorecard(
            confidence=confidence, nu=nu, window=window, n_observations=n_obs,
            n_breaches=n_breaches, z2=float("nan"), z2_critical=float("nan"),
            rejected=False, well_scaled=False, mean_tail_loss=mean_tl,
            mean_es_forecast=mean_es, basis="insufficient", tickers=cols,
            summary=f"ES coverage not evaluated ({why}).",
        )

    crit = _simulate_z2_critical(n_obs, alpha, nu, q, es_mag,
                                 n_sims=n_sims, seed=seed)
    rejected = bool(z2 < crit) if math.isfinite(crit) else False
    well = not rejected
    return ESCoverageScorecard(
        confidence=confidence, nu=nu, window=window, n_observations=n_obs,
        n_breaches=n_breaches, z2=z2, z2_critical=crit, rejected=rejected,
        well_scaled=well, mean_tail_loss=mean_tl, mean_es_forecast=mean_es,
        basis="real", tickers=cols,
        summary=(
            f"{confidence*100:.0f}% ES: realized tail loss {-mean_tl*100:.2f}% vs "
            f"forecast {mean_es*100:.2f}% over {n_breaches}/{n_obs} breaches; "
            f"Acerbi-Szekely Z2={z2:.3f} (crit {crit:.3f}) — "
            f"{'well-scaled' if well else 'ES UNDERESTIMATES the tail'}."
        ),
    )


def run_es_coverage_backtest(
    *, confidence: float = 0.99, window: int = 60, nu: float = EWMA_T_DOF,
    tickers: Optional[list] = None,
) -> ESCoverageScorecard:
    """Live entry: load REAL cached closes, build the panel, run Test 2. Honest
    ``basis="insufficient"`` when no usable cached history (or no tail) exists.
    """
    from processing.book_pnl import returns_panel
    stock_data = _load_cached_stock_data()
    panel = returns_panel(stock_data, tickers or _DEFAULT_TICKERS)
    return backtest_es_coverage(panel, confidence=confidence, window=window, nu=nu)
