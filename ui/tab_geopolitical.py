"""
Geopolitical Risk Intelligence Tab — Institutional Edition

Sections:
  1. Global Risk Heat      — Hero gauge, risk index vs prior month, top 3 risk regions
  2. Risk Map              — Choropleth / scatter_geo with shipping lane overlays
  3. Hotspot Monitor       — Live risk cards for active hotspots
  4. Sanctions Tracker     — Country/entity sanctions table
  5. Trade War Monitor     — Tariff comparison table by trade pair
  6. Rerouting Impact      — Affected lanes, extra distance/days/cost
  7. Insurance & War Risk  — War risk premiums by region, JWC listed areas
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

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
    gradient_card,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# HIGH-risk tier — orange, sits between MODERATE amber and CRITICAL red
_C_ORANGE = "#f97316"

# Domain-specific risk level → palette color (orange tier kept local — the
# shared palette has no orange).
_LEVEL_COLOR: dict[str, str] = {
    "CRITICAL": C_LOW,
    "HIGH":     _C_ORANGE,
    "MODERATE": C_MOD,
    "LOW":      C_HIGH,
}

# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

_HOTSPOTS = [
    {
        "name": "Red Sea / Bab-el-Mandeb",
        "level": "CRITICAL",
        "icon": "🔴",
        "situation": (
            "Houthi forces continue drone and missile attacks on commercial vessels. "
            "Major carriers (Maersk, MSC, CMA CGM) have suspended Suez transits. "
            "US-led Operation Prosperity Guardian provides limited deterrence."
        ),
        "routes": "Asia–Europe, Asia–Mediterranean, India–Europe",
        "rate_premium": "+$2,500–$3,500/day",
        "extra_days": "+14 days via Cape of Good Hope",
        "extra_nm": "+3,500 nm",
        "vessels_affected": "~450 vessels/month rerouted",
        "suez_transit_pct": "−65% vs prior year",
    },
    {
        "name": "Taiwan Strait",
        "level": "HIGH",
        "icon": "🟠",
        "situation": (
            "PLA military exercises near Taiwan have intensified. "
            "Semiconductor supply chains on high alert. "
            "US carrier strike groups deployed to Western Pacific as deterrent. "
            "Insurance underwriters pricing elevated war risk."
        ),
        "routes": "North Asia–SE Asia, Trans-Pacific, Intra-Asia",
        "rate_premium": "+$800–$1,200/day",
        "extra_days": "N/A (no active rerouting)",
        "extra_nm": "N/A",
        "vessels_affected": "~1,200 transits/month at risk",
        "suez_transit_pct": "N/A",
    },
    {
        "name": "Black Sea",
        "level": "HIGH",
        "icon": "🟠",
        "situation": (
            "Ukraine war ongoing; drone attacks on Russian port infrastructure. "
            "Grain corridor fragile after Russia withdrawal from UN deal. "
            "Russian oil exports under Western sanctions with shadow fleet active. "
            "Ukrainian ports Odessa/Chornomorsk operating under naval escort."
        ),
        "routes": "Black Sea–Med, Grain exports (Ukraine/Romania), Russian crude",
        "rate_premium": "+$1,500–$2,500/day",
        "extra_days": "N/A",
        "extra_nm": "N/A",
        "vessels_affected": "~80 vessels/month at risk",
        "suez_transit_pct": "N/A",
    },
    {
        "name": "Strait of Hormuz",
        "level": "HIGH",
        "icon": "🟠",
        "situation": (
            "Iran–US tensions elevated following nuclear talks breakdown. "
            "IRGC has shadowed and briefly detained tankers. "
            "28% of global seaborne oil transits through Hormuz. "
            "Saudi Aramco and UAE ADNOC exports critically dependent."
        ),
        "routes": "Persian Gulf–Asia, Middle East crude exports, LNG from Qatar",
        "rate_premium": "+$1,000–$1,800/day (VLCC)",
        "extra_days": "N/A",
        "extra_nm": "N/A",
        "vessels_affected": "~500 tankers/month at risk",
        "suez_transit_pct": "N/A",
    },
    {
        "name": "Panama Canal",
        "level": "MODERATE",
        "icon": "🟡",
        "situation": (
            "El Niño drought reduced Gatun Lake water levels to historic lows. "
            "ACP restricting vessels to 44-ft draft max (vs 50 ft normal). "
            "Daily transits down ~30% from 36 to ~24 slots. "
            "Wait times 7–20 days; vessels rerouting via Suez or Cape Horn."
        ),
        "routes": "Trans-Pacific, US East Coast–Asia, LNG exports from US Gulf",
        "rate_premium": "+$600–$1,200/day",
        "extra_days": "+20–30 days via Cape Horn (if full reroute)",
        "extra_nm": "+7,900 nm via Cape Horn",
        "vessels_affected": "~180 vessels/month rerouted or delayed",
        "suez_transit_pct": "N/A",
    },
]

_SANCTIONS = [
    {
        "entity": "Russia (Crude & Products)",
        "body": "EU / G7 / US OFAC",
        "asset_type": "Tankers, crude oil cargoes",
        "ships_affected": "~600 vessels (shadow fleet)",
        "effective": "Dec 2022 (oil price cap)",
        "notes": "Price cap $60/bbl crude; EU ban on seaborne imports; SDN listings for shadow-fleet vessels",
    },
    {
        "entity": "Iran (IRGC / NIOC)",
        "body": "US OFAC / EU / UN",
        "asset_type": "Tankers, petrochemical vessels",
        "ships_affected": "~200 vessels (IRGC-linked)",
        "effective": "2012 (nuclear); 2018 re-imposed",
        "notes": "OFAC SDN list; secondary sanctions risk for non-US entities; crude flows to China/India via obscure intermediaries",
    },
    {
        "entity": "North Korea (DPRK)",
        "body": "UN Security Council / US / EU",
        "asset_type": "Bulk carriers, coal/coal STS",
        "ships_affected": "~50+ vessels documented",
        "effective": "UNSCR 2375 (2017)",
        "notes": "Coal and iron ore export ban; vessel identity fraud common; STS transfers in international waters",
    },
    {
        "entity": "Venezuela (PDVSA)",
        "body": "US OFAC",
        "asset_type": "Crude tankers, VLCCs",
        "ships_affected": "~80 vessels (PDVSA-linked)",
        "effective": "Jan 2019",
        "notes": "OFAC Executive Order 13850; crude exports to China and Cuba via circuitous routes; temporary OFAC licenses issued Mar 2024",
    },
    {
        "entity": "Myanmar (Military Junta)",
        "body": "US / EU / UK / Canada",
        "asset_type": "General cargo, fuel tankers",
        "ships_affected": "~30 vessels flagged",
        "effective": "Feb 2021 (post-coup)",
        "notes": "Fuel import ban; sanctions on Myanma Oil & Gas Enterprise (MOGE); jet fuel shipments blocked",
    },
    {
        "entity": "Belarus (Lukashenko Regime)",
        "body": "EU / US / UK",
        "asset_type": "Potash/bulk cargo vessels",
        "ships_affected": "~15 vessels (indirect)",
        "effective": "Jun 2021",
        "notes": "Potash fertiliser export ban via EU ports; rerouting through Russian Baltic ports",
    },
]

_TARIFFS = [
    {
        "pair": "US ↔ China",
        "pre_rate": "7.5–25%",
        "current_rate": "145% (US) / 125% (CN retaliation)",
        "volume_impact": "−35% bilateral container trade",
        "shipping_impact": "Trans-Pacific rates volatile; nearshoring to Mexico/Vietnam accelerating",
        "severity": "CRITICAL",
    },
    {
        "pair": "US ↔ EU",
        "pre_rate": "0–3.5% (TTIP baseline)",
        "current_rate": "10% universal + threatened 25% steel/auto",
        "volume_impact": "−8% trans-Atlantic volumes",
        "shipping_impact": "Minor box rate pressure; EU retaliatory list of $21B US goods",
        "severity": "MODERATE",
    },
    {
        "pair": "US ↔ Rest of World",
        "pre_rate": "0–5%",
        "current_rate": "10% universal baseline tariff",
        "volume_impact": "−5 to −12% (varies by country)",
        "shipping_impact": "Broad demand dampening; minor rerouting through low-tariff hubs",
        "severity": "MODERATE",
    },
    {
        "pair": "China → SE Asia (transshipment)",
        "pre_rate": "N/A",
        "current_rate": "US targeting Vietnam/Thailand origin goods",
        "volume_impact": "+25% SE Asia export volumes (transshipment surge)",
        "shipping_impact": "Intra-Asia and SE Asia–US volumes surging; port congestion Vietnam/Thailand",
        "severity": "HIGH",
    },
    {
        "pair": "EU → Russia",
        "pre_rate": "MFN rates (pre-2022)",
        "current_rate": "Full embargo on most goods",
        "volume_impact": "−99% (near-total ban)",
        "shipping_impact": "Baltic and Black Sea cargo rerouted to third countries; smuggling via Turkey/UAE",
        "severity": "HIGH",
    },
]

_REROUTING = [
    {
        "lane": "Asia – North Europe",
        "original": "Suez Canal",
        "current": "Cape of Good Hope",
        "extra_nm": 3_500,
        "extra_days": 14,
        "extra_bunker": "$280,000–$420,000/voyage",
        "rate_premium": "$2,500–$3,500/day",
        "status": "Active reroute",
    },
    {
        "lane": "Asia – Mediterranean",
        "original": "Suez Canal",
        "current": "Cape of Good Hope",
        "extra_nm": 4_200,
        "extra_days": 16,
        "extra_bunker": "$330,000–$500,000/voyage",
        "rate_premium": "$2,800–$4,000/day",
        "status": "Active reroute",
    },
    {
        "lane": "US Gulf – Asia (LNG)",
        "original": "Panama Canal",
        "current": "Cape of Good Hope or Suez",
        "extra_nm": 8_000,
        "extra_days": 28,
        "extra_bunker": "$600,000–$900,000/voyage",
        "rate_premium": "$800–$1,400/day",
        "status": "Partial reroute",
    },
    {
        "lane": "US East Coast – Asia",
        "original": "Panama Canal",
        "current": "Suez Canal (or Cape Horn)",
        "extra_nm": 5_200,
        "extra_days": 18,
        "extra_bunker": "$400,000–$650,000/voyage",
        "rate_premium": "$700–$1,100/day",
        "status": "Partial reroute",
    },
    {
        "lane": "India – Europe",
        "original": "Suez Canal",
        "current": "Cape of Good Hope",
        "extra_nm": 2_800,
        "extra_days": 11,
        "extra_bunker": "$220,000–$340,000/voyage",
        "rate_premium": "$1,800–$2,800/day",
        "status": "Active reroute",
    },
]

_WAR_RISK = [
    {
        "region": "Red Sea / Gulf of Aden",
        "premium_pct": "0.50–0.75% of vessel value/voyage",
        "jwc_listed": "Yes",
        "base_annual": "$250k–$600k (VLCC equiv.)",
        "kidnap_ransom": "Included in some P&I",
        "trend": "UP",
        "notes": "Peak levels not seen since 2011 Somali piracy era",
    },
    {
        "region": "Black Sea (Ukraine/Russia zones)",
        "premium_pct": "0.35–0.60% of vessel value/voyage",
        "jwc_listed": "Yes",
        "base_annual": "$180k–$450k",
        "kidnap_ransom": "Limited",
        "trend": "STABLE",
        "notes": "Significant variation by port of call; Odessa higher than Romanian ports",
    },
    {
        "region": "Strait of Hormuz / Persian Gulf",
        "premium_pct": "0.10–0.25% of vessel value/voyage",
        "jwc_listed": "Yes (portions)",
        "base_annual": "$50k–$200k",
        "kidnap_ransom": "Included",
        "trend": "UP",
        "notes": "IRGC detention incidents driving premium increases Q1 2026",
    },
    {
        "region": "Taiwan Strait",
        "premium_pct": "0.05–0.15% of vessel value/voyage",
        "jwc_listed": "No (monitoring)",
        "base_annual": "$25k–$120k",
        "kidnap_ransom": "N/A",
        "trend": "UP",
        "notes": "Underwriters issuing monitoring notices; JWC listing possible if exercises escalate",
    },
    {
        "region": "West Africa (Gulf of Guinea)",
        "premium_pct": "0.10–0.20% of vessel value/voyage",
        "jwc_listed": "Yes",
        "base_annual": "$50k–$160k",
        "kidnap_ransom": "Critical — high kidnap risk",
        "trend": "STABLE",
        "notes": "Nigeria, Benin, Togo offshore zones; piracy incidents down 40% from 2020 peak",
    },
    {
        "region": "Mediterranean (Libya/Syria)",
        "premium_pct": "0.03–0.08% of vessel value/voyage",
        "jwc_listed": "Partial",
        "base_annual": "$15k–$65k",
        "kidnap_ransom": "Limited",
        "trend": "STABLE",
        "notes": "Libyan territorial waters remain elevated; Tripoli port occasional incidents",
    },
]

_COUNTRY_RISK: dict[str, int] = {
    "YEM": 95, "IRN": 88, "RUS": 85, "PRK": 82, "SOM": 80,
    "MMR": 72, "SDN": 70, "SYR": 68, "LBY": 65, "IRQ": 60,
    "VEN": 58, "ETH": 55, "MLI": 53, "NGA": 52, "AFG": 90,
    "TKM": 48, "PAK": 47, "EGY": 42, "TWN": 65, "CHN": 45,
    "IND": 30, "IDN": 28, "BRA": 25, "ZAF": 30, "TUR": 38,
    "USA": 15, "GBR": 12, "DEU": 10, "JPN": 10, "AUS": 8,
    "SGP": 5,  "NLD": 8,  "FRA": 12, "KOR": 15, "CAN": 8,
    "NOR": 7,  "GRC": 14, "ESP": 12, "ITA": 13, "PRT": 8,
    "ARE": 28, "SAU": 40, "KWT": 35, "QAT": 32, "OMN": 30,
    "DJI": 45, "ERI": 50, "KEN": 35, "TZA": 28, "MOZ": 32,
    "UKR": 80, "POL": 15, "ROU": 18, "BGR": 16, "GEO": 35,
    "AZE": 32, "KAZ": 28, "UZB": 30, "PHL": 32, "VNM": 22,
    "THA": 25, "MYS": 18, "BGD": 35, "LKA": 30, "MDV": 12,
}

# ---------------------------------------------------------------------------
# Tab-local helpers
# ---------------------------------------------------------------------------

def _trend_arrow(trend: str) -> str:
    if trend == "UP":
        return f'<span style="color:{C_LOW};font-weight:700">▲ Rising</span>'
    if trend == "DOWN":
        return f'<span style="color:{C_HIGH};font-weight:700">▼ Falling</span>'
    return f'<span style="color:{C_MOD};font-weight:700">→ Stable</span>'


def _mono(value: str, color: str = C_TEXT, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};font-size:0.82rem;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};font-size:0.82rem;">{value}</span>'
    )


# ---------------------------------------------------------------------------
# Section 1 — Global Risk Heat
# ---------------------------------------------------------------------------

def _render_global_risk_heat(macro_data: dict | None, insights: list | None) -> None:
    try:
        section_header(
            "Global Risk Heat",
            "Composite geopolitical risk index — weighted by shipping volume exposure",
        )

        risk_index = 74
        prior_month = 68
        delta = risk_index - prior_month
        delta_color = C_LOW if delta > 0 else C_HIGH
        delta_str = f"+{delta}" if delta > 0 else str(delta)

        top_regions = [
            ("Red Sea / Bab-el-Mandeb", 95, "CRITICAL"),
            ("Ukraine / Black Sea", 80, "HIGH"),
            ("Strait of Hormuz", 78, "HIGH"),
        ]

        metric_card_row(
            [
                dict(
                    label="Global Geopolitical Risk Index",
                    value=str(risk_index),
                    sublabel=f"Prior month: {prior_month}/100",
                    delta=f"{delta_str} vs prior month",
                    delta_color=delta_color,
                    accent=C_LOW,
                ),
                dict(
                    label=f"Top Risk Region #1 — {top_regions[0][0]}",
                    value=str(top_regions[0][1]),
                    sublabel=top_regions[0][2],
                    accent=_LEVEL_COLOR[top_regions[0][2]],
                ),
                dict(
                    label=f"Top Risk Region #2 — {top_regions[1][0]}",
                    value=str(top_regions[1][1]),
                    sublabel=top_regions[1][2],
                    accent=_LEVEL_COLOR[top_regions[1][2]],
                ),
                dict(
                    label=f"Top Risk Region #3 — {top_regions[2][0]}",
                    value=str(top_regions[2][1]),
                    sublabel=top_regions[2][2],
                    accent=_LEVEL_COLOR[top_regions[2][2]],
                ),
            ],
            columns=4,
        )

        if insights:
            for ins in insights[:2]:
                try:
                    st.markdown(
                        gradient_card(
                            f'<span style="font-family:var(--sans);font-size:0.84rem;'
                            f'color:{C_TEXT2};line-height:1.55;">{ins}</span>',
                            border_color=C_LOW,
                        ),
                        unsafe_allow_html=True,
                    )
                except Exception:
                    pass

    except Exception as exc:
        logger.warning(f"[tab_geopolitical] global_risk_heat: {exc}")
        st.info("Global risk data unavailable.")


# ---------------------------------------------------------------------------
# Section 2 — Risk Map
# ---------------------------------------------------------------------------

def _render_risk_map() -> None:
    try:
        section_header(
            "Geopolitical Risk Map",
            "Country risk to shipping operations — hover for details. Shipping lane overlays shown.",
        )

        iso_codes = list(_COUNTRY_RISK.keys())
        scores = list(_COUNTRY_RISK.values())

        # Empty-state guard: a choropleth with no locations renders as a blank
        # globe — surface a notice instead.
        if not iso_codes:
            st.info("No country-risk data available to map.")
            return

        hover_text = []
        for iso, score in _COUNTRY_RISK.items():
            level = "CRITICAL" if score >= 80 else "HIGH" if score >= 60 else "MODERATE" if score >= 40 else "LOW"
            if iso == "YEM":
                reason, lanes = "Houthi attacks on commercial shipping", "Suez / Red Sea transit"
            elif iso == "IRN":
                reason, lanes = "IRGC tanker seizures, nuclear sanctions", "Hormuz, Persian Gulf"
            elif iso == "RUS":
                reason, lanes = "Ukraine war, Western sanctions, shadow fleet", "Baltic, Black Sea"
            elif iso == "TWN":
                reason, lanes = "PLA military exercises, strait tensions", "Taiwan Strait, Trans-Pacific"
            elif iso == "PRK":
                reason, lanes = "UNSC sanctions, coal/arms smuggling", "Yellow Sea, East Sea"
            elif iso == "SOM":
                reason, lanes = "Piracy, political instability", "Gulf of Aden, Indian Ocean"
            elif iso == "UKR":
                reason, lanes = "Active conflict, drone attacks", "Black Sea, Azov Sea"
            else:
                reason, lanes = f"Risk score {score}/100", "Regional shipping lanes"
            hover_text.append(
                f"<b>{iso}</b><br>Risk Score: {score}/100<br>Level: {level}<br>"
                f"Reason: {reason}<br>Affected Lanes: {lanes}"
            )

        choropleth = go.Choropleth(
            locations=iso_codes,
            z=scores,
            text=hover_text,
            hovertemplate="%{text}<extra></extra>",
            colorscale=[
                [0.0,  C_HIGH],
                [0.35, C_HIGH],
                [0.50, C_MOD],
                [0.70, _C_ORANGE],
                [1.0,  C_LOW],
            ],
            zmin=0,
            zmax=100,
            colorbar=dict(
                title=dict(text="Risk Score", font=dict(color=C_TEXT2, size=11)),
                tickfont=dict(color=C_TEXT2, size=10),
                bgcolor=C_CARD,
                bordercolor=C_BORDER,
                len=0.65,
                thickness=12,
            ),
            marker_line_color=C_SURFACE,
            marker_line_width=0.5,
        )

        lane_lats = [
            [12.5, 15.0, 20.0, 25.0, 29.9, None],
            [1.3, -10.0, -25.0, -34.4, -20.0, -10.0, 1.3, None],
            [22.0, 24.0, 26.0, None],
            [24.0, 25.5, 26.5, None],
            [8.0, 9.0, 9.4, None],
        ]
        lane_lons = [
            [43.5, 42.0, 38.5, 35.5, 32.6, None],
            [103.8, 100.0, 80.0, 18.5, 10.0, 5.0, -5.0, None],
            [120.0, 120.5, 121.0, None],
            [56.5, 57.0, 57.5, None],
            [-79.5, -79.8, -79.9, None],
        ]
        lane_names = [
            "Red Sea / Suez", "Cape of Good Hope (reroute)",
            "Taiwan Strait", "Strait of Hormuz", "Panama Canal",
        ]
        lane_colors = [C_LOW, C_MOD, C_LOW, C_LOW, C_MOD]

        lane_traces = []
        for lats, lons, name, color in zip(lane_lats, lane_lons, lane_names, lane_colors):
            flat = [x for x in lats if x is not None]
            flon = [x for x in lons if x is not None]
            lane_traces.append(
                go.Scattergeo(
                    lat=flat,
                    lon=flon,
                    mode="lines",
                    line=dict(color=color, width=2),
                    name=name,
                    hoverinfo="name",
                    opacity=0.75,
                )
            )

        fig = go.Figure(data=[choropleth] + lane_traces)
        apply_dark_layout(
            fig,
            height=480,
            margin=dict(l=0, r=0, t=0, b=0),
            geo=dict(
                showframe=False,
                showcoastlines=True,
                coastlinecolor=C_TEXT3,
                showocean=True,
                oceancolor=C_SURFACE,
                showland=True,
                landcolor="#1e2d45",
                bgcolor=C_CARD,
                projection_type="natural earth",
                showlakes=False,
            ),
            legend=dict(
                bgcolor=C_CARD,
                bordercolor=C_BORDER,
                font=dict(color=C_TEXT2, size=10),
                x=0.01, y=0.02,
                orientation="v",
            ),
        )

        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        st.markdown(
            f'<div class="sub-section-header">'
            f'<span style="font-size:0.75rem;color:{C_LOW}">■ Critical (80–100)</span>'
            f'&emsp;<span style="font-size:0.75rem;color:{C_MOD}">■ Moderate (40–69)</span>'
            f'&emsp;<span style="font-size:0.75rem;color:{C_HIGH}">■ Lower (0–39)</span>'
            f'&emsp;<span style="font-size:0.75rem;color:{C_TEXT3}">— Key shipping lanes</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    except Exception as exc:
        logger.warning(f"[tab_geopolitical] risk_map: {exc}")
        st.info("Risk map unavailable.")


# ---------------------------------------------------------------------------
# Section 3 — Hotspot Monitor
# ---------------------------------------------------------------------------

def _render_hotspot_monitor() -> None:
    try:
        section_header(
            "Hotspot Monitor",
            "Live risk cards for active maritime security hotspots",
        )

        if not _HOTSPOTS:
            st.info("No active hotspots to monitor.")
            return

        for hs in _HOTSPOTS:
            level = hs["level"]
            lvl_color = _LEVEL_COLOR.get(level, C_TEXT2)

            # Labeled fields → a compact "·"-separated run of content-styled spans
            field_bits = []
            for label, key in [
                ("Affected Routes", "routes"),
                ("Rate Premium", "rate_premium"),
                ("Extra Voyage Time", "extra_days"),
                ("Extra Distance", "extra_nm"),
                ("Vessels Affected", "vessels_affected"),
            ]:
                val = hs.get(key, "N/A")
                if val and val != "N/A":
                    field_bits.append(
                        f'<span style="color:{C_TEXT3};">{label}:</span> '
                        f'<span style="color:{C_TEXT};font-weight:600;">{val}</span>'
                    )

            content = (
                f'<div class="wsj-headline-sm">{hs["icon"]}&nbsp; {hs["name"]}'
                f'&nbsp;&nbsp;{badge(level, color=lvl_color)}</div>'
                f'<div class="wsj-body">{hs["situation"]}</div>'
            )
            if field_bits:
                content += (
                    '<div class="wsj-body">'
                    + '&nbsp;&nbsp;·&nbsp;&nbsp;'.join(field_bits)
                    + '</div>'
                )

            st.markdown(
                gradient_card(content, border_color=lvl_color),
                unsafe_allow_html=True,
            )

    except Exception as exc:
        logger.warning(f"[tab_geopolitical] hotspot_monitor: {exc}")
        st.info("Hotspot data unavailable.")


# ---------------------------------------------------------------------------
# Section 4 — Sanctions & Embargo Tracker
# ---------------------------------------------------------------------------

def _try_live_geo_sanctions():
    """Attempt a live OFAC SDN screen (R024). OFFLINE-SAFE — never raises.

    Returns ``(rows, source)`` in this tab's row shape when the live OFAC
    consolidated list parsed (REAL provenance), else ``(None, None)`` so the
    caller keeps its modeled ``_SANCTIONS`` rows.
    """
    try:
        from data.sanctions_feed import fetch_ofac_sdn, geopolitical_sanctions_rows
        sl = fetch_ofac_sdn()
        if sl and sl.is_real:
            rows = geopolitical_sanctions_rows(sl)
            if rows:
                return rows, sl.source
    except Exception:
        logger.debug("[tab_geopolitical] live OFAC screen unavailable — modeled fallback")
    return None, None


def _render_sanctions_tracker() -> None:
    try:
        section_header(
            "Sanctions & Embargo Tracker",
            "Active shipping-relevant sanctions by country/entity — compliance critical",
        )

        # ── R024: prefer a LIVE OFAC SDN screen when the keyless feed returns;
        # fall back to the modeled rows (clearly labelled) when dark. ──────────
        live_rows, live_source = _try_live_geo_sanctions()
        is_live = live_rows is not None
        sanctions_data = live_rows if is_live else _SANCTIONS

        if not sanctions_data:
            st.info("No active sanctions to display.")
            return

        if is_live:
            from ui.styles import live_data_badge
            st.markdown(
                "Live OFAC SDN vessel designations grouped by sanctions program. "
                + live_data_badge(live_source),
                unsafe_allow_html=True,
            )
        else:
            st.caption(
                "Demo data — live OFAC SDN feed offline; modeled reference rows shown."
            )

        rows = [
            [
                _sans(row["entity"], color=C_TEXT, weight=600),
                _sans(row["body"], color=C_MOD),
                _sans(row["asset_type"], color=C_TEXT2),
                _sans(row["ships_affected"], color=C_LOW, weight=600),
                _sans(row["effective"], color=C_TEXT3),
                _sans(row["notes"], color=C_TEXT2),
            ]
            for row in sanctions_data
        ]
        wsj_market_table(
            ["Entity", "Sanctioning Body", "Asset Type", "Ships Affected", "Effective", "Compliance Notes"],
            rows,
        )
        if is_live:
            st.markdown(source_footer([live_source]), unsafe_allow_html=True)

    except Exception as exc:
        logger.warning(f"[tab_geopolitical] sanctions_tracker: {exc}")
        st.info("Sanctions data unavailable.")


# ---------------------------------------------------------------------------
# Section 5 — Trade War Monitor
# ---------------------------------------------------------------------------

def _render_trade_war_monitor() -> None:
    try:
        section_header(
            "Trade War Monitor",
            "Tariff escalation by major trade pair and shipping volume impact",
        )

        if not _TARIFFS:
            st.info("No trade-war / tariff data available.")
            return

        rows = []
        for row in _TARIFFS:
            sev = row["severity"]
            sev_color = _LEVEL_COLOR.get(sev, C_TEXT2)
            rows.append([
                _sans(row["pair"], color=C_TEXT, weight=700),
                _sans(row["pre_rate"], color=C_TEXT2),
                _sans(row["current_rate"], color=C_LOW, weight=700),
                _sans(row["volume_impact"], color=C_MOD),
                _sans(row["shipping_impact"], color=C_TEXT2),
                badge(sev, color=sev_color),
            ])
        wsj_market_table(
            ["Trade Pair", "Pre-Tariff Rate", "Current Rate", "Volume Impact", "Shipping Impact", "Severity"],
            rows,
        )

        st.markdown(
            insight_card_html(
                title="US-China escalation is the primary structural shock",
                score=0.82,
                action="Caution",
                rationale=(
                    "Tariff escalation to 145%/125% has cut trans-Pacific container "
                    "demand ~35% YoY on direct lanes, but transshipment via Vietnam "
                    "and Mexico is surging — creating secondary port congestion. "
                    "Carriers are deploying blank sailings to manage capacity utilisation."
                ),
                category="TRADE WAR",
            ),
            unsafe_allow_html=True,
        )

    except Exception as exc:
        logger.warning(f"[tab_geopolitical] trade_war_monitor: {exc}")
        st.info("Trade war data unavailable.")


# ---------------------------------------------------------------------------
# Section 6 — Rerouting Impact
# ---------------------------------------------------------------------------

def _render_rerouting_impact() -> None:
    try:
        section_header(
            "Rerouting Impact",
            "Trade lanes affected by Red Sea / Panama disruptions — cost and time penalties",
        )

        # Empty-state guard: no rerouting rows means no table and no chart.
        if not _REROUTING:
            st.info("No rerouting-impact data available.")
            return

        rows = []
        for row in _REROUTING:
            status_color = C_LOW if row["status"] == "Active reroute" else C_MOD
            rows.append([
                _sans(row["lane"], color=C_TEXT, weight=700),
                _sans(row["original"], color=C_TEXT2),
                _sans(row["current"], color=C_MOD),
                _mono(f"+{row['extra_nm']:,}", color=C_LOW, weight=700),
                _mono(f"+{row['extra_days']}", color=C_LOW, weight=700),
                _sans(row["extra_bunker"], color=C_TEXT2),
                _sans(row["rate_premium"], color=C_ACCENT, weight=600),
                badge(row["status"], color=status_color),
            ])
        wsj_market_table(
            ["Lane", "Original Route", "Current Route", "Extra NM", "Extra Days", "Extra Bunker", "Rate Premium", "Status"],
            rows,
        )

        try:
            lanes = [r["lane"].split(" – ")[0] for r in _REROUTING]
            extra_d = [r["extra_days"] for r in _REROUTING]

            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=lanes,
                y=extra_d,
                marker_color=[C_LOW if d >= 14 else C_MOD if d >= 7 else C_HIGH for d in extra_d],
                text=[f"+{d}d" for d in extra_d],
                textposition="outside",
                textfont=dict(color=C_TEXT2, size=11),
                hovertemplate="<b>%{x}</b><br>Extra days: +%{y}<extra></extra>",
            ))
            apply_dark_layout(
                fig,
                title="Voyage Days Added by Rerouting",
                height=240,
                margin=dict(l=0, r=0, t=40, b=0),
                showlegend=False,
                xaxis=dict(
                    tickfont=dict(size=10, color=C_TEXT2),
                    showgrid=False,
                ),
                yaxis=dict(
                    title=dict(text="Extra Days", font=dict(size=10)),
                ),
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        except Exception as chart_exc:
            logger.debug(f"[tab_geopolitical] rerouting_chart: {chart_exc}")

    except Exception as exc:
        logger.warning(f"[tab_geopolitical] rerouting_impact: {exc}")
        st.info("Rerouting impact data unavailable.")


# ---------------------------------------------------------------------------
# Section 7 — Insurance & War Risk Premiums
# ---------------------------------------------------------------------------

def _render_war_risk_premiums() -> None:
    try:
        section_header(
            "Insurance & War Risk Premiums",
            "War risk insurance by region — Joint War Committee listed areas highlighted",
        )

        if not _WAR_RISK:
            st.info("No war-risk premium data available.")
            return

        rows = []
        for row in _WAR_RISK:
            jwc_color = C_LOW if row["jwc_listed"] == "Yes" else C_MOD if "Partial" in row["jwc_listed"] else C_HIGH
            trend_html = _trend_arrow(row["trend"])
            rows.append([
                _sans(row["region"], color=C_TEXT, weight=700),
                _sans(row["premium_pct"], color=C_LOW, weight=600),
                _sans(row["jwc_listed"], color=jwc_color, weight=600),
                _sans(row["base_annual"], color=C_TEXT2),
                _sans(row["kidnap_ransom"], color=C_TEXT2),
                trend_html,
                _sans(row["notes"], color=C_TEXT3),
            ])
        wsj_market_table(
            ["Region", "Premium (% of value)", "JWC Listed", "Annual Equiv.", "K&R Coverage", "Trend", "Notes"],
            rows,
        )

        st.markdown(
            insight_card_html(
                title="Joint War Committee — Listed Areas schedule",
                score=0.5,
                action="Monitor",
                rationale=(
                    "The Joint War Committee (Lloyd's Market Association) maintains a "
                    "Listed Areas schedule. Vessels transiting listed areas must notify "
                    "their war risk underwriter and may face additional premium calls of "
                    "0.025–0.75% of vessel value per breach. Red Sea and Black Sea areas "
                    "currently attract the highest additional premium calls."
                ),
                category="INSURANCE",
            ),
            unsafe_allow_html=True,
        )

    except Exception as exc:
        logger.warning(f"[tab_geopolitical] war_risk_premiums: {exc}")
        st.info("War risk premium data unavailable.")


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(macro_data=None, insights=None, news_items=None, *args, **kwargs) -> None:
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('geopolitical'):
        try:
            page_header(
                title="Geopolitical Risk Intelligence",
                subtitle=(
                    "Institutional-grade geopolitical risk monitoring for global shipping operations — "
                    "hotspots, sanctions, trade wars, rerouting, and war risk insurance"
                ),
                icon="🌐",
                badge_text="Demo Data",
                badge_color=C_MOD,
            )

            _render_global_risk_heat(macro_data, insights)
            section_divider("Risk Geography")
            _render_risk_map()
            section_divider("Active Hotspots")
            _render_hotspot_monitor()
            section_divider("Sanctions & Embargoes")
            _render_sanctions_tracker()
            section_divider("Trade War")
            _render_trade_war_monitor()
            section_divider("Rerouting Impact")
            _render_rerouting_impact()
            section_divider("War Risk Insurance")
            _render_war_risk_premiums()

            st.markdown(
                source_footer([
                    {"name": "IMO / BIMCO", "kind": "demo", "quality": "demo"},
                    {"name": "Lloyd's MIU", "kind": "demo", "quality": "demo"},
                    {"name": "US OFAC / EU Sanctions Map", "kind": "demo", "quality": "demo"},
                    {"name": "Joint War Committee", "kind": "demo", "quality": "demo"},
                ], align="center"),
                unsafe_allow_html=True,
            )

        except Exception as exc:
            logger.error(f"[tab_geopolitical] render: {exc}")
            st.error(f"Geopolitical tab error: {exc}")
