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

from data.quality import DataSource
from processing.freight_forward_curve import (
    build_forward_curve,
    orderbook_pressure_from_fleet,
)
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
    live_data_badge,
    metric_card_row,
    page_header,
    section_divider,
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


# ── Provenance ────────────────────────────────────────────────────────────────
# R042: the Forward Curve section is now a MODELED term structure built from
# REAL spot + momentum + the fleet orderbook (see processing.freight_forward_curve)
# — its provenance is stamped MODELED by the engine, not demo. The remaining
# sections below (quote board, options, basis, vol surface) are still mock/demo
# placeholders until a live Baltic FFA feed lands.
_DERIV_SOURCES = [
    {"name": "Baltic Exchange FFA quotes (mock)",   "kind": "demo", "quality": "demo"},
    {"name": "Internal options pricing (modeled)",  "kind": "modeled", "quality": "demo"},
]

_OPTIONS_SCREEN_SOURCES = [
    {"name": "Listed equity options screener", "kind": "modeled", "quality": "demo"},
]

# Demo fallback for the modeled curve when no live spot is reachable (kept ONLY
# as an explicitly-labelled demo anchor, never presented as a live FFA quote).
_DEMO_SPOT_BDI: float = 1_847.0
_DEMO_CURVE_SOURCE = DataSource(
    name="Modeled FFA term structure (DEMO anchor — no live spot)",
    kind="demo",
    quality="demo",
    notes="No live BDI/freight spot reachable — curve modeled off a demo "
          "anchor (1,847). Illustrative shape only; not a live FFA quote.",
)


# ── Mock market data (quote board / options / basis — still demo) ──────────────
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


# ── Section 2: FFA Forward Curve (MODELED term structure) ──────────────────────
# R042: replaces the frozen `_FFA_CURVE` mock. The front is anchored on the REAL
# BDI spot (FRED BSXRLM); the shape is a MODELED map from real spot momentum
# (mean-reversion → backwardation/contango) and the real fleet orderbook delivery
# schedule (incoming supply → contango). It is a fair-value CROSS-CHECK, clearly
# labelled MODELED — never presented as a live Baltic FFA print.

_CURVE_TENORS = (1, 3, 6, 12)  # months


def _resolve_curve_inputs(macro_data, freight_data):
    """Derive (spot, momentum, orderbook_pressure, is_real) for the curve.

    Spot + momentum come from REAL BDI (FRED BSXRLM) when reachable, else a
    documented demo anchor. Orderbook pressure always comes from the fleet
    snapshot (a real Clarksons/Alphaliner baseline). Never raises.
    """
    spot = None
    momentum = 0.5
    is_real = False
    try:
        from data.fred_feed import get_bdi, compute_bdi_score
        if macro_data:
            bdi_df = get_bdi(macro_data)
            if bdi_df is not None and not bdi_df.empty:
                vals = bdi_df["value"].dropna() if "value" in bdi_df.columns else None
                if vals is not None and not vals.empty:
                    spot = float(vals.iloc[-1])
                    momentum = float(compute_bdi_score(macro_data))
                    is_real = spot > 0
    except Exception as exc:  # noqa: BLE001 — provenance falls back to demo
        logger.debug(f"BDI spot/momentum unavailable, using demo anchor: {exc}")

    if not is_real:
        spot = _DEMO_SPOT_BDI
        momentum = 0.5

    # Orderbook → supply-pressure scalar from the fleet snapshot.
    pressure = 0.0
    try:
        from processing.fleet_tracker import get_fleet_data
        fleet = get_fleet_data()
        pressure = orderbook_pressure_from_fleet(
            fleet.deliveries_next_12m_teu_m, fleet.total_teu_capacity_m,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Fleet orderbook unavailable, supply pressure 0: {exc}")

    return spot, momentum, pressure, is_real


def _render_forward_curve(macro_data=None, freight_data=None) -> None:
    try:
        section_header(
            "FFA Forward Curve",
            "MODELED BDI term structure — real spot + momentum + fleet orderbook. "
            "A fair-value cross-check, NOT a live FFA quote.",
        )

        spot, momentum, pressure, is_real = _resolve_curve_inputs(macro_data, freight_data)
        curve = build_forward_curve(spot, momentum, pressure, tenors=_CURVE_TENORS)

        # When there is no live BDI spot, ``_resolve_curve_inputs`` falls back to
        # a fixed DEMO anchor (``_DEMO_SPOT_BDI``), so the curve below is always a
        # labeled illustrative DEMO — not an empty state. Say so explicitly (the
        # old "no spot anchor" empty-state box was unreachable dead code, since
        # the demo anchor guarantees a valid positive spot — F5).
        if not is_real:
            st.info(
                "No live BDI/freight spot — showing an ILLUSTRATIVE DEMO curve "
                f"anchored on a fixed demo spot ({curve.spot:,.0f}), neutral "
                "momentum, and the real fleet orderbook. Connect the macro feed "
                "(FRED BSXRLM) to anchor the term structure on a live spot."
            )

        # Provenance pill: MODELED off real spot, or the demo anchor.
        curve_src = curve.source if is_real else _DEMO_CURVE_SOURCE
        anchor_note = (
            f"real BDI spot {curve.spot:,.0f} · momentum {curve.momentum:.2f} · "
            f"orderbook pressure {curve.orderbook_pressure:.2f}"
            if is_real else
            f"demo anchor {curve.spot:,.0f} · neutral momentum · "
            f"orderbook pressure {curve.orderbook_pressure:.2f}"
        )
        st.markdown(
            live_data_badge(curve_src)
            + f'<span style="margin-left:8px;color:{C_TEXT3};font-family:var(--mono);'
              f'font-size:0.72rem;">{anchor_note}</span>',
            unsafe_allow_html=True,
        )

        # ── Plot: modeled forward curve vs the real spot reference line ────────
        x_labels = [f"{t}M" for t in curve.tenors_months]
        line_color = C_HIGH if curve.shape == "BACKWARDATION" else (
            C_LOW if curve.shape == "CONTANGO" else C_ACCENT
        )

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=x_labels, y=list(curve.forwards),
            name="Modeled BDI forward",
            line=dict(color=C_ACCENT, width=2.5),
            mode="lines+markers",
            marker=dict(size=8, color=line_color),
            hovertemplate="Tenor %{x}<br>Modeled fwd %{y:,.0f}<extra></extra>",
        ))
        fig.add_hline(
            y=curve.spot,
            line=dict(color=C_HIGH, width=1.5, dash="dot"),
            annotation_text=f"BDI Spot {curve.spot:,.0f}",
            annotation_font_color=C_HIGH,
        )
        apply_dark_layout(fig, height=380, showlegend=False)
        fig.update_layout(
            yaxis=dict(title="BDI Points (modeled)"),
            xaxis=dict(title="Forward tenor"),
            margin=dict(l=60, r=30, t=20, b=50),
        )
        st.plotly_chart(fig, use_container_width=True)

        # ── Carry KPIs: shape · basis · roll yield ────────────────────────────
        shape_color = C_HIGH if curve.shape == "BACKWARDATION" else (
            C_LOW if curve.shape == "CONTANGO" else C_TEXT2
        )
        basis_color = C_HIGH if curve.basis > 0 else (C_LOW if curve.basis < 0 else C_TEXT2)
        roll_color = C_HIGH if curve.roll_yield > 0 else (C_LOW if curve.roll_yield < 0 else C_TEXT2)
        metric_card_row(
            [
                {"label": "Term Structure", "value": curve.shape,
                 "accent": shape_color, "sublabel": "Modeled from momentum + orderbook"},
                {"label": "Basis (Spot − 1M Fwd)",
                 "value": f"{'+' if curve.basis >= 0 else ''}{curve.basis:,.0f} pts",
                 "accent": basis_color, "sublabel": "Spot rich vs nearest forward" if curve.basis > 0
                 else ("Spot cheap vs nearest forward" if curve.basis < 0 else "At parity")},
                {"label": "Roll Yield (annualized)",
                 "value": f"{curve.roll_yield * 100:+.1f}%",
                 "accent": roll_color, "sublabel": "Carry rolling the front toward spot"},
            ],
            columns=3,
        )

        # ── Tradeable read: fade on a sharp-rally backwardation ───────────────
        if curve.shape == "BACKWARDATION":
            if curve.fade_signal:
                title, score, action = "Backwardation — Fade the Spot Rally", 0.72, "Caution"
                desc = (
                    "Modeled curve is backwardated off a sharp spot rally: forwards "
                    f"sit below spot ({curve.basis:+,.0f} pts basis). Mean-reversion read "
                    "— a long-SPOT holder rolls DOWN the curve (negative carry). Fade / "
                    "favour selling forward freight. MODELED cross-check, not a live FFA quote."
                )
            else:
                title, score, action = "Backwardation — Spot Above Forwards", 0.60, "Monitor"
                desc = (
                    f"Forwards below spot ({curve.basis:+,.0f} pts basis), bullish near-term "
                    "freight but rally not extreme. Carriers hold pricing power on the front."
                )
        elif curve.shape == "CONTANGO":
            title, score, action = "Contango — Forwards Above Spot", 0.40, "Caution"
            desc = (
                f"Modeled curve in contango ({curve.basis:+,.0f} pts basis): weak spot and/or "
                "a heavy fleet orderbook lift the deferred tenors above spot. Bearish — "
                "incoming supply caps how high spot can hold. Favours buyers / shippers."
            )
        else:
            title, score, action = "Flat Term Structure", 0.50, "Monitor"
            desc = (
                "Momentum and orderbook supply roughly offset — the modeled curve is flat. "
                "No directional carry edge; the spike (if any) is expected to be met by deliveries."
            )

        st.markdown(
            insight_card_html(
                title=f"Market Structure: {title}",
                score=score, action=action, rationale=desc, category="MACRO",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(source_footer([curve_src]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Forward curve error: {exc}")
        st.warning("Forward curve chart unavailable.")


# ── Section 3: FFA Quote Board ─────────────────────────────────────────────────

def _render_quote_board() -> None:
    try:
        section_header(
            "FFA Quote Board",
            "Illustrative bid/ask quotes (mock) — Baltic Exchange cleared "
            "contracts; sample data, not a live feed",
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

def render(route_results=None, freight_data=None, macro_data=None,
           *args, **kwargs) -> None:
    """Shipping Derivatives & FFA Dashboard.

    Called positionally from app.py as
    ``render(route_results, freight_data, macro_data)``. The Forward Curve
    section consumes the REAL ``macro_data`` (FRED BDI) + ``freight_data`` to
    build a MODELED term structure; other sections are still demo placeholders.
    """
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render

    with track_render('derivatives'):
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

        section_divider("Forward Market")
        _render_forward_curve(macro_data=macro_data, freight_data=freight_data)
        _render_quote_board()

        section_divider("Options Pricing")
        _render_options_table()

        section_divider("Basis & Hedging")
        _render_basis_analysis()
        _render_hedging_strategies()

        section_divider("Equity Options")
        _render_options_screen()
        _render_vol_surface()
