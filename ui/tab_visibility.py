"""tab_visibility.py — Supply Chain Visibility & Tracking tab.

Renders shipment pipeline, visibility scores, exception management,
milestone tracking, and carrier visibility rankings.
"""
from __future__ import annotations

import random
from typing import Any

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ---------------------------------------------------------------------------
# Colour palette
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
C_PURPLE  = "#8b5cf6"
C_CYAN    = "#06b6d4"

# ---------------------------------------------------------------------------
# Static data helpers
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
    ("MSCU-3847261", "MSC Zoe", "AIS Signal Lost", "48 hrs", "North Atlantic", C_LOW, "Vessel went dark — signal lost near 42°N 28°W"),
    ("OOLU-1029384", "OOCL Europe", "Customs Hold", "72 hrs", "Rotterdam", C_MOD, "Documentary discrepancy — phytosanitary cert missing"),
    ("HLCU-8827364", "Hapag Express", "Port Denial", "96 hrs", "Long Beach", C_LOW, "Terminal congestion — vessel diverted to Oakland"),
    ("CMAU-2038471", "CMA CGM Marco Polo", "Carrier Change", "24 hrs", "Singapore", C_MOD, "Alliance swap — cargo rolled to next sailing"),
    ("EVGU-4482019", "Ever Given II", "ETA Deviation >5d", "120 hrs", "Suez Canal", C_LOW, "Route change via Cape of Good Hope"),
    ("MAEU-9918374", "Maersk Elba", "Reefer Alert", "6 hrs", "In Transit", C_MOD, "Temperature excursion logged — monitoring active"),
    ("ZIMU-3748201", "ZIM Pacific", "AIS Signal Lost", "18 hrs", "Red Sea", C_LOW, "Security zone — AIS intentionally disabled"),
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


def _score_bar(score: int, color: str, width: int = 120) -> str:
    """Return an inline HTML progress bar."""
    pct = max(0, min(100, score))
    return (
        f'<div style="background:{C_SURFACE};border-radius:4px;height:8px;width:{width}px;display:inline-block;vertical-align:middle;">'
        f'<div style="background:{color};width:{pct}%;height:100%;border-radius:4px;"></div>'
        f'</div>'
    )


def _grade_badge(grade: str, color: str) -> str:
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color}44;'
        f'border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700;">{grade}</span>'
    )


def _status_dot(color: str) -> str:
    return f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};margin-right:6px;"></span>'


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_hero_kpis() -> None:
    try:
        kpis = [
            ("Shipments Tracked", "2,847", C_ACCENT, "Total active shipments in system"),
            ("On-Time Rate", "78.4%", C_HIGH, "Delivered within original ETA window"),
            ("In Transit", "1,203", C_ACCENT, "Vessels currently at sea"),
            ("At Origin", "412", C_MOD, "Loaded, awaiting departure"),
            ("At Destination", "384", C_HIGH, "Arrived, pending discharge/delivery"),
            ("Delayed", "298", C_LOW, "ETA deviation >48 hours"),
        ]
        cols = st.columns(6)
        for col, (label, value, color, tip) in zip(cols, kpis):
            with col:
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-top:3px solid {color};'
                    f'border-radius:10px;padding:18px 14px;text-align:center;">'
                    f'<div style="font-size:26px;font-weight:800;color:{color};">{value}</div>'
                    f'<div style="font-size:11px;color:{C_TEXT2};margin-top:4px;font-weight:600;">{label}</div>'
                    f'<div style="font-size:10px;color:{C_TEXT3};margin-top:3px;">{tip}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        logger.warning(f"hero kpis error: {exc}")
        st.info("KPI data unavailable.")


def _render_pipeline() -> None:
    try:
        st.markdown(
            f'<div style="font-size:16px;font-weight:700;color:{C_TEXT};margin:24px 0 12px;">Shipment Pipeline</div>',
            unsafe_allow_html=True,
        )

        col_colors = {
            "ORIGIN LOADED": C_MOD,
            "IN TRANSIT": C_ACCENT,
            "CUSTOMS": C_PURPLE,
            "AT PORT": C_CYAN,
            "LAST MILE": "#f97316",
            "DELIVERED": C_HIGH,
        }

        cols = st.columns(6)
        for col, (stage, shipments) in zip(cols, _PIPELINE_DATA.items()):
            color = col_colors.get(stage, C_TEXT2)
            cards_html = ""
            for sid, route, teu in shipments:
                cards_html += (
                    f'<div style="background:{C_SURFACE};border-radius:6px;padding:8px 10px;margin-bottom:6px;'
                    f'border-left:3px solid {color};">'
                    f'<div style="font-size:10px;color:{C_TEXT};font-weight:700;">{sid}</div>'
                    f'<div style="font-size:9px;color:{C_TEXT2};margin-top:2px;">{route}</div>'
                    f'<div style="font-size:9px;color:{color};margin-top:2px;">{teu}</div>'
                    f'</div>'
                )
            with col:
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:12px;">'
                    f'<div style="font-size:10px;font-weight:800;color:{color};letter-spacing:0.08em;margin-bottom:6px;">{stage}</div>'
                    f'<div style="font-size:20px;font-weight:800;color:{C_TEXT};margin-bottom:10px;">{len(shipments)}</div>'
                    f'{cards_html}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        logger.warning(f"pipeline error: {exc}")
        st.info("Pipeline data unavailable.")


def _render_visibility_scores() -> None:
    try:
        st.markdown(
            f'<div style="font-size:16px;font-weight:700;color:{C_TEXT};margin:24px 0 12px;">Visibility Score by Trade Lane</div>',
            unsafe_allow_html=True,
        )

        header = (
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;overflow:hidden;">'
            f'<table style="width:100%;border-collapse:collapse;font-size:12px;">'
            f'<thead><tr style="border-bottom:1px solid {C_BORDER};">'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Trade Lane</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">AIS Coverage %</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Milestone Tracking %</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Predictive ETA %</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Overall Score</th>'
            f'</tr></thead><tbody>'
        )

        rows = ""
        for i, (lane, ais, milestone, pred_eta, color) in enumerate(_VISIBILITY_LANES):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            overall = int((ais + milestone + pred_eta) / 3)
            rows += (
                f'<tr style="background:{bg};border-bottom:1px solid {C_BORDER};">'
                f'<td style="padding:10px 14px;color:{C_TEXT};font-weight:600;">{lane}</td>'
                f'<td style="padding:10px 14px;text-align:center;">'
                f'<span style="color:{color};font-weight:700;">{ais}%</span> '
                f'{_score_bar(ais, color)}</td>'
                f'<td style="padding:10px 14px;text-align:center;">'
                f'<span style="color:{color};font-weight:700;">{milestone}%</span> '
                f'{_score_bar(milestone, color)}</td>'
                f'<td style="padding:10px 14px;text-align:center;">'
                f'<span style="color:{color};font-weight:700;">{pred_eta}%</span> '
                f'{_score_bar(pred_eta, color)}</td>'
                f'<td style="padding:10px 14px;text-align:center;">'
                f'<span style="font-size:16px;font-weight:800;color:{color};">{overall}</span>'
                f'<span style="font-size:10px;color:{C_TEXT3};">/100</span></td>'
                f'</tr>'
            )

        st.markdown(header + rows + "</tbody></table></div>", unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"visibility scores error: {exc}")
        st.info("Visibility score data unavailable.")


def _render_exception_management() -> None:
    try:
        st.markdown(
            f'<div style="font-size:16px;font-weight:700;color:{C_TEXT};margin:24px 0 12px;">Exception Management</div>',
            unsafe_allow_html=True,
        )

        header = (
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;overflow:hidden;">'
            f'<table style="width:100%;border-collapse:collapse;font-size:12px;">'
            f'<thead><tr style="border-bottom:1px solid {C_BORDER};">'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Booking Ref</th>'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Vessel</th>'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Issue Type</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Duration</th>'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Location</th>'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Detail</th>'
            f'</tr></thead><tbody>'
        )

        rows = ""
        for i, (ref, vessel, issue, duration, location, color, detail) in enumerate(_EXCEPTIONS):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            rows += (
                f'<tr style="background:{bg};border-bottom:1px solid {C_BORDER};">'
                f'<td style="padding:10px 14px;color:{C_TEXT};font-weight:700;">{ref}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT2};">{vessel}</td>'
                f'<td style="padding:10px 14px;">'
                f'<span style="background:{color}22;color:{color};border:1px solid {color}44;'
                f'border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700;">{issue}</span></td>'
                f'<td style="padding:10px 14px;text-align:center;color:{color};font-weight:700;">{duration}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT2};">{location}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT3};font-size:11px;">{detail}</td>'
                f'</tr>'
            )

        st.markdown(header + rows + "</tbody></table></div>", unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"exception mgmt error: {exc}")
        st.info("Exception data unavailable.")


def _render_milestone_tracking() -> None:
    try:
        st.markdown(
            f'<div style="font-size:16px;font-weight:700;color:{C_TEXT};margin:24px 0 4px;">Milestone Tracking</div>'
            f'<div style="font-size:12px;color:{C_TEXT2};margin-bottom:14px;">Sample shipment MAEU-2847561 — Shanghai → Rotterdam (MV Maersk Edmonton, Voy. 026W)</div>',
            unsafe_allow_html=True,
        )

        completed_count = sum(1 for _, _, done, _, _ in _MILESTONE_STEPS if done)
        total = len(_MILESTONE_STEPS)
        pct = int(completed_count / total * 100)

        # Progress bar
        st.markdown(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;padding:16px 20px;margin-bottom:14px;">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:8px;">'
            f'<span style="font-size:12px;color:{C_TEXT2};">Journey Progress</span>'
            f'<span style="font-size:12px;color:{C_HIGH};font-weight:700;">{completed_count}/{total} milestones complete ({pct}%)</span>'
            f'</div>'
            f'<div style="background:{C_CARD};border-radius:6px;height:12px;">'
            f'<div style="background:linear-gradient(90deg,{C_ACCENT},{C_HIGH});width:{pct}%;height:100%;border-radius:6px;"></div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Timeline
        timeline_html = f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;padding:20px 24px;">'
        for i, (name, ts, done, color, note) in enumerate(_MILESTONE_STEPS):
            is_last = i == len(_MILESTONE_STEPS) - 1
            connector = "" if is_last else (
                f'<div style="width:2px;height:28px;background:{"linear-gradient(180deg," + color + "," + C_TEXT3 + ")" if done else C_TEXT3};'
                f'margin-left:11px;"></div>'
            )
            dot_style = (
                f'width:24px;height:24px;border-radius:50%;background:{color};'
                f'display:flex;align-items:center;justify-content:center;flex-shrink:0;'
            ) if done else (
                f'width:24px;height:24px;border-radius:50%;border:2px solid {C_TEXT3};'
                f'background:{C_CARD};flex-shrink:0;'
            )
            checkmark = '<span style="color:#fff;font-size:11px;font-weight:900;">✓</span>' if done else ""
            timeline_html += (
                f'<div style="display:flex;align-items:flex-start;gap:14px;">'
                f'<div>'
                f'<div style="{dot_style}">{checkmark}</div>'
                f'{connector}'
                f'</div>'
                f'<div style="padding-bottom:8px;flex:1;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="font-size:13px;font-weight:700;color:{color if done else C_TEXT2};">{name}</span>'
                f'<span style="font-size:11px;color:{C_TEXT3};">{ts}</span>'
                f'</div>'
                f'<div style="font-size:11px;color:{C_TEXT3};margin-top:2px;">{note}</div>'
                f'</div>'
                f'</div>'
            )
        timeline_html += "</div>"
        st.markdown(timeline_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"milestone tracking error: {exc}")
        st.info("Milestone data unavailable.")


def _render_carrier_rankings() -> None:
    try:
        st.markdown(
            f'<div style="font-size:16px;font-weight:700;color:{C_TEXT};margin:24px 0 12px;">Carrier Digital Visibility Rankings</div>',
            unsafe_allow_html=True,
        )

        header = (
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;overflow:hidden;">'
            f'<table style="width:100%;border-collapse:collapse;font-size:12px;">'
            f'<thead><tr style="border-bottom:1px solid {C_BORDER};">'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">#</th>'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Carrier</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Visibility Score</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Grade</th>'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Capabilities</th>'
            f'</tr></thead><tbody>'
        )

        rows = ""
        for i, (carrier, score, grade, caps, color) in enumerate(_CARRIER_RANKINGS):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            rank_color = [C_MOD, C_TEXT2, C_PURPLE][min(i, 2)] if i < 3 else C_TEXT3
            rows += (
                f'<tr style="background:{bg};border-bottom:1px solid {C_BORDER};">'
                f'<td style="padding:10px 14px;color:{rank_color};font-weight:800;">{i+1}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT};font-weight:700;">{carrier}</td>'
                f'<td style="padding:10px 14px;text-align:center;">'
                f'<span style="font-size:16px;font-weight:800;color:{color};">{score}</span>'
                f'<span style="font-size:10px;color:{C_TEXT3};">/100</span> '
                f'{_score_bar(score, color)}</td>'
                f'<td style="padding:10px 14px;text-align:center;">{_grade_badge(grade, color)}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT3};font-size:11px;">{caps}</td>'
                f'</tr>'
            )

        st.markdown(header + rows + "</tbody></table></div>", unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"carrier rankings error: {exc}")
        st.info("Carrier ranking data unavailable.")


def _render_visibility_chart() -> None:
    """Radar / bar chart: AIS vs Milestone vs Predictive across top lanes."""
    try:
        lanes = [r[0].split(" (")[0] for r in _VISIBILITY_LANES]
        ais_vals = [r[1] for r in _VISIBILITY_LANES]
        ms_vals = [r[2] for r in _VISIBILITY_LANES]
        pred_vals = [r[3] for r in _VISIBILITY_LANES]

        fig = go.Figure()
        fig.add_trace(go.Bar(name="AIS Coverage", x=lanes, y=ais_vals, marker_color=C_ACCENT, opacity=0.85))
        fig.add_trace(go.Bar(name="Milestone Tracking", x=lanes, y=ms_vals, marker_color=C_HIGH, opacity=0.85))
        fig.add_trace(go.Bar(name="Predictive ETA", x=lanes, y=pred_vals, marker_color=C_MOD, opacity=0.85))

        fig.update_layout(
            barmode="group",
            paper_bgcolor=C_CARD,
            plot_bgcolor=C_CARD,
            font=dict(color=C_TEXT2, size=11),
            margin=dict(l=10, r=10, t=30, b=80),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                        font=dict(color=C_TEXT2), bgcolor="rgba(0,0,0,0)"),
            xaxis=dict(gridcolor=C_BORDER, tickangle=-30),
            yaxis=dict(gridcolor=C_BORDER, range=[0, 100], title="Score (%)"),
            height=320,
            title=dict(text="Visibility Scores by Trade Lane", font=dict(color=C_TEXT, size=13)),
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception as exc:
        logger.warning(f"visibility chart error: {exc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render(port_results=None, route_results=None, insights=None) -> None:
    """Render the Supply Chain Visibility & Tracking tab."""
    try:
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{C_CARD},{C_SURFACE});'
            f'border:1px solid {C_BORDER};border-radius:12px;padding:20px 24px;margin-bottom:20px;">'
            f'<div style="font-size:22px;font-weight:800;color:{C_TEXT};">Supply Chain Visibility & Tracking</div>'
            f'<div style="font-size:13px;color:{C_TEXT2};margin-top:4px;">'
            f'Real-time shipment pipeline · AIS monitoring · Exception management · Milestone tracking · Carrier benchmarking'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"header error: {exc}")

    _render_hero_kpis()
    _render_pipeline()

    st.markdown(
        f'<div style="height:1px;background:{C_BORDER};margin:28px 0;"></div>',
        unsafe_allow_html=True,
    )

    col_left, col_right = st.columns([3, 2])
    with col_left:
        _render_visibility_scores()
    with col_right:
        try:
            _render_visibility_chart()
        except Exception as exc:
            logger.warning(f"chart col error: {exc}")

    st.markdown(
        f'<div style="height:1px;background:{C_BORDER};margin:28px 0;"></div>',
        unsafe_allow_html=True,
    )

    _render_exception_management()

    st.markdown(
        f'<div style="height:1px;background:{C_BORDER};margin:28px 0;"></div>',
        unsafe_allow_html=True,
    )

    col_a, col_b = st.columns([2, 3])
    with col_a:
        _render_carrier_rankings()
    with col_b:
        _render_milestone_tracking()

    try:
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
            f'padding:14px 18px;margin-top:28px;font-size:11px;color:{C_TEXT3};">'
            f'Data refreshed every 15 minutes from AIS feeds, carrier APIs, and port EDI streams. '
            f'Visibility scores calculated as rolling 30-day averages. '
            f'Exception alerts generated when deviations exceed configured thresholds.'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"footer error: {exc}")
