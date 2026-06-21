"""
Vessel traffic / port congestion via IMF PortWatch (no API key required).

Falls back to smart synthetic congestion using BDI, freight rates, and
known port baselines when PortWatch data is unavailable.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from data.cache_manager import CacheManager
from data.normalizer import normalize_ais_df
from ports.port_registry import PORTS, PORTS_BY_LOCODE
from utils.helpers import stable_hash

# IMF PortWatch public API — no key required
_PORTWATCH_BASE = "https://portwatch.imf.org/api/v1"

# Known realistic cargo vessel baselines per port (avg vessels in bbox at any time)
# Based on 2023-2024 AIS data averages
_PORT_VESSEL_BASELINES: dict[str, int] = {
    "CNSHA": 180,   # Shanghai — world's busiest
    "CNNBO": 120,   # Ningbo-Zhoushan
    "SGSIN":  95,   # Singapore
    "CNSZN":  85,   # Shenzhen
    "USLAX":  60,   # Los Angeles
    "USLGB":  55,   # Long Beach
    "NLRTM":  70,   # Rotterdam
    "BEANR":  50,   # Antwerp
    "DEHAM":  55,   # Hamburg
    "HKHKG":  75,   # Hong Kong
    "KRPUS":  90,   # Busan
    "JPYOK":  45,   # Yokohama
    "AEJEA":  65,   # Jebel Ali
    "MYPKG":  50,   # Port Klang
    "MYTPP":  40,   # Tanjung Pelepas
    "TWKHH":  45,   # Kaohsiung
    "CNTAO":  80,   # Qingdao
    "CNTXG":  60,   # Tianjin
    "GRPIR":  35,   # Piraeus
    "LKCMB":  30,   # Colombo
    "MATNM":  25,   # Tanger Med
    "USSAV":  30,   # Savannah
    "USNYC":  40,   # New York/NJ
    "GBFXT":  30,   # Felixstowe
    "BRSAO":  35,   # Santos
}

# Seasonal multipliers by month (container shipping peaks)
_SEASONAL = {
    1: 0.85, 2: 0.75, 3: 0.95,  # Jan-Mar: post-CNY slowdown
    4: 1.00, 5: 1.05, 6: 1.10,  # Apr-Jun: spring build
    7: 1.15, 8: 1.20, 9: 1.25,  # Jul-Sep: peak season
    10: 1.15, 11: 1.05, 12: 0.90,  # Oct-Dec: wind-down
}

# Per-port seasonal sensitivity: how strongly a port's congestion tracks the
# global container-shipping season. Trans-Pacific export hubs swing hard with
# US peak-season demand; Middle East / transshipment hubs are far steadier.
# Multiplier blends toward 1.0 (no seasonality) as sensitivity → 0.
_PORT_SEASONAL_SENSITIVITY: dict[str, float] = {
    "CNSHA": 1.15, "CNNBO": 1.15, "CNSZN": 1.15, "CNTAO": 1.10,  # China export hubs
    "CNTXG": 1.05, "HKHKG": 1.00, "KRPUS": 0.95, "JPYOK": 0.85,
    "USLAX": 1.20, "USLGB": 1.20, "USNYC": 1.00, "USSAV": 1.05,  # US import gateways
    "NLRTM": 0.80, "BEANR": 0.80, "DEHAM": 0.80, "GBFXT": 0.80,  # N Europe range
    "SGSIN": 0.55, "MYPKG": 0.60, "MYTPP": 0.60, "LKCMB": 0.55,  # transshipment hubs
    "AEJEA": 0.50, "GRPIR": 0.65, "MATNM": 0.60, "TWKHH": 0.85,
    "BRSAO": 0.70,
}


@st.cache_data(ttl=21600, hash_funcs={CacheManager: lambda _: None})
def fetch_vessel_counts(
    cache: CacheManager | None = None,
    ttl_hours: float = 6.0,
) -> dict[str, pd.DataFrame]:
    """Fetch vessel counts for all tracked ports.

    Tries IMF PortWatch first, then falls back to smart synthetic estimates
    calibrated with real seasonal and macro signals.

    Returns:
        dict mapping port_locode → DataFrame with AIS columns.
    """
    cache = cache or CacheManager()
    results: dict[str, pd.DataFrame] = {}

    # Try PortWatch first (single call covers all ports)
    key = "portwatch_all"
    pw_data = cache.get_or_fetch(
        key=key,
        fetch_fn=_fetch_portwatch_all,
        ttl_hours=ttl_hours,
        source="ais",
    )

    n_synth = 0
    if pw_data is not None and not pw_data.empty:
        for port in PORTS:
            port_rows = pw_data[pw_data["port_locode"] == port.locode]
            if not port_rows.empty:
                results[port.locode] = port_rows
                continue
            results[port.locode] = _synthetic_congestion(port.locode)
            n_synth += 1
    else:
        logger.info("IMF PortWatch unavailable — using calibrated synthetic vessel counts")
        for port in PORTS:
            results[port.locode] = _synthetic_congestion(port.locode)
            n_synth += 1

    # Provenance (R003/R097): the cache choke point only ever sees the REAL
    # PortWatch fetch, so synthetic substitution would otherwise be invisible to
    # the ledger. Stamp it here where the fallback actually happens.
    if n_synth:
        _stamp_synthetic("ais", "vessel_counts", n_synth)

    logger.info(f"Vessel data loaded for {len(results)} ports")
    return results


def _stamp_synthetic(source: str, key: str, row_count: int) -> None:
    """Best-effort 'synthetic' provenance stamp (R003/R097). Never raises."""
    try:
        from state.fetch_ledger import record_fetch
        record_fetch(source, key, "synthetic", row_count=int(row_count),
                     quality="DEMO")
    except Exception:  # pragma: no cover - defensive
        pass


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=3, max=10))
def _fetch_portwatch_all() -> pd.DataFrame:
    """Try to fetch port call data from IMF PortWatch."""
    rows = []

    # Try PortWatch API endpoints
    endpoints = [
        f"{_PORTWATCH_BASE}/portcalls",
        f"{_PORTWATCH_BASE}/port-statistics",
        "https://portwatch.imf.org/api/portcalls",
    ]

    for url in endpoints:
        try:
            resp = requests.get(url, timeout=15, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                continue
            data = resp.json()

            # Parse PortWatch response format
            records = data if isinstance(data, list) else data.get("data", data.get("features", []))

            for rec in records:
                if isinstance(rec, dict) and rec.get("geometry"):
                    # GeoJSON feature format
                    props = rec.get("properties", {})
                    rec = props

                port_id = rec.get("portid", rec.get("port_id", rec.get("locode", "")))
                vessel_count = rec.get("portcalls", rec.get("vessel_count", rec.get("calls", 0)))

                if not port_id or not vessel_count:
                    continue

                # Match to our port LOCODEs
                locode = str(port_id).upper()
                if locode not in PORTS_BY_LOCODE:
                    continue

                now = datetime.now(timezone.utc).replace(tzinfo=None)
                rows.append({
                    "date": now,
                    "port_locode": locode,
                    "vessel_count": int(vessel_count),
                    "vessel_type": "cargo",
                    "source": "portwatch",
                })

            if rows:
                logger.info(f"IMF PortWatch: {len(rows)} port records loaded")
                df = pd.DataFrame(rows)
                return normalize_ais_df(df)

        except Exception as exc:
            logger.debug(f"PortWatch endpoint {url}: {exc}")
            continue

    return pd.DataFrame()


def _interp_seasonal(month: float) -> float:
    """Linearly interpolate the monthly seasonal multiplier across a fractional
    month so congestion drifts smoothly day-to-day instead of stepping on the
    1st of each month."""
    lo = int(month)
    frac = month - lo
    m0 = ((lo - 1) % 12) + 1
    m1 = (lo % 12) + 1
    return _SEASONAL[m0] * (1.0 - frac) + _SEASONAL[m1] * frac


def _synthetic_congestion(port_locode: str) -> pd.DataFrame:
    """Generate realistic synthetic vessel count using baselines + seasonal adjustment.

    Uses:
    - Known 2024 baseline vessel counts per port
    - Smoothly interpolated monthly seasonal curve (peak season Jul-Sep)
    - Port-specific seasonal sensitivity (export hubs swing harder than
      transshipment hubs)
    - Smooth deterministic daily variation (a slow drift + a small daily
      wobble) instead of week-boundary steps
    """
    import math

    baseline = _PORT_VESSEL_BASELINES.get(port_locode, 40)
    now_local = datetime.now()

    # Fractional month so the seasonal curve is continuous across month ends.
    days_in_month = 30.4
    frac_month = now_local.month + (now_local.day - 1) / days_in_month
    seasonal_raw = _interp_seasonal(frac_month)

    # Blend the global seasonal swing toward 1.0 by the port's sensitivity:
    # sensitivity 1.0 → full swing, 0.0 → flat year-round.
    sensitivity = _PORT_SEASONAL_SENSITIVITY.get(port_locode, 0.85)
    seasonal = 1.0 + (seasonal_raw - 1.0) * sensitivity

    # Smooth daily variation: a slow multi-day drift plus two gentler wobbles
    # at shorter periods for texture. All three are continuous functions of
    # day-of-year, so consecutive days differ by only a few percent — no jumps
    # on week boundaries, and the smoothness bound holds for any port phase.
    doy = now_local.timetuple().tm_yday
    port_phase = (stable_hash(port_locode) % 1000) / 1000.0 * 2.0 * math.pi
    slow_drift = 0.050 * math.sin(2.0 * math.pi * doy / 23.0 + port_phase)
    daily_wobble = 0.020 * math.sin(2.0 * math.pi * doy / 7.0 + port_phase * 1.7)
    fast_wobble = 0.010 * math.sin(2.0 * math.pi * doy / 4.0 + port_phase * 2.3)

    variation = 1.0 + slow_drift + daily_wobble + fast_wobble

    vessel_count = max(1, int(round(baseline * seasonal * variation)))
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    df = pd.DataFrame([{
        "date": now,
        "port_locode": port_locode,
        "vessel_count": vessel_count,
        "vessel_type": "cargo",
        "source": "synthetic_baseline",
    }])
    return normalize_ais_df(df)


def get_vessel_count(
    port_locode: str,
    ais_data: dict[str, pd.DataFrame],
    vessel_type: str = "cargo",
) -> int:
    """Return the most recent vessel count for a port."""
    df = ais_data.get(port_locode)
    if df is None or df.empty:
        return _PORT_VESSEL_BASELINES.get(port_locode, 40)
    filtered = df[df["vessel_type"] == vessel_type] if "vessel_type" in df.columns else df
    if filtered.empty:
        return _PORT_VESSEL_BASELINES.get(port_locode, 40)
    return int(filtered["vessel_count"].iloc[-1])


def compute_congestion_index(
    port_locode: str,
    ais_data: dict[str, pd.DataFrame],
    baseline_counts: dict[str, float] | None = None,
) -> float:
    """Compute a [0,1] congestion index for a port using z-score normalization."""
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
        return 0.5

    mean_count = sum(all_counts) / len(all_counts)
    variance = sum((c - mean_count) ** 2 for c in all_counts) / len(all_counts)
    std = variance ** 0.5

    if std == 0:
        return 0.5

    z_score = (current_count - mean_count) / std
    return sigmoid(z_score)
