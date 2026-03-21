from __future__ import annotations

import io
import json
import os
import random
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from engine.insight import Insight
from engine.signals import SignalComponent


# ── Shared palette ─────────────────────────────────────────────────────────────
C_CARD   = "#1a2235"
C_BORDER = "rgba(255,255,255,0.08)"
C_HIGH   = "#10b981"
C_MOD    = "#f59e0b"
C_LOW    = "#ef4444"
C_ACCENT = "#3b82f6"
C_CONV   = "#8b5cf6"
C_MACRO  = "#06b6d4"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_TEXT3  = "#64748b"

CATEGORY_COLORS = {"CONVERGENCE": C_CONV,   "ROUTE": C_ACCENT, "PORT_DEMAND": C_HIGH, "MACRO": C_MACRO}
CATEGORY_ICONS  = {"CONVERGENCE": "🔮",     "ROUTE": "🚢",     "PORT_DEMAND": "🏗️",  "MACRO": "📊"}
ACTION_COLORS   = {"Prioritize": C_HIGH, "Monitor": C_ACCENT, "Watch": C_TEXT2, "Caution": C_MOD, "Avoid": C_LOW}

# Shipping carrier universe
CARRIERS = ["MAERSK", "MSC", "COSCO", "CMA-CGM", "HAPAG", "ONE", "EVERGREEN", "YANG MING"]


def _hex_rgba(h: str, a: float) -> str:
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{a})"


def _score_color(score: float) -> str:
    if score >= 0.70:
        return C_HIGH
    if score >= 0.50:
        return C_MOD
    return C_LOW


def _section_header(title: str, subtitle: str = "") -> None:
    sub_html = f'<div style="font-size:0.78rem; color:{C_TEXT2}; margin-top:3px">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f"""<div style="margin:28px 0 14px 0">
            <div style="font-size:0.68rem; font-weight:800; color:{C_ACCENT}; text-transform:uppercase;
                        letter-spacing:0.12em; margin-bottom:4px">EARNINGS INTELLIGENCE</div>
            <div style="font-size:1.05rem; font-weight:800; color:{C_TEXT}; letter-spacing:-0.01em">{title}</div>
            {sub_html}
        </div>""",
        unsafe_allow_html=True,
    )


def _card(content_html: str, accent: str = C_ACCENT, pad: str = "20px 24px") -> str:
    return f"""
    <div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-top:2px solid {accent};
                border-radius:14px; padding:{pad}; box-shadow:0 4px 24px rgba(0,0,0,0.25);
                transition:all 0.2s ease">
        {content_html}
    </div>"""


# ══════════════════════════════════════════════════════════════════════════════
# SYNTHETIC EARNINGS DATA GENERATORS
# ══════════════════════════════════════════════════════════════════════════════

def _gen_eps_data(insights: list[Insight]) -> pd.DataFrame:
    """Generate synthetic EPS beat/miss data keyed to insights."""
    rng = random.Random(42)
    rows = []
    for carrier in CARRIERS:
        actual   = rng.uniform(1.2, 8.5)
        estimate = actual * rng.uniform(0.85, 1.15)
        surprise_pct = (actual - estimate) / abs(estimate) * 100
        stock_react  = surprise_pct * rng.uniform(0.4, 1.2) + rng.uniform(-2, 2)
        mktcap = rng.uniform(5, 85)  # $B
        rows.append({
            "Carrier":       carrier,
            "Actual EPS":    round(actual, 2),
            "Est EPS":       round(estimate, 2),
            "Surprise %":    round(surprise_pct, 1),
            "Stock React %": round(stock_react, 1),
            "Beat":          surprise_pct > 0,
            "Market Cap $B": round(mktcap, 1),
        })
    return pd.DataFrame(rows).sort_values("Surprise %", ascending=False).reset_index(drop=True)


def _gen_quarterly_eps(carrier: str) -> pd.DataFrame:
    rng = random.Random(hash(carrier) % 9999)
    quarters = ["Q1'24", "Q2'24", "Q3'24", "Q4'24", "Q1'25", "Q2'25", "Q3'25", "Q4'25"]
    base = rng.uniform(2.0, 6.0)
    rows = []
    for q in quarters:
        actual   = base + rng.uniform(-0.8, 1.2)
        estimate = actual * rng.uniform(0.88, 1.12)
        rows.append({"Quarter": q, "Actual EPS": round(actual, 2), "Consensus": round(estimate, 2)})
        base = actual * rng.uniform(0.95, 1.08)
    return pd.DataFrame(rows)


def _gen_revenue_earnings_growth() -> pd.DataFrame:
    rng = random.Random(7)
    rows = []
    for carrier in CARRIERS:
        rev_growth = rng.uniform(-8, 28)
        eps_growth = rng.uniform(-12, 35)
        mktcap     = rng.uniform(5, 85)
        rows.append({
            "Carrier":        carrier,
            "Rev Growth %":   round(rev_growth, 1),
            "EPS Growth %":   round(eps_growth, 1),
            "Market Cap $B":  round(mktcap, 1),
        })
    return pd.DataFrame(rows)


def _gen_beat_rate() -> pd.DataFrame:
    rng = random.Random(13)
    rows = []
    for carrier in CARRIERS:
        rate = rng.uniform(42, 92)
        rows.append({"Carrier": carrier, "Beat Rate %": round(rate, 1)})
    return pd.DataFrame(rows).sort_values("Beat Rate %")


def _gen_guidance() -> pd.DataFrame:
    rng = random.Random(99)
    signals = {
        "Raised":      "Strong buy catalyst — management raising bar",
        "Maintained":  "Neutral — in-line with street expectations",
        "Lowered":     "Caution — management guiding below consensus",
    }
    rows = []
    choices = ["Raised", "Maintained", "Lowered"]
    for carrier in CARRIERS:
        status = rng.choice(choices)
        rows.append({
            "Carrier":    carrier,
            "Guidance":   status,
            "Implication": signals[status],
            "Rev Guide $B": round(rng.uniform(8, 45), 1),
            "EPS Guide":    round(rng.uniform(1.5, 9.0), 2),
        })
    return pd.DataFrame(rows)


def _gen_segment_revenue() -> pd.DataFrame:
    rng = random.Random(55)
    segs = ["Asia-Europe", "Trans-Pacific", "Intra-Asia", "Atlantic", "Other"]
    rows = []
    for carrier in CARRIERS:
        total = rng.uniform(10, 50)
        splits = [rng.uniform(0.1, 0.35) for _ in segs]
        s_sum = sum(splits)
        for seg, sp in zip(segs, splits):
            rows.append({"Carrier": carrier, "Segment": seg, "Revenue $B": round(total * sp / s_sum, 2)})
    return pd.DataFrame(rows)


def _gen_margins() -> pd.DataFrame:
    rng = random.Random(21)
    quarters = ["Q1'24", "Q2'24", "Q3'24", "Q4'24", "Q1'25", "Q2'25", "Q3'25", "Q4'25"]
    rows = []
    for carrier in CARRIERS[:4]:
        gross = rng.uniform(28, 55)
        ebitda = gross * rng.uniform(0.55, 0.75)
        net    = ebitda * rng.uniform(0.45, 0.70)
        for q in quarters:
            rows.append({
                "Carrier": carrier,
                "Quarter": q,
                "Gross %": round(gross + rng.uniform(-3, 3), 1),
                "EBITDA %": round(ebitda + rng.uniform(-2.5, 2.5), 1),
                "Net %":    round(net + rng.uniform(-2, 2), 1),
            })
    return pd.DataFrame(rows)


def _gen_estimate_revisions() -> pd.DataFrame:
    rng = random.Random(88)
    weeks = [f"Wk-{i}" for i in range(8, 0, -1)]
    rows = []
    for wk in weeks:
        rows.append({
            "Week":    wk,
            "Raising": rng.randint(3, 15),
            "Lowering": rng.randint(1, 10),
        })
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Results Hero Dashboard
# ══════════════════════════════════════════════════════════════════════════════

def _render_results_hero(insights: list[Insight], eps_df: pd.DataFrame) -> None:
    try:
        total        = len(insights)
        avg_score    = sum(i.score for i in insights) / total if total else 0.0
        high_conv    = sum(1 for i in insights if i.score >= 0.70)
        avg_sc_color = _score_color(avg_score)

        # Universe P&L proxy from insights scores
        universe_pnl = (avg_score - 0.5) * 200  # synthetic basis points
        pnl_sign     = "+" if universe_pnl >= 0 else ""
        pnl_color    = C_HIGH if universe_pnl >= 0 else C_LOW

        best  = eps_df.iloc[0]
        worst = eps_df.iloc[-1]

        avg_eps_growth = eps_df["Surprise %"].mean()
        eps_g_color    = C_HIGH if avg_eps_growth >= 0 else C_LOW

        def hero_kpi(label, value, sub, color, glow=True):
            glow_css = f"text-shadow:0 0 20px {_hex_rgba(color, 0.45)};" if glow else ""
            return f"""
            <div style="flex:1; min-width:140px; background:{_hex_rgba(color, 0.06)};
                        border:1px solid {_hex_rgba(color, 0.22)}; border-radius:12px;
                        padding:18px 20px; text-align:center">
                <div style="font-size:0.6rem; font-weight:800; color:{C_TEXT3}; text-transform:uppercase;
                            letter-spacing:0.12em; margin-bottom:8px">{label}</div>
                <div style="font-size:2rem; font-weight:900; color:{color}; line-height:1; {glow_css}">{value}</div>
                <div style="font-size:0.72rem; color:{C_TEXT2}; margin-top:6px">{sub}</div>
            </div>"""

        kpis_html = "".join([
            hero_kpi("Universe P&L", f"{pnl_sign}{universe_pnl:.0f} bps", "vs prior quarter", pnl_color),
            hero_kpi("Avg Return", f"{avg_score:.1%}", "signal confidence", avg_sc_color),
            hero_kpi("Best Performer", best['Carrier'], f"+{best['Surprise %']:.1f}% EPS beat", C_HIGH),
            hero_kpi("Worst Performer", worst['Carrier'], f"{worst['Surprise %']:.1f}% EPS miss", C_LOW),
            hero_kpi("Avg EPS Growth", f"{avg_eps_growth:+.1f}%", f"{high_conv} high-conviction", eps_g_color),
        ])

        st.markdown(f"""
        <style>
        @keyframes heroFade {{
            from {{ opacity:0; transform:translateY(-8px); }}
            to   {{ opacity:1; transform:translateY(0); }}
        }}
        .earnings-hero {{ animation: heroFade 0.5s ease-out; }}
        </style>
        <div class="earnings-hero" style="
            background:linear-gradient(135deg, #080d16 0%, {C_CARD} 50%, #0c1628 100%);
            border:1px solid rgba(59,130,246,0.25); border-radius:18px;
            padding:28px 30px; margin-bottom:24px;
            box-shadow:0 0 48px rgba(59,130,246,0.07), inset 0 1px 0 rgba(255,255,255,0.04)">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:22px">
                <div>
                    <div style="font-size:1.25rem; font-weight:900; color:{C_TEXT}; letter-spacing:-0.02em">
                        Earnings Intelligence Dashboard
                    </div>
                    <div style="font-size:0.8rem; color:{C_TEXT2}; margin-top:3px">
                        Carrier universe · {total} active signals · Q4 2025 earnings season
                    </div>
                </div>
                <div style="font-size:0.7rem; font-weight:700; color:{C_HIGH};
                            background:{_hex_rgba(C_HIGH, 0.1)}; border:1px solid {_hex_rgba(C_HIGH, 0.28)};
                            padding:5px 14px; border-radius:999px">
                    ● LIVE FEED
                </div>
            </div>
            <div style="display:flex; gap:12px; flex-wrap:wrap">
                {kpis_html}
            </div>
        </div>""", unsafe_allow_html=True)
    except Exception as e:
        st.warning(f"Results hero unavailable: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Earnings Performance League Table
# ══════════════════════════════════════════════════════════════════════════════

def _render_league_table(eps_df: pd.DataFrame) -> None:
    try:
        _section_header(
            "Earnings Performance League Table",
            "All carriers ranked by EPS beat/miss · colored badges · surprise % bars"
        )

        rows_html = ""
        for rank, row in eps_df.iterrows():
            beat      = row["Beat"]
            surp      = row["Surprise %"]
            badge_col = C_HIGH if beat else C_LOW
            badge_txt = f"BEAT +{surp:.1f}%" if beat else f"MISS {surp:.1f}%"
            bar_w     = min(abs(surp) * 2.5, 100)
            bar_col   = C_HIGH if beat else C_LOW
            rank_num  = rank + 1

            rows_html += f"""
            <div style="display:flex; align-items:center; gap:14px; padding:12px 18px;
                        background:{_hex_rgba(badge_col, 0.04) if rank % 2 == 0 else 'transparent'};
                        border-bottom:1px solid {C_BORDER}">
                <div style="width:28px; text-align:center; font-size:0.78rem; font-weight:800;
                            color:{C_ACCENT if rank_num <= 3 else C_TEXT3}">#{rank_num}</div>
                <div style="width:110px; font-size:0.88rem; font-weight:700; color:{C_TEXT}">{row['Carrier']}</div>
                <div style="flex:1">
                    <div style="display:flex; align-items:center; gap:8px">
                        <div style="width:{bar_w}%; height:6px; background:{bar_col};
                                    border-radius:3px; transition:width 0.4s ease;
                                    box-shadow:0 0 8px {_hex_rgba(bar_col, 0.5)}"></div>
                        <span style="font-size:0.75rem; color:{C_TEXT2}">{abs(surp):.1f}%</span>
                    </div>
                </div>
                <span style="background:{_hex_rgba(badge_col, 0.15)}; color:{badge_col};
                             border:1px solid {_hex_rgba(badge_col, 0.4)};
                             padding:3px 12px; border-radius:999px; font-size:0.7rem; font-weight:800;
                             min-width:120px; text-align:center">{badge_txt}</span>
                <div style="width:60px; text-align:right; font-size:0.82rem; font-weight:700; color:{C_TEXT}">
                    ${row['Actual EPS']:.2f}
                </div>
                <div style="width:60px; text-align:right; font-size:0.75rem; color:{C_TEXT3}">
                    est ${row['Est EPS']:.2f}
                </div>
            </div>"""

        st.markdown(f"""
        <div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:14px; overflow:hidden;
                    box-shadow:0 4px 24px rgba(0,0,0,0.3)">
            <div style="display:flex; align-items:center; gap:14px; padding:12px 18px;
                        background:rgba(255,255,255,0.03); border-bottom:1px solid {C_BORDER}">
                <div style="width:28px; font-size:0.6rem; font-weight:800; color:{C_TEXT3}; text-transform:uppercase">#</div>
                <div style="width:110px; font-size:0.6rem; font-weight:800; color:{C_TEXT3}; text-transform:uppercase">Carrier</div>
                <div style="flex:1; font-size:0.6rem; font-weight:800; color:{C_TEXT3}; text-transform:uppercase">Surprise Bar</div>
                <div style="min-width:120px; text-align:center; font-size:0.6rem; font-weight:800; color:{C_TEXT3}; text-transform:uppercase">Beat / Miss</div>
                <div style="width:60px; text-align:right; font-size:0.6rem; font-weight:800; color:{C_TEXT3}; text-transform:uppercase">Actual</div>
                <div style="width:60px; text-align:right; font-size:0.6rem; font-weight:800; color:{C_TEXT3}; text-transform:uppercase">Est</div>
            </div>
            {rows_html}
        </div>""", unsafe_allow_html=True)
    except Exception as e:
        st.warning(f"League table unavailable: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Revenue vs Earnings Growth Scatter (Bubble Chart)
# ══════════════════════════════════════════════════════════════════════════════

def _render_rev_eps_scatter(growth_df: pd.DataFrame) -> None:
    try:
        _section_header(
            "Revenue vs Earnings Growth",
            "Bubble size = market cap · quadrant labels identify growth profile"
        )

        colors = [C_HIGH if (r > 0 and e > 0) else
                  C_MOD  if (r > 0 or e > 0) else
                  C_LOW
                  for r, e in zip(growth_df["Rev Growth %"], growth_df["EPS Growth %"])]

        fig = go.Figure()

        fig.add_shape(type="line", x0=0, x1=0, y0=growth_df["EPS Growth %"].min()-5,
                      y1=growth_df["EPS Growth %"].max()+5, line=dict(color=C_BORDER, width=1, dash="dot"))
        fig.add_shape(type="line", y0=0, y1=0, x0=growth_df["Rev Growth %"].min()-5,
                      x1=growth_df["Rev Growth %"].max()+5, line=dict(color=C_BORDER, width=1, dash="dot"))

        for q_label, q_x, q_y, q_col in [
            ("Stars", 0.85, 0.92, C_HIGH),
            ("Rev-Led", 0.08, 0.92, C_MOD),
            ("EPS-Led", 0.85, 0.08, C_MOD),
            ("Laggards", 0.08, 0.08, C_LOW),
        ]:
            fig.add_annotation(
                xref="paper", yref="paper", x=q_x, y=q_y,
                text=q_label, showarrow=False,
                font=dict(size=10, color=_hex_rgba(q_col, 0.45)),
            )

        fig.add_trace(go.Scatter(
            x=growth_df["Rev Growth %"],
            y=growth_df["EPS Growth %"],
            mode="markers+text",
            text=growth_df["Carrier"],
            textposition="top center",
            textfont=dict(size=10, color=C_TEXT2),
            marker=dict(
                size=growth_df["Market Cap $B"] * 0.9,
                color=colors,
                opacity=0.82,
                line=dict(width=1, color=C_BORDER),
                sizemode="area",
            ),
            hovertemplate=(
                "<b>%{text}</b><br>Rev Growth: %{x:.1f}%<br>"
                "EPS Growth: %{y:.1f}%<extra></extra>"
            ),
        ))

        fig.update_layout(
            height=420,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor=_hex_rgba(C_CARD, 0.8),
            font=dict(color=C_TEXT2, family="Inter, sans-serif"),
            xaxis=dict(title="Revenue Growth %", gridcolor=C_BORDER, zeroline=False),
            yaxis=dict(title="EPS Growth %", gridcolor=C_BORDER, zeroline=False),
            margin=dict(l=50, r=30, t=20, b=50),
            showlegend=False,
        )

        st.plotly_chart(fig, use_container_width=True, key="rev_eps_scatter")
    except Exception as e:
        st.warning(f"Scatter chart unavailable: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — EPS Trend Chart with Consensus Line
# ══════════════════════════════════════════════════════════════════════════════

def _render_eps_trend(insights: list[Insight]) -> None:
    try:
        _section_header(
            "EPS Trend vs Consensus Estimate",
            "Quarterly EPS actuals vs analyst consensus · select carriers to compare"
        )

        selected = st.multiselect(
            "Select carriers",
            CARRIERS,
            default=CARRIERS[:3],
            key="eps_trend_carriers",
        )
        if not selected:
            st.info("Select at least one carrier to display the EPS trend.")
            return

        fig = go.Figure()
        palette = [C_ACCENT, C_HIGH, C_CONV, C_MOD, C_MACRO, C_LOW, "#e879f9", "#fb923c"]

        for ci, carrier in enumerate(selected):
            df  = _gen_quarterly_eps(carrier)
            col = palette[ci % len(palette)]

            fig.add_trace(go.Scatter(
                x=df["Quarter"], y=df["Actual EPS"],
                name=f"{carrier} Actual",
                mode="lines+markers",
                line=dict(color=col, width=2.5),
                marker=dict(size=7, color=col),
                hovertemplate=f"<b>{carrier}</b> Actual: $%{{y:.2f}}<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=df["Quarter"], y=df["Consensus"],
                name=f"{carrier} Consensus",
                mode="lines",
                line=dict(color=col, width=1.5, dash="dot"),
                opacity=0.55,
                hovertemplate=f"<b>{carrier}</b> Consensus: $%{{y:.2f}}<extra></extra>",
            ))

        fig.update_layout(
            height=400,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor=_hex_rgba(C_CARD, 0.8),
            font=dict(color=C_TEXT2, family="Inter, sans-serif"),
            xaxis=dict(title="Quarter", gridcolor=C_BORDER),
            yaxis=dict(title="EPS ($)", gridcolor=C_BORDER),
            legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
            margin=dict(l=50, r=30, t=20, b=50),
        )
        st.plotly_chart(fig, use_container_width=True, key="eps_trend_chart")
    except Exception as e:
        st.warning(f"EPS trend unavailable: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Beat Rate by Carrier
# ══════════════════════════════════════════════════════════════════════════════

def _render_beat_rate(beat_df: pd.DataFrame) -> None:
    try:
        _section_header(
            "Historical EPS Beat Rate by Carrier",
            "% of quarters where carrier beat consensus EPS estimate"
        )

        colors = [C_HIGH if r >= 70 else C_MOD if r >= 55 else C_LOW for r in beat_df["Beat Rate %"]]

        fig = go.Figure(go.Bar(
            x=beat_df["Beat Rate %"],
            y=beat_df["Carrier"],
            orientation="h",
            marker=dict(
                color=colors,
                line=dict(width=0),
                opacity=0.88,
            ),
            text=[f"{r:.0f}%" for r in beat_df["Beat Rate %"]],
            textposition="outside",
            textfont=dict(color=C_TEXT2, size=12, family="Inter, sans-serif"),
            hovertemplate="<b>%{y}</b>: %{x:.1f}% beat rate<extra></extra>",
        ))

        fig.add_vline(x=70, line_color=C_HIGH, line_dash="dot", line_width=1.5,
                      annotation_text="70% threshold", annotation_font_color=C_HIGH,
                      annotation_font_size=10)
        fig.add_vline(x=50, line_color=C_MOD, line_dash="dot", line_width=1,
                      annotation_text="50% break-even", annotation_font_color=C_MOD,
                      annotation_font_size=10, annotation_position="bottom right")

        fig.update_layout(
            height=340,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor=_hex_rgba(C_CARD, 0.8),
            font=dict(color=C_TEXT2, family="Inter, sans-serif"),
            xaxis=dict(title="Beat Rate %", gridcolor=C_BORDER, range=[0, 105]),
            yaxis=dict(title="", gridcolor="rgba(0,0,0,0)"),
            margin=dict(l=90, r=60, t=20, b=50),
        )
        st.plotly_chart(fig, use_container_width=True, key="beat_rate_chart")
    except Exception as e:
        st.warning(f"Beat rate chart unavailable: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Forward Guidance Tracker
# ══════════════════════════════════════════════════════════════════════════════

def _render_guidance_tracker(guidance_df: pd.DataFrame) -> None:
    try:
        _section_header(
            "Forward Guidance Tracker",
            "Management guidance status and signal implications for each carrier"
        )

        GUIDE_COLORS  = {"Raised": C_HIGH, "Maintained": C_ACCENT, "Lowered": C_LOW}
        GUIDE_ICONS   = {"Raised": "▲", "Maintained": "●", "Lowered": "▼"}

        rows_html = ""
        for _, row in guidance_df.iterrows():
            gc  = GUIDE_COLORS.get(row["Guidance"], C_TEXT2)
            ico = GUIDE_ICONS.get(row["Guidance"], "●")
            rows_html += f"""
            <div style="display:flex; align-items:center; gap:16px; padding:13px 20px;
                        border-bottom:1px solid {C_BORDER}">
                <div style="width:120px; font-size:0.88rem; font-weight:700; color:{C_TEXT}">{row['Carrier']}</div>
                <div style="width:130px">
                    <span style="background:{_hex_rgba(gc, 0.15)}; color:{gc};
                                 border:1px solid {_hex_rgba(gc, 0.4)};
                                 padding:4px 14px; border-radius:999px; font-size:0.72rem; font-weight:800">
                        {ico} {row['Guidance']}
                    </span>
                </div>
                <div style="flex:1; font-size:0.8rem; color:{C_TEXT2}; line-height:1.4">{row['Implication']}</div>
                <div style="width:90px; text-align:right; font-size:0.82rem; font-weight:700; color:{C_TEXT}">
                    ${row['Rev Guide $B']:.1f}B rev
                </div>
                <div style="width:80px; text-align:right; font-size:0.82rem; font-weight:700; color:{gc}">
                    ${row['EPS Guide']:.2f} EPS
                </div>
            </div>"""

        raised    = (guidance_df["Guidance"] == "Raised").sum()
        maintained = (guidance_df["Guidance"] == "Maintained").sum()
        lowered   = (guidance_df["Guidance"] == "Lowered").sum()

        summary_html = f"""
        <div style="display:flex; gap:16px; padding:14px 20px; background:rgba(255,255,255,0.02); border-bottom:1px solid {C_BORDER}">
            <div style="display:flex; align-items:center; gap:8px">
                <span style="font-size:1.1rem; font-weight:900; color:{C_HIGH}">{raised}</span>
                <span style="font-size:0.7rem; color:{C_TEXT2}">Raised</span>
            </div>
            <div style="width:1px; background:{C_BORDER}"></div>
            <div style="display:flex; align-items:center; gap:8px">
                <span style="font-size:1.1rem; font-weight:900; color:{C_ACCENT}">{maintained}</span>
                <span style="font-size:0.7rem; color:{C_TEXT2}">Maintained</span>
            </div>
            <div style="width:1px; background:{C_BORDER}"></div>
            <div style="display:flex; align-items:center; gap:8px">
                <span style="font-size:1.1rem; font-weight:900; color:{C_LOW}">{lowered}</span>
                <span style="font-size:0.7rem; color:{C_TEXT2}">Lowered</span>
            </div>
        </div>"""

        st.markdown(f"""
        <div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:14px; overflow:hidden;
                    box-shadow:0 4px 24px rgba(0,0,0,0.3)">
            {summary_html}
            {rows_html}
        </div>""", unsafe_allow_html=True)
    except Exception as e:
        st.warning(f"Guidance tracker unavailable: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Revenue Breakdown by Segment
# ══════════════════════════════════════════════════════════════════════════════

def _render_segment_revenue(seg_df: pd.DataFrame) -> None:
    try:
        _section_header(
            "Revenue Breakdown by Segment",
            "Stacked revenue contribution per carrier across trade lanes"
        )

        segs    = seg_df["Segment"].unique().tolist()
        palette = [C_ACCENT, C_HIGH, C_CONV, C_MOD, C_MACRO]

        pivot = seg_df.pivot(index="Carrier", columns="Segment", values="Revenue $B").fillna(0)
        carriers_sorted = pivot.sum(axis=1).sort_values(ascending=False).index.tolist()

        fig = go.Figure()
        for si, seg in enumerate(segs):
            if seg not in pivot.columns:
                continue
            fig.add_trace(go.Bar(
                name=seg,
                x=carriers_sorted,
                y=pivot.loc[carriers_sorted, seg],
                marker=dict(color=palette[si % len(palette)], opacity=0.88),
                hovertemplate=f"<b>{seg}</b>: $%{{y:.2f}}B<extra></extra>",
            ))

        fig.update_layout(
            barmode="stack",
            height=380,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor=_hex_rgba(C_CARD, 0.8),
            font=dict(color=C_TEXT2, family="Inter, sans-serif"),
            xaxis=dict(title="", gridcolor=C_BORDER),
            yaxis=dict(title="Revenue $B", gridcolor=C_BORDER),
            legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h", y=-0.2),
            margin=dict(l=50, r=30, t=20, b=80),
        )
        st.plotly_chart(fig, use_container_width=True, key="segment_rev_chart")
    except Exception as e:
        st.warning(f"Segment revenue chart unavailable: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — Margin Expansion / Compression
# ══════════════════════════════════════════════════════════════════════════════

def _render_margin_trends(margin_df: pd.DataFrame) -> None:
    try:
        _section_header(
            "Margin Expansion / Compression",
            "Gross, EBITDA, and net margin trends for top 4 carriers"
        )

        carriers_in = margin_df["Carrier"].unique().tolist()
        sel_carrier = st.selectbox(
            "Carrier", carriers_in, key="margin_carrier_sel"
        )

        df = margin_df[margin_df["Carrier"] == sel_carrier]

        fig = go.Figure()
        for metric, col, dash in [
            ("Gross %", C_HIGH, "solid"),
            ("EBITDA %", C_ACCENT, "dash"),
            ("Net %", C_CONV, "dot"),
        ]:
            fig.add_trace(go.Scatter(
                x=df["Quarter"], y=df[metric],
                name=metric.replace(" %", ""),
                mode="lines+markers",
                line=dict(color=col, width=2.5, dash=dash),
                marker=dict(size=6, color=col),
                fill="tozeroy" if metric == "Gross %" else "none",
                fillcolor=_hex_rgba(col, 0.07),
                hovertemplate=f"<b>{metric}</b>: %{{y:.1f}}%<extra></extra>",
            ))

        fig.update_layout(
            height=360,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor=_hex_rgba(C_CARD, 0.8),
            font=dict(color=C_TEXT2, family="Inter, sans-serif"),
            xaxis=dict(title="Quarter", gridcolor=C_BORDER),
            yaxis=dict(title="Margin %", gridcolor=C_BORDER),
            legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h", y=1.1, x=0),
            margin=dict(l=50, r=30, t=30, b=50),
        )
        st.plotly_chart(fig, use_container_width=True, key="margin_trend_chart")
    except Exception as e:
        st.warning(f"Margin trends unavailable: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — Earnings Surprise vs Share Price Reaction
# ══════════════════════════════════════════════════════════════════════════════

def _render_surprise_price_scatter(eps_df: pd.DataFrame) -> None:
    try:
        _section_header(
            "Earnings Surprise Impact on Share Price",
            "EPS surprise % vs stock price reaction — reveals market pricing efficiency"
        )

        beat_mask = eps_df["Beat"]
        colors    = [C_HIGH if b else C_LOW for b in beat_mask]

        fig = go.Figure()

        # Trend line approximation
        x_vals = eps_df["Surprise %"].values
        y_vals = eps_df["Stock React %"].values
        if len(x_vals) > 1:
            m, b_ = np.polyfit(x_vals, y_vals, 1)
            x_line = np.linspace(x_vals.min(), x_vals.max(), 50)
            y_line = m * x_line + b_
            fig.add_trace(go.Scatter(
                x=x_line, y=y_line,
                mode="lines", name="Trend",
                line=dict(color=_hex_rgba(C_ACCENT, 0.4), width=2, dash="dot"),
                showlegend=True,
            ))

        fig.add_trace(go.Scatter(
            x=eps_df["Surprise %"],
            y=eps_df["Stock React %"],
            mode="markers+text",
            text=eps_df["Carrier"],
            textposition="top center",
            textfont=dict(size=10, color=C_TEXT2),
            marker=dict(
                size=14,
                color=colors,
                opacity=0.85,
                line=dict(width=1.5, color=C_BORDER),
                symbol=["triangle-up" if b else "triangle-down" for b in beat_mask],
            ),
            name="Carriers",
            hovertemplate=(
                "<b>%{text}</b><br>EPS Surprise: %{x:.1f}%<br>"
                "Stock Reaction: %{y:.1f}%<extra></extra>"
            ),
        ))

        fig.add_hline(y=0, line_color=C_BORDER, line_width=1)
        fig.add_vline(x=0, line_color=C_BORDER, line_width=1)

        fig.update_layout(
            height=400,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor=_hex_rgba(C_CARD, 0.8),
            font=dict(color=C_TEXT2, family="Inter, sans-serif"),
            xaxis=dict(title="EPS Surprise %", gridcolor=C_BORDER, zeroline=False),
            yaxis=dict(title="Stock Price Reaction %", gridcolor=C_BORDER, zeroline=False),
            legend=dict(bgcolor="rgba(0,0,0,0)"),
            margin=dict(l=50, r=30, t=20, b=50),
        )
        st.plotly_chart(fig, use_container_width=True, key="surprise_price_scatter")
    except Exception as e:
        st.warning(f"Surprise/price scatter unavailable: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — Consensus Estimate Revisions
# ══════════════════════════════════════════════════════════════════════════════

def _render_estimate_revisions(rev_df: pd.DataFrame) -> None:
    try:
        _section_header(
            "Consensus Estimate Revisions",
            "Analysts raising vs lowering EPS estimates over the past 8 weeks"
        )

        fig = go.Figure()

        fig.add_trace(go.Bar(
            x=rev_df["Week"],
            y=rev_df["Raising"],
            name="Raising",
            marker=dict(color=C_HIGH, opacity=0.85),
            hovertemplate="<b>Raising</b>: %{y} analysts<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            x=rev_df["Week"],
            y=[-v for v in rev_df["Lowering"]],
            name="Lowering",
            marker=dict(color=C_LOW, opacity=0.85),
            hovertemplate="<b>Lowering</b>: %{customdata} analysts<extra></extra>",
            customdata=rev_df["Lowering"],
        ))

        # Net revision line
        net = rev_df["Raising"] - rev_df["Lowering"]
        fig.add_trace(go.Scatter(
            x=rev_df["Week"],
            y=net,
            name="Net Revision",
            mode="lines+markers",
            line=dict(color=C_ACCENT, width=2.5),
            marker=dict(size=7, color=C_ACCENT),
            hovertemplate="<b>Net</b>: %{y}<extra></extra>",
        ))

        fig.add_hline(y=0, line_color=C_BORDER, line_width=1)

        fig.update_layout(
            barmode="relative",
            height=360,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor=_hex_rgba(C_CARD, 0.8),
            font=dict(color=C_TEXT2, family="Inter, sans-serif"),
            xaxis=dict(title="Week (most recent = right)", gridcolor=C_BORDER),
            yaxis=dict(title="# Analysts", gridcolor=C_BORDER),
            legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h", y=1.1),
            margin=dict(l=50, r=30, t=30, b=50),
        )
        st.plotly_chart(fig, use_container_width=True, key="estimate_revisions_chart")

        # Insight callout
        last_net = net.iloc[-1]
        if last_net > 0:
            callout_col = C_HIGH
            callout_msg = f"Positive revision momentum: +{last_net:.0f} net analysts raising this week. Bullish signal."
        elif last_net < 0:
            callout_col = C_LOW
            callout_msg = f"Negative revision pressure: {last_net:.0f} net analysts lowering this week. Watch for downside."
        else:
            callout_col = C_TEXT2
            callout_msg = "Revisions balanced this week. Neutral signal."

        st.markdown(
            f"""<div style="background:{_hex_rgba(callout_col, 0.08)}; border:1px solid {_hex_rgba(callout_col, 0.3)};
                          border-left:3px solid {callout_col}; border-radius:10px; padding:12px 18px; margin-top:8px">
                <span style="font-size:0.82rem; color:{callout_col}; font-weight:700">{callout_msg}</span>
            </div>""",
            unsafe_allow_html=True,
        )
    except Exception as e:
        st.warning(f"Estimate revisions unavailable: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# LEGACY HELPERS (preserved from original)
# ══════════════════════════════════════════════════════════════════════════════

def _render_stale_banner(insights: list[Insight]) -> None:
    try:
        stale = [i for i in insights if i.data_freshness_warning]
        if not stale:
            return
        st.markdown(
            f"""<div style="background:{_hex_rgba(C_MOD, 0.12)}; border:1px solid {_hex_rgba(C_MOD, 0.35)};
                           border-radius:10px; padding:10px 16px; margin-bottom:14px; display:flex; align-items:center; gap:10px">
                <span style="font-size:1rem">⚠️</span>
                <span style="font-size:0.82rem; color:{C_MOD}; font-weight:600">
                    {len(stale)} signal(s) have stale data — refresh data sources for best accuracy.
                </span>
            </div>""",
            unsafe_allow_html=True,
        )
    except Exception:
        pass


def _render_signal_consensus(insights: list[Insight]) -> None:
    try:
        counts = {}
        for i in insights:
            counts[i.action] = counts.get(i.action, 0) + 1
        total = len(insights)
        if total == 0:
            return

        bar_segs = ""
        for action, count in sorted(counts.items(), key=lambda x: -x[1]):
            pct = count / total * 100
            col = ACTION_COLORS.get(action, C_TEXT2)
            bar_segs += f"""<div title="{action}: {count}" style="flex:{pct}; background:{col};
                                height:100%; min-width:4px; transition:flex 0.4s ease"></div>"""

        st.markdown(
            f"""<div style="margin-bottom:12px">
                <div style="font-size:0.65rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase;
                            letter-spacing:0.1em; margin-bottom:6px">Signal Consensus</div>
                <div style="display:flex; height:8px; border-radius:4px; overflow:hidden; gap:2px">
                    {bar_segs}
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
    except Exception:
        pass


def _render_filter_controls(insights: list[Insight]) -> list[Insight]:
    try:
        all_actions = sorted(set(i.action for i in insights))
        all_cats    = sorted(set(i.category for i in insights))

        c1, c2, c3 = st.columns([2, 2, 2])
        with c1:
            sel_actions = st.multiselect("Action", all_actions, default=all_actions, key="results_filter_action")
        with c2:
            sel_cats = st.multiselect("Category", all_cats, default=all_cats, key="results_filter_cat")
        with c3:
            min_score = st.slider("Min Score", 0, 100, 0, 5, key="results_filter_score")

        filtered = [
            i for i in insights
            if i.action in sel_actions
            and i.category in sel_cats
            and i.score >= min_score / 100.0
        ]
        return filtered
    except Exception:
        return insights


def _render_rich_insight_card(ins: Insight, card_key: str = "") -> None:
    try:
        sc_col = _score_color(ins.score)
        cat_col = CATEGORY_COLORS.get(ins.category, C_ACCENT)
        cat_ico = CATEGORY_ICONS.get(ins.category, "📌")
        ac_col  = ACTION_COLORS.get(ins.action, C_ACCENT)

        with st.expander(
            f"{cat_ico} [{ins.action}] {ins.title}  ·  {ins.score:.0%}",
            expanded=False,
            key=f"rich_card_{card_key}_{ins.title[:20]}",
        ):
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                st.markdown(
                    f"<div style='font-size:0.82rem; color:{C_TEXT2}; line-height:1.6'>{ins.reasoning}</div>",
                    unsafe_allow_html=True,
                )
            with c2:
                st.markdown(
                    f"<div style='font-size:0.7rem; color:{C_TEXT3}'>Ports: {', '.join(ins.ports_involved[:3]) if ins.ports_involved else '—'}</div>",
                    unsafe_allow_html=True,
                )
            with c3:
                st.markdown(
                    f"<div style='font-size:0.7rem; color:{C_TEXT3}'>Routes: {', '.join(ins.routes_involved[:2]) if ins.routes_involved else '—'}</div>",
                    unsafe_allow_html=True,
                )
    except Exception:
        pass


def _render_distribution_charts(insights: list[Insight]) -> None:
    try:
        cats   = [i.category for i in insights]
        scores = [i.score for i in insights]
        actions = [i.action for i in insights]

        cat_counts = {c: cats.count(c) for c in set(cats)}
        act_counts = {a: actions.count(a) for a in set(actions)}

        c1, c2 = st.columns(2)
        with c1:
            fig = go.Figure(go.Pie(
                labels=list(cat_counts.keys()),
                values=list(cat_counts.values()),
                marker=dict(colors=[CATEGORY_COLORS.get(k, C_TEXT2) for k in cat_counts]),
                hole=0.5,
                textfont=dict(size=11),
            ))
            fig.update_layout(
                height=220, paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color=C_TEXT2), showlegend=True,
                margin=dict(l=10, r=10, t=20, b=10),
                legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
            )
            st.plotly_chart(fig, use_container_width=True, key="dist_cat_pie")

        with c2:
            fig2 = go.Figure(go.Bar(
                x=list(act_counts.keys()),
                y=list(act_counts.values()),
                marker=dict(color=[ACTION_COLORS.get(k, C_ACCENT) for k in act_counts], opacity=0.85),
            ))
            fig2.update_layout(
                height=220, paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor=_hex_rgba(C_CARD, 0.8),
                font=dict(color=C_TEXT2), showlegend=False,
                xaxis=dict(gridcolor=C_BORDER),
                yaxis=dict(gridcolor=C_BORDER),
                margin=dict(l=30, r=10, t=20, b=40),
            )
            st.plotly_chart(fig2, use_container_width=True, key="dist_action_bar")
    except Exception:
        pass


def _render_export_dataframe(insights: list[Insight]) -> None:
    try:
        if not insights:
            return
        rows = []
        for ins in insights:
            rows.append({
                "Score":    f"{ins.score:.0%}",
                "Action":   ins.action,
                "Category": ins.category,
                "Title":    ins.title,
                "Ports":    ", ".join(ins.ports_involved[:3]) if ins.ports_involved else "—",
                "Routes":   ", ".join(ins.routes_involved[:2]) if ins.routes_involved else "—",
                "Signals":  len(ins.supporting_signals),
                "Stale":    "⚠️" if ins.data_freshness_warning else "✓",
            })

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True, height=min(400, 50 + 36 * len(df)))

        buf = io.StringIO()
        df.to_csv(buf, index=False)
        st.download_button(
            "Download CSV",
            data=buf.getvalue(),
            file_name="ship_signals_filtered.csv",
            mime="text/csv",
            use_container_width=False,
            key="results_dl_btn",
        )
    except Exception:
        pass


def _render_hero_card(ins: Insight) -> None:
    try:
        sc_col  = _score_color(ins.score)
        cat_col = CATEGORY_COLORS.get(ins.category, C_ACCENT)
        cat_ico = CATEGORY_ICONS.get(ins.category, "📌")
        ac_col  = ACTION_COLORS.get(ins.action, C_ACCENT)

        ports_str  = ", ".join(ins.ports_involved[:4]) if ins.ports_involved else "—"
        routes_str = ", ".join(ins.routes_involved[:3]) if ins.routes_involved else "—"

        st.markdown(
            f"""<div style="background:linear-gradient(135deg,{C_CARD},#0f1d35);
                           border:1px solid {_hex_rgba(cat_col,0.35)}; border-left:4px solid {cat_col};
                           border-radius:14px; padding:22px 26px; margin-bottom:16px;
                           box-shadow:0 0 28px {_hex_rgba(cat_col,0.08)}">
                <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; flex-wrap:wrap">
                    <div style="flex:1">
                        <div style="font-size:0.62rem; font-weight:800; color:{cat_col}; text-transform:uppercase;
                                    letter-spacing:0.1em; margin-bottom:6px">{cat_ico} {ins.category} · TOP SIGNAL</div>
                        <div style="font-size:1.05rem; font-weight:800; color:{C_TEXT}; margin-bottom:8px; line-height:1.3">{ins.title}</div>
                        <div style="font-size:0.8rem; color:{C_TEXT2}; line-height:1.6; max-width:680px">{ins.reasoning}</div>
                    </div>
                    <div style="display:flex; flex-direction:column; align-items:flex-end; gap:8px">
                        <div style="font-size:2rem; font-weight:900; color:{sc_col}; text-shadow:0 0 16px {_hex_rgba(sc_col,0.4)}">{ins.score:.0%}</div>
                        <span style="background:{_hex_rgba(ac_col,0.15)}; color:{ac_col};
                                     border:1px solid {_hex_rgba(ac_col,0.4)};
                                     padding:4px 14px; border-radius:999px; font-size:0.72rem; font-weight:800">{ins.action}</span>
                    </div>
                </div>
                <div style="display:flex; gap:20px; margin-top:14px; flex-wrap:wrap">
                    <div style="font-size:0.72rem; color:{C_TEXT3}">Ports: <span style="color:{C_TEXT2}">{ports_str}</span></div>
                    <div style="font-size:0.72rem; color:{C_TEXT3}">Routes: <span style="color:{C_TEXT2}">{routes_str}</span></div>
                    <div style="font-size:0.72rem; color:{C_TEXT3}">Supporting signals: <span style="color:{C_TEXT2}">{len(ins.supporting_signals)}</span></div>
                    {"<span style='font-size:0.7rem; color:" + C_MOD + "; background:" + _hex_rgba(C_MOD, 0.1) + "; padding:2px 8px; border-radius:4px'>⚠️ Stale data</span>" if ins.data_freshness_warning else ""}
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
    except Exception:
        pass


def _render_insight_card(ins: Insight, cat_colors, cat_icons, act_colors, card_key: str = "") -> None:
    try:
        sc_col  = _score_color(ins.score)
        cat_col = cat_colors.get(ins.category, C_ACCENT)
        cat_ico = cat_icons.get(ins.category, "📌")
        ac_col  = act_colors.get(ins.action, C_ACCENT)

        key_safe = f"insight_card_{card_key}_{ins.title[:15].replace(' ','_')}"
        with st.expander(f"{cat_ico} {ins.title}  ·  {ins.score:.0%}  ·  {ins.action}", expanded=False, key=key_safe):
            st.markdown(
                f"<div style='font-size:0.82rem; color:{C_TEXT2}; line-height:1.6'>{ins.reasoning}</div>",
                unsafe_allow_html=True,
            )
            if ins.ports_involved:
                st.markdown(f"<div style='font-size:0.72rem; color:{C_TEXT3}; margin-top:8px'>Ports: {', '.join(ins.ports_involved)}</div>", unsafe_allow_html=True)
    except Exception:
        pass


def _render_convergence_meter(insights: list[Insight]) -> None:
    try:
        conv = [i for i in insights if i.category == "CONVERGENCE"]
        if not conv:
            return
        avg  = sum(i.score for i in conv) / len(conv)
        col  = _score_color(avg)
        bar_w = int(avg * 100)
        st.markdown(
            f"""<div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:10px; padding:14px 18px; margin-bottom:16px">
                <div style="font-size:0.62rem; font-weight:800; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.1em; margin-bottom:8px">
                    Convergence Meter · {len(conv)} signals
                </div>
                <div style="background:rgba(255,255,255,0.05); border-radius:4px; height:10px; overflow:hidden">
                    <div style="width:{bar_w}%; height:100%; background:{col};
                                box-shadow:0 0 12px {_hex_rgba(col, 0.5)}; transition:width 0.5s ease"></div>
                </div>
                <div style="font-size:0.75rem; color:{col}; margin-top:6px; font-weight:700">{avg:.0%} avg convergence score</div>
            </div>""",
            unsafe_allow_html=True,
        )
    except Exception:
        pass


def _render_insight_timeline(insights: list[Insight], chart_key: str = "") -> None:
    try:
        if not insights:
            return
        carriers_x = [i.title[:20] for i in insights]
        scores_y   = [i.score for i in insights]
        colors     = [_score_color(s) for s in scores_y]

        fig = go.Figure(go.Bar(
            x=carriers_x, y=scores_y,
            marker=dict(color=colors, opacity=0.85),
            hovertemplate="<b>%{x}</b>: %{y:.0%}<extra></extra>",
        ))
        fig.update_layout(
            height=220,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor=_hex_rgba(C_CARD, 0.6),
            font=dict(color=C_TEXT2, size=11),
            xaxis=dict(gridcolor=C_BORDER, tickangle=-30),
            yaxis=dict(gridcolor=C_BORDER, tickformat=".0%", range=[0, 1]),
            margin=dict(l=40, r=10, t=10, b=70),
        )
        st.plotly_chart(fig, use_container_width=True, key=f"timeline_{chart_key}")
    except Exception:
        pass


def _render_seasonal(insights: list[Insight]) -> None:
    try:
        from processing.seasonal import get_active_seasonal_signals
        seasonal = get_active_seasonal_signals()
        active   = [s for s in seasonal if s.active_now]
        upcoming = [s for s in seasonal if not s.active_now and s.days_until <= 60]

        if not active and not upcoming:
            st.markdown(f"<div style='color:{C_TEXT3}; font-size:0.85rem; padding:8px 0'>No active seasonal patterns in the next 60 days.</div>", unsafe_allow_html=True)
            return

        for sig in active:
            s_color = C_HIGH if sig.direction == "bullish" else C_LOW if sig.direction == "bearish" else C_TEXT2
            st.markdown(
                f"""<div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-left:3px solid {s_color};
                                border-radius:10px; padding:14px 18px; margin-bottom:8px">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:5px">
                        <div style="font-size:0.9rem; font-weight:700; color:{C_TEXT}">🔄 &nbsp;{sig.name}</div>
                        <span style="background:{_hex_rgba(s_color,0.15)}; color:{s_color};
                            padding:2px 10px; border-radius:999px; font-size:0.7rem; font-weight:700">
                            ACTIVE · {sig.strength:.0%} strength</span>
                    </div>
                    <div style="font-size:0.82rem; color:{C_TEXT2}; line-height:1.5">{sig.description}</div>
                </div>""",
                unsafe_allow_html=True,
            )

        for sig in upcoming:
            s_color = C_MOD
            st.markdown(
                f"""<div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-left:3px solid {s_color};
                                border-radius:10px; padding:12px 18px; margin-bottom:8px; opacity:0.75">
                    <div style="font-size:0.82rem; font-weight:600; color:{C_TEXT}">⏳ {sig.name} — in {sig.days_until}d</div>
                    <div style="font-size:0.75rem; color:{C_TEXT2}; margin-top:4px">{sig.description}</div>
                </div>""",
                unsafe_allow_html=True,
            )
    except ImportError:
        st.markdown(f"<div style='color:{C_TEXT3}; font-size:0.82rem'>Seasonal module not available.</div>", unsafe_allow_html=True)
    except Exception as e:
        st.warning(f"Seasonal patterns unavailable: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RENDER ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def render(insights: list[Insight]) -> None:

    if not insights:
        st.markdown(
            f"""<div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:12px; padding:32px; text-align:center">
                <div style="font-size:2rem; margin-bottom:12px">🔍</div>
                <div style="font-size:1rem; font-weight:600; color:{C_TEXT}; margin-bottom:8px">No insights generated yet</div>
                <div style="font-size:0.85rem; color:{C_TEXT2}">Add API credentials in .env and click Refresh All Data in the sidebar.</div>
            </div>""",
            unsafe_allow_html=True,
        )
        return

    # Pre-generate shared data
    try:
        eps_df     = _gen_eps_data(insights)
        growth_df  = _gen_revenue_earnings_growth()
        beat_df    = _gen_beat_rate()
        guidance_df = _gen_guidance()
        seg_df     = _gen_segment_revenue()
        margin_df  = _gen_margins()
        rev_df     = _gen_estimate_revisions()
    except Exception as e:
        st.error(f"Data generation error: {e}")
        eps_df = pd.DataFrame()
        growth_df = beat_df = guidance_df = seg_df = margin_df = rev_df = pd.DataFrame()

    # ── 1. Results Hero Dashboard ──────────────────────────────────────────────
    try:
        _render_results_hero(insights, eps_df)
    except Exception as e:
        st.warning(f"Hero dashboard error: {e}")

    # ── 2. Earnings Performance League Table ───────────────────────────────────
    try:
        _render_league_table(eps_df)
    except Exception as e:
        st.warning(f"League table error: {e}")

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ── 3 & 5 side by side: Scatter + Beat Rate ────────────────────────────────
    try:
        col_a, col_b = st.columns(2)
        with col_a:
            _render_rev_eps_scatter(growth_df)
        with col_b:
            _render_beat_rate(beat_df)
    except Exception as e:
        st.warning(f"Charts error: {e}")

    # ── 4. EPS Trend Chart ─────────────────────────────────────────────────────
    try:
        _render_eps_trend(insights)
    except Exception as e:
        st.warning(f"EPS trend error: {e}")

    # ── 6. Forward Guidance Tracker ────────────────────────────────────────────
    try:
        _render_guidance_tracker(guidance_df)
    except Exception as e:
        st.warning(f"Guidance tracker error: {e}")

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ── 7 & 8 side by side: Segment Revenue + Margins ─────────────────────────
    try:
        col_c, col_d = st.columns(2)
        with col_c:
            _render_segment_revenue(seg_df)
        with col_d:
            _render_margin_trends(margin_df)
    except Exception as e:
        st.warning(f"Revenue/margins error: {e}")

    # ── 9. Earnings Surprise vs Share Price ────────────────────────────────────
    try:
        _render_surprise_price_scatter(eps_df)
    except Exception as e:
        st.warning(f"Surprise scatter error: {e}")

    # ── 10. Consensus Estimate Revisions ───────────────────────────────────────
    try:
        _render_estimate_revisions(rev_df)
    except Exception as e:
        st.warning(f"Estimate revisions error: {e}")

    st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:28px 0'>", unsafe_allow_html=True)

    # ── Signal intelligence (legacy sections preserved) ────────────────────────
    try:
        _render_stale_banner(insights)
    except Exception:
        pass

    try:
        _render_signal_consensus(insights)
    except Exception:
        pass

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    try:
        st.markdown(
            f'<div style="font-size:0.72rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase;'
            f' letter-spacing:0.08em; margin-bottom:10px">Filter &amp; Sort Signals</div>',
            unsafe_allow_html=True,
        )
        filtered_insights = _render_filter_controls(insights)
    except Exception:
        filtered_insights = insights

    try:
        st.markdown(
            f'<div style="font-size:0.72rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase;'
            f' letter-spacing:0.08em; margin-bottom:12px; margin-top:4px">Signal Cards</div>',
            unsafe_allow_html=True,
        )
        for fi, ins in enumerate(filtered_insights):
            _render_rich_insight_card(ins, card_key=f"filtered_{fi}")
    except Exception:
        pass

    try:
        _render_distribution_charts(insights)
    except Exception:
        pass

    try:
        _render_export_dataframe(filtered_insights)
    except Exception:
        pass

    st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>", unsafe_allow_html=True)

    # ── KPI row ────────────────────────────────────────────────────────────────
    try:
        convergence_count = sum(1 for i in insights if i.category == "CONVERGENCE")
        high_count        = sum(1 for i in insights if i.score >= 0.70)
        stale_count       = sum(1 for i in insights if i.data_freshness_warning)
        top               = insights[0]

        def kpi(label, value, sub="", color=C_ACCENT):
            return (
                f"<div style='background:{C_CARD}; border:1px solid {C_BORDER}; border-top:3px solid {color};"
                f" border-radius:10px; padding:14px 16px; text-align:center'>"
                f"<div style='font-size:0.67rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.07em'>{label}</div>"
                f"<div style='font-size:1.7rem; font-weight:800; color:{C_TEXT}; line-height:1.1; margin:4px 0'>{value}</div>"
                + (f"<div style='font-size:0.75rem; color:{C_TEXT2}'>{sub}</div>" if sub else "")
                + "</div>"
            )

        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(kpi("Top Signal", top.action, f"{top.score:.0%} confidence", ACTION_COLORS.get(top.action, C_ACCENT)), unsafe_allow_html=True)
        c2.markdown(kpi("Total Insights", str(len(insights)), "active signals"), unsafe_allow_html=True)
        c3.markdown(kpi("Convergence", str(convergence_count), "multi-signal aligned", C_CONV), unsafe_allow_html=True)
        if stale_count:
            c4.markdown(kpi("Stale Data", str(stale_count), "sources need refresh", C_MOD), unsafe_allow_html=True)
        else:
            c4.markdown(kpi("Data Quality", "OK", "all sources fresh", C_HIGH), unsafe_allow_html=True)
    except Exception:
        pass

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── Convergence meter ──────────────────────────────────────────────────────
    try:
        _render_convergence_meter(insights)
    except Exception:
        pass

    # ── Category tabs ──────────────────────────────────────────────────────────
    try:
        cats = ["CONVERGENCE", "PORT_DEMAND", "ROUTE", "MACRO"]
        tab_labels = ["All (" + str(len(insights)) + ")"] + [
            CATEGORY_ICONS.get(c, "📌") + " "
            + c.replace("_", " ").title()
            + " (" + str(sum(1 for i in insights if i.category == c)) + ")"
            for c in cats
        ]

        all_tab, conv_tab, port_tab, route_tab, macro_tab = st.tabs(tab_labels)

        tab_map = {
            all_tab:   insights,
            conv_tab:  [i for i in insights if i.category == "CONVERGENCE"],
            port_tab:  [i for i in insights if i.category == "PORT_DEMAND"],
            route_tab: [i for i in insights if i.category == "ROUTE"],
            macro_tab: [i for i in insights if i.category == "MACRO"],
        }

        for ti, (tab_obj, tab_insights) in enumerate(tab_map.items()):
            with tab_obj:
                if not tab_insights:
                    st.markdown(
                        f"<div style='color:{C_TEXT3}; font-size:0.85rem; padding:16px 0'>No insights in this category.</div>",
                        unsafe_allow_html=True,
                    )
                    continue
                _render_hero_card(tab_insights[0])
                for j, insight in enumerate(tab_insights[1:]):
                    _render_insight_card(insight, CATEGORY_COLORS, CATEGORY_ICONS, ACTION_COLORS, card_key=f"{ti}_{j}")
                with st.expander("Insight Landscape", expanded=True, key=f"results_landscape_{ti}"):
                    _render_insight_timeline(tab_insights, chart_key=f"insight_timeline_{ti}")
    except Exception as e:
        st.warning(f"Category tabs error: {e}")

    # ── Seasonal patterns ──────────────────────────────────────────────────────
    st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>", unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-size:0.72rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase;'
        f' letter-spacing:0.08em; margin-bottom:12px">Seasonal Patterns</div>',
        unsafe_allow_html=True,
    )
    try:
        _render_seasonal(insights)
    except Exception as e:
        st.warning(f"Seasonal patterns error: {e}")
