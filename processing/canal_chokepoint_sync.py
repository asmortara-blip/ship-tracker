"""Wire live canal-transit data into the chokepoint registry (rec R007).

The chokepoint component is the TOP shipping-stress-index weight (~0.29), yet the
Suez and Panama nodes' risk levels were 100% hardcoded — while a real canal feed
(``data.canal_feed``) existed but only painted two UI tabs. This maps live canal
status + capacity utilization onto the two canal chokepoints' state, flipping the
top SSI weight's two canal nodes from hardcoded to observed transit.

HONESTY: the overlay is applied ONLY when the canal feed is REAL (a live scrape,
``CanalStats.is_synthetic == False``). A synthetic / modeled fallback leaves the
hardcoded baseline untouched and is reported as ``"modeled"`` — a modeled
fallback is never presented as observed transit. The overlay reassigns the
registry entry to a NEW ``Chokepoint`` (``dataclasses.replace``), so it is
idempotent and the identity-based lookups in ``chokepoint_analyzer`` stay
consistent. ``apply_live_canal_state`` returns a per-canal realness marker so
callers can label the result real-vs-modeled.
"""
from __future__ import annotations

from dataclasses import replace

from processing.chokepoint_analyzer import CHOKEPOINTS

# Which canal (CanalStats.canal) feeds which chokepoint-registry key.
_CANAL_TO_KEY = {"suez": "suez", "panama": "panama"}


def map_canal_to_chokepoint(stats) -> dict:
    """Map a live :class:`~data.canal_feed.CanalStats` → chokepoint state fields.

    Returns ``{current_risk_level, current_disruption_type, daily_vessels}``
    derived from the observed status, capacity utilization, and transit count:

    - status ``Disrupted`` → CRITICAL, ``Restricted`` → HIGH;
    - otherwise high utilization (>90%) → MODERATE (congestion), else LOW;
    - the disruption cause is canal-specific when stressed (Suez = conflict in
      the Red Sea approaches, Panama = drought/water-level), CONGESTION when
      merely full, else NONE.
    """
    status = (getattr(stats, "status", "") or "").strip().lower()
    util = float(getattr(stats, "capacity_utilization_pct", 0.0) or 0.0)
    canal = (getattr(stats, "canal", "") or "").strip().lower()

    if status == "disrupted":
        risk = "CRITICAL"
    elif status == "restricted":
        risk = "HIGH"
    elif util > 90.0:
        risk = "MODERATE"
    else:
        risk = "LOW"

    if status in ("disrupted", "restricted"):
        disruption = "ACTIVE_CONFLICT" if canal == "suez" else "WEATHER"
    elif util > 90.0:
        disruption = "CONGESTION"
    else:
        disruption = "NONE"

    return {
        "current_risk_level": risk,
        "current_disruption_type": disruption,
        "daily_vessels": int(max(0, getattr(stats, "daily_transits", 0) or 0)),
    }


def apply_live_canal_state(stats_list, *, registry=None) -> dict:
    """Overlay REAL canal stats onto the chokepoint registry's canal nodes.

    For each :class:`CanalStats` that is NOT synthetic, reassign the matching
    registry entry (``suez`` / ``panama``) to a ``Chokepoint`` carrying the
    mapped live state. Synthetic / unmatched stats are skipped — the hardcoded
    baseline stands. Idempotent (reapplying the same live state is a no-op).

    Returns a realness marker per canal::

        {canal: {"realness": "live"|"modeled", "risk_level": ..., "status": ...}}
    """
    reg = CHOKEPOINTS if registry is None else registry
    marker: dict[str, dict] = {}
    for stats in stats_list or []:
        canal = (getattr(stats, "canal", "") or "").strip().lower()
        key = _CANAL_TO_KEY.get(canal)
        if key is None or key not in reg:
            continue
        if bool(getattr(stats, "is_synthetic", True)):
            # Modeled fallback: do NOT overlay; report it honestly as modeled.
            marker[canal] = {
                "realness": "modeled",
                "risk_level": reg[key].current_risk_level,
                "status": getattr(stats, "status", ""),
            }
            continue
        fields = map_canal_to_chokepoint(stats)
        reg[key] = replace(
            reg[key],
            current_risk_level=fields["current_risk_level"],
            current_disruption_type=fields["current_disruption_type"],
            daily_vessels=fields["daily_vessels"],
        )
        marker[canal] = {
            "realness": "live",
            "risk_level": fields["current_risk_level"],
            "status": getattr(stats, "status", ""),
        }
    return marker
