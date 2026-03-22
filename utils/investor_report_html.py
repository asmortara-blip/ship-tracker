"""investor_report_html.py — Institutional-grade HTML investor report builder.

Produces a fully self-contained HTML document styled after Bloomberg Intelligence
/ Goldman Sachs research notes. White/off-white background, navy headers, dense
data tables, print-ready layout.

Usage:
    from utils.investor_report_html import render_investor_report_html
    html = render_investor_report_html(report)
    bytes_out = html.encode("utf-8")
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, List

# ---------------------------------------------------------------------------
# Color palette — Bloomberg/GS institutional light theme
# ---------------------------------------------------------------------------
C_BODY          = "#ffffff"
C_SURFACE       = "#f8f9fa"
C_SURFACE2      = "#f1f3f5"
C_BORDER        = "#d0d7de"
C_BORDER_LIGHT  = "#e9ecef"
C_NAVY          = "#0f285a"       # primary header / accent
C_NAVY_MID      = "#1a3a72"       # lighter navy for sub-headers
C_NAVY_RULE     = "#2c4a8a"       # rule lines
C_TEXT          = "#1a1a2e"       # primary body text
C_TEXT2         = "#4a5568"       # secondary text
C_TEXT3         = "#718096"       # muted / captions
C_GOLD          = "#b8860b"       # accent gold (GS brand feel)
C_POS           = "#166044"       # positive numbers (dark green)
C_NEG           = "#b91e1e"       # negative numbers (dark red)
C_NEUT          = "#5a6272"       # neutral
C_POS_BG        = "#ecfdf5"
C_NEG_BG        = "#fef2f2"
C_WARN          = "#7c4a00"
C_WARN_BG       = "#fffbeb"
C_HIGHLIGHT     = "#eff6ff"       # light blue row highlight
C_NAV_BG        = "#0f285a"       # horizontal nav bar bg

# Conviction / rating palette
_CONV_COLORS = {
    "HIGH":   C_POS,
    "MEDIUM": C_WARN,
    "LOW":    C_TEXT3,
}
_SENT_COLORS = {
    "BULLISH": C_POS,
    "BEARISH": C_NEG,
    "NEUTRAL": C_NEUT,
    "MIXED":   C_WARN,
}
_RISK_COLORS = {
    "LOW":      C_POS,
    "MODERATE": C_WARN,
    "HIGH":     C_NEG,
    "CRITICAL": "#7f0000",
}
_ACTION_COLORS = {
    "BUY":     C_POS,
    "LONG":    C_POS,
    "SELL":    C_NEG,
    "SHORT":   C_NEG,
    "HOLD":    C_WARN,
    "MONITOR": C_NAVY_MID,
    "AVOID":   C_NEG,
    "WATCH":   C_TEXT3,
}


# ---------------------------------------------------------------------------
# Safe accessor helpers
# ---------------------------------------------------------------------------

def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_str(val: Any, default: str = "—") -> str:
    if val is None:
        return default
    s = str(val).strip()
    return s if s else default


def _safe_attr(obj: Any, *attrs: str, default: Any = None) -> Any:
    for attr in attrs:
        try:
            v = obj[attr] if isinstance(obj, dict) else getattr(obj, attr, None)
            if v is not None:
                return v
        except (KeyError, TypeError):
            pass
    return default


def _safe_list(val: Any) -> list:
    if isinstance(val, list):
        return val
    return []


def _safe_dict(val: Any) -> dict:
    if isinstance(val, dict):
        return val
    return {}


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _fmt_pct(val: float, decimals: int = 1) -> str:
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.{decimals}f}%"


def _fmt_price(val: float) -> str:
    if val >= 10_000:
        return f"${val:,.0f}"
    if val >= 1_000:
        return f"${val:,.1f}"
    return f"${val:.2f}"


def _fmt_num(val: float, decimals: int = 2) -> str:
    return f"{val:,.{decimals}f}"


def _pct_color(pct: float) -> str:
    if pct > 0.5:
        return C_POS
    if pct < -0.5:
        return C_NEG
    return C_NEUT


def _pct_cell(pct: float) -> str:
    col = _pct_color(pct)
    bg  = C_POS_BG if pct > 0.5 else (C_NEG_BG if pct < -0.5 else "transparent")
    arrow = "&#9650;" if pct >= 0 else "&#9660;"
    return (
        f'<td style="color:{col};background:{bg};text-align:right;'
        f'font-family:\'Courier New\',monospace;font-weight:600;white-space:nowrap">'
        f'{arrow}&nbsp;{_fmt_pct(pct)}</td>'
    )


def _change_span(pct: float) -> str:
    col   = _pct_color(pct)
    arrow = "&#9650;" if pct >= 0 else "&#9660;"
    return (
        f'<span style="color:{col};font-weight:600;'
        f'font-family:\'Courier New\',monospace">'
        f'{arrow}&nbsp;{_fmt_pct(pct)}</span>'
    )


def _badge(text: str, color: str = C_NAVY, bg: str = "") -> str:
    bg_val = bg if bg else _hex_rgba(color, 0.10)
    return (
        f'<span style="display:inline-block;padding:1px 8px;border-radius:3px;'
        f'font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;'
        f'background:{bg_val};color:{color};border:1px solid {_hex_rgba(color,0.30)}">'
        f'{text}</span>'
    )


def _hex_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

def _build_css() -> str:
    return """
/* ── Reset ── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }

/* ── Base ── */
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    line-height: 1.65;
    color: #1a1a2e;
    background: #ffffff;
    max-width: 1240px;
    margin: 0 auto;
    -webkit-font-smoothing: antialiased;
}

a { color: #0f285a; text-decoration: none; }
a:hover { text-decoration: underline; }

/* ── Mono ── */
.mono, code, pre {
    font-family: 'SFMono-Regular', 'Courier New', Courier, monospace;
    font-size: 0.9em;
}

/* ── Top header bar ── */
.ir-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: #0f285a;
    color: #ffffff;
    padding: 10px 32px;
    font-size: 11px;
    letter-spacing: .08em;
    text-transform: uppercase;
}
.ir-topbar-firm {
    font-size: 14px;
    font-weight: 800;
    letter-spacing: .12em;
    color: #ffffff;
}
.ir-topbar-confidential {
    background: #b8860b;
    color: #fff;
    padding: 3px 14px;
    border-radius: 2px;
    font-weight: 700;
    letter-spacing: .14em;
    font-size: 10px;
}
.ir-topbar-date {
    color: #a8bcd4;
    font-size: 11px;
    text-align: right;
}

/* ── Cover section ── */
.ir-cover {
    background: linear-gradient(160deg, #0f285a 0%, #1a3a72 60%, #0f285a 100%);
    color: #ffffff;
    padding: 56px 56px 48px;
}
.ir-cover-eyebrow {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .2em;
    text-transform: uppercase;
    color: #a8bcd4;
    margin-bottom: 12px;
}
.ir-cover-title {
    font-size: 32px;
    font-weight: 800;
    letter-spacing: -.5px;
    line-height: 1.15;
    color: #ffffff;
    margin-bottom: 10px;
}
.ir-cover-subtitle {
    font-size: 15px;
    color: #c8d8ea;
    margin-bottom: 36px;
    font-weight: 400;
}
.ir-cover-metrics {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 1px;
    background: rgba(255,255,255,0.12);
    border-radius: 6px;
    overflow: hidden;
    margin-bottom: 32px;
}
.ir-cover-metric {
    background: rgba(0,0,0,0.20);
    padding: 18px 20px;
    text-align: center;
}
.ir-cover-metric-label {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: .15em;
    text-transform: uppercase;
    color: #a8bcd4;
    margin-bottom: 6px;
}
.ir-cover-metric-value {
    font-size: 22px;
    font-weight: 800;
    font-family: 'SFMono-Regular', 'Courier New', monospace;
    line-height: 1;
}
.ir-cover-metric-sub {
    font-size: 10px;
    color: #a8bcd4;
    margin-top: 4px;
}
.ir-dq-pill {
    display: inline-block;
    padding: 4px 14px;
    border-radius: 20px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .1em;
    text-transform: uppercase;
    border: 1px solid rgba(255,255,255,0.25);
    color: #c8d8ea;
    background: rgba(255,255,255,0.08);
}

/* ── Horizontal nav ── */
.ir-nav {
    position: sticky;
    top: 0;
    z-index: 100;
    background: #0f285a;
    border-bottom: 3px solid #b8860b;
    display: flex;
    align-items: center;
    overflow-x: auto;
    scrollbar-width: none;
}
.ir-nav::-webkit-scrollbar { display: none; }
.ir-nav a {
    display: block;
    padding: 12px 18px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: #a8bcd4;
    white-space: nowrap;
    border-right: 1px solid rgba(255,255,255,0.08);
    transition: background .15s, color .15s;
}
.ir-nav a:hover {
    background: rgba(255,255,255,0.10);
    color: #ffffff;
    text-decoration: none;
}

/* ── Sections ── */
.ir-section {
    padding: 48px 56px 40px;
    border-bottom: 1px solid #e9ecef;
    background: #ffffff;
}
.ir-section:nth-child(even) { background: #f8f9fa; }

/* ── Section header ── */
.ir-section-head {
    display: flex;
    align-items: flex-end;
    gap: 20px;
    margin-bottom: 28px;
    padding-bottom: 14px;
    border-bottom: 2px solid #0f285a;
}
.ir-section-num {
    font-family: 'SFMono-Regular', 'Courier New', monospace;
    font-size: 36px;
    font-weight: 900;
    color: rgba(15,40,90,0.12);
    line-height: 1;
    min-width: 56px;
}
.ir-section-title {
    font-size: 20px;
    font-weight: 800;
    color: #0f285a;
    letter-spacing: -.3px;
    line-height: 1.2;
}
.ir-section-sub {
    font-size: 10px;
    color: #718096;
    text-transform: uppercase;
    letter-spacing: .12em;
    margin-top: 4px;
}

/* ── KPI / stat cards ── */
.ir-kpi-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 14px;
    margin: 20px 0;
}
.ir-kpi {
    background: #ffffff;
    border: 1px solid #d0d7de;
    border-top: 3px solid #0f285a;
    border-radius: 4px;
    padding: 14px 16px;
}
.ir-section:nth-child(even) .ir-kpi { background: #ffffff; }
.ir-kpi-label {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .14em;
    color: #718096;
    margin-bottom: 6px;
}
.ir-kpi-value {
    font-size: 22px;
    font-weight: 800;
    font-family: 'SFMono-Regular', 'Courier New', monospace;
    line-height: 1.1;
    color: #1a1a2e;
}
.ir-kpi-sub {
    font-size: 10px;
    color: #718096;
    margin-top: 4px;
}

/* ── Tables ── */
.ir-table-wrap {
    overflow-x: auto;
    margin: 16px 0;
    border: 1px solid #d0d7de;
    border-radius: 4px;
}
.ir-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
}
.ir-table thead th {
    background: #0f285a;
    color: #ffffff;
    font-size: 9.5px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .1em;
    padding: 10px 12px;
    text-align: left;
    white-space: nowrap;
    border-right: 1px solid rgba(255,255,255,0.10);
    position: sticky;
    top: 0;
}
.ir-table thead th:last-child { border-right: none; }
.ir-table tbody tr { border-bottom: 1px solid #e9ecef; }
.ir-table tbody tr:last-child { border-bottom: none; }
.ir-table tbody tr:nth-child(even) { background: #f8f9fa; }
.ir-table tbody tr:hover { background: #eff6ff; }
.ir-table td {
    padding: 9px 12px;
    vertical-align: middle;
    color: #1a1a2e;
    border-right: 1px solid #e9ecef;
}
.ir-table td:last-child { border-right: none; }
.ir-table .num { font-family: 'SFMono-Regular','Courier New',monospace; text-align: right; }
.ir-table .right { text-align: right; }
.ir-table .center { text-align: center; }

/* ── Two-column layout ── */
.ir-two-col {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
    margin: 20px 0;
}
.ir-three-col {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 20px;
    margin: 20px 0;
}
@media (max-width: 900px) {
    .ir-two-col, .ir-three-col { grid-template-columns: 1fr; }
}

/* ── Sub-cards ── */
.ir-card {
    background: #ffffff;
    border: 1px solid #d0d7de;
    border-radius: 4px;
    padding: 18px 20px;
}
.ir-section:nth-child(even) .ir-card { background: #ffffff; }
.ir-card-title {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .12em;
    color: #0f285a;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid #d0d7de;
}

/* ── Narrative text ── */
.ir-narrative {
    color: #1a1a2e;
    font-size: 13px;
    line-height: 1.75;
    max-width: 820px;
}
.ir-narrative p { margin-bottom: 14px; }
.ir-narrative p:last-child { margin-bottom: 0; }

/* ── Sentiment stacked bar ── */
.ir-sent-bar {
    height: 10px;
    border-radius: 5px;
    overflow: hidden;
    background: #e9ecef;
    display: flex;
    margin: 8px 0 6px;
}

/* ── Recommendation boxes ── */
.ir-rec-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
    margin: 20px 0;
}
.ir-rec-box {
    border: 1px solid #d0d7de;
    border-left: 5px solid #0f285a;
    border-radius: 4px;
    padding: 18px 20px;
    background: #ffffff;
}
.ir-rec-rank {
    font-size: 28px;
    font-weight: 900;
    color: rgba(15,40,90,0.12);
    font-family: 'SFMono-Regular','Courier New',monospace;
    line-height: 1;
    float: right;
}
.ir-rec-title { font-size: 14px; font-weight: 700; color: #0f285a; margin-bottom: 6px; }
.ir-rec-body { font-size: 12px; color: #4a5568; line-height: 1.6; margin-bottom: 10px; }
.ir-rec-meta { font-size: 10px; color: #718096; }

/* ── News feed ── */
.ir-news-item {
    padding: 10px 0;
    border-bottom: 1px solid #e9ecef;
    display: flex;
    align-items: flex-start;
    gap: 10px;
}
.ir-news-item:last-child { border-bottom: none; }
.ir-news-score {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    font-weight: 800;
    font-family: 'SFMono-Regular','Courier New',monospace;
    flex-shrink: 0;
}
.ir-news-headline { font-size: 12px; color: #1a1a2e; font-weight: 500; line-height: 1.4; }
.ir-news-meta { font-size: 10px; color: #718096; margin-top: 3px; }

/* ── Topic pills ── */
.ir-topics { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.ir-topic-pill {
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 600;
    border: 1px solid #d0d7de;
    background: #f8f9fa;
    color: #4a5568;
}

/* ── Score bar ── */
.ir-score-bar-wrap {
    display: flex;
    align-items: center;
    gap: 10px;
}
.ir-score-bar-track {
    flex: 1;
    height: 6px;
    background: #e9ecef;
    border-radius: 3px;
    overflow: hidden;
}
.ir-score-bar-fill { height: 100%; border-radius: 3px; }
.ir-score-pct {
    font-size: 10px;
    font-weight: 700;
    font-family: 'SFMono-Regular','Courier New',monospace;
    color: #4a5568;
    min-width: 32px;
    text-align: right;
}

/* ── Risk table ── */
.ir-risk-row-HIGH td { background: #fef2f2 !important; }
.ir-risk-row-CRITICAL td { background: #fef2f2 !important; }

/* ── Footer ── */
.ir-footer {
    background: #0f285a;
    color: #a8bcd4;
    padding: 32px 56px 40px;
    font-size: 10.5px;
    line-height: 1.7;
}
.ir-footer-disclaimer {
    color: #718096;
    font-size: 10px;
    margin-top: 20px;
    padding-top: 16px;
    border-top: 1px solid rgba(255,255,255,0.10);
    line-height: 1.6;
}

/* ── Divider ── */
.ir-rule {
    border: none;
    border-top: 1px solid #d0d7de;
    margin: 24px 0;
}

/* ── Print ── */
@media print {
    body { max-width: 100%; font-size: 11px; background: #fff; color: #000; }
    .ir-topbar { background: #0f285a !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .ir-cover { background: #0f285a !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .ir-nav { display: none; }
    .ir-section { page-break-before: always; padding: 32px 32px 24px; }
    .ir-section:first-child { page-break-before: avoid; }
    .ir-table thead th { background: #0f285a !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .ir-table tbody tr:nth-child(even) { background: #f8f9fa !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .ir-footer { background: #0f285a !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .ir-rec-box { break-inside: avoid; }
    .ir-kpi-row { grid-template-columns: repeat(4, 1fr); }
    .ir-two-col { grid-template-columns: 1fr 1fr; }
    a[href]::after { content: none; }
}
"""


# ---------------------------------------------------------------------------
# Component builders
# ---------------------------------------------------------------------------

def _topbar(report_date: str) -> str:
    return f"""
<div class="ir-topbar">
  <span class="ir-topbar-firm">ShipIntel Research</span>
  <span class="ir-topbar-confidential">Confidential &mdash; Institutional Distribution Only</span>
  <span class="ir-topbar-date">{report_date}</span>
</div>"""


def _cover(report) -> str:
    sent  = report.sentiment
    macro = report.macro
    alpha = report.alpha
    ai    = report.ai

    overall_score = _safe_float(_safe_attr(sent, "overall_score"), 0.0)
    overall_label = _safe_str(_safe_attr(sent, "overall_label"), "NEUTRAL")
    dq            = _safe_str(_safe_attr(report, "data_quality"), "FULL")
    report_date   = _safe_str(_safe_attr(report, "report_date"), "—")
    gen_at        = _safe_str(_safe_attr(report, "generated_at"), "")
    bdi           = _safe_float(_safe_attr(macro, "bdi"), 0.0)
    bdi_chg       = _safe_float(_safe_attr(macro, "bdi_change_30d_pct"), 0.0)
    wti           = _safe_float(_safe_attr(macro, "wti"), 0.0)
    sc_stress     = _safe_str(_safe_attr(macro, "supply_chain_stress"), "MODERATE")
    sig_count     = _safe_float(_safe_attr(alpha, "signal_count_by_conviction"), None)
    hi_conv       = _safe_float(
        (_safe_dict(_safe_attr(alpha, "signal_count_by_conviction"))).get("HIGH", 0), 0
    )
    active_opp    = _safe_float(_safe_attr(report.market, "active_opportunities"), 0)

    sent_col   = _SENT_COLORS.get(overall_label.upper(), C_NEUT)
    dq_label   = {"FULL": "Full Data", "PARTIAL": "Partial Data", "DEGRADED": "Degraded Data"}.get(dq, dq)
    score_sign = "+" if overall_score > 0 else ""

    gen_str = ""
    if gen_at:
        try:
            dt = datetime.fromisoformat(gen_at.replace("Z", "+00:00"))
            gen_str = dt.strftime("%H:%M UTC")
        except Exception:
            gen_str = gen_at[:16]

    return f"""
<div class="ir-cover">
  <div class="ir-cover-eyebrow">Shipping Intelligence &bull; {report_date}</div>
  <div class="ir-cover-title">Global Shipping Markets<br>Investor Research Report</div>
  <div class="ir-cover-subtitle">
    Comprehensive coverage of freight rates, macro indicators, alpha signals,<br>
    and equity analysis across the institutional shipping universe
  </div>

  <div class="ir-cover-metrics">
    <div class="ir-cover-metric">
      <div class="ir-cover-metric-label">Composite Sentiment</div>
      <div class="ir-cover-metric-value" style="color:{sent_col}">{score_sign}{overall_score:.2f}</div>
      <div class="ir-cover-metric-sub">{overall_label}</div>
    </div>
    <div class="ir-cover-metric">
      <div class="ir-cover-metric-label">Baltic Dry Index</div>
      <div class="ir-cover-metric-value">{_fmt_num(bdi, 0) if bdi else '—'}</div>
      <div class="ir-cover-metric-sub">{_fmt_pct(bdi_chg)} 30d</div>
    </div>
    <div class="ir-cover-metric">
      <div class="ir-cover-metric-label">WTI Crude</div>
      <div class="ir-cover-metric-value">{_fmt_price(wti) if wti else '—'}</div>
      <div class="ir-cover-metric-sub">per barrel</div>
    </div>
    <div class="ir-cover-metric">
      <div class="ir-cover-metric-label">High-Conviction Signals</div>
      <div class="ir-cover-metric-value">{int(hi_conv)}</div>
      <div class="ir-cover-metric-sub">{int(active_opp)} active opportunities</div>
    </div>
    <div class="ir-cover-metric">
      <div class="ir-cover-metric-label">Supply Chain Stress</div>
      <div class="ir-cover-metric-value" style="font-size:16px;color:{_RISK_COLORS.get(sc_stress.upper(), C_NEUT)}">{sc_stress}</div>
      <div class="ir-cover-metric-sub">current assessment</div>
    </div>
  </div>

  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
    <span class="ir-dq-pill">Data Quality: {dq_label}</span>
    {"<span class='ir-dq-pill'>Generated: " + gen_str + "</span>" if gen_str else ""}
  </div>
</div>"""


def _nav() -> str:
    links = [
        ("#s1", "Executive Summary"),
        ("#s2", "Alpha Signals"),
        ("#s3", "Freight Rates"),
        ("#s4", "Macroeconomic"),
        ("#s5", "Equities"),
        ("#s6", "Market Intel"),
        ("#s7", "Risk & Scenarios"),
        ("#s8", "Recommendations"),
    ]
    items = "".join(f'<a href="{href}">{label}</a>' for href, label in links)
    return f'<nav class="ir-nav">{items}</nav>'


def _section_head(num: int, title: str, sub: str = "") -> str:
    return f"""
<div class="ir-section-head">
  <div class="ir-section-num">{num:02d}</div>
  <div>
    <div class="ir-section-title">{title}</div>
    {"<div class='ir-section-sub'>" + sub + "</div>" if sub else ""}
  </div>
</div>"""


def _kpi(label: str, value: str, sub: str = "", color: str = "") -> str:
    col_style = f"color:{color};" if color else ""
    return f"""
<div class="ir-kpi">
  <div class="ir-kpi-label">{label}</div>
  <div class="ir-kpi-value" style="{col_style}">{value}</div>
  {"<div class='ir-kpi-sub'>" + sub + "</div>" if sub else ""}
</div>"""


def _score_bar(score: float, color: str) -> str:
    """Horizontal 0–1 score bar."""
    pct = max(0.0, min(1.0, score)) * 100
    return f"""
<div class="ir-score-bar-wrap">
  <div class="ir-score-bar-track">
    <div class="ir-score-bar-fill" style="width:{pct:.1f}%;background:{color}"></div>
  </div>
  <span class="ir-score-pct">{pct:.0f}%</span>
</div>"""


def _sent_stacked_bar(bullish: int, bearish: int, neutral: int) -> str:
    total = bullish + bearish + neutral
    if total == 0:
        return '<p style="color:#718096;font-size:11px">No sentiment data</p>'
    bp = bullish / total * 100
    np_ = neutral / total * 100
    rp = bearish / total * 100
    return f"""
<div class="ir-sent-bar">
  <div style="width:{bp:.1f}%;background:{C_POS}"></div>
  <div style="width:{np_:.1f}%;background:#cbd5e0"></div>
  <div style="width:{rp:.1f}%;background:{C_NEG}"></div>
</div>
<div style="display:flex;gap:14px;font-size:10px;margin-top:4px;flex-wrap:wrap">
  <span style="color:{C_POS};font-weight:600">&#9632; Bullish {bullish} ({bp:.0f}%)</span>
  <span style="color:#718096;font-weight:600">&#9632; Neutral {neutral} ({np_:.0f}%)</span>
  <span style="color:{C_NEG};font-weight:600">&#9632; Bearish {bearish} ({rp:.0f}%)</span>
</div>"""


# ---------------------------------------------------------------------------
# Section 1 — Executive Summary
# ---------------------------------------------------------------------------

def _section_executive_summary(report) -> str:
    sent = report.sentiment
    ai   = report.ai

    overall_score = _safe_float(_safe_attr(sent, "overall_score"), 0.0)
    overall_label = _safe_str(_safe_attr(sent, "overall_label"), "NEUTRAL")
    news_score    = _safe_float(_safe_attr(sent, "news_score"), 0.0)
    freight_score = _safe_float(_safe_attr(sent, "freight_score"), 0.0)
    macro_score   = _safe_float(_safe_attr(sent, "macro_score"), 0.0)
    alpha_score   = _safe_float(_safe_attr(sent, "alpha_score"), 0.0)
    bullish       = int(_safe_float(_safe_attr(sent, "bullish_count"), 0))
    bearish       = int(_safe_float(_safe_attr(sent, "bearish_count"), 0))
    neutral       = int(_safe_float(_safe_attr(sent, "neutral_count"), 0))
    keywords      = _safe_list(_safe_attr(sent, "top_keywords"))
    trending      = _safe_list(_safe_attr(sent, "trending_topics"))

    exec_summary   = _safe_str(_safe_attr(ai, "executive_summary"), "")
    sent_narrative = _safe_str(_safe_attr(ai, "sentiment_narrative"), "")

    sent_col = _SENT_COLORS.get(overall_label.upper(), C_NEUT)
    sign     = "+" if overall_score > 0 else ""

    # Sentiment component rows
    components = [
        ("News Sentiment",    news_score,    "#1a3a72"),
        ("Freight Momentum",  freight_score, "#2c5282"),
        ("Macro Environment", macro_score,   "#2b6cb0"),
        ("Alpha Signals",     alpha_score,   "#2c4a8a"),
    ]
    comp_rows = ""
    for lbl, sc, col in components:
        # Normalize -1..1 to 0..1
        normalized = (sc + 1.0) / 2.0
        bar = _score_bar(normalized, col)
        sign_sc = "+" if sc > 0 else ""
        comp_rows += f"""
<tr>
  <td style="font-weight:600;white-space:nowrap">{lbl}</td>
  <td style="width:220px;padding:9px 12px">{bar}</td>
  <td class="num" style="font-weight:700;color:{_pct_color(sc * 50)}">{sign_sc}{sc:.3f}</td>
</tr>"""

    # Topic pills
    topic_html = ""
    if trending:
        for t in trending[:12]:
            topic  = _safe_str(_safe_attr(t, "topic") if isinstance(t, dict) else t)
            count  = int(_safe_float(_safe_attr(t, "count") if isinstance(t, dict) else 0, 0))
            label  = f"{topic}" + (f" ({count})" if count else "")
            topic_html += f'<span class="ir-topic-pill">{label}</span>'
    elif keywords:
        for k in keywords[:14]:
            topic_html += f'<span class="ir-topic-pill">{_safe_str(k)}</span>'

    # Narrative paragraphs
    def _paras(text: str) -> str:
        if not text:
            return '<p style="color:#718096;font-style:italic">Narrative not available.</p>'
        parts = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not parts:
            parts = [text.strip()]
        return "".join(f"<p>{p}</p>" for p in parts)

    return f"""
<section class="ir-section" id="s1">
  {_section_head(1, "Executive Summary", "Composite market assessment and sentiment overview")}

  <div class="ir-two-col">
    <div>
      <div class="ir-narrative">{_paras(exec_summary)}</div>
      {"<div class='ir-narrative' style='margin-top:16px'>" + _paras(sent_narrative) + "</div>" if sent_narrative else ""}
    </div>
    <div>
      <div class="ir-card">
        <div class="ir-card-title">Composite Sentiment Score</div>
        <div style="text-align:center;padding:16px 0 8px">
          <div style="font-size:52px;font-weight:900;font-family:'SFMono-Regular','Courier New',monospace;color:{sent_col};line-height:1">{sign}{overall_score:.3f}</div>
          <div style="margin-top:8px">{_badge(overall_label, sent_col)}</div>
        </div>
        <hr class="ir-rule">
        <div class="ir-table-wrap" style="border:none;margin:0">
          <table class="ir-table" style="border:none">
            <thead><tr>
              <th>Component</th><th>Score Bar</th><th class="right">Score</th>
            </tr></thead>
            <tbody>{comp_rows}</tbody>
          </table>
        </div>
        <hr class="ir-rule">
        <div class="ir-card-title" style="margin-top:4px">News Article Sentiment</div>
        {_sent_stacked_bar(bullish, bearish, neutral)}
      </div>

      {"<div class='ir-card' style='margin-top:16px'><div class='ir-card-title'>Trending Topics &amp; Keywords</div><div class='ir-topics'>" + topic_html + "</div></div>" if topic_html else ""}
    </div>
  </div>
</section>"""


# ---------------------------------------------------------------------------
# Section 2 — Alpha Signals
# ---------------------------------------------------------------------------

def _section_alpha_signals(report) -> str:
    alpha = report.alpha
    signals    = _safe_list(_safe_attr(alpha, "signals"))
    top_long   = _safe_list(_safe_attr(alpha, "top_long"))
    top_short  = _safe_list(_safe_attr(alpha, "top_short"))
    cnt_type   = _safe_dict(_safe_attr(alpha, "signal_count_by_type"))
    cnt_conv   = _safe_dict(_safe_attr(alpha, "signal_count_by_conviction"))

    ai = report.ai
    opp_narrative = _safe_str(_safe_attr(ai, "opportunity_narrative"), "")

    # KPIs
    total_sigs = len(signals)
    hi_conv    = int(_safe_float(cnt_conv.get("HIGH", 0), 0))
    med_conv   = int(_safe_float(cnt_conv.get("MEDIUM", 0), 0))
    lo_conv    = int(_safe_float(cnt_conv.get("LOW", 0), 0))
    long_ct    = sum(1 for s in signals if _safe_str(_safe_attr(s, "direction")).upper() in ("LONG", "BUY"))
    short_ct   = sum(1 for s in signals if _safe_str(_safe_attr(s, "direction")).upper() in ("SHORT", "SELL"))

    kpis = f"""
<div class="ir-kpi-row">
  {_kpi("Total Signals", str(total_sigs), "active this session")}
  {_kpi("High Conviction", str(hi_conv), "strong setup", C_POS)}
  {_kpi("Medium Conviction", str(med_conv), "developing", C_WARN)}
  {_kpi("Long Bias", str(long_ct), "directional long signals", C_POS)}
  {_kpi("Short Bias", str(short_ct), "directional short signals", C_NEG)}
</div>"""

    # Signal table
    def _signal_row(s: Any, i: int) -> str:
        ticker   = _safe_str(_safe_attr(s, "ticker"))
        sig_type = _safe_str(_safe_attr(s, "signal_type", "type"), "—")
        direction= _safe_str(_safe_attr(s, "direction"), "—").upper()
        conviction=_safe_str(_safe_attr(s, "conviction"), "—").upper()
        strength = _safe_float(_safe_attr(s, "strength", "score"), 0.0)
        rationale= _safe_str(_safe_attr(s, "rationale", "description"), "—")

        dir_col  = _ACTION_COLORS.get(direction, C_NEUT)
        conv_col = _CONV_COLORS.get(conviction, C_NEUT)
        str_bar  = _score_bar(strength, conv_col)

        return f"""<tr>
  <td class="num" style="color:#718096;width:32px">{i}</td>
  <td style="font-weight:700;font-family:'SFMono-Regular','Courier New',monospace">{ticker}</td>
  <td><span style="font-size:11px;color:#4a5568">{sig_type}</span></td>
  <td>{_badge(direction, dir_col)}</td>
  <td>{_badge(conviction, conv_col)}</td>
  <td style="width:160px">{str_bar}</td>
  <td style="font-size:11.5px;color:#4a5568;max-width:300px">{rationale[:140]}{"…" if len(rationale) > 140 else ""}</td>
</tr>"""

    signal_rows = "".join(_signal_row(s, i + 1) for i, s in enumerate(signals[:50]))
    if not signal_rows:
        signal_rows = '<tr><td colspan="7" style="text-align:center;color:#718096;padding:24px">No signals available</td></tr>'

    # Top longs
    def _top_side_list(title: str, items: list, color: str) -> str:
        if not items:
            return f'<div class="ir-card"><div class="ir-card-title">{title}</div><p style="color:#718096;font-size:11px">No signals</p></div>'
        rows = ""
        for i, s in enumerate(items[:5]):
            ticker     = _safe_str(_safe_attr(s, "ticker"))
            conviction = _safe_str(_safe_attr(s, "conviction"), "—").upper()
            strength   = _safe_float(_safe_attr(s, "strength", "score"), 0.0)
            rationale  = _safe_str(_safe_attr(s, "rationale", "description"), "—")
            rows += f"""
<div style="padding:10px 0;border-bottom:1px solid #e9ecef">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
    <span style="font-weight:700;font-size:14px;font-family:'SFMono-Regular','Courier New',monospace;color:{color}">{ticker}</span>
    {_badge(conviction, _CONV_COLORS.get(conviction, C_NEUT))}
  </div>
  {_score_bar(strength, color)}
  <div style="font-size:11px;color:#4a5568;margin-top:4px">{rationale[:100]}{"…" if len(rationale) > 100 else ""}</div>
</div>"""
        return f'<div class="ir-card"><div class="ir-card-title">{title}</div>{rows}</div>'

    # Signal type breakdown
    type_rows = ""
    for sig_type, cnt in sorted(cnt_type.items(), key=lambda x: -x[1]):
        pct = cnt / total_sigs * 100 if total_sigs else 0
        bar = _score_bar(pct / 100, C_NAVY)
        type_rows += f"""<tr>
  <td>{sig_type}</td>
  <td style="width:160px">{bar}</td>
  <td class="num">{cnt}</td>
  <td class="num">{pct:.0f}%</td>
</tr>"""

    def _paras(text: str) -> str:
        if not text:
            return ""
        parts = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not parts:
            parts = [text.strip()]
        return "".join(f"<p>{p}</p>" for p in parts)

    opp_html = ""
    if opp_narrative:
        opp_html = f'<div class="ir-narrative" style="margin-bottom:20px">{_paras(opp_narrative)}</div>'

    return f"""
<section class="ir-section" id="s2">
  {_section_head(2, "Alpha Signals", "Quantitative signal framework — current session")}
  {kpis}
  {opp_html}

  <div class="ir-two-col" style="margin-bottom:24px">
    {_top_side_list("Top Long Ideas", top_long, C_POS)}
    {_top_side_list("Top Short Ideas", top_short, C_NEG)}
  </div>

  <div style="margin-bottom:16px;font-size:12px;font-weight:700;color:#0f285a;text-transform:uppercase;letter-spacing:.08em">
    Full Signal Table
  </div>
  <div class="ir-table-wrap">
    <table class="ir-table">
      <thead><tr>
        <th>#</th>
        <th>Ticker</th>
        <th>Signal Type</th>
        <th>Direction</th>
        <th>Conviction</th>
        <th>Strength</th>
        <th>Rationale</th>
      </tr></thead>
      <tbody>{signal_rows}</tbody>
    </table>
  </div>

  {"<div class='ir-card' style='margin-top:24px'><div class='ir-card-title'>Signal Type Distribution</div><div class='ir-table-wrap' style='border:none;margin:0'><table class='ir-table' style='border:none'><thead><tr><th>Type</th><th>Distribution</th><th class='right'>Count</th><th class='right'>Share</th></tr></thead><tbody>" + type_rows + "</tbody></table></div></div>" if type_rows else ""}
</section>"""


# ---------------------------------------------------------------------------
# Section 3 — Freight Rates
# ---------------------------------------------------------------------------

def _section_freight_rates(report) -> str:
    freight = report.freight
    routes        = _safe_list(_safe_attr(freight, "routes"))
    avg_chg       = _safe_float(_safe_attr(freight, "avg_change_30d_pct"), 0.0)
    biggest_mover = _safe_dict(_safe_attr(freight, "biggest_mover"))
    momentum_lbl  = _safe_str(_safe_attr(freight, "momentum_label"), "Stable")
    fbx           = _safe_float(_safe_attr(freight, "fbx_composite"), 0.0)

    mom_color = C_POS if momentum_lbl.lower() == "accelerating" else (C_NEG if momentum_lbl.lower() == "decelerating" else C_NEUT)

    bm_route = _safe_str(_safe_attr(biggest_mover, "route_id", "route"), "—")
    bm_chg   = _safe_float(_safe_attr(biggest_mover, "change_pct", "change_30d_pct"), 0.0)
    bm_rate  = _safe_float(_safe_attr(biggest_mover, "rate"), 0.0)

    kpis = f"""
<div class="ir-kpi-row">
  {_kpi("Fleet Avg 30d Change", _fmt_pct(avg_chg), "blended across all routes", _pct_color(avg_chg))}
  {_kpi("FBX Composite", _fmt_num(fbx, 0) if fbx else "—", "Freightos Baltic Index")}
  {_kpi("Momentum", momentum_lbl, "current freight trend", mom_color)}
  {_kpi("Biggest Mover", bm_route, f"{_fmt_pct(bm_chg)} / {_fmt_price(bm_rate)}", _pct_color(bm_chg))}
  {_kpi("Routes Tracked", str(len(routes)), "in coverage universe")}
</div>"""

    # Route rows
    def _route_row(r: Any) -> str:
        route_id  = _safe_str(_safe_attr(r, "route_id", "route"))
        rate      = _safe_float(_safe_attr(r, "rate"), 0.0)
        chg_30d   = _safe_float(_safe_attr(r, "change_30d"), 0.0)
        chg_pct   = _safe_float(_safe_attr(r, "change_pct", "change_30d_pct"), 0.0)
        trend     = _safe_str(_safe_attr(r, "trend"), "—")
        label     = _safe_str(_safe_attr(r, "label"), "")

        trend_col = C_POS if trend.upper() in ("UP", "RISING", "BULLISH") else (C_NEG if trend.upper() in ("DOWN", "FALLING", "BEARISH") else C_NEUT)
        arrow_t   = "&#9650;" if trend.upper() in ("UP", "RISING") else ("&#9660;" if trend.upper() in ("DOWN", "FALLING") else "&#9654;")

        return f"""<tr>
  <td style="font-weight:600;font-family:'SFMono-Regular','Courier New',monospace">{route_id}</td>
  <td style="color:#4a5568;font-size:11.5px">{label}</td>
  <td class="num">{_fmt_price(rate) if rate else "—"}</td>
  <td class="num">{_fmt_price(chg_30d) if chg_30d else "—"}</td>
  {_pct_cell(chg_pct)}
  <td class="center"><span style="color:{trend_col};font-weight:700">{arrow_t} {trend}</span></td>
</tr>"""

    route_rows = "".join(_route_row(r) for r in routes)
    if not route_rows:
        route_rows = '<tr><td colspan="6" style="text-align:center;color:#718096;padding:24px">No freight data available</td></tr>'

    return f"""
<section class="ir-section" id="s3">
  {_section_head(3, "Freight Rates", "Route-level rate analysis — 30-day momentum")}
  {kpis}

  <div class="ir-table-wrap">
    <table class="ir-table">
      <thead><tr>
        <th>Route ID</th>
        <th>Description</th>
        <th class="right">Rate ($/FEU)</th>
        <th class="right">30d Change ($)</th>
        <th class="right">30d Change (%)</th>
        <th class="center">Trend</th>
      </tr></thead>
      <tbody>{route_rows}</tbody>
    </table>
  </div>
</section>"""


# ---------------------------------------------------------------------------
# Section 4 — Macroeconomic
# ---------------------------------------------------------------------------

def _section_macroeconomic(report) -> str:
    macro = report.macro
    bdi       = _safe_float(_safe_attr(macro, "bdi"), 0.0)
    bdi_chg   = _safe_float(_safe_attr(macro, "bdi_change_30d_pct"), 0.0)
    wti       = _safe_float(_safe_attr(macro, "wti"), 0.0)
    wti_chg   = _safe_float(_safe_attr(macro, "wti_change_30d_pct"), 0.0)
    tsy10     = _safe_float(_safe_attr(macro, "treasury_10y"), 0.0)
    dxy       = _safe_float(_safe_attr(macro, "dxy_proxy"), 0.0)
    pmi       = _safe_float(_safe_attr(macro, "pmi_proxy"), 0.0)
    sc_stress = _safe_str(_safe_attr(macro, "supply_chain_stress"), "MODERATE")

    stress_col = _RISK_COLORS.get(sc_stress.upper(), C_NEUT)

    indicators = [
        ("Baltic Dry Index",    bdi,    bdi_chg,  "pts",       "Shipping demand barometer; rises w/ cargo volumes"),
        ("WTI Crude Oil",       wti,    wti_chg,  "$/bbl",     "Bunker fuel proxy; cost pressure indicator"),
        ("10Y US Treasury",     tsy10,  None,     "%",         "Risk-free rate; discount rate for equities"),
        ("DXY Proxy (USD/CNY)", dxy,    None,     "",          "Dollar strength indicator; inverse shipping headwind"),
        ("Industrial Prod.",    pmi,    None,     "index",     "Manufacturing activity proxy; cargo demand leading indicator"),
    ]

    def _fmt_val(v: float, unit: str) -> str:
        if not v:
            return "—"
        if unit == "$/bbl":
            return _fmt_price(v)
        if unit == "%":
            return f"{v:.2f}%"
        if unit == "pts":
            return f"{v:,.0f}"
        return f"{v:,.2f}"

    ind_rows = ""
    for name, val, chg, unit, desc in indicators:
        val_str = _fmt_val(val, unit)
        chg_html = _change_span(chg) if chg is not None else '<span style="color:#718096">—</span>'
        # Signal: is this indicator supportive or headwind for shipping?
        if name == "Baltic Dry Index":
            outlook = "Supportive" if val > 1500 else ("Neutral" if val > 900 else "Headwind")
            out_col = C_POS if outlook == "Supportive" else (C_NEUT if outlook == "Neutral" else C_NEG)
        elif name == "WTI Crude Oil":
            outlook = "Cost Pressure" if wti > 90 else ("Moderate" if wti > 70 else "Supportive")
            out_col = C_NEG if outlook == "Cost Pressure" else (C_NEUT if outlook == "Moderate" else C_POS)
        elif name == "10Y US Treasury":
            outlook = "Headwind" if tsy10 > 4.5 else ("Neutral" if tsy10 > 3.5 else "Supportive")
            out_col = C_NEG if outlook == "Headwind" else (C_NEUT if outlook == "Neutral" else C_POS)
        else:
            outlook = "—"
            out_col = C_NEUT

        ind_rows += f"""<tr>
  <td style="font-weight:600">{name}</td>
  <td class="num" style="font-size:14px;font-weight:700">{val_str}</td>
  <td class="right">{chg_html}</td>
  <td class="center">{"<span style='color:" + out_col + ";font-weight:600;font-size:11px'>" + outlook + "</span>" if outlook != "—" else "<span style='color:#718096'>—</span>"}</td>
  <td style="font-size:11px;color:#4a5568">{desc}</td>
</tr>"""

    kpis = f"""
<div class="ir-kpi-row">
  {_kpi("Baltic Dry Index", f"{bdi:,.0f}" if bdi else "—", f"{_fmt_pct(bdi_chg)} 30d", _pct_color(bdi_chg))}
  {_kpi("WTI Crude", _fmt_price(wti) if wti else "—", f"{_fmt_pct(wti_chg)} 30d", _pct_color(wti_chg))}
  {_kpi("10Y Treasury", f"{tsy10:.2f}%" if tsy10 else "—", "yield")}
  {_kpi("USD/CNY", f"{dxy:.4f}" if dxy else "—", "DXY proxy")}
  {_kpi("Supply Chain Stress", sc_stress, "composite assessment", stress_col)}
</div>"""

    # Stress box
    stress_interpretations = {
        "LOW":      ("Supply chains operating smoothly. Freight availability high, rates stable. "
                     "Low BDI volatility. No material port congestion signals."),
        "MODERATE": ("Some supply chain friction evident. Mixed signals across freight corridors. "
                     "Monitor key choke points. Rate volatility elevated."),
        "HIGH":     ("Elevated supply chain stress. Significant disruption risk in at least one major corridor. "
                     "Rate spikes possible. Recommend defensive positioning or hedging."),
        "CRITICAL": ("Critical supply chain conditions. Major disruption active. "
                     "Immediate action warranted. Surcharge risk, capacity shortages confirmed."),
    }
    stress_text = stress_interpretations.get(sc_stress.upper(),
        "Supply chain stress level could not be determined from available data.")

    return f"""
<section class="ir-section" id="s4">
  {_section_head(4, "Macroeconomic Environment", "Key indicators and their impact on shipping markets")}
  {kpis}

  <div class="ir-table-wrap" style="margin-bottom:24px">
    <table class="ir-table">
      <thead><tr>
        <th>Indicator</th>
        <th class="right">Current Value</th>
        <th class="right">30d Change</th>
        <th class="center">Shipping Outlook</th>
        <th>Commentary</th>
      </tr></thead>
      <tbody>{ind_rows}</tbody>
    </table>
  </div>

  <div class="ir-card" style="border-left:5px solid {stress_col}">
    <div class="ir-card-title">Supply Chain Stress Assessment</div>
    <div style="display:flex;align-items:center;gap:20px;margin-bottom:12px">
      <div style="font-size:28px;font-weight:900;color:{stress_col};font-family:'SFMono-Regular','Courier New',monospace">{sc_stress}</div>
      {_badge(sc_stress + " STRESS", stress_col)}
    </div>
    <p style="font-size:12.5px;color:#4a5568;line-height:1.7">{stress_text}</p>
  </div>
</section>"""


# ---------------------------------------------------------------------------
# Section 5 — Equity Coverage
# ---------------------------------------------------------------------------

def _section_equities(report) -> str:
    stocks = report.stocks
    tickers         = _safe_list(_safe_attr(stocks, "tickers"))
    prices          = _safe_dict(_safe_attr(stocks, "prices"))
    changes_30d     = _safe_dict(_safe_attr(stocks, "changes_30d"))
    signals_by_tkr  = _safe_dict(_safe_attr(stocks, "signals_by_ticker"))
    top_pick        = _safe_str(_safe_attr(stocks, "top_pick"), "—")
    top_pick_rat    = _safe_str(_safe_attr(stocks, "top_pick_rationale"), "—")

    _TICKER_NAMES = {
        "ZIM":  "ZIM Integrated Shipping Services",
        "MATX": "Matson Inc.",
        "SBLK": "Star Bulk Carriers",
        "DAC":  "Danaos Corporation",
        "CMRE": "Costamare Inc.",
    }

    if not tickers:
        tickers = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]

    def _equity_row(tkr: str) -> str:
        price   = _safe_float(prices.get(tkr), 0.0)
        chg     = _safe_float(changes_30d.get(tkr), 0.0)
        sigs    = _safe_list(signals_by_tkr.get(tkr, []))
        name    = _TICKER_NAMES.get(tkr, tkr)
        is_pick = tkr == top_pick

        # Derive rating from signals
        long_sigs  = [s for s in sigs if _safe_str(_safe_attr(s, "direction")).upper() in ("LONG", "BUY")]
        short_sigs = [s for s in sigs if _safe_str(_safe_attr(s, "direction")).upper() in ("SHORT", "SELL")]
        if long_sigs:
            rating     = "BUY"
            rat_col    = C_POS
            conviction = _safe_str(_safe_attr(long_sigs[0], "conviction"), "MEDIUM").upper()
        elif short_sigs:
            rating  = "SELL"
            rat_col = C_NEG
            conviction = _safe_str(_safe_attr(short_sigs[0], "conviction"), "MEDIUM").upper()
        else:
            rating  = "HOLD"
            rat_col = C_WARN
            conviction = "LOW"

        sig_count = len(sigs)
        pick_star = " &#9733;" if is_pick else ""

        return f"""<tr{"" if not is_pick else " style='background:#eff6ff'"}>
  <td style="font-weight:800;font-family:'SFMono-Regular','Courier New',monospace;font-size:14px">{tkr}{pick_star}</td>
  <td style="font-size:11.5px;color:#4a5568">{name}</td>
  <td class="num" style="font-size:14px;font-weight:700">{_fmt_price(price) if price else "—"}</td>
  {_pct_cell(chg)}
  <td class="center">{_badge(rating, rat_col)}</td>
  <td class="center">{_badge(conviction, _CONV_COLORS.get(conviction, C_NEUT))}</td>
  <td class="num">{sig_count}</td>
</tr>"""

    equity_rows = "".join(_equity_row(t) for t in tickers)

    return f"""
<section class="ir-section" id="s5">
  {_section_head(5, "Equity Coverage", "Shipping equity universe — ratings, prices, and signal summary")}

  <div class="ir-kpi-row">
    {_kpi("Top Pick", top_pick, "highest conviction", C_POS)}
    {_kpi("Universe Size", str(len(tickers)), "tickers in coverage")}
    {_kpi("Rated Buy", str(sum(1 for t in tickers if _safe_list(signals_by_tkr.get(t,[])) and _safe_str(_safe_attr(_safe_list(signals_by_tkr.get(t,[]))[0], "direction")).upper() in ("LONG","BUY"))), "active buy ratings", C_POS)}
  </div>

  <div class="ir-table-wrap">
    <table class="ir-table">
      <thead><tr>
        <th>Ticker</th>
        <th>Company</th>
        <th class="right">Price</th>
        <th class="right">30d Return</th>
        <th class="center">Rating</th>
        <th class="center">Conviction</th>
        <th class="right">Signals</th>
      </tr></thead>
      <tbody>{equity_rows}</tbody>
    </table>
  </div>

  {"<div class='ir-card' style='margin-top:20px;border-left:5px solid " + C_POS + "'><div class='ir-card-title'>Top Pick Rationale: " + top_pick + "</div><p style='font-size:12.5px;color:#4a5568;line-height:1.7'>" + top_pick_rat + "</p></div>" if top_pick_rat and top_pick_rat != "—" else ""}
</section>"""


# ---------------------------------------------------------------------------
# Section 6 — Market Intel
# ---------------------------------------------------------------------------

def _section_market_intel(report) -> str:
    market     = report.market
    news_items = _safe_list(_safe_attr(report, "news_items"))

    top_insights = _safe_list(_safe_attr(market, "top_insights"))
    top_ports    = _safe_list(_safe_attr(market, "top_ports"))
    top_routes   = _safe_list(_safe_attr(market, "top_routes"))
    risk_level   = _safe_str(_safe_attr(market, "risk_level"), "MODERATE")
    active_opp   = int(_safe_float(_safe_attr(market, "active_opportunities"), 0))
    hi_conv_ct   = int(_safe_float(_safe_attr(market, "high_conviction_count"), 0))

    risk_col = _RISK_COLORS.get(risk_level.upper(), C_NEUT)

    kpis = f"""
<div class="ir-kpi-row">
  {_kpi("Overall Risk Level", risk_level, "market risk assessment", risk_col)}
  {_kpi("Active Opportunities", str(active_opp), "actionable insights")}
  {_kpi("High-Conviction Insights", str(hi_conv_ct), "score ≥ 0.70")}
  {_kpi("Top Ports Coverage", str(len(top_ports)), "ports in focus")}
  {_kpi("Top Routes Coverage", str(len(top_routes)), "routes in focus")}
</div>"""

    # Insights table
    def _insight_row(ins: Any) -> str:
        title  = _safe_str(_safe_attr(ins, "title", "description", "insight"))
        score  = _safe_float(_safe_attr(ins, "score", "conviction_score"), 0.0)
        action = _safe_str(_safe_attr(ins, "action", "recommended_action"), "Monitor")
        cat    = _safe_str(_safe_attr(ins, "category", "type"), "—")
        act_col= _ACTION_COLORS.get(action.upper().split()[0], C_NEUT)
        return f"""<tr>
  <td style="font-size:12px;color:#1a1a2e">{title}</td>
  <td class="center">{_badge(cat, C_NAVY_MID)}</td>
  <td style="width:120px">{_score_bar(score, C_NAVY)}</td>
  <td class="center">{_badge(action, act_col)}</td>
</tr>"""

    insight_rows = "".join(_insight_row(i) for i in top_insights[:10])
    if not insight_rows:
        insight_rows = '<tr><td colspan="4" style="text-align:center;color:#718096;padding:24px">No insights available</td></tr>'

    # News feed
    def _news_item_html(item: Any) -> str:
        headline = _safe_str(_safe_attr(item, "headline", "title"))
        source   = _safe_str(_safe_attr(item, "source"), "—")
        score    = _safe_float(_safe_attr(item, "sentiment_score"), 0.0)
        pub      = _safe_str(_safe_attr(item, "published_at"), "")

        if pub:
            try:
                dt  = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
                pub = dt.strftime("%b %d, %H:%M")
            except Exception:
                pub = str(pub)[:16]

        score_col  = C_POS if score > 0.15 else (C_NEG if score < -0.15 else C_NEUT)
        score_bg   = C_POS_BG if score > 0.15 else (C_NEG_BG if score < -0.15 else C_SURFACE2)
        score_disp = f"{score:+.2f}"

        return f"""
<div class="ir-news-item">
  <div class="ir-news-score" style="background:{score_bg};color:{score_col}">{score_disp}</div>
  <div style="flex:1">
    <div class="ir-news-headline">{headline}</div>
    <div class="ir-news-meta">{source}{"&ensp;&bull;&ensp;" + pub if pub else ""}</div>
  </div>
</div>"""

    news_html = "".join(_news_item_html(n) for n in news_items[:15])
    if not news_html:
        news_html = '<p style="color:#718096;font-size:11px;padding:12px 0">No news items available</p>'

    # Port spotlight
    def _port_row(p: Any) -> str:
        name   = _safe_str(_safe_attr(p, "port_name", "port", "name"))
        score  = _safe_float(_safe_attr(p, "demand_score", "score"), 0.0)
        region = _safe_str(_safe_attr(p, "region"), "—")
        return f"""<tr>
  <td style="font-weight:600">{name}</td>
  <td style="color:#4a5568;font-size:11.5px">{region}</td>
  <td style="width:120px">{_score_bar(score, C_NAVY_MID)}</td>
</tr>"""

    port_rows = "".join(_port_row(p) for p in top_ports[:6])

    return f"""
<section class="ir-section" id="s6">
  {_section_head(6, "Market Intelligence", "Port demand, route insights, and news sentiment feed")}
  {kpis}

  <div class="ir-table-wrap" style="margin-bottom:24px">
    <table class="ir-table">
      <thead><tr>
        <th>Insight</th>
        <th class="center">Category</th>
        <th>Conviction</th>
        <th class="center">Action</th>
      </tr></thead>
      <tbody>{insight_rows}</tbody>
    </table>
  </div>

  <div class="ir-two-col">
    <div class="ir-card">
      <div class="ir-card-title">News Sentiment Feed (Top 15)</div>
      {news_html}
    </div>
    <div>
      {"<div class='ir-card' style='margin-bottom:16px'><div class='ir-card-title'>Port Demand Spotlight</div><div class='ir-table-wrap' style='border:none;margin:0'><table class='ir-table' style='border:none'><thead><tr><th>Port</th><th>Region</th><th>Demand Score</th></tr></thead><tbody>" + port_rows + "</tbody></table></div></div>" if port_rows else ""}
    </div>
  </div>
</section>"""


# ---------------------------------------------------------------------------
# Section 7 — Risk & Scenarios
# ---------------------------------------------------------------------------

def _section_risk(report) -> str:
    market    = report.market
    ai        = report.ai
    macro     = report.macro

    risk_level    = _safe_str(_safe_attr(market, "risk_level"), "MODERATE")
    risk_narrative= _safe_str(_safe_attr(ai, "risk_narrative"), "")
    sc_stress     = _safe_str(_safe_attr(macro, "supply_chain_stress"), "MODERATE")
    bdi           = _safe_float(_safe_attr(macro, "bdi"), 0.0)
    wti           = _safe_float(_safe_attr(macro, "wti"), 0.0)
    tsy10         = _safe_float(_safe_attr(macro, "treasury_10y"), 0.0)

    risk_col = _RISK_COLORS.get(risk_level.upper(), C_NEUT)

    def _paras(text: str) -> str:
        if not text:
            return ""
        parts = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not parts:
            parts = [text.strip()]
        return "".join(f"<p>{p}</p>" for p in parts)

    # Risk factor table — synthetic from available macro
    risk_factors = []
    if bdi > 0:
        bdi_risk = "LOW" if bdi > 1500 else ("MODERATE" if bdi > 900 else "HIGH")
        risk_factors.append(("Baltic Dry Index Level", bdi_risk, f"BDI at {bdi:,.0f}. {'Demand solid.' if bdi > 1500 else 'Demand soft — overcapacity risk.' if bdi < 900 else 'Demand moderate.'}"))
    if wti > 0:
        wti_risk = "HIGH" if wti > 90 else ("MODERATE" if wti > 70 else "LOW")
        risk_factors.append(("Bunker Fuel / Oil Price", wti_risk, f"WTI at {_fmt_price(wti)}. {'Significant cost pressure on operating margins.' if wti > 90 else 'Elevated but manageable fuel cost.' if wti > 70 else 'Supportive fuel cost environment.'}"))
    if tsy10 > 0:
        tsy_risk = "HIGH" if tsy10 > 4.5 else ("MODERATE" if tsy10 > 3.5 else "LOW")
        risk_factors.append(("Interest Rate Environment", tsy_risk, f"10Y Treasury at {tsy10:.2f}%. {'High rates compress shipping equity multiples.' if tsy10 > 4.5 else 'Elevated rates; monitor refinancing risk.' if tsy10 > 3.5 else 'Supportive rate environment for capital-intensive shipping.'}"))

    risk_factors.append(("Supply Chain Stress", sc_stress, {
        "LOW":      "Supply chains operating smoothly. Low disruption probability.",
        "MODERATE": "Some friction and delays reported. Monitor key chokepoints.",
        "HIGH":     "Elevated disruption risk. Active bottlenecks in at least one corridor.",
        "CRITICAL": "Critical disruptions active. Immediate portfolio impact likely.",
    }.get(sc_stress.upper(), "")))
    risk_factors.append(("Geopolitical Risk", "MODERATE", "Ongoing monitoring of key maritime corridors. Rerouting risk in select chokepoints."))
    risk_factors.append(("Vessel Oversupply", "LOW" if bdi > 1200 else "MODERATE", "Orderbook levels within historical norms. No immediate capacity glut expected."))

    def _risk_row(name: str, level: str, desc: str) -> str:
        col  = _RISK_COLORS.get(level.upper(), C_NEUT)
        bg   = C_NEG_BG if level.upper() in ("HIGH", "CRITICAL") else (C_WARN_BG if level.upper() == "MODERATE" else "transparent")
        return f"""<tr class="ir-risk-row-{level.upper()}" style="background:{bg}">
  <td style="font-weight:600">{name}</td>
  <td class="center">{_badge(level, col)}</td>
  <td style="font-size:11.5px;color:#4a5568">{desc}</td>
</tr>"""

    risk_rows = "".join(_risk_row(n, l, d) for n, l, d in risk_factors)

    # Scenario analysis
    scenarios = [
        ("Bull Case",  C_POS,  "BDI sustains above 1,800. WTI corrects toward $70. Supply chains normalize. Freight rates stabilize at elevated levels. Shipping equities re-rate higher driven by yield compression and strong earnings."),
        ("Base Case",  C_NEUT, "BDI oscillates 1,000–1,600. Rates stable with seasonal variation. No major supply chain disruptions. Equities trade near book value with modest dividend support."),
        ("Bear Case",  C_NEG,  "BDI breaks below 900. WTI spikes above $95 on supply shock. New vessel deliveries exceed demand absorption. Rate collapse across all corridors. Equity multiple compression."),
    ]

    scenario_html = ""
    for title, col, desc in scenarios:
        scenario_html += f"""
<div style="border:1px solid #d0d7de;border-left:5px solid {col};border-radius:4px;padding:16px 18px;background:#ffffff">
  <div style="font-weight:700;font-size:13px;color:{col};margin-bottom:8px;text-transform:uppercase;letter-spacing:.06em">{title}</div>
  <p style="font-size:12px;color:#4a5568;line-height:1.7;margin:0">{desc}</p>
</div>"""

    return f"""
<section class="ir-section" id="s7">
  {_section_head(7, "Risk & Scenario Analysis", "Current risk matrix and forward scenario framework")}

  <div class="ir-kpi-row">
    {_kpi("Overall Risk Level", risk_level, "composite market risk", risk_col)}
    {_kpi("Supply Chain", sc_stress, "chain stress level", _RISK_COLORS.get(sc_stress.upper(), C_NEUT))}
  </div>

  {"<div class='ir-narrative' style='margin-bottom:20px'>" + _paras(risk_narrative) + "</div>" if risk_narrative else ""}

  <div class="ir-table-wrap" style="margin-bottom:24px">
    <table class="ir-table">
      <thead><tr>
        <th>Risk Factor</th>
        <th class="center">Level</th>
        <th>Assessment</th>
      </tr></thead>
      <tbody>{risk_rows}</tbody>
    </table>
  </div>

  <div style="font-size:12px;font-weight:700;color:#0f285a;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px">
    Scenario Analysis — 30-Day Outlook
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px">
    {scenario_html}
  </div>
</section>"""


# ---------------------------------------------------------------------------
# Section 8 — Recommendations
# ---------------------------------------------------------------------------

def _section_recommendations(report) -> str:
    ai = report.ai
    recs     = _safe_list(_safe_attr(ai, "top_recommendations"))
    outlook  = _safe_str(_safe_attr(ai, "outlook_30d"), "")

    def _paras(text: str) -> str:
        if not text:
            return ""
        parts = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not parts:
            parts = [text.strip()]
        return "".join(f"<p>{p}</p>" for p in parts)

    rec_html = ""
    for rec in recs[:9]:
        rank       = int(_safe_float(_safe_attr(rec, "rank"), 0))
        title      = _safe_str(_safe_attr(rec, "title"))
        action     = _safe_str(_safe_attr(rec, "action"), "MONITOR").upper()
        ticker     = _safe_str(_safe_attr(rec, "ticker"), "")
        conviction = _safe_str(_safe_attr(rec, "conviction"), "MEDIUM").upper()
        rationale  = _safe_str(_safe_attr(rec, "rationale", "description", "body"), "")
        horizon    = _safe_str(_safe_attr(rec, "horizon", "time_horizon"), "30d")

        act_col  = _ACTION_COLORS.get(action.split()[0], C_NAVY)
        conv_col = _CONV_COLORS.get(conviction, C_NEUT)

        ticker_html = f"&ensp;{_badge(ticker, C_NAVY_MID)}" if ticker else ""
        rec_html += f"""
<div class="ir-rec-box" style="border-left-color:{act_col}">
  <div class="ir-rec-rank">{rank:02d}</div>
  <div class="ir-rec-title">{title}</div>
  <div style="margin-bottom:8px;display:flex;align-items:center;flex-wrap:wrap;gap:6px">
    {_badge(action, act_col)}{ticker_html}&ensp;{_badge(conviction, conv_col)}&ensp;<span style="font-size:10px;color:#718096">Horizon: {horizon}</span>
  </div>
  <div class="ir-rec-body">{rationale}</div>
</div>"""

    if not rec_html:
        rec_html = '<p style="color:#718096;font-size:12px">No recommendations available for this session.</p>'

    return f"""
<section class="ir-section" id="s8">
  {_section_head(8, "Recommendations", "Priority-ranked actionable recommendations for the current session")}

  {"<div class='ir-card' style='margin-bottom:24px'><div class='ir-card-title'>30-Day Outlook</div><div class='ir-narrative'>" + _paras(outlook) + "</div></div>" if outlook else ""}

  <div class="ir-rec-grid">{rec_html}</div>
</section>"""


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

def _footer(report) -> str:
    disclaimer = _safe_str(_safe_attr(report.ai, "disclaimer"), "")
    gen_at     = _safe_str(_safe_attr(report, "generated_at"), "")

    if not disclaimer:
        disclaimer = (
            "This report is produced by ShipIntel Research using rule-based quantitative analysis "
            "and publicly available data. It is intended for institutional distribution only. "
            "Nothing in this report constitutes investment advice or a solicitation to buy or sell "
            "any security. Past performance is not indicative of future results. All data subject "
            "to revision. Freight rates are indicative only and do not represent firm quotes. "
            "Equity ratings are model-generated and do not reflect a registered investment adviser's opinion."
        )

    sources = [
        "Baltic Exchange — BDI and route rate data",
        "Freightos — FBX composite index",
        "FRED / Federal Reserve — WTI, 10Y Treasury, Industrial Production, USD/CNY",
        "Proprietary alpha engine — signal generation and conviction scoring",
        "Multi-source news aggregation — sentiment scoring",
    ]
    source_list = "".join(f"<li>{s}</li>" for s in sources)

    try:
        gen_display = datetime.fromisoformat(gen_at.replace("Z", "+00:00")).strftime("%B %d, %Y at %H:%M UTC")
    except Exception:
        gen_display = gen_at or "—"

    return f"""
<footer class="ir-footer">
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:32px;margin-bottom:16px">
    <div>
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.12em;color:#c8d8ea;margin-bottom:10px">Data Sources</div>
      <ul style="list-style:disc;padding-left:18px;color:#a8bcd4;font-size:10.5px;line-height:1.8">{source_list}</ul>
    </div>
    <div>
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.12em;color:#c8d8ea;margin-bottom:10px">Report Information</div>
      <div style="color:#a8bcd4;font-size:10.5px;line-height:1.9">
        <div>Generated: {gen_display}</div>
        <div>Distribution: Institutional Only</div>
        <div>Classification: Confidential</div>
        <div>Engine: ShipIntel Research Platform v2</div>
      </div>
    </div>
  </div>
  <div class="ir-footer-disclaimer">
    <strong style="color:#a8bcd4">Disclaimer:</strong> {disclaimer}
  </div>
</footer>"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render_investor_report_html(report) -> str:
    """Return a complete self-contained HTML document as a string.

    Args:
        report: InvestorReport dataclass instance from investor_report_engine.py.
                Also accepts any object with equivalent attributes.

    Returns:
        UTF-8 compatible HTML string. No external dependencies.
    """
    report_date = _safe_str(_safe_attr(report, "report_date"), "—")
    gen_at      = _safe_str(_safe_attr(report, "generated_at"), "")

    try:
        gen_dt = datetime.fromisoformat(gen_at.replace("Z", "+00:00"))
        gen_display = gen_dt.strftime("%B %d, %Y")
    except Exception:
        gen_display = report_date

    css = _build_css()

    body_parts = [
        _topbar(gen_display),
        _cover(report),
        _nav(),
        _section_executive_summary(report),
        _section_alpha_signals(report),
        _section_freight_rates(report),
        _section_macroeconomic(report),
        _section_equities(report),
        _section_market_intel(report),
        _section_risk(report),
        _section_recommendations(report),
        _footer(report),
    ]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="ShipIntel Research — Institutional Shipping Market Report">
  <title>ShipIntel Research | Global Shipping Report | {gen_display}</title>
  <style>
{css}
  </style>
</head>
<body>
{"".join(body_parts)}
</body>
</html>"""

    return html
