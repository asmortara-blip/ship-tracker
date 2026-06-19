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

# Pristine hardcoded baseline risk per chokepoint, captured at import BEFORE any
# overlay mutates the registry. Escalate-only overlays merge against THIS (not the
# already-mutated value), so a cleared disruption returns the node to baseline —
# no ratchet.
_BASELINE_RISK: dict = {k: cp.current_risk_level for k, cp in CHOKEPOINTS.items()}

_RISK_RANK = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "CRITICAL": 3}
_RANK_TO_LEVEL = {v: k for k, v in _RISK_RANK.items()}


def _escalate_level(baseline: str, derived: str) -> str:
    """The MORE-severe of two risk levels (never downgrades the baseline)."""
    b = _RISK_RANK.get((baseline or "").upper(), 0)
    d = _RISK_RANK.get((derived or "").upper(), 0)
    return _RANK_TO_LEVEL[max(b, d)]


# PortWatch portname keyword → chokepoint-registry key, for the NON-canal straits
# ONLY. Suez + Panama are deliberately EXCLUDED — they are owned by the live canal
# feed (``apply_live_canal_state``); two overlays writing the same global node
# would fight, so the transit overlay stays off the canal nodes.
_STRAIT_KEYWORDS = [
    ("mandeb", "bab_el_mandeb"), ("hormuz", "hormuz"), ("malacca", "malacca"),
    ("gibraltar", "gibraltar"), ("dover", "dover"), ("danish", "danish_straits"),
    ("lombok", "lombok_sunda"), ("sunda", "lombok_sunda"),
]


def _strait_key_for(name: str):
    low = (name or "").lower()
    for kw, key in _STRAIT_KEYWORDS:
        if kw in low:
            return key
    return None


def _transit_drop_to_level(drop) -> str:
    """Map a recent-vs-baseline transit drop ratio to a derived risk level.

    ~0 = normal flow; a Red-Sea-scale collapse (≈ −70% for Suez in 2024) lands
    well into CRITICAL. ``None`` (too little history) → LOW (no escalation)."""
    if drop is None:
        return "LOW"
    if drop >= 0.50:
        return "CRITICAL"
    if drop >= 0.30:
        return "HIGH"
    if drop >= 0.15:
        return "MODERATE"
    return "LOW"


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


def apply_live_chokepoint_transits(transits, *, registry=None, recent: int = 7,
                                   baseline: int = 90) -> dict:
    """Escalate NON-canal strait risk from a REAL PortWatch transit collapse.

    ESCALATE-ONLY and ratchet-free: for each strait mapped from the feed, the
    recent-vs-baseline transit drop is mapped to a derived risk level and merged
    with the chokepoint's PRISTINE baseline (``_BASELINE_RISK``) — taking the
    more-severe — then reassigned. Merging against the pristine baseline (not the
    already-mutated registry value) means a cleared collapse returns the node to
    baseline rather than ratcheting up forever.

    HONESTY: applied ONLY when the feed is real (``transits.basis == "real"``);
    an unavailable/empty feed is a no-op so the hardcoded baseline stands —
    silence is never turned into a signal. Suez + Panama are SKIPPED (owned by
    the canal overlay). ``transits`` may be a :class:`PortWatchTransits` or a bare
    rows list.

    Returns ``{key: {"realness": "live", "risk_level": ..., "transit_drop": ...}}``
    for each matched strait.
    """
    reg = CHOKEPOINTS if registry is None else registry
    rows = getattr(transits, "rows", transits) or []
    basis = getattr(transits, "basis", "real" if rows else "unavailable")
    marker: dict[str, dict] = {}
    if basis != "real" or not rows:
        return marker

    from data.portwatch_feed import transit_drop_ratio

    name_by_id: dict[str, str] = {}
    for r in rows:
        name_by_id.setdefault(r.chokepoint_id, r.name)

    seen: set = set()
    for cid, nm in name_by_id.items():
        key = _strait_key_for(nm)
        if key is None or key not in reg or key in seen:
            continue
        seen.add(key)
        drop = transit_drop_ratio(rows, cid, recent=recent, baseline=baseline)
        if drop is None:
            continue
        derived = _transit_drop_to_level(drop)
        pristine = _BASELINE_RISK.get(key, reg[key].current_risk_level)
        new_level = _escalate_level(pristine, derived)
        if new_level != reg[key].current_risk_level:
            reg[key] = replace(reg[key], current_risk_level=new_level)
        marker[key] = {"realness": "live", "risk_level": new_level,
                       "transit_drop": round(float(drop), 4)}
    return marker
