"""tab_macro_projection.py — Macro Projection (Disruption Alpha · stage 3).

Stage 3 of the Disruption Alpha chain projects fleet-wide *disruption* onto a
*macro* read. Two composite indices sit side by side:

* the **Shipping Stress Index (SSI)** — disruption-first, route-resolved
  (``processing.shipping_stress_index.compute_shipping_stress``): *what is
  breaking right now and which lanes are driving it*;
* the **Supply Chain Health Index (SCHI)** — health-first, six-dimensional
  (``engine.supply_chain_health.compute_supply_chain_health``): *the resulting
  state of the system*.

SSI and SCHI are deliberately distinct composites with distinct weights. This
tab never re-derives one from the other — it shows both and explains the
relationship: the SSI's dominant stress drivers map onto the SCHI dimensions
they are pushing, and the closest predefined ``scenario_analyzer`` scenario is
surfaced as a modeled projection of where the stress could carry rates and
macro conditions.

Canonical tab pattern (mirrors ``ui/tab_rate_analytics.py`` /
``ui/tab_voyage_tracker.py``):
  * palette + components imported from ``ui/styles.py`` — never redeclared;
  * ``render(...)`` ends with ``**kwargs`` for argument safety;
  * every section wrapped in try/except + ``logger.exception``;
  * ``source_footer`` at the bottom.

All inputs are modeled/synthetic and labeled via ``data/quality.py`` +
``source_footer``.

Sections
--------
A. Page header (badge "MODELED")
B. Side-by-side gauges — SSI (disruption lens) vs SCHI (health lens)
C. Stress -> health narrative — which SCHI dimensions the SSI is pushing
D. Scenario lens — closest predefined scenario_analyzer scenario as a projection
E. Leading-indicator context strip
F. Source footer
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
    section_header,
    source_footer,
    wsj_market_table,
)

# ── Provenance — this tab synthesises modeled composites ────────────────────
_MACRO_PROJECTION_SOURCE = DataSource.modeled(
    "Macro Projection Model",
    notes="SSI vs SCHI composites + closest-scenario projection — modeled",
)

# ── Domain-specific colour mappings ─────────────────────────────────────────
# Semantic label -> palette colour. Palette constants live in ui/styles.py and
# are imported above, never redeclared here.

# SCHI dimension key -> human-readable display name (mirrors the SCHI module).
_SCHI_DIM_DISPLAY: dict[str, str] = {
    "port_capacity":         "Port Capacity",
    "freight_cost_pressure": "Freight Cost Pressure",
    "macro_environment":     "Macro Environment",
    "chokepoint_risk":       "Chokepoint Risk",
    "inventory_cycle":       "Inventory Cycle",
    "seasonal_factors":      "Seasonal Factors",
}

# SSI dominant-driver label -> the SCHI dimension key it most directly pushes.
# Drives the "what's breaking -> resulting health" narrative in section C.
_DRIVER_TO_SCHI_DIM: dict[str, str] = {
    "Chokepoint disruption":       "chokepoint_risk",
    "Port congestion":             "port_capacity",
    "Weather risk":                "port_capacity",
    "Freight-rate dislocation":    "freight_cost_pressure",
    "Structural vulnerability":    "chokepoint_risk",
}

# SSI component key -> human-readable display name (matches the keys in
# processing.shipping_stress_index.COMPONENT_WEIGHTS exactly).
_SSI_COMPONENT_DISPLAY: dict[str, str] = {
    "chokepoint":    "Chokepoint",
    "congestion":    "Port Congestion",
    "weather":       "Weather Risk",
    "rate":          "Freight-Rate Dislocation",
    "vulnerability": "Structural Vulnerability",
    "anomaly":       "Anomaly Drift",
}


# Leading-indicator forecast verdict -> palette colour.
_FORECAST_COLOR: dict[str, str] = {
    "EXPANSION":   C_HIGH,
    "STABLE":      C_TEXT2,
    "CONTRACTION": C_LOW,
}

# Leading-indicator per-signal colour.
_SIGNAL_COLOR: dict[str, str] = {
    "BULLISH": C_HIGH,
    "NEUTRAL": C_TEXT2,
    "BEARISH": C_LOW,
}


def _ssi_band_color(ssi: float) -> str:
    """Disruption-lens colour for an SSI score in [0, 1] (higher = worse)."""
    if ssi >= 0.65:
        return C_LOW
    if ssi >= 0.45:
        return C_MOD
    if ssi >= 0.25:
        return C_MACRO
    return C_HIGH


def _schi_band_color(schi: float) -> str:
    """Health-lens colour for an SCHI score in [0, 1] (higher = healthier)."""
    if schi >= 0.70:
        return C_HIGH
    if schi >= 0.50:
        return C_ACCENT
    if schi >= 0.35:
        return C_MOD
    return C_LOW


def _delta_color(delta: float, *, higher_is_good: bool = True) -> str:
    """Colour a signed delta; flip semantics with ``higher_is_good``."""
    if abs(delta) < 1e-6:
        return C_TEXT2
    positive = delta > 0
    good = positive if higher_is_good else not positive
    return C_HIGH if good else C_LOW


# ── Cell formatters for the WSJ market table ────────────────────────────────
# wsj_market_table renders each cell string as raw HTML inside a <td>.

def _mono(value: str, color: str = C_TEXT) -> str:
    """Monospace numeric cell content."""
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    """Sans-serif cell content."""
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


# ── Presentation helpers — small, on-theme building blocks ──────────────────
# These keep the section bodies declarative and visually consistent. Each one
# leans on the design-system CSS variables (var(--card), var(--rule), the
# palette constants imported from ui.styles) rather than hand-tuned colours,
# and exists only because styles.py has no helper for that exact composite.

def _narrative(html_body: str, *, accent: str = C_ACCENT) -> None:
    """Render a framed narrative paragraph block.

    The left accent rule colours to the section's dominant reading so the
    block reads as part of the data, not a generic callout.
    """
    st.markdown(
        f'<div style="background:var(--card);border:1px solid var(--border);'
        f'border-left:3px solid {accent};border-radius:var(--radius);'
        f'padding:14px 18px;margin:8px 0 16px;font-family:var(--sans);'
        f'font-size:0.86rem;line-height:1.64;color:{C_TEXT2};">'
        f'{html_body}</div>',
        unsafe_allow_html=True,
    )


def _eyebrow(label: str) -> str:
    """Return an uppercase tracked eyebrow label (HTML span)."""
    return (
        f'<span style="font-family:var(--sans);font-size:0.66rem;'
        f'font-weight:700;text-transform:uppercase;letter-spacing:0.12em;'
        f'color:{C_TEXT3};">{label}</span>'
    )


def _empty_state(message: str, *, accent: str = C_TEXT3) -> None:
    """Render a framed, on-theme empty / unavailable state.

    Replaces bare ``st.info`` / ``st.warning`` so degraded sections still read
    as part of the terminal rather than as raw Streamlit chrome.
    """
    st.markdown(
        f'<div style="background:var(--surface);'
        f'border:1px dashed var(--rule);border-left:3px solid {accent};'
        f'border-radius:var(--radius);padding:14px 18px;margin:6px 0 14px;'
        f'font-family:var(--sans);font-size:0.82rem;line-height:1.6;'
        f'color:{C_TEXT3};">{message}</div>',
        unsafe_allow_html=True,
    )


def _chain_stage_strip() -> None:
    """Render a compact 'Disruption Alpha — stage 3 of 3' progress strip.

    Orients the reader: this tab is the macro projection that sits downstream
    of the per-route disruption read. Static, decorative, palette-only.
    """
    stages = (
        ("01", "Route disruption", False),
        ("02", "Fleet stress", False),
        ("03", "Macro projection", True),
    )
    chips = []
    for num, name, active in stages:
        if active:
            chips.append(
                f'<span style="display:inline-flex;align-items:baseline;gap:6px;'
                f'padding:3px 11px;border-radius:3px;'
                f'background:{_rgba(C_CONV, 0.10)};'
                f'border:1px solid {_rgba(C_CONV, 0.30)};">'
                f'<span style="font-family:var(--mono);font-size:0.66rem;'
                f'font-weight:700;color:{C_CONV};">{num}</span>'
                f'<span style="font-family:var(--sans);font-size:0.72rem;'
                f'font-weight:700;color:{C_TEXT};letter-spacing:0.02em;">'
                f'{name}</span></span>'
            )
        else:
            chips.append(
                f'<span style="display:inline-flex;align-items:baseline;gap:6px;'
                f'padding:3px 11px;">'
                f'<span style="font-family:var(--mono);font-size:0.66rem;'
                f'font-weight:600;color:{C_TEXT3};">{num}</span>'
                f'<span style="font-family:var(--sans);font-size:0.72rem;'
                f'font-weight:500;color:{C_TEXT3};">{name}</span></span>'
            )
    arrow = (
        f'<span style="color:{C_TEXT3};font-family:var(--mono);'
        f'font-size:0.74rem;opacity:0.5;">&rarr;</span>'
    )
    body = arrow.join(chips)
    st.markdown(
        f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;'
        f'margin:-8px 0 22px;padding:8px 0;'
        f'border-bottom:1px solid var(--rule);">'
        f'<span style="font-family:var(--sans);font-size:0.64rem;'
        f'font-weight:700;text-transform:uppercase;letter-spacing:0.12em;'
        f'color:{C_TEXT3};margin-right:10px;">Disruption Alpha</span>'
        f'{body}</div>',
        unsafe_allow_html=True,
    )


def _rgba(hex_color: str, alpha: float) -> str:
    """Local hex -> rgba (mirrors styles._hex_to_rgba; kept private here)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ── Cached composite computation ────────────────────────────────────────────
# The SSI / SCHI / scenario inputs (freight_data, macro_data, port_results,
# route_results) are unhashable dicts/lists, so they cannot be passed straight
# to @st.cache_data. The cached wrapper below takes a single hashable token
# (the current date) and rebuilds the modeled fleet itself — fleet build is
# date-seeded and deterministic, so the cache key is honest. The non-cached
# inputs are read from a private module-level handoff set by render().

_COMPUTE_INPUTS: dict[str, object] = {}


@st.cache_data(ttl=900, show_spinner=False)
def _cached_ssi_report(_date_token: str):
    """Build (and cache) the Shipping Stress Index report.

    ``_date_token`` is the only cache key — a plain date string. The actual
    (unhashable) inputs are handed off via ``_COMPUTE_INPUTS`` so this stays a
    hashable-arg wrapper and never trips Streamlit's unhashable-dict error.
    The modeled voyage fleet is rebuilt here (date-seeded, deterministic).
    """
    from processing.shipping_stress_index import compute_shipping_stress

    freight_data = _COMPUTE_INPUTS.get("freight_data") or {}
    macro_data = _COMPUTE_INPUTS.get("macro_data") or {}
    port_results = _COMPUTE_INPUTS.get("port_results") or []
    route_results = _COMPUTE_INPUTS.get("route_results") or []

    fleet: list = []
    try:
        from data.voyage_dataset import build_voyage_fleet
        fleet = build_voyage_fleet()
    except Exception:  # pragma: no cover - defensive
        logger.exception("Macro Projection — voyage fleet build failed")
        fleet = []

    return compute_shipping_stress(
        freight_data, macro_data, port_results, route_results,
        voyage_fleet=fleet or None,
    )


@st.cache_data(ttl=900, show_spinner=False)
def _cached_schi_report(_date_token: str):
    """Build (and cache) the Supply Chain Health Index report.

    Mirrors :func:`_cached_ssi_report` — single hashable date-string key, real
    inputs read from the ``_COMPUTE_INPUTS`` handoff.
    """
    from engine.supply_chain_health import compute_supply_chain_health

    freight_data = _COMPUTE_INPUTS.get("freight_data") or {}
    macro_data = _COMPUTE_INPUTS.get("macro_data") or {}
    port_results = _COMPUTE_INPUTS.get("port_results") or []
    route_results = _COMPUTE_INPUTS.get("route_results") or []

    return compute_supply_chain_health(
        port_results, freight_data, macro_data, route_results,
    )


def _date_token() -> str:
    """A stable per-day cache token (mirrors the date-seeded fleet)."""
    from datetime import date
    return date.today().isoformat()


# ── Section B: side-by-side SSI vs SCHI gauges ──────────────────────────────

def _gauge_caption(
    *, eyebrow: str, title: str, lens: str, accent: str
) -> str:
    """Return the framed header for a gauge card (eyebrow + title + lens)."""
    return (
        f'<div style="text-align:center;padding-bottom:4px;">'
        f'<div style="margin-bottom:3px;">{_eyebrow(eyebrow)}</div>'
        f'<div style="font-family:var(--serif);font-size:1.04rem;'
        f'font-weight:700;color:{C_TEXT};letter-spacing:-0.01em;">'
        f'{title}</div>'
        f'<div style="display:inline-flex;align-items:center;gap:6px;'
        f'margin-top:5px;">'
        f'<span style="width:14px;height:2px;background:{accent};'
        f'display:inline-block;"></span>'
        f'<span style="font-family:var(--sans);font-size:0.72rem;'
        f'font-style:italic;color:{C_TEXT2};">{lens}</span>'
        f'<span style="width:14px;height:2px;background:{accent};'
        f'display:inline-block;"></span>'
        f'</div></div>'
    )


def _gauge_readout(*, label: str, accent: str, foot: str) -> str:
    """Return the framed footer for a gauge card (badge + descriptor)."""
    return (
        f'<div style="text-align:center;margin-top:-4px;padding-top:6px;'
        f'border-top:1px solid var(--rule);">'
        f'{badge(label, color=accent)}'
        f'<div style="font-family:var(--sans);font-size:0.73rem;'
        f'color:{C_TEXT3};margin-top:6px;letter-spacing:0.01em;">'
        f'{foot}</div></div>'
    )


def _gauge_bridge(ssi: float, schi: float) -> None:
    """Render the central connector spelling out the SSI -> SCHI relationship.

    The bridge sits between the two gauges and turns a loose side-by-side into
    an intentional 'stress in, health out' read. The verb scales with how far
    the two composites diverge, so the visual hierarchy tracks the data.
    """
    # Tension = how far disruption and (inverted) health disagree.
    tension = abs(ssi - (1.0 - schi))
    if tension >= 0.30:
        verb, vcolor = "diverging", C_LOW
    elif tension >= 0.15:
        verb, vcolor = "drifting apart", C_MOD
    else:
        verb, vcolor = "tracking together", C_HIGH
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:14px;'
        f'margin:14px 0 6px;padding:9px 16px;background:var(--surface);'
        f'border:1px solid var(--rule);border-radius:var(--radius);">'
        f'<span style="font-family:var(--sans);font-size:0.66rem;'
        f'font-weight:700;text-transform:uppercase;letter-spacing:0.1em;'
        f'color:{C_TEXT3};">Disruption</span>'
        f'<span style="font-family:var(--mono);font-size:0.84rem;'
        f'font-weight:700;color:{_ssi_band_color(ssi)};'
        f'font-variant-numeric:tabular-nums;">{ssi:.0%}</span>'
        f'<span style="flex:1;height:1px;background:'
        f'linear-gradient(90deg,{_rgba(_ssi_band_color(ssi), 0.5)},'
        f'{_rgba(_schi_band_color(schi), 0.5)});"></span>'
        f'<span style="font-family:var(--sans);font-size:0.7rem;'
        f'font-style:italic;color:{vcolor};white-space:nowrap;">'
        f'{verb}</span>'
        f'<span style="flex:1;height:1px;background:'
        f'linear-gradient(90deg,{_rgba(_ssi_band_color(ssi), 0.5)},'
        f'{_rgba(_schi_band_color(schi), 0.5)});"></span>'
        f'<span style="font-family:var(--mono);font-size:0.84rem;'
        f'font-weight:700;color:{_schi_band_color(schi)};'
        f'font-variant-numeric:tabular-nums;">{schi:.0%}</span>'
        f'<span style="font-family:var(--sans);font-size:0.66rem;'
        f'font-weight:700;text-transform:uppercase;letter-spacing:0.1em;'
        f'color:{C_TEXT3};">Health</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_gauges(ssi_report, schi_report) -> None:
    """Render the two composite gauges side by side, each with its own lens."""
    from ui.styles import gauge_ring

    ssi = float(getattr(ssi_report, "overall_ssi", 0.0))
    ssi_label = getattr(ssi_report, "ssi_label", "—")
    ssi_color = _ssi_band_color(ssi)
    wow = float(getattr(ssi_report, "wow_change", 0.0))

    schi = float(getattr(schi_report, "overall_score", 0.0))
    schi_label = getattr(schi_report, "overall_label", "—")
    schi_color = _schi_band_color(schi)

    col_ssi, col_schi = st.columns(2, gap="large")

    # ─ Shipping Stress Index — disruption lens (higher = more stressed) ─
    with col_ssi:
        st.markdown(
            _gauge_caption(
                eyebrow="Disruption Lens",
                title="Shipping Stress Index",
                lens="what is breaking right now",
                accent=ssi_color,
            ),
            unsafe_allow_html=True,
        )
        fig = gauge_ring(ssi, "SSI", color=ssi_color, size=196)
        st.plotly_chart(
            fig,
            use_container_width=True,
            config={"displayModeBar": False},
            key="macro_projection_gauge_ssi",
        )
        wow_word = "easing" if wow < 0 else ("building" if wow > 0 else "flat")
        st.markdown(
            _gauge_readout(
                label=ssi_label,
                accent=ssi_color,
                foot=f"week-over-week {wow:+.0%} &middot; {wow_word}",
            ),
            unsafe_allow_html=True,
        )

    # ─ Supply Chain Health Index — health lens (higher = healthier) ─
    with col_schi:
        st.markdown(
            _gauge_caption(
                eyebrow="Health Lens",
                title="Supply Chain Health Index",
                lens="the resulting state of the system",
                accent=schi_color,
            ),
            unsafe_allow_html=True,
        )
        fig = gauge_ring(schi, "SCHI", color=schi_color, size=196)
        st.plotly_chart(
            fig,
            use_container_width=True,
            config={"displayModeBar": False},
            key="macro_projection_gauge_schi",
        )
        st.markdown(
            _gauge_readout(
                label=schi_label,
                accent=schi_color,
                foot="six-dimensional health composite",
            ),
            unsafe_allow_html=True,
        )

    # ─ Central bridge — turns two gauges into one stress->health read ─
    _gauge_bridge(ssi, schi)


# ── Section C: stress -> health narrative ───────────────────────────────────

def _build_ssi_component_bars(
    component_scores: dict,
    weights: dict | None = None,
) -> go.Figure:
    """Horizontal bars of the SSI's per-component stress scores.

    Sorted hot-first so the reader sees the dominant disruption driver at
    the top. Each bar is coloured by ``_ssi_band_color`` (the same banding
    the headline gauge uses) and is annotated with the component's weight
    in the overall SSI, so the reader sees both *severity* and *influence*
    without having to cross-reference the weights table.

    Pure builder — no ``st.*`` calls — so the lock-in tests exercise it
    directly. Missing / empty input returns an annotated-empty figure.
    """
    fig = go.Figure()

    items = [
        (key, float(score))
        for key, score in (component_scores or {}).items()
        if score is not None
    ]
    if not items:
        fig.add_annotation(
            text="No SSI component scores",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="SSI Component Decomposition", height=240)
        return fig

    # Hot-first; Plotly stacks categorical y-values bottom-up so we sort
    # *ascending* and let Plotly invert. The highest stress ends up at the top.
    items.sort(key=lambda kv: kv[1])

    weights = weights or {}
    labels = [_SSI_COMPONENT_DISPLAY.get(k, k.title()) for k, _ in items]
    scores = [v * 100.0 for _, v in items]
    colors = [_ssi_band_color(v) for _, v in items]
    # Per-bar weight annotation reads as "weight 29%" → "w 29%" for tightness.
    weight_text = [
        f"w {weights.get(k, 0.0) * 100:.0f}%" if k in weights else ""
        for k, _ in items
    ]

    fig.add_trace(go.Bar(
        x=scores,
        y=labels,
        orientation="h",
        marker={"color": colors,
                "line": {"color": C_BG, "width": 1}},
        text=[f"{s:.0f}%" for s in scores],
        textposition="outside",
        textfont={"color": C_TEXT2, "size": 11},
        customdata=weight_text,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Stress: %{x:.1f}%<br>"
            "Weight in SSI: %{customdata}<extra></extra>"
        ),
        showlegend=False,
    ))

    # Band reference lines mirroring _ssi_band_color thresholds: 25 / 45 / 65
    for x, label in ((25, "Elevated"), (45, "Pressured"), (65, "Critical")):
        fig.add_vline(
            x=x,
            line={"color": "rgba(255,255,255,0.10)", "width": 1, "dash": "dot"},
            annotation_text=label,
            annotation_position="top",
            annotation_font={"color": C_TEXT3, "size": 9},
        )

    apply_dark_layout(
        fig,
        title="SSI Component Decomposition — what's pushing the composite",
        height=max(220, 60 + 32 * len(items)),
    )
    fig.update_layout(
        xaxis={"title": "Component stress (0–100)", "range": [0, 108],
               "gridcolor": "rgba(255,255,255,0.04)"},
        yaxis={"title": None, "automargin": True,
               "tickfont": {"color": C_TEXT2, "size": 11}},
        margin={"l": 8, "r": 60, "t": 50, "b": 40},
        bargap=0.35,
    )
    return fig


def _render_stress_health_narrative(ssi_report, schi_report) -> None:
    """Explain which SCHI dimensions the current SSI stress is pushing.

    The SSI's fleet-wide ``component_scores`` and per-route dominant drivers
    are mapped onto the SCHI dimensions they most directly pressure. The
    narrative is descriptive, not predictive — it states the linkage, then
    names the dimensions and the lanes driving them.
    """
    section_header(
        "Stress -> Health Linkage",
        "SSI = what's breaking · SCHI = the resulting health",
    )

    ssi = float(getattr(ssi_report, "overall_ssi", 0.0))
    schi = float(getattr(schi_report, "overall_score", 0.0))
    ssi_label = getattr(ssi_report, "ssi_label", "—")
    schi_label = getattr(schi_report, "overall_label", "—")
    component_scores: dict = getattr(ssi_report, "component_scores", {}) or {}
    dim_scores: dict = getattr(schi_report, "dimension_scores", {}) or {}
    route_stress: list = getattr(ssi_report, "route_stress", []) or []

    # Which SSI components are running hot (fleet-wide average >= 0.40)?
    hot_components = sorted(
        ((k, v) for k, v in component_scores.items() if v >= 0.40),
        key=lambda kv: kv[1],
        reverse=True,
    )

    # Map the hot components onto the SCHI dimensions they push.
    _COMPONENT_DRIVER_LABEL: dict[str, str] = {
        "chokepoint":    "Chokepoint disruption",
        "congestion":    "Port congestion",
        "weather":       "Weather risk",
        "rate":          "Freight-rate dislocation",
        "vulnerability": "Structural vulnerability",
    }
    pushed_dims: list[str] = []
    for comp_key, _ in hot_components:
        driver_label = _COMPONENT_DRIVER_LABEL.get(comp_key, "")
        dim_key = _DRIVER_TO_SCHI_DIM.get(driver_label)
        if dim_key and dim_key not in pushed_dims:
            pushed_dims.append(dim_key)

    # Headline relationship sentence.
    if ssi >= 0.45 and schi < 0.50:
        relation = (
            f"The system is reading <b style=\"color:{_ssi_band_color(ssi)}\">"
            f"{ssi_label}</b> on disruption and <b style=\"color:"
            f"{_schi_band_color(schi)}\">{schi_label}</b> on health — elevated "
            f"stress is visibly suppressing the health composite."
        )
    elif ssi >= 0.45:
        relation = (
            f"Disruption is running <b style=\"color:{_ssi_band_color(ssi)}\">"
            f"{ssi_label}</b> while health still reads <b style=\"color:"
            f"{_schi_band_color(schi)}\">{schi_label}</b> — the stress has not "
            f"yet fully fed through to the health composite."
        )
    else:
        relation = (
            f"Disruption is <b style=\"color:{_ssi_band_color(ssi)}\">"
            f"{ssi_label}</b> and health reads <b style=\"color:"
            f"{_schi_band_color(schi)}\">{schi_label}</b> — the two composites "
            f"are broadly aligned with no acute dislocation."
        )

    # Name the pushed dimensions and their current SCHI score.
    if pushed_dims:
        dim_phrases = []
        for dim_key in pushed_dims[:3]:
            dim_name = _SCHI_DIM_DISPLAY.get(dim_key, dim_key)
            dim_val = dim_scores.get(dim_key)
            if dim_val is not None:
                dim_phrases.append(
                    f"<b>{dim_name}</b> (SCHI dim at {dim_val:.0%})"
                )
            else:
                dim_phrases.append(f"<b>{dim_name}</b>")
        push_sentence = (
            " The current stress mix is pushing on "
            + ", ".join(dim_phrases)
            + " — those are the SCHI dimensions absorbing the disruption."
        )
    else:
        push_sentence = (
            " No single stress component is hot enough to single out a "
            "pressured SCHI dimension; stress is diffuse across the system."
        )

    # Name the worst lanes (route_stress is pre-sorted worst-first).
    if route_stress:
        worst = route_stress[:3]
        lane_bits = ", ".join(
            f"{getattr(rs, 'route_name', getattr(rs, 'route_id', '—'))} "
            f"({getattr(rs, 'stress_score', 0.0):.0%}, "
            f"{getattr(rs, 'dominant_driver', 'mixed').lower()})"
            for rs in worst
        )
        lane_sentence = f" Stress is concentrated on {lane_bits}."
    else:
        lane_sentence = ""

    _narrative(relation + push_sentence + lane_sentence, accent=_ssi_band_color(ssi))

    # Component decomposition — visualises what the driver→dimension table is
    # about to break down. Imported lazily so the lock-in tests can exercise
    # the pure builder without going through the full COMPONENT_WEIGHTS chain.
    try:
        from processing.shipping_stress_index import COMPONENT_WEIGHTS
        weights_map = COMPONENT_WEIGHTS
    except Exception:
        logger.exception("Macro Projection — COMPONENT_WEIGHTS import failed")
        weights_map = None
    st.plotly_chart(
        _build_ssi_component_bars(component_scores, weights_map),
        use_container_width=True,
        config={"displayModeBar": False},
        key="macro_projection_ssi_components",
    )

    # Compact per-driver -> dimension mapping table.
    if hot_components:
        st.markdown(
            f'<div style="margin:2px 0 6px;">'
            f'{_eyebrow("Stress transmission &middot; driver &rarr; dimension")}'
            f'</div>',
            unsafe_allow_html=True,
        )
        rows = []
        for comp_key, comp_val in hot_components:
            driver_label = _COMPONENT_DRIVER_LABEL.get(comp_key, comp_key)
            dim_key = _DRIVER_TO_SCHI_DIM.get(driver_label, "")
            dim_name = _SCHI_DIM_DISPLAY.get(dim_key, "—")
            dim_val = dim_scores.get(dim_key)
            dim_cell = (
                _mono(f"{dim_val:.0%}", color=_schi_band_color(dim_val))
                if dim_val is not None else _sans("n/a", color=C_TEXT3)
            )
            rows.append([
                _sans(driver_label, color=C_TEXT, weight=700),
                _mono(f"{comp_val:.0%}", color=_ssi_band_color(comp_val)),
                _sans(f"&rarr;  {dim_name}", color=C_TEXT2),
                dim_cell,
            ])
        wsj_market_table(
            headers=[
                "SSI Stress Driver", "Stress (fleet avg)",
                "Pressures SCHI Dimension", "Dimension Health",
            ],
            rows=rows,
        )
    else:
        _empty_state(
            "No SSI stress component is elevated enough to single out a "
            "specific SCHI dimension &mdash; disruption is diffuse across the "
            "system.",
            accent=C_HIGH,
        )


# ── Section D: closest-scenario projection lens ─────────────────────────────

# Map an SSI dominant-driver label to the predefined scenario names whose
# mechanics it most resembles. Used to pick the "closest" scenario.
_DRIVER_TO_SCENARIO_HINT: dict[str, tuple[str, ...]] = {
    "Chokepoint disruption":    ("Suez Canal Closure", "Panama Canal Drought"),
    "Port congestion":          ("Peak Season Surge", "Global Manufacturing Boom"),
    "Weather risk":             ("Panama Canal Drought", "Suez Canal Closure"),
    "Freight-rate dislocation": ("Oil Price Spike (+50%)", "Peak Season Surge"),
    "Structural vulnerability": ("Suez Canal Closure", "Asia Manufacturing Shift"),
}


def _pick_closest_scenario(ssi_report):
    """Pick the predefined scenario closest to the current stress picture.

    Selection logic, fully transparent:
      1. Tally each per-route ``dominant_driver`` across the fleet.
      2. The most common driver suggests a family of scenarios via
         ``_DRIVER_TO_SCENARIO_HINT``.
      3. When overall SSI is low (< 0.30) the disruption picture is benign, so
         a calm-leaning scenario ("Asia Manufacturing Shift") is chosen instead.
      4. The first hinted scenario present in ``PREDEFINED_SCENARIOS`` wins.

    Returns ``(ScenarioInput, reason_text)`` or ``(None, reason_text)`` when
    the scenario catalogue is unavailable.
    """
    try:
        from processing.scenario_analyzer import PREDEFINED_SCENARIOS
    except Exception:  # pragma: no cover - defensive
        logger.exception("Macro Projection — scenario catalogue import failed")
        return None, "Scenario catalogue unavailable."

    by_name = {s.name: s for s in PREDEFINED_SCENARIOS}
    if not by_name:
        return None, "No predefined scenarios are registered."

    ssi = float(getattr(ssi_report, "overall_ssi", 0.0))
    route_stress: list = getattr(ssi_report, "route_stress", []) or []

    # Tally dominant drivers across the fleet.
    driver_counts: dict[str, int] = {}
    for rs in route_stress:
        drv = getattr(rs, "dominant_driver", "")
        if drv:
            driver_counts[drv] = driver_counts.get(drv, 0) + 1

    if ssi < 0.30:
        chosen = by_name.get("Asia Manufacturing Shift") or next(iter(by_name.values()))
        reason = (
            f"Overall SSI is low ({ssi:.0%}); the closest scenario is a mild, "
            f"structural one rather than an acute shock."
        )
        return chosen, reason

    if driver_counts:
        top_driver = max(driver_counts, key=driver_counts.get)
        hints = _DRIVER_TO_SCENARIO_HINT.get(top_driver, ())
        for hint_name in hints:
            if hint_name in by_name:
                reason = (
                    f"The most common per-lane stress driver is "
                    f"<b>{top_driver.lower()}</b> "
                    f"({driver_counts[top_driver]} of {len(route_stress)} "
                    f"lanes); its mechanics most resemble the "
                    f"<b>{hint_name}</b> scenario."
                )
                return by_name[hint_name], reason

    # Fallback — first catalogue entry.
    chosen = next(iter(by_name.values()))
    return chosen, (
        f"No dominant stress driver stood out; defaulting to the "
        f"<b>{chosen.name}</b> scenario as a reference projection."
    )


def _scenario_route_delta(scenario, route_results) -> float | None:
    """Modeled avg route-opportunity delta for *scenario*, if routes exist.

    Runs the scenario through ``scenario_analyzer.run_scenario`` when live
    ``route_results`` are available, returning ``opportunity_delta``. Returns
    ``None`` when no routes are available (the scenario's intrinsic shock
    fields are still shown either way).
    """
    if not route_results:
        return None
    try:
        from processing.scenario_analyzer import run_scenario
        result = run_scenario(scenario, [], list(route_results))
        return float(getattr(result, "opportunity_delta", 0.0))
    except Exception:  # pragma: no cover - defensive
        logger.exception("Macro Projection — run_scenario failed")
        return None


def _render_scenario_lens(ssi_report, route_results) -> None:
    """Render the closest-scenario projection card (section D)."""
    section_header(
        "Scenario Projection Lens",
        "The predefined scenario whose mechanics most resemble current stress",
    )

    scenario, reason = _pick_closest_scenario(ssi_report)
    if scenario is None:
        _empty_state(reason, accent=C_MOD)
        return

    _narrative(
        f"<b>Projection basis</b> &mdash; {reason} The modeled shocks below "
        f"are that scenario's defined inputs; treat them as an illustrative "
        f"path, not a forecast."
    )

    # Scenario headline — framed as an editorial card so the chosen scenario
    # reads as a named, deliberate object rather than a stray heading.
    st.markdown(
        f'<div style="background:var(--card);border:1px solid var(--border);'
        f'border-top:2px solid {C_CONV};border-radius:var(--radius);'
        f'padding:14px 18px;margin:8px 0 14px;">'
        f'<div style="margin-bottom:4px;">{_eyebrow("Closest scenario")}</div>'
        f'<div style="font-family:var(--serif);font-size:1.12rem;'
        f'font-weight:700;color:{C_TEXT};letter-spacing:-0.01em;'
        f'margin-bottom:5px;">{scenario.name}</div>'
        f'<div style="font-family:var(--sans);font-size:0.83rem;'
        f'color:{C_TEXT2};line-height:1.6;">'
        f'{getattr(scenario, "description", "")}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<div style="margin:6px 0 6px;">'
        f'{_eyebrow("Modeled shock inputs")}</div>',
        unsafe_allow_html=True,
    )

    # Modeled impact KPI strip — drawn from the scenario's intrinsic shocks.
    bdi = float(getattr(scenario, "bdi_shock", 0.0))
    fuel = float(getattr(scenario, "fuel_shock", 0.0))
    demand = float(getattr(scenario, "demand_shock", 0.0))
    pmi = float(getattr(scenario, "pmi_shock", 0.0))

    cards = [
        {
            "label":    "Modeled BDI Shock",
            "value":    f"{bdi:+.0%}",
            "accent":   _delta_color(bdi, higher_is_good=True),
            "sublabel": "dry-bulk freight rates",
        },
        {
            "label":    "Modeled Fuel Shock",
            "value":    f"{fuel:+.0%}",
            "accent":   _delta_color(fuel, higher_is_good=False),
            "sublabel": "bunker / WTI cost overlay",
        },
        {
            "label":    "Modeled Demand Shock",
            "value":    f"{demand:+.0%}",
            "accent":   _delta_color(demand, higher_is_good=True),
            "sublabel": "container volume vs norm",
        },
        {
            "label":    "Modeled PMI Shock",
            "value":    f"{pmi:+.1f}",
            "accent":   _delta_color(pmi, higher_is_good=True),
            "sublabel": "manufacturing activity (abs)",
        },
    ]

    # If live routes exist, add the run_scenario opportunity delta as a 5th card.
    route_delta = _scenario_route_delta(scenario, route_results)
    if route_delta is not None:
        cards.append({
            "label":    "Route Opportunity Δ",
            "value":    f"{route_delta:+.0%}",
            "accent":   _delta_color(route_delta, higher_is_good=True),
            "sublabel": "modeled avg across tracked routes",
        })

    metric_card_row(cards, columns=len(cards))

    # Structural-assumption context, when the scenario closes a chokepoint
    # or layers in a tariff. Rendered as discrete chips for at-a-glance scan.
    canal_flags = []
    if getattr(scenario, "suez_closed", False):
        canal_flags.append("Suez Canal closed")
    if getattr(scenario, "panama_closed", False):
        canal_flags.append("Panama Canal closed")
    tariff = float(getattr(scenario, "us_china_tariff_hike", 0.0))
    if tariff:
        canal_flags.append(f"US&ndash;China tariff +{tariff:.0%}")
    if canal_flags:
        chips = "".join(
            f'<span style="display:inline-flex;align-items:center;gap:5px;'
            f'padding:2px 9px;border-radius:3px;'
            f'background:{_rgba(C_LOW, 0.08)};'
            f'border:1px solid {_rgba(C_LOW, 0.22)};'
            f'font-family:var(--sans);font-size:0.72rem;font-weight:600;'
            f'color:{C_TEXT2};">'
            f'<span style="width:5px;height:5px;border-radius:50%;'
            f'background:{C_LOW};display:inline-block;"></span>{flag}</span>'
            for flag in canal_flags
        )
        st.markdown(
            f'<div style="display:flex;align-items:center;flex-wrap:wrap;'
            f'gap:8px;margin-top:12px;padding-top:10px;'
            f'border-top:1px dotted var(--rule);">'
            f'<span style="font-family:var(--sans);font-size:0.66rem;'
            f'font-weight:700;text-transform:uppercase;letter-spacing:0.1em;'
            f'color:{C_TEXT3};">Also assumes</span>{chips}</div>',
            unsafe_allow_html=True,
        )


# ── Section D2: SSI component-predictiveness backtest ──────────────────────


def _build_component_predictiveness_bars(scorecards: list) -> go.Figure:
    """Horizontal bars of per-SSI-component sign-agreement vs. forward rate.

    Companion visual for the per-component backtest in
    ``processing.ssi_component_validation``. Each component's
    sign-agreement rate sits on the x-axis (0–1, 0.5 = random baseline);
    bars are sorted strongest at the top and coloured green / amber / red
    by their edge above 0.5.

    Pure builder — no ``st.*`` calls — exercised by the validator's own
    test suite via the ``_build_*`` import. Empty list returns annotated.
    """
    fig = go.Figure()
    items = list(scorecards or [])
    if not items:
        fig.add_annotation(
            text="No component scorecards",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="SSI Component Predictiveness", height=220)
        return fig

    # Sort ascending so Plotly's bottom-up axis puts the best at the TOP.
    ranked = sorted(items, key=lambda sc: float(getattr(sc, "sign_agreement_rate", 0.0)))
    labels = [_SSI_COMPONENT_DISPLAY.get(sc.component, sc.component.title())
              for sc in ranked]
    rates  = [float(sc.sign_agreement_rate) for sc in ranked]
    weights = [float(getattr(sc, "weight", 0.0)) for sc in ranked]

    def _band(rate: float) -> str:
        if rate >= 0.60:
            return C_HIGH
        if rate >= 0.50:
            return C_MOD
        return C_LOW
    colors = [_band(r) for r in rates]

    fig.add_trace(go.Bar(
        x=rates,
        y=labels,
        orientation="h",
        marker={"color": colors,
                "line": {"color": "#0c0e14", "width": 1}},
        text=[f"{r * 100:.0f}%" for r in rates],
        textposition="outside",
        textfont={"color": C_TEXT2, "size": 11},
        customdata=[f"w {w * 100:.0f}%" for w in weights],
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Sign-agreement: %{x:.2%}<br>"
            "Weight in SSI: %{customdata}<extra></extra>"
        ),
        showlegend=False,
    ))
    # Random-baseline reference at 0.5
    fig.add_vline(
        x=0.5,
        line={"color": "rgba(255,255,255,0.18)", "width": 1, "dash": "dot"},
        annotation_text="random",
        annotation_position="top",
        annotation_font={"color": C_TEXT3, "size": 9},
    )

    apply_dark_layout(
        fig,
        title="SSI Component Predictiveness — sign-agreement vs. forward rate",
        height=max(220, 60 + 30 * len(ranked)),
    )
    fig.update_layout(
        xaxis={"title": "Sign-agreement rate (0–1)", "range": [0, 1.05],
               "gridcolor": "rgba(255,255,255,0.04)"},
        yaxis={"title": None, "automargin": True,
               "tickfont": {"color": C_TEXT2, "size": 11}},
        margin={"l": 8, "r": 60, "t": 44, "b": 36},
        bargap=0.35,
    )
    return fig


def _build_horizon_decay_heatmap(report) -> go.Figure:
    """Heatmap of sign-agreement rates across (component × horizon).

    Companion to ``_build_component_predictiveness_bars``. Each row is
    one SSI component; each column is one forecast horizon (in days).
    Cell colour: red below 0.45, gray near 0.50, green above 0.55.
    Sign-agreement is intentionally pinned to the [0.30, 0.70] visible
    range so the operator sees subtle predictiveness shifts that would
    otherwise wash out on a full [0, 1] scale.

    Pure builder — no ``st.*`` calls. ``report.cells`` empty → annotated.
    """
    fig = go.Figure()
    if not report or not getattr(report, "cells", None):
        fig.add_annotation(
            text="No horizon decay data",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="Horizon decay", height=240)
        return fig

    grid = report.rates_grid()
    components = [_SSI_COMPONENT_DISPLAY.get(c, c.title())
                  for c in report.components]
    horizons   = [f"{h}d" for h in report.horizons]
    text       = [[f"{v * 100:.0f}%" for v in row] for row in grid]

    colorscale = [
        [0.00, "#c0392b"],
        [0.40, "#c9962b"],
        [0.50, "#5a5650"],
        [0.60, "#2e9e6e"],
        [1.00, "#1f8a5b"],
    ]
    fig.add_trace(go.Heatmap(
        z=grid,
        x=horizons,
        y=components,
        colorscale=colorscale,
        zmin=0.30,
        zmax=0.70,
        text=text,
        texttemplate="%{text}",
        textfont={"color": "#0c0e14", "size": 11},
        hovertemplate=(
            "<b>%{y}</b> · %{x}<br>"
            "Sign-agreement: %{z:.1%}<extra></extra>"
        ),
        showscale=True,
        colorbar={
            "title": {"text": "Sign-agreement", "side": "right",
                      "font": {"color": C_TEXT3, "size": 10}},
            "tickfont": {"color": C_TEXT3, "size": 9},
            "len": 0.85,
            "thickness": 12,
            "outlinewidth": 0,
        },
    ))
    apply_dark_layout(
        fig,
        title="SSI Horizon Decay — sign-agreement across forecast horizons",
        height=max(240, 80 + 44 * len(components)),
    )
    fig.update_layout(
        xaxis={"title": "Forecast horizon (days)", "side": "top",
               "tickfont": {"color": C_TEXT2, "size": 11}},
        yaxis={"title": None, "automargin": True,
               "tickfont": {"color": C_TEXT2, "size": 11}},
        margin={"l": 8, "r": 60, "t": 60, "b": 24},
    )
    return fig


def _build_collinearity_heatmap(report) -> go.Figure:
    """N × N symmetric heatmap of pairwise SSI-component correlations.

    Range pinned to [-1, 1] with a red → gray → green colourway centred
    on zero. The diagonal is exactly 1.0 by construction. Operationally
    answers: *do any two SSI components move together strongly enough
    that the static weights are double-counting their shared signal?*

    Pure builder — no ``st.*`` calls. Empty report → annotated-empty.
    """
    fig = go.Figure()
    if not report or not getattr(report, "components", None):
        fig.add_annotation(
            text="No component history",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="Component Collinearity", height=240)
        return fig

    matrix     = report.corr_matrix()
    components = [_SSI_COMPONENT_DISPLAY.get(c, c.title())
                  for c in report.components]
    text = [[f"{v:+.2f}" for v in row] for row in matrix]

    # Red ↔ gray ↔ green centred on zero
    colorscale = [
        [0.00, "#c0392b"],
        [0.35, "#c9962b"],
        [0.50, "#5a5650"],
        [0.65, "#2e9e6e"],
        [1.00, "#1f8a5b"],
    ]
    fig.add_trace(go.Heatmap(
        z=matrix,
        x=components,
        y=components,
        colorscale=colorscale,
        zmin=-1.0,
        zmax=1.0,
        text=text,
        texttemplate="%{text}",
        textfont={"color": "#0c0e14", "size": 11},
        hovertemplate=(
            "<b>%{y}</b> ↔ <b>%{x}</b><br>"
            "Pearson r: %{z:+.3f}<extra></extra>"
        ),
        showscale=True,
        colorbar={
            "title": {"text": "r", "side": "right",
                      "font": {"color": C_TEXT3, "size": 10}},
            "tickfont": {"color": C_TEXT3, "size": 9},
            "len": 0.85,
            "thickness": 12,
            "outlinewidth": 0,
        },
    ))
    apply_dark_layout(
        fig,
        title="SSI Component Collinearity — pairwise Pearson r",
        height=max(280, 80 + 50 * len(components)),
    )
    fig.update_layout(
        xaxis={"title": None, "side": "top",
               "tickfont": {"color": C_TEXT2, "size": 11}},
        yaxis={"title": None, "automargin": True,
               "tickfont": {"color": C_TEXT2, "size": 11}},
        margin={"l": 8, "r": 60, "t": 60, "b": 24},
    )
    return fig


def _render_component_predictiveness() -> None:
    """Surface the per-SSI-component backtest scorecard.

    Uses ``processing.ssi_component_validation.validate_ssi_components`` —
    a deterministic, seed-stable backtest that scores how well each SSI
    component predicts forward rate moves. Pairs the existing
    sign-agreement bars with a horizon-decay heatmap from
    ``validate_ssi_horizons`` and a collinearity heatmap from
    ``compute_component_collinearity``. Together the three answer:
    *which components are predictive, at what horizon, and which are
    secretly double-counting the same signal?*

    Lazy-imported so a backtest module failure can't break the tab.
    """
    try:
        from processing.ssi_component_validation import (
            compute_component_collinearity,
            validate_ssi_components,
            validate_ssi_horizons,
        )
    except Exception:
        logger.exception("Macro Projection — component validator import failed")
        return

    section_header(
        "Component Predictiveness",
        "Which SSI components actually lead the rate move? "
        "Sign-agreement = % of windows where the component's stress delta "
        "predicted the direction of the forward rate move.",
    )
    try:
        report = validate_ssi_components()
        decay  = validate_ssi_horizons(horizons=(1, 7, 14, 30, 60))
        collin = compute_component_collinearity()
    except Exception:
        logger.exception("Macro Projection — validate_ssi_components failed")
        _empty_state(
            "Component validator failed — see logs.",
            accent=C_MOD,
        )
        return

    st.plotly_chart(
        _build_component_predictiveness_bars(report.scorecards),
        use_container_width=True,
        config={"displayModeBar": False},
        key="macro_projection_component_predictiveness",
    )
    st.caption(report.summary)

    # Horizon-decay heatmap — same backtest, sliced across forecast
    # horizons so the operator sees where each component carries its edge.
    st.plotly_chart(
        _build_horizon_decay_heatmap(decay),
        use_container_width=True,
        config={"displayModeBar": False},
        key="macro_projection_horizon_decay",
    )
    st.caption(decay.summary)

    # Collinearity heatmap — flags any two components that move together
    # strongly enough that the static SSI weights double-count the shared
    # signal. Complementary to the per-component predictiveness above.
    st.plotly_chart(
        _build_collinearity_heatmap(collin),
        use_container_width=True,
        config={"displayModeBar": False},
        key="macro_projection_component_collinearity",
    )
    st.caption(collin.summary)


# ── Section E: leading-indicator context strip ──────────────────────────────

def _render_leading_indicator_strip(macro_data) -> None:
    """Render the leading-indicator context strip (section E).

    Leading indicators sit *ahead* of shipping demand — they contextualise
    whether the projected stress lands into a strengthening or weakening
    macro backdrop. Uses ``processing.leading_indicators``.
    """
    section_header(
        "Leading-Indicator Context",
        "Where the projection lands — macro signals ahead of shipping demand",
    )

    if not macro_data:
        _empty_state(
            "No macro data supplied &mdash; the leading-indicator context "
            "strip is unavailable for this run."
        )
        return

    try:
        from processing.leading_indicators import (
            build_leading_indicators,
            compute_leading_indicator_score,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Macro Projection — leading_indicators import failed")
        _empty_state("Leading-indicator module unavailable.", accent=C_MOD)
        return

    score = compute_leading_indicator_score(macro_data)
    composite = float(score.get("composite_score", 0.5))
    forecast = str(score.get("four_week_forecast", "STABLE"))
    weighted = float(score.get("weighted_signal", 0.0))

    metric_card_row(
        [
            {
                "label":    "Leading-Indicator Score",
                "value":    f"{composite:.0%}",
                "accent":   _schi_band_color(composite),
                "sublabel": "composite shipping-demand read",
            },
            {
                "label":    "4-Week Forecast",
                "value":    forecast.title(),
                "accent":   _FORECAST_COLOR.get(forecast, C_TEXT2),
                "sublabel": f"weighted signal {weighted:+.2f}",
            },
            {
                "label":    "Bullish Signals",
                "value":    str(score.get("bullish_count", 0)),
                "accent":   C_HIGH,
                "sublabel": "indicators pointing up",
            },
            {
                "label":    "Bearish Signals",
                "value":    str(score.get("bearish_count", 0)),
                "accent":   C_LOW,
                "sublabel": "indicators pointing down",
            },
        ],
        columns=4,
    )

    # One-line read of where the projected stress lands — strengthening,
    # steady, or weakening backdrop. Accent tracks the forecast verdict.
    f_color = _FORECAST_COLOR.get(forecast, C_TEXT2)
    if forecast == "EXPANSION":
        backdrop = (
            "a <b>strengthening</b> macro backdrop &mdash; projected stress "
            "lands into improving shipping demand"
        )
    elif forecast == "CONTRACTION":
        backdrop = (
            "a <b>weakening</b> macro backdrop &mdash; projected stress "
            "compounds softening shipping demand"
        )
    else:
        backdrop = (
            "a <b>steady</b> macro backdrop &mdash; leading signals show no "
            "clear directional pull on shipping demand"
        )
    _narrative(
        f"Leading indicators run four-or-more weeks ahead of shipping demand. "
        f"They currently point to "
        f"<b style=\"color:{f_color}\">{forecast.title()}</b> &mdash; "
        f"{backdrop}.",
        accent=f_color,
    )

    # Top-weighted indicators table for a transparent read.
    try:
        indicators = build_leading_indicators(macro_data)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Macro Projection — build_leading_indicators failed")
        indicators = []

    indicators = [
        ind for ind in indicators
        if getattr(ind, "previous_value", 0.0) or getattr(ind, "current_value", 0.0)
    ]
    if indicators:
        st.markdown(
            f'<div style="margin:2px 0 6px;">'
            f'{_eyebrow("Top-weighted signals &middot; ranked by composite weight")}'
            f'</div>',
            unsafe_allow_html=True,
        )
        top = sorted(
            indicators, key=lambda i: getattr(i, "weight", 0.0), reverse=True
        )[:8]
        rows = []
        for ind in top:
            sig = getattr(ind, "signal", "NEUTRAL")
            chg = float(getattr(ind, "change_pct", 0.0))
            rows.append([
                _sans(getattr(ind, "name", "—"), color=C_TEXT, weight=700),
                _mono(f"{getattr(ind, 'current_value', 0.0):,.2f}", color=C_TEXT),
                _mono(f"{chg:+.2f}%", color=_delta_color(
                    chg if sig != "BEARISH" else -abs(chg), higher_is_good=True
                )),
                badge(sig.title(), color=_SIGNAL_COLOR.get(sig, C_TEXT2)),
                _mono(f"{getattr(ind, 'lead_time_weeks', 0)}w", color=C_TEXT2),
            ])
        wsj_market_table(
            headers=["Indicator", "Latest", "Change", "Signal", "Lead"],
            rows=rows,
        )
    else:
        _empty_state(
            "The leading-indicator series carried no observations in the "
            "supplied macro data &mdash; only the composite read above is "
            "available."
        )


# ── Public entry point ──────────────────────────────────────────────────────

def render(
    port_results=None,
    freight_data=None,
    macro_data=None,
    route_results=None,
    **kwargs,
) -> None:
    """Render the Macro Projection tab.

    Stage 3 of the Disruption Alpha chain — projects fleet-wide disruption (the
    Shipping Stress Index) onto a macro read (the Supply Chain Health Index),
    explains the linkage between the two, surfaces the closest predefined
    scenario as a modeled projection, and contextualises it with leading
    indicators.

    Parameters
    ----------
    port_results, freight_data, macro_data, route_results:
        The platform-standard analysis inputs computed at the top of
        ``app.py``. Every one is optional — ``render()`` is robust to all-None
        / empty inputs and degrades to neutral defaults rather than raising.
    **kwargs:
        Accepted and ignored for call-site argument safety.

    Returns
    -------
    None
    """
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('macro_projection'):
        # ── A. Page header ──────────────────────────────────────────────────────
        page_header(
            title="Macro Projection",
            subtitle="Fleet-wide disruption projected to a macro read — "
            "Shipping Stress Index vs Supply Chain Health, plus a scenario lens",
            badge_text="MODELED",
            badge_color=C_CONV,
        )
        try:
            _chain_stage_strip()
        except Exception:  # pragma: no cover - decorative only
            logger.exception("Macro Projection — chain-stage strip failed")

        # ── Normalise inputs — be robust to all-None / empty ────────────────────
        freight_data = freight_data or {}
        macro_data = macro_data or {}
        port_results = port_results or []
        route_results = route_results or []

        # Hand the (unhashable) inputs to the cached compute wrappers.
        _COMPUTE_INPUTS["freight_data"] = freight_data
        _COMPUTE_INPUTS["macro_data"] = macro_data
        _COMPUTE_INPUTS["port_results"] = port_results
        _COMPUTE_INPUTS["route_results"] = route_results

        # ── Compute both composites (cached, date-keyed) ────────────────────────
        ssi_report = None
        schi_report = None
        token = _date_token()
        try:
            ssi_report = _cached_ssi_report(token)
        except Exception:
            logger.exception("Macro Projection — SSI computation failed")
        try:
            schi_report = _cached_schi_report(token)
        except Exception:
            logger.exception("Macro Projection — SCHI computation failed")

        if ssi_report is None and schi_report is None:
            st.error(
                "Both the Shipping Stress Index and the Supply Chain Health Index "
                "could not be computed from the supplied inputs."
            )
            st.markdown(
                source_footer([_MACRO_PROJECTION_SOURCE]), unsafe_allow_html=True
            )
            return

        # ── B. Side-by-side gauges ──────────────────────────────────────────────
        if ssi_report is not None and schi_report is not None:
            section_header(
                "Composite Read",
                "Two lenses on the same fleet — disruption (SSI) in, health (SCHI) out",
            )
            try:
                _render_gauges(ssi_report, schi_report)
            except Exception:
                logger.exception("Macro Projection — gauges failed")
                st.error("Composite gauges unavailable.")
        else:
            # One of the two composites is missing — say so plainly.
            missing = "Shipping Stress Index" if ssi_report is None \
                else "Supply Chain Health Index"
            _empty_state(
                f"The {missing} could not be computed from the supplied inputs "
                f"&mdash; the side-by-side composite comparison is unavailable.",
                accent=C_MOD,
            )

        section_divider()

        # ── C. Stress -> health narrative ───────────────────────────────────────
        if ssi_report is not None and schi_report is not None:
            try:
                _render_stress_health_narrative(ssi_report, schi_report)
            except Exception:
                logger.exception("Macro Projection — stress/health narrative failed")
                st.error("Stress-to-health linkage narrative unavailable.")
            section_divider()

        # ── D. Scenario projection lens ─────────────────────────────────────────
        if ssi_report is not None:
            try:
                _render_scenario_lens(ssi_report, route_results)
            except Exception:
                logger.exception("Macro Projection — scenario lens failed")
                st.error("Scenario projection lens unavailable.")
            section_divider()

        # ── D2. SSI component-predictiveness backtest ───────────────────────────
        try:
            _render_component_predictiveness()
        except Exception:
            logger.exception("Macro Projection — component predictiveness failed")
            st.error("Component predictiveness panel unavailable.")
        section_divider()

        # ── E. Leading-indicator context strip ──────────────────────────────────
        try:
            _render_leading_indicator_strip(macro_data)
        except Exception:
            logger.exception("Macro Projection — leading-indicator strip failed")
            st.error("Leading-indicator context strip unavailable.")

        # ── F. Provenance footer ────────────────────────────────────────────────
        try:
            st.markdown(
                source_footer([_MACRO_PROJECTION_SOURCE]), unsafe_allow_html=True
            )
        except Exception:
            logger.exception("Macro Projection — source footer failed")
