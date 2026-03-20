"""
Performance Attribution Engine for Shipping Stocks

Decomposes each shipping stock's total return into:
  - Freight Rate Beta contribution
  - BDI (Baltic Dry Index) macro contribution
  - Market / sector contribution (XLI ETF proxy)
  - Idiosyncratic / alpha return (unexplained residual)

All regression is done with pure numpy (numpy.linalg.lstsq) — no sklearn.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger


# ── Constants ─────────────────────────────────────────────────────────────────

SHIPPING_TICKERS: List[str] = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]

# Minimum observations required to run regression
_MIN_OBS = 20


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class PerformanceAttribution:
    ticker: str
    period_days: int
    total_return_pct: float          # Total % return over the period
    freight_beta_contribution: float  # % return from freight rate moves
    macro_contribution: float         # % return from macro factors (BDI, PMI)
    idiosyncratic_return: float       # Unexplained (alpha) return
    sector_contribution: float        # Shipping ETF beta contribution (XLI)
    r_squared: float                  # Fraction of variance explained by factors
    information_ratio: float          # idiosyncratic_return / tracking_error
    tracking_error: float             # Annualised std-dev of residuals (%)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _pct_returns(series: pd.Series) -> pd.Series:
    """Compute simple daily % returns, drop NaN."""
    return series.pct_change().dropna()


def _align_series(*series: pd.Series) -> pd.DataFrame:
    """Inner-join multiple return series on their index, drop NaN rows."""
    df = pd.concat(list(series), axis=1)
    df = df.dropna()
    return df


def _extract_return_series(
    df: Optional[pd.DataFrame],
    value_col: str = "close",
    date_col: str = "date",
) -> Optional[pd.Series]:
    """Pull a named value column from a stock/freight DataFrame and return daily % returns."""
    if df is None or df.empty:
        return None
    if value_col not in df.columns:
        return None
    work = df.copy()
    if date_col in work.columns:
        work = work.sort_values(date_col).set_index(date_col)
    returns = _pct_returns(work[value_col])
    return returns


def _extract_macro_return(
    macro_data: Dict[str, pd.DataFrame],
    key: str,
    date_col: str = "date",
    value_col: str = "value",
) -> Optional[pd.Series]:
    """Extract a daily % return series from a macro DataFrame keyed in macro_data."""
    df = macro_data.get(key)
    if df is None or df.empty or value_col not in df.columns:
        return None
    work = df.copy()
    if date_col in work.columns:
        work = work.sort_values(date_col).set_index(date_col)
    return _pct_returns(work[value_col])


def _ols_lstsq(
    y: np.ndarray, X_raw: np.ndarray
) -> tuple[np.ndarray, float, np.ndarray]:
    """
    Run OLS via numpy.linalg.lstsq.

    Parameters
    ----------
    y      : (n,) dependent variable
    X_raw  : (n, k) raw factor matrix (intercept NOT included — added internally)

    Returns
    -------
    coeffs    : (k+1,) array — [alpha, beta_1, ..., beta_k]
    r_squared : float [0, 1]
    residuals : (n,) array
    """
    n = len(y)
    # prepend intercept column
    X = np.column_stack([np.ones(n), X_raw])

    coeffs, _, _, _ = np.linalg.lstsq(X, y, rcond=None)

    y_hat = X @ coeffs
    residuals = y - y_hat
    ss_res = float(np.dot(residuals, residuals))
    ss_tot = float(np.dot(y - y.mean(), y - y.mean()))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 0.0

    return coeffs, max(0.0, min(1.0, r_squared)), residuals


def _annualised_vol(daily_returns: np.ndarray) -> float:
    """Convert daily return std-dev to annualised % vol."""
    return float(np.std(daily_returns, ddof=1)) * np.sqrt(252) * 100.0


# ── Core attribution function ─────────────────────────────────────────────────

def AttributePerformance(
    ticker: str,
    stock_data: Dict[str, pd.DataFrame],
    freight_data: Dict[str, pd.DataFrame],
    macro_data: Dict[str, pd.DataFrame],
    period_days: int = 90,
) -> Optional[PerformanceAttribution]:
    """
    Attribute a stock's return over *period_days* to four factor groups.

    Regression model (all in daily returns):
        stock_return = alpha
                     + beta_freight * freight_return
                     + beta_bdi     * bdi_return
                     + beta_market  * xli_return
                     + epsilon

    Factor contributions are computed as beta * mean(factor_return) * period_days.

    Returns None if there is insufficient data to run the regression.
    """
    stock_df = stock_data.get(ticker)
    if stock_df is None or stock_df.empty:
        logger.warning("AttributePerformance: no stock data for " + ticker)
        return None

    # ── 1. Stock returns ───────────────────────────────────────────────────
    stock_ret = _extract_return_series(stock_df, value_col="close")
    if stock_ret is None or len(stock_ret) < _MIN_OBS:
        logger.warning("AttributePerformance: insufficient stock returns for " + ticker)
        return None

    # Trim to requested period
    if len(stock_ret) > period_days:
        stock_ret = stock_ret.iloc[-period_days:]

    # ── 2. Total return over the period ───────────────────────────────────
    # Use compounded daily returns
    total_return_pct = float((1.0 + stock_ret).prod() - 1.0) * 100.0

    # ── 3. Freight factor: use first available freight route ───────────────
    freight_ret: Optional[pd.Series] = None
    for route_id, fdf in freight_data.items():
        if fdf is None or fdf.empty:
            continue
        rate_col = "rate_usd_per_feu" if "rate_usd_per_feu" in fdf.columns else None
        if rate_col is None:
            # try generic value column
            if "value" in fdf.columns:
                rate_col = "value"
            elif "close" in fdf.columns:
                rate_col = "close"
        if rate_col is None:
            continue
        candidate = _extract_return_series(fdf, value_col=rate_col)
        if candidate is not None and len(candidate) >= _MIN_OBS:
            freight_ret = candidate
            break

    # ── 4. BDI factor ─────────────────────────────────────────────────────
    # Try several common keys
    bdi_ret: Optional[pd.Series] = None
    for bdi_key in ("BSXRLM", "BDI", "bdi"):
        bdi_ret = _extract_macro_return(macro_data, bdi_key)
        if bdi_ret is not None and len(bdi_ret) >= _MIN_OBS:
            break

    # ── 5. Market / sector factor (XLI ETF) ───────────────────────────────
    xli_ret: Optional[pd.Series] = None
    xli_df = stock_data.get("XLI")
    if xli_df is not None and not xli_df.empty:
        xli_ret = _extract_return_series(xli_df, value_col="close")

    # ── 6. Align all available factors ────────────────────────────────────
    # Build factor dict — use zero-series placeholder for missing factors
    # so we always have a well-defined regression
    series_dict: Dict[str, pd.Series] = {"stock": stock_ret}

    if freight_ret is not None:
        series_dict["freight"] = freight_ret
    if bdi_ret is not None:
        series_dict["bdi"] = bdi_ret
    if xli_ret is not None:
        series_dict["xli"] = xli_ret

    aligned = _align_series(*[series_dict[k] for k in series_dict])
    aligned.columns = list(series_dict.keys())

    if len(aligned) < _MIN_OBS:
        logger.warning(
            "AttributePerformance: after alignment only "
            + str(len(aligned))
            + " obs for "
            + ticker
        )
        return None

    y = aligned["stock"].values
    factor_cols = [c for c in aligned.columns if c != "stock"]

    if not factor_cols:
        logger.warning("AttributePerformance: no factors available for " + ticker)
        return None

    X_raw = aligned[factor_cols].values

    # ── 7. OLS regression ─────────────────────────────────────────────────
    coeffs, r_squared, residuals = _ols_lstsq(y, X_raw)
    alpha = coeffs[0]
    betas = coeffs[1:]  # one per factor_col

    n_obs = len(y)
    factor_mean = X_raw.mean(axis=0)  # mean daily return per factor

    # Contribution = beta_i * mean_factor_return * n_obs  (simple approximation)
    # This gives the cumulative % contribution in the same units as total_return_pct
    beta_dict: Dict[str, float] = {}
    for i, col in enumerate(factor_cols):
        contribution = float(betas[i]) * float(factor_mean[i]) * n_obs * 100.0
        beta_dict[col] = contribution

    freight_beta_contribution = beta_dict.get("freight", 0.0)
    bdi_contribution = beta_dict.get("bdi", 0.0)
    xli_contribution = beta_dict.get("xli", 0.0)

    # macro_contribution combines BDI + any other macro factor contributions
    macro_contribution = bdi_contribution

    # idiosyncratic = alpha * n_obs (intercept expressed in % over period)
    idiosyncratic_return = float(alpha) * n_obs * 100.0

    # ── 8. Tracking error & information ratio ─────────────────────────────
    tracking_error = _annualised_vol(residuals)
    annualised_idio = idiosyncratic_return / (period_days / 252.0) if period_days > 0 else 0.0
    information_ratio = annualised_idio / tracking_error if tracking_error > 1e-8 else 0.0

    result = PerformanceAttribution(
        ticker=ticker,
        period_days=period_days,
        total_return_pct=total_return_pct,
        freight_beta_contribution=freight_beta_contribution,
        macro_contribution=macro_contribution,
        idiosyncratic_return=idiosyncratic_return,
        sector_contribution=xli_contribution,
        r_squared=r_squared,
        information_ratio=information_ratio,
        tracking_error=tracking_error,
    )
    logger.info(
        "Attribution complete: "
        + ticker
        + " | R2="
        + str(round(r_squared, 3))
        + " | IR="
        + str(round(information_ratio, 2))
    )
    return result


# ── Batch attribution ─────────────────────────────────────────────────────────

def attribute_all_stocks(
    stock_data: Dict[str, pd.DataFrame],
    freight_data: Dict[str, pd.DataFrame],
    macro_data: Dict[str, pd.DataFrame],
    period_days: int = 90,
) -> List[PerformanceAttribution]:
    """
    Run AttributePerformance for every shipping ticker that has data.

    Returns a list sorted by total_return_pct descending.
    """
    results: List[PerformanceAttribution] = []
    tickers = [t for t in SHIPPING_TICKERS if t in stock_data]
    if not tickers:
        # Fall back to whatever tickers are in stock_data
        tickers = list(stock_data.keys())

    for ticker in tickers:
        attr = AttributePerformance(
            ticker=ticker,
            stock_data=stock_data,
            freight_data=freight_data,
            macro_data=macro_data,
            period_days=period_days,
        )
        if attr is not None:
            results.append(attr)

    results.sort(key=lambda a: a.total_return_pct, reverse=True)
    logger.info("attribute_all_stocks: " + str(len(results)) + " attributions computed")
    return results


# ── Factor return series ──────────────────────────────────────────────────────

def compute_factor_returns(
    stock_data: Dict[str, pd.DataFrame],
    freight_data: Dict[str, pd.DataFrame],
    macro_data: Dict[str, pd.DataFrame],
) -> Dict[str, pd.Series]:
    """
    Compute daily factor return series for the four main factor groups:

    freight_momentum  : 5-day rolling mean of freight rate returns
    bdi_trend         : 20-day rolling mean of BDI returns (smoothed trend)
    macro_composite   : equal-weight average of BDI + freight factor returns
    sector_beta       : XLI ETF daily returns

    Returns a dict of factor_name -> pd.Series (daily, aligned to trading days
    where all factors have data).
    """
    factor_series: Dict[str, pd.Series] = {}

    # ── freight_momentum ──────────────────────────────────────────────────
    freight_ret: Optional[pd.Series] = None
    for route_id, fdf in freight_data.items():
        if fdf is None or fdf.empty:
            continue
        rate_col = None
        for col in ("rate_usd_per_feu", "value", "close"):
            if col in fdf.columns:
                rate_col = col
                break
        if rate_col is None:
            continue
        cand = _extract_return_series(fdf, value_col=rate_col)
        if cand is not None and len(cand) >= _MIN_OBS:
            freight_ret = cand
            break

    if freight_ret is not None:
        factor_series["freight_momentum"] = freight_ret.rolling(5).mean().dropna()
    else:
        logger.debug("compute_factor_returns: no freight data available")

    # ── bdi_trend ─────────────────────────────────────────────────────────
    bdi_ret: Optional[pd.Series] = None
    for bdi_key in ("BSXRLM", "BDI", "bdi"):
        bdi_ret = _extract_macro_return(macro_data, bdi_key)
        if bdi_ret is not None and len(bdi_ret) >= _MIN_OBS:
            break

    if bdi_ret is not None:
        factor_series["bdi_trend"] = bdi_ret.rolling(20).mean().dropna()
    else:
        logger.debug("compute_factor_returns: no BDI data available")

    # ── macro_composite ───────────────────────────────────────────────────
    components = []
    if freight_ret is not None:
        components.append(freight_ret)
    if bdi_ret is not None:
        components.append(bdi_ret)

    if components:
        macro_df = _align_series(*components)
        macro_composite = macro_df.mean(axis=1)
        factor_series["macro_composite"] = macro_composite
    else:
        logger.debug("compute_factor_returns: no macro composite components")

    # ── sector_beta ───────────────────────────────────────────────────────
    xli_df = stock_data.get("XLI")
    if xli_df is not None and not xli_df.empty:
        xli_ret = _extract_return_series(xli_df, value_col="close")
        if xli_ret is not None:
            factor_series["sector_beta"] = xli_ret
    else:
        logger.debug("compute_factor_returns: no XLI data available")

    logger.info(
        "compute_factor_returns: factors available: "
        + str(list(factor_series.keys()))
    )
    return factor_series
