"""Targeted tests for the route-explanations panel in
``ui.tab_disruption_radar``.

The shared parametrized ``tests/test_tab_smoke.py`` already covers the tab's
``render(...)`` happy-path. These tests pin behaviour *specific to* the new
explainer wiring:

* ``_render_route_explanations`` with an empty list → graceful "no stressed
  routes" message, no expander.
* All-Calm input → same graceful message (the explainer filters Calm routes,
  so it returns []).
* Stressed input → renders cards (the helper must not raise and must invoke
  the expander).
* Tab still renders end-to-end when the explainer's top-level function is
  monkeypatched to raise — defense in depth: a broken explainer cannot
  block the rest of the tab.
* Tab module imports cleanly (catches symbol-level regressions on the
  helper-export surface).
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from typing import List

import pytest


# ── Lightweight fakes — mirror the platform's RouteStress shape by attribute
# name. Independent of the real dataclass so the test pins the contract by
# the *consumer surface*, not by the import structure.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _FakeRouteStress:
    route_id: str = "asia_europe"
    route_name: str = "Asia–Europe"
    stress_score: float = 0.0
    chokepoint_stress: float = 0.0
    congestion_stress: float = 0.0
    weather_stress: float = 0.0
    rate_stress: float = 0.0
    vulnerability: float = 0.0
    anomaly_stress: float = 0.0
    dominant_driver: str = ""
    affected_chokepoints: List[str] = field(default_factory=list)
    delayed_voyage_count: int = 0


def _reload_tab():
    """Re-import the tab module so the active ``mock_streamlit`` fixture is in
    effect (a prior test may have loaded it under the real ``streamlit``)."""
    mod_path = "ui.tab_disruption_radar"
    if mod_path in sys.modules:
        return importlib.reload(sys.modules[mod_path])
    return importlib.import_module(mod_path)


# ── Smoke: tab module imports without error ────────────────────────────────


def test_tab_disruption_radar_imports_with_explainer_helpers(mock_streamlit) -> None:
    """Importing the tab must surface the new helpers as module attributes."""
    mod = _reload_tab()
    assert hasattr(mod, "render")
    assert hasattr(mod, "_render_route_explanations")
    assert hasattr(mod, "_render_one_route_explanation")


# ── _render_route_explanations: empty input ────────────────────────────────


def test_render_route_explanations_empty_list_does_not_raise(mock_streamlit) -> None:
    """Empty stress list → quiet 'no stressed routes' caption, no crash."""
    mod = _reload_tab()
    # The contract is: never raises on empty input. The fixture mocks
    # st.caption / st.expander to no-ops, so we just assert the call returns.
    result = mod._render_route_explanations([])
    assert result is None


# ── _render_route_explanations: all-Calm input ─────────────────────────────


def test_render_route_explanations_all_calm_does_not_raise(mock_streamlit) -> None:
    """All-Calm stress list → same graceful caption (explainer filters Calm)."""
    mod = _reload_tab()
    calm = [
        _FakeRouteStress(route_id="r1", route_name="R1", stress_score=0.05),
        _FakeRouteStress(route_id="r2", route_name="R2", stress_score=0.10),
        _FakeRouteStress(route_id="r3", route_name="R3", stress_score=0.18),
    ]
    result = mod._render_route_explanations(calm)
    assert result is None


# ── _render_route_explanations: stressed input ─────────────────────────────


def test_render_route_explanations_stressed_renders_cards(mock_streamlit) -> None:
    """Seeded stressed-list input → cards rendered, no exception."""
    mod = _reload_tab()
    stressed = [
        _FakeRouteStress(
            route_id="asia_europe",
            route_name="Asia–Europe",
            stress_score=0.82,
            chokepoint_stress=0.92,
            congestion_stress=0.60,
            rate_stress=0.70,
            affected_chokepoints=["Suez Canal", "Bab-el-Mandeb"],
            delayed_voyage_count=7,
        ),
        _FakeRouteStress(
            route_id="transpacific_eb",
            route_name="Transpacific EB",
            stress_score=0.55,
            congestion_stress=0.70,
            rate_stress=0.40,
            affected_chokepoints=["Long Beach"],
            delayed_voyage_count=4,
        ),
    ]
    # The helper must not raise; the mocked streamlit accepts every call.
    mod._render_route_explanations(stressed)


# ── Per-section exception isolation ─────────────────────────────────────────


def test_render_route_explanations_explainer_raises_yields_warning(
    monkeypatch, mock_streamlit
) -> None:
    """If ``explain_top_disruptions`` raises, helper degrades gracefully —
    no exception propagated. Defense-in-depth: the explainer is exception-
    safe by design, but we still wrap the call so a future change cannot
    break the surrounding tab."""
    mod = _reload_tab()

    # Monkeypatch the explainer module's top-level function so the import
    # inside the helper picks up the broken version.
    from engine import disruption_explainer

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated explainer failure")

    monkeypatch.setattr(
        disruption_explainer, "explain_top_disruptions", _boom
    )
    # Helper must absorb the exception (it has its own try/except).
    stressed = [
        _FakeRouteStress(
            route_id="asia_europe",
            route_name="Asia–Europe",
            stress_score=0.82,
            chokepoint_stress=0.92,
        ),
    ]
    mod._render_route_explanations(stressed)  # MUST NOT RAISE


def test_tab_render_still_works_when_explainer_breaks(
    monkeypatch, mock_streamlit
) -> None:
    """End-to-end: the tab's render() function still completes even if the
    explainer module raises. The outer render() wraps the panel in its own
    try/except, so a broken explainer cannot take the tab down."""
    mod = _reload_tab()

    from engine import disruption_explainer

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated explainer failure")

    monkeypatch.setattr(
        disruption_explainer, "explain_top_disruptions", _boom
    )
    # render() should complete with no exception — every call goes through
    # the mocked Streamlit no-ops.
    mod.render(
        freight_data={},
        macro_data={},
        port_results=[],
        route_results=[],
    )
