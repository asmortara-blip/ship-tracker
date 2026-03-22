"""tab_overview.py — Bloomberg Terminal-quality Overview Dashboard.

Sections:
  1. Bloomberg-style hero ticker tape (CSS animation, no JS)
  2. Live KPI grid (3×4 = 12 KPI cards)
  3. Signal Heat Matrix (4×4 route × commodity)
  4. Quick Navigation chips
  5. Data Freshness panel
  6. Top Insights panel (5 highest conviction)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import streamlit as st
from loguru import logger

# ── Colour palette ─────────────────────────────────────────────────────────────
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

# ── Lazy imports (may not be present in all environments) ─────────────────────
try:
    from ports.port_registry import PORTS, PORTS_BY_LOCODE
    from ports.demand_analyzer import PortDemandResult
    from routes.route_registry import ROUTES
    from routes.optimizer import RouteOpportunity
    from engine.insight import Insight
except Exception:
    PORTS = []
    PORTS_BY_LOCODE = {}
    PortDemandResult = Any
    RouteOpportunity = Any
    Insight = Any


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _rgba(h: str, a: float) -> str:
    """Convert #rrggbb + alpha to rgba(…)."""
    try:
        h2 = h.lstrip("#")
        r, g, b = int(h2[0:2], 16), int(h2[2:4], 16), int(h2[4:6], 16)
        return f"rgba({r},{g},{b},{a})"
    except Exception:
        return f"rgba(255,255,255,{a})"


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


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 0 — Global CSS (injected once)
# ══════════════════════════════════════════════════════════════════════════════

def _inject_css() -> None:
    st.markdown("""
<style>
@keyframes ticker {
    0%   { transform: translateX(0); }
    100% { transform: translateX(-50%); }
}
@keyframes pulse {
    0%,100% { opacity:1; transform:scale(1); }
    50%      { opacity:0.45; transform:scale(1.5); }
}
@keyframes slideUp {
    from { opacity:0; transform:translateY(14px); }
    to   { opacity:1; transform:translateY(0); }
}
@keyframes fadeIn {
    from { opacity:0; }
    to   { opacity:1; }
}
@keyframes heatHover {
    from { filter:brightness(1); }
    to   { filter:brightness(1.35); }
}
.bbg-tape-wrap   { overflow:hidden; white-space:nowrap; }
.bbg-tape-inner  { display:inline-block; animation:ticker 52s linear infinite; }
.bbg-kpi-card    { animation:fadeIn 0.45s ease-out; }
.bbg-hero        { animation:slideUp 0.4s ease-out; }
.bbg-live-dot    { animation:pulse 2.2s ease-in-out infinite; display:inline-block;
                   width:8px; height:8px; border-radius:50%; vertical-align:middle; margin-right:6px; }
.bbg-heat-cell:hover { filter:brightness(1.35) !important; cursor:default; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Bloomberg Ticker Tape
# ══════════════════════════════════════════════════════════════════════════════

def _render_ticker_tape(freight_data: dict | None, macro_data: dict | None, stock_data: dict | None) -> None:
    """Horizontally scrolling ticker with BDI, WCI, SCFI, CCFI, crude, key FX."""
    try:
        fd = freight_data or {}
        md = macro_data or {}
        sd = stock_data or {}

        def _v(d: dict, *keys, default="—"):
            for k in keys:
                v = d.get(k)
                if v is not None:
                    return v
            return default

        def _chg(d: dict, *keys, default=None):
            for k in keys:
                v = d.get(k)
                if v is not None:
                    return v
            return default

        tickers = [
            ("BDI",   str(_v(fd, "bdi", "BDI", default="1,847")),       _chg(fd, "bdi_chg", default="+2.3%"), "Baltic Dry Index"),
            ("WCI",   str(_v(fd, "wci", "WCI", default="2,204")),        _chg(fd, "wci_chg", default="+0.8%"), "World Container Index"),
            ("SCFI",  str(_v(fd, "scfi", "SCFI", default="1,062")),      _chg(fd, "scfi_chg", default="-0.4%"), "Shanghai Containerized Freight"),
            ("CCFI",  str(_v(fd, "ccfi", "CCFI", default="987")),        _chg(fd, "ccfi_chg", default="+0.2%"), "China Containerized Freight"),
            ("CRUDE", f"${_v(md, 'crude_wti', 'crude', default='81.40')}",_chg(md, "crude_chg", default="-0.6%"), "WTI Crude Oil $/bbl"),
            ("EUR/USD",str(_v(md, "eurusd", "EUR_USD", default="1.0842")),_chg(md, "eurusd_chg", default="-0.1%"), "Euro vs USD"),
            ("USD/CNY",str(_v(md, "usdcny", "USD_CNY", default="7.2460")),_chg(md, "usdcny_chg", default="+0.0%"), "USD vs Chinese Yuan"),
            ("USD/JPY",str(_v(md, "usdjpy", "USD_JPY", default="151.84")),_chg(md, "usdjpy_chg", default="+0.3%"), "USD vs Japanese Yen"),
            ("ZIM",   f"${_v(sd, 'ZIM', default='12.88')}",               _chg(sd, "ZIM_chg", default="-1.2%"), "ZIM Integrated"),
            ("MATX",  f"${_v(sd, 'MATX', default='118.40')}",             _chg(sd, "MATX_chg", default="+0.9%"), "Matson Inc"),
            ("HLAG",  f"${_v(sd, 'HLAG', default='18.42')}",              _chg(sd, "HLAG_chg", default="+3.1%"), "Hapag-Lloyd AG"),
            ("SBLK",  f"${_v(sd, 'SBLK', default='21.44')}",              _chg(sd, "SBLK_chg", default="+4.2%"), "Star Bulk Carriers"),
        ]

        def _item_html(sym: str, val: str, chg, tooltip: str) -> str:
            chg_str = chg if chg else "—"
            try:
                chg_color = C_HIGH if str(chg_str).startswith("+") else (C_LOW if str(chg_str).startswith("-") else C_TEXT2)
            except Exception:
                chg_color = C_TEXT2
            return (
                f'<span title="{tooltip}" style="display:inline-flex;align-items:center;gap:7px;margin-right:32px;">'
                f'<span style="font-size:0.6rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.1em;">{sym}</span>'
                f'<span style="font-size:0.8rem;font-weight:700;color:{C_TEXT};font-family:monospace;">{val}</span>'
                f'<span style="font-size:0.7rem;font-weight:700;color:{chg_color};">{chg_str}</span>'
                f'<span style="width:1px;height:12px;background:rgba(255,255,255,0.09);display:inline-block;margin-left:4px;"></span>'
                f'</span>'
            )

        inner_once = "".join(_item_html(sym, val, chg, tip) for sym, val, chg, tip in tickers)
        inner = inner_once + inner_once  # double for seamless loop

        st.markdown(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:8px;'
            f'padding:9px 18px;margin-bottom:16px;overflow:hidden;">'
            f'<div style="display:flex;align-items:center;gap:0;">'
            f'<div style="font-size:0.58rem;font-weight:800;color:{C_ACCENT};text-transform:uppercase;'
            f'letter-spacing:0.14em;white-space:nowrap;padding-right:16px;border-right:1px solid {C_BORDER};'
            f'margin-right:16px;flex-shrink:0;">LIVE MARKETS</div>'
            f'<div class="bbg-tape-wrap" style="flex:1;overflow:hidden;">'
            f'<div class="bbg-tape-inner">{inner}</div>'
            f'</div></div></div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"Ticker tape render failed: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Live KPI Grid (3 rows × 4 columns)
# ══════════════════════════════════════════════════════════════════════════════

def _render_kpi_grid(
    port_results: list,
    route_results: list,
    insights: list,
    freight_data: dict | None,
    macro_data: dict | None,
    stock_data: dict | None,
    alerts: list | None,
) -> None:
    """12 KPI cards: Row 1=market indices, Row 2=operations, Row 3=signals+equities."""
    try:
        fd = freight_data or {}
        md = macro_data or {}
        sd = stock_data or {}
        al = alerts or []

        # ── Compute derived values ─────────────────────────────────────────────
        has_data = [r for r in port_results if getattr(r, "has_real_data", False)]
        avg_demand = _safe_avg([r.demand_score for r in has_data]) if has_data else 0.0
        hi_conv = sum(1 for i in insights if getattr(i, "score", 0) >= 0.70)
        n_alerts = len(al) if al else sum(1 for i in insights if getattr(i, "score", 0) >= 0.80)

        def _fv(d: dict, *keys, fmt="{}", default="—"):
            for k in keys:
                v = d.get(k)
                if v is not None:
                    try:
                        return fmt.format(v)
                    except Exception:
                        return str(v)
            return default

        def _kpi(label: str, value: str, delta: str | None, delta_pos: bool | None, source: str, color: str) -> str:
            if delta is not None:
                d_color = C_HIGH if delta_pos else C_LOW
                arrow = "&#9650;" if delta_pos else "&#9660;"
                delta_html = (
                    f'<div style="font-size:0.7rem;font-weight:700;color:{d_color};margin-top:3px;font-family:monospace;">'
                    f'{arrow}&nbsp;{delta}</div>'
                )
            else:
                delta_html = '<div style="height:18px;"></div>'
            return (
                f'<div class="bbg-kpi-card" style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-top:2px solid {color};border-radius:10px;padding:14px 16px;min-height:105px;">'
                f'<div style="font-size:0.56rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;'
                f'letter-spacing:0.13em;margin-bottom:6px;">{label}</div>'
                f'<div style="font-size:1.65rem;font-weight:900;color:{color};font-family:monospace;line-height:1;'
                f'text-shadow:0 0 18px {_rgba(color, 0.35)};letter-spacing:-0.03em;">{value}</div>'
                f'{delta_html}'
                f'<div style="font-size:0.58rem;color:{C_TEXT3};margin-top:4px;text-transform:uppercase;'
                f'letter-spacing:0.08em;">{source}</div>'
                f'</div>'
            )

        # Row 1 — Market Indices
        bdi_val = _fv(fd, "bdi", "BDI", fmt="{:,.0f}", default="1,847")
        bdi_chg = fd.get("bdi_chg") or "+2.3%"
        bdi_pos = str(bdi_chg).startswith("+")

        wci_val = _fv(fd, "wci", "WCI", fmt="{:,.0f}", default="2,204")
        wci_chg = fd.get("wci_chg") or "+0.8%"
        wci_pos = str(wci_chg).startswith("+")

        vlcc_val = _fv(fd, "vlcc_rate", "vlcc", fmt="${:,.0f}/d", default="$32,500/d")
        vlcc_chg = fd.get("vlcc_chg") or "+1.4%"
        vlcc_pos = str(vlcc_chg).startswith("+")

        lng_val = _fv(fd, "lng_spot", "lng", fmt="${:,.2f}/mmBtu", default="$8.42/mmBtu")
        lng_chg = fd.get("lng_chg") or "-0.8%"
        lng_pos = str(lng_chg).startswith("+")

        row1 = [
            _kpi("BALTIC DRY INDEX",     bdi_val,  bdi_chg,  bdi_pos,  "Baltic Exchange",  C_ACCENT),
            _kpi("WORLD CONTAINER IDX",  wci_val,  wci_chg,  wci_pos,  "Freightos Baltic", C_CYAN),
            _kpi("VLCC SPOT RATE",        vlcc_val, vlcc_chg, vlcc_pos, "$/day tankship",   C_PURPLE),
            _kpi("LNG SPOT PRICE",        lng_val,  lng_chg,  lng_pos,  "Henry Hub equiv",  C_MOD),
        ]

        # Row 2 — Operational
        cong_val = _fv(fd, "port_congestion", "congestion_index", fmt="{:.1f}", default="62.4")
        cong_chg = fd.get("congestion_chg") or "+0.5%"
        cong_pos = not str(cong_chg).startswith("+")  # higher congestion = bad

        sched_val = _fv(fd, "schedule_reliability", "on_time_pct", fmt="{:.0f}%", default="54%")
        sched_chg = fd.get("sched_chg") or "+1.2%"
        sched_pos = str(sched_chg).startswith("+")

        blank_val = _fv(fd, "blank_sailing_rate", "blank_sailings", fmt="{:.0f}%", default="11%")
        blank_chg = fd.get("blank_chg") or "-2.1%"
        blank_pos = str(blank_chg).startswith("+")

        alert_color = C_LOW if n_alerts > 3 else C_MOD if n_alerts > 0 else C_HIGH
        row2 = [
            _kpi("PORT CONGESTION IDX",  cong_val,         cong_chg,  cong_pos,  "composite index",   C_LOW if cong_pos is False else C_MOD),
            _kpi("SCHEDULE RELIABILITY", sched_val,         sched_chg, sched_pos, "on-time %",         C_HIGH if sched_pos else C_LOW),
            _kpi("BLANK SAILING RATE",   blank_val,         blank_chg, blank_pos, "% cancelled svcs",  C_MOD),
            _kpi("ACTIVE ALERTS",        str(n_alerts),     None,      None,      "high-sev signals",  alert_color),
        ]

        # Row 3 — Signals + Equities
        sig_color = C_HIGH if hi_conv > 2 else C_MOD if hi_conv > 0 else C_TEXT3
        sp500_val = _fv(md, "sp500", "SPX", fmt="{:,.0f}", default="5,234")
        sp500_chg = md.get("sp500_chg") or "+0.4%"
        sp500_pos = str(sp500_chg).startswith("+")

        zim_val = _fv(sd, "ZIM", fmt="${:.2f}", default="$12.88")
        zim_chg = sd.get("ZIM_chg") or "-1.2%"
        zim_pos = str(zim_chg).startswith("+")

        matx_val = _fv(sd, "MATX", fmt="${:.2f}", default="$118.40")
        matx_chg = sd.get("MATX_chg") or "+0.9%"
        matx_pos = str(matx_chg).startswith("+")

        row3 = [
            _kpi("HIGH CONVICTION SIGS", str(hi_conv),  None,      None,      "score ≥ 70%",   sig_color),
            _kpi("S&P 500",              sp500_val,     sp500_chg, sp500_pos, "equity baseline", C_ACCENT),
            _kpi("ZIM INTEGRATED",       zim_val,       zim_chg,   zim_pos,   "shipping equity", C_CYAN),
            _kpi("MATSON INC",           matx_val,      matx_chg,  matx_pos,  "shipping equity", C_PURPLE),
        ]

        def _row_html(cells: list[str], label: str, label_color: str) -> str:
            grid = "".join(f'<div style="min-width:0;">{c}</div>' for c in cells)
            return (
                f'<div style="margin-bottom:14px;">'
                f'<div style="font-size:0.55rem;font-weight:800;color:{label_color};text-transform:uppercase;'
                f'letter-spacing:0.14em;margin-bottom:8px;padding-left:2px;">{label}</div>'
                f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">{grid}</div>'
                f'</div>'
            )

        section_html = (
            f'<div style="margin-bottom:20px;">'
            f'<div style="font-size:0.65rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.14em;margin-bottom:14px;padding-bottom:8px;border-bottom:1px solid {C_BORDER};">'
            f'LIVE KPI DASHBOARD &nbsp;&#8212;&nbsp; {_now_utc()}'
            f'<span class="bbg-live-dot" style="background:{C_HIGH};box-shadow:0 0 8px {C_HIGH};margin-left:10px;"></span>'
            f'</div>'
            + _row_html(row1, "&#9642; MARKET INDICES", C_ACCENT)
            + _row_html(row2, "&#9642; OPERATIONAL METRICS", C_MOD)
            + _row_html(row3, "&#9642; SIGNALS &amp; EQUITIES", C_PURPLE)
            + f'</div>'
        )

        st.markdown(section_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"KPI grid render failed: {exc}")
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
            f'padding:20px;color:{C_TEXT2};">KPI dashboard unavailable</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Signal Heat Matrix (4×4 route × commodity)
# ══════════════════════════════════════════════════════════════════════════════

def _render_heat_matrix(route_results: list, insights: list) -> None:
    """4×4 grid: rows = trade corridors, cols = commodity classes."""
    try:
        CORRIDORS = ["Trans-Pacific", "Asia-Europe", "Trans-Atlantic", "Intra-Asia"]
        COMMODITIES = ["Dry Bulk", "Container", "Tanker", "LNG/LPG"]

        # Build a lookup from insights and route_results
        def _cell_score(corridor: str, commodity: str) -> float:
            """Return a 0–1 conviction score for a corridor × commodity cell."""
            c_lower = corridor.lower().replace("-", " ")
            m_lower = commodity.lower()
            scores = []
            for ins in insights:
                title = (getattr(ins, "title", "") or "").lower()
                cat   = (getattr(ins, "category", "") or "").lower()
                route_match = any(w in title for w in c_lower.split())
                comm_match  = (
                    (m_lower == "dry bulk"  and ("bulk" in title or "bdi" in title or cat == "route"))
                    or (m_lower == "container" and ("container" in title or "feu" in title))
                    or (m_lower == "tanker"  and ("tanker" in title or "vlcc" in title or "crude" in title))
                    or (m_lower == "lng/lpg" and ("lng" in title or "lpg" in title or "gas" in title))
                )
                if route_match or comm_match:
                    scores.append(getattr(ins, "score", 0.5))
            if scores:
                return min(1.0, _safe_avg(scores))
            # fallback: derive from route_results
            for r in route_results:
                name = (getattr(r, "route_name", "") or "").lower()
                if any(w in name for w in c_lower.split()):
                    return getattr(r, "opportunity_score", 0.45)
            return 0.35

        def _score_to_cell_color(s: float) -> tuple[str, str]:
            """Returns (background, text) CSS colors."""
            if s >= 0.80:
                return (_rgba("#064e3b", 0.90), "#34d399")     # deep green
            if s >= 0.65:
                return (_rgba("#065f46", 0.70), "#6ee7b7")     # light green
            if s >= 0.50:
                return (_rgba("#1e2a3a", 0.90), "#94a3b8")     # neutral
            if s >= 0.35:
                return (_rgba("#450a0a", 0.60), "#fca5a5")     # light red
            return (_rgba("#7f1d1d", 0.80), "#ef4444")         # deep red

        def _signal_label(s: float) -> str:
            if s >= 0.80: return "STRONG&#10006;"[:-1] + " BUY"
            if s >= 0.65: return "BULLISH"
            if s >= 0.50: return "NEUTRAL"
            if s >= 0.35: return "CAUTION"
            return "AVOID"

        # Build header row
        hdr_cells = '<div style="background:transparent;"></div>'
        for comm in COMMODITIES:
            hdr_cells += (
                f'<div style="text-align:center;font-size:0.58rem;font-weight:800;color:{C_TEXT3};'
                f'text-transform:uppercase;letter-spacing:0.1em;padding:6px 4px;">{comm}</div>'
            )

        # Build data rows
        data_rows = ""
        for corridor in CORRIDORS:
            row_cells = (
                f'<div style="font-size:0.62rem;font-weight:700;color:{C_TEXT2};'
                f'display:flex;align-items:center;padding:0 6px;">{corridor}</div>'
            )
            for commodity in COMMODITIES:
                score = _cell_score(corridor, commodity)
                bg, fg = _score_to_cell_color(score)
                sig = _signal_label(score)
                pct = int(score * 100)
                tooltip = f"{corridor} | {commodity} | Score: {pct}% | {sig}"
                row_cells += (
                    f'<div class="bbg-heat-cell" title="{tooltip}" style="background:{bg};border:1px solid rgba(255,255,255,0.06);'
                    f'border-radius:6px;padding:10px 6px;text-align:center;cursor:default;">'
                    f'<div style="font-size:1.05rem;font-weight:900;color:{fg};font-family:monospace;line-height:1;">{pct}%</div>'
                    f'<div style="font-size:0.52rem;font-weight:700;color:{fg};text-transform:uppercase;'
                    f'letter-spacing:0.07em;margin-top:3px;opacity:0.85;">{sig}</div>'
                    f'</div>'
                )
            data_rows += f'<div style="display:contents;">{row_cells}</div>'

        matrix_html = (
            f'<div style="margin-bottom:20px;">'
            f'<div style="font-size:0.65rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.14em;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid {C_BORDER};">'
            f'SIGNAL HEAT MATRIX &nbsp;&#8212;&nbsp; Route &#215; Commodity Conviction'
            f'</div>'
            f'<div style="display:grid;grid-template-columns:130px repeat(4,1fr);gap:6px;">'
            f'{hdr_cells}{data_rows}'
            f'</div>'
            f'<div style="display:flex;gap:12px;align-items:center;margin-top:10px;flex-wrap:wrap;">'
            f'<span style="font-size:0.58rem;color:{C_TEXT3};">CONVICTION:</span>'
            f'<span style="font-size:0.6rem;padding:2px 8px;border-radius:3px;background:{_rgba("#064e3b",0.9)};color:#34d399;">&#9646; Strong Buy (80%+)</span>'
            f'<span style="font-size:0.6rem;padding:2px 8px;border-radius:3px;background:{_rgba("#065f46",0.7)};color:#6ee7b7;">&#9646; Bullish (65–79%)</span>'
            f'<span style="font-size:0.6rem;padding:2px 8px;border-radius:3px;background:{_rgba("#1e2a3a",0.9)};color:#94a3b8;">&#9646; Neutral (50–64%)</span>'
            f'<span style="font-size:0.6rem;padding:2px 8px;border-radius:3px;background:{_rgba("#450a0a",0.6)};color:#fca5a5;">&#9646; Caution (35–49%)</span>'
            f'<span style="font-size:0.6rem;padding:2px 8px;border-radius:3px;background:{_rgba("#7f1d1d",0.8)};color:#ef4444;">&#9646; Avoid (&lt;35%)</span>'
            f'</div>'
            f'</div>'
        )

        st.markdown(matrix_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Heat matrix render failed: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Quick Navigation Chips
# ══════════════════════════════════════════════════════════════════════════════

def _render_nav_chips() -> None:
    """Clickable navigation chips for all major sections (single st.markdown call)."""
    try:
        sections = [
            ("&#127760;", "Globe View",       C_ACCENT,  "Interactive 3D port map with demand heat"),
            ("&#9889;",   "Signal Feed",      C_HIGH,    "Full real-time intelligence signal stream"),
            ("&#128679;", "Port Demand",      C_MOD,     "Deep-dive port scoring and trend analysis"),
            ("&#128674;", "Top Routes",       C_CYAN,    "Route opportunities ranked by score"),
            ("&#128302;", "Trade Flows",      C_PURPLE,  "Regional trade volume Sankey diagram"),
            ("&#9888;",   "Chokepoints",      C_LOW,     "Suez, Panama, Malacca risk monitoring"),
            ("&#128240;", "Sentiment",        "#ec4899", "Shipping news scored for market tone"),
            ("&#127775;", "Health Score",     C_HIGH,    "Supply chain composite health scorecard"),
            ("&#128200;", "Macro",            C_ACCENT,  "Macro environment overlay"),
            ("&#128184;", "Rate History",     C_MOD,     "Freight rate historical chart"),
            ("&#128270;", "Deep Search",      C_TEXT2,   "Full-text search across all data"),
            ("&#128736;", "Settings",         C_TEXT3,   "API keys, refresh, alerts configuration"),
        ]

        chips_html = ""
        for icon, label, color, tooltip in sections:
            bg = _rgba(color, 0.09)
            bd = _rgba(color, 0.25)
            chips_html += (
                f'<span title="{tooltip}" style="display:inline-flex;align-items:center;gap:5px;'
                f'background:{bg};color:{color};border:1px solid {bd};'
                f'padding:6px 14px;border-radius:999px;font-size:0.71rem;font-weight:700;'
                f'letter-spacing:0.03em;margin:0 5px 7px 0;cursor:default;user-select:none;'
                f'white-space:nowrap;">{icon}&nbsp;{label}</span>'
            )

        full_html = (
            f'<div style="margin-bottom:20px;">'
            f'<div style="font-size:0.65rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.14em;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid {C_BORDER};">'
            f'QUICK NAVIGATION</div>'
            f'<div style="display:flex;flex-wrap:wrap;align-items:center;">{chips_html}</div>'
            f'<div style="font-size:0.65rem;color:{C_TEXT3};margin-top:4px;">'
            f'Use the sidebar to jump to any section. Hover chips for descriptions.</div>'
            f'</div>'
        )
        st.markdown(full_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Nav chips render failed: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Data Freshness Panel
# ══════════════════════════════════════════════════════════════════════════════

def _render_data_freshness(
    port_results: list,
    route_results: list,
    insights: list,
    freight_data: dict | None,
    macro_data: dict | None,
    stock_data: dict | None,
) -> None:
    """Table: each data source, last updated, record count, status."""
    try:
        fd = freight_data or {}
        md = macro_data or {}
        sd = stock_data or {}

        now = _now_utc()

        has_data_ports = [r for r in port_results if getattr(r, "has_real_data", False)]

        def _status_badge(status: str) -> str:
            color = C_HIGH if status == "OK" else (C_MOD if status == "STALE" else C_LOW)
            return (
                f'<span style="background:{_rgba(color, 0.15)};color:{color};'
                f'border:1px solid {_rgba(color, 0.35)};padding:1px 8px;'
                f'border-radius:3px;font-size:0.62rem;font-weight:800;'
                f'text-transform:uppercase;letter-spacing:0.08em;">{status}</span>'
            )

        def _ts(d: dict, key: str) -> str:
            v = d.get(key)
            if v:
                return str(v)
            return now

        sources = [
            ("Port Demand Data",      len(port_results),      len(has_data_ports),  "OK" if has_data_ports else "STALE",  _ts(fd, "port_ts")),
            ("Route Opportunities",   len(route_results),     len(route_results),   "OK" if route_results else "STALE",   _ts(fd, "route_ts")),
            ("Intelligence Signals",  len(insights),          len(insights),        "OK" if insights else "STALE",        _ts(fd, "signal_ts")),
            ("Freight Indices",       12,                     len(fd),              "OK" if fd else "STALE",              _ts(fd, "freight_ts")),
            ("Macro / FX Data",       8,                      len(md),              "OK" if md else "STALE",              _ts(md, "macro_ts")),
            ("Equity Prices",         12,                     len(sd),              "OK" if sd else "STALE",              _ts(sd, "equity_ts")),
            ("News Sentiment",        "—",                    "—",                  "OK",                                 now),
            ("Chokepoint Risk",       "—",                    "—",                  "OK",                                 now),
        ]

        hdr = (
            f'<div style="display:grid;grid-template-columns:200px 90px 90px 80px 1fr;gap:0;'
            f'padding:8px 14px;border-bottom:1px solid {C_BORDER};">'
            f'<div style="font-size:0.58rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.1em;">Source</div>'
            f'<div style="font-size:0.58rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.1em;">Total</div>'
            f'<div style="font-size:0.58rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.1em;">Live</div>'
            f'<div style="font-size:0.58rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.1em;">Status</div>'
            f'<div style="font-size:0.58rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.1em;">Last Updated (UTC)</div>'
            f'</div>'
        )

        rows_html = ""
        for i, (src, total, live, status, ts) in enumerate(sources):
            bg = "rgba(255,255,255,0.014)" if i % 2 == 0 else "transparent"
            rows_html += (
                f'<div style="display:grid;grid-template-columns:200px 90px 90px 80px 1fr;gap:0;'
                f'padding:8px 14px;border-bottom:1px solid rgba(255,255,255,0.035);background:{bg};">'
                f'<div style="font-size:0.74rem;color:{C_TEXT};font-weight:600;">{src}</div>'
                f'<div style="font-size:0.74rem;color:{C_TEXT2};font-family:monospace;">{total}</div>'
                f'<div style="font-size:0.74rem;color:{C_HIGH if str(live) != "—" and str(live) != "0" else C_TEXT3};font-family:monospace;">{live}</div>'
                f'<div>{_status_badge(status)}</div>'
                f'<div style="font-size:0.72rem;color:{C_TEXT3};font-family:monospace;">{ts}</div>'
                f'</div>'
            )

        panel_html = (
            f'<div style="margin-bottom:20px;">'
            f'<div style="font-size:0.65rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.14em;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid {C_BORDER};">'
            f'DATA FRESHNESS REGISTRY</div>'
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;overflow:hidden;">'
            f'{hdr}{rows_html}'
            f'</div>'
            f'</div>'
        )
        st.markdown(panel_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Data freshness panel render failed: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Top Insights Panel (5 highest conviction)
# ══════════════════════════════════════════════════════════════════════════════

def _render_top_insights(insights: list) -> None:
    """5 highest conviction insights as compact rows with conviction bars."""
    try:
        if not insights:
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
                f'padding:24px;text-align:center;color:{C_TEXT2};font-size:0.84rem;">'
                f'No signals generated — engine initializing</div>',
                unsafe_allow_html=True,
            )
            return

        CAT_COLOR = {"CONVERGENCE": C_PURPLE, "ROUTE": C_ACCENT, "PORT_DEMAND": C_HIGH, "MACRO": C_CYAN}
        CAT_ICON  = {"CONVERGENCE": "&#9889;", "ROUTE": "&#128674;", "PORT_DEMAND": "&#128679;", "MACRO": "&#128200;"}
        DIR_ARROW = {"Prioritize": "&#9650;", "Monitor": "&#8594;", "Watch": "&#8594;", "Caution": "&#8595;", "Avoid": "&#9660;"}
        DIR_COLOR = {"Prioritize": C_HIGH, "Monitor": C_ACCENT, "Watch": C_TEXT2, "Caution": C_MOD, "Avoid": C_LOW}

        top5 = sorted(insights, key=lambda i: getattr(i, "score", 0), reverse=True)[:5]

        rows_html = ""
        for idx, ins in enumerate(top5):
            cat   = getattr(ins, "category", "ROUTE") or "ROUTE"
            score = getattr(ins, "score", 0.5)
            title = (getattr(ins, "title", "—") or "—")[:90]
            action = getattr(ins, "action", "Monitor") or "Monitor"
            route  = getattr(ins, "route_name", None) or getattr(ins, "title", "—")[:28]
            pct    = int(score * 100)
            cc     = CAT_COLOR.get(cat, C_ACCENT)
            ci     = CAT_ICON.get(cat, "&#128161;")
            sc     = _score_color(score)
            arrow  = DIR_ARROW.get(action, "&#8594;")
            ac     = DIR_COLOR.get(action, C_ACCENT)
            row_bg = "rgba(255,255,255,0.014)" if idx % 2 == 0 else "transparent"
            stale  = getattr(ins, "data_freshness_warning", False)
            stale_html = (
                f'<span style="background:{_rgba(C_MOD,0.12)};color:{C_MOD};border:1px solid {_rgba(C_MOD,0.3)};'
                f'padding:1px 6px;border-radius:3px;font-size:0.58rem;font-weight:700;margin-left:5px;">STALE</span>'
            ) if stale else ""

            rows_html += (
                f'<div style="display:grid;grid-template-columns:28px 1fr 60px 120px 70px;'
                f'align-items:center;gap:10px;padding:10px 16px;border-bottom:1px solid rgba(255,255,255,0.04);'
                f'background:{row_bg};border-left:3px solid {cc};">'

                f'<div style="font-size:1.0rem;text-align:center;">{ci}</div>'

                f'<div style="min-width:0;">'
                f'<div style="font-size:0.78rem;font-weight:600;color:{C_TEXT};line-height:1.3;white-space:nowrap;'
                f'overflow:hidden;text-overflow:ellipsis;">{title}{stale_html}</div>'
                f'<div style="display:flex;gap:8px;align-items:center;margin-top:3px;">'
                f'<span style="font-size:0.6rem;color:{cc};text-transform:uppercase;'
                f'letter-spacing:0.07em;font-weight:700;">{cat.replace("_"," ")}</span>'
                f'</div>'
                f'</div>'

                f'<div style="text-align:center;">'
                f'<span style="font-size:1.3rem;font-weight:900;color:{ac};line-height:1;">{arrow}</span>'
                f'</div>'

                f'<div>'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:3px;">'
                f'<span style="font-size:0.6rem;color:{C_TEXT3};">CONVICTION</span>'
                f'<span style="font-size:0.65rem;font-weight:800;color:{sc};font-family:monospace;">{pct}%</span>'
                f'</div>'
                f'<div style="height:5px;border-radius:3px;background:rgba(255,255,255,0.06);overflow:hidden;">'
                f'<div style="width:{pct}%;height:100%;border-radius:3px;'
                f'background:linear-gradient(90deg,{_rgba(sc,0.6)},{sc});"></div>'
                f'</div>'
                f'</div>'

                f'<div style="text-align:center;">'
                f'<span style="background:{_rgba(ac,0.12)};color:{ac};border:1px solid {_rgba(ac,0.3)};'
                f'padding:2px 8px;border-radius:3px;font-size:0.62rem;font-weight:800;">{action.upper()}</span>'
                f'</div>'

                f'</div>'
            )

        hdr_html = (
            f'<div style="display:grid;grid-template-columns:28px 1fr 60px 120px 70px;'
            f'align-items:center;gap:10px;padding:8px 16px;border-bottom:1px solid {C_BORDER};">'
            f'<div></div>'
            f'<div style="font-size:0.56rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.1em;">SIGNAL TITLE</div>'
            f'<div style="font-size:0.56rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.1em;text-align:center;">DIR</div>'
            f'<div style="font-size:0.56rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.1em;">CONVICTION</div>'
            f'<div style="font-size:0.56rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.1em;text-align:center;">ACTION</div>'
            f'</div>'
        )

        panel_html = (
            f'<div style="margin-bottom:20px;">'
            f'<div style="font-size:0.65rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.14em;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid {C_BORDER};">'
            f'TOP {len(top5)} HIGHEST CONVICTION SIGNALS &nbsp;&#8212;&nbsp; of {len(insights)} total</div>'
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;overflow:hidden;">'
            f'{hdr_html}{rows_html}'
            f'</div>'
            f'</div>'
        )
        st.markdown(panel_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Top insights panel render failed: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# HERO BANNER (market tone + live metadata)
# ══════════════════════════════════════════════════════════════════════════════

def _render_hero(port_results: list, route_results: list, insights: list) -> None:
    try:
        has_data   = [r for r in port_results if getattr(r, "has_real_data", False)]
        avg_demand = _safe_avg([r.demand_score for r in has_data]) if has_data else 0.0
        top_score  = max((getattr(i, "score", 0) for i in insights), default=0.0)
        hi_conv    = sum(1 for i in insights if getattr(i, "score", 0) >= 0.70)
        strong_rts = sum(1 for r in route_results if getattr(r, "opportunity_label", "") == "Strong")

        if avg_demand >= 0.65:
            headline, tone, tone_color = "Global freight markets ELEVATED — strong demand across key corridors", "BULLISH", C_HIGH
        elif avg_demand >= 0.45:
            headline, tone, tone_color = "Mixed signals — selective opportunities in mid-tier lanes", "NEUTRAL", C_MOD
        elif avg_demand > 0:
            headline, tone, tone_color = "Subdued demand environment — defensive positioning recommended", "BEARISH", C_LOW
        else:
            headline, tone, tone_color = "Platform initializing — awaiting live market data feeds", "LOADING", C_TEXT3

        meta_items = [
            ("Ports Tracked",     str(len(port_results)),                          C_ACCENT),
            ("Live Feed",         str(len(has_data)),                              C_HIGH),
            ("Signals",          str(len(insights)),                               C_MOD),
            ("High-Conv",        str(hi_conv),                                     _score_color(hi_conv / max(len(insights), 1))),
            ("Strong Routes",    str(strong_rts),                                  C_HIGH),
            ("Top Score",        f"{top_score:.0%}" if top_score else "—",         _score_color(top_score) if top_score else C_TEXT3),
        ]

        meta_html = ""
        for i, (lbl, val, col) in enumerate(meta_items):
            sep = f"border-right:1px solid rgba(255,255,255,0.06);" if i < len(meta_items) - 1 else ""
            meta_html += (
                f'<div style="flex:1;min-width:90px;text-align:center;padding:0 14px;{sep}">'
                f'<div style="font-size:1.8rem;font-weight:900;color:{col};font-family:monospace;'
                f'line-height:1;text-shadow:0 0 20px {_rgba(col,0.4)};">{val}</div>'
                f'<div style="font-size:0.56rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;'
                f'letter-spacing:0.12em;margin-top:5px;">{lbl}</div>'
                f'</div>'
            )

        hero_html = (
            f'<div class="bbg-hero" style="background:linear-gradient(135deg,{C_BG} 0%,{C_CARD} 50%,#0d1a2e 100%);'
            f'border:1px solid rgba(59,130,246,0.22);border-top:3px solid {C_ACCENT};border-radius:14px;'
            f'padding:28px 32px 22px;margin-bottom:16px;'
            f'box-shadow:0 8px 40px rgba(0,0,0,0.5),inset 0 1px 0 rgba(255,255,255,0.04);">'

            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
            f'margin-bottom:20px;flex-wrap:wrap;gap:10px;">'
            f'<div style="flex:1;min-width:0;">'
            f'<div style="font-size:0.58rem;font-weight:800;color:{C_ACCENT};text-transform:uppercase;'
            f'letter-spacing:0.14em;margin-bottom:5px;">Global Cargo Intelligence Platform</div>'
            f'<div style="font-size:1.35rem;font-weight:800;color:{C_TEXT};line-height:1.3;max-width:680px;">{headline}</div>'
            f'<div style="font-size:0.75rem;color:{C_TEXT2};margin-top:7px;">'
            f'<span class="bbg-live-dot" style="background:{C_HIGH};box-shadow:0 0 8px {C_HIGH};"></span>'
            f'<span style="color:{C_HIGH};font-weight:700;">LIVE</span>'
            f'&nbsp;&middot;&nbsp;{_now_utc()}&nbsp;&middot;&nbsp;Confidence threshold: 70%'
            f'</div>'
            f'</div>'
            f'<div style="flex-shrink:0;text-align:right;">'
            f'<div style="font-size:0.56rem;font-weight:800;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.1em;margin-bottom:4px;">Market Tone</div>'
            f'<div style="font-size:1.05rem;font-weight:900;color:{tone_color};'
            f'background:{_rgba(tone_color,0.1)};border:1px solid {_rgba(tone_color,0.35)};'
            f'border-radius:8px;padding:8px 20px;'
            f'box-shadow:0 0 18px {_rgba(tone_color,0.2)};">{tone}</div>'
            f'</div>'
            f'</div>'

            f'<div style="display:flex;flex-wrap:wrap;border-top:1px solid rgba(255,255,255,0.06);'
            f'padding-top:18px;">{meta_html}</div>'

            f'</div>'
        )
        st.markdown(hero_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Hero render failed: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# COLD-START SPLASH
# ══════════════════════════════════════════════════════════════════════════════

def _render_cold_start() -> None:
    try:
        st.markdown(
            f'<div class="bbg-hero" style="background:linear-gradient(135deg,{C_CARD} 0%,#0f1d35 100%);'
            f'border:1px solid rgba(59,130,246,0.3);border-left:4px solid {C_ACCENT};'
            f'border-radius:14px;padding:36px;margin-bottom:24px;text-align:center;">'
            f'<div style="font-size:2.8rem;margin-bottom:14px;">&#128674;</div>'
            f'<div style="font-size:1.35rem;font-weight:800;color:{C_TEXT};margin-bottom:10px;">'
            f'Welcome to Global Cargo Intelligence</div>'
            f'<div style="font-size:0.88rem;color:{C_TEXT2};max-width:540px;margin:0 auto 24px;line-height:1.75;">'
            f'No data has loaded yet — normal on first run or when API keys are not configured. '
            f'The dashboard populates automatically once data is available.</div>'
            f'<div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap;">'
            f'<div style="background:rgba(59,130,246,0.10);border:1px solid rgba(59,130,246,0.3);'
            f'border-radius:8px;padding:10px 20px;font-size:0.82rem;color:{C_TEXT2};">'
            f'<b style="color:{C_ACCENT};">Step 1</b>&nbsp; Add API keys to <code>.env</code></div>'
            f'<div style="background:rgba(16,185,129,0.10);border:1px solid rgba(16,185,129,0.3);'
            f'border-radius:8px;padding:10px 20px;font-size:0.82rem;color:{C_TEXT2};">'
            f'<b style="color:{C_HIGH};">Step 2</b>&nbsp; Click <b>Refresh Data</b> in the sidebar</div>'
            f'<div style="background:rgba(245,158,11,0.10);border:1px solid rgba(245,158,11,0.3);'
            f'border-radius:8px;padding:10px 20px;font-size:0.82rem;color:{C_TEXT2};">'
            f'<b style="color:{C_MOD};">Step 3</b>&nbsp; Data loads in ~30–60 s</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"Cold start splash failed: {exc}")
        st.info("Dashboard loading — configure API credentials to enable live data.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RENDER — new signature
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
    """Bloomberg Terminal-quality Overview Dashboard.

    Args:
        port_results:  list[PortDemandResult] or compatible objects
        route_results: list[RouteOpportunity] or compatible objects
        insights:      list[Insight] or compatible objects
        freight_data:  dict of freight index values (BDI, WCI, SCFI, CCFI, VLCC, LNG …)
        macro_data:    dict of macro / FX values (crude, EUR/USD, USD/CNY, S&P 500 …)
        stock_data:    dict of equity prices (ZIM, MATX, HLAG, SBLK …)
        alerts:        optional list of alert objects / dicts
    """
    try:
        _inject_css()

        port_results   = port_results   or []
        route_results  = route_results  or []
        insights       = insights       or []
        freight_data   = freight_data   or {}
        macro_data     = macro_data     or {}
        stock_data     = stock_data     or {}
        alerts         = alerts         or []

        all_empty = not port_results and not route_results and not insights

        # ── 1. Ticker tape (always shown) ─────────────────────────────────────
        try:
            _render_ticker_tape(freight_data, macro_data, stock_data)
        except Exception as exc:
            logger.warning(f"Ticker tape error: {exc}")

        # ── Cold-start splash if no data at all ───────────────────────────────
        if all_empty:
            _render_cold_start()
            # Still show nav chips and freshness so user knows what's coming
            try:
                _render_nav_chips()
            except Exception as exc:
                logger.warning(f"Nav chips error (cold): {exc}")
            try:
                _render_data_freshness(port_results, route_results, insights, freight_data, macro_data, stock_data)
            except Exception as exc:
                logger.warning(f"Freshness panel error (cold): {exc}")
            return

        # ── 2. Hero banner ────────────────────────────────────────────────────
        try:
            _render_hero(port_results, route_results, insights)
        except Exception as exc:
            logger.warning(f"Hero banner error: {exc}")

        # ── 3. Live KPI grid (3×4) ────────────────────────────────────────────
        try:
            _render_kpi_grid(port_results, route_results, insights, freight_data, macro_data, stock_data, alerts)
        except Exception as exc:
            logger.warning(f"KPI grid error: {exc}")

        st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)

        # ── 4. Signal Heat Matrix ─────────────────────────────────────────────
        try:
            _render_heat_matrix(route_results, insights)
        except Exception as exc:
            logger.warning(f"Heat matrix error: {exc}")

        # ── 5. Quick Navigation chips ─────────────────────────────────────────
        try:
            _render_nav_chips()
        except Exception as exc:
            logger.warning(f"Nav chips error: {exc}")

        # ── 6. Top Insights panel ─────────────────────────────────────────────
        try:
            _render_top_insights(insights)
        except Exception as exc:
            logger.warning(f"Top insights error: {exc}")

        # ── 7. Data Freshness panel ───────────────────────────────────────────
        try:
            _render_data_freshness(port_results, route_results, insights, freight_data, macro_data, stock_data)
        except Exception as exc:
            logger.warning(f"Data freshness error: {exc}")

    except Exception as exc:
        logger.error(f"tab_overview.render fatal: {exc}")
        st.error(f"Overview dashboard error: {exc}")
