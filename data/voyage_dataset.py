"""Modeled voyage fleet — synthetic in-transit voyages on real route geometry.

The AIS feed (``data/aisstream_feed.py``) reports vessel *position* and
*destination* but has no notion of an origin, a departure time, or voyage
progress. A true *voyage* therefore has to be modeled. This module builds one
voyage object per slot on every canonical route in ``routes/route_registry``,
using the real origin/destination ports (``ports/port_registry``) for geometry
so the fleet agrees with the rest of the platform.

Realism without losing determinism:

* The fleet is seeded from the *current date* — stable within a session,
  refreshes day to day (today = 2026-05-18).
* The delay distribution is **biased toward lanes that are genuinely
  disrupted**: routes touched by an active maritime chokepoint disruption
  (``processing.chokepoint_analyzer``) or a current-season weather alert
  (``processing.weather_risk``) carry materially larger delays.
* Vessel name pools are imported from ``data.aisstream_feed`` rather than
  duplicated.

This is a pure-data module: **no Streamlit imports**. The labeled provenance is
``DataSource.modeled("Modeled Voyage Fleet")``.
"""
from __future__ import annotations

import datetime as _dt
import math
import random
from dataclasses import dataclass

from data.aisstream_feed import (
    _BULK_NAMES,
    _CONTAINER_NAMES,
    _FLAGS,
    _TANKER_NAMES,
)
from data.quality import DataSource
from ports.port_registry import PORTS_BY_LOCODE
from processing.chokepoint_analyzer import get_current_active_disruptions
from processing.weather_risk import (
    compute_weather_adjusted_eta,
    get_current_season_alerts,
)
from routes.route_registry import ROUTES, ROUTES_BY_ID

# Earth radius — kilometres. Used for the great-circle interpolation.
_EARTH_RADIUS_KM = 6371.0

# Per-vessel-type cruising speed band (knots). Container ships run fastest.
_SPEED_BAND: dict[str, tuple[float, float]] = {
    "Container":    (16.0, 22.0),
    "Bulk Carrier": (11.0, 15.0),
    "Tanker":       (12.0, 16.0),
}

# Voyage status thresholds keyed off the modeled delay in days.
_STATUS_ON_TIME_MAX = 1.0      # delay <= 1d  -> "On Schedule"
_STATUS_MINOR_MAX = 3.0        # 1d < delay <= 3d -> "Minor Delay"
# delay > 3d -> "Major Delay"


@dataclass
class Voyage:
    """One modeled in-transit (or arrived) container/bulk/tanker voyage."""

    voyage_id: str
    vessel_name: str
    mmsi: str
    vessel_type: str
    route_id: str
    origin_locode: str
    dest_locode: str
    departed_at: _dt.date
    nominal_transit_days: int
    eta_nominal: _dt.date
    eta_adjusted: _dt.date
    progress_pct: float            # 0.0 - 1.0
    current_lat: float
    current_lon: float
    status: str                    # "On Schedule" | "Minor Delay" | "Major Delay" | "Arrived"
    delay_days: float
    speed_kts: float
    congestion_at_dest: float      # 0.0 - 1.0 modeled congestion at the destination port
    weather_delay_days: float
    chokepoints_on_route: list[str]


# ── Provenance ───────────────────────────────────────────────────────────────

VOYAGE_DATA_SOURCE: DataSource = DataSource.modeled(
    "Modeled Voyage Fleet",
    notes="Synthetic voyages on real route geometry; delays biased by live "
    "chokepoint + weather disruptions.",
)


# ── Great-circle geometry ────────────────────────────────────────────────────

def _great_circle_point(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    frac: float,
) -> tuple[float, float]:
    """Return the (lat, lon) point ``frac`` of the way along the great circle.

    ``frac`` is clamped to [0, 1]. Uses spherical slerp so interpolated map
    waypoints follow the true shortest path rather than a straight line in
    lat/lon space (which would cut through land near the poles).
    """
    frac = max(0.0, min(1.0, frac))
    phi1, lam1 = math.radians(lat1), math.radians(lon1)
    phi2, lam2 = math.radians(lat2), math.radians(lon2)

    # Angular distance between the two points.
    delta = 2.0 * math.asin(
        math.sqrt(
            math.sin((phi2 - phi1) / 2.0) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin((lam2 - lam1) / 2.0) ** 2
        )
    )
    if delta == 0.0:
        return (lat1, lon1)

    a = math.sin((1.0 - frac) * delta) / math.sin(delta)
    b = math.sin(frac * delta) / math.sin(delta)

    x = a * math.cos(phi1) * math.cos(lam1) + b * math.cos(phi2) * math.cos(lam2)
    y = a * math.cos(phi1) * math.sin(lam1) + b * math.cos(phi2) * math.sin(lam2)
    z = a * math.sin(phi1) + b * math.sin(phi2)

    phi = math.atan2(z, math.sqrt(x * x + y * y))
    lam = math.atan2(y, x)
    return (math.degrees(phi), math.degrees(lam))


# ── Disruption bias inputs ───────────────────────────────────────────────────

def _disrupted_route_ids() -> set[str]:
    """Route IDs touched by an active chokepoint disruption.

    Chokepoint ``affected_routes`` lists contain a few IDs that are not in the
    route registry (e.g. ``persian_gulf_lng``); we intersect against
    ``ROUTES_BY_ID`` so only real routes are biased.
    """
    disrupted: set[str] = set()
    try:
        for cp in get_current_active_disruptions():
            for rid in cp.affected_routes:
                if rid in ROUTES_BY_ID:
                    disrupted.add(rid)
    except Exception:
        pass
    return disrupted


def _weather_alert_route_ids() -> set[str]:
    """Route IDs flagged by a current-season weather alert."""
    flagged: set[str] = set()
    try:
        for ev in get_current_season_alerts():
            for rid in ev.affected_routes:
                if rid in ROUTES_BY_ID:
                    flagged.add(rid)
    except Exception:
        pass
    return flagged


# Cache the active-disruption list once per import so build_voyage_fleet does
# not re-query it for every voyage's chokepoint lookup.
try:
    _ACTIVE_DISRUPTIONS_CACHE = list(get_current_active_disruptions())
except Exception:
    _ACTIVE_DISRUPTIONS_CACHE = []


# ── Fleet builder ────────────────────────────────────────────────────────────

def _date_seed() -> int:
    """Deterministic integer seed derived from today's UTC date."""
    today = _dt.datetime.now(_dt.timezone.utc).date()
    return today.year * 10_000 + today.month * 100 + today.day


def _vessel_name_pool(vessel_type: str) -> list[str]:
    """Return the imported aisstream name pool matching a vessel type."""
    if vessel_type == "Bulk Carrier":
        return _BULK_NAMES
    if vessel_type == "Tanker":
        return _TANKER_NAMES
    return _CONTAINER_NAMES


def build_voyage_fleet(
    seed: int | None = None,
    per_route: tuple[int, int] = (4, 9),
) -> list[Voyage]:
    """Build a deterministic fleet of modeled voyages across every route.

    Parameters
    ----------
    seed:
        RNG seed. Defaults to a value derived from the current UTC date so the
        fleet is stable within a day and refreshes day to day.
    per_route:
        ``(min, max)`` inclusive band for the number of voyages generated per
        route.

    Returns
    -------
    list[Voyage]
        One ``Voyage`` per generated slot, ordered route-by-route.
    """
    if seed is None:
        seed = _date_seed()

    disrupted = _disrupted_route_ids()
    weather_flagged = _weather_alert_route_ids()
    today = _dt.datetime.now(_dt.timezone.utc).date()

    lo, hi = per_route
    if hi < lo:
        lo, hi = hi, lo

    fleet: list[Voyage] = []

    for r_idx, route in enumerate(ROUTES):
        origin = PORTS_BY_LOCODE.get(route.origin_locode)
        dest = PORTS_BY_LOCODE.get(route.dest_locode)
        if origin is None or dest is None:
            # Cannot place a voyage without both port coordinates.
            continue

        # Per-route RNG — deterministic but de-correlated between routes.
        rng = random.Random(seed * 1000 + r_idx)

        # How disrupted is this lane? Drives the delay distribution.
        is_choke = route.id in disrupted
        is_weather = route.id in weather_flagged
        # Weather buffer from the real weather model (expected vs nominal days).
        try:
            wx_expected, _wx_worst = compute_weather_adjusted_eta(
                route.id, float(route.transit_days)
            )
            weather_buffer = max(0.0, wx_expected - route.transit_days)
        except Exception:
            weather_buffer = 0.0

        n_voyages = rng.randint(lo, hi)

        for v_idx in range(n_voyages):
            vessel_type = rng.choices(
                ["Container", "Bulk Carrier", "Tanker"],
                weights=[0.70, 0.18, 0.12],
            )[0]
            name_pool = _vessel_name_pool(vessel_type)
            vessel_name = name_pool[rng.randrange(len(name_pool))]

            # MMSI — 9-digit, deterministic per voyage.
            mmsi = str(200_000_000 + (seed + r_idx * 97 + v_idx * 13) % 599_999_999)

            speed_lo, speed_hi = _SPEED_BAND.get(vessel_type, (14.0, 18.0))
            speed_kts = round(rng.uniform(speed_lo, speed_hi), 1)

            # ── Departure: voyage may be anywhere from just-left to arrived ──
            # Allow departures up to (transit + 6d) ago so some voyages have
            # already arrived.
            max_age = route.transit_days + 6
            days_since_departure = rng.randint(0, max_age)
            departed_at = today - _dt.timedelta(days=days_since_departure)

            # ── Delay model — biased by live disruption state ───────────────
            # Base delay: small noise around zero, occasionally negative (early).
            delay = rng.gauss(0.2, 0.7)

            # Weather contribution — present on weather-flagged lanes.
            weather_delay = 0.0
            if is_weather and weather_buffer > 0.0:
                weather_delay = max(
                    0.0, rng.gauss(weather_buffer, weather_buffer * 0.4)
                )
            delay += weather_delay

            # Chokepoint contribution — the dominant driver on disrupted lanes.
            if is_choke:
                # Suez/Bab-el-Mandeb-class reroute: +7-12 days, heavy tail.
                delay += max(0.0, rng.gauss(8.5, 3.0))

            delay = round(max(-2.0, delay), 1)

            nominal_transit = route.transit_days
            actual_transit = max(1.0, nominal_transit + delay)

            eta_nominal = departed_at + _dt.timedelta(days=nominal_transit)
            eta_adjusted = departed_at + _dt.timedelta(
                days=int(round(actual_transit))
            )

            # ── Progress along the actual (delayed) transit ─────────────────
            progress = days_since_departure / actual_transit
            progress = max(0.0, min(1.0, progress))

            if progress >= 1.0:
                status = "Arrived"
            elif delay <= _STATUS_ON_TIME_MAX:
                status = "On Schedule"
            elif delay <= _STATUS_MINOR_MAX:
                status = "Minor Delay"
            else:
                status = "Major Delay"

            cur_lat, cur_lon = _great_circle_point(
                origin.lat, origin.lon, dest.lat, dest.lon, progress
            )

            # Modeled destination-port congestion — higher on disrupted lanes.
            base_congestion = rng.uniform(0.15, 0.55)
            if is_choke:
                base_congestion = min(1.0, base_congestion + rng.uniform(0.2, 0.4))
            if is_weather:
                base_congestion = min(1.0, base_congestion + rng.uniform(0.05, 0.2))
            congestion_at_dest = round(base_congestion, 3)

            # Chokepoints touched by this route (real registry IDs only).
            chokepoints_on_route = sorted(
                cp.name
                for cp in _ACTIVE_DISRUPTIONS_CACHE
                if route.id in cp.affected_routes and route.id in ROUTES_BY_ID
            )

            voyage_id = f"VY-{route.id[:6].upper()}-{v_idx + 1:02d}-{r_idx:02d}"

            fleet.append(
                Voyage(
                    voyage_id=voyage_id,
                    vessel_name=vessel_name,
                    mmsi=mmsi,
                    vessel_type=vessel_type,
                    route_id=route.id,
                    origin_locode=route.origin_locode,
                    dest_locode=route.dest_locode,
                    departed_at=departed_at,
                    nominal_transit_days=nominal_transit,
                    eta_nominal=eta_nominal,
                    eta_adjusted=eta_adjusted,
                    progress_pct=round(progress, 4),
                    current_lat=round(cur_lat, 4),
                    current_lon=round(cur_lon, 4),
                    status=status,
                    delay_days=delay,
                    speed_kts=speed_kts,
                    congestion_at_dest=congestion_at_dest,
                    weather_delay_days=round(weather_delay, 1),
                    chokepoints_on_route=chokepoints_on_route,
                )
            )

    return fleet


# ── Lookup / search helpers ──────────────────────────────────────────────────

def get_voyage(voyage_id: str, fleet: list[Voyage] | None = None) -> Voyage | None:
    """Return the voyage with ``voyage_id`` (case-insensitive), or ``None``.

    When ``fleet`` is not supplied a fresh fleet is built; callers that already
    hold a fleet should pass it to avoid rebuilding.
    """
    if fleet is None:
        fleet = build_voyage_fleet()
    target = voyage_id.strip().upper()
    for v in fleet:
        if v.voyage_id.upper() == target:
            return v
    return None


def search_voyages(
    query: str,
    fleet: list[Voyage] | None = None,
) -> list[Voyage]:
    """Return voyages matching ``query`` across vessel name, ID, MMSI and route.

    Matching is case-insensitive substring. An empty query returns the whole
    fleet so the caller's selectbox can show every voyage by default.
    """
    if fleet is None:
        fleet = build_voyage_fleet()
    q = (query or "").strip().lower()
    if not q:
        return list(fleet)
    return [
        v
        for v in fleet
        if q in v.vessel_name.lower()
        or q in v.voyage_id.lower()
        or q in v.mmsi.lower()
        or q in v.route_id.lower()
        or q in v.origin_locode.lower()
        or q in v.dest_locode.lower()
    ]


def voyage_fleet_summary(fleet: list[Voyage]) -> dict:
    """Aggregate a fleet into headline KPIs for the tracker's metric strip.

    Returns
    -------
    dict with keys:
        total, in_transit, arrived, on_schedule, minor_delay, major_delay,
        delayed, delayed_pct, avg_delay_days, avg_progress_pct,
        avg_speed_kts, disrupted_routes.
    """
    total = len(fleet)
    if total == 0:
        return {
            "total": 0,
            "in_transit": 0,
            "arrived": 0,
            "on_schedule": 0,
            "minor_delay": 0,
            "major_delay": 0,
            "delayed": 0,
            "delayed_pct": 0.0,
            "avg_delay_days": 0.0,
            "avg_progress_pct": 0.0,
            "avg_speed_kts": 0.0,
            "disrupted_routes": 0,
        }

    arrived = sum(1 for v in fleet if v.status == "Arrived")
    on_schedule = sum(1 for v in fleet if v.status == "On Schedule")
    minor = sum(1 for v in fleet if v.status == "Minor Delay")
    major = sum(1 for v in fleet if v.status == "Major Delay")
    delayed = minor + major
    in_transit = total - arrived

    avg_delay = sum(v.delay_days for v in fleet) / total
    avg_progress = sum(v.progress_pct for v in fleet) / total
    avg_speed = sum(v.speed_kts for v in fleet) / total

    # A route counts as disrupted if any of its voyages carries a chokepoint.
    disrupted_routes = len(
        {v.route_id for v in fleet if v.chokepoints_on_route}
    )

    return {
        "total": total,
        "in_transit": in_transit,
        "arrived": arrived,
        "on_schedule": on_schedule,
        "minor_delay": minor,
        "major_delay": major,
        "delayed": delayed,
        "delayed_pct": round(delayed / total * 100.0, 1),
        "avg_delay_days": round(avg_delay, 1),
        "avg_progress_pct": round(avg_progress * 100.0, 1),
        "avg_speed_kts": round(avg_speed, 1),
        "disrupted_routes": disrupted_routes,
    }
