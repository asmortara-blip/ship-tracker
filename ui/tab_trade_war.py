"""tab_trade_war.py — Trade Policy & Tariff Impact Intelligence Dashboard.

Sections:
    1. Trade War Dashboard (hero) — US-China tariff rates, impact metrics, live badge
    2. Tariff Impact by Commodity — comprehensive color-coded table
    3. Trade Flow Diversion Map — Plotly scatter_geo with rerouting arrows
    4. Nearshoring & Friendshoring — supply chain shift analysis
    5. Shipping Volume Impact — transpacific container volumes before/after tariffs
    6. Trade Deal Tracker — active negotiations and bilateral agreements
    7. Historical Tariff Wars — comparison across tariff episodes
    8. Scenario: Trade De-escalation — modeled recovery if tariffs fall to 50%
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ── Color palette ──────────────────────────────────────────────────────────────
C_BG      = "#0c0e14"
C_SURFACE = "#12151e"
C_CARD    = "#181c28"
C_BORDER  = "rgba(232,230,225,0.06)"
C_HIGH    = "#2e9e6e"
C_MOD     = "#c9962b"
C_LOW     = "#c0392b"
C_ACCENT  = "#3572b0"
C_TEXT    = "#e8e6e1"
C_TEXT2   = "#9a968e"
C_TEXT3   = "#6b6760"

# ── Commodity data ─────────────────────────────────────────────────────────────
_COMMODITIES = [
    {
        "name": "Electronics",
        "us_tariff": "145%",
        "cn_tariff": "—",
        "us_imports_bn": 168,
        "tariff_burden_bn": 244,
        "shipping_impact": "HIGH",
        "alt_sources": "Vietnam, India, Mexico",
    },
    {
        "name": "Auto Parts",
        "us_tariff": "145%",
        "cn_tariff": "—",
        "us_imports_bn": 54,
        "tariff_burden_bn": 78,
        "shipping_impact": "HIGH",
        "alt_sources": "Mexico, South Korea, Germany",
    },
    {
        "name": "Machinery",
        "us_tariff": "145%",
        "cn_tariff": "—",
        "us_imports_bn": 115,
        "tariff_burden_bn": 167,
        "shipping_impact": "HIGH",
        "alt_sources": "Germany, Japan, South Korea",
    },
    {
        "name": "Steel & Aluminum",
        "us_tariff": "145% + 232%",
        "cn_tariff": "—",
        "us_imports_bn": 12,
        "tariff_burden_bn": 44,
        "shipping_impact": "HIGH",
        "alt_sources": "Canada, Brazil, India",
    },
    {
        "name": "Textiles & Apparel",
        "us_tariff": "145%",
        "cn_tariff": "—",
        "us_imports_bn": 42,
        "tariff_burden_bn": 61,
        "shipping_impact": "HIGH",
        "alt_sources": "Bangladesh, Vietnam, Cambodia",
    },
    {
        "name": "Chemicals",
        "us_tariff": "145%",
        "cn_tariff": "—",
        "us_imports_bn": 28,
        "tariff_burden_bn": 41,
        "shipping_impact": "MODERATE",
        "alt_sources": "Germany, India, Singapore",
    },
    {
        "name": "Soybeans",
        "us_tariff": "—",
        "cn_tariff": "125%",
        "us_imports_bn": 14,
        "tariff_burden_bn": 18,
        "shipping_impact": "HIGH",
        "alt_sources": "Brazil, Argentina (replacing US)",
    },
    {
        "name": "LNG",
        "us_tariff": "—",
        "cn_tariff": "125%",
        "us_imports_bn": 8,
        "tariff_burden_bn": 10,
        "shipping_impact": "MODERATE",
        "alt_sources": "Qatar, Australia, Russia",
    },
    {
        "name": "Semiconductors",
        "us_tariff": "Complex/phased",
        "cn_tariff": "125%",
        "us_imports_bn": 22,
        "tariff_burden_bn": 31,
        "shipping_impact": "MODERATE",
        "alt_sources": "Taiwan, South Korea, Netherlands",
    },
    {
        "name": "Pharmaceuticals",
        "us_tariff": "145%",
        "cn_tariff": "—",
        "us_imports_bn": 9,
        "tariff_burden_bn": 13,
        "shipping_impact": "LOW",
        "alt_sources": "India, Ireland, Germany",
    },
    {
        "name": "Furniture",
        "us_tariff": "145%",
        "cn_tariff": "—",
        "us_imports_bn": 19,
        "tariff_burden_bn": 28,
        "shipping_impact": "MODERATE",
        "alt_sources": "Vietnam, Malaysia, Mexico",
    },
    {
        "name": "Plastics",
        "us_tariff": "145%",
        "cn_tariff": "—",
        "us_imports_bn": 16,
        "tariff_burden_bn": 23,
        "shipping_impact": "MODERATE",
        "alt_sources": "South Korea, Germany, Saudi Arabia",
    },
]

# ── Trade deal tracker data ────────────────────────────────────────────────────
_TRADE_DEALS = [
    {
        "parties": "US ↔ China",
        "status": "STALLED",
        "status_color": C_LOW,
        "key_issues": "Fentanyl, tech transfer, Taiwan",
        "likelihood": "15%",
        "shipping_impact": "+38% transpacific volume",
    },
    {
        "parties": "US ↔ EU (TTIP revival)",
        "status": "EXPLORATORY",
        "status_color": C_MOD,
        "key_issues": "Digital taxes, agriculture, carbon border",
        "likelihood": "30%",
        "shipping_impact": "+12% transatlantic volume",
    },
    {
        "parties": "US ↔ UK",
        "status": "ACTIVE",
        "status_color": C_HIGH,
        "key_issues": "Auto tariffs, pharma, NHS access",
        "likelihood": "65%",
        "shipping_impact": "+8% UK-US lane volume",
    },
    {
        "parties": "CPTPP Expansion",
        "status": "ACTIVE",
        "status_color": C_HIGH,
        "key_issues": "China membership bid, UK integration",
        "likelihood": "55%",
        "shipping_impact": "+15% intra-Pacific volume",
    },
    {
        "parties": "RCEP Updates",
        "status": "ONGOING",
        "status_color": C_ACCENT,
        "key_issues": "India re-engagement, digital trade",
        "likelihood": "70%",
        "shipping_impact": "+10% intra-Asia volume",
    },
    {
        "parties": "US ↔ Vietnam FTA",
        "status": "EXPLORATORY",
        "status_color": C_MOD,
        "key_issues": "Currency manipulation, labor standards",
        "likelihood": "40%",
        "shipping_impact": "+22% Vietnam-US volume",
    },
]

# ── Historical tariff wars ─────────────────────────────────────────────────────
_HISTORY = [
    {
        "episode": "Trump 1.0 — Phase 1",
        "period": "2018–2019",
        "peak_rate": "25% on $250B",
        "trade_drop": "-15%",
        "shipping_impact": "-8% transpacific TEUs",
        "resolution": "Phase 1 deal Jan 2020",
        "duration_months": 18,
    },
    {
        "episode": "COVID Disruption",
        "period": "2020–2021",
        "peak_rate": "Tariffs maintained",
        "trade_drop": "-12%",
        "shipping_impact": "+40% freight rates",
        "resolution": "Supply chain normalization",
        "duration_months": 24,
    },
    {
        "episode": "Trump 2.0 — Escalation",
        "period": "Feb–Apr 2025",
        "peak_rate": "145% on all CN",
        "trade_drop": "-35% projected",
        "shipping_impact": "-28% transpacific bookings",
        "resolution": "Ongoing / unresolved",
        "duration_months": 2,
    },
    {
        "episode": "China Retaliation",
        "period": "Apr 2025",
        "peak_rate": "125% on all US",
        "trade_drop": "-40% CN imports of US goods",
        "shipping_impact": "-25% westbound transpacific",
        "resolution": "Ongoing / unresolved",
        "duration_months": 1,
    },
]


def _impact_color(impact: str) -> str:
    if impact == "HIGH":
        return C_LOW
    if impact == "MODERATE":
        return C_MOD
    return C_HIGH


def _impact_badge(impact: str) -> str:
    color = _impact_color(impact)
    return (
        f'<span style="background:{color}22;color:{color};'
        f'border:1px solid {color}44;border-radius:4px;'
        f'padding:2px 8px;font-size:11px;font-weight:700;'
        f'letter-spacing:0.5px;">{impact}</span>'
    )


def _card_open(border_color: str = C_BORDER, extra_style: str = "") -> str:
    return (
        f'<div style="background:{C_CARD};border:1px solid {border_color};'
        f'border-radius:6px;padding:20px 24px;margin-bottom:16px;{extra_style}">'
    )


def _section_header(title: str, subtitle: str = "") -> str:
    sub_html = (
        f'<div style="color:{C_TEXT2};font-size:13px;margin-top:4px;">{subtitle}</div>'
        if subtitle
        else ""
    )
    return (
        f'<div style="margin:28px 0 16px 0;">'
        f'<div style="color:{C_TEXT};font-size:18px;font-weight:700;'
        f'letter-spacing:-0.3px;">{title}</div>'
        f"{sub_html}</div>"
    )


# ── Section 1: Trade War Dashboard ────────────────────────────────────────────
def _render_hero(macro_data: dict | None) -> None:
    try:
        logger.debug("trade_war | rendering hero dashboard")
        st.html(
            f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;">'
            f'<div style="color:{C_TEXT};font-size:22px;font-weight:800;'
            f'letter-spacing:-0.5px;">Trade War Intelligence</div>'
            f'<div style="background:{C_LOW}22;color:{C_LOW};'
            f'border:1px solid {C_LOW}55;border-radius:20px;'
            f'padding:4px 14px;font-size:11px;font-weight:800;'
            f'letter-spacing:1px;animation:pulse 2s infinite;">'
            f'&#9679; LIVE TARIFF SITUATION</div></div>',
        )

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.html(
                f'<div style="background:linear-gradient(135deg,{C_LOW}22,{C_CARD});'
                f'border:1px solid {C_LOW}55;border-radius:6px;padding:20px;text-align:center;">'
                f'<div style="color:{C_TEXT2};font-size:11px;font-weight:700;'
                f'letter-spacing:1px;text-transform:uppercase;">US Tariff on China</div>'
                f'<div style="color:{C_LOW};font-size:52px;font-weight:900;'
                f'line-height:1.1;margin:8px 0;">145%</div>'
                f'<div style="color:{C_TEXT2};font-size:12px;">All Chinese goods · Apr 2025</div>'
                f'</div>',
            )

        with col2:
            st.html(
                f'<div style="background:linear-gradient(135deg,{C_MOD}22,{C_CARD});'
                f'border:1px solid {C_MOD}55;border-radius:6px;padding:20px;text-align:center;">'
                f'<div style="color:{C_TEXT2};font-size:11px;font-weight:700;'
                f'letter-spacing:1px;text-transform:uppercase;">China Retaliation</div>'
                f'<div style="color:{C_MOD};font-size:52px;font-weight:900;'
                f'line-height:1.1;margin:8px 0;">125%</div>'
                f'<div style="color:{C_TEXT2};font-size:12px;">All US goods · Apr 2025</div>'
                f'</div>',
            )

        with col3:
            st.html(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:6px;padding:20px;text-align:center;">'
                f'<div style="color:{C_TEXT2};font-size:11px;font-weight:700;'
                f'letter-spacing:1px;text-transform:uppercase;">Est. Annual Trade Impact</div>'
                f'<div style="color:{C_TEXT};font-size:40px;font-weight:800;'
                f'line-height:1.1;margin:8px 0;">$582B</div>'
                f'<div style="color:{C_LOW};font-size:12px;">&#8595; -38% from 2024 baseline</div>'
                f'</div>',
            )

        with col4:
            st.html(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:6px;padding:20px;text-align:center;">'
                f'<div style="color:{C_TEXT2};font-size:11px;font-weight:700;'
                f'letter-spacing:1px;text-transform:uppercase;">Ships Rerouted / Cancelled</div>'
                f'<div style="color:{C_TEXT};font-size:40px;font-weight:800;'
                f'line-height:1.1;margin:8px 0;">214</div>'
                f'<div style="color:{C_MOD};font-size:12px;">Last 30 days · transpacific</div>'
                f'</div>',
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
            f'</div>',
        )
    except Exception:
        logger.exception("trade_war | hero render failed")
        st.error("Dashboard hero failed to render.")


# ── Section 2: Tariff Impact by Commodity ─────────────────────────────────────
def _render_commodity_table() -> None:
    try:
        logger.debug("trade_war | rendering commodity table")
        st.html(
            _section_header(
                "Tariff Impact by Commodity",
                "Full spectrum of affected goods — US 145% and China 125% retaliatory tariffs",
            ),
        )

        header_style = (
            f"background:{C_SURFACE};color:{C_TEXT2};font-size:10px;"
            f"font-weight:700;letter-spacing:1px;text-transform:uppercase;"
            f"padding:10px 12px;border-bottom:1px solid {C_BORDER};"
        )
        cols_pct = [2, 1.2, 1.2, 1.2, 1.2, 1.2, 2]

        header_html = (
            f'<div style="display:grid;grid-template-columns:'
            f'2fr 1.2fr 1.2fr 1.2fr 1.2fr 1.2fr 2fr;'
            f'background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-radius:6px 10px 0 0;overflow:hidden;">'
            f'<div style="{header_style}">Commodity</div>'
            f'<div style="{header_style}">US Tariff on CN</div>'
            f'<div style="{header_style}">CN Tariff on US</div>'
            f'<div style="{header_style}">US Imports ($B)</div>'
            f'<div style="{header_style}">Tariff Burden ($B)</div>'
            f'<div style="{header_style}">Shipping Impact</div>'
            f'<div style="{header_style}">Alternative Sources</div>'
            f'</div>'
        )
        st.html(header_html)

        rows_html = (
            f'<div style="border:1px solid {C_BORDER};border-top:none;'
            f'border-radius:0 0 10px 10px;overflow:hidden;">'
        )
        for i, c in enumerate(_COMMODITIES):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            impact_color = _impact_color(c["shipping_impact"])
            cell = (
                f"background:{bg};padding:11px 12px;font-size:13px;"
                f"color:{C_TEXT};border-bottom:1px solid {C_BORDER};"
                f"display:flex;align-items:center;"
            )
            rows_html += (
                f'<div style="display:grid;grid-template-columns:'
                f'2fr 1.2fr 1.2fr 1.2fr 1.2fr 1.2fr 2fr;">'
                f'<div style="{cell}font-weight:600;">{c["name"]}</div>'
                f'<div style="{cell}color:{C_LOW if c["us_tariff"] != "—" else C_TEXT3};">'
                f'{c["us_tariff"]}</div>'
                f'<div style="{cell}color:{C_MOD if c["cn_tariff"] != "—" else C_TEXT3};">'
                f'{c["cn_tariff"]}</div>'
                f'<div style="{cell}">${c["us_imports_bn"]}B</div>'
                f'<div style="{cell}color:{C_LOW};font-weight:600;">'
                f'${c["tariff_burden_bn"]}B</div>'
                f'<div style="{cell}">{_impact_badge(c["shipping_impact"])}</div>'
                f'<div style="{cell}color:{C_TEXT2};font-size:12px;">'
                f'{c["alt_sources"]}</div>'
                f'</div>'
            )
        rows_html += "</div>"
        st.html(rows_html)

        st.html(
            f'<div style="display:flex;gap:20px;margin-top:10px;flex-wrap:wrap;">'
            f'<div style="display:flex;align-items:center;gap:6px;">'
            f'{_impact_badge("HIGH")}'
            f'<span style="color:{C_TEXT3};font-size:12px;">Major route disruption / volume loss</span></div>'
            f'<div style="display:flex;align-items:center;gap:6px;">'
            f'{_impact_badge("MODERATE")}'
            f'<span style="color:{C_TEXT3};font-size:12px;">Partial diversion, some resilience</span></div>'
            f'<div style="display:flex;align-items:center;gap:6px;">'
            f'{_impact_badge("LOW")}'
            f'<span style="color:{C_TEXT3};font-size:12px;">Limited impact, inelastic demand</span></div>'
            f'</div>',
        )
    except Exception:
        logger.exception("trade_war | commodity table failed")
        st.error("Commodity table failed to render.")


# ── Section 3: Trade Flow Diversion Map ───────────────────────────────────────
def _render_diversion_map() -> None:
    try:
        logger.debug("trade_war | rendering diversion map")
        st.html(
            _section_header(
                "Trade Flow Diversion Map",
                "Tariffs causing diversion, not elimination — new shipping routes emerging",
            ),
        )

        fig = go.Figure()

        # Node points
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

        # Route lines: (origin, dest, color, width, dash, label)
        routes = [
            ("China", "USA", C_LOW, 4, "solid", "China→US (severely impacted)"),
            ("China", "Vietnam", "#3572b0", 3, "dot", "China→Vietnam (components)"),
            ("Vietnam", "USA", C_HIGH, 3, "solid", "Vietnam→US (rerouted)"),
            ("China", "Mexico", "#7c6eaf", 2, "dot", "China→Mexico (nearshoring)"),
            ("Mexico", "USA", C_HIGH, 2, "solid", "Mexico→US (friendshored)"),
            ("Brazil", "China", C_MOD, 3, "solid", "Brazil→China soy (replacing US)"),
            ("India", "USA", "#4a90a4", 2, "dot", "India→US (emerging)"),
        ]

        for origin, dest, color, width, dash, label in routes:
            lat0, lon0 = nodes[origin]
            lat1, lon1 = nodes[dest]
            fig.add_trace(
                go.Scattergeo(
                    lon=[lon0, lon1],
                    lat=[lat0, lat1],
                    mode="lines",
                    line={"width": width, "color": color, "dash": dash},
                    name=label,
                    showlegend=True,
                    hoverinfo="name",
                )
            )

        lats = [v[0] for v in nodes.values()]
        lons = [v[1] for v in nodes.values()]
        names = list(nodes.keys())
        colors_node = [
            C_LOW if n in ("China", "USA") else C_HIGH if n in ("Vietnam", "Mexico") else C_MOD
            for n in names
        ]

        fig.add_trace(
            go.Scattergeo(
                lon=lons,
                lat=lats,
                mode="markers+text",
                marker={"size": 12, "color": colors_node, "line": {"width": 1, "color": "#fff"}},
                text=names,
                textposition="top center",
                textfont={"color": C_TEXT, "size": 11},
                name="Ports / Countries",
                hoverinfo="text",
                showlegend=False,
            )
        )

        fig.update_layout(
            geo={
                "showframe": False,
                "showcoastlines": True,
                "coastlinecolor": "rgba(255,255,255,0.1)",
                "showland": True,
                "landcolor": "#181c28",
                "showocean": True,
                "oceancolor": "#0c0e14",
                "showcountries": True,
                "countrycolor": "rgba(232,230,225,0.05)",
                "projection_type": "natural earth",
                "bgcolor": C_BG,
            },
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            height=420,
            margin={"l": 0, "r": 0, "t": 0, "b": 0},
            legend={
                "bgcolor": C_CARD,
                "bordercolor": C_BORDER,
                "borderwidth": 1,
                "font": {"color": C_TEXT2, "size": 11},
                "x": 0.01,
                "y": 0.99,
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
            f'</div>',
        )
    except Exception:
        logger.exception("trade_war | diversion map failed")
        st.error("Trade flow diversion map failed to render.")


# ── Section 4: Nearshoring & Friendshoring ────────────────────────────────────
def _render_nearshoring() -> None:
    try:
        logger.debug("trade_war | rendering nearshoring section")
        st.html(
            _section_header(
                "Nearshoring & Friendshoring",
                "Manufacturing migrating from China — new shipping corridors forming",
            ),
        )

        shifts = [
            {
                "country": "Vietnam",
                "flag": "🇻🇳",
                "sectors": "Electronics, Textiles, Furniture",
                "volume_growth": "+55%",
                "color": C_HIGH,
                "commentary": "Primary China+1 beneficiary. Nike, Apple, Samsung shifting production.",
                "lane": "Vietnam → US Pacific",
            },
            {
                "country": "Mexico",
                "flag": "🇲🇽",
                "sectors": "Auto Parts, Machinery, Appliances",
                "volume_growth": "+42%",
                "color": C_HIGH,
                "commentary": "USMCA advantage. Tesla Monterrey, GM expansions driving nearshore boom.",
                "lane": "Mexico → US land / Gulf",
            },
            {
                "country": "India",
                "flag": "🇮🇳",
                "sectors": "Pharma, Textiles, Software goods",
                "volume_growth": "+31%",
                "color": C_MOD,
                "commentary": "Slower regulatory environment but massive labor cost advantage.",
                "lane": "India → US (Suez / Pacific)",
            },
            {
                "country": "Bangladesh",
                "flag": "🇧🇩",
                "sectors": "Apparel, Textiles",
                "volume_growth": "+28%",
                "color": C_MOD,
                "commentary": "Garment sector surging. Factory capacity straining port infrastructure.",
                "lane": "Bangladesh → US (Suez)",
            },
            {
                "country": "Indonesia",
                "flag": "🇮🇩",
                "sectors": "Electronics assembly, Palm oil",
                "volume_growth": "+19%",
                "color": C_MOD,
                "commentary": "Growing electronics hub. Nickel processing for EV supply chains.",
                "lane": "Indonesia → US Pacific",
            },
        ]

        cols = st.columns(len(shifts))
        for col, s in zip(cols, shifts):
            with col:
                st.html(
                    f'<div style="background:{C_CARD};border:1px solid {s["color"]}33;'
                    f'border-radius:6px;padding:16px;height:100%;">'
                    f'<div style="font-size:24px;margin-bottom:6px;">{s["flag"]}</div>'
                    f'<div style="color:{C_TEXT};font-size:14px;font-weight:700;">'
                    f'{s["country"]}</div>'
                    f'<div style="color:{s["color"]};font-size:22px;font-weight:800;'
                    f'margin:6px 0;">{s["volume_growth"]}</div>'
                    f'<div style="color:{C_TEXT2};font-size:11px;margin-bottom:8px;">'
                    f'volume growth YTD</div>'
                    f'<div style="color:{C_TEXT3};font-size:11px;font-weight:600;'
                    f'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">'
                    f'Sectors</div>'
                    f'<div style="color:{C_TEXT2};font-size:12px;margin-bottom:8px;">'
                    f'{s["sectors"]}</div>'
                    f'<div style="color:{C_TEXT3};font-size:11px;line-height:1.5;">'
                    f'{s["commentary"]}</div>'
                    f'<div style="background:{C_SURFACE};border-radius:6px;'
                    f'padding:6px 10px;margin-top:10px;">'
                    f'<div style="color:{C_ACCENT};font-size:11px;font-weight:600;">'
                    f'&#9658; {s["lane"]}</div></div>'
                    f'</div>',
                )
    except Exception:
        logger.exception("trade_war | nearshoring section failed")
        st.error("Nearshoring section failed to render.")


# ── Section 5: Shipping Volume Impact ─────────────────────────────────────────
def _render_volume_chart() -> None:
    try:
        logger.debug("trade_war | rendering volume chart")
        st.html(
            _section_header(
                "Shipping Volume Impact — Transpacific",
                "Monthly container volumes before and after tariff escalation (TEUs, thousands)",
            ),
        )

        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        baseline_2024 = [920, 870, 960, 1010, 1050, 980, 1020, 1040, 990, 1000, 1060, 1100]
        post_tariff_2025 = [910, 900, 930, 680, 560, 520, 540, 580, 610, 650, 680, 720]
        vietnam_us_2025 = [80, 85, 95, 130, 170, 195, 210, 220, 215, 225, 240, 260]
        mexico_us_2025 = [150, 155, 162, 178, 195, 208, 220, 228, 232, 238, 245, 250]

        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=months, y=baseline_2024,
            name="China→US 2024 (Baseline)",
            line={"color": C_ACCENT, "width": 2, "dash": "dash"},
            mode="lines",
            hovertemplate="%{y}K TEUs<extra>China→US 2024</extra>",
        ))

        fig.add_trace(go.Scatter(
            x=months, y=post_tariff_2025,
            name="China→US 2025 (Post-Tariff)",
            line={"color": C_LOW, "width": 3},
            mode="lines+markers",
            marker={"size": 6},
            fill="tonexty",
            fillcolor=f"{C_LOW}15",
            hovertemplate="%{y}K TEUs<extra>China→US 2025</extra>",
        ))

        fig.add_trace(go.Scatter(
            x=months, y=vietnam_us_2025,
            name="Vietnam→US 2025 (Rerouted)",
            line={"color": C_HIGH, "width": 2},
            mode="lines+markers",
            marker={"size": 5},
            hovertemplate="%{y}K TEUs<extra>Vietnam→US 2025</extra>",
        ))

        fig.add_trace(go.Scatter(
            x=months, y=mexico_us_2025,
            name="Mexico→US 2025 (Nearshored)",
            line={"color": "#7c6eaf", "width": 2},
            mode="lines+markers",
            marker={"size": 5},
            hovertemplate="%{y}K TEUs<extra>Mexico→US 2025</extra>",
        ))

        fig.add_vline(
            x="Apr", line_dash="dot", line_color=C_LOW, line_width=2,
            annotation_text="145% tariff", annotation_font_color=C_LOW,
            annotation_font_size=11,
        )

        fig.update_layout(
            paper_bgcolor=C_BG,
            plot_bgcolor=C_CARD,
            height=360,
            font={"color": C_TEXT2, "size": 12},
            xaxis={"gridcolor": C_BORDER, "title": "Month 2025"},
            yaxis={"gridcolor": C_BORDER, "title": "TEUs (thousands)"},
            legend={"bgcolor": "transparent", "font": {"size": 11}},
            margin={"l": 50, "r": 20, "t": 20, "b": 40},
            hovermode="x unified",
        )

        st.plotly_chart(fig, use_container_width=True, key="transpacific_volume_chart")

        carrier_col1, carrier_col2 = st.columns(2)
        with carrier_col1:
            st.html(
                f'<div style="{_card_open()}">'
                f'<div style="color:{C_TEXT};font-size:14px;font-weight:700;margin-bottom:12px;">'
                f'Carriers Cutting Transpacific Capacity</div>'
                f'<div style="display:flex;flex-direction:column;gap:8px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="color:{C_TEXT};font-size:13px;">COSCO</span>'
                f'<span style="color:{C_LOW};font-weight:700;">-22 sailings</span></div>'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="color:{C_TEXT};font-size:13px;">MSC</span>'
                f'<span style="color:{C_LOW};font-weight:700;">-18 sailings</span></div>'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="color:{C_TEXT};font-size:13px;">Evergreen</span>'
                f'<span style="color:{C_LOW};font-weight:700;">-14 sailings</span></div>'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="color:{C_TEXT};font-size:13px;">Yang Ming</span>'
                f'<span style="color:{C_MOD};font-weight:700;">-8 sailings</span></div>'
                f'</div></div>',
            )
        with carrier_col2:
            st.html(
                f'<div style="{_card_open()}">'
                f'<div style="color:{C_TEXT};font-size:14px;font-weight:700;margin-bottom:12px;">'
                f'Carriers Adding ASEAN Capacity</div>'
                f'<div style="display:flex;flex-direction:column;gap:8px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="color:{C_TEXT};font-size:13px;">Maersk (Vietnam)</span>'
                f'<span style="color:{C_HIGH};font-weight:700;">+16 sailings</span></div>'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="color:{C_TEXT};font-size:13px;">CMA CGM (India)</span>'
                f'<span style="color:{C_HIGH};font-weight:700;">+12 sailings</span></div>'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="color:{C_TEXT};font-size:13px;">Hapag-Lloyd (SE Asia)</span>'
                f'<span style="color:{C_HIGH};font-weight:700;">+10 sailings</span></div>'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="color:{C_TEXT};font-size:13px;">ONE (Indonesia)</span>'
                f'<span style="color:{C_MOD};font-weight:700;">+6 sailings</span></div>'
                f'</div></div>',
            )
    except Exception:
        logger.exception("trade_war | volume chart failed")
        st.error("Shipping volume chart failed to render.")


# ── Section 6: Trade Deal Tracker ─────────────────────────────────────────────
def _render_deal_tracker() -> None:
    try:
        logger.debug("trade_war | rendering deal tracker")
        st.html(
            _section_header(
                "Trade Deal Tracker",
                "Active negotiations and bilateral agreements — shipping impact if resolved",
            ),
        )

        header_style = (
            f"background:{C_SURFACE};color:{C_TEXT2};font-size:10px;"
            f"font-weight:700;letter-spacing:1px;text-transform:uppercase;"
            f"padding:10px 14px;border-bottom:1px solid {C_BORDER};"
        )
        st.html(
            f'<div style="display:grid;grid-template-columns:1.8fr 1fr 2fr 1fr 2fr;'
            f'background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-radius:6px 10px 0 0;">'
            f'<div style="{header_style}">Parties</div>'
            f'<div style="{header_style}">Status</div>'
            f'<div style="{header_style}">Key Issues</div>'
            f'<div style="{header_style}">Likelihood</div>'
            f'<div style="{header_style}">Shipping Impact if Resolved</div>'
            f'</div>',
        )

        rows = f'<div style="border:1px solid {C_BORDER};border-top:none;border-radius:0 0 10px 10px;">'
        for i, d in enumerate(_TRADE_DEALS):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            cell = (
                f"background:{bg};padding:12px 14px;font-size:13px;"
                f"color:{C_TEXT};border-bottom:1px solid {C_BORDER};"
                f"display:flex;align-items:center;"
            )
            status_badge = (
                f'<span style="background:{d["status_color"]}22;color:{d["status_color"]};'
                f'border:1px solid {d["status_color"]}44;border-radius:4px;'
                f'padding:2px 8px;font-size:11px;font-weight:700;">{d["status"]}</span>'
            )
            rows += (
                f'<div style="display:grid;grid-template-columns:1.8fr 1fr 2fr 1fr 2fr;">'
                f'<div style="{cell}font-weight:600;">{d["parties"]}</div>'
                f'<div style="{cell}">{status_badge}</div>'
                f'<div style="{cell}color:{C_TEXT2};font-size:12px;">{d["key_issues"]}</div>'
                f'<div style="{cell}color:{C_ACCENT};font-weight:700;">{d["likelihood"]}</div>'
                f'<div style="{cell}color:{C_HIGH};font-size:12px;">{d["shipping_impact"]}</div>'
                f'</div>'
            )
        rows += "</div>"
        st.html(rows)
    except Exception:
        logger.exception("trade_war | deal tracker failed")
        st.error("Trade deal tracker failed to render.")


# ── Section 7: Historical Tariff Wars ─────────────────────────────────────────
def _render_history() -> None:
    try:
        logger.debug("trade_war | rendering historical comparison")
        st.html(
            _section_header(
                "Historical Tariff Wars",
                "Shipping market behavior across tariff escalation episodes",
            ),
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
                    f'<div>'
                    f'<div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
                    f'letter-spacing:0.5px;">Peak Rate</div>'
                    f'<div style="color:{C_TEXT};font-size:13px;">{h["peak_rate"]}</div>'
                    f'</div>'
                    f'<div>'
                    f'<div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
                    f'letter-spacing:0.5px;">Trade Drop</div>'
                    f'<div style="color:{C_LOW};font-size:13px;font-weight:600;">'
                    f'{h["trade_drop"]}</div>'
                    f'</div>'
                    f'<div>'
                    f'<div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
                    f'letter-spacing:0.5px;">Shipping Impact</div>'
                    f'<div style="color:{C_MOD};font-size:13px;">{h["shipping_impact"]}</div>'
                    f'</div>'
                    f'<div style="background:{C_SURFACE};border-radius:6px;padding:8px 10px;margin-top:4px;">'
                    f'<div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
                    f'letter-spacing:0.5px;margin-bottom:4px;">Resolution</div>'
                    f'<div style="color:{C_TEXT2};font-size:12px;">{h["resolution"]}</div>'
                    f'</div>'
                    f'</div></div>',
                )
    except Exception:
        logger.exception("trade_war | history section failed")
        st.error("Historical tariff wars section failed to render.")


# ── Section 8: Scenario — Trade De-escalation ─────────────────────────────────
def _render_scenario() -> None:
    try:
        logger.debug("trade_war | rendering de-escalation scenario")
        st.html(
            _section_header(
                "Scenario: Trade De-Escalation",
                "If US-China tariffs reduced to 50% by end of 2026 — modeled market recovery",
            ),
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
            f'</div>',
        )

        mcol1, mcol2, mcol3, mcol4 = st.columns(4)
        metrics = [
            ("Volume Recovery", "+28%", "transpacific TEUs", C_HIGH),
            ("Freight Rate Impact", "+$400–600", "per FEU on transpacific", C_MOD),
            ("Timeline to Recovery", "6–9 months", "post-deal announcement", C_ACCENT),
            ("Stranded Capacity", "1.8M TEU", "returns to service", C_MOD),
        ]
        for col, (label, value, sub, color) in zip([mcol1, mcol2, mcol3, mcol4], metrics):
            with col:
                st.html(
                    f'<div style="background:{C_CARD};border:1px solid {color}33;'
                    f'border-radius:6px;padding:16px;text-align:center;">'
                    f'<div style="color:{C_TEXT2};font-size:11px;font-weight:700;'
                    f'text-transform:uppercase;letter-spacing:0.5px;">{label}</div>'
                    f'<div style="color:{color};font-size:28px;font-weight:800;margin:8px 0;">'
                    f'{value}</div>'
                    f'<div style="color:{C_TEXT3};font-size:12px;">{sub}</div>'
                    f'</div>',
                )

        st.html(
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px;">'
            f'<div style="{_card_open()}">'
            f'<div style="color:{C_HIGH};font-size:14px;font-weight:700;margin-bottom:12px;">'
            f'&#9650; Winner Carriers &amp; Routes</div>'
            f'<div style="display:flex;flex-direction:column;gap:8px;">'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:{C_TEXT};font-size:13px;">China–US Transpacific</span>'
            f'<span style="color:{C_HIGH};font-weight:700;">+30–35%</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:{C_TEXT};font-size:13px;">COSCO / Evergreen</span>'
            f'<span style="color:{C_HIGH};font-weight:700;">+25–30%</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:{C_TEXT};font-size:13px;">Shanghai / Ningbo ports</span>'
            f'<span style="color:{C_HIGH};font-weight:700;">+20% TEU throughput</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:{C_TEXT};font-size:13px;">US agricultural exporters</span>'
            f'<span style="color:{C_HIGH};font-weight:700;">+$8B soy/LNG</span></div>'
            f'</div></div>'
            f'<div style="{_card_open()}">'
            f'<div style="color:{C_MOD};font-size:14px;font-weight:700;margin-bottom:12px;">'
            f'&#9660; Volume Lost to Permanent Diversion</div>'
            f'<div style="display:flex;flex-direction:column;gap:8px;">'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:{C_TEXT};font-size:13px;">Vietnam→US (stays even post-deal)</span>'
            f'<span style="color:{C_TEXT2};font-weight:600;">~60% retained</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:{C_TEXT};font-size:13px;">Mexico nearshoring</span>'
            f'<span style="color:{C_TEXT2};font-weight:600;">~70% retained</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:{C_TEXT};font-size:13px;">Brazil→China soy route</span>'
            f'<span style="color:{C_TEXT2};font-weight:600;">~50% retained</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:{C_TEXT};font-size:13px;">India pharma/textile</span>'
            f'<span style="color:{C_TEXT2};font-weight:600;">~80% retained</span></div>'
            f'</div></div>'
            f'</div>',
        )

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
            f'</div>',
        )
    except Exception:
        logger.exception("trade_war | scenario section failed")
        st.error("De-escalation scenario section failed to render.")


# ── Main render ────────────────────────────────────────────────────────────────
def render(macro_data=None, freight_data=None, insights=None) -> None:
    """Render the Trade Policy & Tariff Impact Intelligence tab."""
    try:
        logger.info("trade_war | render start")

        _render_hero(macro_data)
        st.html("<hr style='border:none;border-top:1px solid rgba(232,230,225,0.05);margin:24px 0;'>")

        _render_commodity_table()
        st.html("<hr style='border:none;border-top:1px solid rgba(232,230,225,0.05);margin:24px 0;'>")

        _render_diversion_map()
        st.html("<hr style='border:none;border-top:1px solid rgba(232,230,225,0.05);margin:24px 0;'>")

        _render_nearshoring()
        st.html("<hr style='border:none;border-top:1px solid rgba(232,230,225,0.05);margin:24px 0;'>")

        _render_volume_chart()
        st.html("<hr style='border:none;border-top:1px solid rgba(232,230,225,0.05);margin:24px 0;'>")

        _render_deal_tracker()
        st.html("<hr style='border:none;border-top:1px solid rgba(232,230,225,0.05);margin:24px 0;'>")

        _render_history()
        st.html("<hr style='border:none;border-top:1px solid rgba(232,230,225,0.05);margin:24px 0;'>")

        _render_scenario()

        logger.info("trade_war | render complete")

    except Exception:
        logger.exception("trade_war | render failed")
        st.error("Trade War tab encountered an error. Check logs for details.")
