from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from data.cache_manager import CacheManager
from data.normalizer import normalize_ais_df
from ports.port_registry import PORTS, PORTS_BY_LOCODE


def fetch_vessel_counts(
    cache: CacheManager | None = None,
    ttl_hours: float = 6.0,
) -> dict[str, pd.DataFrame]:
    """Fetch AIS vessel counts for all tracked port bounding boxes.

    Returns:
        dict mapping port_locode → DataFrame with AIS_COLS columns.
        Falls back to empty DataFrames if credentials not set.
    """
    username = os.getenv("AISHUB_USERNAME", "")
    password = os.getenv("AISHUB_PASSWORD", "")

    if not username or not password:
        logger.warning("AISHUB_USERNAME/PASSWORD not set — returning empty AIS data")
        return {}

    cache = cache or CacheManager()
    results: dict[str, pd.DataFrame] = {}

    for port in PORTS:
        key = f"ais_{port.locode}"
        df = cache.get_or_fetch(
            key=key,
            fetch_fn=lambda p=port, u=username, pw=password: _fetch_port_vessels(p, u, pw),
            ttl_hours=ttl_hours,
            source="ais",
        )
        if df is not None and not df.empty:
            results[port.locode] = df
        else:
            # Return a synthetic single-row DataFrame with 0 count so downstream
            # scoring can still run with a neutral signal
            results[port.locode] = _synthetic_empty(port.locode)

    logger.info(f"AIS data loaded for {len(results)} ports")
    return results


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=3, max=15))
def _fetch_port_vessels(port, username: str, password: str) -> pd.DataFrame:
    """Fetch vessels within a port's bounding box from AISHub."""
    bbox = port.bbox
    url = "http://data.aishub.net/ws.php"
    params = {
        "username": username,
        "format": "1",
        "output": "json",
        "compress": "0",
        "latmin": bbox["latmin"],
        "latmax": bbox["latmax"],
        "lonmin": bbox["lonmin"],
        "lonmax": bbox["lonmax"],
    }

    logger.debug(f"AISHub fetch: {port.locode} bbox={bbox}")

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error(f"AISHub fetch failed for {port.locode}: {exc}")
        return pd.DataFrame()

    # AISHub returns [header_dict, [vessel_list]] or just a list
    vessels = []
    if isinstance(data, list) and len(data) >= 2:
        vessels = data[1] if isinstance(data[1], list) else []
    elif isinstance(data, list):
        vessels = [v for v in data if isinstance(v, dict) and "MMSI" in v]

    # Filter to cargo vessel types (AIS type codes 70-79)
    cargo_vessels = [
        v for v in vessels
        if isinstance(v, dict) and 70 <= int(v.get("TYPE", 0)) <= 79
    ]

    vessel_count = len(cargo_vessels)
    total_count = len(vessels)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    df = pd.DataFrame([{
        "date": now,
        "port_locode": port.locode,
        "vessel_count": vessel_count,
        "vessel_type": "cargo",
        "source": "aishub",
    }, {
        "date": now,
        "port_locode": port.locode,
        "vessel_count": total_count,
        "vessel_type": "all",
        "source": "aishub",
    }])

    result = normalize_ais_df(df)
    logger.debug(f"  AISHub {port.locode}: {vessel_count} cargo vessels ({total_count} total)")
    return result


def _synthetic_empty(port_locode: str) -> pd.DataFrame:
    """Create a neutral placeholder row when AIS data is unavailable."""
    return pd.DataFrame([{
        "date": datetime.now(timezone.utc).replace(tzinfo=None),
        "port_locode": port_locode,
        "vessel_count": 0,
        "vessel_type": "cargo",
        "source": "unavailable",
    }])


def get_vessel_count(
    port_locode: str,
    ais_data: dict[str, pd.DataFrame],
    vessel_type: str = "cargo",
) -> int:
    """Return the most recent vessel count for a port."""
    df = ais_data.get(port_locode)
    if df is None or df.empty:
        return 0
    filtered = df[df["vessel_type"] == vessel_type]
    if filtered.empty:
        return 0
    return int(filtered["vessel_count"].iloc[-1])


def compute_congestion_index(
    port_locode: str,
    ais_data: dict[str, pd.DataFrame],
    baseline_counts: dict[str, float] | None = None,
) -> float:
    """Compute a [0,1] congestion index for a port using z-score normalization.

    If no baseline provided, uses the global mean across all tracked ports.
    """
    from utils.helpers import sigmoid

    current_count = get_vessel_count(port_locode, ais_data, "cargo")

    if baseline_counts:
        all_counts = list(baseline_counts.values())
    else:
        all_counts = [
            get_vessel_count(locode, ais_data, "cargo")
            for locode in ais_data.keys()
        ]

    if not all_counts or all(c == 0 for c in all_counts):
        return 0.5  # neutral

    mean_count = sum(all_counts) / len(all_counts)
    variance = sum((c - mean_count) ** 2 for c in all_counts) / len(all_counts)
    std = variance ** 0.5

    if std == 0:
        return 0.5

    z_score = (current_count - mean_count) / std
    return sigmoid(z_score)
