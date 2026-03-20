"""Bunker Fuel tab — real-time fuel prices, voyage cost calculator, and IMO compliance.

Bunker fuel represents 40-60% of voyage operating cost for container ships.
This tab gives traders and operators a complete picture of:
  - Current global bunker prices (map + dashboard)
  - Voyage fuel cost calculator per route
  - WTI vs freight rate correlation
  - IMO 2020/2023/2030/2050 regulatory compliance status

Wire-up (add to app.py tabs list):
    import ui.tab_bunker as tab_bunker
    with tab_bunker_tab:
        tab_bunker.render(freight_data, macro_data, route_results)
"""
from __future__ import annotations

import datetime
from typing import Any

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import streamlit as st
from loguru import logger

from processing.bunker_tracker import (
    BUNKER_HUB_PRICES,
    HUB_META,
    BunkerCostAnalysis,
    BunkerPrice,
    compute_voyage_fuel_cost,
    fetch_live_bunker_prices,
    get_optimal_bunkering_port,
    global_average_price,
    price_history_synthetic,
)
from routes.route_registry import ROUTES
from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    _hex_to_rgba,
    apply_dark_layout,
    section_header,
)


# ── Module-level colour aliases ────────────────────────────────────────────────
_GREEN = C_HIGH    # "#10b981"
_RED   = C_LOW     # "#ef4444"
_AMBER = C_MOD     # "#f59e0b"
_BLUE  = C_ACCENT  # "#3b82f6"
_PURPLE = "#8b5cf6"
_CYAN   = "#06b6d4"

_FUEL_COLORS: dict[str, str] = {
    "VLSFO": _BLUE,
    "HFO":   _AMBER,
    "MDO":   _PURPLE,
    "LNG":   _GREEN,
}

_FUEL_LABELS: dict[str, str] = {
    "VLSFO": "Very Low Sulphur Fuel Oil",
    "HFO":   "Heavy Fuel Oil (scrubber)",
    "MDO":   "Marine Diesel Oil",
    "LNG":   "Liquefied Natural Gas",
}

# ── IMO regulatory timeline ───────────────────────────────────────────────────
_IMO_MILESTONES: list[dict[str, Any]] = [
    {
        "year": "IMO 2020",
        "label": "VLSFO / Scrubbers Mandate",
        "status": "IMPLEMENTED",
        "color": _GREEN,
        "detail": "Global 0.5% sulphur cap — fleet compliance ~97%",
        "compliance_pct": 97,
    },
    {
        "year": "IMO 2023",
        "label": "Carbon Intensity Indicator (CII)",
        "status": "ACTIVE",
        "color": _BLUE,
        "detail": "Annual CII ratings A–E required for vessels >5,000 GT. ~38% of fleet rated C or below.",
        "compliance_pct": 62,
    },
    {
        "year": "IMO 2030",
        "label": "40% Carbon Reduction Target",
        "status": "UPCOMING",
        "color": _AMBER,
        "detail": "40% reduction in CO2 intensity vs 2008 baseline. Current trajectory: ~28% achieved.",
        "compliance_pct": 28,
    },
    {
        "year": "IMO 2050",
        "label": "Net Zero Ambition",
        "status": "FUTURE",
        "color": _RED,
        "detail": "Near-zero GHG emissions — requires LNG/methanol/ammonia fleet transition. <5% LNG vessels today.",
        "compliance_pct": 5,
    },
]


# ── Helper: delta colour and arrow ────────────────────────────────────────────

def _delta_html(pct: float, label: str = "") -> str:
    """Return an HTML snippet showing a signed percentage with colour and arrow."""
    arrow = "▲" if pct >= 0 else "▼"
    color = _RED if pct >= 0 else _GREEN   # higher fuel price = bad = red
    sign = "+" if pct >= 0 else ""
    text = label + " " if label else ""
    return (
        '<span style="font-size:0.78rem; color:' + color + ';">'
        + arrow + " " + text + sign + str(round(pct, 1)) + "%"
        + "</span>"
    )


def _card_html(
    label: str,
    value: str,
    sub: str = "",
    accent: str = _BLUE,
    delta_html: str = "",
) -> str:
    """Return a dark KPI card HTML string (no f-string backslashes)."""
    sub_block = (
        '<div style="color:' + C_TEXT3 + '; font-size:0.74rem; margin-top:4px;">'
        + sub + "</div>"
    ) if sub else ""
    delta_block = (
        '<div style="margin-top:6px;">' + delta_html + "</div>"
    ) if delta_html else ""
    border_top = "border-top:3px solid " + accent + ";"
    return (
        '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + '; '
        + 'border-radius:12px; padding:18px 20px; height:100%; ' + border_top + '">'
        + '<div style="font-size:0.72rem; font-weight:700; color:' + C_TEXT3 + '; '
        + 'text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">'
        + label + "</div>"
        + '<div style="font-size:1.85rem; font-weight:700; color:' + C_TEXT + '; line-height:1.1;">'
        + value + "</div>"
        + sub_block
        + delta_block
        + "</div>"
    )


# ── Section 1: Global Bunker Price Map ────────────────────────────────────────

def _render_price_map(
    bunker_prices: dict[tuple[str, str], BunkerPrice],
) -> None:
    section_header(
        "Global Bunker Price Map",
        "VLSFO spot price at major bunkering hubs — marker size proportional to price",
    )

    lats, lons, names, vlsfo_prices, texts = [], [], [], [], []

    for locode, meta in HUB_META.items():
        bp_vlsfo = bunker_prices.get((locode, "VLSFO"))
        if bp_vlsfo is None:
            continue
        price = bp_vlsfo.price_per_mt
        lats.append(meta["lat"])
        lons.append(meta["lon"])
        names.append(meta["name"])
        vlsfo_prices.append(price)

        # Build hover text for all fuel types without backslashes in f-string
        parts = ["<b>" + meta["name"] + "</b> (" + locode + ")<br>"]
        for ft in ("VLSFO", "HFO", "MDO", "LNG"):
            bp = bunker_prices.get((locode, ft))
            if bp:
                chg = ("+" if bp.change_7d_pct >= 0 else "") + str(bp.change_7d_pct) + "% 7d"
                parts.append(ft + ": $" + str(int(bp.price_per_mt)) + "/mt  " + chg + "<br>")
        texts.append("".join(parts) + "<extra></extra>")

    # Normalise marker sizes
    min_p = min(vlsfo_prices) if vlsfo_prices else 600
    max_p = max(vlsfo_prices) if vlsfo_prices else 650
    spread = max(max_p - min_p, 1.0)
    marker_sizes = [18 + 22 * ((p - min_p) / spread) for p in vlsfo_prices]

    fig = go.Figure()
    fig.add_trace(
        go.Scattergeo(
            lat=lats,
            lon=lons,
            text=names,
            hovertemplate=texts,
            mode="markers+text",
            textposition="top center",
            textfont=dict(color=C_TEXT2, size=10),
            marker=dict(
                size=marker_sizes,
                color=vlsfo_prices,
                colorscale=[
                    [0.0, "#10b981"],    # cheapest = green
                    [0.5, "#f59e0b"],    # mid = amber
                    [1.0, "#ef4444"],    # expensive = red
                ],
                showscale=True,
                colorbar=dict(
                    title="VLSFO $/mt",
                    thickness=12,
                    len=0.6,
                    bgcolor="rgba(10,15,26,0.8)",
                    tickfont=dict(color=C_TEXT2, size=10),
                    titlefont=dict(color=C_TEXT2, size=11),
                ),
                line=dict(color="rgba(255,255,255,0.3)", width=1),
                opacity=0.88,
            ),
        )
    )

    fig.update_layout(
        height=450,
        paper_bgcolor="#0a0f1a",
        margin=dict(l=0, r=0, t=0, b=0),
        geo=dict(
            projection_type="natural earth",
            bgcolor="#0a0f1a",
            showland=True,
            landcolor="#111827",
            showocean=True,
            oceancolor="#080e1a",
            showcountries=True,
            countrycolor="rgba(255,255,255,0.06)",
            showcoastlines=True,
            coastlinecolor="rgba(255,255,255,0.08)",
            showframe=False,
            showlakes=False,
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="bunker_price_map")


# ── Section 2: Fuel Price Dashboard ──────────────────────────────────────────

def _render_fuel_dashboard(
    bunker_prices: dict[tuple[str, str], BunkerPrice],
) -> None:
    section_header(
        "Fuel Price Dashboard",
        "Global average spot prices — 7d and 30d change — WTI-correlated history",
    )

    fuel_types = ["VLSFO", "HFO", "MDO", "LNG"]
    cols = st.columns(4)

    for col, ft in zip(cols, fuel_types):
        avg = global_average_price(ft, bunker_prices)
        # Average 7d / 30d change across hubs
        changes_7d = [
            bp.change_7d_pct
            for (_, ftype), bp in bunker_prices.items()
            if ftype == ft
        ]
        changes_30d = [
            bp.change_30d_pct
            for (_, ftype), bp in bunker_prices.items()
            if ftype == ft
        ]
        avg_7d = sum(changes_7d) / len(changes_7d) if changes_7d else 0.0
        avg_30d = sum(changes_30d) / len(changes_30d) if changes_30d else 0.0

        if avg == 0.0:
            with col:
                st.markdown(
                    _card_html(ft, "N/A", sub="No hubs with " + ft, accent=_FUEL_COLORS[ft]),
                    unsafe_allow_html=True,
                )
            continue

        value_str = "$" + str(int(avg)) + "/mt"
        sub_str = _FUEL_LABELS[ft]
        d7_html = _delta_html(avg_7d, "7d")
        d30_html = _delta_html(avg_30d, "30d")
        delta_combo = d7_html + "&nbsp;&nbsp;" + d30_html

        with col:
            st.markdown(
                _card_html(
                    label=ft,
                    value=value_str,
                    sub=sub_str,
                    accent=_FUEL_COLORS[ft],
                    delta_html=delta_combo,
                ),
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # Price history mini-charts (2 per row)
    st.markdown(
        '<div style="font-size:0.85rem; font-weight:600; color:' + C_TEXT2
        + '; margin-bottom:12px;">52-Week Price History (synthetic WTI-correlated)</div>',
        unsafe_allow_html=True,
    )

    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=["VLSFO", "HFO", "MDO", "LNG"],
        vertical_spacing=0.15,
        horizontal_spacing=0.08,
    )

    positions = [(1, 1), (1, 2), (2, 1), (2, 2)]
    for (row, col_idx), ft in zip(positions, fuel_types):
        history = price_history_synthetic(ft, weeks=52)
        dates = [h[0].isoformat() for h in history]
        prices = [h[1] for h in history]
        color = _FUEL_COLORS[ft]

        fig.add_trace(
            go.Scatter(
                x=dates,
                y=prices,
                mode="lines",
                name=ft,
                line=dict(color=color, width=1.8),
                fill="tozeroy",
                fillcolor=_hex_to_rgba(color, 0.07),
                hovertemplate="<b>" + ft + "</b><br>%{x}<br>$%{y:.0f}/mt<extra></extra>",
            ),
            row=row,
            col=col_idx,
        )

    fig.update_layout(
        height=380,
        paper_bgcolor="#0a0f1a",
        plot_bgcolor="#111827",
        font=dict(color=C_TEXT2, size=11),
        showlegend=False,
        margin=dict(l=20, r=20, t=40, b=20),
        hoverlabel=dict(bgcolor="#1a2235", bordercolor="rgba(255,255,255,0.15)", font=dict(color=C_TEXT)),
    )
    for ax in fig.layout:
        if ax.startswith("xaxis") or ax.startswith("yaxis"):
            fig.layout[ax].update(
                gridcolor="rgba(255,255,255,0.05)",
                tickfont=dict(color=C_TEXT3, size=10),
                linecolor="rgba(255,255,255,0.07)",
            )
    fig.update_annotations(font=dict(color=C_TEXT2, size=12))

    st.plotly_chart(fig, use_container_width=True, key="bunker_price_history")


# ── Section 3: Voyage Cost Calculator ────────────────────────────────────────

def _render_cost_calculator(
    bunker_prices: dict[tuple[str, str], BunkerPrice],
) -> None:
    section_header(
        "Voyage Cost Calculator",
        "Estimate total bunker spend and breakeven freight rate for any route",
    )

    route_options = {r.name: r.id for r in ROUTES}
    col_a, col_b, col_c = st.columns([2, 1, 1])

    with col_a:
        selected_route_name = st.selectbox(
            "Route",
            options=list(route_options.keys()),
            index=0,
            key="bunker_calc_route",
        )

    with col_b:
        fuel_type = st.radio(
            "Fuel type",
            options=["VLSFO", "HFO", "MDO"],
            index=0,
            key="bunker_calc_fuel",
        )

    with col_c:
        feu_count = st.number_input(
            "FEU count",
            min_value=1,
            max_value=4000,
            value=100,
            step=50,
            key="bunker_calc_feu",
        )

    route_id = route_options[selected_route_name]
    analysis = compute_voyage_fuel_cost(route_id, fuel_type, bunker_prices)

    # Scale fuel cost to user's FEU count
    total_feu_capacity = (8000 * 0.85) / 2.0  # ~3400 FEU
    feu_share = min(feu_count / total_feu_capacity, 1.0)
    user_fuel_cost = analysis.optimal_fuel_cost * feu_share
    user_cost_per_feu = analysis.cost_per_feu   # per-FEU doesn't scale with count

    # % of typical freight rate
    typical_freight_total = feu_count * 2_400.0   # $2,400/FEU avg
    fuel_pct_of_rate = (user_fuel_cost / typical_freight_total * 100.0
                        if typical_freight_total > 0 else 0.0)

    # ── Result metric cards ────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)

    with m1:
        st.markdown(
            _card_html(
                "Total Fuel Cost",
                "$" + "{:,.0f}".format(user_fuel_cost),
                sub="for " + str(feu_count) + " FEU / " + analysis.optimal_fuel_type,
                accent=_FUEL_COLORS.get(analysis.optimal_fuel_type, _BLUE),
            ),
            unsafe_allow_html=True,
        )

    with m2:
        st.markdown(
            _card_html(
                "Cost per FEU",
                "$" + "{:,.0f}".format(user_cost_per_feu),
                sub="fuel only",
                accent=_CYAN,
            ),
            unsafe_allow_html=True,
        )

    with m3:
        st.markdown(
            _card_html(
                "% of Freight Rate",
                str(round(fuel_pct_of_rate, 1)) + "%",
                sub="vs $2,400/FEU avg rate",
                accent=_AMBER if fuel_pct_of_rate > 50 else _BLUE,
            ),
            unsafe_allow_html=True,
        )

    with m4:
        st.markdown(
            _card_html(
                "Breakeven Rate",
                "$" + "{:,.0f}".format(analysis.breakeven_rate) + "/FEU",
                sub="fuel at 50% of voyage cost",
                accent=_PURPLE,
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Comparison table: VLSFO vs HFO vs LNG ─────────────────────────────────
    st.markdown(
        '<div style="font-size:0.85rem; font-weight:600; color:' + C_TEXT2
        + '; margin-bottom:10px;">Fuel Comparison — ' + selected_route_name + '</div>',
        unsafe_allow_html=True,
    )

    rows = []
    for ft in ("VLSFO", "HFO", "LNG"):
        a = compute_voyage_fuel_cost(route_id, ft, bunker_prices)
        if ft == "LNG" and not a.lng_available:
            rows.append({
                "Fuel": ft + " (LNG)",
                "Voyage Consumption (MT)": round(a.fuel_consumption_mt, 0),
                "Total Voyage Cost ($)": "N/A — no hub",
                "Cost per FEU ($)": "N/A",
                "Optimal Port": get_optimal_bunkering_port(route_id, ft, bunker_prices),
                "vs VLSFO": "—",
            })
            continue
        voyage_cost = a.vlsfo_cost if ft == "VLSFO" else (a.hfo_cost if ft == "HFO" else a.lng_cost)
        vlsfo_ref = compute_voyage_fuel_cost(route_id, "VLSFO", bunker_prices).vlsfo_cost
        vs_vlsfo = ((voyage_cost - vlsfo_ref) / vlsfo_ref * 100.0
                    if vlsfo_ref > 0 and ft != "VLSFO" else 0.0)
        vs_str = ("—" if ft == "VLSFO"
                  else (("+" if vs_vlsfo >= 0 else "") + str(round(vs_vlsfo, 1)) + "%"))
        rows.append({
            "Fuel": ft,
            "Voyage Consumption (MT)": round(a.fuel_consumption_mt, 0),
            "Total Voyage Cost ($)": "{:,.0f}".format(voyage_cost),
            "Cost per FEU ($)": "{:,.0f}".format(voyage_cost / ((8000 * 0.85) / 2.0)),
            "Optimal Port": HUB_META.get(a.bunkering_port, {}).get("name", a.bunkering_port),
            "vs VLSFO": vs_str,
        })

    df_comp = pd.DataFrame(rows)
    st.dataframe(df_comp, use_container_width=True, hide_index=True)

    # Route details callout
    opt_port_name = HUB_META.get(analysis.bunkering_port, {}).get("name", analysis.bunkering_port)
    st.markdown(
        '<div style="background:' + _hex_to_rgba(_BLUE, 0.07) + '; border:1px solid '
        + _hex_to_rgba(_BLUE, 0.2) + '; border-radius:10px; padding:12px 16px; '
        + 'font-size:0.83rem; color:' + C_TEXT2 + '; margin-top:8px;">'
        + "Optimal bunkering port for <b style='color:" + C_TEXT + ";'>"
        + selected_route_name + "</b>: "
        + "<b style='color:" + _BLUE + ";'>" + opt_port_name + "</b>"
        + " &nbsp;|&nbsp; Distance: <b>" + "{:,.0f}".format(analysis.voyage_distance_nm) + " nm</b>"
        + " &nbsp;|&nbsp; Transit: <b>" + str(int(analysis.transit_days)) + " days</b>"
        + " &nbsp;|&nbsp; Consumption: <b>" + str(int(analysis.fuel_consumption_mt)) + " MT</b>"
        + "</div>",
        unsafe_allow_html=True,
    )


# ── Section 4: Fuel vs Rate Correlation ──────────────────────────────────────

def _render_correlation(macro_data: dict, freight_data: dict) -> None:
    section_header(
        "Fuel vs Freight Rate Correlation",
        "WTI crude oil price vs container freight rates — regression and correlation",
    )

    # Pull WTI from macro_data
    wti_df = macro_data.get("WTISPLC") or macro_data.get("WTI") or pd.DataFrame()
    if isinstance(wti_df, pd.DataFrame) and not wti_df.empty and "value" in wti_df.columns:
        wti_series = wti_df.set_index("date")["value"].dropna() if "date" in wti_df.columns else wti_df["value"].dropna()
    else:
        wti_series = pd.Series(dtype=float)

    # Pull freight rates from freight_data (use transpacific_eb as proxy if available)
    freight_series = pd.Series(dtype=float)
    for key in ("transpacific_eb", "FBX01", "fbx01"):
        df = freight_data.get(key, pd.DataFrame())
        if isinstance(df, pd.DataFrame) and not df.empty:
            col = "rate" if "rate" in df.columns else ("value" if "value" in df.columns else None)
            if col:
                idx_col = "date" if "date" in df.columns else None
                if idx_col:
                    freight_series = df.set_index(idx_col)[col].dropna()
                else:
                    freight_series = df[col].dropna()
                break

    # Build synthetic data if live data is insufficient
    import random as _rand
    rng = _rand.Random(42)

    if len(wti_series) < 10 or len(freight_series) < 10:
        logger.debug("Bunker correlation: using synthetic WTI and freight data")
        n = 52
        wti_vals = [70.0 + rng.gauss(0, 8) for _ in range(n)]
        freight_vals = [2000.0 + w * 22 + rng.gauss(0, 300) for w in wti_vals]
        wti_arr = wti_vals
        freight_arr = freight_vals
        data_label = "Synthetic (52-week simulated)"
    else:
        # Align on common dates
        try:
            common = wti_series.index.intersection(freight_series.index)
            if len(common) < 5:
                raise ValueError("insufficient overlap")
            wti_arr = [float(wti_series[d]) for d in common]
            freight_arr = [float(freight_series[d]) for d in common]
            data_label = "Live (FRED WTI + freight index)"
        except Exception:
            n = 52
            wti_vals = [70.0 + rng.gauss(0, 8) for _ in range(n)]
            freight_vals = [2000.0 + w * 22 + rng.gauss(0, 300) for w in wti_vals]
            wti_arr = wti_vals
            freight_arr = freight_vals
            data_label = "Synthetic fallback"

    # Pearson correlation
    n_pts = len(wti_arr)
    mean_w = sum(wti_arr) / n_pts
    mean_f = sum(freight_arr) / n_pts
    cov = sum((w - mean_w) * (f - mean_f) for w, f in zip(wti_arr, freight_arr)) / n_pts
    std_w = (sum((w - mean_w) ** 2 for w in wti_arr) / n_pts) ** 0.5
    std_f = (sum((f - mean_f) ** 2 for f in freight_arr) / n_pts) ** 0.5
    corr = cov / (std_w * std_f) if (std_w > 0 and std_f > 0) else 0.0

    # Simple OLS regression line
    slope = cov / (std_w ** 2) if std_w > 0 else 0.0
    intercept = mean_f - slope * mean_w
    x_min, x_max = min(wti_arr), max(wti_arr)
    reg_x = [x_min, x_max]
    reg_y = [slope * x + intercept for x in reg_x]

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=wti_arr,
            y=freight_arr,
            mode="markers",
            name="Weekly observations",
            marker=dict(
                color=_BLUE,
                size=7,
                opacity=0.65,
                line=dict(color="rgba(255,255,255,0.2)", width=0.5),
            ),
            hovertemplate="WTI: $%{x:.1f}/bbl<br>Freight: $%{y:,.0f}/FEU<extra></extra>",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=reg_x,
            y=reg_y,
            mode="lines",
            name="Regression line",
            line=dict(color=_GREEN, width=2.5, dash="dash"),
            hoverinfo="skip",
        )
    )

    apply_dark_layout(
        fig,
        title="WTI Crude vs Trans-Pacific Freight Rate — correlation",
        height=380,
    )
    fig.update_layout(
        xaxis_title="WTI Crude Oil ($/bbl)",
        yaxis_title="Freight Rate ($/FEU)",
    )

    st.plotly_chart(fig, use_container_width=True, key="bunker_wti_correlation")

    corr_pct = round(abs(corr) * 100, 1)
    direction = "positively" if corr >= 0 else "negatively"
    corr_color = _GREEN if corr_pct >= 50 else (_AMBER if corr_pct >= 30 else C_TEXT2)
    st.markdown(
        '<div style="background:' + _hex_to_rgba(_BLUE, 0.06) + '; border:1px solid '
        + _hex_to_rgba(_BLUE, 0.18) + '; border-radius:10px; padding:12px 16px; '
        + 'font-size:0.84rem; color:' + C_TEXT2 + '; margin-top:-8px;">'
        + "Fuel costs are <b style='color:" + corr_color + ";'>"
        + str(corr_pct) + "% " + direction + " correlated</b> with freight rates"
        + " (Pearson r = " + str(round(corr, 3)) + "). "
        + "Source: " + data_label + "."
        + "</div>",
        unsafe_allow_html=True,
    )


# ── Section 5: IMO Compliance Panel ──────────────────────────────────────────

def _render_imo_compliance() -> None:
    section_header(
        "IMO 2020 / 2030 Compliance Panel",
        "Regulatory timeline and current fleet compliance estimates",
    )

    cols = st.columns(4)
    for col, milestone in zip(cols, _IMO_MILESTONES):
        pct = milestone["compliance_pct"]
        color = milestone["color"]
        status = milestone["status"]
        bg = _hex_to_rgba(color, 0.08)
        border = _hex_to_rgba(color, 0.22)

        # Status badge
        badge_bg = _hex_to_rgba(color, 0.15)
        badge_border = _hex_to_rgba(color, 0.3)
        badge_html = (
            '<span style="display:inline-block; padding:2px 9px; border-radius:999px; '
            + "font-size:0.68rem; font-weight:700; letter-spacing:0.06em; "
            + "background:" + badge_bg + "; color:" + color + "; "
            + "border:1px solid " + badge_border + ";"
            + '">' + status + "</span>"
        )

        # Compliance progress bar
        bar_color = _GREEN if pct >= 80 else (_AMBER if pct >= 40 else _RED)
        bar_html = (
            '<div style="background:rgba(255,255,255,0.07); border-radius:4px; '
            + 'height:6px; width:100%; margin-top:8px;">'
            + '<div style="width:' + str(pct) + '%; height:6px; border-radius:4px; '
            + "background:" + bar_color + ';"></div></div>'
        )

        with col:
            st.markdown(
                '<div style="background:' + bg + '; border:1px solid ' + border + '; '
                + 'border-radius:12px; padding:18px 16px; height:100%;">'
                + '<div style="font-size:1.05rem; font-weight:800; color:' + color
                + '; margin-bottom:4px;">' + milestone["year"] + "</div>"
                + '<div style="font-size:0.82rem; font-weight:600; color:' + C_TEXT
                + '; margin-bottom:8px;">' + milestone["label"] + "</div>"
                + badge_html
                + bar_html
                + '<div style="font-size:0.75rem; color:' + C_TEXT3
                + '; margin-top:6px;">' + str(pct) + "% fleet compliant</div>"
                + '<div style="font-size:0.78rem; color:' + C_TEXT2
                + '; margin-top:10px; line-height:1.45;">' + milestone["detail"] + "</div>"
                + "</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # Fleet transition insight
    st.markdown(
        '<div style="background:' + _hex_to_rgba(_AMBER, 0.06) + '; border:1px solid '
        + _hex_to_rgba(_AMBER, 0.2) + '; border-radius:10px; padding:14px 18px; '
        + 'font-size:0.83rem; color:' + C_TEXT2 + ';">'
        + "<b style='color:" + C_TEXT + ";'>Fleet transition outlook:</b> "
        + "Reaching IMO 2050 net-zero requires ~$1-1.5 trillion in new vessel orders and "
        + "fuel infrastructure. Today only ~5% of the global container fleet is LNG-capable. "
        + "Methanol and ammonia dual-fuel vessels are the next frontier, with 80+ on order "
        + "as of 2026. The $150-250/mt price premium for green fuels vs VLSFO represents "
        + "the core commercial hurdle."
        + "</div>",
        unsafe_allow_html=True,
    )


# ── Main render entry point ───────────────────────────────────────────────────

def render(
    freight_data: dict | None = None,
    macro_data: dict | None = None,
    route_results: list | None = None,
) -> None:
    """Render the full Bunker Fuel tab.

    Parameters
    ----------
    freight_data:
        Dict of route_id -> DataFrame with freight rate history.
    macro_data:
        Dict of FRED series_id -> DataFrame (must include "WTISPLC" for WTI).
    route_results:
        Pre-computed RouteEmissions list (unused here but accepted for
        consistency with other tab signatures).
    """
    if freight_data is None:
        freight_data = {}
    if macro_data is None:
        macro_data = {}

    logger.info("Rendering bunker tab")

    # Use hardcoded prices (live fetch attempted lazily via sidebar cache button)
    bunker_prices: dict[tuple[str, str], BunkerPrice] = BUNKER_HUB_PRICES

    # ── Hero ──────────────────────────────────────────────────────────────────
    avg_vlsfo = global_average_price("VLSFO", bunker_prices)
    avg_hfo = global_average_price("HFO", bunker_prices)
    n_hubs = len(HUB_META)

    st.markdown(
        '<div style="background:linear-gradient(135deg,rgba(59,130,246,0.10),'
        'rgba(26,34,53,0.95)); border:1px solid rgba(59,130,246,0.22); '
        'border-radius:14px; padding:26px 30px; margin-bottom:24px; '
        'box-shadow:0 0 40px rgba(59,130,246,0.06);">'
        + '<div style="display:flex; align-items:center; gap:10px; margin-bottom:4px;">'
        + '<span style="font-size:0.72rem; font-weight:700; letter-spacing:0.10em; '
        + "color:" + _BLUE + '; text-transform:uppercase;">Bunker Fuel Intelligence</span>'
        + "</div>"
        + '<div style="font-size:2.4rem; font-weight:800; color:' + C_TEXT
        + '; line-height:1.1; margin-bottom:4px;">'
        + "VLSFO $" + str(int(avg_vlsfo)) + " / mt"
        + "</div>"
        + '<div style="color:' + C_TEXT2 + '; font-size:0.88rem;">'
        + "Global average &nbsp;|&nbsp; HFO $" + str(int(avg_hfo)) + "/mt"
        + " &nbsp;|&nbsp; <b style='color:" + _BLUE + ";'>"
        + str(n_hubs) + " hubs tracked</b>"
        + " &nbsp;|&nbsp; 40-60% of voyage operating cost"
        + "</div>"
        + '<div style="margin-top:10px; font-size:0.8rem; color:' + C_TEXT3 + ';">'
        + "IMO 2020 VLSFO cap in force &mdash; CII ratings active since 2023 &mdash; "
        + "40% CO2 reduction target by 2030"
        + "</div>"
        + "</div>",
        unsafe_allow_html=True,
    )

    # ── Sections ──────────────────────────────────────────────────────────────
    _render_price_map(bunker_prices)

    st.markdown("<br>", unsafe_allow_html=True)
    _render_fuel_dashboard(bunker_prices)

    st.markdown("<br>", unsafe_allow_html=True)
    _render_cost_calculator(bunker_prices)

    st.markdown("<br>", unsafe_allow_html=True)
    _render_correlation(macro_data, freight_data)

    st.markdown("<br>", unsafe_allow_html=True)
    _render_imo_compliance()

    logger.info("Bunker tab render complete")
