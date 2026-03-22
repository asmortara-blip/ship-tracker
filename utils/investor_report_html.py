"""investor_report_html.py — Institutional-grade HTML investor report builder.

Produces a fully self-contained HTML document from an InvestorReport object.
No external dependencies except an optional Plotly CDN reference (currently
unused — all charts are pure CSS/SVG for offline compatibility).

Usage:
    from utils.investor_report_html import render_investor_report_html
    html = render_investor_report_html(report)
    bytes_out = html.encode("utf-8")  # for download
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── Engine schema imports ─────────────────────────────────────────────────────
try:
    from processing.investor_report_engine import (
        InvestorReport,
        SentimentBreakdown,
        AlphaSignalSummary,
        MarketIntelligenceSummary,
        FreightRateSummary,
        MacroSnapshot,
        StockAnalysis,
        AIAnalysis,
    )
    _ENGINE_SCHEMA_OK = True
except Exception:
    _ENGINE_SCHEMA_OK = False
    # Provide a minimal stub so the module loads even without the engine
    InvestorReport = None  # type: ignore[misc,assignment]


# ── Color palette — Goldman/institutional dark theme ──────────────────────────
C_BG        = "#0D1B2A"    # deep navy
C_SURFACE   = "#132237"    # section bg
C_CARD      = "#1A2E45"    # card bg
C_BORDER    = "#1E3A5F"    # subtle borders
C_GOLD      = "#C9A84C"    # primary gold accent
C_STEEL     = "#2E86C1"    # steel blue
C_TEAL      = "#1ABC9C"    # bullish/positive
C_CRIMSON   = "#E74C3C"    # bearish/negative
C_AMBER     = "#F39C12"    # neutral/caution
C_PURPLE    = "#9B59B6"    # convergence
C_TEXT      = "#ECF0F1"    # primary text
C_TEXT2     = "#95A5A6"    # secondary text
C_TEXT3     = "#6C7A89"    # muted text

# Aliases kept for internal helper compatibility
C_HIGH   = C_TEAL
C_LOW    = C_CRIMSON
C_MOD    = C_AMBER
C_ACCENT = C_STEEL
C_CONV   = C_PURPLE
C_MACRO  = C_STEEL

_SENTIMENT_COLORS = {
    "BULLISH": C_TEAL,
    "BEARISH": C_CRIMSON,
    "NEUTRAL": C_TEXT2,
    "MIXED":   C_AMBER,
}

_CONVICTION_COLORS = {
    "HIGH":   C_TEAL,
    "MEDIUM": C_AMBER,
    "LOW":    C_TEXT2,
}

_ACTION_COLORS = {
    "BUY":     C_TEAL,
    "LONG":    C_TEAL,
    "SELL":    C_CRIMSON,
    "SHORT":   C_CRIMSON,
    "HOLD":    C_AMBER,
    "MONITOR": C_STEEL,
    "AVOID":   C_CRIMSON,
    "WATCH":   C_TEXT2,
}

_RISK_COLORS = {
    "LOW":      C_TEAL,
    "MODERATE": C_AMBER,
    "HIGH":     C_CRIMSON,
    "CRITICAL": "#C0392B",
}

_CATEGORY_COLORS = {
    "CONVERGENCE": C_PURPLE,
    "ROUTE":       C_STEEL,
    "PORT_DEMAND": C_TEAL,
    "MACRO":       C_STEEL,
}

_TICKERS = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]

_TICKER_NAMES = {
    "ZIM":  "ZIM Integrated Shipping Services",
    "MATX": "Matson Inc.",
    "SBLK": "Star Bulk Carriers",
    "DAC":  "Danaos Corporation",
    "CMRE": "Costamare Inc.",
}


# ── Small pure helpers ────────────────────────────────────────────────────────

def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _color_for_change(pct: float) -> str:
    if pct > 0.5:
        return C_TEAL
    if pct < -0.5:
        return C_CRIMSON
    return C_AMBER


def _format_pct(val: float) -> str:
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.1f}%"


def _format_price(val: float) -> str:
    if val >= 1000:
        return f"${val:,.0f}"
    return f"${val:.2f}"


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
            val = obj[attr] if isinstance(obj, dict) else getattr(obj, attr, None)
            if val is not None:
                return val
        except (KeyError, TypeError):
            pass
    return default


# ── SVG / CSS visual helpers ─────────────────────────────────────────────────

def _score_bar_svg(score: float, color: str, width: int = 200, height: int = 8) -> str:
    """Inline SVG progress bar for a [0, 1] score."""
    score = max(0.0, min(1.0, score))
    fill_w = max(2, int(score * width))
    pct_label = f"{score * 100:.0f}%"
    track_color = _hex_to_rgba(C_BORDER, 0.6)
    return (
        f'<div style="display:inline-flex;align-items:center;gap:8px">'
        f'<svg width="{width}" height="{height}" style="flex-shrink:0">'
        f'<rect width="{width}" height="{height}" rx="{height//2}" fill="{track_color}"/>'
        f'<rect width="{fill_w}" height="{height}" rx="{height//2}" fill="{color}"/>'
        f'</svg>'
        f'<span style="font-size:0.73rem;color:{C_TEXT2};font-weight:600;'
        f'font-family:\'JetBrains Mono\',\'Courier New\',monospace">{pct_label}</span>'
        f'</div>'
    )


def _sentiment_gauge_svg(score: float) -> str:
    """SVG semicircle gauge (-1 to +1). Needle rotates with score. Larger, centered."""
    score = max(-1.0, min(1.0, score))
    # Map -1..+1 to 0..180 degrees (left to right across top semicircle)
    angle_deg = (score + 1.0) / 2.0 * 180.0
    cx, cy, r = 130, 130, 100
    rad = math.radians(180.0 - angle_deg)
    nx = cx + r * math.cos(rad)
    ny = cy - r * math.sin(rad)

    if score >= 0.25:
        needle_col = C_TEAL
    elif score <= -0.25:
        needle_col = C_CRIMSON
    else:
        needle_col = C_AMBER

    return f"""<svg width="260" height="150" viewBox="0 0 260 150" xmlns="http://www.w3.org/2000/svg"
     style="display:block;margin:0 auto">
  <defs>
    <linearGradient id="gaugeGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%"   stop-color="{C_CRIMSON}"/>
      <stop offset="40%"  stop-color="{C_AMBER}"/>
      <stop offset="100%" stop-color="{C_TEAL}"/>
    </linearGradient>
  </defs>
  <!-- Track arc -->
  <path d="M 30 130 A 100 100 0 0 1 230 130"
        fill="none" stroke="{_hex_to_rgba(C_BORDER, 0.8)}" stroke-width="18"
        stroke-linecap="round"/>
  <!-- Colored arc -->
  <path d="M 30 130 A 100 100 0 0 1 230 130"
        fill="none" stroke="url(#gaugeGrad)" stroke-width="14"
        stroke-linecap="round" opacity="0.75"/>
  <!-- Needle -->
  <line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}"
        stroke="{needle_col}" stroke-width="3.5" stroke-linecap="round"/>
  <!-- Pivot -->
  <circle cx="{cx}" cy="{cy}" r="7" fill="{needle_col}"/>
  <circle cx="{cx}" cy="{cy}" r="3" fill="{C_BG}"/>
  <!-- Labels -->
  <text x="14" y="148" fill="{C_CRIMSON}" font-size="10"
        font-family="'JetBrains Mono',monospace" font-weight="700">BEAR</text>
  <text x="204" y="148" fill="{C_TEAL}" font-size="10"
        font-family="'JetBrains Mono',monospace" font-weight="700">BULL</text>
  <text x="{cx}" y="148" fill="{needle_col}" font-size="13"
        font-family="'JetBrains Mono','Courier New',monospace" font-weight="800"
        text-anchor="middle">{score:+.2f}</text>
</svg>"""


def _stacked_bar(bullish: int, bearish: int, neutral: int) -> str:
    """Pure-CSS horizontal stacked bar showing sentiment distribution."""
    total = bullish + bearish + neutral
    if total == 0:
        return '<div style="color:{};font-size:0.8rem">No data</div>'.format(C_TEXT3)

    bull_pct = bullish / total * 100
    bear_pct = bearish / total * 100
    neut_pct = neutral / total * 100

    return f"""
<div style="display:flex;flex-direction:column;gap:6px">
  <div style="display:flex;height:18px;border-radius:9px;overflow:hidden;
              background:{_hex_to_rgba(C_BORDER, 0.4)}">
    <div style="width:{bull_pct:.1f}%;background:{C_TEAL};transition:width 0.3s"></div>
    <div style="width:{neut_pct:.1f}%;background:{C_TEXT3};transition:width 0.3s"></div>
    <div style="width:{bear_pct:.1f}%;background:{C_CRIMSON};transition:width 0.3s"></div>
  </div>
  <div style="display:flex;gap:16px;font-size:0.73rem">
    <span style="color:{C_TEAL}">&#9632; Bullish {bullish} ({bull_pct:.0f}%)</span>
    <span style="color:{C_TEXT3}">&#9632; Neutral {neutral} ({neut_pct:.0f}%)</span>
    <span style="color:{C_CRIMSON}">&#9632; Bearish {bearish} ({bear_pct:.0f}%)</span>
  </div>
</div>"""


def _badge(text: str, color: str, bg: str = None) -> str:
    bg_val = bg if bg else _hex_to_rgba(color, 0.15)
    border = _hex_to_rgba(color, 0.35)
    return (
        f'<span class="ir-badge" style="background:{bg_val};color:{color};'
        f'border:1px solid {border}">{text}</span>'
    )


def _stat_box(label: str, value: str, sub: str = "", color: str = None) -> str:
    val_color = color if color else C_TEXT
    return (
        f'<div class="ir-kpi">'
        f'<div class="ir-kpi-label">{label}</div>'
        f'<div class="ir-kpi-value" style="color:{val_color}">{value}</div>'
        f'{"<div class=ir-kpi-sub>" + sub + "</div>" if sub else ""}'
        f'</div>'
    )


def _section_header_html(num: int, title: str, subtitle: str = "") -> str:
    return f"""
<div class="ir-section-header">
  <div class="ir-section-number">{num:02d}</div>
  <div>
    <div class="ir-section-title">{title}</div>
    <div class="ir-section-subtitle">{subtitle}</div>
  </div>
</div>"""


# Legacy single-arg wrapper (kept so callers don't break)
def _section_header(title: str, color: str = None) -> str:
    # Color arg ignored — new design uses section numbers and gold rule
    return f"""
<div class="ir-section-header">
  <div>
    <div class="ir-section-title">{title}</div>
  </div>
</div>"""


def _change_html(pct: float) -> str:
    col = _color_for_change(pct)
    arrow = "&#9650;" if pct >= 0 else "&#9660;"
    return f'<span style="color:{col};font-weight:600;font-family:\'JetBrains Mono\',monospace">{arrow} {_format_pct(pct)}</span>'


# ── CSS ───────────────────────────────────────────────────────────────────────

def _css() -> str:
    return f"""
/* ── Reset & base ── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html {{ font-size: 14px; scroll-behavior: smooth; }}

body {{
    font-family: 'Inter', 'Helvetica Neue', -apple-system, sans-serif;
    background: {C_BG};
    color: {C_TEXT};
    font-size: 13px;
    line-height: 1.6;
    max-width: 1100px;
    margin: 0 auto;
    padding: 0;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}}

a {{ color: {C_STEEL}; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

code, .mono {{
    font-family: 'JetBrains Mono', 'Courier New', Courier, monospace;
    font-size: 0.88em;
}}

/* ── Section layout ── */
.ir-section {{
    margin: 0;
    padding: 48px 48px 32px;
    border-top: 1px solid {C_BORDER};
    page-break-before: always;
}}
.ir-section:first-child {{ border-top: none; }}

/* ── Section header — GS/Bloomberg style ── */
.ir-section-header {{
    display: flex;
    align-items: flex-start;
    gap: 16px;
    margin-bottom: 32px;
    padding-bottom: 16px;
    border-bottom: 2px solid {C_GOLD};
}}
.ir-section-number {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 48px;
    font-weight: 900;
    color: rgba(201,168,76,0.15);
    line-height: 1;
    min-width: 70px;
    flex-shrink: 0;
}}
.ir-section-title {{
    font-size: 22px;
    font-weight: 700;
    color: {C_TEXT};
    letter-spacing: -0.3px;
    line-height: 1.2;
}}
.ir-section-subtitle {{
    font-size: 11px;
    color: {C_TEXT2};
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-top: 5px;
}}

/* ── KPI / stat grid ── */
.ir-kpi-grid,
.ir-stat-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 16px;
    margin: 24px 0;
}}
.ir-kpi {{
    background: {C_CARD};
    border: 1px solid {C_BORDER};
    border-top: 3px solid {C_GOLD};
    border-radius: 8px;
    padding: 16px;
}}
.ir-kpi-label {{
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: {C_TEXT2};
    margin-bottom: 8px;
}}
.ir-kpi-value {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 24px;
    font-weight: 700;
    color: {C_TEXT};
    line-height: 1.1;
}}
.ir-kpi-sub {{
    font-size: 11px;
    color: {C_TEXT2};
    margin-top: 4px;
}}

/* ── Institutional data tables ── */
.ir-table-wrap {{
    border-radius: 6px;
    overflow: hidden;
    border: 1px solid {C_BORDER};
    margin-bottom: 24px;
}}
table.ir-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    margin: 0;
}}
table.ir-table th {{
    background: {C_SURFACE};
    color: {C_TEXT2};
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    padding: 10px 12px;
    border-bottom: 2px solid {C_BORDER};
    white-space: nowrap;
    text-align: left;
}}
table.ir-table td {{
    padding: 9px 12px;
    border-bottom: 1px solid {C_BORDER};
    color: {C_TEXT};
    vertical-align: middle;
}}
table.ir-table tr:last-child td {{ border-bottom: none; }}
table.ir-table tr:nth-child(even) td {{ background: rgba(26,46,69,0.4); }}
table.ir-table tr:hover td {{ background: rgba(201,168,76,0.05); color: {C_TEXT}; }}

/* Table utility classes */
table.ir-table .num,
table.ir-table .tc-mono,
table.ir-table .tc-right {{ font-family: 'JetBrains Mono', monospace; text-align: right; }}
table.ir-table .pos {{ color: {C_TEAL}; font-weight: 600; }}
table.ir-table .neg {{ color: {C_CRIMSON}; font-weight: 600; }}
table.ir-table .neu {{ color: {C_AMBER}; }}
table.ir-table .tc-center {{ text-align: center; }}
table.ir-table .tc-name {{ color: {C_TEXT}; font-weight: 600; }}
table.ir-table .tc-ticker {{
    font-family: 'JetBrains Mono', monospace;
    color: {C_GOLD};
    font-weight: 700;
}}
table.ir-table .tc-dim {{ color: {C_TEXT3}; font-size: 0.78rem; }}
table.ir-table .tc-rank {{
    color: {C_TEXT3};
    font-weight: 700;
    text-align: center;
    width: 36px;
}}

/* ── Signal table (compact variant) ── */
table.ir-signal-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.76rem;
    margin-top: 10px;
}}
table.ir-signal-table th {{
    color: {C_TEXT3};
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 0.62rem;
    padding: 4px 8px;
    border-bottom: 1px solid {C_BORDER};
    text-align: left;
}}
table.ir-signal-table td {{
    padding: 5px 8px;
    color: {C_TEXT2};
    border-bottom: 1px solid rgba(30,58,95,0.4);
}}
table.ir-signal-table tr:last-child td {{ border-bottom: none; }}

/* ── Insight cards ── */
.ir-insight,
.ir-insight-card {{
    background: {C_SURFACE};
    border: 1px solid {C_BORDER};
    border-left: 4px solid var(--cat-color, {C_GOLD});
    border-radius: 8px;
    padding: 20px 24px;
    margin-bottom: 16px;
}}
.ir-insight-header {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 12px;
}}
.ir-insight-title {{
    font-size: 15px;
    font-weight: 700;
    color: {C_TEXT};
    line-height: 1.3;
    margin-top: 6px;
}}
.ir-insight-detail {{
    font-size: 13px;
    color: {C_TEXT2};
    line-height: 1.7;
    margin-bottom: 14px;
}}
.ir-insight-score-circle {{
    width: 54px;
    height: 54px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.85rem;
    font-weight: 800;
    flex-shrink: 0;
    border: 2px solid;
    font-family: 'JetBrains Mono', monospace;
}}
.ir-insight-meta-row {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
    margin-bottom: 10px;
}}
.ir-insight-footer {{
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    font-size: 0.75rem;
    color: {C_TEXT3};
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid {C_BORDER};
}}

/* ── Recommendation cards ── */
.ir-rec,
.ir-rec-card {{
    background: {C_SURFACE};
    border: 1px solid {C_BORDER};
    border-left: 5px solid var(--action-color, {C_TEAL});
    border-radius: 8px;
    padding: 24px;
    margin-bottom: 20px;
    display: grid;
    grid-template-columns: 48px 1fr;
    gap: 18px;
    align-items: start;
}}
.ir-rec-rank {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    background: var(--action-color, {C_TEAL});
    border-radius: 50%;
    font-weight: 900;
    font-size: 14px;
    color: {C_BG};
    font-family: 'JetBrains Mono', monospace;
    flex-shrink: 0;
}}
.ir-rec-title {{
    font-size: 17px;
    font-weight: 700;
    color: {C_TEXT};
    line-height: 1.3;
    margin-bottom: 8px;
}}
.ir-rec-meta {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    margin-bottom: 14px;
}}
.ir-rec-prices {{
    background: rgba(30,58,95,0.5);
    border-radius: 6px;
    padding: 12px 16px;
    margin: 12px 0;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
}}
.ir-rec-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 10px;
    margin-bottom: 14px;
}}
.ir-rec-kv {{
    background: rgba(30,58,95,0.4);
    border-radius: 6px;
    padding: 8px 12px;
}}
.ir-rec-kv-label {{
    font-size: 0.60rem;
    font-weight: 700;
    color: {C_TEXT3};
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 3px;
}}
.ir-rec-kv-value {{
    font-size: 0.88rem;
    font-weight: 700;
    color: {C_TEXT};
    font-family: 'JetBrains Mono', monospace;
}}
.ir-rec-rationale {{
    font-size: 0.83rem;
    color: {C_TEXT2};
    line-height: 1.7;
    border-top: 1px solid {C_BORDER};
    padding-top: 12px;
    margin-top: 4px;
}}

/* ── Cover page ── */
.ir-cover {{
    background: linear-gradient(135deg, {C_BG} 0%, {C_SURFACE} 40%, {C_BG} 100%);
    min-height: 100vh;
    padding: 60px 60px 40px;
    display: flex;
    flex-direction: column;
    position: relative;
    overflow: hidden;
    page-break-after: always;
    border-top: none;
}}
.ir-cover::before {{
    content: '';
    position: absolute;
    top: 0; left: 0;
    width: 6px; height: 100%;
    background: linear-gradient(180deg, {C_GOLD} 0%, {C_AMBER} 100%);
}}

/* ── Prose ── */
.ir-prose {{
    font-size: 13px;
    line-height: 1.75;
    color: {C_TEXT2};
    margin: 16px 0 24px;
}}
.ir-prose p {{ margin: 0 0 14px; }}
.ir-prose p:last-child {{ margin-bottom: 0; }}
.ir-prose-highlight {{
    background: rgba(30,58,95,0.35);
    border: 1px solid {C_BORDER};
    border-left: 4px solid {C_GOLD};
    border-radius: 8px;
    padding: 20px 24px;
    margin-top: 24px;
}}

/* ── Score bars ── */
.ir-score-row {{
    display: grid;
    grid-template-columns: 160px 1fr auto;
    align-items: center;
    gap: 16px;
    padding: 10px 0;
    border-bottom: 1px solid {C_BORDER};
}}
.ir-score-row:last-child {{ border-bottom: none; }}
.ir-score-label {{
    font-size: 12px;
    color: {C_TEXT2};
    font-weight: 600;
}}

/* ── Keyword pills ── */
.ir-keyword-cloud {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 12px 0;
}}
.ir-keyword-pill {{
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
    background: rgba(201,168,76,0.10);
    border: 1px solid rgba(201,168,76,0.25);
    color: {C_GOLD};
    white-space: nowrap;
}}

/* ── Badge ── */
.ir-badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    white-space: nowrap;
}}

/* ── Stock cards ── */
.ir-stock-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
}}
.ir-stock-card {{
    background: {C_CARD};
    border: 1px solid {C_BORDER};
    border-radius: 8px;
    padding: 20px 22px;
}}
.ir-stock-header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 12px;
}}
.ir-stock-ticker {{
    font-size: 1.4rem;
    font-weight: 900;
    color: {C_GOLD};
    font-family: 'JetBrains Mono', monospace;
    line-height: 1;
}}
.ir-stock-name {{
    font-size: 0.73rem;
    color: {C_TEXT3};
    margin-top: 3px;
}}
.ir-stock-price {{ text-align: right; }}
.ir-stock-price-val {{
    font-size: 1.3rem;
    font-weight: 800;
    color: {C_TEXT};
    font-family: 'JetBrains Mono', monospace;
    line-height: 1;
}}
.ir-stock-price-chg {{
    font-size: 0.78rem;
    font-weight: 700;
    margin-top: 4px;
}}

/* ── Generic cards ── */
.ir-card {{
    background: {C_CARD};
    border: 1px solid {C_BORDER};
    border-radius: 8px;
    padding: 24px 28px;
    margin-bottom: 14px;
}}
.ir-card-highlight {{
    background: {C_SURFACE};
    border: 1px solid {C_BORDER};
    border-left: 4px solid {C_GOLD};
}}

/* ── Disclaimer ── */
.ir-disclaimer-box {{
    background: rgba(30,58,95,0.25);
    border: 1px solid {C_BORDER};
    border-radius: 8px;
    padding: 20px 24px;
    font-size: 11px;
    color: {C_TEXT3};
    line-height: 1.75;
}}

/* ── Divider ── */
.ir-divider {{
    height: 1px;
    background: {C_BORDER};
    margin: 28px 0;
}}

/* ── Sub-section title ── */
.ir-sub-title {{
    font-size: 10px;
    font-weight: 700;
    color: {C_TEXT3};
    text-transform: uppercase;
    letter-spacing: 0.12em;
    margin-bottom: 14px;
    padding-bottom: 8px;
    border-bottom: 1px solid {C_BORDER};
}}

/* ── Two-column layout ── */
.ir-two-col {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
}}

/* ── Responsive ── */
@media (max-width: 700px) {{
    .ir-section {{ padding: 32px 24px 24px; }}
    .ir-two-col {{ grid-template-columns: 1fr; }}
    .ir-stock-grid {{ grid-template-columns: 1fr; }}
    .ir-rec-card, .ir-rec {{ grid-template-columns: 1fr; }}
    .ir-cover {{ padding: 40px 24px; }}
}}

/* ── Print ── */
@media print {{
    body {{
        background: white !important;
        color: #111111 !important;
        max-width: none;
    }}
    .ir-cover {{
        background: white !important;
        color: #111111 !important;
        min-height: auto;
        padding: 60px 40px;
    }}
    .ir-section {{ padding: 32px 40px 24px; }}
    .ir-section-title,
    .ir-insight-title,
    .ir-rec-title,
    .ir-stock-ticker,
    .ir-kpi-value {{ color: #111111 !important; }}
    .ir-prose, .ir-insight-detail, .ir-rec-rationale,
    .ir-kpi-label, .ir-kpi-sub {{ color: #444444 !important; }}
    .ir-card, .ir-insight, .ir-insight-card, .ir-rec, .ir-rec-card,
    .ir-stock-card, .ir-kpi {{
        background: #f8f8f8 !important;
        border-color: #cccccc !important;
        box-shadow: none !important;
    }}
    .ir-table-wrap {{ border-color: #cccccc !important; }}
    table.ir-table th {{
        background: #eeeeee !important;
        color: #333333 !important;
    }}
    table.ir-table td {{ color: #444444 !important; border-color: #e0e0e0 !important; }}
    .ir-cover {{ page-break-after: always; }}
    .ir-section {{ page-break-before: always; }}
    .ir-keyword-pill {{
        background: #eee !important;
        border-color: #ccc !important;
        color: #333 !important;
    }}
    a {{ color: #1a56db !important; }}
    .ir-prose-highlight, .ir-card-highlight {{
        background: #f0f4ff !important;
        border-color: #93b4fb !important;
    }}
    .ir-disclaimer-box {{
        background: #f8f8f8 !important;
        border-color: #cccccc !important;
        color: #666666 !important;
    }}
    @page {{
        margin: 1.5cm 1.8cm;
        size: A4;
    }}
}}
"""


# ── Section builders ──────────────────────────────────────────────────────────

def _cover_page(report: "InvestorReport") -> str:
    try:
        overall_label = getattr(report.sentiment, "overall_label", "NEUTRAL")
        overall_score = _safe_float(getattr(report.sentiment, "overall_score", 0.0))
    except Exception:
        overall_label = "NEUTRAL"
        overall_score = 0.0

    sent_color = _SENTIMENT_COLORS.get(overall_label, C_TEXT2)
    dq_color   = {
        "FULL":     C_TEAL,
        "PARTIAL":  C_AMBER,
        "DEGRADED": C_CRIMSON,
    }.get(getattr(report, "data_quality", "FULL"), C_TEXT2)
    dq_label = getattr(report, "data_quality", "FULL") or "FULL"

    gauge_svg = _sentiment_gauge_svg(overall_score)

    try:
        signals   = getattr(report.alpha, "signals", []) or []
        n_long    = sum(1 for s in signals if _safe_attr(s, "direction") == "LONG")
        n_short   = sum(1 for s in signals if _safe_attr(s, "direction") == "SHORT")
        n_signals = len(signals)
        risk_level = getattr(report.market, "risk_level", "MODERATE")
        exp_return = _safe_float((getattr(report.alpha, "portfolio", {}) or {}).get("expected_return", 0.0))
    except Exception:
        n_signals = n_long = n_short = 0
        risk_level = "MODERATE"
        exp_return = 0.0

    risk_color = _RISK_COLORS.get(risk_level, C_AMBER)
    ret_color  = _color_for_change(exp_return)

    kpi_grid = f"""
<div class="ir-kpi-grid" style="margin-top:32px;margin-bottom:32px">
  {_stat_box("Sentiment Score", f"{overall_score:+.3f}", sub=overall_label, color=sent_color)}
  {_stat_box("Alpha Signals", str(n_signals), sub=f"{n_long}L / {n_short}S", color=C_GOLD)}
  {_stat_box("Risk Level", risk_level, color=risk_color)}
  {_stat_box("Exp. Return", _format_pct(exp_return), sub="Portfolio alpha est.", color=ret_color)}
</div>"""

    return f"""
<div class="ir-cover">
  <!-- Faint giant section number in background -->
  <div style="position:absolute;top:40px;right:56px;font-family:'JetBrains Mono',monospace;
              font-size:220px;font-weight:900;color:rgba(201,168,76,0.04);
              line-height:1;user-select:none;pointer-events:none">01</div>

  <!-- Firm branding strip -->
  <div style="display:flex;align-items:center;gap:16px;margin-bottom:48px;z-index:1">
    <div style="width:4px;height:32px;background:linear-gradient(180deg,{C_GOLD},{C_AMBER});
                border-radius:2px;flex-shrink:0"></div>
    <div>
      <div style="font-size:11px;font-weight:700;color:{C_GOLD};text-transform:uppercase;
                  letter-spacing:0.25em">Ship Tracker Intelligence</div>
      <div style="font-size:10px;color:{C_TEXT3};letter-spacing:0.15em;text-transform:uppercase">
        Global Shipping Equity Research
      </div>
    </div>
    <div style="margin-left:auto;font-size:10px;color:{C_TEXT3};text-align:right;
                font-family:'JetBrains Mono',monospace">
      {getattr(report, 'report_date', '')}<br>
      <span style="color:{C_TEXT3}">CONFIDENTIAL</span>
    </div>
  </div>

  <div style="z-index:1;width:100%;max-width:720px">
    <div style="font-size:clamp(1.8rem,4vw,3.0rem);font-weight:900;color:{C_TEXT};
                line-height:1.1;letter-spacing:-0.03em;margin-bottom:12px">
      Global Shipping Intelligence Report
    </div>
    <div style="font-size:1.0rem;color:{C_TEXT2};margin-bottom:8px;letter-spacing:0.02em">
      Institutional Sentiment Analysis &amp; Alpha Signal Briefing
    </div>
    <div style="font-size:0.85rem;color:{C_TEXT3};margin-bottom:40px">
      {getattr(report, 'report_date', '')}
    </div>

    <!-- Gauge block -->
    <div style="background:rgba(26,46,69,0.6);border:1px solid {C_BORDER};
                border-top:3px solid {C_GOLD};border-radius:10px;
                padding:28px 32px;margin-bottom:8px;display:inline-block;
                min-width:320px;text-align:center">
      <div style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;
                  letter-spacing:0.15em;margin-bottom:16px">Overall Market Sentiment</div>
      <div style="font-size:1.7rem;font-weight:900;letter-spacing:0.05em;
                  margin-bottom:20px;text-transform:uppercase;color:{sent_color}">
        {overall_label}
      </div>
      {gauge_svg}
    </div>

    {kpi_grid}

    <div style="font-size:0.78rem;color:{C_TEXT3};margin-bottom:40px">
      Data Quality:&nbsp;
      <span style="color:{dq_color};font-weight:700">{dq_label}</span>
      &nbsp;&bull;&nbsp;Sentiment Score:&nbsp;
      <span style="color:{sent_color};font-weight:700;
            font-family:'JetBrains Mono',monospace">
        {overall_score:+.3f}
      </span>
    </div>
  </div>

  <!-- Confidential footer -->
  <div style="margin-top:auto;padding-top:32px;border-top:1px solid {C_BORDER};
              width:100%;z-index:1">
    <div style="display:flex;align-items:center;justify-content:space-between;
                flex-wrap:wrap;gap:12px">
      <div style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;
                  letter-spacing:0.20em">
        Confidential &mdash; For Institutional Use Only
      </div>
      <div style="font-size:10px;color:{C_TEXT3};
                  font-family:'JetBrains Mono',monospace">
        Generated: {getattr(report, 'generated_at', '')}
      </div>
    </div>
    <div style="font-size:10px;color:{C_TEXT3};margin-top:6px">
      Not for redistribution. Past performance does not guarantee future results.
    </div>
  </div>
</div>"""


def _executive_summary(report: "InvestorReport") -> str:
    try:
        signals   = getattr(report.alpha, "signals", []) or []
        insights  = getattr(report.market, "top_insights", []) or []
        portfolio = getattr(report.alpha, "portfolio", {}) or {}
        risk_level = getattr(report.market, "risk_level", "MODERATE")
        overall_label = getattr(report.sentiment, "overall_label", "NEUTRAL")
        overall_score = _safe_float(getattr(report.sentiment, "overall_score", 0.0))
    except Exception:
        signals = []
        insights = []
        portfolio = {}
        risk_level = "MODERATE"
        overall_label = "NEUTRAL"
        overall_score = 0.0

    risk_color   = _RISK_COLORS.get(risk_level, C_AMBER)
    sent_color   = _SENTIMENT_COLORS.get(overall_label, C_TEXT2)

    n_signals    = len(signals)
    n_long       = sum(1 for s in signals if _safe_attr(s, "direction") == "LONG")
    n_short      = sum(1 for s in signals if _safe_attr(s, "direction") == "SHORT")
    exp_return   = _safe_float(portfolio.get("expected_return", 0.0))
    top_insight  = insights[0] if insights else None
    top_opp_str  = _safe_attr(top_insight, "title", default="—") if top_insight else "—"

    stats_html = f"""
<div class="ir-kpi-grid">
  {_stat_box("Sentiment Score", f"{overall_score:+.3f}",
             sub=overall_label, color=sent_color)}
  {_stat_box("Alpha Signals", str(n_signals),
             sub=f"{n_long}L / {n_short}S", color=C_GOLD)}
  {_stat_box("Risk Level", risk_level, color=risk_color)}
  {_stat_box("Expected Return", _format_pct(exp_return),
             sub="Portfolio alpha est.", color=_color_for_change(exp_return))}
</div>"""

    # Prose paragraphs
    prose_text = getattr(report.ai, "executive_summary", "") or ""
    paras = [p.strip() for p in prose_text.split("\n\n") if p.strip()]
    prose_html = "".join(f"<p>{p}</p>" for p in paras) if paras else (
        f"<p style='color:{C_TEXT3}'>Executive summary not available.</p>"
    )

    # Key findings (top 3 insights) as ir-insight cards
    top3 = insights[:3]
    findings_html = ""
    for ins in top3:
        title  = _safe_attr(ins, "title", default="Signal detected")
        detail = _safe_attr(ins, "detail", default="")
        detail_short = detail[:200].rstrip(".") + ("…" if len(detail) > 200 else "") if detail else ""
        cat    = _safe_attr(ins, "category", default="")
        cat_color = _CATEGORY_COLORS.get(cat, C_GOLD)
        findings_html += f"""
<div class="ir-insight" style="--cat-color:{cat_color}">
  <div class="ir-insight-header">
    <div>
      {_badge(cat, cat_color) if cat else ""}
      <div class="ir-insight-title">{title}</div>
    </div>
  </div>
  <div class="ir-insight-detail">{detail_short}</div>
</div>"""

    top_opp_text = f"""
<div style="margin-top:20px">
  <div class="ir-sub-title">Top Opportunity</div>
  <div style="font-size:0.93rem;color:{C_TEAL};font-weight:600;line-height:1.5">
    {top_opp_str}
  </div>
</div>""" if top_opp_str != "—" else ""

    return f"""
<div class="ir-section">
  {_section_header_html(2, "EXECUTIVE SUMMARY", "Market Overview &amp; Investment Thesis")}
  {stats_html}
  <div class="ir-card ir-card-highlight">
    <div class="ir-prose">{prose_html}</div>
    {top_opp_text}
  </div>
  {"<div class='ir-sub-title' style='margin-top:32px'>Key Findings</div>" + findings_html if findings_html else ""}
</div>"""


def _sentiment_section(report: "InvestorReport") -> str:
    try:
        sb = report.sentiment  # SentimentBreakdown object from engine
    except Exception:
        sb = None

    def _sb_float(attr: str) -> float:
        try:
            return _safe_float(getattr(sb, attr, 0.0))
        except Exception:
            return 0.0

    def _sb_int(attr: str) -> int:
        try:
            return int(getattr(sb, attr, 0) or 0)
        except Exception:
            return 0

    def _sb_list(attr: str) -> list:
        try:
            return list(getattr(sb, attr, []) or [])
        except Exception:
            return []

    # Score breakdown rows
    score_rows = [
        ("News Sentiment",    _sb_float("news_score"),    C_STEEL),
        ("Freight Momentum",  _sb_float("freight_score"), C_TEAL),
        ("Macro Backdrop",    _sb_float("macro_score"),   C_AMBER),
        ("Alpha Signals",     _sb_float("alpha_score"),   C_PURPLE),
    ]

    def _norm(v: float) -> float:
        """Map [-1,+1] to [0,1] for bar width."""
        return (v + 1.0) / 2.0

    score_bars_html = ""
    for label, val, color in score_rows:
        norm = _norm(_safe_float(val))
        val_col = C_TEAL if val > 0.1 else (C_CRIMSON if val < -0.1 else C_TEXT2)
        score_bars_html += f"""
<div class="ir-score-row">
  <div class="ir-score-label">{label}</div>
  <div>{_score_bar_svg(norm, color, 260, 8)}</div>
  <div style="font-size:0.75rem;color:{val_col};font-weight:700;min-width:60px;
              text-align:right;font-family:'JetBrains Mono',monospace">{val:+.3f}</div>
</div>"""

    # Stacked bar
    stacked = _stacked_bar(_sb_int("bullish_count"), _sb_int("bearish_count"), _sb_int("neutral_count"))

    # Keywords
    keywords_html = ""
    top_keywords = _sb_list("top_keywords")
    if top_keywords:
        pills = "".join(
            f'<span class="ir-keyword-pill">{kw}</span>'
            for kw in top_keywords[:20]
        )
        keywords_html = f'<div class="ir-keyword-cloud">{pills}</div>'

    # Trending topics table
    topics_html = ""
    trending_topics = _sb_list("trending_topics")
    if trending_topics:
        rows_h = ""
        for t in trending_topics[:10]:
            name     = _safe_str(t.get("topic", t.get("name", "—")))
            mentions = t.get("mentions", t.get("count", "—"))
            sent_val = t.get("sentiment", t.get("score", None))
            sent_str = f"{sent_val:+.2f}" if isinstance(sent_val, (int, float)) else "—"
            sent_col = _color_for_change(_safe_float(sent_val)) if sent_val is not None else C_TEXT3
            rows_h += f"""<tr>
  <td class="tc-name">{name}</td>
  <td class="num">{mentions}</td>
  <td class="num" style="color:{sent_col};font-weight:700">{sent_str}</td>
</tr>"""
        topics_html = f"""
<div class="ir-sub-title" style="margin-top:24px">Trending Topics</div>
<div class="ir-table-wrap">
  <table class="ir-table">
    <thead><tr><th>Topic</th><th class="num">Mentions</th>
    <th class="num">Sentiment</th></tr></thead>
    <tbody>{rows_h}</tbody>
  </table>
</div>"""

    # Top headlines
    headlines_html = ""
    try:
        news_items = list(getattr(report, "news_items", []) or [])
    except Exception:
        news_items = []
    if news_items:
        hl_rows = ""
        for hl in news_items[:5]:
            if isinstance(hl, dict):
                h_title  = _safe_str(hl.get("title", hl.get("headline", "—")))
                h_source = _safe_str(hl.get("source", "—"))
                h_date   = _safe_str(hl.get("published", hl.get("date", "")))
                h_sent   = hl.get("sentiment_score", hl.get("sentiment", None))
            else:
                h_title  = _safe_str(getattr(hl, "title", "—"))
                h_source = _safe_str(getattr(hl, "source", "—"))
                h_date   = _safe_str(getattr(hl, "published_dt", "") or "")
                h_sent   = getattr(hl, "sentiment_score", None)
            h_col   = _color_for_change(_safe_float(h_sent)) if h_sent is not None else C_TEXT3
            h_label = ("BULL" if _safe_float(h_sent) > 0.1
                       else "BEAR" if _safe_float(h_sent) < -0.1 else "NEUT")
            hl_rows += f"""<tr>
  <td style="color:{C_TEXT};font-weight:600;line-height:1.4">{h_title}</td>
  <td class="tc-dim">{h_source}{"&nbsp;&bull;&nbsp;" + str(h_date) if h_date else ""}</td>
  <td style="text-align:center">{_badge(h_label, h_col)}</td>
</tr>"""
        headlines_html = f"""
<div class="ir-sub-title" style="margin-top:24px">Top News Headlines</div>
<div class="ir-table-wrap">
  <table class="ir-table">
    <thead><tr><th>Headline</th><th>Source / Date</th><th style="text-align:center">Tone</th></tr></thead>
    <tbody>{hl_rows}</tbody>
  </table>
</div>"""

    # AI narrative prose
    narrative_text = getattr(report.ai, "sentiment_narrative", "") or ""
    narrative_paras = [p.strip() for p in narrative_text.split("\n\n") if p.strip()]
    narrative_html  = "".join(f"<p>{p}</p>" for p in narrative_paras)

    return f"""
<div class="ir-section">
  {_section_header_html(3, "MARKET SENTIMENT ANALYSIS", "Component Scores &amp; Signal Distribution")}

  <div class="ir-two-col" style="gap:24px;margin-bottom:24px">
    <div>
      <div class="ir-sub-title">Sentiment Score Breakdown</div>
      {score_bars_html}
    </div>
    <div>
      <div class="ir-sub-title">Signal Distribution</div>
      <div style="margin-top:12px">{stacked}</div>
    </div>
  </div>

  {"<div class='ir-sub-title'>Market Keywords</div>" + keywords_html if keywords_html else ""}
  {topics_html}
  {headlines_html}

  {f'<div class="ir-prose ir-prose-highlight" style="margin-top:28px">{narrative_html}</div>' if narrative_html else ""}
</div>"""


def _alpha_section(report: "InvestorReport") -> str:
    try:
        signals   = getattr(report.alpha, "signals", []) or []
        portfolio = getattr(report.alpha, "portfolio", {}) or {}
    except Exception:
        signals = []
        portfolio = {}

    # Portfolio summary stats
    exp_ret  = _safe_float(portfolio.get("expected_return", 0.0))
    sharpe   = _safe_float(portfolio.get("sharpe", 0.0))
    port_vol = _safe_float(portfolio.get("portfolio_vol", 0.0))
    max_dd   = _safe_float(portfolio.get("max_dd_estimate", 0.0))

    port_stats = f"""
<div class="ir-kpi-grid" style="margin-bottom:28px">
  {_stat_box("Expected Return", _format_pct(exp_ret), color=_color_for_change(exp_ret))}
  {_stat_box("Sharpe Ratio", f"{sharpe:.2f}",
             color=C_TEAL if sharpe > 1 else (C_AMBER if sharpe > 0 else C_CRIMSON))}
  {_stat_box("Portfolio Vol", f"{port_vol:.1f}%", color=C_TEXT2)}
  {_stat_box("Max DD Est.", f"-{max_dd:.1f}%", color=C_CRIMSON)}
</div>"""

    # Conviction breakdown mini bar chart (CSS)
    high_n   = sum(1 for s in signals if _safe_attr(s, "conviction") == "HIGH")
    med_n    = sum(1 for s in signals if _safe_attr(s, "conviction") == "MEDIUM")
    low_n    = sum(1 for s in signals if _safe_attr(s, "conviction") == "LOW")
    total_n  = len(signals) or 1

    conv_html = f"""
<div class="ir-sub-title">Signal Conviction Distribution</div>
<div style="display:flex;flex-direction:column;gap:8px;margin-bottom:24px">
  {_conv_bar("HIGH",   high_n, total_n, C_TEAL)}
  {_conv_bar("MEDIUM", med_n,  total_n, C_AMBER)}
  {_conv_bar("LOW",    low_n,  total_n, C_TEXT3)}
</div>"""

    # By signal type
    type_counts: Dict[str, int] = {}
    for s in signals:
        t = _safe_attr(s, "signal_type", default="OTHER")
        type_counts[t] = type_counts.get(t, 0) + 1
    type_html = ""
    if type_counts:
        type_html = '<div class="ir-sub-title">By Signal Type</div>'
        type_html += '<div style="display:flex;flex-direction:column;gap:6px;margin-bottom:24px">'
        type_colors = {
            "MOMENTUM":       C_TEAL,
            "MEAN_REVERSION": C_PURPLE,
            "FUNDAMENTAL":    C_STEEL,
            "MACRO":          C_STEEL,
            "TECHNICAL":      C_AMBER,
        }
        for st, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            col = type_colors.get(st, C_TEXT2)
            type_html += _conv_bar(st, cnt, total_n, col)
        type_html += "</div>"

    # LONG / SHORT signal tables
    long_signals  = [s for s in signals if _safe_attr(s, "direction") == "LONG"]
    short_signals = [s for s in signals if _safe_attr(s, "direction") == "SHORT"]

    long_table  = _signal_table("LONG Signals", long_signals, C_TEAL)   if long_signals  else ""
    short_table = _signal_table("SHORT Signals", short_signals, C_CRIMSON) if short_signals else ""

    # AI narrative
    opp_text  = getattr(report.ai, "opportunity_narrative", "") or ""
    opp_paras = [p.strip() for p in opp_text.split("\n\n") if p.strip()]
    opp_html  = "".join(f"<p>{p}</p>" for p in opp_paras)

    return f"""
<div class="ir-section">
  {_section_header_html(4, "ALPHA SIGNAL INTELLIGENCE", "Portfolio Metrics &amp; Directional Signals")}
  {port_stats}
  <div class="ir-two-col">
    <div>{conv_html}</div>
    <div>{type_html}</div>
  </div>
  {long_table}
  {short_table}
  {f'<div class="ir-prose ir-prose-highlight" style="margin-top:24px">{opp_html}</div>'
   if opp_html else ""}
</div>"""


def _conv_bar(label: str, count: int, total: int, color: str) -> str:
    pct = count / total * 100 if total else 0
    return f"""
<div style="display:flex;align-items:center;gap:12px">
  <div style="font-size:11px;color:{C_TEXT2};font-weight:600;min-width:100px">{label}</div>
  <div style="flex:1;height:8px;background:{_hex_to_rgba(C_BORDER, 0.6)};
              border-radius:4px;overflow:hidden">
    <div style="width:{pct:.1f}%;height:100%;background:{color};border-radius:4px"></div>
  </div>
  <div style="font-size:11px;color:{C_TEXT3};min-width:28px;
              font-family:'JetBrains Mono',monospace">{count}</div>
</div>"""


def _signal_table(title: str, signals: list, header_color: str) -> str:
    if not signals:
        return ""
    rows_h = ""
    for s in signals[:20]:
        ticker   = _safe_str(_safe_attr(s, "ticker"))
        name     = _safe_str(_safe_attr(s, "signal_name"))
        strength = _safe_float(_safe_attr(s, "strength", default=0.0))
        conv     = _safe_str(_safe_attr(s, "conviction", default="—"))
        conv_col = _CONVICTION_COLORS.get(conv, C_TEXT2)
        entry    = _safe_attr(s, "entry_price")
        target   = _safe_attr(s, "target_price")
        stop     = _safe_attr(s, "stop_loss")
        exp_ret  = _safe_float(_safe_attr(s, "expected_return_pct", default=0.0))
        horizon  = _safe_str(_safe_attr(s, "time_horizon", default="—"))

        entry_s  = _format_price(entry)  if entry  is not None else "—"
        target_s = _format_price(target) if target is not None else "—"
        stop_s   = _format_price(stop)   if stop   is not None else "—"
        ret_col  = _color_for_change(exp_ret)

        rows_h += f"""<tr>
  <td class="tc-name">{name}</td>
  <td class="tc-ticker">{ticker}</td>
  <td class="tc-right">{_score_bar_svg(strength, header_color, 80, 6)}</td>
  <td class="tc-center">{_badge(conv, conv_col)}</td>
  <td class="num">{entry_s}</td>
  <td class="num">{target_s}</td>
  <td class="num">{stop_s}</td>
  <td class="num" style="color:{ret_col};font-weight:700">{_format_pct(exp_ret)}</td>
  <td class="tc-center tc-dim">{horizon}</td>
</tr>"""

    return f"""
<div class="ir-sub-title" style="color:{header_color};margin-top:24px">{title}</div>
<div class="ir-table-wrap" style="margin-bottom:20px">
  <table class="ir-table">
    <thead><tr>
      <th>Signal</th><th>Ticker</th><th>Strength</th><th class="tc-center">Conviction</th>
      <th class="num">Entry</th><th class="num">Target</th>
      <th class="num">Stop</th><th class="num">Exp. Ret.</th>
      <th class="tc-center">Horizon</th>
    </tr></thead>
    <tbody>{rows_h}</tbody>
  </table>
</div>"""


def _market_intelligence_section(report: "InvestorReport") -> str:
    try:
        insights   = getattr(report.market, "top_insights", []) or []
        risk_level = getattr(report.market, "risk_level", "MODERATE")
    except Exception:
        insights = []
        risk_level = "MODERATE"

    risk_color  = _RISK_COLORS.get(risk_level, C_AMBER)

    # Risk level header
    risk_html = f"""
<div style="display:flex;align-items:center;gap:12px;margin-bottom:28px">
  <div style="font-size:12px;color:{C_TEXT2}">Market Risk Level:</div>
  {_badge(risk_level, risk_color)}
</div>"""

    # Top insights cards
    cards_html = ""
    for ins in insights[:5]:
        cat    = _safe_str(_safe_attr(ins, "category",   default="ROUTE"))
        action = _safe_str(_safe_attr(ins, "action",     default="Monitor"))
        score  = _safe_float(_safe_attr(ins, "score",    default=0.5))
        title  = _safe_str(_safe_attr(ins, "title",      default="Signal"))
        detail = _safe_str(_safe_attr(ins, "detail",     default=""))
        ports  = _safe_attr(ins, "ports_involved",       default=[]) or []
        routes = _safe_attr(ins, "routes_involved",      default=[]) or []
        stocks = _safe_attr(ins, "stocks_potentially_affected", default=[]) or []
        sigs   = _safe_attr(ins, "supporting_signals",   default=[]) or []
        stale  = _safe_attr(ins, "data_freshness_warning", default=False)

        cat_color  = _CATEGORY_COLORS.get(cat, C_GOLD)
        score_col  = C_TEAL if score >= 0.70 else (C_AMBER if score >= 0.40 else C_CRIMSON)
        act_color  = _ACTION_COLORS.get(action, C_TEXT2)
        score_pct  = f"{score * 100:.0f}%"

        stale_badge = ("&nbsp;" + _badge("Stale Data", C_AMBER)) if stale else ""

        # Supporting signals mini-list
        sig_items = ""
        for sg in sigs[:4]:
            sg_name = _safe_str(_safe_attr(sg, "name",      default="Signal"))
            sg_dir  = _safe_str(_safe_attr(sg, "direction", default="neutral"))
            sg_val  = _safe_float(_safe_attr(sg, "value",   default=0.0))
            sg_col  = C_TEAL if sg_dir == "bullish" else (C_CRIMSON if sg_dir == "bearish" else C_TEXT3)
            arrow   = "&#9650;" if sg_dir == "bullish" else ("&#9660;" if sg_dir == "bearish" else "&#8594;")
            sig_items += (
                f'<div style="font-size:11px;color:{C_TEXT2};padding:2px 0">'
                f'<span style="color:{sg_col}">{arrow}</span> {sg_name} '
                f'<span style="color:{C_TEXT3};font-family:\'JetBrains Mono\',monospace">{sg_val:.2f}</span>'
                f'</div>'
            )

        ports_txt  = ", ".join(ports)  or "—"
        routes_txt = ", ".join(routes) or "—"
        stocks_txt = ", ".join(stocks) or "—"

        cards_html += f"""
<div class="ir-insight" style="--cat-color:{cat_color}">
  <div class="ir-insight-header">
    <div style="flex:1">
      <div class="ir-insight-meta-row">
        {_badge(cat, cat_color)}
        {_badge(action, act_color)}
        {stale_badge}
      </div>
      <div class="ir-insight-title">{title}</div>
    </div>
    <div class="ir-insight-score-circle"
         style="background:{_hex_to_rgba(score_col, 0.14)};
                color:{score_col};border-color:{_hex_to_rgba(score_col, 0.35)}">
      {score_pct}
    </div>
  </div>
  <div class="ir-insight-detail">{detail}</div>
  {f'<div style="margin-bottom:12px">{sig_items}</div>' if sig_items else ""}
  <div class="ir-insight-footer">
    <span><strong style="color:{C_TEXT2}">Ports:</strong> {ports_txt}</span>
    <span><strong style="color:{C_TEXT2}">Routes:</strong> {routes_txt}</span>
    <span><strong style="color:{C_TEXT2}">Stocks:</strong> {stocks_txt}</span>
  </div>
  <div style="margin-top:10px">{_score_bar_svg(score, score_col, 220, 7)}</div>
</div>"""

    # Port summary table
    port_rows = _extract_port_rows(insights)
    port_table_html = ""
    if port_rows:
        rows_h = ""
        for rank, pr in enumerate(port_rows[:10], 1):
            port_name  = pr.get("port", "—")
            region     = pr.get("region", "—")
            d_score    = _safe_float(pr.get("demand_score", 0.5))
            congestion = pr.get("congestion", None)
            status     = pr.get("status", "—")
            d_col      = C_TEAL if d_score >= 0.70 else (C_AMBER if d_score >= 0.40 else C_CRIMSON)
            s_col      = _RISK_COLORS.get(str(status).upper(), C_TEXT2)

            cong_html = "—"
            if congestion is not None:
                cval = _safe_float(congestion)
                c_col = C_CRIMSON if cval >= 0.6 else (C_AMBER if cval >= 0.35 else C_TEAL)
                cong_html = f'<span style="color:{c_col};font-weight:600">{cval:.0%}</span>'

            rows_h += f"""<tr>
  <td class="tc-rank">#{rank}</td>
  <td class="tc-name">{port_name}</td>
  <td class="tc-dim">{region}</td>
  <td>{_score_bar_svg(d_score, d_col, 100, 6)}</td>
  <td>{cong_html}</td>
  <td>{_badge(status, s_col) if status != "—" else "—"}</td>
</tr>"""
        port_table_html = f"""
<div class="ir-sub-title" style="margin-top:32px">Top Port Overview</div>
<div class="ir-table-wrap">
  <table class="ir-table">
    <thead><tr>
      <th>Rank</th><th>Port</th><th>Region</th>
      <th>Demand Score</th><th>Congestion</th><th>Status</th>
    </tr></thead>
    <tbody>{rows_h}</tbody>
  </table>
</div>"""

    # Route opportunities table
    route_rows = _extract_route_rows(insights)
    route_table_html = ""
    if route_rows:
        rows_h = ""
        for rank, rr in enumerate(route_rows[:10], 1):
            rname  = rr.get("route", "—")
            score  = _safe_float(rr.get("score", 0.5))
            rate   = rr.get("rate", None)
            chg30  = rr.get("change_30d", None)
            opp    = rr.get("opportunity", "—")
            s_col  = C_TEAL if score >= 0.70 else (C_AMBER if score >= 0.40 else C_CRIMSON)
            rate_s = _format_price(rate) if rate is not None else "—"
            chg_s  = _change_html(_safe_float(chg30)) if chg30 is not None else "—"
            rows_h += f"""<tr>
  <td class="tc-rank">#{rank}</td>
  <td class="tc-name">{rname}</td>
  <td>{_score_bar_svg(score, s_col, 80, 6)}</td>
  <td class="num">{rate_s}</td>
  <td class="num">{chg_s}</td>
  <td class="tc-dim">{opp}</td>
</tr>"""
        route_table_html = f"""
<div class="ir-sub-title" style="margin-top:24px">Top Route Opportunities</div>
<div class="ir-table-wrap">
  <table class="ir-table">
    <thead><tr>
      <th>Rank</th><th>Route</th><th>Score</th>
      <th class="num">Rate</th>
      <th class="num">30d Change</th><th>Opportunity</th>
    </tr></thead>
    <tbody>{rows_h}</tbody>
  </table>
</div>"""

    return f"""
<div class="ir-section">
  {_section_header_html(5, "MARKET INTELLIGENCE &amp; INSIGHTS", "Port Demand · Route Opportunities · Risk Assessment")}
  {risk_html}
  {cards_html if cards_html else f'<p style="color:{C_TEXT3}">No insights available.</p>'}
  {port_table_html}
  {route_table_html}
</div>"""


def _extract_port_rows(insights: list) -> list:
    """Collect unique port names from insights and synthesise summary rows."""
    seen: Dict[str, dict] = {}
    for ins in insights:
        ports = _safe_attr(ins, "ports_involved", default=[]) or []
        score = _safe_float(_safe_attr(ins, "score", default=0.5))
        for p in ports:
            if p not in seen:
                seen[p] = {"port": p, "region": "—", "demand_score": score,
                            "congestion": None, "status": "Active", "_count": 1}
            else:
                seen[p]["demand_score"] = (seen[p]["demand_score"] + score) / 2
                seen[p]["_count"] += 1
    return sorted(seen.values(), key=lambda x: -x["demand_score"])


def _extract_route_rows(insights: list) -> list:
    """Collect unique route names from insights."""
    seen: Dict[str, dict] = {}
    for ins in insights:
        routes = _safe_attr(ins, "routes_involved", default=[]) or []
        score  = _safe_float(_safe_attr(ins, "score", default=0.5))
        action = _safe_attr(ins, "action", default="Monitor")
        for r in routes:
            if r not in seen:
                seen[r] = {"route": r, "score": score,
                           "rate": None, "change_30d": None, "opportunity": action}
            else:
                seen[r]["score"] = (seen[r]["score"] + score) / 2
    return sorted(seen.values(), key=lambda x: -x["score"])


def _freight_section(report: "InvestorReport") -> str:
    try:
        fr = report.freight
        momentum_label   = getattr(fr, "momentum_label", "Stable") or "Stable"
        fbx_composite    = getattr(fr, "fbx_composite", None)
        avg_change_30d   = getattr(fr, "avg_change_30d_pct", None)
        biggest_mover    = getattr(fr, "biggest_mover", {}) or {}
        biggest_route    = biggest_mover.get("route_id", "") if isinstance(biggest_mover, dict) else ""
        biggest_pct      = biggest_mover.get("change_pct", None) if isinstance(biggest_mover, dict) else None
        routes           = getattr(fr, "routes", []) or []
    except Exception:
        momentum_label = "Stable"
        fbx_composite  = None
        avg_change_30d = None
        biggest_route  = ""
        biggest_pct    = None
        routes         = []

    mom_col = (
        C_TEAL if momentum_label == "Accelerating"
        else C_CRIMSON if momentum_label == "Decelerating"
        else C_TEXT2
    )
    arrow_sym = (
        "&#9650;" if momentum_label == "Accelerating"
        else "&#9660;" if momentum_label == "Decelerating"
        else "&#8594;"
    )

    fbx_str  = _format_price(fbx_composite) if fbx_composite is not None else "N/A"
    avg_chg  = _format_pct(_safe_float(avg_change_30d)) if avg_change_30d is not None else "—"

    summary_html = f"""
<div class="ir-kpi-grid" style="margin-bottom:28px">
  {_stat_box("Momentum", f'<span style="color:{mom_col}">{arrow_sym} {momentum_label}</span>')}
  {_stat_box("FBX Composite", fbx_str, sub="$/FEU", color=C_STEEL)}
  {_stat_box("Avg 30d Change", avg_chg,
             color=_color_for_change(_safe_float(avg_change_30d)))}
  {_stat_box("Biggest Mover", biggest_route or "—",
             sub=_format_pct(_safe_float(biggest_pct)) if biggest_pct is not None else "",
             color=_color_for_change(_safe_float(biggest_pct)))}
</div>"""

    # Routes table
    rows_html = ""
    for row in routes:
        rname  = _safe_str(row.get("route", row.get("name", "—")))
        rate   = row.get("current_rate", row.get("rate", None))
        chg30  = row.get("change_30d",  row.get("pct_30d",  None))
        chg90  = row.get("change_90d",  row.get("pct_90d",  None))
        updated = _safe_str(row.get("updated", row.get("as_of", "")))

        rate_s  = _format_price(_safe_float(rate)) if rate is not None else "—"
        chg30_h = _change_html(_safe_float(chg30)) if chg30 is not None else "—"
        chg90_h = _change_html(_safe_float(chg90)) if chg90 is not None else "—"

        rows_html += f"""<tr>
  <td class="tc-name">{rname}</td>
  <td class="num" style="color:{C_TEXT};font-weight:700">{rate_s}</td>
  <td class="num">{chg30_h}</td>
  <td class="num">{chg90_h}</td>
  <td class="tc-dim">{updated}</td>
</tr>"""

    table_html = ""
    if rows_html:
        table_html = f"""
<div class="ir-sub-title">Route Rate Table</div>
<div class="ir-table-wrap">
  <table class="ir-table">
    <thead><tr>
      <th>Route</th>
      <th class="num">Current Rate ($/FEU)</th>
      <th class="num">30d Change</th>
      <th class="num">90d Change</th>
      <th>Updated</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""

    # Narrative
    narr_text  = getattr(report.ai, "sentiment_narrative", "") or ""
    narr_paras = [p.strip() for p in narr_text.split("\n\n") if p.strip()]
    narr_html  = "".join(f"<p>{p}</p>" for p in narr_paras[-2:])  # last 2 paras = freight focus

    return f"""
<div class="ir-section">
  {_section_header_html(6, "FREIGHT RATE ANALYSIS", "FBX Composite · Route Rates · Momentum")}
  {summary_html}
  {table_html}
  {f'<div class="ir-prose" style="margin-top:20px">{narr_html}</div>' if narr_html else ""}
</div>"""


def _macro_section(report: "InvestorReport") -> str:
    try:
        m = report.macro
        bdi              = getattr(m, "bdi", None)
        bdi_change_30d   = getattr(m, "bdi_change_30d_pct", None)
        wti              = getattr(m, "wti", None)
        treasury_10y     = getattr(m, "treasury_10y", None)
        supply_chain_stress = getattr(m, "supply_chain_stress", None)
        pmi_proxy        = getattr(m, "pmi_proxy", None)
    except Exception:
        bdi = bdi_change_30d = wti = treasury_10y = supply_chain_stress = pmi_proxy = None

    def _opt(val: Optional[float], fmt: str = ".1f", prefix: str = "",
             suffix: str = "", default: str = "N/A") -> str:
        if val is None:
            return default
        try:
            return f"{prefix}{val:{fmt}}{suffix}"
        except Exception:
            return str(val)

    bdi_s      = _opt(bdi,  ".0f")
    bdi_chg_s  = _opt(bdi_change_30d, "+.1f", suffix="%") if bdi_change_30d is not None else "N/A"
    wti_s      = _opt(wti,  ".2f", prefix="$")
    tsy_s      = _opt(treasury_10y, ".2f", suffix="%")
    scs_s      = str(supply_chain_stress) if supply_chain_stress is not None else "N/A"
    pmi_s      = _opt(pmi_proxy, ".1f")

    bdi_chg_col = _color_for_change(_safe_float(bdi_change_30d))
    _stress_color_map = {"LOW": C_TEAL, "MODERATE": C_AMBER, "HIGH": C_CRIMSON}
    scs_col = _stress_color_map.get(str(supply_chain_stress).upper(), C_TEXT2) \
              if supply_chain_stress is not None else C_TEXT2
    pmi_col = (C_TEAL if _safe_float(pmi_proxy) > 52
               else C_CRIMSON if _safe_float(pmi_proxy) < 48
               else C_AMBER) if pmi_proxy is not None else C_TEXT2

    macro_stats = f"""
<div class="ir-kpi-grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
     margin-bottom:28px">
  {_stat_box("BDI", bdi_s, color=C_STEEL)}
  {_stat_box("BDI 30d Chg", bdi_chg_s, color=bdi_chg_col)}
  {_stat_box("WTI Crude", wti_s, sub="$/bbl", color=C_TEXT2)}
  {_stat_box("10Y Treasury", tsy_s, sub="yield", color=C_TEXT2)}
  {_stat_box("SC Stress", scs_s, color=scs_col)}
  {_stat_box("PMI Proxy", pmi_s, color=pmi_col)}
</div>"""

    narr_html = ""

    # Risk narrative
    risk_text  = getattr(report.ai, "risk_narrative", "") or ""
    risk_paras = [p.strip() for p in risk_text.split("\n\n") if p.strip()]
    risk_html  = "".join(f"<p>{p}</p>" for p in risk_paras)

    return f"""
<div class="ir-section">
  {_section_header_html(7, "MACRO ENVIRONMENT", "BDI · Crude · Rates · Supply Chain Stress")}
  {macro_stats}
  {f'<div class="ir-prose">{narr_html}</div>' if narr_html else ""}
  {f'<div class="ir-prose ir-prose-highlight" style="margin-top:20px">{risk_html}</div>'
   if risk_html else ""}
</div>"""


def _stocks_section(report: "InvestorReport") -> str:
    try:
        stocks_obj = report.stocks
        tickers           = list(getattr(stocks_obj, "tickers", []) or [])
        prices            = dict(getattr(stocks_obj, "prices", {}) or {})
        changes_30d       = dict(getattr(stocks_obj, "changes_30d", {}) or {})
        signals_by_ticker = dict(getattr(stocks_obj, "signals_by_ticker", {}) or {})
        top_ticker        = _safe_str(getattr(stocks_obj, "top_pick", ""))
    except Exception:
        tickers = []
        prices = {}
        changes_30d = {}
        signals_by_ticker = {}
        top_ticker = ""

    # Also get alpha signals for any ticker not covered by stocks_obj
    try:
        alpha_signals = getattr(report.alpha, "signals", []) or []
    except Exception:
        alpha_signals = []

    # Build a fallback ticker_signals dict from alpha signals
    ticker_signals_fallback: Dict[str, list] = {}
    for s in alpha_signals:
        t = _safe_attr(s, "ticker", default="")
        if t:
            ticker_signals_fallback.setdefault(t, []).append(s)

    # If no tickers, fall back to _TICKERS that have alpha signals
    if not tickers:
        tickers = [t for t in _TICKERS if ticker_signals_fallback.get(t)]
        best_ret = -999.0
        for s in alpha_signals:
            if (_safe_attr(s, "conviction") == "HIGH"
                    and _safe_attr(s, "direction") == "LONG"):
                ret = _safe_float(_safe_attr(s, "expected_return_pct", default=0.0))
                if ret > best_ret:
                    best_ret = ret
                    top_ticker = _safe_attr(s, "ticker", default="")

    cards_html = ""
    for ticker in tickers:
        price    = prices.get(ticker)
        chg30    = changes_30d.get(ticker)
        sigs     = signals_by_ticker.get(ticker) or ticker_signals_fallback.get(ticker, [])
        top_sig  = max(sigs, key=lambda s: _safe_float(_safe_attr(s, "strength", default=0))) \
                   if sigs else None

        name     = _TICKER_NAMES.get(ticker, ticker)
        price_s  = _format_price(price) if price is not None else "—"
        chg_col  = _color_for_change(_safe_float(chg30))
        chg_html = f'<span style="color:{chg_col};font-weight:700">{_change_html(_safe_float(chg30))}</span>' \
                   if chg30 is not None else f'<span style="color:{C_TEXT3}">—</span>'

        is_top = ticker == top_ticker
        glow_style = (
            f"background:linear-gradient(135deg,{C_CARD},{C_SURFACE});"
            f"border-color:{_hex_to_rgba(C_TEAL, 0.35)};"
            f"box-shadow:0 0 28px {_hex_to_rgba(C_TEAL, 0.12)};"
            f"border-top:3px solid {C_GOLD};"
        ) if is_top else ""

        # Top signal badge
        top_sig_html = ""
        if top_sig is not None:
            ts_dir  = _safe_attr(top_sig, "direction", default="NEUTRAL")
            ts_conv = _safe_attr(top_sig, "conviction", default="LOW")
            ts_ret  = _safe_float(_safe_attr(top_sig, "expected_return_pct", default=0.0))
            ts_col  = C_TEAL if ts_dir == "LONG" else (C_CRIMSON if ts_dir == "SHORT" else C_TEXT2)
            top_sig_html = (
                f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">'
                f'{_badge(ts_dir, ts_col)}'
                f'{_badge(ts_conv, _CONVICTION_COLORS.get(ts_conv, C_TEXT2))}'
                f'<span style="font-size:11px;color:{ts_col};font-weight:700;align-self:center;'
                f'font-family:\'JetBrains Mono\',monospace">{_format_pct(ts_ret)}</span>'
                f'</div>'
            )

        # Mini signal list
        sig_list = ""
        for sg in sigs[:3]:
            sg_dir  = _safe_attr(sg, "direction", default="NEUTRAL")
            sg_name = _safe_attr(sg, "signal_name", default="Signal")
            sg_col  = C_TEAL if sg_dir == "LONG" else (C_CRIMSON if sg_dir == "SHORT" else C_TEXT3)
            arrow   = "&#9650;" if sg_dir == "LONG" else ("&#9660;" if sg_dir == "SHORT" else "&#8594;")
            sig_list += (
                f'<div style="font-size:11px;color:{C_TEXT2};padding:2px 0">'
                f'<span style="color:{sg_col}">{arrow}</span> {sg_name}'
                f'</div>'
            )

        top_pick_badge = (
            f'<div style="margin-bottom:8px">{_badge("TOP PICK", C_GOLD)}</div>'
            if is_top else ""
        )

        cards_html += f"""
<div class="ir-stock-card" style="{glow_style}">
  {top_pick_badge}
  <div class="ir-stock-header">
    <div>
      <div class="ir-stock-ticker">{ticker}</div>
      <div class="ir-stock-name">{name}</div>
    </div>
    <div class="ir-stock-price">
      <div class="ir-stock-price-val">{price_s}</div>
      <div class="ir-stock-price-chg">{chg_html}</div>
    </div>
  </div>
  {top_sig_html}
  <div>{sig_list}</div>
</div>"""

    return f"""
<div class="ir-section">
  {_section_header_html(8, "SHIPPING STOCK ANALYSIS", "Per-Ticker Signal Grid &amp; Top Pick")}
  <div class="ir-stock-grid">
    {cards_html if cards_html else
     f'<p style="color:{C_TEXT3}">No stock data available.</p>'}
  </div>
</div>"""


def _recommendations_section(report: "InvestorReport") -> str:
    try:
        recs = list(getattr(report.ai, "top_recommendations", []) or [])
    except Exception:
        recs = []

    cards_html = ""
    for rec in recs:
        rank        = rec.get("rank", "—")
        title       = rec.get("title", "Untitled")
        action      = rec.get("action", "MONITOR")
        ticker      = rec.get("ticker", "")
        conviction  = rec.get("conviction", "MEDIUM")
        time_horizon = rec.get("time_horizon", "—")
        rationale   = rec.get("rationale", "")
        exp_return  = _safe_float(rec.get("expected_return", 0.0))
        risk_rating = rec.get("risk_rating", "MODERATE")
        entry       = rec.get("entry", None)
        target      = rec.get("target", None)
        stop        = rec.get("stop", None)

        action_col = _ACTION_COLORS.get(str(action).upper(), C_TEXT2)
        conv_col   = _CONVICTION_COLORS.get(conviction, C_TEXT2)
        risk_col   = _RISK_COLORS.get(risk_rating, C_AMBER)
        ret_col    = _color_for_change(exp_return)

        ticker_str = f" &mdash; {_badge(ticker, C_GOLD)}" if ticker else ""

        # Price grid
        price_kvs = ""
        if entry is not None:
            price_kvs += _rec_kv("Entry", _format_price(_safe_float(entry)))
        if target is not None:
            price_kvs += _rec_kv("Target", _format_price(_safe_float(target)), C_TEAL)
        if stop is not None:
            price_kvs += _rec_kv("Stop", _format_price(_safe_float(stop)), C_CRIMSON)
        price_row = f'<div class="ir-rec-grid">{price_kvs}</div>' if price_kvs else ""

        cards_html += f"""
<div class="ir-rec" style="--action-color:{action_col}">
  <div class="ir-rec-rank">{rank}</div>
  <div>
    <div class="ir-rec-title">
      {title}{ticker_str}
    </div>
    <div class="ir-rec-meta">
      {_badge(action, action_col)}
      {_badge(f"Conv: {conviction}", conv_col)}
      {_badge(f"Risk: {risk_rating}", risk_col)}
      <span style="font-size:11px;color:{ret_col};font-weight:700;align-self:center;
                   font-family:'JetBrains Mono',monospace">
        {_format_pct(exp_return)} expected
      </span>
      <span style="font-size:11px;color:{C_TEXT3};align-self:center">
        {time_horizon}
      </span>
    </div>
    {price_row}
    {"<div class='ir-rec-rationale'>" + rationale + "</div>" if rationale else ""}
  </div>
</div>"""

    # Outlook box
    outlook_text  = getattr(report.ai, "outlook_30d", "") or ""
    outlook_paras = [p.strip() for p in outlook_text.split("\n\n") if p.strip()]
    outlook_html  = "".join(f"<p>{p}</p>" for p in outlook_paras)

    return f"""
<div class="ir-section">
  {_section_header_html(9, "AI RECOMMENDATIONS", "Ranked Actionable Ideas &amp; 30-Day Outlook")}
  {cards_html if cards_html else f'<p style="color:{C_TEXT3}">No recommendations generated.</p>'}
  {f'''
  <div class="ir-card ir-card-highlight" style="margin-top:24px">
    <div class="ir-sub-title" style="color:{C_GOLD}">30-Day Forward Outlook</div>
    <div class="ir-prose">{outlook_html}</div>
  </div>''' if outlook_html else ""}
</div>"""


def _rec_kv(label: str, value: str, color: str = None) -> str:
    val_color = color if color else C_TEXT
    return (
        f'<div class="ir-rec-kv">'
        f'<div class="ir-rec-kv-label">{label}</div>'
        f'<div class="ir-rec-kv-value" style="color:{val_color}">{value}</div>'
        f'</div>'
    )


def _appendix(report: "InvestorReport") -> str:
    try:
        signals = list(getattr(report.alpha, "signals", []) or [])
    except Exception:
        signals = []

    # Full signals scorecard table
    scorecard_html = ""
    if signals:
        rows_h = ""
        for s in signals:
            ticker   = _safe_str(_safe_attr(s, "ticker"))
            name     = _safe_str(_safe_attr(s, "signal_name"))
            stype    = _safe_str(_safe_attr(s, "signal_type",  default="—"))
            direct   = _safe_str(_safe_attr(s, "direction",    default="—"))
            strength = _safe_float(_safe_attr(s, "strength",   default=0.0))
            conv     = _safe_str(_safe_attr(s, "conviction",   default="—"))
            entry    = _safe_attr(s, "entry_price")
            target   = _safe_attr(s, "target_price")
            stop     = _safe_attr(s, "stop_loss")
            exp_ret  = _safe_float(_safe_attr(s, "expected_return_pct", default=0.0))
            rr       = _safe_float(_safe_attr(s, "risk_reward", default=0.0))
            horizon  = _safe_str(_safe_attr(s, "time_horizon",  default="—"))

            dir_col  = C_TEAL if direct == "LONG" else (C_CRIMSON if direct == "SHORT" else C_TEXT3)
            conv_col = _CONVICTION_COLORS.get(conv, C_TEXT2)
            ret_col  = _color_for_change(exp_ret)

            rows_h += f"""<tr>
  <td class="tc-ticker">{ticker}</td>
  <td class="tc-name">{name}</td>
  <td class="tc-dim">{stype}</td>
  <td style="color:{dir_col};font-weight:700">{direct}</td>
  <td class="num">{strength:.2f}</td>
  <td class="tc-center">{_badge(conv, conv_col)}</td>
  <td class="num">{_format_price(entry) if entry else "—"}</td>
  <td class="num">{_format_price(target) if target else "—"}</td>
  <td class="num">{_format_price(stop) if stop else "—"}</td>
  <td class="num" style="color:{ret_col};font-weight:700">{_format_pct(exp_ret)}</td>
  <td class="num">{rr:.2f}x</td>
  <td class="tc-center tc-dim">{horizon}</td>
</tr>"""
        scorecard_html = f"""
<div class="ir-sub-title">Full Signal Scorecard</div>
<div class="ir-table-wrap" style="overflow-x:auto">
  <table class="ir-table">
    <thead><tr>
      <th>Ticker</th><th>Signal</th><th>Type</th><th>Direction</th>
      <th class="num">Strength</th><th class="tc-center">Conviction</th>
      <th class="num">Entry</th><th class="num">Target</th>
      <th class="num">Stop</th><th class="num">Exp. Ret.</th>
      <th class="num">R/R</th><th class="tc-center">Horizon</th>
    </tr></thead>
    <tbody>{rows_h}</tbody>
  </table>
</div>"""

    # Data sources
    sources = [
        ("Freight Rates",     "Freightos Baltic Index (FBX), Baltic Exchange"),
        ("Macroeconomic",     "US Federal Reserve Economic Data (FRED API)"),
        ("Trade Flows",       "UN Comtrade, World Bank WITS"),
        ("Shipping Equities", "Yahoo Finance (yfinance)"),
        ("Port AIS Data",     "MarineTraffic / AIS aggregation"),
        ("Port Registry",     "World Port Index (UKHO)"),
        ("News & Sentiment",  "Ship Tracker NLP Pipeline"),
        ("Alpha Engine",      "Ship Tracker Multi-Factor Alpha Engine v2"),
    ]
    src_rows = "".join(
        f'<tr><td class="tc-name">{s}</td>'
        f'<td style="color:{C_TEXT3}">{d}</td></tr>'
        for s, d in sources
    )
    sources_table = f"""
<div class="ir-sub-title" style="margin-top:32px">Data Sources</div>
<div class="ir-table-wrap">
  <table class="ir-table">
    <thead><tr><th>Data Type</th><th>Source / Provider</th></tr></thead>
    <tbody>{src_rows}</tbody>
  </table>
</div>"""

    # Disclaimer
    disclaimer_text = getattr(report.ai, "disclaimer", None) or (
        "This report has been prepared by the Ship Tracker Intelligence Platform for "
        "informational purposes only. It does not constitute financial, investment, legal, "
        "or tax advice, and should not be relied upon as such. Past performance is not "
        "indicative of future results. All alpha signals, price targets, and return "
        "estimates are model-generated and carry inherent uncertainty. Shipping markets "
        "are subject to geopolitical, macroeconomic, and regulatory risks that may "
        "materially affect outcomes. Recipients should conduct their own due diligence "
        "and consult qualified advisors before making investment decisions. This document "
        "is confidential and intended solely for the named institutional recipient. "
        "Redistribution without written consent is prohibited."
    )

    return f"""
<div class="ir-section">
  {_section_header_html(10, "APPENDIX &amp; DISCLAIMER", "Full Signal Scorecard · Data Sources · Legal")}
  {scorecard_html}
  {sources_table}
  <div class="ir-sub-title" style="margin-top:32px">Legal Disclaimer</div>
  <div class="ir-disclaimer-box">{disclaimer_text}</div>
  <div style="margin-top:32px;padding-top:20px;border-top:1px solid {C_BORDER};
              font-size:11px;color:{C_TEXT3};text-align:center;line-height:1.8">
    Generated by <strong style="color:{C_TEXT2}">Ship Tracker Intelligence Platform</strong>
    &nbsp;&mdash;&nbsp;{report.generated_at}<br>
    Data: yfinance &bull; FRED &bull; World Bank &bull; Freightos FBX
    &bull; MarineTraffic AIS
  </div>
</div>"""


# ── Public API ────────────────────────────────────────────────────────────────

def render_investor_report_html(report: "InvestorReport") -> str:
    """Return a complete, self-contained HTML string for the investor report.

    Parameters
    ----------
    report : InvestorReport
        Fully-populated InvestorReport dataclass.

    Returns
    -------
    str
        Standalone HTML document with all CSS embedded. No external
        dependencies — works offline and can be printed to PDF.
    """
    # Auto-fill timestamps if not set
    now = datetime.now(timezone.utc)
    try:
        if not report.report_date:
            report.report_date = now.strftime("%B %d, %Y")
    except Exception:
        pass
    try:
        if not report.generated_at:
            report.generated_at = now.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        pass

    # Sort market insights by score descending (in-place on the list object)
    try:
        insights = getattr(report.market, "top_insights", None)
        if insights:
            report.market.top_insights = sorted(
                insights,
                key=lambda i: _safe_float(_safe_attr(i, "score", default=0.0)),
                reverse=True,
            )
    except Exception:
        pass

    # Sort recommendations by rank key
    try:
        recs = getattr(report.ai, "top_recommendations", None)
        if recs:
            report.ai.top_recommendations = sorted(
                recs,
                key=lambda r: r.get("rank", 999) if isinstance(r, dict) else getattr(r, "rank", 999),
            )
    except Exception:
        pass

    body = (
        _cover_page(report)
        + _executive_summary(report)
        + _sentiment_section(report)
        + _alpha_section(report)
        + _market_intelligence_section(report)
        + _freight_section(report)
        + _macro_section(report)
        + _stocks_section(report)
        + _recommendations_section(report)
        + _appendix(report)
    )

    date_str = report.report_date

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Global Shipping Intelligence Report \u2014 {date_str}</title>
  <style>
{_css()}
  </style>
</head>
<body>
{body}
</body>
</html>"""


def get_report_bytes(html_str: str) -> bytes:
    """Encode the HTML report string to UTF-8 bytes for download."""
    return html_str.encode("utf-8")
