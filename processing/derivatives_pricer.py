"""
Freight Derivatives Pricing Engine

Prices Forward Freight Agreements (FFAs) and freight rate options using
Black-Scholes adapted for shipping markets.

FFAs allow shippers and carriers to lock in future freight rates, hedging
against rate volatility. Options (Caps, Floors, Collars) add asymmetric
protection.

All Black-Scholes calculations use the math module only — no scipy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from loguru import logger


# ── Black-Scholes helpers (math-only, no scipy) ───────────────────────────────

def _std_norm_cdf(x: float) -> float:
    """Cumulative distribution function for the standard normal distribution.

    Uses the complementary error function via math.erfc, which is available
    in Python's standard library.  Accurate to ~7 significant figures.
    """
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _black_scholes_call(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """Black-Scholes call (cap) price.

    Parameters
    ----------
    S     : current spot rate (USD/FEU)
    K     : strike rate (USD/FEU)
    T     : time to expiry in years
    sigma : annualised volatility (e.g. 0.35 = 35%)
    r     : risk-free rate (default 0 for freight)

    Returns
    -------
    Option premium in USD/FEU.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, S - K)
    sigma_sq_T = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / sigma_sq_T
    d2 = d1 - sigma_sq_T
    return S * math.exp(-r * T) * _std_norm_cdf(d1) - K * math.exp(-r * T) * _std_norm_cdf(d2)


def _black_scholes_put(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """Black-Scholes put (floor) price via put-call parity."""
    call = _black_scholes_call(S, K, T, sigma, r)
    # put-call parity: P = C - S*e^(-rT) + K*e^(-rT)
    return call - S * math.exp(-r * T) + K * math.exp(-r * T)


def _bs_delta_call(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """Delta of a Black-Scholes call option."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 1.0 if S > K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return _std_norm_cdf(d1)


def _bs_delta_put(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """Delta of a Black-Scholes put option (negative value, returned as absolute)."""
    return _bs_delta_call(S, K, T, sigma, r) - 1.0


# ── Historical volatility helper ──────────────────────────────────────────────

def _compute_hist_vol(df, annualise: bool = True) -> float:
    """Estimate annualised historical volatility from log-returns.

    Parameters
    ----------
    df : pd.DataFrame with column 'rate_usd_per_feu' sorted ascending by date.

    Returns
    -------
    Annualised sigma (e.g. 0.35 for 35 % vol).  Falls back to 0.30 if
    insufficient data.
    """
    try:
        rates = df.sort_values("date")["rate_usd_per_feu"].dropna().tolist()
        if len(rates) < 5:
            return 0.30
        log_returns = [
            math.log(rates[i] / rates[i - 1])
            for i in range(1, len(rates))
            if rates[i - 1] > 0 and rates[i] > 0
        ]
        if not log_returns:
            return 0.30
        n = len(log_returns)
        mean_r = sum(log_returns) / n
        variance = sum((r - mean_r) ** 2 for r in log_returns) / max(n - 1, 1)
        daily_vol = math.sqrt(variance)
        return daily_vol * math.sqrt(252) if annualise else daily_vol
    except Exception:
        return 0.30


def _demand_basis_adjustment(freight_data: dict, route_id: str) -> float:
    """Estimate a basis adjustment from recent demand imbalance.

    Positive = supply tight / demand excess → FFA trades above fair value.
    Negative = oversupply → FFA discount.

    Uses the 7-day momentum of rates as a proxy.
    """
    try:
        df = freight_data.get(route_id)
        if df is None or df.empty or len(df) < 10:
            return 0.0
        df = df.sort_values("date")
        rates = df["rate_usd_per_feu"].dropna()
        current = float(rates.iloc[-1])
        rate_7d_ago = float(rates.iloc[-8]) if len(rates) >= 8 else current
        mom_7d = (current - rate_7d_ago) / rate_7d_ago if rate_7d_ago != 0 else 0.0
        # Basis ≈ ±5% of spot for extreme imbalance
        basis_pct = max(-0.05, min(0.05, mom_7d * 0.5))
        return current * basis_pct
    except Exception:
        return 0.0


# ── Settlement period label ───────────────────────────────────────────────────

_MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _settlement_label(months_forward: int) -> str:
    """Generate a settlement period string like 'Q2 2026' or 'May 2026'."""
    import datetime
    target = datetime.date.today()
    # advance by months_forward months
    month = target.month - 1 + months_forward
    year = target.year + month // 12
    month = month % 12 + 1
    if months_forward >= 12:
        return "Cal " + str(year)
    if months_forward in (3, 6, 9, 12):
        quarter = (month - 1) // 3 + 1
        return "Q" + str(quarter) + " " + str(year)
    return _MONTH_NAMES[month - 1] + " " + str(year)


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class FFAContract:
    """Forward Freight Agreement pricing result."""
    route_id: str
    settlement_period: str        # e.g. "Q2 2026", "Cal 2026", "May 2026"
    current_spot: float           # USD/FEU (live spot rate)
    ffa_price: float              # Fair value FFA price (USD/FEU)
    basis: float                  # ffa_price − current_spot
    implied_volatility: float     # Annualised sigma
    days_to_settlement: int       # Calendar days to settlement
    carry_cost: float             # Carry component (USD/FEU)
    confidence_interval: tuple    # 90 % CI (lower, upper) in USD/FEU


@dataclass
class FreightOption:
    """Freight rate option pricing result (Cap, Floor, or Collar)."""
    option_type: str              # "CAP", "FLOOR", "COLLAR"
    strike_rate: float            # USD/FEU
    premium_per_feu: float        # USD/FEU (total premium for collar = cap + floor)
    delta: float                  # 0-1 for calls; absolute value shown for puts
    breakeven_rate: float         # Rate at which option becomes profitable (net of premium)
    max_protection: float         # For cap: rate above which shipper is fully hedged
    recommended_for: str          # Short explanation of who benefits


# ── Core pricing functions ────────────────────────────────────────────────────

def price_ffa(
    route_id: str,
    freight_data: dict,
    months_forward: int = 3,
) -> Optional[FFAContract]:
    """Price a Forward Freight Agreement for a given route and tenor.

    Parameters
    ----------
    route_id       : Route identifier (key in freight_data).
    freight_data   : Dict mapping route_id -> DataFrame with 'rate_usd_per_feu'.
    months_forward : Tenor in months (1-12).

    Returns
    -------
    FFAContract or None if data is insufficient.
    """
    df = freight_data.get(route_id)
    if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
        logger.debug("price_ffa: no data for route " + route_id)
        return None

    df = df.sort_values("date").copy()
    if len(df) < 5:
        logger.debug("price_ffa: insufficient rows for route " + route_id)
        return None

    # ── Spot rate ─────────────────────────────────────────────────────────────
    current_spot = float(df["rate_usd_per_feu"].dropna().iloc[-1])
    if current_spot <= 0:
        return None

    # ── Volatility ────────────────────────────────────────────────────────────
    sigma = _compute_hist_vol(df)

    # ── Time to settlement ────────────────────────────────────────────────────
    days_to_settlement = max(1, int(months_forward * 30.44))
    T = days_to_settlement / 365.0

    # ── FFA fair value = spot * exp(carry * T) ────────────────────────────────
    carry_rate = 0.02          # risk-free rate approximation
    carry_cost = current_spot * (math.exp(carry_rate * T) - 1.0)
    ffa_fair = current_spot * math.exp(carry_rate * T)

    # ── Basis adjustment from demand imbalance ────────────────────────────────
    basis_adj = _demand_basis_adjustment(freight_data, route_id)
    ffa_price = ffa_fair + basis_adj
    basis = ffa_price - current_spot

    # ── 90% Confidence interval using log-normal distribution ─────────────────
    # CI: spot * exp((carry - 0.5*sigma^2)*T ± z * sigma * sqrt(T))
    z90 = 1.6449  # 90% CI z-score
    drift = (carry_rate - 0.5 * sigma ** 2) * T
    spread = z90 * sigma * math.sqrt(T)
    ci_lower = current_spot * math.exp(drift - spread)
    ci_upper = current_spot * math.exp(drift + spread)

    settlement_period = _settlement_label(months_forward)

    return FFAContract(
        route_id=route_id,
        settlement_period=settlement_period,
        current_spot=current_spot,
        ffa_price=round(ffa_price, 2),
        basis=round(basis, 2),
        implied_volatility=round(sigma, 4),
        days_to_settlement=days_to_settlement,
        carry_cost=round(carry_cost, 2),
        confidence_interval=(round(ci_lower, 2), round(ci_upper, 2)),
    )


def price_freight_cap(
    route_id: str,
    freight_data: dict,
    strike_multiplier: float = 1.20,
) -> Optional[FreightOption]:
    """Price a freight rate Cap option (call) for shipper protection.

    The cap pays the shipper the difference between the market rate and the
    strike when rates exceed the strike, capping their effective cost.

    Parameters
    ----------
    route_id          : Route identifier.
    freight_data      : Dict mapping route_id -> DataFrame.
    strike_multiplier : Strike as a multiple of current spot (default 1.20 = 20% OTM).

    Returns
    -------
    FreightOption or None.
    """
    df = freight_data.get(route_id)
    if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
        return None

    df = df.sort_values("date").copy()
    if len(df) < 5:
        return None

    current_rate = float(df["rate_usd_per_feu"].dropna().iloc[-1])
    if current_rate <= 0:
        return None

    sigma = _compute_hist_vol(df)
    T = 0.25   # 3 months
    strike = current_rate * strike_multiplier

    premium = _black_scholes_call(current_rate, strike, T, sigma)
    delta = _bs_delta_call(current_rate, strike, T, sigma)

    # Breakeven: rate at which option payoff equals premium paid
    breakeven = strike + premium

    pct_above = int((strike_multiplier - 1.0) * 100)
    recommended = (
        "Importers and shippers on fixed-price contracts. "
        "Protects against rate spikes above "
        + str(pct_above)
        + "% of today's spot."
    )

    return FreightOption(
        option_type="CAP",
        strike_rate=round(strike, 2),
        premium_per_feu=round(premium, 2),
        delta=round(delta, 4),
        breakeven_rate=round(breakeven, 2),
        max_protection=round(strike, 2),
        recommended_for=recommended,
    )


def price_freight_floor(
    route_id: str,
    freight_data: dict,
    strike_multiplier: float = 0.80,
) -> Optional[FreightOption]:
    """Price a freight rate Floor option (put) for carrier revenue protection.

    The floor guarantees the carrier a minimum effective rate when spot falls
    below the strike.

    Parameters
    ----------
    route_id          : Route identifier.
    freight_data      : Dict mapping route_id -> DataFrame.
    strike_multiplier : Strike as a multiple of current spot (default 0.80 = 20% ITM).

    Returns
    -------
    FreightOption or None.
    """
    df = freight_data.get(route_id)
    if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
        return None

    df = df.sort_values("date").copy()
    if len(df) < 5:
        return None

    current_rate = float(df["rate_usd_per_feu"].dropna().iloc[-1])
    if current_rate <= 0:
        return None

    sigma = _compute_hist_vol(df)
    T = 0.25   # 3 months
    strike = current_rate * strike_multiplier

    premium = _black_scholes_put(current_rate, strike, T, sigma)
    delta_raw = _bs_delta_put(current_rate, strike, T, sigma)
    delta_abs = abs(delta_raw)

    # Breakeven for put: carrier receives strike - premium; net floor = strike - premium
    breakeven = strike - premium

    pct_below = int((1.0 - strike_multiplier) * 100)
    recommended = (
        "Ocean carriers and asset owners seeking minimum revenue guarantees. "
        "Locks in a floor "
        + str(pct_below)
        + "% below current spot."
    )

    return FreightOption(
        option_type="FLOOR",
        strike_rate=round(strike, 2),
        premium_per_feu=round(premium, 2),
        delta=round(delta_abs, 4),
        breakeven_rate=round(breakeven, 2),
        max_protection=round(strike, 2),
        recommended_for=recommended,
    )


def price_freight_collar(
    route_id: str,
    freight_data: dict,
    cap_multiplier: float = 1.20,
    floor_multiplier: float = 0.85,
) -> Optional[FreightOption]:
    """Price a Collar (long cap + short floor) for zero-cost or reduced-cost hedging.

    The shipper buys a cap and sells a floor, offsetting premium costs.  The
    resulting net premium can be near zero depending on strikes chosen.

    Returns
    -------
    FreightOption or None.
    """
    df = freight_data.get(route_id)
    if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
        return None

    df = df.sort_values("date").copy()
    if len(df) < 5:
        return None

    current_rate = float(df["rate_usd_per_feu"].dropna().iloc[-1])
    if current_rate <= 0:
        return None

    sigma = _compute_hist_vol(df)
    T = 0.25

    cap_strike = current_rate * cap_multiplier
    floor_strike = current_rate * floor_multiplier

    cap_premium = _black_scholes_call(current_rate, cap_strike, T, sigma)
    floor_premium = _black_scholes_put(current_rate, floor_strike, T, sigma)

    # Net cost = buy cap, sell floor (receive floor premium)
    net_premium = cap_premium - floor_premium
    net_premium_abs = abs(net_premium)

    # Delta of collar ≈ delta of cap - delta of floor (put delta is negative)
    delta_cap = _bs_delta_call(current_rate, cap_strike, T, sigma)
    delta_floor = abs(_bs_delta_put(current_rate, floor_strike, T, sigma))
    collar_delta = delta_cap - delta_floor

    # Breakeven: rate at which the collar nets to zero P&L (from cap side)
    breakeven = cap_strike + max(0.0, net_premium)

    pct_cap = int((cap_multiplier - 1.0) * 100)
    pct_floor = int((1.0 - floor_multiplier) * 100)
    cost_label = (
        "near zero-cost" if net_premium_abs < current_rate * 0.01
        else ("net cost of $" + "{:.0f}".format(net_premium) + "/FEU")
    )
    recommended = (
        "Shippers wanting rate certainty. Caps upside at +"
        + str(pct_cap)
        + "% and maintains participation down to -"
        + str(pct_floor)
        + "% ("
        + cost_label
        + ")."
    )

    return FreightOption(
        option_type="COLLAR",
        strike_rate=round(cap_strike, 2),
        premium_per_feu=round(net_premium_abs, 2),
        delta=round(collar_delta, 4),
        breakeven_rate=round(breakeven, 2),
        max_protection=round(cap_strike, 2),
        recommended_for=recommended,
    )


# ── Hedging recommendation engine ─────────────────────────────────────────────

def get_hedging_recommendation(
    route_id: str,
    freight_data: dict,
    macro_data: dict,
) -> dict:
    """Analyse rate trend and volatility to generate a hedging recommendation.

    Parameters
    ----------
    route_id     : Route identifier.
    freight_data : Dict mapping route_id -> DataFrame with 'rate_usd_per_feu'.
    macro_data   : Dict of macro series (used for context; keys may vary).

    Returns
    -------
    dict with keys:
        action          : "BUY_CAP" | "BUY_FLOOR" | "WAIT" | "COLLAR"
        rationale       : Human-readable explanation.
        estimated_annual_saving_per_feu : Estimated USD/FEU annual saving.
        implied_vol     : Annualised sigma used in analysis.
        rate_trend      : "RISING" | "FALLING" | "STABLE"
        urgency         : "HIGH" | "MODERATE" | "LOW"
    """
    df = freight_data.get(route_id)
    if df is None or df.empty or "rate_usd_per_feu" not in df.columns or len(df) < 10:
        return {
            "action": "WAIT",
            "rationale": "Insufficient rate history to generate recommendation.",
            "estimated_annual_saving_per_feu": 0.0,
            "implied_vol": 0.0,
            "rate_trend": "STABLE",
            "urgency": "LOW",
        }

    df = df.sort_values("date").copy()
    rates = df["rate_usd_per_feu"].dropna()
    current = float(rates.iloc[-1])

    sigma = _compute_hist_vol(df)

    # ── Trend detection ───────────────────────────────────────────────────────
    rate_30d_ago = float(rates.iloc[-31]) if len(rates) >= 31 else current
    rate_7d_ago = float(rates.iloc[-8]) if len(rates) >= 8 else current
    mom_30d = (current - rate_30d_ago) / rate_30d_ago if rate_30d_ago > 0 else 0.0
    mom_7d = (current - rate_7d_ago) / rate_7d_ago if rate_7d_ago > 0 else 0.0

    if mom_30d > 0.07:
        trend = "RISING"
    elif mom_30d < -0.07:
        trend = "FALLING"
    else:
        trend = "STABLE"

    # ── Volatility regime ─────────────────────────────────────────────────────
    high_vol = sigma > 0.40
    moderate_vol = 0.20 <= sigma <= 0.40

    # ── Decision logic ────────────────────────────────────────────────────────
    if trend == "RISING" and (high_vol or mom_7d > 0.03):
        action = "BUY_CAP"
        urgency = "HIGH" if high_vol else "MODERATE"
        rationale = (
            "Rates are trending up "
            + "{:+.1%}".format(mom_30d)
            + " over 30d with annualised vol of "
            + "{:.0%}".format(sigma)
            + ". Buying a cap now locks in protection before further rate rises."
        )
        # Annual saving estimate: expected excess above strike over 4 quarters
        cap = price_freight_cap(route_id, freight_data)
        if cap:
            expected_excess = max(0.0, current * (1 + mom_30d * 4) - cap.strike_rate)
            annual_saving = expected_excess * 4 - cap.premium_per_feu * 4
        else:
            annual_saving = current * 0.05

    elif trend == "FALLING" and mom_7d < -0.02:
        action = "BUY_FLOOR"
        urgency = "MODERATE"
        rationale = (
            "Rates are declining "
            + "{:+.1%}".format(mom_30d)
            + " over 30d. Carriers should buy a floor to guarantee minimum revenue "
            "through the soft market."
        )
        floor = price_freight_floor(route_id, freight_data)
        if floor:
            expected_shortfall = max(0.0, floor.strike_rate - current * (1 + mom_30d * 4))
            annual_saving = expected_shortfall * 4 - floor.premium_per_feu * 4
        else:
            annual_saving = current * 0.04

    elif high_vol and trend == "STABLE":
        action = "COLLAR"
        urgency = "MODERATE"
        rationale = (
            "High volatility ("
            + "{:.0%}".format(sigma)
            + ") with no clear directional trend. A collar strategy (buy cap, sell floor) "
            "provides two-sided rate certainty at near-zero net cost."
        )
        collar = price_freight_collar(route_id, freight_data)
        if collar:
            annual_saving = current * sigma * 0.3 * 4 - collar.premium_per_feu * 4
        else:
            annual_saving = current * 0.03

    elif moderate_vol and trend == "RISING":
        action = "BUY_CAP"
        urgency = "LOW"
        rationale = (
            "Moderate vol ("
            + "{:.0%}".format(sigma)
            + ") and gradual upward drift suggest hedging is prudent but not urgent. "
            "Consider a cap at 15-20% OTM for cost-effective protection."
        )
        annual_saving = current * 0.03

    else:
        action = "WAIT"
        urgency = "LOW"
        rationale = (
            "Rate environment is stable (30d momentum "
            + "{:+.1%}".format(mom_30d)
            + ", vol "
            + "{:.0%}".format(sigma)
            + "). Hedge costs currently outweigh expected benefit. Monitor for vol expansion."
        )
        annual_saving = 0.0

    annual_saving = round(max(0.0, annual_saving), 2)

    return {
        "action": action,
        "rationale": rationale,
        "estimated_annual_saving_per_feu": annual_saving,
        "implied_vol": round(sigma, 4),
        "rate_trend": trend,
        "urgency": urgency,
    }


# ── Batch helpers used by the UI ──────────────────────────────────────────────

def price_all_ffas(
    freight_data: dict,
    months_forward: int = 3,
) -> dict[str, FFAContract]:
    """Price FFAs for all routes available in freight_data."""
    results = {}
    for route_id in freight_data:
        try:
            ffa = price_ffa(route_id, freight_data, months_forward)
            if ffa:
                results[route_id] = ffa
        except Exception as exc:
            logger.debug("price_all_ffas: failed for " + route_id + ": " + str(exc))
    return results


def get_term_structure(
    route_id: str,
    freight_data: dict,
    tenors: list | None = None,
) -> list[dict]:
    """Return FFA prices across multiple tenors for a term-structure chart.

    Parameters
    ----------
    route_id     : Route identifier.
    freight_data : Dict mapping route_id -> DataFrame.
    tenors       : List of integer month tenors; defaults to [1, 2, 3, 6, 12].

    Returns
    -------
    List of dicts: [{"months": int, "label": str, "ffa_price": float}, ...]
    """
    if tenors is None:
        tenors = [1, 2, 3, 6, 12]
    term_structure = []
    for m in tenors:
        ffa = price_ffa(route_id, freight_data, m)
        if ffa:
            term_structure.append({
                "months": m,
                "label": ffa.settlement_period,
                "ffa_price": ffa.ffa_price,
                "basis": ffa.basis,
            })
    return term_structure


def get_all_hedging_recommendations(
    freight_data: dict,
    macro_data: dict,
) -> dict[str, dict]:
    """Generate hedging recommendations for all routes."""
    recommendations = {}
    for route_id in freight_data:
        try:
            rec = get_hedging_recommendation(route_id, freight_data, macro_data)
            recommendations[route_id] = rec
        except Exception as exc:
            logger.debug("hedging recommendation failed for " + route_id + ": " + str(exc))
    return recommendations
