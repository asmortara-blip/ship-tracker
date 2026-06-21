"""Effective-capacity / "supply destruction by friction" signal.

The shipping-supply read that matters for freight is not nominal fleet size but
EFFECTIVE supply after congestion and rerouting absorb vessel-days and ton-miles.
The key market insight (validated by the OECD/IMF/UNCTAD AIS-intelligence
literature, which prioritizes capacity immobilization over raw counts): when
freight rates RISE while EFFECTIVE supply FALLS, the driver is supply destruction
by friction — congestion + chokepoint diversion — not demand. That distinction
changes the trade.

This synthesizes the SSI's already-computed, real ``congestion`` and
``chokepoint`` components into an effective-supply index + a classified read. It
adds no new feed — it reinterprets signals Ship already produces, honestly (the
weights/thresholds are modeled assumptions, documented below).
"""
from __future__ import annotations

from dataclasses import dataclass


def _clamp01(x: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


@dataclass(frozen=True)
class EffectiveSupply:
    """Effective fleet supply after friction, as a fraction of nominal."""

    effective_supply_pct: float    # [0, 1] of nominal capacity still effective
    drag_pct: float                # 1 - effective (total friction drag)
    congestion_drag: float         # vessel-days absorbed by port congestion
    diversion_drag: float          # ton-mile supply absorbed by chokepoint reroutes


def effective_supply(
    congestion_stress: float,
    chokepoint_stress: float,
    *,
    nominal: float = 1.0,
    congestion_weight: float = 0.5,
    diversion_weight: float = 0.5,
    max_drag: float = 0.6,
) -> EffectiveSupply:
    """Discount nominal capacity by congestion + chokepoint-diversion friction.

    ``congestion_stress`` and ``chokepoint_stress`` are the SSI components in
    [0, 1]. Each contributes a drag (weighted), capped at ``max_drag`` so a
    degenerate all-stressed reading can't claim >60% of the fleet is immobilized
    (the assumed ceiling — these weights/cap are modeled, not measured).
    """
    c = _clamp01(congestion_stress)
    k = _clamp01(chokepoint_stress)
    congestion_drag = congestion_weight * c
    diversion_drag = diversion_weight * k
    drag = min(max_drag, congestion_drag + diversion_drag)
    eff = float(nominal) * (1.0 - drag)
    return EffectiveSupply(
        effective_supply_pct=round(eff, 4),
        drag_pct=round(drag, 4),
        congestion_drag=round(congestion_drag, 4),
        diversion_drag=round(diversion_drag, 4),
    )


@dataclass(frozen=True)
class FrictionRead:
    """The supply-vs-freight read."""

    label: str
    bullish_carriers: bool
    rationale: str


def friction_read(
    drag_pct: float,
    freight_change_pct: float,
    *,
    drag_threshold: float = 0.15,
    freight_threshold: float = 2.0,
) -> FrictionRead:
    """Classify the read from the friction drag and the freight-rate change.

    The headline case: freight UP + high friction drag → supply destruction by
    friction (bullish carriers — tightness is supply-side, not demand). The other
    quadrants are distinguished so a reader doesn't mistake friction for demand.
    """
    high_drag = float(drag_pct) >= drag_threshold
    freight_up = float(freight_change_pct) >= freight_threshold
    freight_down = float(freight_change_pct) <= -freight_threshold

    if freight_up and high_drag:
        return FrictionRead(
            "Supply destruction by friction", True,
            "Freight rising while congestion + chokepoint diversion absorb "
            "effective supply — tightness is friction-driven, supportive of "
            "carrier rates even without demand growth.")
    if freight_up:
        return FrictionRead(
            "Demand-driven tightness", True,
            "Freight rising with low friction drag — demand, not supply "
            "destruction; watch for it to fade if demand cools.")
    if freight_down and high_drag:
        return FrictionRead(
            "Friction easing into soft demand", False,
            "Freight falling despite friction — demand is weak enough to swamp "
            "the supply destruction; bearish for carriers.")
    if freight_down:
        return FrictionRead(
            "Oversupply / soft demand", False,
            "Freight falling with low friction — ample effective supply meeting "
            "soft demand; bearish for carriers.")
    return FrictionRead(
        "Balanced", False,
        "No strong friction or freight signal — effective supply and demand "
        "roughly matched.")
