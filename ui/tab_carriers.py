"""ui/tab_carriers.py — Carrier Intelligence Tab (refactored to ui/styles).

Sections:
  1. Hero header KPI row
  2. Alliance structure cards
  3. Carrier performance table
  4. Schedule reliability rankings
  5. Market concentration (HHI + ratios)
  6. Blank sailing tracker
  7. Carrier news feed
  8. Per-carrier deep-dive expanders
"""
from __future__ import annotations

import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_SURFACE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    wsj_market_table,
)

try:
    from data.carrier_intelligence import (
        get_alliance_breakdown,
        get_blank_sailing_alerts,
        get_carrier_profiles,
        get_market_concentration,
    )
    _CARRIER_DATA_OK = True
except Exception as _e:
    logger.warning(f"tab_carriers: carrier_intelligence import failed: {_e}")
    _CARRIER_DATA_OK = False


# Tab-local semantic colors (not in the shared palette). These drive the
# carrier/alliance color-coding across the tab and deliberately differ from
# the base palette so the mapping reads clearly in legends.
_C_PURPLE = "#7c6eaf"
_C_CYAN   = "#4a90a4"
_C_ORANGE = "#c4621a"

_ALLIANCE_COLORS: dict[str, str] = {
    "MSC-independent":    C_MOD,
    "Gemini":             C_ACCENT,
    "Premier":            _C_PURPLE,
    "unaffiliated":       C_TEXT3,
}

_CARRIER_COLORS: dict[str, str] = {
    "MSC":         C_MOD,
    "Maersk":      C_ACCENT,
    "CMA CGM":     C_HIGH,
    "COSCO":       C_LOW,
    "Hapag-Lloyd": _C_ORANGE,
    "ONE":         _C_PURPLE,
    "Evergreen":   _C_CYAN,
    "Yang Ming":   "#6ea84a",
    "HMM":         "#9187c7",
    "ZIM":         "#c76aa5",
    "PIL":         "#2faaa0",
    "Wan Hai":     "#c6809a",
}

_IMPACT_COLORS: dict[str, str] = {
    "MINIMAL":     C_HIGH,
    "MODERATE":    C_MOD,
    "SIGNIFICANT": _C_ORANGE,
    "SEVERE":      C_LOW,
}

_OUTLOOK_COLORS: dict[str, str] = {
    "Positive": C_HIGH,
    "Neutral":  C_ACCENT,
    "Cautious": C_MOD,
    "Negative": C_LOW,
}

_OUTLOOK_LABELS: dict[str, str] = {
    "Positive": "BULLISH",
    "Neutral":  "NEUTRAL",
    "Cautious": "CAUTIOUS",
    "Negative": "BEARISH",
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _alliance_color(alliance: str) -> str:
    return _ALLIANCE_COLORS.get(alliance, C_TEXT3)


def _carrier_color(name: str) -> str:
    for k, v in _CARRIER_COLORS.items():
        if k.lower() in name.lower():
            return v
    return C_TEXT2


def _short_name(name: str) -> str:
    mapping = {
        "Mediterranean Shipping":     "MSC",
        "MSC":                         "MSC",
        "Maersk":                      "Maersk",
        "CMA CGM":                     "CMA CGM",
        "COSCO":                       "COSCO",
        "Hapag-Lloyd":                 "Hapag-Lloyd",
        "Ocean Network Express":       "ONE",
        "ONE":                         "ONE",
        "Evergreen":                   "Evergreen",
        "Yang Ming":                   "Yang Ming",
        "HMM":                         "HMM",
        "ZIM":                         "ZIM",
        "PIL":                         "PIL",
        "Pacific International":       "PIL",
        "Wan Hai":                     "Wan Hai",
    }
    for k, v in mapping.items():
        if k.lower() in name.lower():
            return v
    return name.split()[0]


def _reliability_color(pct: float) -> str:
    if pct >= 70:
        return C_HIGH
    if pct >= 60:
        return C_MOD
    return C_LOW


def _outlook_badge(outlook: str) -> str:
    color = _OUTLOOK_COLORS.get(outlook, C_TEXT3)
    label = _OUTLOOK_LABELS.get(outlook, outlook.upper())
    return badge(label, color=color)


def _impact_badge(impact: str) -> str:
    color = _IMPACT_COLORS.get(impact.upper(), C_TEXT3)
    return badge(impact.upper(), color=color)


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


def _rate_cell(val: float) -> str:
    color = C_HIGH if val >= 0 else C_LOW
    sign  = "+" if val >= 0 else ""
    return _mono(f"{sign}{val:.1f}%", color=color)


def _teu_str(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _progress_bar_html(pct: float, color: str, width_px: int = 160) -> str:
    filled = int(width_px * min(pct, 100) / 100)
    return (
        f'<span style="display:inline-block;width:{width_px}px;height:6px;'
        f'background:{C_SURFACE};border-radius:3px;vertical-align:middle;">'
        f'<span style="display:inline-block;width:{filled}px;height:6px;'
        f'background:{color};border-radius:3px;"></span></span>'
    )


# ── Section 1: Hero header ──────────────────────────────────────────────────

def _render_header(profiles: list) -> None:
    try:
        n_carriers = len(profiles)
        total_teu  = sum(p.teu_capacity for p in profiles)
        conc       = get_market_concentration() if _CARRIER_DATA_OK else {}
        hhi        = conc.get("hhi", 0)
        hhi_cat    = conc.get("hhi_category", "—")
        alliances  = get_alliance_breakdown() if _CARRIER_DATA_OK else {}
        n_all      = len([k for k in alliances if k != "unaffiliated"])

        page_header(
            title="Carrier Intelligence Dashboard",
            subtitle="Q1 2026 · Top 12 global container carriers · Alliance structure, reliability & market concentration",
            icon="🚢",
        )
        metric_card_row(
            [
                {"label": "Carriers Tracked",  "value": str(n_carriers),           "accent": C_ACCENT,
                 "sublabel": "global container lines"},
                {"label": "Global Capacity",   "value": f"{_teu_str(total_teu)} TEU", "accent": C_ACCENT,
                 "sublabel": "tracked fleet total"},
                {"label": "Market HHI",        "value": f"{hhi:,.0f}",             "accent": C_MOD,
                 "sublabel": hhi_cat},
                {"label": "Alliance Groups",   "value": str(n_all),                 "accent": C_ACCENT,
                 "sublabel": "active cooperation structures"},
            ],
            columns=4,
        )
    except Exception as exc:
        logger.error(f"tab_carriers._render_header: {exc}")
        st.warning("Header unavailable.")


# ── Section 2: Alliance structure ───────────────────────────────────────────

def _render_alliance_panel(profiles: list) -> None:
    try:
        section_header("Alliance Structure", subtitle="Current cooperation landscape — Q1 2026")

        alliance_defs = [
            {"name": "MSC — Independent",  "key": "MSC-independent", "color": C_MOD,
             "desc": "Operates largest global fleet independently post-2M dissolution (Feb 2025)"},
            {"name": "Gemini Cooperation", "key": "Gemini",          "color": C_ACCENT,
             "desc": "Launched Feb 2025 — Maersk + Hapag-Lloyd focusing on schedule reliability"},
            {"name": "Premier Alliance",   "key": "Premier",         "color": _C_PURPLE,
             "desc": "CMA CGM, COSCO, ONE, Evergreen, Yang Ming, HMM — Asia-Europe & Transpacific"},
            {"name": "Unaffiliated",       "key": "unaffiliated",    "color": C_TEXT3,
             "desc": "ZIM, PIL, Wan Hai — operate independently without major alliance membership"},
        ]

        profile_map: dict[str, list] = {}
        for p in profiles:
            profile_map.setdefault(p.alliance, []).append(p)

        cards_html = []
        for adef in alliance_defs:
            members = profile_map.get(adef["key"], [])
            color = adef["color"]
            combined_share = sum(m.market_share_pct for m in members)
            combined_teu   = sum(m.teu_capacity for m in members)
            member_tags = "".join(
                f'<span style="display:inline-block;padding:3px 9px;margin:3px 3px 0 0;'
                f'background:{color}22;color:{color};border:1px solid {color}44;'
                f'border-radius:3px;font-size:11px;font-family:var(--sans);font-weight:600;">'
                f'{_short_name(m.name)}</span>'
                for m in members
            )
            cards_html.append(
                f'<div style="flex:1;min-width:220px;background:{C_CARD};'
                f'border:1px solid {C_BORDER};border-radius:6px;padding:18px 16px;'
                f'border-top:3px solid {color};">'
                f'<div style="font-size:15px;font-weight:700;color:{color};'
                f'font-family:var(--serif);margin-bottom:6px;">{adef["name"]}</div>'
                f'<div style="font-size:11px;font-family:var(--sans);color:{C_TEXT3};'
                f'margin-bottom:12px;line-height:1.5;">{adef["desc"]}</div>'
                f'<div style="margin-bottom:10px;">{member_tags}</div>'
                f'<div style="display:flex;gap:16px;margin-top:10px;padding-top:10px;'
                f'border-top:1px solid {C_BORDER};">'
                f'<div><div style="font-size:10px;font-family:var(--sans);color:{C_TEXT3};'
                f'text-transform:uppercase;letter-spacing:0.08em;">Combined Share</div>'
                f'<div style="font-size:18px;font-weight:700;font-family:var(--mono);color:{color};">'
                f'{combined_share:.1f}%</div></div>'
                f'<div><div style="font-size:10px;font-family:var(--sans);color:{C_TEXT3};'
                f'text-transform:uppercase;letter-spacing:0.08em;">TEU Capacity</div>'
                f'<div style="font-size:18px;font-weight:700;font-family:var(--mono);color:{C_TEXT};">'
                f'{_teu_str(combined_teu)}</div></div>'
                f'</div></div>'
            )

        st.html(f'<div style="display:flex;flex-wrap:wrap;gap:12px;">{"".join(cards_html)}</div>')
    except Exception as exc:
        logger.error(f"tab_carriers._render_alliance_panel: {exc}")
        st.warning("Alliance panel unavailable.")


# ── Section 3: Performance table ────────────────────────────────────────────

def _render_performance_table(profiles: list) -> None:
    try:
        section_header("Carrier Performance Table", subtitle="All 12 tracked carriers · Q1 2026 data")

        rows = []
        for p in sorted(profiles, key=lambda x: x.market_share_pct, reverse=True):
            sname = _short_name(p.name)
            rows.append([
                _sans(sname, color=_carrier_color(p.name), weight=700),
                _sans(p.alliance, color=_alliance_color(p.alliance), weight=600),
                _mono(f"{p.market_share_pct:.1f}%", color=C_TEXT),
                _sans(f"{p.fleet_size} vessels", color=C_TEXT2),
                _mono(f"{p.schedule_reliability:.1f}%", color=_reliability_color(p.schedule_reliability)),
                _rate_cell(p.ytd_rate_change),
                _mono(f"{p.blank_sailing_rate:.1f}%", color=C_TEXT2),
                _outlook_badge(p.outlook),
            ])
        wsj_market_table(
            headers=[
                "Carrier", "Alliance", "Mkt Share", "Fleet",
                "Schedule Reliability", "YTD Rate Δ", "Blank Sail %", "Outlook",
            ],
            rows=rows,
        )
    except Exception as exc:
        logger.error(f"tab_carriers._render_performance_table: {exc}")
        st.warning("Performance table unavailable.")


# ── Section 4: Reliability rankings ─────────────────────────────────────────

_RELIABILITY_TREND_DELTAS: dict[str, float] = {
    "Hapag-Lloyd": +1.8, "Maersk": +2.1, "ONE": -0.6,
    "Wan Hai": +0.4, "HMM": -1.2, "CMA CGM": -0.9,
    "COSCO": +0.7, "Yang Ming": -1.5, "Evergreen": -2.3,
    "MSC": -3.1, "PIL": +0.2, "ZIM": -2.8,
}


def _render_reliability_rankings(profiles: list) -> None:
    try:
        section_header(
            "Schedule Reliability Rankings",
            subtitle="Ranked 1–12 by on-time arrival rate (past 6 months)",
        )
        sorted_profiles = sorted(profiles, key=lambda p: p.schedule_reliability, reverse=True)
        rows = []
        for i, p in enumerate(sorted_profiles):
            rel = p.schedule_reliability
            rel_color = _reliability_color(rel)
            delta = next((v for k, v in _RELIABILITY_TREND_DELTAS.items() if k.lower() in p.name.lower()), 0.0)
            delta_str = f"+{delta:.1f}pp" if delta >= 0 else f"{delta:.1f}pp"
            delta_color = C_HIGH if delta >= 0 else C_LOW
            rows.append([
                _mono(str(i + 1), color=C_TEXT),
                _sans(_short_name(p.name), color=_carrier_color(p.name), weight=700),
                _progress_bar_html(rel, rel_color, width_px=160),
                _mono(f"{rel:.1f}%", color=rel_color),
                _mono(f"{delta_str} QoQ", color=delta_color),
                _outlook_badge(p.outlook),
            ])
        wsj_market_table(
            headers=["#", "Carrier", "Reliability", "On-time %", "QoQ Δ", "Outlook"],
            rows=rows,
        )
    except Exception as exc:
        logger.error(f"tab_carriers._render_reliability_rankings: {exc}")
        st.warning("Reliability rankings unavailable.")


# ── Section 5: Market concentration ─────────────────────────────────────────

def _render_market_concentration() -> None:
    try:
        section_header("Market Concentration", subtitle="Herfindahl-Hirschman Index (HHI) analysis")

        if not _CARRIER_DATA_OK:
            st.info("Carrier data module unavailable.")
            return

        conc    = get_market_concentration()
        hhi     = conc.get("hhi", 0)
        hhi_cat = conc.get("hhi_category", "—")
        top3    = conc.get("top3_share_pct", 0)
        top5    = conc.get("top5_share_pct", 0)
        top10   = conc.get("top10_share_pct", 0)
        total   = conc.get("total_tracked_share_pct", 0)

        hhi_color = C_LOW if hhi >= 2500 else (C_MOD if hhi >= 1500 else C_HIGH)
        hhi_bar_w = int(280 * min(hhi, 10000) / 10000)
        hhi_desc = (
            "Market is highly concentrated — regulatory scrutiny likely" if hhi >= 2500
            else "Moderately concentrated market with oligopolistic dynamics" if hhi >= 1500
            else "Competitive market structure with distributed capacity"
        )

        ratios = [
            ("Top 3 carriers",  top3,  C_LOW),
            ("Top 5 carriers",  top5,  _C_ORANGE),
            ("Top 10 carriers", top10, C_MOD),
            ("All 12 tracked",  total, C_HIGH),
        ]
        ratio_rows = []
        for label, share, color in ratios:
            bar_w = int(200 * share / 100)
            ratio_rows.append(
                f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">'
                f'<div style="width:110px;font-size:12px;font-family:var(--sans);color:{C_TEXT2};">{label}</div>'
                f'<div style="width:200px;height:6px;background:{C_SURFACE};border-radius:3px;">'
                f'<div style="width:{bar_w}px;height:6px;background:{color};border-radius:3px;"></div></div>'
                f'<div style="font-size:14px;font-weight:700;font-family:var(--mono);color:{color};">{share:.1f}%</div>'
                f'</div>'
            )

        st.html(
            f'<div style="display:flex;gap:16px;flex-wrap:wrap;">'
            f'<div style="flex:1;min-width:260px;background:{C_CARD};border:1px solid {C_BORDER};'
            f'border-radius:6px;padding:20px;border-top:3px solid {hhi_color};">'
            f'<div style="font-size:10px;font-weight:700;font-family:var(--sans);color:{C_TEXT3};'
            f'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:8px;">HHI Score</div>'
            f'<div style="font-size:42px;font-weight:900;font-family:var(--serif);color:{hhi_color};line-height:1;">'
            f'{hhi:,.0f}</div>'
            f'<div style="font-size:13px;font-weight:700;font-family:var(--sans);color:{hhi_color};margin:6px 0;">'
            f'{hhi_cat}</div>'
            f'<div style="margin:12px 0 4px;">'
            f'<div style="height:8px;width:280px;background:{C_SURFACE};border-radius:3px;">'
            f'<div style="height:8px;width:{hhi_bar_w}px;background:{hhi_color};border-radius:3px;"></div></div>'
            f'<div style="display:flex;justify-content:space-between;font-size:10px;'
            f'font-family:var(--sans);color:{C_TEXT3};margin-top:3px;">'
            f'<span>0</span><span>1,500</span><span>2,500</span><span>10,000</span></div>'
            f'</div>'
            f'<div style="font-size:12px;font-family:var(--sans);color:{C_TEXT3};margin-top:10px;line-height:1.5;">'
            f'{hhi_desc}</div>'
            f'</div>'
            f'<div style="flex:1;min-width:260px;background:{C_CARD};border:1px solid {C_BORDER};'
            f'border-radius:6px;padding:20px;">'
            f'<div style="font-size:10px;font-weight:700;font-family:var(--sans);color:{C_TEXT3};'
            f'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:16px;">Concentration Ratios</div>'
            f'{"".join(ratio_rows)}</div></div>'
        )
    except Exception as exc:
        logger.error(f"tab_carriers._render_market_concentration: {exc}")
        st.warning("Market concentration data unavailable.")


# ── Section 6: Blank sailing tracker ────────────────────────────────────────

def _blank_impact_level(teu: int) -> str:
    if teu >= 20000:
        return "SEVERE"
    if teu >= 14000:
        return "SIGNIFICANT"
    if teu >= 10000:
        return "MODERATE"
    return "MINIMAL"


def _render_blank_sailing_tracker(alerts: list[dict]) -> None:
    try:
        section_header("Blank Sailing Tracker", subtitle="Recent capacity removal announcements")

        if not alerts:
            st.info("No blank sailing alerts available.")
            return

        total_teu = sum(a.get("teu_impact", 0) for a in alerts)
        metric_card_row(
            [
                {"label": "Alerts tracked",   "value": str(len(alerts)),          "accent": C_ACCENT},
                {"label": "TEU total removed", "value": f"{total_teu:,}",          "accent": C_LOW},
            ],
            columns=2,
        )

        rows = []
        for alert in alerts:
            carrier = alert.get("carrier",       "—")
            trade   = alert.get("trade_lane",    "—")
            week    = alert.get("departure_week", "—")
            teu     = alert.get("teu_impact",    0)
            impact  = _blank_impact_level(teu)
            rows.append([
                _sans(carrier, color=_carrier_color(carrier), weight=700),
                _sans(trade,   color=C_TEXT2),
                _sans(week,    color=C_TEXT3),
                _mono(f"{teu:,} TEU", color=C_TEXT),
                _impact_badge(impact),
            ])
        wsj_market_table(
            headers=["Carrier", "Trade Lane", "Departure Week", "TEUs Removed", "Impact"],
            rows=rows,
        )
    except Exception as exc:
        logger.error(f"tab_carriers._render_blank_sailing_tracker: {exc}")
        st.warning("Blank sailing tracker unavailable.")


# ── Section 7: Carrier news feed ────────────────────────────────────────────

def _render_carrier_news() -> None:
    try:
        section_header("Carrier News Feed", subtitle="Live intelligence from carrier RSS feeds")

        try:
            from data.carrier_intelligence import fetch_carrier_updates
            updates_map = fetch_carrier_updates(max_per_carrier=3, cache_ttl_hours=6.0)
        except Exception as feed_exc:
            logger.warning(f"tab_carriers: news feed unavailable: {feed_exc}")
            updates_map = {}

        if not updates_map or all(len(v) == 0 for v in updates_map.values()):
            st.html(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:6px;padding:20px;text-align:center;'
                f'font-family:var(--sans);color:{C_TEXT3};">'
                f'News feeds unavailable — feedparser library may not be installed '
                f'or RSS sources are unreachable.</div>'
            )
            return

        items_html = []
        for carrier, updates in updates_map.items():
            for upd in updates:
                try:
                    carrier_color = _carrier_color(carrier)
                    sentiment   = upd.sentiment if hasattr(upd, "sentiment") else 0.0
                    sent_color  = C_HIGH if sentiment > 0.1 else (C_LOW if sentiment < -0.1 else C_TEXT3)
                    sent_label  = "POSITIVE" if sentiment > 0.1 else ("NEGATIVE" if sentiment < -0.1 else "NEUTRAL")
                    ts          = upd.published_dt.strftime("%b %d, %H:%M UTC") if hasattr(upd, "published_dt") else "—"
                    headline    = (upd.headline or "")[:120]
                    url         = getattr(upd, "url", "#") or "#"
                    category    = (getattr(upd, "category", "general") or "general").upper()

                    items_html.append(
                        f'<div style="padding:14px 16px;border-bottom:1px solid {C_BORDER};">'
                        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">'
                        f'<span style="font-size:11px;font-weight:700;color:{carrier_color};'
                        f'font-family:var(--sans);padding:2px 7px;background:{carrier_color}22;'
                        f'border-radius:3px;">{carrier}</span>'
                        f'<span style="font-size:10px;color:{C_TEXT3};font-family:var(--sans);'
                        f'background:{C_SURFACE};padding:2px 6px;border-radius:3px;">{category}</span>'
                        f'<span style="font-size:10px;font-weight:700;font-family:var(--sans);'
                        f'color:{sent_color};margin-left:auto;">{sent_label}</span>'
                        f'<span style="font-size:10px;font-family:var(--sans);color:{C_TEXT3};">{ts}</span>'
                        f'</div>'
                        f'<div style="font-size:13px;font-family:var(--serif);color:{C_TEXT};line-height:1.5;">'
                        f'<a href="{url}" target="_blank" style="color:{C_TEXT};text-decoration:none;">'
                        f'{headline}</a></div>'
                        f'</div>'
                    )
                except Exception as item_exc:
                    logger.debug(f"tab_carriers: news item render error: {item_exc}")

        if items_html:
            st.html(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:6px;overflow:hidden;">{"".join(items_html)}</div>'
            )
        else:
            st.info("No news items available.")
    except Exception as exc:
        logger.error(f"tab_carriers._render_carrier_news: {exc}")
        st.warning("News feed section unavailable.")


# ── Section 8: Deep-dive expanders ──────────────────────────────────────────

def _render_deep_dives(profiles: list) -> None:
    try:
        section_header("Carrier Deep-Dive", subtitle="Per-carrier risk, strengths & financial highlights")

        for p in sorted(profiles, key=lambda x: x.market_share_pct, reverse=True):
            sname = _short_name(p.name)
            carrier_color = _carrier_color(p.name)

            with st.expander(
                f"{sname}  ·  {p.market_share_pct:.1f}% market share  ·  {p.alliance}",
                expanded=False,
            ):
                try:
                    margin_color = C_HIGH if p.q_net_margin_pct >= 8 else (C_MOD if p.q_net_margin_pct >= 4 else C_LOW)
                    rel_color    = _reliability_color(p.schedule_reliability)

                    ticker = p.ticker if hasattr(p, "ticker") and p.ticker != "private" else None
                    ticker_html = (
                        f'<span style="font-size:11px;font-family:var(--mono);background:{C_ACCENT}22;'
                        f'color:{C_ACCENT};padding:2px 8px;border-radius:3px;'
                        f'border:1px solid {C_ACCENT}44;margin-left:8px;">{p.ticker}</span>'
                        if ticker
                        else f'<span style="font-size:11px;font-family:var(--sans);color:{C_TEXT3};margin-left:8px;">Private</span>'
                    )

                    st.html(
                        f'<div style="margin-bottom:6px;">'
                        f'<span style="font-size:16px;font-weight:700;font-family:var(--serif);color:{carrier_color};">{p.name}</span>'
                        f'{ticker_html}'
                        f'<span style="font-size:11px;font-family:var(--sans);color:{_alliance_color(p.alliance)};'
                        f'margin-left:10px;font-weight:600;">{p.alliance}</span>'
                        f'</div>'
                        f'<div style="font-size:12px;font-family:var(--sans);color:{C_TEXT3};margin-bottom:14px;">'
                        f'Fleet: {p.fleet_size} vessels &nbsp;|&nbsp; '
                        f'Capacity: {_teu_str(p.teu_capacity)} TEU &nbsp;|&nbsp; '
                        f'YTD Rate: {_rate_cell(p.ytd_rate_change)}'
                        f'</div>'
                    )

                    metric_card_row(
                        [
                            {"label": "Q Revenue",       "value": f"${p.q_revenue_bn:.1f}B",   "accent": C_ACCENT},
                            {"label": "Net Margin",      "value": f"{p.q_net_margin_pct:.1f}%", "accent": margin_color},
                            {"label": "Schedule Rel.",   "value": f"{p.schedule_reliability:.1f}%", "accent": rel_color},
                            {"label": "Blank Sailing",   "value": f"{p.blank_sailing_rate:.1f}%",   "accent": C_TEXT2},
                            {"label": "Outlook",          "value": _OUTLOOK_LABELS.get(p.outlook, p.outlook.upper()),
                             "accent": _OUTLOOK_COLORS.get(p.outlook, C_TEXT3)},
                        ],
                        columns=5,
                    )

                    risks     = list(getattr(p, "key_risks",     []) or [])
                    strengths = list(getattr(p, "key_strengths", []) or [])
                    risk_items     = "".join(f'<li style="margin-bottom:5px;color:{C_TEXT2};">{r}</li>' for r in risks)
                    strength_items = "".join(f'<li style="margin-bottom:5px;color:{C_TEXT2};">{s}</li>' for s in strengths)
                    st.html(
                        f'<div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:14px;">'
                        f'<div style="flex:1;min-width:220px;background:{C_LOW}0a;'
                        f'border:1px solid {C_LOW}44;border-radius:6px;padding:14px;">'
                        f'<div style="font-size:11px;font-weight:700;font-family:var(--sans);color:{C_LOW};'
                        f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:10px;">Key Risks</div>'
                        f'<ul style="margin:0;padding-left:18px;font-family:var(--sans);">{risk_items}</ul></div>'
                        f'<div style="flex:1;min-width:220px;background:{C_HIGH}0a;'
                        f'border:1px solid {C_HIGH}44;border-radius:6px;padding:14px;">'
                        f'<div style="font-size:11px;font-weight:700;font-family:var(--sans);color:{C_HIGH};'
                        f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:10px;">Key Strengths</div>'
                        f'<ul style="margin:0;padding-left:18px;font-family:var(--sans);">{strength_items}</ul></div>'
                        f'</div>'
                    )
                except Exception as inner_exc:
                    logger.error(f"tab_carriers._render_deep_dives [{sname}]: {inner_exc}")
                    st.warning(f"Deep-dive unavailable for {sname}.")
    except Exception as exc:
        logger.error(f"tab_carriers._render_deep_dives: {exc}")
        st.warning("Deep-dive section unavailable.")


# ── Main render ─────────────────────────────────────────────────────────────

def render(port_results=None, route_results=None, insights=None) -> None:
    """Render the Carrier Intelligence tab."""
    if not _CARRIER_DATA_OK:
        st.error(
            "Carrier intelligence data module failed to load. "
            "Check that `data/carrier_intelligence.py` is present and imports correctly."
        )
        return

    profiles: list = []
    alerts: list[dict] = []
    try:
        profiles = get_carrier_profiles()
        logger.info(f"tab_carriers: loaded {len(profiles)} carrier profiles")
    except Exception as exc:
        logger.error(f"tab_carriers: get_carrier_profiles failed: {exc}")
        st.error("Failed to load carrier profiles.")
        return

    try:
        alerts = get_blank_sailing_alerts()
        logger.info(f"tab_carriers: loaded {len(alerts)} blank sailing alerts")
    except Exception as exc:
        logger.warning(f"tab_carriers: get_blank_sailing_alerts failed: {exc}")
        alerts = []

    _render_header(profiles)
    section_divider("Alliance Structure")
    _render_alliance_panel(profiles)
    section_divider("Performance Table")
    _render_performance_table(profiles)

    section_divider("Reliability & Concentration")
    col_a, col_b = st.columns([3, 2])
    with col_a:
        _render_reliability_rankings(profiles)
    with col_b:
        _render_market_concentration()

    section_divider("Blank Sailing Tracker")
    _render_blank_sailing_tracker(alerts)
    section_divider("News Feed")
    _render_carrier_news()
    section_divider("Deep Dive")
    _render_deep_dives(profiles)
