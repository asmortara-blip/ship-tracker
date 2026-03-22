"""Personal portfolio position tracker for shipping stocks.

Manages positions (load/save to JSON), computes live P&L snapshots from
stock_data, enriches each position with AlphaSignal data, and produces a
PortfolioSummary with rebalancing suggestions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POSITIONS_FILE = Path("cache/portfolio/positions.json")

_DEFAULT_TICKERS = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]

# Approximate sector betas vs. a shipping sector index (rough estimates)
_SECTOR_BETAS: dict[str, float] = {
    "ZIM":  1.45,
    "MATX": 0.85,
    "SBLK": 1.20,
    "DAC":  1.10,
    "CMRE": 0.95,
}
_DEFAULT_BETA = 1.0


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Position:
    ticker: str
    shares: float
    avg_cost: float       # average cost basis per share
    entry_date: str = ""  # ISO date, optional
    notes: str = ""       # free text


@dataclass
class PositionSnapshot:
    ticker: str
    shares: float
    avg_cost: float
    current_price: float
    market_value: float
    cost_basis: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    day_pnl: float
    day_pnl_pct: float
    weight_in_portfolio: float    # % of total portfolio market value
    signals: list = field(default_factory=list)   # AlphaSignal objects for this ticker
    bdi_correlation: float = 0.0  # correlation with BDI


@dataclass
class PortfolioSummary:
    positions: list[PositionSnapshot]
    total_value: float
    total_cost: float
    total_pnl: float
    total_pnl_pct: float
    day_pnl: float
    best_performer: str       # ticker
    worst_performer: str
    portfolio_beta: float     # weighted avg beta vs shipping sector
    concentration_risk: str   # "HIGH" if any position > 40%
    suggested_rebalance: list[dict] = field(default_factory=list)
    # [{ticker, current_weight, suggested_weight, action}]


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def save_positions(positions: list[Position]) -> None:
    """Persist positions list to POSITIONS_FILE as JSON."""
    try:
        POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(p) for p in positions]
        POSITIONS_FILE.write_text(json.dumps(data, indent=2))
        logger.debug(f"Saved {len(positions)} positions to {POSITIONS_FILE}")
    except Exception as exc:
        logger.error(f"Failed to save positions: {exc}")


def load_positions() -> list[Position]:
    """Load positions from POSITIONS_FILE. Returns empty list if missing or corrupt."""
    try:
        if not POSITIONS_FILE.exists():
            return []
        raw = json.loads(POSITIONS_FILE.read_text())
        positions = []
        for item in raw:
            positions.append(Position(
                ticker=str(item.get("ticker", "")).upper().strip(),
                shares=float(item.get("shares", 0.0)),
                avg_cost=float(item.get("avg_cost", 0.0)),
                entry_date=str(item.get("entry_date", "")),
                notes=str(item.get("notes", "")),
            ))
        logger.debug(f"Loaded {len(positions)} positions from {POSITIONS_FILE}")
        return positions
    except Exception as exc:
        logger.error(f"Failed to load positions: {exc}")
        return []


def add_position(
    ticker: str,
    shares: float,
    avg_cost: float,
    entry_date: str = "",
    notes: str = "",
) -> None:
    """Add or update a position (merges with existing ticker if present)."""
    ticker = ticker.upper().strip()
    positions = load_positions()
    existing = next((p for p in positions if p.ticker == ticker), None)
    if existing is not None:
        # Weighted average cost for existing + new shares
        total_shares = existing.shares + shares
        if total_shares > 0:
            existing.avg_cost = (
                (existing.shares * existing.avg_cost + shares * avg_cost) / total_shares
            )
            existing.shares = total_shares
        if notes:
            existing.notes = notes
        if entry_date:
            existing.entry_date = entry_date
    else:
        positions.append(Position(
            ticker=ticker,
            shares=shares,
            avg_cost=avg_cost,
            entry_date=entry_date,
            notes=notes,
        ))
    save_positions(positions)


def remove_position(ticker: str) -> None:
    """Remove a position by ticker."""
    ticker = ticker.upper().strip()
    positions = [p for p in load_positions() if p.ticker != ticker]
    save_positions(positions)


def update_position(
    ticker: str,
    shares: Optional[float] = None,
    avg_cost: Optional[float] = None,
) -> None:
    """Update shares and/or avg_cost for an existing position."""
    ticker = ticker.upper().strip()
    positions = load_positions()
    for p in positions:
        if p.ticker == ticker:
            if shares is not None:
                p.shares = shares
            if avg_cost is not None:
                p.avg_cost = avg_cost
            break
    save_positions(positions)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _latest_close(stock_data: dict, ticker: str) -> Optional[float]:
    df = stock_data.get(ticker)
    if df is None or df.empty:
        return None
    if "close" not in df.columns:
        return None
    vals = df["close"].dropna()
    return float(vals.iloc[-1]) if not vals.empty else None


def _prev_close(stock_data: dict, ticker: str) -> Optional[float]:
    df = stock_data.get(ticker)
    if df is None or df.empty:
        return None
    if "close" not in df.columns:
        return None
    vals = df["close"].dropna()
    return float(vals.iloc[-2]) if len(vals) >= 2 else None


def _bdi_correlation(stock_data: dict, macro_data: dict, ticker: str) -> float:
    """Compute rolling correlation between ticker close and BDI (BSXRLM series)."""
    try:
        import pandas as pd
        ticker_df = stock_data.get(ticker)
        bdi_df = macro_data.get("BSXRLM")
        if ticker_df is None or bdi_df is None:
            return 0.0
        if ticker_df.empty or bdi_df.empty:
            return 0.0
        if "close" not in ticker_df.columns or "value" not in bdi_df.columns:
            return 0.0

        # Align on date
        t_series = ticker_df.set_index("date")["close"] if "date" in ticker_df.columns else ticker_df["close"]
        b_series = bdi_df.set_index("date")["value"] if "date" in bdi_df.columns else bdi_df["value"]

        aligned = pd.DataFrame({"stock": t_series, "bdi": b_series}).dropna()
        if len(aligned) < 10:
            return 0.0
        corr = aligned["stock"].corr(aligned["bdi"])
        return round(float(corr), 3) if not np.isnan(corr) else 0.0
    except Exception as exc:
        logger.debug(f"BDI correlation error for {ticker}: {exc}")
        return 0.0


def _signals_for_ticker(signals: list, ticker: str) -> list:
    """Filter signal list to those matching ticker."""
    try:
        return [s for s in signals if getattr(s, "ticker", None) == ticker]
    except Exception:
        return []


def _dominant_signal_direction(ticker_signals: list) -> str:
    """Return 'LONG', 'SHORT', or 'NEUTRAL' based on plurality of signal directions."""
    if not ticker_signals:
        return "NEUTRAL"
    counts: dict[str, float] = {"LONG": 0.0, "SHORT": 0.0, "NEUTRAL": 0.0}
    for s in ticker_signals:
        direction = getattr(s, "direction", "NEUTRAL")
        strength = getattr(s, "strength", 0.5)
        if direction in counts:
            counts[direction] += strength
    return max(counts, key=lambda k: counts[k])


def _build_rebalance_suggestions(
    snapshots: list[PositionSnapshot],
    signals: list,
    total_value: float,
) -> list[dict]:
    """Build rebalancing suggestions based on signal direction vs. current weight."""
    suggestions = []
    n = len(snapshots)
    if n == 0 or total_value <= 0:
        return suggestions

    equal_weight = 1.0 / n  # equal weight target as baseline

    for snap in snapshots:
        ticker_signals = _signals_for_ticker(signals, snap.ticker)
        direction = _dominant_signal_direction(ticker_signals)
        current_w = snap.weight_in_portfolio / 100.0  # convert % to fraction

        # Determine suggested weight
        if direction == "LONG":
            # Signal bullish: suggest increasing toward 25% if under 20%
            suggested_w = max(equal_weight, 0.25) if current_w < 0.20 else current_w
        elif direction == "SHORT":
            # Signal bearish: suggest reducing toward 5%
            suggested_w = min(current_w, 0.05)
        else:
            suggested_w = equal_weight

        # Clamp suggested weight
        suggested_w = max(0.02, min(0.50, suggested_w))

        delta = suggested_w - current_w
        if abs(delta) < 0.02:
            action = "HOLD"
        elif delta > 0:
            action = "INCREASE"
        else:
            action = "REDUCE"

        suggestions.append({
            "ticker": snap.ticker,
            "current_weight": round(current_w * 100, 1),
            "suggested_weight": round(suggested_w * 100, 1),
            "action": action,
            "signal_direction": direction,
        })

    return suggestions


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_portfolio_snapshot(
    positions: list[Position],
    stock_data: dict,
    macro_data: dict,
    signals: list,
) -> PortfolioSummary:
    """Compute current P&L and analytics for all positions.

    Args:
        positions:   List of Position dataclasses held by the user.
        stock_data:  Dict of ticker -> DataFrame (from stock_feed.fetch_all_stocks).
        macro_data:  Dict of series_id -> DataFrame (from fred_feed.fetch_macro_series).
        signals:     List of AlphaSignal objects (from alpha_engine.generate_all_signals).

    Returns:
        A fully populated PortfolioSummary.
    """
    if not positions:
        return PortfolioSummary(
            positions=[],
            total_value=0.0,
            total_cost=0.0,
            total_pnl=0.0,
            total_pnl_pct=0.0,
            day_pnl=0.0,
            best_performer="—",
            worst_performer="—",
            portfolio_beta=1.0,
            concentration_risk="LOW",
            suggested_rebalance=[],
        )

    # --- First pass: compute per-position values ---
    snapshots: list[PositionSnapshot] = []
    total_value = 0.0
    total_cost = 0.0
    total_day_pnl = 0.0

    for pos in positions:
        current_price = _latest_close(stock_data, pos.ticker)
        prev_price = _prev_close(stock_data, pos.ticker)

        # Fall back gracefully if no market data
        if current_price is None:
            current_price = pos.avg_cost  # price = cost basis (flat P&L)
        if prev_price is None:
            prev_price = current_price

        market_value = pos.shares * current_price
        cost_basis = pos.shares * pos.avg_cost
        unrealized_pnl = market_value - cost_basis
        unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis != 0 else 0.0
        day_pnl = pos.shares * (current_price - prev_price)
        day_pnl_pct = ((current_price - prev_price) / prev_price * 100) if prev_price != 0 else 0.0

        total_value += market_value
        total_cost += cost_basis
        total_day_pnl += day_pnl

        bdi_corr = _bdi_correlation(stock_data, macro_data, pos.ticker)
        ticker_signals = _signals_for_ticker(signals, pos.ticker)

        snapshots.append(PositionSnapshot(
            ticker=pos.ticker,
            shares=pos.shares,
            avg_cost=pos.avg_cost,
            current_price=current_price,
            market_value=market_value,
            cost_basis=cost_basis,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
            day_pnl=day_pnl,
            day_pnl_pct=day_pnl_pct,
            weight_in_portfolio=0.0,  # filled in second pass
            signals=ticker_signals,
            bdi_correlation=bdi_corr,
        ))

    # --- Second pass: compute weights ---
    for snap in snapshots:
        snap.weight_in_portfolio = (
            (snap.market_value / total_value * 100) if total_value > 0 else 0.0
        )

    # --- Summary metrics ---
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost != 0 else 0.0

    # Best / worst performer by unrealized P&L %
    sorted_by_pnl = sorted(snapshots, key=lambda s: s.unrealized_pnl_pct)
    worst_performer = sorted_by_pnl[0].ticker if sorted_by_pnl else "—"
    best_performer = sorted_by_pnl[-1].ticker if sorted_by_pnl else "—"

    # Weighted portfolio beta
    portfolio_beta = 0.0
    for snap in snapshots:
        beta = _SECTOR_BETAS.get(snap.ticker, _DEFAULT_BETA)
        weight = snap.weight_in_portfolio / 100.0
        portfolio_beta += beta * weight

    # Concentration risk
    max_weight = max((s.weight_in_portfolio for s in snapshots), default=0.0)
    concentration_risk = "HIGH" if max_weight > 40.0 else ("MODERATE" if max_weight > 25.0 else "LOW")

    # Rebalancing suggestions
    rebalance = _build_rebalance_suggestions(snapshots, signals, total_value)

    return PortfolioSummary(
        positions=snapshots,
        total_value=round(total_value, 2),
        total_cost=round(total_cost, 2),
        total_pnl=round(total_pnl, 2),
        total_pnl_pct=round(total_pnl_pct, 2),
        day_pnl=round(total_day_pnl, 2),
        best_performer=best_performer,
        worst_performer=worst_performer,
        portfolio_beta=round(portfolio_beta, 2),
        concentration_risk=concentration_risk,
        suggested_rebalance=rebalance,
    )
