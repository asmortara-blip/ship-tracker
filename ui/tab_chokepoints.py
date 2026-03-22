"""
Maritime Chokepoints Tab — Enhanced v2

Visualises the world's 9 critical maritime chokepoints and their current
disruption status, with deep analytics across 12 sections:

  0.  Live Intelligence Strip — real-time KPI ticker bar (NEW)
  1.  Status Dashboard       — 6 primary chokepoint status cards (Suez, Hormuz,
                                Panama, Malacca, Bab-el-Mandeb, Turkish Straits)
  1b. Chokepoint Comparison  — side-by-side heatmap + donut (NEW)
  2.  World Map              — Scattergeo with pulsing markers, risk-colored
  3.  Traffic Density        — Weekly vessels transiting each chokepoint (bar chart)
  4.  Transit Fee Analysis   — Suez and Panama Canal fees + recent changes
  4b. Fee Benchmark          — cost-per-TEU benchmark vs spot rate (NEW)
  5.  Chokepoint Risk Score  — Composite geopolitical/weather/congestion radar
  6.  Historical Disruption  — Rate impact when each chokepoint was disrupted
  7.  Alternative Routing    — Cost premium and extra days if each is blocked
  8.  Insurance Rate Impact  — War risk / marine insurance premium by chokepoint
  +   Red Sea Crisis Tracker  — Dedicated deep-dive section
  +   Historical Events       — Annotated 2004-2026 timeline
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

try:
    from data.canal_feed import fetch_panama_stats, fetch_suez_stats, get_canal_shipping_impact
    _CANAL_FEED_OK = True
except Exception as _cf_import_err:
    _CANAL_FEED_OK = False
    logger.warning(f"canal_feed not available: {_cf_import_err}")

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
C_CARD    = "#111827"
C_CARD2   = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.07)"
C_BORDER2 = "rgba(255,255,255,0.12)"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"
C_GREEN   = "#10b981"
C_TEAL    = "#14b8a6"
C_BLUE    = "#3b82f6"
C_INDIGO  = "#6366f1"
C_WARN    = "#f59e0b"
C_ORANGE  = "#f97316"
C_RED     = "#ef4444"
C_CRIMSON = "#dc2626"
C_PURPLE  = "#a855f7"

# Risk level → canonical color
_RISK_COLS = {
    "CRITICAL": C_RED,
    "HIGH":     C_ORANGE,
    "MODERATE": C_WARN,
    "LOW":      C_GREEN,
}

_RISK_ORDER = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3}

# Primary six chokepoints featured in Section 1 status dashboard
_PRIMARY_KEYS = ["suez", "hormuz", "panama", "malacca", "bab_el_mandeb", "turkish_straits"]

# Friendly emoji flag per chokepoint key (purely cosmetic — avoids Streamlit icons)
_REGION_ICON = {
    "suez":           "EG",
    "hormuz":         "IR",
    "panama":         "PA",
    "malacca":        "SG",
    "bab_el_mandeb":  "YE",
    "turkish_straits":"TR",
    "dover":          "GB",
    "danish_straits": "DK",
    "lombok":         "ID",
}


# ---------------------------------------------------------------------------
# CSS injection
# ---------------------------------------------------------------------------

_CSS = """
<style>
@keyframes chk-pulse {
  0%   { opacity:1;    transform:scale(1);    }
  50%  { opacity:0.50; transform:scale(1.06); }
  100% { opacity:1;    transform:scale(1);    }
}
@keyframes chk-glow {
  0%   { box-shadow: 0 0 0px rgba(239,68,68,0); }
  50%  { box-shadow: 0 0 14px rgba(239,68,68,0.35); }
  100% { box-shadow: 0 0 0px rgba(239,68,68,0); }
}
.chk-pulse-badge  { animation: chk-pulse 1.4s ease-in-out infinite; }
.chk-glow-card    { animation: chk-glow  2.2s ease-in-out infinite; }

/* Status card hover lift */
.chk-status-card {
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.chk-status-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 24px rgba(0,0,0,0.45);
}
</style>
"""


# ---------------------------------------------------------------------------
# HTML helper primitives
# ---------------------------------------------------------------------------

def _section_header(title: str, subtitle: str = "", eyebrow: str = "MARITIME INTELLIGENCE") -> None:
    sub_html = (
        f"<div style='font-size:0.83rem; color:{C_TEXT2}; margin-top:5px; "
        f"line-height:1.5'>{subtitle}</div>" if subtitle else ""
    )
    st.markdown(
        f"<div style='margin:32px 0 18px 0; padding-bottom:14px; "
        f"border-bottom:1px solid {C_BORDER}'>"
        f"<div style='font-size:0.65rem; text-transform:uppercase; letter-spacing:0.16em;"
        f" color:{C_TEXT3}; margin-bottom:6px'>{eyebrow}</div>"
        f"<div style='font-size:1.28rem; font-weight:800; color:{C_TEXT}; "
        f"letter-spacing:-0.025em; line-height:1.2'>{title}</div>"
        f"{sub_html}</div>",
        unsafe_allow_html=True,
    )


def _risk_badge(level: str, pulse: bool = False) -> str:
    color = _RISK_COLS.get(level, C_TEXT2)
    bg_map = {
        "CRITICAL": "rgba(239,68,68,0.16)",
        "HIGH":     "rgba(249,115,22,0.16)",
        "MODERATE": "rgba(245,158,11,0.14)",
        "LOW":      "rgba(16,185,129,0.13)",
    }
    bg = bg_map.get(level, "rgba(148,163,184,0.10)")
    cls = " chk-pulse-badge" if pulse else ""
    return (
        f"<span class='chk-pulse-badge' style='display:inline-block; background:{bg};"
        f" color:{color}; border:1px solid {color}55; padding:2px 10px;"
        f" border-radius:999px; font-size:0.66rem; font-weight:700;"
        f" letter-spacing:0.07em'>{level}</span>"
        if pulse else
        f"<span style='display:inline-block; background:{bg}; color:{color};"
        f" border:1px solid {color}55; padding:2px 10px; border-radius:999px;"
        f" font-size:0.66rem; font-weight:700; letter-spacing:0.07em'>{level}</span>"
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
        f"<span style='display:inline-block; background:{c}1a; color:{c};"
        f" border:1px solid {c}40; padding:1px 8px; border-radius:999px;"
        f" font-size:0.62rem; font-weight:600; letter-spacing:0.05em;"
        f" margin-left:6px'>{label}</span>"
    )


def _metric_pill(value: str, label: str, color: str = C_TEXT2) -> str:
    return (
        f"<div style='text-align:center; flex:1'>"
        f"<div style='font-size:1.05rem; font-weight:800; color:{color}; line-height:1.1'>{value}</div>"
        f"<div style='font-size:0.60rem; color:{C_TEXT3}; margin-top:2px; text-transform:uppercase;"
        f" letter-spacing:0.06em'>{label}</div>"
        f"</div>"
    )


def _divider_line() -> None:
    st.markdown(
        f"<hr style='border:none; border-top:1px solid {C_BORDER}; margin:28px 0 0 0'>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 1: Global Status Dashboard (6 primary chokepoints)
# ---------------------------------------------------------------------------

_STATUS_CONTEXT = {
    "suez":           "Houthi attacks on Red Sea shipping force ~70% of traffic to Cape rerouting.",
    "hormuz":         "Iran-US tensions elevated. No alternative route for Persian Gulf oil/LNG.",
    "panama":         "Water levels recovering after 2023 drought. Mild draft restrictions remain.",
    "malacca":        "Highest-volume passage globally. Piracy monitoring ongoing. Stable.",
    "bab_el_mandeb":  "Linked to Suez crisis. Houthi threat extends across Bab-el-Mandeb.",
    "turkish_straits":"Russia-Ukraine war reduces Black Sea trade. Turkish control maintained.",
}


def _render_status_dashboard() -> None:
    _section_header(
        "Global Chokepoint Status",
        "Real-time risk level for the 6 most strategically critical maritime passages",
        eyebrow="STATUS DASHBOARD",
    )

    # Build list of (key, Chokepoint) for primary keys; fall back gracefully
    primary_cps: list[tuple[str, Chokepoint]] = []
    for k in _PRIMARY_KEYS:
        if k in CHOKEPOINTS:
            primary_cps.append((k, CHOKEPOINTS[k]))

    # If primary keys not found, show top-6 by risk order
    if not primary_cps:
        primary_cps = sorted(
            CHOKEPOINTS.items(),
            key=lambda kv: (_RISK_ORDER.get(kv[1].current_risk_level, 99), kv[0]),
        )[:6]

    rows = [primary_cps[i:i + 3] for i in range(0, len(primary_cps), 3)]

    for row in rows:
        cols = st.columns(3, gap="small")
        for col, (key, cp) in zip(cols, row):
            with col:
                level = cp.current_risk_level
                color = _RISK_COLS.get(level, C_TEXT2)
                is_critical = level in ("CRITICAL", "HIGH")
                border_col = color + "55"
                card_cls = "chk-status-card chk-glow-card" if level == "CRITICAL" else "chk-status-card"
                glow_css = f"box-shadow:0 0 0 1px {color}33;" if is_critical else ""
                context = _STATUS_CONTEXT.get(key, "")
                region = _REGION_ICON.get(key, "")
                region_html = (
                    f"<span style='font-size:0.60rem; color:{C_TEXT3}; font-weight:600;"
                    f" letter-spacing:0.06em; margin-left:4px'>[{region}]</span>"
                    if region else ""
                )

                since_html = ""
                if cp.current_disruption_type != "NONE" and cp.disruption_since:
                    since_html = (
                        f"<div style='font-size:0.64rem; color:{C_TEXT3}; margin-top:4px'>"
                        f"Active since {cp.disruption_since}</div>"
                    )

                context_html = ""
                if context:
                    context_html = (
                        f"<div style='font-size:0.68rem; color:{C_TEXT2}; margin-top:10px;"
                        f" padding:8px 10px; background:rgba(255,255,255,0.03);"
                        f" border-left:2px solid {color}55; border-radius:0 6px 6px 0;"
                        f" line-height:1.5'>{context}</div>"
                    )

                no_alt_html = ""
                if not cp.strategic_alternatives:
                    no_alt_html = (
                        f"<div style='font-size:0.64rem; color:{C_RED}; font-weight:600;"
                        f" margin-top:6px'>No viable alternative route</div>"
                    )
                else:
                    alt_short = cp.strategic_alternatives[0][:52]
                    no_alt_html = (
                        f"<div style='font-size:0.63rem; color:{C_TEXT3}; margin-top:6px'>"
                        f"Alt: {alt_short}</div>"
                    )

                st.markdown(
                    f"<div class='{card_cls}' style='background:{C_CARD2}; border:1px solid {border_col};"
                    f" border-radius:14px; padding:18px 16px; height:100%; {glow_css}'>"
                    f"<div style='display:flex; justify-content:space-between; align-items:flex-start;"
                    f" margin-bottom:10px'>"
                    f"<div style='font-size:0.82rem; font-weight:700; color:{C_TEXT}; line-height:1.3'>"
                    f"{cp.name}{region_html}</div>"
                    f"</div>"
                    f"<div style='margin-bottom:8px'>"
                    f"{_risk_badge(level, pulse=is_critical)}"
                    f"{_disruption_badge(cp.current_disruption_type)}"
                    f"</div>"
                    f"{since_html}"
                    f"<div style='display:flex; gap:6px; margin-top:12px; padding-top:10px;"
                    f" border-top:1px solid {C_BORDER}'>"
                    f"{_metric_pill(str(cp.daily_vessels), 'vessels/day', C_TEXT)}"
                    f"{_metric_pill(str(cp.pct_global_trade) + '%', 'global trade', C_WARN)}"
                    f"{_metric_pill(str(cp.extra_days_if_closed) + 'd', 'if closed', C_ORANGE)}"
                    f"</div>"
                    f"{context_html}"
                    f"{no_alt_html}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        # spacing between rows
        st.markdown("<div style='margin-bottom:10px'></div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 2: World Map
# ---------------------------------------------------------------------------

def _render_world_map(risk_scores: dict[str, float]) -> None:
    _section_header(
        "Global Chokepoint Map",
        "All 9 critical maritime passages — marker size = daily TEU throughput, "
        "color = current risk level. Pulsing rings indicate active disruptions.",
        eyebrow="INTERACTIVE MAP",
    )

    lats, lons, names_list, sizes, colors_list, hovers = [], [], [], [], [], []
    active_lats, active_lons, active_sizes, active_colors = [], [], [], []

    for key, cp in CHOKEPOINTS.items():
        lats.append(cp.lat)
        lons.append(cp.lon)
        names_list.append(cp.name)
        sz = max(11, min(46, int(cp.daily_teu_m * 30 + 9)))
        sizes.append(sz)
        col = _RISK_COLS.get(cp.current_risk_level, C_TEXT3)
        colors_list.append(col)
        alt_text = (
            "; ".join(cp.strategic_alternatives[:2]) if cp.strategic_alternatives
            else "None — critical vulnerability"
        )
        score = risk_scores.get(key, 0)
        hovers.append(
            f"<b>{cp.name}</b><br>"
            f"Risk: <b>{cp.current_risk_level}</b><br>"
            f"Risk score: {score:.1f}/10<br>"
            f"Daily vessels: {cp.daily_vessels}<br>"
            f"Daily TEU: {cp.daily_teu_m}M<br>"
            f"Global trade: {cp.pct_global_trade}%<br>"
            f"Disruption: {cp.current_disruption_type}<br>"
            f"Alternative: {alt_text}"
        )
        if cp.current_risk_level in ("CRITICAL", "HIGH"):
            active_lats.append(cp.lat)
            active_lons.append(cp.lon)
            active_sizes.append(sz + 18)
            active_colors.append(col)

    fig = go.Figure()

    # Concentric ring layers for active disruptions
    for ring_factor, opacity_base in [(2.6, 0.10), (1.9, 0.16), (1.3, 0.22)]:
        if active_lats:
            fig.add_trace(go.Scattergeo(
                lat=active_lats,
                lon=active_lons,
                mode="markers",
                marker=dict(
                    size=[int(s * ring_factor) for s in active_sizes],
                    color=[c + "00" for c in active_colors],
                    line=dict(
                        color=[c + f"{int(opacity_base * 255):02x}" for c in active_colors],
                        width=1.5,
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
            line=dict(color="rgba(255,255,255,0.22)", width=1.5),
            opacity=0.90,
        ),
        text=names_list,
        textposition="top center",
        textfont=dict(size=9.5, color="#f1f5f9", family="monospace"),
        hovertemplate="%{customdata}<extra></extra>",
        customdata=hovers,
        showlegend=False,
    ))

    # Legend traces
    for level, color in [("CRITICAL", C_RED), ("HIGH", C_ORANGE),
                         ("MODERATE", C_WARN), ("LOW", C_GREEN)]:
        fig.add_trace(go.Scattergeo(
            lat=[None], lon=[None],
            mode="markers",
            marker=dict(size=9, color=color, line=dict(color="rgba(255,255,255,0.25)", width=1)),
            name=level,
            showlegend=True,
        ))

    try:
        fig.update_layout(
            height=560,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            geo=dict(
                showframe=False,
                showcoastlines=True,
                coastlinecolor="rgba(148,163,184,0.25)",
                showland=True,
                landcolor="#0d1520",
                showocean=True,
                oceancolor="#060c16",
                showlakes=False,
                showrivers=False,
                showcountries=True,
                countrycolor="rgba(255,255,255,0.055)",
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
                bgcolor="rgba(10,15,26,0.75)",
                bordercolor="rgba(255,255,255,0.1)",
                borderwidth=1,
            ),
            margin=dict(l=0, r=0, t=8, b=0),
        )
        st.plotly_chart(
            fig, use_container_width=True,
            config={"displayModeBar": False},
            key="chk_world_map",
        )
    except Exception as _geo_err:
        logger.warning(f"World map geo rendering failed: {_geo_err}; using table fallback")
        st.info("Interactive map unavailable in this environment. Showing chokepoint table.", icon="🗺️")
        _sorted_cps = sorted(CHOKEPOINTS.values(), key=lambda c: _RISK_ORDER.get(c.current_risk_level, 99))
        _rows_html = "".join(
            f"<tr>"
            f"<td style='padding:7px 10px; color:{C_TEXT}; font-weight:600'>{cp.name}</td>"
            f"<td style='padding:7px 10px; color:{C_TEXT2}'>{round(cp.lat, 1)}, {round(cp.lon, 1)}</td>"
            f"<td style='padding:7px 10px'>{_risk_badge(cp.current_risk_level)}</td>"
            f"<td style='padding:7px 10px; color:{C_TEXT2}'>{cp.daily_vessels} v/d</td>"
            f"<td style='padding:7px 10px; color:{C_WARN}'>{cp.pct_global_trade}% trade</td>"
            f"</tr>"
            for cp in _sorted_cps
        )
        st.markdown(
            f"<div style='background:{C_CARD2}; border:1px solid {C_BORDER}; border-radius:12px;"
            f" padding:16px; overflow-x:auto'>"
            f"<table style='width:100%; border-collapse:collapse'>"
            f"<thead><tr>"
            f"<th style='padding:6px 10px; color:{C_TEXT3}; font-size:0.70rem; text-align:left'>CHOKEPOINT</th>"
            f"<th style='padding:6px 10px; color:{C_TEXT3}; font-size:0.70rem; text-align:left'>COORDS</th>"
            f"<th style='padding:6px 10px; color:{C_TEXT3}; font-size:0.70rem; text-align:left'>RISK</th>"
            f"<th style='padding:6px 10px; color:{C_TEXT3}; font-size:0.70rem; text-align:left'>TRAFFIC</th>"
            f"<th style='padding:6px 10px; color:{C_TEXT3}; font-size:0.70rem; text-align:left'>TRADE</th>"
            f"</tr></thead><tbody>{_rows_html}</tbody></table></div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Section 3: Traffic Density Chart
# ---------------------------------------------------------------------------

# Weekly vessel counts per chokepoint (vessels transiting, indexed as relative
# weekly averages based on public AIS estimates — early 2026 snapshot)
_WEEKLY_VESSELS: dict[str, list[int]] = {
    "Strait of Malacca":    [1680, 1720, 1690, 1750, 1700, 1680, 1710, 1695],
    "Suez Canal":           [ 280,  260,  250,  240,  235,  245,  255,  250],
    "Strait of Hormuz":     [ 490,  510,  505,  495,  512,  500,  498,  502],
    "Panama Canal":         [ 210,  195,  205,  210,  200,  198,  207,  203],
    "Bab-el-Mandeb":        [ 140,  135,  138,  130,  128,  135,  133,  136],
    "Turkish Straits":      [ 290,  295,  288,  302,  298,  293,  300,  297],
    "Dover Strait":         [1050, 1080, 1060, 1090, 1070, 1055, 1075, 1065],
    "Danish Straits":       [ 195,  200,  197,  205,  198,  202,  199,  204],
    "Lombok Strait":        [ 360,  355,  362,  370,  358,  365,  360,  368],
}

_WEEKS = ["Wk -7", "Wk -6", "Wk -5", "Wk -4", "Wk -3", "Wk -2", "Wk -1", "Current"]


def _render_traffic_density() -> None:
    _section_header(
        "Weekly Traffic Density",
        "Estimated vessel transits per week at each chokepoint — 8-week rolling window (AIS-derived)",
        eyebrow="TRAFFIC ANALYTICS",
    )

    # Summary bar: average weekly vessels per chokepoint
    cp_names = list(_WEEKLY_VESSELS.keys())
    avg_vessels = [round(sum(v) / len(v)) for v in _WEEKLY_VESSELS.values()]

    # Color by matching CHOKEPOINTS risk level where possible
    bar_colors: list[str] = []
    for name in cp_names:
        matched = next((cp for cp in CHOKEPOINTS.values() if cp.name == name), None)
        bar_colors.append(_RISK_COLS.get(matched.current_risk_level, C_BLUE) if matched else C_BLUE)

    fig_bar = go.Figure()
    fig_bar.add_trace(go.Bar(
        x=cp_names,
        y=avg_vessels,
        marker=dict(
            color=bar_colors,
            opacity=0.85,
            line=dict(color="rgba(255,255,255,0.12)", width=1),
        ),
        text=[f"{v:,}" for v in avg_vessels],
        textposition="outside",
        textfont=dict(size=10, color=C_TEXT2),
        hovertemplate="<b>%{x}</b><br>Avg: %{y:,} vessels/wk<extra></extra>",
        showlegend=False,
    ))
    fig_bar.update_layout(
        height=300,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT2, size=11),
        margin=dict(l=0, r=0, t=28, b=60),
        xaxis=dict(
            showgrid=False,
            zeroline=False,
            tickfont=dict(size=10, color=C_TEXT2),
            tickangle=-25,
        ),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(size=9, color=C_TEXT3),
            zeroline=False,
            title=dict(text="Avg Vessels / Week", font=dict(size=10, color=C_TEXT3)),
        ),
        title=dict(
            text="Average Weekly Vessel Transits by Chokepoint",
            font=dict(size=12, color=C_TEXT2),
            x=0.0,
            xanchor="left",
        ),
    )
    st.plotly_chart(fig_bar, use_container_width=True, config={"displayModeBar": False}, key="chk_traffic_bar")

    # Trend lines for top 3 by volume
    st.markdown(
        f"<div style='font-size:0.70rem; text-transform:uppercase; letter-spacing:0.10em;"
        f" color:{C_TEXT3}; margin:16px 0 8px 0; font-weight:700'>8-Week Trend — Top 3 by Volume</div>",
        unsafe_allow_html=True,
    )
    top3 = sorted(_WEEKLY_VESSELS.items(), key=lambda kv: -sum(kv[1]))[:3]
    trend_colors = [C_BLUE, C_TEAL, C_INDIGO]
    fig_trend = go.Figure()
    for (name, vals), col in zip(top3, trend_colors):
        fig_trend.add_trace(go.Scatter(
            x=_WEEKS,
            y=vals,
            mode="lines+markers",
            name=name,
            line=dict(color=col, width=2.5),
            marker=dict(size=6, color=col),
            hovertemplate=f"<b>{name}</b><br>%{{x}}: %{{y:,}} vessels<extra></extra>",
        ))
    fig_trend.update_layout(
        height=220,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT2, size=11),
        margin=dict(l=0, r=0, t=10, b=30),
        xaxis=dict(showgrid=False, zeroline=False, tickfont=dict(size=9, color=C_TEXT3)),
        yaxis=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(size=9, color=C_TEXT3),
                   zeroline=False, title=dict(text="Vessels/wk", font=dict(size=10, color=C_TEXT3))),
        legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(10,15,26,0.6)"),
    )
    st.plotly_chart(fig_trend, use_container_width=True, config={"displayModeBar": False}, key="chk_traffic_trend")


# ---------------------------------------------------------------------------
# Section 4: Transit Fee / Toll Analysis
# ---------------------------------------------------------------------------

# Suez Canal: Special Drawing Right (SDR) tolls converted to USD, per vessel type
# Panama Canal: PC/UMS net tonnage toll — data current as of early 2026
_CANAL_FEES: dict[str, list[dict]] = {
    "Suez Canal": [
        {"vessel_type": "Container (ultra-large)", "fee_usd": 1_200_000, "unit": "per transit",
         "change_pct": +12.0, "change_note": "Jan 2024 surcharge; 2025 discount offered to lure back traffic"},
        {"vessel_type": "Container (large)",       "fee_usd":   520_000, "unit": "per transit",
         "change_pct": +8.0,  "change_note": "SCA raised SDR rates Jan 2024"},
        {"vessel_type": "Tanker (VLCC)",           "fee_usd":   490_000, "unit": "per transit",
         "change_pct": +5.0,  "change_note": "Northbound surcharge applied"},
        {"vessel_type": "Bulker (Capesize)",        "fee_usd":   270_000, "unit": "per transit",
         "change_pct": +6.0,  "change_note": "Dry bulk surcharge since 2024"},
        {"vessel_type": "LNG Carrier",             "fee_usd":   430_000, "unit": "per transit",
         "change_pct": +9.0,  "change_note": "LNG priority lanes pricing increased"},
    ],
    "Panama Canal": [
        {"vessel_type": "Container (Neo-Panamax)", "fee_usd":   900_000, "unit": "per transit",
         "change_pct": +18.0, "change_note": "Water surcharge added Oct 2023 (drought); partially rescinded 2025"},
        {"vessel_type": "Container (Panamax)",     "fee_usd":   450_000, "unit": "per transit",
         "change_pct": +15.0, "change_note": "Drought-driven booking slot auction premium"},
        {"vessel_type": "Tanker (Aframax)",        "fee_usd":   310_000, "unit": "per transit",
         "change_pct": +10.0, "change_note": "Booking reservation fee introduced"},
        {"vessel_type": "LNG Carrier",             "fee_usd":   550_000, "unit": "per transit",
         "change_pct": +20.0, "change_note": "Record slot auction prices during 2023 drought"},
        {"vessel_type": "Bulker (Panamax)",        "fee_usd":   230_000, "unit": "per transit",
         "change_pct": +12.0, "change_note": "Water restrictions surcharge"},
    ],
}

_FEE_HISTORY = {
    "Suez Canal": {
        "years": ["2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026E"],
        "avg_fee_k": [380, 365, 390, 420, 445, 530, 510, 520],
    },
    "Panama Canal": {
        "years": ["2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026E"],
        "avg_fee_k": [250, 240, 260, 290, 340, 430, 390, 400],
    },
}


def _render_transit_fees() -> None:
    _section_header(
        "Canal Transit Fee Analysis",
        "Suez and Panama Canal tolls by vessel type — current rates and recent changes",
        eyebrow="TOLL ECONOMICS",
    )

    tab_suez, tab_panama = st.tabs(["Suez Canal", "Panama Canal"])

    for tab, canal_name in [(tab_suez, "Suez Canal"), (tab_panama, "Panama Canal")]:
        with tab:
            fees = _CANAL_FEES[canal_name]
            hist = _FEE_HISTORY[canal_name]
            canal_color = C_RED if canal_name == "Suez Canal" else C_BLUE

            fee_col, hist_col = st.columns([3, 2], gap="medium")

            with fee_col:
                st.markdown(
                    f"<div style='font-size:0.70rem; text-transform:uppercase; letter-spacing:0.10em;"
                    f" color:{C_TEXT3}; margin-bottom:10px; font-weight:700'>"
                    f"Current Rates by Vessel Type</div>",
                    unsafe_allow_html=True,
                )
                for fee in fees:
                    change_col = C_RED if fee["change_pct"] > 0 else C_GREEN
                    change_sym = "+" if fee["change_pct"] > 0 else ""
                    st.markdown(
                        f"<div style='background:{C_CARD}; border:1px solid {C_BORDER};"
                        f" border-radius:10px; padding:12px 14px; margin-bottom:8px'>"
                        f"<div style='display:flex; justify-content:space-between; align-items:center'>"
                        f"<div style='font-size:0.78rem; font-weight:600; color:{C_TEXT}'>"
                        f"{fee['vessel_type']}</div>"
                        f"<div style='display:flex; align-items:center; gap:10px'>"
                        f"<span style='font-size:1.0rem; font-weight:800; color:{canal_color}'>"
                        f"${fee['fee_usd']:,.0f}</span>"
                        f"<span style='font-size:0.68rem; font-weight:700; color:{change_col};"
                        f" background:{change_col}18; border:1px solid {change_col}40;"
                        f" padding:1px 7px; border-radius:999px'>"
                        f"{change_sym}{fee['change_pct']:.0f}%</span>"
                        f"</div></div>"
                        f"<div style='font-size:0.65rem; color:{C_TEXT3}; margin-top:5px'>"
                        f"{fee['change_note']}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

            with hist_col:
                st.markdown(
                    f"<div style='font-size:0.70rem; text-transform:uppercase; letter-spacing:0.10em;"
                    f" color:{C_TEXT3}; margin-bottom:10px; font-weight:700'>"
                    f"Average Transit Fee Trend ($K)</div>",
                    unsafe_allow_html=True,
                )
                fig_fee = go.Figure()
                fig_fee.add_trace(go.Scatter(
                    x=hist["years"],
                    y=hist["avg_fee_k"],
                    mode="lines+markers",
                    line=dict(color=canal_color, width=2.5),
                    marker=dict(size=7, color=canal_color, line=dict(color="rgba(255,255,255,0.3)", width=1.5)),
                    fill="tozeroy",
                    fillcolor=canal_color + "14",
                    hovertemplate="<b>%{x}</b><br>Avg fee: $%{y}K<extra></extra>",
                    showlegend=False,
                ))
                # Highlight 2024 drought/conflict surge
                fig_fee.add_vline(
                    x="2024",
                    line_color=canal_color + "55",
                    line_dash="dash",
                    line_width=1.5,
                    annotation_text="Surge",
                    annotation_font=dict(size=9, color=canal_color),
                    annotation_position="top right",
                )
                fig_fee.update_layout(
                    height=280,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color=C_TEXT2, size=11),
                    margin=dict(l=0, r=0, t=10, b=40),
                    xaxis=dict(showgrid=False, zeroline=False, tickfont=dict(size=9, color=C_TEXT3)),
                    yaxis=dict(
                        gridcolor="rgba(255,255,255,0.04)",
                        tickfont=dict(size=9, color=C_TEXT3),
                        zeroline=False,
                        tickprefix="$",
                        ticksuffix="K",
                    ),
                )
                st.plotly_chart(fig_fee, use_container_width=True, config={"displayModeBar": False},
                                key=f"chk_fee_hist_{canal_name.replace(' ', '_')}")


# ---------------------------------------------------------------------------
# Section 5: Chokepoint Risk Score Radar
# ---------------------------------------------------------------------------

# Risk sub-scores per chokepoint (1-10 scale; updated early 2026)
_RISK_FACTORS: dict[str, dict[str, float]] = {
    "Suez Canal":          {"Geopolitical": 9.2, "Conflict": 8.8, "Weather": 2.0, "Congestion": 5.5, "Piracy": 2.5},
    "Strait of Hormuz":    {"Geopolitical": 8.5, "Conflict": 6.0, "Weather": 3.0, "Congestion": 6.0, "Piracy": 1.5},
    "Strait of Malacca":   {"Geopolitical": 4.0, "Conflict": 2.0, "Weather": 4.5, "Congestion": 7.5, "Piracy": 4.0},
    "Panama Canal":        {"Geopolitical": 2.5, "Conflict": 1.0, "Weather": 7.0, "Congestion": 6.5, "Piracy": 0.5},
    "Bab-el-Mandeb":       {"Geopolitical": 9.0, "Conflict": 8.5, "Weather": 3.5, "Congestion": 4.0, "Piracy": 3.0},
    "Turkish Straits":     {"Geopolitical": 6.0, "Conflict": 3.5, "Weather": 5.0, "Congestion": 5.5, "Piracy": 0.5},
    "Dover Strait":        {"Geopolitical": 3.0, "Conflict": 1.0, "Weather": 6.5, "Congestion": 7.0, "Piracy": 0.5},
    "Danish Straits":      {"Geopolitical": 4.5, "Conflict": 2.5, "Weather": 5.5, "Congestion": 4.0, "Piracy": 0.5},
    "Lombok Strait":       {"Geopolitical": 3.5, "Conflict": 1.5, "Weather": 5.0, "Congestion": 4.5, "Piracy": 2.0},
}

_RISK_FACTOR_LABELS = ["Geopolitical", "Conflict", "Weather", "Congestion", "Piracy"]


def _render_risk_scores(risk_scores: dict[str, float]) -> None:
    _section_header(
        "Chokepoint Risk Scoring",
        "Composite risk scores across 5 dimensions — geopolitical, conflict, weather, congestion, piracy",
        eyebrow="RISK ANALYTICS",
    )

    # Overall composite bar chart
    cp_order = sorted(
        [cp.name for cp in CHOKEPOINTS.values()],
        key=lambda n: -_RISK_FACTORS.get(n, {}).get("Geopolitical", 0),
    )
    composites = [
        round(sum(_RISK_FACTORS.get(n, {}).values()) / len(_RISK_FACTOR_LABELS), 2)
        for n in cp_order
    ]
    bar_colors_risk = [
        C_RED if s >= 6.5 else C_ORANGE if s >= 5.0 else C_WARN if s >= 3.5 else C_GREEN
        for s in composites
    ]

    fig_comp = go.Figure()
    fig_comp.add_trace(go.Bar(
        x=cp_order,
        y=composites,
        marker=dict(
            color=bar_colors_risk,
            opacity=0.85,
            line=dict(color="rgba(255,255,255,0.10)", width=1),
        ),
        text=[f"{s:.1f}" for s in composites],
        textposition="outside",
        textfont=dict(size=11, color=C_TEXT2),
        hovertemplate="<b>%{x}</b><br>Composite risk: %{y:.1f}/10<extra></extra>",
        showlegend=False,
    ))
    # Threshold lines
    for thresh, label, col in [(6.5, "HIGH", C_RED), (5.0, "MODERATE", C_WARN), (3.5, "LOW", C_GREEN)]:
        fig_comp.add_hline(
            y=thresh,
            line_color=col + "55",
            line_dash="dot",
            line_width=1.2,
            annotation_text=label,
            annotation_font=dict(size=9, color=col),
            annotation_position="right",
        )
    fig_comp.update_layout(
        height=300,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT2, size=11),
        margin=dict(l=0, r=40, t=28, b=60),
        xaxis=dict(showgrid=False, zeroline=False, tickfont=dict(size=9.5, color=C_TEXT2), tickangle=-22),
        yaxis=dict(
            range=[0, 10.5],
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(size=9, color=C_TEXT3),
            zeroline=False,
            title=dict(text="Composite Risk Score (1-10)", font=dict(size=10, color=C_TEXT3)),
        ),
        title=dict(text="Overall Composite Risk Score by Chokepoint", font=dict(size=12, color=C_TEXT2),
                   x=0.0, xanchor="left"),
    )
    st.plotly_chart(fig_comp, use_container_width=True, config={"displayModeBar": False}, key="chk_risk_composite")

    # Radar chart for top 4 riskiest
    st.markdown(
        f"<div style='font-size:0.70rem; text-transform:uppercase; letter-spacing:0.10em;"
        f" color:{C_TEXT3}; margin:16px 0 8px 0; font-weight:700'>"
        f"Multi-Dimensional Risk Radar — Top 4 Passages</div>",
        unsafe_allow_html=True,
    )
    top4_names = sorted(_RISK_FACTORS.keys(),
                        key=lambda n: -sum(_RISK_FACTORS[n].values()))[:4]
    radar_colors = [C_RED, C_ORANGE, C_WARN, C_BLUE]
    categories = _RISK_FACTOR_LABELS + [_RISK_FACTOR_LABELS[0]]  # close the polygon

    fig_radar = go.Figure()
    for name, col in zip(top4_names, radar_colors):
        vals = [_RISK_FACTORS[name].get(f, 0) for f in _RISK_FACTOR_LABELS]
        vals += [vals[0]]
        fig_radar.add_trace(go.Scatterpolar(
            r=vals,
            theta=categories,
            fill="toself",
            fillcolor=col + "18",
            line=dict(color=col, width=2),
            name=name,
            hovertemplate="<b>" + name + "</b><br>%{theta}: %{r:.1f}<extra></extra>",
        ))
    fig_radar.update_layout(
        height=360,
        paper_bgcolor="rgba(0,0,0,0)",
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(
                visible=True,
                range=[0, 10],
                tickfont=dict(size=9, color=C_TEXT3),
                gridcolor="rgba(255,255,255,0.07)",
                linecolor="rgba(255,255,255,0.07)",
            ),
            angularaxis=dict(
                tickfont=dict(size=10, color=C_TEXT2),
                gridcolor="rgba(255,255,255,0.07)",
                linecolor="rgba(255,255,255,0.07)",
            ),
        ),
        legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(10,15,26,0.6)"),
        margin=dict(l=40, r=40, t=30, b=30),
    )
    st.plotly_chart(fig_radar, use_container_width=True, config={"displayModeBar": False}, key="chk_risk_radar")


# ---------------------------------------------------------------------------
# Section 6: Historical Disruption Rate Impact
# ---------------------------------------------------------------------------

_DISRUPTION_IMPACTS: list[dict] = [
    {
        "event":     "Suez Canal — Ever Given Blockage (Mar 2021)",
        "chokepoint":"Suez Canal",
        "duration":  "6 days",
        "rate_spike":"+38%",
        "trade_loss": "$9.6B/day",
        "recovery":  "8 weeks",
        "color":     C_RED,
        "detail":    "400m vessel blocked entire canal. SCFI jumped 38% in 2 weeks. "
                     "Insurance premiums doubled. Cape routing surged briefly.",
    },
    {
        "event":     "Red Sea / Bab-el-Mandeb — Houthi Crisis (Nov 2023–present)",
        "chokepoint":"Suez Canal / Bab-el-Mandeb",
        "duration":  "26+ months",
        "rate_spike":"+412%",
        "trade_loss": "$1T annualised",
        "recovery":  "Ongoing",
        "color":     C_CRIMSON,
        "detail":    "Asia-Europe FBX03 surged from $850 to $5,200/FEU. 70% of vessels "
                     "rerouted via Cape, adding 9-10 days and $300-500/FEU. "
                     "War risk premiums rose 16x (5→82 bps).",
    },
    {
        "event":     "Strait of Hormuz — Iran Tanker Seizures (2019)",
        "chokepoint":"Strait of Hormuz",
        "duration":  "4 months",
        "rate_spike":"+55%",
        "trade_loss": "$200B at risk",
        "recovery":  "6 months",
        "color":     C_ORANGE,
        "detail":    "VLCC spot rates doubled. Oil tanker war risk premiums rose to 0.25% "
                     "of hull value. No viable alternative for Gulf petroleum flows.",
    },
    {
        "event":     "Panama Canal — Drought / Water Restrictions (Aug 2023–Feb 2024)",
        "chokepoint":"Panama Canal",
        "duration":  "7 months",
        "rate_spike":"+22%",
        "trade_loss": "$1B/month",
        "recovery":  "3 months",
        "color":     C_BLUE,
        "detail":    "Draft restrictions reduced capacity by 40%. Booking slot auctions reached "
                     "$4M+ per slot. LNG carriers particularly affected — some rerouted Suez.",
    },
    {
        "event":     "Strait of Malacca — Piracy Peak (2004–2005)",
        "chokepoint":"Strait of Malacca",
        "duration":  "18 months",
        "rate_spike":"+12%",
        "trade_loss": "$25B cargo at risk",
        "recovery":  "24 months",
        "color":     C_WARN,
        "detail":    "200+ incidents per year. Insurance war-risk premiums tripled. "
                     "IMO emergency protocols deployed. Coordinated naval patrols resolved crisis.",
    },
    {
        "event":     "Turkish Straits — Ukraine War (Feb 2022–present)",
        "chokepoint":"Turkish Straits",
        "duration":  "36+ months",
        "rate_spike":"+28%",
        "trade_loss": "$150B Black Sea trade disrupted",
        "recovery":  "Ongoing",
        "color":     C_INDIGO,
        "detail":    "Black Sea grain corridor suspended. Montreux Convention invoked; "
                     "warships blocked. Ukrainian grain exports fell 60% in 2022.",
    },
]


def _render_historical_impacts() -> None:
    _section_header(
        "Historical Disruption Rate Impacts",
        "What actually happened to freight rates when each chokepoint was disrupted",
        eyebrow="HISTORICAL ANALYSIS",
    )

    # Horizontal bar chart of rate spikes
    events_short = [d["event"].split("—")[0].strip() for d in _DISRUPTION_IMPACTS]
    spikes = [float(d["rate_spike"].replace("+", "").replace("%", "")) for d in _DISRUPTION_IMPACTS]
    colors_hist = [d["color"] for d in _DISRUPTION_IMPACTS]

    fig_hist_bar = go.Figure()
    fig_hist_bar.add_trace(go.Bar(
        y=events_short,
        x=spikes,
        orientation="h",
        marker=dict(
            color=colors_hist,
            opacity=0.80,
            line=dict(color="rgba(255,255,255,0.10)", width=1),
        ),
        text=[f"+{s:.0f}%" for s in spikes],
        textposition="outside",
        textfont=dict(size=10, color=C_TEXT2),
        hovertemplate="<b>%{y}</b><br>Rate spike: +%{x:.0f}%<extra></extra>",
        showlegend=False,
    ))
    fig_hist_bar.update_layout(
        height=300,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT2, size=11),
        margin=dict(l=0, r=60, t=10, b=10),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(size=9, color=C_TEXT3),
            zeroline=False,
            ticksuffix="%",
            title=dict(text="Peak Rate Spike (%)", font=dict(size=10, color=C_TEXT3)),
        ),
        yaxis=dict(showgrid=False, zeroline=False, tickfont=dict(size=9.5, color=C_TEXT2)),
    )
    st.plotly_chart(fig_hist_bar, use_container_width=True, config={"displayModeBar": False},
                    key="chk_hist_rate_spikes")

    # Detail cards
    st.markdown(
        f"<div style='font-size:0.70rem; text-transform:uppercase; letter-spacing:0.10em;"
        f" color:{C_TEXT3}; margin:16px 0 10px 0; font-weight:700'>Event Details</div>",
        unsafe_allow_html=True,
    )
    for i in range(0, len(_DISRUPTION_IMPACTS), 2):
        row = _DISRUPTION_IMPACTS[i:i + 2]
        dcols = st.columns(len(row), gap="small")
        for dcol, d in zip(dcols, row):
            with dcol:
                st.markdown(
                    f"<div style='background:{C_CARD}; border:1px solid {d['color']}40;"
                    f" border-left:3px solid {d['color']}; border-radius:10px; padding:14px 15px;"
                    f" margin-bottom:10px'>"
                    f"<div style='font-size:0.78rem; font-weight:700; color:{C_TEXT};"
                    f" margin-bottom:8px; line-height:1.4'>{d['event']}</div>"
                    f"<div style='display:flex; gap:12px; margin-bottom:10px; flex-wrap:wrap'>"
                    f"<div><span style='font-size:1.1rem; font-weight:800; color:{d['color']}'>"
                    f"{d['rate_spike']}</span><br>"
                    f"<span style='font-size:0.60rem; color:{C_TEXT3}; text-transform:uppercase;"
                    f" letter-spacing:0.06em'>rate spike</span></div>"
                    f"<div><span style='font-size:0.88rem; font-weight:700; color:{C_WARN}'>"
                    f"{d['trade_loss']}</span><br>"
                    f"<span style='font-size:0.60rem; color:{C_TEXT3}; text-transform:uppercase;"
                    f" letter-spacing:0.06em'>trade impact</span></div>"
                    f"<div><span style='font-size:0.88rem; font-weight:700; color:{C_TEXT2}'>"
                    f"{d['duration']}</span><br>"
                    f"<span style='font-size:0.60rem; color:{C_TEXT3}; text-transform:uppercase;"
                    f" letter-spacing:0.06em'>duration</span></div>"
                    f"<div><span style='font-size:0.88rem; font-weight:700; color:{C_GREEN}'>"
                    f"{d['recovery']}</span><br>"
                    f"<span style='font-size:0.60rem; color:{C_TEXT3}; text-transform:uppercase;"
                    f" letter-spacing:0.06em'>recovery</span></div>"
                    f"</div>"
                    f"<div style='font-size:0.70rem; color:{C_TEXT2}; line-height:1.55'>"
                    f"{d['detail']}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )


# ---------------------------------------------------------------------------
# Section 7: Alternative Routing Analysis
# ---------------------------------------------------------------------------

_ALT_ROUTING: list[dict] = [
    {
        "chokepoint":   "Suez Canal",
        "blocked_route":"Asia → Europe (direct)",
        "alternatives": [
            {"name": "Cape of Good Hope",    "extra_days": 9,  "cost_premium_pct": 35, "capacity_pct": 100},
            {"name": "Trans-Siberian Rail",   "extra_days": 0,  "cost_premium_pct": 80, "capacity_pct":  5},
        ],
        "risk_note": "Cape route viable but adds ~$300-500/FEU. Rail cannot handle bulk cargo.",
        "color": C_RED,
    },
    {
        "chokepoint":   "Strait of Hormuz",
        "blocked_route":"Gulf Oil → Asia/Europe",
        "alternatives": [
            {"name": "Saudi Arabia IPSA Pipeline",   "extra_days": 0, "cost_premium_pct": 25, "capacity_pct": 35},
            {"name": "UAE Habshan-Fujairah Pipeline", "extra_days": 0, "cost_premium_pct": 20, "capacity_pct": 25},
        ],
        "risk_note": "Pipelines handle only ~25-35% of current Hormuz oil volume. A full closure would be catastrophic.",
        "color": C_ORANGE,
    },
    {
        "chokepoint":   "Panama Canal",
        "blocked_route":"Asia → US East Coast",
        "alternatives": [
            {"name": "Suez Canal (eastbound)",    "extra_days": 22, "cost_premium_pct": 40, "capacity_pct": 100},
            {"name": "Cape Horn",                 "extra_days": 18, "cost_premium_pct": 30, "capacity_pct": 100},
            {"name": "US Rail (intermodal)",      "extra_days":  3, "cost_premium_pct": 55, "capacity_pct":  18},
        ],
        "risk_note": "Suez and Cape Horn are expensive but viable. US rail limited to intermodal containers.",
        "color": C_BLUE,
    },
    {
        "chokepoint":   "Strait of Malacca",
        "blocked_route":"Asia Intra-Regional / China → Europe",
        "alternatives": [
            {"name": "Lombok Strait (+2d)",       "extra_days": 2, "cost_premium_pct": 8,  "capacity_pct": 100},
            {"name": "Sunda Strait (+3d)",        "extra_days": 3, "cost_premium_pct": 10, "capacity_pct": 100},
            {"name": "Makassar Strait (+4d)",     "extra_days": 4, "cost_premium_pct": 12, "capacity_pct": 100},
        ],
        "risk_note": "Multiple Indonesian straits available. Draft restrictions apply for VLCCs in Lombok.",
        "color": C_GREEN,
    },
    {
        "chokepoint":   "Bab-el-Mandeb",
        "blocked_route":"Red Sea / Suez corridor",
        "alternatives": [
            {"name": "Cape of Good Hope",         "extra_days": 9,  "cost_premium_pct": 35, "capacity_pct": 100},
            {"name": "Suez Northern entry (land)", "extra_days": 0,  "cost_premium_pct": 5,  "capacity_pct":  20},
        ],
        "risk_note": "Effectively same as Suez closure — both must be clear for the corridor to work.",
        "color": C_CRIMSON,
    },
    {
        "chokepoint":   "Turkish Straits",
        "blocked_route":"Black Sea → Mediterranean",
        "alternatives": [
            {"name": "BTC Pipeline (Baku-Ceyhan)", "extra_days": 0, "cost_premium_pct": 30, "capacity_pct": 40},
            {"name": "Constanta-Trieste Rail/Road", "extra_days": 5, "cost_premium_pct": 60, "capacity_pct": 10},
        ],
        "risk_note": "Grain and steel cannot use pipelines. A full closure strands Black Sea exports.",
        "color": C_INDIGO,
    },
]


def _render_alternative_routing() -> None:
    _section_header(
        "Alternative Routing Analysis",
        "Viable detours if each chokepoint is blocked — extra transit days and cost premium",
        eyebrow="ROUTING INTELLIGENCE",
    )

    for item in _ALT_ROUTING:
        col = item["color"]
        alts = item["alternatives"]
        alts_html = ""
        for alt in alts:
            cap_color = C_GREEN if alt["capacity_pct"] >= 80 else C_WARN if alt["capacity_pct"] >= 30 else C_RED
            alts_html += (
                f"<div style='display:flex; align-items:center; gap:10px; padding:8px 0;"
                f" border-bottom:1px solid {C_BORDER}'>"
                f"<div style='flex:2; font-size:0.74rem; color:{C_TEXT}; font-weight:600'>"
                f"{alt['name']}</div>"
                f"<div style='flex:1; text-align:center'>"
                f"<span style='font-size:0.80rem; font-weight:700; color:{C_WARN}'>"
                f"+{alt['extra_days']}d</span>"
                f"<div style='font-size:0.58rem; color:{C_TEXT3}; text-transform:uppercase'>extra days</div>"
                f"</div>"
                f"<div style='flex:1; text-align:center'>"
                f"<span style='font-size:0.80rem; font-weight:700; color:{C_RED}'>"
                f"+{alt['cost_premium_pct']}%</span>"
                f"<div style='font-size:0.58rem; color:{C_TEXT3}; text-transform:uppercase'>cost premium</div>"
                f"</div>"
                f"<div style='flex:1; text-align:center'>"
                f"<span style='font-size:0.80rem; font-weight:700; color:{cap_color}'>"
                f"{alt['capacity_pct']}%</span>"
                f"<div style='font-size:0.58rem; color:{C_TEXT3}; text-transform:uppercase'>capacity</div>"
                f"</div>"
                f"</div>"
            )

        st.markdown(
            f"<div style='background:{C_CARD}; border:1px solid {col}30;"
            f" border-left:3px solid {col}; border-radius:10px; padding:14px 16px; margin-bottom:12px'>"
            f"<div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:10px'>"
            f"<div>"
            f"<span style='font-size:0.80rem; font-weight:700; color:{C_TEXT}'>{item['chokepoint']}</span>"
            f"<span style='font-size:0.66rem; color:{C_TEXT3}; margin-left:10px'>blocked route: "
            f"<b style='color:{C_TEXT2}'>{item['blocked_route']}</b></span>"
            f"</div>"
            f"<span style='font-size:0.62rem; color:{col}; background:{col}18; padding:2px 8px;"
            f" border-radius:999px; font-weight:600'>{len(alts)} alternative(s)</span>"
            f"</div>"
            f"<div style='display:flex; font-size:0.62rem; color:{C_TEXT3}; font-weight:700;"
            f" text-transform:uppercase; letter-spacing:0.06em; padding-bottom:4px;"
            f" border-bottom:1px solid {C_BORDER}'>"
            f"<div style='flex:2'>Alternative Route</div>"
            f"<div style='flex:1; text-align:center'>Extra Days</div>"
            f"<div style='flex:1; text-align:center'>Cost Premium</div>"
            f"<div style='flex:1; text-align:center'>Capacity</div>"
            f"</div>"
            f"{alts_html}"
            f"<div style='margin-top:9px; font-size:0.68rem; color:{C_TEXT2}; line-height:1.5'>"
            f"{item['risk_note']}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Section 8: Insurance Rate Impact
# ---------------------------------------------------------------------------

# War risk / marine insurance premiums by chokepoint, as basis points of hull value
_INSURANCE_DATA: list[dict] = [
    {
        "chokepoint": "Red Sea / Bab-el-Mandeb",
        "pre_crisis_bps":  5,
        "current_bps":    65,
        "peak_bps":       85,
        "trend":          "easing",
        "coverage_note":  "War risk mandatory for Red Sea transits. Many P&I clubs add $50-100K per call surcharge.",
        "color":          C_CRIMSON,
    },
    {
        "chokepoint": "Strait of Hormuz",
        "pre_crisis_bps": 10,
        "current_bps":    35,
        "peak_bps":       60,
        "trend":          "stable",
        "coverage_note":  "AWRP (Additional War Risk Premium) applied for all Gulf ports of call.",
        "color":          C_ORANGE,
    },
    {
        "chokepoint": "Black Sea / Turkish Straits",
        "pre_crisis_bps":  3,
        "current_bps":    55,
        "peak_bps":       75,
        "trend":          "stable",
        "coverage_note":  "JWC listed area since Feb 2022. Specialist war risk underwriters only.",
        "color":          C_INDIGO,
    },
    {
        "chokepoint": "Suez Canal",
        "pre_crisis_bps":  2,
        "current_bps":    18,
        "peak_bps":        25,
        "trend":           "easing",
        "coverage_note":   "Not JWC listed (Suez itself safe). Elevated due to Red Sea approach risk.",
        "color":           C_RED,
    },
    {
        "chokepoint": "Strait of Malacca",
        "pre_crisis_bps":  2,
        "current_bps":     3,
        "peak_bps":        8,
        "trend":           "stable",
        "coverage_note":   "IMB Category 1 alert area. Piracy loadings apply but modest.",
        "color":           C_WARN,
    },
    {
        "chokepoint": "Panama Canal",
        "pre_crisis_bps":  1,
        "current_bps":     2,
        "peak_bps":        2,
        "trend":           "stable",
        "coverage_note":   "No war risk loading. Pure marine perils — grounding risk elevated in drought.",
        "color":           C_BLUE,
    },
]


def _render_insurance_impact() -> None:
    _section_header(
        "Insurance Rate Impact",
        "War risk and marine insurance premiums influenced by chokepoint risk — basis points of hull value",
        eyebrow="INSURANCE ANALYTICS",
    )

    # Summary comparison chart
    cp_ins_names = [d["chokepoint"] for d in _INSURANCE_DATA]
    pre_bps   = [d["pre_crisis_bps"] for d in _INSURANCE_DATA]
    curr_bps  = [d["current_bps"] for d in _INSURANCE_DATA]
    peak_bps  = [d["peak_bps"] for d in _INSURANCE_DATA]

    fig_ins = go.Figure()
    fig_ins.add_trace(go.Bar(
        name="Pre-Crisis",
        x=cp_ins_names,
        y=pre_bps,
        marker=dict(color=C_GREEN, opacity=0.7, line=dict(color="rgba(255,255,255,0.08)", width=1)),
        hovertemplate="<b>%{x}</b><br>Pre-crisis: %{y} bps<extra></extra>",
    ))
    fig_ins.add_trace(go.Bar(
        name="Current",
        x=cp_ins_names,
        y=curr_bps,
        marker=dict(color=C_BLUE, opacity=0.8, line=dict(color="rgba(255,255,255,0.08)", width=1)),
        hovertemplate="<b>%{x}</b><br>Current: %{y} bps<extra></extra>",
    ))
    fig_ins.add_trace(go.Bar(
        name="Peak",
        x=cp_ins_names,
        y=peak_bps,
        marker=dict(color=C_RED, opacity=0.6, line=dict(color="rgba(255,255,255,0.08)", width=1)),
        hovertemplate="<b>%{x}</b><br>Peak: %{y} bps<extra></extra>",
    ))
    fig_ins.update_layout(
        barmode="group",
        height=320,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT2, size=11),
        margin=dict(l=0, r=0, t=28, b=60),
        xaxis=dict(showgrid=False, zeroline=False, tickfont=dict(size=9.5, color=C_TEXT2), tickangle=-22),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(size=9, color=C_TEXT3),
            zeroline=False,
            ticksuffix=" bps",
            title=dict(text="War Risk Premium (bps of hull value)", font=dict(size=10, color=C_TEXT3)),
        ),
        legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(10,15,26,0.65)"),
        title=dict(text="War Risk Insurance Premiums: Pre-Crisis vs Current vs Peak",
                   font=dict(size=12, color=C_TEXT2), x=0.0, xanchor="left"),
    )
    st.plotly_chart(fig_ins, use_container_width=True, config={"displayModeBar": False}, key="chk_insurance_bar")

    # Detail cards in 2-column grid
    st.markdown(
        f"<div style='font-size:0.70rem; text-transform:uppercase; letter-spacing:0.10em;"
        f" color:{C_TEXT3}; margin:14px 0 10px 0; font-weight:700'>Coverage Details</div>",
        unsafe_allow_html=True,
    )
    for i in range(0, len(_INSURANCE_DATA), 2):
        row = _INSURANCE_DATA[i:i + 2]
        icols = st.columns(len(row), gap="small")
        for icol, d in zip(icols, row):
            with icol:
                multiplier = d["current_bps"] / max(d["pre_crisis_bps"], 0.5)
                mult_color = C_RED if multiplier >= 10 else C_ORANGE if multiplier >= 5 else C_WARN
                trend_arrow = "↘" if d["trend"] == "easing" else "→" if d["trend"] == "stable" else "↗"
                trend_color = C_GREEN if d["trend"] == "easing" else C_TEXT2
                st.markdown(
                    f"<div style='background:{C_CARD}; border:1px solid {d['color']}35;"
                    f" border-radius:10px; padding:14px 15px; margin-bottom:10px'>"
                    f"<div style='font-size:0.78rem; font-weight:700; color:{C_TEXT}; margin-bottom:10px'>"
                    f"{d['chokepoint']}</div>"
                    f"<div style='display:flex; gap:10px; margin-bottom:10px; flex-wrap:wrap'>"
                    f"<div style='text-align:center; flex:1'>"
                    f"<div style='font-size:0.60rem; color:{C_TEXT3}; text-transform:uppercase;"
                    f" letter-spacing:0.06em; margin-bottom:3px'>Pre-Crisis</div>"
                    f"<div style='font-size:1.0rem; font-weight:800; color:{C_GREEN}'>{d['pre_crisis_bps']} bps</div>"
                    f"</div>"
                    f"<div style='text-align:center; flex:1'>"
                    f"<div style='font-size:0.60rem; color:{C_TEXT3}; text-transform:uppercase;"
                    f" letter-spacing:0.06em; margin-bottom:3px'>Current</div>"
                    f"<div style='font-size:1.0rem; font-weight:800; color:{d['color']}'>{d['current_bps']} bps</div>"
                    f"</div>"
                    f"<div style='text-align:center; flex:1'>"
                    f"<div style='font-size:0.60rem; color:{C_TEXT3}; text-transform:uppercase;"
                    f" letter-spacing:0.06em; margin-bottom:3px'>Peak</div>"
                    f"<div style='font-size:0.90rem; font-weight:700; color:{C_TEXT2}'>{d['peak_bps']} bps</div>"
                    f"</div>"
                    f"<div style='text-align:center; flex:1'>"
                    f"<div style='font-size:0.60rem; color:{C_TEXT3}; text-transform:uppercase;"
                    f" letter-spacing:0.06em; margin-bottom:3px'>Multiplier</div>"
                    f"<div style='font-size:1.0rem; font-weight:800; color:{mult_color}'>"
                    f"{multiplier:.0f}x</div>"
                    f"</div>"
                    f"</div>"
                    f"<div style='display:flex; justify-content:space-between; align-items:center;"
                    f" margin-bottom:8px'>"
                    f"<span style='font-size:0.65rem; color:{C_TEXT3}'>Trend:</span>"
                    f"<span style='font-size:0.72rem; font-weight:700; color:{trend_color}'>"
                    f"{trend_arrow} {d['trend'].upper()}</span>"
                    f"</div>"
                    f"<div style='font-size:0.67rem; color:{C_TEXT2}; line-height:1.5;"
                    f" border-top:1px solid {C_BORDER}; padding-top:8px'>"
                    f"{d['coverage_note']}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )


# ---------------------------------------------------------------------------
# Section 9: Closure Impact Simulator (interactive)
# ---------------------------------------------------------------------------

def _render_closure_simulator() -> None:
    _section_header(
        "Closure Impact Simulator",
        "Model cascading effects of a chokepoint closure on freight rates, trade flows, and rerouting costs",
        eyebrow="SCENARIO MODELLING",
    )

    cp_names = [cp.name for cp in CHOKEPOINTS.values()]
    cp_keys  = list(CHOKEPOINTS.keys())

    if not cp_names:
        st.info("No chokepoints available for simulation.", icon="ℹ️")
        return

    ctrl_col, sim_col = st.columns([1, 2], gap="medium")

    with ctrl_col:
        st.markdown(
            f"<div style='background:{C_CARD2}; border:1px solid {C_BORDER2}; border-radius:12px;"
            f" padding:18px 16px'>"
            f"<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.10em;"
            f" color:{C_TEXT3}; margin-bottom:12px; font-weight:700'>Simulation Controls</div>",
            unsafe_allow_html=True,
        )
        selected_name = st.selectbox(
            "Chokepoint",
            cp_names,
            index=min(1, len(cp_names) - 1),
            key="chk_sim_select",
            label_visibility="visible",
        )
        duration_weeks = st.slider(
            "Closure duration (weeks)",
            min_value=1,
            max_value=52,
            value=4,
            step=1,
            key="chk_sim_duration",
        )
        st.markdown("</div>", unsafe_allow_html=True)

    selected_key = cp_keys[cp_names.index(selected_name)]
    result = simulate_chokepoint_closure(selected_key, duration_weeks)
    cp = CHOKEPOINTS[selected_key]

    with sim_col:
        level = cp.current_risk_level
        col_level = _RISK_COLS.get(level, C_TEXT2)

        # KPI row
        kpi1, kpi2, kpi3, kpi4 = st.columns(4, gap="small")
        reroute_m = result["rerouting_cost_total_usd"] / 1_000_000

        for kcol, val, label, color in [
            (kpi1, f"+{result['rate_impact_pct']}%",          "Rate Spike",          C_RED),
            (kpi2, f"{result['global_trade_impact_pct']}%",   "Trade Disrupted",     C_ORANGE),
            (kpi3, f"${reroute_m:.0f}M",                       "Rerouting Cost",      C_WARN),
            (kpi4, f"{result.get('extra_days_if_closed', 0)}d","Extra Transit Days",  C_TEXT2),
        ]:
            kcol.markdown(
                f"<div style='background:{C_CARD}; border:1px solid {C_BORDER};"
                f" border-radius:10px; padding:14px; text-align:center'>"
                f"<div style='font-size:0.60rem; text-transform:uppercase; letter-spacing:0.10em;"
                f" color:{C_TEXT3}; margin-bottom:6px'>{label}</div>"
                f"<div style='font-size:1.55rem; font-weight:900; color:{color}'>{val}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # Feasibility note
        st.markdown(
            f"<div style='background:rgba(59,130,246,0.05); border-left:3px solid {C_BLUE};"
            f" border-radius:0 8px 8px 0; padding:10px 14px; margin-top:12px;"
            f" font-size:0.76rem; color:{C_TEXT2}; line-height:1.55'>"
            f"{result['feasibility_note']}</div>",
            unsafe_allow_html=True,
        )

    # Affected routes / alternatives
    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)
    affected = result.get("affected_routes", [])
    alts     = result.get("alternative_routes", [])
    r_col, a_col = st.columns(2, gap="medium")

    with r_col:
        st.markdown(
            f"<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.10em;"
            f" color:{C_TEXT3}; margin-bottom:8px; font-weight:700'>Affected Trade Lanes</div>",
            unsafe_allow_html=True,
        )
        if affected:
            for r in affected:
                st.markdown(
                    f"<div style='padding:6px 0; border-bottom:1px solid {C_BORDER};"
                    f" font-size:0.75rem; color:{C_RED}; display:flex; align-items:center; gap:8px'>"
                    f"<span style='font-size:0.7rem'>&#9679;</span>"
                    f"{r.replace('_', ' ').title()}</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                f"<div style='color:{C_TEXT3}; font-size:0.76rem'>No specific lanes listed.</div>",
                unsafe_allow_html=True,
            )

    with a_col:
        st.markdown(
            f"<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.10em;"
            f" color:{C_TEXT3}; margin-bottom:8px; font-weight:700'>Alternative Routing Options</div>",
            unsafe_allow_html=True,
        )
        if alts:
            for a in alts:
                st.markdown(
                    f"<div style='padding:6px 0; border-bottom:1px solid {C_BORDER};"
                    f" font-size:0.75rem; color:{C_GREEN}; display:flex; align-items:center; gap:8px'>"
                    f"<span>&#8594;</span>{a}</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                f"<div style='color:{C_RED}; font-size:0.76rem; font-weight:700'>"
                f"No viable alternative — closure would be catastrophic.</div>",
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Red Sea Crisis Tracker (dedicated deep-dive)
# ---------------------------------------------------------------------------

_RED_SEA_INCIDENTS = [
    ("2023-11-19", "Houthis seize Galaxy Leader — first vessel seizure"),
    ("2023-12-09", "MSC Palatium III missile attack; carriers begin Red Sea avoidance"),
    ("2023-12-18", "Operation Prosperity Guardian announced by US-led coalition"),
    ("2024-01-09", "Largest Houthi drone swarm attack on commercial shipping"),
    ("2024-01-11", "US + UK strike Houthi land-based targets in Yemen"),
    ("2024-01-12", "Maersk, Hapag-Lloyd, MSC all indefinitely suspend Red Sea transits"),
    ("2024-02-18", "UK-flagged Rubymar struck, sinks — first vessel lost in crisis"),
    ("2024-03-06", "FBX03 Asia-Europe rate reaches 4-year high; +280% vs Oct 2023"),
    ("2024-05-14", "Houthis expand attacks to vessels with no Israel connection"),
    ("2024-09-21", "US carrier strike group rotation; Houthi attacks continue unabated"),
    ("2025-01-19", "Gaza ceasefire; Houthis announce pause — rates begin softening"),
    ("2025-03-12", "Houthi attacks resume after ceasefire tensions escalate"),
    ("2026-01-01", "Red Sea remains HIGH risk; ~70% of traffic still rerouting Cape"),
]

_CAPE_VESSELS = [
    12, 14, 18, 25, 38, 52, 68, 78, 84, 89, 91, 93, 94, 95, 94, 93,
    91, 90, 88, 87, 85, 83, 82, 81, 80, 79, 78, 77, 76, 75, 74, 73,
]
_SUEZ_VESSELS = [
    92, 88, 80, 68, 50, 38, 28, 20, 17, 14, 12, 11, 10, 10, 11, 12,
    13, 13, 14, 14, 15, 16, 16, 17, 18, 18, 19, 20, 20, 21, 22, 22,
]
_FBX03 = [
    850, 920, 1200, 1750, 2400, 3400, 4600, 5100, 5200, 4900, 4700,
    4500, 4300, 4100, 3900, 3800, 3700, 3600, 3400, 3200, 3000, 2900,
    2800, 2750, 2700, 2650, 2600, 2550, 2450, 2350, 2300, 2250,
]
_WAR_RISK_BPS = [
    5, 6, 8, 12, 22, 38, 55, 65, 72, 78, 82, 85, 85, 84, 83,
    82, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68, 67, 66, 65,
]


def _render_red_sea_tracker() -> None:
    _section_header(
        "Red Sea Crisis Tracker",
        "Active disruption — Houthi missile/drone attacks forcing Cape of Good Hope rerouting since Nov 2023",
        eyebrow="ACTIVE CRISIS",
    )

    # Alert banner
    st.markdown(
        f"<div style='background:rgba(239,68,68,0.08); border:1px solid {C_RED}44;"
        f" border-radius:10px; padding:14px 18px; margin-bottom:16px;"
        f" display:flex; align-items:center; gap:14px'>"
        f"<div class='chk-pulse-badge' style='width:10px; height:10px; border-radius:50%;"
        f" background:{C_RED}; flex-shrink:0'></div>"
        f"<div>"
        f"<div style='font-size:0.78rem; font-weight:700; color:{C_RED}; margin-bottom:3px'>"
        f"ACTIVE DISRUPTION — Red Sea / Bab-el-Mandeb</div>"
        f"<div style='font-size:0.70rem; color:{C_TEXT2}; line-height:1.5'>"
        f"~70% of Asia-Europe container traffic rerouted via Cape of Good Hope. "
        f"Suez Canal transit volumes ~75% below pre-crisis levels. "
        f"War risk insurance premiums 13x pre-crisis baseline."
        f"</div></div></div>",
        unsafe_allow_html=True,
    )

    # Incident timeline
    st.markdown(
        f"<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.10em;"
        f" color:{C_TEXT3}; margin-bottom:10px; font-weight:700'>Key Incidents Timeline</div>",
        unsafe_allow_html=True,
    )
    n = len(_RED_SEA_INCIDENTS)
    dates  = [item[0] for item in _RED_SEA_INCIDENTS]
    labels = [item[1] for item in _RED_SEA_INCIDENTS]
    y_vals = [1] * n

    fig_tl = go.Figure()
    fig_tl.add_trace(go.Scatter(
        x=dates, y=y_vals,
        mode="markers+lines",
        line=dict(color="rgba(239,68,68,0.3)", width=1.5, dash="dot"),
        marker=dict(
            size=9,
            color=[C_RED if i % 2 == 0 else C_ORANGE for i in range(n)],
            line=dict(color="rgba(255,255,255,0.28)", width=1),
        ),
        text=labels,
        hovertemplate="<b>%{x}</b><br>%{text}<extra></extra>",
        showlegend=False,
    ))
    for i in range(0, n, 3):
        short = labels[i][:44] + ("..." if len(labels[i]) > 44 else "")
        fig_tl.add_annotation(
            x=dates[i], y=1.0, text=short,
            showarrow=True, arrowhead=2,
            arrowcolor="rgba(148,163,184,0.45)", arrowsize=0.8,
            ax=0, ay=-36 if i % 2 == 0 else 36,
            font=dict(size=9, color=C_TEXT2),
            bgcolor="rgba(10,15,26,0.88)",
            bordercolor="rgba(255,255,255,0.10)",
            borderwidth=1, borderpad=3,
        )
    fig_tl.update_layout(
        height=210,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=20, b=44),
        xaxis=dict(showgrid=False, zeroline=False, tickfont=dict(color=C_TEXT3, size=9.5), tickangle=-28),
        yaxis=dict(visible=False, range=[0.75, 1.5]),
    )
    st.plotly_chart(fig_tl, use_container_width=True, config={"displayModeBar": False},
                    key="chk_red_sea_timeline")

    # Cape vs Suez + FBX + War Risk in a 3-column layout
    weeks = [f"Wk {i + 1}" for i in range(len(_CAPE_VESSELS))]

    c1, c2, c3 = st.columns(3, gap="medium")

    with c1:
        st.markdown(
            f"<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.10em;"
            f" color:{C_TEXT3}; margin-bottom:8px; font-weight:700'>Cape vs Suez Weekly Vessels</div>",
            unsafe_allow_html=True,
        )
        fig_cv = go.Figure()
        fig_cv.add_trace(go.Scatter(
            x=weeks, y=_CAPE_VESSELS, mode="lines+markers",
            name="Cape of Good Hope",
            line=dict(color=C_ORANGE, width=2.2), marker=dict(size=4, color=C_ORANGE),
            fill="tozeroy", fillcolor="rgba(249,115,22,0.09)",
            hovertemplate="Cape: %{y}<extra></extra>",
        ))
        fig_cv.add_trace(go.Scatter(
            x=weeks, y=_SUEZ_VESSELS, mode="lines+markers",
            name="Suez / Red Sea",
            line=dict(color=C_BLUE, width=2.2), marker=dict(size=4, color=C_BLUE),
            fill="tozeroy", fillcolor="rgba(59,130,246,0.09)",
            hovertemplate="Suez: %{y}<extra></extra>",
        ))
        fig_cv.update_layout(
            height=230, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10), margin=dict(l=0, r=0, t=4, b=30),
            xaxis=dict(showgrid=False, tickfont=dict(size=8, color=C_TEXT3), zeroline=False),
            yaxis=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(size=8, color=C_TEXT3),
                       zeroline=False, title=dict(text="Vessels/wk", font=dict(size=9, color=C_TEXT3))),
            legend=dict(font=dict(color=C_TEXT2, size=9), bgcolor="rgba(10,15,26,0.6)"),
        )
        st.plotly_chart(fig_cv, use_container_width=True, config={"displayModeBar": False},
                        key="chk_red_sea_vessel_cmp")

    with c2:
        st.markdown(
            f"<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.10em;"
            f" color:{C_TEXT3}; margin-bottom:8px; font-weight:700'>FBX03 Asia-Europe ($/FEU)</div>",
            unsafe_allow_html=True,
        )
        fig_fbx = go.Figure()
        fig_fbx.add_trace(go.Scatter(
            x=weeks, y=_FBX03, mode="lines",
            line=dict(color=C_RED, width=2.2),
            fill="tozeroy", fillcolor="rgba(239,68,68,0.09)",
            hovertemplate="$%{y:,.0f}/FEU<extra></extra>", showlegend=False,
        ))
        fig_fbx.add_hline(y=850, line_color=C_GREEN + "60", line_dash="dash", line_width=1.2,
                          annotation_text="Baseline $850", annotation_font=dict(color=C_GREEN, size=8.5))
        fig_fbx.update_layout(
            height=230, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10), margin=dict(l=0, r=0, t=4, b=30),
            xaxis=dict(showgrid=False, tickfont=dict(size=8, color=C_TEXT3), zeroline=False),
            yaxis=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(size=8, color=C_TEXT3),
                       zeroline=False, tickprefix="$"),
        )
        st.plotly_chart(fig_fbx, use_container_width=True, config={"displayModeBar": False},
                        key="chk_red_sea_fbx")

    with c3:
        st.markdown(
            f"<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.10em;"
            f" color:{C_TEXT3}; margin-bottom:8px; font-weight:700'>War Risk Premium (bps hull)</div>",
            unsafe_allow_html=True,
        )
        fig_wr = go.Figure()
        fig_wr.add_trace(go.Scatter(
            x=weeks, y=_WAR_RISK_BPS, mode="lines",
            line=dict(color=C_WARN, width=2.2),
            fill="tozeroy", fillcolor="rgba(245,158,11,0.09)",
            hovertemplate="%{y} bps<extra></extra>", showlegend=False,
        ))
        fig_wr.add_hline(y=5, line_color=C_GREEN + "60", line_dash="dash", line_width=1.2,
                         annotation_text="Pre-crisis ~5 bps", annotation_font=dict(color=C_GREEN, size=8.5))
        fig_wr.update_layout(
            height=230, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10), margin=dict(l=0, r=0, t=4, b=30),
            xaxis=dict(showgrid=False, tickfont=dict(size=8, color=C_TEXT3), zeroline=False),
            yaxis=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(size=8, color=C_TEXT3),
                       zeroline=False, ticksuffix=" bps"),
        )
        st.plotly_chart(fig_wr, use_container_width=True, config={"displayModeBar": False},
                        key="chk_red_sea_war_risk")


# ---------------------------------------------------------------------------
# Historical Events Timeline (2004-2026)
# ---------------------------------------------------------------------------

_HISTORICAL_EVENTS = [
    ("2004-06-01", "Malacca piracy peak — 200+ incidents, IMO emergency response",       "HIGH"),
    ("2008-11-01", "Somali piracy escalation — Gulf of Aden attacks surge",              "HIGH"),
    ("2011-03-01", "Somali piracy peak — 151 attacks, 29 ships hijacked",               "HIGH"),
    ("2013-01-01", "International naval patrols drastically cut Somali piracy",          "LOW"),
    ("2016-07-15", "South China Sea arbitration ruling; Malacca tensions spike briefly", "MODERATE"),
    ("2019-06-13", "Gulf of Oman tanker attacks; Hormuz tensions spike",                 "HIGH"),
    ("2021-03-23", "Ever Given blocks Suez Canal — 6-day closure, $9.6B/day halted",    "CRITICAL"),
    ("2021-03-29", "Ever Given refloated; trade restored",                               "LOW"),
    ("2022-02-24", "Russia invades Ukraine; Baltic/Black Sea trade disruptions",         "HIGH"),
    ("2023-08-01", "Panama Canal drought — deepest water restrictions in decades",       "MODERATE"),
    ("2023-11-19", "Houthi Red Sea attacks begin — Asia-Europe crisis starts",           "CRITICAL"),
    ("2024-01-12", "Major carriers abandon Red Sea routes",                              "CRITICAL"),
    ("2024-03-26", "Francis Scott Key Bridge collapse; Baltimore port closure",          "HIGH"),
    ("2025-01-19", "Gaza ceasefire — Houthi attacks pause temporarily",                 "MODERATE"),
    ("2026-01-01", "Red Sea remains HIGH risk; Cape rerouting semi-permanent",           "HIGH"),
]

_RISK_Y = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "LOW": 1}


def _render_historical_timeline() -> None:
    _section_header(
        "Historical Chokepoint Events 2004–2026",
        "Major disruptions by severity — hover each marker for event details",
        eyebrow="HISTORICAL CONTEXT",
    )

    dates  = [e[0] for e in _HISTORICAL_EVENTS]
    descs  = [e[1] for e in _HISTORICAL_EVENTS]
    risks  = [e[2] for e in _HISTORICAL_EVENTS]
    ys     = [_RISK_Y[r] for r in risks]
    colors = [_RISK_COLS[r] for r in risks]

    fig = go.Figure()

    # Risk level bands
    for level, y_val in _RISK_Y.items():
        fig.add_hrect(
            y0=y_val - 0.38,
            y1=y_val + 0.38,
            fillcolor=_RISK_COLS[level] + "0b",
            line_width=0,
        )
        fig.add_annotation(
            x="2004-01-01",
            y=y_val,
            text=level,
            showarrow=False,
            font=dict(size=9, color=_RISK_COLS[level] + "bb"),
            xref="x",
            yref="y",
            xanchor="left",
        )

    fig.add_trace(go.Scatter(
        x=dates, y=ys,
        mode="markers",
        marker=dict(
            size=14,
            color=colors,
            line=dict(color="rgba(255,255,255,0.28)", width=1.5),
            symbol="circle",
        ),
        text=descs,
        customdata=risks,
        hovertemplate="<b>%{x}</b><br>%{text}<br>Severity: <b>%{customdata}</b><extra></extra>",
        showlegend=False,
    ))

    # Annotate key events
    notable_indices = [6, 10, 11, 8]
    for i in notable_indices:
        if i >= len(dates):
            continue
        short = descs[i][:42] + ("..." if len(descs[i]) > 42 else "")
        fig.add_annotation(
            x=dates[i], y=ys[i], text=short,
            showarrow=True, arrowhead=2,
            arrowcolor="rgba(148,163,184,0.4)", arrowsize=0.7,
            ax=0, ay=-40 if ys[i] >= 3 else 40,
            font=dict(size=8.5, color=C_TEXT2),
            bgcolor="rgba(10,15,26,0.90)",
            bordercolor="rgba(255,255,255,0.10)",
            borderwidth=1, borderpad=3,
        )

    fig.update_layout(
        height=340,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT2, size=11),
        margin=dict(l=0, r=0, t=12, b=44),
        xaxis=dict(
            showgrid=False, zeroline=False,
            tickfont=dict(size=10, color=C_TEXT3),
            tickangle=-28,
        ),
        yaxis=dict(
            tickvals=[1, 2, 3, 4],
            ticktext=["LOW", "MOD", "HIGH", "CRIT"],
            tickfont=dict(size=9.5, color=C_TEXT2),
            gridcolor="rgba(255,255,255,0.04)",
            zeroline=False, range=[0.4, 4.8],
        ),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key="chk_historical_timeline")


# ---------------------------------------------------------------------------
# Full Risk Dashboard (all 9 chokepoints, 3x3 card grid)
# ---------------------------------------------------------------------------

def _render_full_risk_dashboard() -> None:
    _section_header(
        "Full Chokepoint Risk Dashboard",
        "All 9 critical maritime passages — current status, traffic, and closure impact",
        eyebrow="RISK DASHBOARD",
    )

    _cp_list = sorted(CHOKEPOINTS.values(), key=lambda c: _RISK_ORDER.get(c.current_risk_level, 99))
    rows = [_cp_list[i:i + 3] for i in range(0, len(_cp_list), 3)]

    for row in rows:
        cols = st.columns(3, gap="small")
        for col, cp in zip(cols, row):
            with col:
                level = cp.current_risk_level
                color = _RISK_COLS.get(level, C_TEXT2)
                is_critical = level in ("CRITICAL", "HIGH")
                border_col = color + "50"
                is_active = cp.current_disruption_type != "NONE"

                disruption_html = ""
                if is_active:
                    dtype_labels = {
                        "ACTIVE_CONFLICT": "Active conflict disruption",
                        "DIPLOMATIC":      "Diplomatic tension",
                        "WEATHER":         "Weather constraints",
                        "CONGESTION":      "Traffic congestion",
                    }
                    since_html = f" since {cp.disruption_since}" if cp.disruption_since else ""
                    disruption_html = (
                        f"<div style='background:rgba(239,68,68,0.06); border-left:2px solid {color}60;"
                        f" padding:6px 8px; border-radius:0 6px 6px 0; margin-top:9px;"
                        f" font-size:0.68rem; color:{C_TEXT2}; line-height:1.4'>"
                        + dtype_labels.get(cp.current_disruption_type, cp.current_disruption_type)
                        + since_html + "</div>"
                    )

                if cp.strategic_alternatives:
                    alt_html = (
                        f"<div style='font-size:0.65rem; color:{C_TEXT3}; margin-top:6px'>"
                        f"Alt: {cp.strategic_alternatives[0][:48]}</div>"
                    )
                else:
                    alt_html = (
                        f"<div style='font-size:0.65rem; color:{C_RED}; margin-top:6px; font-weight:600'>"
                        f"No viable alternative route</div>"
                    )

                st.markdown(
                    f"<div style='background:{C_CARD2}; border:1px solid {border_col};"
                    f" border-radius:12px; padding:16px 15px; height:100%'>"
                    f"<div style='font-size:0.79rem; font-weight:700; color:{C_TEXT}; margin-bottom:8px'>"
                    f"{cp.name}</div>"
                    f"<div>{_risk_badge(level, pulse=is_critical)}"
                    f"{_disruption_badge(cp.current_disruption_type)}</div>"
                    f"<div style='margin-top:11px; display:flex; gap:14px; padding-top:10px;"
                    f" border-top:1px solid {C_BORDER}'>"
                    f"{_metric_pill(str(cp.daily_vessels), 'vessels/d', C_TEXT)}"
                    f"{_metric_pill(str(cp.pct_global_trade) + '%', 'trade', C_WARN)}"
                    f"{_metric_pill(str(cp.extra_days_if_closed) + 'd', 'if closed', C_ORANGE)}"
                    f"</div>"
                    f"{disruption_html}{alt_html}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        st.markdown("<div style='margin-bottom:8px'></div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# NEW Section 0: Live Intelligence Strip
# ---------------------------------------------------------------------------

def _render_live_intelligence_strip() -> None:
    """
    A Bloomberg-style top ticker bar showing real-time chokepoint KPIs:
    active disruptions count, total daily TEU at risk, global trade % exposed,
    and a traffic-light severity gauge for each primary chokepoint.
    """
    # Aggregate stats
    total_vessels_at_risk = 0
    total_teu_at_risk     = 0.0
    trade_pct_at_risk     = 0.0
    n_critical = n_high = n_moderate = n_low = 0

    for cp in CHOKEPOINTS.values():
        lvl = cp.current_risk_level
        if lvl == "CRITICAL":
            n_critical += 1
            total_vessels_at_risk += cp.daily_vessels
            total_teu_at_risk     += getattr(cp, "daily_teu_m", 0.0)
            trade_pct_at_risk     += cp.pct_global_trade
        elif lvl == "HIGH":
            n_high += 1
            total_vessels_at_risk += cp.daily_vessels
            total_teu_at_risk     += getattr(cp, "daily_teu_m", 0.0) * 0.6
            trade_pct_at_risk     += cp.pct_global_trade * 0.6
        elif lvl == "MODERATE":
            n_moderate += 1
        else:
            n_low += 1

    trade_pct_at_risk = min(trade_pct_at_risk, 99.0)

    # Severity bar — 4 segments
    sev_items = [
        (n_critical, "CRITICAL", C_RED),
        (n_high,     "HIGH",     C_ORANGE),
        (n_moderate, "MODERATE", C_WARN),
        (n_low,      "LOW",      C_GREEN),
    ]
    sev_html = "".join(
        f"<div style='display:flex; align-items:center; gap:5px; padding:0 8px;"
        f" border-left:1px solid {C_BORDER}'>"
        f"<span style='width:8px; height:8px; border-radius:50%;"
        f" background:{col}; flex-shrink:0; display:inline-block'></span>"
        f"<span style='font-size:0.80rem; font-weight:800; color:{col}'>{n}</span>"
        f"<span style='font-size:0.60rem; color:{C_TEXT3}; text-transform:uppercase;"
        f" letter-spacing:0.06em'>{lbl}</span></div>"
        for n, lbl, col in sev_items
    )

    kpi_items = [
        (f"{total_vessels_at_risk:,}", "VESSELS AT RISK / DAY", C_RED    if n_critical else C_ORANGE),
        (f"{total_teu_at_risk:.1f}M",  "TEU AT RISK / DAY",     C_ORANGE if n_critical else C_WARN),
        (f"{trade_pct_at_risk:.0f}%",  "GLOBAL TRADE EXPOSED",  C_WARN),
        (f"{len(CHOKEPOINTS)}",        "PASSAGES MONITORED",    C_BLUE),
    ]
    kpi_html = "".join(
        f"<div style='text-align:center; padding:0 16px; border-left:1px solid {C_BORDER}'>"
        f"<div style='font-size:1.20rem; font-weight:900; color:{col};"
        f" font-variant-numeric:tabular-nums; line-height:1'>{val}</div>"
        f"<div style='font-size:0.55rem; color:{C_TEXT3}; text-transform:uppercase;"
        f" letter-spacing:0.07em; margin-top:2px'>{lbl}</div></div>"
        for val, lbl, col in kpi_items
    )

    st.markdown(
        f"<div style='background:linear-gradient(90deg,#0a0f1a 0%,#111827 60%,#0a0f1a 100%);"
        f" border:1px solid {C_BORDER2}; border-radius:14px; padding:14px 20px; margin-bottom:24px;"
        f" display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px;"
        f" box-shadow:0 2px 20px rgba(0,0,0,0.5)'>"

        # Left: label
        f"<div style='display:flex; align-items:center; gap:10px; padding-right:16px'>"
        f"<div class='chk-pulse-badge' style='width:9px; height:9px; border-radius:50%;"
        f" background:{C_RED}; flex-shrink:0'></div>"
        f"<div>"
        f"<div style='font-size:0.60rem; font-weight:700; color:{C_RED}; text-transform:uppercase;"
        f" letter-spacing:0.12em'>LIVE INTELLIGENCE</div>"
        f"<div style='font-size:0.70rem; color:{C_TEXT3}; margin-top:1px'>Maritime Chokepoint Monitor</div>"
        f"</div></div>"

        # Middle: KPIs
        f"<div style='display:flex; align-items:center; gap:0; flex-wrap:wrap'>{kpi_html}</div>"

        # Right: severity counts
        f"<div style='display:flex; align-items:center; gap:0; flex-wrap:wrap'>{sev_html}</div>"

        f"</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# NEW Section 1b: Chokepoint Comparison Matrix
# ---------------------------------------------------------------------------

_COMPARISON_METRICS = {
    # metric_key: (display_label, higher_is_worse, format_fn)
    "daily_vessels":        ("Vessels/day",    False, lambda v: f"{v:,}"),
    "pct_global_trade":     ("% Global Trade", False, lambda v: f"{v}%"),
    "extra_days_if_closed": ("Days if Closed", True,  lambda v: f"{v}d"),
}


def _render_chokepoint_comparison() -> None:
    """
    Side-by-side heatmap matrix of all primary chokepoints across 3 key metrics,
    plus a donut chart showing % of global trade exposure by risk level.
    """
    _section_header(
        "Chokepoint Comparison Matrix",
        "Key metrics for the 6 primary passages — intensity = relative severity",
        eyebrow="COMPARATIVE ANALYTICS",
    )

    left_col, right_col = st.columns([3, 2], gap="medium")

    with left_col:
        # Heatmap: chokepoints × metrics
        primary_cps = [(k, CHOKEPOINTS[k]) for k in _PRIMARY_KEYS if k in CHOKEPOINTS]
        if not primary_cps:
            primary_cps = list(CHOKEPOINTS.items())[:6]

        cp_names_short = [cp.name.replace("Strait of ", "").replace("Canal", "Canal") for _, cp in primary_cps]
        metric_labels  = ["Vessels/Day", "% Global Trade", "Days if Closed"]

        z_vals, text_vals = [], []
        for _, cp in primary_cps:
            raw = [cp.daily_vessels, cp.pct_global_trade, cp.extra_days_if_closed]
            z_vals.append(raw)
            text_vals.append([f"{cp.daily_vessels:,}", f"{cp.pct_global_trade}%", f"{cp.extra_days_if_closed}d"])

        # Normalise each column to 0-1 for colour intensity
        z_norm = []
        for row in z_vals:
            z_norm.append(row[:])  # will overwrite below
        for col_idx in range(3):
            col_values = [z_vals[r][col_idx] for r in range(len(z_vals))]
            col_max = max(col_values) or 1.0
            for r in range(len(z_vals)):
                z_norm[r][col_idx] = z_vals[r][col_idx] / col_max

        colorscale = [
            [0.00, "rgba(13,17,23,1)"],
            [0.30, "rgba(30,58,138,0.55)"],
            [0.55, "rgba(37,99,235,0.80)"],
            [0.80, "rgba(147,197,253,0.90)"],
            [1.00, "rgba(255,255,255,1)"],
        ]

        fig_hm = go.Figure(go.Heatmap(
            z=z_norm,
            x=metric_labels,
            y=cp_names_short,
            colorscale=colorscale,
            zmin=0, zmax=1,
            showscale=False,
            text=text_vals,
            texttemplate="%{text}",
            textfont=dict(size=12, color="#f1f5f9", family="Inter, monospace"),
            hovertemplate="<b>%{y}</b><br>%{x}: %{text}<extra></extra>",
        ))

        # Overlay risk-level coloured left bars via shapes
        risk_order_inv = {0: C_RED, 1: C_ORANGE, 2: C_WARN, 3: C_GREEN}
        for i, (key, cp) in enumerate(primary_cps):
            lvl = _RISK_ORDER.get(cp.current_risk_level, 3)
            col = risk_order_inv.get(lvl, C_TEXT3)
            fig_hm.add_shape(
                type="rect",
                xref="paper", yref="y",
                x0=-0.02, x1=0,
                y0=i - 0.48, y1=i + 0.48,
                fillcolor=col,
                line_width=0,
                layer="above",
            )

        fig_hm.update_layout(
            height=280,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=11),
            margin=dict(l=12, r=8, t=12, b=8),
            xaxis=dict(
                side="top",
                tickfont=dict(size=11, color=C_TEXT2),
                tickangle=0,
                showgrid=False,
            ),
            yaxis=dict(
                tickfont=dict(size=11, color=C_TEXT),
                showgrid=False,
                zeroline=False,
            ),
        )
        st.plotly_chart(fig_hm, use_container_width=True,
                        config={"displayModeBar": False},
                        key="chk_comparison_heatmap")

    with right_col:
        # Donut: global trade % by risk level
        risk_trade: dict[str, float] = {"CRITICAL": 0.0, "HIGH": 0.0, "MODERATE": 0.0, "LOW": 0.0}
        for cp in CHOKEPOINTS.values():
            lvl = cp.current_risk_level
            if lvl in risk_trade:
                risk_trade[lvl] += cp.pct_global_trade

        labels  = list(risk_trade.keys())
        values  = list(risk_trade.values())
        colors  = [_RISK_COLS[l] for l in labels]

        total_at_risk = risk_trade["CRITICAL"] + risk_trade["HIGH"]
        fig_donut = go.Figure(go.Pie(
            labels=labels,
            values=values,
            hole=0.62,
            marker=dict(
                colors=[c + "dd" for c in colors],
                line=dict(color="#0a0f1a", width=3),
            ),
            textfont=dict(size=10, color="#f1f5f9"),
            hovertemplate="<b>%{label}</b><br>%{value:.1f}% of global trade<br>%{percent}<extra></extra>",
            sort=False,
        ))
        fig_donut.add_annotation(
            text=f"<b>{total_at_risk:.0f}%</b><br><span style='font-size:0.7rem'>DISRUPTED</span>",
            font=dict(size=15, color=C_RED, family="Inter, sans-serif"),
            showarrow=False,
            x=0.5, y=0.5,
        )
        fig_donut.update_layout(
            height=260,
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=12, b=8),
            font=dict(color=C_TEXT2, size=11),
            legend=dict(
                orientation="v",
                font=dict(size=10, color=C_TEXT2),
                bgcolor="rgba(0,0,0,0)",
                x=0.72, y=0.5, xanchor="left", yanchor="middle",
            ),
            showlegend=True,
        )
        st.plotly_chart(fig_donut, use_container_width=True,
                        config={"displayModeBar": False},
                        key="chk_trade_risk_donut")

        # Caption
        st.markdown(
            f"<div style='font-size:0.68rem; color:{C_TEXT3}; text-align:center; margin-top:-6px;"
            f" line-height:1.5'>"
            f"<b style='color:{C_RED}'>{total_at_risk:.0f}%</b> of global seaborne trade "
            f"transits chokepoints currently rated CRITICAL or HIGH risk."
            f"</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# NEW Section 4b: Transit Fee Benchmark vs Spot Rate
# ---------------------------------------------------------------------------

# Cost-per-TEU benchmarks: canal toll as % of spot freight rate ($/FEU)
# Spot rate reference: Asia-Europe Q1 2026 ≈ $2,400/FEU
_FEE_BENCHMARK_DATA = [
    {"canal": "Suez Canal",   "vessel": "Container (large)", "toll_usd": 520_000,
     "teu_capacity": 18_000, "spot_rate_feu": 2400,
     "toll_per_teu": round(520_000 / 18_000, 0)},
    {"canal": "Panama Canal", "vessel": "Container (Neo-Panamax)", "toll_usd": 900_000,
     "teu_capacity": 14_000, "spot_rate_feu": 2400,
     "toll_per_teu": round(900_000 / 14_000, 0)},
    {"canal": "Suez Canal",   "vessel": "Container (ultra-large)", "toll_usd": 1_200_000,
     "teu_capacity": 24_000, "spot_rate_feu": 2400,
     "toll_per_teu": round(1_200_000 / 24_000, 0)},
    {"canal": "Panama Canal", "vessel": "Container (Panamax)", "toll_usd": 450_000,
     "teu_capacity": 5_000, "spot_rate_feu": 2400,
     "toll_per_teu": round(450_000 / 5_000, 0)},
]


def _render_fee_benchmark() -> None:
    """Toll cost per TEU vs spot freight rate — shows canal cost as % of revenue."""
    _section_header(
        "Canal Toll vs Spot Rate Benchmark",
        "Toll cost per TEU as a % of current spot freight rate — the hidden cost of transit",
        eyebrow="FEE INTELLIGENCE",
    )

    cols4 = st.columns(4, gap="small")
    for col, row in zip(cols4, _FEE_BENCHMARK_DATA):
        toll_pct_of_spot = row["toll_per_teu"] / row["spot_rate_feu"] * 100
        canal_col = C_RED if row["canal"] == "Suez Canal" else C_BLUE
        warn_col = C_RED if toll_pct_of_spot > 20 else C_ORANGE if toll_pct_of_spot > 12 else C_WARN
        with col:
            col.markdown(
                f"<div style='background:{C_CARD}; border:1px solid {C_BORDER};"
                f" border-top:3px solid {canal_col}; border-radius:12px; padding:16px 14px'>"
                f"<div style='font-size:0.60rem; font-weight:700; color:{C_TEXT3};"
                f" text-transform:uppercase; letter-spacing:0.08em; margin-bottom:6px'>"
                f"{row['canal']}</div>"
                f"<div style='font-size:0.72rem; color:{C_TEXT2}; margin-bottom:10px;"
                f" line-height:1.4'>{row['vessel']}</div>"
                f"<div style='font-size:1.60rem; font-weight:900; color:{warn_col};"
                f" font-variant-numeric:tabular-nums; line-height:1'>"
                f"${row['toll_per_teu']:,.0f}</div>"
                f"<div style='font-size:0.60rem; color:{C_TEXT3}; text-transform:uppercase;"
                f" letter-spacing:0.06em; margin-bottom:10px'>toll / TEU</div>"
                f"<div style='background:rgba(255,255,255,0.04); border-radius:4px;"
                f" height:6px; overflow:hidden; margin-bottom:6px'>"
                f"<div style='background:{warn_col}; width:{min(toll_pct_of_spot,100):.1f}%;"
                f" height:6px; border-radius:4px'></div></div>"
                f"<div style='font-size:0.68rem; color:{warn_col}; font-weight:700'>"
                f"{toll_pct_of_spot:.1f}% of spot rate</div>"
                f"<div style='font-size:0.60rem; color:{C_TEXT3}; margin-top:3px'>"
                f"Spot ref: ${row['spot_rate_feu']:,}/FEU</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    # Scatter: toll/TEU vs vessel capacity
    st.markdown(
        f"<div style='font-size:0.70rem; text-transform:uppercase; letter-spacing:0.10em;"
        f" color:{C_TEXT3}; margin:16px 0 8px 0; font-weight:700'>"
        f"Toll per TEU vs Vessel Capacity — Size Efficiency</div>",
        unsafe_allow_html=True,
    )
    capacities = [r["teu_capacity"] for r in _FEE_BENCHMARK_DATA]
    tolls_per_teu = [r["toll_per_teu"] for r in _FEE_BENCHMARK_DATA]
    vessel_names  = [f"{r['canal'][:5]} {r['vessel'][:18]}" for r in _FEE_BENCHMARK_DATA]
    scatter_colors = [C_RED if r["canal"] == "Suez Canal" else C_BLUE for r in _FEE_BENCHMARK_DATA]

    fig_sc = go.Figure()
    fig_sc.add_trace(go.Scatter(
        x=capacities,
        y=tolls_per_teu,
        mode="markers+text",
        marker=dict(
            size=[max(12, min(36, int(r["toll_usd"] / 40_000))) for r in _FEE_BENCHMARK_DATA],
            color=scatter_colors,
            opacity=0.85,
            line=dict(color="rgba(255,255,255,0.20)", width=1.5),
        ),
        text=vessel_names,
        textposition="top center",
        textfont=dict(size=9, color=C_TEXT2),
        hovertemplate=(
            "<b>%{text}</b><br>"
            "Capacity: %{x:,} TEU<br>"
            "Toll/TEU: $%{y:,.0f}<extra></extra>"
        ),
        showlegend=False,
    ))
    # Efficiency trend line
    if len(capacities) > 1:
        import numpy as _np
        _x = _np.array(capacities)
        _y = _np.array(tolls_per_teu)
        _m, _b = _np.polyfit(_x, _y, 1)
        _x_line = _np.linspace(min(_x), max(_x), 50)
        fig_sc.add_trace(go.Scatter(
            x=list(_x_line),
            y=list(_m * _x_line + _b),
            mode="lines",
            line=dict(color="rgba(148,163,184,0.25)", width=1.5, dash="dot"),
            showlegend=False,
            hoverinfo="skip",
        ))

    fig_sc.update_layout(
        height=240,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT2, size=11),
        margin=dict(l=0, r=0, t=8, b=36),
        xaxis=dict(
            title=dict(text="Vessel Capacity (TEU)", font=dict(size=10, color=C_TEXT3)),
            tickfont=dict(size=9, color=C_TEXT3),
            showgrid=False, zeroline=False,
        ),
        yaxis=dict(
            title=dict(text="Toll per TEU ($)", font=dict(size=10, color=C_TEXT3)),
            tickfont=dict(size=9, color=C_TEXT3),
            gridcolor="rgba(255,255,255,0.04)",
            zeroline=False,
            tickprefix="$",
        ),
        annotations=[
            dict(
                text="Larger vessels → lower per-TEU cost",
                x=0.98, y=0.95, xref="paper", yref="paper",
                showarrow=False,
                font=dict(size=9, color=C_TEXT3),
                xanchor="right",
            )
        ],
    )
    st.plotly_chart(fig_sc, use_container_width=True,
                    config={"displayModeBar": False},
                    key="chk_fee_benchmark_scatter")


# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------
# Canal Wait Times — live data section
# ---------------------------------------------------------------------------

def _render_canal_wait_times() -> None:
    """Fetch and display Panama / Suez live wait-time stat cards."""
    if not _CANAL_FEED_OK:
        return

    try:
        panama = fetch_panama_stats(cache_ttl_hours=12.0)
        suez   = fetch_suez_stats(cache_ttl_hours=12.0)
    except Exception as exc:
        logger.warning(f"canal wait times: fetch failed: {exc}")
        return

    try:
        impact = get_canal_shipping_impact(panama, suez)
    except Exception as exc:
        logger.warning(f"canal wait times: impact failed: {exc}")
        impact = {}

    # ── Section header ────────────────────────────────────────────────────────
    st.markdown(
        f"<div style='margin:28px 0 16px 0; padding-bottom:12px; "
        f"border-bottom:1px solid {C_BORDER2}'>"
        f"<div style='font-size:0.62rem; text-transform:uppercase; letter-spacing:0.16em; "
        f"color:{C_TEXT3}; margin-bottom:5px'>LIVE INTELLIGENCE</div>"
        f"<div style='font-size:1.1rem; font-weight:800; color:{C_TEXT}; "
        f"letter-spacing:-0.02em'>Canal Transit Conditions</div>"
        f"<div style='font-size:0.78rem; color:{C_TEXT2}; margin-top:4px'>"
        f"Real-time wait times, capacity utilisation, and restriction status for the "
        f"Panama and Suez canals</div></div>",
        unsafe_allow_html=True,
    )

    _STATUS_COLORS = {
        "Normal":     (C_GREEN, "rgba(16,185,129,0.13)"),
        "Restricted": (C_WARN,  "rgba(245,158,11,0.14)"),
        "Disrupted":  (C_RED,   "rgba(239,68,68,0.16)"),
    }

    def _canal_card(stats) -> str:
        scolor, sbg = _STATUS_COLORS.get(stats.status, (C_TEXT2, "rgba(148,163,184,0.10)"))
        avg_wait = (stats.northbound_wait_days + stats.southbound_wait_days) / 2

        # Water level row — Panama only
        water_html = ""
        if stats.canal == "Panama" and stats.water_level_m > 0:
            # Gatun Lake alert threshold ~26.5 m
            w_color = C_GREEN if stats.water_level_m >= 26.5 else C_WARN if stats.water_level_m >= 25.5 else C_RED
            water_html = (
                f"<div style='display:flex; justify-content:space-between; "
                f"padding:6px 0; border-bottom:1px solid {C_BORDER}'>"
                f"<span style='font-size:0.75rem; color:{C_TEXT2}'>Gatun Lake Level</span>"
                f"<span style='font-size:0.78rem; font-weight:700; color:{w_color}'>"
                f"{stats.water_level_m:.1f} m</span></div>"
            )

        # Capacity bar
        util = min(stats.capacity_utilization_pct, 100.0)
        bar_color = C_GREEN if util < 75 else C_WARN if util < 90 else C_RED

        return (
            f"<div style='background:{C_CARD2}; border:1px solid {C_BORDER2}; "
            f"border-radius:10px; padding:18px 20px; height:100%'>"
            # Title row
            f"<div style='display:flex; justify-content:space-between; align-items:center; "
            f"margin-bottom:14px'>"
            f"<div style='font-size:1.0rem; font-weight:800; color:{C_TEXT}; "
            f"letter-spacing:-0.02em'>{stats.canal} Canal</div>"
            f"<span style='background:{sbg}; color:{scolor}; border:1px solid {scolor}55; "
            f"padding:2px 10px; border-radius:999px; font-size:0.62rem; font-weight:700; "
            f"letter-spacing:0.07em'>{stats.status.upper()}</span>"
            f"</div>"
            # Metrics grid
            f"<div style='display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:12px'>"
            # NB wait
            f"<div style='background:rgba(255,255,255,0.03); border-radius:6px; padding:8px 10px'>"
            f"<div style='font-size:0.65rem; color:{C_TEXT3}; text-transform:uppercase; "
            f"letter-spacing:0.08em; margin-bottom:3px'>NB Wait</div>"
            f"<div style='font-size:1.05rem; font-weight:800; color:{C_TEXT}'>"
            f"{stats.northbound_wait_days:.1f}d</div></div>"
            # SB wait
            f"<div style='background:rgba(255,255,255,0.03); border-radius:6px; padding:8px 10px'>"
            f"<div style='font-size:0.65rem; color:{C_TEXT3}; text-transform:uppercase; "
            f"letter-spacing:0.08em; margin-bottom:3px'>SB Wait</div>"
            f"<div style='font-size:1.05rem; font-weight:800; color:{C_TEXT}'>"
            f"{stats.southbound_wait_days:.1f}d</div></div>"
            # Daily transits
            f"<div style='background:rgba(255,255,255,0.03); border-radius:6px; padding:8px 10px'>"
            f"<div style='font-size:0.65rem; color:{C_TEXT3}; text-transform:uppercase; "
            f"letter-spacing:0.08em; margin-bottom:3px'>Daily Transits</div>"
            f"<div style='font-size:1.05rem; font-weight:800; color:{C_BLUE}'>"
            f"{stats.daily_transits}</div></div>"
            # Avg wait
            f"<div style='background:rgba(255,255,255,0.03); border-radius:6px; padding:8px 10px'>"
            f"<div style='font-size:0.65rem; color:{C_TEXT3}; text-transform:uppercase; "
            f"letter-spacing:0.08em; margin-bottom:3px'>Avg Wait</div>"
            f"<div style='font-size:1.05rem; font-weight:800; color:{C_TEXT}'>"
            f"{avg_wait:.1f}d</div></div>"
            f"</div>"
            # Water level (Panama only)
            + water_html +
            # Capacity bar
            f"<div style='margin:10px 0 6px'>"
            f"<div style='display:flex; justify-content:space-between; margin-bottom:4px'>"
            f"<span style='font-size:0.68rem; color:{C_TEXT3}'>Capacity Utilisation</span>"
            f"<span style='font-size:0.72rem; font-weight:700; color:{bar_color}'>"
            f"{util:.0f}%</span></div>"
            f"<div style='background:rgba(255,255,255,0.08); border-radius:999px; height:5px'>"
            f"<div style='background:{bar_color}; width:{util:.1f}%; height:5px; "
            f"border-radius:999px; transition:width 0.4s'></div></div></div>"
            # Restrictions
            f"<div style='margin-top:10px; padding:8px 10px; "
            f"background:rgba(255,255,255,0.03); border-radius:6px; "
            f"border-left:3px solid {scolor}'>"
            f"<div style='font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase; "
            f"letter-spacing:0.08em; margin-bottom:3px'>Restrictions</div>"
            f"<div style='font-size:0.73rem; color:{C_TEXT2}; line-height:1.4'>"
            f"{stats.restrictions}</div></div>"
            # Source footnote
            f"<div style='margin-top:8px; font-size:0.6rem; color:{C_TEXT3}'>"
            f"Source: <a href='{stats.source_url}' style='color:{C_TEXT3}' "
            f"target='_blank'>{stats.source_url[:50]}...</a> &mdash; "
            f"{stats.fetched_at[:16]} UTC</div>"
            f"</div>"
        )

    col_pan, col_sue = st.columns(2, gap="medium")
    with col_pan:
        st.markdown(_canal_card(panama), unsafe_allow_html=True)
    with col_sue:
        st.markdown(_canal_card(suez), unsafe_allow_html=True)

    # Shipping impact summary bar
    if impact:
        p_lvl = impact.get("panama_impact", "Low")
        s_lvl = impact.get("suez_impact", "Low")
        premium = impact.get("rate_premium_est_pct", 0.0)
        narrative = impact.get("narrative", "")
        routes = impact.get("affected_routes", [])

        _IMPACT_COLORS = {
            "Low":      C_GREEN,
            "Moderate": C_WARN,
            "High":     C_ORANGE,
            "Critical": C_RED,
        }
        p_color = _IMPACT_COLORS.get(p_lvl, C_TEXT2)
        s_color = _IMPACT_COLORS.get(s_lvl, C_TEXT2)

        routes_html = (
            " &bull; ".join(
                f"<span style='color:{C_TEXT2}'>{r}</span>" for r in routes[:4]
            ) if routes else "No major route impacts"
        )

        st.markdown(
            f"<div style='margin-top:14px; padding:14px 18px; "
            f"background:{C_CARD}; border:1px solid {C_BORDER2}; border-radius:8px; "
            f"display:flex; gap:24px; align-items:flex-start; flex-wrap:wrap'>"
            f"<div style='min-width:110px'>"
            f"<div style='font-size:0.6rem; color:{C_TEXT3}; text-transform:uppercase; "
            f"letter-spacing:0.1em; margin-bottom:3px'>Panama Impact</div>"
            f"<div style='font-size:0.88rem; font-weight:800; color:{p_color}'>{p_lvl}</div>"
            f"</div>"
            f"<div style='min-width:110px'>"
            f"<div style='font-size:0.6rem; color:{C_TEXT3}; text-transform:uppercase; "
            f"letter-spacing:0.1em; margin-bottom:3px'>Suez Impact</div>"
            f"<div style='font-size:0.88rem; font-weight:800; color:{s_color}'>{s_lvl}</div>"
            f"</div>"
            f"<div style='min-width:130px'>"
            f"<div style='font-size:0.6rem; color:{C_TEXT3}; text-transform:uppercase; "
            f"letter-spacing:0.1em; margin-bottom:3px'>Est. Rate Premium</div>"
            f"<div style='font-size:0.88rem; font-weight:800; color:{C_WARN}'>"
            f"+{premium:.1f}%</div>"
            f"</div>"
            f"<div style='flex:1; min-width:200px'>"
            f"<div style='font-size:0.6rem; color:{C_TEXT3}; text-transform:uppercase; "
            f"letter-spacing:0.1em; margin-bottom:4px'>Affected Routes</div>"
            f"<div style='font-size:0.71rem; line-height:1.6'>{routes_html}</div>"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        if narrative:
            st.caption(narrative)


# ---------------------------------------------------------------------------

def render(route_results: list, freight_data: dict, macro_data: dict) -> None:
    """Render the Maritime Chokepoints tab — 8 enhanced analytical sections."""

    logger.info("Rendering tab_chokepoints (enhanced)")

    # CSS
    st.markdown(_CSS, unsafe_allow_html=True)

    # ── Page header ──────────────────────────────────────────────────────────
    try:
        active_disruptions = get_current_active_disruptions()
    except Exception as _e:
        logger.warning(f"get_current_active_disruptions failed: {_e}")
        active_disruptions = []

    n_active = len(active_disruptions)
    active_names = ", ".join(
        cp.name for cp in active_disruptions if cp.current_risk_level in ("CRITICAL", "HIGH")
    )

    alert_html = ""
    if active_names:
        alert_html = (
            f"&ensp;&mdash;&ensp;<span style='color:{C_ORANGE}'>{active_names}</span>"
        )

    st.markdown(
        f"<div style='padding:18px 0 22px 0; border-bottom:1px solid {C_BORDER}; margin-bottom:22px'>"
        f"<div style='font-size:0.65rem; text-transform:uppercase; letter-spacing:0.16em;"
        f" color:{C_TEXT3}; margin-bottom:7px'>GEOPOLITICAL INTELLIGENCE</div>"
        f"<div style='font-size:1.85rem; font-weight:900; color:{C_TEXT};"
        f" letter-spacing:-0.035em; line-height:1.1'>Maritime Chokepoints</div>"
        f"<div style='font-size:0.85rem; color:{C_TEXT2}; margin-top:7px; line-height:1.6'>"
        f"9 critical passages controlling 60%+ of global seaborne trade"
        f"&ensp;|&ensp;"
        f"<span style='color:{C_RED}; font-weight:700'>{n_active} active disruption(s)</span>"
        f"{alert_html}"
        f"</div></div>",
        unsafe_allow_html=True,
    )

    # ── Guard: data loaded? ──────────────────────────────────────────────────
    if not CHOKEPOINTS:
        st.warning(
            "No chokepoint data loaded. Check that `processing.chokepoint_analyzer` "
            "populates the `CHOKEPOINTS` dictionary.",
            icon="⚠️",
        )
        return

    # ── Staleness check ──────────────────────────────────────────────────────
    from datetime import date as _cp_date, datetime as _dt_cp
    _today_cp = _cp_date.today()
    _stale = sum(
        1 for cp in CHOKEPOINTS.values()
        if getattr(cp, "disruption_since", None) and
        isinstance(cp.disruption_since, str) and len(cp.disruption_since) >= 10
        and (() or (
            lambda d: (_today_cp - d).days > 730
        )(_dt_cp.strptime(cp.disruption_since[:10], "%Y-%m-%d").date()))
    )
    if _stale > 0:
        st.warning(
            f"{_stale} chokepoint(s) have disruption records older than 2 years — "
            "data may be stale.",
            icon="⚠️",
        )

    # ── Risk scores (computed once) ───────────────────────────────────────────
    try:
        risk_scores = compute_chokepoint_risk_score()
    except Exception as _rs_err:
        logger.warning(f"compute_chokepoint_risk_score failed: {_rs_err}")
        risk_scores = {}

    # ── Canal Wait Times (live data) ─────────────────────────────────────────
    try:
        _render_canal_wait_times()
    except Exception as _e:
        logger.warning(f"Canal wait times failed: {_e}")

    # ── Section 0: Live Intelligence Strip (NEW) ─────────────────────────────
    try:
        _render_live_intelligence_strip()
    except Exception as _e:
        logger.warning(f"Live intelligence strip failed: {_e}")

    # ── Section 1: Status Dashboard ─────────────────────────────────────────
    try:
        _render_status_dashboard()
    except Exception as _e:
        logger.error(f"Status dashboard failed: {_e}")
        st.error("Could not render status dashboard.", icon="⚠️")

    _divider_line()

    # ── Section 1b: Comparison Matrix + Trade Donut (NEW) ────────────────────
    try:
        _render_chokepoint_comparison()
    except Exception as _e:
        logger.warning(f"Comparison matrix failed: {_e}")

    _divider_line()

    # ── Section 2: World Map ─────────────────────────────────────────────────
    try:
        _render_world_map(risk_scores)
    except Exception as _e:
        logger.error(f"World map failed: {_e}")
        st.error("Could not render world map.", icon="⚠️")

    _divider_line()

    # ── Section 3: Traffic Density ───────────────────────────────────────────
    try:
        _render_traffic_density()
    except Exception as _e:
        logger.error(f"Traffic density failed: {_e}")
        st.error("Could not render traffic density chart.", icon="⚠️")

    _divider_line()

    # ── Section 4: Transit Fees ──────────────────────────────────────────────
    try:
        _render_transit_fees()
    except Exception as _e:
        logger.error(f"Transit fees failed: {_e}")
        st.error("Could not render transit fee analysis.", icon="⚠️")

    _divider_line()

    # ── Section 4b: Fee Benchmark vs Spot Rate (NEW) ─────────────────────────
    try:
        _render_fee_benchmark()
    except Exception as _e:
        logger.warning(f"Fee benchmark failed: {_e}")

    _divider_line()

    # ── Section 5: Risk Scores ───────────────────────────────────────────────
    try:
        _render_risk_scores(risk_scores)
    except Exception as _e:
        logger.error(f"Risk scores failed: {_e}")
        st.error("Could not render risk score analysis.", icon="⚠️")

    _divider_line()

    # ── Section 6: Historical Disruption Impacts ─────────────────────────────
    try:
        _render_historical_impacts()
    except Exception as _e:
        logger.error(f"Historical impacts failed: {_e}")
        st.error("Could not render historical disruption analysis.", icon="⚠️")

    _divider_line()

    # ── Section 7: Alternative Routing ───────────────────────────────────────
    try:
        _render_alternative_routing()
    except Exception as _e:
        logger.error(f"Alternative routing failed: {_e}")
        st.error("Could not render alternative routing analysis.", icon="⚠️")

    _divider_line()

    # ── Section 8: Insurance Impact ──────────────────────────────────────────
    try:
        _render_insurance_impact()
    except Exception as _e:
        logger.error(f"Insurance impact failed: {_e}")
        st.error("Could not render insurance rate analysis.", icon="⚠️")

    _divider_line()

    # ── Section 9: Closure Impact Simulator ──────────────────────────────────
    try:
        _render_closure_simulator()
    except Exception as _e:
        logger.error(f"Closure simulator failed: {_e}")
        st.error("Could not render closure simulator.", icon="⚠️")

    _divider_line()

    # ── Section 10: Red Sea Crisis Tracker ───────────────────────────────────
    try:
        _render_red_sea_tracker()
    except Exception as _e:
        logger.error(f"Red Sea tracker failed: {_e}")
        st.error("Could not render Red Sea crisis tracker.", icon="⚠️")

    _divider_line()

    # ── Section 11: Full Risk Dashboard (all 9) ───────────────────────────────
    try:
        _render_full_risk_dashboard()
    except Exception as _e:
        logger.error(f"Full risk dashboard failed: {_e}")
        st.error("Could not render full risk dashboard.", icon="⚠️")

    _divider_line()

    # ── Section 12: Historical Timeline ──────────────────────────────────────
    try:
        _render_historical_timeline()
    except Exception as _e:
        logger.error(f"Historical timeline failed: {_e}")
        st.error("Could not render historical timeline.", icon="⚠️")
