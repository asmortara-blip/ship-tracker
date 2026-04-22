"""tab_cycle.py - Shipping Market Cycle Positioning tab.

Identifies where we are in the shipping cycle and surfaces
cycle-based trade recommendations.

Sections:
  1. Cycle Dashboard          - current phase with large text + historical context
  2. Cycle Clock              - pure-CSS clock face showing cycle position
  3. Cycle Indicator Table    - 10 indicators with cycle signal + composite score
  4. Historical Cycle Map     - BDI 2000-2025 with shaded phase regions
  5. Cycle-Based Trade Recs   - what to buy/sell in each phase
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_SURFACE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    live_data_badge,
    metric_card_row,
    page_header,
    section_header,
    wsj_market_table,
)

# ── Domain-specific phase palette (semantic, kept local to this tab) ────────
_PHASE_COLOR: dict[str, str] = {
    "TROUGH":      C_LOW,
    "RECOVERY":    C_ACCENT,
    "EXPANSION":   C_HIGH,
    "PEAK":        C_MOD,
    "CONTRACTION": "#f97316",
}

_PHASE_DESC: dict[str, str] = {
    "TROUGH":      "Freight rates at cycle lows. Prime accumulation window for quality names.",
    "RECOVERY":    "Rates recovering. Demand exceeding supply. Earnings upgrades incoming.",
    "EXPANSION":   "Sustained rate strength. Orderbook filling. Maximise long exposure.",
    "PEAK":        "Rates elevated but momentum fading. Newbuilds on order. Reduce risk.",
    "CONTRACTION": "Rates declining. Oversupply emerging. Reduce longs, add hedges.",
}

_PHASE_HIST: dict[str, str] = {
    "TROUGH":      "Last seen: Dec 2015 - Mar 2016 (BDI 290). Avg duration: 4-8 months.",
    "RECOVERY":    "Last seen: Apr 2020 - Dec 2020. Avg duration: 6-12 months.",
    "EXPANSION":   "Last seen: Jan 2021 - Aug 2021. Avg duration: 8-18 months.",
    "PEAK":        "Last seen: Oct 2021 (BDI 5,650). Avg duration: 2-6 months.",
    "CONTRACTION": "Last seen: Sep 2022 - Mar 2023. Avg duration: 6-12 months.",
}

# Clock-face angles: 12 o'clock = PEAK (0 deg), clockwise.
_PHASE_ANGLE: dict[str, int] = {
    "PEAK":        0,
    "CONTRACTION": 90,
    "TROUGH":      180,
    "RECOVERY":    270,
    "EXPANSION":   315,
}

_ALL_PHASES: tuple[str, ...] = (
    "TROUGH", "RECOVERY", "EXPANSION", "PEAK", "CONTRACTION",
)

# ── Data sources (provenance pills) ──────────────────────────────────────────
_CYCLE_SOURCE = DataSource.modeled(
    "Cycle Phase Engine",
    notes="Composite indicator model - refresh from processing.cycle_engine",
)
_INDICATOR_SOURCE = DataSource.modeled(
    "Cycle Indicators",
    notes="Fixed fixture data - replace with live feed when wired",
)
_BDI_DEMO_SOURCE = DataSource.demo("BDI 2000-2025 (synthetic)")
_RECS_SOURCE = DataSource.modeled(
    "Cycle Playbook",
    notes="Hand-curated trade recommendations per cycle phase",
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
        ("BDI Trend",              "+18% MoM",    "RECOVERY",    0.15, 72),
        ("Fleet Utilization",      "87.4%",       "EXPANSION",   0.12, 80),
        ("Newbuild Orders",        "12% of fleet", "CONTRACTION", 0.10, 35),
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


# ── BDI historical data (synthetic — DEMO badge is mandatory) ─────────────────
def _build_bdi_history() -> pd.DataFrame:
    rng = np.random.default_rng(99)
    bdi_anchor = {
        2000: 1200, 2001: 900,  2002: 1100, 2003: 1800, 2004: 4500,
        2005: 3000, 2006: 3200, 2007: 7200, 2008: 11793, 2009: 1770,
        2010: 2758, 2011: 1549, 2012: 700,  2013: 1200, 2014: 1000,
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


_CYCLE_PHASES_HIST: tuple[tuple[str, str, str, str], ...] = (
    # (start, end, phase_label, rgba_fill)
    ("2000-01", "2003-12", "RECOVERY",    "rgba(53,114,176,0.15)"),
    ("2004-01", "2008-09", "EXPANSION",   "rgba(46,158,110,0.15)"),
    ("2008-10", "2009-06", "CONTRACTION", "rgba(249,115,22,0.18)"),
    ("2009-07", "2010-12", "RECOVERY",    "rgba(53,114,176,0.15)"),
    ("2011-01", "2016-02", "CONTRACTION", "rgba(249,115,22,0.18)"),
    ("2016-03", "2016-12", "TROUGH",      "rgba(192,57,43,0.18)"),
    ("2017-01", "2019-12", "RECOVERY",    "rgba(53,114,176,0.15)"),
    ("2020-01", "2020-05", "TROUGH",      "rgba(192,57,43,0.18)"),
    ("2020-06", "2021-10", "EXPANSION",   "rgba(46,158,110,0.15)"),
    ("2021-11", "2021-12", "PEAK",        "rgba(201,150,43,0.18)"),
    ("2022-01", "2023-06", "CONTRACTION", "rgba(249,115,22,0.18)"),
    ("2023-07", "2025-12", "RECOVERY",    "rgba(53,114,176,0.15)"),
)


# ── Cell formatters for WSJ market table ────────────────────────────────────
def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};font-variant-numeric:tabular-nums;">'
        f'{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT, weight: int = 600) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _score_bar(score: float) -> str:
    pct = max(0, min(100, score))
    color = C_HIGH if pct >= 65 else (C_MOD if pct >= 40 else C_LOW)
    return (
        f'<span style="display:inline-flex;align-items:center;gap:8px;">'
        f'<span style="display:inline-block;background:{C_CARD};border-radius:4px;'
        f'height:6px;width:60px;overflow:hidden;">'
        f'<span style="display:inline-block;background:{color};'
        f'width:{pct:.0f}%;height:6px;border-radius:4px;"></span></span>'
        f'<span style="color:{C_TEXT2};font-size:11px;font-family:var(--mono);">'
        f'{pct:.0f}</span>'
        f'</span>'
    )


# ── Section renderers ────────────────────────────────────────────────────────
def _render_cycle_dashboard(phase: str) -> None:
    try:
        color = _PHASE_COLOR.get(phase, C_ACCENT)
        desc  = _PHASE_DESC.get(phase, "")
        hist  = _PHASE_HIST.get(phase, "")

        st.html(live_data_badge(_CYCLE_SOURCE))

        pills_html = ""
        for p in _ALL_PHASES:
            pc     = _PHASE_COLOR.get(p, C_TEXT3)
            active = p == phase
            bg     = f"{pc}30" if active else "transparent"
            border = pc if active else C_BORDER
            fw     = "700" if active else "400"
            pills_html += (
                f'<span style="background:{bg};border:1px solid {border};'
                f'color:{pc};padding:4px 14px;border-radius:4px;font-size:11px;'
                f'font-weight:{fw};letter-spacing:1px;white-space:nowrap;">'
                f'{p}</span>'
            )

        st.html(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-left:5px solid {color};border-radius:6px;padding:24px 28px;'
            f'margin-bottom:20px;">'
            f'<div style="color:{C_TEXT3};font-family:var(--sans);font-size:11px;'
            f'text-transform:uppercase;letter-spacing:2px;margin-bottom:8px;">'
            f'Current Cycle Phase</div>'
            f'<div style="color:{color};font-size:52px;font-weight:900;'
            f'font-family:var(--mono);letter-spacing:2px;line-height:1;">'
            f'{phase}</div>'
            f'<div style="color:{C_TEXT2};font-family:var(--sans);'
            f'font-size:13px;margin-top:12px;max-width:600px;">{desc}</div>'
            f'<div style="color:{C_TEXT3};font-size:11px;margin-top:8px;">'
            f'{hist}</div>'
            f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:18px;">'
            f'{pills_html}'
            f'</div>'
            f'</div>'
        )
    except Exception:
        logger.exception("Cycle dashboard render failed")
        st.warning("Cycle dashboard unavailable.")


def _render_cycle_clock(phase: str, position_score: float) -> None:
    """Pure-SVG clock face paired with a legend column."""
    try:
        color   = _PHASE_COLOR.get(phase, C_ACCENT)
        angle_d = _PHASE_ANGLE.get(phase, 0)
        angle_r = math.radians(angle_d)

        cx, cy   = 110, 110
        hand_len = 78
        tip_x    = cx + hand_len * math.sin(angle_r)
        tip_y    = cy - hand_len * math.cos(angle_r)

        label_radius = 102
        phase_labels = {
            "PEAK":        (0,   _PHASE_COLOR["PEAK"]),
            "CONTRACTION": (90,  _PHASE_COLOR["CONTRACTION"]),
            "TROUGH":      (180, _PHASE_COLOR["TROUGH"]),
            "RECOVERY":    (270, _PHASE_COLOR["RECOVERY"]),
        }
        labels_svg = ""
        for lbl, (ang, lc) in phase_labels.items():
            ar = math.radians(ang)
            lx = cx + label_radius * math.sin(ar)
            ly = cy - label_radius * math.cos(ar)
            fw = "700" if lbl == phase else "400"
            op = "1" if lbl == phase else "0.55"
            sz = "11" if lbl == phase else "10"
            labels_svg += (
                f'<text x="{lx:.1f}" y="{ly:.1f}" fill="{lc}" opacity="{op}" '
                f'font-size="{sz}" font-weight="{fw}" font-family="var(--sans)" '
                f'text-anchor="middle" dominant-baseline="middle" '
                f'letter-spacing="0.5">{lbl}</text>'
            )

        tick_lines = "".join(
            f'<line x1="{cx + 68*math.sin(math.radians(i*30)):.1f}" '
            f'y1="{cy - 68*math.cos(math.radians(i*30)):.1f}" '
            f'x2="{cx + 78*math.sin(math.radians(i*30)):.1f}" '
            f'y2="{cy - 78*math.cos(math.radians(i*30)):.1f}" '
            f'stroke="{C_BORDER}" stroke-width="1.5"/>'
            for i in range(12)
        )

        clock_svg = (
            f'<svg width="240" height="240" viewBox="0 0 220 220" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<circle cx="{cx}" cy="{cy}" r="108" fill="{C_CARD}" '
            f'stroke="{C_BORDER}" stroke-width="2"/>'
            f'<circle cx="{cx}" cy="{cy}" r="80" fill="none" '
            f'stroke="{C_BORDER}" stroke-width="1"/>'
            f'{tick_lines}'
            f'<line x1="{cx}" y1="{cy}" x2="{tip_x:.1f}" y2="{tip_y:.1f}" '
            f'stroke="{color}" stroke-width="3" stroke-linecap="round"/>'
            f'<circle cx="{cx}" cy="{cy}" r="5" fill="{color}"/>'
            f'{labels_svg}'
            f'</svg>'
        )

        legend_html = (
            f'<span class="sub-section-header" '
            f'style="display:block;margin-bottom:8px;">Cycle Clock</span>'
            f'<span style="display:block;color:{color};font-size:26px;font-weight:800;'
            f'font-family:var(--mono);">{phase}</span>'
            f'<span style="display:block;color:{C_TEXT3};font-size:12px;margin-top:6px;">'
            f'12 o\'clock = PEAK &nbsp;|&nbsp; 6 o\'clock = TROUGH</span>'
            f'<span style="display:block;color:{C_TEXT3};font-size:12px;margin-top:4px;">'
            f'Cycle position score: '
            f'<span style="color:{C_TEXT2};font-family:var(--mono);">'
            f'{position_score:.0%}</span> of peak</span>'
            f'<span style="display:block;margin-top:14px;background:{C_CARD};'
            f'border-radius:6px;height:8px;width:180px;overflow:hidden;">'
            f'<span style="display:block;background:{color};'
            f'width:{position_score*100:.0f}%;height:100%;border-radius:6px;">'
            f'</span></span>'
            f'<span style="display:flex;color:{C_TEXT3};font-size:10px;'
            f'margin-top:4px;justify-content:space-between;width:180px;">'
            f'<span>TROUGH</span><span>PEAK</span></span>'
        )

        st.html(live_data_badge(_CYCLE_SOURCE))
        left, right = st.columns([1, 2])
        with left:
            st.html(clock_svg)
        with right:
            st.html(legend_html)
    except Exception:
        logger.exception("Cycle clock render failed")
        st.warning("Cycle clock unavailable.")


def _render_indicator_table(df: pd.DataFrame) -> None:
    try:
        composite = (df["Score"] * df["Weight"]).sum() / df["Weight"].sum()
        comp_color = C_HIGH if composite >= 65 else (C_MOD if composite >= 40 else C_LOW)

        metric_card_row(
            [
                {
                    "label": "Composite Score",
                    "value": f"{composite:.1f}/100",
                    "accent": comp_color,
                    "sublabel": "Weighted average of 10 indicators",
                },
                {
                    "label": "Indicators Tracked",
                    "value": f"{len(df)}",
                    "accent": C_ACCENT,
                    "sublabel": "Supply, demand, price, credit",
                },
                {
                    "label": "Dominant Signal",
                    "value": df["Cycle Signal"].mode().iloc[0],
                    "accent": _PHASE_COLOR.get(df["Cycle Signal"].mode().iloc[0], C_ACCENT),
                    "sublabel": "Most-frequent cycle read",
                },
            ],
            columns=3,
        )
        st.html("<div style='height:12px;'></div>")
        st.html(live_data_badge(_INDICATOR_SOURCE))

        headers = ["Indicator", "Current Reading", "Cycle Signal", "Weight", "Score"]
        rows = []
        for _, row in df.iterrows():
            signal = row["Cycle Signal"]
            rows.append([
                _sans(row["Indicator"], color=C_TEXT, weight=600),
                _mono(row["Current Reading"], color=C_TEXT2, weight=500),
                badge(signal, color=_PHASE_COLOR.get(signal, C_TEXT3)),
                _mono(f"{row['Weight']*100:.0f}%", color=C_TEXT3, weight=500),
                _score_bar(row["Score"]),
            ])
        wsj_market_table(headers, rows)
    except Exception:
        logger.exception("Indicator table render failed")
        st.warning("Indicator table unavailable.")


def _render_historical_cycle_map(bdi_df: pd.DataFrame) -> None:
    try:
        st.html(live_data_badge(_BDI_DEMO_SOURCE))

        fig = go.Figure()

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
                logger.debug("vrect failed for %s - %s", start_s, end_s)

        fig.add_trace(go.Scatter(
            x=bdi_df["Date"].astype(str),
            y=bdi_df["BDI"],
            mode="lines",
            name="BDI",
            line=dict(color=C_ACCENT, width=2),
        ))

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
                logger.debug("annotation failed for %s", date_s)

        apply_dark_layout(
            fig,
            title="Baltic Dry Index 2000-2025 - Cycle Phases",
            height=380,
            showlegend=False,
            xaxis=dict(title=""),
            yaxis=dict(title="BDI", zeroline=False),
        )
        st.plotly_chart(fig, use_container_width=True, key="cycle_historical_bdi")
    except Exception:
        logger.exception("Historical cycle map render failed")
        st.warning("Historical cycle map unavailable.")


def _render_trade_recommendations(current_phase: str) -> None:
    try:
        st.html(live_data_badge(_RECS_SOURCE))

        recs = {
            "RECOVERY": {
                "action":  "BUY",
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
                "summary": "Rates peak. Newbuilds ordered. Begin reducing exposure systematically.",
                "buys":  [],
                "sells": [
                    ("ZIM",  "Container", "Sell half - rate cycle turning"),
                    ("SBLK", "Bulker",    "Trim into strength"),
                    ("GOGL", "Bulker",    "High leverage cuts both ways"),
                    ("FRO",  "Tanker",    "Tankers peak earlier"),
                    ("MATX", "Container", "Protected but reduce risk"),
                ],
                "options": "Buy ZIM puts 2M out. Sell covered calls on remaining longs.",
            },
            "CONTRACTION": {
                "action":  "SHORT / HEDGE",
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
                "summary": "Generational entry. Buy quality with stops. Patience required.",
                "buys": [
                    ("MATX", "Container", "Bulletproof balance sheet"),
                    ("ZIM",  "Container", "Buy in tranches with stops at -10%"),
                    ("GNK",  "Bulker",    "Accumulate slowly - dry bulk first"),
                    ("FLNG", "LNG",       "LNG resilient through cycle"),
                ],
                "sells": [
                    ("HAFN", "Tanker",    "Weakest balance sheet - exit"),
                ],
                "options": "Sell ZIM/MATX puts to get paid to wait for entry.",
            },
        }

        for phase in _ALL_PHASES:
            rec   = recs.get(phase, {})
            color = _PHASE_COLOR.get(phase, C_ACCENT)
            is_current = (phase == current_phase)
            border_style = (
                f"border:2px solid {color};"
                if is_current else f"border:1px solid {C_BORDER};"
            )
            opacity = "1" if is_current else "0.6"
            current_pill = (
                f'<span style="background:{color}33;color:{color};padding:2px 10px;'
                f'border-radius:6px;font-size:10px;font-weight:700;margin-left:10px;">'
                f'CURRENT</span>'
                if is_current else ""
            )

            action_rows = []
            for ticker, sector, reason in rec.get("buys", []):
                action_rows.append(
                    f'<div style="display:flex;align-items:center;gap:10px;'
                    f'padding:6px 0;border-bottom:1px solid {C_BORDER};">'
                    f'<span style="background:{C_HIGH}22;color:{C_HIGH};'
                    f'padding:2px 8px;border-radius:4px;font-size:11px;'
                    f'font-weight:700;min-width:44px;text-align:center;">BUY</span>'
                    f'<span style="color:{C_TEXT};font-size:13px;font-weight:600;'
                    f'font-family:var(--mono);min-width:44px;">{ticker}</span>'
                    f'<span style="color:{C_TEXT3};font-size:11px;min-width:70px;">'
                    f'{sector}</span>'
                    f'<span style="color:{C_TEXT2};font-size:11px;">{reason}</span>'
                    f'</div>'
                )
            for ticker, sector, reason in rec.get("sells", []):
                action_rows.append(
                    f'<div style="display:flex;align-items:center;gap:10px;'
                    f'padding:6px 0;border-bottom:1px solid {C_BORDER};">'
                    f'<span style="background:{C_LOW}22;color:{C_LOW};'
                    f'padding:2px 8px;border-radius:4px;font-size:11px;'
                    f'font-weight:700;min-width:44px;text-align:center;">SELL</span>'
                    f'<span style="color:{C_TEXT};font-size:13px;font-weight:600;'
                    f'font-family:var(--mono);min-width:44px;">{ticker}</span>'
                    f'<span style="color:{C_TEXT3};font-size:11px;min-width:70px;">'
                    f'{sector}</span>'
                    f'<span style="color:{C_TEXT2};font-size:11px;">{reason}</span>'
                    f'</div>'
                )

            opts = rec.get("options", "")
            opts_block = (
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

            st.html(
                f'<div style="{border_style}border-radius:6px;padding:16px 18px;'
                f'margin-bottom:14px;background:{C_SURFACE};opacity:{opacity};">'
                f'<div style="display:flex;align-items:center;gap:8px;'
                f'margin-bottom:10px;">'
                f'<span style="color:{color};font-size:15px;font-weight:800;'
                f'letter-spacing:1px;">{phase}</span>'
                f'{current_pill}'
                f'<span style="margin-left:auto;background:{color}22;color:{color};'
                f'padding:3px 12px;border-radius:6px;font-size:11px;font-weight:700;">'
                f'{action_label}</span>'
                f'</div>'
                f'<div style="color:{C_TEXT2};font-family:var(--sans);'
                f'font-size:12px;margin-bottom:12px;">{summary}</div>'
                f'{"".join(action_rows)}'
                f'{opts_block}'
                f'</div>'
            )
    except Exception:
        logger.exception("Trade recommendations render failed")
        st.warning("Trade recommendations unavailable.")


# ── Main render ───────────────────────────────────────────────────────────────
def render(macro_data=None, freight_data=None, insights=None, stock_data=None) -> None:
    """Render the Shipping Market Cycle Positioning tab."""
    try:
        phase     = _current_phase()
        pos_score = _cycle_position_score()
    except Exception:
        logger.exception("Failed to resolve current cycle phase")
        phase, pos_score = "RECOVERY", 0.35

    current_color = _PHASE_COLOR.get(phase, C_ACCENT)

    page_header(
        title="Shipping Cycle Positioning",
        subtitle="~7-year cycle analysis and trade recommendations",
        badge_text=phase,
        badge_color=current_color,
    )

    # Hero KPI strip summarising the current cycle read
    try:
        metric_card_row(
            [
                {
                    "label": "Current Phase",
                    "value": phase,
                    "accent": current_color,
                    "sublabel": _PHASE_HIST.get(phase, ""),
                },
                {
                    "label": "Cycle Position",
                    "value": f"{pos_score:.0%}",
                    "accent": current_color,
                    "sublabel": "0% = trough, 100% = peak",
                },
                {
                    "label": "Next Likely Phase",
                    "value": _next_phase(phase),
                    "accent": _PHASE_COLOR.get(_next_phase(phase), C_ACCENT),
                    "sublabel": "Based on typical ordering",
                },
                {
                    "label": "Playbook",
                    "value": "Accumulate",
                    "accent": C_HIGH,
                    "sublabel": "Quality names on dips",
                },
            ],
            columns=4,
        )
    except Exception:
        logger.exception("Hero metrics render failed")

    # ── 1. Cycle Dashboard ────────────────────────────────────────────────────
    try:
        section_header(
            "Cycle Dashboard",
            "Current phase with historical context",
        )
        _render_cycle_dashboard(phase)
    except Exception:
        logger.exception("Section 1 render failed")
        st.warning("Cycle dashboard section unavailable.")

    # ── 2. Cycle Clock ────────────────────────────────────────────────────────
    try:
        section_header(
            "Cycle Clock",
            "12 o'clock = PEAK, 6 o'clock = TROUGH",
        )
        _render_cycle_clock(phase, pos_score)
    except Exception:
        logger.exception("Section 2 render failed")
        st.warning("Cycle clock section unavailable.")

    # ── 3. Cycle Indicator Table ──────────────────────────────────────────────
    try:
        section_header(
            "Cycle Indicator Scorecard",
            "10 indicators - current reading, cycle signal, composite score",
        )
        ind_df = _build_indicators()
        _render_indicator_table(ind_df)
    except Exception:
        logger.exception("Section 3 render failed")
        st.warning("Cycle indicator section unavailable.")

    # ── 4. Historical Cycle Map ───────────────────────────────────────────────
    try:
        section_header(
            "Historical Cycle Map",
            "BDI 2000-2025 with cycle phase regions - key events marked",
        )
        bdi_df = _build_bdi_history()
        _render_historical_cycle_map(bdi_df)
    except Exception:
        logger.exception("Section 4 render failed")
        st.warning("Historical cycle map section unavailable.")

    # ── 5. Cycle-Based Trade Recommendations ─────────────────────────────────
    try:
        section_header(
            "Cycle-Based Trade Recommendations",
            "What to buy / sell in each phase - current phase highlighted",
        )
        _render_trade_recommendations(phase)
    except Exception:
        logger.exception("Section 5 render failed")
        st.warning("Trade recommendations section unavailable.")


def _next_phase(phase: str) -> str:
    """Return the canonical next phase in the cycle ordering."""
    order = ["TROUGH", "RECOVERY", "EXPANSION", "PEAK", "CONTRACTION"]
    try:
        return order[(order.index(phase) + 1) % len(order)]
    except ValueError:
        return "RECOVERY"
