from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests
from loguru import logger

from data.cache_manager import CacheManager

# ---------------------------------------------------------------------------
# Base URL
# ---------------------------------------------------------------------------

_OECD_BASE = "https://stats.oecd.org/SDMX-JSON/data"

# ---------------------------------------------------------------------------
# Endpoint definitions
# ---------------------------------------------------------------------------

_CLI_URL = (
    f"{_OECD_BASE}/MEI_CLI/"
    "LOLITOAA+LOLITONO.AUS+CAN+CHL+CHN+DEU+FRA+GBR+IND+ITA+JPN+KOR+MEX+NLD+NOR+SGP+USA+OECDE.M"
    "/all?startTime=2023-01&endTime=2025-12&dimensionAtObservation=allDimensions"
)

_TRADE_URL = (
    f"{_OECD_BASE}/MEI_TRADE/"
    "XTIMVA01+XTEXVA01.USA+CHN+DEU+JPN+KOR+NLD+GBR+FRA.GP.M"
    "/all?startTime=2023-01&endTime=2025-12&dimensionAtObservation=allDimensions"
)

_IP_URL = (
    f"{_OECD_BASE}/MEI/"
    "PRINTO01.CHN+USA+DEU+JPN+KOR.IXOBSA.M"
    "/all?startTime=2023-01&endTime=2025-12&dimensionAtObservation=allDimensions"
)

# ---------------------------------------------------------------------------
# SDMX-JSON parser
# ---------------------------------------------------------------------------


def _parse_sdmx_json(data: dict) -> dict[str, list[tuple[str, float]]]:
    """Parse OECD SDMX-JSON response into {series_key: [(date, value)]} dict.

    The SDMX-JSON format stores observations keyed by dimension index tuples.
    For allDimensions requests, the observation key encodes every dimension
    (including time) as colon-separated positional indices.
    """
    try:
        structure = data.get("structure", {})
        dims_obs = structure.get("dimensions", {}).get("observation", [])

        # Build a mapping from dim position → list of values for each dimension
        dim_values: list[list[str]] = []
        time_dim_index: int | None = None

        for i, dim in enumerate(dims_obs):
            values = [v.get("id", str(v)) for v in dim.get("values", [])]
            dim_values.append(values)
            if dim.get("keyPosition") is not None or dim.get("role") == "time":
                # The time dimension is typically last or has role=time
                if "TIME" in dim.get("id", "").upper() or dim.get("role") == "time":
                    time_dim_index = i

        if not dim_values:
            logger.warning("SDMX-JSON: no observation dimensions found")
            return {}

        # Time dimension is usually the last one
        if time_dim_index is None:
            time_dim_index = len(dim_values) - 1

        observations: dict[str, list] = (
            data.get("dataSets", [{}])[0].get("observations", {})
        )

        result: dict[str, list[tuple[str, float]]] = {}

        for obs_key, obs_vals in observations.items():
            indices = obs_key.split(":")
            if len(indices) != len(dim_values):
                continue

            value = obs_vals[0] if obs_vals else None
            if value is None:
                continue

            try:
                float_val = float(value)
            except (TypeError, ValueError):
                continue

            # Build a series key from all non-time dimensions
            parts = []
            date_str = None
            for i, idx_str in enumerate(indices):
                try:
                    idx = int(idx_str)
                except ValueError:
                    continue
                if i == time_dim_index:
                    dim_list = dim_values[i]
                    date_str = dim_list[idx] if idx < len(dim_list) else None
                else:
                    dim_list = dim_values[i]
                    parts.append(dim_list[idx] if idx < len(dim_list) else idx_str)

            if date_str is None:
                continue

            series_key = ":".join(parts)
            result.setdefault(series_key, []).append((date_str, float_val))

        # Sort each series by date
        for k in result:
            result[k].sort(key=lambda x: x[0])

        return result

    except Exception as exc:
        logger.error(f"_parse_sdmx_json failed: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Individual fetchers (called by cache layer)
# ---------------------------------------------------------------------------


def _fetch_url(url: str, label: str) -> dict:
    """GET a URL and return parsed JSON, or {} on any error."""
    try:
        resp = requests.get(url, timeout=20, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        logger.warning(f"OECD {label}: request timed out")
    except requests.exceptions.HTTPError as exc:
        logger.warning(f"OECD {label}: HTTP {exc.response.status_code}")
    except requests.exceptions.RequestException as exc:
        logger.warning(f"OECD {label}: network error — {exc}")
    except Exception as exc:
        logger.error(f"OECD {label}: unexpected error — {exc}")
    return {}


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------


def fetch_oecd_indicators(cache_ttl_hours: float = 48.0) -> dict:
    """Fetch OECD economic indicators relevant to shipping demand.

    Returns dict with keys:
    - 'cli': dict[country_code -> list of (date, value)] — Leading indicators
    - 'trade': dict[country_code -> {imports: [...], exports: [...]}]
    - 'industrial_production': dict[country_code -> list of (date, value)]
    - 'fetched_at': ISO timestamp

    Returns empty dict on failure. No API key needed.
    """
    cache = CacheManager()

    # --- CLI ---
    def _do_fetch_cli() -> dict:
        return _fetch_url(_CLI_URL, "CLI")

    def _do_fetch_trade() -> dict:
        return _fetch_url(_TRADE_URL, "Trade")

    def _do_fetch_ip() -> dict:
        return _fetch_url(_IP_URL, "Industrial Production")

    # We cache the raw JSON as a single-row DataFrame keyed by 'json'
    # Since CacheManager expects DataFrames, we store results as dicts
    # and use a lightweight wrapper.
    import pandas as pd

    def _cached_json(key: str, fetch_fn) -> dict:
        """Cache a JSON payload via CacheManager using a single-cell parquet."""
        import json

        def _as_df():
            payload = fetch_fn()
            if not payload:
                return pd.DataFrame()
            return pd.DataFrame({"json": [json.dumps(payload)]})

        df = cache.get_or_fetch(key, _as_df, ttl_hours=cache_ttl_hours, source="oecd")
        if df is None or df.empty:
            return {}
        try:
            import json as _json
            return _json.loads(df["json"].iloc[0])
        except Exception:
            return {}

    cli_raw = _cached_json("cli_mei_cli", _do_fetch_cli)
    trade_raw = _cached_json("trade_mei_trade", _do_fetch_trade)
    ip_raw = _cached_json("ip_mei", _do_fetch_ip)

    # --- Parse CLI ---
    cli_out: dict[str, list[tuple[str, float]]] = {}
    if cli_raw:
        parsed = _parse_sdmx_json(cli_raw)
        # parsed keys are like "LOLITOAA:USA" or similar; extract country
        for series_key, series in parsed.items():
            parts = series_key.split(":")
            # Expect: indicator:country
            country = parts[-1] if len(parts) >= 1 else series_key
            indicator = parts[0] if len(parts) >= 2 else "CLI"
            composite_key = f"{country}:{indicator}"
            cli_out[composite_key] = series
    logger.info(f"OECD CLI: {len(cli_out)} series parsed")

    # --- Parse Trade ---
    trade_out: dict[str, dict] = {}
    if trade_raw:
        parsed = _parse_sdmx_json(trade_raw)
        for series_key, series in parsed.items():
            parts = series_key.split(":")
            # Expected structure: indicator:country:freq (XTIMVA01/XTEXVA01, country, GP)
            # After stripping time dim, parts are indicator, country, [freq]
            if len(parts) < 2:
                continue
            indicator = parts[0]
            country = parts[1]
            entry = trade_out.setdefault(country, {"imports": [], "exports": []})
            if "XTIMVA01" in indicator:
                entry["imports"] = series
            elif "XTEXVA01" in indicator:
                entry["exports"] = series
    logger.info(f"OECD Trade: {len(trade_out)} countries parsed")

    # --- Parse Industrial Production ---
    ip_out: dict[str, list[tuple[str, float]]] = {}
    if ip_raw:
        parsed = _parse_sdmx_json(ip_raw)
        for series_key, series in parsed.items():
            parts = series_key.split(":")
            # Parts: indicator:country:adjustment
            country = parts[1] if len(parts) >= 2 else series_key
            ip_out[country] = series
    logger.info(f"OECD IP: {len(ip_out)} countries parsed")

    if not cli_out and not trade_out and not ip_out:
        logger.warning("OECD: all endpoints returned empty — returning {}")
        return {}

    return {
        "cli": cli_out,
        "trade": trade_out,
        "industrial_production": ip_out,
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Derived signal
# ---------------------------------------------------------------------------


def get_global_trade_momentum(oecd_data: dict) -> dict:
    """Summarize trade momentum from OECD data.

    Returns:
        {
            'us_trade_growth_3m': float,   # 3-month trade growth %
            'china_ip_trend': str,          # "Expanding" | "Contracting" | "Stable"
            'eu_cli_signal': str,           # "Positive" | "Negative" | "Neutral"
            'asia_demand_score': float,     # 0-1 composite
        }
    """
    defaults: dict[str, Any] = {
        "us_trade_growth_3m": 0.0,
        "china_ip_trend": "Stable",
        "eu_cli_signal": "Neutral",
        "asia_demand_score": 0.5,
    }

    if not oecd_data:
        return defaults

    # --- US trade growth (3-month) ---
    us_trade_growth = 0.0
    try:
        us_trade = oecd_data.get("trade", {}).get("USA", {})
        us_imports = us_trade.get("imports", []) or us_trade.get("exports", [])
        if len(us_imports) >= 4:
            recent = us_imports[-1][1]
            three_months_ago = us_imports[-4][1]
            if three_months_ago and three_months_ago != 0:
                us_trade_growth = round(
                    (recent - three_months_ago) / abs(three_months_ago) * 100, 2
                )
    except Exception as exc:
        logger.debug(f"US trade growth calc failed: {exc}")

    # --- China industrial production trend ---
    china_ip_trend = "Stable"
    try:
        cn_ip = oecd_data.get("industrial_production", {}).get("CHN", [])
        if len(cn_ip) >= 4:
            recent_avg = sum(v for _, v in cn_ip[-3:]) / 3
            earlier_avg = sum(v for _, v in cn_ip[-6:-3]) / 3
            diff = recent_avg - earlier_avg
            if diff > 0.5:
                china_ip_trend = "Expanding"
            elif diff < -0.5:
                china_ip_trend = "Contracting"
    except Exception as exc:
        logger.debug(f"China IP trend calc failed: {exc}")

    # --- EU CLI signal (DEU as proxy) ---
    eu_cli_signal = "Neutral"
    try:
        cli = oecd_data.get("cli", {})
        # Look for Germany CLI series
        deu_series: list[tuple[str, float]] = []
        for key, series in cli.items():
            if "DEU" in key:
                deu_series = series
                break
        if deu_series:
            latest_val = deu_series[-1][1]
            # OECD CLI is centred around 100; above=positive, below=negative
            if latest_val > 100.2:
                eu_cli_signal = "Positive"
            elif latest_val < 99.8:
                eu_cli_signal = "Negative"
    except Exception as exc:
        logger.debug(f"EU CLI signal calc failed: {exc}")

    # --- Asia demand score (JPN, KOR, CHN, SGP IP composite) ---
    asia_demand_score = 0.5
    try:
        ip = oecd_data.get("industrial_production", {})
        asia_countries = ["CHN", "JPN", "KOR"]
        scores = []
        for country in asia_countries:
            series = ip.get(country, [])
            if len(series) >= 4:
                recent_avg = sum(v for _, v in series[-3:]) / 3
                earlier_avg = sum(v for _, v in series[-6:-3]) / 3 if len(series) >= 6 else recent_avg
                change_pct = (recent_avg - earlier_avg) / abs(earlier_avg) * 100 if earlier_avg else 0
                # Normalise: assume ±5% maps to 0–1
                score = max(0.0, min(1.0, 0.5 + change_pct / 10.0))
                scores.append(score)
        if scores:
            asia_demand_score = round(sum(scores) / len(scores), 3)
    except Exception as exc:
        logger.debug(f"Asia demand score calc failed: {exc}")

    return {
        "us_trade_growth_3m": us_trade_growth,
        "china_ip_trend": china_ip_trend,
        "eu_cli_signal": eu_cli_signal,
        "asia_demand_score": asia_demand_score,
    }


# ---------------------------------------------------------------------------
# Test entry point
# ---------------------------------------------------------------------------


def main() -> None:
    from loguru import logger as _logger
    import json

    _logger.info("=== OECD Feed — quick test ===")
    data = fetch_oecd_indicators(cache_ttl_hours=48.0)

    if not data:
        print("OECD fetch returned empty dict — check network / endpoint availability.")
        return

    print(f"\nFetched at: {data.get('fetched_at')}")
    print(f"CLI series count : {len(data.get('cli', {}))}")
    print(f"Trade countries  : {list(data.get('trade', {}).keys())}")
    print(f"IP countries     : {list(data.get('industrial_production', {}).keys())}")

    momentum = get_global_trade_momentum(data)
    print("\n--- Trade Momentum Signal ---")
    print(json.dumps(momentum, indent=2))


if __name__ == "__main__":
    main()
