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


@st.cache_data(ttl=3600, hash_funcs={CacheManager: lambda _: None})
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
        # R127: auto_adjust=False keeps ``Close`` as the RAW as-observed price
        # (display = real share price; no look-ahead from back-restating history
        # on future splits/dividends). yfinance includes the Dividends + Stock
        # Splits action columns by default, which normalize_stock_df folds into
        # the forward look-ahead-free adj_factor for total-return backtests.
        raw = ticker.history(period=f"{lookback_days}d", interval="1d",
                             auto_adjust=False)
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


# ── DataSeries variant (Phase 1 rollout) ─────────────────────────────────────

def fetch_all_stocks_wrapped(
    tickers: list[str] | None = None,
    lookback_days: int = 180,
    cache: CacheManager | None = None,
    ttl_hours: float = 1.0,
):
    """Wrapped-variant of ``fetch_all_stocks`` returning a ``DataSeries``.

    Payload is the legacy ``dict[ticker → DataFrame]``. ``DataSource`` reflects
    Yahoo Finance's public API — unofficial, so quality is ``unofficial`` when
    data arrives, ``demo`` when it doesn't.
    """
    from data.quality import DataSeries, DataSource, DataKind, DataQuality

    data = fetch_all_stocks(
        tickers=tickers, lookback_days=lookback_days,
        cache=cache, ttl_hours=ttl_hours,
    )
    if data:
        source = DataSource(
            name="Yahoo Finance",
            kind=DataKind.SCRAPED,
            url="https://finance.yahoo.com",
            quality=DataQuality.UNOFFICIAL,
            sla_hours=ttl_hours,
            notes=f"{len(data)} tickers, {lookback_days}d lookback",
        )
    else:
        source = DataSource.demo("Yahoo Finance unavailable")
    return DataSeries(data=data, source=source,
                      meta={"lookback_days": lookback_days,
                            "tickers": tickers or _DEFAULT_TICKERS})


def get_pct_change(symbol: str, stock_data: dict[str, pd.DataFrame], days: int = 30) -> float | None:
    """Return percentage price change over the last N days."""
    df = stock_data.get(symbol)
    if df is None or len(df) < 2:
        return None
    # R127: use the look-ahead-free total-return path so a split in the window
    # doesn't show a spurious ~-50% "change"; get_latest_price stays raw (the
    # real share price).
    from data.normalizer import adjusted_close
    recent = adjusted_close(df).tail(days + 1)
    if len(recent) < 2:
        return None
    start = recent.iloc[0]
    end = recent.iloc[-1]
    if start == 0:
        return None
    return (end - start) / start


def deepen_stock_cache(
    tickers: list[str],
    *,
    lookback_days: int = 1825,
    cache_dir: str = "cache",
    inter_request_sleep: float = 2.0,
    fetch=None,
) -> dict[str, str]:
    """Persist DEEP (multi-year) price history for ``tickers`` so the headless
    VaR-coverage backtest + book risk run on deep REAL data (the EWMA VaR was
    validated on this depth). Writes ``cache/stocks/{sym}_{lookback}d.parquet``
    in the canonical shape; ``var_coverage_backtest._load_cached_stock_data``
    prefers the longest history per symbol, so the deep file wins.

    Gentle + offline-safe: a courtesy sleep between fetches; a throttled / empty
    fetch for a ticker is SKIPPED (the existing cache stands — silence never
    overwrites real data), and NOTHING raises. ``fetch`` is injectable
    (``(symbol, lookback_days) -> DataFrame``) for offline tests.

    Returns ``{ticker: "written" | "skipped"}``.
    """
    import time

    getter = fetch or _fetch_single
    stocks_dir = Path(cache_dir) / "stocks"
    result: dict[str, str] = {}
    try:
        stocks_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return {t: "skipped" for t in (tickers or [])}

    for i, sym in enumerate(tickers or []):
        try:
            if i > 0 and inter_request_sleep:
                time.sleep(inter_request_sleep)
            df = getter(sym, lookback_days)
            if df is None or getattr(df, "empty", True):
                result[sym] = "skipped"
                continue
            df.to_parquet(stocks_dir / f"{sym.lower()}_{lookback_days}d.parquet")
            result[sym] = "written"
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"deepen_stock_cache: {sym} skipped: {exc}")
            result[sym] = "skipped"
    return result
