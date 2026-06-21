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
    "BookFactorExposure",
    "fit_carrier_factors",
    "portfolio_factor_exposures",
    "factor_covariance",
    "factor_risk_decomposition",
    "FactorRiskDecomposition",
    "risk_attribution",
    "RiskAttribution",
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
    psr: float = 0.5  # probabilistic Sharpe: P(true Sharpe > 0), Bailey/Lopez de Prado
    net_sharpe: float = 0.0  # net of an ASSUMED per-trade turnover cost (R103)


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


# ── Factor attribution ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class FactorAttribution:
    """Decomposes a carrier's window return into factor contributions + alpha.

    The decomposition mirrors the regression's own structure:

        r_t  ≈  α  +  Σ_f  β_f · f_t  +  ε_t

    summed over the window. Each factor's *contribution* is ``β_f × Σ Δf``
    over the window; alpha's contribution is ``α × len(window)``; the residual
    is whatever isn't explained.

    "Why is ZIM up 8% this week?" → look at which factor contributions are
    biggest and what sign they carry. If most of the move is in ``residual``,
    the regression model doesn't explain it — alpha or a missing factor.
    """
    name: str
    window_size: int                          # number of periods aggregated
    observed_return: float                    # realized Σ r_t over the window
    explained_return: float                   # α·n + Σ β_f · Σ f_t
    residual_return: float                    # observed − explained
    alpha_contribution: float                 # α × window_size
    factor_contributions: dict[str, float]    # β_f × Σ f_t per factor
    r_squared_in_sample: float                # fit's r² (context)
    n_obs_in_sample: int                      # fit's n_obs (context)


def attribute_window_return(
    fit: CarrierFactorFit,
    returns_series: pd.Series,
    factors_df: pd.DataFrame,
    *,
    window: int = 4,
) -> FactorAttribution:
    """Decompose the most recent ``window`` periods of ``returns_series`` into
    α + per-factor contributions + residual, using ``fit``'s β vector.

    The decomposition is **exact** by construction: the contributions plus
    the residual sum to the observed window return up to floating-point
    roundoff. Callers can therefore display them as a stacked bar chart
    that totals the realized return.

    Notes
    -----
    - ``window`` is in periods of the input series, not days. If the series
      is weekly, ``window=4`` aggregates ~1 month.
    - The aligned overlap of ``returns_series`` and ``factors_df`` is what's
      used; if either truncates the other, the actual window may be smaller.
    - Returns a zero-attribution result if the window is empty / mis-shaped,
      so the UI path doesn't have to guard.
    """
    try:
        y, x = _align(returns_series, factors_df)
    except ValueError:
        # `_align` raises when there's no overlap (empty input, mismatched
        # indices, etc.). Treat as "nothing to attribute".
        return _empty_attribution(fit)
    if y.empty or x.empty:
        return _empty_attribution(fit)
    effective_window = min(int(window), len(y))
    if effective_window < 1:
        return _empty_attribution(fit)

    y_win = y.iloc[-effective_window:]
    x_win = x.iloc[-effective_window:]

    observed = float(y_win.sum())
    alpha_contribution = float(fit.alpha) * effective_window
    factor_contributions: dict[str, float] = {}
    explained = alpha_contribution
    for col in x_win.columns:
        beta = float(fit.betas.get(col, 0.0))
        contrib = beta * float(x_win[col].sum())
        factor_contributions[col] = round(contrib, 6)
        explained += contrib
    residual = observed - explained

    return FactorAttribution(
        name=fit.name,
        window_size=effective_window,
        observed_return=round(observed, 6),
        explained_return=round(explained, 6),
        residual_return=round(residual, 6),
        alpha_contribution=round(alpha_contribution, 6),
        factor_contributions=factor_contributions,
        r_squared_in_sample=round(float(fit.r_squared), 4),
        n_obs_in_sample=int(fit.n_obs),
    )


def _empty_attribution(fit: CarrierFactorFit) -> FactorAttribution:
    """Identity-shaped attribution used when alignment yields no usable data."""
    return FactorAttribution(
        name=fit.name,
        window_size=0,
        observed_return=0.0,
        explained_return=0.0,
        residual_return=0.0,
        alpha_contribution=0.0,
        factor_contributions={c: 0.0 for c in fit.betas},
        r_squared_in_sample=round(float(fit.r_squared), 4),
        n_obs_in_sample=int(fit.n_obs),
    )


def attribute_all_carriers(
    fits: dict[str, CarrierFactorFit],
    returns_df: pd.DataFrame,
    factors_df: pd.DataFrame,
    *,
    window: int = 4,
) -> list[FactorAttribution]:
    """Apply :func:`attribute_window_return` to every fitted carrier.

    Returns the attributions sorted by ``observed_return`` descending so the
    biggest movers float to the top — natural ordering for a "what moved?"
    table.
    """
    out: list[FactorAttribution] = []
    for ticker, fit in fits.items():
        if ticker not in returns_df.columns:
            continue
        out.append(
            attribute_window_return(
                fit, returns_df[ticker], factors_df, window=window,
            )
        )
    out.sort(key=lambda a: a.observed_return, reverse=True)
    return out


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

    # Net of an ASSUMED per-trade turnover cost (R103): each position change is a
    # trade of 1 unit of the carrier, charged at its per-side rate on the trade
    # step. Net Sharpe answers whether the residual edge survives friction.
    from processing.cost_model import per_side_cost_bps
    side_frac = per_side_cost_bps(name) / 1e4
    dpos = np.abs(np.diff(positions, prepend=0.0))   # |Δposition| per step
    net_pnl_series = pnl_series - pd.Series(dpos * side_frac, index=y.index)
    net_std = float(net_pnl_series.std(ddof=0))
    net_sharpe = 0.0 if net_std == 0 else float(
        net_pnl_series.mean() / net_std * math.sqrt(periods_per_year)
    )

    # Probabilistic Sharpe (honest haircut): P(true per-period Sharpe > 0)
    # given the sample length and higher moments. No trial-count assumption —
    # the selection-bias (deflated) haircut belongs at the suite level.
    from processing.stat_significance import probabilistic_sharpe_ratio
    per_period_sr = 0.0 if std == 0 else float(pnl_series.mean() / std)
    sk = float(pnl_series.skew())
    ku = float(pnl_series.kurt())
    if not (math.isfinite(sk) and math.isfinite(ku)):
        sk, ku = 0.0, 3.0
    else:
        ku = ku + 3.0  # pandas .kurt() is excess (Fisher); PSR wants full kurtosis
    psr = probabilistic_sharpe_ratio(per_period_sr, len(pnl_series), skew=sk, kurt=ku)

    return FactorBacktest(
        name=name,
        n_trades=trades,
        hit_rate=hit_rate,
        mean_return_bps=mean_bps,
        cumulative_return=cum,
        sharpe=sharpe,
        information_ratio=ir,
        equity_curve=equity,
        psr=psr,
        net_sharpe=float(round(net_sharpe, 4)),
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


# ── Book-level factor exposure (rec R106) ───────────────────────────────────


@dataclass(frozen=True)
class BookFactorExposure:
    """Portfolio-level factor exposure: the beta-weighted tilt per factor.

    ``exposures[f] = Σ_i w_i · β_{i,f}`` over the names in the book that have a
    factor fit. This is the Barra-grade aggregation Ship lacked — the portfolio
    risk view was a single market beta, not a named multi-factor vector.
    """
    exposures: dict[str, float]      # Σ w_i β_{i,f} per factor
    factors: tuple[str, ...]
    n_names: int                     # names with a fit that contributed
    coverage: float                  # fraction of |weight| covered by a fit

    def dollar_tilt(self, book_value: float) -> dict[str, float]:
        """Net dollar exposure per factor for a book worth ``book_value``."""
        return {f: e * float(book_value) for f, e in self.exposures.items()}


def portfolio_factor_exposures(
    weights: dict[str, float],
    fits: dict[str, object],
    *,
    factors: tuple[str, ...] = DEFAULT_FACTORS,
) -> BookFactorExposure:
    """Aggregate per-name factor betas into a book exposure vector.

    Parameters
    ----------
    weights : dict[ticker -> weight]
        Portfolio weights (need not sum to 1; signed for long/short).
    fits : dict[ticker -> CarrierFactorFit | dict[factor -> beta]]
        Per-name factor fits (``.betas``) or raw beta dicts. Names absent from
        ``fits`` are skipped and lower ``coverage`` — never silently treated as
        zero-beta exposure that the caller might mistake for "no risk".
    """
    exposures = {f: 0.0 for f in factors}
    total_w = 0.0
    covered_w = 0.0
    used = 0
    for ticker, w in (weights or {}).items():
        wf = abs(float(w))
        total_w += wf
        fit = fits.get(ticker) if fits else None
        if fit is None:
            continue
        betas = getattr(fit, "betas", None)
        if betas is None and isinstance(fit, dict):
            betas = fit
        if not betas:
            continue
        used += 1
        covered_w += wf
        for f in factors:
            exposures[f] += float(w) * float(betas.get(f, 0.0))
    coverage = (covered_w / total_w) if total_w > 0 else 0.0
    return BookFactorExposure(
        exposures=exposures,
        factors=tuple(factors),
        n_names=used,
        coverage=coverage,
    )


# ── Factor covariance + factor-vs-specific risk decomposition (rec R107) ─────


def factor_covariance(factors_df: pd.DataFrame, *, periods_per_year: int = 52) -> pd.DataFrame:
    """Annualized sample covariance of the factor regressors.

    Index/columns are the factor names. Empty DataFrame when there is nothing
    to estimate. ``periods_per_year`` annualizes (52 = weekly factors).
    """
    if factors_df is None or factors_df.empty or factors_df.shape[1] < 1:
        return pd.DataFrame()
    clean = factors_df.dropna()
    if len(clean) < 2:
        return pd.DataFrame()
    return clean.cov() * float(periods_per_year)


@dataclass(frozen=True)
class FactorRiskDecomposition:
    """Portfolio risk split into factor (systematic) vs specific (idiosyncratic).

    ``total_var = w'BΣ_fB'w  (factor)  +  Σ w_i² σ_i²  (specific)``. Vols are
    annualized. ``per_factor_var`` sums to the factor variance; it attributes
    the systematic variance back to each named factor.
    """
    total_vol: float
    factor_vol: float
    specific_vol: float
    pct_factor: float
    pct_specific: float
    per_factor_var: dict
    n_names: int


def factor_risk_decomposition(
    weights: dict[str, float],
    fits: dict[str, object],
    factor_cov: pd.DataFrame,
    *,
    periods_per_year: int = 52,
) -> FactorRiskDecomposition:
    """Decompose book variance into factor vs specific risk (Barra-style).

    ``factor_cov`` must already be annualized (see :func:`factor_covariance`);
    each fit's per-period ``residual_std`` is annualized here. Names absent from
    ``fits`` or factors absent from ``factor_cov`` contribute nothing.
    """
    empty = FactorRiskDecomposition(0.0, 0.0, 0.0, 0.0, 0.0, {}, 0)
    if factor_cov is None or factor_cov.empty:
        return empty
    factors = [f for f in factor_cov.columns]
    names = [n for n in (weights or {}) if n in (fits or {})]
    if not names or not factors:
        return empty

    w = np.array([float(weights[n]) for n in names])
    B = np.array([
        [float(getattr(fits[n], "betas", {}).get(f, 0.0)) for f in factors]
        for n in names
    ])
    sigma_f = factor_cov.loc[factors, factors].to_numpy(dtype=float)

    bw = B.T @ w                              # net factor exposure vector
    factor_var = float(bw @ sigma_f @ bw)

    spec_var = 0.0
    for i, n in enumerate(names):
        sig = float(getattr(fits[n], "residual_std", 0.0) or 0.0)
        spec_var += (w[i] ** 2) * (sig ** 2) * float(periods_per_year)

    factor_var = max(factor_var, 0.0)
    spec_var = max(spec_var, 0.0)
    total_var = factor_var + spec_var

    contrib = sigma_f @ bw
    per_factor_var = {f: float(bw[j] * contrib[j]) for j, f in enumerate(factors)}

    pct_factor = (factor_var / total_var) if total_var > 0 else 0.0
    return FactorRiskDecomposition(
        total_vol=math.sqrt(total_var),
        factor_vol=math.sqrt(factor_var),
        specific_vol=math.sqrt(spec_var),
        pct_factor=pct_factor,
        pct_specific=1.0 - pct_factor,
        per_factor_var=per_factor_var,
        n_names=len(names),
    )


# ── Ex-ante RISK attribution: WHICH factor + WHICH name owns the variance (R124) ─


@dataclass(frozen=True)
class RiskAttribution:
    """Ex-ante decomposition of forecast portfolio VARIANCE.

    The forward-looking RISK analog of :func:`attribute_window_return` (which
    decomposes a realized RETURN). Where return-attribution answers "why did the
    book move?", this answers "what will move it?" — which **factor** and which
    **name** own the forecast tracking error.

    Model
    -----
    With per-name factor loadings ``B`` (names × factors), an (annualized)
    factor covariance ``Ω`` (factors × factors), diagonal specific variances
    ``D`` (per name, ``D_i = σ_i²``), and signed weights ``w`` (per name):

        σ_p²  =  wᵀ (B Ω Bᵀ + D) w  =  (factor variance) + (specific variance)

    - Net factor exposure  ``x = Bᵀw``  (per factor).
    - Factor variance       ``xᵀ Ω x``.
    - Per-factor contribution ``xₖ · (Ω x)ₖ`` — these **sum exactly** to the
      factor variance (row split of the quadratic form). A factor's marginal
      contribution can be negative when its exposure hedges another (off-diagonal
      Ω), which is correct and intended.
    - Per-name specific contribution ``wᵢ² Dᵢ`` — these **sum exactly** to the
      specific variance and are always ≥ 0.

    Identities (exact to float roundoff)
    ------------------------------------
    - ``factor_variance + specific_variance == total_variance``
    - ``sum(per_factor.values) == factor_variance``
    - ``sum(per_name_specific.values) == specific_variance``

    Attributes
    ----------
    total_variance, total_vol
        ``σ_p²`` and ``σ_p`` (annualized if Ω/D are). ``total_vol`` is
        ``sqrt(max(total_variance, 0))`` — float-noise negatives clamp to 0.
    factor_variance, specific_variance
        The two top-level buckets; they sum to ``total_variance``.
    per_factor
        ``{factor: {"variance": v, "pct": v/total_variance}}`` — contribution to
        TOTAL variance. ``pct`` keys are 0.0 when total is 0.
    per_name_specific
        ``{name: {"variance": wᵢ²Dᵢ, "pct": .../total_variance}}`` — the
        idiosyncratic (name-specific) share each holding contributes.
    pct_factor, pct_specific
        Factor / specific share of total variance (sum to 1, or 0/0 when empty).
    n_names
        Names that carried a usable weight (had a fit OR a specific-only entry).
    n_names_missing_fit
        Names in ``weights`` with no fit in ``fits``. See the missing-fit note
        below — they contribute NOTHING (factor or specific) and lower coverage.
    """
    total_variance: float
    total_vol: float
    factor_variance: float
    specific_variance: float
    per_factor: dict          # {factor: {"variance": float, "pct": float}}
    per_name_specific: dict    # {name:   {"variance": float, "pct": float}}
    pct_factor: float
    pct_specific: float
    n_names: int
    n_names_missing_fit: int


def _empty_risk_attribution(n_missing: int = 0) -> RiskAttribution:
    """Zeroed attribution for empty / unusable input (no crash)."""
    return RiskAttribution(
        total_variance=0.0,
        total_vol=0.0,
        factor_variance=0.0,
        specific_variance=0.0,
        per_factor={},
        per_name_specific={},
        pct_factor=0.0,
        pct_specific=0.0,
        n_names=0,
        n_names_missing_fit=int(n_missing),
    )


def risk_attribution(
    weights: dict[str, float],
    fits: dict[str, object],
    factor_cov: "pd.DataFrame | None",
    *,
    periods_per_year: int = 52,
) -> RiskAttribution:
    """Decompose forecast portfolio VARIANCE into per-factor + per-name shares.

    Ex-ante risk analog of :func:`attribute_window_return`. Tells a desk WHICH
    factor and WHICH name drive forecast tracking error.

    Parameters
    ----------
    weights : dict[ticker -> weight]
        Signed portfolio weights (long/short; need not sum to 1).
    fits : dict[ticker -> CarrierFactorFit | obj with .betas / .residual_std]
        Per-name factor fits. ``.betas`` gives the factor loadings; the
        per-period ``residual_std`` is the specific (idiosyncratic) vol and is
        **annualized here** by ``periods_per_year`` to match ``factor_cov``.
    factor_cov : pd.DataFrame | None
        **Already-annualized** factor covariance Ω (see :func:`factor_covariance`),
        indexed/columned by factor name. When ``None``/empty, the factor bucket
        is zero and the result is specific-only (still a valid decomposition).
    periods_per_year : int
        Annualization for the per-period specific variance (52 = weekly).

    Missing-fit handling
    --------------------
    A name present in ``weights`` but absent from ``fits`` is **skipped
    entirely** — it contributes neither factor nor specific variance, and is
    counted in ``n_names_missing_fit``. We do NOT fabricate a specific variance
    for it (no fit ⇒ no honest σ_i), so the reported variance is a *lower bound*
    when coverage < 1. This mirrors ``portfolio_factor_exposures``'s coverage
    discipline: an uncovered name is never silently treated as zero-risk that a
    caller might mistake for "no exposure".

    Returns
    -------
    RiskAttribution
        Zeroed (no crash) on empty weights/fits. The three additive identities
        in :class:`RiskAttribution` hold to float roundoff.
    """
    weights = weights or {}
    fits = fits or {}

    # Partition requested names into covered (have a fit) vs missing.
    names = [n for n in weights if n in fits]
    n_missing = sum(1 for n in weights if n not in fits)
    if not names:
        return _empty_risk_attribution(n_missing)

    w = np.array([float(weights[n]) for n in names], dtype=float)

    # ── Factor bucket ────────────────────────────────────────────────────────
    factor_var = 0.0
    per_factor_var: dict[str, float] = {}
    if factor_cov is not None and not getattr(factor_cov, "empty", True):
        factors = [f for f in factor_cov.columns]
        if factors:
            B = np.array(
                [
                    [float(getattr(fits[n], "betas", {}).get(f, 0.0)) for f in factors]
                    for n in names
                ],
                dtype=float,
            )
            omega = factor_cov.loc[factors, factors].to_numpy(dtype=float)
            x = B.T @ w                       # net factor exposure (per factor)
            omega_x = omega @ x               # Ω x
            factor_var = float(x @ omega_x)   # xᵀ Ω x
            # Row split: xₖ·(Ωx)ₖ sums to xᵀΩx exactly.
            per_factor_var = {
                f: float(x[k] * omega_x[k]) for k, f in enumerate(factors)
            }

    # ── Specific bucket ──────────────────────────────────────────────────────
    per_name_var: dict[str, float] = {}
    spec_var = 0.0
    for i, n in enumerate(names):
        sig = float(getattr(fits[n], "residual_std", 0.0) or 0.0)
        v = (w[i] ** 2) * (sig ** 2) * float(periods_per_year)
        per_name_var[n] = v
        spec_var += v

    # ── Totals + clamps ──────────────────────────────────────────────────────
    # factor_var can be a tiny negative from float roundoff on a PSD Ω (or a
    # genuinely non-PSD Ω); the specific bucket is a sum of non-negatives. Clamp
    # the factor total so sqrt never sees a negative. Individual per-factor
    # contributions stay UNCLAMPED — a hedging factor's negative share is
    # meaningful — but if (and only if) the clamp actually moved factor_var off
    # the raw quadratic form, drop the per-factor split to {} so the documented
    # identity ``sum(per_factor) == factor_variance`` can't silently break. The
    # clamp fires only on (near-zero) noise or a non-PSD input, never on a
    # well-formed PSD Ω with real exposure, so this is a guard, not the path.
    raw_factor_var = factor_var
    factor_var = max(factor_var, 0.0)
    if factor_var != raw_factor_var:
        per_factor_var = {}
    spec_var = max(spec_var, 0.0)
    total_var = factor_var + spec_var

    def _pct(v: float) -> float:
        return (v / total_var) if total_var > 0 else 0.0

    per_factor = {
        f: {"variance": v, "pct": _pct(v)} for f, v in per_factor_var.items()
    }
    per_name_specific = {
        n: {"variance": v, "pct": _pct(v)} for n, v in per_name_var.items()
    }
    pct_factor = _pct(factor_var)

    return RiskAttribution(
        total_variance=total_var,
        total_vol=math.sqrt(max(total_var, 0.0)),
        factor_variance=factor_var,
        specific_variance=spec_var,
        per_factor=per_factor,
        per_name_specific=per_name_specific,
        pct_factor=pct_factor,
        pct_specific=(1.0 - pct_factor) if total_var > 0 else 0.0,
        n_names=len(names),
        n_names_missing_fit=int(n_missing),
    )
