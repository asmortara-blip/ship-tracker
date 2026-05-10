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

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.options_screener import screen_options
from ui.styles import (
    C_ACCENT,
    C_CONV,
    C_HIGH,
    C_LOW,
    C_MACRO,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    insight_card_html,
    metric_card_row,
    page_header,
    section_header,
    source_footer,
    wsj_market_table,
)

# Tab-local accent — emerald teal used to highlight Q2 FFA bands & trader-count KPI.
# Not part of the shared palette (intentionally distinct from C_HIGH market green).
C_TEAL = "#14b8a6"


# ── Cell formatters for wsj_market_table() ────────────────────────────────
# wsj_market_table renders cell strings as raw HTML inside <td>. These helpers
# only style content (font + conditional color); table CSS handles alignment
# and rule lines. Mirrors the pattern in ui/tab_results.py.

def _mono(value: str, color: str = C_TEXT) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


# ── Provenance — every section in this tab is mock/demo until live FFA feed lands ──
_DERIV_SOURCES = [
    {"name": "Baltic Exchange FFA quotes (mock)",   "kind": "demo", "quality": "demo"},
    {"name": "Internal options pricing (modeled)",  "kind": "modeled", "quality": "demo"},
]

_OPTIONS_SCREEN_SOURCES = [
    {"name": "Listed equity options screener", "kind": "modeled", "quality": "demo"},
]


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
        basis_str = f"{'+' if basis > 0 else ''}{basis:,.0f}"

        metric_card_row(
            [
                {"label": "FFA Open Interest", "value": f"{total_oi:,}",
                 "accent": C_ACCENT, "sublabel": "All active contracts"},
                {"label": "Daily FFA Volume", "value": f"{total_vol:,}",
                 "accent": C_MOD, "sublabel": "Lots traded today"},
                {"label": "C5TC Spot vs Q2 FFA", "value": basis_str,
                 "accent": struct_color, "sublabel": structure},
                {"label": "Active FFA Traders", "value": "148",
                 "accent": C_TEAL, "sublabel": "Cleared via Baltic Exchange"},
            ],
            columns=4,
        )
    except Exception as exc:
        logger.warning(f"Derivatives header error: {exc}")
        st.warning("Header KPIs unavailable.")


# ── Section 2: FFA Forward Curve ───────────────────────────────────────────────

def _render_forward_curve() -> None:
    try:
        section_header(
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
        for q_label, q_color, month_range in [
            ("Q1 2026", C_MOD,   (0, 2)),
            ("Q2 2026", C_TEAL,  (3, 5)),
            ("Q3 2026", C_CONV,  (6, 8)),
            ("Q4 2026", C_MACRO, (9, 11)),
        ]:
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

        apply_dark_layout(fig, height=420)
        fig.update_layout(
            yaxis=dict(title="BDI Points"),
            yaxis2=dict(
                title="TC Rate ($/day)", overlaying="y", side="right",
                color=C_TEXT2, gridcolor="rgba(0,0,0,0)",
            ),
            legend=dict(orientation="h", y=-0.15, font=dict(color=C_TEXT2, size=11)),
            margin=dict(l=60, r=60, t=20, b=50),
        )

        st.plotly_chart(fig, use_container_width=True)

        # Structure label — replaced with insight card
        bdi_12m_avg = sum(_FFA_CURVE["bdi"][1:]) / len(_FFA_CURVE["bdi"][1:])
        structure = "BACKWARDATION" if _BDI_SPOT > bdi_12m_avg else "CONTANGO"
        s_action = "Prioritize" if structure == "BACKWARDATION" else "Caution"
        s_desc = (
            "Spot above forward — bullish freight market. Carriers hold pricing power."
            if structure == "BACKWARDATION"
            else "Spot below forward — bearish freight market. Shippers have advantage."
        )
        st.markdown(
            insight_card_html(
                title=f"Market Structure: {structure}",
                score=0.75 if structure == "BACKWARDATION" else 0.35,
                action=s_action,
                rationale=s_desc,
                category="MACRO",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(source_footer(_DERIV_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Forward curve error: {exc}")
        st.warning("Forward curve chart unavailable.")


# ── Section 3: FFA Quote Board ─────────────────────────────────────────────────

def _render_quote_board() -> None:
    try:
        section_header(
            "FFA Quote Board",
            "Live bid/ask quotes — Baltic Exchange cleared contracts",
        )

        rows = []
        for r in _QUOTE_BOARD:
            spread = r["ask"] - r["bid"]
            chg_color = _chg_color(r["chg"])
            chg_str = _chg_str(r["chg"])
            rows.append([
                _sans(r["contract"], color=C_TEXT, weight=700),
                _mono(f"{r['bid']:,}"),
                _mono(f"{r['ask']:,}"),
                _mono(f"{spread:,}", color=C_TEXT3),
                _mono(f"{r['last']:,}", color=C_TEXT),
                _mono(chg_str, color=chg_color),
                _mono(f"{r['oi']:,}"),
                _mono(f"{r['vol']:,}"),
            ])

        wsj_market_table(
            ["Contract", "Bid", "Ask", "Spread", "Last", "Change", "Open Int", "Volume"],
            rows,
        )
        st.markdown(source_footer(_DERIV_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Quote board error: {exc}")
        st.warning("Quote board unavailable.")


# ── Section 4: Options Pricing Table ──────────────────────────────────────────

def _render_options_table() -> None:
    try:
        section_header(
            "FFA Options Pricing",
            "Barrier options on BDI/C5TC/P5TC — CAP (call), FLOOR (put), STRADDLE",
        )

        type_colors = {"CAP": C_HIGH, "FLOOR": C_LOW, "STRADDLE": C_ACCENT}

        rows = []
        for o in _OPTIONS_TABLE:
            tc = type_colors.get(o["type"], C_TEXT2)
            theta_color = C_LOW if o["theta"] < 0 else C_HIGH
            rows.append([
                _sans(o["contract"], color=C_TEXT, weight=700),
                _sans(o["type"], color=tc, weight=700),
                _mono(f"{o['strike']:,}"),
                _mono(f"${o['premium']:,}", color=C_MOD),
                _mono(f"{o['delta']:+.2f}"),
                _mono(f"{o['gamma']:.4f}"),
                _mono(f"{o['theta']:.1f}", color=theta_color),
                _mono(f"{o['iv']*100:.1f}%", color=C_ACCENT),
            ])

        wsj_market_table(
            ["Contract", "Type", "Strike", "Premium", "Delta", "Gamma", "Theta", "IV"],
            rows,
        )
        st.markdown(source_footer(_DERIV_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Options table error: {exc}")
        st.warning("Options pricing table unavailable.")


# ── Section 5: Basis Analysis ──────────────────────────────────────────────────

def _render_basis_analysis() -> None:
    try:
        section_header(
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
            apply_dark_layout(fig, height=280, showlegend=False)
            fig.update_layout(
                margin=dict(l=40, r=20, t=20, b=40),
                yaxis=dict(title="Basis (pts)"),
            )
            st.plotly_chart(fig, use_container_width=True)

        with col_stats:
            current_basis = _BASIS_HIST[-1]
            bias_vs_avg = current_basis - _BASIS_AVG
            opp_color = C_HIGH if abs(bias_vs_avg) > 30 else C_MOD

            basis_cards = [
                {"label": "Current Basis", "value": f"{current_basis:+} pts",
                 "accent": C_HIGH if current_basis >= 0 else C_LOW},
                {"label": "6M Average", "value": f"{_BASIS_AVG:+.1f} pts",
                 "accent": C_TEXT2},
                {"label": "vs Average", "value": f"{bias_vs_avg:+.1f} pts",
                 "accent": opp_color},
                {"label": "Max Basis", "value": f"{max(_BASIS_HIST):+} pts",
                 "accent": C_HIGH},
                {"label": "Min Basis", "value": f"{min(_BASIS_HIST):+} pts",
                 "accent": C_LOW},
            ]
            # Stack vertically inside the narrow right column — one card per row.
            for card in basis_cards:
                metric_card_row([card], columns=1)

            # Opportunity signal
            if abs(bias_vs_avg) > 30:
                signal = "SELL FFA" if current_basis > _BASIS_AVG + 30 else "BUY FFA"
                action = "Avoid" if signal == "SELL FFA" else "Prioritize"
                st.markdown(
                    insight_card_html(
                        title=f"Basis Trade Signal: {signal}",
                        score=min(abs(bias_vs_avg) / 100, 0.95),
                        action=action,
                        rationale=f"{signal}: basis vs 6M avg is {bias_vs_avg:+.1f} pts.",
                        category="MACRO",
                    ),
                    unsafe_allow_html=True,
                )
        st.markdown(source_footer(_DERIV_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Basis analysis error: {exc}")
        st.warning("Basis analysis unavailable.")


# ── Section 6: Position & Hedging Strategies ───────────────────────────────────

def _render_hedging_strategies() -> None:
    try:
        section_header(
            "Position & Hedging Strategies",
            "How to use FFAs to hedge physical freight exposure",
        )

        col_a, col_b = st.columns(2)

        with col_a:
            carrier_rationale = (
                "A carrier earning spot freight wants protection against rate declines. "
                "Strategy: sell C5TC FFA forward to lock in current rate. "
                "Example: sell Q3 C5TC @ $15,920/day for 3 months — if spot falls to $12,000, "
                "the FFA profit offsets the physical loss. Net locked rate: ~$15,920/day. "
                "P&L worked example (Q3 2026): FFA sold @ 15,920 vs spot settles @ 13,500 → "
                "FFA gain of $2,420/day × 92 days × vessel = +$222,640."
            )
            st.markdown(
                insight_card_html(
                    title="Freight Receiver — Carrier (Sell FFA)",
                    score=0.78,
                    action="Prioritize",
                    rationale=carrier_rationale,
                    category="ROUTE",
                ),
                unsafe_allow_html=True,
            )

        with col_b:
            shipper_rationale = (
                "A shipper paying voyage freight fears rate increases. "
                "Strategy: buy C5TC FFA to cap freight cost. "
                "Example: buy Q2 C5TC @ $15,030/day for 3 months — if spot rises to $18,000, "
                "the FFA profit covers the extra cost. Max freight cost capped at ~$15,030/day. "
                "Worked example (50,000 MT/MO Capesize, Brazil → China): hedge cost ~$1.38M/quarter, "
                "breakeven protection above $18,500/day."
            )
            st.markdown(
                insight_card_html(
                    title="Freight Payer — Shipper (Buy FFA)",
                    score=0.72,
                    action="Monitor",
                    rationale=shipper_rationale,
                    category="ROUTE",
                ),
                unsafe_allow_html=True,
            )

        # Spread trades — three KPI cards
        section_header(
            "Speculative FFA Spread Trades",
            "Curve and ratio plays across the dry bulk forward complex",
        )
        metric_card_row(
            [
                {"label": "Q2/Q3 Cape Spread", "value": "−$890/day",
                 "accent": C_ACCENT, "sublabel": "Buy Q2, Sell Q3 — bet Q2 outperforms"},
                {"label": "Cape/Pmax Ratio", "value": "1.44×",
                 "accent": C_MOD, "sublabel": "C5TC / P5TC Cal26 — hist avg 1.38× (Cape rich)"},
                {"label": "Cal26/Cal27 Carry", "value": "+69 pts",
                 "accent": C_HIGH, "sublabel": "Buy Cal27, Sell Cal26 BDI — deferred premium intact"},
            ],
            columns=3,
        )
        st.markdown(source_footer(_DERIV_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Hedging strategies error: {exc}")
        st.warning("Hedging strategies section unavailable.")


# ── Section 7: Shipping Options Screen ────────────────────────────────────────

def _render_options_screen() -> None:
    try:
        section_header(
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

        tickers_order = ["ZIM", "MATX", "DAC", "SBLK"]
        prices = {"ZIM": 14.50, "MATX": 94.00, "DAC": 68.00, "SBLK": 18.50}

        rows = []
        for ticker in tickers_order:
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
            activity_label = "UNUSUAL" if unusual else "NORMAL"
            activity_color = C_MOD if unusual else C_TEXT3

            rows.append([
                _sans(ticker, color=C_TEXT, weight=800),
                _mono(f"${prices[ticker]:.2f}"),
                _mono(f"{avg_iv*100:.1f}%", color=C_ACCENT),
                _mono(f"{pcr:.2f}", color=pcr_color),
                _mono(f"${max_pain_strike:.2f}"),
                _mono(f"{ts['total_oi']:,}"),
                _sans(activity_label, color=activity_color, weight=700),
            ])

        if rows:
            wsj_market_table(
                ["Ticker", "Underlying", "Avg IV", "PCR", "Max Pain", "Total OI", "Activity"],
                rows,
            )
            st.markdown(source_footer(_OPTIONS_SCREEN_SOURCES), unsafe_allow_html=True)
        else:
            st.info("Options screen returned no qualifying tickers.")

    except Exception as exc:
        logger.warning(f"Options screen error: {exc}")
        st.warning("Options screener unavailable.")


# ── Section 8: Volatility Surface ─────────────────────────────────────────────

def _render_vol_surface() -> None:
    try:
        section_header(
            "FFA Implied Volatility Surface",
            "IV by strike × term — shows vol smile and term structure",
        )

        strikes_pct = [-20, -15, -10, -5, 0, +5, +10, +15, +20]
        terms = ["1M", "2M", "3M", "6M", "9M", "12M"]

        # Realistic IV smile: higher IV at tails, decreasing with term (term structure flattens)
        base_iv = 0.38
        surface = []
        for t_idx, _term in enumerate(terms):
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
                [0.0,  "#0c0e14"],
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

        apply_dark_layout(fig, height=320)
        fig.update_layout(
            margin=dict(l=60, r=20, t=20, b=40),
            xaxis=dict(title="Strike (% OTM/ITM)"),
            yaxis=dict(title="Expiry"),
        )

        col_heat, col_notes = st.columns([3, 1])
        with col_heat:
            st.plotly_chart(fig, use_container_width=True)
        with col_notes:
            atm_3m = surface[2][4]
            min_12m = min(surface[-1])
            notes = [
                ("Vol Smile",      0.65, "Monitor",    "Higher IV at OTM strikes — market prices tail risk"),
                ("Put Skew",       0.55, "Caution",    "Puts carry slight premium over calls (downside hedging demand)"),
                ("Term Structure", 0.70, "Prioritize", "Near-term IV elevated — uncertainty compresses at 12M"),
                ("ATM IV (3M)",    0.50, "Monitor",    f"{atm_3m:.1f}% — near 12M low of {min_12m:.1f}%"),
            ]
            for title, score, action, body in notes:
                st.markdown(
                    insight_card_html(
                        title=title,
                        score=score,
                        action=action,
                        rationale=body,
                        category="MACRO",
                    ),
                    unsafe_allow_html=True,
                )
        st.markdown(source_footer(_DERIV_SOURCES), unsafe_allow_html=True)

    except Exception as exc:
        logger.warning(f"Vol surface error: {exc}")
        st.warning("Volatility surface unavailable.")


# ── Main render ────────────────────────────────────────────────────────────────

def render(stock_data=None, macro_data=None, freight_data=None) -> None:
    """Shipping Derivatives & FFA Dashboard."""
    try:
        page_header(
            title="Freight Derivatives Desk",
            subtitle="FFA forward curves · Quote board · Options Greeks · Basis analysis · Hedging strategies · Vol surface",
            badge_text="DERIVATIVES",
            badge_color=C_ACCENT,
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
