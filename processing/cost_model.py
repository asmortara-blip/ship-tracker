"""Transaction-cost model for the realized signal track record and backtests.

The platform's track record (``state.signal_ledger``) and its walk-forward
backtests report GROSS signed returns — no trading frictions deducted. A modeled
edge that looks profitable gross can be unprofitable once you pay the bid-ask
spread, commission, and market impact on entry AND exit. This module supplies a
single, conservative, *assumed* round-trip cost so the track record can be shown
net-of-cost alongside gross.

HONESTY: these costs are ASSUMED from typical execution of US-listed mid-cap
shipping equities — they are NOT measured from real fills. They are deliberately
conservative (erring high) so that a "survives costs" verdict is robust. Treat
the net figures as a stress test, not a realized P&L. See ``DISCLAIMER``.

A signal in the ledger is one round trip (enter at ``issue_close``, exit at the
mark), so the full round-trip cost is deducted once per signal — regardless of
direction, since the spread/commission/impact are paid whether long or short.

SHORTS additionally pay a stock-borrow / financing fee that accrues per day held
(longs pay none). The borrow rate is ASSUMED per liquidity tier (thin names cost
more) and applied over the actual days-held; without it the net edge on a short
book would be overstated and "conservative" would be a false claim for shorts.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostAssumption:
    """Per-side execution cost components, in basis points (1 bp = 0.01%)."""

    half_spread_bps: float   # half the quoted bid-ask spread
    commission_bps: float    # broker commission / exchange fees
    impact_bps: float        # market-impact / slippage at a modest clip

    def per_side_bps(self) -> float:
        return self.half_spread_bps + self.commission_bps + self.impact_bps

    def round_trip_bps(self) -> float:
        # Entry + exit: the per-side cost is paid twice.
        return 2.0 * self.per_side_bps()


# Conservative default for a liquid US-listed mid-cap shipping name:
# ~8 bp half-spread + 2 bp commission + 10 bp impact = 20 bp/side = 40 bp round trip.
DEFAULT_COST = CostAssumption(half_spread_bps=8.0, commission_bps=2.0, impact_bps=10.0)

# Per-ticker overrides by ASSUMED liquidity tier (not measured). Tighter names
# (ZIM/MATX) cost less; thinner names (CMRE/GSL) cost more. Anything not listed
# falls back to DEFAULT_COST.
_TICKER_COST: dict[str, CostAssumption] = {
    # liquid / tight
    "ZIM":  CostAssumption(6.0, 2.0, 8.0),    # 32 bp round trip
    "MATX": CostAssumption(6.0, 2.0, 8.0),    # 32 bp
    "STNG": CostAssumption(8.0, 2.0, 10.0),   # 40 bp
    # mid
    "SBLK": CostAssumption(8.0, 2.0, 10.0),   # 40 bp
    "DAC":  CostAssumption(10.0, 2.0, 12.0),  # 48 bp
    "GOGL": CostAssumption(10.0, 2.0, 12.0),  # 48 bp
    # thinner / wider spread
    "CMRE": CostAssumption(12.0, 2.0, 16.0),  # 60 bp
    "GSL":  CostAssumption(14.0, 2.0, 18.0),  # 68 bp
}

# Assumed annualized stock-borrow / financing rate for SHORT positions, in
# bps/year (longs pay none). Thin / hard-to-borrow names cost more. Assumed, not
# measured. Anything not listed uses _BORROW_BPS_PER_YEAR_DEFAULT.
_BORROW_BPS_PER_YEAR_DEFAULT = 75.0
_BORROW_BPS_PER_YEAR: dict[str, float] = {
    "ZIM": 60.0, "MATX": 50.0, "STNG": 75.0,
    "SBLK": 75.0, "DAC": 100.0, "GOGL": 100.0,
    "CMRE": 150.0, "GSL": 200.0,
}

DISCLAIMER = (
    "Net-of-cost figures apply an ASSUMED round-trip trading cost (bid-ask "
    "half-spread + commission + market impact); shorts also accrue an assumed "
    "stock-borrow fee per day held. These are not costs measured from real "
    "fills — a conservative stress test, not realized P&L."
)


def cost_assumption(ticker: str) -> CostAssumption:
    """The cost assumption used for *ticker* (its tier override, or the default)."""
    return _TICKER_COST.get((ticker or "").upper(), DEFAULT_COST)


def short_borrow_bps_per_year(ticker: str) -> float:
    """Assumed annualized short-borrow rate for *ticker*, in bps/year."""
    return _BORROW_BPS_PER_YEAR.get((ticker or "").upper(), _BORROW_BPS_PER_YEAR_DEFAULT)


def short_borrow_cost_pct(ticker: str, days_held: float) -> float:
    """Accrued short-borrow cost in PERCENT over *days_held* (0 for non-positive
    days). Longs never call this — borrow is a short-only friction."""
    days = max(0.0, float(days_held))
    return short_borrow_bps_per_year(ticker) / 100.0 * (days / 365.0)


def per_side_cost_bps(ticker: str | None = None) -> float:
    """Assumed one-side execution cost (half-spread + commission + impact), bps."""
    return cost_assumption(ticker or "").per_side_bps()


def turnover_cost_frac(
    turnover: float,
    *,
    per_side_bps: float | None = None,
    ticker: str | None = None,
) -> float:
    """Trading cost as a RETURN FRACTION for trading ``turnover`` units of
    one-sided notional.

    ``turnover`` is the sum of absolute weight/position changes at a rebalance
    (Σ|Δw|); each unit is one side of a trade, charged at the per-side cost. This
    is the backtest-facing form (the walk-forward PnL lives in return-fraction
    space, unlike the ledger's percent-space :func:`net_of_cost_pct`). When
    ``per_side_bps`` is omitted it falls back to *ticker*'s tier (or the default).
    """
    if per_side_bps is None:
        per_side_bps = per_side_cost_bps(ticker)
    return max(0.0, float(turnover)) * float(per_side_bps) / 1e4


def round_trip_cost_bps(ticker: str) -> float:
    """Assumed round-trip (entry + exit) trading cost for *ticker*, in bps."""
    return cost_assumption(ticker).round_trip_bps()


def round_trip_cost_pct(ticker: str) -> float:
    """Assumed round-trip trading cost for *ticker*, in PERCENT (bps / 100)."""
    return round_trip_cost_bps(ticker) / 100.0


def net_of_cost_pct(
    gross_return_pct: float,
    ticker: str,
    *,
    is_short: bool = False,
    days_held: float = 0.0,
) -> float:
    """Deduct trading cost from a gross % return (a single entry+exit).

    Works for signed returns: a signal is one round trip and the spread/
    commission/impact are paid whether long or short, so the full round-trip
    cost is always subtracted. SHORTS additionally pay an assumed stock-borrow
    fee accrued over ``days_held`` (longs pay no borrow).
    """
    cost = round_trip_cost_pct(ticker)
    if is_short:
        cost += short_borrow_cost_pct(ticker, days_held)
    return float(gross_return_pct) - cost
