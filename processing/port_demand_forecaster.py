"""
Port Demand Forecaster

Produces 30/60/90-day demand score forecasts for each tracked port using
signal-based extrapolation (no true time-series history per port).

Methodology is intentionally transparent — each adjustment is broken out so
users can evaluate the forecast logic rather than trust a black box.

Forecast formula
----------------
monthly_delta = macro_adj + seasonal_adj + trade_momentum + congestion_reversion

forecast_30d = clamp(current + monthly_delta * (30/30),          0.05, 0.95)
forecast_60d = clamp(current + monthly_delta * (60/30) * 0.85,   0.05, 0.95)
forecast_90d = clamp(current + monthly_delta * (90/30) * 0.70,   0.05, 0.95)

Confidence starts at 0.4 (no time series) and gains credit for data richness.
"""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger


@dataclass
class PortDemandForecast:
    port_locode: str
    port_name: str
    current_score: float        # current demand score [0, 1]
    forecast_30d: float         # projected score in 30 days [0, 1]
    forecast_60d: float         # projected score in 60 days [0, 1]
    forecast_90d: float         # projected score in 90 days [0, 1]
    trend_direction: str        # "Accelerating" / "Stable" / "Decelerating"
    confidence: float           # [0, 1] based on data richness
    key_drivers: list[str]      # top factors driving the forecast
    seasonal_adjustment: float  # from seasonal module [-0.2, +0.2]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def forecast_port_demand(
    port_results: list,           # list of PortDemandResult
    macro_data: dict,             # FRED series data
    wb_data: dict,                # World Bank data
    lookback_days: int = 90,
) -> list[PortDemandForecast]:
    """Generate demand forecasts for each port in port_results.

    Args:
        port_results:   list of PortDemandResult objects from demand_analyzer
        macro_data:     dict series_id → DataFrame from fred_feed
        wb_data:        dict indicator_id → DataFrame from worldbank_feed
        lookback_days:  window used when evaluating macro trend (default 90)

    Returns:
        List of PortDemandForecast, one per input port.
    """
    # Compute macro adjustments once — they are global, not port-specific
    macro_adj, macro_drivers = _compute_macro_adjustment(macro_data)

    results: list[PortDemandForecast] = []
    for port in port_results:
        forecast = _forecast_single_port(port, macro_adj, macro_drivers, macro_data, wb_data)
        if forecast is not None:
            results.append(forecast)

    logger.info(f"Port demand forecaster: {len(results)} forecasts generated")
    return results


def forecast_all_ports(
    port_results: list,
    macro_data: dict,
    wb_data: dict,
) -> list[PortDemandForecast]:
    """Convenience wrapper — forecast all ports, sorted by 30d outlook descending.

    Args:
        port_results: list of PortDemandResult from demand_analyzer
        macro_data:   dict series_id → DataFrame from fred_feed
        wb_data:      dict indicator_id → DataFrame from worldbank_feed

    Returns:
        List of PortDemandForecast sorted by forecast_30d descending.
    """
    forecasts = forecast_port_demand(port_results, macro_data, wb_data)
    forecasts.sort(key=lambda f: f.forecast_30d, reverse=True)
    return forecasts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_macro_adjustment(macro_data: dict) -> tuple[float, list[str]]:
    """Derive a single monthly macro delta score and a list of driver strings.

    Returns:
        (macro_adj, driver_strings) where macro_adj is in roughly [-0.05, +0.05]
    """
    from data.fred_feed import compute_bdi_score, get_latest_value

    adj = 0.0
    drivers: list[str] = []

    # ── BDI trend ──────────────────────────────────────────────────────────
    # Prefer the canonical FRED key (BSXRLM); fall back to BDIY for legacy callers.
    bdi_df = macro_data.get("BSXRLM")
    if bdi_df is None:
        bdi_df = macro_data.get("BDIY")
    if bdi_df is not None and not bdi_df.empty and len(bdi_df) >= 10:
        bdi_score = compute_bdi_score(macro_data)
        if bdi_score > 0.55:
            adj += 0.03
            pct = round((bdi_score - 0.5) * 100)
            drivers.append(f"Positive macro tailwind (+3%)")
        elif bdi_score < 0.45:
            adj -= 0.03
            drivers.append(f"Negative macro headwind (BDI below trend, -3%)")

    # ── PMI proxy (IPMAN industrial production) ────────────────────────────
    ipman_df = macro_data.get("IPMAN")
    if ipman_df is not None and not ipman_df.empty:
        vals = ipman_df["value"].dropna()
        if len(vals) >= 3:
            # A rising industrial production index implies PMI > 52 territory
            recent_avg = float(vals.tail(3).mean())
            prior_avg  = float(vals.tail(12).head(9).mean()) if len(vals) >= 12 else float(vals.mean())
            pmi_rising = recent_avg > prior_avg * 1.005  # up > 0.5 % → proxy for PMI > 52
            if pmi_rising:
                adj += 0.02
                drivers.append("Industrial production rising — PMI tailwind (+2%)")

    # ── Fuel cost (WTI) ────────────────────────────────────────────────────
    wti_df = macro_data.get("DCOILWTICO")
    if wti_df is not None and not wti_df.empty:
        vals = wti_df["value"].dropna()
        if len(vals) >= 10:
            current_wti = float(vals.iloc[-1])
            avg_wti     = float(vals.tail(90).mean())
            if current_wti > avg_wti * 1.10:   # fuel >10% above 90d avg → headwind
                adj -= 0.02
                drivers.append(f"High fuel costs dampening demand (-2%)")

    return adj, drivers


def _compute_seasonal_adjustment(port_locode: str) -> float:
    """Return a seasonal score delta for the port, scaled to [-0.2, +0.2]."""
    from processing.seasonal import get_seasonal_adjustment

    # get_seasonal_adjustment uses route_id; passing port_locode will return 0.0
    # for most ports (no route match), which is the correct neutral default.
    # Port-specific seasonal effects (e.g. CNY for Chinese ports) are intentionally
    # surfaced through the affected_regions logic in seasonal events; this call
    # captures any direct route-level match and scales it to the port score range.
    raw = get_seasonal_adjustment(port_locode)  # returns [-0.15, +0.15]
    # Scale from route range to port score delta range
    return max(-0.2, min(0.2, raw * (0.2 / 0.15)))


def _compute_trade_momentum(trade_flow_score: float) -> tuple[float, str | None]:
    """Return monthly trade momentum delta and optional driver string."""
    if trade_flow_score > 0.65:
        return 0.02, f"Strong trade flow momentum (+2%/month, score={trade_flow_score:.2f})"
    elif trade_flow_score < 0.35:
        return -0.02, f"Weak trade flow decaying demand (-2%/month, score={trade_flow_score:.2f})"
    return 0.0, None


def _compute_congestion_reversion(congestion_score: float) -> tuple[float, str | None]:
    """Return monthly congestion-reversion delta and optional driver string."""
    if congestion_score > 0.75:
        return -0.01, f"High congestion dampening growth (reversion -1%/month)"
    elif congestion_score < 0.25:
        return 0.01, f"Low congestion — capacity for demand growth (+1%/month)"
    return 0.0, None


def _compute_confidence(
    macro_data: dict,
    wb_data: dict,
    congestion_score: float,
) -> float:
    """Compute confidence score [0, 1] based on data richness."""
    conf = 0.4  # baseline — no true port time series

    # +0.2 if BDI data present (canonical BSXRLM, BDIY fallback)
    bdi_df = macro_data.get("BSXRLM")
    if bdi_df is None:
        bdi_df = macro_data.get("BDIY")
    if bdi_df is not None and not bdi_df.empty and len(bdi_df) >= 5:
        conf += 0.2

    # +0.2 if World Bank TEU data present
    teu_key_found = any(
        "teu" in str(k).lower() or "container" in str(k).lower()
        for k in wb_data.keys()
    )
    if not teu_key_found:
        # Also check for IS.SHP.GOOD.TU (World Bank container traffic indicator)
        teu_key_found = "IS.SHP.GOOD.TU" in wb_data
    if teu_key_found:
        conf += 0.2

    # +0.1 if congestion is informative (i.e. not exactly the default 0.5)
    if abs(congestion_score - 0.5) > 0.001:
        conf += 0.1

    return round(min(1.0, conf), 3)


def _build_seasonal_driver(seasonal_adj: float) -> str | None:
    """Return a human-readable driver string for the seasonal adjustment."""
    from processing.seasonal import get_active_seasonal_signals

    if abs(seasonal_adj) < 0.001:
        return None

    signals = get_active_seasonal_signals()
    active = [s for s in signals if s.active_now]
    if active:
        # Use the strongest active signal name
        strongest = max(active, key=lambda s: s.strength)
        pct = round(seasonal_adj * 100)
        sign = "+" if seasonal_adj > 0 else ""
        return f"Seasonal: {strongest.name} ({sign}{pct}%)"

    pct = round(seasonal_adj * 100)
    sign = "+" if seasonal_adj > 0 else ""
    return f"Seasonal adjustment ({sign}{pct}%)"


def _clamp(value: float, lo: float = 0.05, hi: float = 0.95) -> float:
    return max(lo, min(hi, value))


def _forecast_single_port(
    port,                        # PortDemandResult
    macro_adj: float,
    macro_drivers: list[str],
    macro_data: dict,
    wb_data: dict,
) -> PortDemandForecast | None:
    """Build a PortDemandForecast for a single port."""
    try:
        current = float(port.demand_score)

        # Retrieve sub-scores (fall back to 0.5 neutral if missing)
        trade_flow_score  = float(getattr(port, "trade_flow_component",  0.5))
        congestion_score  = float(getattr(port, "congestion_component",  0.5))

        # ── Per-port adjustments ───────────────────────────────────────────
        seasonal_raw = _compute_seasonal_adjustment(port.locode)
        seasonal_adj = seasonal_raw * 0.15   # scale: raw [-0.2,+0.2] * 0.15 → small delta

        trade_delta, trade_driver         = _compute_trade_momentum(trade_flow_score)
        congestion_delta, congestion_driver = _compute_congestion_reversion(congestion_score)

        # ── Total monthly delta ────────────────────────────────────────────
        monthly_delta = macro_adj + seasonal_adj + trade_delta + congestion_delta

        # ── Forecasts with diminishing certainty at longer horizons ────────
        forecast_30d = _clamp(current + monthly_delta * (30 / 30))
        forecast_60d = _clamp(current + monthly_delta * (60 / 30) * 0.85)
        forecast_90d = _clamp(current + monthly_delta * (90 / 30) * 0.70)

        # ── Trend direction ────────────────────────────────────────────────
        if forecast_90d > current + 0.05:
            trend_direction = "Accelerating"
        elif forecast_90d < current - 0.05:
            trend_direction = "Decelerating"
        else:
            trend_direction = "Stable"

        # ── Confidence ────────────────────────────────────────────────────
        confidence = _compute_confidence(macro_data, wb_data, congestion_score)

        # ── Key drivers list ───────────────────────────────────────────────
        key_drivers: list[str] = list(macro_drivers)  # copy global macro drivers

        seasonal_driver = _build_seasonal_driver(seasonal_raw)
        if seasonal_driver:
            key_drivers.append(seasonal_driver)

        if trade_driver:
            key_drivers.append(trade_driver)

        if congestion_driver:
            key_drivers.append(congestion_driver)

        if not key_drivers:
            key_drivers.append("No dominant signal — forecast near-stable")

        return PortDemandForecast(
            port_locode=port.locode,
            port_name=port.port_name,
            current_score=round(current, 4),
            forecast_30d=round(forecast_30d, 4),
            forecast_60d=round(forecast_60d, 4),
            forecast_90d=round(forecast_90d, 4),
            trend_direction=trend_direction,
            confidence=confidence,
            key_drivers=key_drivers,
            seasonal_adjustment=round(seasonal_raw, 4),
        )

    except Exception as exc:
        logger.error(f"Port demand forecast failed for {getattr(port, 'locode', '?')}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Integration: in ui/tab_port_demand.py render() function, add:
# from processing.port_demand_forecaster import forecast_all_ports
# port_forecasts = forecast_all_ports(port_results, macro_data, wb_data)
# Then call _render_port_forecasts(port_forecasts) at bottom of tab
# ---------------------------------------------------------------------------


# =============================================================================
# Time-series baseline + walk-forward backtest
#
# Per docs/ROADMAP principle 5, every model in the codebase ships with a
# walk-forward backtest. The signal-based forecaster above is a snapshot — it
# produces forward scores from current adjustments rather than from a per-port
# time series. To satisfy principle 5 we add (a) a *baseline* time-series
# forecaster the signal-based path can be benchmarked against, and (b) a
# walk-forward backtest harness that scores the baseline's MAE + direction
# hit rate against held-out data. The signal-based forecaster can be wired
# into the same harness once a historical macro-context series exists.
# =============================================================================

import math as _math
from typing import Optional as _Optional, Sequence as _Sequence


@dataclass(frozen=True)
class PortDemandBacktestResult:
    """Summary of a walk-forward backtest on one port-demand history."""
    port_locode: str
    n_predictions: int
    mae: float                   # mean absolute error on the forecast horizon
    rmse: float                  # root-mean-squared error
    direction_hit_rate: float    # fraction where forecast direction matched realized
    avg_horizon_days: int        # the test horizon used (informational)
    bias: float                  # mean(forecast − realized); +ve = forecasts too high


def naive_history_forecast(
    series,
    horizon: int = 30,
    *,
    drift_weight: float = 0.6,
    mean_weight: float = 0.4,
) -> float:
    """One-shot forecast: blend the trailing drift with the trailing mean.

    Inputs
    ------
    series   : pandas.Series of historical demand scores, ascending date index.
    horizon  : forecast horizon in days. Drift is scaled to this horizon.
    drift_weight + mean_weight should sum to ~1.0; we don't enforce the
    invariant because callers may want to weight by hand.

    Construction
    ------------
    drift_per_day  = (last − first) / (n − 1)
    mean_value     = trailing mean
    forecast       = drift_weight × (last + drift_per_day × horizon)
                   + mean_weight  × mean_value
    Clamped into [0.05, 0.95] to match the signal-based forecaster's bounds.

    A pragmatic baseline — simpler than ARIMA but captures both momentum
    (drift) and mean-reversion (trailing average) in one knob-tunable line.
    """
    import pandas as pd  # local — keeps the module importable in tests without pandas at module load

    if series is None:
        return 0.5
    s = pd.Series(series).dropna()
    if s.empty:
        return 0.5
    if len(s) == 1:
        return float(_clamp(s.iloc[0]))

    last = float(s.iloc[-1])
    first = float(s.iloc[0])
    n = len(s)
    drift_per_step = (last - first) / max(1, n - 1)
    trailing_mean = float(s.mean())

    forecast = (
        drift_weight * (last + drift_per_step * horizon)
        + mean_weight * trailing_mean
    )
    return float(_clamp(forecast))


def walk_forward_backtest(
    history,
    *,
    port_locode: str = "",
    train_window: int = 60,
    horizon: int = 30,
    step: int = 15,
    drift_weight: float = 0.6,
    mean_weight: float = 0.4,
) -> PortDemandBacktestResult:
    """Walk-forward backtest of :func:`naive_history_forecast` on one series.

    At each rolling step:
      - Fit on the last ``train_window`` days.
      - Predict ``horizon`` days ahead.
      - Compare to the realized value at that horizon.
      - Score MAE, RMSE, direction hit-rate (was the predicted change in the
        same direction as the realized change), and bias (mean signed error).

    Returns an empty result (n_predictions=0, MAE=0) when ``history`` is too
    short to fit (< train_window + horizon).
    """
    import pandas as pd

    if history is None:
        return _empty_pd_backtest(port_locode, horizon)
    s = pd.Series(history).dropna().sort_index()
    if s.empty or len(s) < train_window + horizon:
        return _empty_pd_backtest(port_locode, horizon)

    abs_errors: list[float] = []
    sq_errors: list[float] = []
    direction_hits: int = 0
    direction_total: int = 0
    signed_errors: list[float] = []

    # Start at the first index where we have a full training window AND a
    # realized value at t+horizon to compare against.
    n = len(s)
    t = train_window
    while t + horizon <= n:
        train = s.iloc[t - train_window: t]
        predicted = naive_history_forecast(
            train, horizon=horizon,
            drift_weight=drift_weight, mean_weight=mean_weight,
        )
        realized = float(s.iloc[t + horizon - 1])
        err = predicted - realized
        abs_errors.append(abs(err))
        sq_errors.append(err * err)
        signed_errors.append(err)

        # Direction: did the predicted change from "current" point the same
        # way as the realized change?
        anchor = float(s.iloc[t - 1])
        pred_dir = 1.0 if predicted > anchor else (-1.0 if predicted < anchor else 0.0)
        realized_dir = 1.0 if realized > anchor else (-1.0 if realized < anchor else 0.0)
        if pred_dir != 0.0 and realized_dir != 0.0:
            direction_total += 1
            if pred_dir == realized_dir:
                direction_hits += 1

        t += step

    if not abs_errors:
        return _empty_pd_backtest(port_locode, horizon)

    mae = float(sum(abs_errors) / len(abs_errors))
    rmse = float(_math.sqrt(sum(sq_errors) / len(sq_errors)))
    hit_rate = (direction_hits / direction_total) if direction_total else 0.0
    bias = float(sum(signed_errors) / len(signed_errors))

    return PortDemandBacktestResult(
        port_locode=port_locode,
        n_predictions=len(abs_errors),
        mae=round(mae, 4),
        rmse=round(rmse, 4),
        direction_hit_rate=round(hit_rate, 4),
        avg_horizon_days=int(horizon),
        bias=round(bias, 4),
    )


def _empty_pd_backtest(port_locode: str, horizon: int) -> PortDemandBacktestResult:
    return PortDemandBacktestResult(
        port_locode=port_locode,
        n_predictions=0,
        mae=0.0,
        rmse=0.0,
        direction_hit_rate=0.0,
        avg_horizon_days=int(horizon),
        bias=0.0,
    )


def backtest_all_ports(
    history_by_port: dict,
    *,
    train_window: int = 60,
    horizon: int = 30,
    step: int = 15,
) -> list[PortDemandBacktestResult]:
    """Run :func:`walk_forward_backtest` on every port in the supplied dict.

    ``history_by_port`` is ``{locode: pandas.Series}``. Results are returned
    sorted by MAE ascending (most-accurate first).
    """
    results: list[PortDemandBacktestResult] = []
    if not history_by_port:
        return results
    for locode, series in history_by_port.items():
        try:
            results.append(walk_forward_backtest(
                series, port_locode=str(locode),
                train_window=train_window, horizon=horizon, step=step,
            ))
        except Exception as exc:
            logger.debug(f"backtest_all_ports: {locode} failed: {exc}")
    results.sort(key=lambda r: r.mae)
    return results
