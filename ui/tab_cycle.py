"""tab_cycle.py — Shipping Market Cycle Positioning tab.

Identifies where we are in the shipping cycle and surfaces
cycle-based trade recommendations.

Sections:
  1. Cycle Dashboard          — current phase with large text + historical context
  2. Cycle Clock              — pure-CSS clock face showing cycle position
  3. Cycle Indicator Table    — 10 indicators with cycle signal + composite score
  4. Historical Cycle Map     — BDI 2000-2025 with shaded phase regions
  5. Cycle-Based Trade Recs   — what to buy/sell in each phase
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ── Design constants ─────────────────────────────────────────────────────────
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

# Phase palette
_PHASE_COLOR = {
    "TROUGH":      "#ef4444",
    "RECOVERY":    "#3b82f6",
    "EXPANSION":   "#10b981",
    "PEAK":        "#f59e0b",
    "CONTRACTION": "#f97316",
}

_PHASE_DESC = {
    "TROUGH":      "Freight rates at cycle lows. Prime accumulation window for quality names.",
    "RECOVERY":    "Rates recovering. Demand exceeding supply. Earnings upgrades incoming.",
    "EXPANSION":   "Sustained rate strength. Orderbook filling. Maximise long exposure.",
    "PEAK":        "Rates elevated but momentum fading. Newbuilds on order. Reduce risk.",
    "CONTRACTION": "Rates declining. Oversupply emerging. Reduce longs, add hedges.",
}

_PHASE_HIST = {
    "TROUGH":      "Last seen: Dec 2015 – Mar 2016 (BDI 290). Avg duration: 4–8 months.",
    "RECOVERY":    "Last seen: Apr 2020 – Dec 2020. Avg duration: 6–12 months.",
    "EXPANSION":   "Last seen: Jan 2021 – Aug 2021. Avg duration: 8–18 months.",
    "PEAK":        "Last seen: Oct 2021 (BDI 5,650). Avg duration: 2–6 months.",
    "CONTRACTION": "Last seen: Sep 2022 – Mar 2023. Avg duration: 6–12 months.",
}

# Clock-face angles: 12 o'clock = PEAK (0°), clockwise
# PEAK=0°, CONTRACTION=90°, TROUGH=180°, RECOVERY=270°
_PHASE_ANGLE = {
    "PEAK":        0,
    "CONTRACTION": 90,
    "TROUGH":      180,
    "RECOVERY":    270,
    "EXPANSION":   315,
}

_CHART_LAYOUT = dict(
    paper_bgcolor=C_SURFACE,
    plot_bgcolor=C_SURFACE,
    font=dict(family="monospace", color=C_TEXT2, size=11),
    margin=dict(l=48, r=24, t=40, b=40),
)


# ── Current cycle state (deterministic, replace with live engine when wired) ──

def _current_phase() -> str:
    return "RECOVERY"


def _cycle_position_score() -> float:
    """Returns 0.0 (trough) to 1.0 (peak), current estimated position."""
    return 0.35  # recovery territory


# ── Cycle indicator data ──────────────────────────────────────────────────────

def _build_indicators() -> pd.DataFrame:
    rows = [
        ("BDI Trend",              "+18% MoM",   "RECOVERY",    0.15, 72),
        ("Fleet Utilization",      "87.4%",       "EXPANSION",   0.12, 80),
        ("Newbuild Orders",        "12% of fleet","CONTRACTION", 0.10, 35),
        ("Scrapping Rate",         "0.8% pa",     "TROUGH",      0.08, 20),
        ("Freight Rate Momentum",  "+11% QoQ",    "RECOVERY",    0.14, 68),
        ("Carrier Profitability",  "EBITDA +22%", "EXPANSION",   0.12, 78),
        ("Port Congestion",        "Moderate",    "RECOVERY",    0.08, 55),
        ("Charter Rates (1Y TC)",  "$18,400/d",   "RECOVERY",    0.11, 62),
        ("Bond Spreads (IG Ship)", "+145 bps",    "CONTRACTION", 0.06, 38),
        ("PMI Trend (Global)",     "51.2",        "RECOVERY",    0.04, 60),
    ]
    return pd.DataFrame(
        rows,
        columns=["Indicator", "Current Reading", "Cycle Signal", "Weight", "Score"],
    )


# ── BDI historical data ───────────────────────────────────────────────────────

def _build_bdi_history() -> pd.DataFrame:
    rng = np.random.default_rng(99)
    years = np.arange(2000, 2026)
    # Approximate annual BDI peaks for realism
    bdi_anchor = {
        2000: 1200, 2001: 900,  2002: 1100, 2003: 1800, 2004: 4500,
        2005: 3000, 2006: 3200, 2007: 7200, 2008: 11793,2009: 1770,
        2010: 2758,2011: 1549, 2012: 700,  2013: 1200, 2014: 1000,
        2015: 550,  2016: 290,  2017: 1300, 2018: 1250, 2019: 2100,
        2020: 1400, 2021: 5650, 2022: 2100, 2023: 1500, 2024: 1800,
        2025: 2100,
    }
    dates = pd.date_range("2000-01-01", "2025-12-31", freq="MS")
    bdi_vals = []
    for d in dates:
        base = bdi_anchor.get(d.year, 1500)
        noise = rng.normal(0, base * 0.08)
        bdi_vals.append(max(200, base + noise))
    return pd.DataFrame({"Date": dates, "BDI": bdi_vals})


_CYCLE_PHASES_HIST = [
    # (start, end, phase_label, color_key)
    ("2000-01", "2003-12", "RECOVERY",    "rgba(59,130,246,0.15)"),
    ("2004-01", "2008-09", "EXPANSION",   "rgba(16,185,129,0.15)"),
    ("2008-10", "2009-06", "CONTRACTION", "rgba(249,115,22,0.18)"),
    ("2009-07", "2010-12", "RECOVERY",    "rgba(59,130,246,0.15)"),
    ("2011-01", "2016-02", "CONTRACTION", "rgba(249,115,22,0.18)"),
    ("2016-03", "2016-12", "TROUGH",      "rgba(239,68,68,0.18)"),
    ("2017-01", "2019-12", "RECOVERY",    "rgba(59,130,246,0.15)"),
    ("2020-01", "2020-05", "TROUGH",      "rgba(239,68,68,0.18)"),
    ("2020-06", "2021-10", "EXPANSION",   "rgba(16,185,129,0.15)"),
    ("2021-11", "2021-12", "PEAK",        "rgba(245,158,11,0.18)"),
    ("2022-01", "2023-06", "CONTRACTION", "rgba(249,115,22,0.18)"),
    ("2023-07", "2025-12", "RECOVERY",    "rgba(59,130,246,0.15)"),
]


# ── Section renderers ─────────────────────────────────────────────────────────

def _section_header(title: str, subtitle: str = "") -> None:
    sub = (
        f'<span style="color:{C_TEXT3};font-size:11px;margin-left:10px;">'
        f'{subtitle}</span>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="margin:24px 0 10px;padding-bottom:8px;'
        f'border-bottom:1px solid {C_BORDER};">'
        f'<span style="color:{C_TEXT};font-size:14px;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:1px;">{title}</span>'
        f'{sub}</div>',
        unsafe_allow_html=True,
    )


def _render_cycle_dashboard(phase: str) -> None:
    try:
        color   = _PHASE_COLOR.get(phase, C_ACCENT)
        desc    = _PHASE_DESC.get(phase, "")
        hist    = _PHASE_HIST.get(phase, "")

        # Context pills for each phase
        all_phases = ["TROUGH", "RECOVERY", "EXPANSION", "PEAK", "CONTRACTION"]
        pills_html = ""
        for p in all_phases:
            pc     = _PHASE_COLOR.get(p, C_TEXT3)
            active = p == phase
            bg     = f"{pc}30" if active else "transparent"
            border = pc if active else C_BORDER
            fw     = "700" if active else "400"
            pills_html += (
                f'<span style="background:{bg};border:1px solid {border};'
                f'color:{pc};padding:4px 14px;border-radius:20px;font-size:11px;'
                f'font-weight:{fw};letter-spacing:1px;white-space:nowrap;">'
                f'{p}</span>'
            )

        html = (
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-left:5px solid {color};border-radius:10px;padding:24px 28px;'
            f'margin-bottom:20px;">'
            f'<div style="color:{C_TEXT3};font-size:11px;text-transform:uppercase;'
            f'letter-spacing:2px;margin-bottom:8px;">Current Cycle Phase</div>'
            f'<div style="color:{color};font-size:52px;font-weight:900;'
            f'font-family:monospace;letter-spacing:2px;line-height:1;">{phase}</div>'
            f'<div style="color:{C_TEXT2};font-size:13px;margin-top:12px;'
            f'max-width:600px;">{desc}</div>'
            f'<div style="color:{C_TEXT3};font-size:11px;margin-top:8px;">'
            f'{hist}</div>'
            f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:18px;">'
            f'{pills_html}'
            f'</div>'
            f'</div>'
        )
        st.markdown(html, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Cycle dashboard unavailable: {exc}")


def _render_cycle_clock(phase: str, position_score: float) -> None:
    """Pure-CSS clock face. No JS. Uses positioned div elements."""
    try:
        color   = _PHASE_COLOR.get(phase, C_ACCENT)
        angle_d = _PHASE_ANGLE.get(phase, 0)
        angle_r = math.radians(angle_d)

        # Hand tip coordinates (clock radius 90px from center 110,110)
        cx, cy    = 110, 110
        hand_len  = 78
        tip_x     = cx + hand_len * math.sin(angle_r)
        tip_y     = cy - hand_len * math.cos(angle_r)

        # Phase label positions (outer ring, radius ~96)
        label_radius = 96
        phase_labels = {
            "PEAK":        (0,   "#f59e0b"),
            "CONTRACTION": (90,  "#f97316"),
            "TROUGH":      (180, "#ef4444"),
            "RECOVERY":    (270, "#3b82f6"),
        }
        labels_html = ""
        for lbl, (ang, lc) in phase_labels.items():
            ar   = math.radians(ang)
            lx   = cx + label_radius * math.sin(ar)
            ly   = cy - label_radius * math.cos(ar)
            fw   = "700" if lbl == phase else "400"
            op   = "1" if lbl == phase else "0.5"
            size = "10" if lbl != phase else "11"
            labels_html += (
                f'<div style="position:absolute;left:{lx:.1f}px;top:{ly:.1f}px;'
                f'transform:translate(-50%,-50%);color:{lc};font-size:{size}px;'
                f'font-weight:{fw};opacity:{op};letter-spacing:0.5px;'
                f'text-align:center;white-space:nowrap;">{lbl}</div>'
            )

        # Tick marks (12 ticks for months)
        ticks_html = ""
        for i in range(12):
            ta    = math.radians(i * 30)
            r_in  = 68
            r_out = 76
            tx1   = cx + r_in  * math.sin(ta)
            ty1   = cy - r_in  * math.cos(ta)
            tx2   = cx + r_out * math.sin(ta)
            ty2   = cy - r_out * math.cos(ta)
            ticks_html += (
                f'<div style="position:absolute;left:{tx1:.1f}px;top:{ty1:.1f}px;'
                f'width:{abs(tx2-tx1)+1:.1f}px;height:2px;background:{C_BORDER};'
                f'transform-origin:0 50%;'
                f'transform:rotate({i*30}deg);"></div>'
            )

        html = (
            f'<div style="display:flex;align-items:center;gap:32px;'
            f'flex-wrap:wrap;margin-bottom:20px;">'
            # Clock face
            f'<div style="position:relative;width:220px;height:220px;'
            f'flex-shrink:0;">'
            # Outer ring
            f'<div style="position:absolute;left:0;top:0;width:220px;height:220px;'
            f'border-radius:50%;border:2px solid {C_BORDER};'
            f'background:{C_CARD};"></div>'
            # Inner ring
            f'<div style="position:absolute;left:30px;top:30px;width:160px;height:160px;'
            f'border-radius:50%;border:1px solid {C_BORDER};"></div>'
            # Tick marks (SVG line via thin divs — kept simple)
            f'<svg style="position:absolute;left:0;top:0;" width="220" height="220">'
            + "".join(
                f'<line x1="{cx + 68*math.sin(math.radians(i*30)):.1f}" '
                f'y1="{cy - 68*math.cos(math.radians(i*30)):.1f}" '
                f'x2="{cx + 78*math.sin(math.radians(i*30)):.1f}" '
                f'y2="{cy - 78*math.cos(math.radians(i*30)):.1f}" '
                f'stroke="{C_BORDER}" stroke-width="1.5"/>'
                for i in range(12)
            )
            # Clock hand (SVG line)
            + f'<line x1="{cx}" y1="{cy}" x2="{tip_x:.1f}" y2="{tip_y:.1f}" '
            f'stroke="{color}" stroke-width="3" stroke-linecap="round"/>'
            # Center dot
            + f'<circle cx="{cx}" cy="{cy}" r="5" fill="{color}"/>'
            f'</svg>'
            # Phase labels
            f'{labels_html}'
            f'</div>'
            # Legend text
            f'<div>'
            f'<div style="color:{C_TEXT3};font-size:11px;text-transform:uppercase;'
            f'letter-spacing:1.5px;margin-bottom:8px;">Cycle Clock</div>'
            f'<div style="color:{color};font-size:26px;font-weight:800;'
            f'font-family:monospace;">{phase}</div>'
            f'<div style="color:{C_TEXT3};font-size:12px;margin-top:6px;">'
            f'12 o\'clock = PEAK &nbsp;|&nbsp; 6 o\'clock = TROUGH</div>'
            f'<div style="color:{C_TEXT3};font-size:12px;margin-top:4px;">'
            f'Cycle position score: '
            f'<span style="color:{C_TEXT2};font-family:monospace;">'
            f'{position_score:.0%}</span> of peak</div>'
            f'<div style="margin-top:14px;background:{C_CARD};border-radius:6px;'
            f'height:8px;width:180px;overflow:hidden;">'
            f'<div style="background:{color};width:{position_score*100:.0f}%;'
            f'height:100%;border-radius:6px;"></div>'
            f'</div>'
            f'<div style="color:{C_TEXT3};font-size:10px;margin-top:4px;">'
            f'TROUGH &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;'
            f'&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;PEAK</div>'
            f'</div>'
            f'</div>'
        )
        st.markdown(html, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Cycle clock unavailable: {exc}")


def _render_indicator_table(df: pd.DataFrame) -> None:
    try:
        composite = (df["Score"] * df["Weight"]).sum() / df["Weight"].sum()

        def sig_badge(signal: str) -> str:
            color = _PHASE_COLOR.get(signal, C_TEXT3)
            return (
                f'<span style="background:{color}22;color:{color};'
                f'padding:2px 9px;border-radius:4px;font-size:10px;'
                f'font-weight:600;letter-spacing:0.5px;">{signal}</span>'
            )

        def score_bar(score: float) -> str:
            pct   = max(0, min(100, score))
            color = C_HIGH if pct >= 65 else (C_MOD if pct >= 40 else C_LOW)
            return (
                f'<div style="display:flex;align-items:center;gap:8px;">'
                f'<div style="background:{C_CARD};border-radius:4px;height:6px;'
                f'width:60px;overflow:hidden;">'
                f'<div style="background:{color};width:{pct:.0f}%;height:100%;'
                f'border-radius:4px;"></div></div>'
                f'<span style="color:{C_TEXT2};font-size:11px;font-family:monospace;">'
                f'{pct:.0f}</span>'
                f'</div>'
            )

        rows_html = ""
        for i, (_, row) in enumerate(df.iterrows()):
            bg = C_CARD if i % 2 == 0 else "transparent"
            rows_html += (
                f'<tr style="background:{bg};border-bottom:1px solid {C_BORDER};">'
                f'<td style="padding:10px 12px;color:{C_TEXT};font-size:12px;">'
                f'{row["Indicator"]}</td>'
                f'<td style="padding:10px 12px;color:{C_TEXT2};font-size:12px;'
                f'font-family:monospace;">{row["Current Reading"]}</td>'
                f'<td style="padding:10px 12px;">{sig_badge(row["Cycle Signal"])}</td>'
                f'<td style="padding:10px 12px;color:{C_TEXT3};font-size:11px;'
                f'font-family:monospace;text-align:right;">'
                f'{row["Weight"]*100:.0f}%</td>'
                f'<td style="padding:10px 12px;">{score_bar(row["Score"])}</td>'
                f'</tr>'
            )

        comp_color = C_HIGH if composite >= 65 else (C_MOD if composite >= 40 else C_LOW)
        th = (
            f'color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
            f'letter-spacing:1px;padding:10px 12px;border-bottom:1px solid {C_BORDER};'
            f'font-weight:600;'
        )

        html = (
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-radius:10px;overflow:hidden;margin-bottom:20px;">'
            f'<div style="padding:12px 16px;background:{C_CARD};'
            f'border-bottom:1px solid {C_BORDER};display:flex;'
            f'justify-content:space-between;align-items:center;">'
            f'<span style="color:{C_TEXT};font-size:13px;font-weight:600;">'
            f'Cycle Indicator Scorecard</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;">'
            f'Composite score: <span style="color:{comp_color};font-family:monospace;'
            f'font-weight:700;">{composite:.1f}</span>/100</span>'
            f'</div>'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr style="background:{C_CARD};">'
            f'<th style="{th}text-align:left;">Indicator</th>'
            f'<th style="{th}text-align:left;">Current Reading</th>'
            f'<th style="{th}text-align:left;">Cycle Signal</th>'
            f'<th style="{th}text-align:right;">Weight</th>'
            f'<th style="{th}text-align:left;">Score</th>'
            f'</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table>'
            f'</div>'
        )
        st.markdown(html, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Indicator table unavailable: {exc}")


def _render_historical_cycle_map(bdi_df: pd.DataFrame) -> None:
    try:
        fig = go.Figure()

        # Shaded phase regions
        for start_s, end_s, label, rgba in _CYCLE_PHASES_HIST:
            try:
                fig.add_vrect(
                    x0=start_s,
                    x1=end_s,
                    fillcolor=rgba,
                    line_width=0,
                    annotation_text=label,
                    annotation_position="top left",
                    annotation_font_size=9,
                    annotation_font_color=C_TEXT3,
                )
            except Exception:
                pass

        # BDI line
        fig.add_trace(go.Scatter(
            x=bdi_df["Date"].astype(str),
            y=bdi_df["BDI"],
            mode="lines",
            name="BDI",
            line=dict(color=C_ACCENT, width=2),
        ))

        # Key event markers
        events = [
            ("2008-05-01", 11793, "2008 Peak\n11,793"),
            ("2016-02-01",   290, "2016 Trough\n290"),
            ("2021-10-01",  5650, "2021 Peak\n5,650"),
            ("2025-06-01",  2100, "Current"),
        ]
        for date_s, val, lbl in events:
            try:
                fig.add_annotation(
                    x=date_s,
                    y=val,
                    text=lbl,
                    showarrow=True,
                    arrowhead=2,
                    arrowcolor=C_MOD,
                    arrowwidth=1.5,
                    font=dict(color=C_MOD, size=9),
                    bgcolor=C_CARD,
                    bordercolor=C_BORDER,
                    borderwidth=1,
                )
            except Exception:
                pass

        fig.update_layout(
            **_CHART_LAYOUT,
            title=dict(
                text="Baltic Dry Index 2000-2025 — Cycle Phases",
                font=dict(color=C_TEXT, size=13),
                x=0,
            ),
            xaxis=dict(
                title="",
                gridcolor=C_BORDER,
                color=C_TEXT2,
                showline=False,
            ),
            yaxis=dict(
                title="BDI",
                gridcolor=C_BORDER,
                color=C_TEXT2,
                zeroline=False,
            ),
            showlegend=False,
            height=380,
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception as exc:
        st.warning(f"Historical cycle map unavailable: {exc}")


def _render_trade_recommendations(current_phase: str) -> None:
    try:
        recs = {
            "RECOVERY": {
                "action":  "BUY",
                "color":   C_HIGH,
                "summary": "Accumulate quality carriers on dips. BDI momentum turning.",
                "buys": [
                    ("ZIM",  "Container", "Spot rate leverage, strong FCF"),
                    ("MATX", "Container", "Hawaii route protected, consistent divs"),
                    ("SBLK", "Bulker",    "Low break-even, commodity tailwind"),
                    ("GNK",  "Bulker",    "Panamax/Supramax recovery play"),
                    ("GOGL", "Bulker",    "High financial leverage to BDI"),
                ],
                "sells": [
                    ("TK",   "Tanker",    "Rate momentum lagging, trim"),
                    ("FRO",  "Tanker",    "Overextended vs spot rate"),
                ],
                "options": "Sell puts on ZIM/MATX to enter at better levels. Buy SBLK calls 3M out.",
            },
            "EXPANSION": {
                "action":  "HOLD / ADD",
                "color":   "#10b981",
                "summary": "Maximum long exposure. Rates running, earnings being upgraded.",
                "buys": [
                    ("ZIM",  "Container", "Maximize position size"),
                    ("MATX", "Container", "Add on pullbacks"),
                    ("FLNG", "LNG",       "LNG premium trade winter"),
                    ("SBLK", "Bulker",    "Ride BDI strength"),
                ],
                "sells": [
                    ("NMM",  "Container", "Charter-in exposure limits upside"),
                ],
                "options": "Buy near-term calls on shipping ETF. Sell deep OTM puts for income.",
            },
            "PEAK": {
                "action":  "REDUCE / SELL",
                "color":   C_MOD,
                "summary": "Rates peak. Newbuilds ordered. Begin reducing exposure systematically.",
                "buys":  [],
                "sells": [
                    ("ZIM",  "Container", "Sell half — rate cycle turning"),
                    ("SBLK", "Bulker",    "Trim into strength"),
                    ("GOGL", "Bulker",    "High leverage cuts both ways"),
                    ("FRO",  "Tanker",    "Tankers peak earlier"),
                    ("MATX", "Container", "Protected but reduce risk"),
                ],
                "options": "Buy ZIM puts 2M out. Sell covered calls on remaining longs.",
            },
            "CONTRACTION": {
                "action":  "SHORT / HEDGE",
                "color":   "#f97316",
                "summary": "Rates declining. Oversupply building. Protect capital aggressively.",
                "buys":  [],
                "sells": [
                    ("FRO",  "Tanker",    "Short: oversupply + rate decline"),
                    ("TK",   "Tanker",    "Short: high fleet growth"),
                    ("SBLK", "Bulker",    "Short: commodity demand weak"),
                    ("ZIM",  "Container", "Short: contract renewal risk"),
                ],
                "options": "Buy shipping sector puts. Hedge via BDI-linked instruments.",
            },
            "TROUGH": {
                "action":  "ACCUMULATE",
                "color":   C_LOW,
                "summary": "Generational entry. Buy quality with stops. Patience required.",
                "buys": [
                    ("MATX", "Container", "Bulletproof balance sheet"),
                    ("ZIM",  "Container", "Buy in tranches with stops at -10%"),
                    ("GNK",  "Bulker",    "Accumulate slowly — dry bulk first"),
                    ("FLNG", "LNG",       "LNG resilient through cycle"),
                ],
                "sells": [
                    ("HAFN", "Tanker",    "Weakest balance sheet — exit"),
                ],
                "options": "Sell ZIM/MATX puts to get paid to wait for entry.",
            },
        }

        all_phases = ["TROUGH", "RECOVERY", "EXPANSION", "PEAK", "CONTRACTION"]

        for phase in all_phases:
            rec   = recs.get(phase, {})
            color = _PHASE_COLOR.get(phase, C_ACCENT)
            is_current = (phase == current_phase)
            border_style = f"border:2px solid {color};" if is_current else f"border:1px solid {C_BORDER};"
            opacity = "1" if is_current else "0.6"
            badge = (
                f'<span style="background:{color}33;color:{color};padding:2px 10px;'
                f'border-radius:12px;font-size:10px;font-weight:700;margin-left:10px;">'
                f'CURRENT</span>'
                if is_current else ""
            )

            buys_html = ""
            for ticker, sector, reason in rec.get("buys", []):
                buys_html += (
                    f'<div style="display:flex;align-items:center;gap:10px;'
                    f'padding:6px 0;border-bottom:1px solid {C_BORDER};">'
                    f'<span style="background:{C_HIGH}22;color:{C_HIGH};'
                    f'padding:2px 8px;border-radius:4px;font-size:11px;'
                    f'font-weight:700;min-width:44px;text-align:center;">BUY</span>'
                    f'<span style="color:{C_TEXT};font-size:13px;font-weight:600;'
                    f'font-family:monospace;min-width:44px;">{ticker}</span>'
                    f'<span style="color:{C_TEXT3};font-size:11px;min-width:70px;">'
                    f'{sector}</span>'
                    f'<span style="color:{C_TEXT2};font-size:11px;">{reason}</span>'
                    f'</div>'
                )

            sells_html = ""
            for ticker, sector, reason in rec.get("sells", []):
                sells_html += (
                    f'<div style="display:flex;align-items:center;gap:10px;'
                    f'padding:6px 0;border-bottom:1px solid {C_BORDER};">'
                    f'<span style="background:{C_LOW}22;color:{C_LOW};'
                    f'padding:2px 8px;border-radius:4px;font-size:11px;'
                    f'font-weight:700;min-width:44px;text-align:center;">SELL</span>'
                    f'<span style="color:{C_TEXT};font-size:13px;font-weight:600;'
                    f'font-family:monospace;min-width:44px;">{ticker}</span>'
                    f'<span style="color:{C_TEXT3};font-size:11px;min-width:70px;">'
                    f'{sector}</span>'
                    f'<span style="color:{C_TEXT2};font-size:11px;">{reason}</span>'
                    f'</div>'
                )

            opts = rec.get("options", "")
            opts_html = (
                f'<div style="margin-top:10px;padding:10px;background:{C_CARD};'
                f'border-radius:6px;border-left:3px solid {C_ACCENT};">'
                f'<span style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
                f'letter-spacing:1px;">Options Strategy: </span>'
                f'<span style="color:{C_TEXT2};font-size:12px;">{opts}</span>'
                f'</div>'
                if opts else ""
            )

            action_label = rec.get("action", phase)
            summary      = rec.get("summary", "")

            html = (
                f'<div style="{border_style}border-radius:10px;padding:16px 18px;'
                f'margin-bottom:14px;background:{C_SURFACE};opacity:{opacity};">'
                f'<div style="display:flex;align-items:center;gap:8px;'
                f'margin-bottom:10px;">'
                f'<span style="color:{color};font-size:15px;font-weight:800;'
                f'letter-spacing:1px;">{phase}</span>'
                f'{badge}'
                f'<span style="margin-left:auto;background:{color}22;color:{color};'
                f'padding:3px 12px;border-radius:6px;font-size:11px;font-weight:700;">'
                f'{action_label}</span>'
                f'</div>'
                f'<div style="color:{C_TEXT2};font-size:12px;margin-bottom:12px;">'
                f'{summary}</div>'
                f'{buys_html}'
                f'{sells_html}'
                f'{opts_html}'
                f'</div>'
            )
            st.markdown(html, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Trade recommendations unavailable: {exc}")


# ── Main render ───────────────────────────────────────────────────────────────

def render(macro_data=None, freight_data=None, insights=None, stock_data=None):
    """Render the Shipping Market Cycle Positioning tab."""
    try:
        st.markdown(
            f'<div style="padding:4px 0 18px;">'
            f'<span style="color:{C_TEXT};font-size:18px;font-weight:800;'
            f'letter-spacing:0.5px;">Shipping Cycle Positioning</span>'
            f'<span style="color:{C_TEXT3};font-size:12px;margin-left:12px;">'
            f'~7-year cycle analysis &amp; trade recommendations</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        st.subheader("Shipping Cycle Positioning")

    try:
        phase    = _current_phase()
        pos_score = _cycle_position_score()
    except Exception:
        phase    = "RECOVERY"
        pos_score = 0.35

    # ── 1. Cycle Dashboard ────────────────────────────────────────────────────
    try:
        _section_header("1. Cycle Dashboard", "Current phase with historical context")
        _render_cycle_dashboard(phase)
    except Exception as exc:
        st.warning(f"Section 1 error: {exc}")

    # ── 2. Cycle Clock ────────────────────────────────────────────────────────
    try:
        _section_header("2. Cycle Clock", "Pure-CSS clock face — 12 o'clock = PEAK, 6 o'clock = TROUGH")
        _render_cycle_clock(phase, pos_score)
    except Exception as exc:
        st.warning(f"Section 2 error: {exc}")

    # ── 3. Cycle Indicator Table ──────────────────────────────────────────────
    try:
        _section_header(
            "3. Cycle Indicator Scorecard",
            "10 indicators — current reading, cycle signal, composite score",
        )
        ind_df = _build_indicators()
        _render_indicator_table(ind_df)
    except Exception as exc:
        st.warning(f"Section 3 error: {exc}")

    # ── 4. Historical Cycle Map ───────────────────────────────────────────────
    try:
        _section_header(
            "4. Historical Cycle Map",
            "BDI 2000-2025 with cycle phase regions — key events marked",
        )
        bdi_df = _build_bdi_history()
        _render_historical_cycle_map(bdi_df)
    except Exception as exc:
        st.warning(f"Section 4 error: {exc}")

    # ── 5. Cycle-Based Trade Recommendations ─────────────────────────────────
    try:
        _section_header(
            "5. Cycle-Based Trade Recommendations",
            "What to buy / sell in each phase — current phase highlighted",
        )
        _render_trade_recommendations(phase)
    except Exception as exc:
        st.warning(f"Section 5 error: {exc}")
