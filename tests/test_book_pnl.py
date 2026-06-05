"""Mark-the-book-to-real-closes P&L and NAV (rec R113)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from processing.book_pnl import (
    BookMark, day_change_pct, mark_book, nav_series, returns_panel,
)


def _frame(prices, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(prices), freq="B")
    return pd.DataFrame({"close": prices}, index=idx)


def _canonical_frame(prices, symbol, start="2025-01-01"):
    """The normalised stock frame stock_feed actually caches: date as a COLUMN
    with a plain RangeIndex (data.normalizer.STOCK_COLS), NOT a DatetimeIndex."""
    dates = pd.date_range(start, periods=len(prices), freq="B")
    return pd.DataFrame({"date": dates, "symbol": symbol, "close": prices})


_STOCK = {
    "ZIM": _frame([10.0, 11.0, 12.0, 13.0, 14.0]),   # last close 14
    "SBLK": _frame([20.0, 19.0, 18.0, 17.0, 16.0]),  # last close 16
}

_BOOK = [
    {"ticker": "ZIM", "sector": "Container", "shares": 100, "avg_cost": 10.0},
    {"ticker": "SBLK", "sector": "Dry Bulk", "shares": 50, "avg_cost": 20.0},
]


# ── mark_book ───────────────────────────────────────────────────────────────

def test_mark_book_uses_real_last_close() -> None:
    bm = mark_book(_BOOK, _STOCK)
    assert isinstance(bm, BookMark)
    zim = next(p for p in bm.positions if p.ticker == "ZIM")
    assert zim.last_close == 14.0 and zim.priced
    assert zim.market_value == pytest.approx(100 * 14.0)
    assert zim.unrealized_pnl == pytest.approx(100 * (14.0 - 10.0))  # +400
    assert zim.unrealized_pnl_pct == pytest.approx(40.0)


def test_mark_book_totals_are_priced_only() -> None:
    bm = mark_book(_BOOK, _STOCK)
    # ZIM 100*14=1400, SBLK 50*16=800 -> 2200 mv; cost 1000+1000=2000
    assert bm.market_value == pytest.approx(2200.0)
    assert bm.cost_basis == pytest.approx(2000.0)
    assert bm.unrealized_pnl == pytest.approx(200.0)
    assert bm.n_priced == 2 and bm.n_unpriced == 0


def test_unpriced_position_is_flagged_not_fabricated() -> None:
    book = _BOOK + [{"ticker": "NOPRICE", "shares": 10, "avg_cost": 5.0}]
    bm = mark_book(book, _STOCK)
    np_pos = next(p for p in bm.positions if p.ticker == "NOPRICE")
    assert not np_pos.priced
    assert np_pos.last_close is None
    assert np_pos.market_value == 0.0
    assert np_pos.unrealized_pnl == 0.0   # NOT marked at a fabricated price
    assert bm.n_unpriced == 1
    # totals exclude the unpriced lot
    assert bm.market_value == pytest.approx(2200.0)


# ── day_change_pct ──────────────────────────────────────────────────────────

def test_day_change_is_real_last_two_closes() -> None:
    # ZIM 13 -> 14 = +7.69%
    assert day_change_pct("ZIM", _STOCK) == pytest.approx((14.0 - 13.0) / 13.0 * 100)


def test_day_change_none_when_unavailable() -> None:
    assert day_change_pct("MISSING", _STOCK) is None
    assert day_change_pct("ZIM", {"ZIM": _frame([10.0])}) is None  # < 2 closes


# ── nav_series ──────────────────────────────────────────────────────────────

def test_nav_series_marks_to_history_base_100() -> None:
    nav = nav_series(_BOOK, _STOCK, days=90, base=100.0)
    assert not nav.empty
    assert nav.iloc[0] == pytest.approx(100.0)        # base
    # day0 book = 100*10 + 50*20 = 2000; last = 100*14 + 50*16 = 2200 -> 110
    assert nav.iloc[-1] == pytest.approx(110.0)
    assert isinstance(nav.index, pd.DatetimeIndex)


def test_nav_series_empty_without_priced_holdings() -> None:
    assert nav_series([{"ticker": "MISSING", "shares": 10, "avg_cost": 1.0}], _STOCK).empty
    assert nav_series([], _STOCK).empty


def test_nav_series_handles_unaligned_dates() -> None:
    stock = {
        "ZIM": _frame([10.0, 11.0, 12.0], start="2026-01-01"),
        "SBLK": _frame([20.0, 21.0, 22.0], start="2026-01-02"),  # offset by a day
    }
    nav = nav_series(_BOOK, stock, days=90)
    assert not nav.empty
    assert nav.iloc[0] == pytest.approx(100.0)


# ── shared real-returns panel (R019/R021) ───────────────────────────────────

def test_returns_panel_built_from_real_closes() -> None:
    from processing.book_pnl import returns_panel
    idx = pd.date_range("2024-01-01", periods=200, freq="B")
    rng = np.random.default_rng(3)
    stock = {t: pd.DataFrame({"close": 100 + rng.normal(0, 1, 200).cumsum()}, index=idx)
             for t in ("ZIM", "SBLK", "DAC")}
    rp = returns_panel(stock, ["ZIM", "SBLK", "DAC", "XXX"])  # XXX has no data
    assert set(rp.columns) == {"ZIM", "SBLK", "DAC"}
    assert len(rp) > 100
    assert isinstance(rp.index, pd.DatetimeIndex)


def test_returns_panel_empty_on_insufficient_data() -> None:
    from processing.book_pnl import returns_panel
    assert returns_panel(None, ["ZIM"]).empty
    assert returns_panel({"ZIM": pd.DataFrame({"close": [1, 2, 3]})}, ["ZIM", "SBLK"]).empty


# ── canonical (date-as-column) frame shape — the real cache shape ────────────

def test_returns_panel_works_on_date_column_frame() -> None:
    # Regression: stock_feed caches frames with date as a COLUMN + RangeIndex.
    # returns_panel must build the time-indexed panel from these (it previously
    # returned empty -> the "real covariance/returns" path silently never
    # engaged in production, defeating R009/R021).
    import numpy as np
    rng = np.random.default_rng(0)
    px = lambda: list(100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 90))))
    stock = {"ZIM": _canonical_frame(px(), "ZIM"),
             "SBLK": _canonical_frame(px(), "SBLK")}
    panel = returns_panel(stock, ["ZIM", "SBLK"])
    assert not panel.empty
    assert set(panel.columns) == {"ZIM", "SBLK"}
    assert isinstance(panel.index, pd.DatetimeIndex)
    assert len(panel) > 60


def test_mark_book_prices_date_column_frame() -> None:
    stock = {"ZIM": _canonical_frame([10.0, 12.0, 14.0], "ZIM")}
    bm = mark_book([{"ticker": "ZIM", "sector": "Container",
                     "shares": 10, "avg_cost": 10.0}], stock)
    zim = next(p for p in bm.positions if p.ticker == "ZIM")
    assert zim.priced and zim.last_close == pytest.approx(14.0)
