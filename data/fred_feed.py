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
    # ── Core shipping / freight ──────────────────────────────────────────────
    "BDIY":             "Baltic Dry Index",
    "WPU101":           "PPI Crude Petroleum",
    "XTIMVA01USM667S":  "US Imports Value (Monthly)",
    "XTEXVA01USM667S":  "US Exports Value (Monthly)",
    "CPIAUCSL":         "CPI All Urban Consumers",
    "ISRATIO":          "Total Business Inventories/Sales Ratio",
    "MRTSIR44X722USS":  "Retail Inventories/Sales Ratio",
    "DGORDER":          "Manufacturers New Orders: Durable Goods",
    "IPMAN":            "Industrial Production: Manufacturing",
    "PPIACO":           "PPI All Commodities",
    "PCU4841484148":    "PPI Deep Sea Freight",

    # ── Supply chain indicators ──────────────────────────────────────────────
    "AMTMNO":           "Manufacturing New Orders",
    "AMDMNO":           "Durable Goods New Orders",
    "AMDMUS":           "Durable Goods Unfilled Orders",
    "AMTMTI":           "Manufacturing Inventories",

    # ── Labor and employment ─────────────────────────────────────────────────
    "MANEMP":           "Manufacturing Employment",
    "USPHCI":           "Philly Fed Manufacturing Index",
    "CFNAI":            "Chicago Fed National Activity Index",

    # ── Consumer and retail ──────────────────────────────────────────────────
    "MRTSSM44000USS":   "Retail Sales Total",
    "MRTSSM448USS":     "Sporting Goods Retail",
    "PCE":              "Personal Consumption Expenditure",
    "UMCSENT":          "Consumer Sentiment (UMich)",

    # ── Housing (leads container imports) ────────────────────────────────────
    "HOUST":            "Housing Starts",
    "PERMIT":           "Building Permits",
    "HSN1F":            "New Home Sales",

    # ── Trade specific ───────────────────────────────────────────────────────
    "BOPGSTB":          "Trade Balance (Goods)",
    "IMPGS":            "Imports of Goods and Services",
    "EXPGS":            "Exports of Goods and Services",
    "DNBGDQ027SAAR":    "Trade in Goods Deficit",

    # ── Energy / Fuel (shipping cost proxy) ─────────────────────────────────
    "DCOILWTICO":       "WTI Crude Oil Price",
    "DCOILBRENTEU":     "Brent Crude Oil Price",
    "GASDESW":          "US Diesel Fuel Price (Retail)",

    # ── Monetary / Financial ─────────────────────────────────────────────────
    "DGS1M":            "1-Month Treasury Yield",
    "DGS3M":            "3-Month Treasury Yield",
    "DGS6M":            "6-Month Treasury Yield",
    "DGS1":             "1-Year Treasury Yield",
    "DGS2":             "2-Year Treasury Yield",
    "DGS5":             "5-Year Treasury Yield",
    "DGS10":            "10-Year Treasury Yield",
    "DGS30":            "30-Year Treasury Yield",
    "T10Y2Y":           "10Y-2Y Yield Curve Spread",
    "DEXCHUS":          "USD/CNY Exchange Rate",
    "DEXUSEU":          "USD/EUR Exchange Rate",
    "DEXJPUS":          "JPY/USD Exchange Rate",
    "VIXCLS":           "VIX Volatility Index",

    # ── Additional shipping-specific PPI ─────────────────────────────────────
    "WPU0561":          "PPI: Diesel Fuel",

    # ── ISM PMI (fetched opportunistically; may not be on FRED) ──────────────
    "NAPMPI":           "ISM Manufacturing PMI",
}

# ISM PMI is available via FRED as a calculated/derived series
# Some FRED keys for PMI-related data:
PMI_SERIES: dict[str, str] = {}  # merged into FRED_SERIES above


class _FredSeriesNotFound(Exception):
    """Raised when FRED returns 400/404 for a series — skip without retry."""


def _is_not_found_error(exc: Exception) -> bool:
    """Return True if the exception looks like a 400 or 404 from FRED."""
    msg = str(exc).lower()
    return any(code in msg for code in ("400", "404", "not found", "bad request"))


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
        try:
            df = cache.get_or_fetch(
                key=key,
                fetch_fn=lambda sid=series_id, sname=series_name, lb=lookback_days: _fetch_series(
                    sid, sname, lb, api_key
                ),
                ttl_hours=ttl_hours,
                source="fred",
            )
        except _FredSeriesNotFound:
            # Already logged inside _fetch_series — nothing more to do.
            continue
        except Exception as exc:
            logger.warning(f"FRED series {series_id} skipped (unavailable): {exc}")
            continue
        if df is not None and not df.empty:
            results[series_id] = df

    logger.info(f"FRED data loaded: {list(results.keys())}")
    return results


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _fetch_series(series_id: str, series_name: str, lookback_days: int, api_key: str) -> pd.DataFrame:
    """Fetch a single FRED series and normalize it.

    Returns an empty DataFrame when the series exists but has no data.
    Raises _FredSeriesNotFound (not retried upstream) for 400/404 responses.
    """
    logger.debug(f"FRED fetch: {series_id}")
    fred = Fred(api_key=api_key)
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    try:
        series = fred.get_series(series_id, observation_start=start_date)
    except Exception as exc:
        if _is_not_found_error(exc):
            logger.warning(f"FRED series {series_id} not available (skipping): {exc}")
            raise _FredSeriesNotFound(series_id) from exc
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


def get_series_value(series_id: str, macro_data: dict, periods_back: int = 1) -> float:
    """Get a specific FRED series value.

    Args:
        series_id:    FRED series key (e.g. "DGS10").
        macro_data:   Dict returned by fetch_macro_series.
        periods_back: 1 = most recent observation, 2 = second-most-recent, etc.

    Returns:
        The scalar value, or 0.0 if the series is absent / too short.
    """
    df = macro_data.get(series_id)
    if df is None or df.empty:
        return 0.0
    col = "value" if "value" in df.columns else df.columns[-1]
    return float(df[col].iloc[-periods_back]) if len(df) >= periods_back else 0.0


def get_series_change(series_id: str, macro_data: dict, periods: int = 4) -> float:
    """Get the fractional change in a series over the last N periods.

    Args:
        series_id:  FRED series key.
        macro_data: Dict returned by fetch_macro_series.
        periods:    How many periods back to compare against (default 4).

    Returns:
        Fractional change ((recent - prior) / prior), or 0.0 if insufficient data.
    """
    df = macro_data.get(series_id)
    if df is None or len(df) < periods + 1:
        return 0.0
    col = "value" if "value" in df.columns else df.columns[-1]
    recent = float(df[col].iloc[-1])
    prior = float(df[col].iloc[-(periods + 1)])
    return (recent - prior) / prior if prior != 0 else 0.0


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
