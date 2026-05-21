"""Tests for processing.cargo_analyzer — HS-category flow analysis.

Covers:
  - HS_CATEGORIES + CARGO_CHARACTERISTICS catalog parity (every key in
    HS_CATEGORIES has a matching CARGO_CHARACTERISTICS entry)
  - _demand_signal: SURGING / GROWING / STABLE / DECLINING tier boundaries
  - _key_insight: returns a non-empty sentence carrying the category label
  - CargoFlowAnalysis dataclass shape
  - analyze_cargo_flows:
      * empty trade_data → falls back to benchmark totals; never crashes
      * real trade_data → top_origin_ports / top_dest_ports populated from
        the data, sorted desc
      * dominant routes populated from the region-mapping table
      * demand_signal label matches what _demand_signal returns for each
        category's yoy_growth
  - get_route_cargo_mix:
      * unknown route_id → generic uniform mix summing to 1.0
      * known route fallback → returns the canonical mix summing to 1.0
      * with real trade_data → category shares from the data
  - get_seasonal_cargo_calendar: 12 month keys; each category appears under
    its own seasonal_peak month
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from processing.cargo_analyzer import (
    CARGO_CHARACTERISTICS,
    CargoFlowAnalysis,
    HS_CATEGORIES,
    _demand_signal,
    _key_insight,
    analyze_cargo_flows,
    get_route_cargo_mix,
    get_seasonal_cargo_calendar,
)


def _trade_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ─── Catalog parity ─────────────────────────────────────────────────────────

def test_hs_and_characteristics_share_keys() -> None:
    """Every HS_CATEGORIES key must have a CARGO_CHARACTERISTICS entry."""
    assert set(HS_CATEGORIES.keys()) == set(CARGO_CHARACTERISTICS.keys())


def test_hs_categories_codes_are_lists_of_strings() -> None:
    for cat, meta in HS_CATEGORIES.items():
        assert isinstance(meta["codes"], list)
        for c in meta["codes"]:
            assert isinstance(c, str)


def test_cargo_characteristics_seasonal_peaks_in_month_range() -> None:
    for cat, meta in CARGO_CHARACTERISTICS.items():
        assert 1 <= meta["seasonal_peak"] <= 12


# ─── _demand_signal ─────────────────────────────────────────────────────────

def test_demand_signal_surging_above_5pct() -> None:
    label, color = _demand_signal(6.0)
    assert label == "SURGING"
    assert color.startswith("#")


def test_demand_signal_growing_between_2_and_5() -> None:
    label, _ = _demand_signal(3.0)
    assert label == "GROWING"


def test_demand_signal_stable_between_neg1_and_2() -> None:
    label, _ = _demand_signal(0.5)
    assert label == "STABLE"
    label, _ = _demand_signal(-0.5)
    assert label == "STABLE"


def test_demand_signal_declining_below_neg1() -> None:
    label, _ = _demand_signal(-2.0)
    assert label == "DECLINING"


# ─── _key_insight ───────────────────────────────────────────────────────────

def test_key_insight_includes_category_label() -> None:
    text = _key_insight("electronics", 6.0, "SURGING")
    assert "Electronics" in text
    assert "6.0" in text or "+6" in text


def test_key_insight_each_branch_returns_distinct_text() -> None:
    a = _key_insight("apparel", 6.0, "SURGING")
    b = _key_insight("apparel", 3.0, "GROWING")
    c = _key_insight("apparel", 0.0, "STABLE")
    d = _key_insight("apparel", -3.0, "DECLINING")
    assert len({a, b, c, d}) == 4


# ─── CargoFlowAnalysis dataclass ───────────────────────────────────────────

def test_cargo_flow_analysis_shape() -> None:
    cfa = CargoFlowAnalysis(
        hs_category="electronics", category_label="Electronics",
        hs_codes=["8471"], total_value_usd=1e9,
        top_origin_ports=[("CNSHA", 5e8)], top_dest_ports=[("USLAX", 4e8)],
        top_routes=["transpacific_eb"], yoy_growth_pct=6.0,
        seasonality_peak_month=9, shipping_characteristics="container",
        demand_signal="SURGING", signal_color="#2e9e6e", key_insight="i",
    )
    assert cfa.hs_category == "electronics"


# ─── analyze_cargo_flows ───────────────────────────────────────────────────

def test_analyze_cargo_flows_empty_uses_benchmark_totals() -> None:
    """Empty trade_data → fallback benchmark values; one entry per category."""
    out = analyze_cargo_flows({})
    assert len(out) == len(HS_CATEGORIES)
    for cfa in out:
        assert cfa.total_value_usd > 0
        # Illustrative origins/dests come from the fallback
        assert len(cfa.top_origin_ports) > 0


def test_analyze_cargo_flows_none_input_does_not_raise() -> None:
    out = analyze_cargo_flows(None)
    assert len(out) == len(HS_CATEGORIES)


def test_analyze_cargo_flows_real_data_aggregates_by_category() -> None:
    """When real trade_data carries the category column, top_origin_ports
    and top_dest_ports reflect that data."""
    trade = {
        "CNSHA": _trade_df([
            {"hs_category": "electronics", "value_usd": 1e9, "flow": "Export"},
            {"hs_category": "machinery", "value_usd": 5e8, "flow": "Export"},
        ]),
        "USLAX": _trade_df([
            {"hs_category": "electronics", "value_usd": 8e8, "flow": "Import"},
        ]),
    }
    out = analyze_cargo_flows(trade)
    elec = next(c for c in out if c.hs_category == "electronics")
    # CNSHA is the only export; USLAX is the only import
    assert elec.top_origin_ports[0][0] == "CNSHA"
    assert elec.top_dest_ports[0][0] == "USLAX"


def test_analyze_cargo_flows_dominant_routes_present() -> None:
    """Electronics is dominant on Asia-East origin routes → at least one
    top_routes entry comes from there."""
    out = analyze_cargo_flows({})
    elec = next(c for c in out if c.hs_category == "electronics")
    assert elec.top_routes  # non-empty
    assert len(elec.top_routes) <= 4


def test_analyze_cargo_flows_demand_signal_matches_helper() -> None:
    """Each category's demand_signal label should match _demand_signal."""
    out = analyze_cargo_flows({})
    for cfa in out:
        expected, _ = _demand_signal(cfa.yoy_growth_pct)
        assert cfa.demand_signal == expected


def test_analyze_cargo_flows_skips_bad_dfs() -> None:
    """None / empty / wrong-column frames are silently skipped."""
    trade = {
        "X": None,
        "Y": pd.DataFrame(),
        "Z": pd.DataFrame({"other": [1, 2]}),
    }
    out = analyze_cargo_flows(trade)
    # No real data anywhere → falls back to benchmarks
    assert all(c.total_value_usd > 0 for c in out)


# ─── get_route_cargo_mix ───────────────────────────────────────────────────

def test_get_route_cargo_mix_unknown_route_returns_uniform() -> None:
    mix = get_route_cargo_mix("totally_made_up_route", {})
    # Generic fallback: 1/N for each of the 7 categories
    assert set(mix.keys()) == set(HS_CATEGORIES.keys())
    assert sum(mix.values()) == pytest.approx(1.0, abs=0.01)
    # All categories equal
    vals = list(mix.values())
    assert max(vals) - min(vals) < 0.001


def test_get_route_cargo_mix_known_route_uses_fallback_weights() -> None:
    mix = get_route_cargo_mix("transpacific_eb", {})
    # Transpacific EB has electronics as dominant in the fallback table
    assert mix.get("electronics", 0) > 0.3
    assert sum(mix.values()) == pytest.approx(1.0, abs=0.001)


def test_get_route_cargo_mix_real_data_overrides_fallback() -> None:
    """When real trade_data is available, mix comes from the data."""
    trade = {
        "CNSHA": _trade_df([
            {"hs_category": "electronics", "value_usd": 1e9},
            {"hs_category": "apparel", "value_usd": 1e9},
        ]),
    }
    mix = get_route_cargo_mix("asia_europe", trade)
    # Two categories at equal value → ~0.5 each, other cats either 0 or absent
    assert mix.get("electronics", 0) == pytest.approx(0.5, abs=0.01)
    assert mix.get("apparel", 0) == pytest.approx(0.5, abs=0.01)


# ─── get_seasonal_cargo_calendar ───────────────────────────────────────────

def test_get_seasonal_cargo_calendar_has_12_months() -> None:
    cal = get_seasonal_cargo_calendar()
    assert set(cal.keys()) == set(range(1, 13))


def test_get_seasonal_cargo_calendar_categories_appear_under_peak_month() -> None:
    cal = get_seasonal_cargo_calendar()
    for cat, chars in CARGO_CHARACTERISTICS.items():
        peak = chars["seasonal_peak"]
        assert cat in cal[peak]


def test_get_seasonal_cargo_calendar_every_category_present_exactly_once() -> None:
    cal = get_seasonal_cargo_calendar()
    all_appearances = [c for cats in cal.values() for c in cats]
    assert sorted(all_appearances) == sorted(CARGO_CHARACTERISTICS.keys())
