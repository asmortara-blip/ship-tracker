"""
tab_eta.py — Vessel ETA Prediction and Voyage Tracking tab.

Sections:
  1. ETA Intelligence Dashboard  — KPI cards
  2. Vessel Voyage Tracker        — main voyage table
  3. ETA Calculator               — interactive form
  4. Delay Analysis               — Plotly bar + histogram
  5. Schedule Reliability Trends  — carrier reliability line chart
  6. Weather Delay Forecast       — 14-day route risk table
  7. Port Queue Tracker           — top-10 port queue table
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

_CHART_LAYOUT = dict(
    paper_bgcolor=C_BG,
    plot_bgcolor=C_SURFACE,
    font=dict(color=C_TEXT, family="Inter, system-ui, sans-serif"),
    margin=dict(t=48, b=36, l=56, r=24),
    xaxis=dict(gridcolor="rgba(255,255,255,0.05)", zerolinecolor="rgba(255,255,255,0.08)"),
    yaxis=dict(gridcolor="rgba(255,255,255,0.05)", zerolinecolor="rgba(255,255,255,0.08)"),
)

# ---------------------------------------------------------------------------
# Static data helpers
# ---------------------------------------------------------------------------
_VESSELS = [
    ("MSC Adriatic",     "9784512", "Shanghai",     "Rotterdam",      -18, 22.1,  "51.9°N 4.5°E"),
    ("Ever Forward",     "9612034", "Busan",         "Los Angeles",    -12, 20.5,  "33.5°N 119.1°W"),
    ("CMA CGM Atlas",    "9503421", "Singapore",     "New York",        -8, 18.7,  "39.2°N 73.4°W"),
    ("Maersk Elba",      "9345678", "Ningbo",        "Hamburg",        -22, 21.3,  "53.5°N 9.9°E"),
    ("ONE Apus",         "9812345", "Yantian",       "Long Beach",      -6, 19.8,  "30.1°N 140.2°W"),
    ("HMM Algeciras",    "9890123", "Kaohsiung",     "Antwerp",        -30, 17.2,  "37.2°N 9.8°W"),
    ("Yang Ming Wish",   "9456789", "Tokyo",         "Seattle",          0, 20.1,  "47.6°N 127.4°W"),
    ("Evergreen Ever",   "9234567", "Port Said",     "Houston",          4, 16.9,  "28.5°N 89.2°W"),
    ("Hapag Dublin",     "9678901", "Colombo",       "Felixstowe",       6, 18.4,  "49.8°N 1.2°W"),
    ("COSCO Shipping",   "9123456", "Tianjin",       "Vancouver",       10, 20.8,  "48.4°N 125.1°W"),
    ("PIL Dakar",        "9345123", "Dakar",         "Rotterdam",       14, 15.3,  "47.5°N 8.3°W"),
    ("ZIM Kingston",     "9567890", "Ashdod",        "New York",        18, 17.6,  "36.1°N 65.4°W"),
    ("Wan Hai 505",      "9789012", "Keelung",       "Singapore",        2, 19.2,  "12.5°N 108.3°E"),
    ("Seaspan Emerald",  "9012345", "Prince Rupert", "Shanghai",         8, 21.0,  "42.3°N 167.5°E"),
    ("MOL Triumph",      "9234012", "Yokohama",      "Durban",          26, 16.1,  "28.9°S 33.7°E"),
    ("Navios Harmony",   "9456234", "Santos",        "Algeciras",       -4, 18.9,  "35.9°N 5.4°W"),
    ("Pacific Basin",    "9678456", "Manila",        "Busan",            0, 20.3,  "27.1°N 124.6°E"),
    ("MSC Carlotta",     "9890678", "Le Havre",      "Montreal",        36, 17.4,  "47.2°N 53.8°W"),
    ("CMA CGM Libra",    "9012890", "Jeddah",        "Rotterdam",       12, 18.1,  "37.4°N 14.2°E"),
    ("Maersk Kensington","9234901", "Melbourne",     "Shenzhen",        -8, 22.0,  "18.3°S 142.7°E"),
    ("Evergreen Ever A", "9456012", "Los Angeles",   "Shanghai",         2, 21.5,  "22.1°N 156.3°W"),
    ("K-Line Courage",   "9678234", "Incheon",       "Hamburg",         48, 15.8,  "44.7°N 21.3°E"),
    ("Nordic Reefer",    "9890456", "Reykjavik",     "Rotterdam",        0, 14.2,  "58.3°N 5.6°W"),
    ("OOCL Hong Kong",   "9012678", "Hong Kong",     "London Gateway",  22, 18.6,  "44.1°N 4.8°W"),
    ("Stolt Tanker",     "9234890", "Houston",       "Antwerp",         -2, 16.7,  "42.1°N 61.3°W"),
]

_ROUTES_DIST: dict[tuple[str, str], int] = {
    ("Shanghai",     "Rotterdam"):       11500,
    ("Busan",        "Los Angeles"):      5900,
    ("Singapore",    "New York"):        10200,
    ("Ningbo",       "Hamburg"):         11700,
    ("Yantian",      "Long Beach"):       6100,
    ("Kaohsiung",    "Antwerp"):         11800,
    ("Tokyo",        "Seattle"):          4800,
    ("Port Said",    "Houston"):          8400,
    ("Colombo",      "Felixstowe"):       7200,
    ("Tianjin",      "Vancouver"):        5600,
    ("Los Angeles",  "Shanghai"):         6000,
    ("Houston",      "Antwerp"):          8200,
}

_CARGO_TYPES = ["Container (TEU)", "Bulk (MT)", "Liquid Bulk (MT)", "Ro-Ro", "Breakbulk"]
_CARRIERS = [
    "Maersk", "MSC", "CMA CGM", "COSCO", "Hapag-Lloyd",
    "ONE", "Evergreen", "Yang Ming", "HMM", "ZIM",
]

_WEATHER_ROUTES = [
    ("North Atlantic",      "Extratropical Cyclone", 0.72, 38, 14),
    ("Trans-Pacific",       "Typhoon Formation",     0.55, 52, 9),
    ("Gulf of Aden",        "Monsoon Swell",         0.41, 18, 7),
    ("English Channel",     "Storm System",          0.63, 24, 22),
    ("Bay of Bengal",       "Cyclonic Activity",     0.38, 31, 5),
    ("Cape of Good Hope",   "Southern Ocean Gale",   0.80, 44, 11),
    ("Strait of Malacca",   "Squall Line",           0.29, 12, 18),
    ("Mediterranean East",  "Sirocco Wind",          0.45, 20, 8),
    ("North Sea",           "Severe Depression",     0.68, 29, 17),
    ("Caribbean",           "Tropical Wave",         0.33, 16, 6),
    ("Yellow Sea",          "Fog/Low Visibility",    0.52, 14, 12),
    ("Indian Ocean West",   "Swell Pattern",         0.37, 22, 9),
    ("Norwegian Sea",       "Polar Vortex",          0.61, 35, 4),
    ("Gulf of Mexico",      "Cold Front",            0.44, 18, 15),
]

_PORT_QUEUES = [
    ("Shanghai",     "CNSHA", 48,  18.2),
    ("Singapore",    "SGSIN", 24,   6.1),
    ("Rotterdam",    "NLRTM", 19,   9.4),
    ("Los Angeles",  "USLAX", 61,  31.6),
    ("Antwerp",      "BEANR", 22,  11.2),
    ("Hamburg",      "DEHAM", 17,   7.8),
    ("Busan",        "KRPUS", 35,  14.3),
    ("Hong Kong",    "HKHKG", 29,  12.7),
    ("Long Beach",   "USLGB", 54,  28.4),
    ("Ningbo",       "CNNBO", 41,  16.9),
]

_MONTHS_18 = [
    (date(2024,  9, 1), "Sep '24"),
    (date(2024, 10, 1), "Oct '24"),
    (date(2024, 11, 1), "Nov '24"),
    (date(2024, 12, 1), "Dec '24"),
    (date(2025,  1, 1), "Jan '25"),
    (date(2025,  2, 1), "Feb '25"),
    (date(2025,  3, 1), "Mar '25"),
    (date(2025,  4, 1), "Apr '25"),
    (date(2025,  5, 1), "May '25"),
    (date(2025,  6, 1), "Jun '25"),
    (date(2025,  7, 1), "Jul '25"),
    (date(2025,  8, 1), "Aug '25"),
    (date(2025,  9, 1), "Sep '25"),
    (date(2025, 10, 1), "Oct '25"),
    (date(2025, 11, 1), "Nov '25"),
    (date(2025, 12, 1), "Dec '25"),
    (date(2026,  1, 1), "Jan '26"),
    (date(2026,  2, 1), "Feb '26"),
]

# Seeded reliability curves per carrier (top 5 vs bottom 5)
_TOP_CARRIERS    = ["Maersk", "Hapag-Lloyd", "CMA CGM", "ONE", "Evergreen"]
_BOTTOM_CARRIERS = ["HMM", "Yang Ming", "ZIM", "PIL", "IRISL"]

random.seed(42)

def _reliability_series(base: float, volatility: float, n: int = 18) -> list[float]:
    vals, v = [], base
    for _ in range(n):
        v = max(30.0, min(95.0, v + random.gauss(0, volatility)))
        vals.append(round(v, 1))
    return vals


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------

def _kpi_card(label: str, value: str, sub: str = "", color: str = C_TEXT) -> str:
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;'
        f'padding:20px 16px;text-align:center;">'
        f'<div style="font-size:28px;font-weight:700;color:{color};line-height:1.1;">{value}</div>'
        f'<div style="font-size:12px;color:{C_TEXT2};margin-top:4px;text-transform:uppercase;'
        f'letter-spacing:.06em;">{label}</div>'
        f'{"<div style=font-size:11px;color:" + C_TEXT3 + ";margin-top:2px;>" + sub + "</div>" if sub else ""}'
        f'</div>'
    )


def _section_header(title: str, sub: str = "") -> None:
    sub_html = f'<div style="font-size:13px;color:{C_TEXT2};margin-top:2px;">{sub}</div>' if sub else ""
    st.markdown(
        f'<div style="margin:28px 0 12px;border-left:3px solid {C_ACCENT};padding-left:12px;">'
        f'<div style="font-size:18px;font-weight:700;color:{C_TEXT};">{title}</div>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )


def _delay_color(hrs: int) -> str:
    if hrs < 0:
        return C_HIGH
    if hrs == 0:
        return C_TEXT2
    if hrs <= 24:
        return C_MOD
    return C_LOW


def _status_badge(hrs: int) -> str:
    if hrs < 0:
        return f'<span style="background:rgba(16,185,129,.18);color:{C_HIGH};border-radius:4px;padding:2px 7px;font-size:11px;font-weight:600;">AHEAD</span>'
    if hrs == 0:
        return f'<span style="background:rgba(148,163,184,.12);color:{C_TEXT2};border-radius:4px;padding:2px 7px;font-size:11px;font-weight:600;">ON TIME</span>'
    if hrs <= 24:
        return f'<span style="background:rgba(245,158,11,.18);color:{C_MOD};border-radius:4px;padding:2px 7px;font-size:11px;font-weight:600;">DELAYED</span>'
    return f'<span style="background:rgba(239,68,68,.18);color:{C_LOW};border-radius:4px;padding:2px 7px;font-size:11px;font-weight:600;">DIVERTED</span>'


# ---------------------------------------------------------------------------
# Section 1 — KPI Dashboard
# ---------------------------------------------------------------------------

def _render_kpis() -> None:
    try:
        delays = [v[4] for v in _VESSELS]
        tracked = len(_VESSELS)
        on_time_pct = round(100 * sum(1 for d in delays if d <= 0) / tracked, 1)
        avg_delay = round(sum(d for d in delays if d > 0) / max(1, sum(1 for d in delays if d > 0)), 1)
        worst = max(delays)
        unknown = 0

        cols = st.columns(5)
        cards = [
            ("Vessels Tracked",        str(tracked),          "active voyages",         C_ACCENT),
            ("On-Time Arrival",        f"{on_time_pct}%",     "vs 65% industry avg",    C_HIGH),
            ("Avg Delay (delayed)",    f"{avg_delay}h",       "hours per delayed vessel", C_MOD),
            ("Worst Delay",            f"{worst}h",           "max delay in fleet",     C_LOW),
            ("Unknown ETA",            str(unknown),          "vessels with no ETA",    C_TEXT2),
        ]
        for col, (lbl, val, sub, clr) in zip(cols, cards):
            col.markdown(_kpi_card(lbl, val, sub, clr), unsafe_allow_html=True)
    except Exception:
        logger.exception("ETA KPI render failed")
        st.warning("KPI data unavailable.")


# ---------------------------------------------------------------------------
# Section 2 — Vessel Voyage Tracker
# ---------------------------------------------------------------------------

def _render_voyage_tracker() -> None:
    try:
        _section_header(
            "Vessel Voyage Tracker",
            "Real-time voyage positions and ETA status — refreshed hourly",
        )

        today = date(2026, 3, 22)

        rows_html = ""
        for name, imo, orig, dest, delay_hrs, spd, pos in _VESSELS:
            departed = today - timedelta(days=random.randint(3, 25))
            orig_eta = today + timedelta(days=random.randint(1, 18))
            curr_eta = orig_eta + timedelta(hours=delay_hrs)
            dc = _delay_color(delay_hrs)
            badge = _status_badge(delay_hrs)
            delay_txt = f'<span style="color:{dc};font-weight:600;">{"+" if delay_hrs > 0 else ""}{delay_hrs}h</span>'
            rows_html += (
                f"<tr>"
                f'<td style="color:{C_TEXT};font-weight:600;">{name}</td>'
                f'<td style="color:{C_TEXT3};">{imo}</td>'
                f'<td style="color:{C_TEXT2};">{orig}</td>'
                f'<td style="color:{C_TEXT2};">{dest}</td>'
                f'<td style="color:{C_TEXT3};">{departed.strftime("%b %d")}</td>'
                f'<td style="color:{C_TEXT2};">{orig_eta.strftime("%b %d")}</td>'
                f'<td style="color:{C_TEXT};">{curr_eta.strftime("%b %d")}</td>'
                f"<td>{delay_txt}</td>"
                f"<td>{badge}</td>"
                f'<td style="color:{C_ACCENT};">{spd} kn</td>'
                f'<td style="color:{C_TEXT3};font-size:11px;">{pos}</td>'
                f"</tr>"
            )

        th_style = f'style="color:{C_TEXT3};font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:8px 10px;border-bottom:1px solid {C_BORDER};"'
        td_style  = f'style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);"'

        table_html = (
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;'
            f'overflow-x:auto;padding:4px 0;">'
            f'<table style="width:100%;border-collapse:collapse;font-size:13px;">'
            f'<thead><tr>'
            f'<th {th_style}>VESSEL</th>'
            f'<th {th_style}>IMO</th>'
            f'<th {th_style}>ORIGIN</th>'
            f'<th {th_style}>DESTINATION</th>'
            f'<th {th_style}>DEPARTED</th>'
            f'<th {th_style}>ORIG ETA</th>'
            f'<th {th_style}>CURR ETA</th>'
            f'<th {th_style}>DELAY</th>'
            f'<th {th_style}>STATUS</th>'
            f'<th {th_style}>SPEED</th>'
            f'<th {th_style}>POSITION</th>'
            f'</tr></thead>'
            f'<tbody style="color:{C_TEXT};">'
        )

        # inject td_style per row — rebuild rows with proper td padding
        rows_final = ""
        for name, imo, orig, dest, delay_hrs, spd, pos in _VESSELS:
            departed = date(2026, 3, 22) - timedelta(days=abs(hash(name)) % 22 + 3)
            orig_eta = date(2026, 3, 22) + timedelta(days=abs(hash(name + "e")) % 18 + 1)
            curr_eta = orig_eta + timedelta(hours=delay_hrs)
            dc = _delay_color(delay_hrs)
            badge = _status_badge(delay_hrs)
            delay_txt = f'<span style="color:{dc};font-weight:600;">{"+" if delay_hrs > 0 else ""}{delay_hrs}h</span>'
            td = f'style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);"'
            rows_final += (
                f"<tr>"
                f'<td {td} style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_TEXT};font-weight:600;">{name}</td>'
                f'<td {td} style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_TEXT3};">{imo}</td>'
                f'<td {td} style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_TEXT2};">{orig}</td>'
                f'<td {td} style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_TEXT2};">{dest}</td>'
                f'<td {td} style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_TEXT3};">{departed.strftime("%b %d")}</td>'
                f'<td {td} style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_TEXT2};">{orig_eta.strftime("%b %d")}</td>'
                f'<td {td} style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_TEXT};">{curr_eta.strftime("%b %d")}</td>'
                f'<td style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);">{delay_txt}</td>'
                f'<td style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);">{badge}</td>'
                f'<td {td} style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_ACCENT};">{spd} kn</td>'
                f'<td {td} style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_TEXT3};font-size:11px;">{pos}</td>'
                f"</tr>"
            )

        st.markdown(table_html + rows_final + "</tbody></table></div>", unsafe_allow_html=True)
    except Exception:
        logger.exception("Voyage tracker render failed")
        st.error("Voyage tracker unavailable.")


# ---------------------------------------------------------------------------
# Section 3 — ETA Calculator
# ---------------------------------------------------------------------------

def _render_eta_calculator() -> None:
    try:
        _section_header(
            "ETA Calculator",
            "Estimate transit time, distance, fuel consumption, and route risk factors",
        )

        all_ports = sorted({p for pair in _ROUTES_DIST for p in pair})
        cargo_options = _CARGO_TYPES

        with st.container():
            c1, c2, c3, c4 = st.columns(4)
            origin      = c1.selectbox("Origin Port",      all_ports, key="eta_calc_origin")
            destination = c2.selectbox("Destination Port", all_ports, index=min(1, len(all_ports)-1), key="eta_calc_dest")
            speed_kn    = c3.slider("Vessel Speed (kn)", 10, 25, 18, key="eta_calc_speed")
            cargo_type  = c4.selectbox("Cargo Type", cargo_options, key="eta_calc_cargo")

        if st.button("Calculate ETA", key="eta_calc_btn", type="primary"):
            try:
                dist_nm = _ROUTES_DIST.get((origin, destination)) or _ROUTES_DIST.get((destination, origin))
                if dist_nm is None:
                    dist_nm = int(abs(hash(origin + destination)) % 6000 + 3000)

                transit_days   = round(dist_nm / (speed_kn * 24), 1)
                fuel_rate_mt   = {"Container (TEU)": 120, "Bulk (MT)": 90, "Liquid Bulk (MT)": 95,
                                  "Ro-Ro": 85, "Breakbulk": 75}.get(cargo_type, 100)
                fuel_total_mt  = round(fuel_rate_mt * transit_days, 0)
                bunker_usd_mt  = 620
                bunker_cost    = int(fuel_total_mt * bunker_usd_mt)
                accuracy_pct   = round(random.uniform(6.0, 15.0), 1)
                voyage_count   = random.randint(800, 2800)

                congestion_risk = random.choice(["Low", "Moderate", "High"])
                weather_risk    = random.choice(["Low", "Moderate", "High"])
                canal_wait_hrs  = random.randint(0, 48)

                risk_colors = {"Low": C_HIGH, "Moderate": C_MOD, "High": C_LOW}
                cr_clr = risk_colors[congestion_risk]
                wr_clr = risk_colors[weather_risk]

                result_html = (
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;padding:20px 24px;margin-top:12px;">'
                    f'<div style="font-size:15px;font-weight:700;color:{C_TEXT};margin-bottom:14px;">'
                    f'Route: {origin} → {destination}</div>'
                    f'<div style="display:flex;gap:32px;flex-wrap:wrap;">'
                    f'<div><div style="font-size:24px;font-weight:700;color:{C_ACCENT};">{transit_days}d</div>'
                    f'<div style="font-size:11px;color:{C_TEXT3};text-transform:uppercase;">Transit Time</div></div>'
                    f'<div><div style="font-size:24px;font-weight:700;color:{C_TEXT};">{dist_nm:,} nm</div>'
                    f'<div style="font-size:11px;color:{C_TEXT3};text-transform:uppercase;">Distance</div></div>'
                    f'<div><div style="font-size:24px;font-weight:700;color:{C_MOD};">{int(fuel_total_mt):,} MT</div>'
                    f'<div style="font-size:11px;color:{C_TEXT3};text-transform:uppercase;">Fuel Consumption</div></div>'
                    f'<div><div style="font-size:24px;font-weight:700;color:{C_HIGH};">${bunker_cost:,}</div>'
                    f'<div style="font-size:11px;color:{C_TEXT3};text-transform:uppercase;">Est. Bunker Cost</div></div>'
                    f'</div>'
                    f'<div style="margin-top:16px;padding-top:16px;border-top:1px solid {C_BORDER};">'
                    f'<div style="font-size:12px;color:{C_TEXT2};">Historical accuracy: '
                    f'<span style="color:{C_ACCENT};font-weight:600;">±{accuracy_pct}%</span> '
                    f'based on <span style="color:{C_TEXT};font-weight:600;">{voyage_count:,} voyages</span></div>'
                    f'</div>'
                    f'<div style="margin-top:14px;display:flex;gap:20px;flex-wrap:wrap;">'
                    f'<span style="font-size:12px;color:{C_TEXT2};">Port Congestion: <span style="color:{cr_clr};font-weight:600;">{congestion_risk}</span></span>'
                    f'<span style="font-size:12px;color:{C_TEXT2};">Weather Risk: <span style="color:{wr_clr};font-weight:600;">{weather_risk}</span></span>'
                    f'<span style="font-size:12px;color:{C_TEXT2};">Canal Wait: <span style="color:{C_MOD};font-weight:600;">{canal_wait_hrs}h</span></span>'
                    f'</div>'
                    f'</div>'
                )
                st.markdown(result_html, unsafe_allow_html=True)
            except Exception:
                logger.exception("ETA calculation inner error")
                st.error("Calculation failed.")
    except Exception:
        logger.exception("ETA calculator render failed")
        st.error("ETA calculator unavailable.")


# ---------------------------------------------------------------------------
# Section 4 — Delay Analysis
# ---------------------------------------------------------------------------

def _render_delay_analysis() -> None:
    try:
        _section_header(
            "Delay Analysis",
            "Which routes and ports drive the most schedule disruption",
        )

        route_delays = {
            "Shanghai–Rotterdam": 28.4,
            "Busan–Los Angeles": 14.2,
            "Singapore–New York": 19.7,
            "Ningbo–Hamburg": 31.1,
            "Yantian–Long Beach": 12.8,
            "Kaohsiung–Antwerp": 36.5,
            "Tokyo–Seattle": 8.3,
            "Port Said–Houston": 22.6,
            "Colombo–Felixstowe": 17.9,
            "Tianjin–Vancouver": 11.4,
        }
        port_delays = {
            "Los Angeles":  41.2,
            "Long Beach":   38.7,
            "Shanghai":     29.4,
            "Antwerp":      24.8,
            "Rotterdam":    18.3,
            "Hamburg":      16.9,
            "Busan":        22.1,
            "Hong Kong":    19.6,
            "Ningbo":       26.3,
            "Singapore":    11.7,
        }
        delay_distribution = (
            [-12, -8, -6, -4, -2, 0] * 3
            + [2, 4, 6, 8, 10, 12, 14, 16, 18, 20] * 5
            + [24, 28, 32, 36, 40, 48, 60, 72, 96] * 2
        )

        col1, col2 = st.columns(2)

        with col1:
            routes = list(route_delays.keys())
            vals   = list(route_delays.values())
            colors = [C_LOW if v > 25 else C_MOD if v > 15 else C_HIGH for v in vals]
            fig_r = go.Figure(go.Bar(
                x=vals, y=routes, orientation="h",
                marker_color=colors,
                text=[f"{v}h" for v in vals],
                textposition="outside",
                textfont=dict(color=C_TEXT2, size=11),
            ))
            fig_r.update_layout(**_CHART_LAYOUT, title="Avg Delay by Route (hours)", height=360)
            fig_r.update_xaxes(title="Avg Delay (h)")
            st.plotly_chart(fig_r, use_container_width=True, key="eta_delay_by_route")

        with col2:
            ports  = list(port_delays.keys())
            pvals  = list(port_delays.values())
            pcolors = [C_LOW if v > 30 else C_MOD if v > 18 else C_HIGH for v in pvals]
            fig_p = go.Figure(go.Bar(
                x=pvals, y=ports, orientation="h",
                marker_color=pcolors,
                text=[f"{v}h" for v in pvals],
                textposition="outside",
                textfont=dict(color=C_TEXT2, size=11),
            ))
            fig_p.update_layout(**_CHART_LAYOUT, title="Avg Delay by Port (hours)", height=360)
            fig_p.update_xaxes(title="Avg Delay (h)")
            st.plotly_chart(fig_p, use_container_width=True, key="eta_delay_by_port")

        # Delay distribution histogram
        sorted_delays = sorted(delay_distribution)
        n = len(sorted_delays)
        median_val = sorted_delays[n // 2]
        p80_val    = sorted_delays[int(n * 0.80)]
        p95_val    = sorted_delays[int(n * 0.95)]

        fig_h = go.Figure(go.Histogram(
            x=delay_distribution,
            nbinsx=28,
            marker_color=C_ACCENT,
            opacity=0.8,
            name="Voyages",
        ))
        for pval, plabel, pclr in [
            (median_val, f"Median: {median_val}h", C_HIGH),
            (p80_val,   f"P80: {p80_val}h",       C_MOD),
            (p95_val,   f"P95: {p95_val}h",       C_LOW),
        ]:
            fig_h.add_vline(x=pval, line_dash="dash", line_color=pclr,
                            annotation_text=plabel,
                            annotation_font_color=pclr,
                            annotation_position="top right")
        fig_h.update_layout(**_CHART_LAYOUT, title="Delay Distribution (hours) — All Routes", height=320)
        fig_h.update_xaxes(title="Delay (hours)")
        fig_h.update_yaxes(title="Voyage Count")
        st.plotly_chart(fig_h, use_container_width=True, key="eta_delay_distribution")
    except Exception:
        logger.exception("Delay analysis render failed")
        st.error("Delay analysis unavailable.")


# ---------------------------------------------------------------------------
# Section 5 — Schedule Reliability Trends
# ---------------------------------------------------------------------------

def _render_reliability_trends() -> None:
    try:
        _section_header(
            "Schedule Reliability Trends",
            "Carrier on-time performance over 18 months vs industry average (65%)",
        )

        months_labels = [m[1] for m in _MONTHS_18]
        industry_avg  = [65.0] * 18

        fig = go.Figure()

        top_bases = {"Maersk": 74, "Hapag-Lloyd": 71, "CMA CGM": 69, "ONE": 68, "Evergreen": 66}
        bot_bases = {"HMM": 55, "Yang Ming": 57, "ZIM": 54, "PIL": 51, "IRISL": 48}

        random.seed(7)
        for carrier, base in top_bases.items():
            series = _reliability_series(base, 3.5)
            fig.add_trace(go.Scatter(
                x=months_labels, y=series, mode="lines+markers",
                name=carrier, line=dict(width=2),
                marker=dict(size=5),
            ))

        for carrier, base in bot_bases.items():
            series = _reliability_series(base, 4.2)
            fig.add_trace(go.Scatter(
                x=months_labels, y=series, mode="lines",
                name=carrier, line=dict(width=1.5, dash="dot"),
            ))

        fig.add_trace(go.Scatter(
            x=months_labels, y=industry_avg, mode="lines",
            name="Industry Avg", line=dict(color=C_TEXT3, width=1.5, dash="dash"),
        ))

        fig.update_layout(
            **_CHART_LAYOUT,
            title="Carrier Schedule Reliability % — Last 18 Months",
            height=400,
            legend=dict(orientation="h", y=-0.18, font=dict(size=11)),
            yaxis=dict(range=[35, 90], title="On-Time %", gridcolor="rgba(255,255,255,0.05)"),
        )
        st.plotly_chart(fig, use_container_width=True, key="eta_reliability_trends")
    except Exception:
        logger.exception("Reliability trends render failed")
        st.error("Reliability trends unavailable.")


# ---------------------------------------------------------------------------
# Section 6 — Weather Delay Forecast
# ---------------------------------------------------------------------------

def _render_weather_forecast() -> None:
    try:
        _section_header(
            "Weather Delay Forecast — Next 14 Days",
            "Routes with elevated weather delay risk based on current meteorological data",
        )

        header_style = f'style="color:{C_TEXT3};font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:8px 10px;border-bottom:1px solid {C_BORDER};"'
        rows_html = ""
        for route, system, prob, delay_h, affected in _WEATHER_ROUTES:
            prob_pct = f"{int(prob * 100)}%"
            if prob >= 0.65:
                prob_clr = C_LOW
            elif prob >= 0.40:
                prob_clr = C_MOD
            else:
                prob_clr = C_HIGH

            if delay_h >= 36:
                delay_clr = C_LOW
            elif delay_h >= 20:
                delay_clr = C_MOD
            else:
                delay_clr = C_TEXT2

            rows_html += (
                f'<tr style="font-size:13px;">'
                f'<td style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_TEXT};font-weight:600;">{route}</td>'
                f'<td style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_TEXT2};">{system}</td>'
                f'<td style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{prob_clr};font-weight:600;">{prob_pct}</td>'
                f'<td style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{delay_clr};font-weight:600;">{delay_h}h</td>'
                f'<td style="padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_ACCENT};">{affected} vessels</td>'
                f'</tr>'
            )

        table_html = (
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow-x:auto;padding:4px 0;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>'
            f'<th {header_style}>ROUTE</th>'
            f'<th {header_style}>WEATHER SYSTEM</th>'
            f'<th {header_style}>DELAY PROBABILITY</th>'
            f'<th {header_style}>EXPECTED DELAY</th>'
            f'<th {header_style}>AFFECTED VESSELS</th>'
            f'</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>'
        )
        st.markdown(table_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Weather forecast render failed")
        st.error("Weather forecast unavailable.")


# ---------------------------------------------------------------------------
# Section 7 — Port Queue Tracker
# ---------------------------------------------------------------------------

def _render_port_queue() -> None:
    try:
        _section_header(
            "Port Queue Tracker — Top 10 Busiest Ports",
            "Current anchorage queue depth and estimated wait time — updated hourly",
        )

        col1, col2 = st.columns([2, 1])

        with col1:
            header_style = f'style="color:{C_TEXT3};font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:8px 12px;border-bottom:1px solid {C_BORDER};"'
            rows_html = ""
            for port, locode, vessels, wait_h in _PORT_QUEUES:
                bar_pct = min(100, int(vessels / 65 * 100))
                if vessels >= 45:
                    bar_clr = C_LOW
                    wait_clr = C_LOW
                elif vessels >= 25:
                    bar_clr = C_MOD
                    wait_clr = C_MOD
                else:
                    bar_clr = C_HIGH
                    wait_clr = C_HIGH

                bar_html = (
                    f'<div style="background:rgba(255,255,255,0.07);border-radius:3px;height:6px;width:140px;margin-top:4px;">'
                    f'<div style="background:{bar_clr};width:{bar_pct}%;height:6px;border-radius:3px;"></div>'
                    f'</div>'
                )
                rows_html += (
                    f'<tr style="font-size:13px;">'
                    f'<td style="padding:10px 12px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_TEXT};font-weight:600;">{port}</td>'
                    f'<td style="padding:10px 12px;border-bottom:1px solid rgba(255,255,255,0.04);color:{C_TEXT3};">{locode}</td>'
                    f'<td style="padding:10px 12px;border-bottom:1px solid rgba(255,255,255,0.04);">'
                    f'<span style="color:{bar_clr};font-weight:700;font-size:15px;">{vessels}</span>'
                    f'<span style="color:{C_TEXT3};font-size:11px;"> vessels</span>'
                    f'{bar_html}</td>'
                    f'<td style="padding:10px 12px;border-bottom:1px solid rgba(255,255,255,0.04);color:{wait_clr};font-weight:600;">{wait_h}h</td>'
                    f'</tr>'
                )

            table_html = (
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden;">'
                f'<table style="width:100%;border-collapse:collapse;">'
                f'<thead><tr>'
                f'<th {header_style}>PORT</th>'
                f'<th {header_style}>LOCODE</th>'
                f'<th {header_style}>VESSELS WAITING</th>'
                f'<th {header_style}>EST. WAIT TIME</th>'
                f'</tr></thead>'
                f'<tbody>{rows_html}</tbody>'
                f'</table></div>'
            )
            st.markdown(table_html, unsafe_allow_html=True)

        with col2:
            ports_chart  = [p[0] for p in _PORT_QUEUES]
            vessels_list = [p[2] for p in _PORT_QUEUES]
            colors_list  = [C_LOW if v >= 45 else C_MOD if v >= 25 else C_HIGH for v in vessels_list]
            fig_q = go.Figure(go.Bar(
                y=ports_chart, x=vessels_list, orientation="h",
                marker_color=colors_list,
                text=vessels_list,
                textposition="outside",
                textfont=dict(color=C_TEXT2, size=11),
            ))
            fig_q.update_layout(
                **_CHART_LAYOUT,
                title="Vessels in Queue",
                height=380,
                margin=dict(t=40, b=20, l=100, r=40),
            )
            fig_q.update_xaxes(title="Vessel Count")
            st.plotly_chart(fig_q, use_container_width=True, key="eta_port_queue_chart")
    except Exception:
        logger.exception("Port queue render failed")
        st.error("Port queue tracker unavailable.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render(port_results=None, route_results=None) -> None:
    try:
        st.markdown(
            f'<div style="padding:20px 0 4px;">'
            f'<div style="font-size:26px;font-weight:800;color:{C_TEXT};letter-spacing:-.02em;">'
            f'ETA Intelligence &amp; Voyage Tracking</div>'
            f'<div style="font-size:14px;color:{C_TEXT2};margin-top:4px;">'
            f'Vessel ETA prediction, delay analysis, carrier reliability, and port queue monitoring</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        _section_header("ETA Intelligence Dashboard", "Fleet-wide on-time performance snapshot")
        _render_kpis()

        _render_voyage_tracker()
        _render_eta_calculator()
        _render_delay_analysis()
        _render_reliability_trends()
        _render_weather_forecast()
        _render_port_queue()

    except Exception:
        logger.exception("tab_eta render failed")
        st.error("ETA tab failed to load. Check logs for details.")
