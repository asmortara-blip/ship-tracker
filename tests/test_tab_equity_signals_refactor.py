"""Refactor lock-in tests for tab_equity_signals.

The Equity Signals tab is the final stage of the Disruption Alpha cascade,
and it carries a deliberately rich set of hand-crafted micro-helpers
(``_eyebrow``, ``_mono``, ``_sans``, ``_contribution_cell``, ``_rank_chip``,
``_render_subhead``, ``_render_distribution_rail``, ``_render_tag_row``,
``_render_signal_list``) that do cell-level / component-level work without
a 1:1 mapping in ``ui.styles``. These tests pin two things:

  1. The contract: required ``ui.styles`` helpers are imported, the module
     imports cleanly, ``render(...)`` survives both empty and seeded inputs,
     and the conviction-vs-30d-move scatter builder behaves correctly.
  2. The budget: the count of inline ``<div style=...>`` / ``<span style=...>``
     blocks is capped at today's *intentional* total. Future edits cannot
     silently push more inline styling into the tab — they have to either
     route through ``ui.styles`` or explicitly raise the cap (which forces
     a docs / review step).

Companion to the broader UI smoke suite — this file is *structural*.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ── Streamlit stub (same shape as the equipment refactor file) ──────────────

@pytest.fixture
def streamlit_stub(monkeypatch):
    """Stub the `st.*` surface so the tab renders headlessly."""
    import streamlit as st

    stub = MagicMock()
    stub.columns.return_value = [MagicMock() for _ in range(8)]
    stub.tabs.return_value    = [MagicMock() for _ in range(8)]
    for m in stub.columns.return_value + stub.tabs.return_value:
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__  = MagicMock(return_value=False)

    # st.expander returns a context manager too
    expander_cm = MagicMock()
    expander_cm.__enter__ = MagicMock(return_value=expander_cm)
    expander_cm.__exit__  = MagicMock(return_value=False)
    stub.expander.return_value = expander_cm

    for attr in (
        "markdown", "error", "info", "warning", "success", "write",
        "dataframe", "plotly_chart", "download_button", "metric",
        "selectbox", "slider", "number_input", "button", "container",
        "caption", "subheader", "title", "header", "divider", "code",
        "table", "json", "image", "altair_chart",
    ):
        monkeypatch.setattr(st, attr, stub, raising=False)
    monkeypatch.setattr(st, "columns",  stub.columns,  raising=False)
    monkeypatch.setattr(st, "tabs",     stub.tabs,     raising=False)
    monkeypatch.setattr(st, "expander", stub.expander, raising=False)
    yield stub


def _make_idea(
    ticker: str = "AAA",
    direction: str = "Bullish",
    conviction: float = 0.62,
    label: str = "Moderate",
    price: float = 100.0,
    change_30d: float = 2.5,
    hops: int = 3,
):
    """Build a minimal EquityIdea suitable for the scatter builder tests."""
    from processing.disruption_cascade import CascadeLink, EquityIdea

    chain = [
        CascadeLink(
            route_id=f"R{i}",
            route_stress=0.5,
            hs_category="Electronics",
            cargo_share=0.2,
            commodity_signal="Bullish",
            contribution=0.1,
        )
        for i in range(hops)
    ]
    return EquityIdea(
        ticker=ticker,
        company_name=f"{ticker} Corp",
        direction=direction,
        conviction_score=conviction,
        conviction_label=label,
        thesis=f"{ticker} thesis",
        cascade_chain=chain,
        price=price,
        change_30d=change_30d,
    )


# ── 1. Imports cleanly ─────────────────────────────────────────────────────

def test_module_imports_cleanly() -> None:
    """The tab must still import without any error."""
    import importlib
    import sys
    if "ui.tab_equity_signals" in sys.modules:
        importlib.reload(sys.modules["ui.tab_equity_signals"])
    else:
        importlib.import_module("ui.tab_equity_signals")

    from ui import tab_equity_signals
    assert callable(tab_equity_signals.render)


# ── 2. Renders cleanly with empty data ─────────────────────────────────────

def test_render_with_empty_data_no_exception(streamlit_stub) -> None:
    """All-None inputs must flow through every guard cleanly."""
    from ui import tab_equity_signals

    tab_equity_signals.render(
        stock_data=None, freight_data=None, macro_data=None,
        port_results=None, route_results=None, insights=None,
    )


# ── 3. Renders cleanly with seeded data ────────────────────────────────────

def test_render_with_seeded_data_no_exception(streamlit_stub) -> None:
    """A modest synthetic payload must also pass through cleanly."""
    import pandas as pd
    from ui import tab_equity_signals

    dates = pd.date_range("2024-01-01", periods=60, freq="D")
    tab_equity_signals.render(
        stock_data={
            "AAPL": pd.DataFrame({"date": dates,
                                  "close": [150.0 + i * 0.2 for i in range(60)]}),
        },
        freight_data={"baltic_dry_index": 1500.0},
        macro_data={"china_pmi": 51.0},
        port_results=[], route_results=[], insights=[],
    )


# ── 4. Canonical design-system helpers are wired in ────────────────────────

def test_imports_design_system_helpers() -> None:
    """tab_equity_signals must import the load-bearing ui.styles helpers.

    These are the contract for the Disruption Alpha tabs' shared visual
    identity. If any are dropped during a future edit the tab will still
    import (Python won't notice the missing name in a parenthesized block
    until something tries to call it), but the visual contract would
    silently break. This test pins it.
    """
    src = Path("ui/tab_equity_signals.py").read_text(encoding="utf-8")
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)",
        src,
        re.DOTALL,
    )
    assert m, "tab_equity_signals must import from ui.styles via a parenthesized import block"
    imported = {tok.strip().rstrip(",") for tok in m.group(1).split() if tok.strip()}

    required = {
        "alert_banner",
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


# ── 5. Inline-style budget is capped at today's intentional total ─────────
# The tab carries a deliberate set of hand-crafted micro-helpers that don't
# have ui.styles equivalents. The counts below pin TODAY's totals — they
# are an upper bound, not a target. Future edits that route NEW inline
# styles through ui.styles instead can lower these numbers; new ad-hoc
# inline styles cannot raise them without an explicit change here.

_INLINE_DIV_BUDGET:  int = 15   # _render_empty_note + _render_distribution_rail +
                                # _render_tag_row + _render_signal_list +
                                # _render_subhead + _render_card_rank_line +
                                # the inter-card hairline separator
_INLINE_SPAN_BUDGET: int = 25   # _eyebrow + _mono + _sans + _contribution_cell
                                # (multi-span) + _rank_chip + per-row chip
                                # wrappers in tag_row / signal_list / etc.


def test_inline_div_count_within_documented_budget() -> None:
    """Cap inline ``<div style=`` count at the documented intentional total."""
    src = Path("ui/tab_equity_signals.py").read_text(encoding="utf-8")
    hits = re.findall(r"<div(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_DIV_BUDGET, (
        f"Found {len(hits)} inline <div style=...> blocks — budget is "
        f"{_INLINE_DIV_BUDGET}. Route the new ones through ui.styles "
        "(metric_card_row, gradient_card, section_divider, status_badge, "
        "insight_card_html) instead of bumping the cap."
    )


def test_inline_span_count_within_documented_budget() -> None:
    """Cap inline ``<span style=`` count at the documented intentional total."""
    src = Path("ui/tab_equity_signals.py").read_text(encoding="utf-8")
    hits = re.findall(r"<span(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_SPAN_BUDGET, (
        f"Found {len(hits)} inline <span style=...> blocks — budget is "
        f"{_INLINE_SPAN_BUDGET}. The existing micro-helpers (_eyebrow, _mono, "
        "_sans, _contribution_cell, _rank_chip) cover the documented "
        "cell-level cases; new ones must use those or extend ui.styles."
    )


# ── 6. Figure-builder: conviction × 30-day-move scatter ───────────────────

def test_conviction_scatter_empty_ideas_returns_annotated_figure() -> None:
    """Empty list → non-crashing annotated-empty figure."""
    import plotly.graph_objects as go
    from ui.tab_equity_signals import _build_conviction_scatter

    fig = _build_conviction_scatter([])
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0
    assert any("No priced ideas" in (a.text or "") for a in fig.layout.annotations)


def test_conviction_scatter_skips_unpriced_ideas() -> None:
    """Ideas with no price history must be omitted from the scatter."""
    from ui.tab_equity_signals import _build_conviction_scatter

    ideas = [
        _make_idea(ticker="AAA", price=100.0, change_30d=4.0),
        _make_idea(ticker="BBB", price=0.0,   change_30d=0.0),   # unpriced
        _make_idea(ticker="CCC", price=200.0, change_30d=-2.5),
    ]
    fig = _build_conviction_scatter(ideas)
    plotted_tickers: list[str] = []
    for trace in fig.data:
        if trace.text is not None:
            plotted_tickers.extend(list(trace.text))
    assert "AAA" in plotted_tickers
    assert "CCC" in plotted_tickers
    assert "BBB" not in plotted_tickers


def test_conviction_scatter_groups_by_direction() -> None:
    """Each non-empty direction must get its own trace, with the platform's
    Bullish→C_HIGH, Bearish→C_LOW, Neutral→C_TEXT2 colour map."""
    from ui.styles import C_HIGH, C_LOW, C_TEXT2
    from ui.tab_equity_signals import _build_conviction_scatter

    ideas = [
        _make_idea(ticker="A", direction="Bullish", price=100, change_30d=3),
        _make_idea(ticker="B", direction="Bearish", price=80,  change_30d=-2),
        _make_idea(ticker="C", direction="Neutral", price=50,  change_30d=0.5),
        _make_idea(ticker="D", direction="Bullish", price=120, change_30d=1),
    ]
    fig = _build_conviction_scatter(ideas)

    by_name = {trace.name: trace for trace in fig.data}
    assert set(by_name.keys()) == {"Bullish", "Bearish", "Neutral"}
    assert by_name["Bullish"].marker.color == C_HIGH
    assert by_name["Bearish"].marker.color == C_LOW
    assert by_name["Neutral"].marker.color == C_TEXT2

    # Two Bullish ideas → trace carries two points
    assert len(by_name["Bullish"].x) == 2


def test_conviction_scatter_marker_size_tracks_cascade_depth() -> None:
    """Marker size must increase with cascade depth, capped to a sane range."""
    from ui.tab_equity_signals import _build_conviction_scatter

    shallow = _make_idea(ticker="S", hops=0)   # 0 hops → minimum
    deep    = _make_idea(ticker="D", hops=50)  # 50 hops → saturates at cap
    fig = _build_conviction_scatter([shallow, deep])
    sizes = list(fig.data[0].marker.size)
    assert sizes[0] == 8       # 0 hops floors at 8
    assert sizes[1] == 24      # 50 hops saturates at 24
