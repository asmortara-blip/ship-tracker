"""ui/tab_risk_lab.py — Risk Lab tab.

Phase-4 tab. Surfaces processing.risk_lab's three pieces in one screen:

  1. Portfolio VaR & CVaR (historical and parametric)
  2. Scenario stress test against the canonical scenario catalog
  3. Market regime detection — Bull / Bear / Sideways / Crisis

Complements (does not duplicate) tab_risk_matrix, which focuses on
factor-level risk decomposition.

Demo data
---------
The platform doesn't yet persist per-ticker return histories, so this
tab uses the same synthetic-panel pattern as tab_portfolio and
tab_idea_engine: per-ticker mean/vol seeded via utils.helpers.stable_hash.
Source footer labels it DataSource.demo.
"""
from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)
from utils.helpers import stable_hash


# ─── Helpers ────────────────────────────────────────────────────────────────

def _mono(value: str, color: str = C_TEXT) -> str:
    return (
        f'<span style="font-family:JetBrains Mono,monospace;color:{color};'
        f'font-size:0.82rem;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 500) -> str:
    return (
        f'<span style="font-family:Libre Franklin,sans-serif;color:{color};'
        f'font-weight:{weight};font-size:0.82rem;">{value}</span>'
    )


def _regime_color(label: str) -> str:
    return {
        "Bull":     C_HIGH,
        "Bear":     C_LOW,
        "Sideways": C_MOD,
        "Crisis":   C_LOW,
        "Unknown":  C_TEXT3,
    }.get(label, C_TEXT2)


def _synth_returns_panel(tickers: list[str], n: int = 504) -> pd.DataFrame:
    """Same construction as tab_portfolio / tab_idea_engine.

    Per-ticker mean/vol via stable_hash, uniform 0.30 pairwise correlation,
    504 days of multivariate-normal returns. Deterministic per ticker set.
    """
    if not tickers:
        return pd.DataFrame()
    k = len(tickers)
    means = np.array([
        0.0002 + (stable_hash(t + "mu") % 10) / 10_000.0
        for t in tickers
    ])
    vols = np.array([
        0.014 + (stable_hash(t + "vol") % 100) / 5_000.0
        for t in tickers
    ])
    corr = np.full((k, k), 0.30)
    np.fill_diagonal(corr, 1.0)
    cov = np.outer(vols, vols) * corr
    rng = np.random.default_rng(stable_hash("risk_lab_panel_" + "".join(tickers)) % (2**31))
    samples = rng.multivariate_normal(mean=means, cov=cov, size=n)
    dates = pd.date_range(end=_dt.date.today(), periods=n, freq="B")
    return pd.DataFrame(samples, index=dates, columns=tickers)


# ─── Section 1: Portfolio VaR strip ─────────────────────────────────────────

def _render_var_strip(returns_df: pd.DataFrame, weights: dict,
                       portfolio_value: float, confidence: float) -> None:
    """Side-by-side historical + parametric VaR cards."""
    from processing.risk_lab import portfolio_var

    section_header(
        "Portfolio Value-at-Risk",
        subtitle=(
            f"VaR & CVaR at {confidence*100:.0f}% confidence, 1-day horizon. "
            "Historical = empirical percentile; parametric = Gaussian "
            "(μ + zα × σ)."
        ),
    )

    hist = portfolio_var(returns_df, weights, confidence=confidence,
                        method="historical", portfolio_value=portfolio_value)
    para = portfolio_var(returns_df, weights, confidence=confidence,
                        method="parametric", portfolio_value=portfolio_value)

    cards = [
        {"label": "VaR (Historical)",
         "value": f"{hist.var_pct*100:+.2f}%",
         "accent": C_LOW,
         "sublabel": f"${hist.var_dollar:,.0f} on ${portfolio_value:,.0f}"},
        {"label": "CVaR (Historical)",
         "value": f"{hist.cvar_pct*100:+.2f}%",
         "accent": C_LOW,
         "sublabel": f"${hist.cvar_dollar:,.0f} — expected loss in the tail"},
        {"label": "VaR (Parametric)",
         "value": f"{para.var_pct*100:+.2f}%",
         "accent": C_LOW,
         "sublabel": f"${para.var_dollar:,.0f} — Gaussian assumption"},
        {"label": "CVaR (Parametric)",
         "value": f"{para.cvar_pct*100:+.2f}%",
         "accent": C_LOW,
         "sublabel": f"${para.cvar_dollar:,.0f} — closed-form Gaussian"},
    ]
    metric_card_row(cards, columns=4)


# ─── Section 2: Scenario stress test table ──────────────────────────────────

def _render_stress_table(weights: dict, portfolio_value: float) -> None:
    """Run every catalog scenario against the weights — show P&L impact."""
    from processing.risk_lab import stress_test_all_scenarios

    section_header(
        "Scenario Stress Test",
        subtitle=(
            "P&L impact of each canonical scenario applied to the current "
            "weights. Sorted worst-loss-first — tail risks at the top."
        ),
    )

    results = stress_test_all_scenarios(weights, portfolio_value=portfolio_value)
    if not results:
        st.info(
            "No scenarios surfaced. Check that state/scenarios.py is importable."
        )
        return

    headers = ["Scenario", "Category", "P&L %", "P&L $", "Portfolio After", "Top Contributor"]
    rows: list[list[str]] = []
    for r in results:
        # Find the ticker contributing the largest dollar loss (or gain).
        if r.per_ticker_pnl:
            top_ticker, top_pnl = max(
                r.per_ticker_pnl.items(), key=lambda kv: abs(kv[1])
            )
            top_str = f"{top_ticker} ({top_pnl:+,.0f})"
        else:
            top_str = "—"

        pnl_color = C_HIGH if r.pnl_pct > 0 else (C_LOW if r.pnl_pct < 0 else C_TEXT2)
        category_color = {
            "Geopolitical": C_LOW, "Weather": C_MOD, "Macro": C_ACCENT,
            "Demand": "#7c6eaf", "Operational": C_TEXT2,
        }.get(r.category, C_TEXT2)

        rows.append([
            _sans(r.scenario_name, color=C_TEXT, weight=600),
            _sans(badge(r.category, color=category_color), color=C_TEXT2),
            _mono(f"{r.pnl_pct*100:+5.2f}%", color=pnl_color),
            _mono(f"${r.pnl_dollar:+,.0f}", color=pnl_color),
            _mono(f"${r.portfolio_value_after:,.0f}", color=C_TEXT2),
            _sans(top_str, color=C_TEXT2),
        ])
    wsj_market_table(headers, rows)

    # Plot: bar chart of P&L % by scenario.
    fig = go.Figure(go.Bar(
        x=[r.pnl_pct * 100 for r in results],
        y=[r.scenario_name for r in results],
        orientation="h",
        marker_color=[
            C_LOW if r.pnl_pct < 0 else C_HIGH for r in results
        ],
        text=[f"{r.pnl_pct*100:+.1f}%" for r in results],
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>P&L: %{x:+.2f}%<extra></extra>",
    ))
    apply_dark_layout(
        fig, title="Scenario P&L Impact (% of portfolio)",
        height=max(280, 28 * len(results) + 80),
        margin=dict(l=12, r=80, t=46, b=30),
        xaxis=dict(title=dict(text="P&L %", font=dict(color=C_TEXT2, size=11))),
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ─── Section 3: Regime detection card ───────────────────────────────────────

def _render_regime_card(returns_df: pd.DataFrame, weights: dict) -> None:
    """Run detect_regime on the portfolio's weighted-return series."""
    from processing.risk_lab import detect_regime

    section_header(
        "Market Regime",
        subtitle=(
            "Bull / Bear / Sideways / Crisis classification from the "
            "portfolio's trailing return path."
        ),
    )

    common = [c for c in returns_df.columns if c in weights]
    if not common:
        st.info("No overlap between holdings and returns panel.")
        return
    w_vec = np.array([float(weights[c]) for c in common])
    sub = returns_df[common].dropna()
    if sub.empty:
        st.info("No returns data available.")
        return
    port_returns = pd.Series(sub.to_numpy() @ w_vec, index=sub.index)

    regime = detect_regime(port_returns)
    regime_color = _regime_color(regime.label)

    st.markdown(
        f'<div style="background:rgba(53,114,176,0.08);'
        f'border-left:3px solid {regime_color};padding:14px 18px;'
        f'border-radius:3px;margin-bottom:12px">'
        f'<div style="font-size:0.66rem;text-transform:uppercase;letter-spacing:0.14em;'
        f'color:{C_TEXT3};font-weight:600;margin-bottom:4px">'
        f'CURRENT REGIME · confidence {regime.confidence:.2f}</div>'
        f'<div style="font-family:Libre Baskerville,Georgia,serif;font-size:1.6rem;'
        f'color:{regime_color};font-weight:700">{regime.label}</div>'
        f'<div style="font-family:Libre Franklin,sans-serif;font-size:0.86rem;'
        f'color:{C_TEXT2};margin-top:8px;line-height:1.5">{regime.interpretation}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Indicators that drove the call.
    if regime.indicators:
        ind_cards = []
        for label, key in (
            ("Ann. Return (long)", "ann_return_long"),
            ("Ann. Vol (long)", "ann_vol_long"),
            ("Vol Ratio", "vol_ratio"),
            ("Short Drawdown", "short_drawdown"),
        ):
            v = regime.indicators.get(key)
            if v is None:
                continue
            if "ratio" in key:
                val_str = f"{v:.2f}×"
            elif "drawdown" in key:
                val_str = f"{v*100:+.1f}%"
            else:
                val_str = f"{v*100:+.1f}%"
            ind_cards.append({
                "label": label, "value": val_str,
                "accent": C_TEXT2, "sublabel": key,
            })
        if ind_cards:
            metric_card_row(ind_cards, columns=len(ind_cards))


# ─── Main render ────────────────────────────────────────────────────────────

def render(
    port_results=None,
    route_results=None,
    insights=None,
    freight_data=None,
    macro_data=None,
    stock_data=None,
    **_kwargs,
) -> None:
    """Render the Risk Lab tab."""
    try:
        page_header(
            title="Risk Lab",
            subtitle=(
                "Portfolio VaR/CVaR, scenario stress test against the "
                "canonical catalog, and market-regime classification. "
                "Demo: synthetic returns panel."
            ),
            badge_text="RISK",
            badge_color=C_ACCENT,
        )

        # ── Controls ──────────────────────────────────────────────────────
        c1, c2 = st.columns(2, gap="medium")
        with c1:
            portfolio_value = st.number_input(
                "Portfolio value ($)",
                min_value=10_000, max_value=100_000_000,
                value=1_000_000, step=50_000,
                key="risk_lab_value",
            )
        with c2:
            confidence_pct = st.slider(
                "VaR confidence (%)", 90, 99, 95, step=1,
                key="risk_lab_confidence",
            )
        confidence = confidence_pct / 100.0

        # Build a default equal-weight portfolio over the shipping universe.
        tickers = ["ZIM", "MATX", "SBLK", "DAC", "CMRE", "STNG"]
        weights = {t: 1.0 / len(tickers) for t in tickers}
        returns_df = _synth_returns_panel(tickers)

        if returns_df.empty:
            st.info("Returns panel could not be built. Aborting Risk Lab.")
            return

        section_divider("Value-at-Risk")
        _render_var_strip(returns_df, weights, portfolio_value, confidence)

        section_divider("Scenario Stress")
        _render_stress_table(weights, portfolio_value)

        section_divider("Regime")
        _render_regime_card(returns_df, weights)

        st.markdown(
            source_footer([
                DataSource.demo(
                    "Synthetic 2-year returns panel (per-ticker mean/vol via "
                    "stable_hash). Scenario catalog from state/scenarios.py."
                ),
            ]),
            unsafe_allow_html=True,
        )

    except Exception:
        logger.exception("tab_risk_lab render failed")
        st.error("Risk Lab encountered an error. See logs.")
