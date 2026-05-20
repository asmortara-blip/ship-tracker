"""
Scenario Analysis & Stress Testing Tab
========================================
Institutional scenario analysis covering:

  Section 1 - Scenario Dashboard (hero metrics)
  Section 2 - Base / Bull / Bear 3-column comparison
  Section 3 - Scenario Comparison Table (6 scenarios x 8 metrics)
  Section 4 - Interactive Scenario Builder (st.form + sliders)
  Section 5 - Event Probability Tracker
  Section 6 - Monte Carlo Fan Chart (500 paths, 90-day horizon)
"""
from __future__ import annotations

import numpy as np
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
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)


# ── Data-source provenance ────────────────────────────────────────────────────
_MACRO_SRC = DataSource.modeled(
    "Macro scenario engine",
    notes="Base/Bull/Bear cases derived from current BDI + WCI inputs.",
)
_EVENT_SRC = DataSource.modeled(
    "Event probability tracker",
    notes="Analyst-estimated probabilities; calibrate against forecasting markets.",
)
_MC_SRC = DataSource.modeled(
    "BDI Monte Carlo (GBM)",
    notes="500 paths, geometric Brownian motion, 90-day horizon.",
)
_CUSTOM_SRC = DataSource.demo("User-defined scenarios (session state)")


# ── Helpers ────────────────────────────────────────────────────────────────────
def _val(data: dict | None, *keys, default=None):
    try:
        v = data
        for k in keys:
            v = v[k]
        return v
    except Exception:
        return default


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


def _delta_color(pct_str: str) -> str:
    try:
        v = float(pct_str.replace("%", "").replace("+", "").split("–")[0])
        if v > 0:
            return C_HIGH
        if v < 0:
            return C_LOW
    except Exception:
        pass
    return C_TEXT2


# ── Section 1: Scenario Dashboard ──────────────────────────────────────────────
def _render_dashboard(macro_data, freight_data):
    try:
        active = 6
        base_prob = 60
        upside_skew = 25
        downside_skew = 15

        bdi_val = _val(macro_data, "bdi", "value", default=1_850)
        wci_val = _val(freight_data, "wci", "current", default=3_200)

        skew_color = C_HIGH if upside_skew >= downside_skew else C_LOW
        skew_label = "Upside" if upside_skew >= downside_skew else "Downside"
        skew_pct   = upside_skew if upside_skew >= downside_skew else downside_skew

        metric_card_row(
            [
                {"label": "Active Scenarios", "value": str(active), "accent": C_ACCENT,
                 "delta": "Tracked this quarter", "delta_color": C_TEXT3},
                {"label": "Base Case Probability", "value": f"{base_prob}%", "accent": C_TEXT,
                 "delta": "Central estimate", "delta_color": C_TEXT3},
                {"label": "Risk Skew", "value": skew_label, "accent": skew_color,
                 "delta": f"{skew_pct}% probability weight", "delta_color": skew_color},
                {"label": "Current BDI", "value": f"{int(bdi_val):,}", "accent": C_TEXT,
                 "delta": "Baltic Dry Index", "delta_color": C_TEXT3},
                {"label": "Current WCI", "value": f"${int(wci_val):,}", "accent": C_TEXT,
                 "delta": "World Container Index", "delta_color": C_TEXT3},
            ],
            columns=5,
        )
        st.markdown(source_footer([_MACRO_SRC]), unsafe_allow_html=True)
    except Exception as exc:
        logger.exception("dashboard hero error")
        st.error(f"Dashboard error: {exc}")


# ── Section 2: Base / Bull / Bear Comparison ───────────────────────────────────
def _render_three_scenarios(macro_data, freight_data):
    """Render base/bull/bear as a single comparison table.

    Old implementation drew three large composite divs side-by-side; this
    consolidates them into a wsj_market_table with one column per scenario,
    paired with per-scenario metric_card_row blocks for the headline numbers.
    """
    try:
        bdi_base = int(_val(macro_data, "bdi", "value", default=1_850))
        bdi_bull = int(bdi_base * 1.45)
        bdi_bear = int(bdi_base * 0.62)

        wci_base = int(_val(freight_data, "wci", "current", default=3_200))
        wci_bull = int(wci_base * 1.60)
        wci_bear = int(wci_base * 0.55)

        scenarios = [
            {
                "label": "BASE CASE", "prob": 60, "color": C_ACCENT,
                "bdi": bdi_base, "wci": wci_base, "util": 84,
                "fr_impact": "+0%", "fr_color": C_TEXT2,
                "headline": "Moderate growth, stable rates",
                "equity": "Neutral - sector inline with market",
                "equity_color": C_TEXT2,
                "assumptions": [
                    "Global GDP growth ~2.4%",
                    "Fleet growth 3.2% YoY",
                    "Red Sea disruptions persist H1",
                    "China PMI stabilizes 50-52",
                    "Oil price $75-$85/bbl range",
                ],
            },
            {
                "label": "BULL CASE", "prob": 25, "color": C_HIGH,
                "bdi": bdi_bull, "wci": wci_bull, "util": 92,
                "fr_impact": "+38-55%", "fr_color": C_HIGH,
                "headline": "Supply disruptions + demand recovery",
                "equity": "Strongly bullish - shipping equities +30-60%",
                "equity_color": C_HIGH,
                "assumptions": [
                    "Chokepoint disruptions extend 12+ mo",
                    "China stimulus drives import surge",
                    "New orderbook delays slip further",
                    "Port congestion re-emerges globally",
                    "VLCC demand spikes on rerouting",
                ],
            },
            {
                "label": "BEAR CASE", "prob": 15, "color": C_LOW,
                "bdi": bdi_bear, "wci": wci_bear, "util": 72,
                "fr_impact": "-30-45%", "fr_color": C_LOW,
                "headline": "Demand slowdown + capacity glut",
                "equity": "Bearish - shipping equities -20-40%",
                "equity_color": C_LOW,
                "assumptions": [
                    "US recession reduces import demand",
                    "Newbuild deliveries accelerate",
                    "China property sector re-deteriorates",
                    "Red Sea normalizes, routes shorten",
                    "Consumer spending contracts",
                ],
            },
        ]

        # Per-scenario headline metrics row (probabilities and overall lean).
        metric_card_row(
            [
                {"label": sc["label"], "value": f"{sc['prob']}%",
                 "accent": sc["color"], "delta": sc["headline"],
                 "delta_color": C_TEXT3}
                for sc in scenarios
            ],
            columns=3,
        )

        # Quantitative comparison table: rows are metrics, columns are scenarios.
        metric_rows = [
            ["BDI"] + [_mono(f"{sc['bdi']:,}", color=sc["color"]) for sc in scenarios],
            ["WCI ($/FEU)"] + [_mono(f"${sc['wci']:,}", color=sc["color"]) for sc in scenarios],
            ["Fleet Utilization"] + [_mono(f"{sc['util']}%", color=sc["color"]) for sc in scenarios],
            ["Rate Impact"] + [_mono(sc["fr_impact"], color=sc["fr_color"]) for sc in scenarios],
            ["Equity Sector Impact"] + [_sans(sc["equity"], color=sc["equity_color"], weight=600) for sc in scenarios],
        ]
        # Apply Metric label styling (first cell of each row).
        styled_rows = [
            [_sans(row[0], color=C_TEXT, weight=600)] + row[1:]
            for row in metric_rows
        ]
        wsj_market_table(
            headers=["Metric"] + [sc["label"] for sc in scenarios],
            rows=styled_rows,
        )

        # Assumptions: one row per assumption index, scenarios as columns.
        max_assumptions = max(len(sc["assumptions"]) for sc in scenarios)
        assumption_rows = []
        for i in range(max_assumptions):
            row = [_sans(f"#{i+1}", color=C_TEXT3, weight=600)]
            for sc in scenarios:
                if i < len(sc["assumptions"]):
                    row.append(_sans(sc["assumptions"][i], color=C_TEXT2))
                else:
                    row.append(_sans("--", color=C_TEXT3))
            assumption_rows.append(row)

        section_header(
            "Key Assumptions by Scenario",
            subtitle="Macro drivers underlying each case",
        )
        wsj_market_table(
            headers=["#"] + [sc["label"] for sc in scenarios],
            rows=assumption_rows,
        )
        st.markdown(source_footer([_MACRO_SRC]), unsafe_allow_html=True)
    except Exception as exc:
        logger.exception("three-scenario render error")
        st.error(f"Three-scenario error: {exc}")


# ── Section 3: Scenario Comparison Table ──────────────────────────────────────
def _render_comparison_table():
    try:
        scenarios = [
            ("Base Case",               C_ACCENT,   "+0%",   "+0%",   "+0%",   "8-12%",  60, "12 mo", "PMI inflection",     "Recession shock"),
            ("Bull - Supply Shock",     C_HIGH,     "+42%",  "+55%",  "+38%",  "28-40%", 25, "6-9 mo","Chokepoint event",    "Normalization"),
            ("Bull - Demand Surge",     "#34d399",  "+28%",  "+35%",  "+18%",  "18-28%", 15, "9-15mo","China stimulus",      "Overcapacity"),
            ("Bear - Recession",        C_LOW,      "-38%",  "-42%",  "-25%",  "-15-25%",12, "12-18mo","US GDP<0",           "Prolonged slump"),
            ("Bear - Oversupply Glut",  "#f87171",  "-22%",  "-30%",  "-15%",  "-10-18%",18, "18-24mo","Delivery surge",     "Fleet scrapping lag"),
            ("Tail - Geopolitical",     "#a855f7",  "+65%",  "+80%",  "+55%",  "35-55%",  5, "3-6 mo","Military conflict",   "Rapid resolution"),
        ]

        rows = []
        for name, color, bdi_d, wci_d, vlcc_d, port_r, prob, horizon, trigger, risk in scenarios:
            rows.append([
                _sans(name, color=color, weight=700),
                _mono(bdi_d, color=_delta_color(bdi_d)),
                _mono(wci_d, color=_delta_color(wci_d)),
                _mono(vlcc_d, color=_delta_color(vlcc_d)),
                _mono(port_r, color=_delta_color(port_r)),
                badge(f"{prob}%", color=color),
                _sans(horizon, color=C_TEXT2),
                _sans(trigger, color=C_TEXT2),
                _sans(risk, color=C_TEXT3),
            ])

        section_header(
            "Scenario Comparison Matrix",
            subtitle="Six scenarios scored across rate, volatility, and probability dimensions",
        )
        wsj_market_table(
            headers=["Scenario", "BDI Δ", "WCI Δ", "VLCC Δ", "Portfolio Return", "Probability", "Time Horizon", "Key Trigger", "Key Risk"],
            rows=rows,
        )
        st.markdown(source_footer([_MACRO_SRC]), unsafe_allow_html=True)
    except Exception as exc:
        logger.exception("comparison table error")
        st.error(f"Comparison table error: {exc}")


# ── Section 4: Scenario Builder ────────────────────────────────────────────────
def _render_scenario_builder():
    try:
        if "custom_scenarios" not in st.session_state:
            st.session_state.custom_scenarios = []

        section_header(
            "Interactive Scenario Builder",
            subtitle="Adjust macro parameters to estimate freight rate and BDI impact",
        )

        with st.form("scenario_builder_form"):
            c1, c2 = st.columns(2)
            with c1:
                gdp = st.slider("GDP Growth (%)", min_value=-2.0, max_value=4.0, value=2.4, step=0.1)
                oil = st.slider("Oil Price ($/bbl)", min_value=40, max_value=140, value=80, step=5)
            with c2:
                fleet_growth = st.slider("Fleet Growth (%)", min_value=-2.0, max_value=6.0, value=3.2, step=0.1)
                demand_growth = st.slider("Trade Demand Growth (%)", min_value=-3.0, max_value=5.0, value=2.8, step=0.1)

            scenario_name = st.text_input("Scenario Name", value="Custom Scenario", max_chars=40)
            submitted = st.form_submit_button("Calculate & Save Scenario", use_container_width=True)

        if submitted:
            try:
                supply_demand_gap = demand_growth - fleet_growth
                gdp_factor = (gdp - 2.0) * 8.0
                oil_factor = (oil - 80) * 0.15
                fr_impact = supply_demand_gap * 12.0 + gdp_factor + oil_factor
                bdi_impact = fr_impact * 0.85

                impact_color = C_HIGH if fr_impact > 0 else (C_LOW if fr_impact < 0 else C_TEXT2)
                bdi_color = C_HIGH if bdi_impact > 0 else (C_LOW if bdi_impact < 0 else C_TEXT2)

                new_sc = {
                    "name": scenario_name,
                    "gdp": gdp,
                    "oil": oil,
                    "fleet": fleet_growth,
                    "demand": demand_growth,
                    "fr_impact": fr_impact,
                    "bdi_impact": bdi_impact,
                }
                st.session_state.custom_scenarios.append(new_sc)

                metric_card_row(
                    [
                        {"label": "Freight Rate Impact", "value": f"{fr_impact:+.1f}%", "accent": impact_color},
                        {"label": "BDI Impact", "value": f"{bdi_impact:+.1f}%", "accent": bdi_color},
                        {"label": "Supply/Demand Gap", "value": f"{supply_demand_gap:+.1f}pp", "accent": C_TEXT},
                    ],
                    columns=3,
                )
                st.caption(
                    f"{scenario_name} saved. {len(st.session_state.custom_scenarios)} custom scenario(s) stored this session."
                )
            except Exception as calc_exc:
                logger.exception("scenario builder calculation error")
                st.error(f"Calculation error: {calc_exc}")

        saved = st.session_state.get("custom_scenarios", [])
        if len(saved) > 1:
            section_header("Saved Custom Scenarios")
            rows = []
            for s in saved[-5:]:
                rows.append([
                    _sans(s["name"], color=C_TEXT, weight=600),
                    _mono(f'{s["gdp"]:+.1f}%', color=C_TEXT2),
                    _mono(f'${s["oil"]}', color=C_TEXT2),
                    _mono(f'{s["fleet"]:+.1f}%', color=C_TEXT2),
                    _mono(f'{s["demand"]:+.1f}%', color=C_TEXT2),
                    _mono(f'{s["fr_impact"]:+.1f}%', color=C_HIGH if s["fr_impact"] > 0 else C_LOW),
                    _mono(f'{s["bdi_impact"]:+.1f}%', color=C_HIGH if s["bdi_impact"] > 0 else C_LOW),
                ])
            wsj_market_table(
                headers=["Name", "GDP", "Oil", "Fleet Δ", "Demand Δ", "Rate Impact", "BDI Impact"],
                rows=rows,
            )
            st.markdown(source_footer([_CUSTOM_SRC]), unsafe_allow_html=True)
    except Exception as exc:
        logger.exception("scenario builder error")
        st.error(f"Scenario builder error: {exc}")


# ── Section 5: Event Probability Tracker ──────────────────────────────────────
def _render_event_tracker():
    try:
        events = [
            {"event": "Red Sea Normalization",      "timing": "Q3 2026",    "prob": 35, "impact": "HIGH", "direction": "Bearish", "dir_color": C_LOW,  "indicator": "Houthi ceasefire reports, transit volume data"},
            {"event": "US Recession",               "timing": "Q4 2026",    "prob": 20, "impact": "HIGH", "direction": "Bearish", "dir_color": C_LOW,  "indicator": "GDP prints, unemployment claims, yield curve"},
            {"event": "China Demand Surge",         "timing": "Q2-Q3 2026", "prob": 25, "impact": "HIGH", "direction": "Bullish", "dir_color": C_HIGH, "indicator": "PBoC stimulus, PMI >52, import growth >8%"},
            {"event": "Major Newbuild Oversupply",  "timing": "2026-2027",  "prob": 30, "impact": "MOD",  "direction": "Bearish", "dir_color": C_LOW,  "indicator": "Orderbook delivery schedule, scrapping rates"},
            {"event": "Panama Drought Persists",    "timing": "Q2-Q4 2026", "prob": 40, "impact": "MOD",  "direction": "Bullish", "dir_color": C_HIGH, "indicator": "Gatun Lake water levels, canal authority bulletins"},
            {"event": "New Chokepoint Disruption",  "timing": "2026",       "prob": 15, "impact": "HIGH", "direction": "Bullish", "dir_color": C_HIGH, "indicator": "Geopolitical risk indices, naval incident reports"},
        ]
        impact_colors = {"HIGH": C_LOW, "MOD": C_MOD, "LOW": C_HIGH}

        section_header(
            "Event Probability Tracker",
            subtitle="Market-moving events ranked by probability and impact",
        )

        rows = []
        for ev in events:
            ic = impact_colors.get(ev["impact"], C_TEXT2)
            rows.append([
                _sans(ev["event"], color=C_TEXT, weight=600),
                _sans(ev["timing"], color=C_TEXT2),
                _mono(f"{ev['prob']}%", color=ev["dir_color"]),
                badge(ev["impact"], color=ic),
                badge(ev["direction"], color=ev["dir_color"]),
                _sans(ev["indicator"], color=C_TEXT3),
            ])

        wsj_market_table(
            headers=["Event", "Timing", "Probability", "Impact", "Direction", "Key Indicator"],
            rows=rows,
        )
        st.markdown(source_footer([_EVENT_SRC]), unsafe_allow_html=True)
    except Exception as exc:
        logger.exception("event tracker error")
        st.error(f"Event tracker error: {exc}")


# ── Section 6: Monte Carlo Fan Chart ──────────────────────────────────────────
def _render_monte_carlo(macro_data):
    try:
        bdi_start = float(_val(macro_data, "bdi", "value", default=1_850))
        n_paths = 500
        horizon  = 90
        mu       = 0.0003
        sigma    = 0.028

        rng = np.random.default_rng(seed=42)
        shocks = rng.normal(mu, sigma, size=(n_paths, horizon))
        log_returns = np.cumsum(shocks, axis=1)
        paths = bdi_start * np.exp(log_returns)

        days = np.arange(1, horizon + 1)
        pcts = [5, 25, 50, 75, 95]
        bands = {p: np.percentile(paths, p, axis=0) for p in pcts}

        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=np.concatenate([[0], days]),
            y=np.concatenate([[bdi_start], bands[95]]),
            mode="lines", line=dict(width=0),
            showlegend=False, name="95th",
            hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=np.concatenate([[0], days]),
            y=np.concatenate([[bdi_start], bands[5]]),
            mode="lines", line=dict(width=0),
            fill="tonexty",
            fillcolor="rgba(53,114,176,0.08)",
            name="90% CI",
            hovertemplate="Day %{x}<br>5-95th: %{y:.0f}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=np.concatenate([[0], days]),
            y=np.concatenate([[bdi_start], bands[75]]),
            mode="lines", line=dict(width=0),
            showlegend=False, name="75th",
            hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=np.concatenate([[0], days]),
            y=np.concatenate([[bdi_start], bands[25]]),
            mode="lines", line=dict(width=0),
            fill="tonexty",
            fillcolor="rgba(53,114,176,0.15)",
            name="50% CI",
            hovertemplate="Day %{x}<br>25-75th: %{y:.0f}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=np.concatenate([[0], days]),
            y=np.concatenate([[bdi_start], bands[50]]),
            mode="lines",
            line=dict(color=C_ACCENT, width=2.5),
            name="Median",
            hovertemplate="Day %{x}<br>Median BDI: %{y:.0f}<extra></extra>",
        ))

        sample_paths = paths[rng.integers(0, n_paths, size=30)]
        for path in sample_paths:
            fig.add_trace(go.Scatter(
                x=days, y=path,
                mode="lines",
                line=dict(color="rgba(53,114,176,0.06)", width=1),
                showlegend=False,
                hoverinfo="skip",
            ))

        fig.add_hline(
            y=bdi_start,
            line_dash="dash",
            line_color=C_TEXT3,
            annotation_text=f"Current BDI: {int(bdi_start):,}",
            annotation_font_color=C_TEXT3,
            annotation_font_size=11,
        )

        apply_dark_layout(fig, height=420, title="BDI Monte Carlo Simulation - 500 Paths, 90-Day Horizon")
        fig.update_layout(
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            hovermode="x unified",
        )
        fig.update_xaxes(title="Trading Days")
        fig.update_yaxes(title="Baltic Dry Index", tickformat=",d")
        st.plotly_chart(fig, use_container_width=True)

        final_median = bands[50][-1]
        final_5th    = bands[5][-1]
        final_95th   = bands[95][-1]
        med_chg = (final_median / bdi_start - 1) * 100
        lo_chg  = (final_5th / bdi_start - 1) * 100
        hi_chg  = (final_95th / bdi_start - 1) * 100

        metric_card_row(
            [
                {"label": "90-Day Median", "value": f"{int(final_median):,}", "accent": C_TEXT,
                 "delta": f"{med_chg:+.1f}% vs today",
                 "delta_color": C_HIGH if med_chg >= 0 else C_LOW},
                {"label": "5th Percentile (Bear)", "value": f"{int(final_5th):,}", "accent": C_LOW,
                 "delta": f"{lo_chg:+.1f}% vs today", "delta_color": C_LOW},
                {"label": "95th Percentile (Bull)", "value": f"{int(final_95th):,}", "accent": C_HIGH,
                 "delta": f"{hi_chg:+.1f}% vs today", "delta_color": C_HIGH},
                {"label": "Simulated Paths", "value": "500", "accent": C_TEXT,
                 "delta": f"GBM, sigma={sigma:.1%}/day", "delta_color": C_TEXT2},
            ],
            columns=4,
        )
        st.markdown(source_footer([_MC_SRC]), unsafe_allow_html=True)
    except Exception as exc:
        logger.exception("Monte Carlo fan chart error")
        st.error(f"Monte Carlo error: {exc}")


# ── Canonical Catalog (state.scenarios) ───────────────────────────────────────
def _render_canonical_catalog():
    """Browser for the canonical what-if catalog from state.scenarios.

    Surfaces the schema (Scenario / ScenarioShock) live: each catalog entry
    is a row in a wsj_market_table, with an Activate button per row that
    flips the active scenario in session state. A small "Effect Preview"
    grid below shows the active scenario's impact on representative numbers
    (Asia-Europe rate, BDI, ZIM return, transit days) so the overlay isn't
    an abstraction — users see the actual multipliers / addends in action.
    """
    try:
        from state.scenarios import (
            SCENARIO_CATALOG,
            active_scenario,
            list_scenarios,
            overlay_addend,
            overlay_multiplier,
            overlay_value,
            set_active_scenario,
        )

        section_header(
            "Canonical What-If Catalog",
            subtitle="Six pre-built scenarios. Activate one to overlay it across every tab that reads the scenario via state.scenarios.",
        )

        current = active_scenario()

        # ── Active-scenario banner ─────────────────────────────────────────
        if current is not None:
            cols = st.columns([5, 1], gap="small")
            with cols[0]:
                st.markdown(
                    f'<div style="background:rgba(53,114,176,0.10);'
                    f'border-left:3px solid {C_ACCENT};padding:10px 14px;border-radius:3px;'
                    f'margin-bottom:8px">'
                    f'<div style="font-size:0.72rem;text-transform:uppercase;letter-spacing:0.12em;'
                    f'color:{C_TEXT3};font-weight:600">Active Scenario · {current.category}</div>'
                    f'<div style="font-size:1.05rem;color:{C_TEXT};font-weight:600;margin-top:2px">'
                    f'{current.name}</div>'
                    f'<div style="font-size:0.78rem;color:{C_TEXT2};margin-top:4px;line-height:1.4">'
                    f'{current.summary}</div></div>',
                    unsafe_allow_html=True,
                )
            with cols[1]:
                if st.button("Clear", key="scn_clear", use_container_width=True):
                    set_active_scenario(None)
                    st.rerun()
        else:
            st.markdown(
                f'<div style="font-size:0.78rem;color:{C_TEXT3};padding:6px 0 10px 0">'
                f'No active scenario. Pick one below or from the sidebar selector.</div>',
                unsafe_allow_html=True,
            )

        # ── Catalog table with per-row activate buttons ────────────────────
        scenarios = list_scenarios()
        for scen in scenarios:
            is_active = current is not None and current.id == scen.id
            cols = st.columns([1.8, 1.0, 0.7, 4.2, 0.8], gap="small")
            with cols[0]:
                st.markdown(
                    f'<div style="font-size:0.85rem;color:{C_TEXT};font-weight:600;'
                    f'font-family:Libre Franklin,sans-serif">{scen.name}</div>',
                    unsafe_allow_html=True,
                )
            with cols[1]:
                _category_color = {
                    "Geopolitical": C_LOW, "Weather": C_MOD, "Macro": C_ACCENT,
                    "Demand": "#7c6eaf", "Operational": C_TEXT2,
                }.get(scen.category, C_TEXT2)
                st.markdown(badge(scen.category, color=_category_color), unsafe_allow_html=True)
            with cols[2]:
                st.markdown(
                    f'<div style="font-size:0.78rem;color:{C_TEXT2};'
                    f'font-family:JetBrains Mono,monospace">{len(scen.shocks)} shock'
                    f'{"" if len(scen.shocks)==1 else "s"}</div>',
                    unsafe_allow_html=True,
                )
            with cols[3]:
                st.markdown(
                    f'<div style="font-size:0.78rem;color:{C_TEXT2};line-height:1.4">'
                    f'{scen.summary}</div>',
                    unsafe_allow_html=True,
                )
            with cols[4]:
                btn_label = "Active" if is_active else "Activate"
                if st.button(btn_label, key=f"scn_btn_{scen.id}",
                             use_container_width=True, disabled=is_active):
                    set_active_scenario(scen.id)
                    st.rerun()
            st.markdown(
                f'<div style="border-bottom:1px dotted rgba(232,230,225,0.06);'
                f'margin:6px 0 8px 0"></div>', unsafe_allow_html=True,
            )

        # ── Effect Preview — show before/after on representative numbers ────
        st.markdown("<br>", unsafe_allow_html=True)
        section_header(
            "Effect Preview",
            subtitle="Representative numbers under the active scenario. Cells show the multiplier × base + addend output from state.scenarios.overlay_value.",
        )

        previews: list[tuple[str, str, str, float, str]] = [
            # (target_key,                          row label,                  unit,    base,    note)
            ("route:transpacific_eb.rate",          "Trans-Pacific EB rate",     "$/FEU", 2500.0, "Container spot"),
            ("route:asia_europe.rate",              "Asia-Europe rate",          "$/FEU", 2000.0, "Container spot"),
            ("route:middle_east_to_europe.rate",    "Middle East → Europe rate", "$/FEU", 1800.0, "Container spot"),
            ("route:asia_europe.transit_days",      "Asia-Europe transit",       "days",  25.0,   "Nominal time"),
            ("macro:bdi.level",                     "Baltic Dry Index",          "pts",   1450.0, "Composite"),
            ("commodity:wti.spot",                  "WTI crude spot",            "$/bbl", 78.0,   "Front-month"),
            ("ticker:ZIM.return",                   "ZIM expected return",       "x",     1.00,   "Per-period multiplier"),
            ("ticker:MATX.return",                  "MATX expected return",      "x",     1.00,   "Per-period multiplier"),
        ]

        headers = ["Target", "Base", "Mult", "Addend", "Result", "Δ vs Base", "Note"]
        rows: list[list[str]] = []
        for target, label, unit, base, note in previews:
            mult = overlay_multiplier(target)
            addend = overlay_addend(target)
            result = overlay_value(target, base)
            if base != 0:
                pct_delta = (result - base) / abs(base) * 100.0
            else:
                pct_delta = 0.0
            delta_color = (
                C_HIGH if pct_delta > 0.5 else
                (C_LOW if pct_delta < -0.5 else C_TEXT2)
            )
            mult_color = (
                C_HIGH if mult > 1.001 else
                (C_LOW if mult < 0.999 else C_TEXT3)
            )
            base_fmt = (
                f"{base:,.2f}" if unit == "x" else
                f"{int(base):,}" if unit in ("pts", "$/FEU") else
                f"{base:,.1f}"
            )
            result_fmt = (
                f"{result:,.2f}" if unit == "x" else
                f"{int(round(result)):,}" if unit in ("pts", "$/FEU") else
                f"{result:,.1f}"
            )
            rows.append([
                _mono(f"{label}", color=C_TEXT) + _sans(
                    f" · {unit}", color=C_TEXT3
                ),
                _mono(base_fmt, color=C_TEXT2),
                _mono(f"{mult:5.2f}×", color=mult_color),
                _mono(f"{addend:+5.1f}" if addend else "—", color=C_TEXT2),
                _mono(result_fmt, color=C_TEXT),
                _mono(f"{pct_delta:+5.1f}%" if base else "—", color=delta_color),
                _sans(note, color=C_TEXT3),
            ])
        wsj_market_table(headers, rows)

        st.markdown(
            source_footer([
                DataSource.modeled(
                    "What-If Overlay",
                    notes="Base numbers are illustrative anchors; the multiplier and addend columns are the actual shock values from the active scenario's matching ScenarioShock records.",
                ),
            ]),
            unsafe_allow_html=True,
        )

    except Exception as exc:
        logger.exception("Canonical scenario catalog render failed")
        st.error(f"Canonical catalog unavailable: {exc}")


# ── Main Entry Point ───────────────────────────────────────────────────────────
def render(macro_data=None, freight_data=None, insights=None):
    """Render the Scenario Analysis & Stress Testing tab."""
    logger.info("tab_scenarios.render() called")
    try:
        page_header(
            title="Scenario Analysis & Stress Testing",
            subtitle="Institutional scenario modeling - base/bull/bear cases, event probabilities, and Monte Carlo simulation",
            badge_text="SCENARIOS",
            badge_color=C_ACCENT,
        )

        _render_canonical_catalog()

        section_divider("Reference Cases")

        _render_dashboard(macro_data, freight_data)

        section_header(
            "Base / Bull / Bear Comparison",
            subtitle="Three reference cases scaled from current BDI and WCI prints",
        )
        _render_three_scenarios(macro_data, freight_data)

        _render_comparison_table()

        section_divider("Custom Modeling")

        _render_scenario_builder()

        section_divider("Events & Simulation")

        _render_event_tracker()

        section_header(
            "Monte Carlo Simulation",
            subtitle="Stochastic BDI paths to bound rate volatility expectations",
        )
        _render_monte_carlo(macro_data)

    except Exception as exc:
        logger.exception("tab_scenarios.render() top-level error")
        st.error(f"Scenario tab error: {exc}")
