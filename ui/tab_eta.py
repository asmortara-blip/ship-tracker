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
from datetime import date, timedelta

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from utils.helpers import stable_hash

from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# ETA tab data is wholly modeled / mock fleet — surface that in every footer.
_ETA_SOURCES = [
    {"name": "Synthetic vessel telemetry", "kind": "modeled", "quality": "demo"},
    {"name": "Mock voyage delay log",      "kind": "modeled", "quality": "demo"},
]

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

def _reliability_series(
    base: float,
    volatility: float,
    rng: random.Random,
    n: int = 18,
) -> list[float]:
    """Synthesize a noisy reliability path. Caller supplies an instance RNG so
    the global ``random`` module state is never mutated mid-render (two tabs
    rendering concurrently would otherwise step on each other's seeds).
    """
    vals, v = [], base
    for _ in range(n):
        v = max(30.0, min(95.0, v + rng.gauss(0, volatility)))
        vals.append(round(v, 1))
    return vals


# ---------------------------------------------------------------------------
# Cell formatters
# ---------------------------------------------------------------------------
def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};font-size:0.82rem;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};font-size:0.82rem;">{value}</span>'
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
        return badge("AHEAD", C_HIGH)
    if hrs == 0:
        return badge("ON TIME", C_TEXT3)
    if hrs <= 24:
        return badge("DELAYED", C_MOD)
    return badge("DIVERTED", C_LOW)


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

        metric_card_row([
            {"label": "VESSELS TRACKED",     "value": str(tracked),    "accent": C_ACCENT, "sublabel": "active voyages"},
            {"label": "ON-TIME ARRIVAL",     "value": f"{on_time_pct}%", "accent": C_HIGH, "sublabel": "vs 65% industry avg"},
            {"label": "AVG DELAY (DELAYED)", "value": f"{avg_delay}h",  "accent": C_MOD,    "sublabel": "hours per delayed vessel"},
            {"label": "WORST DELAY",         "value": f"{worst}h",      "accent": C_LOW,    "sublabel": "max delay in fleet"},
            {"label": "UNKNOWN ETA",         "value": str(unknown),     "accent": C_TEXT2,  "sublabel": "vessels with no ETA"},
        ], columns=5)
        st.markdown(source_footer(_ETA_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("ETA KPI render failed")
        st.warning("KPI data unavailable.")


# ---------------------------------------------------------------------------
# Section 2 — Vessel Voyage Tracker
# ---------------------------------------------------------------------------

def _render_voyage_tracker() -> None:
    try:
        section_header(
            "Vessel Voyage Tracker",
            "Real-time voyage positions and ETA status — refreshed hourly",
        )

        today = date(2026, 3, 22)
        headers = ["Vessel", "IMO", "Origin", "Destination", "Departed", "Orig ETA", "Curr ETA", "Delay", "Status", "Speed", "Position"]
        rows: list[list[str]] = []
        for name, imo, orig, dest, delay_hrs, spd, pos in _VESSELS:
            departed = today - timedelta(days=stable_hash(name) % 22 + 3)
            orig_eta = today + timedelta(days=stable_hash(name + "e") % 18 + 1)
            curr_eta = orig_eta + timedelta(hours=delay_hrs)
            dc = _delay_color(delay_hrs)
            sign = "+" if delay_hrs > 0 else ""
            rows.append([
                _sans(name, weight=600),
                _mono(imo, color=C_TEXT3),
                _sans(orig, color=C_TEXT2),
                _sans(dest, color=C_TEXT2),
                _sans(departed.strftime("%b %d"), color=C_TEXT3),
                _sans(orig_eta.strftime("%b %d"), color=C_TEXT2),
                _sans(curr_eta.strftime("%b %d"), color=C_TEXT),
                _mono(f"{sign}{delay_hrs}h", color=dc, weight=600),
                _status_badge(delay_hrs),
                _mono(f"{spd} kn", color=C_ACCENT),
                _mono(pos, color=C_TEXT3),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer(_ETA_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Voyage tracker render failed")
        st.error("Voyage tracker unavailable.")


# ---------------------------------------------------------------------------
# Section 3 — ETA Calculator
# ---------------------------------------------------------------------------

def _render_eta_calculator() -> None:
    try:
        section_header(
            "ETA Calculator",
            "Estimate transit time, distance, fuel consumption, and route risk factors",
        )

        all_ports = sorted({p for pair in _ROUTES_DIST for p in pair})

        c1, c2, c3, c4 = st.columns(4)
        origin      = c1.selectbox("Origin Port",      all_ports, key="eta_calc_origin")
        destination = c2.selectbox("Destination Port", all_ports, index=min(1, len(all_ports)-1), key="eta_calc_dest")
        speed_kn    = c3.slider("Vessel Speed (kn)", 10, 25, 18, key="eta_calc_speed")
        cargo_type  = c4.selectbox("Cargo Type", _CARGO_TYPES, key="eta_calc_cargo")

        if st.button("Calculate ETA", key="eta_calc_btn", type="primary"):
            try:
                dist_nm = _ROUTES_DIST.get((origin, destination)) or _ROUTES_DIST.get((destination, origin))
                if dist_nm is None:
                    dist_nm = int(stable_hash(origin + destination) % 6000 + 3000)

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

                risk_accent = {"Low": C_HIGH, "Moderate": C_MOD, "High": C_LOW}

                section_header(
                    f"Route Estimate — {origin} → {destination}",
                    f"Modeled at {speed_kn} kn · {cargo_type}",
                )
                metric_card_row([
                    {"label": "TRANSIT TIME",   "value": f"{transit_days}d",       "accent": C_ACCENT},
                    {"label": "DISTANCE",       "value": f"{dist_nm:,} nm",        "accent": C_TEXT},
                    {"label": "FUEL CONSUMPTION","value": f"{int(fuel_total_mt):,} MT", "accent": C_MOD},
                    {"label": "EST. BUNKER COST","value": f"${bunker_cost:,}",    "accent": C_HIGH},
                ], columns=4)

                metric_card_row([
                    {"label": "HISTORICAL ACCURACY", "value": f"±{accuracy_pct}%",
                     "accent": C_ACCENT, "sublabel": f"based on {voyage_count:,} voyages"},
                    {"label": "PORT CONGESTION",     "value": congestion_risk,
                     "accent": risk_accent[congestion_risk], "sublabel": "current outlook"},
                    {"label": "WEATHER RISK",        "value": weather_risk,
                     "accent": risk_accent[weather_risk],    "sublabel": "voyage window"},
                    {"label": "CANAL WAIT",          "value": f"{canal_wait_hrs}h",
                     "accent": C_MOD, "sublabel": "estimated queue"},
                ], columns=4)
                st.markdown(source_footer(_ETA_SOURCES), unsafe_allow_html=True)
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
        section_header(
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
            apply_dark_layout(
                fig_r,
                title="Avg Delay by Route (hours)",
                height=360,
                margin=dict(t=48, b=36, l=150, r=24),
                xaxis=dict(title="Avg Delay (h)"),
            )
            st.plotly_chart(fig_r, use_container_width=True, key="eta_delay_by_route")

        with col2:
            ports   = list(port_delays.keys())
            pvals   = list(port_delays.values())
            pcolors = [C_LOW if v > 30 else C_MOD if v > 18 else C_HIGH for v in pvals]
            fig_p = go.Figure(go.Bar(
                x=pvals, y=ports, orientation="h",
                marker_color=pcolors,
                text=[f"{v}h" for v in pvals],
                textposition="outside",
                textfont=dict(color=C_TEXT2, size=11),
            ))
            apply_dark_layout(
                fig_p,
                title="Avg Delay by Port (hours)",
                height=360,
                margin=dict(t=48, b=36, l=120, r=24),
                xaxis=dict(title="Avg Delay (h)"),
            )
            st.plotly_chart(fig_p, use_container_width=True, key="eta_delay_by_port")

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
        apply_dark_layout(
            fig_h,
            title="Delay Distribution (hours) — All Routes",
            height=320,
            margin=dict(t=48, b=48, l=60, r=24),
            xaxis=dict(title="Delay (hours)"),
            yaxis=dict(title="Voyage Count"),
        )
        st.plotly_chart(fig_h, use_container_width=True, key="eta_delay_distribution")
        st.markdown(source_footer(_ETA_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Delay analysis render failed")
        st.error("Delay analysis unavailable.")


# ---------------------------------------------------------------------------
# Section 5 — Schedule Reliability Trends
# ---------------------------------------------------------------------------

def _render_reliability_trends() -> None:
    try:
        section_header(
            "Schedule Reliability Trends",
            "Carrier on-time performance over 18 months vs industry average (65%)",
        )

        months_labels = [m[1] for m in _MONTHS_18]
        industry_avg  = [65.0] * 18

        fig = go.Figure()

        top_bases = {"Maersk": 74, "Hapag-Lloyd": 71, "CMA CGM": 69, "ONE": 68, "Evergreen": 66}
        bot_bases = {"HMM": 55, "Yang Ming": 57, "ZIM": 54, "PIL": 51, "IRISL": 48}

        rng = random.Random(7)
        for carrier, base in top_bases.items():
            series = _reliability_series(base, 3.5, rng)
            fig.add_trace(go.Scatter(
                x=months_labels, y=series, mode="lines+markers",
                name=carrier, line=dict(width=2),
                marker=dict(size=5),
            ))

        for carrier, base in bot_bases.items():
            series = _reliability_series(base, 4.2, rng)
            fig.add_trace(go.Scatter(
                x=months_labels, y=series, mode="lines",
                name=carrier, line=dict(width=1.5, dash="dot"),
            ))

        fig.add_trace(go.Scatter(
            x=months_labels, y=industry_avg, mode="lines",
            name="Industry Avg", line=dict(color=C_TEXT3, width=1.5, dash="dash"),
        ))

        apply_dark_layout(
            fig,
            title="Carrier Schedule Reliability % — Last 18 Months",
            height=400,
            margin=dict(t=48, b=56, l=56, r=24),
            legend=dict(orientation="h", y=-0.18, font=dict(size=11)),
            yaxis=dict(range=[35, 90], title="On-Time %"),
        )
        st.plotly_chart(fig, use_container_width=True, key="eta_reliability_trends")
        st.markdown(source_footer(_ETA_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Reliability trends render failed")
        st.error("Reliability trends unavailable.")


# ---------------------------------------------------------------------------
# Section 6 — Weather Delay Forecast
# ---------------------------------------------------------------------------

def _render_weather_forecast() -> None:
    try:
        section_header(
            "Weather Delay Forecast — Next 14 Days",
            "Routes with elevated weather delay risk based on current meteorological data",
        )

        headers = ["Route", "Weather System", "Delay Probability", "Expected Delay", "Affected Vessels"]
        rows: list[list[str]] = []
        for route, system, prob, delay_h, affected in _WEATHER_ROUTES:
            prob_pct = f"{int(prob * 100)}%"
            prob_clr = C_LOW if prob >= 0.65 else C_MOD if prob >= 0.40 else C_HIGH
            delay_clr = C_LOW if delay_h >= 36 else C_MOD if delay_h >= 20 else C_TEXT2
            rows.append([
                _sans(route, weight=600),
                _sans(system, color=C_TEXT2),
                _mono(prob_pct, color=prob_clr, weight=700),
                _mono(f"{delay_h}h", color=delay_clr, weight=600),
                _mono(f"{affected} vessels", color=C_ACCENT),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer(_ETA_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Weather forecast render failed")
        st.error("Weather forecast unavailable.")


# ---------------------------------------------------------------------------
# Section 7 — Port Queue Tracker
# ---------------------------------------------------------------------------

def _render_port_queue() -> None:
    try:
        section_header(
            "Port Queue Tracker — Top 10 Busiest Ports",
            "Current anchorage queue depth and estimated wait time — updated hourly",
        )

        col1, col2 = st.columns([2, 1])

        with col1:
            headers = ["Port", "LOCODE", "Vessels Waiting", "Est. Wait Time"]
            rows: list[list[str]] = []
            for port, locode, vessels, wait_h in _PORT_QUEUES:
                bar_pct = min(100, int(vessels / 65 * 100))
                if vessels >= 45:
                    bar_clr, wait_clr = C_LOW, C_LOW
                elif vessels >= 25:
                    bar_clr, wait_clr = C_MOD, C_MOD
                else:
                    bar_clr, wait_clr = C_HIGH, C_HIGH
                filled = int(round(bar_pct / 10))
                bar_str = "█" * filled + "░" * (10 - filled)
                vessels_cell = (
                    _mono(f"{vessels} ", color=bar_clr, weight=700)
                    + _sans("vessels  ", color=C_TEXT3, weight=400)
                    + _mono(bar_str, color=bar_clr, weight=500)
                )
                rows.append([
                    _sans(port, weight=600),
                    _mono(locode, color=C_TEXT3),
                    vessels_cell,
                    _mono(f"{wait_h}h", color=wait_clr, weight=600),
                ])
            wsj_market_table(headers, rows)

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
            apply_dark_layout(
                fig_q,
                title="Vessels in Queue",
                height=380,
                margin=dict(t=40, b=20, l=100, r=40),
                xaxis=dict(title="Vessel Count"),
            )
            st.plotly_chart(fig_q, use_container_width=True, key="eta_port_queue_chart")

        st.markdown(source_footer(_ETA_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Port queue render failed")
        st.error("Port queue tracker unavailable.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render(port_results=None, route_results=None, freight_data=None,
           macro_data=None, **kwargs) -> None:
    try:
        page_header(
            title="ETA Intelligence & Voyage Tracking",
            subtitle="Vessel ETA prediction, delay analysis, carrier reliability and port queue monitoring",
            badge_text="ETA",
            badge_color=C_ACCENT,
        )

        section_header("ETA Intelligence Dashboard", "Fleet-wide on-time performance snapshot")
        _render_kpis()

        section_divider("Voyage Tracker")
        _render_voyage_tracker()
        section_divider("ETA Calculator")
        _render_eta_calculator()
        section_divider("Delay Analysis")
        _render_delay_analysis()
        section_divider("Reliability Trends")
        _render_reliability_trends()
        section_divider("Weather Forecast")
        _render_weather_forecast()
        section_divider("Port Queues")
        _render_port_queue()

    except Exception:
        logger.exception("tab_eta render failed")
        st.error("ETA tab failed to load. Check logs for details.")
