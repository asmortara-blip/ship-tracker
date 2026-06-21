"""engine/cointegration.py

Cointegration analysis for shipping-index pairs and baskets.

This is the flagship Phase 2 quant artifact. It turns the collection of
Baltic / Freightos / container indices from "a bunch of lines that move
together" into testable hypotheses: is this pair cointegrated, what is the
error-correction speed, how fast does a shock mean-revert, and does a simple
spread-reversion strategy deliver IR > 0 out of sample?

Everything here is read-only on ``pd.DataFrame`` / ``pd.Series`` inputs. The
UI surface that consumes these results lives in ``tab_indices`` (Phase 2)
and later ``tab_convergence`` (Phase 4).

Public API
----------
- ``johansen_test(df, det_order=0, k_ar_diff=1)``  — multi-series rank test
- ``engle_granger(y, x)``                          — single-pair test + β̂
- ``fit_ecm(y, x)``                                — vector-error-correction
- ``half_life(spread)``                            — OU mean-reversion t½
- ``pair_report(y, x, *, min_obs=120)``            — everything for one pair
- ``walk_forward_backtest(y, x, ...)``             — out-of-sample IR/Sharpe

All functions return typed dataclasses; no Streamlit/UI imports leak in.
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

try:
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools.tools import add_constant
    from statsmodels.tsa.stattools import coint
    from statsmodels.tsa.vector_ar.vecm import coint_johansen
    _STATSMODELS_OK = True
except ImportError:  # pragma: no cover - statsmodels is a hard dep in CI
    _STATSMODELS_OK = False


__all__ = [
    "JohansenResult",
    "EngleGrangerResult",
    "ECMResult",
    "PairReport",
    "BacktestResult",
    "johansen_test",
    "engle_granger",
    "fit_ecm",
    "half_life",
    "pair_report",
    "walk_forward_backtest",
]


# ── Results ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class JohansenResult:
    """Johansen cointegration-rank test summary.

    ``trace_stat[i]`` is the trace statistic against the null of at most
    ``i`` cointegrating relations; ``trace_crit_95[i]`` is the 95% critical
    value for the same null. ``rank`` is the estimated number of
    cointegrating relations at 95%.
    """
    series: tuple[str, ...]
    trace_stat: np.ndarray
    trace_crit_95: np.ndarray
    eig_stat: np.ndarray
    eig_crit_95: np.ndarray
    rank: int
    eigenvalues: np.ndarray
    eigenvectors: np.ndarray  # columns are cointegrating vectors

    @property
    def is_cointegrated(self) -> bool:
        return self.rank >= 1


@dataclass(frozen=True)
class EngleGrangerResult:
    """Engle-Granger two-step result for the single-pair case."""
    y_name: str
    x_name: str
    beta: float           # hedge ratio from OLS y = α + β·x + ε
    alpha: float
    coint_tstat: float    # ADF t-stat on the OLS residual
    coint_pvalue: float
    is_cointegrated: bool  # p < 0.05


@dataclass(frozen=True)
class ECMResult:
    """Error-correction model on the cointegrated pair.

    The ECM regresses Δy_t on ε_{t-1} (lagged spread) and Δx_t:
        Δy_t = c + λ·ε_{t-1} + γ·Δx_t + u_t
    ``lambda_y`` should be < 0 and significant for pull-back toward
    equilibrium.
    """
    lambda_y: float           # speed of adjustment (should be negative)
    lambda_y_tstat: float
    gamma: float              # contemporaneous beta on Δx
    half_life_days: float     # -ln(2)/ln(1+λ)  when λ ∈ (-1, 0)


@dataclass(frozen=True)
class PairReport:
    """Combined pair-level output used by tab_indices / tab_convergence."""
    y: str
    x: str
    n_obs: int
    engle_granger: EngleGrangerResult
    ecm: ECMResult
    spread: pd.Series = field(repr=False)
    spread_zscore: float


@dataclass(frozen=True)
class BacktestResult:
    """Walk-forward backtest of a simple spread mean-reversion rule."""
    pair: tuple[str, str]
    n_trades: int
    hit_rate: float
    mean_return_bps: float
    cumulative_return: float
    sharpe: float
    information_ratio: float  # sharpe vs. buy-and-hold y
    equity_curve: pd.Series = field(repr=False)
    net_sharpe: float = 0.0   # net of an ASSUMED per-trade turnover cost (R103)


# ── Guards ──────────────────────────────────────────────────────────────────


def _require_statsmodels() -> None:
    if not _STATSMODELS_OK:
        raise RuntimeError(
            "engine.cointegration requires statsmodels. Install with "
            "`pip install statsmodels`."
        )


def _clean_pair(y: pd.Series, x: pd.Series) -> tuple[pd.Series, pd.Series]:
    df = pd.concat([y, x], axis=1, join="inner").dropna()
    if df.shape[1] != 2:
        raise ValueError("_clean_pair expects two 1-D series")
    df.columns = ["y", "x"]
    return df["y"], df["x"]


# ── Johansen ────────────────────────────────────────────────────────────────


def johansen_test(
    df: pd.DataFrame,
    *,
    det_order: int = 0,
    k_ar_diff: int = 1,
) -> JohansenResult:
    """Johansen trace test on the columns of ``df``.

    Parameters
    ----------
    df : pd.DataFrame
        One level-series per column (e.g. log-prices). Must have ≥ 20 rows.
    det_order : int
        -1 for no deterministic term, 0 for constant, 1 for constant+trend.
    k_ar_diff : int
        Number of lagged differences in the VECM. 1 is the standard default.

    Returns
    -------
    JohansenResult
        Trace/eigenvalue stats, 95% critical values, and the estimated
        cointegrating rank.
    """
    _require_statsmodels()
    clean = df.dropna()
    if clean.shape[0] < 20:
        raise ValueError(f"johansen_test needs ≥ 20 obs, got {clean.shape[0]}")
    if clean.shape[1] < 2:
        raise ValueError("johansen_test needs ≥ 2 series")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = coint_johansen(clean.values, det_order, k_ar_diff)

    trace = np.asarray(res.lr1)
    trace_crit = np.asarray(res.cvt)[:, 1]  # column 1 = 95%
    eig = np.asarray(res.lr2)
    eig_crit = np.asarray(res.cvm)[:, 1]

    rank = 0
    for i, (stat, crit) in enumerate(zip(trace, trace_crit)):
        if stat > crit:
            rank = i + 1
        else:
            break

    return JohansenResult(
        series=tuple(clean.columns),
        trace_stat=trace,
        trace_crit_95=trace_crit,
        eig_stat=eig,
        eig_crit_95=eig_crit,
        rank=rank,
        eigenvalues=np.asarray(res.eig),
        eigenvectors=np.asarray(res.evec),
    )


# ── Engle-Granger + ECM ─────────────────────────────────────────────────────


def engle_granger(y: pd.Series, x: pd.Series) -> EngleGrangerResult:
    """Engle-Granger two-step cointegration test.

    Runs OLS ``y = α + β·x + ε`` then ADF on ε. ``coint`` from statsmodels
    returns the correct Phillips-Ouliaris adjusted p-value.
    """
    _require_statsmodels()
    y, x = _clean_pair(y, x)
    X = add_constant(x.values)
    ols = OLS(y.values, X).fit()
    alpha, beta = float(ols.params[0]), float(ols.params[1])

    tstat, pvalue, _ = coint(y.values, x.values, trend="c", autolag="AIC")
    return EngleGrangerResult(
        y_name=str(y.name or "y"),
        x_name=str(x.name or "x"),
        beta=beta,
        alpha=alpha,
        coint_tstat=float(tstat),
        coint_pvalue=float(pvalue),
        is_cointegrated=bool(pvalue < 0.05),
    )


def fit_ecm(y: pd.Series, x: pd.Series) -> ECMResult:
    """Fit the pair's error-correction model and return adjustment speed."""
    _require_statsmodels()
    y, x = _clean_pair(y, x)
    eg = engle_granger(y, x)

    eps = y - (eg.alpha + eg.beta * x)
    dy = y.diff()
    dx = x.diff()
    lag_eps = eps.shift(1)
    frame = pd.concat([dy, lag_eps, dx], axis=1).dropna()
    frame.columns = ["dy", "lag_eps", "dx"]
    X = add_constant(frame[["lag_eps", "dx"]].values)
    ols = OLS(frame["dy"].values, X).fit()
    lam = float(ols.params[1])
    lam_t = float(ols.tvalues[1])
    gamma = float(ols.params[2])

    hl = half_life_from_lambda(lam)
    return ECMResult(
        lambda_y=lam,
        lambda_y_tstat=lam_t,
        gamma=gamma,
        half_life_days=hl,
    )


def half_life_from_lambda(lam: float) -> float:
    """Half-life implied by ECM adjustment coefficient λ.

    For Δy = λ·ε_{t-1} + …, the spread decays at rate 1+λ per step, so the
    half-life is ``-ln(2) / ln(1 + λ)``. λ outside (-1, 0) → non-reverting.
    """
    if lam >= 0 or lam <= -1:
        return math.inf
    return -math.log(2.0) / math.log(1.0 + lam)


def half_life(spread: pd.Series) -> float:
    """OU half-life estimated from the spread itself (AR(1) on the level).

    Fit ε_t = φ·ε_{t-1} + u_t. Half-life = -ln(2)/ln(φ) if 0 < φ < 1.
    Returns +inf when non-stationary.
    """
    _require_statsmodels()
    s = spread.dropna()
    if len(s) < 20:
        return math.inf
    lag = s.shift(1).dropna()
    y = s.loc[lag.index]
    denom = float(np.dot(lag, lag))
    if denom == 0:
        return math.inf
    phi = float(np.dot(lag, y) / denom)
    # NaN comparisons are False — guard explicitly so math.log(NaN) never runs.
    if np.isnan(phi) or phi <= 0 or phi >= 1:
        return math.inf
    return -math.log(2.0) / math.log(phi)


# ── Pair report ─────────────────────────────────────────────────────────────


def pair_report(
    y: pd.Series,
    x: pd.Series,
    *,
    min_obs: int = 120,
) -> PairReport:
    """Run EG + ECM + z-score in one shot for a single pair."""
    y, x = _clean_pair(y, x)
    if len(y) < min_obs:
        raise ValueError(f"pair_report needs ≥ {min_obs} obs, got {len(y)}")
    eg = engle_granger(y, x)
    ecm = fit_ecm(y, x)
    spread = y - (eg.alpha + eg.beta * x)
    mu, sd = float(spread.mean()), float(spread.std(ddof=0))
    z = 0.0 if sd == 0 else (float(spread.iloc[-1]) - mu) / sd
    return PairReport(
        y=eg.y_name,
        x=eg.x_name,
        n_obs=len(y),
        engle_granger=eg,
        ecm=ecm,
        spread=spread,
        spread_zscore=z,
    )


# ── Walk-forward backtest ───────────────────────────────────────────────────


def walk_forward_backtest(
    y: pd.Series,
    x: pd.Series,
    *,
    lookback: int = 120,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    stop_z: float = 4.0,
    hold_max: int | None = None,
) -> BacktestResult:
    """Out-of-sample mean-reversion backtest of y - β·x.

    At each step ``t``, refit β on the trailing ``lookback`` observations,
    compute the current spread z-score, and apply the trade rule:
      - enter short spread when z > entry_z, long when z < -entry_z
      - exit when |z| < exit_z (take profit), |z| > stop_z (stop), or the
        position has been held for more than ``hold_max`` steps
      - position size = 1 unit of y, -β units of x (dollar-neutral)

    Returns PnL in log-return space. Sharpe is annualized at √252 assuming
    daily data; if the input is sparser the number is still comparable
    across pairs.
    """
    y, x = _clean_pair(y, x)
    n = len(y)
    if n < lookback + 20:
        raise ValueError(
            f"walk_forward_backtest needs ≥ {lookback + 20} obs, got {n}"
        )
    hold_max = hold_max or (lookback * 2)

    from processing.cost_model import per_side_cost_bps
    side_y = per_side_cost_bps(str(y.name or "")) / 1e4
    side_x = per_side_cost_bps(str(x.name or "")) / 1e4

    pnl = np.zeros(n)
    cost = np.zeros(n)   # assumed turnover cost charged at each entry/exit (R103)
    pos = 0     # -1, 0, +1 on the spread
    beta = 1.0
    alpha = 0.0
    mu, sd = 0.0, 0.0
    held = 0

    for t in range(lookback, n):
        window_y = y.iloc[t - lookback:t]
        window_x = x.iloc[t - lookback:t]
        # Refit β on the trailing window (no look-ahead)
        cx = np.vstack([np.ones(lookback), window_x.values]).T
        try:
            sol, *_ = np.linalg.lstsq(cx, window_y.values, rcond=None)
            alpha, beta = float(sol[0]), float(sol[1])
        except np.linalg.LinAlgError:
            continue
        window_spread = window_y - (alpha + beta * window_x)
        mu = float(window_spread.mean())
        sd = float(window_spread.std(ddof=0))
        if sd == 0 or not np.isfinite(sd):
            continue
        cur_spread = float(y.iloc[t] - (alpha + beta * x.iloc[t]))
        z = (cur_spread - mu) / sd

        if pos == 0:
            if z > entry_z:
                pos, held = -1, 0
                # Entry trades both legs: 1 unit of y + |β| units of x.
                cost[t] += side_y + abs(beta) * side_x
            elif z < -entry_z:
                pos, held = 1, 0
                cost[t] += side_y + abs(beta) * side_x
        else:
            held += 1
            # realize one-period PnL from yesterday→today
            dy = float(np.log(y.iloc[t] / y.iloc[t - 1]))
            dx = float(np.log(x.iloc[t] / x.iloc[t - 1]))
            pnl[t] = pos * (dy - beta * dx)
            if abs(z) < exit_z or abs(z) > stop_z or held >= hold_max:
                pos, held = 0, 0
                # Exit unwinds both legs.
                cost[t] += side_y + abs(beta) * side_x

    ret = pd.Series(pnl, index=y.index)
    equity = ret.cumsum()
    trades = int((ret != 0).sum())
    wins = int(((ret > 0)).sum())
    hit_rate = float(wins / trades) if trades else 0.0
    mean_bps = float(ret.mean() * 1e4)
    cum = float(equity.iloc[-1])
    std = float(ret.std(ddof=0))
    sharpe = 0.0 if std == 0 else float(ret.mean() / std * math.sqrt(252))

    # Net of the assumed per-trade turnover cost (R103): each entry/exit trades
    # both legs (1 unit y + |β| units x) at their per-side rate.
    net_ret = pd.Series(pnl - cost, index=y.index)
    net_std = float(net_ret.std(ddof=0))
    net_sharpe = 0.0 if net_std == 0 else float(
        net_ret.mean() / net_std * math.sqrt(252))

    bh_ret = np.log(y / y.shift(1)).iloc[lookback:].dropna()
    bh_sharpe = 0.0
    bh_std = float(bh_ret.std(ddof=0))
    if bh_std:
        bh_sharpe = float(bh_ret.mean() / bh_std * math.sqrt(252))
    ir = sharpe - bh_sharpe

    return BacktestResult(
        pair=(str(y.name or "y"), str(x.name or "x")),
        n_trades=trades,
        hit_rate=hit_rate,
        mean_return_bps=mean_bps,
        cumulative_return=cum,
        sharpe=sharpe,
        information_ratio=ir,
        equity_curve=equity,
        net_sharpe=float(round(net_sharpe, 4)),
    )


# ── Convenience: batch run over a list of pairs ────────────────────────────


def batch_pair_reports(
    df: pd.DataFrame,
    pairs: Iterable[Sequence[str]],
    *,
    min_obs: int = 120,
) -> list[PairReport]:
    """Run ``pair_report`` for every (col_y, col_x) in ``pairs``.

    Silently skips pairs with insufficient data or singular regressions —
    callers should check ``len(result)`` against the pairs they passed in.
    """
    out: list[PairReport] = []
    for a, b in pairs:
        if a not in df.columns or b not in df.columns:
            continue
        try:
            out.append(pair_report(df[a], df[b], min_obs=min_obs))
        except (ValueError, np.linalg.LinAlgError):
            continue
    return out
