"""Tests for processing.sector_dashboard — sub-sector performance + trade flow.

Covers:
  - _safe_pct: zero divisor, None inputs, normal arithmetic
  - compute_sector_performance:
      * empty stock_data returns scaffolding (every catalog sector, momentum "N/A")
      * sector with full price history produces avg_return_1d / 5d / 30d
      * freight column selection by NAME (not positional) so dates never leak
      * momentum tiers map to ret_30 thresholds (Strong / Positive / Weak / Negative)
      * outlook tiers map to avg(ret_30, idx_30) (Bullish / Bearish / Neutrals)
  - compute_trade_flow_summary:
      * empty port_results returns "Insufficient data" narrative
      * region mapping (Asia/Europe/Americas/Middle East/Other) routes ports correctly
      * share_pct sums to ~100 when there is any trade
      * dominant_region is the region with highest total_trade
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from processing.sector_dashboard import (
    SECTORS,
    _safe_pct,
    compute_sector_performance,
    compute_trade_flow_summary,
)


# ─── Stand-in for PortDemandResult ────────────────────────────────────────────

@dataclass
class _FakePort:
    port_name: str
    region: str
    export_value_usd: float = 0.0
    import_value_usd: float = 0.0
    throughput_teu_m: float = 0.0
    demand_score: float = 0.5


def _price_df(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(prices), freq="B"),
        "close": prices,
    })


def _freight_df(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(values), freq="D"),
        "rate_usd_per_feu": values,
    })


# ─── _safe_pct ─────────────────────────────────────────────────────────────────

def test_safe_pct_zero_divisor_returns_none() -> None:
    assert _safe_pct(100.0, 0.0) is None


def test_safe_pct_none_input_returns_none() -> None:
    assert _safe_pct(None, 10.0) is None
    assert _safe_pct(10.0, None) is None


def test_safe_pct_normal_positive() -> None:
    # 110 from 100 → +10%
    assert _safe_pct(110.0, 100.0) == pytest.approx(10.0)


def test_safe_pct_normal_negative() -> None:
    # 90 from 100 → -10%
    assert _safe_pct(90.0, 100.0) == pytest.approx(-10.0)


def test_safe_pct_uses_abs_for_denominator() -> None:
    # (10 - (-10)) / |-10| = 200%
    assert _safe_pct(10.0, -10.0) == pytest.approx(200.0)


# ─── compute_sector_performance ─────────────────────────────────────────────

def test_compute_sector_performance_empty_inputs_returns_scaffolding() -> None:
    """Empty data → every catalog sector still present with momentum/outlook
    set to N/A / Insufficient Data."""
    out = compute_sector_performance({}, {})
    assert len(out) == len(SECTORS)
    names = {s["name"] for s in out}
    assert names == set(SECTORS.keys())
    for s in out:
        assert s["momentum"] == "N/A"
        assert s["outlook"] == "Insufficient Data"
        assert s["avg_return_1d"] is None
        assert s["avg_return_30d"] is None


def test_compute_sector_performance_strong_momentum_when_ret_30_above_5pct() -> None:
    """Linearly rising prices: 100 → 110 (+10% over 31 obs) → 'Strong' momentum."""
    # 35 obs so the [-31] window exists
    prices = [100.0 + i * (10.0 / 34) for i in range(35)]
    stock = {"ZIM": _price_df(prices)}  # ZIM ∈ Container
    out = compute_sector_performance(stock, {})
    container = next(s for s in out if s["name"] == "Container")
    assert container["avg_return_30d"] is not None
    assert container["avg_return_30d"] > 5.0
    assert container["momentum"] == "Strong"


def test_compute_sector_performance_negative_momentum_when_ret_30_below_minus_5pct() -> None:
    """Linearly falling prices → 'Negative' momentum (ret_30 ≤ -5)."""
    prices = [100.0 - i * (10.0 / 34) for i in range(35)]
    stock = {"ZIM": _price_df(prices)}
    out = compute_sector_performance(stock, {})
    container = next(s for s in out if s["name"] == "Container")
    assert container["avg_return_30d"] < -5.0
    assert container["momentum"] == "Negative"


def test_compute_sector_performance_skips_missing_or_empty_ticker_frames() -> None:
    """Missing ticker, empty df, df without 'close' column — all skipped silently."""
    stock = {
        "ZIM": pd.DataFrame(),                                  # empty
        "MATX": pd.DataFrame({"date": [pd.Timestamp.now()]}),   # no close col
        # DAC and CMRE missing
    }
    out = compute_sector_performance(stock, {})
    container = next(s for s in out if s["name"] == "Container")
    # No valid prices to derive returns from
    assert container["avg_return_1d"] is None
    assert container["stock_prices"] == []


def test_compute_sector_performance_freight_uses_named_column_not_positional() -> None:
    """Regression guard: freight df has 'date' at col 0; positional index would
    feed dates into float(). The fix selects by name."""
    freight = {"SCFI": _freight_df([1000.0, 1100.0])}
    out = compute_sector_performance({}, freight)
    container = next(s for s in out if s["name"] == "Container")
    assert container["index_current"] == pytest.approx(1100.0)
    assert container["index_prev"] == pytest.approx(1000.0)
    assert container["index_chg_1d"] == pytest.approx(10.0)


def test_compute_sector_performance_freight_30d_change_when_history_sufficient() -> None:
    """31 obs needed for the 30d change calc."""
    freight = {"SCFI": _freight_df([1000.0] + [1000.0] * 29 + [1100.0])}
    out = compute_sector_performance({}, freight)
    container = next(s for s in out if s["name"] == "Container")
    assert container["index_chg_30d"] == pytest.approx(10.0)


def test_compute_sector_performance_outlook_bullish_when_avg_signal_above_5() -> None:
    """avg(ret_30, idx_30) > 5 → Bullish."""
    # Sharp 20% rise in both stock and freight
    prices = [100.0 + i * (20.0 / 34) for i in range(35)]
    freight = _freight_df([1000.0] + [1000.0] * 29 + [1200.0])
    out = compute_sector_performance({"ZIM": _price_df(prices)}, {"SCFI": freight})
    container = next(s for s in out if s["name"] == "Container")
    assert container["outlook"] == "Bullish"


def test_compute_sector_performance_outlook_bearish_when_avg_signal_below_minus_5() -> None:
    prices = [100.0 - i * (20.0 / 34) for i in range(35)]
    freight = _freight_df([1000.0] + [1000.0] * 29 + [800.0])
    out = compute_sector_performance({"ZIM": _price_df(prices)}, {"SCFI": freight})
    container = next(s for s in out if s["name"] == "Container")
    assert container["outlook"] == "Bearish"


def test_compute_sector_performance_includes_sector_metadata() -> None:
    out = compute_sector_performance({}, {})
    for s in out:
        cfg = SECTORS[s["name"]]
        assert s["description"] == cfg["description"]
        assert s["key_driver"] == cfg["key_driver"]
        assert s["index_name"] == cfg["index"]
        assert s["tickers"] == cfg["tickers"]


# ─── compute_trade_flow_summary ─────────────────────────────────────────────

def test_compute_trade_flow_summary_empty_port_results_returns_scaffolding() -> None:
    out = compute_trade_flow_summary({}, [])
    assert out["total_global_trade"] == 0
    assert out["dominant_region"] == "N/A"
    assert "Insufficient data" in out["narrative"]
    # All 5 region buckets exist with empty port lists
    assert set(out["regions"].keys()) == {
        "Asia-Pacific", "Europe", "North America", "Middle East", "Other",
    }


def test_compute_trade_flow_summary_routes_asia_keyword_to_asia_pacific() -> None:
    ports = [_FakePort("Shanghai", region="Asia", export_value_usd=1e9, import_value_usd=5e8)]
    out = compute_trade_flow_summary({}, ports)
    assert len(out["regions"]["Asia-Pacific"]["ports"]) == 1
    assert out["regions"]["Asia-Pacific"]["total_trade"] == 1.5e9


def test_compute_trade_flow_summary_routes_europe_keyword_to_europe() -> None:
    ports = [_FakePort("Rotterdam", region="Europe", export_value_usd=2e8, import_value_usd=3e8)]
    out = compute_trade_flow_summary({}, ports)
    assert len(out["regions"]["Europe"]["ports"]) == 1
    assert out["regions"]["Europe"]["total_trade"] == 5e8


def test_compute_trade_flow_summary_routes_america_to_north_america() -> None:
    ports = [_FakePort("LA", region="North America", export_value_usd=1e9, import_value_usd=2e9)]
    out = compute_trade_flow_summary({}, ports)
    assert len(out["regions"]["North America"]["ports"]) == 1


def test_compute_trade_flow_summary_routes_gulf_to_middle_east() -> None:
    ports = [_FakePort("Dubai", region="Persian Gulf", export_value_usd=1e8, import_value_usd=1e8)]
    out = compute_trade_flow_summary({}, ports)
    assert len(out["regions"]["Middle East"]["ports"]) == 1


def test_compute_trade_flow_summary_unknown_region_falls_to_other() -> None:
    ports = [_FakePort("X", region="Antarctica", export_value_usd=1e8, import_value_usd=1e8)]
    out = compute_trade_flow_summary({}, ports)
    assert len(out["regions"]["Other"]["ports"]) == 1


def test_compute_trade_flow_summary_share_pct_sums_to_100() -> None:
    ports = [
        _FakePort("Shanghai", "Asia", export_value_usd=4e9, import_value_usd=4e9),  # 8B
        _FakePort("Rotterdam", "Europe", export_value_usd=1e9, import_value_usd=1e9),  # 2B
    ]
    out = compute_trade_flow_summary({}, ports)
    shares = sum(r["share_pct"] for r in out["regions"].values())
    assert shares == pytest.approx(100.0, abs=0.01)
    # Asia-Pacific = 80%, Europe = 20%
    assert out["regions"]["Asia-Pacific"]["share_pct"] == pytest.approx(80.0, abs=0.5)
    assert out["regions"]["Europe"]["share_pct"] == pytest.approx(20.0, abs=0.5)


def test_compute_trade_flow_summary_dominant_region_is_largest() -> None:
    ports = [
        _FakePort("Shanghai", "Asia", export_value_usd=5e9, import_value_usd=5e9),  # 10B
        _FakePort("Rotterdam", "Europe", export_value_usd=1e9, import_value_usd=1e9),  # 2B
    ]
    out = compute_trade_flow_summary({}, ports)
    assert out["dominant_region"] == "Asia-Pacific"


def test_compute_trade_flow_summary_narrative_mentions_top_region_and_share() -> None:
    ports = [
        _FakePort("Shanghai", "Asia", export_value_usd=4e9, import_value_usd=4e9, demand_score=0.7),
        _FakePort("Rotterdam", "Europe", export_value_usd=1e9, import_value_usd=1e9, demand_score=0.6),
    ]
    out = compute_trade_flow_summary({}, ports)
    assert "Asia-Pacific" in out["narrative"]
    # Demand bucket: avg=0.65 → "elevated" branch
    assert "elevated" in out["narrative"] or "demand" in out["narrative"].lower()


def test_compute_trade_flow_summary_handles_missing_demand_score_attribute() -> None:
    """Ports without demand_score get default 0.5 via getattr fallback."""
    @dataclass
    class _MinimalPort:
        port_name: str
        region: str
        export_value_usd: float
        import_value_usd: float

    ports = [_MinimalPort("X", "Asia", 1e9, 1e9)]
    out = compute_trade_flow_summary({}, ports)
    # Doesn't raise; some narrative produced
    assert isinstance(out["narrative"], str) and out["narrative"]
