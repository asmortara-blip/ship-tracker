"""tab_valuation.py — the illustrative equity-valuation suite.

A single tab that surfaces the four classic valuation models built in
``processing.valuation`` — a multi-stage DCF, a worst/base/best scenario set, a
Monte-Carlo percentile summary and a one-at-a-time sensitivity tornado — plus
the disruption-linkage layer that ties the worst case to a modeled severity.

HONESTY IS THE POINT
--------------------
A provenance audit established that **no real company fundamentals reach any
analytics module** on this platform (see ``docs/DATA_PROVENANCE.md``): there is
no free-cash-flow, debt, revenue or shares-outstanding feed wired in. Every
per-share number this tab shows is therefore the output of **assumed inputs** —
an illustration of the mechanics, never a measured valuation and never a price
target. The module's :data:`processing.valuation.DISCLAIMER` is rendered in a
prominent ``st.warning`` near the top, the input controls are openly labelled
as assumptions, and every result echoes which fundamentals are ``"assumed"``.

The tab is read-only over a pure module: the operator picks an illustrative
ticker and nudges a handful of assumed inputs with sliders; everything below
recomputes from a single :class:`ValuationInputs` object.

Five sections, top to bottom:

  1. **Assumed inputs** — a ticker ``selectbox`` (seeded from
     ``illustrative_inputs``) + sliders for fcf_0 / fcf_growth / discount_rate /
     terminal_growth, which assemble the ``ValuationInputs``.
  2. **DCF** — ``dcf_valuation``: per-share + equity value + EV as metric cards,
     with the PV decomposition and the assumed-input echo.
  3. **Scenarios** — ``scenario_valuation`` over a worst/base/best set whose
     worst case is driven by a disruption-severity slider
     (``build_disruption_scenarios``); a per-scenario bar + table.
  4. **Monte-Carlo** — ``monte_carlo_valuation`` (seed fixed for determinism).
     The module returns a *percentile summary* (mean/median/std + a
     ``percentiles`` map), NOT raw draws, so the distribution is drawn as a
     p5–p95 percentile fan (``go.Bar``) rather than a histogram, with the
     summary stats on metric cards.
  5. **Sensitivity** — ``sensitivity_analysis``: a horizontal tornado of the
     per-share swing per input (sorted), plus the underlying table.

Data flow (all pure below the render wrapper / figure-builders — no ``st.*``
inside the builders, so they are independently unit-testable):

  illustrative_inputs(...) → ValuationInputs (mutated by the sliders)
    → dcf_valuation(...)            # headline + PV decomposition
    → build_disruption_scenarios(...) → scenario_valuation(...)   # worst/base/best
    → monte_carlo_valuation(..., seed=…)                          # percentile fan
    → sensitivity_analysis(...)                                   # tornado
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_BG,
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
    source_footer,
    wsj_market_table,
)


# Provenance for the footer. The whole suite is a pure derivation over ASSUMED
# inputs — there are no real fundamentals wired in — so it is itself MODELED and
# the notes say so loudly.
VALUATION_SOURCE = DataSource.modeled(
    "Illustrative Valuation",
    notes=(
        "DCF/scenario/MC/sensitivity on ASSUMED inputs; no real fundamentals "
        "are wired in. Not investment advice."
    ),
)

# A small, illustrative roster of listed shipping names. These are LABELS only —
# the platform has no fundamentals feed, so picking a ticker simply seeds the
# same assumed-default input set; it does not load measured financials.
_TICKERS: tuple[str, ...] = ("ZIM", "MATX", "SBLK", "DAC", "CMRE")

# Per-scenario colour: worst is the WSJ market red, base the steel-blue accent,
# best the market green. Any other scenario name falls back to neutral grey.
_SCENARIO_COLOR: dict[str, str] = {
    "worst": C_LOW,
    "base": C_ACCENT,
    "best": C_HIGH,
}

# Human labels for the six fundamental inputs (used in the sensitivity table /
# tornado so the axis doesn't read raw field names).
_INPUT_LABEL: dict[str, str] = {
    "fcf_0": "FCF₀",
    "fcf_growth": "FCF growth",
    "discount_rate": "Discount rate",
    "terminal_growth": "Terminal growth",
    "shares_outstanding": "Shares out.",
    "net_debt": "Net debt",
}


# ── Pure figure-builders (no st.* — independently unit-testable) ────────────


def _annotated_empty(title: str, height: int = 360) -> go.Figure:
    """An annotated-empty figure so callers can render unconditionally even when
    a model produced nothing plottable."""
    fig = go.Figure()
    fig.add_annotation(
        text="No data", xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False,
        font={"color": C_TEXT3, "size": 12},
    )
    apply_dark_layout(fig, title=title, height=height)
    return fig


def _build_scenario_figure(scenarios: dict) -> go.Figure:
    """Bar of per-share value by scenario — worst (red) / base (blue) / best
    (green), ordered worst→base→best so the eye reads the spread left-to-right.

    ``scenarios`` is the ``{name: ValuationResult}`` map from
    ``scenario_valuation``. Pure builder — an empty map returns an
    annotated-empty figure.
    """
    if not scenarios:
        return _annotated_empty("Per-share value by scenario", height=360)

    # Canonical order first, then any extra scenario names alphabetically.
    order = [n for n in ("worst", "base", "best") if n in scenarios]
    order += sorted(n for n in scenarios if n not in order)

    names = [n.capitalize() for n in order]
    values = [float(scenarios[n].per_share_value) for n in order]
    colors = [_SCENARIO_COLOR.get(n, C_TEXT2) for n in order]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=names,
        y=values,
        marker={"color": colors, "line": {"color": C_BG, "width": 1.0}},
        text=[f"{v:,.2f}" for v in values],
        textposition="outside",
        cliponaxis=False,
        hovertemplate="<b>%{x}</b><br>Per-share: %{y:,.2f}<extra></extra>",
        showlegend=False,
    ))
    apply_dark_layout(
        fig,
        title="Illustrative per-share value by scenario",
        height=360,
        yaxis={"title": "per-share (assumed)"},
    )
    return fig


def _build_montecarlo_figure(mc: dict) -> go.Figure:
    """Percentile fan of the Monte-Carlo per-share distribution.

    ``monte_carlo_valuation`` returns a *summary*, not raw draws — a
    ``percentiles`` map ``{p5, p25, p50, p75, p95}`` plus mean/median/std. So we
    draw the distribution as a percentile fan: a bar at each percentile (the
    median highlighted in the accent colour, the tails muted), which is the
    honest way to show a summarised distribution without inventing draws.

    Pure builder — a missing / empty percentile map returns annotated-empty.
    """
    pct = (mc or {}).get("percentiles") or {}
    order = ["p5", "p25", "p50", "p75", "p95"]
    labels = {"p5": "P5", "p25": "P25", "p50": "Median", "p75": "P75", "p95": "P95"}
    have = [k for k in order if k in pct]
    if not have:
        return _annotated_empty("Monte-Carlo per-share percentiles", height=380)

    xs = [labels[k] for k in have]
    ys = [float(pct[k]) for k in have]
    # Median pops in the accent; the surrounding percentiles sit in muted teal.
    colors = [C_ACCENT if k == "p50" else C_MACRO for k in have]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=xs,
        y=ys,
        marker={"color": colors, "line": {"color": C_BG, "width": 1.0}},
        text=[f"{v:,.2f}" for v in ys],
        textposition="outside",
        cliponaxis=False,
        hovertemplate="<b>%{x}</b><br>Per-share: %{y:,.2f}<extra></extra>",
        showlegend=False,
    ))
    # A thin line across the percentile points emphasises the fan shape.
    fig.add_trace(go.Scatter(
        x=xs,
        y=ys,
        mode="lines",
        line={"color": "rgba(74,144,164,0.5)", "width": 1.4},
        hoverinfo="skip",
        showlegend=False,
    ))
    apply_dark_layout(
        fig,
        title="Monte-Carlo per-share percentiles (P5–P95)",
        height=380,
        yaxis={"title": "per-share (assumed)"},
    )
    return fig


def _build_tornado_figure(rows: list) -> go.Figure:
    """Horizontal tornado of per-share swing by input.

    ``sensitivity_analysis`` returns rows sorted by ``swing`` descending. A
    horizontal bar chart reads as a tornado when the *largest* swing sits at the
    TOP, so we reverse the order for the y-axis (Plotly draws the first category
    at the bottom). Each bar spans from the input's low-end per-share value to
    its high-end value, centred on nothing in particular — the WIDTH is the
    signal. Pure builder — empty rows return annotated-empty.
    """
    if not rows:
        return _annotated_empty("Per-share sensitivity (tornado)", height=380)

    # Reverse so the biggest swing is the top bar.
    ordered = list(reversed(rows))
    labels = [_INPUT_LABEL.get(r["input"], r["input"]) for r in ordered]
    lows = [float(r["low_value"]) for r in ordered]
    highs = [float(r["high_value"]) for r in ordered]
    bases = [min(lo, hi) for lo, hi in zip(lows, highs)]
    widths = [abs(hi - lo) for lo, hi in zip(lows, highs)]

    fig = go.Figure()
    # Transparent offset bar pushes each visible bar to start at its low end, so
    # the coloured segment spans low→high (a floating horizontal bar).
    fig.add_trace(go.Bar(
        y=labels,
        x=bases,
        orientation="h",
        marker={"color": "rgba(0,0,0,0)"},
        hoverinfo="skip",
        showlegend=False,
    ))
    fig.add_trace(go.Bar(
        y=labels,
        x=widths,
        orientation="h",
        marker={"color": C_MOD, "line": {"color": C_BG, "width": 1.0}},
        customdata=[[lo, hi] for lo, hi in zip(lows, highs)],
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Low: %{customdata[0]:,.2f}<br>"
            "High: %{customdata[1]:,.2f}<br>"
            "Swing: %{x:,.2f}<extra></extra>"
        ),
        showlegend=False,
    ))
    apply_dark_layout(
        fig,
        title="Per-share sensitivity — swing by assumed input",
        height=max(300, 70 + 46 * len(labels)),
        barmode="stack",
        xaxis={"title": "per-share (assumed)"},
    )
    return fig


# ── Render layer (Streamlit) ───────────────────────────────────────────────


def _provenance_caption(provenance: dict) -> str:
    """One-line caption naming which fundamentals are ASSUMED (vs flagged real).

    Per the audit every input is assumed here, so this almost always lists them
    all — which is exactly the honesty we want surfaced under the headline.
    """
    assumed = sorted(k for k, v in (provenance or {}).items() if v != "real")
    if not assumed:
        return "All inputs flagged real."
    pretty = ", ".join(_INPUT_LABEL.get(k, k) for k in assumed)
    return f"Assumed inputs (not measured): {pretty}."


def _render_dcf(result, ticker: str) -> None:
    """DCF section: headline per-share + EV/equity metric cards, PV decomposition
    and the assumed-input echo."""
    metric_card_row([
        {"label": "Per-share (illustrative)",
         "value": f"${result.per_share_value:,.2f}",
         "accent": C_ACCENT,
         "sublabel": f"{ticker} · {result.horizon}y horizon · ASSUMED inputs"},
        {"label": "Equity value",
         "value": f"${result.equity_value:,.0f}",
         "accent": C_HIGH,
         "sublabel": "EV − net debt (assumed)"},
        {"label": "Enterprise value",
         "value": f"${result.enterprise_value:,.0f}",
         "accent": C_MACRO,
         "sublabel": "PV(explicit) + PV(terminal)"},
        {"label": "PV split — terminal",
         "value": f"${result.pv_terminal_value:,.0f}",
         "accent": C_CONV,
         "sublabel": f"explicit PV ${result.pv_explicit_fcf:,.0f}"},
    ], columns=4)

    st.caption(_provenance_caption(result.input_provenance))
    if result.terminal_growth_clamped:
        st.info(
            "Terminal growth was at/above the discount rate, so it was clamped "
            "to keep the Gordon terminal value finite and positive."
        )


def _render_scenarios(scenarios: dict) -> None:
    """Scenario section: per-share bar + a worst/base/best table."""
    st.plotly_chart(
        _build_scenario_figure(scenarios),
        use_container_width=True,
        config={"displayModeBar": False},
        key="val_scenario_chart",
    )

    order = [n for n in ("worst", "base", "best") if n in scenarios]
    order += sorted(n for n in scenarios if n not in order)
    rows: list[list[str]] = []
    for name in order:
        res = scenarios[name]
        accent = _SCENARIO_COLOR.get(name, C_TEXT2)
        clamp = badge("clamped r≤g", color=C_MOD) if res.terminal_growth_clamped else ""
        rows.append([
            badge(name.capitalize(), color=accent),
            f"${res.per_share_value:,.2f}",
            f"${res.equity_value:,.0f}",
            f"${res.enterprise_value:,.0f}",
            clamp,
        ])
    wsj_market_table(
        ["Scenario", "Per-share", "Equity value", "Enterprise value", "Note"],
        rows,
        title="Worst / base / best — all per-share figures are illustrative",
    )


def _render_montecarlo(mc: dict) -> None:
    """Monte-Carlo section: summary metric cards + the percentile fan."""
    n_valid = int(mc.get("n_valid", 0))
    skipped = int(mc.get("skipped", 0))
    metric_card_row([
        {"label": "Mean per-share",
         "value": f"${float(mc.get('mean', 0.0)):,.2f}",
         "accent": C_ACCENT,
         "sublabel": "across valid draws (assumed)"},
        {"label": "Median (P50)",
         "value": f"${float(mc.get('median', 0.0)):,.2f}",
         "accent": C_HIGH,
         "sublabel": f"P5 ${float(mc.get('p5', 0.0)):,.2f} · P95 ${float(mc.get('p95', 0.0)):,.2f}"},
        {"label": "Std. deviation",
         "value": f"${float(mc.get('std', 0.0)):,.2f}",
         "accent": C_MACRO,
         "sublabel": "dispersion of per-share"},
        {"label": "Valid draws",
         "value": f"{n_valid:,}",
         "accent": C_CONV,
         "sublabel": f"{skipped:,} skipped (non-economic r≤g)"},
    ], columns=4)

    st.plotly_chart(
        _build_montecarlo_figure(mc),
        use_container_width=True,
        config={"displayModeBar": False},
        key="val_montecarlo_chart",
    )
    st.caption(
        "Deterministic given a fixed seed. The module returns a percentile "
        "summary (not raw draws), so the distribution is shown as a P5–P95 "
        "percentile fan. " + str(mc.get("disclaimer", ""))
    )


def _render_sensitivity(rows: list) -> None:
    """Sensitivity section: tornado chart + the per-input swing table."""
    st.plotly_chart(
        _build_tornado_figure(rows),
        use_container_width=True,
        config={"displayModeBar": False},
        key="val_tornado_chart",
    )
    if not rows:
        st.info("No sensitivity ranges produced any swing.")
        return
    table_rows: list[list[str]] = []
    for r in rows:
        table_rows.append([
            badge(_INPUT_LABEL.get(r["input"], r["input"]), color=C_ACCENT),
            f"${float(r['low_value']):,.2f}",
            f"${float(r['high_value']):,.2f}",
            badge(f"${float(r['swing']):,.2f}", color=C_MOD),
        ])
    wsj_market_table(
        ["Input", "Low per-share", "High per-share", "Swing"],
        table_rows,
        title="One-at-a-time sensitivity, sorted by swing (illustrative)",
    )


def render(**_kwargs) -> None:
    """Render the illustrative valuation tab."""
    from engine.perf_telemetry import track_render

    with track_render("valuation"):
        page_header(
            title="Illustrative Valuation",
            subtitle=(
                "DCF, scenario, Monte-Carlo and sensitivity models for a "
                "shipping name — driven entirely by ASSUMED inputs you set "
                "below. Mechanics, not measured fundamentals."
            ),
            badge_text="ILLUSTRATIVE",
            badge_color=C_MOD,
        )

        # ── Import the pure module + constants ─────────────────────────────
        try:
            from processing.valuation import (
                DISCLAIMER,
                build_disruption_scenarios,
                dcf_valuation,
                illustrative_inputs,
                monte_carlo_valuation,
                scenario_valuation,
                sensitivity_analysis,
            )
        except Exception:
            logger.exception("valuation: import failed")
            st.error("Valuation module unavailable.")
            return

        # ── Mandatory, prominent disclaimer near the top ───────────────────
        st.warning(DISCLAIMER)

        # ── Section 1: assumed inputs ──────────────────────────────────────
        section_divider("Assumed inputs")
        try:
            ticker = st.selectbox(
                "Shipping name (label only — seeds assumed defaults, loads no "
                "real fundamentals)",
                options=list(_TICKERS),
                index=0,
                key="val_ticker",
            )
            # Seed the slider defaults from the module's illustrative defaults so
            # the "assumed" nature is centralised in one place.
            seed = illustrative_inputs()
            seed_growth = (
                float(seed.fcf_growth)
                if isinstance(seed.fcf_growth, (int, float))
                else 0.03
            )

            c1, c2 = st.columns(2)
            with c1:
                fcf_0 = st.slider(
                    "Annual free cash flow (FCF₀, USD millions) — assumed",
                    min_value=50.0, max_value=2000.0,
                    value=float(seed.fcf_0), step=10.0,
                    key="val_fcf0",
                )
                fcf_growth = st.slider(
                    "FCF growth rate (per year) — assumed",
                    min_value=-0.10, max_value=0.20,
                    value=seed_growth, step=0.005,
                    key="val_growth",
                )
            with c2:
                discount_rate = st.slider(
                    "Discount rate / WACC — assumed",
                    min_value=0.04, max_value=0.20,
                    value=float(seed.discount_rate), step=0.005,
                    key="val_discount",
                )
                terminal_growth = st.slider(
                    "Terminal (perpetual) growth — assumed",
                    min_value=0.00, max_value=0.05,
                    value=float(seed.terminal_growth), step=0.005,
                    key="val_terminal",
                )

            inputs = illustrative_inputs(
                fcf_0=fcf_0,
                fcf_growth=fcf_growth,
                discount_rate=discount_rate,
                terminal_growth=terminal_growth,
            )
        except Exception:
            logger.exception("valuation: input controls failed")
            st.error("Valuation inputs unavailable.")
            return

        # ── Section 2: DCF ─────────────────────────────────────────────────
        section_divider("Discounted cash flow")
        try:
            dcf_result = dcf_valuation(inputs)
            _render_dcf(dcf_result, ticker)
        except Exception:
            logger.exception("valuation: DCF section failed")
            st.error("DCF valuation unavailable.")

        # ── Section 3: scenarios (worst case driven by a severity slider) ──
        section_divider("Scenarios")
        try:
            severity = st.slider(
                "Disruption severity (0 = calm, 1 = severe) — drives the worst "
                "case via the modeled disruption layer",
                min_value=0.0, max_value=1.0, value=0.4, step=0.05,
                key="val_severity",
            )
            scen_overrides = build_disruption_scenarios(inputs, severity)
            scenarios = scenario_valuation(inputs, scenarios=scen_overrides)
            _render_scenarios(scenarios)
            st.caption(
                "Worst case haircuts near-term growth and adds a discount-rate "
                "risk premium in proportion to severity; best case is a "
                "symmetric upside. worst ≤ base ≤ best by construction."
            )
        except Exception:
            logger.exception("valuation: scenario section failed")
            st.error("Scenario valuation unavailable.")

        # ── Section 4: Monte-Carlo (seed fixed for determinism) ────────────
        section_divider("Monte-Carlo")
        try:
            # Distributions on the two most consequential inputs. Spreads are
            # illustrative knobs, deliberately kept inside economic ranges so the
            # skip-rate stays modest. Seed fixed → reproducible summary.
            distributions = {
                "fcf_growth": ("normal", (float(fcf_growth), 0.03)),
                "discount_rate": ("triangular", (
                    max(0.02, float(discount_rate) - 0.02),
                    float(discount_rate),
                    float(discount_rate) + 0.03,
                )),
            }
            mc = monte_carlo_valuation(
                inputs, distributions=distributions, n=10000, seed=0,
            )
            _render_montecarlo(mc)
        except Exception:
            logger.exception("valuation: monte-carlo section failed")
            st.error("Monte-Carlo valuation unavailable.")

        # ── Section 5: sensitivity (tornado) ───────────────────────────────
        section_divider("Sensitivity")
        try:
            ranges = {
                "fcf_0": (float(fcf_0) * 0.7, float(fcf_0) * 1.3),
                "fcf_growth": (
                    float(fcf_growth) - 0.04, float(fcf_growth) + 0.04,
                ),
                "discount_rate": (
                    max(0.02, float(discount_rate) - 0.02),
                    float(discount_rate) + 0.02,
                ),
                "terminal_growth": (
                    max(0.0, float(terminal_growth) - 0.01),
                    float(terminal_growth) + 0.01,
                ),
            }
            tornado = sensitivity_analysis(inputs, ranges=ranges)
            _render_sensitivity(tornado)
        except Exception:
            logger.exception("valuation: sensitivity section failed")
            st.error("Sensitivity analysis unavailable.")

        # ── Source footer ──────────────────────────────────────────────────
        try:
            st.markdown(
                source_footer([VALUATION_SOURCE]),
                unsafe_allow_html=True,
            )
        except Exception:
            logger.exception("valuation: source footer failed")
