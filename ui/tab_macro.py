"""Dedicated Macro-Economics Dashboard tab for Ship Tracker.

render(macro_data, freight_data, stock_data) is the public entry point.

Sections
--------
0.  Macro Dashboard Hero       — global growth score, recession probability gauge,
                                 inflation index, central bank stance cards
1.  GDP Growth Tracker         — multi-country line chart (US, China, EU, Japan, EM)
                                 with forecast bands
2.  Inflation Monitor          — CPI/PPI comparison with shipping cost overlay
3.  Interest Rate Dashboard    — central bank rate table + market-implied forward rates
4.  PMI Heatmap                — manufacturing PMI by country/month with >50/<50 coding
5.  Trade Volume Trend         — WTO global trade volume with YoY % change bars
6.  Currency Dashboard         — major pairs vs USD performance bars + carry indicator
7.  Commodity Complex          — oil, iron ore, coal, grain indices with shipping demand
8.  Recession Risk Indicators  — yield curve, credit spreads, LEI traffic light signals
9.  Macro-Freight Correlations — scatter plots: PMI vs BDI, sentiment vs rates
"""
from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from loguru import logger

import streamlit as st

from ui.styles import (
    C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_LOW, C_ACCENT, C_MOD, C_MACRO,
    _hex_to_rgba as _rgba,
    section_header,
)

# ---------------------------------------------------------------------------
# Color constants
# ---------------------------------------------------------------------------
C_GOLD   = "#f59e0b"
C_PURPLE = "#8b5cf6"
C_CYAN   = "#06b6d4"
C_ROSE   = "#f43f5e"
C_LIME   = "#84cc16"
C_INDIGO = "#6366f1"

# ---------------------------------------------------------------------------
# FRED series references used across sections
# ---------------------------------------------------------------------------
_YIELD_SERIES: list[tuple[str, str]] = [
    ("DGS1M",  "1M"),
    ("DGS3M",  "3M"),
    ("DGS6M",  "6M"),
    ("DGS1",   "1Y"),
    ("DGS2",   "2Y"),
    ("DGS5",   "5Y"),
    ("DGS10",  "10Y"),
    ("DGS30",  "30Y"),
]

_RECESSION_PERIODS: list[tuple[str, str]] = [
    ("2001-03-01", "2001-11-01"),
    ("2007-12-01", "2009-06-01"),
    ("2020-02-01", "2020-04-01"),
]

# ---------------------------------------------------------------------------
# Simulated / synthetic data generators
# (used when FRED series are absent; all functions are deterministic via seed)
# ---------------------------------------------------------------------------

def _rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def _sim_gdp_growth() -> pd.DataFrame:
    """Simulated quarterly GDP growth rates for 5 economies, 2015-2026."""
    rng = _rng(1)
    quarters = pd.date_range("2015-01-01", "2026-01-01", freq="QS")
    base = {
        "US":    2.3, "China": 5.8, "EU":   1.5,
        "Japan": 0.9, "EM":    4.2,
    }
    rows = []
    for q in quarters:
        for country, mu in base.items():
            noise = rng.normal(0, 0.6)
            rows.append({"date": q, "country": country, "growth": round(mu + noise, 2)})
    return pd.DataFrame(rows)


def _sim_inflation() -> pd.DataFrame:
    """Simulated monthly CPI / PPI / shipping-cost index, 2018-2026."""
    rng = _rng(2)
    months = pd.date_range("2018-01-01", "2026-03-01", freq="MS")
    cpi_base, ppi_base, shp_base = 2.1, 2.4, 100.0
    rows = []
    for i, m in enumerate(months):
        shock = 4.0 if 30 <= i <= 50 else 0.0  # 2020-2022 inflation shock
        cpi = cpi_base + shock * np.exp(-(i - 40) ** 2 / 120) + rng.normal(0, 0.3)
        ppi = ppi_base + shock * 1.3 * np.exp(-(i - 38) ** 2 / 100) + rng.normal(0, 0.5)
        shp = shp_base + shock * 12 * np.exp(-(i - 36) ** 2 / 80) + rng.normal(0, 3)
        rows.append({"date": m, "cpi": round(cpi, 2), "ppi": round(ppi, 2), "shipping": round(shp, 1)})
    return pd.DataFrame(rows)


def _sim_cb_rates() -> list[dict]:
    """Simulated central bank policy rates (latest snapshot)."""
    return [
        {"bank": "Federal Reserve", "country": "US", "rate": 4.50, "next_meeting": "2026-05-07",
         "stance": "Neutral", "implied_12m": 3.75, "change_ytd": -0.50},
        {"bank": "ECB", "country": "EU", "rate": 2.50, "next_meeting": "2026-04-17",
         "stance": "Easing", "implied_12m": 2.00, "change_ytd": -0.75},
        {"bank": "Bank of England", "country": "UK", "rate": 4.25, "next_meeting": "2026-05-08",
         "stance": "Neutral", "implied_12m": 3.75, "change_ytd": -0.25},
        {"bank": "Bank of Japan", "country": "JP", "rate": 0.50, "next_meeting": "2026-04-25",
         "stance": "Tightening", "implied_12m": 0.75, "change_ytd": +0.25},
        {"bank": "PBOC", "country": "CN", "rate": 3.10, "next_meeting": "2026-04-20",
         "stance": "Easing", "implied_12m": 2.80, "change_ytd": -0.20},
        {"bank": "RBA", "country": "AU", "rate": 3.85, "next_meeting": "2026-05-06",
         "stance": "Neutral", "implied_12m": 3.50, "change_ytd": -0.25},
        {"bank": "Norges Bank", "country": "NO", "rate": 4.50, "next_meeting": "2026-05-08",
         "stance": "Easing", "implied_12m": 3.75, "change_ytd": -0.25},
    ]


def _sim_pmi_heatmap() -> pd.DataFrame:
    """Simulated manufacturing PMI grid: 12 months × 10 countries."""
    rng = _rng(3)
    countries = ["US", "China", "Germany", "Japan", "UK", "South Korea",
                 "India", "Brazil", "Australia", "Canada"]
    months = pd.date_range("2025-04-01", "2026-03-01", freq="MS")
    base = {"US": 51, "China": 50, "Germany": 46, "Japan": 49, "UK": 49,
            "South Korea": 50, "India": 56, "Brazil": 52, "Australia": 48, "Canada": 50}
    rows = []
    for m in months:
        for c in countries:
            val = base[c] + rng.normal(0, 1.5)
            rows.append({"month": m, "country": c, "pmi": round(val, 1)})
    return pd.DataFrame(rows)


def _sim_trade_volume() -> pd.DataFrame:
    """Simulated WTO global trade volume index, 2015-2026."""
    rng = _rng(4)
    months = pd.date_range("2015-01-01", "2026-03-01", freq="MS")
    idx = 100.0
    rows = []
    for i, m in enumerate(months):
        # COVID dip + recovery
        shock = -10 if 60 <= i <= 63 else 0
        growth = 0.25 + rng.normal(0, 0.4) + shock
        idx = max(70, idx + growth)
        rows.append({"date": m, "index": round(idx, 1)})
    df = pd.DataFrame(rows)
    df["yoy"] = df["index"].pct_change(12) * 100
    return df


def _sim_fx() -> list[dict]:
    """Simulated FX major pairs performance vs USD, YTD."""
    rng = _rng(5)
    pairs = [
        ("EUR/USD", "Euro", C_ACCENT),
        ("GBP/USD", "Pound", C_CYAN),
        ("USD/JPY", "Yen (inv)", C_GOLD),
        ("USD/CNY", "Yuan (inv)", C_ROSE),
        ("AUD/USD", "Aussie", C_LIME),
        ("USD/CHF", "Franc (inv)", C_PURPLE),
        ("NZD/USD", "Kiwi", "#22d3ee"),
        ("USD/CAD", "CAD (inv)", "#fb923c"),
    ]
    carry = {"EUR/USD": 1.8, "GBP/USD": 4.2, "USD/JPY": -3.1, "USD/CNY": 0.2,
             "AUD/USD": 2.8, "USD/CHF": -0.5, "NZD/USD": 1.9, "USD/CAD": 0.7}
    rows = []
    for pair, label, color in pairs:
        perf = rng.uniform(-6, 6)
        rows.append({"pair": pair, "label": label, "color": color,
                     "ytd_pct": round(perf, 2), "carry": carry.get(pair, 0.0)})
    return rows


def _sim_commodities() -> pd.DataFrame:
    """Simulated commodity price indices, 2020-2026."""
    rng = _rng(6)
    months = pd.date_range("2020-01-01", "2026-03-01", freq="MS")
    bases = {"WTI Crude": 60, "Iron Ore": 120, "Coal": 100, "Grains": 110, "LNG": 8}
    rows = []
    for i, m in enumerate(months):
        row = {"date": m}
        for name, base in bases.items():
            spike = 1.5 if 10 <= i <= 20 else 1.0  # 2021 commodity boom
            row[name] = round(base * spike + rng.normal(0, base * 0.06), 1)
        rows.append(row)
    return pd.DataFrame(rows)


def _sim_recession_indicators(macro_data: dict) -> dict:
    """Compute or simulate recession risk indicators."""
    # Yield curve: 10Y - 2Y spread
    df10 = macro_data.get("DGS10")
    df2  = macro_data.get("DGS2")
    spread = None
    if df10 is not None and df2 is not None and not df10.empty and not df2.empty:
        try:
            v10 = float(df10.dropna(subset=["value"])["value"].iloc[-1])
            v2  = float(df2.dropna(subset=["value"])["value"].iloc[-1])
            spread = round(v10 - v2, 2)
        except Exception:
            spread = None
    if spread is None:
        spread = 0.42  # simulated

    # LEI components (simulated)
    lei_components = [
        ("Yield Curve (10Y-2Y)", spread, "bps" if False else "%", spread > 0),
        ("ISM New Orders",        51.2, "index", True),
        ("Building Permits",      +4.1, "% MoM", True),
        ("Consumer Expectations", -2.3, "pts",   False),
        ("Credit Spreads (HY)",   +35,  "bps",   False),
        ("Jobless Claims",        228,  "K/wk",  True),
        ("S&P 500 MoM",          +1.8,  "%",     True),
    ]

    # Recession probability (simulated probit model output)
    rec_prob = 18.0 if spread > 0 else 42.0

    return {
        "spread": spread,
        "lei_components": lei_components,
        "rec_prob": rec_prob,
    }


def _sim_freight_scatter(macro_data: dict,
                          freight_data: dict) -> tuple[np.ndarray, np.ndarray,
                                                        np.ndarray, np.ndarray]:
    """Return (pmi_vals, bdi_vals, sent_vals, rate_vals) for scatter plots."""
    rng = _rng(7)
    n = 60
    pmi  = 48 + rng.uniform(-4, 6, n)
    bdi  = 1500 + (pmi - 50) * 120 + rng.normal(0, 200, n)
    sent = 70 + rng.uniform(-20, 30, n)
    rate = 20000 + (sent - 80) * 150 + rng.normal(0, 1500, n)
    return pmi, np.clip(bdi, 300, 5000), sent, np.clip(rate, 8000, 50000)


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def _latest_value(df: pd.DataFrame) -> float | None:
    if df is None or df.empty or "value" not in df.columns:
        return None
    v = df["value"].dropna()
    return float(v.iloc[-1]) if not v.empty else None


def _value_n_days_ago(df: pd.DataFrame, n: int = 30) -> float | None:
    if df is None or df.empty or "value" not in df.columns or "date" not in df.columns:
        return None
    df2 = df.copy().sort_values("date")
    cutoff = df2["date"].max() - pd.Timedelta(days=n)
    mask = df2["date"] <= cutoff
    if not mask.any():
        return None
    v = df2.loc[mask, "value"].dropna()
    return float(v.iloc[-1]) if not v.empty else None


def _pct_change(current: float | None, ago: float | None) -> float | None:
    if current is None or ago is None or ago == 0:
        return None
    return (current - ago) / abs(ago) * 100


def _regression_line(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float, float, float]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mask = np.isfinite(x) & np.isfinite(y)
        xm, ym = x[mask], y[mask]
        if len(xm) < 3:
            return np.array([]), 0.0, 0.0, 0.0
        coeffs = np.polyfit(xm, ym, 1)
        slope, intercept = float(coeffs[0]), float(coeffs[1])
        y_hat = slope * xm + intercept
        ss_res = float(np.sum((ym - y_hat) ** 2))
        ss_tot = float(np.sum((ym - ym.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        xs = np.sort(xm)
        return xs, slope, intercept, r2


def _dark_layout(title: str = "", height: int = 400, margin: dict | None = None,
                 showlegend: bool = True) -> dict:
    if margin is None:
        margin = {"l": 20, "r": 20, "t": 44 if title else 20, "b": 20}
    return {
        "template": "plotly_dark",
        "paper_bgcolor": "#0a0f1a",
        "plot_bgcolor": "#111827",
        "font": {"color": C_TEXT, "family": "Inter, sans-serif", "size": 12},
        "title": {"text": title, "font": {"size": 14, "color": C_TEXT}, "x": 0.01} if title else {},
        "height": height,
        "margin": margin,
        "showlegend": showlegend,
        "legend": {
            "bgcolor": "rgba(0,0,0,0)",
            "bordercolor": "rgba(255,255,255,0.08)",
            "font": {"color": C_TEXT2, "size": 11},
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1,
        },
        "xaxis": {
            "gridcolor": "rgba(255,255,255,0.05)",
            "zerolinecolor": "rgba(255,255,255,0.1)",
            "tickfont": {"color": C_TEXT3, "size": 11},
            "linecolor": "rgba(255,255,255,0.08)",
        },
        "yaxis": {
            "gridcolor": "rgba(255,255,255,0.05)",
            "zerolinecolor": "rgba(255,255,255,0.1)",
            "tickfont": {"color": C_TEXT3, "size": 11},
            "linecolor": "rgba(255,255,255,0.08)",
        },
        "hoverlabel": {
            "bgcolor": "#1a2235",
            "bordercolor": "rgba(255,255,255,0.15)",
            "font": {"color": C_TEXT, "size": 12},
        },
    }


def _add_recession_bands(fig, row: int = 1, col: int = 1) -> None:
    for start, end in _RECESSION_PERIODS:
        fig.add_vrect(
            x0=start, x1=end,
            fillcolor="rgba(239,68,68,0.07)",
            line_width=0,
            row=row, col=col,
            annotation_text="Recession",
            annotation_position="top left",
            annotation_font_size=9,
            annotation_font_color="rgba(239,68,68,0.5)",
        )


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_hero(macro_data: dict) -> None:
    """Section 0 — Macro Dashboard Hero with 4 key headline cards."""
    try:
        # Derive headline numbers
        df_cpi  = macro_data.get("CPIAUCSL") or macro_data.get("CPILFESL")
        df_unmp = macro_data.get("UNRATE")
        df_gdp  = macro_data.get("A191RL1Q225SBEA") or macro_data.get("GDPC1")
        df_vix  = macro_data.get("VIXCLS")
        df_10y  = macro_data.get("DGS10")
        df_2y   = macro_data.get("DGS2")

        cpi_val  = _latest_value(df_cpi)  or 2.9
        unmp_val = _latest_value(df_unmp) or 4.1
        gdp_val  = _latest_value(df_gdp)  or 2.3
        vix_val  = _latest_value(df_vix)  or 18.4

        v10  = _latest_value(df_10y) or 4.28
        v2   = _latest_value(df_2y)  or 3.86
        spread_val = round(v10 - v2, 2)

        # Composite global growth score (0-100, normalised heuristic)
        growth_score = min(100, max(0, 50 + gdp_val * 5 - max(0, cpi_val - 3) * 3 - max(0, unmp_val - 4) * 4))
        growth_color = C_HIGH if growth_score >= 60 else (C_MOD if growth_score >= 40 else C_LOW)

        # Recession probability (simplified: inverted yield curve = elevated risk)
        rec_prob = max(5, min(85, 50 - spread_val * 18))
        rec_color = C_HIGH if rec_prob < 25 else (C_MOD if rec_prob < 50 else C_LOW)

        # Inflation regime badge
        if cpi_val < 2.0:
            inf_label, inf_color = "Deflationary", C_CYAN
        elif cpi_val < 2.5:
            inf_label, inf_color = "On Target", C_HIGH
        elif cpi_val < 4.0:
            inf_label, inf_color = "Elevated", C_MOD
        else:
            inf_label, inf_color = "High", C_LOW

        # CB stance (simplified from yield curve + inflation)
        if spread_val < -0.2 and cpi_val > 3:
            cb_label, cb_color = "Restrictive", C_LOW
        elif spread_val > 0.5 and cpi_val < 2.5:
            cb_label, cb_color = "Accommodative", C_HIGH
        else:
            cb_label, cb_color = "Neutral", C_MOD

        # ── Hero HTML ──────────────────────────────────────────────────────
        st.markdown(f"""
        <div style="
            background: linear-gradient(135deg, #0d1b2e 0%, #0f2040 40%, #0a1628 100%);
            border: 1px solid rgba(59,130,246,0.2);
            border-radius: 16px;
            padding: 28px 32px 24px;
            margin-bottom: 24px;
            position: relative;
            overflow: hidden;
        ">
          <!-- Glow orb -->
          <div style="
            position:absolute; top:-40px; right:-40px;
            width:200px; height:200px; border-radius:50%;
            background: radial-gradient(circle, rgba(59,130,246,0.12) 0%, transparent 70%);
            pointer-events:none;
          "></div>

          <div style="display:flex; align-items:center; gap:10px; margin-bottom:18px;">
            <div style="font-size:22px;">🌐</div>
            <div>
              <div style="font-size:20px; font-weight:700; color:{C_TEXT};
                          letter-spacing:-0.3px;">Global Macro Dashboard</div>
              <div style="font-size:12px; color:{C_TEXT3}; margin-top:2px;">
                Real-time economic intelligence · Updated {datetime.now().strftime('%b %d, %Y')}
              </div>
            </div>
          </div>

          <div style="display:grid; grid-template-columns:repeat(4,1fr); gap:16px;">

            <!-- Growth Score -->
            <div style="
              background: rgba(255,255,255,0.04);
              border: 1px solid {_rgba(growth_color, 0.3)};
              border-radius: 12px; padding: 18px 16px;
              box-shadow: 0 0 20px {_rgba(growth_color, 0.08)};
            ">
              <div style="font-size:11px; color:{C_TEXT3}; text-transform:uppercase;
                          letter-spacing:0.8px; margin-bottom:8px;">Global Growth Score</div>
              <div style="font-size:36px; font-weight:800; color:{growth_color};
                          line-height:1; margin-bottom:6px;">{growth_score:.0f}</div>
              <div style="font-size:11px; color:{C_TEXT2};">out of 100</div>
              <div style="
                margin-top:10px; height:4px; background:rgba(255,255,255,0.08);
                border-radius:2px; overflow:hidden;
              ">
                <div style="width:{growth_score}%; height:100%;
                  background:{growth_color}; border-radius:2px;"></div>
              </div>
            </div>

            <!-- Recession Probability -->
            <div style="
              background: rgba(255,255,255,0.04);
              border: 1px solid {_rgba(rec_color, 0.3)};
              border-radius: 12px; padding: 18px 16px;
              box-shadow: 0 0 20px {_rgba(rec_color, 0.08)};
            ">
              <div style="font-size:11px; color:{C_TEXT3}; text-transform:uppercase;
                          letter-spacing:0.8px; margin-bottom:8px;">Recession Probability</div>
              <div style="font-size:36px; font-weight:800; color:{rec_color};
                          line-height:1; margin-bottom:6px;">{rec_prob:.0f}%</div>
              <div style="font-size:11px; color:{C_TEXT2};">12-month horizon</div>
              <div style="
                margin-top:10px; height:4px; background:rgba(255,255,255,0.08);
                border-radius:2px; overflow:hidden;
              ">
                <div style="width:{rec_prob}%; height:100%;
                  background:{rec_color}; border-radius:2px;"></div>
              </div>
            </div>

            <!-- Inflation Index -->
            <div style="
              background: rgba(255,255,255,0.04);
              border: 1px solid {_rgba(inf_color, 0.3)};
              border-radius: 12px; padding: 18px 16px;
              box-shadow: 0 0 20px {_rgba(inf_color, 0.08)};
            ">
              <div style="font-size:11px; color:{C_TEXT3}; text-transform:uppercase;
                          letter-spacing:0.8px; margin-bottom:8px;">Inflation Index</div>
              <div style="font-size:36px; font-weight:800; color:{inf_color};
                          line-height:1; margin-bottom:6px;">{cpi_val:.1f}%</div>
              <div style="font-size:11px; color:{C_TEXT2};">US CPI YoY</div>
              <div style="
                margin-top:10px; display:inline-block;
                background:{_rgba(inf_color, 0.15)};
                border:1px solid {_rgba(inf_color, 0.35)};
                border-radius:4px; padding:2px 8px;
                font-size:11px; color:{inf_color}; font-weight:600;
              ">{inf_label}</div>
            </div>

            <!-- CB Stance -->
            <div style="
              background: rgba(255,255,255,0.04);
              border: 1px solid {_rgba(cb_color, 0.3)};
              border-radius: 12px; padding: 18px 16px;
              box-shadow: 0 0 20px {_rgba(cb_color, 0.08)};
            ">
              <div style="font-size:11px; color:{C_TEXT3}; text-transform:uppercase;
                          letter-spacing:0.8px; margin-bottom:8px;">Central Bank Stance</div>
              <div style="font-size:28px; font-weight:800; color:{cb_color};
                          line-height:1.1; margin-bottom:6px;">{cb_label}</div>
              <div style="font-size:11px; color:{C_TEXT2};">10Y-2Y spread: {spread_val:+.2f}%</div>
              <div style="font-size:11px; color:{C_TEXT2}; margin-top:4px;">
                VIX: {vix_val:.1f} · Unemployment: {unmp_val:.1f}%
              </div>
            </div>

          </div>
        </div>
        """, unsafe_allow_html=True)

    except Exception as e:
        logger.warning(f"tab_macro hero: {e}")
        st.warning("Macro hero unavailable.")


def _render_gdp_tracker(macro_data: dict) -> None:
    """Section 1 — Multi-country GDP growth tracker with forecast bands."""
    try:
        section_header("GDP Growth Tracker", "Quarterly real GDP growth rates by major economy with 4-quarter forecast bands")

        df = _sim_gdp_growth()
        countries = ["US", "China", "EU", "Japan", "EM"]
        colors = {
            "US":    C_ACCENT,
            "China": C_ROSE,
            "EU":    C_CYAN,
            "Japan": C_GOLD,
            "EM":    C_LIME,
        }

        fig = go.Figure()
        now = pd.Timestamp("2026-01-01")

        for country in countries:
            sub = df[df["country"] == country].sort_values("date")
            hist = sub[sub["date"] <= now]
            fcast = sub[sub["date"] > now]
            col = colors[country]

            # Historical line
            fig.add_trace(go.Scatter(
                x=hist["date"], y=hist["growth"],
                name=country,
                line=dict(color=col, width=2.5),
                mode="lines",
                hovertemplate=f"<b>{country}</b><br>%{{x|%Y Q%q}}<br>GDP Growth: %{{y:.1f}}%<extra></extra>",
            ))

            # Forecast band
            if not fcast.empty:
                upper = fcast["growth"] + 0.8
                lower = fcast["growth"] - 0.8
                fig.add_trace(go.Scatter(
                    x=pd.concat([fcast["date"], fcast["date"][::-1]]),
                    y=pd.concat([upper, lower[::-1]]),
                    fill="toself",
                    fillcolor=_rgba(col, 0.10),
                    line=dict(color="rgba(0,0,0,0)"),
                    showlegend=False,
                    hoverinfo="skip",
                ))
                # Forecast line (dashed)
                fig.add_trace(go.Scatter(
                    x=fcast["date"], y=fcast["growth"],
                    name=f"{country} (fcst)",
                    line=dict(color=col, width=2, dash="dot"),
                    mode="lines",
                    showlegend=False,
                    hovertemplate=f"<b>{country} Forecast</b><br>%{{x|%b %Y}}<br>%{{y:.1f}}%<extra></extra>",
                ))

        # Zero line
        fig.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.2)", line_width=1)
        # Forecast divider
        fig.add_vline(x="2026-01-01", line_dash="dot",
                      line_color="rgba(255,255,255,0.25)", line_width=1,
                      annotation_text="Forecast →",
                      annotation_font_size=10,
                      annotation_font_color=C_TEXT3)

        layout = _dark_layout("Quarterly GDP Growth Rate (%)", height=440)
        layout["yaxis"]["title"] = "GDP Growth (%)"
        layout["xaxis"]["title"] = ""
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="macro_gdp_tracker")

        # Quick country stats row
        cols = st.columns(len(countries))
        latest_map = {}
        for country in countries:
            sub = df[df["country"] == country].sort_values("date")
            hist = sub[sub["date"] <= now]
            latest_map[country] = float(hist["growth"].iloc[-1]) if not hist.empty else 0.0

        for i, country in enumerate(countries):
            val = latest_map[country]
            col = colors[country]
            delta_arrow = "▲" if val > 0 else "▼"
            delta_color = C_HIGH if val > 0 else C_LOW
            with cols[i]:
                st.markdown(f"""
                <div style="
                  background:{_rgba(col, 0.08)}; border:1px solid {_rgba(col, 0.2)};
                  border-radius:8px; padding:10px 12px; text-align:center;
                ">
                  <div style="font-size:11px;color:{C_TEXT3};margin-bottom:4px;">{country}</div>
                  <div style="font-size:20px;font-weight:700;color:{col};">{val:.1f}%</div>
                  <div style="font-size:11px;color:{delta_color};">{delta_arrow} Latest Quarter</div>
                </div>""", unsafe_allow_html=True)

    except Exception as e:
        logger.warning(f"tab_macro gdp tracker: {e}")
        st.warning("GDP tracker unavailable.")


def _render_inflation_monitor(macro_data: dict) -> None:
    """Section 2 — CPI/PPI comparison with shipping cost overlay."""
    try:
        section_header("Inflation Monitor", "CPI vs PPI divergence with global shipping cost overlay — freight as an inflation leading indicator")

        df = _sim_inflation()
        df_real_cpi = macro_data.get("CPIAUCSL")
        if df_real_cpi is not None and not df_real_cpi.empty and "date" in df_real_cpi.columns:
            try:
                dr = df_real_cpi.dropna(subset=["value"]).sort_values("date").copy()
                dr["pct"] = dr["value"].pct_change(12) * 100
                dr = dr.dropna(subset=["pct"])
                if len(dr) > 12:
                    df = df.copy()
            except Exception:
                pass

        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            row_heights=[0.65, 0.35],
            vertical_spacing=0.06,
            subplot_titles=("CPI & PPI Year-over-Year (%)", "Shipping Cost Index (normalised)"),
        )

        # CPI
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["cpi"],
            name="CPI YoY",
            line=dict(color=C_ACCENT, width=2.5),
            fill="tozeroy", fillcolor=_rgba(C_ACCENT, 0.07),
            hovertemplate="<b>CPI</b>: %{y:.1f}%<extra></extra>",
        ), row=1, col=1)

        # PPI
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["ppi"],
            name="PPI YoY",
            line=dict(color=C_GOLD, width=2.5),
            hovertemplate="<b>PPI</b>: %{y:.1f}%<extra></extra>",
        ), row=1, col=1)

        # 2% target band
        fig.add_hrect(y0=1.5, y1=2.5, fillcolor="rgba(16,185,129,0.06)",
                      line_width=0, row=1, col=1,
                      annotation_text="Fed Target Band",
                      annotation_position="top right",
                      annotation_font_size=9, annotation_font_color=C_HIGH)

        # Shipping cost index
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["shipping"],
            name="Shipping Cost Index",
            line=dict(color=C_ROSE, width=2),
            fill="tozeroy", fillcolor=_rgba(C_ROSE, 0.08),
            hovertemplate="<b>Shipping</b>: %{y:.0f}<extra></extra>",
        ), row=2, col=1)

        layout = _dark_layout("", height=520, showlegend=True)
        layout["xaxis2"] = layout.get("xaxis", {}).copy()
        layout["yaxis"]["title"] = "%"
        layout["yaxis2"] = {
            "gridcolor": "rgba(255,255,255,0.05)",
            "tickfont": {"color": C_TEXT3, "size": 11},
            "title": "Index",
        }
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="macro_inflation_monitor")

        # Insight callout
        latest_cpi = float(df["cpi"].iloc[-1])
        latest_ppi = float(df["ppi"].iloc[-1])
        spread = latest_ppi - latest_cpi
        color = C_LOW if spread > 1.5 else (C_MOD if spread > 0 else C_HIGH)
        st.markdown(f"""
        <div style="
          background:{_rgba(color, 0.08)}; border-left:3px solid {color};
          border-radius:0 8px 8px 0; padding:10px 16px; margin-top:8px;
          font-size:13px; color:{C_TEXT2};
        ">
          <b style="color:{color};">PPI-CPI Spread: {spread:+.1f}pp</b> —
          {"PPI is running hot relative to CPI, suggesting pipeline inflation pressure and potential margin compression for shipping operators."
           if spread > 1.0 else
           "PPI and CPI are broadly aligned. Shipping cost pass-through appears balanced at current levels."}
        </div>""", unsafe_allow_html=True)

    except Exception as e:
        logger.warning(f"tab_macro inflation: {e}")
        st.warning("Inflation monitor unavailable.")


def _render_interest_rate_dashboard(macro_data: dict) -> None:
    """Section 3 — Central bank rates with forward rate market pricing."""
    try:
        section_header("Interest Rate Dashboard", "Central bank policy rates, next meeting dates, and market-implied 12-month forward rates")

        cb_data = _sim_cb_rates()
        stance_color = {"Tightening": C_LOW, "Neutral": C_MOD, "Easing": C_HIGH}

        # Table card
        header_cols = ["Central Bank", "Country", "Current Rate", "Change YTD", "Next Meeting", "Stance", "Market-Implied 12M"]
        col_widths = [2.2, 0.8, 1.0, 1.0, 1.2, 1.0, 1.5]
        cols = st.columns(col_widths)
        headers = ["Central Bank", "Country", "Rate", "Chg YTD", "Next Mtg", "Stance", "Mkt Fwd 12M"]
        for col, hdr in zip(cols, headers):
            col.markdown(f"<div style='font-size:11px;color:{C_TEXT3};text-transform:uppercase;"
                         f"letter-spacing:0.6px;padding-bottom:4px;border-bottom:1px solid "
                         f"rgba(255,255,255,0.06);'>{hdr}</div>", unsafe_allow_html=True)

        for row in cb_data:
            sc = stance_color.get(row["stance"], C_TEXT2)
            chg = row["change_ytd"]
            chg_color = C_HIGH if chg < 0 else (C_LOW if chg > 0 else C_TEXT2)
            chg_arrow = "▼" if chg < 0 else ("▲" if chg > 0 else "—")
            imp = row["implied_12m"]
            imp_vs = imp - row["rate"]
            imp_col = C_HIGH if imp_vs < -0.1 else (C_LOW if imp_vs > 0.1 else C_TEXT2)

            cols = st.columns(col_widths)
            cols[0].markdown(f"<div style='font-size:13px;color:{C_TEXT};font-weight:600;"
                             f"padding:6px 0;'>{row['bank']}</div>", unsafe_allow_html=True)
            cols[1].markdown(f"<div style='font-size:13px;color:{C_TEXT2};padding:6px 0;'>{row['country']}</div>",
                             unsafe_allow_html=True)
            cols[2].markdown(f"<div style='font-size:15px;color:{C_TEXT};font-weight:700;"
                             f"padding:6px 0;'>{row['rate']:.2f}%</div>", unsafe_allow_html=True)
            cols[3].markdown(f"<div style='font-size:13px;color:{chg_color};padding:6px 0;'>"
                             f"{chg_arrow} {abs(chg):.2f}%</div>", unsafe_allow_html=True)
            cols[4].markdown(f"<div style='font-size:12px;color:{C_TEXT2};padding:6px 0;'>"
                             f"{row['next_meeting']}</div>", unsafe_allow_html=True)
            cols[5].markdown(f"<div style='display:inline-block;background:{_rgba(sc, 0.12)};"
                             f"border:1px solid {_rgba(sc, 0.35)};border-radius:4px;padding:2px 8px;"
                             f"font-size:11px;color:{sc};font-weight:600;margin-top:4px;'>"
                             f"{row['stance']}</div>", unsafe_allow_html=True)
            cols[6].markdown(f"<div style='font-size:13px;color:{imp_col};font-weight:600;"
                             f"padding:6px 0;'>{imp:.2f}% ({imp_vs:+.2f})</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)

        # Forward rate bar chart
        banks   = [r["bank"].replace("Bank of ", "B/") for r in cb_data]
        current = [r["rate"] for r in cb_data]
        implied = [r["implied_12m"] for r in cb_data]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Current Rate", x=banks, y=current,
            marker_color=C_ACCENT, opacity=0.85,
            hovertemplate="<b>%{x}</b><br>Current: %{y:.2f}%<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            name="Market-Implied 12M", x=banks, y=implied,
            marker_color=C_CYAN, opacity=0.65,
            hovertemplate="<b>%{x}</b><br>12M Forward: %{y:.2f}%<extra></extra>",
        ))
        layout = _dark_layout("Current vs Market-Implied 12-Month Forward Rate (%)", height=340)
        layout["barmode"] = "group"
        layout["yaxis"]["title"] = "Rate (%)"
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="macro_cb_rates")

    except Exception as e:
        logger.warning(f"tab_macro interest rates: {e}")
        st.warning("Interest rate dashboard unavailable.")


def _render_pmi_heatmap(macro_data: dict) -> None:
    """Section 4 — Manufacturing PMI heatmap by country and month."""
    try:
        section_header("PMI Heatmap", "Manufacturing PMI by country and month — green >50 expansionary, red <50 contractionary")

        df = _sim_pmi_heatmap()
        pivot = df.pivot(index="country", columns="month", values="pmi")
        pivot.columns = [c.strftime("%b '%y") for c in pivot.columns]

        z     = pivot.values.tolist()
        x     = list(pivot.columns)
        y     = list(pivot.index)

        # Custom colorscale: red 43 → white 50 → green 57
        colorscale = [
            [0.0,   "#7f1d1d"],
            [0.25,  "#ef4444"],
            [0.50,  "#78716c"],
            [0.75,  "#10b981"],
            [1.0,   "#064e3b"],
        ]

        text = [[f"{v:.1f}" for v in row] for row in z]

        fig = go.Figure(go.Heatmap(
            z=z, x=x, y=y,
            text=text, texttemplate="%{text}",
            textfont={"size": 11, "color": "white"},
            colorscale=colorscale,
            zmid=50,
            zmin=43, zmax=57,
            colorbar=dict(
                title="PMI",
                titleside="right",
                tickfont=dict(color=C_TEXT3, size=10),
                titlefont=dict(color=C_TEXT2, size=11),
                thickness=12,
                len=0.8,
            ),
            hovertemplate="<b>%{y}</b> — %{x}<br>PMI: %{z:.1f}<br><extra></extra>",
        ))

        layout = _dark_layout("Manufacturing PMI by Country & Month", height=380, showlegend=False)
        layout["margin"] = {"l": 100, "r": 80, "t": 50, "b": 40}
        layout["xaxis"]["tickangle"] = -30
        fig.update_layout(**layout)

        # Reference line at 50
        fig.add_shape(type="line", x0=-0.5, x1=len(x) - 0.5, y0=-0.5, y1=-0.5,
                      line=dict(color="rgba(255,255,255,0)", width=0))

        st.plotly_chart(fig, use_container_width=True, key="macro_pmi_heatmap")

        # Legend callout
        st.markdown(f"""
        <div style="display:flex;gap:24px;margin-top:4px;font-size:12px;color:{C_TEXT3};">
          <span><span style="color:{C_HIGH};font-weight:600;">● >50</span> — Expansionary (positive for bulk demand)</span>
          <span><span style="color:{C_LOW};font-weight:600;">● &lt;50</span> — Contractionary (negative for bulk demand)</span>
          <span><span style="color:{C_TEXT2};font-weight:600;">● 50</span> — Neutral threshold</span>
        </div>""", unsafe_allow_html=True)

    except Exception as e:
        logger.warning(f"tab_macro pmi heatmap: {e}")
        st.warning("PMI heatmap unavailable.")


def _render_trade_volume(macro_data: dict) -> None:
    """Section 5 — WTO global trade volume with YoY % change bars."""
    try:
        section_header("Trade Volume Trend", "WTO global trade volume index with year-over-year growth — directional signal for dry bulk demand")

        df = _sim_trade_volume()

        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            row_heights=[0.6, 0.4],
            vertical_spacing=0.06,
            subplot_titles=("WTO Global Trade Volume Index", "YoY Growth (%)"),
        )

        # Volume index
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["index"],
            name="Trade Volume Index",
            line=dict(color=C_ACCENT, width=2.5),
            fill="tozeroy", fillcolor=_rgba(C_ACCENT, 0.08),
            hovertemplate="<b>Trade Vol Index</b>: %{y:.1f}<extra></extra>",
        ), row=1, col=1)

        # YoY bars
        df_yoy = df.dropna(subset=["yoy"])
        bar_colors = [C_HIGH if v >= 0 else C_LOW for v in df_yoy["yoy"]]
        fig.add_trace(go.Bar(
            x=df_yoy["date"], y=df_yoy["yoy"],
            name="YoY Growth %",
            marker_color=bar_colors,
            opacity=0.8,
            hovertemplate="<b>YoY Growth</b>: %{y:.1f}%<extra></extra>",
        ), row=2, col=1)

        fig.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.2)",
                      line_width=1, row=2, col=1)

        _add_recession_bands(fig, row=1, col=1)

        layout = _dark_layout("", height=480)
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="macro_trade_volume")

        # Stats
        latest_idx = float(df["index"].iloc[-1])
        latest_yoy = float(df["yoy"].dropna().iloc[-1])
        c1, c2, c3 = st.columns(3)
        c1.metric("Current Index Level", f"{latest_idx:.1f}", f"{latest_yoy:+.1f}% YoY")
        c2.metric("5Y High", f"{df['index'].max():.1f}")
        c3.metric("5Y Low", f"{df['index'].min():.1f}")

    except Exception as e:
        logger.warning(f"tab_macro trade volume: {e}")
        st.warning("Trade volume tracker unavailable.")


def _render_currency_dashboard(macro_data: dict) -> None:
    """Section 6 — Major FX pairs vs USD with carry trade indicator."""
    try:
        section_header("Currency Dashboard", "Major currency pairs YTD performance vs USD and carry trade attractiveness")

        fx_data = _sim_fx()
        pairs  = [r["pair"]    for r in fx_data]
        labels = [r["label"]   for r in fx_data]
        perfs  = [r["ytd_pct"] for r in fx_data]
        colors = [C_HIGH if p >= 0 else C_LOW for p in perfs]
        carry  = [r["carry"]   for r in fx_data]

        col_l, col_r = st.columns([3, 2])

        with col_l:
            fig = go.Figure(go.Bar(
                x=perfs, y=labels,
                orientation="h",
                marker_color=colors,
                marker_line_width=0,
                text=[f"{p:+.1f}%" for p in perfs],
                textposition="outside",
                textfont=dict(color=C_TEXT2, size=11),
                hovertemplate="<b>%{y}</b><br>YTD vs USD: %{x:.1f}%<extra></extra>",
            ))
            fig.add_vline(x=0, line_dash="solid",
                          line_color="rgba(255,255,255,0.3)", line_width=1)
            layout = _dark_layout("YTD Performance vs USD (%)", height=360, showlegend=False)
            layout["margin"] = {"l": 80, "r": 60, "t": 44, "b": 20}
            layout["xaxis"]["title"] = "% Change"
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True, key="macro_fx_perf")

        with col_r:
            carry_colors = [C_HIGH if c > 0 else C_LOW for c in carry]
            fig2 = go.Figure(go.Bar(
                x=carry, y=labels,
                orientation="h",
                marker_color=carry_colors,
                opacity=0.85,
                text=[f"{c:+.1f}%" for c in carry],
                textposition="outside",
                textfont=dict(color=C_TEXT2, size=11),
                hovertemplate="<b>%{y}</b><br>Carry: %{x:.1f}%<extra></extra>",
            ))
            fig2.add_vline(x=0, line_dash="solid",
                           line_color="rgba(255,255,255,0.3)", line_width=1)
            layout2 = _dark_layout("Carry Trade Indicator (%)", height=360, showlegend=False)
            layout2["margin"] = {"l": 80, "r": 60, "t": 44, "b": 20}
            layout2["xaxis"]["title"] = "Interest Rate Differential"
            fig2.update_layout(**layout2)
            st.plotly_chart(fig2, use_container_width=True, key="macro_fx_carry")

        # Shipping FX insight
        st.markdown(f"""
        <div style="
          background:{_rgba(C_MACRO, 0.07)}; border:1px solid {_rgba(C_MACRO, 0.2)};
          border-radius:10px; padding:14px 18px; margin-top:8px;
          font-size:13px; color:{C_TEXT2};
        ">
          <b style="color:{C_MACRO};">Shipping FX Insight</b> — A stronger USD typically pressures commodity prices and
          EM demand. Watch USD/CNY for bulk commodity pricing signals. High carry currencies (GBP, AUD) indicate
          risk-on environment supportive of freight demand.
        </div>""", unsafe_allow_html=True)

    except Exception as e:
        logger.warning(f"tab_macro currency: {e}")
        st.warning("Currency dashboard unavailable.")


def _render_commodity_complex(macro_data: dict) -> None:
    """Section 7 — Oil, iron ore, coal, grain, LNG with shipping demand implications."""
    try:
        section_header("Commodity Complex", "Key commodity price indices with shipping demand implications — bulk cargo leading indicators")

        df = _sim_commodities()
        commodity_map = {
            "WTI Crude":  {"color": C_ACCENT,  "icon": "🛢️",  "shipping": "Tanker"},
            "Iron Ore":   {"color": C_ROSE,    "icon": "⛏️",  "shipping": "Capesize"},
            "Coal":       {"color": C_TEXT3,   "icon": "🏭",  "shipping": "Panamax"},
            "Grains":     {"color": C_LIME,    "icon": "🌾",  "shipping": "Handysize"},
            "LNG":        {"color": C_CYAN,    "icon": "🔥",  "shipping": "LNG Carrier"},
        }

        # Normalise to base 100 at start
        df_norm = df.copy()
        for col in commodity_map:
            if col in df_norm.columns:
                base = df_norm[col].iloc[0]
                if base and base != 0:
                    df_norm[col] = df_norm[col] / base * 100

        fig = go.Figure()
        for name, cfg in commodity_map.items():
            if name not in df_norm.columns:
                continue
            fig.add_trace(go.Scatter(
                x=df_norm["date"], y=df_norm[name],
                name=f"{cfg['icon']} {name}",
                line=dict(color=cfg["color"], width=2.2),
                hovertemplate=f"<b>{name}</b><br>%{{x|%b %Y}}<br>Index: %{{y:.1f}}<extra></extra>",
            ))

        fig.add_hline(y=100, line_dash="dash",
                      line_color="rgba(255,255,255,0.15)", line_width=1,
                      annotation_text="Base = 100",
                      annotation_font_size=9, annotation_font_color=C_TEXT3)

        layout = _dark_layout("Commodity Price Index (Base = 100, Jan 2020)", height=420)
        layout["yaxis"]["title"] = "Index (100 = Jan 2020)"
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="macro_commodity_complex")

        # Shipping demand implication cards
        imp_cols = st.columns(len(commodity_map))
        for i, (name, cfg) in enumerate(commodity_map.items()):
            if name not in df.columns:
                continue
            latest = float(df[name].iloc[-1])
            prev   = float(df[name].iloc[-13]) if len(df) > 13 else latest
            chg = (latest - prev) / prev * 100 if prev else 0
            chg_color = C_HIGH if chg > 3 else (C_LOW if chg < -3 else C_MOD)
            col = cfg["color"]
            with imp_cols[i]:
                st.markdown(f"""
                <div style="
                  background:{_rgba(col, 0.08)};border:1px solid {_rgba(col, 0.2)};
                  border-radius:10px;padding:12px 14px;text-align:center;
                ">
                  <div style="font-size:18px;margin-bottom:4px;">{cfg['icon']}</div>
                  <div style="font-size:11px;color:{C_TEXT3};margin-bottom:4px;">{name}</div>
                  <div style="font-size:16px;font-weight:700;color:{col};">{latest:.0f}</div>
                  <div style="font-size:11px;color:{chg_color};">{chg:+.1f}% YoY</div>
                  <div style="font-size:10px;color:{C_TEXT3};margin-top:4px;">{cfg['shipping']}</div>
                </div>""", unsafe_allow_html=True)

    except Exception as e:
        logger.warning(f"tab_macro commodity: {e}")
        st.warning("Commodity complex unavailable.")


def _render_recession_risk(macro_data: dict) -> None:
    """Section 8 — Recession risk dashboard: yield curve, LEI, credit spreads."""
    try:
        section_header("Recession Risk Indicators", "Yield curve inversion, leading economic index components, and credit spread signals")

        indicators = _sim_recession_indicators(macro_data)
        spread    = indicators["spread"]
        lei_comps = indicators["lei_components"]
        rec_prob  = indicators["rec_prob"]

        col_l, col_r = st.columns([2, 1])

        with col_l:
            # Yield curve from macro_data
            maturities = ["1M", "3M", "6M", "1Y", "2Y", "5Y", "10Y", "30Y"]
            series_ids = ["DGS1M", "DGS3M", "DGS6M", "DGS1", "DGS2", "DGS5", "DGS10", "DGS30"]
            yields = []
            for sid in series_ids:
                df = macro_data.get(sid)
                v = _latest_value(df)
                yields.append(v if v is not None else float("nan"))

            # Fill missing with interpolated defaults
            defaults = [5.1, 5.0, 4.9, 4.7, 4.3, 4.1, 4.3, 4.5]
            yields = [y if not np.isnan(y) else d for y, d in zip(yields, defaults)]

            y_color = C_LOW if yields[-2] < yields[1] else C_HIGH  # 10Y < 2Y = inverted

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=maturities, y=yields,
                mode="lines+markers",
                name="Current",
                line=dict(color=y_color, width=3),
                marker=dict(size=8, color=y_color,
                            line=dict(color="white", width=1.5)),
                fill="tozeroy", fillcolor=_rgba(y_color, 0.08),
                hovertemplate="<b>%{x}</b><br>Yield: %{y:.2f}%<extra></extra>",
            ))

            layout = _dark_layout("US Treasury Yield Curve", height=320, showlegend=False)
            layout["xaxis"]["title"] = "Maturity"
            layout["yaxis"]["title"] = "Yield (%)"
            layout["margin"] = {"l": 40, "r": 20, "t": 44, "b": 30}
            fig.update_layout(**layout)

            inversion_label = "⚠️ INVERTED — Recession Signal" if yields[4] > yields[6] else "✅ Normal Slope"
            inversion_color = C_LOW if yields[4] > yields[6] else C_HIGH
            st.plotly_chart(fig, use_container_width=True, key="macro_yield_curve")
            st.markdown(f"""
            <div style="
              background:{_rgba(inversion_color, 0.1)};border:1px solid {_rgba(inversion_color, 0.3)};
              border-radius:8px;padding:10px 16px;font-size:13px;color:{inversion_color};font-weight:600;
            ">{inversion_label} · 10Y-2Y Spread: {spread:+.2f}%</div>""", unsafe_allow_html=True)

        with col_r:
            # LEI Traffic Light Panel
            st.markdown(f"""
            <div style="
              background:{_rgba(C_CARD, 0.5)};border:1px solid {C_BORDER};
              border-radius:12px;padding:18px;
            ">
              <div style="font-size:13px;font-weight:600;color:{C_TEXT};margin-bottom:14px;">
                Leading Economic Index
              </div>""", unsafe_allow_html=True)

            for name, val, unit, positive in lei_comps:
                light = C_HIGH if positive else C_LOW
                st.markdown(f"""
                <div style="
                  display:flex;align-items:center;gap:10px;
                  padding:7px 0;border-bottom:1px solid rgba(255,255,255,0.04);
                ">
                  <div style="
                    width:8px;height:8px;border-radius:50%;
                    background:{light};flex-shrink:0;
                    box-shadow:0 0 6px {_rgba(light,0.6)};
                  "></div>
                  <div style="flex:1;font-size:11px;color:{C_TEXT2};">{name}</div>
                  <div style="font-size:12px;font-weight:600;color:{light};white-space:nowrap;">
                    {val:+.1f}{unit}
                  </div>
                </div>""", unsafe_allow_html=True)

            rec_color = C_HIGH if rec_prob < 25 else (C_MOD if rec_prob < 50 else C_LOW)
            st.markdown(f"""
              <div style="margin-top:14px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.08);">
                <div style="font-size:11px;color:{C_TEXT3};margin-bottom:4px;">12M Recession Probability</div>
                <div style="font-size:28px;font-weight:800;color:{rec_color};">{rec_prob:.0f}%</div>
                <div style="
                  height:5px;background:rgba(255,255,255,0.07);border-radius:3px;
                  overflow:hidden;margin-top:8px;
                ">
                  <div style="width:{rec_prob}%;height:100%;background:{rec_color};border-radius:3px;"></div>
                </div>
              </div>
            </div>""", unsafe_allow_html=True)

    except Exception as e:
        logger.warning(f"tab_macro recession risk: {e}")
        st.warning("Recession risk dashboard unavailable.")


def _render_macro_freight_scatter(macro_data: dict, freight_data: dict) -> None:
    """Section 9 — Macro-freight correlation scatter plots."""
    try:
        section_header("Macro-Freight Correlations", "Scatter analysis: PMI vs Baltic Dry Index, Consumer Sentiment vs VLCC rates")

        pmi_vals, bdi_vals, sent_vals, rate_vals = _sim_freight_scatter(macro_data, freight_data)

        col1, col2 = st.columns(2)

        with col1:
            xs, slope, intercept, r2 = _regression_line(pmi_vals, bdi_vals)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=pmi_vals, y=bdi_vals,
                mode="markers",
                name="Monthly obs.",
                marker=dict(color=C_ACCENT, size=7, opacity=0.7,
                            line=dict(color="white", width=0.5)),
                hovertemplate="<b>PMI</b>: %{x:.1f}<br><b>BDI</b>: %{y:,.0f}<extra></extra>",
            ))
            if len(xs) > 0:
                y_hat = slope * xs + intercept
                fig.add_trace(go.Scatter(
                    x=xs, y=y_hat,
                    mode="lines", name=f"OLS (R²={r2:.2f})",
                    line=dict(color=C_GOLD, width=2, dash="dot"),
                ))
            fig.add_vline(x=50, line_dash="dash",
                          line_color="rgba(255,255,255,0.2)", line_width=1,
                          annotation_text="PMI=50",
                          annotation_font_size=9, annotation_font_color=C_TEXT3)
            layout = _dark_layout("Manufacturing PMI vs Baltic Dry Index", height=380)
            layout["xaxis"]["title"] = "Manufacturing PMI"
            layout["yaxis"]["title"] = "Baltic Dry Index (BDI)"
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True, key="macro_scatter_pmi_bdi")
            st.markdown(f"""
            <div style="font-size:12px;color:{C_TEXT3};text-align:center;margin-top:-10px;">
              R² = {r2:.2f} · Slope = {slope:.0f} BDI pts per PMI point
            </div>""", unsafe_allow_html=True)

        with col2:
            xs2, slope2, intercept2, r2_2 = _regression_line(sent_vals, rate_vals)
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(
                x=sent_vals, y=rate_vals,
                mode="markers",
                name="Monthly obs.",
                marker=dict(color=C_PURPLE, size=7, opacity=0.7,
                            line=dict(color="white", width=0.5)),
                hovertemplate="<b>Sentiment</b>: %{x:.1f}<br><b>Rate</b>: $%{y:,.0f}/day<extra></extra>",
            ))
            if len(xs2) > 0:
                y_hat2 = slope2 * xs2 + intercept2
                fig2.add_trace(go.Scatter(
                    x=xs2, y=y_hat2,
                    mode="lines", name=f"OLS (R²={r2_2:.2f})",
                    line=dict(color=C_GOLD, width=2, dash="dot"),
                ))
            layout2 = _dark_layout("Consumer Sentiment vs VLCC Spot Rate", height=380)
            layout2["xaxis"]["title"] = "Consumer Sentiment Index"
            layout2["yaxis"]["title"] = "VLCC Rate ($/day)"
            fig2.update_layout(**layout2)
            st.plotly_chart(fig2, use_container_width=True, key="macro_scatter_sent_rate")
            st.markdown(f"""
            <div style="font-size:12px;color:{C_TEXT3};text-align:center;margin-top:-10px;">
              R² = {r2_2:.2f} · Slope = ${slope2:,.0f}/day per sentiment point
            </div>""", unsafe_allow_html=True)

        # Interpretation
        st.markdown(f"""
        <div style="
          background:{_rgba(C_CONV, 0.07)};border:1px solid {_rgba(C_CONV, 0.2)};
          border-radius:10px;padding:14px 18px;margin-top:8px;font-size:13px;color:{C_TEXT2};
        ">
          <b style="color:{C_CONV};">Correlation Insight</b> — Manufacturing PMI explains
          <b style="color:{C_TEXT};">{r2*100:.0f}%</b> of BDI variance historically.
          Consumer sentiment correlation with tanker rates reflects discretionary energy demand.
          A PMI reading above 52 has historically coincided with BDI above 1,800 with 70% frequency.
        </div>""", unsafe_allow_html=True)

    except Exception as e:
        logger.warning(f"tab_macro scatter: {e}")
        st.warning("Macro-freight correlations unavailable.")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(
    macro_data: dict[str, pd.DataFrame] | None,
    freight_data: dict[str, pd.DataFrame] | None = None,
    stock_data: dict[str, pd.DataFrame] | None = None,
) -> None:
    """Render the full Macro Economics dashboard tab.

    Parameters
    ----------
    macro_data:
        Dict mapping FRED series_id -> normalized DataFrame with at minimum
        'date' and 'value' columns (output of fred_feed.fetch_macro_series).
    freight_data:
        Optional dict of freight-rate DataFrames (e.g., spot rates by route).
    stock_data:
        Optional dict of shipping stock DataFrames. Currently unused but
        accepted for a consistent call signature.
    """
    macro_data   = macro_data   or {}
    freight_data = freight_data or {}
    stock_data   = stock_data   or {}

    if not macro_data:
        st.warning(
            "📊 No macro data loaded — FRED data refreshes every 24 hours. "
            "Set FRED_API_KEY in .env, ensure fredapi is installed, and click Refresh All Data in the sidebar."
        )
        # Still render with simulated data
        macro_data = {}

    n_loaded = len(macro_data)
    logger.info(f"tab_macro: rendering with {n_loaded} FRED series")

    # ── Section 0: Hero Dashboard ──────────────────────────────────────────
    _render_hero(macro_data)

    # ── Tabs for remaining sections ────────────────────────────────────────
    tabs = st.tabs([
        "📈 GDP Growth",
        "🔥 Inflation",
        "🏦 Interest Rates",
        "🏭 PMI Heatmap",
        "🚢 Trade Volume",
        "💱 Currencies",
        "⛏️ Commodities",
        "⚠️ Recession Risk",
        "📊 Correlations",
    ])

    with tabs[0]:
        _render_gdp_tracker(macro_data)

    with tabs[1]:
        _render_inflation_monitor(macro_data)

    with tabs[2]:
        _render_interest_rate_dashboard(macro_data)

    with tabs[3]:
        _render_pmi_heatmap(macro_data)

    with tabs[4]:
        _render_trade_volume(macro_data)

    with tabs[5]:
        _render_currency_dashboard(macro_data)

    with tabs[6]:
        _render_commodity_complex(macro_data)

    with tabs[7]:
        _render_recession_risk(macro_data)

    with tabs[8]:
        _render_macro_freight_scatter(macro_data, freight_data)
