"""Lock-in tests for ui/tab_port_supply_lines.py."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def streamlit_stub(monkeypatch):
    import streamlit as st

    def _make_col() -> MagicMock:
        col = MagicMock()
        col.__enter__ = MagicMock(return_value=col)
        col.__exit__  = MagicMock(return_value=False)
        return col

    def _columns_factory(spec, *_a, **_kw):
        if isinstance(spec, int):
            n = spec
        else:
            try:
                n = len(list(spec))
            except TypeError:
                n = 1
        return [_make_col() for _ in range(max(1, n))]

    stub = MagicMock()
    stub.columns.side_effect = _columns_factory
    stub.tabs.return_value = [_make_col() for _ in range(8)]

    expander_cm = MagicMock()
    expander_cm.__enter__ = MagicMock(return_value=expander_cm)
    expander_cm.__exit__  = MagicMock(return_value=False)
    stub.expander.return_value = expander_cm

    stub.text_input.return_value = ""
    stub.selectbox.return_value = ""
    stub.button.return_value     = False

    for attr in (
        "markdown", "error", "info", "warning", "success", "write",
        "dataframe", "plotly_chart", "metric", "container", "caption",
        "subheader", "title", "header", "divider", "code", "table",
        "json", "image", "altair_chart", "html",
    ):
        monkeypatch.setattr(st, attr, stub, raising=False)
    for attr in ("columns", "tabs", "expander", "text_input",
                 "selectbox", "button"):
        monkeypatch.setattr(st, attr, getattr(stub, attr), raising=False)
    if not hasattr(st, "session_state") or st.session_state is None:
        monkeypatch.setattr(st, "session_state", {}, raising=False)
    yield stub


# ── Import + render contract ──────────────────────────────────────────────

def test_module_imports_cleanly() -> None:
    import importlib
    import sys
    if "ui.tab_port_supply_lines" in sys.modules:
        importlib.reload(sys.modules["ui.tab_port_supply_lines"])
    else:
        importlib.import_module("ui.tab_port_supply_lines")
    from ui import tab_port_supply_lines
    assert callable(tab_port_supply_lines.render)


def test_render_with_no_kwargs_no_exception(streamlit_stub) -> None:
    """The tab consumes the chains module directly — render() takes no
    inputs but must still flow through every guard cleanly."""
    from ui import tab_port_supply_lines
    tab_port_supply_lines.render()


# ── Required ui.styles helpers wired in ───────────────────────────────────

def test_imports_design_system_helpers() -> None:
    src = Path("ui/tab_port_supply_lines.py").read_text(encoding="utf-8")
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)", src, re.DOTALL,
    )
    assert m
    imported = {tok.strip().rstrip(",") for tok in m.group(1).split() if tok.strip()}
    required = {
        "apply_dark_layout", "badge", "metric_card_row",
        "page_header", "section_divider", "section_header",
        "source_footer", "wsj_market_table",
    }
    missing = required - imported
    assert not missing, f"missing helpers: {sorted(missing)}"


# ── Inline-style budget caps ──────────────────────────────────────────────

def test_inline_div_count_within_budget() -> None:
    src = Path("ui/tab_port_supply_lines.py").read_text(encoding="utf-8")
    hits = re.findall(r"<div(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) == 0, "no inline divs allowed — use ui.styles helpers"


def test_inline_span_count_within_budget() -> None:
    src = Path("ui/tab_port_supply_lines.py").read_text(encoding="utf-8")
    hits = re.findall(r"<span(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) == 0, "no inline spans allowed — use ui.styles helpers"


# ── World map builder ─────────────────────────────────────────────────────

def test_world_map_empty_chains_returns_annotated_figure() -> None:
    import plotly.graph_objects as go
    from ui.tab_port_supply_lines import _build_world_supply_map
    fig = _build_world_supply_map([])
    assert isinstance(fig, go.Figure)
    assert any("No port data" in (a.text or "")
               for a in fig.layout.annotations)


def test_world_map_groups_by_severity_label() -> None:
    """Each present severity label gets its own Scattergeo trace —
    the legend reads by severity, not by individual port."""
    from processing.port_supply_lines import build_port_supply_chains
    from ui.tab_port_supply_lines import _build_world_supply_map

    chains = build_port_supply_chains()
    fig = _build_world_supply_map(chains)
    # Every trace must be a Scattergeo and its name must be one of the
    # documented severity labels.
    from processing.port_supply_lines import SEVERITY_LABELS
    for trace in fig.data:
        assert trace.type == "scattergeo"
        assert trace.name in SEVERITY_LABELS


def test_world_map_marker_size_clamped() -> None:
    """All markers must sit in the 10-28 px range so well-served + lightly
    served ports stay visible side by side."""
    from processing.port_supply_lines import build_port_supply_chains
    from ui.tab_port_supply_lines import _build_world_supply_map

    chains = build_port_supply_chains()
    fig = _build_world_supply_map(chains)
    all_sizes = []
    for trace in fig.data:
        all_sizes.extend(list(trace.marker.size))
    if all_sizes:
        assert min(all_sizes) >= 10.0
        assert max(all_sizes) <= 28.0


# ── Per-port company exposure bar builder ─────────────────────────────────

def test_company_exposure_bars_empty_chain_annotated() -> None:
    from ui.tab_port_supply_lines import _build_company_exposure_bars
    fig = _build_company_exposure_bars(None)
    assert any("No exposed companies" in (a.text or "")
               for a in fig.layout.annotations)


def test_company_exposure_bars_sort_heaviest_at_top() -> None:
    """Bars must read with the largest-exposure ticker at the TOP of the
    chart — Plotly stacks categorical y-values bottom-up, so the builder
    sorts ascending."""
    from processing.port_supply_lines import (
        CompanyExposure, PortExposureChain, PortSupplyState,
    )
    from ui.tab_port_supply_lines import _build_company_exposure_bars

    port = PortSupplyState(
        locode="X", name="X", region="R", country_iso3="XXX",
        lat=0.0, lon=0.0, supply_deficit_days=-5.0,
        utilization_pct=80.0, severity_label="Deficit",
        container_type="40FT_DRY",
    )
    chain = PortExposureChain(
        port=port,
        exposed_companies=[
            CompanyExposure(ticker="LIGHT",  exposure_weight=0.10),
            CompanyExposure(ticker="MEDIUM", exposure_weight=0.30),
            CompanyExposure(ticker="HEAVY",  exposure_weight=0.80),
        ],
    )
    fig = _build_company_exposure_bars(chain)
    y_vals = list(fig.data[0].y)
    assert y_vals[0]  == "LIGHT"   # bottom of chart
    assert y_vals[-1] == "HEAVY"   # top of chart


# ── Supply-chain Sankey builder ──────────────────────────────────────────

def test_sankey_empty_chain_annotated() -> None:
    from ui.tab_port_supply_lines import _build_supply_chain_sankey
    fig = _build_supply_chain_sankey(None)
    assert any("No supply chain to render" in (a.text or "")
               for a in fig.layout.annotations)


def test_sankey_renders_for_chain_with_both_commodities_and_companies() -> None:
    """A chain with non-empty top_commodities + exposed_companies must
    produce a Sankey trace with at least one link in each column."""
    import plotly.graph_objects as go
    from processing.port_supply_lines import build_port_supply_chains
    from ui.tab_port_supply_lines import _build_supply_chain_sankey

    chains = build_port_supply_chains()
    rich_chain = next(
        (c for c in chains
         if c.top_commodities and c.exposed_companies),
        None,
    )
    assert rich_chain is not None, (
        "expected at least one chain with both commodities + companies"
    )
    fig = _build_supply_chain_sankey(rich_chain)
    assert len(fig.data) == 1
    assert fig.data[0].type == "sankey"
    n_links = len(fig.data[0].link.source)
    # Must have at least the port→commodity links, AND ideally
    # commodity→company links too.
    assert n_links >= len(rich_chain.top_commodities)
