"""
Booking Intelligence & Optimization Tab

Shipping booking intelligence suite: market dashboard, rate comparison,
optimal booking window, contract vs spot analysis, booking calendar,
spot rate alerts, and space availability by carrier.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_CONV,
    C_HIGH,
    C_LOW,
    C_MACRO,
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
# All sections in this tab use synthetic mock booking data driven by seeded
# random.Random. Mark every source pill accordingly so users know not to trust
# the numbers as live data.
_BOOKING_SOURCES = [
    {"name": "Internal booking-intelligence mock", "kind": "modeled", "quality": "demo"},
    {"name": "Synthetic carrier rate sheet",       "kind": "modeled", "quality": "demo"},
]


# ── Reference data ────────────────────────────────────────────────────────────
_ORIGINS = ["Shanghai", "Ningbo", "Shenzhen", "Singapore", "Rotterdam",
            "Hamburg", "Los Angeles", "New York", "Busan", "Colombo"]
_DESTINATIONS = ["Rotterdam", "Hamburg", "Los Angeles", "New York", "Felixstowe",
                 "Singapore", "Dubai", "Sydney", "Mumbai", "Santos"]
_CARGO_TYPES = ["General Cargo", "Electronics", "Machinery", "Apparel",
                "Chemicals", "Reefer", "Hazmat", "Automotive"]
_CARRIERS = [
    {"name": "COSCO",       "code": "COSU", "reliability": 91, "color": C_HIGH},
    {"name": "Maersk",      "code": "MAEU", "reliability": 89, "color": C_ACCENT},
    {"name": "MSC",         "code": "MSCU", "reliability": 87, "color": C_MOD},
    {"name": "CMA CGM",     "code": "CMDU", "reliability": 85, "color": C_CONV},
    {"name": "Hapag-Lloyd", "code": "HLCU", "reliability": 88, "color": C_MACRO},
]
_ROUTES = [
    "Shanghai → Rotterdam",
    "Shenzhen → Los Angeles",
    "Singapore → Hamburg",
    "Busan → New York",
    "Rotterdam → New York",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _seed_for(key: str) -> int:
    return abs(hash(key)) % 10000


def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _hex_alpha(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ── Section 1: Booking Market Dashboard ───────────────────────────────────────

def _booking_market_dashboard(freight_data) -> None:
    section_header("Booking Market Dashboard",
                   "Live market pulse — booking conditions as of today")
    try:
        rng = random.Random(42)
        volume     = rng.randint(148000, 162000)
        vol_delta  = rng.randint(-8, 12)
        lead_time  = rng.uniform(18, 28)
        lead_delta = rng.uniform(-3, 4)
        space_pct  = rng.uniform(72, 91)
        sp_delta   = rng.uniform(-5, 6)
        rfq_spot   = rng.uniform(80, 160)
        ctc_spot   = rng.uniform(-200, 150)

        metric_card_row([
            {
                "label":       "Booking Volume (TEU)",
                "value":       f"{volume:,}",
                "delta":       f"{'▲' if vol_delta >= 0 else '▼'} {abs(vol_delta)}% WoW",
                "delta_color": C_HIGH if vol_delta >= 0 else C_LOW,
                "sublabel":    "7-day rolling",
                "accent":      C_ACCENT,
            },
            {
                "label":       "Avg Lead Time",
                "value":       f"{lead_time:.1f} days",
                "delta":       f"{'▲' if lead_delta >= 0 else '▼'} {abs(lead_delta):.1f}d WoW",
                "delta_color": C_HIGH if lead_delta <= 0 else C_LOW,
                "sublabel":    "Days before sailing",
                "accent":      C_MOD,
            },
            {
                "label":       "Space Availability",
                "value":       f"{space_pct:.0f}% booked",
                "delta":       f"{'▲' if sp_delta >= 0 else '▼'} {abs(sp_delta):.1f}% WoW",
                "delta_color": C_HIGH if sp_delta <= 0 else C_LOW,
                "sublabel":    f"{100 - space_pct:.0f}% remaining",
                "accent":      C_LOW if space_pct > 85 else C_HIGH,
            },
            {
                "label":       "RFQ vs Spot",
                "value":       f"+${rfq_spot:.0f}/TEU",
                "delta":       "RFQ premium over spot",
                "delta_color": C_HIGH if rfq_spot < 100 else C_LOW,
                "sublabel":    "Neg = spot cheaper",
                "accent":      C_CONV,
            },
            {
                "label":       "Contract vs Spot",
                "value":       f"{'+' if ctc_spot >= 0 else ''}${ctc_spot:.0f}/TEU",
                "delta":       "Contract premium" if ctc_spot >= 0 else "Contract discount",
                "delta_color": C_HIGH if ctc_spot <= 0 else C_LOW,
                "sublabel":    "LTC vs spot market",
                "accent":      C_MACRO,
            },
        ], columns=5)
        st.markdown(source_footer(_BOOKING_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Booking dashboard error: {exc}")
        st.info("Booking dashboard data unavailable.")


# ── Section 2: Rate Comparison Tool ───────────────────────────────────────────

def _rate_comparison_tool() -> None:
    section_header("Rate Comparison Tool",
                   "Compare live rates across top carriers for your specific lane")
    try:
        c1, c2, c3 = st.columns(3)
        with c1:
            origin = st.selectbox("Origin Port", _ORIGINS, key="bk_origin")
        with c2:
            dest = st.selectbox("Destination Port", _DESTINATIONS, key="bk_dest")
        with c3:
            cargo = st.selectbox("Cargo Type", _CARGO_TYPES, key="bk_cargo")

        seed = _seed_for(f"{origin}{dest}{cargo}")
        rng = random.Random(seed)
        base = rng.randint(1800, 4200)

        rows = []
        for c in _CARRIERS:
            rate = int(base * rng.uniform(0.88, 1.18))
            transit = rng.randint(18, 42)
            on_time = c["reliability"] + rng.randint(-3, 3)
            score = round((on_time / 100) * 0.5 + (1 - (rate - base) / base) * 0.3
                          + (1 - (transit - 18) / 30) * 0.2, 2)
            rec = "BEST VALUE" if score > 0.75 else ("GOOD" if score > 0.55 else "SKIP")
            rows.append({
                "carrier": c["name"],
                "rate_usd": rate,
                "transit_days": transit,
                "on_time_pct": on_time,
                "score": score,
                "rec": rec,
                "color": c["color"],
            })
        rows.sort(key=lambda x: -x["score"])

        headers = ["Carrier", "Rate / TEU", "Transit", "On-Time %", "Score", "Verdict"]
        table_rows = []
        for r in rows:
            rec_color = (
                C_HIGH if r["rec"] == "BEST VALUE"
                else C_MOD if r["rec"] == "GOOD"
                else C_TEXT3
            )
            table_rows.append([
                _sans(r["carrier"], color=r["color"], weight=600),
                _mono(f"${r['rate_usd']:,}", color=C_TEXT, weight=700),
                _mono(f"{r['transit_days']}d", color=C_TEXT2),
                _mono(f"{r['on_time_pct']}%", color=C_TEXT2),
                _mono(f"{r['score']:.2f}", color=C_ACCENT, weight=600),
                badge(r["rec"], color=rec_color),
            ])
        wsj_market_table(headers, table_rows)
        st.markdown(source_footer(_BOOKING_SOURCES), unsafe_allow_html=True)

        st.caption(
            f"Showing rates for {origin} → {dest} | Cargo: {cargo} | "
            "Score weights: reliability 50%, rate 30%, transit 20%"
        )
    except Exception as exc:
        logger.warning(f"Rate comparison error: {exc}")
        st.info("Rate comparison unavailable.")


# ── Section 3: Optimal Booking Window ─────────────────────────────────────────

def _optimal_booking_window() -> None:
    section_header("Optimal Booking Window",
                   "Historical rate premium by weeks before sailing date")
    try:
        route_sel = st.selectbox("Select Route", _ROUTES, key="bk_window_route")
        seed = _seed_for(route_sel)
        rng = random.Random(seed)

        weeks = list(range(1, 13))
        base_rate = rng.randint(2000, 3500)
        premiums = []
        for w in weeks:
            if w <= 2:
                p = rng.uniform(0.18, 0.30)
            elif w <= 4:
                p = rng.uniform(0.06, 0.14)
            elif w <= 7:
                p = rng.uniform(-0.04, 0.04)
            else:
                p = rng.uniform(0.02, 0.12)
            premiums.append(round(base_rate * (1 + p)))

        colors = []
        for w, rate in zip(weeks, premiums):
            if 4 <= w <= 6:
                colors.append(C_HIGH)
            elif 3 <= w <= 8:
                colors.append(C_MOD)
            elif w <= 2:
                colors.append(C_LOW)
            else:
                colors.append(C_ACCENT)

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=[f"{w}w" for w in weeks],
            y=premiums,
            marker_color=colors,
            text=[f"${r:,}" for r in premiums],
            textposition="outside",
            hovertemplate="<b>%{x} before sailing</b><br>Rate: $%{y:,}/TEU<extra></extra>",
        ))
        sweet_max = max(p for w, p in zip(weeks, premiums) if 4 <= w <= 6)
        fig.add_hrect(
            y0=min(premiums) * 0.98, y1=sweet_max * 1.02,
            fillcolor=_hex_alpha(C_HIGH, 0.08), line_width=0,
            annotation_text="Sweet Spot", annotation_position="top left",
            annotation_font_color=C_HIGH,
        )
        apply_dark_layout(
            fig,
            height=320,
            showlegend=False,
            xaxis=dict(title="Weeks Before Sailing"),
            yaxis=dict(title="Rate ($/TEU)"),
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown(insight_card_html(
            title=f"Sweet spot: 4-6 weeks ahead — {route_sel}",
            score=0.85,
            action="Prioritize",
            rationale=(
                f"Book 4-6 weeks ahead for best rates on {route_sel}. "
                "Late bookings (1-2 weeks out) carry an 18-30% premium. "
                "Booking too early (9+ weeks) may incur an 8-12% premium "
                "due to uncertainty pricing."
            ),
            category="ROUTE",
        ), unsafe_allow_html=True)
        st.markdown(source_footer(_BOOKING_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Booking window error: {exc}")
        st.info("Booking window analysis unavailable.")


# ── Section 4: Contract vs Spot Analysis ──────────────────────────────────────

def _contract_vs_spot_analysis() -> None:
    section_header("Long-Term Contract vs Spot Analysis",
                   "Route-level recommendation: lock in a contract or ride the spot market?")
    try:
        rows = []
        for route in _ROUTES:
            rng = random.Random(_seed_for(route))
            ltc   = rng.randint(1800, 3200)
            spot  = rng.randint(1400, 4000)
            spread = spot - ltc
            vol   = rng.uniform(12, 35)
            brkevn = round(abs(spread) / (ltc * vol / 100), 1)
            if spread > 300:
                rec, rec_color = "USE LTC", C_HIGH
            elif spread < -200:
                rec, rec_color = "RIDE SPOT", C_ACCENT
            else:
                rec, rec_color = "NEUTRAL", C_MOD
            rows.append({
                "route": route, "ltc": ltc, "spot": spot,
                "spread": spread, "vol": vol, "brkevn": brkevn,
                "rec": rec, "rec_color": rec_color,
            })

        headers = ["Route", "LTC Rate", "Spot Rate", "Spread", "Volatility", "Breakeven", "Signal"]
        table_rows = []
        for r in rows:
            sp_color = C_HIGH if r["spread"] > 0 else C_LOW
            sp_sign  = "+" if r["spread"] >= 0 else ""
            table_rows.append([
                _sans(r["route"], color=C_TEXT, weight=600),
                _mono(f"${r['ltc']:,}", color=C_TEXT2),
                _mono(f"${r['spot']:,}", color=C_TEXT2),
                _mono(f"{sp_sign}${r['spread']:,}", color=sp_color, weight=600),
                _mono(f"{r['vol']:.1f}%", color=C_TEXT2),
                _mono(f"{r['brkevn']}x", color=C_TEXT3),
                badge(r["rec"], color=r["rec_color"]),
            ])
        wsj_market_table(headers, table_rows)
        st.markdown(source_footer(_BOOKING_SOURCES), unsafe_allow_html=True)

        st.caption(
            "Spread = Spot minus LTC. Positive spread = spot more expensive = "
            "LTC advantageous. Breakeven = how many rounds of spot avg needed "
            "before LTC breaks even."
        )
    except Exception as exc:
        logger.warning(f"Contract vs spot error: {exc}")
        st.info("Contract vs spot analysis unavailable.")


# ── Section 5: Booking Calendar ───────────────────────────────────────────────

def _booking_calendar() -> None:
    section_header("Booking Calendar — Space Availability",
                   "Color-coded weekly availability for major routes (next 12 weeks)")
    try:
        route_cal = st.selectbox("Route", _ROUTES, key="bk_cal_route")
        rng = random.Random(_seed_for(route_cal + "cal"))

        today = date.today()
        monday = today - timedelta(days=today.weekday())
        weeks = [monday + timedelta(weeks=i) for i in range(12)]

        def avail_color(pct: float) -> tuple[str, str]:
            if pct < 60:
                return C_HIGH, "OPEN"
            if pct < 80:
                return C_MOD, "FILLING"
            if pct < 92:
                return C_LOW, "TIGHT"
            return "#c0392b", "FULL"

        cards = []
        for w in weeks:
            booked = rng.uniform(35, 98)
            color, label = avail_color(booked)
            cards.append({
                "label":    w.strftime("%b %d"),
                "value":    f"{booked:.0f}%",
                "sublabel": label,
                "accent":   color,
            })

        # First six weeks
        metric_card_row(cards[:6], columns=6)
        # Next six weeks
        metric_card_row(cards[6:], columns=6)

        legend = " &nbsp; ".join([
            f"{badge('OPEN', color=C_HIGH)} <60%",
            f"{badge('FILLING', color=C_MOD)} 60-80%",
            f"{badge('TIGHT', color=C_LOW)} 80-92%",
            f"{badge('FULL', color='#c0392b')} >92%",
        ])
        st.markdown(legend, unsafe_allow_html=True)
        st.markdown(source_footer(_BOOKING_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Booking calendar error: {exc}")
        st.info("Booking calendar unavailable.")


# ── Section 6: Spot Rate Alert ─────────────────────────────────────────────────

def _spot_rate_alert() -> None:
    section_header("Spot Rate Alert Configuration",
                   "Get notified when spot rates cross your defined thresholds")
    try:
        c1, c2 = st.columns([2, 1])
        with c1:
            alert_route = st.selectbox("Monitor Route", _ROUTES, key="bk_alert_route")
        with c2:
            alert_type = st.selectbox("Alert Type", ["Falls Below", "Rises Above"],
                                      key="bk_alert_type")

        rng = random.Random(_seed_for(alert_route))
        current_rate = rng.randint(1800, 3800)
        threshold = st.slider("Rate Threshold ($/TEU)", 500, 6000, current_rate, 50,
                              key="bk_alert_thresh")

        diff = current_rate - threshold if alert_type == "Falls Below" else threshold - current_rate
        triggered = diff < 0

        status_color = C_LOW if triggered else C_HIGH
        status_label = "ALERT TRIGGERED" if triggered else "MONITORING"
        gap_label = f"${abs(diff):,}/TEU {'BELOW' if diff < 0 else 'above'} threshold"

        metric_card_row([
            {
                "label":    f"Current Rate — {alert_route}",
                "value":    f"${current_rate:,}/TEU",
                "delta":    gap_label,
                "delta_color": status_color,
                "sublabel": f"Threshold: ${threshold:,}/TEU",
                "accent":   C_ACCENT,
            },
            {
                "label":    "Alert Status",
                "value":    status_label,
                "sublabel": f"Type: {alert_type}",
                "accent":   status_color,
            },
        ], columns=2)

        active_alerts = []
        for route in _ROUTES:
            r2 = random.Random(_seed_for(route + "alert"))
            rt = r2.randint(1800, 3800)
            thr = r2.randint(1600, 4000)
            if abs(rt - thr) < 200:
                active_alerts.append((route, rt, thr))

        section_header("Near-Threshold Routes",
                       "Routes whose current rate is within $200 of their alert threshold")
        if active_alerts:
            near_rows = []
            for route, rt, thr in active_alerts[:3]:
                near_rows.append([
                    _sans(route, color=C_TEXT, weight=600),
                    _mono(f"${rt:,}", color=C_TEXT),
                    _mono(f"${thr:,}", color=C_TEXT2),
                    _mono(f"${abs(rt - thr):,}", color=C_MOD, weight=600),
                ])
            wsj_market_table(
                ["Route", "Current Rate", "Threshold", "Gap"],
                near_rows,
            )
        else:
            st.info("No routes are currently within $200 of an alert threshold.")
        st.markdown(source_footer(_BOOKING_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Spot rate alert error: {exc}")
        st.info("Spot rate alert unavailable.")


# ── Section 7: Space Availability by Carrier ──────────────────────────────────

def _space_availability_by_carrier() -> None:
    section_header("Space Availability by Carrier",
                   "Current vessel space and upcoming sailings across major carriers")
    try:
        today = date.today()
        rows = []
        for c in _CARRIERS:
            for route in _ROUTES[:3]:
                rng = random.Random(_seed_for(c["name"] + route))
                space_left = rng.uniform(4, 45)
                days_out   = rng.randint(3, 28)
                sail_date  = today + timedelta(days=days_out)
                vessel_names = ["Ever Given", "MSC Oscar", "CSCL Globe",
                                "Madrid Maersk", "Cosco Shipping Universe",
                                "HMM Algeciras"]
                vessel = rng.choice(vessel_names)
                rows.append({
                    "carrier": c["name"],
                    "route": route,
                    "space_pct": space_left,
                    "sail_date": sail_date.strftime("%d %b %Y"),
                    "vessel": vessel,
                    "color": c["color"],
                })

        rows.sort(key=lambda x: x["space_pct"], reverse=True)

        headers = ["Carrier", "Route", "Space Left", "Sailing", "Vessel"]
        table_rows = []
        for r in rows:
            sp = r["space_pct"]
            bar_color = C_HIGH if sp > 25 else (C_MOD if sp > 10 else C_LOW)
            bar_pct   = min(sp * 2.2, 100)
            space_cell = (
                _mono(f"{sp:.0f}%", color=bar_color, weight=600)
                + f'<div class="progress-bar-custom">'
                f'<span class="progress-bar-fill" style="display:block;width:{bar_pct}%;background:{bar_color};"></span>'
                f'</div>'
            )
            table_rows.append([
                _sans(r["carrier"], color=r["color"], weight=600),
                _sans(r["route"], color=C_TEXT2),
                space_cell,
                _sans(r["sail_date"], color=C_TEXT2),
                _sans(r["vessel"], color=C_TEXT3),
            ])
        wsj_market_table(headers, table_rows)
        st.markdown(source_footer(_BOOKING_SOURCES), unsafe_allow_html=True)

        st.caption(
            f"Space remaining as % of vessel TEU capacity. "
            f"Updated: {datetime.now().strftime('%H:%M UTC')}"
        )
    except Exception as exc:
        logger.warning(f"Space availability error: {exc}")
        st.info("Space availability data unavailable.")


# ── Main render ────────────────────────────────────────────────────────────────

def render(route_results=None, freight_data=None, port_results=None, *args, **kwargs) -> None:
    """Render the Booking Intelligence & Optimization tab."""
    try:
        page_header(
            title="Booking Intelligence & Optimization",
            subtitle=(
                "Market-timed booking decisions · Rate comparison across carriers · "
                "Contract vs spot analytics · Space availability tracker"
            ),
            badge_text="BOOKING",
            badge_color=C_ACCENT,
        )

        _booking_market_dashboard(freight_data)
        section_divider("Rate Comparison")
        _rate_comparison_tool()
        section_divider("Timing the Market")
        _optimal_booking_window()
        _contract_vs_spot_analysis()
        section_divider("Capacity Planning")
        _booking_calendar()
        _spot_rate_alert()
        _space_availability_by_carrier()
    except Exception as exc:
        logger.exception(f"tab_booking.render() fatal: {exc}")
        st.error(f"Booking tab render error: {exc}")
