"""Smoke test for the canonical refactored tab.

This is the representative UI test: the one tab we've committed to as the
reference implementation should render without exception on fixture data.
Other tabs will follow once they adopt the pattern in Phase 2.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def streamlit_stub(monkeypatch):
    """Stub st.* methods that the tab calls at render time.

    We can't run Streamlit's full runtime inside pytest, so we patch in a
    no-op module surface that silently absorbs markdown/columns/info/etc.
    """
    import streamlit as st

    stub = MagicMock()
    stub.columns.return_value = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    # Mirror a few attributes the tab reads directly
    for attr in ("markdown", "error", "info", "write", "dataframe"):
        monkeypatch.setattr(st, attr, stub, raising=False)
    monkeypatch.setattr(st, "columns", stub.columns, raising=False)
    yield stub


def test_refactored_tab_renders_without_data(streamlit_stub) -> None:
    """Empty freight_data should be handled gracefully (st.info path)."""
    from ui import tab_rate_analytics_refactored
    # freight_data = {} should hit the `st.info("No freight data...")` branch.
    tab_rate_analytics_refactored.render(freight_data={}, route_results=[])
    # No exception → success.
