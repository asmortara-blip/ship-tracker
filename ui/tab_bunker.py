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

from data.quality import DataSource
from routes.rate_estimator import (
    compute_net_freight,
    net_freight_divergence,
)
from utils.helpers import stable_hash
from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    insight_card_html,
    live_data_badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# Domain accents for fuel types
C_PURPLE = "#7c6eaf"
C_CYAN   = "#4a90a4"
C_TEAL   = "#14b8a6"

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

_VESSEL_CONSUMPTION = {
    "VLCC (320k DWT)":      {"base_mt_day": 85,  "design_speed": 15.5},
    "Capesize (180k DWT)":  {"base_mt_day": 58,  "design_speed": 14.5},
    "Panamax (75k DWT)":    {"base_mt_day": 32,  "design_speed": 14.0},
    "Handymax (50k DWT)":   {"base_mt_day": 22,  "design_speed": 13.5},
    "Container (8k TEU)":   {"base_mt_day": 120, "design_speed": 22.0},
    "Container (15k TEU)":  {"base_mt_day": 210, "design_speed": 23.0},
}

_GLOBAL_AVG = {"VLSFO": 628, "HFO": 480, "MGO": 875}

# ── Data sources (provenance pills) ────────────────────────────────────────────
_BUNKER_SRC = DataSource(
    name="Bunker Price Model (synthetic)",
    kind="modeled",
    quality="demo",
    notes="Demo bunker hub prices and history - replace with Ship & Bunker / Platts feed",
)
_SPREAD_SRC = DataSource(
    name="VLSFO-HFO Spread Model (synthetic)",
    kind="modeled",
    quality="demo",
    notes="Demo scrubber spread series",
)
_HEDGE_SRC = DataSource(
    name="Bunker Hedging Model (synthetic)",
    kind="modeled",
    quality="demo",
    notes="Demo correlation, vol, and swap quotes",
)
# Net-freight (R050): crude anchor is REAL when FRED WTI is live; the
# crude→VLSFO conversion + fuel-leg are always modeled.
_NETFREIGHT_REAL_SRC = DataSource(
    name="FRED WTI crude (live) + modeled bunker/fuel-leg",
    kind="live",
    quality="good",
    url="https://fred.stlouisfed.org/series/DCOILWTICO",
    notes="Net freight = gross $/FEU − fuel leg. Crude anchored on real FRED "
          "WTI (DCOILWTICO); crude→VLSFO (×6.35 +$95/MT) + fuel-leg are modeled.",
)
_NETFREIGHT_MODELED_SRC = DataSource(
    name="Net-Freight Model (modeled crude fallback)",
    kind="modeled",
    quality="modeled",
    notes="FRED WTI unavailable — crude anchored on a modeled fallback "
          "($72/bbl). Entire net-freight figure is modeled.",
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mono(value: str, color: str = C_TEXT) -> str:
    return f'<span style="font-family:var(--mono);color:{color};font-variant-numeric:tabular-nums;">{value}</span>'


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return f'<span style="font-family:var(--sans);color:{color};font-weight:{weight};">{value}</span>'


def _seed_price(port: str, fuel: str) -> float:
    rng = random.Random(stable_hash(port + fuel) % 9999)
    base = _GLOBAL_AVG[fuel]
    return round(base * rng.uniform(0.91, 1.12), 1)


def _avail_label(val: float) -> tuple[str, str]:
    if val > 0.6:
        return "PLENTIFUL", C_HIGH
    elif val > 0.35:
        return "ADEQUATE", C_MOD
    else:
        return "TIGHT", C_LOW


# ── Section 1: Bunker Dashboard ────────────────────────────────────────────────

def _bunker_dashboard() -> None:
    section_header(
        "Bunker Fuel Dashboard",
        "Current bunker prices at key hubs — represents 40-60% of voyage operating cost",
    )
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

        def _arrow(delta: float) -> str:
            return "▲" if delta >= 0 else "▼"

        metric_card_row(
            [
                {
                    "label": "VLSFO (0.5% Sulfur)",
                    "value": f"${vlsfo:.0f}/MT",
                    "accent": C_ACCENT,
                    "delta": f"{_arrow(vlsfo_wow)} ${abs(vlsfo_wow):.1f} WoW",
                    "delta_color": C_HIGH if vlsfo_wow <= 0 else C_LOW,
                    "sublabel": "Singapore benchmark",
                },
                {
                    "label": "HFO (3.5% Sulfur)",
                    "value": f"${hfo:.0f}/MT",
                    "accent": C_MOD,
                    "delta": f"{_arrow(hfo_wow)} ${abs(hfo_wow):.1f} WoW",
                    "delta_color": C_HIGH if hfo_wow <= 0 else C_LOW,
                    "sublabel": "Scrubber vessels only",
                },
                {
                    "label": "MGO (0.1% Sulfur)",
                    "value": f"${mgo:.0f}/MT",
                    "accent": C_PURPLE,
                    "delta": f"{_arrow(mgo_wow)} ${abs(mgo_wow):.1f} WoW",
                    "delta_color": C_HIGH if mgo_wow <= 0 else C_LOW,
                    "sublabel": "ECA zones / anchorage",
                },
                {
                    "label": "LNG Equivalent",
                    "value": f"${lng_eq:.0f}/MT",
                    "accent": C_TEAL,
                    "delta": f"vs VLSFO +${lng_eq - vlsfo:.0f}",
                    "delta_color": C_HIGH if lng_eq <= vlsfo else C_LOW,
                    "sublabel": "Energy-equivalent price",
                },
                {
                    "label": "Bunker % Voyage Cost",
                    "value": f"{bunker_pct:.1f}%",
                    "accent": C_CYAN,
                    "delta": "of total operating cost",
                    "delta_color": C_HIGH if bunker_pct < 45 else C_MOD,
                    "sublabel": "Container 15k TEU basis",
                },
            ],
            columns=5,
        )
        st.markdown(source_footer([_BUNKER_SRC]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Bunker dashboard error: {exc}")
        st.info("Bunker dashboard unavailable.")


# ── Section 2: Bunker Price by Port ───────────────────────────────────────────

def _bunker_price_by_port() -> None:
    section_header(
        "Bunker Price by Port",
        "10 major bunkering hubs — prices in $/MT and spread vs global average",
    )
    try:
        headers = ["Port", "Region", "VLSFO", "HFO", "MGO", "Availability", "vs Global Avg"]
        rows = []
        for p in _PORTS:
            rng = random.Random(stable_hash(p["name"]) % 9999)
            vlsfo = _seed_price(p["name"], "VLSFO")
            hfo   = _seed_price(p["name"], "HFO")
            mgo   = _seed_price(p["name"], "MGO")
            avail_raw = rng.uniform(0.2, 0.85)
            avail_lbl, avail_color = _avail_label(avail_raw)
            spread = vlsfo - _GLOBAL_AVG["VLSFO"]
            sp_color = C_LOW if spread > 15 else (C_HIGH if spread < -15 else C_TEXT3)
            sp_sign  = "+" if spread >= 0 else ""

            rows.append([
                _sans(p["name"], color=C_TEXT, weight=600),
                _sans(p["region"], color=C_TEXT3),
                _mono(f"${vlsfo:.0f}", color=C_ACCENT),
                _mono(f"${hfo:.0f}",   color=C_MOD),
                _mono(f"${mgo:.0f}",   color=C_PURPLE),
                badge(avail_lbl, avail_color),
                _mono(f"{sp_sign}${spread:.0f}", color=sp_color),
            ])
        wsj_market_table(headers, rows)
        st.caption(
            f"Global averages: VLSFO ${_GLOBAL_AVG['VLSFO']}/MT | "
            f"HFO ${_GLOBAL_AVG['HFO']}/MT | MGO ${_GLOBAL_AVG['MGO']}/MT"
        )
        st.markdown(source_footer([_BUNKER_SRC]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Port price table error: {exc}")
        st.info("Port price table unavailable.")


# ── Section 3: Bunker Price Chart ─────────────────────────────────────────────

def _bunker_price_chart() -> None:
    section_header(
        "24-Month Bunker Price History",
        "VLSFO, HFO, MGO monthly prices — note IMO 2020 implementation spike",
    )
    try:
        today = date.today()
        months = [(today.replace(day=1) - timedelta(days=30 * i)) for i in range(23, -1, -1)]

        rng = random.Random(101)
        vlsfo_prices, hfo_prices, mgo_prices = [], [], []
        vlsfo_base, hfo_base, mgo_base = 560.0, 410.0, 820.0
        for i in range(len(months)):
            shock = 1.15 if i in (0, 1, 2) else 1.0
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
            fillcolor="rgba(192,57,43,0.08)", line_width=0,
            annotation_text="IMO 2020 Spike",
            annotation_position="top left",
            annotation_font_color=C_LOW,
        )
        apply_dark_layout(
            fig,
            height=360,
            margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(tickangle=-30),
            yaxis=dict(title="Price ($/MT)"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(source_footer([_BUNKER_SRC]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Bunker chart error: {exc}")
        st.info("Bunker price chart unavailable.")


# ── Section 4: Bunker Optimization Calculator ──────────────────────────────────

def _bunker_optimization_calculator() -> None:
    section_header(
        "Bunker Optimization Calculator",
        "Estimate total bunker cost and explore slow-steaming fuel savings",
    )
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

        speed = st.slider(
            "Vessel Speed (knots)", 8.0, float(design_speed + 2),
            float(design_speed), 0.5, key="bk_speed",
        )

        speed_factor = (speed / design_speed) ** 3
        mt_day       = base_mt_day * speed_factor
        travel_days  = distance / (speed * 24)
        total_mt     = mt_day * travel_days
        total_cost   = total_mt * fuel_price

        slow_speed   = speed * 0.90
        slow_factor  = (slow_speed / design_speed) ** 3
        slow_mt_day  = base_mt_day * slow_factor
        slow_days    = distance / (slow_speed * 24)
        slow_mt      = slow_mt_day * slow_days
        slow_cost    = slow_mt * fuel_price
        fuel_save_pct = (1 - slow_mt / total_mt) * 100
        cost_save     = total_cost - slow_cost
        extra_days    = slow_days - travel_days

        metric_card_row(
            [
                {
                    "label": "Total Bunker Cost",
                    "value": f"${total_cost:,.0f}",
                    "accent": C_ACCENT,
                    "delta": f"{total_mt:.0f} MT consumed",
                    "sublabel": f"{travel_days:.1f} days",
                },
                {
                    "label": "Consumption Rate",
                    "value": f"{mt_day:.1f} MT/day",
                    "accent": C_MOD,
                    "delta": f"At {speed:.1f} kn",
                    "sublabel": "Speed³ law",
                },
                {
                    "label": "Slow Steam Saving",
                    "value": f"${cost_save:,.0f}",
                    "accent": C_HIGH,
                    "delta": f"−{fuel_save_pct:.0f}% fuel at {slow_speed:.1f} kn",
                    "delta_color": C_HIGH,
                    "sublabel": f"+{extra_days:.1f} days transit",
                },
                {
                    "label": "Cost per NM",
                    "value": f"${total_cost / distance:.2f}",
                    "accent": C_TEAL,
                    "delta": f"${slow_cost / distance:.2f} slow steam",
                    "sublabel": "Per nautical mile",
                },
            ],
            columns=4,
        )

        st.markdown(
            insight_card_html(
                title="Slow Steaming Rule of Thumb",
                score=min(fuel_save_pct / 30.0, 1.0),
                action="Prioritize",
                rationale=(
                    f"Reducing speed by 10% cuts fuel consumption by approximately 27% "
                    f"(cubic relationship). On this voyage, that saves "
                    f"${cost_save:,.0f} in bunker cost at the cost of "
                    f"{extra_days:.1f} extra days at sea."
                ),
                category="ROUTE",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(source_footer([_BUNKER_SRC]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Bunker calculator error: {exc}")
        st.info("Bunker calculator unavailable.")


# ── Section 4b: Bunker-Adjusted Net Freight (TCE proxy, R050) ──────────────────

def _net_freight_panel(freight_data=None, macro_data=None, route_results=None) -> None:
    section_header(
        "Bunker-Adjusted Net Freight",
        "Margin net of fuel, not just gross spot — crude-anchored. "
        "Flags 'gross up, net down' when a rate rally is eaten by bunkers.",
    )
    try:
        from routes.route_registry import ROUTES_BY_ID
        from data.fred_feed import get_latest_value

        # ── Real crude anchor (cache-backed, offline-safe). get_latest_value
        # reads the already-fetched FRED payload; NO hot/uncached fetch here.
        crude = None
        if macro_data:
            crude = get_latest_value("DCOILWTICO", macro_data)
            if crude is None or crude <= 0:
                # Fall back to Brent if WTI is dark but Brent is live.
                crude = get_latest_value("DCOILBRENTEU", macro_data)
        crude = float(crude) if (crude is not None and crude > 0) else None
        is_real = crude is not None
        src = _NETFREIGHT_REAL_SRC if is_real else _NETFREIGHT_MODELED_SRC

        # ── Gather gross rates per route (current $/FEU).
        gross_by_route: dict[str, float] = {}
        if freight_data:
            for rid, df in freight_data.items():
                try:
                    if df is None or getattr(df, "empty", True):
                        continue
                    if "rate_usd_per_feu" not in df.columns:
                        continue
                    rates = df["rate_usd_per_feu"].dropna()
                    if not rates.empty:
                        gross_by_route[rid] = float(rates.iloc[-1])
                except Exception:
                    continue

        if not gross_by_route:
            st.info(
                "No gross freight rates available — net freight needs a live "
                "rate feed (rate_usd_per_feu per route)."
            )
            st.markdown(source_footer([src]), unsafe_allow_html=True)
            return

        # Provenance badge: REAL crude vs modeled fallback.
        st.markdown(
            live_data_badge(src)
            + (
                f'<span style="margin-left:8px;color:{C_TEXT3};font-family:var(--mono);'
                f'font-size:0.72rem;">WTI ${crude:.2f}/bbl</span>'
                if is_real else
                f'<span style="margin-left:8px;color:{C_TEXT3};font-family:var(--mono);'
                f'font-size:0.72rem;">modeled crude $72.00/bbl</span>'
            ),
            unsafe_allow_html=True,
        )

        headers = ["Route", "Gross $/FEU", "Fuel Leg $/FEU", "Net $/FEU", "Net Margin", "Distance"]
        rows = []
        eaten_routes = []
        for rid in sorted(gross_by_route):
            gross = gross_by_route[rid]
            nf = compute_net_freight(rid, gross, crude_usd_per_bbl=crude)
            name = ROUTES_BY_ID[rid].name if rid in ROUTES_BY_ID else rid
            net_color = C_HIGH if nf.net_freight_usd_per_feu > 0 else C_LOW
            margin_color = (
                C_HIGH if nf.net_margin_pct > 0.5
                else (C_MOD if nf.net_margin_pct > 0.0 else C_LOW)
            )
            if nf.net_freight_usd_per_feu < 0:
                eaten_routes.append(name)
            rows.append([
                _sans(name, color=C_TEXT, weight=600),
                _mono(f"${gross:,.0f}", color=C_TEXT),
                _mono(f"−${nf.fuel_leg_usd_per_feu:,.0f}", color=C_MOD),
                _mono(
                    f"{'−' if nf.net_freight_usd_per_feu < 0 else ''}"
                    f"${abs(nf.net_freight_usd_per_feu):,.0f}",
                    color=net_color,
                ),
                _mono(f"{nf.net_margin_pct * 100:,.0f}%", color=margin_color),
                _mono(f"{nf.distance_nm:,.0f} nm", color=C_TEXT3),
            ])
        wsj_market_table(headers, rows)

        if eaten_routes:
            st.markdown(
                insight_card_html(
                    title="Fuel-Eaten Routes (net freight below zero)",
                    score=1.0,
                    action="Caution",
                    rationale=(
                        "On these lanes the modeled fuel leg exceeds the gross "
                        "spot rate — net freight is NEGATIVE at current crude: "
                        + ", ".join(eaten_routes[:6])
                        + (" …" if len(eaten_routes) > 6 else "")
                        + ". Shown honestly, not clamped to zero."
                    ),
                    category="ROUTE",
                ),
                unsafe_allow_html=True,
            )

        # ── 'Gross up, net down' divergence over a 30-obs window per route.
        diverged = []
        for rid in sorted(gross_by_route):
            df = freight_data.get(rid)
            try:
                if df is None or getattr(df, "empty", True):
                    continue
                rates = df["rate_usd_per_feu"].dropna()
                if len(rates) < 2:
                    continue
                window = min(30, len(rates) - 1)
                gross_start = float(rates.iloc[-(window + 1)])
                gross_end = float(rates.iloc[-1])
                # Same crude both ends here (we only have the latest print);
                # the divergence is then driven purely by the gross path vs
                # the (constant-crude) fuel leg. Still surfaces a rally that
                # fails to lift net at the current fuel cost.
                dv = net_freight_divergence(
                    rid, gross_start, gross_end,
                    crude_start_usd_per_bbl=crude, crude_end_usd_per_bbl=crude,
                )
                if dv.diverged:
                    name = ROUTES_BY_ID[rid].name if rid in ROUTES_BY_ID else rid
                    diverged.append((name, dv))
            except Exception:
                continue

        if diverged:
            for name, dv in diverged:
                st.markdown(
                    insight_card_html(
                        title=f"Gross Up, Net Down — {name}",
                        score=1.0,
                        action="Alert",
                        rationale=(
                            f"Gross rate rose +${dv.gross_change_usd_per_feu:,.0f}/FEU "
                            f"over the window, but net freight moved "
                            f"{'+' if dv.net_change_usd_per_feu >= 0 else '−'}"
                            f"${abs(dv.net_change_usd_per_feu):,.0f}/FEU — the rally "
                            f"was eaten by fuel."
                        ),
                        category="CONVERGENCE",
                    ),
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No 'gross up, net down' divergence detected over the recent window.")

        st.markdown(source_footer([src]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Net freight panel error: {exc}")
        st.info("Net freight panel unavailable.")


# ── Section 5: Fuel Spread Analysis ───────────────────────────────────────────

def _fuel_spread_analysis() -> None:
    section_header(
        "VLSFO / HFO Spread Analysis",
        "Spread is the economic driver for scrubber investment decisions",
    )
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
            fill="tozeroy", fillcolor="rgba(53,114,176,0.10)",
            line=dict(color=C_ACCENT, width=2.5),
            hovertemplate="Spread: $%{y}/MT<extra></extra>",
        ))
        fig.add_hline(
            y=200, line_dash="dash", line_color=C_HIGH,
            annotation_text="Scrubber Payback Threshold ~$200/MT",
            annotation_font_color=C_HIGH,
        )
        apply_dark_layout(
            fig,
            height=300,
            showlegend=False,
            margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(tickangle=-30),
            yaxis=dict(title="VLSFO−HFO Spread ($/MT)"),
        )
        st.plotly_chart(fig, use_container_width=True)

        payback_color = C_HIGH if current_spread > 200 else C_MOD
        verdict = "MARGINAL — monitor spread" if current_spread < 200 else "ECONOMIC — scrubber pays"
        metric_card_row(
            [
                {
                    "label": "Current Spread",
                    "value": f"${current_spread}/MT",
                    "accent": C_ACCENT,
                    "sublabel": "VLSFO − HFO 3.5%",
                },
                {
                    "label": "Scrubber Payback",
                    "value": f"{voyages_to_payback:.0f} voyages",
                    "accent": payback_color,
                    "sublabel": "$3.5M CAPEX / spread × 200 MT",
                },
                {
                    "label": "Scrubber Verdict",
                    "value": verdict,
                    "accent": payback_color,
                    "sublabel": "Threshold ≈ $200/MT spread",
                },
            ],
            columns=3,
        )
        st.markdown(source_footer([_SPREAD_SRC]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Spread analysis error: {exc}")
        st.info("Spread analysis unavailable.")


# ── Section 6: Alternative Fuels Comparison ────────────────────────────────────

def _alternative_fuels_comparison() -> None:
    section_header(
        "Alternative Fuels Comparison",
        "VLSFO vs low-carbon alternatives — cost, availability, and readiness",
    )
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

        headers = ["Fuel", "Cost ($/MT)", "Availability", "Fleet", "Pros", "Cons"]
        rows = []
        for f in fuels:
            if f["avail_score"] > 7:
                avail_color = C_HIGH
            elif f["avail_score"] > 4:
                avail_color = C_MOD
            else:
                avail_color = C_LOW
            rows.append([
                _sans(f["name"], color=f["color"], weight=700),
                _mono(f"${f['cost']}", color=C_TEXT),
                badge(f"{f['avail_score']:.1f}/10", avail_color),
                _sans(f["vessels"], color=C_TEXT3),
                _sans(f["pros"], color=C_TEXT2),
                _sans(f["cons"], color=C_TEXT2),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer([_BUNKER_SRC]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Alternative fuels error: {exc}")
        st.info("Alternative fuels comparison unavailable.")


# ── Section 7: Bunker Hedging ──────────────────────────────────────────────────

def _bunker_hedging() -> None:
    section_header(
        "Bunker Hedging Strategy",
        "Tools and mechanisms to manage bunker price exposure",
    )
    try:
        rng = random.Random(303)
        brent_corr = round(rng.uniform(0.72, 0.89), 2)
        vlsfo_vol  = round(rng.uniform(18, 32), 1)
        swap_bid   = _GLOBAL_AVG["VLSFO"] - rng.uniform(4, 12)
        swap_ask   = swap_bid + rng.uniform(6, 15)

        metric_card_row(
            [
                {
                    "label": "Brent–VLSFO Correlation",
                    "value": f"{brent_corr:.2f}",
                    "accent": C_ACCENT,
                    "delta": "90-day rolling R²",
                    "sublabel": "High = Brent hedges bunker",
                },
                {
                    "label": "VLSFO 30-day Volatility",
                    "value": f"{vlsfo_vol:.1f}%",
                    "accent": C_MOD,
                    "delta": "Annualized σ",
                    "delta_color": C_HIGH if vlsfo_vol < 25 else C_LOW,
                    "sublabel": "Higher vol = more hedging value",
                },
                {
                    "label": "Bunker Swap Market",
                    "value": f"${swap_bid:.0f} / ${swap_ask:.0f}",
                    "accent": C_TEAL,
                    "delta": f"Spread: ${swap_ask - swap_bid:.0f}/MT",
                    "sublabel": "Bid / Ask (SIN delivery)",
                },
            ],
            columns=3,
        )

        strategies = [
            {
                "title": "Crude Oil Futures (ICE Brent)",
                "score": brent_corr,
                "action": "Prioritize",
                "category": "MACRO",
                "rationale": (
                    f"Hedge bunker exposure using ICE Brent futures. Correlation of "
                    f"{brent_corr:.2f} means Brent captures ~{int(brent_corr * 100)}% of VLSFO "
                    f"price moves. Cost-effective: liquid market, tight spreads. "
                    f"Best for 1-6 month horizons."
                ),
            },
            {
                "title": "Bunker Fuel Swaps (OTC)",
                "score": 0.85,
                "action": "Monitor",
                "category": "ROUTE",
                "rationale": (
                    "Direct VLSFO or HFO 380 swaps settled against Platts assessments. "
                    "Eliminates basis risk vs crude hedges. Available at Singapore, "
                    "Rotterdam, and Houston. Typical tenor: 1-12 months. Min size: 500 MT."
                ),
            },
            {
                "title": "Bunker Call Options",
                "score": min(vlsfo_vol / 40.0, 1.0),
                "action": "Watch",
                "category": "CONVERGENCE",
                "rationale": (
                    "Buy call options on bunker swaps to cap downside with unlimited upside. "
                    "Premium paid upfront — no margin calls. Useful when vol is low. "
                    f"At {vlsfo_vol:.0f}% vol, ATM 6-month call premium ≈ $45-60/MT."
                ),
            },
            {
                "title": "Collar Strategy",
                "score": 0.65,
                "action": "Caution",
                "category": "PORT_DEMAND",
                "rationale": (
                    "Buy call + sell put to finance hedge at zero net premium. "
                    "Caps maximum cost but limits benefit if prices fall. "
                    "Typical: buy $700 call, sell $550 put. Common for annual budgets."
                ),
            },
        ]

        c1, c2 = st.columns(2)
        for i, s in enumerate(strategies):
            target = c1 if i % 2 == 0 else c2
            with target:
                st.markdown(
                    insight_card_html(
                        title=s["title"],
                        score=s["score"],
                        action=s["action"],
                        rationale=s["rationale"],
                        category=s["category"],
                    ),
                    unsafe_allow_html=True,
                )

        st.markdown(
            insight_card_html(
                title="Hedging Rule of Thumb",
                score=0.75,
                action="Prioritize",
                rationale=(
                    "Hedge 50-80% of expected bunker consumption 3-6 months forward using "
                    "a blended strategy: 50% in Brent futures (low cost, high liquidity) and "
                    "30% in direct bunker swaps (precision). Leave 20% unhedged to benefit "
                    "from any price declines. Review hedge ratio monthly against consumption "
                    "actuals."
                ),
                category="CONVERGENCE",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(source_footer([_HEDGE_SRC]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Bunker hedging error: {exc}")
        st.info("Bunker hedging section unavailable.")


# ── Main render ────────────────────────────────────────────────────────────────

def render(*args, **kwargs) -> None:
    """Render the Bunker Fuel Intelligence tab.

    Called positionally from app.py as ``render(freight_data, macro_data,
    route_results)``. Accepts either positional or keyword forms so the
    net-freight panel (R050) gets the *correct* freight_data + macro_data
    regardless of caller convention.
    """
    # Resolve args robustly: positional order is (freight_data, macro_data,
    # route_results); keywords win if supplied.
    freight_data = kwargs.get("freight_data")
    macro_data = kwargs.get("macro_data")
    route_results = kwargs.get("route_results")
    if freight_data is None and len(args) >= 1:
        freight_data = args[0]
    if macro_data is None and len(args) >= 2:
        macro_data = args[1]
    if route_results is None and len(args) >= 3:
        route_results = args[2]

    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render

    with track_render('bunker'):
        try:
            page_header(
                title="Bunker Fuel Intelligence",
                subtitle=(
                    "Real-time bunker prices & port comparison · Optimization calculator · "
                    "Scrubber spread economics · Alternative fuels · Hedging strategy"
                ),
                badge_text="BUNKER",
                badge_color=C_ACCENT,
            )
        except Exception:
            logger.exception("tab_bunker: page header failed")
            section_header("Bunker Fuel Intelligence")

        _bunker_dashboard()
        _bunker_price_by_port()
        section_divider("Price History")
        _bunker_price_chart()
        section_divider("Voyage Economics")
        _bunker_optimization_calculator()
        _net_freight_panel(
            freight_data=freight_data,
            macro_data=macro_data,
            route_results=route_results,
        )
        _fuel_spread_analysis()
        section_divider("Fuel Transition")
        _alternative_fuels_comparison()
        section_divider("Risk Management")
        _bunker_hedging()
