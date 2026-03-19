"""tab_cargo.py — Cargo type and product category deep-dive tab.

Renders five sections:
  1. Category overview grid (7 cards, 3-2-2 layout)
  2. Cargo-flow Sankey diagram
  3. Seasonal cargo calendar (12-month grid)
  4. Route cargo-mix selector + donut chart
  5. Cargo value trend (line or bar chart)
"""

from __future__ import annotations

import calendar as _cal

import plotly.graph_objects as go
import streamlit as st

from processing.cargo_analyzer import (
    CARGO_CHARACTERISTICS,
    HS_CATEGORIES,
    CargoFlowAnalysis,
    analyze_cargo_flows,
    get_route_cargo_mix,
    get_seasonal_cargo_calendar,
)
from utils.helpers import format_usd

# ---------------------------------------------------------------------------
# Color palette (mirrors styles.py)
# ---------------------------------------------------------------------------
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_CARD    = "#1a2235"
_C_BORDER  = "rgba(255,255,255,0.08)"
_C_HIGH    = "#10b981"
_C_GROW    = "#3b82f6"
_C_STABLE  = "#64748b"
_C_DECLINE = "#ef4444"
_C_WARN    = "#f59e0b"
_C_ACCENT  = "#3b82f6"
_C_TEXT    = "#f1f5f9"
_C_TEXT2   = "#94a3b8"
_C_TEXT3   = "#64748b"

# Category icons
_ICONS: dict[str, str] = {
    "electronics": "🖥️",
    "machinery":   "⚙️",
    "automotive":  "🚗",
    "apparel":     "👕",
    "chemicals":   "🧪",
    "agriculture": "🌾",
    "metals":      "🔩",
}

# Sankey node colors per category
_CAT_COLORS: dict[str, str] = {
    "electronics": "#3b82f6",
    "machinery":   "#f59e0b",
    "automotive":  "#8b5cf6",
    "apparel":     "#ec4899",
    "chemicals":   "#14b8a6",
    "agriculture": "#84cc16",
    "metals":      "#94a3b8",
}

# Signal-to-color map
_SIGNAL_COLORS: dict[str, str] = {
    "SURGING":  _C_HIGH,
    "GROWING":  _C_GROW,
    "STABLE":   _C_STABLE,
    "DECLINING": _C_DECLINE,
}

# All route ids for the selectbox
_ALL_ROUTES: list[str] = [
    "transpacific_eb",
    "asia_europe",
    "transpacific_wb",
    "transatlantic",
    "sea_transpacific_eb",
    "ningbo_europe",
    "middle_east_to_europe",
    "middle_east_to_asia",
    "south_asia_to_europe",
    "intra_asia_china_sea",
    "intra_asia_china_japan",
    "china_south_america",
    "europe_south_america",
    "med_hub_to_asia",
    "north_africa_to_europe",
    "us_east_south_america",
    "longbeach_to_asia",
]

_ROUTE_LABELS: dict[str, str] = {
    "transpacific_eb":       "Trans-Pacific Eastbound",
    "asia_europe":           "Asia-Europe",
    "transpacific_wb":       "Trans-Pacific Westbound",
    "transatlantic":         "Transatlantic",
    "sea_transpacific_eb":   "SE Asia Eastbound",
    "ningbo_europe":         "Ningbo-Europe via Suez",
    "middle_east_to_europe": "Middle East to Europe",
    "middle_east_to_asia":   "Middle East to Asia",
    "south_asia_to_europe":  "South Asia to Europe",
    "intra_asia_china_sea":  "Intra-Asia: China to SE Asia",
    "intra_asia_china_japan":"Intra-Asia: China to Japan/Korea",
    "china_south_america":   "China to South America",
    "europe_south_america":  "Europe to South America",
    "med_hub_to_asia":       "Mediterranean Hub to Asia",
    "north_africa_to_europe":"North Africa to Europe",
    "us_east_south_america": "US East Coast to South America",
    "longbeach_to_asia":     "Long Beach to Asia",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _divider(label: str) -> None:
    st.markdown(
        '<div style="display:flex;align-items:center;gap:12px;margin:28px 0">'
        '<div style="flex:1;height:1px;background:rgba(255,255,255,0.06)"></div>'
        '<span style="font-size:0.65rem;color:#475569;text-transform:uppercase;'
        'letter-spacing:0.12em">' + label + "</span>"
        '<div style="flex:1;height:1px;background:rgba(255,255,255,0.06)"></div>'
        "</div>",
        unsafe_allow_html=True,
    )


def _badge(text: str, color: str, text_color: str = "#fff") -> str:
    """Return an inline HTML badge string."""
    return (
        '<span style="background:' + color + ";color:" + text_color
        + ";font-size:0.68rem;font-weight:700;padding:2px 8px;"
        + 'border-radius:4px;letter-spacing:0.05em">' + text + "</span>"
    )


def _card_html(analysis: CargoFlowAnalysis) -> str:
    icon = _ICONS.get(analysis.hs_category, "📦")
    value_str = (
        format_usd(analysis.total_value_usd)
        if analysis.total_value_usd > 0
        else "Data pending"
    )
    yoy_sign = "+" if analysis.yoy_growth_pct >= 0 else ""
    yoy_color = _C_HIGH if analysis.yoy_growth_pct >= 0 else _C_DECLINE
    signal_color = _SIGNAL_COLORS.get(analysis.demand_signal, _C_STABLE)

    return (
        '<div style="background:' + _C_CARD
        + ";border:1px solid " + _C_BORDER
        + ";border-radius:12px;padding:16px 18px;height:100%;"
        + 'display:flex;flex-direction:column;gap:8px">'
        # header row
        + '<div style="display:flex;align-items:center;gap:10px">'
        + '<span style="font-size:1.6rem">' + icon + "</span>"
        + '<span style="font-size:0.95rem;font-weight:700;color:' + _C_TEXT + '">'
        + analysis.category_label
        + "</span></div>"
        # value row
        + '<div style="font-size:1.25rem;font-weight:800;color:' + _C_TEXT + '">'
        + value_str
        + "</div>"
        # badges row
        + '<div style="display:flex;gap:6px;flex-wrap:wrap">'
        + _badge(yoy_sign + str(round(analysis.yoy_growth_pct, 1)) + "% YoY", yoy_color)
        + _badge(analysis.demand_signal, signal_color)
        + "</div>"
        # insight
        + '<div style="font-size:0.72rem;color:' + _C_TEXT2
        + ";line-height:1.4;margin-top:4px\">"
        + analysis.key_insight[:120] + ("…" if len(analysis.key_insight) > 120 else "")
        + "</div>"
        + "</div>"
    )


# ---------------------------------------------------------------------------
# Section 1 – Category Overview Grid
# ---------------------------------------------------------------------------

def _render_category_grid(flows: list[CargoFlowAnalysis]) -> None:
    _divider("CARGO CATEGORY OVERVIEW")

    ordered_keys = ["electronics", "machinery", "automotive", "apparel", "chemicals", "agriculture", "metals"]
    flow_map = {f.hs_category: f for f in flows}

    # Row 1: 3 cards
    cols_r1 = st.columns(3)
    for idx, key in enumerate(ordered_keys[:3]):
        f = flow_map.get(key)
        if f:
            with cols_r1[idx]:
                st.markdown(_card_html(f), unsafe_allow_html=True)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # Row 2: 2 cards
    cols_r2 = st.columns([1, 1, 0.001])
    for idx, key in enumerate(ordered_keys[3:5]):
        f = flow_map.get(key)
        if f:
            with cols_r2[idx]:
                st.markdown(_card_html(f), unsafe_allow_html=True)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # Row 3: 2 cards
    cols_r3 = st.columns([1, 1, 0.001])
    for idx, key in enumerate(ordered_keys[5:7]):
        f = flow_map.get(key)
        if f:
            with cols_r3[idx]:
                st.markdown(_card_html(f), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 2 – Cargo Flow Sankey
# ---------------------------------------------------------------------------

def _render_sankey(flows: list[CargoFlowAnalysis]) -> None:
    _divider("CARGO FLOW SANKEY — CATEGORY → REGION → DESTINATION")

    # Node layout: categories → origin regions → dest regions
    categories   = list(HS_CATEGORIES.keys())
    orig_regions = ["Asia East", "Europe", "North America West", "North America East", "Southeast Asia", "Middle East", "South America", "South Asia", "Africa"]
    dest_regions = ["North America West", "North America East", "Europe", "Asia East", "Southeast Asia", "South America", "Middle East"]

    # Build unique node list: categories + orig_regions (with "Orig:" prefix) + dest_regions (with "Dest:" prefix)
    nodes: list[str] = (
        categories
        + ["Orig: " + r for r in orig_regions]
        + ["Dest: " + r for r in dest_regions]
    )
    node_idx = {n: i for i, n in enumerate(nodes)}

    # Node colors
    node_colors = (
        [_CAT_COLORS.get(c, "#64748b") for c in categories]
        + ["rgba(59,130,246,0.6)"] * len(orig_regions)
        + ["rgba(16,185,129,0.6)"] * len(dest_regions)
    )

    # Build flow_map for quick lookup
    flow_map = {f.hs_category: f for f in flows}

    # Illustrative region-to-region flows (cat -> orig region -> dest region)
    _CAT_REGION_FLOWS: list[tuple[str, str, str, float]] = [
        # (category, orig_region, dest_region, weight)
        ("electronics",  "Asia East",          "North America West", 0.38),
        ("electronics",  "Asia East",          "Europe",             0.28),
        ("electronics",  "Southeast Asia",     "North America West", 0.18),
        ("machinery",    "Asia East",          "Europe",             0.30),
        ("machinery",    "Europe",             "North America East", 0.28),
        ("machinery",    "Asia East",          "North America West", 0.20),
        ("automotive",   "Asia East",          "North America West", 0.35),
        ("automotive",   "Europe",             "North America East", 0.30),
        ("apparel",      "Asia East",          "North America West", 0.30),
        ("apparel",      "Asia East",          "Europe",             0.25),
        ("apparel",      "South Asia",         "Europe",             0.22),
        ("chemicals",    "Europe",             "Asia East",          0.28),
        ("chemicals",    "Middle East",        "Europe",             0.26),
        ("chemicals",    "North America West", "Asia East",          0.22),
        ("agriculture",  "North America West", "Asia East",          0.38),
        ("agriculture",  "South America",      "Asia East",          0.28),
        ("agriculture",  "North America East", "Europe",             0.18),
        ("metals",       "Asia East",          "North America West", 0.30),
        ("metals",       "Asia East",          "Europe",             0.26),
        ("metals",       "Middle East",        "Europe",             0.20),
    ]

    sources: list[int] = []
    targets: list[int] = []
    values_:  list[float] = []
    link_colors: list[str] = []

    for cat, orig, dest, weight in _CAT_REGION_FLOWS:
        cat_node  = cat
        orig_node = "Orig: " + orig
        dest_node = "Dest: " + dest

        if cat_node not in node_idx or orig_node not in node_idx or dest_node not in node_idx:
            continue

        # Scale by total value if available
        scale = flow_map[cat].total_value_usd if cat in flow_map else 1_000_000_000
        flow_val = scale * weight / 1e8  # normalise to Sankey unit

        # cat -> orig
        sources.append(node_idx[cat_node])
        targets.append(node_idx[orig_node])
        values_.append(flow_val)
        link_colors.append(_CAT_COLORS.get(cat, "#64748b").replace(")", ",0.35)").replace("rgb", "rgba").replace("#", "rgba(") if "#" in _CAT_COLORS.get(cat, "") else "rgba(100,116,139,0.35)")

        # orig -> dest
        sources.append(node_idx[orig_node])
        targets.append(node_idx[dest_node])
        values_.append(flow_val)
        link_colors.append("rgba(148,163,184,0.25)")

    # Build proper hex -> rgba conversion for link colors
    def _hex_to_rgba(hex_color: str, alpha: float = 0.35) -> str:
        h = hex_color.lstrip("#")
        if len(h) == 6:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return "rgba(" + str(r) + "," + str(g) + "," + str(b) + "," + str(alpha) + ")"
        return "rgba(100,116,139," + str(alpha) + ")"

    link_colors_clean: list[str] = []
    for cat, orig, dest, weight in _CAT_REGION_FLOWS:
        hex_c = _CAT_COLORS.get(cat, "#64748b")
        link_colors_clean.append(_hex_to_rgba(hex_c, 0.40))
        link_colors_clean.append("rgba(148,163,184,0.20)")

    fig = go.Figure(
        go.Sankey(
            arrangement="snap",
            node=dict(
                pad=18,
                thickness=18,
                line=dict(color="rgba(255,255,255,0.1)", width=0.5),
                label=nodes,
                color=node_colors,
                hovertemplate="<b>%{label}</b><extra></extra>",
            ),
            link=dict(
                source=sources,
                target=targets,
                value=values_,
                color=link_colors_clean,
                hovertemplate="Flow: %{value:.1f} units<extra></extra>",
            ),
        )
    )
    fig.update_layout(
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        height=400,
        margin=dict(t=20, b=20, l=20, r=20),
        font=dict(color=_C_TEXT, family="Inter, sans-serif", size=11),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Flow weights are illustrative when live Comtrade data is unavailable. Thickness proportional to estimated trade value.")


# ---------------------------------------------------------------------------
# Section 3 – Seasonal Cargo Calendar
# ---------------------------------------------------------------------------

def _render_seasonal_calendar() -> None:
    _divider("SEASONAL CARGO CALENDAR — PEAK DEMAND BY MONTH")

    import datetime
    current_month = datetime.date.today().month

    cal = get_seasonal_cargo_calendar()

    months_ordered = list(range(1, 13))
    rows = [months_ordered[0:4], months_ordered[4:8], months_ordered[8:12]]

    for row_months in rows:
        cols = st.columns(4)
        for col, month_num in zip(cols, row_months):
            with col:
                month_name = _cal.month_abbr[month_num]
                is_current = (month_num == current_month)
                border_style = (
                    "2px solid " + _C_ACCENT if is_current
                    else "1px solid " + _C_BORDER
                )
                bg = "#1a2a3a" if is_current else _C_CARD

                peak_cats = cal.get(month_num, [])
                badges_html = ""
                for cat in peak_cats:
                    icon = _ICONS.get(cat, "📦")
                    color = _CAT_COLORS.get(cat, "#64748b")
                    label = HS_CATEGORIES.get(cat, {}).get("label", cat.title())
                    badges_html += (
                        '<span style="background:' + color
                        + ";color:#fff;font-size:0.62rem;font-weight:700;"
                        + "padding:2px 6px;border-radius:4px;display:inline-block;"
                        + 'margin:2px 2px 0 0">' + icon + " " + label + "</span>"
                    )

                current_marker = " ◀ NOW" if is_current else ""
                html = (
                    '<div style="background:' + bg
                    + ";border:" + border_style
                    + ";border-radius:10px;padding:12px 10px;min-height:90px\">"
                    + '<div style="font-size:0.8rem;font-weight:700;color:' + _C_TEXT
                    + ';margin-bottom:6px">' + month_name + current_marker + "</div>"
                    + (badges_html if badges_html else '<span style="color:' + _C_TEXT3 + ';font-size:0.65rem">No peak</span>')
                    + "</div>"
                )
                st.markdown(html, unsafe_allow_html=True)

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    st.caption("Peak months sourced from CARGO_CHARACTERISTICS seasonal_peak data. Colours match category palette above.")


# ---------------------------------------------------------------------------
# Section 4 – Route Cargo Mix
# ---------------------------------------------------------------------------

def _render_route_cargo_mix(trade_data: dict, route_results: list) -> None:
    _divider("ROUTE CARGO MIX BREAKDOWN")

    route_options = _ALL_ROUTES
    route_display = [_ROUTE_LABELS.get(r, r) for r in route_options]

    # Pre-select based on route_results if available
    default_idx = 0
    if route_results:
        try:
            first_route = route_results[0]
            route_id = getattr(first_route, "route_id", None) or getattr(first_route, "id", None)
            if route_id and route_id in route_options:
                default_idx = route_options.index(route_id)
        except (IndexError, AttributeError):
            pass

    selected_label = st.selectbox(
        "Select a shipping route",
        options=route_display,
        index=default_idx,
        key="cargo_route_selectbox",
    )
    selected_route = route_options[route_display.index(selected_label)]

    mix = get_route_cargo_mix(selected_route, trade_data)

    # Sort by share descending
    sorted_mix = sorted(mix.items(), key=lambda x: x[1], reverse=True)
    labels = [HS_CATEGORIES.get(k, {}).get("label", k.title()) for k, _ in sorted_mix]
    values = [v * 100 for _, v in sorted_mix]
    colors = [_CAT_COLORS.get(k, "#64748b") for k, _ in sorted_mix]
    icons  = [_ICONS.get(k, "📦") for k, _ in sorted_mix]
    label_display = [icons[i] + " " + labels[i] for i in range(len(labels))]

    col_chart, col_table = st.columns([1, 1])

    with col_chart:
        fig = go.Figure(
            go.Pie(
                labels=label_display,
                values=values,
                hole=0.55,
                marker=dict(colors=colors, line=dict(color=_C_BG, width=2)),
                textinfo="label+percent",
                textfont=dict(size=11, color=_C_TEXT),
                hovertemplate="<b>%{label}</b><br>Share: %{percent}<extra></extra>",
            )
        )
        fig.update_layout(
            paper_bgcolor=_C_BG,
            plot_bgcolor=_C_BG,
            height=320,
            margin=dict(t=20, b=20, l=20, r=20),
            legend=dict(
                font=dict(color=_C_TEXT2, size=10),
                bgcolor="rgba(0,0,0,0)",
            ),
            annotations=[
                dict(
                    text="<b>Cargo Mix</b>",
                    x=0.5, y=0.5,
                    font=dict(size=12, color=_C_TEXT),
                    showarrow=False,
                )
            ],
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_table:
        st.markdown(
            '<div style="background:' + _C_CARD + ";border:1px solid " + _C_BORDER
            + ";border-radius:10px;padding:14px 16px\">",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div style="font-size:0.75rem;font-weight:700;color:' + _C_TEXT2
            + ';margin-bottom:10px;text-transform:uppercase;letter-spacing:0.08em">'
            + "Shipping Characteristics</div>",
            unsafe_allow_html=True,
        )
        for cat_key, share in sorted_mix:
            chars = CARGO_CHARACTERISTICS.get(cat_key, {})
            shipping = chars.get("shipping", "standard container")
            sensitivity = chars.get("sensitivity", "—")
            icon = _ICONS.get(cat_key, "📦")
            label = HS_CATEGORIES.get(cat_key, {}).get("label", cat_key.title())
            color = _CAT_COLORS.get(cat_key, "#64748b")
            pct_str = str(round(share * 100, 1)) + "%"

            st.markdown(
                '<div style="display:flex;align-items:flex-start;gap:8px;'
                + "margin-bottom:8px;padding-bottom:8px;"
                + "border-bottom:1px solid rgba(255,255,255,0.05)\">"
                + '<span style="font-size:1.1rem">' + icon + "</span>"
                + "<div style='flex:1'>"
                + '<div style="display:flex;justify-content:space-between">'
                + '<span style="font-size:0.78rem;font-weight:700;color:' + color + '">' + label + "</span>"
                + '<span style="font-size:0.78rem;color:' + _C_TEXT + ';font-weight:700">' + pct_str + "</span>"
                + "</div>"
                + '<div style="font-size:0.68rem;color:' + _C_TEXT3 + '">'
                + shipping + " | sensitivity: " + sensitivity
                + "</div>"
                + "</div></div>",
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 5 – Cargo Value Trend
# ---------------------------------------------------------------------------

def _render_value_trend(trade_data: dict, flows: list[CargoFlowAnalysis]) -> None:
    _divider("CARGO VALUE TREND BY CATEGORY")

    # Check whether trade_data contains time-series data
    has_time_series = False
    if trade_data:
        for locode, df in trade_data.items():
            if df is not None and hasattr(df, "columns") and not df.empty:
                if "period" in df.columns or "date" in df.columns or "year" in df.columns:
                    has_time_series = True
                    break

    if has_time_series:
        # Build per-category time-series from real data
        time_col = None
        sample_df = next(
            (df for df in trade_data.values() if df is not None and not df.empty),
            None,
        )
        if sample_df is not None:
            for candidate in ("period", "date", "year"):
                if candidate in sample_df.columns:
                    time_col = candidate
                    break

        if time_col:
            import pandas as pd
            cat_series: dict[str, dict] = {}
            for locode, df in trade_data.items():
                if df is None or df.empty or "hs_category" not in df.columns:
                    continue
                if time_col not in df.columns or "value_usd" not in df.columns:
                    continue
                for cat_key in HS_CATEGORIES:
                    cat_df = df[df["hs_category"] == cat_key]
                    if cat_df.empty:
                        continue
                    grouped = cat_df.groupby(time_col)["value_usd"].sum()
                    for period, val in grouped.items():
                        cat_series.setdefault(cat_key, {})[period] = (
                            cat_series.get(cat_key, {}).get(period, 0) + val
                        )

            fig = go.Figure()
            for cat_key, ts in cat_series.items():
                if not ts:
                    continue
                periods = sorted(ts.keys())
                values = [ts[p] / 1e9 for p in periods]
                fig.add_trace(
                    go.Scatter(
                        x=periods,
                        y=values,
                        mode="lines+markers",
                        name=_ICONS.get(cat_key, "") + " " + HS_CATEGORIES[cat_key]["label"],
                        line=dict(color=_CAT_COLORS.get(cat_key, "#64748b"), width=2),
                        marker=dict(size=5),
                        hovertemplate="<b>%{fullData.name}</b><br>Period: %{x}<br>Value: $%{y:.2f}B<extra></extra>",
                    )
                )
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor=_C_BG,
                plot_bgcolor=_C_SURFACE,
                height=380,
                margin=dict(t=24, b=40, l=60, r=24),
                xaxis=dict(title="Period", color=_C_TEXT2, gridcolor="rgba(255,255,255,0.05)"),
                yaxis=dict(title="Trade Value (USD Billion)", color=_C_TEXT2, gridcolor="rgba(255,255,255,0.05)"),
                legend=dict(font=dict(size=10, color=_C_TEXT2), bgcolor="rgba(0,0,0,0)"),
            )
            st.plotly_chart(fig, use_container_width=True)
            return

    # Fallback: bar chart using benchmark/computed totals from flows
    flow_map = {f.hs_category: f for f in flows}
    labels: list[str] = []
    values_b: list[float] = []
    bar_colors: list[str] = []
    hover_texts: list[str] = []

    for cat_key in HS_CATEGORIES:
        f = flow_map.get(cat_key)
        if not f:
            continue
        labels.append(_ICONS.get(cat_key, "") + " " + f.category_label)
        values_b.append(f.total_value_usd / 1e9)
        bar_colors.append(_CAT_COLORS.get(cat_key, "#64748b"))
        yoy_sign = "+" if f.yoy_growth_pct >= 0 else ""
        hover_texts.append(
            f.category_label
            + "<br>Value: $" + str(round(f.total_value_usd / 1e9, 2)) + "B"
            + "<br>YoY: " + yoy_sign + str(round(f.yoy_growth_pct, 1)) + "%"
            + "<br>Signal: " + f.demand_signal
        )

    fig = go.Figure(
        go.Bar(
            x=labels,
            y=values_b,
            marker_color=bar_colors,
            text=[str(round(v, 1)) + "B" for v in values_b],
            textposition="outside",
            textfont=dict(color=_C_TEXT, size=11),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=hover_texts,
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        height=380,
        margin=dict(t=30, b=40, l=60, r=24),
        xaxis=dict(color=_C_TEXT2, gridcolor="rgba(255,255,255,0.0)"),
        yaxis=dict(title="Estimated Trade Value (USD Billion)", color=_C_TEXT2, gridcolor="rgba(255,255,255,0.05)"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Values are benchmark estimates when live Comtrade data is unavailable. Colours indicate demand signal.")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(trade_data: dict, wb_data: dict, route_results: list) -> None:
    """Render the Cargo Analysis tab.

    Parameters
    ----------
    trade_data:
        Mapping of port_locode -> DataFrame (may be empty).
    wb_data:
        World Bank supplemental data (may be None or empty).
    route_results:
        List of RouteOpportunity objects from the route optimizer.
    """
    st.markdown(
        '<h2 style="font-size:1.4rem;font-weight:800;color:' + _C_TEXT
        + ';margin-bottom:4px">Cargo & Product Category Analysis</h2>'
        + '<p style="font-size:0.82rem;color:' + _C_TEXT2
        + ';margin-bottom:0">Deep dive into HS-code categories, seasonal patterns, '
        + "and route-level cargo composition.</p>",
        unsafe_allow_html=True,
    )

    # Compute analysis (cached implicitly via Streamlit session or caller)
    flows = analyze_cargo_flows(trade_data, wb_data)

    _render_category_grid(flows)
    _render_sankey(flows)
    _render_seasonal_calendar()
    _render_route_cargo_mix(trade_data, route_results)
    _render_value_trend(trade_data, flows)


# ---------------------------------------------------------------------------
# Integration note (for app.py maintainer)
# ---------------------------------------------------------------------------
# To wire this tab into app.py, add the following inside the tab block:
#
#   from ui import tab_cargo
#   with tab_cargo_ui:  # or whichever st.tab() variable is assigned
#       tab_cargo.render(trade_data, wb_data, route_results)
#
# The render() signature matches the other tab modules (trade_data, wb_data,
# route_results). No changes to app.py logic are required beyond this import
# and the tab_cargo.render() call.
