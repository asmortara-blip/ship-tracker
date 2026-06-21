"""Tests for processing/order_blotter.py (R108).

build_blotter turns idea→target deltas into proposed Orders:
  * Bullish under-weight → BUY toward target
  * Bearish → SELL/short tilt
  * Neutral → no order
  * explicit target_weights override the idea mapping
  * quantity is filled only when a real price exists; never raises on garbage.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from processing.order_blotter import Order, build_blotter


@dataclass
class _Idea:
    ticker: str
    direction: str
    conviction_score: float


def _px(close: float, n: int = 5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n),
            "close": [close] * n,
            "volume": [10_000.0] * n,
        }
    )


def test_bullish_underweight_becomes_buy():
    sd = {"ZIM": _px(100.0)}
    ideas = [_Idea("ZIM", "Bullish", 0.8)]
    orders = build_blotter(ideas, [], nav=1_000_000.0, stock_data=sd, max_weight=0.10)
    assert len(orders) == 1
    o = orders[0]
    assert o.ticker == "ZIM"
    assert o.side == "BUY"
    # target = 0.8 * 0.10 = 0.08; delta from 0 = 0.08; notional = 0.08 * 1e6
    assert o.notional == 80_000.0
    # quantity = notional / price = 80000 / 100
    assert o.quantity == 800.0


def test_bearish_becomes_sell():
    sd = {"SBLK": _px(50.0)}
    ideas = [_Idea("SBLK", "Bearish", 0.6)]
    orders = build_blotter(ideas, [], nav=1_000_000.0, stock_data=sd, max_weight=0.10)
    assert len(orders) == 1
    o = orders[0]
    assert o.ticker == "SBLK"
    assert o.side == "SELL"
    # target = -0.06; current 0 → delta -0.06 → notional 60000
    assert o.notional == 60_000.0


def test_neutral_produces_no_order():
    sd = {"DAC": _px(70.0)}
    ideas = [_Idea("DAC", "Neutral", 0.9)]
    orders = build_blotter(ideas, [], nav=1_000_000.0, stock_data=sd)
    assert orders == []


def test_quantity_zero_when_price_missing():
    # No stock_data for the name → notional set, quantity stays 0.0.
    ideas = [_Idea("GOGL", "Bullish", 0.5)]
    orders = build_blotter(ideas, [], nav=1_000_000.0, stock_data={})
    assert len(orders) == 1
    assert orders[0].side == "BUY"
    assert orders[0].notional == 50_000.0
    assert orders[0].quantity == 0.0


def test_explicit_target_weight_overrides_idea():
    sd = {"ZIM": _px(100.0)}
    # Idea says Neutral (→0 target) but caller forces +0.05 target.
    ideas = [_Idea("ZIM", "Neutral", 0.9)]
    orders = build_blotter(
        ideas, [], target_weights={"ZIM": 0.05}, nav=1_000_000.0, stock_data=sd
    )
    assert len(orders) == 1
    assert orders[0].side == "BUY"
    assert orders[0].notional == 50_000.0


def test_no_trade_band_suppresses_tiny_delta():
    sd = {"ZIM": _px(100.0)}
    # Tiny target below the no-trade band → no order.
    ideas = [_Idea("ZIM", "Bullish", 0.01)]
    orders = build_blotter(
        ideas, [], nav=1_000_000.0, stock_data=sd, max_weight=0.10, no_trade_band=0.005
    )
    # 0.01 * 0.10 = 0.001 < 0.005 band → suppressed.
    assert orders == []


def test_held_name_without_idea_is_left_alone():
    sd = {"ZIM": _px(100.0)}
    book = [{"ticker": "ZIM", "shares": 100, "avg_cost": 90}]
    # No idea, no override → no order (no view).
    orders = build_blotter([], book, nav=1_000_000.0, stock_data=sd)
    assert orders == []


def test_orders_sorted_by_descending_notional():
    sd = {"ZIM": _px(100.0), "SBLK": _px(50.0)}
    ideas = [_Idea("ZIM", "Bullish", 0.3), _Idea("SBLK", "Bullish", 0.9)]
    orders = build_blotter(ideas, [], nav=1_000_000.0, stock_data=sd)
    assert [o.ticker for o in orders] == ["SBLK", "ZIM"]
    assert orders[0].notional >= orders[1].notional


def test_returns_frozen_orders():
    sd = {"ZIM": _px(100.0)}
    orders = build_blotter([_Idea("ZIM", "Bullish", 0.5)], [], stock_data=sd)
    import dataclasses

    assert dataclasses.is_dataclass(Order)
    try:
        object.__setattr__  # frozen → setattr raises
        orders[0].ticker = "X"  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised


def test_never_raises_on_garbage_input():
    # None ideas, None book, weird overrides, no nav kwarg quirks.
    assert build_blotter(None, None) == []
    assert build_blotter([object()], None) == []  # idea with no attrs → skipped
    # Garbage idea attrs should not crash.
    bad = [_Idea("ZIM", None, None)]  # type: ignore[arg-type]
    out = build_blotter(bad, [], stock_data={})
    assert isinstance(out, list)


def test_deterministic():
    sd = {"ZIM": _px(100.0), "SBLK": _px(50.0)}
    ideas = [_Idea("ZIM", "Bullish", 0.4), _Idea("SBLK", "Bearish", 0.7)]
    a = build_blotter(ideas, [], nav=1_000_000.0, stock_data=sd)
    b = build_blotter(ideas, [], nav=1_000_000.0, stock_data=sd)
    assert a == b
