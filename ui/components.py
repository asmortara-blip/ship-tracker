"""Deprecated — re-exports from ``ui.styles``.

This module was a second home for palette constants and widget helpers. The
canonical location is now ``ui.styles``. Nothing here should be imported in
new code; it stays as a back-compat shim while any remaining callers are
migrated, and will be deleted in a subsequent phase.

See ``docs/TAB_MIGRATION.md`` for the 10-step refactor recipe.
"""
from __future__ import annotations

import warnings

from ui.styles import (
    # Palette
    C_BG, C_SURFACE, C_CARD, C_BORDER,
    C_HIGH, C_MOD, C_LOW, C_ACCENT, C_CONV, C_MACRO,
    C_TEXT, C_TEXT2, C_TEXT3,
    # Helpers (canonical home: ui/styles.py)
    stat_counter, mini_sparkline, gauge_ring, alert_banner,
    kpi_row, shipping_heat_bar, section_divider,
)

warnings.warn(
    "ui.components is deprecated; import from ui.styles instead.",
    DeprecationWarning,
    stacklevel=2,
)


# route_card lives here for now — it reads a domain object shape specific to
# ``routes.route_registry`` and has not been promoted into ``ui.styles``. Tabs
# that need it should continue importing it from here until it moves.

def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _opportunity_color(score: float) -> str:
    if score >= 0.65:
        return C_HIGH
    if score >= 0.40:
        return C_MOD
    return C_LOW


def route_card(route, rank: int | None = None) -> None:
    """Render a rich route opportunity card (legacy consumer of routes.route_registry)."""
    import streamlit as st

    score      = getattr(route, "opportunity_score", 0.0)
    color      = _opportunity_color(score)
    bg_border  = _hex_to_rgba(color, 0.25)
    score_bg   = _hex_to_rgba(color, 0.12)

    rank_html = ""
    if rank is not None:
        rank_html = (
            f'<span style="background:{_hex_to_rgba(color, 0.15)};color:{color};'
            f'border:1px solid {_hex_to_rgba(color, 0.3)};border-radius:3px;'
            f'font-size:0.68rem;font-weight:700;padding:2px 8px;margin-right:8px;'
            f'vertical-align:middle;">#{rank}</span>'
        )

    origin  = getattr(route, "origin_region",  getattr(route, "origin_locode",  "—"))
    dest    = getattr(route, "dest_region",    getattr(route, "dest_locode",    "—"))
    transit = getattr(route, "transit_days",   "—")
    label   = getattr(route, "opportunity_label", "")
    rate    = getattr(route, "current_rate_usd_feu", None)
    trend   = getattr(route, "rate_trend", "")
    pct_chg = getattr(route, "rate_pct_change_30d", None)

    rate_html = ""
    if rate is not None:
        trend_arrow = {"Rising": "▲", "Falling": "▼", "Stable": "→"}.get(trend, "")
        trend_color = {"Rising": C_HIGH, "Falling": C_LOW, "Stable": C_TEXT2}.get(trend, C_TEXT2)
        pct_str = f" {abs(pct_chg):.1f}%" if pct_chg is not None else ""
        rate_html = (
            f'<div style="font-size:0.78rem;color:{C_TEXT2};margin-top:8px;">'
            f'Rate: <span style="color:{C_TEXT};font-weight:600;">'
            f'${rate:,.0f}/FEU</span>'
            f' <span style="color:{trend_color}">{trend_arrow}{pct_str}</span>'
            f'</div>'
        )

    sub_scores = {
        "Rate Momentum": getattr(route, "rate_momentum_component",        None),
        "Demand":        getattr(route, "demand_imbalance_component",     None),
        "Congestion":    getattr(route, "congestion_clearance_component", None),
        "Macro":         getattr(route, "macro_tailwind_component",       None),
    }
    sub_bars = ""
    for sub_label, sub_val in sub_scores.items():
        if sub_val is None:
            continue
        sub_color = _opportunity_color(sub_val)
        width     = max(4, int(sub_val * 60))
        sub_bars += (
            f'<div style="display:flex;align-items:center;gap:6px;margin-top:4px;">'
            f'<span style="font-size:0.65rem;color:{C_TEXT3};width:90px;flex-shrink:0;">'
            f'{sub_label}</span>'
            f'<div style="background:rgba(232,230,225,0.04);border-radius:3px;'
            f'flex:1;height:4px;overflow:hidden;">'
            f'<div style="background:{sub_color};height:4px;width:{width}px;'
            f'border-radius:3px;"></div></div>'
            f'<span style="font-size:0.65rem;color:{sub_color};width:28px;text-align:right;">'
            f'{sub_val:.2f}</span></div>'
        )

    rationale = getattr(route, "rationale", "")
    rationale_html = (
        f'<div style="font-size:0.75rem;color:{C_TEXT3};margin-top:10px;'
        f'line-height:1.5;border-top:1px solid rgba(232,230,225,0.04);'
        f'padding-top:8px;">{rationale}</div>' if rationale else ""
    )

    st.markdown(
        f"""
        <div class="slide-in" style="background:{C_CARD};border:1px solid {bg_border};
            border-left:4px solid {color};border-radius:6px;padding:18px 20px;
            margin-bottom:10px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
              {rank_html}
              <span style="font-size:0.95rem;font-weight:700;color:{C_TEXT};">
                {origin} → {dest}
              </span>
              <div style="font-size:0.75rem;color:{C_TEXT2};margin-top:3px;">
                Transit: {transit} days
              </div>
            </div>
            <div style="background:{score_bg};
              border:1px solid {_hex_to_rgba(color, 0.3)};border-radius:6px;
              padding:8px 14px;text-align:center;min-width:64px;">
              <div style="font-size:1.5rem;font-weight:900;color:{color};
                font-variant-numeric:tabular-nums;line-height:1;">
                {score:.0%}
              </div>
              <div style="font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;
                letter-spacing:0.06em;margin-top:2px;">{label}</div>
            </div>
          </div>
          {rate_html}
          <div style="margin-top:8px;">{sub_bars}</div>
          {rationale_html}
        </div>
        """, unsafe_allow_html=True)


__all__ = [
    # Palette (deprecated — import from ui.styles)
    "C_BG", "C_SURFACE", "C_CARD", "C_BORDER",
    "C_HIGH", "C_MOD", "C_LOW", "C_ACCENT", "C_CONV", "C_MACRO",
    "C_TEXT", "C_TEXT2", "C_TEXT3",
    # Widgets
    "stat_counter", "mini_sparkline", "gauge_ring", "alert_banner",
    "kpi_row", "shipping_heat_bar", "section_divider", "route_card",
]
