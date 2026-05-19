"""Live Market Feed tab — WSJ editorial-style real-time data ticker and feed dashboard.

Refactored to use the shared design system in :mod:`ui.styles`. All palette
constants are imported (not redeclared); ad-hoc inline HTML has been replaced
with ``page_header``, ``metric_card_row``, ``section_header``,
``wsj_market_table``, ``insight_card_html``, ``badge``, ``source_footer`` and
``ticker_tape_html`` helpers.

Every figure/table that consumes data surfaces a provenance pill via
``source_footer`` — synthetic blocks are labeled ``kind="modeled"`` /
``quality="demo"`` so the viewer can see the data is not trustworthy.
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

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
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    ticker_tape_html,
    wsj_market_table,
)

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


# ── Domain color mappings (tab-local) ──────────────────────────────────────────

_FEED_TYPE_COLOR: dict[str, str] = {
    "SIGNAL":      C_ACCENT,
    "NEWS":        C_TEXT3,
    "RATE CHANGE": C_HIGH,
    "ALERT":       C_LOW,
    "DATA UPDATE": C_TEXT,
}

_SEVERITY_COLOR: dict[str, str] = {
    "HIGH": C_LOW,
    "MOD":  C_MOD,
    "LOW":  C_TEXT3,
}

_SIGNAL_COLOR_MAP = {
    "STRONG BUY": C_HIGH,
    "BUY":        C_HIGH,
    "HOLD":       C_MOD,
    "SELL":       C_LOW,
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _pct(val: float, base: float) -> float:
    try:
        return (val / base) * 100
    except ZeroDivisionError:
        return 0.0


def _signal_color(signal: str) -> str:
    for key, color in _SIGNAL_COLOR_MAP.items():
        if key in signal:
            return color
    return C_MOD


def _conviction_color(conv: int) -> str:
    if conv >= 75:
        return C_HIGH
    if conv >= 55:
        return C_MOD
    return C_TEXT3


def _severity_color(sev: str) -> str:
    return _SEVERITY_COLOR.get(sev, C_TEXT3)


# ── Cell formatters ────────────────────────────────────────────────────────────

def _sans(value: str, color: str = C_TEXT, weight: int = 600) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};font-weight:{weight};">'
        f'{value}</span>'
    )


def _mono(value: str, color: str = C_TEXT, weight: int = 600) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};font-weight:{weight};">'
        f'{value}</span>'
    )


def _badge_for_severity(sev: str) -> str:
    color_map = {"HIGH": C_LOW, "MOD": C_MOD, "LOW": C_TEXT3}
    return badge(sev, color=color_map.get(sev, C_TEXT3))


# ── Data source provenance (mark all synthetic feed data) ─────────────────────
# Mock feed content is flagged ``kind="modeled"``, ``quality="demo"`` so the
# provenance pill renders red and viewers know not to trust the numbers.

def _demo_source(name: str, notes: str = "Mock live-feed data") -> dict:
    """Build a source-footer-friendly dict for synthetic feed content."""
    return {"name": name, "kind": "modeled", "quality": "demo", "notes": notes}


_SRC_TICKER     = _demo_source("Baltic / Drewry / SCFI composite")
_SRC_ALERTS     = _demo_source("News + insights composite")
_SRC_MULTI_FEED = _demo_source("Composite multi-feed (rates + signals + news)")
_SRC_SIGNAL_HR  = _demo_source("Hourly signal counts (24h)")
_SRC_FREIGHT    = _demo_source("Rate change ticker")
_SRC_SENTIMENT  = _demo_source("Headline-derived sentiment scores")


# ── Section 1: Page header + status strip ─────────────────────────────────────

def _render_header(auto_refresh: bool, last_ts: float) -> None:
    try:
        now = _utc_now()
        elapsed = int(time.time() - last_ts)
        badge_color = C_HIGH if auto_refresh else C_TEXT3
        badge_text = "LIVE" if auto_refresh else "PAUSED"

        page_header(
            title="Live Market Feed",
            subtitle=(
                f"Real-time shipping market pulse · UTC "
                f"{now.strftime('%Y-%m-%d %H:%M:%S')} · updated {elapsed}s ago"
            ),
            badge_text=badge_text,
            badge_color=badge_color,
        )
    except Exception as exc:
        logger.warning(f"_render_header error: {exc}")


# ── Section 2: Market ticker strip ─────────────────────────────────────────────

def _render_ticker_strip() -> None:
    try:
        section_header(
            "Market Ticker",
            subtitle="Benchmarks, equities and FX — live snapshot",
        )

        # Build ticker items in the format `ticker_tape_html` expects.
        items: list[dict] = []
        for label, val, chg, pct in _MARKET_METRICS:
            if label in ("BDI", "WCI", "SCFI"):
                val_str = f"{val:,.0f}"
                unit = ""
            elif label in ("USD/CNY", "EUR/USD"):
                val_str = f"{val:.4f}"
                unit = ""
            else:
                val_str = f"{val:.2f}"
                unit = ""
            items.append({
                "label":  label,
                "value":  val_str,
                "unit":   unit,
                "change": float(pct),
            })

        st.html(ticker_tape_html(items))
        st.markdown(source_footer([_SRC_TICKER]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"_render_ticker_strip error: {exc}")


# ── Section 3: Breaking alerts ─────────────────────────────────────────────────

def _render_breaking_alerts(insights: Any, news_items: Any) -> None:
    try:
        section_header("Breaking Alerts",
                       subtitle="High-severity events from news + insight feeds")

        alerts: list[str] = []

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

        if insights:
            for ins in (insights if isinstance(insights, list) else []):
                try:
                    sev = str(ins.get("severity", ins.get("urgency", ""))).upper()
                    if sev in ("HIGH", "CRITICAL"):
                        alerts.append(ins.get("message", ins.get("title", str(ins))))
                except Exception:
                    pass

        if not alerts:
            alerts = [h for h, s in _NEWS_ITEMS if s == "HIGH"]

        alerts = alerts[:3]

        if not alerts:
            st.markdown(
                insight_card_html(
                    title="No critical alerts at this time",
                    score=0.0,
                    action="Monitor",
                    rationale="Feed monitored continuously — severity escalates the pill colour automatically.",
                    category="MACRO",
                ),
                unsafe_allow_html=True,
            )
        else:
            for alert in alerts:
                st.markdown(
                    insight_card_html(
                        title=alert,
                        score=0.95,
                        action="Prioritize",
                        rationale="High-severity event flagged in the live feed.",
                        category="PORT_DEMAND",
                    ),
                    unsafe_allow_html=True,
                )

        st.markdown(source_footer([_SRC_ALERTS]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"_render_breaking_alerts error: {exc}")


# ── Section 4: Multi-feed table ────────────────────────────────────────────────

def _build_feed_rows() -> list[dict]:
    rows: list[dict] = []
    base = _utc_now()

    def ts(offset_min: int) -> str:
        return _fmt_dt(base - timedelta(minutes=offset_min))

    # Rate changes (15)
    for i, (route, old, new, chg) in enumerate(_RATE_CHANGES[:15]):
        sign = "+" if chg >= 0 else ""
        color = C_HIGH if chg >= 0 else C_LOW
        rows.append({
            "ts": ts(i * 3 + 1),
            "type": "RATE CHANGE",
            "item": f"{route}",
            "value": _mono(f"{sign}${chg:,.0f}/TEU → ${new:,.0f}", color=color, weight=600),
            "severity": "MOD" if abs(chg) < 200 else "HIGH",
            "sort_key": i * 3 + 1,
        })

    # Alpha signals (10)
    for i, (ticker, signal, conv, rationale) in enumerate(_ALPHA_SIGNALS):
        rows.append({
            "ts": ts(i * 4 + 2),
            "type": "SIGNAL",
            "item": f"{ticker}: {rationale[:45]}...",
            "value": (
                _sans(signal, color=_signal_color(signal), weight=700)
                + " "
                + _sans(f"conviction {conv}%", color=_conviction_color(conv), weight=500)
            ),
            "severity": "HIGH" if conv >= 80 else "MOD",
            "sort_key": i * 4 + 2,
        })

    # News (10)
    for i, (headline, sev) in enumerate(_NEWS_ITEMS):
        rows.append({
            "ts": ts(i * 5 + 3),
            "type": "NEWS",
            "item": headline[:60] + ("..." if len(headline) > 60 else ""),
            "value": _sans(f"&#9632; {sev}", color=_severity_color(sev), weight=500),
            "severity": sev,
            "sort_key": i * 5 + 3,
        })

    # Port updates (5)
    for i, (port, update) in enumerate(_PORT_UPDATES):
        rows.append({
            "ts": ts(i * 7 + 10),
            "type": "ALERT",
            "item": f"{port}: {update}",
            "value": _sans("PORT OPS", color=C_MOD, weight=600),
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
            "value": _sans(note, color=color, weight=500),
            "severity": "MOD",
            "sort_key": i * 6 + 15,
        })

    # Data refreshes (5)
    for i, (metric, val, source) in enumerate(_DATA_REFRESHES):
        rows.append({
            "ts": ts(i * 8 + 20),
            "type": "DATA UPDATE",
            "item": f"{metric} updated: {val}",
            "value": _sans(source, color=C_TEXT3, weight=500),
            "severity": "LOW",
            "sort_key": i * 8 + 20,
        })

    rows.sort(key=lambda r: r["sort_key"])
    return rows[:50]


def _render_feed_table() -> None:
    try:
        section_header(
            "Live Data Feed",
            subtitle="Unified stream of rates, signals, news and macro updates",
        )

        rows = _build_feed_rows()

        headers = ["Time", "Feed Type", "Item", "Value / Change", "Severity"]
        table_rows: list[list[str]] = []
        for row in rows:
            ftype = row["type"]
            type_color = _FEED_TYPE_COLOR.get(ftype, C_TEXT2)
            table_rows.append([
                _mono(row["ts"], color=C_TEXT3, weight=500),
                badge(ftype, color=type_color),
                _sans(row["item"], color=C_TEXT2, weight=500),
                row["value"],
                _badge_for_severity(row["severity"]),
            ])

        wsj_market_table(headers, table_rows)
        st.markdown(source_footer([_SRC_MULTI_FEED]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"_render_feed_table error: {exc}")


# ── Section 5: Signal activity chart ──────────────────────────────────────────

def _render_signal_chart() -> None:
    try:
        section_header(
            "Signal Activity — Last 24h",
            subtitle="Hourly count of alpha signals, shaded by avg conviction",
        )

        rng = list(range(24))
        # Synthetic data: replace with a signal-history feed when wired.
        counts = [random.randint(0, 8) for _ in rng]
        convs = [random.randint(50, 95) for _ in rng]
        labels = [f"{h:02d}:00" for h in rng]
        colors = [C_HIGH if c >= 75 else (C_MOD if c >= 60 else C_TEXT3) for c in convs]

        fig = go.Figure(go.Bar(
            x=labels,
            y=counts,
            marker_color=colors,
            text=[str(c) if c > 0 else "" for c in counts],
            textposition="outside",
            textfont_color=C_TEXT2,
            hovertemplate="<b>%{x}</b><br>Signals: %{y}<extra></extra>",
        ))
        apply_dark_layout(
            fig,
            height=220,
            margin=dict(l=40, r=20, t=10, b=40),
            xaxis=dict(showgrid=False),
            yaxis=dict(title_text="Signals"),
            bargap=0.25,
        )
        fig.update_layout(
            annotations=[
                dict(
                    xref="paper", yref="paper", x=0.0, y=1.08, showarrow=False,
                    align="left", xanchor="left",
                    font=dict(family="var(--sans)", size=11, color=C_TEXT3),
                    text=(
                        f"<span style='color:{C_HIGH};'>■</span> High (≥75%)  "
                        f"<span style='color:{C_MOD};'>■</span> Moderate (60–74%)  "
                        f"<span style='color:{C_TEXT3};'>■</span> Low (&lt;60%)"
                    ),
                )
            ]
        )

        st.plotly_chart(fig, use_container_width=True, key="live_feed_signal_chart")
        st.markdown(source_footer([_SRC_SIGNAL_HR]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"_render_signal_chart error: {exc}")


# ── Section 6: Freight rate changes ────────────────────────────────────────────

def _render_freight_table(freight_data: Any) -> None:
    try:
        section_header(
            "Freight Rate Changes",
            subtitle="Recent per-TEU rate moves across primary container lanes",
        )

        base = _utc_now()
        headers = ["Time", "Route", "Old", "New", "Change", "Pct"]
        rows: list[list[str]] = []
        for i, (route, old, new, chg) in enumerate(_RATE_CHANGES[:20]):
            pct_val = _pct(chg, old)
            sign = "+" if chg >= 0 else ""
            # Jitter the timestamp so entries look staggered; synthetic.
            ts_str = _fmt_time(base - timedelta(minutes=i * 4 + random.randint(0, 3)))
            chg_col = C_HIGH if chg >= 0 else C_LOW
            rows.append([
                _mono(ts_str, color=C_TEXT3, weight=500),
                _sans(route, color=C_TEXT2, weight=500),
                _mono(f"${old:,.0f}", color=C_TEXT3, weight=500),
                _mono(f"${new:,.0f}", color=C_TEXT, weight=600),
                _mono(f"{sign}${chg:,.0f}", color=chg_col, weight=700),
                _mono(f"{sign}{pct_val:.1f}%", color=chg_col, weight=600),
            ])

        wsj_market_table(headers, rows)
        st.markdown(source_footer([_SRC_FREIGHT]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"_render_freight_table error: {exc}")


# ── Section 7: News sentiment pulse ────────────────────────────────────────────

def _render_sentiment_pulse(news_items: Any) -> None:
    try:
        section_header(
            "News Sentiment Pulse",
            subtitle="Rolling sentiment scores derived from headline severity mix",
        )

        s1h, s4h, s24h = -0.18, 0.04, 0.11

        if news_items and isinstance(news_items, list):
            high_count = sum(
                1 for n in news_items
                if isinstance(n, dict) and str(n.get("severity", "")).upper() == "HIGH"
            )
            total = max(len(news_items), 1)
            # Synthetic jitter — replace with real sentiment windowing when a
            # scored news feed is wired into the app.
            s1h = round(-high_count / total + random.uniform(-0.05, 0.05), 2)
            s4h = round(s1h * 0.6 + random.uniform(-0.08, 0.08), 2)
            s24h = round(s4h * 0.5 + random.uniform(-0.05, 0.08), 2)

        def _score_color(v: float) -> str:
            if v > 0.05:
                return C_HIGH
            if v < -0.05:
                return C_LOW
            return C_MOD

        def _accent(v: float) -> str:
            return _score_color(v)

        def _fmt_score(v: float) -> str:
            return f"{'+' if v >= 0 else ''}{v:.2f}"

        metric_card_row(
            [
                {
                    "label":    "1-Hour Score",
                    "value":    _fmt_score(s1h),
                    "accent":   _accent(s1h),
                    "sublabel": "Rolling 1h",
                },
                {
                    "label":    "4-Hour Average",
                    "value":    _fmt_score(s4h),
                    "accent":   _accent(s4h),
                    "sublabel": "Rolling 4h",
                },
                {
                    "label":    "24-Hour Average",
                    "value":    _fmt_score(s24h),
                    "accent":   _accent(s24h),
                    "sublabel": "Rolling 24h",
                },
            ],
            columns=3,
        )
        st.markdown(source_footer([_SRC_SENTIMENT]), unsafe_allow_html=True)
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
    """Render the WSJ editorial-style Live Market Feed tab."""
    try:
        logger.debug("Rendering tab_live_feed")

        _TS_KEY = "_live_feed_last_ts"
        if _TS_KEY not in st.session_state:
            st.session_state[_TS_KEY] = time.time()

        ctrl_col, _ = st.columns([3, 7])
        with ctrl_col:
            st.markdown(
                f'<div style="font-family:var(--sans);font-size:0.66rem;'
                f'font-weight:700;letter-spacing:0.1em;text-transform:uppercase;'
                f'color:{C_TEXT3};margin-bottom:2px;">Feed Control</div>',
                unsafe_allow_html=True,
            )
            auto_refresh = st.checkbox(
                "Auto-refresh every 60 seconds",
                value=False,
                key="_live_feed_auto",
            )

        last_ts = st.session_state.get(_TS_KEY, time.time())

        _render_header(auto_refresh, last_ts)

        # ── Movement 1: market pulse ────────────────────────────────────────
        _render_ticker_strip()
        _render_breaking_alerts(insights, news_items)

        # ── Movement 2: live stream ─────────────────────────────────────────
        section_divider("Live Stream")
        _render_feed_table()
        _render_signal_chart()

        # ── Movement 3: rates & sentiment ───────────────────────────────────
        section_divider("Rates & Sentiment")
        _render_freight_table(freight_data)
        _render_sentiment_pulse(news_items)

        # ── Provenance footer ───────────────────────────────────────────────
        st.markdown(
            source_footer([
                _SRC_MULTI_FEED,
                _SRC_TICKER,
                _SRC_SENTIMENT,
            ]),
            unsafe_allow_html=True,
        )

        _handle_auto_refresh(auto_refresh)

    except Exception as exc:
        logger.error(f"tab_live_feed render error: {exc}")
        st.error(f"Live feed error: {exc}")
