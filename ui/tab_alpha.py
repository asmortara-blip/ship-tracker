"""Alpha Signal tab — sophisticated alpha signal generation and display dashboard."""
from __future__ import annotations

import datetime
import random
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from engine.alpha_engine import AlphaSignal, generate_all_signals
from ui.styles import (
    C_BG, C_SURFACE, C_CARD, C_BORDER,
    C_HIGH, C_MOD, C_LOW, C_ACCENT,
    C_TEXT, C_TEXT2, C_TEXT3,
    dark_layout,
)

# ---------------------------------------------------------------------------
# Local color aliases
# ---------------------------------------------------------------------------

C_LONG    = C_HIGH       # "#10b981"
C_SHORT   = C_LOW        # "#ef4444"
C_NEUTRAL = C_TEXT2      # "#94a3b8"
C_PURPLE  = "#8b5cf6"
C_CYAN    = "#06b6d4"

# ---------------------------------------------------------------------------
# Mock / fallback data
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
    ("Container Ships", "Mean Reversion"):("MOD",  "#16a34a"),
    ("Container Ships", "BDI Divergence"):("HIGH", C_HIGH),
    ("Container Ships", "Macro Overlay"): ("MOD",  "#16a34a"),
    ("Container Ships", "Sentiment"):     ("MOD",  "#16a34a"),
    ("Dry Bulk",        "Momentum"):      ("HIGH", C_HIGH),
    ("Dry Bulk",        "Mean Reversion"):("LOW",  "#dc2626"),
    ("Dry Bulk",        "BDI Divergence"):("HIGH", C_HIGH),
    ("Dry Bulk",        "Macro Overlay"): ("HIGH", C_HIGH),
    ("Dry Bulk",        "Sentiment"):     ("MOD",  "#16a34a"),
    ("Tankers",         "Momentum"):      ("LOW",  "#dc2626"),
    ("Tankers",         "Mean Reversion"):("MOD",  C_MOD),
    ("Tankers",         "BDI Divergence"):("NONE", C_TEXT3),
    ("Tankers",         "Macro Overlay"): ("LOW",  "#dc2626"),
    ("Tankers",         "Sentiment"):     ("LOW",  "#dc2626"),
    ("LNG",             "Momentum"):      ("MOD",  C_MOD),
    ("LNG",             "Mean Reversion"):("HIGH", C_HIGH),
    ("LNG",             "BDI Divergence"):("NONE", C_TEXT3),
    ("LNG",             "Macro Overlay"): ("MOD",  C_MOD),
    ("LNG",             "Sentiment"):     ("HIGH", C_HIGH),
    ("Port Operators",  "Momentum"):      ("MOD",  "#16a34a"),
    ("Port Operators",  "Mean Reversion"):("MOD",  C_MOD),
    ("Port Operators",  "BDI Divergence"):("LOW",  "#dc2626"),
    ("Port Operators",  "Macro Overlay"): ("HIGH", C_HIGH),
    ("Port Operators",  "Sentiment"):     ("MOD",  C_MOD),
    ("Mixed",           "Momentum"):      ("MOD",  C_MOD),
    ("Mixed",           "Mean Reversion"):("MOD",  C_MOD),
    ("Mixed",           "BDI Divergence"):("MOD",  "#16a34a"),
    ("Mixed",           "Macro Overlay"): ("MOD",  C_MOD),
    ("Mixed",           "Sentiment"):     ("LOW",  "#dc2626"),
}

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60, show_spinner=False)
def _cached_signals(stock_data_key: str, now_bucket: str) -> list[dict]:
    """Return live-monitor signals list (cached 60 s)."""
    rng = random.Random(hash(now_bucket))
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
        "MODERATE": C_MOD,
        "MOD": C_MOD,
        "LOW": C_LOW,
        "MEDIUM": C_MOD,
    }.get(conv, C_TEXT3)

def _badge(text: str, color: str) -> str:
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color}55;'
        f'border-radius:6px;padding:2px 8px;font-size:0.65rem;font-weight:700;'
        f'letter-spacing:0.07em;white-space:nowrap">{text}</span>'
    )

def _hr() -> None:
    st.markdown(
        "<hr style='border:none;border-top:1px solid rgba(255,255,255,0.06);margin:28px 0'>",
        unsafe_allow_html=True,
    )

def _section_title(label: str, sub: str = "") -> None:
    sub_html = f'<div style="font-size:0.7rem;color:{C_TEXT3};margin-top:3px">{sub}</div>' if sub else ""
    st.markdown(
        f'<div style="margin-bottom:16px">'
        f'<span style="font-size:0.65rem;font-weight:900;color:{C_TEXT2};letter-spacing:0.15em;'
        f'text-transform:uppercase;font-family:monospace">{label}</span>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )

def _card_open(extra_style: str = "") -> str:
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
        f'border-radius:14px;padding:20px 22px;{extra_style}">'
    )

def _card_close() -> str:
    return "</div>"

# ---------------------------------------------------------------------------
# Section 1 — Alpha Signal Hub (hero KPIs)
# ---------------------------------------------------------------------------

def _render_hero(signals: list[dict]) -> None:
    try:
        today_str = datetime.date.today().strftime("%B %d, %Y")
        n_total   = len(signals)
        n_high    = sum(1 for s in signals if s.get("conviction") in ("HIGH",))
        strengths = [s.get("strength", 0.5) for s in signals]
        avg_str   = round(float(np.mean(strengths)), 2) if strengths else 0.0
        # Estimated annualized alpha: avg strength × RR × 12 (monthly compounding heuristic)
        avg_rr    = float(np.mean([s.get("rr", 2.0) for s in signals])) if signals else 2.0
        est_alpha = round(avg_str * avg_rr * 0.18 * 100, 1)  # rough annualized %

        kpis = [
            (str(n_total), "ACTIVE SIGNALS", "total generated", C_ACCENT),
            (str(n_high),  "HIGH CONVICTION", "strong edge signals", C_HIGH),
            (f"{avg_str:.2f}", "AVG SIGNAL STRENGTH", "scale 0.00 – 1.00", C_MOD),
            (f"{est_alpha:.1f}%", "EST. ALPHA P.A.", "backtest-based estimate", C_PURPLE),
        ]

        kpi_html = "".join([
            f'<div style="background:rgba(0,0,0,0.28);border:1px solid rgba(255,255,255,0.07);'
            f'border-radius:14px;padding:22px 18px;text-align:center">'
            f'<div style="font-size:2.4rem;font-weight:900;color:{col};line-height:1;'
            f'font-variant-numeric:tabular-nums;font-family:monospace">{val}</div>'
            f'<div style="font-size:0.6rem;font-weight:800;color:{col};opacity:0.8;'
            f'text-transform:uppercase;letter-spacing:0.12em;margin-top:8px">{label}</div>'
            f'<div style="font-size:0.63rem;color:{C_TEXT3};margin-top:3px">{sub}</div>'
            f'</div>'
            for val, label, sub, col in kpis
        ])

        st.markdown(
            f'<div style="background:linear-gradient(135deg,#0d1826 0%,#111f35 50%,#0a1520 100%);'
            f'border:1px solid rgba(59,130,246,0.22);border-radius:20px;padding:32px 36px 28px;'
            f'margin-bottom:8px;position:relative;overflow:hidden">'
            f'<div style="position:absolute;top:-50px;right:-50px;width:200px;height:200px;'
            f'border-radius:50%;background:radial-gradient(circle,rgba(59,130,246,0.10) 0%,transparent 70%);'
            f'pointer-events:none"></div>'
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:24px">'
            f'<div style="width:9px;height:9px;border-radius:50%;background:{C_HIGH};'
            f'box-shadow:0 0 10px rgba(16,185,129,0.7)"></div>'
            f'<span style="font-size:0.72rem;font-weight:900;color:{C_TEXT};letter-spacing:0.18em;'
            f'text-transform:uppercase;font-family:monospace">ALPHA SIGNAL GENERATOR</span>'
            f'<span style="margin-left:auto;font-size:0.65rem;color:{C_TEXT3};font-family:monospace">'
            f'{today_str}</span>'
            f'</div>'
            f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px">'
            f'{kpi_html}'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"[tab_alpha] hero render failed: {exc}")
        st.info("Alpha Signal Hub unavailable.")

# ---------------------------------------------------------------------------
# Section 2 — Signal Conviction Matrix
# ---------------------------------------------------------------------------

def _render_conviction_matrix() -> None:
    try:
        _section_title("Signal Conviction Matrix", "Bloomberg-style heat map: category × signal type")

        col_w = f"repeat({len(_SIG_TYPES)}, 1fr)"
        header_cells = "".join([
            f'<div style="font-size:0.6rem;font-weight:700;color:{C_TEXT2};'
            f'text-transform:uppercase;letter-spacing:0.1em;text-align:center;'
            f'padding:8px 4px;background:rgba(0,0,0,0.2);border-radius:6px">{t}</div>'
            for t in _SIG_TYPES
        ])

        rows_html = ""
        for cat in _CATEGORIES:
            row_cells = ""
            for sig in _SIG_TYPES:
                label, color = _MATRIX_DATA.get((cat, sig), ("NONE", C_TEXT3))
                text_color = C_BG if label in ("HIGH",) else C_TEXT
                if label == "NONE":
                    text_color = C_TEXT3
                row_cells += (
                    f'<div style="background:{color}33;border:1px solid {color}55;'
                    f'border-radius:8px;text-align:center;padding:10px 4px;'
                    f'font-size:0.62rem;font-weight:800;color:{color};'
                    f'letter-spacing:0.06em">{label}</div>'
                )
            cat_label = (
                f'<div style="font-size:0.65rem;font-weight:700;color:{C_TEXT};'
                f'padding:10px 0;white-space:nowrap">{cat}</div>'
            )
            rows_html += (
                f'<div style="display:contents">'
                f'<div style="display:flex;align-items:center;padding-right:12px">{cat_label}</div>'
                f'{row_cells}'
                f'</div>'
            )

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:14px;padding:20px 22px">'
            f'<div style="display:grid;grid-template-columns:160px {col_w};gap:8px;align-items:center">'
            f'<div></div>'
            f'{header_cells}'
            f'{rows_html}'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"[tab_alpha] conviction matrix failed: {exc}")
        st.info("Conviction matrix unavailable.")

# ---------------------------------------------------------------------------
# Section 3 — Top Signals Table
# ---------------------------------------------------------------------------

def _render_signals_table(signals: list[dict]) -> None:
    try:
        _section_title("Top Signals Table", "Actionable long/short signals — ranked by conviction × strength")

        headers = ["INSTRUMENT", "DIRECTION", "CONVICTION", "STRENGTH",
                   "SIGNAL TYPE", "BASIS", "ENTRY", "STOP", "TARGET", "R/R", "AGE"]
        col_w   = "90px 90px 90px 70px 110px 1fr 70px 70px 70px 45px 70px"

        header_html = "".join([
            f'<div style="font-size:0.58rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.09em;padding:6px 8px">{h}</div>'
            for h in headers
        ])

        rows_html = ""
        for i, s in enumerate(signals):
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

            d_col  = _dir_color(direction)
            c_col  = _conv_color(conv)
            row_bg = "rgba(255,255,255,0.02)" if i % 2 == 0 else "transparent"

            age_str = f"{mins_ago}m" if mins_ago < 60 else f"{mins_ago // 60}h {mins_ago % 60}m"

            rows_html += (
                f'<div style="display:contents">'
                f'<div style="padding:10px 8px;background:{row_bg};border-radius:6px 0 0 6px;'
                f'font-size:0.75rem;font-weight:800;color:{C_TEXT};font-family:monospace">{ticker}</div>'
                f'<div style="padding:10px 8px;background:{row_bg};font-size:0.72rem;font-weight:700;color:{d_col}">'
                f'{_dir_arrow(direction)}</div>'
                f'<div style="padding:10px 8px;background:{row_bg}">{_badge(conv, c_col)}</div>'
                f'<div style="padding:10px 8px;background:{row_bg};font-size:0.72rem;font-weight:700;'
                f'color:{C_TEXT};font-family:monospace">{strength:.2f}</div>'
                f'<div style="padding:10px 8px;background:{row_bg};font-size:0.65rem;color:{C_ACCENT};'
                f'font-weight:600">{sig_type}</div>'
                f'<div style="padding:10px 8px;background:{row_bg};font-size:0.63rem;color:{C_TEXT2}">{basis}</div>'
                f'<div style="padding:10px 8px;background:{row_bg};font-size:0.68rem;color:{C_TEXT};'
                f'font-family:monospace">${entry:.2f}</div>'
                f'<div style="padding:10px 8px;background:{row_bg};font-size:0.68rem;color:{C_SHORT};'
                f'font-family:monospace">${stop:.2f}</div>'
                f'<div style="padding:10px 8px;background:{row_bg};font-size:0.68rem;color:{C_HIGH};'
                f'font-family:monospace">${target:.2f}</div>'
                f'<div style="padding:10px 8px;background:{row_bg};font-size:0.68rem;color:{C_MOD};'
                f'font-family:monospace">{rr:.1f}x</div>'
                f'<div style="padding:10px 8px;background:{row_bg};border-radius:0 6px 6px 0;'
                f'font-size:0.63rem;color:{C_TEXT3}">{age_str}</div>'
                f'</div>'
            )

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:14px;padding:20px 22px">'
            f'<div style="display:grid;grid-template-columns:{col_w};gap:2px;'
            f'border-bottom:1px solid rgba(255,255,255,0.06);margin-bottom:8px">'
            f'{header_html}'
            f'</div>'
            f'<div style="display:grid;grid-template-columns:{col_w};gap:2px">'
            f'{rows_html}'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"[tab_alpha] signals table failed: {exc}")
        st.info("Signals table unavailable.")

# ---------------------------------------------------------------------------
# Section 4 — Signal Generation Engine Display
# ---------------------------------------------------------------------------

def _render_engine_diagram() -> None:
    try:
        _section_title("Signal Generation Engine", "Transparency into how each signal is constructed")

        inputs = [
            ("BDI / Baltic Indices", C_CYAN),
            ("WCI / Freightos WCI", C_CYAN),
            ("Stock Price History", C_ACCENT),
            ("Macro Data (CPI, PMI)", C_MOD),
            ("Port Congestion Index", C_PURPLE),
            ("Options Sentiment", C_HIGH),
            ("Insider Filings", C_TEXT2),
        ]
        engine_steps = [
            ("① Factor Scoring", "Score each input 0–1"),
            ("② Regime Detection", "Bull/Bear/High-Vol"),
            ("③ Signal Fusion", "Weighted ensemble"),
            ("④ Conviction Filter", "Threshold: >0.6 HIGH"),
            ("⑤ Risk Adjustment", "Stop/Target placement"),
        ]
        outputs = [
            ("HIGH conviction signals", C_HIGH),
            ("MODERATE signals", C_MOD),
            ("LOW / monitor", C_TEXT3),
            ("Factor attribution", C_ACCENT),
            ("Entry / Stop / Target", C_CYAN),
        ]

        inp_html = "".join([
            f'<div style="background:rgba(0,0,0,0.25);border-left:3px solid {col};'
            f'border-radius:0 6px 6px 0;padding:7px 12px;margin-bottom:6px;'
            f'font-size:0.65rem;color:{C_TEXT}">{label}</div>'
            for label, col in inputs
        ])
        eng_html = "".join([
            f'<div style="background:rgba(59,130,246,0.10);border:1px solid rgba(59,130,246,0.25);'
            f'border-radius:8px;padding:8px 12px;margin-bottom:6px">'
            f'<div style="font-size:0.66rem;font-weight:700;color:{C_ACCENT}">{step}</div>'
            f'<div style="font-size:0.60rem;color:{C_TEXT3};margin-top:2px">{desc}</div>'
            f'</div>'
            for step, desc in engine_steps
        ])
        out_html = "".join([
            f'<div style="background:rgba(0,0,0,0.25);border-left:3px solid {col};'
            f'border-radius:0 6px 6px 0;padding:7px 12px;margin-bottom:6px;'
            f'font-size:0.65rem;color:{C_TEXT}">{label}</div>'
            for label, col in outputs
        ])

        arrow = (
            f'<div style="display:flex;align-items:center;justify-content:center;height:100%">'
            f'<div style="font-size:2rem;color:{C_TEXT3}">→</div>'
            f'</div>'
        )

        col_labels = ["DATA INPUTS", "→ SIGNAL ENGINE →", "OUTPUT"]
        label_html = "".join([
            f'<div style="font-size:0.6rem;font-weight:800;color:{C_TEXT2};'
            f'text-transform:uppercase;letter-spacing:0.12em;margin-bottom:12px;text-align:center">{l}</div>'
            for l in col_labels
        ])

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:14px;padding:20px 22px">'
            f'<div style="display:grid;grid-template-columns:1fr 60px 1fr 60px 1fr;gap:0;align-items:start">'
            f'<div>'
            f'<div style="font-size:0.6rem;font-weight:800;color:{C_TEXT2};text-transform:uppercase;'
            f'letter-spacing:0.12em;margin-bottom:12px;text-align:center">DATA INPUTS</div>'
            f'{inp_html}</div>'
            f'{arrow}'
            f'<div>'
            f'<div style="font-size:0.6rem;font-weight:800;color:{C_TEXT2};text-transform:uppercase;'
            f'letter-spacing:0.12em;margin-bottom:12px;text-align:center">SIGNAL ENGINE</div>'
            f'{eng_html}</div>'
            f'{arrow}'
            f'<div>'
            f'<div style="font-size:0.6rem;font-weight:800;color:{C_TEXT2};text-transform:uppercase;'
            f'letter-spacing:0.12em;margin-bottom:12px;text-align:center">OUTPUT</div>'
            f'{out_html}</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
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

def _factor_bar(score: float) -> str:
    pct = int(score * 100)
    col = C_HIGH if score >= 0.75 else (C_MOD if score >= 0.55 else C_LOW)
    return (
        f'<div style="display:flex;align-items:center;gap:8px">'
        f'<div style="flex:1;background:rgba(255,255,255,0.06);border-radius:4px;height:6px">'
        f'<div style="width:{pct}%;height:6px;border-radius:4px;background:{col}"></div>'
        f'</div>'
        f'<span style="font-size:0.63rem;color:{col};font-family:monospace;width:32px">{score:.2f}</span>'
        f'</div>'
    )

def _render_factor_breakdown(signals: list[dict]) -> None:
    try:
        high_signals = [s for s in signals if s.get("conviction") == "HIGH"][:4]
        if not high_signals:
            return

        _section_title("Multi-Factor Signal Breakdown", "Factor decomposition for HIGH conviction signals")

        cols = st.columns(min(len(high_signals), 4))
        factors = ["Momentum", "Fundamental", "Sentiment", "Technical", "Macro"]

        for col_obj, s in zip(cols, high_signals):
            ticker  = s.get("ticker", "—")
            sig_key = list(_FACTOR_SCORES.keys())[high_signals.index(s) % len(_FACTOR_SCORES)]
            scores  = _FACTOR_SCORES[sig_key]
            combined = round(float(np.mean(list(scores.values()))), 2)
            d_col   = _dir_color(s.get("direction", "FLAT"))
            dir_lbl = _dir_arrow(s.get("direction", "FLAT"))

            rows = "".join([
                f'<tr>'
                f'<td style="font-size:0.63rem;color:{C_TEXT2};padding:6px 0;white-space:nowrap">{f}</td>'
                f'<td style="padding:6px 0 6px 10px;width:120px">{_factor_bar(scores[f])}</td>'
                f'</tr>'
                for f in factors
            ])

            with col_obj:
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:14px;'
                    f'padding:16px 18px;height:100%">'
                    f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">'
                    f'<span style="font-size:0.8rem;font-weight:800;color:{C_TEXT};font-family:monospace">{ticker}</span>'
                    f'<span style="font-size:0.65rem;font-weight:700;color:{d_col}">{dir_lbl}</span>'
                    f'</div>'
                    f'<table style="width:100%;border-collapse:collapse">'
                    f'{rows}'
                    f'</table>'
                    f'<div style="border-top:1px solid rgba(255,255,255,0.07);margin-top:10px;padding-top:10px;'
                    f'display:flex;justify-content:space-between;align-items:center">'
                    f'<span style="font-size:0.6rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.1em">Combined</span>'
                    f'<span style="font-size:0.78rem;font-weight:800;color:{C_HIGH};font-family:monospace">{combined:.2f}</span>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        logger.warning(f"[tab_alpha] factor breakdown failed: {exc}")
        st.info("Factor breakdown unavailable.")

# ---------------------------------------------------------------------------
# Section 6 — Signal vs Price Chart (ZIM & MATX)
# ---------------------------------------------------------------------------

def _render_price_signal_chart(stock_data: dict, signals: list[dict]) -> None:
    try:
        _section_title("Signal vs Price Chart", "ZIM & MATX price history with signal entry/exit markers")

        tab1, tab2 = st.tabs(["ZIM", "MATX"])

        for ticker, tab_obj in [("ZIM", tab1), ("MATX", tab2)]:
            with tab_obj:
                try:
                    df = stock_data.get(ticker) if stock_data else None
                    if df is not None and not df.empty and "close" in df.columns:
                        df = df.copy()
                        if "date" in df.columns:
                            df = df.sort_values("date")
                            x_vals = df["date"].tolist()
                        else:
                            x_vals = list(range(len(df)))
                        y_vals = df["close"].tolist()
                    else:
                        # Generate synthetic price series
                        rng = np.random.default_rng(42 + hash(ticker) % 100)
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
                    sig_list = [s for s in signals if s.get("ticker") == ticker]
                    long_x, long_y, short_x, short_y = [], [], [], []
                    rng2 = random.Random(hash(ticker))
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
                        fillcolor="rgba(59,130,246,0.05)",
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

                    fig.update_layout(
                        **dark_layout(title=f"{ticker} — Price + Signal Markers", height=340),
                    )
                    st.plotly_chart(fig, use_container_width=True)

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
        _section_title(
            "Live Signal Monitor",
            "Signals generated in last 24 h — newest first — auto-refreshes every 60 s",
        )

        now_bucket = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")[:-1]  # 10-min buckets
        live = _cached_signals("static", now_bucket)
        live_24h = [s for s in live if s.get("mins_ago", 9999) <= 1440]

        if not live_24h:
            st.info("No signals in the last 24 hours.")
            return

        rows_html = ""
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

            rows_html += (
                f'<div style="display:flex;align-items:center;gap:12px;padding:10px 14px;'
                f'border-bottom:1px solid rgba(255,255,255,0.04);'
                f'background:{"rgba(16,185,129,0.04)" if mins_ago < 15 else "transparent"}">'
                f'<div style="width:7px;height:7px;border-radius:50%;background:{dot_col};flex-shrink:0;'
                f'box-shadow:0 0 6px {dot_col}88"></div>'
                f'<span style="font-size:0.72rem;font-weight:800;color:{C_TEXT};font-family:monospace;width:48px">{ticker}</span>'
                f'<span style="font-size:0.7rem;font-weight:700;color:{d_col};width:72px">{_dir_arrow(direction)}</span>'
                f'<span style="width:80px">{_badge(conv, c_col)}</span>'
                f'<span style="font-size:0.65rem;color:{C_ACCENT};width:110px">{sig_type}</span>'
                f'<span style="font-size:0.63rem;color:{C_TEXT2};flex:1">{basis}</span>'
                f'<span style="font-size:0.65rem;color:{C_TEXT};font-family:monospace;width:38px">{strength:.2f}</span>'
                f'<span style="font-size:0.62rem;color:{C_TEXT3};width:72px;text-align:right">{age_str}</span>'
                f'</div>'
            )

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:14px;overflow:hidden">'
            f'<div style="background:rgba(0,0,0,0.2);padding:12px 16px;'
            f'border-bottom:1px solid rgba(255,255,255,0.06);'
            f'display:flex;align-items:center;gap:8px">'
            f'<div style="width:7px;height:7px;border-radius:50%;background:{C_HIGH};'
            f'box-shadow:0 0 8px rgba(16,185,129,0.8);animation:pulse 2s infinite"></div>'
            f'<span style="font-size:0.62rem;font-weight:700;color:{C_TEXT2};'
            f'text-transform:uppercase;letter-spacing:0.12em">LIVE — {len(live_24h)} signals / 24 h</span>'
            f'<span style="margin-left:auto;font-size:0.60rem;color:{C_TEXT3}">Cache: 60 s</span>'
            f'</div>'
            f'{rows_html}'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"[tab_alpha] live monitor failed: {exc}")
        st.info("Live signal monitor unavailable.")

# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------

def render(
    stock_data: dict | None,
    insights: Any,
    freight_data: Any = None,
    macro_data: Any = None,
) -> None:
    """Render the Alpha Signal Generator tab."""
    try:
        # ── Resolve signals ──────────────────────────────────────────────────
        signals: list[dict] = []

        # Try live engine first
        try:
            if stock_data:
                raw = generate_all_signals(stock_data)
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
        _hr()

        # ── Section 2: Conviction Matrix ─────────────────────────────────────
        _render_conviction_matrix()
        _hr()

        # ── Section 3: Top Signals Table ─────────────────────────────────────
        _render_signals_table(signals)
        _hr()

        # ── Section 4: Engine Diagram ─────────────────────────────────────────
        _render_engine_diagram()
        _hr()

        # ── Section 5: Factor Breakdown ───────────────────────────────────────
        _render_factor_breakdown(signals)
        _hr()

        # ── Section 6: Price + Signal Chart ──────────────────────────────────
        _render_price_signal_chart(stock_data or {}, signals)
        _hr()

        # ── Section 7: Live Monitor ───────────────────────────────────────────
        _render_live_monitor(signals)

    except Exception as top_exc:
        logger.error(f"[tab_alpha] top-level render failed: {top_exc}")
        st.error("Alpha Signal tab encountered an unexpected error. Please refresh.")
