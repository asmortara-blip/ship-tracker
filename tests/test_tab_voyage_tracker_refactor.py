"""Refactor lock-in tests for tab_voyage_tracker.

Stage 1 of the Disruption Alpha pipeline (voyages → stress → forecast →
linkage → equity). These tests pin the structural contract, cap the
inline-style budget at today's intentional totals, and exercise the new
``_build_delay_distribution`` pure builder that sits underneath the
existing KPI strip.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def streamlit_stub(monkeypatch):
    """Stub the `st.*` surface so the tab renders headlessly.

    `st.columns` is request-adaptive (returns exactly the requested count)
    so tab_voyage_tracker's mix of `st.columns(n)` and `st.columns([w1, w2])`
    calls all unpack correctly.
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
    # text_input + selectbox must yield real strings — the tab uses them
    # downstream in string operations / dict lookups.
    stub.text_input.return_value = ""
    stub.selectbox.return_value = ""

    for attr in (
        "markdown", "error", "info", "warning", "success", "write",
        "dataframe", "plotly_chart", "download_button", "metric",
        "number_input", "button", "container", "caption", "subheader",
        "title", "header", "divider", "code", "table", "json", "image",
        "altair_chart", "expander",
    ):
        monkeypatch.setattr(st, attr, stub, raising=False)
    monkeypatch.setattr(st, "columns",    stub.columns,    raising=False)
    monkeypatch.setattr(st, "tabs",       stub.tabs,       raising=False)
    monkeypatch.setattr(st, "text_input", stub.text_input, raising=False)
    monkeypatch.setattr(st, "selectbox",  stub.selectbox,  raising=False)
    if not hasattr(st, "session_state") or st.session_state is None:
        monkeypatch.setattr(st, "session_state", {}, raising=False)
    yield stub


def _voyage(delay_days: float, status: str = "On Schedule"):
    """Build a minimal voyage namespace matching what _build_delay_distribution
    looks at."""
    return SimpleNamespace(delay_days=float(delay_days), status=status)


# ── 1. Imports cleanly ─────────────────────────────────────────────────────

def test_module_imports_cleanly() -> None:
    """The tab must still import without any error."""
    import importlib
    import sys
    if "ui.tab_voyage_tracker" in sys.modules:
        importlib.reload(sys.modules["ui.tab_voyage_tracker"])
    else:
        importlib.import_module("ui.tab_voyage_tracker")

    from ui import tab_voyage_tracker
    assert callable(tab_voyage_tracker.render)


# ── 2. Render survives empty inputs ───────────────────────────────────────

def test_render_with_empty_data_no_exception(streamlit_stub) -> None:
    """All-None / empty inputs must flow through every guard cleanly."""
    from ui import tab_voyage_tracker

    tab_voyage_tracker.render(freight_data=None, route_results=None)


# ── 3. Required ui.styles helpers are wired in ────────────────────────────

def test_imports_design_system_helpers() -> None:
    """Pin the load-bearing ui.styles helper imports."""
    src = Path("ui/tab_voyage_tracker.py").read_text(encoding="utf-8")
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)",
        src,
        re.DOTALL,
    )
    assert m, "tab_voyage_tracker must import from ui.styles via a parenthesized import block"
    imported = {tok.strip().rstrip(",") for tok in m.group(1).split() if tok.strip()}

    required = {
        "alert_banner",
        "apply_dark_layout",
        "badge",
        "gauge_ring",
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

_INLINE_DIV_BUDGET:  int = 8    # voyage detail labels + lane legend wrappers
_INLINE_SPAN_BUDGET: int = 8    # _mono + _sans + _metric_label + _group_label


def test_inline_div_count_within_documented_budget() -> None:
    """Cap inline ``<div style=`` count at today's intentional total."""
    src = Path("ui/tab_voyage_tracker.py").read_text(encoding="utf-8")
    hits = re.findall(r"<div(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_DIV_BUDGET, (
        f"Found {len(hits)} inline <div style=...> blocks — budget is "
        f"{_INLINE_DIV_BUDGET}. Route new ones through ui.styles helpers."
    )


def test_inline_span_count_within_documented_budget() -> None:
    """Cap inline ``<span style=`` count at today's intentional total."""
    src = Path("ui/tab_voyage_tracker.py").read_text(encoding="utf-8")
    hits = re.findall(r"<span(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_SPAN_BUDGET, (
        f"Found {len(hits)} inline <span style=...> blocks — budget is "
        f"{_INLINE_SPAN_BUDGET}. The existing micro-helpers (_mono, _sans, "
        "_metric_label, _group_label) cover the documented per-cell cases."
    )


# ── 5. Delay-distribution builder ─────────────────────────────────────────

def test_delay_distribution_empty_fleet_returns_annotated_figure() -> None:
    """Empty fleet → non-crashing annotated-empty figure."""
    import plotly.graph_objects as go
    from ui.tab_voyage_tracker import _build_delay_distribution

    fig = _build_delay_distribution([])
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0
    assert any("No voyages" in (a.text or "")
               for a in fig.layout.annotations)


def test_delay_distribution_bands_voyages_correctly() -> None:
    """Each voyage lands in exactly one band based on `delay_days`.

    Boundaries (must match _build_delay_distribution's band definitions):
      Ahead   delay <  0
      On Time 0 <= delay < 2
      Minor   2 <= delay < 5
      Major   delay >= 5
    """
    from ui.tab_voyage_tracker import _build_delay_distribution

    fleet = [
        _voyage(-3.5),  # Ahead
        _voyage(-1.0),  # Ahead
        _voyage(0.0),   # On Time (lower bound is inclusive)
        _voyage(1.5),   # On Time
        _voyage(2.0),   # Minor (lower bound is inclusive)
        _voyage(4.9),   # Minor
        _voyage(5.0),   # Major (lower bound is inclusive)
        _voyage(12.5),  # Major
    ]
    fig = _build_delay_distribution(fleet)
    bar = fig.data[0]
    by_label = dict(zip(list(bar.x), list(bar.y)))
    assert by_label == {"Ahead": 2, "On Time": 2, "Minor": 2, "Major": 2}


def test_delay_distribution_colour_follows_severity_zone() -> None:
    """Per-bar colours follow the severity-zone palette mapping."""
    from ui.styles import C_HIGH, C_LOW, C_MOD, C_TEXT2
    from ui.tab_voyage_tracker import _build_delay_distribution

    fleet = [_voyage(0.5)]  # any single voyage will do
    fig = _build_delay_distribution(fleet)
    bar = fig.data[0]
    by_label = dict(zip(list(bar.x), list(bar.marker.color)))
    assert by_label["Ahead"]   == C_HIGH
    assert by_label["On Time"] == C_TEXT2
    assert by_label["Minor"]   == C_MOD
    assert by_label["Major"]   == C_LOW


def test_delay_distribution_title_includes_fleet_size() -> None:
    """The chart title surfaces the total voyage count for context."""
    from ui.tab_voyage_tracker import _build_delay_distribution

    fleet = [_voyage(1.0) for _ in range(7)]
    fig = _build_delay_distribution(fleet)
    title_text = (fig.layout.title.text or "")
    assert "7 voyage" in title_text
