"""Tests for utils.html_report — standalone HTML intelligence report generator.

Covers:
  - Pure helpers: _hex_to_rgba, _score_bar, _badge, _score_color,
    _risk_level, _market_assessment, _fmt_rate, _fmt_pct, _change_cell
  - Static catalogs: _CATEGORY_COLORS, _ACTION_COLORS, _RISK_COLORS,
    _DIRECTION_ARROW, _DIRECTION_COLOR keyspaces
  - Section builders: _build_hero, _build_exec_summary, _build_insights_section,
    _build_port_table, _build_route_table, _build_freight_table,
    _build_macro_section, _build_stock_section, _build_appendix, _build_footer
  - Public entry points: generate_html_report (happy + empty-input path),
    get_report_as_bytes
  - Defining properties: insights sort by score, empty input → empty string
    or minimal valid HTML, stat counts match input sizes.

All seeds explicit integers. Tests check STRUCTURAL facts (h1 text, badge
text, row counts, data values) not cosmetic markup (specific CSS classes
or exact tag attributes).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from utils.html_report import (
    _ACTION_COLORS,
    _CATEGORY_COLORS,
    _DIRECTION_ARROW,
    _DIRECTION_COLOR,
    _RISK_COLORS,
    _badge,
    _build_appendix,
    _build_exec_summary,
    _build_footer,
    _build_freight_table,
    _build_hero,
    _build_insights_section,
    _build_macro_section,
    _build_port_table,
    _build_route_table,
    _build_stock_section,
    _change_cell,
    _fmt_pct,
    _fmt_rate,
    _hex_to_rgba,
    _market_assessment,
    _risk_level,
    _score_bar,
    _score_color,
    generate_html_report,
    get_report_as_bytes,
)


# ─── Stand-in dataclasses for duck-typed inputs ─────────────────────────────

@dataclass
class FakeSignal:
    name: str = "FRED PMI"
    value: float = 52.3
    weight: float = 0.4
    direction: str = "bullish"
    label: str = "Expansion territory"


@dataclass
class FakeInsight:
    title: str = "Test insight"
    detail: str = "Some detail text describing the insight in concise terms."
    score: float = 0.75
    score_label: str = "High"
    action: str = "Prioritize"
    category: str = "MACRO"
    insight_id: str = "INS-001"
    ports_involved: list = field(default_factory=list)
    routes_involved: list = field(default_factory=list)
    stocks_potentially_affected: list = field(default_factory=list)
    supporting_signals: list = field(default_factory=list)
    data_freshness_warning: bool = False


# ─── _hex_to_rgba ────────────────────────────────────────────────────────────

def test_hex_to_rgba_basic_components():
    assert _hex_to_rgba("#ff0000", 1.0) == "rgba(255,0,0,1.0)"


def test_hex_to_rgba_strips_leading_hash():
    with_hash = _hex_to_rgba("#3572b0", 0.5)
    without_hash = _hex_to_rgba("3572b0", 0.5)
    assert with_hash == without_hash


def test_hex_to_rgba_alpha_included():
    out = _hex_to_rgba("#000000", 0.25)
    assert "0.25" in out
    assert out.startswith("rgba(") and out.endswith(")")


# ─── _score_bar ──────────────────────────────────────────────────────────────

def test_score_bar_percentage_label():
    html = _score_bar(0.75)
    assert "75%" in html


def test_score_bar_zero_score_has_minimum_width():
    html = _score_bar(0.0)
    # `max(2, ...)` floor — bar should still render with width:2px
    assert "width:2px" in html


def test_score_bar_full_score_uses_full_width():
    html = _score_bar(1.0, width=120)
    assert "width:120px" in html


def test_score_bar_custom_color_propagated():
    html = _score_bar(0.5, color="#abcdef")
    assert "#abcdef" in html


# ─── _badge ──────────────────────────────────────────────────────────────────

def test_badge_includes_text():
    assert "URGENT" in _badge("URGENT", "#ff0000")


def test_badge_includes_color():
    out = _badge("Hi", "#3572b0")
    assert "#3572b0" in out
    # rgba derived background also expected
    assert "rgba(53,114,176" in out


# ─── _score_color ────────────────────────────────────────────────────────────

def test_score_color_high_threshold():
    assert _score_color(0.85) == "#2e9e6e"
    assert _score_color(0.70) == "#2e9e6e"  # boundary inclusive


def test_score_color_moderate_threshold():
    assert _score_color(0.55) == "#c9962b"
    assert _score_color(0.40) == "#c9962b"  # boundary inclusive


def test_score_color_low_threshold():
    assert _score_color(0.25) == "#c0392b"
    assert _score_color(0.0) == "#c0392b"


# ─── _fmt_rate / _fmt_pct ────────────────────────────────────────────────────

def test_fmt_rate_thousands_separator_and_dollar():
    assert _fmt_rate(2850.0) == "$2,850"


def test_fmt_rate_rounds_to_integer():
    assert _fmt_rate(2850.49) == "$2,850"


def test_fmt_pct_positive_shows_plus_by_default():
    assert _fmt_pct(3.5) == "+3.5%"


def test_fmt_pct_negative_keeps_minus():
    assert _fmt_pct(-2.1) == "-2.1%"


def test_fmt_pct_suppresses_plus_when_disabled():
    assert _fmt_pct(3.5, show_sign=False) == "3.5%"


# ─── _change_cell ────────────────────────────────────────────────────────────

def test_change_cell_positive_uses_up_arrow_and_high_color():
    out = _change_cell(2.5)
    assert "&#9650;" in out
    assert "#2e9e6e" in out  # _C_HIGH
    assert "+2.5%" in out


def test_change_cell_negative_uses_down_arrow_and_low_color():
    out = _change_cell(-1.8)
    assert "&#9660;" in out
    assert "#c0392b" in out  # _C_LOW
    assert "-1.8%" in out


# ─── _risk_level ─────────────────────────────────────────────────────────────

def test_risk_level_empty_is_moderate():
    assert _risk_level([]) == "MODERATE"


def test_risk_level_high_avg_no_bearish_is_low():
    insights = [FakeInsight(score=0.85), FakeInsight(score=0.80)]
    assert _risk_level(insights) == "LOW"


def test_risk_level_high_avg_but_bearish_signals_demotes():
    bearish_sig = FakeSignal(direction="bearish")
    insights = [
        FakeInsight(score=0.85, supporting_signals=[bearish_sig]),
        FakeInsight(score=0.80, supporting_signals=[bearish_sig]),
    ]
    # avg=0.825, bearish_count > 0 → not LOW; falls to MODERATE
    assert _risk_level(insights) == "MODERATE"


def test_risk_level_low_avg_majority_bearish_is_high():
    bearish_sig = FakeSignal(direction="bearish")
    insights = [
        FakeInsight(score=0.30, supporting_signals=[bearish_sig]),
        FakeInsight(score=0.25, supporting_signals=[bearish_sig]),
        FakeInsight(score=0.35, supporting_signals=[bearish_sig]),
    ]
    assert _risk_level(insights) == "HIGH"


# ─── _market_assessment ──────────────────────────────────────────────────────

def test_market_assessment_empty():
    assert "Insufficient data" in _market_assessment([])


def test_market_assessment_high_confidence_mentions_constructive():
    insights = [
        FakeInsight(score=0.85, category="PORT_DEMAND"),
        FakeInsight(score=0.75, category="PORT_DEMAND"),
    ]
    out = _market_assessment(insights)
    assert "constructive" in out.lower()
    assert "port demand" in out.lower()


def test_market_assessment_moderate_uses_mixed_wording():
    insights = [FakeInsight(score=0.55, category="ROUTE")]
    out = _market_assessment(insights)
    assert "mixed" in out.lower() or "selective" in out.lower()


def test_market_assessment_low_uses_cautionary():
    insights = [FakeInsight(score=0.20, category="MACRO")]
    out = _market_assessment(insights)
    assert "caution" in out.lower() or "defensive" in out.lower()


# ─── Static catalogs ─────────────────────────────────────────────────────────

def test_category_colors_covers_known_categories():
    assert set(_CATEGORY_COLORS) >= {"CONVERGENCE", "ROUTE", "PORT_DEMAND", "MACRO"}


def test_action_colors_covers_known_actions():
    assert set(_ACTION_COLORS) >= {"Prioritize", "Monitor", "Watch", "Caution", "Avoid"}


def test_risk_colors_covers_all_levels():
    assert set(_RISK_COLORS) == {"LOW", "MODERATE", "HIGH", "CRITICAL"}


def test_direction_arrow_and_color_keys_aligned():
    # Every direction in arrow map should also have a color
    assert set(_DIRECTION_ARROW) == set(_DIRECTION_COLOR)


# ─── _build_hero ─────────────────────────────────────────────────────────────

def test_build_hero_stat_counts_reflect_inputs():
    ports = [{"x": 1}, {"y": 2}, {"z": 3}]
    routes = [{"a": 1}, {"b": 2}]
    insights = [
        FakeInsight(score=0.85, supporting_signals=[FakeSignal(), FakeSignal()]),
        FakeInsight(score=0.50, supporting_signals=[FakeSignal()]),
    ]
    html = _build_hero(ports, routes, insights, "2026-05-21 12:00 UTC", "Q2 2026")
    # 3 ports, 2 routes, 3 signals, 1 high-conv (score>=0.70)
    assert ">3<" in html  # ports analyzed
    assert ">2<" in html  # routes
    assert "Ports Analyzed" in html
    assert "Routes Tracked" in html
    assert "Active Signals" in html
    assert "High-Conviction" in html


def test_build_hero_includes_timestamp_and_date_range():
    html = _build_hero([], [], [], "2026-05-21 12:00 UTC", "Q2 2026")
    assert "2026-05-21 12:00 UTC" in html
    assert "Q2 2026" in html


def test_build_hero_omits_date_range_when_empty():
    html = _build_hero([], [], [], "2026-05-21 12:00 UTC", "")
    # No spurious em-dash before missing date range
    assert "Q2 2026" not in html


def test_build_hero_title_present():
    html = _build_hero([], [], [], "ts", "")
    assert "Container Market Intelligence Report" in html


# ─── _build_exec_summary ─────────────────────────────────────────────────────

def test_build_exec_summary_empty_insights_shows_placeholder():
    html = _build_exec_summary([])
    assert "No insights available" in html
    assert "Executive Summary" in html


def test_build_exec_summary_shows_top_three_only():
    insights = [
        FakeInsight(title=f"Insight {i}", score=0.80 - i * 0.05)
        for i in range(5)
    ]
    html = _build_exec_summary(insights)
    assert "Insight 0" in html
    assert "Insight 1" in html
    assert "Insight 2" in html
    assert "Insight 3" not in html
    assert "Insight 4" not in html


def test_build_exec_summary_risk_badge_present():
    insights = [FakeInsight(score=0.85), FakeInsight(score=0.80)]
    html = _build_exec_summary(insights)
    assert "Risk: LOW" in html


def test_build_exec_summary_long_detail_truncated():
    long_detail = "x" * 500
    insights = [FakeInsight(title="T", detail=long_detail)]
    html = _build_exec_summary(insights)
    # 160-char cap then trailing ellipsis added
    assert "..." in html
    assert long_detail not in html  # full string not embedded


# ─── _build_insights_section ─────────────────────────────────────────────────

def test_build_insights_section_empty_returns_empty_string():
    assert _build_insights_section([]) == ""


def test_build_insights_section_caps_at_five():
    insights = [
        FakeInsight(title=f"Insight {i}", insight_id=f"INS-{i:03d}")
        for i in range(8)
    ]
    html = _build_insights_section(insights)
    assert "INS-000" in html
    assert "INS-004" in html
    assert "INS-005" not in html
    assert "INS-007" not in html


def test_build_insights_section_renders_signal_table_when_signals_present():
    sigs = [FakeSignal(name="PMI", value=52.3, weight=0.4)]
    insights = [FakeInsight(supporting_signals=sigs)]
    html = _build_insights_section(insights)
    assert "PMI" in html
    assert "52.30" in html  # value formatted with 2 decimals


def test_build_insights_section_omits_signal_table_when_no_signals():
    insights = [FakeInsight(supporting_signals=[])]
    html = _build_insights_section(insights)
    # No signal-table block rendered
    assert "<table class=\"signal-table\">" not in html


def test_build_insights_section_freshness_warning_badge():
    fresh = FakeInsight(data_freshness_warning=False, insight_id="A")
    stale = FakeInsight(data_freshness_warning=True, insight_id="B")
    fresh_html = _build_insights_section([fresh])
    stale_html = _build_insights_section([stale])
    assert "Stale Data" not in fresh_html
    assert "STALE DATA" in stale_html.upper()


def test_build_insights_section_dash_for_empty_lists():
    ins = FakeInsight(ports_involved=[], routes_involved=[], stocks_potentially_affected=[])
    html = _build_insights_section([ins])
    # &mdash; entity used as placeholder
    assert "&mdash;" in html


# ─── _build_port_table ───────────────────────────────────────────────────────

def test_build_port_table_empty_returns_empty_string():
    assert _build_port_table([]) == ""


def test_build_port_table_ranks_rows_sequentially():
    ports = [
        {"port_name": "Shanghai", "region": "APAC", "demand_score": 0.85},
        {"port_name": "Rotterdam", "region": "EU", "demand_score": 0.60},
    ]
    html = _build_port_table(ports)
    assert "#1" in html
    assert "#2" in html
    assert "Shanghai" in html
    assert "Rotterdam" in html


def test_build_port_table_falls_back_to_port_id_when_no_name():
    ports = [{"port_id": "CNSHA", "demand_score": 0.5}]
    html = _build_port_table(ports)
    assert "CNSHA" in html


def test_build_port_table_handles_missing_congestion_with_dash():
    ports = [{"port_name": "X", "demand_score": 0.5}]  # no congestion_score
    html = _build_port_table(ports)
    assert "—" in html  # literal em-dash placeholder


def test_build_port_table_congestion_renders_when_present():
    ports = [{"port_name": "X", "demand_score": 0.5, "congestion_score": 0.75}]
    html = _build_port_table(ports)
    # 75% rendered
    assert "75%" in html


# ─── _build_route_table ──────────────────────────────────────────────────────

def test_build_route_table_empty_returns_empty_string():
    assert _build_route_table([]) == ""


def test_build_route_table_includes_origin_destination_arrow():
    routes = [{
        "route_name": "Transpac EB",
        "origin": "CNSHA",
        "destination": "USLAX",
        "score": 0.7,
    }]
    html = _build_route_table(routes)
    assert "CNSHA" in html
    assert "USLAX" in html
    assert "&rarr;" in html


def test_build_route_table_formats_current_rate_as_dollars():
    routes = [{"route_name": "R1", "current_rate": 2850.0, "score": 0.7}]
    html = _build_route_table(routes)
    assert "$2,850" in html


def test_build_route_table_30d_change_color_and_arrow():
    routes_up = [{"route_name": "R", "change_30d": 4.5, "score": 0.5}]
    routes_dn = [{"route_name": "R", "change_30d": -3.1, "score": 0.5}]
    html_up = _build_route_table(routes_up)
    html_dn = _build_route_table(routes_dn)
    assert "&#9650;" in html_up
    assert "+4.5%" in html_up
    assert "&#9660;" in html_dn
    assert "-3.1%" in html_dn


def test_build_route_table_trend_arrow_for_known_directions():
    routes = [{"route_name": "R", "score": 0.5, "trend": "Rising"}]
    html = _build_route_table(routes)
    # Rising → up triangle
    assert "&#9650;" in html
    assert "Rising" in html


# ─── _build_freight_table ────────────────────────────────────────────────────

def test_build_freight_table_none_returns_empty():
    assert _build_freight_table(None) == ""
    assert _build_freight_table({}) == ""
    assert _build_freight_table([]) == ""


def test_build_freight_table_accepts_dict_of_dicts():
    data = {
        "transpac": {"current_rate": 2500.0, "change_30d": 1.5, "change_90d": -2.0},
    }
    html = _build_freight_table(data)
    assert "transpac" in html
    assert "$2,500" in html
    assert "+1.5%" in html
    assert "-2.0%" in html


def test_build_freight_table_accepts_list_of_dicts():
    data = [
        {"route": "asia_eu", "current_rate": 1900.0, "change_30d": 0.5},
    ]
    html = _build_freight_table(data)
    assert "asia_eu" in html
    assert "$1,900" in html


def test_build_freight_table_scalar_value_dict_form():
    # Dict with numeric values (not nested dicts) becomes one row per key
    data = {"transpac": 2500.0}
    html = _build_freight_table(data)
    assert "transpac" in html
    assert "$2,500" in html


def test_build_freight_table_missing_changes_render_dash():
    data = [{"route": "x", "current_rate": 1000.0}]  # no change_30d / change_90d
    html = _build_freight_table(data)
    assert "—" in html


# ─── _build_macro_section ────────────────────────────────────────────────────

def test_build_macro_section_none_returns_empty():
    assert _build_macro_section(None) == ""
    assert _build_macro_section({}) == ""


def test_build_macro_section_renders_series_rows():
    data = {
        "GDP": {"name": "Gross Domestic Product", "value": 23.5, "unit": "T", "trend": "Rising"},
    }
    html = _build_macro_section(data)
    assert "GDP" in html
    assert "Gross Domestic Product" in html
    assert "23.5" in html


def test_build_macro_section_handles_scalar_dict_value():
    data = {"UNRATE": 3.7}
    html = _build_macro_section(data)
    assert "UNRATE" in html
    assert "3.7" in html


def test_build_macro_section_trend_color_for_directions():
    rising = {"X": {"value": 1.0, "trend": "Rising"}}
    falling = {"X": {"value": 1.0, "trend": "Falling"}}
    html_up = _build_macro_section(rising)
    html_dn = _build_macro_section(falling)
    assert "#2e9e6e" in html_up   # _C_HIGH
    assert "#c0392b" in html_dn   # _C_LOW


# ─── _build_stock_section ────────────────────────────────────────────────────

def test_build_stock_section_none_returns_empty():
    assert _build_stock_section(None) == ""
    assert _build_stock_section({}) == ""
    assert _build_stock_section([]) == ""


def test_build_stock_section_dict_keyed_by_ticker():
    data = {
        "ZIM": {"name": "ZIM Integrated", "price": 22.50, "change_1d": 1.2, "change_30d": -3.5},
    }
    html = _build_stock_section(data)
    assert "ZIM" in html
    assert "ZIM Integrated" in html
    assert "$22.50" in html


def test_build_stock_section_signal_badge_uses_action_colors():
    data = [{"ticker": "DAC", "name": "Danaos", "price": 80.0, "signal": "Prioritize"}]
    html = _build_stock_section(data)
    assert "Prioritize".upper() in html.upper()
    # _ACTION_COLORS["Prioritize"] = _C_HIGH = #2e9e6e
    assert "#2e9e6e" in html


def test_build_stock_section_scalar_dict_form():
    data = {"ZIM": 22.5}
    html = _build_stock_section(data)
    assert "ZIM" in html
    assert "$22.50" in html


# ─── _build_appendix / _build_footer ─────────────────────────────────────────

def test_build_appendix_lists_known_sources():
    html = _build_appendix()
    assert "Freight Rates" in html
    assert "FRED" in html
    assert "Yahoo Finance" in html
    assert "Data Sources" in html


def test_build_footer_contains_disclaimer():
    html = _build_footer()
    assert "Ship Tracker" in html
    assert "informational purposes" in html.lower()


# ─── generate_html_report (top-level orchestrator) ───────────────────────────

def test_generate_html_report_minimal_inputs_returns_valid_html():
    html = generate_html_report([], [], [], None, None, None)
    assert html.startswith("<!DOCTYPE html>")
    assert "<html" in html and "</html>" in html
    assert "<head>" in html and "</head>" in html
    assert "<body>" in html and "</body>" in html
    # Title always present
    assert "Global Shipping Intelligence Report" in html


def test_generate_html_report_populated_includes_all_sections():
    ports = [{"port_name": "Shanghai", "demand_score": 0.85, "region": "APAC"}]
    routes = [{"route_name": "Transpac", "score": 0.72, "current_rate": 2850.0}]
    insights = [
        FakeInsight(
            title="China exports up",
            score=0.82,
            category="MACRO",
            action="Prioritize",
        ),
    ]
    freight = {"transpac": {"current_rate": 2850.0, "change_30d": 4.5}}
    macro = {"UNRATE": {"name": "Unemployment", "value": 3.7, "trend": "Falling"}}
    stocks = {"ZIM": {"name": "ZIM Integrated", "price": 22.50, "signal": "Monitor"}}

    html = generate_html_report(ports, routes, insights, freight, macro, stocks)

    # Every section title should be present
    assert "Executive Summary" in html
    assert "Top Insights" in html
    assert "Port Rankings" in html
    assert "Route Rankings" in html
    assert "Freight Rate Tracker" in html
    assert "Macro Environment" in html
    assert "Shipping Equities" in html
    assert "Appendix" in html

    # Data values surfaced
    assert "Shanghai" in html
    assert "Transpac" in html
    assert "China exports up" in html
    assert "ZIM" in html
    assert "UNRATE" in html


def test_generate_html_report_sorts_insights_by_score_descending():
    insights = [
        FakeInsight(title="Low conviction",  score=0.30, insight_id="L"),
        FakeInsight(title="High conviction", score=0.85, insight_id="H"),
        FakeInsight(title="Mid conviction",  score=0.55, insight_id="M"),
    ]
    html = generate_html_report([], [], insights, None, None, None)
    # Top-3 exec summary should hit high before low
    pos_high = html.find("High conviction")
    pos_low  = html.find("Low conviction")
    assert pos_high < pos_low
    assert pos_high > 0


def test_generate_html_report_includes_date_range_when_provided():
    html = generate_html_report([], [], [], None, None, None, date_range="Q2 2026")
    assert "Q2 2026" in html


def test_generate_html_report_css_is_inlined_no_external_deps():
    html = generate_html_report([], [], [], None, None, None)
    # No CDN / external font hooks
    assert "cdn." not in html.lower()
    assert "googleapis" not in html.lower()
    assert "<link " not in html  # no external <link rel="stylesheet">
    assert "<style>" in html      # inline style block present


# ─── get_report_as_bytes ─────────────────────────────────────────────────────

def test_get_report_as_bytes_round_trips_utf8():
    html = "<html><body>café résumé</body></html>"
    out = get_report_as_bytes(html)
    assert isinstance(out, bytes)
    assert out.decode("utf-8") == html


def test_get_report_as_bytes_on_full_report():
    html = generate_html_report([], [], [], None, None, None)
    raw = get_report_as_bytes(html)
    assert isinstance(raw, bytes)
    assert raw.startswith(b"<!DOCTYPE html>")
