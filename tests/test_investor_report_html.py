"""Tests for utils.investor_report_html — WSJ-styled HTML investor report builder.

The module renders an InvestorReport-shaped object into a fully self-contained
HTML document. It is duck-typed: every helper uses _safe_attr() to read either
dict keys or object attributes and falls back to defaults when fields are
missing. These tests exercise the pure helpers and section builders directly,
using lightweight stand-in dataclasses that expose only the attributes each
function actually reads.

Covered surface
===============
Safe accessors / coercion
-------------------------
- _safe_float: numeric pass-through, string coercion, None / garbage → default
- _safe_str: None → default, blank → default, scalar → str() with strip
- _safe_attr: dict-key lookup, attribute lookup, falls through to default,
  prefers first non-None across multiple candidate keys
- _safe_list: passes through lists, coerces non-lists → []
- _safe_dict: passes through dicts, coerces non-dicts → {}

Formatters / color helpers
--------------------------
- _fmt_pct: sign prefix on positives, decimal precision honored
- _fmt_price: $-prefix and thresholded comma formatting at $1K and $10K
- _fmt_num: comma grouping at decimal precision
- _pct_color: > +0.5 → green, < -0.5 → red, else neutral
- _pct_cell: emits a <td> with arrow + sign + pct, color set by _pct_color
- _change_span: emits a <span> with arrow + signed pct
- _hex_rgba: parses 6-digit hex to rgba(r,g,b,alpha) string
- _badge: renders a span containing the badge text, uppercased only at the
  CSS layer (text passed through unchanged)

Small component builders
------------------------
- _topbar: contains date + firm string
- _nav: emits all 8 anchor links (#s1 .. #s8)
- _section_head: emits zero-padded section number + title (+ optional sub)
- _kpi: emits label + value (+ optional sub) and applies color when given
- _score_bar: clamps score to [0, 1] before width%-rendering
- _sent_stacked_bar: zero-total returns a "No sentiment data" paragraph;
  populated returns positive/negative legend with bullish + bearish counts

CSS bundle
----------
- _build_css: returns a non-empty string covering key class selectors used by
  the rendered body (ir-section, ir-kpi, ir-table, ir-cover, ir-footer)

Section builders (each consumes a slice of the report object)
-------------------------------------------------------------
- _cover: emits cover-metric values for sentiment score, BDI, WTI, signals,
  and supply-chain stress; honors data_quality pill label
- _section_executive_summary: emits section #01, composite-score block, the
  four sentiment components, trending-topic pills, and a fallback paragraph
  when narrative text is missing
- _section_alpha_signals: emits KPI counts, long/short top lists, full signal
  rows; falls back to "No signals" when the list is empty
- _section_freight_rates: emits KPI block + per-route rows; "No freight data"
  fallback when routes is empty; momentum colors
- _section_macroeconomic: emits stress card with interpretation text matched
  to the LOW/MODERATE/HIGH/CRITICAL bucket
- _section_equities: derives BUY/SELL/HOLD per ticker from signal direction;
  default ticker universe applied when stocks.tickers is empty
- _section_market_intel: emits insight rows, news feed entries, port rows
- _section_risk: synthesizes risk factor table from macro inputs; renders
  three scenarios (Bull / Base / Bear)
- _section_recommendations: emits rank-numbered recommendation cards plus
  outlook block

Footer + top-level orchestration
--------------------------------
- _footer: emits source list + disclaimer fallback when ai.disclaimer is empty
- render_investor_report_html: full happy-path render with all 8 sections;
  minimal-input render still produces a well-formed document; ISO-date
  parsing on generated_at

Skipped (intentionally)
=======================
- Exact CSS strings / inline styles (cosmetic, brittle)
- Exact tag attributes / indentation
- Streamlit / plotly integration (not imported by this module)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from utils import investor_report_html as irh
from utils.investor_report_html import (
    C_NEG,
    C_NEUT,
    C_POS,
    _badge,
    _build_css,
    _change_span,
    _cover,
    _fmt_num,
    _fmt_pct,
    _fmt_price,
    _footer,
    _hex_rgba,
    _kpi,
    _nav,
    _pct_cell,
    _pct_color,
    _safe_attr,
    _safe_dict,
    _safe_float,
    _safe_list,
    _safe_str,
    _score_bar,
    _section_alpha_signals,
    _section_equities,
    _section_executive_summary,
    _section_freight_rates,
    _section_head,
    _section_macroeconomic,
    _section_market_intel,
    _section_recommendations,
    _section_risk,
    _sent_stacked_bar,
    _topbar,
    render_investor_report_html,
)


# ─── Stand-in dataclasses (duck-typed inputs) ──────────────────────────────


@dataclass
class _Signal:
    ticker: str = "ZIM"
    signal_type: str = "MOMENTUM"
    direction: str = "LONG"
    conviction: str = "HIGH"
    strength: float = 0.7
    rationale: str = "Rates rising and demand robust."


@dataclass
class _Route:
    route_id: str = "TPEB"
    label: str = "Trans-Pacific Eastbound"
    rate: float = 2400.0
    change_30d: float = 150.0
    change_pct: float = 6.7
    trend: str = "UP"


@dataclass
class _Insight:
    title: str = "Asia export demand strong"
    score: float = 0.78
    action: str = "BUY"
    category: str = "DEMAND"


@dataclass
class _NewsItem:
    headline: str = "Container rates surge on Asia-EU lane"
    source: str = "Reuters"
    sentiment_score: float = 0.42
    published_at: str = "2026-05-20T14:32:00Z"


@dataclass
class _Port:
    port_name: str = "Singapore"
    region: str = "APAC"
    demand_score: float = 0.81


@dataclass
class _Rec:
    rank: int = 1
    title: str = "Overweight container liners"
    action: str = "BUY"
    ticker: str = "ZIM"
    conviction: str = "HIGH"
    rationale: str = "Multi-quarter rate strength expected."
    horizon: str = "30d"


@dataclass
class _Sentiment:
    overall_score: float = 0.42
    overall_label: str = "BULLISH"
    news_score: float = 0.30
    freight_score: float = 0.55
    macro_score: float = 0.10
    alpha_score: float = 0.65
    bullish_count: int = 12
    bearish_count: int = 4
    neutral_count: int = 8
    top_keywords: List[str] = field(default_factory=lambda: ["congestion", "rates", "demand"])
    trending_topics: List[Dict] = field(
        default_factory=lambda: [{"topic": "Red Sea", "count": 9}, {"topic": "Panama", "count": 5}]
    )


@dataclass
class _Macro:
    bdi: float = 1820.0
    bdi_change_30d_pct: float = 8.4
    wti: float = 78.5
    wti_change_30d_pct: float = -2.1
    treasury_10y: float = 4.25
    dxy_proxy: float = 7.1234
    pmi_proxy: float = 51.0
    supply_chain_stress: str = "MODERATE"


@dataclass
class _Alpha:
    signals: List[_Signal] = field(default_factory=lambda: [_Signal(), _Signal(ticker="MATX", direction="SHORT", conviction="MEDIUM")])
    top_long: List[_Signal] = field(default_factory=lambda: [_Signal()])
    top_short: List[_Signal] = field(default_factory=lambda: [_Signal(ticker="MATX", direction="SHORT", conviction="MEDIUM")])
    signal_count_by_type: Dict[str, int] = field(default_factory=lambda: {"MOMENTUM": 2})
    signal_count_by_conviction: Dict[str, int] = field(default_factory=lambda: {"HIGH": 1, "MEDIUM": 1, "LOW": 0})


@dataclass
class _Freight:
    routes: List[_Route] = field(default_factory=lambda: [_Route(), _Route(route_id="ASIAEU", change_pct=-3.2, trend="DOWN")])
    avg_change_30d_pct: float = 1.8
    biggest_mover: Dict[str, Any] = field(default_factory=lambda: {"route_id": "TPEB", "change_pct": 6.7, "rate": 2400.0})
    momentum_label: str = "Accelerating"
    fbx_composite: float = 1825.0


@dataclass
class _Stocks:
    tickers: List[str] = field(default_factory=lambda: ["ZIM", "MATX"])
    prices: Dict[str, float] = field(default_factory=lambda: {"ZIM": 18.5, "MATX": 142.0})
    changes_30d: Dict[str, float] = field(default_factory=lambda: {"ZIM": 4.5, "MATX": -1.2})
    signals_by_ticker: Dict[str, List[_Signal]] = field(
        default_factory=lambda: {"ZIM": [_Signal()], "MATX": [_Signal(ticker="MATX", direction="SHORT", conviction="MEDIUM")]}
    )
    top_pick: str = "ZIM"
    top_pick_rationale: str = "Best operating leverage in the container universe."


@dataclass
class _Market:
    top_insights: List[_Insight] = field(default_factory=lambda: [_Insight()])
    top_ports: List[_Port] = field(default_factory=lambda: [_Port()])
    top_routes: List[Dict] = field(default_factory=lambda: [{"route_id": "TPEB"}])
    risk_level: str = "MODERATE"
    active_opportunities: int = 6
    high_conviction_count: int = 3


@dataclass
class _AI:
    executive_summary: str = "Markets are buoyant.\n\nFreight rates show momentum."
    sentiment_narrative: str = "Sentiment supportive."
    opportunity_narrative: str = "Multiple long opportunities."
    risk_narrative: str = "Two-sided risk remains."
    outlook_30d: str = "We see continued strength."
    top_recommendations: List[_Rec] = field(default_factory=lambda: [_Rec(), _Rec(rank=2, ticker="MATX", action="HOLD", title="Stay neutral on Matson")])
    disclaimer: str = ""


@dataclass
class _Report:
    report_date: str = "2026-05-20"
    generated_at: str = "2026-05-20T15:00:00Z"
    data_quality: str = "FULL"
    sentiment: _Sentiment = field(default_factory=_Sentiment)
    macro: _Macro = field(default_factory=_Macro)
    alpha: _Alpha = field(default_factory=_Alpha)
    freight: _Freight = field(default_factory=_Freight)
    stocks: _Stocks = field(default_factory=_Stocks)
    market: _Market = field(default_factory=_Market)
    ai: _AI = field(default_factory=_AI)
    news_items: List[_NewsItem] = field(default_factory=lambda: [_NewsItem()])


@dataclass
class _Empty:
    """Report-shape with the bare minimum to render — empty children."""
    report_date: str = ""
    generated_at: str = ""
    data_quality: str = ""
    sentiment: Any = None
    macro: Any = None
    alpha: Any = None
    freight: Any = None
    stocks: Any = None
    market: Any = None
    ai: Any = None
    news_items: List = field(default_factory=list)


def _empty_report() -> _Report:
    """Empty-everything report — children present but with no data."""
    return _Report(
        sentiment=_Sentiment(
            overall_score=0.0,
            overall_label="NEUTRAL",
            news_score=0.0,
            freight_score=0.0,
            macro_score=0.0,
            alpha_score=0.0,
            bullish_count=0,
            bearish_count=0,
            neutral_count=0,
            top_keywords=[],
            trending_topics=[],
        ),
        macro=_Macro(
            bdi=0.0,
            bdi_change_30d_pct=0.0,
            wti=0.0,
            wti_change_30d_pct=0.0,
            treasury_10y=0.0,
            dxy_proxy=0.0,
            pmi_proxy=0.0,
            supply_chain_stress="MODERATE",
        ),
        alpha=_Alpha(signals=[], top_long=[], top_short=[], signal_count_by_type={}, signal_count_by_conviction={}),
        freight=_Freight(routes=[], biggest_mover={}, fbx_composite=0.0),
        stocks=_Stocks(tickers=[], prices={}, changes_30d={}, signals_by_ticker={}, top_pick="", top_pick_rationale=""),
        market=_Market(top_insights=[], top_ports=[], top_routes=[], active_opportunities=0, high_conviction_count=0),
        ai=_AI(
            executive_summary="",
            sentiment_narrative="",
            opportunity_narrative="",
            risk_narrative="",
            outlook_30d="",
            top_recommendations=[],
            disclaimer="",
        ),
        news_items=[],
    )


# ─── Safe accessors / coercion ─────────────────────────────────────────────


class TestSafeFloat:
    def test_passthrough(self):
        assert _safe_float(1.5) == 1.5

    def test_string_coercion(self):
        assert _safe_float("3.14") == 3.14

    def test_int_to_float(self):
        assert _safe_float(7) == 7.0

    def test_none_default(self):
        assert _safe_float(None) == 0.0

    def test_explicit_default(self):
        assert _safe_float("garbage", default=-1.0) == -1.0

    def test_object_default(self):
        assert _safe_float(object()) == 0.0


class TestSafeStr:
    def test_none_default(self):
        assert _safe_str(None) == "—"

    def test_blank_default(self):
        assert _safe_str("   ") == "—"

    def test_explicit_default(self):
        assert _safe_str(None, default="N/A") == "N/A"

    def test_int_coerced(self):
        assert _safe_str(42) == "42"

    def test_passthrough_with_strip(self):
        assert _safe_str("  hello  ") == "hello"


class TestSafeAttr:
    def test_dict_lookup(self):
        d = {"a": 1, "b": 2}
        assert _safe_attr(d, "a") == 1

    def test_object_lookup(self):
        @dataclass
        class _O:
            x: int = 9
        assert _safe_attr(_O(), "x") == 9

    def test_fallback_to_next_key(self):
        d = {"description": "fallback-hit"}
        assert _safe_attr(d, "title", "description") == "fallback-hit"

    def test_missing_returns_default(self):
        assert _safe_attr({}, "missing", default="x") == "x"

    def test_none_value_skipped(self):
        d = {"a": None, "b": "value"}
        assert _safe_attr(d, "a", "b") == "value"


class TestSafeListDict:
    def test_safe_list_passthrough(self):
        assert _safe_list([1, 2, 3]) == [1, 2, 3]

    def test_safe_list_non_list(self):
        assert _safe_list("oops") == []
        assert _safe_list(None) == []
        assert _safe_list({"k": "v"}) == []

    def test_safe_dict_passthrough(self):
        assert _safe_dict({"a": 1}) == {"a": 1}

    def test_safe_dict_non_dict(self):
        assert _safe_dict([]) == {}
        assert _safe_dict(None) == {}


# ─── Formatters / color helpers ────────────────────────────────────────────


class TestFormatters:
    def test_fmt_pct_positive_has_sign(self):
        assert _fmt_pct(2.5) == "+2.5%"

    def test_fmt_pct_negative_no_extra_sign(self):
        assert _fmt_pct(-2.5) == "-2.5%"

    def test_fmt_pct_zero(self):
        # zero is not > 0, so no + sign
        assert _fmt_pct(0.0) == "0.0%"

    def test_fmt_pct_decimals(self):
        assert _fmt_pct(3.14159, decimals=3) == "+3.142%"

    def test_fmt_price_small(self):
        assert _fmt_price(42.5) == "$42.50"

    def test_fmt_price_thousands(self):
        assert _fmt_price(2400.0) == "$2,400.0"

    def test_fmt_price_ten_thousands(self):
        assert _fmt_price(12500.0) == "$12,500"

    def test_fmt_num_default(self):
        assert _fmt_num(1234.5678) == "1,234.57"

    def test_fmt_num_zero_decimals(self):
        assert _fmt_num(1234.56, decimals=0) == "1,235"


class TestColorHelpers:
    def test_pct_color_positive(self):
        assert _pct_color(1.0) == C_POS

    def test_pct_color_negative(self):
        assert _pct_color(-1.0) == C_NEG

    def test_pct_color_neutral_band(self):
        assert _pct_color(0.0) == C_NEUT
        assert _pct_color(0.4) == C_NEUT
        assert _pct_color(-0.4) == C_NEUT

    def test_pct_cell_positive(self):
        html = _pct_cell(5.0)
        assert html.startswith("<td")
        assert "+5.0%" in html
        # up-arrow entity
        assert "&#9650;" in html
        assert C_POS in html

    def test_pct_cell_negative(self):
        html = _pct_cell(-5.0)
        assert "-5.0%" in html
        assert "&#9660;" in html
        assert C_NEG in html

    def test_change_span(self):
        html = _change_span(3.2)
        assert html.startswith("<span")
        assert "+3.2%" in html

    def test_hex_rgba_known_color(self):
        # navy hex C_NAVY = "#0f285a"
        assert _hex_rgba("#0f285a", 0.5) == "rgba(15,40,90,0.5)"

    def test_hex_rgba_no_hash(self):
        assert _hex_rgba("ffffff", 1.0) == "rgba(255,255,255,1.0)"

    def test_badge_contains_text(self):
        html = _badge("BUY", C_POS)
        assert "BUY" in html
        assert html.startswith("<span")


# ─── Small component builders ─────────────────────────────────────────────


class TestSmallComponents:
    def test_topbar_contains_date(self):
        html = _topbar("2026-05-20")
        assert "2026-05-20" in html
        assert "ShipIntel Research" in html

    def test_nav_has_all_eight_sections(self):
        html = _nav()
        for i in range(1, 9):
            assert f"#s{i}" in html
        assert "Executive Summary" in html
        assert "Recommendations" in html

    def test_section_head_numbers_zero_padded(self):
        html = _section_head(3, "Freight Rates")
        assert "03" in html
        assert "Freight Rates" in html

    def test_section_head_with_subtitle(self):
        html = _section_head(7, "Risk", "30-day outlook")
        assert "Risk" in html
        assert "30-day outlook" in html

    def test_section_head_no_subtitle(self):
        html = _section_head(2, "Alpha")
        # the sub div is gated on truthiness; should not render an empty sub
        assert "ir-section-sub" not in html

    def test_kpi_renders_label_and_value(self):
        html = _kpi("Test Label", "42", "subtitle")
        assert "Test Label" in html
        assert "42" in html
        assert "subtitle" in html

    def test_kpi_omits_sub_when_empty(self):
        html = _kpi("Lbl", "9")
        assert "ir-kpi-sub" not in html

    def test_kpi_applies_color(self):
        html = _kpi("Lbl", "v", "", C_POS)
        assert C_POS in html

    def test_score_bar_clamps_high(self):
        html = _score_bar(2.5, C_POS)
        # 2.5 should clamp to 1.0 → 100.0%
        assert "100.0%" in html

    def test_score_bar_clamps_low(self):
        html = _score_bar(-0.4, C_POS)
        assert "0.0%" in html

    def test_score_bar_midpoint(self):
        html = _score_bar(0.5, C_POS)
        assert "50.0%" in html

    def test_sent_stacked_bar_zero_total(self):
        html = _sent_stacked_bar(0, 0, 0)
        assert "No sentiment data" in html

    def test_sent_stacked_bar_with_counts(self):
        html = _sent_stacked_bar(bullish=8, bearish=2, neutral=5)
        assert "Bullish 8" in html
        assert "Bearish 2" in html
        assert "Neutral 5" in html


# ─── CSS bundle ─────────────────────────────────────────────────────────────


class TestCSS:
    def test_css_non_empty(self):
        css = _build_css()
        assert len(css) > 1000

    def test_css_has_key_classes(self):
        css = _build_css()
        for cls in (".ir-section", ".ir-kpi", ".ir-table", ".ir-cover", ".ir-footer", ".ir-nav"):
            assert cls in css


# ─── Section builders ──────────────────────────────────────────────────────


class TestCover:
    def test_renders_sentiment_score(self):
        report = _Report()
        html = _cover(report)
        # overall_score=0.42 with + sign formatted to 2 decimals
        assert "+0.42" in html
        assert "BULLISH" in html

    def test_renders_bdi_value(self):
        report = _Report()
        html = _cover(report)
        assert "1,820" in html

    def test_renders_wti(self):
        report = _Report()
        html = _cover(report)
        # WTI is $78.50 (under 1000 threshold so 2 decimals)
        assert "$78.50" in html

    def test_supply_chain_stress_label(self):
        report = _Report()
        html = _cover(report)
        assert "MODERATE" in html

    def test_data_quality_label_translated(self):
        report = _Report(data_quality="FULL")
        html = _cover(report)
        assert "Full Data" in html

    def test_empty_report_renders_dashes(self):
        report = _empty_report()
        html = _cover(report)
        # BDI=0 → em-dash, WTI=0 → em-dash
        assert "—" in html


class TestExecutiveSummary:
    def test_renders_section_one(self):
        html = _section_executive_summary(_Report())
        assert 'id="s1"' in html
        assert "Executive Summary" in html

    def test_includes_component_scores(self):
        html = _section_executive_summary(_Report())
        for component in ("News Sentiment", "Freight Momentum", "Macro Environment", "Alpha Signals"):
            assert component in html

    def test_topic_pills_rendered(self):
        html = _section_executive_summary(_Report())
        # trending_topics fixture has "Red Sea"
        assert "Red Sea" in html

    def test_fallback_when_no_narrative(self):
        empty = _empty_report()
        html = _section_executive_summary(empty)
        assert "Narrative not available" in html

    def test_composite_score_block(self):
        html = _section_executive_summary(_Report())
        # 3-decimal display
        assert "+0.420" in html


class TestAlphaSignals:
    def test_renders_section_two(self):
        html = _section_alpha_signals(_Report())
        assert 'id="s2"' in html
        assert "Alpha Signals" in html

    def test_signal_rows_present(self):
        html = _section_alpha_signals(_Report())
        assert "ZIM" in html
        assert "MATX" in html

    def test_kpi_counts(self):
        html = _section_alpha_signals(_Report())
        # Two signals, one LONG, one SHORT, one HIGH, one MEDIUM
        assert "Total Signals" in html
        assert "High Conviction" in html
        assert "Long Bias" in html
        assert "Short Bias" in html

    def test_empty_signals_fallback(self):
        empty = _empty_report()
        html = _section_alpha_signals(empty)
        assert "No signals available" in html

    def test_long_rationale_truncated(self):
        long_rat = "x" * 200
        sig = _Signal(rationale=long_rat)
        report = _Report(alpha=_Alpha(signals=[sig], top_long=[sig], top_short=[]))
        html = _section_alpha_signals(report)
        # Should be cut at 140 chars with ellipsis
        assert "x" * 140 + "…" in html


class TestFreightRates:
    def test_renders_section_three(self):
        html = _section_freight_rates(_Report())
        assert 'id="s3"' in html
        assert "Freight Rates" in html

    def test_route_rows(self):
        html = _section_freight_rates(_Report())
        assert "TPEB" in html
        assert "ASIAEU" in html
        # rate $2,400.0 (thousands branch)
        assert "$2,400.0" in html

    def test_no_freight_fallback(self):
        html = _section_freight_rates(_empty_report())
        assert "No freight data available" in html

    def test_momentum_label_visible(self):
        html = _section_freight_rates(_Report())
        assert "Accelerating" in html


class TestMacroeconomic:
    def test_renders_section_four(self):
        html = _section_macroeconomic(_Report())
        assert 'id="s4"' in html
        assert "Macroeconomic Environment" in html

    def test_renders_bdi_kpi(self):
        html = _section_macroeconomic(_Report())
        assert "1,820" in html

    def test_stress_card_moderate(self):
        html = _section_macroeconomic(_Report())
        # The interpretation text for MODERATE
        assert "Some supply chain friction evident" in html

    def test_stress_card_critical(self):
        report = _Report(macro=_Macro(
            bdi=500.0, wti=120.0, treasury_10y=5.0,
            supply_chain_stress="CRITICAL"
        ))
        html = _section_macroeconomic(report)
        assert "Critical supply chain conditions" in html

    def test_supply_chain_stress_unknown_falls_back(self):
        report = _Report(macro=_Macro(supply_chain_stress="UNKNOWN_BUCKET"))
        html = _section_macroeconomic(report)
        assert "could not be determined" in html


class TestEquities:
    def test_renders_section_five(self):
        html = _section_equities(_Report())
        assert 'id="s5"' in html
        assert "Equity Coverage" in html

    def test_renders_top_pick(self):
        html = _section_equities(_Report())
        assert "ZIM" in html
        # rationale block emitted only when non-default
        assert "Best operating leverage" in html

    def test_buy_rating_from_long_signal(self):
        html = _section_equities(_Report())
        # ZIM has a LONG signal → BUY rating
        assert "BUY" in html

    def test_sell_rating_from_short_signal(self):
        html = _section_equities(_Report())
        # MATX has a SHORT signal → SELL rating
        assert "SELL" in html

    def test_hold_rating_for_no_signals(self):
        stocks = _Stocks(
            tickers=["DAC"],
            prices={"DAC": 50.0},
            changes_30d={"DAC": 0.0},
            signals_by_ticker={"DAC": []},
            top_pick="",
            top_pick_rationale="",
        )
        html = _section_equities(_Report(stocks=stocks))
        assert "HOLD" in html

    def test_default_tickers_when_empty(self):
        report = _empty_report()
        html = _section_equities(report)
        # default universe applied
        for default_tkr in ("ZIM", "MATX", "SBLK", "DAC", "CMRE"):
            assert default_tkr in html


class TestMarketIntel:
    def test_renders_section_six(self):
        html = _section_market_intel(_Report())
        assert 'id="s6"' in html
        assert "Market Intelligence" in html

    def test_insight_row(self):
        html = _section_market_intel(_Report())
        assert "Asia export demand strong" in html

    def test_news_item(self):
        html = _section_market_intel(_Report())
        assert "Container rates surge" in html
        assert "Reuters" in html

    def test_port_row(self):
        html = _section_market_intel(_Report())
        assert "Singapore" in html

    def test_empty_news_fallback(self):
        html = _section_market_intel(_empty_report())
        assert "No news items available" in html

    def test_empty_insights_fallback(self):
        html = _section_market_intel(_empty_report())
        assert "No insights available" in html


class TestRisk:
    def test_renders_section_seven(self):
        html = _section_risk(_Report())
        assert 'id="s7"' in html

    def test_synth_risk_factors_from_macro(self):
        html = _section_risk(_Report())
        # bdi=1820>1500 → LOW; wti=78.5 in 70-90 → MODERATE; tsy=4.25 in 3.5-4.5 → MODERATE
        assert "Baltic Dry Index Level" in html
        assert "Bunker Fuel" in html
        assert "Interest Rate Environment" in html

    def test_scenarios_present(self):
        html = _section_risk(_Report())
        for scenario in ("Bull Case", "Base Case", "Bear Case"):
            assert scenario in html

    def test_geopolitical_always_present(self):
        html = _section_risk(_Report())
        assert "Geopolitical Risk" in html

    def test_risk_narrative_renders_when_present(self):
        html = _section_risk(_Report())
        assert "Two-sided risk remains" in html


class TestRecommendations:
    def test_renders_section_eight(self):
        html = _section_recommendations(_Report())
        assert 'id="s8"' in html
        assert "Recommendations" in html

    def test_rec_title_and_action(self):
        html = _section_recommendations(_Report())
        assert "Overweight container liners" in html
        assert "Stay neutral on Matson" in html

    def test_rank_zero_padded(self):
        html = _section_recommendations(_Report())
        assert "01" in html
        assert "02" in html

    def test_outlook_block(self):
        html = _section_recommendations(_Report())
        assert "30-Day Outlook" in html
        assert "continued strength" in html

    def test_empty_recs_fallback(self):
        html = _section_recommendations(_empty_report())
        assert "No recommendations available" in html


# ─── Footer + top-level orchestration ──────────────────────────────────────


class TestFooter:
    def test_disclaimer_fallback(self):
        report = _Report()
        # ai.disclaimer is "" in default fixture → falls back to canned text
        html = _footer(report)
        assert "ShipIntel Research" in html
        assert "not indicative of future results" in html

    def test_custom_disclaimer_used(self):
        report = _Report(ai=_AI(disclaimer="CUSTOM_DISCLAIMER_TEXT"))
        html = _footer(report)
        assert "CUSTOM_DISCLAIMER_TEXT" in html

    def test_data_sources_listed(self):
        html = _footer(_Report())
        assert "Baltic Exchange" in html
        assert "FRED" in html
        assert "Freightos" in html

    def test_generated_at_parsed(self):
        report = _Report(generated_at="2026-05-20T15:00:00Z")
        html = _footer(report)
        # The footer formats as %B %d, %Y at %H:%M UTC
        assert "May 20, 2026" in html

    def test_invalid_generated_at_falls_back(self):
        report = _Report(generated_at="not-a-date")
        html = _footer(report)
        # Falls back to raw value
        assert "not-a-date" in html


class TestRenderInvestorReportHtml:
    def test_returns_full_html_document(self):
        html = render_investor_report_html(_Report())
        assert html.startswith("<!DOCTYPE html>")
        assert html.rstrip().endswith("</html>")

    def test_all_eight_sections_present(self):
        html = render_investor_report_html(_Report())
        for i in range(1, 9):
            assert f'id="s{i}"' in html

    def test_includes_topbar_and_cover_and_footer(self):
        html = render_investor_report_html(_Report())
        assert "ir-topbar" in html
        assert "ir-cover" in html
        assert "ir-footer" in html

    def test_includes_inline_css(self):
        html = render_investor_report_html(_Report())
        assert "<style>" in html
        assert ".ir-section" in html

    def test_title_includes_generated_date(self):
        html = render_investor_report_html(_Report())
        # Title uses %B %d, %Y format from generated_at
        assert "May 20, 2026" in html

    def test_empty_report_renders_without_error(self):
        # All sections still emit a fragment, no exception, full document shape
        html = render_investor_report_html(_empty_report())
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html
        # Sentinel: section ids still present
        for i in range(1, 9):
            assert f'id="s{i}"' in html

    def test_invalid_generated_at_falls_back_to_report_date(self):
        report = _Report(generated_at="garbage", report_date="2026-05-20")
        html = render_investor_report_html(report)
        # gen_display falls back to report_date
        assert "2026-05-20" in html
