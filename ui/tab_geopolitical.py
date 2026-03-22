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
C_ORANGE  = "#f97316"
C_PURPLE  = "#8b5cf6"

_LEVEL_COLOR: dict[str, str] = {
    "CRITICAL": C_LOW,
    "HIGH":     C_ORANGE,
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

# Country risk data for choropleth
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
# HTML helpers
# ---------------------------------------------------------------------------

def _card(content: str, border_color: str = C_BORDER, extra_style: str = "") -> str:
    return (
        f'<div style="background:{C_CARD};border:1px solid {border_color};'
        f'border-radius:14px;padding:20px 22px;margin-bottom:14px;{extra_style}">'
        + content + "</div>"
    )


def _section_title(text: str, subtitle: str = "") -> None:
    sub = (
        f'<div style="color:{C_TEXT2};font-size:0.83rem;margin-top:3px">{subtitle}</div>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="margin:6px 0 16px">'
        f'<div style="font-size:1.08rem;font-weight:700;color:{C_TEXT};letter-spacing:-0.01em">{text}</div>'
        + sub + "</div>",
        unsafe_allow_html=True,
    )


def _badge(text: str, color: str) -> str:
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color}44;'
        f'border-radius:6px;padding:2px 9px;font-size:0.72rem;font-weight:700;'
        f'letter-spacing:0.04em">{text}</span>'
    )


def _trend_arrow(trend: str) -> str:
    if trend == "UP":
        return f'<span style="color:{C_LOW};font-weight:700">▲ Rising</span>'
    if trend == "DOWN":
        return f'<span style="color:{C_HIGH};font-weight:700">▼ Falling</span>'
    return f'<span style="color:{C_MOD};font-weight:700">→ Stable</span>'

# ---------------------------------------------------------------------------
# Section 1 — Global Risk Heat
# ---------------------------------------------------------------------------

def _render_global_risk_heat(macro_data: dict | None, insights: list | None) -> None:
    try:
        _section_title(
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

        cols = st.columns([1.5, 1, 1, 1])
        with cols[0]:
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_LOW}44;border-radius:14px;'
                f'padding:24px 22px;text-align:center">'
                f'<div style="font-size:0.78rem;color:{C_TEXT2};font-weight:600;letter-spacing:0.08em;'
                f'text-transform:uppercase;margin-bottom:6px">Global Geopolitical Risk Index</div>'
                f'<div style="font-size:3.2rem;font-weight:800;color:{C_LOW};line-height:1">{risk_index}</div>'
                f'<div style="font-size:0.8rem;color:{C_TEXT3};margin-top:4px">out of 100</div>'
                f'<div style="margin-top:10px;font-size:0.9rem;color:{delta_color};font-weight:700">'
                f'{delta_str} vs prior month</div>'
                f'<div style="font-size:0.75rem;color:{C_TEXT3};margin-top:4px">Prior month: {prior_month}/100</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        for i, (region, score, level) in enumerate(top_regions):
            lvl_color = _LEVEL_COLOR.get(level, C_TEXT2)
            with cols[i + 1]:
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {lvl_color}44;'
                    f'border-radius:14px;padding:20px 16px;height:100%">'
                    f'<div style="font-size:0.72rem;color:{C_TEXT3};font-weight:600;'
                    f'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px">Top Risk Region #{i+1}</div>'
                    f'<div style="font-size:0.92rem;font-weight:700;color:{C_TEXT};margin-bottom:8px">{region}</div>'
                    f'<div style="font-size:2rem;font-weight:800;color:{lvl_color}">{score}</div>'
                    f'<div style="margin-top:8px">{_badge(level, lvl_color)}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        if insights:
            for ins in insights[:2]:
                try:
                    st.markdown(
                        _card(f'<div style="font-size:0.85rem;color:{C_TEXT2}">📌 {ins}</div>'),
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
        _section_title(
            "Geopolitical Risk Map",
            "Country risk to shipping operations — hover for details. Shipping lane overlays shown.",
        )

        iso_codes = list(_COUNTRY_RISK.keys())
        scores = list(_COUNTRY_RISK.values())

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
                [0.0,  "#10b981"],
                [0.35, "#10b981"],
                [0.50, "#f59e0b"],
                [0.70, "#f97316"],
                [1.0,  "#ef4444"],
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

        # Shipping lane overlays (key lanes as scatter_geo lines)
        lane_lats = [
            # Red Sea lane
            [12.5, 15.0, 20.0, 25.0, 29.9, None],
            # Cape of Good Hope reroute
            [1.3, -10.0, -25.0, -34.4, -20.0, -10.0, 1.3, None],
            # Taiwan Strait
            [22.0, 24.0, 26.0, None],
            # Strait of Hormuz
            [24.0, 25.5, 26.5, None],
            # Panama Canal
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
        fig.update_layout(
            height=480,
            margin=dict(l=0, r=0, t=0, b=0),
            paper_bgcolor=C_CARD,
            plot_bgcolor=C_CARD,
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
            font=dict(color=C_TEXT),
        )

        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        st.markdown(
            f'<div style="display:flex;gap:20px;margin-top:6px;margin-bottom:16px;flex-wrap:wrap">'
            f'<span style="font-size:0.75rem;color:{C_TEXT3}">■ <span style="color:{C_LOW}">Red</span> = Critical risk (80–100)</span>'
            f'<span style="font-size:0.75rem;color:{C_TEXT3}">■ <span style="color:{C_MOD}">Amber</span> = Moderate risk (40–69)</span>'
            f'<span style="font-size:0.75rem;color:{C_TEXT3}">■ <span style="color:{C_HIGH}">Green</span> = Lower risk (0–39)</span>'
            f'<span style="font-size:0.75rem;color:{C_TEXT3}">— Lines = key shipping lanes</span>'
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
        _section_title(
            "Hotspot Monitor",
            "Live risk cards for active maritime security hotspots",
        )

        for hs in _HOTSPOTS:
            level = hs["level"]
            lvl_color = _LEVEL_COLOR.get(level, C_TEXT2)

            fields_html = ""
            for label, key in [
                ("Affected Routes", "routes"),
                ("Rate Premium", "rate_premium"),
                ("Extra Voyage Time", "extra_days"),
                ("Extra Distance", "extra_nm"),
                ("Vessels Affected", "vessels_affected"),
            ]:
                val = hs.get(key, "N/A")
                if val and val != "N/A":
                    fields_html += (
                        f'<div style="margin-top:8px">'
                        f'<span style="color:{C_TEXT3};font-size:0.75rem">{label}:&nbsp;</span>'
                        f'<span style="color:{C_TEXT};font-size:0.8rem;font-weight:600">{val}</span>'
                        f'</div>'
                    )

            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {lvl_color}33;border-radius:14px;'
                f'padding:20px 22px;margin-bottom:14px">'
                f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">'
                f'<span style="font-size:1.3rem">{hs["icon"]}</span>'
                f'<span style="font-size:1.0rem;font-weight:700;color:{C_TEXT}">{hs["name"]}</span>'
                f'&nbsp;{_badge(level, lvl_color)}'
                f'</div>'
                f'<div style="font-size:0.83rem;color:{C_TEXT2};line-height:1.55;margin-bottom:4px">'
                f'{hs["situation"]}</div>'
                + fields_html +
                f'</div>',
                unsafe_allow_html=True,
            )

    except Exception as exc:
        logger.warning(f"[tab_geopolitical] hotspot_monitor: {exc}")
        st.info("Hotspot data unavailable.")

# ---------------------------------------------------------------------------
# Section 4 — Sanctions & Embargo Tracker
# ---------------------------------------------------------------------------

def _render_sanctions_tracker() -> None:
    try:
        _section_title(
            "Sanctions & Embargo Tracker",
            "Active shipping-relevant sanctions by country/entity — compliance critical",
        )

        header = (
            f'<div style="display:grid;grid-template-columns:1.4fr 1.2fr 1.1fr 1fr 0.9fr 2fr;'
            f'gap:8px;padding:8px 14px;background:{C_SURFACE};border-radius:8px;'
            f'font-size:0.72rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px">'
            f'<div>Entity</div><div>Sanctioning Body</div><div>Asset Type</div>'
            f'<div>Ships Affected</div><div>Effective</div><div>Compliance Notes</div>'
            f'</div>'
        )
        st.markdown(header, unsafe_allow_html=True)

        for row in _SANCTIONS:
            st.markdown(
                f'<div style="display:grid;grid-template-columns:1.4fr 1.2fr 1.1fr 1fr 0.9fr 2fr;'
                f'gap:8px;padding:12px 14px;background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:8px;margin-bottom:6px;font-size:0.8rem">'
                f'<div style="color:{C_TEXT};font-weight:600">{row["entity"]}</div>'
                f'<div style="color:{C_MOD}">{row["body"]}</div>'
                f'<div style="color:{C_TEXT2}">{row["asset_type"]}</div>'
                f'<div style="color:{C_LOW};font-weight:600">{row["ships_affected"]}</div>'
                f'<div style="color:{C_TEXT3}">{row["effective"]}</div>'
                f'<div style="color:{C_TEXT2};font-size:0.76rem">{row["notes"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    except Exception as exc:
        logger.warning(f"[tab_geopolitical] sanctions_tracker: {exc}")
        st.info("Sanctions data unavailable.")

# ---------------------------------------------------------------------------
# Section 5 — Trade War Monitor
# ---------------------------------------------------------------------------

def _render_trade_war_monitor() -> None:
    try:
        _section_title(
            "Trade War Monitor",
            "Tariff escalation by major trade pair and shipping volume impact",
        )

        header = (
            f'<div style="display:grid;grid-template-columns:1.2fr 1fr 1.4fr 1.4fr 2fr 0.8fr;'
            f'gap:8px;padding:8px 14px;background:{C_SURFACE};border-radius:8px;'
            f'font-size:0.72rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px">'
            f'<div>Trade Pair</div><div>Pre-Tariff Rate</div><div>Current Rate</div>'
            f'<div>Volume Impact</div><div>Shipping Impact</div><div>Severity</div>'
            f'</div>'
        )
        st.markdown(header, unsafe_allow_html=True)

        for row in _TARIFFS:
            sev = row["severity"]
            sev_color = _LEVEL_COLOR.get(sev, C_TEXT2)
            st.markdown(
                f'<div style="display:grid;grid-template-columns:1.2fr 1fr 1.4fr 1.4fr 2fr 0.8fr;'
                f'gap:8px;padding:12px 14px;background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:8px;margin-bottom:6px;font-size:0.8rem">'
                f'<div style="color:{C_TEXT};font-weight:700">{row["pair"]}</div>'
                f'<div style="color:{C_TEXT2}">{row["pre_rate"]}</div>'
                f'<div style="color:{C_LOW};font-weight:700">{row["current_rate"]}</div>'
                f'<div style="color:{C_MOD}">{row["volume_impact"]}</div>'
                f'<div style="color:{C_TEXT2};font-size:0.76rem">{row["shipping_impact"]}</div>'
                f'<div>{_badge(sev, sev_color)}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            _card(
                f'<div style="font-size:0.82rem;color:{C_TEXT2}">'
                f'<b style="color:{C_MOD}">Key insight:</b> US-China tariff escalation to 145%/125% is the primary '
                f'structural shock. Trans-Pacific container demand has fallen ~35% YoY on direct lanes, '
                f'but transshipment via Vietnam and Mexico is surging, creating secondary port congestion. '
                f'Carriers are deploying blank sailings to manage capacity utilisation.'
                f'</div>'
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
        _section_title(
            "Rerouting Impact",
            "Trade lanes affected by Red Sea / Panama disruptions — cost and time penalties",
        )

        header = (
            f'<div style="display:grid;grid-template-columns:1.3fr 1fr 1.2fr 0.9fr 0.9fr 1.2fr 1.2fr 0.9fr;'
            f'gap:6px;padding:8px 14px;background:{C_SURFACE};border-radius:8px;'
            f'font-size:0.68rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">'
            f'<div>Lane</div><div>Original Route</div><div>Current Route</div>'
            f'<div>Extra NM</div><div>Extra Days</div><div>Extra Bunker</div>'
            f'<div>Rate Premium</div><div>Status</div>'
            f'</div>'
        )
        st.markdown(header, unsafe_allow_html=True)

        for row in _REROUTING:
            status_color = C_LOW if row["status"] == "Active reroute" else C_MOD
            st.markdown(
                f'<div style="display:grid;grid-template-columns:1.3fr 1fr 1.2fr 0.9fr 0.9fr 1.2fr 1.2fr 0.9fr;'
                f'gap:6px;padding:12px 14px;background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:8px;margin-bottom:6px;font-size:0.78rem">'
                f'<div style="color:{C_TEXT};font-weight:700">{row["lane"]}</div>'
                f'<div style="color:{C_TEXT2}">{row["original"]}</div>'
                f'<div style="color:{C_MOD}">{row["current"]}</div>'
                f'<div style="color:{C_LOW};font-weight:700">+{row["extra_nm"]:,}</div>'
                f'<div style="color:{C_LOW};font-weight:700">+{row["extra_days"]}</div>'
                f'<div style="color:{C_TEXT2};font-size:0.73rem">{row["extra_bunker"]}</div>'
                f'<div style="color:{C_ACCENT};font-weight:600">{row["rate_premium"]}</div>'
                f'<div>{_badge(row["status"], status_color)}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # Mini bar chart of extra days
        try:
            lanes = [r["lane"].split(" – ")[0] for r in _REROUTING]
            extra_d = [r["extra_days"] for r in _REROUTING]
            extra_b = [r["extra_bunker"].split("–")[0].replace("$", "").replace(",", "").strip() for r in _REROUTING]

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
            fig.update_layout(
                height=240,
                margin=dict(l=0, r=0, t=16, b=0),
                paper_bgcolor=C_CARD,
                plot_bgcolor=C_CARD,
                font=dict(color=C_TEXT2),
                xaxis=dict(
                    color=C_TEXT3, gridcolor=C_BORDER, showgrid=False,
                    tickfont=dict(size=10, color=C_TEXT2),
                ),
                yaxis=dict(
                    color=C_TEXT3, gridcolor=C_BORDER,
                    title=dict(text="Extra Days", font=dict(size=10)),
                ),
                showlegend=False,
                title=dict(
                    text="Voyage Days Added by Rerouting",
                    font=dict(color=C_TEXT2, size=12),
                    x=0,
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
        _section_title(
            "Insurance & War Risk Premiums",
            "War risk insurance by region — Joint War Committee listed areas highlighted",
        )

        header = (
            f'<div style="display:grid;grid-template-columns:1.5fr 1.4fr 0.9fr 1.2fr 1.2fr 0.7fr 2fr;'
            f'gap:6px;padding:8px 14px;background:{C_SURFACE};border-radius:8px;'
            f'font-size:0.68rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">'
            f'<div>Region</div><div>Premium (% of value)</div><div>JWC Listed</div>'
            f'<div>Annual Equiv.</div><div>K&R Coverage</div><div>Trend</div><div>Notes</div>'
            f'</div>'
        )
        st.markdown(header, unsafe_allow_html=True)

        for row in _WAR_RISK:
            jwc_color = C_LOW if row["jwc_listed"] == "Yes" else C_MOD if "Partial" in row["jwc_listed"] else C_HIGH
            trend_html = _trend_arrow(row["trend"])
            st.markdown(
                f'<div style="display:grid;grid-template-columns:1.5fr 1.4fr 0.9fr 1.2fr 1.2fr 0.7fr 2fr;'
                f'gap:6px;padding:12px 14px;background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:8px;margin-bottom:6px;font-size:0.78rem">'
                f'<div style="color:{C_TEXT};font-weight:700">{row["region"]}</div>'
                f'<div style="color:{C_LOW};font-weight:600">{row["premium_pct"]}</div>'
                f'<div style="color:{jwc_color};font-weight:600">{row["jwc_listed"]}</div>'
                f'<div style="color:{C_TEXT2}">{row["base_annual"]}</div>'
                f'<div style="color:{C_TEXT2};font-size:0.73rem">{row["kidnap_ransom"]}</div>'
                f'<div>{trend_html}</div>'
                f'<div style="color:{C_TEXT3};font-size:0.73rem">{row["notes"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            _card(
                f'<div style="font-size:0.82rem;color:{C_TEXT2}">'
                f'<b style="color:{C_ACCENT}">JWC Note:</b> The Joint War Committee (Lloyd\'s Market Association) '
                f'maintains a Listed Areas schedule. Vessels transiting listed areas must notify their war risk '
                f'underwriter and may face additional premium calls of 0.025–0.75% of vessel value per breach. '
                f'Red Sea and Black Sea areas currently attract highest additional premium calls.'
                f'</div>'
            ),
            unsafe_allow_html=True,
        )

    except Exception as exc:
        logger.warning(f"[tab_geopolitical] war_risk_premiums: {exc}")
        st.info("War risk premium data unavailable.")

# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(macro_data=None, insights=None, news_items=None) -> None:
    try:
        st.markdown(
            '<style>'
            f'[data-testid="stAppViewContainer"] {{ background:{C_BG}; }}'
            f'[data-testid="stSidebar"] {{ background:{C_SURFACE}; }}'
            '.stPlotlyChart { border-radius: 12px; overflow: hidden; }'
            '</style>',
            unsafe_allow_html=True,
        )

        st.markdown(
            f'<div style="margin-bottom:28px;padding-bottom:20px;border-bottom:1px solid {C_BORDER}">'
            f'<div style="font-size:1.6rem;font-weight:800;color:{C_TEXT};letter-spacing:-0.02em">'
            f'Geopolitical Risk Intelligence</div>'
            f'<div style="font-size:0.88rem;color:{C_TEXT2};margin-top:6px">'
            f'Institutional-grade geopolitical risk monitoring for global shipping operations — '
            f'hotspots, sanctions, trade wars, rerouting, and war risk insurance</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        _render_global_risk_heat(macro_data, insights)
        st.markdown(f'<div style="margin:28px 0 0;border-top:1px solid {C_BORDER}"></div>', unsafe_allow_html=True)

        _render_risk_map()
        st.markdown(f'<div style="margin:28px 0 0;border-top:1px solid {C_BORDER}"></div>', unsafe_allow_html=True)

        _render_hotspot_monitor()
        st.markdown(f'<div style="margin:28px 0 0;border-top:1px solid {C_BORDER}"></div>', unsafe_allow_html=True)

        _render_sanctions_tracker()
        st.markdown(f'<div style="margin:28px 0 0;border-top:1px solid {C_BORDER}"></div>', unsafe_allow_html=True)

        _render_trade_war_monitor()
        st.markdown(f'<div style="margin:28px 0 0;border-top:1px solid {C_BORDER}"></div>', unsafe_allow_html=True)

        _render_rerouting_impact()
        st.markdown(f'<div style="margin:28px 0 0;border-top:1px solid {C_BORDER}"></div>', unsafe_allow_html=True)

        _render_war_risk_premiums()

        st.markdown(
            f'<div style="margin-top:32px;padding-top:16px;border-top:1px solid {C_BORDER};'
            f'font-size:0.73rem;color:{C_TEXT3};text-align:center">'
            f'Data: IMO, Lloyd\'s MIU, BIMCO, US OFAC, EU Sanctions Map, Joint War Committee — '
            f'Updated 2026-03-22 | For institutional use only. Not financial advice.'
            f'</div>',
            unsafe_allow_html=True,
        )

    except Exception as exc:
        logger.error(f"[tab_geopolitical] render: {exc}")
        st.error(f"Geopolitical tab error: {exc}")
