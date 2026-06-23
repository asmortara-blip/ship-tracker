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
# ONLY. Suez + Panama are handled by ``apply_live_canal_nodes`` (the precedence
# resolver), NOT this list — keeping the per-strait transit overlay and the
# canal-node resolver on disjoint nodes so two overlays never write the same node.
_STRAIT_KEYWORDS = [
    ("mandeb", "bab_el_mandeb"), ("hormuz", "hormuz"), ("malacca", "malacca"),
    ("gibraltar", "gibraltar"), ("dover", "dover"), ("danish", "danish_straits"),
    ("lombok", "lombok_sunda"), ("sunda", "lombok_sunda"),
]

# PortWatch portname keyword → canal-registry key, for the two canal nodes that
# ``apply_live_canal_nodes`` drives from the real PortWatch chokepoint rows.
_CANAL_KEYWORDS = [("suez", "suez"), ("panama", "panama")]


def _strait_key_for(name: str):
    low = (name or "").lower()
    for kw, key in _STRAIT_KEYWORDS:
        if kw in low:
            return key
    return None


def _canal_key_for(name: str):
    low = (name or "").lower()
    for kw, key in _CANAL_KEYWORDS:
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


def apply_live_canal_nodes(canal_stats, transits, *, registry=None,
                           recent: int = 7, baseline: int = 90) -> dict:
    """Resolve the Suez/Panama chokepoint nodes by SOURCE PRECEDENCE (rec R267):

        real PortWatch transit  >  real canal scrape  >  hardcoded baseline

    PortWatch is the validator-grade daily-transit source and carries real
    Suez/Panama rows; the ``canal_feed`` scrape is brittle HTML regex that
    near-always returns ``is_synthetic=True``, so it is demoted to a real-only
    FALLBACK used only when PortWatch is unavailable. This lights up the two
    highest-leverage chokepoint nodes (the #1 SSI weight) — previously dark,
    because the canal feed owned them yet almost never returned a real scrape.

    ESCALATE-ONLY and ratchet-free for BOTH real tiers: the derived level is
    merged with the PRISTINE baseline (``_BASELINE_RISK``) and only the more
    severe is kept, so a normal/cleared reading returns the node to baseline
    rather than downgrading below it or ratcheting up forever. Reassigns via
    ``dataclasses.replace`` so ``chokepoint_analyzer``'s identity lookups stay
    stable. A present real signal is NEVER overridden by the synthetic feed.

    Disjoint from ``apply_live_chokepoint_transits`` (non-canal straits only), so
    the two overlays never write the same node.

    Returns ``{canal: {"realness", "source": "portwatch"|"canal"|"baseline",
    "risk_level", ...}}`` for each canal that had any input.
    """
    reg = CHOKEPOINTS if registry is None else registry
    rows = getattr(transits, "rows", transits) or []
    basis = getattr(transits, "basis", "real" if rows else "unavailable")
    pw_real = basis == "real" and bool(rows)

    # Canal key → its real PortWatch chokepoint_id (matched by portname).
    pw_id_for: dict[str, str] = {}
    if pw_real:
        for r in rows:
            k = _canal_key_for(r.name)
            if k and k not in pw_id_for:
                pw_id_for[k] = r.chokepoint_id

    # Canal name → its CanalStats scrape (the fallback tier).
    stats_for: dict = {}
    for s in canal_stats or []:
        c = (getattr(s, "canal", "") or "").strip().lower()
        if c in _CANAL_TO_KEY:
            stats_for[c] = s

    marker: dict[str, dict] = {}
    for canal, key in _CANAL_TO_KEY.items():
        if key not in reg:
            continue
        pristine = _BASELINE_RISK.get(key, reg[key].current_risk_level)

        # 1) Real PortWatch transit (top precedence).
        if key in pw_id_for:
            from data.portwatch_feed import transit_drop_ratio
            drop = transit_drop_ratio(rows, pw_id_for[key], recent=recent,
                                      baseline=baseline)
            if drop is not None:
                new_level = _escalate_level(pristine, _transit_drop_to_level(drop))
                if new_level != reg[key].current_risk_level:
                    reg[key] = replace(reg[key], current_risk_level=new_level)
                marker[canal] = {"realness": "live", "source": "portwatch",
                                 "risk_level": new_level,
                                 "transit_drop": round(float(drop), 4)}
                continue

        # 2) Real canal scrape (fallback, escalate-only).
        stats = stats_for.get(canal)
        if stats is not None and not bool(getattr(stats, "is_synthetic", True)):
            fields = map_canal_to_chokepoint(stats)
            new_level = _escalate_level(pristine, fields["current_risk_level"])
            repl = {"current_risk_level": new_level,
                    "daily_vessels": fields["daily_vessels"]}
            if new_level != pristine:   # adopt the cause only when we escalated
                repl["current_disruption_type"] = fields["current_disruption_type"]
            reg[key] = replace(reg[key], **repl)
            marker[canal] = {"realness": "live", "source": "canal",
                             "risk_level": new_level,
                             "status": getattr(stats, "status", "")}
            continue

        # 3) Only a synthetic scrape (or nothing real): leave baseline, report it
        #    honestly as modeled — never present the synthetic feed as observed.
        if stats is not None:
            marker[canal] = {"realness": "modeled", "source": "baseline",
                             "risk_level": reg[key].current_risk_level,
                             "status": getattr(stats, "status", "")}
    return marker
