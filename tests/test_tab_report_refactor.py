"""Refactor lock-in tests for tab_report.

The Investor Report tab is already nearly inline-style-clean (2 divs /
5 spans at the time of writing). These tests pin the structural contract,
cap the inline-style budget, and exercise the new
``_build_sentiment_trend`` pure builder that sits above the History
table.
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

    Request-adaptive `st.columns` so column-tuple unpacking works.
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

    # Widget defaults that downstream string ops can choke on
    stub.text_input.return_value = ""
    stub.selectbox.return_value = ""
    stub.button.return_value = False
    stub.checkbox.return_value = False
    stub.multiselect.return_value = []

    for attr in (
        "markdown", "error", "info", "warning", "success", "write",
        "dataframe", "plotly_chart", "download_button", "metric",
        "slider", "number_input", "container", "caption", "subheader",
        "title", "header", "divider", "code", "table", "json", "image",
        "altair_chart", "rerun", "toast", "spinner", "progress",
    ):
        monkeypatch.setattr(st, attr, stub, raising=False)
    monkeypatch.setattr(st, "columns",     stub.columns,     raising=False)
    monkeypatch.setattr(st, "tabs",        stub.tabs,        raising=False)
    monkeypatch.setattr(st, "expander",    stub.expander,    raising=False)
    monkeypatch.setattr(st, "form",        stub.form,        raising=False)
    monkeypatch.setattr(st, "text_input",  stub.text_input,  raising=False)
    monkeypatch.setattr(st, "selectbox",   stub.selectbox,   raising=False)
    monkeypatch.setattr(st, "button",      stub.button,      raising=False)
    monkeypatch.setattr(st, "checkbox",    stub.checkbox,    raising=False)
    monkeypatch.setattr(st, "multiselect", stub.multiselect, raising=False)
    if not hasattr(st, "session_state") or st.session_state is None:
        monkeypatch.setattr(st, "session_state", {}, raising=False)
    yield stub


def _meta(*, ts: str = "2026-05-01T12:00:00Z",
          label: str = "BULLISH", score: float = 0.4) -> SimpleNamespace:
    """Build a minimal ReportMeta-like namespace for the sentiment builder."""
    return SimpleNamespace(
        report_id="rep_x",
        generated_at=ts,
        report_date=ts[:10],
        sentiment_label=label,
        sentiment_score=score,
        risk_level="LOW",
        signal_count=5,
        data_quality="FULL",
        file_path="",
        file_size_kb=10.0,
    )


# ── 1. Imports cleanly ─────────────────────────────────────────────────────

def test_module_imports_cleanly() -> None:
    """The tab must still import without any error."""
    import importlib
    import sys
    if "ui.tab_report" in sys.modules:
        importlib.reload(sys.modules["ui.tab_report"])
    else:
        importlib.import_module("ui.tab_report")

    from ui import tab_report
    assert callable(tab_report.render)


# ── 2. Render survives empty inputs ───────────────────────────────────────

def test_render_with_empty_data_no_exception(streamlit_stub) -> None:
    """All-None / empty inputs must flow through every guard cleanly."""
    from ui import tab_report

    # tab_report.render takes no positional inputs — it pulls from session
    # state + history layer. Just call it.
    tab_report.render()


# ── 3. Required ui.styles helpers are wired in ────────────────────────────

def test_imports_design_system_helpers() -> None:
    """Pin the load-bearing ui.styles helper imports."""
    src = Path("ui/tab_report.py").read_text(encoding="utf-8")
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)",
        src,
        re.DOTALL,
    )
    assert m, "tab_report must import from ui.styles via a parenthesized import block"
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
        "status_badge",
        "wsj_market_table",
    }
    missing = required - imported
    assert not missing, (
        f"Required ui.styles helpers missing from import block: {sorted(missing)}"
    )


# ── 4. Inline-style budget caps ───────────────────────────────────────────

_INLINE_DIV_BUDGET:  int = 2   # wsj-card wrapper + section-pills wrapper
_INLINE_SPAN_BUDGET: int = 5   # _mono + _sans + badge-spacing wrappers


def test_inline_div_count_within_documented_budget() -> None:
    """Cap inline ``<div style=`` count at today's intentional total."""
    src = Path("ui/tab_report.py").read_text(encoding="utf-8")
    hits = re.findall(r"<div(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_DIV_BUDGET, (
        f"Found {len(hits)} inline <div style=...> blocks — budget is "
        f"{_INLINE_DIV_BUDGET}. Route new ones through ui.styles helpers."
    )


def test_inline_span_count_within_documented_budget() -> None:
    """Cap inline ``<span style=`` count at today's intentional total."""
    src = Path("ui/tab_report.py").read_text(encoding="utf-8")
    hits = re.findall(r"<span(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_SPAN_BUDGET, (
        f"Found {len(hits)} inline <span style=...> blocks — budget is "
        f"{_INLINE_SPAN_BUDGET}. The existing micro-helpers (_mono, _sans) "
        "cover the documented per-cell cases."
    )


# ── 5. Sentiment trend builder ────────────────────────────────────────────

def test_sentiment_trend_empty_returns_annotated_figure() -> None:
    """Empty / None reports → non-crashing annotated-empty figure."""
    import plotly.graph_objects as go
    from ui.tab_report import _build_sentiment_trend

    fig = _build_sentiment_trend([])
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0
    assert any("No historical reports" in (a.text or "")
               for a in fig.layout.annotations)

    fig_none = _build_sentiment_trend(None)
    assert any("No historical reports" in (a.text or "")
               for a in fig_none.layout.annotations)


def test_sentiment_trend_orders_oldest_to_newest() -> None:
    """X-axis must read oldest → newest left-to-right, regardless of the
    order the history layer returns the reports in.

    The history layer typically returns newest-first; the builder flips
    that so the chart reads naturally.
    """
    from ui.tab_report import _build_sentiment_trend

    # Reports passed in NEWEST-first (as the history layer returns them):
    reports = [
        _meta(ts="2026-05-10T10:00:00Z", score=0.3),
        _meta(ts="2026-05-05T10:00:00Z", score=0.1),
        _meta(ts="2026-05-01T10:00:00Z", score=-0.2),
    ]
    fig = _build_sentiment_trend(reports)
    x_vals = list(fig.data[0].x)
    # After sorting, x_vals are timestamps in ascending order — verify the
    # leftmost is the OLDEST report and the rightmost is the NEWEST.
    assert x_vals[0].startswith("2026-05-01")
    assert x_vals[-1].startswith("2026-05-10")


def test_sentiment_trend_marker_colors_follow_label() -> None:
    """BULLISH → C_HIGH, BEARISH → C_LOW, NEUTRAL/MIXED → C_TEXT2."""
    from ui.styles import C_HIGH, C_LOW, C_TEXT2
    from ui.tab_report import _build_sentiment_trend

    reports = [
        _meta(ts="2026-05-01T10:00:00Z", label="BULLISH", score=0.5),
        _meta(ts="2026-05-02T10:00:00Z", label="BEARISH", score=-0.5),
        _meta(ts="2026-05-03T10:00:00Z", label="NEUTRAL", score=0.0),
        _meta(ts="2026-05-04T10:00:00Z", label="MIXED",   score=0.1),
    ]
    fig = _build_sentiment_trend(reports)
    colors = list(fig.data[0].marker.color)
    assert colors[0] == C_HIGH    # BULLISH
    assert colors[1] == C_LOW     # BEARISH
    assert colors[2] == C_TEXT2   # NEUTRAL
    assert colors[3] == C_TEXT2   # MIXED


def test_sentiment_trend_y_range_pinned_to_unit_interval() -> None:
    """Y-axis range must be [-1.05, +1.05] so the colour & threshold
    interpretation stays stable regardless of the actual score range."""
    from ui.tab_report import _build_sentiment_trend

    fig = _build_sentiment_trend([_meta(score=0.3)])
    yr = list(fig.layout.yaxis.range)
    assert yr == [-1.05, 1.05]


def test_sentiment_trend_supports_dict_records() -> None:
    """The builder must also work for dict-shaped report records (the
    history layer may return either dataclasses or dicts depending on
    the persistence path)."""
    from ui.tab_report import _build_sentiment_trend

    reports = [
        {"generated_at": "2026-05-01T10:00:00Z",
         "sentiment_label": "BULLISH", "sentiment_score": 0.4},
        {"generated_at": "2026-05-02T10:00:00Z",
         "sentiment_label": "BEARISH", "sentiment_score": -0.3},
    ]
    fig = _build_sentiment_trend(reports)
    assert len(fig.data) == 1
    assert list(fig.data[0].y) == [0.4, -0.3]
