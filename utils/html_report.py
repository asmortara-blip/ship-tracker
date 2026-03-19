"""Standalone HTML intelligence report generator for Ship Tracker.

Produces a fully self-contained HTML string with no external dependencies.
All CSS is inline; no CDN links, no external fonts, no JS frameworks.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# ── Color palette (mirrors ui/styles.py) ────────────────────────────────────
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_CARD    = "#1a2235"
_C_BORDER  = "rgba(255,255,255,0.08)"
_C_HIGH    = "#10b981"
_C_MOD     = "#f59e0b"
_C_LOW     = "#ef4444"
_C_ACCENT  = "#3b82f6"
_C_CONV    = "#8b5cf6"
_C_MACRO   = "#06b6d4"
_C_TEXT    = "#f1f5f9"
_C_TEXT2   = "#94a3b8"
_C_TEXT3   = "#64748b"

_CATEGORY_COLORS = {
    "CONVERGENCE": _C_CONV,
    "ROUTE":       _C_ACCENT,
    "PORT_DEMAND": _C_HIGH,
    "MACRO":       _C_MACRO,
}

_ACTION_COLORS = {
    "Prioritize": _C_HIGH,
    "Monitor":    _C_ACCENT,
    "Watch":      _C_TEXT2,
    "Caution":    _C_MOD,
    "Avoid":      _C_LOW,
}

_RISK_COLORS = {
    "LOW":      _C_HIGH,
    "MODERATE": _C_MOD,
    "HIGH":     _C_LOW,
    "CRITICAL": "#b91c1c",
}

_DIRECTION_ARROW = {
    "bullish": "&#9650;",   # ▲
    "bearish": "&#9660;",   # ▼
    "neutral": "&#8594;",   # →
    "Rising":  "&#9650;",
    "Falling": "&#9660;",
    "Stable":  "&#8594;",
}

_DIRECTION_COLOR = {
    "bullish": _C_HIGH,
    "bearish": _C_LOW,
    "neutral": _C_TEXT2,
    "Rising":  _C_HIGH,
    "Falling": _C_LOW,
    "Stable":  _C_TEXT2,
}


# ── Internal helpers ─────────────────────────────────────────────────────────

def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _score_bar(score: float, color: str = _C_ACCENT, width: int = 120) -> str:
    """Render an inline SVG progress bar for a [0,1] score."""
    fill_w = max(2, int(score * width))
    pct = f"{score * 100:.0f}%"
    return (
        f'<div style="display:inline-flex;align-items:center;gap:6px">'
        f'<div style="width:{width}px;height:8px;background:rgba(255,255,255,0.08);'
        f'border-radius:4px;overflow:hidden">'
        f'<div style="width:{fill_w}px;height:100%;background:{color};'
        f'border-radius:4px"></div></div>'
        f'<span style="font-size:0.75rem;color:{_C_TEXT2};font-weight:600">{pct}</span>'
        f'</div>'
    )


def _badge(text: str, color: str) -> str:
    bg = _hex_to_rgba(color, 0.15)
    border = _hex_to_rgba(color, 0.30)
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
        f'font-size:0.7rem;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:0.05em;background:{bg};color:{color};'
        f'border:1px solid {border}">{text}</span>'
    )


def _score_color(score: float) -> str:
    if score >= 0.70:
        return _C_HIGH
    if score >= 0.40:
        return _C_MOD
    return _C_LOW


def _risk_level(insights: list) -> str:
    if not insights:
        return "MODERATE"
    avg = sum(i.score for i in insights) / len(insights)
    bearish_count = sum(
        1 for i in insights
        if any(
            getattr(s, "direction", "neutral") == "bearish"
            for s in getattr(i, "supporting_signals", [])
        )
    )
    if avg >= 0.70 and bearish_count == 0:
        return "LOW"
    if avg >= 0.50:
        return "MODERATE"
    if bearish_count >= len(insights) // 2:
        return "HIGH"
    return "MODERATE"


def _market_assessment(insights: list) -> str:
    if not insights:
        return "Insufficient data to assess market conditions."
    avg = sum(i.score for i in insights) / len(insights)
    cats = [i.category for i in insights]
    dominant = max(set(cats), key=cats.count) if cats else "MIXED"
    label_map = {
        "PORT_DEMAND": "port demand dynamics",
        "ROUTE":       "route-level freight conditions",
        "MACRO":       "macroeconomic conditions",
        "CONVERGENCE": "multi-signal convergence patterns",
    }
    cat_phrase = label_map.get(dominant, "cross-market conditions")
    if avg >= 0.70:
        return (
            f"Market signals are broadly constructive. Analysis is dominated by {cat_phrase}, "
            f"with a composite confidence of {avg:.0%}. High-conviction opportunities are present."
        )
    if avg >= 0.50:
        return (
            f"Market conditions are mixed with selective opportunities. {cat_phrase.capitalize()} "
            f"drive the primary signal set at {avg:.0%} average confidence. "
            f"Careful positioning is warranted."
        )
    return (
        f"Market signals are cautionary. {cat_phrase.capitalize()} show stress at "
        f"{avg:.0%} average confidence. Defensive posture is recommended."
    )


def _fmt_rate(value: float) -> str:
    return f"${value:,.0f}"


def _fmt_pct(value: float, show_sign: bool = True) -> str:
    sign = "+" if (show_sign and value > 0) else ""
    return f"{sign}{value:.1f}%"


def _change_cell(value: float) -> str:
    color = _C_HIGH if value >= 0 else _C_LOW
    arrow = "&#9650;" if value >= 0 else "&#9660;"
    return (
        f'<td style="color:{color};font-weight:600;text-align:right">'
        f'{arrow} {_fmt_pct(value)}</td>'
    )


# ── CSS ──────────────────────────────────────────────────────────────────────

_CSS = f"""
* {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
    background: {_C_BG};
    color: {_C_TEXT};
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
                 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    padding: 40px;
    min-height: 100vh;
}}

a {{ color: {_C_ACCENT}; text-decoration: none; }}

/* ── Hero ── */
.hero {{
    background: linear-gradient(135deg, #0a0f1a 0%, #0f1d35 40%, #0d1829 70%, #070d18 100%);
    border: 1px solid rgba(59,130,246,0.20);
    border-radius: 16px;
    padding: 48px 52px;
    margin-bottom: 32px;
    position: relative;
    overflow: hidden;
}}
.hero::before {{
    content: '';
    position: absolute;
    top: -60px; right: -60px;
    width: 320px; height: 320px;
    background: radial-gradient(circle, rgba(59,130,246,0.12) 0%, transparent 65%);
    pointer-events: none;
}}
.hero::after {{
    content: '';
    position: absolute;
    bottom: -40px; left: 20%;
    width: 200px; height: 200px;
    background: radial-gradient(circle, rgba(139,92,246,0.08) 0%, transparent 65%);
    pointer-events: none;
}}
.hero-eyebrow {{
    font-size: 0.72rem;
    font-weight: 700;
    color: {_C_ACCENT};
    text-transform: uppercase;
    letter-spacing: 0.18em;
    margin-bottom: 12px;
}}
.hero-title {{
    font-size: 2.4rem;
    font-weight: 800;
    color: {_C_TEXT};
    line-height: 1.15;
    margin-bottom: 10px;
    letter-spacing: -0.02em;
}}
.hero-meta {{
    font-size: 0.82rem;
    color: {_C_TEXT3};
    margin-bottom: 36px;
}}
.stat-boxes {{
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
}}
.stat-box {{
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 10px;
    padding: 16px 22px;
    min-width: 130px;
    flex: 1;
    max-width: 200px;
}}
.stat-box-value {{
    font-size: 1.9rem;
    font-weight: 800;
    color: {_C_TEXT};
    line-height: 1;
    margin-bottom: 4px;
}}
.stat-box-label {{
    font-size: 0.68rem;
    font-weight: 600;
    color: {_C_TEXT3};
    text-transform: uppercase;
    letter-spacing: 0.07em;
}}

/* ── Sections ── */
.section {{
    margin-bottom: 32px;
}}
.section-title {{
    font-size: 0.72rem;
    font-weight: 700;
    color: {_C_TEXT3};
    text-transform: uppercase;
    letter-spacing: 0.10em;
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid {_C_BORDER};
}}

/* ── Cards ── */
.card {{
    background: {_C_CARD};
    border: 1px solid {_C_BORDER};
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 12px;
}}
.card-accent-left {{
    border-left: 4px solid {_C_ACCENT};
}}

/* ── Exec summary ── */
.exec-summary {{
    background: linear-gradient(135deg, {_C_CARD}, #141e30);
    border: 1px solid rgba(59,130,246,0.18);
    border-radius: 14px;
    padding: 28px 32px;
    margin-bottom: 12px;
}}
.exec-assessment {{
    font-size: 0.95rem;
    color: {_C_TEXT};
    line-height: 1.7;
    margin-bottom: 20px;
}}
.key-findings-label {{
    font-size: 0.68rem;
    font-weight: 700;
    color: {_C_TEXT3};
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 10px;
}}
.finding-item {{
    display: flex;
    gap: 10px;
    padding: 8px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    font-size: 0.85rem;
    color: {_C_TEXT2};
    line-height: 1.5;
}}
.finding-item:last-child {{ border-bottom: none; }}
.finding-bullet {{
    color: {_C_ACCENT};
    font-weight: 700;
    flex-shrink: 0;
    margin-top: 1px;
}}

/* ── Insight cards ── */
.insight-card {{
    background: {_C_CARD};
    border: 1px solid {_C_BORDER};
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 12px;
    border-left: 4px solid {_C_ACCENT};
}}
.insight-header {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 10px;
}}
.insight-title {{
    font-size: 1.0rem;
    font-weight: 700;
    color: {_C_TEXT};
    line-height: 1.3;
}}
.insight-detail {{
    font-size: 0.85rem;
    color: {_C_TEXT2};
    line-height: 1.6;
    margin-bottom: 14px;
}}
.score-badge {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 52px;
    height: 52px;
    border-radius: 50%;
    font-size: 0.88rem;
    font-weight: 800;
    flex-shrink: 0;
    border: 2px solid;
}}
.insight-meta {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
    margin-bottom: 14px;
}}
.signal-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.78rem;
    margin-top: 8px;
}}
.signal-table th {{
    color: {_C_TEXT3};
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 0.66rem;
    text-align: left;
    padding: 4px 8px;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}}
.signal-table td {{
    padding: 5px 8px;
    color: {_C_TEXT2};
    border-bottom: 1px solid rgba(255,255,255,0.03);
}}
.signal-table tr:last-child td {{ border-bottom: none; }}

/* ── Data tables ── */
.data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
}}
.data-table th {{
    background: rgba(59,130,246,0.15);
    color: {_C_ACCENT};
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 0.68rem;
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid rgba(59,130,246,0.25);
}}
.data-table td {{
    padding: 10px 14px;
    color: {_C_TEXT2};
    border-bottom: 1px solid rgba(255,255,255,0.04);
    vertical-align: middle;
}}
.data-table tr:nth-child(even) td {{
    background: rgba(255,255,255,0.018);
}}
.data-table tr:hover td {{
    background: rgba(59,130,246,0.06);
    color: {_C_TEXT};
}}
.data-table .rank-cell {{
    color: {_C_TEXT3};
    font-weight: 700;
    font-size: 0.80rem;
    width: 36px;
    text-align: center;
}}
.data-table .name-cell {{
    color: {_C_TEXT};
    font-weight: 600;
}}

/* ── Macro table ── */
.macro-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
}}
.macro-table th {{
    background: rgba(6,182,212,0.12);
    color: {_C_MACRO};
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 0.68rem;
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid rgba(6,182,212,0.20);
}}
.macro-table td {{
    padding: 10px 14px;
    color: {_C_TEXT2};
    border-bottom: 1px solid rgba(255,255,255,0.04);
}}
.macro-table tr:nth-child(even) td {{
    background: rgba(255,255,255,0.018);
}}

/* ── Footer ── */
.footer {{
    margin-top: 48px;
    padding-top: 20px;
    border-top: 1px solid {_C_BORDER};
    font-size: 0.75rem;
    color: {_C_TEXT3};
    text-align: center;
    line-height: 1.7;
}}
"""


# ── Section builders ─────────────────────────────────────────────────────────

def _build_hero(
    port_results: list,
    route_results: list,
    insights: list,
    generated_at: str,
    date_range: str,
) -> str:
    n_ports    = len(port_results)
    n_routes   = len(route_results)
    n_signals  = sum(len(getattr(i, "supporting_signals", [])) for i in insights)
    n_highconv = sum(1 for i in insights if i.score >= 0.70)

    stats = [
        (str(n_ports),    "Ports Analyzed"),
        (str(n_routes),   "Routes Tracked"),
        (str(n_signals),  "Active Signals"),
        (str(n_highconv), "High-Conviction"),
    ]
    boxes_html = "".join(
        f'<div class="stat-box">'
        f'<div class="stat-box-value">{v}</div>'
        f'<div class="stat-box-label">{lbl}</div>'
        f'</div>'
        for v, lbl in stats
    )
    return f"""
<div class="hero">
  <div class="hero-eyebrow">&#9632; Global Shipping Intelligence</div>
  <div class="hero-title">Container Market Intelligence Report</div>
  <div class="hero-meta">
    Generated {generated_at}
    {f'&nbsp;&mdash;&nbsp;{date_range}' if date_range else ''}
  </div>
  <div class="stat-boxes">{boxes_html}</div>
</div>
"""


def _build_exec_summary(insights: list) -> str:
    assessment = _market_assessment(insights)
    risk       = _risk_level(insights)
    risk_color = _RISK_COLORS.get(risk, _C_MOD)

    top3 = insights[:3]
    findings_html = ""
    for ins in top3:
        findings_html += (
            f'<div class="finding-item">'
            f'<span class="finding-bullet">&#8250;</span>'
            f'<span><strong style="color:{_C_TEXT}">{ins.title}.</strong> '
            f'{ins.detail[:160].rstrip(".")}.{" ..." if len(ins.detail) > 160 else ""}</span>'
            f'</div>'
        )

    risk_badge = _badge(f"Risk: {risk}", risk_color)

    return f"""
<div class="section">
  <div class="section-title">Executive Summary</div>
  <div class="exec-summary">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;
                margin-bottom:14px;gap:16px">
      <div class="exec-assessment">{assessment}</div>
      <div style="flex-shrink:0">{risk_badge}</div>
    </div>
    <div class="key-findings-label">Key Findings</div>
    {findings_html if findings_html else
     '<div style="color:' + _C_TEXT3 + ';font-size:0.83rem">No insights available.</div>'}
  </div>
</div>
"""


def _build_insights_section(insights: list) -> str:
    top5 = insights[:5]
    if not top5:
        return ""

    cards_html = ""
    for ins in top5:
        cat_color  = _CATEGORY_COLORS.get(ins.category, _C_ACCENT)
        score_col  = _score_color(ins.score)
        act_color  = _ACTION_COLORS.get(ins.action, _C_TEXT2)
        score_pct  = f"{ins.score * 100:.0f}"

        # Signal breakdown table
        sigs = getattr(ins, "supporting_signals", [])
        if sigs:
            sig_rows = ""
            for s in sigs:
                dir_arrow = _DIRECTION_ARROW.get(s.direction, "&#8594;")
                dir_color = _DIRECTION_COLOR.get(s.direction, _C_TEXT2)
                sig_rows += (
                    f'<tr>'
                    f'<td>{s.name}</td>'
                    f'<td style="text-align:right">{s.value:.2f}</td>'
                    f'<td style="text-align:right">{s.weight:.2f}</td>'
                    f'<td style="color:{dir_color};text-align:center">'
                    f'{dir_arrow} {s.direction.capitalize()}</td>'
                    f'<td style="color:{_C_TEXT3}">{s.label}</td>'
                    f'</tr>'
                )
            sig_table = f"""
<table class="signal-table">
  <thead>
    <tr>
      <th>Signal</th><th style="text-align:right">Value</th>
      <th style="text-align:right">Wt</th>
      <th style="text-align:center">Direction</th>
      <th>Interpretation</th>
    </tr>
  </thead>
  <tbody>{sig_rows}</tbody>
</table>"""
        else:
            sig_table = ""

        ports_txt  = ", ".join(ins.ports_involved) or "&mdash;"
        routes_txt = ", ".join(ins.routes_involved) or "&mdash;"
        stocks_txt = ", ".join(ins.stocks_potentially_affected) or "&mdash;"

        freshness_warn = ""
        if getattr(ins, "data_freshness_warning", False):
            freshness_warn = (
                f'&nbsp;{_badge("Stale Data", _C_MOD)}'
            )

        cards_html += f"""
<div class="insight-card" style="border-left-color:{cat_color}">
  <div class="insight-header">
    <div>
      <div class="insight-meta">
        {_badge(ins.category, cat_color)}
        {_badge(ins.action, act_color)}
        {_badge(ins.score_label, score_col)}
        {freshness_warn}
      </div>
      <div class="insight-title">{ins.title}</div>
    </div>
    <div class="score-badge"
         style="background:{_hex_to_rgba(score_col, 0.15)};
                color:{score_col};border-color:{_hex_to_rgba(score_col, 0.35)}">
      {score_pct}%
    </div>
  </div>
  <div class="insight-detail">{ins.detail}</div>
  <div style="display:flex;gap:24px;flex-wrap:wrap;
              font-size:0.75rem;color:{_C_TEXT3};margin-bottom:12px">
    <span><strong style="color:{_C_TEXT2}">Ports:</strong> {ports_txt}</span>
    <span><strong style="color:{_C_TEXT2}">Routes:</strong> {routes_txt}</span>
    <span><strong style="color:{_C_TEXT2}">Stocks:</strong> {stocks_txt}</span>
    <span><strong style="color:{_C_TEXT2}">ID:</strong>
          <code style="font-size:0.7rem;color:{_C_TEXT3}">{ins.insight_id}</code></span>
  </div>
  {_score_bar(ins.score, score_col)}
  {sig_table}
</div>"""

    return f"""
<div class="section">
  <div class="section-title">Top Insights &mdash; High Conviction Signals</div>
  {cards_html}
</div>
"""


def _build_port_table(port_results: list) -> str:
    if not port_results:
        return ""

    def _safe(d: Any, *keys: str, default: Any = "") -> Any:
        for k in keys:
            if isinstance(d, dict):
                d = d.get(k, default)
            else:
                d = getattr(d, k, default)
        return d if d is not None else default

    rows_html = ""
    for rank, pr in enumerate(port_results, 1):
        score      = float(_safe(pr, "demand_score", default=0.0))
        port_name  = _safe(pr, "port_name", default=_safe(pr, "port_id", default="—"))
        region     = _safe(pr, "region", default="—")
        trade_flow = _safe(pr, "trade_flow", default="—")
        congestion = _safe(pr, "congestion_score", default=None)
        status     = _safe(pr, "status", default=_safe(pr, "demand_label", default="—"))
        col        = _score_color(score)

        cong_html = ""
        if congestion is not None:
            cong_val = float(congestion)
            cong_col = _C_LOW if cong_val >= 0.6 else (_C_MOD if cong_val >= 0.35 else _C_HIGH)
            cong_html = (
                f'<div style="display:inline-flex;align-items:center;gap:6px">'
                f'<div style="width:60px;height:6px;background:rgba(255,255,255,0.08);'
                f'border-radius:3px;overflow:hidden">'
                f'<div style="width:{int(cong_val*60)}px;height:100%;'
                f'background:{cong_col};border-radius:3px"></div></div>'
                f'<span style="font-size:0.73rem;color:{cong_col}">{cong_val:.0%}</span>'
                f'</div>'
            )
        else:
            cong_html = f'<span style="color:{_C_TEXT3}">—</span>'

        status_col = _RISK_COLORS.get(str(status).upper(), _C_TEXT2)
        status_badge = _badge(str(status), status_col) if status != "—" else "—"

        rows_html += f"""
<tr>
  <td class="rank-cell">#{rank}</td>
  <td class="name-cell">{port_name}</td>
  <td>{region}</td>
  <td>{_score_bar(score, col, 100)}</td>
  <td style="color:{_C_TEXT3}">{trade_flow}</td>
  <td>{cong_html}</td>
  <td>{status_badge}</td>
</tr>"""

    return f"""
<div class="section">
  <div class="section-title">Port Rankings &mdash; Demand Analysis</div>
  <div class="card" style="padding:0;overflow:hidden">
    <table class="data-table">
      <thead>
        <tr>
          <th>Rank</th><th>Port</th><th>Region</th>
          <th>Demand Score</th><th>Trade Flow</th>
          <th>Congestion</th><th>Status</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</div>
"""


def _build_route_table(route_results: list) -> str:
    if not route_results:
        return ""

    def _safe(d: Any, *keys: str, default: Any = "") -> Any:
        for k in keys:
            if isinstance(d, dict):
                d = d.get(k, default)
            else:
                d = getattr(d, k, default)
        return d if d is not None else default

    rows_html = ""
    for rank, rr in enumerate(route_results, 1):
        score       = float(_safe(rr, "score", default=0.0))
        route_name  = _safe(rr, "route_name", default=_safe(rr, "route_id", default="—"))
        origin      = _safe(rr, "origin", default="?")
        dest        = _safe(rr, "destination", default="?")
        rate        = _safe(rr, "current_rate", default=None)
        chg_30d     = _safe(rr, "change_30d", default=None)
        trend       = _safe(rr, "trend", default="Stable")
        opportunity = _safe(rr, "opportunity", default="—")
        col         = _score_color(score)

        route_display = (
            f'<strong style="color:{_C_TEXT}">{route_name}</strong>'
            if route_name != "—" else "—"
        )
        leg = (
            f'<span style="color:{_C_TEXT3};font-size:0.78rem">'
            f'{origin} &rarr; {dest}</span>'
            if origin != "?" else ""
        )

        rate_html = _fmt_rate(float(rate)) if rate is not None else "—"
        chg_html  = (
            f'<span style="color:{"#10b981" if float(chg_30d) >= 0 else "#ef4444"};'
            f'font-weight:600">'
            f'{"&#9650;" if float(chg_30d) >= 0 else "&#9660;"} '
            f'{_fmt_pct(float(chg_30d))}</span>'
            if chg_30d is not None else "—"
        )

        trend_col   = _DIRECTION_COLOR.get(str(trend), _C_TEXT2)
        trend_arrow = _DIRECTION_ARROW.get(str(trend), "&#8594;")

        rows_html += f"""
<tr>
  <td class="rank-cell">#{rank}</td>
  <td>{route_display}<br>{leg}</td>
  <td>{_score_bar(score, col, 100)}</td>
  <td style="text-align:right;font-weight:600;color:{_C_TEXT}">{rate_html}</td>
  <td style="text-align:right">{chg_html}</td>
  <td style="color:{trend_col}">{trend_arrow} {trend}</td>
  <td style="color:{_C_TEXT3};font-size:0.80rem">{opportunity}</td>
</tr>"""

    return f"""
<div class="section">
  <div class="section-title">Route Rankings &mdash; Freight Opportunities</div>
  <div class="card" style="padding:0;overflow:hidden">
    <table class="data-table">
      <thead>
        <tr>
          <th>Rank</th><th>Route</th><th>Score</th>
          <th style="text-align:right">Current Rate</th>
          <th style="text-align:right">30d Change</th>
          <th>Trend</th><th>Opportunity</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</div>
"""


def _build_freight_table(freight_data: dict | list | None) -> str:
    if not freight_data:
        return ""

    # Normalize: accept dict-of-dicts, list-of-dicts, or flat dict
    rows: list[dict] = []
    if isinstance(freight_data, dict):
        for k, v in freight_data.items():
            if isinstance(v, dict):
                row = dict(v)
                row.setdefault("route", k)
                rows.append(row)
            elif isinstance(v, (int, float)):
                rows.append({"route": k, "current_rate": v})
    elif isinstance(freight_data, list):
        rows = list(freight_data)

    if not rows:
        return ""

    rows_html = ""
    for row in rows:
        route   = row.get("route", row.get("name", "—"))
        rate    = row.get("current_rate", row.get("rate", None))
        chg30   = row.get("change_30d", row.get("pct_30d", None))
        chg90   = row.get("change_90d", row.get("pct_90d", None))
        updated = row.get("updated", row.get("as_of", "—"))

        rate_html = _fmt_rate(float(rate)) if rate is not None else "—"
        chg30_html = (
            f'<span style="color:{"#10b981" if float(chg30) >= 0 else "#ef4444"};'
            f'font-weight:600">'
            f'{"&#9650;" if float(chg30) >= 0 else "&#9660;"} {_fmt_pct(float(chg30))}</span>'
            if chg30 is not None else '<span style="color:#64748b">—</span>'
        )
        chg90_html = (
            f'<span style="color:{"#10b981" if float(chg90) >= 0 else "#ef4444"};'
            f'font-weight:600">'
            f'{"&#9650;" if float(chg90) >= 0 else "&#9660;"} {_fmt_pct(float(chg90))}</span>'
            if chg90 is not None else '<span style="color:#64748b">—</span>'
        )

        rows_html += f"""
<tr>
  <td class="name-cell">{route}</td>
  <td style="text-align:right;font-weight:700;color:{_C_TEXT}">{rate_html}</td>
  <td style="text-align:right">{chg30_html}</td>
  <td style="text-align:right">{chg90_html}</td>
  <td style="color:{_C_TEXT3};font-size:0.78rem">{updated}</td>
</tr>"""

    return f"""
<div class="section">
  <div class="section-title">Freight Rate Tracker &mdash; Current Rates &amp; Changes</div>
  <div class="card" style="padding:0;overflow:hidden">
    <table class="data-table">
      <thead>
        <tr>
          <th>Route</th>
          <th style="text-align:right">Current Rate ($/FEU)</th>
          <th style="text-align:right">30d Change</th>
          <th style="text-align:right">90d Change</th>
          <th>As Of</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</div>
"""


def _build_macro_section(macro_data: dict | None) -> str:
    if not macro_data:
        return ""

    rows: list[dict] = []
    if isinstance(macro_data, dict):
        for series_id, v in macro_data.items():
            if isinstance(v, dict):
                row = dict(v)
                row.setdefault("series_id", series_id)
                rows.append(row)
            else:
                rows.append({"series_id": series_id, "value": v})

    if not rows:
        return ""

    rows_html = ""
    for row in rows:
        sid      = row.get("series_id", "—")
        name     = row.get("name", row.get("title", sid))
        value    = row.get("value", row.get("latest", None))
        unit     = row.get("unit", row.get("units", ""))
        freq     = row.get("frequency", row.get("freq", "—"))
        trend    = row.get("direction", row.get("trend", "neutral"))
        updated  = row.get("updated", row.get("as_of", "—"))

        val_html = (
            f'<strong style="color:{_C_TEXT}">{value:.4g}{" " + unit if unit else ""}</strong>'
            if isinstance(value, (int, float)) else
            f'<strong style="color:{_C_TEXT}">{value} {unit}</strong>' if value else "—"
        )

        t_key   = str(trend).lower()
        t_arrow = _DIRECTION_ARROW.get(t_key.capitalize(), _DIRECTION_ARROW.get(t_key, "&#8594;"))
        t_color = (
            _C_HIGH if t_key in ("rising", "bullish")
            else _C_LOW if t_key in ("falling", "bearish")
            else _C_TEXT2
        )

        rows_html += f"""
<tr>
  <td style="font-family:monospace;font-size:0.78rem;color:{_C_MACRO}">{sid}</td>
  <td class="name-cell">{name}</td>
  <td style="text-align:right">{val_html}</td>
  <td style="text-align:center;color:{t_color}">{t_arrow}</td>
  <td style="color:{_C_TEXT3};font-size:0.78rem">{freq}</td>
  <td style="color:{_C_TEXT3};font-size:0.78rem">{updated}</td>
</tr>"""

    return f"""
<div class="section">
  <div class="section-title">Macro Environment &mdash; FRED Indicators</div>
  <div class="card" style="padding:0;overflow:hidden">
    <table class="macro-table">
      <thead>
        <tr>
          <th>Series ID</th><th>Indicator</th><th style="text-align:right">Value</th>
          <th style="text-align:center">Trend</th><th>Frequency</th><th>Updated</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</div>
"""


def _build_stock_section(stock_data: dict | list | None) -> str:
    if not stock_data:
        return ""

    rows: list[dict] = []
    if isinstance(stock_data, dict):
        for ticker, v in stock_data.items():
            if isinstance(v, dict):
                row = dict(v)
                row.setdefault("ticker", ticker)
                rows.append(row)
            elif isinstance(v, (int, float)):
                rows.append({"ticker": ticker, "price": v})
    elif isinstance(stock_data, list):
        rows = list(stock_data)

    if not rows:
        return ""

    rows_html = ""
    for row in rows:
        ticker = row.get("ticker", row.get("symbol", "—"))
        name   = row.get("name", row.get("company", "—"))
        price  = row.get("price", row.get("close", None))
        chg1d  = row.get("change_1d", row.get("pct_1d", None))
        chg30d = row.get("change_30d", row.get("pct_30d", None))
        sector = row.get("sector", "—")
        signal = row.get("signal", row.get("action", "—"))

        price_html = (
            f'<strong style="color:{_C_TEXT}">{"$"}{float(price):,.2f}</strong>'
            if price is not None else "—"
        )
        chg1d_html = (
            f'<span style="color:{"#10b981" if float(chg1d) >= 0 else "#ef4444"};'
            f'font-weight:600">{"&#9650;" if float(chg1d) >= 0 else "&#9660;"} '
            f'{_fmt_pct(float(chg1d))}</span>'
            if chg1d is not None else "—"
        )
        chg30d_html = (
            f'<span style="color:{"#10b981" if float(chg30d) >= 0 else "#ef4444"};'
            f'font-weight:600">{"&#9650;" if float(chg30d) >= 0 else "&#9660;"} '
            f'{_fmt_pct(float(chg30d))}</span>'
            if chg30d is not None else "—"
        )
        sig_col = _ACTION_COLORS.get(str(signal), _C_TEXT2)
        sig_html = _badge(signal, sig_col) if signal != "—" else "—"

        rows_html += f"""
<tr>
  <td style="font-family:monospace;font-weight:700;color:{_C_ACCENT}">{ticker}</td>
  <td class="name-cell">{name}</td>
  <td style="text-align:right">{price_html}</td>
  <td style="text-align:right">{chg1d_html}</td>
  <td style="text-align:right">{chg30d_html}</td>
  <td style="color:{_C_TEXT3};font-size:0.80rem">{sector}</td>
  <td>{sig_html}</td>
</tr>"""

    return f"""
<div class="section">
  <div class="section-title">Shipping Equities &mdash; Market Signals</div>
  <div class="card" style="padding:0;overflow:hidden">
    <table class="data-table">
      <thead>
        <tr>
          <th>Ticker</th><th>Company</th>
          <th style="text-align:right">Price</th>
          <th style="text-align:right">1d Change</th>
          <th style="text-align:right">30d Change</th>
          <th>Sector</th><th>Signal</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</div>
"""


def _build_appendix() -> str:
    sources = [
        ("Freight Rates",    "Freightos Baltic Index (FBX), Baltic Exchange"),
        ("Macroeconomic",    "US Federal Reserve Economic Data (FRED API)"),
        ("Trade Flows",      "UN Comtrade, World Bank WITS"),
        ("Shipping Equities","Yahoo Finance (yfinance)"),
        ("Port AIS Data",    "MarineTraffic / AIS aggregation"),
        ("Port Registry",    "World Port Index (UKHO)"),
    ]
    rows = "".join(
        f'<tr><td style="color:{_C_TEXT2};font-weight:600">{s}</td>'
        f'<td style="color:{_C_TEXT3}">{d}</td></tr>'
        for s, d in sources
    )
    return f"""
<div class="section">
  <div class="section-title">Appendix &mdash; Data Sources</div>
  <div class="card" style="padding:0;overflow:hidden">
    <table class="data-table">
      <thead><tr><th>Data Type</th><th>Source</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>
"""


def _build_footer() -> str:
    return (
        f'<div class="footer">'
        f'Generated by <strong>Ship Tracker Intelligence Platform</strong><br>'
        f'Data sources: yfinance, FRED, World Bank, Freightos FBX<br>'
        f'<span style="font-size:0.70rem;color:#334155">'
        f'This report is for informational purposes only and does not constitute '
        f'financial or investment advice.</span>'
        f'</div>'
    )


# ── Public API ───────────────────────────────────────────────────────────────

def generate_html_report(
    port_results: list,
    route_results: list,
    insights: list,
    freight_data,
    macro_data,
    stock_data,
    *,
    date_range: str = "",
) -> str:
    """Return a complete, self-contained HTML intelligence report string.

    Parameters
    ----------
    port_results:   List of port analysis result objects or dicts.
    route_results:  List of route analysis result objects or dicts.
    insights:       List of Insight objects (engine.insight.Insight).
    freight_data:   Dict or list of freight rate records.
    macro_data:     Dict of FRED indicator records.
    stock_data:     Dict or list of shipping equity records.
    date_range:     Optional human-readable date range string for the header.

    Returns
    -------
    str: Standalone HTML document (no external dependencies).
    """
    now         = datetime.now(timezone.utc)
    date_str    = now.strftime("%B %d, %Y")
    ts_str      = now.strftime("%Y-%m-%d %H:%M UTC")

    # Sort insights by score descending
    sorted_insights = sorted(insights, key=lambda i: i.score, reverse=True)

    body = (
        _build_hero(port_results, route_results, sorted_insights, ts_str, date_range)
        + _build_exec_summary(sorted_insights)
        + _build_insights_section(sorted_insights)
        + _build_port_table(port_results)
        + _build_route_table(route_results)
        + _build_freight_table(freight_data)
        + _build_macro_section(macro_data)
        + _build_stock_section(stock_data)
        + _build_appendix()
        + _build_footer()
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Global Shipping Intelligence Report \u2014 {date_str}</title>
  <style>{_CSS}</style>
</head>
<body>
{body}
</body>
</html>"""


def get_report_as_bytes(html_str: str) -> bytes:
    """Encode the HTML report string to UTF-8 bytes for download."""
    return html_str.encode("utf-8")
