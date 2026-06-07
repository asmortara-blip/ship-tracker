"""
data/gdelt_feed.py
──────────────────
Keyless GDELT geo-event feed for maritime chokepoint risk (rec R014).

WHAT IT DOES
    Fetches recent geo-located conflict / disruption news events from GDELT's
    public, KEYLESS DOC 2.0 API and turns the density + tone of events near a
    chokepoint's coordinates into a risk signal that the chokepoint overlay
    (``processing.chokepoint_analyzer.apply_live_chokepoint_risk``) can use to
    ESCALATE the hardcoded baseline — but ONLY when real events are present.

ENDPOINT (keyless — NO api key required)
    https://api.gdeltproject.org/api/v2/doc/doc
    GDELT's Document API 2.0 is free and keyless (rate-limited to ~1 req / 5 s).
    We query it per-chokepoint with the documented ``near:`` proximity operator
    (``near<radius_km>:<lat>,<lon>``) so the host-side returns only articles
    geo-tagged within ``radius_km`` of the chokepoint, plus a disruption-term
    filter. ``mode=artlist&format=json`` returns the matching article list; the
    record count is the event density and (when present) the per-record/overall
    tone gives sentiment. The parser ALSO accepts a generic GeoJSON ``features``
    payload (GDELT GEO 2.0 shape) so a future swap needs no rewrite.

HONESTY (this is the whole point — R014)
    * OFFLINE-SAFE: any network error / non-200 / parse failure → return an
      EMPTY event list and stamp realness ``"unavailable"``. NEVER raises, NEVER
      fabricates an event.
    * Absence of events is NOT escalation. ``gdelt_chokepoint_signal`` only ever
      raises risk ABOVE the baseline when real, sufficiently-dense / negative
      events are observed; with no events it returns ``realness="dark"`` and the
      caller keeps the modeled baseline constant.
    * Provenance-stamped via ``state.fetch_ledger`` (R003/R097): ``live`` when a
      real fetch returned events, ``empty`` when it returned none, ``synthetic``
      / ``failed`` on the offline / error paths.

Dependencies: requests, loguru (both already in the project).
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from loguru import logger


# ── Endpoint / constants ─────────────────────────────────────────────────────

# GDELT Document API 2.0 — public, KEYLESS. Rate-limited host-side (~1 req/5s).
_GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_REQUEST_TIMEOUT = 12          # seconds — per-query network timeout
_FETCH_BUDGET_SECONDS = 25     # overall wall-clock cap across all chokepoint queries
_INTER_REQUEST_DELAY = 5.2     # seconds between GDELT queries (host asks for >5s)
_DEFAULT_RADIUS_KM = 250.0     # geo-proximity radius for the near: operator
_DEFAULT_TIMESPAN = "3d"       # how far back GDELT should look
_MAX_RECORDS = 75              # cap per query (density saturates well before this)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

# Disruption / conflict terms that make a near-chokepoint article RELEVANT.
# Kept broad-but-maritime so unrelated local news near a coastal coordinate
# does not by itself escalate risk; the density + tone thresholds gate further.
_DISRUPTION_TERMS = (
    "attack OR strike OR missile OR drone OR blockade OR seized OR "
    "hijack OR closure OR closed OR conflict OR clash OR sanctions OR "
    "collision OR grounding OR blocked OR disruption OR explosion"
)

_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "gdelt"
try:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:  # pragma: no cover - read-only FS guard
    pass


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class GdeltEvent:
    """One geo-located GDELT news event near a chokepoint."""

    lat: float
    lon: float
    title: str
    url: str
    seen_at: str                 # ISO-ish date string from GDELT (best-effort)
    tone: float                  # GDELT average tone (negative = adverse)
    chokepoint_key: str = ""     # which chokepoint this was fetched for ("" if generic)

    def to_dict(self) -> dict:
        return asdict(self)


# Risk vocabulary shared with chokepoint_analyzer (CRITICAL > HIGH > MODERATE > LOW).
_RISK_ORDER = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "CRITICAL": 3}


# ── Geo helpers (pure) ────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def events_near(lat: float, lon: float, radius_km: float, events) -> list:
    """Pure geo-filter: return the events within ``radius_km`` of (lat, lon).

    Accepts an iterable of :class:`GdeltEvent` (or any object/dict exposing
    ``lat``/``lon``). Events missing usable coordinates are dropped, never
    counted. The boundary is inclusive (distance <= radius_km).
    """
    out: list = []
    for ev in events or []:
        elat = getattr(ev, "lat", None)
        elon = getattr(ev, "lon", None)
        if elat is None and isinstance(ev, dict):
            elat, elon = ev.get("lat"), ev.get("lon")
        if elat is None or elon is None:
            continue
        try:
            if _haversine_km(lat, lon, float(elat), float(elon)) <= float(radius_km):
                out.append(ev)
        except (TypeError, ValueError):
            continue
    return out


# ── Event → risk mapping (pure, deterministic, documented) ────────────────────
#
# DESIGN (conservative — real events required to ESCALATE; absence ≠ escalation):
#   1. Filter the supplied events to those within ``radius_km`` of the
#      chokepoint coords (``events_near``).
#   2. If there are NONE → realness="dark": return (None, None, "dark"). The
#      caller MUST keep the hardcoded baseline. We never invent risk from silence.
#   3. Otherwise compute:
#         n         = number of near events (density)
#         avg_tone  = mean GDELT tone (negative is adverse; GDELT tone is
#                     typically in roughly [-10, +10])
#      and map to a level using BOTH density and adversity:
#         CRITICAL : n >= 8  AND avg_tone <= -4      (dense + strongly adverse)
#         HIGH     : n >= 4  AND avg_tone <= -2      (clustered + adverse)
#         MODERATE : n >= 2  AND avg_tone <= -0.5    (some adverse coverage)
#         LOW      : otherwise (events exist but benign / sparse)
#   4. disruption_type is ACTIVE_CONFLICT when the derived level is HIGH/CRITICAL
#      (the query is conflict/disruption-termed, so dense adverse clusters are
#      treated as active conflict), CONGESTION for MODERATE, NONE for LOW.
#
# The thresholds are intentionally cautious: it takes a genuine, negative-tone
# cluster of real articles to push a chokepoint above its baseline.

# (density_min, tone_max, risk_level, disruption_type), evaluated worst-first.
_SIGNAL_RULES = (
    (8, -4.0,  "CRITICAL", "ACTIVE_CONFLICT"),
    (4, -2.0,  "HIGH",     "ACTIVE_CONFLICT"),
    (2, -0.5,  "MODERATE", "CONGESTION"),
)


def gdelt_chokepoint_signal(chokepoint, events, *, radius_km: float = _DEFAULT_RADIUS_KM):
    """Map GDELT events near a chokepoint → (risk_level, disruption_type, realness).

    Parameters
    ----------
    chokepoint
        Anything exposing ``lat`` / ``lon`` (a ``Chokepoint`` or a dict).
    events
        Iterable of :class:`GdeltEvent` (or dicts) — typically the output of
        ``fetch_chokepoint_events`` / a test fixture.
    radius_km
        Geo-proximity radius for ``events_near``.

    Returns
    -------
    (risk_level, disruption_type, realness)
        * ``realness == "dark"`` with ``(None, None)`` when NO real near events
          exist — the caller keeps the modeled baseline. Absence is never
          escalation.
        * ``realness == "live"`` with a derived ``(risk_level, disruption_type)``
          when a real cluster is present.
    """
    lat = getattr(chokepoint, "lat", None)
    lon = getattr(chokepoint, "lon", None)
    if lat is None and isinstance(chokepoint, dict):
        lat, lon = chokepoint.get("lat"), chokepoint.get("lon")
    if lat is None or lon is None:
        return (None, None, "dark")

    near = events_near(float(lat), float(lon), radius_km, events)
    if not near:
        return (None, None, "dark")

    n = len(near)
    tones = [float(getattr(e, "tone", 0.0) if not isinstance(e, dict)
                   else e.get("tone", 0.0) or 0.0) for e in near]
    avg_tone = sum(tones) / len(tones) if tones else 0.0

    for density_min, tone_max, level, dtype in _SIGNAL_RULES:
        if n >= density_min and avg_tone <= tone_max:
            return (level, dtype, "live")

    # Real events exist but they are sparse / benign — a real, low-risk read.
    return ("LOW", "NONE", "live")


def escalate(baseline_level: str, derived_level: str) -> str:
    """Return the MORE-severe of two risk levels (never downgrades baseline).

    The overlay only ever RAISES risk from real GDELT signal; a real-but-mild
    reading must not silently lower the modeled baseline (which encodes
    standing geopolitical context GDELT may not see in a 3-day window).
    """
    if _RISK_ORDER.get(derived_level, -1) > _RISK_ORDER.get(baseline_level, -1):
        return derived_level
    return baseline_level


# ── Provenance stamp (best-effort; never raises) ──────────────────────────────

def _stamp(kind: str, key: str, row_count: int) -> None:
    """Best-effort per-fetch provenance stamp (R003/R097). Never raises."""
    try:
        from state.fetch_ledger import record_fetch
        quality = ("GOOD" if kind == "live"
                   else "UNKNOWN" if kind in ("empty", "failed") else "DEMO")
        record_fetch("gdelt", key, kind, row_count=int(row_count), quality=quality)
    except Exception:  # pragma: no cover - defensive
        pass


# ── Cache (JSON sidecar, mirrors canal_feed) ──────────────────────────────────

def _cache_path(key: str) -> Path:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in key)[:120]
    return _CACHE_DIR / f"{safe}.json"


def _read_cache(key: str, ttl_hours: float):
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours > ttl_hours:
            return None
        rows = json.loads(path.read_text())
        return [GdeltEvent(**r) for r in rows]
    except Exception as exc:
        logger.debug(f"gdelt_feed: cache read failed ({key}): {exc}")
        return None


def _write_cache(key: str, events: list) -> None:
    try:
        _cache_path(key).write_text(
            json.dumps([e.to_dict() for e in events], indent=2)
        )
    except Exception as exc:  # pragma: no cover - read-only FS guard
        logger.debug(f"gdelt_feed: cache write failed ({key}): {exc}")


# ── Network fetch (offline-safe, injectable) ──────────────────────────────────

def _build_doc_url(lat: float, lon: float, radius_km: float, timespan: str) -> str:
    """Build the keyless GDELT DOC 2.0 geo-proximity query URL."""
    # GDELT near:<km>:<lat>,<lon> restricts to articles geo-tagged within the
    # radius; combined with the disruption terms this returns relevant local
    # disruption coverage only.
    query = f"near{int(radius_km)}:{lat},{lon} ({_DISRUPTION_TERMS})"
    from urllib.parse import urlencode
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "timespan": timespan,
        "maxrecords": _MAX_RECORDS,
        "sort": "datedesc",
    }
    return f"{_GDELT_DOC_URL}?{urlencode(params)}"


def _parse_gdelt_payload(payload, *, default_lat: float, default_lon: float,
                         chokepoint_key: str) -> list:
    """Parse a GDELT JSON payload → list[GdeltEvent]. Tolerant of two shapes.

    1. DOC 2.0 ``{"articles": [ {url,title,seendate,...}, ... ]}`` — articles
       are geo-restricted host-side by the ``near:`` operator, so they may not
       carry their own coords; we stamp the chokepoint's coords so the pure
       geo-filter still treats them as in-radius. Tone defaults to 0 unless
       GDELT supplies it (``tone``).
    2. Generic GeoJSON ``{"features": [ {geometry:{coordinates:[lon,lat]},
       properties:{...}}, ... ]}`` — GDELT GEO 2.0 shape; coords + count + tone
       read from the feature.
    """
    events: list = []
    if not isinstance(payload, dict):
        return events

    # Shape 1 — DOC artlist
    arts = payload.get("articles")
    if isinstance(arts, list):
        for a in arts:
            if not isinstance(a, dict):
                continue
            tone = a.get("tone")
            try:
                tone = float(tone) if tone is not None else 0.0
            except (TypeError, ValueError):
                tone = 0.0
            events.append(GdeltEvent(
                lat=default_lat, lon=default_lon,
                title=str(a.get("title", ""))[:300],
                url=str(a.get("url", "")),
                seen_at=str(a.get("seendate", "")),
                tone=tone,
                chokepoint_key=chokepoint_key,
            ))
        return events

    # Shape 2 — GeoJSON features (GDELT GEO 2.0)
    feats = payload.get("features")
    if isinstance(feats, list):
        for f in feats:
            if not isinstance(f, dict):
                continue
            geom = f.get("geometry") or {}
            coords = geom.get("coordinates") or []
            props = f.get("properties") or {}
            if not (isinstance(coords, list) and len(coords) >= 2):
                continue
            try:
                lon, lat = float(coords[0]), float(coords[1])  # GeoJSON = [lon,lat]
            except (TypeError, ValueError):
                continue
            tone = props.get("tone", props.get("avgtone", 0.0))
            try:
                tone = float(tone) if tone is not None else 0.0
            except (TypeError, ValueError):
                tone = 0.0
            # GEO points carry a 'count' (articles at that location) — fan it
            # out so density reflects coverage volume, capped to keep it sane.
            count = props.get("count", 1)
            try:
                count = max(1, min(20, int(count)))
            except (TypeError, ValueError):
                count = 1
            for _ in range(count):
                events.append(GdeltEvent(
                    lat=lat, lon=lon,
                    title=str(props.get("name", props.get("html", "")))[:300],
                    url=str(props.get("shareimage", "")),
                    seen_at="",
                    tone=tone,
                    chokepoint_key=chokepoint_key,
                ))
        return events

    return events


def fetch_chokepoint_events(
    chokepoints,
    *,
    radius_km: float = _DEFAULT_RADIUS_KM,
    timespan: str = _DEFAULT_TIMESPAN,
    cache_ttl_hours: float = 6.0,
    http_get=None,
) -> tuple:
    """Fetch recent GDELT geo-events near each chokepoint. OFFLINE-SAFE.

    Parameters
    ----------
    chokepoints
        Iterable of objects exposing ``lat``/``lon`` and (optionally) a name.
        For the registry, pass ``CHOKEPOINTS.items()``-style — but this accepts
        any iterable of (key, chokepoint) pairs OR bare chokepoints.
    http_get
        Injectable callable ``(url, *, headers, timeout) -> response`` for
        OFFLINE tests. Defaults to ``requests.get``. A test passes a stub that
        returns a fixture payload (or raises, to exercise the offline path).

    Returns
    -------
    (events, realness)
        ``events`` is a flat ``list[GdeltEvent]`` across all chokepoints.
        ``realness`` is one of ``"live"`` (a real fetch returned >=1 event),
        ``"empty"`` (real fetch, zero events), or ``"unavailable"`` (every fetch
        failed / offline). Never raises.
    """
    getter = http_get or requests.get

    # Normalize input to (key, cp) pairs.
    pairs: list = []
    for item in (chokepoints or []):
        if isinstance(item, tuple) and len(item) == 2:
            pairs.append((str(item[0]), item[1]))
        else:
            pairs.append((getattr(item, "name", "") or "", item))

    all_events: list = []
    any_success = False
    any_failure = False
    start = time.monotonic()

    for i, (key, cp) in enumerate(pairs):
        lat = getattr(cp, "lat", None)
        lon = getattr(cp, "lon", None)
        if lat is None and isinstance(cp, dict):
            lat, lon = cp.get("lat"), cp.get("lon")
        if lat is None or lon is None:
            continue

        if time.monotonic() - start > _FETCH_BUDGET_SECONDS:
            logger.warning("gdelt_feed: fetch budget exhausted — stopping early")
            break

        cache_key = f"{key or 'cp'}_{round(float(lat),2)}_{round(float(lon),2)}_{timespan}"
        cached = _read_cache(cache_key, cache_ttl_hours)
        if cached is not None:
            all_events.extend(cached)
            any_success = any_success or bool(cached)
            continue

        # Courtesy inter-request delay (GDELT asks for >5s). Skipped for the
        # first query and skipped entirely when a stub getter is injected (tests
        # must not sleep).
        if i > 0 and http_get is None:
            time.sleep(_INTER_REQUEST_DELAY)

        url = _build_doc_url(float(lat), float(lon), radius_km, timespan)
        try:
            resp = getter(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
            status = getattr(resp, "status_code", 200)
            if status != 200:
                logger.debug(f"gdelt_feed: {key} non-200 ({status})")
                any_failure = True
                continue
            # GDELT sometimes returns a plain-text rate-limit notice with a 200.
            text = getattr(resp, "text", "") or ""
            if text.strip().lower().startswith("please limit requests"):
                logger.debug(f"gdelt_feed: {key} rate-limited")
                any_failure = True
                continue
            try:
                payload = resp.json()
            except Exception:
                payload = json.loads(text) if text.strip().startswith("{") else {}
            cp_events = _parse_gdelt_payload(
                payload, default_lat=float(lat), default_lon=float(lon),
                chokepoint_key=key,
            )
            _write_cache(cache_key, cp_events)
            all_events.extend(cp_events)
            any_success = True
        except Exception as exc:
            logger.debug(f"gdelt_feed: {key} fetch failed: {exc}")
            any_failure = True
            continue

    if any_success and all_events:
        _stamp("live", "chokepoint_events", len(all_events))
        return (all_events, "live")
    if any_success and not all_events:
        # Real fetch(es) succeeded but returned zero events — honestly empty.
        _stamp("empty", "chokepoint_events", 0)
        return ([], "empty")
    if any_failure:
        # Everything failed / offline — honestly unavailable, NOT synthetic data.
        _stamp("failed", "chokepoint_events", 0)
        return ([], "unavailable")
    # No chokepoints had coords / nothing attempted.
    return ([], "unavailable")
