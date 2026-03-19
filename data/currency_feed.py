"""currency_feed.py — Fetch FX/currency exchange rates relevant to shipping economics.

Pulls live and historical rates for key currency pairs via yfinance and caches
results using CacheManager. Falls back to hardcoded defaults on failure so the
rest of the pipeline always has usable data.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from loguru import logger

from data.cache_manager import CacheManager

try:
    import yfinance as yf
    _YFINANCE_AVAILABLE = True
except ImportError:
    _YFINANCE_AVAILABLE = False
    logger.warning("yfinance not installed; FX data will use hardcoded defaults")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KEY_CURRENCIES: dict[str, dict] = {
    "USD/CNY": {
        "name": "US Dollar / Chinese Yuan",
        "shipping_impact": "Trans-Pacific volume indicator",
    },
    "USD/EUR": {
        "name": "US Dollar / Euro",
        "shipping_impact": "Transatlantic trade competitiveness",
    },
    "USD/KRW": {
        "name": "US Dollar / Korean Won",
        "shipping_impact": "Korean shipbuilder cost proxy",
    },
    "USD/JPY": {
        "name": "US Dollar / Japanese Yen",
        "shipping_impact": "Japanese export competitiveness",
    },
    "USD/BRL": {
        "name": "US Dollar / Brazilian Real",
        "shipping_impact": "South America trade flows",
    },
    "USD/SGD": {
        "name": "US Dollar / Singapore Dollar",
        "shipping_impact": "SE Asia hub cost proxy",
    },
}

# yfinance ticker symbol → canonical pair name
_YF_TICKER_MAP: dict[str, str] = {
    "USDCNY=X": "USD/CNY",
    "EURUSD=X": "USD/EUR",   # note: EUR is quoted as EUR/USD, inverted below
    "USDKRW=X": "USD/KRW",
    "USDJPY=X": "USD/JPY",
    "USDBRL=X": "USD/BRL",
    "USDSGD=X": "USD/SGD",
}

_EURUSD_TICKER = "EURUSD=X"   # yfinance gives EUR/USD; we store as USD/EUR (inverted)

_DEFAULTS: dict[str, float] = {
    "USD/CNY": 7.24,
    "USD/EUR": 0.92,
    "USD/KRW": 1345.0,
    "USD/JPY": 149.0,
    "USD/BRL": 5.10,
    "USD/SGD": 1.35,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_fx_rates(
    cache: CacheManager | None = None,
    ttl_hours: float = 4.0,
) -> dict[str, float]:
    """Return the latest spot rate for each key currency pair.

    Rates are cached as a single-row Parquet file. Returns hardcoded defaults
    if yfinance is unavailable or all fetches fail.

    Returns
    -------
    dict  pair → float, e.g. {"USD/CNY": 7.24, "USD/EUR": 0.92, ...}
    """
    cache = cache or CacheManager()
    cache_key = "fx_spot_rates"

    cached_df = cache.get_or_fetch(
        key=cache_key,
        fetch_fn=_fetch_spot_rates_df,
        ttl_hours=ttl_hours,
        source="fx",
    )

    if cached_df is not None and not cached_df.empty:
        try:
            row = cached_df.iloc[0]
            return {col: float(row[col]) for col in row.index if col in KEY_CURRENCIES}
        except Exception as exc:
            logger.warning(f"Failed to parse cached FX rates: {exc}")

    logger.warning("FX rates unavailable — using hardcoded defaults")
    return dict(_DEFAULTS)


def fetch_fx_history(
    lookback_days: int = 90,
    cache: CacheManager | None = None,
) -> dict[str, pd.DataFrame]:
    """Return historical close prices for each key currency pair.

    Each DataFrame has columns: date, close, pair.
    Returns empty dict entries on failure (caller must handle missing history).

    Parameters
    ----------
    lookback_days:
        How many calendar days back to pull. Cached with a 4-hour TTL.
    cache:
        Optional shared CacheManager; a new one is created if not provided.

    Returns
    -------
    dict  pair → DataFrame(date, close, pair)
    """
    cache = cache or CacheManager()
    results: dict[str, pd.DataFrame] = {}

    tickers = list(_YF_TICKER_MAP.keys())
    cache_key = f"fx_history_{lookback_days}d"

    combined_df = cache.get_or_fetch(
        key=cache_key,
        fetch_fn=lambda: _fetch_history_df(tickers, lookback_days),
        ttl_hours=4.0,
        source="fx",
    )

    if combined_df is None or combined_df.empty:
        logger.warning("FX history unavailable — returning empty history")
        return {pair: pd.DataFrame() for pair in KEY_CURRENCIES}

    # Split combined frame back into per-pair DataFrames
    for pair in KEY_CURRENCIES:
        subset = combined_df[combined_df["pair"] == pair].copy()
        if not subset.empty:
            results[pair] = subset[["date", "close", "pair"]].reset_index(drop=True)
        else:
            results[pair] = pd.DataFrame()

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_spot_rates_df() -> pd.DataFrame:
    """Download current spot rates and return as a single-row DataFrame."""
    if not _YFINANCE_AVAILABLE:
        return _defaults_as_df()

    rates: dict[str, float] = {}
    tickers = list(_YF_TICKER_MAP.keys())

    try:
        raw = yf.download(
            tickers=tickers,
            period="5d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if raw.empty:
            logger.warning("yfinance returned empty data for FX spot rates")
            return _defaults_as_df()

        # yfinance returns MultiIndex columns (metric, ticker) when >1 ticker
        close = raw["Close"] if "Close" in raw.columns else raw.xs("Close", axis=1, level=0)

        for ticker, pair in _YF_TICKER_MAP.items():
            try:
                col = ticker if ticker in close.columns else None
                if col is None:
                    continue
                series = close[col].dropna()
                if series.empty:
                    continue
                value = float(series.iloc[-1])
                # EUR/USD is quoted inverted in yfinance — convert to USD/EUR
                if ticker == _EURUSD_TICKER:
                    value = round(1.0 / value, 6) if value != 0 else _DEFAULTS["USD/EUR"]
                rates[pair] = round(value, 6)
            except Exception as exc:
                logger.debug(f"Skipping {ticker} spot: {exc}")

    except Exception as exc:
        logger.error(f"yfinance FX spot fetch failed: {exc}")
        return _defaults_as_df()

    # Fill any missing pairs with defaults
    for pair, default in _DEFAULTS.items():
        if pair not in rates:
            logger.debug(f"Using default for {pair}")
            rates[pair] = default

    logger.info(f"FX spot rates fetched: {rates}")
    return pd.DataFrame([rates])


def _fetch_history_df(tickers: list[str], lookback_days: int) -> pd.DataFrame:
    """Download historical close prices and return a tidy long-format DataFrame."""
    if not _YFINANCE_AVAILABLE:
        return pd.DataFrame()

    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    frames: list[pd.DataFrame] = []

    try:
        raw = yf.download(
            tickers=tickers,
            start=start_date,
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if raw.empty:
            logger.warning("yfinance returned empty history for FX pairs")
            return pd.DataFrame()

        close = raw["Close"] if "Close" in raw.columns else raw.xs("Close", axis=1, level=0)

        for ticker, pair in _YF_TICKER_MAP.items():
            try:
                col = ticker if ticker in close.columns else None
                if col is None:
                    continue
                series = close[col].dropna()
                if series.empty:
                    continue
                df = series.reset_index()
                df.columns = ["date", "close"]
                df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
                # Invert EUR/USD → USD/EUR
                if ticker == _EURUSD_TICKER:
                    df["close"] = df["close"].apply(
                        lambda x: round(1.0 / x, 6) if x != 0 else _DEFAULTS["USD/EUR"]
                    )
                df["close"] = df["close"].round(6)
                df["pair"] = pair
                frames.append(df)
            except Exception as exc:
                logger.debug(f"Skipping {ticker} history: {exc}")

    except Exception as exc:
        logger.error(f"yfinance FX history fetch failed: {exc}")
        return pd.DataFrame()

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    logger.info(f"FX history fetched: {len(combined)} rows across {len(frames)} pairs")
    return combined


def _defaults_as_df() -> pd.DataFrame:
    """Return hardcoded defaults as a single-row DataFrame."""
    return pd.DataFrame([_DEFAULTS])
