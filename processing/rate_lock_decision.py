"""Contract LOCK / RIDE / SPLIT decision from the real rate forecast (rec R002).

The Booking tab's headline "lock a contract or ride the spot" recommendation was
fabricated from ``random.randint(1800, 3200)`` LTC + ``randint(1400, 4000)``
spot — pure noise dressed as advice, even though a live ML GBR forecast
(``rate_forecaster.forecast_route`` → ``RateForecast``) already exists and was
simply never imported by booking.

This turns that real forecast (direction, 30-day point, direction confidence)
into a defensible verdict:

* **LOCK** — rates forecast Rising with confidence above the floor: lock the
  contract now to avoid paying the higher future spot. Savings ≈ the forecast
  upside per FEU.
* **RIDE** — rates forecast Falling with confidence: ride the spot down. Savings
  ≈ the forecast downside per FEU.
* **SPLIT** — Stable, or confidence below the floor: hedge, tilting the locked
  fraction toward the weak directional signal.

The breakeven is the current rate (locking ≈ today's rate vs riding ≈ the future
spot are indifferent when the future spot equals today's). Everything is grounded
in the forecast and labeled with its confidence — no random rows. Pure +
duck-typed (any object exposing the RateForecast fields works), never raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_DEFAULT_CONFIDENCE_FLOOR: float = 0.55


@dataclass
class RateLockDecision:
    """A LOCK / RIDE / SPLIT verdict for one route, grounded in the forecast."""

    route_id: str
    route_name: str
    verdict: str                       # "LOCK" | "RIDE" | "SPLIT"
    current_rate: float
    forecast_30d: float
    direction: str                     # "Rising" | "Falling" | "Stable"
    confidence: float                  # direction confidence in [0, 1]
    expected_move_pct: float           # (forecast_30d - current) / current
    expected_savings_per_feu: float    # $/FEU advantage of the verdict
    breakeven_rate: float              # lock-vs-ride indifference (= current rate)
    lock_fraction: float               # 1.0 LOCK / 0.0 RIDE / (0,1) SPLIT
    rationale: str = ""


def _decide(
    route_id: str, route_name: str, current: float, forecast_30d: float,
    direction: str, confidence: float, *, confidence_floor: float,
) -> RateLockDecision:
    current = float(current or 0.0)
    forecast_30d = float(forecast_30d or 0.0)
    conf = max(0.0, min(1.0, float(confidence or 0.0)))
    direction = str(direction or "Stable")
    move = forecast_30d - current
    move_pct = (move / current) if current > 0 else 0.0
    breakeven = round(current, 0)
    strong = conf >= confidence_floor

    if direction == "Rising" and strong and move > 0:
        verdict, lock_frac, savings = "LOCK", 1.0, move
        rationale = (
            f"Rates forecast Rising ({move_pct:+.1%}) at {conf:.0%} confidence — "
            f"lock the contract now to avoid ~${move:,.0f}/FEU of upside."
        )
    elif direction == "Falling" and strong and move < 0:
        verdict, lock_frac, savings = "RIDE", 0.0, -move
        rationale = (
            f"Rates forecast Falling ({move_pct:+.1%}) at {conf:.0%} confidence — "
            f"ride the spot to capture ~${-move:,.0f}/FEU of downside."
        )
    else:
        verdict = "SPLIT"
        tilt_sign = 1 if direction == "Rising" else (-1 if direction == "Falling" else 0)
        lock_frac = round(min(0.85, max(0.15, 0.5 + 0.5 * conf * tilt_sign)), 2)
        savings = abs(move) * 0.5
        why = ("signal Stable" if tilt_sign == 0
               else f"{direction} but {conf:.0%} confidence below the "
                    f"{confidence_floor:.0%} floor")
        rationale = (
            f"{why} — split {lock_frac:.0%} locked / {1 - lock_frac:.0%} spot to hedge."
        )

    return RateLockDecision(
        route_id=str(route_id), route_name=str(route_name or route_id),
        verdict=verdict, current_rate=round(current, 0),
        forecast_30d=round(forecast_30d, 0), direction=direction,
        confidence=round(conf, 3), expected_move_pct=round(move_pct, 4),
        expected_savings_per_feu=round(savings, 0), breakeven_rate=breakeven,
        lock_fraction=lock_frac, rationale=rationale,
    )


def decide_rate_lock(
    forecast, *, confidence_floor: float = _DEFAULT_CONFIDENCE_FLOOR,
) -> RateLockDecision:
    """Turn a ``RateForecast`` (or any object with its fields) into a verdict."""
    return _decide(
        getattr(forecast, "route_id", ""),
        getattr(forecast, "route_name", ""),
        getattr(forecast, "current_rate", 0.0),
        getattr(forecast, "forecast_30d", 0.0),
        getattr(forecast, "direction", "Stable"),
        getattr(forecast, "direction_confidence", 0.0),
        confidence_floor=confidence_floor,
    )


def decide_all_rate_locks(
    freight_data: dict,
    macro_data: dict | None = None,
    *,
    route_ids: list | None = None,
    confidence_floor: float = _DEFAULT_CONFIDENCE_FLOOR,
) -> list[RateLockDecision]:
    """Run the REAL ML forecast per route, then decide. Skips routes the model
    can't forecast (no fabricated rows). Never raises.

    ``freight_data`` is ``{route_id -> rate DataFrame}``. ``route_ids`` defaults
    to the keys of ``freight_data`` (only routes with real history are decided).
    """
    out: list[RateLockDecision] = []
    fd = freight_data or {}
    macro = macro_data or {}
    ids = route_ids if route_ids is not None else list(fd.keys())
    try:
        from processing.rate_forecaster import forecast_route
        from routes.route_registry import ROUTES_BY_ID
    except Exception:  # pragma: no cover - defensive
        return out

    for rid in ids:
        rid = str(rid)
        rate_df = fd.get(rid)
        if rate_df is None or getattr(rate_df, "empty", True):
            continue
        route = ROUTES_BY_ID.get(rid)
        name = getattr(route, "name", rid) if route is not None else rid
        try:
            fc = forecast_route(rid, name, rate_df, macro)
        except Exception:
            fc = None
        if fc is None:
            continue
        out.append(decide_rate_lock(fc, confidence_floor=confidence_floor))
    return out
