"""alert_correlator.py — group related alerts into incidents.

Read-side analytics companion to ``engine.alert_engine_v2``. The v2
engine fires individual alerts the moment a condition trips; with the
detection thresholds tuned aggressively a single underlying event (a
BDI spike, a port closure, a carrier earnings miss) can fan out into
N rows across BDI_MOVE + RATE_SURGE on several routes + STOCK_MOVE on
shipping equities, all within the same 30-minute window. To the
operator that is ONE incident, not N. This module collapses those
related rows into ``AlertIncident`` groups at READ time so the
algorithm can evolve (graph-based correlation, ML clustering) without
re-processing historical alert rows.

Why read-side
-------------
* No schema change — no incident_id column, no migration, no
  back-fill. The grouping is recomputed each call from the raw alert
  rows already in the table.
* The algorithm is intentionally simple (greedy time-bucketed cluster
  + entity/alert_type relatedness). Swapping it for something fancier
  (a graph cut, a learned similarity metric) is a one-function change
  inside ``correlate_alerts`` — the public surface stays the same.
* Dedup at write time (``alert_engine_v2.save_alerts``) and grouping
  at read time (this module) are orthogonal. Dedup collapses
  near-identical re-fires of the SAME row; correlation groups
  DIFFERENT rows that fired together because of the same underlying
  cause.

Severity ordering
-----------------
Matches ``engine.alert_engine_v2._SEVERITY_ORDER`` exactly: CRITICAL
< HIGH < MEDIUM < LOW. ``severity_max`` on an incident is the most
severe (i.e. lowest numeric order) severity present in its alerts.
The import is re-exported below so callers and tests don't need to
reach into the v2 module for the constant.

Determinism
-----------
``incident_id`` is a SHA-1 hash of (started_at, sorted alert_ids) so
the same set of alerts always produces the same id across runs.
Sorting the alert_ids is what gives the determinism — otherwise the
hash would shift with whatever order ``correlate_alerts`` happened to
visit the alerts in. The hash is truncated to 16 hex chars (64 bits of
entropy) — plenty for an in-app correlation id, not a security
primitive.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from engine.alert_engine_v2 import (
    ShippingAlert,
    _SEVERITY_ORDER,
    load_alerts,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AlertIncident:
    """A group of related alerts that fired in close succession.

    Fields:

    * ``incident_id`` — deterministic SHA-1-based id derived from
      ``started_at`` and the sorted alert_ids in this incident. Same
      alerts → same id across runs.
    * ``started_at`` — ISO timestamp of the EARLIEST alert in the
      incident. Anchors the incident on the timeline.
    * ``severity_max`` — the most severe severity present in this
      group (CRITICAL > HIGH > MEDIUM > LOW). When the group contains
      mixed severities the worst one wins.
    * ``alert_count`` — len(alerts). Surfaced as a top-level field for
      the UI badge ("3 alerts grouped").
    * ``dominant_alert_type`` — the most common alert_type within the
      incident. Ties are broken alphabetically (so the same tied set
      always picks the same winner). Used by ``get_incident_summary``
      for the breakdown.
    * ``alerts`` — the underlying ShippingAlert objects, in the order
      they were added to the incident (chronological by created_at).
    * ``entities_touched`` — small dict with the unique tickers /
      route_ids / port_locodes the incident touched. Always has the
      three keys (``tickers`` / ``routes`` / ``ports``) for stable
      shape; values are sorted lists.
    """
    incident_id: str
    started_at: str
    severity_max: str
    alert_count: int
    dominant_alert_type: str
    alerts: list[ShippingAlert] = field(default_factory=list)
    entities_touched: dict[str, list[str]] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_iso(s: str) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp; tolerate trailing 'Z' shorthand.

    Returns ``None`` on any parse failure so callers can skip rows
    without crashing the aggregation. Mirrors the same helper in
    ``engine.alert_analytics`` — kept local to avoid a cross-module
    import that would tie this module's lifecycle to that one.
    """
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _alert_entities(a: ShippingAlert) -> set[tuple[str, str]]:
    """Return the set of (kind, value) entity tuples for an alert.

    Empty fields are skipped — an alert that doesn't carry a ticker
    contributes no ticker entity to the set. The tuple shape lets
    ``correlate_alerts`` test relatedness with a single set
    intersection regardless of which entity kind matches.
    """
    out: set[tuple[str, str]] = set()
    if a.ticker:
        out.add(("ticker", a.ticker))
    if a.route_id:
        out.add(("route", a.route_id))
    if a.port_locode:
        out.add(("port", a.port_locode))
    return out


def _incident_id(started_at: str, alert_ids: list[str]) -> str:
    """Hash an incident shape into a stable 16-hex-char id.

    The hash inputs are joined with ``|`` so a stray ``|`` in an
    alert_id can't smuggle a separator. We sort alert_ids first so
    the visit order in ``correlate_alerts`` doesn't leak into the id.
    SHA-1 is fine here — this is an in-app correlation id, not a
    security primitive — and the first 16 hex chars give 64 bits of
    entropy, plenty to avoid collisions in a 500-row alert table.
    """
    payload = "|".join([started_at, *sorted(alert_ids)])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _build_incident(group: list[ShippingAlert]) -> Optional[AlertIncident]:
    """Build an AlertIncident from a non-empty list of related alerts.

    Returns ``None`` for an empty group so the caller can filter it
    out cleanly. The group is expected to be in chronological order
    (earliest first) since the cluster loop walks the input that way;
    we still sort defensively by created_at to pick ``started_at``.
    """
    if not group:
        return None

    # Sort defensively so started_at and the hash are stable even if
    # the caller hands us a group in some other order.
    ordered = sorted(group, key=lambda a: a.created_at)
    started_at = ordered[0].created_at

    # severity_max = lowest numeric _SEVERITY_ORDER (CRITICAL=0).
    # min() over the ranks, then map back to the severity string by
    # picking the first alert whose severity ranks at that minimum.
    sev_ranks = [_SEVERITY_ORDER.get(a.severity, 99) for a in ordered]
    min_rank = min(sev_ranks)
    severity_max = next(
        (a.severity for a in ordered
         if _SEVERITY_ORDER.get(a.severity, 99) == min_rank),
        ordered[0].severity,
    )

    # dominant_alert_type = highest count; ties broken alphabetically
    # so the same input set always picks the same winner.
    counts = Counter(a.alert_type for a in ordered)
    top_n = max(counts.values())
    dominant = sorted(k for k, v in counts.items() if v == top_n)[0]

    # entities_touched: stable keys, sorted unique values.
    tickers = sorted({a.ticker for a in ordered if a.ticker})
    routes = sorted({a.route_id for a in ordered if a.route_id})
    ports = sorted({a.port_locode for a in ordered if a.port_locode})
    entities = {"tickers": tickers, "routes": routes, "ports": ports}

    return AlertIncident(
        incident_id=_incident_id(started_at, [a.alert_id for a in ordered]),
        started_at=started_at,
        severity_max=severity_max,
        alert_count=len(ordered),
        dominant_alert_type=dominant,
        alerts=ordered,
        entities_touched=entities,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def correlate_alerts(
    alerts: list[ShippingAlert],
    *,
    window_minutes: int = 30,
) -> list[AlertIncident]:
    """Group alerts into incidents using a greedy time-bucketed cluster.

    Algorithm
    ---------
    1. Sort alerts by ``created_at`` ascending.
    2. Walk through, maintaining one "open" incident at a time.
    3. For each new alert, it JOINS the open incident iff:
       * its ``created_at`` is within ``window_minutes`` of the
         most-recent alert already in the open incident, AND
       * it shares at least one entity (ticker / route_id / port_locode)
         OR alert_type with the open incident.
       Otherwise the open incident is CLOSED and a new one begins.
    4. After the walk, any non-empty open incident is emitted.

    The "within window of the most-recent member" rule (not the
    incident's start) is what lets a long-running storm of related
    alerts stay grouped — each new fire resets the clock, so a
    3-hour stream of related alerts becomes one big incident as long
    as gaps between consecutive alerts stay under ``window_minutes``.

    Never raises. Returns ``[]`` on empty input or any error.
    """
    if not alerts:
        return []

    try:
        # Defensive sort — callers may pass alerts in any order
        # (load_alerts returns newest-first; we want oldest-first for
        # the greedy walk).
        ordered = sorted(alerts, key=lambda a: a.created_at)

        # The "most recent" timestamp in the open incident, parsed
        # once and updated as alerts join. None means "no open
        # incident yet".
        open_group: list[ShippingAlert] = []
        open_last_dt: Optional[datetime] = None
        open_entities: set[tuple[str, str]] = set()
        open_types: set[str] = set()

        incidents: list[AlertIncident] = []
        window_seconds = max(0, int(window_minutes)) * 60

        for a in ordered:
            a_dt = _parse_iso(a.created_at)
            if a_dt is None:
                # Unparseable timestamp — skip rather than fail the
                # whole correlation pass. These alerts simply do not
                # get grouped (and don't break the open incident either).
                continue

            if not open_group:
                # Start a fresh incident.
                open_group = [a]
                open_last_dt = a_dt
                open_entities = _alert_entities(a)
                open_types = {a.alert_type}
                continue

            # Within window?
            assert open_last_dt is not None  # narrowing for type-checkers
            time_close = (a_dt - open_last_dt).total_seconds() <= window_seconds

            # Related? Shares ≥1 entity OR shares alert_type.
            a_entities = _alert_entities(a)
            related = bool(a_entities & open_entities) or (a.alert_type in open_types)

            if time_close and related:
                # Join the open incident.
                open_group.append(a)
                open_last_dt = a_dt
                open_entities |= a_entities
                open_types.add(a.alert_type)
            else:
                # Close the open incident and start a new one.
                inc = _build_incident(open_group)
                if inc is not None:
                    incidents.append(inc)
                open_group = [a]
                open_last_dt = a_dt
                open_entities = _alert_entities(a)
                open_types = {a.alert_type}

        # Flush whatever's still open at the end of the walk.
        if open_group:
            inc = _build_incident(open_group)
            if inc is not None:
                incidents.append(inc)

        return incidents
    except Exception as exc:
        logger.warning(f"correlate_alerts failed: {exc}")
        return []


def get_recent_incidents(
    window_days: int = 7,
    *,
    user_id: Optional[str] = None,
) -> list[AlertIncident]:
    """Load alerts via ``load_alerts`` and correlate them into incidents.

    Returned newest-first by ``started_at`` so the UI shows the most
    recent incident at the top — matches the ordering used by every
    other "recent" list in the app.

    Never raises. Returns ``[]`` on any error.
    """
    try:
        # Coerce window_days defensively — a non-int upstream value
        # would otherwise propagate into load_alerts and timedelta.
        try:
            window_days = int(window_days) if window_days is not None else 7
        except (TypeError, ValueError):
            window_days = 7
        if window_days <= 0:
            return []

        alerts = load_alerts(max_age_days=window_days, user_id=user_id)
        incidents = correlate_alerts(alerts)
        # correlate_alerts returns chronological (oldest-first); the
        # UI wants newest-first. Sort by started_at descending.
        incidents.sort(key=lambda inc: inc.started_at, reverse=True)
        return incidents
    except Exception as exc:
        logger.warning(f"get_recent_incidents failed: {exc}")
        return []


def get_incident_summary(window_days: int = 7) -> dict[str, Any]:
    """Aggregate a one-shot summary of incident activity over the window.

    Returns a dict with the same keys regardless of input size — the
    UI can render the panel without conditional shape checks:

    * ``n_incidents`` — number of distinct incidents in the window.
    * ``n_total_alerts`` — total alert rows ACROSS all incidents
      (i.e. the alert volume the incidents represent).
    * ``avg_alerts_per_incident`` — n_total_alerts / n_incidents,
      or 0.0 when n_incidents is 0 (no divide-by-zero exposure).
    * ``largest_incident_size`` — max(alert_count) across incidents,
      or 0 when empty.
    * ``breakdown_by_dominant_type`` — dict[alert_type → incident
      count]. Counts INCIDENTS not alerts (an incident dominated by
      RATE_SURGE counts as 1 regardless of how many RATE_SURGE
      alerts it contains).

    Never raises. Returns a zeroed shape on any error.
    """
    zero = {
        "n_incidents": 0,
        "n_total_alerts": 0,
        "avg_alerts_per_incident": 0.0,
        "largest_incident_size": 0,
        "breakdown_by_dominant_type": {},
    }
    try:
        incidents = get_recent_incidents(window_days=window_days)
        if not incidents:
            return zero
        n_inc = len(incidents)
        n_alerts = sum(inc.alert_count for inc in incidents)
        largest = max(inc.alert_count for inc in incidents)
        avg = n_alerts / n_inc if n_inc > 0 else 0.0
        # Sorted dict so the UI rendering is deterministic.
        breakdown_counter = Counter(inc.dominant_alert_type for inc in incidents)
        breakdown = dict(sorted(breakdown_counter.items()))
        return {
            "n_incidents": n_inc,
            "n_total_alerts": n_alerts,
            "avg_alerts_per_incident": avg,
            "largest_incident_size": largest,
            "breakdown_by_dominant_type": breakdown,
        }
    except Exception as exc:
        logger.warning(f"get_incident_summary failed: {exc}")
        return zero
