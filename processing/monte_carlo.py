"""Monte Carlo simulation engine for freight rate forecasting.

Simulates future freight rate paths and derives probabilistic forecasts for
each shipping route.

Process: Ornstein-Uhlenbeck mean reversion with optional jumps
---------------------------------------------------------------
Freight rates are **cyclical and mean-reverting**, not a pure random walk:
oversupply pushes rates down → carriers idle capacity → the market tightens →
rates recover. A plain Geometric Brownian Motion (constant drift, constant
vol, log-normal) has *no reversion force*, so long-horizon GBM paths fan out
and drift away from any economically plausible level.

This engine instead simulates the **log-rate** with an Ornstein-Uhlenbeck
(OU) mean-reverting process — the discrete-time recursion of:

    d(lnS) = θ · (μ_long − lnS) · dt  +  σ · dW

  * ``lnS``    — natural log of the freight rate (kept in log-space so the
                 rate itself can never go negative).
  * ``μ_long`` — the long-run equilibrium log-rate the series is pulled
                 toward. Estimated from the trailing history (a blend of the
                 trailing mean and median of log-rates, so a few spikes do
                 not distort the anchor).
  * ``θ``      — the *reversion speed*: how strongly the rate is pulled back
                 toward ``μ_long`` each step. A modest value is used so the
                 pull is gentle, not snap-back. ``θ`` relates to the
                 reversion *half-life* by  half_life = ln(2) / θ.
  * ``σ``      — the per-step volatility of the log-rate (the historical
                 daily log-return std, or an annualised override).
  * ``dW``     — a standard normal shock, one per simulated day.

On top of the diffusion, an optional **Poisson jump term** injects discrete
disruption shocks (a canal closure, a sudden blank-sailing wave). Jumps are
kept *low-intensity by default* (a rare event, modest size) so they add a
realistic fat tail without ever dominating the mean-reverting dynamics.

The model is intentionally simple, transparent and pure-numpy — no new
dependencies, no black-box ML. Every parameter above is documented and
estimated directly from the input series.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from loguru import logger


# ── Process parameters (documented defaults) ────────────────────────────────────
#
# These govern the OU + jump dynamics. They are deliberately conservative so the
# simulation stays well-behaved on the platform's synthetic data.

# Reversion speed θ (per day). 0.025/day ⇒ a reversion half-life of
# ln(2)/0.025 ≈ 28 days — rates drift back toward equilibrium over roughly a
# month, which is gentle enough not to flatten the near-term path.
_DEFAULT_REVERSION_SPEED: float = 0.025

# Weight on the trailing MEAN vs. MEDIAN when estimating the long-run anchor
# μ_long. A blend (mean is responsive, median is robust to spikes).
_MU_MEAN_WEIGHT: float = 0.5

# Poisson jump term — discrete disruption shocks. Kept low-intensity so jumps
# add a fat tail without dominating the diffusion.
_JUMP_INTENSITY_PER_DAY: float = 0.012     # ≈ 1 jump per ~83 days per path
_JUMP_MEAN_LOG: float = 0.0                # jumps are symmetric (up or down)
_JUMP_STD_LOG: float = 0.045               # ~4.5% typical jump size in log-space


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
    # ── Added fields (process transparency; existing fields unchanged) ──────────
    process: str = "ornstein_uhlenbeck_jump"     # name of the path process used
    reversion_speed: float = 0.0                 # θ — OU reversion speed per day
    long_run_rate: float = 0.0                   # equilibrium rate exp(μ_long)
    daily_volatility: float = 0.0                # σ — per-day log-rate volatility


# ── Core simulation ────────────────────────────────────────────────────────────

def simulate_freight_rates(
    freight_data: dict,
    route_id: str,
    n_simulations: int = 500,
    forecast_days: int = 90,
    volatility_override: float | None = None,
) -> MonteCarloResult | None:
    """Run a mean-reverting Monte Carlo simulation for a single route.

    The default path process is **Ornstein-Uhlenbeck mean reversion with an
    optional low-intensity Poisson jump term** (see the module docstring for
    the full process description). The log-rate is pulled toward a long-run
    equilibrium estimated from the trailing history, so long-horizon paths
    stay economically plausible instead of drifting like a GBM random walk.

    Parameters
    ----------
    freight_data:
        Dict mapping route_id -> DataFrame with columns ["date", "rate_usd_per_feu"].
    route_id:
        The route to simulate.
    n_simulations:
        Number of independent rate paths to generate.
    forecast_days:
        Number of calendar days to project forward.
    volatility_override:
        If provided, use this annualised sigma instead of the historical
        estimate. It is converted to a per-day log-rate sigma (÷√252).

    Returns
    -------
    MonteCarloResult or None if data is insufficient.

    Notes
    -----
    The public return shape is unchanged: percentiles, VaR, prob_up/prob_down
    and the end-of-horizon metrics all derive from the simulated path matrix
    exactly as before. Four descriptive fields (``process``,
    ``reversion_speed``, ``long_run_rate``, ``daily_volatility``) are added
    so callers can inspect the process — they are optional and defaulted.
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
    # Everything is estimated in LOG-space: the OU process is run on ln(rate),
    # which keeps simulated rates strictly positive and makes σ a clean
    # log-return volatility.
    log_rates = np.log(rates.clip(lower=1e-6))
    log_returns = log_rates.diff().dropna()

    # σ — per-day volatility of the log-rate.
    if volatility_override is not None:
        # Treat the override as an annualised sigma; convert to a daily value.
        daily_sigma = float(volatility_override) / np.sqrt(252)
    else:
        daily_sigma = float(log_returns.std())  # historical daily log-return std

    # μ_long — long-run equilibrium log-rate. A blend of the trailing mean
    # (responsive) and median (robust to spikes) so a few outliers in the
    # synthetic series do not drag the anchor.
    trailing = log_rates.tail(min(len(log_rates), 180))
    mu_mean = float(trailing.mean())
    mu_median = float(trailing.median())
    mu_long = _MU_MEAN_WEIGHT * mu_mean + (1.0 - _MU_MEAN_WEIGHT) * mu_median

    # θ — reversion speed. A modest constant default; the pull is gentle so
    # the near-term path is still shock-driven, not snapped to the mean.
    theta = _DEFAULT_REVERSION_SPEED

    # Guard against degenerate parameters.
    if not np.isfinite(daily_sigma) or daily_sigma <= 0:
        daily_sigma = 0.01
    if not np.isfinite(mu_long):
        mu_long = float(np.log(current_rate))
    if not np.isfinite(theta) or theta <= 0:
        theta = _DEFAULT_REVERSION_SPEED

    # ── OU + jump simulation ──────────────────────────────────────────────────
    # Discrete recursion with dt = 1 day:
    #   ln S_{t+1} = ln S_t + θ·(μ_long − ln S_t)·dt + σ·Z   (+ jump)
    # Each path starts at the current (log) rate and is pulled toward μ_long.
    # Deterministic seed so the Monte Carlo tab is reproducible across reruns
    # for the same input rate, matching the rest of the platform's synthetic
    # paths (options_screener, freight_scraper, backtester all seed too).
    rng = np.random.default_rng(seed=int(round(current_rate * 100)) & 0xFFFFFFFF)

    # Diffusion shocks: one standard-normal draw per (sim, day).
    diffusion = rng.normal(0.0, daily_sigma, size=(n_simulations, forecast_days))

    # Poisson jump term — discrete disruption shocks. Low-intensity by design:
    # most days see zero jumps; a jump, when it occurs, is a modest log-space
    # move. This adds a realistic fat tail without dominating the OU dynamics.
    jump_counts = rng.poisson(_JUMP_INTENSITY_PER_DAY, size=(n_simulations, forecast_days))
    jump_sizes = rng.normal(_JUMP_MEAN_LOG, _JUMP_STD_LOG, size=(n_simulations, forecast_days))
    jumps = jump_counts * jump_sizes  # zero on non-jump days

    # Step forward day by day. The reversion term depends on the *current*
    # log-rate, so this must be a recursion (not a vectorised cumsum).
    ln_s = np.full(n_simulations, float(np.log(current_rate)))
    log_paths = np.empty((n_simulations, forecast_days))
    for t in range(forecast_days):
        reversion = theta * (mu_long - ln_s)          # pull toward equilibrium
        ln_s = ln_s + reversion + diffusion[:, t] + jumps[:, t]
        log_paths[:, t] = ln_s

    paths = np.exp(log_paths)  # back to rate-space; strictly positive

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

    # VaR 95%: worst expected loss at 95th-percentile downside.
    # Losses are positive; if 5th-percentile final rate > current, VaR = 0.
    p5_final = float(np.percentile(final_rates, 5))
    var_95 = max(0.0, current_rate - p5_final)

    # Day-90 (or last day) metrics.
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
        process="ornstein_uhlenbeck_jump",
        reversion_speed=float(theta),
        long_run_rate=float(np.exp(mu_long)),
        daily_volatility=float(daily_sigma),
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
