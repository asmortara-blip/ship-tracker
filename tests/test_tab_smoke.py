"""Smoke tests for UI tab modules — verify they import and render without
crashing on empty/minimal inputs.

UI tabs are mostly side-effecting Streamlit render code. We don't unit-test
the layout; we just verify:
  1. The module imports cleanly (catches broken imports during engine/
     processing refactors).
  2. The tab's `render(...)` function does NOT raise on empty data
     (catches missing-key bugs, None dereferencing, broken defaults).
  3. With minimal-but-populated synthetic data, render() still completes
     (catches type errors in the data-shaping happy path).

The `mock_streamlit` fixture in conftest.py injects a no-op streamlit
module before each test so we don't need a real Streamlit runtime.

Each tab gets a minimal pair of tests — one with empty inputs, one with
populated synthetic inputs. We use `*args, **kwargs` to call render()
since the signatures vary; the wildcard catches every public parameter.
"""
from __future__ import annotations

import importlib

import pandas as pd
import pytest


# Tabs covered by this file — listed once so adding a new tab is one line.
# Pairs of (module_path, populated-call args/kwargs dict). Empty-call uses
# the empty dict.
_TAB_MODULES = [
    "ui.tab_alerts",
    "ui.tab_alpha",
    "ui.tab_assistant",
    "ui.tab_attribution",
    "ui.tab_backtest",
    "ui.tab_bellwethers",
    "ui.tab_booking",
    "ui.tab_briefing",
    "ui.tab_bunker",
    "ui.tab_cargo",
    "ui.tab_carriers",
    "ui.tab_chokepoints",
    "ui.tab_commentary",
    "ui.tab_compliance",
    "ui.tab_congestion",
    "ui.tab_convergence",
    "ui.tab_cycle",
    "ui.tab_data_health",
    "ui.tab_deep_dive",
    "ui.tab_derivatives",
    "ui.tab_disruption_radar",
    "ui.tab_ecommerce",
    "ui.tab_emerging_routes",
    "ui.tab_equipment",
    "ui.tab_equity_signals",
    "ui.tab_eta",
    "ui.tab_finance",
    "ui.tab_fleet",
    "ui.tab_fundamentals",
    "ui.tab_geopolitical",
    "ui.tab_idea_engine",
    "ui.tab_indices",
    "ui.tab_intermodal",
    "ui.tab_live_feed",
    "ui.tab_macro",
    "ui.tab_macro_projection",
    "ui.tab_markets",
    "ui.tab_monte_carlo",
    "ui.tab_network",
    "ui.tab_news",
    "ui.tab_nowcast",
    "ui.tab_operator",
    "ui.tab_operator_overview",
    "ui.tab_options",
    "ui.tab_overview",
    "ui.tab_port_demand",
    "ui.tab_port_monitor",
    "ui.tab_portfolio",
    "ui.tab_rate_analytics",
    "ui.tab_report",
    "ui.tab_results",
    "ui.tab_risk_lab",
    "ui.tab_risk_matrix",
    "ui.tab_routes",
    "ui.tab_rule_history",
    "ui.tab_scenarios",
    "ui.tab_scorecard",
    "ui.tab_sector",
    "ui.tab_setup",
    "ui.tab_supply_chain",
    "ui.tab_supply_linkage",
    "ui.tab_sustainability",
    "ui.tab_trade_flows",
    "ui.tab_trade_war",
    "ui.tab_vessel_map",
    "ui.tab_visibility",
    "ui.tab_voyage_tracker",
    "ui.tab_weather",
    "ui.tab_worker_health",
]


# ─── Module import smoke tests ─────────────────────────────────────────────

def _reload_tab(module_path: str):
    """Import-or-reload so the tab picks up the active mock_streamlit
    fixture, even if a prior test imported it under the real streamlit."""
    import sys
    if module_path in sys.modules:
        return importlib.reload(sys.modules[module_path])
    return importlib.import_module(module_path)


@pytest.mark.parametrize("module_path", _TAB_MODULES)
def test_tab_imports_cleanly(mock_streamlit, module_path: str) -> None:
    """Importing the tab module must not raise. Catches broken imports
    from engine/processing refactors."""
    mod = _reload_tab(module_path)
    assert hasattr(mod, "render"), f"{module_path} has no render() function"


# ─── Empty-input render smoke tests ────────────────────────────────────────

@pytest.mark.parametrize("module_path", _TAB_MODULES)
def test_tab_render_empty_does_not_crash(mock_streamlit, module_path: str) -> None:
    """Calling render() with None for every required arg must not raise.
    Some tabs use positional-required args without defaults; pass None
    for those so we still exercise the empty-data branch."""
    import inspect
    mod = _reload_tab(module_path)
    sig = inspect.signature(mod.render)
    required = {
        name: None
        for name, p in sig.parameters.items()
        if p.default is inspect.Parameter.empty
        and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                       inspect.Parameter.KEYWORD_ONLY)
    }
    mod.render(**required)


# ─── Populated-input render smoke tests ────────────────────────────────────

def _populated_data_bundle() -> dict:
    """Synthetic data shaped to match the most common tab inputs."""
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    return {
        "stock_data": {
            "ZIM": pd.DataFrame({"date": dates, "close": [10.0 + i * 0.1 for i in range(30)]}),
        },
        "freight_data": {
            "transpacific_eb": pd.DataFrame({
                "date": dates,
                "rate_usd_per_feu": [2000.0 + i * 5 for i in range(30)],
            }),
        },
        "macro_data": {
            "BSXRLM": pd.DataFrame({"date": dates, "value": [1500.0 + i for i in range(30)]}),
            "IPMAN": pd.DataFrame({"date": dates, "value": [100.0 + i * 0.1 for i in range(30)]}),
        },
        "port_results": [],
        "route_results": [],
        "insights": [],
    }


@pytest.mark.parametrize("module_path", _TAB_MODULES)
def test_tab_render_populated_does_not_crash(mock_streamlit, module_path: str) -> None:
    """Calling render() with a synthetic data bundle must not raise.
    Only pass kwargs the render() signature actually accepts — tabs vary
    on whether they take **kwargs."""
    import inspect
    mod = _reload_tab(module_path)
    bundle = _populated_data_bundle()
    sig = inspect.signature(mod.render)
    accepts_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if accepts_kwargs:
        kwargs = bundle
    else:
        kwargs = {k: v for k, v in bundle.items() if k in sig.parameters}
    mod.render(**kwargs)


def test_app_binds_stock_data_to_tab_alpha_by_keyword() -> None:
    """Regression: app.py dispatched tab_alpha.render POSITIONALLY in an order
    that bound route_results→stock_data (render's signature is stock_data-first),
    so the Alpha tab silently ran on the wrong data and its engine path always
    crashed to mock. The dispatch must bind stock_data explicitly by keyword."""
    import re
    from pathlib import Path
    src = Path("app.py").read_text(encoding="utf-8")
    m = re.search(
        r"from ui\.tab_alpha import render as _r\b.*?_r\((.*?)\)",
        src, re.DOTALL,
    )
    assert m, "tab_alpha render dispatch not found in app.py"
    call = m.group(1)
    assert "stock_data=stock_data" in call, (
        f"tab_alpha must receive stock_data by keyword (it drifted to a "
        f"positional call that misbound route_results→stock_data); got: {call!r}"
    )
