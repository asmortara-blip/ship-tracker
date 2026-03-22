"""
AISstream.io live vessel tracking — free WebSocket/REST API.

Primary source: AISstream.io REST-compatible endpoint (requires free API key).
Fallback: realistic synthetic vessel data for all configured ports.

Usage
-----
    from data.aisstream_feed import fetch_vessels_near_port, aisstream_available
    vessels = fetch_vessels_near_port(lat=33.74, lon=-118.27, radius_nm=50)
"""
from __future__ import annotations

import math
import os
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
import streamlit as st
from loguru import logger

# ── API key helper ────────────────────────────────────────────────────────────

def _get_aisstream_key() -> str:
    try:
        return st.secrets.get("AISSTREAM_KEY", "")
    except Exception:
        return os.environ.get("AISSTREAM_KEY", "")


def aisstream_available() -> bool:
    """Return True if an AISstream API key is configured."""
    return bool(_get_aisstream_key())


# ── Vessel type labels (AIS type codes → human-readable) ─────────────────────

VESSEL_TYPE_LABELS: dict[int, str] = {
    70: "Cargo",
    71: "Container",
    72: "Tanker",
    74: "Bulk Carrier",
    79: "Cargo",
    80: "Tanker",
    81: "Tanker",
    82: "Tanker",
    83: "Tanker",
    84: "Tanker",
    89: "LNG",
    60: "Passenger",
    61: "Passenger",
    30: "Fishing",
    36: "Sailing",
    37: "Pleasure",
    90: "Other",
    0:  "Unknown",
}

_VESSEL_COLORS: dict[str, str] = {
    "Container":  "#3b82f6",
    "Cargo":      "#6366f1",
    "Bulk Carrier": "#f97316",
    "Tanker":     "#ef4444",
    "LNG":        "#f59e0b",
    "Passenger":  "#10b981",
    "Fishing":    "#8b5cf6",
    "Other":      "#64748b",
    "Unknown":    "#475569",
}

# ── Bounding box helper ───────────────────────────────────────────────────────

_NM_PER_DEGREE = 60.0  # 1 nautical mile ≈ 1/60 degree latitude


def _bbox_from_center(lat: float, lon: float, radius_nm: float) -> tuple[float, float, float, float]:
    """Return (lat_min, lon_min, lat_max, lon_max) for a given centre and radius."""
    delta_lat = radius_nm / _NM_PER_DEGREE
    # Longitude degrees per nm shrinks with cos(latitude)
    cos_lat = math.cos(math.radians(lat)) or 1e-9
    delta_lon = radius_nm / (_NM_PER_DEGREE * cos_lat)
    return (
        lat - delta_lat,
        lon - delta_lon,
        lat + delta_lat,
        lon + delta_lon,
    )


# ── Synthetic vessel data ─────────────────────────────────────────────────────

# Realistic vessel name pools
_CONTAINER_NAMES = [
    "MSC GÜLSÜN", "EVER GIVEN", "CMA CGM MARCO POLO", "COSCO SHIPPING UNIVERSE",
    "HMM ALGECIRAS", "MAERSK ESSEN", "OOCL HONG KONG", "MOL TRIUMPH",
    "MSC ZÖE", "EVER ACE", "CMA CGM TROCADERO", "ONE INNOVATION",
    "YANG MING WISH", "HAPAG LLOYD BARCELONA", "MSC OSCAR", "MAERSK HARTFORD",
    "COSCO SHIPPING TAURUS", "EVER GENIUS", "CMA CGM BOUGAINVILLE", "MSC IRINA",
]

_BULK_NAMES = [
    "BULK VIKING", "STELLAR JAZZ", "GOLDEN STAR", "PACIFIC COURAGE",
    "ATLANTIC BREEZE", "IRON WARRIOR", "ORE BRASIL", "COAL HUNTER",
    "GRAIN MASTER", "CAPE TIGER",
]

_TANKER_NAMES = [
    "EURONAV SUEZMAX", "FRONTLINE NAVIGATOR", "NORDIC AQUARIUS", "SCF ARCTIC",
    "GULF SPIRIT", "OCEAN DESTINY", "SEAWAYS MARITIME", "AEGEAN LEGEND",
]

_FLAGS = ["Panama", "Liberia", "Marshall Islands", "Bahamas", "Cyprus", "Malta",
          "Greece", "Singapore", "Hong Kong", "China", "South Korea", "Japan",
          "Norway", "Denmark", "USA"]

_PORT_NAMES_DEST = [
    "USLAX", "CNSHA", "NLRTM", "SGSIN", "DEHAM", "KRPUS",
    "JPYOK", "HKHKG", "BEANR", "AEJEA", "USNYC", "LKCMB",
]

# Per-port realistic vessel pools (baselines scaled by port size)
_PORT_VESSEL_CONFIG: dict[str, dict] = {
    "USLAX": {"count": (10, 16), "lat": 33.74, "lon": -118.27, "spread": 0.6},
    "CNSHA": {"count": (18, 28), "lat": 31.23, "lon": 121.47, "spread": 0.8},
    "NLRTM": {"count": (12, 18), "lat": 51.92, "lon": 4.48,  "spread": 0.5},
    "SGSIN": {"count": (15, 22), "lat": 1.29,  "lon": 103.85, "spread": 0.5},
    "DEHAM": {"count": (8,  14), "lat": 53.55, "lon": 9.99,  "spread": 0.4},
    "KRPUS": {"count": (10, 16), "lat": 35.10, "lon": 129.04, "spread": 0.5},
    "JPYOK": {"count": (6,  12), "lat": 35.44, "lon": 139.64, "spread": 0.4},
    "HKHKG": {"count": (10, 15), "lat": 22.29, "lon": 114.18, "spread": 0.4},
    "BEANR": {"count": (7,  12), "lat": 51.26, "lon": 4.40,  "spread": 0.4},
    "AEJEA": {"count": (8,  14), "lat": 24.99, "lon": 55.06, "spread": 0.5},
    "USNYC": {"count": (8,  13), "lat": 40.66, "lon": -74.04, "spread": 0.4},
    "CNNBO": {"count": (12, 18), "lat": 29.87, "lon": 121.55, "spread": 0.5},
    "CNSZN": {"count": (10, 15), "lat": 22.54, "lon": 113.94, "spread": 0.4},
    "CNTAO": {"count": (8,  14), "lat": 36.07, "lon": 120.33, "spread": 0.4},
    "CNTXG": {"count": (6,  12), "lat": 38.99, "lon": 117.72, "spread": 0.4},
    "MYPKG": {"count": (7,  12), "lat": 3.00,  "lon": 101.39, "spread": 0.4},
    "MYTPP": {"count": (6,  11), "lat": 1.36,  "lon": 103.55, "spread": 0.3},
    "TWKHH": {"count": (6,  11), "lat": 22.61, "lon": 120.29, "spread": 0.4},
    "USLGB": {"count": (9,  15), "lat": 33.76, "lon": -118.19, "spread": 0.5},
    "MATNM": {"count": (5,  10), "lat": 35.89, "lon": -5.50, "spread": 0.3},
    "LKCMB": {"count": (5,  10), "lat": 6.93,  "lon": 79.84, "spread": 0.3},
    "GRPIR": {"count": (5,  10), "lat": 37.94, "lon": 23.64, "spread": 0.3},
    "USSAV": {"count": (5,  10), "lat": 32.08, "lon": -81.10, "spread": 0.3},
    "GBFXT": {"count": (5,  10), "lat": 51.96, "lon": 1.35,  "spread": 0.3},
    "BRSAO": {"count": (5,  10), "lat": -23.95, "lon": -46.33, "spread": 0.3},
}

# Deterministic seed per port so data is stable within a session
_SYNTH_CACHE: dict[str, tuple[float, list[dict]]] = {}
_SYNTH_TTL_SECS = 1800  # 30-minute synthetic cache


def _synth_vessel(mmsi_base: int, idx: int, lat_c: float, lon_c: float, spread: float) -> dict:
    """Generate a single plausible synthetic vessel dict."""
    rng = random.Random(mmsi_base + idx * 7919)

    v_type_code = rng.choice([70, 71, 74, 80, 89, 60, 30, 79])
    v_type = VESSEL_TYPE_LABELS.get(v_type_code, "Unknown")

    if v_type in ("Container",):
        name = rng.choice(_CONTAINER_NAMES)
        length = rng.randint(280, 400)
        speed = round(rng.uniform(14.0, 22.0), 1)
    elif v_type in ("Bulk Carrier",):
        name = rng.choice(_BULK_NAMES)
        length = rng.randint(200, 320)
        speed = round(rng.uniform(10.0, 15.0), 1)
    elif v_type in ("Tanker", "LNG"):
        name = rng.choice(_TANKER_NAMES)
        length = rng.randint(240, 340)
        speed = round(rng.uniform(12.0, 17.0), 1)
    elif v_type == "Cargo":
        name = rng.choice(_CONTAINER_NAMES + _BULK_NAMES)
        length = rng.randint(150, 260)
        speed = round(rng.uniform(11.0, 16.0), 1)
    else:
        name = f"VESSEL {mmsi_base + idx}"
        length = rng.randint(80, 200)
        speed = round(rng.uniform(6.0, 14.0), 1)

    # Vessels near port may be anchored/slow
    anchored = rng.random() < 0.25
    if anchored:
        speed = round(rng.uniform(0.0, 1.0), 1)

    heading = rng.randint(0, 359)
    lat = lat_c + rng.uniform(-spread, spread)
    lon = lon_c + rng.uniform(-spread, spread)

    dest = rng.choice(_PORT_NAMES_DEST)
    eta_hours = rng.randint(12, 240)
    eta_dt = datetime.now(timezone.utc) + timedelta(hours=eta_hours)

    return {
        "mmsi": str(mmsi_base + idx),
        "name": name,
        "vessel_type": v_type,
        "vessel_type_code": v_type_code,
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "speed_kts": speed,
        "heading": heading,
        "destination": dest,
        "eta": eta_dt.strftime("%Y-%m-%d %H:%M UTC"),
        "length_m": length,
        "flag": rng.choice(_FLAGS),
        "last_updated": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        "color": _VESSEL_COLORS.get(v_type, "#64748b"),
        "source": "synthetic",
    }


def _synthetic_vessels_for_port(locode: str, lat: float, lon: float) -> list[dict]:
    """Return a stable list of synthetic vessels for a port (cached 30 min)."""
    now = time.time()
    cached = _SYNTH_CACHE.get(locode)
    if cached and (now - cached[0]) < _SYNTH_TTL_SECS:
        return cached[1]

    cfg = _PORT_VESSEL_CONFIG.get(locode, {"count": (5, 10), "spread": 0.4})
    rng = random.Random(int(now // _SYNTH_TTL_SECS) ^ hash(locode))
    count = rng.randint(*cfg["count"])
    spread = cfg.get("spread", 0.4)

    # Generate a MMSI base that is deterministic but varied per port
    mmsi_base = (abs(hash(locode)) % 900_000_000) + 100_000_000

    vessels = [_synth_vessel(mmsi_base, i, lat, lon, spread) for i in range(count)]
    _SYNTH_CACHE[locode] = (now, vessels)
    return vessels


# ── AISstream REST fetch ──────────────────────────────────────────────────────

_AISSTREAM_BASE = "https://api.aisstream.io/v0"
_REQUEST_TIMEOUT = 15


def _parse_aisstream_vessel(raw: dict) -> dict:
    """Normalise a single raw AISstream vessel record into our standard schema."""
    try:
        pos = raw.get("Position", {})
        meta = raw.get("ShipStaticAndVoyageRelatedData", {})
        nav = raw.get("PositionReport", {})

        lat = pos.get("Latitude") or nav.get("Latitude") or 0.0
        lon = pos.get("Longitude") or nav.get("Longitude") or 0.0
        speed = nav.get("SpeedOverGround") or 0.0
        heading = nav.get("TrueHeading") or nav.get("CourseOverGround") or 0

        type_code = int(meta.get("TypeOfShipAndCargoType") or raw.get("ShipType") or 0)
        v_type = VESSEL_TYPE_LABELS.get(type_code, "Other")

        return {
            "mmsi": str(raw.get("Mmsi", "")),
            "name": (meta.get("ShipName") or raw.get("ShipName") or "UNKNOWN").strip(),
            "vessel_type": v_type,
            "vessel_type_code": type_code,
            "lat": round(float(lat), 5),
            "lon": round(float(lon), 5),
            "speed_kts": round(float(speed), 1),
            "heading": int(heading) % 360,
            "destination": (meta.get("Destination") or "").strip(),
            "eta": str(meta.get("Eta") or ""),
            "length_m": int(meta.get("DimensionToBow", 0) or 0) + int(meta.get("DimensionToStern", 0) or 0),
            "flag": "",
            "last_updated": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            "color": _VESSEL_COLORS.get(v_type, "#64748b"),
            "source": "aisstream",
        }
    except Exception as exc:
        logger.debug(f"AISstream parse error: {exc} — raw keys: {list(raw.keys())[:8]}")
        return {}


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_vessels_near_port(
    lat: float,
    lon: float,
    radius_nm: float = 50,
    locode: str = "",
) -> list[dict]:
    """
    Fetch vessels within *radius_nm* nautical miles of (lat, lon).

    Uses AISstream bounding-box REST endpoint when a key is available,
    otherwise returns realistic synthetic data.

    Returns a list of vessel dicts with keys:
        mmsi, name, vessel_type, lat, lon, speed_kts, heading,
        destination, eta, length_m, flag, last_updated
    """
    key = _get_aisstream_key()

    if key:
        lat1, lon1, lat2, lon2 = _bbox_from_center(lat, lon, radius_nm)
        url = f"{_AISSTREAM_BASE}/messages/vessels"
        params = {"boundingBox": f"{lat1},{lon1},{lat2},{lon2}"}
        headers = {"Authorization": key}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            raw_list = resp.json()
            if isinstance(raw_list, list):
                vessels = [_parse_aisstream_vessel(v) for v in raw_list]
                vessels = [v for v in vessels if v and v.get("mmsi")]
                if vessels:
                    logger.info(f"AISstream: {len(vessels)} vessels near ({lat:.2f},{lon:.2f})")
                    return vessels
        except requests.HTTPError as exc:
            logger.warning(f"AISstream HTTP {exc.response.status_code} — falling back to synthetic")
        except Exception as exc:
            logger.warning(f"AISstream fetch error: {exc} — falling back to synthetic")

    # Fallback: synthetic
    return _synthetic_vessels_for_port(locode or f"{lat:.1f},{lon:.1f}", lat, lon)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_vessels_on_route(
    route_id: str,
    waypoints: list[tuple],  # [(lat, lon), ...]
    radius_nm: float = 25,
) -> list[dict]:
    """
    Fetch vessels along a route corridor defined by waypoints.

    Samples up to 4 waypoints and unions the bounding boxes.
    Falls back to synthetic data if AISstream is unavailable.
    """
    if not waypoints:
        return []

    # Sample evenly spaced waypoints (cap at 4 to limit requests)
    step = max(1, len(waypoints) // 4)
    sample_wps = waypoints[::step][:4]

    all_vessels: dict[str, dict] = {}
    key = _get_aisstream_key()

    if key:
        for (wp_lat, wp_lon) in sample_wps:
            try:
                segment_vessels = fetch_vessels_near_port(wp_lat, wp_lon, radius_nm)
                for v in segment_vessels:
                    if v.get("mmsi"):
                        all_vessels[v["mmsi"]] = v
            except Exception as exc:
                logger.debug(f"Route segment fetch error: {exc}")

        if all_vessels:
            logger.info(f"Route {route_id}: {len(all_vessels)} unique vessels")
            return list(all_vessels.values())

    # Fallback: generate synthetic corridor vessels
    center_lat = sum(wp[0] for wp in waypoints) / len(waypoints)
    center_lon = sum(wp[1] for wp in waypoints) / len(waypoints)
    return _synthetic_vessels_for_port(f"route_{route_id}", center_lat, center_lon)


# ── All-ports convenience loader ──────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_all_port_vessels(ports_cfg: list[dict]) -> dict[str, list[dict]]:
    """
    Fetch vessel lists for all configured ports in one call.

    Parameters
    ----------
    ports_cfg : list of port dicts from config.yaml (must have locode, lat, lon)

    Returns
    -------
    dict keyed by locode → list[vessel dict]
    """
    result: dict[str, list[dict]] = {}
    for port in ports_cfg:
        locode = port.get("locode", "")
        lat = port.get("lat", 0.0)
        lon = port.get("lon", 0.0)
        if not locode:
            continue
        try:
            result[locode] = fetch_vessels_near_port(lat, lon, radius_nm=50, locode=locode)
        except Exception as exc:
            logger.warning(f"fetch_all_port_vessels({locode}): {exc}")
            result[locode] = []
    return result


# ── Helper: colour lookup ─────────────────────────────────────────────────────

def vessel_color(vessel_type: str) -> str:
    """Return the hex colour for a vessel type label."""
    return _VESSEL_COLORS.get(vessel_type, "#64748b")
