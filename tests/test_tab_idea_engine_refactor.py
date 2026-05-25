"""Refactor lock-in tests for tab_idea_engine.

Phase-4 Signal-to-Trade Ideas dashboard. These tests pin the structural
contract, cap the inline-style budget at today's intentional totals,
and exercise the new ``_build_idea_conviction_bars`` pure builder that
sits between Hero and the Ranked Table.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def streamlit_stub(monkeypatch):
    """Request-adaptive Streamlit stub."""
    import streamlit as st

    def _make_col() -> MagicMock:
        col = MagicMock()
        col.__enter__ = MagicMock(return_value=col)
        col.__exit__  = MagicMock(return_value=False)
        return col

    def _columns_factory(spec, *_a, **_kw):
        if isinstance(spec, int):
            n = spec
        else:
            try:
                n = len(list(spec))
            except TypeError:
                n = 1
        return [_make_col() for _ in range(max(1, n))]

    stub = MagicMock()
    stub.columns.side_effect = _columns_factory
    stub.tabs.return_value = [_make_col() for _ in range(8)]

    expander_cm = MagicMock()
    expander_cm.__enter__ = MagicMock(return_value=expander_cm)
    expander_cm.__exit__  = MagicMock(return_value=False)
    stub.expander.return_value = expander_cm

    stub.text_input.return_value  = ""
    stub.selectbox.return_value   = ""
    stub.button.return_value      = False
    stub.checkbox.return_value    = False

    for attr in (
        "markdown", "error", "info", "warning", "success", "write",
        "dataframe", "plotly_chart", "download_button", "metric",
        "container", "caption", "subheader", "title", "header", "divider",
        "code", "table", "json", "image", "altair_chart", "slider",
        "number_input",
    ):
        monkeypatch.setattr(st, attr, stub, raising=False)
    for attr in (
        "columns", "tabs", "expander", "text_input", "selectbox",
        "button", "checkbox",
    ):
        monkeypatch.setattr(st, attr, getattr(stub, attr), raising=False)
    if not hasattr(st, "session_state") or st.session_state is None:
        monkeypatch.setattr(st, "session_state", {}, raising=False)
    yield stub


def _idea(*, ticker: str = "AAA", direction: str = "Bullish",
          conviction: float = 0.6, label: str = "Moderate"):
    """Build a minimal EquityIdea-like namespace for the bar-builder tests."""
    return SimpleNamespace(
        ticker=ticker,
        company_name=f"{ticker} Corp",
        direction=direction,
        conviction_score=conviction,
        conviction_label=label,
        thesis=f"{ticker} thesis",
    )


# ── 1. Imports cleanly ─────────────────────────────────────────────────────

def test_module_imports_cleanly() -> None:
    """The tab must still import without any error."""
    import importlib
    import sys
    if "ui.tab_idea_engine" in sys.modules:
        importlib.reload(sys.modules["ui.tab_idea_engine"])
    else:
        importlib.import_module("ui.tab_idea_engine")

    from ui import tab_idea_engine
    assert callable(tab_idea_engine.render)


# ── 2. Render survives empty inputs ───────────────────────────────────────

def test_render_with_empty_data_no_exception(streamlit_stub) -> None:
    """All-None / empty inputs must flow through every guard cleanly."""
    from ui import tab_idea_engine

    tab_idea_engine.render(
        port_results=None, route_results=None, insights=None,
        freight_data=None, macro_data=None, stock_data=None,
    )


# ── 3. Required ui.styles helpers are wired in ────────────────────────────

def test_imports_design_system_helpers() -> None:
    """Pin the load-bearing ui.styles helper imports."""
    src = Path("ui/tab_idea_engine.py").read_text(encoding="utf-8")
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)",
        src,
        re.DOTALL,
    )
    assert m, "tab_idea_engine must import from ui.styles via a parenthesized import block"
    imported = {tok.strip().rstrip(",") for tok in m.group(1).split() if tok.strip()}

    required = {
        "apply_dark_layout",
        "badge",
        "insight_card_html",
        "metric_card_row",
        "page_header",
        "section_divider",
        "section_header",
        "source_footer",
        "wsj_market_table",
    }
    missing = required - imported
    assert not missing, (
        f"Required ui.styles helpers missing from import block: {sorted(missing)}"
    )


# ── 4. Inline-style budget caps ───────────────────────────────────────────

_INLINE_DIV_BUDGET:  int = 12  # hero card + section labels
_INLINE_SPAN_BUDGET: int = 4   # _mono + _sans + scenario chip


def test_inline_div_count_within_documented_budget() -> None:
    """Cap inline ``<div style=`` count at today's intentional total."""
    src = Path("ui/tab_idea_engine.py").read_text(encoding="utf-8")
    hits = re.findall(r"<div(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_DIV_BUDGET, (
        f"Found {len(hits)} inline <div style=...> blocks — budget is "
        f"{_INLINE_DIV_BUDGET}. Route new ones through ui.styles helpers."
    )


def test_inline_span_count_within_documented_budget() -> None:
    """Cap inline ``<span style=`` count at today's intentional total."""
    src = Path("ui/tab_idea_engine.py").read_text(encoding="utf-8")
    hits = re.findall(r"<span(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_SPAN_BUDGET, (
        f"Found {len(hits)} inline <span style=...> blocks — budget is "
        f"{_INLINE_SPAN_BUDGET}. The existing micro-helpers (_mono, _sans) "
        "cover the documented per-cell cases."
    )


# ── 5. Conviction-bars builder ────────────────────────────────────────────

def test_conviction_bars_empty_returns_annotated_figure() -> None:
    """Empty / None ideas → annotated-empty figure."""
    import plotly.graph_objects as go
    from ui.tab_idea_engine import _build_idea_conviction_bars

    fig = _build_idea_conviction_bars([])
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0
    assert any("No ideas" in (a.text or "")
               for a in fig.layout.annotations)


def test_conviction_bars_orders_highest_at_top() -> None:
    """Plotly stacks categorical y-values bottom-up — builder reverses the
    sort so the highest-conviction idea lands at the TOP of the chart."""
    from ui.tab_idea_engine import _build_idea_conviction_bars

    ideas = [
        _idea(ticker="LOW",  conviction=0.20),
        _idea(ticker="HIGH", conviction=0.95),
        _idea(ticker="MID",  conviction=0.55),
    ]
    fig = _build_idea_conviction_bars(ideas)
    y = list(fig.data[0].y)
    # bottom-of-list = lowest conviction; top-of-list = highest conviction
    assert y[0]  == "LOW"
    assert y[-1] == "HIGH"


def test_conviction_bars_colour_follows_direction() -> None:
    """Bar colour comes from the tab's existing `_direction_color` helper
    (Bullish → C_HIGH, Bearish → C_LOW, Neutral → C_MOD)."""
    from ui.styles import C_HIGH, C_LOW, C_MOD
    from ui.tab_idea_engine import _build_idea_conviction_bars

    ideas = [
        _idea(ticker="A", direction="Bullish", conviction=0.7),
        _idea(ticker="B", direction="Bearish", conviction=0.5),
        _idea(ticker="C", direction="Neutral", conviction=0.3),
    ]
    fig = _build_idea_conviction_bars(ideas)
    by_ticker = dict(zip(list(fig.data[0].y), list(fig.data[0].marker.color)))
    assert by_ticker["A"] == C_HIGH
    assert by_ticker["B"] == C_LOW
    assert by_ticker["C"] == C_MOD


def test_conviction_bars_respects_limit() -> None:
    """Builder caps at ``limit`` rows even when more ideas are passed."""
    from ui.tab_idea_engine import _build_idea_conviction_bars

    ideas = [_idea(ticker=f"T{i}", conviction=i / 20) for i in range(20)]
    fig = _build_idea_conviction_bars(ideas, limit=5)
    assert len(list(fig.data[0].y)) == 5
    # Title surfaces the total + the shown count
    title_text = (fig.layout.title.text or "")
    assert "5 of 20" in title_text
