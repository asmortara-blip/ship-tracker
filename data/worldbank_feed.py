from __future__ import annotations

import pandas as pd
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from data.cache_manager import CacheManager
from data.normalizer import normalize_throughput_df
from ports.port_registry import PORTS, get_all_country_numerics


# World Bank API base
_WB_BASE = "https://api.worldbank.org/v2"

# Indicators to fetch
WB_INDICATORS: dict[str, str] = {
    "IS.SHP.GOOD.TU":   "Container Port Traffic (TEU)",
    "IS.SHP.GCNW.XQ":   "Liner Shipping Connectivity Index",
    "TX.VAL.MRCH.CD.WT": "Merchandise Exports (USD)",
    "TM.VAL.MRCH.CD.WT": "Merchandise Imports (USD)",
}

# ISO 3166-1 alpha-2 codes for World Bank API (maps from our alpha-3)
_ISO3_TO_ISO2: dict[str, str] = {
    "USA": "US", "CHN": "CN", "NLD": "NL",
    "SGP": "SG", "DEU": "DE", "JPN": "JP", "KOR": "KR",
    # Expanded port universe
    "HKG": "HK",  # Hong Kong (separate WB entity)
    "MYS": "MY",  # Malaysia (Port Klang + Tanjung Pelepas)
    "ARE": "AE",  # UAE (Jebel Ali)
    "BEL": "BE",  # Belgium (Antwerp)
    "TWN": "TW",  # Taiwan (Kaohsiung)
    "MAR": "MA",  # Morocco (Tanger Med)
    "LKA": "LK",  # Sri Lanka (Colombo)
    "GRC": "GR",  # Greece (Piraeus)
    "GBR": "GB",  # UK (Felixstowe)
    "BRA": "BR",  # Brazil (Santos)
}


def fetch_port_throughput(
    cache: CacheManager | None = None,
    ttl_hours: float = 168.0,
    years_back: int = 7,
) -> dict[str, pd.DataFrame]:
    """Fetch World Bank port throughput and trade indicators for tracked countries.

    Returns:
        dict mapping indicator_id → normalized DataFrame.
    """
    cache = cache or CacheManager()
    results: dict[str, pd.DataFrame] = {}

    # Unique ISO2 codes for our tracked ports
    iso2_codes = list({
        _ISO3_TO_ISO2[p.country_iso3]
        for p in PORTS
        if p.country_iso3 in _ISO3_TO_ISO2
    })

    for indicator_id, indicator_name in WB_INDICATORS.items():
        key = f"{indicator_id}_{'_'.join(sorted(iso2_codes))}_{years_back}y"
        df = cache.get_or_fetch(
            key=key,
            fetch_fn=lambda iid=indicator_id, iname=indicator_name, codes=iso2_codes, yb=years_back: _fetch_indicator(
                iid, iname, codes, yb
            ),
            ttl_hours=ttl_hours,
            source="worldbank",
        )
        if df is not None and not df.empty:
            results[indicator_id] = df

    logger.info(f"World Bank data loaded: {list(results.keys())}")
    return results


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_indicator(
    indicator_id: str,
    indicator_name: str,
    iso2_codes: list[str],
    years_back: int,
) -> pd.DataFrame:
    """Fetch a single World Bank indicator for a set of countries."""
    country_str = ";".join(iso2_codes)
    url = f"{_WB_BASE}/country/{country_str}/indicator/{indicator_id}"
    params = {
        "format": "json",
        "per_page": 500,
        "mrv": years_back,  # most recent values
    }

    logger.debug(f"World Bank fetch: {indicator_id} for {country_str}")

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error(f"World Bank {indicator_id} failed: {exc}")
        return pd.DataFrame()

    if not data or len(data) < 2 or not data[1]:
        logger.warning(f"World Bank returned no data for {indicator_id}")
        return pd.DataFrame()

    records = data[1]
    rows = []
    for rec in records:
        if rec.get("value") is None:
            continue
        rows.append({
            "year": int(rec.get("date", 0)),
            "country_iso3": _iso2_to_iso3(rec.get("countryiso3code", "")),
            "country_iso2": rec.get("country", {}).get("id", ""),
            "indicator_id": indicator_id,
            "indicator_name": indicator_name,
            "value": float(rec["value"]),
            "source": "worldbank",
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    logger.debug(f"  World Bank {indicator_id}: {len(df)} observations")
    return df


def get_teu_for_country(
    country_iso3: str,
    wb_data: dict[str, pd.DataFrame],
    port_locode: str = "",
) -> float:
    """Return the most recent TEU value (millions) for a country.

    Applies port traffic weight if the country has multiple tracked ports.
    """
    df = wb_data.get("IS.SHP.GOOD.TU")
    if df is None or df.empty:
        return 0.0

    country_df = df[df["country_iso3"] == country_iso3]
    if country_df.empty:
        return 0.0

    latest = country_df.sort_values("year").iloc[-1]
    raw_teu = float(latest["value"])  # in TEUs

    # Convert to millions and apply port weight if multiple ports in same country
    from ports.port_registry import PORT_TRAFFIC_WEIGHTS
    weight = 1.0
    if port_locode and country_iso3 in PORT_TRAFFIC_WEIGHTS:
        weight = PORT_TRAFFIC_WEIGHTS[country_iso3].get(port_locode, 1.0)

    return (raw_teu / 1_000_000) * weight


def get_connectivity_for_country(country_iso3: str, wb_data: dict[str, pd.DataFrame]) -> float:
    """Return the most recent Liner Shipping Connectivity Index for a country."""
    df = wb_data.get("IS.SHP.GCNW.XQ")
    if df is None or df.empty:
        return 0.0

    country_df = df[df["country_iso3"] == country_iso3]
    if country_df.empty:
        return 0.0

    return float(country_df.sort_values("year").iloc[-1]["value"])


def _iso2_to_iso3(code: str) -> str:
    _map = {v: k for k, v in _ISO3_TO_ISO2.items()}
    return _map.get(code.upper(), code)
