"""tab_port_supply_lines.py — Port Supply Lines map + exposure chain.

Operator-facing answer to "which ports have surplus / deficit container
supply, and which publicly-traded shipping companies are exposed to the
supply lines flowing through them?"

The tab is built around three coordinated views, top to bottom:

  1. **World map** — every port plotted at its real coordinates, marker
     colour banded by supply state (deep red Critical Deficit →
     deep green Heavy Surplus), marker size scaled to the number of
     routes touching the port.
  2. **Deficit / surplus leaders** — two side-by-side ranked tables
     surfacing the top N most-stressed and most-surplus ports.
  3. **Per-port supply-chain drill-down** — pick a port from the
     selectbox, see its severity badge + a bar chart of top exposed
     companies + a Sankey diagram tracing port → commodities →
     companies. Every link in the Sankey has a width proportional
     to the join weight (route_share × cargo_weight × company_weight).

Data flow (all pure modules — no Streamlit imports below the wrapper):

  processing.port_supply_lines.build_port_supply_chains()
    → list[PortExposureChain] sorted most-stressed first
    → each chain joins:
         * ports.port_registry (port coords + region)
         * processing.equipment_tracker (regional supply state)
         * routes.route_registry (routes touching the port)
         * processing.cargo_analyzer.get_route_cargo_mix (per-route cargo)
         * processing.exposure_matrix.COMPANY_COMMODITY_EXPOSURE (company weights)
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_BG,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_RULE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)


# Severity-band colour ramp. Keys match SEVERITY_LABELS in
# processing.port_supply_lines; values are hex strings the map +
# badges share so the visual language is consistent across views.
_SEVERITY_COLOR: dict[str, str] = {
    "Critical Deficit": "#c0392b",   # deep red
    "Deficit":          "#c9962b",   # amber
    "Balanced":         "#5a5650",   # neutral gray
    "Surplus":          "#2e9e6e",   # green
    "Heavy Surplus":    "#1f8a5b",   # deep green
}

# Status mapping for the existing status_badge / metric accent palette.
_SEVERITY_ACCENT: dict[str, str] = {
    "Critical Deficit": C_LOW,
    "Deficit":          C_MOD,
    "Balanced":         C_TEXT2,
    "Surplus":          C_HIGH,
    "Heavy Surplus":    C_HIGH,
}


# ── Pure figure-builders ───────────────────────────────────────────────────


def _build_world_supply_map(chains: list) -> go.Figure:
    """Scattergeo of every port, coloured by severity band + sized by routes.

    Pure builder — no ``st.*`` calls. Empty input returns an annotated
    empty figure so the caller can render unconditionally.
    """
    fig = go.Figure()
    if not chains:
        fig.add_annotation(
            text="No port data", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="Port Supply Lines", height=440)
        return fig

    # Group by severity so the legend reads consistently.
    severities = ("Critical Deficit", "Deficit", "Balanced",
                  "Surplus", "Heavy Surplus")
    for sev in severities:
        bucket = [c for c in chains if c.port.severity_label == sev]
        if not bucket:
            continue
        lats = [c.port.lat for c in bucket]
        lons = [c.port.lon for c in bucket]
        names = [c.port.name for c in bucket]
        deficits = [c.port.supply_deficit_days for c in bucket]
        n_routes = [len(c.routes_touching) for c in bucket]
        n_companies = [len(c.exposed_companies) for c in bucket]
        # Size scales with route count, clamped to a 10–28 px range so
        # well-served + lightly-served ports both stay visible.
        max_routes = max(n_routes) if n_routes else 1
        sizes = [
            10 + 18 * (n / max_routes if max_routes else 0.0)
            for n in n_routes
        ]
        fig.add_trace(go.Scattergeo(
            lat=lats, lon=lons,
            mode="markers",
            name=sev,
            marker={
                "size": sizes,
                "color": _SEVERITY_COLOR.get(sev, C_TEXT2),
                "line": {"color": C_BG, "width": 1.5},
                "opacity": 0.88,
            },
            text=names,
            customdata=list(zip(deficits, n_routes, n_companies)),
            hovertemplate=(
                "<b>%{text}</b><br>"
                f"Severity: {sev}<br>"
                "Supply: %{customdata[0]:+.1f}d<br>"
                "Routes touching: %{customdata[1]}<br>"
                "Companies exposed: %{customdata[2]}<extra></extra>"
            ),
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
        title="Port Supply Lines — surplus/deficit + routes touching each port",
        height=480,
    )
    fig.update_layout(
        margin={"l": 4, "r": 4, "t": 44, "b": 0},
        legend={"orientation": "h", "y": -0.05,
                "font": {"color": C_TEXT3, "size": 10}},
    )
    return fig


def _build_company_exposure_bars(chain) -> go.Figure:
    """Horizontal bars of top exposed companies for one port.

    Sorted highest-exposure at the top (Plotly reverses the
    bottom-up axis convention). Pure builder.
    """
    fig = go.Figure()
    if chain is None or not chain.exposed_companies:
        fig.add_annotation(
            text="No exposed companies", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="Top exposed companies", height=220)
        return fig

    # Ascending sort so Plotly's bottom-up categorical axis puts the
    # heaviest exposure at the TOP of the chart.
    sorted_companies = sorted(
        chain.exposed_companies, key=lambda ce: ce.exposure_weight,
    )
    tickers = [ce.ticker for ce in sorted_companies]
    weights = [ce.exposure_weight for ce in sorted_companies]
    via_counts = [len(ce.via_commodities) for ce in sorted_companies]
    color = _SEVERITY_COLOR.get(chain.port.severity_label, C_ACCENT)

    fig.add_trace(go.Bar(
        x=weights, y=tickers, orientation="h",
        marker={"color": color, "line": {"color": C_BG, "width": 1}},
        text=[f"{w:.3f}" for w in weights],
        textposition="outside",
        textfont={"color": C_TEXT2, "size": 11},
        customdata=via_counts,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Exposure weight: %{x:.4f}<br>"
            "Via %{customdata} commodity(s)<extra></extra>"
        ),
        showlegend=False,
    ))
    apply_dark_layout(
        fig,
        title=f"Top exposed companies — {chain.port.name}",
        height=max(220, 50 + 28 * len(tickers)),
    )
    fig.update_layout(
        xaxis={"title": "Exposure weight (route × cargo × company)",
               "gridcolor": "rgba(255,255,255,0.04)"},
        yaxis={"title": None, "automargin": True,
               "tickfont": {"color": C_TEXT2, "size": 11}},
        margin={"l": 8, "r": 60, "t": 44, "b": 40},
        bargap=0.35,
    )
    return fig


def _build_supply_chain_sankey(chain) -> go.Figure:
    """Sankey diagram tracing port → commodities → exposed companies.

    Node layout (left to right):
      column 0:  single source node = the port
      column 1:  one node per commodity in `top_commodities`
      column 2:  one node per ticker in `exposed_companies`

    Link widths:
      port → commodity: total cargo weight for that commodity
      commodity → ticker: company_weight[commodity] (read directly off
        COMPANY_COMMODITY_EXPOSURE since per-port exposure is already
        averaged)

    Pure builder — empty-chain returns annotated-empty.
    """
    fig = go.Figure()
    if (chain is None or not chain.top_commodities
            or not chain.exposed_companies):
        fig.add_annotation(
            text="No supply chain to render", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="Supply chain", height=320)
        return fig

    # Lazy import keeps the figure-builder unit-testable without the full
    # exposure-matrix import chain.
    try:
        from processing.exposure_matrix import COMPANY_COMMODITY_EXPOSURE
    except Exception:
        COMPANY_COMMODITY_EXPOSURE = {}  # type: ignore[assignment]

    port_node = f"Port: {chain.port.name}"
    commodities = [hs for hs, _ in chain.top_commodities]
    commodity_weights = {hs: w for hs, w in chain.top_commodities}
    tickers = [ce.ticker for ce in chain.exposed_companies]

    # Node list (column 0, then column 1, then column 2)
    labels = [port_node] + commodities + tickers
    node_x = [0.05] + [0.5] * len(commodities) + [0.95] * len(tickers)
    n_total = len(labels)
    # Even vertical spread per column.
    def _evenly_spaced(n: int) -> list[float]:
        if n <= 1:
            return [0.5]
        return [i / (n - 1) for i in range(n)]
    node_y = (
        [0.5]
        + _evenly_spaced(len(commodities))
        + _evenly_spaced(len(tickers))
    )

    # Index lookups
    comm_idx = {hs: 1 + i for i, hs in enumerate(commodities)}
    tick_idx = {t: 1 + len(commodities) + i for i, t in enumerate(tickers)}

    source: list[int] = []
    target: list[int] = []
    value:  list[float] = []
    link_color: list[str] = []

    # Port → commodity links (left column)
    for hs in commodities:
        source.append(0)
        target.append(comm_idx[hs])
        value.append(max(commodity_weights[hs], 0.001))
        link_color.append(_SEVERITY_COLOR.get(chain.port.severity_label, C_ACCENT))

    # Commodity → company links (right column)
    for ticker in tickers:
        company_weights = COMPANY_COMMODITY_EXPOSURE.get(ticker, {}) or {}
        for hs in commodities:
            w = float(company_weights.get(hs, 0.0))
            if w <= 0.0:
                continue
            source.append(comm_idx[hs])
            target.append(tick_idx[ticker])
            value.append(w)
            link_color.append("rgba(120,170,210,0.40)")

    fig.add_trace(go.Sankey(
        arrangement="snap",
        node={
            "label": labels,
            "pad": 16,
            "thickness": 14,
            "color": (
                [_SEVERITY_COLOR.get(chain.port.severity_label, C_ACCENT)]
                + ["#2c333c"] * len(commodities)
                + [C_ACCENT] * len(tickers)
            ),
            "line": {"color": "rgba(255,255,255,0.10)", "width": 0.5},
            "x": node_x,
            "y": node_y,
        },
        link={
            "source": source,
            "target": target,
            "value":  value,
            "color":  link_color,
        },
    ))
    apply_dark_layout(
        fig,
        title=f"Supply chain — {chain.port.name} → commodities → companies",
        height=max(320, 50 + 28 * n_total),
    )
    fig.update_layout(
        margin={"l": 4, "r": 4, "t": 44, "b": 12},
    )
    return fig


# ── Render layer (Streamlit) ──────────────────────────────────────────────


def _render_leaderboard(chains: list, *, kind: str) -> None:
    """Render a top-N ranked table of ports by deficit (kind='deficit') or
    surplus (kind='surplus'). Operator-facing leader board sit alongside
    the world map."""
    if kind == "deficit":
        ranked = [c for c in chains if c.port.supply_deficit_days < 0][:8]
        header_title = "Top deficit ports"
        subtitle = "Container-supply gap, most stressed first"
    else:
        ranked = sorted(
            [c for c in chains if c.port.supply_deficit_days > 0],
            key=lambda c: c.port.supply_deficit_days, reverse=True,
        )[:8]
        header_title = "Top surplus ports"
        subtitle = "Container-supply slack, most over-supplied first"

    section_header(header_title, subtitle)
    if not ranked:
        st.info(f"No ports currently in {kind}.")
        return
    rows = []
    for c in ranked:
        accent = _SEVERITY_ACCENT.get(c.port.severity_label, C_TEXT2)
        rows.append([
            badge(c.port.locode, color=accent),
            badge(c.port.name,   color=C_TEXT2),
            badge(c.port.region, color=C_TEXT3),
            badge(f"{c.port.supply_deficit_days:+.1f}d", color=accent),
            badge(c.port.severity_label, color=accent),
            badge(str(len(c.exposed_companies)), color=C_ACCENT),
        ])
    wsj_market_table(
        ["LOCODE", "Port", "Region", "Supply", "Severity", "Exposed cos"],
        rows,
    )


def _render_supply_chain_drilldown(chains: list) -> None:
    """Per-port detail — selectbox + KPI strip + exposure bars + Sankey."""
    if not chains:
        return

    section_header(
        "Per-port supply chain",
        "Pick a port to see its severity, top exposed companies, and the "
        "full port → commodities → companies supply line.",
    )

    label_map = {
        f"{c.port.locode} · {c.port.name} ({c.port.region}) — "
        f"{c.port.severity_label} {c.port.supply_deficit_days:+.1f}d": c
        for c in chains
    }
    pick = st.selectbox(
        "Port",
        options=list(label_map.keys()),
        key="port_supply_lines_picker",
    )
    selected = label_map.get(pick)
    if selected is None:
        return

    accent = _SEVERITY_ACCENT.get(selected.port.severity_label, C_TEXT2)
    metric_card_row([
        {"label":  "Supply state",
         "value":  selected.port.severity_label,
         "accent": accent,
         "sublabel": f"{selected.port.supply_deficit_days:+.1f}d on "
                     f"{selected.port.container_type}"},
        {"label":  "Utilization",
         "value":  f"{selected.port.utilization_pct:.0f}%",
         "accent": accent,
         "sublabel": "regional avg (equipment_tracker)"},
        {"label":  "Routes touching",
         "value":  str(len(selected.routes_touching)),
         "accent": C_ACCENT,
         "sublabel": "via route_registry"},
        {"label":  "Companies exposed",
         "value":  str(len(selected.exposed_companies)),
         "accent": C_ACCENT,
         "sublabel": "via cargo × COMPANY_COMMODITY_EXPOSURE"},
    ], columns=4)

    col_bars, col_sankey = st.columns([1, 1.6])
    with col_bars:
        st.plotly_chart(
            _build_company_exposure_bars(selected),
            use_container_width=True,
            config={"displayModeBar": False},
            key="port_supply_lines_bars",
        )
    with col_sankey:
        st.plotly_chart(
            _build_supply_chain_sankey(selected),
            use_container_width=True,
            config={"displayModeBar": False},
            key="port_supply_lines_sankey",
        )

    if selected.routes_touching:
        with st.expander(
            f"Routes touching {selected.port.name} "
            f"({len(selected.routes_touching)})",
            expanded=False,
        ):
            for r in selected.routes_touching:
                st.write(f"- {r}")


def render(**_kwargs) -> None:
    """Render the Port Supply Lines tab."""
    from engine.perf_telemetry import track_render

    with track_render('port_supply_lines'):
        page_header(
            title="Port Supply Lines",
            subtitle=(
                "Container-supply surplus and deficit across every tracked "
                "port — and the publicly-traded shipping companies exposed "
                "to the supply lines flowing through them."
            ),
            badge_text="MODELED",
            badge_color=C_ACCENT,
        )

        try:
            from processing.port_supply_lines import (
                build_port_supply_chains,
                PORT_SUPPLY_LINES_SOURCE,
            )
        except Exception:
            logger.exception("port_supply_lines: import failed")
            st.error("Port supply lines module unavailable.")
            return

        # Container-type selector — the equipment_tracker carries
        # per-type slices and the join is parameterised on it.
        col_select, _ = st.columns([1, 3])
        with col_select:
            container_type = st.selectbox(
                "Container type",
                options=["40FT_DRY", "20FT_DRY", "40FT_HC",
                         "40FT_REEFER", "20FT_TANK"],
                index=0,
                key="port_supply_lines_ctype",
                help="Which container-type slice of regional supply state to use.",
            )

        try:
            chains = build_port_supply_chains(container_type=container_type)
        except Exception:
            logger.exception("port_supply_lines: build_port_supply_chains failed")
            st.error("Port supply chain build failed.")
            return

        # ── A. World map ──────────────────────────────────────────────
        try:
            st.plotly_chart(
                _build_world_supply_map(chains),
                use_container_width=True,
                config={"displayModeBar": False},
                key="port_supply_lines_map",
            )
        except Exception:
            logger.exception("port_supply_lines: map render failed")
            st.error("World map unavailable.")

        section_divider("Leaders")

        # ── B. Top deficit / surplus leaderboards ──────────────────────
        col_d, col_s = st.columns(2, gap="medium")
        with col_d:
            try:
                _render_leaderboard(chains, kind="deficit")
            except Exception:
                logger.exception("port_supply_lines: deficit leaderboard failed")
        with col_s:
            try:
                _render_leaderboard(chains, kind="surplus")
            except Exception:
                logger.exception("port_supply_lines: surplus leaderboard failed")

        section_divider("Drilldown")

        # ── C. Per-port supply-chain detail ────────────────────────────
        try:
            _render_supply_chain_drilldown(chains)
        except Exception:
            logger.exception("port_supply_lines: drilldown failed")
            st.error("Per-port drilldown unavailable.")

        # ── D. Source footer ───────────────────────────────────────────
        try:
            st.markdown(
                source_footer([PORT_SUPPLY_LINES_SOURCE]),
                unsafe_allow_html=True,
            )
        except Exception:
            logger.exception("port_supply_lines: source footer failed")
