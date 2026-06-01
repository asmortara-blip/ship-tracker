"""ui/filter_bar.py — cross-tab filter bar.

Renders a compact horizontal strip of controls (date range, universe,
routes, demo-mode) at the top of every tab. Filter values persist via
state/session.py's ``SessionState.filters`` so any tab that reads them
sees the user's latest selection regardless of which tab made the change.

Public surface
--------------
  render_filter_bar() -> Filters
      Renders the bar, returns the resolved Filters dataclass.

  active_filters() -> Filters
      Read-only accessor — what's currently selected, without rendering UI.
      Useful for tabs that conditionally apply filters.

Usage in a tab
--------------
  from ui.filter_bar import active_filters
  from state.session import apply_filters_to_freight

  def render(freight_data, …):
      filters = active_filters()
      freight_filtered = apply_filters_to_freight(freight_data, filters)
      …
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import streamlit as st
from loguru import logger

from state.session import Filters, get_session
from ui.styles import C_ACCENT, C_TEXT, C_TEXT2, C_TEXT3


# ─── Defaults / palette helpers ────────────────────────────────────────────

_DEFAULT_TICKER_UNIVERSE: tuple[str, ...] = (
    "ZIM", "MATX", "SBLK", "DAC", "STNG", "CMRE", "GOGL", "EGLE",
    "DHT", "FRO", "EURN", "TNK", "GSL", "DSX", "HAFNI",
)


def _load_route_options() -> list[str]:
    """Return every route_id in the registry — used to populate the routes
    multi-select. Cached lazily; failures fall back to an empty list."""
    try:
        from routes.route_registry import ROUTES
        return [r.id for r in ROUTES]
    except Exception as exc:
        logger.debug(f"filter_bar: route registry unavailable: {exc}")
        return []


def _load_region_options() -> list[str]:
    """Distinct region pairs derived from the route registry."""
    try:
        from routes.route_registry import ROUTES
        regions: set[str] = set()
        for r in ROUTES:
            regions.add(getattr(r, "origin_region", ""))
            regions.add(getattr(r, "dest_region", ""))
        return sorted(r for r in regions if r)
    except Exception:
        return []


# ─── Public API ────────────────────────────────────────────────────────────

def active_filters() -> Filters:
    """Read-only access to the current filters. Safe to call without a
    Streamlit runtime — falls through to defaults via ``get_session()``."""
    try:
        return get_session().filters
    except Exception:
        return Filters()


def render_filter_bar() -> Filters:
    """Render the cross-tab filter bar and persist any changes.

    Layout: a single horizontal strip with five compact controls:
      [ Date range ] [ Universe ] [ Routes ] [ Regions ] [ Demo mode ]

    Returns the (possibly updated) ``Filters`` so the caller can apply
    them immediately without re-reading session state.
    """
    state = get_session()
    filters = state.filters

    # ── Container styling — subtle background strip ────────────────────────
    st.markdown(
        f'<div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.12em;'
        f'color:{C_TEXT3};font-weight:600;margin:6px 0 4px 0">'
        f'Cross-Tab Filters · changes persist across every tab</div>',
        unsafe_allow_html=True,
    )

    # 5 columns; the date pickers share one column for compactness.
    cols = st.columns([1.8, 1.4, 1.4, 1.2, 0.8], gap="small")

    # ── Date range ────────────────────────────────────────────────────────
    with cols[0]:
        default_start = filters.date_start or (date.today() - timedelta(days=365))
        default_end = filters.date_end or date.today()
        date_range = st.date_input(
            "Date range",
            value=(default_start, default_end),
            key="filter_bar_date_range",
            label_visibility="visible",
        )
        # st.date_input returns either a tuple (start, end) or a single date
        # while the user is still picking. Normalize.
        if isinstance(date_range, tuple) and len(date_range) == 2:
            new_start, new_end = date_range
        elif isinstance(date_range, tuple) and len(date_range) == 1:
            new_start = date_range[0]
            new_end = default_end
        elif isinstance(date_range, date):
            new_start = date_range
            new_end = default_end
        else:
            new_start, new_end = default_start, default_end

    # ── Universe ──────────────────────────────────────────────────────────
    with cols[1]:
        # Merge the canonical default universe with anything already in
        # the filter so user-added tickers don't disappear.
        ticker_options = sorted(set(_DEFAULT_TICKER_UNIVERSE) | set(filters.universe))
        new_universe = st.multiselect(
            "Universe",
            options=ticker_options,
            default=list(filters.universe),
            key="filter_bar_universe",
            placeholder="All tickers",
            label_visibility="visible",
        )

    # ── Routes ────────────────────────────────────────────────────────────
    with cols[2]:
        route_options = _load_route_options()
        # Same merge trick — never drop a route the user already selected.
        if filters.routes:
            route_options = sorted(set(route_options) | set(filters.routes))
        new_routes = st.multiselect(
            "Routes",
            options=route_options,
            default=list(filters.routes),
            key="filter_bar_routes",
            placeholder="All routes",
            label_visibility="visible",
        )

    # ── Regions ───────────────────────────────────────────────────────────
    with cols[3]:
        region_options = _load_region_options()
        if filters.regions:
            region_options = sorted(set(region_options) | set(filters.regions))
        new_regions = st.multiselect(
            "Regions",
            options=region_options,
            default=list(filters.regions),
            key="filter_bar_regions",
            placeholder="All regions",
            label_visibility="visible",
        )

    # ── Demo mode toggle ──────────────────────────────────────────────────
    with cols[4]:
        st.markdown(
            '<div style="height:28px"></div>',  # vertical alignment with the controls above
            unsafe_allow_html=True,
        )
        new_demo = st.toggle(
            "Demo data",
            value=bool(filters.demo_mode),
            key="filter_bar_demo_mode",
            help="When on, tabs prefer synthetic/example data over live feeds.",
        )

    # ── Persist into session ──────────────────────────────────────────────
    updated = Filters(
        date_start=new_start,
        date_end=new_end,
        universe=tuple(new_universe),
        routes=tuple(new_routes),
        regions=tuple(new_regions),
        demo_mode=bool(new_demo),
    )
    state.filters = updated

    # ── Active-filters chip strip (under the controls) ────────────────────
    chips: list[str] = []
    if updated.date_start and updated.date_end:
        chips.append(
            f"{updated.date_start.strftime('%Y-%m-%d')} → "
            f"{updated.date_end.strftime('%Y-%m-%d')}"
        )
    if updated.universe:
        chips.append(f"{len(updated.universe)} ticker(s)")
    if updated.routes:
        chips.append(f"{len(updated.routes)} route(s)")
    if updated.regions:
        chips.append(f"{len(updated.regions)} region(s)")
    if updated.demo_mode:
        chips.append("demo mode")
    chips_html = " · ".join(chips) if chips else "no narrowing — every tab sees all data"
    st.markdown(
        f'<div style="font-size:0.7rem;color:{C_TEXT3};margin-bottom:14px">'
        f'<b style="color:{C_TEXT2}">Active:</b> {chips_html}</div>',
        unsafe_allow_html=True,
    )

    return updated


__all__ = ["render_filter_bar", "active_filters"]
