"""Tests for the Instrument Tearsheet tab (R013) + its ?entity= URL routing.

Mirrors the tab-smoke philosophy: we don't assert on layout, only that the
module imports, that render() does not crash on empty OR seeded inputs, that the
empty-state path is honest, and that the ?entity= deep-link round-trips through
``ui.url_state``.

The ``mock_streamlit`` fixture (conftest.py) injects a no-op streamlit module so
render() runs without a Streamlit runtime. Its mock ``selectbox`` returns
``options[index]``, so seeding ``st.session_state['active_entity']`` to a valid
ticker (which sets the selectbox index to that ticker) deterministically drives
the populated render path.
"""
from __future__ import annotations

import importlib

import pandas as pd
import pytest


# ─── url_state: ?entity= round-trip (no streamlit needed) ──────────────────

def test_entity_url_query_uppercases_and_trims():
    from ui.url_state import entity_url_query, ENTITY_QUERY_KEY

    q = entity_url_query("  zim ")
    assert q == {ENTITY_QUERY_KEY: "ZIM"}


def test_resolve_active_entity_round_trip():
    """set (entity_url_query) → read back (resolve_active_entity)."""
    from ui.url_state import entity_url_query, resolve_active_entity

    query = entity_url_query("matx")  # what a share-link would carry
    # No validation set → accepted as-is (uppercased).
    assert resolve_active_entity(query) == "MATX"
    # Validated against a universe containing it → kept.
    assert resolve_active_entity(query, ["ZIM", "MATX"]) == "MATX"


def test_resolve_active_entity_rejects_unknown_against_universe():
    from ui.url_state import resolve_active_entity

    q = {"entity": "NOPE"}
    assert resolve_active_entity(q, ["ZIM", "MATX"]) == ""
    assert resolve_active_entity(q, ["ZIM", "MATX"], default="ZIM") == "ZIM"


def test_resolve_active_entity_missing_or_empty():
    from ui.url_state import resolve_active_entity

    assert resolve_active_entity(None) == ""
    assert resolve_active_entity({}) == ""
    assert resolve_active_entity({"section": "markets"}) == ""  # no entity key


def test_resolve_active_entity_unwraps_list_param():
    from ui.url_state import resolve_active_entity

    # Streamlit can surface a repeated param as a list.
    assert resolve_active_entity({"entity": ["zim", "matx"]}) == "ZIM"


# ─── tab import + render smoke ─────────────────────────────────────────────

def _reload_tab():
    import sys
    if "ui.tab_tearsheet" in sys.modules:
        return importlib.reload(sys.modules["ui.tab_tearsheet"])
    return importlib.import_module("ui.tab_tearsheet")


def test_tearsheet_imports_and_has_render(mock_streamlit):
    mod = _reload_tab()
    assert hasattr(mod, "render")


def test_tearsheet_render_empty_no_entity_does_not_crash(mock_streamlit):
    """Empty inputs + no active_entity → honest empty-state, no crash.

    With no active_entity the selectbox index is 0 (the '— select —'
    placeholder), so _resolve_ticker returns "" and render must short-circuit
    to the empty-state without raising.
    """
    mod = _reload_tab()
    mod.render(stock_data={}, macro_data={}, insights=[])


def test_tearsheet_render_none_args_does_not_crash(mock_streamlit):
    """All-None args (the smoke harness path) must not raise."""
    mod = _reload_tab()
    mod.render(stock_data=None, macro_data=None, insights=None)


def test_tearsheet_render_seeded_entity_does_not_crash(mock_streamlit):
    """A seeded active_entity + a seeded stock_data renders the full page.

    Seeding active_entity='ZIM' makes _resolve_ticker pick ZIM (the mock
    selectbox returns options[index] and the index points at ZIM), exercising
    every section: profile, price, cascade idea, exposure, alerts.
    """
    mock_streamlit.session_state["active_entity"] = "ZIM"

    dates = pd.date_range("2024-01-01", periods=40, freq="D")
    stock_data = {
        "ZIM": pd.DataFrame(
            {"date": dates, "close": [10.0 + i * 0.1 for i in range(40)]}
        ),
    }
    macro_data = {
        "BSXRLM": pd.DataFrame({"date": dates, "value": [1500.0 + i for i in range(40)]}),
    }

    mod = _reload_tab()
    mod.render(stock_data=stock_data, macro_data=macro_data, insights=[])


def test_tearsheet_universe_nonempty():
    """The tracked universe drives the selectbox; it must be populated so the
    deep-link + fallback have something to resolve against."""
    mod = importlib.import_module("ui.tab_tearsheet")
    universe = mod._tracked_universe()
    assert universe, "tearsheet universe should be non-empty"
    assert "ZIM" in universe
