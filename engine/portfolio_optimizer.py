"""engine/portfolio_optimizer.py — convex portfolio optimization for shipping equities.

Four optimization methods, one walk-forward backtest, all pure-function:

  * **max_sharpe**     — max Sharpe ratio subject to long-only + weight cap.
  * **min_variance**   — minimize w'Σw subject to long-only + weight cap.
  * **mean_variance**  — max μ'w − λ/2 · w'Σw (Markowitz quadratic utility).
  * **risk_parity**    — equal risk contribution: w_i × (Σw)_i constant across i.

Inputs
------
A ``returns_df`` indexed by date with one column per ticker. The optimizer
estimates ``μ`` (annualized mean) and ``Σ`` (annualized sample covariance)
from this panel by default; callers can override either explicitly.

Why scipy.optimize.minimize and not cvxpy
-----------------------------------------
SLSQP handles the linear-equality + linear-inequality + box-constraint mix
this problem needs (sum(w)=1, w ≥ 0, w ≤ cap) without a separate convex-
modeling dependency. cvxpy would be cleaner mathematically but adds a
heavy package + a transpilation step; scipy is already in requirements.

Principle-5 compliance
----------------------
``walk_forward_backtest`` rolls a window through the returns panel,
re-optimizes at each rebalance date using only data up to that point,
holds for ``rebal_freq`` days, and tracks the resulting equity curve.
Reports annualized return / vol / Sharpe / max drawdown — what you need
to compare against equal-weight or buy-and-hold benchmarks.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger
from scipy import optimize


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

TRADING_DAYS_PER_YEAR: int = 252
"""Used to annualize daily mean / std. Standard equity convention."""

VALID_METHODS: tuple[str, ...] = (
    "max_sharpe",
    "min_variance",
    "mean_variance",
    "risk_parity",
)


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OptimizedPortfolio:
    """A single-period optimization result."""
    weights: dict[str, float]              # ticker → weight (sums to 1.0)
    expected_return: float                 # annualized
    expected_vol: float                    # annualized
    sharpe: float                          # (expected_return − rf) / expected_vol
    method: str                            # one of VALID_METHODS
    risk_contributions: dict[str, float]   # ticker → fraction of portfolio variance


@dataclass(frozen=True)
class BacktestResult:
    """Walk-forward backtest summary."""
    n_rebalances: int
    annualized_return: float
    annualized_vol: float
    sharpe: float
    max_drawdown: float                    # negative number, e.g. −0.18 = −18%
    final_equity: float                    # ending value of $1 invested at t=0
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))


# ─────────────────────────────────────────────────────────────────────────────
# Statistic estimators
# ─────────────────────────────────────────────────────────────────────────────

def estimate_mu(returns_df: pd.DataFrame) -> pd.Series:
    """Annualized historical mean per asset."""
    return returns_df.mean() * TRADING_DAYS_PER_YEAR


def estimate_cov(returns_df: pd.DataFrame) -> pd.DataFrame:
    """Annualized sample covariance matrix."""
    return returns_df.cov() * TRADING_DAYS_PER_YEAR


# ─────────────────────────────────────────────────────────────────────────────
# Core helpers
# ─────────────────────────────────────────────────────────────────────────────

def _portfolio_stats(
    weights: np.ndarray,
    mu: np.ndarray,
    cov: np.ndarray,
) -> tuple[float, float]:
    """Return (annualized expected return, annualized vol) for `weights`."""
    exp_ret = float(weights @ mu)
    var = float(weights @ cov @ weights)
    vol = math.sqrt(max(var, 0.0))
    return exp_ret, vol


def _risk_contributions(weights: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Per-asset fraction-of-variance contribution.

    RC_i = w_i · (Σw)_i / (w'Σw). Returns identity-fractions (1/N) when the
    portfolio variance is degenerate.
    """
    marginal = cov @ weights
    portfolio_var = float(weights @ marginal)
    if portfolio_var <= 0.0 or not math.isfinite(portfolio_var):
        return np.full_like(weights, 1.0 / max(1, len(weights)))
    return (weights * marginal) / portfolio_var


# ─────────────────────────────────────────────────────────────────────────────
# Objectives + constraints
# ─────────────────────────────────────────────────────────────────────────────

def _build_bounds_and_constraints(
    n: int,
    long_only: bool,
    weight_cap: float,
) -> tuple[list[tuple[float, float]], list[dict]]:
    """Return (bounds, constraints) for scipy.optimize.minimize SLSQP.

    Common to every method: weights sum to 1.0 (linear-equality), each weight
    is in [0, weight_cap] for long-only or [-weight_cap, weight_cap] for
    long-short.
    """
    lo = 0.0 if long_only else -float(weight_cap)
    bounds = [(lo, float(weight_cap))] * n
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    return bounds, constraints


def _solve_max_sharpe(mu: np.ndarray, cov: np.ndarray, rf: float,
                     bounds, constraints) -> np.ndarray:
    """Maximize Sharpe ratio. Minimizes the negative Sharpe."""
    n = len(mu)

    def neg_sharpe(w: np.ndarray) -> float:
        ret, vol = _portfolio_stats(w, mu, cov)
        if vol < 1e-10:
            return 1e10
        return -(ret - rf) / vol

    x0 = np.full(n, 1.0 / n)
    res = optimize.minimize(
        neg_sharpe, x0, method="SLSQP",
        bounds=bounds, constraints=constraints,
        options={"maxiter": 200, "ftol": 1e-8},
    )
    return _safe_weights(res.x, n)


def _solve_min_variance(cov: np.ndarray, bounds, constraints) -> np.ndarray:
    """Minimize portfolio variance."""
    n = cov.shape[0]

    def variance(w: np.ndarray) -> float:
        return float(w @ cov @ w)

    x0 = np.full(n, 1.0 / n)
    res = optimize.minimize(
        variance, x0, method="SLSQP",
        bounds=bounds, constraints=constraints,
        options={"maxiter": 200, "ftol": 1e-10},
    )
    return _safe_weights(res.x, n)


def _solve_mean_variance(
    mu: np.ndarray, cov: np.ndarray, risk_aversion: float,
    bounds, constraints,
) -> np.ndarray:
    """Maximize Markowitz utility: μ'w − (λ/2) × w'Σw."""
    n = len(mu)

    def neg_utility(w: np.ndarray) -> float:
        ret = float(w @ mu)
        var = float(w @ cov @ w)
        return -(ret - 0.5 * risk_aversion * var)

    x0 = np.full(n, 1.0 / n)
    res = optimize.minimize(
        neg_utility, x0, method="SLSQP",
        bounds=bounds, constraints=constraints,
        options={"maxiter": 200, "ftol": 1e-8},
    )
    return _safe_weights(res.x, n)


def _solve_risk_parity(cov: np.ndarray, bounds, constraints) -> np.ndarray:
    """Equal-risk-contribution portfolio.

    Minimizes ``sum((RC_i − 1/N)²)`` where ``RC_i`` is asset i's fraction of
    portfolio variance. Convex on the interior; SLSQP converges reliably
    when seeded at equal weights.
    """
    n = cov.shape[0]
    target = 1.0 / n

    def err(w: np.ndarray) -> float:
        rc = _risk_contributions(w, cov)
        return float(np.sum((rc - target) ** 2))

    x0 = np.full(n, target)
    res = optimize.minimize(
        err, x0, method="SLSQP",
        bounds=bounds, constraints=constraints,
        options={"maxiter": 500, "ftol": 1e-10},
    )
    return _safe_weights(res.x, n)


def _safe_weights(raw: np.ndarray, n: int) -> np.ndarray:
    """Clip near-zero numerical noise and renormalize. Falls back to equal
    weights if the solver returned non-finite values."""
    if raw is None or not np.all(np.isfinite(raw)):
        return np.full(n, 1.0 / n)
    clipped = np.where(np.abs(raw) < 1e-8, 0.0, raw)
    total = float(np.sum(clipped))
    if abs(total) < 1e-10:
        return np.full(n, 1.0 / n)
    return clipped / total


# ─────────────────────────────────────────────────────────────────────────────
# Public optimization API
# ─────────────────────────────────────────────────────────────────────────────

def optimize_portfolio(
    returns_df: pd.DataFrame,
    method: str = "max_sharpe",
    *,
    mu: Optional[pd.Series] = None,
    cov: Optional[pd.DataFrame] = None,
    long_only: bool = True,
    weight_cap: float = 0.40,
    risk_aversion: float = 2.0,
    rf: float = 0.045,
) -> OptimizedPortfolio:
    """Solve the requested optimization on the supplied returns panel.

    Parameters
    ----------
    returns_df : DataFrame
        Date-indexed asset returns. Columns are tickers; values are
        period returns (daily by default).
    method : str
        One of ``VALID_METHODS``. Defaults to ``"max_sharpe"``.
    mu, cov : Optional
        Override the default historical estimates.
    long_only : bool
        If True, every weight is in ``[0, weight_cap]``. If False, in
        ``[-weight_cap, weight_cap]``.
    weight_cap : float
        Max absolute weight per asset.
    risk_aversion : float
        λ in the mean-variance utility. Ignored by other methods.
    rf : float
        Annualized risk-free rate, used for ``max_sharpe`` and the
        reported Sharpe of every method.

    Raises
    ------
    ValueError on empty input, unknown method, or fewer than 2 assets.
    """
    if method not in VALID_METHODS:
        raise ValueError(f"unknown method {method!r}; expected one of {VALID_METHODS}")
    if returns_df is None or returns_df.empty:
        raise ValueError("returns_df is empty")
    if len(returns_df.columns) < 2:
        raise ValueError("need at least 2 assets")
    if returns_df.shape[0] < 20:
        raise ValueError("need at least 20 observations to estimate covariance")

    tickers = list(returns_df.columns)
    n = len(tickers)

    mu_vec = (mu if mu is not None else estimate_mu(returns_df)).to_numpy()
    cov_mat = (cov if cov is not None else estimate_cov(returns_df)).to_numpy()

    bounds, constraints = _build_bounds_and_constraints(n, long_only, weight_cap)

    if method == "max_sharpe":
        w = _solve_max_sharpe(mu_vec, cov_mat, rf, bounds, constraints)
    elif method == "min_variance":
        w = _solve_min_variance(cov_mat, bounds, constraints)
    elif method == "mean_variance":
        w = _solve_mean_variance(mu_vec, cov_mat, risk_aversion, bounds, constraints)
    elif method == "risk_parity":
        w = _solve_risk_parity(cov_mat, bounds, constraints)
    else:
        # Defensive — already validated above.
        raise ValueError(f"unknown method {method!r}")

    ret, vol = _portfolio_stats(w, mu_vec, cov_mat)
    sharpe = (ret - rf) / vol if vol > 0 else 0.0
    rc = _risk_contributions(w, cov_mat)

    return OptimizedPortfolio(
        weights={t: float(round(w[i], 6)) for i, t in enumerate(tickers)},
        expected_return=float(round(ret, 6)),
        expected_vol=float(round(vol, 6)),
        sharpe=float(round(sharpe, 4)),
        method=method,
        risk_contributions={t: float(round(rc[i], 4)) for i, t in enumerate(tickers)},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward backtest (principle 5)
# ─────────────────────────────────────────────────────────────────────────────

def walk_forward_backtest(
    returns_df: pd.DataFrame,
    method: str = "max_sharpe",
    *,
    train_window: int = TRADING_DAYS_PER_YEAR,
    rebal_freq: int = 21,
    long_only: bool = True,
    weight_cap: float = 0.40,
    risk_aversion: float = 2.0,
    rf: float = 0.045,
) -> BacktestResult:
    """Walk-forward backtest with periodic rebalancing.

    Procedure
    ---------
    1. At time ``t`` (starting at ``train_window``):
         - Fit on returns_df.iloc[t-train_window : t]
         - Hold the resulting weights for ``rebal_freq`` days
         - Realize the actual returns over the holding period.
    2. Step ``t`` forward by ``rebal_freq`` and repeat.

    Each rebalance pays no transaction cost (intentional — keeps the
    benchmark interpretable; net-of-cost analysis lives downstream).
    """
    if returns_df is None or returns_df.empty:
        return _empty_backtest()
    if returns_df.shape[0] < train_window + rebal_freq:
        return _empty_backtest()

    n_rows = returns_df.shape[0]
    realized: list[float] = []
    dates: list = []
    n_rebalances = 0
    t = train_window

    while t + rebal_freq <= n_rows:
        train = returns_df.iloc[t - train_window : t]
        try:
            opt = optimize_portfolio(
                train, method,
                long_only=long_only, weight_cap=weight_cap,
                risk_aversion=risk_aversion, rf=rf,
            )
        except Exception as exc:
            logger.debug(f"portfolio_optimizer: skipped window at t={t}: {exc}")
            t += rebal_freq
            continue

        weights = np.array([opt.weights[c] for c in returns_df.columns])
        # Hold for rebal_freq days at fixed weights.
        hold = returns_df.iloc[t : t + rebal_freq].to_numpy()
        portfolio_returns = hold @ weights
        for d_idx in range(rebal_freq):
            realized.append(float(portfolio_returns[d_idx]))
            dates.append(returns_df.index[t + d_idx])
        n_rebalances += 1
        t += rebal_freq

    if not realized:
        return _empty_backtest()

    returns = pd.Series(realized, index=pd.Index(dates))
    equity = (1.0 + returns).cumprod()

    ann_ret = float(returns.mean() * TRADING_DAYS_PER_YEAR)
    ann_vol = float(returns.std(ddof=0) * math.sqrt(TRADING_DAYS_PER_YEAR))
    sharpe = (ann_ret - rf) / ann_vol if ann_vol > 0 else 0.0
    running_peak = equity.cummax()
    drawdown = (equity - running_peak) / running_peak
    max_dd = float(drawdown.min()) if not drawdown.empty else 0.0

    return BacktestResult(
        n_rebalances=n_rebalances,
        annualized_return=float(round(ann_ret, 6)),
        annualized_vol=float(round(ann_vol, 6)),
        sharpe=float(round(sharpe, 4)),
        max_drawdown=float(round(max_dd, 4)),
        final_equity=float(round(equity.iloc[-1], 6)),
        equity_curve=equity,
    )


def _empty_backtest() -> BacktestResult:
    return BacktestResult(
        n_rebalances=0,
        annualized_return=0.0,
        annualized_vol=0.0,
        sharpe=0.0,
        max_drawdown=0.0,
        final_equity=1.0,
        equity_curve=pd.Series(dtype=float),
    )
