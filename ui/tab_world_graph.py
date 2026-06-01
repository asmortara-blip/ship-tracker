"""tab_world_graph.py — the unified "Shipping World Graph".

One interactive, navigable picture of how the entities this platform already
models hang together — ports, lanes, canals, companies and commodities — drawn
as a typed node-link network and, alongside it, on the real globe.

The operator question this answers is *structural*: which nodes are the
load-bearing hubs of the whole shipping world, and what is the blast radius if
one of them is disrupted? It sits on top of two pure modules:

  * ``processing.world_graph.build_world_graph`` — assembles the graph from the
    existing builders (no new data sources).
  * ``processing.world_graph_metrics.betweenness_centrality`` — the systemic
    "chokepoint" signal used to SIZE the nodes.
  * ``engine.world_graph_layout.spectral_layout`` — a pure-numpy 2-D embedding
    of the structural backbone for the node-link view.

Two coordinated views, top to bottom:

  1. **Node-link network** — the abstract graph laid out by the spectral
     layout. Marker size ∝ betweenness criticality, colour by node type. This
     chart is the SELECTION MASTER: click a node (``on_select="rerun"``) and the
     whole tab focuses on it — its 1-hop neighbourhood stays lit while the rest
     dims, and the detail panel + geo map below update.
  2. **Geographic map** — every geo-mappable node (ports, chokepoints) plotted
     at its real coordinates, mirroring the Port Supply Lines map style. Purely
     read-only; it follows the network/selectbox selection, never drives it.

A ``st.selectbox`` is the always-works fallback selector (and the only way to
focus an abstract, non-geo node like a company or commodity); it drives both
views. A detail panel surfaces the selected node's criticality / degree / type
plus a table of its direct neighbours.

Data flow (all pure — no Streamlit imports below the wrapper / figure-builders):
  build_world_graph(include_vessels=False)   # backbone → clean centrality
    → betweenness_centrality(...)             # node sizing
    → g.adjacency() → numpy matrix            # layout + neighbourhood dimming
    → spectral_layout(matrix)                 # 2-D node positions
"""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_BG,
    C_CONV,
    C_HIGH,
    C_LOW,
    C_MACRO,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    source_footer,
    wsj_market_table,
)


# Provenance for the footer. The world graph is a pure derivation over the
# existing modeled builders, so it is itself MODELED.
WORLD_GRAPH_SOURCE = DataSource.modeled(
    "Shipping World Graph",
    notes=(
        "Unified graph derived from port-supply, route, chokepoint, company "
        "and commodity builders; centrality via Brandes betweenness."
    ),
)


# Per-node-type colour, drawn from the WSJ palette so the legend is on-brand
# and distinct across the six types.
_TYPE_COLOR: dict[str, str] = {
    "port":       C_ACCENT,   # steel blue
    "route":      C_MACRO,    # teal
    "chokepoint": C_LOW,      # red — the systemic constraints
    "vessel":     C_TEXT2,    # neutral grey (leaf instances)
    "company":    C_MOD,      # amber
    "commodity":  C_CONV,     # purple
}

# Types that can be plotted on the globe (carry lat/lon).
_GEO_TYPES: tuple[str, ...] = ("port", "chokepoint", "vessel")


# ── Pure figure-builders (no st.* — independently unit-testable) ────────────


def _annotated_empty(title: str, height: int = 460) -> go.Figure:
    """An annotated empty figure so callers can render unconditionally —
    mirrors the no-data path of ``_build_world_supply_map``."""
    fig = go.Figure()
    fig.add_annotation(
        text="No graph data", xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False,
        font={"color": C_TEXT3, "size": 12},
    )
    apply_dark_layout(fig, title=title, height=height)
    return fig


def _build_network_figure(
    nodes: list,
    adjacency: np.ndarray,
    pos: np.ndarray,
    *,
    selected_id: str | None = None,
) -> go.Figure:
    """Cartesian node-link diagram of the world graph.

    Edges are drawn as ONE ``go.Scatter`` line trace with ``None``-separated
    segments (the standard fast way to draw many edges in a single trace).
    Nodes are a SECOND ``go.Scatter`` marker trace: size ∝ criticality
    (read off each node's ``attrs['criticality']`` in [0,1]), colour by node
    type, and ``customdata`` carries the node_id so the selection event can map
    a clicked point back to its node.

    When ``selected_id`` is set, the selected node + its 1-hop neighbourhood
    stay fully opaque while every other node + edge dims, focusing the eye on
    the blast radius. Pure builder — empty input returns an annotated-empty
    figure.
    """
    n = len(nodes)
    if n == 0 or pos is None or len(pos) != n:
        return _annotated_empty("Shipping world graph", height=560)

    A = np.asarray(adjacency, dtype=float)
    index = {nd.node_id: i for i, nd in enumerate(nodes)}
    sel_i = index.get(selected_id) if selected_id is not None else None

    # 1-hop neighbourhood (indices) of the selection, for the dimming mask.
    neighbourhood: set[int] = set()
    if sel_i is not None:
        neighbourhood = {sel_i}
        row = A[sel_i]
        col = A[:, sel_i]
        for j in range(n):
            if row[j] != 0.0 or col[j] != 0.0:
                neighbourhood.add(j)

    def _node_in_focus(i: int) -> bool:
        return sel_i is None or i in neighbourhood

    # ── Edges: one trace, None-separated segments ──────────────────────────
    # Only the upper triangle so each undirected edge is drawn once.
    edge_x: list[float | None] = []
    edge_y: list[float | None] = []
    for i in range(n):
        for j in range(i + 1, n):
            if A[i, j] == 0.0 and A[j, i] == 0.0:
                continue
            # When a node is selected, draw only edges touching its
            # neighbourhood (keeps the focused view legible).
            if sel_i is not None and not (i in neighbourhood and j in neighbourhood):
                continue
            edge_x.extend([pos[i, 0], pos[j, 0], None])
            edge_y.extend([pos[i, 1], pos[j, 1], None])

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=edge_x, y=edge_y,
        mode="lines",
        line={"width": 0.6, "color": "rgba(154,150,142,0.22)"},
        hoverinfo="skip",
        showlegend=False,
        name="edges",
    ))

    # ── Nodes: one trace per type so the legend reads by type ──────────────
    # Marker size scales with criticality; clamped to a 7–30 px range so even
    # zero-criticality leaves stay visible next to the systemic hubs.
    for ntype in ("port", "route", "chokepoint", "vessel", "company", "commodity"):
        members = [i for i, nd in enumerate(nodes) if nd.node_type == ntype]
        if not members:
            continue
        xs = [pos[i, 0] for i in members]
        ys = [pos[i, 1] for i in members]
        crit = [float(nodes[i].attrs.get("criticality", 0.0)) for i in members]
        sizes = [7.0 + 23.0 * max(0.0, min(1.0, c)) for c in crit]
        labels = [nodes[i].label or nodes[i].node_id for i in members]
        ids = [nodes[i].node_id for i in members]
        degrees = [int(nodes[i].attrs.get("degree_count", 0)) for i in members]
        # Dim out-of-focus nodes when something is selected.
        opacities = [0.95 if _node_in_focus(i) else 0.12 for i in members]
        # A Scatter trace takes a single opacity, so split focus vs dim only if
        # we actually have a selection; otherwise one bright trace per type.
        if sel_i is None:
            marker_opacity: float | list[float] = 0.92
        else:
            marker_opacity = opacities

        fig.add_trace(go.Scatter(
            x=xs, y=ys,
            mode="markers",
            name=ntype,
            marker={
                "size": sizes,
                "color": _TYPE_COLOR.get(ntype, C_TEXT2),
                "line": {"color": C_BG, "width": 1.0},
                "opacity": marker_opacity,
            },
            text=labels,
            customdata=ids,
            hovertemplate=(
                "<b>%{text}</b><br>"
                f"Type: {ntype}<br>"
                "Criticality: %{marker.size:.0f}px<br>"
                "<extra></extra>"
            ),
            showlegend=True,
        ))
        # Stash per-point hover detail (criticality/degree) via customdata is
        # already used for node_id, so keep the hovertemplate type-level; the
        # detail panel below carries the precise numbers.
        del degrees  # retained above for clarity; panel shows exact values

    apply_dark_layout(
        fig,
        title="Shipping world graph — node size ∝ systemic criticality",
        height=560,
    )
    fig.update_layout(
        margin={"l": 8, "r": 8, "t": 44, "b": 8},
        legend={"orientation": "h", "y": -0.04,
                "font": {"color": C_TEXT3, "size": 10}},
        xaxis={"visible": False, "showgrid": False, "zeroline": False},
        yaxis={"visible": False, "showgrid": False, "zeroline": False,
               "scaleanchor": "x", "scaleratio": 1},
        hovermode="closest",
    )
    return fig


def _build_geo_figure(
    nodes: list,
    adjacency: np.ndarray,
    *,
    selected_id: str | None = None,
) -> go.Figure:
    """Scattergeo of the geo-mappable nodes (ports / chokepoints / vessels).

    Mirrors ``_build_world_supply_map`` in ``tab_port_supply_lines``: natural
    earth projection, translucent land/ocean, ``C_BG`` marker outlines. One
    trace per geo node type so the legend reads consistently. Read-only — this
    map FOLLOWS the current selection (the selected node + its geo neighbours
    are highlighted) but never drives it.

    Pure builder — no ``st.*``. Empty / no-geo input returns an annotated-empty
    figure so the caller can render unconditionally.
    """
    geo_nodes = [
        nd for nd in nodes
        if nd.node_type in _GEO_TYPES
        and nd.lat is not None and nd.lon is not None
    ]
    if not geo_nodes:
        return _annotated_empty("World graph — geography", height=480)

    A = np.asarray(adjacency, dtype=float)
    index = {nd.node_id: i for i, nd in enumerate(nodes)}
    sel_i = index.get(selected_id) if selected_id is not None else None
    neighbourhood: set[int] = set()
    if sel_i is not None:
        neighbourhood = {sel_i}
        row = A[sel_i]
        col = A[:, sel_i]
        for j in range(len(nodes)):
            if row[j] != 0.0 or col[j] != 0.0:
                neighbourhood.add(j)

    fig = go.Figure()
    for ntype in _GEO_TYPES:
        bucket = [nd for nd in geo_nodes if nd.node_type == ntype]
        if not bucket:
            continue
        lats = [nd.lat for nd in bucket]
        lons = [nd.lon for nd in bucket]
        names = [nd.label or nd.node_id for nd in bucket]
        crit = [float(nd.attrs.get("criticality", 0.0)) for nd in bucket]
        sizes = [8.0 + 18.0 * max(0.0, min(1.0, c)) for c in crit]
        # Highlight: when a node is selected, its geo neighbourhood pops and the
        # rest fades. With no selection, everything sits at a uniform opacity.
        if sel_i is None:
            opacities = 0.85
            line_widths = 1.2
        else:
            opacities = [
                0.95 if index.get(nd.node_id) in neighbourhood else 0.18
                for nd in bucket
            ]
            line_widths = [
                2.2 if index.get(nd.node_id) == sel_i else 1.2
                for nd in bucket
            ]
        fig.add_trace(go.Scattergeo(
            lat=lats, lon=lons,
            mode="markers",
            name=ntype,
            marker={
                "size": sizes,
                "color": _TYPE_COLOR.get(ntype, C_TEXT2),
                "line": {"color": C_BG, "width": line_widths},
                "opacity": opacities,
            },
            text=names,
            hovertemplate="<b>%{text}</b><br>" f"Type: {ntype}<extra></extra>",
        ))

    fig.update_geos(
        projection_type="natural earth",
        showland=True,
        landcolor="rgba(255,255,255,0.04)",
        showocean=True,
        oceancolor="rgba(120,170,210,0.05)",
        showcountries=True,
        countrycolor="rgba(255,255,255,0.10)",
        coastlinecolor="rgba(255,255,255,0.18)",
        bgcolor="rgba(0,0,0,0)",
    )
    apply_dark_layout(
        fig,
        title="World graph — geography of ports & chokepoints",
        height=480,
    )
    fig.update_layout(
        margin={"l": 4, "r": 4, "t": 44, "b": 0},
        legend={"orientation": "h", "y": -0.05,
                "font": {"color": C_TEXT3, "size": 10}},
    )
    return fig


# ── Render layer (Streamlit) ───────────────────────────────────────────────


def _node_option_label(nd) -> str:
    """Human-readable selectbox label for a node: 'type · label (crit)'."""
    crit = float(nd.attrs.get("criticality", 0.0))
    base = nd.label or nd.node_id
    return f"{nd.node_type} · {base} — crit {crit:.2f}"


def _render_detail_panel(g, selected_id: str | None) -> None:
    """KPI strip (criticality / degree / type) + a table of direct neighbours
    for the selected node. No-op when nothing is selected."""
    if not selected_id:
        st.caption(
            "Click a node in the network above (or pick one from the selector) "
            "to focus its 1-hop neighbourhood and see its detail here."
        )
        return
    node = g.get_node(selected_id)
    if node is None:
        return

    crit = float(node.attrs.get("criticality", 0.0))
    degree = int(node.attrs.get("degree_count", 0))
    accent = _TYPE_COLOR.get(node.node_type, C_TEXT2)

    section_divider(node.label or node.node_id)
    metric_card_row([
        {"label":  "Node",
         "value":  node.label or node.node_id,
         "accent": accent,
         "sublabel": f"id: {node.node_id}"},
        {"label":  "Type",
         "value":  node.node_type,
         "accent": accent,
         "sublabel": "one of 6 world-graph types"},
        {"label":  "Criticality",
         "value":  f"{crit:.2f}",
         "accent": (C_LOW if crit > 0.5 else (C_MOD if crit > 0.15 else C_HIGH)),
         "sublabel": "betweenness, normalised to peak = 1.0"},
        {"label":  "Degree",
         "value":  str(degree),
         "accent": C_ACCENT,
         "sublabel": "direct connections (undirected)"},
    ], columns=4)

    # Neighbour table — sorted most-critical neighbour first.
    neighbour_ids = sorted(g.neighbors(selected_id, undirected=True))
    if not neighbour_ids:
        st.info("This node has no direct connections in the backbone graph.")
        return
    neighbours = [g.get_node(nid) for nid in neighbour_ids]
    neighbours = [nd for nd in neighbours if nd is not None]
    neighbours.sort(
        key=lambda nd: float(nd.attrs.get("criticality", 0.0)), reverse=True,
    )
    rows: list[list[str]] = []
    for nd in neighbours:
        n_accent = _TYPE_COLOR.get(nd.node_type, C_TEXT2)
        n_crit = float(nd.attrs.get("criticality", 0.0))
        n_deg = int(nd.attrs.get("degree_count", 0))
        rows.append([
            badge(nd.node_type, color=n_accent),
            badge(nd.label or nd.node_id, color=C_TEXT2),
            badge(f"{n_crit:.2f}", color=n_accent),
            badge(str(n_deg), color=C_ACCENT),
        ])
    wsj_market_table(
        ["Type", "Neighbour", "Criticality", "Degree"],
        rows,
        title=f"{len(rows)} direct neighbour(s) of {node.label or node.node_id}",
    )


def render(**_kwargs) -> None:
    """Render the Shipping World Graph tab."""
    from engine.perf_telemetry import track_render

    with track_render("world_graph"):
        page_header(
            title="Shipping World Graph",
            subtitle=(
                "Ports, lanes, canals, companies and commodities as one typed "
                "network — sized by systemic criticality and laid out on the "
                "globe. Click a node to focus its blast radius."
            ),
            badge_text="MODELED",
            badge_color=C_ACCENT,
        )

        # ── Build the graph once (backbone → clean centrality + fast) ──────
        try:
            from processing.world_graph import build_world_graph
            from processing.world_graph_metrics import (
                betweenness_centrality,
                degree_centrality,
            )
            from engine.world_graph_layout import spectral_layout
        except Exception:
            logger.exception("world_graph: import failed")
            st.error("World graph module unavailable.")
            return

        try:
            g = build_world_graph(include_vessels=False)
        except Exception:
            logger.exception("world_graph: build_world_graph failed")
            st.error("World graph build failed.")
            return

        nodes = list(g.nodes)
        if not nodes:
            # Empty graph → annotated-empty figures, rendered unconditionally.
            st.plotly_chart(
                _build_network_figure([], np.zeros((0, 0)), np.zeros((0, 2))),
                use_container_width=True,
                config={"displayModeBar": False},
                key="wg_network",
            )
            st.plotly_chart(
                _build_geo_figure([], np.zeros((0, 0))),
                use_container_width=True,
                config={"displayModeBar": False},
                key="wg_geo",
            )
            try:
                st.markdown(
                    source_footer([WORLD_GRAPH_SOURCE]),
                    unsafe_allow_html=True,
                )
            except Exception:
                logger.exception("world_graph: source footer failed")
            return

        node_ids = [nd.node_id for nd in nodes]

        # Centrality → node sizing. Normalise betweenness to [0, 1] (peak = 1)
        # so the largest hub is the biggest marker regardless of absolute scale.
        try:
            crit_raw = betweenness_centrality(node_ids, g.edge_tuples())
        except Exception:
            logger.exception("world_graph: betweenness failed")
            crit_raw = {nid: 0.0 for nid in node_ids}
        peak = max(crit_raw.values()) if crit_raw else 0.0
        try:
            deg = degree_centrality(node_ids, g.edge_tuples())
        except Exception:
            deg = {nid: 0.0 for nid in node_ids}

        adj = g.adjacency(undirected=True)
        # Stash per-node sizing inputs on attrs so the pure builders + detail
        # panel read a single source of truth.
        for nd in nodes:
            nd.attrs["criticality"] = (
                (crit_raw.get(nd.node_id, 0.0) / peak) if peak > 0 else 0.0
            )
            nd.attrs["degree_count"] = len(adj.get(nd.node_id, set()))
        # Keep degree_centrality available too (unused for sizing but cheap).
        del deg

        # ── Ordered node list + adjacency MATRIX (numpy) + layout ──────────
        index = {nid: i for i, nid in enumerate(node_ids)}
        n = len(nodes)
        matrix = np.zeros((n, n), dtype=float)
        for src, targets in adj.items():
            i = index.get(src)
            if i is None:
                continue
            for tgt in targets:
                j = index.get(tgt)
                if j is not None:
                    matrix[i, j] = 1.0
        try:
            pos = spectral_layout(matrix)
        except Exception:
            logger.exception("world_graph: spectral_layout failed")
            pos = np.zeros((n, 2))

        # ── Selection state: network click is master; selectbox is fallback ─
        if "wg_selected_id" not in st.session_state:
            st.session_state["wg_selected_id"] = None

        # ── A. Node-link network (SELECTION MASTER via on_select) ──────────
        selected_id = st.session_state.get("wg_selected_id")
        try:
            event = st.plotly_chart(
                _build_network_figure(
                    nodes, matrix, pos, selected_id=selected_id,
                ),
                use_container_width=True,
                config={"displayModeBar": False},
                key="wg_network",
                on_select="rerun",
                selection_mode="points",
            )
        except Exception:
            logger.exception("world_graph: network chart failed")
            event = None
            st.error("Network chart unavailable.")

        # Read the clicked point's customdata (the node_id) into session state.
        # Plotly wraps per-point customdata as a list, so unwrap defensively.
        if event is not None:
            try:
                points = event.selection.points if event.selection else []
            except Exception:
                points = []
            if points:
                cd = points[0].get("customdata")
                clicked: str | None = None
                if isinstance(cd, (list, tuple)) and cd:
                    clicked = str(cd[0])
                elif isinstance(cd, str):
                    clicked = cd
                if clicked and clicked != st.session_state.get("wg_selected_id"):
                    st.session_state["wg_selected_id"] = clicked
                    selected_id = clicked
                    st.rerun()

        # ── Fallback selector — always works + drives the geo side ─────────
        # (and is the only way to focus a non-geo node like a company.)
        ordered = sorted(
            nodes,
            key=lambda nd: float(nd.attrs.get("criticality", 0.0)),
            reverse=True,
        )
        label_map = {_node_option_label(nd): nd.node_id for nd in ordered}
        options = ["(none — show full graph)"] + list(label_map.keys())
        # Preselect the current selection in the dropdown.
        current_index = 0
        if selected_id is not None:
            for i, (lbl, nid) in enumerate(label_map.items(), start=1):
                if nid == selected_id:
                    current_index = i
                    break
        pick = st.selectbox(
            "Focus node",
            options=options,
            index=current_index,
            key="wg_node_picker",
            help=(
                "Click a node in the network above, or pick one here. This "
                "selector is also the way to focus an abstract node (company / "
                "commodity / route) that isn't on the map."
            ),
        )
        picked_id = label_map.get(pick) if pick in label_map else None
        if picked_id != selected_id:
            st.session_state["wg_selected_id"] = picked_id
            selected_id = picked_id

        st.caption(
            f"{g.summary()['n_nodes']} nodes · {g.summary()['n_edges']} edges · "
            "node size ∝ betweenness criticality · colour by type. "
            "The map below follows this selection (read-only)."
        )

        section_divider("Geography")

        # ── B. Geographic map (read-only — follows selection) ──────────────
        try:
            st.plotly_chart(
                _build_geo_figure(nodes, matrix, selected_id=selected_id),
                use_container_width=True,
                config={"displayModeBar": False},
                key="wg_geo",
            )
        except Exception:
            logger.exception("world_graph: geo chart failed")
            st.error("Geographic map unavailable.")

        section_divider("Detail")

        # ── C. Selected-node detail panel ──────────────────────────────────
        try:
            _render_detail_panel(g, selected_id)
        except Exception:
            logger.exception("world_graph: detail panel failed")
            st.error("Node detail unavailable.")

        # ── D. Source footer ───────────────────────────────────────────────
        try:
            st.markdown(
                source_footer([WORLD_GRAPH_SOURCE]),
                unsafe_allow_html=True,
            )
        except Exception:
            logger.exception("world_graph: source footer failed")
