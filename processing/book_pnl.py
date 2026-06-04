"""Mark the position book to real closing prices (rec R113).

Turns the durable position ledger (``state.positions``) into honest P&L:
per-lot market value + unrealized P&L from REAL ``stock_feed`` closes, a book
NAV, and a NAV time series. Positions without a real price are flagged
``unpriced`` and excluded from totals — never marked at a fabricated price.

``nav_series`` values TODAY's holdings at each past day's real close — a
*mark-to-history* curve, NOT a replayed trading path (entries/exits are not
reconstructed). It is labelled as such at the UI so it is not mistaken for a
realized track record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

_CLOSE_COLS = ("close", "Close", "adj_close", "price")


def _close_series(stock_data, ticker: str) -> Optional[pd.Series]:
    if not isinstance(stock_data, dict):
        return None
    frame = stock_data.get(ticker)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    col = next((c for c in _CLOSE_COLS if c in frame.columns), None)
    if col is None:
        return None
    s = pd.to_numeric(frame[col], errors="coerce").dropna()
    return s if not s.empty else None


def _latest_close(stock_data, ticker: str) -> Optional[float]:
    s = _close_series(stock_data, ticker)
    return float(s.iloc[-1]) if s is not None and not s.empty else None


@dataclass(frozen=True)
class MarkedPosition:
    ticker: str
    sector: Optional[str]
    shares: float
    avg_cost: float
    last_close: Optional[float]      # None when no real price is available
    priced: bool
    market_value: float              # 0.0 when unpriced
    cost_basis: float
    unrealized_pnl: float            # 0.0 when unpriced (not a fabricated loss)
    unrealized_pnl_pct: float


@dataclass(frozen=True)
class BookMark:
    positions: list                  # list[MarkedPosition]
    market_value: float              # priced market value only
    cost_basis: float                # priced cost basis only
    unrealized_pnl: float
    unrealized_pnl_pct: float
    n_priced: int
    n_unpriced: int


def mark_book(positions, stock_data) -> BookMark:
    """Mark each position to its latest real close. Unpriced lots are flagged
    and kept out of the book totals."""
    marked: list[MarkedPosition] = []
    tot_mv = tot_cb = 0.0
    n_priced = n_unpriced = 0
    for p in positions or []:
        ticker = str(p.get("ticker", ""))
        shares = float(p.get("shares", 0) or 0)
        avg_cost = float(p.get("avg_cost", 0) or 0)
        last = _latest_close(stock_data, ticker)
        priced = last is not None
        cost_basis = shares * avg_cost
        if priced:
            mv = shares * last
            upnl = mv - cost_basis
            upct = (upnl / cost_basis * 100) if cost_basis else 0.0
            tot_mv += mv
            tot_cb += cost_basis
            n_priced += 1
        else:
            mv = 0.0
            upnl = 0.0
            upct = 0.0
            n_unpriced += 1
        marked.append(MarkedPosition(
            ticker=ticker, sector=p.get("sector"), shares=shares, avg_cost=avg_cost,
            last_close=last, priced=priced, market_value=mv, cost_basis=cost_basis,
            unrealized_pnl=upnl, unrealized_pnl_pct=upct,
        ))
    book_upnl = tot_mv - tot_cb
    book_upct = (book_upnl / tot_cb * 100) if tot_cb else 0.0
    return BookMark(
        positions=marked, market_value=tot_mv, cost_basis=tot_cb,
        unrealized_pnl=book_upnl, unrealized_pnl_pct=book_upct,
        n_priced=n_priced, n_unpriced=n_unpriced,
    )


def day_change_pct(ticker: str, stock_data) -> Optional[float]:
    """Real 1-day % change from the last two closes. None when unavailable."""
    s = _close_series(stock_data, ticker)
    if s is None or len(s) < 2:
        return None
    prev = float(s.iloc[-2])
    last = float(s.iloc[-1])
    return ((last - prev) / prev * 100.0) if prev else None


def nav_series(positions, stock_data, *, days: int = 90, base: float = 100.0) -> pd.Series:
    """Current holdings marked against historical closes -> indexed NAV (base).

    A mark-to-history curve over the trailing ``days`` of common real closes.
    Empty Series when there are no priced holdings or no common history.
    """
    series_by_col: dict[int, pd.Series] = {}
    shares_by_col: dict[int, float] = {}
    i = 0
    for p in positions or []:
        sh = float(p.get("shares", 0) or 0)
        s = _close_series(stock_data, str(p.get("ticker", "")))
        if s is not None and not s.empty and sh and isinstance(s.index, pd.DatetimeIndex):
            series_by_col[i] = s
            shares_by_col[i] = sh
            i += 1
    if not series_by_col:
        return pd.Series(dtype=float)
    frame = pd.concat(series_by_col, axis=1).sort_index().ffill().dropna(how="any")
    if frame.empty:
        return pd.Series(dtype=float)
    shares_vec = pd.Series(shares_by_col)
    nav = frame.mul(shares_vec, axis=1).sum(axis=1).tail(days)
    if nav.empty or nav.iloc[0] == 0:
        return pd.Series(dtype=float)
    return nav / nav.iloc[0] * base
