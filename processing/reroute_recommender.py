"""reroute_recommender.py — costed failover / substitute-corridor recommender (R022).

The route optimizer (``routes.optimizer``) scores the 17 fixed registry lanes
for booking *opportunity* but never proposes a SUBSTITUTE when an
origin/dest/chokepoint is stressed. Operators facing a jammed lane or a
disrupted chokepoint need the costed detour, not a re-scored status quo:
*"this corridor is stuck — here are the three substitute lanes that bypass it,
ranked by how much headroom they actually have and what the detour costs."*

This module is the pure, deterministic ranker for that question. Given a
stressed lane (a ``ShippingRoute``) or a stressed chokepoint (a registry key /
name), it:

  1. resolves the stressed lane(s) — directly, or via the chokepoint's
     ``affected_routes`` — and the chokepoint being bypassed (if any);
  2. finds candidate SUBSTITUTE corridors — *other* registry routes serving the
     SAME corridor (matched on BOTH ``origin_region`` AND ``dest_region``), with
     the stressed route(s) excluded, and (for a chokepoint stress) any candidate
     that ALSO transits the stressed chokepoint excluded so every proposal is a
     genuine bypass. The same-origin match is deliberate (R022/F8): a chokepoint
     bypass must be a SAME-ORIGIN like-for-like detour, otherwise the transit-day
     and $/FEU deltas (measured against the single-origin baseline) would be a
     misleading cross-origin comparison. When the registry has no same-origin
     bypass for a stressed chokepoint, the honest result is ``[]`` rather than a
     misleading number;
  3. scores each substitute on FOUR documented axes from REAL upstream signals
     — congestion headroom (its forecast 30-day congestion band vs. the
     stressed baseline), transit-day delta (the time cost of the detour),
     supply-deficit headroom at its ports (less deficit = better), and a
     $/FEU rate delta (the money cost of the detour) — and blends them into a
     composite ``score`` in [0, 1] with a one-line "why this detour" rationale.

HONESTY / determinism contract
------------------------------
* Pure + deterministic: no clock, no RNG, no I/O. Identical inputs → identical
  output. ``generated_at`` is intentionally NOT stamped here (callers add it).
* REAL signals only: headroom comes from the supplied congestion forecasts and
  supply-state map — never fabricated. A substitute with no congestion forecast
  or no supply state degrades to a neutral contribution, transparently, rather
  than inventing headroom.
* Honest empty state: no similar corridor, or every similar corridor is itself
  the stressed lane / also transits the stressed chokepoint → ``[]`` (no
  "viable substitute corridor"), never a fabricated option.
* Never raises: malformed / empty inputs degrade to ``[]``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence


__all__ = [
    "RerouteOption",
    "recommend_reroutes",
    "REROUTE_WEIGHTS",
    "CONGESTION_HEADROOM_WEIGHT",
    "TRANSIT_DELTA_WEIGHT",
    "SUPPLY_HEADROOM_WEIGHT",
    "RATE_DELTA_WEIGHT",
]


# ---------------------------------------------------------------------------
# Documented composite weights
# ---------------------------------------------------------------------------
# A substitute corridor is good when it (a) has spare congestion headroom vs.
# the stressed lane, (b) doesn't cost too many extra transit days, (c) routes
# through ports that aren't themselves short on containers, and (d) doesn't cost
# too much more per FEU. The four sub-scores are each normalised to [0, 1]
# (1 = best) and blended convexly. Weights sum to 1.0.
#
#   * Congestion headroom (0.35) — the PRIMARY signal. The whole point of a
#     reroute is to escape congestion; a substitute with no more headroom than
#     the jammed lane is pointless however cheap. Highest weight.
#   * Transit-day delta (0.25) — the dominant *operational* cost of a detour.
#     Extra sea-days tie up capital, blow ETAs and burn bunker. Weighted just
#     below headroom.
#   * Supply-deficit headroom (0.20) — a corridor through container-short ports
#     can't actually absorb the diverted boxes; penalise deficit ports.
#   * Rate ($/FEU) delta (0.20) — the money cost of the detour. Real but the
#     operator will pay a premium to move stuck cargo, so it trails the
#     operational axes.
CONGESTION_HEADROOM_WEIGHT: float = 0.35
TRANSIT_DELTA_WEIGHT: float = 0.25
SUPPLY_HEADROOM_WEIGHT: float = 0.20
RATE_DELTA_WEIGHT: float = 0.20

REROUTE_WEIGHTS: dict[str, float] = {
    "congestion_headroom": CONGESTION_HEADROOM_WEIGHT,
    "transit_delta": TRANSIT_DELTA_WEIGHT,
    "supply_headroom": SUPPLY_HEADROOM_WEIGHT,
    "rate_delta": RATE_DELTA_WEIGHT,
}

# Normalisation anchors (documented, demo-heuristic).
# ``_TRANSIT_DELTA_SATURATION`` — extra sea-days at/above which the transit
# sub-score bottoms out at 0. A 14-day-longer detour (e.g. Suez → Cape of Good
# Hope on the Asia-Europe run is ~+7-10d; a full lane swap can be more) is a
# severe but not unheard-of penalty.
_TRANSIT_DELTA_SATURATION: float = 14.0

# ``_RATE_DELTA_SATURATION`` — extra $/FEU at/above which the rate sub-score
# bottoms out at 0. $3,000/FEU over the base is a heavy premium for a detour.
_RATE_DELTA_SATURATION: float = 3000.0

# ``_DEFICIT_SATURATION`` — port-days of container deficit at/above which the
# supply sub-score bottoms out at 0. Mirrors the "Critical Deficit" band
# (< -10 days) in ``port_supply_lines._severity_label``, rounded to 12 so the
# very worst ports still separate from merely-deficit ones.
_DEFICIT_SATURATION: float = 12.0

# Neutral congestion baseline used when the stressed lane has no forecast — a
# substitute is then scored on its absolute headroom below this midpoint.
_NEUTRAL_CONGESTION: float = 0.55


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class RerouteOption:
    """One costed substitute corridor for a stressed lane / chokepoint.

    All four sub-scores are surfaced (each in [0, 1], 1 = best) alongside the
    raw signals they were derived from, so the operator can audit the composite
    rather than trust it blind.
    """

    substitute_route_id: str
    substitute_route_name: str
    origin_locode: str
    dest_locode: str

    # ── Raw, real signals ─────────────────────────────────────────────────
    congestion_headroom: float      # stressed_congestion - substitute_congestion (signed)
    substitute_congestion: float    # the substitute's forecast 30-day-high congestion [0, 1]
    extra_transit_days: int         # substitute.transit_days - stressed.transit_days (signed)
    supply_deficit_days: float      # worst (most-negative) deficit across substitute's ports
    extra_cost_usd_feu: float       # substitute $/FEU - stressed $/FEU (signed)

    # ── Normalised sub-scores [0, 1] (1 = best) ───────────────────────────
    congestion_headroom_score: float
    transit_delta_score: float
    supply_headroom_score: float
    rate_delta_score: float

    composite_score: float          # convex blend of the four sub-scores [0, 1]
    rationale: str                  # one-line "why this detour"
    bypasses_chokepoint: str = ""   # chokepoint key bypassed, or "" for a lane swap
    provenance: list[str] = field(default_factory=list)  # which real signals were present


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _route_attr(route: object, name: str, default=None):
    """Read an attribute from a route (dataclass) or dict, defensively."""
    if isinstance(route, Mapping):
        return route.get(name, default)
    return getattr(route, name, default)


def _congestion_high(forecast: object) -> float | None:
    """Pull the forecast 30-day HIGH-edge congestion from a CongestionForecast.

    We use the high edge of the 30-day band (the pessimistic case over the
    planning horizon) so a reroute is judged against the worst plausible state
    of the substitute, not a rosy point estimate. Falls back to the 30-day
    point, then current congestion. Returns None if nothing usable is present.
    """
    if forecast is None:
        return None
    try:
        if isinstance(forecast, Mapping):
            band = forecast.get("band_30d")
            point = forecast.get("predicted_30d")
            current = forecast.get("current_congestion")
        else:
            band = getattr(forecast, "band_30d", None)
            point = getattr(forecast, "predicted_30d", None)
            current = getattr(forecast, "current_congestion", None)
        if band is not None:
            try:
                high = float(band[1])
                if high == high:  # not NaN
                    return _clamp(high)
            except (TypeError, ValueError, IndexError):
                pass
        for candidate in (point, current):
            if candidate is not None:
                val = float(candidate)
                if val == val:
                    return _clamp(val)
    except (TypeError, ValueError):
        return None
    return None


def _supply_deficit_for_route(
    route: object,
    supply: Mapping | None,
) -> tuple[float | None, bool]:
    """Worst (most-negative) supply-deficit-days across the route's two ports.

    Returns ``(worst_deficit, present)`` — ``present`` is True iff at least one
    of the route's ports had a real supply state in ``supply``. ``worst_deficit``
    is the more-negative of the origin/dest deficits (the binding constraint:
    a corridor is only as good as its tightest port), or None when neither port
    is known.
    """
    if not supply:
        return None, False
    deficits: list[float] = []
    for locode in (_route_attr(route, "origin_locode"), _route_attr(route, "dest_locode")):
        if not locode:
            continue
        state = supply.get(locode)
        if state is None:
            continue
        if isinstance(state, Mapping):
            raw = state.get("supply_deficit_days")
        else:
            raw = getattr(state, "supply_deficit_days", None)
        if raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if val == val:  # not NaN
            deficits.append(val)
    if not deficits:
        return None, False
    return min(deficits), True


def _affected_routes_for_chokepoint(key_or_name: str) -> tuple[list[str], str]:
    """Resolve a chokepoint key/name → (affected route ids, canonical key).

    Returns ``([], "")`` when the chokepoint is unknown. Lookup is
    case-insensitive on both the registry key and the human name.
    """
    try:
        from processing.chokepoint_analyzer import CHOKEPOINTS
    except Exception:
        return [], ""
    needle = str(key_or_name).strip().lower()
    for key, cp in CHOKEPOINTS.items():
        if key.lower() == needle or str(getattr(cp, "name", "")).lower() == needle:
            return list(getattr(cp, "affected_routes", []) or []), key
    return [], ""


def _resolve_stress(
    stressed_route_or_chokepoint: object,
    routes_by_id: Mapping[str, object],
) -> tuple[list[object], object | None, str]:
    """Resolve the stress descriptor into concrete stressed routes.

    Returns ``(stressed_routes, primary_stressed_route, chokepoint_key)``:
      * ``stressed_routes``        — every registry route considered stressed
        (one for a lane, possibly several for a chokepoint).
      * ``primary_stressed_route`` — the single route the deltas are measured
        against (the lane itself, or the first affected route for a chokepoint),
        or None if unresolvable.
      * ``chokepoint_key``         — the canonical chokepoint key being bypassed,
        or "" for a plain lane swap.
    """
    # A ShippingRoute-like object (has origin_locode + an id).
    if not isinstance(stressed_route_or_chokepoint, str):
        rid = _route_attr(stressed_route_or_chokepoint, "id")
        if rid is not None and _route_attr(stressed_route_or_chokepoint, "origin_locode") is not None:
            return [stressed_route_or_chokepoint], stressed_route_or_chokepoint, ""
        return [], None, ""

    token = stressed_route_or_chokepoint.strip()
    # 1) A route id?
    if token in routes_by_id:
        route = routes_by_id[token]
        return [route], route, ""
    # 2) A chokepoint key / name?
    affected_ids, cp_key = _affected_routes_for_chokepoint(token)
    stressed = [routes_by_id[rid] for rid in affected_ids if rid in routes_by_id]
    primary = stressed[0] if stressed else None
    return stressed, primary, cp_key


def _routes_through_chokepoint(cp_key: str) -> set[str]:
    """Set of route ids that transit a given chokepoint (its affected_routes)."""
    if not cp_key:
        return set()
    affected, _ = _affected_routes_for_chokepoint(cp_key)
    return set(affected)


def _rate_for_route(route_id: str, rates: Mapping[str, float] | None) -> float | None:
    """Current $/FEU for a route from the supplied rate map, or None."""
    if not rates:
        return None
    raw = rates.get(route_id)
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if val == val else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def recommend_reroutes(
    stressed_route_or_chokepoint: object,
    routes: Sequence[object],
    congestion: Mapping[str, object] | None = None,
    supply: Mapping[str, object] | None = None,
    *,
    rates: Mapping[str, float] | None = None,
    top_n: int = 3,
) -> list[RerouteOption]:
    """Rank costed substitute corridors for a stressed lane or chokepoint.

    Parameters
    ----------
    stressed_route_or_chokepoint:
        Either a ``ShippingRoute`` (the stressed lane), a route id, or a
        chokepoint registry key / name (the stressed passage). For a chokepoint
        the deltas are measured against the first of its ``affected_routes``.
    routes:
        The candidate universe — typically ``routes.route_registry.ROUTES``.
        Each may be a ``ShippingRoute`` or a dict with the same keys
        (``id``, ``name``, ``origin_locode``, ``dest_locode``,
        ``origin_region``, ``dest_region``, ``transit_days``).
    congestion:
        ``{locode: CongestionForecast}`` — REAL forecasts from
        ``processing.congestion_predictor``. The substitute's *destination*
        congestion (30-day band high) is the headroom signal. Missing → the
        substitute's congestion contribution degrades to neutral, transparently.
    supply:
        ``{locode: PortSupplyState}`` — REAL supply states from
        ``processing.port_supply_lines``. The worst (most-negative) deficit
        across a substitute's ports is its supply-headroom signal.
    rates:
        Optional ``{route_id: usd_per_feu}`` current rate map (REAL FBX/FRED
        levels). When absent the rate axis degrades to neutral for every
        candidate (no fabricated $/FEU delta).
    top_n:
        Cap on the number of ranked options returned.

    Returns
    -------
    list[RerouteOption]
        Ranked by composite score descending. Empty when there is no viable
        substitute corridor (honest empty state). Never raises.
    """
    try:
        route_list = [r for r in (routes or []) if r is not None]
        routes_by_id: dict[str, object] = {}
        for r in route_list:
            rid = _route_attr(r, "id")
            if rid is not None:
                routes_by_id[str(rid)] = r

        stressed_routes, primary, cp_key = _resolve_stress(
            stressed_route_or_chokepoint, routes_by_id
        )
        if primary is None:
            return []

        stressed_ids = {
            str(_route_attr(r, "id"))
            for r in stressed_routes
            if _route_attr(r, "id") is not None
        }
        primary_id = str(_route_attr(primary, "id"))

        # Baselines (the stressed lane the deltas are measured against).
        base_origin_region = _route_attr(primary, "origin_region")
        base_dest_region = _route_attr(primary, "dest_region")
        base_transit = _route_attr(primary, "transit_days", 0) or 0
        try:
            base_transit = int(base_transit)
        except (TypeError, ValueError):
            base_transit = 0
        base_rate = _rate_for_route(primary_id, rates)

        # Stressed-lane congestion baseline = high-edge dest congestion of the
        # WORST (most-congested) stressed route, so the headroom is measured
        # against the actual bottleneck. Falls back to the neutral midpoint.
        base_congestion = _NEUTRAL_CONGESTION
        cong_vals: list[float] = []
        for r in stressed_routes:
            dest = _route_attr(r, "dest_locode")
            fc = (congestion or {}).get(dest) if dest else None
            high = _congestion_high(fc)
            if high is not None:
                cong_vals.append(high)
        if cong_vals:
            base_congestion = max(cong_vals)

        # Routes that also transit the stressed chokepoint must be excluded so
        # every proposal genuinely bypasses it.
        through_cp = _routes_through_chokepoint(cp_key)

        options: list[RerouteOption] = []
        for cand in route_list:
            cid = _route_attr(cand, "id")
            if cid is None:
                continue
            cid = str(cid)
            # Exclude the stressed lane(s) themselves.
            if cid in stressed_ids:
                continue
            # For a chokepoint stress, exclude candidates that also transit it.
            if cp_key and cid in through_cp:
                continue
            # Candidate must serve a SIMILAR corridor. In BOTH the lane-stress
            # and chokepoint-stress cases we require the SAME origin AND dest
            # region, so the transit-day + $/FEU deltas are a true like-for-like
            # comparison against the single-origin baseline.
            #   * Lane stress (no chokepoint): a like-for-like swap — same
            #     ORIGIN and DEST region (e.g. Asia→Europe ↔ Asia→Europe).
            #   * Chokepoint stress: a same-origin DETOUR that reaches the same
            #     destination market via a DIFFERENT passage (bypassing the
            #     stressed chokepoint, already enforced by the through_cp
            #     exclusion above). We deliberately keep the SAME origin region:
            #     a cross-origin candidate would make the transit/$ deltas vs a
            #     single-origin baseline misleading (R022/F8). The honest
            #     comparison is a true like-for-like detour from the same origin.
            if (
                _route_attr(cand, "origin_region") != base_origin_region
                or _route_attr(cand, "dest_region") != base_dest_region
            ):
                continue

            opt = _score_substitute(
                cand=cand,
                base_congestion=base_congestion,
                base_transit=base_transit,
                base_rate=base_rate,
                congestion=congestion or {},
                supply=supply,
                rates=rates,
                cp_key=cp_key,
                stressed_name=str(_route_attr(primary, "name", primary_id)),
            )
            if opt is not None:
                options.append(opt)

        # Deterministic ranking: composite desc, then route id for tie-break.
        options.sort(key=lambda o: (-o.composite_score, o.substitute_route_id))
        n = max(0, int(top_n)) if top_n is not None else len(options)
        return options[:n]
    except Exception:
        # Pure + never-raises contract: any unexpected shape degrades to [].
        return []


def _score_substitute(
    *,
    cand: object,
    base_congestion: float,
    base_transit: int,
    base_rate: float | None,
    congestion: Mapping[str, object],
    supply: Mapping[str, object] | None,
    rates: Mapping[str, float] | None,
    cp_key: str,
    stressed_name: str,
) -> RerouteOption | None:
    """Score one candidate substitute corridor. Returns None on bad shape."""
    cid = str(_route_attr(cand, "id"))
    cname = str(_route_attr(cand, "name", cid))
    origin = str(_route_attr(cand, "origin_locode", "") or "")
    dest = str(_route_attr(cand, "dest_locode", "") or "")
    provenance: list[str] = []

    # ── 1. Congestion headroom ───────────────────────────────────────────
    # headroom = stressed_baseline - substitute_congestion (positive = better).
    sub_cong_high = _congestion_high(congestion.get(dest)) if dest else None
    if sub_cong_high is not None:
        substitute_congestion = sub_cong_high
        provenance.append("congestion")
    else:
        # No real forecast → assume the substitute sits at the neutral baseline
        # (no fabricated advantage), so headroom collapses toward 0.
        substitute_congestion = _NEUTRAL_CONGESTION
    headroom = base_congestion - substitute_congestion
    # Map signed headroom [-1, 1] → [0, 1]; 0.5 = no headroom, 1 = full relief.
    congestion_headroom_score = _clamp(0.5 + headroom * 0.5)

    # ── 2. Transit-day delta ─────────────────────────────────────────────
    cand_transit = _route_attr(cand, "transit_days", base_transit) or base_transit
    try:
        cand_transit = int(cand_transit)
    except (TypeError, ValueError):
        cand_transit = base_transit
    extra_days = cand_transit - base_transit
    provenance.append("transit")
    if extra_days <= 0:
        # Same or faster — full marks (a faster bypass is a strict win).
        transit_delta_score = 1.0
    else:
        transit_delta_score = _clamp(1.0 - extra_days / _TRANSIT_DELTA_SATURATION)

    # ── 3. Supply-deficit headroom ───────────────────────────────────────
    worst_deficit, supply_present = _supply_deficit_for_route(cand, supply)
    if supply_present and worst_deficit is not None:
        provenance.append("supply")
        deficit_magnitude = max(0.0, -worst_deficit)  # only deficits penalise
        supply_headroom_score = _clamp(1.0 - deficit_magnitude / _DEFICIT_SATURATION)
        reported_deficit = worst_deficit
    else:
        # No supply state → neutral (don't reward or punish unknown ports).
        supply_headroom_score = 0.5
        reported_deficit = 0.0

    # ── 4. Rate ($/FEU) delta ────────────────────────────────────────────
    cand_rate = _rate_for_route(cid, rates)
    if cand_rate is not None and base_rate is not None:
        provenance.append("rate")
        extra_cost = cand_rate - base_rate
        if extra_cost <= 0:
            rate_delta_score = 1.0  # cheaper detour is a strict win
        else:
            rate_delta_score = _clamp(1.0 - extra_cost / _RATE_DELTA_SATURATION)
    else:
        # No real rate pair → neutral (no fabricated $/FEU delta).
        rate_delta_score = 0.5
        extra_cost = 0.0

    # ── Composite ────────────────────────────────────────────────────────
    composite = (
        CONGESTION_HEADROOM_WEIGHT * congestion_headroom_score
        + TRANSIT_DELTA_WEIGHT * transit_delta_score
        + SUPPLY_HEADROOM_WEIGHT * supply_headroom_score
        + RATE_DELTA_WEIGHT * rate_delta_score
    )
    composite = _clamp(composite)

    rationale = _build_rationale(
        cname=cname,
        headroom=headroom,
        extra_days=extra_days,
        worst_deficit=reported_deficit if supply_present else None,
        extra_cost=extra_cost if (cand_rate is not None and base_rate is not None) else None,
        cp_key=cp_key,
        stressed_name=stressed_name,
    )

    return RerouteOption(
        substitute_route_id=cid,
        substitute_route_name=cname,
        origin_locode=origin,
        dest_locode=dest,
        congestion_headroom=round(headroom, 4),
        substitute_congestion=round(substitute_congestion, 4),
        extra_transit_days=extra_days,
        supply_deficit_days=round(reported_deficit, 2),
        extra_cost_usd_feu=round(extra_cost, 2),
        congestion_headroom_score=round(congestion_headroom_score, 4),
        transit_delta_score=round(transit_delta_score, 4),
        supply_headroom_score=round(supply_headroom_score, 4),
        rate_delta_score=round(rate_delta_score, 4),
        composite_score=round(composite, 4),
        rationale=rationale,
        bypasses_chokepoint=cp_key,
        provenance=provenance,
    )


def _build_rationale(
    *,
    cname: str,
    headroom: float,
    extra_days: int,
    worst_deficit: float | None,
    extra_cost: float | None,
    cp_key: str,
    stressed_name: str,
) -> str:
    """One-line 'why this detour' summary from the real deltas."""
    parts: list[str] = []

    if headroom > 0.05:
        parts.append(f"{headroom*100:+.0f}pp congestion headroom")
    elif headroom < -0.05:
        parts.append(f"{headroom*100:+.0f}pp MORE congested")
    else:
        parts.append("similar congestion")

    if extra_days > 0:
        parts.append(f"+{extra_days}d transit")
    elif extra_days < 0:
        parts.append(f"{extra_days}d transit (faster)")
    else:
        parts.append("same transit")

    if worst_deficit is not None:
        if worst_deficit < -3:
            parts.append(f"ports short {worst_deficit:.0f}d on boxes")
        elif worst_deficit > 3:
            parts.append(f"ports surplus {worst_deficit:+.0f}d boxes")

    if extra_cost is not None:
        if extra_cost > 0:
            parts.append(f"+${extra_cost:,.0f}/FEU")
        elif extra_cost < 0:
            parts.append(f"-${abs(extra_cost):,.0f}/FEU (cheaper)")

    if cp_key:
        head = f"Bypass {cp_key} via {cname}"
    else:
        head = f"Substitute {stressed_name} with {cname}"
    return f"{head}: " + ", ".join(parts) + "."
