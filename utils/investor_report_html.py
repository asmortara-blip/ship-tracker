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


# ── Color palette (mirrors ui/styles.py) ─────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"   # green  — bullish / positive
C_MOD     = "#f59e0b"   # amber  — moderate / caution
C_LOW     = "#ef4444"   # red    — bearish / negative
C_ACCENT  = "#3b82f6"   # blue   — primary accent
C_CONV    = "#8b5cf6"   # purple — convergence / signals
C_MACRO   = "#06b6d4"   # cyan   — macro data
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

_SENTIMENT_COLORS = {
    "BULLISH": C_HIGH,
    "BEARISH": C_LOW,
    "NEUTRAL": C_TEXT2,
    "MIXED":   C_MOD,
}

_CONVICTION_COLORS = {
    "HIGH":   C_HIGH,
    "MEDIUM": C_MOD,
    "LOW":    C_TEXT2,
}

_ACTION_COLORS = {
    "BUY":     C_HIGH,
    "LONG":    C_HIGH,
    "SELL":    C_LOW,
    "SHORT":   C_LOW,
    "HOLD":    C_MOD,
    "MONITOR": C_ACCENT,
    "AVOID":   C_LOW,
    "WATCH":   C_TEXT2,
}

_RISK_COLORS = {
    "LOW":      C_HIGH,
    "MODERATE": C_MOD,
    "HIGH":     C_LOW,
    "CRITICAL": "#b91c1c",
}

_CATEGORY_COLORS = {
    "CONVERGENCE": C_CONV,
    "ROUTE":       C_ACCENT,
    "PORT_DEMAND": C_HIGH,
    "MACRO":       C_MACRO,
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
        return C_HIGH
    if pct < -0.5:
        return C_LOW
    return C_MOD


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
    track_color = "rgba(255,255,255,0.08)"
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
    """SVG semicircle gauge (-1 to +1). Needle rotates with score."""
    score = max(-1.0, min(1.0, score))
    # Map -1..+1 to 0..180 degrees (left to right across top semicircle)
    angle_deg = (score + 1.0) / 2.0 * 180.0
    # Needle: pivots at center bottom of semicircle
    cx, cy, r = 90, 90, 70
    # Angle in standard math coords: 180° = left, 0° = right, gauge goes left(-1) to right(+1)
    rad = math.radians(180.0 - angle_deg)
    nx = cx + r * math.cos(rad)
    ny = cy - r * math.sin(rad)

    # Gradient stops
    if score >= 0.25:
        needle_col = C_HIGH
    elif score <= -0.25:
        needle_col = C_LOW
    else:
        needle_col = C_MOD

    return f"""<svg width="180" height="100" viewBox="0 0 180 100" xmlns="http://www.w3.org/2000/svg"
     style="display:block;margin:0 auto">
  <defs>
    <linearGradient id="gaugeGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%"   stop-color="{C_LOW}"/>
      <stop offset="40%"  stop-color="{C_MOD}"/>
      <stop offset="100%" stop-color="{C_HIGH}"/>
    </linearGradient>
  </defs>
  <!-- Track arc -->
  <path d="M 20 90 A 70 70 0 0 1 160 90"
        fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="14"
        stroke-linecap="round"/>
  <!-- Colored arc -->
  <path d="M 20 90 A 70 70 0 0 1 160 90"
        fill="none" stroke="url(#gaugeGrad)" stroke-width="10"
        stroke-linecap="round" opacity="0.6"/>
  <!-- Needle -->
  <line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}"
        stroke="{needle_col}" stroke-width="3" stroke-linecap="round"/>
  <!-- Pivot -->
  <circle cx="{cx}" cy="{cy}" r="5" fill="{needle_col}"/>
  <!-- Labels -->
  <text x="10" y="108" fill="{C_LOW}" font-size="9"
        font-family="system-ui,sans-serif" font-weight="700">BEAR</text>
  <text x="145" y="108" fill="{C_HIGH}" font-size="9"
        font-family="system-ui,sans-serif" font-weight="700">BULL</text>
  <text x="{cx}" y="108" fill="{needle_col}" font-size="11"
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
              background:rgba(255,255,255,0.04)">
    <div style="width:{bull_pct:.1f}%;background:{C_HIGH};transition:width 0.3s"></div>
    <div style="width:{neut_pct:.1f}%;background:{C_TEXT3};transition:width 0.3s"></div>
    <div style="width:{bear_pct:.1f}%;background:{C_LOW};transition:width 0.3s"></div>
  </div>
  <div style="display:flex;gap:16px;font-size:0.73rem">
    <span style="color:{C_HIGH}">&#9632; Bullish {bullish} ({bull_pct:.0f}%)</span>
    <span style="color:{C_TEXT3}">&#9632; Neutral {neutral} ({neut_pct:.0f}%)</span>
    <span style="color:{C_LOW}">&#9632; Bearish {bearish} ({bear_pct:.0f}%)</span>
  </div>
</div>"""


def _badge(text: str, color: str, bg: str = None) -> str:
    bg_val = bg if bg else _hex_to_rgba(color, 0.15)
    border = _hex_to_rgba(color, 0.30)
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:999px;'
        f'font-size:0.68rem;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:0.06em;background:{bg_val};color:{color};'
        f'border:1px solid {border};white-space:nowrap">{text}</span>'
    )


def _stat_box(label: str, value: str, sub: str = "", color: str = None) -> str:
    val_color = color if color else C_TEXT
    return (
        f'<div class="ir-stat-box">'
        f'<div class="ir-stat-label">{label}</div>'
        f'<div class="ir-stat-value" style="color:{val_color}">{value}</div>'
        f'{"<div class=ir-stat-sub>" + sub + "</div>" if sub else ""}'
        f'</div>'
    )


def _section_header(title: str, color: str = C_ACCENT) -> str:
    return f"""
<div class="ir-section-header">
  <div class="ir-section-eyebrow" style="color:{color}">&#9632;</div>
  <div>
    <div class="ir-section-title">{title}</div>
    <div class="ir-section-rule" style="background:{color}"></div>
  </div>
</div>"""


def _change_html(pct: float) -> str:
    col = _color_for_change(pct)
    arrow = "&#9650;" if pct >= 0 else "&#9660;"
    return f'<span style="color:{col};font-weight:600">{arrow} {_format_pct(pct)}</span>'


# ── CSS ───────────────────────────────────────────────────────────────────────

def _css() -> str:
    return f"""
/* ── Reset & base ── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

html {{ font-size: 14px; scroll-behavior: smooth; }}

body {{
    background: {C_BG};
    color: {C_TEXT};
    font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto,
                 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}}

a {{ color: {C_ACCENT}; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

code, .mono {{
    font-family: 'JetBrains Mono', 'Courier New', Courier, monospace;
    font-size: 0.88em;
}}

/* ── Page layout ── */
.ir-page {{
    max-width: 1100px;
    margin: 0 auto;
    padding: 0 32px 64px;
}}

/* ── Cover page ── */
.ir-cover {{
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    text-align: center;
    padding: 80px 40px;
    background: linear-gradient(160deg, #070d18 0%, #0d1829 35%, #0f1d35 65%, #0a0f1a 100%);
    position: relative;
    overflow: hidden;
    page-break-after: always;
}}
.ir-cover::before {{
    content: '';
    position: absolute;
    top: -100px; right: -100px;
    width: 500px; height: 500px;
    background: radial-gradient(circle, rgba(59,130,246,0.10) 0%, transparent 60%);
    pointer-events: none;
}}
.ir-cover::after {{
    content: '';
    position: absolute;
    bottom: -80px; left: 10%;
    width: 400px; height: 400px;
    background: radial-gradient(circle, rgba(139,92,246,0.07) 0%, transparent 60%);
    pointer-events: none;
}}
.ir-cover-logo-line {{
    font-size: 0.72rem;
    font-weight: 700;
    color: {C_ACCENT};
    text-transform: uppercase;
    letter-spacing: 0.25em;
    margin-bottom: 40px;
    opacity: 0.9;
}}
.ir-cover-title {{
    font-size: clamp(1.8rem, 4vw, 3.0rem);
    font-weight: 900;
    color: {C_TEXT};
    line-height: 1.1;
    letter-spacing: -0.03em;
    margin-bottom: 16px;
    text-shadow: 0 2px 24px rgba(59,130,246,0.2);
}}
.ir-cover-subtitle {{
    font-size: 1.0rem;
    color: {C_TEXT2};
    margin-bottom: 48px;
    letter-spacing: 0.02em;
    max-width: 560px;
}}
.ir-cover-date {{
    font-size: 0.90rem;
    color: {C_TEXT3};
    margin-bottom: 40px;
    font-weight: 500;
}}
.ir-cover-sentiment-block {{
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 20px;
    padding: 28px 48px;
    margin-bottom: 40px;
    display: inline-block;
    min-width: 340px;
}}
.ir-cover-sentiment-label {{
    font-size: 0.65rem;
    font-weight: 700;
    color: {C_TEXT3};
    text-transform: uppercase;
    letter-spacing: 0.15em;
    margin-bottom: 12px;
}}
.ir-cover-sentiment-badge {{
    font-size: 1.7rem;
    font-weight: 900;
    letter-spacing: 0.05em;
    margin-bottom: 16px;
    text-transform: uppercase;
}}
.ir-cover-data-quality {{
    font-size: 0.75rem;
    color: {C_TEXT3};
    margin-bottom: 48px;
}}
.ir-cover-confidential {{
    font-size: 0.65rem;
    font-weight: 700;
    color: {C_TEXT3};
    text-transform: uppercase;
    letter-spacing: 0.20em;
    margin-top: auto;
    padding-top: 40px;
    border-top: 1px solid rgba(255,255,255,0.06);
    width: 100%;
    max-width: 560px;
}}
.ir-cover-timestamp {{
    font-size: 0.68rem;
    color: {C_TEXT3};
    margin-top: 8px;
    font-family: 'JetBrains Mono', 'Courier New', monospace;
}}

/* ── Section structure ── */
.ir-section {{
    padding: 56px 0 0;
    page-break-before: always;
}}
.ir-section-inner {{
    max-width: 1100px;
    margin: 0 auto;
    padding: 0 32px 48px;
}}
.ir-section-header {{
    display: flex;
    align-items: flex-start;
    gap: 12px;
    margin-bottom: 36px;
}}
.ir-section-eyebrow {{
    font-size: 1.2rem;
    margin-top: 2px;
    flex-shrink: 0;
}}
.ir-section-title {{
    font-size: 1.5rem;
    font-weight: 800;
    color: {C_TEXT};
    letter-spacing: -0.02em;
    line-height: 1.2;
}}
.ir-section-rule {{
    height: 3px;
    width: 48px;
    border-radius: 2px;
    margin-top: 8px;
}}

/* ── Stat boxes ── */
.ir-stat-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
}}
.ir-stat-box {{
    background: {C_CARD};
    border: 1px solid {C_BORDER};
    border-radius: 14px;
    padding: 20px 22px;
    position: relative;
    overflow: hidden;
}}
.ir-stat-box::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, {C_ACCENT}, transparent);
    opacity: 0.5;
}}
.ir-stat-label {{
    font-size: 0.65rem;
    font-weight: 700;
    color: {C_TEXT3};
    text-transform: uppercase;
    letter-spacing: 0.10em;
    margin-bottom: 8px;
}}
.ir-stat-value {{
    font-size: 1.9rem;
    font-weight: 800;
    color: {C_TEXT};
    line-height: 1;
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    margin-bottom: 4px;
}}
.ir-stat-sub {{
    font-size: 0.73rem;
    color: {C_TEXT3};
}}

/* ── Cards ── */
.ir-card {{
    background: {C_CARD};
    border: 1px solid {C_BORDER};
    border-radius: 14px;
    padding: 24px 28px;
    margin-bottom: 14px;
}}
.ir-card-accent {{
    border-left: 4px solid {C_ACCENT};
}}
.ir-card-highlight {{
    background: linear-gradient(135deg, {C_CARD}, #141e30);
    border: 1px solid rgba(59,130,246,0.20);
    box-shadow: 0 0 32px rgba(59,130,246,0.06);
}}
.ir-card-green-glow {{
    background: linear-gradient(135deg, {C_CARD}, #0d2318);
    border: 1px solid rgba(16,185,129,0.25);
    box-shadow: 0 0 32px rgba(16,185,129,0.08);
}}

/* ── Prose ── */
.ir-prose {{
    font-size: 0.93rem;
    color: {C_TEXT2};
    line-height: 1.8;
}}
.ir-prose p {{
    margin-bottom: 1em;
}}
.ir-prose p:last-child {{
    margin-bottom: 0;
}}
.ir-prose-highlight {{
    background: rgba(59,130,246,0.06);
    border: 1px solid rgba(59,130,246,0.15);
    border-left: 4px solid {C_ACCENT};
    border-radius: 10px;
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
    border-bottom: 1px solid rgba(255,255,255,0.04);
}}
.ir-score-row:last-child {{ border-bottom: none; }}
.ir-score-label {{
    font-size: 0.80rem;
    color: {C_TEXT2};
    font-weight: 600;
}}

/* ── Tables ── */
.ir-table-wrap {{
    border-radius: 14px;
    overflow: hidden;
    border: 1px solid {C_BORDER};
    margin-bottom: 24px;
}}
table.ir-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
}}
table.ir-table th {{
    background: rgba(59,130,246,0.12);
    color: {C_ACCENT};
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 0.65rem;
    padding: 11px 16px;
    text-align: left;
    border-bottom: 1px solid rgba(59,130,246,0.20);
    white-space: nowrap;
}}
table.ir-table td {{
    padding: 11px 16px;
    color: {C_TEXT2};
    border-bottom: 1px solid rgba(255,255,255,0.04);
    vertical-align: middle;
}}
table.ir-table tr:last-child td {{ border-bottom: none; }}
table.ir-table tr:nth-child(even) td {{
    background: rgba(255,255,255,0.016);
}}
table.ir-table tr:hover td {{
    background: rgba(59,130,246,0.05);
    color: {C_TEXT};
}}
table.ir-table .tc-right {{ text-align: right; }}
table.ir-table .tc-center {{ text-align: center; }}
table.ir-table .tc-name {{
    color: {C_TEXT};
    font-weight: 600;
}}
table.ir-table .tc-ticker {{
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    color: {C_ACCENT};
    font-weight: 700;
}}
table.ir-table .tc-mono {{
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 0.80rem;
}}
table.ir-table .tc-dim {{ color: {C_TEXT3}; font-size: 0.78rem; }}
table.ir-table .tc-rank {{
    color: {C_TEXT3};
    font-weight: 700;
    text-align: center;
    width: 36px;
}}

/* ── Signal tables (compact) ── */
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
    border-bottom: 1px solid rgba(255,255,255,0.06);
    text-align: left;
}}
table.ir-signal-table td {{
    padding: 5px 8px;
    color: {C_TEXT2};
    border-bottom: 1px solid rgba(255,255,255,0.03);
}}
table.ir-signal-table tr:last-child td {{ border-bottom: none; }}

/* ── Macro table header variant ── */
table.ir-macro-table th {{
    background: rgba(6,182,212,0.10);
    color: {C_MACRO};
    border-bottom-color: rgba(6,182,212,0.18);
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
    font-size: 0.73rem;
    font-weight: 600;
    background: rgba(59,130,246,0.10);
    border: 1px solid rgba(59,130,246,0.20);
    color: {C_ACCENT};
    white-space: nowrap;
}}

/* ── Recommendation cards ── */
.ir-rec-card {{
    background: {C_CARD};
    border: 1px solid {C_BORDER};
    border-radius: 14px;
    padding: 22px 26px;
    margin-bottom: 16px;
    display: grid;
    grid-template-columns: 48px 1fr;
    gap: 18px;
    align-items: start;
}}
.ir-rec-rank {{
    width: 48px;
    height: 48px;
    border-radius: 50%;
    background: rgba(59,130,246,0.12);
    border: 2px solid rgba(59,130,246,0.30);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.1rem;
    font-weight: 900;
    color: {C_ACCENT};
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    flex-shrink: 0;
}}
.ir-rec-title {{
    font-size: 1.05rem;
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
.ir-rec-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 10px;
    margin-bottom: 14px;
}}
.ir-rec-kv {{
    background: rgba(255,255,255,0.03);
    border-radius: 8px;
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
    font-family: 'JetBrains Mono', 'Courier New', monospace;
}}
.ir-rec-rationale {{
    font-size: 0.83rem;
    color: {C_TEXT2};
    line-height: 1.7;
    border-top: 1px solid rgba(255,255,255,0.05);
    padding-top: 12px;
    margin-top: 4px;
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
    border-radius: 14px;
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
    color: {C_ACCENT};
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    line-height: 1;
}}
.ir-stock-name {{
    font-size: 0.73rem;
    color: {C_TEXT3};
    margin-top: 3px;
}}
.ir-stock-price {{
    text-align: right;
}}
.ir-stock-price-val {{
    font-size: 1.3rem;
    font-weight: 800;
    color: {C_TEXT};
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    line-height: 1;
}}
.ir-stock-price-chg {{
    font-size: 0.78rem;
    font-weight: 700;
    margin-top: 4px;
}}

/* ── Insight cards ── */
.ir-insight-card {{
    background: {C_CARD};
    border: 1px solid {C_BORDER};
    border-radius: 14px;
    padding: 22px 26px;
    margin-bottom: 14px;
    border-left: 4px solid {C_ACCENT};
}}
.ir-insight-header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
    margin-bottom: 10px;
}}
.ir-insight-title {{
    font-size: 1.0rem;
    font-weight: 700;
    color: {C_TEXT};
    line-height: 1.3;
    margin-top: 8px;
}}
.ir-insight-detail {{
    font-size: 0.85rem;
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
    font-family: 'JetBrains Mono', 'Courier New', monospace;
}}
.ir-insight-meta-row {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
    margin-bottom: 14px;
}}
.ir-insight-footer {{
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    font-size: 0.75rem;
    color: {C_TEXT3};
    margin-top: 10px;
}}

/* ── Disclaimer ── */
.ir-disclaimer-box {{
    background: rgba(100,116,139,0.08);
    border: 1px solid rgba(100,116,139,0.20);
    border-radius: 12px;
    padding: 20px 24px;
    font-size: 0.78rem;
    color: {C_TEXT3};
    line-height: 1.7;
}}

/* ── Divider ── */
.ir-divider {{
    height: 1px;
    background: {C_BORDER};
    margin: 28px 0;
}}

/* ── Sub-section title ── */
.ir-sub-title {{
    font-size: 0.68rem;
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
@media (max-width: 700px) {{
    .ir-two-col {{ grid-template-columns: 1fr; }}
    .ir-stock-grid {{ grid-template-columns: 1fr; }}
    .ir-rec-card {{ grid-template-columns: 1fr; }}
}}

/* ── Print styles ── */
@media print {{
    body {{
        background: #ffffff !important;
        color: #111111 !important;
    }}
    .ir-cover {{
        background: #ffffff !important;
        color: #111111 !important;
        min-height: auto;
        padding: 60px 40px;
    }}
    .ir-cover-title, .ir-section-title, .ir-insight-title, .ir-rec-title,
    .ir-stock-ticker, .ir-stat-value {{ color: #111111 !important; }}
    .ir-cover-subtitle, .ir-prose, .ir-insight-detail, .ir-rec-rationale,
    .ir-stat-label, .ir-stat-sub {{ color: #444444 !important; }}
    .ir-card, .ir-insight-card, .ir-rec-card, .ir-stock-card,
    .ir-stat-box, .ir-cover-sentiment-block {{
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
    .ir-section {{ page-break-before: always; padding-top: 32px; }}
    .ir-cover {{ page-break-after: always; }}
    .ir-keyword-pill {{
        background: #eee !important;
        border-color: #ccc !important;
        color: #333 !important;
    }}
    a {{ color: #1a56db !important; }}
    .ir-prose-highlight {{
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
        "FULL":     C_HIGH,
        "PARTIAL":  C_MOD,
        "DEGRADED": C_LOW,
    }.get(getattr(report, "data_quality", "FULL"), C_TEXT2)
    dq_label = getattr(report, "data_quality", "FULL") or "FULL"

    gauge_svg = _sentiment_gauge_svg(overall_score)

    return f"""
<div class="ir-cover">
  <div style="z-index:1;width:100%;max-width:680px;display:flex;flex-direction:column;
              align-items:center">
    <div class="ir-cover-logo-line">&#9632; Ship Tracker &nbsp;&mdash;&nbsp;
      Global Intelligence Platform</div>

    <div class="ir-cover-title">Global Shipping Intelligence Report</div>
    <div class="ir-cover-subtitle">
      Institutional Sentiment Analysis &amp; Alpha Signal Briefing
    </div>
    <div class="ir-cover-date">{getattr(report, 'report_date', '')}</div>

    <div class="ir-cover-sentiment-block">
      <div class="ir-cover-sentiment-label">Overall Market Sentiment</div>
      <div class="ir-cover-sentiment-badge" style="color:{sent_color}">
        {overall_label}
      </div>
      {gauge_svg}
    </div>

    <div class="ir-cover-data-quality">
      Data Quality:&nbsp;
      <span style="color:{dq_color};font-weight:700">{dq_label}</span>
      &nbsp;&bull;&nbsp;Sentiment Score:&nbsp;
      <span style="color:{sent_color};font-weight:700;
            font-family:'JetBrains Mono','Courier New',monospace">
        {overall_score:+.3f}
      </span>
    </div>

    <div class="ir-cover-confidential">
      Confidential &mdash; For Institutional Use Only<br>
      Not for redistribution. Past performance does not guarantee future results.
      <div class="ir-cover-timestamp">Generated: {getattr(report, 'generated_at', '')}</div>
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

    risk_color   = _RISK_COLORS.get(risk_level, C_MOD)
    sent_color   = _SENTIMENT_COLORS.get(overall_label, C_TEXT2)

    n_signals    = len(signals)
    n_long       = sum(1 for s in signals if _safe_attr(s, "direction") == "LONG")
    n_short      = sum(1 for s in signals if _safe_attr(s, "direction") == "SHORT")
    exp_return   = _safe_float(portfolio.get("expected_return", 0.0))
    top_insight  = insights[0] if insights else None
    top_opp_str  = _safe_attr(top_insight, "title", default="—") if top_insight else "—"

    stats_html = f"""
<div class="ir-stat-grid">
  {_stat_box("Sentiment Score", f"{overall_score:+.3f}",
             sub=overall_label, color=sent_color)}
  {_stat_box("Alpha Signals", str(n_signals),
             sub=f"{n_long}L / {n_short}S", color=C_ACCENT)}
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

    # Key findings (top 3 insights)
    top3 = insights[:3]
    findings_html = ""
    for ins in top3:
        title  = _safe_attr(ins, "title", default="Signal detected")
        detail = _safe_attr(ins, "detail", default="")
        detail_short = detail[:180].rstrip(".") + ("…" if len(detail) > 180 else "") if detail else ""
        cat    = _safe_attr(ins, "category", default="")
        cat_color = _CATEGORY_COLORS.get(cat, C_ACCENT)
        findings_html += f"""
<div style="display:flex;gap:12px;padding:12px 0;
            border-bottom:1px solid rgba(255,255,255,0.05)">
  <span style="color:{cat_color};font-weight:700;flex-shrink:0;margin-top:2px">&#8250;</span>
  <div>
    <div style="font-size:0.88rem;font-weight:700;color:{C_TEXT};margin-bottom:3px">
      {title}
    </div>
    <div style="font-size:0.82rem;color:{C_TEXT2};line-height:1.6">{detail_short}</div>
  </div>
</div>"""

    top_opp_text = f"""
<div style="margin-top:20px">
  <div class="ir-sub-title">Top Opportunity</div>
  <div style="font-size:0.93rem;color:{C_HIGH};font-weight:600;line-height:1.5">
    {top_opp_str}
  </div>
</div>""" if top_opp_str != "—" else ""

    return f"""
<div class="ir-section">
  <div class="ir-section-inner">
    {_section_header("EXECUTIVE SUMMARY", C_ACCENT)}
    {stats_html}
    <div class="ir-card ir-card-highlight">
      <div class="ir-prose">{prose_html}</div>
      {top_opp_text}
    </div>
    {"<div class='ir-sub-title' style='margin-top:24px'>Key Findings</div>" + findings_html if findings_html else ""}
  </div>
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
        ("News Sentiment",    _sb_float("news_score"),    C_ACCENT),
        ("Freight Momentum",  _sb_float("freight_score"), C_MACRO),
        ("Macro Backdrop",    _sb_float("macro_score"),   C_MOD),
        ("Alpha Signals",     _sb_float("alpha_score"),   C_CONV),
    ]

    def _norm(v: float) -> float:
        """Map [-1,+1] to [0,1] for bar width."""
        return (v + 1.0) / 2.0

    score_bars_html = ""
    for label, val, color in score_rows:
        norm = _norm(_safe_float(val))
        direction = "BULLISH" if val > 0.1 else ("BEARISH" if val < -0.1 else "NEUTRAL")
        d_color = C_HIGH if val > 0.1 else (C_LOW if val < -0.1 else C_TEXT2)
        score_bars_html += f"""
<div class="ir-score-row">
  <div class="ir-score-label">{label}</div>
  <div>{_score_bar_svg(norm, color, 260, 8)}</div>
  <div style="font-size:0.75rem;color:{d_color};font-weight:700;min-width:60px;
              text-align:right">{val:+.3f}</div>
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
  <td class="tc-right tc-mono">{mentions}</td>
  <td class="tc-right" style="color:{sent_col};font-weight:700">{sent_str}</td>
</tr>"""
        topics_html = f"""
<div class="ir-sub-title" style="margin-top:24px">Trending Topics</div>
<div class="ir-table-wrap">
  <table class="ir-table">
    <thead><tr><th>Topic</th><th class="tc-right">Mentions</th>
    <th class="tc-right">Sentiment</th></tr></thead>
    <tbody>{rows_h}</tbody>
  </table>
</div>"""

    # Top headlines — from report.news_items (NewsArticle objects)
    headlines_html = ""
    try:
        news_items = list(getattr(report, "news_items", []) or [])
    except Exception:
        news_items = []
    if news_items:
        for hl in news_items[:5]:
            # Support both NewsArticle objects (with attributes) and plain dicts
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
            h_col    = _color_for_change(_safe_float(h_sent)) if h_sent is not None else C_TEXT3
            h_badge  = _badge(
                "BULL" if _safe_float(h_sent) > 0.1 else
                ("BEAR" if _safe_float(h_sent) < -0.1 else "NEUT"),
                h_col
            )
            headlines_html += f"""
<div style="display:flex;justify-content:space-between;align-items:flex-start;
            padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.04);gap:12px">
  <div>
    <div style="font-size:0.85rem;color:{C_TEXT};font-weight:600;
                line-height:1.4;margin-bottom:4px">{h_title}</div>
    <div style="font-size:0.73rem;color:{C_TEXT3}">{h_source}
      {"&nbsp;&bull;&nbsp;" + str(h_date) if h_date else ""}</div>
  </div>
  <div style="flex-shrink:0">{h_badge}</div>
</div>"""

    # AI narrative prose
    narrative_text = getattr(report.ai, "sentiment_narrative", "") or ""
    narrative_paras = [p.strip() for p in narrative_text.split("\n\n") if p.strip()]
    narrative_html  = "".join(f"<p>{p}</p>" for p in narrative_paras)

    return f"""
<div class="ir-section">
  <div class="ir-section-inner">
    {_section_header("MARKET SENTIMENT ANALYSIS", C_ACCENT)}

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

    {"<div class='ir-sub-title' style='margin-top:24px'>Top News Headlines</div>" + headlines_html if headlines_html else ""}

    {f'<div class="ir-prose ir-prose-highlight" style="margin-top:28px">{narrative_html}</div>' if narrative_html else ""}
  </div>
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
<div class="ir-stat-grid" style="margin-bottom:28px">
  {_stat_box("Expected Return", _format_pct(exp_ret), color=_color_for_change(exp_ret))}
  {_stat_box("Sharpe Ratio", f"{sharpe:.2f}",
             color=C_HIGH if sharpe > 1 else (C_MOD if sharpe > 0 else C_LOW))}
  {_stat_box("Portfolio Vol", f"{port_vol:.1f}%", color=C_TEXT2)}
  {_stat_box("Max DD Est.", f"-{max_dd:.1f}%", color=C_LOW)}
</div>"""

    # Conviction breakdown mini bar chart (CSS)
    high_n   = sum(1 for s in signals if _safe_attr(s, "conviction") == "HIGH")
    med_n    = sum(1 for s in signals if _safe_attr(s, "conviction") == "MEDIUM")
    low_n    = sum(1 for s in signals if _safe_attr(s, "conviction") == "LOW")
    total_n  = len(signals) or 1

    conv_html = f"""
<div class="ir-sub-title">Signal Conviction Distribution</div>
<div style="display:flex;flex-direction:column;gap:8px;margin-bottom:24px">
  {_conv_bar("HIGH",   high_n, total_n, C_HIGH)}
  {_conv_bar("MEDIUM", med_n,  total_n, C_MOD)}
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
            "MOMENTUM":       C_HIGH,
            "MEAN_REVERSION": C_CONV,
            "FUNDAMENTAL":    C_ACCENT,
            "MACRO":          C_MACRO,
            "TECHNICAL":      C_MOD,
        }
        for st, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            col = type_colors.get(st, C_TEXT2)
            type_html += _conv_bar(st, cnt, total_n, col)
        type_html += "</div>"

    # LONG signals table
    long_signals  = [s for s in signals if _safe_attr(s, "direction") == "LONG"]
    short_signals = [s for s in signals if _safe_attr(s, "direction") == "SHORT"]

    long_table  = _signal_table("LONG Signals", long_signals, C_HIGH)   if long_signals  else ""
    short_table = _signal_table("SHORT Signals", short_signals, C_LOW)  if short_signals else ""

    # AI narrative
    opp_text  = getattr(report.ai, "opportunity_narrative", "") or ""
    opp_paras = [p.strip() for p in opp_text.split("\n\n") if p.strip()]
    opp_html  = "".join(f"<p>{p}</p>" for p in opp_paras)

    return f"""
<div class="ir-section">
  <div class="ir-section-inner">
    {_section_header("ALPHA SIGNAL INTELLIGENCE", C_CONV)}
    {port_stats}
    <div class="ir-two-col">
      <div>{conv_html}</div>
      <div>{type_html}</div>
    </div>
    {long_table}
    {short_table}
    {f'<div class="ir-prose ir-prose-highlight" style="margin-top:24px">{opp_html}</div>'
     if opp_html else ""}
  </div>
</div>"""


def _conv_bar(label: str, count: int, total: int, color: str) -> str:
    pct = count / total * 100 if total else 0
    return f"""
<div style="display:flex;align-items:center;gap:12px">
  <div style="font-size:0.73rem;color:{C_TEXT2};font-weight:600;min-width:100px">{label}</div>
  <div style="flex:1;height:8px;background:rgba(255,255,255,0.06);
              border-radius:4px;overflow:hidden">
    <div style="width:{pct:.1f}%;height:100%;background:{color};border-radius:4px"></div>
  </div>
  <div style="font-size:0.73rem;color:{C_TEXT3};min-width:28px;
              font-family:'JetBrains Mono','Courier New',monospace">{count}</div>
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
  <td class="tc-right tc-mono">{entry_s}</td>
  <td class="tc-right tc-mono">{target_s}</td>
  <td class="tc-right tc-mono">{stop_s}</td>
  <td class="tc-right" style="color:{ret_col};font-weight:700">{_format_pct(exp_ret)}</td>
  <td class="tc-center tc-dim">{horizon}</td>
</tr>"""

    return f"""
<div class="ir-sub-title" style="color:{header_color}">{title}</div>
<div class="ir-table-wrap" style="margin-bottom:20px">
  <table class="ir-table">
    <thead><tr>
      <th>Signal</th><th>Ticker</th><th>Strength</th><th class="tc-center">Conviction</th>
      <th class="tc-right">Entry</th><th class="tc-right">Target</th>
      <th class="tc-right">Stop</th><th class="tc-right">Exp. Ret.</th>
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

    risk_color  = _RISK_COLORS.get(risk_level, C_MOD)

    # Risk level header
    risk_html = f"""
<div style="display:flex;align-items:center;gap:12px;margin-bottom:28px">
  <div style="font-size:0.82rem;color:{C_TEXT2}">Market Risk Level:</div>
  {_badge(risk_level, risk_color)}
</div>"""

    # Top insights cards
    cards_html = ""
    for ins in insights[:5]:
        cat    = _safe_str(_safe_attr(ins, "category", default="ROUTE"))
        action = _safe_str(_safe_attr(ins, "action",   default="Monitor"))
        score  = _safe_float(_safe_attr(ins, "score", default=0.5))
        title  = _safe_str(_safe_attr(ins, "title",  default="Signal"))
        detail = _safe_str(_safe_attr(ins, "detail", default=""))
        ports  = _safe_attr(ins, "ports_involved",  default=[]) or []
        routes = _safe_attr(ins, "routes_involved", default=[]) or []
        stocks = _safe_attr(ins, "stocks_potentially_affected", default=[]) or []
        sigs   = _safe_attr(ins, "supporting_signals", default=[]) or []
        stale  = _safe_attr(ins, "data_freshness_warning", default=False)

        cat_color  = _CATEGORY_COLORS.get(cat, C_ACCENT)
        score_col  = C_HIGH if score >= 0.70 else (C_MOD if score >= 0.40 else C_LOW)
        act_color  = _ACTION_COLORS.get(action, C_TEXT2)
        score_pct  = f"{score * 100:.0f}%"

        stale_badge = ("&nbsp;" + _badge("Stale Data", C_MOD)) if stale else ""

        # Supporting signals mini-list
        sig_items = ""
        for sg in sigs[:4]:
            sg_name = _safe_str(_safe_attr(sg, "name", default="Signal"))
            sg_dir  = _safe_str(_safe_attr(sg, "direction", default="neutral"))
            sg_val  = _safe_float(_safe_attr(sg, "value", default=0.0))
            sg_col  = C_HIGH if sg_dir == "bullish" else (C_LOW if sg_dir == "bearish" else C_TEXT3)
            arrow   = "&#9650;" if sg_dir == "bullish" else ("&#9660;" if sg_dir == "bearish" else "&#8594;")
            sig_items += (
                f'<div style="font-size:0.78rem;color:{C_TEXT2};padding:2px 0">'
                f'<span style="color:{sg_col}">{arrow}</span> {sg_name} '
                f'<span style="color:{C_TEXT3};font-family:monospace">{sg_val:.2f}</span>'
                f'</div>'
            )

        ports_txt  = ", ".join(ports)  or "—"
        routes_txt = ", ".join(routes) or "—"
        stocks_txt = ", ".join(stocks) or "—"

        cards_html += f"""
<div class="ir-insight-card" style="border-left-color:{cat_color}">
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

    # Port summary table (from insights metadata)
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
            d_col      = C_HIGH if d_score >= 0.70 else (C_MOD if d_score >= 0.40 else C_LOW)
            s_col      = _RISK_COLORS.get(str(status).upper(), C_TEXT2)

            cong_html = "—"
            if congestion is not None:
                cval = _safe_float(congestion)
                c_col = C_LOW if cval >= 0.6 else (C_MOD if cval >= 0.35 else C_HIGH)
                cong_html = (
                    f'<span style="color:{c_col};font-weight:600">{cval:.0%}</span>'
                )

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

    # Route opportunities table (from insights)
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
            s_col  = C_HIGH if score >= 0.70 else (C_MOD if score >= 0.40 else C_LOW)
            rate_s = _format_price(rate) if rate is not None else "—"
            chg_s  = _change_html(_safe_float(chg30)) if chg30 is not None else "—"
            rows_h += f"""<tr>
  <td class="tc-rank">#{rank}</td>
  <td class="tc-name">{rname}</td>
  <td>{_score_bar_svg(score, s_col, 80, 6)}</td>
  <td class="tc-right tc-mono">{rate_s}</td>
  <td class="tc-right">{chg_s}</td>
  <td class="tc-dim">{opp}</td>
</tr>"""
        route_table_html = f"""
<div class="ir-sub-title" style="margin-top:24px">Top Route Opportunities</div>
<div class="ir-table-wrap">
  <table class="ir-table">
    <thead><tr>
      <th>Rank</th><th>Route</th><th>Score</th>
      <th class="tc-right">Rate</th>
      <th class="tc-right">30d Change</th><th>Opportunity</th>
    </tr></thead>
    <tbody>{rows_h}</tbody>
  </table>
</div>"""

    return f"""
<div class="ir-section">
  <div class="ir-section-inner">
    {_section_header("MARKET INTELLIGENCE &amp; INSIGHTS", C_CONV)}
    {risk_html}
    {cards_html if cards_html else f'<p style="color:{C_TEXT3}">No insights available.</p>'}
    {port_table_html}
    {route_table_html}
  </div>
</div>"""


def _extract_port_rows(insights: list) -> list:
    """Collect unique port names from insights and synthesise summary rows."""
    seen: Dict[str, dict] = {}
    for ins in insights:
        ports = _safe_attr(ins, "ports_involved", default=[]) or []
        score = _safe_float(_safe_attr(ins, "score", default=0.5))
        cat   = _safe_attr(ins, "category", default="")
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
        C_HIGH if momentum_label == "Accelerating"
        else C_LOW if momentum_label == "Decelerating"
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
<div class="ir-stat-grid" style="margin-bottom:28px">
  {_stat_box("Momentum", f'<span style="color:{mom_col}">{arrow_sym} {momentum_label}</span>')}
  {_stat_box("FBX Composite", fbx_str, sub="$/FEU", color=C_MACRO)}
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
  <td class="tc-right tc-mono" style="color:{C_TEXT};font-weight:700">{rate_s}</td>
  <td class="tc-right">{chg30_h}</td>
  <td class="tc-right">{chg90_h}</td>
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
      <th class="tc-right">Current Rate ($/FEU)</th>
      <th class="tc-right">30d Change</th>
      <th class="tc-right">90d Change</th>
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
  <div class="ir-section-inner">
    {_section_header("FREIGHT RATE ANALYSIS", C_MACRO)}
    {summary_html}
    {table_html}
    {f'<div class="ir-prose" style="margin-top:20px">{narr_html}</div>' if narr_html else ""}
  </div>
</div>"""


def _macro_section(report: "InvestorReport") -> str:
    try:
        m = report.macro
        bdi              = getattr(m, "bdi", None)
        bdi_change_30d   = getattr(m, "bdi_change_30d_pct", None)
        wti              = getattr(m, "wti", None)
        treasury_10y     = getattr(m, "treasury_10y", None)
        supply_chain_stress = getattr(m, "supply_chain_stress", None)  # string: LOW|MODERATE|HIGH
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
    # supply_chain_stress is a string label from the engine, not a float
    scs_s      = str(supply_chain_stress) if supply_chain_stress is not None else "N/A"
    pmi_s      = _opt(pmi_proxy, ".1f")

    bdi_chg_col = _color_for_change(_safe_float(bdi_change_30d))
    # Map string stress label to a colour
    _stress_color_map = {"LOW": C_HIGH, "MODERATE": C_MOD, "HIGH": C_LOW}
    scs_col = _stress_color_map.get(str(supply_chain_stress).upper(), C_TEXT2) \
              if supply_chain_stress is not None else C_TEXT2
    pmi_col = (C_HIGH if _safe_float(pmi_proxy) > 52
               else C_LOW if _safe_float(pmi_proxy) < 48
               else C_MOD) if pmi_proxy is not None else C_TEXT2

    macro_stats = f"""
<div class="ir-stat-grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
     margin-bottom:28px">
  {_stat_box("BDI", bdi_s, color=C_MACRO)}
  {_stat_box("BDI 30d Chg", bdi_chg_s, color=bdi_chg_col)}
  {_stat_box("WTI Crude", wti_s, sub="$/bbl", color=C_TEXT2)}
  {_stat_box("10Y Treasury", tsy_s, sub="yield", color=C_TEXT2)}
  {_stat_box("SC Stress", scs_s, color=scs_col)}
  {_stat_box("PMI Proxy", pmi_s, color=pmi_col)}
</div>"""

    # No .narrative field on engine MacroSnapshot — use AI risk narrative instead
    narr_html = ""

    # Risk narrative
    risk_text  = getattr(report.ai, "risk_narrative", "") or ""
    risk_paras = [p.strip() for p in risk_text.split("\n\n") if p.strip()]
    risk_html  = "".join(f"<p>{p}</p>" for p in risk_paras)

    return f"""
<div class="ir-section">
  <div class="ir-section-inner">
    {_section_header("MACRO ENVIRONMENT", C_MACRO)}
    {macro_stats}
    {f'<div class="ir-prose">{narr_html}</div>' if narr_html else ""}
    {f'<div class="ir-prose ir-prose-highlight" style="margin-top:20px">{risk_html}</div>'
     if risk_html else ""}
  </div>
</div>"""


def _stocks_section(report: "InvestorReport") -> str:
    try:
        stocks_obj = report.stocks
        tickers         = list(getattr(stocks_obj, "tickers", []) or [])
        prices          = dict(getattr(stocks_obj, "prices", {}) or {})
        changes_30d     = dict(getattr(stocks_obj, "changes_30d", {}) or {})
        signals_by_ticker = dict(getattr(stocks_obj, "signals_by_ticker", {}) or {})
        top_ticker      = _safe_str(getattr(stocks_obj, "top_pick", ""))
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
        # Derive top_ticker from alpha signals
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
                   if chg30 is not None else '<span style="color:{C_TEXT3}">—</span>'

        glow_style = ""
        if ticker == top_ticker:
            glow_style = (
                "background:linear-gradient(135deg,#1a2235,#0d2318);"
                f"border-color:rgba(16,185,129,0.30);"
                "box-shadow:0 0 24px rgba(16,185,129,0.10);"
            )

        # Top signal badge
        top_sig_html = ""
        if top_sig is not None:
            ts_dir  = _safe_attr(top_sig, "direction", default="NEUTRAL")
            ts_conv = _safe_attr(top_sig, "conviction", default="LOW")
            ts_ret  = _safe_float(_safe_attr(top_sig, "expected_return_pct", default=0.0))
            ts_col  = C_HIGH if ts_dir == "LONG" else (C_LOW if ts_dir == "SHORT" else C_TEXT2)
            top_sig_html = (
                f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">'
                f'{_badge(ts_dir, ts_col)}'
                f'{_badge(ts_conv, _CONVICTION_COLORS.get(ts_conv, C_TEXT2))}'
                f'<span style="font-size:0.73rem;color:{ts_col};font-weight:700;align-self:center">'
                f'{_format_pct(ts_ret)}</span>'
                f'</div>'
            )

        # Mini signal list
        sig_list = ""
        for sg in sigs[:3]:
            sg_dir  = _safe_attr(sg, "direction", default="NEUTRAL")
            sg_name = _safe_attr(sg, "signal_name", default="Signal")
            sg_col  = C_HIGH if sg_dir == "LONG" else (C_LOW if sg_dir == "SHORT" else C_TEXT3)
            arrow   = "&#9650;" if sg_dir == "LONG" else ("&#9660;" if sg_dir == "SHORT" else "&#8594;")
            sig_list += (
                f'<div style="font-size:0.75rem;color:{C_TEXT2};padding:2px 0">'
                f'<span style="color:{sg_col}">{arrow}</span> {sg_name}'
                f'</div>'
            )

        top_pick_badge = (
            f'<div style="margin-bottom:8px">{_badge("★ TOP PICK", C_HIGH)}</div>'
            if ticker == top_ticker else ""
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
  <div class="ir-section-inner">
    {_section_header("SHIPPING STOCK ANALYSIS", C_ACCENT)}
    <div class="ir-stock-grid">
      {cards_html if cards_html else
       f'<p style="color:{C_TEXT3}">No stock data available.</p>'}
    </div>
  </div>
</div>"""


def _recommendations_section(report: "InvestorReport") -> str:
    try:
        recs = list(getattr(report.ai, "top_recommendations", []) or [])
    except Exception:
        recs = []

    cards_html = ""
    for rec in recs:
        # Each rec is a dict with keys: rank, title, action, ticker, conviction,
        # time_horizon, rationale, expected_return, risk_rating, entry, target, stop
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
        risk_col   = _RISK_COLORS.get(risk_rating, C_MOD)
        ret_col    = _color_for_change(exp_return)

        ticker_str = f" — {_badge(ticker, C_ACCENT)}" if ticker else ""

        # Price grid
        price_kvs = ""
        if entry is not None:
            price_kvs += _rec_kv("Entry", _format_price(_safe_float(entry)))
        if target is not None:
            price_kvs += _rec_kv("Target", _format_price(_safe_float(target)), C_HIGH)
        if stop is not None:
            price_kvs += _rec_kv("Stop", _format_price(_safe_float(stop)), C_LOW)
        price_row = f'<div class="ir-rec-grid">{price_kvs}</div>' if price_kvs else ""

        cards_html += f"""
<div class="ir-rec-card">
  <div class="ir-rec-rank">{rank}</div>
  <div>
    <div class="ir-rec-title">
      {title}{ticker_str}
    </div>
    <div class="ir-rec-meta">
      {_badge(action, action_col)}
      {_badge(f"Conv: {conviction}", conv_col)}
      {_badge(f"Risk: {risk_rating}", risk_col)}
      <span style="font-size:0.75rem;color:{ret_col};font-weight:700;align-self:center">
        {_format_pct(exp_return)} expected
      </span>
      <span style="font-size:0.73rem;color:{C_TEXT3};align-self:center">
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
  <div class="ir-section-inner">
    {_section_header("AI RECOMMENDATIONS", C_HIGH)}
    {cards_html if cards_html else f'<p style="color:{C_TEXT3}">No recommendations generated.</p>'}
    {f'''
    <div class="ir-card ir-card-highlight" style="margin-top:24px">
      <div class="ir-sub-title" style="color:{C_ACCENT}">30-Day Forward Outlook</div>
      <div class="ir-prose">{outlook_html}</div>
    </div>''' if outlook_html else ""}
  </div>
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
    macro_kv = {}  # could be extended with structured macro data

    # Full signals scorecard table
    scorecard_html = ""
    if signals:
        rows_h = ""
        for s in signals:
            ticker   = _safe_str(_safe_attr(s, "ticker"))
            name     = _safe_str(_safe_attr(s, "signal_name"))
            stype    = _safe_str(_safe_attr(s, "signal_type",  default="—"))
            direct   = _safe_str(_safe_attr(s, "direction",    default="—"))
            strength = _safe_float(_safe_attr(s, "strength",  default=0.0))
            conv     = _safe_str(_safe_attr(s, "conviction",   default="—"))
            entry    = _safe_attr(s, "entry_price")
            target   = _safe_attr(s, "target_price")
            stop     = _safe_attr(s, "stop_loss")
            exp_ret  = _safe_float(_safe_attr(s, "expected_return_pct", default=0.0))
            rr       = _safe_float(_safe_attr(s, "risk_reward", default=0.0))
            horizon  = _safe_str(_safe_attr(s, "time_horizon",  default="—"))

            dir_col  = C_HIGH if direct == "LONG" else (C_LOW if direct == "SHORT" else C_TEXT3)
            conv_col = _CONVICTION_COLORS.get(conv, C_TEXT2)
            ret_col  = _color_for_change(exp_ret)

            rows_h += f"""<tr>
  <td class="tc-ticker">{ticker}</td>
  <td class="tc-name">{name}</td>
  <td class="tc-dim">{stype}</td>
  <td style="color:{dir_col};font-weight:700">{direct}</td>
  <td class="tc-right">{strength:.2f}</td>
  <td class="tc-center">{_badge(conv, conv_col)}</td>
  <td class="tc-right tc-mono">{_format_price(entry) if entry else "—"}</td>
  <td class="tc-right tc-mono">{_format_price(target) if target else "—"}</td>
  <td class="tc-right tc-mono">{_format_price(stop) if stop else "—"}</td>
  <td class="tc-right" style="color:{ret_col};font-weight:700">{_format_pct(exp_ret)}</td>
  <td class="tc-right tc-mono">{rr:.2f}x</td>
  <td class="tc-center tc-dim">{horizon}</td>
</tr>"""
        scorecard_html = f"""
<div class="ir-sub-title">Full Signal Scorecard</div>
<div class="ir-table-wrap" style="overflow-x:auto">
  <table class="ir-table">
    <thead><tr>
      <th>Ticker</th><th>Signal</th><th>Type</th><th>Direction</th>
      <th class="tc-right">Strength</th><th class="tc-center">Conviction</th>
      <th class="tc-right">Entry</th><th class="tc-right">Target</th>
      <th class="tc-right">Stop</th><th class="tc-right">Exp. Ret.</th>
      <th class="tc-right">R/R</th><th class="tc-center">Horizon</th>
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
  <div class="ir-section-inner">
    {_section_header("APPENDIX &amp; DISCLAIMER", C_TEXT3)}
    {scorecard_html}
    {sources_table}
    <div class="ir-sub-title" style="margin-top:32px">Legal Disclaimer</div>
    <div class="ir-disclaimer-box">{disclaimer_text}</div>
    <div style="margin-top:32px;padding-top:20px;border-top:1px solid {C_BORDER};
                font-size:0.72rem;color:{C_TEXT3};text-align:center;line-height:1.8">
      Generated by <strong style="color:{C_TEXT2}">Ship Tracker Intelligence Platform</strong>
      &nbsp;&mdash;&nbsp;{report.generated_at}<br>
      Data: yfinance &bull; FRED &bull; World Bank &bull; Freightos FBX
      &bull; MarineTraffic AIS
    </div>
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
