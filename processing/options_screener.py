"""
Options screener for shipping stocks.
Generates realistic mock options data with Greeks, IV surface, and flow analysis.
"""

from dataclasses import dataclass
from typing import Optional
import numpy as np
from datetime import datetime, timedelta


@dataclass
class OptionsData:
    ticker: str
    expiry: str
    strike: float
    call_put: str           # "C" or "P"
    bid: float
    ask: float
    iv: float               # implied volatility (annualised, e.g. 0.65 = 65%)
    delta: float
    gamma: float
    theta: float
    vega: float
    oi: int                 # open interest
    volume: int
    underlying_price: float
    moneyness: float        # strike / underlying_price


# ── Ticker universe ────────────────────────────────────────────────────────────

_TICKER_PRICES: dict[str, dict] = {
    "ZIM":  {"price": 14.50, "vol_base": 0.85},
    "MATX": {"price": 94.00, "vol_base": 0.42},
    "DAC":  {"price": 68.00, "vol_base": 0.55},
    "SBLK": {"price": 18.50, "vol_base": 0.70},
    "STNG": {"price": 52.00, "vol_base": 0.60},
    "GSL":  {"price": 22.00, "vol_base": 0.65},
}

_EXPIRY_OFFSETS_DAYS: list[int] = [14, 28, 42, 70, 105, 182]


def _generate_expiry_dates() -> list[str]:
    today = datetime.today()
    return [(today + timedelta(days=d)).strftime("%Y-%m-%d") for d in _EXPIRY_OFFSETS_DAYS]


# ── Greeks approximation ───────────────────────────────────────────────────────

def _black_scholes_greeks(S: float, K: float, T: float, r: float, sigma: float, call_put: str) -> dict:
    """Approximate Black-Scholes price and Greeks.  T is time in years."""
    from math import log, sqrt, exp

    try:
        from scipy.stats import norm

        d1 = (log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt(T))
        d2 = d1 - sigma * sqrt(T)

        if call_put == "C":
            delta = norm.cdf(d1)
            price = S * norm.cdf(d1) - K * exp(-r * T) * norm.cdf(d2)
            theta_rhs = norm.cdf(d2)
        else:
            delta = norm.cdf(d1) - 1.0
            price = K * exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
            theta_rhs = -norm.cdf(-d2)

        gamma = norm.pdf(d1) / (S * sigma * sqrt(T))
        theta = (
            -(S * norm.pdf(d1) * sigma) / (2.0 * sqrt(T))
            - r * K * exp(-r * T) * theta_rhs
        ) / 365.0
        vega = S * norm.pdf(d1) * sqrt(T) / 100.0

        return {
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta": round(theta, 4),
            "vega":  round(vega, 4),
            "price": max(price, 0.01),
        }

    except ImportError:
        # Lightweight fallback without scipy
        from math import exp, sqrt
        moneyness = S / K
        if call_put == "C":
            delta = max(0.05, min(0.95, 0.5 + 0.4 * (moneyness - 1.0)))
        else:
            delta = max(-0.95, min(-0.05, -0.5 + 0.4 * (moneyness - 1.0)))
        gamma = max(0.001, 0.05 * exp(-10.0 * (moneyness - 1.0) ** 2))
        theta = -sigma * S * 0.002
        vega  = S * sqrt(T) * 0.01
        price = max(abs(S - K) * 0.05, 0.05)
        return {
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta": round(theta, 4),
            "vega":  round(vega, 4),
            "price": round(price, 2),
        }


# ── Public API ─────────────────────────────────────────────────────────────────

def screen_options(tickers: list, min_oi: int = 100, max_iv: float = 2.0) -> list:
    """
    Generate realistic mock options data for shipping stocks.

    Parameters
    ----------
    tickers : list of ticker strings; unknown tickers are ignored.
    min_oi  : minimum open interest filter.
    max_iv  : maximum implied volatility filter.

    Returns
    -------
    List of OptionsData (mix of calls and puts, various strikes and expiries).
    """
    rng = np.random.default_rng(seed=42)
    expiries = _generate_expiry_dates()
    today = datetime.today()
    results: list[OptionsData] = []

    valid_tickers = [t for t in tickers if t in _TICKER_PRICES]
    if not valid_tickers:
        valid_tickers = list(_TICKER_PRICES.keys())

    moneyness_grid = [0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20]
    moneyness_probs = [0.05, 0.08, 0.12, 0.18, 0.14, 0.18, 0.12, 0.08, 0.05]

    for ticker in valid_tickers:
        info = _TICKER_PRICES[ticker]
        S = info["price"] * float(1.0 + rng.normal(0.0, 0.02))
        vol_base = info["vol_base"]

        n_options = int(rng.integers(10, 21))  # 10–20 options per ticker

        for _ in range(n_options):
            expiry = expiries[int(rng.integers(0, len(expiries)))]
            T_days = (datetime.strptime(expiry, "%Y-%m-%d") - today).days
            T = max(T_days / 365.0, 0.01)

            m = float(rng.choice(moneyness_grid, p=moneyness_probs))
            raw_strike = S * m

            # Round to sensible option strike increments
            if S < 20.0:
                strike = round(raw_strike * 2.0) / 2.0
            elif S < 50.0:
                strike = float(round(raw_strike))
            else:
                strike = round(raw_strike / 2.5) * 2.5

            call_put = str(rng.choice(["C", "P"]))

            # Volatility smile: OTM options have higher IV
            otm = abs(m - 1.0)
            iv = float(np.clip(
                vol_base + otm * 0.40 + float(rng.normal(0.0, 0.05)),
                0.15,
                max_iv,
            ))

            greeks = _black_scholes_greeks(S, strike, T, 0.05, iv, call_put)

            mid = greeks["price"]
            spread_pct = float(rng.uniform(0.03, 0.12))
            bid = max(0.01, round(mid * (1.0 - spread_pct / 2.0), 2))
            ask = round(mid * (1.0 + spread_pct / 2.0), 2)

            # OI: higher for near-term ATM
            oi_base = int(rng.integers(50, 5001))
            if otm < 0.05:
                oi_base = int(oi_base * 2.5)
            if T_days < 45:
                oi_base = int(oi_base * 1.5)

            # Volume: normally a fraction of OI, occasionally a spike
            volume = int(rng.integers(0, max(1, int(oi_base * 0.3))))
            if float(rng.random()) < 0.08:
                volume = int(oi_base * float(rng.uniform(1.5, 4.0)))

            if oi_base < min_oi:
                continue

            results.append(OptionsData(
                ticker=ticker,
                expiry=expiry,
                strike=strike,
                call_put=call_put,
                bid=bid,
                ask=ask,
                iv=round(iv, 4),
                delta=greeks["delta"],
                gamma=greeks["gamma"],
                theta=greeks["theta"],
                vega=greeks["vega"],
                oi=oi_base,
                volume=volume,
                underlying_price=round(S, 2),
                moneyness=round(strike / S, 4),
            ))

    return results


def get_iv_surface(ticker: str) -> dict:
    """
    Build an IV surface for a single ticker.

    Returns
    -------
    dict with keys:
      - ticker   : str
      - spot     : float
      - strikes  : list[float]
      - expiries : list[str]   (e.g. "2W", "1M", …)
      - iv_grid  : list[list[float]]  shape [n_expiries][n_strikes]
    """
    rng = np.random.default_rng(seed=abs(hash(ticker)) % (2 ** 31))
    info = _TICKER_PRICES.get(ticker, {"price": 20.0, "vol_base": 0.60})
    S = info["price"]
    vol_base = info["vol_base"]

    moneyness_levels = [0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20]
    strikes = [round(S * m, 1) for m in moneyness_levels]
    expiry_labels = ["2W", "1M", "6W", "10W", "15W", "6M"]
    T_values = [14 / 365, 28 / 365, 42 / 365, 70 / 365, 105 / 365, 182 / 365]

    iv_grid: list[list[float]] = []
    for T in T_values:
        row: list[float] = []
        for m in moneyness_levels:
            otm = abs(m - 1.0)
            term_premium = 0.02 * float(np.sqrt(T * 365 / 30))
            smile = otm * 0.45
            iv = float(np.clip(
                vol_base + smile + term_premium + float(rng.normal(0.0, 0.02)),
                0.15,
                2.50,
            ))
            row.append(round(iv, 4))
        iv_grid.append(row)

    return {
        "ticker":  ticker,
        "spot":    round(S, 2),
        "strikes": strikes,
        "expiries": expiry_labels,
        "iv_grid": iv_grid,
    }


def get_unusual_activity(options: list) -> list:
    """
    Filter for options with high volume-to-OI ratio (unusual flow).

    Returns list of OptionsData sorted by vol/OI ratio descending,
    limited to options where volume >= 50% of open interest.
    """
    scored: list[tuple[float, OptionsData]] = []
    for opt in options:
        if opt.oi > 0 and opt.volume > 0:
            ratio = opt.volume / opt.oi
            if ratio >= 0.50:
                scored.append((ratio, opt))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [opt for _, opt in scored]


def calculate_max_pain(options: list, ticker: str) -> float:
    """
    Calculate the max pain strike for *ticker*.

    Max pain is defined as the strike that minimises the total intrinsic value
    paid out by option writers (i.e., where aggregate option buyer losses are
    maximised).

    Returns 0.0 when no options data is available for the ticker.
    """
    ticker_opts = [o for o in options if o.ticker == ticker]
    if not ticker_opts:
        return 0.0

    strikes = sorted({o.strike for o in ticker_opts})
    if not strikes:
        return 0.0

    min_pain = float("inf")
    max_pain_strike = strikes[0]

    for test_k in strikes:
        total = 0.0
        for opt in ticker_opts:
            if opt.call_put == "C":
                # ITM calls: strike < test_k
                intrinsic = max(0.0, test_k - opt.strike)
            else:
                # ITM puts: strike > test_k
                intrinsic = max(0.0, opt.strike - test_k)
            total += intrinsic * opt.oi

        if total < min_pain:
            min_pain = total
            max_pain_strike = test_k

    return float(max_pain_strike)
