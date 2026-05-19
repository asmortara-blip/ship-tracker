"""
Trade flow data via World Bank WITS API (no API key required).

Replaces UN Comtrade. Uses:
  1. WITS trade statistics (World Bank trade database) — no auth
  2. World Bank merchandise trade indicators + sector % breakdowns — fallback
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from data.cache_manager import CacheManager
from data.normalizer import normalize_trade_df
from ports.port_registry import PORTS, PORT_TRAFFIC_WEIGHTS

# WITS API — World Bank trade statistics, no API key needed
_WITS_BASE = "https://wits.worldbank.org/API/V1/wits/datasource/tradeStats/tradestats-trade"

# World Bank merchandise trade indicator IDs (used as fallback)
_WB_BASE = "https://api.worldbank.org/v2"
_WB_SECTOR_INDICATORS = {
    "TX.VAL.MANF.ZS.UN": ("exports", "manufactured"),
    "TX.VAL.AGRI.ZS.UN": ("exports", "agriculture"),
    "TX.VAL.FUEL.ZS.UN": ("exports", "fuel"),
    "TM.VAL.MANF.ZS.UN": ("imports", "manufactured"),
    "TM.VAL.AGRI.ZS.UN": ("imports", "agriculture"),
}

# ISO3 → ISO2 for WITS country codes
_ISO3_TO_ISO2: dict[str, str] = {
    "USA": "US", "CHN": "CN", "NLD": "NL", "SGP": "SG", "DEU": "DE",
    "JPN": "JP", "KOR": "KR", "HKG": "HK", "MYS": "MY", "ARE": "AE",
    "BEL": "BE", "TWN": "TW", "MAR": "MA", "LKA": "LK", "GRC": "GR",
    "GBR": "GB", "BRA": "BR",
}

# HS-4 codes → category labels mapped to our config categories
_HS_CATEGORY_MAP = {
    "8471": "electronics", "8517": "electronics", "8542": "electronics",
    "8413": "machinery",   "8479": "machinery",   "8431": "machinery",
    "8703": "automotive",  "8708": "automotive",  "8716": "automotive",
    "6109": "apparel",     "6110": "apparel",     "6204": "apparel",
    "2902": "chemicals",   "2903": "chemicals",   "3901": "chemicals",
    "1001": "agriculture", "1201": "agriculture", "0901": "agriculture",
    "7208": "metals",      "7209": "metals",      "7210": "metals",
}

# Approximate global export share by category (used for synthetic splits)
_CATEGORY_SHARES = {
    "electronics": 0.28, "machinery": 0.22, "automotive": 0.18,
    "chemicals": 0.12,   "apparel": 0.08,  "metals": 0.07, "agriculture": 0.05,
}

# Country-specific export sector mix (ISO2 → category → share). Hand-tuned to
# reflect each economy's real export profile; exporters differ markedly in
# their electronics / machinery / automotive / apparel / agriculture mix.
# Countries not listed here fall back to the global _CATEGORY_SHARES table.
# Each row is normalised to sum to 1.0 before use, so the numbers below only
# need to be roughly proportional.
_COUNTRY_CATEGORY_SHARES: dict[str, dict[str, float]] = {
    # China — electronics/machinery powerhouse, large apparel base
    "CN": {"electronics": 0.34, "machinery": 0.24, "automotive": 0.10,
           "chemicals": 0.10, "apparel": 0.12, "metals": 0.06, "agriculture": 0.04},
    # United States — machinery/automotive/chemicals heavy, big agriculture
    "US": {"electronics": 0.20, "machinery": 0.24, "automotive": 0.16,
           "chemicals": 0.18, "apparel": 0.02, "metals": 0.06, "agriculture": 0.14},
    # Germany — automotive and machinery dominated
    "DE": {"electronics": 0.14, "machinery": 0.27, "automotive": 0.30,
           "chemicals": 0.18, "apparel": 0.02, "metals": 0.06, "agriculture": 0.03},
    # Japan — automotive and machinery
    "JP": {"electronics": 0.18, "machinery": 0.24, "automotive": 0.34,
           "chemicals": 0.13, "apparel": 0.01, "metals": 0.07, "agriculture": 0.03},
    # South Korea — electronics-led (semiconductors), strong automotive
    "KR": {"electronics": 0.38, "machinery": 0.16, "automotive": 0.21,
           "chemicals": 0.13, "apparel": 0.02, "metals": 0.07, "agriculture": 0.03},
    # Taiwan — semiconductor/electronics concentration
    "TW": {"electronics": 0.52, "machinery": 0.18, "automotive": 0.05,
           "chemicals": 0.13, "apparel": 0.02, "metals": 0.07, "agriculture": 0.03},
    # Hong Kong — re-export entrepôt skewed to electronics
    "HK": {"electronics": 0.45, "machinery": 0.16, "automotive": 0.05,
           "chemicals": 0.09, "apparel": 0.16, "metals": 0.06, "agriculture": 0.03},
    # Singapore — electronics and chemicals (refining/petrochemicals)
    "SG": {"electronics": 0.36, "machinery": 0.19, "automotive": 0.04,
           "chemicals": 0.28, "apparel": 0.02, "metals": 0.07, "agriculture": 0.04},
    # Netherlands — diversified entrepôt, notable agriculture/agri-food
    "NL": {"electronics": 0.22, "machinery": 0.20, "automotive": 0.11,
           "chemicals": 0.20, "apparel": 0.04, "metals": 0.07, "agriculture": 0.16},
    # Belgium — chemicals/pharma heavy
    "BE": {"electronics": 0.12, "machinery": 0.16, "automotive": 0.16,
           "chemicals": 0.34, "apparel": 0.03, "metals": 0.09, "agriculture": 0.10},
    # Malaysia — electronics and palm-oil agriculture
    "MY": {"electronics": 0.41, "machinery": 0.15, "automotive": 0.05,
           "chemicals": 0.13, "apparel": 0.04, "metals": 0.07, "agriculture": 0.15},
    # United Arab Emirates — fuels skew into chemicals/metals here
    "AE": {"electronics": 0.16, "machinery": 0.14, "automotive": 0.12,
           "chemicals": 0.30, "apparel": 0.04, "metals": 0.20, "agriculture": 0.04},
    # Sri Lanka — apparel-dominated, agriculture (tea)
    "LK": {"electronics": 0.06, "machinery": 0.06, "automotive": 0.03,
           "chemicals": 0.07, "apparel": 0.55, "metals": 0.05, "agriculture": 0.18},
    # Morocco — automotive assembly, apparel, phosphate-driven chemicals
    "MA": {"electronics": 0.12, "machinery": 0.10, "automotive": 0.27,
           "chemicals": 0.22, "apparel": 0.16, "metals": 0.05, "agriculture": 0.08},
    # Greece — agriculture and refined-product chemicals
    "GR": {"electronics": 0.07, "machinery": 0.12, "automotive": 0.05,
           "chemicals": 0.30, "apparel": 0.06, "metals": 0.13, "agriculture": 0.27},
    # United Kingdom — machinery/automotive/chemicals
    "GB": {"electronics": 0.18, "machinery": 0.22, "automotive": 0.22,
           "chemicals": 0.20, "apparel": 0.03, "metals": 0.06, "agriculture": 0.09},
    # Brazil — agriculture and metals (ore) heavy
    "BR": {"electronics": 0.07, "machinery": 0.13, "automotive": 0.12,
           "chemicals": 0.10, "apparel": 0.02, "metals": 0.18, "agriculture": 0.38},
}


def _category_shares_for_country(iso2: str) -> dict[str, float]:
    """Return the export sector-share table for a country, normalised to sum
    to 1.0. Falls back to the global shares for countries not in the
    hand-tuned per-country table."""
    raw = _COUNTRY_CATEGORY_SHARES.get(iso2, _CATEGORY_SHARES)
    total = sum(raw.values()) or 1.0
    return {cat: val / total for cat, val in raw.items()}


@st.cache_data(ttl=604800, hash_funcs={CacheManager: lambda _: None})
def fetch_all_ports(
    lookback_months: int = 3,
    cache: CacheManager | None = None,
    ttl_hours: float = 168.0,
) -> dict[str, pd.DataFrame]:
    """Fetch trade flow data for all tracked ports via WITS (no API key needed).

    Returns:
        dict mapping port_locode → DataFrame with trade columns.
    """
    cache = cache or CacheManager()

    # Build unique ISO2 country list from our ports
    country_ports: dict[str, list] = {}
    for port in PORTS:
        iso2 = _ISO3_TO_ISO2.get(port.country_iso3, "")
        if iso2:
            country_ports.setdefault(iso2, []).append(port)

    # Fetch years we want (WITS has ~1-2yr lag on latest data)
    current_year = datetime.now().year
    years = [str(current_year - 1), str(current_year - 2)]

    all_port_dfs: dict[str, list[pd.DataFrame]] = {p.locode: [] for p in PORTS}

    for iso2, ports_in_country in country_ports.items():
        for year in years:
            key = f"wits_{iso2}_{year}"
            df = cache.get_or_fetch(
                key=key,
                fetch_fn=lambda c=iso2, y=year: _fetch_wits_country(c, y),
                ttl_hours=ttl_hours,
                source="comtrade",
            )

            if df is None or df.empty:
                continue

            for port in ports_in_country:
                iso3 = port.country_iso3
                weight = 1.0
                if iso3 in PORT_TRAFFIC_WEIGHTS:
                    weight = PORT_TRAFFIC_WEIGHTS[iso3].get(port.locode, 1.0)

                port_df = df.copy()
                port_df["port_locode"] = port.locode
                port_df["value_usd"] *= weight
                port_df["net_weight_kg"] *= weight
                all_port_dfs[port.locode].append(port_df)

    results: dict[str, pd.DataFrame] = {}
    for locode, dfs in all_port_dfs.items():
        if dfs:
            combined = pd.concat(dfs, ignore_index=True)
            combined = combined.sort_values("date").reset_index(drop=True)
            results[locode] = combined
            logger.debug(f"WITS trade {locode}: {len(combined)} records")

    if not results:
        logger.info("WITS fetch returned empty — using World Bank merchandise fallback")
        results = _wb_merchandise_fallback(cache, ttl_hours)

    logger.info(f"Trade data loaded for {len(results)} ports")
    return results


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=3, max=15))
def _fetch_wits_country(iso2: str, year: str) -> pd.DataFrame:
    """Fetch country trade flows from WITS for a given year."""
    rows = []

    for flow in ["exports", "imports"]:
        url = f"{_WITS_BASE}/{iso2}/{year}/all/{flow}"
        params = {"format": "JSON"}
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                continue
            data = resp.json()
        except Exception as exc:
            logger.debug(f"WITS {iso2} {year} {flow}: {exc}")
            continue

        # WITS response: {"TradeStats": {"datasource": "tradeStats", "data": [...]}}
        # or directly a list of records
        records = []
        if isinstance(data, dict):
            records = (
                data.get("TradeStats", {}).get("data", [])
                or data.get("data", [])
                or []
            )
        elif isinstance(data, list):
            records = data

        flow_code = "X" if flow == "exports" else "M"
        date = pd.Timestamp(f"{year}-06-01")  # mid-year anchor

        for rec in records:
            if not isinstance(rec, dict):
                continue
            hs_code = str(rec.get("productCode", rec.get("cmdCode", "")))
            # Only keep 4-digit HS codes
            if len(hs_code) != 4:
                continue
            value = rec.get("tradeValue", rec.get("value", 0)) or 0
            try:
                value = float(value)
            except (ValueError, TypeError):
                continue
            if value <= 0:
                continue

            rows.append({
                "date": date,
                "port_locode": "",
                "hs_code": hs_code,
                "flow": flow_code,
                "value_usd": value * 1000,  # WITS usually in USD thousands
                "net_weight_kg": value * 500,  # rough proxy: 0.5kg per $1000
                "country_iso2": iso2,
                "source": "wits",
            })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    logger.debug(f"WITS {iso2} {year}: {len(df)} HS-4 trade rows")
    return df


def _wb_merchandise_fallback(
    cache: CacheManager,
    ttl_hours: float,
) -> dict[str, pd.DataFrame]:
    """Fallback: use World Bank merchandise totals + fixed sector shares."""
    country_ports: dict[str, list] = {}
    for port in PORTS:
        iso2 = _ISO3_TO_ISO2.get(port.country_iso3, "")
        if iso2:
            country_ports.setdefault(iso2, []).append(port)

    iso2_list = list(country_ports.keys())
    country_str = ";".join(iso2_list)
    current_year = datetime.now().year

    # Fetch total merchandise exports and imports
    results: dict[str, pd.DataFrame] = {}
    totals: dict[str, dict[str, float]] = {}  # iso2 → {exports: float, imports: float}

    for ind_id, label in [("TX.VAL.MRCH.CD.WT", "exports"), ("TM.VAL.MRCH.CD.WT", "imports")]:
        url = f"{_WB_BASE}/country/{country_str}/indicator/{ind_id}"
        params = {"format": "json", "per_page": 500, "mrv": 3}
        try:
            resp = requests.get(url, params=params, timeout=30)
            data = resp.json()
            if not data or len(data) < 2 or not data[1]:
                continue
            for rec in data[1]:
                if rec.get("value") is None:
                    continue
                country_code = rec.get("country", {}).get("id", "")
                val = float(rec["value"])
                totals.setdefault(country_code, {})[label] = val
        except Exception as exc:
            logger.debug(f"WB merchandise fallback {ind_id}: {exc}")

    # Build synthetic product-level rows using sector shares
    date = pd.Timestamp(f"{current_year - 1}-06-01")
    for iso2, ports_in_country in country_ports.items():
        country_total = totals.get(iso2, {})
        exp_total = country_total.get("exports", 5e10)
        imp_total = country_total.get("imports", 5e10)

        # Country-specific export sector mix (falls back to global shares).
        category_shares = _category_shares_for_country(iso2)

        rows = []
        for category, share in category_shares.items():
            # Map category to first HS code in that category
            hs_code = next(
                (k for k, v in _HS_CATEGORY_MAP.items() if v == category), "9999"
            )
            rows.append({
                "date": date, "port_locode": "",
                "hs_code": hs_code, "flow": "X",
                "value_usd": exp_total * share, "net_weight_kg": exp_total * share / 2000,
                "country_iso2": iso2, "source": "wb_synthetic",
            })
            rows.append({
                "date": date, "port_locode": "",
                "hs_code": hs_code, "flow": "M",
                "value_usd": imp_total * share, "net_weight_kg": imp_total * share / 2000,
                "country_iso2": iso2, "source": "wb_synthetic",
            })

        base_df = pd.DataFrame(rows)

        for port in ports_in_country:
            iso3 = port.country_iso3
            weight = 1.0
            if iso3 in PORT_TRAFFIC_WEIGHTS:
                weight = PORT_TRAFFIC_WEIGHTS[iso3].get(port.locode, 1.0)
            port_df = base_df.copy()
            port_df["port_locode"] = port.locode
            port_df["value_usd"] *= weight
            port_df["net_weight_kg"] *= weight
            results[port.locode] = port_df

    logger.info(f"WB merchandise fallback loaded for {len(results)} ports")
    return results


def get_top_products_for_port(
    port_locode: str,
    trade_data: dict[str, pd.DataFrame],
    top_n: int = 3,
    flow: str = "M",
) -> list[dict]:
    """Return top N product categories by trade value for a port."""
    from ports.product_mapper import get_category

    df = trade_data.get(port_locode)
    if df is None or df.empty:
        return []

    filtered = df[df["flow"] == flow] if "flow" in df.columns else df
    if filtered.empty:
        filtered = df

    if "hs_code" not in filtered.columns or "value_usd" not in filtered.columns:
        return []

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
