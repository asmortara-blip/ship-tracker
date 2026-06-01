"""Refactor lock-in tests for tab_briefing.

The Daily Briefing tab carries a small set of intentional inline-style
formatters (``_mono`` / ``_sans`` for cell content + a couple of section
labels) and a transparency panel of indicator chips. These tests pin the
structural contract, cap the inline-style budget, and exercise the new
``_build_forecast_quadrant_scatter`` pure builder.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

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

    for attr in (
        "markdown", "error", "info", "warning", "success", "write",
        "dataframe", "plotly_chart", "download_button", "metric",
        "selectbox", "slider", "number_input", "button", "container",
        "caption", "subheader", "title", "header", "divider", "code",
        "table", "json", "image", "altair_chart", "text_input",
        "text_area",
    ):
        monkeypatch.setattr(st, attr, stub, raising=False)
    monkeypatch.setattr(st, "columns",  stub.columns,  raising=False)
    monkeypatch.setattr(st, "tabs",     stub.tabs,     raising=False)
    monkeypatch.setattr(st, "expander", stub.expander, raising=False)
    if not hasattr(st, "session_state") or st.session_state is None:
        monkeypatch.setattr(st, "session_state", {}, raising=False)
    yield stub


def _fc(
    *,
    route_name: str = "Asia → Europe",
    route_id: str = "ASIA_EU",
    current: float = 0.40,
    forecast: float = 0.60,
    trend: str = "Worsening",
    rate_pct: float = 0.03,
):
    """Build a minimal forecast namespace matching the duck-typed shape
    `_build_forecast_quadrant_scatter` expects."""
    return SimpleNamespace(
        route_name=route_name,
        route_id=route_id,
        current_stress=current,
        stress_30d=forecast,
        trend=trend,
        rate_forecast_pct=rate_pct,
    )


# ── 1. Imports cleanly ─────────────────────────────────────────────────────

def test_module_imports_cleanly() -> None:
    """The tab must still import without any error."""
    import importlib
    import sys
    if "ui.tab_briefing" in sys.modules:
        importlib.reload(sys.modules["ui.tab_briefing"])
    else:
        importlib.import_module("ui.tab_briefing")

    from ui import tab_briefing
    assert callable(tab_briefing.render)


# ── 2. Render survives empty inputs ───────────────────────────────────────

def test_render_with_empty_data_no_exception(streamlit_stub) -> None:
    """All-None / empty inputs must flow through every guard cleanly."""
    from ui import tab_briefing

    tab_briefing.render(
        port_results=None, route_results=None, insights=None,
        freight_data=None, macro_data=None, stock_data=None,
    )


# ── 3. Required ui.styles helpers are wired in ────────────────────────────

def test_imports_design_system_helpers() -> None:
    """Pin the load-bearing ui.styles helper imports."""
    src = Path("ui/tab_briefing.py").read_text(encoding="utf-8")
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)",
        src,
        re.DOTALL,
    )
    assert m, "tab_briefing must import from ui.styles via a parenthesized import block"
    imported = {tok.strip().rstrip(",") for tok in m.group(1).split() if tok.strip()}

    required = {
        "apply_dark_layout",
        "badge",
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

_INLINE_DIV_BUDGET:  int = 12   # section labels + headline / body / sections grid
_INLINE_SPAN_BUDGET: int = 12   # _mono + _sans + indicator chips


def test_inline_div_count_within_documented_budget() -> None:
    """Cap inline ``<div style=`` count at today's intentional total."""
    src = Path("ui/tab_briefing.py").read_text(encoding="utf-8")
    hits = re.findall(r"<div(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_DIV_BUDGET, (
        f"Found {len(hits)} inline <div style=...> blocks — budget is "
        f"{_INLINE_DIV_BUDGET}. Route new ones through ui.styles helpers."
    )


def test_inline_span_count_within_documented_budget() -> None:
    """Cap inline ``<span style=`` count at today's intentional total."""
    src = Path("ui/tab_briefing.py").read_text(encoding="utf-8")
    hits = re.findall(r"<span(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_SPAN_BUDGET, (
        f"Found {len(hits)} inline <span style=...> blocks — budget is "
        f"{_INLINE_SPAN_BUDGET}. The existing micro-helpers (_mono, _sans) "
        "cover the documented per-cell cases."
    )


# ── 5. Figure-builder: forecast quadrant scatter ──────────────────────────

def test_forecast_quadrant_scatter_empty_returns_annotated_figure() -> None:
    """Empty / None forecasts → non-crashing annotated-empty figure."""
    import plotly.graph_objects as go
    from ui.tab_briefing import _build_forecast_quadrant_scatter

    fig = _build_forecast_quadrant_scatter([])
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0
    assert any("No route forecasts" in (a.text or "")
               for a in fig.layout.annotations)

    fig_none = _build_forecast_quadrant_scatter(None)
    assert any("No route forecasts" in (a.text or "")
               for a in fig_none.layout.annotations)


def test_forecast_quadrant_scatter_groups_by_trend() -> None:
    """Each present trend gets its own trace; the colour matches `_TREND_COLOR`."""
    from ui.styles import C_HIGH, C_LOW, C_TEXT2
    from ui.tab_briefing import _build_forecast_quadrant_scatter

    forecasts = [
        _fc(route_name="A", trend="Worsening"),
        _fc(route_name="B", trend="Improving"),
        _fc(route_name="C", trend="Stable"),
        _fc(route_name="D", trend="Worsening"),
    ]
    fig = _build_forecast_quadrant_scatter(forecasts)
    by_name = {t.name: t for t in fig.data}
    assert set(by_name.keys()) == {"Worsening", "Improving", "Stable"}
    assert by_name["Worsening"].marker.color == C_LOW
    assert by_name["Improving"].marker.color == C_HIGH
    assert by_name["Stable"].marker.color    == C_TEXT2
    # Two Worsening forecasts → trace carries two points
    assert len(by_name["Worsening"].x) == 2


def test_forecast_quadrant_scatter_marker_size_clamps() -> None:
    """Marker size scales with |rate_forecast_pct| and clamps to 10–28 px."""
    from ui.tab_briefing import _build_forecast_quadrant_scatter

    forecasts = [
        _fc(route_name="quiet", rate_pct=0.0),     # |r| = 0 → floor at 10
        _fc(route_name="loud",  rate_pct=0.50),    # |r| = 50% → caps at 28
    ]
    fig = _build_forecast_quadrant_scatter(forecasts)
    # Both end up in the same trace (both default trend="Worsening")
    sizes = list(fig.data[0].marker.size)
    assert min(sizes) == 10
    assert max(sizes) == 28


def test_forecast_quadrant_scatter_has_diagonal_reference() -> None:
    """A y=x diagonal reference line must be present."""
    from ui.tab_briefing import _build_forecast_quadrant_scatter

    fig = _build_forecast_quadrant_scatter([_fc()])
    diagonals = [
        s for s in (fig.layout.shapes or [])
        if s.type == "line" and s.x0 == s.y0 and s.x1 == s.y1
    ]
    assert diagonals, "expected a y=x diagonal reference line"
