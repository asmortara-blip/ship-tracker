"""tab_trade_war.py — Interactive trade war and tariff impact simulator.

Extends processing/tariff_analyzer.py with a full interactive UI covering:
  1. Global tariff heat map (choropleth)
  2. Tariff scenario builder with live impact preview
  3. Trade diversion Sankey diagram
  4. Historical tariff event timeline
  5. Supply chain reshoring tracker
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.tariff_analyzer import (
    ROUTE_TARIFF_EXPOSURE,
    analyze_tariff_sensitivity,
)

# ── Color palette ──────────────────────────────────────────────────────────────
C_BG     = "#0a0f1a"
C_CARD   = "#1a2235"
C_BORDER = "rgba(255,255,255,0.08)"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_TEXT3  = "#64748b"
C_HIGH   = "#10b981"
C_ACCENT = "#3b82f6"
C_WARN   = "#f59e0b"
C_DANGER = "#ef4444"


# ── Hardcoded 2025-2026 tariff risk data (20+ countries) ──────────────────────
# tariff_risk: 0 = low risk, 1 = high risk
_COUNTRY_TARIFF_RISK: list[dict] = [
    {"iso": "USA", "name": "United States",      "tariff_risk": 0.85, "note": "Major tariff imposer — Section 301, reciprocal tariffs"},
    {"iso": "CHN", "name": "China",              "tariff_risk": 0.90, "note": "Primary tariff target — 145% US tariffs in force"},
    {"iso": "DEU", "name": "Germany",            "tariff_risk": 0.40, "note": "EU member — moderate exposure via US-EU 10% baseline"},
    {"iso": "FRA", "name": "France",             "tariff_risk": 0.38, "note": "EU member — auto/luxury goods tariff risk"},
    {"iso": "GBR", "name": "United Kingdom",     "tariff_risk": 0.35, "note": "Post-Brexit — negotiating bilateral deal with US"},
    {"iso": "JPN", "name": "Japan",              "tariff_risk": 0.55, "note": "25% auto tariffs; ongoing bilateral negotiations"},
    {"iso": "KOR", "name": "South Korea",        "tariff_risk": 0.50, "note": "Steel/auto tariffs; KORUS under review"},
    {"iso": "VNM", "name": "Vietnam",            "tariff_risk": 0.60, "note": "Trade diversion hub — elevated US scrutiny"},
    {"iso": "MEX", "name": "Mexico",             "tariff_risk": 0.55, "note": "25% IEEPA tariffs — nearshoring destination"},
    {"iso": "CAN", "name": "Canada",             "tariff_risk": 0.50, "note": "25% tariffs outside USMCA — CUSMA review"},
    {"iso": "IND", "name": "India",              "tariff_risk": 0.30, "note": "Beneficiary of China+1 — low direct exposure"},
    {"iso": "BRA", "name": "Brazil",             "tariff_risk": 0.25, "note": "Limited direct US-China tariff impact"},
    {"iso": "AUS", "name": "Australia",          "tariff_risk": 0.20, "note": "Allied nation — minimal tariff risk"},
    {"iso": "IDN", "name": "Indonesia",          "tariff_risk": 0.35, "note": "Moderate — some transshipment exposure"},
    {"iso": "THA", "name": "Thailand",           "tariff_risk": 0.45, "note": "Regional manufacturing — moderate US scrutiny"},
    {"iso": "MYS", "name": "Malaysia",           "tariff_risk": 0.42, "note": "Semiconductor hub — elevated trade war risk"},
    {"iso": "BGD", "name": "Bangladesh",         "tariff_risk": 0.20, "note": "Apparel exporter — GSP beneficiary"},
    {"iso": "SAU", "name": "Saudi Arabia",       "tariff_risk": 0.18, "note": "Energy corridor — low manufactured goods exposure"},
    {"iso": "ARE", "name": "UAE",                "tariff_risk": 0.22, "note": "Re-export hub — some transshipment risk"},
    {"iso": "SGP", "name": "Singapore",          "tariff_risk": 0.28, "note": "Transshipment hub — indirect exposure"},
    {"iso": "ZAF", "name": "South Africa",       "tariff_risk": 0.18, "note": "Commodities focus — low manufactured goods risk"},
    {"iso": "MAR", "name": "Morocco",            "tariff_risk": 0.15, "note": "EU-adjacent nearshoring — low US tariff risk"},
    {"iso": "POL", "name": "Poland",             "tariff_risk": 0.35, "note": "EU eastern manufacturing hub — moderate exposure"},
    {"iso": "TUR", "name": "Turkey",             "tariff_risk": 0.30, "note": "Steel tariffs — moderate bilateral exposure"},
    {"iso": "RUS", "name": "Russia",             "tariff_risk": 0.10, "note": "Sanctioned — de facto excluded from major trade flows"},
]


# ── Helper: section title ──────────────────────────────────────────────────────

def _section_title(text: str, subtitle: str = "") -> None:
    sub_html = (
        '<div style="color:' + C_TEXT2 + '; font-size:0.83rem; margin-top:3px">'
        + subtitle + "</div>"
        if subtitle
        else ""
    )
    st.markdown(
        '<div style="margin-bottom:14px; margin-top:8px">'
        '<div style="font-size:1.05rem; font-weight:700; color:' + C_TEXT + '">'
        + text + "</div>"
        + sub_html
        + "</div>",
        unsafe_allow_html=True,
    )


def _card(content_html: str, border_color: str = C_BORDER) -> str:
    return (
        '<div style="background:' + C_CARD + '; border:1px solid ' + border_color + ';'
        ' border-radius:12px; padding:18px 20px; margin-bottom:10px">'
        + content_html + "</div>"
    )


def _metric_pill(label: str, value: str, color: str) -> str:
    return (
        '<div style="background:rgba(0,0,0,0.25); border:1px solid '
        + color
        + '44; border-radius:8px; padding:10px 14px; margin-bottom:8px">'
        '<div style="font-size:0.70rem; text-transform:uppercase; letter-spacing:0.08em; color:'
        + C_TEXT3 + '; margin-bottom:4px">' + label + "</div>"
        '<div style="font-size:1.15rem; font-weight:800; color:' + color + '">' + value + "</div>"
        "</div>"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Global Tariff Heat Map
# ══════════════════════════════════════════════════════════════════════════════

def _render_tariff_heatmap() -> None:
    logger.debug("Rendering tariff heat map choropleth")

    iso_codes   = [c["iso"]         for c in _COUNTRY_TARIFF_RISK]
    risk_values = [c["tariff_risk"] for c in _COUNTRY_TARIFF_RISK]
    names       = [c["name"]        for c in _COUNTRY_TARIFF_RISK]
    notes       = [c["note"]        for c in _COUNTRY_TARIFF_RISK]

    hover_text = [
        "<b>" + names[i] + "</b> (" + iso_codes[i] + ")<br>"
        "Tariff Risk: " + str(int(risk_values[i] * 100)) + "%<br>"
        "<i>" + notes[i] + "</i>"
        for i in range(len(iso_codes))
    ]

    fig = go.Figure(go.Choropleth(
        locations=iso_codes,
        z=risk_values,
        zmin=0.0,
        zmax=1.0,
        colorscale=[
            [0.00, "#10b981"],
            [0.33, "#84cc16"],
            [0.55, "#f59e0b"],
            [0.75, "#f97316"],
            [1.00, "#ef4444"],
        ],
        colorbar=dict(
            title=dict(text="Tariff Risk", font=dict(color=C_TEXT2, size=11)),
            tickfont=dict(color=C_TEXT2, size=10),
            tickformat=".0%",
            tickvals=[0, 0.25, 0.5, 0.75, 1.0],
            ticktext=["0%", "25%", "50%", "75%", "100%"],
            bgcolor="rgba(10,15,26,0.85)",
            bordercolor=C_BORDER,
            borderwidth=1,
            len=0.75,
            thickness=14,
        ),
        hovertext=hover_text,
        hoverinfo="text",
        marker_line_color="rgba(255,255,255,0.10)",
        marker_line_width=0.5,
    ))

    fig.update_layout(
        geo=dict(
            showframe=False,
            showcoastlines=True,
            coastlinecolor="rgba(255,255,255,0.12)",
            showland=True,
            landcolor="#111827",
            showocean=True,
            oceancolor="#0a0f1a",
            showlakes=False,
            showrivers=False,
            showcountries=True,
            countrycolor="rgba(255,255,255,0.10)",
            bgcolor=C_BG,
            projection_type="natural earth",
        ),
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font=dict(color=C_TEXT),
        height=400,
        margin=dict(l=0, r=0, t=10, b=0),
    )

    st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Tariff Scenario Builder
# ══════════════════════════════════════════════════════════════════════════════

def _compute_scenario_impacts(
    us_china_pct: float,
    us_eu_pct: float,
    china_retaliation_pct: float,
    pmi_impact_pp: float,
    trade_diversion: bool,
) -> dict:
    """
    Compute estimated volume and rate impacts for key shipping lanes
    based on user-supplied tariff scenario parameters.

    All inputs are as percentages (0-100 for tariffs, -10 to 0 for PMI).
    Returns a dict with computed impact strings.
    """
    # Convert to decimal fractions for elasticity math
    us_china_frac        = us_china_pct / 100.0
    us_eu_frac           = us_eu_pct / 100.0
    china_retal_frac     = china_retaliation_pct / 100.0

    # Volume elasticity: -0.8 per unit of tariff shock (from tariff_analyzer)
    _elast = -0.8
    _rate_follow = 0.6

    # Baseline tariff on transpacific EB = 14.5% — incremental shock above baseline
    baseline_tp = 0.145
    shock_tp = max(0.0, us_china_frac - baseline_tp)
    tp_eb_vol_chg = shock_tp * 0.85 * _elast  # exposure_score=0.85
    tp_eb_rate_chg = tp_eb_vol_chg * _rate_follow

    # Trade diversion bonus: if diverting, SE Asia lanes gain ~40% of lost US-China volume
    diversion_bonus_tp = 0.0
    if trade_diversion and shock_tp > 0:
        diversion_bonus_tp = shock_tp * 0.85 * 0.40  # 40% recapture via SEA

    # Asia-Europe: exposure_score=0.40, influenced by US-EU tariff + china retaliation
    baseline_ae = 0.065
    shock_ae = max(0.0, (us_eu_frac - baseline_ae) * 0.40 + china_retal_frac * 0.20)
    ae_vol_chg = shock_ae * _elast
    ae_rate_chg = ae_vol_chg * _rate_follow

    # PMI multiplier: every 1pp drop in global PMI reduces trade volumes ~0.5%
    pmi_vol_effect = pmi_impact_pp * 0.005  # negative already

    # Apply PMI to both lanes
    tp_eb_vol_chg  += pmi_vol_effect
    ae_vol_chg     += pmi_vol_effect

    # Rate shock estimate on key shipping stocks (ZIM most exposed at ~90% TP/AE)
    zim_rate_impact  = tp_eb_rate_chg * 0.65 + ae_rate_chg * 0.25
    matx_rate_impact = tp_eb_rate_chg * 0.75  # MATX = Matson, Hawaii/Pacific focus

    # Format helper
    def _fmt_pct(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return sign + str(round(v * 100, 1)) + "%"

    def _color(v: float) -> str:
        return C_HIGH if v >= 0 else C_DANGER

    return {
        "tp_eb_vol_chg":          tp_eb_vol_chg,
        "tp_eb_rate_chg":         tp_eb_rate_chg,
        "ae_vol_chg":             ae_vol_chg,
        "ae_rate_chg":            ae_rate_chg,
        "zim_rate_impact":        zim_rate_impact,
        "matx_rate_impact":       matx_rate_impact,
        "diversion_bonus_tp":     diversion_bonus_tp,
        "tp_eb_vol_str":          _fmt_pct(tp_eb_vol_chg + diversion_bonus_tp),
        "tp_eb_rate_str":         _fmt_pct(tp_eb_rate_chg),
        "ae_vol_str":             _fmt_pct(ae_vol_chg),
        "ae_rate_str":            _fmt_pct(ae_rate_chg),
        "zim_str":                _fmt_pct(zim_rate_impact),
        "matx_str":               _fmt_pct(matx_rate_impact),
        "tp_eb_vol_color":        _color(tp_eb_vol_chg + diversion_bonus_tp),
        "tp_eb_rate_color":       _color(tp_eb_rate_chg),
        "ae_vol_color":           _color(ae_vol_chg),
        "ae_rate_color":          _color(ae_rate_chg),
        "zim_color":              _color(zim_rate_impact),
        "matx_color":             _color(matx_rate_impact),
    }


def _render_scenario_builder(route_results: list) -> None:
    logger.debug("Rendering tariff scenario builder")

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown(
            '<div style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.10em;'
            ' color:' + C_TEXT3 + '; margin-bottom:12px">Scenario Controls</div>',
            unsafe_allow_html=True,
        )

        us_china_pct = st.slider(
            "US-China tariff rate %",
            min_value=0,
            max_value=100,
            value=25,
            step=1,
            help="Total US tariff rate applied to Chinese goods imports",
        )
        us_eu_pct = st.slider(
            "US-EU tariff rate %",
            min_value=0,
            max_value=50,
            value=0,
            step=1,
            help="US tariff rate applied to EU goods imports",
        )
        china_retaliation_pct = st.slider(
            "China retaliation %",
            min_value=0,
            max_value=80,
            value=15,
            step=1,
            help="Chinese retaliatory tariff rate on US exports",
        )
        pmi_impact_pp = st.slider(
            "Global PMI impact (pp)",
            min_value=-10,
            max_value=0,
            value=-2,
            step=1,
            help="Estimated drag on global PMI from trade war uncertainty (percentage points)",
        )
        trade_diversion = st.checkbox(
            "Trade diversion to Vietnam/Mexico",
            value=True,
            help="Model trade flow diversion through alternative manufacturing hubs",
        )

    impacts = _compute_scenario_impacts(
        us_china_pct=float(us_china_pct),
        us_eu_pct=float(us_eu_pct),
        china_retaliation_pct=float(china_retaliation_pct),
        pmi_impact_pp=float(pmi_impact_pp),
        trade_diversion=trade_diversion,
    )

    with col_right:
        st.markdown(
            '<div style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.10em;'
            ' color:' + C_TEXT3 + '; margin-bottom:12px">Live Impact Preview</div>',
            unsafe_allow_html=True,
        )

        preview_html = (
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
            ' border-radius:12px; padding:18px 20px">'

            # Trans-Pacific EB
            '<div style="font-size:0.72rem; font-weight:700; color:' + C_TEXT3 + ';'
            ' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:10px">'
            "Trans-Pacific EB Lane</div>"

            + _metric_pill(
                "Volume Change",
                impacts["tp_eb_vol_str"],
                impacts["tp_eb_vol_color"],
            )
            + _metric_pill(
                "Rate Change Estimate",
                impacts["tp_eb_rate_str"],
                impacts["tp_eb_rate_color"],
            )

            # Asia-Europe
            + '<div style="font-size:0.72rem; font-weight:700; color:' + C_TEXT3 + ';'
            ' text-transform:uppercase; letter-spacing:0.08em; margin-top:14px; margin-bottom:10px">'
            "Asia-Europe Lane</div>"

            + _metric_pill(
                "Volume Change",
                impacts["ae_vol_str"],
                impacts["ae_vol_color"],
            )
            + _metric_pill(
                "Rate Change Estimate",
                impacts["ae_rate_str"],
                impacts["ae_rate_color"],
            )

            # Stock exposure
            + '<div style="font-size:0.72rem; font-weight:700; color:' + C_TEXT3 + ';'
            ' text-transform:uppercase; letter-spacing:0.08em; margin-top:14px; margin-bottom:10px">'
            "Stock Exposure (Rate Impact)</div>"

            + _metric_pill("ZIM (Most Exposed)",  impacts["zim_str"],  impacts["zim_color"])
            + _metric_pill("MATX (Pacific Focus)", impacts["matx_str"], impacts["matx_color"])

            + "</div>"
        )

        st.markdown(preview_html, unsafe_allow_html=True)

        # Diversion note
        if trade_diversion and float(us_china_pct) > 14:
            st.markdown(
                '<div style="margin-top:8px; padding:10px 14px; background:rgba(16,185,129,0.10);'
                ' border:1px solid rgba(16,185,129,0.30); border-radius:8px;'
                ' font-size:0.80rem; color:' + C_HIGH + '">'
                "Trade diversion active — Vietnam/Mexico absorbing ~"
                + str(round(impacts["diversion_bonus_tp"] * 100, 1))
                + "pp of diverted Trans-Pacific volume"
                + "</div>",
                unsafe_allow_html=True,
            )

    # Also run tariff_analyzer if we have real route_results
    if route_results:
        shock_frac = max(0.0, float(us_china_pct) / 100.0 - 0.145)
        try:
            tariff_impacts = analyze_tariff_sensitivity(route_results, tariff_shock_pct=shock_frac)
            logger.debug(
                "tariff_analyzer returned {} route impacts for scenario shock={:.1%}",
                len(tariff_impacts),
                shock_frac,
            )
        except Exception as exc:
            logger.warning("tariff_analyzer call failed: {}", exc)
            tariff_impacts = []

        if tariff_impacts:
            top_hits = sorted(
                tariff_impacts,
                key=lambda x: abs(x.net_opportunity_delta),
                reverse=True,
            )[:5]

            rows_html = ""
            for ti in top_hits:
                delta_color = C_HIGH if ti.net_opportunity_delta >= 0 else C_DANGER
                sign = "+" if ti.net_opportunity_delta >= 0 else ""
                rows_html += (
                    "<tr>"
                    '<td style="color:' + C_TEXT + '; font-size:0.82rem; padding:8px 6px">'
                    + ti.route_name + "</td>"
                    '<td style="color:' + C_TEXT2 + '; font-size:0.82rem; padding:8px 6px">'
                    + str(round(ti.volume_impact_pct * 100, 1)) + "%" + "</td>"
                    '<td style="color:' + C_TEXT2 + '; font-size:0.82rem; padding:8px 6px">'
                    + str(round(ti.rate_impact_pct * 100, 1)) + "%" + "</td>"
                    '<td style="color:' + delta_color + '; font-weight:700; font-size:0.82rem; padding:8px 6px">'
                    + sign + str(round(ti.net_opportunity_delta * 100, 1)) + "%" + "</td>"
                    "</tr>"
                )

            table_html = (
                '<div style="margin-top:18px">'
                '<div style="font-size:0.72rem; text-transform:uppercase; letter-spacing:0.08em;'
                ' color:' + C_TEXT3 + '; margin-bottom:8px">Top 5 Affected Routes (Tariff Analyzer)</div>'
                '<table style="width:100%; border-collapse:collapse">'
                "<thead><tr>"
                '<th style="color:' + C_TEXT3 + '; font-size:0.70rem; text-transform:uppercase;'
                ' padding:6px 6px; text-align:left; border-bottom:1px solid rgba(255,255,255,0.08)">Route</th>'
                '<th style="color:' + C_TEXT3 + '; font-size:0.70rem; text-transform:uppercase;'
                ' padding:6px 6px; text-align:left; border-bottom:1px solid rgba(255,255,255,0.08)">Vol Chg</th>'
                '<th style="color:' + C_TEXT3 + '; font-size:0.70rem; text-transform:uppercase;'
                ' padding:6px 6px; text-align:left; border-bottom:1px solid rgba(255,255,255,0.08)">Rate Chg</th>'
                '<th style="color:' + C_TEXT3 + '; font-size:0.70rem; text-transform:uppercase;'
                ' padding:6px 6px; text-align:left; border-bottom:1px solid rgba(255,255,255,0.08)">Net Delta</th>'
                "</tr></thead>"
                "<tbody>" + rows_html + "</tbody>"
                "</table></div>"
            )
            st.markdown(table_html, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Trade Diversion Sankey
# ══════════════════════════════════════════════════════════════════════════════

def _render_trade_diversion_sankey() -> None:
    logger.debug("Rendering trade diversion Sankey diagram")

    # Nodes
    # 0: China (origin)
    # 1: USA (destination)
    # 2: Vietnam (intermediate)
    # 3: Mexico (intermediate)
    # 4: USA-direct (same destination, split trace for clarity)
    # 5: EU (origin for second triangle)
    # 6: EU-bound (destination)

    node_labels = [
        "China",          # 0
        "USA (Direct)",   # 1
        "Vietnam",        # 2
        "Mexico",         # 3
        "USA (Diverted)", # 4
        "China (AE)",     # 5
        "Europe",         # 6
        "SE Asia Hub",    # 7
    ]

    node_colors = [
        "#ef4444",   # China — red
        "#f59e0b",   # USA direct — amber
        "#10b981",   # Vietnam — green
        "#10b981",   # Mexico — green
        "#3b82f6",   # USA diverted — blue
        "#ef4444",   # China AE — red
        "#8b5cf6",   # Europe — purple
        "#06b6d4",   # SE Asia Hub — cyan
    ]

    # source, target, value (TEU thousands/year), color, label
    # Original direct US-China flow (gray, faded) — pre-tariff baseline
    # Diverted flows (colored) — post 145% tariff scenario
    flow_defs = [
        # Original US-China direct (faded — historical baseline)
        (0, 1, 8000,  "rgba(100,116,139,0.35)", "Pre-tariff: China → USA (8.0M TEU)"),
        # Diverted: China → Vietnam → USA
        (0, 2, 2200,  "rgba(16,185,129,0.70)",  "China → Vietnam transship (2.2M TEU)"),
        (2, 4, 2200,  "rgba(16,185,129,0.70)",  "Vietnam → USA (2.2M TEU)"),
        # Diverted: China → Mexico → USA (assembly)
        (0, 3, 1400,  "rgba(16,185,129,0.60)",  "China components → Mexico assembly (1.4M TEU)"),
        (3, 4, 1400,  "rgba(16,185,129,0.60)",  "Mexico nearshoring → USA (1.4M TEU)"),
        # Residual direct US-China (what remains at 145% tariff)
        (0, 1, 2800,  "rgba(239,68,68,0.55)",   "Residual China → USA direct (2.8M TEU)"),
        # Asia-Europe diversion via SE Asia
        (5, 7, 1800,  "rgba(6,182,212,0.65)",   "China → SE Asia hub (1.8M TEU)"),
        (7, 6, 1800,  "rgba(6,182,212,0.65)",   "SE Asia hub → Europe (1.8M TEU)"),
        # China direct to Europe (residual)
        (5, 6, 3200,  "rgba(139,92,246,0.45)",  "China → Europe direct (3.2M TEU)"),
    ]

    sources  = [f[0] for f in flow_defs]
    targets  = [f[1] for f in flow_defs]
    values   = [f[2] for f in flow_defs]
    link_col = [f[3] for f in flow_defs]
    link_lbl = [f[4] for f in flow_defs]

    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            pad=18,
            thickness=22,
            line=dict(color="rgba(255,255,255,0.12)", width=0.8),
            label=node_labels,
            color=node_colors,
            hovertemplate="<b>%{label}</b><extra></extra>",
        ),
        link=dict(
            source=sources,
            target=targets,
            value=values,
            color=link_col,
            label=link_lbl,
            hovertemplate=(
                "<b>%{label}</b><br>"
                "Volume: %{value:,} TEU/yr<extra></extra>"
            ),
        ),
    ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font=dict(color=C_TEXT, size=11),
        height=400,
        margin=dict(l=20, r=20, t=20, b=20),
    )

    st.plotly_chart(fig, use_container_width=True)

    # Legend note
    st.markdown(
        '<div style="font-size:0.78rem; color:' + C_TEXT3 + '; margin-top:-6px; padding:0 4px">'
        "Gray flows = pre-tariff baseline. Colored flows = diverted/residual volumes at 145% US-China tariff. "
        "TEU/yr figures are modeled estimates based on 2024 trade data and elasticity assumptions."
        "</div>",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Historical Tariff Impact Events Timeline
# ══════════════════════════════════════════════════════════════════════════════

def _render_historical_timeline() -> None:
    logger.debug("Rendering historical tariff event timeline")

    # Key tariff events: (date_str, label, y_annot, description)
    tariff_events = [
        ("2018-07-06", "Section 301\n$34B",   1, "US Section 301 tariffs on $34B Chinese goods at 25%"),
        ("2018-08-23", "+$16B",                2, "US tariffs on additional $16B Chinese goods at 25%"),
        ("2018-09-24", "$200B @ 10%",          1, "US tariffs on $200B Chinese goods at 10%"),
        ("2019-05-10", "Raised to 25%",        2, "US raises $200B tariff tranche from 10% to 25%"),
        ("2020-01-15", "Phase 1 Deal",         1, "US-China Phase 1 trade deal — partial rollback"),
        ("2021-06-01", "Pause",                2, "Trade war pause — rates stabilize under Biden"),
        ("2025-01-20", "Trump 2.0\n10% base", 1, "Trump tariffs 2.0 — 10% universal baseline"),
        ("2025-04-09", "Escalation\n145%",    2, "US-China tariffs escalate to 145%"),
    ]

    # Approximate Trans-Pacific EB spot rate index (illustrative, USD/FEU)
    # Key: rate series overlaid to show correlation with tariff events
    rate_dates = [
        "2018-01-01", "2018-07-01", "2018-10-01", "2019-01-01", "2019-05-01",
        "2019-10-01", "2020-01-01", "2020-06-01", "2020-10-01", "2021-01-01",
        "2021-06-01", "2021-12-01", "2022-03-01", "2022-09-01", "2023-01-01",
        "2023-06-01", "2024-01-01", "2024-06-01", "2025-01-01", "2025-04-01",
        "2025-07-01", "2025-10-01",
    ]
    rate_values = [
        1800, 2100, 2400, 2200, 2600,
        2100, 1900, 2400, 3800, 5500,
        7200, 10800, 9500, 6200, 2800,
        1600, 2200, 2800, 3400, 5100,
        4200, 3800,
    ]

    fig = go.Figure()

    # Rate series (left y-axis)
    fig.add_trace(go.Scatter(
        x=rate_dates,
        y=rate_values,
        name="Trans-Pacific EB Rate (USD/FEU)",
        mode="lines",
        line=dict(color=C_ACCENT, width=2.5),
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.08)",
        yaxis="y1",
        hovertemplate="<b>Trans-Pacific EB</b><br>%{x}<br>$%{y:,}/FEU<extra></extra>",
    ))

    # Tariff event markers
    event_x = [ev[0] for ev in tariff_events]
    event_labels = [ev[1] for ev in tariff_events]
    event_desc   = [ev[3] for ev in tariff_events]

    # Map event dates to approximate rate values for marker placement
    rate_lookup = dict(zip(rate_dates, rate_values))

    def _nearest_rate(date_str: str) -> float:
        yr, mo = date_str[:7].split("-")
        key_mo = date_str[:7] + "-01"
        if key_mo in rate_lookup:
            return rate_lookup[key_mo]
        # fallback: median
        return 3500.0

    event_y = [_nearest_rate(ev[0]) for ev in tariff_events]

    fig.add_trace(go.Scatter(
        x=event_x,
        y=event_y,
        name="Tariff Events",
        mode="markers+text",
        marker=dict(
            size=16,
            color=C_WARN,
            symbol="diamond",
            line=dict(color="rgba(255,255,255,0.35)", width=1.5),
        ),
        text=event_labels,
        textposition=[
            "top center", "top right", "top center", "top right",
            "top center", "top right", "top center", "top right",
        ],
        textfont=dict(size=9, color=C_WARN),
        hovertext=[
            "<b>" + ev[1].replace("\n", " ") + "</b><br>" + ev[3]
            for ev in tariff_events
        ],
        hoverinfo="text",
        yaxis="y1",
    ))

    # Vertical annotation lines for tariff events
    for ev in tariff_events:
        fig.add_vline(
            x=ev[0],
            line_color="rgba(245,158,11,0.25)",
            line_dash="dot",
            line_width=1,
        )

    # Phase 1 shading
    fig.add_vrect(
        x0="2020-01-15", x1="2025-01-20",
        fillcolor="rgba(16,185,129,0.05)",
        line_width=0,
        annotation_text="Trade War Pause",
        annotation_position="top left",
        annotation_font=dict(size=9, color=C_TEXT3),
    )

    # 2021 peak shading
    fig.add_vrect(
        x0="2021-06-01", x1="2022-06-01",
        fillcolor="rgba(239,68,68,0.06)",
        line_width=0,
        annotation_text="COVID Surge Peak",
        annotation_position="top right",
        annotation_font=dict(size=9, color=C_TEXT3),
    )

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor="#111827",
        font=dict(color=C_TEXT),
        height=420,
        margin=dict(l=60, r=40, t=30, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=10, color=C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
        xaxis=dict(
            title="Date",
            gridcolor="rgba(255,255,255,0.05)",
            color=C_TEXT2,
            showspikes=True,
            spikecolor="rgba(255,255,255,0.20)",
            spikethickness=1,
        ),
        yaxis=dict(
            title="Trans-Pacific EB Rate (USD/FEU)",
            gridcolor="rgba(255,255,255,0.05)",
            color=C_TEXT2,
            tickformat="$,.0f",
        ),
        hovermode="x unified",
    )

    st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Supply Chain Reshoring Tracker
# ══════════════════════════════════════════════════════════════════════════════

_RESHORING_TRENDS: list[dict] = [
    {
        "flag": "VN",
        "country": "Vietnam",
        "industry": "Electronics & Manufacturing",
        "growth_pct": 40,
        "routes": ["Trans-Pacific EB", "SEA-Transpacific EB"],
        "note": "US-China alternative hub for consumer electronics, semiconductors, apparel",
        "rating": "HIGH",
        "rating_color": C_HIGH,
    },
    {
        "flag": "MX",
        "country": "Mexico",
        "industry": "Automotive & Assembly",
        "growth_pct": 25,
        "routes": ["US East-South America", "Trans-Pacific WB"],
        "note": "USMCA-driven nearshoring — auto parts, EV assembly, white goods",
        "rating": "HIGH",
        "rating_color": C_HIGH,
    },
    {
        "flag": "IN",
        "country": "India",
        "industry": "Pharma, IT & Textiles",
        "growth_pct": 15,
        "routes": ["South Asia-Europe", "Asia-Europe"],
        "note": "China+1 beneficiary — Apple supply chain, generic pharma, IT services",
        "rating": "MODERATE",
        "rating_color": C_ACCENT,
    },
    {
        "flag": "PL",
        "country": "Eastern Europe (Poland/Romania)",
        "industry": "EU Supply Chain",
        "growth_pct": 20,
        "routes": ["Transatlantic", "Med Hub-Asia"],
        "note": "EU manufacturing reshoring — auto components, electronics, logistics hubs",
        "rating": "MODERATE",
        "rating_color": C_ACCENT,
    },
    {
        "flag": "MA",
        "country": "Morocco / North Africa",
        "industry": "EU Textile & Automotive",
        "growth_pct": 30,
        "routes": ["North Africa-Europe", "Med Hub-Asia"],
        "note": "Proximity to EU markets — Renault/Stellantis production, textile OEM",
        "rating": "MODERATE",
        "rating_color": C_ACCENT,
    },
    {
        "flag": "BD",
        "country": "Bangladesh",
        "industry": "Apparel & RMG",
        "growth_pct": 10,
        "routes": ["South Asia-Europe", "Asia-Europe"],
        "note": "Low-cost apparel manufacturing — H&M, Zara, PVH supply chains",
        "rating": "LOW-MOD",
        "rating_color": C_WARN,
    },
]

# Unicode flag emojis by 2-letter country code
_FLAG_MAP: dict[str, str] = {
    "VN": "\U0001f1fb\U0001f1f3",
    "MX": "\U0001f1f2\U0001f1fd",
    "IN": "\U0001f1ee\U0001f1f3",
    "PL": "\U0001f1f5\U0001f1f1",
    "MA": "\U0001f1f2\U0001f1e6",
    "BD": "\U0001f1e7\U0001f1e9",
}


def _render_reshoring_tracker() -> None:
    logger.debug("Rendering reshoring tracker cards")

    cols = st.columns(3)

    for idx, trend in enumerate(_RESHORING_TRENDS):
        col = cols[idx % 3]
        flag_emoji = _FLAG_MAP.get(trend["flag"], "")
        routes_str = " | ".join(trend["routes"])
        growth_bar_pct = min(100, trend["growth_pct"] * 2)  # scale 50% = full bar
        rating_color = trend["rating_color"]

        card_html = (
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
            ' border-radius:12px; padding:16px 18px; margin-bottom:12px; height:100%">'

            # Flag + Country header
            '<div style="display:flex; align-items:center; gap:10px; margin-bottom:10px">'
            '<span style="font-size:1.8rem; line-height:1">' + flag_emoji + "</span>"
            '<div>'
            '<div style="font-size:0.90rem; font-weight:700; color:' + C_TEXT + '">'
            + trend["country"] + "</div>"
            '<div style="font-size:0.75rem; color:' + C_TEXT2 + '">' + trend["industry"] + "</div>"
            "</div></div>"

            # Growth metric
            '<div style="margin-bottom:8px">'
            '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px">'
            '<span style="font-size:0.72rem; color:' + C_TEXT3 + '; text-transform:uppercase; letter-spacing:0.07em">YoY Capacity Growth</span>'
            '<span style="font-size:1.0rem; font-weight:800; color:' + rating_color + '">+'
            + str(trend["growth_pct"]) + "%</span>"
            "</div>"
            '<div style="background:rgba(255,255,255,0.06); border-radius:4px; height:5px">'
            '<div style="width:' + str(growth_bar_pct) + '%; height:100%; background:' + rating_color + '; border-radius:4px"></div>'
            "</div></div>"

            # Opportunity rating
            '<div style="margin-bottom:8px">'
            '<span style="font-size:0.68rem; font-weight:700; color:' + rating_color + ';'
            ' background:' + rating_color + '18; border:1px solid ' + rating_color + '44;'
            ' border-radius:4px; padding:2px 7px; letter-spacing:0.06em">'
            + trend["rating"] + " OPPORTUNITY"
            + "</span></div>"

            # Note
            '<div style="font-size:0.78rem; color:' + C_TEXT2 + '; line-height:1.45; margin-bottom:10px">'
            + trend["note"] + "</div>"

            # Impacted routes
            '<div style="font-size:0.70rem; color:' + C_TEXT3 + '; border-top:1px solid '
            + C_BORDER + '; padding-top:8px; margin-top:4px">'
            "Routes: " + routes_str + "</div>"

            "</div>"
        )

        with col:
            st.markdown(card_html, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Main render function
# ══════════════════════════════════════════════════════════════════════════════

def render(
    route_results: list,
    port_results: list,
    freight_data: dict,
    macro_data: dict,
    trade_data: dict,
) -> None:
    """Render the Trade War & Tariff Impact Simulator tab."""
    logger.info("Rendering Trade War tab")

    # ── Tab header ─────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="padding:16px 0 24px 0; border-bottom:1px solid rgba(255,255,255,0.06);'
        ' margin-bottom:24px">'
        '<div style="font-size:0.68rem; text-transform:uppercase; letter-spacing:0.15em;'
        ' color:#475569; margin-bottom:6px">GEOPOLITICAL ANALYSIS</div>'
        '<div style="font-size:1.6rem; font-weight:900; color:#f1f5f9;'
        ' letter-spacing:-0.03em; line-height:1.1">Trade War & Tariff Impact Simulator</div>'
        '<div style="font-size:0.85rem; color:#64748b; margin-top:6px">'
        "Model the shipping market impact of US-China tariffs, trade diversion, and reshoring trends"
        "</div></div>",
        unsafe_allow_html=True,
    )

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — Global Tariff Heat Map
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Global Tariff Risk Heat Map",
        "2025-2026 tariff exposure by country — color scale: green (low) to red (high risk)",
    )
    _render_tariff_heatmap()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — Scenario Builder
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Tariff Scenario Builder",
        "Adjust tariff parameters to preview live shipping market impacts",
    )
    _render_scenario_builder(route_results)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Trade Diversion Sankey
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Trade Diversion Analysis",
        "How elevated US-China tariffs redirect cargo flows through Vietnam and Mexico",
    )
    _render_trade_diversion_sankey()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — Historical Timeline
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Historical Tariff Impact Events",
        "Key tariff escalation events overlaid with Trans-Pacific EB spot rate series",
    )
    _render_historical_timeline()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 5 — Reshoring Tracker
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Supply Chain Reshoring Tracker",
        "Major manufacturing relocation trends and their impact on shipping lanes",
    )
    _render_reshoring_tracker()

    logger.info("Trade War tab render complete")
