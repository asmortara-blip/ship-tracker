"""tab_supply_linkage.py — Supply Linkage (Disruption Alpha · stage 4).

The "Link" stage of the Disruption Alpha chain: it walks the visible hops from a
physical disruption to a portfolio-relevant exposure —

    disrupted lane  →  the commodity that lane carries  →  the ETF that proxies
    that commodity's demand  →  the shipping companies exposed to it.

Nothing here is a black box. Every figure traces back to a registry/profile
constant or a tracked ETF's 30-day move.

Canonical tab pattern (mirrors ``ui/tab_rate_analytics.py`` /
``ui/tab_voyage_tracker.py``):
  * palette + components imported from ``ui/styles.py`` — never redeclared;
  * ``render(...)`` ends with ``**kwargs`` for argument safety;
  * every section wrapped in try/except + ``logger.exception``;
  * expensive compute is ``@st.cache_data``-wrapped behind no-arg helpers so an
    unhashable ``stock_data`` / ``freight_data`` dict never reaches the cache key;
  * ``source_footer`` at the bottom, data labelled via ``data.quality.DataSource``.

Sections
--------
A. Page header (badge "MODELED") + a one-line editorial framing of the chain
B. Commodity KPI strip + disruption → commodity → company exposure table —
   one row per HS category, ordered by ETF-signal strength
C. Companies × commodities exposure heatmap (Plotly go.Heatmap)
D. Per-route cargo-mix drill-down — lane fact strip, selectbox of routes,
   cargo_analyzer breakdown bar + table
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    alert_banner,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# ── Domain-specific color mappings ──────────────────────────────────────────
# Keep *semantic* mappings (direction → color) local to the tab. Palette
# constants themselves live in ui/styles.py and are imported above.

_DIRECTION_COLOR: dict[str, str] = {
    "Bullish": C_HIGH,
    "Bearish": C_LOW,
    "Neutral": C_TEXT2,
}


def _direction_color(direction: str) -> str:
    return _DIRECTION_COLOR.get(direction, C_TEXT2)


def _change_color(change_30d: float) -> str:
    """Color a 30-day ETF move — green up, red down, grey flat."""
    if change_30d > 0.005:
        return C_HIGH
    if change_30d < -0.005:
        return C_LOW
    return C_TEXT2


def _stress_color(score: float) -> str:
    """Color a stress score in [0, 1] — green calm → red severe."""
    if score >= 0.65:
        return C_LOW
    if score >= 0.45:
        return C_MOD
    if score >= 0.25:
        return C_ACCENT
    return C_HIGH


# ── Cell formatters for the WSJ market table ────────────────────────────────
# wsj_market_table renders each cell string as raw HTML inside a <td>. These
# helpers only style cell *content* (font + color); the table CSS owns
# alignment, rule lines and hover.

def _mono(value: str, color: str = C_TEXT, weight: int = 400) -> str:
    """Monospace numeric cell content."""
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    """Sans-serif cell content."""
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _label_kicker(text: str, sub: str = "") -> str:
    """A two-line cell label — bold primary line plus a faint mono kicker.

    Used in the leftmost column so each row reads as 'name / context' without
    needing an extra table column.
    """
    primary = _sans(text, color=C_TEXT, weight=700)
    if not sub:
        return primary
    kicker = (
        f'<span style="display:block;font-family:var(--mono);color:{C_TEXT3};'
        f'font-size:0.66rem;letter-spacing:0.04em;margin-top:2px;'
        f'text-transform:uppercase;">{sub}</span>'
    )
    return primary + kicker


def _hop_arrow(color: str = C_TEXT3) -> str:
    """A small directional connector glyph for chain-of-exposure cells."""
    return (
        f'<span style="color:{color};font-family:var(--mono);'
        f'padding:0 2px;opacity:0.7;">&rsaquo;</span>'
    )


def _tickers_html(tickers: list[str], color: str) -> str:
    """Render a short ticker list as colored chips, or an em-dash when empty.

    Caps at five chips; a sixth-plus count is summarised as a faint ``+N`` so a
    crowded category never blows out the row height.
    """
    if not tickers:
        return _sans("—", color=C_TEXT3)
    shown = " ".join(badge(t, color=color) for t in tickers[:5])
    extra = len(tickers) - 5
    if extra > 0:
        shown += " " + _sans(f"+{extra}", color=C_TEXT3, weight=600)
    return shown


# ── Cached compute ──────────────────────────────────────────────────────────
# build_exposure_matrix / compute_shipping_stress take dict arguments
# (stock_data, freight_data, ...) whose values are pandas DataFrames — those
# dicts are unhashable, so they must NOT reach an @st.cache_data key. The
# voyage fleet, however, is self-contained and safe to cache behind a no-arg
# helper (the same pattern as ui/tab_voyage_tracker._load_fleet).

@st.cache_data(ttl=3600, show_spinner=False)
def _load_voyage_fleet() -> list:
    """Build (and cache) the modeled voyage fleet.

    The fleet is seeded from the current date inside ``build_voyage_fleet``,
    so it is stable within a session and refreshes day to day. Any failure
    degrades to an empty fleet — the SSI simply reports 0 delayed voyages.
    """
    try:
        from data.voyage_dataset import build_voyage_fleet
        return build_voyage_fleet()
    except Exception:
        logger.exception("Supply Linkage — voyage fleet build failed")
        return []


def _route_stress_lookup(
    freight_data: dict | None,
    macro_data: dict | None,
    port_results,
    route_results,
) -> dict[str, object]:
    """Return a ``route_id -> RouteStress`` map from the Shipping Stress Index.

    ``compute_shipping_stress`` already tolerates empty inputs and never raises;
    this wrapper additionally degrades to an empty map on any unexpected error
    so the exposure table still renders (stress columns simply read "n/a").
    """
    try:
        from processing.shipping_stress_index import compute_shipping_stress

        report = compute_shipping_stress(
            freight_data or {},
            macro_data or {},
            list(port_results) if port_results else [],
            list(route_results) if route_results else [],
            voyage_fleet=_load_voyage_fleet(),
        )
        return {rs.route_id: rs for rs in report.route_stress}
    except Exception:
        logger.exception("Supply Linkage — shipping stress compute failed")
        return {}


# ── Section B: disruption → commodity → company table ───────────────────────

def _render_exposure_table(
    stock_data: dict | None,
    stress_by_route: dict[str, object],
) -> None:
    """Render the headline disruption → commodity → company linkage table.

    One row per HS cargo category. Columns: commodity, ETF 30-day move, exposed
    routes, mean stress on those routes (looked up from the SSI), bullish
    companies, bearish companies.
    """
    from processing.exposure_matrix import build_exposure_matrix

    # build_exposure_matrix is a cheap pure derivation (the heavy
    # COMPANY_COMMODITY_EXPOSURE matrix is computed once at import); it tolerates
    # an empty / partial stock_data dict and returns Neutral rows. Call directly
    # — never through @st.cache_data, since stock_data is unhashable.
    matrix = build_exposure_matrix(stock_data or {})

    section_header(
        "Disruption → Commodity → Company",
        "Each HS cargo category, the ETF proxying its demand, the lanes that "
        "carry it, and the shipping names exposed — every hop visible",
    )

    if not matrix:
        alert_banner(
            "Exposure matrix unavailable — no HS cargo categories resolved "
            "from the registry.",
            level="warning",
        )
        return

    # Sort the table so the strongest reads surface first: live ETF signals
    # (largest absolute 30-day move) above Neutral / unproxied rows.
    ordered = sorted(
        matrix,
        key=lambda e: (e.direction == "Neutral", -abs(e.etf_price_change_30d)),
    )

    rows: list[list[str]] = []
    for exp in ordered:
        # Mean SSI stress across the routes this commodity dominates.
        route_scores = [
            float(getattr(stress_by_route[rid], "stress_score", 0.0))
            for rid in exp.affected_routes
            if rid in stress_by_route
        ]
        if route_scores:
            mean_stress = sum(route_scores) / len(route_scores)
            stress_cell = _mono(
                f"{mean_stress * 100:.0f}%",
                color=_stress_color(mean_stress),
                weight=600,
            )
        else:
            stress_cell = _sans("n/a", color=C_TEXT3)

        # ETF proxy reads as "TICKER › +x.x%" so the linkage hop is explicit.
        if exp.etf_ticker:
            etf_cell = (
                _sans(exp.etf_ticker, color=C_TEXT2, weight=600)
                + _hop_arrow()
                + _mono(
                    f"{exp.etf_price_change_30d:+.1%}",
                    color=_change_color(exp.etf_price_change_30d),
                    weight=600,
                )
            )
        else:
            etf_cell = _sans("no ETF proxy", color=C_TEXT3)

        route_n = len(exp.affected_routes)
        route_cell = (
            _mono(str(route_n), color=C_TEXT2 if route_n else C_TEXT3, weight=600)
            + _sans(" lane" + ("s" if route_n != 1 else ""), color=C_TEXT3)
        )

        rows.append([
            _label_kicker(exp.category_label, sub=exp.hs_category.replace("_", " ")),
            etf_cell,
            badge(exp.direction, color=_direction_color(exp.direction)),
            route_cell,
            stress_cell,
            _tickers_html(exp.bullish_companies, C_HIGH),
            _tickers_html(exp.bearish_companies, C_LOW),
        ])

    wsj_market_table(
        headers=[
            "Commodity", "ETF Proxy → 30d", "Signal",
            "Exposed Lanes", "Lane Stress", "Bullish Names", "Bearish Names",
        ],
        rows=rows,
    )

    st.caption(
        "Rows are ordered by ETF-signal strength — the sharpest 30-day moves "
        "first, Neutral and unproxied categories last. Lane Stress is the mean "
        "Shipping Stress Index score across the routes where the commodity is a "
        "dominant cargo; when an ETF is absent from the feed the row stays "
        "Neutral and stress reads n/a."
    )


# ── Section B helper: commodity KPI strip ───────────────────────────────────

def _render_commodity_kpis(stock_data: dict | None) -> None:
    """Render a compact KPI strip summarising the exposure matrix."""
    from processing.exposure_matrix import build_exposure_matrix

    matrix = build_exposure_matrix(stock_data or {})
    if not matrix:
        return

    bullish = sum(1 for e in matrix if e.direction == "Bullish")
    bearish = sum(1 for e in matrix if e.direction == "Bearish")
    neutral = sum(1 for e in matrix if e.direction == "Neutral")

    # Strongest mover (by absolute 30-day ETF change) with a real proxy.
    with_proxy = [e for e in matrix if e.etf_ticker]
    if with_proxy:
        mover = max(with_proxy, key=lambda e: abs(e.etf_price_change_30d))
        mover_value = f"{mover.etf_price_change_30d:+.1%}"
        mover_sub = f"{mover.category_label} · {mover.etf_ticker}"
        mover_accent = _change_color(mover.etf_price_change_30d)
    else:
        mover_value, mover_sub, mover_accent = "n/a", "no ETF proxies", C_TEXT2

    metric_card_row(
        [
            {
                "label": "Commodities Tracked",
                "value": str(len(matrix)),
                "accent": C_ACCENT,
                "sublabel": "HS cargo categories",
            },
            {
                "label": "Bullish Signals",
                "value": str(bullish),
                "accent": C_HIGH if bullish else C_TEXT2,
                "sublabel": "ETF demand rising",
            },
            {
                "label": "Bearish Signals",
                "value": str(bearish),
                "accent": C_LOW if bearish else C_TEXT2,
                "sublabel": "ETF demand falling",
            },
            {
                "label": "Neutral",
                "value": str(neutral),
                "accent": C_TEXT2,
                "sublabel": "flat / no proxy",
            },
            {
                "label": "Strongest Mover",
                "value": mover_value,
                "accent": mover_accent,
                "sublabel": mover_sub,
            },
        ],
        columns=5,
    )


# ── Section C: companies × commodities exposure heatmap ─────────────────────

def _render_exposure_heatmap() -> None:
    """Render a companies × commodities exposure heatmap.

    Each cell is a company's weight (share of cargo exposure, 0-1) on one HS
    category, straight from ``exposure_matrix.COMPANY_COMMODITY_EXPOSURE``.
    """
    from processing import cargo_analyzer
    from processing.company_profiler import COMPANY_PROFILES
    from processing.exposure_matrix import COMPANY_COMMODITY_EXPOSURE

    section_header(
        "Company × Commodity Exposure",
        "Each company's cargo-exposure weight across HS categories — darker "
        "means a larger share of that name's freight is the commodity",
    )

    if not COMPANY_COMMODITY_EXPOSURE:
        alert_banner(
            "Exposure matrix is empty — no companies resolved.",
            level="warning",
        )
        return

    # Stable category order from cargo_analyzer; readable labels for the axis.
    categories = list(cargo_analyzer.HS_CATEGORIES.keys())
    cat_labels = [
        cargo_analyzer.HS_CATEGORIES[c].get("label", c.title())
        for c in categories
    ]

    tickers = sorted(COMPANY_COMMODITY_EXPOSURE.keys())
    if not tickers or not categories:
        alert_banner(
            "Not enough exposure data to draw the heatmap.",
            level="warning",
        )
        return

    z: list[list[float]] = []
    text: list[list[str]] = []
    y_labels: list[str] = []
    for ticker in tickers:
        weights = COMPANY_COMMODITY_EXPOSURE.get(ticker, {})
        row = [float(weights.get(c, 0.0)) for c in categories]
        z.append(row)
        # Suppress near-zero cell labels so the eye lands on the real
        # concentrations rather than a grid of faint "0%"s.
        text.append([f"{v * 100:.0f}%" if v >= 0.03 else "" for v in row])
        name = str(COMPANY_PROFILES.get(ticker, {}).get("name", ticker))
        short = name if len(name) <= 22 else name[:21] + "…"
        y_labels.append(f"{ticker} · {short}")

    fig = go.Figure(go.Heatmap(
        z=z,
        x=cat_labels,
        y=y_labels,
        text=text,
        texttemplate="%{text}",
        textfont={"size": 10, "color": C_TEXT, "family": "JetBrains Mono"},
        colorscale=[
            [0.0, C_CARD],            # ~no exposure — matches the card surface
            [0.15, "#1d3a55"],
            [0.45, C_ACCENT],
            [1.0, C_HIGH],            # heavily concentrated
        ],
        zmin=0.0,
        zmax=max(0.5, max((v for row in z for v in row), default=0.5)),
        showscale=True,
        colorbar=dict(
            title=dict(text="WEIGHT", font=dict(color=C_TEXT3, size=10)),
            tickfont=dict(color=C_TEXT2, size=10),
            thickness=12,
            len=0.85,
            outlinewidth=0,
            tickformat=".0%",
        ),
        hovertemplate=(
            "<b>%{y}</b><br>%{x}<br>exposure weight: %{z:.0%}<extra></extra>"
        ),
        xgap=2,
        ygap=2,
    ))
    apply_dark_layout(
        fig,
        height=max(280, 52 * len(tickers) + 110),
        showlegend=False,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        xaxis=dict(side="top", tickfont=dict(color=C_TEXT2, size=11)),
        yaxis=dict(autorange="reversed", tickfont=dict(color=C_TEXT2, size=10)),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False},
        key="supply_linkage_heatmap",
    )

    st.caption(
        "Weights are derived at import from each company's trade routes averaged "
        "through the cargo-mix model, tilted toward its sector and lightly hand-"
        "tuned for credibility. Each company's row sums to 100%."
    )


# ── Section D: per-route cargo-mix drill-down ───────────────────────────────

def _render_cargo_drilldown(trade_data: dict | None) -> None:
    """Render a per-route cargo-mix drill-down.

    A selectbox of canonical routes; for the chosen route, the HS-category cargo
    mix from ``cargo_analyzer.get_route_cargo_mix`` is shown as a horizontal bar
    plus a supporting table.
    """
    from processing import cargo_analyzer
    from routes.route_registry import ROUTES, ROUTES_BY_ID

    section_header(
        "Route Cargo-Mix Drill-Down",
        "Pick a lane to see the cargo it carries — the commodity layer that "
        "links a disruption on that route to exposed companies",
    )

    if not ROUTES:
        alert_banner(
            "Route registry is empty — no lanes to drill into.",
            level="warning",
        )
        return

    # Stable, human-readable label per route.
    label_map = {f"{r.name} ({r.id})": r.id for r in ROUTES}
    chosen_label = st.selectbox(
        "Select a route",
        options=list(label_map.keys()),
        key="supply_linkage_route_select",
    )
    route_id = label_map.get(chosen_label, ROUTES[0].id)
    route = ROUTES_BY_ID.get(route_id)

    # get_route_cargo_mix returns illustrative, normalised weights when
    # trade_data is empty — it never raises.
    mix = cargo_analyzer.get_route_cargo_mix(route_id, trade_data or {})
    mix_items = sorted(
        ((k, v) for k, v in mix.items() if v > 0),
        key=lambda kv: kv[1],
        reverse=True,
    )

    if not mix_items:
        alert_banner(
            f"No cargo-mix data available for {chosen_label}.",
            level="info",
        )
        return

    # Lane fact strip — orients the reader before the cargo breakdown.
    top_key, top_share = mix_items[0]
    top_label = cargo_analyzer.HS_CATEGORIES.get(top_key, {}).get(
        "label", top_key.title()
    )
    metric_card_row(
        [
            {
                "label": "Lane",
                "value": (
                    f"{route.origin_locode} → {route.dest_locode}"
                    if route is not None else chosen_label
                ),
                "accent": C_ACCENT,
                "sublabel": (
                    f"origin region · {route.origin_region}"
                    if route is not None else "registry route"
                ),
            },
            {
                "label": "Dominant Cargo",
                "value": top_label,
                "accent": C_HIGH,
                "sublabel": f"{top_share * 100:.0f}% of route volume",
            },
            {
                "label": "Cargo Categories",
                "value": str(len(mix_items)),
                "accent": C_MOD,
                "sublabel": "HS classes carried",
            },
        ],
        columns=3,
    )

    col_chart, col_table = st.columns([3, 2], gap="large")

    labels = [
        cargo_analyzer.HS_CATEGORIES.get(k, {}).get("label", k.title())
        for k, _ in mix_items
    ]
    values = [v * 100.0 for _, v in mix_items]

    with col_chart:
        fig = go.Figure(go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker=dict(
                color=values,
                colorscale=[[0.0, C_ACCENT], [1.0, C_HIGH]],
                line=dict(width=0),
            ),
            text=[f"{v:.0f}%" for v in values],
            textposition="outside",
            textfont=dict(color=C_TEXT2, size=11, family="JetBrains Mono"),
            cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>%{x:.1f}% of route cargo<extra></extra>",
        ))
        apply_dark_layout(
            fig,
            height=max(240, 44 * len(mix_items) + 80),
            showlegend=False,
            margin={"l": 10, "r": 48, "t": 10, "b": 34},
            bargap=0.34,
            xaxis=dict(
                title=dict(text="SHARE OF ROUTE CARGO",
                           font=dict(color=C_TEXT3, size=10)),
                ticksuffix="%",
                range=[0, max(values) * 1.2],
            ),
            yaxis=dict(autorange="reversed", tickfont=dict(size=11)),
        )
        st.plotly_chart(
            fig,
            use_container_width=True,
            config={"displayModeBar": False},
            key="supply_linkage_cargo_bar",
        )

    with col_table:
        from processing.exposure_matrix import COMMODITY_ETF_MAP

        rows = []
        for (key, _), label, value in zip(mix_items, labels, values):
            etf = COMMODITY_ETF_MAP.get(key, "")
            etf_cell = (
                _sans(etf, color=C_TEXT2, weight=600)
                if etf else _sans("—", color=C_TEXT3)
            )
            rows.append([
                _sans(label, color=C_TEXT, weight=600),
                _mono(f"{value:.1f}%", color=C_TEXT, weight=600),
                etf_cell,
            ])
        wsj_market_table(
            headers=["Cargo", "Share", "ETF Proxy"],
            rows=rows,
        )
        st.caption(
            f"Cargo shares sum to ~{sum(values):.0f}%. The ETF Proxy is the "
            "demand instrument carrying that commodity's signal into the "
            "exposure table above."
        )


# ── Public entry point ──────────────────────────────────────────────────────

def render(
    stock_data=None,
    freight_data=None,
    macro_data=None,
    port_results=None,
    route_results=None,
    trade_data=None,
    **kwargs,
) -> None:
    """Render the Supply Linkage tab (Disruption Alpha · stage 4).

    Parameters
    ----------
    stock_data:
        Mapping ``ticker -> DataFrame`` of equity / ETF history. May be ``{}``,
        ``None`` or missing some commodity ETFs — ``build_exposure_matrix``
        degrades those rows to Neutral, so the table still renders every HS
        category.
    freight_data, macro_data, port_results, route_results:
        Platform-standard analysis inputs, forwarded to the Shipping Stress
        Index to populate the per-commodity lane-stress column. All may be
        empty / ``None`` — stress simply reads "n/a" in that case.
    trade_data:
        Optional port-keyed trade DataFrames for the route cargo-mix drill-down.
        When absent, ``cargo_analyzer`` uses illustrative fallback weights.
    **kwargs:
        Accepted for call-site symmetry with the rest of the platform.

    The function is robust to all-``None`` / empty inputs — every section is
    wrapped in try/except and degrades gracefully rather than raising.
    """
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('supply_linkage'):
        # ── A. Page header ──────────────────────────────────────────────────────
        page_header(
            title="Supply Linkage",
            subtitle="From a disrupted lane to an exposed company — the commodity "
            "layer that links physical shipping stress to shipping equities",
            badge_text="MODELED",
            badge_color=C_ACCENT,
        )

        # Editorial framing — the four-hop chain this tab walks, stated once so the
        # sections below read as one continuous argument rather than four widgets.
        try:
            alert_banner(
                "Stage 4 of the Disruption Alpha chain. This tab walks four visible "
                "hops — <b>disrupted lane → the commodity that lane carries → the "
                "ETF proxying that commodity's demand → the shipping names exposed "
                "to it</b>. Nothing is a black box: every figure traces back to a "
                "registry constant or a tracked ETF's 30-day move.",
                level="info",
            )
        except Exception:
            logger.exception("Supply Linkage — intro banner failed")

        # ── Shared compute — the per-route Shipping Stress Index ────────────────
        # Computed once and shared by Section B. compute_shipping_stress already
        # tolerates empty inputs; the wrapper degrades to {} on any failure.
        stress_by_route = _route_stress_lookup(
            freight_data, macro_data, port_results, route_results
        )

        # ── B. Commodity KPI strip ──────────────────────────────────────────────
        try:
            _render_commodity_kpis(stock_data)
        except Exception:
            logger.exception("Supply Linkage — commodity KPI strip failed")
            st.error("Commodity summary unavailable.")

        section_divider("Exposure Linkage")

        # ── B. Disruption → commodity → company table ───────────────────────────
        try:
            _render_exposure_table(stock_data, stress_by_route)
        except Exception:
            logger.exception("Supply Linkage — exposure table failed")
            st.error("Exposure table unavailable.")

        section_divider("Concentration")

        # ── C. Company × commodity exposure heatmap ─────────────────────────────
        try:
            _render_exposure_heatmap()
        except Exception:
            logger.exception("Supply Linkage — exposure heatmap failed")
            st.error("Exposure heatmap unavailable.")

        section_divider("Route Detail")

        # ── D. Per-route cargo-mix drill-down ───────────────────────────────────
        try:
            _render_cargo_drilldown(trade_data)
        except Exception:
            logger.exception("Supply Linkage — cargo drill-down failed")
            st.error("Route cargo-mix drill-down unavailable.")

        # ── Provenance footer ───────────────────────────────────────────────────
        try:
            from data.quality import DataSource

            sources = [
                DataSource.modeled(
                    "Modeled Commodity Exposure Matrix",
                    notes="Company↔commodity weights derived from trade routes via "
                    "the cargo-mix model; HS categories mapped to tracked ETFs.",
                ),
                DataSource.modeled(
                    "Shipping Stress Index",
                    notes="Per-route disruption composite — chokepoint, congestion, "
                    "weather, rate and vulnerability.",
                ),
            ]
            st.markdown(source_footer(sources), unsafe_allow_html=True)
        except Exception:
            logger.exception("Supply Linkage — source footer failed")
