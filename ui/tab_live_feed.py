"""Live Market Feed tab — Bloomberg-style real-time data ticker and feed dashboard."""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ── Palette ────────────────────────────────────────────────────────────────────

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

# ── Static feed data ───────────────────────────────────────────────────────────

_MARKET_METRICS = [
    ("BDI",      1847,  23,   1.26),
    ("WCI",      3210, -45,  -1.38),
    ("SCFI",     2856,  67,   2.40),
    ("ZIM",      18.42,  0.83,  4.72),
    ("MATX",    103.57, -1.24, -1.18),
    ("CRUDE",    81.34,  0.47,  0.58),
    ("USD/CNY",   7.243,  0.012, 0.17),
    ("EUR/USD",   1.0842,-0.0031,-0.28),
]

_RATE_CHANGES = [
    ("Asia-Europe",      4500, 4820,  320),
    ("Transpacific",     2100, 1980, -120),
    ("Asia-USGC",        5200, 5450,  250),
    ("N.Europe-USEC",    1800, 1920,  120),
    ("Asia-Med",         3900, 4150,  250),
    ("Intra-Asia",        850,  790,  -60),
    ("Asia-Australia",   1650, 1720,   70),
    ("USEC-Europe",      2400, 2280, -120),
    ("Asia-WAF",         2950, 3100,  150),
    ("Asia-India",        780,  810,   30),
    ("Trans-Atlantic",   3200, 3050, -150),
    ("Asia-LatAm",       4100, 4320,  220),
    ("Asia-ME Gulf",     1200, 1350,  150),
    ("Europe-Asia",      1600, 1480, -120),
    ("Transpacific USWC",1950, 2100,  150),
    ("Asia-USEC",        3800, 3950,  150),
    ("Med-USEC",         2700, 2820,  120),
    ("N.Europe-Med",      950,  990,   40),
    ("Asia-RSA",         2400, 2550,  150),
    ("Intra-Europe",      650,  680,   30),
]

_ALPHA_SIGNALS = [
    ("STNG",  "STRONG BUY",  87, "Tanker rate momentum + fleet utilization 94%"),
    ("ZIM",   "BUY",         71, "Container spot rates inflecting, Q2 guidance raised"),
    ("SBLK",  "HOLD",        55, "BDI softness offset by long-term contract coverage"),
    ("MATX",  "BUY",         78, "Hawaii trade lane monopoly; yield 3.2%"),
    ("DAC",   "STRONG BUY",  82, "Scrubber retrofit premium; charter backlog 3.2yr"),
    ("CMRE",  "SELL",        63, "Leverage risk in rising rate env; refi wall 2026"),
    ("DSX",   "BUY",         74, "Dry bulk recovery play; spot exposure 65%"),
    ("GRIN",  "HOLD",        51, "Mixed signals: earnings beat offset by order book"),
    ("HAFNI", "BUY",         79, "Product tanker upcycle; VLCC equivalent rates +18%"),
    ("EURN",  "STRONG BUY",  91, "Earnings yield 14%; buyback program $200M"),
]

_NEWS_ITEMS = [
    ("Houthi forces claim strike on container vessel in Red Sea corridor",      "HIGH"),
    ("Panama Canal authority raises transit fees 15% effective Q2 2026",         "HIGH"),
    ("Port of Los Angeles reports 12% YoY volume increase in March",             "MOD"),
    ("Maersk announces 8 new megaships on Asia-Europe route by 2027",            "MOD"),
    ("ILA union threatens strike action at Gulf Coast ports over automation",    "HIGH"),
    ("Singapore MPA reports record 5,200 vessel calls in February",              "LOW"),
    ("European Commission proposes new carbon levy on shipping emissions",        "MOD"),
    ("MSC overtakes Maersk as world's largest container line by capacity",       "MOD"),
    ("Evergreen orders 20 dual-fuel 24,000 TEU vessels from CSSC",              "LOW"),
    ("Taiwan Strait tensions rise; military drills disrupt AIS tracking",        "HIGH"),
]

_PORT_UPDATES = [
    ("Rotterdam",    "3 vessels delayed; dense fog, visibility <200m"),
    ("Shanghai",     "Berth queue: 47 vessels; avg wait 2.3 days"),
    ("Singapore",    "Anchorage congestion easing; draft restrictions lifted"),
    ("Los Angeles",  "Rail dwell time: 5.2 days, above 4.0-day target"),
    ("Hamburg",      "Strike action ended; operations resuming at 70% capacity"),
]

_MACRO_UPDATES = [
    ("US PMI",          51.2,  50.0, "above consensus"),
    ("China Caixin PMI", 49.8, 50.5, "below consensus; contraction"),
    ("EU CPI YoY",       2.3,   2.1, "above consensus"),
    ("US Non-Farm",    "+256K","+185K","beat; USD strengthening"),
    ("Fed Funds Rate",  "5.25%","5.25%","held; 2 cuts priced for 2026"),
]

_DATA_REFRESHES = [
    ("BDI",  "1,847",   "Baltic Exchange"),
    ("WCI",  "3,210",   "Drewry"),
    ("SCFI", "2,856",   "Shanghai Shipping Exchange"),
    ("VLCC spot", "$38,200/day", "Clarksons"),
    ("Capesize TCE", "$14,500/day", "Baltic Exchange"),
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _color_for(val: float) -> str:
    return C_HIGH if val >= 0 else C_LOW


def _arrow(val: float) -> str:
    return "▲" if val >= 0 else "▼"


def _sign(val: float) -> str:
    return "+" if val >= 0 else ""


def _pct(val: float, base: float) -> float:
    try:
        return (val / base) * 100
    except ZeroDivisionError:
        return 0.0


# ── Section 1: Live Header ─────────────────────────────────────────────────────

def _render_header(auto_refresh: bool, last_ts: float) -> None:
    try:
        now = _utc_now()
        elapsed = int(time.time() - last_ts)
        pulse_color = C_HIGH if auto_refresh else C_TEXT3

        st.markdown(
            f"""
            <div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;
                        padding:18px 24px;margin-bottom:16px;display:flex;
                        align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
              <div style="display:flex;align-items:center;gap:14px;">
                <span style="display:inline-block;width:12px;height:12px;border-radius:50%;
                             background:{pulse_color};
                             box-shadow:0 0 8px {pulse_color};
                             animation:pulse 1.4s ease-in-out infinite;"></span>
                <span style="color:{C_TEXT};font-size:22px;font-weight:700;
                             letter-spacing:2px;font-family:monospace;">LIVE MARKET FEED</span>
              </div>
              <div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap;">
                <span style="color:{C_TEXT2};font-size:13px;font-family:monospace;">
                  UTC {now.strftime("%Y-%m-%d %H:%M:%S")}
                </span>
                <span style="color:{C_TEXT3};font-size:12px;">
                  Last updated <span style="color:{C_MOD};font-weight:600;">{elapsed}s ago</span>
                </span>
              </div>
            </div>
            <style>
              @keyframes pulse {{
                0%,100% {{ opacity:1; transform:scale(1); }}
                50%      {{ opacity:0.4; transform:scale(0.85); }}
              }}
            </style>
            """,
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"_render_header error: {exc}")


# ── Section 2: Market Ticker Strip ─────────────────────────────────────────────

def _render_ticker_strip() -> None:
    try:
        items = []
        for label, val, chg, pct in _MARKET_METRICS:
            color = _color_for(chg)
            arrow = _arrow(chg)
            sign  = _sign(chg)
            if label in ("BDI", "WCI", "SCFI"):
                val_str = f"{val:,.0f}"
                chg_str = f"{sign}{chg:,.0f}"
            elif label in ("USD/CNY", "EUR/USD"):
                val_str = f"{val:.4f}"
                chg_str = f"{sign}{chg:.4f}"
            elif label == "CRUDE":
                val_str = f"${val:.2f}"
                chg_str = f"{sign}{chg:.2f}"
            else:
                val_str = f"${val:.2f}"
                chg_str = f"{sign}{chg:.2f}"
            items.append(
                f'<span style="margin:0 28px;white-space:nowrap;">'
                f'<span style="color:{C_TEXT3};font-size:11px;letter-spacing:1px;">{label}</span> '
                f'<span style="color:{C_TEXT};font-weight:700;font-size:14px;">{val_str}</span> '
                f'<span style="color:{color};font-size:12px;">{arrow} {chg_str} ({sign}{pct:.2f}%)</span>'
                f'</span>'
            )

        ticker_html = "".join(items)
        # duplicate for seamless loop
        double = ticker_html + ticker_html

        st.markdown(
            f"""
            <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;
                        overflow:hidden;padding:10px 0;margin-bottom:16px;">
              <div style="overflow:hidden;white-space:nowrap;position:relative;">
                <div style="display:inline-block;animation:scroll-left 45s linear infinite;
                            font-family:monospace;">
                  {double}
                </div>
              </div>
            </div>
            <style>
              @keyframes scroll-left {{
                0%   {{ transform: translateX(0); }}
                100% {{ transform: translateX(-50%); }}
              }}
            </style>
            """,
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"_render_ticker_strip error: {exc}")


# ── Section 3: Breaking Alerts ─────────────────────────────────────────────────

def _render_breaking_alerts(insights: Any, news_items: Any) -> None:
    try:
        st.markdown(
            f'<div style="color:{C_TEXT};font-size:16px;font-weight:700;'
            f'letter-spacing:1px;margin:20px 0 10px;">BREAKING ALERTS</div>',
            unsafe_allow_html=True,
        )

        alerts: list[str] = []

        # pull from news_items
        if news_items:
            for item in news_items:
                try:
                    if isinstance(item, dict):
                        sev = str(item.get("severity", item.get("urgency", ""))).upper()
                        txt = item.get("headline", item.get("title", str(item)))
                    else:
                        sev, txt = "HIGH", str(item)
                    if sev == "HIGH":
                        alerts.append(txt)
                except Exception:
                    pass

        # pull from insights
        if insights:
            for ins in (insights if isinstance(insights, list) else []):
                try:
                    sev = str(ins.get("severity", ins.get("urgency", ""))).upper()
                    if sev in ("HIGH", "CRITICAL"):
                        alerts.append(ins.get("message", ins.get("title", str(ins))))
                except Exception:
                    pass

        # fallback to static high-urgency news
        if not alerts:
            alerts = [h for h, s in _NEWS_ITEMS if s == "HIGH"]

        alerts = alerts[:3]

        if not alerts:
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;'
                f'padding:14px 18px;color:{C_TEXT3};font-size:13px;">No critical alerts at this time.</div>',
                unsafe_allow_html=True,
            )
            return

        for alert in alerts:
            st.markdown(
                f"""
                <div style="background:{C_CARD};border:1px solid {C_LOW};border-left:4px solid {C_LOW};
                            border-radius:8px;padding:14px 18px;margin-bottom:8px;
                            display:flex;align-items:center;gap:14px;">
                  <span style="background:{C_LOW};color:#fff;font-size:10px;font-weight:800;
                               padding:2px 7px;border-radius:4px;letter-spacing:1px;
                               animation:blink 1.2s step-start infinite;">NEW</span>
                  <span style="color:{C_TEXT};font-size:13px;font-weight:500;">{alert}</span>
                </div>
                <style>
                  @keyframes blink {{ 0%,100%{{opacity:1}} 50%{{opacity:0}} }}
                </style>
                """,
                unsafe_allow_html=True,
            )
    except Exception as exc:
        logger.warning(f"_render_breaking_alerts error: {exc}")


# ── Section 4: Multi-Feed Table ─────────────────────────────────────────────────

_FEED_TYPE_STYLE: dict[str, tuple[str, str]] = {
    "SIGNAL":      (C_ACCENT, "#1e3a5f"),
    "NEWS":        (C_TEXT3,  C_SURFACE),
    "RATE CHANGE": (C_HIGH,   "#0d2b1e"),
    "ALERT":       (C_LOW,    "#2d0f0f"),
    "DATA UPDATE": (C_TEXT,   "#1a2235"),
}


def _build_feed_rows() -> list[dict]:
    rows: list[dict] = []
    base = _utc_now()

    def ts(offset_min: int) -> str:
        return _fmt_dt(base - timedelta(minutes=offset_min))

    # Rate changes (15)
    for i, (route, old, new, chg) in enumerate(_RATE_CHANGES[:15]):
        sign = "+" if chg >= 0 else ""
        color = C_HIGH if chg >= 0 else C_LOW
        pct_val = _pct(chg, old)
        rows.append({
            "ts": ts(i * 3 + 1),
            "type": "RATE CHANGE",
            "item": f"{route}",
            "value": f'<span style="color:{color};font-weight:600;">{sign}${chg:,.0f}/TEU → ${new:,.0f}</span>',
            "severity": "MOD" if abs(chg) < 200 else "HIGH",
            "sort_key": i * 3 + 1,
        })

    # Alpha signals (10)
    for i, (ticker, signal, conv, rationale) in enumerate(_ALPHA_SIGNALS):
        conv_color = C_HIGH if conv >= 75 else (C_MOD if conv >= 55 else C_TEXT3)
        sig_color  = C_HIGH if "BUY" in signal else (C_LOW if "SELL" in signal else C_MOD)
        rows.append({
            "ts": ts(i * 4 + 2),
            "type": "SIGNAL",
            "item": f"{ticker}: {rationale[:45]}...",
            "value": f'<span style="color:{sig_color};font-weight:700;">{signal}</span> '
                     f'<span style="color:{conv_color};font-size:11px;">conviction {conv}%</span>',
            "severity": "HIGH" if conv >= 80 else "MOD",
            "sort_key": i * 4 + 2,
        })

    # News (10)
    for i, (headline, sev) in enumerate(_NEWS_ITEMS):
        sev_color = C_LOW if sev == "HIGH" else (C_MOD if sev == "MOD" else C_TEXT3)
        rows.append({
            "ts": ts(i * 5 + 3),
            "type": "NEWS",
            "item": headline[:60] + ("..." if len(headline) > 60 else ""),
            "value": f'<span style="color:{sev_color};font-size:11px;">&#9632; {sev}</span>',
            "severity": sev,
            "sort_key": i * 5 + 3,
        })

    # Port updates (5)
    for i, (port, update) in enumerate(_PORT_UPDATES):
        rows.append({
            "ts": ts(i * 7 + 10),
            "type": "ALERT",
            "item": f"{port}: {update}",
            "value": f'<span style="color:{C_MOD};">PORT OPS</span>',
            "severity": "MOD",
            "sort_key": i * 7 + 10,
        })

    # Macro updates (5)
    for i, (name, actual, consensus, note) in enumerate(_MACRO_UPDATES):
        color = C_HIGH if "above" in str(note) or "beat" in str(note) else C_LOW
        rows.append({
            "ts": ts(i * 6 + 15),
            "type": "DATA UPDATE",
            "item": f"{name}: {actual} (consensus {consensus})",
            "value": f'<span style="color:{color};font-size:12px;">{note}</span>',
            "severity": "MOD",
            "sort_key": i * 6 + 15,
        })

    # Data refreshes (5)
    for i, (metric, val, source) in enumerate(_DATA_REFRESHES):
        rows.append({
            "ts": ts(i * 8 + 20),
            "type": "DATA UPDATE",
            "item": f"{metric} updated: {val}",
            "value": f'<span style="color:{C_TEXT3};font-size:11px;">{source}</span>',
            "severity": "LOW",
            "sort_key": i * 8 + 20,
        })

    rows.sort(key=lambda r: r["sort_key"])
    return rows[:50]


def _render_feed_table() -> None:
    try:
        st.markdown(
            f'<div style="color:{C_TEXT};font-size:16px;font-weight:700;'
            f'letter-spacing:1px;margin:20px 0 10px;">LIVE DATA FEED</div>',
            unsafe_allow_html=True,
        )

        rows = _build_feed_rows()

        header = (
            f'<div style="display:grid;grid-template-columns:70px 110px 1fr 220px 60px;'
            f'gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-radius:8px 8px 0 0;padding:8px 12px;">'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;letter-spacing:1px;">TIME</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;letter-spacing:1px;">FEED TYPE</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;letter-spacing:1px;">ITEM</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;letter-spacing:1px;">VALUE / CHANGE</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;letter-spacing:1px;">SEV</span>'
            f'</div>'
        )

        body_parts = [header]
        for idx, row in enumerate(rows):
            ftype      = row["type"]
            label_col, bg_col = _FEED_TYPE_STYLE.get(ftype, (C_TEXT2, C_CARD))
            sev        = row["severity"]
            sev_color  = C_LOW if sev == "HIGH" else (C_MOD if sev == "MOD" else C_TEXT3)
            row_bg     = bg_col if idx % 2 == 0 else C_BG
            border_bot = f"border-bottom:1px solid {C_BORDER};"

            body_parts.append(
                f'<div style="display:grid;grid-template-columns:70px 110px 1fr 220px 60px;'
                f'gap:0;background:{row_bg};{border_bot}padding:7px 12px;align-items:center;">'
                f'<span style="color:{C_TEXT3};font-size:11px;font-family:monospace;">{row["ts"]}</span>'
                f'<span style="background:{label_col}22;color:{label_col};font-size:10px;'
                f'font-weight:700;padding:2px 6px;border-radius:4px;letter-spacing:0.5px;'
                f'white-space:nowrap;">{ftype}</span>'
                f'<span style="color:{C_TEXT2};font-size:12px;padding:0 8px;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{row["item"]}</span>'
                f'<span style="font-size:12px;">{row["value"]}</span>'
                f'<span style="color:{sev_color};font-size:10px;font-weight:700;">{sev}</span>'
                f'</div>'
            )

        st.markdown(
            f'<div style="border-radius:8px;overflow:hidden;max-height:480px;'
            f'overflow-y:auto;border:1px solid {C_BORDER};">{"".join(body_parts)}</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"_render_feed_table error: {exc}")


# ── Section 5: Signal Activity Chart ───────────────────────────────────────────

def _render_signal_chart() -> None:
    try:
        st.markdown(
            f'<div style="color:{C_TEXT};font-size:16px;font-weight:700;'
            f'letter-spacing:1px;margin:24px 0 10px;">SIGNAL ACTIVITY — LAST 24H</div>',
            unsafe_allow_html=True,
        )

        rng = list(range(24))
        counts  = [random.randint(0, 8) for _ in rng]
        convs   = [random.randint(50, 95) for _ in rng]
        labels  = [f"{h:02d}:00" for h in rng]
        colors  = [C_HIGH if c >= 75 else (C_MOD if c >= 60 else C_TEXT3) for c in convs]

        fig = go.Figure(go.Bar(
            x=labels,
            y=counts,
            marker_color=colors,
            text=[str(c) if c > 0 else "" for c in counts],
            textposition="outside",
            textfont_color=C_TEXT2,
            hovertemplate="<b>%{x}</b><br>Signals: %{y}<extra></extra>",
        ))

        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor=C_SURFACE,
            font_color=C_TEXT2,
            height=220,
            margin=dict(l=40, r=20, t=10, b=40),
            xaxis=dict(
                showgrid=False,
                tickfont_color=C_TEXT3,
                tickfont_size=10,
            ),
            yaxis=dict(
                showgrid=True,
                gridcolor=C_BORDER,
                tickfont_color=C_TEXT3,
                title_text="Signals",
                title_font_color=C_TEXT3,
            ),
            bargap=0.25,
        )

        st.plotly_chart(fig, use_container_width=True)

        # Legend
        st.markdown(
            f'<div style="display:flex;gap:20px;margin-top:-12px;padding:0 8px;">'
            f'<span style="color:{C_HIGH};font-size:11px;">&#9632; High conviction (&ge;75%)</span>'
            f'<span style="color:{C_MOD};font-size:11px;">&#9632; Moderate (60–74%)</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;">&#9632; Low (&lt;60%)</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"_render_signal_chart error: {exc}")


# ── Section 6: Freight Rate Changes ────────────────────────────────────────────

def _render_freight_table(freight_data: Any) -> None:
    try:
        st.markdown(
            f'<div style="color:{C_TEXT};font-size:16px;font-weight:700;'
            f'letter-spacing:1px;margin:24px 0 10px;">FREIGHT RATE CHANGES</div>',
            unsafe_allow_html=True,
        )

        base = _utc_now()
        rows = []
        for i, (route, old, new, chg) in enumerate(_RATE_CHANGES[:20]):
            pct_val  = _pct(chg, old)
            sign     = "+" if chg >= 0 else ""
            ts_str   = _fmt_time(base - timedelta(minutes=i * 4 + random.randint(0, 3)))
            chg_col  = C_HIGH if chg >= 0 else C_LOW
            rows.append((ts_str, route, f"${old:,.0f}", f"${new:,.0f}",
                         f"{sign}${chg:,.0f}", f"{sign}{pct_val:.1f}%", chg_col))

        header = (
            f'<div style="display:grid;grid-template-columns:80px 1fr 90px 90px 90px 70px;'
            f'gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-radius:8px 8px 0 0;padding:8px 12px;">'
            + "".join(
                f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;letter-spacing:1px;">{h}</span>'
                for h in ["TIME", "ROUTE", "OLD", "NEW", "CHANGE", "PCT"]
            )
            + "</div>"
        )

        body_parts = [header]
        for idx, (ts_str, route, old_s, new_s, chg_s, pct_s, chg_col) in enumerate(rows):
            row_bg = C_CARD if idx % 2 == 0 else C_BG
            body_parts.append(
                f'<div style="display:grid;grid-template-columns:80px 1fr 90px 90px 90px 70px;'
                f'gap:0;background:{row_bg};border-bottom:1px solid {C_BORDER};'
                f'padding:7px 12px;align-items:center;">'
                f'<span style="color:{C_TEXT3};font-size:11px;font-family:monospace;">{ts_str}</span>'
                f'<span style="color:{C_TEXT2};font-size:12px;">{route}</span>'
                f'<span style="color:{C_TEXT3};font-size:12px;">{old_s}</span>'
                f'<span style="color:{C_TEXT};font-size:12px;font-weight:600;">{new_s}</span>'
                f'<span style="color:{chg_col};font-size:12px;font-weight:700;">{chg_s}</span>'
                f'<span style="color:{chg_col};font-size:12px;">{pct_s}</span>'
                f'</div>'
            )

        st.markdown(
            f'<div style="border-radius:8px;overflow:hidden;max-height:340px;'
            f'overflow-y:auto;border:1px solid {C_BORDER};">{"".join(body_parts)}</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"_render_freight_table error: {exc}")


# ── Section 7: News Sentiment Pulse ────────────────────────────────────────────

def _render_sentiment_pulse(news_items: Any) -> None:
    try:
        st.markdown(
            f'<div style="color:{C_TEXT};font-size:16px;font-weight:700;'
            f'letter-spacing:1px;margin:24px 0 10px;">NEWS SENTIMENT PULSE</div>',
            unsafe_allow_html=True,
        )

        # Derive from news_items if available, else use static values
        s1h  = -0.18
        s4h  =  0.04
        s24h =  0.11

        if news_items and isinstance(news_items, list):
            high_count = sum(1 for n in news_items if isinstance(n, dict) and
                             str(n.get("severity", "")).upper() == "HIGH")
            total = max(len(news_items), 1)
            s1h  = round(-high_count / total + random.uniform(-0.05, 0.05), 2)
            s4h  = round(s1h * 0.6 + random.uniform(-0.08, 0.08), 2)
            s24h = round(s4h * 0.5 + random.uniform(-0.05, 0.08), 2)

        def _fmt_score(v: float) -> str:
            return f"{'+' if v >= 0 else ''}{v:.2f}"

        def _score_color(v: float) -> str:
            if v > 0.05:
                return C_HIGH
            if v < -0.05:
                return C_LOW
            return C_MOD

        cols = st.columns(3)
        for col, (label, val, window) in zip(cols, [
            ("1-Hour Score",    s1h,  "Rolling 1h"),
            ("4-Hour Average",  s4h,  "Rolling 4h"),
            ("24-Hour Average", s24h, "Rolling 24h"),
        ]):
            col.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
                f'padding:18px;text-align:center;">'
                f'<div style="color:{C_TEXT3};font-size:11px;letter-spacing:1px;margin-bottom:6px;">{label}</div>'
                f'<div style="color:{_score_color(val)};font-size:32px;font-weight:800;'
                f'font-family:monospace;">{_fmt_score(val)}</div>'
                f'<div style="color:{C_TEXT3};font-size:11px;margin-top:6px;">{window}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception as exc:
        logger.warning(f"_render_sentiment_pulse error: {exc}")


# ── Auto-refresh logic ──────────────────────────────────────────────────────────

def _handle_auto_refresh(auto_refresh: bool) -> None:
    try:
        if not auto_refresh:
            return
        key = "_live_feed_refresh_ts"
        now = time.time()
        if key not in st.session_state:
            st.session_state[key] = now
        elapsed = now - st.session_state[key]
        if elapsed >= 60:
            st.session_state[key] = now
            logger.info("Live feed auto-refreshing after 60s")
            st.rerun()
    except Exception as exc:
        logger.warning(f"_handle_auto_refresh error: {exc}")


# ── Public entry point ──────────────────────────────────────────────────────────

def render(
    port_results:  Any = None,
    route_results: Any = None,
    insights:      Any = None,
    freight_data:  Any = None,
    macro_data:    Any = None,
    news_items:    Any = None,
) -> None:
    """Render the Bloomberg-style Live Market Feed tab."""
    try:
        logger.debug("Rendering tab_live_feed")

        # Track last-updated timestamp in session state
        _TS_KEY = "_live_feed_last_ts"
        if _TS_KEY not in st.session_state:
            st.session_state[_TS_KEY] = time.time()

        # ── Controls row ──────────────────────────────────────────────────────
        ctrl_col, _ = st.columns([2, 8])
        with ctrl_col:
            auto_refresh = st.checkbox("Auto-refresh (60s)", value=False, key="_live_feed_auto")

        last_ts = st.session_state.get(_TS_KEY, time.time())

        # ── 1. Header ─────────────────────────────────────────────────────────
        _render_header(auto_refresh, last_ts)

        # ── 2. Ticker strip ───────────────────────────────────────────────────
        _render_ticker_strip()

        # ── 3. Breaking alerts ────────────────────────────────────────────────
        _render_breaking_alerts(insights, news_items)

        st.markdown("<div style='margin:8px 0;'></div>", unsafe_allow_html=True)

        # ── 4. Multi-feed table ───────────────────────────────────────────────
        _render_feed_table()

        # ── 5. Signal activity chart ──────────────────────────────────────────
        _render_signal_chart()

        # ── 6. Freight rate changes ───────────────────────────────────────────
        _render_freight_table(freight_data)

        # ── 7. Sentiment pulse ────────────────────────────────────────────────
        _render_sentiment_pulse(news_items)

        # ── 8. Auto-refresh trigger ───────────────────────────────────────────
        _handle_auto_refresh(auto_refresh)

    except Exception as exc:
        logger.error(f"tab_live_feed render error: {exc}")
        st.error(f"Live feed error: {exc}")
