"""
Shipping Derivatives & FFA Dashboard

Comprehensive Forward Freight Agreement (FFA) and derivatives intelligence:
  1.  Derivatives Market Header — KPI cards: open interest, volume, BDI basis, trader count
  2.  FFA Forward Curve — Plotly multi-line: spot + quarterly + Cal-year FFAs for BDI/C5TC/P5TC
  3.  FFA Quote Board — live bid/ask/spread/OI table for all active contracts
  4.  Options Pricing Table — FFA barrier options: cap/floor/straddle with full Greeks
  5.  Basis Analysis — spot vs FFA historical basis chart + opportunity highlight
  6.  Position & Hedging Strategies — carrier vs shipper hedging with worked example
  7.  Shipping Options Screen — ZIM/MATX listed options: IV, PCR, max pain, unusual activity
  8.  Volatility Surface — Plotly heatmap: IV by strike × term, shows IV smile
"""
from __future__ import annotations

import math
import random

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.options_screener import screen_options
from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    dark_layout,
)

# ── Palette ────────────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_PURPLE  = "#8b5cf6"
C_TEAL    = "#14b8a6"
C_CYAN    = "#06b6d4"

# ── Mock market data ───────────────────────────────────────────────────────────
_BDI_SPOT = 1_847

_FFA_CURVE = {
    "months":   ["Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb"],
    "bdi":      [1847, 1790, 1830, 1920, 2040, 2110, 2080, 1950, 1870, 1810, 1760, 1800],
    "c5tc":     [14800, 14200, 14600, 15100, 15800, 16200, 16000, 15300, 14700, 14200, 13900, 14100],
    "p5tc":     [10200, 9900, 10100, 10500, 11000, 11300, 11100, 10700, 10300, 10000, 9800, 9900],
}

_QUOTE_BOARD = [
    {"contract": "BDI Cal26",     "bid": 1870, "ask": 1890, "last": 1882, "chg": +12,  "oi": 4820,  "vol": 312},
    {"contract": "BDI Cal27",     "bid": 1940, "ask": 1965, "last": 1951, "chg": +5,   "oi": 2140,  "vol": 88},
    {"contract": "C5TC Q1 2026",  "bid": 14050, "ask": 14200, "last": 14130, "chg": -85, "oi": 3210, "vol": 197},
    {"contract": "C5TC Q2 2026",  "bid": 14950, "ask": 15100, "last": 15030, "chg": +120, "oi": 5640, "vol": 445},
    {"contract": "C5TC Q3 2026",  "bid": 15800, "ask": 16000, "last": 15920, "chg": +210, "oi": 4380, "vol": 360},
    {"contract": "C5TC Q4 2026",  "bid": 14100, "ask": 14350, "last": 14220, "chg": -30,  "oi": 2180, "vol": 120},
    {"contract": "C5TC Cal26",    "bid": 14980, "ask": 15150, "last": 15070, "chg": +55,  "oi": 6900, "vol": 520},
    {"contract": "P5TC Q1 2026",  "bid": 9820,  "ask": 9980,  "last": 9900,  "chg": -60,  "oi": 2450, "vol": 155},
    {"contract": "P5TC Q2 2026",  "bid": 10400, "ask": 10550, "last": 10480, "chg": +90,  "oi": 3600, "vol": 290},
    {"contract": "P5TC Q3 2026",  "bid": 11100, "ask": 11280, "last": 11190, "chg": +180, "oi": 2980, "vol": 215},
    {"contract": "P5TC Q4 2026",  "bid": 9900,  "ask": 10050, "last": 9970,  "chg": -20,  "oi": 1320, "vol": 85},
    {"contract": "S10TC Q1 2026", "bid": 7800,  "ask": 7950,  "last": 7870,  "chg": -40,  "oi": 980,  "vol": 58},
    {"contract": "S10TC Q2 2026", "bid": 8200,  "ask": 8380,  "last": 8290,  "chg": +75,  "oi": 1540, "vol": 102},
    {"contract": "S10TC Q3 2026", "bid": 8700,  "ask": 8890,  "last": 8800,  "chg": +140, "oi": 1280, "vol": 88},
    {"contract": "S10TC Q4 2026", "bid": 7900,  "ask": 8050,  "last": 7970,  "chg": -15,  "oi": 620,  "vol": 40},
]

_OPTIONS_TABLE = [
    {"contract": "BDI C5TC Q2",  "type": "CAP",     "strike": 16000, "premium": 280, "delta": 0.42, "gamma": 0.0018, "theta": -4.2, "iv": 0.38},
    {"contract": "BDI C5TC Q3",  "type": "CAP",     "strike": 17000, "premium": 195, "delta": 0.31, "gamma": 0.0012, "theta": -3.1, "iv": 0.41},
    {"contract": "BDI C5TC Q2",  "type": "FLOOR",   "strike": 13000, "premium": 215, "delta": -0.38, "gamma": 0.0015, "theta": -3.8, "iv": 0.35},
    {"contract": "BDI P5TC Q2",  "type": "CAP",     "strike": 11500, "premium": 175, "delta": 0.35, "gamma": 0.0014, "theta": -3.5, "iv": 0.43},
    {"contract": "BDI P5TC Q2",  "type": "FLOOR",   "strike": 9000,  "premium": 160, "delta": -0.33, "gamma": 0.0013, "theta": -3.2, "iv": 0.39},
    {"contract": "BDI Cal26",    "type": "STRADDLE", "strike": 1900,  "premium": 310, "delta": 0.02,  "gamma": 0.0025, "theta": -5.1, "iv": 0.45},
    {"contract": "BDI Cal26",    "type": "CAP",      "strike": 2200,  "premium": 145, "delta": 0.28,  "gamma": 0.0010, "theta": -2.8, "iv": 0.42},
    {"contract": "BDI Cal26",    "type": "FLOOR",    "strike": 1500,  "premium": 130, "delta": -0.25, "gamma": 0.0009, "theta": -2.5, "iv": 0.36},
]

_BASIS_MONTHS   = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
_BASIS_HIST     = [-42, -65, -18, +35, +82, +47]
_BASIS_AVG      = +6.5


# ── Helpers ────────────────────────────────────────────────────────────────────

def _chg_color(v: float) -> str:
    return C_HIGH if v >= 0 else C_LOW


def _chg_str(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:,.0f}"


def _kpi_card(label: str, value: str, sub: str = "", color: str = C_TEXT) -> str:
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
        f'padding:18px 20px;text-align:center;">'
        f'<div style="color:{C_TEXT3};font-size:11px;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;">{label}</div>'
        f'<div style="color:{color};font-size:24px;font-weight:700;line-height:1;">{value}</div>'
        f'<div style="color:{C_TEXT2};font-size:12px;margin-top:6px;">{sub}</div>'
        f'</div>'
    )


def _section_header(title: str, subtitle: str = "") -> None:
    sub_html = f'<div style="color:{C_TEXT2};font-size:13px;margin-top:4px;">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f'<div style="margin:28px 0 14px;">'
        f'<div style="color:{C_TEXT};font-size:18px;font-weight:700;letter-spacing:.3px;">{title}</div>'
        f'{sub_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Section 1: Header KPIs ─────────────────────────────────────────────────────

def _render_header() -> None:
    try:
        total_oi = sum(r["oi"] for r in _QUOTE_BOARD)
        total_vol = sum(r["vol"] for r in _QUOTE_BOARD)
        ffa_q2 = next(r["last"] for r in _QUOTE_BOARD if r["contract"] == "C5TC Q2 2026")
        c5_spot = 14_780
        basis = c5_spot - ffa_q2
        structure = "Backwardation" if basis > 0 else "Contango"
        struct_color = C_HIGH if basis > 0 else C_LOW

        cols = st.columns(4)
        with cols[0]:
            st.markdown(_kpi_card("FFA Open Interest", f"{total_oi:,}", "All active contracts", C_ACCENT), unsafe_allow_html=True)
        with cols[1]:
            st.markdown(_kpi_card("Daily FFA Volume", f"{total_vol:,}", "Lots traded today", C_MOD), unsafe_allow_html=True)
        with cols[2]:
            basis_str = f"{'+' if basis > 0 else ''}{basis:,.0f}"
            st.markdown(_kpi_card("C5TC Spot vs Q2 FFA", basis_str, structure, struct_color), unsafe_allow_html=True)
        with cols[3]:
            st.markdown(_kpi_card("Active FFA Traders", "148", "Cleared via Baltic Exchange", C_TEAL), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Derivatives header error: {exc}")
        st.warning("Header KPIs unavailable.")


# ── Section 2: FFA Forward Curve ───────────────────────────────────────────────

def _render_forward_curve() -> None:
    try:
        _section_header(
            "FFA Forward Curve",
            "BDI spot vs 12-month FFA prices — C5TC Capesize and P5TC Panamax overlaid",
        )

        months = _FFA_CURVE["months"]

        # Normalize C5TC and P5TC to BDI-comparable scale for overlay clarity
        # Show on dual-axis: BDI left, TC rates right
        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=months, y=_FFA_CURVE["bdi"],
            name="BDI FFA",
            line=dict(color=C_ACCENT, width=2.5),
            mode="lines+markers",
            marker=dict(size=6),
        ))

        # Spot reference line
        fig.add_hline(
            y=_BDI_SPOT,
            line=dict(color=C_HIGH, width=1.5, dash="dot"),
            annotation_text=f"BDI Spot {_BDI_SPOT:,}",
            annotation_font_color=C_HIGH,
        )

        # Quarter bands
        for q_idx, (q_label, q_color, month_range) in enumerate([
            ("Q1 2026", C_MOD,    (0, 2)),
            ("Q2 2026", C_TEAL,   (3, 5)),
            ("Q3 2026", C_PURPLE, (6, 8)),
            ("Q4 2026", C_CYAN,   (9, 11)),
        ]):
            fig.add_vrect(
                x0=months[month_range[0]], x1=months[min(month_range[1], len(months)-1)],
                fillcolor=q_color, opacity=0.04, line_width=0,
                annotation_text=q_label, annotation_position="top left",
                annotation_font_color=q_color, annotation_font_size=10,
            )

        # C5TC on secondary y
        fig.add_trace(go.Scatter(
            x=months, y=_FFA_CURVE["c5tc"],
            name="C5TC FFA ($/day)",
            line=dict(color=C_MOD, width=2, dash="dash"),
            mode="lines+markers",
            marker=dict(size=5),
            yaxis="y2",
        ))

        fig.add_trace(go.Scatter(
            x=months, y=_FFA_CURVE["p5tc"],
            name="P5TC FFA ($/day)",
            line=dict(color=C_HIGH, width=2, dash="dot"),
            mode="lines+markers",
            marker=dict(size=5),
            yaxis="y2",
        ))

        fig.update_layout(
            **dark_layout(),
            height=420,
            yaxis=dict(title="BDI Points", color=C_TEXT2, gridcolor=C_BORDER),
            yaxis2=dict(
                title="TC Rate ($/day)", overlaying="y", side="right",
                color=C_TEXT2, gridcolor="rgba(0,0,0,0)",
            ),
            xaxis=dict(color=C_TEXT2, gridcolor=C_BORDER),
            legend=dict(orientation="h", y=-0.15, font=dict(color=C_TEXT2, size=11)),
            margin=dict(l=60, r=60, t=20, b=50),
        )

        st.plotly_chart(fig, use_container_width=True)

        # Structure label
        bdi_12m_avg = sum(_FFA_CURVE["bdi"][1:]) / len(_FFA_CURVE["bdi"][1:])
        structure = "BACKWARDATION" if _BDI_SPOT > bdi_12m_avg else "CONTANGO"
        s_color = C_HIGH if structure == "BACKWARDATION" else C_LOW
        s_desc = ("Spot above forward — bullish freight market. Carriers hold pricing power." if structure == "BACKWARDATION"
                  else "Spot below forward — bearish freight market. Shippers have advantage.")
        st.markdown(
            f'<div style="background:{C_CARD};border-left:3px solid {s_color};border-radius:8px;'
            f'padding:12px 18px;margin-top:8px;display:flex;align-items:center;gap:14px;">'
            f'<span style="color:{s_color};font-size:13px;font-weight:700;">Market Structure: {structure}</span>'
            f'<span style="color:{C_TEXT2};font-size:12px;">{s_desc}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"Forward curve error: {exc}")
        st.warning("Forward curve chart unavailable.")


# ── Section 3: FFA Quote Board ─────────────────────────────────────────────────

def _render_quote_board() -> None:
    try:
        _section_header(
            "FFA Quote Board",
            "Live bid/ask quotes — Baltic Exchange cleared contracts",
        )

        header_html = (
            '<div style="overflow-x:auto;">'
            '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
            '<thead>'
            '<tr style="border-bottom:1px solid ' + C_BORDER + ';">'
        )
        for col in ["CONTRACT", "BID", "ASK", "SPREAD", "LAST", "CHANGE", "OPEN INT", "VOLUME"]:
            header_html += f'<th style="color:{C_TEXT3};font-size:10px;letter-spacing:1px;padding:8px 10px;text-align:right;font-weight:600;">{col}</th>'
        header_html += "</tr></thead><tbody>"

        rows_html = ""
        for r in _QUOTE_BOARD:
            spread = r["ask"] - r["bid"]
            chg_color = _chg_color(r["chg"])
            chg_str = _chg_str(r["chg"])
            is_alt = _QUOTE_BOARD.index(r) % 2 == 1
            row_bg = f"background:rgba(255,255,255,0.02);" if is_alt else ""
            rows_html += (
                f'<tr style="{row_bg}border-bottom:1px solid rgba(255,255,255,0.04);">'
                f'<td style="color:{C_TEXT};padding:9px 10px;font-weight:600;text-align:right;">{r["contract"]}</td>'
                f'<td style="color:{C_TEXT2};padding:9px 10px;text-align:right;">{r["bid"]:,}</td>'
                f'<td style="color:{C_TEXT2};padding:9px 10px;text-align:right;">{r["ask"]:,}</td>'
                f'<td style="color:{C_TEXT3};padding:9px 10px;text-align:right;">{spread:,}</td>'
                f'<td style="color:{C_TEXT};padding:9px 10px;text-align:right;font-weight:500;">{r["last"]:,}</td>'
                f'<td style="color:{chg_color};padding:9px 10px;text-align:right;font-weight:600;">{chg_str}</td>'
                f'<td style="color:{C_TEXT2};padding:9px 10px;text-align:right;">{r["oi"]:,}</td>'
                f'<td style="color:{C_TEXT2};padding:9px 10px;text-align:right;">{r["vol"]:,}</td>'
                f'</tr>'
            )

        table_html = (
            header_html + rows_html +
            '</tbody></table></div>'
        )
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
            f'padding:16px 20px;">{table_html}</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"Quote board error: {exc}")
        st.warning("Quote board unavailable.")


# ── Section 4: Options Pricing Table ──────────────────────────────────────────

def _render_options_table() -> None:
    try:
        _section_header(
            "FFA Options Pricing",
            "Barrier options on BDI/C5TC/P5TC — CAP (call), FLOOR (put), STRADDLE",
        )

        type_colors = {"CAP": C_HIGH, "FLOOR": C_LOW, "STRADDLE": C_ACCENT}

        header_html = (
            '<div style="overflow-x:auto;">'
            '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
            '<thead><tr style="border-bottom:1px solid ' + C_BORDER + ';">'
        )
        for col in ["CONTRACT", "TYPE", "STRIKE", "PREMIUM", "DELTA", "GAMMA", "THETA", "IV"]:
            header_html += f'<th style="color:{C_TEXT3};font-size:10px;letter-spacing:1px;padding:8px 10px;text-align:right;font-weight:600;">{col}</th>'
        header_html += "</tr></thead><tbody>"

        rows_html = ""
        for i, o in enumerate(_OPTIONS_TABLE):
            tc = type_colors.get(o["type"], C_TEXT2)
            row_bg = "background:rgba(255,255,255,0.02);" if i % 2 == 1 else ""
            rows_html += (
                f'<tr style="{row_bg}border-bottom:1px solid rgba(255,255,255,0.04);">'
                f'<td style="color:{C_TEXT};padding:9px 10px;font-weight:600;text-align:right;">{o["contract"]}</td>'
                f'<td style="color:{tc};padding:9px 10px;font-weight:700;text-align:right;">{o["type"]}</td>'
                f'<td style="color:{C_TEXT2};padding:9px 10px;text-align:right;">{o["strike"]:,}</td>'
                f'<td style="color:{C_MOD};padding:9px 10px;font-weight:600;text-align:right;">${o["premium"]:,}</td>'
                f'<td style="color:{C_TEXT2};padding:9px 10px;text-align:right;">{o["delta"]:+.2f}</td>'
                f'<td style="color:{C_TEXT2};padding:9px 10px;text-align:right;">{o["gamma"]:.4f}</td>'
                f'<td style="color:{C_LOW if o["theta"] < 0 else C_HIGH};padding:9px 10px;text-align:right;">{o["theta"]:.1f}</td>'
                f'<td style="color:{C_ACCENT};padding:9px 10px;font-weight:600;text-align:right;">{o["iv"]*100:.1f}%</td>'
                f'</tr>'
            )

        table_html = header_html + rows_html + '</tbody></table></div>'
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:16px 20px;">{table_html}</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"Options table error: {exc}")
        st.warning("Options pricing table unavailable.")


# ── Section 5: Basis Analysis ──────────────────────────────────────────────────

def _render_basis_analysis() -> None:
    try:
        _section_header(
            "Basis Analysis",
            "Historical FFA basis (spot − FFA) and trading opportunity signals",
        )

        col_chart, col_stats = st.columns([2, 1])

        with col_chart:
            bar_colors = [C_HIGH if v >= 0 else C_LOW for v in _BASIS_HIST]
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=_BASIS_MONTHS,
                y=_BASIS_HIST,
                marker_color=bar_colors,
                name="Spot−FFA Basis",
                text=[f"{v:+}" for v in _BASIS_HIST],
                textposition="outside",
                textfont=dict(color=C_TEXT2, size=10),
            ))
            fig.add_hline(
                y=_BASIS_AVG,
                line=dict(color=C_MOD, dash="dash", width=1.5),
                annotation_text=f"6M Avg: {_BASIS_AVG:+.1f}",
                annotation_font_color=C_MOD,
            )
            fig.add_hline(y=0, line=dict(color=C_TEXT3, width=1))
            fig.update_layout(
                **dark_layout(),
                height=280,
                margin=dict(l=40, r=20, t=20, b=40),
                xaxis=dict(color=C_TEXT2, gridcolor=C_BORDER),
                yaxis=dict(title="Basis (pts)", color=C_TEXT2, gridcolor=C_BORDER),
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

        with col_stats:
            current_basis = _BASIS_HIST[-1]
            bias_vs_avg = current_basis - _BASIS_AVG
            opp_color = C_HIGH if abs(bias_vs_avg) > 30 else C_MOD

            cards = [
                ("Current Basis", f"{current_basis:+} pts", C_HIGH if current_basis >= 0 else C_LOW),
                ("6M Average", f"{_BASIS_AVG:+.1f} pts", C_TEXT2),
                ("vs Average", f"{bias_vs_avg:+.1f} pts", opp_color),
                ("Max Basis", f"{max(_BASIS_HIST):+} pts", C_HIGH),
                ("Min Basis", f"{min(_BASIS_HIST):+} pts", C_LOW),
            ]
            for lbl, val, col in cards:
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;'
                    f'padding:10px 14px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;">'
                    f'<span style="color:{C_TEXT3};font-size:11px;">{lbl}</span>'
                    f'<span style="color:{col};font-size:13px;font-weight:700;">{val}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # Opportunity signal
            if abs(bias_vs_avg) > 30:
                signal = "SELL FFA" if current_basis > _BASIS_AVG + 30 else "BUY FFA"
                s_color = C_LOW if signal == "SELL FFA" else C_HIGH
                st.markdown(
                    f'<div style="background:{C_CARD};border-left:3px solid {s_color};border-radius:8px;'
                    f'padding:10px 14px;margin-top:4px;">'
                    f'<div style="color:{s_color};font-size:11px;font-weight:700;">BASIS TRADE SIGNAL</div>'
                    f'<div style="color:{C_TEXT2};font-size:12px;margin-top:4px;">{signal}: basis vs 6M avg</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        logger.warning(f"Basis analysis error: {exc}")
        st.warning("Basis analysis unavailable.")


# ── Section 6: Position & Hedging Strategies ───────────────────────────────────

def _render_hedging_strategies() -> None:
    try:
        _section_header(
            "Position & Hedging Strategies",
            "How to use FFAs to hedge physical freight exposure",
        )

        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:18px 20px;">'
                f'<div style="color:{C_HIGH};font-size:13px;font-weight:700;margin-bottom:10px;">FREIGHT RECEIVER — Carrier (Sell FFA)</div>'
                f'<div style="color:{C_TEXT2};font-size:12px;line-height:1.8;">'
                f'A carrier earning spot freight wants protection against rate <b style="color:{C_LOW};">declines</b>.<br>'
                f'<b>Strategy:</b> Sell C5TC FFA forward to lock in current rate.<br>'
                f'<b>Example:</b> Sell Q3 C5TC @ $15,920/day for 3 months.<br>'
                f'If spot falls to $12,000 — FFA profit offsets physical loss.<br>'
                f'Net locked rate: ~$15,920/day regardless of market.'
                f'</div>'
                f'<div style="margin-top:14px;padding:10px 14px;background:rgba(16,185,129,0.08);border-radius:6px;">'
                f'<span style="color:{C_HIGH};font-size:11px;font-weight:700;">P&amp;L AT EXPIRY (Q3 2026)</span><br>'
                f'<span style="color:{C_TEXT2};font-size:11px;">FFA sold @ 15,920 | Spot settles @ 13,500</span><br>'
                f'<span style="color:{C_HIGH};font-size:12px;font-weight:600;">FFA gain: $2,420/day × 92 days × vessel = +$222,640</span>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        with col_b:
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:18px 20px;">'
                f'<div style="color:{C_LOW};font-size:13px;font-weight:700;margin-bottom:10px;">FREIGHT PAYER — Shipper (Buy FFA)</div>'
                f'<div style="color:{C_TEXT2};font-size:12px;line-height:1.8;">'
                f'A shipper paying voyage freight fears rate <b style="color:{C_HIGH};">increases</b>.<br>'
                f'<b>Strategy:</b> Buy C5TC FFA to cap freight cost.<br>'
                f'<b>Example:</b> Buy Q2 C5TC @ $15,030/day for 3 months.<br>'
                f'If spot rises to $18,000 — FFA profit covers the extra cost.<br>'
                f'Max freight cost capped at ~$15,030/day.'
                f'</div>'
                f'<div style="margin-top:14px;padding:10px 14px;background:rgba(239,68,68,0.08);border-radius:6px;">'
                f'<span style="color:{C_LOW};font-size:11px;font-weight:700;">WORKED EXAMPLE: 50,000 MT/MO CAPESIZE</span><br>'
                f'<span style="color:{C_TEXT2};font-size:11px;">Route: Brazil → China | C5TC Q2 FFA: $15,030/day</span><br>'
                f'<span style="color:{C_MOD};font-size:12px;font-weight:600;">Hedge cost: ~$1.38M/quarter | Breakeven protection above $18,500/day</span>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # Spread trades
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:16px 20px;margin-top:14px;">'
            f'<div style="color:{C_ACCENT};font-size:13px;font-weight:700;margin-bottom:10px;">SPECULATIVE FFA SPREAD TRADES</div>'
            f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;">'
            f'<div style="padding:12px;background:rgba(59,130,246,0.06);border-radius:8px;">'
            f'<div style="color:{C_TEXT2};font-size:11px;font-weight:600;">Q2/Q3 CAPE SPREAD</div>'
            f'<div style="color:{C_TEXT3};font-size:11px;margin-top:4px;">Buy Q2, Sell Q3 C5TC</div>'
            f'<div style="color:{C_ACCENT};font-size:12px;font-weight:700;margin-top:6px;">Spread: −$890/day</div>'
            f'<div style="color:{C_TEXT3};font-size:10px;">Bet Q2 outperforms</div>'
            f'</div>'
            f'<div style="padding:12px;background:rgba(59,130,246,0.06);border-radius:8px;">'
            f'<div style="color:{C_TEXT2};font-size:11px;font-weight:600;">CAPE/PMAX RATIO</div>'
            f'<div style="color:{C_TEXT3};font-size:11px;margin-top:4px;">C5TC / P5TC Cal26</div>'
            f'<div style="color:{C_MOD};font-size:12px;font-weight:700;margin-top:6px;">Ratio: 1.44×</div>'
            f'<div style="color:{C_TEXT3};font-size:10px;">Hist avg 1.38× — Cape rich</div>'
            f'</div>'
            f'<div style="padding:12px;background:rgba(59,130,246,0.06);border-radius:8px;">'
            f'<div style="color:{C_TEXT2};font-size:11px;font-weight:600;">CAL26/CAL27 CURVE</div>'
            f'<div style="color:{C_TEXT3};font-size:11px;margin-top:4px;">Buy Cal27, Sell Cal26 BDI</div>'
            f'<div style="color:{C_HIGH};font-size:12px;font-weight:700;margin-top:6px;">Carry: +69 pts</div>'
            f'<div style="color:{C_TEXT3};font-size:10px;">Deferred premium intact</div>'
            f'</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"Hedging strategies error: {exc}")
        st.warning("Hedging strategies section unavailable.")


# ── Section 7: Shipping Options Screen ────────────────────────────────────────

def _render_options_screen() -> None:
    try:
        _section_header(
            "Shipping Stock Options Screen",
            "ZIM, MATX, DAC, SBLK — listed options: IV, PCR, max pain, unusual activity",
        )

        options_data = screen_options(["ZIM", "MATX", "DAC", "SBLK"])
        if not options_data:
            st.info("No options data returned from screener.")
            return

        # Summarise by ticker
        from collections import defaultdict
        ticker_summary: dict[str, dict] = defaultdict(lambda: {
            "calls": 0, "puts": 0, "total_oi": 0, "total_vol": 0,
            "iv_sum": 0.0, "iv_count": 0, "strikes": [],
        })

        for opt in options_data:
            t = opt.ticker
            ts = ticker_summary[t]
            if opt.call_put == "C":
                ts["calls"] += opt.oi
            else:
                ts["puts"] += opt.oi
            ts["total_oi"] += opt.oi
            ts["total_vol"] += opt.volume
            ts["iv_sum"] += opt.iv
            ts["iv_count"] += 1
            ts["strikes"].append((opt.strike, opt.oi, opt.call_put))

        cols = st.columns(4)
        tickers_order = ["ZIM", "MATX", "DAC", "SBLK"]
        prices = {"ZIM": 14.50, "MATX": 94.00, "DAC": 68.00, "SBLK": 18.50}

        for i, ticker in enumerate(tickers_order):
            ts = ticker_summary.get(ticker)
            if not ts or ts["iv_count"] == 0:
                continue
            avg_iv = ts["iv_sum"] / ts["iv_count"]
            pcr = ts["puts"] / max(ts["calls"], 1)
            pcr_color = C_LOW if pcr > 1.2 else (C_HIGH if pcr < 0.8 else C_MOD)

            # Max pain: strike with highest total OI
            strike_oi: dict[float, int] = defaultdict(int)
            for strike, oi, _ in ts["strikes"]:
                strike_oi[strike] += oi
            max_pain_strike = max(strike_oi, key=lambda k: strike_oi[k]) if strike_oi else prices[ticker]

            unusual = ts["total_vol"] > ts["total_oi"] * 0.15
            unusual_badge = (
                f'<div style="color:{C_MOD};font-size:10px;font-weight:700;margin-top:6px;">UNUSUAL ACTIVITY</div>'
                if unusual else ""
            )

            with cols[i]:
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:14px 16px;">'
                    f'<div style="color:{C_TEXT};font-size:16px;font-weight:800;">{ticker}</div>'
                    f'<div style="color:{C_TEXT3};font-size:10px;margin-bottom:10px;">${prices[ticker]:.2f} underlying</div>'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                    f'<span style="color:{C_TEXT3};font-size:11px;">Avg IV</span>'
                    f'<span style="color:{C_ACCENT};font-size:12px;font-weight:700;">{avg_iv*100:.1f}%</span>'
                    f'</div>'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                    f'<span style="color:{C_TEXT3};font-size:11px;">PCR</span>'
                    f'<span style="color:{pcr_color};font-size:12px;font-weight:700;">{pcr:.2f}</span>'
                    f'</div>'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                    f'<span style="color:{C_TEXT3};font-size:11px;">Max Pain</span>'
                    f'<span style="color:{C_TEXT2};font-size:12px;font-weight:600;">${max_pain_strike:.2f}</span>'
                    f'</div>'
                    f'<div style="display:flex;justify-content:space-between;">'
                    f'<span style="color:{C_TEXT3};font-size:11px;">Total OI</span>'
                    f'<span style="color:{C_TEXT2};font-size:12px;">{ts["total_oi"]:,}</span>'
                    f'</div>'
                    f'{unusual_badge}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    except Exception as exc:
        logger.warning(f"Options screen error: {exc}")
        st.warning("Options screener unavailable.")


# ── Section 8: Volatility Surface ─────────────────────────────────────────────

def _render_vol_surface() -> None:
    try:
        _section_header(
            "FFA Implied Volatility Surface",
            "IV by strike × term — shows vol smile and term structure",
        )

        strikes_pct = [-20, -15, -10, -5, 0, +5, +10, +15, +20]
        terms = ["1M", "2M", "3M", "6M", "9M", "12M"]

        # Realistic IV smile: higher IV at tails, decreasing with term (term structure flattens)
        base_iv = 0.38
        surface = []
        for t_idx, term in enumerate(terms):
            term_factor = 1.0 - t_idx * 0.03
            row = []
            for s_pct in strikes_pct:
                smile = 0.08 * (s_pct / 20) ** 2 + 0.02 * abs(s_pct / 20)
                skew = -0.015 * (s_pct / 20)   # slight put skew
                iv = round((base_iv + smile + skew) * term_factor, 4)
                row.append(round(iv * 100, 2))
            surface.append(row)

        strike_labels = [f"{s:+d}%" for s in strikes_pct]

        fig = go.Figure(data=go.Heatmap(
            z=surface,
            x=strike_labels,
            y=terms,
            colorscale=[
                [0.0,  "#0a0f1a"],
                [0.25, C_ACCENT],
                [0.55, C_MOD],
                [0.85, C_LOW],
                [1.0,  "#fef3c7"],
            ],
            text=[[f"{v:.1f}%" for v in row] for row in surface],
            texttemplate="%{text}",
            textfont=dict(size=10, color="white"),
            colorbar=dict(
                title=dict(text="IV (%)", font=dict(color=C_TEXT2, size=11)),
                tickfont=dict(color=C_TEXT2, size=10),
            ),
            hovertemplate="Strike: %{x}<br>Term: %{y}<br>IV: %{z:.1f}%<extra></extra>",
        ))

        fig.update_layout(
            **dark_layout(),
            height=320,
            margin=dict(l=60, r=20, t=20, b=40),
            xaxis=dict(title="Strike (% OTM/ITM)", color=C_TEXT2, gridcolor=C_BORDER),
            yaxis=dict(title="Expiry", color=C_TEXT2, gridcolor=C_BORDER),
        )

        col_heat, col_notes = st.columns([3, 1])
        with col_heat:
            st.plotly_chart(fig, use_container_width=True)
        with col_notes:
            notes = [
                ("Vol Smile", "Higher IV at OTM strikes — market prices tail risk", C_MOD),
                ("Put Skew", "Puts carry slight premium over calls (downside hedging demand)", C_LOW),
                ("Term Structure", "Near-term IV elevated — uncertainty compresses at 12M", C_ACCENT),
                ("ATM IV (3M)", f"{surface[2][4]:.1f}% — near 12M low of {min(surface[-1]):.1f}%", C_TEXT2),
            ]
            for title, body, color in notes:
                st.markdown(
                    f'<div style="background:{C_CARD};border-left:3px solid {color};border-radius:8px;'
                    f'padding:10px 12px;margin-bottom:8px;">'
                    f'<div style="color:{color};font-size:11px;font-weight:700;">{title}</div>'
                    f'<div style="color:{C_TEXT3};font-size:11px;margin-top:3px;line-height:1.5;">{body}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    except Exception as exc:
        logger.warning(f"Vol surface error: {exc}")
        st.warning("Volatility surface unavailable.")


# ── Main render ────────────────────────────────────────────────────────────────

def render(stock_data=None, macro_data=None, freight_data=None) -> None:
    """Shipping Derivatives & FFA Dashboard."""
    try:
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{C_CARD} 0%,rgba(59,130,246,0.08) 100%);'
            f'border:1px solid {C_BORDER};border-radius:12px;padding:22px 28px;margin-bottom:22px;">'
            f'<div style="color:{C_TEXT};font-size:22px;font-weight:800;letter-spacing:.4px;">Freight Derivatives Desk</div>'
            f'<div style="color:{C_TEXT2};font-size:13px;margin-top:6px;">'
            f'FFA forward curves · Quote board · Options Greeks · Basis analysis · Hedging strategies · Vol surface'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"Derivatives banner error: {exc}")

    _render_header()
    _render_forward_curve()
    _render_quote_board()
    _render_options_table()
    _render_basis_analysis()
    _render_hedging_strategies()
    _render_options_screen()
    _render_vol_surface()
