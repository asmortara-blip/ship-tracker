"""Tests for processing.commodity_shipping — ETF-to-shipping signal mapper.

Covers:
  - CommodityShippingSignal dataclass shape
  - COMMODITY_SHIPPING_MAP catalog: every entry has the keys analyze_commodity_signals
    reads (name, hypothesis, bullish_routes, bullish_stocks, bearish_stocks,
    signal_direction_if_rising)
  - analyze_commodity_signals:
      * empty stock_data → empty list
      * missing close column / empty df / < 2 obs → ticker skipped
      * 'bullish' direction-if-rising ticker (DBA): price up > 3% → direction='bullish'
      * 'bearish' direction-if-rising ticker (USO): price up > 3% → direction='bearish'
      * |change| < 3% → direction='neutral'
      * signal_strength = min(1.0, |chg| / 0.15) — clamps at 1.0
  - get_commodity_consensus:
      * empty signals → NEUTRAL + None strongest
      * majority rule for BULLISH / BEARISH / NEUTRAL
      * strongest_signal is the entry with max signal_strength
"""
from __future__ import annotations

import pandas as pd
import pytest

from processing.commodity_shipping import (
    COMMODITY_SHIPPING_MAP,
    CommodityShippingSignal,
    analyze_commodity_signals,
    get_commodity_consensus,
)


def _price_df(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(prices), freq="B"),
        "close": prices,
    })


# ─── Dataclass + catalog shape ──────────────────────────────────────────────

def test_commodity_shipping_signal_shape() -> None:
    s = CommodityShippingSignal(
        commodity_ticker="DBA", commodity_name="Agriculture",
        current_price=20.0, price_change_30d=0.05,
        direction="bullish", shipping_hypothesis="h",
        affected_routes=["r1"], affected_stocks=["SBLK"],
        signal_strength=0.4, trade_idea="t",
    )
    assert s.commodity_ticker == "DBA"
    assert s.direction == "bullish"


def test_commodity_shipping_map_entries_have_required_keys() -> None:
    """All entries must carry the keys analyze_commodity_signals reads."""
    required = {"name", "hypothesis", "bullish_routes", "bullish_stocks",
                "bearish_stocks", "signal_direction_if_rising"}
    for ticker, meta in COMMODITY_SHIPPING_MAP.items():
        assert required <= set(meta.keys()), f"{ticker} missing keys"
        assert meta["signal_direction_if_rising"] in {"bullish", "bearish"}


# ─── analyze_commodity_signals ─────────────────────────────────────────────

def test_analyze_commodity_signals_empty_input_returns_empty() -> None:
    assert analyze_commodity_signals({}) == []


def test_analyze_commodity_signals_skips_missing_columns() -> None:
    """Frame without 'close' column → ticker skipped."""
    out = analyze_commodity_signals({"DBA": pd.DataFrame({"date": [pd.Timestamp.now()]})})
    assert out == []


def test_analyze_commodity_signals_skips_too_short_frames() -> None:
    out = analyze_commodity_signals({"DBA": _price_df([20.0])})
    assert out == []


def test_analyze_commodity_signals_skips_empty_frames() -> None:
    assert analyze_commodity_signals({"DBA": pd.DataFrame()}) == []


def test_analyze_commodity_signals_bullish_when_bullish_etf_rising() -> None:
    """DBA is 'bullish if rising'; +10% over 30d → direction='bullish'."""
    prices = [100.0] + [100.0] * 29 + [110.0]
    out = analyze_commodity_signals({"DBA": _price_df(prices)})
    dba = next(s for s in out if s.commodity_ticker == "DBA")
    assert dba.direction == "bullish"
    assert dba.price_change_30d > 0.03
    assert dba.signal_strength > 0


def test_analyze_commodity_signals_bearish_when_bullish_etf_falling() -> None:
    """DBA is 'bullish if rising'; -10% over 30d → direction='bearish'."""
    prices = [100.0] + [100.0] * 29 + [90.0]
    out = analyze_commodity_signals({"DBA": _price_df(prices)})
    dba = next(s for s in out if s.commodity_ticker == "DBA")
    assert dba.direction == "bearish"


def test_analyze_commodity_signals_bearish_when_bearish_etf_rising() -> None:
    """USO is 'bearish if rising' (oil hits shipping margins);
    +10% → direction='bearish' for shipping."""
    prices = [100.0] + [100.0] * 29 + [110.0]
    out = analyze_commodity_signals({"USO": _price_df(prices)})
    uso = next(s for s in out if s.commodity_ticker == "USO")
    assert uso.direction == "bearish"


def test_analyze_commodity_signals_bullish_when_bearish_etf_falling() -> None:
    """USO -10% → cheap fuel → shipping bullish."""
    prices = [100.0] + [100.0] * 29 + [90.0]
    out = analyze_commodity_signals({"USO": _price_df(prices)})
    uso = next(s for s in out if s.commodity_ticker == "USO")
    assert uso.direction == "bullish"


def test_analyze_commodity_signals_neutral_when_change_under_3pct() -> None:
    """|change| < 3% → neutral regardless of direction-mapping."""
    prices = [100.0] + [100.0] * 29 + [101.0]  # +1%
    out = analyze_commodity_signals({"DBA": _price_df(prices)})
    dba = next(s for s in out if s.commodity_ticker == "DBA")
    assert dba.direction == "neutral"


def test_analyze_commodity_signals_signal_strength_in_unit_interval() -> None:
    """signal_strength = min(1.0, |chg|/0.15) — clamps at 1.0 for big moves."""
    big_move = [100.0] + [100.0] * 29 + [200.0]   # +100%
    out = analyze_commodity_signals({"DBA": _price_df(big_move)})
    dba = next(s for s in out if s.commodity_ticker == "DBA")
    assert dba.signal_strength == pytest.approx(1.0)


def test_analyze_commodity_signals_carries_affected_routes_and_stocks() -> None:
    prices = [100.0] + [100.0] * 29 + [110.0]
    out = analyze_commodity_signals({"DBA": _price_df(prices)})
    dba = next(s for s in out if s.commodity_ticker == "DBA")
    assert dba.affected_routes == COMMODITY_SHIPPING_MAP["DBA"]["bullish_routes"]
    # affected_stocks is bullish + bearish concatenated
    expected = (COMMODITY_SHIPPING_MAP["DBA"]["bullish_stocks"]
                + COMMODITY_SHIPPING_MAP["DBA"]["bearish_stocks"])
    assert dba.affected_stocks == expected


# ─── get_commodity_consensus ────────────────────────────────────────────────

def _signal(ticker: str, direction: str, strength: float = 0.5) -> CommodityShippingSignal:
    return CommodityShippingSignal(
        commodity_ticker=ticker, commodity_name=ticker,
        current_price=100.0, price_change_30d=0.05,
        direction=direction, shipping_hypothesis="",
        affected_routes=[], affected_stocks=[],
        signal_strength=strength, trade_idea="",
    )


def test_get_commodity_consensus_empty_signals_returns_neutral() -> None:
    out = get_commodity_consensus([])
    assert out["net_signal"] == "NEUTRAL"
    assert out["strongest_signal"] is None
    assert out["bullish_count"] == 0


def test_get_commodity_consensus_majority_bullish() -> None:
    sigs = [_signal("A", "bullish"), _signal("B", "bullish"),
            _signal("C", "bearish")]
    out = get_commodity_consensus(sigs)
    assert out["net_signal"] == "BULLISH"
    assert out["bullish_count"] == 2
    assert out["bearish_count"] == 1


def test_get_commodity_consensus_majority_bearish() -> None:
    sigs = [_signal("A", "bearish"), _signal("B", "bearish"),
            _signal("C", "bullish")]
    out = get_commodity_consensus(sigs)
    assert out["net_signal"] == "BEARISH"


def test_get_commodity_consensus_tie_is_neutral() -> None:
    sigs = [_signal("A", "bullish"), _signal("B", "bearish")]
    out = get_commodity_consensus(sigs)
    assert out["net_signal"] == "NEUTRAL"


def test_get_commodity_consensus_strongest_is_max_signal_strength() -> None:
    sigs = [_signal("A", "bullish", 0.3),
            _signal("B", "bullish", 0.9),
            _signal("C", "bearish", 0.5)]
    out = get_commodity_consensus(sigs)
    assert out["strongest_signal"] is not None
    assert out["strongest_signal"].commodity_ticker == "B"


def test_get_commodity_consensus_counts_neutral() -> None:
    sigs = [_signal("A", "neutral"), _signal("B", "neutral"),
            _signal("C", "bullish")]
    out = get_commodity_consensus(sigs)
    assert out["neutral_count"] == 2
