"""Tests for ui.tab_operator_overview — the at-a-glance operator dashboard.

Coverage:
  - Smoke: module imports cleanly with the streamlit mock + render() runs.
  - Each panel renders WITHOUT crashing when its upstream returns empty.
  - Each panel renders WITHOUT crashing when its upstream returns populated
    synthetic data.
  - A single panel raising an exception does NOT take down the whole tab —
    the remaining panels still render.

All tests use the ``mock_streamlit`` fixture from conftest.py so no real
Streamlit runtime is needed. Engine helpers are monkeypatched so this test
file is hermetic — no SQLite, no LLM, no network.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


# ─── Helper: reload the tab under the active streamlit mock ─────────────────

def _reload_tab():
    """Import-or-reload so we pick up the active mock_streamlit fixture
    even when an earlier test imported the module under the real
    streamlit."""
    if "ui.tab_operator_overview" in sys.modules:
        return importlib.reload(sys.modules["ui.tab_operator_overview"])
    return importlib.import_module("ui.tab_operator_overview")


# ─── Synthetic upstream payloads (shaped to match the real engines) ─────────

def _synth_alert(severity: str = "HIGH", alert_id: str = "a1"):
    """A ShippingAlert-shaped object — SimpleNamespace is fine because the
    tab reads attributes via getattr()."""
    return SimpleNamespace(
        alert_id=alert_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        alert_type="RATE_SURGE",
        severity=severity,
        title=f"Test alert {alert_id}",
        body="Synthetic test body.",
        ticker="",
        route_id="",
        port_locode="",
        value=1.0,
        threshold=0.5,
        change_pct=10.0,
        acknowledged=False,
    )


def _synth_channel(name: str = "ops", enabled: bool = True):
    return SimpleNamespace(
        channel_id=f"c-{name}",
        name=name,
        kind="slack",
        target="https://hooks.slack.example/x",
        severity_threshold="HIGH",
        enabled=enabled,
    )


def _synth_incident(alert_count: int = 2, severity: str = "HIGH"):
    return SimpleNamespace(
        incident_id="inc-1",
        started_at=datetime.now(timezone.utc).isoformat(),
        severity_max=severity,
        alert_count=alert_count,
        dominant_alert_type="RATE_SURGE",
        alerts=[],
        entities_touched={"tickers": [], "routes": [], "ports": []},
    )


def _synth_audit_event(action: str = "login"):
    return SimpleNamespace(
        event_id="ev-1",
        created_at=datetime.now(timezone.utc).isoformat(),
        user_id="user-x",
        action=action,
        entity_type="auth",
        entity_id="some-id-1234567890",
        detail_json={},
    )


def _synth_llm_summary() -> dict:
    return {
        "window_days":      7,
        "total_calls":      42,
        "total_tokens_in":  10_000,
        "total_tokens_out": 2_500,
        "total_cost_usd":   3.14,
        "by_source": {
            "commentary": {"calls": 30, "tokens_in": 6000, "tokens_out": 1500, "cost": 2.10},
            "narration":  {"calls": 12, "tokens_in": 4000, "tokens_out": 1000, "cost": 1.04},
        },
        "by_model": {
            "claude-haiku": {"calls": 42, "cost": 3.14},
        },
        "by_day": [],
    }


def _synth_perf_summary() -> dict:
    return {
        "window_hours":  24,
        "total_renders": 100,
        "success_rate":  0.98,
        "by_tab": {
            "overview":  {"count": 50, "median_ms": 25, "p95_ms": 80, "error_count": 0},
            "alerts":    {"count": 30, "median_ms": 120, "p95_ms": 400, "error_count": 1},
            "portfolio": {"count": 20, "median_ms": 200, "p95_ms": 900, "error_count": 0},
        },
        "top_slow_tabs": [],
    }


def _synth_health_summary() -> dict:
    return {
        "window_hours": 24,
        "total_pings":  72,
        "by_source": {
            "fred":     {"count": 24, "up_count": 22, "degraded_count": 1, "down_count": 1,
                         "avg_duration_ms": 312.5, "last_status": "up",
                         "last_started_at": "2026-05-22T12:00:00+00:00"},
            "yfinance": {"count": 24, "up_count": 0, "degraded_count": 0, "down_count": 24,
                         "avg_duration_ms": 9999, "last_status": "down",
                         "last_started_at": "2026-05-22T11:30:00+00:00"},
        },
        "current_outages": ["yfinance"],
    }


# ─── Smoke: import + render ────────────────────────────────────────────────

def test_module_imports_cleanly(mock_streamlit) -> None:
    """Module must import under the streamlit mock and expose render()."""
    mod = _reload_tab()
    assert hasattr(mod, "render"), "tab_operator_overview missing render()"
    assert callable(mod.render)


def test_render_empty_upstreams_does_not_crash(mock_streamlit, monkeypatch) -> None:
    """When every engine returns nothing, the tab must still render — every
    panel falls back to its own ``st.info(...)``."""
    mod = _reload_tab()
    monkeypatch.setattr(mod, "_load_alerts",        lambda: [])
    monkeypatch.setattr(mod, "_load_channels",      lambda: [])
    monkeypatch.setattr(mod, "_load_incidents",     lambda window_days=7: [])
    monkeypatch.setattr(mod, "_load_llm_summary",   lambda days=7: {})
    monkeypatch.setattr(mod, "_load_perf_summary",  lambda window_hours=24: {})
    monkeypatch.setattr(mod, "_load_source_health", lambda window_hours=24: {})
    monkeypatch.setattr(mod, "_load_audit_events",  lambda limit=20: [])

    # Must not raise.
    mod.render()


def test_render_populated_upstreams_does_not_crash(mock_streamlit, monkeypatch) -> None:
    """With realistic synthetic payloads on every loader, render() succeeds."""
    mod = _reload_tab()
    monkeypatch.setattr(mod, "_load_alerts",
                        lambda: [_synth_alert("CRITICAL", "a1"),
                                 _synth_alert("HIGH", "a2"),
                                 _synth_alert("MEDIUM", "a3")])
    monkeypatch.setattr(mod, "_load_channels",
                        lambda: [_synth_channel("ops", True),
                                 _synth_channel("offhours", False)])
    monkeypatch.setattr(mod, "_load_incidents",
                        lambda window_days=7: [_synth_incident(3, "CRITICAL")])
    monkeypatch.setattr(mod, "_load_llm_summary",   lambda days=7: _synth_llm_summary())
    monkeypatch.setattr(mod, "_load_perf_summary",  lambda window_hours=24: _synth_perf_summary())
    monkeypatch.setattr(mod, "_load_source_health",
                        lambda window_hours=24: _synth_health_summary())
    monkeypatch.setattr(mod, "_load_audit_events",
                        lambda limit=20: [_synth_audit_event()])

    mod.render()


# ─── Each panel must render empty fallback without crashing ────────────────

@pytest.mark.parametrize(
    ("loader_name", "empty_value"),
    [
        ("_load_alerts",        []),
        ("_load_channels",      []),
        ("_load_incidents",     []),
        ("_load_llm_summary",   {}),
        ("_load_perf_summary",  {}),
        ("_load_source_health", {}),
        ("_load_audit_events",  []),
    ],
)
def test_individual_panel_empty(mock_streamlit, monkeypatch,
                                loader_name: str, empty_value) -> None:
    """For each panel in isolation, the empty-upstream code path must not
    raise. We stub every OTHER loader to a populated default and stub the
    target loader to empty so we exercise the empty branch under realistic
    conditions for the rest of the tab."""
    mod = _reload_tab()
    populated = {
        "_load_alerts":        lambda: [_synth_alert()],
        "_load_channels":      lambda: [_synth_channel()],
        "_load_incidents":     lambda window_days=7: [_synth_incident()],
        "_load_llm_summary":   lambda days=7: _synth_llm_summary(),
        "_load_perf_summary":  lambda window_hours=24: _synth_perf_summary(),
        "_load_source_health": lambda window_hours=24: _synth_health_summary(),
        "_load_audit_events":  lambda limit=20: [_synth_audit_event()],
    }
    for name, fn in populated.items():
        monkeypatch.setattr(mod, name, fn)

    # Override just the target loader to return the empty value, ignoring
    # whatever kwargs the real engine signature accepts.
    monkeypatch.setattr(mod, loader_name, lambda *a, **kw: empty_value)

    mod.render()


# ─── A single panel exception must NOT crash the whole tab ─────────────────

def test_one_panel_failure_does_not_break_others(mock_streamlit, monkeypatch) -> None:
    """If a single engine helper raises, the tab continues rendering — that's
    the contract documented in the module docstring."""
    mod = _reload_tab()

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic upstream failure")

    # Break source-health while every other loader returns populated data.
    monkeypatch.setattr(mod, "_load_alerts",        lambda: [_synth_alert()])
    monkeypatch.setattr(mod, "_load_channels",      lambda: [_synth_channel()])
    monkeypatch.setattr(mod, "_load_incidents",     lambda window_days=7: [_synth_incident()])
    monkeypatch.setattr(mod, "_load_llm_summary",   lambda days=7: _synth_llm_summary())
    monkeypatch.setattr(mod, "_load_perf_summary",  lambda window_hours=24: _synth_perf_summary())
    monkeypatch.setattr(mod, "_load_source_health", _boom)
    monkeypatch.setattr(mod, "_load_audit_events",  lambda limit=20: [_synth_audit_event()])

    # Must not raise — the broken panel logs + warns, the rest render.
    mod.render()


def test_panel_render_helper_failure_caught(mock_streamlit, monkeypatch) -> None:
    """If a panel-render helper itself raises (not just the loader), the
    surrounding try/except in render() must swallow it and render the
    `st.warning(...)` fallback."""
    mod = _reload_tab()
    monkeypatch.setattr(mod, "_load_alerts",        lambda: [_synth_alert()])
    monkeypatch.setattr(mod, "_load_channels",      lambda: [_synth_channel()])
    monkeypatch.setattr(mod, "_load_incidents",     lambda window_days=7: [])
    monkeypatch.setattr(mod, "_load_llm_summary",   lambda days=7: {})
    monkeypatch.setattr(mod, "_load_perf_summary",  lambda window_hours=24: {})
    monkeypatch.setattr(mod, "_load_source_health", lambda window_hours=24: {})
    monkeypatch.setattr(mod, "_load_audit_events",  lambda limit=20: [])

    def _boom_panel(*args, **kwargs):
        raise ValueError("synthetic panel render failure")

    monkeypatch.setattr(mod, "_render_alerts_panel", _boom_panel)
    mod.render()


# ─── Status-color helper sanity ────────────────────────────────────────────

def test_status_color_maps_known_statuses(mock_streamlit) -> None:
    """Lock the colour mapping — used by the source-health panel pills."""
    mod = _reload_tab()
    # Map every supported status to ONE of the colour constants.
    assert mod._status_color("up") == mod.C_HIGH
    assert mod._status_color("healthy") == mod.C_HIGH
    assert mod._status_color("degraded") == mod.C_MOD
    assert mod._status_color("warning") == mod.C_MOD
    assert mod._status_color("down") == mod.C_LOW
    assert mod._status_color("failed") == mod.C_LOW
    # Unknown / empty falls back to the muted text colour.
    assert mod._status_color("") == mod.C_TEXT3
    assert mod._status_color("???") == mod.C_TEXT3
