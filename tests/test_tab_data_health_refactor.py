"""Refactor lock-in tests for tab_data_health.

The Data Source Health tab is the platform's operator dashboard and
ranks #1 on the audit baseline. ~3,400 LOC across 22 panels with only
two charts pre-existing. These tests pin the structural contract, cap
the inline-style budget at today's intentional totals, and exercise
the new ``_build_tab_perf_scatter`` pure builder.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def streamlit_stub(monkeypatch):
    """Request-adaptive Streamlit stub (same shape as the other refactor
    files in this push)."""
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

    form_cm = MagicMock()
    form_cm.__enter__ = MagicMock(return_value=form_cm)
    form_cm.__exit__  = MagicMock(return_value=False)
    stub.form.return_value = form_cm

    stub.text_input.return_value   = ""
    stub.text_area.return_value    = ""
    stub.selectbox.return_value    = ""
    stub.multiselect.return_value  = []
    stub.checkbox.return_value     = False
    stub.button.return_value       = False
    stub.toggle.return_value       = False
    stub.number_input.return_value = 0
    stub.slider.return_value       = 0
    stub.radio.return_value        = ""
    stub.form_submit_button.return_value = False

    for attr in (
        "markdown", "html", "error", "info", "warning", "success", "write",
        "dataframe", "plotly_chart", "download_button", "metric",
        "container", "caption", "subheader", "title", "header", "divider",
        "code", "table", "json", "image", "altair_chart", "rerun", "toast",
        "spinner", "progress", "line_chart", "bar_chart", "area_chart",
        "pyplot",
    ):
        monkeypatch.setattr(st, attr, stub, raising=False)
    for attr in (
        "columns", "tabs", "expander", "form", "text_input", "text_area",
        "selectbox", "multiselect", "checkbox", "button", "toggle",
        "number_input", "slider", "radio", "form_submit_button",
    ):
        monkeypatch.setattr(st, attr, getattr(stub, attr), raising=False)
    if not hasattr(st, "session_state") or st.session_state is None:
        monkeypatch.setattr(st, "session_state", {}, raising=False)
    try:
        st.session_state.clear()
    except Exception:
        pass
    yield stub


# ── 1. Imports cleanly ─────────────────────────────────────────────────────

def test_module_imports_cleanly() -> None:
    """The tab must still import without any error."""
    import importlib
    import sys
    if "ui.tab_data_health" in sys.modules:
        importlib.reload(sys.modules["ui.tab_data_health"])
    else:
        importlib.import_module("ui.tab_data_health")

    from ui import tab_data_health
    assert callable(tab_data_health.render)


# ── 2. Required ui.styles helpers are wired in ────────────────────────────

def test_imports_design_system_helpers() -> None:
    """Pin the load-bearing ui.styles helper imports."""
    src = Path("ui/tab_data_health.py").read_text(encoding="utf-8")
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)",
        src,
        re.DOTALL,
    )
    assert m, "tab_data_health must import from ui.styles via a parenthesized import block"
    imported = {tok.strip().rstrip(",") for tok in m.group(1).split() if tok.strip()}

    required = {
        "alert_banner",
        "apply_dark_layout",
        "badge",
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


# ── 3. Inline-style budget caps ───────────────────────────────────────────
# 22 divs / 5 spans today across ~3,400 LOC. Caps lock that in.

_INLINE_DIV_BUDGET:  int = 22
_INLINE_SPAN_BUDGET: int = 5


def test_inline_div_count_within_documented_budget() -> None:
    """Cap inline ``<div style=`` count at today's intentional total."""
    src = Path("ui/tab_data_health.py").read_text(encoding="utf-8")
    hits = re.findall(r"<div(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_DIV_BUDGET, (
        f"Found {len(hits)} inline <div style=...> blocks — budget is "
        f"{_INLINE_DIV_BUDGET}. Route new ones through ui.styles helpers."
    )


def test_inline_span_count_within_documented_budget() -> None:
    """Cap inline ``<span style=`` count at today's intentional total."""
    src = Path("ui/tab_data_health.py").read_text(encoding="utf-8")
    hits = re.findall(r"<span(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_SPAN_BUDGET, (
        f"Found {len(hits)} inline <span style=...> blocks — budget is "
        f"{_INLINE_SPAN_BUDGET}. The existing micro-helpers (_mono, _sans) "
        "cover the documented per-cell cases."
    )


# ── 4. Tab-perf scatter builder ───────────────────────────────────────────

def test_tab_perf_scatter_empty_returns_annotated_figure() -> None:
    """Empty / None by_tab → annotated-empty figure."""
    import plotly.graph_objects as go
    from ui.tab_data_health import _build_tab_perf_scatter

    fig = _build_tab_perf_scatter({})
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0
    assert any("No render telemetry" in (a.text or "")
               for a in fig.layout.annotations)


def test_tab_perf_scatter_plots_every_tab() -> None:
    """One marker per tab; medians on x; p95s on y; tab names as text."""
    from ui.tab_data_health import _build_tab_perf_scatter

    by_tab = {
        "alpha":   {"median_ms": 100, "p95_ms": 250, "count": 40, "success_rate": 1.00},
        "beta":    {"median_ms": 350, "p95_ms": 900, "count": 12, "success_rate": 0.92},
        "gamma":   {"median_ms": 50,  "p95_ms": 90,  "count": 80, "success_rate": 0.98},
    }
    fig = _build_tab_perf_scatter(by_tab)
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert len(trace.x) == 3
    assert set(trace.x)    == {100.0, 350.0, 50.0}
    assert set(trace.y)    == {250.0, 900.0, 90.0}
    assert set(trace.text) == {"alpha", "beta", "gamma"}


def test_tab_perf_scatter_colour_by_success_rate_band() -> None:
    """Success >=99% → C_HIGH; 95-99% → C_MOD; <95% → C_LOW."""
    from ui.styles import C_HIGH, C_LOW, C_MOD
    from ui.tab_data_health import _build_tab_perf_scatter

    by_tab = {
        "great":  {"median_ms": 50,  "p95_ms": 80, "count": 10, "success_rate": 1.00},
        "okay":   {"median_ms": 80,  "p95_ms": 200, "count": 10, "success_rate": 0.97},
        "poor":   {"median_ms": 600, "p95_ms": 2000, "count": 10, "success_rate": 0.80},
    }
    fig = _build_tab_perf_scatter(by_tab)
    by_label = dict(zip(list(fig.data[0].text), list(fig.data[0].marker.color)))
    assert by_label["great"] == C_HIGH
    assert by_label["okay"]  == C_MOD
    assert by_label["poor"]  == C_LOW


def test_tab_perf_scatter_marker_size_clamps_to_range() -> None:
    """Marker size scales with render count, clamped to 10–32 px."""
    from ui.tab_data_health import _build_tab_perf_scatter

    by_tab = {
        "rare":   {"median_ms": 100, "p95_ms": 150, "count": 1,    "success_rate": 1.0},
        "common": {"median_ms": 100, "p95_ms": 150, "count": 1000, "success_rate": 1.0},
    }
    fig = _build_tab_perf_scatter(by_tab)
    sizes = list(fig.data[0].marker.size)
    assert min(sizes) >= 10.0
    assert max(sizes) <= 32.0
    by_label = dict(zip(list(fig.data[0].text), sizes))
    assert by_label["common"] > by_label["rare"]


def test_tab_perf_scatter_has_diagonal_reference_line() -> None:
    """A y=x diagonal reference line marks the ideal flat-latency case."""
    from ui.tab_data_health import _build_tab_perf_scatter

    by_tab = {"x": {"median_ms": 100, "p95_ms": 250, "count": 10, "success_rate": 1.0}}
    fig = _build_tab_perf_scatter(by_tab)
    diagonals = [
        s for s in (fig.layout.shapes or [])
        if s.type == "line" and s.x0 == s.y0 and s.x1 == s.y1
    ]
    assert diagonals, "expected a y=x diagonal reference line"
