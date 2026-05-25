"""Refactor lock-in tests for tab_assistant.

The Q&A Assistant tab is mostly a chat surface — its inline styling lives
inside the hand-rolled answer HTML in ``_build_response`` (deliberate
colour emphasis for headlines / metrics inside answer text). These tests
pin the structural contract, cap the inline-style budget, and exercise
the new session-topic-distribution visual + its classifier.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def streamlit_stub(monkeypatch):
    """Stub the `st.*` surface so the tab renders headlessly.

    `st.columns` is request-adaptive: a request for ``n`` columns (int) or
    a list of ``n`` weights returns exactly ``n`` mock cols. This matters
    for tabs that unpack column tuples (``col_a, col_b = st.columns([3, 1])``).
    """
    import streamlit as st

    def _make_col() -> MagicMock:
        col = MagicMock()
        col.__enter__ = MagicMock(return_value=col)
        col.__exit__  = MagicMock(return_value=False)
        return col

    def _columns_factory(spec, *_args, **_kwargs):
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
    # Force a *real* empty string out of widgets that the tab pipes into
    # string operations later (the assistant's send-message path passes
    # text_input output through `.strip()` and re.sub — both choke on
    # a plain MagicMock).
    stub.text_input.return_value = ""
    stub.button.return_value     = False

    for attr in (
        "markdown", "error", "info", "warning", "success", "write",
        "dataframe", "plotly_chart", "download_button", "metric",
        "selectbox", "slider", "number_input", "container", "caption",
        "subheader", "title", "header", "divider", "code", "table",
        "json", "image", "altair_chart", "rerun",
    ):
        monkeypatch.setattr(st, attr, stub, raising=False)
    monkeypatch.setattr(st, "columns",    stub.columns,    raising=False)
    monkeypatch.setattr(st, "tabs",       stub.tabs,       raising=False)
    monkeypatch.setattr(st, "text_input", stub.text_input, raising=False)
    monkeypatch.setattr(st, "button",     stub.button,     raising=False)
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
    if "ui.tab_assistant" in sys.modules:
        importlib.reload(sys.modules["ui.tab_assistant"])
    else:
        importlib.import_module("ui.tab_assistant")

    from ui import tab_assistant
    assert callable(tab_assistant.render)


# ── 2. Render survives empty inputs ───────────────────────────────────────

def test_render_with_empty_data_no_exception(streamlit_stub) -> None:
    """All-None / empty inputs must flow through every guard cleanly."""
    from ui import tab_assistant

    tab_assistant.render(
        port_results=[], route_results=[], insights=[],
        freight_data=None, macro_data=None, stock_data=None,
    )


# ── 3. Required ui.styles helpers are wired in ────────────────────────────

def test_imports_design_system_helpers() -> None:
    """Pin the load-bearing ui.styles helper imports."""
    src = Path("ui/tab_assistant.py").read_text(encoding="utf-8")
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)",
        src,
        re.DOTALL,
    )
    assert m, "tab_assistant must import from ui.styles via a parenthesized import block"
    imported = {tok.strip().rstrip(",") for tok in m.group(1).split() if tok.strip()}

    required = {
        "apply_dark_layout",
        "badge",
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
# tab_assistant has 0 inline divs and a chunk of inline spans inside the
# answer HTML strings (`_build_response`). Caps lock today's totals.

_INLINE_DIV_BUDGET:  int = 0   # zero today — the chat HTML uses class names
_INLINE_SPAN_BUDGET: int = 44  # _sans + per-answer colour highlights


def test_inline_div_count_within_documented_budget() -> None:
    """Cap inline ``<div style=`` count — currently zero."""
    src = Path("ui/tab_assistant.py").read_text(encoding="utf-8")
    hits = re.findall(r"<div(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_DIV_BUDGET, (
        f"Found {len(hits)} inline <div style=...> blocks — budget is "
        f"{_INLINE_DIV_BUDGET}. The chat HTML uses class names; new inline "
        "divs must use ui.styles helpers instead."
    )


def test_inline_span_count_within_documented_budget() -> None:
    """Cap inline ``<span style=`` count at today's intentional total.

    The bulk lives inside `_build_response` answer text — deliberate
    colour emphasis on metrics inside the rendered answers — plus the
    `_sans` cell-content helper.
    """
    src = Path("ui/tab_assistant.py").read_text(encoding="utf-8")
    hits = re.findall(r"<span(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) <= _INLINE_SPAN_BUDGET, (
        f"Found {len(hits)} inline <span style=...> blocks — budget is "
        f"{_INLINE_SPAN_BUDGET}. The existing micro-helpers (_sans + "
        "answer-text colour highlights) cover the documented cases."
    )


# ── 5. Question classifier ────────────────────────────────────────────────

def test_classify_question_matches_canonical_categories() -> None:
    """Each keyword family maps to the right category label."""
    from ui.tab_assistant import _classify_question

    cases = [
        ("What are current Asia-Europe freight rates?", "Freight Rates"),
        ("How does the BDI look today?",                 "BDI / Dry Bulk"),
        ("Tell me about Red Sea Houthi attacks",         "Red Sea"),
        ("Panama Canal drought update?",                 "Panama Canal"),
        ("Which shipping stock has a LONG signal?",      "Equity Signals"),
        ("What's the Q2 2026 outlook for shipping?",     "Outlook"),
        ("Compare carrier schedule reliability",         "Carrier Reliability"),
        ("Tell me a joke",                               "General"),
        ("",                                             "General"),
    ]
    for question, expected in cases:
        assert _classify_question(question) == expected, (
            f"_classify_question({question!r}) returned the wrong category"
        )


# ── 6. Topic-distribution builder ─────────────────────────────────────────

def test_topic_distribution_empty_messages_returns_annotated_figure() -> None:
    """No user messages → annotated placeholder figure (no traces)."""
    import plotly.graph_objects as go
    from ui.tab_assistant import _build_topic_distribution_bars

    fig = _build_topic_distribution_bars([])
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0
    assert any("No questions" in (a.text or "")
               for a in fig.layout.annotations)

    fig_assist_only = _build_topic_distribution_bars([
        {"role": "assistant", "content": "Hi", "ts": "x"},
    ])
    assert any("No questions" in (a.text or "")
               for a in fig_assist_only.layout.annotations)


def test_topic_distribution_counts_only_user_messages() -> None:
    """Assistant messages are skipped; only user questions count."""
    from ui.tab_assistant import _build_topic_distribution_bars

    messages = [
        {"role": "user",      "content": "What's the BDI?",                   "ts": "1"},
        {"role": "assistant", "content": "BDI is at ...",                    "ts": "2"},
        {"role": "user",      "content": "Panama Canal status?",             "ts": "3"},
        {"role": "user",      "content": "freight rate Asia-Europe today?",  "ts": "4"},
    ]
    fig = _build_topic_distribution_bars(messages)
    # 3 user questions → 3 categories (BDI, Panama, Freight)
    bar = fig.data[0]
    assert sum(bar.x) == 3
    assert set(bar.y) == {"BDI / Dry Bulk", "Panama Canal", "Freight Rates"}


def test_topic_distribution_orders_top_category_at_chart_top() -> None:
    """Most-asked category must render at the top of the bar chart.

    Plotly stacks categorical y-values bottom-up, so the builder sorts
    ascending and lets Plotly invert. This test pins that contract.
    """
    from ui.tab_assistant import _build_topic_distribution_bars

    messages = [
        {"role": "user", "content": "freight rate?",         "ts": "1"},
        {"role": "user", "content": "freight rate Q2?",      "ts": "2"},
        {"role": "user", "content": "asia-europe rate?",     "ts": "3"},
        {"role": "user", "content": "BDI today?",            "ts": "4"},
    ]
    fig = _build_topic_distribution_bars(messages)
    y_labels = list(fig.data[0].y)
    # Bottom-most (smallest count) is BDI; top-most (largest) is Freight Rates
    assert y_labels[0]  == "BDI / Dry Bulk"
    assert y_labels[-1] == "Freight Rates"


def test_topic_distribution_top_category_highlighted_in_accent() -> None:
    """The leading category gets ``C_ACCENT``; the rest stay neutral."""
    from ui.styles import C_ACCENT, C_TEXT2
    from ui.tab_assistant import _build_topic_distribution_bars

    messages = [
        {"role": "user", "content": "freight rate Q2 2026?", "ts": "1"},
        {"role": "user", "content": "BDI?",                  "ts": "2"},
        {"role": "user", "content": "BDI panamax?",          "ts": "3"},
    ]
    fig = _build_topic_distribution_bars(messages)
    colors = list(fig.data[0].marker.color)
    # BDI has 2 hits, Freight has 1 — BDI is the top → highlighted
    y_labels = list(fig.data[0].y)
    bdi_idx     = y_labels.index("BDI / Dry Bulk")
    freight_idx = y_labels.index("Freight Rates")
    assert colors[bdi_idx]     == C_ACCENT
    assert colors[freight_idx] == C_TEXT2
