"""tab_ecommerce.py — E-commerce demand pulse dashboard for container shipping.

Sections:
  1. E-commerce Demand Pulse        — 4-platform card grid
  2. Retail Calendar                — 12-month demand heat strip
  3. Booking Window Alerts          — urgency callout cards
  4. Platform Shipping Mode Analysis — air vs ocean bar chart
  5. De Minimis Impact              — policy risk explainer
  6. Forecast: Next 90 Days         — trans-Pacific demand line chart
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.ecommerce_tracker import (
    ECOMMERCE_SIGNALS,
    RETAIL_CALENDAR,
    compute_ecommerce_demand_index,
    get_seasonal_booking_windows,
)

# ── Color palette ──────────────────────────────────────────────────────────────
C_BG     = "#0a0f1a"
C_CARD   = "#1a2235"
C_BORDER = "rgba(255,255,255,0.08)"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_TEXT3  = "#64748b"
C_HIGH   = "#10b981"
C_ACCENT = "#3b82f6"
C_WARN   = "#f59e0b"
C_DANGER = "#ef4444"
C_PURPLE = "#8b5cf6"
C_CYAN   = "#06b6d4"
C_ORANGE = "#f97316"

# Platform brand colors
_PLATFORM_COLORS: dict[str, str] = {
    "AMAZON":  "#ff9900",
    "ALIBABA": "#ff6900",
    "SHEIN":   "#e91e8c",
    "TEMU":    "#e53935",
    "SHOPIFY": "#96bf48",
    "WAYFAIR": "#7f187f",
}

_PLATFORM_ICONS: dict[str, str] = {
    "AMAZON":  "A",
    "ALIBABA": "阿",
    "SHEIN":   "S",
    "TEMU":    "T",
    "SHOPIFY": "SH",
    "WAYFAIR": "W",
}

_PLATFORM_LABELS: dict[str, str] = {
    "AMAZON":  "Amazon",
    "ALIBABA": "Alibaba",
    "SHEIN":   "SHEIN",
    "TEMU":    "Temu",
    "SHOPIFY": "Shopify",
    "WAYFAIR": "Wayfair",
}

# Air vs ocean freight splits by platform (percent air)
_PLATFORM_AIR_PCT: dict[str, float] = {
    "AMAZON":  20.0,
    "ALIBABA":  5.0,
    "SHEIN":   80.0,
    "TEMU":    85.0,
    "SHOPIFY": 15.0,
    "WAYFAIR":  8.0,
}

# Monthly demand index (trans-Pacific EB); mirrors ecommerce_tracker internal data
_TP_MONTHLY: dict[int, float] = {
    1: 0.85, 2: 0.70, 3: 0.80, 4: 0.90,
    5: 1.10, 6: 1.25, 7: 1.40, 8: 1.45,
    9: 1.35, 10: 1.20, 11: 1.00, 12: 0.90,
}

_MONTH_NAMES = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

_URGENCY_COLOR: dict[str, str] = {
    "CRITICAL": C_DANGER,
    "HIGH":     C_WARN,
    "MODERATE": C_ACCENT,
    "MONITOR":  C_HIGH,
}

_URGENCY_ICON: dict[str, str] = {
    "CRITICAL": "⚡",
    "HIGH":     "🔴",
    "MODERATE": "📅",
    "MONITOR":  "✅",
}


# ── HTML helpers ───────────────────────────────────────────────────────────────

def _section_title(text: str, subtitle: str = "") -> None:
    sub_html = (
        '<div style="color:' + C_TEXT2 + '; font-size:0.83rem; margin-top:3px">'
        + subtitle + "</div>"
        if subtitle else ""
    )
    st.markdown(
        '<div style="margin-bottom:14px; margin-top:8px">'
        '<div style="font-size:1.05rem; font-weight:700; color:' + C_TEXT + '">'
        + text + "</div>"
        + sub_html + "</div>",
        unsafe_allow_html=True,
    )


def _card(content_html: str, border_color: str = C_BORDER) -> str:
    return (
        '<div style="background:' + C_CARD + '; border:1px solid ' + border_color + ';'
        ' border-radius:12px; padding:16px 18px; margin-bottom:10px; height:100%">'
        + content_html + "</div>"
    )


def _badge(text: str, color: str) -> str:
    return (
        '<span style="background:' + color + '22; color:' + color + '; border:1px solid '
        + color + '55; border-radius:6px; padding:2px 8px; font-size:0.72rem;'
        ' font-weight:700; letter-spacing:0.05em">' + text + "</span>"
    )


def _platform_logo(platform: str, size: int = 44) -> str:
    color = _PLATFORM_COLORS.get(platform, C_ACCENT)
    icon  = _PLATFORM_ICONS.get(platform, platform[:1])
    sz    = str(size)
    fsz   = str(int(size * 0.38))
    return (
        '<div style="width:' + sz + 'px; height:' + sz + 'px; border-radius:50%;'
        ' background:' + color + '22; border:2px solid ' + color + '66;'
        ' display:flex; align-items:center; justify-content:center;'
        ' font-size:' + fsz + 'px; font-weight:900; color:' + color + ';'
        ' margin-bottom:10px">' + icon + "</div>"
    )


def _route_pill(route: str) -> str:
    labels = {
        "transpacific_eb": "Trans-Pac EB",
        "transpacific_wb": "Trans-Pac WB",
        "asia_europe":     "Asia-Europe",
        "intra_asia_sea":  "Intra-Asia",
        "us_mexico":       "US-Mexico",
        "gulf_coast_inbound": "Gulf Coast",
        "asia_latam":      "Asia-LATAM",
    }
    label = labels.get(route, route)
    return (
        '<span style="background:rgba(59,130,246,0.12); color:' + C_ACCENT + ';'
        ' border:1px solid rgba(59,130,246,0.30); border-radius:5px;'
        ' padding:1px 7px; font-size:0.68rem; margin-right:4px">' + label + "</span>"
    )


# ── Section 1: E-commerce Demand Pulse ────────────────────────────────────────

def _render_platform_cards() -> None:
    logger.debug("Rendering e-commerce platform demand pulse cards")
    _section_title(
        "E-Commerce Demand Pulse",
        "Real-time shipping signals from major platforms — 2025/2026 data",
    )

    primary_platforms = ["AMAZON", "ALIBABA", "SHEIN", "SHOPIFY"]
    cols = st.columns(4)

    for i, platform in enumerate(primary_platforms):
        signals = ECOMMERCE_SIGNALS.get(platform, [])
        if not signals:
            continue

        # Use first signal as the headline
        lead = signals[0]
        color = _PLATFORM_COLORS.get(platform, C_ACCENT)
        label = _PLATFORM_LABELS.get(platform, platform)

        growth_str = (
            "+" + str(round(lead.yoy_growth_pct, 0)).rstrip("0").rstrip(".") + "% YoY"
            if lead.yoy_growth_pct >= 0
            else str(round(lead.yoy_growth_pct, 0)).rstrip("0").rstrip(".") + "% YoY"
        )
        growth_color = C_HIGH if lead.yoy_growth_pct >= 0 else C_DANGER

        # Build route pills HTML
        routes_html = "".join(_route_pill(r) for r in lead.affected_routes[:3])

        conf_pct = str(int(lead.confidence * 100))

        signal_preview = (
            lead.shipping_implication[:120] + "…"
            if len(lead.shipping_implication) > 120
            else lead.shipping_implication
        )

        content = (
            _platform_logo(platform, 44)
            + '<div style="font-size:1.0rem; font-weight:800; color:' + C_TEXT + '; margin-bottom:4px">'
            + label + "</div>"
            + '<div style="margin-bottom:8px">'
            + _badge(growth_str, growth_color)
            + "&nbsp;"
            + _badge("Conf " + conf_pct + "%", C_TEXT3)
            + "</div>"
            + '<div style="font-size:0.75rem; color:' + C_TEXT2 + '; margin-bottom:10px; line-height:1.45">'
            + signal_preview + "</div>"
            + '<div style="font-size:0.70rem; color:' + C_TEXT3 + '; margin-bottom:6px">AFFECTED ROUTES</div>'
            + routes_html
            + '<div style="font-size:0.70rem; color:' + C_TEXT3 + '; margin-top:10px">'
            + "Lead time: <b style='color:" + color + "'>" + str(lead.lead_time_weeks) + " wks</b>"
            + "</div>"
        )

        with cols[i]:
            st.markdown(_card(content, border_color=color + "44"), unsafe_allow_html=True)

    # Second row: all signals expander
    with st.expander("View all platform signals", expanded=False):
        for platform, signals in ECOMMERCE_SIGNALS.items():
            color = _PLATFORM_COLORS.get(platform, C_ACCENT)
            label = _PLATFORM_LABELS.get(platform, platform)
            st.markdown(
                '<div style="font-size:0.90rem; font-weight:700; color:' + color + '; margin:12px 0 6px">'
                + label + "</div>",
                unsafe_allow_html=True,
            )
            for sig in signals:
                growth_str = (
                    "+" + str(round(sig.yoy_growth_pct, 1)) + "% YoY"
                    if sig.yoy_growth_pct >= 0
                    else str(round(sig.yoy_growth_pct, 1)) + "% YoY"
                )
                routes_html = "".join(_route_pill(r) for r in sig.affected_routes)
                st.markdown(
                    '<div style="background:rgba(26,34,53,0.7); border-left:3px solid ' + color + '55;'
                    ' padding:10px 14px; margin-bottom:8px; border-radius:0 8px 8px 0">'
                    '<div style="font-size:0.78rem; font-weight:700; color:' + C_TEXT + '; margin-bottom:4px">'
                    + sig.metric_name + " &nbsp;" + _badge(growth_str, C_HIGH if sig.yoy_growth_pct >= 0 else C_DANGER)
                    + "</div>"
                    + '<div style="font-size:0.74rem; color:' + C_TEXT2 + '; margin-bottom:8px; line-height:1.45">'
                    + sig.shipping_implication + "</div>"
                    + routes_html + "</div>",
                    unsafe_allow_html=True,
                )


# ── Section 2: Retail Calendar ─────────────────────────────────────────────────

def _demand_color(idx: float) -> str:
    """Map demand index to a color from green → amber → red."""
    if idx >= 1.40:
        return "#ef4444"
    if idx >= 1.25:
        return "#f97316"
    if idx >= 1.10:
        return "#f59e0b"
    if idx >= 0.95:
        return "#84cc16"
    return "#10b981"


def _render_retail_calendar() -> None:
    logger.debug("Rendering retail calendar — 12-month heat strip")
    _section_title(
        "Retail Calendar — 12-Month Trans-Pacific Demand",
        "Color-coded booking pressure | Key events | Book-by annotations",
    )

    today = date.today()

    # Build event lookup: month -> list of event names
    event_by_month: dict[int, list[str]] = {}
    for cal in RETAIL_CALENDAR:
        event_by_month.setdefault(cal.month, []).append(cal.event_name)

    # Build booking window lookup: which months are booking windows for upcoming events
    booking_by_month: dict[int, list[str]] = {}
    for cal in RETAIL_CALENDAR:
        bw_month = cal.month - int(math.ceil(cal.typical_order_window_weeks_before / 4.33))
        bw_month = ((bw_month - 1) % 12) + 1
        booking_by_month.setdefault(bw_month, []).append("Book: " + cal.event_name)

    # Render 12 months starting from current month
    months_html = '<div style="display:flex; gap:6px; overflow-x:auto; padding-bottom:6px">'
    for offset in range(12):
        m = ((today.month - 1 + offset) % 12) + 1
        year_offset = (today.month - 1 + offset) // 12
        yr = today.year + year_offset

        idx = _TP_MONTHLY[m]
        bg  = _demand_color(idx)
        is_current = (m == today.month and yr == today.year)

        events     = event_by_month.get(m, [])
        bookings   = booking_by_month.get(m, [])

        event_dots = "".join(
            '<div style="width:6px; height:6px; border-radius:50%; background:'
            + C_TEXT + '; margin:1px auto" title="' + e + '"></div>'
            for e in events
        )
        booking_dots = "".join(
            '<div style="width:6px; height:6px; border-radius:50%; background:'
            + C_WARN + '; margin:1px auto" title="' + b + '"></div>'
            for b in bookings
        )

        border_style = (
            "3px solid " + C_TEXT
            if is_current
            else "1px solid " + bg + "55"
        )
        curr_label = (
            '<div style="font-size:0.58rem; color:' + C_TEXT + '; text-align:center;'
            ' font-weight:700; margin-top:2px">NOW</div>'
            if is_current else ""
        )
        idx_str = str(round(idx, 2))

        cell = (
            '<div style="min-width:72px; background:' + bg + '1a; border:' + border_style + ';'
            ' border-radius:10px; padding:10px 6px; text-align:center; flex:1">'
            + curr_label
            + '<div style="font-size:0.80rem; font-weight:700; color:' + C_TEXT + '">'
            + _MONTH_NAMES[m] + "</div>"
            + '<div style="font-size:0.68rem; color:' + C_TEXT3 + '; margin-bottom:6px">' + str(yr) + "</div>"
            + '<div style="font-size:1.1rem; font-weight:900; color:' + bg + '; margin-bottom:6px">'
            + idx_str + "x</div>"
            + '<div style="font-size:0.60rem; color:' + C_TEXT3 + '; margin-bottom:4px">DEMAND IDX</div>'
            + event_dots
            + booking_dots
            + "</div>"
        )
        months_html += cell

    months_html += "</div>"

    # Legend
    legend_html = (
        '<div style="display:flex; gap:16px; margin-top:10px; flex-wrap:wrap">'
        '<div style="display:flex; align-items:center; gap:5px">'
        '<div style="width:10px; height:10px; border-radius:50%; background:#ef4444"></div>'
        '<span style="font-size:0.72rem; color:' + C_TEXT2 + '">Very High (1.40x+)</span></div>'
        '<div style="display:flex; align-items:center; gap:5px">'
        '<div style="width:10px; height:10px; border-radius:50%; background:#f97316"></div>'
        '<span style="font-size:0.72rem; color:' + C_TEXT2 + '">High (1.25x+)</span></div>'
        '<div style="display:flex; align-items:center; gap:5px">'
        '<div style="width:10px; height:10px; border-radius:50%; background:#f59e0b"></div>'
        '<span style="font-size:0.72rem; color:' + C_TEXT2 + '">Moderate (1.10x+)</span></div>'
        '<div style="display:flex; align-items:center; gap:5px">'
        '<div style="width:10px; height:10px; border-radius:50%; background:#10b981"></div>'
        '<span style="font-size:0.72rem; color:' + C_TEXT2 + '">Low (&lt;0.95x)</span></div>'
        '<div style="display:flex; align-items:center; gap:5px">'
        '<div style="width:10px; height:10px; border-radius:50%; background:' + C_TEXT + '"></div>'
        '<span style="font-size:0.72rem; color:' + C_TEXT2 + '">Retail event</span></div>'
        '<div style="display:flex; align-items:center; gap:5px">'
        '<div style="width:10px; height:10px; border-radius:50%; background:' + C_WARN + '"></div>'
        '<span style="font-size:0.72rem; color:' + C_TEXT2 + '">Booking window</span></div>'
        "</div>"
    )

    st.markdown(months_html + legend_html, unsafe_allow_html=True)

    # Event detail table
    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander("Key event details & book-by dates", expanded=False):
        for cal in sorted(RETAIL_CALENDAR, key=lambda c: (c.month, c.day)):
            bw_month_raw = cal.month - int(math.ceil(cal.typical_order_window_weeks_before / 4.33))
            bw_month = ((bw_month_raw - 1) % 12) + 1
            bw_label = _MONTH_NAMES[bw_month]
            mult_str = str(round(cal.container_demand_multiplier, 2)) + "x"
            routes_html = "".join(_route_pill(r) for r in cal.affected_routes)
            st.markdown(
                '<div style="border-left:3px solid ' + C_ACCENT + '44; padding:8px 14px;'
                ' margin-bottom:8px; border-radius:0 8px 8px 0">'
                '<div style="display:flex; justify-content:space-between; align-items:center">'
                '<span style="font-weight:700; color:' + C_TEXT + '; font-size:0.85rem">'
                + cal.event_name + "</span>"
                + _badge(mult_str + " demand", C_WARN)
                + "</div>"
                + '<div style="font-size:0.73rem; color:' + C_TEXT2 + '; margin:4px 0">'
                + cal.description + "</div>"
                + '<div style="font-size:0.70rem; color:' + C_TEXT3 + '; margin-top:6px">'
                + "Book containers in: <b style='color:" + C_WARN + "'>" + bw_label + "</b>"
                + " &nbsp;|&nbsp; Weeks lead time: <b style='color:" + C_ACCENT + "'>"
                + str(cal.typical_order_window_weeks_before) + " wks</b> &nbsp;"
                + "</div>"
                + '<div style="margin-top:6px">' + routes_html + "</div>"
                + "</div>",
                unsafe_allow_html=True,
            )


# ── Section 3: Booking Window Alerts ──────────────────────────────────────────

def _render_booking_alerts() -> None:
    logger.debug("Rendering booking window alert cards")
    _section_title(
        "Booking Window Alerts",
        "Act now — container procurement lead times are unforgiving",
    )

    windows = get_seasonal_booking_windows()

    if not windows:
        st.info("No upcoming booking windows in the next 52 weeks.")
        return

    for w in windows:
        urgency   = w["urgency_level"]
        color     = _URGENCY_COLOR.get(urgency, C_TEXT3)
        icon      = _URGENCY_ICON.get(urgency, "")
        wk_book   = w["weeks_until_book_by"]
        wk_event  = w["weeks_until_event"]
        event     = w["event_name"]
        mult      = w["demand_multiplier"]
        book_date = w["book_by_date"]

        if wk_book <= 0:
            time_label = "Booking window OPEN NOW"
        elif wk_book == 1:
            time_label = "Book within 1 week"
        else:
            time_label = "Book within " + str(wk_book) + " weeks"

        routes_html = "".join(_route_pill(r) for r in w["affected_routes"])
        mult_str    = str(round(mult, 2)) + "x demand"

        content = (
            '<div style="display:flex; align-items:flex-start; gap:14px">'
            '<div style="font-size:1.6rem; line-height:1">' + icon + "</div>"
            '<div style="flex:1">'
            '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px">'
            '<span style="font-size:0.92rem; font-weight:800; color:' + color + '">'
            + urgency + ": " + event.upper() + "</span>"
            + _badge(mult_str, C_WARN)
            + "</div>"
            + '<div style="font-size:0.80rem; color:' + C_TEXT + '; font-weight:600; margin-bottom:4px">'
            + time_label
            + " — book by " + book_date.strftime("%B %d, %Y")
            + "</div>"
            + '<div style="font-size:0.74rem; color:' + C_TEXT2 + '; margin-bottom:8px">'
            + "Event in " + str(wk_event) + " weeks"
            + "</div>"
            + routes_html
            + "</div></div>"
        )

        st.markdown(_card(content, border_color=color + "66"), unsafe_allow_html=True)


# ── Section 4: Platform Shipping Mode Analysis ────────────────────────────────

def _render_mode_split_chart() -> None:
    logger.debug("Rendering platform shipping mode split bar chart")
    _section_title(
        "Platform Shipping Mode Analysis",
        "Air freight % vs Ocean freight % — SHEIN/TEMU disrupting traditional ocean model",
    )

    platforms = ["Amazon", "Alibaba", "SHEIN", "Temu", "Shopify", "Wayfair"]
    keys      = ["AMAZON", "ALIBABA", "SHEIN", "TEMU", "SHOPIFY", "WAYFAIR"]
    air_pcts  = [_PLATFORM_AIR_PCT[k] for k in keys]
    ocean_pcts = [100.0 - a for a in air_pcts]
    colors     = [_PLATFORM_COLORS[k] for k in keys]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        name="Air Freight %",
        x=platforms,
        y=air_pcts,
        marker_color=[_PLATFORM_COLORS[k] for k in keys],
        marker_opacity=0.9,
        text=[str(int(v)) + "%" for v in air_pcts],
        textposition="inside",
        textfont=dict(color="white", size=12, family="monospace"),
        hovertemplate="<b>%{x}</b><br>Air: %{y:.0f}%<extra></extra>",
    ))

    fig.add_trace(go.Bar(
        name="Ocean Freight %",
        x=platforms,
        y=ocean_pcts,
        marker_color="rgba(255,255,255,0.12)",
        marker_line_color="rgba(255,255,255,0.20)",
        marker_line_width=1,
        text=[str(int(v)) + "%" for v in ocean_pcts],
        textposition="inside",
        textfont=dict(color=C_TEXT2, size=12, family="monospace"),
        hovertemplate="<b>%{x}</b><br>Ocean: %{y:.0f}%<extra></extra>",
    ))

    # Trend annotation: e-commerce shifting toward air
    fig.add_annotation(
        x=2, y=90,
        text="SHEIN/TEMU: de minimis-driven<br>air parcel disruption",
        showarrow=True,
        arrowhead=2,
        arrowcolor=C_WARN,
        arrowwidth=1.5,
        ax=60,
        ay=-40,
        font=dict(color=C_WARN, size=11),
        bgcolor="rgba(10,15,26,0.85)",
        bordercolor=C_WARN,
        borderwidth=1,
        borderpad=6,
    )

    fig.update_layout(
        barmode="stack",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font=dict(color=C_TEXT, family="monospace"),
        height=380,
        margin=dict(l=40, r=20, t=30, b=40),
        legend=dict(
            font=dict(color=C_TEXT2, size=11),
            bgcolor="rgba(10,15,26,0.6)",
            bordercolor=C_BORDER,
            borderwidth=1,
            x=0.01,
            y=0.99,
        ),
        xaxis=dict(
            tickfont=dict(color=C_TEXT2, size=12),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        yaxis=dict(
            title=dict(text="Share (%)", font=dict(color=C_TEXT2, size=11)),
            tickfont=dict(color=C_TEXT2, size=11),
            gridcolor="rgba(255,255,255,0.06)",
            ticksuffix="%",
            range=[0, 110],
        ),
    )

    st.plotly_chart(fig, use_container_width=True)

    # Trend note below chart
    st.markdown(
        '<div style="background:rgba(245,158,11,0.08); border:1px solid rgba(245,158,11,0.25);'
        ' border-radius:10px; padding:12px 16px; margin-top:-8px">'
        '<span style="font-size:0.78rem; color:' + C_WARN + '; font-weight:700">TREND: </span>'
        '<span style="font-size:0.78rem; color:' + C_TEXT2 + '">'
        "Air freight share increasing 5-8 pp/year among e-commerce platforms. "
        "If de minimis threshold is eliminated, 10-15% of SHEIN/TEMU volume would shift "
        "to ocean containers — equivalent to ~200,000 TEU/year of incremental trans-Pacific demand."
        "</span></div>",
        unsafe_allow_html=True,
    )


# ── Section 5: De Minimis Impact ──────────────────────────────────────────────

def _render_de_minimis() -> None:
    logger.debug("Rendering de minimis policy impact section")
    _section_title(
        "De Minimis Impact — The $800 Threshold Debate",
        "How SHEIN/TEMU exploit a US trade provision — and what happens if Congress closes it",
    )

    col1, col2 = st.columns([1, 1])

    with col1:
        # What it is
        st.markdown(
            _card(
                '<div style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.08em;'
                ' color:' + C_TEXT3 + '; margin-bottom:8px">WHAT IS DE MINIMIS?</div>'
                '<div style="font-size:1.8rem; font-weight:900; color:' + C_ACCENT + '; margin-bottom:6px">$800</div>'
                '<div style="font-size:0.80rem; color:' + C_TEXT + '; margin-bottom:10px; font-weight:600">'
                "Duty-Free Threshold per Package</div>"
                '<div style="font-size:0.75rem; color:' + C_TEXT2 + '; line-height:1.55">'
                "Under Section 321 of the Tariff Act, packages valued under $800 enter the "
                "United States duty-free. The threshold was raised from $200 in 2016, "
                "enabling the modern direct-to-consumer air parcel model."
                "<br><br>"
                "In 2024, ~1 billion de minimis packages entered the US — roughly 70% "
                "originated in China, with SHEIN and Temu accounting for the majority."
                "</div>",
                border_color=C_ACCENT + "55",
            ),
            unsafe_allow_html=True,
        )

        st.markdown(
            _card(
                '<div style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.08em;'
                ' color:' + C_TEXT3 + '; margin-bottom:8px">HOW SHEIN/TEMU EXPLOIT IT</div>'
                '<div style="font-size:0.77rem; color:' + C_TEXT2 + '; line-height:1.55">'
                "<b style='color:" + C_TEXT + "'>1. Warehouse in China, ship direct.</b> "
                "Individual orders are shipped as separate air parcels from Chinese warehouses "
                "directly to US consumers, each under $800, avoiding all import duties."
                "<br><br>"
                "<b style='color:" + C_TEXT + "'>2. Pricing advantage.</b> "
                "Avoiding 7.5-145% tariffs (depending on product category under Section 301) "
                "gives SHEIN/TEMU a structural cost advantage over US importers paying full duties."
                "<br><br>"
                "<b style='color:" + C_TEXT + "'>3. Scale.</b> "
                "SHEIN ships an estimated 600,000+ packages/day to the US. "
                "Temu ships 400,000+/day. Together they account for ~30% of all US de minimis entries."
                "</div>",
                border_color=C_ORANGE + "55",
            ),
            unsafe_allow_html=True,
        )

    with col2:
        # Policy risk
        st.markdown(
            _card(
                '<div style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.08em;'
                ' color:' + C_TEXT3 + '; margin-bottom:8px">POLICY RISK — CONGRESS DEBATE</div>'
                + _badge("HIGH RISK", C_DANGER)
                + '<div style="font-size:0.77rem; color:' + C_TEXT2 + '; margin-top:10px; line-height:1.55">'
                "<b style='color:" + C_TEXT + "'>STOP CHINA'S De Minimis Abuse Act</b> — "
                "bipartisan legislation proposes eliminating de minimis for goods from non-market "
                "economy countries (i.e., China). Passed House Ways and Means committee in 2024."
                "<br><br>"
                "<b style='color:" + C_TEXT + "'>Executive Order Route.</b> "
                "President has authority to restrict de minimis via IEEPA — already used to impose "
                "tariffs. An EO could close the loophole with 30-90 days notice."
                "<br><br>"
                "<b style='color:" + C_TEXT + "'>Timeline:</b> "
                "Congressional action expected 2025-2026. Trade hawks in both parties support reform. "
                "E-commerce lobby spending $100M+ opposing changes."
                "</div>",
                border_color=C_DANGER + "55",
            ),
            unsafe_allow_html=True,
        )

        # Ocean freight impact
        st.markdown(
            _card(
                '<div style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.08em;'
                ' color:' + C_TEXT3 + '; margin-bottom:8px">IMPACT ON OCEAN FREIGHT IF ELIMINATED</div>'
                '<div style="display:flex; gap:12px; margin-bottom:10px">'
                '<div style="text-align:center">'
                '<div style="font-size:1.6rem; font-weight:900; color:' + C_HIGH + '">+200K</div>'
                '<div style="font-size:0.68rem; color:' + C_TEXT3 + '">TEU/year<br>incremental TP</div>'
                "</div>"
                '<div style="text-align:center">'
                '<div style="font-size:1.6rem; font-weight:900; color:' + C_WARN + '">$15-20B</div>'
                '<div style="font-size:0.68rem; color:' + C_TEXT3 + '">Additional<br>tariff costs</div>'
                "</div>"
                '<div style="text-align:center">'
                '<div style="font-size:1.6rem; font-weight:900; color:' + C_ACCENT + '">+8-12%</div>'
                '<div style="font-size:0.68rem; color:' + C_TEXT3 + '">Trans-Pac EB<br>rate lift est.</div>'
                "</div>"
                "</div>"
                '<div style="font-size:0.75rem; color:' + C_TEXT2 + '; line-height:1.5">'
                "Elimination of de minimis for Chinese goods would force SHEIN/TEMU to either "
                "raise prices (reducing volume) or shift to bonded US warehouses (requiring ocean "
                "container pre-positioning). Estimated 10-15% volume shift to ocean = "
                "~200,000 incremental TEU/year on trans-Pacific EB. "
                "Rate impact: +8-12% sustained uplift. "
                "Timing lag: 6-12 months for supply chain reconfiguration."
                "</div>",
                border_color=C_HIGH + "55",
            ),
            unsafe_allow_html=True,
        )


# ── Section 6: Forecast — Next 90 Days ────────────────────────────────────────

def _render_90day_forecast() -> None:
    logger.debug("Rendering 90-day trans-Pacific demand forecast chart")
    _section_title(
        "Forecast: Next 90 Days — Trans-Pacific Demand",
        "Weekly predicted demand index based on retail calendar + e-commerce seasonal patterns",
    )

    today  = date.today()
    weeks  = 13  # ~90 days
    dates  = [today + timedelta(weeks=i) for i in range(weeks)]
    labels = [d.strftime("%b %d") for d in dates]

    # Compute weekly demand index by interpolating monthly values
    demand_vals: list[float] = []
    for d in dates:
        m     = d.month
        base  = _TP_MONTHLY[m]

        # Overlay retail calendar events — boost demand in booking windows
        boost = 0.0
        for cal in RETAIL_CALENDAR:
            for yr_off in (0, 1):
                try:
                    ev_date = date(d.year + yr_off, cal.month, cal.day)
                except ValueError:
                    ev_date = date(d.year + yr_off, cal.month, 28)
                bw_start = ev_date - timedelta(weeks=cal.typical_order_window_weeks_before)
                bw_end   = ev_date - timedelta(weeks=max(0, cal.typical_order_window_weeks_before - 4))
                if bw_start <= d <= bw_end:
                    boost = max(boost, (cal.container_demand_multiplier - 1.0) * 0.5)
                    break

        demand_vals.append(round(base + boost, 3))

    # Urgency color per week
    fill_colors: list[str] = []
    for v in demand_vals:
        if v >= 1.35:
            fill_colors.append("rgba(239,68,68,0.18)")
        elif v >= 1.15:
            fill_colors.append("rgba(249,115,22,0.15)")
        elif v >= 1.00:
            fill_colors.append("rgba(245,158,11,0.12)")
        else:
            fill_colors.append("rgba(16,185,129,0.10)")

    fig = go.Figure()

    # Shaded urgency bands
    fig.add_hrect(y0=1.35, y1=1.60, fillcolor="rgba(239,68,68,0.08)",
                  line_width=0, annotation_text="PEAK", annotation_position="right",
                  annotation_font=dict(color=C_DANGER, size=10))
    fig.add_hrect(y0=1.15, y1=1.35, fillcolor="rgba(249,115,22,0.06)",
                  line_width=0, annotation_text="HIGH", annotation_position="right",
                  annotation_font=dict(color=C_ORANGE, size=10))
    fig.add_hrect(y0=0.95, y1=1.15, fillcolor="rgba(245,158,11,0.05)",
                  line_width=0, annotation_text="MOD", annotation_position="right",
                  annotation_font=dict(color=C_WARN, size=10))
    fig.add_hrect(y0=0.60, y1=0.95, fillcolor="rgba(16,185,129,0.04)",
                  line_width=0, annotation_text="LOW", annotation_position="right",
                  annotation_font=dict(color=C_HIGH, size=10))

    # Baseline reference
    fig.add_hline(y=1.0, line_dash="dot", line_color="rgba(255,255,255,0.20)",
                  line_width=1, annotation_text="Baseline",
                  annotation_font=dict(color=C_TEXT3, size=10),
                  annotation_position="right")

    # Fill area under curve
    fig.add_trace(go.Scatter(
        x=labels,
        y=demand_vals,
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.07)",
        line=dict(color="transparent"),
        showlegend=False,
        hoverinfo="skip",
    ))

    # Main demand line
    fig.add_trace(go.Scatter(
        x=labels,
        y=demand_vals,
        mode="lines+markers",
        name="Demand Index",
        line=dict(color=C_ACCENT, width=2.5),
        marker=dict(
            color=[_demand_color(v) for v in demand_vals],
            size=8,
            line=dict(color=C_BG, width=1.5),
        ),
        hovertemplate="<b>%{x}</b><br>Demand Index: %{y:.3f}x<extra></extra>",
    ))

    # Mark retail events within window
    for cal in RETAIL_CALENDAR:
        for yr_off in (0, 1):
            try:
                ev_date = date(today.year + yr_off, cal.month, cal.day)
            except ValueError:
                ev_date = date(today.year + yr_off, cal.month, 28)
            if today <= ev_date <= dates[-1]:
                ev_label = ev_date.strftime("%b %d")
                if ev_label in labels:
                    idx_pos = labels.index(ev_label)
                    fig.add_annotation(
                        x=ev_label,
                        y=demand_vals[idx_pos] + 0.05,
                        text=cal.event_name[:12],
                        showarrow=True,
                        arrowhead=1,
                        arrowcolor=C_WARN,
                        arrowwidth=1,
                        font=dict(color=C_WARN, size=9),
                        ax=0,
                        ay=-30,
                    )

    # Today marker
    fig.add_vline(
        x=labels[0],
        line_dash="dash",
        line_color=C_HIGH,
        line_width=1.5,
        annotation_text="Today",
        annotation_font=dict(color=C_HIGH, size=10),
        annotation_position="top left",
    )

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font=dict(color=C_TEXT, family="monospace"),
        height=380,
        margin=dict(l=40, r=80, t=20, b=40),
        xaxis=dict(
            tickfont=dict(color=C_TEXT2, size=10),
            gridcolor="rgba(255,255,255,0.04)",
            tickangle=-30,
        ),
        yaxis=dict(
            title=dict(text="Demand Index (1.0 = baseline)", font=dict(color=C_TEXT2, size=11)),
            tickfont=dict(color=C_TEXT2, size=11),
            gridcolor="rgba(255,255,255,0.06)",
            range=[0.50, 1.65],
        ),
        showlegend=False,
    )

    st.plotly_chart(fig, use_container_width=True)

    # Booking urgency legend below
    st.markdown(
        '<div style="display:flex; gap:12px; flex-wrap:wrap; margin-top:-6px">'
        '<div style="display:flex; align-items:center; gap:5px">'
        '<div style="width:12px; height:12px; border-radius:3px; background:#ef4444; opacity:0.7"></div>'
        '<span style="font-size:0.72rem; color:' + C_TEXT2 + '">Peak — book immediately</span></div>'
        '<div style="display:flex; align-items:center; gap:5px">'
        '<div style="width:12px; height:12px; border-radius:3px; background:#f97316; opacity:0.7"></div>'
        '<span style="font-size:0.72rem; color:' + C_TEXT2 + '">High — book within 2 weeks</span></div>'
        '<div style="display:flex; align-items:center; gap:5px">'
        '<div style="width:12px; height:12px; border-radius:3px; background:#f59e0b; opacity:0.7"></div>'
        '<span style="font-size:0.72rem; color:' + C_TEXT2 + '">Moderate — monitor closely</span></div>'
        '<div style="display:flex; align-items:center; gap:5px">'
        '<div style="width:12px; height:12px; border-radius:3px; background:#10b981; opacity:0.7"></div>'
        '<span style="font-size:0.72rem; color:' + C_TEXT2 + '">Low — normal procurement</span></div>'
        "</div>",
        unsafe_allow_html=True,
    )


# ── Public render entry point ──────────────────────────────────────────────────

def render(
    route_results: dict | None = None,
    freight_data: dict | None = None,
    macro_data: dict | None = None,
) -> None:
    """Render the E-Commerce Shipping Intelligence tab.

    Args:
        route_results: Route analysis output from the main engine (optional).
        freight_data:  Current freight rate data (optional).
        macro_data:    Macroeconomic context data (optional).
    """
    logger.info("Rendering tab_ecommerce")

    today = date.today()
    demand_idx = compute_ecommerce_demand_index(today.month)
    tp_label   = demand_idx["transpacific_eb"]["label"]
    tp_idx_val = demand_idx["transpacific_eb"]["index"]

    # ── Top status bar ─────────────────────────────────────────────────────────
    tp_color = _demand_color(tp_idx_val)
    st.markdown(
        '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
        ' border-radius:12px; padding:14px 20px; margin-bottom:18px;'
        ' display:flex; align-items:center; gap:24px">'
        '<div>'
        '<div style="font-size:0.68rem; text-transform:uppercase; letter-spacing:0.08em;'
        ' color:' + C_TEXT3 + '; margin-bottom:4px">TRANS-PAC EB — CURRENT DEMAND</div>'
        '<div style="font-size:1.6rem; font-weight:900; color:' + tp_color + '">'
        + str(tp_idx_val) + "x"
        + "</div>"
        + '<div style="font-size:0.75rem; color:' + tp_color + '; font-weight:700">'
        + tp_label + "</div>"
        + "</div>"
        + '<div style="flex:1; height:2px; background:linear-gradient(90deg,'
        + tp_color + "66, transparent);"
        + ' border-radius:2px"></div>'
        + '<div style="font-size:0.80rem; color:' + C_TEXT2 + '">'
        + "E-Commerce Intelligence Dashboard &nbsp;|&nbsp; "
        + today.strftime("%B %Y")
        + "</div>"
        + "</div>",
        unsafe_allow_html=True,
    )

    st.markdown("---")

    # ── Section 1 ──────────────────────────────────────────────────────────────
    _render_platform_cards()

    st.markdown("---")

    # ── Section 2 ──────────────────────────────────────────────────────────────
    _render_retail_calendar()

    st.markdown("---")

    # ── Section 3 ──────────────────────────────────────────────────────────────
    _render_booking_alerts()

    st.markdown("---")

    # ── Section 4 ──────────────────────────────────────────────────────────────
    _render_mode_split_chart()

    st.markdown("---")

    # ── Section 5 ──────────────────────────────────────────────────────────────
    _render_de_minimis()

    st.markdown("---")

    # ── Section 6 ──────────────────────────────────────────────────────────────
    _render_90day_forecast()

    logger.info("tab_ecommerce render complete")
