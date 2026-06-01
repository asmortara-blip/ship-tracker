"""tab_trade_war.py — Trade Policy & Tariff Impact Intelligence Dashboard.

Migrated to the canonical design system. See docs/TAB_MIGRATION.md.

Sections:
    1. Trade War Dashboard (hero) — US-China tariff rates, impact KPIs
    2. Tariff Impact by Commodity — wsj_market_table
    3. Trade Flow Diversion Map — Plotly scatter_geo with rerouting arrows
    4. Nearshoring & Friendshoring — supply-chain shift cards
    5. Shipping Volume Impact — transpacific container volumes
    6. Trade Deal Tracker — wsj_market_table
    7. Historical Tariff Wars — comparison across tariff episodes
    8. Scenario: Trade De-escalation — modeled recovery if tariffs fall to 50%
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    _hex_to_rgba,
    C_ACCENT,
    C_BG,
    C_BORDER,
    C_CARD,
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


# ── Provenance ────────────────────────────────────────────────────────────────
# Trade-war metrics here are a mix of reported policy figures and modeled
# projections. All flows shown are illustrative; demo pills make the
# uncertainty explicit.

_DS_TARIFF_POLICY = DataSource.modeled(
    "USTR / MOFCOM tariff schedules",
    notes="Tariff rates as announced April 2025; impact estimates modeled.",
)
_DS_TRADE_FLOWS = DataSource.modeled(
    "Trade flow diversion model",
    notes="Bilateral and rerouted flows projected from customs + AIS data.",
)
_DS_VOLUME = DataSource.modeled(
    "Transpacific TEU model",
    notes="Container volumes projected from carrier sailing schedules.",
)
_DS_DEALS = DataSource.demo("Trade deal tracker (illustrative)")
_DS_HISTORY = DataSource.modeled(
    "Historical tariff episodes",
    notes="Compiled from BEA / Census trade balance series.",
)
_DS_SCENARIO = DataSource.demo("De-escalation scenario (illustrative)")


# ── Commodity data ─────────────────────────────────────────────────────────────
_COMMODITIES = [
    {"name": "Electronics",          "us_tariff": "145%",           "cn_tariff": "—",    "us_imports_bn": 168, "tariff_burden_bn": 244, "shipping_impact": "HIGH",     "alt_sources": "Vietnam, India, Mexico"},
    {"name": "Auto Parts",           "us_tariff": "145%",           "cn_tariff": "—",    "us_imports_bn":  54, "tariff_burden_bn":  78, "shipping_impact": "HIGH",     "alt_sources": "Mexico, South Korea, Germany"},
    {"name": "Machinery",            "us_tariff": "145%",           "cn_tariff": "—",    "us_imports_bn": 115, "tariff_burden_bn": 167, "shipping_impact": "HIGH",     "alt_sources": "Germany, Japan, South Korea"},
    {"name": "Steel & Aluminum",     "us_tariff": "145% + 232%",    "cn_tariff": "—",    "us_imports_bn":  12, "tariff_burden_bn":  44, "shipping_impact": "HIGH",     "alt_sources": "Canada, Brazil, India"},
    {"name": "Textiles & Apparel",   "us_tariff": "145%",           "cn_tariff": "—",    "us_imports_bn":  42, "tariff_burden_bn":  61, "shipping_impact": "HIGH",     "alt_sources": "Bangladesh, Vietnam, Cambodia"},
    {"name": "Chemicals",            "us_tariff": "145%",           "cn_tariff": "—",    "us_imports_bn":  28, "tariff_burden_bn":  41, "shipping_impact": "MODERATE", "alt_sources": "Germany, India, Singapore"},
    {"name": "Soybeans",             "us_tariff": "—",              "cn_tariff": "125%", "us_imports_bn":  14, "tariff_burden_bn":  18, "shipping_impact": "HIGH",     "alt_sources": "Brazil, Argentina (replacing US)"},
    {"name": "LNG",                  "us_tariff": "—",              "cn_tariff": "125%", "us_imports_bn":   8, "tariff_burden_bn":  10, "shipping_impact": "MODERATE", "alt_sources": "Qatar, Australia, Russia"},
    {"name": "Semiconductors",       "us_tariff": "Complex/phased", "cn_tariff": "125%", "us_imports_bn":  22, "tariff_burden_bn":  31, "shipping_impact": "MODERATE", "alt_sources": "Taiwan, South Korea, Netherlands"},
    {"name": "Pharmaceuticals",      "us_tariff": "145%",           "cn_tariff": "—",    "us_imports_bn":   9, "tariff_burden_bn":  13, "shipping_impact": "LOW",      "alt_sources": "India, Ireland, Germany"},
    {"name": "Furniture",            "us_tariff": "145%",           "cn_tariff": "—",    "us_imports_bn":  19, "tariff_burden_bn":  28, "shipping_impact": "MODERATE", "alt_sources": "Vietnam, Malaysia, Mexico"},
    {"name": "Plastics",             "us_tariff": "145%",           "cn_tariff": "—",    "us_imports_bn":  16, "tariff_burden_bn":  23, "shipping_impact": "MODERATE", "alt_sources": "South Korea, Germany, Saudi Arabia"},
]

# ── Trade deal tracker data ────────────────────────────────────────────────────
_TRADE_DEALS = [
    {"parties": "US ↔ China",           "status": "STALLED",     "status_color": C_LOW,    "key_issues": "Fentanyl, tech transfer, Taiwan",      "likelihood": "15%", "shipping_impact": "+38% transpacific volume"},
    {"parties": "US ↔ EU (TTIP revival)", "status": "EXPLORATORY", "status_color": C_MOD,  "key_issues": "Digital taxes, agriculture, carbon border", "likelihood": "30%", "shipping_impact": "+12% transatlantic volume"},
    {"parties": "US ↔ UK",              "status": "ACTIVE",      "status_color": C_HIGH,   "key_issues": "Auto tariffs, pharma, NHS access",     "likelihood": "65%", "shipping_impact": "+8% UK-US lane volume"},
    {"parties": "CPTPP Expansion",      "status": "ACTIVE",      "status_color": C_HIGH,   "key_issues": "China membership bid, UK integration", "likelihood": "55%", "shipping_impact": "+15% intra-Pacific volume"},
    {"parties": "RCEP Updates",         "status": "ONGOING",     "status_color": C_ACCENT, "key_issues": "India re-engagement, digital trade",   "likelihood": "70%", "shipping_impact": "+10% intra-Asia volume"},
    {"parties": "US ↔ Vietnam FTA",     "status": "EXPLORATORY", "status_color": C_MOD,    "key_issues": "Currency manipulation, labor standards", "likelihood": "40%", "shipping_impact": "+22% Vietnam-US volume"},
]

# ── Historical tariff wars ─────────────────────────────────────────────────────
_HISTORY = [
    {"episode": "Trump 1.0 — Phase 1",   "period": "2018–2019",    "peak_rate": "25% on $250B",      "trade_drop": "-15%",                  "shipping_impact": "-8% transpacific TEUs",    "resolution": "Phase 1 deal Jan 2020"},
    {"episode": "COVID Disruption",      "period": "2020–2021",    "peak_rate": "Tariffs maintained", "trade_drop": "-12%",                  "shipping_impact": "+40% freight rates",       "resolution": "Supply chain normalization"},
    {"episode": "Trump 2.0 — Escalation","period": "Feb–Apr 2025", "peak_rate": "145% on all CN",    "trade_drop": "-35% projected",        "shipping_impact": "-28% transpacific bookings","resolution": "Ongoing / unresolved"},
    {"episode": "China Retaliation",     "period": "Apr 2025",     "peak_rate": "125% on all US",    "trade_drop": "-40% CN imports of US", "shipping_impact": "-25% westbound TP",        "resolution": "Ongoing / unresolved"},
]


# ── Tab-local cell helpers (style content only) ────────────────────────────────
def _mono(v: str, color: str = C_TEXT, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};font-variant-numeric:tabular-nums;">{v}</span>'
    )


def _sans(v: str, color: str = C_TEXT, weight: int = 400, size: str = "13px") -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};font-size:{size};">{v}</span>'
    )


def _impact_color(impact: str) -> str:
    if impact == "HIGH":
        return C_LOW
    if impact == "MODERATE":
        return C_MOD
    return C_HIGH


# ── Section 1: Trade War Dashboard ────────────────────────────────────────────
def _render_hero(macro_data: dict | None) -> None:
    try:
        logger.debug("trade_war | rendering hero dashboard")
        page_header(
            title="Trade War Intelligence",
            subtitle="US 145% / China 125% bilateral tariff regime — April 2025 escalation",
            badge_text="TRADE WAR",
            badge_color=C_ACCENT,
        )
        metric_card_row(
            [
                {"label": "US Tariff on China", "value": "145%",  "accent": C_LOW,    "sublabel": "All Chinese goods · Apr 2025"},
                {"label": "China Retaliation",  "value": "125%",  "accent": C_MOD,    "sublabel": "All US goods · Apr 2025"},
                {"label": "Est. Annual Impact", "value": "$582B", "accent": C_ACCENT, "delta": "-38% from 2024", "delta_color": C_LOW},
                {"label": "Ships Rerouted",     "value": "214",   "accent": C_MOD,    "sublabel": "Last 30 days · transpacific"},
            ],
            columns=4,
        )
        st.caption(
            "145% tariff = effective embargo on most goods · "
            "trade is diverting to Vietnam, Mexico, India rather than disappearing · "
            "transpacific bookings down 28% month-over-month."
        )
        st.markdown(source_footer([_DS_TARIFF_POLICY]), unsafe_allow_html=True)
    except Exception:
        logger.exception("trade_war | hero render failed")
        st.error("Dashboard hero failed to render.")


# ── Section 2: Tariff Impact by Commodity ─────────────────────────────────────
def _render_commodity_table() -> None:
    try:
        logger.debug("trade_war | rendering commodity table")
        section_header(
            "Tariff Impact by Commodity",
            "Full spectrum of affected goods — US 145% and China 125% retaliatory tariffs",
        )
        rows = []
        for c in _COMMODITIES:
            us_col  = C_LOW if c["us_tariff"] != "—" else C_TEXT3
            cn_col  = C_MOD if c["cn_tariff"] != "—" else C_TEXT3
            impact  = c["shipping_impact"]
            rows.append([
                _sans(c["name"], weight=700),
                _mono(c["us_tariff"], color=us_col, weight=700),
                _mono(c["cn_tariff"], color=cn_col, weight=700),
                _mono(f"${c['us_imports_bn']}B"),
                _mono(f"${c['tariff_burden_bn']}B", color=C_LOW, weight=600),
                badge(impact, color=_impact_color(impact)),
                _sans(c["alt_sources"], color=C_TEXT2, size="12px"),
            ])
        wsj_market_table(
            ["Commodity", "US Tariff on CN", "CN Tariff on US", "US Imports", "Tariff Burden", "Shipping Impact", "Alternative Sources"],
            rows,
        )
        st.caption(
            "Shipping Impact key — HIGH: major route disruption / volume loss · "
            "MODERATE: partial diversion, some resilience · "
            "LOW: limited impact, inelastic demand."
        )
        st.markdown(source_footer([_DS_TARIFF_POLICY]), unsafe_allow_html=True)
    except Exception:
        logger.exception("trade_war | commodity table failed")
        st.error("Commodity table failed to render.")


# ── Section 3: Trade Flow Diversion Map ───────────────────────────────────────
def _render_diversion_map() -> None:
    try:
        logger.debug("trade_war | rendering diversion map")
        section_header(
            "Trade Flow Diversion Map",
            "Tariffs causing diversion, not elimination — new shipping routes emerging",
        )
        fig = go.Figure()

        nodes = {
            "China": (35.0, 105.0),
            "USA": (38.0, -97.0),
            "Vietnam": (14.0, 108.0),
            "Mexico": (23.6, -102.4),
            "India": (20.6, 78.9),
            "Bangladesh": (23.7, 90.4),
            "Brazil": (-10.0, -55.0),
            "Indonesia": (-5.0, 117.0),
        }
        routes = [
            ("China", "USA", C_LOW, 4, "solid", "China→US (severely impacted)"),
            ("China", "Vietnam", C_ACCENT, 3, "dot", "China→Vietnam (components)"),
            ("Vietnam", "USA", C_HIGH, 3, "solid", "Vietnam→US (rerouted)"),
            ("China", "Mexico", "#7c6eaf", 2, "dot", "China→Mexico (nearshoring)"),
            ("Mexico", "USA", C_HIGH, 2, "solid", "Mexico→US (friendshored)"),
            ("Brazil", "China", C_MOD, 3, "solid", "Brazil→China soy (replacing US)"),
            ("India", "USA", "#4a90a4", 2, "dot", "India→US (emerging)"),
        ]
        for origin, dest, color, width, dash, label in routes:
            lat0, lon0 = nodes[origin]
            lat1, lon1 = nodes[dest]
            fig.add_trace(go.Scattergeo(
                lon=[lon0, lon1], lat=[lat0, lat1], mode="lines",
                line={"width": width, "color": color, "dash": dash},
                name=label, showlegend=True, hoverinfo="name",
            ))

        lats = [v[0] for v in nodes.values()]
        lons = [v[1] for v in nodes.values()]
        names = list(nodes.keys())
        colors_node = [
            C_LOW if n in ("China", "USA") else C_HIGH if n in ("Vietnam", "Mexico") else C_MOD
            for n in names
        ]
        fig.add_trace(go.Scattergeo(
            lon=lons, lat=lats, mode="markers+text",
            marker={"size": 12, "color": colors_node, "line": {"width": 1, "color": "#fff"}},
            text=names, textposition="top center",
            textfont={"color": C_TEXT, "size": 11},
            name="Ports / Countries", hoverinfo="text", showlegend=False,
        ))

        apply_dark_layout(fig, title="", height=420, showlegend=True)
        fig.update_layout(
            geo={
                "showframe": False, "showcoastlines": True,
                "coastlinecolor": "rgba(255,255,255,0.1)",
                "showland": True, "landcolor": C_CARD,
                "showocean": True, "oceancolor": C_BG,
                "showcountries": True,
                "countrycolor": "rgba(232,230,225,0.05)",
                "projection_type": "natural earth",
                "bgcolor": C_BG,
            },
            margin={"l": 0, "r": 0, "t": 0, "b": 0},
            legend={
                "bgcolor": C_CARD, "bordercolor": C_BORDER,
                "borderwidth": 1, "font": {"color": C_TEXT2, "size": 11},
                "x": 0.01, "y": 0.99,
            },
        )
        st.plotly_chart(fig, use_container_width=True, key="trade_diversion_map")

        col_l, col_w, col_e = st.columns(3, gap="medium")
        with col_l:
            st.markdown(insight_card_html(
                title="Direct China–US transpacific",
                score=0.85,
                action="Avoid",
                rationale="-28% bookings YTD; 145% effective tariff has collapsed direct flow.",
                category="LOSERS",
            ), unsafe_allow_html=True)
        with col_w:
            st.markdown(insight_card_html(
                title="Vietnam, Mexico, India lanes",
                score=0.75,
                action="Buy",
                rationale="+35-55% volume growth as production migrates out of China.",
                category="WINNERS",
            ), unsafe_allow_html=True)
        with col_e:
            st.markdown(insight_card_html(
                title="Brazil-China agricultural route",
                score=0.55,
                action="Watch",
                rationale="Brazil filling US soy gap; structural shift in Pacific bulk flows.",
                category="EMERGING",
            ), unsafe_allow_html=True)
        st.markdown(source_footer([_DS_TRADE_FLOWS]), unsafe_allow_html=True)
    except Exception:
        logger.exception("trade_war | diversion map failed")
        st.error("Trade flow diversion map failed to render.")


# ── Section 4: Nearshoring & Friendshoring ────────────────────────────────────
def _render_nearshoring() -> None:
    try:
        logger.debug("trade_war | rendering nearshoring section")
        section_header(
            "Nearshoring & Friendshoring",
            "Manufacturing migrating from China — new shipping corridors forming",
        )
        shifts = [
            {"country": "Vietnam",    "sectors": "Electronics · Textiles · Furniture",   "volume_growth": "+55%", "color": C_HIGH, "lane": "Vietnam → US Pacific"},
            {"country": "Mexico",     "sectors": "Auto Parts · Machinery · Appliances",  "volume_growth": "+42%", "color": C_HIGH, "lane": "Mexico → US land / Gulf"},
            {"country": "India",      "sectors": "Pharma · Textiles · Software goods",   "volume_growth": "+31%", "color": C_MOD,  "lane": "India → US (Suez / Pacific)"},
            {"country": "Bangladesh", "sectors": "Apparel · Textiles",                   "volume_growth": "+28%", "color": C_MOD,  "lane": "Bangladesh → US (Suez)"},
            {"country": "Indonesia",  "sectors": "Electronics assembly · Palm oil",      "volume_growth": "+19%", "color": C_MOD,  "lane": "Indonesia → US Pacific"},
        ]
        metric_card_row(
            [
                {
                    "label": s["country"],
                    "value": s["volume_growth"],
                    "accent": s["color"],
                    "delta": s["lane"],
                    "delta_color": C_ACCENT,
                    "sublabel": s["sectors"],
                }
                for s in shifts
            ],
            columns=len(shifts),
        )

        commentary_rows = [
            ["Vietnam",    "Primary China+1 beneficiary. Nike, Apple, Samsung shifting production."],
            ["Mexico",     "USMCA advantage. Tesla Monterrey, GM expansions driving nearshore boom."],
            ["India",      "Slower regulatory environment but massive labor cost advantage."],
            ["Bangladesh", "Garment sector surging. Factory capacity straining port infrastructure."],
            ["Indonesia",  "Growing electronics hub. Nickel processing for EV supply chains."],
        ]
        wsj_market_table(
            ["Origin", "Why it is winning"],
            [[_sans(name, weight=700), _sans(note, color=C_TEXT2, size="12px")] for name, note in commentary_rows],
        )
        st.markdown(source_footer([_DS_TRADE_FLOWS]), unsafe_allow_html=True)
    except Exception:
        logger.exception("trade_war | nearshoring section failed")
        st.error("Nearshoring section failed to render.")


# ── Section 5: Shipping Volume Impact ─────────────────────────────────────────
def _render_volume_chart() -> None:
    try:
        logger.debug("trade_war | rendering volume chart")
        section_header(
            "Shipping Volume Impact — Transpacific",
            "Monthly container volumes before and after tariff escalation (TEUs, thousands)",
        )
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        baseline_2024    = [920, 870, 960, 1010, 1050, 980, 1020, 1040, 990, 1000, 1060, 1100]
        post_tariff_2025 = [910, 900, 930, 680, 560, 520, 540, 580, 610, 650, 680, 720]
        vietnam_us_2025  = [80, 85, 95, 130, 170, 195, 210, 220, 215, 225, 240, 260]
        mexico_us_2025   = [150, 155, 162, 178, 195, 208, 220, 228, 232, 238, 245, 250]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=months, y=baseline_2024, name="China→US 2024 (Baseline)",
            line={"color": C_ACCENT, "width": 2, "dash": "dash"}, mode="lines",
            hovertemplate="%{y}K TEUs<extra>China→US 2024</extra>",
        ))
        fig.add_trace(go.Scatter(
            x=months, y=post_tariff_2025, name="China→US 2025 (Post-Tariff)",
            line={"color": C_LOW, "width": 3}, mode="lines+markers",
            marker={"size": 6}, fill="tonexty", fillcolor=_hex_to_rgba(C_LOW, 0.08),
            hovertemplate="%{y}K TEUs<extra>China→US 2025</extra>",
        ))
        fig.add_trace(go.Scatter(
            x=months, y=vietnam_us_2025, name="Vietnam→US 2025 (Rerouted)",
            line={"color": C_HIGH, "width": 2}, mode="lines+markers",
            marker={"size": 5},
            hovertemplate="%{y}K TEUs<extra>Vietnam→US 2025</extra>",
        ))
        fig.add_trace(go.Scatter(
            x=months, y=mexico_us_2025, name="Mexico→US 2025 (Nearshored)",
            line={"color": "#7c6eaf", "width": 2}, mode="lines+markers",
            marker={"size": 5},
            hovertemplate="%{y}K TEUs<extra>Mexico→US 2025</extra>",
        ))
        # add_vline's annotation positioning averages the x-coords, which fails
        # on a categorical axis — draw the line and annotation separately.
        fig.add_vline(x="Apr", line_dash="dot", line_color=C_LOW, line_width=2)
        fig.add_annotation(
            x="Apr", y=1.0, yref="paper", yanchor="bottom",
            text="145% tariff", showarrow=False,
            font={"color": C_LOW, "size": 11},
        )
        apply_dark_layout(fig, title="", height=360, showlegend=True)
        fig.update_layout(
            xaxis={"gridcolor": C_BORDER, "title": "Month 2025"},
            yaxis={"gridcolor": C_BORDER, "title": "TEUs (thousands)"},
            legend={"bgcolor": "rgba(0,0,0,0)", "font": {"size": 11}},
            margin={"l": 50, "r": 20, "t": 20, "b": 40},
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True, key="transpacific_volume_chart")

        carrier_col1, carrier_col2 = st.columns(2, gap="large")
        cut_rows = [("COSCO", -22, C_LOW), ("MSC", -18, C_LOW), ("Evergreen", -14, C_LOW), ("Yang Ming", -8, C_MOD)]
        add_rows = [("Maersk (Vietnam)", 16, C_HIGH), ("CMA CGM (India)", 12, C_HIGH), ("Hapag-Lloyd (SE Asia)", 10, C_HIGH), ("ONE (Indonesia)", 6, C_MOD)]
        with carrier_col1:
            st.markdown('<div class="sub-section-header">Carriers Cutting Transpacific Capacity</div>', unsafe_allow_html=True)
            wsj_market_table(
                ["Carrier", "Δ Sailings"],
                [[_sans(name, weight=700), _mono(f"{delta:+d}", color=color, weight=700)]
                 for name, delta, color in cut_rows],
            )
        with carrier_col2:
            st.markdown('<div class="sub-section-header">Carriers Adding ASEAN Capacity</div>', unsafe_allow_html=True)
            wsj_market_table(
                ["Carrier", "Δ Sailings"],
                [[_sans(name, weight=700), _mono(f"{delta:+d}", color=color, weight=700)]
                 for name, delta, color in add_rows],
            )
        st.markdown(source_footer([_DS_VOLUME]), unsafe_allow_html=True)
    except Exception:
        logger.exception("trade_war | volume chart failed")
        st.error("Shipping volume chart failed to render.")


# ── Section 6: Trade Deal Tracker ─────────────────────────────────────────────
def _render_deal_tracker() -> None:
    try:
        logger.debug("trade_war | rendering deal tracker")
        section_header(
            "Trade Deal Tracker",
            "Active negotiations and bilateral agreements — shipping impact if resolved",
        )
        rows = []
        for d in _TRADE_DEALS:
            rows.append([
                _sans(d["parties"], weight=700),
                badge(d["status"], color=d["status_color"]),
                _sans(d["key_issues"], color=C_TEXT2, size="12px"),
                _mono(d["likelihood"], color=C_ACCENT, weight=700),
                _sans(d["shipping_impact"], color=C_HIGH, size="12px"),
            ])
        wsj_market_table(
            ["Parties", "Status", "Key Issues", "Likelihood", "Shipping Impact if Resolved"],
            rows,
        )
        st.markdown(source_footer([_DS_DEALS]), unsafe_allow_html=True)
    except Exception:
        logger.exception("trade_war | deal tracker failed")
        st.error("Trade deal tracker failed to render.")


# ── Section 7: Historical Tariff Wars ─────────────────────────────────────────
def _render_history() -> None:
    try:
        logger.debug("trade_war | rendering historical comparison")
        section_header(
            "Historical Tariff Wars",
            "Shipping market behavior across tariff escalation episodes",
        )
        episode_colors = [C_ACCENT, C_MOD, C_LOW, C_LOW]
        metric_card_row(
            [
                {
                    "label": f"{h['period']} — {h['episode']}",
                    "value": h["peak_rate"],
                    "accent": color,
                    "delta": h["trade_drop"],
                    "delta_color": C_LOW,
                    "sublabel": h["shipping_impact"],
                }
                for h, color in zip(_HISTORY, episode_colors)
            ],
            columns=len(_HISTORY),
        )

        resolution_rows = [
            [_sans(h["episode"], weight=700), _sans(h["resolution"], color=C_TEXT2, size="12px")]
            for h in _HISTORY
        ]
        wsj_market_table(["Episode", "Resolution"], resolution_rows)
        st.markdown(source_footer([_DS_HISTORY]), unsafe_allow_html=True)
    except Exception:
        logger.exception("trade_war | history section failed")
        st.error("Historical tariff wars section failed to render.")


# ── Section 8: Scenario — Trade De-escalation ─────────────────────────────────
def _render_scenario() -> None:
    try:
        logger.debug("trade_war | rendering de-escalation scenario")
        section_header(
            "Scenario: Trade De-Escalation",
            "If US-China tariffs reduced to 50% by end of 2026 — modeled market recovery",
        )
        st.markdown(insight_card_html(
            title="Base scenario — tariffs fall from 145% to 50% by Q4 2026",
            score=0.6,
            action="Watch",
            rationale=(
                "A partial de-escalation — driven by bilateral negotiations, economic pressure, "
                "or a new framework deal — would unlock significant suppressed trade demand. "
                "Not a full reversal: some manufacturing has already relocated and supply chains "
                "have restructured. But the volume recovery would be substantial and rapid."
            ),
            category="SCENARIO",
        ), unsafe_allow_html=True)
        metric_card_row(
            [
                {"label": "Volume Recovery",      "value": "+28%",       "accent": C_HIGH,   "sublabel": "transpacific TEUs"},
                {"label": "Freight Rate Impact",  "value": "+$400–600",  "accent": C_MOD,    "sublabel": "per FEU on transpacific"},
                {"label": "Timeline to Recovery", "value": "6–9 months", "accent": C_ACCENT, "sublabel": "post-deal announcement"},
                {"label": "Stranded Capacity",    "value": "1.8M TEU",   "accent": C_MOD,    "sublabel": "returns to service"},
            ],
            columns=4,
        )

        winners = [
            ("China–US Transpacific", "+30–35%"),
            ("COSCO / Evergreen", "+25–30%"),
            ("Shanghai / Ningbo ports", "+20% TEU throughput"),
            ("US agricultural exporters", "+$8B soy/LNG"),
        ]
        losers = [
            ("Vietnam→US (post-deal)", "~60% retained"),
            ("Mexico nearshoring", "~70% retained"),
            ("Brazil→China soy route", "~50% retained"),
            ("India pharma/textile", "~80% retained"),
        ]
        col_w, col_l = st.columns(2, gap="large")
        with col_w:
            st.markdown('<div class="sub-section-header">Winner Carriers and Routes</div>', unsafe_allow_html=True)
            wsj_market_table(
                ["Beneficiary", "Upside"],
                [[_sans(name, weight=700), _mono(val, color=C_HIGH, weight=700)] for name, val in winners],
            )
        with col_l:
            st.markdown('<div class="sub-section-header">Volume Lost to Permanent Diversion</div>', unsafe_allow_html=True)
            wsj_market_table(
                ["Diverted Lane", "Retained Share"],
                [[_sans(name, weight=700), _mono(val, color=C_MOD, weight=700)] for name, val in losers],
            )

        st.markdown(insight_card_html(
            title="Key insight — supply chain reconfiguration is largely permanent",
            score=0.8,
            action="Caution",
            rationale=(
                "Even a full tariff reversal would not restore pre-2025 trade patterns. An estimated "
                "30–40% of diverted manufacturing stays in Vietnam, Mexico, and India permanently — "
                "the supply chain reconfiguration has already happened. The de-escalation upside for "
                "shipping is real but structurally capped."
            ),
            category="OUTLOOK",
        ), unsafe_allow_html=True)
        st.markdown(source_footer([_DS_SCENARIO]), unsafe_allow_html=True)
    except Exception:
        logger.exception("trade_war | scenario section failed")
        st.error("De-escalation scenario section failed to render.")


# ── Main render ────────────────────────────────────────────────────────────────
def render(macro_data=None, freight_data=None, insights=None, *args, **kwargs) -> None:
    """Render the Trade Policy & Tariff Impact Intelligence tab."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('trade_war'):
        try:
            logger.info("trade_war | render start")
            _render_hero(macro_data)
            section_divider("Tariff Exposure")
            _render_commodity_table()
            section_divider("Flow Diversion")
            _render_diversion_map()
            section_divider("Supply-Chain Shift")
            _render_nearshoring()
            section_divider("Volume Impact")
            _render_volume_chart()
            section_divider("Negotiation Tracker")
            _render_deal_tracker()
            section_divider("Historical Context")
            _render_history()
            section_divider("Scenario")
            _render_scenario()
            logger.info("trade_war | render complete")
        except Exception:
            logger.exception("trade_war | render failed")
            st.error("Trade War tab encountered an error. Check logs for details.")
