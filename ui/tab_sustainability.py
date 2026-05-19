"""Sustainability tab — shipping ESG and sustainability intelligence."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_BG,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# Semantic CII rating colors (A–E scale) — tab-local because they extend the
# shared palette with a bright green for an "A" grade.
_CII_COLORS = {"A": "#2e9e6e", "B": "#34d399", "C": "#c9962b", "D": "#f97316", "E": "#c0392b"}

# Local chart palette for fuel mix (neutral / indigo / teal shades the global palette lacks).
_FUEL_GREY   = "#6b6760"
_FUEL_PURPLE = "#7c6eaf"
_FUEL_TEAL   = "#4a90a4"


# ── Static datasets ─────────────────────────────────────────────────────────

_CARRIERS = [
    {"carrier": "Maersk",          "cii": "B", "eeoi": 8.2,  "eco_pct": 34, "lng_pct": 12, "on_track": True,  "actions": "Methanol newbuilds, CII retrofits"},
    {"carrier": "MSC",             "cii": "C", "eeoi": 10.4, "eco_pct": 21, "lng_pct": 5,  "on_track": False, "actions": "Speed reduction program, scrubbers"},
    {"carrier": "CMA CGM",         "cii": "B", "eeoi": 9.1,  "eco_pct": 29, "lng_pct": 18, "on_track": True,  "actions": "LNG fleet expansion, biofuel blend"},
    {"carrier": "COSCO",           "cii": "C", "eeoi": 11.3, "eco_pct": 18, "lng_pct": 7,  "on_track": False, "actions": "Fleet renewal, EEDI compliance"},
    {"carrier": "Hapag-Lloyd",     "cii": "B", "eeoi": 8.7,  "eco_pct": 31, "lng_pct": 9,  "on_track": True,  "actions": "Methanol orders, efficiency upgrades"},
    {"carrier": "ONE",             "cii": "C", "eeoi": 10.8, "eco_pct": 20, "lng_pct": 4,  "on_track": False, "actions": "EEXI compliance, slow steaming"},
    {"carrier": "Evergreen",       "cii": "D", "eeoi": 12.6, "eco_pct": 12, "lng_pct": 2,  "on_track": False, "actions": "Urgent retrofit program required"},
    {"carrier": "Yang Ming",       "cii": "C", "eeoi": 11.1, "eco_pct": 17, "lng_pct": 3,  "on_track": False, "actions": "Speed optimization, biofuel trials"},
    {"carrier": "HMM",             "cii": "B", "eeoi": 8.9,  "eco_pct": 28, "lng_pct": 11, "on_track": True,  "actions": "LNG dual-fuel newbuilds"},
    {"carrier": "PIL",             "cii": "D", "eeoi": 13.2, "eco_pct": 9,  "lng_pct": 1,  "on_track": False, "actions": "Fleet phase-out plan needed"},
    {"carrier": "Zim",             "cii": "C", "eeoi": 10.2, "eco_pct": 22, "lng_pct": 6,  "on_track": False, "actions": "LNG charters, route optimization"},
    {"carrier": "WanHai",          "cii": "C", "eeoi": 10.9, "eco_pct": 16, "lng_pct": 3,  "on_track": False, "actions": "Feeder fleet efficiency program"},
]

_ROUTES = [
    {"route": "Asia–Europe",       "vessel": "ULCV 20k+ TEU",  "co2_teu_km": 0.0098, "vs_2008": -32, "vs_imo": -8,  "trend": "Improving"},
    {"route": "Trans-Pacific",     "vessel": "VLCV 14k TEU",   "co2_teu_km": 0.0112, "vs_2008": -28, "vs_imo": +4,  "trend": "Worsening"},
    {"route": "Trans-Atlantic",    "vessel": "Neo-Panamax 12k", "co2_teu_km": 0.0134, "vs_2008": -22, "vs_imo": +11, "trend": "Worsening"},
    {"route": "Asia–LatAm",        "vessel": "Panamax 8k TEU",  "co2_teu_km": 0.0109, "vs_2008": -30, "vs_imo": +2,  "trend": "Stable"},
    {"route": "Intra-Asia",        "vessel": "Feeder 2k TEU",   "co2_teu_km": 0.0178, "vs_2008": -18, "vs_imo": +28, "trend": "Worsening"},
    {"route": "Europe–LatAm",      "vessel": "Panamax 9k TEU",  "co2_teu_km": 0.0121, "vs_2008": -25, "vs_imo": +8,  "trend": "Stable"},
    {"route": "Asia–Mideast Gulf", "vessel": "Feeder 3k TEU",   "co2_teu_km": 0.0162, "vs_2008": -20, "vs_imo": +22, "trend": "Improving"},
    {"route": "Europe–W Africa",   "vessel": "MPV 1.5k TEU",    "co2_teu_km": 0.0201, "vs_2008": -14, "vs_imo": +38, "trend": "Worsening"},
]

_ESG_SCORES = [
    {"company": "Maersk (MAERSK-B)", "overall": 78, "env": 82, "social": 74, "gov": 78, "cdp": "A-", "djsi": True,  "cbds": 88},
    {"company": "CMA CGM (priv.)",   "overall": 71, "env": 74, "social": 70, "gov": 69, "cdp": "B",  "djsi": False, "cbds": 72},
    {"company": "Hapag-Lloyd (HLAG)","overall": 73, "env": 76, "social": 72, "gov": 71, "cdp": "B+", "djsi": True,  "cbds": 79},
    {"company": "Evergreen (2603)",  "overall": 52, "env": 48, "social": 55, "gov": 53, "cdp": "C",  "djsi": False, "cbds": 44},
    {"company": "HMM (011200)",      "overall": 65, "env": 68, "social": 63, "gov": 64, "cdp": "B-", "djsi": False, "cbds": 61},
    {"company": "Yang Ming (2609)", "overall": 58, "env": 55, "social": 60, "gov": 59, "cdp": "C+", "djsi": False, "cbds": 52},
    {"company": "Zim (ZIM)",        "overall": 62, "env": 60, "social": 65, "gov": 61, "cdp": "B-", "djsi": False, "cbds": 58},
    {"company": "ONE (priv.)",      "overall": 67, "env": 69, "social": 66, "gov": 66, "cdp": "B",  "djsi": False, "cbds": 64},
]

_EU_EXPOSURE = [
    {"carrier": "Maersk",      "eu_rev_pct": 41, "carbon_int": 8.2,  "est_ets_cost_mUSD": 312},
    {"carrier": "MSC",         "eu_rev_pct": 38, "carbon_int": 10.4, "est_ets_cost_mUSD": 498},
    {"carrier": "CMA CGM",     "eu_rev_pct": 44, "carbon_int": 9.1,  "est_ets_cost_mUSD": 421},
    {"carrier": "COSCO",       "eu_rev_pct": 28, "carbon_int": 11.3, "est_ets_cost_mUSD": 289},
    {"carrier": "Hapag-Lloyd", "eu_rev_pct": 49, "carbon_int": 8.7,  "est_ets_cost_mUSD": 367},
    {"carrier": "Evergreen",   "eu_rev_pct": 22, "carbon_int": 12.6, "est_ets_cost_mUSD": 198},
    {"carrier": "Zim",         "eu_rev_pct": 31, "carbon_int": 10.2, "est_ets_cost_mUSD": 141},
]

_PORT_INFRA = [
    {"port": "Rotterdam",    "lng_stations": 8,  "methanol_terminals": 2, "ammonia_ready": True,  "green_shore_power": True},
    {"port": "Singapore",    "lng_stations": 12, "methanol_terminals": 1, "ammonia_ready": True,  "green_shore_power": False},
    {"port": "Shanghai",     "lng_stations": 6,  "methanol_terminals": 0, "ammonia_ready": False, "green_shore_power": True},
    {"port": "Antwerp",      "lng_stations": 5,  "methanol_terminals": 3, "ammonia_ready": True,  "green_shore_power": True},
    {"port": "Hamburg",      "lng_stations": 4,  "methanol_terminals": 2, "ammonia_ready": False, "green_shore_power": True},
    {"port": "Los Angeles",  "lng_stations": 3,  "methanol_terminals": 0, "ammonia_ready": False, "green_shore_power": True},
    {"port": "Busan",        "lng_stations": 7,  "methanol_terminals": 1, "ammonia_ready": False, "green_shore_power": False},
    {"port": "Dubai (JEBEL)","lng_stations": 2,  "methanol_terminals": 0, "ammonia_ready": False, "green_shore_power": False},
]

_SPEED_TABLE = [
    {"speed_kn": 24, "fuel_tpd": 310, "daily_opex_usd": 94200, "capacity_util_pct": 100, "co2_tpd": 985},
    {"speed_kn": 22, "fuel_tpd": 240, "daily_opex_usd": 75800, "capacity_util_pct": 96,  "co2_tpd": 763},
    {"speed_kn": 20, "fuel_tpd": 181, "daily_opex_usd": 60100, "capacity_util_pct": 92,  "co2_tpd": 575},
    {"speed_kn": 18, "fuel_tpd": 131, "daily_opex_usd": 46800, "capacity_util_pct": 87,  "co2_tpd": 416},
    {"speed_kn": 16, "fuel_tpd": 92,  "daily_opex_usd": 36100, "capacity_util_pct": 81,  "co2_tpd": 292},
    {"speed_kn": 14, "fuel_tpd": 62,  "daily_opex_usd": 27400, "capacity_util_pct": 74,  "co2_tpd": 197},
    {"speed_kn": 12, "fuel_tpd": 39,  "daily_opex_usd": 20200, "capacity_util_pct": 65,  "co2_tpd": 124},
]


# ── Cell formatters ─────────────────────────────────────────────────────────

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


def _yn_badge(val: bool) -> str:
    return badge("YES" if val else "NO", color=C_HIGH if val else C_LOW)


# ── Hero KPIs ───────────────────────────────────────────────────────────────

def _render_hero_kpis() -> None:
    try:
        section_header(
            "Sustainability Dashboard",
            subtitle="Real-time shipping ESG intelligence — IMO, EU ETS, and green fuel metrics",
        )
        metric_card_row(
            [
                {"label": "Global Shipping CO₂",     "value": "812M t/yr",       "accent": C_LOW,
                 "delta": "▲ +1.2% YoY", "delta_color": C_LOW},
                {"label": "Carbon Intensity (CII)",  "value": "8.9 gCO₂/t-nm",   "accent": C_HIGH,
                 "delta": "▼ −4.1% vs 2022", "delta_color": C_HIGH},
                {"label": "Fleet IMO-2030 Ready",    "value": "23.4%",            "accent": C_MOD,
                 "delta": "Target: 100% by 2030", "delta_color": C_TEXT2},
                {"label": "EU ETS Carbon Price",     "value": "€63/t CO₂",        "accent": C_MOD,
                 "delta": "▼ −8% MTD", "delta_color": C_HIGH},
                {"label": "Green Fuel Adoption",     "value": "7.4%",              "accent": C_HIGH,
                 "delta": "▲ +1.9pp YoY", "delta_color": C_HIGH},
            ],
            columns=5,
        )
        _src = DataSource.demo("IMO / EU ETS / Industry Reports (synthetic)")
        st.markdown(source_footer([_src]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Hero KPIs render error")
        st.error("Could not render sustainability dashboard KPIs.")


# ── CII compliance tracker ──────────────────────────────────────────────────

def _render_cii_tracker() -> None:
    try:
        section_header(
            "IMO 2030/2050 Compliance Tracker",
            subtitle="CII ratings, EEOI, and eco-fleet progress for 12 major carriers",
        )
        rows = []
        for row in _CARRIERS:
            cii_color   = _CII_COLORS.get(row["cii"], C_TEXT2)
            track_color = C_HIGH if row["on_track"] else C_LOW
            eeoi_color  = C_HIGH if row["eeoi"] < 9.5  else (C_MOD if row["eeoi"] < 11.5  else C_LOW)
            eco_color   = C_HIGH if row["eco_pct"] >= 28 else (C_MOD if row["eco_pct"] >= 18 else C_LOW)
            rows.append([
                _sans(row["carrier"], color=C_TEXT, weight=700),
                badge(row["cii"], color=cii_color),
                _mono(f"{row['eeoi']:.1f}", color=eeoi_color),
                _mono(f"{row['eco_pct']}%", color=eco_color),
                _mono(f"{row['lng_pct']}%", color=C_TEXT2),
                badge("Yes" if row["on_track"] else "No", color=track_color),
                _sans(row["actions"], color=C_TEXT2),
            ])
        wsj_market_table(
            headers=[
                "Carrier", "CII", "EEOI", "Eco %",
                "LNG Dual-Fuel %", "On Track 2030?", "Key Actions",
            ],
            rows=rows,
        )
        _src = DataSource.demo("IMO / Carrier ESG Reports (synthetic)")
        st.markdown(source_footer([_src]), unsafe_allow_html=True)
    except Exception:
        logger.exception("CII tracker render error")
        st.error("Could not render compliance tracker.")


# ── Route carbon intensity ──────────────────────────────────────────────────

def _render_route_carbon() -> None:
    try:
        section_header(
            "Carbon Intensity by Route",
            subtitle="CO₂ per TEU-km vs 2008 baseline and IMO 2030 target (−40% vs 2008)",
        )
        rows = []
        for row in _ROUTES:
            vs08_color  = C_HIGH if row["vs_2008"] <= -30 else (C_MOD if row["vs_2008"] <= -20 else C_LOW)
            vsimo_color = C_HIGH if row["vs_imo"] <= 0   else (C_MOD if row["vs_imo"] <= 10  else C_LOW)
            trend_color = C_HIGH if row["trend"] == "Improving" else (C_MOD if row["trend"] == "Stable" else C_LOW)
            sign08  = "+" if row["vs_2008"] > 0 else ""
            signimo = "+" if row["vs_imo"]  > 0 else ""
            rows.append([
                _sans(row["route"],  color=C_TEXT, weight=700),
                _sans(row["vessel"], color=C_TEXT2),
                _mono(f"{row['co2_teu_km']:.4f}", color=C_TEXT),
                _mono(f"{sign08}{row['vs_2008']}%", color=vs08_color),
                _mono(f"{signimo}{row['vs_imo']}%", color=vsimo_color),
                badge(row["trend"], color=trend_color),
            ])
        wsj_market_table(
            headers=[
                "Route", "Vessel Class", "CO₂/TEU-km (g)",
                "vs 2008", "vs IMO Target", "Trend",
            ],
            rows=rows,
        )
        _src = DataSource.demo("IMO 2030 Target / Sea Intelligence (synthetic)")
        st.markdown(source_footer([_src]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Route carbon render error")
        st.error("Could not render carbon intensity by route.")


# ── Green fuel transition ───────────────────────────────────────────────────

def _render_green_fuel() -> None:
    try:
        section_header(
            "Green Fuel Transition",
            subtitle="Alternative fuel adoption, newbuild orderbook mix, cost premiums, and port infrastructure",
        )
        col_pie, col_bar = st.columns(2, gap="large")

        with col_pie:
            try:
                labels  = ["VLSFO", "LNG", "Biodiesel", "Methanol", "Ammonia"]
                values  = [92.6, 4.5, 2.0, 0.8, 0.1]
                colors  = [_FUEL_GREY, C_ACCENT, C_HIGH, _FUEL_PURPLE, _FUEL_TEAL]
                fig_pie = go.Figure(go.Pie(
                    labels=labels, values=values, hole=0.55,
                    marker=dict(colors=colors, line=dict(color=C_BG, width=2)),
                    textinfo="label+percent",
                    textfont=dict(color=C_TEXT, size=11),
                    hovertemplate="<b>%{label}</b><br>Share: %{percent}<extra></extra>",
                ))
                apply_dark_layout(
                    fig_pie,
                    title="Current Fleet Fuel Mix",
                    showlegend=False, height=300,
                )
                st.plotly_chart(
                    fig_pie, use_container_width=True,
                    config={"displayModeBar": False},
                    key="sustain_fuel_mix_pie",
                )
            except Exception:
                logger.exception("Fuel pie chart error")
                st.warning("Fuel mix chart unavailable.")

        with col_bar:
            try:
                vessel_classes = ["ULCVs", "VLCVs", "Panamaxes", "Feeders", "Bulk", "Tankers"]
                conv = [42, 38, 61, 88, 71, 65]
                dual = [58, 62, 39, 12, 29, 35]
                fig_bar = go.Figure()
                fig_bar.add_trace(go.Bar(
                    name="Conventional Fuel", x=vessel_classes, y=conv,
                    marker_color=_FUEL_GREY,
                    hovertemplate="<b>%{x}</b> — Conventional: %{y}%<extra></extra>",
                ))
                fig_bar.add_trace(go.Bar(
                    name="Dual-Fuel / Alt-Fuel", x=vessel_classes, y=dual,
                    marker_color=C_ACCENT,
                    hovertemplate="<b>%{x}</b> — Alt-Fuel: %{y}%<extra></extra>",
                ))
                apply_dark_layout(
                    fig_bar,
                    title="Newbuild Orderbook by Fuel Type (%)",
                    barmode="stack", height=300,
                    yaxis=dict(ticksuffix="%"),
                )
                st.plotly_chart(
                    fig_bar, use_container_width=True,
                    config={"displayModeBar": False},
                    key="sustain_newbuild_orderbook_bar",
                )
            except Exception:
                logger.exception("Fuel bar chart error")
                st.warning("Newbuild orderbook chart unavailable.")

        # Cost premium row
        metric_card_row(
            [
                {"label": "LNG",            "value": "+$18–28",   "accent": C_ACCENT,
                 "sublabel": "per TEU Asia–EU"},
                {"label": "Bio-Methanol",   "value": "+$42–61",   "accent": C_MOD,
                 "sublabel": "per TEU Asia–EU"},
                {"label": "Green Ammonia",  "value": "+$90–140",  "accent": C_LOW,
                 "sublabel": "per TEU Asia–EU"},
                {"label": "Green H₂",       "value": "+$110–180", "accent": C_LOW,
                 "sublabel": "per TEU Asia–EU"},
            ],
            columns=4,
        )

        # Port infrastructure table
        section_header(
            "Port Green Fuel Infrastructure Readiness",
            subtitle="Bunkering stations and shore-power readiness at major hubs",
        )
        rows = []
        for row in _PORT_INFRA:
            lng_color = C_HIGH if row["lng_stations"] >= 7 else (C_MOD if row["lng_stations"] >= 4 else C_LOW)
            rows.append([
                _sans(row["port"], color=C_TEXT, weight=700),
                _mono(str(row["lng_stations"]), color=lng_color),
                _mono(str(row["methanol_terminals"]), color=C_TEXT2),
                _yn_badge(row["ammonia_ready"]),
                _yn_badge(row["green_shore_power"]),
            ])
        wsj_market_table(
            headers=["Port", "LNG Stations", "Methanol Terminals", "Ammonia Ready", "Green Shore Power"],
            rows=rows,
        )
        _src = DataSource.demo("SGMF / Port Authority Reports (synthetic)")
        st.markdown(source_footer([_src]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Green fuel section render error")
        st.error("Could not render green fuel transition section.")


# ── EU ETS impact ───────────────────────────────────────────────────────────

def _render_eu_ets() -> None:
    try:
        section_header(
            "EU ETS Impact Analysis",
            subtitle="Shipping entered EU Emissions Trading System Jan 2024 — cost exposure and compliance implications",
        )
        col_chart, col_calc = st.columns([3, 2], gap="large")

        with col_chart:
            try:
                months = [
                    "Jan-23", "Apr-23", "Jul-23", "Oct-23",
                    "Jan-24", "Apr-24", "Jul-24", "Oct-24",
                    "Jan-25", "Apr-25", "Jul-25", "Oct-25",
                    "Jan-26",
                ]
                prices = [93, 87, 91, 72, 58, 63, 67, 59, 62, 70, 65, 61, 63]
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=months, y=prices, mode="lines+markers",
                    line=dict(color=C_ACCENT, width=2),
                    marker=dict(size=5, color=C_ACCENT),
                    fill="tozeroy",
                    fillcolor="rgba(53,114,176,0.1)",
                    hovertemplate="<b>%{x}</b><br>€%{y}/tonne CO₂<extra></extra>",
                    name="EU ETS Price",
                ))
                # add_vline with an annotation averages x-coords, which fails on a
                # categorical axis — add the line and the annotation separately.
                fig.add_vline(
                    x="Jan-24", line_dash="dash", line_color=C_MOD, line_width=1.5,
                )
                fig.add_annotation(
                    x="Jan-24", yref="paper", y=1.0, yanchor="bottom", xanchor="left",
                    text="Shipping enters EU ETS", showarrow=False,
                    font=dict(color=C_MOD, size=11),
                )
                apply_dark_layout(
                    fig,
                    title="EU Carbon Price (€/tonne CO₂)",
                    showlegend=False, height=280,
                    yaxis=dict(tickprefix="€"),
                )
                st.plotly_chart(
                    fig, use_container_width=True,
                    config={"displayModeBar": False},
                    key="sustain_eu_carbon_price",
                )
            except Exception:
                logger.exception("EU ETS chart error")
                st.warning("EU ETS price chart unavailable.")

        with col_calc:
            st.markdown('<div class="sub-section-header">ETS Cost Estimator</div>',
                        unsafe_allow_html=True)
            distance_nm  = st.number_input("Route distance (nm)", min_value=100, max_value=25000, value=11200, step=100)
            vessel_teu   = st.number_input("Vessel capacity (TEU)", min_value=500, max_value=24000, value=15000, step=500)
            load_factor  = st.slider("Load factor (%)", min_value=50, max_value=100, value=85)
            carbon_price = st.number_input("Carbon price (€/tonne)", min_value=20, max_value=150, value=63)
            try:
                fuel_cons_mt = (distance_nm / 10.0) * 0.14
                co2_mt       = fuel_cons_mt * 3.114
                ets_eligible = co2_mt * 0.5
                ets_cost_eur = ets_eligible * carbon_price
                teu_carried  = vessel_teu * (load_factor / 100)
                cost_per_teu = ets_cost_eur / teu_carried if teu_carried else 0
                metric_card_row(
                    [
                        {"label": "Est. CO₂ emitted",    "value": f"{co2_mt:.0f} t",      "accent": C_TEXT2},
                        {"label": "ETS-eligible (50%)",  "value": f"{ets_eligible:.0f} t", "accent": C_TEXT2},
                        {"label": "Total ETS cost",      "value": f"€{ets_cost_eur:,.0f}", "accent": C_MOD},
                        {"label": "Cost per TEU",        "value": f"€{cost_per_teu:.1f}",  "accent": C_ACCENT},
                    ],
                    columns=2,
                )
            except Exception:
                logger.exception("ETS calculator error")
                st.warning("Calculation error.")

        # Exposure table
        section_header(
            "Carrier EU ETS Exposure Ranking",
            subtitle="Estimated annual carbon cost, ranked most-exposed first",
        )
        sorted_ets = sorted(_EU_EXPOSURE, key=lambda r: r["est_ets_cost_mUSD"], reverse=True)
        rows = []
        for row in sorted_ets:
            rev_color  = C_LOW  if row["eu_rev_pct"] >= 40 else (C_MOD if row["eu_rev_pct"] >= 28 else C_HIGH)
            cost_color = C_LOW  if row["est_ets_cost_mUSD"] >= 400 else (C_MOD if row["est_ets_cost_mUSD"] >= 200 else C_HIGH)
            rows.append([
                _sans(row["carrier"], color=C_TEXT, weight=700),
                _mono(f"{row['eu_rev_pct']}%", color=rev_color),
                _mono(f"{row['carbon_int']} gCO₂/t-nm", color=C_TEXT2),
                _mono(f"${row['est_ets_cost_mUSD']}M USD", color=cost_color),
            ])
        wsj_market_table(
            headers=["Carrier", "EU Revenue %", "Carbon Intensity", "Est. Annual ETS Cost"],
            rows=rows,
        )
        _src = DataSource.demo("EU ETS / ICE Carbon (synthetic)")
        st.markdown(source_footer([_src]), unsafe_allow_html=True)
    except Exception:
        logger.exception("EU ETS section render error")
        st.error("Could not render EU ETS section.")


# ── ESG scores ──────────────────────────────────────────────────────────────

def _score_color(s: int) -> str:
    return C_HIGH if s >= 70 else (C_MOD if s >= 55 else C_LOW)


def _render_esg_scores() -> None:
    try:
        section_header(
            "ESG Score Comparison",
            subtitle="Aggregated ESG ratings, CDP scores, and DJSI inclusion for listed shipping companies",
        )
        rows = []
        for row in sorted(_ESG_SCORES, key=lambda r: r["overall"], reverse=True):
            cdp_color  = C_HIGH if row["cdp"].startswith("A")  else (C_MOD if row["cdp"].startswith("B") else C_LOW)
            cbds_color = C_HIGH if row["cbds"] >= 75 else (C_MOD if row["cbds"] >= 55 else C_LOW)
            rows.append([
                _sans(row["company"], color=C_TEXT, weight=700),
                _mono(str(row["overall"]), color=_score_color(row["overall"])),
                _mono(str(row["env"]),     color=_score_color(row["env"])),
                _mono(str(row["social"]),  color=_score_color(row["social"])),
                _mono(str(row["gov"]),     color=_score_color(row["gov"])),
                badge(row["cdp"], color=cdp_color),
                _yn_badge(row["djsi"]),
                _mono(f"{row['cbds']}/100", color=cbds_color),
            ])
        wsj_market_table(
            headers=["Company", "Overall", "Env", "Social", "Gov", "CDP", "DJSI", "Carbon Disclosure"],
            rows=rows,
        )
        _src = DataSource.demo("MSCI ESG / CDP / DJSI (synthetic)")
        st.markdown(source_footer([_src]), unsafe_allow_html=True)
    except Exception:
        logger.exception("ESG scores render error")
        st.error("Could not render ESG score comparison.")


# ── Speed optimization ──────────────────────────────────────────────────────

def _render_speed_optimization() -> None:
    try:
        section_header(
            "Speed Optimization — Slow Steaming Analysis",
            subtitle="Reducing speed 10% cuts fuel consumption ~27% but reduces effective capacity; full trade-off breakdown",
        )
        col_tbl, col_chart = st.columns([2, 3], gap="large")

        with col_tbl:
            rows = []
            for row in _SPEED_TABLE:
                spd_color  = C_HIGH if row["speed_kn"]       <= 16 else (C_MOD if row["speed_kn"]       <= 20 else C_LOW)
                opex_color = C_HIGH if row["daily_opex_usd"] < 40000 else (C_MOD if row["daily_opex_usd"] < 70000 else C_LOW)
                cap_color  = C_HIGH if row["capacity_util_pct"] >= 90 else (C_MOD if row["capacity_util_pct"] >= 75 else C_LOW)
                rows.append([
                    _mono(str(row["speed_kn"]), color=spd_color),
                    _mono(str(row["fuel_tpd"]), color=C_TEXT2),
                    _mono(f"${row['daily_opex_usd']:,}", color=opex_color),
                    _mono(f"{row['capacity_util_pct']}%", color=cap_color),
                    _mono(str(row["co2_tpd"]), color=C_TEXT2),
                ])
            wsj_market_table(
                headers=["Speed (kn)", "Fuel (t/day)", "Opex ($/day)", "Capacity %", "CO₂ (t/day)"],
                rows=rows,
            )

        with col_chart:
            try:
                speeds = [r["speed_kn"] for r in _SPEED_TABLE]
                fuels  = [r["fuel_tpd"] for r in _SPEED_TABLE]
                opex   = [r["daily_opex_usd"] / 1000 for r in _SPEED_TABLE]
                co2s   = [r["co2_tpd"] for r in _SPEED_TABLE]
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=speeds, y=fuels, name="Fuel (t/day)",
                    mode="lines+markers",
                    line=dict(color=C_ACCENT, width=2), marker=dict(size=6),
                    hovertemplate="Speed: %{x}kn<br>Fuel: %{y}t/day<extra></extra>",
                ))
                fig.add_trace(go.Scatter(
                    x=speeds, y=opex, name="Opex ($k/day)",
                    mode="lines+markers",
                    line=dict(color=C_MOD, width=2, dash="dot"), marker=dict(size=6),
                    yaxis="y2",
                    hovertemplate="Speed: %{x}kn<br>Opex: $%{y:.0f}k/day<extra></extra>",
                ))
                fig.add_trace(go.Scatter(
                    x=speeds, y=co2s, name="CO₂ (t/day)",
                    mode="lines+markers",
                    line=dict(color=C_LOW, width=2, dash="dash"), marker=dict(size=6),
                    hovertemplate="Speed: %{x}kn<br>CO₂: %{y}t/day<extra></extra>",
                ))
                apply_dark_layout(
                    fig,
                    title="Speed vs Fuel / Opex / CO₂ Trade-off",
                    height=340,
                    xaxis=dict(title="Speed (kn)", autorange="reversed"),
                    yaxis=dict(title="Fuel / CO₂"),
                    yaxis2=dict(title="Opex ($k)", overlaying="y", side="right", color=C_MOD, showgrid=False),
                    legend=dict(orientation="h", y=-0.2),
                )
                st.plotly_chart(
                    fig, use_container_width=True,
                    config={"displayModeBar": False},
                    key="sustain_speed_tradeoff",
                )
            except Exception:
                logger.exception("Speed chart error")
                st.warning("Speed optimization chart unavailable.")

        # Insight cards
        metric_card_row(
            [
                {"label": "Fuel saving (10% speed cut)",  "value": "−27%",    "accent": C_HIGH,
                 "sublabel": "cubic law of fuel vs speed"},
                {"label": "Effective capacity loss",       "value": "−8–12%",  "accent": C_MOD,
                 "sublabel": "due to longer voyage times"},
                {"label": "Optimal slow-steam band",       "value": "16–18 kn", "accent": C_ACCENT,
                 "sublabel": "balances cost and capacity"},
            ],
            columns=3,
        )
        _src = DataSource.demo("MEPC / Vessel Performance (synthetic)")
        st.markdown(source_footer([_src]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Speed optimization render error")
        st.error("Could not render speed optimization section.")


# ── Main render ─────────────────────────────────────────────────────────────

def render(port_results=None, insights=None) -> None:
    """Render the full Sustainability & ESG intelligence tab."""
    try:
        page_header(
            title="Shipping ESG & Sustainability Intelligence",
            subtitle="IMO 2030/2050 compliance · EU ETS · Green fuel transition · ESG ratings · Speed optimization",
            icon="🌱",
        )
    except Exception:
        logger.exception("Tab header render error")

    _render_hero_kpis()
    section_divider("IMO Compliance")
    _render_cii_tracker()
    section_divider("Route Carbon Intensity")
    _render_route_carbon()
    section_divider("Green Fuel Transition")
    _render_green_fuel()
    section_divider("EU ETS Impact")
    _render_eu_ets()
    section_divider("ESG Scores")
    _render_esg_scores()
    section_divider("Speed Optimization")
    _render_speed_optimization()
