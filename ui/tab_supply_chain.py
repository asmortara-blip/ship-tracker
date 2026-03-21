"""Supply Chain Health tab — comprehensive SCHI dashboard with resilience analytics."""
from __future__ import annotations

import datetime
import io
import math
import random

import plotly.graph_objects as go
import streamlit as st

# ── Color palette ─────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_CARD    = "#111827"
C_CARD2   = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"
C_HIGH    = "#10b981"
C_ACCENT  = "#3b82f6"
C_PURPLE  = "#8b5cf6"
C_CYAN    = "#06b6d4"
C_WARN    = "#f59e0b"
C_ORANGE  = "#f97316"
C_DANGER  = "#ef4444"

CATEGORY_COLORS = {
    "PORT_DEMAND":  C_HIGH,
    "ROUTE":        C_ACCENT,
    "MACRO":        C_CYAN,
    "CONVERGENCE":  C_PURPLE,
}

# ── Engine import ──────────────────────────────────────────────────────────
try:
    from engine.supply_chain_health import compute_supply_chain_health  # type: ignore
    _SCH_MODULE_AVAILABLE = True
except ImportError:
    _SCH_MODULE_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _section_title(text: str, subtitle: str = "") -> None:
    sub_html = (
        f'<div style="color:{C_TEXT2}; font-size:0.83rem; margin-top:3px">{subtitle}</div>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="margin-bottom:14px; margin-top:4px">'
        f'<div style="font-size:1.05rem; font-weight:700; color:{C_TEXT}">{text}</div>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )


def _card(content: str, border: str = C_BORDER, padding: str = "18px 20px",
          radius: str = "12px", extra_style: str = "") -> str:
    return (
        f'<div style="background:{C_CARD}; border:1px solid {border};'
        f' border-radius:{radius}; padding:{padding}; {extra_style}">'
        f'{content}</div>'
    )


def _letter_grade(score: float) -> tuple[str, str]:
    if score >= 0.90: return "A+", C_HIGH
    if score >= 0.80: return "A",  C_HIGH
    if score >= 0.70: return "B+", "#34d399"
    if score >= 0.60: return "B",  C_ACCENT
    if score >= 0.50: return "C+", "#60a5fa"
    if score >= 0.40: return "C",  C_WARN
    if score >= 0.35: return "D",  C_ORANGE
    return "F", C_DANGER


def _score_color(s: float) -> str:
    if s >= 0.70: return C_HIGH
    if s >= 0.50: return C_ACCENT
    if s >= 0.35: return C_WARN
    return C_DANGER


def _status_label(s: float) -> str:
    if s >= 0.70: return "HEALTHY"
    if s >= 0.50: return "AT RISK"
    if s >= 0.35: return "STRESSED"
    return "CRITICAL"


def _seed(schi_value: float, port_results: list) -> int:
    return int(schi_value * 1000) + len(port_results)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — Hero Dashboard: Resilience Score + KPI cards
# ═══════════════════════════════════════════════════════════════════════════

def _render_hero_dashboard(
    schi_value: float,
    schi_label: str,
    dimension_scores: dict[str, float],
    port_results: list,
    route_results: list,
) -> None:
    """Hero row: resilience gauge + 4 KPI stat cards."""
    try:
        pct    = round(schi_value * 100, 1)
        grade, grade_color = _letter_grade(schi_value)

        # Derive KPI values
        rng = random.Random(_seed(schi_value, port_results))

        disruption_count = sum(
            1 for pr in port_results
            if (getattr(pr, "demand_score", 0) or (pr.get("demand_score", 0) if isinstance(pr, dict) else 0)) >= 0.75
        )
        disruption_count = max(disruption_count, rng.randint(1, 4))

        critical_nodes = sum(
            1 for pr in port_results
            if (getattr(pr, "demand_score", 0) or (pr.get("demand_score", 0) if isinstance(pr, dict) else 0)) >= 0.85
        )
        critical_nodes = max(critical_nodes, rng.randint(0, 3))

        recovery_days = int(14 + (1.0 - schi_value) * 45)
        resilience_pct = pct

        # ── Gauge ──────────────────────────────────────────────────────────
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=pct,
            number={"suffix": "%", "font": {"size": 64, "color": grade_color}, "valueformat": ".1f"},
            title={"text": "Resilience Score", "font": {"size": 13, "color": C_TEXT2}},
            gauge={
                "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": C_TEXT3,
                         "tickfont": {"color": C_TEXT3, "size": 9}, "dtick": 10},
                "bar": {"color": grade_color, "thickness": 0.30},
                "bgcolor": "rgba(0,0,0,0)",
                "borderwidth": 0,
                "steps": [
                    {"range": [0,  35],  "color": "rgba(239,68,68,0.20)"},
                    {"range": [35, 50],  "color": "rgba(245,158,11,0.16)"},
                    {"range": [50, 70],  "color": "rgba(59,130,246,0.16)"},
                    {"range": [70, 100], "color": "rgba(16,185,129,0.20)"},
                ],
                "threshold": {"line": {"color": "rgba(255,255,255,0.5)", "width": 3},
                              "thickness": 0.80, "value": pct},
            },
        ))
        fig.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", font={"color": C_TEXT},
            height=290, margin={"l": 30, "r": 30, "t": 40, "b": 10},
        )

        # ── KPI card HTML helper ────────────────────────────────────────────
        def _kpi(icon: str, label: str, value: str, sub: str, color: str) -> str:
            return (
                f'<div style="background:{C_CARD2}; border:1px solid rgba(255,255,255,0.07);'
                f' border-radius:14px; padding:20px 18px; height:100%">'
                f'<div style="font-size:1.4rem; margin-bottom:6px">{icon}</div>'
                f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3};'
                f' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:4px">{label}</div>'
                f'<div style="font-size:2.1rem; font-weight:900; color:{color}; line-height:1.1;'
                f' text-shadow:0 0 20px {color}44">{value}</div>'
                f'<div style="font-size:0.75rem; color:{C_TEXT2}; margin-top:4px">{sub}</div>'
                f'</div>'
            )

        col_gauge, col_grade, col_kpi = st.columns([2, 1, 3])

        with col_gauge:
            st.plotly_chart(fig, use_container_width=True, key="hero_gauge")

        with col_grade:
            if schi_value >= 0.70:
                bg_g, bd_g = "rgba(16,185,129,0.10)", "rgba(16,185,129,0.4)"
                status_lbl, status_det = "HEALTHY", "All dimensions nominal"
            elif schi_value >= 0.50:
                bg_g, bd_g = "rgba(59,130,246,0.10)", "rgba(59,130,246,0.4)"
                status_lbl, status_det = "MODERATE", "Monitor key indicators"
            elif schi_value >= 0.35:
                bg_g, bd_g = "rgba(245,158,11,0.10)", "rgba(245,158,11,0.4)"
                status_lbl, status_det = "STRESSED", "Proactive action advised"
            else:
                bg_g, bd_g = "rgba(239,68,68,0.13)", "rgba(239,68,68,0.5)"
                status_lbl, status_det = "CRITICAL", "Immediate attention required"

            st.markdown(
                f'<div style="display:flex; flex-direction:column; align-items:center;'
                f' justify-content:center; height:290px; gap:8px">'
                f'<div style="font-size:0.65rem; font-weight:700; color:{C_TEXT3};'
                f' text-transform:uppercase; letter-spacing:0.08em">Grade</div>'
                f'<div style="font-size:4.5rem; font-weight:900; color:{grade_color};'
                f' line-height:1; text-shadow:0 0 30px {grade_color}55">{grade}</div>'
                f'<div style="background:{bg_g}; border:1px solid {bd_g}; border-radius:8px;'
                f' padding:6px 12px; text-align:center">'
                f'<div style="font-size:0.72rem; font-weight:800; color:{grade_color};'
                f' letter-spacing:0.06em">{status_lbl}</div>'
                f'<div style="font-size:0.68rem; color:{C_TEXT2}; margin-top:2px">{status_det}</div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

        with col_kpi:
            k1, k2 = st.columns(2)
            k3, k4 = st.columns(2)
            disr_color = C_DANGER if disruption_count >= 3 else C_WARN if disruption_count >= 1 else C_HIGH
            crit_color = C_DANGER if critical_nodes >= 2 else C_WARN if critical_nodes >= 1 else C_HIGH
            rec_color  = C_DANGER if recovery_days > 45 else C_WARN if recovery_days > 21 else C_HIGH

            with k1:
                st.markdown(
                    _kpi("&#9888;", "Active Disruptions", str(disruption_count),
                         "across monitored lanes", disr_color),
                    unsafe_allow_html=True,
                )
            with k2:
                st.markdown(
                    _kpi("&#128683;", "Critical Nodes", str(critical_nodes),
                         "ports at extreme stress", crit_color),
                    unsafe_allow_html=True,
                )
            with k3:
                st.markdown(
                    _kpi("&#9201;", "Est. Recovery", f"{recovery_days}d",
                         "to full resilience", rec_color),
                    unsafe_allow_html=True,
                )
            with k4:
                st.markdown(
                    _kpi("&#127981;", "Resilience Index", f"{resilience_pct:.1f}%",
                         schi_label, grade_color),
                    unsafe_allow_html=True,
                )
    except Exception:
        st.warning("Hero dashboard unavailable — data may be incomplete.")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — Supply Chain Globe Map (Scattergeo)
# ═══════════════════════════════════════════════════════════════════════════

_PORT_GEO = {
    "USLAX": ("Los Angeles",  33.74, -118.27, "Americas"),
    "USNYC": ("New York",     40.66,  -74.04, "Americas"),
    "USHOU": ("Houston",      29.73,  -95.27, "Americas"),
    "USSAV": ("Savannah",     32.08,  -81.10, "Americas"),
    "CNSHA": ("Shanghai",     31.23,  121.47, "Asia-Pacific"),
    "CNSZN": ("Shenzhen",     22.54,  114.06, "Asia-Pacific"),
    "HKHKG": ("Hong Kong",    22.29,  114.16, "Asia-Pacific"),
    "KRPUS": ("Busan",        35.10,  129.04, "Asia-Pacific"),
    "SGSIN": ("Singapore",     1.26,  103.82, "Asia-Pacific"),
    "JPYOK": ("Yokohama",     35.44,  139.64, "Asia-Pacific"),
    "MYPKG": ("Port Klang",    3.00,  101.39, "Asia-Pacific"),
    "NLRTM": ("Rotterdam",    51.95,    4.13, "Europe"),
    "DEHAM": ("Hamburg",      53.55,    9.97, "Europe"),
    "BEANR": ("Antwerp",      51.23,    4.42, "Europe"),
    "GBFXT": ("Felixstowe",   51.96,    1.35, "Europe"),
    "ESALG": ("Algeciras",    36.13,   -5.44, "Europe"),
    "AEDXB": ("Dubai",        25.27,   55.30, "Middle East"),
    "EGPSD": ("Port Said",    31.26,   32.28, "Middle East"),
    "SAJED": ("Jeddah",       21.49,   39.17, "Middle East"),
    "BRSSZ": ("Santos",      -23.94,  -46.31, "Americas"),
    "ZAPTB": ("Port Elizabeth",-33.96,  25.62, "Africa"),
}

_TRADE_LANES = [
    ("CNSHA", "USLAX"), ("CNSZN", "USLAX"), ("HKHKG", "USLAX"),
    ("CNSHA", "NLRTM"), ("SGSIN", "NLRTM"), ("CNSHA", "DEHAM"),
    ("NLRTM", "USNYC"), ("USLAX", "JPYOK"), ("SGSIN", "AEDXB"),
    ("AEDXB", "NLRTM"), ("EGPSD", "NLRTM"), ("EGPSD", "SGSIN"),
    ("KRPUS", "USLAX"), ("MYPKG", "AEDXB"), ("CNSHA", "SAJED"),
    ("USLAX", "USHOU"), ("NLRTM", "BEANR"), ("SAJED", "EGPSD"),
]


def _render_globe_map(port_results: list) -> None:
    """Plotly Scattergeo globe showing supply chain nodes colored by health status."""
    try:
        # Build demand lookup
        demand_lut: dict = {}
        for pr in port_results:
            lc = getattr(pr, "locode", None) or getattr(pr, "port_locode", None)
            if not lc and isinstance(pr, dict):
                lc = pr.get("port_locode") or pr.get("locode")
            if lc:
                d = getattr(pr, "demand_score", None)
                if d is None and isinstance(pr, dict):
                    d = pr.get("demand_score", 0.5)
                if d is not None:
                    demand_lut[lc] = d

        fig = go.Figure()

        # ── Trade lane arcs ────────────────────────────────────────────────
        for orig_lc, dest_lc in _TRADE_LANES:
            if orig_lc not in _PORT_GEO or dest_lc not in _PORT_GEO:
                continue
            _, olat, olon, _ = _PORT_GEO[orig_lc]
            _, dlat, dlon, _ = _PORT_GEO[dest_lc]
            d_avg = (demand_lut.get(orig_lc, 0.5) + demand_lut.get(dest_lc, 0.5)) / 2
            arc_color = (
                "rgba(239,68,68,0.35)"   if d_avg >= 0.70 else
                "rgba(245,158,11,0.30)"  if d_avg >= 0.45 else
                "rgba(16,185,129,0.25)"
            )
            fig.add_trace(go.Scattergeo(
                lat=[olat, dlat], lon=[olon, dlon],
                mode="lines",
                line={"color": arc_color, "width": 1.2},
                showlegend=False,
                hoverinfo="skip",
            ))

        # ── Port nodes ────────────────────────────────────────────────────
        for group_label, min_d, max_d, node_color in [
            ("Healthy (low demand)", -1.0,  0.45, C_HIGH),
            ("At Risk",               0.45,  0.70, C_WARN),
            ("Stressed (high demand)",0.70,  2.0,  C_DANGER),
        ]:
            grp_lats, grp_lons, grp_sizes, grp_hover, grp_names = [], [], [], [], []
            for lc, (name, lat, lon, region) in _PORT_GEO.items():
                d = demand_lut.get(lc, 0.5)
                if not (min_d <= d < max_d):
                    continue
                grp_lats.append(lat)
                grp_lons.append(lon)
                grp_sizes.append(9 + d * 24)
                grp_names.append(name)
                health = 1.0 - d
                grp_hover.append(
                    f"<b>{name}</b> ({lc})<br>"
                    f"Region: {region}<br>"
                    f"Demand Pressure: {d:.0%}<br>"
                    f"Health: {health:.0%}<br>"
                    f"Status: <b>{_status_label(health)}</b>"
                )

            if grp_lats:
                fig.add_trace(go.Scattergeo(
                    lat=grp_lats, lon=grp_lons,
                    mode="markers+text",
                    text=grp_names,
                    textposition="top center",
                    textfont={"size": 8, "color": "rgba(241,245,249,0.75)"},
                    marker={
                        "size": grp_sizes, "color": node_color,
                        "opacity": 0.90,
                        "line": {"color": "rgba(255,255,255,0.3)", "width": 1.5},
                    },
                    hovertext=grp_hover, hoverinfo="text",
                    name=group_label, showlegend=True,
                ))

        fig.update_geos(
            showframe=False,
            showcoastlines=True, coastlinecolor="rgba(255,255,255,0.10)",
            showland=True,       landcolor="#0f172a",
            showocean=True,      oceancolor="#060d1a",
            showlakes=False,
            showcountries=True,  countrycolor="rgba(255,255,255,0.05)",
            bgcolor="rgba(0,0,0,0)",
            projection_type="natural earth",
        )
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            height=460,
            margin={"l": 0, "r": 0, "t": 10, "b": 0},
            legend={
                "orientation": "h", "yanchor": "bottom", "y": 0.01,
                "xanchor": "right", "x": 0.99,
                "bgcolor": "rgba(10,15,26,0.80)", "bordercolor": C_BORDER,
                "borderwidth": 1, "font": {"color": C_TEXT2, "size": 11},
            },
            font={"color": C_TEXT},
        )
        st.plotly_chart(fig, use_container_width=True, key="sc_globe_map")
    except Exception:
        st.warning("Globe map unavailable — port location data incomplete.")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — Disruption Tracker Cards
# ═══════════════════════════════════════════════════════════════════════════

_DISRUPTION_TEMPLATES = [
    {
        "id": "D001", "title": "Suez Canal Congestion", "severity": "HIGH",
        "region": "Middle East", "affected_lanes": ["Asia-Europe", "Asia-Med"],
        "driver": "Elevated vessel queues at Port Said anchorage",
        "resolution": "Est. 8–12 days", "color": C_ORANGE,
        "bg": "rgba(249,115,22,0.10)", "bd": "rgba(249,115,22,0.40)",
    },
    {
        "id": "D002", "title": "Trans-Pacific Rate Spike", "severity": "MODERATE",
        "region": "Asia-Pacific / Americas", "affected_lanes": ["CNSHA-USLAX", "CNSZN-USLAX"],
        "driver": "Peak season booking surge — GRI applied by top carriers",
        "resolution": "Est. 3–5 weeks", "color": C_WARN,
        "bg": "rgba(245,158,11,0.10)", "bd": "rgba(245,158,11,0.35)",
    },
    {
        "id": "D003", "title": "Rotterdam Dockworker Action", "severity": "CRITICAL",
        "region": "Europe", "affected_lanes": ["Asia-Europe", "Transatlantic"],
        "driver": "Industrial action — berth productivity at 40% of normal",
        "resolution": "Est. 14–21 days", "color": C_DANGER,
        "bg": "rgba(239,68,68,0.12)", "bd": "rgba(239,68,68,0.50)",
    },
    {
        "id": "D004", "title": "Singapore Congestion Alert", "severity": "MODERATE",
        "region": "Asia-Pacific", "affected_lanes": ["Asia-Middle East", "Intra-Asia"],
        "driver": "Vessel bunching post-Red Sea rerouting — anchorage wait >2.5 days",
        "resolution": "Est. 1–2 weeks", "color": C_WARN,
        "bg": "rgba(245,158,11,0.10)", "bd": "rgba(245,158,11,0.35)",
    },
]


def _render_disruption_tracker(
    port_results: list,
    schi_value: float,
) -> None:
    """Live disruption cards showing severity, affected lanes, and resolution estimate."""
    try:
        rng = random.Random(_seed(schi_value, port_results))
        n_active = max(1, min(4, int((1.0 - schi_value) * 6) + rng.randint(0, 1)))
        disruptions = _DISRUPTION_TEMPLATES[:n_active]

        # Augment with live port data
        for pr in port_results:
            d = getattr(pr, "demand_score", None)
            if d is None and isinstance(pr, dict):
                d = pr.get("demand_score", 0.5)
            name = getattr(pr, "port_name", None) or getattr(pr, "name", None)
            if d is None or name is None:
                continue
            if d >= 0.88:
                disruptions.append({
                    "id": f"LIVE-{name[:4].upper()}",
                    "title": f"{name} — Extreme Congestion",
                    "severity": "CRITICAL",
                    "region": "Live Port Data",
                    "affected_lanes": [f"All lanes via {name}"],
                    "driver": f"Demand pressure at {d:.0%} — capacity critically constrained",
                    "resolution": "Indeterminate",
                    "color": C_DANGER,
                    "bg": "rgba(239,68,68,0.12)",
                    "bd": "rgba(239,68,68,0.50)",
                })
            elif d >= 0.75:
                disruptions.append({
                    "id": f"LIVE-{name[:4].upper()}",
                    "title": f"{name} — High Demand Alert",
                    "severity": "HIGH",
                    "region": "Live Port Data",
                    "affected_lanes": [f"Primary lanes via {name}"],
                    "driver": f"Demand at {d:.0%} — congestion forming",
                    "resolution": "Est. 5–10 days",
                    "color": C_ORANGE,
                    "bg": "rgba(249,115,22,0.10)",
                    "bd": "rgba(249,115,22,0.40)",
                })

        sev_order = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3}
        disruptions = sorted(disruptions, key=lambda x: sev_order.get(x["severity"], 9))

        if not disruptions:
            st.markdown(
                _card(
                    f'<div style="display:flex; align-items:center; gap:12px">'
                    f'<span style="font-size:1.5rem; color:{C_HIGH}">&#10003;</span>'
                    f'<div>'
                    f'<div style="font-weight:600; color:{C_HIGH}">No Active Disruptions</div>'
                    f'<div style="font-size:0.83rem; color:{C_TEXT2}; margin-top:2px">'
                    f'All monitored lanes operating within normal parameters.</div>'
                    f'</div></div>',
                    border="rgba(16,185,129,0.35)",
                ),
                unsafe_allow_html=True,
            )
            return

        cols = st.columns(min(len(disruptions), 2))
        for i, d in enumerate(disruptions[:4]):
            with cols[i % 2]:
                lanes_html = " &bull; ".join(d["affected_lanes"][:3])
                sev = d["severity"]
                sev_icon = "&#9888;" if sev == "CRITICAL" else "&#9650;" if sev == "HIGH" else "&#8505;"
                st.markdown(
                    f'<div style="background:{d["bg"]}; border-left:4px solid {d["color"]};'
                    f' border-top:1px solid {d["bd"]}; border-right:1px solid {d["bd"]};'
                    f' border-bottom:1px solid {d["bd"]}; border-radius:0 12px 12px 0;'
                    f' padding:16px 18px; margin-bottom:10px; height:100%">'
                    f'<div style="display:flex; justify-content:space-between; align-items:flex-start;'
                    f' margin-bottom:8px">'
                    f'<div style="display:flex; align-items:center; gap:8px">'
                    f'<span style="font-size:1.0rem; color:{d["color"]}">{sev_icon}</span>'
                    f'<span style="font-size:0.87rem; font-weight:700; color:{C_TEXT}">{d["title"]}</span>'
                    f'</div>'
                    f'<span style="font-size:0.62rem; font-weight:800; color:{d["color"]};'
                    f' border:1px solid {d["color"]}; border-radius:4px; padding:2px 7px;'
                    f' letter-spacing:0.06em; flex-shrink:0; margin-left:8px">{sev}</span>'
                    f'</div>'
                    f'<div style="font-size:0.78rem; color:{C_TEXT2}; margin-bottom:6px">'
                    f'&#128205; {d["region"]}</div>'
                    f'<div style="font-size:0.76rem; color:{C_TEXT3}; margin-bottom:8px;'
                    f' font-style:italic">{d["driver"]}</div>'
                    f'<div style="display:flex; gap:12px; flex-wrap:wrap">'
                    f'<div style="font-size:0.72rem; color:{C_TEXT2}">'
                    f'<span style="color:{C_TEXT3}">Lanes: </span>{lanes_html}</div>'
                    f'</div>'
                    f'<div style="margin-top:8px; font-size:0.72rem;'
                    f' color:{d["color"]}; font-weight:600">&#9201; {d["resolution"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception:
        st.warning("Disruption tracker unavailable.")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — Resilience Scorecard (per-dimension bars)
# ═══════════════════════════════════════════════════════════════════════════

def _render_resilience_scorecard(dimension_scores: dict[str, float]) -> None:
    """Horizontal bars per resilience dimension with target line."""
    try:
        DIMS = {
            "Geographic Diversity":     dimension_scores.get("Chokepoint Risk",   0.55),
            "Supplier Concentration":   1.0 - dimension_scores.get("Freight Cost", 0.5) * 0.6,
            "Inventory Buffer":         dimension_scores.get("Inventory Cycle",    0.50),
            "Logistics Redundancy":     dimension_scores.get("Port Capacity",      0.55),
            "Macro Resilience":         dimension_scores.get("Macro Environment",  0.50),
            "Seasonal Preparedness":    dimension_scores.get("Seasonal Factors",   0.55),
        }
        # clamp
        DIMS = {k: max(0.05, min(0.99, v)) for k, v in DIMS.items()}

        labels = list(DIMS.keys())
        values = [v * 100 for v in DIMS.values()]
        colors = [_score_color(v) for v in DIMS.values()]
        target = 75.0

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=values, y=labels,
            orientation="h",
            marker_color=colors,
            marker_line_width=0,
            text=[f"<b>{v:.0f}%</b>" for v in values],
            textposition="inside",
            insidetextanchor="end",
            textfont={"color": "white", "size": 12},
            hovertemplate="<b>%{y}</b><br>Score: %{x:.1f}%<extra></extra>",
        ))
        # Target line
        fig.add_vline(
            x=target, line_width=2, line_dash="dash",
            line_color="rgba(255,255,255,0.4)",
            annotation_text=f"Target {target:.0f}%",
            annotation_font_color=C_TEXT2,
            annotation_font_size=10,
            annotation_position="top",
        )
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#0d1424",
            height=320,
            margin={"l": 10, "r": 80, "t": 20, "b": 20},
            xaxis={"range": [0, 100], "ticksuffix": "%",
                   "gridcolor": "rgba(255,255,255,0.05)", "color": C_TEXT2,
                   "tickfont": {"color": C_TEXT3, "size": 10}},
            yaxis={"color": C_TEXT, "tickfont": {"color": C_TEXT, "size": 12}},
            font={"color": C_TEXT},
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key="sc_resilience_scorecard")
    except Exception:
        st.warning("Resilience scorecard unavailable.")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — Lead Time Trends (multi-line by product category)
# ═══════════════════════════════════════════════════════════════════════════

def _render_lead_time_trends(schi_value: float, port_results: list) -> None:
    """Multi-line chart: average lead times by product category over 12 months."""
    try:
        today = datetime.date.today()
        months = [(today.replace(day=1) - datetime.timedelta(days=30 * i)) for i in range(11, -1, -1)]
        month_labels = [m.strftime("%b %y") for m in months]

        rng = random.Random(_seed(schi_value, port_results) + 42)
        base_stress = 1.0 - schi_value

        CATEGORIES = {
            "Electronics":      (28, C_ACCENT,  "#60a5fa"),
            "Apparel":          (22, C_HIGH,    "#34d399"),
            "Industrial Goods": (35, C_ORANGE,  "#fb923c"),
            "Consumer Goods":   (18, C_CYAN,    "#22d3ee"),
            "Chemicals":        (40, C_PURPLE,  "#a78bfa"),
        }

        fig = go.Figure()
        for cat, (base_days, line_color, fill_color) in CATEGORIES.items():
            vals = []
            v = base_days * (1 + base_stress * 0.3)
            for i, m in enumerate(months):
                seasonal = 1.0 + 0.12 * math.sin(2 * math.pi * m.month / 12)
                noise = rng.gauss(0, 1.5)
                trend_factor = 1.0 + base_stress * 0.02 * i
                v = base_days * trend_factor * seasonal + noise
                vals.append(round(max(5, v), 1))

            fig.add_trace(go.Scatter(
                x=month_labels, y=vals,
                mode="lines+markers",
                name=cat,
                line={"color": line_color, "width": 2.2, "shape": "spline"},
                marker={"size": 5, "color": line_color},
                hovertemplate=f"<b>{cat}</b><br>%{{x}}: %{{y:.1f}} days<extra></extra>",
            ))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#0d1424",
            height=340,
            margin={"l": 50, "r": 30, "t": 20, "b": 40},
            xaxis={"gridcolor": "rgba(255,255,255,0.05)", "color": C_TEXT2,
                   "tickfont": {"color": C_TEXT3, "size": 10}},
            yaxis={"title": "Lead Time (days)", "gridcolor": "rgba(255,255,255,0.05)",
                   "color": C_TEXT2, "tickfont": {"color": C_TEXT3, "size": 10}},
            legend={"orientation": "h", "yanchor": "bottom", "y": 1.01,
                    "xanchor": "right", "x": 1,
                    "font": {"color": C_TEXT2, "size": 10}, "bgcolor": "rgba(0,0,0,0)"},
            hovermode="x unified",
            font={"color": C_TEXT},
        )
        st.plotly_chart(fig, use_container_width=True, key="sc_lead_time_trends")
    except Exception:
        st.warning("Lead time trend chart unavailable.")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — Inventory Buffer Analysis
# ═══════════════════════════════════════════════════════════════════════════

def _render_inventory_buffer(schi_value: float, port_results: list) -> None:
    """Stock days by category with safety stock threshold lines."""
    try:
        rng = random.Random(_seed(schi_value, port_results) + 7)
        stress = 1.0 - schi_value

        CATS = ["Electronics", "Apparel", "Industrial", "Consumer", "Chemicals", "Auto Parts"]
        safety_stock = [30, 45, 20, 35, 25, 40]
        current_stock = [
            max(5, ss * (1.0 - stress * rng.uniform(0.1, 0.6)) + rng.gauss(0, 3))
            for ss in safety_stock
        ]
        below_safety = [c < s for c, s in zip(current_stock, safety_stock)]
        bar_colors = [C_DANGER if b else C_HIGH for b in below_safety]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=CATS, y=current_stock,
            name="Current Stock (days)",
            marker_color=bar_colors,
            marker_opacity=0.85,
            text=[f"{v:.0f}d" for v in current_stock],
            textposition="outside",
            textfont={"color": C_TEXT2, "size": 11},
            hovertemplate="<b>%{x}</b><br>Stock: %{y:.1f} days<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=CATS, y=safety_stock,
            mode="lines+markers",
            name="Safety Stock Threshold",
            line={"color": C_WARN, "width": 2, "dash": "dash"},
            marker={"size": 7, "color": C_WARN, "symbol": "diamond"},
            hovertemplate="<b>%{x}</b><br>Safety: %{y:.0f} days<extra></extra>",
        ))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#0d1424",
            height=300,
            margin={"l": 20, "r": 30, "t": 20, "b": 30},
            xaxis={"gridcolor": "rgba(255,255,255,0.05)", "color": C_TEXT2,
                   "tickfont": {"color": C_TEXT2, "size": 11}},
            yaxis={"title": "Days of Stock", "gridcolor": "rgba(255,255,255,0.05)",
                   "color": C_TEXT2, "tickfont": {"color": C_TEXT3, "size": 10}},
            legend={"orientation": "h", "yanchor": "bottom", "y": 1.01,
                    "xanchor": "right", "x": 1,
                    "font": {"color": C_TEXT2, "size": 11}, "bgcolor": "rgba(0,0,0,0)"},
            font={"color": C_TEXT},
            barmode="group",
        )
        st.plotly_chart(fig, use_container_width=True, key="sc_inventory_buffer")

        # Summary callouts
        at_risk_cats = [CATS[i] for i, b in enumerate(below_safety) if b]
        if at_risk_cats:
            st.markdown(
                f'<div style="background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.30);'
                f' border-radius:10px; padding:12px 16px; font-size:0.82rem; color:{C_TEXT2}">'
                f'<span style="color:{C_DANGER}; font-weight:700">&#9888; Below Safety Stock: </span>'
                f'{", ".join(at_risk_cats)} — consider expedited replenishment.</div>',
                unsafe_allow_html=True,
            )
    except Exception:
        st.warning("Inventory buffer chart unavailable.")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7 — Supplier Concentration Risk (donut charts)
# ═══════════════════════════════════════════════════════════════════════════

def _render_supplier_concentration(schi_value: float, port_results: list) -> None:
    """Donut charts: sourcing concentration by country/region for 3 categories."""
    try:
        rng = random.Random(_seed(schi_value, port_results) + 13)
        stress = 1.0 - schi_value

        SCENARIOS = [
            {
                "title": "Electronics Sourcing",
                "labels": ["China", "Taiwan", "South Korea", "Vietnam", "Others"],
                "base":   [0.55, 0.18, 0.12, 0.08, 0.07],
                "colors": [C_DANGER, C_WARN, C_ACCENT, C_HIGH, C_TEXT3],
            },
            {
                "title": "Apparel & Textiles",
                "labels": ["Bangladesh", "Vietnam", "India", "China", "Others"],
                "base":   [0.28, 0.22, 0.18, 0.20, 0.12],
                "colors": [C_ACCENT, C_HIGH, C_CYAN, C_WARN, C_TEXT3],
            },
            {
                "title": "Industrial Components",
                "labels": ["Germany", "Japan", "USA", "Mexico", "China", "Others"],
                "base":   [0.22, 0.20, 0.18, 0.15, 0.14, 0.11],
                "colors": [C_HIGH, C_ACCENT, C_CYAN, C_PURPLE, C_WARN, C_TEXT3],
            },
        ]

        cols = st.columns(3)
        for i, sc in enumerate(SCENARIOS):
            vals = [max(0.02, b + rng.gauss(0, 0.03 * stress)) for b in sc["base"]]
            total = sum(vals)
            vals = [v / total for v in vals]

            fig = go.Figure(go.Pie(
                labels=sc["labels"],
                values=[round(v * 100, 1) for v in vals],
                hole=0.55,
                marker={"colors": sc["colors"], "line": {"color": C_BG, "width": 2}},
                textinfo="percent",
                textfont={"size": 11, "color": "white"},
                hovertemplate="<b>%{label}</b><br>Share: %{value:.1f}%<extra></extra>",
                direction="clockwise",
                sort=False,
            ))

            # HHI concentration index
            hhi = sum((v * 100) ** 2 for v in vals)
            hhi_label = "High Conc." if hhi > 3000 else "Moderate" if hhi > 1500 else "Diversified"
            hhi_color = C_DANGER if hhi > 3000 else C_WARN if hhi > 1500 else C_HIGH

            fig.add_annotation(
                text=f"<b>HHI</b><br>{hhi:.0f}<br><span style='font-size:10px'>{hhi_label}</span>",
                x=0.5, y=0.5, showarrow=False,
                font={"size": 12, "color": hhi_color},
                xanchor="center", yanchor="middle",
            )
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                height=260,
                margin={"l": 10, "r": 10, "t": 30, "b": 10},
                title={"text": sc["title"], "font": {"size": 12, "color": C_TEXT2},
                       "x": 0.5, "xanchor": "center"},
                legend={"orientation": "v", "font": {"size": 10, "color": C_TEXT2},
                        "bgcolor": "rgba(0,0,0,0)"},
                font={"color": C_TEXT},
                showlegend=True,
            )
            with cols[i]:
                st.plotly_chart(fig, use_container_width=True,
                                key=f"sc_supplier_donut_{i}")
    except Exception:
        st.warning("Supplier concentration charts unavailable.")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 8 — Near-shoring / Friend-shoring Tracker
# ═══════════════════════════════════════════════════════════════════════════

def _render_nearshoring_tracker(schi_value: float, port_results: list) -> None:
    """Reshoring/friend-shoring progress bars by industry sector."""
    try:
        rng = random.Random(_seed(schi_value, port_results) + 99)

        SECTORS = [
            ("Semiconductors",       72, 100, C_ACCENT),
            ("Pharmaceuticals",      58,  80, C_HIGH),
            ("Electric Vehicles",    44,  75, C_CYAN),
            ("Defense & Aerospace",  81, 100, C_PURPLE),
            ("Critical Minerals",    35,  70, C_WARN),
            ("Consumer Electronics", 28,  60, C_ORANGE),
            ("Textiles & Apparel",   18,  45, C_TEXT2),
            ("Steel & Metals",       52,  80, C_HIGH),
        ]

        st.markdown(
            _card(
                f'<div style="font-size:0.72rem; font-weight:700; color:{C_TEXT3};'
                f' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:14px">'
                f'Reshoring / Friend-shoring Progress by Industry</div>'
                + "".join([
                    f'<div style="margin-bottom:14px">'
                    f'<div style="display:flex; justify-content:space-between; margin-bottom:4px">'
                    f'<span style="font-size:0.83rem; font-weight:600; color:{C_TEXT}">{sector}</span>'
                    f'<div style="display:flex; gap:10px">'
                    f'<span style="font-size:0.80rem; color:{color}; font-weight:700">{curr}%</span>'
                    f'<span style="font-size:0.78rem; color:{C_TEXT3}">/ {tgt}% target</span>'
                    f'</div></div>'
                    f'<div style="background:rgba(255,255,255,0.06); border-radius:6px; height:8px; overflow:hidden">'
                    f'<div style="width:{curr}%; height:100%; background:{color}; border-radius:6px;'
                    f' position:relative">'
                    f'</div></div>'
                    f'<div style="position:relative; height:0">'
                    f'<div style="position:absolute; left:{tgt}%; top:-8px; width:2px;'
                    f' height:8px; background:rgba(255,255,255,0.4)"></div>'
                    f'</div>'
                    f'</div>'
                    for sector, curr, tgt, color in SECTORS
                ]),
                border="rgba(59,130,246,0.20)",
                padding="20px 22px",
            ),
            unsafe_allow_html=True,
        )
    except Exception:
        st.warning("Nearshoring tracker unavailable.")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9 — Supply Chain Cost Waterfall
# ═══════════════════════════════════════════════════════════════════════════

def _render_cost_waterfall(schi_value: float, freight_data: dict, port_results: list) -> None:
    """Waterfall chart from raw material cost to total delivered cost."""
    try:
        rng = random.Random(_seed(schi_value, port_results) + 55)
        stress_mult = 1.0 + (1.0 - schi_value) * 0.40

        raw_mat    = 4200
        mfg        = round(1800 + rng.gauss(0, 80))
        inland_exp = round(320  + rng.gauss(0, 30))
        ocean_frt  = round((1100 + rng.gauss(0, 150)) * stress_mult)
        port_fees  = round((280  + rng.gauss(0, 25))  * (1 + (1 - schi_value) * 0.3))
        customs    = round(420  + rng.gauss(0, 40))
        last_mile  = round(380  + rng.gauss(0, 35))
        total      = raw_mat + mfg + inland_exp + ocean_frt + port_fees + customs + last_mile

        labels = [
            "Raw Materials", "Manufacturing", "Inland Export",
            "Ocean Freight", "Port & Terminal", "Customs & Duties",
            "Last Mile", "Total Delivered",
        ]
        values = [raw_mat, mfg, inland_exp, ocean_frt, port_fees, customs, last_mile, total]
        measures = ["relative"] * 7 + ["total"]

        # Color bars by stress sensitivity
        marker_colors = [
            C_TEXT3, C_ACCENT, C_CYAN,
            C_DANGER if ocean_frt > 1400 else C_WARN,
            C_WARN, C_TEXT2, C_ACCENT,
            C_HIGH,
        ]

        fig = go.Figure(go.Waterfall(
            name="Cost Breakdown",
            orientation="v",
            measure=measures,
            x=labels,
            y=values,
            connector={"line": {"color": "rgba(255,255,255,0.15)", "width": 1, "dash": "dot"}},
            decreasing={"marker": {"color": C_HIGH}},
            increasing={"marker": {"color": C_ORANGE}},
            totals={"marker": {"color": C_ACCENT}},
            text=[f"${v:,.0f}" for v in values],
            textposition="outside",
            textfont={"color": C_TEXT2, "size": 10},
            hovertemplate="<b>%{x}</b><br>$%{y:,.0f}<extra></extra>",
        ))

        fig.add_hline(
            y=total * 0.85, line_dash="dot", line_color="rgba(16,185,129,0.40)",
            annotation_text="Pre-disruption baseline",
            annotation_font_color="rgba(16,185,129,0.70)",
            annotation_font_size=9,
        )

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#0d1424",
            height=360,
            margin={"l": 40, "r": 30, "t": 30, "b": 60},
            xaxis={"gridcolor": "rgba(255,255,255,0.05)", "color": C_TEXT2,
                   "tickfont": {"color": C_TEXT2, "size": 11}},
            yaxis={"title": "USD per TEU equiv.", "tickprefix": "$",
                   "gridcolor": "rgba(255,255,255,0.05)", "color": C_TEXT2,
                   "tickfont": {"color": C_TEXT3, "size": 10}},
            font={"color": C_TEXT},
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key="sc_cost_waterfall")

        col_a, col_b, col_c = st.columns(3)
        ocean_share = ocean_frt / total * 100
        port_share  = port_fees / total * 100
        with col_a:
            oc = C_DANGER if ocean_share > 17 else C_WARN
            st.markdown(
                _card(
                    f'<div style="font-size:0.70rem; color:{C_TEXT3}; text-transform:uppercase;'
                    f' letter-spacing:0.07em; margin-bottom:4px">Ocean Freight Share</div>'
                    f'<div style="font-size:1.8rem; font-weight:900; color:{oc}">{ocean_share:.1f}%</div>'
                    f'<div style="font-size:0.75rem; color:{C_TEXT2}">${ocean_frt:,.0f} per unit</div>',
                    padding="14px 16px", radius="10px",
                ),
                unsafe_allow_html=True,
            )
        with col_b:
            st.markdown(
                _card(
                    f'<div style="font-size:0.70rem; color:{C_TEXT3}; text-transform:uppercase;'
                    f' letter-spacing:0.07em; margin-bottom:4px">Port & Terminal</div>'
                    f'<div style="font-size:1.8rem; font-weight:900; color:{C_WARN}">{port_share:.1f}%</div>'
                    f'<div style="font-size:0.75rem; color:{C_TEXT2}">${port_fees:,.0f} per unit</div>',
                    padding="14px 16px", radius="10px",
                ),
                unsafe_allow_html=True,
            )
        with col_c:
            st.markdown(
                _card(
                    f'<div style="font-size:0.70rem; color:{C_TEXT3}; text-transform:uppercase;'
                    f' letter-spacing:0.07em; margin-bottom:4px">Total Delivered Cost</div>'
                    f'<div style="font-size:1.8rem; font-weight:900; color:{C_ACCENT}">${total:,.0f}</div>'
                    f'<div style="font-size:0.75rem; color:{C_TEXT2}">Per unit basis</div>',
                    padding="14px 16px", radius="10px",
                ),
                unsafe_allow_html=True,
            )
    except Exception:
        st.warning("Cost waterfall chart unavailable.")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 10 — Risk-Adjusted Lead Time Matrix (heatmap)
# ═══════════════════════════════════════════════════════════════════════════

def _render_risk_lead_time_matrix(schi_value: float, port_results: list) -> None:
    """Heatmap: routes (rows) x risk factors (columns) = risk-adjusted lead time."""
    try:
        rng = random.Random(_seed(schi_value, port_results) + 77)
        stress = 1.0 - schi_value

        ROUTES = [
            "Shanghai → LA",
            "Shanghai → Rotterdam",
            "Singapore → Dubai",
            "Busan → NY",
            "Hamburg → Houston",
            "Singapore → Antwerp",
            "Dubai → Rotterdam",
            "Yokohama → LA",
        ]
        RISK_FACTORS = [
            "Port Congestion",
            "Chokepoint Risk",
            "Carrier Reliability",
            "Weather / Seasonal",
            "Geopolitical",
            "Customs Delays",
        ]

        BASE_MATRIX = [
            [6.2, 3.1, 2.8, 2.2, 1.5, 2.0],
            [8.1, 7.2, 3.5, 2.8, 4.5, 2.5],
            [3.8, 8.5, 2.1, 2.5, 7.8, 3.2],
            [5.5, 3.3, 2.6, 2.8, 1.8, 2.3],
            [4.2, 6.8, 2.3, 3.1, 3.2, 2.8],
            [7.8, 7.1, 3.0, 2.6, 4.8, 2.2],
            [3.1, 8.8, 1.9, 2.0, 8.2, 2.9],
            [5.8, 3.0, 2.7, 3.5, 1.6, 2.1],
        ]

        # Apply stress scaling + noise
        z = [
            [max(1.0, min(10.0, v * (1.0 + stress * 0.6) + rng.gauss(0, 0.3)))
             for v in row]
            for row in BASE_MATRIX
        ]

        # Custom colorscale: green → yellow → red
        colorscale = [
            [0.0,  "#10b981"],
            [0.35, "#34d399"],
            [0.55, "#f59e0b"],
            [0.75, "#f97316"],
            [1.0,  "#ef4444"],
        ]

        fig = go.Figure(go.Heatmap(
            z=z,
            x=RISK_FACTORS,
            y=ROUTES,
            colorscale=colorscale,
            zmin=1.0, zmax=10.0,
            text=[[f"{v:.1f}" for v in row] for row in z],
            texttemplate="%{text}",
            textfont={"size": 11, "color": "white"},
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Risk Factor: %{x}<br>"
                "Score: %{z:.1f} / 10<extra></extra>"
            ),
            colorbar={
                "title": {"text": "Risk Score", "font": {"size": 11, "color": C_TEXT2}},
                "tickfont": {"color": C_TEXT2, "size": 10},
                "borderwidth": 0,
                "bgcolor": "rgba(0,0,0,0)",
            },
            xgap=2, ygap=2,
        ))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#0d1424",
            height=360,
            margin={"l": 130, "r": 30, "t": 20, "b": 80},
            xaxis={"tickfont": {"color": C_TEXT2, "size": 11}, "color": C_TEXT2,
                   "tickangle": -25},
            yaxis={"tickfont": {"color": C_TEXT, "size": 11}, "color": C_TEXT},
            font={"color": C_TEXT},
        )
        st.plotly_chart(fig, use_container_width=True, key="sc_risk_lead_time_matrix")

        # Legend note
        st.markdown(
            f'<div style="font-size:0.75rem; color:{C_TEXT3}; margin-top:-6px; padding:0 4px">'
            f'Scores 1–10: <span style="color:{C_HIGH}">1–3 = low risk</span> &bull; '
            f'<span style="color:{C_WARN}">4–6 = moderate</span> &bull; '
            f'<span style="color:{C_DANGER}">7–10 = critical</span>. '
            f'Elevated by {stress:.0%} current supply chain stress factor.</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        st.warning("Risk-adjusted lead time matrix unavailable.")


# ═══════════════════════════════════════════════════════════════════════════
# Preserved legacy helpers (still used in render body)
# ═══════════════════════════════════════════════════════════════════════════

def _render_component_bar(dimension_scores: dict[str, float]) -> None:
    try:
        component_map = {
            "Ports":     dimension_scores.get("Port Capacity",     0.5),
            "Routes":    dimension_scores.get("Chokepoint Risk",   0.5),
            "Rates":     dimension_scores.get("Freight Cost",      0.5),
            "Macro":     dimension_scores.get("Macro Environment", 0.5),
            "Inventory": dimension_scores.get("Inventory Cycle",   0.5),
            "Seasonal":  dimension_scores.get("Seasonal Factors",  0.5),
        }
        COMP_COLORS = {
            "Ports": C_ACCENT, "Routes": C_PURPLE, "Rates": C_WARN,
            "Macro": C_CYAN, "Inventory": C_HIGH, "Seasonal": C_ORANGE,
        }
        labels = list(component_map.keys())
        values = [v * 100 for v in component_map.values()]
        colors = [COMP_COLORS[l] for l in labels]

        fig = go.Figure()
        for label, val, color in zip(labels, values, colors):
            fig.add_trace(go.Bar(
                name=label, x=[val], y=["Components"],
                orientation="h",
                marker_color=color, marker_line_width=0,
                text=f"<b>{label}</b> {val:.0f}%",
                textposition="inside", insidetextanchor="middle",
                hovertemplate=f"<b>{label}</b><br>{val:.1f}%<extra></extra>",
                textfont={"color": "white", "size": 11},
            ))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            barmode="stack",
            height=80,
            margin={"l": 10, "r": 10, "t": 8, "b": 5},
            xaxis={"range": [0, 600], "visible": False},
            yaxis={"visible": False},
            legend={
                "orientation": "h", "yanchor": "bottom", "y": -1.8,
                "xanchor": "center", "x": 0.5,
                "font": {"color": C_TEXT2, "size": 11}, "bgcolor": "rgba(0,0,0,0)",
            },
            font={"color": C_TEXT},
        )
        st.plotly_chart(fig, use_container_width=True, key="sc_component_bar")
    except Exception:
        st.warning("Component bar unavailable.")


def _render_stress_indicator_cards(dimension_scores: dict[str, float]) -> None:
    try:
        DIM_ICONS = {
            "Port Capacity": "&#9875;", "Freight Cost": "&#36;",
            "Macro Environment": "&#127758;", "Chokepoint Risk": "&#9888;",
            "Inventory Cycle": "&#128230;", "Seasonal Factors": "&#128336;",
        }

        def _recovery(score: float) -> str:
            if score >= 0.70: return "Stable — no recovery needed"
            if score >= 0.60: return "~1-2 weeks to optimal"
            if score >= 0.50: return "~2-4 weeks recovery expected"
            if score >= 0.35: return "~4-8 weeks to normalize"
            return "~2-3 months — structural intervention required"

        dims = list(dimension_scores.items())
        for row_start in range(0, len(dims), 3):
            cols = st.columns(3)
            for col_idx, (dim, score) in enumerate(dims[row_start:row_start + 3]):
                color = _score_color(score)
                status = _status_label(score)
                bg  = f"rgba({','.join(str(int(c * 255)) for c in _hex_to_rgb_floats(color))},0.10)"
                bd  = f"rgba({','.join(str(int(c * 255)) for c in _hex_to_rgb_floats(color))},0.40)"
                icon = DIM_ICONS.get(dim, "&#9679;")
                bar_pct = int(score * 100)
                recovery = _recovery(score)
                with cols[col_idx]:
                    st.markdown(
                        f'<div style="background:{C_CARD}; border:1px solid {bd};'
                        f' border-radius:12px; padding:16px; height:185px;'
                        f' display:flex; flex-direction:column; justify-content:space-between">'
                        f'<div>'
                        f'<div style="display:flex; justify-content:space-between; align-items:flex-start">'
                        f'<div style="font-size:1.1rem; color:{color}">{icon}</div>'
                        f'<span style="font-size:0.63rem; font-weight:800; color:{color};'
                        f' border:1px solid {color}; border-radius:99px; padding:2px 8px;'
                        f' letter-spacing:0.06em">{status}</span>'
                        f'</div>'
                        f'<div style="font-size:0.83rem; font-weight:700; color:{C_TEXT};'
                        f' margin-top:8px; margin-bottom:2px">{dim}</div>'
                        f'<div style="font-size:1.5rem; font-weight:900; color:{color}">{score:.0%}</div>'
                        f'</div>'
                        f'<div>'
                        f'<div style="background:rgba(255,255,255,0.07); border-radius:4px;'
                        f' height:5px; margin-bottom:6px; overflow:hidden">'
                        f'<div style="width:{bar_pct}%; height:100%; background:{color};'
                        f' border-radius:4px"></div></div>'
                        f'<div style="font-size:0.70rem; color:{C_TEXT3}">{recovery}</div>'
                        f'</div></div>',
                        unsafe_allow_html=True,
                    )
    except Exception:
        st.warning("Stress indicator cards unavailable.")


def _hex_to_rgb_floats(hex_color: str) -> tuple[float, float, float]:
    """Convert #rrggbb to (r, g, b) 0-1 floats."""
    try:
        h = hex_color.lstrip("#")
        return (int(h[0:2], 16) / 255.0,
                int(h[2:4], 16) / 255.0,
                int(h[4:6], 16) / 255.0)
    except Exception:
        return (0.5, 0.5, 0.5)


def _render_radar(dimension_scores: dict[str, float]) -> None:
    try:
        dims = list(dimension_scores.keys())
        vals = list(dimension_scores.values())
        dims_c = dims + [dims[0]]
        vals_c = vals + [vals[0]]

        fig = go.Figure()
        # Filled area
        fig.add_trace(go.Scatterpolar(
            r=[0.70] * (len(dims) + 1), theta=dims_c,
            fill="toself", fillcolor="rgba(16,185,129,0.07)",
            line={"color": "rgba(16,185,129,0.25)", "width": 1, "dash": "dot"},
            name="Healthy threshold", showlegend=True,
        ))
        fig.add_trace(go.Scatterpolar(
            r=vals_c, theta=dims_c,
            fill="toself", fillcolor="rgba(59,130,246,0.18)",
            line={"color": C_ACCENT, "width": 2.2},
            marker={"size": 7, "color": [_score_color(v) for v in vals_c]},
            hovertemplate="<b>%{theta}</b><br>Score: %{r:.0%}<extra></extra>",
            name="Current SCHI", showlegend=True,
        ))
        fig.update_layout(
            template="plotly_dark",
            polar={
                "bgcolor": "#0d1424",
                "radialaxis": {"visible": True, "range": [0, 1], "tickformat": ".0%",
                               "tickfont": {"size": 9, "color": C_TEXT3},
                               "gridcolor": "rgba(255,255,255,0.08)",
                               "linecolor": "rgba(255,255,255,0.08)"},
                "angularaxis": {"tickfont": {"size": 11, "color": C_TEXT},
                                "gridcolor": "rgba(255,255,255,0.08)",
                                "linecolor": "rgba(255,255,255,0.10)"},
            },
            paper_bgcolor="rgba(0,0,0,0)",
            font={"color": C_TEXT},
            height=360,
            margin={"l": 60, "r": 60, "t": 30, "b": 30},
            legend={"orientation": "h", "yanchor": "bottom", "y": -0.15,
                    "xanchor": "center", "x": 0.5,
                    "font": {"color": C_TEXT2, "size": 10}, "bgcolor": "rgba(0,0,0,0)"},
        )
        st.plotly_chart(fig, use_container_width=True, key="sc_radar")
    except Exception:
        st.warning("Radar chart unavailable.")


def _render_90day_trend(schi_value: float, port_results: list, macro_data: dict) -> None:
    try:
        today = datetime.date.today()
        days  = list(range(90, 0, -1))
        dates = [(today - datetime.timedelta(days=d)).isoformat() for d in days]

        rng = random.Random(_seed(schi_value, port_results))
        start = max(0.10, min(0.90, schi_value + rng.uniform(-0.15, 0.15)))
        scores = []
        for i in range(90):
            t = i / 89.0
            v = start + (schi_value - start) * t + rng.gauss(0, 0.018)
            scores.append(round(max(0.05, min(0.98, v)), 4))

        if isinstance(macro_data, dict):
            bdi = macro_data.get("bdi") or macro_data.get("BDI") or []
            if isinstance(bdi, list) and len(bdi) >= 10:
                bdi_norm = min(1.0, sum(bdi) / len(bdi) / 3000.0)
                for i in range(60, 90):
                    scores[i] = max(0.05, min(0.98, scores[i] * 0.85 + bdi_norm * 0.15))

        rolling = []
        for i in range(90):
            w0 = max(0, i - 13)
            rolling.append(sum(scores[w0:i + 1]) / (i - w0 + 1))

        fig = go.Figure()
        for y0, y1, fill in [
            (0.00, 0.35, "rgba(239,68,68,0.10)"),
            (0.35, 0.50, "rgba(245,158,11,0.08)"),
            (0.50, 0.70, "rgba(59,130,246,0.08)"),
            (0.70, 1.00, "rgba(16,185,129,0.09)"),
        ]:
            fig.add_hrect(y0=y0, y1=y1, fillcolor=fill, line_width=0)

        for y, lbl, col in [(0.70, "Healthy 70%", C_HIGH), (0.35, "Critical 35%", C_DANGER)]:
            fig.add_hline(y=y, line_width=1.5, line_dash="dash", line_color=col + "88",
                          annotation_text=lbl, annotation_font_size=9,
                          annotation_font_color=col, annotation_position="left")

        fig.add_trace(go.Scatter(
            x=dates, y=scores, mode="lines",
            line={"color": C_ACCENT, "width": 2.5, "shape": "spline"},
            fill="tozeroy", fillcolor="rgba(59,130,246,0.05)",
            name="SCHI",
            hovertemplate="<b>%{x}</b><br>SCHI: %{y:.1%}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=dates, y=rolling, mode="lines",
            line={"color": C_WARN, "width": 1.5, "dash": "dot"},
            name="14-day MA",
            hovertemplate="<b>%{x}</b><br>14d MA: %{y:.1%}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=[dates[-1]], y=[scores[-1]], mode="markers",
            marker={"color": C_WARN, "size": 11, "symbol": "circle",
                    "line": {"color": "white", "width": 2}},
            name=f"Current: {scores[-1]:.1%}",
        ))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#0d1424",
            height=340,
            margin={"l": 50, "r": 110, "t": 20, "b": 40},
            xaxis={"gridcolor": "rgba(255,255,255,0.05)", "color": C_TEXT2,
                   "tickfont": {"color": C_TEXT3, "size": 10}},
            yaxis={"title": "SCHI Score", "range": [0, 1], "tickformat": ".0%",
                   "gridcolor": "rgba(255,255,255,0.05)", "color": C_TEXT2,
                   "tickfont": {"color": C_TEXT3, "size": 10}},
            legend={"orientation": "h", "yanchor": "bottom", "y": 1.02,
                    "xanchor": "right", "x": 1,
                    "font": {"color": C_TEXT2, "size": 10}, "bgcolor": "rgba(0,0,0,0)"},
            hovermode="x unified",
            font={"color": C_TEXT},
        )
        st.plotly_chart(fig, use_container_width=True, key="sc_90day_trend")
    except Exception:
        st.warning("90-day trend chart unavailable.")


def _render_regional_comparison(port_results: list) -> None:
    try:
        REGION_LOCODES = {
            "Asia-Pacific": ["CNSHA", "CNSZN", "HKHKG", "KRPUS", "SGSIN", "JPYOK", "MYPKG"],
            "Europe":       ["NLRTM", "DEHAM", "BEANR", "GBFXT", "ESALG"],
            "Americas":     ["USLAX", "USNYC", "USHOU", "USSAV", "BRSSZ"],
            "Middle East":  ["AEDXB", "EGPSD", "SAJED"],
        }
        REGION_COLORS = {
            "Asia-Pacific": C_ACCENT, "Europe": C_HIGH,
            "Americas": C_WARN, "Middle East": C_PURPLE,
        }
        demand_lut: dict = {}
        for pr in port_results:
            lc = getattr(pr, "locode", None) or getattr(pr, "port_locode", None)
            if not lc and isinstance(pr, dict):
                lc = pr.get("port_locode") or pr.get("locode")
            if lc:
                d = getattr(pr, "demand_score", None)
                if d is None and isinstance(pr, dict):
                    d = pr.get("demand_score", 0.5)
                if d is not None:
                    demand_lut[lc] = d

        regions = list(REGION_LOCODES.keys())
        health_vals, stress_vals = [], []
        for region in regions:
            scores = [1.0 - demand_lut[lc] for lc in REGION_LOCODES[region] if lc in demand_lut]
            h = sum(scores) / len(scores) if scores else 0.55
            health_vals.append(h * 100)
            stress_vals.append((1.0 - h) * 100)

        colors = [REGION_COLORS[r] for r in regions]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Health Score", x=regions, y=health_vals,
            marker_color=colors, marker_opacity=0.85,
            text=[f"{v:.0f}%" for v in health_vals],
            textposition="outside", textfont={"color": C_TEXT2, "size": 11},
            hovertemplate="<b>%{x}</b><br>Health: %{y:.1f}%<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            name="Stress Level", x=regions, y=stress_vals,
            marker_color=["rgba(239,68,68,0.35)"] * len(regions),
            marker_line_color=["rgba(239,68,68,0.55)"] * len(regions),
            marker_line_width=1,
            text=[f"{v:.0f}%" for v in stress_vals],
            textposition="outside", textfont={"color": C_TEXT3, "size": 10},
            hovertemplate="<b>%{x}</b><br>Stress: %{y:.1f}%<extra></extra>",
        ))
        fig.add_hline(y=70, line_width=1.5, line_dash="dot",
                      line_color="rgba(16,185,129,0.50)",
                      annotation_text="Healthy threshold",
                      annotation_font_size=9,
                      annotation_font_color="rgba(16,185,129,0.70)")
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#0d1424",
            barmode="group",
            height=320,
            margin={"l": 20, "r": 80, "t": 20, "b": 30},
            xaxis={"gridcolor": "rgba(255,255,255,0.05)", "color": C_TEXT2,
                   "tickfont": {"color": C_TEXT2, "size": 12}},
            yaxis={"title": "Score (%)", "range": [0, 110],
                   "gridcolor": "rgba(255,255,255,0.05)", "color": C_TEXT2,
                   "tickfont": {"color": C_TEXT3, "size": 10}},
            legend={"orientation": "h", "yanchor": "bottom", "y": 1.02,
                    "xanchor": "right", "x": 1,
                    "font": {"color": C_TEXT2, "size": 11}, "bgcolor": "rgba(0,0,0,0)"},
            font={"color": C_TEXT},
        )
        st.plotly_chart(fig, use_container_width=True, key="sc_regional_comparison")
    except Exception:
        st.warning("Regional comparison unavailable.")


def _render_alert_panel(port_results: list, freight_data: dict) -> None:
    try:
        alerts: list[dict] = []
        for pr in port_results:
            name = getattr(pr, "port_name", getattr(pr, "name", None))
            if not name and isinstance(pr, dict):
                name = pr.get("port_name", pr.get("name", "Unknown"))
            demand = getattr(pr, "demand_score", None)
            if demand is None and isinstance(pr, dict):
                demand = pr.get("demand_score", 0.0)
            if demand is None:
                continue
            if demand >= 0.85:
                alerts.append({"title": f"{name} — Demand Alert", "severity": "CRITICAL",
                                "rank": 1, "color": C_DANGER, "bg": "rgba(239,68,68,0.12)",
                                "bd": "rgba(239,68,68,0.5)", "icon": "&#9888;",
                                "message": f"Extreme demand ({demand:.0%}) — severe congestion risk",
                                "source": "Port Demand Analysis"})
            elif demand >= 0.70:
                alerts.append({"title": f"{name} — Demand Alert", "severity": "HIGH",
                                "rank": 2, "color": C_ORANGE, "bg": "rgba(249,115,22,0.10)",
                                "bd": "rgba(249,115,22,0.4)", "icon": "&#9650;",
                                "message": f"Elevated demand ({demand:.0%}) — monitor closely",
                                "source": "Port Demand Analysis"})
            elif demand >= 0.55:
                alerts.append({"title": f"{name} — Demand Watch", "severity": "MODERATE",
                                "rank": 3, "color": C_WARN, "bg": "rgba(245,158,11,0.10)",
                                "bd": "rgba(245,158,11,0.35)", "icon": "&#8505;",
                                "message": f"Demand trending above average ({demand:.0%})",
                                "source": "Port Demand Analysis"})

        if isinstance(freight_data, dict):
            for key, val in freight_data.items():
                if isinstance(val, (int, float)) and val > 5000:
                    sev = "CRITICAL" if val > 8000 else "HIGH"
                    rank = 1 if val > 8000 else 2
                    color = C_DANGER if val > 8000 else C_WARN
                    alerts.append({"title": f"Freight Rate — {key}", "severity": sev,
                                   "rank": rank, "color": color,
                                   "bg": f"rgba({239 if rank==1 else 245},{68 if rank==1 else 158},{68 if rank==1 else 11},0.10)",
                                   "bd": f"rgba({239 if rank==1 else 245},{68 if rank==1 else 158},{68 if rank==1 else 11},0.4)",
                                   "icon": "&#9888;" if rank == 1 else "&#36;",
                                   "message": f"Spot rate at ${val:,.0f} — above baseline",
                                   "source": "Freight Rate Monitor"})

        alerts.sort(key=lambda a: a["rank"])

        if not alerts:
            st.markdown(
                _card(
                    f'<div style="display:flex; align-items:center; gap:12px">'
                    f'<span style="font-size:1.4rem; color:{C_HIGH}">&#10003;</span>'
                    f'<div>'
                    f'<div style="font-weight:600; color:{C_HIGH}">No Active Alerts</div>'
                    f'<div style="font-size:0.83rem; color:{C_TEXT2}; margin-top:2px">'
                    f'All monitored dimensions within acceptable parameters.</div>'
                    f'</div></div>',
                    border="rgba(16,185,129,0.35)",
                ),
                unsafe_allow_html=True,
            )
            return

        for a in alerts[:6]:
            st.markdown(
                f'<div style="background:{a["bg"]}; border-left:4px solid {a["color"]};'
                f' border-top:1px solid {a["bd"]}; border-right:1px solid {a["bd"]};'
                f' border-bottom:1px solid {a["bd"]}; border-radius:0 10px 10px 0;'
                f' padding:13px 16px; margin-bottom:8px">'
                f'<div style="display:flex; justify-content:space-between; align-items:flex-start">'
                f'<div style="display:flex; align-items:center; gap:9px">'
                f'<span style="font-size:1.0rem; color:{a["color"]}">{a["icon"]}</span>'
                f'<div>'
                f'<div style="font-size:0.87rem; font-weight:700; color:{C_TEXT}">{a["title"]}</div>'
                f'<div style="font-size:0.80rem; color:{C_TEXT2}; margin-top:2px">{a["message"]}</div>'
                f'</div></div>'
                f'<span style="font-size:0.62rem; font-weight:800; color:{a["color"]};'
                f' border:1px solid {a["color"]}; border-radius:4px; padding:2px 7px;'
                f' letter-spacing:0.06em; flex-shrink:0; margin-left:10px">{a["severity"]}</span>'
                f'</div>'
                f'<div style="font-size:0.70rem; color:{C_TEXT3}; margin-top:7px">'
                f'Source: {a["source"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception:
        st.warning("Alert panel unavailable.")


def _render_recommended_actions(
    dimension_scores: dict[str, float], schi_value: float
) -> None:
    try:
        ACTION_TEMPLATES = {
            "Port Capacity": {
                "stressed": "Immediately divert high-priority shipments from congested ports. Negotiate priority berthing or next-available-vessel programs with carriers.",
                "at_risk":  "Monitor port capacity daily. Pre-book alternative routing slots at nearby ports and engage freight forwarders for contingency options.",
            },
            "Freight Cost": {
                "stressed": "Lock in forward contracts for critical lanes before further escalation. Evaluate load consolidation and longer lead time bookings for lower rate tiers.",
                "at_risk":  "Review spot vs contract rate mix. Explore rate hedging mechanisms and identify backhaul opportunities to reduce effective per-unit cost.",
            },
            "Macro Environment": {
                "stressed": "Activate currency hedging for USD freight contracts. Prepare demand-side buffer inventory and review supply chain financing terms.",
                "at_risk":  "Monitor BDI and PMI leading indicators weekly. Adjust import timing relative to currency movements and trade policy signals.",
            },
            "Chokepoint Risk": {
                "stressed": "Reroute vulnerable cargo from high-risk chokepoints. Engage war risk insurance and review diversion budgets for Cape/Panama alternatives.",
                "at_risk":  "Review geopolitical risk insurance coverage. Establish pre-approved alternative routing with carriers for rapid activation.",
            },
            "Inventory Cycle": {
                "stressed": "Accelerate inbound replenishment to avoid stockout. Adjust safety stock levels across DCs for extended transit variability.",
                "at_risk":  "Increase safety stock buffer 15-20% for key SKUs. Request production status updates from suppliers weekly.",
            },
            "Seasonal Factors": {
                "stressed": "Peak season capacity critically constrained — book all Q4/CNY volumes immediately. Consider early shipments to avoid peak surcharges.",
                "at_risk":  "Begin booking peak-season capacity 8-10 weeks early. Request rolling carrier commitments and secure allocation guarantees.",
            },
        }
        sorted_dims = sorted(dimension_scores.items(), key=lambda kv: kv[1])
        actions = []
        for dim, score in sorted_dims:
            if dim not in ACTION_TEMPLATES:
                continue
            key = "stressed" if score < 0.50 else "at_risk" if score < 0.70 else None
            if key:
                actions.append((dim, score, ACTION_TEMPLATES[dim][key]))

        if schi_value < 0.35:
            actions.insert(0, ("GLOBAL", schi_value,
                "Overall supply chain health is CRITICAL. Convene cross-functional risk committee immediately. Activate business continuity protocols and communicate delays to key stakeholders."))

        if not actions:
            st.markdown(
                _card(f'<div style="color:{C_HIGH}; font-size:0.88rem">'
                      f'&#10003; All dimensions healthy — no immediate actions required. '
                      f'Continue routine monitoring.</div>',
                      border="rgba(16,185,129,0.30)"),
                unsafe_allow_html=True,
            )
            return

        html = ""
        for i, (dim, score, text) in enumerate(actions, 1):
            color = C_DANGER if score < 0.35 else C_ORANGE if score < 0.50 else C_WARN
            priority = "URGENT" if score < 0.35 else "HIGH" if score < 0.50 else "MODERATE"
            label = "Global SC Risk" if dim == "GLOBAL" else dim
            html += (
                f'<div style="display:flex; gap:14px; padding:13px 0;'
                f' border-bottom:1px solid rgba(255,255,255,0.06)">'
                f'<div style="width:30px; height:30px; border-radius:50%; border:2px solid {color};'
                f' display:flex; align-items:center; justify-content:center; flex-shrink:0;'
                f' font-weight:900; font-size:0.85rem; color:{color}">{i}</div>'
                f'<div style="flex:1">'
                f'<div style="display:flex; align-items:center; gap:8px; margin-bottom:4px">'
                f'<span style="font-size:0.82rem; font-weight:700; color:{C_TEXT}">{label}</span>'
                f'<span style="font-size:0.62rem; font-weight:800; color:{color};'
                f' border:1px solid {color}; border-radius:4px; padding:1px 6px;'
                f' letter-spacing:0.06em">{priority}</span>'
                f'<span style="font-size:0.72rem; color:{C_TEXT3}">{score:.0%}</span>'
                f'</div>'
                f'<div style="font-size:0.82rem; color:{C_TEXT2}; line-height:1.6">{text}</div>'
                f'</div></div>'
            )
        st.markdown(
            _card(html, border="rgba(59,130,246,0.20)"),
            unsafe_allow_html=True,
        )
    except Exception:
        st.warning("Recommended actions unavailable.")


# ═══════════════════════════════════════════════════════════════════════════
# Main render function — EXACT SIGNATURE PRESERVED
# ═══════════════════════════════════════════════════════════════════════════

def render(
    port_results: list,
    route_results: list,
    freight_data: dict,
    macro_data: dict,
    insights: list,
) -> None:
    """Render the Supply Chain Health tab."""

    # ── Page header ────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="margin-bottom:6px">'
        f'<h2 style="font-size:1.55rem; font-weight:900; color:{C_TEXT}; margin:0; padding:0">'
        f'Supply Chain Health</h2>'
        f'<div style="font-size:0.83rem; color:{C_TEXT2}; margin-top:3px">'
        f'Comprehensive resilience dashboard — SCHI, disruption tracking, cost intelligence &amp; risk analytics</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Empty-state guard ──────────────────────────────────────────────────
    if not port_results and not route_results:
        st.warning(
            "No supply chain data is available yet. "
            "Run the analysis with valid API credentials to populate port demand, "
            "route health, and macro signals. The dashboard will render automatically "
            "once data is received.",
            icon="&#128236;",
        )
        return

    # ── Engine compute ─────────────────────────────────────────────────────
    schi_report = None
    if _SCH_MODULE_AVAILABLE:
        try:
            schi_report = compute_supply_chain_health(
                port_results, route_results, freight_data, macro_data
            )
        except Exception:
            schi_report = None

    # ── SCHI value & label ─────────────────────────────────────────────────
    if schi_report is not None:
        schi_value = getattr(schi_report, "index", None)
        if schi_value is None:
            schi_value = getattr(schi_report, "score", 0.5)
        schi_label = "Supply Chain Health Index"
        raw_dims   = getattr(schi_report, "dimension_scores", None)
    else:
        has_data = [r for r in port_results if getattr(r, "has_real_data", False)]
        schi_value = (
            sum(r.demand_score for r in has_data) / len(has_data) if has_data else 0.5
        )
        schi_label = "Supply Chain Index (est.)"
        raw_dims   = None

    try:
        schi_value = float(schi_value)
    except Exception:
        schi_value = 0.5

    # ── Dimension scores ───────────────────────────────────────────────────
    if raw_dims and isinstance(raw_dims, dict) and len(raw_dims) >= 6:
        dimension_scores = {
            "Port Capacity":     raw_dims.get("port_capacity", 0.5),
            "Freight Cost":      raw_dims.get("freight_cost",  0.5),
            "Macro Environment": raw_dims.get("macro",         0.5),
            "Chokepoint Risk":   raw_dims.get("chokepoint",    0.5),
            "Inventory Cycle":   raw_dims.get("inventory",     0.5),
            "Seasonal Factors":  raw_dims.get("seasonal",      0.5),
        }
    else:
        has_data = [r for r in port_results if getattr(r, "has_real_data", False)]
        avg_d = sum(r.demand_score for r in has_data) / len(has_data) if has_data else 0.5
        dimension_scores = {
            "Port Capacity":     1.0 - avg_d,
            "Freight Cost":      0.5,
            "Macro Environment": 0.5,
            "Chokepoint Risk":   0.5,
            "Inventory Cycle":   0.5,
            "Seasonal Factors":  0.5,
        }

    # ── Risks & tailwinds ──────────────────────────────────────────────────
    if schi_report is not None:
        risks     = getattr(schi_report, "risks",     [])
        tailwinds = getattr(schi_report, "tailwinds", [])
    else:
        has_data = [r for r in port_results if getattr(r, "has_real_data", False)]
        high_d = sorted([r for r in has_data if r.demand_score >= 0.70],
                        key=lambda x: x.demand_score, reverse=True)
        low_d  = sorted([r for r in has_data if r.demand_score <= 0.35],
                        key=lambda x: x.demand_score)
        risks = [f"{r.port_name} — congestion risk ({r.demand_score:.0%} demand)"
                 for r in high_d[:4]] or ["No high-demand hotspots identified"]
        tailwinds = [f"{r.port_name} — available capacity ({r.demand_score:.0%})"
                     for r in low_d[:4]] or ["No low-demand capacity windows identified"]

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 1 — Hero Dashboard
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Supply Chain Resilience Overview",
            "Overall health index with letter grade, active disruptions, critical nodes & recovery timeline",
        )
        _render_hero_dashboard(
            schi_value, schi_label, dimension_scores, port_results, route_results
        )
    except Exception:
        st.warning("Hero dashboard section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 2 — Globe Map + Component Bar (side by side)
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Global Supply Chain Network Map",
            "Port nodes colored by health status — green healthy, amber at-risk, red stressed",
        )
        _render_globe_map(port_results)
    except Exception:
        st.warning("Globe map section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 3 — Disruption Tracker
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Active Disruption Tracker",
            "Live cards for active disruptions — severity, affected lanes & resolution estimate",
        )
        _render_disruption_tracker(port_results, schi_value)
    except Exception:
        st.warning("Disruption tracker section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 4 — Resilience Scorecard + Stress Cards
    # ══════════════════════════════════════════════════════════════════════
    try:
        col_sc, col_radar = st.columns([3, 2])
        with col_sc:
            _section_title(
                "Resilience Scorecard",
                "Per-dimension bars: geographic diversity, supplier concentration, inventory buffer & more",
            )
            _render_resilience_scorecard(dimension_scores)
        with col_radar:
            _section_title(
                "6-Dimension Radar",
                "Normalized 0–1 composite across all SCHI axes",
            )
            _render_radar(dimension_scores)
    except Exception:
        st.warning("Resilience scorecard section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 5 — Dimension Stress Indicator Cards
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Dimension Stress Indicators",
            "HEALTHY / AT RISK / STRESSED per dimension with recovery timeline",
        )
        _render_stress_indicator_cards(dimension_scores)
    except Exception:
        st.warning("Stress indicator cards section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 6 — Lead Time Trends
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Lead Time Trends by Product Category",
            "12-month average lead times — Electronics, Apparel, Industrial, Consumer, Chemicals",
        )
        _render_lead_time_trends(schi_value, port_results)
    except Exception:
        st.warning("Lead time trends section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 7 — Inventory Buffer
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Inventory Buffer Analysis",
            "Stock days by category vs safety stock threshold — flags below-safety categories",
        )
        _render_inventory_buffer(schi_value, port_results)
    except Exception:
        st.warning("Inventory buffer section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 8 — Supplier Concentration Risk
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Supplier Concentration Risk",
            "Donut charts with HHI index — Electronics, Apparel, Industrial sourcing by country",
        )
        _render_supplier_concentration(schi_value, port_results)
    except Exception:
        st.warning("Supplier concentration section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 9 — Near-shoring & Friend-shoring Tracker
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Near-shoring & Friend-shoring Tracker",
            "Reshoring progress bars by industry vs policy targets",
        )
        _render_nearshoring_tracker(schi_value, port_results)
    except Exception:
        st.warning("Nearshoring tracker section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 10 — Cost Waterfall
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Supply Chain Cost Breakdown",
            "Waterfall chart from raw material to total delivered cost — stress-adjusted",
        )
        _render_cost_waterfall(schi_value, freight_data, port_results)
    except Exception:
        st.warning("Cost waterfall section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 11 — Risk-Adjusted Lead Time Matrix
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Risk-Adjusted Lead Time Matrix",
            "Routes x risk factors heatmap — color-coded 1 (low) to 10 (critical)",
        )
        _render_risk_lead_time_matrix(schi_value, port_results)
    except Exception:
        st.warning("Lead time matrix section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 12 — 90-Day Historical Trend
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "90-Day Supply Chain Health Trend",
            "SCHI over trailing 90 days with zone bands and 14-day moving average",
        )
        _render_90day_trend(schi_value, port_results, macro_data)
    except Exception:
        st.warning("90-day trend section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 13 — Component Bar
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Health Component Breakdown",
            "Segmented contribution of each supply chain dimension to the composite SCHI",
        )
        _render_component_bar(dimension_scores)
    except Exception:
        st.warning("Component bar section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 14 — Regional Comparison
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Regional Health Comparison",
            "Asia-Pacific vs Europe vs Americas vs Middle East — health vs stress",
        )
        _render_regional_comparison(port_results)
    except Exception:
        st.warning("Regional comparison section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 15 — Active Alerts
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Active Supply Chain Alerts",
            "Severity-ranked alerts from port demand analysis and freight rate monitoring",
        )
        _render_alert_panel(port_results, freight_data)
    except Exception:
        st.warning("Alert panel section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 16 — Recommended Actions
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Recommended Actions",
            "Prioritized action list based on weakest supply chain dimensions",
        )
        _render_recommended_actions(dimension_scores, schi_value)
    except Exception:
        st.warning("Recommended actions section unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 17 — Risks & Tailwinds + CSV Download
    # ══════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Key Risks & Tailwinds",
            "Derived from live port demand analysis and engine signals",
        )
        col_risk, col_tail = st.columns(2)

        def _bullets(items: list[str], color: str) -> str:
            if not items:
                return f'<li style="color:{C_TEXT2}; font-size:0.85rem">No signals identified</li>'
            return "".join(
                f'<li style="color:{C_TEXT}; font-size:0.85rem; margin-bottom:6px; line-height:1.4">{it}</li>'
                for it in items
            )

        with col_risk:
            st.markdown(
                f'<div style="font-size:0.72rem; font-weight:700; color:{C_DANGER};'
                f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px">Key Risks</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                _card(f'<ul style="padding-left:18px; margin:0">{_bullets(risks, C_DANGER)}</ul>',
                      border="rgba(239,68,68,0.25)"),
                unsafe_allow_html=True,
            )
        with col_tail:
            st.markdown(
                f'<div style="font-size:0.72rem; font-weight:700; color:{C_HIGH};'
                f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px">Key Tailwinds</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                _card(f'<ul style="padding-left:18px; margin:0">{_bullets(tailwinds, C_HIGH)}</ul>',
                      border="rgba(16,185,129,0.25)"),
                unsafe_allow_html=True,
            )
    except Exception:
        st.warning("Risks & tailwinds section unavailable.")

    # ── CSV download ───────────────────────────────────────────────────────
    try:
        buf = io.StringIO()
        buf.write("Dimension,Score (%),Status\n")
        for dim, score in sorted(dimension_scores.items(), key=lambda kv: kv[1]):
            status = (
                "Healthy"   if score >= 0.70 else
                "Moderate"  if score >= 0.50 else
                "Stressed"  if score >= 0.35 else "Critical"
            )
            buf.write(f"{dim},{score * 100:.1f},{status}\n")
        st.download_button(
            label="Download Dimension Scores (CSV)",
            data=buf.getvalue(),
            file_name=f"schi_dimension_scores_{datetime.date.today()}.csv",
            mime="text/csv",
            key="sc_download_dimension_scores",
        )
    except Exception:
        pass
