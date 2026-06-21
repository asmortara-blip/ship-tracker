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
  * ``monte_carlo.simulate_freight_rates`` — **both** Monte Carlo rate tails.
    A fat *right* (P90) tail implies tightening capacity, i.e. building cost
    stress for shippers; a fat *left* (P10) tail implies a capacity glut /
    blank-sailing risk that pulls projected stress *down*. The forecast reads
    both, so it can be revised down as well as up.

These signals are blended into 7-day / 30-day stress numbers on a 0-1 scale.

Three documented refinements over the original fixed-weight blend:

1. **Volatility-scaled rate signal.** A raw rate move is normalised by the
   route's own historical volatility before it feeds the stress blend, so a
   5% move on a calm lane counts more than the same 5% on a swingy one.

2. **Symmetric Monte Carlo tails.** Both MC tails are read — the P90 upside
   (capacity tightening) *and* the P10 downside (capacity glut / blank
   sailings). The net of the two can push projected stress either way.

3. **A documented cross-signal interaction.** When destination-port congestion
   is high *and* freight rates are softening — evidenced by either the central
   rate forecast or the Monte Carlo P10 capacity-glut tail — that combination
   reads as structural oversupply (idle capacity stuck behind a bottleneck).
   The forecaster detects this and surfaces it in the narrative and drivers.

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
if abs(_W_CURRENT + _W_CONGESTION + _W_RATE - 1.0) >= 1e-9:
    # ValueError (not assert) so the invariant fires under ``python -O``.
    raise ValueError("_W_CURRENT + _W_CONGESTION + _W_RATE must sum to 1.0")

# A rate move of this magnitude (fraction) is treated as a "full" stress signal.
# Used as a fallback saturation when a route's own volatility is unavailable.
_RATE_SATURATION: float = 0.15

# Volatility-scaled rate signal. A raw 30-day rate move is first normalised by
# the route's own historical volatility, then by this many standard deviations
# to reach a "full strength" (±1) signal. ~2.5σ ⇒ only a genuinely large move
# *relative to that route's own noise* saturates the rate contribution.
_RATE_VOL_SATURATION_SIGMAS: float = 2.5

# Floor / ceiling on the per-route monthly rate volatility estimate (fraction).
# The floor stops a near-flat synthetic series making every move look enormous;
# the ceiling stops one wild route making real moves invisible.
_RATE_VOL_FLOOR: float = 0.03
_RATE_VOL_CEIL: float = 0.40

# Symmetric Monte Carlo tails. The P90 upside and P10 downside are each scaled
# by this magnitude to reach a full-strength (±1) tail signal. A ~30% tail move
# is treated as a full-strength tail.
_MC_TAIL_SATURATION: float = 0.30

# Cross-signal interaction: structural-oversupply trigger. Fires when
# destination-port congestion is at/above ``_OVERSUPPLY_CONGESTION_MIN`` WHILE
# there is genuine downward rate evidence — *either* the 30-day rate forecast
# is at/below ``_OVERSUPPLY_RATE_MAX`` (the negative threshold), *or* the Monte
# Carlo P10 downside tail is at/below ``_OVERSUPPLY_P10_MAX`` (a fat capacity-
# glut left tail). High congestion alongside softening rates reads as idle
# capacity stuck behind a bottleneck; the rule applies a small downward stress
# relief and is surfaced in the narrative.
_OVERSUPPLY_CONGESTION_MIN: float = 0.60
_OVERSUPPLY_RATE_MAX: float = -0.03
_OVERSUPPLY_P10_MAX: float = -0.08
_OVERSUPPLY_STRESS_RELIEF: float = 0.05

# Threshold (in stress points) for calling a trajectory Improving / Worsening.
_TREND_BAND: float = 0.04

# Forward-interval dispersion bounds (rec R023). The 30d stress band half-width
# (forecast_sigma) is blended from the MC rate-tail half-spread + the route's
# own rate volatility (both mapped into stress points via _W_RATE), then clamped
# here. A forward forecast is NEVER certain, so even a calm lane keeps the floor;
# the ceiling stops a wild MC tail from blowing the band past plausibility. The
# 7d band is the 30d sigma scaled by sqrt(7/30) (dispersion grows ~sqrt-time).
_FORECAST_SIGMA_MIN: float = 0.03
_FORECAST_SIGMA_MAX: float = 0.30


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
    # ── Added fields (refinement transparency; existing fields unchanged) ────
    mc_p10_downside: float = 0.0   # Monte Carlo P10 rate downside, as a fraction
                                   # (negative = capacity glut / blank-sailing risk)
    rate_volatility: float = 0.0   # route's own monthly rate volatility (fraction)
    rate_signal_z: float = 0.0     # 30d rate move expressed in route-σ units
    structural_oversupply: bool = False  # high congestion + falling rates flag
    # ── Honest forward intervals (rec R023) — every point ships a band ──────
    # Illustrative ±1σ stress intervals (≈68%), the dispersion blended from the
    # MC rate-tail half-spread + the route's own rate volatility, horizon-scaled.
    # An interval that CAN be PIT/coverage-scored, not a fitted predictive band.
    stress_7d_band: tuple = (0.0, 0.0)    # (low, high) in [0, 1]
    stress_30d_band: tuple = (0.0, 0.0)   # (low, high) in [0, 1]
    forecast_sigma: float = 0.0           # 30d stress dispersion (the band half-width)


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
        # Prefer the canonical FRED key (BSXRLM); fall back to BDIY for legacy callers.
        bdi_df = macro_data.get("BSXRLM")
        if bdi_df is None:
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


def _route_rate_volatility(route_id: str, freight_data: dict | None) -> float:
    """Estimate a route's own monthly freight-rate volatility, as a fraction.

    Computed from the route's rate history: the standard deviation of
    ~monthly (21-trading-day) log-returns. A volatile lane (rates that swing
    hard) gets a high number; a calm lane gets a low one. The estimate is
    clamped into ``[_RATE_VOL_FLOOR, _RATE_VOL_CEIL]`` so a near-flat synthetic
    series cannot make every move look enormous, nor one wild route make real
    moves invisible. Returns the floor whenever history is too thin to measure.
    """
    if not freight_data or route_id not in freight_data:
        return _RATE_VOL_FLOOR
    try:
        import numpy as np

        df = freight_data.get(route_id)
        if df is None or getattr(df, "empty", True):
            return _RATE_VOL_FLOOR
        if "rate_usd_per_feu" not in getattr(df, "columns", []):
            return _RATE_VOL_FLOOR

        rates = df["rate_usd_per_feu"].dropna()
        if len(rates) < 30:
            # Not enough history to measure dispersion — use the floor.
            return _RATE_VOL_FLOOR

        log_rates = np.log(rates.clip(lower=1e-6))
        # ~Monthly log-returns: difference over a 21-trading-day window.
        monthly_returns = log_rates.diff(21).dropna()
        if len(monthly_returns) < 2:
            return _RATE_VOL_FLOOR

        vol = float(monthly_returns.std())
        if not np.isfinite(vol) or vol <= 0:
            return _RATE_VOL_FLOOR
        return max(_RATE_VOL_FLOOR, min(_RATE_VOL_CEIL, vol))
    except Exception as exc:
        logger.debug(f"disruption_forecast: rate volatility failed for {route_id}: {exc}")
        return _RATE_VOL_FLOOR


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


def _mc_tails(route_id: str, freight_data: dict | None) -> tuple[float, float]:
    """Monte Carlo rate tails for a route, as fractions of the current rate.

    Returns ``(p90_upside, p10_downside)`` — **both** tails of the simulated
    30-day rate distribution:

    * ``p90_upside``   — ``(P90 − current) / current``. A large positive value
      means a fat *right* tail: capacity tightening, building cost stress for
      shippers. Floored at 0 (the right tail only ever adds upside risk).
    * ``p10_downside`` — ``(P10 − current) / current``. A large negative value
      means a fat *left* tail: a capacity glut / blank-sailing risk that
      relieves cost stress. Capped at 0 (the left tail only ever subtracts).

    The Monte Carlo engine (``monte_carlo.simulate_freight_rates``) projects 30
    days here; ``bull_case_90d`` / ``bear_case_90d`` are its P90 / P10 at the
    end of the projection window — the field names are historical, the values
    are the end-of-horizon tails regardless of horizon length.

    Returns ``(0.0, 0.0)`` whenever the simulation cannot run.
    """
    if not freight_data or route_id not in freight_data:
        return 0.0, 0.0
    try:
        from processing.monte_carlo import simulate_freight_rates

        result = simulate_freight_rates(
            freight_data,
            route_id,
            n_simulations=300,
            forecast_days=30,
        )
        if result is None or result.current_rate <= 0:
            return 0.0, 0.0
        current = result.current_rate
        upside = (result.bull_case_90d - current) / current
        downside = (result.bear_case_90d - current) / current
        # Keep each tail one-signed: the right tail adds upside risk, the left
        # tail subtracts. This guards against an odd simulation where, say, the
        # P90 lands marginally below the current rate.
        return max(0.0, float(upside)), min(0.0, float(downside))
    except Exception as exc:
        logger.debug(f"disruption_forecast: Monte Carlo failed for {route_id}: {exc}")
        return 0.0, 0.0


def _build_narrative(
    route_name: str,
    current: float,
    stress_7d: float,
    stress_30d: float,
    trend: str,
    rate_pct: float,
    mc_p90: float,
    mc_p10: float = 0.0,
    structural_oversupply: bool = False,
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

    # Monte Carlo tail clause — reports whichever tail is the more material.
    tail_clause = ""
    if mc_p90 > 0.10 and mc_p90 >= -mc_p10:
        tail_clause = (
            f". Monte Carlo P90 shows {mc_p90:+.0%} rate upside risk in a "
            f"tail scenario"
        )
    elif mc_p10 < -0.10:
        tail_clause = (
            f". Monte Carlo P10 shows {mc_p10:+.0%} rate downside risk in a "
            f"capacity-glut tail scenario"
        )

    # Cross-signal interaction: high congestion + falling rates = structural
    # oversupply. Called out explicitly because the two signals together mean
    # something neither does alone (idle capacity stuck behind a bottleneck).
    oversupply_clause = ""
    if structural_oversupply:
        oversupply_clause = (
            ". Note: high port congestion alongside falling rates points to "
            "structural oversupply — idle capacity queued behind the bottleneck"
        )

    return (
        f"{head} ({stress_7d:.0%} at 7 days){rate_clause}{tail_clause}"
        f"{oversupply_clause}."
    )


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
    direction (``rate_forecaster`` / ``forecaster``) and the Monte Carlo rate
    tails (``monte_carlo``).

    The rate signal is **volatility-scaled** — a raw move is normalised by the
    route's own historical rate volatility, so an equal % move counts more on a
    calm lane than on a swingy one. **Both** Monte Carlo tails feed the blend:
    the P90 upside (capacity tightening) raises projected stress, the P10
    downside (capacity glut / blank sailings) lowers it. A documented
    cross-signal rule — high destination congestion alongside falling rates —
    flags structural oversupply and applies a small downward stress relief.

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
    mc_p10 = 0.0
    rate_vol = _RATE_VOL_FLOOR
    rate_signal_z = 0.0
    cong_30d = current
    structural_oversupply = False

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

        # ── Volatility-scaled rate signal ────────────────────────────────────
        # Normalise the raw 30-day rate move by the route's OWN historical
        # volatility, then by a fixed σ-count to reach a full-strength signal.
        # A 5% move on a calm lane is a big z-score; the same 5% on a swingy
        # lane is barely a blip. When no history is available the volatility
        # floor makes this degrade gracefully toward the legacy fixed-saturation
        # behaviour.
        rate_vol = _route_rate_volatility(route_id, freight_data)
        rate_signal_z = rate_pct / rate_vol if rate_vol > 0 else 0.0
        if abs(rate_signal_z) >= _RATE_VOL_SATURATION_SIGMAS:
            if rate_signal_z > 0:
                drivers.append(
                    "Rate move is large relative to this route's own volatility"
                )
            else:
                drivers.append(
                    "Rate drop is large relative to this route's own volatility"
                )

        # ── Monte Carlo tails — symmetric (P90 upside AND P10 downside) ───────
        mc_p90, mc_p10 = _mc_tails(route_id, freight_data)
        if mc_p10 < -0.10:
            drivers.append("Monte Carlo P10 flags capacity-glut / blank-sailing risk")

        # ── Cross-signal interaction: structural oversupply ──────────────────
        # High destination-port congestion WHILE freight rates are softening is
        # not just "two soft signals" — together they read as structural
        # oversupply: capacity exists but is idle, queued behind a bottleneck.
        # Downward rate evidence is taken from EITHER the central rate forecast
        # OR the Monte Carlo P10 capacity-glut tail, so the rule is robust to a
        # near-flat central forecast that nonetheless carries a fat left tail.
        # When it fires, projected stress is pulled down and the narrative says so.
        rate_falling = rate_pct <= _OVERSUPPLY_RATE_MAX
        glut_tail = mc_p10 <= _OVERSUPPLY_P10_MAX
        if cong_30d >= _OVERSUPPLY_CONGESTION_MIN and (rate_falling or glut_tail):
            structural_oversupply = True
            drivers.append(
                "Structural oversupply — high congestion with softening rates "
                "(idle capacity behind the bottleneck)"
            )

        # Map the rate signal into a 0-1 stress contribution centred on `current`.
        # Rising rates push stress up, falling rates pull it down. The push is
        # now driven by the VOLATILITY-SCALED z-score, not the raw % move.
        rate_push = max(-1.0, min(1.0, rate_signal_z / _RATE_VOL_SATURATION_SIGMAS))

        # Symmetric tail push: the P90 right tail adds upward stress, the P10
        # left tail subtracts. Their NET means the tails can revise stress
        # either way — not the upside-only nudge of the original.
        up_tail = max(0.0, min(1.0, mc_p90 / _MC_TAIL_SATURATION))
        down_tail = max(-1.0, min(0.0, mc_p10 / _MC_TAIL_SATURATION))
        tail_push = up_tail + down_tail   # in [-1, 1]

        rate_stress_30d = _clamp01(current + 0.5 * rate_push + 0.25 * tail_push)
        # The 7-day rate effect is partial — rate moves accrue over the horizon.
        rate_stress_7d = _clamp01(current + 0.25 * rate_push + 0.12 * tail_push)

        # Structural-oversupply relief: a small explicit downward adjustment on
        # top of the blend, applied to both horizons.
        oversupply_relief = _OVERSUPPLY_STRESS_RELIEF if structural_oversupply else 0.0

        # ── Blend into forward stress numbers ────────────────────────────────
        stress_7d = _clamp01(
            _W_CURRENT * current
            + _W_CONGESTION * cong_7d
            + _W_RATE * rate_stress_7d
            - oversupply_relief
        )
        stress_30d = _clamp01(
            _W_CURRENT * current
            + _W_CONGESTION * cong_30d
            + _W_RATE * rate_stress_30d
            - oversupply_relief
        )
    except Exception as exc:
        # Total-failure safety net: hold stress flat at the current level.
        logger.error(f"disruption_forecast: forecast failed for {route_id}: {exc}")
        stress_7d = current
        stress_30d = current

    trend = _classify_trend(current, stress_30d)
    if not drivers:
        drivers.append("No significant directional signal — stress projected flat")

    # ── Honest forward intervals (R023) ──────────────────────────────────────
    # 30d stress dispersion, in stress points, blended from two real sources:
    #   * the MC rate-distribution half-spread (mc_p90 - mc_p10)/2, mapped into
    #     stress via the same _W_RATE / _MC_TAIL_SATURATION the point estimate
    #     uses, and
    #   * the route's own rate volatility (a calm lane gets a tight band, a
    #     swingy one a wide band),
    # then clamped to [_FORECAST_SIGMA_MIN, _FORECAST_SIGMA_MAX] so the band is
    # never degenerate and never absurd. ±1σ ≈ a 68% interval.
    tail_halfspread = max(0.0, (mc_p90 - mc_p10)) / 2.0
    tail_sigma = _W_RATE * tail_halfspread / _MC_TAIL_SATURATION
    vol_sigma = _W_RATE * rate_vol
    sigma_30d = min(_FORECAST_SIGMA_MAX,
                    max(_FORECAST_SIGMA_MIN, tail_sigma + vol_sigma))
    sigma_7d = sigma_30d * (7.0 / 30.0) ** 0.5
    stress_7d_band = (round(_clamp01(stress_7d - sigma_7d), 4),
                      round(_clamp01(stress_7d + sigma_7d), 4))
    stress_30d_band = (round(_clamp01(stress_30d - sigma_30d), 4),
                       round(_clamp01(stress_30d + sigma_30d), 4))

    narrative = _build_narrative(
        route_name, current, stress_7d, stress_30d, trend, rate_pct, mc_p90,
        mc_p10=mc_p10, structural_oversupply=structural_oversupply,
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
        mc_p10_downside=round(mc_p10, 4),
        rate_volatility=round(rate_vol, 4),
        rate_signal_z=round(rate_signal_z, 4),
        structural_oversupply=structural_oversupply,
        stress_7d_band=stress_7d_band,
        stress_30d_band=stress_30d_band,
        forecast_sigma=round(sigma_30d, 4),
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
