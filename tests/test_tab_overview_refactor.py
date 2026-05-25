"""Refactor lock-in tests for tab_overview.

The Overview tab is the landing page and is already comparatively clean
(9 divs / 6 spans at the time of writing). These tests pin the
structural contract, cap the inline-style budget, and exercise the new
``_build_signal_conviction_heatmap`` pure builder.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def streamlit_stub(monkeypatch):
    """Stub the `st.*` surface so the tab renders headlessly.

    `st.columns` is request-adaptive — see the assistant refactor file for
    background; same trick here so column-tuple unpacking works.
    """
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

    for attr in (
        "markdown", "error", "info", "warning", "success", "write",
        "dataframe", "plotly_chart", "download_button", "metric",
        "selectbox", "slider", "number_input", "button", "container",
        "caption", "subheader", "title", "header", "divider", "code",
        "table", "json", "image", "altair_chart", "expander",
    ):
        monkeypatch.setattr(st, attr, stub, raising=False)
    monkeypatch.setattr(st, "columns", stub.columns, raising=False)
    monkeypatch.setattr(st, "tabs",    stub.tabs,    raising=False)
    if not hasattr(st, "session_state") or st.session_state is None:
        monkeypatch.setattr(st, "session_state", {}, raising=False)
    yield stub


# ── 1. Imports cleanly ─────────────────────────────────────────────────────

def test_module_imports_cleanly() -> None:
    """The tab must still import without any error."""
    import importlib
    import sys
    if "ui.tab_overview" in sys.modules:
        importlib.reload(sys.modules["ui.tab_overview"])
    else:
        importlib.import_module("ui.tab_overview")

    from ui import tab_overview
    assert callable(tab_overview.render)


# ── 2. Render survives empty inputs ───────────────────────────────────────

def test_render_with_empty_data_no_exception(streamlit_stub) -> None:
    """All-None / empty inputs must flow through every guard cleanly."""
    from ui import tab_overview

    tab_overview.render(
        port_results=[], route_results=[], insights=[], alerts=[],
        freight_data=None, macro_data=None, stock_data=None,
    )


# ── 3. Required ui.styles helpers are wired in ────────────────────────────

def test_imports_design_system_helpers() -> None:
    """Pin the load-bearing ui.styles helper imports."""
    src = Path("ui/tab_overview.py").read_text(encoding="utf-8")
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)",
        src,
        re.DOTALL,
    )
    assert m, "tab_overview must import from ui.styles via a parenthesized import block"
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
        "status_badge",
        "wsj_market_table",
    }
    missing = required - imported
    assert not missing, (
        f"Required ui.styles helpers missing from import block: {sorted(missing)}"
    )


# ── 4. Inline-style budget caps ───────────────────────────────────────────

_INLINE_DIV_BUDGET:  int = 9    # cold-start, narrative wrappers, sparkline labels
_INLINE_SPAN_BUDGET: int = 8    # _mono + _sans + "As of" timestamps


def test_inline_div_count_within_documented_budget() -> None:
    """Cap inline ``<div style=`` count at today's intentional total."""
    src = Path("ui/tab_overview.py").read_text(encoding="utf-8")
    hits = re.findall(r"<div(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_DIV_BUDGET, (
        f"Found {len(hits)} inline <div style=...> blocks — budget is "
        f"{_INLINE_DIV_BUDGET}. Route new ones through ui.styles helpers."
    )


def test_inline_span_count_within_documented_budget() -> None:
    """Cap inline ``<span style=`` count at today's intentional total."""
    src = Path("ui/tab_overview.py").read_text(encoding="utf-8")
    hits = re.findall(r"<span(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_SPAN_BUDGET, (
        f"Found {len(hits)} inline <span style=...> blocks — budget is "
        f"{_INLINE_SPAN_BUDGET}. The existing micro-helpers (_mono, _sans) "
        "cover the documented per-cell cases."
    )


# ── 5. Signal conviction heatmap builder ──────────────────────────────────

def test_signal_conviction_heatmap_empty_returns_annotated_figure() -> None:
    """Empty grid → non-crashing annotated-empty figure."""
    import plotly.graph_objects as go
    from ui.tab_overview import _build_signal_conviction_heatmap

    fig = _build_signal_conviction_heatmap([], [], [])
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0
    assert any("No signal conviction" in (a.text or "")
               for a in fig.layout.annotations)


def test_signal_conviction_heatmap_mismatched_grid_falls_back() -> None:
    """Grid shape ≠ (len(corridors), len(commodities)) falls back to empty."""
    from ui.tab_overview import _build_signal_conviction_heatmap

    corridors  = ["A", "B"]
    commodities = ["X", "Y", "Z"]
    bad_grid    = [[0.5, 0.6]]  # 1 row × 2 cols — wrong both ways
    fig = _build_signal_conviction_heatmap(corridors, commodities, bad_grid)
    assert len(fig.data) == 0
    assert any("No signal conviction" in (a.text or "")
               for a in fig.layout.annotations)


def test_signal_conviction_heatmap_basic_shape() -> None:
    """A correctly-shaped grid produces one Heatmap trace with axes wired."""
    from ui.tab_overview import _build_signal_conviction_heatmap

    corridors   = ["Trans-Pacific", "Asia-Europe"]
    commodities = ["Dry Bulk", "Container", "Tanker"]
    grid = [
        [0.80, 0.55, 0.30],
        [0.45, 0.70, 0.20],
    ]
    fig = _build_signal_conviction_heatmap(corridors, commodities, grid)
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert trace.type == "heatmap"
    assert list(trace.x) == commodities
    assert list(trace.y) == corridors
    # z is a tuple-of-tuples in plotly's internal form
    flat_z = [val for row in trace.z for val in row]
    assert flat_z == [0.80, 0.55, 0.30, 0.45, 0.70, 0.20]


def test_signal_conviction_heatmap_clamps_color_range() -> None:
    """zmin/zmax must be pinned to 0 / 1 so the colour scale stays stable
    regardless of the actual score range in this snapshot."""
    from ui.tab_overview import _build_signal_conviction_heatmap

    fig = _build_signal_conviction_heatmap(
        ["A"], ["X"], [[0.4]],
    )
    assert fig.data[0].zmin == 0.0
    assert fig.data[0].zmax == 1.0


def test_signal_conviction_heatmap_cells_annotated_with_percent() -> None:
    """Cell text reads as integer percentages (e.g. '55%')."""
    from ui.tab_overview import _build_signal_conviction_heatmap

    fig = _build_signal_conviction_heatmap(
        ["A", "B"], ["X", "Y"],
        [[0.55, 0.10], [0.95, 0.50]],
    )
    flat_text = [t for row in fig.data[0].text for t in row]
    assert flat_text == ["55%", "10%", "95%", "50%"]
