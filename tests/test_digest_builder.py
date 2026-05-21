"""Tests for utils.digest_builder — DailyDigest synthesis + renderers.

Covered branches
================
- _safe_float: float pass-through, int coercion, None → default, string-junk → default
- _safe_str: None → default, scalar → str(), preserves existing strings
- _compute_sentiment:
    * empty everything → (0.0, "NEUTRAL")
    * bullish insights only → score > 0, label "BULLISH"
    * bearish insights only → score < 0, label "BEARISH"
    * mid-band score → "MIXED" or "NEUTRAL" by threshold
    * BDI clamping (>>10% capped to ±1)
    * PMI > 50 contributes positively; PMI below contributes negative
- _pick_top_opportunities:
    * filters non-CONVERGENCE / non-ROUTE
    * sorts by score desc
    * top-n cap (default 3)
    * empty insights → []
    * payload keys present (title/score/action/rationale/category/routes/ports)
- _extract_key_risks:
    * always returns exactly 3
    * Caution / Avoid insights surface first
    * low-GDP risk appears when gdp_growth_pct < 1.5
    * high-volatility risk appears when volatility_index > 30
    * generic fallbacks fill remaining slots
- _build_executive_summary:
    * always 3 paragraphs separated by "\\n\\n"
    * mentions sentiment label, top route, top port
    * BDI direction string ("up"/"down"/"flat") reflects bdi_change_pct sign
    * PMI < 50 → "below" wording; PMI > 50 → "above" wording
- _build_port_highlights:
    * accepts both "port" and "name"/"locode" aliases
    * caps at 3
    * empty → []
    * "score" alias works for demand_score
- _build_freight_rate_moves:
    * sorts by abs(change_pct) desc
    * direction up/down/flat
    * default currency USD
- _build_stock_movers:
    * sorts by abs(change_pct) desc
    * direction labels
    * ticker/symbol alias
- _assess_data_quality: FULL (6/6), PARTIAL (3–5), DEGRADED (<3)
- _build_headline:
    * BULLISH + 2+ convergence → multi-signal headline
    * BEARISH → caution headline
    * MIXED → cross-currents headline
    * NEUTRAL → steady headline
- build_digest (integration):
    * happy path returns populated DailyDigest with non-empty fields
    * empty inputs → DailyDigest with sentinel/empty containers and "DEGRADED"
    * macro_snapshot only surfaces recognised keys
    * date and generated_at are ISO-shaped strings
- render_as_json: round-trips into dict with all 13 fields
- render_as_markdown: contains date, sentiment, headline; respects 1500-char cap
- render_as_html: contains digest.headline; respects sentiment color choice
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from utils import digest_builder as db
from utils.digest_builder import (
    DailyDigest,
    _assess_data_quality,
    _build_executive_summary,
    _build_freight_rate_moves,
    _build_headline,
    _build_port_highlights,
    _build_stock_movers,
    _compute_sentiment,
    _extract_key_risks,
    _pick_top_opportunities,
    _safe_float,
    _safe_str,
    build_digest,
    render_as_html,
    render_as_json,
    render_as_markdown,
)


# ─── Fake Insight (duck-typed against engine.insight.Insight) ──────────────

@dataclass
class _FakeInsight:
    title: str = "Default Title"
    category: str = "ROUTE"            # ROUTE | CONVERGENCE | PORT_DEMAND | MACRO | ...
    score: float = 0.50                 # 0..1
    detail: str = "Default detail."
    action: str = "Monitor"             # Prioritize | Monitor | Watch | Caution | Avoid
    routes_involved: list[str] = field(default_factory=list)
    ports_involved: list[str] = field(default_factory=list)


def _bullish_ins(score: float = 0.85, category: str = "CONVERGENCE") -> _FakeInsight:
    return _FakeInsight(
        title="Bullish convergence on TPEB",
        category=category,
        score=score,
        detail="Demand at LA-LGB hub up sharply with rate momentum supportive across container lanes.",
        action="Prioritize",
        routes_involved=["transpacific_eb"],
        ports_involved=["USLAX"],
    )


def _bearish_ins(score: float = 0.18, action: str = "Caution") -> _FakeInsight:
    return _FakeInsight(
        title="Weakening Asia-Europe lane",
        category="ROUTE",
        score=score,
        detail="Front-haul rates rolling over and PMI deceleration in EU manufacturing signals demand drag.",
        action=action,
        routes_involved=["asia_europe"],
        ports_involved=["DEHAM"],
    )


# ─── _safe_float / _safe_str helpers ───────────────────────────────────────

def test_safe_float_passes_floats() -> None:
    assert _safe_float(1.25) == 1.25


def test_safe_float_coerces_int_and_string() -> None:
    assert _safe_float(7) == 7.0
    assert _safe_float("3.14") == pytest.approx(3.14)


def test_safe_float_falls_back_on_none() -> None:
    assert _safe_float(None, default=-1.0) == -1.0


def test_safe_float_falls_back_on_garbage_string() -> None:
    assert _safe_float("not-a-number", default=99.0) == 99.0


def test_safe_str_none_returns_default() -> None:
    assert _safe_str(None) == "N/A"
    assert _safe_str(None, default="") == ""


def test_safe_str_coerces_non_string() -> None:
    assert _safe_str(42) == "42"
    assert _safe_str("already a string") == "already a string"


# ─── _compute_sentiment ────────────────────────────────────────────────────

def test_compute_sentiment_no_components_returns_neutral() -> None:
    score, label = _compute_sentiment([], {}, {})
    assert score == 0.0
    assert label == "NEUTRAL"


def test_compute_sentiment_strong_bullish_insights() -> None:
    insights = [_bullish_ins(score=0.95) for _ in range(3)]
    score, label = _compute_sentiment(insights, {}, {})
    assert score > 0.35
    assert label == "BULLISH"


def test_compute_sentiment_strong_bearish_insights() -> None:
    insights = [_FakeInsight(score=0.05) for _ in range(3)]
    score, label = _compute_sentiment(insights, {}, {})
    assert score < -0.35
    assert label == "BEARISH"


def test_compute_sentiment_score_is_clamped_to_unit_interval() -> None:
    # BDI change of +500% would explode without clamp; expect ≤1
    insights = [_bullish_ins(score=1.0)]
    freight = {"bdi_change_pct": 500.0}
    score, _label = _compute_sentiment(insights, freight, {})
    assert -1.0 <= score <= 1.0


def test_compute_sentiment_pmi_above_50_contributes_positive() -> None:
    score_high, _ = _compute_sentiment([], {}, {"global_pmi": 56.0})
    score_low, _ = _compute_sentiment([], {}, {"global_pmi": 42.0})
    assert score_high > score_low


def test_compute_sentiment_mixed_label_in_middle_band() -> None:
    # Construct components that average to ~0.20 (>0.10, <0.35) → MIXED
    insights = [_FakeInsight(score=0.60)]
    _score, label = _compute_sentiment(insights, {}, {})
    assert label == "MIXED"


# ─── _pick_top_opportunities ───────────────────────────────────────────────

def test_pick_top_opportunities_filters_eligible_categories() -> None:
    insights = [
        _FakeInsight(title="port-only", category="PORT_DEMAND", score=0.99),
        _FakeInsight(title="macro-only", category="MACRO", score=0.99),
        _bullish_ins(score=0.6, category="ROUTE"),
        _bullish_ins(score=0.7, category="CONVERGENCE"),
    ]
    opps = _pick_top_opportunities(insights)
    titles = [o["title"] for o in opps]
    assert "port-only" not in titles
    assert "macro-only" not in titles
    assert len(opps) == 2


def test_pick_top_opportunities_sorts_by_score_descending() -> None:
    insights = [
        _FakeInsight(title="low", category="ROUTE", score=0.20),
        _FakeInsight(title="mid", category="ROUTE", score=0.55),
        _FakeInsight(title="high", category="ROUTE", score=0.90),
    ]
    opps = _pick_top_opportunities(insights)
    assert [o["title"] for o in opps] == ["high", "mid", "low"]


def test_pick_top_opportunities_caps_at_n() -> None:
    insights = [_FakeInsight(category="ROUTE", score=0.8) for _ in range(10)]
    opps = _pick_top_opportunities(insights, n=3)
    assert len(opps) == 3


def test_pick_top_opportunities_empty_input() -> None:
    assert _pick_top_opportunities([]) == []


def test_pick_top_opportunities_payload_keys() -> None:
    opps = _pick_top_opportunities([_bullish_ins()])
    assert opps  # non-empty
    payload = opps[0]
    for key in ("title", "score", "action", "rationale", "category", "routes", "ports"):
        assert key in payload


# ─── _extract_key_risks ────────────────────────────────────────────────────

def test_extract_key_risks_always_returns_three() -> None:
    risks = _extract_key_risks([], {}, {})
    assert len(risks) == 3


def test_extract_key_risks_includes_caution_insight_title() -> None:
    ins = _bearish_ins(score=0.15, action="Caution")
    risks = _extract_key_risks([ins], {}, {})
    assert any("Weakening Asia-Europe" in r for r in risks)


def test_extract_key_risks_low_gdp_surfaces_macro_risk() -> None:
    risks = _extract_key_risks([], {"gdp_growth_pct": 0.8}, {})
    assert any("GDP" in r for r in risks)


def test_extract_key_risks_high_volatility_surfaces_volatility_risk() -> None:
    risks = _extract_key_risks([], {}, {"volatility_index": 45.0})
    assert any("volatility" in r.lower() for r in risks)


def test_extract_key_risks_truncates_long_caution_detail() -> None:
    long_detail = "X" * 500
    ins = _FakeInsight(
        title="Long bearish",
        category="CONVERGENCE",
        score=0.05,
        action="Avoid",
        detail=long_detail,
    )
    risks = _extract_key_risks([ins], {}, {})
    assert risks[0].endswith("...")


# ─── _build_executive_summary ──────────────────────────────────────────────

def test_executive_summary_three_paragraphs() -> None:
    summary = _build_executive_summary(
        insights=[],
        port_results=[],
        route_results=[],
        freight_data={},
        macro_data={},
        stock_data=[],
        sentiment_label="NEUTRAL",
        sentiment_score=0.0,
    )
    paras = summary.split("\n\n")
    assert len(paras) == 3


def test_executive_summary_mentions_sentiment_label_lowercased() -> None:
    summary = _build_executive_summary(
        [], [], [], {}, {}, [], "BULLISH", 0.55,
    )
    assert "bullish" in summary


def test_executive_summary_uses_top_port_and_route() -> None:
    summary = _build_executive_summary(
        insights=[],
        port_results=[{"port": "USLAX"}, {"port": "OTHER"}],
        route_results=[{"route": "TPEB"}, {"route": "BACKHAUL"}],
        freight_data={},
        macro_data={},
        stock_data=[],
        sentiment_label="NEUTRAL",
        sentiment_score=0.0,
    )
    assert "USLAX" in summary
    assert "TPEB" in summary


def test_executive_summary_bdi_direction_reflects_change_sign() -> None:
    up_summary = _build_executive_summary(
        [], [], [], {"bdi": 1500.0, "bdi_change_pct": 2.5}, {}, [],
        "BULLISH", 0.5,
    )
    down_summary = _build_executive_summary(
        [], [], [], {"bdi": 1500.0, "bdi_change_pct": -3.1}, {}, [],
        "BEARISH", -0.5,
    )
    assert " up " in up_summary
    assert " down " in down_summary


def test_executive_summary_pmi_above_50_says_above() -> None:
    above = _build_executive_summary(
        [], [], [], {}, {"global_pmi": 54.0}, [], "BULLISH", 0.4,
    )
    below = _build_executive_summary(
        [], [], [], {}, {"global_pmi": 46.0}, [], "MIXED", 0.1,
    )
    assert "above" in above.lower()
    assert "below" in below.lower()


# ─── _build_port_highlights ────────────────────────────────────────────────

def test_build_port_highlights_empty() -> None:
    assert _build_port_highlights([]) == []


def test_build_port_highlights_caps_at_three() -> None:
    ports = [{"port": f"P{i}", "demand_score": 0.5} for i in range(7)]
    highlights = _build_port_highlights(ports)
    assert len(highlights) == 3


def test_build_port_highlights_uses_port_or_name_alias() -> None:
    out = _build_port_highlights([{"name": "Singapore", "score": 0.81}])
    assert out[0]["port"] == "Singapore"
    assert out[0]["demand_score"] == pytest.approx(0.81)


def test_build_port_highlights_falls_back_through_alias_chain() -> None:
    out = _build_port_highlights([{"locode": "USLAX"}])
    assert out[0]["port"] == "USLAX"


# ─── _build_freight_rate_moves ─────────────────────────────────────────────

def test_freight_rate_moves_sorted_by_abs_change() -> None:
    rm = {
        "rate_moves": [
            {"route": "small", "change_pct": 0.5},
            {"route": "big_down", "change_pct": -8.0},
            {"route": "big_up", "change_pct": 6.0},
        ]
    }
    out = _build_freight_rate_moves(rm)
    assert out[0]["route"] == "big_down"
    assert out[1]["route"] == "big_up"
    assert out[2]["route"] == "small"


def test_freight_rate_moves_direction_labels() -> None:
    rm = {"rate_moves": [
        {"route": "up", "change_pct": 1.0},
        {"route": "down", "change_pct": -1.0},
        {"route": "flat", "change_pct": 0.0},
    ]}
    out = _build_freight_rate_moves(rm)
    by_route = {o["route"]: o["direction"] for o in out}
    assert by_route["up"] == "up"
    assert by_route["down"] == "down"
    assert by_route["flat"] == "flat"


def test_freight_rate_moves_default_currency_usd() -> None:
    out = _build_freight_rate_moves({"rate_moves": [{"route": "x", "change_pct": 1.0}]})
    assert out[0]["currency"] == "USD"


def test_freight_rate_moves_empty() -> None:
    assert _build_freight_rate_moves({}) == []


# ─── _build_stock_movers ───────────────────────────────────────────────────

def test_stock_movers_sorted_by_abs_change() -> None:
    stocks = [
        {"ticker": "AAA", "change_pct": 0.2},
        {"ticker": "BBB", "change_pct": -5.0},
        {"ticker": "CCC", "change_pct": 3.0},
    ]
    out = _build_stock_movers(stocks)
    assert [m["ticker"] for m in out] == ["BBB", "CCC", "AAA"]


def test_stock_movers_symbol_alias_works() -> None:
    out = _build_stock_movers([{"symbol": "ZIM", "close": 12.5, "daily_change_pct": 1.2}])
    assert out[0]["ticker"] == "ZIM"
    assert out[0]["price"] == 12.5
    assert out[0]["direction"] == "up"


def test_stock_movers_empty() -> None:
    assert _build_stock_movers([]) == []


# ─── _assess_data_quality ──────────────────────────────────────────────────

def test_data_quality_full_when_all_present() -> None:
    assert _assess_data_quality([1], [1], [1], {"k": 1}, {"k": 1}, [1]) == "FULL"


def test_data_quality_partial_when_some_present() -> None:
    assert _assess_data_quality([1], [1], [1], {}, {}, []) == "PARTIAL"


def test_data_quality_degraded_when_few_present() -> None:
    assert _assess_data_quality([], [], [], {}, {}, []) == "DEGRADED"
    assert _assess_data_quality([1], [], [], {}, {}, []) == "DEGRADED"


# ─── _build_headline ───────────────────────────────────────────────────────

def test_headline_bullish_multi_signal() -> None:
    insights = [_bullish_ins(category="CONVERGENCE") for _ in range(2)]
    head = _build_headline("BULLISH", insights, {})
    assert "convergence" in head.lower()


def test_headline_bullish_single_signal_mentions_bdi_when_positive() -> None:
    head = _build_headline("BULLISH", [], {"bdi_change_pct": 4.2})
    assert "BDI" in head or "bdi" in head.lower()


def test_headline_bearish_mentions_softening() -> None:
    head = _build_headline("BEARISH", [], {})
    assert "softening" in head.lower() or "caution" in head.lower()


def test_headline_mixed_says_cross_currents() -> None:
    head = _build_headline("MIXED", [], {})
    assert "cross" in head.lower()


def test_headline_neutral_says_steady() -> None:
    head = _build_headline("NEUTRAL", [], {})
    assert "steady" in head.lower()


# ─── build_digest (integration) ────────────────────────────────────────────

def _happy_inputs() -> dict:
    return {
        "port_results": [
            {"port": "USLAX", "demand_score": 0.91, "trend": "rising"},
            {"port": "SGSIN", "demand_score": 0.84, "trend": "rising"},
        ],
        "route_results": [
            {"route": "transpacific_eb", "score": 0.88},
            {"route": "asia_europe", "score": 0.62},
        ],
        "insights": [
            _bullish_ins(score=0.88, category="CONVERGENCE"),
            _bullish_ins(score=0.74, category="ROUTE"),
        ],
        "freight_data": {
            "bdi": 1820.0,
            "bdi_change_pct": 3.4,
            "volatility_index": 18.0,
            "rate_moves": [
                {"route": "transpacific_eb", "rate": 2400.0, "change_pct": 5.2},
                {"route": "asia_europe",     "rate": 1750.0, "change_pct": -1.8},
            ],
        },
        "macro_data": {
            "global_pmi": 52.5,
            "gdp_growth_pct": 2.4,
            "cpi": 3.1,
            "fed_rate": 4.5,
            "irrelevant_key": "ignored",
        },
        "stock_data": [
            {"ticker": "ZIM",  "price": 14.50, "change_pct":  2.1, "name": "ZIM"},
            {"ticker": "MAERSK","price": 1850.0,"change_pct": -0.5, "name": "Maersk"},
        ],
    }


def test_build_digest_happy_path_returns_populated_digest() -> None:
    digest = build_digest(**_happy_inputs())
    assert isinstance(digest, DailyDigest)
    assert digest.headline                       # non-empty
    assert digest.market_sentiment in {"BULLISH", "MIXED", "NEUTRAL", "BEARISH"}
    assert -1.0 <= digest.sentiment_score <= 1.0
    assert len(digest.top_opportunities) >= 1
    assert len(digest.key_risks) == 3
    assert len(digest.port_highlights) == 2
    assert digest.data_quality == "FULL"


def test_build_digest_empty_inputs_yields_degraded() -> None:
    digest = build_digest(
        port_results=[], route_results=[], insights=[],
        freight_data={}, macro_data={}, stock_data=[],
    )
    assert digest.market_sentiment == "NEUTRAL"
    assert digest.sentiment_score == 0.0
    assert digest.top_opportunities == []
    assert digest.port_highlights == []
    assert digest.freight_rate_moves == []
    assert digest.stock_movers == []
    assert digest.macro_snapshot == {}
    assert digest.data_quality == "DEGRADED"
    assert len(digest.key_risks) == 3   # filled by generic fallbacks


def test_build_digest_macro_snapshot_filters_to_known_keys() -> None:
    digest = build_digest(
        port_results=[], route_results=[], insights=[],
        freight_data={},
        macro_data={
            "global_pmi": 51.0,
            "cpi": 3.2,
            "irrelevant_key": "should_be_dropped",
            "another_random": 42,
        },
        stock_data=[],
    )
    assert "global_pmi" in digest.macro_snapshot
    assert "cpi" in digest.macro_snapshot
    assert "irrelevant_key" not in digest.macro_snapshot
    assert "another_random" not in digest.macro_snapshot


def test_build_digest_date_is_iso_yyyy_mm_dd() -> None:
    digest = build_digest([], [], [], {}, {}, [])
    assert len(digest.date) == 10
    assert digest.date[4] == "-" and digest.date[7] == "-"
    # generated_at is ISO-8601 (contains the date prefix)
    assert digest.generated_at.startswith(digest.date)


def test_build_digest_freight_rate_moves_are_sorted() -> None:
    inputs = _happy_inputs()
    digest = build_digest(**inputs)
    abs_changes = [abs(m["change_pct"]) for m in digest.freight_rate_moves]
    assert abs_changes == sorted(abs_changes, reverse=True)


# ─── Renderers ─────────────────────────────────────────────────────────────

def test_render_as_json_round_trips_into_dict_with_all_fields() -> None:
    digest = build_digest(**_happy_inputs())
    s = render_as_json(digest)
    payload = json.loads(s)
    expected_keys = {
        "date", "headline", "market_sentiment", "sentiment_score",
        "executive_summary", "top_opportunities", "key_risks",
        "port_highlights", "freight_rate_moves", "macro_snapshot",
        "stock_movers", "data_quality", "generated_at",
    }
    assert expected_keys.issubset(payload.keys())
    assert payload["headline"] == digest.headline
    assert payload["sentiment_score"] == digest.sentiment_score


def test_render_as_markdown_includes_key_data_points() -> None:
    digest = build_digest(**_happy_inputs())
    md = render_as_markdown(digest)
    assert digest.date in md
    assert digest.headline in md
    assert digest.market_sentiment in md
    assert "Top Opportunities" in md
    assert "Key Risks" in md


def test_render_as_markdown_respects_1500_char_cap() -> None:
    # Cram in big inputs so the body would naturally exceed 1500 chars
    inputs = _happy_inputs()
    inputs["insights"] = [_bullish_ins(score=0.9, category="ROUTE") for _ in range(20)]
    inputs["freight_data"]["rate_moves"] = [
        {"route": f"lane_{i}", "rate": 2000.0 + i, "change_pct": 1.0 + i}
        for i in range(20)
    ]
    digest = build_digest(**inputs)
    md = render_as_markdown(digest)
    assert len(md) <= 1500


def test_render_as_html_contains_headline_and_sentiment() -> None:
    digest = build_digest(**_happy_inputs())
    html = render_as_html(digest)
    assert digest.headline in html
    assert digest.market_sentiment in html
    assert html.startswith("<!DOCTYPE html>")
    # Pick up the data-quality tag too
    assert "Data Quality" in html


def test_render_as_html_falls_back_when_sentiment_unknown() -> None:
    # Force a sentiment outside the colour map → defaults applied, no exception
    digest = build_digest(**_happy_inputs())
    digest.market_sentiment = "UNRECOGNISED"
    html = render_as_html(digest)
    assert "UNRECOGNISED" in html
