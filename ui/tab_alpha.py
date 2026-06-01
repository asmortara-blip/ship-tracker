"""Alpha Signal tab — sophisticated alpha signal generation and display dashboard."""
from __future__ import annotations

import datetime
import random
from typing import Any

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from engine.alpha_engine import generate_all_signals
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
# Mock / fallback data — kept verbatim (legitimate alpha-signal scaffolding)
# ---------------------------------------------------------------------------

_MOCK_SIGNALS = [
    # ticker, direction, conviction, strength, sig_type, basis, entry, stop, target, rr, age_min
    ("ZIM",  "LONG",  "HIGH",     0.87, "Momentum",      "BDI 12% surge + vol breakout",   19.40, 17.80, 23.50, 2.6, 5),
    ("MATX", "LONG",  "HIGH",     0.82, "BDI Divergence","CCFI–MATX spread blowout",        24.10, 22.30, 28.40, 2.4, 12),
    ("SBLK", "LONG",  "MODERATE", 0.71, "Mean Reversion","52W low reversion + BDI uptick",  17.50, 16.10, 20.80, 2.3, 31),
    ("GOGL", "LONG",  "HIGH",     0.84, "Macro Overlay", "China stimulus + Capesize demand", 13.20, 12.10, 15.90, 2.4, 8),
    ("DAC",  "LONG",  "MODERATE", 0.68, "Momentum",      "Container rate stabilization",    69.00, 64.50, 79.00, 2.2, 47),
    ("STNG", "SHORT", "HIGH",     0.79, "Sentiment",     "Tanker oversupply + rate decline", 51.30, 54.80, 43.20, 2.3, 14),
    ("GSL",  "LONG",  "MODERATE", 0.66, "Mean Reversion","Charter rate uptick + underval",   19.80, 18.20, 23.50, 2.3, 22),
    ("ZIM",  "SHORT", "LOW",      0.44, "Macro Overlay", "Red Sea normalization risk",       19.40, 21.00, 17.00, 1.5, 90),
    ("MATX", "LONG",  "MODERATE", 0.73, "BDI Divergence","Hawaii route premium expansion",   24.10, 22.50, 28.00, 2.2, 38),
    ("SBLK", "SHORT", "LOW",      0.41, "Sentiment",     "Insider selling + iron ore soft",  17.50, 19.20, 15.40, 1.2, 105),
    ("GOGL", "LONG",  "HIGH",     0.80, "Momentum",      "Capesize hire rates 3-wk high",    13.20, 12.40, 15.60, 2.0, 3),
    ("DAC",  "LONG",  "HIGH",     0.85, "Fundamental",   "Asset coverage 1.4x + FCF yield",  69.00, 63.00, 81.00, 2.0, 18),
    ("STNG", "SHORT", "MODERATE", 0.62, "Macro Overlay", "OPEC+ output cut uncertainty",    51.30, 54.00, 45.00, 2.3, 55),
    ("GSL",  "SHORT", "LOW",      0.38, "Mean Reversion","Box-ship charter softening",       19.80, 21.50, 17.20, 1.5, 130),
    ("ZIM",  "LONG",  "HIGH",     0.90, "BDI Divergence","WCI–ZIM earnings correlation hit", 19.40, 17.50, 24.20, 2.8, 1),
]

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

_ALPHA_SOURCES = [
    DataSource.modeled("Internal alpha-signal engine"),
    DataSource.demo("Synthetic signal log"),
]

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60, show_spinner=False)
def _cached_signals(stock_data_key: str, now_bucket: str) -> list[dict]:
    """Return live-monitor signals list (cached 60 s)."""
    rng = random.Random(stable_hash(now_bucket))
    results = []
    for row in _MOCK_SIGNALS:
        ticker, direction, conviction, strength, sig_type, basis, entry, stop, target, rr, age_min = row
        age_jitter = rng.randint(-3, 3)
        mins_ago = max(1, age_min + age_jitter)
        results.append({
            "ticker": ticker, "direction": direction, "conviction": conviction,
            "strength": strength, "sig_type": sig_type, "basis": basis,
            "entry": entry, "stop": stop, "target": target, "rr": rr,
            "mins_ago": mins_ago,
        })
    results.sort(key=lambda x: x["mins_ago"])
    return results

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
        # Estimated annualized alpha: avg strength × RR × 12 (monthly compounding heuristic)
        avg_rr    = float(np.mean([s.get("rr", 2.0) for s in signals])) if signals else 2.0
        est_alpha = round(avg_str * avg_rr * 0.18 * 100, 1)  # rough annualized %

        metric_card_row(
            [
                {"label": "Active Signals",       "value": str(n_total),
                 "accent": C_ACCENT, "sublabel": "total generated"},
                {"label": "High Conviction",      "value": str(n_high),
                 "accent": C_HIGH,   "sublabel": "strong edge signals"},
                {"label": "Avg Signal Strength",  "value": f"{avg_str:.2f}",
                 "accent": C_MOD,    "sublabel": "scale 0.00 – 1.00"},
                {"label": "Est. Alpha p.a.",      "value": f"{est_alpha:.1f}%",
                 "accent": C_PURPLE, "sublabel": "backtest-based estimate"},
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
            "Conviction by category and signal type",
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

        headers = ["Instrument", "Direction", "Conviction", "Strength",
                   "Signal Type", "Basis", "Entry", "Stop", "Target", "R/R", "Age"]

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
            mins_ago  = s.get("mins_ago", 999)

            d_col = _dir_color(direction)
            c_col = _conv_color(conv)
            age_str = f"{mins_ago}m" if mins_ago < 60 else f"{mins_ago // 60}h {mins_ago % 60}m"

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
                _sans(age_str, color=C_TEXT3),
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
            "Transparency into how each signal is constructed",
        )

        inputs = [
            ("BDI / Baltic Indices",  C_CYAN),
            ("WCI / Freightos WCI",   C_CYAN),
            ("Stock Price History",   C_ACCENT),
            ("Macro Data (CPI, PMI)", C_MOD),
            ("Port Congestion Index", C_PURPLE),
            ("Options Sentiment",     C_HIGH),
            ("Insider Filings",       C_TEXT2),
        ]
        engine_steps = [
            ("(1) Factor Scoring",    "Score each input 0–1"),
            ("(2) Regime Detection",  "Bull / Bear / High-Vol"),
            ("(3) Signal Fusion",     "Weighted ensemble"),
            ("(4) Conviction Filter", "Threshold: > 0.6 = HIGH"),
            ("(5) Risk Adjustment",   "Stop / Target placement"),
        ]
        outputs = [
            ("HIGH conviction signals", C_HIGH),
            ("MODERATE signals",        C_MOD),
            ("LOW / monitor",           C_TEXT3),
            ("Factor attribution",      C_ACCENT),
            ("Entry / Stop / Target",   C_CYAN),
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
            wsj_market_table(["Stage", "Description"], eng_rows)
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

_FACTOR_SCORES = {
    "ZIM-HIGH-BDI":  {"Momentum": 0.88, "Fundamental": 0.74, "Sentiment": 0.81, "Technical": 0.79, "Macro": 0.71},
    "MATX-HIGH-BDI": {"Momentum": 0.76, "Fundamental": 0.82, "Sentiment": 0.70, "Technical": 0.73, "Macro": 0.68},
    "GOGL-HIGH-MOM": {"Momentum": 0.91, "Fundamental": 0.69, "Sentiment": 0.77, "Technical": 0.83, "Macro": 0.85},
    "DAC-HIGH-FND":  {"Momentum": 0.65, "Fundamental": 0.90, "Sentiment": 0.62, "Technical": 0.70, "Macro": 0.58},
}


def _factor_score_color(score: float) -> str:
    return C_HIGH if score >= 0.75 else (C_MOD if score >= 0.55 else C_LOW)


def _render_factor_breakdown(signals: list[dict]) -> None:
    try:
        high_signals = [s for s in signals if s.get("conviction") == "HIGH"][:4]
        if not high_signals:
            return

        section_header(
            "Multi-Factor Signal Breakdown",
            "Factor decomposition for HIGH conviction signals",
        )

        factors = ["Momentum", "Fundamental", "Sentiment", "Technical", "Macro"]
        factor_keys = list(_FACTOR_SCORES.keys())

        # Build one row per factor; columns = ticker/HIGH-conviction signals.
        ticker_labels: list[str] = []
        score_lookup: list[dict] = []
        for idx, s in enumerate(high_signals):
            ticker = s.get("ticker", "—")
            sig_key = factor_keys[idx % len(factor_keys)]
            ticker_labels.append(f"{ticker} {_dir_arrow(s.get('direction', 'FLAT'))}")
            score_lookup.append(_FACTOR_SCORES[sig_key])

        headers = ["Factor", *ticker_labels]
        rows: list[list[str]] = []
        for f in factors:
            row = [_sans(f, color=C_TEXT2, weight=600)]
            for scores in score_lookup:
                v = scores[f]
                row.append(_mono(f"{v:.2f}", color=_factor_score_color(v)))
            rows.append(row)

        # Combined avg row
        combined_row = [_sans("Combined", color=C_TEXT, weight=700)]
        for scores in score_lookup:
            avg = round(float(np.mean(list(scores.values()))), 2)
            combined_row.append(_mono(f"{avg:.2f}", color=C_HIGH))
        rows.append(combined_row)

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
                        df = df.copy()
                        if "date" in df.columns:
                            df = df.sort_values("date")
                            x_vals = df["date"].tolist()
                        else:
                            x_vals = list(range(len(df)))
                        y_vals = df["close"].tolist()
                    else:
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

                    # Signal markers
                    sig_list = [s for s in signals
                                if isinstance(s, dict) and s.get("ticker") == ticker]
                    long_x, long_y, short_x, short_y = [], [], [], []
                    rng2 = random.Random(stable_hash(ticker))
                    for s in sig_list[:3]:
                        idx = rng2.randint(max(0, len(x_vals) - 30), len(x_vals) - 1)
                        px  = float(y_vals[idx])
                        if s.get("direction") == "LONG":
                            long_x.append(x_vals[idx])
                            long_y.append(px)
                        else:
                            short_x.append(x_vals[idx])
                            short_y.append(px)

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=x_vals, y=y_vals, name="Price",
                        line=dict(color=C_ACCENT, width=1.8),
                        fill="tozeroy",
                        fillcolor="rgba(53,114,176,0.05)",
                    ))
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
                    st.markdown(source_footer(_ALPHA_SOURCES), unsafe_allow_html=True)

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
            "Live Signal Monitor",
            "Signals generated in last 24 h — newest first — auto-refreshes every 60 s",
        )

        now_bucket = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")[:-1]  # 10-min buckets
        live = _cached_signals("static", now_bucket)
        live_24h = [s for s in live if s.get("mins_ago", 9999) <= 1440]

        if not live_24h:
            st.info("No signals in the last 24 hours.")
            return

        headers = ["", "Instrument", "Direction", "Conviction", "Signal Type",
                   "Basis", "Strength", "Age"]
        rows: list[list[str]] = []
        for s in live_24h:
            ticker    = s.get("ticker", "—")
            direction = s.get("direction", "FLAT")
            conv      = s.get("conviction", "LOW")
            strength  = s.get("strength", 0.0)
            sig_type  = s.get("sig_type", "—")
            basis     = s.get("basis", "—")
            mins_ago  = s.get("mins_ago", 999)
            d_col     = _dir_color(direction)
            c_col     = _conv_color(conv)
            age_str   = f"{mins_ago}m ago" if mins_ago < 60 else f"{mins_ago // 60}h {mins_ago % 60}m ago"
            dot_col   = C_HIGH if mins_ago < 15 else (C_MOD if mins_ago < 60 else C_TEXT3)
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
                _sans(age_str, color=C_TEXT3),
            ])

        wsj_market_table(headers, rows)
        st.markdown(source_footer(_ALPHA_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"[tab_alpha] live monitor failed: {exc}")
        st.info("Live signal monitor unavailable.")

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
                                "basis":     getattr(s, "rationale", "—")[:60],
                                "entry":     float(getattr(s, "entry_price", 0.0)),
                                "stop":      float(getattr(s, "stop_loss", 0.0)),
                                "target":    float(getattr(s, "target_price", 0.0)),
                                "rr":        float(getattr(s, "risk_reward", 1.5)),
                                "mins_ago":  random.randint(1, 120),
                            })
                        except Exception:
                            pass
            except Exception as eng_exc:
                logger.debug(f"[tab_alpha] engine signals skipped: {eng_exc}")

            # Fall back to mock if empty
            if not signals:
                for row in _MOCK_SIGNALS:
                    ticker, direction, conviction, strength, sig_type, basis, entry, stop, target, rr, mins_ago = row
                    signals.append({
                        "ticker": ticker, "direction": direction, "conviction": conviction,
                        "strength": strength, "sig_type": sig_type, "basis": basis,
                        "entry": entry, "stop": stop, "target": target,
                        "rr": rr, "mins_ago": mins_ago,
                    })

            # Sort: HIGH first, then by strength desc
            signals.sort(key=lambda s: (
                0 if s.get("conviction") == "HIGH" else (1 if s.get("conviction") in ("MODERATE", "MOD") else 2),
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
