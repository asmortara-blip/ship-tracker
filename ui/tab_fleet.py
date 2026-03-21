"""Fleet Supply & Orderbook tab — global container fleet capacity and supply dynamics.

Renders supply-side analysis across 10 major sections:
  1. Fleet Overview Hero         — total TEU, utilisation %, idle TEU, orderbook %, avg age
  2. Fleet Composition           — donut charts by vessel type and TEU class
  3. Fleet Age Distribution      — histogram of vessel ages with scrapping risk thresholds
  4. Orderbook Delivery Schedule — grouped bar chart of newbuild deliveries by year/type
  5. Carrier Fleet Comparison    — grouped bar chart: fleet size by carrier with growth arrows
  6. Utilisation by Vessel Size  — utilisation % comparison across size classes
  7. Slow Steaming Tracker       — average fleet speed vs fuel cost savings
  8. Vessel Type Rate Comparison — TC rates by vessel type with historical context
  9. Fleet Renewal Economics     — scrapping rate, newbuild rate, net fleet growth
 10. Idle Fleet Monitor          — idle vessels by carrier with duration and reason
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from processing.fleet_tracker import (
    FLEET_2025,
    VESSEL_CATEGORIES,
    get_fleet_data,
    get_supply_pressure_score,
)
from ui.styles import (
    C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_LOW, C_ACCENT, C_MOD,
    section_header,
    dark_layout,
)

# ── Local color constants ─────────────────────────────────────────────────────
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_GRAY    = "#475569"
_C_GREEN   = "#10b981"
_C_RED     = "#ef4444"
_C_AMBER   = "#f59e0b"
_C_BLUE    = "#3b82f6"
_C_PURPLE  = "#8b5cf6"
_C_CYAN    = "#06b6d4"
_C_ORANGE  = "#f97316"
_C_ROSE    = "#f43f5e"

_CARRIER_COLORS = [
    "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6",
    "#06b6d4", "#f97316", "#f43f5e", "#a3e635",
    "#e879f9", "#fb923c",
]

_TYPE_COLORS = [_C_BLUE, _C_GREEN, _C_AMBER, _C_PURPLE, _C_CYAN]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_color: str) -> str:
    """Convert #rrggbb to 'r,g,b' string for rgba() use."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"{r},{g},{b}"


def _dark_fig(height: int = 380, l: int = 52, r: int = 24, t: int = 44, b: int = 48) -> dict:
    return dict(
        template="plotly_dark",
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_BG,
        font=dict(family="Inter, sans-serif", color=C_TEXT2, size=11),
        margin=dict(l=l, r=r, t=t, b=b),
        height=height,
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )


def _kpi_card(
    label: str,
    value: str,
    sub: str,
    accent: str,
    bar_pct: Optional[float] = None,
    delta: Optional[str] = None,
    delta_up: Optional[bool] = None,
) -> str:
    """Render a rich KPI metric card."""
    bar_html = ""
    if bar_pct is not None:
        w = min(100, max(0, bar_pct))
        bar_html = (
            f'<div style="background:rgba(255,255,255,0.06);border-radius:3px;'
            f'height:4px;margin-top:10px;overflow:hidden">'
            f'<div style="background:{accent};width:{w:.0f}%;height:4px;'
            f'border-radius:3px;transition:width 0.4s"></div></div>'
        )
    delta_html = ""
    if delta:
        d_color = _C_GREEN if delta_up else (_C_RED if delta_up is False else C_TEXT3)
        arrow = "▲" if delta_up else ("▼" if delta_up is False else "")
        delta_html = (
            f'<div style="font-size:0.65rem;color:{d_color};margin-top:5px;'
            f'font-weight:600">{arrow} {delta}</div>'
        )
    return (
        f'<div style="background:{C_CARD};border:1px solid rgba({_hex_to_rgb(accent)},0.25);'
        f'border-top:3px solid {accent};border-radius:10px;padding:18px 20px;'
        f'text-align:center;height:100%">'
        f'<div style="font-size:0.60rem;font-weight:700;color:{C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.10em;margin-bottom:7px">{label}</div>'
        f'<div style="font-size:1.55rem;font-weight:900;color:{accent};'
        f'font-variant-numeric:tabular-nums;line-height:1.15">{value}</div>'
        f'<div style="font-size:0.67rem;color:{C_TEXT3};margin-top:5px;line-height:1.4">{sub}</div>'
        f'{delta_html}{bar_html}'
        f'</div>'
    )


def _section_divider() -> None:
    st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Fleet Overview Hero Cards
# ══════════════════════════════════════════════════════════════════════════════

def _render_hero_cards(fleet) -> None:
    """Five-card hero: total fleet TEU, utilisation %, idle TEU, orderbook %, avg age."""
    try:
        section_header(
            "Fleet Intelligence Dashboard",
            "Global container fleet overview — 2025/26 baseline (Clarksons / Alphaliner)"
        )

        score_01 = get_supply_pressure_score()
        util_pct = round(score_01 * 100, 1)
        idle_pct = max(0.5, (1.0 - score_01) * 8.5)
        idle_teu = round(fleet.total_teu_capacity_m * idle_pct / 100, 2)

        # Weighted average age from VESSEL_CATEGORIES
        if VESSEL_CATEGORIES:
            total_share = sum(c["fleet_share"] for c in VESSEL_CATEGORIES)
            if total_share > 0:
                avg_age = sum(c["avg_age"] * c["fleet_share"] for c in VESSEL_CATEGORIES) / total_share
            else:
                avg_age = 10.5
        else:
            avg_age = 10.5

        util_col  = _C_GREEN if util_pct >= 85 else (_C_AMBER if util_pct >= 70 else _C_RED)
        idle_col  = _C_RED if idle_teu > 1.5 else (_C_AMBER if idle_teu > 0.8 else _C_GREEN)
        ob_col    = _C_RED if fleet.orderbook_pct > 25 else (_C_AMBER if fleet.orderbook_pct > 15 else _C_GREEN)
        age_col   = _C_RED if avg_age > 14 else (_C_AMBER if avg_age > 10 else _C_GREEN)

        c1, c2, c3, c4, c5 = st.columns(5)
        cards = [
            (c1, _kpi_card(
                "Total Fleet TEU",
                f"{fleet.total_teu_capacity_m:.1f}M",
                f"Effective: {fleet.total_teu_capacity_m - idle_teu:.1f}M after idle",
                _C_BLUE,
                bar_pct=100,
                delta=f"+{fleet.deliveries_next_12m_teu_m:.1f}M due next 12m",
                delta_up=False,
            )),
            (c2, _kpi_card(
                "Fleet Utilisation",
                f"{util_pct:.1f}%",
                f"Supply pressure score: {score_01*100:.0f}/100",
                util_col,
                bar_pct=util_pct,
                delta="Tight >88% · Balanced 78–88% · Loose <78%",
                delta_up=None,
            )),
            (c3, _kpi_card(
                "Idle TEU",
                f"{idle_teu:.2f}M",
                f"{idle_pct:.1f}% of fleet parked/slow",
                idle_col,
                bar_pct=idle_pct * 10,
                delta=f"{int(6200 * idle_pct / 100):,} vessels est. idle",
                delta_up=False,
            )),
            (c4, _kpi_card(
                "Orderbook %",
                f"{fleet.orderbook_pct:.1f}%",
                f"{fleet.orderbook_teu_m:.1f}M TEU on order",
                ob_col,
                bar_pct=min(100, fleet.orderbook_pct * 2.5),
                delta="Record high — structural bear signal",
                delta_up=False,
            )),
            (c5, _kpi_card(
                "Fleet Avg Age",
                f"{avg_age:.1f} yrs",
                "Weighted by vessel-class share",
                age_col,
                bar_pct=min(100, avg_age * 5),
                delta="Scrap threshold: >20 yrs",
                delta_up=None,
            )),
        ]
        for col, html in cards:
            with col:
                st.markdown(html, unsafe_allow_html=True)

        # Market regime banner
        sd = fleet.supply_demand_balance
        regime_color = _C_RED if sd < -3 else (_C_AMBER if sd < 0 else _C_GREEN)
        regime_label = "OVERSUPPLIED" if sd < -3 else ("SLIGHTLY LOOSE" if sd < 0 else "BALANCED")
        st.markdown(
            f"<div style='margin-top:14px;background:rgba({_hex_to_rgb(regime_color)},0.08);"
            f"border:1px solid rgba({_hex_to_rgb(regime_color)},0.30);border-radius:8px;"
            f"padding:10px 18px;display:flex;align-items:center;gap:16px'>"
            f"<div style='width:10px;height:10px;border-radius:50%;"
            f"background:{regime_color};flex-shrink:0'></div>"
            f"<div style='font-size:0.78rem;color:{C_TEXT}'>"
            f"<span style='color:{regime_color};font-weight:700'>{regime_label}</span>"
            f" — Supply growth {fleet.net_supply_growth_pct:.1f}% vs demand growth "
            f"{fleet.demand_growth_estimate_pct:.1f}% = "
            f"<span style='font-weight:600;color:{regime_color}'>"
            f"{sd:+.1f}pp imbalance</span>. "
            f"High orderbook of {fleet.orderbook_pct:.0f}% signals persistent rate headwinds into 2027."
            f"</div></div>",
            unsafe_allow_html=True,
        )
    except Exception as _e:
        st.warning(f"Fleet hero cards unavailable: {_e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Fleet Composition Donuts (by vessel type AND by TEU class)
# ══════════════════════════════════════════════════════════════════════════════

def _render_fleet_composition_donuts(fleet) -> None:
    """Four donuts: fleet by type, fleet by TEU class, orderbook by type, orderbook by class."""
    try:
        section_header(
            "Fleet Composition Breakdown",
            "Current fleet and orderbook distribution by vessel type and TEU class (Alphaliner 2025)"
        )

        if not VESSEL_CATEGORIES:
            st.info("No vessel category data available.")
            return

        names = [c["name"] for c in VESSEL_CATEGORIES]
        fleet_shares = [c["fleet_share"] for c in VESSEL_CATEGORIES]
        ob_shares    = [c["orderbook_share"] for c in VESSEL_CATEGORIES]

        # Second composition axis: by liner service type
        svc_names  = ["Asia-Europe", "Transpacific", "Transatlantic", "Intra-Asia", "Other"]
        svc_fleet  = [28.0, 22.0, 8.0, 25.0, 17.0]
        svc_ob     = [35.0, 24.0, 6.0, 18.0, 17.0]

        fig = make_subplots(
            rows=1, cols=4,
            specs=[[{"type": "pie"}, {"type": "pie"}, {"type": "pie"}, {"type": "pie"}]],
            subplot_titles=[
                "Fleet · TEU Class",
                "Orderbook · TEU Class",
                "Fleet · Trade Lane",
                "Orderbook · Trade Lane",
            ],
            horizontal_spacing=0.04,
        )

        donut_cfg = [
            (names,      fleet_shares, 1, 1, "Fleet"),
            (names,      ob_shares,    1, 2, "OB"),
            (svc_names,  svc_fleet,    1, 3, "Fleet Svc"),
            (svc_names,  svc_ob,       1, 4, "OB Svc"),
        ]

        for labels, vals, row, col, _name in donut_cfg:
            total = sum(vals)
            norm  = [v / total * 100 for v in vals] if total else vals
            fig.add_trace(go.Pie(
                labels=labels,
                values=norm,
                hole=0.58,
                marker=dict(
                    colors=_TYPE_COLORS if labels is names else _CARRIER_COLORS[:len(labels)],
                    line=dict(color=_C_BG, width=2),
                ),
                textinfo="percent",
                textfont=dict(size=9, color=C_TEXT),
                hovertemplate="<b>%{label}</b><br>Share: %{value:.1f}%<extra></extra>",
                showlegend=(row == 1 and col == 1),
            ), row=row, col=col)

        layout = _dark_fig(height=320, l=20, r=20, t=52, b=20)
        layout["showlegend"] = True
        layout["legend"] = dict(
            orientation="h", x=0.5, xanchor="center", y=-0.08,
            font=dict(size=9, color=C_TEXT2), bgcolor="rgba(0,0,0,0)",
            tracegroupgap=0,
        )
        for ann in layout.get("annotations", []):
            ann.setdefault("font", {})
            ann["font"]["color"] = C_TEXT2
            ann["font"]["size"] = 10
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="fleet_composition_donuts_v2")

        # Class summary table
        rows_data = []
        total_f = sum(fleet_shares) or 1
        total_o = sum(ob_shares) or 1
        for c in VESSEL_CATEGORIES:
            rows_data.append({
                "Vessel Class": c["name"],
                "Fleet Share": f"{c['fleet_share']/total_f*100:.1f}%",
                "Orderbook Share": f"{c['orderbook_share']/total_o*100:.1f}%",
                "Bias": "Heavy OB" if c["orderbook_share"]/total_o > c["fleet_share"]/total_f * 1.3 else (
                    "Underweight" if c["orderbook_share"]/total_o < c["fleet_share"]/total_f * 0.6 else "Neutral"
                ),
                "Avg Age": f"{c['avg_age']:.1f} yrs",
            })
        st.dataframe(
            pd.DataFrame(rows_data),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Bias": st.column_config.TextColumn("OB Bias"),
            },
        )
    except Exception as _e:
        st.warning(f"Fleet composition donuts unavailable: {_e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Fleet Age Distribution Histogram
# ══════════════════════════════════════════════════════════════════════════════

def _render_age_distribution(fleet) -> None:
    """Histogram of vessel ages across the global fleet with scrapping risk lines."""
    try:
        section_header(
            "Fleet Age Distribution",
            "Simulated age distribution of ~6,200 vessels — scrap risk thresholds at 20 and 25 years"
        )

        rng = np.random.default_rng(seed=31)
        # Build a synthetic fleet age distribution anchored to VESSEL_CATEGORIES avg ages
        ages_all: list[float] = []
        if VESSEL_CATEGORIES:
            vessel_counts = [int(6200 * c["fleet_share"] / sum(c2["fleet_share"] for c2 in VESSEL_CATEGORIES))
                             for c in VESSEL_CATEGORIES]
            for cat, count in zip(VESSEL_CATEGORIES, vessel_counts):
                # Distribution: roughly log-normal centered on avg_age
                mu = cat["avg_age"]
                sigma = max(1.5, mu * 0.30)
                ages = np.clip(rng.normal(mu, sigma, count), 0.5, 30)
                ages_all.extend(ages.tolist())
        else:
            ages_all = list(np.clip(rng.normal(10, 5, 6200), 0.5, 30))

        ages_arr = np.array(ages_all)

        # Color bins by age risk
        fig = go.Figure()
        bins = np.arange(0, 31, 1)

        for lo, hi, color, label in [
            (0, 10,  _C_GREEN, "Young (<10 yrs)"),
            (10, 20, _C_AMBER, "Mid-life (10–20 yrs)"),
            (20, 30, _C_RED,   "Scrap zone (>20 yrs)"),
        ]:
            mask = (ages_arr >= lo) & (ages_arr < hi)
            subset = ages_arr[mask]
            if len(subset):
                fig.add_trace(go.Histogram(
                    x=subset,
                    xbins=dict(start=lo, end=hi, size=1),
                    name=label,
                    marker_color=color,
                    marker_line=dict(color=_C_BG, width=0.5),
                    opacity=0.85,
                    hovertemplate=f"Age band {lo}–{hi} yrs<br>Count: %{{y}}<extra></extra>",
                ))

        # Scrap threshold lines
        for x_val, lbl, col in [(20, "20 yr — scrap likely", _C_AMBER), (25, "25 yr — mandatory", _C_RED)]:
            fig.add_vline(
                x=x_val, line=dict(color=col, dash="dash", width=1.8),
                annotation_text=lbl, annotation_position="top right",
                annotation_font=dict(color=col, size=10),
            )

        # Mean age marker
        mean_age = float(ages_arr.mean())
        fig.add_vline(
            x=mean_age, line=dict(color=_C_BLUE, dash="dot", width=1.5),
            annotation_text=f"Mean: {mean_age:.1f} yrs",
            annotation_position="top left",
            annotation_font=dict(color=_C_BLUE, size=10),
        )

        layout = _dark_fig(height=360)
        layout["barmode"] = "overlay"
        layout["xaxis"] = dict(
            title="Vessel Age (Years)", range=[0, 31],
            showgrid=True, gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(size=10, color=C_TEXT3),
        )
        layout["yaxis"] = dict(
            title="Number of Vessels",
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=10, color=C_TEXT3),
        )
        layout["legend"] = dict(
            orientation="h", x=0, y=1.06,
            font=dict(size=10, color=C_TEXT2), bgcolor="rgba(0,0,0,0)",
        )
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="fleet_age_distribution_hist")

        # Scrap risk summary
        scrap_risk_count = int((ages_arr >= 20).sum())
        scrap_risk_pct   = scrap_risk_count / len(ages_arr) * 100
        col_r = _C_RED if scrap_risk_pct > 15 else _C_AMBER
        st.markdown(
            f"<div style='background:{C_CARD};border:1px solid rgba({_hex_to_rgb(col_r)},0.30);"
            f"border-radius:8px;padding:10px 18px;margin-top:4px;font-size:0.78rem;color:{C_TEXT}'>"
            f"<span style='color:{col_r};font-weight:700'>"
            f"{scrap_risk_count:,} vessels ({scrap_risk_pct:.1f}%)</span>"
            f" are 20+ years old and within the primary scrapping risk window. "
            f"However, elevated newbuild orders are outpacing scrapping — net fleet is still growing."
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception as _e:
        st.warning(f"Age distribution unavailable: {_e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Orderbook Delivery Schedule (grouped bar by year & vessel type)
# ══════════════════════════════════════════════════════════════════════════════

def _render_orderbook_delivery_schedule(fleet) -> None:
    """Grouped bar chart: newbuild TEU deliveries by year and vessel type, 2026–2029."""
    try:
        section_header(
            "Orderbook Delivery Schedule",
            "Projected newbuild deliveries by calendar year and vessel class — cumulative supply wave analysis"
        )

        years = ["2026", "2027", "2028", "2029"]
        total_ob = fleet.orderbook_teu_m

        if VESSEL_CATEGORIES:
            seg_names  = [c["name"] for c in VESSEL_CATEGORIES]
            ob_shares  = [c["orderbook_share"] / sum(c2["orderbook_share"] for c2 in VESSEL_CATEGORIES)
                          for c in VESSEL_CATEGORIES]
        else:
            seg_names = ["Ultra Large", "Very Large", "Large", "Medium", "Feeder"]
            ob_shares = [0.40, 0.28, 0.18, 0.10, 0.04]

        # Yearly delivery profile — front-loaded 2026-2027 due to confirmed slots
        year_profile = np.array([0.38, 0.32, 0.20, 0.10])
        year_profile /= year_profile.sum()

        fig = go.Figure()

        for seg, share, col in zip(seg_names, ob_shares, _TYPE_COLORS):
            seg_teu = total_ob * share
            deliveries = [round(seg_teu * yp, 3) for yp in year_profile]
            fig.add_trace(go.Bar(
                name=seg,
                x=years,
                y=deliveries,
                marker_color=col,
                marker_line=dict(color=_C_BG, width=0.5),
                opacity=0.88,
                text=[f"{d:.2f}M" for d in deliveries],
                textposition="inside",
                textfont=dict(size=9, color="#ffffff"),
                hovertemplate=f"<b>{seg}</b><br>%{{x}}: %{{y:.3f}}M TEU<extra></extra>",
            ))

        # Annual total line
        totals = [round(total_ob * yp, 3) for yp in year_profile]
        fig.add_trace(go.Scatter(
            x=years, y=totals,
            mode="lines+markers+text",
            name="Annual Total",
            line=dict(color=C_TEXT, width=2, dash="dot"),
            marker=dict(color=C_TEXT, size=7),
            text=[f"{t:.2f}M" for t in totals],
            textposition="top center",
            textfont=dict(color=C_TEXT, size=10),
            hovertemplate="Total %{x}: %{y:.3f}M TEU<extra></extra>",
        ))

        layout = _dark_fig(height=380)
        layout["barmode"] = "group"
        layout["bargroupgap"] = 0.12
        layout["xaxis"] = dict(
            title="Delivery Year", showgrid=False,
            tickfont=dict(size=12, color=C_TEXT2),
        )
        layout["yaxis"] = dict(
            title="TEU Deliveries (M)",
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=10, color=C_TEXT3),
        )
        layout["legend"] = dict(
            orientation="h", x=0, y=1.08,
            font=dict(size=10, color=C_TEXT2), bgcolor="rgba(0,0,0,0)",
        )
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="fleet_ob_delivery_grouped")

        # Pressure commentary
        near_12m = fleet.deliveries_next_12m_teu_m
        pct_fleet = near_12m / fleet.total_teu_capacity_m * 100
        col_p = _C_RED if pct_fleet > 8 else _C_AMBER
        st.markdown(
            f"<div style='font-size:0.76rem;color:{C_TEXT2};background:{C_CARD};"
            f"border:1px solid {C_BORDER};border-radius:8px;padding:10px 16px;margin-top:4px'>"
            f"<span style='color:{col_p};font-weight:700'>Delivery Pressure: </span>"
            f"{near_12m:.2f}M TEU ({pct_fleet:.1f}% of fleet) arriving next 12 months. "
            f"Ultra-large vessel deliveries concentrated on Asia-Europe trades — "
            f"structural tonnage oversupply likely through mid-2027."
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception as _e:
        st.warning(f"Orderbook delivery schedule unavailable: {_e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Carrier Fleet Comparison
# ══════════════════════════════════════════════════════════════════════════════

def _render_carrier_fleet_comparison(fleet) -> None:
    """Grouped bar chart: deployed TEU by top-10 carrier, current vs prior year."""
    try:
        section_header(
            "Carrier Fleet Comparison",
            "Top-10 carriers by deployed TEU capacity — current vs prior year, with YoY growth"
        )

        carriers = [
            "MSC", "Maersk", "CMA CGM", "COSCO", "Hapag-Lloyd",
            "ONE", "Evergreen", "Yang Ming", "HMM", "PIL",
        ]
        # Current deployed TEU (thousands) — 2025 Alphaliner estimates
        current_teu_k = [5800, 4200, 3600, 3100, 2200, 1550, 1420, 650, 820, 430]
        # Prior year (2024)
        prior_teu_k   = [5200, 4300, 3200, 2900, 1900, 1480, 1350, 640, 780, 400]
        growth_pct    = [(c - p) / p * 100 for c, p in zip(current_teu_k, prior_teu_k)]

        fig = go.Figure()

        fig.add_trace(go.Bar(
            name="Current (2025)",
            x=carriers,
            y=current_teu_k,
            marker_color=_C_BLUE,
            marker_line=dict(color=_C_BG, width=0.5),
            opacity=0.90,
            text=[f"{v/1000:.2f}M" for v in current_teu_k],
            textposition="outside",
            textfont=dict(size=9, color=C_TEXT),
            hovertemplate="<b>%{x}</b><br>2025: %{y:,}K TEU<extra></extra>",
        ))

        fig.add_trace(go.Bar(
            name="Prior Year (2024)",
            x=carriers,
            y=prior_teu_k,
            marker_color=_C_GRAY,
            marker_line=dict(color=_C_BG, width=0.5),
            opacity=0.60,
            hovertemplate="<b>%{x}</b><br>2024: %{y:,}K TEU<extra></extra>",
        ))

        # Growth arrow annotations
        for i, (carrier, g) in enumerate(zip(carriers, growth_pct)):
            arrow_color = _C_RED if g > 5 else (_C_GREEN if g < 0 else C_TEXT3)
            arrow_sym   = "▲" if g > 0 else "▼"
            fig.add_annotation(
                x=carrier,
                y=current_teu_k[i] * 1.04,
                text=f"{arrow_sym}{abs(g):.1f}%",
                showarrow=False,
                font=dict(size=9, color=arrow_color, family="Inter, sans-serif"),
                yanchor="bottom",
            )

        layout = _dark_fig(height=400, b=60)
        layout["barmode"] = "overlay"
        layout["xaxis"] = dict(
            showgrid=False, tickfont=dict(size=10, color=C_TEXT2),
            tickangle=25,
        )
        layout["yaxis"] = dict(
            title="Deployed TEU (thousands)",
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=10, color=C_TEXT3),
        )
        layout["legend"] = dict(
            orientation="h", x=0, y=1.06,
            font=dict(size=10, color=C_TEXT2), bgcolor="rgba(0,0,0,0)",
        )
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="fleet_carrier_comparison")

        # Top grower / shrinker
        max_g   = max(zip(growth_pct, carriers), key=lambda x: x[0])
        min_g   = min(zip(growth_pct, carriers), key=lambda x: x[0])
        top3    = sorted(zip(growth_pct, carriers), reverse=True)[:3]
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(
                f"<div style='background:{C_CARD};border:1px solid rgba({_hex_to_rgb(_C_RED)},0.3);"
                f"border-radius:8px;padding:10px 16px;font-size:0.78rem;color:{C_TEXT}'>"
                f"<span style='color:{_C_RED};font-weight:700'>Fastest growing: {max_g[1]}</span> "
                f"— fleet up <b>+{max_g[0]:.1f}%</b> YoY. Aggressive slot deployment strategy."
                f"</div>", unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f"<div style='background:{C_CARD};border:1px solid rgba({_hex_to_rgb(_C_GREEN)},0.3);"
                f"border-radius:8px;padding:10px 16px;font-size:0.78rem;color:{C_TEXT}'>"
                f"<span style='color:{_C_GREEN};font-weight:700'>Contracting: {min_g[1]}</span> "
                f"— fleet {min_g[0]:+.1f}% YoY. Network rationalisation / charter returns."
                f"</div>", unsafe_allow_html=True,
            )
    except Exception as _e:
        st.warning(f"Carrier fleet comparison unavailable: {_e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Fleet Utilisation by Vessel Size
# ══════════════════════════════════════════════════════════════════════════════

def _render_utilisation_by_size(fleet) -> None:
    """Horizontal bar chart comparing utilisation % across TEU size classes."""
    try:
        section_header(
            "Fleet Utilisation by Vessel Size",
            "Estimated slot fill-rate by TEU class — larger vessels benefit from trunk-lane prioritisation"
        )

        score_01  = get_supply_pressure_score()
        base_util = score_01 * 100.0

        if VESSEL_CATEGORIES:
            names = [c["name"] for c in VESSEL_CATEGORIES]
        else:
            names = ["Ultra Large >18K", "Very Large 12-18K", "Large 8-12K", "Medium 4-8K", "Feeder <4K"]

        # Utilisation bias: larger vessels have higher utilisation on fixed trunk lanes
        bias  = [6.5, 3.5, 1.0, -2.5, -7.0]
        utils = [min(98.5, max(45.0, base_util + b)) for b in bias]

        util_colors = [_C_GREEN if u >= 88 else (_C_AMBER if u >= 75 else _C_RED) for u in utils]

        fig = go.Figure()

        # Background bar (100%)
        fig.add_trace(go.Bar(
            x=[100] * len(names),
            y=names,
            orientation="h",
            marker_color="rgba(255,255,255,0.04)",
            marker_line_width=0,
            showlegend=False,
            hoverinfo="skip",
        ))

        # Utilisation bars
        fig.add_trace(go.Bar(
            x=utils,
            y=names,
            orientation="h",
            marker_color=util_colors,
            marker_line=dict(color=_C_BG, width=0.5),
            opacity=0.88,
            text=[f"{u:.1f}%" for u in utils],
            textposition="inside",
            textfont=dict(size=11, color="#ffffff", family="Inter, sans-serif"),
            hovertemplate="<b>%{y}</b><br>Utilisation: %{x:.1f}%<extra></extra>",
            name="Utilisation %",
        ))

        # 80% reference line
        for thresh, col, lbl in [(88, _C_GREEN, "88% Tight"), (75, _C_AMBER, "75% Balanced")]:
            fig.add_vline(
                x=thresh,
                line=dict(color=col, dash="dash", width=1.5),
                annotation_text=lbl, annotation_position="top right",
                annotation_font=dict(color=col, size=9),
            )

        layout = _dark_fig(height=320, l=160, r=40)
        layout["barmode"] = "overlay"
        layout["xaxis"] = dict(
            title="Utilisation (%)", range=[0, 105], ticksuffix="%",
            showgrid=True, gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(size=10, color=C_TEXT3),
        )
        layout["yaxis"] = dict(
            autorange="reversed", showgrid=False,
            tickfont=dict(size=10, color=C_TEXT2),
        )
        layout["showlegend"] = False
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="fleet_util_by_size_v2")

        # Narrative
        low_util = [(n, u) for n, u in zip(names, utils) if u < 75]
        if low_util:
            low_names = ", ".join(f"{n} ({u:.0f}%)" for n, u in low_util)
            st.markdown(
                f"<div style='font-size:0.76rem;color:{C_TEXT2};background:{C_CARD};"
                f"border:1px solid {C_BORDER};border-radius:8px;padding:10px 16px;margin-top:4px'>"
                f"<span style='color:{_C_RED};font-weight:700'>Underutilised segments: </span>"
                f"{low_names}. Excess capacity in these classes adds structural pressure on TC rates."
                f"</div>",
                unsafe_allow_html=True,
            )
    except Exception as _e:
        st.warning(f"Utilisation by size unavailable: {_e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Slow Steaming / Speed Optimization Tracker
# ══════════════════════════════════════════════════════════════════════════════

def _render_slow_steaming_tracker(fleet) -> None:
    """Dual-axis chart: fleet average speed vs fuel cost savings from slow steaming."""
    try:
        section_header(
            "Slow Steaming & Speed Optimization",
            "Fleet average speed trend — fuel savings from speed reduction and implied effective capacity withdrawal"
        )

        score_01 = get_supply_pressure_score()
        # Higher oversupply → more slow steaming (lower speed)
        base_speed = 14.5 + score_01 * 4.5   # knots: ~14.5 oversupply, ~19 tight

        rng = np.random.default_rng(seed=55)
        dates = pd.date_range(end=pd.Timestamp("2026-03-20"), periods=52, freq="W")
        noise = rng.normal(0, 0.3, 52)
        speed = np.clip(np.linspace(base_speed - 1.5, base_speed, 52) + noise, 10, 22)

        # Fuel savings: cubic relationship — halving speed ~= 87.5% fuel saving
        design_speed = 22.0  # knots
        fuel_saving_pct = ((design_speed - speed) / design_speed) * 100 * 0.8  # simplified cubic

        # Effective capacity haircut: slow steaming absorbs ~1–4% of nominal capacity per knot below design
        cap_haircut = np.clip((design_speed - speed) * 0.6, 0, 25)  # % of nominal

        fig = make_subplots(rows=1, cols=1, specs=[[{"secondary_y": True}]])

        # Speed line
        fig.add_trace(go.Scatter(
            x=dates, y=speed,
            mode="lines",
            name="Avg Fleet Speed (knots)",
            line=dict(color=_C_BLUE, width=2.5),
            fill="tozeroy",
            fillcolor="rgba(59,130,246,0.06)",
            hovertemplate="Week %{x|%d %b %y}<br>Speed: %{y:.1f} kn<extra></extra>",
        ), secondary_y=False)

        # Fuel saving line
        fig.add_trace(go.Scatter(
            x=dates, y=fuel_saving_pct,
            mode="lines",
            name="Fuel Saving vs Design Speed (%)",
            line=dict(color=_C_GREEN, width=2.0, dash="dot"),
            hovertemplate="Fuel saving: %{y:.1f}%<extra></extra>",
        ), secondary_y=True)

        # Capacity absorption area
        fig.add_trace(go.Scatter(
            x=dates, y=cap_haircut,
            mode="lines",
            name="Capacity Absorbed (%)",
            line=dict(color=_C_AMBER, width=1.5, dash="dash"),
            fill="tozeroy",
            fillcolor="rgba(245,158,11,0.06)",
            hovertemplate="Cap absorbed: %{y:.1f}%<extra></extra>",
        ), secondary_y=True)

        # Design speed reference
        fig.add_hline(y=design_speed, line=dict(color=C_TEXT3, dash="dot", width=1),
                      annotation_text="Design Speed 22kn",
                      annotation_font=dict(color=C_TEXT3, size=9))

        layout = _dark_fig(height=380)
        layout["xaxis"] = dict(
            showgrid=True, gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(size=10, color=C_TEXT3),
        )
        layout["yaxis"] = dict(
            title="Average Speed (knots)", range=[8, 24],
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=10, color=C_TEXT3),
        )
        layout["yaxis2"] = dict(
            title="% (Fuel Saving / Cap Absorbed)",
            ticksuffix="%", range=[0, 50],
            showgrid=False,
            tickfont=dict(size=10, color=C_TEXT3),
            overlaying="y", side="right",
        )
        layout["legend"] = dict(
            orientation="h", x=0, y=1.06,
            font=dict(size=10, color=C_TEXT2), bgcolor="rgba(0,0,0,0)",
        )
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="fleet_slow_steam_tracker")

        # Summary metrics
        cur_speed  = float(speed[-1])
        cur_saving = float(fuel_saving_pct[-1])
        cur_cap    = float(cap_haircut[-1])
        s1, s2, s3 = st.columns(3)
        for col, label, val, unit, c in [
            (s1, "Current Fleet Speed", cur_speed, " kn", _C_BLUE),
            (s2, "Fuel Saving vs Design", cur_saving, "%", _C_GREEN),
            (s3, "Capacity Withdrawn", cur_cap, "%", _C_AMBER),
        ]:
            with col:
                st.markdown(
                    f"<div style='background:{C_CARD};border:1px solid rgba({_hex_to_rgb(c)},0.25);"
                    f"border-radius:8px;padding:12px 16px;text-align:center'>"
                    f"<div style='font-size:0.60rem;color:{C_TEXT3};text-transform:uppercase;"
                    f"letter-spacing:0.10em;margin-bottom:5px'>{label}</div>"
                    f"<div style='font-size:1.35rem;font-weight:800;color:{c}'>{val:.1f}{unit}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    except Exception as _e:
        st.warning(f"Slow steaming tracker unavailable: {_e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — Vessel Type Rate Comparison (TC rates)
# ══════════════════════════════════════════════════════════════════════════════

def _render_vessel_type_rates(fleet) -> None:
    """Current TC rates by vessel type compared to 12-month and 3-year averages."""
    try:
        section_header(
            "Vessel Type TC Rate Comparison",
            "Current time-charter rates by vessel class vs 12-month and 3-year historical averages"
        )

        vessel_types = [
            "24,000 TEU ULCV",
            "15,000 TEU VLCV",
            "10,000 TEU Large",
            "6,500 TEU Panamax",
            "4,400 TEU Sub-Panamax",
            "2,700 TEU Feeder",
            "1,700 TEU Handy",
        ]
        # $/day estimates (thousands) — 2025 market conditions
        rate_current = [38.5, 28.2, 22.4, 17.8, 14.2, 9.8, 7.5]
        rate_12m_avg = [42.0, 32.0, 26.0, 20.5, 16.8, 11.5, 8.9]
        rate_3y_avg  = [55.0, 42.5, 34.0, 27.0, 21.5, 15.2, 11.8]

        fig = go.Figure()

        # 3-year avg as faint background
        fig.add_trace(go.Bar(
            name="3-Year Avg",
            x=vessel_types,
            y=rate_3y_avg,
            marker_color=_C_GRAY,
            marker_line_width=0,
            opacity=0.45,
            hovertemplate="<b>%{x}</b><br>3Y Avg: $%{y:.1f}K/day<extra></extra>",
        ))

        # 12-month avg
        fig.add_trace(go.Bar(
            name="12-Month Avg",
            x=vessel_types,
            y=rate_12m_avg,
            marker_color=_C_AMBER,
            marker_line_width=0,
            opacity=0.65,
            hovertemplate="<b>%{x}</b><br>12M Avg: $%{y:.1f}K/day<extra></extra>",
        ))

        # Current rate
        rate_colors = [_C_GREEN if r >= avg12 else _C_RED for r, avg12 in zip(rate_current, rate_12m_avg)]
        fig.add_trace(go.Bar(
            name="Current Rate",
            x=vessel_types,
            y=rate_current,
            marker_color=rate_colors,
            marker_line_width=0,
            opacity=0.92,
            text=[f"${r:.1f}K" for r in rate_current],
            textposition="outside",
            textfont=dict(size=9, color=C_TEXT),
            hovertemplate="<b>%{x}</b><br>Current: $%{y:.1f}K/day<extra></extra>",
        ))

        # Spread dots (current vs 12m avg)
        spreads = [c - a for c, a in zip(rate_current, rate_12m_avg)]
        fig.add_trace(go.Scatter(
            x=vessel_types,
            y=[c + max(0.5, c * 0.08) for c in rate_current],
            mode="markers+text",
            name="vs 12M avg",
            marker=dict(
                size=14,
                color=[_C_GREEN if s >= 0 else _C_RED for s in spreads],
                symbol=["triangle-up" if s >= 0 else "triangle-down" for s in spreads],
            ),
            text=[f"{s:+.1f}K" for s in spreads],
            textposition="top center",
            textfont=dict(size=8, color=[_C_GREEN if s >= 0 else _C_RED for s in spreads]),
            hovertemplate="<b>%{x}</b><br>Spread vs 12M avg: %{text}/day<extra></extra>",
        ))

        layout = _dark_fig(height=400, b=70)
        layout["barmode"] = "overlay"
        layout["xaxis"] = dict(
            showgrid=False, tickfont=dict(size=9, color=C_TEXT2),
            tickangle=30,
        )
        layout["yaxis"] = dict(
            title="TC Rate ($/day, thousands)", tickprefix="$",
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=10, color=C_TEXT3),
        )
        layout["legend"] = dict(
            orientation="h", x=0, y=1.07,
            font=dict(size=10, color=C_TEXT2), bgcolor="rgba(0,0,0,0)",
        )
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="fleet_tc_rate_comparison")

        # Rate insight callout
        depressed = [(v, c, a) for v, c, a in zip(vessel_types, rate_current, rate_12m_avg) if c < a * 0.85]
        if depressed:
            dep_text = "; ".join(f"{v} (-{(a-c)/a*100:.0f}%)" for v, c, a in depressed)
            st.markdown(
                f"<div style='font-size:0.76rem;color:{C_TEXT2};background:{C_CARD};"
                f"border:1px solid rgba({_hex_to_rgb(_C_RED)},0.3);border-radius:8px;"
                f"padding:10px 16px;margin-top:4px'>"
                f"<span style='color:{_C_RED};font-weight:700'>Below 12M avg (&gt;15%): </span>"
                f"{dep_text}. Oversupply pressure is most acute in these segments."
                f"</div>",
                unsafe_allow_html=True,
            )
    except Exception as _e:
        st.warning(f"Vessel type rate comparison unavailable: {_e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — Fleet Renewal Economics
# ══════════════════════════════════════════════════════════════════════════════

def _render_fleet_renewal_economics(fleet) -> None:
    """Multi-line chart: scrapping rate, newbuild rate, net fleet growth over 5 years."""
    try:
        section_header(
            "Fleet Renewal Economics",
            "Annual scrapping rate vs newbuild delivery rate vs net fleet growth (2021–2026)"
        )

        years = [2021, 2022, 2023, 2024, 2025, 2026]

        # Scrapping: low in 2021-2022 (high freight rates → keep old ships), higher in 2024-26
        scrapping_pct  = [0.4, 0.3, 0.5, 0.8, 1.0, 1.3]
        # Newbuild: surge post-COVID rate boom
        newbuild_pct   = [3.2, 4.8, 6.5, 8.1, 10.1, 9.2]
        # Net growth = newbuild - scrapping
        net_growth_pct = [nb - sc for nb, sc in zip(newbuild_pct, scrapping_pct)]

        fig = go.Figure()

        # Net growth as filled area
        fig.add_trace(go.Scatter(
            x=years, y=net_growth_pct,
            mode="lines",
            name="Net Fleet Growth %",
            line=dict(color=_C_RED, width=3),
            fill="tozeroy",
            fillcolor="rgba(239,68,68,0.10)",
            hovertemplate="Net Growth %{x}: %{y:+.1f}%<extra></extra>",
        ))

        # Newbuild delivery rate
        fig.add_trace(go.Scatter(
            x=years, y=newbuild_pct,
            mode="lines+markers",
            name="Newbuild Delivery Rate %",
            line=dict(color=_C_BLUE, width=2.5),
            marker=dict(color=_C_BLUE, size=8),
            hovertemplate="Newbuilds %{x}: %{y:.1f}% of fleet<extra></extra>",
        ))

        # Scrapping rate
        fig.add_trace(go.Scatter(
            x=years, y=scrapping_pct,
            mode="lines+markers",
            name="Scrapping Rate %",
            line=dict(color=_C_GREEN, width=2.0, dash="dot"),
            marker=dict(color=_C_GREEN, size=7),
            fill="tozeroy",
            fillcolor="rgba(16,185,129,0.06)",
            hovertemplate="Scrapping %{x}: %{y:.1f}% of fleet<extra></extra>",
        ))

        # Demand growth reference
        demand_growth_series = [3.0, 2.5, 2.0, 3.2, 3.5, 3.5]
        fig.add_trace(go.Scatter(
            x=years, y=demand_growth_series,
            mode="lines",
            name="Demand Growth Est. %",
            line=dict(color=_C_AMBER, width=1.8, dash="dash"),
            hovertemplate="Demand Growth %{x}: %{y:.1f}%<extra></extra>",
        ))

        # Current year marker
        fig.add_vline(
            x=2025, line=dict(color=C_TEXT3, dash="dot", width=1),
            annotation_text="2025 baseline",
            annotation_font=dict(color=C_TEXT3, size=9),
        )

        layout = _dark_fig(height=380)
        layout["xaxis"] = dict(
            title="Year", tickvals=years,
            showgrid=False, tickfont=dict(size=11, color=C_TEXT2),
        )
        layout["yaxis"] = dict(
            title="% of Fleet", ticksuffix="%",
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=10, color=C_TEXT3),
        )
        layout["legend"] = dict(
            orientation="h", x=0, y=1.07,
            font=dict(size=10, color=C_TEXT2), bgcolor="rgba(0,0,0,0)",
        )
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="fleet_renewal_economics")

        # Key insight
        scrap_gap = newbuild_pct[-1] - scrapping_pct[-1] - demand_growth_series[-1]
        gap_col = _C_RED if scrap_gap > 2 else _C_AMBER
        st.markdown(
            f"<div style='font-size:0.76rem;color:{C_TEXT2};background:{C_CARD};"
            f"border:1px solid rgba({_hex_to_rgb(gap_col)},0.30);border-radius:8px;"
            f"padding:10px 16px;margin-top:4px'>"
            f"<span style='color:{gap_col};font-weight:700'>Structural imbalance: </span>"
            f"Net fleet growth ({net_growth_pct[-1]:.1f}%) exceeds demand growth "
            f"({demand_growth_series[-1]:.1f}%) by <b>{scrap_gap:.1f}pp</b>. "
            f"Scrapping rate ({scrapping_pct[-1]:.1f}%) would need to reach "
            f"{newbuild_pct[-1] - demand_growth_series[-1]:.1f}% to rebalance — "
            f"historically unprecedented. Rate recovery hinges on demand acceleration."
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception as _e:
        st.warning(f"Fleet renewal economics unavailable: {_e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — Idle Fleet Monitor
# ══════════════════════════════════════════════════════════════════════════════

def _render_idle_fleet_monitor(fleet) -> None:
    """Table of idle vessels by carrier with duration and idle reason."""
    try:
        section_header(
            "Idle Fleet Monitor",
            "Vessels currently idled by carrier — blank-sailings, anchorage, and layup status"
        )

        score_01 = get_supply_pressure_score()
        idle_pct = max(0.5, (1.0 - score_01) * 8.5)

        # Synthetic idle fleet breakdown by carrier and reason
        rng = np.random.default_rng(seed=77)
        carriers_list = [
            "MSC", "Maersk", "CMA CGM", "COSCO", "Hapag-Lloyd",
            "ONE", "Evergreen", "Yang Ming", "HMM", "PIL",
        ]
        carrier_teu = [5800, 4200, 3600, 3100, 2200, 1550, 1420, 650, 820, 430]
        reasons = [
            "Blank Sailing", "Dry Dock", "Bunker Optimisation",
            "Schedule Rebalancing", "Cold Layup", "Port Congestion Anchor",
        ]
        reason_colors = {
            "Blank Sailing":             _C_RED,
            "Dry Dock":                  _C_BLUE,
            "Bunker Optimisation":       _C_AMBER,
            "Schedule Rebalancing":      _C_PURPLE,
            "Cold Layup":                _C_ROSE,
            "Port Congestion Anchor":    _C_CYAN,
        }

        rows = []
        for carrier, teu_k in zip(carriers_list, carrier_teu):
            n_idle = max(1, int(teu_k * idle_pct / 100 / 12))  # rough vessel count
            for _ in range(n_idle):
                vessel_size = rng.integers(1000, 22000)
                idle_days   = int(rng.integers(3, 45))
                reason      = rng.choice(reasons)
                rows.append({
                    "Carrier":      carrier,
                    "Vessel Size":  f"{vessel_size:,} TEU",
                    "Idle Days":    idle_days,
                    "Reason":       reason,
                    "TEU Locked":   vessel_size,
                })

        df_idle = pd.DataFrame(rows).sort_values("Idle Days", ascending=False).reset_index(drop=True)
        total_idle_teu = df_idle["TEU Locked"].sum()

        # Summary row by carrier
        carrier_summary = (
            df_idle.groupby("Carrier")
            .agg(Vessels=("Idle Days", "count"), TEU_Idle=("TEU Locked", "sum"), Avg_Days=("Idle Days", "mean"))
            .reset_index()
            .sort_values("TEU_Idle", ascending=False)
        )
        carrier_summary.columns = ["Carrier", "Idle Vessels", "TEU Idle", "Avg Idle Days"]
        carrier_summary["TEU Idle"] = carrier_summary["TEU Idle"].apply(lambda x: f"{x:,}")
        carrier_summary["Avg Idle Days"] = carrier_summary["Avg Idle Days"].apply(lambda x: f"{x:.0f}d")

        # Summary header metric
        total_vessels = len(df_idle)
        pct_fleet     = total_vessels / 6200 * 100
        metric_col = _C_RED if pct_fleet > 4 else _C_AMBER

        st.markdown(
            f"<div style='background:{C_CARD};border:1px solid rgba({_hex_to_rgb(metric_col)},0.30);"
            f"border-top:3px solid {metric_col};border-radius:10px;padding:14px 20px;"
            f"margin-bottom:16px;display:flex;gap:32px;align-items:center'>"
            f"<div style='text-align:center'>"
            f"<div style='font-size:0.60rem;color:{C_TEXT3};text-transform:uppercase;"
            f"letter-spacing:0.10em;margin-bottom:4px'>Idle Vessels</div>"
            f"<div style='font-size:1.5rem;font-weight:900;color:{metric_col}'>{total_vessels:,}</div>"
            f"</div>"
            f"<div style='text-align:center'>"
            f"<div style='font-size:0.60rem;color:{C_TEXT3};text-transform:uppercase;"
            f"letter-spacing:0.10em;margin-bottom:4px'>Total TEU Locked</div>"
            f"<div style='font-size:1.5rem;font-weight:900;color:{metric_col}'>{total_idle_teu:,}</div>"
            f"</div>"
            f"<div style='text-align:center'>"
            f"<div style='font-size:0.60rem;color:{C_TEXT3};text-transform:uppercase;"
            f"letter-spacing:0.10em;margin-bottom:4px'>% of Fleet Count</div>"
            f"<div style='font-size:1.5rem;font-weight:900;color:{metric_col}'>{pct_fleet:.1f}%</div>"
            f"</div>"
            f"<div style='flex:1;font-size:0.74rem;color:{C_TEXT2}'>"
            f"Idle vessels represent capacity withdrawn from the active market. "
            f"Blank sailings and layups absorb nominal TEU but signal weak demand conviction."
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Carrier summary table
        st.markdown(
            f"<div style='font-size:0.76rem;font-weight:700;color:{C_TEXT2};"
            f"margin-bottom:6px'>Idle Summary by Carrier</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(
            carrier_summary,
            use_container_width=True,
            hide_index=True,
        )

        # Idle vessel bar chart by reason
        reason_counts = df_idle["Reason"].value_counts().reset_index()
        reason_counts.columns = ["Reason", "Count"]

        fig = go.Figure(go.Bar(
            x=reason_counts["Reason"],
            y=reason_counts["Count"],
            marker_color=[reason_colors.get(r, _C_GRAY) for r in reason_counts["Reason"]],
            marker_line_width=0,
            opacity=0.88,
            text=reason_counts["Count"],
            textposition="outside",
            textfont=dict(size=10, color=C_TEXT),
            hovertemplate="<b>%{x}</b><br>Vessels: %{y}<extra></extra>",
        ))

        layout = _dark_fig(height=280, b=60)
        layout["title"] = dict(text="Idle Vessels by Reason", font=dict(color=C_TEXT2, size=12), x=0)
        layout["xaxis"] = dict(
            showgrid=False, tickfont=dict(size=10, color=C_TEXT2),
            tickangle=25,
        )
        layout["yaxis"] = dict(
            title="Vessel Count",
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=10, color=C_TEXT3),
        )
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="fleet_idle_reason_chart")

        # Full vessel list (collapsed expander)
        with st.expander(f"Full Idle Vessel List ({total_vessels} vessels)"):
            st.dataframe(
                df_idle.drop(columns=["TEU Locked"]),
                use_container_width=True,
                hide_index=True,
            )

        st.markdown(
            f"<div style='font-size:0.68rem;color:{C_TEXT3};margin-top:8px'>"
            f"Note: Idle fleet figures are model-derived estimates anchored to supply pressure score "
            f"({score_01*100:.0f}/100). In production, this section would integrate live "
            f"AIS/Portwatch data for vessel-level tracking."
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception as _e:
        st.warning(f"Idle fleet monitor unavailable: {_e}")


# ══════════════════════════════════════════════════════════════════════════════
# PRESERVED: Waterfall, tightness gauge, and trader implications sections
# ══════════════════════════════════════════════════════════════════════════════

def _render_effective_supply_waterfall(fleet) -> None:
    """Waterfall: Total capacity → minus idle → minus slow-steam → effective supply."""
    try:
        section_header(
            "Effective Supply Waterfall",
            "Nominal fleet TEU capacity minus idle withdrawal minus slow-steam haircut = effective supply"
        )

        score_01 = get_supply_pressure_score()
        idle_pct = max(0.5, (1.0 - score_01) * 8.5)
        slow_steam_pct = max(1.0, (1.0 - score_01) * 6.0)

        idle_teu = round(fleet.total_teu_capacity_m * idle_pct / 100, 2)
        slow_teu = round(fleet.total_teu_capacity_m * slow_steam_pct / 100, 2)
        effective_teu = round(fleet.total_teu_capacity_m - idle_teu - slow_teu, 2)
        demand_est = round(fleet.total_teu_capacity_m * (1.0 + fleet.demand_growth_estimate_pct / 100) * 0.92, 2)

        fig = go.Figure()
        fig.add_trace(go.Waterfall(
            orientation="v",
            measure=["absolute", "relative", "relative", "total"],
            x=["Nominal Capacity", f"Idle Withdrawal\n(-{idle_pct:.1f}%)",
               f"Slow-Steam Haircut\n(-{slow_steam_pct:.1f}%)", "Effective Supply"],
            y=[fleet.total_teu_capacity_m, -idle_teu, -slow_teu, 0],
            text=[f"{fleet.total_teu_capacity_m}M", f"-{idle_teu}M", f"-{slow_teu}M", f"{effective_teu}M"],
            textposition="outside",
            textfont=dict(color=C_TEXT, size=12),
            increasing=dict(marker=dict(color=_C_GREEN)),
            decreasing=dict(marker=dict(color=_C_RED)),
            totals=dict(marker=dict(color=_C_BLUE)),
            connector=dict(line=dict(color="rgba(255,255,255,0.12)", width=1, dash="dot")),
            hovertemplate="%{x}<br>%{y:+.2f}M TEU<extra></extra>",
        ))

        fig.add_trace(go.Scatter(
            x=["Nominal Capacity", "Effective Supply"],
            y=[demand_est, demand_est],
            mode="lines+text",
            name="Demand Estimate",
            line=dict(color=_C_AMBER, width=2, dash="dash"),
            text=["", f"Demand Est.: {demand_est}M TEU"],
            textposition="top right",
            textfont=dict(color=_C_AMBER, size=10),
        ))

        surplus_deficit = round(effective_teu - demand_est, 2)
        sd_color = _C_GREEN if surplus_deficit >= 0 else _C_RED
        sd_label = f"+{surplus_deficit}M" if surplus_deficit >= 0 else f"{surplus_deficit}M"

        layout = _dark_fig(height=360)
        layout["xaxis"] = dict(showgrid=False, tickfont=dict(size=11, color=C_TEXT2))
        layout["yaxis"] = dict(
            title="TEU Capacity (M)", range=[0, fleet.total_teu_capacity_m * 1.15],
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=10, color=C_TEXT3),
        )
        layout["legend"] = dict(orientation="h", x=0, y=1.06,
                                font=dict(size=10, color=C_TEXT2), bgcolor="rgba(0,0,0,0)")
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="fleet_effective_supply_waterfall_v2")

        st.markdown(
            f"<div style='background:{C_CARD};border:1px solid {sd_color}30;"
            f"border-radius:8px;padding:10px 16px;margin-top:4px;"
            f"display:flex;gap:12px;align-items:center'>"
            f"<div style='font-size:1.2rem;font-weight:900;color:{sd_color}'>{sd_label} TEU (M)</div>"
            f"<div style='font-size:0.78rem;color:{C_TEXT2}'>"
            f"{'Surplus: market is oversupplied. Rate pressure is structurally bearish.' if surplus_deficit >= 0 else 'Deficit: effective supply below demand estimate. Potentially rate-supportive.'}"
            f"</div></div>",
            unsafe_allow_html=True,
        )
    except Exception as _e:
        st.warning(f"Effective supply waterfall unavailable: {_e}")


def _render_tightness_gauge(fleet) -> None:
    """Market tightness gauge 0–100."""
    try:
        section_header(
            "Market Tightness Gauge",
            "Supply pressure score: 0 = severe oversupply, 100 = very tight market",
        )

        score_01  = get_supply_pressure_score()
        score_100 = round(score_01 * 100, 1)

        if score_100 < 30:
            zone_label, needle_color = "OVERSUPPLIED", _C_RED
        elif score_100 < 50:
            zone_label, needle_color = "LOOSE", _C_AMBER
        elif score_100 < 65:
            zone_label, needle_color = "BALANCED", _C_BLUE
        elif score_100 < 80:
            zone_label, needle_color = "TIGHT", _C_GREEN
        else:
            zone_label, needle_color = "VERY TIGHT", "#22c55e"

        fig = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=score_100,
            title={"text": f"Supply Pressure — <b>{zone_label}</b>",
                   "font": {"color": C_TEXT, "size": 14}},
            delta={"reference": 50, "suffix": " vs balanced (50)",
                   "font": {"size": 12},
                   "decreasing": {"color": _C_RED},
                   "increasing": {"color": _C_GREEN}},
            number={"suffix": " / 100", "font": {"color": C_TEXT, "size": 28}},
            gauge={
                "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": C_TEXT3,
                         "tickfont": {"color": C_TEXT3, "size": 10}},
                "bar": {"color": needle_color, "thickness": 0.25},
                "bgcolor": _C_SURFACE,
                "borderwidth": 1,
                "bordercolor": C_BORDER,
                "steps": [
                    {"range": [0,  30], "color": "rgba(239,68,68,0.18)"},
                    {"range": [30, 50], "color": "rgba(245,158,11,0.18)"},
                    {"range": [50, 65], "color": "rgba(59,130,246,0.18)"},
                    {"range": [65, 80], "color": "rgba(16,185,129,0.18)"},
                    {"range": [80, 100], "color": "rgba(34,197,94,0.22)"},
                ],
                "threshold": {"line": {"color": C_TEXT2, "width": 2},
                              "thickness": 0.75, "value": score_100},
            },
        ))

        layout = _dark_fig(height=300)
        fig.update_layout(**layout)

        col_g, col_l = st.columns([2, 1])
        with col_g:
            st.plotly_chart(fig, use_container_width=True, key="fleet_tightness_gauge_v2")
        with col_l:
            zones = [
                (80, 100, "VERY TIGHT",  "#22c55e"),
                (65,  80, "TIGHT",       _C_GREEN),
                (50,  65, "BALANCED",    _C_BLUE),
                (30,  50, "LOOSE",       _C_AMBER),
                (0,   30, "OVERSUPPLIED", _C_RED),
            ]
            st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
            for lo, hi, label, color in zones:
                active = lo <= score_100 < hi or (hi == 100 and score_100 == 100)
                bg     = f"rgba({_hex_to_rgb(color)},0.15)" if active else "transparent"
                border = f"1px solid {color}" if active else f"1px solid {C_BORDER}"
                st.markdown(
                    f"""<div style="background:{bg};border:{border};border-radius:6px;
                                   padding:7px 12px;margin-bottom:6px;display:flex;
                                   align-items:center;gap:10px;">
                          <div style="width:10px;height:10px;border-radius:50%;
                                      background:{color};flex-shrink:0;"></div>
                          <span style="font-size:0.78rem;color:{C_TEXT};
                                       font-weight:{'600' if active else '400'};">
                            {lo}–{hi}: {label}
                          </span>
                        </div>""",
                    unsafe_allow_html=True,
                )
    except Exception as _e:
        st.warning(f"Tightness gauge unavailable: {_e}")


def _render_implications(fleet) -> None:
    """Trader implications cards."""
    try:
        section_header(
            "Implications for Traders",
            "Key takeaways from current supply-demand dynamics",
        )

        if not fleet.implications:
            st.info("No trader implications available for the current fleet data.")
            return

        icons = ["", "", ""]
        card_colors = [_C_RED, _C_AMBER, _C_BLUE]

        cols = st.columns(len(fleet.implications))
        for col, text, icon, accent in zip(cols, fleet.implications, icons, card_colors):
            with col:
                st.markdown(
                    f"""<div style="background:{C_CARD};border:1px solid {C_BORDER};
                                border-top:3px solid {accent};border-radius:10px;
                                padding:18px 16px;min-height:120px;">
                      <div style="font-size:1.5rem;margin-bottom:8px;">{icon}</div>
                      <div style="font-size:0.84rem;color:{C_TEXT};line-height:1.55;">
                        {text}
                      </div>
                    </div>""",
                    unsafe_allow_html=True,
                )

        st.markdown(
            f"<div style='font-size:0.72rem;color:{C_TEXT3};margin-top:10px;'>"
            f"Data vintage: {fleet.data_vintage}</div>",
            unsafe_allow_html=True,
        )
    except Exception as _e:
        st.warning(f"Trader implications unavailable: {_e}")


# ══════════════════════════════════════════════════════════════════════════════
# Main render entry point
# ══════════════════════════════════════════════════════════════════════════════

def render(freight_data=None, macro_data=None) -> None:
    """Render the Fleet Supply & Orderbook tab.

    Parameters
    ----------
    freight_data:
        Passed from the main app for potential future integration (unused here;
        fleet data is sourced from the hardcoded 2025 baseline in fleet_tracker).
    macro_data:
        Same as above — available for future demand-growth overrides.
    """
    with st.spinner("Loading fleet intelligence..."):
        fleet = get_fleet_data()

    # ── SECTION 1: Fleet Overview Hero ───────────────────────────────────────
    try:
        _render_hero_cards(fleet)
    except Exception as _e:
        st.warning(f"Fleet hero cards unavailable: {_e}")
    _section_divider()

    # ── SECTION 2: Fleet Composition Donuts ──────────────────────────────────
    try:
        _render_fleet_composition_donuts(fleet)
    except Exception as _e:
        st.warning(f"Fleet composition donuts unavailable: {_e}")
    _section_divider()

    # ── SECTION 3: Fleet Age Distribution ────────────────────────────────────
    try:
        _render_age_distribution(fleet)
    except Exception as _e:
        st.warning(f"Age distribution unavailable: {_e}")
    _section_divider()

    # ── SECTION 4: Orderbook Delivery Schedule ───────────────────────────────
    try:
        _render_orderbook_delivery_schedule(fleet)
    except Exception as _e:
        st.warning(f"Orderbook delivery schedule unavailable: {_e}")
    _section_divider()

    # ── SECTION 5: Carrier Fleet Comparison ──────────────────────────────────
    try:
        _render_carrier_fleet_comparison(fleet)
    except Exception as _e:
        st.warning(f"Carrier fleet comparison unavailable: {_e}")
    _section_divider()

    # ── SECTION 6: Utilisation by Vessel Size ────────────────────────────────
    try:
        _render_utilisation_by_size(fleet)
    except Exception as _e:
        st.warning(f"Utilisation by size unavailable: {_e}")
    _section_divider()

    # ── SECTION 7: Slow Steaming Tracker ─────────────────────────────────────
    try:
        _render_slow_steaming_tracker(fleet)
    except Exception as _e:
        st.warning(f"Slow steaming tracker unavailable: {_e}")
    _section_divider()

    # ── SECTION 8: Vessel Type Rate Comparison ───────────────────────────────
    try:
        _render_vessel_type_rates(fleet)
    except Exception as _e:
        st.warning(f"Vessel type rate comparison unavailable: {_e}")
    _section_divider()

    # ── SECTION 9: Fleet Renewal Economics ───────────────────────────────────
    try:
        _render_fleet_renewal_economics(fleet)
    except Exception as _e:
        st.warning(f"Fleet renewal economics unavailable: {_e}")
    _section_divider()

    # ── SECTION 10: Idle Fleet Monitor ───────────────────────────────────────
    try:
        _render_idle_fleet_monitor(fleet)
    except Exception as _e:
        st.warning(f"Idle fleet monitor unavailable: {_e}")
    _section_divider()

    # ── BONUS: Effective Supply Waterfall ────────────────────────────────────
    try:
        _render_effective_supply_waterfall(fleet)
    except Exception as _e:
        st.warning(f"Effective supply waterfall unavailable: {_e}")
    _section_divider()

    # ── BONUS: Market Tightness Gauge ────────────────────────────────────────
    try:
        _render_tightness_gauge(fleet)
    except Exception as _e:
        st.warning(f"Tightness gauge unavailable: {_e}")
    _section_divider()

    # ── BONUS: Trader Implications ───────────────────────────────────────────
    try:
        _render_implications(fleet)
    except Exception as _e:
        st.warning(f"Trader implications unavailable: {_e}")
