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

from utils.helpers import stable_hash

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


# ── Route-biased destination model ───────────────────────────────────────────
#
# A vessel near a port is far more likely to be heading to a destination served
# by an actual shipping lane out of that port than to a random global port.
# We derive per-origin destination weights from routes/route_registry.py and
# blend in a small uniform background so unlisted destinations remain possible.

_AVG_TRANSIT_DAYS = 16  # fallback transit when an O→D pair has no registered lane


def _build_route_dest_tables() -> tuple[dict[str, dict[str, float]], dict[tuple[str, str], int]]:
    """Build, from the route registry:

    - dest_weights: origin_locode → {dest_locode: weight} destination bias
    - transit_lookup: (origin, dest) → typical transit days

    Built once at import time. Degrades gracefully to empty tables if the
    route registry cannot be imported.
    """
    dest_weights: dict[str, dict[str, float]] = {}
    transit_lookup: dict[tuple[str, str], int] = {}
    try:
        from routes.route_registry import ROUTES
    except Exception as exc:  # pragma: no cover - registry always present in repo
        logger.debug(f"route registry unavailable for synthetic AIS bias: {exc}")
        return dest_weights, transit_lookup

    for route in ROUTES:
        o, d = route.origin_locode, route.dest_locode
        # A registered lane gets a heavy weight; busier (shorter intra-Asia and
        # the headline Trans-Pacific) lanes are weighted up via inverse-ish
        # transit so feeder lanes do not get drowned out.
        weight = 3.0 if route.transit_days <= 15 else 2.0
        dest_weights.setdefault(o, {})
        dest_weights[o][d] = dest_weights[o].get(d, 0.0) + weight
        # Keep the shortest transit if a pair appears on multiple lanes.
        prev = transit_lookup.get((o, d))
        if prev is None or route.transit_days < prev:
            transit_lookup[(o, d)] = route.transit_days

    return dest_weights, transit_lookup


_ROUTE_DEST_WEIGHTS, _ROUTE_TRANSIT = _build_route_dest_tables()


def _destination_for_port(origin: str, rng: random.Random) -> str:
    """Pick a plausible destination for a vessel near *origin*.

    Destinations on registered lanes out of the origin are heavily favoured; a
    small uniform background over the global destination pool keeps every other
    port reachable. Falls back to a uniform pick when the origin has no lanes.
    """
    lane_dests = _ROUTE_DEST_WEIGHTS.get(origin, {})
    weighted: dict[str, float] = {}

    # Registered lanes out of the origin take roughly four-fifths of the
    # probability mass; a uniform background over the global pool keeps the
    # remaining ~18% open to any other port, so off-lane destinations stay
    # plausible without drowning out the real lanes. The background is scaled
    # to the origin's total lane weight so the lane / off-lane split stays
    # stable regardless of how many lanes a given port has.
    lane_total = sum(lane_dests.values())
    if lane_total > 0:
        bg = (lane_total * 0.22) / max(1, len(_PORT_NAMES_DEST))
    else:
        bg = 1.0  # origin has no registered lanes — fall back to uniform pick
    for dest in _PORT_NAMES_DEST:
        weighted[dest] = weighted.get(dest, 0.0) + bg
    # Lane destinations from the route registry (may include ports outside the
    # default destination pool) get the dominant weight.
    for dest, w in lane_dests.items():
        weighted[dest] = weighted.get(dest, 0.0) + w

    # A vessel never lists its own port as destination.
    weighted.pop(origin, None)
    if not weighted:
        return rng.choice(_PORT_NAMES_DEST)

    dests = list(weighted.keys())
    wts = [weighted[d] for d in dests]
    return rng.choices(dests, weights=wts, k=1)[0]


def _transit_days(origin: str, dest: str) -> int:
    """Typical transit days for an origin→destination pair.

    Uses the route registry when the pair is a registered lane; otherwise
    estimates from great-circle distance between the two ports, falling back to
    a global average if either port location is unknown.
    """
    direct = _ROUTE_TRANSIT.get((origin, dest))
    if direct is not None:
        return direct
    reverse = _ROUTE_TRANSIT.get((dest, origin))
    if reverse is not None:
        return reverse

    # Estimate from distance: container ships average ~20 kts ≈ 480 nm/day.
    o_cfg = _PORT_VESSEL_CONFIG.get(origin)
    d_cfg = _PORT_VESSEL_CONFIG.get(dest)
    if o_cfg and d_cfg and "lat" in o_cfg and "lat" in d_cfg:
        dist_nm = _great_circle_nm(o_cfg["lat"], o_cfg["lon"], d_cfg["lat"], d_cfg["lon"])
        # +1.5 days of port/pilotage/approach overhead.
        return max(2, int(round(dist_nm / 480.0 + 1.5)))
    return _AVG_TRANSIT_DAYS


def _great_circle_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles between two lat/lon points."""
    r_nm = 3440.065  # Earth radius in nautical miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * r_nm * math.asin(min(1.0, math.sqrt(a)))

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

# Monthly container-shipping seasonal curve (mirrors data/ais_feed.py so the
# synthetic vessel feed and the synthetic congestion feed tell the same story).
_SEASONAL_MONTH = {
    1: 0.85, 2: 0.75, 3: 0.95, 4: 1.00, 5: 1.05, 6: 1.10,
    7: 1.15, 8: 1.20, 9: 1.25, 10: 1.15, 11: 1.05, 12: 0.90,
}


def _port_congestion(locode: str) -> float:
    """Estimate a [0,1] congestion level for a port for the synthetic feed.

    Combines the port's relative size (its configured vessel-count range vs.
    the busiest port) with the current month's seasonal multiplier. Used only
    to make the synthetic anchored-vessel fraction congestion-aware — busier,
    in-season ports show more vessels waiting at anchor.
    """
    cfg = _PORT_VESSEL_CONFIG.get(locode)
    if not cfg:
        size = 0.4  # unknown port: assume mid-small
    else:
        lo, hi = cfg["count"]
        mid = (lo + hi) / 2.0
        # Normalise against the busiest configured port (Shanghai ≈ 23).
        size = min(1.0, mid / 23.0)

    seasonal = _SEASONAL_MONTH.get(datetime.now(timezone.utc).month, 1.0)
    # Seasonal runs 0.75–1.25; recentre so an average month is neutral.
    level = size * (0.7 + 0.5 * (seasonal - 0.75))
    return max(0.0, min(1.0, level))


def _synth_vessel(
    mmsi_base: int,
    idx: int,
    lat_c: float,
    lon_c: float,
    spread: float,
    origin: str = "",
    congestion: float = 0.4,
) -> dict:
    """Generate a single plausible synthetic vessel dict.

    Parameters
    ----------
    origin : str
        LOCODE of the port this vessel is near. Used to bias the destination
        toward ports actually served by lanes out of *origin*.
    congestion : float
        [0,1] congestion level of the origin port. Raises the probability the
        vessel is anchored (waiting for a berth).
    """
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

    # Route-biased destination: prefer ports on real lanes out of the origin.
    dest = _destination_for_port(origin, rng) if origin else rng.choice(_PORT_NAMES_DEST)

    # Congestion-aware anchored fraction: a base ~18% rises toward ~48% as the
    # origin port gets more congested (more ships waiting for a berth).
    anchored_prob = 0.18 + 0.30 * congestion
    anchored = rng.random() < anchored_prob
    if anchored:
        speed = round(rng.uniform(0.0, 1.0), 1)

    heading = rng.randint(0, 359)
    lat = lat_c + rng.uniform(-spread, spread)
    lon = lon_c + rng.uniform(-spread, spread)

    # ETA clustering: cluster around the realistic transit time for this
    # origin→destination pair rather than a flat 12–240h spread. A vessel still
    # at the origin has most of the voyage ahead; one already under way is
    # somewhere along it — model that as a fraction of the full transit.
    transit_h = _transit_days(origin, dest) * 24.0 if origin else _AVG_TRANSIT_DAYS * 24.0
    progress = rng.random()  # 0 = just departed, 1 = nearly arrived
    remaining = transit_h * (1.0 - progress)
    # Small ±15% scheduling noise (weather, speed, port slot timing).
    remaining *= rng.uniform(0.85, 1.15)
    eta_hours = int(max(6, min(360, round(remaining))))
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
    rng = random.Random(int(now // _SYNTH_TTL_SECS) ^ stable_hash(locode))
    count = rng.randint(*cfg["count"])
    spread = cfg.get("spread", 0.4)

    # Origin port congestion drives the anchored-vessel fraction. Only a real
    # port LOCODE has a meaningful congestion level; route/coordinate keys fall
    # back to the neutral default inside _port_congestion.
    congestion = _port_congestion(locode) if locode in _PORT_VESSEL_CONFIG else 0.4

    # Generate a MMSI base that is deterministic and varied per port — stable
    # across processes so the "same vessel at this port" stays the same MMSI.
    mmsi_base = (stable_hash(locode) % 900_000_000) + 100_000_000

    vessels = [
        _synth_vessel(mmsi_base, i, lat, lon, spread, origin=locode, congestion=congestion)
        for i in range(count)
    ]
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
