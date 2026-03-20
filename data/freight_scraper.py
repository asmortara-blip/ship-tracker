from __future__ import annotations

import re
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from data.cache_manager import CacheManager
from data.normalizer import normalize_freight_df
from routes.route_registry import ROUTES, get_all_fbx_indices


# FBX index metadata (used for scraping and labeling)
FBX_INDICES = {
    "FBX01":  {"name": "Trans-Pacific Eastbound",        "route_id": "transpacific_eb"},
    "FBX02":  {"name": "Trans-Pacific Westbound",        "route_id": "transpacific_wb"},
    "FBX03":  {"name": "Asia-Europe",                    "route_id": "asia_europe"},
    "FBX04":  {"name": "Europe-Asia",                    "route_id": "med_hub_to_asia"},
    "FBX11":  {"name": "Transatlantic EB",               "route_id": "transatlantic"},
    "FBX12":  {"name": "Transatlantic WB",               "route_id": "transatlantic"},
    "FBX21":  {"name": "Asia-S. America West Coast",     "route_id": "china_south_america"},
    "FBX22":  {"name": "S. America West Coast-Asia",     "route_id": "china_south_america"},
    "FBX31":  {"name": "Europe-S. America East Coast",   "route_id": "europe_south_america"},
    "FBX32":  {"name": "S. America East Coast-Europe",   "route_id": "europe_south_america"},
    "FBXGLO": {"name": "Global Container Index",         "route_id": "global"},
}

# Public Freightos data endpoints (these are publicly accessible)
_FBX_API_URL = "https://fbx.freightos.com/api/v1/indices"
_FBX_PAGE_URL = "https://fbx.freightos.com/"


@st.cache_data(ttl=86400, hash_funcs={CacheManager: lambda _: None})
def fetch_fbx_rates(
    lookback_days: int = 120,
    cache: CacheManager | None = None,
    ttl_hours: float = 24.0,
) -> dict[str, pd.DataFrame]:
    """Fetch Freightos Baltic Index rates for all tracked routes.

    Attempts multiple strategies:
    1. FBX API (public JSON endpoint)
    2. FBX page scrape (HTML/embedded JSON)
    3. Synthetic flat-rate fallback (neutral signal)

    Returns:
        dict mapping route_id → normalized DataFrame with FREIGHT_COLS columns.
    """
    cache = cache or CacheManager()
    results: dict[str, pd.DataFrame] = {}

    key = f"fbx_all_{lookback_days}d"
    raw_df = cache.get_or_fetch(
        key=key,
        fetch_fn=lambda lb=lookback_days: _fetch_fbx_all(lb),
        ttl_hours=ttl_hours,
        source="freight",
    )

    if raw_df is None or raw_df.empty:
        logger.warning("FBX fetch failed; using synthetic fallback rates")
        return _synthetic_fallback()

    for route in ROUTES:
        route_df = raw_df[raw_df["route_id"] == route.id]
        if not route_df.empty:
            results[route.id] = route_df
        else:
            logger.debug(f"No FBX data for {route.id}; using fallback")
            results[route.id] = _single_fallback(route.id, route.fbx_index)

    return results


def _fetch_fbx_all(lookback_days: int) -> pd.DataFrame:
    """Try multiple strategies to get FBX data."""
    # Strategy 1: Try the public API
    df = _try_fbx_api(lookback_days)
    if df is not None and not df.empty:
        logger.info(f"FBX: loaded {len(df)} rate records from API")
        return df

    # Strategy 2: Scrape the page for embedded JSON
    df = _try_fbx_scrape()
    if df is not None and not df.empty:
        logger.info(f"FBX: loaded {len(df)} rate records from scrape")
        return df

    logger.warning("All FBX fetch strategies failed")
    return pd.DataFrame()


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=10))
def _try_fbx_api(lookback_days: int) -> pd.DataFrame | None:
    """Attempt to fetch from Freightos public API endpoint."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ShipTracker/1.0)",
            "Accept": "application/json",
        }
        resp = requests.get(_FBX_API_URL, headers=headers, timeout=20)

        if resp.status_code != 200:
            return None

        data = resp.json()
        return _parse_fbx_json(data, lookback_days)

    except Exception as exc:
        logger.debug(f"FBX API attempt failed: {exc}")
        return None


def _try_fbx_scrape() -> pd.DataFrame | None:
    """Scrape FBX page for embedded rate data."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ShipTracker/1.0)"}
        resp = requests.get(_FBX_PAGE_URL, headers=headers, timeout=20)

        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        # Look for JSON data embedded in script tags
        for script in soup.find_all("script"):
            text = script.string or ""
            if "FBX" in text and ("rate" in text.lower() or "index" in text.lower()):
                # Try to extract JSON objects
                json_matches = re.findall(r'\{[^{}]{100,}\}', text)
                for match in json_matches:
                    try:
                        import json
                        obj = json.loads(match)
                        df = _parse_fbx_json(obj, 120)
                        if df is not None and not df.empty:
                            return df
                    except Exception:
                        continue

        return None

    except Exception as exc:
        logger.debug(f"FBX scrape failed: {exc}")
        return None


def _parse_fbx_json(data: dict | list, lookback_days: int) -> pd.DataFrame | None:
    """Parse various FBX JSON response formats into a unified DataFrame."""
    rows = []
    cutoff = datetime.now() - timedelta(days=lookback_days)

    # Handle list of index objects
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                rows.extend(_extract_fbx_rows(item, cutoff))
    elif isinstance(data, dict):
        # Handle nested structures
        for key in ["indices", "data", "rates", "results"]:
            if key in data:
                sub = data[key]
                if isinstance(sub, list):
                    for item in sub:
                        rows.extend(_extract_fbx_rows(item, cutoff))
                break
        else:
            rows.extend(_extract_fbx_rows(data, cutoff))

    if not rows:
        return None

    df = pd.DataFrame(rows)
    return df


def _extract_fbx_rows(item: dict, cutoff: datetime) -> list[dict]:
    """Extract rate rows from a single FBX JSON object."""
    rows = []
    index_code = item.get("index", item.get("code", item.get("id", "")))

    if index_code not in FBX_INDICES:
        return rows

    meta = FBX_INDICES[index_code]
    route_id = meta["route_id"]

    # Handle different date/rate field names
    date_str = item.get("date", item.get("timestamp", item.get("period", "")))
    rate = item.get("rate", item.get("value", item.get("price", item.get("close", 0))))

    try:
        date = pd.to_datetime(date_str)
        if date < cutoff:
            return rows
        rows.append({
            "date": date,
            "route_id": route_id,
            "rate_usd_per_feu": float(rate),
            "index_name": index_code,
            "source": "freightos_fbx",
        })
    except Exception:
        pass

    # Also check for series/history arrays
    for hist_key in ["history", "series", "data", "values"]:
        if hist_key in item and isinstance(item[hist_key], list):
            for point in item[hist_key]:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    try:
                        date = pd.to_datetime(point[0])
                        if date >= cutoff:
                            rows.append({
                                "date": date,
                                "route_id": route_id,
                                "rate_usd_per_feu": float(point[1]),
                                "index_name": index_code,
                                "source": "freightos_fbx",
                            })
                    except Exception:
                        continue

    return rows


def _synthetic_fallback() -> dict[str, pd.DataFrame]:
    """Generate neutral fallback data when all real fetches fail."""
    results = {}
    for route in ROUTES:
        results[route.id] = _single_fallback(route.id, route.fbx_index)
    return results


def _single_fallback(route_id: str, fbx_index: str) -> pd.DataFrame:
    """Single-point fallback DataFrame with a neutral rate."""
    _DEFAULT_RATES = {
        "FBX01": 2500.0,  # Trans-Pacific EB (USD/FEU) — rough long-term avg
        "FBX02": 800.0,   # Trans-Pacific WB
        "FBX03": 1800.0,  # Asia-Europe
        "FBX11": 1200.0,  # Transatlantic
    }
    rate = _DEFAULT_RATES.get(fbx_index, 1500.0)
    df = pd.DataFrame([{
        "date": datetime.now(),
        "route_id": route_id,
        "rate_usd_per_feu": rate,
        "index_name": fbx_index,
        "source": "fallback",
    }])
    return normalize_freight_df(df, route_id=route_id, index_name=fbx_index)


def get_current_rate(route_id: str, freight_data: dict[str, pd.DataFrame]) -> float:
    """Return most recent rate for a route in USD/FEU."""
    df = freight_data.get(route_id)
    if df is None or df.empty:
        return 0.0
    return float(df["rate_usd_per_feu"].iloc[-1])


def get_rate_trend(
    route_id: str,
    freight_data: dict[str, pd.DataFrame],
    lookback_days: int = 90,
) -> tuple[float, str]:
    """Return (pct_change_30d, trend_label) for a route.

    trend_label is one of: "Rising", "Stable", "Falling"
    """
    from utils.helpers import trend_label

    df = freight_data.get(route_id)
    if df is None or len(df) < 2:
        return 0.0, "Stable"

    df = df.sort_values("date")
    recent = df.tail(31)

    if len(recent) < 2:
        return 0.0, "Stable"

    start_rate = recent["rate_usd_per_feu"].iloc[0]
    end_rate = recent["rate_usd_per_feu"].iloc[-1]

    if start_rate == 0:
        return 0.0, "Stable"

    pct = (end_rate - start_rate) / start_rate
    label = trend_label(pct)
    return pct, label
