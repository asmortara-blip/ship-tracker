from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests
from loguru import logger

from data.cache_manager import CacheManager

# ---------------------------------------------------------------------------
# Base URL
# ---------------------------------------------------------------------------

_IMF_BASE = "http://dataservices.imf.org/REST/SDMX_JSON/CompactData"

# ---------------------------------------------------------------------------
# Endpoint definitions
# ---------------------------------------------------------------------------

_WEO_COUNTRIES = "USA+CHN+DEU+JPN+KOR+SGP+NLD+GBR+FRA+IND+BRA"

_GDP_URL = f"{_IMF_BASE}/WEO/A.{_WEO_COUNTRIES}.NGDP_RPCH"

_DOTS_URL = f"{_IMF_BASE}/DOT/Q.US+CN+DE.TXG_FOB_USD+TMG_CIF_USD.W00"

_COMMODITY_URL = (
    f"{_IMF_BASE}/PCPS/M.W00.PCOAL+PIRON+PNGAS_US+POILAPSP+PSOYBEA+PWHEAT"
)

# Human-readable labels for commodity codes
_COMMODITY_LABELS: dict[str, str] = {
    "PCOAL": "Coal",
    "PIRON": "Iron Ore",
    "PNGAS_US": "Natural Gas (US)",
    "POILAPSP": "Oil (Avg Spot)",
    "PSOYBEA": "Soybeans",
    "PWHEAT": "Wheat",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fetch_url(url: str, label: str) -> dict:
    """GET a URL and return parsed JSON, or {} on any error."""
    try:
        resp = requests.get(url, timeout=20, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        logger.warning(f"IMF {label}: request timed out")
    except requests.exceptions.HTTPError as exc:
        logger.warning(f"IMF {label}: HTTP {exc.response.status_code}")
    except requests.exceptions.RequestException as exc:
        logger.warning(f"IMF {label}: network error — {exc}")
    except Exception as exc:
        logger.error(f"IMF {label}: unexpected error — {exc}")
    return {}


def _cached_json(cache: CacheManager, key: str, fetch_fn, ttl_hours: float) -> dict:
    """Persist a JSON payload through CacheManager using a single-cell parquet."""
    import json
    import pandas as pd

    def _as_df() -> pd.DataFrame:
        payload = fetch_fn()
        if not payload:
            return pd.DataFrame()
        return pd.DataFrame({"json": [json.dumps(payload)]})

    df = cache.get_or_fetch(key, _as_df, ttl_hours=ttl_hours, source="imf")
    if df is None or df.empty:
        return {}
    try:
        return json.loads(df["json"].iloc[0])
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# IMF Compact SDMX-JSON parser
# ---------------------------------------------------------------------------


def _parse_imf_compact(data: dict) -> dict[str, list[tuple[str, float]]]:
    """Parse IMF CompactData SDMX-JSON into {series_key: [(date, value)]} dict.

    The IMF compact format nests data under:
        data["CompactData"]["DataSet"]["Series"]
    Each Series has attributes (dimension values) and "Obs" list with
    @TIME_PERIOD and @OBS_VALUE attributes.
    """
    try:
        dataset = (
            data.get("CompactData", {})
            .get("DataSet", {})
        )
        if not dataset:
            logger.debug("IMF compact: no DataSet found")
            return {}

        raw_series = dataset.get("Series", [])
        # IMF sometimes returns a single dict instead of a list
        if isinstance(raw_series, dict):
            raw_series = [raw_series]

        result: dict[str, list[tuple[str, float]]] = {}

        for series in raw_series:
            if not isinstance(series, dict):
                continue

            # Build a series key from all @-prefixed attributes except meta
            key_parts = []
            for attr, val in series.items():
                if attr.startswith("@") and attr not in ("@xmlns",):
                    key_parts.append(f"{attr[1:]}={val}")
            series_key = "|".join(key_parts)

            obs_list = series.get("Obs", [])
            if isinstance(obs_list, dict):
                obs_list = [obs_list]

            points: list[tuple[str, float]] = []
            for obs in obs_list:
                if not isinstance(obs, dict):
                    continue
                time_val = obs.get("@TIME_PERIOD")
                obs_val = obs.get("@OBS_VALUE")
                if time_val is None or obs_val is None:
                    continue
                try:
                    points.append((str(time_val), float(obs_val)))
                except (TypeError, ValueError):
                    continue

            if points:
                points.sort(key=lambda x: x[0])
                result[series_key] = points

        return result

    except Exception as exc:
        logger.error(f"_parse_imf_compact failed: {exc}")
        return {}


def _extract_attr(series_key: str, attr_name: str) -> str | None:
    """Extract a named attribute value from a parsed series key string."""
    for part in series_key.split("|"):
        if part.startswith(f"{attr_name}="):
            return part.split("=", 1)[1]
    return None


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------


def fetch_imf_data(cache_ttl_hours: float = 48.0) -> dict:
    """Fetch IMF economic and trade data relevant to global shipping.

    Returns dict with keys:
    - 'gdp_forecasts': dict[country_code -> latest_growth_pct]
    - 'trade_flows': dict[country_code -> {exports_usd, imports_usd, latest_date}]
    - 'commodity_prices': dict[commodity -> {latest_price, change_3m_pct}]
    - 'fetched_at': ISO timestamp

    Returns empty dict on failure. No API key needed.
    """
    cache = CacheManager()

    gdp_raw = _cached_json(
        cache, "weo_gdp_forecasts",
        lambda: _fetch_url(_GDP_URL, "WEO GDP"),
        cache_ttl_hours,
    )

    dots_raw = _cached_json(
        cache, "dots_trade_flows",
        lambda: _fetch_url(_DOTS_URL, "DOTS Trade"),
        cache_ttl_hours,
    )

    pcps_raw = _cached_json(
        cache, "pcps_commodity_prices",
        lambda: _fetch_url(_COMMODITY_URL, "PCPS Commodities"),
        cache_ttl_hours,
    )

    # --- Parse GDP forecasts ---
    gdp_forecasts: dict[str, float] = {}
    if gdp_raw:
        parsed = _parse_imf_compact(gdp_raw)
        for series_key, series in parsed.items():
            country = _extract_attr(series_key, "REF_AREA")
            if country and series:
                gdp_forecasts[country] = series[-1][1]
    logger.info(f"IMF GDP: {len(gdp_forecasts)} countries — {list(gdp_forecasts.keys())}")

    # --- Parse trade flows (DOTS) ---
    trade_flows: dict[str, dict] = {}
    if dots_raw:
        parsed = _parse_imf_compact(dots_raw)
        for series_key, series in parsed.items():
            country = _extract_attr(series_key, "REF_AREA")
            indicator = _extract_attr(series_key, "INDICATOR") or _extract_attr(series_key, "COMMODITY")
            if not country or not series:
                continue
            entry = trade_flows.setdefault(country, {
                "exports_usd": None,
                "imports_usd": None,
                "latest_date": None,
            })
            latest_date, latest_val = series[-1]
            # TXG = exports, TMG = imports
            if indicator and "TXG" in indicator:
                entry["exports_usd"] = latest_val
                entry["latest_date"] = latest_date
            elif indicator and "TMG" in indicator:
                entry["imports_usd"] = latest_val
                if entry["latest_date"] is None:
                    entry["latest_date"] = latest_date
    logger.info(f"IMF DOTS: {len(trade_flows)} countries — {list(trade_flows.keys())}")

    # --- Parse commodity prices (PCPS) ---
    commodity_prices: dict[str, dict] = {}
    if pcps_raw:
        parsed = _parse_imf_compact(pcps_raw)
        for series_key, series in parsed.items():
            # Commodity code lives in COMMODITY attribute
            commodity_code = (
                _extract_attr(series_key, "COMMODITY")
                or _extract_attr(series_key, "INDICATOR")
            )
            if not commodity_code or not series:
                continue
            label = _COMMODITY_LABELS.get(commodity_code, commodity_code)
            latest_price = series[-1][1]
            change_3m_pct = 0.0
            if len(series) >= 4:
                price_3m_ago = series[-4][1]
                if price_3m_ago and price_3m_ago != 0:
                    change_3m_pct = round(
                        (latest_price - price_3m_ago) / abs(price_3m_ago) * 100, 2
                    )
            commodity_prices[label] = {
                "latest_price": round(latest_price, 4),
                "change_3m_pct": change_3m_pct,
            }
    logger.info(f"IMF PCPS: {len(commodity_prices)} commodities — {list(commodity_prices.keys())}")

    if not gdp_forecasts and not trade_flows and not commodity_prices:
        logger.warning("IMF: all endpoints returned empty — returning {}")
        return {}

    return {
        "gdp_forecasts": gdp_forecasts,
        "trade_flows": trade_flows,
        "commodity_prices": commodity_prices,
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Derived signal
# ---------------------------------------------------------------------------


def get_shipping_demand_outlook(imf_data: dict) -> dict:
    """Derive shipping demand signals from IMF data.

    Returns:
        {
            'global_gdp_signal': str,         # "Accelerating" | "Stable" | "Decelerating"
            'commodity_shipping_demand': str,  # "High" | "Moderate" | "Low"
            'iron_ore_trend': str,             # "Rising" | "Falling" | "Stable"
            'oil_trend': str,
            'top_growth_economies': list[str], # top 3 countries by GDP growth
            'demand_score': float,             # 0-1 composite shipping demand signal
        }
    """
    defaults: dict[str, Any] = {
        "global_gdp_signal": "Stable",
        "commodity_shipping_demand": "Moderate",
        "iron_ore_trend": "Stable",
        "oil_trend": "Stable",
        "top_growth_economies": [],
        "demand_score": 0.5,
    }

    if not imf_data:
        return defaults

    # --- Global GDP signal ---
    global_gdp_signal = "Stable"
    top_growth_economies: list[str] = []
    try:
        gdp = imf_data.get("gdp_forecasts", {})
        if gdp:
            sorted_countries = sorted(gdp.items(), key=lambda x: x[1], reverse=True)
            top_growth_economies = [c for c, _ in sorted_countries[:3]]
            growth_values = list(gdp.values())
            avg_growth = sum(growth_values) / len(growth_values)
            # IMF WEO values are year-on-year % change; > 3% = accelerating
            if avg_growth > 3.5:
                global_gdp_signal = "Accelerating"
            elif avg_growth < 2.0:
                global_gdp_signal = "Decelerating"
    except Exception as exc:
        logger.debug(f"GDP signal calc failed: {exc}")

    # --- Commodity trends ---
    def _trend(change_pct: float, threshold: float = 2.0) -> str:
        if change_pct > threshold:
            return "Rising"
        elif change_pct < -threshold:
            return "Falling"
        return "Stable"

    iron_ore_trend = "Stable"
    oil_trend = "Stable"
    commodity_shipping_demand = "Moderate"

    try:
        commodities = imf_data.get("commodity_prices", {})

        iron = commodities.get("Iron Ore", {})
        iron_ore_trend = _trend(iron.get("change_3m_pct", 0.0))

        oil = commodities.get("Oil (Avg Spot)", {})
        oil_trend = _trend(oil.get("change_3m_pct", 0.0))

        coal = commodities.get("Coal", {})
        soy = commodities.get("Soybeans", {})
        wheat = commodities.get("Wheat", {})

        # Bulk shipping demand: coal + iron ore + grains all rising = High
        bulk_indicators = [
            iron.get("change_3m_pct", 0.0),
            coal.get("change_3m_pct", 0.0),
            soy.get("change_3m_pct", 0.0),
            wheat.get("change_3m_pct", 0.0),
        ]
        avg_bulk_change = sum(bulk_indicators) / len(bulk_indicators) if bulk_indicators else 0.0
        if avg_bulk_change > 3.0:
            commodity_shipping_demand = "High"
        elif avg_bulk_change < -3.0:
            commodity_shipping_demand = "Low"

    except Exception as exc:
        logger.debug(f"Commodity trend calc failed: {exc}")

    # --- Composite demand score ---
    demand_score = 0.5
    try:
        score_components: list[float] = []

        # GDP component (0-1): avg growth / 6 capped at 1
        gdp = imf_data.get("gdp_forecasts", {})
        if gdp:
            avg_g = sum(gdp.values()) / len(gdp)
            score_components.append(max(0.0, min(1.0, avg_g / 6.0)))

        # Commodity component
        commodity_map = {"High": 0.9, "Moderate": 0.5, "Low": 0.1}
        score_components.append(commodity_map.get(commodity_shipping_demand, 0.5))

        # Iron ore component (Rising=0.8, Stable=0.5, Falling=0.2)
        trend_map = {"Rising": 0.8, "Stable": 0.5, "Falling": 0.2}
        score_components.append(trend_map.get(iron_ore_trend, 0.5))

        if score_components:
            demand_score = round(sum(score_components) / len(score_components), 3)

    except Exception as exc:
        logger.debug(f"Demand score calc failed: {exc}")

    return {
        "global_gdp_signal": global_gdp_signal,
        "commodity_shipping_demand": commodity_shipping_demand,
        "iron_ore_trend": iron_ore_trend,
        "oil_trend": oil_trend,
        "top_growth_economies": top_growth_economies,
        "demand_score": demand_score,
    }


# ---------------------------------------------------------------------------
# Test entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import json

    logger.info("=== IMF Feed — quick test ===")
    data = fetch_imf_data(cache_ttl_hours=48.0)

    if not data:
        print("IMF fetch returned empty dict — check network / endpoint availability.")
        return

    print(f"\nFetched at: {data.get('fetched_at')}")
    print(f"GDP forecasts    : {data.get('gdp_forecasts')}")
    print(f"Trade flow keys  : {list(data.get('trade_flows', {}).keys())}")
    print(f"Commodities      : {list(data.get('commodity_prices', {}).keys())}")

    outlook = get_shipping_demand_outlook(data)
    print("\n--- Shipping Demand Outlook ---")
    print(json.dumps(outlook, indent=2))


if __name__ == "__main__":
    main()
