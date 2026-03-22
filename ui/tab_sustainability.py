"""Sustainability tab — shipping ESG and sustainability intelligence."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ── Colour palette ────────────────────────────────────────────────────────────
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

_CII_COLORS = {"A": "#10b981", "B": "#34d399", "C": "#f59e0b", "D": "#f97316", "E": "#ef4444"}

# ── Static datasets ───────────────────────────────────────────────────────────

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

# ── Helpers ───────────────────────────────────────────────────────────────────

def _card_css() -> str:
    return (
        f"background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;"
        f"padding:20px 24px;margin-bottom:16px;"
    )

def _section_header(title: str, subtitle: str = "") -> None:
    sub_html = f"<p style='color:{C_TEXT2};font-size:13px;margin:4px 0 0 0;'>{subtitle}</p>" if subtitle else ""
    st.markdown(
        f"<div style='border-left:3px solid {C_ACCENT};padding-left:14px;margin:28px 0 16px 0;'>"
        f"<h3 style='color:{C_TEXT};font-size:18px;font-weight:700;margin:0;'>{title}</h3>"
        f"{sub_html}</div>",
        unsafe_allow_html=True,
    )

def _kpi_card(label: str, value: str, delta: str = "", color: str = C_TEXT, icon: str = "") -> str:
    delta_html = (
        f"<div style='color:{color};font-size:12px;margin-top:4px;'>{delta}</div>"
        if delta else ""
    )
    return (
        f"<div style='{_card_css()}'>"
        f"<div style='color:{C_TEXT3};font-size:11px;font-weight:600;text-transform:uppercase;"
        f"letter-spacing:0.08em;'>{icon} {label}</div>"
        f"<div style='color:{C_TEXT};font-size:26px;font-weight:800;margin-top:8px;'>{value}</div>"
        f"{delta_html}</div>"
    )

def _badge(text: str, color: str) -> str:
    return (
        f"<span style='background:{color}22;color:{color};border:1px solid {color}44;"
        f"border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700;'>{text}</span>"
    )

def _yn(val: bool) -> str:
    return _badge("YES", C_HIGH) if val else _badge("NO", C_LOW)

# ── Section renderers ─────────────────────────────────────────────────────────

def _render_hero_kpis() -> None:
    try:
        _section_header(
            "Sustainability Dashboard",
            "Real-time shipping ESG intelligence — IMO, EU ETS, and green fuel metrics",
        )
        c1, c2, c3, c4, c5 = st.columns(5)
        kpis = [
            (c1, "Global Shipping CO₂", "812M t/yr", "▲ +1.2% YoY", C_LOW, "🌍"),
            (c2, "Carbon Intensity (CII)", "8.9 gCO₂/t-nm", "▼ −4.1% vs 2022", C_HIGH, "📊"),
            (c3, "Fleet IMO-2030 Ready", "23.4%", "Target: 100% by 2030", C_MOD, "⚓"),
            (c4, "EU ETS Carbon Price", "€63/t CO₂", "▼ −8% MTD", C_MOD, "🇪🇺"),
            (c5, "Green Fuel Adoption", "7.4%", "▲ +1.9pp YoY", C_HIGH, "⚡"),
        ]
        for col, label, value, delta, color, icon in kpis:
            with col:
                st.markdown(_kpi_card(label, value, delta, color, icon), unsafe_allow_html=True)
    except Exception:
        logger.exception("Hero KPIs render error")
        st.error("Could not render sustainability dashboard KPIs.")


def _render_cii_tracker() -> None:
    try:
        _section_header(
            "IMO 2030/2050 Compliance Tracker",
            "CII ratings, EEOI, and eco-fleet progress for 12 major carriers",
        )
        header_cols = st.columns([2, 1, 1, 1, 1, 1, 3])
        headers = ["CARRIER", "CII RATING", "EEOI", "ECO SHIPS %", "LNG DUAL-FUEL %", "ON TRACK 2030?", "KEY ACTIONS"]
        for col, h in zip(header_cols, headers):
            col.markdown(
                f"<div style='color:{C_TEXT3};font-size:10px;font-weight:700;"
                f"text-transform:uppercase;letter-spacing:0.07em;padding-bottom:6px;"
                f"border-bottom:1px solid {C_BORDER};'>{h}</div>",
                unsafe_allow_html=True,
            )
        for row in _CARRIERS:
            cii_color = _CII_COLORS.get(row["cii"], C_TEXT2)
            track_color = C_HIGH if row["on_track"] else C_LOW
            track_text  = "✔ Yes" if row["on_track"] else "✘ No"
            eeoi_color  = C_HIGH if row["eeoi"] < 9.5 else (C_MOD if row["eeoi"] < 11.5 else C_LOW)
            eco_color   = C_HIGH if row["eco_pct"] >= 28 else (C_MOD if row["eco_pct"] >= 18 else C_LOW)
            r1, r2, r3, r4, r5, r6, r7 = st.columns([2, 1, 1, 1, 1, 1, 3])
            r1.markdown(
                f"<div style='color:{C_TEXT};font-size:13px;font-weight:600;"
                f"padding:6px 0;'>{row['carrier']}</div>",
                unsafe_allow_html=True,
            )
            r2.markdown(
                f"<div style='padding:6px 0;'>"
                f"<span style='background:{cii_color}33;color:{cii_color};border:1px solid {cii_color}66;"
                f"border-radius:4px;padding:2px 10px;font-size:13px;font-weight:800;'>"
                f"{row['cii']}</span></div>",
                unsafe_allow_html=True,
            )
            r3.markdown(
                f"<div style='color:{eeoi_color};font-size:13px;font-weight:600;padding:6px 0;'>"
                f"{row['eeoi']}</div>",
                unsafe_allow_html=True,
            )
            r4.markdown(
                f"<div style='color:{eco_color};font-size:13px;font-weight:600;padding:6px 0;'>"
                f"{row['eco_pct']}%</div>",
                unsafe_allow_html=True,
            )
            r5.markdown(
                f"<div style='color:{C_TEXT2};font-size:13px;padding:6px 0;'>{row['lng_pct']}%</div>",
                unsafe_allow_html=True,
            )
            r6.markdown(
                f"<div style='color:{track_color};font-size:12px;font-weight:700;padding:6px 0;'>"
                f"{track_text}</div>",
                unsafe_allow_html=True,
            )
            r7.markdown(
                f"<div style='color:{C_TEXT2};font-size:12px;padding:6px 0;'>{row['actions']}</div>",
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("CII tracker render error")
        st.error("Could not render compliance tracker.")


def _render_route_carbon() -> None:
    try:
        _section_header(
            "Carbon Intensity by Route",
            "CO₂ per TEU-km vs 2008 baseline and IMO 2030 target (−40% vs 2008)",
        )
        hcols = st.columns([2.2, 2, 1.2, 1.2, 1.2, 1.2])
        for col, h in zip(hcols, ["ROUTE", "VESSEL CLASS", "CO₂/TEU-KM (g)", "VS 2008", "VS IMO TARGET", "TREND"]):
            col.markdown(
                f"<div style='color:{C_TEXT3};font-size:10px;font-weight:700;"
                f"text-transform:uppercase;letter-spacing:0.07em;padding-bottom:6px;"
                f"border-bottom:1px solid {C_BORDER};'>{h}</div>",
                unsafe_allow_html=True,
            )
        for row in _ROUTES:
            vs08_color  = C_HIGH if row["vs_2008"] <= -30 else (C_MOD if row["vs_2008"] <= -20 else C_LOW)
            vsimo_color = C_HIGH if row["vs_imo"] <= 0 else (C_MOD if row["vs_imo"] <= 10 else C_LOW)
            trend_color = C_HIGH if row["trend"] == "Improving" else (C_MOD if row["trend"] == "Stable" else C_LOW)
            r1, r2, r3, r4, r5, r6 = st.columns([2.2, 2, 1.2, 1.2, 1.2, 1.2])
            r1.markdown(
                f"<div style='color:{C_TEXT};font-size:13px;font-weight:600;padding:5px 0;'>"
                f"{row['route']}</div>",
                unsafe_allow_html=True,
            )
            r2.markdown(
                f"<div style='color:{C_TEXT2};font-size:12px;padding:5px 0;'>{row['vessel']}</div>",
                unsafe_allow_html=True,
            )
            r3.markdown(
                f"<div style='color:{C_TEXT};font-size:13px;font-weight:600;padding:5px 0;'>"
                f"{row['co2_teu_km']:.4f}</div>",
                unsafe_allow_html=True,
            )
            sign08  = "+" if row["vs_2008"] > 0 else ""
            signimo = "+" if row["vs_imo"] > 0 else ""
            r4.markdown(
                f"<div style='color:{vs08_color};font-size:13px;font-weight:600;padding:5px 0;'>"
                f"{sign08}{row['vs_2008']}%</div>",
                unsafe_allow_html=True,
            )
            r5.markdown(
                f"<div style='color:{vsimo_color};font-size:13px;font-weight:600;padding:5px 0;'>"
                f"{signimo}{row['vs_imo']}%</div>",
                unsafe_allow_html=True,
            )
            r6.markdown(
                f"<div style='color:{trend_color};font-size:12px;font-weight:700;padding:5px 0;'>"
                f"{row['trend']}</div>",
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("Route carbon render error")
        st.error("Could not render carbon intensity by route.")


def _render_green_fuel() -> None:
    try:
        _section_header(
            "Green Fuel Transition",
            "Alternative fuel adoption, newbuild orderbook mix, cost premiums, and port infrastructure",
        )
        col_pie, col_bar = st.columns(2)

        with col_pie:
            try:
                labels  = ["VLSFO", "LNG", "Biodiesel", "Methanol", "Ammonia"]
                values  = [92.6, 4.5, 2.0, 0.8, 0.1]
                colors  = ["#64748b", "#3b82f6", "#10b981", "#8b5cf6", "#06b6d4"]
                fig_pie = go.Figure(go.Pie(
                    labels=labels, values=values, hole=0.55,
                    marker=dict(colors=colors, line=dict(color=C_BG, width=2)),
                    textinfo="label+percent",
                    textfont=dict(color=C_TEXT, size=11),
                    hovertemplate="<b>%{label}</b><br>Share: %{percent}<extra></extra>",
                ))
                fig_pie.update_layout(
                    title=dict(text="Current Fleet Fuel Mix", font=dict(color=C_TEXT, size=14), x=0.02),
                    paper_bgcolor=C_CARD, plot_bgcolor=C_CARD,
                    font=dict(color=C_TEXT2),
                    showlegend=False, margin=dict(t=50, b=20, l=20, r=20),
                    height=300,
                )
                st.plotly_chart(fig_pie, use_container_width=True)
            except Exception:
                logger.exception("Fuel pie chart error")
                st.warning("Fuel mix chart unavailable.")

        with col_bar:
            try:
                vessel_classes = ["ULCVs", "VLCVs", "Panamaxes", "Feeders", "Bulk", "Tankers"]
                conv    = [42, 38, 61, 88, 71, 65]
                dual    = [58, 62, 39, 12, 29, 35]
                fig_bar = go.Figure()
                fig_bar.add_trace(go.Bar(
                    name="Conventional Fuel", x=vessel_classes, y=conv,
                    marker_color="#64748b",
                    hovertemplate="<b>%{x}</b> — Conventional: %{y}%<extra></extra>",
                ))
                fig_bar.add_trace(go.Bar(
                    name="Dual-Fuel / Alt-Fuel", x=vessel_classes, y=dual,
                    marker_color=C_ACCENT,
                    hovertemplate="<b>%{x}</b> — Alt-Fuel: %{y}%<extra></extra>",
                ))
                fig_bar.update_layout(
                    title=dict(text="Newbuild Orderbook by Fuel Type (%)", font=dict(color=C_TEXT, size=14), x=0.02),
                    barmode="stack", paper_bgcolor=C_CARD, plot_bgcolor=C_CARD,
                    font=dict(color=C_TEXT2), legend=dict(font=dict(color=C_TEXT2), bgcolor=C_CARD),
                    xaxis=dict(gridcolor=C_BORDER, color=C_TEXT2),
                    yaxis=dict(gridcolor=C_BORDER, color=C_TEXT2, ticksuffix="%"),
                    margin=dict(t=50, b=20, l=40, r=20), height=300,
                )
                st.plotly_chart(fig_bar, use_container_width=True)
            except Exception:
                logger.exception("Fuel bar chart error")
                st.warning("Newbuild orderbook chart unavailable.")

        st.markdown(
            f"<div style='{_card_css()}'>"
            f"<div style='color:{C_TEXT};font-size:14px;font-weight:700;margin-bottom:12px;'>Green Fuel Cost Premium vs VLSFO (per TEU)</div>"
            f"<div style='display:grid;grid-template-columns:repeat(4,1fr);gap:12px;'>"
            f"<div style='text-align:center;'>"
            f"<div style='color:{C_TEXT3};font-size:11px;text-transform:uppercase;'>LNG</div>"
            f"<div style='color:{C_ACCENT};font-size:20px;font-weight:800;'>+$18–28</div>"
            f"<div style='color:{C_TEXT3};font-size:11px;'>per TEU Asia–EU</div></div>"
            f"<div style='text-align:center;'>"
            f"<div style='color:{C_TEXT3};font-size:11px;text-transform:uppercase;'>Bio-Methanol</div>"
            f"<div style='color:{C_MOD};font-size:20px;font-weight:800;'>+$42–61</div>"
            f"<div style='color:{C_TEXT3};font-size:11px;'>per TEU Asia–EU</div></div>"
            f"<div style='text-align:center;'>"
            f"<div style='color:{C_TEXT3};font-size:11px;text-transform:uppercase;'>Green Ammonia</div>"
            f"<div style='color:{C_LOW};font-size:20px;font-weight:800;'>+$90–140</div>"
            f"<div style='color:{C_TEXT3};font-size:11px;'>per TEU Asia–EU</div></div>"
            f"<div style='text-align:center;'>"
            f"<div style='color:{C_TEXT3};font-size:11px;text-transform:uppercase;'>Green H₂</div>"
            f"<div style='color:{C_LOW};font-size:20px;font-weight:800;'>+$110–180</div>"
            f"<div style='color:{C_TEXT3};font-size:11px;'>per TEU Asia–EU</div></div>"
            f"</div></div>",
            unsafe_allow_html=True,
        )

        st.markdown(
            f"<div style='color:{C_TEXT};font-size:13px;font-weight:700;margin:16px 0 8px 0;'>"
            f"Port Green Fuel Infrastructure Readiness</div>",
            unsafe_allow_html=True,
        )
        hcols = st.columns([1.8, 1, 1.4, 1.2, 1.4])
        for col, h in zip(hcols, ["PORT", "LNG STATIONS", "METHANOL TERMINALS", "AMMONIA READY", "GREEN SHORE POWER"]):
            col.markdown(
                f"<div style='color:{C_TEXT3};font-size:10px;font-weight:700;"
                f"text-transform:uppercase;letter-spacing:0.07em;padding-bottom:6px;"
                f"border-bottom:1px solid {C_BORDER};'>{h}</div>",
                unsafe_allow_html=True,
            )
        for row in _PORT_INFRA:
            lng_color = C_HIGH if row["lng_stations"] >= 7 else (C_MOD if row["lng_stations"] >= 4 else C_LOW)
            r1, r2, r3, r4, r5 = st.columns([1.8, 1, 1.4, 1.2, 1.4])
            r1.markdown(
                f"<div style='color:{C_TEXT};font-size:13px;font-weight:600;padding:5px 0;'>"
                f"{row['port']}</div>",
                unsafe_allow_html=True,
            )
            r2.markdown(
                f"<div style='color:{lng_color};font-size:13px;font-weight:700;padding:5px 0;'>"
                f"{row['lng_stations']}</div>",
                unsafe_allow_html=True,
            )
            r3.markdown(
                f"<div style='color:{C_TEXT2};font-size:13px;padding:5px 0;'>"
                f"{row['methanol_terminals']}</div>",
                unsafe_allow_html=True,
            )
            r4.markdown(f"<div style='padding:5px 0;'>{_yn(row['ammonia_ready'])}</div>", unsafe_allow_html=True)
            r5.markdown(f"<div style='padding:5px 0;'>{_yn(row['green_shore_power'])}</div>", unsafe_allow_html=True)
    except Exception:
        logger.exception("Green fuel section render error")
        st.error("Could not render green fuel transition section.")


def _render_eu_ets() -> None:
    try:
        _section_header(
            "EU ETS Impact Analysis",
            "Shipping entered EU Emissions Trading System Jan 2024 — cost exposure and compliance implications",
        )

        col_chart, col_calc = st.columns([3, 2])

        with col_chart:
            try:
                months = [
                    "Jan-23","Apr-23","Jul-23","Oct-23",
                    "Jan-24","Apr-24","Jul-24","Oct-24",
                    "Jan-25","Apr-25","Jul-25","Oct-25",
                    "Jan-26",
                ]
                prices = [93, 87, 91, 72, 58, 63, 67, 59, 62, 70, 65, 61, 63]
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=months, y=prices, mode="lines+markers",
                    line=dict(color=C_ACCENT, width=2),
                    marker=dict(size=5, color=C_ACCENT),
                    fill="tozeroy",
                    fillcolor=f"rgba(59,130,246,0.1)",
                    hovertemplate="<b>%{x}</b><br>€%{y}/tonne CO₂<extra></extra>",
                    name="EU ETS Price",
                ))
                fig.add_vline(
                    x="Jan-24", line_dash="dash", line_color=C_MOD, line_width=1.5,
                    annotation_text="Shipping enters EU ETS",
                    annotation_font=dict(color=C_MOD, size=11),
                    annotation_position="top right",
                )
                fig.update_layout(
                    title=dict(text="EU Carbon Price (€/tonne CO₂)", font=dict(color=C_TEXT, size=14), x=0.02),
                    paper_bgcolor=C_CARD, plot_bgcolor=C_CARD,
                    font=dict(color=C_TEXT2),
                    xaxis=dict(gridcolor=C_BORDER, color=C_TEXT2),
                    yaxis=dict(gridcolor=C_BORDER, color=C_TEXT2, tickprefix="€"),
                    margin=dict(t=50, b=30, l=50, r=20), height=280,
                    showlegend=False,
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                logger.exception("EU ETS chart error")
                st.warning("EU ETS price chart unavailable.")

        with col_calc:
            st.markdown(
                f"<div style='{_card_css()}'>"
                f"<div style='color:{C_TEXT};font-size:13px;font-weight:700;margin-bottom:10px;'>"
                f"ETS Cost Estimator</div>",
                unsafe_allow_html=True,
            )
            distance_nm  = st.number_input("Route distance (nm)", min_value=100, max_value=25000, value=11200, step=100)
            vessel_teu   = st.number_input("Vessel capacity (TEU)", min_value=500, max_value=24000, value=15000, step=500)
            load_factor  = st.slider("Load factor (%)", min_value=50, max_value=100, value=85)
            carbon_price = st.number_input("Carbon price (€/tonne)", min_value=20, max_value=150, value=63)

            try:
                fuel_cons_mt   = (distance_nm / 10.0) * 0.14
                co2_mt         = fuel_cons_mt * 3.114
                ets_eligible   = co2_mt * 0.5
                ets_cost_eur   = ets_eligible * carbon_price
                teu_carried    = vessel_teu * (load_factor / 100)
                cost_per_teu   = ets_cost_eur / teu_carried if teu_carried else 0
                st.markdown(
                    f"<div style='margin-top:10px;'>"
                    f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:8px;'>"
                    f"<div style='background:{C_SURFACE};border-radius:8px;padding:10px;'>"
                    f"<div style='color:{C_TEXT3};font-size:10px;'>Est. CO₂ emitted</div>"
                    f"<div style='color:{C_TEXT};font-size:16px;font-weight:700;'>{co2_mt:.0f} t</div></div>"
                    f"<div style='background:{C_SURFACE};border-radius:8px;padding:10px;'>"
                    f"<div style='color:{C_TEXT3};font-size:10px;'>ETS-eligible (50%)</div>"
                    f"<div style='color:{C_TEXT};font-size:16px;font-weight:700;'>{ets_eligible:.0f} t</div></div>"
                    f"<div style='background:{C_SURFACE};border-radius:8px;padding:10px;'>"
                    f"<div style='color:{C_TEXT3};font-size:10px;'>Total ETS cost</div>"
                    f"<div style='color:{C_MOD};font-size:16px;font-weight:700;'>€{ets_cost_eur:,.0f}</div></div>"
                    f"<div style='background:{C_SURFACE};border-radius:8px;padding:10px;'>"
                    f"<div style='color:{C_TEXT3};font-size:10px;'>Cost per TEU</div>"
                    f"<div style='color:{C_ACCENT};font-size:16px;font-weight:700;'>€{cost_per_teu:.1f}</div></div>"
                    f"</div></div>",
                    unsafe_allow_html=True,
                )
            except Exception:
                logger.exception("ETS calculator error")
                st.warning("Calculation error.")

        st.markdown(
            f"<div style='color:{C_TEXT};font-size:13px;font-weight:700;margin:16px 0 8px 0;'>"
            f"Carrier EU ETS Exposure Ranking</div>",
            unsafe_allow_html=True,
        )
        hcols = st.columns([2, 1.2, 1.4, 2])
        for col, h in zip(hcols, ["CARRIER", "EU REVENUE %", "CARBON INTENSITY", "EST. ANNUAL ETS COST"]):
            col.markdown(
                f"<div style='color:{C_TEXT3};font-size:10px;font-weight:700;"
                f"text-transform:uppercase;letter-spacing:0.07em;padding-bottom:6px;"
                f"border-bottom:1px solid {C_BORDER};'>{h}</div>",
                unsafe_allow_html=True,
            )
        sorted_ets = sorted(_EU_EXPOSURE, key=lambda r: r["est_ets_cost_mUSD"], reverse=True)
        for row in sorted_ets:
            rev_color  = C_LOW if row["eu_rev_pct"] >= 40 else (C_MOD if row["eu_rev_pct"] >= 28 else C_HIGH)
            cost_color = C_LOW if row["est_ets_cost_mUSD"] >= 400 else (C_MOD if row["est_ets_cost_mUSD"] >= 200 else C_HIGH)
            r1, r2, r3, r4 = st.columns([2, 1.2, 1.4, 2])
            r1.markdown(
                f"<div style='color:{C_TEXT};font-size:13px;font-weight:600;padding:5px 0;'>"
                f"{row['carrier']}</div>",
                unsafe_allow_html=True,
            )
            r2.markdown(
                f"<div style='color:{rev_color};font-size:13px;font-weight:700;padding:5px 0;'>"
                f"{row['eu_rev_pct']}%</div>",
                unsafe_allow_html=True,
            )
            r3.markdown(
                f"<div style='color:{C_TEXT2};font-size:13px;padding:5px 0;'>"
                f"{row['carbon_int']} gCO₂/t-nm</div>",
                unsafe_allow_html=True,
            )
            r4.markdown(
                f"<div style='color:{cost_color};font-size:13px;font-weight:700;padding:5px 0;'>"
                f"${row['est_ets_cost_mUSD']}M USD equiv.</div>",
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("EU ETS section render error")
        st.error("Could not render EU ETS section.")


def _render_esg_scores() -> None:
    try:
        _section_header(
            "ESG Score Comparison",
            "Aggregated ESG ratings, CDP scores, and DJSI inclusion for listed shipping companies",
        )
        hcols = st.columns([2.5, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 1])
        for col, h in zip(hcols, ["COMPANY", "OVERALL", "ENV", "SOCIAL", "GOV", "CDP", "DJSI", "CARBON DISCLOSURE"]):
            col.markdown(
                f"<div style='color:{C_TEXT3};font-size:10px;font-weight:700;"
                f"text-transform:uppercase;letter-spacing:0.07em;padding-bottom:6px;"
                f"border-bottom:1px solid {C_BORDER};'>{h}</div>",
                unsafe_allow_html=True,
            )
        for row in sorted(_ESG_SCORES, key=lambda r: r["overall"], reverse=True):
            def score_color(s: int) -> str:
                return C_HIGH if s >= 70 else (C_MOD if s >= 55 else C_LOW)
            r1, r2, r3, r4, r5, r6, r7, r8 = st.columns([2.5, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 1])
            r1.markdown(
                f"<div style='color:{C_TEXT};font-size:12px;font-weight:600;padding:5px 0;'>"
                f"{row['company']}</div>",
                unsafe_allow_html=True,
            )
            r2.markdown(
                f"<div style='color:{score_color(row['overall'])};font-size:13px;"
                f"font-weight:800;padding:5px 0;'>{row['overall']}</div>",
                unsafe_allow_html=True,
            )
            r3.markdown(
                f"<div style='color:{score_color(row['env'])};font-size:12px;padding:5px 0;'>"
                f"{row['env']}</div>",
                unsafe_allow_html=True,
            )
            r4.markdown(
                f"<div style='color:{score_color(row['social'])};font-size:12px;padding:5px 0;'>"
                f"{row['social']}</div>",
                unsafe_allow_html=True,
            )
            r5.markdown(
                f"<div style='color:{score_color(row['gov'])};font-size:12px;padding:5px 0;'>"
                f"{row['gov']}</div>",
                unsafe_allow_html=True,
            )
            cdp_color = C_HIGH if row["cdp"].startswith("A") else (C_MOD if row["cdp"].startswith("B") else C_LOW)
            r6.markdown(
                f"<div style='color:{cdp_color};font-size:12px;font-weight:700;padding:5px 0;'>"
                f"{row['cdp']}</div>",
                unsafe_allow_html=True,
            )
            r7.markdown(f"<div style='padding:5px 0;'>{_yn(row['djsi'])}</div>", unsafe_allow_html=True)
            cbds_color = C_HIGH if row["cbds"] >= 75 else (C_MOD if row["cbds"] >= 55 else C_LOW)
            r8.markdown(
                f"<div style='color:{cbds_color};font-size:12px;font-weight:700;padding:5px 0;'>"
                f"{row['cbds']}/100</div>",
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("ESG scores render error")
        st.error("Could not render ESG score comparison.")


def _render_speed_optimization() -> None:
    try:
        _section_header(
            "Speed Optimization — Slow Steaming Analysis",
            "Reducing speed 10% cuts fuel consumption ~27% but reduces effective capacity; full trade-off breakdown",
        )

        col_tbl, col_chart = st.columns([2, 3])

        with col_tbl:
            hcols = st.columns([1, 1, 1.4, 1.2, 1])
            for col, h in zip(hcols, ["SPEED (kn)", "FUEL (t/day)", "OPEX ($/day)", "CAPACITY %", "CO₂ (t/day)"]):
                col.markdown(
                    f"<div style='color:{C_TEXT3};font-size:10px;font-weight:700;"
                    f"text-transform:uppercase;letter-spacing:0.07em;padding-bottom:6px;"
                    f"border-bottom:1px solid {C_BORDER};'>{h}</div>",
                    unsafe_allow_html=True,
                )
            base_fuel = _SPEED_TABLE[0]["fuel_tpd"]
            for row in _SPEED_TABLE:
                pct_saving = (1 - row["fuel_tpd"] / base_fuel) * 100
                spd_color  = C_HIGH if row["speed_kn"] <= 16 else (C_MOD if row["speed_kn"] <= 20 else C_LOW)
                r1, r2, r3, r4, r5 = st.columns([1, 1, 1.4, 1.2, 1])
                r1.markdown(
                    f"<div style='color:{spd_color};font-size:12px;font-weight:700;padding:4px 0;'>"
                    f"{row['speed_kn']}</div>",
                    unsafe_allow_html=True,
                )
                r2.markdown(
                    f"<div style='color:{C_TEXT2};font-size:12px;padding:4px 0;'>{row['fuel_tpd']}</div>",
                    unsafe_allow_html=True,
                )
                opex_color = C_HIGH if row["daily_opex_usd"] < 40000 else (C_MOD if row["daily_opex_usd"] < 70000 else C_LOW)
                r3.markdown(
                    f"<div style='color:{opex_color};font-size:12px;font-weight:600;padding:4px 0;'>"
                    f"${row['daily_opex_usd']:,}</div>",
                    unsafe_allow_html=True,
                )
                cap_color = C_HIGH if row["capacity_util_pct"] >= 90 else (C_MOD if row["capacity_util_pct"] >= 75 else C_LOW)
                r4.markdown(
                    f"<div style='color:{cap_color};font-size:12px;padding:4px 0;'>"
                    f"{row['capacity_util_pct']}%</div>",
                    unsafe_allow_html=True,
                )
                r5.markdown(
                    f"<div style='color:{C_TEXT2};font-size:12px;padding:4px 0;'>{row['co2_tpd']}</div>",
                    unsafe_allow_html=True,
                )

        with col_chart:
            try:
                speeds  = [r["speed_kn"] for r in _SPEED_TABLE]
                fuels   = [r["fuel_tpd"] for r in _SPEED_TABLE]
                opex    = [r["daily_opex_usd"] / 1000 for r in _SPEED_TABLE]
                co2s    = [r["co2_tpd"] for r in _SPEED_TABLE]
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=speeds, y=fuels, name="Fuel (t/day)",
                    mode="lines+markers",
                    line=dict(color=C_ACCENT, width=2),
                    marker=dict(size=6),
                    hovertemplate="Speed: %{x}kn<br>Fuel: %{y}t/day<extra></extra>",
                ))
                fig.add_trace(go.Scatter(
                    x=speeds, y=opex, name="Opex ($k/day)",
                    mode="lines+markers",
                    line=dict(color=C_MOD, width=2, dash="dot"),
                    marker=dict(size=6),
                    yaxis="y2",
                    hovertemplate="Speed: %{x}kn<br>Opex: $%{y:.0f}k/day<extra></extra>",
                ))
                fig.add_trace(go.Scatter(
                    x=speeds, y=co2s, name="CO₂ (t/day)",
                    mode="lines+markers",
                    line=dict(color=C_LOW, width=2, dash="dash"),
                    marker=dict(size=6),
                    hovertemplate="Speed: %{x}kn<br>CO₂: %{y}t/day<extra></extra>",
                ))
                fig.update_layout(
                    title=dict(text="Speed vs Fuel / Opex / CO₂ Trade-off", font=dict(color=C_TEXT, size=14), x=0.02),
                    paper_bgcolor=C_CARD, plot_bgcolor=C_CARD,
                    font=dict(color=C_TEXT2),
                    xaxis=dict(title="Speed (kn)", gridcolor=C_BORDER, color=C_TEXT2, autorange="reversed"),
                    yaxis=dict(title="Fuel / CO₂", gridcolor=C_BORDER, color=C_TEXT2),
                    yaxis2=dict(title="Opex ($k)", overlaying="y", side="right", color=C_MOD, showgrid=False),
                    legend=dict(font=dict(color=C_TEXT2), bgcolor=C_CARD, orientation="h", y=-0.2),
                    margin=dict(t=50, b=60, l=50, r=60), height=340,
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                logger.exception("Speed chart error")
                st.warning("Speed optimization chart unavailable.")

        st.markdown(
            f"<div style='{_card_css()}'>"
            f"<div style='color:{C_TEXT};font-size:13px;font-weight:700;margin-bottom:10px;'>Key Slow-Steaming Insights</div>"
            f"<div style='display:grid;grid-template-columns:repeat(3,1fr);gap:12px;'>"
            f"<div style='background:{C_SURFACE};border-radius:8px;padding:12px;border-left:3px solid {C_HIGH};'>"
            f"<div style='color:{C_HIGH};font-size:16px;font-weight:800;'>−27%</div>"
            f"<div style='color:{C_TEXT2};font-size:12px;'>Fuel saving from 10% speed reduction (cubic law)</div></div>"
            f"<div style='background:{C_SURFACE};border-radius:8px;padding:12px;border-left:3px solid {C_MOD};'>"
            f"<div style='color:{C_MOD};font-size:16px;font-weight:800;'>−8–12%</div>"
            f"<div style='color:{C_TEXT2};font-size:12px;'>Effective capacity reduction due to longer voyage times</div></div>"
            f"<div style='background:{C_SURFACE};border-radius:8px;padding:12px;border-left:3px solid {C_ACCENT};'>"
            f"<div style='color:{C_ACCENT};font-size:16px;font-weight:800;'>16–18 kn</div>"
            f"<div style='color:{C_TEXT2};font-size:12px;'>Optimal slow-steam band balancing cost and capacity</div></div>"
            f"</div></div>",
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Speed optimization render error")
        st.error("Could not render speed optimization section.")


# ── Main entry point ──────────────────────────────────────────────────────────

def render(port_results=None, insights=None) -> None:
    """Render the full Sustainability & ESG intelligence tab."""
    try:
        st.markdown(
            f"<div style='background:linear-gradient(135deg,{C_CARD} 0%,{C_BG} 100%);"
            f"border:1px solid {C_BORDER};border-radius:14px;padding:24px 28px;margin-bottom:24px;'>"
            f"<h2 style='color:{C_TEXT};font-size:22px;font-weight:800;margin:0 0 6px 0;'>"
            f"Shipping ESG &amp; Sustainability Intelligence</h2>"
            f"<p style='color:{C_TEXT2};font-size:13px;margin:0;'>"
            f"IMO 2030/2050 compliance · EU ETS · Green fuel transition · ESG ratings · Speed optimization"
            f"</p></div>",
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Tab header render error")

    _render_hero_kpis()
    _render_cii_tracker()
    _render_route_carbon()
    _render_green_fuel()
    _render_eu_ets()
    _render_esg_scores()
    _render_speed_optimization()
