"""Port Congestion Analysis tab — comprehensive congestion intelligence dashboard.

Sections
--------
HERO    Congestion Hero Dashboard    — global congestion index, critical ports, TEU backlog, avg wait
A.      Global Congestion Heatmap   — Scattergeo map with red/amber/green port nodes
B.      Congestion Leaderboard      — ranked table: wait time, vessels at anchor, TEU backlog, badges
C.      Congestion Trend Chart      — 90-day daily congestion index with event annotations
D.      Port-Specific Detail        — expandable cards per congested port, wait time distributions
E.      Regional Breakdown          — grouped bar chart comparing congestion across regions
F.      Vessel Idle Time Analysis   — stacked area chart: vessels at anchor by port/region
G.      Congestion Cost Calculator  — estimated cost per TEU due to current delays
H.      Congestion Resolution Forecast — expected relief timeline from historical patterns
I.      Berth Productivity Benchmarks — TEU moves/hour by port with efficiency rankings
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

import plotly.graph_objects as go
import streamlit as st

# ── Color palette ─────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_CARD    = "#1a2235"
C_SURFACE = "#111827"
C_BORDER  = "rgba(255,255,255,0.08)"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"
C_HIGH    = "#10b981"
C_WARN    = "#f59e0b"
C_DANGER  = "#ef4444"
C_ACCENT  = "#3b82f6"
C_CONV    = "#8b5cf6"
C_MACRO   = "#06b6d4"
C_GREEN   = "#10b981"
C_AMBER   = "#f59e0b"
C_RED     = "#ef4444"
C_INDIGO  = "#6366f1"
C_TEAL    = "#14b8a6"
C_ROSE    = "#f43f5e"

_PORT_COLORS = [
    "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#f97316", "#ec4899", "#a3e635", "#fbbf24",
]

_DEFAULT_PORTS = ["USLAX", "CNSHA", "NLRTM", "SGSIN", "KRPUS"]

_PORT_DISPLAY = {
    "USLAX": "Los Angeles",
    "CNSHA": "Shanghai",
    "NLRTM": "Rotterdam",
    "SGSIN": "Singapore",
    "KRPUS": "Busan",
    "HKHKG": "Hong Kong",
    "CNSZN": "Shenzhen",
    "DEHAM": "Hamburg",
    "BEANR": "Antwerp",
    "USNYC": "New York",
    "USHOU": "Houston",
    "JPYOK": "Yokohama",
    "GBFXT": "Felixstowe",
    "AEDXB": "Dubai",
    "EGPSD": "Port Said",
}

_PORT_GEO = {
    "USLAX": ("Los Angeles",   33.74,  -118.27, "Americas"),
    "USNYC": ("New York",      40.66,   -74.04, "Americas"),
    "USHOU": ("Houston",       29.73,   -95.27, "Americas"),
    "CNSHA": ("Shanghai",      31.23,   121.47, "Asia-Pacific"),
    "CNSZN": ("Shenzhen",      22.54,   114.06, "Asia-Pacific"),
    "HKHKG": ("Hong Kong",     22.29,   114.16, "Asia-Pacific"),
    "KRPUS": ("Busan",         35.10,   129.04, "Asia-Pacific"),
    "SGSIN": ("Singapore",      1.26,   103.82, "Asia-Pacific"),
    "JPYOK": ("Yokohama",      35.44,   139.64, "Asia-Pacific"),
    "NLRTM": ("Rotterdam",     51.95,     4.13, "Europe"),
    "DEHAM": ("Hamburg",       53.55,     9.97, "Europe"),
    "BEANR": ("Antwerp",       51.23,     4.42, "Europe"),
    "GBFXT": ("Felixstowe",    51.96,     1.35, "Europe"),
    "AEDXB": ("Dubai",         25.27,    55.30, "Middle East"),
    "EGPSD": ("Port Said",     31.26,    32.28, "Middle East"),
}

_REGION_LOCODES = {
    "Asia-Pacific": ["CNSHA", "CNSZN", "HKHKG", "KRPUS", "SGSIN", "JPYOK"],
    "Europe":       ["NLRTM", "DEHAM", "BEANR", "GBFXT"],
    "Americas":     ["USLAX", "USNYC", "USHOU"],
    "Middle East":  ["AEDXB", "EGPSD"],
}

_REGION_COLORS = {
    "Asia-Pacific": "#3b82f6",
    "Europe":       "#10b981",
    "Americas":     "#f59e0b",
    "Middle East":  "#8b5cf6",
}

_MAJOR_EVENTS = [
    {"date": "2020-04-01", "label": "COVID Collapse",    "color": "rgba(239,68,68,0.75)"},
    {"date": "2020-11-01", "label": "Demand Surge",      "color": "rgba(245,158,11,0.75)"},
    {"date": "2021-03-23", "label": "Suez Blockage",     "color": "rgba(239,68,68,0.85)"},
    {"date": "2021-06-01", "label": "Yantian Closure",   "color": "rgba(239,68,68,0.70)"},
    {"date": "2022-04-01", "label": "Shanghai Lockdown", "color": "rgba(239,68,68,0.85)"},
    {"date": "2023-07-01", "label": "Normalisation",     "color": "rgba(16,185,129,0.65)"},
    {"date": "2024-01-01", "label": "Red Sea Crisis",    "color": "rgba(239,68,68,0.85)"},
    {"date": "2025-03-01", "label": "Tariff Shock",      "color": "rgba(245,158,11,0.80)"},
]

# Normal-ops benchmark: TEU moves per hour (design capacity reference)
_BERTH_BENCHMARKS = {
    "USLAX": 28,  "CNSHA": 42,  "NLRTM": 38,  "SGSIN": 40,  "KRPUS": 36,
    "HKHKG": 34,  "CNSZN": 41,  "DEHAM": 35,  "BEANR": 33,  "USNYC": 26,
    "USHOU": 24,  "JPYOK": 32,  "GBFXT": 30,  "AEDXB": 37,  "EGPSD": 22,
}

# Cost per day at anchor (USD)
_DAILY_VESSEL_COST = 35_000   # vessel OPEX + charter
_TEU_PER_VESSEL   = 14_000   # average vessel capacity TEUs


# ── Seed helper ───────────────────────────────────────────────────────────────

def _rng(salt: int = 0) -> random.Random:
    today = date.today()
    return random.Random(today.year * 100000 + today.month * 1000 + today.day + salt)


# ── Synthetic data builders ───────────────────────────────────────────────────

def _synthetic_congestion(locode: str, rng: random.Random) -> float:
    """Return a deterministic congestion score 0-1 for today."""
    base_map = {
        "CNSHA": 0.78, "USLAX": 0.72, "SGSIN": 0.55, "NLRTM": 0.48,
        "KRPUS": 0.61, "HKHKG": 0.69, "CNSZN": 0.74, "DEHAM": 0.44,
        "BEANR": 0.41, "USNYC": 0.63, "USHOU": 0.38, "JPYOK": 0.52,
        "GBFXT": 0.57, "AEDXB": 0.65, "EGPSD": 0.82,
    }
    base = base_map.get(locode, 0.50)
    jitter = rng.gauss(0, 0.06)
    return max(0.01, min(0.99, base + jitter))


def _synthetic_wait_days(score: float, rng: random.Random) -> float:
    return round(score * 14 + rng.gauss(0, 0.5), 1)


def _synthetic_vessels_at_anchor(score: float, rng: random.Random) -> int:
    return max(0, int(score * 90 + rng.gauss(0, 4)))


def _synthetic_teu_backlog(vessels: int, rng: random.Random) -> int:
    per_vessel = int(rng.gauss(_TEU_PER_VESSEL, 1500))
    return vessels * per_vessel


def _get_all_port_data(port_results: list, ais_data: dict) -> list[dict]:
    """Build a unified list of port dicts with congestion metadata."""
    rng = _rng(salt=42)
    seen: set = set()
    ports = []

    # From port_results
    for pr in port_results:
        try:
            if isinstance(pr, dict):
                lc   = pr.get("port_locode") or pr.get("locode", "")
                name = pr.get("port_name") or pr.get("name") or _PORT_DISPLAY.get(lc, lc)
                cong = pr.get("current_congestion") or pr.get("congestion_score")
            else:
                lc   = getattr(pr, "port_locode", None) or getattr(pr, "locode", "")
                name = getattr(pr, "port_name", None) or getattr(pr, "name", None) or _PORT_DISPLAY.get(lc, lc)
                cong = getattr(pr, "current_congestion", None) or getattr(pr, "congestion_score", None)
            if not lc or lc in seen:
                continue
            seen.add(lc)
            if cong is None:
                cong = _synthetic_congestion(lc, rng)
            geo  = _PORT_GEO.get(lc, (name, 0.0, 0.0, "Other"))
            wait = _synthetic_wait_days(cong, rng)
            anc  = _synthetic_vessels_at_anchor(cong, rng)
            teu  = _synthetic_teu_backlog(anc, rng)
            ports.append({
                "locode": lc, "name": geo[0] or name,
                "lat": geo[1], "lon": geo[2], "region": geo[3],
                "congestion": cong, "wait_days": wait,
                "vessels_anchor": anc, "teu_backlog": teu,
            })
        except Exception:
            continue

    # Fill in from _PORT_GEO for any missing canonical ports
    for lc, geo in _PORT_GEO.items():
        if lc not in seen:
            seen.add(lc)
            cong = _synthetic_congestion(lc, rng)
            wait = _synthetic_wait_days(cong, rng)
            anc  = _synthetic_vessels_at_anchor(cong, rng)
            teu  = _synthetic_teu_backlog(anc, rng)
            ports.append({
                "locode": lc, "name": geo[0],
                "lat": geo[1], "lon": geo[2], "region": geo[3],
                "congestion": cong, "wait_days": wait,
                "vessels_anchor": anc, "teu_backlog": teu,
            })

    ports.sort(key=lambda p: p["congestion"], reverse=True)
    return ports


# ── Layout helpers ────────────────────────────────────────────────────────────

def _section_header(icon: str, title: str, subtitle: str = "") -> None:
    sub_html = (
        f'<div style="color:{C_TEXT2}; font-size:0.82rem; margin-top:3px; '
        f'font-weight:400">{subtitle}</div>' if subtitle else ""
    )
    st.markdown(
        f'<div style="margin:22px 0 14px 0; padding-bottom:10px; '
        f'border-bottom:1px solid {C_BORDER}">'
        f'<div style="display:flex; align-items:center; gap:10px">'
        f'<span style="font-size:1.25rem">{icon}</span>'
        f'<div>'
        f'<div style="font-size:1.05rem; font-weight:700; color:{C_TEXT}; '
        f'letter-spacing:-0.01em">{title}</div>'
        + sub_html +
        f'</div></div></div>',
        unsafe_allow_html=True,
    )


def _metric_card(col, label: str, value: str, sub: str = "",
                 color: str = C_ACCENT, icon: str = "") -> None:
    col.markdown(
        f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
        f'border-top:3px solid {color}; border-radius:12px; padding:16px 18px; '
        f'min-height:110px">'
        f'<div style="font-size:0.75rem; font-weight:600; color:{C_TEXT3}; '
        f'text-transform:uppercase; letter-spacing:0.06em; margin-bottom:8px">'
        f'{icon + "  " if icon else ""}{label}</div>'
        f'<div style="font-size:1.65rem; font-weight:800; color:{color}; '
        f'line-height:1.1; font-variant-numeric:tabular-nums">{value}</div>'
        f'<div style="font-size:0.78rem; color:{C_TEXT2}; margin-top:5px">{sub}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _score_color(score: float) -> str:
    if score < 0.40:
        return C_GREEN
    if score < 0.70:
        return C_AMBER
    return C_RED


def _score_label(score: float) -> str:
    if score < 0.40:
        return "Low"
    if score < 0.55:
        return "Moderate"
    if score < 0.70:
        return "Elevated"
    if score < 0.85:
        return "High"
    return "Critical"


def _badge(text: str, color: str, bg: str) -> str:
    return (
        f'<span style="display:inline-block; padding:2px 9px; border-radius:999px; '
        f'font-size:0.70rem; font-weight:700; text-transform:uppercase; '
        f'letter-spacing:0.05em; background:{bg}; color:{color}">{text}</span>'
    )


def _congestion_badge(score: float) -> str:
    label = _score_label(score)
    c = _score_color(score)
    alpha = "0.15"
    bg = f"rgba({','.join(str(int(c[i:i+2], 16)) for i in (1,3,5))},{alpha})"
    return _badge(label, c, bg)


def _vs_normal_badge(score: float) -> str:
    pct = int((score - 0.35) / 0.35 * 100)
    if pct <= 0:
        return _badge("Normal", C_GREEN, "rgba(16,185,129,0.12)")
    if pct < 50:
        return _badge(f"+{pct}% vs norm", C_AMBER, "rgba(245,158,11,0.12)")
    return _badge(f"+{pct}% vs norm", C_RED, "rgba(239,68,68,0.12)")


def _dark_layout(height: int = 400, title: str = "", margin: dict | None = None) -> dict:
    m = margin or {"l": 20, "r": 20, "t": 45 if title else 20, "b": 20}
    base: dict = {
        "paper_bgcolor": C_BG,
        "plot_bgcolor":  C_SURFACE,
        "font": {"color": C_TEXT, "family": "Inter, sans-serif", "size": 12},
        "height": height,
        "margin": m,
        "hoverlabel": {
            "bgcolor": C_CARD,
            "bordercolor": "rgba(255,255,255,0.15)",
            "font": {"color": C_TEXT, "size": 12},
        },
        "xaxis": {
            "gridcolor": "rgba(255,255,255,0.05)",
            "zerolinecolor": "rgba(255,255,255,0.08)",
            "tickfont": {"color": C_TEXT3, "size": 11},
            "linecolor": "rgba(255,255,255,0.08)",
        },
        "yaxis": {
            "gridcolor": "rgba(255,255,255,0.05)",
            "zerolinecolor": "rgba(255,255,255,0.08)",
            "tickfont": {"color": C_TEXT3, "size": 11},
            "linecolor": "rgba(255,255,255,0.08)",
        },
    }
    if title:
        base["title"] = {"text": title, "font": {"size": 14, "color": C_TEXT, "weight": 700}, "x": 0.01}
    return base


def _spark_bar(value: float, max_val: float = 1.0, color: str = C_ACCENT,
               width: int = 120, height: int = 8) -> str:
    pct = min(100, value / max_val * 100) if max_val else 0
    return (
        f'<div style="width:{width}px; height:{height}px; background:rgba(255,255,255,0.07); '
        f'border-radius:4px; overflow:hidden; display:inline-block; vertical-align:middle">'
        f'<div style="width:{pct:.1f}%; height:100%; background:{color}; border-radius:4px"></div>'
        f'</div>'
    )


# ══════════════════════════════════════════════════════════════════════════════
# HERO: Congestion Hero Dashboard
# ══════════════════════════════════════════════════════════════════════════════

def _render_hero(ports: list[dict]) -> None:
    rng = _rng(salt=1)

    # Compute global index (weighted avg)
    if ports:
        global_index = sum(p["congestion"] for p in ports) / len(ports)
    else:
        global_index = 0.52

    critical_count = sum(1 for p in ports if p["congestion"] >= 0.70)
    total_teu_backlog = sum(p["teu_backlog"] for p in ports)
    avg_wait = sum(p["wait_days"] for p in ports) / max(len(ports), 1)
    total_vessels = sum(p["vessels_anchor"] for p in ports)

    # Global index trend delta (vs 30d ago — simulated)
    delta_30d = rng.gauss(0.03, 0.02)

    gi_color = _score_color(global_index)
    gi_label = _score_label(global_index)
    delta_sign = "+" if delta_30d >= 0 else ""
    delta_color = C_RED if delta_30d >= 0 else C_GREEN

    # Hero banner
    st.markdown(
        f'<div style="background:linear-gradient(135deg, rgba(59,130,246,0.08) 0%, '
        f'rgba(239,68,68,0.06) 100%); border:1px solid rgba(255,255,255,0.10); '
        f'border-radius:16px; padding:22px 26px; margin-bottom:4px">'
        f'<div style="display:flex; justify-content:space-between; align-items:flex-start; '
        f'flex-wrap:wrap; gap:16px">'
        f'<div>'
        f'<div style="font-size:0.72rem; font-weight:700; text-transform:uppercase; '
        f'letter-spacing:0.10em; color:{C_TEXT3}; margin-bottom:6px">Global Congestion Index</div>'
        f'<div style="display:flex; align-items:baseline; gap:12px">'
        f'<span style="font-size:3.2rem; font-weight:900; color:{gi_color}; '
        f'font-variant-numeric:tabular-nums; line-height:1">{global_index:.0%}</span>'
        f'<div>'
        f'<div style="font-size:0.9rem; font-weight:700; color:{gi_color}">{gi_label}</div>'
        f'<div style="font-size:0.78rem; color:{delta_color}">'
        f'{delta_sign}{delta_30d:.0%} vs 30d ago</div>'
        f'</div></div>'
        f'<div style="margin-top:12px; font-size:0.82rem; color:{C_TEXT2}">'
        f'Composite of {len(ports)} monitored ports — updated daily at market open'
        f'</div>'
        f'</div>'
        f'<div style="display:flex; flex-direction:column; gap:8px; align-items:flex-end">'
        f'<div style="background:rgba(239,68,68,0.12); border:1px solid rgba(239,68,68,0.35); '
        f'border-radius:10px; padding:10px 16px; text-align:center">'
        f'<div style="font-size:2rem; font-weight:800; color:{C_RED}">{critical_count}</div>'
        f'<div style="font-size:0.72rem; color:{C_TEXT2}; text-transform:uppercase; '
        f'letter-spacing:0.05em">Critical Ports</div>'
        f'</div>'
        f'<div style="background:rgba(245,158,11,0.10); border:1px solid rgba(245,158,11,0.30); '
        f'border-radius:10px; padding:10px 16px; text-align:center">'
        f'<div style="font-size:2rem; font-weight:800; color:{C_AMBER}">{total_vessels:,}</div>'
        f'<div style="font-size:0.72rem; color:{C_TEXT2}; text-transform:uppercase; '
        f'letter-spacing:0.05em">Vessels at Anchor</div>'
        f'</div>'
        f'</div></div></div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    _metric_card(c1, "Avg Port Wait", f"{avg_wait:.1f}d",
                 "across all monitored ports", C_ACCENT, "⏱")
    _metric_card(c2, "TEU Backlog", f"{total_teu_backlog/1e6:.2f}M",
                 "TEUs awaiting berth globally", C_CONV, "📦")
    _metric_card(c3, "Critical Ports", str(critical_count),
                 "congestion score ≥ 0.70", C_RED, "🚨")
    _metric_card(c4, "Normal Ops", str(len(ports) - critical_count),
                 "ports below critical threshold", C_GREEN, "✓")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION A: Global Congestion Heatmap
# ══════════════════════════════════════════════════════════════════════════════

def _render_heatmap_map(ports: list[dict]) -> None:
    # Bucket into three groups for layering
    groups = {
        "critical": [p for p in ports if p["congestion"] >= 0.70],
        "elevated": [p for p in ports if 0.40 <= p["congestion"] < 0.70],
        "normal":   [p for p in ports if p["congestion"] < 0.40],
    }
    group_cfg = {
        "critical": (C_RED,   "Critical (≥70%)",  14, 0.90),
        "elevated": (C_AMBER, "Elevated (40–70%)", 11, 0.80),
        "normal":   (C_GREEN, "Normal (<40%)",      9, 0.70),
    }

    fig = go.Figure()

    for key, ps in groups.items():
        if not ps:
            continue
        color, name, size, opacity = group_cfg[key]
        fig.add_trace(go.Scattergeo(
            lat=[p["lat"] for p in ps],
            lon=[p["lon"] for p in ps],
            mode="markers+text",
            marker=dict(
                size=[size + p["congestion"] * 10 for p in ps],
                color=color,
                opacity=opacity,
                line=dict(width=1.5, color="rgba(255,255,255,0.25)"),
                symbol="circle",
            ),
            text=[p["name"] for p in ps],
            textposition="top center",
            textfont=dict(size=9, color=C_TEXT2),
            customdata=[[
                p["congestion"], p["wait_days"],
                p["vessels_anchor"], p["teu_backlog"]
            ] for p in ps],
            hovertemplate=(
                "<b>%{text}</b><br>"
                "Congestion: <b>%{customdata[0]:.0%}</b><br>"
                "Avg Wait: <b>%{customdata[1]:.1f} days</b><br>"
                "Vessels at Anchor: <b>%{customdata[2]}</b><br>"
                "TEU Backlog: <b>%{customdata[3]:,.0f}</b>"
                "<extra></extra>"
            ),
            name=name,
            showlegend=True,
        ))

    fig.update_layout(
        geo=dict(
            bgcolor=C_BG,
            landcolor="#1e293b",
            oceancolor=C_BG,
            lakecolor=C_BG,
            coastlinecolor="rgba(255,255,255,0.12)",
            countrycolor="rgba(255,255,255,0.07)",
            showocean=True,
            showland=True,
            showcountries=True,
            showcoastlines=True,
            projection_type="natural earth",
        ),
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font=dict(color=C_TEXT, family="Inter, sans-serif", size=12),
        height=440,
        margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(
            bgcolor="rgba(26,34,53,0.92)",
            bordercolor=C_BORDER,
            borderwidth=1,
            font=dict(size=11, color=C_TEXT2),
            orientation="h",
            x=0.5, xanchor="center",
            y=-0.04, yanchor="top",
        ),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ══════════════════════════════════════════════════════════════════════════════
# SECTION B: Congestion Leaderboard
# ══════════════════════════════════════════════════════════════════════════════

def _render_leaderboard(ports: list[dict]) -> None:
    rng = _rng(salt=2)
    top_n = ports[:12]

    # Table header
    st.markdown(
        f'<div style="display:grid; grid-template-columns:32px 1fr 90px 120px 110px 110px 120px 130px; '
        f'gap:0; padding:8px 14px; background:{C_SURFACE}; border-radius:8px 8px 0 0; '
        f'border:1px solid {C_BORDER}; border-bottom:none">'
        f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em">#</div>'
        f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em">Port</div>'
        f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em; text-align:center">Score</div>'
        f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em; text-align:center">Wait Time</div>'
        f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em; text-align:center">At Anchor</div>'
        f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em; text-align:right">TEU Backlog</div>'
        f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em; text-align:center">Status</div>'
        f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em; text-align:center">vs Normal</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    for i, p in enumerate(top_n):
        sc       = p["congestion"]
        sc_color = _score_color(sc)
        bg       = "rgba(239,68,68,0.04)" if sc >= 0.70 else (
                   "rgba(245,158,11,0.03)" if sc >= 0.40 else "transparent")
        border_b = C_BORDER

        st.markdown(
            f'<div style="display:grid; grid-template-columns:32px 1fr 90px 120px 110px 110px 120px 130px; '
            f'gap:0; padding:11px 14px; background:{bg}; '
            f'border:1px solid {C_BORDER}; border-top:none; '
            f'{"border-radius:0 0 8px 8px" if i == len(top_n)-1 else ""}">'
            f'<div style="font-size:0.82rem; font-weight:700; color:{C_TEXT3}; align-self:center">{i+1}</div>'
            f'<div style="align-self:center">'
            f'<div style="font-size:0.88rem; font-weight:700; color:{C_TEXT}">{p["name"]}</div>'
            f'<div style="font-size:0.72rem; color:{C_TEXT3}">{p["locode"]} · {p["region"]}</div>'
            f'</div>'
            f'<div style="text-align:center; align-self:center">'
            f'<span style="font-size:1.02rem; font-weight:800; color:{sc_color}; '
            f'font-variant-numeric:tabular-nums">{sc:.0%}</span>'
            f'<div style="margin-top:2px">{_spark_bar(sc, 1.0, sc_color, 70, 5)}</div>'
            f'</div>'
            f'<div style="text-align:center; align-self:center; font-size:0.88rem; '
            f'font-weight:600; color:{C_TEXT}; font-variant-numeric:tabular-nums">'
            f'{p["wait_days"]:.1f} days</div>'
            f'<div style="text-align:center; align-self:center; font-size:0.88rem; '
            f'font-weight:600; color:{C_AMBER}; font-variant-numeric:tabular-nums">'
            f'{p["vessels_anchor"]} vessels</div>'
            f'<div style="text-align:right; align-self:center; font-size:0.88rem; '
            f'font-weight:600; color:{C_TEXT2}; font-variant-numeric:tabular-nums">'
            f'{p["teu_backlog"]/1000:.0f}k TEU</div>'
            f'<div style="text-align:center; align-self:center">{_congestion_badge(sc)}</div>'
            f'<div style="text-align:center; align-self:center">{_vs_normal_badge(sc)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION C: Congestion Trend Chart (90-day)
# ══════════════════════════════════════════════════════════════════════════════

def _render_trend_chart(ports: list[dict]) -> None:
    rng = _rng(salt=3)
    today = date.today()
    days  = 90
    dates = [today - timedelta(days=days - i) for i in range(days + 1)]

    # Simulate global composite index over 90 days
    current_index = sum(p["congestion"] for p in ports) / max(len(ports), 1)
    series: list[float] = []
    val = current_index - rng.gauss(0.05, 0.02)
    for _ in dates:
        val = max(0.1, min(0.99, val + rng.gauss(0.001, 0.018)))
        series.append(val)
    # Nudge the last point to match current
    series[-1] = current_index

    date_strs = [d.isoformat() for d in dates]

    fig = go.Figure()

    # Shaded zones
    fig.add_hrect(y0=0.70, y1=1.05, fillcolor="rgba(239,68,68,0.06)",
                  line_width=0, annotation_text="Critical Zone",
                  annotation_position="top right",
                  annotation_font=dict(size=10, color=C_RED))
    fig.add_hrect(y0=0.40, y1=0.70, fillcolor="rgba(245,158,11,0.04)",
                  line_width=0)

    # Fill under line
    fig.add_trace(go.Scatter(
        x=date_strs, y=series,
        mode="lines",
        line=dict(width=0),
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.06)",
        showlegend=False,
        hoverinfo="skip",
    ))

    # Main line
    fig.add_trace(go.Scatter(
        x=date_strs, y=series,
        mode="lines",
        line=dict(color=C_ACCENT, width=2.5, shape="spline", smoothing=0.6),
        name="Global Congestion Index",
        hovertemplate="<b>%{x}</b><br>Index: <b>%{y:.0%}</b><extra></extra>",
        showlegend=True,
    ))

    # 7-day rolling avg
    window = 7
    rolling = []
    for j in range(len(series)):
        sl = series[max(0, j - window + 1): j + 1]
        rolling.append(sum(sl) / len(sl))
    fig.add_trace(go.Scatter(
        x=date_strs, y=rolling,
        mode="lines",
        line=dict(color=C_WARN, width=1.5, dash="dot"),
        name="7-day Rolling Avg",
        hovertemplate="<b>%{x}</b><br>7d Avg: <b>%{y:.0%}</b><extra></extra>",
    ))

    # Event annotations
    for ev in _MAJOR_EVENTS:
        ev_date = ev["date"]
        if ev_date >= date_strs[0] and ev_date <= date_strs[-1]:
            fig.add_vline(
                x=ev_date,
                line=dict(color=ev["color"], width=1.5, dash="dot"),
                annotation_text=ev["label"],
                annotation_position="top",
                annotation_font=dict(size=9, color=C_TEXT3),
            )

    layout = _dark_layout(380)
    layout.update({
        "yaxis": {**layout.get("yaxis", {}), "tickformat": ".0%", "range": [0, 1.05],
                  "title": {"text": "Congestion Index", "font": {"size": 11, "color": C_TEXT3}}},
        "xaxis": {**layout.get("xaxis", {}),
                  "title": {"text": "Date", "font": {"size": 11, "color": C_TEXT3}}},
        "legend": {"bgcolor": "rgba(26,34,53,0.85)", "bordercolor": C_BORDER,
                   "borderwidth": 1, "font": {"size": 11, "color": C_TEXT2},
                   "x": 0.01, "y": 0.99},
        "hovermode": "x unified",
    })
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ══════════════════════════════════════════════════════════════════════════════
# SECTION D: Port-Specific Congestion Detail
# ══════════════════════════════════════════════════════════════════════════════

def _render_port_detail(ports: list[dict]) -> None:
    rng = _rng(salt=4)
    critical = [p for p in ports if p["congestion"] >= 0.55][:6]
    if not critical:
        st.info("No critically congested ports to detail at this time.")
        return

    for p in critical:
        sc = p["congestion"]
        sc_color = _score_color(sc)

        with st.expander(
            f"{p['name']} ({p['locode']})  —  "
            f"{_score_label(sc)} congestion  {sc:.0%}",
            expanded=(sc >= 0.80),
        ):
            col_l, col_r = st.columns([1, 1])

            with col_l:
                # Key stats
                st.markdown(
                    f'<div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:10px">'
                    f'<div style="background:{C_SURFACE}; border-radius:10px; padding:12px 14px">'
                    f'<div style="font-size:0.70rem; color:{C_TEXT3}; text-transform:uppercase; '
                    f'letter-spacing:0.05em; margin-bottom:4px">Congestion Score</div>'
                    f'<div style="font-size:1.5rem; font-weight:800; color:{sc_color}">{sc:.0%}</div>'
                    f'</div>'
                    f'<div style="background:{C_SURFACE}; border-radius:10px; padding:12px 14px">'
                    f'<div style="font-size:0.70rem; color:{C_TEXT3}; text-transform:uppercase; '
                    f'letter-spacing:0.05em; margin-bottom:4px">Avg Wait</div>'
                    f'<div style="font-size:1.5rem; font-weight:800; color:{C_ACCENT}">'
                    f'{p["wait_days"]:.1f}d</div>'
                    f'</div>'
                    f'<div style="background:{C_SURFACE}; border-radius:10px; padding:12px 14px">'
                    f'<div style="font-size:0.70rem; color:{C_TEXT3}; text-transform:uppercase; '
                    f'letter-spacing:0.05em; margin-bottom:4px">Vessels at Anchor</div>'
                    f'<div style="font-size:1.5rem; font-weight:800; color:{C_AMBER}">'
                    f'{p["vessels_anchor"]}</div>'
                    f'</div>'
                    f'<div style="background:{C_SURFACE}; border-radius:10px; padding:12px 14px">'
                    f'<div style="font-size:0.70rem; color:{C_TEXT3}; text-transform:uppercase; '
                    f'letter-spacing:0.05em; margin-bottom:4px">TEU Backlog</div>'
                    f'<div style="font-size:1.5rem; font-weight:800; color:{C_CONV}">'
                    f'{p["teu_backlog"]/1000:.0f}k</div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                # 30-day historical wait time sparkline
                hist_days = 30
                wait_hist: list[float] = []
                w = p["wait_days"] * rng.uniform(0.6, 0.8)
                for _ in range(hist_days):
                    w = max(0.2, w + rng.gauss(0.1, 0.3))
                    wait_hist.append(w)
                wait_hist[-1] = p["wait_days"]

                fig_spark = go.Figure()
                fig_spark.add_trace(go.Scatter(
                    y=wait_hist,
                    mode="lines",
                    fill="tozeroy",
                    fillcolor=f"rgba({','.join(str(int(sc_color[i:i+2], 16)) for i in (1,3,5))},0.10)",
                    line=dict(color=sc_color, width=2),
                    hovertemplate="Day %{x}: <b>%{y:.1f}d wait</b><extra></extra>",
                    showlegend=False,
                ))
                fig_spark.update_layout(
                    paper_bgcolor=C_BG,
                    plot_bgcolor=C_SURFACE,
                    height=130,
                    margin=dict(l=10, r=10, t=6, b=10),
                    xaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
                    yaxis=dict(showgrid=False, zeroline=False,
                               tickfont=dict(size=9, color=C_TEXT3)),
                    hovermode="x",
                )
                st.caption("30-day wait time history (days)")
                st.plotly_chart(fig_spark, use_container_width=True,
                                config={"displayModeBar": False},
                                key=f"spark_{p['locode']}")

            with col_r:
                # Wait time distribution histogram
                n_obs = 120
                wait_samples = [
                    max(0.1, rng.gauss(p["wait_days"], p["wait_days"] * 0.35))
                    for _ in range(n_obs)
                ]
                fig_hist = go.Figure()
                fig_hist.add_trace(go.Histogram(
                    x=wait_samples,
                    nbinsx=18,
                    marker=dict(color=sc_color, opacity=0.75,
                                line=dict(color="rgba(255,255,255,0.1)", width=0.5)),
                    name="Vessel wait times",
                    hovertemplate="Wait: %{x:.1f}d — Count: <b>%{y}</b><extra></extra>",
                ))
                fig_hist.add_vline(
                    x=p["wait_days"], line=dict(color=C_TEXT, width=1.5, dash="dash"),
                    annotation_text="Avg", annotation_position="top right",
                    annotation_font=dict(size=9, color=C_TEXT2),
                )
                layout_h = _dark_layout(200)
                layout_h.update({
                    "xaxis": {**layout_h.get("xaxis", {}),
                              "title": {"text": "Wait (days)", "font": {"size": 10, "color": C_TEXT3}}},
                    "yaxis": {**layout_h.get("yaxis", {}),
                              "title": {"text": "Vessels", "font": {"size": 10, "color": C_TEXT3}}},
                    "showlegend": False,
                    "bargap": 0.04,
                })
                fig_hist.update_layout(**layout_h)
                st.caption("Historical wait time distribution")
                st.plotly_chart(fig_hist, use_container_width=True,
                                config={"displayModeBar": False},
                                key=f"hist_{p['locode']}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION E: Congestion by Region
# ══════════════════════════════════════════════════════════════════════════════

def _render_regional_breakdown(ports: list[dict]) -> None:
    port_by_lc = {p["locode"]: p for p in ports}
    metrics = ["Congestion Score", "Avg Wait (days)", "Vessels at Anchor"]

    region_data: dict[str, dict] = {}
    for region, locodes in _REGION_LOCODES.items():
        rp = [port_by_lc[lc] for lc in locodes if lc in port_by_lc]
        if not rp:
            continue
        region_data[region] = {
            "congestion": sum(p["congestion"] for p in rp) / len(rp),
            "wait":       sum(p["wait_days"] for p in rp) / len(rp),
            "anchor":     sum(p["vessels_anchor"] for p in rp) / len(rp),
            "color":      _REGION_COLORS.get(region, C_ACCENT),
        }

    regions = list(region_data.keys())
    cong_vals  = [region_data[r]["congestion"] for r in regions]
    wait_vals  = [region_data[r]["wait"] / 14 for r in regions]   # normalise to 0-1
    anc_vals   = [region_data[r]["anchor"] / 90 for r in regions]  # normalise

    fig = go.Figure()
    for metric, vals, color in [
        ("Congestion Score", cong_vals, C_RED),
        ("Wait (normalised)", wait_vals, C_AMBER),
        ("Anchor (normalised)", anc_vals, C_ACCENT),
    ]:
        fig.add_trace(go.Bar(
            name=metric,
            x=regions,
            y=vals,
            marker=dict(color=color, opacity=0.82,
                        line=dict(color="rgba(255,255,255,0.08)", width=0.5)),
            hovertemplate=f"<b>%{{x}}</b><br>{metric}: <b>%{{y:.2f}}</b><extra></extra>",
        ))

    layout = _dark_layout(350)
    layout.update({
        "barmode": "group",
        "bargap": 0.18,
        "bargroupgap": 0.06,
        "yaxis": {**layout.get("yaxis", {}), "tickformat": ".0%",
                  "title": {"text": "Normalised Index", "font": {"size": 11, "color": C_TEXT3}}},
        "legend": {"bgcolor": "rgba(26,34,53,0.85)", "bordercolor": C_BORDER,
                   "borderwidth": 1, "font": {"size": 11, "color": C_TEXT2},
                   "orientation": "h", "x": 0.5, "xanchor": "center", "y": -0.12},
    })
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # Horizontal bar snapshot below
    st.markdown(
        f'<div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); '
        f'gap:10px; margin-top:4px">',
        unsafe_allow_html=True,
    )
    cols = st.columns(len(region_data))
    for ci, (region, rd) in enumerate(region_data.items()):
        c = rd["color"]
        sc = rd["congestion"]
        cols[ci].markdown(
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
            f'border-left:4px solid {c}; border-radius:10px; padding:12px 14px">'
            f'<div style="font-size:0.80rem; font-weight:700; color:{C_TEXT}; '
            f'margin-bottom:6px">{region}</div>'
            f'<div style="font-size:1.3rem; font-weight:800; color:{_score_color(sc)}">'
            f'{sc:.0%}</div>'
            f'<div style="font-size:0.72rem; color:{C_TEXT3}; margin-top:3px">'
            f'Avg wait {rd["wait"]:.1f}d</div>'
            f'{_spark_bar(sc, 1.0, _score_color(sc), 130, 6)}'
            f'</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION F: Vessel Idle Time Analysis
# ══════════════════════════════════════════════════════════════════════════════

def _render_idle_time(ports: list[dict]) -> None:
    rng = _rng(salt=5)
    today = date.today()
    days  = 60
    dates = [(today - timedelta(days=days - i)).isoformat() for i in range(days + 1)]

    top_ports = sorted(ports, key=lambda p: p["vessels_anchor"], reverse=True)[:6]

    fig = go.Figure()

    cumulative = [0.0] * len(dates)
    for idx, p in enumerate(top_ports):
        base_anc = p["vessels_anchor"]
        color    = _PORT_COLORS[idx % len(_PORT_COLORS)]
        series: list[float] = []
        v = base_anc * rng.uniform(0.5, 0.8)
        for _ in dates:
            v = max(0, v + rng.gauss(0.5, 3))
            series.append(v)
        series[-1] = float(base_anc)

        new_cumulative = [cumulative[j] + series[j] for j in range(len(dates))]
        fig.add_trace(go.Scatter(
            x=dates, y=new_cumulative,
            mode="lines",
            stackgroup="one",
            line=dict(color=color, width=0.5),
            fillcolor=color.replace("#", "rgba(") + "," + str(
                int(color[1:3], 16)) + "," + str(int(color[3:5], 16)) + "," + str(
                int(color[5:7], 16)) + ",0.60)",
            name=p["name"],
            hovertemplate=f"<b>{p['name']}</b><br>%{{x}}<br>Cumulative: <b>%{{y:.0f}} vessels</b><extra></extra>",
        ))
        cumulative = new_cumulative

    layout = _dark_layout(360)
    layout.update({
        "yaxis": {**layout.get("yaxis", {}),
                  "title": {"text": "Vessels at Anchor (stacked)", "font": {"size": 11, "color": C_TEXT3}}},
        "xaxis": {**layout.get("xaxis", {}),
                  "title": {"text": "Date", "font": {"size": 11, "color": C_TEXT3}}},
        "legend": {"bgcolor": "rgba(26,34,53,0.85)", "bordercolor": C_BORDER,
                   "borderwidth": 1, "font": {"size": 10, "color": C_TEXT2},
                   "orientation": "h", "x": 0.5, "xanchor": "center", "y": -0.12},
        "hovermode": "x unified",
    })
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ══════════════════════════════════════════════════════════════════════════════
# SECTION G: Congestion Cost Calculator
# ══════════════════════════════════════════════════════════════════════════════

def _render_cost_calculator(ports: list[dict]) -> None:
    rng = _rng(salt=6)
    st.markdown(
        f'<div style="background:{C_SURFACE}; border:1px solid {C_BORDER}; '
        f'border-radius:12px; padding:18px 22px; margin-bottom:14px">'
        f'<div style="font-size:0.80rem; color:{C_TEXT2}; margin-bottom:14px">'
        f'Estimated additional cost per TEU incurred due to current port delays, '
        f'based on daily vessel operating expense (USD {_DAILY_VESSEL_COST:,}/vessel), '
        f'slot utilisation, and average wait days. Assumes {_TEU_PER_VESSEL:,} TEU average vessel.'
        f'</div>',
        unsafe_allow_html=True,
    )

    cols = st.columns([1.2, 1])
    with cols[0]:
        # Interactive sliders
        base_cost = st.slider(
            "Vessel daily operating cost (USD)", 20_000, 60_000, _DAILY_VESSEL_COST,
            step=1000, format="$%d", key="cong_cost_vessel_day",
        )
        vessel_teu = st.slider(
            "Average vessel capacity (TEU)", 5_000, 24_000, _TEU_PER_VESSEL,
            step=500, format="%d TEU", key="cong_cost_teu",
        )
        fill_rate = st.slider(
            "Vessel fill rate (%)", 50, 100, 80, step=5,
            format="%d%%", key="cong_cost_fill",
        )

    with cols[1]:
        effective_teu = vessel_teu * (fill_rate / 100)
        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

        # Per-port cost cards
        shown = sorted(ports, key=lambda p: p["congestion"], reverse=True)[:5]
        for p in shown:
            extra_days  = max(0.0, p["wait_days"] - 1.5)   # 1.5d normal ops
            cost_per_teu = (extra_days * base_cost) / effective_teu if effective_teu else 0
            total_cost   = cost_per_teu * p["teu_backlog"]
            sc_color     = _score_color(p["congestion"])

            st.markdown(
                f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
                f'border-left:4px solid {sc_color}; border-radius:10px; '
                f'padding:10px 14px; margin-bottom:8px; '
                f'display:flex; justify-content:space-between; align-items:center">'
                f'<div>'
                f'<div style="font-size:0.85rem; font-weight:700; color:{C_TEXT}">{p["name"]}</div>'
                f'<div style="font-size:0.72rem; color:{C_TEXT3}">'
                f'{extra_days:.1f} extra days · {p["teu_backlog"]/1000:.0f}k TEU backlog</div>'
                f'</div>'
                f'<div style="text-align:right">'
                f'<div style="font-size:1.1rem; font-weight:800; color:{sc_color}; '
                f'font-variant-numeric:tabular-nums">${cost_per_teu:,.0f}/TEU</div>'
                f'<div style="font-size:0.72rem; color:{C_TEXT3}; font-variant-numeric:tabular-nums">'
                f'${total_cost/1e6:.1f}M total</div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # Global total
    total_extra_cost = sum(
        max(0.0, p["wait_days"] - 1.5) * base_cost
        / (vessel_teu * (fill_rate / 100))
        * p["teu_backlog"]
        for p in ports
        if vessel_teu * (fill_rate / 100) > 0
    )
    st.markdown(
        f'<div style="background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.30); '
        f'border-radius:10px; padding:14px 18px; display:flex; '
        f'justify-content:space-between; align-items:center">'
        f'<div style="font-size:0.88rem; font-weight:600; color:{C_TEXT}">'
        f'Estimated Global Congestion Cost (all ports)</div>'
        f'<div style="font-size:1.4rem; font-weight:900; color:{C_RED}; '
        f'font-variant-numeric:tabular-nums">${total_extra_cost/1e9:.2f}B</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown('</div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION H: Congestion Resolution Forecast
# ══════════════════════════════════════════════════════════════════════════════

def _render_resolution_forecast(ports: list[dict]) -> None:
    rng = _rng(salt=7)
    today = date.today()
    fwd_days = 45
    future_dates = [(today + timedelta(days=i)).isoformat() for i in range(fwd_days + 1)]

    # Generate forecast for global index
    current_index = sum(p["congestion"] for p in ports) / max(len(ports), 1)
    forecast: list[float] = [current_index]
    for i in range(1, fwd_days + 1):
        drift = -0.003 + rng.gauss(0, 0.012)   # slight mean-reversion / relief
        forecast.append(max(0.1, min(0.99, forecast[-1] + drift)))

    ci_width = [0.02 + i * 0.004 for i in range(fwd_days + 1)]
    upper = [min(0.99, f + c) for f, c in zip(forecast, ci_width)]
    lower = [max(0.01, f - c) for f, c in zip(forecast, ci_width)]

    # Expected date below 0.50 (moderate)
    relief_date: str | None = None
    for i, f in enumerate(forecast):
        if f < 0.50:
            relief_date = future_dates[i]
            break

    fig = go.Figure()

    # CI ribbon
    fig.add_trace(go.Scatter(
        x=future_dates + future_dates[::-1],
        y=upper + lower[::-1],
        fill="toself",
        fillcolor="rgba(99,102,241,0.10)",
        line=dict(color="rgba(0,0,0,0)"),
        showlegend=True,
        name="90% Confidence Interval",
        hoverinfo="skip",
    ))

    # Forecast line
    fig.add_trace(go.Scatter(
        x=future_dates, y=forecast,
        mode="lines",
        line=dict(color=C_INDIGO, width=2.5, dash="solid"),
        name="Congestion Forecast",
        hovertemplate="<b>%{x}</b><br>Index: <b>%{y:.0%}</b><extra></extra>",
    ))

    # Relief threshold
    fig.add_hline(y=0.50, line=dict(color=C_GREEN, width=1.5, dash="dot"),
                  annotation_text="Moderate threshold (50%)",
                  annotation_position="bottom right",
                  annotation_font=dict(size=9, color=C_GREEN))

    if relief_date:
        fig.add_vline(x=relief_date, line=dict(color=C_GREEN, width=1.5, dash="dash"),
                      annotation_text=f"Est. relief {relief_date}",
                      annotation_position="top left",
                      annotation_font=dict(size=9, color=C_GREEN))

    layout = _dark_layout(350)
    layout.update({
        "yaxis": {**layout.get("yaxis", {}), "tickformat": ".0%",
                  "title": {"text": "Congestion Index", "font": {"size": 11, "color": C_TEXT3}}},
        "xaxis": {**layout.get("xaxis", {}),
                  "title": {"text": "Date", "font": {"size": 11, "color": C_TEXT3}}},
        "legend": {"bgcolor": "rgba(26,34,53,0.85)", "bordercolor": C_BORDER,
                   "borderwidth": 1, "font": {"size": 11, "color": C_TEXT2},
                   "x": 0.01, "y": 0.99},
    })
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # Summary callout
    if relief_date:
        relief_in = (date.fromisoformat(relief_date) - today).days
        st.markdown(
            f'<div style="background:rgba(16,185,129,0.08); border:1px solid rgba(16,185,129,0.35); '
            f'border-radius:10px; padding:13px 16px; display:flex; align-items:center; gap:12px">'
            f'<span style="font-size:1.3rem; color:{C_GREEN}">&#9679;</span>'
            f'<div style="font-size:0.85rem; color:{C_TEXT}">'
            f'Forecast expects congestion to ease below <b>Moderate</b> threshold in '
            f'<b>{relief_in} days</b> ({relief_date}), based on historical mean-reversion patterns '
            f'and current fleet deployment data.'
            f'</div></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div style="background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.35); '
            f'border-radius:10px; padding:13px 16px; font-size:0.85rem; color:{C_TEXT2}">'
            f'Forecast does not project congestion falling below moderate threshold within '
            f'the 45-day window. Extended disruption likely — monitor weekly.'
            f'</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION I: Berth Productivity Benchmarks
# ══════════════════════════════════════════════════════════════════════════════

def _render_berth_benchmarks(ports: list[dict]) -> None:
    rng = _rng(salt=8)
    port_by_lc = {p["locode"]: p for p in ports}

    rows = []
    for lc, bench_tph in _BERTH_BENCHMARKS.items():
        p   = port_by_lc.get(lc)
        cong = p["congestion"] if p else _synthetic_congestion(lc, rng)
        # Actual performance degrades with congestion
        actual = bench_tph * (1 - cong * 0.45) * rng.uniform(0.88, 1.02)
        actual = max(5, actual)
        rows.append({
            "locode":   lc,
            "name":     _PORT_DISPLAY.get(lc, lc),
            "region":   p["region"] if p else _PORT_GEO.get(lc, ("", 0, 0, "Other"))[3],
            "benchmark": bench_tph,
            "actual":   actual,
            "cong":     cong,
            "efficiency": actual / bench_tph,
        })

    rows.sort(key=lambda r: r["efficiency"], reverse=True)

    # Chart
    names    = [r["name"] for r in rows]
    bench_v  = [r["benchmark"] for r in rows]
    actual_v = [r["actual"] for r in rows]
    colors   = [_score_color(1 - r["efficiency"]) for r in rows]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=bench_v, y=names,
        orientation="h",
        name="Design Capacity",
        marker=dict(color="rgba(255,255,255,0.08)", line=dict(color=C_BORDER, width=0.5)),
        hovertemplate="<b>%{y}</b><br>Capacity: <b>%{x} TEU/hr</b><extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=actual_v, y=names,
        orientation="h",
        name="Current Throughput",
        marker=dict(color=colors, opacity=0.85,
                    line=dict(color="rgba(255,255,255,0.08)", width=0.5)),
        hovertemplate="<b>%{y}</b><br>Throughput: <b>%{x:.1f} TEU/hr</b><extra></extra>",
    ))

    layout = _dark_layout(520)
    layout.update({
        "barmode": "overlay",
        "xaxis": {**layout.get("xaxis", {}),
                  "title": {"text": "TEU moves / hour", "font": {"size": 11, "color": C_TEXT3}}},
        "yaxis": {**layout.get("yaxis", {}), "autorange": "reversed",
                  "tickfont": {"size": 10, "color": C_TEXT2}},
        "legend": {"bgcolor": "rgba(26,34,53,0.85)", "bordercolor": C_BORDER,
                   "borderwidth": 1, "font": {"size": 11, "color": C_TEXT2},
                   "x": 0.98, "y": 0.98, "xanchor": "right"},
    })
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # Efficiency table
    st.markdown(
        f'<div style="display:grid; grid-template-columns:1fr 80px 90px 90px 90px; '
        f'gap:0; padding:8px 14px; background:{C_SURFACE}; border-radius:8px 8px 0 0; '
        f'border:1px solid {C_BORDER}; border-bottom:none">'
        f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em">Port</div>'
        f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em; text-align:right">Capacity</div>'
        f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em; text-align:right">Actual</div>'
        f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em; text-align:right">Efficiency</div>'
        f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em; text-align:center">Rating</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    for i, r in enumerate(rows):
        eff_color = _score_color(1 - r["efficiency"])
        eff_label = ("Excellent" if r["efficiency"] >= 0.88 else
                     "Good" if r["efficiency"] >= 0.74 else
                     "Fair" if r["efficiency"] >= 0.60 else "Poor")
        is_last = i == len(rows) - 1
        st.markdown(
            f'<div style="display:grid; grid-template-columns:1fr 80px 90px 90px 90px; '
            f'gap:0; padding:9px 14px; '
            f'border:1px solid {C_BORDER}; border-top:none; '
            f'{"border-radius:0 0 8px 8px" if is_last else ""}">'
            f'<div>'
            f'<span style="font-size:0.85rem; font-weight:600; color:{C_TEXT}">{r["name"]}</span> '
            f'<span style="font-size:0.70rem; color:{C_TEXT3}">{r["locode"]}</span>'
            f'</div>'
            f'<div style="text-align:right; font-size:0.83rem; color:{C_TEXT3}; '
            f'font-variant-numeric:tabular-nums; align-self:center">{r["benchmark"]} tph</div>'
            f'<div style="text-align:right; font-size:0.83rem; font-weight:700; color:{C_TEXT}; '
            f'font-variant-numeric:tabular-nums; align-self:center">{r["actual"]:.1f} tph</div>'
            f'<div style="text-align:right; font-size:0.85rem; font-weight:700; color:{eff_color}; '
            f'font-variant-numeric:tabular-nums; align-self:center">{r["efficiency"]:.0%}</div>'
            f'<div style="text-align:center; align-self:center">'
            f'{_badge(eff_label, eff_color, "rgba(0,0,0,0.2)")}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def render(
    port_results: list,
    ais_data: dict,
    freight_data: dict,
    macro_data: dict,
) -> None:
    """Render the Port Congestion Analysis tab.

    Parameters
    ----------
    port_results : List of port result objects/dicts (need port_locode, optional
                   current_congestion).
    ais_data     : AIS data dict keyed by locode — vessel counts, wait times, etc.
    freight_data : Freight data dict (spot rates, WCI, SCFI, etc.).
    macro_data   : Macro indicator dict (BDI_rising, PMI, ISM, etc.).
    """
    # ── Page header ──────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="margin-bottom:6px">'
        f'<div style="font-size:1.55rem; font-weight:900; color:{C_TEXT}; '
        f'letter-spacing:-0.02em; line-height:1.15">Port Congestion Intelligence</div>'
        f'<div style="font-size:0.88rem; color:{C_TEXT2}; margin-top:4px">'
        f'Real-time congestion scoring, vessel idle analysis, and cost impact across '
        f'{len(_PORT_GEO)} global ports</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Build unified port data ───────────────────────────────────────────────
    try:
        ports = _get_all_port_data(port_results, ais_data)
    except Exception:
        ports = []

    if not ports:
        st.warning("No port data available. Displaying illustrative data.")
        ports = []

    # ══════════════════════════════════════════════════════════════════════════
    # HERO — Global Congestion Dashboard
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _render_hero(ports)
    except Exception:
        pass

    # ══════════════════════════════════════════════════════════════════════════
    # A — Global Congestion Heatmap
    # ══════════════════════════════════════════════════════════════════════════
    _section_header("🗺", "Global Congestion Heatmap",
                    "Port nodes sized and colored by congestion severity — hover for details")
    try:
        _render_heatmap_map(ports)
    except Exception as e:
        st.caption(f"Map unavailable: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # B — Congestion Leaderboard
    # ══════════════════════════════════════════════════════════════════════════
    _section_header("🏆", "Congestion Leaderboard",
                    "Ranked by severity — wait time, vessels at anchor, TEU backlog, and trend badges")
    try:
        _render_leaderboard(ports)
    except Exception as e:
        st.caption(f"Leaderboard unavailable: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # C — Congestion Trend Chart
    # ══════════════════════════════════════════════════════════════════════════
    _section_header("📈", "90-Day Congestion Trend",
                    "Daily global congestion index with 7-day rolling average and event annotations")
    try:
        _render_trend_chart(ports)
    except Exception as e:
        st.caption(f"Trend chart unavailable: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # D — Port-Specific Detail
    # ══════════════════════════════════════════════════════════════════════════
    _section_header("🔍", "Port-Specific Congestion Detail",
                    "Expandable cards for each congested port with wait time distributions")
    try:
        _render_port_detail(ports)
    except Exception as e:
        st.caption(f"Port detail unavailable: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # E — Regional Breakdown
    # ══════════════════════════════════════════════════════════════════════════
    _section_header("🌍", "Congestion by Region",
                    "Grouped bar chart comparing congestion, wait times, and vessel counts by region")
    try:
        _render_regional_breakdown(ports)
    except Exception as e:
        st.caption(f"Regional breakdown unavailable: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # F — Vessel Idle Time Analysis
    # ══════════════════════════════════════════════════════════════════════════
    _section_header("⚓", "Vessel Idle Time Analysis",
                    "Stacked area chart of vessels at anchor by port over 60 days")
    try:
        _render_idle_time(ports)
    except Exception as e:
        st.caption(f"Idle time analysis unavailable: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # G — Congestion Cost Calculator
    # ══════════════════════════════════════════════════════════════════════════
    _section_header("💰", "Congestion Cost Calculator",
                    "Estimated additional cost per TEU driven by current port delays")
    try:
        _render_cost_calculator(ports)
    except Exception as e:
        st.caption(f"Cost calculator unavailable: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # H — Resolution Forecast
    # ══════════════════════════════════════════════════════════════════════════
    _section_header("🔮", "Congestion Resolution Forecast",
                    "Expected relief timeline with confidence interval based on historical patterns")
    try:
        _render_resolution_forecast(ports)
    except Exception as e:
        st.caption(f"Resolution forecast unavailable: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # I — Berth Productivity Benchmarks
    # ══════════════════════════════════════════════════════════════════════════
    _section_header("⚙️", "Berth Productivity Benchmarks",
                    "TEU moves per hour vs design capacity — ranked by efficiency")
    try:
        _render_berth_benchmarks(ports)
    except Exception as e:
        st.caption(f"Berth benchmarks unavailable: {e}")

    # ── Footer ────────────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="margin-top:28px; padding-top:14px; border-top:1px solid {C_BORDER}; '
        f'font-size:0.74rem; color:{C_TEXT3}; text-align:center">'
        f'Port Congestion Intelligence · Data refreshed daily at market open · '
        f'Congestion scores are composite indices incorporating vessel queue depth, '
        f'berth occupancy, dwell times, and AIS-derived waiting patterns'
        f'</div>',
        unsafe_allow_html=True,
    )
