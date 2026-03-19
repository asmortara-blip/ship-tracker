"""Monte Carlo simulation engine for freight rate forecasting.

Uses Geometric Brownian Motion (GBM) to simulate future freight rate paths
and derive probabilistic forecasts for each shipping route.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from loguru import logger


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class MonteCarloResult:
    route_id: str
    n_simulations: int
    forecast_days: int
    current_rate: float
    simulated_paths: list[list[float]]          # shape: [n_sims, forecast_days]
    percentiles: dict[str, list[float]]          # "p5","p25","p50","p75","p95"
    prob_rate_increase: float
    prob_rate_decrease: float
    var_95: float                                # 95th-percentile loss from current
    expected_rate_90d: float                     # p50 at day 90 (or last day)
    bull_case_90d: float                         # p90 at day 90
    bear_case_90d: float                         # p10 at day 90
    confidence_interval_90d: tuple[float, float] # (p5, p95)


# ── Core simulation ────────────────────────────────────────────────────────────

def simulate_freight_rates(
    freight_data: dict,
    route_id: str,
    n_simulations: int = 500,
    forecast_days: int = 90,
    volatility_override: float | None = None,
) -> MonteCarloResult | None:
    """Run a GBM Monte Carlo simulation for a single route.

    Parameters
    ----------
    freight_data:
        Dict mapping route_id -> DataFrame with columns ["date", "rate_usd_per_feu"].
    route_id:
        The route to simulate.
    n_simulations:
        Number of independent price paths to generate.
    forecast_days:
        Number of calendar days to project forward.
    volatility_override:
        If provided, use this annualised sigma instead of the historical estimate.

    Returns
    -------
    MonteCarloResult or None if data is insufficient.
    """
    df = freight_data.get(route_id)
    if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
        logger.debug(f"MC: no data for {route_id}")
        return None

    df = df.sort_values("date").copy()
    rates = df["rate_usd_per_feu"].dropna()
    if len(rates) < 10:
        logger.debug(f"MC: insufficient data for {route_id} ({len(rates)} rows)")
        return None

    current_rate = float(rates.iloc[-1])
    if current_rate <= 0:
        logger.debug(f"MC: non-positive current rate for {route_id}: {current_rate}")
        return None

    # ── Parameter estimation ──────────────────────────────────────────────────
    log_returns = np.log(rates / rates.shift(1)).dropna()

    mu = float(log_returns.mean())          # daily mean log-return

    if volatility_override is not None:
        # Treat override as annualised sigma; convert to daily
        sigma = float(volatility_override) / np.sqrt(252)
    else:
        sigma = float(log_returns.std())    # daily sigma

    # Guard against degenerate parameters
    if sigma <= 0 or not np.isfinite(mu) or not np.isfinite(sigma):
        sigma = 0.01
        mu = 0.0

    # ── GBM simulation ────────────────────────────────────────────────────────
    # Annualised drift & vol → daily increments
    daily_mu = mu / 252          # already a daily value; keep consistent with spec
    daily_sigma = sigma / np.sqrt(252)

    # shape: (n_simulations, forecast_days)
    rng = np.random.default_rng()
    random_shocks = rng.normal(daily_mu, daily_sigma, size=(n_simulations, forecast_days))

    # Cumulative product of (1 + approximate daily return via exp of log-return)
    daily_factors = np.exp(random_shocks)        # exp(log-return) = return factor
    cumulative = np.cumprod(daily_factors, axis=1)
    paths = current_rate * cumulative             # shape: (n_simulations, forecast_days)

    # ── Percentile bands ─────────────────────────────────────────────────────
    pct_levels = {"p5": 5, "p25": 25, "p50": 50, "p75": 75, "p95": 95}
    percentiles: dict[str, list[float]] = {}
    for key, level in pct_levels.items():
        pct_vals = np.percentile(paths, level, axis=0)   # shape: (forecast_days,)
        percentiles[key] = pct_vals.tolist()

    # ── End-of-horizon metrics ────────────────────────────────────────────────
    final_rates = paths[:, -1]                           # shape: (n_simulations,)

    prob_increase = float(np.mean(final_rates > current_rate))
    prob_decrease = float(np.mean(final_rates < current_rate))

    # VaR 95%: worst expected loss at 95th-percentile downside
    # Losses are positive; if 5th-percentile final rate > current, VaR = 0
    p5_final = float(np.percentile(final_rates, 5))
    var_95 = max(0.0, current_rate - p5_final)

    # Day-90 (or last day) metrics
    day90_idx = min(89, forecast_days - 1)
    day90_rates = paths[:, day90_idx]

    expected_rate_90d = float(np.percentile(day90_rates, 50))
    bull_case_90d = float(np.percentile(day90_rates, 90))
    bear_case_90d = float(np.percentile(day90_rates, 10))
    ci_p5 = float(np.percentile(day90_rates, 5))
    ci_p95 = float(np.percentile(day90_rates, 95))

    return MonteCarloResult(
        route_id=route_id,
        n_simulations=n_simulations,
        forecast_days=forecast_days,
        current_rate=current_rate,
        simulated_paths=paths.tolist(),
        percentiles=percentiles,
        prob_rate_increase=prob_increase,
        prob_rate_decrease=prob_decrease,
        var_95=var_95,
        expected_rate_90d=expected_rate_90d,
        bull_case_90d=bull_case_90d,
        bear_case_90d=bear_case_90d,
        confidence_interval_90d=(ci_p5, ci_p95),
    )


# ── Multi-route helpers ────────────────────────────────────────────────────────

def simulate_all_routes(
    freight_data: dict,
    n_simulations: int = 300,
) -> dict[str, MonteCarloResult]:
    """Run Monte Carlo simulation for every route in freight_data.

    Returns a dict mapping route_id -> MonteCarloResult for successful runs.
    """
    results: dict[str, MonteCarloResult] = {}
    for route_id in freight_data:
        try:
            result = simulate_freight_rates(
                freight_data,
                route_id,
                n_simulations=n_simulations,
            )
            if result is not None:
                results[route_id] = result
        except Exception as exc:
            logger.debug(f"MC simulation failed for {route_id}: {exc}")
    return results


def get_highest_upside_routes(
    results: dict[str, MonteCarloResult],
    top_n: int = 5,
) -> list[MonteCarloResult]:
    """Return the top-N routes ranked by expected upside at 90 days.

    Upside is defined as (bull_case_90d - current_rate) / current_rate.
    """
    def _upside(r: MonteCarloResult) -> float:
        if r.current_rate <= 0:
            return 0.0
        return (r.bull_case_90d - r.current_rate) / r.current_rate

    return sorted(results.values(), key=_upside, reverse=True)[:top_n]


def get_risk_adjusted_opportunity(
    result: MonteCarloResult,
    risk_free_rate: float = 0.04,
) -> float:
    """Return a Sharpe-like ratio for a Monte Carlo result.

    Ratio = (expected_return_90d - risk_free_rate) / volatility

    expected_return_90d = (expected_rate_90d - current_rate) / current_rate
    volatility          = std of final-day rates across all simulations, as
                          a fraction of current_rate.

    Returns 0.0 if computation is not possible.
    """
    if result.current_rate <= 0:
        return 0.0

    expected_return_90d = (result.expected_rate_90d - result.current_rate) / result.current_rate

    # Compute volatility from the simulated final-day distribution
    final_rates = [path[-1] for path in result.simulated_paths]
    if len(final_rates) < 2:
        return 0.0

    vol = float(np.std(final_rates)) / result.current_rate
    if vol <= 0:
        return 0.0

    return (expected_return_90d - risk_free_rate) / vol
