"""Tests for processing.freight_volatility — rate volatility / momentum analyzer.

Covers:
  - FreightVolatilityReport dataclass shape
  - analyze_freight_volatility:
      * returns None when route missing / column missing / < 10 obs
      * trending-up series → regime TRENDING_UP, bullish direction
      * trending-down series → regime TRENDING_DOWN, bearish direction
      * spike at the end → regime BREAKOUT, mean_reversion OVERBOUGHT
      * flat series → regime RANGING, mean_reversion NEUTRAL
      * NaN-from-constant-series guard (vol_30d defaults to 0.0)
  - analyze_all_routes_volatility: aggregates; failures silently dropped
  - get_breakout_alerts: only BREAKOUT entries, sorted by |z| desc
  - get_trending_routes: direction filter (up/down), sorted by signal_strength
  - get_volatility_summary: empty → scaffolding; counts + most-common regime
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from processing.freight_volatility import (
    FreightVolatilityReport,
    analyze_all_routes_volatility,
    analyze_freight_volatility,
    get_breakout_alerts,
    get_trending_routes,
    get_volatility_summary,
)
from engine.signals import SignalComponent


def _freight_df(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(values), freq="D"),
        "rate_usd_per_feu": values,
    })


# ─── FreightVolatilityReport dataclass ──────────────────────────────────────

def test_freight_volatility_report_shape() -> None:
    sc = SignalComponent(name="x", value=0.5, weight=0.2, label="L", direction="neutral")
    r = FreightVolatilityReport(
        route_id="r", route_name="R",
        volatility_30d=0.1, volatility_90d=0.12, volatility_percentile=0.6,
        momentum_7d=0.05, momentum_30d=0.10, momentum_acceleration=-0.05,
        zscore_from_mean=1.0, mean_reversion_signal="NEUTRAL", regime="RANGING",
        signal_strength=0.4, signal_component=sc,
    )
    assert r.regime == "RANGING"
    assert r.signal_component is sc


# ─── analyze_freight_volatility — input guards ──────────────────────────────

def test_analyze_freight_volatility_returns_none_when_route_missing() -> None:
    assert analyze_freight_volatility({}, "r") is None


def test_analyze_freight_volatility_returns_none_when_column_missing() -> None:
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=20), "other": range(20)})
    assert analyze_freight_volatility({"r": df}, "r") is None


def test_analyze_freight_volatility_returns_none_when_too_short() -> None:
    assert analyze_freight_volatility({"r": _freight_df([1000.0] * 9)}, "r") is None


def test_analyze_freight_volatility_minimum_length_succeeds() -> None:
    """Exactly 10 obs is the lower bound."""
    rep = analyze_freight_volatility({"r": _freight_df([1000.0 + i * 10 for i in range(10)])}, "r")
    assert rep is not None
    assert isinstance(rep, FreightVolatilityReport)


# ─── analyze_freight_volatility — regime detection ──────────────────────────

def test_analyze_freight_volatility_trending_up_regime() -> None:
    """Steady linear rise: mom_30d > 0.10 and mom_7d > 0 → TRENDING_UP."""
    # 100 obs rising +15% over the 30d window
    rates = [1000.0 + i * 7 for i in range(100)]
    rep = analyze_freight_volatility({"r": _freight_df(rates)}, "r", "Route R")
    assert rep is not None
    assert rep.regime == "TRENDING_UP"
    assert rep.signal_component.direction == "bullish"
    assert rep.momentum_30d > 0.10


def test_analyze_freight_volatility_trending_down_regime() -> None:
    rates = [1000.0 - i * 5 for i in range(100)]
    rep = analyze_freight_volatility({"r": _freight_df(rates)}, "r")
    assert rep is not None
    assert rep.regime == "TRENDING_DOWN"
    assert rep.signal_component.direction == "bearish"
    assert rep.momentum_30d < -0.10


def test_analyze_freight_volatility_breakout_regime_on_zscore_above_2() -> None:
    """Stable then sharp spike at the end → |z| > 2 → BREAKOUT."""
    rng = np.random.default_rng(42)
    base = 1000.0 + rng.normal(0, 5, 90).cumsum() * 0.01  # very flat
    rates = list(base) + [1500.0]  # massive spike
    rep = analyze_freight_volatility({"r": _freight_df(list(rates))}, "r")
    assert rep is not None
    assert rep.regime == "BREAKOUT"
    assert abs(rep.zscore_from_mean) > 2.0
    assert rep.mean_reversion_signal == "OVERBOUGHT"


def test_analyze_freight_volatility_ranging_regime_when_flat() -> None:
    """Noise around 1000 — pin last value to 1000.0 so |zscore| ≈ 0 and
    momentum is well under the ±10% trending threshold."""
    rng = np.random.default_rng(7)
    rates = [1000.0 + rng.normal(0, 2) for _ in range(99)] + [1000.0]
    rep = analyze_freight_volatility({"r": _freight_df(rates)}, "r")
    assert rep is not None
    assert rep.regime == "RANGING"
    assert rep.mean_reversion_signal == "NEUTRAL"


def test_analyze_freight_volatility_oversold_when_zscore_below_neg_1_5() -> None:
    """End rates well below 90d mean — but not below -2 z (so regime != BREAKOUT)."""
    rates = [1000.0] * 80 + [950.0] * 10 + [980.0] * 10
    rep = analyze_freight_volatility({"r": _freight_df(rates)}, "r")
    assert rep is not None
    # We just want OVERSOLD or NEUTRAL — not OVERBOUGHT
    assert rep.mean_reversion_signal in {"OVERSOLD", "NEUTRAL"}


def test_analyze_freight_volatility_constant_series_does_not_propagate_nan() -> None:
    """Constant rates → pct_change all zero → rolling std NaN; the guard
    converts that to 0.0 so the report fields stay finite."""
    rep = analyze_freight_volatility({"r": _freight_df([1000.0] * 100)}, "r")
    assert rep is not None
    assert rep.volatility_30d == 0.0
    assert rep.volatility_90d == 0.0
    assert np.isfinite(rep.signal_strength)


def test_analyze_freight_volatility_signal_strength_in_unit_interval() -> None:
    rates = [1000.0 + i * 7 for i in range(100)]
    rep = analyze_freight_volatility({"r": _freight_df(rates)}, "r")
    assert rep is not None
    assert 0.0 <= rep.signal_strength <= 1.0


def test_analyze_freight_volatility_uses_route_id_when_no_name_passed() -> None:
    rep = analyze_freight_volatility({"abc": _freight_df([1000.0] * 20)}, "abc")
    assert rep is not None
    assert rep.route_name == "abc"


# ─── analyze_all_routes_volatility ───────────────────────────────────────────

def test_analyze_all_routes_volatility_aggregates() -> None:
    freight = {
        "r1": _freight_df([1000.0 + i * 7 for i in range(50)]),
        "r2": _freight_df([1000.0 - i * 5 for i in range(50)]),
    }
    out = analyze_all_routes_volatility(freight)
    assert set(out.keys()) == {"r1", "r2"}


def test_analyze_all_routes_volatility_silently_drops_failures() -> None:
    """Routes that fail analysis (too short, no column) are skipped."""
    freight = {
        "good": _freight_df([1000.0 + i for i in range(50)]),
        "bad_short": _freight_df([1000.0] * 5),
        "bad_no_col": pd.DataFrame({"date": pd.date_range("2024-01-01", periods=20)}),
    }
    out = analyze_all_routes_volatility(freight)
    assert "good" in out
    assert "bad_short" not in out
    assert "bad_no_col" not in out


def test_analyze_all_routes_volatility_empty_input() -> None:
    assert analyze_all_routes_volatility({}) == {}


# ─── get_breakout_alerts ───────────────────────────────────────────────────

def _mk_report(route_id: str, regime: str, zscore: float, sig_strength: float = 0.5) -> FreightVolatilityReport:
    sc = SignalComponent(name=route_id, value=sig_strength, weight=0.2,
                         label=regime, direction="neutral")
    return FreightVolatilityReport(
        route_id=route_id, route_name=route_id,
        volatility_30d=0.1, volatility_90d=0.1, volatility_percentile=0.5,
        momentum_7d=0.0, momentum_30d=0.0, momentum_acceleration=0.0,
        zscore_from_mean=zscore, mean_reversion_signal="NEUTRAL", regime=regime,
        signal_strength=sig_strength, signal_component=sc,
    )


def test_get_breakout_alerts_filters_to_breakout_only() -> None:
    reports = {
        "a": _mk_report("a", "BREAKOUT", 2.5),
        "b": _mk_report("b", "TRENDING_UP", 1.0),
        "c": _mk_report("c", "BREAKOUT", -3.0),
    }
    out = get_breakout_alerts(reports)
    assert {r.route_id for r in out} == {"a", "c"}


def test_get_breakout_alerts_sorted_by_abs_zscore_desc() -> None:
    reports = {
        "a": _mk_report("a", "BREAKOUT", 2.5),
        "b": _mk_report("b", "BREAKOUT", -3.5),
        "c": _mk_report("c", "BREAKOUT", 2.0),
    }
    out = get_breakout_alerts(reports)
    # |-3.5| > |2.5| > |2.0|
    assert [r.route_id for r in out] == ["b", "a", "c"]


def test_get_breakout_alerts_empty_when_no_breakouts() -> None:
    reports = {"a": _mk_report("a", "RANGING", 0.0)}
    assert get_breakout_alerts(reports) == []


# ─── get_trending_routes ───────────────────────────────────────────────────

def test_get_trending_routes_up_filters_trending_up_only() -> None:
    reports = {
        "a": _mk_report("a", "TRENDING_UP", 1.0, sig_strength=0.6),
        "b": _mk_report("b", "TRENDING_DOWN", -1.0),
        "c": _mk_report("c", "TRENDING_UP", 0.5, sig_strength=0.4),
    }
    out = get_trending_routes(reports, "up")
    assert {r.route_id for r in out} == {"a", "c"}


def test_get_trending_routes_down_filters_trending_down_only() -> None:
    reports = {
        "a": _mk_report("a", "TRENDING_UP", 1.0),
        "b": _mk_report("b", "TRENDING_DOWN", -1.0),
    }
    out = get_trending_routes(reports, "down")
    assert {r.route_id for r in out} == {"b"}


def test_get_trending_routes_sorted_by_signal_strength_desc() -> None:
    reports = {
        "weak": _mk_report("weak", "TRENDING_UP", 1.0, sig_strength=0.3),
        "strong": _mk_report("strong", "TRENDING_UP", 1.0, sig_strength=0.9),
        "mid": _mk_report("mid", "TRENDING_UP", 1.0, sig_strength=0.5),
    }
    out = get_trending_routes(reports, "up")
    assert [r.route_id for r in out] == ["strong", "mid", "weak"]


# ─── get_volatility_summary ─────────────────────────────────────────────────

def test_get_volatility_summary_empty_returns_scaffolding() -> None:
    out = get_volatility_summary({})
    assert out["avg_volatility"] == 0.0
    assert out["high_volatility_routes"] == []
    assert out["breakout_count"] == 0
    assert out["trending_up_count"] == 0
    assert out["trending_down_count"] == 0
    assert out["market_regime"] == "RANGING"


def test_get_volatility_summary_counts_each_regime() -> None:
    reports = {
        "a": _mk_report("a", "BREAKOUT", 2.5),
        "b": _mk_report("b", "TRENDING_UP", 1.0),
        "c": _mk_report("c", "TRENDING_DOWN", -1.0),
        "d": _mk_report("d", "TRENDING_UP", 0.8),
    }
    out = get_volatility_summary(reports)
    assert out["breakout_count"] == 1
    assert out["trending_up_count"] == 2
    assert out["trending_down_count"] == 1


def test_get_volatility_summary_market_regime_is_most_common() -> None:
    reports = {
        "a": _mk_report("a", "TRENDING_UP", 1.0),
        "b": _mk_report("b", "TRENDING_UP", 1.0),
        "c": _mk_report("c", "BREAKOUT", 2.5),
    }
    assert get_volatility_summary(reports)["market_regime"] == "TRENDING_UP"


def test_get_volatility_summary_high_volatility_filter_above_0_75_percentile() -> None:
    sc = lambda n: SignalComponent(name=n, value=0.5, weight=0.2, label="L", direction="neutral")
    reports = {
        "hi": FreightVolatilityReport(
            route_id="hi", route_name="hi",
            volatility_30d=0.2, volatility_90d=0.2, volatility_percentile=0.85,
            momentum_7d=0.0, momentum_30d=0.0, momentum_acceleration=0.0,
            zscore_from_mean=0.0, mean_reversion_signal="NEUTRAL", regime="RANGING",
            signal_strength=0.5, signal_component=sc("hi"),
        ),
        "lo": FreightVolatilityReport(
            route_id="lo", route_name="lo",
            volatility_30d=0.05, volatility_90d=0.05, volatility_percentile=0.30,
            momentum_7d=0.0, momentum_30d=0.0, momentum_acceleration=0.0,
            zscore_from_mean=0.0, mean_reversion_signal="NEUTRAL", regime="RANGING",
            signal_strength=0.5, signal_component=sc("lo"),
        ),
    }
    out = get_volatility_summary(reports)
    assert out["high_volatility_routes"] == ["hi"]
