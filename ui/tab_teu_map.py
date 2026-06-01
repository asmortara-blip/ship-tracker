"""tab_teu_map.py — the "Global TEU Throughput" map.

One interactive picture of how container traffic is distributed across the
ports this platform tracks, sliced four ways: total throughput, modeled
imports, modeled exports, and the net (export − import) balance.

The operator question this answers is *spatial*: where is the world's box
traffic concentrated, and which ports run a net-export vs net-import posture?
It sits on top of one pure module:

  * ``processing.port_teu_map.build_port_teu_map`` — assembles per-port
    :class:`~processing.port_teu_map.PortTEU` records from the World Bank data
    dict (``data.worldbank_feed.fetch_port_throughput``). All TEU figures are in
    MILLIONS/yr.

Data provenance (honest about it)
---------------------------------
* **Total per-port TEU** is REAL World Bank country *Container Port Traffic*
  (``IS.SHP.GOOD.TU``) split across each country's tracked ports by a MODELED
  per-port weight. The COUNTRY totals are measured; the per-port split is not.
* **Import / export split** is MODELED — each port's TEU is split by its
  country's REAL merchandise export/import VALUE ratio
  (``TX.VAL.MRCH.CD.WT`` / ``TM.VAL.MRCH.CD.WT``). Trade *value* is only a proxy
  for box *volume*, so the split is indicative, not measured.
* **Net** = export − import. Nothing here is a forecast.

Layout, top to bottom:
  1. A category selector (total / imports / exports / net) — the single control
     that drives every view below it.
  2. A platform-wide KPI strip (total / exports / imports / net, M TEU/yr).
  3. **Geographic map** — every tracked port plotted at its real coordinates;
     marker SIZE ∝ the selected category's value (abs() for 'net' so net
     importers stay visible), colour by sign for 'net' / single accent
     otherwise. Mirrors the Port Supply Lines map style.
  4. **Ranked table** — the top ports by the selected category, showing all four
     slices + the modeled export share.
  5. **Region rollup** — the selected category summed by region as a horizontal
     bar.

Data flow (all pure — no Streamlit imports below the wrapper / figure-builders):
  fetch_port_throughput()                 # REAL WB dict (cached)
    → build_port_teu_map(wb_data)         # per-port PortTEU records
    → summarize / rank_ports / aggregate_by_region  # pure reducers
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from processing.port_teu_map import (
    CATEGORIES,
    CATEGORY_LABELS,
    PortTEU,
    aggregate_by_region,
    build_port_teu_map,
    build_port_teu_trends,
    rank_by_growth,
    rank_ports,
    summarize,
)
from ui.styles import (
    C_ACCENT,
    C_BG,
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


# Provenance for the footer. The container TEU + trade values are REAL World
# Bank data (publicly published, so .scraped()); the per-port allocation and the
# import/export split are MODELED — said plainly in the notes so the badge can't
# overclaim.
TEU_MAP_SOURCE = DataSource.scraped(
    "World Bank port traffic",
    url="https://data.worldbank.org/indicator/IS.SHP.GOOD.TU",
    notes=(
        "Real WB country container traffic (IS.SHP.GOOD.TU) + merchandise trade "
        "values (TX/TM.VAL.MRCH.CD.WT); per-port allocation + import/export "
        "split are MODELED (trade value is a proxy for box volume)."
    ),
)


# ── Pure figure-builders (no st.* — independently unit-testable) ────────────


def _annotated_empty(title: str, height: int = 460) -> go.Figure:
    """An annotated empty figure so callers can render unconditionally —
    mirrors the no-data path of ``tab_world_graph._annotated_empty``."""
    fig = go.Figure()
    fig.add_annotation(
        text="No port data", xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False,
        font={"color": C_TEXT3, "size": 12},
    )
    apply_dark_layout(fig, title=title, height=height)
    return fig


def _scale_sizes(values: list[float], *, lo: float = 8.0, hi: float = 40.0) -> list[float]:
    """Linearly map non-negative magnitudes into a ``[lo, hi]`` pixel range.

    Callers pass abs() values, so the smallest port still renders at ``lo`` px
    and the largest at ``hi`` px. A degenerate spread (all equal) collapses to a
    mid size so nothing disappears. Pure.
    """
    if not values:
        return []
    vmax = max(values)
    vmin = min(values)
    if vmax <= 0.0:
        # No throughput anywhere → a uniform small marker.
        return [lo for _ in values]
    span = vmax - vmin
    if span <= 0.0:
        mid = (lo + hi) / 2.0
        return [mid for _ in values]
    return [lo + (hi - lo) * ((v - vmin) / span) for v in values]


def _hover_for(pt: PortTEU) -> str:
    """Multi-line hover string: name + region + all four TEU slices."""
    region = pt.region or "—"
    return (
        f"<b>{pt.name}</b><br>"
        f"{region}<br>"
        f"Total: {pt.teu_total:.1f}M<br>"
        f"Exports: {pt.teu_export:.1f}M<br>"
        f"Imports: {pt.teu_import:.1f}M<br>"
        f"Net: {pt.teu_net:+.1f}M"
    )


def _build_teu_geo(
    port_teus: list[PortTEU], category: str, *, height: int = 520,
) -> go.Figure:
    """Scattergeo of every tracked port, sized by the selected category.

    Mirrors ``tab_world_graph._build_geo_figure``: natural-earth projection,
    translucent land/ocean, ``C_BG`` marker outlines. One marker per port at its
    real (lat, lon); ``marker.size`` ∝ the port's ``value_for(category)`` scaled
    into ~8–40 px (abs() for 'net' so net importers stay visible). COLOUR: for
    'net', green when the balance is positive (net exporter) and red when
    negative (net importer); for every other category a single steel accent.

    Pure builder — no ``st.*``. Empty / no-coordinate input returns an
    annotated-empty figure so the caller can render unconditionally.
    """
    label = CATEGORY_LABELS.get(category, category)
    geo = [
        pt for pt in port_teus
        if pt.lat is not None and pt.lon is not None
    ]
    if not geo:
        return _annotated_empty(f"Global TEU — {label}", height=height)

    raw = [pt.value_for(category) for pt in geo]
    # Size by magnitude (abs) so net importers (negative net) still draw.
    sizes = _scale_sizes([abs(v) for v in raw])

    if category == "net":
        colors = [C_HIGH if v > 0 else C_LOW for v in raw]
    else:
        colors = [C_ACCENT for _ in raw]

    fig = go.Figure()
    fig.add_trace(go.Scattergeo(
        lat=[pt.lat for pt in geo],
        lon=[pt.lon for pt in geo],
        mode="markers",
        name=label,
        marker={
            "size": sizes,
            "color": colors,
            "line": {"color": C_BG, "width": 1.0},
            "opacity": 0.82,
        },
        text=[_hover_for(pt) for pt in geo],
        hovertemplate="%{text}<extra></extra>",
        showlegend=False,
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
        title=f"Container throughput by port — {label} (marker size ∝ TEU)",
        height=height,
    )
    fig.update_layout(margin={"l": 4, "r": 4, "t": 44, "b": 0})
    return fig


def _build_region_bar(
    agg: dict[str, float], category: str, *, height: int = 360,
) -> go.Figure:
    """Horizontal bar of the selected category summed by region.

    ``agg`` is the dict from :func:`aggregate_by_region` (already sorted desc by
    magnitude). Bars are coloured by sign for 'net' (green exporter / red
    importer) and a single teal otherwise; drawn smallest-at-top so the biggest
    region reads at the top of the chart. Pure builder — empty input returns an
    annotated-empty figure.
    """
    label = CATEGORY_LABELS.get(category, category)
    items = [(r, v) for r, v in agg.items() if r]
    if not items:
        return _annotated_empty(f"By region — {label}", height=height)

    # Plotly draws the first y entry at the bottom, so reverse the desc input to
    # put the largest-magnitude region at the TOP of the bar chart.
    items = list(reversed(items))
    regions = [r for r, _ in items]
    values = [v for _, v in items]

    if category == "net":
        colors = [C_HIGH if v > 0 else C_LOW for v in values]
    else:
        colors = [C_MACRO for _ in values]

    fig = go.Figure(go.Bar(
        x=values,
        y=regions,
        orientation="h",
        marker={"color": colors, "line": {"color": C_BG, "width": 0.5}},
        text=[f"{v:+.1f}M" if category == "net" else f"{v:.1f}M" for v in values],
        textposition="auto",
        textfont={"color": C_TEXT, "size": 11},
        hovertemplate="<b>%{y}</b><br>" f"{label}: " "%{x:.1f}M TEU<extra></extra>",
    ))
    apply_dark_layout(fig, title=f"{label} by region (M TEU/yr)", height=height)
    fig.update_layout(
        margin={"l": 8, "r": 8, "t": 44, "b": 8},
        xaxis={"title": "", "zeroline": True, "zerolinecolor": "rgba(255,255,255,0.18)"},
        yaxis={"title": ""},
    )
    return fig


# ── Render layer (Streamlit) ───────────────────────────────────────────────


def _category_value_color(pt: PortTEU, category: str) -> str:
    """Cell accent for a port's category value — sign-coloured for 'net'."""
    if category == "net":
        return C_HIGH if pt.teu_net > 0 else (C_LOW if pt.teu_net < 0 else C_TEXT2)
    return C_ACCENT


def _build_trend_lines(trends, *, top_n: int = 8, height: int = 420) -> go.Figure:
    """Multi-year container-throughput lines for the top ports by latest TEU.

    Pure builder — empty / no-multi-year input returns an annotated-empty figure.
    """
    have = [t for t in trends if t.n_years >= 2 and t.teu_by_year]
    have.sort(key=lambda t: t.teu_by_year[-1], reverse=True)
    have = have[:max(1, int(top_n))]
    if not have:
        return _annotated_empty("Throughput trends", height=height)
    palette = [C_ACCENT, C_MACRO, C_HIGH, C_MOD, C_LOW, C_TEXT2, C_TEXT3]
    fig = go.Figure()
    for i, t in enumerate(have):
        fig.add_trace(go.Scatter(
            x=t.years, y=t.teu_by_year, mode="lines+markers", name=t.name,
            line={"width": 1.8, "color": palette[i % len(palette)]},
            marker={"size": 5},
            hovertemplate="<b>" + t.name + "</b><br>%{x}: %{y:.1f}M TEU<extra></extra>",
        ))
    apply_dark_layout(fig, title="Container throughput by port (M TEU/yr)", height=height)
    fig.update_layout(
        margin={"l": 8, "r": 8, "t": 44, "b": 8},
        legend={"orientation": "h", "y": -0.14, "font": {"color": C_TEXT3, "size": 9}},
        hovermode="x unified",
    )
    return fig


def _build_connectivity_scatter(teus, *, height: int = 420) -> go.Figure:
    """Liner connectivity (real WB LSCI) vs container throughput (TEU).

    Global hubs sit top-right (high volume + well-connected). Pure builder —
    empty / no-connectivity input returns an annotated-empty figure.
    """
    pts = [pt for pt in teus if pt.connectivity > 0 and pt.teu_total > 0]
    if not pts:
        return _annotated_empty("Connectivity vs throughput", height=height)
    xs = [pt.teu_total for pt in pts]
    ys = [pt.connectivity for pt in pts]
    names = [pt.name for pt in pts]
    mx = max(xs) or 1.0
    sizes = [10.0 + 26.0 * (x / mx) for x in xs]
    fig = go.Figure(go.Scatter(
        x=xs, y=ys, mode="markers",
        text=names,
        marker={
            "size": sizes, "color": C_ACCENT,
            "line": {"color": C_BG, "width": 1.0}, "opacity": 0.85,
        },
        hovertemplate=(
            "<b>%{text}</b><br>Throughput: %{x:.1f}M TEU<br>"
            "Connectivity (LSCI): %{y:.0f}<extra></extra>"
        ),
    ))
    apply_dark_layout(
        fig, title="Liner connectivity vs throughput — global hubs sit top-right",
        height=height,
    )
    fig.update_layout(
        margin={"l": 8, "r": 8, "t": 44, "b": 44},
        xaxis={"title": "Container throughput (M TEU/yr)"},
        yaxis={"title": "Liner Shipping Connectivity Index"},
    )
    return fig


def render(wb_data=None, **_kwargs) -> None:
    """Render the Global TEU Throughput map tab.

    ``wb_data`` is the World Bank data dict (as returned by
    ``fetch_port_throughput``). When falsy it is fetched here. Every section is
    individually guarded so the tab never crashes — a failed section logs +
    surfaces ``st.error`` and the rest still renders.
    """
    from engine.perf_telemetry import track_render

    with track_render("teu_map"):
        page_header(
            title="Global TEU Throughput",
            subtitle=(
                "Container port traffic by port — total, imports, exports and "
                "net balance"
            ),
            badge_text="WB + MODELED",
            badge_color=C_MACRO,
        )

        # Honest lede: what is real vs modeled, and that it is not a forecast.
        st.caption(
            "Per-port totals are REAL World Bank country container traffic "
            "(IS.SHP.GOOD.TU) split across each country's ports by a MODELED "
            "weight. The import/export split is MODELED from each country's real "
            "merchandise export/import VALUE ratio — trade value is a proxy for "
            "box volume, so treat the split as indicative. Net = exports − "
            "imports. This is a snapshot, not a forecast."
        )

        # ── Data: use the passed dict, else fetch it (guarded → {}) ────────
        if not wb_data:
            try:
                from data.worldbank_feed import fetch_port_throughput
                wb_data = fetch_port_throughput()
            except Exception:
                logger.exception("teu_map: fetch_port_throughput failed")
                wb_data = {}

        try:
            teus = build_port_teu_map(wb_data)
        except Exception:
            logger.exception("teu_map: build_port_teu_map failed")
            teus = []

        # Honest no-data path: empty OR all-zero throughput → info + footer.
        if not teus or all(pt.teu_total <= 0 for pt in teus):
            st.info(
                "World Bank port-throughput data is currently unavailable, so "
                "the TEU map cannot be drawn. Check back once the cache "
                "refreshes."
            )
            try:
                st.markdown(source_footer([TEU_MAP_SOURCE]), unsafe_allow_html=True)
            except Exception:
                logger.exception("teu_map: source footer failed (no-data path)")
            return

        # ── Category selector — the single control driving every view ──────
        # Map the human label back to its CATEGORIES key.
        try:
            label_to_key = {CATEGORY_LABELS[k]: k for k in CATEGORIES}
            ordered_labels = [CATEGORY_LABELS[k] for k in CATEGORIES]
            picked_label = st.selectbox(
                "Category",
                options=ordered_labels,
                index=0,  # "total"
                key="teu_category",
                help=(
                    "Which slice of each port's container traffic to size and "
                    "rank by. 'Net' is exports − imports (modeled)."
                ),
            )
            category = label_to_key.get(picked_label, "total")
        except Exception:
            logger.exception("teu_map: category selector failed")
            category = "total"

        # ── Platform-wide KPI strip ────────────────────────────────────────
        try:
            s = summarize(teus)
            net = s["teu_net"]
            metric_card_row([
                {"label": "Total throughput", "value": f"{s['teu_total']:.1f}M",
                 "accent": C_ACCENT,
                 "sublabel": f"TEU/yr · {s['n_with_data']}/{s['n_ports']} ports w/ data"},
                {"label": "Exports (modeled)", "value": f"{s['teu_export']:.1f}M",
                 "accent": C_HIGH, "sublabel": "TEU/yr — outbound"},
                {"label": "Imports (modeled)", "value": f"{s['teu_import']:.1f}M",
                 "accent": C_MOD, "sublabel": "TEU/yr — inbound"},
                {"label": "Net balance", "value": f"{net:+.1f}M",
                 "accent": (C_HIGH if net > 0 else (C_LOW if net < 0 else C_TEXT2)),
                 "sublabel": "exports − imports (modeled)"},
            ], columns=4)
        except Exception:
            logger.exception("teu_map: KPI strip failed")
            st.error("Throughput summary unavailable.")

        section_divider("Map")

        # ── A. Geographic map (sized + coloured by the selected category) ──
        try:
            st.plotly_chart(
                _build_teu_geo(teus, category),
                use_container_width=True,
                config={"displayModeBar": False},
                key="teu_geo",
            )
        except Exception:
            logger.exception("teu_map: geo map failed")
            st.error("TEU map unavailable.")

        section_divider("Ranked ports")

        # ── B. Ranked table — top 15 by the selected category ──────────────
        try:
            top = rank_ports(teus, category, top_n=15)
            rows: list[list[str]] = []
            for pt in top:
                val_accent = _category_value_color(pt, category)
                rows.append([
                    badge(pt.name, color=val_accent),
                    badge(pt.region or "—", color=C_TEXT2),
                    badge(f"{pt.teu_total:.1f}M", color=C_TEXT2),
                    badge(f"{pt.teu_export:.1f}M", color=C_HIGH),
                    badge(f"{pt.teu_import:.1f}M", color=C_MOD),
                    badge(f"{pt.teu_net:+.1f}M",
                          color=(C_HIGH if pt.teu_net > 0
                                 else (C_LOW if pt.teu_net < 0 else C_TEXT2))),
                    badge(f"{pt.export_share * 100:.0f}%", color=C_ACCENT),
                ])
            wsj_market_table(
                ["Port", "Region", "Total", "Exports", "Imports", "Net", "Exp share"],
                rows,
                title=(
                    f"Top {len(rows)} ports by {CATEGORY_LABELS.get(category, category)} "
                    "(M TEU/yr)"
                ),
            )
        except Exception:
            logger.exception("teu_map: ranked table failed")
            st.error("Ranked-ports table unavailable.")

        section_divider("By region")

        # ── C. Region rollup — selected category summed by region ──────────
        try:
            agg = aggregate_by_region(teus, category)
            st.plotly_chart(
                _build_region_bar(agg, category),
                use_container_width=True,
                config={"displayModeBar": False},
                key="teu_region_bar",
            )
        except Exception:
            logger.exception("teu_map: region rollup failed")
            st.error("Region rollup unavailable.")

        section_divider("Throughput trends")

        # ── C2. Multi-year throughput trends (real WB annual container traffic) ─
        try:
            trends = build_port_teu_trends(wb_data)
            st.plotly_chart(
                _build_trend_lines(trends, top_n=8),
                use_container_width=True,
                config={"displayModeBar": False},
                key="teu_trends",
            )
            growth = rank_by_growth(trends, top_n=12)
            if growth:
                rows = []
                for t in growth:
                    g_color = C_HIGH if t.cagr_pct >= 0 else C_LOW
                    latest = t.teu_by_year[-1] if t.teu_by_year else 0.0
                    rows.append([
                        badge(t.name, color=C_TEXT2),
                        badge(t.region or "—", color=C_TEXT3),
                        badge(f"{latest:.1f}M", color=C_ACCENT),
                        badge(f"{t.cagr_pct:+.1f}%", color=g_color),
                        badge(f"{t.yoy_latest_pct:+.1f}%", color=g_color),
                    ])
                wsj_market_table(
                    ["Port", "Region", "Latest TEU", "CAGR", "YoY"],
                    rows,
                    title="Throughput growth — real World Bank multi-year (per-port split modeled)",
                )
            st.caption(
                "Compound annual growth of container throughput from real World "
                "Bank annual data; the per-port allocation is modeled. Descriptive "
                "history, not a forecast."
            )
        except Exception:
            logger.exception("teu_map: trends section failed")
            st.error("Throughput trends unavailable.")

        section_divider("Liner connectivity")

        # ── C3. Connectivity vs throughput (real WB Liner Shipping Connectivity) ─
        try:
            st.plotly_chart(
                _build_connectivity_scatter(teus),
                use_container_width=True,
                config={"displayModeBar": False},
                key="teu_connectivity",
            )
            st.caption(
                "Liner Shipping Connectivity Index (real World Bank / UNCTAD, "
                "country-level; China ~ baseline 100+) vs container throughput. "
                "Ports toward the top-right are the best-connected high-volume "
                "hubs. Throughput's per-port split is modeled; LSCI is real."
            )
        except Exception:
            logger.exception("teu_map: connectivity section failed")
            st.error("Connectivity view unavailable.")

        # ── D. Source footer ───────────────────────────────────────────────
        try:
            st.markdown(source_footer([TEU_MAP_SOURCE]), unsafe_allow_html=True)
        except Exception:
            logger.exception("teu_map: source footer failed")
