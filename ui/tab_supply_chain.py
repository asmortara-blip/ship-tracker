"""Supply Chain Resilience & Visibility tab — comprehensive SCHI dashboard."""
from __future__ import annotations

import random

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)


# ══════════════════════════════════════════════════════════════════════════
# Data sources (provenance pills)
# ══════════════════════════════════════════════════════════════════════════

_SCHI_SOURCE       = DataSource.modeled(
    "Supply Chain Health Composite",
    notes="Composite across freight, port, intermodal, carrier sub-indices",
)
_DISRUPTION_SOURCE = DataSource.modeled(
    "Global Disruption Monitor",
    notes="Curated active events affecting trade routes",
)
_IS_SOURCE         = DataSource.modeled(
    "US Census Wholesale I/S Ratio + Container Volume Index",
    notes="Inventory-to-sales ratio paired with container volume index",
)
_NEARSHORE_SOURCE  = DataSource.modeled(
    "Nearshoring / Reshoring Tracker",
    notes="Company-announced production shifts",
)
_LEADTIME_SOURCE   = DataSource.modeled(
    "Lead Time Tracker",
    notes="Origin-to-destination transit times by commodity",
)
_RESILIENCE_SOURCE = DataSource.modeled(
    "Industry Resilience Scorecard",
    notes="Five-dimension resilience assessment per industry",
)
_JIT_SOURCE        = DataSource.modeled(
    "JIT vs JIC Inventory Strategy Survey",
    notes="Sector safety-stock months — pre-COVID vs current",
)
_FORECAST_SOURCE   = DataSource.modeled(
    "90-day Supply Chain Forecast",
    notes="Forward look at easing and worsening conditions",
)


# ══════════════════════════════════════════════════════════════════════════
# Tab-local semantic helpers
# ══════════════════════════════════════════════════════════════════════════

def _score_color(v: float) -> str:
    if v >= 70:
        return C_HIGH
    if v >= 45:
        return C_MOD
    return C_LOW


def _sev_color(sev: str) -> str:
    s = sev.upper()
    if s == "CRITICAL":
        return C_LOW
    if s == "HIGH":
        return "#f97316"
    if s == "MODERATE":
        return C_MOD
    return C_HIGH


def _confidence_color(conf: str) -> str:
    c = conf.upper()
    if c == "HIGH":
        return C_HIGH
    if c == "MODERATE":
        return C_MOD
    return C_TEXT3


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _mono(value: str, color: str = C_TEXT) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


# ══════════════════════════════════════════════════════════════════════════
# Section 1 — Supply Chain Health Index
# ══════════════════════════════════════════════════════════════════════════

def _render_health_index(rng: random.Random) -> None:
    try:
        section_header(
            "Supply Chain Health Index",
            "Composite score across freight availability, port fluidity, intermodal connectivity, and carrier reliability",
        )

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
                    bgcolor="rgba(232,230,225,0.03)",
                    bordercolor=C_BORDER,
                    steps=[
                        dict(range=[0, 45],  color="rgba(192,57,43,0.12)"),
                        dict(range=[45, 70], color="rgba(201,150,43,0.12)"),
                        dict(range=[70, 100], color="rgba(46,158,110,0.12)"),
                    ],
                    threshold=dict(line=dict(color=C_TEXT2, width=2), thickness=0.75, value=pre_covid),
                ),
                title=dict(text="SCHI Score", font=dict(color=C_TEXT2, size=13)),
            ))
            apply_dark_layout(gauge, height=220)
            gauge.update_layout(margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(gauge, use_container_width=True, key="schi_gauge")

            metric_card_row(
                [
                    dict(
                        label="vs Prior Mo.",
                        value=f"{'+' if delta_month >= 0 else ''}{delta_month:.1f}",
                        accent=C_HIGH if delta_month >= 0 else C_LOW,
                    ),
                    dict(
                        label="vs Pre-COVID",
                        value=f"{'+' if delta_precovid >= 0 else ''}{delta_precovid:.1f}",
                        accent=C_HIGH if delta_precovid >= 0 else C_LOW,
                    ),
                ],
                columns=2,
            )

        with col_subs:
            sub_scores = [
                ("Freight Availability",    freight_raw,    25, "Spot market capacity, blank sailings, vessel utilization"),
                ("Port Fluidity",           port_raw,       25, "Dwell time, berth availability, congestion index"),
                ("Intermodal Connectivity", intermodal_raw, 25, "Rail on-time, truck capacity, inland depot fill"),
                ("Carrier Reliability",     carrier_raw,    25, "Schedule reliability, blank sailing rate, port calls met"),
            ]

            rows = []
            for label, val, cap, desc in sub_scores:
                pct = val / cap
                bar_color = C_HIGH if pct >= 0.70 else (C_MOD if pct >= 0.45 else C_LOW)
                rows.append([
                    _sans(label, color=C_TEXT, weight=600),
                    _mono(f"{val:.1f} / {cap}", color=bar_color),
                    badge(f"{int(pct * 100)}%", color=bar_color),
                    _sans(desc, color=C_TEXT3),
                ])
            wsj_market_table(
                ["Sub-Index", "Score", "% of Cap", "Detail"],
                rows,
            )

        st.markdown(source_footer([_SCHI_SOURCE]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"SCHI render error: {exc}")
        st.warning("Supply Chain Health Index unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 2 — Disruption Monitor
# ══════════════════════════════════════════════════════════════════════════

def _render_disruption_monitor() -> None:
    try:
        section_header(
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

        rows = [
            [
                _sans(d["event"], color=C_TEXT, weight=600),
                _sans(d["cause"], color=C_TEXT2),
                badge(d["severity"], color=_sev_color(d["severity"])),
                _sans(d["routes"], color=C_TEXT2),
                _sans(d["duration"], color=C_TEXT2),
                _sans(d["resolution"], color=C_TEXT2),
                _sans(d["rate_impact"], color=C_MOD, weight=600),
            ]
            for d in disruptions
        ]
        wsj_market_table(
            ["Disruption", "Cause", "Severity", "Affected Routes", "Duration", "Est. Resolution", "Rate Impact"],
            rows,
        )
        st.markdown(source_footer([_DISRUPTION_SOURCE]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Disruption monitor error: {exc}")
        st.warning("Disruption monitor unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 3 — Inventory-to-Sales Ratio
# ══════════════════════════════════════════════════════════════════════════

def _render_inventory_sales(rng: random.Random) -> None:
    try:
        section_header(
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
        apply_dark_layout(fig, height=280)
        fig.update_layout(
            yaxis=dict(title="I/S Ratio", color=C_MOD, gridcolor="rgba(232,230,225,0.04)"),
            yaxis2=dict(title="Volume Index", color=C_ACCENT, overlaying="y", side="right", gridcolor="rgba(0,0,0,0)"),
            legend=dict(x=0.01, y=0.99, bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        )
        st.plotly_chart(fig, use_container_width=True, key="is_ratio_chart")

        current_is = is_ratio[-1]
        trend_str  = "Declining (lean inventories)" if is_ratio[-1] < is_ratio[-3] else "Rising (restocking)"
        is_bullish = current_is < 1.22
        implication = (
            "Lean inventories signal upcoming restocking cycle — bullish for container volumes Q2–Q3 2026"
            if is_bullish else
            "Elevated inventories suggest muted near-term shipping demand; watch for destocking"
        )

        metric_card_row(
            [
                dict(label="Current I/S Ratio", value=f"{current_is:.2f}", sublabel="US Wholesale", accent=C_MOD),
                dict(label="Trend", value=trend_str, sublabel="3-month direction", accent=C_ACCENT),
                dict(
                    label="Shipping Demand Signal",
                    value="BULLISH" if is_bullish else "NEUTRAL",
                    sublabel="Next 3–6 months",
                    accent=C_HIGH if is_bullish else C_MOD,
                ),
            ],
            columns=3,
        )
        st.markdown(
            insight_card_html(
                title="Implication",
                score=0.75 if is_bullish else 0.45,
                action="BUY" if is_bullish else "HOLD",
                rationale=implication,
                category="DEMAND",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(source_footer([_IS_SOURCE]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"I/S ratio render error: {exc}")
        st.warning("Inventory-to-Sales chart unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 4 — Nearshoring / Reshoring Tracker
# ══════════════════════════════════════════════════════════════════════════

def _render_nearshoring() -> None:
    try:
        section_header(
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

        rows = [
            [
                _sans(s["company"], color=C_TEXT, weight=600),
                _sans(s["current"], color=C_TEXT2),
                _sans(s["new_loc"], color=C_TEXT2),
                _sans(s["timeline"], color=C_TEXT2),
                _mono(s["teu_shift"], color=C_MOD),
                _sans(s["winner"], color=C_HIGH),
                _sans(s["loser"], color=C_LOW),
            ]
            for s in shifts
        ]
        wsj_market_table(
            ["Company", "Current Production", "New / Additional", "Timeline", "TEU Volume Shift", "Route Gains", "Route Loses"],
            rows,
        )
        st.markdown(source_footer([_NEARSHORE_SOURCE]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Nearshoring tracker error: {exc}")
        st.warning("Nearshoring tracker unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 5 — Lead Time Tracker
# ══════════════════════════════════════════════════════════════════════════

def _render_lead_times() -> None:
    try:
        section_header(
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

        trend_colors = {"IMPROVING": C_HIGH, "STABLE": C_ACCENT, "WORSENING": C_LOW}
        rows = []
        for name, b19, p21, n23, cur, trend in commodities:
            vs_base = cur - b19
            base_clr = C_HIGH if vs_base <= 1 else (C_MOD if vs_base <= 4 else C_LOW)
            rows.append([
                _sans(name, color=C_TEXT, weight=600),
                _mono(f"{b19} wks", color=C_TEXT2),
                _mono(f"{p21} wks", color=C_LOW),
                _mono(f"{n23} wks", color=C_TEXT2),
                _mono(f"{cur} wks", color=base_clr),
                badge(trend, color=trend_colors.get(trend, C_TEXT2)),
            ])
        wsj_market_table(
            ["Commodity", "2019 Baseline", "2021 Peak", "2023 Normalized", "Current (wks)", "Trend"],
            rows,
        )
        st.markdown(source_footer([_LEADTIME_SOURCE]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Lead time tracker error: {exc}")
        st.warning("Lead time tracker unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 6 — Industry Resilience Scorecard
# ══════════════════════════════════════════════════════════════════════════

def _render_resilience_scorecard(rng: random.Random) -> None:
    try:
        section_header(
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
        colors_list = [C_ACCENT, C_HIGH, "#7c6eaf", C_MOD, "#4a90a4", "#f97316"]
        for (name, g, s, inv, cd, res), clr in zip(industries, colors_list):
            fig.add_trace(go.Scatterpolar(
                r=[g, s, inv, cd, g],
                theta=dims + [dims[0]],
                name=name,
                line=dict(color=clr, width=2),
                fill="toself",
                fillcolor=_hex_to_rgba(clr, 0.15),
            ))

        apply_dark_layout(fig, height=380)
        fig.update_layout(
            polar=dict(
                bgcolor="rgba(255,255,255,0.02)",
                radialaxis=dict(range=[0, 100], gridcolor="rgba(255,255,255,0.1)", tickfont=dict(color=C_TEXT3, size=9)),
                angularaxis=dict(gridcolor="rgba(255,255,255,0.1)", tickfont=dict(color=C_TEXT2, size=10)),
            ),
            legend=dict(x=1.05, y=0.9, bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        )
        st.plotly_chart(fig, use_container_width=True, key="resilience_radar")

        rows = []
        for name, g, s, inv, cd, res in industries:
            rows.append([
                _sans(name, color=C_TEXT, weight=600),
                _mono(f"{g}/100", color=_score_color(g)),
                _mono(f"{s}/100", color=_score_color(s)),
                _mono(f"{inv}/100", color=_score_color(inv)),
                _mono(f"{cd}/100", color=_score_color(cd)),
                _sans(str(res), color=_score_color(res), weight=700),
            ])
        wsj_market_table(
            ["Industry", "Geo Divers.", "Single-Src Risk", "Inventory Buffer", "Carrier Diversity", "Score"],
            rows,
        )
        st.markdown(source_footer([_RESILIENCE_SOURCE]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Resilience scorecard error: {exc}")
        st.warning("Resilience scorecard unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 7 — JIT vs JIC Shift
# ══════════════════════════════════════════════════════════════════════════

def _render_jit_vs_jic() -> None:
    try:
        section_header(
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
        apply_dark_layout(fig, height=300)
        fig.update_layout(
            barmode="group",
            yaxis=dict(title="Months of Stock", gridcolor="rgba(232,230,225,0.04)"),
            legend=dict(x=0.01, y=0.99, bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        )
        st.plotly_chart(fig, use_container_width=True, key="jit_jic_chart")

        max_delta_sector = sectors[delta.index(max(delta))]
        avg_increase = sum(delta) / len(delta)

        metric_card_row(
            [
                dict(
                    label="Largest Safety Stock Increase",
                    value=max_delta_sector,
                    sublabel=f"+{max(delta):.1f} months added",
                    accent=C_ACCENT,
                ),
                dict(
                    label="Average Buffer Increase (all sectors)",
                    value=f"+{avg_increase:.1f} months",
                    sublabel="vs pre-COVID JIT baseline",
                    accent=C_ACCENT,
                ),
                dict(
                    label="Structural Demand Impact",
                    value="~8–12% higher",
                    sublabel="Baseline container volumes from permanent restocking",
                    accent=C_ACCENT,
                ),
            ],
            columns=3,
        )

        st.markdown(
            insight_card_html(
                title="Structural Tailwind",
                score=0.80,
                action="BUY",
                rationale=(
                    "The JIT-to-JIC shift represents a permanent increase in safety-stock "
                    "requirements across most industrial sectors. This elevates baseline "
                    "container shipping demand by an estimated 8–12% above pre-COVID trend, "
                    "independent of cyclical economic conditions. Pharma and automotive show "
                    "the most durable increases."
                ),
                category="DEMAND",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(source_footer([_JIT_SOURCE]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"JIT/JIC render error: {exc}")
        st.warning("JIT vs JIC analysis unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Section 8 — Supply Chain Forecast (90 days)
# ══════════════════════════════════════════════════════════════════════════

def _render_forecast() -> None:
    try:
        section_header(
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

        def _forecast_rows(items: list, effect_color: str) -> list[list[str]]:
            rows = []
            for it in items:
                rows.append([
                    _sans(it["item"], color=C_TEXT, weight=600),
                    _sans(it["detail"], color=C_TEXT2),
                    badge(it["confidence"], color=_confidence_color(it["confidence"])),
                    _mono(it["rate_effect"], color=effect_color),
                ])
            return rows

        with col_ease:
            section_header("Conditions Easing")
            wsj_market_table(
                ["Item", "Detail", "Confidence", "Rate Effect"],
                _forecast_rows(easing, C_HIGH),
            )

        with col_worse:
            section_header("Conditions Worsening")
            wsj_market_table(
                ["Item", "Detail", "Confidence", "Rate Effect"],
                _forecast_rows(worsening, C_LOW),
            )

        st.markdown(source_footer([_FORECAST_SOURCE]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Forecast render error: {exc}")
        st.warning("Supply chain forecast unavailable.")


# ══════════════════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════════════════

def render(port_results=None, route_results=None, insights=None, macro_data=None, *args, **kwargs) -> None:
    """Render the Supply Chain Resilience & Visibility tab."""
    try:
        port_results  = port_results  or []
        route_results = route_results or []

        seed_val = len(port_results) * 17 + len(route_results) * 31
        rng = random.Random(seed_val + 42)

        page_header(
            title="Supply Chain Resilience & Visibility",
            subtitle="End-to-end supply chain health monitoring — disruptions, inventory signals, reshoring trends, and lead times",
            badge_text="SUPPLY CHAIN",
            badge_color=C_ACCENT,
        )

        _render_health_index(rng)
        section_divider()
        _render_disruption_monitor()
        section_divider()
        _render_inventory_sales(rng)
        section_divider()
        _render_nearshoring()
        section_divider()
        _render_lead_times()
        section_divider()
        _render_resilience_scorecard(rng)
        section_divider()
        _render_jit_vs_jic()
        section_divider()
        _render_forecast()

    except Exception as exc:
        logger.error(f"Supply chain tab render failed: {exc}")
        st.error(f"Supply chain tab failed to render: {exc}")
