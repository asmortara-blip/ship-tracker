"""
Congestion forecasting for tracked container ports.

Uses simple exponential smoothing with seasonal adjustment and macro pressure
to produce 7-, 14-, and 30-day congestion outlooks.

Three documented refinements over the original stateless smoother:

1. **Confidence bands.** Each horizon now carries a low/high band derived from
   recent congestion volatility. With no observed history we fall back to a
   modest structural volatility and widen the band with the square root of the
   horizon (the standard random-walk-style fan-out), so a 30-day band is wider
   than a 7-day one.

2. **Port-specific mean-reversion baseline.** The 30-day forecast no longer
   reverts every port toward a flat 0.5. Busy hub ports structurally sit at a
   higher congestion equilibrium than quiet feeder ports, so each tracked
   port has its own baseline (see ``_PORT_BASELINE``); unknown ports use a
   neutral default.

3. **Magnitude-aware macro pressure.** Macro pressure is now continuous rather
   than a yes/no threshold: it scales with the *size* of the Baltic Dry Index
   move and with how far Manufacturing PMI sits from the neutral 50 line, so a
   small drift and a large surge no longer produce the same pressure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Tuning constants (documented)
# ---------------------------------------------------------------------------

# Default mean-reversion baseline for any port not in ``_PORT_BASELINE``.
# 0.55 — slightly above the midpoint: a *tracked* container port is, by
# selection, a non-trivial node, so its quiet-state equilibrium is a little
# above a true 0.5 neutral.
_DEFAULT_BASELINE: float = 0.55

# Per-port mean-reversion baseline — the congestion level a port structurally
# drifts back toward once a transient shock fades. Busy global hubs clear
# slower (more calls, deeper backlogs) and therefore revert HIGH; quiet feeder
# / short-sea ports revert LOW. Values are demo heuristics on a [0, 1] scale,
# anchored to each port's role in the route registry. Unknown ports fall back
# to ``_DEFAULT_BASELINE``.
_PORT_BASELINE: dict[str, float] = {
    # ── Mega-hubs — chronically busy, slow to clear ──────────────────────────
    "CNSHA": 0.62,   # Shanghai — world's busiest container port
    "SGSIN": 0.60,   # Singapore — top transshipment hub
    "NLRTM": 0.58,   # Rotterdam — busiest European port
    "USLAX": 0.60,   # Los Angeles — busiest US container gateway
    "USLGB": 0.58,   # Long Beach — San Pedro Bay complex, paired with LAX
    "CNNBO": 0.59,   # Ningbo-Zhoushan — second-busiest Chinese port
    # ── Large but better-flowing gateways ────────────────────────────────────
    "BEANR": 0.55,   # Antwerp — large North European range port
    "AEJEA": 0.55,   # Jebel Ali — dominant Gulf hub
    "USNYC": 0.54,   # New York/New Jersey — busiest US East Coast port
    "USSAV": 0.52,   # Savannah — large but generally free-flowing
    "GBFXT": 0.52,   # Felixstowe — UK's busiest box port
    # ── Mid-size / regional ports ────────────────────────────────────────────
    "JPYOK": 0.48,   # Yokohama — efficient, moderate volume
    "GRPIR": 0.50,   # Piraeus — growing Mediterranean gateway
    "BRSAO": 0.50,   # Santos — largest LATAM port, moderate congestion
    "LKCMB": 0.50,   # Colombo — transshipment hub, moderate
    # ── Quiet feeder / short-sea ports — clear fast, revert LOW ───────────────
    "MATNM": 0.42,   # Tanger Med — efficient, modern, free-flowing
}

# Structural fallback volatility (in congestion points) used when no observed
# congestion history is supplied. Wide enough to give an honest band, narrow
# enough not to swamp the point forecast.
_DEFAULT_VOLATILITY: float = 0.06

# Z-multiplier for the confidence band. ~1.28 ≈ a 10th/90th-percentile band
# (an ~80% interval) under a normal approximation — a deliberately moderate,
# not 95%, band so the high edge stays plausible on a [0, 1] scale.
_BAND_Z: float = 1.28

# Reference horizon (days) at which the band equals the raw volatility. The
# band widens with sqrt(horizon / reference): the 7d band is tighter, the 30d
# band ~2x wider, mirroring the rate forecaster's horizon scaling.
_BAND_REFERENCE_HORIZON: float = 7.0

# BDI % change that counts as a "full strength" macro pressure signal. A move
# at or beyond this magnitude saturates the BDI pressure contribution.
_BDI_SATURATION_PCT: float = 0.20   # a 20% BDI swing is a full-strength signal

# Maximum additive pressure each macro channel can contribute (congestion pts).
_BDI_MAX_PRESSURE: float = 0.04
_PMI_MAX_PRESSURE: float = 0.03

# PMI distance from the neutral 50 line that saturates the PMI pressure
# contribution. PMI rarely strays more than ~8 points from neutral.
_PMI_SATURATION_DISTANCE: float = 8.0


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class CongestionForecast:
    port_locode: str
    current_congestion: float    # [0, 1]
    predicted_7d: float
    predicted_14d: float
    predicted_30d: float
    trend: str                   # "WORSENING" | "STABLE" | "IMPROVING"
    peak_risk_date: str          # ISO-format estimated date of peak congestion
    seasonal_factor: float       # from seasonal module
    driving_factors: list[str] = field(default_factory=list)
    # ── Added fields (confidence bands; existing fields unchanged) ───────────
    # Low/high congestion band per horizon, each a [0, 1] tuple (low, high).
    # Derived from recent congestion volatility, widened with the horizon.
    band_7d: tuple[float, float] = (0.0, 0.0)
    band_14d: tuple[float, float] = (0.0, 0.0)
    band_30d: tuple[float, float] = (0.0, 0.0)
    # The volatility (congestion points) the bands were derived from, and the
    # per-port mean-reversion baseline used for the 30-day forecast — surfaced
    # so callers / the UI can inspect the heuristic rather than trust it blind.
    congestion_volatility: float = 0.0
    reversion_baseline: float = _DEFAULT_BASELINE


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _port_baseline(port_locode: str) -> float:
    """Return the per-port mean-reversion baseline congestion level.

    Busy hubs revert toward a higher equilibrium than quiet feeder ports.
    Unknown ports fall back to ``_DEFAULT_BASELINE``. The lookup is
    case-insensitive on the UN/LOCODE.
    """
    if not port_locode:
        return _DEFAULT_BASELINE
    return _PORT_BASELINE.get(port_locode.strip().upper(), _DEFAULT_BASELINE)


def _macro_pressure(macro_data: dict | None) -> float:
    """Derive a continuous additive pressure scalar from macro indicators.

    The signal is *magnitude-aware*, not a yes/no threshold:

    * **BDI** — pressure scales with the size of the Baltic Dry Index move.
      ``BDI_change_pct`` (a fraction, e.g. ``0.10`` for +10%) is preferred;
      a ``BDI_rising`` bool is still honoured as a coarse fallback (treated as
      a half-strength move). Only a *rising* BDI adds congestion pressure — a
      falling dry-bulk market does not relieve container-port congestion, so
      negative moves contribute zero rather than going negative.
    * **PMI** — pressure scales with how far Manufacturing PMI sits *above* the
      neutral 50 line (demand pull). A PMI at or below neutral contributes
      zero; the contribution saturates a few points above neutral.

    Both channels are individually capped, so even an extreme macro print can
    only nudge — never dominate — the congestion forecast.
    """
    if not macro_data:
        return 0.0

    pressure = 0.0

    # ── BDI channel — magnitude-scaled ───────────────────────────────────────
    bdi_pct = macro_data.get("BDI_change_pct")
    if bdi_pct is not None:
        try:
            move = float(bdi_pct)
        except (TypeError, ValueError):
            move = 0.0
        # Only a rising BDI adds container-port pressure.
        if move > 0:
            strength = min(1.0, move / _BDI_SATURATION_PCT)
            pressure += _BDI_MAX_PRESSURE * strength
    elif macro_data.get("BDI_rising", False):
        # Coarse boolean fallback — treat as a half-strength move so callers
        # supplying only the legacy flag still get a sensible (not maximal)
        # pressure contribution.
        pressure += _BDI_MAX_PRESSURE * 0.5

    # ── PMI channel — distance-from-neutral scaled ───────────────────────────
    pmi = macro_data.get("PMI", 50.0)
    try:
        pmi_val = float(pmi)
    except (TypeError, ValueError):
        pmi_val = 50.0
    distance_above_neutral = max(0.0, pmi_val - 50.0)
    if distance_above_neutral > 0:
        strength = min(1.0, distance_above_neutral / _PMI_SATURATION_DISTANCE)
        pressure += _PMI_MAX_PRESSURE * strength

    return pressure


def _congestion_volatility(history: object) -> float:
    """Estimate recent congestion volatility (in [0, 1] congestion points).

    ``history`` may be any sequence of recent congestion observations (floats
    in [0, 1]). When fewer than three usable points are supplied we cannot
    measure dispersion, so we fall back to ``_DEFAULT_VOLATILITY`` — a modest
    structural value that still yields an honest, non-zero band.

    Volatility is the standard deviation of the *changes* between successive
    observations: that captures how jumpy congestion has been recently, which
    is what a forward band should widen on.
    """
    if history is None:
        return _DEFAULT_VOLATILITY
    try:
        values = [float(v) for v in history if v is not None]
    except (TypeError, ValueError):
        return _DEFAULT_VOLATILITY
    # Keep only finite, in-range observations.
    values = [v for v in values if v == v and 0.0 <= v <= 1.0]
    if len(values) < 3:
        return _DEFAULT_VOLATILITY

    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    n = len(diffs)
    if n < 2:
        return _DEFAULT_VOLATILITY
    mean = sum(diffs) / n
    variance = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    vol = variance ** 0.5
    if vol != vol or vol <= 0:  # NaN / degenerate guard
        return _DEFAULT_VOLATILITY
    # Clamp into a sane range: never absurdly tight, never wider than the scale.
    return _clamp(vol, 0.01, 0.5)


def _confidence_band(
    point: float,
    volatility: float,
    horizon_days: int,
) -> tuple[float, float]:
    """Return a (low, high) congestion band around a horizon's point forecast.

    The half-width is ``_BAND_Z * volatility * sqrt(horizon / reference)`` —
    the band widens with the square root of the horizon, the standard
    random-walk-style fan-out. Both edges are clamped into [0, 1].
    """
    horizon_scale = (horizon_days / _BAND_REFERENCE_HORIZON) ** 0.5
    half_width = _BAND_Z * volatility * horizon_scale
    low = _clamp(point - half_width)
    high = _clamp(point + half_width)
    return (round(low, 4), round(high, 4))


def _trend(current: float, predicted_30d: float) -> str:
    if predicted_30d > current + 0.05:
        return "WORSENING"
    if predicted_30d < current - 0.05:
        return "IMPROVING"
    return "STABLE"


def _peak_risk_date(
    current: float,
    predicted_7d: float,
    predicted_14d: float,
    predicted_30d: float,
    trend: str,
) -> str:
    """Estimate the date of peak congestion based on trend shape."""
    today = date.today()
    if trend == "WORSENING":
        # Peak is likely at or beyond 30 days
        peak = today + timedelta(days=30)
    elif trend == "IMPROVING":
        # Peak is now (current is highest); already improving
        peak = today
    else:
        # Stable — put peak at the earliest highest predicted point
        vals = [
            (7,  predicted_7d),
            (14, predicted_14d),
            (30, predicted_30d),
        ]
        peak_days = max(vals, key=lambda x: x[1])[0]
        peak = today + timedelta(days=peak_days)
    return peak.isoformat()


def _driving_factors(
    port_locode: str,
    current: float,
    macro_data: dict | None,
    seasonal: float,
    pressure: float,
) -> list[str]:
    factors: list[str] = []
    if current >= 0.75:
        factors.append("High current congestion level")
    # Pressure is now continuous; describe it by tier rather than by which
    # boolean fired. The tier cut-offs are a fraction of the combined cap.
    if pressure >= 0.045:
        factors.append("Strong macro pressure (large BDI move + expansionary PMI)")
    elif pressure >= 0.025:
        factors.append("Moderate macro pressure from BDI / PMI signals")
    elif pressure >= 0.012:
        factors.append("Mild macro pressure — BDI / PMI modestly supportive")
    if seasonal > 0.05:
        factors.append("Positive seasonal demand pattern active")
    elif seasonal < -0.05:
        factors.append("Seasonal demand headwind reducing pressure")
    if not factors:
        factors.append("No significant near-term pressure signals")
    return factors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def predict_congestion(
    port_locode: str,
    current_congestion: float,
    macro_data: dict | None = None,
    congestion_history: object | None = None,
) -> CongestionForecast:
    """Forecast port congestion over 7, 14, and 30 days.

    Parameters
    ----------
    port_locode:        UN/LOCODE of the port.
    current_congestion: Current congestion level [0, 1].
    macro_data:         Optional dict with macro indicators. Recognised keys:
                        ``BDI_change_pct`` (float fraction — preferred,
                        magnitude-scaled), ``BDI_rising`` (bool — coarse
                        fallback) and ``PMI`` (float).
    congestion_history: Optional sequence of recent congestion observations
                        ([0, 1] floats). When supplied, the confidence bands
                        are derived from its realised volatility; otherwise a
                        modest structural volatility is used.

    Returns
    -------
    CongestionForecast dataclass instance. The point fields
    (``predicted_7d/14d/30d``, ``trend`` etc.) are unchanged in meaning; the
    new ``band_*`` / ``congestion_volatility`` / ``reversion_baseline`` fields
    add a transparent confidence range around each horizon.
    """
    # Lazy import to avoid circular dependency issues at module load time
    from processing.seasonal import get_seasonal_adjustment

    alpha = 0.3
    seasonal = get_seasonal_adjustment(port_locode)
    pressure = _macro_pressure(macro_data)

    # 7-day forecast
    predicted_7d = _clamp(
        current_congestion * (1 - alpha)
        + alpha * (current_congestion + pressure + seasonal * 0.1)
    )

    # 14-day forecast — pressure attenuated
    predicted_14d = _clamp(
        predicted_7d * (1 - alpha)
        + alpha * (predicted_7d + pressure * 0.8)
    )

    # 30-day forecast — mean reversion toward the PORT-SPECIFIC baseline.
    # Busy hubs revert toward a higher equilibrium congestion level than quiet
    # feeder ports, so the reversion target is no longer a flat 0.5.
    mean_reversion = _port_baseline(port_locode)
    predicted_30d = _clamp(
        predicted_14d * (1 - alpha)
        + alpha * (predicted_14d + pressure * 0.6 + (mean_reversion - predicted_14d) * 0.15)
    )

    trend = _trend(current_congestion, predicted_30d)
    peak_date = _peak_risk_date(
        current_congestion, predicted_7d, predicted_14d, predicted_30d, trend
    )
    factors = _driving_factors(port_locode, current_congestion, macro_data, seasonal, pressure)

    # ── Confidence bands ─────────────────────────────────────────────────────
    # Derive a volatility estimate (from observed history when available) and
    # build a low/high band around each horizon that widens with the horizon.
    volatility = _congestion_volatility(congestion_history)
    band_7d = _confidence_band(predicted_7d, volatility, 7)
    band_14d = _confidence_band(predicted_14d, volatility, 14)
    band_30d = _confidence_band(predicted_30d, volatility, 30)

    return CongestionForecast(
        port_locode=port_locode,
        current_congestion=round(current_congestion, 4),
        predicted_7d=round(predicted_7d, 4),
        predicted_14d=round(predicted_14d, 4),
        predicted_30d=round(predicted_30d, 4),
        trend=trend,
        peak_risk_date=peak_date,
        seasonal_factor=round(seasonal, 4),
        driving_factors=factors,
        band_7d=band_7d,
        band_14d=band_14d,
        band_30d=band_30d,
        congestion_volatility=round(volatility, 4),
        reversion_baseline=round(mean_reversion, 4),
    )


def predict_all_ports(
    port_results: list,
    macro_data: dict,
) -> dict[str, CongestionForecast]:
    """Produce congestion forecasts for a list of port result objects.

    Parameters
    ----------
    port_results: List of objects with at minimum a ``port_locode`` attribute
                  and a ``current_congestion`` attribute (float [0, 1]).
                  Compatible with PortEfficiencyScore and similar dataclasses
                  that expose a congestion-like metric, or plain dicts with
                  the same keys. When a result also exposes a
                  ``congestion_history`` sequence it is used to tighten /
                  widen that port's confidence bands.
    macro_data:   Macro indicator dict passed to predict_congestion.

    Returns
    -------
    Dict mapping locode str -> CongestionForecast.
    """
    forecasts: dict[str, CongestionForecast] = {}
    for result in port_results:
        if isinstance(result, dict):
            locode = result.get("port_locode") or result.get("locode", "")
            congestion = result.get("current_congestion", 0.5)
            history = result.get("congestion_history")
        else:
            locode = result.port_locode
            congestion = getattr(result, "current_congestion", 0.5)
            history = getattr(result, "congestion_history", None)

        if locode:
            forecasts[locode] = predict_congestion(
                locode,
                congestion,
                macro_data=macro_data,
                congestion_history=history,
            )

    return forecasts
