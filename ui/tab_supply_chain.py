"""Supply Chain Resilience & Visibility tab — comprehensive SCHI dashboard."""
from __future__ import annotations

import random

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ── Color palette ──────────────────────────────────────────────────────────
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

# ── Plotly base layout ─────────────────────────────────────────────────────
_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color=C_TEXT, family="Inter, sans-serif"),
    margin=dict(l=40, r=20, t=30, b=40),
    xaxis=dict(gridcolor="rgba(255,255,255,0.05)", linecolor=C_BORDER),
    yaxis=dict(gridcolor="rgba(255,255,255,0.05)", linecolor=C_BORDER),
)


# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════

def _divider() -> None:
    st.markdown(
        f'<hr style="border:none; border-top:1px solid {C_BORDER}; margin:28px 0 22px 0;">',
        unsafe_allow_html=True,
    )


def _section_header(title: str, subtitle: str = "") -> None:
    sub = (
        f'<div style="color:{C_TEXT2}; font-size:0.82rem; margin-top:4px;">{subtitle}</div>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="margin-bottom:16px;">'
        f'<span style="font-size:1.05rem; font-weight:700; color:{C_TEXT};">{title}</span>'
        f'{sub}</div>',
        unsafe_allow_html=True,
    )


def _score_color(v: float) -> str:
    if v >= 70: return C_HIGH
    if v >= 45: return C_MOD
    return C_LOW


def _sev_color(sev: str) -> str:
    s = sev.upper()
    if s == "CRITICAL": return C_LOW
    if s == "HIGH":     return "#f97316"
    if s == "MODERATE": return C_MOD
    return C_HIGH


def _trend_arrow(delta: float) -> str:
    if delta > 0: return f'<span style="color:{C_HIGH};">&#9650; +{delta:.1f}</span>'
    if delta < 0: return f'<span style="color:{C_LOW};">&#9660; {delta:.1f}</span>'
    return f'<span style="color:{C_TEXT3};">&#8212; 0</span>'


def _sparkline_color(v: float, invert: bool = False) -> str:
    good = v >= 70 if not invert else v <= 30
    return C_HIGH if good else (C_MOD if (35 <= v <= 69) else C_LOW)


# ══════════════════════════════════════════════════════════════════════════
# Section 1 — Supply Chain Health Index
# ══════════════════════════════════════════════════════════════════════════

def _render_health_index(rng: random.Random) -> None:
    try:
        _section_header(
            "Supply Chain Health Index",
            "Composite score across freight availability, port fluidity, intermodal connectivity, and carrier reliability",
        )

        # Deterministic sub-scores from seeded rng
        freight_raw    = rng.uniform(14.5, 22.5)
        port_raw       = rng.uniform(13.0, 21.5)
        intermodal_raw = rng.uniform(15.5, 23.0)
        carrier_raw    = rng.uniform(14.0, 22.0)
        overall        = freight_raw + port_raw + intermodal_raw + carrier_raw
        prior_month    = overall - rng.uniform(-4, 6)
        pre_covid      = 82.4
        delta_month    = overall - prior_month
        delta_precovid = overall - pre_covid

        col_hero, col_subs = st.columns([1, 2], gap="large")

        with col_hero:
            arc_color = _score_color(overall)
            gauge = go.Figure(go.Indicator(
                mode="gauge+number",
                value=overall,
                number=dict(font=dict(color=arc_color, size=42), suffix=""),
                gauge=dict(
                    axis=dict(range=[0, 100], tickfont=dict(color=C_TEXT3)),
                    bar=dict(color=arc_color, thickness=0.3),
                    bgcolor="rgba(255,255,255,0.03)",
                    bordercolor=C_BORDER,
                    steps=[
                        dict(range=[0, 45],  color="rgba(239,68,68,0.12)"),
                        dict(range=[45, 70], color="rgba(245,158,11,0.12)"),
                        dict(range=[70, 100], color="rgba(16,185,129,0.12)"),
                    ],
                    threshold=dict(line=dict(color=C_TEXT2, width=2), thickness=0.75, value=pre_covid),
                ),
                title=dict(text="SCHI Score", font=dict(color=C_TEXT2, size=13)),
            ))
            gauge.update_layout(paper_bgcolor="rgba(0,0,0,0)", font=dict(color=C_TEXT), height=220, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(gauge, use_container_width=True, key="schi_gauge")

            st.markdown(
                f'<div style="display:flex; gap:12px; flex-wrap:wrap; margin-top:4px;">'
                f'<div style="background:{C_SURFACE}; border:1px solid {C_BORDER}; border-radius:8px; padding:8px 14px; flex:1; min-width:110px;">'
                f'<div style="color:{C_TEXT3}; font-size:0.72rem; text-transform:uppercase;">vs Prior Mo.</div>'
                f'<div style="color:{"#10b981" if delta_month>=0 else C_LOW}; font-size:1.1rem; font-weight:700;">{"+" if delta_month>=0 else ""}{delta_month:.1f}</div>'
                f'</div>'
                f'<div style="background:{C_SURFACE}; border:1px solid {C_BORDER}; border-radius:8px; padding:8px 14px; flex:1; min-width:110px;">'
                f'<div style="color:{C_TEXT3}; font-size:0.72rem; text-transform:uppercase;">vs Pre-COVID</div>'
                f'<div style="color:{"#10b981" if delta_precovid>=0 else C_LOW}; font-size:1.1rem; font-weight:700;">{"+" if delta_precovid>=0 else ""}{delta_precovid:.1f}</div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        with col_subs:
            sub_scores = [
                ("Freight Availability",    freight_raw,    25, "Spot market capacity, blank sailings, vessel utilization"),
                ("Port Fluidity",           port_raw,       25, "Dwell time, berth availability, congestion index"),
                ("Intermodal Connectivity", intermodal_raw, 25, "Rail on-time, truck capacity, inland depot fill"),
                ("Carrier Reliability",     carrier_raw,    25, "Schedule reliability, blank sailing rate, port calls met"),
            ]
            rows_html = ""
            for label, val, cap, desc in sub_scores:
                pct = val / cap
                bar_color = C_HIGH if pct >= 0.70 else (C_MOD if pct >= 0.45 else C_LOW)
                bar_w = int(pct * 100)
                rows_html += (
                    f'<div style="margin-bottom:14px;">'
                    f'<div style="display:flex; justify-content:space-between; margin-bottom:4px;">'
                    f'<span style="color:{C_TEXT}; font-size:0.88rem; font-weight:600;">{label}</span>'
                    f'<span style="color:{bar_color}; font-weight:700; font-size:0.88rem;">{val:.1f} / {cap}</span>'
                    f'</div>'
                    f'<div style="background:rgba(255,255,255,0.06); border-radius:4px; height:7px;">'
                    f'<div style="width:{bar_w}%; background:{bar_color}; border-radius:4px; height:7px;"></div>'
                    f'</div>'
                    f'<div style="color:{C_TEXT3}; font-size:0.73rem; margin-top:3px;">{desc}</div>'
                    f'</div>'
                )
            st.markdown(
                f'<div style="background:{C_SURFACE}; border:1px solid {C_BORDER}; border-radius:12px; padding:18px 20px;">'
                f'{rows_html}'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception as exc:
        logger.warning(f"SCHI render error: {exc}")
        st.warning("Supply Chain Health Index unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 2 — Disruption Monitor
# ══════════════════════════════════════════════════════════════════════════

def _render_disruption_monitor() -> None:
    try:
        _section_header(
            "Supply Chain Disruption Monitor",
            "Active events affecting global trade routes — updated continuously",
        )

        disruptions = [
            {
                "event": "Red Sea / Bab-el-Mandeb",
                "cause": "Houthi missile & drone attacks on commercial vessels",
                "severity": "CRITICAL",
                "routes": "Asia–Europe, Middle East–Europe",
                "duration": "14+ months",
                "resolution": "Indeterminate (geopolitical)",
                "rate_impact": "+$900–$1,400 / FEU (re-routing via Cape)",
            },
            {
                "event": "Panama Canal Drought",
                "cause": "El Niño-driven low water levels; locks at reduced draft",
                "severity": "MODERATE",
                "routes": "US East Coast–Asia, US Gulf–West Coast LATAM",
                "duration": "6 months (recovering)",
                "resolution": "Q2 2026 — La Niña normalizing reservoirs",
                "rate_impact": "+$200–$600 / FEU; transit delays –4 days",
            },
            {
                "event": "East China Sea Weather",
                "cause": "Typhoon season + persistent fog delays at Shanghai/Ningbo",
                "severity": "HIGH",
                "routes": "Transpacific, Intra-Asia",
                "duration": "Seasonal (Mar–Oct window)",
                "resolution": "Late Oct 2026",
                "rate_impact": "+1–3 day delays; +$100–$300 / FEU seasonal premium",
            },
            {
                "event": "LA/LB Port Labor Watch",
                "cause": "ILWU contract renegotiation — work-to-rule risk",
                "severity": "HIGH",
                "routes": "Transpacific (US West Coast)",
                "duration": "Ongoing negotiation",
                "resolution": "Contract talks Aug 2026; potential slowdown risk",
                "rate_impact": "Potential +$400–$800 / FEU if action taken",
            },
            {
                "event": "Rotterdam Terminal Upgrade",
                "cause": "Maasvlakte II automation retrofit — berth closures rotating",
                "severity": "MODERATE",
                "routes": "Asia–Europe, Transatlantic",
                "duration": "18-month program",
                "resolution": "Phased completion Q3 2026",
                "rate_impact": "+0.5–1.5 day dwell; minimal rate premium",
            },
        ]

        header_html = (
            f'<div style="display:grid; grid-template-columns:1.4fr 2fr 90px 1.3fr 1fr 1.5fr 1.3fr;'
            f' gap:8px; padding:8px 14px; background:{C_SURFACE}; border-radius:8px 8px 0 0;'
            f' border:1px solid {C_BORDER}; margin-bottom:2px;">'
            + "".join(
                f'<div style="color:{C_TEXT3}; font-size:0.70rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em;">{h}</div>'
                for h in ["Disruption", "Cause", "Severity", "Affected Routes", "Duration", "Est. Resolution", "Rate Impact"]
            )
            + "</div>"
        )

        rows_html = ""
        for i, d in enumerate(disruptions):
            sc = _sev_color(d["severity"])
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            rows_html += (
                f'<div style="display:grid; grid-template-columns:1.4fr 2fr 90px 1.3fr 1fr 1.5fr 1.3fr;'
                f' gap:8px; padding:10px 14px; background:{bg}; border:1px solid {C_BORDER};'
                f' border-top:none; {"border-radius:0 0 8px 8px;" if i==len(disruptions)-1 else ""}">'
                f'<div style="color:{C_TEXT}; font-size:0.82rem; font-weight:600;">{d["event"]}</div>'
                f'<div style="color:{C_TEXT2}; font-size:0.80rem;">{d["cause"]}</div>'
                f'<div><span style="background:{sc}22; color:{sc}; border:1px solid {sc}44;'
                f' border-radius:5px; padding:2px 7px; font-size:0.70rem; font-weight:700;">{d["severity"]}</span></div>'
                f'<div style="color:{C_TEXT2}; font-size:0.80rem;">{d["routes"]}</div>'
                f'<div style="color:{C_TEXT2}; font-size:0.80rem;">{d["duration"]}</div>'
                f'<div style="color:{C_TEXT2}; font-size:0.80rem;">{d["resolution"]}</div>'
                f'<div style="color:{C_MOD}; font-size:0.80rem; font-weight:600;">{d["rate_impact"]}</div>'
                f'</div>'
            )

        st.markdown(header_html + rows_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Disruption monitor error: {exc}")
        st.warning("Disruption monitor unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 3 — Inventory-to-Sales Ratio
# ══════════════════════════════════════════════════════════════════════════

def _render_inventory_sales(rng: random.Random) -> None:
    try:
        _section_header(
            "Inventory-to-Sales Ratio vs Container Shipping Demand",
            "When inventory is lean, retailers restock via ocean freight — a leading demand signal",
        )

        months = ["Jan'23","Apr'23","Jul'23","Oct'23","Jan'24","Apr'24","Jul'24","Oct'24","Jan'25","Apr'25","Jul'25","Oct'25","Jan'26","Mar'26"]
        is_ratio  = [1.37, 1.34, 1.32, 1.31, 1.30, 1.28, 1.25, 1.23, 1.21, 1.19, 1.18, 1.17, 1.16, 1.15]
        vol_index = [88, 90, 91, 93, 95, 97, 100, 102, 105, 107, 109, 111, 112, 114]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=months, y=is_ratio, name="I/S Ratio (L)",
            line=dict(color=C_MOD, width=2.5),
            yaxis="y1",
        ))
        fig.add_trace(go.Scatter(
            x=months, y=vol_index, name="Container Vol Index (R)",
            line=dict(color=C_ACCENT, width=2.5, dash="dot"),
            yaxis="y2",
        ))
        fig.update_layout(
            **_LAYOUT,
            height=280,
            yaxis=dict(title="I/S Ratio", color=C_MOD, gridcolor="rgba(255,255,255,0.05)"),
            yaxis2=dict(title="Volume Index", color=C_ACCENT, overlaying="y", side="right", gridcolor="rgba(0,0,0,0)"),
            legend=dict(x=0.01, y=0.99, bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        )
        st.plotly_chart(fig, use_container_width=True, key="is_ratio_chart")

        current_is = is_ratio[-1]
        trend_str  = "Declining (lean inventories)" if is_ratio[-1] < is_ratio[-3] else "Rising (restocking)"
        implication = (
            "Lean inventories signal upcoming restocking cycle — bullish for container volumes Q2–Q3 2026"
            if current_is < 1.22 else
            "Elevated inventories suggest muted near-term shipping demand; watch for destocking"
        )

        c1, c2, c3 = st.columns(3)
        for col, label, val, sub in [
            (c1, "Current I/S Ratio", f"{current_is:.2f}", "US Wholesale"),
            (c2, "Trend", trend_str, "3-month direction"),
            (c3, "Shipping Demand Signal", "BULLISH" if current_is < 1.22 else "NEUTRAL", "Next 3–6 months"),
        ]:
            with col:
                st.markdown(
                    f'<div style="background:{C_SURFACE}; border:1px solid {C_BORDER}; border-radius:10px; padding:14px 16px;">'
                    f'<div style="color:{C_TEXT3}; font-size:0.72rem; text-transform:uppercase; margin-bottom:4px;">{label}</div>'
                    f'<div style="color:{C_TEXT}; font-size:1.1rem; font-weight:700;">{val}</div>'
                    f'<div style="color:{C_TEXT3}; font-size:0.75rem; margin-top:3px;">{sub}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        st.markdown(
            f'<div style="background:{C_ACCENT}11; border-left:3px solid {C_ACCENT}; border-radius:0 8px 8px 0;'
            f' padding:10px 14px; margin-top:12px; color:{C_TEXT2}; font-size:0.83rem;">'
            f'<strong style="color:{C_TEXT};">Implication:</strong> {implication}</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"I/S ratio render error: {exc}")
        st.warning("Inventory-to-Sales chart unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 4 — Nearshoring / Reshoring Tracker
# ══════════════════════════════════════════════════════════════════════════

def _render_nearshoring() -> None:
    try:
        _section_header(
            "Nearshoring / Reshoring Tracker",
            "Companies shifting supply chains — trade route winners and losers",
        )

        shifts = [
            {
                "company": "Apple",
                "current": "China (Foxconn)",
                "new_loc": "India (Tamil Nadu, Telangana)",
                "timeline": "2024–2027",
                "teu_shift": "~120,000 TEU/yr",
                "winner": "India–Europe, India–US",
                "loser": "China–US Transpacific",
            },
            {
                "company": "Tesla",
                "current": "Shanghai Gigafactory",
                "new_loc": "Monterrey, Mexico",
                "timeline": "2025–2026",
                "teu_shift": "~40,000 TEU/yr",
                "winner": "US–Mexico nearshore trucking",
                "loser": "Asia–US Transpacific (EVs)",
            },
            {
                "company": "TSMC / Intel / Samsung",
                "current": "Taiwan / Korea",
                "new_loc": "Arizona (TSMC), Germany (Intel)",
                "timeline": "2025–2028",
                "teu_shift": "~80,000 TEU/yr equipment",
                "winner": "Intra-US, Asia–Europe (equipment)",
                "loser": "Minor — chips fly, not sail",
            },
            {
                "company": "EV Battery Mfrs (CATL, LG, SK)",
                "current": "China / Korea",
                "new_loc": "Kentucky, Michigan, Hungary",
                "timeline": "2024–2027",
                "teu_shift": "~60,000 TEU/yr",
                "winner": "Transatlantic, US Gulf imports",
                "loser": "China–US (cell imports)",
            },
            {
                "company": "Hasbro / Mattel",
                "current": "China",
                "new_loc": "Vietnam, India, Mexico",
                "timeline": "2023–2025 (underway)",
                "teu_shift": "~25,000 TEU/yr",
                "winner": "Southeast Asia–US, Mexico–US",
                "loser": "China–US (consumer goods)",
            },
        ]

        header_html = (
            f'<div style="display:grid; grid-template-columns:1fr 1.2fr 1.4fr 0.9fr 1fr 1.3fr 1.3fr;'
            f' gap:8px; padding:8px 14px; background:{C_SURFACE}; border-radius:8px 8px 0 0;'
            f' border:1px solid {C_BORDER}; margin-bottom:2px;">'
            + "".join(
                f'<div style="color:{C_TEXT3}; font-size:0.70rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em;">{h}</div>'
                for h in ["Company", "Current Production", "New / Additional", "Timeline", "TEU Volume Shift", "Route Gains", "Route Loses"]
            )
            + "</div>"
        )

        rows_html = ""
        for i, s in enumerate(shifts):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            last = i == len(shifts) - 1
            rows_html += (
                f'<div style="display:grid; grid-template-columns:1fr 1.2fr 1.4fr 0.9fr 1fr 1.3fr 1.3fr;'
                f' gap:8px; padding:10px 14px; background:{bg}; border:1px solid {C_BORDER};'
                f' border-top:none; {"border-radius:0 0 8px 8px;" if last else ""}">'
                f'<div style="color:{C_TEXT}; font-size:0.82rem; font-weight:600;">{s["company"]}</div>'
                f'<div style="color:{C_TEXT2}; font-size:0.80rem;">{s["current"]}</div>'
                f'<div style="color:{C_TEXT2}; font-size:0.80rem;">{s["new_loc"]}</div>'
                f'<div style="color:{C_TEXT2}; font-size:0.80rem;">{s["timeline"]}</div>'
                f'<div style="color:{C_MOD}; font-size:0.80rem; font-weight:600;">{s["teu_shift"]}</div>'
                f'<div style="color:{C_HIGH}; font-size:0.80rem;">{s["winner"]}</div>'
                f'<div style="color:{C_LOW}; font-size:0.80rem;">{s["loser"]}</div>'
                f'</div>'
            )

        st.markdown(header_html + rows_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Nearshoring tracker error: {exc}")
        st.warning("Nearshoring tracker unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 5 — Lead Time Tracker
# ══════════════════════════════════════════════════════════════════════════

def _render_lead_times() -> None:
    try:
        _section_header(
            "Lead Time Tracker — Key Commodities",
            "Ocean + inland transit times from order placement to destination. COVID distortion vs current state.",
        )

        commodities = [
            ("Electronics (Consumer)",  8,  26, 10, 11, "WORSENING"),
            ("Auto Parts",              4,  20,  8,  9, "STABLE"),
            ("Semiconductors",          6,  32, 12, 12, "STABLE"),
            ("Apparel / Textiles",      6,  18,  7,  7, "STABLE"),
            ("Industrial Machinery",    10, 28, 13, 14, "WORSENING"),
            ("Pharmaceuticals",         5,  16,  7,  8, "WORSENING"),
            ("Agricultural Commodities",3,  12,  5,  5, "STABLE"),
            ("Furniture / Home Goods",  10, 30, 12, 11, "IMPROVING"),
        ]

        header_html = (
            f'<div style="display:grid; grid-template-columns:1.6fr 1fr 1fr 1.2fr 1fr 1fr;'
            f' gap:8px; padding:8px 14px; background:{C_SURFACE}; border-radius:8px 8px 0 0;'
            f' border:1px solid {C_BORDER}; margin-bottom:2px;">'
            + "".join(
                f'<div style="color:{C_TEXT3}; font-size:0.70rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em;">{h}</div>'
                for h in ["Commodity", "2019 Baseline", "2021 Peak", "2023 Normalized", "Current (wks)", "Trend"]
            )
            + "</div>"
        )

        rows_html = ""
        trend_colors = {"IMPROVING": C_HIGH, "STABLE": C_ACCENT, "WORSENING": C_LOW}
        for i, (name, b19, p21, n23, cur, trend) in enumerate(commodities):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            last = i == len(commodities) - 1
            tc = trend_colors.get(trend, C_TEXT2)
            vs_base = cur - b19
            base_clr = C_HIGH if vs_base <= 1 else (C_MOD if vs_base <= 4 else C_LOW)
            rows_html += (
                f'<div style="display:grid; grid-template-columns:1.6fr 1fr 1fr 1.2fr 1fr 1fr;'
                f' gap:8px; padding:10px 14px; background:{bg}; border:1px solid {C_BORDER};'
                f' border-top:none; {"border-radius:0 0 8px 8px;" if last else ""}">'
                f'<div style="color:{C_TEXT}; font-size:0.82rem; font-weight:600;">{name}</div>'
                f'<div style="color:{C_TEXT2}; font-size:0.82rem;">{b19} wks</div>'
                f'<div style="color:{C_LOW}; font-size:0.82rem; font-weight:600;">{p21} wks</div>'
                f'<div style="color:{C_TEXT2}; font-size:0.82rem;">{n23} wks</div>'
                f'<div style="color:{base_clr}; font-size:0.82rem; font-weight:700;">{cur} wks</div>'
                f'<div><span style="background:{tc}22; color:{tc}; border:1px solid {tc}44;'
                f' border-radius:5px; padding:2px 8px; font-size:0.72rem; font-weight:700;">{trend}</span></div>'
                f'</div>'
            )

        st.markdown(header_html + rows_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Lead time tracker error: {exc}")
        st.warning("Lead time tracker unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 6 — Industry Resilience Scorecard
# ══════════════════════════════════════════════════════════════════════════

def _render_resilience_scorecard(rng: random.Random) -> None:
    try:
        _section_header(
            "Supply Chain Resilience Scorecard — by Industry",
            "Assessment across five resilience dimensions. Higher = more resilient.",
        )

        industries = [
            ("Automotive",        62, 45, 55, 60, 58),
            ("Electronics",       70, 40, 65, 55, 57),
            ("Pharmaceuticals",   75, 60, 80, 70, 71),
            ("Food & Agriculture",65, 72, 60, 65, 65),
            ("Apparel",           80, 68, 45, 75, 67),
            ("Industrial Goods",  55, 55, 60, 62, 58),
        ]

        dims = ["Geo Diversification", "Single-Source Risk", "Inventory Buffer", "Carrier Diversity"]

        fig = go.Figure()
        colors_list = [C_ACCENT, C_HIGH, "#8b5cf6", C_MOD, "#06b6d4", "#f97316"]
        for (name, g, s, inv, cd, res), clr in zip(industries, colors_list):
            fig.add_trace(go.Scatterpolar(
                r=[g, s, inv, cd, g],
                theta=dims + [dims[0]],
                name=name,
                line=dict(color=clr, width=2),
                fill="toself",
                fillcolor=clr.replace("#", "") if len(clr) == 7 else clr,
                opacity=0.15,
            ))

        fig.update_layout(
            **_LAYOUT,
            height=380,
            polar=dict(
                bgcolor="rgba(255,255,255,0.02)",
                radialaxis=dict(range=[0, 100], gridcolor="rgba(255,255,255,0.1)", tickfont=dict(color=C_TEXT3, size=9)),
                angularaxis=dict(gridcolor="rgba(255,255,255,0.1)", tickfont=dict(color=C_TEXT2, size=10)),
            ),
            legend=dict(x=1.05, y=0.9, bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        )
        st.plotly_chart(fig, use_container_width=True, key="resilience_radar")

        # Scorecard table below radar
        header_html = (
            f'<div style="display:grid; grid-template-columns:1.3fr 1fr 1fr 1fr 1fr 0.8fr;'
            f' gap:8px; padding:8px 14px; background:{C_SURFACE}; border-radius:8px 8px 0 0;'
            f' border:1px solid {C_BORDER}; margin-bottom:2px;">'
            + "".join(
                f'<div style="color:{C_TEXT3}; font-size:0.70rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em;">{h}</div>'
                for h in ["Industry", "Geo Divers.", "Single-Src Risk", "Inventory Buffer", "Carrier Diversity", "Score"]
            )
            + "</div>"
        )
        rows_html = ""
        for i, (name, g, s, inv, cd, res) in enumerate(industries):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            last = i == len(industries) - 1
            rc = _score_color(res)
            rows_html += (
                f'<div style="display:grid; grid-template-columns:1.3fr 1fr 1fr 1fr 1fr 0.8fr;'
                f' gap:8px; padding:10px 14px; background:{bg}; border:1px solid {C_BORDER};'
                f' border-top:none; {"border-radius:0 0 8px 8px;" if last else ""}">'
                f'<div style="color:{C_TEXT}; font-size:0.82rem; font-weight:600;">{name}</div>'
                f'<div style="color:{_score_color(g)}; font-size:0.82rem;">{g}/100</div>'
                f'<div style="color:{_score_color(s)}; font-size:0.82rem;">{s}/100</div>'
                f'<div style="color:{_score_color(inv)}; font-size:0.82rem;">{inv}/100</div>'
                f'<div style="color:{_score_color(cd)}; font-size:0.82rem;">{cd}/100</div>'
                f'<div style="color:{rc}; font-size:0.90rem; font-weight:700;">{res}</div>'
                f'</div>'
            )
        st.markdown(header_html + rows_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Resilience scorecard error: {exc}")
        st.warning("Resilience scorecard unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 7 — JIT vs JIC Shift
# ══════════════════════════════════════════════════════════════════════════

def _render_jit_vs_jic() -> None:
    try:
        _section_header(
            "Just-in-Time vs Just-in-Case — Post-COVID Inventory Strategy Shift",
            "Sectors that have permanently increased safety stock — a structural tailwind for shipping demand",
        )

        sectors = ["Automotive", "Electronics", "Pharma", "Food & Ag", "Apparel", "Chemicals", "Industrial", "Retail"]
        jit_era  = [0.5, 0.3, 0.8, 1.2, 0.6, 0.7, 0.5, 0.8]
        jic_now  = [2.2, 1.8, 3.5, 1.6, 1.2, 2.0, 1.7, 1.5]
        delta    = [j - t for j, t in zip(jic_now, jit_era)]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Pre-COVID (JIT era)",
            x=sectors, y=jit_era,
            marker_color=C_TEXT3,
            opacity=0.6,
        ))
        fig.add_trace(go.Bar(
            name="Current Safety Stock",
            x=sectors, y=jic_now,
            marker_color=C_ACCENT,
            opacity=0.9,
        ))
        fig.update_layout(
            **_LAYOUT,
            height=300,
            barmode="group",
            yaxis=dict(title="Months of Stock", gridcolor="rgba(255,255,255,0.05)"),
            legend=dict(x=0.01, y=0.99, bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        )
        st.plotly_chart(fig, use_container_width=True, key="jit_jic_chart")

        # Insight cards
        max_delta_sector = sectors[delta.index(max(delta))]
        avg_increase = sum(delta) / len(delta)

        c1, c2, c3 = st.columns(3)
        cards = [
            (c1, "Largest Safety Stock Increase", max_delta_sector, f"+{max(delta):.1f} months added"),
            (c2, "Average Buffer Increase (all sectors)", f"+{avg_increase:.1f} months", "vs pre-COVID JIT baseline"),
            (c3, "Structural Demand Impact", "~8–12% higher", "Baseline container volumes from permanent restocking"),
        ]
        for col, label, val, sub in cards:
            with col:
                st.markdown(
                    f'<div style="background:{C_SURFACE}; border:1px solid {C_BORDER}; border-radius:10px; padding:14px 16px;">'
                    f'<div style="color:{C_TEXT3}; font-size:0.72rem; text-transform:uppercase; margin-bottom:4px;">{label}</div>'
                    f'<div style="color:{C_ACCENT}; font-size:1.1rem; font-weight:700;">{val}</div>'
                    f'<div style="color:{C_TEXT3}; font-size:0.75rem; margin-top:3px;">{sub}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        st.markdown(
            f'<div style="background:{C_HIGH}11; border-left:3px solid {C_HIGH}; border-radius:0 8px 8px 0;'
            f' padding:10px 14px; margin-top:12px; color:{C_TEXT2}; font-size:0.83rem;">'
            f'<strong style="color:{C_TEXT};">Structural Tailwind:</strong> The JIT-to-JIC shift represents '
            f'a permanent increase in safety-stock requirements across most industrial sectors. This elevates '
            f'baseline container shipping demand by an estimated 8–12% above pre-COVID trend, independent of '
            f'cyclical economic conditions. Pharma and automotive show the most durable increases.'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"JIT/JIC render error: {exc}")
        st.warning("JIT vs JIC analysis unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 8 — Supply Chain Forecast (90 days)
# ══════════════════════════════════════════════════════════════════════════

def _render_forecast() -> None:
    try:
        _section_header(
            "Supply Chain Forecast — Next 90 Days",
            "Which disruptions ease, which worsen, and where the opportunities emerge",
        )

        easing = [
            {
                "item": "Panama Canal Capacity",
                "detail": "La Niña rainfall restoring Gatun Lake. Draft restrictions lifting by ~May 2026. Expect +2–4 transits/day by Q2.",
                "confidence": "HIGH",
                "rate_effect": "–$100 to –$200 / FEU on US East–Asia lanes",
            },
            {
                "item": "Transpacific Spot Rates",
                "detail": "Seasonal peak buying subsiding. Carriers adding capacity on Asia–USWC. Rates softening ~10–15% from March peak.",
                "confidence": "MODERATE",
                "rate_effect": "–$200 to –$400 / FEU",
            },
            {
                "item": "Shanghai/Ningbo Port Congestion",
                "detail": "Pre-CNY backlog clearing. Berth productivity improving through April.",
                "confidence": "HIGH",
                "rate_effect": "Neutral on rates; –1 day transit improvement",
            },
        ]

        worsening = [
            {
                "item": "Red Sea Diversions",
                "detail": "No credible ceasefire timeline. Cape re-routing now normalized into carrier schedules. Risk of escalation to Strait of Hormuz.",
                "confidence": "HIGH",
                "rate_effect": "+Sustained $800–$1,200 / FEU premium on Europe lanes",
            },
            {
                "item": "LA/LB Labor Risk",
                "detail": "ILWU contract talks heating into summer. Work-to-rule actions historically coincide with peak season, maximizing leverage.",
                "confidence": "MODERATE",
                "rate_effect": "+$400–$800 / FEU if action materializes",
            },
            {
                "item": "Transpacific Peak Season Build",
                "detail": "US consumer demand resilient; retailers front-loading ahead of tariff risk. June–August volume surge expected to tighten space.",
                "confidence": "MODERATE",
                "rate_effect": "+$300–$600 / FEU peak season premium",
            },
        ]

        col_ease, col_worse = st.columns(2, gap="large")

        def _forecast_block(container, title: str, items: list, color: str) -> None:
            rows = ""
            for it in items:
                conf_c = C_HIGH if it["confidence"] == "HIGH" else (C_MOD if it["confidence"] == "MODERATE" else C_TEXT3)
                rows += (
                    f'<div style="border-bottom:1px solid {C_BORDER}; padding:12px 0; last-child:border:none;">'
                    f'<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:5px;">'
                    f'<span style="color:{C_TEXT}; font-weight:600; font-size:0.87rem;">{it["item"]}</span>'
                    f'<span style="background:{conf_c}22; color:{conf_c}; border:1px solid {conf_c}44;'
                    f' border-radius:5px; padding:2px 7px; font-size:0.68rem; font-weight:700;">{it["confidence"]} CONF</span>'
                    f'</div>'
                    f'<div style="color:{C_TEXT2}; font-size:0.80rem; margin-bottom:5px;">{it["detail"]}</div>'
                    f'<div style="color:{color}; font-size:0.78rem; font-weight:600;">{it["rate_effect"]}</div>'
                    f'</div>'
                )
            container.markdown(
                f'<div style="background:{C_SURFACE}; border:1px solid {color}44; border-top:3px solid {color};'
                f' border-radius:0 0 10px 10px; padding:14px 16px;">'
                f'<div style="color:{color}; font-size:0.80rem; font-weight:700; text-transform:uppercase;'
                f' letter-spacing:0.06em; margin-bottom:10px;">{title}</div>'
                f'{rows}'
                f'</div>',
                unsafe_allow_html=True,
            )

        _forecast_block(col_ease,  "Conditions Easing", easing,    C_HIGH)
        _forecast_block(col_worse, "Conditions Worsening", worsening, C_LOW)
    except Exception as exc:
        logger.warning(f"Forecast render error: {exc}")
        st.warning("Supply chain forecast unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════════════════

def render(port_results=None, route_results=None, insights=None, macro_data=None) -> None:
    """Render the Supply Chain Resilience & Visibility tab."""
    try:
        port_results  = port_results  or []
        route_results = route_results or []

        # Stable seed for deterministic random numbers within session
        seed_val = len(port_results) * 17 + len(route_results) * 31
        rng = random.Random(seed_val + 42)

        st.markdown(
            f'<div style="padding:18px 0 6px 0;">'
            f'<div style="font-size:1.45rem; font-weight:800; color:{C_TEXT}; letter-spacing:-0.01em;">'
            f'Supply Chain Resilience &amp; Visibility</div>'
            f'<div style="color:{C_TEXT2}; font-size:0.87rem; margin-top:5px;">'
            f'End-to-end supply chain health monitoring — disruptions, inventory signals, reshoring trends, and lead times</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        _divider()
        _render_health_index(rng)

        _divider()
        _render_disruption_monitor()

        _divider()
        _render_inventory_sales(rng)

        _divider()
        _render_nearshoring()

        _divider()
        _render_lead_times()

        _divider()
        _render_resilience_scorecard(rng)

        _divider()
        _render_jit_vs_jic()

        _divider()
        _render_forecast()

    except Exception as exc:
        logger.error(f"Supply chain tab render failed: {exc}")
        st.error(f"Supply chain tab failed to render: {exc}")
