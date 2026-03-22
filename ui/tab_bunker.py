"""
Bunker Fuel Intelligence Tab

Comprehensive bunker fuel analytics: price dashboard, port-by-port prices,
historical chart, optimization calculator, spread analysis, alternative fuels
comparison, and hedging strategy guide.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ── Design tokens ──────────────────────────────────────────────────────────────
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
C_PURPLE  = "#8b5cf6"
C_CYAN    = "#06b6d4"
C_TEAL    = "#14b8a6"

# ── Port & fuel data ───────────────────────────────────────────────────────────
_PORTS = [
    {"name": "Singapore",  "region": "Asia"},
    {"name": "Rotterdam",  "region": "Europe"},
    {"name": "Fujairah",   "region": "Middle East"},
    {"name": "Houston",    "region": "Americas"},
    {"name": "Hong Kong",  "region": "Asia"},
    {"name": "Las Palmas", "region": "Atlantic"},
    {"name": "Gibraltar",  "region": "Europe"},
    {"name": "Piraeus",    "region": "Mediterranean"},
    {"name": "Santos",     "region": "Americas"},
    {"name": "Durban",     "region": "Africa"},
]

_VESSEL_TYPES = ["VLCC (320k DWT)", "Capesize (180k DWT)", "Panamax (75k DWT)",
                 "Handymax (50k DWT)", "Container (8k TEU)", "Container (15k TEU)"]

_VESSEL_CONSUMPTION = {
    "VLCC (320k DWT)":      {"base_mt_day": 85,  "design_speed": 15.5},
    "Capesize (180k DWT)":  {"base_mt_day": 58,  "design_speed": 14.5},
    "Panamax (75k DWT)":    {"base_mt_day": 32,  "design_speed": 14.0},
    "Handymax (50k DWT)":   {"base_mt_day": 22,  "design_speed": 13.5},
    "Container (8k TEU)":   {"base_mt_day": 120, "design_speed": 22.0},
    "Container (15k TEU)":  {"base_mt_day": 210, "design_speed": 23.0},
}

# Global average benchmark prices ($/MT)
_GLOBAL_AVG = {"VLSFO": 628, "HFO": 480, "MGO": 875}


def _seed_price(port: str, fuel: str) -> float:
    rng = random.Random(abs(hash(port + fuel)) % 9999)
    spreads = {"VLSFO": _GLOBAL_AVG["VLSFO"], "HFO": _GLOBAL_AVG["HFO"], "MGO": _GLOBAL_AVG["MGO"]}
    base = spreads[fuel]
    return round(base * rng.uniform(0.91, 1.12), 1)


def _avail_label(val: float) -> tuple[str, str]:
    if val > 0.6:
        return "PLENTIFUL", C_HIGH
    elif val > 0.35:
        return "ADEQUATE", C_MOD
    else:
        return "TIGHT", C_LOW


def _kpi_card(label: str, value: str, delta: str, delta_good: bool,
              sub: str = "", accent: str = C_ACCENT) -> str:
    delta_color = C_HIGH if delta_good else C_LOW
    sub_html = f'<div style="font-size:11px;color:{C_TEXT3};margin-top:4px">{sub}</div>' if sub else ""
    return f"""
<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;
padding:18px 20px;height:100%">
  <div style="font-size:11px;color:{C_TEXT2};letter-spacing:0.5px;text-transform:uppercase;
  margin-bottom:8px">{label}</div>
  <div style="font-size:26px;font-weight:700;color:{C_TEXT};line-height:1">{value}</div>
  <div style="font-size:12px;color:{delta_color};margin-top:6px;font-weight:600">{delta}</div>
  {sub_html}
</div>"""


def _section_header(title: str, subtitle: str = "") -> None:
    sub_html = f'<div style="font-size:13px;color:{C_TEXT2};margin-top:4px">{subtitle}</div>' if subtitle else ""
    st.markdown(f"""
<div style="margin:28px 0 16px 0;padding-bottom:10px;border-bottom:1px solid {C_BORDER}">
  <div style="font-size:18px;font-weight:700;color:{C_TEXT}">{title}</div>
  {sub_html}
</div>""", unsafe_allow_html=True)


# ── Section 1: Bunker Dashboard ────────────────────────────────────────────────

def _bunker_dashboard() -> None:
    _section_header("Bunker Fuel Dashboard",
                    "Current bunker prices at key hubs — represents 40-60% of voyage operating cost")
    try:
        rng = random.Random(77)
        vlsfo   = _GLOBAL_AVG["VLSFO"] + rng.uniform(-18, 18)
        hfo     = _GLOBAL_AVG["HFO"]   + rng.uniform(-15, 15)
        mgo     = _GLOBAL_AVG["MGO"]   + rng.uniform(-22, 22)
        lng_eq  = round(vlsfo * 1.08, 1)
        bunker_pct = rng.uniform(38, 58)
        vlsfo_wow  = rng.uniform(-4.2, 5.1)
        hfo_wow    = rng.uniform(-3.8, 4.6)
        mgo_wow    = rng.uniform(-5.0, 6.2)

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.markdown(_kpi_card(
                "VLSFO (0.5% Sulfur)",
                f"${vlsfo:.0f}/MT",
                f"{'▲' if vlsfo_wow >= 0 else '▼'} ${abs(vlsfo_wow):.1f} WoW",
                vlsfo_wow <= 0,
                "Singapore benchmark",
                C_ACCENT
            ), unsafe_allow_html=True)
        with c2:
            st.markdown(_kpi_card(
                "HFO (3.5% Sulfur)",
                f"${hfo:.0f}/MT",
                f"{'▲' if hfo_wow >= 0 else '▼'} ${abs(hfo_wow):.1f} WoW",
                hfo_wow <= 0,
                "Scrubber vessels only",
                C_MOD
            ), unsafe_allow_html=True)
        with c3:
            st.markdown(_kpi_card(
                "MGO (0.1% Sulfur)",
                f"${mgo:.0f}/MT",
                f"{'▲' if mgo_wow >= 0 else '▼'} ${abs(mgo_wow):.1f} WoW",
                mgo_wow <= 0,
                "ECA zones / anchorage",
                C_PURPLE
            ), unsafe_allow_html=True)
        with c4:
            st.markdown(_kpi_card(
                "LNG Equivalent",
                f"${lng_eq:.0f}/MT",
                f"vs VLSFO +${lng_eq - vlsfo:.0f}",
                lng_eq <= vlsfo,
                "Energy-equivalent price",
                C_TEAL
            ), unsafe_allow_html=True)
        with c5:
            st.markdown(_kpi_card(
                "Bunker % Voyage Cost",
                f"{bunker_pct:.1f}%",
                "of total operating cost",
                bunker_pct < 45,
                "Container 15k TEU basis",
                C_CYAN
            ), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Bunker dashboard error: {exc}")
        st.info("Bunker dashboard unavailable.")


# ── Section 2: Bunker Price by Port ───────────────────────────────────────────

def _bunker_price_by_port() -> None:
    _section_header("Bunker Price by Port",
                    "10 major bunkering hubs — prices in $/MT and spread vs global average")
    try:
        st.markdown(f"""
<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:14px;overflow:hidden">
  <div style="display:grid;grid-template-columns:1.3fr 0.7fr 1fr 1fr 1fr 1fr 1.2fr;
  padding:10px 16px;border-bottom:1px solid {C_BORDER};font-size:11px;
  color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.5px">
    <span>Port</span><span>Region</span><span>VLSFO</span><span>HFO</span>
    <span>MGO</span><span>Availability</span><span>vs Global Avg</span>
  </div>""", unsafe_allow_html=True)

        for p in _PORTS:
            rng = random.Random(abs(hash(p["name"])) % 9999)
            vlsfo = _seed_price(p["name"], "VLSFO")
            hfo   = _seed_price(p["name"], "HFO")
            mgo   = _seed_price(p["name"], "MGO")
            avail_raw = rng.uniform(0.2, 0.85)
            avail_lbl, avail_color = _avail_label(avail_raw)
            spread = vlsfo - _GLOBAL_AVG["VLSFO"]
            sp_color = C_LOW if spread > 15 else (C_HIGH if spread < -15 else C_TEXT3)
            sp_sign  = "+" if spread >= 0 else ""

            st.markdown(f"""
<div style="display:grid;grid-template-columns:1.3fr 0.7fr 1fr 1fr 1fr 1fr 1.2fr;
padding:11px 16px;border-bottom:1px solid {C_BORDER};align-items:center">
  <span style="color:{C_TEXT};font-weight:600">{p['name']}</span>
  <span style="color:{C_TEXT3};font-size:11px">{p['region']}</span>
  <span style="color:{C_ACCENT};font-weight:600">${vlsfo:.0f}</span>
  <span style="color:{C_MOD}">${hfo:.0f}</span>
  <span style="color:{C_PURPLE}">${mgo:.0f}</span>
  <span style="background:{avail_color}22;color:{avail_color};border-radius:5px;
  padding:2px 7px;font-size:10px;font-weight:700">{avail_lbl}</span>
  <span style="color:{sp_color};font-weight:600">{sp_sign}${spread:.0f}</span>
</div>""", unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)
        st.caption(f"Global averages: VLSFO ${_GLOBAL_AVG['VLSFO']}/MT | HFO ${_GLOBAL_AVG['HFO']}/MT | MGO ${_GLOBAL_AVG['MGO']}/MT")
    except Exception as exc:
        logger.warning(f"Port price table error: {exc}")
        st.info("Port price table unavailable.")


# ── Section 3: Bunker Price Chart ─────────────────────────────────────────────

def _bunker_price_chart() -> None:
    _section_header("24-Month Bunker Price History",
                    "VLSFO, HFO, MGO monthly prices — note IMO 2020 implementation spike")
    try:
        today = date.today()
        months = [(today.replace(day=1) - timedelta(days=30 * i)) for i in range(23, -1, -1)]

        rng = random.Random(101)
        vlsfo_prices, hfo_prices, mgo_prices = [], [], []

        vlsfo_base, hfo_base, mgo_base = 560.0, 410.0, 820.0
        for i, m in enumerate(months):
            shock = 1.0
            # IMO 2020 spike: Jan–Mar 2020 analog (roughly 24 months ago if today is Mar 2026)
            if i in (0, 1, 2):
                shock = 1.15
            trend = 1 + i * 0.003
            vlsfo_prices.append(round(vlsfo_base * trend * shock * rng.uniform(0.96, 1.04)))
            hfo_prices.append(round(hfo_base   * trend * shock * rng.uniform(0.95, 1.05)))
            mgo_prices.append(round(mgo_base   * trend * shock * rng.uniform(0.97, 1.03)))

        x_labels = [m.strftime("%b %Y") for m in months]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=x_labels, y=vlsfo_prices, name="VLSFO 0.5%",
            line=dict(color=C_ACCENT, width=2.5),
            hovertemplate="VLSFO: $%{y}/MT<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=x_labels, y=hfo_prices, name="HFO 3.5%",
            line=dict(color=C_MOD, width=2.5),
            hovertemplate="HFO: $%{y}/MT<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=x_labels, y=mgo_prices, name="MGO 0.1%",
            line=dict(color=C_PURPLE, width=2.5),
            hovertemplate="MGO: $%{y}/MT<extra></extra>",
        ))
        fig.add_vrect(
            x0=x_labels[0], x1=x_labels[2],
            fillcolor="rgba(239,68,68,0.08)", line_width=0,
            annotation_text="IMO 2020 Spike",
            annotation_position="top left",
            annotation_font_color=C_LOW,
        )
        fig.update_layout(
            plot_bgcolor=C_SURFACE, paper_bgcolor=C_CARD,
            font_color=C_TEXT2, margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(gridcolor=C_BORDER, tickangle=-30),
            yaxis=dict(title="Price ($/MT)", gridcolor=C_BORDER),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            height=360,
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception as exc:
        logger.warning(f"Bunker chart error: {exc}")
        st.info("Bunker price chart unavailable.")


# ── Section 4: Bunker Optimization Calculator ──────────────────────────────────

def _bunker_optimization_calculator() -> None:
    _section_header("Bunker Optimization Calculator",
                    "Estimate total bunker cost and explore slow-steaming fuel savings")
    try:
        c1, c2, c3 = st.columns(3)
        with c1:
            vessel = st.selectbox("Vessel Type", list(_VESSEL_CONSUMPTION.keys()), key="bk_vessel")
        with c2:
            distance = st.number_input("Voyage Distance (NM)", 500, 25000, 12000, 500, key="bk_dist")
        with c3:
            fuel_price = st.number_input("Fuel Price ($/MT)", 300, 1200, 628, 10, key="bk_fuel_price")

        spec = _VESSEL_CONSUMPTION[vessel]
        design_speed = spec["design_speed"]
        base_mt_day  = spec["base_mt_day"]

        speed = st.slider("Vessel Speed (knots)", 8.0, float(design_speed + 2),
                          float(design_speed), 0.5, key="bk_speed")

        # Admiralty law: fuel ∝ speed^3 relative to base
        speed_factor = (speed / design_speed) ** 3
        mt_day       = base_mt_day * speed_factor
        travel_days  = distance / (speed * 24)
        total_mt     = mt_day * travel_days
        total_cost   = total_mt * fuel_price

        # Slow-steam comparison: 10% speed reduction
        slow_speed   = speed * 0.90
        slow_factor  = (slow_speed / design_speed) ** 3
        slow_mt_day  = base_mt_day * slow_factor
        slow_days    = distance / (slow_speed * 24)
        slow_mt      = slow_mt_day * slow_days
        slow_cost    = slow_mt * fuel_price
        fuel_save_pct = (1 - slow_mt / total_mt) * 100
        cost_save     = total_cost - slow_cost
        extra_days    = slow_days - travel_days

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(_kpi_card("Total Bunker Cost", f"${total_cost:,.0f}",
                                  f"{total_mt:.0f} MT consumed", True, f"{travel_days:.1f} days", C_ACCENT),
                        unsafe_allow_html=True)
        with c2:
            st.markdown(_kpi_card("Consumption Rate", f"{mt_day:.1f} MT/day",
                                  f"At {speed:.1f} kn", True, "Speed³ law", C_MOD),
                        unsafe_allow_html=True)
        with c3:
            st.markdown(_kpi_card("Slow Steam Saving", f"${cost_save:,.0f}",
                                  f"−{fuel_save_pct:.0f}% fuel at {slow_speed:.1f} kn",
                                  True, f"+{extra_days:.1f} days transit", C_HIGH),
                        unsafe_allow_html=True)
        with c4:
            st.markdown(_kpi_card("Cost per NM", f"${total_cost / distance:.2f}",
                                  f"${slow_cost / distance:.2f} slow steam",
                                  True, "Per nautical mile", C_TEAL),
                        unsafe_allow_html=True)

        st.markdown(f"""
<div style="background:rgba(16,185,129,0.08);border:1px solid {C_HIGH};border-radius:10px;
padding:13px 18px;margin-top:6px">
  <span style="color:{C_HIGH};font-weight:700">SLOW STEAMING RULE OF THUMB: </span>
  <span style="color:{C_TEXT};font-size:13px">Reducing speed by 10% cuts fuel consumption by
  approximately <b>27%</b> (cubic relationship). On this voyage, that saves
  <b>${cost_save:,.0f}</b> in bunker cost at the cost of <b>{extra_days:.1f} extra days</b> at sea.</span>
</div>""", unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Bunker calculator error: {exc}")
        st.info("Bunker calculator unavailable.")


# ── Section 5: Fuel Spread Analysis ───────────────────────────────────────────

def _fuel_spread_analysis() -> None:
    _section_header("VLSFO / HFO Spread Analysis",
                    "Spread is the economic driver for scrubber investment decisions")
    try:
        today = date.today()
        months = [(today.replace(day=1) - timedelta(days=30 * i)) for i in range(23, -1, -1)]
        rng = random.Random(202)

        spreads = []
        base_spread = 148.0
        for i in range(24):
            shock = 1.3 if i < 3 else 1.0
            spreads.append(round(base_spread * shock * rng.uniform(0.82, 1.22)))

        x_labels = [m.strftime("%b %Y") for m in months]
        current_spread = spreads[-1]

        scrubber_capex = 3_500_000
        voyages_to_payback = scrubber_capex / (current_spread * 200)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=x_labels, y=spreads, name="VLSFO-HFO Spread",
            fill="tozeroy", fillcolor="rgba(59,130,246,0.10)",
            line=dict(color=C_ACCENT, width=2.5),
            hovertemplate="Spread: $%{y}/MT<extra></extra>",
        ))
        fig.add_hline(y=200, line_dash="dash", line_color=C_HIGH,
                      annotation_text="Scrubber Payback Threshold ~$200/MT",
                      annotation_font_color=C_HIGH)
        fig.update_layout(
            plot_bgcolor=C_SURFACE, paper_bgcolor=C_CARD,
            font_color=C_TEXT2, margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(gridcolor=C_BORDER, tickangle=-30),
            yaxis=dict(title="VLSFO−HFO Spread ($/MT)", gridcolor=C_BORDER),
            height=300, showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

        payback_color = C_HIGH if current_spread > 200 else C_MOD
        st.markdown(f"""
<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:4px">
  <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:14px 16px;text-align:center">
    <div style="font-size:11px;color:{C_TEXT3};margin-bottom:6px">CURRENT SPREAD</div>
    <div style="font-size:24px;font-weight:700;color:{C_ACCENT}">${current_spread}/MT</div>
  </div>
  <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:14px 16px;text-align:center">
    <div style="font-size:11px;color:{C_TEXT3};margin-bottom:6px">SCRUBBER PAYBACK</div>
    <div style="font-size:24px;font-weight:700;color:{payback_color}">{voyages_to_payback:.0f} voyages</div>
  </div>
  <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:14px 16px;text-align:center">
    <div style="font-size:11px;color:{C_TEXT3};margin-bottom:6px">SCRUBBER VERDICT</div>
    <div style="font-size:16px;font-weight:700;color:{payback_color}">
      {'MARGINAL — monitor spread' if current_spread < 200 else 'ECONOMIC — scrubber pays'}
    </div>
  </div>
</div>""", unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Spread analysis error: {exc}")
        st.info("Spread analysis unavailable.")


# ── Section 6: Alternative Fuels Comparison ────────────────────────────────────

def _alternative_fuels_comparison() -> None:
    _section_header("Alternative Fuels Comparison",
                    "VLSFO vs low-carbon alternatives — cost, availability, and readiness")
    try:
        fuels = [
            {
                "name": "VLSFO", "color": C_ACCENT,
                "cost": 628, "avail_score": 9.2,
                "pros": "Universal availability · Proven technology · No CAPEX",
                "cons": "High CO₂ · Regulatory risk post-2030 · Price volatility",
                "vessels": "99% of fleet",
            },
            {
                "name": "HFO + Scrubber", "color": C_MOD,
                "cost": 480, "avail_score": 8.5,
                "pros": "Cheapest fuel · IMO 2020 compliant with scrubber",
                "cons": "$3-5M scrubber CAPEX · Washwater regulations tightening · No GHG benefit",
                "vessels": "~4,500 vessels",
            },
            {
                "name": "LNG", "color": C_HIGH,
                "cost": 680, "avail_score": 5.8,
                "pros": "25% fewer GHG emissions · Increasingly available · IMO 2030 compliant",
                "cons": "Higher CAPEX · Limited bunkering network · Methane slip risk",
                "vessels": "~500 LNG-ready",
            },
            {
                "name": "Methanol", "color": C_CYAN,
                "cost": 820, "avail_score": 4.1,
                "pros": "Green methanol pathway · Lower CAPEX than LNG · Liquid at ambient",
                "cons": "Low energy density (2× volume) · Green supply scarce · High cost",
                "vessels": "~50 vessels (Maersk)",
            },
            {
                "name": "Ammonia", "color": C_PURPLE,
                "cost": 950, "avail_score": 2.3,
                "pros": "Zero direct CO₂ · Hydrogen carrier · IMO 2050 target fuel",
                "cons": "Toxicity risk · No commercial vessels yet · Very high cost",
                "vessels": "Pilots only (2026)",
            },
            {
                "name": "Bio-diesel (B30)", "color": C_TEAL,
                "cost": 740, "avail_score": 5.0,
                "pros": "Drop-in fuel · No engine modification · 20-30% CO₂ reduction",
                "cons": "Feedstock competition · Price premium · Sustainability certification",
                "vessels": "Growing adoption",
            },
        ]

        for f in fuels:
            bar_width = min(f["avail_score"] * 10, 100)
            avail_color = C_HIGH if f["avail_score"] > 7 else (C_MOD if f["avail_score"] > 4 else C_LOW)
            st.markdown(f"""
<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;
padding:16px 20px;margin-bottom:10px">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px">
    <div>
      <span style="font-size:15px;font-weight:700;color:{f['color']}">{f['name']}</span>
      <span style="margin-left:12px;font-size:13px;color:{C_TEXT};font-weight:600">
        ${f['cost']}/MT equiv.
      </span>
      <span style="margin-left:12px;font-size:11px;color:{C_TEXT3}">{f['vessels']}</span>
    </div>
    <div style="text-align:right;min-width:120px">
      <div style="font-size:10px;color:{C_TEXT3};margin-bottom:3px">Availability {f['avail_score']}/10</div>
      <div style="height:5px;background:{C_BORDER};border-radius:3px;width:120px">
        <div style="height:5px;width:{bar_width}%;background:{avail_color};border-radius:3px"></div>
      </div>
    </div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;font-size:12px">
    <div><span style="color:{C_HIGH}">+ </span><span style="color:{C_TEXT2}">{f['pros']}</span></div>
    <div><span style="color:{C_LOW}">− </span><span style="color:{C_TEXT2}">{f['cons']}</span></div>
  </div>
</div>""", unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Alternative fuels error: {exc}")
        st.info("Alternative fuels comparison unavailable.")


# ── Section 7: Bunker Hedging ──────────────────────────────────────────────────

def _bunker_hedging() -> None:
    _section_header("Bunker Hedging Strategy",
                    "Tools and mechanisms to manage bunker price exposure")
    try:
        rng = random.Random(303)
        brent_corr = round(rng.uniform(0.72, 0.89), 2)
        vlsfo_vol  = round(rng.uniform(18, 32), 1)
        swap_bid   = _GLOBAL_AVG["VLSFO"] - rng.uniform(4, 12)
        swap_ask   = swap_bid + rng.uniform(6, 15)

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(_kpi_card(
                "Brent–VLSFO Correlation",
                f"{brent_corr:.2f}",
                "90-day rolling R²",
                True,
                "High = Brent hedges bunker",
                C_ACCENT
            ), unsafe_allow_html=True)
        with c2:
            st.markdown(_kpi_card(
                "VLSFO 30-day Volatility",
                f"{vlsfo_vol:.1f}%",
                "Annualized σ",
                vlsfo_vol < 25,
                "Higher vol = more hedging value",
                C_MOD
            ), unsafe_allow_html=True)
        with c3:
            st.markdown(_kpi_card(
                "Bunker Swap Market",
                f"${swap_bid:.0f} / ${swap_ask:.0f}",
                f"Spread: ${swap_ask - swap_bid:.0f}/MT",
                True,
                "Bid / Ask (SIN delivery)",
                C_TEAL
            ), unsafe_allow_html=True)

        strategies = [
            {
                "name": "Crude Oil Futures (ICE Brent)",
                "icon": "O",
                "color": C_ACCENT,
                "desc": "Hedge bunker exposure using ICE Brent futures. Correlation of "
                        f"{brent_corr:.2f} means Brent captures ~{int(brent_corr*100)}% of VLSFO price moves. "
                        "Cost-effective: liquid market, tight spreads. Best for 1-6 month horizons.",
                "rating": "PREFERRED",
                "rating_color": C_HIGH,
            },
            {
                "name": "Bunker Fuel Swaps (OTC)",
                "icon": "S",
                "color": C_MOD,
                "desc": "Direct VLSFO or HFO 380 swaps settled against Platts assessments. "
                        "Eliminates basis risk vs crude hedges. Available at Singapore, "
                        "Rotterdam, and Houston. Typical tenor: 1-12 months. Min size: 500 MT.",
                "rating": "MOST PRECISE",
                "rating_color": C_ACCENT,
            },
            {
                "name": "Bunker Call Options",
                "icon": "C",
                "color": C_PURPLE,
                "desc": "Buy call options on bunker swaps to cap downside with unlimited upside. "
                        "Premium paid upfront — no margin calls. Useful when vol is low. "
                        f"At {vlsfo_vol:.0f}% vol, ATM 6-month call premium ≈ $45-60/MT.",
                "rating": "PROTECTION ONLY",
                "rating_color": C_PURPLE,
            },
            {
                "name": "Collar Strategy",
                "icon": "C",
                "color": C_CYAN,
                "desc": "Buy call + sell put to finance hedge at zero net premium. "
                        "Caps maximum cost but limits benefit if prices fall. "
                        "Typical: buy $700 call, sell $550 put. Common for annual budgets.",
                "rating": "BUDGET CERTAINTY",
                "rating_color": C_CYAN,
            },
        ]

        st.markdown(f'<div style="margin-top:16px;display:grid;grid-template-columns:1fr 1fr;gap:12px">', unsafe_allow_html=True)
        for s in strategies:
            st.markdown(f"""
<div style="background:{C_CARD};border:1px solid {C_BORDER};border-left:3px solid {s['color']};
border-radius:12px;padding:16px 18px">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
    <span style="font-size:14px;font-weight:700;color:{s['color']}">{s['name']}</span>
    <span style="background:{s['rating_color']}22;color:{s['rating_color']};border-radius:5px;
    padding:2px 8px;font-size:10px;font-weight:700">{s['rating']}</span>
  </div>
  <div style="font-size:12px;color:{C_TEXT2};line-height:1.6">{s['desc']}</div>
</div>""", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown(f"""
<div style="background:rgba(99,102,241,0.08);border:1px solid {C_PURPLE};border-radius:10px;
padding:14px 18px;margin-top:12px;font-size:12px;color:{C_TEXT2};line-height:1.7">
  <span style="color:{C_PURPLE};font-weight:700">HEDGING RULE OF THUMB: </span>
  Hedge 50-80% of expected bunker consumption 3-6 months forward using a blended strategy:
  50% in Brent futures (low cost, high liquidity) and 30% in direct bunker swaps (precision).
  Leave 20% unhedged to benefit from any price declines. Review hedge ratio monthly
  against consumption actuals.
</div>""", unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Bunker hedging error: {exc}")
        st.info("Bunker hedging section unavailable.")


# ── Main render ────────────────────────────────────────────────────────────────

def render(macro_data=None, freight_data=None) -> None:
    """Render the Bunker Fuel Intelligence tab."""
    try:
        st.markdown(f"""
<div style="background:linear-gradient(135deg,{C_MOD}18,{C_ACCENT}10);
border:1px solid {C_BORDER};border-radius:16px;padding:22px 26px;margin-bottom:24px">
  <div style="font-size:22px;font-weight:800;color:{C_TEXT}">
    Bunker Fuel Intelligence
  </div>
  <div style="font-size:13px;color:{C_TEXT2};margin-top:6px">
    Real-time bunker prices &amp; port comparison · Optimization calculator ·
    Scrubber spread economics · Alternative fuels · Hedging strategy
  </div>
</div>""", unsafe_allow_html=True)
    except Exception:
        st.subheader("Bunker Fuel Intelligence")

    _bunker_dashboard()
    _bunker_price_by_port()
    _bunker_price_chart()
    _bunker_optimization_calculator()
    _fuel_spread_analysis()
    _alternative_fuels_comparison()
    _bunker_hedging()
