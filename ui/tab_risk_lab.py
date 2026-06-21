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


def _real_returns_panel(stock_data, tickers: list[str], *, min_obs: int = 60) -> pd.DataFrame:
    """Daily log-returns from REAL cached closes for the requested tickers.

    The VaR/CVaR/regime engine then runs on the book's actual covariance and
    tails instead of a synthetic panel with a fixed 0.30 correlation. Returns
    an EMPTY frame when fewer than 2 tickers have >= ``min_obs`` real returns,
    so the caller falls back to the synthetic panel (labeled demo).
    """
    from processing.book_pnl import returns_panel
    return returns_panel(stock_data, tickers, min_obs=min_obs)


# ─── Section 1: Portfolio VaR strip ─────────────────────────────────────────

def _render_var_strip(returns_df: pd.DataFrame, weights: dict,
                       portfolio_value: float, confidence: float) -> None:
    """VaR cards — EWMA (live) headline + historical/parametric comparison."""
    from processing.risk_lab import portfolio_var

    section_header(
        "Portfolio Value-at-Risk",
        subtitle=(
            f"VaR & CVaR at {confidence*100:.0f}% confidence, 1-day horizon. "
            "EWMA (RiskMetrics vol-adaptive) is the platform's live method — "
            "coverage-tested against realized P&L; historical (empirical "
            "percentile) and parametric (flat Gaussian) shown for comparison."
        ),
    )

    ewma = portfolio_var(returns_df, weights, confidence=confidence,
                        method="ewma", portfolio_value=portfolio_value)
    hist = portfolio_var(returns_df, weights, confidence=confidence,
                        method="historical", portfolio_value=portfolio_value)
    para = portfolio_var(returns_df, weights, confidence=confidence,
                        method="parametric", portfolio_value=portfolio_value)

    var_cards = [
        {"label": "VaR (EWMA · live)",
         "value": f"{ewma.var_pct*100:+.2f}%",
         "accent": C_LOW,
         "sublabel": f"${ewma.var_dollar:,.0f} — vol-adaptive (platform default)"},
        {"label": "VaR (Historical)",
         "value": f"{hist.var_pct*100:+.2f}%",
         "accent": C_LOW,
         "sublabel": f"${hist.var_dollar:,.0f} — empirical percentile"},
        {"label": "VaR (Parametric)",
         "value": f"{para.var_pct*100:+.2f}%",
         "accent": C_LOW,
         "sublabel": f"${para.var_dollar:,.0f} — flat Gaussian"},
    ]
    cvar_cards = [
        {"label": "CVaR (EWMA · live)",
         "value": f"{ewma.cvar_pct*100:+.2f}%",
         "accent": C_LOW,
         "sublabel": f"${ewma.cvar_dollar:,.0f} — expected loss in the tail"},
        {"label": "CVaR (Historical)",
         "value": f"{hist.cvar_pct*100:+.2f}%",
         "accent": C_LOW,
         "sublabel": f"${hist.cvar_dollar:,.0f} — empirical tail mean"},
        {"label": "CVaR (Parametric)",
         "value": f"{para.cvar_pct*100:+.2f}%",
         "accent": C_LOW,
         "sublabel": f"${para.cvar_dollar:,.0f} — closed-form Gaussian"},
    ]
    metric_card_row(var_cards, columns=3)
    metric_card_row(cvar_cards, columns=3)


# ─── Section 1b: Editorial commentary (per-tab LLM + template fallback) ───

def _render_editorial_commentary(
    returns_df: pd.DataFrame, weights: dict,
    portfolio_value: float, confidence: float,
) -> None:
    """1-2 paragraph editorial read on the current risk snapshot.

    Sits between the VaR strip and the scenario stress table. Wraps the
    engine call in try/except — template fallback is safe; the only failure
    mode we guard against here is import / DB errors.
    """
    try:
        from engine.tab_commentary import build_commentary
        from processing.risk_lab import portfolio_var

        # Recompute the VaR numbers locally (cheap) so the commentary has
        # the same figures the user just saw above.
        hist = portfolio_var(
            returns_df, weights, confidence=confidence,
            method="historical", portfolio_value=portfolio_value,
        )
        para = portfolio_var(
            returns_df, weights, confidence=confidence,
            method="parametric", portfolio_value=portfolio_value,
        )

        # Largest single-name weight, for a contextual color on concentration.
        top_ticker = max(weights, key=weights.get) if weights else ""
        top_weight_pct = (
            round(float(weights[top_ticker]) * 100.0, 1)
            if top_ticker else 0.0
        )

        context: dict[str, object] = {
            "portfolio_value_usd": float(portfolio_value),
            "confidence_pct": int(confidence * 100),
            "n_positions": len(weights),
            "var_historical_pct": round(float(hist.var_pct) * 100.0, 2),
            "var_historical_usd": round(float(hist.var_dollar), 0),
            "cvar_historical_pct": round(float(hist.cvar_pct) * 100.0, 2),
            "cvar_historical_usd": round(float(hist.cvar_dollar), 0),
            "var_parametric_pct": round(float(para.var_pct) * 100.0, 2),
            "var_parametric_usd": round(float(para.var_dollar), 0),
            "top_position": (
                f"{top_ticker} ({top_weight_pct:.1f}% of book)"
                if top_ticker else ""
            ),
        }

        commentary = build_commentary("Risk Lab", context)

        section_header(
            "Editorial",
            subtitle=(
                "LLM-narrated read on the current risk snapshot. Falls back "
                "to a deterministic template when no API key is configured."
            ),
        )

        source_label, source_color = (
            ("LLM", C_HIGH) if commentary.source == "llm"
            else ("Template", C_MOD)
        )
        meta_bits = [f"<span style='color:{source_color}'>{source_label}</span>"]
        if commentary.source == "llm" and commentary.model:
            meta_bits.append(
                f"<code style='font-size:0.66rem;color:{C_TEXT3}'>{commentary.model}</code>"
            )
        if commentary.tokens_in or commentary.tokens_out:
            meta_bits.append(
                f"<span style='font-size:0.66rem;color:{C_TEXT3}'>"
                f"{commentary.tokens_in}→{commentary.tokens_out} tok</span>"
            )

        body_html = "".join(
            f'<p style="margin:0 0 10px 0;font-size:0.86rem;line-height:1.55;'
            f'color:{C_TEXT2}">{para.strip()}</p>'
            for para in commentary.body.split("\n\n") if para.strip()
        )
        st.markdown(
            f'<div style="background:rgba(53,114,176,0.06);'
            f'border-left:3px solid {C_ACCENT};padding:14px 18px;border-radius:3px;'
            f'margin-bottom:14px">'
            f'<div style="font-size:0.66rem;text-transform:uppercase;letter-spacing:0.14em;'
            f'color:{C_TEXT3};font-weight:600;margin-bottom:6px">'
            f'Source: {" · ".join(meta_bits)}'
            f'</div>'
            f'<div style="font-family:Libre Baskerville,Georgia,serif;font-size:1.05rem;'
            f'line-height:1.4;color:{C_TEXT};font-weight:600;margin-bottom:10px">'
            f'{commentary.headline}</div>'
            f'{body_html}'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Risk Lab — editorial commentary render failed")


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


def _build_cascade_ideas(
    port_results, route_results, freight_data, macro_data, stock_data, insights,
) -> list:
    """Run the live disruption cascade -> scored EquityIdea list (defensive).

    Same chain the Idea Engine + scheduler use
    (compute_shipping_stress -> build_exposure_matrix -> score_equity_ideas).
    Returns [] on any failure, in which case the Stress-VaR runs with NO
    directional shock — a coherent ES on the book's real market covariance
    alone (still honest, just no disruption tilt).
    """
    try:
        from processing.shipping_stress_index import compute_shipping_stress
        from processing.exposure_matrix import build_exposure_matrix
        from processing.disruption_cascade import score_equity_ideas

        stress_report = compute_shipping_stress(
            freight_data or {}, macro_data or {},
            port_results or [], route_results or [],
        )
        exposure = build_exposure_matrix(stock_data or {})
        return score_equity_ideas(
            stress_report, exposure, stock_data or {}, insights or [],
        )
    except Exception as exc:
        logger.exception(f"risk_lab: cascade ideas build failed: {exc}")
        return []


def _render_stress_var_es(
    weights: dict, ideas: list, stock_data, portfolio_value: float,
    confidence: float, *, horizon_days: int = 5,
) -> None:
    """Coherent, cascade-grounded Stress-VaR / Expected-Shortfall (R009).

    Unlike the parametric VaR strip (market noise only) and the catalog stress
    table (hardcoded multipliers), this pushes the LIVE cascade's per-name
    direction x conviction through the book's REAL return covariance via Monte
    Carlo, and reports a coherent ES with an exact per-name tail decomposition.
    """
    from processing.stress_var import monte_carlo_book_es

    section_header(
        "Coherent Stress-VaR / Expected Shortfall",
        subtitle=(
            "The live disruption cascade pushed into the book's loss "
            "distribution: per-name direction × conviction shocks over a "
            f"{horizon_days}-day horizon, drawn against the book's real return "
            "covariance. ES is coherent (sub-additive); the component split is "
            "exact — it shows which names own the tail."
        ),
    )

    try:
        r = monte_carlo_book_es(
            weights, ideas, stock_data,
            confidence=confidence, horizon_days=horizon_days,
            portfolio_value=portfolio_value, scenario_name="Live cascade",
        )
    except Exception as exc:
        logger.exception(f"risk_lab: stress-VaR/ES failed: {exc}")
        st.info("Coherent Stress-VaR could not be computed for this book.")
        return

    if r.n_names == 0:
        st.info("No positions to stress.")
        return

    n_shocked = sum(1 for v in r.shocks_pct.values() if abs(v) > 1e-9)
    # var_pct/es_pct are SIGNED — under a net-bullish cascade the tail can be a
    # gain. Label the sign rather than rendering a phantom 'loss'.
    var_word = "loss" if r.var_pct < 0 else "gain"
    es_word = "loss" if r.es_pct < 0 else "gain"
    metric_card_row([
        {"label": f"Stress VaR ({confidence*100:.0f}%)",
         "value": f"{r.var_pct*100:+.2f}%",
         "accent": (C_LOW if r.var_pct < 0 else C_HIGH),
         "sublabel": f"${abs(r.var_dollar):,.0f} tail {var_word} over {horizon_days}d"},
        {"label": "Expected Shortfall",
         "value": f"{r.es_pct*100:+.2f}%",
         "accent": (C_LOW if r.es_pct < 0 else C_HIGH),
         "sublabel": f"${abs(r.es_dollar):,.0f} — mean tail {es_word}"},
        {"label": "Mean Stressed P&L",
         "value": f"{r.mean_pnl_pct*100:+.2f}%",
         "accent": (C_HIGH if r.mean_pnl_pct > 0 else C_LOW),
         "sublabel": f"{n_shocked} of {r.n_names} names cascade-shocked"},
        {"label": "Covariance basis",
         "value": ("Real" if r.basis == "real-cov" else "Fallback"),
         "accent": (C_HIGH if r.basis == "real-cov" else C_MOD),
         "sublabel": (
             "book's cached returns" if r.basis == "real-cov"
             else "diagonal-vol (prices dark)")},
    ], columns=4)

    # Component-ES bar — each name's exact share of the tail loss (sums to ES).
    comp = sorted(r.component_es_pct.items(), key=lambda kv: kv[1])  # worst first
    fig = go.Figure(go.Bar(
        x=[c * 100 for _, c in comp],
        y=[t for t, _ in comp],
        orientation="h",
        marker_color=[C_LOW if c < 0 else C_HIGH for _, c in comp],
        text=[f"{c*100:+.2f}%" for _, c in comp],
        textposition="outside",
        customdata=[r.shocks_pct.get(t, 0.0) * 100 for t, _ in comp],
        hovertemplate=(
            "<b>%{y}</b><br>Tail contribution: %{x:+.2f}%"
            "<br>Cascade shock: %{customdata:+.1f}%<extra></extra>"
        ),
    ))
    apply_dark_layout(
        fig, title="Component ES — which names own the tail (sums to ES)",
        height=max(260, 30 * len(comp) + 90),
        margin=dict(l=12, r=80, t=46, b=30),
        xaxis=dict(title=dict(text="ES contribution %",
                              font=dict(color=C_TEXT2, size=11))),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.caption(
        "Shock = direction × conviction × severity (capped); 0 for held names "
        "with no active idea. Components sum to ES exactly (Euler split). "
        "Illustrative severity scalar, not a price forecast."
    )

    # Per-driver tail attribution (R065) — re-bucket the per-name component ES
    # by each name's dominant cascade driver, so the PM sees that several names
    # may share ONE bet (e.g. a single chokepoint). Sums to ES exactly.
    try:
        from processing.stress_var import (
            component_es_by_driver, idea_driver_map,
        )
        by_driver = component_es_by_driver(
            r.component_es_pct, idea_driver_map(ideas)
        )
    except Exception:
        by_driver = {}
    if len(by_driver) > 1 or (by_driver and "market" not in by_driver):
        _DRIVER_LABEL = {
            "chokepoint": "Chokepoint", "congestion": "Port congestion",
            "weather": "Weather", "rate": "Freight-rate", "fuel": "Fuel cost",
            "vulnerability": "Structural", "market": "Market (no idea)",
        }
        dsorted = sorted(by_driver.items(), key=lambda kv: kv[1])  # worst first
        dfig = go.Figure(go.Bar(
            x=[v * 100 for _, v in dsorted],
            y=[_DRIVER_LABEL.get(k, k.title()) for k, _ in dsorted],
            orientation="h",
            marker_color=[C_LOW if v < 0 else C_HIGH for _, v in dsorted],
            text=[f"{v*100:+.2f}%" for _, v in dsorted],
            textposition="outside",
            hovertemplate="<b>%{y}</b><br>Tail share: %{x:+.2f}%<extra></extra>",
        ))
        apply_dark_layout(
            dfig, title="Component ES by cascade driver — which bet owns the tail",
            height=max(220, 32 * len(dsorted) + 90),
            margin=dict(l=12, r=80, t=46, b=30),
            xaxis=dict(title=dict(text="ES contribution %",
                                  font=dict(color=C_TEXT2, size=11))),
        )
        st.plotly_chart(dfig, use_container_width=True,
                        config={"displayModeBar": False})
        st.caption(
            "Names rolled up by their dominant cascade driver — a single "
            "chokepoint bet shared across names shows as one bar."
        )


# ─── Section 2c: Ex-ante risk attribution (R124) ────────────────────────────

def _build_carrier_fits(stock_data, macro_data):
    """Build (fits, factors_df, weights, is_real) for the ex-ante risk view.

    Reuses tab_portfolio's exact factor-model path: weekly log-returns from the
    real cached closes, a factor panel from macro_data (FRED/Baltic) with a
    deterministic synthetic fallback when macro is dark, one OLS fit per carrier,
    and an equal-weight book over the fitted names — the same representative book
    the Carrier Factor Lens uses. Returns ``(None, ...)``-shaped emptiness when
    fewer than two carriers can be fit (caller renders an honest empty-state).
    """
    from engine.carrier_factor_model import fit_carrier_factors
    from ui.tab_portfolio import (
        _factors_from_macro,
        _synthetic_factor_frame,
        _weekly_log_returns,
    )

    returns_df = _weekly_log_returns(stock_data)
    if returns_df.empty or returns_df.shape[1] < 2:
        return {}, pd.DataFrame(), {}, False

    factors_df = _factors_from_macro(macro_data)
    factors_real = not factors_df.empty and len(factors_df) >= 80
    if not factors_real:
        factors_df = _synthetic_factor_frame(returns_df)

    combined_idx = returns_df.index.intersection(factors_df.index)
    if len(combined_idx) < 60:
        return {}, pd.DataFrame(), {}, False
    returns_df = returns_df.loc[combined_idx]
    factors_df = factors_df.loc[combined_idx]

    fits = fit_carrier_factors(returns_df, factors_df, hac_lags=4)
    if len(fits) < 1:
        return {}, pd.DataFrame(), {}, False

    names = list(fits.keys())
    weights = {n: 1.0 / len(names) for n in names}
    return fits, factors_df, weights, factors_real


def _render_risk_attribution(stock_data, macro_data) -> None:
    """"What owns the risk" — ex-ante VARIANCE attribution (R124).

    The forward-looking analog of the return attribution: decompose the book's
    forecast tracking error into per-factor (systematic) and per-name (specific)
    variance contributions, so the desk sees WHICH factor and WHICH name drive
    the risk. Stacked bar (factor contributions + a 'specific' bucket) + a small
    vol / %-factor-vs-specific table.
    """
    from engine.carrier_factor_model import factor_covariance, risk_attribution

    section_header(
        "What owns the risk (ex-ante variance)",
        subtitle=(
            "Forecast portfolio variance decomposed into per-factor "
            "(systematic) and per-name (specific) contributions: σ²ₚ = "
            "wᵀ(BΩBᵀ + D)w. The forward-looking analog of return attribution — "
            "which factor and which name drive the book's tracking error. "
            "Equal-weight over the fitted carriers."
        ),
    )

    try:
        fits, factors_df, weights, factors_real = _build_carrier_fits(
            stock_data, macro_data,
        )
        if not fits:
            st.info(
                "Ex-ante risk attribution needs ≥ 2 carriers with enough cached "
                "return history to fit. Prices appear dark — nothing to decompose."
            )
            return

        attr = risk_attribution(weights, fits, factor_covariance(factors_df))
        if attr.total_variance <= 0.0:
            st.info("Forecast variance is zero for this book — nothing to attribute.")
            return

        # ── Headline cards: book vol + factor-vs-specific split ──
        metric_card_row([
            {"label": "Forecast Vol (ann)",
             "value": f"{attr.total_vol * 100:.1f}%",
             "accent": C_ACCENT,
             "sublabel": f"equal-wt {attr.n_names} carriers"},
            {"label": "Factor (systematic)",
             "value": f"{attr.pct_factor * 100:.0f}%",
             "accent": C_MOD,
             "sublabel": f"σ²={attr.factor_variance:.2e}"},
            {"label": "Specific (name)",
             "value": f"{attr.pct_specific * 100:.0f}%",
             "accent": C_TEXT2,
             "sublabel": f"σ²={attr.specific_variance:.2e}"},
        ], columns=3)

        # ── Stacked bar: per-factor contributions + one 'Specific' bucket ──
        # All entries are a share of TOTAL variance (per the .pct field), so the
        # stacked bar reads as a single book whose segments sum to 100%.
        factor_items = sorted(
            attr.per_factor.items(), key=lambda kv: kv[1]["variance"], reverse=True,
        )
        labels = [f for f, _ in factor_items] + ["Specific"]
        pcts = [d["pct"] * 100 for _, d in factor_items] + [attr.pct_specific * 100]
        colors = [C_ACCENT] * len(factor_items) + [C_TEXT2]

        fig = go.Figure(go.Bar(
            x=pcts,
            y=labels,
            orientation="h",
            marker_color=colors,
            text=[f"{p:+.1f}%" for p in pcts],
            textposition="outside",
            hovertemplate="<b>%{y}</b><br>Share of forecast variance: %{x:+.1f}%<extra></extra>",
        ))
        apply_dark_layout(
            fig,
            title="Share of forecast variance — by factor + specific (sums to 100%)",
            height=max(260, 30 * len(labels) + 90),
            margin=dict(l=12, r=80, t=46, b=30),
            xaxis=dict(title=dict(text="% of total variance",
                                  font=dict(color=C_TEXT2, size=11))),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        # ── Top specific names (which name owns the idiosyncratic risk) ──
        name_items = sorted(
            attr.per_name_specific.items(),
            key=lambda kv: kv[1]["variance"], reverse=True,
        )[:6]
        if name_items:
            headers = ["Name", "Specific σ² (ann)", "% of total var"]
            rows = [
                [
                    _sans(n, color=C_TEXT, weight=600),
                    _mono(f"{d['variance']:.2e}", color=C_TEXT2),
                    _mono(f"{d['pct'] * 100:.1f}%", color=C_LOW),
                ]
                for n, d in name_items
            ]
            wsj_market_table(headers, rows)

        st.caption(
            "Per-factor contributions are the row split xₖ·(Ωx)ₖ of the factor "
            "quadratic form (a hedging factor can read negative); per-name shares "
            "are wᵢ²σ²ᵢ. Factor + specific buckets sum to the total variance "
            "exactly. Illustrative — modeled factor exposures, not a price forecast."
        )

        st.markdown(
            source_footer([
                DataSource.live(
                    "Weekly carrier log-returns from cached closes; factor panel "
                    "from FRED/Baltic macro_data (modeled)."
                ) if factors_real else DataSource.demo(
                    "Weekly carrier log-returns from cached closes; SYNTHETIC "
                    "factor panel (macro dark) — exposures illustrative."
                ),
            ]),
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Risk Lab — ex-ante risk attribution render failed")
        st.info("Ex-ante risk attribution could not be computed for this book.")


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
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('risk_lab'):
        try:
            page_header(
                title="Risk Lab",
                subtitle=(
                    "Portfolio VaR/CVaR, scenario stress test against the "
                    "canonical catalog, and market-regime classification. "
                    "Real cached returns where available; synthetic fallback "
                    "when prices are dark (see footer)."
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
            # Prefer REAL returns from cached closes (so VaR/CVaR/regime use the
            # book's actual covariance + tails); fall back to the synthetic
            # panel — labeled demo — only when prices are dark.
            real_panel = _real_returns_panel(stock_data, tickers)
            panel_is_real = not real_panel.empty
            if panel_is_real:
                returns_df = real_panel
                avail = list(returns_df.columns)
                weights = {t: 1.0 / len(avail) for t in avail}
            else:
                returns_df = _synth_returns_panel(tickers)
                weights = {t: 1.0 / len(tickers) for t in tickers}

            if returns_df.empty:
                st.info("Returns panel could not be built. Aborting Risk Lab.")
                return

            section_divider("Value-at-Risk")
            _render_var_strip(returns_df, weights, portfolio_value, confidence)

            # ── Editorial commentary (per-tab LLM + template fallback) ──
            _render_editorial_commentary(
                returns_df, weights, portfolio_value, confidence,
            )

            section_divider("Scenario Stress")
            _render_stress_table(weights, portfolio_value)

            # Coherent, cascade-grounded Stress-VaR/ES (R009) — the live
            # disruption pushed into the book's real loss distribution.
            section_divider("Coherent Stress-VaR")
            ideas = _build_cascade_ideas(
                port_results, route_results, freight_data, macro_data,
                stock_data, insights,
            )
            _render_stress_var_es(
                weights, ideas, stock_data, portfolio_value, confidence,
            )

            # Ex-ante RISK attribution (R124) — decompose forecast variance into
            # per-factor + per-name shares, the forward-looking analog of the
            # return attribution. Own section + provenance footer inside.
            section_divider("Ex-ante Risk Attribution")
            _render_risk_attribution(stock_data, macro_data)

            section_divider("Regime")
            _render_regime_card(returns_df, weights)

            # ── Export this view (PDF) ────────────────────────────────────────
            try:
                from utils.view_export import (
                    ViewSection, ViewSnapshot, ViewTable, render_export_button,
                )
                from processing.risk_lab import (
                    detect_regime, portfolio_var, stress_test_all_scenarios,
                )
                # Recompute the headline numbers (the per-section funcs render
                # them but don't return them; cheap to redo).
                hist = portfolio_var(returns_df, weights, confidence=confidence,
                                    method="historical",
                                    portfolio_value=portfolio_value)
                common = [c for c in returns_df.columns if c in weights]
                w_vec = np.array([float(weights[c]) for c in common])
                port_returns = pd.Series(
                    returns_df[common].dropna().to_numpy() @ w_vec,
                    index=returns_df[common].dropna().index,
                )
                regime = detect_regime(port_returns)
                stress = stress_test_all_scenarios(weights, portfolio_value=portfolio_value)

                stress_rows = [
                    [
                        r.scenario_name,
                        r.category,
                        f"{r.pnl_pct*100:+5.2f}%",
                        f"${r.pnl_dollar:+,.0f}",
                    ]
                    for r in stress
                ]

                snapshot = ViewSnapshot(
                    title="Risk Lab",
                    subtitle=(
                        f"Portfolio ${portfolio_value:,.0f} · "
                        f"VaR confidence {confidence*100:.0f}%"
                    ),
                    headline=(
                        f"VaR {hist.var_pct*100:+.2f}% (${hist.var_dollar:,.0f}) · "
                        f"CVaR {hist.cvar_pct*100:+.2f}% · "
                        f"Regime: {regime.label} ({regime.confidence:.2f})"
                    ),
                    body=regime.interpretation,
                    sections=[
                        ViewSection(
                            title="Scenario Stress (worst-first)",
                            tables=[ViewTable(
                                title=f"{len(stress)} catalog scenarios",
                                headers=["Scenario", "Category", "P&L %", "P&L $"],
                                rows=stress_rows,
                            )],
                        ),
                        ViewSection(
                            title="Regime Indicators",
                            bullets=[f"{k}: {v}" for k, v in regime.indicators.items()],
                        ),
                    ],
                    footer_note=(
                        "VaR + scenario stress + regime detection from "
                        "processing.risk_lab. Returns panel synthetic (stable_hash)."
                    ),
                )
                cols = st.columns([1, 5], gap="small")
                with cols[0]:
                    render_export_button(snapshot, "risk_lab", key="risk_lab_export")
            except Exception as exc:
                logger.debug(f"tab_risk_lab: PDF export skipped: {exc}")

            st.markdown(
                source_footer([
                    DataSource.live(
                        "Real per-ticker daily returns from cached yfinance "
                        "closes. Scenario catalog from state/scenarios.py."
                    ) if panel_is_real else DataSource.demo(
                        "Synthetic 2-year returns panel (per-ticker mean/vol via "
                        "stable_hash) — prices unavailable. Scenario catalog "
                        "from state/scenarios.py."
                    ),
                ]),
                unsafe_allow_html=True,
            )

        except Exception:
            logger.exception("tab_risk_lab render failed")
            st.error("Risk Lab encountered an error. See logs.")
