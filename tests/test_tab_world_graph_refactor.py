"""Lock-in tests for ui/tab_world_graph.py."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
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
    # plotly_chart must return an event-like object whose .selection.points is
    # empty (no click) so the master-selection path is exercised without
    # triggering st.rerun().
    stub.plotly_chart.return_value = SimpleNamespace(
        selection=SimpleNamespace(points=[]),
    )

    for attr in (
        "markdown", "error", "info", "warning", "success", "write",
        "dataframe", "plotly_chart", "metric", "container", "caption",
        "subheader", "title", "header", "divider", "code", "table",
        "json", "image", "altair_chart", "html", "rerun",
    ):
        monkeypatch.setattr(st, attr, stub, raising=False)
    for attr in ("columns", "tabs", "expander", "text_input",
                 "selectbox", "button", "plotly_chart"):
        monkeypatch.setattr(st, attr, getattr(stub, attr), raising=False)
    if not hasattr(st, "session_state") or st.session_state is None:
        monkeypatch.setattr(st, "session_state", {}, raising=False)
    yield stub


# ── Import + render contract ──────────────────────────────────────────────

def test_module_imports_cleanly() -> None:
    import importlib
    import sys
    if "ui.tab_world_graph" in sys.modules:
        importlib.reload(sys.modules["ui.tab_world_graph"])
    else:
        importlib.import_module("ui.tab_world_graph")
    from ui import tab_world_graph
    assert callable(tab_world_graph.render)


def test_render_with_no_kwargs_no_exception(streamlit_stub) -> None:
    """The tab consumes build_world_graph directly — render() takes no inputs
    but must still flow through every guard cleanly under the stub."""
    from ui import tab_world_graph
    tab_world_graph.render()


# ── Required ui.styles helpers wired in ───────────────────────────────────

def test_imports_design_system_helpers() -> None:
    src = Path("ui/tab_world_graph.py").read_text(encoding="utf-8")
    m = re.search(
        r"from\s+ui\.styles\s+import\s*\(([^)]*)\)", src, re.DOTALL,
    )
    assert m, "expected a multi-line `from ui.styles import (...)` block"
    imported = {tok.strip().rstrip(",") for tok in m.group(1).split() if tok.strip()}
    required = {
        "apply_dark_layout", "badge", "metric_card_row",
        "page_header", "section_divider", "source_footer",
        "wsj_market_table",
    }
    missing = required - imported
    assert not missing, f"missing helpers: {sorted(missing)}"


# ── Inline-style budget caps (== 0) ────────────────────────────────────────

def test_inline_div_count_within_budget() -> None:
    src = Path("ui/tab_world_graph.py").read_text(encoding="utf-8")
    hits = re.findall(r"<div(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) == 0, "no inline divs allowed — use ui.styles helpers"


def test_inline_span_count_within_budget() -> None:
    src = Path("ui/tab_world_graph.py").read_text(encoding="utf-8")
    hits = re.findall(r"<span(?:[^>]*\s)?style\s*=", src, re.IGNORECASE)
    assert len(hits) == 0, "no inline spans allowed — use ui.styles helpers"


# ── Network figure-builder ─────────────────────────────────────────────────

def test_network_figure_empty_returns_annotated() -> None:
    import plotly.graph_objects as go
    from ui.tab_world_graph import _build_network_figure
    fig = _build_network_figure([], np.zeros((0, 0)), np.zeros((0, 2)))
    assert isinstance(fig, go.Figure)
    assert any("No graph data" in (a.text or "")
               for a in fig.layout.annotations)


def test_network_figure_populated_has_at_least_two_traces() -> None:
    """A populated graph yields one edge trace + at least one node trace
    (one per present node type), so >= 2 traces total."""
    import plotly.graph_objects as go
    from processing.world_graph import build_world_graph
    from processing.world_graph_metrics import betweenness_centrality
    from engine.world_graph_layout import spectral_layout

    g = build_world_graph(include_vessels=False)
    nodes = list(g.nodes)
    node_ids = [nd.node_id for nd in nodes]
    crit = betweenness_centrality(node_ids, g.edge_tuples())
    peak = max(crit.values()) if crit else 0.0
    adj = g.adjacency(undirected=True)
    for nd in nodes:
        nd.attrs["criticality"] = (crit.get(nd.node_id, 0.0) / peak) if peak > 0 else 0.0
        nd.attrs["degree_count"] = len(adj.get(nd.node_id, set()))
    index = {nid: i for i, nid in enumerate(node_ids)}
    n = len(nodes)
    matrix = np.zeros((n, n))
    for src, targets in adj.items():
        i = index.get(src)
        if i is None:
            continue
        for tgt in targets:
            j = index.get(tgt)
            if j is not None:
                matrix[i, j] = 1.0
    pos = spectral_layout(matrix)

    fig = _build = None
    from ui.tab_world_graph import _build_network_figure
    fig = _build_network_figure(nodes, matrix, pos)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 2
    # First trace is the edge line trace.
    assert fig.data[0].mode == "lines"
    # Node traces carry customdata = node_id so selection can map back.
    node_traces = [t for t in fig.data if getattr(t, "mode", "") == "markers"]
    assert node_traces, "expected at least one node (markers) trace"
    all_customdata = []
    for t in node_traces:
        if t.customdata is not None:
            all_customdata.extend([cd[0] if hasattr(cd, "__len__")
                                   and not isinstance(cd, str) else cd
                                   for cd in t.customdata])
    assert any(str(cd) in set(node_ids) for cd in all_customdata)


def test_network_figure_selection_dims_via_opacity() -> None:
    """When a node is selected, node traces carry a per-point opacity list
    (focused vs dimmed) rather than a single scalar opacity."""
    from processing.world_graph import build_world_graph
    from processing.world_graph_metrics import betweenness_centrality
    from engine.world_graph_layout import spectral_layout
    from ui.tab_world_graph import _build_network_figure

    g = build_world_graph(include_vessels=False)
    nodes = list(g.nodes)
    node_ids = [nd.node_id for nd in nodes]
    crit = betweenness_centrality(node_ids, g.edge_tuples())
    peak = max(crit.values()) if crit else 0.0
    adj = g.adjacency(undirected=True)
    for nd in nodes:
        nd.attrs["criticality"] = (crit.get(nd.node_id, 0.0) / peak) if peak > 0 else 0.0
        nd.attrs["degree_count"] = len(adj.get(nd.node_id, set()))
    index = {nid: i for i, nid in enumerate(node_ids)}
    n = len(nodes)
    matrix = np.zeros((n, n))
    for src, targets in adj.items():
        i = index.get(src)
        if i is None:
            continue
        for tgt in targets:
            j = index.get(tgt)
            if j is not None:
                matrix[i, j] = 1.0
    pos = spectral_layout(matrix)

    # Pick the highest-criticality node so it definitely has neighbours.
    target = max(nodes, key=lambda nd: float(nd.attrs.get("criticality", 0.0)))
    fig = _build_network_figure(nodes, matrix, pos, selected_id=target.node_id)
    node_traces = [t for t in fig.data if getattr(t, "mode", "") == "markers"]
    assert node_traces
    # At least one node trace's marker.opacity must be a per-point sequence.
    saw_seq = any(
        hasattr(t.marker.opacity, "__len__")
        and not isinstance(t.marker.opacity, str)
        for t in node_traces
    )
    assert saw_seq, "selection should produce per-point opacity dimming"


# ── Geo figure-builder ──────────────────────────────────────────────────────

def test_geo_figure_empty_returns_annotated() -> None:
    import plotly.graph_objects as go
    from ui.tab_world_graph import _build_geo_figure
    fig = _build_geo_figure([], np.zeros((0, 0)))
    assert isinstance(fig, go.Figure)
    assert any("No graph data" in (a.text or "")
               for a in fig.layout.annotations)


def test_geo_figure_has_scattergeo_traces() -> None:
    """The geo view plots ports/chokepoints as Scattergeo traces."""
    from processing.world_graph import build_world_graph
    from ui.tab_world_graph import _build_geo_figure

    g = build_world_graph(include_vessels=False)
    nodes = list(g.nodes)
    n = len(nodes)
    # Geo builder reads lat/lon off nodes directly; adjacency only used for
    # neighbour highlighting, so a zero matrix is fine for the no-selection case.
    fig = _build_geo_figure(nodes, np.zeros((n, n)))
    geo_traces = [t for t in fig.data if t.type == "scattergeo"]
    assert geo_traces, "expected at least one Scattergeo trace for the geo view"
    # Every geo trace name must be a geo-mappable node type.
    for t in geo_traces:
        assert t.name in ("port", "chokepoint", "vessel")


# ── Layout module (FILE 1) lock-in ─────────────────────────────────────────

def test_layout_module_finite_and_deterministic() -> None:
    """spectral_layout + fruchterman_reingold_layout are finite, NaN-safe and
    deterministic on a star + a disconnected graph + degenerate sizes."""
    from engine.world_graph_layout import (
        fruchterman_reingold_layout,
        spectral_layout,
    )
    # Star.
    star = np.zeros((6, 6))
    for leaf in range(1, 6):
        star[0, leaf] = star[leaf, 0] = 1.0
    fr = fruchterman_reingold_layout(star, seed=0)
    sp = spectral_layout(star, seed=0)
    assert fr.shape == (6, 2) and sp.shape == (6, 2)
    assert np.isfinite(fr).all() and np.isfinite(sp).all()
    assert np.array_equal(fr, fruchterman_reingold_layout(star, seed=0))
    assert np.array_equal(sp, spectral_layout(star, seed=0))
    # Disconnected (two edges + isolated node) → still finite.
    disc = np.zeros((5, 5))
    disc[0, 1] = disc[1, 0] = 1.0
    disc[2, 3] = disc[3, 2] = 1.0
    assert np.isfinite(spectral_layout(disc)).all()
    assert np.isfinite(fruchterman_reingold_layout(disc)).all()
    # Degenerate sizes.
    assert fruchterman_reingold_layout(np.zeros((0, 0))).shape == (0, 2)
    assert fruchterman_reingold_layout(np.zeros((1, 1))).shape == (1, 2)
    assert spectral_layout(np.zeros((2, 2))).shape == (2, 2)
