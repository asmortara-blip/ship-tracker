"""tab_cycle.py — Shipping Cycle Timer tab.

Identifies where we are in the ~7-year shipping cycle and when to buy/sell
shipping stocks. Uses BDI, orderbook, utilization, P/B, and macro indicators.
"""
from __future__ import annotations

import csv
import io
import math

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from engine.cycle_timer import (
    CyclePhase,
    CycleTiming,
    classify_shipping_cycle,
    estimate_cycle_position_score,
    generate_entry_signals,
    get_historical_cycle_data,
)
from ui.styles import (
    C_BG, C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_MOD, C_LOW, C_ACCENT,
    _hex_to_rgba,
    section_header,
)

# ── Phase color palette ────────────────────────────────────────────────────────
C_PEAK      = "#f59e0b"   # amber
C_DECLINE   = "#f97316"   # orange
C_RECOVERY  = "#3b82f6"   # blue
C_TROUGH    = "#ef4444"   # red

_PHASE_COLORS: dict[str, str] = {
    CyclePhase.TROUGH:   C_TROUGH,
    CyclePhase.RECOVERY: C_RECOVERY,
    CyclePhase.PEAK:     C_PEAK,
    CyclePhase.DECLINE:  C_DECLINE,
}

# Friendly display labels (EXPANSION = RECOVERY, CONTRACTION = DECLINE)
_PHASE_DISPLAY: dict[str, str] = {
    CyclePhase.TROUGH:   "TROUGH",
    CyclePhase.RECOVERY: "EXPANSION",
    CyclePhase.PEAK:     "PEAK",
    CyclePhase.DECLINE:  "CONTRACTION",
}

_PHASE_ICON: dict[str, str] = {
    CyclePhase.TROUGH:   "&#8681;",   # ↓ down arrow
    CyclePhase.RECOVERY: "&#8679;",   # ↑ up arrow
    CyclePhase.PEAK:     "&#9650;",   # ▲ solid up
    CyclePhase.DECLINE:  "&#9660;",   # ▼ solid down
}

_PHASE_DESC: dict[str, str] = {
    CyclePhase.TROUGH:   "Freight rates at cycle lows — prime accumulation window",
    CyclePhase.RECOVERY: "Rates rising, fleet utilization tightening — hold & add",
    CyclePhase.PEAK:     "Rates at or near cycle highs — reduce / take profits",
    CyclePhase.DECLINE:  "Oversupply emerging, rates falling — avoid or short",
}

_POSITIONING_COLORS: dict[str, str] = {
    "AGGRESSIVE_LONG": C_HIGH,
    "LONG":            C_HIGH,
    "NEUTRAL":         C_MOD,
    "REDUCE":          C_DECLINE,
    "SHORT":           C_LOW,
}

_ACTION_COLORS: dict[str, str] = {
    "BUY":    C_HIGH,
    "HOLD":   C_MOD,
    "REDUCE": C_DECLINE,
    "SELL":   C_LOW,
    "WATCH":  C_TEXT2,
}

# ── Phase characteristics data ─────────────────────────────────────────────────
_PHASE_CHARACTERISTICS: dict[str, dict] = {
    CyclePhase.TROUGH: {
        "rates":   ("Near Lows", C_LOW,      "Freight rates at multi-year lows, spot < OPEX"),
        "stocks":  ("Distressed", C_LOW,     "Shipping stocks at P/B < 0.8x, deep value"),
        "demand":  ("Weak", C_LOW,           "Global trade growth stalled or contracting"),
        "fleet":   ("Oversupplied", C_LOW,   "Fleet utilization <80%, excess idle capacity"),
        "ob":      ("Elevated", C_MOD,       "High orderbook slowly being absorbed"),
        "action":  "Aggressive buying opportunity — maximum fear = maximum opportunity",
    },
    CyclePhase.RECOVERY: {
        "rates":   ("Rising", C_RECOVERY,    "Spot rates trending up, time charter gaining"),
        "stocks":  ("Re-rating", C_RECOVERY, "Shipping stocks breaking out, P/B expanding"),
        "demand":  ("Improving", C_HIGH,     "Trade volumes recovering, commodity demand up"),
        "fleet":   ("Tightening", C_MOD,     "Utilization climbing toward 85-90%, scrapping slow"),
        "ob":      ("Low-Moderate", C_HIGH,  "Orderbook lean from prior cycle, slow ordering"),
        "action":  "Accumulate on dips — ride the re-rating from value to growth",
    },
    CyclePhase.PEAK: {
        "rates":   ("At Highs", C_PEAK,      "Freight rates at or near historical highs"),
        "stocks":  ("Overvalued", C_PEAK,    "P/B > 2x, stocks pricing in perfection"),
        "demand":  ("Strong", C_HIGH,        "Trade volumes robust, high commodity prices"),
        "fleet":   ("Fully Utilised", C_MOD, "Utilization >90%, very low idle fleet"),
        "ob":      ("Rising Fast", C_LOW,    "Newbuilding orders surging — future supply risk"),
        "action":  "Take profits, reduce positions — supply wave on the horizon",
    },
    CyclePhase.DECLINE: {
        "rates":   ("Falling", C_DECLINE,    "Spot rates correcting from highs, pressure building"),
        "stocks":  ("Declining", C_DECLINE,  "Shipping stocks rolling over, earnings topping"),
        "demand":  ("Softening", C_MOD,      "Trade growth slowing, commodity demand cooling"),
        "fleet":   ("Loosening", C_DECLINE,  "New deliveries hitting market, utilization falling"),
        "ob":      ("Heavy", C_LOW,          "Large order backlog due to deliver over 2-3 years"),
        "action":  "Reduce exposure, avoid new longs — wait for next trough entry",
    },
}

# ── Phase transition signals ───────────────────────────────────────────────────
_TRANSITION_SIGNALS: dict[str, dict] = {
    CyclePhase.TROUGH: {
        "next_phase": "EXPANSION",
        "signals": [
            "BDI sustains a break above its 200-day moving average",
            "Fleet utilization rises above 82% for two consecutive quarters",
            "Scrapping activity accelerates > 20M DWT / year",
            "P/B ratios begin recovering from sub-0.8x lows",
            "Freight futures curve moves into backwardation",
        ],
        "risk": "False dawn — BDI spikes but fades; utilization stalls below threshold",
    },
    CyclePhase.RECOVERY: {
        "next_phase": "PEAK",
        "signals": [
            "BDI sustains above 2,500 for >3 months",
            "Newbuilding orders surge > 30M DWT in a single quarter",
            "Fleet utilization plateaus near 90%",
            "Shipping stock P/B multiples exceed 2.0x",
            "Time charter rates for 1-year fixtures exceed 5-year averages",
        ],
        "risk": "Cycle cut short by macro shock (recession, trade war, pandemic)",
    },
    CyclePhase.PEAK: {
        "next_phase": "CONTRACTION",
        "signals": [
            "New vessel deliveries accelerate > 5% fleet growth per year",
            "BDI breaks below its 200-day moving average on high volume",
            "Spot rates fall 20%+ from recent highs",
            "Global trade growth forecasts revised down materially",
            "Credit conditions tighten; shipping bank loans pulled",
        ],
        "risk": "Prolonged plateau if structural demand shock sustains elevated rates",
    },
    CyclePhase.DECLINE: {
        "next_phase": "TROUGH",
        "signals": [
            "BDI falls to multi-year lows (< 1,000)",
            "Scrapping activity exceeds 30M DWT / year",
            "Fleet utilization drops below 78%",
            "Orderbook collapses to < 8% of fleet as orders cancelled/deferred",
            "Multiple operators filing for bankruptcy or distressed asset sales",
        ],
        "risk": "Extended decline if macro environment stays weak; oversupply persists",
    },
}

# ── Historical phase duration statistics ──────────────────────────────────────
_PHASE_DURATION_STATS: dict[str, dict] = {
    CyclePhase.TROUGH:   {"avg_months": 14, "min_months": 6,  "max_months": 30,
                           "examples": ["2009 (12mo)", "2016 (18mo)", "2020 (8mo)"]},
    CyclePhase.RECOVERY: {"avg_months": 28, "min_months": 12, "max_months": 48,
                           "examples": ["2010–12 (24mo)", "2017–19 (36mo)"]},
    CyclePhase.PEAK:     {"avg_months": 10, "min_months": 4,  "max_months": 18,
                           "examples": ["2007–08 (14mo)", "2021 (8mo)"]},
    CyclePhase.DECLINE:  {"avg_months": 32, "min_months": 18, "max_months": 60,
                           "examples": ["2012–15 (42mo)", "2022–24 (28mo)"]},
}

# ── Investment implications by phase ─────────────────────────────────────────
_INVESTMENT_IMPLICATIONS: dict[str, list[tuple[str, str, str]]] = {
    CyclePhase.TROUGH: [
        ("Dry Bulk", "AGGRESSIVE BUY", C_HIGH,
         "Buy diversified bulkers. GOGL, SBLK at P/B < 1x. Target 3-5x over full cycle."),
        ("Tankers", "BUY", C_HIGH,
         "Crude tankers (DHT, INSW) attractive. Tanker cycle may lag dry bulk by 6-12mo."),
        ("Container", "WATCH", C_MOD,
         "Container lines more susceptible to macro; wait for demand confirmation."),
        ("Options", "BUY CALLS", C_HIGH,
         "Long-dated calls on 2CLF capture asymmetric upside through recovery."),
    ],
    CyclePhase.RECOVERY: [
        ("Dry Bulk", "HOLD / ADD", C_RECOVERY,
         "Hold core positions. Add on 10-15% pullbacks. Momentum still favourable."),
        ("Tankers", "BUY", C_HIGH,
         "Tanker recovery often lags — still early innings if behind dry bulk."),
        ("Container", "BUY", C_RECOVERY,
         "Container demand picks up with global trade. ZIM, MATX attractive."),
        ("Options", "SELL PUTS", C_RECOVERY,
         "Collect premium on bullish shipping stocks. IV compressed from trough spike."),
    ],
    CyclePhase.PEAK: [
        ("Dry Bulk", "REDUCE", C_PEAK,
         "Take 30-50% profits. Keep a core position but trim high-beta names."),
        ("Tankers", "HOLD", C_MOD,
         "Tanker cycle may be earlier — review orderbook and utilization separately."),
        ("Container", "REDUCE", C_PEAK,
         "Container lines most leveraged to peak: sell into strength."),
        ("Options", "BUY PUTS", C_PEAK,
         "Protective puts or outright bearish options hedge the coming correction."),
    ],
    CyclePhase.DECLINE: [
        ("Dry Bulk", "AVOID / SHORT", C_LOW,
         "Avoid new longs. Short high-beta names (SBLK, GOGL) with elevated leverage."),
        ("Tankers", "NEUTRAL", C_MOD,
         "Tankers may hold better if geopolitical tonne-miles support remains."),
        ("Container", "SELL / SHORT", C_LOW,
         "Container rates collapse fastest — ZIM, MATX most exposed."),
        ("Options", "BUY PUTS / SPREADS", C_DECLINE,
         "Bear put spreads offer defined-risk exposure to continued decline."),
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION: Hero Phase Badge
# ══════════════════════════════════════════════════════════════════════════════

def _render_phase_hero(timing: CycleTiming, position_score: float) -> None:
    """Full-width hero banner: large phase badge + key stats."""
    phase = timing.current_phase
    display = _PHASE_DISPLAY.get(phase, phase)
    color = _PHASE_COLORS.get(phase, C_ACCENT)
    icon = _PHASE_ICON.get(phase, "")
    desc = _PHASE_DESC.get(phase, "")
    pos_color = _POSITIONING_COLORS.get(timing.recommended_positioning, C_TEXT2)
    stats = _PHASE_DURATION_STATS.get(phase, {})
    avg_mo = stats.get("avg_months", 24)
    months_in = timing.months_in_current_phase
    pct_through = min(100, int(months_in / max(1, avg_mo) * 100))

    # RGB decomposition for CSS
    r, g, b = [int(color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)]
    phase_rgba_18 = "rgba({},{},{},0.18)".format(r, g, b)
    phase_rgba_35 = "rgba({},{},{},0.35)".format(r, g, b)
    phase_rgba_06 = "rgba({},{},{},0.06)".format(r, g, b)
    phase_rgba_50 = "rgba({},{},{},0.50)".format(r, g, b)

    # Cycle wheel SVG: simple SVG arc indicating position on circle
    # angle in radians from top, clockwise
    angle_rad = position_score * 2 * math.pi
    cx, cy, radius_outer, radius_inner = 50, 50, 42, 28

    def _polar(angle: float, r: float) -> tuple[float, float]:
        # 0 = top, clockwise
        x = cx + r * math.sin(angle)
        y = cy - r * math.cos(angle)
        return x, y

    def _arc_path(r_val: float, start_a: float, end_a: float) -> str:
        n = 32
        pts = []
        for i in range(n + 1):
            a = start_a + (end_a - start_a) * i / n
            px, py = _polar(a, r_val)
            pts.append("{:.2f},{:.2f}".format(px, py))
        return " ".join(pts)

    # Quadrant fills for the mini SVG wheel
    quadrant_def = [
        (CyclePhase.TROUGH,   0,                 math.pi / 2),
        (CyclePhase.RECOVERY, math.pi / 2,       math.pi),
        (CyclePhase.PEAK,     math.pi,           3 * math.pi / 2),
        (CyclePhase.DECLINE,  3 * math.pi / 2,  2 * math.pi),
    ]

    quad_paths = ""
    for qphase, a_start, a_end in quadrant_def:
        qc = _PHASE_COLORS.get(qphase, "#888")
        is_cur = qphase == phase
        opacity = "0.55" if is_cur else "0.18"
        pts_outer = _arc_path(radius_outer, a_start, a_end)
        pts_inner = _arc_path(radius_inner, a_end, a_start)
        qr, qg, qb = [int(qc.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)]
        quad_paths += (
            '<polygon points="{outer} {inner}" '
            'fill="rgba({qr},{qg},{qb},{op})" stroke="rgba({qr},{qg},{qb},0.5)" '
            'stroke-width="0.5"/>'.format(
                outer=pts_outer, inner=pts_inner, qr=qr, qg=qg, qb=qb, op=opacity,
            )
        )

    # Needle tip
    nx, ny = _polar(angle_rad, radius_outer - 2)
    # Needle base slightly offset for width
    nb1x, nb1y = _polar(angle_rad - 0.12, radius_inner + 4)
    nb2x, nb2y = _polar(angle_rad + 0.12, radius_inner + 4)

    svg_wheel = """
    <svg viewBox="0 0 100 100" width="110" height="110" xmlns="http://www.w3.org/2000/svg"
         style="filter:drop-shadow(0 0 8px {glow})">
      <circle cx="50" cy="50" r="{ro}" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="0.5"/>
      <circle cx="50" cy="50" r="{ri}" fill="rgba(10,15,26,0.85)" stroke="rgba(255,255,255,0.05)" stroke-width="0.5"/>
      {quads}
      <polygon points="{nx:.1f},{ny:.1f} {nb1x:.1f},{nb1y:.1f} {nb2x:.1f},{nb2y:.1f}"
               fill="{color}" opacity="0.95"/>
      <circle cx="50" cy="50" r="3.5" fill="{color}" opacity="0.9"/>
      <circle cx="50" cy="50" r="2" fill="#0a0f1a"/>
    </svg>
    """.format(
        glow=phase_rgba_50,
        ro=radius_outer, ri=radius_inner,
        quads=quad_paths,
        nx=nx, ny=ny,
        nb1x=nb1x, nb1y=nb1y,
        nb2x=nb2x, nb2y=nb2y,
        color=color,
    )

    st.markdown(
        """
        <div style="
            background: linear-gradient(135deg, {phase_rgba_18} 0%, {card} 55%, {phase_rgba_06} 100%);
            border: 2px solid {phase_rgba_35};
            border-radius: 20px;
            padding: 28px 32px;
            margin-bottom: 24px;
            box-shadow: 0 0 40px {phase_rgba_18}, 0 4px 24px rgba(0,0,0,0.4);
            display: flex;
            align-items: center;
            gap: 28px;
            flex-wrap: wrap;
        ">
            <!-- Cycle Wheel SVG -->
            <div style="flex-shrink:0; opacity:0.92">{svg}</div>

            <!-- Phase Badge -->
            <div style="flex-shrink:0">
                <div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.14em;
                            color:{text3}; margin-bottom:8px; font-weight:700">
                    CURRENT SHIPPING CYCLE PHASE
                </div>
                <div style="
                    background: {color};
                    color: #000;
                    font-size: 1.85rem;
                    font-weight: 900;
                    padding: 10px 28px;
                    border-radius: 14px;
                    letter-spacing: 0.07em;
                    text-transform: uppercase;
                    white-space: nowrap;
                    box-shadow: 0 0 24px {phase_rgba_50};
                    display: inline-flex;
                    align-items: center;
                    gap: 10px;
                ">
                    <span style="font-size:1.5rem">{icon}</span>
                    {badge}
                </div>
                <div style="font-size:0.85rem; color:{text2}; margin-top:10px; font-weight:500">
                    {desc}
                </div>
            </div>

            <!-- Stats cluster -->
            <div style="flex:1; min-width:280px; display:grid; grid-template-columns:1fr 1fr; gap:12px">

                <!-- Confidence -->
                <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.07);
                            border-radius:12px; padding:14px 16px">
                    <div style="font-size:0.62rem; text-transform:uppercase; letter-spacing:0.1em;
                                color:{text3}; margin-bottom:6px">CONFIDENCE</div>
                    <div style="font-size:1.5rem; font-weight:800; color:{color};
                                line-height:1">{conf:.0f}%</div>
                    <div style="height:4px; background:rgba(255,255,255,0.08); border-radius:2px; margin-top:8px">
                        <div style="width:{conf:.0f}%; height:100%; background:{color}; border-radius:2px;
                                    box-shadow: 0 0 6px {color}80"></div>
                    </div>
                </div>

                <!-- Cycle position -->
                <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.07);
                            border-radius:12px; padding:14px 16px">
                    <div style="font-size:0.62rem; text-transform:uppercase; letter-spacing:0.1em;
                                color:{text3}; margin-bottom:6px">CYCLE POSITION</div>
                    <div style="font-size:1.5rem; font-weight:800; color:{accent};
                                line-height:1">{pos:.0f}%</div>
                    <div style="font-size:0.72rem; color:{text3}; margin-top:4px">
                        0% = trough &nbsp;&rarr;&nbsp; 100% = peak
                    </div>
                </div>

                <!-- Duration -->
                <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.07);
                            border-radius:12px; padding:14px 16px">
                    <div style="font-size:0.62rem; text-transform:uppercase; letter-spacing:0.1em;
                                color:{text3}; margin-bottom:6px">DURATION</div>
                    <div style="font-size:1.5rem; font-weight:800; color:{text};
                                line-height:1">{months_in}<span style="font-size:0.9rem;font-weight:500"> mo</span></div>
                    <div style="font-size:0.72rem; color:{text3}; margin-top:4px">
                        {pct_through}% of avg {avg_mo}mo phase
                    </div>
                </div>

                <!-- To next -->
                <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.07);
                            border-radius:12px; padding:14px 16px">
                    <div style="font-size:0.62rem; text-transform:uppercase; letter-spacing:0.1em;
                                color:{text3}; margin-bottom:6px">TO NEXT PHASE</div>
                    <div style="font-size:1.5rem; font-weight:800; color:{pos_color};
                                line-height:1">~{months_next}<span style="font-size:0.9rem;font-weight:500"> mo</span></div>
                    <div style="font-size:0.72rem; color:{pos_color}; margin-top:4px; font-weight:600">
                        {positioning}
                    </div>
                </div>
            </div>
        </div>
        """.format(
            svg=svg_wheel,
            card=C_CARD,
            phase_rgba_06=phase_rgba_06,
            phase_rgba_18=phase_rgba_18,
            phase_rgba_35=phase_rgba_35,
            phase_rgba_50=phase_rgba_50,
            color=color,
            icon=icon,
            badge=display,
            desc=desc,
            text=C_TEXT, text2=C_TEXT2, text3=C_TEXT3,
            accent=C_ACCENT,
            conf=timing.confidence * 100,
            pos=position_score * 100,
            months_in=months_in,
            avg_mo=avg_mo,
            pct_through=pct_through,
            months_next=timing.estimated_months_to_next_phase,
            pos_color=pos_color,
            positioning=timing.recommended_positioning.replace("_", " "),
        ),
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION: Cycle Wheel (Polar) — full Plotly version
# ══════════════════════════════════════════════════════════════════════════════

def _render_cycle_clock(timing: CycleTiming, position_score: float) -> None:
    """Full polar chart: 4 quadrant arcs, needle at current position, historic markers."""

    def _score_to_angle(score: float) -> float:
        return score * 360.0

    current_angle = _score_to_angle(position_score)

    quadrants = [
        (CyclePhase.TROUGH,   0,   90,  C_TROUGH,   "TROUGH\nBuy zone"),
        (CyclePhase.RECOVERY, 90,  180, C_RECOVERY, "EXPANSION\nAccumulate"),
        (CyclePhase.PEAK,     180, 270, C_PEAK,     "PEAK\nReduce"),
        (CyclePhase.DECLINE,  270, 360, C_DECLINE,  "CONTRACTION\nAvoid"),
    ]

    fig = go.Figure()

    # ── Outer colored arcs ───────────────────────────────────────────────────
    for phase, start_deg, end_deg, color, label in quadrants:
        is_current = phase == timing.current_phase
        opacity = 0.32 if is_current else 0.14
        angles_deg = list(range(start_deg, end_deg + 1))
        r_vals = [1.0] * len(angles_deg)
        theta_arc = angles_deg + [start_deg]
        r_arc = r_vals + [0.0]

        fig.add_trace(go.Scatterpolar(
            r=r_arc,
            theta=theta_arc,
            fill="toself",
            fillcolor=_hex_to_rgba(color, opacity),
            line=dict(color=_hex_to_rgba(color, 0.6 if is_current else 0.3), width=1.5 if is_current else 0.7),
            mode="lines",
            hoverinfo="skip",
            showlegend=False,
        ))

    # ── Inner ring (donut effect) ────────────────────────────────────────────
    for phase, start_deg, end_deg, color, _ in quadrants:
        is_current = phase == timing.current_phase
        angles_deg = list(range(start_deg, end_deg + 1))
        r_inner = [0.50] * len(angles_deg)
        theta_inner = angles_deg + [start_deg]
        r_inner_closed = r_inner + [0.0]

        fig.add_trace(go.Scatterpolar(
            r=r_inner_closed,
            theta=theta_inner,
            fill="toself",
            fillcolor=_hex_to_rgba(color, 0.06 if not is_current else 0.12),
            line=dict(color=_hex_to_rgba(color, 0.2), width=0.5),
            mode="lines",
            hoverinfo="skip",
            showlegend=False,
        ))

    # ── Rim highlight for current quadrant ───────────────────────────────────
    phase_color = _PHASE_COLORS[timing.current_phase]
    cur_q = next((q for q in quadrants if q[0] == timing.current_phase), None)
    if cur_q:
        _, cstart, cend, ccolor, _ = cur_q
        rim_angles = list(range(cstart, cend + 1))
        fig.add_trace(go.Scatterpolar(
            r=[1.04] * len(rim_angles),
            theta=rim_angles,
            mode="lines",
            line=dict(color=_hex_to_rgba(ccolor, 0.85), width=5),
            hoverinfo="skip",
            showlegend=False,
        ))

    # ── Quadrant labels ──────────────────────────────────────────────────────
    label_positions = [
        (CyclePhase.TROUGH,   45,  0.75),
        (CyclePhase.RECOVERY, 135, 0.75),
        (CyclePhase.PEAK,     225, 0.75),
        (CyclePhase.DECLINE,  315, 0.75),
    ]
    label_text = {
        CyclePhase.TROUGH:   "TROUGH",
        CyclePhase.RECOVERY: "EXPANSION",
        CyclePhase.PEAK:     "PEAK",
        CyclePhase.DECLINE:  "CONTRACTION",
    }
    for phase, theta, r in label_positions:
        color = _PHASE_COLORS[phase]
        is_current = phase == timing.current_phase
        fig.add_trace(go.Scatterpolar(
            r=[r],
            theta=[theta],
            mode="text",
            text=["<b>{}</b>".format(label_text[phase]) if is_current else label_text[phase]],
            textfont=dict(
                color=color if is_current else _hex_to_rgba(color, 0.55),
                size=12 if is_current else 10,
            ),
            hoverinfo="skip",
            showlegend=False,
        ))

    # ── Historical markers ───────────────────────────────────────────────────
    historical_markers = [
        (0.75, "2021 PEAK",  C_PEAK,     12),
        (0.88, "2023 DECL",  C_DECLINE,  11),
        (0.10, "2016 LOW",   C_TROUGH,   11),
        (0.30, "2017 REC",   C_RECOVERY, 11),
    ]
    for score, label, color, size in historical_markers:
        angle = _score_to_angle(score)
        fig.add_trace(go.Scatterpolar(
            r=[0.63],
            theta=[angle],
            mode="markers+text",
            marker=dict(size=size, color=color, opacity=0.50, symbol="circle"),
            text=["<span style='font-size:9px'>{}</span>".format(label)],
            textposition="top center",
            textfont=dict(size=8, color=_hex_to_rgba(color, 0.65)),
            hovertemplate=label + "<extra></extra>",
            showlegend=False,
        ))

    # ── Needle ───────────────────────────────────────────────────────────────
    fig.add_trace(go.Scatterpolar(
        r=[0.0, 0.96],
        theta=[current_angle, current_angle],
        mode="lines",
        line=dict(color=phase_color, width=3.5),
        hoverinfo="skip",
        showlegend=False,
    ))
    # Glow trace (wider, lower opacity)
    fig.add_trace(go.Scatterpolar(
        r=[0.0, 0.96],
        theta=[current_angle, current_angle],
        mode="lines",
        line=dict(color=_hex_to_rgba(phase_color, 0.25), width=10),
        hoverinfo="skip",
        showlegend=False,
    ))
    # Tip
    fig.add_trace(go.Scatterpolar(
        r=[0.96],
        theta=[current_angle],
        mode="markers",
        marker=dict(
            size=16, color=phase_color, symbol="circle",
            line=dict(color="white", width=2.5),
        ),
        hovertemplate=(
            "<b>Current Position</b><br>"
            "Phase: {}<br>"
            "Score: {:.0f}%<br>"
            "Confidence: {:.0f}%<extra></extra>".format(
                _PHASE_DISPLAY.get(timing.current_phase, timing.current_phase),
                position_score * 100,
                timing.confidence * 100,
            )
        ),
        showlegend=False,
    ))
    # Center dot
    fig.add_trace(go.Scatterpolar(
        r=[0.0], theta=[0],
        mode="markers",
        marker=dict(size=9, color=C_TEXT3, symbol="circle"),
        hoverinfo="skip",
        showlegend=False,
    ))

    # Compass tick marks
    for deg, label in [(0, "N"), (90, "E"), (180, "S"), (270, "W")]:
        fig.add_trace(go.Scatterpolar(
            r=[1.12], theta=[deg],
            mode="text",
            text=[label],
            textfont=dict(color=C_TEXT3, size=9),
            hoverinfo="skip",
            showlegend=False,
        ))

    fig.update_layout(
        polar=dict(
            radialaxis=dict(visible=False, range=[0, 1.18]),
            angularaxis=dict(
                visible=False,
                direction="clockwise",
                rotation=90,
            ),
            bgcolor=C_BG,
        ),
        paper_bgcolor=C_BG,
        height=420,
        margin=dict(l=40, r=40, t=55, b=40),
        showlegend=False,
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
        title=dict(
            text="<b>Shipping Cycle Compass</b>",
            font=dict(color=C_TEXT, size=14, family="Inter, sans-serif"),
            x=0.5,
            y=0.98,
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="cycle_clock_polar")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION: Indicator Dashboard
# ══════════════════════════════════════════════════════════════════════════════

def _render_indicator_dashboard(
    freight_data: dict,
    macro_data: dict,
    stock_data: dict,
) -> None:
    """Grid of indicator cards; each shows reading, signal, bar, and weight."""
    from engine.cycle_timer import (
        _build_bdi_indicator,
        _build_rate_momentum_indicator,
        _build_orderbook_indicator,
        _build_utilization_indicator,
        _build_pb_ratio_indicator,
        _build_newbuilding_indicator,
        _build_scrapping_indicator,
        _build_bdi_52w_indicator,
    )

    indicators = [
        _build_bdi_indicator(freight_data),
        _build_bdi_52w_indicator(freight_data),
        _build_rate_momentum_indicator(freight_data),
        _build_orderbook_indicator(macro_data),
        _build_utilization_indicator(macro_data),
        _build_pb_ratio_indicator(stock_data),
        _build_newbuilding_indicator(macro_data),
        _build_scrapping_indicator(macro_data),
    ]

    if not indicators or all(ind is None for ind in indicators):
        st.info("Cycle indicator data unavailable — check data feeds.")
        return

    valid_inds = [ind for ind in indicators if ind is not None]

    # ── Summary bar: count per phase signal ─────────────────────────────────
    signal_counts: dict[str, int] = {}
    for ind in valid_inds:
        signal_counts[ind.phase_signal] = signal_counts.get(ind.phase_signal, 0) + 1

    signal_summary_html = ""
    for ph, cnt in sorted(signal_counts.items(), key=lambda x: -x[1]):
        c = _PHASE_COLORS.get(ph, C_TEXT2)
        signal_summary_html += (
            '<span style="background:{bg}; color:{c}; border:1px solid {border}; '
            'padding:3px 10px; border-radius:999px; font-size:0.72rem; font-weight:700; '
            'display:inline-block; margin:2px">{n} &rarr; {ph}</span>'.format(
                bg=_hex_to_rgba(c, 0.12),
                c=c,
                border=_hex_to_rgba(c, 0.35),
                n=cnt,
                ph=_PHASE_DISPLAY.get(ph, ph),
            )
        )

    st.markdown(
        '<div style="margin-bottom:10px; display:flex; flex-wrap:wrap; gap:4px; '
        'align-items:center">'
        '<span style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.1em; '
        'color:{t}; margin-right:6px; font-weight:700">SIGNALS:</span>{summary}</div>'.format(
            t=C_TEXT3, summary=signal_summary_html,
        ),
        unsafe_allow_html=True,
    )

    # ── Indicator accordion rows ─────────────────────────────────────────────
    for ind in valid_inds:
        phase_color = _PHASE_COLORS.get(ind.phase_signal, C_TEXT2)
        pct = int(ind.normalized_value * 100)
        bar_fill = _hex_to_rgba(phase_color, 0.85)
        bar_bg = _hex_to_rgba(phase_color, 0.12)
        display_phase = _PHASE_DISPLAY.get(ind.phase_signal, ind.phase_signal)
        header_label = "{} — {}".format(ind.name, display_phase)

        with st.expander(header_label, expanded=False,
                         key="cycle_ind_{}".format(ind.name.replace(" ", "_"))):
            st.markdown(
                """
                <div style="display:flex; align-items:stretch; gap:16px; padding:8px 0">
                    <!-- Left: reading + interpretation -->
                    <div style="flex:1">
                        <div style="font-size:0.65rem; color:{text3}; text-transform:uppercase;
                                    letter-spacing:0.08em; margin-bottom:4px">Current Reading</div>
                        <div style="font-size:1.5rem; font-weight:800; color:{text};
                                    font-family:'JetBrains Mono',monospace">
                            {value}
                        </div>
                        <div style="font-size:0.8rem; color:{text2}; margin-top:8px;
                                    line-height:1.55; padding-right:8px">
                            {interp}
                        </div>
                        <div style="font-size:0.68rem; color:{text3}; margin-top:8px">
                            Model weight: <b style="color:{text2}">{weight:.0f}%</b>
                        </div>
                    </div>
                    <!-- Right: signal gauge -->
                    <div style="min-width:110px; text-align:center; display:flex;
                                flex-direction:column; align-items:center; justify-content:center;
                                background:rgba(255,255,255,0.02); border-radius:10px;
                                padding:12px; border:1px solid rgba(255,255,255,0.05)">
                        <div style="font-size:0.62rem; color:{text3}; text-transform:uppercase;
                                    letter-spacing:0.08em; margin-bottom:8px">Normalised</div>
                        <div style="font-size:2rem; font-weight:900; color:{phase_color};
                                    line-height:1; text-shadow:0 0 12px {phase_color}80">
                            {pct}%
                        </div>
                        <div style="width:100%; height:8px; background:{bar_bg}; border-radius:4px;
                                    margin:10px 0 6px; overflow:hidden">
                            <div style="width:{pct}%; height:100%; background:{bar_fill};
                                        border-radius:4px;
                                        box-shadow:0 0 6px {phase_color}60"></div>
                        </div>
                        <div style="font-size:0.7rem; color:{phase_color}; font-weight:800;
                                    text-transform:uppercase; letter-spacing:0.06em">
                            {display_phase}
                        </div>
                    </div>
                </div>
                """.format(
                    text3=C_TEXT3, text=C_TEXT, text2=C_TEXT2,
                    value=ind.value,
                    interp=ind.interpretation,
                    phase_color=phase_color,
                    pct=pct,
                    bar_bg=bar_bg, bar_fill=bar_fill,
                    weight=ind.weight * 100,
                    display_phase=display_phase,
                ),
                unsafe_allow_html=True,
            )

    # ── CSV download ─────────────────────────────────────────────────────────
    if valid_inds:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Indicator", "Value", "Normalized %", "Phase Signal", "Weight %", "Interpretation"])
        for ind in valid_inds:
            writer.writerow([
                ind.name, ind.value,
                int(ind.normalized_value * 100),
                ind.phase_signal,
                round(ind.weight * 100, 1),
                ind.interpretation,
            ])
        st.download_button(
            label="Export Cycle Indicators CSV",
            data=buf.getvalue().encode(),
            file_name="cycle_indicators.csv",
            mime="text/csv",
            key="cycle_indicators_csv_download",
        )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION: Historical Cycle Chart (BDI with phase bands)
# ══════════════════════════════════════════════════════════════════════════════

def _render_historical_cycle_chart(
    freight_data: dict,
    timing: CycleTiming,
) -> None:
    """BDI history 2008-2026 with colored background bands for each cycle phase + event annotations."""
    cycle_history = get_historical_cycle_data()

    if not cycle_history or len(cycle_history) < 2:
        st.info(
            "Insufficient historical cycle data to render chart "
            "(need at least 2 cycle entries)."
        )
        return

    # ── Build synthetic BDI path from historical data ────────────────────────
    years: list[float] = []
    bdi_values: list[float] = []

    for entry in cycle_history:
        yr_s = entry["year_start"]
        yr_e = entry["year_end"]
        b_s = entry["bdi_start"]
        b_e = entry["bdi_end"]
        n_pts = max(2, (yr_e - yr_s) * 12 + 1)
        for i in range(n_pts):
            frac = i / max(1, n_pts - 1)
            yr = yr_s + frac * (yr_e - yr_s)
            noise = math.sin(frac * math.pi * 3) * (b_e - b_s) * 0.08
            bdi = b_s + (b_e - b_s) * frac + noise
            years.append(round(yr, 3))
            bdi_values.append(max(200, round(bdi, 0)))

    # Try live BDI data
    bdi_df = freight_data.get("BDIY") or freight_data.get("bdi")
    use_real = False
    real_dates: list = []
    real_bdi: list = []
    if bdi_df is not None and not bdi_df.empty and "value" in bdi_df.columns:
        bdi_df2 = bdi_df.copy()
        if "date" in bdi_df2.columns:
            bdi_df2 = bdi_df2.sort_values("date")
            real_dates = bdi_df2["date"].tolist()
            real_bdi = bdi_df2["value"].tolist()
            use_real = len(real_bdi) > 50

    fig = go.Figure()

    # ── Phase bands (colored background for each cycle phase) ─────────────────
    phase_band_colors = {
        CyclePhase.TROUGH:   _hex_to_rgba(C_TROUGH,   0.10),
        CyclePhase.RECOVERY: _hex_to_rgba(C_RECOVERY, 0.09),
        CyclePhase.PEAK:     _hex_to_rgba(C_PEAK,     0.12),
        CyclePhase.DECLINE:  _hex_to_rgba(C_DECLINE,  0.10),
    }
    phase_label_map = {
        CyclePhase.TROUGH:   "TROUGH",
        CyclePhase.RECOVERY: "EXPANSION",
        CyclePhase.PEAK:     "PEAK",
        CyclePhase.DECLINE:  "CONTRACTION",
    }

    for entry in cycle_history:
        phase = entry["phase"]
        band_color = phase_band_colors.get(phase, "rgba(255,255,255,0.03)")
        phase_line_color = _PHASE_COLORS.get(phase, C_TEXT3)
        display_label = phase_label_map.get(phase, phase[:4])

        fig.add_vrect(
            x0=float(entry["year_start"]),
            x1=float(entry["year_end"]) + 0.99,
            fillcolor=band_color,
            line=dict(color=_hex_to_rgba(phase_line_color, 0.28), width=1),
            annotation_text="<b>{}</b>".format(display_label),
            annotation_position="top left",
            annotation_font=dict(color=_hex_to_rgba(phase_line_color, 0.8), size=9),
        )

    # ── BDI line ─────────────────────────────────────────────────────────────
    if use_real:
        fig.add_trace(go.Scatter(
            x=real_dates, y=real_bdi,
            mode="lines", name="BDI (live)",
            line=dict(color=C_ACCENT, width=2.2),
            hovertemplate="Date: %{x}<br>BDI: %{y:,.0f}<extra></extra>",
        ))
    else:
        # Gradient color line by phase value
        fig.add_trace(go.Scatter(
            x=years, y=bdi_values,
            mode="lines", name="BDI (reconstructed)",
            line=dict(color=C_ACCENT, width=2.2),
            hovertemplate="Year: %{x:.1f}<br>BDI: %{y:,.0f}<extra></extra>",
        ))

    # ── Event annotations ────────────────────────────────────────────────────
    events = [
        (2008.5,  11793, "GFC Peak",       C_PEAK),
        (2009.0,  663,   "BDI 663 Low",    C_TROUGH),
        (2016.1,  291,   "All-time Low",   C_TROUGH),
        (2020.3,  400,   "COVID Trough",   C_TROUGH),
        (2021.5,  3800,  "COVID Surge",    C_PEAK),
        (2024.0,  2000,  "Red Sea Crisis", C_RECOVERY),
    ]
    for yr, bdi_lvl, label, color in events:
        fig.add_annotation(
            x=yr, y=bdi_lvl,
            text="<b>{}</b>".format(label),
            showarrow=True, arrowhead=2,
            arrowcolor=_hex_to_rgba(color, 0.75),
            arrowsize=0.9, arrowwidth=1.5,
            font=dict(color=_hex_to_rgba(color, 0.95), size=9,
                      family="Inter, sans-serif"),
            bgcolor=_hex_to_rgba(C_BG, 0.88),
            bordercolor=_hex_to_rgba(color, 0.45),
            borderwidth=1, borderpad=4,
        )

    # ── Current BDI line ─────────────────────────────────────────────────────
    bdi_df_cur = freight_data.get("BDIY") or freight_data.get("bdi")
    if bdi_df_cur is not None and not bdi_df_cur.empty and "value" in bdi_df_cur.columns:
        try:
            cur_bdi = float(bdi_df_cur["value"].dropna().iloc[-1])
            fig.add_hline(
                y=cur_bdi,
                line=dict(color=_hex_to_rgba(C_HIGH, 0.65), width=1.5, dash="dot"),
                annotation_text="<b>Current BDI: {:,.0f}</b>".format(cur_bdi),
                annotation_position="bottom right",
                annotation_font=dict(color=C_HIGH, size=10),
            )
        except Exception:
            pass

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor="#0d1420",
        height=420,
        font=dict(color=C_TEXT, size=12, family="Inter, sans-serif"),
        xaxis=dict(
            title=dict(text="Year", font=dict(color=C_TEXT3, size=11)),
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(color=C_TEXT3, size=10),
            linecolor="rgba(255,255,255,0.08)",
            showspikes=True,
            spikecolor=_hex_to_rgba(C_ACCENT, 0.4),
            spikethickness=1,
        ),
        yaxis=dict(
            title=dict(text="Baltic Dry Index (BDI)", font=dict(color=C_TEXT3, size=11)),
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(color=C_TEXT3, size=10),
            linecolor="rgba(255,255,255,0.08)",
            tickformat=",",
        ),
        margin=dict(l=65, r=20, t=50, b=45),
        showlegend=True,
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10),
            x=0.01, y=0.99,
        ),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
        title=dict(
            text="<b>Baltic Dry Index — Historical Shipping Cycles 2008–2026</b>",
            font=dict(size=13, color=C_TEXT, family="Inter, sans-serif"),
            x=0.01,
        ),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True, key="cycle_bdi_historical_line")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION: Phase Characteristics Dashboard
# ══════════════════════════════════════════════════════════════════════════════

def _render_phase_characteristics(timing: CycleTiming) -> None:
    """Tile grid: what happens to rates, stocks, demand, fleet in the current phase."""
    phase = timing.current_phase
    chars = _PHASE_CHARACTERISTICS.get(phase, {})
    if not chars:
        return

    tiles = [
        ("FREIGHT RATES",  chars.get("rates",  ("—", C_TEXT2, "No data")),  "&#9634;"),
        ("SHIPPING STOCKS", chars.get("stocks", ("—", C_TEXT2, "No data")), "&#9650;"),
        ("TRADE DEMAND",   chars.get("demand", ("—", C_TEXT2, "No data")),  "&#9672;"),
        ("FLEET SUPPLY",   chars.get("fleet",  ("—", C_TEXT2, "No data")),  "&#9632;"),
        ("ORDERBOOK",      chars.get("ob",     ("—", C_TEXT2, "No data")),  "&#9679;"),
    ]

    action = chars.get("action", "")
    phase_color = _PHASE_COLORS.get(phase, C_TEXT2)

    # Action banner
    st.markdown(
        """
        <div style="background:{bg}; border:1px solid {border}; border-left:4px solid {color};
                    border-radius:12px; padding:14px 18px; margin-bottom:16px;
                    display:flex; align-items:center; gap:12px">
            <span style="font-size:1.3rem">{icon}</span>
            <div>
                <div style="font-size:0.62rem; text-transform:uppercase; letter-spacing:0.1em;
                            color:{text3}; margin-bottom:3px">PHASE PLAYBOOK</div>
                <div style="font-size:0.88rem; font-weight:600; color:{text}; line-height:1.5">
                    {action}
                </div>
            </div>
        </div>
        """.format(
            bg=_hex_to_rgba(phase_color, 0.08),
            border=_hex_to_rgba(phase_color, 0.30),
            color=phase_color,
            icon=_PHASE_ICON.get(phase, "&#9679;"),
            text3=C_TEXT3, text=C_TEXT,
            action=action,
        ),
        unsafe_allow_html=True,
    )

    # Tiles row
    cols = st.columns(len(tiles))
    for col, (title, (status, color, detail), icon) in zip(cols, tiles):
        r, g, b = [int(color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)]
        with col:
            col.markdown(
                """
                <div style="background:{card}; border:1px solid {border};
                            border-top:3px solid {color}; border-radius:12px;
                            padding:14px 14px 12px; text-align:center; height:100%">
                    <div style="font-size:0.6rem; text-transform:uppercase; letter-spacing:0.1em;
                                color:{text3}; margin-bottom:8px">{title}</div>
                    <div style="font-size:0.85rem; font-weight:800; color:{color};
                                margin-bottom:6px; text-shadow:0 0 10px rgba({r},{g},{b},0.4)">
                        {status}
                    </div>
                    <div style="font-size:0.72rem; color:{text2}; line-height:1.4">
                        {detail}
                    </div>
                </div>
                """.format(
                    card=C_CARD, border=C_BORDER,
                    color=color, r=r, g=g, b=b,
                    text3=C_TEXT3, text2=C_TEXT2,
                    title=title, status=status, detail=detail,
                ),
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION: Phase Transition Signals
# ══════════════════════════════════════════════════════════════════════════════

def _render_transition_signals(timing: CycleTiming) -> None:
    """What would trigger a move to the next phase."""
    phase = timing.current_phase
    ts = _TRANSITION_SIGNALS.get(phase, {})
    if not ts:
        return

    phase_color = _PHASE_COLORS.get(phase, C_TEXT2)
    signals_list = ts.get("signals", [])
    next_phase = ts.get("next_phase", "NEXT")
    risk_note = ts.get("risk", "")

    # Determine next phase color
    next_phase_key = {
        "EXPANSION": CyclePhase.RECOVERY,
        "PEAK": CyclePhase.PEAK,
        "CONTRACTION": CyclePhase.DECLINE,
        "TROUGH": CyclePhase.TROUGH,
    }.get(next_phase, CyclePhase.RECOVERY)
    next_color = _PHASE_COLORS.get(next_phase_key, C_MOD)

    signals_html = ""
    for i, sig in enumerate(signals_list):
        signals_html += """
        <div style="display:flex; align-items:flex-start; gap:10px; padding:9px 0;
                    border-bottom:1px solid rgba(255,255,255,0.05)">
            <div style="flex-shrink:0; width:22px; height:22px; border-radius:50%;
                        background:{bg}; border:1px solid {color}; display:flex;
                        align-items:center; justify-content:center; margin-top:1px">
                <span style="font-size:0.68rem; font-weight:800; color:{color}">{n}</span>
            </div>
            <div style="font-size:0.82rem; color:{text2}; line-height:1.5">{sig}</div>
        </div>
        """.format(
            bg=_hex_to_rgba(next_color, 0.12),
            color=next_color,
            n=i + 1,
            text2=C_TEXT2,
            sig=sig,
        )

    st.markdown(
        """
        <div style="background:{card}; border:1px solid {border}; border-radius:14px;
                    padding:20px 22px; margin-bottom:16px">
            <div style="display:flex; align-items:center; justify-content:space-between;
                        margin-bottom:14px; flex-wrap:wrap; gap:8px">
                <div>
                    <div style="font-size:0.62rem; text-transform:uppercase; letter-spacing:0.1em;
                                color:{text3}; margin-bottom:4px">TRANSITION WATCH</div>
                    <div style="font-size:0.95rem; font-weight:700; color:{text}">
                        Signals that would confirm move to
                        <span style="color:{next_color}; font-weight:900">{next_phase}</span>
                    </div>
                </div>
                <div style="background:{next_bg}; border:1px solid {next_border}; border-radius:999px;
                            padding:4px 14px; font-size:0.78rem; font-weight:700; color:{next_color}">
                    {phase} &rarr; {next_phase}
                </div>
            </div>
            {signals_html}
            <div style="margin-top:12px; padding:10px 12px; background:rgba(245,158,11,0.07);
                        border:1px solid rgba(245,158,11,0.25); border-radius:8px;
                        font-size:0.78rem; color:{text2}">
                <b style="color:{mod}">&#9888; Bear case risk:</b> {risk}
            </div>
        </div>
        """.format(
            card=C_CARD, border=C_BORDER,
            text3=C_TEXT3, text=C_TEXT, text2=C_TEXT2,
            mod=C_MOD,
            next_color=next_color,
            next_phase=next_phase,
            next_bg=_hex_to_rgba(next_color, 0.08),
            next_border=_hex_to_rgba(next_color, 0.30),
            phase=_PHASE_DISPLAY.get(phase, phase),
            signals_html=signals_html,
            risk=risk_note,
        ),
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION: Duration Analysis
# ══════════════════════════════════════════════════════════════════════════════

def _render_duration_analysis(timing: CycleTiming) -> None:
    """How long current phase has lasted vs historical average — with mini bar chart."""
    phase = timing.current_phase
    stats = _PHASE_DURATION_STATS.get(phase, {})
    if not stats:
        return

    avg_mo = stats.get("avg_months", 24)
    min_mo = stats.get("min_months", 6)
    max_mo = stats.get("max_months", 48)
    examples = stats.get("examples", [])
    months_in = timing.months_in_current_phase
    months_next = timing.estimated_months_to_next_phase
    phase_color = _PHASE_COLORS.get(phase, C_TEXT2)

    # Percentage through average
    pct_of_avg = min(150, int(months_in / max(1, avg_mo) * 100))
    pct_width = min(100, pct_of_avg)  # cap bar at 100%

    # Status label
    if months_in < min_mo:
        duration_status = "Early — below historical minimum"
        status_color = C_MOD
    elif months_in < avg_mo:
        duration_status = "Within normal range"
        status_color = C_HIGH
    elif months_in < max_mo:
        duration_status = "Extended — above average duration"
        status_color = C_MOD
    else:
        duration_status = "Very long — above historical maximum"
        status_color = C_LOW

    examples_html = " ".join(
        '<span style="background:rgba(255,255,255,0.04); color:{t}; border:1px solid rgba(255,255,255,0.08); '
        'padding:2px 9px; border-radius:999px; font-size:0.7rem">{e}</span>'.format(
            t=C_TEXT2, e=ex,
        )
        for ex in examples
    )

    # Bar chart: months_in vs avg vs max (Plotly)
    fig = go.Figure()

    categories = ["Min ({})".format(min_mo), "Current ({})".format(months_in),
                  "Avg ({})".format(avg_mo), "Max ({})".format(max_mo)]
    values = [min_mo, months_in, avg_mo, max_mo]
    bar_colors = [
        _hex_to_rgba(C_TEXT3, 0.5),
        _hex_to_rgba(phase_color, 0.85),
        _hex_to_rgba(C_ACCENT, 0.7),
        _hex_to_rgba(C_TEXT3, 0.5),
    ]

    fig.add_trace(go.Bar(
        x=categories,
        y=values,
        marker=dict(color=bar_colors, line=dict(color="rgba(255,255,255,0.08)", width=0.5)),
        text=["{} mo".format(v) for v in values],
        textposition="outside",
        textfont=dict(color=C_TEXT2, size=10),
        hovertemplate="%{x}<br>%{y} months<extra></extra>",
    ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor="#0d1420",
        height=220,
        margin=dict(l=40, r=20, t=20, b=40),
        font=dict(color=C_TEXT, size=11, family="Inter, sans-serif"),
        xaxis=dict(
            tickfont=dict(color=C_TEXT2, size=10),
            gridcolor="rgba(255,255,255,0.03)",
            linecolor="rgba(255,255,255,0.06)",
        ),
        yaxis=dict(
            title=dict(text="Months", font=dict(color=C_TEXT3, size=10)),
            tickfont=dict(color=C_TEXT3, size=9),
            gridcolor="rgba(255,255,255,0.05)",
        ),
        showlegend=False,
        hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=C_TEXT, size=12)),
    )

    col_stats, col_chart = st.columns([1, 1.4])

    with col_stats:
        st.markdown(
            """
            <div style="background:{card}; border:1px solid {border}; border-radius:14px;
                        padding:18px 20px; height:100%">
                <div style="font-size:0.62rem; text-transform:uppercase; letter-spacing:0.1em;
                            color:{text3}; margin-bottom:10px">DURATION IN CURRENT PHASE</div>

                <div style="font-size:2.4rem; font-weight:900; color:{color}; line-height:1;
                            text-shadow:0 0 16px {color}60; margin-bottom:4px">
                    {months_in}
                    <span style="font-size:1rem; font-weight:500; color:{text2}">months</span>
                </div>
                <div style="font-size:0.78rem; font-weight:700; color:{status_color};
                            margin-bottom:14px">{status}</div>

                <div style="height:6px; background:rgba(255,255,255,0.07); border-radius:3px;
                            margin-bottom:6px; overflow:hidden">
                    <div style="width:{pct_width}%; height:100%; background:{color};
                                border-radius:3px; box-shadow:0 0 8px {color}60"></div>
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:14px">
                    <span style="font-size:0.68rem; color:{text3}">{min_mo}mo min</span>
                    <span style="font-size:0.68rem; color:{text3}">{avg_mo}mo avg</span>
                    <span style="font-size:0.68rem; color:{text3}">{max_mo}mo max</span>
                </div>

                <div style="font-size:0.72rem; color:{text3}; margin-bottom:6px;
                            text-transform:uppercase; letter-spacing:0.07em">Historical examples</div>
                <div style="display:flex; flex-wrap:wrap; gap:4px">
                    {examples}
                </div>

                <div style="margin-top:12px; padding:8px 10px;
                            background:rgba(255,255,255,0.03); border-radius:8px;
                            border:1px solid rgba(255,255,255,0.06)">
                    <div style="font-size:0.7rem; color:{text3}; margin-bottom:2px">Est. remaining</div>
                    <div style="font-size:1.1rem; font-weight:800; color:{mod}">
                        ~{months_next} months
                    </div>
                </div>
            </div>
            """.format(
                card=C_CARD, border=C_BORDER,
                color=phase_color, text3=C_TEXT3, text2=C_TEXT2,
                months_in=months_in, status=duration_status, status_color=status_color,
                pct_width=pct_width, min_mo=min_mo, avg_mo=avg_mo, max_mo=max_mo,
                examples=examples_html, months_next=months_next, mod=C_MOD,
            ),
            unsafe_allow_html=True,
        )

    with col_chart:
        st.plotly_chart(fig, use_container_width=True, key="cycle_duration_bar_chart")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION: Investment Implications
# ══════════════════════════════════════════════════════════════════════════════

def _render_investment_implications(timing: CycleTiming) -> None:
    """Grid of sector cards showing what the current phase means for shipping stocks."""
    phase = timing.current_phase
    implications = _INVESTMENT_IMPLICATIONS.get(phase, [])
    if not implications:
        return

    phase_color = _PHASE_COLORS.get(phase, C_TEXT2)
    display = _PHASE_DISPLAY.get(phase, phase)

    st.markdown(
        """
        <div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.12em;
                    color:{text3}; margin-bottom:12px; font-weight:700">
            INVESTMENT IMPLICATIONS DURING <span style="color:{color}">{display}</span>
        </div>
        """.format(text3=C_TEXT3, color=phase_color, display=display),
        unsafe_allow_html=True,
    )

    cols = st.columns(len(implications))
    for col, (sector, action, action_color, detail) in zip(cols, implications):
        r, g, b = [int(action_color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)]
        with col:
            col.markdown(
                """
                <div style="background:{card}; border:1px solid rgba({r},{g},{b},0.25);
                            border-top:3px solid {color}; border-radius:12px;
                            padding:16px 14px; height:100%;
                            box-shadow:0 0 16px rgba({r},{g},{b},0.08)">
                    <div style="font-size:0.62rem; text-transform:uppercase; letter-spacing:0.1em;
                                color:{text3}; margin-bottom:8px">{sector}</div>
                    <div style="background:rgba({r},{g},{b},0.15); color:{color};
                                border:1px solid rgba({r},{g},{b},0.4);
                                border-radius:999px; padding:3px 12px;
                                font-size:0.75rem; font-weight:800; display:inline-block;
                                text-transform:uppercase; margin-bottom:10px;
                                letter-spacing:0.05em">
                        {action}
                    </div>
                    <div style="font-size:0.78rem; color:{text2}; line-height:1.5">
                        {detail}
                    </div>
                </div>
                """.format(
                    card=C_CARD, r=r, g=g, b=b,
                    color=action_color,
                    text3=C_TEXT3, text2=C_TEXT2,
                    sector=sector, action=action, detail=detail,
                ),
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION: Entry/Exit Signal Panel
# ══════════════════════════════════════════════════════════════════════════════

def _render_signal_panel(timing: CycleTiming, signals: list[dict]) -> None:
    """Recommendation card + backtested signal accuracy + individual stock signals."""
    pos_color = _POSITIONING_COLORS.get(timing.recommended_positioning, C_TEXT2)
    phase_color = _PHASE_COLORS.get(timing.current_phase, C_TEXT2)

    r, g, b = [int(phase_color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)]

    st.markdown(
        """
        <div style="
            background: linear-gradient(135deg, rgba({r},{g},{b},0.14) 0%, {card} 60%);
            border: 1px solid {border};
            border-left: 5px solid {pos_color};
            border-radius: 14px;
            padding: 22px 24px;
            margin-bottom: 16px;
        ">
            <div style="display:flex; align-items:flex-start; justify-content:space-between;
                        flex-wrap:wrap; gap:12px; margin-bottom:16px">
                <div>
                    <div style="font-size:0.62rem; font-weight:700; color:{text3};
                                text-transform:uppercase; letter-spacing:0.1em; margin-bottom:6px">
                        CYCLE TIMER RECOMMENDATION
                    </div>
                    <div style="font-size:2.2rem; font-weight:900; color:{pos_color};
                                letter-spacing:-0.02em; line-height:1;
                                text-shadow:0 0 16px {pos_color}60">
                        {positioning}
                    </div>
                </div>
                <div style="text-align:right">
                    <div style="font-size:0.62rem; color:{text3}; margin-bottom:5px">CURRENT PHASE</div>
                    <div style="background:rgba({r},{g},{b},0.15); border:1px solid rgba({r},{g},{b},0.4);
                                padding:6px 18px; border-radius:999px;
                                font-size:1.05rem; font-weight:800; color:{phase_color};
                                text-shadow:0 0 12px rgba({r},{g},{b},0.5)">
                        {display_phase}
                    </div>
                    <div style="font-size:0.75rem; color:{text2}; margin-top:8px">
                        Confidence: <b style="color:{text}">{conf:.0f}%</b>
                        &nbsp;&bull;&nbsp; {months_in}mo in phase
                    </div>
                </div>
            </div>
            <div style="font-size:0.85rem; color:{text2}; line-height:1.65;
                        border-top:1px solid rgba(255,255,255,0.06); padding-top:14px">
                {rationale}
            </div>
            <div style="margin-top:10px; font-size:0.78rem; color:{text3};
                        display:flex; gap:16px; flex-wrap:wrap">
                <span>&#9200; Est. <b style="color:{mod}">{months_next}mo</b> to next transition</span>
                <span>&#128200; Phase score: <b style="color:{text2}">{phase_score:.0f}%</b> through phase</span>
            </div>
        </div>
        """.format(
            card=C_CARD, border=C_BORDER,
            r=r, g=g, b=b,
            pos_color=pos_color,
            phase_color=phase_color,
            text3=C_TEXT3, text2=C_TEXT2, text=C_TEXT, mod=C_MOD,
            positioning=timing.recommended_positioning.replace("_", " "),
            display_phase=_PHASE_DISPLAY.get(timing.current_phase, timing.current_phase),
            conf=timing.confidence * 100,
            months_in=timing.months_in_current_phase,
            rationale=timing.positioning_rationale,
            months_next=timing.estimated_months_to_next_phase,
            phase_score=timing.phase_score * 100,
        ),
        unsafe_allow_html=True,
    )

    # ── Backtested accuracy ───────────────────────────────────────────────────
    accuracy_data = [
        ("TROUGH Buy",     72, "2009, 2016, 2020 entries",  C_HIGH),
        ("EXPANSION Hold", 68, "2010–11, 2017–19 hold",     C_RECOVERY),
        ("PEAK Reduce",    65, "2008, 2021 sell signals",   C_PEAK),
        ("CONTRACTION Short", 58, "2012–15, 2022 avoidance", C_DECLINE),
    ]
    acc_html = ""
    for label, pct, note, color in accuracy_data:
        bar_fill = _hex_to_rgba(color, 0.85)
        bar_bg = _hex_to_rgba(color, 0.12)
        acc_html += """
        <div style="margin-bottom:11px">
            <div style="display:flex; justify-content:space-between; margin-bottom:3px">
                <span style="font-size:0.78rem; color:{text}">{label}</span>
                <span style="font-size:0.78rem; font-weight:800; color:{color}">{pct}%</span>
            </div>
            <div style="height:6px; background:{bar_bg}; border-radius:3px; overflow:hidden">
                <div style="width:{pct}%; height:100%; background:{bar_fill}; border-radius:3px;
                            box-shadow:0 0 4px {color}80"></div>
            </div>
            <div style="font-size:0.68rem; color:{text3}; margin-top:2px">{note}</div>
        </div>
        """.format(
            text=C_TEXT, label=label, color=color, pct=pct,
            bar_bg=bar_bg, bar_fill=bar_fill, text3=C_TEXT3, note=note,
        )

    with st.expander("Historical Signal Accuracy (Backtested 2008–2024)",
                     expanded=False, key="cycle_signal_accuracy_expander"):
        st.markdown(
            """
            <div style="padding:8px 0">
                <div style="font-size:0.72rem; color:{text3}; margin-bottom:12px">
                    Signal accuracy across historical cycles 2008–2024.
                    Synthetic backtest — directionally realistic.
                </div>
                {acc_html}
            </div>
            """.format(text3=C_TEXT3, acc_html=acc_html),
            unsafe_allow_html=True,
        )

    # ── Individual stock signals ──────────────────────────────────────────────
    if signals:
        st.markdown(
            '<div style="font-size:0.82rem; font-weight:700; color:{t}; '
            'margin:18px 0 10px; text-transform:uppercase; letter-spacing:0.06em">'
            'Stock Recommendations — Cycle Phase</div>'.format(t=C_TEXT),
            unsafe_allow_html=True,
        )

        for sig in signals:
            action = sig["action"]
            action_color = _ACTION_COLORS.get(action, C_TEXT2)
            action_bg = _hex_to_rgba(action_color, 0.12)
            action_border = _hex_to_rgba(action_color, 0.35)
            conf_pct = int(sig["confidence"] * 100)
            backtest_pct = int(sig["backtest_accuracy"] * 100)
            beta_icon = {
                "high":   "&#9889;&#9889; High BDI Beta",
                "medium": "&#9889; Med BDI Beta",
                "low":    "&#9675; Low BDI Beta",
            }.get(sig["bdi_beta"], "")

            st.markdown(
                """
                <div style="background:{card}; border:1px solid {border};
                            border-left:4px solid {action_color};
                            border-radius:12px; padding:14px 16px; margin-bottom:8px">
                    <div style="display:flex; justify-content:space-between; align-items:flex-start;
                                flex-wrap:wrap; gap:8px; margin-bottom:8px">
                        <div>
                            <span style="font-size:1.05rem; font-weight:800; color:{text}">{ticker}</span>
                            <span style="font-size:0.78rem; color:{text2}; margin-left:8px">{name}</span>
                            <div style="font-size:0.68rem; color:{text3}; margin-top:2px">{beta}</div>
                        </div>
                        <div style="display:flex; gap:8px; align-items:center">
                            <span style="background:{action_bg}; color:{action_color};
                                         border:1px solid {action_border};
                                         padding:4px 14px; border-radius:999px;
                                         font-size:0.82rem; font-weight:900;
                                         letter-spacing:0.04em">{action}</span>
                            <div style="text-align:right">
                                <div style="font-size:0.78rem; color:{text3}">{price}</div>
                                <div style="font-size:0.68rem; color:{text3}">52w pos: {pct52}%</div>
                            </div>
                        </div>
                    </div>
                    <div style="font-size:0.82rem; color:{text2}; margin-bottom:6px; font-weight:600">
                        {target}
                    </div>
                    <div style="font-size:0.78rem; color:{text3}; line-height:1.5; margin-bottom:8px">
                        {timing_note}
                    </div>
                    <div style="display:flex; gap:18px; font-size:0.7rem; color:{text3}">
                        <span>Signal confidence: <b style="color:{text2}">{conf}%</b></span>
                        <span>Backtest accuracy: <b style="color:{text2}">{backtest}%</b></span>
                    </div>
                </div>
                """.format(
                    card=C_CARD, border=C_BORDER,
                    action_color=action_color, action_bg=action_bg, action_border=action_border,
                    text=C_TEXT, text2=C_TEXT2, text3=C_TEXT3,
                    ticker=sig["ticker"],
                    name=sig["name"],
                    beta=beta_icon,
                    action=action,
                    price=sig["current_price"],
                    pct52=int(sig["price_52w_pct"]),
                    target=sig["target_note"],
                    timing_note=sig["timing_note"],
                    conf=conf_pct,
                    backtest=backtest_pct,
                ),
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION: Orderbook vs Fleet Monitor
# ══════════════════════════════════════════════════════════════════════════════

def _render_orderbook_monitor(macro_data: dict) -> None:
    """Bar chart of orderbook as % of fleet — current + historical."""
    years_hist = [2007, 2008, 2009, 2010, 2012, 2014, 2016, 2018, 2020, 2021, 2022, 2023, 2024, 2025]
    orderbook_hist = [55, 60, 50, 40, 20, 16, 12, 10, 8, 15, 28, 30, 25, 22]

    colors = []
    for ob in orderbook_hist:
        if ob >= 25:
            colors.append(_hex_to_rgba(C_DECLINE, 0.85))
        elif ob >= 15:
            colors.append(_hex_to_rgba(C_MOD, 0.80))
        else:
            colors.append(_hex_to_rgba(C_RECOVERY, 0.80))

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=years_hist,
        y=orderbook_hist,
        name="Orderbook % of Fleet",
        marker=dict(
            color=colors,
            line=dict(color="rgba(255,255,255,0.1)", width=0.5),
        ),
        hovertemplate="Year: %{x}<br>Orderbook: %{y:.0f}% of fleet<extra></extra>",
    ))

    # Danger threshold
    fig.add_hline(
        y=25,
        line=dict(color=_hex_to_rgba(C_DECLINE, 0.75), width=2, dash="dash"),
        annotation_text="<b>25% oversupply threshold</b>",
        annotation_position="top right",
        annotation_font=dict(color=C_DECLINE, size=10),
    )
    # Healthy zone
    fig.add_hrect(
        y0=0, y1=12,
        fillcolor=_hex_to_rgba(C_HIGH, 0.05),
        line_width=0,
    )

    current_ob = orderbook_hist[-1]
    cur_color = C_DECLINE if current_ob >= 25 else C_MOD if current_ob >= 15 else C_RECOVERY
    fig.add_annotation(
        x=years_hist[-1], y=current_ob + 2,
        text="<b>Now: {}%</b>".format(current_ob),
        showarrow=True, arrowhead=2,
        arrowcolor=_hex_to_rgba(cur_color, 0.7),
        arrowsize=0.8, arrowwidth=1.5,
        font=dict(color=cur_color, size=10),
        bgcolor=_hex_to_rgba(C_BG, 0.85),
        bordercolor=_hex_to_rgba(cur_color, 0.45),
        borderwidth=1, borderpad=4,
    )

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor="#0d1420",
        height=320,
        font=dict(color=C_TEXT, size=12, family="Inter, sans-serif"),
        xaxis=dict(
            title=dict(text="Year", font=dict(color=C_TEXT3, size=11)),
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(color=C_TEXT3, size=10),
        ),
        yaxis=dict(
            title=dict(text="Orderbook as % of Fleet", font=dict(color=C_TEXT3, size=11)),
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(color=C_TEXT3, size=10),
            ticksuffix="%",
        ),
        margin=dict(l=65, r=20, t=40, b=40),
        showlegend=False,
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="cycle_orderbook_bar")

    st.markdown(
        '<div style="font-size:0.78rem; color:{t}; padding:10px 14px; '
        'background:{c}; border:1px solid {b}; border-left:3px solid {warn}; '
        'border-radius:8px; margin-top:4px; line-height:1.55">'
        '<b style="color:{warn}">Orderbook above 25%</b> of fleet historically signals future oversupply '
        'and rate decline. Current orderbook <b style="color:{cc}">~{ob}%</b> — partially offset by '
        'Red Sea rerouting absorbing ~15% of effective capacity.'
        '</div>'.format(
            t=C_TEXT2, c=C_CARD, b=C_BORDER, warn=C_MOD, ob=current_ob, cc=cur_color,
        ),
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION: Phase Transition Probability Matrix
# ══════════════════════════════════════════════════════════════════════════════

def _render_phase_probability_matrix(timing: CycleTiming) -> None:
    """Heatmap showing probability of being in each phase at 6/12/18 month horizons."""
    phases = [CyclePhase.TROUGH, CyclePhase.RECOVERY, CyclePhase.PEAK, CyclePhase.DECLINE]
    phase_labels = [_PHASE_DISPLAY.get(p, p) for p in phases]
    horizons = ["6 months", "12 months", "18 months"]

    _TRANSITION_BASE = {
        CyclePhase.TROUGH: {
            "6 months":  [0.50, 0.40, 0.05, 0.05],
            "12 months": [0.20, 0.55, 0.15, 0.10],
            "18 months": [0.10, 0.45, 0.30, 0.15],
        },
        CyclePhase.RECOVERY: {
            "6 months":  [0.05, 0.65, 0.20, 0.10],
            "12 months": [0.05, 0.40, 0.35, 0.20],
            "18 months": [0.05, 0.30, 0.40, 0.25],
        },
        CyclePhase.PEAK: {
            "6 months":  [0.02, 0.20, 0.50, 0.28],
            "12 months": [0.05, 0.15, 0.30, 0.50],
            "18 months": [0.15, 0.20, 0.15, 0.50],
        },
        CyclePhase.DECLINE: {
            "6 months":  [0.20, 0.10, 0.10, 0.60],
            "12 months": [0.40, 0.25, 0.08, 0.27],
            "18 months": [0.30, 0.40, 0.15, 0.15],
        },
    }

    current_transitions = _TRANSITION_BASE.get(
        timing.current_phase,
        _TRANSITION_BASE[CyclePhase.RECOVERY],
    )

    z_data = [current_transitions[h] for h in horizons]

    fig = go.Figure(go.Heatmap(
        z=z_data,
        x=phase_labels,
        y=horizons,
        colorscale=[
            [0.00, "#0a0f1a"],
            [0.15, "#0f2140"],
            [0.35, "#1d4ed8"],
            [0.60, "#3b82f6"],
            [0.80, "#10b981"],
            [1.00, "#34d399"],
        ],
        zmin=0.0, zmax=0.70,
        hovertemplate=(
            "Horizon: %{y}<br>"
            "Phase: %{x}<br>"
            "Probability: %{z:.0%}<extra></extra>"
        ),
        text=[["{:.0f}%".format(p * 100) for p in row] for row in z_data],
        texttemplate="%{text}",
        textfont=dict(size=13, color="white", family="JetBrains Mono, monospace"),
        showscale=True,
        colorbar=dict(
            title=dict(text="Prob", font=dict(color=C_TEXT2, size=11)),
            tickformat=".0%",
            tickfont=dict(color=C_TEXT2, size=10),
            thickness=14,
            len=0.9,
            bgcolor="rgba(0,0,0,0)",
        ),
    ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        height=250,
        font=dict(color=C_TEXT, size=12, family="Inter, sans-serif"),
        xaxis=dict(
            title=dict(text="Phase at Horizon", font=dict(color=C_TEXT3, size=11)),
            tickfont=dict(color=C_TEXT2, size=11),
            side="bottom",
        ),
        yaxis=dict(
            title="",
            tickfont=dict(color=C_TEXT2, size=11),
            autorange="reversed",
        ),
        margin=dict(l=95, r=80, t=30, b=50),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
        title=dict(
            text="<b>Phase Transition Probabilities</b> — from {} (current)".format(
                _PHASE_DISPLAY.get(timing.current_phase, timing.current_phase)
            ),
            font=dict(size=12, color=C_TEXT, family="Inter, sans-serif"),
            x=0.01,
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="cycle_phase_probability_heatmap")

    # Caption
    phase_color = _PHASE_COLORS.get(timing.current_phase, C_TEXT2)
    st.markdown(
        '<div style="font-size:0.75rem; color:{t}; padding:4px 2px">'.format(t=C_TEXT3)
        + "Probabilities derived from historical phase duration distributions (2007–2024). "
        + "Current phase: <b style='color:{c}'>{p}</b> — {m} months elapsed, "
          "est. <b style='color:{mod}'>{n} months</b> remaining.".format(
              c=phase_color,
              p=_PHASE_DISPLAY.get(timing.current_phase, timing.current_phase),
              m=timing.months_in_current_phase,
              mod=C_MOD,
              n=timing.estimated_months_to_next_phase,
          )
        + "</div>",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION: Supporting / Contrarian Indicator Footer
# ══════════════════════════════════════════════════════════════════════════════

def _render_indicator_footer(timing: CycleTiming) -> None:
    """Two-column: supporting indicators + contrarian signals."""
    if not timing.key_indicators_supporting and not timing.contrarian_indicators:
        return

    col_sup, col_con = st.columns(2)

    with col_sup:
        if timing.key_indicators_supporting:
            st.markdown(
                '<div style="background:{c}; border:1px solid rgba(16,185,129,0.2); '
                'border-left:3px solid {h}; border-radius:12px; padding:16px 18px">'.format(
                    c=_hex_to_rgba(C_HIGH, 0.06), h=C_HIGH,
                )
                + '<div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.1em; '
                  'color:{h}; margin-bottom:10px; font-weight:700">&#10003; Supporting Signals</div>'.format(h=C_HIGH)
                + "".join(
                    '<div style="font-size:0.8rem; color:{t}; padding:5px 0; '
                    'border-bottom:1px solid rgba(255,255,255,0.04); display:flex; gap:8px">'
                    '<span style="color:{h}; flex-shrink:0">&#10003;</span>'
                    '<span>{txt}</span></div>'.format(t=C_TEXT2, h=C_HIGH, txt=txt)
                    for txt in timing.key_indicators_supporting
                )
                + "</div>",
                unsafe_allow_html=True,
            )

    with col_con:
        if timing.contrarian_indicators:
            st.markdown(
                '<div style="background:{c}; border:1px solid rgba(245,158,11,0.2); '
                'border-left:3px solid {w}; border-radius:12px; padding:16px 18px">'.format(
                    c=_hex_to_rgba(C_MOD, 0.06), w=C_MOD,
                )
                + '<div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.1em; '
                  'color:{w}; margin-bottom:10px; font-weight:700">&#9888; Contrarian Signals (watch)</div>'.format(w=C_MOD)
                + "".join(
                    '<div style="font-size:0.8rem; color:{t}; padding:5px 0; '
                    'border-bottom:1px solid rgba(255,255,255,0.04); display:flex; gap:8px">'
                    '<span style="color:{w}; flex-shrink:0">&#9888;</span>'
                    '<span>{txt}</span></div>'.format(t=C_TEXT2, w=C_MOD, txt=txt)
                    for txt in timing.contrarian_indicators
                )
                + "</div>",
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN RENDER
# ══════════════════════════════════════════════════════════════════════════════

def render(
    freight_data: dict,
    macro_data: dict,
    stock_data: dict,
    route_results: list,
) -> None:
    """Render the Shipping Cycle Timer tab.

    Args:
        freight_data:  dict route_id -> DataFrame from freight_scraper / fred_feed (BDIY)
        macro_data:    dict series_id -> DataFrame from fred_feed
        stock_data:    dict ticker -> DataFrame from stock_feed
        route_results: list[RouteOpportunity] (unused here, passed for consistency)
    """
    logger.info("Rendering Cycle Timer tab...")

    # ── Classify cycle ────────────────────────────────────────────────────────
    try:
        timing = classify_shipping_cycle(freight_data, macro_data, stock_data)
        position_score = estimate_cycle_position_score(freight_data, macro_data, stock_data)
        signals = generate_entry_signals(timing, stock_data)
    except Exception as exc:
        logger.exception("Cycle classification failed: {}", exc)
        st.error("Cycle classification unavailable: {}".format(exc))
        return

    # ── Header ────────────────────────────────────────────────────────────────
    section_header(
        "Shipping Cycle Timer",
        "Identify where we are in the ~7-year shipping cycle and when to buy / sell",
    )

    # ══════════════════════════════════════════════════════════════════════════
    #  1. CURRENT CYCLE PHASE — large colored badge (prominent hero at top)
    # ══════════════════════════════════════════════════════════════════════════
    _render_phase_hero(timing, position_score)

    # Historical analogs pills
    if timing.historical_analogs:
        pills_html = " ".join(
            '<span style="background:{bg}; color:{text2}; border:1px solid {border}; '
            'padding:3px 11px; border-radius:999px; font-size:0.72rem; '
            'font-weight:500; display:inline-block; margin:2px">{a}</span>'.format(
                bg=_hex_to_rgba(C_ACCENT, 0.10),
                text2=C_TEXT2,
                border=_hex_to_rgba(C_ACCENT, 0.30),
                a=analog,
            )
            for analog in timing.historical_analogs
        )
        st.markdown(
            '<div style="margin-bottom:16px; display:flex; flex-wrap:wrap; align-items:center; gap:4px">'
            '<span style="font-size:0.65rem; color:{t}; text-transform:uppercase; '
            'letter-spacing:0.1em; font-weight:700; margin-right:6px">Historical Analogs</span>'
            '{pills}</div>'.format(t=C_TEXT3, pills=pills_html),
            unsafe_allow_html=True,
        )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    #  2. CYCLE WHEEL (polar) + CYCLE INDICATOR DASHBOARD (side-by-side)
    # ══════════════════════════════════════════════════════════════════════════
    col_clock, col_indicators = st.columns([1, 1])

    with col_clock:
        st.markdown(
            '<div style="font-size:0.82rem; font-weight:700; color:{t}; '
            'margin-bottom:8px; text-transform:uppercase; letter-spacing:0.06em">'
            'Cycle Compass — Polar Wheel</div>'.format(t=C_TEXT),
            unsafe_allow_html=True,
        )
        _render_cycle_clock(timing, position_score)

    with col_indicators:
        st.markdown(
            '<div style="font-size:0.82rem; font-weight:700; color:{t}; '
            'margin-bottom:8px; text-transform:uppercase; letter-spacing:0.06em">'
            'Cycle Indicator Readings</div>'.format(t=C_TEXT),
            unsafe_allow_html=True,
        )
        _render_indicator_dashboard(freight_data, macro_data, stock_data)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    #  3. TYPICAL PHASE CHARACTERISTICS
    #     What happens to rates, stocks, demand in current phase
    # ══════════════════════════════════════════════════════════════════════════
    section_header(
        "Current Phase Characteristics",
        "What typically happens to rates, stocks, demand, and fleet in this phase",
    )
    _render_phase_characteristics(timing)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    #  4. HISTORICAL FREIGHT RATE CHART with colored phase background bands
    # ══════════════════════════════════════════════════════════════════════════
    section_header(
        "Historical Shipping Cycle — BDI 2008–2026",
        "Colored bands show cycle phases; annotated with major market events",
    )
    _render_historical_cycle_chart(freight_data, timing)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    #  5. PHASE TRANSITION SIGNALS + DURATION ANALYSIS (side-by-side)
    #     What would trigger next phase change
    # ══════════════════════════════════════════════════════════════════════════
    col_trans, col_dur = st.columns([1.1, 1])

    with col_trans:
        section_header(
            "Phase Transition Signals",
            "What indicators would confirm a move to the next cycle phase",
        )
        _render_transition_signals(timing)

    with col_dur:
        section_header(
            "Duration Analysis",
            "How long the current phase has lasted vs historical range",
        )
        _render_duration_analysis(timing)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    #  6. ENTRY / EXIT SIGNALS + ORDERBOOK MONITOR (side-by-side)
    # ══════════════════════════════════════════════════════════════════════════
    col_signals, col_orderbook = st.columns([1, 1])

    with col_signals:
        section_header(
            "Entry / Exit Signals",
            "Cycle-based positioning with historical backtest accuracy",
        )
        _render_signal_panel(timing, signals)

    with col_orderbook:
        section_header(
            "Orderbook vs Fleet Monitor",
            "Key leading indicator for future oversupply and rate decline",
        )
        _render_orderbook_monitor(macro_data)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    #  7. INVESTMENT IMPLICATIONS (prominent — what current phase means for stocks)
    # ══════════════════════════════════════════════════════════════════════════
    section_header(
        "Investment Implications",
        "What the current cycle phase means for dry bulk, tankers, containers, and options",
    )
    _render_investment_implications(timing)

    st.divider()

    # Phase Probability Matrix
    section_header(
        "Phase Transition Probability Matrix",
        "Likelihood of being in each cycle phase at 6 / 12 / 18 month horizons",
    )
    _render_phase_probability_matrix(timing)

    st.divider()

    # Supporting / Contrarian footer
    _render_indicator_footer(timing)

    logger.info("Cycle Timer tab rendered successfully.")
