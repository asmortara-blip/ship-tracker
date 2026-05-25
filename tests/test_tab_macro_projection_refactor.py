"""Refactor lock-in tests for tab_macro_projection.

Macro Projection (stage 3 of the Disruption Alpha chain) carries a
deliberate set of hand-crafted micro-helpers — ``_narrative``, ``_eyebrow``,
``_empty_state``, ``_chain_stage_strip``, ``_gauge_caption``,
``_gauge_readout``, ``_gauge_bridge``, ``_mono``, ``_sans`` — that do
section-level / cell-level work without a 1:1 mapping in ``ui.styles``.
These tests pin the structural contract and cap the inline-style budget
so future drift cannot silently push more ad-hoc inline styling in.

Additionally exercises the new ``_build_ssi_component_bars`` pure builder.
"""
from __future__ import annotations

import re
from pathlib import Path
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

    for attr in (
        "markdown", "error", "info", "warning", "success", "write",
        "dataframe", "plotly_chart", "download_button", "expander",
        "metric", "selectbox", "slider", "number_input", "button",
        "container", "caption", "subheader", "title", "header", "divider",
        "code", "table", "json", "image", "altair_chart",
    ):
        monkeypatch.setattr(st, attr, stub, raising=False)
    monkeypatch.setattr(st, "columns", stub.columns, raising=False)
    monkeypatch.setattr(st, "tabs",    stub.tabs,    raising=False)
    yield stub


# ── 1. Imports cleanly ─────────────────────────────────────────────────────

def test_module_imports_cleanly() -> None:
    """The tab must still import without any error."""
    import importlib
    import sys
    if "ui.tab_macro_projection" in sys.modules:
        importlib.reload(sys.modules["ui.tab_macro_projection"])
    else:
        importlib.import_module("ui.tab_macro_projection")

    from ui import tab_macro_projection
    assert callable(tab_macro_projection.render)


# ── 2. Renders cleanly with empty data ─────────────────────────────────────

def test_render_with_empty_data_no_exception(streamlit_stub) -> None:
    """All-None inputs must flow through every guard cleanly."""
    from ui import tab_macro_projection

    tab_macro_projection.render(
        port_results=None, freight_data=None,
        macro_data=None, route_results=None,
    )


# ── 3. Renders cleanly with seeded data ────────────────────────────────────

def test_render_with_seeded_data_no_exception(streamlit_stub) -> None:
    """A modest synthetic payload must also pass through cleanly."""
    import pandas as pd
    from ui import tab_macro_projection

    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    tab_macro_projection.render(
        port_results=[],
        freight_data={
            "baltic_dry_index": 1500.0,
            "transpacific_eb": pd.DataFrame({
                "date": dates,
                "rate_usd_per_feu": [2000.0 + i * 5 for i in range(30)],
            }),
        },
        macro_data={"china_pmi": 51.0},
        route_results=[],
    )


# ── 4. Canonical design-system helpers are wired in ────────────────────────

def test_imports_design_system_helpers() -> None:
    """tab_macro_projection must import the load-bearing ui.styles helpers."""
    src = Path("ui/tab_macro_projection.py").read_text(encoding="utf-8")
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)",
        src,
        re.DOTALL,
    )
    assert m, "tab_macro_projection must import from ui.styles via a parenthesized import block"
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


# ── 5. Inline-style budget is capped ───────────────────────────────────────
# The tab carries a deliberate set of hand-crafted micro-helpers that do
# section-level work without ui.styles equivalents. The counts below pin
# TODAY's totals as an upper bound — new ad-hoc inline styles can't be
# added without an explicit cap bump (which forces a docs / review step).

_INLINE_DIV_BUDGET:  int = 18  # _narrative, _empty_state, _chain_stage_strip
                               # (multi-div wrapper), _gauge_caption,
                               # _gauge_readout, _gauge_bridge, eyebrow wrapper
_INLINE_SPAN_BUDGET: int = 24  # _eyebrow, _mono, _sans, _chain_stage_strip
                               # per-stage chips, _gauge_caption accents


def test_inline_div_count_within_documented_budget() -> None:
    """Cap inline ``<div style=`` count at today's intentional total."""
    src = Path("ui/tab_macro_projection.py").read_text(encoding="utf-8")
    hits = re.findall(r"<div(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_DIV_BUDGET, (
        f"Found {len(hits)} inline <div style=...> blocks — budget is "
        f"{_INLINE_DIV_BUDGET}. Route new ones through ui.styles helpers "
        "(metric_card_row, gradient_card, section_divider, status_badge) "
        "instead of bumping the cap."
    )


def test_inline_span_count_within_documented_budget() -> None:
    """Cap inline ``<span style=`` count at today's intentional total."""
    src = Path("ui/tab_macro_projection.py").read_text(encoding="utf-8")
    hits = re.findall(r"<span(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_SPAN_BUDGET, (
        f"Found {len(hits)} inline <span style=...> blocks — budget is "
        f"{_INLINE_SPAN_BUDGET}. The existing micro-helpers (_eyebrow, "
        "_mono, _sans, _chain_stage_strip, _gauge_caption, _gauge_readout) "
        "cover the documented cases."
    )


# ── 6. Figure-builder: SSI component bars ─────────────────────────────────

def test_ssi_component_bars_empty_scores_returns_annotated_figure() -> None:
    """Empty / None component_scores → non-crashing annotated-empty figure."""
    import plotly.graph_objects as go
    from ui.tab_macro_projection import _build_ssi_component_bars

    fig = _build_ssi_component_bars({})
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0
    assert any("No SSI component scores" in (a.text or "")
               for a in fig.layout.annotations)

    fig_none = _build_ssi_component_bars(None)
    assert any("No SSI component scores" in (a.text or "")
               for a in fig_none.layout.annotations)


def test_ssi_component_bars_orders_hot_first() -> None:
    """Highest stress must render at the TOP of the bar chart.

    Plotly stacks categorical y-values bottom-up, so the builder sorts
    ascending and lets Plotly invert. This test pins that contract.
    """
    from ui.tab_macro_projection import (
        _SSI_COMPONENT_DISPLAY,
        _build_ssi_component_bars,
    )

    scores = {
        "chokepoint":    0.30,
        "congestion":    0.80,  # hottest
        "weather":       0.10,  # coldest
        "rate":          0.55,
    }
    fig = _build_ssi_component_bars(scores)
    y = list(fig.data[0].y)
    # Bottom of the y-list is the lowest score; top is the highest.
    assert y[0]  == _SSI_COMPONENT_DISPLAY["weather"]
    assert y[-1] == _SSI_COMPONENT_DISPLAY["congestion"]


def test_ssi_component_bars_colour_uses_band_function() -> None:
    """Each bar's colour must come from `_ssi_band_color` (consistency)."""
    from ui.tab_macro_projection import (
        _build_ssi_component_bars,
        _ssi_band_color,
    )

    scores = {
        "chokepoint": 0.70,   # critical band
        "congestion": 0.50,   # pressured band
        "weather":    0.30,   # elevated band
        "rate":       0.10,   # calm band
    }
    fig = _build_ssi_component_bars(scores)
    # Ascending sort → rate (0.10) is at index 0, chokepoint (0.70) is at index -1
    colors = list(fig.data[0].marker.color)
    assert colors[0]  == _ssi_band_color(0.10)
    assert colors[-1] == _ssi_band_color(0.70)


def test_ssi_component_bars_weight_annotation_present_when_supplied() -> None:
    """If weights are passed, each bar carries a ``w XX%`` hover annotation."""
    from ui.tab_macro_projection import _build_ssi_component_bars

    scores  = {"chokepoint": 0.5, "weather": 0.2}
    weights = {"chokepoint": 0.29, "weather": 0.16}
    fig = _build_ssi_component_bars(scores, weights)
    customdata = list(fig.data[0].customdata)
    # Every entry should look like 'w NN%'
    assert all(re.fullmatch(r"w \d+%", entry) for entry in customdata)
