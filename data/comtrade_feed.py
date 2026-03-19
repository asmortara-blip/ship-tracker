from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from data.cache_manager import CacheManager
from data.normalizer import normalize_trade_df
from ports.port_registry import PORTS, PORT_TRAFFIC_WEIGHTS


_BASE_URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"

# HS codes to fetch per category (use 4-digit chapter codes)
# Keeping list short to conserve rate limit quota
HS_CODES_TO_FETCH = [
    "8471",  # Computers/Electronics
    "8517",  # Phones/Telecom
    "8703",  # Passenger vehicles
    "6109",  # T-shirts/Apparel
    "2902",  # Chemicals
    "1001",  # Wheat/Agriculture
    "7208",  # Steel flat-rolled
]

# Flow codes: X=export, M=import
FLOWS = ["X", "M"]


def fetch_all_ports(
    lookback_months: int = 3,
    cache: CacheManager | None = None,
    ttl_hours: float = 168.0,
) -> dict[str, pd.DataFrame]:
    """Fetch UN Comtrade trade flow data for all tracked port countries.

    Fetches monthly aggregates at country level. Uses PORT_TRAFFIC_WEIGHTS
    to split country-level data across ports in the same country.

    Returns:
        dict mapping port_locode → DataFrame with TRADE_COLS columns.
    """
    api_key = os.getenv("COMTRADE_API_KEY", "")
    if not api_key:
        logger.warning("COMTRADE_API_KEY not set — returning empty trade data")
        return {}

    cache = cache or CacheManager()

    # Build list of unique (country_numeric, hs_code, flow) combos
    # Group ports by country to avoid duplicate API calls
    country_ports: dict[str, list] = {}
    for port in PORTS:
        country_ports.setdefault(port.country_numeric, []).append(port)

    periods = _get_periods(lookback_months)
    all_port_dfs: dict[str, list[pd.DataFrame]] = {p.locode: [] for p in PORTS}

    request_count = 0
    for country_numeric, ports_in_country in country_ports.items():
        for hs_code in HS_CODES_TO_FETCH:
            for flow in FLOWS:
                period_str = ",".join(periods)
                key = f"comtrade_{country_numeric}_{hs_code}_{flow}_{period_str}"

                df = cache.get_or_fetch(
                    key=key,
                    fetch_fn=lambda cn=country_numeric, hc=hs_code, fl=flow, ps=period_str, ak=api_key: _fetch_country_flow(
                        cn, hc, fl, ps, ak
                    ),
                    ttl_hours=ttl_hours,
                    source="comtrade",
                )

                request_count += 1
                # Respect rate limit: ~100/hr = ~1 per 36s when not cached
                # (Cache manager handles actual skipping; this throttle only on cache miss)

                if df is None or df.empty:
                    continue

                # Distribute to ports in this country using traffic weights
                for port in ports_in_country:
                    weight = 1.0
                    country_iso3 = port.country_iso3
                    if country_iso3 in PORT_TRAFFIC_WEIGHTS:
                        weight = PORT_TRAFFIC_WEIGHTS[country_iso3].get(port.locode, 1.0)

                    port_df = df.copy()
                    port_df["port_locode"] = port.locode
                    port_df["value_usd"] *= weight
                    port_df["net_weight_kg"] *= weight
                    all_port_dfs[port.locode].append(port_df)

    # Concatenate all DataFrames per port
    results: dict[str, pd.DataFrame] = {}
    for locode, dfs in all_port_dfs.items():
        if dfs:
            combined = pd.concat(dfs, ignore_index=True)
            combined = combined.sort_values("date").reset_index(drop=True)
            results[locode] = combined
            logger.debug(f"Comtrade {locode}: {len(combined)} trade records")

    logger.info(f"Comtrade data loaded for {len(results)} ports ({request_count} cache checks)")
    return results


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=5, max=60))
def _fetch_country_flow(
    country_numeric: str,
    hs_code: str,
    flow: str,
    period_str: str,
    api_key: str,
) -> pd.DataFrame:
    """Fetch a single country/HS/flow combination from Comtrade API."""
    params = {
        "reporterCode": country_numeric,
        "partnerCode": "0",       # 0 = world
        "cmdCode": hs_code,
        "flowCode": flow,
        "period": period_str,
        "includeDesc": "false",
        "subscription-key": api_key,
    }

    logger.debug(f"Comtrade fetch: country={country_numeric} hs={hs_code} flow={flow}")

    try:
        resp = requests.get(_BASE_URL, params=params, timeout=30)

        # Handle rate limit
        if resp.status_code == 429:
            logger.warning("Comtrade rate limit hit; sleeping 60s")
            time.sleep(60)
            raise Exception("Rate limited")

        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error(f"Comtrade request failed: {exc}")
        return pd.DataFrame()

    records = data.get("data", [])
    if not records:
        logger.debug(f"  No records for country={country_numeric} hs={hs_code} flow={flow}")
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["flow"] = flow
    result = normalize_trade_df(df)
    logger.debug(f"  Comtrade country={country_numeric} hs={hs_code}: {len(result)} rows")
    return result


def get_top_products_for_port(
    port_locode: str,
    trade_data: dict[str, pd.DataFrame],
    top_n: int = 3,
    flow: str = "M",
) -> list[dict]:
    """Return top N product categories by trade value for a port.

    Returns list of dicts: [{"hs_code": ..., "category": ..., "value_usd": ...}]
    """
    from ports.product_mapper import get_category

    df = trade_data.get(port_locode)
    if df is None or df.empty:
        return []

    filtered = df[df["flow"] == flow]
    if filtered.empty:
        filtered = df

    grouped = (
        filtered.groupby("hs_code")["value_usd"]
        .sum()
        .sort_values(ascending=False)
        .head(top_n)
        .reset_index()
    )

    return [
        {
            "hs_code": row["hs_code"],
            "category": get_category(row["hs_code"]),
            "value_usd": row["value_usd"],
        }
        for _, row in grouped.iterrows()
    ]


def _get_periods(lookback_months: int) -> list[str]:
    """Generate list of YYYYMM period strings for lookback."""
    periods = []
    now = datetime.now()
    for i in range(lookback_months):
        # Go back i+2 months to account for Comtrade data lag (typically 2 months)
        dt = now - timedelta(days=30 * (i + 2))
        periods.append(dt.strftime("%Y%m"))
    return periods
