"""ui/tab_carriers.py — Carrier Intelligence Tab.

Comprehensive carrier intelligence dashboard: alliance structure, performance
table, schedule reliability rankings, market concentration, blank sailing
tracker, carrier news feed, and per-carrier deep-dive expanders.

Integration:
    from ui.tab_carriers import render as render_carriers
    with tab_carriers:
        render_carriers(port_results, route_results, insights)
"""
from __future__ import annotations

from typing import Optional

import streamlit as st
from loguru import logger

# ── Carrier data import ────────────────────────────────────────────────────────
try:
    from data.carrier_intelligence import (
        get_carrier_profiles,
        get_alliance_breakdown,
        get_market_concentration,
        get_schedule_reliability_ranking,
        get_blank_sailing_alerts,
    )
    _CARRIER_DATA_OK = True
except Exception as _e:
    logger.warning(f"tab_carriers: carrier_intelligence import failed: {_e}")
    _CARRIER_DATA_OK = False

# ── Color palette ──────────────────────────────────────────────────────────────
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
C_ORANGE  = "#f97316"

# Alliance color mapping
_ALLIANCE_COLORS: dict[str, str] = {
    "MSC-independent":    C_MOD,
    "Gemini":             C_ACCENT,
    "Premier":            C_PURPLE,
    "unaffiliated":       C_TEXT3,
}

# Carrier short-name color mapping
_CARRIER_COLORS: dict[str, str] = {
    "MSC":         C_MOD,
    "Maersk":      C_ACCENT,
    "CMA CGM":     C_HIGH,
    "COSCO":       C_LOW,
    "Hapag-Lloyd": C_ORANGE,
    "ONE":         C_PURPLE,
    "Evergreen":   C_CYAN,
    "Yang Ming":   "#84cc16",
    "HMM":         "#a78bfa",
    "ZIM":         "#ec4899",
    "PIL":         "#14b8a6",
    "Wan Hai":     "#f472b6",
}

# Impact level colors
_IMPACT_COLORS: dict[str, str] = {
    "MINIMAL":     C_HIGH,
    "MODERATE":    C_MOD,
    "SIGNIFICANT": C_ORANGE,
    "SEVERE":      C_LOW,
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _alliance_color(alliance: str) -> str:
    return _ALLIANCE_COLORS.get(alliance, C_TEXT3)


def _carrier_color(name: str) -> str:
    for k, v in _CARRIER_COLORS.items():
        if k.lower() in name.lower():
            return v
    return C_TEXT2


def _short_name(name: str) -> str:
    """Return a short carrier label for display."""
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
    # Fallback: first word
    return name.split()[0]


def _reliability_color(pct: float) -> str:
    if pct >= 70:
        return C_HIGH
    if pct >= 60:
        return C_MOD
    return C_LOW


def _outlook_badge(outlook: str) -> str:
    styles = {
        "Positive": f"background:{C_HIGH}22;color:{C_HIGH};border:1px solid {C_HIGH}44",
        "Neutral":  f"background:{C_ACCENT}22;color:{C_ACCENT};border:1px solid {C_ACCENT}44",
        "Cautious": f"background:{C_MOD}22;color:{C_MOD};border:1px solid {C_MOD}44",
        "Negative": f"background:{C_LOW}22;color:{C_LOW};border:1px solid {C_LOW}44",
    }
    style = styles.get(outlook, f"background:{C_TEXT3}22;color:{C_TEXT3}")
    label_map = {
        "Positive": "BULLISH",
        "Neutral":  "NEUTRAL",
        "Cautious": "CAUTIOUS",
        "Negative": "BEARISH",
    }
    label = label_map.get(outlook, outlook.upper())
    return (
        f'<span style="padding:2px 8px;border-radius:4px;font-size:11px;'
        f'font-weight:700;letter-spacing:0.5px;{style}">{label}</span>'
    )


def _rate_cell(val: float) -> str:
    color = C_HIGH if val >= 0 else C_LOW
    arrow = "▲" if val >= 0 else "▼"
    return f'<span style="color:{color};font-weight:600">{arrow} {abs(val):.1f}%</span>'


def _impact_badge(impact: str) -> str:
    color = _IMPACT_COLORS.get(impact.upper(), C_TEXT3)
    return (
        f'<span style="padding:2px 8px;border-radius:4px;font-size:11px;'
        f'font-weight:700;background:{color}22;color:{color};border:1px solid {color}44">'
        f'{impact.upper()}</span>'
    )


def _teu_str(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n)


def _progress_bar(pct: float, color: str, max_width: int = 200) -> str:
    """Pure CSS progress bar, no JS."""
    width_px = int(max_width * min(pct, 100) / 100)
    return (
        f'<div style="display:inline-block;width:{max_width}px;height:8px;'
        f'background:{C_SURFACE};border-radius:4px;vertical-align:middle">'
        f'<div style="width:{width_px}px;height:8px;background:{color};'
        f'border-radius:4px"></div></div>'
    )


def _section_header(title: str, subtitle: str = "") -> None:
    sub_html = (
        f'<div style="font-size:13px;color:{C_TEXT3};margin-top:2px">{subtitle}</div>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="margin:24px 0 12px 0">'
        f'<div style="font-size:16px;font-weight:700;color:{C_TEXT};'
        f'letter-spacing:0.3px">{title}</div>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )


# ── Section 1: Hero header ─────────────────────────────────────────────────────

def _render_header(profiles: list) -> None:
    try:
        n_carriers = len(profiles)
        total_teu = sum(p.teu_capacity for p in profiles)
        conc = get_market_concentration() if _CARRIER_DATA_OK else {}
        hhi = conc.get("hhi", 0)
        hhi_cat = conc.get("hhi_category", "—")
        alliances = get_alliance_breakdown() if _CARRIER_DATA_OK else {}
        n_alliances = len([k for k in alliances if k not in ("unaffiliated",)])

        metrics = [
            ("CARRIERS TRACKED", str(n_carriers), "global container lines"),
            ("GLOBAL CAPACITY", _teu_str(total_teu) + " TEU", "tracked fleet total"),
            ("MARKET HHI", f"{hhi:,.0f}", hhi_cat),
            ("ALLIANCE GROUPS", str(n_alliances), "active cooperation structures"),
        ]

        cols_html = ""
        for label, value, sub in metrics:
            cols_html += (
                f'<div style="flex:1;padding:20px 24px;background:{C_CARD};'
                f'border:1px solid {C_BORDER};border-radius:10px;text-align:center">'
                f'<div style="font-size:11px;font-weight:700;color:{C_TEXT3};'
                f'letter-spacing:1px;text-transform:uppercase;margin-bottom:8px">{label}</div>'
                f'<div style="font-size:28px;font-weight:800;color:{C_ACCENT};'
                f'line-height:1">{value}</div>'
                f'<div style="font-size:12px;color:{C_TEXT3};margin-top:6px">{sub}</div>'
                f'</div>'
            )

        st.markdown(
            f'<div style="background:linear-gradient(135deg,{C_SURFACE},{C_CARD});'
            f'border:1px solid {C_BORDER};border-radius:12px;padding:24px;margin-bottom:20px">'
            f'<div style="font-size:22px;font-weight:800;color:{C_TEXT};margin-bottom:6px">'
            f'Carrier Intelligence Dashboard</div>'
            f'<div style="font-size:13px;color:{C_TEXT2};margin-bottom:20px">'
            f'Q1 2026 · Top 12 global container carriers · Alliance structure, reliability &amp; market concentration</div>'
            f'<div style="display:flex;gap:12px">{cols_html}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.error(f"tab_carriers._render_header: {exc}")
        st.warning("Header unavailable.")


# ── Section 2: Alliance structure panel ───────────────────────────────────────

def _render_alliance_panel(profiles: list) -> None:
    try:
        _section_header("Alliance Structure", "Current cooperation landscape — Q1 2026")

        alliance_defs = [
            {
                "name": "MSC — Independent",
                "key": "MSC-independent",
                "color": C_MOD,
                "desc": "Operates largest global fleet independently post-2M dissolution (Feb 2025)",
            },
            {
                "name": "Gemini Cooperation",
                "key": "Gemini",
                "color": C_ACCENT,
                "desc": "Launched Feb 2025 — Maersk + Hapag-Lloyd focusing on schedule reliability",
            },
            {
                "name": "Premier Alliance",
                "key": "Premier",
                "color": C_PURPLE,
                "desc": "CMA CGM, COSCO, ONE, Evergreen, Yang Ming, HMM — Asia-Europe &amp; Transpacific",
            },
            {
                "name": "Unaffiliated",
                "key": "unaffiliated",
                "color": C_TEXT3,
                "desc": "ZIM, PIL, Wan Hai — operate independently without major alliance membership",
            },
        ]

        # Build per-alliance data
        profile_map: dict[str, list] = {}
        for p in profiles:
            profile_map.setdefault(p.alliance, []).append(p)

        cards_html = ""
        for adef in alliance_defs:
            key = adef["key"]
            color = adef["color"]
            members = profile_map.get(key, [])
            combined_share = sum(m.market_share_pct for m in members)
            combined_teu = sum(m.teu_capacity for m in members)
            member_tags = "".join(
                f'<span style="display:inline-block;padding:3px 9px;margin:3px 3px 0 0;'
                f'background:{color}1a;color:{color};border:1px solid {color}33;'
                f'border-radius:6px;font-size:11px;font-weight:600">'
                f'{_short_name(m.name)}</span>'
                for m in members
            )
            cards_html += (
                f'<div style="flex:1;min-width:220px;background:{C_CARD};'
                f'border:1px solid {color}33;border-radius:10px;padding:18px 16px;'
                f'border-top:3px solid {color}">'
                f'<div style="font-size:14px;font-weight:700;color:{color};'
                f'margin-bottom:6px">{adef["name"]}</div>'
                f'<div style="font-size:11px;color:{C_TEXT3};margin-bottom:12px;line-height:1.5">'
                f'{adef["desc"]}</div>'
                f'<div style="margin-bottom:10px">{member_tags}</div>'
                f'<div style="display:flex;gap:16px;margin-top:10px;padding-top:10px;'
                f'border-top:1px solid {C_BORDER}">'
                f'<div><div style="font-size:10px;color:{C_TEXT3};text-transform:uppercase;'
                f'letter-spacing:0.8px">Combined Share</div>'
                f'<div style="font-size:18px;font-weight:700;color:{color}">'
                f'{combined_share:.1f}%</div></div>'
                f'<div><div style="font-size:10px;color:{C_TEXT3};text-transform:uppercase;'
                f'letter-spacing:0.8px">TEU Capacity</div>'
                f'<div style="font-size:18px;font-weight:700;color:{C_TEXT}">'
                f'{_teu_str(combined_teu)}</div></div>'
                f'</div></div>'
            )

        st.markdown(
            f'<div style="display:flex;flex-wrap:wrap;gap:12px">{cards_html}</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.error(f"tab_carriers._render_alliance_panel: {exc}")
        st.warning("Alliance panel unavailable.")


# ── Section 3: Carrier performance table ──────────────────────────────────────

def _render_performance_table(profiles: list) -> None:
    try:
        _section_header("Carrier Performance Table", "All 12 tracked carriers · Q1 2026 data")

        header_cells = [
            "CARRIER", "ALLIANCE", "MKT SHARE", "FLEET",
            "SCHEDULE RELIABILITY", "YTD RATE Δ", "BLANK SAIL %", "OUTLOOK",
        ]
        header_html = "".join(
            f'<th style="padding:10px 12px;text-align:left;font-size:10px;'
            f'font-weight:700;color:{C_TEXT3};letter-spacing:0.8px;'
            f'text-transform:uppercase;border-bottom:1px solid {C_BORDER};'
            f'white-space:nowrap">{h}</th>'
            for h in header_cells
        )

        rows_html = ""
        for i, p in enumerate(sorted(profiles, key=lambda x: x.market_share_pct, reverse=True)):
            sname = _short_name(p.name)
            carrier_color = _carrier_color(p.name)
            a_color = _alliance_color(p.alliance)
            rel_color = _reliability_color(p.schedule_reliability)
            row_bg = C_CARD if i % 2 == 0 else C_SURFACE

            rows_html += (
                f'<tr style="background:{row_bg}">'
                f'<td style="padding:10px 12px;font-size:13px;font-weight:700;'
                f'color:{carrier_color};white-space:nowrap">{sname}</td>'
                f'<td style="padding:10px 12px">'
                f'<span style="font-size:11px;color:{a_color};font-weight:600">'
                f'{p.alliance}</span></td>'
                f'<td style="padding:10px 12px;font-size:13px;font-weight:700;color:{C_TEXT}">'
                f'{p.market_share_pct:.1f}%</td>'
                f'<td style="padding:10px 12px;font-size:13px;color:{C_TEXT2}">'
                f'{p.fleet_size} vessels</td>'
                f'<td style="padding:10px 12px">'
                f'<span style="font-size:13px;font-weight:700;color:{rel_color}">'
                f'{p.schedule_reliability:.1f}%</span></td>'
                f'<td style="padding:10px 12px">{_rate_cell(p.ytd_rate_change)}</td>'
                f'<td style="padding:10px 12px;font-size:13px;color:{C_TEXT2}">'
                f'{p.blank_sailing_rate:.1f}%</td>'
                f'<td style="padding:10px 12px">{_outlook_badge(p.outlook)}</td>'
                f'</tr>'
            )

        st.markdown(
            f'<div style="overflow-x:auto;background:{C_SURFACE};'
            f'border:1px solid {C_BORDER};border-radius:10px">'
            f'<table style="width:100%;border-collapse:collapse">'
            f'<thead><tr>{header_html}</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.error(f"tab_carriers._render_performance_table: {exc}")
        st.warning("Performance table unavailable.")


# ── Section 4: Schedule reliability rankings ───────────────────────────────────

def _render_reliability_rankings(profiles: list) -> None:
    try:
        _section_header("Schedule Reliability Rankings", "Ranked 1–12 by on-time arrival rate (past 6 months)")

        # Trend delta vs last quarter (mock deltas — realistic per carrier)
        _TREND_DELTAS: dict[str, float] = {
            "Hapag-Lloyd": +1.8, "Maersk": +2.1, "ONE": -0.6,
            "Wan Hai": +0.4, "HMM": -1.2, "CMA CGM": -0.9,
            "COSCO": +0.7, "Yang Ming": -1.5, "Evergreen": -2.3,
            "MSC": -3.1, "PIL": +0.2, "ZIM": -2.8,
        }

        sorted_profiles = sorted(profiles, key=lambda p: p.schedule_reliability, reverse=True)

        rows_html = ""
        for i, p in enumerate(sorted_profiles):
            rank = i + 1
            sname = _short_name(p.name)
            rel = p.schedule_reliability
            rel_color = _reliability_color(rel)
            carrier_color = _carrier_color(p.name)

            delta = next((v for k, v in _TREND_DELTAS.items() if k.lower() in p.name.lower()), 0.0)
            delta_str = f"+{delta:.1f}pp" if delta >= 0 else f"{delta:.1f}pp"
            delta_color = C_HIGH if delta >= 0 else C_LOW

            # Rank medal
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"#{rank}")
            medal_html = (
                f'<span style="font-size:15px">{medal}</span>'
                if rank <= 3
                else f'<span style="font-size:13px;color:{C_TEXT3};font-weight:600">#{rank}</span>'
            )

            bar = _progress_bar(rel, rel_color, 160)

            rows_html += (
                f'<div style="display:flex;align-items:center;gap:16px;'
                f'padding:10px 16px;border-bottom:1px solid {C_BORDER};'
                f'background:{"" if i % 2 == 0 else C_SURFACE + "88"}">'
                f'<div style="width:36px;text-align:center">{medal_html}</div>'
                f'<div style="width:100px;font-size:13px;font-weight:700;color:{carrier_color}">'
                f'{sname}</div>'
                f'<div style="flex:1">{bar}</div>'
                f'<div style="width:52px;font-size:14px;font-weight:800;color:{rel_color};'
                f'text-align:right">{rel:.1f}%</div>'
                f'<div style="width:68px;font-size:12px;color:{delta_color};text-align:right;'
                f'font-weight:600">{delta_str} QoQ</div>'
                f'<div style="width:70px">{_outlook_badge(p.outlook)}</div>'
                f'</div>'
            )

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;overflow:hidden">'
            f'{rows_html}</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.error(f"tab_carriers._render_reliability_rankings: {exc}")
        st.warning("Reliability rankings unavailable.")


# ── Section 5: Market concentration ───────────────────────────────────────────

def _render_market_concentration() -> None:
    try:
        _section_header("Market Concentration", "Herfindahl-Hirschman Index (HHI) analysis")

        if not _CARRIER_DATA_OK:
            st.info("Carrier data module unavailable.")
            return

        conc = get_market_concentration()
        hhi = conc.get("hhi", 0)
        hhi_cat = conc.get("hhi_category", "—")
        top3 = conc.get("top3_share_pct", 0)
        top5 = conc.get("top5_share_pct", 0)
        top10 = conc.get("top10_share_pct", 0)
        total = conc.get("total_tracked_share_pct", 0)

        hhi_color = C_LOW if hhi >= 2500 else (C_MOD if hhi >= 1500 else C_HIGH)
        hhi_desc = (
            "Market is highly concentrated — regulatory scrutiny likely"
            if hhi >= 2500
            else "Moderately concentrated market with oligopolistic dynamics"
            if hhi >= 1500
            else "Competitive market structure with distributed capacity"
        )

        # HHI gauge (CSS bar 0–10000)
        hhi_bar_width = int(280 * min(hhi, 10000) / 10000)

        ratios = [
            ("Top 3 carriers", top3, C_LOW),
            ("Top 5 carriers", top5, C_ORANGE),
            ("Top 10 carriers", top10, C_MOD),
            ("All 12 tracked", total, C_HIGH),
        ]
        ratio_rows = ""
        for label, share, color in ratios:
            bar_w = int(200 * share / 100)
            ratio_rows += (
                f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">'
                f'<div style="width:110px;font-size:12px;color:{C_TEXT2}">{label}</div>'
                f'<div style="width:200px;height:8px;background:{C_SURFACE};border-radius:4px">'
                f'<div style="width:{bar_w}px;height:8px;background:{color};border-radius:4px"></div></div>'
                f'<div style="font-size:14px;font-weight:700;color:{color}">{share:.1f}%</div>'
                f'</div>'
            )

        st.markdown(
            f'<div style="display:flex;gap:16px;flex-wrap:wrap">'
            f'<div style="flex:1;min-width:260px;background:{C_CARD};border:1px solid {hhi_color}33;'
            f'border-radius:10px;padding:20px;border-top:3px solid {hhi_color}">'
            f'<div style="font-size:11px;font-weight:700;color:{C_TEXT3};letter-spacing:0.8px;'
            f'text-transform:uppercase;margin-bottom:8px">HHI Score</div>'
            f'<div style="font-size:42px;font-weight:900;color:{hhi_color};line-height:1">'
            f'{hhi:,.0f}</div>'
            f'<div style="font-size:13px;font-weight:700;color:{hhi_color};margin:6px 0">'
            f'{hhi_cat}</div>'
            f'<div style="margin:12px 0 4px">'
            f'<div style="height:10px;width:280px;background:{C_SURFACE};border-radius:5px">'
            f'<div style="height:10px;width:{hhi_bar_width}px;background:{hhi_color};border-radius:5px"></div></div>'
            f'<div style="display:flex;justify-content:space-between;font-size:10px;color:{C_TEXT3};margin-top:3px">'
            f'<span>0</span><span>1,500</span><span>2,500</span><span>10,000</span></div>'
            f'</div>'
            f'<div style="font-size:12px;color:{C_TEXT3};margin-top:10px;line-height:1.5">'
            f'{hhi_desc}</div>'
            f'</div>'
            f'<div style="flex:1;min-width:260px;background:{C_CARD};border:1px solid {C_BORDER};'
            f'border-radius:10px;padding:20px">'
            f'<div style="font-size:11px;font-weight:700;color:{C_TEXT3};letter-spacing:0.8px;'
            f'text-transform:uppercase;margin-bottom:16px">Concentration Ratios</div>'
            f'{ratio_rows}</div></div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.error(f"tab_carriers._render_market_concentration: {exc}")
        st.warning("Market concentration data unavailable.")


# ── Section 6: Blank sailing tracker ──────────────────────────────────────────

def _render_blank_sailing_tracker(alerts: list[dict]) -> None:
    try:
        _section_header("Blank Sailing Tracker", "Recent capacity removal announcements")

        if not alerts:
            st.info("No blank sailing alerts available.")
            return

        def _impact_level(teu: int) -> str:
            if teu >= 20000:
                return "SEVERE"
            if teu >= 14000:
                return "SIGNIFICANT"
            if teu >= 10000:
                return "MODERATE"
            return "MINIMAL"

        headers = ["CARRIER", "TRADE LANE", "DEPARTURE WEEK", "TEUs REMOVED", "IMPACT"]
        header_html = "".join(
            f'<th style="padding:10px 12px;text-align:left;font-size:10px;'
            f'font-weight:700;color:{C_TEXT3};letter-spacing:0.8px;'
            f'text-transform:uppercase;border-bottom:1px solid {C_BORDER};'
            f'white-space:nowrap">{h}</th>'
            for h in headers
        )

        rows_html = ""
        for i, alert in enumerate(alerts):
            carrier = alert.get("carrier", "—")
            trade = alert.get("trade_lane", "—")
            week = alert.get("departure_week", "—")
            teu = alert.get("teu_impact", 0)
            impact = _impact_level(teu)
            carrier_color = _carrier_color(carrier)
            row_bg = C_CARD if i % 2 == 0 else C_SURFACE

            rows_html += (
                f'<tr style="background:{row_bg}">'
                f'<td style="padding:10px 12px;font-size:13px;font-weight:700;color:{carrier_color}">'
                f'{carrier}</td>'
                f'<td style="padding:10px 12px;font-size:12px;color:{C_TEXT2}">{trade}</td>'
                f'<td style="padding:10px 12px;font-size:12px;color:{C_TEXT3}">{week}</td>'
                f'<td style="padding:10px 12px;font-size:13px;font-weight:700;color:{C_TEXT}">'
                f'{teu:,} TEU</td>'
                f'<td style="padding:10px 12px">{_impact_badge(impact)}</td>'
                f'</tr>'
            )

        total_teu = sum(a.get("teu_impact", 0) for a in alerts)

        st.markdown(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-radius:10px;overflow-x:auto">'
            f'<div style="padding:12px 16px;border-bottom:1px solid {C_BORDER};'
            f'display:flex;justify-content:space-between;align-items:center">'
            f'<span style="font-size:12px;color:{C_TEXT2}">'
            f'{len(alerts)} alerts tracked</span>'
            f'<span style="font-size:12px;color:{C_LOW};font-weight:700">'
            f'{total_teu:,} TEU total removed</span></div>'
            f'<table style="width:100%;border-collapse:collapse">'
            f'<thead><tr>{header_html}</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.error(f"tab_carriers._render_blank_sailing_tracker: {exc}")
        st.warning("Blank sailing tracker unavailable.")


# ── Section 7: Carrier news feed ──────────────────────────────────────────────

def _render_carrier_news() -> None:
    try:
        _section_header("Carrier News Feed", "Live intelligence from carrier RSS feeds")

        try:
            from data.carrier_intelligence import fetch_carrier_updates, ALL_CARRIERS
            updates_map = fetch_carrier_updates(max_per_carrier=3, cache_ttl_hours=6.0)
        except Exception as feed_exc:
            logger.warning(f"tab_carriers: news feed unavailable: {feed_exc}")
            updates_map = {}

        if not updates_map or all(len(v) == 0 for v in updates_map.values()):
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:10px;padding:20px;text-align:center;color:{C_TEXT3}">'
                f'News feeds unavailable — feedparser library may not be installed '
                f'or RSS sources are unreachable.</div>',
                unsafe_allow_html=True,
            )
            return

        items_html = ""
        for carrier, updates in updates_map.items():
            for upd in updates:
                try:
                    carrier_color = _carrier_color(carrier)
                    sentiment = upd.sentiment if hasattr(upd, "sentiment") else 0.0
                    sent_color = C_HIGH if sentiment > 0.1 else (C_LOW if sentiment < -0.1 else C_TEXT3)
                    sent_label = "POSITIVE" if sentiment > 0.1 else ("NEGATIVE" if sentiment < -0.1 else "NEUTRAL")
                    ts = upd.published_dt.strftime("%b %d, %H:%M UTC") if hasattr(upd, "published_dt") else "—"
                    headline = (upd.headline or "")[:120]
                    url = getattr(upd, "url", "#") or "#"
                    category = (getattr(upd, "category", "general") or "general").upper()

                    items_html += (
                        f'<div style="padding:14px 16px;border-bottom:1px solid {C_BORDER}">'
                        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">'
                        f'<span style="font-size:11px;font-weight:700;color:{carrier_color};'
                        f'padding:2px 7px;background:{carrier_color}1a;border-radius:4px">'
                        f'{carrier}</span>'
                        f'<span style="font-size:10px;color:{C_TEXT3};background:{C_SURFACE};'
                        f'padding:2px 6px;border-radius:4px">{category}</span>'
                        f'<span style="font-size:10px;font-weight:700;color:{sent_color};'
                        f'margin-left:auto">{sent_label}</span>'
                        f'<span style="font-size:10px;color:{C_TEXT3}">{ts}</span>'
                        f'</div>'
                        f'<div style="font-size:13px;color:{C_TEXT};line-height:1.5">'
                        f'<a href="{url}" target="_blank" style="color:{C_TEXT};text-decoration:none">'
                        f'{headline}</a></div>'
                        f'</div>'
                    )
                except Exception as item_exc:
                    logger.debug(f"tab_carriers: news item render error: {item_exc}")

        if items_html:
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:10px;overflow:hidden">{items_html}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.info("No news items available.")

    except Exception as exc:
        logger.error(f"tab_carriers._render_carrier_news: {exc}")
        st.warning("News feed section unavailable.")


# ── Section 8: Carrier deep-dive expanders ────────────────────────────────────

def _render_deep_dives(profiles: list) -> None:
    try:
        _section_header("Carrier Deep-Dive", "Per-carrier risk, strengths & financial highlights")

        for p in sorted(profiles, key=lambda x: x.market_share_pct, reverse=True):
            sname = _short_name(p.name)
            carrier_color = _carrier_color(p.name)
            a_color = _alliance_color(p.alliance)
            rel_color = _reliability_color(p.schedule_reliability)

            with st.expander(
                f"{sname}  ·  {p.market_share_pct:.1f}% market share  ·  {p.alliance}",
                expanded=False,
            ):
                try:
                    # Financial highlights row
                    margin_color = C_HIGH if p.q_net_margin_pct >= 8 else (C_MOD if p.q_net_margin_pct >= 4 else C_LOW)
                    financials_html = (
                        f'<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px">'
                        f'<div style="flex:1;min-width:120px;background:{C_SURFACE};border-radius:8px;'
                        f'padding:12px;text-align:center">'
                        f'<div style="font-size:10px;color:{C_TEXT3};text-transform:uppercase;'
                        f'letter-spacing:0.7px">Q Revenue</div>'
                        f'<div style="font-size:20px;font-weight:800;color:{C_TEXT}">'
                        f'${p.q_revenue_bn:.1f}B</div></div>'
                        f'<div style="flex:1;min-width:120px;background:{C_SURFACE};border-radius:8px;'
                        f'padding:12px;text-align:center">'
                        f'<div style="font-size:10px;color:{C_TEXT3};text-transform:uppercase;'
                        f'letter-spacing:0.7px">Net Margin</div>'
                        f'<div style="font-size:20px;font-weight:800;color:{margin_color}">'
                        f'{p.q_net_margin_pct:.1f}%</div></div>'
                        f'<div style="flex:1;min-width:120px;background:{C_SURFACE};border-radius:8px;'
                        f'padding:12px;text-align:center">'
                        f'<div style="font-size:10px;color:{C_TEXT3};text-transform:uppercase;'
                        f'letter-spacing:0.7px">Schedule Rel.</div>'
                        f'<div style="font-size:20px;font-weight:800;color:{rel_color}">'
                        f'{p.schedule_reliability:.1f}%</div></div>'
                        f'<div style="flex:1;min-width:120px;background:{C_SURFACE};border-radius:8px;'
                        f'padding:12px;text-align:center">'
                        f'<div style="font-size:10px;color:{C_TEXT3};text-transform:uppercase;'
                        f'letter-spacing:0.7px">Blank Sailing</div>'
                        f'<div style="font-size:20px;font-weight:800;color:{C_TEXT2}">'
                        f'{p.blank_sailing_rate:.1f}%</div></div>'
                        f'<div style="flex:1;min-width:120px;background:{C_SURFACE};border-radius:8px;'
                        f'padding:12px;text-align:center">'
                        f'<div style="font-size:10px;color:{C_TEXT3};text-transform:uppercase;'
                        f'letter-spacing:0.7px">Outlook</div>'
                        f'<div style="margin-top:4px">{_outlook_badge(p.outlook)}</div></div>'
                        f'</div>'
                    )

                    # Risks
                    risks = p.key_risks if hasattr(p, "key_risks") else []
                    risk_items = "".join(
                        f'<li style="margin-bottom:5px;color:{C_TEXT2}">{r}</li>'
                        for r in risks
                    )

                    # Strengths
                    strengths = p.key_strengths if hasattr(p, "key_strengths") else []
                    strength_items = "".join(
                        f'<li style="margin-bottom:5px;color:{C_TEXT2}">{s}</li>'
                        for s in strengths
                    )

                    risks_strengths_html = (
                        f'<div style="display:flex;gap:14px;flex-wrap:wrap">'
                        f'<div style="flex:1;min-width:220px;background:{C_LOW}0d;'
                        f'border:1px solid {C_LOW}33;border-radius:8px;padding:14px">'
                        f'<div style="font-size:11px;font-weight:700;color:{C_LOW};'
                        f'text-transform:uppercase;letter-spacing:0.7px;margin-bottom:10px">'
                        f'Key Risks</div>'
                        f'<ul style="margin:0;padding-left:18px">{risk_items}</ul></div>'
                        f'<div style="flex:1;min-width:220px;background:{C_HIGH}0d;'
                        f'border:1px solid {C_HIGH}33;border-radius:8px;padding:14px">'
                        f'<div style="font-size:11px;font-weight:700;color:{C_HIGH};'
                        f'text-transform:uppercase;letter-spacing:0.7px;margin-bottom:10px">'
                        f'Key Strengths</div>'
                        f'<ul style="margin:0;padding-left:18px">{strength_items}</ul></div>'
                        f'</div>'
                    )

                    # Ticker badge
                    ticker = p.ticker if hasattr(p, "ticker") and p.ticker != "private" else None
                    ticker_html = (
                        f'<span style="font-size:11px;background:{C_ACCENT}22;color:{C_ACCENT};'
                        f'padding:2px 8px;border-radius:4px;border:1px solid {C_ACCENT}44;'
                        f'margin-left:8px">{p.ticker}</span>'
                        if ticker
                        else f'<span style="font-size:11px;color:{C_TEXT3};margin-left:8px">Private</span>'
                    )

                    st.markdown(
                        f'<div style="margin-bottom:6px">'
                        f'<span style="font-size:16px;font-weight:800;color:{carrier_color}">'
                        f'{p.name}</span>'
                        f'{ticker_html}'
                        f'<span style="font-size:11px;color:{a_color};margin-left:10px;font-weight:600">'
                        f'{p.alliance}</span>'
                        f'</div>'
                        f'<div style="font-size:12px;color:{C_TEXT3};margin-bottom:14px">'
                        f'Fleet: {p.fleet_size} vessels &nbsp;|&nbsp; '
                        f'Capacity: {_teu_str(p.teu_capacity)} TEU &nbsp;|&nbsp; '
                        f'YTD Rate: {_rate_cell(p.ytd_rate_change)}'
                        f'</div>'
                        f'{financials_html}'
                        f'{risks_strengths_html}',
                        unsafe_allow_html=True,
                    )
                except Exception as inner_exc:
                    logger.error(f"tab_carriers._render_deep_dives [{sname}]: {inner_exc}")
                    st.warning(f"Deep-dive unavailable for {sname}.")

    except Exception as exc:
        logger.error(f"tab_carriers._render_deep_dives: {exc}")
        st.warning("Deep-dive section unavailable.")


# ── Main render ────────────────────────────────────────────────────────────────

def render(
    port_results=None,
    route_results=None,
    insights=None,
) -> None:
    """Render the Carrier Intelligence tab.

    Args:
        port_results:  Optional port analysis results (unused directly).
        route_results: Optional route analysis results (unused directly).
        insights:      Optional pre-computed insights dict (unused directly).
    """
    try:
        st.markdown(
            f'<style>'
            f'[data-testid="stExpander"] summary {{color:{C_TEXT} !important}}'
            f'</style>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass

    if not _CARRIER_DATA_OK:
        st.error(
            "Carrier intelligence data module failed to load. "
            "Check that `data/carrier_intelligence.py` is present and imports correctly."
        )
        return

    # Load all data once up front
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

    # ── Render all sections ────────────────────────────────────────────────────
    _render_header(profiles)
    _render_alliance_panel(profiles)
    _render_performance_table(profiles)

    col_a, col_b = st.columns([3, 2])
    with col_a:
        _render_reliability_rankings(profiles)
    with col_b:
        _render_market_concentration()

    _render_blank_sailing_tracker(alerts)
    _render_carrier_news()
    _render_deep_dives(profiles)
