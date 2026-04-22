"""engine/carrier_factor_model.py

Linear factor model for carrier equities.

This is the second Phase 2 quant artifact (after ``engine.cointegration``). It
regresses weekly log-returns of each carrier {ZIM, MATX, SBLK, DAC, CMRE,
CMB.BR, ...} onto a set of macro/freight factors and publishes:

- per-name β vector with HAC-robust (Newey-West) t-stats,
- R² and residual standard deviation,
- rolling residual z-score — the "unexplained" component of the carrier
  return — which is the signal we backtest,
- a simple walk-forward mean-reversion backtest of that residual signal.

Everything here is a pure function of pandas inputs. No Streamlit, no live
feeds. The UI surface that consumes the results lives in ``tab_portfolio``
(Phase 2) and, later, ``tab_idea_engine`` (Phase 4).

Public API
----------
- ``fit_carrier_factors(returns_df, factors_df, *, hac_lags=4)`` — one OLS
  per column of ``returns_df``; returns ``dict[name, CarrierFactorFit]``.
- ``residual_zscore(fit, returns_series, factors_df, *, window=52)`` —
  rolling z-score of the residual.
- ``residual_signal_backtest(fit, returns_series, factors_df, *, ...)`` —
  walk-forward IR/Sharpe/hit-rate of a mean-reversion rule.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

try:
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools.tools import add_constant
    _STATSMODELS_OK = True
except ImportError:  # pragma: no cover
    _STATSMODELS_OK = False


__all__ = [
    "CarrierFactorFit",
    "FactorBacktest",
    "fit_carrier_factors",
    "residual_zscore",
    "residual_signal_backtest",
    "DEFAULT_CARRIERS",
    "DEFAULT_FACTORS",
]


DEFAULT_CARRIERS: tuple[str, ...] = (
    "ZIM", "MATX", "SBLK", "DAC", "CMRE", "CMB.BR",
)

DEFAULT_FACTORS: tuple[str, ...] = (
    "dBDI", "dSCFI", "dBrent", "WTI_Brent_spread", "dDXY", "VIX",
)


# ── Results ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CarrierFactorFit:
    """OLS fit of one carrier's returns on the factor set.

    Attributes
    ----------
    name : str
        Ticker (e.g. "ZIM").
    alpha : float
        Regression intercept.
    alpha_tstat : float
        HAC-robust t-stat for the intercept.
    betas : dict[str, float]
        Factor loadings, keyed by factor column name.
    tvalues : dict[str, float]
        HAC-robust t-stats for each factor.
    r_squared : float
        Ordinary R² (not adjusted).
    r_squared_adj : float
    n_obs : int
    residuals : pd.Series
        In-sample residual series (index aligned with input returns).
    residual_std : float
        ``np.std(residuals, ddof=0)``.
    """
    name: str
    alpha: float
    alpha_tstat: float
    betas: dict[str, float]
    tvalues: dict[str, float]
    r_squared: float
    r_squared_adj: float
    n_obs: int
    residuals: pd.Series = field(repr=False)
    residual_std: float


@dataclass(frozen=True)
class FactorBacktest:
    """Walk-forward backtest of the residual mean-reversion rule.

    The rule at each step ``t``:
      - refit the factor model on the trailing ``lookback`` observations,
      - compute residual z-score using the training-window mean/std,
      - if z > ``entry_z`` → short the carrier one step (size −1),
        if z < −``entry_z`` → long (size +1), else flat.

    We realize next-period PnL as ``position * r_{t+1}``. The IR is the
    Sharpe of the strategy minus the Sharpe of equal-weight buy-and-hold of
    the same carrier. Both are annualized at √52 for weekly data.
    """
    name: str
    n_trades: int
    hit_rate: float
    mean_return_bps: float
    cumulative_return: float
    sharpe: float
    information_ratio: float
    equity_curve: pd.Series = field(repr=False)


# ── Guards + cleaning ───────────────────────────────────────────────────────


def _require_statsmodels() -> None:
    if not _STATSMODELS_OK:
        raise RuntimeError(
            "engine.carrier_factor_model requires statsmodels. "
            "Install with `pip install statsmodels`."
        )


def _align(y: pd.Series, factors: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """Inner-join ``y`` against ``factors`` and drop rows with any NaN."""
    f = factors.copy()
    f["__y__"] = y
    f = f.dropna()
    if f.empty:
        raise ValueError("No overlapping observations between returns and factors")
    y_out = f["__y__"].astype(float)
    x_out = f.drop(columns="__y__").astype(float)
    return y_out, x_out


# ── Core fit ────────────────────────────────────────────────────────────────


def _fit_one(
    name: str,
    y: pd.Series,
    factors: pd.DataFrame,
    *,
    hac_lags: int,
) -> CarrierFactorFit:
    y, x = _align(y, factors)
    if len(y) < x.shape[1] + 5:
        raise ValueError(
            f"{name}: need ≥ {x.shape[1] + 5} obs, got {len(y)}"
        )
    X = add_constant(x.values)
    ols = OLS(y.values, X).fit(cov_type="HAC", cov_kwds={"maxlags": hac_lags})

    params = ols.params
    tvals = ols.tvalues
    cols = list(x.columns)

    alpha = float(params[0])
    alpha_t = float(tvals[0])
    betas = {c: float(params[i + 1]) for i, c in enumerate(cols)}
    tvalues = {c: float(tvals[i + 1]) for i, c in enumerate(cols)}

    fitted = pd.Series(ols.fittedvalues, index=y.index)
    resid = y - fitted
    return CarrierFactorFit(
        name=name,
        alpha=alpha,
        alpha_tstat=alpha_t,
        betas=betas,
        tvalues=tvalues,
        r_squared=float(ols.rsquared),
        r_squared_adj=float(ols.rsquared_adj),
        n_obs=int(ols.nobs),
        residuals=resid,
        residual_std=float(resid.std(ddof=0)),
    )


def fit_carrier_factors(
    returns_df: pd.DataFrame,
    factors_df: pd.DataFrame,
    *,
    hac_lags: int = 4,
) -> dict[str, CarrierFactorFit]:
    """Fit one factor model per carrier column in ``returns_df``.

    Parameters
    ----------
    returns_df : pd.DataFrame
        Columns are carrier tickers, rows are periodic log-returns
        (weekly recommended; works for any fixed frequency).
    factors_df : pd.DataFrame
        Columns are factor names. Must be pre-differenced / normalized as
        appropriate — this function does not transform factors.
    hac_lags : int
        Number of Newey-West lags for the heteroskedasticity-and-
        autocorrelation-consistent covariance. Default 4 is standard for
        weekly data.

    Returns
    -------
    dict[str, CarrierFactorFit]
        One fit per column of ``returns_df``. Carriers with insufficient
        overlap (< k+5 obs) are silently skipped — caller should diff
        requested vs. returned keys.
    """
    _require_statsmodels()
    if returns_df.empty:
        return {}
    out: dict[str, CarrierFactorFit] = {}
    for name in returns_df.columns:
        try:
            out[str(name)] = _fit_one(
                str(name), returns_df[name], factors_df, hac_lags=hac_lags,
            )
        except (ValueError, np.linalg.LinAlgError):
            continue
    return out


# ── Residual z-score ────────────────────────────────────────────────────────


def residual_zscore(
    fit: CarrierFactorFit,
    returns_series: pd.Series,
    factors_df: pd.DataFrame,
    *,
    window: int = 52,
) -> pd.Series:
    """Rolling z-score of the residual using the fitted β vector.

    Uses the *in-sample* β but computes the residual on the full series and
    standardizes against a rolling window. This is what the UI plots as the
    "residual trajectory".
    """
    y, x = _align(returns_series, factors_df)
    beta_vec = np.array([fit.betas[c] for c in x.columns])
    fitted = fit.alpha + x.values @ beta_vec
    resid = pd.Series(y.values - fitted, index=y.index, name=f"{fit.name}_resid")

    roll = resid.rolling(window=window, min_periods=max(5, window // 4))
    mu = roll.mean()
    sd = roll.std(ddof=0)
    z = (resid - mu) / sd.replace(0.0, np.nan)
    return z.rename(f"{fit.name}_z")


# ── Walk-forward backtest ───────────────────────────────────────────────────


def residual_signal_backtest(
    returns_series: pd.Series,
    factors_df: pd.DataFrame,
    *,
    name: str | None = None,
    lookback: int = 52,
    entry_z: float = 1.0,
    exit_z: float = 0.25,
    periods_per_year: int = 52,
) -> FactorBacktest:
    """Walk-forward backtest of residual mean-reversion on one carrier.

    At each step ``t`` (starting at ``lookback``):
      1. Refit OLS β on the trailing ``lookback`` obs (no HAC — speed).
      2. Compute residual z using the training-window μ, σ.
      3. Open long if z < -entry_z, short if z > +entry_z; close when
         |z| < exit_z (or flip on opposite entry).
      4. Realize PnL = position * return_{t+1} (one-period-ahead).

    Returns
    -------
    FactorBacktest
        Annualized Sharpe, IR vs. equal-weight buy-and-hold of the same
        carrier, hit rate, cumulative log return, equity curve.
    """
    y, x = _align(returns_series, factors_df)
    n = len(y)
    name = str(name or returns_series.name or "series")
    if n < lookback + 10:
        raise ValueError(
            f"{name}: need ≥ {lookback + 10} obs for backtest, got {n}"
        )

    y_arr = y.values
    X_arr = np.hstack([np.ones((n, 1)), x.values])
    positions = np.zeros(n)
    pos = 0
    last_z = 0.0

    for t in range(lookback, n - 1):
        Xw = X_arr[t - lookback:t]
        yw = y_arr[t - lookback:t]
        try:
            sol, *_ = np.linalg.lstsq(Xw, yw, rcond=None)
        except np.linalg.LinAlgError:
            continue
        resid_w = yw - Xw @ sol
        mu = float(resid_w.mean())
        sd = float(resid_w.std(ddof=0))
        if sd == 0 or not np.isfinite(sd):
            continue
        cur_resid = float(y_arr[t] - X_arr[t] @ sol)
        z = (cur_resid - mu) / sd
        last_z = z

        if pos == 0:
            if z > entry_z:
                pos = -1
            elif z < -entry_z:
                pos = 1
        else:
            if abs(z) < exit_z:
                pos = 0
            elif pos == -1 and z < -entry_z:
                pos = 1
            elif pos == 1 and z > entry_z:
                pos = -1
        positions[t] = pos

    # Realize next-period PnL
    pnl = np.zeros(n)
    for t in range(lookback, n - 1):
        pnl[t + 1] = positions[t] * y_arr[t + 1]
    pnl_series = pd.Series(pnl, index=y.index)
    equity = pnl_series.cumsum()

    active = pnl_series[pnl_series != 0]
    trades = int(len(active))
    wins = int((active > 0).sum())
    hit_rate = float(wins / trades) if trades else 0.0
    mean_bps = float(pnl_series.mean() * 1e4)
    cum = float(equity.iloc[-1])
    std = float(pnl_series.std(ddof=0))
    sharpe = 0.0 if std == 0 else float(
        pnl_series.mean() / std * math.sqrt(periods_per_year)
    )

    bh = y.iloc[lookback:]
    bh_std = float(bh.std(ddof=0))
    bh_sharpe = 0.0 if bh_std == 0 else float(
        bh.mean() / bh_std * math.sqrt(periods_per_year)
    )
    ir = sharpe - bh_sharpe
    _ = last_z  # kept for parity with the cointegration backtest

    return FactorBacktest(
        name=name,
        n_trades=trades,
        hit_rate=hit_rate,
        mean_return_bps=mean_bps,
        cumulative_return=cum,
        sharpe=sharpe,
        information_ratio=ir,
        equity_curve=equity,
    )


# ── Convenience: factor builder from raw level series ───────────────────────


def build_factor_frame(
    levels: dict[str, pd.Series],
    *,
    log_diff: tuple[str, ...] = ("BDI", "SCFI", "Brent", "DXY"),
    level: tuple[str, ...] = ("VIX",),
    spread: tuple[tuple[str, str], ...] = (("WTI", "Brent"),),
    resample: str | None = "W-FRI",
) -> pd.DataFrame:
    """Build a factor DataFrame from raw level series.

    Default transforms:
      - log-diff for most macro factors (BDI, SCFI, Brent, DXY),
      - level for volatility (VIX) — it's already stationary-ish,
      - differenced pair-spread for (WTI, Brent).

    If ``resample`` is provided, each level series is resampled to that
    frequency (last obs) before transformation. This is how we go from
    mixed-frequency raw feeds to a clean weekly panel.
    """
    if not levels:
        return pd.DataFrame()

    def _maybe_resample(s: pd.Series) -> pd.Series:
        if resample is None or not isinstance(s.index, pd.DatetimeIndex):
            return s.dropna()
        return s.resample(resample).last().dropna()

    cols: dict[str, pd.Series] = {}
    for key in log_diff:
        if key in levels:
            s = _maybe_resample(levels[key]).astype(float)
            s = np.log(s.where(s > 0)).diff().dropna()
            cols[f"d{key}"] = s
    for key in level:
        if key in levels:
            cols[key] = _maybe_resample(levels[key]).astype(float)
    for a, b in spread:
        if a in levels and b in levels:
            sa = _maybe_resample(levels[a]).astype(float)
            sb = _maybe_resample(levels[b]).astype(float)
            cols[f"{a}_{b}_spread"] = (sa - sb).diff().dropna()

    if not cols:
        return pd.DataFrame()
    df = pd.concat(cols, axis=1).dropna(how="all")
    return df.dropna()
