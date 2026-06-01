"""Tests for processing.report_diff — InvestorReport snapshot comparison.

Uses stand-in dataclasses (not the real InvestorReport) so the module
stays decoupled from the engine internals.

Covers:
  - SignalDelta / RouteDelta / ReportDiff dataclass shapes
  - compute_report_diff: zero-diff sentinel paths (None inputs, missing
    attrs); sentiment_shift = curr - prev; risk_level_change formatting;
    new_signals / dropped_signals correct on a partial overlap; top
    score/rate deltas sorted by absolute delta; delta_pct guards
    against zero divisor
  - _signal_name / _signal_score / _route_name / _route_rate extractors
    work against dict input AND attribute input
  - format_diff_html: renders the curr/prev dates, sentiment shift,
    risk change; handles empty new/dropped/changes gracefully
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

from processing.report_diff import (
    ReportDiff,
    RouteDelta,
    SignalDelta,
    compute_report_diff,
    format_diff_html,
)


# ─── Stand-in InvestorReport shape ───────────────────────────────────────

@dataclass
class _Sentiment:
    overall_score: float = 0.0
    overall_label: str = "NEUTRAL"


@dataclass
class _Signal:
    title: str = ""
    score: float = 0.0


@dataclass
class _Alpha:
    signals: list = field(default_factory=list)


@dataclass
class _Market:
    risk_level: str = "LOW"


@dataclass
class _Freight:
    routes: list = field(default_factory=list)


@dataclass
class _Report:
    report_date: str = ""
    sentiment: _Sentiment = field(default_factory=_Sentiment)
    alpha: _Alpha = field(default_factory=_Alpha)
    market: _Market = field(default_factory=_Market)
    freight: _Freight = field(default_factory=_Freight)


def _mk_report(
    date: str,
    overall_score: float = 0.0,
    risk: str = "LOW",
    signals: list[tuple[str, float]] | None = None,
    routes: list[tuple[str, float]] | None = None,
) -> _Report:
    return _Report(
        report_date=date,
        sentiment=_Sentiment(overall_score=overall_score),
        alpha=_Alpha(signals=[_Signal(title=t, score=s) for t, s in (signals or [])]),
        market=_Market(risk_level=risk),
        freight=_Freight(routes=[{"route_id": r, "rate_usd_per_feu": rate} for r, rate in (routes or [])]),
    )


# ─── Dataclass shape ─────────────────────────────────────────────────────

def test_signal_delta_shape() -> None:
    d = SignalDelta(name="x", status="new", prev_score=0.0, curr_score=0.8, delta_score=0.8)
    assert d.status == "new"


def test_route_delta_shape() -> None:
    d = RouteDelta(name="r", status="unchanged", prev_rate_usd_per_feu=2000.0,
                   curr_rate_usd_per_feu=2200.0, delta_pct=10.0)
    assert d.delta_pct == 10.0


def test_report_diff_default_lists_are_independent() -> None:
    """field(default_factory) used — fresh list per instance."""
    a = ReportDiff(prev_date="x", curr_date="y", sentiment_shift=0.0, risk_level_change="unchanged")
    b = ReportDiff(prev_date="x", curr_date="y", sentiment_shift=0.0, risk_level_change="unchanged")
    a.new_signals.append("solo")
    assert b.new_signals == []


# ─── compute_report_diff: zero-diff paths ────────────────────────────────

def test_compute_report_diff_none_input_returns_zeroed_diff() -> None:
    d = compute_report_diff(None, None)
    assert d.sentiment_shift == 0.0
    assert d.risk_level_change == "unchanged"
    assert d.new_signals == []
    assert d.dropped_signals == []
    assert "No meaningful" in d.summary_narrative


def test_compute_report_diff_identical_reports_yields_unchanged() -> None:
    """Two identical reports → no signal/route diffs, no risk change."""
    rep = _mk_report("2026-05-21", overall_score=0.3, risk="MODERATE",
                     signals=[("ZIM LONG", 0.7)], routes=[("transpacific_eb", 2000.0)])
    d = compute_report_diff(rep, rep)
    assert d.sentiment_shift == 0.0
    assert d.risk_level_change == "unchanged"
    assert d.new_signals == []
    assert d.dropped_signals == []


# ─── Sentiment shift ─────────────────────────────────────────────────────

def test_compute_report_diff_sentiment_shift_positive() -> None:
    prev = _mk_report("yesterday", overall_score=0.1)
    curr = _mk_report("today", overall_score=0.4)
    d = compute_report_diff(prev, curr)
    assert d.sentiment_shift == pytest.approx(0.3)


def test_compute_report_diff_sentiment_shift_negative() -> None:
    prev = _mk_report("yesterday", overall_score=0.5)
    curr = _mk_report("today", overall_score=-0.1)
    d = compute_report_diff(prev, curr)
    assert d.sentiment_shift == pytest.approx(-0.6)


# ─── Risk level change ─────────────────────────────────────────────────

def test_compute_report_diff_risk_unchanged_when_same() -> None:
    prev = _mk_report("y", risk="MODERATE")
    curr = _mk_report("t", risk="MODERATE")
    d = compute_report_diff(prev, curr)
    assert d.risk_level_change == "unchanged"


def test_compute_report_diff_risk_change_formatted_with_arrow() -> None:
    prev = _mk_report("y", risk="LOW")
    curr = _mk_report("t", risk="HIGH")
    d = compute_report_diff(prev, curr)
    assert d.risk_level_change == "LOW -> HIGH"


# ─── Signal diffs ─────────────────────────────────────────────────────

def test_compute_report_diff_new_and_dropped_signals() -> None:
    prev = _mk_report("y", signals=[("A", 0.5), ("B", 0.4), ("C", 0.3)])
    curr = _mk_report("t", signals=[("B", 0.6), ("C", 0.3), ("D", 0.8)])
    d = compute_report_diff(prev, curr)
    assert d.new_signals == ["D"]
    assert d.dropped_signals == ["A"]


def test_compute_report_diff_top_signal_score_changes_sorted_by_abs_delta() -> None:
    """Signal B moved +0.20, signal C moved -0.40 → C should come first."""
    prev = _mk_report("y", signals=[("A", 0.5), ("B", 0.4), ("C", 0.6)])
    curr = _mk_report("t", signals=[("A", 0.5), ("B", 0.6), ("C", 0.2)])
    d = compute_report_diff(prev, curr)
    names_in_order = [s.name for s in d.top_signal_score_changes]
    # C moved 0.4 (biggest), B moved 0.2, A moved 0
    assert names_in_order[:2] == ["C", "B"]


def test_compute_report_diff_signal_status_labels_correct() -> None:
    prev = _mk_report("y", signals=[("kept", 0.5), ("gone", 0.4)])
    curr = _mk_report("t", signals=[("kept", 0.5), ("fresh", 0.8)])
    d = compute_report_diff(prev, curr)
    status_map = {s.name: s.status for s in d.top_signal_score_changes}
    assert status_map["fresh"] == "new"
    assert status_map["gone"] == "dropped"
    assert status_map["kept"] == "unchanged"


# ─── Route rate diffs ─────────────────────────────────────────────────

def test_compute_report_diff_route_delta_pct_computation() -> None:
    prev = _mk_report("y", routes=[("transpacific_eb", 2000.0)])
    curr = _mk_report("t", routes=[("transpacific_eb", 2200.0)])
    d = compute_report_diff(prev, curr)
    routes = {r.name: r for r in d.top_route_rate_changes}
    assert routes["transpacific_eb"].delta_pct == pytest.approx(10.0)


def test_compute_report_diff_route_zero_prev_rate_gives_zero_delta() -> None:
    """prev rate 0 → division-by-zero guarded; delta_pct = 0."""
    prev = _mk_report("y", routes=[("new_route", 0.0)])
    curr = _mk_report("t", routes=[("new_route", 1500.0)])
    d = compute_report_diff(prev, curr)
    routes = {r.name: r for r in d.top_route_rate_changes}
    assert routes["new_route"].delta_pct == 0.0
    assert routes["new_route"].curr_rate_usd_per_feu == 1500.0


def test_compute_report_diff_route_changes_sorted_by_abs_delta() -> None:
    prev = _mk_report("y", routes=[
        ("steady", 2000.0), ("big_mover", 2000.0), ("small_mover", 2000.0),
    ])
    curr = _mk_report("t", routes=[
        ("steady", 2000.0), ("big_mover", 1500.0), ("small_mover", 2050.0),
    ])
    d = compute_report_diff(prev, curr)
    names = [r.name for r in d.top_route_rate_changes]
    assert names[0] == "big_mover"  # 25% drop, biggest


# ─── Narrative ───────────────────────────────────────────────────────

def test_compute_report_diff_narrative_mentions_sentiment_when_meaningful() -> None:
    prev = _mk_report("y", overall_score=-0.3)
    curr = _mk_report("t", overall_score=0.4)
    d = compute_report_diff(prev, curr)
    assert "improved" in d.summary_narrative.lower()


def test_compute_report_diff_narrative_mentions_risk_change() -> None:
    prev = _mk_report("y", risk="LOW")
    curr = _mk_report("t", risk="CRITICAL")
    d = compute_report_diff(prev, curr)
    assert "LOW -> CRITICAL" in d.summary_narrative


def test_compute_report_diff_narrative_default_when_nothing_changed() -> None:
    rep = _mk_report("x", overall_score=0.1, risk="LOW")
    d = compute_report_diff(rep, rep)
    assert "No meaningful" in d.summary_narrative


# ─── format_diff_html ─────────────────────────────────────────────────

def test_format_diff_html_includes_dates_and_summary() -> None:
    prev = _mk_report("2026-05-20", overall_score=0.1, risk="LOW")
    curr = _mk_report("2026-05-21", overall_score=0.3, risk="MODERATE",
                     signals=[("Fresh signal", 0.8)],
                     routes=[("transpacific_eb", 2200.0)])
    d = compute_report_diff(prev, curr)
    html = format_diff_html(d)
    assert "2026-05-20" in html
    assert "2026-05-21" in html
    assert "LOW -> MODERATE" in html


def test_format_diff_html_handles_zero_diff() -> None:
    """An all-empty diff should still render a usable HTML snippet."""
    d = ReportDiff(prev_date="a", curr_date="b", sentiment_shift=0.0,
                   risk_level_change="unchanged")
    html = format_diff_html(d)
    assert isinstance(html, str)
    assert "No signal-level changes" in html or "No meaningful" in html


def test_format_diff_html_shows_top_signal_changes() -> None:
    prev = _mk_report("y", signals=[("ZIM LONG", 0.5)])
    curr = _mk_report("t", signals=[("ZIM LONG", 0.8)])
    d = compute_report_diff(prev, curr)
    html = format_diff_html(d)
    assert "ZIM LONG" in html


def test_format_diff_html_never_raises_on_garbage_input() -> None:
    """Defensive: a mangled ReportDiff doesn't crash format_diff_html."""
    d = ReportDiff(prev_date="", curr_date="", sentiment_shift=float("nan"),
                   risk_level_change="unchanged")
    html = format_diff_html(d)  # must not raise
    assert isinstance(html, str)


# ─── Extractor tolerance ─────────────────────────────────────────────

def test_compute_report_diff_accepts_dict_routes() -> None:
    """Routes can be dicts (already exercised by _mk_report) — verify
    explicitly the rate keys cycle through fallbacks."""
    @dataclass
    class _R:
        freight: object = field(default_factory=lambda: _Freight(routes=[
            {"route_id": "r1", "rate": 2000.0},  # 'rate' not 'rate_usd_per_feu'
        ]))
        sentiment: object = field(default_factory=_Sentiment)
        alpha: object = field(default_factory=_Alpha)
        market: object = field(default_factory=_Market)
        report_date: str = "x"

    @dataclass
    class _R2:
        freight: object = field(default_factory=lambda: _Freight(routes=[
            {"route_id": "r1", "rate": 2200.0},
        ]))
        sentiment: object = field(default_factory=_Sentiment)
        alpha: object = field(default_factory=_Alpha)
        market: object = field(default_factory=_Market)
        report_date: str = "y"

    d = compute_report_diff(_R(), _R2())
    routes = {r.name: r for r in d.top_route_rate_changes}
    assert routes["r1"].delta_pct == pytest.approx(10.0)


def test_compute_report_diff_accepts_signal_dicts() -> None:
    """Signal objects can be dict-shaped too — title + score keys."""
    @dataclass
    class _RR:
        alpha: object = field(default_factory=lambda: _Alpha(signals=[
            {"title": "X", "score": 0.5},
        ]))
        sentiment: object = field(default_factory=_Sentiment)
        market: object = field(default_factory=_Market)
        freight: object = field(default_factory=_Freight)
        report_date: str = "x"

    @dataclass
    class _RR2:
        alpha: object = field(default_factory=lambda: _Alpha(signals=[
            {"title": "X", "score": 0.9},
        ]))
        sentiment: object = field(default_factory=_Sentiment)
        market: object = field(default_factory=_Market)
        freight: object = field(default_factory=_Freight)
        report_date: str = "y"

    d = compute_report_diff(_RR(), _RR2())
    deltas = {s.name: s for s in d.top_signal_score_changes}
    assert deltas["X"].delta_score == pytest.approx(0.4)
