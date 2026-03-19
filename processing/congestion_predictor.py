"""
Congestion forecasting for tracked container ports.

Uses simple exponential smoothing with seasonal adjustment and macro pressure
to produce 7-, 14-, and 30-day congestion outlooks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _macro_pressure(macro_data: dict | None) -> float:
    """Derive additive pressure scalar from macro indicators."""
    if not macro_data:
        return 0.0
    pressure = 0.0
    if macro_data.get("BDI_rising", False):
        pressure += 0.03
    pmi = macro_data.get("PMI", 50.0)
    if pmi > 52:
        pressure += 0.02
    return pressure


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
    if pressure >= 0.04:
        factors.append("Elevated macro pressure (BDI rising + PMI > 52)")
    elif pressure >= 0.03:
        factors.append("Baltic Dry Index trending upward")
    elif pressure >= 0.02:
        factors.append("Manufacturing PMI above 52 — demand pull")
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
) -> CongestionForecast:
    """Forecast port congestion over 7, 14, and 30 days.

    Parameters
    ----------
    port_locode:        UN/LOCODE of the port.
    current_congestion: Current congestion level [0, 1].
    macro_data:         Optional dict with keys such as 'BDI_rising' (bool)
                        and 'PMI' (float).

    Returns
    -------
    CongestionForecast dataclass instance.
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

    # 30-day forecast — mean reversion toward 0.5 added
    mean_reversion = 0.5
    predicted_30d = _clamp(
        predicted_14d * (1 - alpha)
        + alpha * (predicted_14d + pressure * 0.6 + (mean_reversion - predicted_14d) * 0.15)
    )

    trend = _trend(current_congestion, predicted_30d)
    peak_date = _peak_risk_date(
        current_congestion, predicted_7d, predicted_14d, predicted_30d, trend
    )
    factors = _driving_factors(port_locode, current_congestion, macro_data, seasonal, pressure)

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
                  the same keys.
    macro_data:   Macro indicator dict passed to predict_congestion.

    Returns
    -------
    Dict mapping locode str -> CongestionForecast.
    """
    forecasts: dict[str, CongestionForecast] = {}
    for result in port_results:
        if isinstance(result, dict):
            locode = result["port_locode"]
            congestion = result.get("current_congestion", 0.5)
        else:
            locode = result.port_locode
            congestion = getattr(result, "current_congestion", 0.5)

        forecasts[locode] = predict_congestion(locode, congestion, macro_data=macro_data)

    return forecasts
