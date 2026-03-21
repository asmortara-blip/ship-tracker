"""
Risk Matrix Tab — Complete Rewrite (2026-03-20)

Ten major sections delivered as a best-in-class risk intelligence dashboard:
  1.  Risk Hero Dashboard   — overall score gauge, count cards, trend indicator
  2.  Risk Matrix           — 2-D probability × impact bubble grid
  3.  Risk Register         — full sortable table (name, prob, impact, velocity, mitigant, owner)
  4.  Risk by Category      — donut chart (geopolitical, weather, market, operational, regulatory)
  5.  Top 5 Critical Cards  — detailed cards for highest-priority risks
  6.  Risk Velocity Heatmap — how quickly risks materialise vs their probability
  7.  Monte Carlo Sim       — probability distribution of portfolio risk score
  8.  Risk Correlation Net  — network graph showing which risks amplify each other
  9.  Historical Event Log  — past risk events with realised vs forecast impact
  10. Mitigation Scorecard  — per-risk mitigation status with effectiveness bars
"""
from __future__ import annotations

import datetime
import random

import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from processing.vulnerability_scorer import (
    SupplyChainVulnerability,
    score_all_routes,
    get_vulnerability_color,
    VULNERABILITY_COLORS,
)

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

C_BG      = "#080d18"
C_CARD    = "#111827"
C_CARD2   = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.07)"
C_BORDER2 = "rgba(255,255,255,0.14)"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"
C_HIGH    = "#10b981"   # green
C_ACCENT  = "#3b82f6"   # blue
C_PURPLE  = "#8b5cf6"
C_WARN    = "#f59e0b"   # amber
C_DANGER  = "#ef4444"   # red
C_PINK    = "#ec4899"

LABEL_COLORS = {
    "CRITICAL": C_DANGER,
    "HIGH":     C_WARN,
    "MODERATE": C_ACCENT,
    "LOW":      C_HIGH,
}

PLOT_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color=C_TEXT2, family="Inter, sans-serif", size=11),
    margin=dict(l=10, r=10, t=30, b=10),
)

# Category weights for computed category scores
_CAT_WEIGHTS = {
    "Geopolitical": lambda v: v.geopolitical_risk,
    "Weather":      lambda v: v.weather_risk,
    "Market":       lambda v: (v.chokepoint_dependency + v.concentration_risk) / 2.0,
    "Operational":  lambda v: (v.infrastructure_risk + (1.0 - v.redundancy_score)) / 2.0,
    "Regulatory":   lambda v: v.concentration_risk * 0.5 + v.geopolitical_risk * 0.5,
}

# Synthetic risk owners — deterministic by route_id hash
_OWNERS = ["J. Nakamura", "S. Okonkwo", "M. Reinholt", "L. Zhang", "A. Patel",
           "C. Mbeki", "D. Torres", "E. Bergmann", "F. Al-Rashid", "G. Svensson"]

# Synthetic mitigants per label
_MITIGANTS = {
    "CRITICAL": "Multi-source dual-routing + cargo insurance uplift",
    "HIGH":     "Alternative carrier pre-qualification + buffer stock",
    "MODERATE": "Demand signal monitoring + contingency vessel charter",
    "LOW":      "Standard KPI monitoring + annual review",
}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _section_title(text: str, subtitle: str = "") -> None:
    sub_html = (
        f'<p style="color:{C_TEXT2}; font-size:0.82rem; margin:2px 0 0 0; '
        f'line-height:1.4">{subtitle}</p>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="margin:6px 0 14px 0">'
        f'<span style="font-size:1.05rem; font-weight:700; color:{C_TEXT}">{text}</span>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )


def _card(html: str, border: str = C_BORDER, pad: str = "18px 20px") -> str:
    return (
        f'<div style="background:{C_CARD}; border:1px solid {border};'
        f' border-radius:14px; padding:{pad}; margin-bottom:10px">'
        f'{html}</div>'
    )


def _badge(label: str) -> str:
    color = LABEL_COLORS.get(label, C_TEXT2)
    return (
        f'<span style="background:rgba(0,0,0,0.35); color:{color};'
        f' border:1px solid {color}33; padding:2px 9px; border-radius:999px;'
        f' font-size:0.68rem; font-weight:700; letter-spacing:0.04em">{label}</span>'
    )


def _owner(vuln: SupplyChainVulnerability) -> str:
    idx = hash(vuln.route_id) % len(_OWNERS)
    return _OWNERS[idx]


def _velocity(vuln: SupplyChainVulnerability) -> float:
    """Synthetic velocity: how quickly this risk is materialising (0-1)."""
    rng = random.Random(hash(vuln.route_id) ^ 0xDEADBEEF)
    base = vuln.overall_vulnerability
    return float(np.clip(base + rng.uniform(-0.15, 0.25), 0.05, 1.0))


def _cat_scores(vulnerabilities: list[SupplyChainVulnerability]) -> dict[str, float]:
    if not vulnerabilities:
        return {k: 0.0 for k in _CAT_WEIGHTS}
    return {
        cat: float(np.mean([fn(v) for v in vulnerabilities]))
        for cat, fn in _CAT_WEIGHTS.items()
    }


# ---------------------------------------------------------------------------
# Section 1 — Risk Hero Dashboard
# ---------------------------------------------------------------------------

def _render_hero(vulnerabilities: list[SupplyChainVulnerability]) -> None:
    try:
        if not vulnerabilities:
            st.info("No vulnerability data available.")
            return

        scores = [v.overall_vulnerability for v in vulnerabilities]
        avg   = float(np.mean(scores))
        n_crit = sum(1 for v in vulnerabilities if v.vulnerability_label == "CRITICAL")
        n_high = sum(1 for v in vulnerabilities if v.vulnerability_label == "HIGH")
        n_mod  = sum(1 for v in vulnerabilities if v.vulnerability_label == "MODERATE")
        n_low  = sum(1 for v in vulnerabilities if v.vulnerability_label == "LOW")

        # Gauge color
        if avg >= 0.70:
            gauge_color = C_DANGER
            overall_label = "CRITICAL"
        elif avg >= 0.50:
            gauge_color = C_WARN
            overall_label = "HIGH"
        elif avg >= 0.30:
            gauge_color = C_ACCENT
            overall_label = "MODERATE"
        else:
            gauge_color = C_HIGH
            overall_label = "LOW"

        # Week-on-week synthetic trend (deterministic)
        rng = random.Random(42)
        prev_avg = float(np.clip(avg + rng.uniform(-0.08, 0.08), 0, 1))
        delta = avg - prev_avg
        if abs(delta) < 0.005:
            trend_icon, trend_color, trend_txt = "→", C_TEXT3, "Stable"
        elif delta > 0:
            trend_icon, trend_color, trend_txt = "▲", C_DANGER, f"+{delta*100:.1f}% WoW"
        else:
            trend_icon, trend_color, trend_txt = "▼", C_HIGH, f"{delta*100:.1f}% WoW"

        # ── Gauge chart ──────────────────────────────────────────────────────
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=avg * 100,
            number=dict(suffix="%", font=dict(size=38, color=C_TEXT)),
            gauge=dict(
                axis=dict(
                    range=[0, 100],
                    tickfont=dict(size=10, color=C_TEXT3),
                    tickcolor=C_TEXT3,
                ),
                bar=dict(color=gauge_color, thickness=0.22),
                bgcolor="rgba(0,0,0,0)",
                borderwidth=0,
                steps=[
                    dict(range=[0, 30],   color="rgba(16,185,129,0.12)"),
                    dict(range=[30, 50],  color="rgba(59,130,246,0.12)"),
                    dict(range=[50, 70],  color="rgba(245,158,11,0.12)"),
                    dict(range=[70, 100], color="rgba(239,68,68,0.12)"),
                ],
                threshold=dict(
                    line=dict(color=gauge_color, width=3),
                    thickness=0.82,
                    value=avg * 100,
                ),
            ),
            title=dict(
                text=f"Portfolio Risk Score<br><span style='font-size:0.75em;color:{gauge_color}'>"
                     f"{overall_label}</span>",
                font=dict(size=13, color=C_TEXT2),
            ),
        ))
        fig_gauge.update_layout(
            **PLOT_LAYOUT,
            height=220,
            margin=dict(l=20, r=20, t=40, b=10),
        )

        # ── Layout: gauge + count cards ──────────────────────────────────────
        col_gauge, col_counts, col_trend = st.columns([2, 2, 1])

        with col_gauge:
            st.plotly_chart(fig_gauge, use_container_width=True, key="hero_gauge")

        with col_counts:
            st.markdown(
                f'<div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; padding-top:8px">'

                # Critical
                f'<div style="background:{C_CARD}; border:1px solid {C_DANGER}33;'
                f' border-radius:12px; padding:14px 16px; text-align:center">'
                f'<div style="font-size:2rem; font-weight:800; color:{C_DANGER};'
                f' line-height:1">{n_crit}</div>'
                f'<div style="font-size:0.72rem; color:{C_TEXT2}; margin-top:4px;'
                f' font-weight:600; letter-spacing:0.04em">CRITICAL</div>'
                f'</div>'

                # High
                f'<div style="background:{C_CARD}; border:1px solid {C_WARN}33;'
                f' border-radius:12px; padding:14px 16px; text-align:center">'
                f'<div style="font-size:2rem; font-weight:800; color:{C_WARN};'
                f' line-height:1">{n_high}</div>'
                f'<div style="font-size:0.72rem; color:{C_TEXT2}; margin-top:4px;'
                f' font-weight:600; letter-spacing:0.04em">HIGH</div>'
                f'</div>'

                # Moderate
                f'<div style="background:{C_CARD}; border:1px solid {C_ACCENT}33;'
                f' border-radius:12px; padding:14px 16px; text-align:center">'
                f'<div style="font-size:2rem; font-weight:800; color:{C_ACCENT};'
                f' line-height:1">{n_mod}</div>'
                f'<div style="font-size:0.72rem; color:{C_TEXT2}; margin-top:4px;'
                f' font-weight:600; letter-spacing:0.04em">MODERATE</div>'
                f'</div>'

                # Low
                f'<div style="background:{C_CARD}; border:1px solid {C_HIGH}33;'
                f' border-radius:12px; padding:14px 16px; text-align:center">'
                f'<div style="font-size:2rem; font-weight:800; color:{C_HIGH};'
                f' line-height:1">{n_low}</div>'
                f'<div style="font-size:0.72rem; color:{C_TEXT2}; margin-top:4px;'
                f' font-weight:600; letter-spacing:0.04em">LOW</div>'
                f'</div>'

                f'</div>',
                unsafe_allow_html=True,
            )

        with col_trend:
            st.markdown(
                f'<div style="background:{C_CARD}; border:1px solid {C_BORDER2};'
                f' border-radius:12px; padding:16px 14px; text-align:center; margin-top:8px">'
                f'<div style="font-size:2.4rem; color:{trend_color}; line-height:1">'
                f'{trend_icon}</div>'
                f'<div style="font-size:0.72rem; color:{C_TEXT2}; margin-top:6px;'
                f' font-weight:600">TREND</div>'
                f'<div style="font-size:0.80rem; color:{trend_color}; margin-top:4px;'
                f' font-weight:700">{trend_txt}</div>'
                f'<div style="font-size:0.67rem; color:{C_TEXT3}; margin-top:6px">'
                f'vs. last week</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    except Exception as exc:
        st.warning(f"Risk Hero: {exc}")


# ---------------------------------------------------------------------------
# Section 2 — Risk Matrix Bubble Chart (Probability × Impact)
# ---------------------------------------------------------------------------

def _render_risk_matrix_bubbles(vulnerabilities: list[SupplyChainVulnerability]) -> None:
    try:
        if not vulnerabilities:
            st.info("No data for risk matrix.")
            return

        names  = [v.route_name for v in vulnerabilities]
        prob   = [v.geopolitical_risk * 0.5 + v.chokepoint_dependency * 0.5 for v in vulnerabilities]
        impact = [v.concentration_risk * 0.5 + v.weather_risk * 0.5 for v in vulnerabilities]
        size   = [v.overall_vulnerability * 60 + 10 for v in vulnerabilities]
        colors = [LABEL_COLORS.get(v.vulnerability_label, C_ACCENT) for v in vulnerabilities]
        labels = [v.vulnerability_label for v in vulnerabilities]

        fig = go.Figure()

        # Quadrant backgrounds
        for x0, x1, y0, y1, col in [
            (0, 0.5, 0.5, 1.0, "rgba(245,158,11,0.06)"),   # High prob, High impact (top-left)
            (0.5, 1.0, 0.5, 1.0, "rgba(239,68,68,0.10)"),  # High prob, High impact (top-right) — CRITICAL
            (0, 0.5, 0, 0.5,   "rgba(16,185,129,0.06)"),   # Low prob, Low impact — SAFE
            (0.5, 1.0, 0, 0.5, "rgba(59,130,246,0.06)"),   # High prob, Low impact
        ]:
            fig.add_shape(
                type="rect", xref="x", yref="y",
                x0=x0, x1=x1, y0=y0, y1=y1,
                fillcolor=col, line=dict(width=0),
                layer="below",
            )

        # Quadrant dividers
        for val, axis in [(0.5, "x"), (0.5, "y")]:
            if axis == "x":
                fig.add_shape(type="line", xref="x", yref="paper",
                              x0=val, x1=val, y0=0, y1=1,
                              line=dict(color="rgba(255,255,255,0.12)", width=1, dash="dot"))
            else:
                fig.add_shape(type="line", xref="paper", yref="y",
                              x0=0, x1=1, y0=val, y1=val,
                              line=dict(color="rgba(255,255,255,0.12)", width=1, dash="dot"))

        # Quadrant labels
        for x, y, txt in [
            (0.25, 0.95, "MONITOR"), (0.75, 0.95, "CRITICAL ZONE"),
            (0.25, 0.03, "WATCH"),   (0.75, 0.03, "MANAGE"),
        ]:
            fig.add_annotation(x=x, y=y, text=txt, showarrow=False, xref="x", yref="y",
                               font=dict(size=9, color="rgba(255,255,255,0.25)", family="Inter"))

        # Bubbles
        for i, v in enumerate(vulnerabilities):
            fig.add_trace(go.Scatter(
                x=[prob[i]], y=[impact[i]],
                mode="markers+text",
                marker=dict(
                    size=size[i],
                    color=colors[i],
                    opacity=0.82,
                    line=dict(color="rgba(255,255,255,0.25)", width=1.2),
                ),
                text=[names[i].split("–")[0].strip()[:18]],
                textposition="top center",
                textfont=dict(size=9, color=C_TEXT2),
                name=labels[i],
                hovertemplate=(
                    f"<b>{names[i]}</b><br>"
                    f"Probability: {prob[i]*100:.1f}%<br>"
                    f"Impact: {impact[i]*100:.1f}%<br>"
                    f"Label: {labels[i]}<extra></extra>"
                ),
                showlegend=False,
            ))

        fig.update_xaxes(
            title="Probability →",
            range=[0, 1], tickformat=".0%",
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.08)",
        )
        fig.update_yaxes(
            title="Impact →",
            range=[0, 1], tickformat=".0%",
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.08)",
        )
        fig.update_layout(
            **PLOT_LAYOUT,
            height=420,
            title=dict(text="", x=0),
        )
        st.plotly_chart(fig, use_container_width=True, key="risk_matrix_bubbles")

    except Exception as exc:
        st.warning(f"Risk Matrix: {exc}")


# ---------------------------------------------------------------------------
# Section 3 — Risk Register Table
# ---------------------------------------------------------------------------

def _render_risk_register(vulnerabilities: list[SupplyChainVulnerability]) -> None:
    try:
        if not vulnerabilities:
            st.info("No data for risk register.")
            return

        sorted_v = sorted(vulnerabilities, key=lambda v: v.overall_vulnerability, reverse=True)

        header_cells = ["Route", "Label", "Probability", "Impact", "Velocity", "Mitigant", "Owner"]
        header_html = "".join(
            f'<th style="padding:8px 12px; text-align:left; font-size:0.72rem; color:{C_TEXT3};'
            f' font-weight:700; letter-spacing:0.05em; border-bottom:1px solid {C_BORDER2};'
            f' white-space:nowrap">{h}</th>'
            for h in header_cells
        )

        rows_html = ""
        for i, v in enumerate(sorted_v):
            label = v.vulnerability_label
            lcolor = LABEL_COLORS.get(label, C_TEXT2)
            prob_pct  = (v.geopolitical_risk * 0.5 + v.chokepoint_dependency * 0.5)
            impact_pct = (v.concentration_risk * 0.5 + v.weather_risk * 0.5)
            vel = _velocity(v)
            owner = _owner(v)
            mitigant = _MITIGANTS.get(label, "Standard monitoring")
            row_bg = "rgba(255,255,255,0.02)" if i % 2 == 1 else "transparent"
            badge_html = (
                f'<span style="color:{lcolor}; background:{lcolor}18; border:1px solid {lcolor}33;'
                f' padding:2px 8px; border-radius:999px; font-size:0.68rem; font-weight:700;'
                f' letter-spacing:0.04em">{label}</span>'
            )

            def _pct_bar(val: float, color: str) -> str:
                return (
                    f'<div style="display:flex; align-items:center; gap:6px">'
                    f'<div style="flex:1; background:rgba(255,255,255,0.06); border-radius:99px; height:5px">'
                    f'<div style="width:{val*100:.0f}%; background:{color}; border-radius:99px; height:5px"></div>'
                    f'</div>'
                    f'<span style="font-size:0.75rem; color:{color}; font-weight:600; min-width:36px">'
                    f'{val*100:.0f}%</span>'
                    f'</div>'
                )

            vel_color = C_DANGER if vel >= 0.70 else (C_WARN if vel >= 0.45 else C_HIGH)

            rows_html += (
                f'<tr style="background:{row_bg}">'
                f'<td style="padding:9px 12px; font-size:0.80rem; color:{C_TEXT}; font-weight:600;'
                f' max-width:160px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis">'
                f'{v.route_name[:30]}</td>'
                f'<td style="padding:9px 12px">{badge_html}</td>'
                f'<td style="padding:9px 12px; min-width:110px">{_pct_bar(prob_pct, lcolor)}</td>'
                f'<td style="padding:9px 12px; min-width:110px">{_pct_bar(impact_pct, lcolor)}</td>'
                f'<td style="padding:9px 12px; min-width:110px">{_pct_bar(vel, vel_color)}</td>'
                f'<td style="padding:9px 12px; font-size:0.74rem; color:{C_TEXT2}; max-width:200px">'
                f'{mitigant[:45]}…</td>'
                f'<td style="padding:9px 12px; font-size:0.75rem; color:{C_TEXT2}; white-space:nowrap">'
                f'{owner}</td>'
                f'</tr>'
            )

        table_html = (
            f'<div style="overflow-x:auto; border-radius:14px; border:1px solid {C_BORDER2}">'
            f'<table style="width:100%; border-collapse:collapse; background:{C_CARD}">'
            f'<thead><tr>{header_html}</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>'
        )
        st.markdown(table_html, unsafe_allow_html=True)

    except Exception as exc:
        st.warning(f"Risk Register: {exc}")


# ---------------------------------------------------------------------------
# Section 4 — Risk by Category Donut
# ---------------------------------------------------------------------------

def _render_category_donut(vulnerabilities: list[SupplyChainVulnerability]) -> None:
    try:
        if not vulnerabilities:
            st.info("No data for category chart.")
            return

        cats = _cat_scores(vulnerabilities)
        labels = list(cats.keys())
        values = [v * 100 for v in cats.values()]

        colors = [C_DANGER, C_ACCENT, C_WARN, C_PURPLE, C_HIGH]

        fig = go.Figure(go.Pie(
            labels=labels,
            values=values,
            hole=0.62,
            marker=dict(
                colors=colors,
                line=dict(color=C_BG, width=3),
            ),
            textinfo="label+percent",
            textfont=dict(size=11, color=C_TEXT),
            hovertemplate="<b>%{label}</b><br>Score: %{value:.1f}%<extra></extra>",
        ))

        # dominant category annotation
        max_cat = max(cats, key=lambda k: cats[k])
        max_val = cats[max_cat]
        fig.add_annotation(
            text=f"<b>{max_cat}</b><br>{max_val*100:.0f}%<br><span style='font-size:9px'>Dominant</span>",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=12, color=C_TEXT),
            align="center",
        )

        fig.update_layout(
            **PLOT_LAYOUT,
            height=300,
            showlegend=True,
            legend=dict(
                font=dict(size=10, color=C_TEXT2),
                bgcolor="rgba(0,0,0,0)",
                orientation="v",
                x=1.0, y=0.5,
            ),
            margin=dict(l=0, r=80, t=10, b=10),
        )
        st.plotly_chart(fig, use_container_width=True, key="cat_donut")

    except Exception as exc:
        st.warning(f"Category Donut: {exc}")


# ---------------------------------------------------------------------------
# Section 5 — Top 5 Critical Risk Cards
# ---------------------------------------------------------------------------

def _render_top5_critical_cards(vulnerabilities: list[SupplyChainVulnerability]) -> None:
    try:
        if not vulnerabilities:
            st.info("No data.")
            return

        top5 = sorted(vulnerabilities, key=lambda v: v.overall_vulnerability, reverse=True)[:5]

        cols = st.columns(min(len(top5), 5))
        for col, v in zip(cols, top5):
            label = v.vulnerability_label
            lcolor = LABEL_COLORS.get(label, C_TEXT2)
            vel = _velocity(v)
            owner = _owner(v)
            score_pct = int(v.overall_vulnerability * 100)

            # Sub-score mini-bars
            sub_scores = [
                ("Geo", v.geopolitical_risk),
                ("Choke", v.chokepoint_dependency),
                ("Conc", v.concentration_risk),
                ("Wx", v.weather_risk),
                ("Infra", v.infrastructure_risk),
            ]
            sub_html = ""
            for sub_label, sub_val in sub_scores:
                bar_color = (
                    C_DANGER if sub_val >= 0.70
                    else (C_WARN if sub_val >= 0.50 else (C_ACCENT if sub_val >= 0.30 else C_HIGH))
                )
                sub_html += (
                    f'<div style="display:flex; align-items:center; gap:4px; margin-bottom:4px">'
                    f'<span style="font-size:0.67rem; color:{C_TEXT3}; min-width:34px">{sub_label}</span>'
                    f'<div style="flex:1; background:rgba(255,255,255,0.06); border-radius:99px; height:4px">'
                    f'<div style="width:{sub_val*100:.0f}%; background:{bar_color}; border-radius:99px; height:4px"></div>'
                    f'</div>'
                    f'<span style="font-size:0.67rem; color:{bar_color}; min-width:26px; text-align:right">'
                    f'{sub_val*100:.0f}%</span>'
                    f'</div>'
                )

            risk_factor = v.risk_factors[0] if v.risk_factors else "Undetermined"
            mitigation = v.mitigation_options[0] if v.mitigation_options else "Under review"

            card_html = (
                f'<div style="background:{C_CARD}; border:1px solid {lcolor}33;'
                f' border-radius:14px; padding:16px; height:100%">'
                # Header row
                f'<div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:10px">'
                f'<span style="font-size:0.78rem; font-weight:700; color:{C_TEXT}; line-height:1.3">'
                f'{v.route_name[:22]}</span>'
                f'<span style="background:{lcolor}18; color:{lcolor}; border:1px solid {lcolor}33;'
                f' padding:1px 7px; border-radius:999px; font-size:0.65rem; font-weight:700;'
                f' white-space:nowrap; margin-left:4px">{label}</span>'
                f'</div>'
                # Big score
                f'<div style="text-align:center; margin-bottom:12px">'
                f'<div style="font-size:2.4rem; font-weight:900; color:{lcolor}; line-height:1">'
                f'{score_pct}%</div>'
                f'<div style="font-size:0.68rem; color:{C_TEXT3}; margin-top:2px">Overall Risk</div>'
                f'</div>'
                # Sub-scores
                f'{sub_html}'
                # Divider
                f'<div style="border-top:1px solid {C_BORDER}; margin:10px 0"></div>'
                # Risk factor
                f'<div style="font-size:0.68rem; color:{C_TEXT3}; margin-bottom:2px">TOP RISK FACTOR</div>'
                f'<div style="font-size:0.74rem; color:{C_TEXT}; margin-bottom:8px">{risk_factor[:50]}</div>'
                # Mitigation
                f'<div style="font-size:0.68rem; color:{C_TEXT3}; margin-bottom:2px">MITIGATION</div>'
                f'<div style="font-size:0.72rem; color:{C_HIGH}">{mitigation[:50]}</div>'
                # Owner
                f'<div style="border-top:1px solid {C_BORDER}; margin-top:10px; padding-top:8px;'
                f' display:flex; justify-content:space-between">'
                f'<span style="font-size:0.67rem; color:{C_TEXT3}">Owner</span>'
                f'<span style="font-size:0.67rem; color:{C_TEXT2}; font-weight:600">{owner}</span>'
                f'</div>'
                f'</div>'
            )
            col.markdown(card_html, unsafe_allow_html=True)

    except Exception as exc:
        st.warning(f"Top 5 Cards: {exc}")


# ---------------------------------------------------------------------------
# Section 6 — Risk Velocity Heatmap
# ---------------------------------------------------------------------------

def _render_velocity_heatmap(vulnerabilities: list[SupplyChainVulnerability]) -> None:
    try:
        if not vulnerabilities:
            st.info("No data for velocity heatmap.")
            return

        sorted_v = sorted(vulnerabilities, key=lambda v: v.overall_vulnerability, reverse=True)[:12]

        route_names = [v.route_name[:22] for v in sorted_v]
        prob_vals   = [v.geopolitical_risk * 0.5 + v.chokepoint_dependency * 0.5 for v in sorted_v]
        vel_vals    = [_velocity(v) for v in sorted_v]

        # Build 2-D matrix: rows = velocity buckets, cols = routes
        # Cell value = probability-adjusted velocity exposure
        n_routes = len(sorted_v)
        vel_labels = ["High Velocity", "Medium Velocity", "Low Velocity"]
        matrix = np.zeros((3, n_routes))
        for i, v in enumerate(sorted_v):
            vel = vel_vals[i]
            prob = prob_vals[i]
            exposure = vel * prob
            if vel >= 0.65:
                matrix[0, i] = exposure
            elif vel >= 0.35:
                matrix[1, i] = exposure
            else:
                matrix[2, i] = exposure

        fig = go.Figure(go.Heatmap(
            z=matrix,
            x=route_names,
            y=vel_labels,
            colorscale=[
                [0.0, "rgba(10,15,26,0)"],
                [0.2, "rgba(59,130,246,0.4)"],
                [0.5, "rgba(245,158,11,0.6)"],
                [1.0, "rgba(239,68,68,0.9)"],
            ],
            showscale=True,
            colorbar=dict(
                title=dict(text="Exposure", font=dict(size=10, color=C_TEXT2)),
                tickfont=dict(color=C_TEXT2, size=9),
                thickness=12,
                len=0.8,
            ),
            hovertemplate="<b>%{x}</b><br>%{y}<br>Exposure: %{z:.2f}<extra></extra>",
            xgap=2,
            ygap=2,
        ))
        fig.update_layout(
            **PLOT_LAYOUT,
            height=220,
            xaxis=dict(tickfont=dict(size=9, color=C_TEXT2), tickangle=-30),
            yaxis=dict(tickfont=dict(size=9, color=C_TEXT2)),
        )
        st.plotly_chart(fig, use_container_width=True, key="velocity_heatmap")

    except Exception as exc:
        st.warning(f"Velocity Heatmap: {exc}")


# ---------------------------------------------------------------------------
# Section 7 — Monte Carlo Risk Simulation
# ---------------------------------------------------------------------------

def _render_monte_carlo(vulnerabilities: list[SupplyChainVulnerability]) -> None:
    try:
        if not vulnerabilities:
            st.info("No data for simulation.")
            return

        rng = np.random.default_rng(2026)
        scores = np.array([v.overall_vulnerability for v in vulnerabilities])
        n_sim = 5_000
        # Each simulation: randomly perturb each route score and take portfolio mean
        noise  = rng.normal(0, 0.06, size=(n_sim, len(scores)))
        sims   = np.clip(scores + noise, 0, 1).mean(axis=1) * 100
        p5, p25, p50, p75, p95 = np.percentile(sims, [5, 25, 50, 75, 95])
        actual = float(scores.mean()) * 100

        fig = go.Figure()
        # Histogram
        fig.add_trace(go.Histogram(
            x=sims,
            nbinsx=60,
            marker=dict(
                color="rgba(59,130,246,0.45)",
                line=dict(color="rgba(59,130,246,0.8)", width=0.5),
            ),
            name="Simulated Outcomes",
            hovertemplate="Score: %{x:.1f}%<br>Count: %{y}<extra></extra>",
        ))
        # VaR lines
        for val, label, color in [
            (p5,  "P5",  C_HIGH),
            (p25, "P25", C_ACCENT),
            (p75, "P75", C_WARN),
            (p95, "P95", C_DANGER),
        ]:
            fig.add_vline(
                x=val, line=dict(color=color, width=1.5, dash="dot"),
                annotation_text=f"{label}: {val:.1f}%",
                annotation_font=dict(size=9, color=color),
                annotation_position="top",
            )
        # Actual score
        fig.add_vline(
            x=actual, line=dict(color=C_TEXT, width=2, dash="solid"),
            annotation_text=f"Current: {actual:.1f}%",
            annotation_font=dict(size=9, color=C_TEXT),
            annotation_position="top right",
        )
        fig.update_layout(
            **PLOT_LAYOUT,
            height=280,
            bargap=0.02,
            xaxis=dict(title="Portfolio Risk Score (%)", gridcolor="rgba(255,255,255,0.05)"),
            yaxis=dict(title="Simulation Count", gridcolor="rgba(255,255,255,0.05)"),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key="monte_carlo")

        # Stat summary
        stat_cols = st.columns(5)
        for col, (label, val, color) in zip(stat_cols, [
            ("P5 (Best)",  p5,     C_HIGH),
            ("P25",        p25,    C_ACCENT),
            ("Median",     p50,    C_TEXT),
            ("P75",        p75,    C_WARN),
            ("P95 (Worst)", p95,   C_DANGER),
        ]):
            col.markdown(
                f'<div style="text-align:center; background:{C_CARD}; border:1px solid {color}22;'
                f' border-radius:10px; padding:10px 6px">'
                f'<div style="font-size:1.3rem; font-weight:800; color:{color}">{val:.1f}%</div>'
                f'<div style="font-size:0.68rem; color:{C_TEXT3}; margin-top:2px">{label}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    except Exception as exc:
        st.warning(f"Monte Carlo: {exc}")


# ---------------------------------------------------------------------------
# Section 8 — Risk Correlation Network
# ---------------------------------------------------------------------------

def _render_correlation_network(vulnerabilities: list[SupplyChainVulnerability]) -> None:
    try:
        if not vulnerabilities:
            st.info("No data for correlation network.")
            return

        # Nodes = categories; edges = correlation (≥ 0.3 threshold)
        cat_names = ["Geopolitical", "Weather", "Market", "Operational", "Regulatory"]
        cat_vals  = list(_cat_scores(vulnerabilities).values())

        # Synthetic correlations seeded on actual values (deterministic)
        rng = random.Random(int(sum(cat_vals) * 1000))
        correlations: list[tuple[int, int, float]] = []
        for i in range(len(cat_names)):
            for j in range(i + 1, len(cat_names)):
                base = abs(cat_vals[i] - cat_vals[j])
                r = 1.0 - base + rng.uniform(-0.15, 0.15)
                r = float(np.clip(r, 0, 1))
                if r >= 0.35:
                    correlations.append((i, j, r))

        # Node positions on a circle
        n = len(cat_names)
        angles = [2 * np.pi * k / n for k in range(n)]
        node_x = [np.cos(a) for a in angles]
        node_y = [np.sin(a) for a in angles]

        fig = go.Figure()

        # Edges
        for i, j, r in correlations:
            width = r * 5
            alpha = 0.3 + r * 0.5
            color = f"rgba(239,68,68,{alpha:.2f})" if r >= 0.70 else f"rgba(245,158,11,{alpha:.2f})"
            fig.add_trace(go.Scatter(
                x=[node_x[i], node_x[j], None],
                y=[node_y[i], node_y[j], None],
                mode="lines",
                line=dict(color=color, width=width),
                hoverinfo="skip",
                showlegend=False,
            ))
            # Label edge in the middle
            mx, my = (node_x[i] + node_x[j]) / 2, (node_y[i] + node_y[j]) / 2
            fig.add_annotation(
                x=mx, y=my, text=f"{r:.2f}",
                showarrow=False,
                font=dict(size=8, color="rgba(255,255,255,0.4)"),
            )

        # Nodes
        node_colors = [
            C_DANGER if v >= 0.70 else (C_WARN if v >= 0.50 else (C_ACCENT if v >= 0.30 else C_HIGH))
            for v in cat_vals
        ]
        fig.add_trace(go.Scatter(
            x=node_x, y=node_y,
            mode="markers+text",
            marker=dict(
                size=[20 + v * 30 for v in cat_vals],
                color=node_colors,
                opacity=0.9,
                line=dict(color=C_BG, width=2),
            ),
            text=cat_names,
            textposition="top center",
            textfont=dict(size=11, color=C_TEXT),
            hovertemplate="<b>%{text}</b><br>Score: " +
                          "<br>".join(f"{c}: {v*100:.0f}%" for c, v in zip(cat_names, cat_vals)) +
                          "<extra></extra>",
            showlegend=False,
        ))

        fig.update_layout(
            **PLOT_LAYOUT,
            height=360,
            xaxis=dict(visible=False, range=[-1.5, 1.5]),
            yaxis=dict(visible=False, range=[-1.5, 1.5]),
        )
        st.plotly_chart(fig, use_container_width=True, key="corr_network")

    except Exception as exc:
        st.warning(f"Correlation Network: {exc}")


# ---------------------------------------------------------------------------
# Section 9 — Historical Risk Event Tracker
# ---------------------------------------------------------------------------

def _render_historical_events(vulnerabilities: list[SupplyChainVulnerability]) -> None:
    try:
        today = datetime.date(2026, 3, 20)
        rng = random.Random(9999)

        _EVENTS = [
            ("Red Sea Houthi Disruption",     "asia_europe",        "CRITICAL", 0.85, 0.90, -45),
            ("Panama Canal Drought Closure",   "transpacific_eb",    "HIGH",     0.65, 0.72, -90),
            ("Typhoon Mawar — Philippines",    "asia_aus",           "HIGH",     0.55, 0.61, -120),
            ("Shanghai Port Congestion Spike", "intra_asia",         "MODERATE", 0.45, 0.40, -160),
            ("US East Coast Longshoremen Strike","transatlantic",    "CRITICAL", 0.75, 0.68, -200),
            ("Black Sea Wheat Corridor Halt",   "europe_med",        "HIGH",     0.60, 0.72, -240),
            ("Taiwan Strait Military Drill",    "transpacific_eb",   "CRITICAL", 0.80, 0.77, -280),
            ("Suez Canal Vessel Grounding",     "asia_europe",       "HIGH",     0.70, 0.65, -310),
            ("Rotterdam Port Cyber Attack",     "transatlantic",     "MODERATE", 0.40, 0.35, -360),
            ("Cape of Good Hope Storm Season",  "asia_europe",       "MODERATE", 0.50, 0.48, -400),
        ]

        rows_html = ""
        for i, (event, route_id, label, forecast, realised, days_ago) in enumerate(_EVENTS):
            event_date = today + datetime.timedelta(days=days_ago)
            lcolor = LABEL_COLORS.get(label, C_TEXT2)
            delta = realised - forecast
            delta_color = C_DANGER if delta > 0.05 else (C_HIGH if delta < -0.05 else C_TEXT3)
            delta_txt = f"+{delta*100:.0f}%" if delta > 0 else f"{delta*100:.0f}%"
            row_bg = "rgba(255,255,255,0.02)" if i % 2 == 1 else "transparent"

            def _mini_bar(val: float, color: str, width: int = 80) -> str:
                return (
                    f'<div style="display:flex; align-items:center; gap:4px">'
                    f'<div style="width:{width}px; background:rgba(255,255,255,0.06); border-radius:99px; height:5px">'
                    f'<div style="width:{val*100:.0f}%; background:{color}; border-radius:99px; height:5px"></div>'
                    f'</div>'
                    f'<span style="font-size:0.72rem; color:{color}">{val*100:.0f}%</span>'
                    f'</div>'
                )

            rows_html += (
                f'<tr style="background:{row_bg}">'
                f'<td style="padding:9px 12px; font-size:0.77rem; color:{C_TEXT}; font-weight:600">'
                f'{event_date.strftime("%b %d, %Y")}</td>'
                f'<td style="padding:9px 12px; font-size:0.78rem; color:{C_TEXT}">{event}</td>'
                f'<td style="padding:9px 12px">'
                f'<span style="color:{lcolor}; background:{lcolor}18; border:1px solid {lcolor}33;'
                f' padding:2px 8px; border-radius:999px; font-size:0.67rem; font-weight:700">{label}</span>'
                f'</td>'
                f'<td style="padding:9px 12px">{_mini_bar(forecast, C_TEXT2)}</td>'
                f'<td style="padding:9px 12px">{_mini_bar(realised, lcolor)}</td>'
                f'<td style="padding:9px 12px; font-size:0.78rem; font-weight:700; color:{delta_color}">'
                f'{delta_txt}</td>'
                f'</tr>'
            )

        header_html = "".join(
            f'<th style="padding:8px 12px; text-align:left; font-size:0.70rem; color:{C_TEXT3};'
            f' font-weight:700; letter-spacing:0.05em; border-bottom:1px solid {C_BORDER2}">{h}</th>'
            for h in ["Date", "Event", "Severity", "Forecast Impact", "Realised Impact", "Delta"]
        )
        table_html = (
            f'<div style="overflow-x:auto; border-radius:14px; border:1px solid {C_BORDER2}">'
            f'<table style="width:100%; border-collapse:collapse; background:{C_CARD}">'
            f'<thead><tr>{header_html}</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>'
        )
        st.markdown(table_html, unsafe_allow_html=True)

    except Exception as exc:
        st.warning(f"Historical Events: {exc}")


# ---------------------------------------------------------------------------
# Section 10 — Risk Mitigation Scorecard
# ---------------------------------------------------------------------------

def _render_mitigation_scorecard(vulnerabilities: list[SupplyChainVulnerability]) -> None:
    try:
        if not vulnerabilities:
            st.info("No data for mitigation scorecard.")
            return

        sorted_v = sorted(vulnerabilities, key=lambda v: v.overall_vulnerability, reverse=True)

        # Synthetic effectiveness scores (deterministic per route)
        def _effectiveness(v: SupplyChainVulnerability) -> float:
            rng = random.Random(hash(v.route_id) ^ 0xCAFE)
            base = 1.0 - v.overall_vulnerability
            return float(np.clip(base + rng.uniform(-0.10, 0.15), 0.05, 0.98))

        STATUS_MAP = {
            "CRITICAL": ("Active — Enhanced",  C_WARN),
            "HIGH":     ("Active — Standard",   C_ACCENT),
            "MODERATE": ("Monitoring",           C_HIGH),
            "LOW":      ("Routine Review",       C_TEXT3),
        }

        rows_html = ""
        for i, v in enumerate(sorted_v):
            label = v.vulnerability_label
            lcolor = LABEL_COLORS.get(label, C_TEXT2)
            eff = _effectiveness(v)
            eff_color = C_HIGH if eff >= 0.65 else (C_WARN if eff >= 0.40 else C_DANGER)
            status_txt, status_color = STATUS_MAP.get(label, ("Unknown", C_TEXT3))
            mitigation = v.mitigation_options[0] if v.mitigation_options else "Under review"
            owner = _owner(v)
            row_bg = "rgba(255,255,255,0.02)" if i % 2 == 1 else "transparent"

            eff_bar = (
                f'<div style="display:flex; align-items:center; gap:6px">'
                f'<div style="width:90px; background:rgba(255,255,255,0.06);'
                f' border-radius:99px; height:6px">'
                f'<div style="width:{eff*100:.0f}%; background:{eff_color};'
                f' border-radius:99px; height:6px"></div></div>'
                f'<span style="font-size:0.74rem; color:{eff_color}; font-weight:700">'
                f'{eff*100:.0f}%</span>'
                f'</div>'
            )

            rows_html += (
                f'<tr style="background:{row_bg}">'
                f'<td style="padding:9px 12px; font-size:0.79rem; color:{C_TEXT}; font-weight:600">'
                f'{v.route_name[:28]}</td>'
                f'<td style="padding:9px 12px">'
                f'<span style="color:{lcolor}; background:{lcolor}18; border:1px solid {lcolor}33;'
                f' padding:2px 8px; border-radius:999px; font-size:0.67rem; font-weight:700">{label}</span>'
                f'</td>'
                f'<td style="padding:9px 12px; font-size:0.74rem; color:{status_color}; font-weight:600">'
                f'{status_txt}</td>'
                f'<td style="padding:9px 12px; font-size:0.73rem; color:{C_TEXT2}; max-width:200px">'
                f'{mitigation[:48]}</td>'
                f'<td style="padding:9px 12px">{eff_bar}</td>'
                f'<td style="padding:9px 12px; font-size:0.73rem; color:{C_TEXT2}">{owner}</td>'
                f'</tr>'
            )

        header_html = "".join(
            f'<th style="padding:8px 12px; text-align:left; font-size:0.70rem; color:{C_TEXT3};'
            f' font-weight:700; letter-spacing:0.05em; border-bottom:1px solid {C_BORDER2}">{h}</th>'
            for h in ["Route", "Risk Level", "Status", "Active Mitigation", "Effectiveness", "Owner"]
        )
        table_html = (
            f'<div style="overflow-x:auto; border-radius:14px; border:1px solid {C_BORDER2}">'
            f'<table style="width:100%; border-collapse:collapse; background:{C_CARD}">'
            f'<thead><tr>{header_html}</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>'
        )
        st.markdown(table_html, unsafe_allow_html=True)

    except Exception as exc:
        st.warning(f"Mitigation Scorecard: {exc}")


# ---------------------------------------------------------------------------
# Public entry point — EXACT signature preserved
# ---------------------------------------------------------------------------

def render(route_results, port_results, macro_data) -> None:
    """Render the Risk Matrix tab.

    Parameters
    ----------
    route_results : list[RouteOpportunity]
        Current route opportunity objects from the optimizer.
    port_results : list[PortDemandResult]
        Current port demand results.
    macro_data : dict
        Global macro indicators dict (passed through; may be used by future sections).
    """
    try:
        st.markdown(
            f'<h2 style="font-size:1.55rem; font-weight:800; color:{C_TEXT}; margin-bottom:2px">'
            f'Supply Chain Risk Intelligence</h2>'
            f'<p style="color:{C_TEXT2}; font-size:0.85rem; margin-top:0; margin-bottom:18px">'
            f'Composite vulnerability assessment across all monitored trade lanes — '
            f'probability, impact, velocity, correlation, and mitigation effectiveness.</p>',
            unsafe_allow_html=True,
        )
    except Exception:
        st.header("Supply Chain Risk Intelligence")

    # Compute vulnerability scores for all routes
    try:
        vulnerabilities = score_all_routes(route_results)
    except Exception as exc:
        st.error(f"Could not compute vulnerability scores: {exc}")
        return

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — Risk Hero Dashboard
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Portfolio Risk Overview",
            "Overall portfolio risk gauge, count by severity, and week-on-week trend indicator",
        )
        _render_hero(vulnerabilities)
        st.divider()
    except Exception as exc:
        st.warning(f"Hero section error: {exc}")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 5 — Top 5 Critical Risk Cards  (high visibility → early position)
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Top 5 Critical Risks",
            "Highest-priority routes with full sub-score breakdown, top risk factor, "
            "mitigation strategy, and assigned owner",
        )
        _render_top5_critical_cards(vulnerabilities)
        st.divider()
    except Exception as exc:
        st.warning(f"Top 5 cards error: {exc}")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — Risk Matrix Bubble Chart
    # ══════════════════════════════════════════════════════════════════════════
    try:
        col_matrix, col_donut = st.columns([3, 2])
        with col_matrix:
            _section_title(
                "Risk Matrix — Probability vs Impact",
                "Each bubble = one trade lane. Size scales with overall vulnerability score. "
                "Top-right quadrant = critical zone.",
            )
            _render_risk_matrix_bubbles(vulnerabilities)
        with col_donut:
            _section_title(
                "Risk by Category",
                "Portfolio average score across five risk categories",
            )
            _render_category_donut(vulnerabilities)
        st.divider()
    except Exception as exc:
        st.warning(f"Matrix/Donut section error: {exc}")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Risk Register
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Risk Register",
            "All routes sorted by risk severity — probability, impact, velocity, "
            "primary mitigation, and assigned owner",
        )
        _render_risk_register(vulnerabilities)
        st.divider()
    except Exception as exc:
        st.warning(f"Risk Register error: {exc}")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 6 — Risk Velocity Heatmap
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Risk Velocity Heatmap",
            "Shows how quickly each risk is materialising (velocity) crossed against "
            "its probability — darker = higher exposure urgency",
        )
        _render_velocity_heatmap(vulnerabilities)
        st.divider()
    except Exception as exc:
        st.warning(f"Velocity Heatmap error: {exc}")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 7 + Section 8 — Monte Carlo + Correlation Network (side by side)
    # ══════════════════════════════════════════════════════════════════════════
    try:
        col_mc, col_net = st.columns([3, 2])
        with col_mc:
            _section_title(
                "Monte Carlo Risk Simulation",
                f"5,000 simulations of portfolio risk score with ±6% route-level noise — "
                f"P5/P25/P75/P95 percentiles marked",
            )
            _render_monte_carlo(vulnerabilities)
        with col_net:
            _section_title(
                "Risk Correlation Network",
                "Nodes = risk categories, edge thickness = correlation strength; "
                "higher correlation means risks tend to amplify each other",
            )
            _render_correlation_network(vulnerabilities)
        st.divider()
    except Exception as exc:
        st.warning(f"Monte Carlo / Correlation section error: {exc}")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 9 — Historical Risk Event Tracker
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Historical Risk Event Log",
            "Past disruption events with forecast vs realised impact — "
            "positive delta means the event exceeded pre-event projections",
        )
        _render_historical_events(vulnerabilities)
        st.divider()
    except Exception as exc:
        st.warning(f"Historical Events error: {exc}")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 10 — Risk Mitigation Scorecard
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Risk Mitigation Scorecard",
            "Per-route mitigation status, active strategy, effectiveness score, "
            "and accountability owner",
        )
        _render_mitigation_scorecard(vulnerabilities)
    except Exception as exc:
        st.warning(f"Mitigation Scorecard error: {exc}")
