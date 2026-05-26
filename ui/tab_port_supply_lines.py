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


def _build_world_supply_map(
    chains: list,
    *,
    arcs: list | None = None,
) -> go.Figure:
    """Scattergeo of every port, coloured by severity band + sized by routes.

    When ``arcs`` is non-empty, each ``RouteArc`` is drawn as a faint
    great-circle line from origin to destination port, so the operator
    sees the literal *supply lines* flowing through the port network
    on top of the per-port supply-state markers.

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

    # ── Arcs first so they render UNDERNEATH the port markers ─────────
    if arcs:
        for arc in arcs:
            # Status colour: delayed = red-ish, on-schedule = neutral.
            status = (arc.status or "").lower()
            if "major" in status:
                line_color = "rgba(192,57,43,0.32)"
            elif "minor" in status:
                line_color = "rgba(201,150,43,0.28)"
            else:
                line_color = "rgba(120,170,210,0.20)"
            fig.add_trace(go.Scattergeo(
                lat=[arc.origin_lat, arc.dest_lat],
                lon=[arc.origin_lon, arc.dest_lon],
                mode="lines",
                line={"width": 1.0, "color": line_color},
                hoverinfo="skip",
                showlegend=False,
                opacity=0.85,
            ))

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


def _build_company_port_footprint_bars(footprint) -> go.Figure:
    """Horizontal bars of the top ports one ticker is exposed to.

    Companion to ``_build_company_exposure_bars`` but with the axes
    flipped: y = port name, x = exposure weight. Bars are coloured by
    each port's severity band so deficit-stressed ports pop visually
    in the ticker's footprint.
    """
    fig = go.Figure()
    if footprint is None or not footprint.port_exposures:
        fig.add_annotation(
            text="No port exposures", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="Top port exposures", height=220)
        return fig

    # Ascending sort so Plotly's bottom-up axis puts the heaviest at top
    sorted_exposures = sorted(
        footprint.port_exposures, key=lambda pe: pe.exposure_weight,
    )
    labels   = [pe.port_name for pe in sorted_exposures]
    weights  = [pe.exposure_weight for pe in sorted_exposures]
    severity = [pe.severity_label for pe in sorted_exposures]
    deficits = [pe.supply_deficit_days for pe in sorted_exposures]
    regions  = [pe.region for pe in sorted_exposures]
    colors   = [_SEVERITY_COLOR.get(sev, C_ACCENT) for sev in severity]

    fig.add_trace(go.Bar(
        x=weights, y=labels, orientation="h",
        marker={"color": colors, "line": {"color": C_BG, "width": 1}},
        text=[f"{w:.3f}" for w in weights],
        textposition="outside",
        textfont={"color": C_TEXT2, "size": 11},
        customdata=list(zip(severity, deficits, regions)),
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Exposure: %{x:.4f}<br>"
            "Severity: %{customdata[0]}<br>"
            "Supply: %{customdata[1]:+.1f}d<br>"
            "Region: %{customdata[2]}<extra></extra>"
        ),
        showlegend=False,
    ))
    apply_dark_layout(
        fig,
        title=f"Top port exposures — {footprint.ticker}",
        height=max(220, 50 + 28 * len(labels)),
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


def _render_csv_exports(chains: list, container_type: str) -> None:
    """Five CSV download buttons — three views of the raw join, plus
    a regional rollup and an action-oriented deficit watchlist.

    Every export carries a UTF-8 BOM + a metadata comment header so the
    on-disk file is auditable hours later without round-tripping
    through the UI, and Excel renders it cleanly in UTF-8."""
    try:
        from utils.port_supply_csv import (
            chains_to_deficit_watchlist_csv,
            chains_to_exposure_csv,
            chains_to_regional_rollup_csv,
            chains_to_summary_csv,
            footprints_to_csv,
        )
        from processing.port_supply_lines import build_company_port_footprints
    except Exception:
        logger.exception("port_supply_lines: csv exporter import failed")
        return

    section_header(
        "Download CSV",
        "Five views of the same data. Every export carries a metadata "
        "header (timestamp, container type, schema version) + UTF-8 BOM "
        "so Excel opens cleanly. Filenames stamped with container type + "
        "today's UTC date so daily pulls keep history."
    )

    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = f"_{container_type.lower()}_{stamp}.csv"

    # ── Row 1: raw join views ──────────────────────────────────────────
    col_a, col_b, col_c = st.columns(3, gap="medium")
    with col_a:
        try:
            csv_summary = chains_to_summary_csv(
                chains, container_type=container_type,
            )
        except Exception:
            logger.exception("port_supply_lines: summary csv failed")
            csv_summary = ""
        st.download_button(
            label="Per-port summary + analytics",
            data=csv_summary,
            file_name=f"port_supply_summary{suffix}",
            mime="text/csv",
            use_container_width=True,
            key="port_supply_lines_csv_summary",
            help=(
                "Each port + deficit rank + regional context (mean + delta) "
                "+ exposure concentration HHI + top-5 exposed tickers."
            ),
        )
    with col_b:
        try:
            csv_exposure = chains_to_exposure_csv(
                chains, container_type=container_type,
            )
        except Exception:
            logger.exception("port_supply_lines: exposure csv failed")
            csv_exposure = ""
        st.download_button(
            label="Port × company (flat + shares)",
            data=csv_exposure,
            file_name=f"port_company_exposure{suffix}",
            mime="text/csv",
            use_container_width=True,
            key="port_supply_lines_csv_exposure",
            help=(
                "One row per (port, exposed company) pair. Includes "
                "share_within_port (sums to 1.0 per port) and "
                "rank_within_port (1..N heaviest first)."
            ),
        )
    with col_c:
        try:
            footprints = build_company_port_footprints(
                container_type=container_type,
            )
            csv_footprints = footprints_to_csv(
                footprints, container_type=container_type,
            )
        except Exception:
            logger.exception("port_supply_lines: footprints csv failed")
            csv_footprints = ""
        st.download_button(
            label="Ticker × port (reverse + concentration)",
            data=csv_footprints,
            file_name=f"company_port_footprint{suffix}",
            mime="text/csv",
            use_container_width=True,
            key="port_supply_lines_csv_footprints",
            help=(
                "Reverse axis. Adds deficit_share (% of ticker's exposure "
                "in deficit ports), concentration HHI, top region, and "
                "per-ticker port rank."
            ),
        )

    # ── Row 2: aggregated + ops-triage views ───────────────────────────
    col_d, col_e, _ = st.columns(3, gap="medium")
    with col_d:
        try:
            csv_rollup = chains_to_regional_rollup_csv(
                chains, container_type=container_type,
            )
        except Exception:
            logger.exception("port_supply_lines: rollup csv failed")
            csv_rollup = ""
        st.download_button(
            label="Regional rollup",
            data=csv_rollup,
            file_name=f"port_supply_regional_rollup{suffix}",
            mime="text/csv",
            use_container_width=True,
            key="port_supply_lines_csv_rollup",
            help=(
                "One row per region: port count, mean/min/max deficit, "
                "severity-bucket tallies, top-5 exposed tickers across "
                "the region."
            ),
        )
    with col_e:
        try:
            csv_watchlist = chains_to_deficit_watchlist_csv(
                chains, container_type=container_type, threshold_days=-3.0,
            )
        except Exception:
            logger.exception("port_supply_lines: watchlist csv failed")
            csv_watchlist = ""
        st.download_button(
            label="Deficit watchlist (action-oriented)",
            data=csv_watchlist,
            file_name=f"port_deficit_watchlist{suffix}",
            mime="text/csv",
            use_container_width=True,
            key="port_supply_lines_csv_watchlist",
            help=(
                "Only ports <= -3d. Adds action column (Escalate at "
                "<= -10d, Monitor otherwise) and why explanation — "
                "ready to drop into a ticket / standup deck."
            ),
        )

    # ── Excel workbook — all five sheets bundled together ─────────────
    try:
        from utils.port_supply_xlsx import build_workbook
        footprints_for_xlsx = build_company_port_footprints(
            container_type=container_type,
        )
        xlsx_bytes = build_workbook(
            chains,
            footprints_for_xlsx,
            container_type=container_type,
            threshold_days=-3.0,
        )
    except Exception:
        logger.exception("port_supply_lines: xlsx workbook failed")
        xlsx_bytes = b""
    if xlsx_bytes:
        col_x, _ = st.columns([1, 2])
        with col_x:
            st.download_button(
                label="📓 Excel workbook (all 5 sheets)",
                data=xlsx_bytes,
                file_name=f"port_supply_lines_workbook"
                          f"_{container_type.lower()}"
                          f"_{stamp}.xlsx",
                mime=("application/vnd.openxmlformats-officedocument"
                      ".spreadsheetml.sheet"),
                use_container_width=True,
                key="port_supply_lines_xlsx_workbook",
                help=(
                    "Single .xlsx workbook bundling all five CSV views "
                    "as sheets, plus an overview sheet with the snapshot "
                    "timestamp, container type, threshold, and per-sheet "
                    "row counts. Bold + frozen headers, autosized "
                    "columns, numeric coercion so SUM / sort / pivot "
                    "work without type conversions."
                ),
            )


def _render_historical_trends(chains: list, container_type: str) -> None:
    """Per-port + regional deficit trend over time.

    Reads accumulating daily snapshots from
    ``cache/port_supply_snapshots/`` via
    ``processing.port_supply_trend`` and renders:
      * one per-port line chart (default = worst-deficit port today)
        with current/7d-delta/30d-delta stat block
      * a 2x2 grid of regional rollup charts so the operator sees
        whether a port's slide is idiosyncratic or regional

    Defensive — if zero snapshots exist (fresh deploy), shows an info
    note instead of an empty chart.
    """
    try:
        from processing.port_supply_trend import (
            build_port_trend_series,
            build_regional_trend_series,
        )
        from processing.port_supply_history import list_snapshot_dates
        from ui.plots.port_supply_trends import (
            build_port_trend_figure,
            build_regional_trend_figure,
        )
    except Exception:
        logger.exception("port_supply_lines: trend imports failed")
        return

    section_header(
        "Historical trends",
        "Per-port deficit-day trajectory pulled from the daily snapshot "
        "tree, with a 2x2 regional rollup underneath. Severity bands "
        "shaded so 'is this port trending into trouble' reads at a glance.",
    )

    # Bail-out: nothing on disk yet (fresh deploy).
    try:
        all_dates = list_snapshot_dates()
    except Exception:
        logger.exception("port_supply_lines: list_snapshot_dates failed")
        all_dates = []
    if not all_dates:
        st.info(
            "No snapshot history on disk yet. The daily snapshot job "
            "writes one CSV per port per day; once a few days accumulate, "
            "this section will plot the trend."
        )
        return

    # Default selection = the worst-deficit port in today's chains
    # (chains arrive sorted most-stressed first, so chains[0] is the
    # current most-deficit port unless the operator already chose).
    label_map = {
        f"{c.port.locode} · {c.port.name} ({c.port.region}) — "
        f"{c.port.severity_label} {c.port.supply_deficit_days:+.1f}d": c
        for c in chains
    }
    options = list(label_map.keys())
    default_index = 0
    pick = st.selectbox(
        "Port",
        options=options,
        index=default_index,
        key="port_supply_lines_trend_picker",
        help=(
            "Pick a port to see its deficit-day trajectory. Defaults to "
            "the worst-deficit port in today's snapshot so the most "
            "operationally relevant trend leads."
        ),
    )
    selected = label_map.get(pick) if pick else None
    if selected is None:
        return

    try:
        points = build_port_trend_series(
            selected.port.locode,
            container_type=container_type,
            max_days=90,
        )
    except Exception:
        logger.exception("port_supply_lines: port trend build failed")
        points = []

    # Stat block: current + 7d delta + 30d delta. NaN-safe.
    import math as _math
    real_points = [p for p in points if not _math.isnan(p.deficit_days)]
    current_val = real_points[-1].deficit_days if real_points else float("nan")
    current_label = (
        f"{current_val:+.1f}d" if not _math.isnan(current_val) else "—"
    )
    current_accent = (
        _SEVERITY_ACCENT.get(real_points[-1].severity_label, C_TEXT2)
        if real_points else C_TEXT2
    )

    def _delta_n_days(n: int) -> str:
        if len(real_points) < n + 1:
            return "—"
        delta = real_points[-1].deficit_days - real_points[-1 - n].deficit_days
        return f"{delta:+.1f}d"

    delta_7d = _delta_n_days(7)
    delta_30d = _delta_n_days(30)

    metric_card_row([
        {"label":  "Current deficit",
         "value":  current_label,
         "accent": current_accent,
         "sublabel": (
             real_points[-1].date if real_points else "no snapshots yet"
         )},
        {"label":  "7-day delta",
         "value":  delta_7d,
         "accent": C_ACCENT,
         "sublabel": "vs 7 snapshots ago"},
        {"label":  "30-day delta",
         "value":  delta_30d,
         "accent": C_ACCENT,
         "sublabel": "vs 30 snapshots ago"},
        {"label":  "Snapshots",
         "value":  str(len(points)),
         "accent": C_ACCENT,
         "sublabel": f"last {len(points)} day(s)"},
    ], columns=4)

    try:
        st.plotly_chart(
            build_port_trend_figure(
                points, selected.port.locode, selected.port.name,
            ),
            use_container_width=True,
            config={"displayModeBar": False},
            key="port_supply_lines_trend_port",
        )
    except Exception:
        logger.exception("port_supply_lines: port trend chart failed")

    # ── 2x2 regional rollup grid ──────────────────────────────────────
    # Pick the four most-populated regions so the grid is informative.
    # The task spec says "Asia, Europe, North America, South America" —
    # we map those generic labels to the closest concrete regions in
    # the port registry (Asia East, Europe, North America West,
    # North America East) so the grid actually has data.
    regional_grid = [
        "Asia East",
        "Europe",
        "North America West",
        "North America East",
    ]

    st.caption(
        "Regional rollups average deficit days across every port in the "
        "region per day — see whether a port's slide is idiosyncratic "
        "or part of a regional shift."
    )

    row_a, row_b = st.columns(2, gap="medium"), st.columns(2, gap="medium")
    grid_cells = [row_a[0], row_a[1], row_b[0], row_b[1]]
    for cell, region in zip(grid_cells, regional_grid):
        with cell:
            try:
                series = build_regional_trend_series(
                    region,
                    container_type=container_type,
                    max_days=90,
                )
            except Exception:
                logger.exception(
                    "port_supply_lines: regional trend build failed (%s)",
                    region,
                )
                series = []
            try:
                st.plotly_chart(
                    build_regional_trend_figure(series, region),
                    use_container_width=True,
                    config={"displayModeBar": False},
                    key=f"port_supply_lines_trend_region_{region}",
                )
            except Exception:
                logger.exception(
                    "port_supply_lines: regional trend chart failed (%s)",
                    region,
                )


def _render_spillover_graph(container_type: str) -> None:
    """Contagion-network view: lead-lag edges between ports.

    Walks the persisted snapshot history via
    ``processing.port_spillover_graph.build_spillover_graph`` and
    renders a sortable leaderboard of the strongest edges. Each row
    is "source → target (support, lift, count)" so the operator can
    read it as "when port X enters deficit, port Y follows N times
    with M lift over chance".

    Hidden silently when there's not enough history (< 2 days of
    snapshots) — operators see this section appear after the second
    day's snapshot is persisted.
    """
    try:
        from processing.port_spillover_graph import build_spillover_graph
        from processing.port_supply_history import (
            list_snapshot_dates, load_snapshot,
        )
    except Exception:
        logger.exception("spillover graph imports failed")
        return

    section_header(
        "Contagion network — who follows whom",
        "Lead-lag edges derived from snapshot history. "
        "support = P(target | source). lift > 1 = follows more than chance.",
    )

    # Sidebar-style controls: lookback window + lookahead lag. Kept
    # narrow so the section doesn't dominate the tab visually.
    col_w, col_l, col_t = st.columns(3)
    with col_w:
        window_days = st.number_input(
            "History window (days)",
            min_value=2, max_value=180, value=60, step=1,
            key="spillover_window_days",
            help="How many days of snapshot history to walk.",
        )
    with col_l:
        lag = st.number_input(
            "Lookahead lag (days)",
            min_value=1, max_value=14, value=3, step=1,
            key="spillover_lag",
            help="Within how many days a follow-on entry counts.",
        )
    with col_t:
        top_n = st.number_input(
            "Top N edges",
            min_value=5, max_value=100, value=20, step=5,
            key="spillover_top_n",
            help="Cap on rows shown in the leaderboard.",
        )

    # Walk the persisted snapshot history. Pulling load_snapshot per
    # date keeps memory bounded — each list is freed before the next.
    try:
        from datetime import date as _date, timedelta as _td
        today_dt = _date.today()
        since = today_dt - _td(days=int(window_days))
        all_dates = list_snapshot_dates()
        in_window = [d for d in all_dates if d >= since]
        history: list = []
        for d in in_window:
            try:
                rows = load_snapshot(d, container_type=container_type)
                history.append(rows)
            except FileNotFoundError:
                continue
    except Exception:
        logger.exception("spillover graph: history walk failed")
        alert_banner(
            "Could not read snapshot history for the contagion graph.",
            level="warning",
        )
        return

    if len(history) < 2:
        alert_banner(
            f"Need at least 2 snapshots to build the contagion graph "
            f"(found {len(history)}). Once the daily worker accumulates "
            "more history, the network will populate here.",
            level="info",
        )
        return

    try:
        graph = build_spillover_graph(
            history,
            lag_within_days=int(lag),
            min_co_occurrences=2,
            min_lift=1.0,
        )
    except Exception:
        logger.exception("spillover graph: build_spillover_graph failed")
        alert_banner(
            "Failed to build the contagion graph.", level="warning",
        )
        return

    if not graph.edges:
        alert_banner(
            f"No contagion edges detected in the last {graph.n_days_examined} "
            f"day(s) at lag={graph.lag_within_days}d. Either supply has "
            "stayed stable or the lookahead window is too tight.",
            level="info",
        )
        return

    # Headline metrics row.
    metric_card_row([
        {"label": "Days examined",  "value": str(graph.n_days_examined),
         "delta": "", "color": C_TEXT2},
        {"label": "Edges found",    "value": f"{len(graph.edges):,}",
         "delta": "", "color": C_TEXT2},
        {"label": "Unique sources", "value": str(graph.n_unique_sources),
         "delta": "", "color": C_TEXT2},
        {"label": "Unique targets", "value": str(graph.n_unique_targets),
         "delta": "", "color": C_TEXT2},
    ])

    # Leaderboard table — top-N by lift.
    top_edges = graph.edges[: int(top_n)]
    table_rows: list[dict] = []
    for e in top_edges:
        table_rows.append({
            "Source":     e.source_locode,
            "Target":     e.target_locode,
            "Co-occur":   f"{e.co_occurrence_count}",
            "Source events": f"{e.source_event_count}",
            "Support":    f"{e.support * 100:.0f}%",
            "Base rate":  f"{e.target_base_rate * 100:.0f}%",
            "Lift":       f"{e.lift:.2f}x",
        })
    wsj_market_table(
        title=(
            f"Top {len(table_rows)} contagion edges "
            f"(lag ≤ {graph.lag_within_days}d, min co-occur=2, min lift=1.0)"
        ),
        rows=table_rows,
    )


def _render_company_footprint_drilldown(container_type: str) -> None:
    """Inverted view: pick a ticker → see its top exposed ports.

    Symmetric to ``_render_supply_chain_drilldown``. Same data joins,
    inverted axis: each ticker's footprint across every port it touches.
    Sorted by deficit-weighted score so the ticker most exposed to
    currently-stressed ports surfaces first.
    """
    try:
        from processing.port_supply_lines import build_company_port_footprints
    except Exception:
        logger.exception("port_supply_lines: company footprint import failed")
        return

    section_header(
        "Company → port footprint",
        "Pick a ticker to invert the chain — see which ports it depends "
        "on most heavily. Bar colours flag deficit-stressed ports.",
    )

    try:
        footprints = build_company_port_footprints(container_type=container_type)
    except Exception:
        logger.exception("port_supply_lines: footprint build failed")
        st.error("Company footprint build failed.")
        return
    if not footprints:
        st.info("No ticker exposures available.")
        return

    label_map = {
        f"{f.ticker} — deficit score {f.deficit_weighted_score:.3f} "
        f"({f.n_deficit_ports} of {len(f.port_exposures)} ports in deficit)": f
        for f in footprints
    }
    pick = st.selectbox(
        "Ticker",
        options=list(label_map.keys()),
        key="port_supply_lines_ticker_picker",
    )
    selected = label_map.get(pick)
    if selected is None:
        return

    top_port = (selected.port_exposures[0]
                if selected.port_exposures else None)
    top_severity_accent = (
        _SEVERITY_ACCENT.get(top_port.severity_label, C_TEXT2)
        if top_port else C_TEXT2
    )
    metric_card_row([
        {"label":  "Total exposure",
         "value":  f"{selected.total_exposure:.3f}",
         "accent": C_ACCENT,
         "sublabel": f"across {len(selected.port_exposures)} port(s)"},
        {"label":  "Deficit-weighted score",
         "value":  f"{selected.deficit_weighted_score:.3f}",
         "accent": (C_LOW if selected.deficit_weighted_score > 0.5
                    else (C_MOD if selected.deficit_weighted_score > 0.1
                          else C_HIGH)),
         "sublabel": "Σ exposure × (-deficit days)"},
        {"label":  "Ports in deficit",
         "value":  str(selected.n_deficit_ports),
         "accent": C_LOW if selected.n_deficit_ports > 0 else C_HIGH,
         "sublabel": f"of {len(selected.port_exposures)} touched"},
        {"label":  "Top port",
         "value":  top_port.port_name if top_port else "—",
         "accent": top_severity_accent,
         "sublabel": (f"{top_port.region}" if top_port else "")},
    ], columns=4)

    st.plotly_chart(
        _build_company_port_footprint_bars(selected),
        use_container_width=True,
        config={"displayModeBar": False},
        key="port_supply_lines_footprint_bars",
    )

    if selected.port_exposures:
        with st.expander(
            f"Per-port detail for {selected.ticker} "
            f"({len(selected.port_exposures)})",
            expanded=False,
        ):
            rows = []
            for pe in selected.port_exposures:
                rows.append([
                    badge(pe.port_locode, color=C_ACCENT),
                    badge(pe.port_name,   color=C_TEXT2),
                    badge(pe.region,      color=C_TEXT3),
                    badge(f"{pe.supply_deficit_days:+.1f}d",
                          color=_SEVERITY_ACCENT.get(
                              pe.severity_label, C_TEXT2)),
                    badge(f"{pe.exposure_weight:.4f}", color=C_ACCENT),
                ])
            wsj_market_table(
                ["LOCODE", "Port", "Region", "Supply", "Exposure"],
                rows,
            )


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
                active_voyage_arcs,
                build_port_supply_chains,
                PORT_SUPPLY_LINES_SOURCE,
            )
        except Exception:
            logger.exception("port_supply_lines: import failed")
            st.error("Port supply lines module unavailable.")
            return

        # Container-type selector + voyage-arc toggle.
        col_select, col_arcs, _ = st.columns([1, 1, 2])
        with col_select:
            container_type = st.selectbox(
                "Container type",
                options=["40FT_DRY", "20FT_DRY", "40FT_HC",
                         "40FT_REEFER", "20FT_TANK"],
                index=0,
                key="port_supply_lines_ctype",
                help="Which container-type slice of regional supply state to use.",
            )
        with col_arcs:
            show_arcs = st.checkbox(
                "Show active voyage arcs",
                value=True,
                key="port_supply_lines_show_arcs",
                help=(
                    "Overlay great-circle origin→destination lines for in-"
                    "transit voyages on top of the port-state markers. "
                    "Line colour: red=major delay, amber=minor delay, "
                    "neutral=on schedule."
                ),
            )

        try:
            chains = build_port_supply_chains(container_type=container_type)
        except Exception:
            logger.exception("port_supply_lines: build_port_supply_chains failed")
            st.error("Port supply chain build failed.")
            return

        # ── A. World map ──────────────────────────────────────────────
        arcs = []
        if show_arcs:
            try:
                arcs = active_voyage_arcs(limit=60)
            except Exception:
                logger.exception("port_supply_lines: arc fetch failed")
                arcs = []
        try:
            st.plotly_chart(
                _build_world_supply_map(chains, arcs=arcs),
                use_container_width=True,
                config={"displayModeBar": False},
                key="port_supply_lines_map",
            )
            if show_arcs and arcs:
                st.caption(
                    f"{len(arcs)} active voyage arcs overlaid. "
                    "Red = major delay · amber = minor delay · "
                    "neutral = on schedule. Toggle off to declutter the map."
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

        section_divider("Historical trends")

        # ── D. Historical deficit trends (per-port + regional grid) ────
        try:
            _render_historical_trends(chains, container_type)
        except Exception:
            logger.exception("port_supply_lines: historical trends failed")
            st.error("Historical trends unavailable.")

        section_divider("Contagion network")

        # ── E. Spillover / contagion graph (snapshot-history derived) ──
        try:
            _render_spillover_graph(container_type)
        except Exception:
            logger.exception("port_supply_lines: spillover graph failed")
            st.error("Contagion graph unavailable.")

        section_divider("Company footprint")

        # ── F. Company → port reverse drilldown ────────────────────────
        try:
            _render_company_footprint_drilldown(container_type)
        except Exception:
            logger.exception("port_supply_lines: company footprint failed")
            st.error("Company footprint drilldown unavailable.")

        section_divider("Export")

        # ── F. CSV exports ─────────────────────────────────────────────
        try:
            _render_csv_exports(chains, container_type)
        except Exception:
            logger.exception("port_supply_lines: csv export failed")

        # ── G. Source footer ───────────────────────────────────────────
        try:
            st.markdown(
                source_footer([PORT_SUPPLY_LINES_SOURCE]),
                unsafe_allow_html=True,
            )
        except Exception:
            logger.exception("port_supply_lines: source footer failed")
