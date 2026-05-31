"""Refactor lock-in tests for tab_alerts.

The Alert Center is the platform's largest tab (~4,900 LOC, 21 panels).
These tests pin the structural contract, cap the inline-style budget at
today's intentional totals, and exercise the new
``_build_effectiveness_bubble`` pure builder that sits inside the Alert
Effectiveness panel.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def streamlit_stub(monkeypatch):
    """Stub the `st.*` surface so the tab renders headlessly.

    Request-adaptive `st.columns` so the many column-tuple unpacks in
    this tab don't blow up on a static-width stub.
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

    expander_cm = MagicMock()
    expander_cm.__enter__ = MagicMock(return_value=expander_cm)
    expander_cm.__exit__  = MagicMock(return_value=False)
    stub.expander.return_value = expander_cm

    form_cm = MagicMock()
    form_cm.__enter__ = MagicMock(return_value=form_cm)
    form_cm.__exit__  = MagicMock(return_value=False)
    stub.form.return_value = form_cm

    # Widget defaults — many of these get piped into str / numeric ops.
    stub.text_input.return_value  = ""
    stub.text_area.return_value   = ""
    stub.selectbox.return_value   = ""
    stub.multiselect.return_value = []
    stub.checkbox.return_value    = False
    stub.button.return_value      = False
    stub.toggle.return_value      = False
    stub.number_input.return_value = 0
    stub.slider.return_value      = 0
    stub.radio.return_value       = ""
    stub.date_input.return_value  = None
    stub.time_input.return_value  = None
    stub.color_picker.return_value = "#000000"
    stub.file_uploader.return_value = None
    stub.form_submit_button.return_value = False

    for attr in (
        "markdown", "html", "error", "info", "warning", "success", "write",
        "dataframe", "plotly_chart", "download_button", "metric",
        "container", "caption", "subheader", "title", "header", "divider",
        "code", "table", "json", "image", "altair_chart", "rerun", "toast",
        "spinner", "progress", "line_chart", "bar_chart", "area_chart",
        "pyplot", "graphviz_chart", "vega_lite_chart", "map", "audio",
        "video", "balloons", "snow", "popover", "status",
    ):
        monkeypatch.setattr(st, attr, stub, raising=False)
    # Explicit widget rebinds — without these the side-effect / .return_value
    # contract on the parent stub is shadowed by the bare attribute writes.
    for attr in (
        "columns", "tabs", "expander", "form", "text_input", "text_area",
        "selectbox", "multiselect", "checkbox", "button", "toggle",
        "number_input", "slider", "radio", "date_input", "time_input",
        "color_picker", "file_uploader", "form_submit_button",
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
    if "ui.tab_alerts" in sys.modules:
        importlib.reload(sys.modules["ui.tab_alerts"])
    else:
        importlib.import_module("ui.tab_alerts")

    from ui import tab_alerts
    assert callable(tab_alerts.render)


# ── 2. Required ui.styles helpers are wired in ────────────────────────────

def test_imports_design_system_helpers() -> None:
    """Pin the load-bearing ui.styles helper imports."""
    src = Path("ui/tab_alerts.py").read_text(encoding="utf-8")
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)",
        src,
        re.DOTALL,
    )
    assert m, "tab_alerts must import from ui.styles via a parenthesized import block"
    imported = {tok.strip().rstrip(",") for tok in m.group(1).split() if tok.strip()}

    required = {
        "apply_dark_layout",
        "badge",
        "live_data_badge",
        "metric_card_row",
        "page_header",
        "section_divider",
        "section_header",
        "wsj_market_table",
    }
    missing = required - imported
    assert not missing, (
        f"Required ui.styles helpers missing from import block: {sorted(missing)}"
    )


# ── 3. Inline-style budget caps ───────────────────────────────────────────
# At ~4,900 LOC the file is huge but already nearly inline-style-clean.
# 15 divs / 2 spans today; caps lock that in.

_INLINE_DIV_BUDGET:  int = 15
_INLINE_SPAN_BUDGET: int = 2


def test_inline_div_count_within_documented_budget() -> None:
    """Cap inline ``<div style=`` count at today's intentional total."""
    src = Path("ui/tab_alerts.py").read_text(encoding="utf-8")
    hits = re.findall(r"<div(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_DIV_BUDGET, (
        f"Found {len(hits)} inline <div style=...> blocks — budget is "
        f"{_INLINE_DIV_BUDGET}. Route new ones through ui.styles helpers."
    )


def test_inline_span_count_within_documented_budget() -> None:
    """Cap inline ``<span style=`` count at today's intentional total."""
    src = Path("ui/tab_alerts.py").read_text(encoding="utf-8")
    hits = re.findall(r"<span(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_SPAN_BUDGET, (
        f"Found {len(hits)} inline <span style=...> blocks — budget is "
        f"{_INLINE_SPAN_BUDGET}. The existing micro-helpers (_mono, _sans) "
        "cover the documented per-cell cases."
    )


# ── 4. Effectiveness bubble builder ───────────────────────────────────────

def test_effectiveness_bubble_empty_returns_annotated_figure() -> None:
    """Empty / None by_alert_type → annotated-empty figure."""
    import plotly.graph_objects as go
    from ui.tab_alerts import _build_effectiveness_bubble

    fig = _build_effectiveness_bubble({})
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0
    assert any("No alert-type breakdown" in (a.text or "")
               for a in fig.layout.annotations)


def test_effectiveness_bubble_plots_every_type() -> None:
    """One marker per alert type; counts on x; rates on y as percent."""
    from ui.tab_alerts import _build_effectiveness_bubble

    by_type = {
        "BDI_MOVE":   {"count": 10, "hit_rate": 0.65},
        "RATE_SURGE": {"count":  5, "hit_rate": 0.40},
        "CONGESTION": {"count": 20, "hit_rate": 0.55},
    }
    fig = _build_effectiveness_bubble(by_type)
    assert len(fig.data) == 1
    trace = fig.data[0]
    # Three points, one per type. Order is insertion-order from the dict.
    assert len(trace.x) == 3
    # Counts on x, rates*100 on y (compare loosely to dodge float artefacts
    # like 0.55 * 100 → 55.00000000000001), text = labels.
    assert set(trace.x)    == {10, 5, 20}
    y_vals = sorted(round(v, 2) for v in trace.y)
    assert y_vals == [40.0, 55.0, 65.0]
    assert set(trace.text) == {"BDI_MOVE", "RATE_SURGE", "CONGESTION"}


def test_effectiveness_bubble_colour_by_hit_rate_band() -> None:
    """Hit rate ≥0.55 → C_HIGH; 0.45–0.55 → C_MOD; <0.45 → C_LOW."""
    from ui.styles import C_HIGH, C_LOW, C_MOD
    from ui.tab_alerts import _build_effectiveness_bubble

    by_type = {
        "good":     {"count": 5, "hit_rate": 0.70},
        "midline":  {"count": 5, "hit_rate": 0.48},
        "bad":      {"count": 5, "hit_rate": 0.30},
    }
    fig = _build_effectiveness_bubble(by_type)
    by_label = dict(zip(list(fig.data[0].text), list(fig.data[0].marker.color)))
    assert by_label["good"]    == C_HIGH
    assert by_label["midline"] == C_MOD
    assert by_label["bad"]     == C_LOW


def test_effectiveness_bubble_size_clamps_to_min_and_max() -> None:
    """Marker size scales with count, clamped to a 14–44 px range."""
    from ui.tab_alerts import _build_effectiveness_bubble

    by_type = {
        "tiny":  {"count":   1, "hit_rate": 0.5},
        "big":   {"count": 200, "hit_rate": 0.5},
    }
    fig = _build_effectiveness_bubble(by_type)
    sizes = list(fig.data[0].marker.size)
    assert min(sizes) >= 14.0
    assert max(sizes) <= 44.0
    # The bigger count should get the larger marker.
    by_label = dict(zip(list(fig.data[0].text), sizes))
    assert by_label["big"] > by_label["tiny"]


def test_effectiveness_bubble_has_50pct_reference_line() -> None:
    """The 50% hit-rate reference line must be present and annotated."""
    from ui.tab_alerts import _build_effectiveness_bubble

    fig = _build_effectiveness_bubble({"x": {"count": 5, "hit_rate": 0.6}})
    shapes = list(fig.layout.shapes or [])
    # An hline at y=50 has y0 == y1 == 50.
    hlines = [s for s in shapes if s.type == "line" and s.y0 == s.y1 == 50.0]
    assert hlines, "expected a horizontal reference line at y=50%"
    annotations = [a.text for a in (fig.layout.annotations or []) if a.text]
    assert any("50" in a for a in annotations), \
        "expected a '50%' annotation on the reference line"


def test_sans_and_mono_escape_user_text() -> None:
    """#7 stored-XSS guard: _sans/_mono escape their value — alert-rule and
    delivery-channel NAMES flow through them into unsafe_allow_html sinks."""
    from ui.tab_alerts import _sans, _mono
    out = _sans("<img src=x onerror=alert(1)>")
    assert "<img" not in out and "&lt;img" in out
    assert "&lt;script&gt;" in _mono("<script>")
