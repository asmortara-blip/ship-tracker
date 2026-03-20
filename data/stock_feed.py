from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st
import yfinance as yf
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from data.cache_manager import CacheManager
from data.normalizer import normalize_stock_df


# FRED series IDs for Baltic Dry and freight indices
# (fetched here as numeric signals alongside stock data)
_DEFAULT_TICKERS = [
    "ZIM", "MATX", "SBLK", "DAC", "CMRE",  # shipping stocks
    "XRT", "XLI",                             # sector ETFs
]


@st.cache_data(ttl=3600)
def fetch_all_stocks(
    tickers: list[str] | None = None,
    lookback_days: int = 180,
    cache: CacheManager | None = None,
    ttl_hours: float = 1.0,
) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV data for all shipping stocks and ETFs.

    Returns:
        dict mapping ticker symbol → normalized DataFrame with STOCK_COLS columns.
        Missing/failed tickers are excluded from the dict (no None values).
    """
    tickers = tickers or _DEFAULT_TICKERS
    cache = cache or CacheManager()

    results: dict[str, pd.DataFrame] = {}
    for symbol in tickers:
        key = f"{symbol}_{lookback_days}d"
        df = cache.get_or_fetch(
            key=key,
            fetch_fn=lambda s=symbol, lb=lookback_days: _fetch_single(s, lb),
            ttl_hours=ttl_hours,
            source="stocks",
        )
        if df is not None and not df.empty:
            results[symbol] = df
        else:
            logger.warning(f"No data returned for {symbol}")

    logger.info(f"Stock data loaded: {list(results.keys())}")
    return results


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_single(symbol: str, lookback_days: int) -> pd.DataFrame:
    """Fetch a single ticker from yfinance and normalize."""
    logger.debug(f"yfinance fetch: {symbol} ({lookback_days}d)")
    try:
        ticker = yf.Ticker(symbol)
        raw = ticker.history(period=f"{lookback_days}d", interval="1d", auto_adjust=True)
    except Exception as e:
        logger.warning(f"yfinance request failed for {symbol}: {e}")
        # Fallback: return empty DataFrame so the caller skips this ticker
        return pd.DataFrame()

    if raw is None or raw.empty:
        logger.warning(f"yfinance returned empty for {symbol}")
        return pd.DataFrame()

    df = normalize_stock_df(raw, symbol=symbol)
    logger.debug(f"  {symbol}: {len(df)} rows, {df['date'].min()} → {df['date'].max()}")
    return df


def get_latest_price(symbol: str, stock_data: dict[str, pd.DataFrame]) -> float | None:
    """Return the most recent close price for a symbol."""
    df = stock_data.get(symbol)
    if df is None or df.empty:
        return None
    return float(df["close"].iloc[-1])


def get_pct_change(symbol: str, stock_data: dict[str, pd.DataFrame], days: int = 30) -> float | None:
    """Return percentage price change over the last N days."""
    df = stock_data.get(symbol)
    if df is None or len(df) < 2:
        return None
    recent = df.tail(days + 1)
    if len(recent) < 2:
        return None
    start = recent["close"].iloc[0]
    end = recent["close"].iloc[-1]
    if start == 0:
        return None
    return (end - start) / start
