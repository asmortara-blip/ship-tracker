"""Monte Carlo simulation dashboard for shipping market forecasting.

Sections:
  1. Monte Carlo Configuration (st.form)
  2. Simulation Fan Chart (Plotly)
  3. Distribution at Horizon (Plotly histogram)
  4. Statistics Table (HTML)
  5. Scenario Overlays (Plotly)
  6. Value at Risk / CVaR (KPI cards)
  7. Path Analysis (breach timing + drawdown)
"""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from loguru import logger
from scipy import stats as scipy_stats

from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    gradient_card,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# Domain-specific accent colors for scenario overlays. Kept local because
# their semantics ("Red Sea" = green, "China surge" = purple) are unique to
# this tab; not promoted to ui.styles.
C_PURPLE = "#7c6eaf"


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


# ── Simulation engine ─────────────────────────────────────────────────────────

def _run_gbm(
    S0: float,
    mu: float,
    sigma: float,
    T: int,
    n_paths: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Geometric Brownian Motion: shape (n_paths, T+1)."""
    dt = 1 / 252
    drift = (mu - 0.5 * sigma ** 2) * dt
    diffusion = sigma * np.sqrt(dt)
    Z = rng.standard_normal((n_paths, T))
    log_returns = drift + diffusion * Z
    log_paths = np.cumsum(log_returns, axis=1)
    paths = S0 * np.exp(np.hstack([np.zeros((n_paths, 1)), log_paths]))
    return paths


def _run_jump_diffusion(
    S0: float,
    mu: float,
    sigma: float,
    T: int,
    n_paths: int,
    rng: np.random.Generator,
    lam: float = 10.0,
    jump_mu: float = 0.0,
    jump_sigma: float = 0.05,
) -> np.ndarray:
    """Merton jump-diffusion model: GBM + Poisson jumps."""
    dt = 1 / 252
    drift = (mu - 0.5 * sigma ** 2 - lam * (np.exp(jump_mu + 0.5 * jump_sigma ** 2) - 1)) * dt
    diffusion = sigma * np.sqrt(dt)
    Z = rng.standard_normal((n_paths, T))
    n_jumps = rng.poisson(lam * dt, (n_paths, T))
    jump_sizes = np.where(
        n_jumps > 0,
        rng.normal(jump_mu * n_jumps, jump_sigma * np.sqrt(np.maximum(n_jumps, 1))),
        0.0,
    )
    log_returns = drift + diffusion * Z + jump_sizes
    log_paths = np.cumsum(log_returns, axis=1)
    paths = S0 * np.exp(np.hstack([np.zeros((n_paths, 1)), log_paths]))
    return paths


def _simulate(
    S0: float,
    mu_annual: float,
    sigma_annual: float,
    T: int,
    n_paths: int,
    model: str,
    seed: int = 42,
) -> np.ndarray:
    """Dispatch to GBM or jump-diffusion. Returns shape (n_paths, T+1)."""
    rng = np.random.default_rng(seed)
    if model == "Jump Diffusion":
        return _run_jump_diffusion(S0, mu_annual, sigma_annual, T, n_paths, rng)
    return _run_gbm(S0, mu_annual, sigma_annual, T, n_paths, rng)


# ── (Local helpers removed; use section_header / metric_card_row / apply_dark_layout from ui.styles) ──


# ── Section 1: Configuration form ────────────────────────────────────────────

def _render_config_form() -> dict | None:
    """Render simulation parameter form. Returns params dict on submit, else None."""
    section_header(
        "Monte Carlo Configuration",
        "Configure simulation parameters and run forecast paths.",
    )
    targets = ["BDI", "WCI", "ZIM Stock", "MATX Stock", "Container Rates"]
    defaults: dict = {
        "BDI": 1800.0,
        "WCI": 3200.0,
        "ZIM Stock": 18.0,
        "MATX Stock": 110.0,
        "Container Rates": 2800.0,
    }
    with st.form("mc_config_form"):
        c1, c2 = st.columns(2)
        with c1:
            target = st.selectbox("Simulation target", targets, index=0)
            n_paths = st.slider("Number of paths", 100, 10_000, 1_000, step=100)
            horizon = st.slider("Horizon (days)", 30, 365, 90, step=5)
        with c2:
            s0 = st.number_input(
                "Starting value",
                min_value=0.01,
                value=float(defaults.get(target, 1000.0)),
                step=10.0,
            )
            sigma_pct = st.slider("Annual volatility (%)", 10, 80, 35)
            mu_pct = st.slider("Annual drift (%)", -30, 30, 5)
        model = st.radio(
            "Model type",
            ["GBM (Geometric Brownian Motion)", "Jump Diffusion"],
            horizontal=True,
        )
        submitted = st.form_submit_button("Run Simulation", use_container_width=True)
    if submitted:
        model_key = "Jump Diffusion" if "Jump" in model else "GBM"
        return dict(
            target=target,
            n_paths=n_paths,
            horizon=horizon,
            s0=s0,
            sigma=sigma_pct / 100.0,
            mu=mu_pct / 100.0,
            model=model_key,
        )
    return None


# ── Section 2: Fan chart ──────────────────────────────────────────────────────

def _render_fan_chart(paths: np.ndarray, target: str, s0: float, n_paths_total: int) -> None:
    section_header("Simulation Fan Chart", "100 sample paths with confidence bands.")
    try:
        T = paths.shape[1] - 1
        days = np.arange(T + 1)
        pct = np.percentile(paths, [2.5, 10.0, 25.0, 50.0, 75.0, 90.0, 97.5], axis=0)
        fig = go.Figure()
        # confidence bands — from widest to narrowest
        bands = [
            (pct[0], pct[6], C_ACCENT, "95% CI", 0.10),
            (pct[1], pct[5], C_HIGH,   "80% CI", 0.13),
            (pct[2], pct[4], C_MOD,    "50% CI", 0.18),
        ]
        for lo, hi, color, name, opacity in bands:
            fig.add_trace(go.Scatter(
                x=np.concatenate([days, days[::-1]]),
                y=np.concatenate([hi, lo[::-1]]),
                fill="toself",
                fillcolor=color.replace("#", "rgba(") + f",{opacity})" if color.startswith("#") else color,
                line=dict(width=0),
                name=name,
                hoverinfo="skip",
            ))
        # 100 sample paths
        idx = np.linspace(0, paths.shape[0] - 1, min(100, paths.shape[0]), dtype=int)
        for i in idx:
            fig.add_trace(go.Scatter(
                x=days, y=paths[i],
                mode="lines",
                line=dict(color=C_TEXT3, width=0.6),
                opacity=0.3,
                showlegend=False,
                hoverinfo="skip",
            ))
        # median path
        fig.add_trace(go.Scatter(
            x=days, y=pct[3],
            mode="lines",
            line=dict(color=C_HIGH, width=2.5),
            name="Median",
        ))
        # starting value line
        fig.add_hline(
            y=s0, line_dash="dot",
            line_color=C_TEXT3, line_width=1,
            annotation_text="Start",
            annotation_font_color=C_TEXT3,
        )
        apply_dark_layout(fig, title=f"{target} — Simulated Paths", height=420)
        fig.update_layout(
            xaxis=dict(title="Days", zeroline=False),
            yaxis=dict(title="Value", zeroline=False),
        )
        st.plotly_chart(fig, use_container_width=True, key="mc_fan_chart")
        st.markdown(source_footer([
            {"name": f"Monte Carlo simulation ({n_paths_total:,} runs)",
             "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Fan chart render failed")
        st.warning("Fan chart unavailable.")


# ── Section 3: Horizon distribution ──────────────────────────────────────────

def _render_horizon_dist(paths: np.ndarray, s0: float, target: str, n_paths_total: int) -> None:
    section_header(
        "Distribution at Horizon",
        "Histogram of final simulated values across all paths.",
    )
    try:
        finals = paths[:, -1]
        p5, p25, p50, p75, p95 = np.percentile(finals, [5, 25, 50, 75, 95])
        fig = go.Figure()
        # full histogram
        fig.add_trace(go.Histogram(
            x=finals,
            nbinsx=80,
            marker_color=C_ACCENT,
            opacity=0.7,
            name="All paths",
        ))
        # left tail overlay
        tail_lo = finals[finals <= p5]
        fig.add_trace(go.Histogram(
            x=tail_lo,
            nbinsx=20,
            marker_color=C_LOW,
            opacity=0.9,
            name="Bottom 5%",
        ))
        # right tail overlay
        tail_hi = finals[finals >= p95]
        fig.add_trace(go.Histogram(
            x=tail_hi,
            nbinsx=20,
            marker_color=C_HIGH,
            opacity=0.9,
            name="Top 5%",
        ))
        pct_lines = [(p5, "P5", C_LOW), (p25, "P25", C_MOD), (p50, "P50", C_HIGH),
                     (p75, "P75", C_MOD), (p95, "P95", C_LOW)]
        for val, lbl, col in pct_lines:
            fig.add_vline(
                x=val, line_dash="dash", line_color=col, line_width=1.4,
                annotation_text=f"{lbl}: {val:,.0f}",
                annotation_font_color=col,
                annotation_position="top",
            )
        apply_dark_layout(fig, title=f"{target} — Final Value Distribution", height=380)
        fig.update_layout(
            xaxis=dict(title="Final Value"),
            yaxis=dict(title="Frequency"),
            barmode="overlay",
        )
        st.plotly_chart(fig, use_container_width=True, key="mc_horizon_dist")
        st.markdown(source_footer([
            {"name": f"Monte Carlo simulation ({n_paths_total:,} runs)",
             "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Horizon distribution render failed")
        st.warning("Distribution chart unavailable.")


# ── Section 4: Statistics table ───────────────────────────────────────────────

def _render_stats_table(paths: np.ndarray, s0: float, horizon: int, sigma: float, n_paths_total: int) -> None:
    section_header("Simulation Statistics", "Summary metrics across all simulated paths.")
    try:
        finals = paths[:, -1]
        mean_v  = float(np.mean(finals))
        med_v   = float(np.median(finals))
        std_v   = float(np.std(finals))
        skew_v  = float(scipy_stats.skew(finals))
        kurt_v  = float(scipy_stats.kurtosis(finals))
        p5_v, p25_v, p75_v, p95_v = np.percentile(finals, [5, 25, 75, 95])
        prob_up  = float(np.mean(finals > s0) * 100)
        prob_dn  = float(np.mean(finals < s0) * 100)
        dt_ann   = horizon / 252
        sharpe   = ((mean_v / s0 - 1) / max(sigma * np.sqrt(dt_ann), 1e-9))

        prob_up_color = C_HIGH if prob_up >= 50 else C_LOW
        prob_dn_color = C_LOW if prob_dn >= 50 else C_HIGH
        sharpe_color  = C_HIGH if sharpe >= 0 else C_LOW

        stat_rows = [
            [_sans("Mean",                color=C_TEXT2), _mono(f"{mean_v:,.2f}")],
            [_sans("Median",              color=C_TEXT2), _mono(f"{med_v:,.2f}")],
            [_sans("Std Dev",             color=C_TEXT2), _mono(f"{std_v:,.2f}")],
            [_sans("Skewness",            color=C_TEXT2), _mono(f"{skew_v:.3f}")],
            [_sans("Kurtosis",            color=C_TEXT2), _mono(f"{kurt_v:.3f}")],
            [_sans("5th Percentile",      color=C_TEXT2), _mono(f"{p5_v:,.2f}",  color=C_LOW)],
            [_sans("25th Percentile",     color=C_TEXT2), _mono(f"{p25_v:,.2f}", color=C_MOD)],
            [_sans("75th Percentile",     color=C_TEXT2), _mono(f"{p75_v:,.2f}", color=C_MOD)],
            [_sans("95th Percentile",     color=C_TEXT2), _mono(f"{p95_v:,.2f}", color=C_HIGH)],
            [_sans("Prob(> start)",       color=C_TEXT2), _mono(f"{prob_up:.1f}%", color=prob_up_color)],
            [_sans("Prob(< start)",       color=C_TEXT2), _mono(f"{prob_dn:.1f}%", color=prob_dn_color)],
            [_sans("Sharpe (annualized)", color=C_TEXT2), _mono(f"{sharpe:.3f}",   color=sharpe_color)],
        ]
        wsj_market_table(["Metric", "Value"], stat_rows)
        st.markdown(source_footer([
            {"name": f"Monte Carlo simulation ({n_paths_total:,} runs)",
             "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Stats table render failed")
        st.warning("Statistics table unavailable.")


# ── Section 5: Scenario overlays ─────────────────────────────────────────────

def _render_scenario_overlays(
    base_paths: np.ndarray,
    s0: float,
    sigma: float,
    horizon: int,
    model: str,
    target: str,
    n_paths_total: int,
) -> None:
    section_header(
        "Scenario Overlays",
        "Median paths for macro event scenarios compared to base case.",
    )
    try:
        scenarios = [
            ("Red Sea normalization",  0.10, C_HIGH,
             "Caution",
             "Suez throughput recovers; westbound capacity unwinds and rates "
             "normalize from current premia."),
            ("Trade war escalation",  -0.15, C_LOW,
             "Avoid",
             "Tariff regime tightens demand; volumes contract on Transpac and "
             "Asia-Europe lanes."),
            ("China demand surge",     0.20, C_PURPLE,
             "Monitor",
             "Stimulus-led restock pulls forward demand; rates spike on "
             "container and dry-bulk routes."),
        ]
        scenario_dashes = {
            "Red Sea normalization":  "dot",
            "Trade war escalation":   "dash",
            "China demand surge":     "dashdot",
        }
        T = base_paths.shape[1] - 1
        days = np.arange(T + 1)
        base_mu = st.session_state.get("mc_params", {}).get("mu", 0.05)
        base_sigma = st.session_state.get("mc_params", {}).get("sigma", sigma)

        fig = go.Figure()
        base_median = np.median(base_paths, axis=0)
        fig.add_trace(go.Scatter(
            x=days, y=base_median,
            mode="lines",
            line=dict(color=C_TEXT, width=2.5),
            name="Base case",
        ))
        for name, delta_mu, color, _action, _rationale in scenarios:
            try:
                s_paths = _simulate(s0, base_mu + delta_mu, base_sigma, T, 500, model, seed=99)
                s_median = np.median(s_paths, axis=0)
                fig.add_trace(go.Scatter(
                    x=days, y=s_median,
                    mode="lines",
                    line=dict(color=color, width=2.0, dash=scenario_dashes[name]),
                    name=name,
                ))
            except Exception:
                logger.warning(f"Scenario '{name}' failed to simulate")
        fig.add_hline(y=s0, line_dash="dot", line_color=C_TEXT3, line_width=1)
        apply_dark_layout(fig, title=f"{target} — Scenario Comparison (Median Paths)", height=400)
        fig.update_layout(
            xaxis=dict(title="Days", zeroline=False),
            yaxis=dict(title="Value", zeroline=False),
        )
        st.plotly_chart(fig, use_container_width=True, key="mc_scenario_chart")

        # Scenario insight cards: one per scenario with directional impact.
        cols = st.columns(len(scenarios))
        for col, (name, delta_mu, _color, action, rationale) in zip(cols, scenarios):
            score = min(1.0, max(0.0, abs(delta_mu) / 0.25))
            with col:
                st.markdown(insight_card_html(
                    title=name,
                    score=score,
                    action=action,
                    rationale=f"Drift shock {delta_mu:+.0%}. {rationale}",
                    category="SCENARIO",
                ), unsafe_allow_html=True)
        st.markdown(source_footer([
            {"name": f"Monte Carlo simulation ({n_paths_total:,} runs)",
             "kind": "modeled", "quality": "demo"},
            {"name": "Scenario overlays (500 runs each)",
             "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Scenario overlay render failed")
        st.warning("Scenario overlays unavailable.")


# ── Section 6: VaR / CVaR cards ──────────────────────────────────────────────

def _render_var_cards(paths: np.ndarray, s0: float, n_paths_total: int) -> None:
    section_header(
        "Value at Risk (VaR)",
        "95% confidence VaR and Expected Shortfall based on simulated returns.",
    )
    try:
        daily_returns = paths[:, 1] / s0 - 1
        var_1d_pct = float(np.percentile(daily_returns, 5))
        var_1d_pts = var_1d_pct * s0
        var_10d_pct = var_1d_pct * np.sqrt(10)
        var_10d_pts = var_10d_pct * s0
        tail = daily_returns[daily_returns <= np.percentile(daily_returns, 5)]
        cvar_pct = float(np.mean(tail))
        cvar_pts = cvar_pct * s0
        metric_card_row([
            {"label": "1-Day VaR (95%)",            "value": f"{var_1d_pts:+,.1f}",
             "accent": C_LOW, "sublabel": f"{var_1d_pct*100:.2f}%"},
            {"label": "10-Day VaR (95%)",           "value": f"{var_10d_pts:+,.1f}",
             "accent": C_LOW, "sublabel": f"{var_10d_pct*100:.2f}%"},
            {"label": "Expected Shortfall (CVaR)",  "value": f"{cvar_pts:+,.1f}",
             "accent": C_LOW, "sublabel": f"Worst 5% avg: {cvar_pct*100:.2f}%"},
        ], columns=3)
        st.markdown(source_footer([
            {"name": f"Monte Carlo simulation ({n_paths_total:,} runs)",
             "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("VaR cards render failed")
        st.warning("VaR metrics unavailable.")


# ── Section 7: Path analysis ──────────────────────────────────────────────────

def _render_path_analysis(paths: np.ndarray, s0: float, target: str) -> None:
    section_header(
        "Path Analysis",
        "Breach timing and maximum drawdown distribution across all paths.",
    )
    try:
        up_thresh  = s0 * 1.20
        dn_thresh  = s0 * 0.80
        n_paths, _T_plus1 = paths.shape

        # Time to first breach +20%
        breach_up_days = []
        breach_dn_days = []
        for i in range(n_paths):
            up_idx = np.where(paths[i] >= up_thresh)[0]
            if len(up_idx):
                breach_up_days.append(int(up_idx[0]))
            dn_idx = np.where(paths[i] <= dn_thresh)[0]
            if len(dn_idx):
                breach_dn_days.append(int(dn_idx[0]))

        prob_up = len(breach_up_days) / n_paths * 100
        prob_dn = len(breach_dn_days) / n_paths * 100
        med_up  = float(np.median(breach_up_days)) if breach_up_days else float("nan")
        med_dn  = float(np.median(breach_dn_days)) if breach_dn_days else float("nan")

        # Max drawdown per path
        drawdowns = []
        for i in range(n_paths):
            p = paths[i]
            roll_max = np.maximum.accumulate(p)
            dd = (p - roll_max) / roll_max
            drawdowns.append(float(np.min(dd)) * 100)
        drawdowns_arr = np.array(drawdowns)
        med_dd = float(np.median(drawdowns_arr))
        p95_dd = float(np.percentile(drawdowns_arr, 5))   # worst 5th pct

        def fmt_days(v: float) -> str:
            return f"{v:.0f}d" if not np.isnan(v) else "N/A"

        metric_card_row([
            {"label": "Median days to +20%",   "value": fmt_days(med_up),
             "accent": C_HIGH, "sublabel": f"{prob_up:.1f}% of paths"},
            {"label": "Median days to -20%",   "value": fmt_days(med_dn),
             "accent": C_LOW,  "sublabel": f"{prob_dn:.1f}% of paths"},
            {"label": "Median Max Drawdown",   "value": f"{med_dd:.1f}%",
             "accent": C_MOD,  "sublabel": "across all paths"},
            {"label": "95th-pct Max Drawdown", "value": f"{p95_dd:.1f}%",
             "accent": C_LOW,  "sublabel": "worst 5% of paths"},
        ], columns=4)

        # Drawdown distribution chart
        fig = go.Figure()
        fig.add_trace(go.Histogram(
            x=drawdowns_arr,
            nbinsx=60,
            marker_color=C_LOW,
            opacity=0.75,
            name="Max Drawdown",
        ))
        fig.add_vline(
            x=med_dd, line_dash="dash", line_color=C_MOD, line_width=1.5,
            annotation_text=f"Median {med_dd:.1f}%",
            annotation_font_color=C_MOD,
        )
        fig.add_vline(
            x=p95_dd, line_dash="dot", line_color=C_LOW, line_width=1.5,
            annotation_text=f"P5 {p95_dd:.1f}%",
            annotation_font_color=C_LOW,
        )
        apply_dark_layout(
            fig,
            title=f"{target} — Maximum Drawdown Distribution",
            height=320,
            xaxis=dict(title="Max Drawdown (%)", zeroline=False),
            yaxis=dict(title="Frequency", zeroline=False),
        )
        st.plotly_chart(fig, use_container_width=True, key="mc_drawdown_dist")
    except Exception:
        logger.exception("Path analysis render failed")
        st.warning("Path analysis unavailable.")


# ── Main render ───────────────────────────────────────────────────────────────

def render(stock_data=None, macro_data=None, freight_data=None) -> None:
    """Monte Carlo simulation dashboard for shipping market forecasting."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('monte_carlo'):
        try:
            page_header(
                title="Monte Carlo Simulation",
                subtitle="Stochastic path simulation for shipping indices and equities using GBM and Jump-Diffusion models.",
                badge_text="MODELED",
                badge_color=C_ACCENT,
            )
        except Exception:
            logger.exception("Header render failed")

        # Section 1: config form
        try:
            params = _render_config_form()
            if params is not None:
                st.session_state["mc_params"] = params
                st.session_state["mc_paths"] = _simulate(
                    S0=params["s0"],
                    mu_annual=params["mu"],
                    sigma_annual=params["sigma"],
                    T=params["horizon"],
                    n_paths=params["n_paths"],
                    model=params["model"],
                )
        except Exception:
            logger.exception("Config form failed")
            st.error("Configuration form error.")

        # Retrieve cached results
        paths = st.session_state.get("mc_paths")
        params = st.session_state.get("mc_params", {})

        if paths is None:
            placeholder = (
                f'<div style="font-family:var(--serif);font-size:1.02rem;'
                f'font-weight:700;color:{C_TEXT};margin-bottom:6px;">'
                f'Awaiting simulation</div>'
                f'<p style="font-family:var(--sans);font-size:0.84rem;'
                f'color:{C_TEXT3};line-height:1.6;margin:0;">'
                f'Set a target, horizon and volatility above, then click '
                f'<strong style="color:{C_TEXT2};">Run Simulation</strong> — the '
                f'fan chart, horizon distribution, VaR and path analytics will '
                f'populate below.</p>'
            )
            st.markdown(
                gradient_card(placeholder, border_color=C_ACCENT),
                unsafe_allow_html=True,
            )
            return

        target  = params.get("target", "BDI")
        s0      = params.get("s0", 1000.0)
        sigma   = params.get("sigma", 0.35)
        horizon = params.get("horizon", 90)
        model   = params.get("model", "GBM")

        n_paths_actual = paths.shape[0]

        # Quick run summary
        try:
            finals = paths[:, -1]
            med_final = float(np.median(finals))
            chg_pct = (med_final / s0 - 1) * 100
            chg_col = C_HIGH if chg_pct >= 0 else C_LOW
            metric_card_row([
                {"label": "Target",         "value": target,
                 "accent": C_ACCENT, "sublabel": f"{model} model"},
                {"label": "Paths",          "value": f"{n_paths_actual:,}",
                 "accent": C_TEXT,   "sublabel": f"Horizon: {horizon}d"},
                {"label": "Start",          "value": f"{s0:,.2f}",
                 "accent": C_TEXT,   "sublabel": "initial value"},
                {"label": "Median at T",    "value": f"{med_final:,.2f}",
                 "accent": chg_col,  "sublabel": f"{chg_pct:+.1f}%"},
                {"label": "Annualized Vol", "value": f"{sigma*100:.0f}%",
                 "accent": C_MOD,    "sublabel": "input parameter"},
            ], columns=5)
        except Exception:
            logger.exception("Summary KPI row failed")

        # Sections 2–7
        section_divider("Path Simulation")
        _render_fan_chart(paths, target, s0, n_paths_actual)
        _render_horizon_dist(paths, s0, target, n_paths_actual)
        _render_stats_table(paths, s0, horizon, sigma, n_paths_actual)

        section_divider("Scenario & Risk")
        _render_scenario_overlays(paths, s0, sigma, horizon, model, target, n_paths_actual)
        _render_var_cards(paths, s0, n_paths_actual)
        _render_path_analysis(paths, s0, target)
