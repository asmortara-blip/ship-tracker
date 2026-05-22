"""tab_rate_analytics.py — Freight Rate Analytics Dashboard.

Playbook-compliant implementation that imports palette + helpers from
``ui/styles.py`` instead of redeclaring constants or inlining HTML. See
``docs/TAB_MIGRATION.md`` for the 10-step recipe that produced this file.

Sections:
  1. Page header
  2. Market regime KPI strip (regime, avg z-score, avg percentile, avg vol)
  3. Route-level regime table
  4. Rate spread analysis table
"""
from __future__ import annotations

import streamlit as st

# Single source of truth for palette, typography, and component helpers.
# Never redeclare color constants in a tab module — always import them.
from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    badge,
    metric_card_row,
    page_header,
    section_header,
    wsj_market_table,
)


# ── Domain-specific color mappings ──────────────────────────────────────────
# Keep *semantic* mappings (regime → color) local to the tab. Keep *palette*
# constants (what "C_HIGH" resolves to) in ui/styles.py.

_REGIME_COLOR: dict[str, str] = {
    "Boom":           C_HIGH,
    "Above Average":  C_HIGH,
    "Normal":         C_ACCENT,
    "Below Average":  C_MOD,
}

_MARKET_REGIME_COLORS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("Bull", "Growth"),       C_HIGH),
    (("Bear", "Contraction"),  C_LOW),
)


def _regime_color(regime: str) -> str:
    """Map a route-level regime string to a semantic palette color."""
    return _REGIME_COLOR.get(regime, C_LOW)


def _market_regime_color(regime: str) -> str:
    """Map the overall market regime string to a semantic palette color."""
    for keywords, color in _MARKET_REGIME_COLORS:
        if any(k in regime for k in keywords):
            return color
    return C_MOD


def _zscore_color(z: float, threshold: float = 0.5) -> str:
    if z > threshold:
        return C_HIGH
    if z < -threshold:
        return C_LOW
    return C_TEXT2


def _trend_color(pct: float) -> str:
    if pct > 0:
        return C_HIGH
    if pct < 0:
        return C_LOW
    return C_TEXT2


def _percentile_color(pct: float) -> str:
    if pct > 70:
        return C_HIGH
    if pct < 30:
        return C_LOW
    return C_TEXT2


def _spread_signal_color(signal: str) -> str:
    if signal == "Wide":
        return C_LOW
    if signal == "Narrow":
        return C_HIGH
    return C_TEXT2


# ── Cell formatters for WSJ market tables ───────────────────────────────────
# wsj_market_table() renders cell strings as raw HTML inside <td>. The table
# CSS (`.wsj-market-table`) already handles alignment, rule lines, and hover.
# These helpers only need to style *content* (font family + conditional color).

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


# ── Public entry point ──────────────────────────────────────────────────────

def render(freight_data=None, route_results=None, **kwargs) -> None:
    """Render the Freight Rate Analytics dashboard."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('rate_analytics'):
        try:
            from processing.rate_analytics import compute_rate_regime, compute_rate_spreads
        except ImportError as e:
            st.error(f"Rate analytics module unavailable: {e}")
            return

        # Apply the cross-tab filter bar's narrowing (route + date range).
        try:
            from state.session import apply_filters_to_freight
            from ui.filter_bar import active_filters
            filters = active_filters()
            freight_data = apply_filters_to_freight(freight_data or {}, filters)
        except Exception:
            freight_data = freight_data or {}

        if not freight_data:
            st.info(
                "No freight data after applying the cross-tab filter bar. "
                "Clear route / date selections to see all data."
            )
            return

        # ── 1. Page header ──────────────────────────────────────────────────────
        page_header(
            title="Freight Rate Analytics",
            subtitle="Rate regime detection, percentile ranking, and market spread analysis",
        )

        # ── 2. Market regime KPI strip ──────────────────────────────────────────
        regime = compute_rate_regime(freight_data)
        market_regime = regime.get("market_regime", "N/A")

        metric_card_row(
            [
                {
                    "label":  "Market Regime",
                    "value":  market_regime,
                    "accent": _market_regime_color(market_regime),
                },
                {
                    "label":  "Avg Z-Score",
                    "value":  f"{regime.get('avg_z_score', 0):.2f}",
                    "accent": C_ACCENT,
                },
                {
                    "label":  "Avg Percentile",
                    "value":  f"{regime.get('avg_percentile', 50):.0f}th",
                    "accent": C_ACCENT,
                },
                {
                    "label":  "Avg Volatility",
                    "value":  f"{regime.get('avg_volatility', 0):.1f}%",
                    "accent": C_MOD,
                },
            ],
            columns=4,
        )

        # ── 3. Route-level regime table ─────────────────────────────────────────
        routes: dict = regime.get("routes", {}) or {}
        if routes:
            section_header("Route-Level Rate Regimes")

            rows = []
            for route_key, r in sorted(
                routes.items(), key=lambda kv: kv[1]["z_score"], reverse=True
            ):
                regime_label = r["regime"]
                regime_color = _regime_color(regime_label)
                z             = r["z_score"]
                trend_pct     = r["trend_30d_pct"]
                trend_sign    = "+" if trend_pct >= 0 else ""
                pct           = r["percentile"]

                rows.append([
                    _sans(str(route_key)[:20], color=C_TEXT, weight=700),
                    _mono(f"{r['current']:,.0f}",         color=C_TEXT),
                    _mono(f"{r['mean']:,.0f}",            color=C_TEXT2),
                    _mono(f"{z:+.2f}",                    color=_zscore_color(z)),
                    _mono(f"{pct:.0f}th",                 color=_percentile_color(pct)),
                    badge(regime_label, color=regime_color),
                    _mono(f"{trend_sign}{trend_pct:.1f}%", color=_trend_color(trend_pct)),
                    _mono(f"{r['volatility_annual']:.1f}%", color=C_TEXT2),
                ])

            wsj_market_table(
                headers=[
                    "Route", "Current", "Mean", "Z-Score",
                    "Pctile", "Regime", "30d Trend", "Volatility",
                ],
                rows=rows,
            )

        # ── 4. Rate spreads ─────────────────────────────────────────────────────
        spreads = compute_rate_spreads(freight_data) or []
        if spreads:
            section_header(
                "Rate Spread Analysis",
                subtitle="Most dislocated route pairs by z-score",
            )

            rows = []
            for sp in spreads[:8]:
                signal = sp["signal"]
                rows.append([
                    _sans(
                        f"{str(sp['route1'])[:14]} / {str(sp['route2'])[:14]}",
                        color=C_TEXT, weight=600,
                    ),
                    _mono(f"{sp['current_spread']:+,.0f}", color=C_TEXT),
                    _mono(f"{sp['mean_spread']:+,.0f}",    color=C_TEXT2),
                    _mono(f"{sp['z_score']:+.2f}",         color=_zscore_color(sp["z_score"], threshold=1.0)),
                    _mono(f"{sp['correlation']:.2f}",      color=C_TEXT2),
                    badge(signal, color=_spread_signal_color(signal)),
                ])

            wsj_market_table(
                headers=["Route Pair", "Spread", "Mean", "Z-Score", "Correlation", "Signal"],
                rows=rows,
            )
