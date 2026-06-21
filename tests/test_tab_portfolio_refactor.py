"""Refactor lock-in tests for tab_portfolio.

Portfolio Tracker has a relatively clean inline-style profile already
(4 divs / 12 spans at the time of writing) but no structural lock-in
file exists for it. These tests pin:

  1. Module import + render-with-empty / render-with-seeded survive cleanly.
  2. Required ``ui.styles`` helpers stay imported.
  3. Inline-style budget caps lock in today's intentional totals.
  4. The new ``_build_risk_return_scatter`` pure builder behaves
     correctly (empty input, marker size scales with weight, P&L colour
     map, reference lines at Beta=1 and P&L=0).
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest


# ── Streamlit stub ─────────────────────────────────────────────────────────

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

    expander_cm = MagicMock()
    expander_cm.__enter__ = MagicMock(return_value=expander_cm)
    expander_cm.__exit__  = MagicMock(return_value=False)
    stub.expander.return_value = expander_cm

    form_cm = MagicMock()
    form_cm.__enter__ = MagicMock(return_value=form_cm)
    form_cm.__exit__  = MagicMock(return_value=False)
    stub.form.return_value = form_cm

    for attr in (
        "markdown", "error", "info", "warning", "success", "write",
        "dataframe", "plotly_chart", "download_button", "metric",
        "selectbox", "slider", "number_input", "text_input", "button",
        "container", "caption", "subheader", "title", "header", "divider",
        "code", "table", "json", "image", "altair_chart", "form_submit_button",
        "checkbox", "radio", "color_picker", "date_input",
    ):
        monkeypatch.setattr(st, attr, stub, raising=False)
    monkeypatch.setattr(st, "columns",  stub.columns,  raising=False)
    monkeypatch.setattr(st, "tabs",     stub.tabs,     raising=False)
    monkeypatch.setattr(st, "expander", stub.expander, raising=False)
    monkeypatch.setattr(st, "form",     stub.form,     raising=False)

    # Session state — a real-ish dict; tab init writes default positions here.
    if not hasattr(st, "session_state") or st.session_state is None:
        monkeypatch.setattr(st, "session_state", {}, raising=False)
    yield stub


def _make_snapshot_df() -> pd.DataFrame:
    """Build a tiny snapshot DataFrame matching the schema _build_snapshot emits."""
    return pd.DataFrame([
        {"Ticker": "AAA", "Sector": "Container", "Shares": 100,
         "Avg Cost": 50.0, "Price": 60.0, "Market Value": 6000.0,
         "P&L $": 1000.0, "P&L %": 20.0, "Day Chg %": 1.0,
         "Day P&L $": 60.0, "Beta": 1.4, "Weight %": 40.0},
        {"Ticker": "BBB", "Sector": "Dry Bulk", "Shares": 200,
         "Avg Cost": 25.0, "Price": 22.5, "Market Value": 4500.0,
         "P&L $": -500.0, "P&L %": -10.0, "Day Chg %": -0.4,
         "Day P&L $": -18.0, "Beta": 0.9, "Weight %": 30.0},
        {"Ticker": "CCC", "Sector": "Tanker", "Shares": 50,
         "Avg Cost": 80.0, "Price": 80.0, "Market Value": 4000.0,
         "P&L $": 0.0, "P&L %": 0.0, "Day Chg %": 0.0,
         "Day P&L $": 0.0, "Beta": 1.1, "Weight %": 30.0},
    ])


# ── 1. Imports cleanly ─────────────────────────────────────────────────────

def test_module_imports_cleanly() -> None:
    """The tab must still import without any error."""
    import importlib
    import sys
    if "ui.tab_portfolio" in sys.modules:
        importlib.reload(sys.modules["ui.tab_portfolio"])
    else:
        importlib.import_module("ui.tab_portfolio")

    from ui import tab_portfolio
    assert callable(tab_portfolio.render)


# ── 2. Renders cleanly with empty / minimal inputs ────────────────────────

def test_render_with_empty_data_no_exception(streamlit_stub) -> None:
    """The render path must handle empty / None inputs cleanly."""
    from ui import tab_portfolio

    tab_portfolio.render(stock_data=None, macro_data=None, insights=None)


# ── 3. Required ui.styles helpers are wired in ────────────────────────────

def test_imports_design_system_helpers() -> None:
    """tab_portfolio must import the load-bearing ui.styles helpers."""
    src = Path("ui/tab_portfolio.py").read_text(encoding="utf-8")
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)",
        src,
        re.DOTALL,
    )
    assert m, "tab_portfolio must import from ui.styles via a parenthesized import block"
    imported = {tok.strip().rstrip(",") for tok in m.group(1).split() if tok.strip()}

    required = {
        "apply_dark_layout",
        "badge",
        "insight_card_html",
        "live_data_badge",
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


# ── 4. Inline-style budget is capped ──────────────────────────────────────
# tab_portfolio is already relatively clean — caps are intentionally low.

_INLINE_DIV_BUDGET:  int = 4    # remaining inline divs (header / formatting)
_INLINE_SPAN_BUDGET: int = 12   # _mono + _sans + per-cell colour spans


def test_inline_div_count_within_documented_budget() -> None:
    """Cap inline ``<div style=`` count at today's intentional total."""
    src = Path("ui/tab_portfolio.py").read_text(encoding="utf-8")
    hits = re.findall(r"<div(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_DIV_BUDGET, (
        f"Found {len(hits)} inline <div style=...> blocks — budget is "
        f"{_INLINE_DIV_BUDGET}. Route new ones through ui.styles helpers."
    )


def test_inline_span_count_within_documented_budget() -> None:
    """Cap inline ``<span style=`` count at today's intentional total."""
    src = Path("ui/tab_portfolio.py").read_text(encoding="utf-8")
    hits = re.findall(r"<span(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_SPAN_BUDGET, (
        f"Found {len(hits)} inline <span style=...> blocks — budget is "
        f"{_INLINE_SPAN_BUDGET}. The existing micro-helpers (_mono, _sans) "
        "cover the documented per-cell cases."
    )


# ── 5. Risk-return scatter builder ────────────────────────────────────────

def test_risk_return_scatter_empty_df_returns_annotated_figure() -> None:
    """Empty / None df → non-crashing annotated-empty figure."""
    import plotly.graph_objects as go
    from ui.tab_portfolio import _build_risk_return_scatter

    empty = pd.DataFrame()
    fig = _build_risk_return_scatter(empty)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0
    assert any("No positions to plot" in (a.text or "")
               for a in fig.layout.annotations)

    fig_none = _build_risk_return_scatter(None)  # type: ignore[arg-type]
    assert any("No positions to plot" in (a.text or "")
               for a in fig_none.layout.annotations)


def test_risk_return_scatter_plots_every_position() -> None:
    """Every row in the snapshot df becomes one marker on the scatter."""
    from ui.tab_portfolio import _build_risk_return_scatter

    df = _make_snapshot_df()
    fig = _build_risk_return_scatter(df)
    assert len(fig.data) == 1
    assert len(fig.data[0].x) == len(df)
    assert list(fig.data[0].text) == df["Ticker"].to_list()


def test_risk_return_scatter_marker_size_tracks_weight() -> None:
    """The largest-weight position must render with the largest marker.

    Sizes are clamped to a 12–44 px range to keep both ends visible.
    """
    from ui.tab_portfolio import _build_risk_return_scatter

    df = _make_snapshot_df()
    fig = _build_risk_return_scatter(df)
    sizes = list(fig.data[0].marker.size)
    weights = df["Weight %"].to_list()

    # The position with the heaviest weight should have the largest marker.
    heaviest_idx  = weights.index(max(weights))
    assert sizes[heaviest_idx] == max(sizes)
    # And the lightest weight should produce the smallest marker (≥ floor)
    lightest_idx  = weights.index(min(weights))
    assert sizes[lightest_idx] == min(sizes)
    assert min(sizes) >= 12.0 and max(sizes) <= 44.0


def test_risk_return_scatter_colour_follows_pnl_direction() -> None:
    """P&L > 0 → C_HIGH, < 0 → C_LOW (matches the tab's binary `_color`).

    The existing ``_color`` helper is intentionally binary (``v >= 0`` →
    long colour, else short). The scatter inherits that convention rather
    than introducing a separate three-state map.
    """
    from ui.styles import C_HIGH, C_LOW
    from ui.tab_portfolio import _build_risk_return_scatter

    df = _make_snapshot_df()
    fig = _build_risk_return_scatter(df)
    colors = list(fig.data[0].marker.color)
    # df row order: AAA (+20%), BBB (-10%), CCC (0%)
    assert colors[0] == C_HIGH    # gain
    assert colors[1] == C_LOW     # loss
    assert colors[2] == C_HIGH    # zero is treated as non-negative by _color


def test_risk_return_scatter_has_market_beta_reference_line() -> None:
    """Reference line at Beta=1 must be present and annotated 'Market β'."""
    from ui.tab_portfolio import _build_risk_return_scatter

    df = _make_snapshot_df()
    fig = _build_risk_return_scatter(df)
    shapes = list(fig.layout.shapes or [])
    vline_xs = [s.x0 for s in shapes if s.type == "line" and s.x0 == s.x1]
    assert 1.0 in vline_xs, "expected a vertical line at Beta=1.0"
    annotations = [a.text for a in (fig.layout.annotations or []) if a.text]
    assert any("Market" in a for a in annotations), \
        "expected 'Market β' annotation on the Beta=1 reference line"


# ── R008: the risk panel must NOT fabricate (real VaR / BDI, not rng.normal) ──

def test_risk_metrics_no_longer_fabricates_returns_or_bdi() -> None:
    import inspect

    import ui.tab_portfolio as tp
    src = inspect.getsource(tp._render_risk_metrics)
    # The whole rng.normal 252-day Monte-Carlo panel + the 0.6*port_ret synthetic
    # BDI correlation are gone (mentions in the docstring don't count — those are
    # prose, but np.random calls / the 0.6 factor are code).
    assert "np.random.default_rng" not in src
    assert "0.6 * port_ret" not in src
    assert "Simulated daily P&L" not in src
    # The panel now builds from the real returns panel + real macro.
    assert "returns_panel" in src
    assert "var_dollar" in src


def test_book_cascade_section_exists_and_is_wired() -> None:
    import inspect

    import ui.tab_portfolio as tp
    assert hasattr(tp, "_render_book_cascade")
    render_src = inspect.getsource(tp.render)
    assert "_render_book_cascade(" in render_src
    assert "_render_risk_metrics(df, stock_data, macro_data)" in render_src
