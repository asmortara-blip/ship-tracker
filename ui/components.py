"""Shared animated UI component library for Ship Tracker."""
from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go

# ── Color constants (self-contained; mirrors ui/styles.py) ──────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_CONV    = "#8b5cf6"
C_MACRO   = "#06b6d4"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"


# ── Internal helpers ─────────────────────────────────────────────────────────

def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Convert a #rrggbb hex color to an rgba() string."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _opportunity_color(score: float) -> str:
    """Map an opportunity score [0,1] to a color."""
    if score >= 0.65:
        return C_HIGH
    if score >= 0.40:
        return C_MOD
    return C_LOW


# ── 1. stat_counter ──────────────────────────────────────────────────────────

def stat_counter(
    label: str,
    value,
    prefix: str = "",
    suffix: str = "",
    color: str = "#10b981",
    delta: float | None = None,
    delta_label: str = "",
) -> None:
    """Render a large animated stat card with optional delta indicator."""
    shadow = _hex_to_rgba(color, 0.1)

    if delta is not None:
        arrow = "▲" if delta >= 0 else "▼"
        delta_color = C_HIGH if delta >= 0 else C_LOW
        delta_text = f"{abs(delta):.1f}%"
        if delta_label:
            delta_text = delta_text + " " + delta_label
        delta_html = (
            f'<div style="font-size:0.75rem; color:{delta_color}; margin-top:6px">'
            f'{arrow} {delta_text}'
            f'</div>'
        )
    else:
        delta_html = ""

    st.markdown(
        f"""
        <div class="slide-in" style="
            background:{C_CARD};
            border-radius:12px;
            padding:20px;
            text-align:center;
            border-top:3px solid {color};
            box-shadow:0 0 20px {shadow};
        ">
          <div style="
            font-size:2.2rem;
            font-weight:900;
            color:{color};
            font-variant-numeric:tabular-nums;
            line-height:1.1;
          ">{prefix}{value}{suffix}</div>
          <div style="
            font-size:0.78rem;
            color:{C_TEXT2};
            text-transform:uppercase;
            letter-spacing:0.08em;
            margin-top:4px;
          ">{label}</div>
          {delta_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── 2. mini_sparkline ────────────────────────────────────────────────────────

def mini_sparkline(
    data: list[float],
    color: str = "#3b82f6",
    height: int = 60,
    show_area: bool = True,
) -> go.Figure:
    """Return a minimal Plotly sparkline figure for embedding in a column."""
    fill_color = _hex_to_rgba(color, 0.15)
    fill = "tozeroy" if show_area else "none"

    fig = go.Figure(
        go.Scatter(
            y=data,
            mode="lines",
            line=dict(color=color, width=1.5),
            fill=fill,
            fillcolor=fill_color,
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(
            visible=False,
            showgrid=False,
            zeroline=False,
        ),
        yaxis=dict(
            visible=False,
            showgrid=False,
            zeroline=False,
        ),
        showlegend=False,
    )
    return fig


# ── 3. gauge_ring ────────────────────────────────────────────────────────────

def gauge_ring(
    value: float,
    label: str,
    color: str = "#10b981",
    size: int = 180,
) -> go.Figure:
    """Return a clean circular gauge (donut) figure."""
    pct_text = f"{value * 100:.0f}%"

    fig = go.Figure(
        go.Pie(
            values=[value, 1 - value],
            hole=0.75,
            marker=dict(colors=[color, C_CARD]),
            textinfo="none",
            hoverinfo="skip",
            showlegend=False,
            direction="clockwise",
            sort=False,
        )
    )
    fig.update_layout(
        paper_bgcolor=C_BG,
        height=size,
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=False,
        annotations=[
            dict(
                text=(
                    f'<b><span style="font-size:1.4em; color:{color}">'
                    f'{pct_text}</span></b>'
                    f'<br><span style="font-size:0.7em; color:{C_TEXT2}">'
                    f'{label}</span>'
                ),
                x=0.5,
                y=0.5,
                font=dict(color=color, size=14),
                showarrow=False,
            )
        ],
    )
    return fig


# ── 4. alert_banner ──────────────────────────────────────────────────────────

_ALERT_CONFIG: dict[str, dict] = {
    "info":     {"color": "#3b82f6", "icon": "ℹ️",  "pulse": False},
    "success":  {"color": "#10b981", "icon": "✅",  "pulse": False},
    "warning":  {"color": "#f59e0b", "icon": "⚠️",  "pulse": False},
    "critical": {"color": "#ef4444", "icon": "🚨",  "pulse": True},
}


def alert_banner(
    message: str,
    level: str = "info",
    dismissible: bool = False,
) -> None:
    """Render a styled alert banner."""
    cfg = _ALERT_config = _ALERT_CONFIG.get(level, _ALERT_CONFIG["info"])
    color  = cfg["color"]
    icon   = cfg["icon"]
    pulse  = cfg["pulse"]
    bg     = _hex_to_rgba(color, 0.08)
    border = _hex_to_rgba(color, 0.35)
    anim_class = "pulse-glow" if pulse else ""

    st.markdown(
        f"""
        <div class="{anim_class}" style="
            background:{bg};
            border-left:4px solid {color};
            border-radius:8px;
            padding:12px 16px;
            display:flex;
            align-items:flex-start;
            gap:10px;
            margin:8px 0;
            border:1px solid {border};
            border-left:4px solid {color};
        ">
          <span style="font-size:1.1rem; line-height:1.4">{icon}</span>
          <span style="color:{C_TEXT}; font-size:0.88rem; line-height:1.5">{message}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── 5. kpi_row ───────────────────────────────────────────────────────────────

def kpi_row(metrics: list[dict]) -> None:
    """Render a horizontal row of KPI cards.

    Each dict: {"label": str, "value": str, "delta": float|None, "color": str}
    """
    cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics):
        color = m.get("color", C_ACCENT)
        delta = m.get("delta")
        shadow = _hex_to_rgba(color, 0.08)

        if delta is not None:
            arrow = "▲" if delta >= 0 else "▼"
            d_color = C_HIGH if delta >= 0 else C_LOW
            delta_html = (
                f'<div style="font-size:0.78rem; color:{d_color}; margin-top:5px">'
                f'{arrow} {abs(delta):.1f}%'
                f'</div>'
            )
        else:
            delta_html = ""

        card_html = f"""
        <div class="slide-in" style="
            background:{C_CARD};
            border:1px solid {C_BORDER};
            border-top:3px solid {color};
            border-radius:12px;
            padding:18px 16px;
            text-align:center;
            box-shadow:0 0 16px {shadow};
            height:100%;
        ">
          <div style="
            font-size:1.9rem;
            font-weight:800;
            color:{C_TEXT};
            font-variant-numeric:tabular-nums;
            line-height:1.1;
          ">{m.get('value', '')}</div>
          <div style="
            font-size:0.72rem;
            color:{C_TEXT3};
            text-transform:uppercase;
            letter-spacing:0.06em;
            margin-top:4px;
          ">{m.get('label', '')}</div>
          {delta_html}
        </div>
        """
        with col:
            st.markdown(card_html, unsafe_allow_html=True)


# ── 6. shipping_heat_bar ─────────────────────────────────────────────────────

def shipping_heat_bar(scores: dict[str, float], title: str = "") -> None:
    """Render a horizontal heat bar segmented by metric scores."""
    total = sum(scores.values()) or 1.0

    segments_html = ""
    for name, val in scores.items():
        pct = val / total * 100
        color = _opportunity_color(val)
        bg = _hex_to_rgba(color, 0.8)
        segments_html += (
            f'<div title="{name}: {val:.2f}" style="'
            f'flex:{pct};'
            f'background:{bg};'
            f'height:8px;'
            f'min-width:2px;'
            f'" ></div>'
        )

    title_html = ""
    if title:
        title_html = (
            f'<div style="font-size:0.72rem; color:{C_TEXT3}; '
            f'text-transform:uppercase; letter-spacing:0.06em; margin-bottom:4px">'
            f'{title}</div>'
        )

    legend_html = "".join(
        f'<span style="font-size:0.68rem; color:{C_TEXT3}; margin-right:10px">'
        f'<span style="color:{_hex_to_rgba(_opportunity_color(v), 0.9)}">&#9632;</span>'
        f' {k}: {v:.2f}</span>'
        for k, v in scores.items()
    )

    st.markdown(
        f"""
        {title_html}
        <div style="display:flex; height:8px; border-radius:4px; overflow:hidden; margin:8px 0">
          {segments_html}
        </div>
        <div style="display:flex; flex-wrap:wrap; margin-top:4px">
          {legend_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── 7. route_card ────────────────────────────────────────────────────────────

def route_card(route, rank: int | None = None) -> None:
    """Render a rich route opportunity card."""
    score = getattr(route, "opportunity_score", 0.0)
    color = _opportunity_color(score)
    bg_border = _hex_to_rgba(color, 0.25)
    score_bg  = _hex_to_rgba(color, 0.12)

    rank_html = ""
    if rank is not None:
        rank_html = (
            f'<span style="'
            f'background:{_hex_to_rgba(color, 0.15)};'
            f'color:{color};'
            f'border:1px solid {_hex_to_rgba(color, 0.3)};'
            f'border-radius:999px;'
            f'font-size:0.68rem;'
            f'font-weight:700;'
            f'padding:2px 8px;'
            f'margin-right:8px;'
            f'vertical-align:middle;'
            f'">#{rank}</span>'
        )

    origin  = getattr(route, "origin_region",  getattr(route, "origin_locode",  "—"))
    dest    = getattr(route, "dest_region",     getattr(route, "dest_locode",    "—"))
    transit = getattr(route, "transit_days",    "—")
    label   = getattr(route, "opportunity_label", "")
    rate    = getattr(route, "current_rate_usd_feu", None)
    trend   = getattr(route, "rate_trend", "")
    pct_chg = getattr(route, "rate_pct_change_30d", None)

    rate_html = ""
    if rate is not None:
        trend_arrow = {"Rising": "▲", "Falling": "▼", "Stable": "→"}.get(trend, "")
        trend_color = {"Rising": C_HIGH, "Falling": C_LOW, "Stable": C_TEXT2}.get(trend, C_TEXT2)
        pct_str = f" {abs(pct_chg):.1f}%" if pct_chg is not None else ""
        rate_html = (
            f'<div style="font-size:0.78rem; color:{C_TEXT2}; margin-top:8px">'
            f'Rate: <span style="color:{C_TEXT}; font-weight:600">'
            f'${rate:,.0f}/FEU</span>'
            f' <span style="color:{trend_color}">{trend_arrow}{pct_str}</span>'
            f'</div>'
        )

    # Sub-score mini bars
    sub_scores = {
        "Rate Momentum": getattr(route, "rate_momentum_component", None),
        "Demand":        getattr(route, "demand_imbalance_component", None),
        "Congestion":    getattr(route, "congestion_clearance_component", None),
        "Macro":         getattr(route, "macro_tailwind_component", None),
    }
    sub_bars_html = ""
    for sub_label, sub_val in sub_scores.items():
        if sub_val is None:
            continue
        sub_color = _opportunity_color(sub_val)
        bar_width = max(4, int(sub_val * 60))
        sub_bars_html += (
            f'<div style="display:flex; align-items:center; gap:6px; margin-top:4px">'
            f'<span style="font-size:0.65rem; color:{C_TEXT3}; width:90px; flex-shrink:0">'
            f'{sub_label}</span>'
            f'<div style="background:rgba(255,255,255,0.05); border-radius:3px; '
            f'flex:1; height:4px; overflow:hidden">'
            f'<div style="background:{sub_color}; height:4px; width:{bar_width}px; '
            f'border-radius:3px"></div>'
            f'</div>'
            f'<span style="font-size:0.65rem; color:{sub_color}; width:28px; text-align:right">'
            f'{sub_val:.2f}</span>'
            f'</div>'
        )

    rationale = getattr(route, "rationale", "")
    rationale_html = ""
    if rationale:
        rationale_html = (
            f'<div style="font-size:0.75rem; color:{C_TEXT3}; '
            f'margin-top:10px; line-height:1.5; '
            f'border-top:1px solid rgba(255,255,255,0.05); padding-top:8px">'
            f'{rationale}'
            f'</div>'
        )

    st.markdown(
        f"""
        <div class="slide-in" style="
            background:{C_CARD};
            border:1px solid {bg_border};
            border-left:4px solid {color};
            border-radius:12px;
            padding:18px 20px;
            margin-bottom:10px;
        ">
          <!-- Header row -->
          <div style="display:flex; justify-content:space-between; align-items:flex-start">
            <div>
              {rank_html}
              <span style="font-size:0.95rem; font-weight:700; color:{C_TEXT}">
                {origin} → {dest}
              </span>
              <div style="font-size:0.75rem; color:{C_TEXT2}; margin-top:3px">
                Transit: {transit} days
              </div>
            </div>
            <div style="
                background:{score_bg};
                border:1px solid {_hex_to_rgba(color, 0.3)};
                border-radius:10px;
                padding:8px 14px;
                text-align:center;
                min-width:64px;
            ">
              <div style="font-size:1.5rem; font-weight:900; color:{color};
                          font-variant-numeric:tabular-nums; line-height:1">
                {score:.0%}
              </div>
              <div style="font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase;
                          letter-spacing:0.06em; margin-top:2px">
                {label}
              </div>
            </div>
          </div>
          {rate_html}
          <!-- Sub-score bars -->
          <div style="margin-top:8px">{sub_bars_html}</div>
          {rationale_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── 8. section_divider ───────────────────────────────────────────────────────

def section_divider(label: str = "") -> None:
    """Render a subtle horizontal section divider with optional label."""
    label_span = (
        '<span style="font-size:0.65rem; color:#475569; text-transform:uppercase;'
        ' letter-spacing:0.12em">' + label + "</span>"
        if label
        else ""
    )
    st.markdown(
        f"""
        <div style="display:flex; align-items:center; gap:12px; margin:28px 0">
            <div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>
            {label_span}
            <div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
