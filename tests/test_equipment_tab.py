"""Smoke test for tab_equipment.

The Equipment & Container Pools tab is large (11 render subsections); this
smoke test stubs the `st.*` surface and verifies `render(...)` doesn't raise
under empty / minimal data. It does NOT exercise visual fidelity — that's a
manual streamlit walkthrough.

Pattern mirrors `tests/test_rate_analytics_tab.py`.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def streamlit_stub(monkeypatch):
    """Stub the `st.*` methods that the tab + its helpers call at render time."""
    import streamlit as st

    # Generic mock returned by all stubbed methods.
    stub = MagicMock()
    # st.columns(n) — return n MagicMocks so `with cols[i]:` works.
    stub.columns.return_value = [MagicMock() for _ in range(8)]
    # st.tabs(labels) — same shape.
    stub.tabs.return_value = [MagicMock() for _ in range(8)]
    # Streamlit context manager protocol on returned mocks.
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
    monkeypatch.setattr(st, "tabs", stub.tabs, raising=False)
    yield stub


def test_tab_renders_without_data(streamlit_stub) -> None:
    """Empty inputs should not raise — every subsection is exception-wrapped."""
    from ui import tab_equipment

    tab_equipment.render(route_results=[], freight_data={}, macro_data={})


def test_tab_renders_with_minimal_data(streamlit_stub) -> None:
    """A minimal payload should also pass through cleanly."""
    from ui import tab_equipment

    tab_equipment.render(
        route_results=[],
        freight_data={"baltic_dry_index": 1500.0},
        macro_data={"china_pmi": 51.0},
    )
