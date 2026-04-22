"""Smoke test for tab_rate_analytics.

This tab is the playbook-compliant exemplar that other tabs migrate toward.
We can't run Streamlit's full runtime inside pytest, so we stub out the
`st.*` surface and just assert that `render(...)` raises nothing on an empty
freight_data payload (the `st.info("No freight data...")` code path).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def streamlit_stub(monkeypatch):
    """Stub the `st.*` methods the tab calls at render time."""
    import streamlit as st

    stub = MagicMock()
    stub.columns.return_value = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    for attr in ("markdown", "error", "info", "write", "dataframe"):
        monkeypatch.setattr(st, attr, stub, raising=False)
    monkeypatch.setattr(st, "columns", stub.columns, raising=False)
    yield stub


def test_tab_renders_without_data(streamlit_stub) -> None:
    """Empty freight_data should hit the graceful `st.info(...)` branch."""
    from ui import tab_rate_analytics

    tab_rate_analytics.render(freight_data={}, route_results=[])
