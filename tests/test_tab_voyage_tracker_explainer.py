"""Targeted tests for the voyage-explanations panel in
``ui.tab_voyage_tracker``.

The shared parametrized ``tests/test_tab_smoke.py`` already covers the tab's
``render(...)`` happy-path. These tests pin behaviour *specific to* the new
explainer wiring:

* ``_render_voyage_explanations`` with an empty list → graceful caption,
  no expander.
* All-on-schedule fleet → same caption (the explainer filters voyages with
  delay <= 1 day so a calm fleet yields []).
* Delayed-fleet input → renders cards (helper must not raise).
* Tab still renders end-to-end when the explainer module raises — defense
  in depth.
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import List

import pytest


# ── Lightweight fake — mirrors the platform's Voyage shape by attribute name. ─


@dataclass
class _FakeVoyage:
    voyage_id: str = "VY-TEST-01"
    vessel_name: str = "EVERGREEN STAR"
    route_id: str = "asia_europe"
    status: str = "Major Delay"
    delay_days: float = 6.5
    weather_delay_days: float = 0.0
    congestion_at_dest: float = 0.4
    chokepoints_on_route: List[str] = field(default_factory=list)


def _reload_tab():
    """Re-import the tab module so the active ``mock_streamlit`` fixture
    is the streamlit module the tab sees."""
    mod_path = "ui.tab_voyage_tracker"
    if mod_path in sys.modules:
        return importlib.reload(sys.modules[mod_path])
    return importlib.import_module(mod_path)


# ── Smoke: helpers are surfaced on the tab module ──────────────────────────


def test_tab_voyage_tracker_imports_with_explainer_helpers(mock_streamlit) -> None:
    """Importing the tab must surface the new helpers as module attributes."""
    mod = _reload_tab()
    assert hasattr(mod, "render")
    assert hasattr(mod, "_render_voyage_explanations")
    assert hasattr(mod, "_render_one_voyage_explanation")


# ── _render_voyage_explanations: empty input ───────────────────────────────


def test_render_voyage_explanations_empty_does_not_raise(mock_streamlit) -> None:
    """Empty fleet → quiet caption, no crash."""
    mod = _reload_tab()
    result = mod._render_voyage_explanations([])
    assert result is None


# ── _render_voyage_explanations: on-schedule fleet ─────────────────────────


def test_render_voyage_explanations_on_schedule_fleet_does_not_raise(
    mock_streamlit,
) -> None:
    """Fleet with no material delays (delay <= 1 day) → explainer returns []
    and we surface the 'running on schedule' caption."""
    mod = _reload_tab()
    calm_fleet = [
        _FakeVoyage(voyage_id="VY-A", vessel_name="A", status="On Schedule",
                    delay_days=0.0),
        _FakeVoyage(voyage_id="VY-B", vessel_name="B", status="On Schedule",
                    delay_days=0.5),
    ]
    result = mod._render_voyage_explanations(calm_fleet)
    assert result is None


# ── _render_voyage_explanations: delayed fleet renders cards ───────────────


def test_render_voyage_explanations_delayed_renders_cards(mock_streamlit) -> None:
    """Mixed delay causes → cards rendered, no exception. Each card hits a
    different attribution branch (weather / congestion / chokepoint)."""
    mod = _reload_tab()
    fleet = [
        _FakeVoyage(
            voyage_id="VY-WX",
            vessel_name="WEATHER ROYAL",
            status="Major Delay",
            delay_days=7.0,
            weather_delay_days=4.0,
        ),
        _FakeVoyage(
            voyage_id="VY-CG",
            vessel_name="CONGESTION QUEEN",
            status="Major Delay",
            delay_days=5.0,
            congestion_at_dest=0.85,
        ),
        _FakeVoyage(
            voyage_id="VY-CP",
            vessel_name="CHOKEPOINT KING",
            status="Major Delay",
            delay_days=9.5,
            chokepoints_on_route=["Suez Canal"],
        ),
    ]
    mod._render_voyage_explanations(fleet)


# ── Per-section exception isolation ────────────────────────────────────────


def test_render_voyage_explanations_explainer_raises_does_not_propagate(
    monkeypatch, mock_streamlit
) -> None:
    """If ``explain_delayed_voyages`` raises, helper degrades gracefully —
    no exception propagates."""
    mod = _reload_tab()

    from engine import disruption_explainer

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated explainer failure")

    monkeypatch.setattr(
        disruption_explainer, "explain_delayed_voyages", _boom
    )
    fleet = [
        _FakeVoyage(
            voyage_id="VY-CP",
            vessel_name="CHOKEPOINT KING",
            status="Major Delay",
            delay_days=9.5,
            chokepoints_on_route=["Suez Canal"],
        ),
    ]
    mod._render_voyage_explanations(fleet)  # MUST NOT RAISE
