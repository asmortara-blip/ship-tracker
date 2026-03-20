"""
Maritime Chokepoints Tab

Visualises the world's 9 critical maritime chokepoints and their current
disruption status.  Sections:

  1. Chokepoint World Map  — Scattergeo with pulsing markers for CRITICAL/HIGH
  2. Risk Dashboard        — 3x3 grid of chokepoint cards
  3. Closure Impact Simulator — interactive selectbox + slider
  4. Red Sea Crisis Tracker   — dedicated Houthi / Red Sea disruption section
  5. Historical Events Timeline — annotated scatter 2000-2026
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.chokepoint_analyzer import (
    CHOKEPOINTS,
    Chokepoint,
    compute_chokepoint_risk_score,
    get_current_active_disruptions,
    risk_color,
    simulate_chokepoint_closure,
)


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

C_BG      = "#0a0f1a"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"
C_GREEN   = "#10b981"
C_BLUE    = "#3b82f6"
C_WARN    = "#f59e0b"
C_ORANGE  = "#f97316"
C_RED     = "#ef4444"
C_CRIMSON = "#dc2626"


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _section_header(title: str, subtitle: str = "") -> None:
    sub = (
        "<div style='font-size:0.83rem; color:" + C_TEXT2 + "; margin-top:4px'>"
        + subtitle + "</div>"
        if subtitle else ""
    )
    st.markdown(
        "<div style='margin:28px 0 16px 0'>"
        "<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.14em;"
        " color:" + C_TEXT3 + "; margin-bottom:5px'>MARITIME CHOKEPOINTS</div>"
        "<div style='font-size:1.25rem; font-weight:800; color:" + C_TEXT + "; "
        "letter-spacing:-0.02em'>" + title + "</div>"
        + sub +
        "</div>",
        unsafe_allow_html=True,
    )


def _risk_badge(level: str, pulse: bool = False) -> str:
    color = risk_color(level)
    pulse_style = "animation:chokepoint-pulse 1.3s ease-in-out infinite;" if pulse else ""
    bg_map = {
        "CRITICAL": "rgba(239,68,68,0.18)",
        "HIGH":     "rgba(249,115,22,0.18)",
        "MODERATE": "rgba(245,158,11,0.15)",
        "LOW":      "rgba(16,185,129,0.13)",
    }
    bg = bg_map.get(level, "rgba(148,163,184,0.12)")
    return (
        "<span style='"
        + pulse_style
        + "display:inline-block; background:" + bg + "; color:" + color + ";"
        " border:1px solid " + color + "55; padding:2px 10px; border-radius:999px;"
        " font-size:0.68rem; font-weight:700; letter-spacing:0.06em'>"
        + level + "</span>"
    )


def _disruption_badge(dtype: str) -> str:
    color_map = {
        "ACTIVE_CONFLICT": C_RED,
        "DIPLOMATIC":      C_ORANGE,
        "WEATHER":         C_WARN,
        "CONGESTION":      C_BLUE,
        "NONE":            C_TEXT3,
    }
    label_map = {
        "ACTIVE_CONFLICT": "ACTIVE CONFLICT",
        "DIPLOMATIC":      "DIPLOMATIC",
        "WEATHER":         "WEATHER",
        "CONGESTION":      "CONGESTION",
        "NONE":            "NORMAL",
    }
    c = color_map.get(dtype, C_TEXT3)
    label = label_map.get(dtype, dtype)
    return (
        "<span style='display:inline-block; background:" + c + "22; color:" + c + ";"
        " border:1px solid " + c + "44; padding:1px 8px; border-radius:999px;"
        " font-size:0.64rem; font-weight:600; letter-spacing:0.05em; margin-left:6px'>"
        + label + "</span>"
    )


# ---------------------------------------------------------------------------
# Section 1: World Map
# ---------------------------------------------------------------------------

def _render_world_map(risk_scores: dict[str, float]) -> None:
    _section_header(
        "Global Chokepoint Map",
        "All 9 critical maritime passages — size = daily TEU throughput, "
        "color = current risk level",
    )

    # Build marker data
    lats, lons, names_list, sizes, colors_list, hovers = [], [], [], [], [], []
    active_lats, active_lons, active_sizes = [], [], []

    for key, cp in CHOKEPOINTS.items():
        lats.append(cp.lat)
        lons.append(cp.lon)
        names_list.append(cp.name)
        # Scale marker size: 0.08 TEU/day => 8px, 1.20 => 36px
        sz = max(10, min(42, int(cp.daily_teu_m * 28 + 8)))
        sizes.append(sz)
        colors_list.append(risk_color(cp.current_risk_level))
        alt_text = (
            ", ".join(cp.strategic_alternatives[:2]) if cp.strategic_alternatives
            else "None — critical vulnerability"
        )
        hovers.append(
            "<b>" + cp.name + "</b><br>"
            + "Risk: <b>" + cp.current_risk_level + "</b><br>"
            + "Daily vessels: " + str(cp.daily_vessels) + "<br>"
            + "Daily TEU: " + str(cp.daily_teu_m) + "M<br>"
            + "% Global trade: " + str(cp.pct_global_trade) + "%<br>"
            + "Disruption: " + cp.current_disruption_type + "<br>"
            + "Alternatives: " + alt_text
        )
        # Collect CRITICAL/HIGH chokepoints for pulse overlay
        if cp.current_risk_level in ("CRITICAL", "HIGH"):
            active_lats.append(cp.lat)
            active_lons.append(cp.lon)
            active_sizes.append(sz + 14)

    fig = go.Figure()

    # Outer pulse ring for active disruptions (3 layers)
    for ring_factor in (2.0, 1.5, 1.1):
        if active_lats:
            fig.add_trace(go.Scattergeo(
                lat=active_lats,
                lon=active_lons,
                mode="markers",
                marker=dict(
                    size=[int(s * ring_factor) for s in active_sizes],
                    color="rgba(239,68,68,0.0)",
                    line=dict(
                        color="rgba(239,68,68," + str(round(0.25 / ring_factor, 2)) + ")",
                        width=2,
                    ),
                ),
                hoverinfo="skip",
                showlegend=False,
            ))

    # Main chokepoint markers
    fig.add_trace(go.Scattergeo(
        lat=lats,
        lon=lons,
        mode="markers+text",
        marker=dict(
            size=sizes,
            color=colors_list,
            line=dict(color="rgba(255,255,255,0.25)", width=1.5),
            opacity=0.92,
        ),
        text=names_list,
        textposition="top center",
        textfont=dict(size=10, color="#f1f5f9"),
        hovertemplate="%{customdata}<extra></extra>",
        customdata=hovers,
        showlegend=False,
    ))

    # Legend traces (invisible points)
    for level, color in [("CRITICAL", C_RED), ("HIGH", C_ORANGE),
                         ("MODERATE", C_WARN), ("LOW", C_GREEN)]:
        fig.add_trace(go.Scattergeo(
            lat=[None], lon=[None],
            mode="markers",
            marker=dict(size=10, color=color, line=dict(color="rgba(255,255,255,0.3)", width=1)),
            name=level,
            showlegend=True,
        ))

    fig.update_layout(
        height=550,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        geo=dict(
            showframe=False,
            showcoastlines=True,
            coastlinecolor="rgba(148,163,184,0.3)",
            showland=True,
            landcolor="#0f1925",
            showocean=True,
            oceancolor="#070d18",
            showlakes=False,
            showrivers=False,
            showcountries=True,
            countrycolor="rgba(255,255,255,0.06)",
            projection_type="natural earth",
            bgcolor="rgba(0,0,0,0)",
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=0.01,
            xanchor="right",
            x=0.99,
            font=dict(color=C_TEXT2, size=11),
            bgcolor="rgba(10,15,26,0.7)",
        ),
        margin=dict(l=0, r=0, t=8, b=0),
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# Section 2: Risk Dashboard (3x3 grid of cards)
# ---------------------------------------------------------------------------

def _render_risk_dashboard() -> None:
    _section_header(
        "Chokepoint Risk Dashboard",
        "Current status of all 9 critical maritime passages",
    )

    cp_list = list(CHOKEPOINTS.values())
    rows = [cp_list[i:i + 3] for i in range(0, len(cp_list), 3)]

    for row in rows:
        cols = st.columns(3)
        for col, cp in zip(cols, row):
            with col:
                is_active = cp.current_disruption_type != "NONE"
                is_critical = cp.current_risk_level in ("CRITICAL", "HIGH")
                border_color = risk_color(cp.current_risk_level) + "55"
                alt_html: str
                if cp.strategic_alternatives:
                    alt_html = (
                        "<div style='font-size:0.68rem; color:" + C_TEXT3 + "; margin-top:6px'>"
                        + "Alt: " + cp.strategic_alternatives[0][:50]
                        + "</div>"
                    )
                else:
                    alt_html = (
                        "<div style='font-size:0.68rem; color:" + C_RED + "; margin-top:6px; "
                        "font-weight:600'>No viable alternative route</div>"
                    )

                disruption_html = ""
                if is_active:
                    dtype_labels = {
                        "ACTIVE_CONFLICT": "Active conflict disruption",
                        "DIPLOMATIC":      "Diplomatic tension",
                        "WEATHER":         "Weather constraints",
                        "CONGESTION":      "Traffic congestion",
                    }
                    since_html = (
                        " since " + cp.disruption_since if cp.disruption_since else ""
                    )
                    disruption_html = (
                        "<div style='background:rgba(239,68,68,0.07); border-left:2px solid "
                        + risk_color(cp.current_risk_level) + "; padding:6px 8px; "
                        "border-radius:0 6px 6px 0; margin-top:8px; font-size:0.70rem;"
                        " color:" + C_TEXT2 + "'>"
                        + dtype_labels.get(cp.current_disruption_type,
                                           cp.current_disruption_type)
                        + since_html
                        + "</div>"
                    )

                badge_html = _risk_badge(cp.current_risk_level, pulse=is_critical)
                disp_badge = _disruption_badge(cp.current_disruption_type)

                st.markdown(
                    "<div style='background:" + C_CARD + "; border:1px solid " + border_color + ";"
                    " border-radius:12px; padding:16px 15px; height:100%'>"
                    "<div style='font-size:0.8rem; font-weight:700; color:" + C_TEXT + ";"
                    " margin-bottom:8px'>" + cp.name + "</div>"
                    "<div>" + badge_html + disp_badge + "</div>"
                    "<div style='margin-top:10px; display:flex; gap:16px'>"
                    "<div style='font-size:0.72rem; color:" + C_TEXT3 + "'>"
                    "<div style='font-size:1.0rem; font-weight:800; color:" + C_TEXT + "'>"
                    + str(cp.daily_vessels) + "</div>vessels/day</div>"
                    "<div style='font-size:0.72rem; color:" + C_TEXT3 + "'>"
                    "<div style='font-size:1.0rem; font-weight:800; color:" + C_WARN + "'>"
                    + str(cp.pct_global_trade) + "%</div>global trade</div>"
                    "<div style='font-size:0.72rem; color:" + C_TEXT3 + "'>"
                    "<div style='font-size:1.0rem; font-weight:800; color:" + C_TEXT2 + "'>"
                    + str(cp.extra_days_if_closed) + "d</div>if closed</div>"
                    "</div>"
                    + disruption_html + alt_html +
                    "</div>",
                    unsafe_allow_html=True,
                )


# ---------------------------------------------------------------------------
# Section 3: Closure Impact Simulator
# ---------------------------------------------------------------------------

def _render_closure_simulator() -> None:
    _section_header(
        "Closure Impact Simulator",
        "Model the cascading effects of a chokepoint closure on global freight",
    )

    cp_names = [cp.name for cp in CHOKEPOINTS.values()]
    cp_keys = list(CHOKEPOINTS.keys())

    sim_col, ctrl_col = st.columns([2, 1])

    with ctrl_col:
        selected_name = st.selectbox(
            "Select chokepoint",
            cp_names,
            index=1,
            key="chk_sim_select",
        )
        duration_weeks = st.slider(
            "Closure duration (weeks)",
            min_value=1,
            max_value=52,
            value=4,
            step=1,
            key="chk_sim_duration",
        )

    # Find selected key
    selected_key = cp_keys[cp_names.index(selected_name)]
    result = simulate_chokepoint_closure(selected_key, duration_weeks)
    cp = CHOKEPOINTS[selected_key]

    with sim_col:
        # Impact summary cards
        mc1, mc2, mc3 = st.columns(3)
        with mc1:
            st.markdown(
                "<div style='background:" + C_CARD + "; border:1px solid " + C_BORDER + ";"
                " border-radius:10px; padding:14px; text-align:center'>"
                "<div style='font-size:0.65rem; text-transform:uppercase; letter-spacing:0.1em;"
                " color:" + C_TEXT3 + "; margin-bottom:6px'>Rate Spike</div>"
                "<div style='font-size:1.9rem; font-weight:900; color:" + C_RED + "'>"
                "+" + str(result["rate_impact_pct"]) + "%</div>"
                "<div style='font-size:0.68rem; color:" + C_TEXT3 + "; margin-top:4px'>"
                "spot freight estimate</div>"
                "</div>",
                unsafe_allow_html=True,
            )
        with mc2:
            st.markdown(
                "<div style='background:" + C_CARD + "; border:1px solid " + C_BORDER + ";"
                " border-radius:10px; padding:14px; text-align:center'>"
                "<div style='font-size:0.65rem; text-transform:uppercase; letter-spacing:0.1em;"
                " color:" + C_TEXT3 + "; margin-bottom:6px'>Trade Disrupted</div>"
                "<div style='font-size:1.9rem; font-weight:900; color:" + C_ORANGE + "'>"
                + str(result["global_trade_impact_pct"]) + "%</div>"
                "<div style='font-size:0.68rem; color:" + C_TEXT3 + "; margin-top:4px'>"
                "of global trade</div>"
                "</div>",
                unsafe_allow_html=True,
            )
        with mc3:
            reroute_m = result["rerouting_cost_total_usd"] / 1_000_000
            st.markdown(
                "<div style='background:" + C_CARD + "; border:1px solid " + C_BORDER + ";"
                " border-radius:10px; padding:14px; text-align:center'>"
                "<div style='font-size:0.65rem; text-transform:uppercase; letter-spacing:0.1em;"
                " color:" + C_TEXT3 + "; margin-bottom:6px'>Rerouting Cost</div>"
                "<div style='font-size:1.9rem; font-weight:900; color:" + C_WARN + "'>"
                "$" + "{:.0f}M".format(reroute_m) + "</div>"
                "<div style='font-size:0.68rem; color:" + C_TEXT3 + "; margin-top:4px'>"
                "cumulative est.</div>"
                "</div>",
                unsafe_allow_html=True,
            )

        # Feasibility note
        st.markdown(
            "<div style='background:rgba(59,130,246,0.05); border-left:3px solid " + C_BLUE + ";"
            " border-radius:0 8px 8px 0; padding:10px 14px; margin-top:12px;"
            " font-size:0.78rem; color:" + C_TEXT2 + "'>"
            + result["feasibility_note"]
            + "</div>",
            unsafe_allow_html=True,
        )

    # Affected routes visual
    affected = result.get("affected_routes", [])
    alts = result.get("alternative_routes", [])
    extra_days = result.get("extra_days_if_closed", 0)

    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)
    route_col, alt_col = st.columns(2)

    with route_col:
        st.markdown(
            "<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
            " color:" + C_TEXT3 + "; margin-bottom:8px; font-weight:700'>"
            "Affected Trade Lanes</div>",
            unsafe_allow_html=True,
        )
        if affected:
            rows_html = ""
            for r in affected:
                rows_html += (
                    "<div style='padding:5px 0; border-bottom:1px solid rgba(255,255,255,0.04);"
                    " font-size:0.76rem; color:" + C_RED + "; display:flex; align-items:center'>"
                    "<span style='margin-right:8px; font-size:0.9rem'>&#9679;</span>"
                    + r.replace("_", " ").title()
                    + "</div>"
                )
            st.markdown(rows_html, unsafe_allow_html=True)
        else:
            st.markdown(
                "<div style='color:" + C_TEXT3 + "; font-size:0.76rem'>No affected routes listed.</div>",
                unsafe_allow_html=True,
            )

    with alt_col:
        st.markdown(
            "<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
            " color:" + C_TEXT3 + "; margin-bottom:8px; font-weight:700'>"
            "Alternative Routing Options</div>",
            unsafe_allow_html=True,
        )
        if alts:
            for a in alts:
                st.markdown(
                    "<div style='padding:5px 0; border-bottom:1px solid rgba(255,255,255,0.04);"
                    " font-size:0.76rem; color:" + C_GREEN + "; display:flex; align-items:center'>"
                    "<span style='margin-right:8px'>&#8594;</span>" + a
                    + "</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                "<div style='color:" + C_RED + "; font-size:0.76rem; font-weight:700'>"
                "No viable alternative — this closure would be catastrophic."
                "</div>",
                unsafe_allow_html=True,
            )

        if extra_days:
            st.markdown(
                "<div style='font-size:0.72rem; color:" + C_TEXT3 + "; margin-top:6px'>"
                "Extra transit days via best alternative: "
                "<b style='color:" + C_TEXT + "'>" + str(extra_days) + " days</b></div>",
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Section 4: Red Sea Crisis Tracker
# ---------------------------------------------------------------------------

_RED_SEA_INCIDENTS = [
    ("2023-11-19", "Houthis seize Galaxy Leader cargo ship; first vessel seizure"),
    ("2023-12-09", "MSC Palatium III missile attack; carriers begin Red Sea avoidance"),
    ("2023-12-18", "Operation Prosperity Guardian announced by US-led coalition"),
    ("2024-01-09", "Largest Houthi drone swarm attack on commercial shipping to date"),
    ("2024-01-11", "US + UK first strike Houthi land-based targets in Yemen"),
    ("2024-01-12", "Maersk, Hapag-Lloyd, MSC all indefinitely suspend Red Sea transits"),
    ("2024-02-18", "UK-flagged Rubymar struck, sinks — first vessel sunk in crisis"),
    ("2024-03-06", "FBX03 Asia-Europe rate reaches 4-year high; +280% vs Oct 2023"),
    ("2024-05-14", "Houthis expand attacks to vessels with no Israel connection"),
    ("2024-09-21", "US carrier strike group rotation; Houthi attacks continue"),
    ("2025-01-19", "Gaza ceasefire; Houthis announce pause — rates begin softening"),
    ("2025-03-12", "Houthi attacks resume after ceasefire tensions"),
    ("2026-01-01", "Red Sea remains HIGH risk; ~70% of traffic still rerouting Cape"),
]

# Synthetic but realistic: Cape vs Suez vessel weekly counts (indexed Nov 2023 = week 0)
_CAPE_VESSELS = [
    12, 14, 18, 25, 38, 52, 68, 78, 84, 89, 91, 93, 94, 95, 94, 93,
    91, 90, 88, 87, 85, 83, 82, 81, 80, 79, 78, 77, 76, 75, 74, 73,
]
_SUEZ_VESSELS = [
    92, 88, 80, 68, 50, 38, 28, 20, 17, 14, 12, 11, 10, 10, 11, 12,
    13, 13, 14, 14, 15, 16, 16, 17, 18, 18, 19, 20, 20, 21, 22, 22,
]
# FBX03 spot index baseline: $850/FEU in Oct 2023, peak ~$5200 in Jan 2024
_FBX03 = [
    850, 920, 1200, 1750, 2400, 3400, 4600, 5100, 5200, 4900, 4700,
    4500, 4300, 4100, 3900, 3800, 3700, 3600, 3400, 3200, 3000, 2900,
    2800, 2750, 2700, 2650, 2600, 2550, 2450, 2350, 2300, 2250,
]


def _render_red_sea_tracker() -> None:
    _section_header(
        "Red Sea Crisis Tracker",
        "Active disruption — Houthi attacks forcing Cape of Good Hope rerouting since Nov 2023",
    )

    # ── Incident timeline ──────────────────────────────────────────────────
    st.markdown(
        "<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
        " color:" + C_TEXT3 + "; margin-bottom:10px; font-weight:700'>"
        "Key Incidents Timeline</div>",
        unsafe_allow_html=True,
    )

    n = len(_RED_SEA_INCIDENTS)
    dates = [item[0] for item in _RED_SEA_INCIDENTS]
    labels = [item[1] for item in _RED_SEA_INCIDENTS]
    y_vals = [1] * n

    fig_tl = go.Figure()
    fig_tl.add_trace(go.Scatter(
        x=dates,
        y=y_vals,
        mode="markers+lines",
        line=dict(color="rgba(239,68,68,0.35)", width=1.5, dash="dot"),
        marker=dict(
            size=9,
            color=[C_RED if i % 2 == 0 else C_ORANGE for i in range(n)],
            line=dict(color="rgba(255,255,255,0.3)", width=1),
        ),
        text=labels,
        hovertemplate="<b>%{x}</b><br>%{text}<extra></extra>",
        showlegend=False,
    ))

    # Add annotation text for first few key events
    for i in range(0, n, 3):
        short = labels[i][:42] + ("..." if len(labels[i]) > 42 else "")
        fig_tl.add_annotation(
            x=dates[i],
            y=1.0,
            text=short,
            showarrow=True,
            arrowhead=2,
            arrowcolor="rgba(148,163,184,0.5)",
            arrowsize=0.8,
            ax=0,
            ay=-35 if i % 2 == 0 else 35,
            font=dict(size=9, color=C_TEXT2),
            bgcolor="rgba(10,15,26,0.85)",
            bordercolor="rgba(255,255,255,0.12)",
            borderwidth=1,
            borderpad=3,
        )

    fig_tl.update_layout(
        height=200,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            showgrid=False,
            zeroline=False,
            tickfont=dict(color=C_TEXT3, size=10),
            tickangle=-30,
        ),
        yaxis=dict(visible=False, range=[0.8, 1.4]),
        margin=dict(l=0, r=0, t=20, b=40),
    )
    st.plotly_chart(fig_tl, use_container_width=True, config={"displayModeBar": False})

    # ── Cape vs Suez vessel comparison ────────────────────────────────────
    st.markdown(
        "<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
        " color:" + C_TEXT3 + "; margin-top:18px; margin-bottom:10px; font-weight:700'>"
        "Asia-Europe: Cape vs Suez Weekly Vessels</div>",
        unsafe_allow_html=True,
    )

    weeks = ["Wk " + str(i + 1) for i in range(len(_CAPE_VESSELS))]

    fig_cv = go.Figure()
    fig_cv.add_trace(go.Scatter(
        x=weeks,
        y=_CAPE_VESSELS,
        mode="lines+markers",
        name="Cape of Good Hope",
        line=dict(color=C_ORANGE, width=2.5),
        marker=dict(size=5, color=C_ORANGE),
        fill="tozeroy",
        fillcolor="rgba(249,115,22,0.08)",
        hovertemplate="Cape: %{y} vessels<extra></extra>",
    ))
    fig_cv.add_trace(go.Scatter(
        x=weeks,
        y=_SUEZ_VESSELS,
        mode="lines+markers",
        name="Suez / Red Sea",
        line=dict(color=C_BLUE, width=2.5),
        marker=dict(size=5, color=C_BLUE),
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.08)",
        hovertemplate="Suez: %{y} vessels<extra></extra>",
    ))
    fig_cv.update_layout(
        height=240,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT2, size=11),
        margin=dict(l=0, r=0, t=10, b=30),
        xaxis=dict(
            showgrid=False,
            tickfont=dict(size=9, color=C_TEXT3),
            zeroline=False,
        ),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(size=9, color=C_TEXT3),
            zeroline=False,
            title=dict(text="Vessels/wk", font=dict(size=10, color=C_TEXT3)),
        ),
        legend=dict(
            font=dict(color=C_TEXT2, size=10),
            bgcolor="rgba(10,15,26,0.6)",
        ),
    )
    st.plotly_chart(fig_cv, use_container_width=True, config={"displayModeBar": False})

    # ── FBX03 rate premium ────────────────────────────────────────────────
    rr_col, ins_col = st.columns(2)

    with rr_col:
        st.markdown(
            "<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
            " color:" + C_TEXT3 + "; margin-bottom:10px; font-weight:700'>"
            "FBX03 Asia-Europe Spot Rate ($/FEU)</div>",
            unsafe_allow_html=True,
        )
        fig_fbx = go.Figure()
        fig_fbx.add_trace(go.Scatter(
            x=weeks,
            y=_FBX03,
            mode="lines",
            line=dict(color=C_RED, width=2.5),
            fill="tozeroy",
            fillcolor="rgba(239,68,68,0.08)",
            hovertemplate="$%{y:,.0f}/FEU<extra></extra>",
            showlegend=False,
        ))
        fig_fbx.add_hline(
            y=850,
            line_color="rgba(16,185,129,0.4)",
            line_dash="dash",
            line_width=1,
            annotation_text="Pre-crisis baseline $850",
            annotation_font_color=C_GREEN,
            annotation_font_size=9,
        )
        fig_fbx.update_layout(
            height=220,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=10, b=30),
            xaxis=dict(showgrid=False, tickfont=dict(size=8, color=C_TEXT3), zeroline=False),
            yaxis=dict(
                gridcolor="rgba(255,255,255,0.04)",
                tickfont=dict(size=9, color=C_TEXT3),
                zeroline=False,
                tickprefix="$",
            ),
        )
        st.plotly_chart(fig_fbx, use_container_width=True, config={"displayModeBar": False})

    with ins_col:
        # War risk insurance premium (basis points, synthetic but realistic)
        _WAR_RISK_BPS = [
            5, 6, 8, 12, 22, 38, 55, 65, 72, 78, 82, 85, 85, 84, 83,
            82, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68, 67, 66, 65,
        ]
        st.markdown(
            "<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
            " color:" + C_TEXT3 + "; margin-bottom:10px; font-weight:700'>"
            "War Risk Insurance Premium (basis points of hull value)</div>",
            unsafe_allow_html=True,
        )
        fig_ins = go.Figure()
        fig_ins.add_trace(go.Scatter(
            x=weeks,
            y=_WAR_RISK_BPS,
            mode="lines",
            line=dict(color=C_WARN, width=2.5),
            fill="tozeroy",
            fillcolor="rgba(245,158,11,0.08)",
            hovertemplate="%{y} bps<extra></extra>",
            showlegend=False,
        ))
        fig_ins.add_hline(
            y=5,
            line_color="rgba(16,185,129,0.4)",
            line_dash="dash",
            line_width=1,
            annotation_text="Pre-crisis ~5 bps",
            annotation_font_color=C_GREEN,
            annotation_font_size=9,
        )
        fig_ins.update_layout(
            height=220,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=10, b=30),
            xaxis=dict(showgrid=False, tickfont=dict(size=8, color=C_TEXT3), zeroline=False),
            yaxis=dict(
                gridcolor="rgba(255,255,255,0.04)",
                tickfont=dict(size=9, color=C_TEXT3),
                zeroline=False,
                ticksuffix=" bps",
            ),
        )
        st.plotly_chart(fig_ins, use_container_width=True, config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# Section 5: Historical Chokepoint Events Timeline
# ---------------------------------------------------------------------------

_HISTORICAL_EVENTS = [
    ("2004-06-01", "Malacca piracy peak — 200+ incidents, IMO emergency response",      "HIGH"),
    ("2008-11-01", "Somali piracy escalation — Gulf of Aden attacks surge",              "HIGH"),
    ("2011-03-01", "Somali piracy peak — 151 attacks, 29 ships hijacked",               "HIGH"),
    ("2013-01-01", "International naval patrols drastically cut Somali piracy",          "LOW"),
    ("2016-07-15", "South China Sea arbitration ruling; Malacca tensions spike briefly", "MODERATE"),
    ("2019-06-13", "Gulf of Oman tanker attacks; Hormuz tensions spike",                 "HIGH"),
    ("2021-03-23", "Ever Given blocks Suez Canal — 6-day closure",                      "CRITICAL"),
    ("2021-03-29", "Ever Given refloated; $9.6B daily trade restored",                  "LOW"),
    ("2022-02-24", "Russia invades Ukraine; Baltic/Black Sea disruptions begin",         "HIGH"),
    ("2023-08-01", "Panama Canal drought — deepest water restrictions in decades",       "MODERATE"),
    ("2023-11-19", "Houthi Red Sea attacks begin — Asia-Europe crisis starts",           "CRITICAL"),
    ("2024-01-12", "Major carriers abandon Red Sea routes permanently (near-term)",      "CRITICAL"),
    ("2024-03-26", "Francis Scott Key Bridge collapse; Baltimore port closure",          "HIGH"),
    ("2025-01-19", "Gaza ceasefire — Houthi attacks pause temporarily",                 "MODERATE"),
    ("2026-01-01", "Red Sea remains HIGH risk; Cape rerouting semi-permanent",           "HIGH"),
]

_RISK_Y = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "LOW": 1}
_RISK_COLS = {
    "CRITICAL": C_RED,
    "HIGH":     C_ORANGE,
    "MODERATE": C_WARN,
    "LOW":      C_GREEN,
}


def _render_historical_timeline() -> None:
    _section_header(
        "Historical Chokepoint Events 2004-2026",
        "Major disruptions and their market impact",
    )

    dates = [e[0] for e in _HISTORICAL_EVENTS]
    descs = [e[1] for e in _HISTORICAL_EVENTS]
    risks = [e[2] for e in _HISTORICAL_EVENTS]
    ys = [_RISK_Y[r] for r in risks]
    colors = [_RISK_COLS[r] for r in risks]

    fig = go.Figure()

    # Horizontal bands for risk levels
    for level, y_val in _RISK_Y.items():
        fig.add_hrect(
            y0=y_val - 0.35,
            y1=y_val + 0.35,
            fillcolor=_RISK_COLS[level] + "0a",
            line_width=0,
        )

    fig.add_trace(go.Scatter(
        x=dates,
        y=ys,
        mode="markers",
        marker=dict(
            size=14,
            color=colors,
            line=dict(color="rgba(255,255,255,0.3)", width=1.5),
            symbol="circle",
        ),
        text=descs,
        customdata=risks,
        hovertemplate="<b>%{x}</b><br>%{text}<br>Risk: %{customdata}<extra></extra>",
        showlegend=False,
    ))

    # Annotate notable events
    notable_indices = [4, 6, 10, 13]
    for i in notable_indices:
        short = descs[i][:40] + ("..." if len(descs[i]) > 40 else "")
        fig.add_annotation(
            x=dates[i],
            y=ys[i],
            text=short,
            showarrow=True,
            arrowhead=2,
            arrowcolor="rgba(148,163,184,0.4)",
            arrowsize=0.7,
            ax=0,
            ay=-38 if ys[i] >= 3 else 38,
            font=dict(size=8.5, color=C_TEXT2),
            bgcolor="rgba(10,15,26,0.88)",
            bordercolor="rgba(255,255,255,0.1)",
            borderwidth=1,
            borderpad=3,
        )

    fig.update_layout(
        height=320,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT2, size=11),
        margin=dict(l=0, r=0, t=10, b=40),
        xaxis=dict(
            showgrid=False,
            zeroline=False,
            tickfont=dict(size=10, color=C_TEXT3),
            tickangle=-30,
            title=dict(text="", font=dict(size=10)),
        ),
        yaxis=dict(
            tickvals=[1, 2, 3, 4],
            ticktext=["LOW", "MODERATE", "HIGH", "CRITICAL"],
            tickfont=dict(size=10, color=C_TEXT2),
            gridcolor="rgba(255,255,255,0.04)",
            zeroline=False,
            range=[0.4, 4.7],
        ),
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------

def render(route_results: list, freight_data: dict, macro_data: dict) -> None:
    """Render the Maritime Chokepoints tab."""

    logger.info("Rendering tab_chokepoints")

    # Inject CSS for pulsing animation
    st.markdown(
        """
<style>
@keyframes chokepoint-pulse {
    0%   { opacity: 1;   transform: scale(1);    }
    50%  { opacity: 0.55; transform: scale(1.04); }
    100% { opacity: 1;   transform: scale(1);    }
}
</style>
""",
        unsafe_allow_html=True,
    )

    # ── Page header ─────────────────────────────────────────────────────────
    active_disruptions = get_current_active_disruptions()
    n_active = len(active_disruptions)
    active_names = ", ".join(cp.name for cp in active_disruptions if
                             cp.current_risk_level in ("CRITICAL", "HIGH"))

    st.markdown(
        "<div style='padding:16px 0 20px 0; border-bottom:1px solid rgba(255,255,255,0.06);"
        " margin-bottom:20px'>"
        "<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.15em;"
        " color:" + C_TEXT3 + "; margin-bottom:6px'>GEOPOLITICAL INTELLIGENCE</div>"
        "<div style='font-size:1.75rem; font-weight:900; color:" + C_TEXT + ";"
        " letter-spacing:-0.03em; line-height:1.1'>Maritime Chokepoints</div>"
        "<div style='font-size:0.85rem; color:" + C_TEXT2 + "; margin-top:6px'>"
        "9 critical passages controlling 60%+ of global trade  &nbsp;|&nbsp; "
        "<span style='color:" + C_RED + "; font-weight:700'>"
        + str(n_active) + " active disruptions</span>"
        + ("  &mdash;  <span style='color:" + C_ORANGE + "'>" + active_names + "</span>"
           if active_names else "")
        + "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # Compute risk scores once
    risk_scores = compute_chokepoint_risk_score()

    # ── Section 1 ───────────────────────────────────────────────────────────
    _render_world_map(risk_scores)

    st.divider()

    # ── Section 2 ───────────────────────────────────────────────────────────
    _render_risk_dashboard()

    st.divider()

    # ── Section 3 ───────────────────────────────────────────────────────────
    _render_closure_simulator()

    st.divider()

    # ── Section 4 ───────────────────────────────────────────────────────────
    _render_red_sea_tracker()

    st.divider()

    # ── Section 5 ───────────────────────────────────────────────────────────
    _render_historical_timeline()

    logger.info("tab_chokepoints render complete")
