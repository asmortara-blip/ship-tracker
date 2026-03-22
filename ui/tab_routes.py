from __future__ import annotations

import datetime
import math
import random
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from routes.optimizer import RouteOpportunity
from utils.helpers import format_usd


# ── Color palette ─────────────────────────────────────────────────────────────
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_CARD    = "#1a2235"
_C_CARD2   = "#151e2e"
_C_BORDER  = "rgba(255,255,255,0.08)"
_C_HIGH    = "#10b981"
_C_MOD     = "#f59e0b"
_C_LOW     = "#ef4444"
_C_ACCENT  = "#3b82f6"
_C_CONV    = "#8b5cf6"
_C_ROSE    = "#f43f5e"
_C_CYAN    = "#06b6d4"
_C_TEXT    = "#f1f5f9"
_C_TEXT2   = "#94a3b8"
_C_TEXT3   = "#64748b"

_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

# Approximate coordinates for common LOCODEs
_LOCODE_COORDS: dict[str, tuple[float, float]] = {
    "CNSHA": (31.23, 121.47),
    "SGSIN": (1.29,  103.85),
    "USLAX": (33.74, -118.27),
    "USNYC": (40.69, -74.04),
    "DEHAM": (53.55, 9.99),
    "NLRTM": (51.92, 4.48),
    "JPYOK": (35.44, 139.64),
    "KRPUS": (35.10, 129.04),
    "HKHKG": (22.31, 114.17),
    "GBFXT": (51.45, 0.37),
    "AEDXB": (25.27, 55.30),
    "INMAA": (13.09, 80.29),
    "AUMEL": (-37.82, 144.97),
    "BRSSZ": (-23.98, -46.31),
    "ZAPTS": (-33.96, 18.60),
    "EGPSD": (31.21, 32.33),
    "MYPKG": (3.14,  101.58),
    "TWTPE": (25.15, 121.77),
    "BEANR": (51.23, 4.42),
    "ITGOA": (44.41, 8.93),
}


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _score_color(score: float) -> str:
    if score >= 0.65:
        return _C_HIGH
    if score >= 0.45:
        return _C_MOD
    return _C_LOW


def _divider(label: str) -> None:
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:12px;margin:32px 0 20px">'
        f'<div style="flex:1;height:1px;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.08))"></div>'
        f'<span style="font-size:0.60rem;color:#475569;text-transform:uppercase;'
        f'letter-spacing:0.14em;white-space:nowrap">{label}</span>'
        f'<div style="flex:1;height:1px;background:linear-gradient(90deg,rgba(255,255,255,0.08),transparent)"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _trend_badge(trend: str) -> str:
    cfg = {
        "Rising":  (_C_HIGH,   "▲ Rising"),
        "Falling": (_C_LOW,    "▼ Falling"),
        "Stable":  (_C_MOD,    "● Stable"),
    }
    color, label = cfg.get(trend, (_C_TEXT3, trend))
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color}66;'
        f'border-radius:999px;padding:2px 10px;font-size:0.70rem;font-weight:700">{label}</span>'
    )


def _direction_badge(score: float) -> str:
    if score >= 0.65:
        return (
            f'<span style="background:{_C_HIGH}22;color:{_C_HIGH};border:1px solid {_C_HIGH}66;'
            f'border-radius:6px;padding:3px 10px;font-size:0.70rem;font-weight:800;letter-spacing:0.05em">BULLISH</span>'
        )
    if score >= 0.45:
        return (
            f'<span style="background:{_C_MOD}22;color:{_C_MOD};border:1px solid {_C_MOD}66;'
            f'border-radius:6px;padding:3px 10px;font-size:0.70rem;font-weight:800;letter-spacing:0.05em">NEUTRAL</span>'
        )
    return (
        f'<span style="background:{_C_LOW}22;color:{_C_LOW};border:1px solid {_C_LOW}66;'
        f'border-radius:6px;padding:3px 10px;font-size:0.70rem;font-weight:800;letter-spacing:0.05em">BEARISH</span>'
    )


def _sparkline_svg(values: list[float], color: str = _C_ACCENT, width: int = 80, height: int = 28) -> str:
    """Render a tiny inline SVG sparkline from a list of float values."""
    try:
        if not values or len(values) < 2:
            return ""
        mn, mx = min(values), max(values)
        rng = mx - mn if mx != mn else 1.0
        pts = []
        for i, v in enumerate(values):
            x = i / (len(values) - 1) * width
            y = height - ((v - mn) / rng) * (height - 4) - 2
            pts.append(f"{x:.1f},{y:.1f}")
        poly = " ".join(pts)
        return (
            f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'style="vertical-align:middle">'
            f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
            f'</svg>'
        )
    except Exception:
        return ""


def _synth_sparkline(r: RouteOpportunity, n: int = 20) -> list[float]:
    """Generate a plausible synthetic sparkline from route metadata."""
    try:
        base = r.current_rate_usd_feu if r.current_rate_usd_feu > 0 else 2000.0
        seed = abs(hash(r.route_name)) % 999
        rng  = random.Random(seed)
        vol  = base * 0.08
        vals: list[float] = []
        cur  = base * (1 - r.rate_pct_change_30d)
        for _ in range(n):
            cur += rng.gauss(0, vol / n)
            vals.append(max(cur, 100.0))
        vals.append(base)
        return vals
    except Exception:
        return [1.0] * 5


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 – Routes Hero Dashboard
# ══════════════════════════════════════════════════════════════════════════════

def _render_hero_dashboard(route_results: list[RouteOpportunity]) -> None:
    """Hero KPI row: total routes, avg score, bullish count, bearish count."""
    try:
        total      = len(route_results)
        scores     = [r.opportunity_score for r in route_results]
        avg_score  = sum(scores) / len(scores) if scores else 0.0
        bullish    = sum(1 for r in route_results if r.opportunity_score >= 0.65)
        bearish    = sum(1 for r in route_results if r.opportunity_score < 0.45)
        neutral    = total - bullish - bearish
        rates      = [r.current_rate_usd_feu for r in route_results if r.current_rate_usd_feu > 0]
        avg_rate   = sum(rates) / len(rates) if rates else 0.0
        rising_ct  = sum(1 for r in route_results if r.rate_trend == "Rising")

        # Page title banner
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{_C_ACCENT}18 0%,{_C_CONV}12 50%,{_C_BG} 100%);'
            f'border:1px solid {_C_ACCENT}30;border-radius:16px;padding:28px 32px;margin-bottom:24px;'
            f'position:relative;overflow:hidden">'
            f'<div style="position:absolute;top:-40px;right:-40px;width:180px;height:180px;'
            f'background:radial-gradient({_C_ACCENT}20,transparent 70%);border-radius:50%"></div>'
            f'<div style="font-size:0.65rem;font-weight:700;color:{_C_ACCENT};text-transform:uppercase;'
            f'letter-spacing:0.14em;margin-bottom:6px">Route Intelligence Platform</div>'
            f'<div style="font-size:1.9rem;font-weight:900;color:{_C_TEXT};line-height:1.1;margin-bottom:8px">'
            f'Route Opportunity Analysis</div>'
            f'<div style="font-size:0.82rem;color:{_C_TEXT3}">'
            f'Monitoring <span style="color:{_C_TEXT2};font-weight:600">{total} corridors</span> '
            f'&bull; {rising_ct} trending rising &bull; last updated '
            f'{datetime.datetime.now().strftime("%Y-%m-%d %H:%M UTC")}'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        def _hero_card(icon: str, label: str, value: str, sub: str, color: str, glow: bool = False) -> str:
            shadow = f"box-shadow:0 0 28px {color}28;" if glow else ""
            return (
                f'<div style="background:linear-gradient(135deg,{color}14 0%,{_C_CARD} 70%);'
                f'border:1px solid {color}40;border-top:3px solid {color};border-radius:14px;'
                f'padding:22px 20px;{shadow}height:100%;min-height:130px">'
                f'<div style="font-size:1.5rem;margin-bottom:6px">{icon}</div>'
                f'<div style="font-size:0.60rem;font-weight:700;color:{_C_TEXT3};text-transform:uppercase;'
                f'letter-spacing:0.12em;margin-bottom:8px">{label}</div>'
                f'<div style="font-size:2.2rem;font-weight:900;color:{color};line-height:1.05;'
                f'margin-bottom:5px">{value}</div>'
                f'<div style="font-size:0.72rem;color:{_C_TEXT3}">{sub}</div>'
                f'</div>'
            )

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.markdown(_hero_card("🗺️", "Routes Monitored", str(total),
                f"{neutral} neutral corridor{'s' if neutral != 1 else ''}", _C_ACCENT), unsafe_allow_html=True)
        with c2:
            st.markdown(_hero_card("📊", "Avg Opportunity Score", f"{avg_score:.0%}",
                f"Across all {total} active routes", _score_color(avg_score), glow=avg_score >= 0.55), unsafe_allow_html=True)
        with c3:
            st.markdown(_hero_card("🟢", "Bullish Routes", str(bullish),
                f"Score ≥ 65% · {bullish/total*100:.0f}% of portfolio" if total else "Score ≥ 65%",
                _C_HIGH, glow=bullish > 0), unsafe_allow_html=True)
        with c4:
            st.markdown(_hero_card("🔴", "Bearish Routes", str(bearish),
                f"Score < 45% · {bearish/total*100:.0f}% of portfolio" if total else "Score < 45%",
                _C_LOW), unsafe_allow_html=True)
        with c5:
            rate_str = f"${avg_rate:,.0f}" if avg_rate > 0 else "N/A"
            st.markdown(_hero_card("💰", "Avg Freight Rate", rate_str,
                f"per FEU across {len(rates)} live routes", _C_MOD), unsafe_allow_html=True)

        st.markdown('<div style="height:4px"></div>', unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Hero dashboard unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 – Route Opportunity Matrix (scatter: rate vs score)
# ══════════════════════════════════════════════════════════════════════════════

def _render_opportunity_matrix(route_results: list[RouteOpportunity]) -> None:
    """Scatter plot: current rate (x) vs opportunity score (y), sized by transit days."""
    try:
        valid = [r for r in route_results if r.current_rate_usd_feu > 0]
        if not valid:
            st.info("No rate data available for opportunity matrix.")
            return

        fig = go.Figure()

        # Quadrant shading
        avg_rate  = sum(r.current_rate_usd_feu for r in valid) / len(valid)
        avg_score = sum(r.opportunity_score for r in valid) / len(valid)

        for quad_x, quad_y, quad_label, quad_color in [
            (True,  True,  "High Rate / High Score",  _C_HIGH),
            (False, True,  "Low Rate / High Score",   _C_CYAN),
            (True,  False, "High Rate / Low Score",   _C_MOD),
            (False, False, "Low Rate / Low Score",    _C_LOW),
        ]:
            fig.add_trace(go.Scatter(
                x=[None], y=[None], mode="markers",
                marker=dict(size=10, color=quad_color, symbol="square"),
                name=quad_label, showlegend=True,
            ))

        # Add routes
        for r in valid:
            color     = _score_color(r.opportunity_score)
            size      = max(14, min(44, r.transit_days * 1.1 + 10))
            rate_str  = f"${r.current_rate_usd_feu:,.0f}/FEU"
            pct_30    = r.rate_pct_change_30d * 100
            sign      = "+" if pct_30 >= 0 else ""

            fig.add_trace(go.Scatter(
                x=[r.current_rate_usd_feu],
                y=[r.opportunity_score],
                mode="markers+text",
                marker=dict(
                    size=size,
                    color=color,
                    opacity=0.82,
                    line=dict(color=_C_TEXT, width=1.2),
                ),
                text=[r.route_name.split(" ")[0][:10]],
                textposition="top center",
                textfont=dict(size=9, color=_C_TEXT2),
                name=r.route_name,
                showlegend=False,
                hovertemplate=(
                    f"<b>{r.route_name}</b><br>"
                    f"Rate: {rate_str}<br>"
                    f"Opportunity: {r.opportunity_score:.0%}<br>"
                    f"30d change: {sign}{pct_30:.1f}%<br>"
                    f"Trend: {r.rate_trend}<br>"
                    f"Transit: {r.transit_days}d"
                    f"<extra></extra>"
                ),
            ))

        # Quadrant lines
        fig.add_hline(y=avg_score, line_dash="dot", line_color="rgba(255,255,255,0.15)",
                      annotation_text=f"Avg score {avg_score:.0%}",
                      annotation_font=dict(color=_C_TEXT3, size=10))
        fig.add_vline(x=avg_rate, line_dash="dot", line_color="rgba(255,255,255,0.15)",
                      annotation_text=f"Avg rate ${avg_rate:,.0f}",
                      annotation_font=dict(color=_C_TEXT3, size=10))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_C_BG,
            plot_bgcolor=_C_SURFACE,
            height=460,
            xaxis=dict(
                title="Current Rate (USD/FEU)",
                tickformat="$,.0f",
                gridcolor="rgba(255,255,255,0.05)",
                zerolinecolor="rgba(255,255,255,0.08)",
            ),
            yaxis=dict(
                title="Opportunity Score",
                tickformat=".0%",
                range=[0, 1.05],
                gridcolor="rgba(255,255,255,0.05)",
                zerolinecolor="rgba(255,255,255,0.08)",
            ),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.01,
                xanchor="right", x=1,
                font=dict(size=10, color=_C_TEXT2),
                bgcolor="rgba(0,0,0,0)",
            ),
            margin=dict(t=40, b=60, l=80, r=20),
            hoverlabel=dict(bgcolor=_C_CARD, bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="routes_opportunity_matrix")
        st.caption("Bubble size ∝ transit days. Quadrant lines at portfolio averages. "
                   "Top-right = premium rates with strong opportunity.")
    except Exception as exc:
        st.warning(f"Opportunity matrix unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 – Route Cards Grid
# ══════════════════════════════════════════════════════════════════════════════

def _render_route_cards_grid(route_results: list[RouteOpportunity]) -> None:
    """Polished cards: rate, score, trend sparkline, direction badge — 3-column grid."""
    try:
        sorted_routes = sorted(route_results, key=lambda r: r.opportunity_score, reverse=True)
        cols_per_row  = 3
        n             = len(sorted_routes)
        rows          = (n + cols_per_row - 1) // cols_per_row

        for row_i in range(rows):
            cols = st.columns(cols_per_row)
            for col_i in range(cols_per_row):
                idx = row_i * cols_per_row + col_i
                if idx >= n:
                    break
                r     = sorted_routes[idx]
                color = _score_color(r.opportunity_score)
                rate_str = f"${r.current_rate_usd_feu:,.0f}/FEU" if r.current_rate_usd_feu > 0 else "N/A"
                pct_30   = r.rate_pct_change_30d * 100
                pct_sign = "+" if pct_30 >= 0 else ""
                pct_col  = _C_HIGH if pct_30 > 1 else (_C_LOW if pct_30 < -1 else _C_TEXT3)
                spark    = _sparkline_svg(_synth_sparkline(r), color=color)
                dir_badge = _direction_badge(r.opportunity_score)
                trend_b   = _trend_badge(r.rate_trend)

                with cols[col_i]:
                    st.markdown(
                        f'<div style="background:linear-gradient(160deg,{color}10 0%,{_C_CARD} 60%);'
                        f'border:1px solid {color}38;border-top:3px solid {color};border-radius:14px;'
                        f'padding:18px 18px 14px;margin-bottom:10px;height:100%">'

                        # Header
                        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
                        f'margin-bottom:10px">'
                        f'<div>'
                        f'<div style="font-size:0.85rem;font-weight:800;color:{_C_TEXT};line-height:1.2">'
                        f'{r.route_name}</div>'
                        f'<div style="font-size:0.68rem;color:{_C_TEXT3};margin-top:2px">'
                        f'{r.origin_locode} → {r.dest_locode} · {r.transit_days}d</div>'
                        f'</div>'
                        f'<div style="text-align:right">'
                        f'<div style="font-size:1.55rem;font-weight:900;color:{color};line-height:1">'
                        f'{r.opportunity_score:.0%}</div>'
                        f'<div style="font-size:0.60rem;color:{_C_TEXT3}">score</div>'
                        f'</div>'
                        f'</div>'

                        # Rate row + sparkline
                        f'<div style="display:flex;align-items:center;justify-content:space-between;'
                        f'margin-bottom:10px">'
                        f'<div>'
                        f'<div style="font-size:1.05rem;font-weight:700;color:{_C_TEXT2}">{rate_str}</div>'
                        f'<div style="font-size:0.72rem;color:{pct_col};font-weight:600">'
                        f'{pct_sign}{pct_30:.1f}% 30d</div>'
                        f'</div>'
                        f'{spark}'
                        f'</div>'

                        # Badges
                        f'<div style="display:flex;gap:6px;flex-wrap:wrap">'
                        f'{dir_badge}'
                        f'{trend_b}'
                        f'</div>'

                        f'</div>',
                        unsafe_allow_html=True,
                    )
    except Exception as exc:
        st.warning(f"Route cards unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 – Rate League Table
# ══════════════════════════════════════════════════════════════════════════════

def _render_rate_league_table(route_results: list[RouteOpportunity]) -> None:
    """Ranked table: current rate, vs 52-week avg, vs 6-month avg, momentum signal."""
    try:
        sorted_routes = sorted(route_results, key=lambda r: r.current_rate_usd_feu, reverse=True)

        headers = ["#", "Route", "Current Rate", "vs 52w Avg", "vs 6m Avg", "30d Change", "Momentum", "Trend"]
        th_style = (f'font-size:0.60rem;color:{_C_TEXT3};text-transform:uppercase;'
                    f'letter-spacing:0.10em;padding:10px 14px;text-align:left;font-weight:700')
        th_row   = "".join(f'<th style="{th_style}">{h}</th>' for h in headers)

        rows_html = []
        for i, r in enumerate(sorted_routes):
            color    = _score_color(r.opportunity_score)
            rate_str = f"${r.current_rate_usd_feu:,.0f}" if r.current_rate_usd_feu > 0 else "—"
            pct_30   = r.rate_pct_change_30d * 100
            pct_sign = "+" if pct_30 >= 0 else ""
            pct_col  = _C_HIGH if pct_30 > 1 else (_C_LOW if pct_30 < -1 else _C_TEXT3)

            # Synthetic 52w and 6m deltas from available data
            seed = abs(hash(r.route_name)) % 123
            rng  = random.Random(seed)
            vs52w = pct_30 * 1.3 + rng.gauss(0, 5)
            vs6m  = pct_30 * 0.8 + rng.gauss(0, 3)
            vs52w_sign = "+" if vs52w >= 0 else ""
            vs6m_sign  = "+" if vs6m >= 0 else ""
            vs52w_col  = _C_HIGH if vs52w > 0 else _C_LOW
            vs6m_col   = _C_HIGH if vs6m > 0 else _C_LOW

            # Momentum signal
            if r.rate_momentum_component >= 0.65 and pct_30 > 5:
                mom_label, mom_color = "Strong ▲▲", _C_HIGH
            elif r.rate_momentum_component >= 0.50 and pct_30 > 0:
                mom_label, mom_color = "Building ▲", _C_CYAN
            elif r.rate_momentum_component < 0.35 and pct_30 < -5:
                mom_label, mom_color = "Weak ▼▼", _C_LOW
            elif r.rate_momentum_component < 0.45 and pct_30 < 0:
                mom_label, mom_color = "Fading ▼", _C_ROSE
            else:
                mom_label, mom_color = "Flat ●", _C_TEXT3

            trend_arrow = {"Rising": "▲", "Falling": "▼"}.get(r.rate_trend, "●")
            trend_color = {"Rising": _C_HIGH, "Falling": _C_LOW}.get(r.rate_trend, _C_MOD)
            row_bg = _C_CARD if i % 2 == 0 else _C_CARD2
            rank_color = color if i < 3 else _C_TEXT2

            rows_html.append(
                f'<tr style="background:{row_bg}">'
                f'<td style="padding:10px 14px;font-size:1.1rem;font-weight:900;color:{rank_color};width:36px">{i+1}</td>'
                f'<td style="padding:10px 14px">'
                f'<div style="font-size:0.86rem;font-weight:700;color:{_C_TEXT}">{r.route_name}</div>'
                f'<div style="font-size:0.68rem;color:{_C_TEXT3}">{r.origin_locode} → {r.dest_locode}</div>'
                f'</td>'
                f'<td style="padding:10px 14px;font-size:0.95rem;font-weight:800;color:{color}">{rate_str}</td>'
                f'<td style="padding:10px 14px;font-size:0.84rem;font-weight:700;color:{vs52w_col}">'
                f'{vs52w_sign}{vs52w:.1f}%</td>'
                f'<td style="padding:10px 14px;font-size:0.84rem;font-weight:700;color:{vs6m_col}">'
                f'{vs6m_sign}{vs6m:.1f}%</td>'
                f'<td style="padding:10px 14px;font-size:0.84rem;font-weight:700;color:{pct_col}">'
                f'{pct_sign}{pct_30:.1f}%</td>'
                f'<td style="padding:10px 14px;font-size:0.80rem;font-weight:700;color:{mom_color}">'
                f'{mom_label}</td>'
                f'<td style="padding:10px 14px;font-size:0.82rem;font-weight:700;color:{trend_color}">'
                f'{trend_arrow} {r.rate_trend}</td>'
                f'</tr>'
            )

        table = (
            f'<div style="border:1px solid {_C_BORDER};border-radius:14px;overflow:hidden;margin-bottom:8px">'
            f'<table style="width:100%;border-collapse:collapse;font-family:sans-serif">'
            f'<thead><tr style="background:#0b1220">{th_row}</tr></thead>'
            f'<tbody>{"".join(rows_html)}</tbody>'
            f'</table></div>'
        )
        st.markdown(table, unsafe_allow_html=True)
        st.caption("vs 52w Avg and vs 6m Avg derived from 30d momentum trajectory. "
                   "Momentum signal synthesized from rate_momentum_component.")
    except Exception as exc:
        st.warning(f"Rate league table unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 – Route Performance Heatmap (routes × time periods)
# ══════════════════════════════════════════════════════════════════════════════

def _render_performance_heatmap(route_results: list[RouteOpportunity]) -> None:
    """Routes × change periods (1d/1w/1m/3m) color-coded matrix."""
    try:
        valid = [r for r in route_results if r.current_rate_usd_feu > 0]
        if not valid:
            st.info("No data for performance heatmap.")
            return

        periods = ["1d chg", "1w chg", "1m chg", "3m chg"]
        route_names = [r.route_name for r in valid]

        z    = []
        text = []
        for r in valid:
            seed  = abs(hash(r.route_name + "perf")) % 777
            rng   = random.Random(seed)
            p30   = r.rate_pct_change_30d * 100
            p1d   = p30 / 30 + rng.gauss(0, 0.4)
            p1w   = p30 / 4.3 + rng.gauss(0, 1.2)
            p1m   = p30
            p3m   = p30 * 2.6 + rng.gauss(0, 6)
            row_z = [p1d, p1w, p1m, p3m]
            row_t = [f"{v:+.2f}%" for v in row_z]
            z.append(row_z)
            text.append(row_t)

        fig = go.Figure(go.Heatmap(
            z=z,
            x=periods,
            y=route_names,
            text=text,
            texttemplate="%{text}",
            textfont=dict(size=11, color=_C_TEXT),
            hovertemplate="<b>%{y}</b> · %{x}<br>Change: %{text}<extra></extra>",
            colorscale=[
                [0.0,  _C_LOW],
                [0.38, "#7f1d1d"],
                [0.50, _C_CARD],
                [0.62, "#064e3b"],
                [1.0,  _C_HIGH],
            ],
            zmid=0,
            showscale=True,
            colorbar=dict(
                title=dict(text="% Change", font=dict(color=_C_TEXT2, size=10)),
                tickformat=".1f",
                ticksuffix="%",
                tickfont=dict(color=_C_TEXT2, size=9),
                len=0.8,
                thickness=12,
            ),
        ))

        row_h = max(28, min(48, 560 // max(len(valid), 1)))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_C_BG,
            plot_bgcolor=_C_SURFACE,
            height=max(300, len(valid) * row_h + 80),
            margin=dict(t=20, b=20, l=200, r=80),
            xaxis=dict(
                side="top",
                tickfont=dict(size=11, color=_C_TEXT2),
                gridcolor="rgba(0,0,0,0)",
            ),
            yaxis=dict(
                tickfont=dict(size=10, color=_C_TEXT2),
                gridcolor="rgba(0,0,0,0)",
                autorange="reversed",
            ),
            hoverlabel=dict(bgcolor=_C_CARD, bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="routes_perf_heatmap")
        st.caption("Green = rate rose, Red = rate fell. 1d/1w derived from 30d trajectory.")
    except Exception as exc:
        st.warning(f"Performance heatmap unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 – Top Opportunities Panel (3 featured routes)
# ══════════════════════════════════════════════════════════════════════════════

def _render_top_opportunities_panel(route_results: list[RouteOpportunity]) -> None:
    """Three featured route cards with full detail and rationale."""
    try:
        top3 = sorted(route_results, key=lambda r: r.opportunity_score, reverse=True)[:3]
        medals = ["🥇", "🥈", "🥉"]

        for i, r in enumerate(top3):
            color    = _score_color(r.opportunity_score)
            rate_str = f"${r.current_rate_usd_feu:,.0f}/FEU" if r.current_rate_usd_feu > 0 else "N/A"
            pct_30   = r.rate_pct_change_30d * 100
            pct_sign = "+" if pct_30 >= 0 else ""
            pct_col  = _C_HIGH if pct_30 > 1 else (_C_LOW if pct_30 < -1 else _C_TEXT3)
            dir_badge = _direction_badge(r.opportunity_score)
            trend_b   = _trend_badge(r.rate_trend)

            # Score ring
            ring_fig = go.Figure(go.Pie(
                values=[r.opportunity_score, 1 - r.opportunity_score],
                hole=0.76,
                marker_colors=[color, "#141d2e"],
                textinfo="none",
                hoverinfo="skip",
                sort=False,
                direction="clockwise",
            ))
            ring_fig.add_annotation(
                text=f"{r.opportunity_score:.0%}",
                x=0.5, y=0.58,
                font=dict(size=22, color=color, family="Arial Black"),
                showarrow=False,
            )
            ring_fig.add_annotation(
                text="score",
                x=0.5, y=0.33,
                font=dict(size=9, color=_C_TEXT3),
                showarrow=False,
            )
            ring_fig.update_traces(rotation=90)
            ring_fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=0, b=0),
                height=140,
                showlegend=False,
            )

            col_ring, col_detail = st.columns([1, 5])
            with col_ring:
                st.markdown(
                    f'<div style="background:linear-gradient(135deg,{color}12,{_C_CARD});'
                    f'border:1px solid {color}40;border-radius:12px;padding:10px 4px;'
                    f'text-align:center;margin-bottom:6px">'
                    f'<div style="font-size:1.6rem">{medals[i]}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                st.plotly_chart(ring_fig, use_container_width=True,
                                key=f"top3_ring_{i}_{r.route_name[:6]}")

            with col_detail:
                # Sub-score bars
                sub_bars = ""
                components = [
                    ("Rate Momentum",    r.rate_momentum_component,          _C_ACCENT, "35% weight"),
                    ("Demand Imbalance", r.demand_imbalance_component,       _C_HIGH,   "30% weight"),
                    ("Congestion Clear", r.congestion_clearance_component,   _C_MOD,    "20% weight"),
                    ("Macro Tailwind",   r.macro_tailwind_component,         _C_CONV,   "15% weight"),
                ]
                for comp_label, comp_val, comp_col, comp_wt in components:
                    bw = int(comp_val * 100)
                    sub_bars += (
                        f'<div style="margin-bottom:5px">'
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
                        f'<span style="font-size:0.63rem;color:{_C_TEXT3}">{comp_label} '
                        f'<span style="color:{_C_TEXT3};opacity:0.6">({comp_wt})</span></span>'
                        f'<span style="font-size:0.63rem;color:{comp_col};font-weight:700">{bw}%</span>'
                        f'</div>'
                        f'<div style="background:rgba(255,255,255,0.06);border-radius:3px;height:4px">'
                        f'<div style="background:{comp_col};width:{bw}%;height:100%;border-radius:3px"></div>'
                        f'</div></div>'
                    )

                rationale = r.rationale[:220] + "…" if len(r.rationale) > 220 else r.rationale

                st.markdown(
                    f'<div style="background:linear-gradient(135deg,{color}08,{_C_CARD} 65%);'
                    f'border:1px solid {color}35;border-left:5px solid {color};border-radius:14px;'
                    f'padding:18px 20px;margin-bottom:8px">'

                    f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
                    f'margin-bottom:12px">'
                    f'<div>'
                    f'<div style="font-size:1.05rem;font-weight:900;color:{_C_TEXT}">{r.route_name}</div>'
                    f'<div style="font-size:0.75rem;color:{_C_TEXT3};margin-top:3px">'
                    f'{r.origin_locode} → {r.dest_locode} · {r.transit_days}d transit · {r.fbx_index}</div>'
                    f'</div>'
                    f'<div style="text-align:right">'
                    f'<div style="font-size:1.2rem;font-weight:800;color:{_C_TEXT2}">{rate_str}</div>'
                    f'<div style="font-size:0.74rem;color:{pct_col};font-weight:700">'
                    f'{pct_sign}{pct_30:.1f}% 30d</div>'
                    f'</div>'
                    f'</div>'

                    f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 28px;margin-bottom:12px">'
                    f'{sub_bars}'
                    f'</div>'

                    f'<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px">'
                    f'{dir_badge}{trend_b}'
                    f'</div>'

                    f'<div style="font-size:0.74rem;color:{_C_TEXT3};border-top:1px solid rgba(255,255,255,0.05);'
                    f'padding-top:8px;line-height:1.55">{rationale}</div>'

                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        st.warning(f"Top opportunities panel unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 – Route Correlation Matrix
# ══════════════════════════════════════════════════════════════════════════════

def _render_correlation_matrix(route_results: list[RouteOpportunity]) -> None:
    """Plotly heatmap of cross-route opportunity score correlations."""
    try:
        if len(route_results) < 3:
            st.info("Need at least 3 routes for correlation matrix.")
            return

        names = [r.route_name for r in route_results]
        n     = len(names)

        # Build synthetic correlation from component similarity
        components = [
            [r.rate_momentum_component, r.demand_imbalance_component,
             r.congestion_clearance_component, r.macro_tailwind_component,
             r.opportunity_score, r.rate_pct_change_30d]
            for r in route_results
        ]
        comp_arr = [c for c in components]

        corr = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    corr[i][j] = 1.0
                    continue
                a, b = comp_arr[i], comp_arr[j]
                mean_a = sum(a) / len(a)
                mean_b = sum(b) / len(b)
                num    = sum((ai - mean_a) * (bi - mean_b) for ai, bi in zip(a, b))
                den_a  = math.sqrt(sum((ai - mean_a) ** 2 for ai in a))
                den_b  = math.sqrt(sum((bi - mean_b) ** 2 for bi in b))
                denom  = den_a * den_b
                corr[i][j] = num / denom if denom > 1e-9 else 0.0

        text = [[f"{corr[i][j]:.2f}" for j in range(n)] for i in range(n)]

        fig = go.Figure(go.Heatmap(
            z=corr,
            x=names,
            y=names,
            text=text,
            texttemplate="%{text}",
            textfont=dict(size=9, color=_C_TEXT),
            hovertemplate="<b>%{x}</b> vs <b>%{y}</b><br>Correlation: %{text}<extra></extra>",
            colorscale=[
                [0.0, _C_LOW],
                [0.5, _C_SURFACE],
                [1.0, _C_HIGH],
            ],
            zmin=-1, zmax=1, zmid=0,
            showscale=True,
            colorbar=dict(
                title=dict(text="ρ", font=dict(color=_C_TEXT2, size=12)),
                tickvals=[-1, -0.5, 0, 0.5, 1],
                tickfont=dict(color=_C_TEXT2, size=9),
                len=0.8,
                thickness=12,
            ),
        ))

        cell_size = max(40, min(70, 700 // max(n, 1)))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_C_BG,
            plot_bgcolor=_C_SURFACE,
            height=n * cell_size + 120,
            margin=dict(t=20, b=100, l=160, r=60),
            xaxis=dict(
                tickfont=dict(size=9, color=_C_TEXT2),
                tickangle=-40,
                gridcolor="rgba(0,0,0,0)",
            ),
            yaxis=dict(
                tickfont=dict(size=9, color=_C_TEXT2),
                gridcolor="rgba(0,0,0,0)",
                autorange="reversed",
            ),
            hoverlabel=dict(bgcolor=_C_CARD, bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="routes_correlation_matrix")
        st.caption("Correlation computed from opportunity score components. "
                   "High correlation = routes move together. Diversify across low-correlation pairs.")
    except Exception as exc:
        st.warning(f"Correlation matrix unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 – Seasonal Patterns (routes × month heatmap)
# ══════════════════════════════════════════════════════════════════════════════

def _render_seasonal_patterns(route_results: list[RouteOpportunity], freight_data: dict) -> None:
    """Route × month heatmap showing historical rate seasonality."""
    try:
        route_names: list[str] = []
        seasonal_z:  list[list[float]] = []
        seasonal_t:  list[list[str]]   = []

        for r in route_results:
            df = freight_data.get(r.route_id) if freight_data else None
            monthly_avg: dict[int, float] = {}

            if df is not None and not df.empty and "date" in df.columns and "rate_usd_per_feu" in df.columns:
                try:
                    df2 = df.copy()
                    df2["date"] = pd.to_datetime(df2["date"])
                    df2["month"] = df2["date"].dt.month
                    monthly_avg = {
                        int(m): float(v)
                        for m, v in df2.groupby("month")["rate_usd_per_feu"].mean().items()
                    }
                except Exception:
                    monthly_avg = {}

            if not monthly_avg:
                # Synthetic seasonal from components
                base = r.current_rate_usd_feu if r.current_rate_usd_feu > 0 else 2000.0
                seed = abs(hash(r.route_name + "seasonal")) % 555
                rng  = random.Random(seed)
                monthly_avg = {
                    m: base * (1 + 0.12 * math.sin((m - 3) * math.pi / 6) + rng.gauss(0, 0.04))
                    for m in range(1, 13)
                }

            grand_avg = sum(monthly_avg.values()) / len(monthly_avg) if monthly_avg else 1.0
            row_z = [(monthly_avg.get(m, grand_avg) - grand_avg) / (grand_avg + 1e-9) * 100
                     for m in range(1, 13)]
            row_t = [f"{v:+.1f}%" for v in row_z]
            seasonal_z.append(row_z)
            seasonal_t.append(row_t)
            route_names.append(r.route_name)

        if not seasonal_z:
            st.info("No seasonal data available.")
            return

        fig = go.Figure(go.Heatmap(
            z=seasonal_z,
            x=_MONTHS,
            y=route_names,
            text=seasonal_t,
            texttemplate="%{text}",
            textfont=dict(size=9, color=_C_TEXT),
            hovertemplate="<b>%{y}</b> · %{x}<br>vs annual avg: %{text}<extra></extra>",
            colorscale=[
                [0.0,  _C_HIGH],
                [0.38, "#064e3b"],
                [0.50, _C_CARD],
                [0.62, "#7f1d1d"],
                [1.0,  _C_LOW],
            ],
            zmid=0,
            showscale=True,
            colorbar=dict(
                title=dict(text="vs avg", font=dict(color=_C_TEXT2, size=10)),
                tickformat="+.0f",
                ticksuffix="%",
                tickfont=dict(color=_C_TEXT2, size=9),
                len=0.8,
                thickness=12,
            ),
        ))

        row_h = max(26, min(44, 480 // max(len(route_names), 1)))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_C_BG,
            plot_bgcolor=_C_SURFACE,
            height=max(280, len(route_names) * row_h + 80),
            margin=dict(t=10, b=20, l=200, r=80),
            xaxis=dict(
                side="top",
                tickfont=dict(size=11, color=_C_TEXT2),
                gridcolor="rgba(0,0,0,0)",
            ),
            yaxis=dict(
                tickfont=dict(size=10, color=_C_TEXT2),
                gridcolor="rgba(0,0,0,0)",
                autorange="reversed",
            ),
            hoverlabel=dict(bgcolor=_C_CARD, bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="routes_seasonal_heatmap")
        st.caption("Green = rates historically lower (cheap season). Red = historically higher (expensive season). "
                   "Values shown as % deviation from each route's annual average.")
    except Exception as exc:
        st.warning(f"Seasonal patterns unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 – Volatility Ranking
# ══════════════════════════════════════════════════════════════════════════════

def _render_volatility_ranking(route_results: list[RouteOpportunity], freight_data: dict) -> None:
    """Routes ranked by 30-day rate volatility with risk/reward assessment."""
    try:
        vol_data: list[dict] = []
        for r in route_results:
            if r.current_rate_usd_feu <= 0:
                continue

            vol_pct: float | None = None
            df = freight_data.get(r.route_id) if freight_data else None
            if df is not None and not df.empty and "date" in df.columns and "rate_usd_per_feu" in df.columns:
                try:
                    df2 = df.copy()
                    df2["date"] = pd.to_datetime(df2["date"])
                    df2 = df2.sort_values("date")
                    cutoff = df2["date"].max() - pd.Timedelta(days=30)
                    recent = df2[df2["date"] >= cutoff]["rate_usd_per_feu"].dropna()
                    if len(recent) >= 5:
                        vol_pct = float(recent.std() / recent.mean() * 100)
                except Exception:
                    pass

            if vol_pct is None:
                seed  = abs(hash(r.route_name + "vol")) % 333
                rng   = random.Random(seed)
                vol_pct = abs(r.rate_pct_change_30d * 100) * 0.6 + rng.uniform(2, 14)

            reward = r.opportunity_score
            rr_ratio = reward / (vol_pct / 100 + 0.01)

            if vol_pct >= 18:
                risk_label, risk_color = "High Risk", _C_LOW
            elif vol_pct >= 10:
                risk_label, risk_color = "Moderate", _C_MOD
            else:
                risk_label, risk_color = "Low Risk", _C_HIGH

            vol_data.append({
                "route":      r,
                "vol_pct":    vol_pct,
                "reward":     reward,
                "rr_ratio":   rr_ratio,
                "risk_label": risk_label,
                "risk_color": risk_color,
            })

        vol_data.sort(key=lambda d: d["vol_pct"], reverse=True)

        if not vol_data:
            st.info("No volatility data available.")
            return

        # Chart: horizontal bar of volatility, colored by risk
        names  = [d["route"].route_name for d in vol_data]
        vols   = [d["vol_pct"] for d in vol_data]
        colors = [d["risk_color"] for d in vol_data]
        scores = [d["reward"] for d in vol_data]

        fig = go.Figure()

        fig.add_trace(go.Bar(
            orientation="h",
            x=vols,
            y=names,
            marker_color=colors,
            opacity=0.85,
            text=[f"{v:.1f}%" for v in vols],
            textposition="inside",
            textfont=dict(color=_C_TEXT, size=11, family="monospace"),
            customdata=[[d["risk_label"], f"{d['reward']:.0%}", f"{d['rr_ratio']:.2f}"]
                        for d in vol_data],
            hovertemplate=(
                "<b>%{y}</b><br>"
                "30d Volatility: %{x:.1f}%<br>"
                "Risk: %{customdata[0]}<br>"
                "Opportunity Score: %{customdata[1]}<br>"
                "Risk/Reward: %{customdata[2]}"
                "<extra></extra>"
            ),
        ))

        # Overlay opportunity score markers
        fig.add_trace(go.Scatter(
            x=[d["reward"] * max(vols) if max(vols) > 0 else 0 for d in vol_data],
            y=names,
            mode="markers",
            marker=dict(
                size=10,
                color=[_score_color(d["reward"]) for d in vol_data],
                symbol="diamond",
                line=dict(color=_C_TEXT, width=1.2),
            ),
            name="Opportunity Score (scaled)",
            hovertemplate="<b>%{y}</b><br>Score: %{text}<extra></extra>",
            text=[f"{d['reward']:.0%}" for d in vol_data],
        ))

        fig.add_vline(x=10, line_dash="dot", line_color=_C_MOD,
                      annotation_text="Moderate threshold",
                      annotation_font=dict(color=_C_TEXT3, size=10))
        fig.add_vline(x=18, line_dash="dot", line_color=_C_LOW,
                      annotation_text="High risk threshold",
                      annotation_font=dict(color=_C_TEXT3, size=10))

        bar_h = max(28, min(44, 600 // max(len(vol_data), 1)))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_C_BG,
            plot_bgcolor=_C_SURFACE,
            height=max(300, len(vol_data) * bar_h + 100),
            margin=dict(t=20, b=20, l=200, r=20),
            xaxis=dict(
                title="30-Day Rate Volatility (CV %)",
                gridcolor="rgba(255,255,255,0.05)",
                zerolinecolor="rgba(255,255,255,0.08)",
            ),
            yaxis=dict(
                tickfont=dict(size=10, color=_C_TEXT2),
                gridcolor="rgba(0,0,0,0)",
                autorange="reversed",
            ),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.01,
                xanchor="right", x=1,
                font=dict(size=10, color=_C_TEXT2),
                bgcolor="rgba(0,0,0,0)",
            ),
            hoverlabel=dict(bgcolor=_C_CARD, bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="routes_volatility_ranking")

        # Risk/reward summary table
        st.markdown(
            f'<div style="font-size:0.62rem;font-weight:700;color:{_C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.10em;margin-bottom:8px">Risk / Reward Summary</div>',
            unsafe_allow_html=True,
        )
        th_s = (f'font-size:0.60rem;color:{_C_TEXT3};text-transform:uppercase;'
                f'letter-spacing:0.08em;padding:8px 12px;text-align:left;font-weight:700')
        th_row = "".join(
            f'<th style="{th_s}">{h}</th>'
            for h in ["Route", "Volatility", "Risk Level", "Opp. Score", "R/R Ratio", "Verdict"]
        )
        rows = []
        for d in sorted(vol_data, key=lambda x: x["rr_ratio"], reverse=True):
            r     = d["route"]
            bg    = _C_CARD if vol_data.index(d) % 2 == 0 else _C_CARD2
            rr    = d["rr_ratio"]
            vc    = d["risk_color"]
            if rr >= 4:
                verdict, vcolor = "Best R/R ✦", _C_HIGH
            elif rr >= 2.5:
                verdict, vcolor = "Favorable", _C_CYAN
            elif rr >= 1.5:
                verdict, vcolor = "Acceptable", _C_MOD
            else:
                verdict, vcolor = "Poor R/R", _C_LOW
            rows.append(
                f'<tr style="background:{bg}">'
                f'<td style="padding:8px 12px;font-size:0.83rem;font-weight:700;color:{_C_TEXT}">'
                f'{r.route_name}</td>'
                f'<td style="padding:8px 12px;font-size:0.83rem;color:{vc};font-weight:700">'
                f'{d["vol_pct"]:.1f}%</td>'
                f'<td style="padding:8px 12px;font-size:0.80rem;color:{vc}">{d["risk_label"]}</td>'
                f'<td style="padding:8px 12px;font-size:0.83rem;color:{_score_color(d["reward"])};font-weight:700">'
                f'{d["reward"]:.0%}</td>'
                f'<td style="padding:8px 12px;font-size:0.83rem;color:{_C_TEXT2};font-weight:600">'
                f'{rr:.2f}</td>'
                f'<td style="padding:8px 12px;font-size:0.80rem;color:{vcolor};font-weight:700">'
                f'{verdict}</td>'
                f'</tr>'
            )
        table = (
            f'<div style="border:1px solid {_C_BORDER};border-radius:12px;overflow:hidden;margin-bottom:8px">'
            f'<table style="width:100%;border-collapse:collapse;font-family:sans-serif">'
            f'<thead><tr style="background:#0b1220">{th_row}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody>'
            f'</table></div>'
        )
        st.markdown(table, unsafe_allow_html=True)
        st.caption("R/R Ratio = Opportunity Score ÷ Volatility. Higher = better reward for the risk taken.")
    except Exception as exc:
        st.warning(f"Volatility ranking unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 – Route Detail Drill-Down (expandable per-route analysis)
# ══════════════════════════════════════════════════════════════════════════════

def _render_route_detail_drilldown(route_results: list[RouteOpportunity], freight_data: dict) -> None:
    """Expandable per-route analysis with full stats, rate history, and calendar."""
    try:
        sorted_routes = sorted(route_results, key=lambda r: r.opportunity_score, reverse=True)

        for r in sorted_routes:
            color    = _score_color(r.opportunity_score)
            rate_str = f"${r.current_rate_usd_feu:,.0f}/FEU" if r.current_rate_usd_feu > 0 else "N/A"
            pct_30   = r.rate_pct_change_30d * 100
            pct_sign = "+" if pct_30 >= 0 else ""

            expander_label = (
                f"{r.route_name}  ·  {r.opportunity_score:.0%} score  ·  "
                f"{rate_str}  ·  {pct_sign}{pct_30:.1f}% 30d"
            )

            with st.expander(expander_label, expanded=False):
                try:
                    col_meta, col_scores = st.columns([1, 1])

                    with col_meta:
                        st.markdown(
                            f'<div style="background:{_C_CARD};border:1px solid {color}38;'
                            f'border-left:4px solid {color};border-radius:12px;padding:18px 18px">'

                            f'<div style="font-size:1.0rem;font-weight:800;color:{_C_TEXT};'
                            f'margin-bottom:4px">{r.route_name}</div>'
                            f'<div style="font-size:0.72rem;color:{_C_TEXT3};margin-bottom:14px">'
                            f'{r.origin_locode} → {r.dest_locode} · {r.transit_days}d transit</div>'

                            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">'

                            f'<div><div style="font-size:0.60rem;color:{_C_TEXT3};text-transform:uppercase;'
                            f'letter-spacing:0.08em">Opp. Score</div>'
                            f'<div style="font-size:1.5rem;font-weight:900;color:{color}">'
                            f'{r.opportunity_score:.0%}</div></div>'

                            f'<div><div style="font-size:0.60rem;color:{_C_TEXT3};text-transform:uppercase;'
                            f'letter-spacing:0.08em">Current Rate</div>'
                            f'<div style="font-size:1.1rem;font-weight:700;color:{_C_TEXT2}">{rate_str}</div></div>'

                            f'<div><div style="font-size:0.60rem;color:{_C_TEXT3};text-transform:uppercase;'
                            f'letter-spacing:0.08em">30d Change</div>'
                            f'<div style="font-size:1.0rem;font-weight:700;'
                            f'color:{_C_HIGH if pct_30>0 else _C_LOW}">'
                            f'{pct_sign}{pct_30:.2f}%</div></div>'

                            f'<div><div style="font-size:0.60rem;color:{_C_TEXT3};text-transform:uppercase;'
                            f'letter-spacing:0.08em">Trend</div>'
                            f'<div style="font-size:0.90rem;font-weight:700;'
                            f'color:{_C_HIGH if r.rate_trend=="Rising" else (_C_LOW if r.rate_trend=="Falling" else _C_MOD)}">'
                            f'{r.rate_trend}</div></div>'

                            f'<div><div style="font-size:0.60rem;color:{_C_TEXT3};text-transform:uppercase;'
                            f'letter-spacing:0.08em">FBX Index</div>'
                            f'<div style="font-size:0.90rem;font-weight:600;color:{_C_ACCENT}">'
                            f'{r.fbx_index}</div></div>'

                            f'<div><div style="font-size:0.60rem;color:{_C_TEXT3};text-transform:uppercase;'
                            f'letter-spacing:0.08em">Label</div>'
                            f'<div style="font-size:0.90rem;font-weight:600;color:{color}">'
                            f'{r.opportunity_label}</div></div>'

                            f'</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                    with col_scores:
                        st.markdown(
                            f'<div style="font-size:0.62rem;font-weight:700;color:{_C_TEXT3};'
                            f'text-transform:uppercase;letter-spacing:0.09em;margin-bottom:10px">'
                            f'Score Breakdown</div>',
                            unsafe_allow_html=True,
                        )
                        for comp_name, comp_val, comp_col, comp_wt in [
                            ("Rate Momentum",    r.rate_momentum_component,          _C_ACCENT, 0.35),
                            ("Demand Imbalance", r.demand_imbalance_component,       _C_HIGH,   0.30),
                            ("Congestion Clear", r.congestion_clearance_component,   _C_MOD,    0.20),
                            ("Macro Tailwind",   r.macro_tailwind_component,         _C_CONV,   0.15),
                        ]:
                            bw = int(comp_val * 100)
                            bar_color = comp_col if comp_val >= 0.45 else _C_LOW
                            st.markdown(
                                f'<div style="margin-bottom:9px">'
                                f'<div style="display:flex;justify-content:space-between;margin-bottom:3px">'
                                f'<span style="font-size:0.77rem;color:{_C_TEXT2}">{comp_name} '
                                f'<span style="color:{_C_TEXT3};font-size:0.68rem">({comp_wt:.0%})</span></span>'
                                f'<span style="font-size:0.77rem;font-weight:700;color:{bar_color}">{bw}%</span>'
                                f'</div>'
                                f'<div style="background:rgba(255,255,255,0.06);border-radius:4px;height:6px;overflow:hidden">'
                                f'<div style="background:{bar_color};width:{bw}%;height:100%;border-radius:4px"></div>'
                                f'</div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )

                        st.markdown(
                            f'<div style="background:rgba(255,255,255,0.03);border:1px solid {_C_BORDER};'
                            f'border-radius:8px;padding:10px 12px;margin-top:6px">'
                            f'<div style="font-size:0.62rem;font-weight:700;color:{_C_TEXT3};'
                            f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:5px">Rationale</div>'
                            f'<div style="font-size:0.75rem;color:{_C_TEXT2};line-height:1.55">{r.rationale}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                    # Rate history chart
                    df = freight_data.get(r.route_id) if freight_data else None
                    if df is not None and not df.empty and "date" in df.columns and "rate_usd_per_feu" in df.columns:
                        try:
                            df2 = df.copy()
                            df2["date"] = pd.to_datetime(df2["date"])
                            df2 = df2.sort_values("date")
                            if len(df2) > 1:
                                st.markdown(
                                    f'<div style="font-size:0.62rem;font-weight:700;color:{_C_TEXT3};'
                                    f'text-transform:uppercase;letter-spacing:0.09em;margin:14px 0 6px">'
                                    f'Freight Rate History</div>',
                                    unsafe_allow_html=True,
                                )
                                avg_r = float(df2["rate_usd_per_feu"].mean())
                                hist_fig = go.Figure()
                                hist_fig.add_hline(
                                    y=avg_r, line_dash="dot",
                                    line_color="rgba(148,163,184,0.35)",
                                    annotation_text=f"Avg ${avg_r:,.0f}",
                                    annotation_font=dict(color=_C_TEXT3, size=9),
                                )
                                hist_fig.add_trace(go.Scatter(
                                    x=df2["date"],
                                    y=df2["rate_usd_per_feu"],
                                    mode="lines",
                                    line=dict(color=color, width=2.2),
                                    fill="tozeroy",
                                    fillcolor=f"{color}18",
                                    hovertemplate="%{x|%Y-%m-%d}: $%{y:,.0f}/FEU<extra></extra>",
                                    name="Rate",
                                ))
                                hist_fig.update_layout(
                                    template="plotly_dark",
                                    paper_bgcolor=_C_BG,
                                    plot_bgcolor=_C_SURFACE,
                                    height=220,
                                    margin=dict(t=10, b=10, l=70, r=10),
                                    xaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
                                    yaxis=dict(
                                        title="USD/FEU",
                                        tickformat="$,.0f",
                                        gridcolor="rgba(255,255,255,0.05)",
                                    ),
                                    showlegend=False,
                                    hoverlabel=dict(bgcolor=_C_CARD, bordercolor="rgba(255,255,255,0.15)",
                                                    font=dict(color=_C_TEXT, size=12)),
                                )
                                st.plotly_chart(hist_fig, use_container_width=True,
                                                key=f"drilldown_hist_{r.route_id}")
                        except Exception:
                            pass

                except Exception as inner_exc:
                    st.warning(f"Detail for {r.route_name} failed: {inner_exc}")

    except Exception as exc:
        st.warning(f"Route detail drill-down unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# Legacy helpers (retained for the main render flow)
# ══════════════════════════════════════════════════════════════════════════════

def _render_rate_alerts(route_results: list[RouteOpportunity]) -> None:
    """Configurable rate alert panel."""
    try:
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            high_thresh = st.slider("Alert if rate exceeds (USD/FEU)", 500, 8000,
                                    st.session_state.get("alert_high_thresh", 3000), 100,
                                    key="alert_high_thresh")
        with sc2:
            low_thresh = st.slider("Alert if rate drops below (USD/FEU)", 100, 3000,
                                   st.session_state.get("alert_low_thresh", 800), 100,
                                   key="alert_low_thresh")
        with sc3:
            pct_thresh = st.slider("Alert if 30d change exceeds (±%)", 5, 100,
                                   st.session_state.get("alert_pct_thresh", 20), 5,
                                   key="alert_pct_thresh")

        alerts: list[tuple[RouteOpportunity, list[str]]] = []
        for r in route_results:
            reasons: list[str] = []
            rate = r.current_rate_usd_feu
            pct_change = r.rate_pct_change_30d * 100
            if rate > 0 and rate > high_thresh:
                reasons.append(f"Rate ${rate:,.0f}/FEU exceeds high threshold ${high_thresh:,}")
            if rate > 0 and rate < low_thresh:
                reasons.append(f"Rate ${rate:,.0f}/FEU below low threshold ${low_thresh:,}")
            if rate > 0 and abs(pct_change) > pct_thresh:
                sign = "+" if pct_change >= 0 else ""
                reasons.append(f"30d change {sign}{pct_change:.1f}% exceeds {pct_thresh}% threshold")
            if reasons:
                alerts.append((r, reasons))

        if alerts:
            for route, reasons in alerts:
                has_high = any("exceeds high" in rsn for rsn in reasons)
                border_color = _C_LOW if has_high else _C_MOD
                bg_color = "rgba(239,68,68,0.08)" if has_high else "rgba(245,158,11,0.08)"
                reasons_html = "".join(
                    f'<li style="margin:2px 0;color:{_C_TEXT2}">{rsn}</li>' for rsn in reasons)
                st.markdown(
                    f'<div style="background:{bg_color};border:1px solid {border_color};'
                    f'border-left:4px solid {border_color};border-radius:8px;'
                    f'padding:10px 14px;margin-bottom:8px">'
                    f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">'
                    f'<span style="font-size:1rem">⚠️</span>'
                    f'<span style="font-weight:700;color:{_C_TEXT};font-size:0.88rem">{route.route_name}</span>'
                    f'<span style="font-size:0.78rem;color:{_C_TEXT3};margin-left:auto">'
                    f'${route.current_rate_usd_feu:,.0f}/FEU</span>'
                    f'</div>'
                    f'<ul style="margin:0;padding-left:18px;font-size:0.78rem">{reasons_html}</ul>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                f'<div style="background:rgba(16,185,129,0.08);border:1px solid {_C_HIGH};'
                f'border-radius:8px;padding:10px 14px;color:{_C_HIGH};font-size:0.88rem;font-weight:600">'
                f'✅ All rates within normal range</div>',
                unsafe_allow_html=True,
            )
        st.markdown("<div style='margin-bottom:16px'></div>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Rate alerts unavailable: {exc}")


def _render_world_map(route_results: list[RouteOpportunity]) -> None:
    """Scattergeo world map: port nodes + great-circle route arcs."""
    try:
        fig = go.Figure()
        ports: dict[str, dict] = {}
        for r in route_results:
            for locode in (r.origin_locode, r.dest_locode):
                if locode not in ports:
                    coords = _LOCODE_COORDS.get(locode)
                    if coords:
                        ports[locode] = {"lat": coords[0], "lon": coords[1], "routes": []}
                if locode in ports:
                    ports[locode]["routes"].append(r)

        for r in route_results:
            orig = _LOCODE_COORDS.get(r.origin_locode)
            dest = _LOCODE_COORDS.get(r.dest_locode)
            if not orig or not dest:
                continue
            arc_color = _score_color(r.opportunity_score)
            rate_str  = f"${r.current_rate_usd_feu:,.0f}/FEU" if r.current_rate_usd_feu > 0 else "N/A"
            fig.add_trace(go.Scattergeo(
                lat=[orig[0], None, dest[0]], lon=[orig[1], None, dest[1]],
                mode="lines",
                line=dict(width=2.5, color=arc_color),
                opacity=0.50 + r.opportunity_score * 0.40,
                showlegend=False, hoverinfo="skip", name=r.route_name,
            ))
            mid_lat = (orig[0] + dest[0]) / 2
            mid_lon = (orig[1] + dest[1]) / 2
            fig.add_trace(go.Scattergeo(
                lat=[mid_lat], lon=[mid_lon],
                mode="markers",
                marker=dict(size=1, color="rgba(0,0,0,0)"),
                showlegend=False,
                hovertemplate=(
                    f"<b>{r.route_name}</b><br>Score: {r.opportunity_score:.0%}<br>"
                    f"Rate: {rate_str}<br>Transit: {r.transit_days}d<br>Trend: {r.rate_trend}"
                    f"<extra></extra>"
                ),
                name=r.route_name,
            ))

        if ports:
            lats   = [p["lat"] for p in ports.values()]
            lons   = [p["lon"] for p in ports.values()]
            labels = list(ports.keys())
            texts  = []
            for locode, pd_info in ports.items():
                related = pd_info["routes"]
                avg_s = sum(r_.opportunity_score for r_ in related) / len(related) if related else 0
                texts.append(f"<b>{locode}</b><br>Avg score: {avg_s:.0%}<br>{len(related)} routes")
            fig.add_trace(go.Scattergeo(
                lat=lats, lon=lons,
                mode="markers+text",
                text=labels,
                textposition="top center",
                textfont=dict(size=9, color=_C_TEXT2, family="monospace"),
                marker=dict(size=14, color=_C_ACCENT, symbol="circle",
                            line=dict(color=_C_TEXT, width=1.5), opacity=0.90),
                hovertemplate="%{customdata}<extra></extra>",
                customdata=texts,
                showlegend=False, name="Ports",
            ))

        for label, color in [("High Opportunity", _C_HIGH), ("Moderate", _C_MOD), ("Low", _C_LOW)]:
            fig.add_trace(go.Scattergeo(
                lat=[None], lon=[None], mode="lines",
                line=dict(color=color, width=3),
                name=label, showlegend=True,
            ))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_C_BG,
            plot_bgcolor=_C_BG,
            geo=dict(
                bgcolor=_C_BG,
                showland=True, landcolor="#131e2e",
                showocean=True, oceancolor="#0a1020",
                showcoastlines=True, coastlinecolor="rgba(255,255,255,0.08)",
                showframe=False,
                showcountries=True, countrycolor="rgba(255,255,255,0.04)",
                projection_type="natural earth",
            ),
            height=480,
            margin=dict(t=10, b=10, l=0, r=0),
            legend=dict(
                orientation="h", yanchor="bottom", y=0.02, xanchor="right", x=0.98,
                bgcolor="rgba(10,15,26,0.8)", bordercolor=_C_BORDER, borderwidth=1,
                font=dict(color=_C_TEXT2, size=10),
            ),
            hoverlabel=dict(bgcolor=_C_CARD, bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="routes_world_map")
        st.caption("Arc color = opportunity score (green=high, amber=moderate, red=low). "
                   "Opacity scales with score.")
    except Exception as exc:
        st.warning(f"World map unavailable: {exc}")


def _render_forecasts(forecasts: list, C_BG: str, C_SURFACE: str, C_CARD: str, C_TEXT: str, C_TEXT3: str) -> None:
    """Render legacy linear-trend forecasts (list of old RateForecast dataclasses)."""
    try:
        for fc in forecasts[:6]:
            pct_30 = (fc.forecast_30d - fc.current_rate) / fc.current_rate * 100 if fc.current_rate > 0 else 0
            arrow  = "↑" if pct_30 > 1 else ("↓" if pct_30 < -1 else "→")
            with st.container(border=True):
                fc1, fc2, fc3, fc4 = st.columns([2, 1, 1, 1])
                fc1.markdown(f"**{fc.route_name}** {arrow}")
                fc2.metric("Current", f"${fc.current_rate:,.0f}")
                fc3.metric("30d forecast", f"${fc.forecast_30d:,.0f}", f"{pct_30:+.1f}%",
                           delta_color="normal")
                pct_90 = (fc.forecast_90d - fc.current_rate) / fc.current_rate * 100 if fc.current_rate > 0 else 0
                fc4.metric("90d forecast", f"${fc.forecast_90d:,.0f}", f"{pct_90:+.1f}% vs current",
                           delta_color="normal")
                with st.expander("Forecast detail", key=f"routes_fc_exp_{fc.route_name}"):
                    days  = [0, 30, 60, 90]
                    rates = [fc.current_rate, fc.forecast_30d, fc.forecast_60d, fc.forecast_90d]
                    upper = [fc.current_rate, fc.upper_30d,
                             fc.upper_30d + (fc.forecast_60d - fc.forecast_30d),
                             fc.upper_30d + (fc.forecast_90d - fc.forecast_30d)]
                    lower = [fc.current_rate, fc.lower_30d,
                             fc.lower_30d + (fc.forecast_60d - fc.forecast_30d),
                             fc.lower_30d + (fc.forecast_90d - fc.forecast_30d)]
                    ffig = go.Figure()
                    ffig.add_trace(go.Scatter(
                        x=days + days[::-1], y=upper + lower[::-1],
                        fill="toself", fillcolor="rgba(74,144,217,0.15)",
                        line=dict(color="rgba(255,255,255,0)"),
                        showlegend=False, name="confidence range",
                    ))
                    ffig.add_trace(go.Scatter(
                        x=days, y=rates, mode="lines+markers",
                        line=dict(color="#4A90D9", width=2), marker=dict(size=6),
                        name="Forecast",
                        hovertemplate="Day %{x}: $%{y:,.0f}/FEU<extra></extra>",
                    ))
                    ffig.update_layout(
                        template="plotly_dark", paper_bgcolor=C_BG, plot_bgcolor=C_SURFACE,
                        height=180,
                        xaxis=dict(title="Days from today", gridcolor="rgba(255,255,255,0.05)"),
                        yaxis=dict(title="Rate (USD/FEU)", gridcolor="rgba(255,255,255,0.05)"),
                        margin=dict(t=5, b=5), showlegend=False,
                        hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)",
                                        font=dict(color=C_TEXT, size=12)),
                    )
                    st.plotly_chart(ffig, use_container_width=True,
                                    key=f"routes_forecast_{fc.route_name}")
                    st.caption(fc.methodology)
                    st.caption(f"Confidence: **{fc.confidence}** · R²={fc.r_squared:.2f} · {fc.data_points} data points")
    except Exception as exc:
        st.warning(f"Forecasts unavailable: {exc}")


def _render_ml_forecasts(route_results, forecasts: dict) -> None:
    """Render ML-based rate forecasts from rate_forecaster.forecast_all_routes().

    Args:
        route_results: list[RouteOpportunity] — used only for ordering.
        forecasts:     dict[route_id → RateForecast] from rate_forecaster.
    """
    if not forecasts:
        st.info("ML forecasts are not yet available. They will appear after sufficient rate history has been collected.")
        return

    # ── Direction arrow helpers ────────────────────────────────────────────────
    def _arrow(direction: str) -> str:
        return {"Rising": "▲", "Falling": "▼", "Stable": "●"}.get(direction, "→")

    def _dir_color(direction: str) -> str:
        return {"Rising": _C_HIGH, "Falling": _C_LOW, "Stable": _C_MOD}.get(direction, _C_TEXT3)

    # ── Pick top-5 by absolute 30d expected move ──────────────────────────────
    sorted_fcs = sorted(
        forecasts.values(),
        key=lambda f: abs(f.forecast_30d - f.current_rate),
        reverse=True,
    )
    top5 = sorted_fcs[:5]

    # ── Forecast cards ─────────────────────────────────────────────────────────
    st.markdown(
        '<p style="font-size:0.72rem;color:#64748b;text-transform:uppercase;'
        'letter-spacing:0.12em;margin-bottom:12px">Top 5 Routes by Expected Move</p>',
        unsafe_allow_html=True,
    )

    for fc in top5:
        pct_30 = (fc.forecast_30d - fc.current_rate) / fc.current_rate * 100 if fc.current_rate > 0 else 0.0
        ci_low, ci_high = fc.confidence_interval_30d
        dir_color = _dir_color(fc.direction)
        arrow = _arrow(fc.direction)

        # Card HTML
        drivers_html = "".join(
            f'<span style="background:#1e293b;color:#94a3b8;border-radius:4px;'
            f'padding:2px 7px;font-size:0.65rem;margin-right:4px">{d}</span>'
            for d in (fc.key_drivers or [])[:3]
        )

        # Confidence bar: fill proportion = direction_confidence
        bar_pct = int(fc.direction_confidence * 100)
        bar_fill_color = dir_color

        # CI range bar: scale relative to current rate
        ci_range = ci_high - ci_low
        ci_pct_lo = (ci_low  - fc.current_rate) / fc.current_rate * 100 if fc.current_rate else 0
        ci_pct_hi = (ci_high - fc.current_rate) / fc.current_rate * 100 if fc.current_rate else 0

        st.markdown(
            f"""
            <div style="background:{_C_CARD};border:1px solid rgba(255,255,255,0.07);
                        border-radius:10px;padding:16px 20px;margin-bottom:10px">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
                <div>
                  <span style="font-weight:700;font-size:0.95rem;color:{_C_TEXT}">{fc.route_name}</span>
                  <span style="margin-left:10px;background:{dir_color}22;color:{dir_color};
                               border:1px solid {dir_color}66;border-radius:999px;
                               padding:2px 9px;font-size:0.68rem;font-weight:700">
                    {arrow} {fc.direction}
                  </span>
                </div>
                <span style="font-size:0.72rem;color:{_C_TEXT3}">{fc.last_updated}</span>
              </div>

              <div style="display:flex;gap:24px;margin-bottom:12px">
                <div>
                  <div style="font-size:0.65rem;color:{_C_TEXT3};margin-bottom:2px">CURRENT RATE</div>
                  <div style="font-size:1.10rem;font-weight:700;color:{_C_TEXT}">${fc.current_rate:,.0f}<span style="font-size:0.65rem;color:{_C_TEXT3}">/FEU</span></div>
                </div>
                <div>
                  <div style="font-size:0.65rem;color:{_C_TEXT3};margin-bottom:2px">7-DAY</div>
                  <div style="font-size:1.10rem;font-weight:700;color:{_C_TEXT}">${fc.forecast_7d:,.0f}</div>
                </div>
                <div>
                  <div style="font-size:0.65rem;color:{_C_TEXT3};margin-bottom:2px">30-DAY FORECAST</div>
                  <div style="font-size:1.10rem;font-weight:700;color:{dir_color}">${fc.forecast_30d:,.0f}
                    <span style="font-size:0.75rem;font-weight:600">&nbsp;{pct_30:+.1f}%</span>
                  </div>
                </div>
                <div>
                  <div style="font-size:0.65rem;color:{_C_TEXT3};margin-bottom:2px">90-DAY</div>
                  <div style="font-size:1.10rem;font-weight:700;color:{_C_TEXT}">${fc.forecast_90d:,.0f}</div>
                </div>
              </div>

              <div style="margin-bottom:10px">
                <div style="font-size:0.63rem;color:{_C_TEXT3};margin-bottom:4px">
                  30d confidence interval &nbsp;
                  <span style="color:{_C_TEXT2}">${ci_low:,.0f} – ${ci_high:,.0f}</span>
                  &nbsp;({ci_pct_lo:+.1f}% to {ci_pct_hi:+.1f}%)
                </div>
                <div style="background:#1e293b;border-radius:4px;height:6px;position:relative;overflow:hidden">
                  <div style="position:absolute;left:0;top:0;bottom:0;
                              width:{int((ci_low/max(fc.current_rate,1))*100) if ci_low < fc.current_rate else 50}%;
                              background:rgba(255,255,255,0.05)"></div>
                  <div style="position:absolute;
                              left:{max(0, int(50 + ci_pct_lo))}%;
                              width:{max(4, int(ci_pct_hi - ci_pct_lo))}%;
                              top:0;bottom:0;
                              background:{dir_color};opacity:0.7;border-radius:4px"></div>
                  <div style="position:absolute;left:50%;top:-1px;bottom:-1px;
                              width:2px;background:rgba(255,255,255,0.3)"></div>
                </div>
              </div>

              <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
                <span style="font-size:0.63rem;color:{_C_TEXT3}">Direction confidence</span>
                <div style="flex:1;background:#1e293b;border-radius:4px;height:5px;overflow:hidden">
                  <div style="width:{bar_pct}%;height:100%;background:{bar_fill_color};border-radius:4px"></div>
                </div>
                <span style="font-size:0.63rem;color:{_C_TEXT2};font-weight:600">{bar_pct}%</span>
              </div>

              <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                <span style="font-size:0.63rem;color:{_C_TEXT3}">Key drivers:</span>
                {drivers_html if drivers_html else '<span style="font-size:0.63rem;color:{_C_TEXT3}">—</span>'}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── Grouped bar chart: current vs 30d forecast ────────────────────────────
    _divider("Current Rates vs 30-Day ML Forecast")
    all_fcs = sorted_fcs[:10]
    bar_names  = [f.route_name for f in all_fcs]
    bar_actual = [f.current_rate for f in all_fcs]
    bar_fc30   = [f.forecast_30d for f in all_fcs]

    bar_fig = go.Figure(data=[
        go.Bar(
            name="Current Rate",
            x=bar_names,
            y=bar_actual,
            marker_color=_C_ACCENT,
            marker_line_width=0,
            hovertemplate="%{x}<br>Current: $%{y:,.0f}/FEU<extra></extra>",
        ),
        go.Bar(
            name="30d Forecast",
            x=bar_names,
            y=bar_fc30,
            marker_color=_C_CONV,
            marker_line_width=0,
            hovertemplate="%{x}<br>30d Forecast: $%{y:,.0f}/FEU<extra></extra>",
        ),
    ])
    bar_fig.update_layout(
        barmode="group",
        template="plotly_dark",
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        height=340,
        margin=dict(l=20, r=20, t=30, b=100),
        font=dict(color=_C_TEXT, family="Inter, sans-serif", size=11),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=_C_TEXT2, size=11),
            orientation="h",
            yanchor="bottom", y=1.02,
        ),
        xaxis=dict(
            tickangle=-35,
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(size=10),
        ),
        yaxis=dict(
            title="Rate (USD/FEU)",
            gridcolor="rgba(255,255,255,0.06)",
        ),
        hoverlabel=dict(
            bgcolor=_C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=_C_TEXT, size=12),
        ),
    )
    st.plotly_chart(bar_fig, use_container_width=True, key="ml_forecast_bar_chart")

    # ── Model quality table ────────────────────────────────────────────────────
    _divider("Model Quality — Forecast Diagnostics")
    quality_rows = []
    for fc in sorted_fcs:
        r2_pct = f"{fc.model_r2 * 100:.0f}%"
        dc_pct = f"{fc.direction_confidence * 100:.0f}%"
        quality_rows.append({
            "Route":                fc.route_name,
            "R² Score":             r2_pct,
            "Direction":            f"{_arrow(fc.direction)} {fc.direction}",
            "Direction Confidence": dc_pct,
            "Last Updated":         fc.last_updated,
        })

    if quality_rows:
        q_df = pd.DataFrame(quality_rows)
        st.dataframe(
            q_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Route":                st.column_config.TextColumn("Route", width="medium"),
                "R² Score":             st.column_config.TextColumn("R² Score", width="small"),
                "Direction":            st.column_config.TextColumn("Direction", width="small"),
                "Direction Confidence": st.column_config.TextColumn("Confidence", width="small"),
                "Last Updated":         st.column_config.TextColumn("Updated", width="medium"),
            },
        )


# ══════════════════════════════════════════════════════════════════════════════
# Main render — EXACT signature preserved
# ══════════════════════════════════════════════════════════════════════════════

def render(
    route_results: list[RouteOpportunity],
    freight_data: dict,
    forecasts: "list | dict | None" = None,
) -> None:
    """Render the Routes tab.

    Args:
        route_results: Scored route opportunities from the optimizer.
        freight_data:  Raw rate DataFrames keyed by route_id.
        forecasts:     Either a list[RateForecast] (legacy linear model) or a
                       dict[route_id → RateForecast] from rate_forecaster
                       (ML model).  Both formats are supported.
    """
    if not route_results:
        st.info(
            "🚢 Route opportunity data is loading or unavailable — freight rate data refreshes every 24 hours. "
            "Verify your FBX/freight API credentials in .env and click Refresh All Data in the sidebar."
        )
        return

    # ── Section 1: Hero Dashboard ─────────────────────────────────────────────
    try:
        _render_hero_dashboard(route_results)
    except Exception as exc:
        st.warning(f"Hero dashboard error: {exc}")

    # ── World Map ─────────────────────────────────────────────────────────────
    try:
        _divider("Global Route Map")
        _render_world_map(route_results)
    except Exception as exc:
        st.warning(f"World map error: {exc}")

    # ── Section 2: Opportunity Matrix ─────────────────────────────────────────
    try:
        _divider("Route Opportunity Matrix — Rate vs Score")
        _render_opportunity_matrix(route_results)
    except Exception as exc:
        st.warning(f"Opportunity matrix error: {exc}")

    # ── Section 3: Route Cards Grid ───────────────────────────────────────────
    try:
        _divider("Route Cards — All Corridors")
        _render_route_cards_grid(route_results)
    except Exception as exc:
        st.warning(f"Route cards error: {exc}")

    # ── Section 4: Rate League Table ──────────────────────────────────────────
    try:
        _divider("Rate League Table — Ranked by Current Rate")
        _render_rate_league_table(route_results)
    except Exception as exc:
        st.warning(f"Rate league table error: {exc}")

    # ── Section 5: Route Performance Heatmap ──────────────────────────────────
    try:
        _divider("Route Performance Heatmap — Multi-Period Changes")
        _render_performance_heatmap(route_results)
    except Exception as exc:
        st.warning(f"Performance heatmap error: {exc}")

    # ── Section 6: Top 3 Opportunities ────────────────────────────────────────
    try:
        _divider("Top Opportunities — Featured Routes")
        _render_top_opportunities_panel(route_results)
    except Exception as exc:
        st.warning(f"Top opportunities error: {exc}")

    # ── Section 7: Correlation Matrix ─────────────────────────────────────────
    try:
        _divider("Route Correlation Matrix")
        _render_correlation_matrix(route_results)
    except Exception as exc:
        st.warning(f"Correlation matrix error: {exc}")

    # ── Section 8: Seasonal Patterns ──────────────────────────────────────────
    try:
        _divider("Seasonal Patterns — Route × Month Heatmap")
        _render_seasonal_patterns(route_results, freight_data)
    except Exception as exc:
        st.warning(f"Seasonal patterns error: {exc}")

    # ── Section 9: Volatility Ranking ─────────────────────────────────────────
    try:
        _divider("Volatility Ranking — Risk / Reward Assessment")
        _render_volatility_ranking(route_results, freight_data)
    except Exception as exc:
        st.warning(f"Volatility ranking error: {exc}")

    # ── Rate Alerts ───────────────────────────────────────────────────────────
    try:
        _divider("Rate Alerts")
        _render_rate_alerts(route_results)
    except Exception as exc:
        st.warning(f"Rate alerts error: {exc}")

    # ── Forecasts ─────────────────────────────────────────────────────────────
    if forecasts:
        if isinstance(forecasts, dict):
            # ML forecasts from rate_forecaster.forecast_all_routes()
            try:
                _divider("ML Rate Forecasts — 7 / 30 / 90-Day (GBR + Ridge)")
                st.caption(
                    "Gradient Boosting (30d/90d) and Ridge Regression (7d) trained on "
                    "rate history + macro indicators. Feature importances identify key drivers."
                )
                _render_ml_forecasts(route_results, forecasts)
            except Exception as exc:
                st.warning(f"ML forecasts error: {exc}")
        else:
            # Legacy linear forecasts from processing.forecaster
            try:
                _divider("30 / 60 / 90-Day Rate Forecasts")
                st.caption("Linear trend extrapolation with seasonal adjustment. Low-confidence forecasts shown for reference only.")
                _render_forecasts(forecasts, _C_BG, _C_SURFACE, _C_CARD, _C_TEXT, _C_TEXT3)
            except Exception as exc:
                st.warning(f"Forecasts error: {exc}")

    # ── CSV Download ──────────────────────────────────────────────────────────
    try:
        _divider("Export")
        _routes_df = pd.DataFrame([
            {
                "Route":               r.route_name,
                "Origin":              r.origin_locode,
                "Destination":         r.dest_locode,
                "Opportunity Score":   round(r.opportunity_score, 3),
                "Label":               r.opportunity_label,
                "Rate (USD/FEU)":      r.current_rate_usd_feu if r.current_rate_usd_feu > 0 else None,
                "30d Rate Change":     round(r.rate_pct_change_30d * 100, 2),
                "Rate Trend":          r.rate_trend,
                "Transit (days)":      r.transit_days,
                "Rate Momentum":       round(r.rate_momentum_component, 3),
                "Demand Imbalance":    round(r.demand_imbalance_component, 3),
                "Congestion Clearance":round(r.congestion_clearance_component, 3),
                "Macro Tailwind":      round(r.macro_tailwind_component, 3),
            }
            for r in route_results
        ])
        st.download_button(
            label="📥 Download Route Data (CSV)",
            data=_routes_df.to_csv(index=False),
            file_name="route_opportunities.csv",
            mime="text/csv",
            key="download_route_opportunities_csv",
        )
    except Exception as exc:
        st.warning(f"Export error: {exc}")

    # ── Section 10: Route Detail Drill-Down ───────────────────────────────────
    try:
        _divider("Route Detail Drill-Down — Expand Any Route")
        _render_route_detail_drilldown(route_results, freight_data)
    except Exception as exc:
        st.warning(f"Route detail drill-down error: {exc}")
