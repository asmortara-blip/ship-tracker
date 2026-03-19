from __future__ import annotations

import os
from datetime import datetime, timedelta

import pandas as pd
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from data.cache_manager import CacheManager
from data.normalizer import normalize_macro_df

try:
    from fredapi import Fred
    _FREDAPI_AVAILABLE = True
except ImportError:
    _FREDAPI_AVAILABLE = False
    logger.warning("fredapi not installed; FRED data unavailable")


# Series to fetch and their human-readable names
FRED_SERIES: dict[str, str] = {
    "BDIY":             "Baltic Dry Index",
    "WPU101":           "PPI Freight Transport",
    "XTIMVA01USM667S":  "US Imports Value (Monthly)",
    "XTEXVA01USM667S":  "US Exports Value (Monthly)",
    "DCOILWTICO":       "WTI Crude Oil Spot",
    "CPIAUCSL":         "CPI All Urban Consumers",
    "ISRATIO":              "Total Business Inventories/Sales Ratio",
    "MRTSIR44X722USS":      "Retail Inventories/Sales Ratio",
    "MRTSSM44000USS":       "Advance Retail Sales: Retail Trade",
    "UMCSENT":              "U. of Michigan Consumer Sentiment",
    "AMTMNO":               "Manufacturers New Orders: All Manufacturing",
    "DGORDER":              "Manufacturers New Orders: Durable Goods",
}

# ISM PMI is available via FRED as a calculated/derived series
# Some FRED keys for PMI-related data:
PMI_SERIES: dict[str, str] = {
    "MANEMP":   "Manufacturing Employment (ISM proxy)",
    "IPMAN":    "Industrial Production Manufacturing",
}


def fetch_macro_series(
    lookback_days: int = 365,
    cache: CacheManager | None = None,
    ttl_hours: float = 24.0,
) -> dict[str, pd.DataFrame]:
    """Fetch all macro series from FRED.

    Returns:
        dict mapping series_id → normalized DataFrame with MACRO_COLS columns.
        Empty dict if FRED API key not set or fredapi not installed.
    """
    api_key = os.getenv("FRED_API_KEY", "")
    if not api_key:
        logger.warning("FRED_API_KEY not set — returning empty macro data")
        return {}

    if not _FREDAPI_AVAILABLE:
        return {}

    cache = cache or CacheManager()
    results: dict[str, pd.DataFrame] = {}
    all_series = {**FRED_SERIES, **PMI_SERIES}

    for series_id, series_name in all_series.items():
        key = f"{series_id}_{lookback_days}d"
        df = cache.get_or_fetch(
            key=key,
            fetch_fn=lambda sid=series_id, sname=series_name, lb=lookback_days: _fetch_series(
                sid, sname, lb, api_key
            ),
            ttl_hours=ttl_hours,
            source="fred",
        )
        if df is not None and not df.empty:
            results[series_id] = df

    logger.info(f"FRED data loaded: {list(results.keys())}")
    return results


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_series(series_id: str, series_name: str, lookback_days: int, api_key: str) -> pd.DataFrame:
    """Fetch a single FRED series and normalize it."""
    logger.debug(f"FRED fetch: {series_id}")
    fred = Fred(api_key=api_key)
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    try:
        series = fred.get_series(series_id, observation_start=start_date)
    except Exception as exc:
        logger.error(f"FRED series {series_id} failed: {exc}")
        return pd.DataFrame()

    if series is None or series.empty:
        return pd.DataFrame()

    df = series.reset_index()
    df.columns = ["date", "value"]
    df["series_id"] = series_id
    df["series_name"] = series_name
    df["source"] = "fred"
    df = df.dropna(subset=["value"])

    result = normalize_macro_df(df, series_id=series_id, series_name=series_name)
    logger.debug(f"  FRED {series_id}: {len(result)} observations")
    return result


def get_latest_value(series_id: str, macro_data: dict[str, pd.DataFrame]) -> float | None:
    """Return the most recent value for a FRED series."""
    df = macro_data.get(series_id)
    if df is None or df.empty:
        return None
    latest = df.dropna(subset=["value"])
    if latest.empty:
        return None
    return float(latest["value"].iloc[-1])


def get_bdi(macro_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Convenience: return the Baltic Dry Index series."""
    return macro_data.get("BDIY", pd.DataFrame())


def get_wti(macro_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Convenience: return WTI crude oil price series."""
    return macro_data.get("DCOILWTICO", pd.DataFrame())


def compute_bdi_score(macro_data: dict[str, pd.DataFrame], lookback_days: int = 90) -> float:
    """Score BDI relative to its rolling average. Returns [0, 1].

    Score > 0.5 means current BDI is above its recent average (bullish for shipping).
    """
    bdi_df = get_bdi(macro_data)
    if bdi_df.empty or len(bdi_df) < 10:
        return 0.5  # neutral if no data

    values = bdi_df["value"].dropna()
    current = values.iloc[-1]
    rolling_avg = values.tail(lookback_days).mean()

    if rolling_avg == 0:
        return 0.5

    ratio = current / rolling_avg  # 1.0 = at average, >1.0 = above average
    # Map ratio [0.5, 1.5] → [0, 1]
    score = (ratio - 0.5) / 1.0
    return max(0.0, min(1.0, score))
