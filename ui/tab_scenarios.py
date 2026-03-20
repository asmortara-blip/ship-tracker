"""
What-If Scenario Analysis Tab
Renders the interactive scenario modeler inside the Streamlit app.
"""
from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go

from processing.scenario_analyzer import (
    PREDEFINED_SCENARIOS,
    ScenarioInput,
    run_scenario,
    run_all_scenarios,
)


# ── Risk-level colour map ──────────────────────────────────────────────────────

_RISK_COLORS = {
    "LOW": "#10b981",
    "MODERATE": "#f59e0b",
    "HIGH": "#ef4444",
    "SEVERE": "#dc2626",
}


def _score_bar(score: float, color: str = "#3b82f6", width: int = 120) -> str:
    """Render a tiny inline progress bar as HTML."""
    pct = int(score * 100)
    return (
        "<div style='display:inline-block; vertical-align:middle; width:"
        + str(width)
        + "px; background:rgba(255,255,255,0.08); border-radius:4px; height:8px; overflow:hidden'>"
        + "<div style='width:"
        + str(pct)
        + "%; height:100%; background:"
        + color
        + "; border-radius:4px'></div></div>"
        + " <span style='font-size:0.72rem; color:#94a3b8; margin-left:4px'>"
        + str(pct)
        + "%</span>"
    )


def _delta_arrow(delta: float) -> str:
    if delta > 0.005:
        return "<span style='color:#10b981; font-weight:700'>▲ " + "{:+.1%}".format(delta) + "</span>"
    if delta < -0.005:
        return "<span style='color:#ef4444; font-weight:700'>▼ " + "{:.1%}".format(delta) + "</span>"
    return "<span style='color:#94a3b8'>— " + "{:+.1%}".format(delta) + "</span>"


def render(port_results: list, route_results: list, macro_data: dict) -> None:
    """Render the What-If Scenario Analysis tab."""

    # ── Section 1: Header ─────────────────────────────────────────────────────
    st.markdown("""
    <div style="padding: 16px 0 24px 0; border-bottom: 1px solid rgba(255,255,255,0.06); margin-bottom: 24px">
        <div style="font-size:0.68rem; text-transform:uppercase; letter-spacing:0.15em;
                    color:#475569; margin-bottom:6px">
            WHAT-IF ANALYSIS
        </div>
        <div style="font-size:1.6rem; font-weight:900; color:#f1f5f9; letter-spacing:-0.03em; line-height:1.1">
            Scenario Analysis
        </div>
        <div style="font-size:0.85rem; color:#64748b; margin-top:6px">
            Model the impact of geopolitical, economic, and seasonal events on shipping markets
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Section 2: Scenario Selector ──────────────────────────────────────────
    preset_names = [s.name for s in PREDEFINED_SCENARIOS] + ["Custom"]
    col_left, col_right = st.columns([1.4, 1])

    with col_left:
        st.markdown(
            "<div style='font-size:0.75rem; text-transform:uppercase; "
            "letter-spacing:0.1em; color:#64748b; margin-bottom:8px'>Select Scenario</div>",
            unsafe_allow_html=True,
        )
        selected_name = st.radio(
            "scenario_radio",
            preset_names,
            label_visibility="collapsed",
            key="scenario_selector",
        )

    with col_right:
        # Description card for selected preset (not shown for Custom here — handled below)
        if selected_name != "Custom":
            preset = next(s for s in PREDEFINED_SCENARIOS if s.name == selected_name)
            badges: list[str] = []
            if preset.suez_closed:
                badges.append(
                    "<span style='background:rgba(239,68,68,0.15); color:#ef4444; "
                    "border:1px solid rgba(239,68,68,0.3); padding:2px 8px; "
                    "border-radius:999px; font-size:0.68rem; font-weight:600'>Suez Closed</span>"
                )
            if preset.panama_closed:
                badges.append(
                    "<span style='background:rgba(239,68,68,0.15); color:#ef4444; "
                    "border:1px solid rgba(239,68,68,0.3); padding:2px 8px; "
                    "border-radius:999px; font-size:0.68rem; font-weight:600'>Panama Closed</span>"
                )
            if preset.bdi_shock != 0.0:
                col = "#10b981" if preset.bdi_shock > 0 else "#ef4444"
                badges.append(
                    "<span style='background:rgba(59,130,246,0.15); color:"
                    + col
                    + "; border:1px solid rgba(59,130,246,0.3); padding:2px 8px; "
                    "border-radius:999px; font-size:0.68rem; font-weight:600'>BDI "
                    + "{:+.0%}".format(preset.bdi_shock)
                    + "</span>"
                )
            if preset.demand_shock != 0.0:
                col = "#10b981" if preset.demand_shock > 0 else "#ef4444"
                badges.append(
                    "<span style='background:rgba(16,185,129,0.1); color:"
                    + col
                    + "; border:1px solid rgba(16,185,129,0.3); padding:2px 8px; "
                    "border-radius:999px; font-size:0.68rem; font-weight:600'>Demand "
                    + "{:+.0%}".format(preset.demand_shock)
                    + "</span>"
                )
            if preset.fuel_shock != 0.0:
                badges.append(
                    "<span style='background:rgba(245,158,11,0.15); color:#f59e0b; "
                    "border:1px solid rgba(245,158,11,0.3); padding:2px 8px; "
                    "border-radius:999px; font-size:0.68rem; font-weight:600'>Fuel "
                    + "{:+.0%}".format(preset.fuel_shock)
                    + "</span>"
                )
            if preset.us_china_tariff_hike != 0.0:
                badges.append(
                    "<span style='background:rgba(168,85,247,0.15); color:#a855f7; "
                    "border:1px solid rgba(168,85,247,0.3); padding:2px 8px; "
                    "border-radius:999px; font-size:0.68rem; font-weight:600'>Tariff +"
                    + "{:.0%}".format(preset.us_china_tariff_hike)
                    + "</span>"
                )

            badges_html = " ".join(badges) if badges else ""
            st.markdown(
                "<div style='background:#1a2235; border:1px solid rgba(255,255,255,0.08); "
                "border-radius:10px; padding:16px 18px; margin-top:4px'>"
                "<div style='font-size:0.82rem; font-weight:700; color:#f1f5f9; "
                "margin-bottom:8px'>"
                + preset.name
                + "</div>"
                "<div style='font-size:0.78rem; color:#94a3b8; line-height:1.5; "
                "margin-bottom:10px'>"
                + preset.description
                + "</div>"
                "<div style='display:flex; flex-wrap:wrap; gap:6px'>"
                + badges_html
                + "</div>"
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<div style='background:#1a2235; border:1px solid rgba(255,255,255,0.08); "
                "border-radius:10px; padding:16px 18px; margin-top:4px'>"
                "<div style='font-size:0.82rem; font-weight:700; color:#f1f5f9; "
                "margin-bottom:8px'>Custom Scenario</div>"
                "<div style='font-size:0.78rem; color:#94a3b8; line-height:1.5'>Configure "
                "your own shock parameters using the sliders below.</div>"
                "</div>",
                unsafe_allow_html=True,
            )

    # ── Section 3: Custom Sliders ─────────────────────────────────────────────
    if selected_name == "Custom":
        st.markdown(
            "<div style='font-size:0.75rem; text-transform:uppercase; "
            "letter-spacing:0.1em; color:#64748b; margin-top:20px; margin-bottom:12px'>"
            "Configure Custom Scenario</div>",
            unsafe_allow_html=True,
        )
        sl1, sl2, sl3 = st.columns(3)
        with sl1:
            bdi_shock = st.slider("BDI Shock", -0.50, 2.00, 0.0, 0.05, format="%.0f%%",
                                  key="cust_bdi")
            fuel_shock = st.slider("Fuel Change", -0.30, 1.00, 0.0, 0.05, format="%.0f%%",
                                   key="cust_fuel")
        with sl2:
            pmi_shock = st.slider("PMI Change", -15.0, 15.0, 0.0, 0.5, format="%.1f pts",
                                  key="cust_pmi")
            tariff_hike = st.slider("US-China Tariff", 0.0, 0.50, 0.0, 0.05, format="%.0f%%",
                                    key="cust_tariff")
        with sl3:
            demand_shock = st.slider("Demand Shock", -0.40, 0.40, 0.0, 0.05, format="%.0f%%",
                                     key="cust_demand")
            suez_closed = st.checkbox("Suez Canal Closed", key="cust_suez")
            panama_closed = st.checkbox("Panama Canal Closed", key="cust_panama")

        active_scenario = ScenarioInput(
            name="Custom Scenario",
            bdi_shock=bdi_shock,
            fuel_shock=fuel_shock,
            pmi_shock=pmi_shock,
            suez_closed=suez_closed,
            panama_closed=panama_closed,
            us_china_tariff_hike=tariff_hike,
            demand_shock=demand_shock,
            description="User-defined scenario.",
        )
    else:
        active_scenario = next(s for s in PREDEFINED_SCENARIOS if s.name == selected_name)

    # ── Section 4: Results ────────────────────────────────────────────────────
    st.divider()

    if not route_results:
        st.info("Route results unavailable — cannot run scenario analysis.")
        return

    result = run_scenario(active_scenario, port_results, route_results)

    if not result.route_impacts and not result.port_impacts:
        st.warning("Simulation returned no results — try adjusting parameters")
        return

    delta = result.opportunity_delta
    risk = result.risk_level
    risk_color = _RISK_COLORS.get(risk, "#94a3b8")

    # Big delta card
    delta_sign = "+" if delta >= 0 else ""
    delta_color = "#10b981" if delta >= 0 else "#ef4444"
    delta_bg = "rgba(16,185,129,0.08)" if delta >= 0 else "rgba(239,68,68,0.08)"

    # Risk badge animation for SEVERE
    pulse_style = (
        "animation: pulse-severe 1.2s ease-in-out infinite;"
        if risk == "SEVERE"
        else ""
    )

    st.markdown(
        """
<style>
@keyframes pulse-severe {
    0%   { opacity: 1; }
    50%  { opacity: 0.5; }
    100% { opacity: 1; }
}
</style>
""",
        unsafe_allow_html=True,
    )

    c_delta, c_risk, c_summary = st.columns([1, 1, 2])

    with c_delta:
        st.markdown(
            "<div style='background:"
            + delta_bg
            + "; border:1px solid "
            + delta_color
            + "33; border-radius:12px; padding:20px 16px; text-align:center'>"
            "<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.12em; "
            "color:#64748b; margin-bottom:8px'>Route Opportunity Impact</div>"
            "<div style='font-size:2.4rem; font-weight:900; color:"
            + delta_color
            + "; line-height:1'>"
            + delta_sign
            + "{:.0%}".format(delta)
            + "</div>"
            "<div style='font-size:0.72rem; color:#64748b; margin-top:6px'>"
            "vs. baseline avg</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    with c_risk:
        st.markdown(
            "<div style='background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); "
            "border-radius:12px; padding:20px 16px; text-align:center'>"
            "<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.12em; "
            "color:#64748b; margin-bottom:12px'>Risk Level</div>"
            "<div style='"
            + pulse_style
            + " background:rgba("
            + ("16,185,129" if risk == "LOW" else ("245,158,11" if risk == "MODERATE" else "239,68,68"))
            + ",0.15); color:"
            + risk_color
            + "; border:1px solid "
            + risk_color
            + "55; display:inline-block; padding:8px 20px; border-radius:999px; "
            "font-size:1rem; font-weight:800; letter-spacing:0.06em'>"
            + risk
            + "</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    with c_summary:
        st.markdown(
            "<div style='background:rgba(59,130,246,0.05); border-left:3px solid #3b82f6; "
            "border-radius:0 10px 10px 0; padding:16px 18px; height:100%; box-sizing:border-box'>"
            "<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.12em; "
            "color:#3b82f6; margin-bottom:8px; font-weight:700'>Scenario Summary</div>"
            "<div style='font-size:0.82rem; color:#cbd5e1; line-height:1.6'>"
            + result.summary
            + "</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='margin-top:24px'></div>", unsafe_allow_html=True)

    # Route impact table
    rt_col, pt_col = st.columns(2)

    with rt_col:
        st.markdown(
            "<div style='font-size:0.75rem; text-transform:uppercase; letter-spacing:0.1em; "
            "color:#64748b; margin-bottom:10px; font-weight:700'>Route Impacts</div>",
            unsafe_allow_html=True,
        )
        table_html = (
            "<table style='width:100%; border-collapse:collapse; font-size:0.75rem; color:#cbd5e1'>"
            "<thead><tr>"
            "<th style='text-align:left; padding:6px 8px; color:#64748b; font-weight:600; "
            "border-bottom:1px solid rgba(255,255,255,0.06)'>Route</th>"
            "<th style='text-align:left; padding:6px 8px; color:#64748b; font-weight:600; "
            "border-bottom:1px solid rgba(255,255,255,0.06)'>Baseline</th>"
            "<th style='text-align:left; padding:6px 8px; color:#64748b; font-weight:600; "
            "border-bottom:1px solid rgba(255,255,255,0.06)'>Scenario</th>"
            "<th style='text-align:right; padding:6px 8px; color:#64748b; font-weight:600; "
            "border-bottom:1px solid rgba(255,255,255,0.06)'>Delta</th>"
            "</tr></thead><tbody>"
        )
        sorted_routes = sorted(result.route_impacts, key=lambda x: abs(x["delta"]), reverse=True)
        for ri in sorted_routes[:10]:
            sc_color = "#10b981" if ri["scenario_score"] >= 0.65 else (
                "#f59e0b" if ri["scenario_score"] >= 0.45 else "#ef4444"
            )
            table_html += (
                "<tr style='border-bottom:1px solid rgba(255,255,255,0.03)'>"
                "<td style='padding:7px 8px; color:#f1f5f9; font-weight:500'>"
                + ri["route_name"][:28]
                + "</td>"
                "<td style='padding:7px 8px'>"
                + _score_bar(ri["baseline"], "#475569", 80)
                + "</td>"
                "<td style='padding:7px 8px'>"
                + _score_bar(ri["scenario_score"], sc_color, 80)
                + "</td>"
                "<td style='padding:7px 8px; text-align:right'>"
                + _delta_arrow(ri["delta"])
                + "</td>"
                "</tr>"
            )
        table_html += "</tbody></table>"
        st.markdown(table_html, unsafe_allow_html=True)

    with pt_col:
        st.markdown(
            "<div style='font-size:0.75rem; text-transform:uppercase; letter-spacing:0.1em; "
            "color:#64748b; margin-bottom:10px; font-weight:700'>Port Demand Impacts</div>",
            unsafe_allow_html=True,
        )
        ptable_html = (
            "<table style='width:100%; border-collapse:collapse; font-size:0.75rem; color:#cbd5e1'>"
            "<thead><tr>"
            "<th style='text-align:left; padding:6px 8px; color:#64748b; font-weight:600; "
            "border-bottom:1px solid rgba(255,255,255,0.06)'>Port</th>"
            "<th style='text-align:left; padding:6px 8px; color:#64748b; font-weight:600; "
            "border-bottom:1px solid rgba(255,255,255,0.06)'>Baseline</th>"
            "<th style='text-align:left; padding:6px 8px; color:#64748b; font-weight:600; "
            "border-bottom:1px solid rgba(255,255,255,0.06)'>Scenario</th>"
            "<th style='text-align:right; padding:6px 8px; color:#64748b; font-weight:600; "
            "border-bottom:1px solid rgba(255,255,255,0.06)'>Delta</th>"
            "</tr></thead><tbody>"
        )
        sorted_ports = sorted(result.port_impacts, key=lambda x: abs(x["delta"]), reverse=True)
        for pi in sorted_ports[:10]:
            sc_color = "#10b981" if pi["scenario_score"] >= 0.65 else (
                "#f59e0b" if pi["scenario_score"] >= 0.45 else "#ef4444"
            )
            ptable_html += (
                "<tr style='border-bottom:1px solid rgba(255,255,255,0.03)'>"
                "<td style='padding:7px 8px; color:#f1f5f9; font-weight:500'>"
                + pi["port_name"][:22]
                + "</td>"
                "<td style='padding:7px 8px'>"
                + _score_bar(pi["baseline"], "#475569", 70)
                + "</td>"
                "<td style='padding:7px 8px'>"
                + _score_bar(pi["scenario_score"], sc_color, 70)
                + "</td>"
                "<td style='padding:7px 8px; text-align:right'>"
                + _delta_arrow(pi["delta"])
                + "</td>"
                "</tr>"
            )
        ptable_html += "</tbody></table>"
        st.markdown(ptable_html, unsafe_allow_html=True)

    # ── Section 5: All-Scenario Comparison Chart ──────────────────────────────
    st.markdown("<div style='margin-top:32px'></div>", unsafe_allow_html=True)
    st.markdown(
        "<div style='font-size:0.75rem; text-transform:uppercase; letter-spacing:0.1em; "
        "color:#64748b; margin-bottom:14px; font-weight:700'>All Scenarios — Opportunity Impact</div>",
        unsafe_allow_html=True,
    )

    all_results = run_all_scenarios(port_results, route_results)

    if not all_results:
        st.warning("Simulation returned no results — try adjusting parameters")
        return

    names = [r.scenario.name for r in all_results]
    deltas = [r.opportunity_delta for r in all_results]
    bar_colors = ["#10b981" if d >= 0 else "#ef4444" for d in deltas]

    fig = go.Figure(
        go.Bar(
            x=deltas,
            y=names,
            orientation="h",
            marker_color=bar_colors,
            marker_line_width=0,
            text=[("{:+.1%}".format(d)) for d in deltas],
            textposition="outside",
            textfont=dict(size=11, color="#94a3b8"),
            hovertemplate="<b>%{y}</b><br>Delta: %{x:.1%}<extra></extra>",
        )
    )
    fig.add_vline(x=0, line_color="rgba(255,255,255,0.15)", line_width=1)

    fig.update_layout(
        template="plotly_dark",
        height=400,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#94a3b8", size=12),
        margin=dict(l=10, r=80, t=20, b=40),
        xaxis=dict(
            range=[-0.5, 0.5],
            tickformat=".0%",
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
        ),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.0)",
            automargin=True,
        ),
        showlegend=False,
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key="scenarios_all_bar_chart")
