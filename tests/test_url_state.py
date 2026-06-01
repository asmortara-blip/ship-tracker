"""Tests for ui/url_state.py — section deep-linking logic (pure, offline)."""
from __future__ import annotations

from ui.url_state import (
    SECTION_QUERY_KEY,
    resolve_active_section,
    section_url_query,
)

_VALID = ["dashboard", "markets", "ports_routes", "risk"]


def test_valid_section_param_is_resolved() -> None:
    assert resolve_active_section({"section": "markets"}, _VALID) == "markets"


def test_missing_param_falls_back_to_default() -> None:
    assert resolve_active_section({}, _VALID) == "dashboard"
    assert resolve_active_section(None, _VALID) == "dashboard"
    assert resolve_active_section({"other": "x"}, _VALID) == "dashboard"


def test_unknown_section_falls_back_to_default() -> None:
    assert resolve_active_section({"section": "nope"}, _VALID) == "dashboard"
    assert resolve_active_section({"section": ""}, _VALID) == "dashboard"


def test_list_valued_param_takes_first() -> None:
    """A repeated ?section=a&section=b can surface as a list — take the first."""
    assert resolve_active_section({"section": ["risk", "markets"]}, _VALID) == "risk"
    assert resolve_active_section({"section": []}, _VALID) == "dashboard"


def test_whitespace_is_trimmed() -> None:
    assert resolve_active_section({"section": "  markets  "}, _VALID) == "markets"


def test_default_not_in_valid_uses_first_valid() -> None:
    """If the supplied default isn't a known key, fall back to a valid one
    (sorted-first) rather than returning an unroutable section."""
    out = resolve_active_section({}, ["markets", "risk"], default="dashboard")
    assert out in ("markets", "risk")
    assert out == "markets"  # sorted-first of the valid set


def test_empty_valid_set_returns_default() -> None:
    assert resolve_active_section({"section": "x"}, [], default="dashboard") == "dashboard"


def test_section_url_query_roundtrips() -> None:
    q = section_url_query("ports_routes")
    assert q == {SECTION_QUERY_KEY: "ports_routes"}
    assert resolve_active_section(q, _VALID) == "ports_routes"
