"""Refactor lock-in tests for tab_equipment.

These tests guard the design-system migration of ``ui/tab_equipment.py``:
the tab must keep rendering cleanly while routing all styling through
``ui.styles`` helpers (no inline ``<div style=...`` blocks reintroduced,
the canonical helpers actually invoked, etc.).

Companion to ``tests/test_equipment_tab.py`` — that one is a thin smoke
test; this one is a *structural* test enforcing the audit baseline gain
doesn't silently regress.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ── Streamlit stub (same shape as tests/test_equipment_tab.py) ──────────────

@pytest.fixture
def streamlit_stub(monkeypatch):
    """Stub the `st.*` surface so the tab renders headlessly."""
    import streamlit as st

    stub = MagicMock()
    # n columns / tabs — context-manager protocol so `with cols[i]:` works.
    stub.columns.return_value = [MagicMock() for _ in range(8)]
    stub.tabs.return_value    = [MagicMock() for _ in range(8)]
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
    monkeypatch.setattr(st, "tabs",    stub.tabs,    raising=False)
    yield stub


# ── 1. Imports cleanly ─────────────────────────────────────────────────────

def test_module_imports_cleanly() -> None:
    """The refactored tab must still import without any error.

    Catches any leftover dangling references to helpers that were swapped
    out during the design-system migration.
    """
    import importlib
    import sys
    if "ui.tab_equipment" in sys.modules:
        importlib.reload(sys.modules["ui.tab_equipment"])
    else:
        importlib.import_module("ui.tab_equipment")

    from ui import tab_equipment
    assert callable(tab_equipment.render), "render() must remain callable"


# ── 2. Renders cleanly with empty data ─────────────────────────────────────

def test_render_with_empty_data_no_exception(streamlit_stub) -> None:
    """Every subsection is exception-wrapped — empty inputs cannot crash."""
    from ui import tab_equipment

    tab_equipment.render(route_results=[], freight_data={}, macro_data={})


# ── 3. Renders cleanly with seeded data ────────────────────────────────────

def test_render_with_seeded_data_no_exception(streamlit_stub) -> None:
    """A populated synthetic payload should also pass through cleanly."""
    import pandas as pd
    from ui import tab_equipment

    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    tab_equipment.render(
        route_results=[],
        freight_data={
            "baltic_dry_index": 1500.0,
            "transpacific_eb": pd.DataFrame({
                "date": dates,
                "rate_usd_per_feu": [2000.0 + i * 5 for i in range(30)],
            }),
        },
        macro_data={
            "china_pmi": 51.0,
            "BSXRLM":    pd.DataFrame({"date": dates,
                                       "value": [1500.0 + i for i in range(30)]}),
        },
    )


# ── 4. Canonical design-system helpers are wired in ────────────────────────

def test_imports_design_system_helpers() -> None:
    """tab_equipment must import the key helpers from ui.styles.

    These four are the load-bearing ones for the migration:
      - section_divider:  every visual mini-section header now routes through it
      - metric_card_row:  every KPI strip uses this row helper
      - live_data_badge:  surfaces provenance on the tab header
      - status_badge:     replaces the legacy inline-style color chips

    If any helper gets dropped during a future edit the file will still
    import — but the migration's contract would silently break. This test
    pins the contract.
    """
    src = Path("ui/tab_equipment.py").read_text(encoding="utf-8")
    # Match a multi-line `from ui.styles import (...)` block.
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)",
        src,
        re.DOTALL,
    )
    assert m, "tab_equipment must import from ui.styles via a parenthesized import block"
    imported = {tok.strip().rstrip(",") for tok in m.group(1).split() if tok.strip()}

    required = {
        "section_divider",
        "metric_card_row",
        "live_data_badge",
        "status_badge",
        "page_header",
    }
    missing = required - imported
    assert not missing, (
        f"Design-system helpers missing from ui.styles import block: {sorted(missing)}"
    )


# ── 5. No new inline `<div style=` blocks slip back in ────────────────────

def test_no_new_inline_div_style_blocks() -> None:
    """Lock in the audit win: no plain inline ``<div style=`` blocks remain.

    The migration removed every hand-rolled ``<div style="background:..."``
    /``<div style="display:flex..."`` block in favour of design-system
    helpers (gradient_card, metric_card_row, status_badge, section_divider,
    ...). This test fails fast if a future edit drops one back in.

    Note: the audit's full regex catches ``<span style=`` too — two
    intentional micro-helpers (``_mono``/``_sans`` for table cells) remain
    by design because they need per-cell color injection that the current
    CSS classes don't support. They are scoped to this file only and
    documented inline; if/when ``ui.styles`` grows a ``cell_mono(value,
    color)`` helper they should be removed.
    """
    src = Path("ui/tab_equipment.py").read_text(encoding="utf-8")
    # Only flag inline `<div style=`. Spans are tracked separately above.
    pattern = re.compile(r"<div(?:[^>]*\s)?style\s*=", re.IGNORECASE)
    hits = pattern.findall(src)
    assert hits == [], (
        f"Found {len(hits)} inline <div style=...> block(s) in tab_equipment.py — "
        "use a ui.styles helper (gradient_card, metric_card_row, section_divider, "
        "status_badge, insight_card_html) instead."
    )


# ── 6. Figure-builder: global equipment health bullet ─────────────────────

def test_health_bullet_returns_indicator_figure() -> None:
    """`_build_health_bullet` must return a plotly Figure with one Indicator."""
    import plotly.graph_objects as go
    from ui.tab_equipment import _build_health_bullet

    fig = _build_health_bullet(78.4)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    assert fig.data[0].type == "indicator"
    # Value must round-trip into the figure
    assert abs(float(fig.data[0].value) - 78.4) < 1e-6


def test_health_bullet_color_reflects_regime() -> None:
    """Bar colour must follow the regime band the index falls into.

    Regimes (see processing.equipment_tracker.get_global_equipment_index
    docstring): <70 surplus → C_HIGH, 70–85 normal → C_MOD, >85 tight → C_LOW.
    """
    from ui.styles import C_HIGH, C_LOW, C_MOD
    from ui.tab_equipment import _build_health_bullet, _health_regime

    assert _health_regime(55.0) == ("Surplus", C_HIGH)
    assert _health_regime(78.0) == ("Normal",  C_MOD)
    assert _health_regime(91.0) == ("Tight",   C_LOW)

    # Boundary: exactly 85 should land in "Tight" (the docstring says >85
    # is tight, but the upper-band edge needs to belong to one regime).
    assert _health_regime(85.0)[0] == "Tight"

    # Bar colour propagates into the figure
    fig = _build_health_bullet(91.0)
    assert fig.data[0].gauge.bar.color == C_LOW


def test_health_bullet_clamps_out_of_range() -> None:
    """A bogus index value should clamp into [0, 100] rather than crash."""
    from ui.tab_equipment import _build_health_bullet

    fig_lo = _build_health_bullet(-15.0)
    fig_hi = _build_health_bullet(200.0)
    assert float(fig_lo.data[0].value) == 0.0
    assert float(fig_hi.data[0].value) == 100.0


# ── 7. Figure-builder: alert severity lollipop ───────────────────────────

def test_severity_lollipop_empty_alerts_returns_annotated_figure() -> None:
    """Empty alert list must yield a non-crashing annotated-empty figure."""
    import plotly.graph_objects as go
    from ui.tab_equipment import _build_severity_lollipop

    fig = _build_severity_lollipop([])
    assert isinstance(fig, go.Figure)
    # No data traces, but the "No active alerts" annotation is present.
    assert len(fig.data) == 0
    assert any("No active alerts" in (a.text or "") for a in fig.layout.annotations)


def test_severity_lollipop_orders_highest_severity_at_top() -> None:
    """The y-axis must read top-down by severity (highest at the top).

    Plotly stacks categorical y-values bottom-up, so the builder sorts
    *ascending* by score and lets Plotly invert it. This test pins that
    contract so a future "let's sort descending" refactor cannot silently
    flip the visual reading order.
    """
    from ui.tab_equipment import _build_severity_lollipop

    alerts = [
        {"region": "Asia Pacific",  "type": "40FT DRY",   "risk": "HIGH",     "score": 62.0},
        {"region": "North America", "type": "40FT REEFER","risk": "CRITICAL", "score": 88.5},
        {"region": "Europe",        "type": "20FT DRY",   "risk": "MODERATE", "score": 45.0},
    ]
    fig = _build_severity_lollipop(alerts)
    # Single scatter trace carries the markers
    scatter = [t for t in fig.data if t.type == "scatter"]
    assert len(scatter) == 1
    y_labels = list(scatter[0].y)
    # Bottom of the y-list is the lowest score; top is the highest.
    assert "Europe"        in y_labels[0]
    assert "North America" in y_labels[-1]


def test_severity_lollipop_marker_colors_match_risk() -> None:
    """CRITICAL → C_LOW, HIGH → C_MOD, MODERATE → C_ACCENT, LOW → C_HIGH."""
    from ui.styles import C_ACCENT, C_HIGH, C_LOW, C_MOD
    from ui.tab_equipment import _build_severity_lollipop

    alerts = [
        {"region": "R1", "type": "T", "risk": "CRITICAL", "score": 90.0},
        {"region": "R2", "type": "T", "risk": "HIGH",     "score": 70.0},
        {"region": "R3", "type": "T", "risk": "MODERATE", "score": 50.0},
        {"region": "R4", "type": "T", "risk": "LOW",      "score": 20.0},
    ]
    fig = _build_severity_lollipop(alerts)
    scatter = [t for t in fig.data if t.type == "scatter"][0]
    # Ascending sort → LOW at bottom (index 0), CRITICAL at top (index -1).
    colors = list(scatter.marker.color)
    assert colors[0]  == C_HIGH   # LOW risk
    assert colors[1]  == C_ACCENT # MODERATE
    assert colors[2]  == C_MOD    # HIGH
    assert colors[-1] == C_LOW    # CRITICAL


# ── 8. The remaining inline spans are exactly the documented helpers ──────

def test_only_documented_inline_spans_remain() -> None:
    """Audit gate: the only ``<span style=`` instances allowed are the
    table-cell micro-helpers (``_mono``/``_sans``). If a future edit adds
    a third, this test fails — forcing it through ui.styles or an
    explicit follow-up to lift the helper into the design system.
    """
    src = Path("ui/tab_equipment.py").read_text(encoding="utf-8")
    pattern = re.compile(r"<span(?:[^>]*\s)?style\s*=", re.IGNORECASE)
    hits = pattern.findall(src)
    # Exactly 2 by design (one inside _mono, one inside _sans). If a future
    # design-system helper subsumes them, drop to 0 and update this test.
    assert len(hits) <= 2, (
        f"Found {len(hits)} inline <span style=> blocks (expected at most 2 "
        "from the _mono/_sans table-cell micro-helpers). New ones must "
        "route through ui.styles instead."
    )
