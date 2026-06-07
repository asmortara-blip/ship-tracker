"""
data/gdelt_feed.py
──────────────────
Keyless GDELT disruption-NEWS-VOLUME feed for maritime chokepoint risk (R014).

WHAT THIS SIGNAL ACTUALLY IS (read this before trusting it)
    A COUNTRY-LEVEL disruption-news VOLUME signal. For each chokepoint we map it
    to the ISO country code(s) of the state(s) that physically control it (Suez →
    EG, Bab-el-Mandeb → YE, Hormuz → IR/OM, …) and ask GDELT DOC 2.0 how many
    recent articles match a disruption-term query restricted to that
    ``sourcecountry``. The COUNT of those articles in the window is the signal.

    It is NOT precise geolocation (the articles carry no lat/lon), and it is NOT
    tone-based (DOC ``artlist`` carries no per-article tone). It is an
    ILLUSTRATIVE volume heuristic attributed at country granularity, used ONLY to
    ESCALATE the hardcoded chokepoint baseline when a genuine news surge appears —
    never to lower it, never to invent risk from silence.

ENDPOINT (keyless — NO api key required)
    https://api.gdeltproject.org/api/v2/doc/doc   (GDELT Document API 2.0)
    Free, keyless, rate-limited host-side to ~1 req / 5 s. We query it per
    chokepoint with a VALID country-restricted query::

        ({disruption terms}) sourcecountry:EG        (single country)
        ({disruption terms}) (sourcecountry:MY OR sourcecountry:SG OR sourcecountry:ID)

    plus ``mode=artlist&format=json&timespan=<…>&maxrecords=250``. The returned
    ``articles`` list is the volume. (GDELT GEO 2.0 — the old ``near<km>:lat,lon``
    geo operator — is GONE: it 404s, and DOC's ``near`` is a TEXT-proximity
    operator, NOT geographic. The previous design's ``near{km}:{lat},{lon}`` query
    was INVALID and silently returned a plain-text error that masqueraded as an
    honest empty fetch — R014 was DEAD in production. This file fixes that.)

HONESTY (this is the whole point — R014)
    * OFFLINE-SAFE: any network error / non-200 → empty list, realness
      ``"unavailable"``. NEVER raises, NEVER fabricates.
    * STRUCTURAL FAILURE ≠ EMPTY: a 200 whose body is plain text that does NOT
      parse as JSON (rate-limit notice, invalid-query notice, anything not
      starting ``{``/``[``) is a FAILURE → realness ``"unavailable"``, NOT cached.
      ``"empty"`` is reserved for a REAL JSON response that genuinely held zero
      articles.
    * ABSENCE IS NEVER ESCALATION: ``gdelt_chokepoint_signal`` returns
      ``(None, None, "dark")`` for zero articles; the caller keeps the modeled
      baseline. A chokepoint with no mapped country is SKIPPED (honest no-signal).
    * Provenance-stamped via ``state.fetch_ledger`` (R003/R097).

Dependencies: requests, loguru (both already in the project).
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import requests
from loguru import logger


# ── Endpoint / constants ─────────────────────────────────────────────────────

# GDELT Document API 2.0 — public, KEYLESS. Rate-limited host-side (~1 req/5s).
_GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_REQUEST_TIMEOUT = 12          # seconds — per-query network timeout
_FETCH_BUDGET_SECONDS = 25     # overall wall-clock cap across all chokepoint queries
_INTER_REQUEST_DELAY = 5.2     # seconds between GDELT queries (host asks for >5s)
_DEFAULT_RADIUS_KM = 250.0     # geo radius used ONLY to attribute events to a node
_DEFAULT_TIMESPAN = "14d"      # how far back GDELT should look
_MAX_RECORDS = 250             # cap per query — the denominator for the volume bins

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

# Disruption / conflict terms that make an article RELEVANT to a chokepoint's
# country. Kept broad-but-maritime; the volume thresholds gate further so a
# genuine surge — not background news — is required to escalate.
_DISRUPTION_TERMS = (
    "(attack OR strike OR missile OR drone OR blockade OR seized OR "
    "hijack OR closure OR conflict OR clash OR sanctions OR "
    "collision OR grounding OR blocked OR disruption OR explosion)"
)

# Chokepoint key → GDELT ``sourcecountry`` code(s). When >1, they are OR-ed in the
# query. A chokepoint absent from this map has NO mapped country → SKIPPED (the
# feed honestly produces no signal for it rather than guessing).
#
# IMPORTANT: GDELT DOC 2.0 ``sourcecountry`` uses FIPS 10-4 codes, NOT ISO-3166.
# Verified live: ``sourcecountry:SN`` returns Singapore (FIPS SN, a major port,
# n=182) while ISO ``SG`` returns Senegal; ``GB`` is Gabon (FIPS) not the UK. So
# these are the FIPS codes, several of which differ from ISO:
#   suez → EG (Egypt)              bab_el_mandeb → YM (Yemen)
#   hormuz → IR, MU (Iran/Oman)    malacca → MY, SN, ID (Malaysia/Singapore/Indonesia)
#   panama → PM                    gibraltar → SP, MO (Spain/Morocco)
#   danish_straits → DA            dover → UK, FR
#   lombok_sunda → ID
_CHOKEPOINT_COUNTRY: dict[str, tuple] = {
    "suez": ("EG",),
    "bab_el_mandeb": ("YM",),
    "hormuz": ("IR", "MU"),
    "malacca": ("MY", "SN", "ID"),
    "panama": ("PM",),
    "gibraltar": ("SP", "MO"),
    "danish_straits": ("DA",),
    "dover": ("UK", "FR"),
    "lombok_sunda": ("ID",),
}

_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "gdelt"
try:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:  # pragma: no cover - read-only FS guard
    pass


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class GdeltEvent:
    """One disruption-news article attributed to a chokepoint.

    The article is attributed at COUNTRY granularity (it matched the chokepoint's
    ``sourcecountry`` query), so ``lat``/``lon`` are the CHOKEPOINT's coords, not
    the article's — DOC ``artlist`` carries no article geolocation. ``tone`` is
    likewise unavailable from ``artlist`` and is left 0.0 (kept on the dataclass
    only for backward compatibility / the optional GeoJSON path).
    """

    lat: float
    lon: float
    title: str
    url: str
    seen_at: str                 # ISO-ish date string from GDELT (best-effort)
    tone: float = 0.0            # GDELT tone — NOT available via artlist; left 0
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

    Used to ATTRIBUTE article-events to a chokepoint by its coords (each event is
    stamped with the chokepoint's own coords when fetched, so this recovers the
    events fetched for that node). The genuine filter upstream is
    ``sourcecountry`` — this is just the coord-side attribution. Accepts an
    iterable of :class:`GdeltEvent` (or any object/dict exposing ``lat``/``lon``).
    Events missing usable coordinates are dropped. Boundary inclusive.
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


def _events_for_chokepoint(chokepoint_key: str, lat, lon, radius_km, events) -> list:
    """Recover the article-events belonging to one chokepoint.

    Prefers an exact ``chokepoint_key`` match when events carry one (the fetch
    stamps it), so neighbouring chokepoints whose coords fall inside each other's
    radius are not cross-counted. Falls back to the coord radius for events with
    no key (e.g. test fixtures or the GeoJSON path).
    """
    keyed = [e for e in (events or [])
             if str(getattr(e, "chokepoint_key", "") or
                     (e.get("chokepoint_key", "") if isinstance(e, dict) else "")
                     ) == chokepoint_key]
    if chokepoint_key and keyed:
        return keyed
    if lat is None or lon is None:
        return []
    return events_near(float(lat), float(lon), radius_km, events)


# ── Volume → risk mapping (pure, deterministic, documented) ───────────────────
#
# DESIGN — country-level disruption-news VOLUME (NO geolocation, NO tone):
#   1. Recover the articles fetched for this chokepoint (``_events_for_chokepoint``
#      — exact key match, else coord radius).
#   2. n = number of those articles (out of maxrecords=250). n == 0 → realness
#      "dark", (None, None): the caller keeps the modeled baseline. We never
#      invent risk from silence.
#   3. Map n to a level with conservative, round, ILLUSTRATIVE cutoffs (a genuine
#      surge is required to escalate above the baseline):
#         CRITICAL : n >= 120     (near-saturation of the 250-record window)
#         HIGH     : n >=  50
#         MODERATE : n >=  15
#         LOW      : n >=   1      (real but calm — events exist, no surge)
#   4. disruption_type is ACTIVE_CONFLICT for HIGH/CRITICAL (heavy disruption-term
#      coverage of the country), CONGESTION for MODERATE, NONE for LOW.
#
# These are an illustrative volume heuristic, NOT a calibrated model. They are
# intentionally cautious: it takes a sustained, country-wide disruption-news
# surge to push a chokepoint above its hardcoded baseline.

# (count_min, risk_level, disruption_type), evaluated worst-first.
_VOLUME_RULES = (
    (120, "CRITICAL", "ACTIVE_CONFLICT"),
    (50,  "HIGH",     "ACTIVE_CONFLICT"),
    (15,  "MODERATE", "CONGESTION"),
)


def gdelt_chokepoint_signal(chokepoint, events, *, radius_km: float = _DEFAULT_RADIUS_KM,
                            chokepoint_key: str = ""):
    """Map disruption-news VOLUME for a chokepoint's country → (level, type, realness).

    Parameters
    ----------
    chokepoint
        Anything exposing ``lat`` / ``lon`` (a ``Chokepoint`` or a dict).
    events
        Iterable of :class:`GdeltEvent` (or dicts) — typically the output of
        ``fetch_chokepoint_events`` / a test fixture. The COUNT of those
        attributed to this chokepoint is the signal (no tone, no geolocation).
    radius_km
        Coord radius used to attribute keyless events to the chokepoint.
    chokepoint_key
        Optional registry key (e.g. ``"suez"``). When supplied it is used to
        match events by their stamped ``chokepoint_key`` exactly — avoiding
        cross-counting neighbouring chokepoints whose coords overlap. Falls back
        to the chokepoint's ``name`` then the coord radius.

    Returns
    -------
    (risk_level, disruption_type, realness)
        * ``(None, None, "dark")`` when the chokepoint has ZERO attributed
          articles — the caller keeps the modeled baseline. Absence is never
          escalation.
        * ``(risk_level, disruption_type, "live")`` when ``n >= 1`` — a real read
          binned by the documented volume cutoffs.
    """
    lat = getattr(chokepoint, "lat", None)
    lon = getattr(chokepoint, "lon", None)
    if lat is None and isinstance(chokepoint, dict):
        lat, lon = chokepoint.get("lat"), chokepoint.get("lon")

    key = str(chokepoint_key or "")
    if not key:
        cp_name = getattr(chokepoint, "name", None)
        if cp_name is None and isinstance(chokepoint, dict):
            cp_name = chokepoint.get("name")
        if cp_name:
            key = str(cp_name)

    attributed = _events_for_chokepoint(key, lat, lon, radius_km, events)
    n = len(attributed)
    if n == 0:
        return (None, None, "dark")

    for count_min, level, dtype in _VOLUME_RULES:
        if n >= count_min:
            return (level, dtype, "live")

    # Articles exist but below the MODERATE surge floor — a real, calm read.
    return ("LOW", "NONE", "live")


def escalate(baseline_level: str, derived_level: str) -> str:
    """Return the MORE-severe of two risk levels (never downgrades baseline).

    The overlay only ever RAISES risk from a real GDELT volume surge; a
    real-but-calm reading must not silently lower the modeled baseline (which
    encodes standing geopolitical context the news window may not reflect).
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

def _country_codes_for(key: str, cp) -> tuple:
    """ISO ``sourcecountry`` code(s) for a chokepoint, or () if unmapped.

    Prefers an explicit ``country`` attribute on the chokepoint if one ever
    exists (str or sequence); falls back to the ``_CHOKEPOINT_COUNTRY`` map.
    """
    attr = getattr(cp, "country", None)
    if attr is None and isinstance(cp, dict):
        attr = cp.get("country")
    if isinstance(attr, str) and attr.strip():
        return (attr.strip().upper(),)
    if isinstance(attr, (list, tuple)) and attr:
        codes = tuple(str(c).strip().upper() for c in attr if str(c).strip())
        if codes:
            return codes
    return _CHOKEPOINT_COUNTRY.get(str(key), ())


def _build_doc_url(country_codes, timespan: str) -> str:
    """Build a VALID keyless GDELT DOC 2.0 country-restricted query URL.

    ``({disruption terms}) sourcecountry:EG`` for one country, or
    ``({terms}) (sourcecountry:MY OR sourcecountry:SG)`` for several. This is the
    correct DOC query shape — the previous ``near{km}:{lat},{lon}`` was INVALID
    (DOC ``near`` is a TEXT-proximity operator, not geographic).
    """
    from urllib.parse import urlencode

    codes = [str(c).strip().upper() for c in (country_codes or []) if str(c).strip()]
    if len(codes) == 1:
        sc = f"sourcecountry:{codes[0]}"
    else:
        sc = "(" + " OR ".join(f"sourcecountry:{c}" for c in codes) + ")"
    query = f"{_DISRUPTION_TERMS} {sc}"
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

    1. DOC 2.0 ``{"articles": [ {url,title,seendate,...}, ... ]}`` — the live
       shape. Articles carry NO coords and NO tone, so each event is stamped with
       the CHOKEPOINT's coords (country-level attribution) and tone 0.0. The COUNT
       is the signal.
    2. Generic GeoJSON ``{"features": [...]}`` (legacy GDELT GEO 2.0 shape, now
       404 in production) — kept harmless: parsed if ever present, never
       fabricated. Coords/tone read from the feature.
    """
    events: list = []
    if not isinstance(payload, dict):
        return events

    # Shape 1 — DOC artlist (the live shape).
    arts = payload.get("articles")
    if isinstance(arts, list):
        for a in arts:
            if not isinstance(a, dict):
                continue
            events.append(GdeltEvent(
                lat=default_lat, lon=default_lon,
                title=str(a.get("title", ""))[:300],
                url=str(a.get("url", "")),
                seen_at=str(a.get("seendate", "")),
                tone=0.0,                     # NOT available via artlist
                chokepoint_key=chokepoint_key,
            ))
        return events

    # Shape 2 — GeoJSON features (legacy; harmless if it ever reappears).
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
    """Fetch recent disruption-news articles for each chokepoint's country.

    OFFLINE-SAFE. Each chokepoint is mapped to its ISO ``sourcecountry`` code(s)
    (``_CHOKEPOINT_COUNTRY`` / an explicit ``country`` attr); an unmapped
    chokepoint is SKIPPED (honest no-signal, not a guess). The returned article
    events are stamped with the chokepoint's own coords + key so the volume signal
    can attribute them back.

    Parameters
    ----------
    chokepoints
        Iterable of (key, chokepoint) pairs OR bare chokepoints exposing
        ``lat``/``lon``/``name``. For the registry pass ``CHOKEPOINTS.items()``.
    http_get
        Injectable ``(url, *, headers, timeout) -> response`` for OFFLINE tests.
        Defaults to ``requests.get``.

    Returns
    -------
    (events, realness)
        ``events`` is a flat ``list[GdeltEvent]`` across all chokepoints.
        ``realness`` ∈ ``"live"`` (≥1 article from a real JSON fetch),
        ``"empty"`` (a real JSON fetch that genuinely returned zero articles), or
        ``"unavailable"`` (every fetch failed / offline / structurally-bad body).
        A structurally-bad 200 (non-JSON text) is a FAILURE, never ``"empty"``,
        and is NEVER cached. Never raises.
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

        country_codes = _country_codes_for(key, cp)
        if not country_codes:
            # No mapped country → honestly produce no signal for this node.
            logger.debug(f"gdelt_feed: {key} has no mapped country — skipped")
            continue

        if time.monotonic() - start > _FETCH_BUDGET_SECONDS:
            logger.warning("gdelt_feed: fetch budget exhausted — stopping early")
            break

        cc_tag = "-".join(country_codes)
        cache_key = f"{key or 'cp'}_{cc_tag}_{timespan}"
        cached = _read_cache(cache_key, cache_ttl_hours)
        if cached is not None:
            all_events.extend(cached)
            any_success = True
            continue

        # Courtesy inter-request delay (GDELT asks for >5s). Skipped for the
        # first query and skipped entirely when a stub getter is injected (tests
        # must not sleep).
        if i > 0 and http_get is None:
            time.sleep(_INTER_REQUEST_DELAY)

        url = _build_doc_url(country_codes, timespan)
        try:
            resp = getter(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
            status = getattr(resp, "status_code", 200)
            if status != 200:
                logger.debug(f"gdelt_feed: {key} non-200 ({status})")
                any_failure = True
                continue

            text = getattr(resp, "text", "") or ""
            stripped = text.strip()
            # GDELT returns plain-text 200s for rate-limits AND invalid queries
            # ("Please limit requests…", "Your query contained an invalid NEAR
            # search…"). Any 200 body that is non-empty and does NOT look like
            # JSON is a STRUCTURAL FAILURE — not an honest empty fetch.
            try:
                payload = resp.json()
            except Exception:
                if stripped.startswith("{") or stripped.startswith("["):
                    try:
                        payload = json.loads(stripped)
                    except Exception:
                        logger.debug(f"gdelt_feed: {key} unparseable JSON body")
                        any_failure = True
                        continue
                else:
                    # Non-JSON plain-text body (rate-limit / invalid-query / etc.)
                    # → failure. Do NOT fall through to empty, do NOT cache.
                    logger.debug(
                        f"gdelt_feed: {key} non-JSON 200 body "
                        f"({stripped[:60]!r}) — treated as failure"
                    )
                    any_failure = True
                    continue

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
        # Real JSON fetch(es) succeeded but genuinely returned zero articles.
        _stamp("empty", "chokepoint_events", 0)
        return ([], "empty")
    if any_failure:
        # Everything failed / offline / structurally-bad — honestly unavailable.
        _stamp("failed", "chokepoint_events", 0)
        return ([], "unavailable")
    # No chokepoints had coords / a mapped country / nothing attempted.
    return ([], "unavailable")
