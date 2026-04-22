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

from ui.styles import (
    C_ACCENT,
    C_BG,
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
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    wsj_market_table,
)


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
            badge_text="LIVE TARIFF SITUATION",
            badge_color=C_LOW,
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
        st.html(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-radius:6px;padding:14px 20px;margin-top:4px;'
            f'display:flex;gap:32px;flex-wrap:wrap;">'
            f'<div style="color:{C_TEXT2};font-size:13px;">'
            f'<span style="color:{C_LOW};font-weight:700;">&#9650;</span> '
            f'145% tariff = effective embargo on most goods</div>'
            f'<div style="color:{C_TEXT2};font-size:13px;">'
            f'<span style="color:{C_MOD};font-weight:700;">&#9654;</span> '
            f'Trade diverting to Vietnam, Mexico, India — not disappearing</div>'
            f'<div style="color:{C_TEXT2};font-size:13px;">'
            f'<span style="color:{C_ACCENT};font-weight:700;">&#9679;</span> '
            f'Transpacific bookings down 28% month-over-month</div>'
            f'</div>'
        )
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
        st.html(
            f'<div style="display:flex;gap:20px;margin-top:10px;flex-wrap:wrap;">'
            f'<div style="display:flex;align-items:center;gap:6px;">'
            f'{badge("HIGH", color=C_LOW)}'
            f'<span style="color:{C_TEXT3};font-size:12px;">Major route disruption / volume loss</span></div>'
            f'<div style="display:flex;align-items:center;gap:6px;">'
            f'{badge("MODERATE", color=C_MOD)}'
            f'<span style="color:{C_TEXT3};font-size:12px;">Partial diversion, some resilience</span></div>'
            f'<div style="display:flex;align-items:center;gap:6px;">'
            f'{badge("LOW", color=C_HIGH)}'
            f'<span style="color:{C_TEXT3};font-size:12px;">Limited impact, inelastic demand</span></div>'
            f'</div>'
        )
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

        st.html(
            f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:4px;">'
            f'<div style="background:{C_LOW}11;border:1px solid {C_LOW}33;'
            f'border-radius:8px;padding:12px 16px;">'
            f'<div style="color:{C_LOW};font-size:12px;font-weight:700;">&#9660; Losers</div>'
            f'<div style="color:{C_TEXT};font-size:13px;margin-top:4px;">Direct China–US transpacific</div>'
            f'<div style="color:{C_TEXT2};font-size:12px;">-28% bookings YTD</div></div>'
            f'<div style="background:{C_HIGH}11;border:1px solid {C_HIGH}33;'
            f'border-radius:8px;padding:12px 16px;">'
            f'<div style="color:{C_HIGH};font-size:12px;font-weight:700;">&#9650; Winners</div>'
            f'<div style="color:{C_TEXT};font-size:13px;margin-top:4px;">Vietnam, Mexico, India lanes</div>'
            f'<div style="color:{C_TEXT2};font-size:12px;">+35–55% volume growth</div></div>'
            f'<div style="background:{C_ACCENT}11;border:1px solid {C_ACCENT}33;'
            f'border-radius:8px;padding:12px 16px;">'
            f'<div style="color:{C_ACCENT};font-size:12px;font-weight:700;">&#9654; Emerging</div>'
            f'<div style="color:{C_TEXT};font-size:13px;margin-top:4px;">Brazil–China agricultural route</div>'
            f'<div style="color:{C_TEXT2};font-size:12px;">Brazil fills US soy gap</div></div>'
            f'</div>'
        )
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
            {"country": "Vietnam",    "flag": "🇻🇳", "sectors": "Electronics, Textiles, Furniture",   "volume_growth": "+55%", "color": C_HIGH, "commentary": "Primary China+1 beneficiary. Nike, Apple, Samsung shifting production.", "lane": "Vietnam → US Pacific"},
            {"country": "Mexico",     "flag": "🇲🇽", "sectors": "Auto Parts, Machinery, Appliances",  "volume_growth": "+42%", "color": C_HIGH, "commentary": "USMCA advantage. Tesla Monterrey, GM expansions driving nearshore boom.", "lane": "Mexico → US land / Gulf"},
            {"country": "India",      "flag": "🇮🇳", "sectors": "Pharma, Textiles, Software goods",   "volume_growth": "+31%", "color": C_MOD,  "commentary": "Slower regulatory environment but massive labor cost advantage.",         "lane": "India → US (Suez / Pacific)"},
            {"country": "Bangladesh", "flag": "🇧🇩", "sectors": "Apparel, Textiles",                  "volume_growth": "+28%", "color": C_MOD,  "commentary": "Garment sector surging. Factory capacity straining port infrastructure.",  "lane": "Bangladesh → US (Suez)"},
            {"country": "Indonesia",  "flag": "🇮🇩", "sectors": "Electronics assembly, Palm oil",     "volume_growth": "+19%", "color": C_MOD,  "commentary": "Growing electronics hub. Nickel processing for EV supply chains.",         "lane": "Indonesia → US Pacific"},
        ]
        cols = st.columns(len(shifts))
        for col, s in zip(cols, shifts):
            with col:
                st.html(
                    f'<div style="background:{C_CARD};border:1px solid {s["color"]}33;'
                    f'border-radius:6px;padding:16px;height:100%;">'
                    f'<div style="font-size:24px;margin-bottom:6px;">{s["flag"]}</div>'
                    f'<div style="color:{C_TEXT};font-size:14px;font-weight:700;">{s["country"]}</div>'
                    f'<div style="color:{s["color"]};font-size:22px;font-weight:800;margin:6px 0;">{s["volume_growth"]}</div>'
                    f'<div style="color:{C_TEXT2};font-size:11px;margin-bottom:8px;">volume growth YTD</div>'
                    f'<div style="color:{C_TEXT3};font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Sectors</div>'
                    f'<div style="color:{C_TEXT2};font-size:12px;margin-bottom:8px;">{s["sectors"]}</div>'
                    f'<div style="color:{C_TEXT3};font-size:11px;line-height:1.5;">{s["commentary"]}</div>'
                    f'<div style="background:{C_SURFACE};border-radius:6px;padding:6px 10px;margin-top:10px;">'
                    f'<div style="color:{C_ACCENT};font-size:11px;font-weight:600;">&#9658; {s["lane"]}</div></div>'
                    f'</div>'
                )
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
            marker={"size": 6}, fill="tonexty", fillcolor=f"{C_LOW}15",
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
        fig.add_vline(
            x="Apr", line_dash="dot", line_color=C_LOW, line_width=2,
            annotation_text="145% tariff", annotation_font_color=C_LOW,
            annotation_font_size=11,
        )
        apply_dark_layout(fig, title="", height=360, showlegend=True)
        fig.update_layout(
            xaxis={"gridcolor": C_BORDER, "title": "Month 2025"},
            yaxis={"gridcolor": C_BORDER, "title": "TEUs (thousands)"},
            legend={"bgcolor": "transparent", "font": {"size": 11}},
            margin={"l": 50, "r": 20, "t": 20, "b": 40},
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True, key="transpacific_volume_chart")

        carrier_col1, carrier_col2 = st.columns(2)
        cut_rows = [("COSCO", -22, C_LOW), ("MSC", -18, C_LOW), ("Evergreen", -14, C_LOW), ("Yang Ming", -8, C_MOD)]
        add_rows = [("Maersk (Vietnam)", 16, C_HIGH), ("CMA CGM (India)", 12, C_HIGH), ("Hapag-Lloyd (SE Asia)", 10, C_HIGH), ("ONE (Indonesia)", 6, C_MOD)]
        with carrier_col1:
            _render_carrier_card("Carriers Cutting Transpacific Capacity", cut_rows)
        with carrier_col2:
            _render_carrier_card("Carriers Adding ASEAN Capacity", add_rows)
    except Exception:
        logger.exception("trade_war | volume chart failed")
        st.error("Shipping volume chart failed to render.")


def _render_carrier_card(title: str, rows: list[tuple[str, int, str]]) -> None:
    body = "".join(
        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
        f'<span style="color:{C_TEXT};font-size:13px;">{name}</span>'
        f'<span style="color:{color};font-weight:700;">{delta:+d} sailings</span></div>'
        for name, delta, color in rows
    )
    st.html(
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
        f'border-radius:6px;padding:20px 24px;margin-bottom:16px;">'
        f'<div style="color:{C_TEXT};font-size:14px;font-weight:700;margin-bottom:12px;">{title}</div>'
        f'<div style="display:flex;flex-direction:column;gap:8px;">{body}</div>'
        f'</div>'
    )


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
        cols = st.columns(len(_HISTORY))
        episode_colors = [C_ACCENT, C_MOD, C_LOW, C_LOW]
        for col, h, color in zip(cols, _HISTORY, episode_colors):
            with col:
                st.html(
                    f'<div style="background:{C_CARD};border:1px solid {color}33;'
                    f'border-radius:6px;padding:16px;">'
                    f'<div style="color:{color};font-size:11px;font-weight:700;'
                    f'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;">'
                    f'{h["period"]}</div>'
                    f'<div style="color:{C_TEXT};font-size:13px;font-weight:700;'
                    f'margin-bottom:12px;line-height:1.4;">{h["episode"]}</div>'
                    f'<div style="display:flex;flex-direction:column;gap:8px;">'
                    f'<div><div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
                    f'letter-spacing:0.5px;">Peak Rate</div>'
                    f'<div style="color:{C_TEXT};font-size:13px;">{h["peak_rate"]}</div></div>'
                    f'<div><div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
                    f'letter-spacing:0.5px;">Trade Drop</div>'
                    f'<div style="color:{C_LOW};font-size:13px;font-weight:600;">{h["trade_drop"]}</div></div>'
                    f'<div><div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
                    f'letter-spacing:0.5px;">Shipping Impact</div>'
                    f'<div style="color:{C_MOD};font-size:13px;">{h["shipping_impact"]}</div></div>'
                    f'<div style="background:{C_SURFACE};border-radius:6px;padding:8px 10px;margin-top:4px;">'
                    f'<div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
                    f'letter-spacing:0.5px;margin-bottom:4px;">Resolution</div>'
                    f'<div style="color:{C_TEXT2};font-size:12px;">{h["resolution"]}</div></div>'
                    f'</div></div>'
                )
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
        st.html(
            f'<div style="background:linear-gradient(135deg,{C_HIGH}11,{C_CARD});'
            f'border:1px solid {C_HIGH}33;border-radius:6px;padding:20px 24px;margin-bottom:16px;">'
            f'<div style="color:{C_HIGH};font-size:13px;font-weight:700;margin-bottom:12px;">'
            f'&#9654; Base Scenario: Tariffs fall from 145% → 50% by Q4 2026</div>'
            f'<div style="color:{C_TEXT2};font-size:13px;line-height:1.7;">'
            f'A partial de-escalation — driven by bilateral negotiations, economic pressure, or a new '
            f'framework deal — would unlock significant suppressed trade demand. Not a full reversal: '
            f'some manufacturing has already relocated, supply chains have restructured. '
            f'But the volume recovery would be substantial and rapid.</div>'
            f'</div>'
        )
        metric_card_row(
            [
                {"label": "Volume Recovery",      "value": "+28%",       "accent": C_HIGH,   "sublabel": "transpacific TEUs"},
                {"label": "Freight Rate Impact",  "value": "+$400–600",  "accent": C_MOD,    "sublabel": "per FEU on transpacific"},
                {"label": "Timeline to Recovery", "value": "6–9 months", "accent": C_ACCENT, "sublabel": "post-deal announcement"},
                {"label": "Stranded Capacity",    "value": "1.8M TEU",   "accent": C_MOD,    "sublabel": "returns to service"},
            ],
            columns=4,
        )
        winners = [("China–US Transpacific", "+30–35%"), ("COSCO / Evergreen", "+25–30%"), ("Shanghai / Ningbo ports", "+20% TEU throughput"), ("US agricultural exporters", "+$8B soy/LNG")]
        losers  = [("Vietnam→US (post-deal)", "~60% retained"), ("Mexico nearshoring", "~70% retained"), ("Brazil→China soy route", "~50% retained"), ("India pharma/textile", "~80% retained")]
        col_w, col_l = st.columns(2)
        with col_w:
            _render_scenario_card("▲ Winner Carriers & Routes", winners, C_HIGH)
        with col_l:
            _render_scenario_card("▼ Volume Lost to Permanent Diversion", losers, C_MOD)

        st.html(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-left:3px solid {C_ACCENT};border-radius:8px;'
            f'padding:14px 18px;margin-top:4px;">'
            f'<span style="color:{C_ACCENT};font-weight:700;">Key insight: </span>'
            f'<span style="color:{C_TEXT2};font-size:13px;">'
            f'Even a full tariff reversal would not restore pre-2025 trade patterns. '
            f'An estimated 30–40% of diverted manufacturing stays in Vietnam, Mexico, and India '
            f'permanently — the supply chain reconfiguration has already happened. '
            f'The de-escalation upside for shipping is real but structurally capped.</span>'
            f'</div>'
        )
    except Exception:
        logger.exception("trade_war | scenario section failed")
        st.error("De-escalation scenario section failed to render.")


def _render_scenario_card(title: str, rows: list[tuple[str, str]], title_color: str) -> None:
    body = "".join(
        f'<div style="display:flex;justify-content:space-between;">'
        f'<span style="color:{C_TEXT};font-size:13px;">{name}</span>'
        f'<span style="color:{title_color if "%" in val or "retained" not in val else C_TEXT2};font-weight:700;">{val}</span></div>'
        for name, val in rows
    )
    st.html(
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
        f'border-radius:6px;padding:20px 24px;margin-bottom:16px;">'
        f'<div style="color:{title_color};font-size:14px;font-weight:700;margin-bottom:12px;">{title}</div>'
        f'<div style="display:flex;flex-direction:column;gap:8px;">{body}</div>'
        f'</div>'
    )


# ── Main render ────────────────────────────────────────────────────────────────
def render(macro_data=None, freight_data=None, insights=None) -> None:
    """Render the Trade Policy & Tariff Impact Intelligence tab."""
    try:
        logger.info("trade_war | render start")
        _render_hero(macro_data)
        section_divider()
        _render_commodity_table()
        section_divider()
        _render_diversion_map()
        section_divider()
        _render_nearshoring()
        section_divider()
        _render_volume_chart()
        section_divider()
        _render_deal_tracker()
        section_divider()
        _render_history()
        section_divider()
        _render_scenario()
        logger.info("trade_war | render complete")
    except Exception:
        logger.exception("trade_war | render failed")
        st.error("Trade War tab encountered an error. Check logs for details.")
