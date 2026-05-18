"""tab_visibility.py — Supply Chain Visibility & Tracking tab.

Renders shipment pipeline, visibility scores, exception management,
milestone tracking, and carrier visibility rankings.
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_CONV,
    C_HIGH,
    C_LOW,
    C_MACRO,
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

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

_VIS_SOURCES = [
    DataSource.demo("AIS Feed (mock)"),
    DataSource.demo("Carrier APIs (mock)"),
    DataSource.demo("Port EDI Streams (mock)"),
]

# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------

_PIPELINE_DATA = {
    "ORIGIN LOADED": [
        ("MAEU-2847561", "Shanghai → Rotterdam", "2,400 TEU"),
        ("MSCU-1938472", "Ningbo → Hamburg", "1,800 TEU"),
        ("COSCO-774832", "Shenzhen → LA", "3,200 TEU"),
        ("HLCU-992341", "Qingdao → NY", "1,100 TEU"),
    ],
    "IN TRANSIT": [
        ("MAEU-2103847", "Singapore → Antwerp", "2,100 TEU"),
        ("MSCU-3847261", "Port Klang → Felixstowe", "1,600 TEU"),
        ("EVGU-1029384", "Kaohsiung → Vancouver", "900 TEU"),
        ("CMAU-8837261", "Colombo → Hamburg", "1,400 TEU"),
        ("HLCU-2038471", "Dubai → Rotterdam", "2,800 TEU"),
    ],
    "CUSTOMS": [
        ("MSCU-9918374", "Rotterdam → Inland DE", "760 TEU"),
        ("OOLU-3847102", "LA → Chicago Rail", "540 TEU"),
        ("ZIMU-1029384", "Hamburg → Prague", "320 TEU"),
    ],
    "AT PORT": [
        ("MAEU-4482019", "Shanghai → Long Beach", "2,200 TEU"),
        ("HLCU-8827364", "Singapore → Felixstowe", "1,900 TEU"),
        ("CMAU-3748291", "Busan → Rotterdam", "1,300 TEU"),
        ("EGLV-9918273", "Ningbo → LA", "3,100 TEU"),
    ],
    "LAST MILE": [
        ("MSCU-1029374", "Rotterdam DC → Berlin", "180 TEU"),
        ("MAEU-8829103", "LA Port → Phoenix DC", "210 TEU"),
        ("HLCU-3748201", "Hamburg → Munich DC", "95 TEU"),
    ],
    "DELIVERED": [
        ("OOLU-2038471", "Shanghai → Rotterdam", "1,400 TEU"),
        ("COSCO-9918273", "Shenzhen → Antwerp", "2,600 TEU"),
        ("EVGU-4482019", "Busan → Hamburg", "800 TEU"),
        ("ZIMU-1029348", "Ningbo → Felixstowe", "1,100 TEU"),
        ("MAEU-7736291", "Singapore → NY", "950 TEU"),
    ],
}

_VISIBILITY_LANES = [
    ("Asia–Europe (AEX)", 94, 87, 91, C_HIGH),
    ("Transpacific EB (TPE)", 89, 82, 85, C_HIGH),
    ("Transpacific WB (TPW)", 86, 79, 83, C_HIGH),
    ("Asia–USEC (AEX2)", 78, 71, 76, C_MOD),
    ("Europe–USEC (EUX)", 81, 74, 79, C_MOD),
    ("Middle East–Europe", 68, 61, 65, C_MOD),
    ("Intra-Asia", 59, 48, 54, C_LOW),
    ("Africa–Europe", 52, 41, 47, C_LOW),
    ("LATAM–USEC", 61, 53, 58, C_MOD),
    ("Australia–Asia", 73, 66, 70, C_MOD),
]

_EXCEPTIONS = [
    ("MSCU-3847261", "MSC Zoe", "AIS Signal Lost", "48 hrs", "North Atlantic", "red", "Vessel went dark — signal lost near 42°N 28°W"),
    ("OOLU-1029384", "OOCL Europe", "Customs Hold", "72 hrs", "Rotterdam", "yellow", "Documentary discrepancy — phytosanitary cert missing"),
    ("HLCU-8827364", "Hapag Express", "Port Denial", "96 hrs", "Long Beach", "red", "Terminal congestion — vessel diverted to Oakland"),
    ("CMAU-2038471", "CMA CGM Marco Polo", "Carrier Change", "24 hrs", "Singapore", "yellow", "Alliance swap — cargo rolled to next sailing"),
    ("EVGU-4482019", "Ever Given II", "ETA Deviation >5d", "120 hrs", "Suez Canal", "red", "Route change via Cape of Good Hope"),
    ("MAEU-9918374", "Maersk Elba", "Reefer Alert", "6 hrs", "In Transit", "yellow", "Temperature excursion logged — monitoring active"),
    ("ZIMU-3748201", "ZIM Pacific", "AIS Signal Lost", "18 hrs", "Red Sea", "red", "Security zone — AIS intentionally disabled"),
]

_MILESTONE_STEPS = [
    ("Booking Confirmed", "2026-02-10 09:15", True, C_HIGH, "Booking MAEU-2847561 accepted by Maersk"),
    ("Vessel Assigned", "2026-02-12 14:30", True, C_HIGH, "MV Maersk Edmonton assigned — Voy. 026W"),
    ("Cargo Received at CFS", "2026-02-18 08:00", True, C_HIGH, "1,840 TEU received Shanghai Waigaoqiao terminal"),
    ("Loaded on Vessel", "2026-02-20 22:45", True, C_HIGH, "Stowage plan confirmed — Bay 12, 24, 36"),
    ("Departed Origin", "2026-02-21 06:00", True, C_HIGH, "Vessel departed Shanghai — AIS confirmed"),
    ("Transshipment (Singapore)", "2026-02-26 14:00", True, C_HIGH, "Feeder transfer complete at PSA Singapore"),
    ("In Transit — Indian Ocean", "2026-02-28 00:00", True, C_ACCENT, "Vessel position: 8°N 72°E — on schedule"),
    ("Suez Canal Transit", "2026-03-08 04:30", True, C_HIGH, "Northbound convoy — transit time 14 hrs"),
    ("Arrived Destination Port", "2026-03-15 07:00", False, C_MOD, "ETA Rotterdam — awaiting berth assignment"),
    ("Vessel Discharged", "2026-03-16 18:00", False, C_TEXT3, "Estimated — subject to terminal productivity"),
    ("Customs Cleared", "2026-03-17 12:00", False, C_TEXT3, "Pre-lodged entry — T1 document filed"),
    ("Gate Out / Delivered", "2026-03-18 10:00", False, C_TEXT3, "Final-mile trucking to Berlin DC"),
]

_CARRIER_RANKINGS = [
    ("Maersk (MSC Alliance)", 94, "A+", "Real-time AIS + Maersk Track portal + API", C_HIGH),
    ("MSC", 88, "A", "MSC Track with milestone alerts + vessel position", C_HIGH),
    ("CMA CGM", 85, "A-", "CMA CGM eBusiness + predictive ETA engine", C_HIGH),
    ("Hapag-Lloyd", 83, "B+", "Hapag-Lloyd online tracking + EDI milestones", C_HIGH),
    ("COSCO", 76, "B", "COSCO e-Tracking — AIS coverage gaps in port", C_MOD),
    ("Evergreen", 74, "B", "Evergreen Track — 6hr update cycle", C_MOD),
    ("ONE (Ocean Network Express)", 71, "B-", "ONE Track — limited predictive ETA", C_MOD),
    ("Yang Ming", 65, "C+", "YM Biz Track — milestone events only", C_MOD),
    ("HMM", 62, "C+", "HMM e-Service — 24hr update lag typical", C_MOD),
    ("ZIM", 58, "C", "ZIM Track — AIS not always linked to booking", C_LOW),
    ("PIL (Pacific Int'l Lines)", 49, "D+", "Manual status updates — no real-time AIS", C_LOW),
    ("Wan Hai", 44, "D", "Email-based updates only", C_LOW),
]


# ---------------------------------------------------------------------------
# Cell / content formatters
# ---------------------------------------------------------------------------

def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return f'<span style="font-family:var(--sans);color:{color};font-weight:{weight};">{value}</span>'


def _score_bar(score: int, color: str, width: int = 120) -> str:
    """Inline progress bar used as a cell content widget."""
    pct = max(0, min(100, score))
    # Outer container uses the global .progress-bar-custom class; only the
    # dynamic fill width is kept as an inline style on the class-based element
    # (permitted exception for data-driven width per-playbook step 4).
    return (
        f'<span class="progress-bar-custom" '
        f'style="display:inline-block;width:{width}px;vertical-align:middle;">'
        f'<span class="progress-bar-fill" style="width:{pct}%;background:{color};"></span>'
        f'</span>'
    )


def _score_cell(score: int, color: str) -> str:
    return f'<span style="color:{color};font-weight:700;">{score}%</span> {_score_bar(score, color)}'


def _overall_cell(score: int, color: str) -> str:
    return (
        f'<span style="font-size:16px;font-weight:800;color:{color};">{score}</span>'
        f'<span style="font-size:10px;color:{C_TEXT3};">/100</span>'
    )


def _grade_badge(grade: str, color: str) -> str:
    return badge(grade, color)


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_hero_kpis() -> None:
    try:
        metric_card_row(
            [
                {"label": "Shipments Tracked", "value": "2,847", "accent": C_ACCENT, "sublabel": "Total active shipments in system"},
                {"label": "On-Time Rate",      "value": "78.4%", "accent": C_HIGH,   "sublabel": "Delivered within original ETA window"},
                {"label": "In Transit",        "value": "1,203", "accent": C_ACCENT, "sublabel": "Vessels currently at sea"},
                {"label": "At Origin",         "value": "412",   "accent": C_MOD,    "sublabel": "Loaded, awaiting departure"},
                {"label": "At Destination",    "value": "384",   "accent": C_HIGH,   "sublabel": "Arrived, pending discharge/delivery"},
                {"label": "Delayed",           "value": "298",   "accent": C_LOW,    "sublabel": "ETA deviation >48 hours"},
            ],
            columns=6,
        )
        st.markdown(source_footer(_VIS_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"hero kpis error: {exc}")
        st.info("KPI data unavailable.")


def _render_pipeline() -> None:
    try:
        section_header("Shipment Pipeline", "Active shipments by stage — click through to carrier portal")

        col_colors = {
            "ORIGIN LOADED": C_MOD,
            "IN TRANSIT":    C_ACCENT,
            "CUSTOMS":       C_CONV,
            "AT PORT":       C_MACRO,
            "LAST MILE":     "#f97316",
            "DELIVERED":     C_HIGH,
        }

        cols = st.columns(6)
        for col, (stage, shipments) in zip(cols, _PIPELINE_DATA.items()):
            color = col_colors.get(stage, C_TEXT2)
            cards_html = ""
            for sid, route, teu in shipments:
                # port-card provides background/border/radius/padding via class;
                # <span style="color:..."> is the permitted content-level coloring.
                cards_html += (
                    f'<div class="port-card">'
                    f'<div class="port-name">{sid}</div>'
                    f'<div class="port-detail">{route}</div>'
                    f'<div class="port-detail"><span style="color:{color};">{teu}</span></div>'
                    f'</div>'
                )
            with col:
                st.html(
                    f'<div class="wsj-card">'
                    f'<div class="sub-section-header">'
                    f'<span style="color:{color};">{stage}</span>'
                    f'</div>'
                    f'<span class="kpi-value">{len(shipments)}</span>'
                    f'{cards_html}'
                    f'</div>'
                )
        st.markdown(source_footer(_VIS_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"pipeline error: {exc}")
        st.info("Pipeline data unavailable.")


def _render_visibility_scores() -> None:
    try:
        section_header("Visibility Score by Trade Lane", "AIS coverage × milestone reporting × predictive ETA quality")

        headers = ["Trade Lane", "AIS Coverage %", "Milestone Tracking %", "Predictive ETA %", "Overall Score"]
        rows = []
        for lane, ais, milestone, pred_eta, color in _VISIBILITY_LANES:
            overall = int((ais + milestone + pred_eta) / 3)
            rows.append([
                _sans(lane, weight=600),
                _score_cell(ais, color),
                _score_cell(milestone, color),
                _score_cell(pred_eta, color),
                _overall_cell(overall, color),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer(_VIS_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"visibility scores error: {exc}")
        st.info("Visibility score data unavailable.")


def _render_exception_management() -> None:
    try:
        section_header("Exception Management", "Active issues requiring attention — investigate and resolve")

        headers = ["Booking Ref", "Vessel", "Issue Type", "Duration", "Location", "Detail"]
        rows = []
        for ref, vessel, issue, duration, location, color_name, detail in _EXCEPTIONS:
            severity_color = C_LOW if color_name == "red" else C_MOD
            rows.append([
                _sans(ref, weight=700),
                _sans(vessel, color=C_TEXT2),
                badge(issue, severity_color),
                _mono(duration, color=severity_color, weight=700),
                _sans(location, color=C_TEXT2),
                _sans(detail, color=C_TEXT3),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer(_VIS_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"exception mgmt error: {exc}")
        st.info("Exception data unavailable.")


def _render_milestone_tracking() -> None:
    try:
        section_header(
            "Milestone Tracking",
            "Sample shipment MAEU-2847561 — Shanghai → Rotterdam (MV Maersk Edmonton, Voy. 026W)",
        )

        completed_count = sum(1 for _, _, done, _, _ in _MILESTONE_STEPS if done)
        total = len(_MILESTONE_STEPS)
        pct = int(completed_count / total * 100)

        # Journey-progress KPI row (replaces inline progress card)
        metric_card_row(
            [
                {"label": "Milestones Complete", "value": f"{completed_count}/{total}", "accent": C_HIGH,
                 "sublabel": f"{pct}% of journey"},
                {"label": "Current Stage", "value": "Indian Ocean", "accent": C_ACCENT,
                 "sublabel": "In transit — vessel on schedule"},
                {"label": "Next Milestone", "value": "Arrive Rotterdam", "accent": C_MOD,
                 "sublabel": "ETA 2026-03-15 07:00"},
            ],
            columns=3,
        )

        # Milestone timeline rendered as a WSJ market table; each row uses
        # _mono/_sans content spans for per-row coloring.
        milestone_rows = []
        for name, ts, done, color, note in _MILESTONE_STEPS:
            status_color = color if done else C_TEXT3
            status_text  = "✓ Done" if done else "Pending"
            milestone_rows.append([
                badge(status_text, status_color),
                _sans(name,  color=status_color, weight=700),
                _mono(ts,    color=C_TEXT3),
                _sans(note,  color=C_TEXT3),
            ])
        wsj_market_table(
            headers=["Status", "Milestone", "Timestamp", "Detail"],
            rows=milestone_rows,
        )
        st.markdown(source_footer(_VIS_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"milestone tracking error: {exc}")
        st.info("Milestone data unavailable.")


def _render_carrier_rankings() -> None:
    try:
        section_header(
            "Carrier Digital Visibility Rankings",
            "Scored 0-100 across AIS, milestone, and predictive ETA capabilities",
        )

        headers = ["#", "Carrier", "Visibility Score", "Grade", "Capabilities"]
        rows = []
        for i, (carrier, score, grade, caps, color) in enumerate(_CARRIER_RANKINGS):
            rank_color = [C_MOD, C_TEXT2, C_CONV][min(i, 2)] if i < 3 else C_TEXT3
            rows.append([
                _mono(str(i + 1), color=rank_color, weight=800),
                _sans(carrier, weight=700),
                _score_cell(score, color),
                _grade_badge(grade, color),
                _sans(caps, color=C_TEXT3),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer(_VIS_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"carrier rankings error: {exc}")
        st.info("Carrier ranking data unavailable.")


def _render_visibility_chart() -> None:
    try:
        lanes = [r[0].split(" (")[0] for r in _VISIBILITY_LANES]
        ais_vals = [r[1] for r in _VISIBILITY_LANES]
        ms_vals = [r[2] for r in _VISIBILITY_LANES]
        pred_vals = [r[3] for r in _VISIBILITY_LANES]

        fig = go.Figure()
        fig.add_trace(go.Bar(name="AIS Coverage",       x=lanes, y=ais_vals,  marker_color=C_ACCENT, opacity=0.85))
        fig.add_trace(go.Bar(name="Milestone Tracking", x=lanes, y=ms_vals,   marker_color=C_HIGH,   opacity=0.85))
        fig.add_trace(go.Bar(name="Predictive ETA",     x=lanes, y=pred_vals, marker_color=C_MOD,    opacity=0.85))

        apply_dark_layout(fig, title="Visibility Scores by Trade Lane", height=320)
        fig.update_layout(
            margin=dict(l=10, r=10, t=46, b=80),
            barmode="group",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                font=dict(color=C_TEXT2), bgcolor="rgba(0,0,0,0)",
            ),
            xaxis=dict(tickangle=-30),
            yaxis=dict(range=[0, 100], title="Score (%)"),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(source_footer(_VIS_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"visibility chart error: {exc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render(port_results=None, route_results=None, insights=None) -> None:
    """Render the Supply Chain Visibility & Tracking tab."""
    try:
        page_header(
            title="Supply Chain Visibility & Tracking",
            subtitle="Real-time shipment pipeline · AIS monitoring · Exception management · Milestone tracking · Carrier benchmarking",
            badge_text="VISIBILITY",
            badge_color=C_ACCENT,
        )
    except Exception as exc:
        logger.warning(f"header error: {exc}")

    _render_hero_kpis()
    _render_pipeline()

    section_divider()

    col_left, col_right = st.columns([3, 2])
    with col_left:
        _render_visibility_scores()
    with col_right:
        try:
            _render_visibility_chart()
        except Exception as exc:
            logger.warning(f"chart col error: {exc}")

    section_divider()

    _render_exception_management()

    section_divider()

    col_a, col_b = st.columns([2, 3])
    with col_a:
        _render_carrier_rankings()
    with col_b:
        _render_milestone_tracking()
