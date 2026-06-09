"""AIS integrity — coverage-gap + kinematic-impossibility (spoofing) detection.

The flagship Windward / Kpler "dark activity" signal, in pure form. Two
detectors run over a vessel's position track:

* **Coverage gaps** — a stretch of time with no AIS ping. A gap in open ocean
  is unremarkable (sparse coverage, satellite revisit time). A gap *inside a
  high-risk geofence* — astride a sanctioned-trade chokepoint — is the signal:
  it is consistent with a transponder deliberately going dark to obscure a
  ship-to-ship transfer or a sanctioned port call.
* **Kinematic impossibility (teleports)** — the implied speed between two
  consecutive pings, ``great_circle_distance / time_delta``, exceeds the
  vessel type's physical maximum by a documented margin. No real ship can
  cover the distance in the time, so one of the two positions was spoofed.

Both detectors use the *real* great-circle geometry already in
``data.voyage_dataset`` (``_great_circle_point`` / Haversine) and the real
per-vessel-type speed bands (``_SPEED_BAND``). The math is genuine.

────────────────────────────────────────────────────────────────────────────
HONESTY / PROVENANCE — read before trusting any anomaly this surfaces.

The DETECTION LOGIC here is real (great-circle distance, implied-speed
kinematics, point-in-geofence math). But it runs over the **synthetic**
``data.voyage_dataset`` fleet — modeled voyages on real route geometry, NOT a
live AIS feed. Every anomaly this module returns is therefore *illustrative*:
a demo of the capability on modeled data, NOT a real vessel-tracking finding.
The ``AisAnomaly.provenance`` field is stamped ``MODELED`` and every anomaly
inherits the voyage dataset's modeled provenance. Do NOT present a detected
anomaly as real intelligence about a real ship.
────────────────────────────────────────────────────────────────────────────

Pure, deterministic, and **never raises** — degenerate tracks (a single point,
missing coordinates, NaNs, bad timestamps) yield an empty list, not an error.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

# Real geometry + real speed bands from the (synthetic) voyage dataset.
from data.voyage_dataset import _EARTH_RADIUS_KM, _SPEED_BAND, _great_circle_point

# ── Provenance label ─────────────────────────────────────────────────────────
# Every anomaly carries this so a consumer can never mistake an illustrative
# finding for real vessel-tracking intelligence. The detection math is real;
# the voyages it ran on are synthetic.
MODELED_PROVENANCE = "MODELED"

# Kilometres per nautical mile — to convert the _SPEED_BAND (knots = nm/h)
# into the km/h frame the great-circle distance is computed in.
_KM_PER_NM = 1.852

# ── Documented thresholds ────────────────────────────────────────────────────
# COVERAGE GAP: a stretch with no ping for at least this many hours is a "gap".
# Satellite-AIS revisit time over open ocean is routinely several hours, so a
# conservative 6h floor keeps ordinary sparse coverage from registering. Only
# gaps whose MIDPOINT falls inside a high-risk geofence are reported — a gap in
# open ocean is normal and is suppressed.
_GAP_THRESHOLD_HOURS = 6.0

# TELEPORT (kinematic impossibility): the implied leg speed must exceed the
# vessel-type maximum cruising speed (top of its _SPEED_BAND) by this
# multiplicative margin before we call it impossible. A vessel can transiently
# exceed its cruising band (currents, coarse sampling rounding two close pings
# into a high implied speed), so we demand the implied speed be 1.8x the band
# top. That margin is deliberately conservative: it tolerates real sprint /
# sampling error and only fires on a physically impossible jump.
_TELEPORT_SPEED_MARGIN = 1.8

# Below this leg duration (hours) the implied-speed ratio is too noisy to
# trust — two near-simultaneous pings divided by a tiny dt explode the implied
# speed. Legs shorter than this are skipped for teleport detection (they are
# still spanned for gap detection, which keys off duration not speed).
_TELEPORT_MIN_LEG_HOURS = 0.25  # 15 minutes

# Fallback band top (knots) for an unknown vessel type — generous so an unknown
# type does not produce spurious teleports.
_DEFAULT_SPEED_BAND_TOP = 25.0

# Default high-risk geofence radius (km) when a zone supplies none. Chokepoints
# are narrow; a ~120 km radius brackets the approaches where a dark gap matters
# without flagging the whole ocean.
_DEFAULT_ZONE_RADIUS_KM = 120.0


# ── Public dataclasses ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class AisPing:
    """One AIS position report: a timestamp + a coordinate.

    ``ts`` is a timezone-aware (or naive — treated as UTC) ``datetime``.
    """

    ts: _dt.datetime
    lat: float
    lon: float


@dataclass(frozen=True)
class HighRiskZone:
    """A circular high-risk geofence centred on (lat, lon) with a km radius.

    A coverage gap whose midpoint falls inside this circle is reportable; a gap
    outside every zone is suppressed as ordinary sparse coverage.
    """

    name: str
    lat: float
    lon: float
    radius_km: float = _DEFAULT_ZONE_RADIUS_KM


@dataclass(frozen=True)
class AisAnomaly:
    """One detected AIS-integrity anomaly. MODELED / illustrative provenance."""

    voyage_id: str
    imo: str
    kind: str                      # "GAP" | "TELEPORT"
    severity: str                  # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    reason: str                    # one-line human-readable explanation
    lat: float                     # representative location of the anomaly
    lon: float
    vessel_name: str = ""
    flag: str = ""
    owner_id: str = ""
    # GAP-only: duration of the coverage gap in hours (0.0 for teleports).
    gap_duration_hours: float = 0.0
    # GAP-only: the geofence the gap fell inside ("" for teleports).
    zone_name: str = ""
    # TELEPORT-only: the implied leg speed and the band ceiling it broke.
    implied_speed_kts: float = 0.0
    max_speed_kts: float = 0.0
    # Provenance — always MODELED here (see module docstring).
    provenance: str = MODELED_PROVENANCE


# ── Geometry helpers (real great-circle math) ────────────────────────────────

def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine great-circle distance in kilometres. Never raises.

    Returns 0.0 for any non-finite input rather than propagating a NaN.
    """
    try:
        for v in (lat1, lon1, lat2, lon2):
            if not math.isfinite(v):
                return 0.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = (
            math.sin(dphi / 2.0) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
        )
        a = min(1.0, max(0.0, a))
        return 2.0 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))
    except Exception:
        return 0.0


def _band_top_kts(vessel_type: str) -> float:
    """Top of the vessel-type cruising-speed band (knots)."""
    band = _SPEED_BAND.get(vessel_type)
    if band is None:
        return _DEFAULT_SPEED_BAND_TOP
    return float(band[1])


def _hours_between(a: _dt.datetime, b: _dt.datetime) -> Optional[float]:
    """Signed hours from ``a`` to ``b`` (b - a). ``None`` on bad inputs."""
    try:
        delta = (b - a).total_seconds() / 3600.0
        if not math.isfinite(delta):
            return None
        return delta
    except Exception:
        return None


def _point_in_zone(lat: float, lon: float, zone: HighRiskZone) -> bool:
    """True iff (lat, lon) is within ``zone``'s radius. Never raises."""
    try:
        if not (math.isfinite(lat) and math.isfinite(lon)):
            return False
        radius = zone.radius_km if zone.radius_km > 0 else _DEFAULT_ZONE_RADIUS_KM
        return great_circle_km(lat, lon, zone.lat, zone.lon) <= radius
    except Exception:
        return False


def _midpoint(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """Great-circle midpoint between two coordinates (real slerp)."""
    try:
        return _great_circle_point(lat1, lon1, lat2, lon2, 0.5)
    except Exception:
        return (lat1, lon1)


def _clean_track(track: Sequence) -> list[AisPing]:
    """Coerce + sort a raw track into clean ``AisPing``s. Never raises.

    Drops any ping with a missing/non-finite coordinate or an unusable
    timestamp, then sorts chronologically. A track that reduces to <2 valid
    pings is returned as-is (callers short-circuit on length).
    """
    cleaned: list[AisPing] = []
    if not track:
        return cleaned
    for p in track:
        try:
            ts = getattr(p, "ts", None)
            lat = getattr(p, "lat", None)
            lon = getattr(p, "lon", None)
            if ts is None or lat is None or lon is None:
                continue
            lat = float(lat)
            lon = float(lon)
            if not (math.isfinite(lat) and math.isfinite(lon)):
                continue
            if not isinstance(ts, _dt.datetime):
                continue
            cleaned.append(AisPing(ts=ts, lat=lat, lon=lon))
        except Exception:
            continue
    try:
        cleaned.sort(key=lambda x: x.ts)
    except Exception:
        # Mixed tz-aware/naive timestamps can make sort raise — bail to empty
        # rather than propagate. (Callers build homogeneous tracks.)
        return []
    return cleaned


# ── High-risk zones from the real chokepoint registry ────────────────────────

def default_high_risk_zones() -> list[HighRiskZone]:
    """Build high-risk geofences from the real maritime chokepoint registry.

    A coverage gap near a sanctioned-trade chokepoint (Hormuz, Bab-el-Mandeb,
    Suez, …) is the suspicious case. The chokepoint coordinates are real;
    whether a *modeled* voyage's gap lands there is, of course, illustrative.
    Returns ``[]`` if the registry cannot be imported (never raises).
    """
    zones: list[HighRiskZone] = []
    try:
        from processing.chokepoint_analyzer import CHOKEPOINTS

        for cp in CHOKEPOINTS.values():
            try:
                # Radius scales loosely with the passage width but is floored so
                # a narrow strait still brackets its approaches.
                radius = max(_DEFAULT_ZONE_RADIUS_KM, float(cp.width_km) * 1.5)
                zones.append(
                    HighRiskZone(
                        name=cp.name,
                        lat=float(cp.lat),
                        lon=float(cp.lon),
                        radius_km=radius,
                    )
                )
            except Exception:
                continue
    except Exception:
        return []
    return zones


def _resolve_zones(high_risk_zones: Optional[Iterable]) -> list[HighRiskZone]:
    """Normalise the ``high_risk_zones`` argument to a list of ``HighRiskZone``.

    ``None`` → the default chokepoint-derived zones. An iterable is coerced;
    any malformed entry is skipped. Never raises.
    """
    if high_risk_zones is None:
        return default_high_risk_zones()
    out: list[HighRiskZone] = []
    try:
        for z in high_risk_zones:
            if isinstance(z, HighRiskZone):
                out.append(z)
                continue
            try:
                out.append(
                    HighRiskZone(
                        name=str(getattr(z, "name", "zone")),
                        lat=float(getattr(z, "lat")),
                        lon=float(getattr(z, "lon")),
                        radius_km=float(
                            getattr(z, "radius_km", _DEFAULT_ZONE_RADIUS_KM)
                        ),
                    )
                )
            except Exception:
                continue
    except Exception:
        return []
    return out


# ── Voyage → track reconstruction ────────────────────────────────────────────

def voyage_ping_track(voyage, *, n_pings: int = 12) -> list[AisPing]:
    """Reconstruct a deterministic AIS ping track for a modeled voyage.

    The ``Voyage`` dataclass carries only a *single* current position, so a
    multi-ping track has to be reconstructed from the voyage geometry: pings
    are sampled along the great-circle path from the origin port to the
    voyage's current position, evenly spaced in time between the departure date
    and "now" (departure + elapsed transit).

    This reconstruction is **modeled** — it is the synthetic track the real
    detectors then operate on. It is smooth by construction, so it produces NO
    anomalies on its own; ``assess_voyage`` injects a deterministic,
    per-vessel-stable perturbation (derived from the vessel's stable name key)
    into a fraction of the fleet so the capability has something illustrative to
    surface. See ``_inject_modeled_anomaly``.

    Returns ``[]`` for a voyage with no usable geometry (never raises).
    """
    try:
        from ports.port_registry import PORTS_BY_LOCODE

        origin = PORTS_BY_LOCODE.get(getattr(voyage, "origin_locode", ""))
        cur_lat = float(getattr(voyage, "current_lat"))
        cur_lon = float(getattr(voyage, "current_lon"))
        if origin is None or not (math.isfinite(cur_lat) and math.isfinite(cur_lon)):
            return []
        o_lat, o_lon = float(origin.lat), float(origin.lon)

        departed = getattr(voyage, "departed_at", None)
        if isinstance(departed, _dt.date) and not isinstance(departed, _dt.datetime):
            departed_dt = _dt.datetime(
                departed.year, departed.month, departed.day,
                tzinfo=_dt.timezone.utc,
            )
        elif isinstance(departed, _dt.datetime):
            departed_dt = departed
        else:
            return []

        # Elapsed sailing time: progress along the (delayed) transit, in days,
        # clamped to a sane floor so a just-departed voyage still spans hours.
        nominal = float(getattr(voyage, "nominal_transit_days", 1) or 1)
        delay = float(getattr(voyage, "delay_days", 0.0) or 0.0)
        actual_transit = max(1.0, nominal + delay)
        progress = float(getattr(voyage, "progress_pct", 0.0) or 0.0)
        progress = max(0.0, min(1.0, progress))
        elapsed_days = max(0.25, actual_transit * progress)
        now_dt = departed_dt + _dt.timedelta(days=elapsed_days)

        n = max(2, int(n_pings))
        track: list[AisPing] = []
        for i in range(n):
            frac = i / (n - 1)
            lat, lon = _great_circle_point(o_lat, o_lon, cur_lat, cur_lon, frac)
            ts = departed_dt + (now_dt - departed_dt) * frac
            track.append(AisPing(ts=ts, lat=lat, lon=lon))
        return track
    except Exception:
        return []


def _vessel_key(voyage) -> int:
    """Stable, process-independent integer derived from the vessel identity.

    Reuses the voyage dataset's FNV-1a name hash so the same vessel always
    perturbs the same way — the injected illustrative anomaly is reproducible.
    """
    try:
        from data.voyage_dataset import _vessel_name_key

        seed = getattr(voyage, "imo", "") or getattr(voyage, "vessel_name", "")
        return _vessel_name_key(str(seed))
    except Exception:
        return 0


def _inject_modeled_anomaly(voyage, track: list[AisPing], zones: list[HighRiskZone]):
    """Deterministically perturb a fraction of voyages so the demo has signal.

    The reconstructed track is smooth and never anomalous on its own. To
    illustrate the capability on the synthetic fleet we deterministically
    inject — keyed off the vessel's stable name hash so the SAME vessel always
    behaves identically — one of:

      * a COVERAGE GAP: stretch the time delta of a mid-track leg whose
        midpoint sits inside a high-risk zone (≈1 in 7 vessels that happen to
        transit a zone), OR
      * a TELEPORT: jump one ping far off the path so the implied speed is
        physically impossible (≈1 in 11 vessels).

    This is purely illustrative MODELED behaviour — it is NOT a claim that the
    vessel did anything. Returns the (possibly) modified track.
    """
    if len(track) < 3:
        return track
    key = _vessel_key(voyage)
    if key == 0:
        return track

    # GAP injection: only meaningful if some leg's midpoint is in a zone.
    if zones and (key % 7 == 0):
        # Find the first leg whose midpoint sits inside a high-risk zone.
        for i in range(len(track) - 1):
            a, b = track[i], track[i + 1]
            m_lat, m_lon = _midpoint(a.lat, a.lon, b.lat, b.lon)
            if any(_point_in_zone(m_lat, m_lon, z) for z in zones):
                # Stretch every subsequent ping's timestamp forward by a big
                # offset → the i→i+1 leg becomes a long coverage gap.
                bump = _dt.timedelta(hours=11.0)
                new = track[: i + 1] + [
                    AisPing(ts=p.ts + bump, lat=p.lat, lon=p.lon)
                    for p in track[i + 1 :]
                ]
                return new

    # TELEPORT injection: shove one interior ping far off the great-circle so
    # the implied speed on the leg INTO it is impossible. Offset by ~6 degrees
    # of latitude (~660 km) over a sub-hour modeled leg → clearly impossible.
    if key % 11 == 0:
        j = 1 + (key % (len(track) - 2))  # an interior index, never first/last
        p = track[j]
        # Push north (clamped to a valid latitude) so the jump is large.
        spoof_lat = p.lat + 6.0
        if spoof_lat > 89.0:
            spoof_lat = p.lat - 6.0
        track = list(track)
        track[j] = AisPing(ts=p.ts, lat=spoof_lat, lon=p.lon)
        return track

    return track


# ── Detectors ────────────────────────────────────────────────────────────────

def _voyage_meta(voyage) -> dict:
    """Pull the labeling fields off a voyage (all optional, never raises)."""
    return {
        "voyage_id": str(getattr(voyage, "voyage_id", "") or ""),
        "imo": str(getattr(voyage, "imo", "") or ""),
        "vessel_name": str(getattr(voyage, "vessel_name", "") or ""),
        "flag": str(getattr(voyage, "flag", "") or ""),
        "owner_id": str(getattr(voyage, "owner_id", "") or ""),
        "vessel_type": str(getattr(voyage, "vessel_type", "") or ""),
    }


def detect_gaps(
    voyage,
    *,
    high_risk_zones: Optional[Iterable] = None,
    track: Optional[Sequence] = None,
) -> list[AisAnomaly]:
    """Flag AIS coverage gaps that fall INSIDE a high-risk geofence.

    Scans the voyage's (reconstructed) position track for consecutive pings
    separated by more than ``_GAP_THRESHOLD_HOURS``. A gap is reported only when
    its great-circle midpoint lies inside a high-risk zone — a gap in open ocean
    is normal coverage sparsity and is suppressed. The midpoint is where the
    transponder would have gone dark, so it is the right point to geofence-test.

    ``track`` lets a caller (or a test) supply an explicit list of ``AisPing``
    to scan directly, bypassing the modeled reconstruction — the detection math
    is identical, just on a real (caller-provided) track. When ``None`` the
    voyage's modeled-and-perturbed track is used.

    Severity scales with how far the gap exceeds the threshold. MODELED.
    """
    zones = _resolve_zones(high_risk_zones)
    if track is None:
        track = _with_injection(voyage, high_risk_zones)
    track = _clean_track(track)
    if len(track) < 2 or not zones:
        return []

    meta = _voyage_meta(voyage)
    out: list[AisAnomaly] = []
    for i in range(len(track) - 1):
        a, b = track[i], track[i + 1]
        hrs = _hours_between(a.ts, b.ts)
        if hrs is None or hrs < _GAP_THRESHOLD_HOURS:
            continue
        m_lat, m_lon = _midpoint(a.lat, a.lon, b.lat, b.lon)
        zone = next((z for z in zones if _point_in_zone(m_lat, m_lon, z)), None)
        if zone is None:
            continue
        # Severity by gap magnitude relative to the 6h floor.
        if hrs >= _GAP_THRESHOLD_HOURS * 3:
            sev = "CRITICAL"
        elif hrs >= _GAP_THRESHOLD_HOURS * 2:
            sev = "HIGH"
        else:
            sev = "MEDIUM"
        out.append(
            AisAnomaly(
                voyage_id=meta["voyage_id"],
                imo=meta["imo"],
                kind="GAP",
                severity=sev,
                reason=(
                    f"{hrs:.1f}h AIS coverage gap inside high-risk zone "
                    f"'{zone.name}' — consistent with a transponder going dark "
                    f"near a sanctioned chokepoint (MODELED)."
                ),
                lat=round(m_lat, 4),
                lon=round(m_lon, 4),
                vessel_name=meta["vessel_name"],
                flag=meta["flag"],
                owner_id=meta["owner_id"],
                gap_duration_hours=round(hrs, 2),
                zone_name=zone.name,
            )
        )
    return out


def detect_teleports(
    voyage,
    *,
    track: Optional[Sequence] = None,
) -> list[AisAnomaly]:
    """Flag kinematic impossibility — implied leg speed exceeding vessel max.

    For each consecutive ping pair, implied speed = great-circle distance /
    time delta. When that exceeds the vessel-type cruising-band ceiling by
    ``_TELEPORT_SPEED_MARGIN`` (1.8x), no real ship could have made the leg →
    one of the positions was spoofed. Legs shorter than
    ``_TELEPORT_MIN_LEG_HOURS`` are skipped (their implied speed is too noisy).

    ``track`` lets a caller (or test) supply an explicit ``AisPing`` list to
    scan directly; ``None`` uses the voyage's modeled-and-perturbed track.

    MODELED — the kinematics are real; the voyage is synthetic.
    """
    if track is None:
        track = _with_injection(voyage, None)
    track = _clean_track(track)
    if len(track) < 2:
        return []

    meta = _voyage_meta(voyage)
    band_top = _band_top_kts(meta["vessel_type"])
    limit_kts = band_top * _TELEPORT_SPEED_MARGIN

    out: list[AisAnomaly] = []
    for i in range(len(track) - 1):
        a, b = track[i], track[i + 1]
        hrs = _hours_between(a.ts, b.ts)
        if hrs is None or hrs < _TELEPORT_MIN_LEG_HOURS:
            continue
        dist_km = great_circle_km(a.lat, a.lon, b.lat, b.lon)
        implied_kmh = dist_km / hrs
        implied_kts = implied_kmh / _KM_PER_NM
        if implied_kts <= limit_kts:
            continue
        # Severity by how far past the physical limit the implied speed sits.
        ratio = implied_kts / band_top if band_top > 0 else 0.0
        if ratio >= 4.0:
            sev = "CRITICAL"
        elif ratio >= 2.8:
            sev = "HIGH"
        else:
            sev = "MEDIUM"
        out.append(
            AisAnomaly(
                voyage_id=meta["voyage_id"],
                imo=meta["imo"],
                kind="TELEPORT",
                severity=sev,
                reason=(
                    f"Implied speed {implied_kts:.0f} kts over a {hrs:.1f}h leg "
                    f"({dist_km:.0f} km) exceeds the {meta['vessel_type'] or 'vessel'} "
                    f"max ~{band_top:.0f} kts — kinematically impossible, "
                    f"position-spoof flag (MODELED)."
                ),
                lat=round(b.lat, 4),
                lon=round(b.lon, 4),
                vessel_name=meta["vessel_name"],
                flag=meta["flag"],
                owner_id=meta["owner_id"],
                implied_speed_kts=round(implied_kts, 1),
                max_speed_kts=round(band_top, 1),
            )
        )
    return out


def _with_injection(voyage, high_risk_zones):
    """Build the voyage's (modeled) track and inject the illustrative anomaly.

    Centralised so ``detect_gaps`` / ``detect_teleports`` / ``assess_voyage``
    all see the SAME perturbed track for a given voyage. Never raises.
    """
    track = voyage_ping_track(voyage)
    if not track:
        return []
    zones = _resolve_zones(high_risk_zones)
    return _inject_modeled_anomaly(voyage, track, zones)


def assess_voyage(voyage, *, high_risk_zones: Optional[Iterable] = None) -> list[AisAnomaly]:
    """Run both detectors on a voyage and return the combined anomaly list.

    Pure, deterministic, never raises. A clean voyage → empty list.
    """
    try:
        out: list[AisAnomaly] = []
        out.extend(detect_gaps(voyage, high_risk_zones=high_risk_zones))
        out.extend(detect_teleports(voyage))
        return out
    except Exception:
        return []


def scan_fleet(
    voyages: Iterable,
    *,
    high_risk_zones: Optional[Iterable] = None,
) -> list[AisAnomaly]:
    """Assess every voyage and aggregate all anomalies, severity-sorted.

    Returns anomalies ordered CRITICAL → LOW then by kind, so the worst
    surface first. Pure, deterministic, never raises — a bad element is
    skipped, not fatal. MODELED.
    """
    sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    anomalies: list[AisAnomaly] = []
    try:
        zones = _resolve_zones(high_risk_zones)
        for v in voyages or []:
            try:
                anomalies.extend(assess_voyage(v, high_risk_zones=zones))
            except Exception:
                continue
    except Exception:
        return []
    try:
        anomalies.sort(key=lambda a: (sev_order.get(a.severity, 9), a.kind, a.voyage_id))
    except Exception:
        pass
    return anomalies
