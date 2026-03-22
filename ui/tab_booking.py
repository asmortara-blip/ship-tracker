"""
Booking Intelligence & Optimization Tab

Shipping booking intelligence suite: market dashboard, rate comparison,
optimal booking window, contract vs spot analysis, booking calendar,
spot rate alerts, and space availability by carrier.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

import pandas as pd
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

# ── Mock data ──────────────────────────────────────────────────────────────────
_ORIGINS = ["Shanghai", "Ningbo", "Shenzhen", "Singapore", "Rotterdam",
            "Hamburg", "Los Angeles", "New York", "Busan", "Colombo"]
_DESTINATIONS = ["Rotterdam", "Hamburg", "Los Angeles", "New York", "Felixstowe",
                 "Singapore", "Dubai", "Sydney", "Mumbai", "Santos"]
_CARGO_TYPES = ["General Cargo", "Electronics", "Machinery", "Apparel",
                "Chemicals", "Reefer", "Hazmat", "Automotive"]
_CARRIERS = [
    {"name": "COSCO",         "code": "COSU", "reliability": 91, "color": C_HIGH},
    {"name": "Maersk",        "code": "MAEU", "reliability": 89, "color": C_ACCENT},
    {"name": "MSC",           "code": "MSCU", "reliability": 87, "color": C_MOD},
    {"name": "CMA CGM",       "code": "CMDU", "reliability": 85, "color": C_PURPLE},
    {"name": "Hapag-Lloyd",   "code": "HLCU", "reliability": 88, "color": C_CYAN},
]
_ROUTES = [
    "Shanghai → Rotterdam",
    "Shenzhen → Los Angeles",
    "Singapore → Hamburg",
    "Busan → New York",
    "Rotterdam → New York",
]


def _seed_for(key: str) -> int:
    return abs(hash(key)) % 10000


def _kpi_card(label: str, value: str, delta: str, delta_good: bool,
              sub: str = "", accent: str = C_ACCENT) -> str:
    delta_color = C_HIGH if delta_good else C_LOW
    sub_html = f'<div style="font-size:11px;color:{C_TEXT3};margin-top:4px">{sub}</div>' if sub else ""
    return f"""
<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;
padding:18px 20px;height:100%;">
  <div style="font-size:12px;color:{C_TEXT2};letter-spacing:0.5px;text-transform:uppercase;
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


# ── Section 1: Booking Market Dashboard ───────────────────────────────────────

def _booking_market_dashboard(freight_data) -> None:
    _section_header("Booking Market Dashboard",
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

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.markdown(_kpi_card(
                "Booking Volume (TEU)",
                f"{volume:,}",
                f"{'▲' if vol_delta >= 0 else '▼'} {abs(vol_delta)}% vs last week",
                vol_delta >= 0,
                "7-day rolling",
                C_ACCENT
            ), unsafe_allow_html=True)
        with c2:
            st.markdown(_kpi_card(
                "Avg Booking Lead Time",
                f"{lead_time:.1f} days",
                f"{'▲' if lead_delta >= 0 else '▼'} {abs(lead_delta):.1f}d WoW",
                lead_delta <= 0,
                "Days before sailing",
                C_MOD
            ), unsafe_allow_html=True)
        with c3:
            st.markdown(_kpi_card(
                "Space Availability",
                f"{space_pct:.0f}% booked",
                f"{'▲' if sp_delta >= 0 else '▼'} {abs(sp_delta):.1f}% WoW",
                sp_delta <= 0,
                f"{100 - space_pct:.0f}% remaining",
                C_LOW if space_pct > 85 else C_HIGH
            ), unsafe_allow_html=True)
        with c4:
            st.markdown(_kpi_card(
                "RFQ vs Spot Differential",
                f"+${rfq_spot:.0f}/TEU",
                "RFQ premium over spot",
                rfq_spot < 100,
                "Neg = spot cheaper",
                C_PURPLE
            ), unsafe_allow_html=True)
        with c5:
            sign = "+" if ctc_spot >= 0 else ""
            st.markdown(_kpi_card(
                "Contract vs Spot",
                f"{sign}${ctc_spot:.0f}/TEU",
                "Contract premium" if ctc_spot >= 0 else "Contract discount",
                ctc_spot <= 0,
                "LTC vs spot market",
                C_CYAN
            ), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Booking dashboard error: {exc}")
        st.info("Booking dashboard data unavailable.")


# ── Section 2: Rate Comparison Tool ───────────────────────────────────────────

def _rate_comparison_tool() -> None:
    _section_header("Rate Comparison Tool",
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

        header = f"""
<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:14px;
overflow:hidden;margin-top:12px">
  <div style="display:grid;grid-template-columns:1.4fr 1fr 1fr 1fr 1fr 1fr;
  padding:10px 16px;border-bottom:1px solid {C_BORDER};font-size:11px;
  color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.5px">
    <span>Carrier</span><span>Rate / TEU</span><span>Transit</span>
    <span>On-Time %</span><span>Score</span><span>Verdict</span>
  </div>"""
        st.markdown(header, unsafe_allow_html=True)

        for r in rows:
            rec_color = C_HIGH if r["rec"] == "BEST VALUE" else (C_MOD if r["rec"] == "GOOD" else C_TEXT3)
            st.markdown(f"""
<div style="display:grid;grid-template-columns:1.4fr 1fr 1fr 1fr 1fr 1fr;
padding:12px 16px;border-bottom:1px solid {C_BORDER};align-items:center">
  <span style="font-weight:600;color:{r['color']}">{r['carrier']}</span>
  <span style="color:{C_TEXT};font-size:15px;font-weight:700">${r['rate_usd']:,}</span>
  <span style="color:{C_TEXT2}">{r['transit_days']}d</span>
  <span style="color:{C_TEXT2}">{r['on_time_pct']}%</span>
  <span style="color:{C_ACCENT}">{r['score']:.2f}</span>
  <span style="color:{rec_color};font-weight:600;font-size:12px">{r['rec']}</span>
</div>""", unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)
        st.caption(f"Showing rates for {origin} → {dest} | Cargo: {cargo} | Score weights: reliability 50%, rate 30%, transit 20%")
    except Exception as exc:
        logger.warning(f"Rate comparison error: {exc}")
        st.info("Rate comparison unavailable.")


# ── Section 3: Optimal Booking Window ─────────────────────────────────────────

def _optimal_booking_window() -> None:
    _section_header("Optimal Booking Window",
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
        fig.add_hrect(y0=min(premiums) * 0.98, y1=max(p for w, p in zip(weeks, premiums) if 4 <= w <= 6) * 1.02,
                      fillcolor="rgba(16,185,129,0.08)", line_width=0,
                      annotation_text="Sweet Spot", annotation_position="top left",
                      annotation_font_color=C_HIGH)
        fig.update_layout(
            plot_bgcolor=C_SURFACE, paper_bgcolor=C_CARD,
            font_color=C_TEXT2, margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(title="Weeks Before Sailing", gridcolor=C_BORDER),
            yaxis=dict(title="Rate ($/TEU)", gridcolor=C_BORDER),
            height=320, showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown(f"""
<div style="background:rgba(16,185,129,0.1);border:1px solid {C_HIGH};border-radius:10px;
padding:14px 18px;margin-top:4px;display:flex;align-items:center;gap:12px">
  <span style="font-size:22px">&#128200;</span>
  <div>
    <span style="color:{C_HIGH};font-weight:700;font-size:14px">INSIGHT: </span>
    <span style="color:{C_TEXT};font-size:13px">Book <b>4-6 weeks ahead</b> for best rates on {route_sel}.
    Late bookings (1-2 weeks out) carry a <b>18-30% premium</b>. Booking too early (9+ weeks)
    may incur an 8-12% premium due to uncertainty pricing.</span>
  </div>
</div>""", unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Booking window error: {exc}")
        st.info("Booking window analysis unavailable.")


# ── Section 4: Contract vs Spot Analysis ──────────────────────────────────────

def _contract_vs_spot_analysis() -> None:
    _section_header("Long-Term Contract vs Spot Analysis",
                    "Route-level recommendation: lock in a contract or ride the spot market?")
    try:
        rows = []
        for i, route in enumerate(_ROUTES):
            rng = random.Random(_seed_for(route))
            ltc   = rng.randint(1800, 3200)
            spot  = rng.randint(1400, 4000)
            spread = spot - ltc
            vol   = rng.uniform(12, 35)
            brkevn = round(abs(spread) / (ltc * vol / 100), 1)
            if spread > 300:
                rec = "USE LTC"
                rec_color = C_HIGH
            elif spread < -200:
                rec = "RIDE SPOT"
                rec_color = C_ACCENT
            else:
                rec = "NEUTRAL"
                rec_color = C_MOD
            rows.append({
                "route": route, "ltc": ltc, "spot": spot,
                "spread": spread, "vol": vol, "brkevn": brkevn,
                "rec": rec, "rec_color": rec_color,
            })

        header = f"""
<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:14px;overflow:hidden">
  <div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr 1fr 1fr;
  padding:10px 16px;border-bottom:1px solid {C_BORDER};font-size:11px;
  color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.5px">
    <span>Route</span><span>LTC Rate</span><span>Spot Rate</span>
    <span>Spread</span><span>Volatility</span><span>Breakeven</span><span>Signal</span>
  </div>"""
        st.markdown(header, unsafe_allow_html=True)

        for r in rows:
            sp_color = C_HIGH if r["spread"] > 0 else C_LOW
            sp_sign  = "+" if r["spread"] >= 0 else ""
            st.markdown(f"""
<div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr 1fr 1fr;
padding:12px 16px;border-bottom:1px solid {C_BORDER};align-items:center">
  <span style="color:{C_TEXT};font-weight:600;font-size:13px">{r['route']}</span>
  <span style="color:{C_TEXT2}">${r['ltc']:,}</span>
  <span style="color:{C_TEXT2}">${r['spot']:,}</span>
  <span style="color:{sp_color};font-weight:600">{sp_sign}${r['spread']:,}</span>
  <span style="color:{C_TEXT2}">{r['vol']:.1f}%</span>
  <span style="color:{C_TEXT3}">{r['brkevn']}x</span>
  <span style="background:rgba(255,255,255,0.05);border-radius:6px;padding:3px 8px;
  color:{r['rec_color']};font-size:11px;font-weight:700">{r['rec']}</span>
</div>""", unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)
        st.caption("Spread = Spot minus LTC. Positive spread = spot more expensive = LTC advantageous. Breakeven = how many rounds of spot avg needed before LTC breaks even.")
    except Exception as exc:
        logger.warning(f"Contract vs spot error: {exc}")
        st.info("Contract vs spot analysis unavailable.")


# ── Section 5: Booking Calendar ───────────────────────────────────────────────

def _booking_calendar() -> None:
    _section_header("Booking Calendar — Space Availability",
                    "Color-coded weekly availability for major routes (next 12 weeks)")
    try:
        route_cal = st.selectbox("Route", _ROUTES, key="bk_cal_route")
        rng = random.Random(_seed_for(route_cal + "cal"))

        today = date.today()
        monday = today - timedelta(days=today.weekday())
        weeks = [monday + timedelta(weeks=i) for i in range(12)]

        def avail_color(pct):
            if pct < 60:
                return C_HIGH, "OPEN"
            elif pct < 80:
                return C_MOD, "FILLING"
            elif pct < 92:
                return C_LOW, "TIGHT"
            else:
                return "#ef4444", "FULL"

        cells_html = ""
        for w in weeks:
            booked = rng.uniform(35, 98)
            color, label = avail_color(booked)
            wk_label = w.strftime("%b %d")
            cells_html += f"""
<div style="background:{C_CARD};border:1px solid {color}33;border-radius:10px;
padding:14px 10px;text-align:center;border-top:3px solid {color}">
  <div style="font-size:11px;color:{C_TEXT3};margin-bottom:4px">{wk_label}</div>
  <div style="font-size:18px;font-weight:700;color:{color}">{booked:.0f}%</div>
  <div style="font-size:10px;color:{color};font-weight:600;margin-top:2px">{label}</div>
</div>"""

        st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-top:8px">
  {cells_html}
</div>""", unsafe_allow_html=True)

        st.markdown(f"""
<div style="display:flex;gap:18px;margin-top:12px;font-size:12px;color:{C_TEXT2}">
  <span><span style="color:{C_HIGH}">&#9632;</span> OPEN (&lt;60% booked)</span>
  <span><span style="color:{C_MOD}">&#9632;</span> FILLING (60-80%)</span>
  <span><span style="color:{C_LOW}">&#9632;</span> TIGHT (80-92%)</span>
  <span><span style="color:#ef4444">&#9632;</span> FULL (&gt;92%)</span>
</div>""", unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Booking calendar error: {exc}")
        st.info("Booking calendar unavailable.")


# ── Section 6: Spot Rate Alert ─────────────────────────────────────────────────

def _spot_rate_alert() -> None:
    _section_header("Spot Rate Alert Configuration",
                    "Get notified when spot rates cross your defined thresholds")
    try:
        c1, c2 = st.columns([2, 1])
        with c1:
            alert_route = st.selectbox("Monitor Route", _ROUTES, key="bk_alert_route")
        with c2:
            alert_type = st.selectbox("Alert Type", ["Falls Below", "Rises Above"], key="bk_alert_type")

        rng = random.Random(_seed_for(alert_route))
        current_rate = rng.randint(1800, 3800)
        threshold = st.slider("Rate Threshold ($/TEU)", 500, 6000, current_rate, 50, key="bk_alert_thresh")

        diff = current_rate - threshold if alert_type == "Falls Below" else threshold - current_rate
        triggered = diff < 0

        status_color = C_LOW if triggered else C_HIGH
        status_label = "ALERT TRIGGERED" if triggered else "MONITORING"
        gap_label = f"${abs(diff):,}/TEU {'BELOW' if diff < 0 else 'above'} threshold"

        st.markdown(f"""
<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;
padding:18px 22px;margin-top:10px;display:flex;justify-content:space-between;align-items:center">
  <div>
    <div style="font-size:12px;color:{C_TEXT3};margin-bottom:4px">Current Rate — {alert_route}</div>
    <div style="font-size:28px;font-weight:700;color:{C_TEXT}">${current_rate:,}<span style="font-size:14px;color:{C_TEXT3}">/TEU</span></div>
    <div style="font-size:12px;color:{C_TEXT2};margin-top:4px">{gap_label}</div>
  </div>
  <div style="text-align:right">
    <div style="font-size:11px;color:{C_TEXT3};margin-bottom:6px">Status</div>
    <div style="background:{status_color}22;border:1px solid {status_color};border-radius:8px;
    padding:8px 16px;color:{status_color};font-weight:700;font-size:13px">{status_label}</div>
    <div style="font-size:11px;color:{C_TEXT3};margin-top:6px">Threshold: ${threshold:,}/TEU</div>
  </div>
</div>""", unsafe_allow_html=True)

        active_alerts = []
        for route in _ROUTES:
            r2 = random.Random(_seed_for(route + "alert"))
            rt = r2.randint(1800, 3800)
            thr = r2.randint(1600, 4000)
            if abs(rt - thr) < 200:
                active_alerts.append((route, rt, thr))

        if active_alerts:
            st.markdown(f"""
<div style="margin-top:14px;padding:12px 16px;background:rgba(245,158,11,0.1);
border:1px solid {C_MOD};border-radius:10px">
  <div style="font-size:12px;font-weight:700;color:{C_MOD};margin-bottom:8px">
    NEAR-THRESHOLD ROUTES
  </div>""", unsafe_allow_html=True)
            for route, rt, thr in active_alerts[:3]:
                st.markdown(f"""
  <div style="font-size:12px;color:{C_TEXT2};padding:3px 0">
    {route}: ${rt:,} vs threshold ${thr:,}
    <span style="color:{C_MOD}"> — within ${abs(rt-thr):,}</span>
  </div>""", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Spot rate alert error: {exc}")
        st.info("Spot rate alert unavailable.")


# ── Section 7: Space Availability by Carrier ──────────────────────────────────

def _space_availability_by_carrier() -> None:
    _section_header("Space Availability by Carrier",
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
                vessel_names = ["Ever Given", "MSC Oscar", "CSCL Globe", "Madrid Maersk",
                                "Cosco Shipping Universe", "HMM Algeciras"]
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

        st.markdown(f"""
<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:14px;overflow:hidden">
  <div style="display:grid;grid-template-columns:1.2fr 2fr 1fr 1fr 1.4fr;
  padding:10px 16px;border-bottom:1px solid {C_BORDER};font-size:11px;
  color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.5px">
    <span>Carrier</span><span>Route</span><span>Space Left</span>
    <span>Sailing</span><span>Vessel</span>
  </div>""", unsafe_allow_html=True)

        for r in rows:
            sp = r["space_pct"]
            bar_color = C_HIGH if sp > 25 else (C_MOD if sp > 10 else C_LOW)
            bar_pct   = min(sp * 2.2, 100)
            st.markdown(f"""
<div style="display:grid;grid-template-columns:1.2fr 2fr 1fr 1fr 1.4fr;
padding:11px 16px;border-bottom:1px solid {C_BORDER};align-items:center">
  <span style="font-weight:600;color:{r['color']}">{r['carrier']}</span>
  <span style="color:{C_TEXT2};font-size:12px">{r['route']}</span>
  <span>
    <div style="font-size:12px;color:{bar_color};font-weight:600;margin-bottom:3px">{sp:.0f}%</div>
    <div style="height:4px;background:{C_BORDER};border-radius:2px">
      <div style="height:4px;width:{bar_pct}%;background:{bar_color};border-radius:2px"></div>
    </div>
  </span>
  <span style="color:{C_TEXT2};font-size:12px">{r['sail_date']}</span>
  <span style="color:{C_TEXT3};font-size:12px">{r['vessel']}</span>
</div>""", unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)
        st.caption(f"Space remaining as % of vessel TEU capacity. Updated: {datetime.now().strftime('%H:%M UTC')}")
    except Exception as exc:
        logger.warning(f"Space availability error: {exc}")
        st.info("Space availability data unavailable.")


# ── Main render ────────────────────────────────────────────────────────────────

def render(route_results=None, freight_data=None, port_results=None) -> None:
    """Render the Booking Intelligence & Optimization tab."""
    try:
        st.markdown(f"""
<div style="background:linear-gradient(135deg,{C_ACCENT}18,{C_PURPLE}10);
border:1px solid {C_BORDER};border-radius:16px;padding:22px 26px;margin-bottom:24px">
  <div style="font-size:22px;font-weight:800;color:{C_TEXT}">
    Booking Intelligence &amp; Optimization
  </div>
  <div style="font-size:13px;color:{C_TEXT2};margin-top:6px">
    Market-timed booking decisions · Rate comparison across carriers ·
    Contract vs spot analytics · Space availability tracker
  </div>
</div>""", unsafe_allow_html=True)
    except Exception:
        st.subheader("Booking Intelligence & Optimization")

    _booking_market_dashboard(freight_data)
    _rate_comparison_tool()
    _optimal_booking_window()
    _contract_vs_spot_analysis()
    _booking_calendar()
    _spot_rate_alert()
    _space_availability_by_carrier()
