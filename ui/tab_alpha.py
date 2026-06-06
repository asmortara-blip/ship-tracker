"""Alpha Signal tab — sophisticated alpha signal generation and display dashboard."""
from __future__ import annotations

import datetime
from typing import Any

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from engine.alpha_engine import DISCLAIMER, generate_all_signals
from utils.helpers import stable_hash
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
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# ---------------------------------------------------------------------------
# Local color aliases (domain-specific direction semantics)
# ---------------------------------------------------------------------------

C_LONG    = C_HIGH       # "#2e9e6e"
C_SHORT   = C_LOW        # "#c0392b"
C_NEUTRAL = C_TEXT2      # "#9a968e"
C_PURPLE  = C_CONV       # muted purple from shared palette
C_CYAN    = C_MACRO      # teal from shared palette


# ---------------------------------------------------------------------------
# Cell formatters for wsj_market_table()
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Static display scaffolding (categories + conviction-matrix framework only).
# The signal book itself is built ONLY from real engine output — there is no
# fabricated signal log here (the former _MOCK_SIGNALS was removed: it
# masqueraded fake tickers/prices/ages as live signals).
# ---------------------------------------------------------------------------

_CATEGORIES = ["Container Ships", "Dry Bulk", "Tankers", "LNG", "Port Operators", "Mixed"]
_SIG_TYPES  = ["Momentum", "Mean Reversion", "BDI Divergence", "Macro Overlay", "Sentiment"]

# Conviction matrix: category × signal_type → (label, color)
_MATRIX_DATA = {
    ("Container Ships", "Momentum"):      ("HIGH", C_HIGH),
    ("Container Ships", "Mean Reversion"):("MOD",  C_HIGH),
    ("Container Ships", "BDI Divergence"):("HIGH", C_HIGH),
    ("Container Ships", "Macro Overlay"): ("MOD",  C_HIGH),
    ("Container Ships", "Sentiment"):     ("MOD",  C_HIGH),
    ("Dry Bulk",        "Momentum"):      ("HIGH", C_HIGH),
    ("Dry Bulk",        "Mean Reversion"):("LOW",  C_LOW),
    ("Dry Bulk",        "BDI Divergence"):("HIGH", C_HIGH),
    ("Dry Bulk",        "Macro Overlay"): ("HIGH", C_HIGH),
    ("Dry Bulk",        "Sentiment"):     ("MOD",  C_HIGH),
    ("Tankers",         "Momentum"):      ("LOW",  C_LOW),
    ("Tankers",         "Mean Reversion"):("MOD",  C_MOD),
    ("Tankers",         "BDI Divergence"):("NONE", C_TEXT3),
    ("Tankers",         "Macro Overlay"): ("LOW",  C_LOW),
    ("Tankers",         "Sentiment"):     ("LOW",  C_LOW),
    ("LNG",             "Momentum"):      ("MOD",  C_MOD),
    ("LNG",             "Mean Reversion"):("HIGH", C_HIGH),
    ("LNG",             "BDI Divergence"):("NONE", C_TEXT3),
    ("LNG",             "Macro Overlay"): ("MOD",  C_MOD),
    ("LNG",             "Sentiment"):     ("HIGH", C_HIGH),
    ("Port Operators",  "Momentum"):      ("MOD",  C_HIGH),
    ("Port Operators",  "Mean Reversion"):("MOD",  C_MOD),
    ("Port Operators",  "BDI Divergence"):("LOW",  C_LOW),
    ("Port Operators",  "Macro Overlay"): ("HIGH", C_HIGH),
    ("Port Operators",  "Sentiment"):     ("MOD",  C_MOD),
    ("Mixed",           "Momentum"):      ("MOD",  C_MOD),
    ("Mixed",           "Mean Reversion"):("MOD",  C_MOD),
    ("Mixed",           "BDI Divergence"):("MOD",  C_HIGH),
    ("Mixed",           "Macro Overlay"): ("MOD",  C_MOD),
    ("Mixed",           "Sentiment"):     ("LOW",  C_LOW),
}

# ---------------------------------------------------------------------------
# Provenance — sources used across this tab
# ---------------------------------------------------------------------------

# Signal sections are pure real engine output — no synthetic signal source.
# (The former DataSource.demo("Synthetic signal log") named _MOCK_SIGNALS, which
# has been deleted; leaving it would falsely stamp real tables as synthetic.)
_ALPHA_SOURCES = [
    DataSource.modeled("Internal alpha-signal engine"),
]

# The price chart alone can fall back to a synthetic price series when a
# ticker's feed is dark (always labelled on the chart itself), so only that
# section advertises a demo source.
_CHART_SOURCES = [
    DataSource.modeled("Internal alpha-signal engine"),
    DataSource.demo("Synthetic price series (used only when a price feed is dark)"),
]

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _dir_arrow(direction: str) -> str:
    return "↑ LONG" if direction == "LONG" else ("↓ SHORT" if direction == "SHORT" else "→ FLAT")

def _dir_color(direction: str) -> str:
    return {
        "LONG": C_LONG,
        "SHORT": C_SHORT,
    }.get(direction, C_NEUTRAL)

def _conv_color(conv: str) -> str:
    return {
        "HIGH": C_HIGH,
        "MODERATE": C_MOD,
        "MOD": C_MOD,
        "LOW": C_LOW,
        "MEDIUM": C_MOD,
    }.get(conv, C_TEXT3)


# ---------------------------------------------------------------------------
# Section 1 — Alpha Signal Hub (hero KPIs)
# ---------------------------------------------------------------------------

def _render_hero(signals: list[dict]) -> None:
    try:
        n_total   = len(signals)
        n_high    = sum(1 for s in signals if s.get("conviction") in ("HIGH",))
        strengths = [s.get("strength", 0.5) for s in signals]
        avg_str   = round(float(np.mean(strengths)), 2) if strengths else 0.0
        # Real average risk/reward of the live signals (target ÷ stop distance).
        # Replaces a former annualized-alpha card whose value was
        # avg_strength × avg_rr × 0.18 — a heuristic that was mislabelled as a
        # backtested estimate (the engine runs no such backtest).
        rrs       = [float(s.get("rr", 0.0)) for s in signals if s.get("rr")]
        avg_rr    = round(float(np.mean(rrs)), 2) if rrs else 0.0

        metric_card_row(
            [
                {"label": "Active Signals",       "value": str(n_total),
                 "accent": C_ACCENT, "sublabel": "fired on live data"},
                {"label": "High Conviction",      "value": str(n_high),
                 "accent": C_HIGH,   "sublabel": "strong-edge signals"},
                {"label": "Avg Signal Strength",  "value": f"{avg_str:.2f}",
                 "accent": C_MOD,    "sublabel": "scale 0.00 – 1.00"},
                {"label": "Avg Risk / Reward",    "value": f"{avg_rr:.2f}x",
                 "accent": C_PURPLE, "sublabel": "modeled target ÷ stop"},
            ],
            columns=4,
        )
    except Exception as exc:
        logger.warning(f"[tab_alpha] hero render failed: {exc}")
        st.info("Alpha Signal Hub unavailable.")

# ---------------------------------------------------------------------------
# Section 2 — Signal Conviction Matrix
# ---------------------------------------------------------------------------

def _render_conviction_matrix() -> None:
    try:
        section_header(
            "Signal Conviction Matrix",
            "Illustrative framework — which signal types the engine weighs per "
            "category (not live readings)",
        )

        headers = ["Category"] + _SIG_TYPES
        rows = []
        for cat in _CATEGORIES:
            row_cells = [_sans(cat, color=C_TEXT, weight=700)]
            for sig in _SIG_TYPES:
                label, color = _MATRIX_DATA.get((cat, sig), ("NONE", C_TEXT3))
                if label == "NONE":
                    row_cells.append(_sans("—", color=C_TEXT3))
                else:
                    row_cells.append(badge(label, color=color))
            rows.append(row_cells)

        wsj_market_table(headers, rows)
        st.markdown(source_footer(_ALPHA_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"[tab_alpha] conviction matrix failed: {exc}")
        st.info("Conviction matrix unavailable.")

# ---------------------------------------------------------------------------
# Section 3 — Top Signals Table
# ---------------------------------------------------------------------------

def _render_signals_table(signals: list[dict]) -> None:
    try:
        section_header(
            "Top Signals Table",
            "Actionable long/short signals — ranked by conviction × strength",
        )

        if not signals:
            st.info(
                "No live signals — the engine emits a signal only when a real "
                "triggering condition (rate move, divergence, regime shift, …) is "
                "met on live prices. Expected when markets are quiet or a price "
                "feed is unavailable."
            )
            return

        # No "Age" column: engine signals are computed as of the latest close,
        # they carry no genuine wall-clock age (the former value was random).
        headers = ["Instrument", "Direction", "Conviction", "Strength",
                   "Signal Type", "Basis", "Entry", "Stop", "Target", "R/R"]

        rows = []
        for s in signals:
            ticker    = s.get("ticker", "—")
            direction = s.get("direction", "FLAT")
            conv      = s.get("conviction", "LOW")
            strength  = s.get("strength", 0.0)
            sig_type  = s.get("sig_type", "—")
            basis     = s.get("basis", "—")
            entry     = s.get("entry", 0.0)
            stop      = s.get("stop", 0.0)
            target    = s.get("target", 0.0)
            rr        = s.get("rr", 0.0)

            d_col = _dir_color(direction)
            c_col = _conv_color(conv)

            rows.append([
                _mono(ticker, color=C_TEXT),
                _sans(_dir_arrow(direction), color=d_col, weight=700),
                badge(conv, color=c_col),
                _mono(f"{strength:.2f}"),
                _sans(sig_type, color=C_ACCENT, weight=600),
                _sans(basis, color=C_TEXT2),
                _mono(f"${entry:.2f}"),
                _mono(f"${stop:.2f}", color=C_SHORT),
                _mono(f"${target:.2f}", color=C_HIGH),
                _mono(f"{rr:.1f}x", color=C_MOD),
            ])

        wsj_market_table(headers, rows)
        st.markdown(source_footer(_ALPHA_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"[tab_alpha] signals table failed: {exc}")
        st.info("Signals table unavailable.")

# ---------------------------------------------------------------------------
# Section 4 — Signal Generation Engine Display
# ---------------------------------------------------------------------------

def _render_engine_diagram() -> None:
    try:
        section_header(
            "Signal Generation Engine",
            "The six rule-based strategies and the inputs they read",
        )

        # Only the inputs the strategies actually consume — the former list
        # advertised "Options Sentiment" and "Insider Filings", which no engine
        # strategy reads.
        inputs = [
            ("BDI / Baltic Indices",  C_CYAN),
            ("FBX Freight Rates",     C_CYAN),
            ("Stock Price History",   C_ACCENT),
            ("Macro Data (PMI, IP)",  C_MOD),
            ("Port Congestion Index", C_PURPLE),
        ]
        # The REAL strategies in engine.alpha_engine.generate_all_signals — six
        # independent rule-based strategies, each anchored to a real entry price,
        # each assigning conviction from its own raw-metric thresholds. (The
        # former diagram fabricated a 5-stage "factor-scoring → regime-detection →
        # signal-fusion → 0.6-threshold → risk-adjustment" pipeline the engine
        # never runs.)
        engine_steps = [
            ("FBX Rate Momentum",    "Trans-Pacific / Asia-Europe rate surge → LONG"),
            ("BDI–SBLK Divergence",  "Baltic up while SBLK lags → catch-up LONG"),
            ("Congestion Arbitrage", "High port congestion → ZIM rate-spike LONG"),
            ("Oversold Mean-Revert", "Big drop + positive freight backdrop → LONG"),
            ("Macro Regime",         "PMI-proxy + BDI → basket LONG / ZIM SHORT"),
            ("Seasonal Prior",       "Peak-season / post-CNY (LOW, calendar-only)"),
        ]
        outputs = [
            ("HIGH conviction signals", C_HIGH),
            ("MEDIUM signals",          C_MOD),
            ("LOW signals",             C_TEXT3),
            ("Entry / Stop / Target",   C_CYAN),
            ("Risk / Reward per signal", C_ACCENT),
        ]

        # Each pipeline column → a single wsj_market_table for crisp alignment.
        c_in, c_eng, c_out = st.columns([1, 1, 1])

        with c_in:
            in_rows = [[_sans(label, color=col, weight=600)] for label, col in inputs]
            wsj_market_table(["Data Inputs"], in_rows)
        with c_eng:
            eng_rows = [
                [_sans(step, color=C_ACCENT, weight=700), _sans(desc, color=C_TEXT3)]
                for step, desc in engine_steps
            ]
            wsj_market_table(["Strategy", "Trigger"], eng_rows)
        with c_out:
            out_rows = [[_sans(label, color=col, weight=600)] for label, col in outputs]
            wsj_market_table(["Output"], out_rows)

        st.markdown(source_footer(_ALPHA_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"[tab_alpha] engine diagram failed: {exc}")
        st.info("Engine diagram unavailable.")

# ---------------------------------------------------------------------------
# Section 5 — Multi-Factor Signal Breakdown (HIGH conviction only)
# ---------------------------------------------------------------------------

def _factor_score_color(score: float) -> str:
    return C_HIGH if score >= 0.75 else (C_MOD if score >= 0.55 else C_LOW)


def _render_factor_breakdown(signals: list[dict]) -> None:
    """Per-signal strength for HIGH-conviction signals, built from REAL engine
    output.

    The engine emits single-factor signals (each signal IS one strategy/factor),
    so it does not produce a true multi-factor decomposition. The former version
    rendered a hand-coded 5-factor grid mapped to signals by list position —
    fabricated numbers (and fake tickers) presented as a factor decomposition.
    This shows only what the engine actually computes: the real strategy,
    direction, and strength of each HIGH-conviction signal.
    """
    try:
        high_signals = [s for s in signals if s.get("conviction") == "HIGH"]
        if not high_signals:
            return

        section_header(
            "HIGH-Conviction Signal Strength",
            "Real strategy, direction and strength for each HIGH-conviction signal",
        )

        headers = ["Instrument", "Strategy / Factor", "Direction", "Strength"]
        rows: list[list[str]] = []
        for s in sorted(high_signals, key=lambda x: -float(x.get("strength", 0.0))):
            v = float(s.get("strength", 0.0))
            direction = s.get("direction", "FLAT")
            rows.append([
                _mono(s.get("ticker", "—"), color=C_TEXT),
                _sans(s.get("sig_type", "—"), color=C_ACCENT, weight=600),
                _sans(_dir_arrow(direction), color=_dir_color(direction), weight=700),
                _mono(f"{v:.2f}", color=_factor_score_color(v)),
            ])

        wsj_market_table(headers, rows)
        st.markdown(source_footer(_ALPHA_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"[tab_alpha] factor breakdown failed: {exc}")
        st.info("Factor breakdown unavailable.")

# ---------------------------------------------------------------------------
# Section 6 — Signal vs Price Chart (ZIM & MATX)
# ---------------------------------------------------------------------------

def _render_price_signal_chart(stock_data: dict, signals: list[dict]) -> None:
    try:
        section_header(
            "Signal vs Price Chart",
            "ZIM & MATX price history with signal entry/exit markers",
        )

        tab1, tab2 = st.tabs(["ZIM", "MATX"])

        for ticker, tab_obj in [("ZIM", tab1), ("MATX", tab2)]:
            with tab_obj:
                try:
                    df = stock_data.get(ticker) if isinstance(stock_data, dict) else None
                    if df is not None and not df.empty and "close" in df.columns:
                        price_is_synthetic = False
                        df = df.copy()
                        if "date" in df.columns:
                            df = df.sort_values("date")
                            x_vals = df["date"].tolist()
                        else:
                            x_vals = list(range(len(df)))
                        y_vals = df["close"].tolist()
                    else:
                        price_is_synthetic = True
                        # Generate synthetic price series (legitimate fallback)
                        rng = np.random.default_rng(42 + stable_hash(ticker) % 100)
                        n = 120
                        base = 19.4 if ticker == "ZIM" else 24.1
                        price_returns = rng.normal(0.0005, 0.025, n)
                        prices = base * np.exp(np.cumsum(price_returns))
                        x_vals = [
                            (datetime.date(2025, 11, 1) + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
                            for i in range(n)
                        ]
                        y_vals = prices.tolist()

                    # Signal markers. Engine signals are computed as of the
                    # latest close, so they are marked at the most recent price
                    # point — NOT a fabricated historical date (the former code
                    # scattered them at random indices over the last 30 bars).
                    sig_list = [s for s in signals
                                if isinstance(s, dict) and s.get("ticker") == ticker]
                    long_x, long_y, short_x, short_y = [], [], [], []
                    if x_vals and y_vals:
                        last_x, last_px = x_vals[-1], float(y_vals[-1])
                        for s in sig_list[:3]:
                            if s.get("direction") == "LONG":
                                long_x.append(last_x)
                                long_y.append(last_px)
                            elif s.get("direction") == "SHORT":
                                short_x.append(last_x)
                                short_y.append(last_px)

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=x_vals, y=y_vals,
                        name="Price (synthetic)" if price_is_synthetic else "Price",
                        line=dict(color=C_ACCENT, width=1.8),
                        fill="tozeroy",
                        fillcolor="rgba(53,114,176,0.05)",
                    ))
                    if price_is_synthetic:
                        # Never let a synthetic series read as real on the chart.
                        fig.add_annotation(
                            text="⚠ Synthetic price series — no live data for this ticker (illustrative)",
                            xref="paper", yref="paper", x=0.5, y=1.0,
                            showarrow=False, font=dict(color="#f59e0b", size=10),
                        )
                    if long_x:
                        fig.add_trace(go.Scatter(
                            x=long_x, y=long_y, name="LONG Signal",
                            mode="markers",
                            marker=dict(symbol="triangle-up", size=14, color=C_HIGH,
                                        line=dict(color="white", width=1)),
                        ))
                    if short_x:
                        fig.add_trace(go.Scatter(
                            x=short_x, y=short_y, name="SHORT Signal",
                            mode="markers",
                            marker=dict(symbol="triangle-down", size=14, color=C_SHORT,
                                        line=dict(color="white", width=1)),
                        ))

                    sig_list_high = [s for s in sig_list if s.get("conviction") == "HIGH"]
                    if sig_list_high:
                        s0 = sig_list_high[0]
                        entry = float(s0.get("entry", y_vals[-1]))
                        stop  = float(s0.get("stop", entry * 0.92))
                        tgt   = float(s0.get("target", entry * 1.20))
                        fig.add_hline(y=entry, line=dict(color=C_ACCENT, dash="dash", width=1),
                                      annotation_text="Entry", annotation_font_color=C_ACCENT)
                        fig.add_hline(y=stop, line=dict(color=C_SHORT, dash="dot", width=1),
                                      annotation_text="Stop", annotation_font_color=C_SHORT)
                        fig.add_hline(y=tgt, line=dict(color=C_HIGH, dash="dot", width=1),
                                      annotation_text="Target", annotation_font_color=C_HIGH)

                    apply_dark_layout(fig, title=f"{ticker} — Price + Signal Markers", height=340)
                    st.plotly_chart(fig, use_container_width=True)
                    st.markdown(source_footer(_CHART_SOURCES), unsafe_allow_html=True)

                except Exception as inner_exc:
                    logger.warning(f"[tab_alpha] chart {ticker} failed: {inner_exc}")
                    st.info(f"{ticker} chart unavailable.")
    except Exception as exc:
        logger.warning(f"[tab_alpha] price chart section failed: {exc}")
        st.info("Price chart unavailable.")

# ---------------------------------------------------------------------------
# Section 7 — Live Signal Monitor (60 s cache)
# ---------------------------------------------------------------------------

def _render_live_monitor(signals: list[dict]) -> None:
    try:
        section_header(
            "Current Signal Monitor",
            "Signals the engine emits on the latest data — refreshes on each load",
        )

        # Render the REAL signals this tab computed. The former version ignored
        # them and rebuilt the table from an internal mock cache with random
        # "X minutes ago" ages, presenting fabricated rows as a live 24-hour feed.
        if not signals:
            st.info("No signals on the current data.")
            return

        # Dot reflects conviction (there is no genuine signal age to colour by).
        headers = ["", "Instrument", "Direction", "Conviction", "Signal Type",
                   "Basis", "Strength"]
        rows: list[list[str]] = []
        for s in signals:
            ticker    = s.get("ticker", "—")
            direction = s.get("direction", "FLAT")
            conv      = s.get("conviction", "LOW")
            strength  = s.get("strength", 0.0)
            sig_type  = s.get("sig_type", "—")
            basis     = s.get("basis", "—")
            d_col     = _dir_color(direction)
            c_col     = _conv_color(conv)
            dot_col   = (C_HIGH if conv == "HIGH"
                         else (C_MOD if conv in ("MEDIUM", "MODERATE", "MOD") else C_TEXT3))
            dot       = (
                f'<span style="display:inline-block;width:7px;height:7px;'
                f'border-radius:50%;background:{dot_col};"></span>'
            )

            rows.append([
                dot,
                _mono(ticker, color=C_TEXT),
                _sans(_dir_arrow(direction), color=d_col, weight=700),
                badge(conv, color=c_col),
                _sans(sig_type, color=C_ACCENT, weight=600),
                _sans(basis, color=C_TEXT2),
                _mono(f"{strength:.2f}"),
            ])

        wsj_market_table(headers, rows)
        st.markdown(source_footer(_ALPHA_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"[tab_alpha] live monitor failed: {exc}")
        st.info("Signal monitor unavailable.")

# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------

def render(
    stock_data: dict | None = None,
    insights: Any = None,
    freight_data: Any = None,
    macro_data: Any = None,
    *args,
    **kwargs,
) -> None:
    """Render the Alpha Signal Generator tab."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('alpha'):
        try:
            page_header(
                title="Alpha Signal Generator",
                subtitle="Multi-factor alpha signals across container ships, dry bulk, tankers, and ports.",
                badge_text="ALPHA",
                badge_color=C_ACCENT,
            )

            st.warning(
                "**Modeled, illustrative — not investment advice.** The "
                "entry / target / stop levels below come from rule-based models "
                "running on a mix of real prices and *synthetic* shipping data; "
                "they are not tradeable price targets. See docs/DATA_PROVENANCE.md."
            )

            # ── Resolve signals ──────────────────────────────────────────────────
            signals: list[dict] = []

            # Try live engine first
            try:
                if stock_data:
                    # Pass the feeds this tab has (stock + freight + macro); the
                    # engine defaults the port/route feeds it doesn't. The call
                    # previously passed only stock_data, which raised TypeError
                    # (5 required args) and was silently swallowed — so the
                    # engine path was dead and the tab always showed mock data.
                    raw = generate_all_signals(
                        stock_data,
                        freight_data=freight_data,
                        macro_data=macro_data,
                    )
                    for s in (raw or []):
                        try:
                            signals.append({
                                "ticker":    getattr(s, "ticker", "—"),
                                "direction": getattr(s, "direction", "FLAT"),
                                "conviction": getattr(s, "conviction", "LOW"),
                                "strength":  float(getattr(s, "strength", 0.5)),
                                "sig_type":  getattr(s, "signal_type", "Momentum").replace("_", " ").title(),
                                # Strip the appended not-advice DISCLAIMER before
                                # truncating, else the 60-char clip just shows a
                                # half-cut disclaimer (the page banner carries the
                                # caveat). Show the actual reasoning here.
                                "basis":     ((getattr(s, "rationale", "—") or "—").replace(DISCLAIMER, "").strip() or "—")[:60],
                                "entry":     float(getattr(s, "entry_price", 0.0)),
                                "stop":      float(getattr(s, "stop_loss", 0.0)),
                                "target":    float(getattr(s, "target_price", 0.0)),
                                "rr":        float(getattr(s, "risk_reward", 1.5)),
                            })
                        except Exception:
                            pass
            except Exception as eng_exc:
                logger.debug(f"[tab_alpha] engine signals skipped: {eng_exc}")

            # No mock fallback: when the engine emits nothing (quiet markets or a
            # dark price feed) the sections below show honest empty-states rather
            # than fabricated signals. Surface that plainly up top.
            if not signals:
                st.info(
                    "No alpha signals fire on the current data. The engine emits "
                    "a signal only when a real triggering condition is met on live "
                    "prices — expected when markets are quiet or a price feed is "
                    "unavailable. Methodology sections remain below."
                )

            # Sort: HIGH → MEDIUM/MODERATE → LOW, then by strength desc.
            signals.sort(key=lambda s: (
                0 if s.get("conviction") == "HIGH"
                else (1 if s.get("conviction") in ("MEDIUM", "MODERATE", "MOD") else 2),
                -s.get("strength", 0.0),
            ))

            # ── Section 1: Hero KPIs ─────────────────────────────────────────────
            _render_hero(signals)

            # ── Signal Book ──────────────────────────────────────────────────────
            section_divider("Signal Book")

            # ── Section 2: Conviction Matrix ─────────────────────────────────────
            _render_conviction_matrix()

            # ── Section 3: Top Signals Table ─────────────────────────────────────
            _render_signals_table(signals)

            # ── Methodology ──────────────────────────────────────────────────────
            section_divider("Methodology")

            # ── Section 4: Engine Diagram ─────────────────────────────────────────
            _render_engine_diagram()

            # ── Section 5: Factor Breakdown ───────────────────────────────────────
            _render_factor_breakdown(signals)

            # ── Live Tape ────────────────────────────────────────────────────────
            section_divider("Live Tape")

            # ── Section 6: Price + Signal Chart ──────────────────────────────────
            _render_price_signal_chart(stock_data or {}, signals)

            # ── Section 7: Live Monitor ───────────────────────────────────────────
            _render_live_monitor(signals)

        except Exception as top_exc:
            logger.error(f"[tab_alpha] top-level render failed: {top_exc}")
            st.error("Alpha Signal tab encountered an unexpected error. Please refresh.")
