"""
tab_finance.py
==============
Trade Finance Dashboard tab for the Ship Tracker application.

render(macro_data, freight_data, route_results) is the public entry point.

Sections
--------
1. Trade Finance Health Dashboard  — 5-column indicator cards with gauges
2. Interest Rate Impact Model      — current suppression + interactive slider
3. Credit Availability Map         — choropleth: green = easy, red = tight
4. L/C vs Open Account Trend       — 2015-2026 stacked area / line chart
5. De-dollarization Monitor        — USD trade share decline + CNY growth
6. Sanctions Impact Tracker        — route-level sanctions card summary
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from loguru import logger
import streamlit as st

from processing.trade_finance import (
    TradeFinanceIndicator,
    TradeFinanceRiskScore,
    build_trade_finance_indicators,
    compute_trade_finance_composite,
    compute_regional_finance_risk,
    compute_interest_rate_impact_on_shipping,
)
from ui.styles import (
    C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_LOW, C_ACCENT, C_MOD, C_MACRO,
    _hex_to_rgba as _rgba,
    section_header,
)

# ---------------------------------------------------------------------------
# Local colour helpers
# ---------------------------------------------------------------------------

C_WARN   = C_MOD
C_DANGER = C_LOW
C_BULL   = C_HIGH

_SIGNAL_COLOR: Dict[str, str] = {
    "BULLISH":  C_HIGH,
    "BEARISH":  C_LOW,
    "NEUTRAL":  C_TEXT3,
}

_SIGNAL_ARROW: Dict[str, str] = {
    "BULLISH":  "▲",
    "BEARISH":  "▼",
    "NEUTRAL":  "—",
}


def _hr() -> None:
    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )


def _latest_macro_value(macro_data: dict, series_id: str) -> float | None:
    """Extract the most recent float value from a FRED dataframe."""
    df = macro_data.get(series_id)
    if df is None or df.empty or "value" not in df.columns:
        return None
    v = df["value"].dropna()
    return float(v.iloc[-1]) if not v.empty else None


# ---------------------------------------------------------------------------
# Section 1 — Trade Finance Health Dashboard
# ---------------------------------------------------------------------------

def _render_finance_health(indicators: List[TradeFinanceIndicator]) -> None:
    section_header(
        "Trade Finance Health Dashboard",
        "10 key indicators — green border = bullish credit, red = bearish/tight,"
        " signal leads shipping demand by weeks shown",
    )

    composite = compute_trade_finance_composite(indicators)
    cs = composite["composite_score"]
    dom = composite["dominant_signal"]
    dom_color = _SIGNAL_COLOR.get(dom, C_TEXT3)

    # ── composite summary bar ───────────────────────────────────────────────
    pct_bar = round(cs * 100)
    st.markdown(
        '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER
        + '; border-radius:10px; padding:14px 18px; margin-bottom:16px;'
        ' display:flex; align-items:center; gap:20px">'
        '<div style="flex:0 0 auto">'
        '<div style="font-size:0.65rem; font-weight:700; color:' + C_TEXT3
        + '; text-transform:uppercase; letter-spacing:0.07em">Composite Credit Score</div>'
        '<div style="font-size:2rem; font-weight:800; color:' + dom_color
        + '; font-variant-numeric:tabular-nums">'
        + str(pct_bar) + '/100</div>'
        '<div style="font-size:0.75rem; font-weight:600; color:' + dom_color
        + '">' + dom + '</div>'
        '</div>'
        '<div style="flex:1 1 auto">'
        '<div style="font-size:0.70rem; color:' + C_TEXT2
        + '; margin-bottom:6px">Bullish: '
        + str(composite["bullish_count"])
        + '&nbsp;&nbsp;Bearish: '
        + str(composite["bearish_count"])
        + '&nbsp;&nbsp;Neutral: '
        + str(composite["neutral_count"]) + '</div>'
        '<div style="background:rgba(255,255,255,0.07); border-radius:6px; height:8px; overflow:hidden">'
        '<div style="width:' + str(pct_bar) + '%; height:100%; background:' + dom_color
        + '; border-radius:6px; transition:width 0.4s ease"></div>'
        '</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── 5-column indicator grid (2 rows of 5) ───────────────────────────────
    chunk_size = 5
    rows = [indicators[i:i + chunk_size] for i in range(0, len(indicators), chunk_size)]

    for row in rows:
        cols = st.columns(chunk_size)
        for col, ind in zip(cols, row):
            with col:
                sig_color = _SIGNAL_COLOR.get(ind.signal, C_TEXT3)
                arrow = _SIGNAL_ARROW.get(ind.signal, "—")
                yoy = ind.yoy_change_pct
                yoy_str = ("+" if yoy >= 0 else "") + str(round(yoy, 1)) + "%"

                # Gauge fill: map score into 0-100 loosely from yoy direction
                gauge_pct = min(100, max(0, 50 + yoy * 2))

                # Format current value
                if abs(ind.current_value) >= 1000:
                    val_str = ("$" + str(round(ind.current_value / 1000, 1)) + "T"
                               if ind.current_value >= 1000
                               else str(round(ind.current_value, 1)))
                elif abs(ind.current_value) <= 20 and ind.current_value != 0:
                    val_str = str(round(ind.current_value, 1))
                else:
                    val_str = str(round(ind.current_value, 0)).rstrip(".0") or "0"

                # Short label (first 22 chars)
                short_name = ind.indicator_name
                if len(short_name) > 22:
                    short_name = short_name[:20] + "…"

                st.markdown(
                    '<div style="background:' + C_CARD + '; border:1px solid '
                    + sig_color + '44; border-top:2px solid ' + sig_color
                    + '; border-radius:10px; padding:12px 12px 8px 12px;'
                    ' margin-bottom:4px; min-height:130px">'
                    # label row
                    '<div style="font-size:0.58rem; font-weight:700; color:' + C_TEXT3
                    + '; text-transform:uppercase; letter-spacing:0.06em;'
                    ' white-space:nowrap; overflow:hidden; text-overflow:ellipsis;'
                    ' margin-bottom:4px" title="' + ind.indicator_name + '">'
                    + short_name + '</div>'
                    # value + arrow
                    '<div style="display:flex; align-items:baseline; gap:5px;'
                    ' margin-bottom:4px">'
                    '<span style="font-size:1.05rem; font-weight:800; color:' + C_TEXT
                    + '; font-variant-numeric:tabular-nums">' + val_str + '</span>'
                    '<span style="font-size:0.68rem; font-weight:600; color:'
                    + sig_color + '">' + arrow + ' ' + yoy_str + '</span>'
                    '</div>'
                    # mini gauge bar
                    '<div style="background:rgba(255,255,255,0.07); border-radius:4px;'
                    ' height:4px; margin-bottom:6px; overflow:hidden">'
                    '<div style="width:' + str(round(gauge_pct)) + '%; height:100%;'
                    ' background:' + sig_color + '; border-radius:4px"></div>'
                    '</div>'
                    # signal badge + lead time
                    '<div style="display:flex; justify-content:space-between;'
                    ' align-items:center">'
                    '<span style="background:' + _rgba(sig_color, 0.15)
                    + '; color:' + sig_color
                    + '; border:1px solid ' + _rgba(sig_color, 0.3)
                    + '; padding:1px 7px; border-radius:999px; font-size:0.58rem;'
                    ' font-weight:700; text-transform:uppercase">'
                    + ind.signal + '</span>'
                    '<span style="font-size:0.58rem; color:' + C_TEXT3
                    + '">leads ' + str(ind.shipping_lead_weeks) + 'w</span>'
                    '</div>'
                    '</div>',
                    unsafe_allow_html=True,
                )

    # ── data source expander ─────────────────────────────────────────────────
    with st.expander("Indicator Details & Sources"):
        rows_data = []
        for ind in indicators:
            rows_data.append({
                "Indicator": ind.indicator_name,
                "Value": ind.current_value,
                "YoY %": ind.yoy_change_pct,
                "Signal": ind.signal,
                "Lead (wks)": ind.shipping_lead_weeks,
                "Source": ind.data_source,
            })
        detail_df = pd.DataFrame(rows_data)
        st.dataframe(detail_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Section 2 — Interest Rate Impact Model
# ---------------------------------------------------------------------------

def _render_rate_impact(macro_data: dict) -> None:
    section_header(
        "Interest Rate Impact on Container Demand",
        "Higher rates increase inventory carrying costs, triggering destocking"
        " and suppressing import order volumes with a 6-12 month lag",
    )

    # Try to pull DGS10 from macro data, fall back to static 2025 estimate
    dgs10_val = _latest_macro_value(macro_data, "DGS10")
    static_rate = 4.45   # approximate 10Y Treasury yield March 2026
    current_rate = dgs10_val if dgs10_val is not None else static_rate

    rate_source = "DGS10 (FRED live)" if dgs10_val is not None else "static estimate (4.45%)"
    logger.info(
        "tab_finance rate model: using rate={r:.2f}% source={s}",
        r=current_rate, s=rate_source,
    )

    # Compute impact at the live rate
    live_impact = compute_interest_rate_impact_on_shipping(current_rate)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER
            + '; border-top:2px solid ' + C_ACCENT
            + '; border-radius:10px; padding:16px; text-align:center">'
            '<div style="font-size:0.62rem; font-weight:700; color:' + C_TEXT3
            + '; text-transform:uppercase; letter-spacing:0.07em">10Y Treasury (DGS10)</div>'
            '<div style="font-size:2rem; font-weight:800; color:' + C_TEXT
            + '; font-variant-numeric:tabular-nums">'
            + str(round(current_rate, 2)) + '%</div>'
            '<div style="font-size:0.70rem; color:' + C_TEXT3 + '">' + rate_source + '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    with c2:
        impact_val = live_impact["estimated_demand_impact_pct"]
        impact_color = C_LOW if impact_val < 0 else C_HIGH
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER
            + '; border-top:2px solid ' + impact_color
            + '; border-radius:10px; padding:16px; text-align:center">'
            '<div style="font-size:0.62rem; font-weight:700; color:' + C_TEXT3
            + '; text-transform:uppercase; letter-spacing:0.07em">Demand Suppression (vs Neutral)</div>'
            '<div style="font-size:2rem; font-weight:800; color:' + impact_color
            + '; font-variant-numeric:tabular-nums">'
            + str(impact_val) + '%</div>'
            '<div style="font-size:0.70rem; color:' + C_TEXT3
            + '">6-12 month transmission lag</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    with c3:
        cum_val = live_impact["cumulative_impact_since_2022_pct"]
        cum_color = C_LOW if cum_val < 0 else C_HIGH
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER
            + '; border-top:2px solid ' + cum_color
            + '; border-radius:10px; padding:16px; text-align:center">'
            '<div style="font-size:0.62rem; font-weight:700; color:' + C_TEXT3
            + '; text-transform:uppercase; letter-spacing:0.07em">Cumulative Since Mar 2022</div>'
            '<div style="font-size:2rem; font-weight:800; color:' + cum_color
            + '; font-variant-numeric:tabular-nums">'
            + str(cum_val) + '%</div>'
            '<div style="font-size:0.70rem; color:' + C_TEXT3
            + '">vs 0.08% pre-hike baseline</div>'
            '</div>',
            unsafe_allow_html=True,
        )

    # Narrative callout
    st.markdown(
        '<div style="background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.25);'
        ' border-radius:8px; padding:12px 16px; margin-top:12px;'
        ' font-size:0.82rem; color:' + C_TEXT2 + '">'
        '<strong style="color:#ef4444">Rate Cycle Impact:</strong>&nbsp;'
        'Fed rate hikes since March 2022 have suppressed container demand by an'
        ' estimated <strong style="color:#ef4444">'
        + str(abs(cum_val))
        + '%</strong>,'
        ' acting through elevated inventory carrying costs, reduced import order'
        ' frequency, and tighter trade credit availability. '
        + live_impact["scenario_label"] + '.'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── Interactive "what if rates cut to X%?" slider ────────────────────────
    st.markdown(
        '<div style="margin-top:18px; margin-bottom:8px; font-size:0.78rem;'
        ' font-weight:600; color:' + C_TEXT2 + '">What-If Rate Scenario Modeller</div>',
        unsafe_allow_html=True,
    )
    scenario_rate = st.slider(
        "Model rate cut to (%)",
        min_value=0.5,
        max_value=7.0,
        value=float(round(current_rate, 1)),
        step=0.25,
        help="Drag to model how a rate change would affect estimated container demand",
        key="finance_rate_slider",
    )

    if abs(scenario_rate - current_rate) > 0.05:
        scenario_impact = compute_interest_rate_impact_on_shipping(scenario_rate)
        delta_demand = round(
            scenario_impact["estimated_demand_impact_pct"]
            - live_impact["estimated_demand_impact_pct"], 2
        )
        delta_color = C_HIGH if delta_demand >= 0 else C_LOW
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid '
            + _rgba(delta_color, 0.3) + '; border-radius:8px; padding:12px 16px;'
            ' font-size:0.82rem; color:' + C_TEXT2 + '">'
            'At <strong style="color:' + C_TEXT + '">'
            + str(round(scenario_rate, 2)) + '%</strong>:'
            ' estimated demand impact = <strong style="color:' + delta_color + '">'
            + str(scenario_impact["estimated_demand_impact_pct"]) + '%</strong>'
            ' (delta vs current: <strong style="color:' + delta_color + '">'
            + ("+" if delta_demand >= 0 else "") + str(delta_demand) + ' pp</strong>).'
            '&nbsp;&nbsp;' + scenario_impact["scenario_label"] + '.'
            '</div>',
            unsafe_allow_html=True,
        )

        # Rate sensitivity bar chart
        rate_points = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0]
        demand_vals = [
            compute_interest_rate_impact_on_shipping(r)["estimated_demand_impact_pct"]
            for r in rate_points
        ]
        bar_colors = [C_HIGH if v >= 0 else C_LOW for v in demand_vals]

        fig_rate = go.Figure(go.Bar(
            x=rate_points,
            y=demand_vals,
            marker_color=bar_colors,
            hovertemplate="Rate: %{x:.2f}%<br>Demand impact: %{y:.1f}%<extra></extra>",
        ))
        # Mark current rate
        fig_rate.add_vline(
            x=current_rate,
            line_dash="dot",
            line_color=C_ACCENT,
            line_width=2,
            annotation_text="Current",
            annotation_font=dict(color=C_ACCENT, size=10),
            annotation_position="top",
        )
        # Mark scenario rate
        if abs(scenario_rate - current_rate) > 0.05:
            fig_rate.add_vline(
                x=scenario_rate,
                line_dash="dash",
                line_color=C_MOD,
                line_width=2,
                annotation_text="Scenario",
                annotation_font=dict(color=C_MOD, size=10),
                annotation_position="top right",
            )
        fig_rate.update_layout(
            template="plotly_dark",
            height=260,
            paper_bgcolor=C_CARD,
            plot_bgcolor=C_CARD,
            margin=dict(t=20, b=20, l=10, r=10),
            xaxis=dict(
                title="Benchmark Rate (%)",
                gridcolor="rgba(255,255,255,0.05)",
                tickfont=dict(color=C_TEXT2, size=11),
                ticksuffix="%",
            ),
            yaxis=dict(
                title="Container Demand Impact (%)",
                gridcolor="rgba(255,255,255,0.05)",
                zeroline=True,
                zerolinecolor="rgba(255,255,255,0.18)",
                tickfont=dict(color=C_TEXT2, size=11),
                ticksuffix="%",
            ),
            hoverlabel=dict(
                bgcolor="#1a2235",
                bordercolor="rgba(255,255,255,0.15)",
                font=dict(color=C_TEXT, size=12),
            ),
            font=dict(family="Inter, sans-serif"),
            showlegend=False,
        )
        st.plotly_chart(fig_rate, use_container_width=True)


# ---------------------------------------------------------------------------
# Section 3 — Credit Availability Map
# ---------------------------------------------------------------------------

def _render_credit_map(risk_scores: List[TradeFinanceRiskScore]) -> None:
    section_header(
        "Trade Credit Availability — Global Risk Map",
        "Green = easy credit access · Red = tight / restricted · "
        "Bubble size proportional to risk score",
    )

    # Country-level mapping for choropleth
    # (score 0=easy/green  1=tight/red; mapped from regional risk scores)
    _COUNTRY_SCORES: list[tuple[str, float]] = [
        # High-risk / sanctioned
        ("RUS", 0.95), ("IRN", 0.90), ("VEN", 0.82), ("ARG", 0.78),
        ("BLR", 0.80), ("SYR", 0.85), ("PRK", 0.92), ("CUB", 0.70),
        # Elevated risk
        ("NGA", 0.62), ("ETH", 0.60), ("KEN", 0.55), ("GHA", 0.58),
        ("PAK", 0.52), ("BGD", 0.48), ("EGY", 0.50), ("TUR", 0.44),
        # Moderate (China)
        ("CHN", 0.38),
        # ASEAN moderate
        ("IDN", 0.30), ("PHL", 0.28), ("VNM", 0.26), ("MMR", 0.40),
        # Low risk — advanced economies
        ("USA", 0.12), ("DEU", 0.10), ("GBR", 0.11), ("FRA", 0.11),
        ("JPN", 0.12), ("KOR", 0.14), ("AUS", 0.13), ("CAN", 0.12),
        ("NLD", 0.10), ("SGP", 0.13), ("CHE", 0.09), ("SWE", 0.10),
        ("NOR", 0.10), ("DNK", 0.11), ("FIN", 0.10), ("BEL", 0.11),
        ("IND", 0.32), ("BRA", 0.38), ("MEX", 0.34), ("ZAF", 0.30),
        ("SAU", 0.20), ("ARE", 0.18), ("TWN", 0.15), ("HKG", 0.14),
    ]

    iso_codes  = [c[0] for c in _COUNTRY_SCORES]
    scores     = [c[1] for c in _COUNTRY_SCORES]
    # Map 0=easy (green) to 1=tight (red)
    colors     = scores

    fig_map = go.Figure(go.Choropleth(
        locations=iso_codes,
        z=colors,
        colorscale=[
            [0.0,  "#10b981"],   # green — easy
            [0.4,  "#f59e0b"],   # amber — moderate
            [0.7,  "#f97316"],   # orange — elevated
            [1.0,  "#ef4444"],   # red — restricted/sanctioned
        ],
        zmin=0.0,
        zmax=1.0,
        colorbar=dict(
            title=dict(text="Credit Risk", font=dict(color=C_TEXT2, size=11)),
            tickvals=[0, 0.25, 0.5, 0.75, 1.0],
            ticktext=["Easy", "Low", "Moderate", "High", "Restricted"],
            tickfont=dict(color=C_TEXT2, size=10),
            bgcolor="rgba(26,34,53,0.8)",
            bordercolor="rgba(255,255,255,0.1)",
            len=0.7,
            y=0.5,
        ),
        marker_line_color="rgba(255,255,255,0.08)",
        marker_line_width=0.5,
        hovertemplate="<b>%{location}</b><br>Credit Risk Score: %{z:.2f}<extra></extra>",
    ))

    # Annotate key restricted zones
    _ANNOTATIONS = [
        dict(lat=61.5, lon=105.3, text="Russia (SWIFT exc.)", color="#ef4444"),
        dict(lat=32.4, lon=53.7, text="Iran (sanctions)", color="#ef4444"),
        dict(lat=-34.0, lon=-64.0, text="Argentina (FX controls)", color="#f97316"),
        dict(lat=6.4, lon=-66.6, text="Venezuela (OFAC)", color="#ef4444"),
    ]

    for ann in _ANNOTATIONS:
        fig_map.add_trace(go.Scattergeo(
            lat=[ann["lat"]],
            lon=[ann["lon"]],
            mode="markers+text",
            marker=dict(size=10, color=ann["color"], symbol="circle",
                        line=dict(color="white", width=1)),
            text=[ann["text"]],
            textfont=dict(color=ann["color"], size=9),
            textposition="top center",
            showlegend=False,
            hoverinfo="skip",
        ))

    fig_map.update_geos(
        showcoastlines=True,
        coastlinecolor="rgba(255,255,255,0.15)",
        showland=True,
        landcolor="#111827",
        showocean=True,
        oceancolor="#0a0f1a",
        showlakes=False,
        showcountries=True,
        countrycolor="rgba(255,255,255,0.06)",
        projection_type="natural earth",
        bgcolor="#0a0f1a",
    )
    fig_map.update_layout(
        template="plotly_dark",
        height=420,
        paper_bgcolor=C_CARD,
        margin=dict(t=10, b=10, l=0, r=0),
        geo_bgcolor="#0a0f1a",
        font=dict(family="Inter, sans-serif"),
    )
    st.plotly_chart(fig_map, use_container_width=True)

    # ── Regional risk table ──────────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.72rem; font-weight:700; color:' + C_TEXT3
        + '; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px">'
        'Regional Credit Risk Detail</div>',
        unsafe_allow_html=True,
    )
    risk_rows = []
    for rs in risk_scores:
        score_pct = round(rs.score * 100)
        bar_color = (
            C_LOW if rs.score >= 0.7
            else C_MOD if rs.score >= 0.4
            else C_HIGH
        )
        risk_rows.append({
            "Region": rs.region,
            "Risk Score": score_pct,
            "Primary Risk": rs.primary_risk[:60] + ("…" if len(rs.primary_risk) > 60 else ""),
            "Rate Impact": ("+" if rs.rate_impact_pct > 0 else "") + str(rs.rate_impact_pct) + "%",
        })

    risk_df = pd.DataFrame(risk_rows)
    st.dataframe(risk_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Section 4 — L/C vs Open Account Trend
# ---------------------------------------------------------------------------

_LC_OA_DATA: dict = {
    "year": [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026],
    "lc_pct":   [43,   41,   38,   36,   34,   33,   31,   29,   27,   26,   25,   23],
    "doc_coll": [14,   13,   13,   12,   12,   11,   11,   10,   10,    9,    9,    8],
    "open_acc": [43,   46,   49,   52,   54,   56,   58,   61,   63,   65,   66,   69],
}


def _render_lc_oa_trend() -> None:
    section_header(
        "Letter of Credit vs Open Account Trend (2015-2026)",
        "L/C used for new/riskier counterparties — shift to open account signals"
        " deepening trade trust but reduces bank L/C revenue",
    )

    years = _LC_OA_DATA["year"]
    lc    = _LC_OA_DATA["lc_pct"]
    dc    = _LC_OA_DATA["doc_coll"]
    oa    = _LC_OA_DATA["open_acc"]

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=years, y=oa,
        name="Open Account",
        mode="lines+markers",
        line=dict(color=C_HIGH, width=2.5),
        marker=dict(size=6, color=C_HIGH),
        fill="tozeroy",
        fillcolor=_rgba(C_HIGH, 0.08),
        hovertemplate="Year: %{x}<br>Open Account: %{y}%<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=years, y=dc,
        name="Documentary Collections",
        mode="lines+markers",
        line=dict(color=C_MOD, width=2),
        marker=dict(size=6, color=C_MOD),
        fill="tonexty",
        fillcolor=_rgba(C_MOD, 0.06),
        hovertemplate="Year: %{x}<br>Documentary Collections: %{y}%<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=years, y=lc,
        name="Letter of Credit",
        mode="lines+markers",
        line=dict(color=C_ACCENT, width=2.5),
        marker=dict(size=6, color=C_ACCENT),
        hovertemplate="Year: %{x}<br>Letter of Credit: %{y}%<extra></extra>",
    ))

    # Annotation: 2021 peak
    fig.add_annotation(
        x=2021, y=31,
        text="L/C peak 31%",
        showarrow=True,
        arrowhead=2,
        arrowcolor=C_ACCENT,
        ax=40, ay=-30,
        font=dict(size=10, color=C_ACCENT),
        bgcolor="rgba(26,34,53,0.85)",
        borderpad=4,
    )
    # Annotation: open account 2026
    fig.add_annotation(
        x=2026, y=69,
        text="Open acct 69%",
        showarrow=True,
        arrowhead=2,
        arrowcolor=C_HIGH,
        ax=-50, ay=-20,
        font=dict(size=10, color=C_HIGH),
        bgcolor="rgba(26,34,53,0.85)",
        borderpad=4,
    )

    fig.update_layout(
        template="plotly_dark",
        height=360,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=20, b=20, l=10, r=10),
        xaxis=dict(
            title="Year",
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(color=C_TEXT2, size=11),
            dtick=2,
        ),
        yaxis=dict(
            title="Share of Global Trade Financing (%)",
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
            tickfont=dict(color=C_TEXT2, size=11),
            ticksuffix="%",
            range=[0, 80],
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="center", x=0.5,
            font=dict(size=11),
            bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
        font=dict(family="Inter, sans-serif"),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "Sources: ICC Banking Commission · BIS Payment Statistics · McKinsey Global"
        " Payments Report · SWIFT Trade Finance Activity.  2025-2026 = projections."
    )

    st.info(
        "Shipping implication: Rising open-account share indicates maturing trade"
        " relationships and trust. However, it also shifts risk from banks to"
        " exporters — when trade finance conditions tighten, open-account deals"
        " are more likely to be cancelled abruptly, creating sharp short-term"
        " shipping demand volatility vs. the stability of L/C-backed shipments."
    )


# ---------------------------------------------------------------------------
# Section 5 — De-dollarization Monitor
# ---------------------------------------------------------------------------

_DEDOLLAR_DATA: dict = {
    "year":     [2015, 2017, 2019, 2021, 2022, 2023, 2024, 2025, 2026],
    "usd_pct":  [85.4, 84.9, 83.8, 82.5, 81.2, 80.6, 80.1, 79.8, 79.2],
    "eur_pct":  [ 6.0,  6.1,  6.4,  6.8,  6.9,  7.0,  7.1,  7.2,  7.2],
    "cny_pct":  [ 1.5,  1.8,  2.1,  2.7,  3.1,  3.7,  4.2,  4.6,  5.1],
    "other_pct":[ 7.1,  7.2,  7.7,  8.0,  8.8,  8.7,  8.6,  8.4,  8.5],
}


def _render_dedollarization() -> None:
    section_header(
        "De-dollarization Monitor",
        "% of global trade settled in USD declining from 85% — CNY growing via"
        " China bilateral agreements — impacts freight pricing benchmarks",
    )

    years = _DEDOLLAR_DATA["year"]
    usd   = _DEDOLLAR_DATA["usd_pct"]
    eur   = _DEDOLLAR_DATA["eur_pct"]
    cny   = _DEDOLLAR_DATA["cny_pct"]
    other = _DEDOLLAR_DATA["other_pct"]

    fig = make_subplots(
        rows=1, cols=2,
        column_widths=[0.6, 0.4],
        subplot_titles=["Trade Settlement Currency Share (% global)", "2026 Currency Share"],
    )

    # Left: stacked area
    for y_vals, name, color in [
        (usd,   "USD",   "#3b82f6"),
        (eur,   "EUR",   "#10b981"),
        (cny,   "CNY",   "#f59e0b"),
        (other, "Other", "#64748b"),
    ]:
        fig.add_trace(go.Scatter(
            x=years, y=y_vals,
            name=name,
            mode="lines",
            line=dict(color=color, width=2),
            stackgroup="one",
            hovertemplate=name + ": %{y:.1f}%<extra></extra>",
        ), row=1, col=1)

    # Right: donut for 2026
    latest = [usd[-1], eur[-1], cny[-1], other[-1]]
    labels_pie = ["USD", "EUR", "CNY", "Other"]
    colors_pie  = ["#3b82f6", "#10b981", "#f59e0b", "#64748b"]
    fig.add_trace(go.Pie(
        values=latest,
        labels=labels_pie,
        marker=dict(colors=colors_pie, line=dict(color="rgba(0,0,0,0.4)", width=1)),
        hole=0.55,
        textfont=dict(color=C_TEXT, size=11),
        hovertemplate="%{label}: %{value:.1f}%<extra></extra>",
        showlegend=False,
    ), row=1, col=2)

    # CNY annotation on line chart
    fig.add_annotation(
        x=2026, y=cny[-1],
        text="CNY " + str(cny[-1]) + "%",
        showarrow=True, arrowhead=2, arrowcolor="#f59e0b",
        ax=-55, ay=-25,
        font=dict(size=10, color="#f59e0b"),
        bgcolor="rgba(26,34,53,0.85)",
        borderpad=4,
        row=1, col=1,
    )

    fig.update_layout(
        template="plotly_dark",
        height=350,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=40, b=20, l=10, r=10),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="center", x=0.35,
            font=dict(size=11),
            bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
        font=dict(family="Inter, sans-serif"),
        annotations=[
            dict(
                text="Trade Settlement Currency Share (% global)",
                xref="paper", yref="paper",
                x=0.3, y=1.06, showarrow=False,
                font=dict(size=12, color=C_TEXT2),
            ),
            dict(
                text="2026 Currency Share",
                xref="paper", yref="paper",
                x=0.85, y=1.06, showarrow=False,
                font=dict(size=12, color=C_TEXT2),
            ),
        ],
    )
    fig.update_xaxes(
        gridcolor="rgba(255,255,255,0.05)",
        tickfont=dict(color=C_TEXT2, size=11),
        row=1, col=1,
    )
    fig.update_yaxes(
        title="Share (%)",
        gridcolor="rgba(255,255,255,0.05)",
        tickfont=dict(color=C_TEXT2, size=11),
        ticksuffix="%",
        row=1, col=1,
    )

    st.plotly_chart(fig, use_container_width=True)

    # Insight callout
    usd_drop = round(_DEDOLLAR_DATA["usd_pct"][0] - _DEDOLLAR_DATA["usd_pct"][-1], 1)
    cny_rise = round(_DEDOLLAR_DATA["cny_pct"][-1] - _DEDOLLAR_DATA["cny_pct"][0], 1)
    st.markdown(
        '<div style="background:' + C_CARD + '; border:1px solid ' + _rgba(C_MOD, 0.3)
        + '; border-radius:8px; padding:12px 16px; font-size:0.82rem; color:' + C_TEXT2
        + '; margin-top:4px">'
        '<strong style="color:' + C_MOD + '">De-dollarization Watch:</strong>&nbsp;'
        'USD trade-settlement share has fallen ~' + str(usd_drop)
        + 'pp since 2015. CNY has gained ' + str(cny_rise)
        + 'pp primarily through China bilateral payment agreements (CIPS network,'
        ' petroyuan pricing, Belt and Road settlements). '
        'If USD weakens as the dominant trade currency, USD-denominated freight'
        ' benchmarks (BDI, FBX) may decouple from actual trade volumes, complicating'
        ' rate forecasting for trans-Pacific and Asia-Europe routes.'
        '</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 6 — Sanctions Impact Tracker
# ---------------------------------------------------------------------------

_SANCTIONS_DATA: list[dict] = [
    {
        "jurisdiction": "Russia",
        "mechanism": "SWIFT exclusion · SDN asset freeze · EU import bans",
        "shipping_impact": (
            "Major Russian ports (Novorossiysk, St. Petersburg, Vladivostok) face"
            " vessel withdrawal by western carriers. Trans-Atlantic and Baltic routes"
            " have absorbed ~5% diverted commodity volumes rerouted via Turkey/India."
        ),
        "diverted_vol_pct": 5.0,
        "affected_routes": ["BSEA_TRANSIT", "EUROPE_RUSSIA", "ARCTIC_ROUTE"],
        "severity": "CRITICAL",
        "in_force_since": "2022-02-28",
    },
    {
        "jurisdiction": "Iran",
        "mechanism": "US OFAC secondary sanctions · SWIFT exclusion (Iranian banks)",
        "shipping_impact": (
            "Iranian oil tankers use shadow fleet / flag-of-convenience vessels."
            " Strait of Hormuz insurance premiums elevated. Gulf carriers avoid"
            " Iranian port calls. Estimate 0.8M bbl/day oil trade rerouted."
        ),
        "diverted_vol_pct": 2.5,
        "affected_routes": ["HORMUZ_TRANSIT", "MIDEAST_GULF", "INDIA_WEST"],
        "severity": "HIGH",
        "in_force_since": "2018-11-05",
    },
    {
        "jurisdiction": "Cuba",
        "mechanism": "US embargo · OFAC vessel/port restrictions",
        "shipping_impact": (
            "Minimal direct shipping impact — Cuba trades primarily with China,"
            " Russia, and EU via non-US carriers. US-flagged vessels and those"
            " calling Cuban ports face OFAC 180-day bar on US port entry."
        ),
        "diverted_vol_pct": 0.2,
        "affected_routes": ["CARIB_WEST"],
        "severity": "LOW",
        "in_force_since": "1962-02-07",
    },
    {
        "jurisdiction": "Venezuela",
        "mechanism": "US OFAC oil sector sanctions · secondary sanctions on financiers",
        "shipping_impact": (
            "Venezuelan crude export volumes suppressed; tanker operators face"
            " secondary sanction risk. PDVSA cargoes handled via shadow fleet."
            " Minimal mainstream container shipping impact."
        ),
        "diverted_vol_pct": 0.4,
        "affected_routes": ["CARIB_WEST", "LATAM_NORTH"],
        "severity": "MODERATE",
        "in_force_since": "2019-01-28",
    },
    {
        "jurisdiction": "Belarus",
        "mechanism": "EU/US/UK sanctions following 2020 election · partial SWIFT",
        "shipping_impact": (
            "Belarusian potash and fertiliser export routes through Lithuanian/Latvian"
            " ports blocked; rerouted via Russian ports. Rail and road freight impacted."
        ),
        "diverted_vol_pct": 1.2,
        "affected_routes": ["EUROPE_RUSSIA", "BSEA_TRANSIT"],
        "severity": "MODERATE",
        "in_force_since": "2021-06-21",
    },
]

_SEVERITY_COLOR: dict[str, str] = {
    "CRITICAL": "#ef4444",
    "HIGH":     "#f97316",
    "MODERATE": "#f59e0b",
    "LOW":      "#64748b",
}


def _render_sanctions_tracker() -> None:
    section_header(
        "Financial Sanctions Impact Tracker",
        "Active sanctions regimes affecting shipping routes via financial channel"
        " restrictions (SWIFT, OFAC, SDN, correspondent banking cutoffs)",
    )

    total_diverted = sum(s["diverted_vol_pct"] for s in _SANCTIONS_DATA)
    critical_count = sum(1 for s in _SANCTIONS_DATA if s["severity"] == "CRITICAL")
    high_count     = sum(1 for s in _SANCTIONS_DATA if s["severity"] == "HIGH")

    # Summary KPI row
    k1, k2, k3 = st.columns(3)
    with k1:
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER
            + '; border-top:2px solid #ef4444; border-radius:10px; padding:14px;'
            ' text-align:center">'
            '<div style="font-size:0.62rem; font-weight:700; color:' + C_TEXT3
            + '; text-transform:uppercase; letter-spacing:0.07em">Active Regimes</div>'
            '<div style="font-size:2rem; font-weight:800; color:#ef4444">'
            + str(len(_SANCTIONS_DATA)) + '</div>'
            '<div style="font-size:0.70rem; color:' + C_TEXT3
            + '">' + str(critical_count) + ' Critical · ' + str(high_count) + ' High</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    with k2:
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER
            + '; border-top:2px solid ' + C_MOD
            + '; border-radius:10px; padding:14px; text-align:center">'
            '<div style="font-size:0.62rem; font-weight:700; color:' + C_TEXT3
            + '; text-transform:uppercase; letter-spacing:0.07em">Diverted Trade Volume</div>'
            '<div style="font-size:2rem; font-weight:800; color:' + C_MOD + '">'
            + str(round(total_diverted, 1)) + '%</div>'
            '<div style="font-size:0.70rem; color:' + C_TEXT3
            + '">of affected route volumes rerouted</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    with k3:
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER
            + '; border-top:2px solid ' + C_ACCENT
            + '; border-radius:10px; padding:14px; text-align:center">'
            '<div style="font-size:0.62rem; font-weight:700; color:' + C_TEXT3
            + '; text-transform:uppercase; letter-spacing:0.07em">Longest Running</div>'
            '<div style="font-size:2rem; font-weight:800; color:' + C_ACCENT + '">64y</div>'
            '<div style="font-size:0.70rem; color:' + C_TEXT3 + '">Cuba (since 1962)</div>'
            '</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # Per-jurisdiction cards
    for sanction in _SANCTIONS_DATA:
        sev = sanction["severity"]
        sev_color = _SEVERITY_COLOR.get(sev, C_TEXT3)
        routes_str = " · ".join(sanction["affected_routes"])
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid '
            + _rgba(sev_color, 0.3)
            + '; border-left:4px solid ' + sev_color
            + '; border-radius:10px; padding:14px 16px; margin-bottom:10px">'
            # header row
            '<div style="display:flex; justify-content:space-between;'
            ' align-items:flex-start; margin-bottom:8px">'
            '<div>'
            '<span style="font-size:1rem; font-weight:700; color:' + C_TEXT
            + '">' + sanction["jurisdiction"] + '</span>&nbsp;&nbsp;'
            '<span style="background:' + _rgba(sev_color, 0.15)
            + '; color:' + sev_color
            + '; border:1px solid ' + _rgba(sev_color, 0.3)
            + '; padding:2px 8px; border-radius:999px; font-size:0.65rem;'
            ' font-weight:700">' + sev + '</span>'
            '</div>'
            '<div style="font-size:0.68rem; color:' + C_TEXT3
            + '">Since ' + sanction["in_force_since"] + '</div>'
            '</div>'
            # mechanism
            '<div style="font-size:0.73rem; color:' + C_TEXT2
            + '; margin-bottom:6px">'
            '<strong>Mechanism:</strong> ' + sanction["mechanism"] + '</div>'
            # impact
            '<div style="font-size:0.73rem; color:' + C_TEXT2
            + '; margin-bottom:8px">' + sanction["shipping_impact"] + '</div>'
            # routes + diverted vol
            '<div style="display:flex; gap:16px; flex-wrap:wrap">'
            '<div style="font-size:0.65rem; color:' + C_TEXT3
            + '"><strong>Routes:</strong> ' + routes_str + '</div>'
            '<div style="font-size:0.65rem; color:' + sev_color
            + '"><strong>Diverted vol:</strong> '
            + str(sanction["diverted_vol_pct"]) + '%</div>'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(
    macro_data: dict | None,
    freight_data: dict | None = None,
    route_results: list | None = None,
) -> None:
    """Render the Trade Finance Dashboard tab.

    Parameters
    ----------
    macro_data:
        Dict mapping FRED series_id -> pd.DataFrame with 'date' and 'value'
        columns. DGS10 is used for the rate impact model if present.
    freight_data:
        Optional dict of freight-rate DataFrames (accepted for API consistency;
        not directly consumed by this tab).
    route_results:
        Optional list of route analysis result dicts (accepted for API
        consistency; not directly consumed by this tab).
    """
    macro_data = macro_data or {}
    n_loaded = len(macro_data)
    logger.info(
        "tab_finance: rendering with {n} FRED series, freight_data={fd},"
        " route_results={rr}",
        n=n_loaded,
        fd=freight_data is not None,
        rr=route_results is not None,
    )

    # ── Load processing layer ────────────────────────────────────────────────
    indicators = build_trade_finance_indicators()
    risk_scores = compute_regional_finance_risk()

    # ── Section 1: Trade Finance Health Dashboard ────────────────────────────
    _render_finance_health(indicators)

    _hr()

    # ── Section 2: Interest Rate Impact Model ────────────────────────────────
    _render_rate_impact(macro_data)

    _hr()

    # ── Section 3: Credit Availability Map ──────────────────────────────────
    _render_credit_map(risk_scores)

    _hr()

    # ── Section 4: L/C vs Open Account Trend ────────────────────────────────
    _render_lc_oa_trend()

    _hr()

    # ── Section 5: De-dollarization Monitor ─────────────────────────────────
    _render_dedollarization()

    _hr()

    # ── Section 6: Sanctions Impact Tracker ─────────────────────────────────
    _render_sanctions_tracker()
