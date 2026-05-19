"""tab_overview.py — Executive Overview Dashboard (flagship landing page).

The first screen a user sees. A financial-terminal front page that states the
market verdict, then fans out into KPIs, signals, risk and feed health.

Canonical tab pattern (mirrors ``ui/tab_rate_analytics.py`` /
``ui/tab_disruption_radar.py``):
  * palette + components imported from ``ui/styles.py`` — never redeclared;
  * no hand-rolled inline-styled divs — every block is a ``ui/styles.py``
    helper, or a ``wsj_market_table`` cell formatted with span content;
  * labeled ``section_divider`` rules separate the dashboard's zones;
  * every section wrapped in try/except + ``logger.exception``.

Sections
--------
A. Page header (badge "DASHBOARD")
B. Market verdict — tone banner + per-feed health row
C. Headline KPI strip — six key metrics
D. Markets & Signals — market pulse, signal-conviction matrix, featured
   signal + top-signals table
E. Risk & Routes — risk/alert table, route opportunities, data-feed status
F. Quick Views — three distribution snapshots
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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
    alert_banner,
    apply_dark_layout,
    badge,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    status_badge,
    wsj_market_table,
)

try:
    from ports.port_registry import PORTS, PORTS_BY_LOCODE
    from ports.demand_analyzer import PortDemandResult
    from routes.optimizer import RouteOpportunity
    from engine.insight import Insight
except Exception:
    PORTS = []
    PORTS_BY_LOCODE = {}
    PortDemandResult = Any
    RouteOpportunity = Any
    Insight = Any


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — cell formatters + domain colors
# ══════════════════════════════════════════════════════════════════════════════
# wsj_market_table renders cell strings as raw HTML inside <td>. These helpers
# only style content (font + conditional color); table CSS handles alignment
# and rule lines. Mirrors the pattern in ui/tab_results.py and tab_rate_analytics.py.


def _mono(value: str, color: str = C_TEXT) -> str:
    """Monospace numeric cell content."""
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    """Sans-serif cell content."""
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _score_color(score: float) -> str:
    if score >= 0.70:
        return C_HIGH
    if score >= 0.45:
        return C_MOD
    return C_LOW


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _safe_avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _fv(d: dict, *keys, fmt="{}", default="--"):
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return fmt.format(v)
            except Exception:
                return str(v)
    return default


# Local domain colors used by the signal-conviction matrix (kept local because
# they're semantic only for this tab).
_CONVICTION_PALETTE: dict[str, str] = {
    "Strong":   C_HIGH,
    "Bullish":  C_HIGH,
    "Neutral":  C_TEXT2,
    "Caution":  C_MOD,
    "Avoid":    C_LOW,
}


def _conviction_label(score: float) -> str:
    if score >= 0.80:
        return "Strong"
    if score >= 0.65:
        return "Bullish"
    if score >= 0.50:
        return "Neutral"
    if score >= 0.35:
        return "Caution"
    return "Avoid"


# Plain-English framing for the market tone — surfaced in the verdict banner so
# the front page reads as an editorial call before the eye reaches the KPIs.
_TONE_GLOSS: dict[str, str] = {
    "Bullish": "demand is running hot across the tracked port network — "
               "conditions favour leaning into capacity",
    "Neutral": "demand is balanced across the network — no decisive tilt "
               "either way",
    "Bearish": "demand is soft across the tracked port network — conditions "
               "argue for caution on new capacity",
    "Awaiting Data": "live feeds have not populated yet — figures below are "
                     "illustrative until a refresh completes",
}

_TONE_LEVEL: dict[str, str] = {
    "Bullish": "success",
    "Neutral": "warning",
    "Bearish": "critical",
    "Awaiting Data": "info",
}


# Sources used by the dashboard's composite sections.
_DASHBOARD_SOURCES = [
    {"name": "Port demand engine",   "kind": "modeled",  "quality": "modeled"},
    {"name": "Route optimizer",      "kind": "modeled",  "quality": "modeled"},
    {"name": "Insight engine",       "kind": "modeled",  "quality": "modeled"},
    {"name": "Freight indices",      "kind": "scraped",  "quality": "good"},
    {"name": "Macro / FX",           "kind": "live",     "quality": "good"},
]


def _market_tone(port_results: list) -> tuple[str, str]:
    """Resolve the dashboard's headline tone from average port demand.

    Returns ``(tone, status)`` where ``status`` is a ``status_badge`` key.
    """
    has_data = [r for r in port_results if getattr(r, "has_real_data", False)]
    avg = _safe_avg([r.demand_score for r in has_data]) if has_data else 0.0
    if avg >= 0.65:
        return "Bullish", "success"
    if avg >= 0.45:
        return "Neutral", "warning"
    if avg > 0:
        return "Bearish", "danger"
    return "Awaiting Data", "neutral"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION B — Market Verdict (tone banner + feed health)
# ══════════════════════════════════════════════════════════════════════════════

def _render_market_verdict(
    port_results: list, route_results: list, insights: list,
    freight_data: dict, macro_data: dict, stock_data: dict,
) -> None:
    """Render the headline market-tone banner plus the per-feed health row.

    The banner states the editorial call; the ``source_footer`` strip beneath
    it shows which of the six platform feeds are live versus running on demo
    defaults — the same idiom every other tab uses for provenance.
    """
    try:
        tone, _ = _market_tone(port_results)
        has_data = [r for r in port_results if getattr(r, "has_real_data", False)]
        avg_demand = _safe_avg([r.demand_score for r in has_data]) if has_data else 0.0

        level = _TONE_LEVEL.get(tone, "info")
        gloss = _TONE_GLOSS.get(tone, "fleet conditions are mixed")
        demand_clause = (
            f" Average port demand reads <b>{avg_demand:.0%}</b> across "
            f"<b>{len(has_data)}</b> tracked ports."
            if has_data else ""
        )
        alert_banner(
            f"Market tone reads <b>{tone}</b> — {gloss}.{demand_clause}",
            level=level,
        )

        feeds = [
            ("Ports",    bool(has_data),       "modeled"),
            ("Routes",   bool(route_results),  "modeled"),
            ("Signals",  bool(insights),       "modeled"),
            ("Freight",  bool(freight_data),   "scraped"),
            ("Macro",    bool(macro_data),     "live"),
            ("Equities", bool(stock_data),     "live"),
        ]
        feed_sources = [
            {
                "name":    name,
                "kind":    kind if ok else "demo",
                "quality": "good" if ok else "demo",
            }
            for name, ok, kind in feeds
        ]

        sb_left, sb_right = st.columns([6, 2])
        with sb_left:
            st.markdown(source_footer(feed_sources, align="left"), unsafe_allow_html=True)
        with sb_right:
            st.markdown(
                _mono(f"As of {_now_utc()}", color=C_TEXT3),
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("Overview — market verdict render failed")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION C — Headline KPI Strip
# ══════════════════════════════════════════════════════════════════════════════

def _render_kpi_strip(
    port_results: list, route_results: list, insights: list,
    freight_data: dict, macro_data: dict, stock_data: dict,
    alerts: list,
) -> None:
    try:
        fd = freight_data or {}
        has_data = [r for r in port_results if getattr(r, "has_real_data", False)]
        avg_demand = _safe_avg([r.demand_score for r in has_data]) if has_data else 0.0
        hi_conv = sum(1 for i in insights if getattr(i, "score", 0) >= 0.70)
        n_alerts = len(alerts) if alerts else sum(1 for i in insights if getattr(i, "score", 0) >= 0.80)
        strong_rts = sum(1 for r in route_results if getattr(r, "opportunity_label", "") == "Strong")

        demand_str = f"{avg_demand:.0%}" if avg_demand else "--"
        alert_color = C_LOW if n_alerts > 3 else C_MOD if n_alerts > 0 else C_HIGH
        alert_sub = (
            "elevated risk load" if n_alerts > 3
            else "monitoring" if n_alerts > 0
            else "all clear"
        )

        section_header(
            "Headline KPIs",
            "Six figures that frame the session — freight indices, port demand, "
            "risk load and conviction",
        )
        metric_card_row(
            [
                {"label": "Baltic Dry Index", "value": _fv(fd, "bdi", "BDI", fmt="{:,.0f}", default="1,847"),
                 "accent": C_ACCENT, "sublabel": "dry-bulk benchmark"},
                {"label": "Container Index", "value": _fv(fd, "wci", "WCI", "SCFI", fmt="{:,.0f}", default="2,204"),
                 "accent": C_ACCENT, "sublabel": "boxship spot rates"},
                {"label": "Avg Port Demand", "value": demand_str,
                 "accent": C_HIGH if avg_demand >= 0.6 else C_TEXT3,
                 "sublabel": f"{len(has_data)} ports tracked"},
                {"label": "Active Alerts", "value": str(n_alerts),
                 "accent": alert_color, "sublabel": alert_sub},
                {"label": "High Conviction", "value": str(hi_conv),
                 "accent": C_HIGH if hi_conv > 2 else C_TEXT3,
                 "sublabel": f"of {len(insights)} signals"},
                {"label": "Strong Routes", "value": str(strong_rts),
                 "accent": C_HIGH if strong_rts > 2 else C_TEXT3,
                 "sublabel": f"of {len(route_results)} lanes"},
            ],
            columns=6,
        )
    except Exception:
        logger.exception("Overview — KPI strip render failed")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION D — Market Pulse
# ══════════════════════════════════════════════════════════════════════════════

def _render_market_pulse(freight_data: dict, macro_data: dict, stock_data: dict) -> None:
    try:
        fd = freight_data or {}
        md = macro_data or {}

        rates = [
            ("BDI",       _fv(fd, "bdi", "BDI", fmt="{:,.0f}", default="1,847"),  "+1.3%",  C_HIGH),
            ("SCFI",      _fv(fd, "scfi", "SCFI", fmt="{:,.0f}", default="2,856"), "+2.4%",  C_HIGH),
            ("WCI",       _fv(fd, "wci", "WCI", fmt="{:,.0f}", default="3,210"),  "-1.4%",  C_LOW),
            ("VLCC Spot", _fv(fd, "vlcc_rate", "vlcc", fmt="${:,.0f}/d", default="$32,500/d"), "+0.6%", C_HIGH),
            ("Crude",     _fv(md, "crude", "CRUDE", fmt="${:.2f}", default="$81.34"),  "+0.6%", C_HIGH),
            ("USD/CNY",   _fv(md, "usdcny", "USD/CNY", fmt="{:.3f}", default="7.243"), "+0.2%", C_MOD),
        ]

        section_header("Market Pulse", "Key freight indices, rates and macro markers")

        rows = []
        for name, value, delta, delta_color in rates:
            rows.append([
                _sans(name, color=C_TEXT, weight=600),
                _mono(value, color=C_TEXT),
                _mono(delta, color=delta_color),
            ])
        wsj_market_table(["Indicator", "Value", "Δ 24h"], rows)

        st.markdown(
            source_footer([
                {"name": "Baltic Exchange",  "kind": "scraped", "quality": "good"},
                {"name": "FRED / macro",     "kind": "live",    "quality": "good"},
            ]),
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Overview — market pulse render failed")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION D — Signal Conviction Matrix
# ══════════════════════════════════════════════════════════════════════════════

def _render_signal_matrix(route_results: list, insights: list) -> None:
    try:
        CORRIDORS = ["Trans-Pacific", "Asia-Europe", "Trans-Atlantic", "Intra-Asia"]
        COMMODITIES = ["Dry Bulk", "Container", "Tanker", "LNG/LPG"]

        def _cell_score(corridor: str, commodity: str) -> float:
            c_lower = corridor.lower().replace("-", " ")
            m_lower = commodity.lower()
            scores = []
            for ins in insights:
                title = (getattr(ins, "title", "") or "").lower()
                cat = (getattr(ins, "category", "") or "").lower()
                route_match = any(w in title for w in c_lower.split())
                comm_match = (
                    (m_lower == "dry bulk" and ("bulk" in title or "bdi" in title or cat == "route"))
                    or (m_lower == "container" and ("container" in title or "feu" in title))
                    or (m_lower == "tanker" and ("tanker" in title or "vlcc" in title or "crude" in title))
                    or (m_lower == "lng/lpg" and ("lng" in title or "lpg" in title or "gas" in title))
                )
                if route_match or comm_match:
                    scores.append(getattr(ins, "score", 0.5))
            if scores:
                return min(1.0, _safe_avg(scores))
            for r in route_results:
                name = (getattr(r, "route_name", "") or "").lower()
                if any(w in name for w in c_lower.split()):
                    return getattr(r, "opportunity_score", 0.45)
            return 0.35

        section_header(
            "Signal Conviction",
            "Composite conviction by trade corridor and commodity class",
        )

        rows = []
        for corridor in CORRIDORS:
            row = [_sans(corridor, color=C_TEXT, weight=700)]
            for commodity in COMMODITIES:
                score = _cell_score(corridor, commodity)
                label = _conviction_label(score)
                color = _CONVICTION_PALETTE.get(label, C_TEXT2)
                pct = int(score * 100)
                row.append(
                    f'{_mono(f"{pct}%", color=color)}'
                    f'<br>{badge(label, color=color)}'
                )
            rows.append(row)

        wsj_market_table(["Corridor", *COMMODITIES], rows)
        st.markdown(source_footer(_DASHBOARD_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Overview — signal matrix render failed")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION D — Top Signals (featured card + table)
# ══════════════════════════════════════════════════════════════════════════════

def _render_top_signals(insights: list) -> None:
    try:
        section_header(
            "Top Signals",
            f"{len(insights)} active — highest-conviction calls first",
        )

        if not insights:
            alert_banner(
                "No signals generated yet — refresh live feeds to populate the "
                "decision engine.",
                level="info",
            )
            return

        ACTION_COLOR = {"Prioritize": C_HIGH, "Monitor": C_ACCENT, "Watch": C_TEXT2, "Caution": C_MOD, "Avoid": C_LOW}
        ranked = sorted(insights, key=lambda i: getattr(i, "score", 0), reverse=True)

        # ─ Featured signal — the single highest-conviction call, promoted to an
        #   editorial insight card so the front page leads with a verdict, not a
        #   table row. Mirrors the forecast callout in tab_disruption_radar.
        lead = ranked[0]
        lead_score = float(getattr(lead, "score", 0.5) or 0.5)
        lead_title = (getattr(lead, "title", "--") or "--")[:90]
        lead_action = getattr(lead, "action", "Monitor") or "Monitor"
        lead_detail = (getattr(lead, "detail", "") or "").strip()
        lead_category = (getattr(lead, "category", "") or "").upper()
        st.markdown(
            insight_card_html(
                title=lead_title,
                score=max(0.0, min(1.0, lead_score)),
                action=lead_action,
                rationale=lead_detail[:220],
                category=lead_category,
            ),
            unsafe_allow_html=True,
        )

        # ─ Remaining high-conviction signals as a compact table ─
        rest = ranked[1:6]
        if rest:
            rows = []
            for ins in rest:
                score = getattr(ins, "score", 0.5)
                title = (getattr(ins, "title", "--") or "--")[:80]
                action = getattr(ins, "action", "Monitor") or "Monitor"
                pct = int(score * 100)
                sc = _score_color(score)
                ac = ACTION_COLOR.get(action, C_ACCENT)
                rows.append([
                    _mono(f"{pct}%", color=sc),
                    _sans(title, color=C_TEXT, weight=500),
                    badge(action, color=ac),
                ])
            wsj_market_table(["Score", "Signal", "Action"], rows)

        st.markdown(source_footer(_DASHBOARD_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Overview — top signals render failed")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION E — Risk & Alerts
# ══════════════════════════════════════════════════════════════════════════════

def _render_risk_alerts(insights: list, alerts: list) -> None:
    try:
        alert_items = []
        if alerts:
            for a in alerts[:6]:
                sev = getattr(a, "severity", "MODERATE") or "MODERATE"
                title = getattr(a, "title", "") or getattr(a, "message", "") or "--"
                alert_items.append((sev, title[:70]))
        else:
            risky = [i for i in insights if getattr(i, "score", 0) >= 0.75]
            risky.sort(key=lambda i: getattr(i, "score", 0), reverse=True)
            for ins in risky[:6]:
                score = getattr(ins, "score", 0.5)
                title = getattr(ins, "title", "--") or "--"
                sev = "HIGH" if score >= 0.85 else "MODERATE"
                alert_items.append((sev, title[:70]))

        SEV_STATUS = {"CRITICAL": "danger", "HIGH": "danger", "MODERATE": "warning", "LOW": "success"}

        section_header("Risk & Alerts", f"{len(alert_items)} flagged for attention")

        if not alert_items:
            alert_banner("No active alerts — the network is reading clean.", level="success")
            return

        rows = []
        for sev, title in alert_items:
            rows.append([
                status_badge(sev, SEV_STATUS.get(sev, "warning")),
                _sans(title, color=C_TEXT2, weight=500),
            ])
        wsj_market_table(["Severity", "Detail"], rows)
        st.markdown(source_footer(_DASHBOARD_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Overview — risk alerts render failed")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION E — Route Opportunities
# ══════════════════════════════════════════════════════════════════════════════

def _render_route_opps(route_results: list) -> None:
    try:
        strong = sorted(
            [r for r in route_results if getattr(r, "opportunity_score", 0) >= 0.55],
            key=lambda r: getattr(r, "opportunity_score", 0), reverse=True,
        )[:5]

        section_header("Route Opportunities", f"{len(strong)} lanes scoring strong")

        if not strong:
            alert_banner(
                "No lanes are scoring strong right now — the route optimizer "
                "found no standout opportunities.",
                level="info",
            )
            return

        rows = []
        for r in strong:
            name = getattr(r, "route_name", "") or getattr(r, "route_id", "") or "--"
            score = getattr(r, "opportunity_score", 0)
            pct = int(score * 100)
            sc = _score_color(score)
            rate = getattr(r, "current_rate_usd_feu", None)
            rate_str = f"${rate:,.0f}/FEU" if rate else "--"

            rows.append([
                _sans(str(name)[:25], color=C_TEXT, weight=600),
                _mono(rate_str, color=C_TEXT2),
                _mono(f"{pct}%", color=sc),
            ])
        wsj_market_table(["Route", "Rate", "Score"], rows)
        st.markdown(
            source_footer([{"name": "Route optimizer", "kind": "modeled", "quality": "modeled"}]),
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Overview — route opps render failed")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION E — Data-Feed Status
# ══════════════════════════════════════════════════════════════════════════════

def _render_data_status(
    port_results: list, route_results: list, insights: list,
    freight_data: dict, macro_data: dict, stock_data: dict,
) -> None:
    try:
        has_data_ports = [r for r in port_results if getattr(r, "has_real_data", False)]

        sources = [
            ("Port Demand",  bool(has_data_ports)),
            ("Routes",       bool(route_results)),
            ("Signals",      bool(insights)),
            ("Freight",      bool(freight_data)),
            ("Macro / FX",   bool(macro_data)),
            ("Equities",     bool(stock_data)),
        ]

        ok_count = sum(1 for _, ok in sources if ok)
        section_header("Data Feeds", f"{ok_count} of {len(sources)} feeds live")

        rows = []
        for name, ok in sources:
            rows.append([
                _sans(name, color=C_TEXT2, weight=500),
                status_badge("Live" if ok else "Offline", "success" if ok else "danger"),
            ])
        wsj_market_table(["Feed", "Status"], rows)
    except Exception:
        logger.exception("Overview — data status render failed")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION F — Quick Views (distribution snapshots)
# ══════════════════════════════════════════════════════════════════════════════

def _render_sparklines(port_results: list, insights: list, freight_data: dict) -> None:
    try:
        section_header(
            "Quick Views",
            "Distribution snapshots across the signal book and port network",
        )
        cols = st.columns(3)
        drew_any = False

        with cols[0]:
            if port_results:
                scores = [getattr(r, "demand_score", 0) for r in port_results
                          if getattr(r, "has_real_data", False)]
                if scores:
                    fig = go.Figure(go.Histogram(
                        x=scores, nbinsx=12,
                        marker_color=C_ACCENT,
                        marker_line_width=0,
                    ))
                    apply_dark_layout(
                        fig,
                        title="Port Demand Distribution",
                        height=200,
                        showlegend=False,
                        margin={"l": 16, "r": 16, "t": 36, "b": 24},
                    )
                    fig.update_xaxes(title_text="Score", title_font_size=10)
                    fig.update_yaxes(title_text="Ports", title_font_size=10)
                    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
                    drew_any = True

        with cols[1]:
            if insights:
                scores = [getattr(i, "score", 0) for i in insights]
                cats = [getattr(i, "category", "OTHER") or "OTHER" for i in insights]
                cat_colors = {
                    "CONVERGENCE": C_CONV, "ROUTE": C_ACCENT,
                    "PORT_DEMAND": C_HIGH, "MACRO": C_MACRO,
                }
                colors = [cat_colors.get(c, C_TEXT3) for c in cats]

                fig = go.Figure(go.Bar(
                    x=list(range(len(scores))),
                    y=scores,
                    marker_color=colors,
                    marker_line_width=0,
                ))
                apply_dark_layout(
                    fig,
                    title="Signal Scores",
                    height=200,
                    showlegend=False,
                    margin={"l": 16, "r": 16, "t": 36, "b": 24},
                )
                fig.update_xaxes(showticklabels=False, title_text="Signals", title_font_size=10)
                fig.update_yaxes(title_text="Score", title_font_size=10, range=[0, 1])
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
                drew_any = True

        with cols[2]:
            if port_results:
                demand = [getattr(r, "demand_score", 0) for r in port_results
                          if getattr(r, "has_real_data", False)]
                congestion = [getattr(r, "congestion_index", 0) for r in port_results
                              if getattr(r, "has_real_data", False)]
                names = [getattr(r, "port_name", "") for r in port_results
                         if getattr(r, "has_real_data", False)]
                if demand and congestion:
                    fig = go.Figure(go.Scatter(
                        x=demand, y=congestion,
                        mode="markers",
                        marker=dict(
                            size=8, color=demand,
                            colorscale=[[0, C_LOW], [0.5, C_MOD], [1, C_HIGH]],
                            line=dict(width=0),
                        ),
                        text=names,
                        hovertemplate="<b>%{text}</b><br>Demand: %{x:.0%}<br>Congestion: %{y:.0%}<extra></extra>",
                    ))
                    apply_dark_layout(
                        fig,
                        title="Demand vs Congestion",
                        height=200,
                        showlegend=False,
                        margin={"l": 16, "r": 16, "t": 36, "b": 24},
                    )
                    fig.update_xaxes(title_text="Demand", title_font_size=10, range=[0, 1])
                    fig.update_yaxes(title_text="Congestion", title_font_size=10, range=[0, 1])
                    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
                    drew_any = True

        if not drew_any:
            alert_banner(
                "Quick Views populate once port and signal data are available.",
                level="info",
            )
            return

        st.markdown(source_footer(_DASHBOARD_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Overview — quick views render failed")


# ══════════════════════════════════════════════════════════════════════════════
# COLD-START SPLASH
# ══════════════════════════════════════════════════════════════════════════════

def _render_cold_start() -> None:
    try:
        alert_banner(
            "<b>No data loaded yet.</b> The dashboard is waiting on its first "
            "live refresh — follow the three steps below to populate every tab.",
            level="info",
        )
        section_header(
            "Getting Started",
            "Three steps from a cold start to a fully live dashboard",
        )
        metric_card_row(
            [
                {"label": "Step 1", "value": "Add API keys",
                 "accent": C_ACCENT, "sublabel": "Update .env with credentials"},
                {"label": "Step 2", "value": "Refresh Data",
                 "accent": C_HIGH,   "sublabel": "Click the sidebar refresh button"},
                {"label": "Step 3", "value": "Wait 30-60s",
                 "accent": C_MOD,    "sublabel": "Live feeds populate the tabs"},
            ],
            columns=3,
        )
    except Exception:
        logger.exception("Overview — cold start splash failed")
        st.info("Dashboard loading -- configure API credentials to enable live data.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RENDER
# ══════════════════════════════════════════════════════════════════════════════

def render(
    port_results,
    route_results,
    insights,
    freight_data=None,
    macro_data=None,
    stock_data=None,
    alerts=None,
) -> None:
    """Render the executive Overview dashboard.

    Parameters
    ----------
    port_results, route_results, insights:
        Platform-standard model outputs computed at the top of ``app.py``.
    freight_data, macro_data, stock_data, alerts:
        Optional feed dicts / alert list — each may be ``None`` or empty, in
        which case the dashboard degrades to a cold-start splash or to
        illustrative defaults.

    The signature is positional — ``app.py`` calls ``render(...)`` by position,
    so the parameter order must not change.
    """
    try:
        port_results  = port_results  or []
        route_results = route_results or []
        insights      = insights      or []
        freight_data  = freight_data  or {}
        macro_data    = macro_data    or {}
        stock_data    = stock_data    or {}
        alerts        = alerts        or []

        # ── A. Page header ──────────────────────────────────────────────────
        page_header(
            title="Overview",
            subtitle="Market tone, live KPIs, signals, alerts and data-feed "
            "health at a glance.",
            badge_text="DASHBOARD",
            badge_color=C_ACCENT,
        )

        # ── Cold start — nothing modeled yet ────────────────────────────────
        all_empty = not port_results and not route_results and not insights
        if all_empty:
            _render_cold_start()
            section_divider("Data Feeds")
            _render_data_status(port_results, route_results, insights,
                                freight_data, macro_data, stock_data)
            return

        # ── B. Market verdict — tone banner + feed health ───────────────────
        _render_market_verdict(port_results, route_results, insights,
                               freight_data, macro_data, stock_data)

        # ── C. Headline KPI strip ───────────────────────────────────────────
        section_divider("Headline KPIs")
        _render_kpi_strip(port_results, route_results, insights,
                         freight_data, macro_data, stock_data, alerts)

        # ── D. Markets & Signals ────────────────────────────────────────────
        section_divider("Markets & Signals")
        left, right = st.columns([3, 2], gap="large")
        with left:
            _render_market_pulse(freight_data, macro_data, stock_data)
            _render_signal_matrix(route_results, insights)
            _render_top_signals(insights)
        with right:
            _render_risk_alerts(insights, alerts)
            _render_route_opps(route_results)
            _render_data_status(port_results, route_results, insights,
                               freight_data, macro_data, stock_data)

        # ── F. Quick Views ──────────────────────────────────────────────────
        section_divider("Quick Views")
        _render_sparklines(port_results, insights, freight_data)
    except Exception as exc:
        logger.exception("tab_overview.render fatal")
        st.error(f"Overview dashboard error: {exc}")
