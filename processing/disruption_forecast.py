"""
Disruption Stress Forecaster
============================
Forecasts shipping disruption / stress *forward* over 7- and 30-day horizons.

This module is **thin orchestration** over forecasters that already exist in the
platform — it does not reinvent any modelling:

  * ``congestion_predictor.predict_congestion`` — congestion trajectory at the
    destination port (drives how the physical bottleneck evolves).
  * ``rate_forecaster.forecast_all_routes`` (ML) with ``forecaster.forecast_all_routes``
    (linear) as a fallback — freight-rate direction per route.
  * ``monte_carlo.simulate_freight_rates`` — the P90 rate tail, used as an
    upside-stress indicator (a fat right tail on rates implies tightening
    capacity, i.e. building stress for shippers).

These signals are blended into 7-day / 30-day stress numbers on a 0-1 scale.

Design notes
------------
* Pure processing module — **no Streamlit imports, no ``st.`` calls.**
* Every public function tolerates empty ``freight_data`` and returns neutral
  defaults rather than crashing. ``stress_7d`` / ``stress_30d`` are always
  floats in ``[0, 1]``.
* The optional ``stress_report`` argument is a ``ShippingStressReport`` produced
  by ``processing/shipping_stress_index.py``. It is treated **duck-typed** and
  is *not* imported at module load time — that module is built in parallel and
  importing it here would risk a circular import. We only read ``.route_stress``
  (a list of items each exposing ``.route_id`` and ``.stress_score``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

from routes.route_registry import ROUTES, ROUTES_BY_ID

# ── Tuning constants ─────────────────────────────────────────────────────────

# Neutral baseline stress used whenever no current-stress signal is available.
_NEUTRAL_STRESS: float = 0.40

# Blend weights for the forward stress projection. They sum to 1.0; the blend
# mixes where stress is *now* with where the congestion trajectory and the
# rate-direction signal say it is heading.
_W_CURRENT: float = 0.55      # persistence — stress is sticky
_W_CONGESTION: float = 0.28   # how the physical bottleneck is trending
_W_RATE: float = 0.17         # rate direction as a capacity-tightness proxy
assert abs(_W_CURRENT + _W_CONGESTION + _W_RATE - 1.0) < 1e-9

# A rate move of this magnitude (fraction) is treated as a "full" stress signal.
_RATE_SATURATION: float = 0.15

# Threshold (in stress points) for calling a trajectory Improving / Worsening.
_TREND_BAND: float = 0.04


# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class StressForecast:
    """Forward disruption-stress projection for a single shipping route."""

    route_id: str
    route_name: str
    current_stress: float          # [0, 1] — stress level today
    stress_7d: float               # [0, 1] — projected stress in 7 days
    stress_30d: float              # [0, 1] — projected stress in 30 days
    trend: str                     # "Improving" | "Stable" | "Worsening"
    rate_forecast_pct: float       # projected 30d rate change, as a fraction
    mc_p90_upside: float           # Monte Carlo P90 rate upside, as a fraction
    narrative: str                 # plain-English summary of the projection
    drivers: list[str] = field(default_factory=list)  # contributing factors


# ── Internal helpers ─────────────────────────────────────────────────────────

def _clamp01(value: float) -> float:
    """Clamp a value into [0, 1]; coerce non-finite input to the neutral baseline."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return _NEUTRAL_STRESS
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf guard
        return _NEUTRAL_STRESS
    return max(0.0, min(1.0, v))


def _route_name(route_id: str) -> str:
    """Human-readable route name from the registry, with a sensible fallback."""
    route = ROUTES_BY_ID.get(route_id)
    if route is not None:
        return route.name
    return route_id.replace("_", " ").title()


def _classify_trend(current: float, projected_30d: float) -> str:
    """Label the 30-day trajectory relative to today."""
    delta = projected_30d - current
    if delta > _TREND_BAND:
        return "Worsening"
    if delta < -_TREND_BAND:
        return "Improving"
    return "Stable"


def _current_stress_lookup(stress_report: Any) -> dict[str, float]:
    """Build a ``route_id -> stress_score`` map from a duck-typed stress report.

    ``stress_report`` is expected to expose ``.route_stress`` — a list whose
    items each carry ``.route_id`` and ``.stress_score``. Anything that does not
    match that shape is skipped silently so a partially-formed or unexpected
    object can never crash the forecaster.
    """
    lookup: dict[str, float] = {}
    if stress_report is None:
        return lookup

    route_stress = getattr(stress_report, "route_stress", None)
    if not route_stress:
        return lookup

    try:
        iterator = iter(route_stress)
    except TypeError:
        return lookup

    for item in iterator:
        rid = getattr(item, "route_id", None)
        score = getattr(item, "stress_score", None)
        if rid is None or score is None:
            continue
        lookup[str(rid)] = _clamp01(score)
    return lookup


def _macro_for_congestion(macro_data: dict | None) -> dict:
    """Adapt the FRED-style ``macro_data`` dict to what ``predict_congestion`` wants.

    ``predict_congestion`` expects a small dict with optional ``BDI_rising``
    (bool) and ``PMI`` (float) keys — not raw FRED DataFrames. We derive those
    cheaply and defensively; on any problem we return ``{}`` (neutral pressure).
    """
    if not macro_data or not isinstance(macro_data, dict):
        return {}

    adapted: dict = {}

    # Pass through if the caller already supplied the simple-dict form.
    if "BDI_rising" in macro_data or "PMI" in macro_data:
        if "BDI_rising" in macro_data:
            adapted["BDI_rising"] = bool(macro_data["BDI_rising"])
        if "PMI" in macro_data:
            try:
                adapted["PMI"] = float(macro_data["PMI"])
            except (TypeError, ValueError):
                pass
        return adapted

    # Otherwise try to derive from FRED-style DataFrames.
    try:
        bdi_df = macro_data.get("BDIY")
        if bdi_df is not None and hasattr(bdi_df, "empty") and not bdi_df.empty:
            col = "value" if "value" in bdi_df.columns else None
            if col is None:
                num = bdi_df.select_dtypes(include="number").columns
                col = num[0] if len(num) else None
            if col is not None:
                series = bdi_df[col].dropna()
                if len(series) > 30:
                    adapted["BDI_rising"] = bool(series.iloc[-1] > series.iloc[-31])
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"disruption_forecast: BDI adaptation skipped: {exc}")

    try:
        for pmi_id in ("IPMAN", "NAPMPI", "PMI"):
            pmi_df = macro_data.get(pmi_id)
            if pmi_df is not None and hasattr(pmi_df, "empty") and not pmi_df.empty:
                col = "value" if "value" in pmi_df.columns else None
                if col is None:
                    num = pmi_df.select_dtypes(include="number").columns
                    col = num[0] if len(num) else None
                if col is not None:
                    series = pmi_df[col].dropna()
                    if not series.empty:
                        adapted["PMI"] = float(series.iloc[-1])
                        break
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"disruption_forecast: PMI adaptation skipped: {exc}")

    return adapted


def _congestion_trajectory(
    route_id: str,
    current_stress: float,
    route_results: list | None,
    macro_data: dict | None,
) -> tuple[float, float, list[str]]:
    """Project the congestion component forward via ``congestion_predictor``.

    We seed the predictor with the destination-port congestion taken from
    ``route_results`` when available, otherwise we fall back to ``current_stress``
    as a proxy for the current bottleneck level.

    Returns ``(congestion_7d, congestion_30d, drivers)`` — both values in [0, 1].
    """
    drivers: list[str] = []
    route = ROUTES_BY_ID.get(route_id)
    dest_locode = route.dest_locode if route is not None else route_id

    # Find this route's optimizer result to read the live congestion seed.
    seed_congestion: Optional[float] = None
    for r in route_results or []:
        rid = r.get("route_id") if isinstance(r, dict) else getattr(r, "route_id", None)
        if rid != route_id:
            continue
        if isinstance(r, dict):
            seed_congestion = r.get("dest_demand_score") or r.get("origin_congestion")
        else:
            seed_congestion = getattr(r, "origin_congestion", None)
        break

    if seed_congestion is None:
        seed_congestion = current_stress  # proxy: today's stress ≈ today's bottleneck

    seed_congestion = _clamp01(seed_congestion)

    try:
        from processing.congestion_predictor import predict_congestion

        forecast = predict_congestion(
            dest_locode,
            seed_congestion,
            macro_data=_macro_for_congestion(macro_data),
        )
        cong_7d = _clamp01(forecast.predicted_7d)
        cong_30d = _clamp01(forecast.predicted_30d)
        if forecast.trend == "WORSENING":
            drivers.append("Destination-port congestion worsening")
        elif forecast.trend == "IMPROVING":
            drivers.append("Destination-port congestion easing")
        return cong_7d, cong_30d, drivers
    except Exception as exc:
        logger.debug(f"disruption_forecast: congestion projection failed for {route_id}: {exc}")
        # Neutral fallback: congestion holds flat at the seed level.
        return seed_congestion, seed_congestion, drivers


def _rate_signal_for_route(
    route_id: str,
    rate_forecasts: dict[str, Any],
) -> tuple[float, list[str]]:
    """Translate a route's rate forecast into a 30-day fractional rate change.

    ``rate_forecasts`` may hold either ML ``RateForecast`` objects
    (``rate_forecaster``) or linear ``RateForecast`` objects (``forecaster``);
    both expose ``current_rate`` and ``forecast_30d``, so we can read them
    uniformly. Returns ``(rate_forecast_pct, drivers)``.
    """
    drivers: list[str] = []
    fc = rate_forecasts.get(route_id)
    if fc is None:
        return 0.0, drivers

    current = getattr(fc, "current_rate", 0.0) or 0.0
    fwd_30d = getattr(fc, "forecast_30d", current)
    if current <= 0:
        return 0.0, drivers

    pct = (float(fwd_30d) - float(current)) / float(current)

    direction = getattr(fc, "direction", "")
    if direction == "Rising" or pct > 0.03:
        drivers.append("Freight rates forecast to rise (tightening capacity)")
    elif direction == "Falling" or pct < -0.03:
        drivers.append("Freight rates forecast to fall (slack capacity)")
    return float(pct), drivers


def _build_rate_forecasts(
    freight_data: dict | None,
    macro_data: dict | None,
) -> dict[str, Any]:
    """Build a ``route_id -> RateForecast`` map, ML first with a linear fallback.

    ``rate_forecaster.forecast_all_routes`` (ML / sklearn) is preferred. If it
    yields nothing — empty data, insufficient history, or sklearn unavailable —
    we fall back to the lighter linear ``forecaster.forecast_all_routes``.
    Either way the values expose ``current_rate`` / ``forecast_30d``.
    """
    if not freight_data:
        return {}

    # Preferred: ML forecaster (returns a dict keyed by route_id).
    try:
        from processing.rate_forecaster import forecast_all_routes as ml_forecast_all

        ml = ml_forecast_all(freight_data, macro_data or {})
        if ml:
            return dict(ml)
    except Exception as exc:
        logger.debug(f"disruption_forecast: ML rate forecaster unavailable: {exc}")

    # Fallback: linear trend forecaster (returns a list of RateForecast).
    try:
        from processing.forecaster import forecast_all_routes as linear_forecast_all

        linear = linear_forecast_all(freight_data)
        return {fc.route_id: fc for fc in linear}
    except Exception as exc:
        logger.debug(f"disruption_forecast: linear rate forecaster failed: {exc}")

    return {}


def _mc_p90_upside(route_id: str, freight_data: dict | None) -> float:
    """Monte Carlo P90 rate upside for a route, as a fraction of the current rate.

    A large positive value means the simulated rate distribution has a fat
    right tail — capacity tightening, i.e. building cost stress for shippers.
    Returns ``0.0`` whenever the simulation cannot run.
    """
    if not freight_data or route_id not in freight_data:
        return 0.0
    try:
        from processing.monte_carlo import simulate_freight_rates

        result = simulate_freight_rates(
            freight_data,
            route_id,
            n_simulations=300,
            forecast_days=30,
        )
        if result is None or result.current_rate <= 0:
            return 0.0
        upside = (result.bull_case_90d - result.current_rate) / result.current_rate
        return float(upside)
    except Exception as exc:
        logger.debug(f"disruption_forecast: Monte Carlo failed for {route_id}: {exc}")
        return 0.0


def _build_narrative(
    route_name: str,
    current: float,
    stress_7d: float,
    stress_30d: float,
    trend: str,
    rate_pct: float,
    mc_p90: float,
) -> str:
    """Compose a plain-English summary of the forward stress projection."""
    if trend == "Worsening":
        head = (
            f"{route_name} stress is projected to rise from {current:.0%} "
            f"to {stress_30d:.0%} over 30 days"
        )
    elif trend == "Improving":
        head = (
            f"{route_name} stress is projected to ease from {current:.0%} "
            f"to {stress_30d:.0%} over 30 days"
        )
    else:
        head = (
            f"{route_name} stress is projected to hold near {current:.0%} "
            f"over the next 30 days"
        )

    rate_clause = ""
    if rate_pct > 0.03:
        rate_clause = f"; rates are forecast {rate_pct:+.0%} (capacity tightening)"
    elif rate_pct < -0.03:
        rate_clause = f"; rates are forecast {rate_pct:+.0%} (capacity loosening)"

    tail_clause = ""
    if mc_p90 > 0.10:
        tail_clause = (
            f". Monte Carlo P90 shows {mc_p90:+.0%} rate upside risk in a "
            f"tail scenario"
        )

    return f"{head} ({stress_7d:.0%} at 7 days){rate_clause}{tail_clause}."


# ── Public API ───────────────────────────────────────────────────────────────

def forecast_route_stress(
    route_id: str,
    freight_data: dict,
    macro_data: dict,
    route_results: list,
    current_stress: Optional[float] = None,
) -> StressForecast:
    """Forecast disruption stress forward for a single route.

    Blends three already-existing forward signals into 7-/30-day stress numbers:
    the congestion trajectory (``congestion_predictor``), the freight-rate
    direction (``rate_forecaster`` / ``forecaster``) and the Monte Carlo P90
    rate tail (``monte_carlo``).

    Parameters
    ----------
    route_id:
        Route identifier (ideally a key in ``routes.route_registry.ROUTES_BY_ID``).
    freight_data:
        ``dict[route_id -> DataFrame]`` of freight rate history. May be empty.
    macro_data:
        FRED-style macro dict (or the simple ``predict_congestion`` dict). May be empty.
    route_results:
        List of route optimizer results (``RouteOpportunity`` objects or dicts).
        Used only to seed the congestion trajectory; may be empty.
    current_stress:
        Current stress level in ``[0, 1]``. If ``None``, a neutral baseline
        (~0.4) is used so the function is independently runnable.

    Returns
    -------
    StressForecast
        ``stress_7d`` and ``stress_30d`` are always floats in ``[0, 1]``.
    """
    route_name = _route_name(route_id)

    if current_stress is None:
        current = _NEUTRAL_STRESS
    else:
        current = _clamp01(current_stress)

    drivers: list[str] = []
    rate_pct = 0.0
    mc_p90 = 0.0

    try:
        # ── Congestion trajectory (physical bottleneck) ──────────────────────
        cong_7d, cong_30d, cong_drivers = _congestion_trajectory(
            route_id, current, route_results, macro_data
        )
        drivers.extend(cong_drivers)

        # ── Rate direction signal (capacity-tightness proxy) ─────────────────
        rate_forecasts = _build_rate_forecasts(freight_data, macro_data)
        rate_pct, rate_drivers = _rate_signal_for_route(route_id, rate_forecasts)
        drivers.extend(rate_drivers)

        # ── Monte Carlo P90 tail ─────────────────────────────────────────────
        mc_p90 = _mc_p90_upside(route_id, freight_data)

        # Map the rate signal into a 0-1 stress contribution centred on `current`.
        # Rising rates push stress up, falling rates pull it down; the P90 tail
        # adds a smaller upward nudge (tail risk only ever raises stress).
        rate_push = max(-1.0, min(1.0, rate_pct / _RATE_SATURATION))
        tail_push = max(0.0, min(1.0, mc_p90 / (_RATE_SATURATION * 2.0)))
        rate_stress_30d = _clamp01(current + 0.5 * rate_push + 0.25 * tail_push)
        # The 7-day rate effect is partial — rate moves accrue over the horizon.
        rate_stress_7d = _clamp01(current + 0.25 * rate_push + 0.12 * tail_push)

        # ── Blend into forward stress numbers ────────────────────────────────
        stress_7d = _clamp01(
            _W_CURRENT * current
            + _W_CONGESTION * cong_7d
            + _W_RATE * rate_stress_7d
        )
        stress_30d = _clamp01(
            _W_CURRENT * current
            + _W_CONGESTION * cong_30d
            + _W_RATE * rate_stress_30d
        )
    except Exception as exc:
        # Total-failure safety net: hold stress flat at the current level.
        logger.error(f"disruption_forecast: forecast failed for {route_id}: {exc}")
        stress_7d = current
        stress_30d = current

    trend = _classify_trend(current, stress_30d)
    if not drivers:
        drivers.append("No significant directional signal — stress projected flat")

    narrative = _build_narrative(
        route_name, current, stress_7d, stress_30d, trend, rate_pct, mc_p90
    )

    return StressForecast(
        route_id=route_id,
        route_name=route_name,
        current_stress=round(current, 4),
        stress_7d=round(_clamp01(stress_7d), 4),
        stress_30d=round(_clamp01(stress_30d), 4),
        trend=trend,
        rate_forecast_pct=round(rate_pct, 4),
        mc_p90_upside=round(mc_p90, 4),
        narrative=narrative,
        drivers=drivers,
    )


def forecast_all_stress(
    freight_data: dict,
    macro_data: dict,
    route_results: list,
    stress_report: Optional[Any] = None,
) -> list[StressForecast]:
    """Forecast disruption stress forward for every tracked route.

    Iterates the registry's routes and, for any extra routes seen in
    ``route_results``, produces a :class:`StressForecast` for each.

    Parameters
    ----------
    freight_data:
        ``dict[route_id -> DataFrame]`` of freight rate history. May be empty —
        in that case every route falls back to neutral defaults without crashing.
    macro_data:
        FRED-style macro dict. May be empty.
    route_results:
        List of route optimizer results (``RouteOpportunity`` objects or dicts).
        May be empty.
    stress_report:
        Optional ``ShippingStressReport`` (duck-typed — see the module docstring).
        When provided, its ``.route_stress`` entries seed each route's
        ``current_stress``. When absent, a neutral ~0.4 baseline is used so this
        function is independently runnable.

    Returns
    -------
    list[StressForecast]
        One entry per route, sorted by ``stress_30d`` descending (most-stressed
        routes first). Every entry's ``stress_7d`` / ``stress_30d`` is a float
        in ``[0, 1]``.
    """
    current_lookup = _current_stress_lookup(stress_report)

    # Build the route universe: registry routes plus anything extra in
    # route_results, de-duplicated while preserving order.
    route_ids: list[str] = [r.id for r in ROUTES]
    seen = set(route_ids)
    for r in route_results or []:
        rid = r.get("route_id") if isinstance(r, dict) else getattr(r, "route_id", None)
        if rid and rid not in seen:
            route_ids.append(rid)
            seen.add(rid)

    forecasts: list[StressForecast] = []
    for route_id in route_ids:
        try:
            forecast = forecast_route_stress(
                route_id=route_id,
                freight_data=freight_data or {},
                macro_data=macro_data or {},
                route_results=route_results or [],
                current_stress=current_lookup.get(route_id),
            )
            forecasts.append(forecast)
        except Exception as exc:
            # Never let one bad route abort the whole batch.
            logger.error(f"disruption_forecast: skipping {route_id}: {exc}")
            forecasts.append(
                StressForecast(
                    route_id=route_id,
                    route_name=_route_name(route_id),
                    current_stress=_NEUTRAL_STRESS,
                    stress_7d=_NEUTRAL_STRESS,
                    stress_30d=_NEUTRAL_STRESS,
                    trend="Stable",
                    rate_forecast_pct=0.0,
                    mc_p90_upside=0.0,
                    narrative=f"{_route_name(route_id)}: no forecast available.",
                    drivers=["Forecast unavailable — neutral baseline applied"],
                )
            )

    forecasts.sort(key=lambda f: f.stress_30d, reverse=True)
    logger.info(f"disruption_forecast: {len(forecasts)} route stress forecasts generated")
    return forecasts
