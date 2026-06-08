"""
data/marine_weather_feed.py
───────────────────────────
Live marine-weather conditions at a route's maritime-chokepoint waypoints via
the **free, KEYLESS** Open-Meteo APIs (rec R032). This turns the #2 SSI weight
(weather, 0.16) — today a 100%-hardcoded almanac in ``processing.weather_risk``
— into an OBSERVED signal *when the feed answers*, while staying byte-for-byte
unchanged offline (the blend is opt-in; see ``processing.weather_risk``).

Endpoints (both keyless, no token, no sign-up):
  * Marine   — https://marine-api.open-meteo.com/v1/marine
               current=wave_height,wind_wave_height,swell_wave_height  (metres)
  * Forecast — https://api.open-meteo.com/v1/forecast
               current=wind_speed_10m,wind_gusts_10m                   (km/h)

Design (mirrors ``data/gdelt_feed.py`` + ``data/canal_feed.py``):
  * OFFLINE-SAFE: an injectable ``http_get`` (defaults ``requests.get``); ANY
    network/parse error → ``None`` (never raises, never blocks). Tests pass a
    stub getter or a raising stub and run fully network-free.
  * CACHE-BACKED: a small TTL JSON file cache under ``cache/marine/``.
  * HONEST PROVENANCE: a successful real fetch is stamped ``is_real=True``; a
    dark/failed fetch returns ``None`` (the caller falls back to the modeled
    almanac and labels it modeled).

NOTE: an inland-canal point (e.g. the Suez Canal centroid) has no marine grid
cell, so Open-Meteo returns ``wave_height: null`` there. That is handled
gracefully — wave terms simply drop out and the score leans on wind/gusts (or
the reading is treated as unavailable if *every* field is null).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from loguru import logger


# ── Endpoints + request shape (keyless) ───────────────────────────────────────

_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Marine "current" fields — significant wave height + the wind-wave / swell
# decomposition, all in metres.
_MARINE_CURRENT = "wave_height,wind_wave_height,swell_wave_height"
# Forecast "current" fields — surface wind speed + gusts, in km/h.
_FORECAST_CURRENT = "wind_speed_10m,wind_gusts_10m"

_REQUEST_TIMEOUT = 10  # seconds — hard per-request network timeout
_HEADERS = {"User-Agent": "ship-tracker/1.0 (marine-weather; keyless Open-Meteo)"}


# ── Cache ─────────────────────────────────────────────────────────────────────

_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "marine"
try:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception as exc:  # pragma: no cover - defensive
    logger.warning(f"marine_weather_feed: cache dir create failed: {exc}")


@dataclass
class MarineConditions:
    """Observed marine conditions at a single waypoint.

    Any field may be ``None`` when the grid cell does not report it (e.g. an
    inland canal has no wave data). ``is_real`` is ``True`` only for a value
    that came from a real fetch (never the modeled fallback — there is none in
    this module; the caller owns the fallback)."""

    lat: float
    lon: float
    wave_height_m: Optional[float]      # significant wave height (m)
    wind_wave_height_m: Optional[float] # wind-wave component (m)
    swell_wave_height_m: Optional[float]
    wind_speed_kmh: Optional[float]     # 10m wind speed (km/h)
    wind_gusts_kmh: Optional[float]     # 10m wind gusts (km/h)
    fetched_at: str                     # ISO-8601 UTC
    is_real: bool = True                # True = real fetch; provenance stamp

    def to_dict(self) -> dict:
        return asdict(self)

    def has_any_signal(self) -> bool:
        """True when at least one quantitative field is present."""
        return any(
            v is not None
            for v in (
                self.wave_height_m,
                self.wind_wave_height_m,
                self.swell_wave_height_m,
                self.wind_speed_kmh,
                self.wind_gusts_kmh,
            )
        )


def _cache_path(lat: float, lon: float) -> Path:
    # Round to 0.25° so nearby calls share a cell-ish key (and the filename is
    # filesystem-safe — no '.' issues beyond the rounded number).
    return _CACHE_DIR / f"wx_{round(lat, 2)}_{round(lon, 2)}.json"


def _read_cache(lat: float, lon: float, ttl_hours: float) -> Optional[MarineConditions]:
    path = _cache_path(lat, lon)
    if not path.exists():
        return None
    try:
        if ttl_hours > 0:
            age_hours = (time.time() - path.stat().st_mtime) / 3600
            if age_hours > ttl_hours:
                return None
        data = json.loads(path.read_text())
        return MarineConditions(**data)
    except Exception as exc:
        logger.debug(f"marine_weather_feed: cache read failed ({path.name}): {exc}")
        return None


def _write_cache(conditions: MarineConditions) -> None:
    path = _cache_path(conditions.lat, conditions.lon)
    try:
        path.write_text(json.dumps(conditions.to_dict(), indent=2))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"marine_weather_feed: cache write failed: {exc}")


def _stamp_provenance(source: str, key: str, kind: str) -> None:
    """Best-effort per-fetch realness stamp (rec R003/R097). Never raises, never
    blocks; off in tests unless ``state.fetch_ledger.RECORDING_ENABLED``."""
    try:
        from state.fetch_ledger import record_fetch
        quality = "GOOD" if kind == "live" else "UNKNOWN"
        record_fetch(source, key, kind, row_count=0, quality=quality)
    except Exception:  # pragma: no cover - defensive
        pass


# ── Network parse helpers ─────────────────────────────────────────────────────

def _as_float(value) -> Optional[float]:
    """Coerce a JSON number to float, mapping null / non-numeric to None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # Open-Meteo uses null for "no data"; guard against NaN sneaking through.
    if f != f:  # NaN
        return None
    return f


def _current_block(getter, url: str, lat: float, lon: float, current: str) -> Optional[dict]:
    """GET an Open-Meteo endpoint and return its ``current`` dict, or None.

    OFFLINE-SAFE: any network error, non-200, or non-JSON body → None (logged at
    debug, never raised)."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": current,
    }
    try:
        resp = getter(url, params=params, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
    except Exception as exc:
        logger.debug(f"marine_weather_feed: request failed ({url}): {exc}")
        return None
    try:
        if getattr(resp, "status_code", 200) != 200:
            logger.debug(f"marine_weather_feed: non-200 ({url}): {resp.status_code}")
            return None
        data = resp.json()
    except Exception as exc:
        logger.debug(f"marine_weather_feed: parse failed ({url}): {exc}")
        return None
    if not isinstance(data, dict):
        return None
    current_block = data.get("current")
    if not isinstance(current_block, dict):
        return None
    return current_block


# ── Public fetch ──────────────────────────────────────────────────────────────

def fetch_marine_conditions(
    lat: float,
    lon: float,
    *,
    http_get=None,
    cache_ttl_hours: float = 6.0,
) -> Optional[MarineConditions]:
    """Fetch live marine + wind conditions at ``(lat, lon)`` from Open-Meteo.

    Waterfall: TTL file cache → marine fetch (waves) + forecast fetch (wind) →
    ``None`` when the feed is dark.

    OFFLINE-SAFE. ``http_get`` is an injectable ``(url, *, params, headers,
    timeout) -> response`` (defaults ``requests.get``); ANY error returns
    ``None`` and NEVER raises. A reading with no usable field at all (e.g. both
    endpoints down, or an inland point that reports all-null) returns ``None``
    so the caller falls back to the modeled almanac.

    Parameters
    ----------
    lat, lon:
        Waypoint coordinates (e.g. a chokepoint centroid).
    http_get:
        Injectable getter for OFFLINE tests. Defaults to ``requests.get``.
    cache_ttl_hours:
        File-cache lifetime. Pass ``0.0`` in tests to bypass the cache.

    Returns
    -------
    MarineConditions | None
    """
    getter = http_get or requests.get

    # 1 — cache (only when a real getter would otherwise hit the network; tests
    # pass cache_ttl_hours=0.0 to bypass).
    cached = _read_cache(lat, lon, cache_ttl_hours)
    if cached is not None:
        return cached

    # 2 — live marine waves
    marine = _current_block(getter, _MARINE_URL, lat, lon, _MARINE_CURRENT)
    # 3 — live wind / gusts
    forecast = _current_block(getter, _FORECAST_URL, lat, lon, _FORECAST_CURRENT)

    if marine is None and forecast is None:
        # Entirely dark — honest None; caller uses the modeled almanac.
        _stamp_provenance("marine_weather", f"{round(lat,2)}_{round(lon,2)}", "failed")
        logger.debug(f"marine_weather_feed: dark for ({lat}, {lon})")
        return None

    marine = marine or {}
    forecast = forecast or {}

    conditions = MarineConditions(
        lat=lat,
        lon=lon,
        wave_height_m=_as_float(marine.get("wave_height")),
        wind_wave_height_m=_as_float(marine.get("wind_wave_height")),
        swell_wave_height_m=_as_float(marine.get("swell_wave_height")),
        wind_speed_kmh=_as_float(forecast.get("wind_speed_10m")),
        wind_gusts_kmh=_as_float(forecast.get("wind_gusts_10m")),
        fetched_at=datetime.now(tz=timezone.utc).isoformat(),
        is_real=True,
    )

    if not conditions.has_any_signal():
        # A real 200 that carried only nulls (e.g. inland point with no wind
        # either) is no signal — treat as dark, do NOT cache an empty reading.
        _stamp_provenance("marine_weather", f"{round(lat,2)}_{round(lon,2)}", "empty")
        logger.debug(f"marine_weather_feed: all-null reading for ({lat}, {lon})")
        return None

    _stamp_provenance("marine_weather", f"{round(lat,2)}_{round(lon,2)}", "live")
    if cache_ttl_hours > 0:
        _write_cache(conditions)
    return conditions


# ── Risk mapping (pure) ───────────────────────────────────────────────────────

# Conservative, documented thresholds. Each driver is mapped to [0, 1] by a
# piecewise-linear ramp between a "calm" floor (→ 0) and a "severe" ceiling
# (→ 1), then the drivers are combined by MAX (the worst driver dominates a
# voyage — a hazard is set by its single worst condition, not an average).
#
#   wave_height_m   : 1.0 m calm  → 6.0 m severe  (open-ocean container voyage:
#                     ~1m benign, ~4-6m forces slow-steaming / course change)
#   wind_speed_kmh  :  20 calm    →  90 severe    (90 km/h ≈ 49 kn ≈ Beaufort 9-10,
#                     gale that closes cranes / forces heave-to)
#   wind_gusts_kmh  :  30 calm    → 110 severe    (gusts run higher than mean wind;
#                     110 km/h ≈ 59 kn — violent-storm gusting)
#
# Swell/wind-wave are folded into wave_height already (significant wave height is
# the combined sea state), so they are NOT double-counted; they are available on
# the dataclass for display.

_WAVE_CALM_M, _WAVE_SEVERE_M = 1.0, 6.0
_WIND_CALM_KMH, _WIND_SEVERE_KMH = 20.0, 90.0
_GUST_CALM_KMH, _GUST_SEVERE_KMH = 30.0, 110.0


def _ramp(value: Optional[float], calm: float, severe: float) -> Optional[float]:
    """Piecewise-linear ramp: <=calm → 0, >=severe → 1, linear between. None
    passes through as None (missing driver, not zero risk)."""
    if value is None:
        return None
    if value <= calm:
        return 0.0
    if value >= severe:
        return 1.0
    return (value - calm) / (severe - calm)


def conditions_to_risk(conditions: Optional[MarineConditions]) -> Optional[float]:
    """Map observed conditions to a current_risk_score in [0, 1].

    MAX over the available drivers (waves, wind, gusts) — the worst single
    condition sets the hazard. Missing drivers are skipped (None ≠ zero). Returns
    ``None`` when nothing usable is present (so the caller keeps the modeled
    score rather than reading the absence as "calm"). Monotonic: a higher wave /
    wind / gust never lowers the score. Always clamped to [0, 1]."""
    if conditions is None:
        return None
    drivers = [
        _ramp(conditions.wave_height_m, _WAVE_CALM_M, _WAVE_SEVERE_M),
        _ramp(conditions.wind_speed_kmh, _WIND_CALM_KMH, _WIND_SEVERE_KMH),
        _ramp(conditions.wind_gusts_kmh, _GUST_CALM_KMH, _GUST_SEVERE_KMH),
    ]
    present = [d for d in drivers if d is not None]
    if not present:
        return None
    return max(0.0, min(1.0, max(present)))


# ── Route → waypoint(s) → live score ──────────────────────────────────────────

def _route_waypoints(route_id: str) -> list[tuple[float, float]]:
    """Return the chokepoint waypoint coords (lat, lon) for a route.

    Uses the maritime-chokepoint registry (``processing.chokepoint_analyzer``):
    every chokepoint touching ``route_id`` contributes its centroid. These are
    the open-water pinch points where weather most affects a voyage. Falls back
    to an empty list (→ no live score) for a route with no chokepoint coverage.
    Never raises."""
    try:
        from processing.chokepoint_analyzer import CHOKEPOINTS
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"marine_weather_feed: chokepoint registry import failed: {exc}")
        return []
    points: list[tuple[float, float]] = []
    for cp in CHOKEPOINTS.values():
        if route_id in getattr(cp, "affected_routes", []) or []:
            lat = getattr(cp, "lat", None)
            lon = getattr(cp, "lon", None)
            if lat is not None and lon is not None:
                points.append((float(lat), float(lon)))
    return points


def fetch_route_weather(
    route_id: str,
    *,
    http_get=None,
    cache_ttl_hours: float = 6.0,
) -> Optional[float]:
    """Return a live weather risk score in [0, 1] for ``route_id``, or ``None``.

    Queries Open-Meteo at each chokepoint waypoint touching the route and takes
    the MAX live ``conditions_to_risk`` across them (the worst pinch point sets
    the route's live weather hazard). Returns ``None`` when no waypoint answers
    (offline / dark / route with no chokepoint coverage), so the caller keeps
    the modeled almanac score.

    OFFLINE-SAFE. ``http_get`` is injectable (defaults ``requests.get``); never
    raises. ``route_registry`` is accepted for API symmetry but unused — route →
    waypoints is resolved via the chokepoint registry, which carries the coords.
    """
    waypoints = _route_waypoints(route_id)
    if not waypoints:
        return None
    scores: list[float] = []
    for lat, lon in waypoints:
        conditions = fetch_marine_conditions(
            lat, lon, http_get=http_get, cache_ttl_hours=cache_ttl_hours
        )
        score = conditions_to_risk(conditions)
        if score is not None:
            scores.append(score)
    if not scores:
        return None
    return max(0.0, min(1.0, max(scores)))
