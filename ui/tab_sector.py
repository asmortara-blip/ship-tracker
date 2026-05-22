"""tab_sector.py — WSJ-style Shipping Sector Comparative Dashboard.

Refactored against the shared design system in ``ui/styles.py``. Displays:
  1. Page header + sector KPI strip
  2. Sector performance comparison table
  3. Sector profile cards (constituent equities, outlook)
  4. Global trade flow regional breakdown
"""
from __future__ import annotations

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
    badge,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)


# ── Domain-specific color mappings ──────────────────────────────────────────
# Keep *semantic* mappings (outlook → color, momentum → color) local to
# this tab; the palette constants live in ``ui/styles.py``.

_OUTLOOK_COLOR: dict[str, str] = {
    "Bullish":  C_HIGH,
    "Positive": "#4a8a6a",
    "Neutral":  C_ACCENT,
    "Negative": C_MOD,
    "Bearish":  C_LOW,
}

_MOMENTUM_COLOR: dict[str, str] = {
    "Strong":   C_HIGH,
    "Positive": C_MOD,
    "Neutral":  C_TEXT2,
    "Negative": C_LOW,
}

# Map sector outlook → insight_card action label so the action chip uses the
# shared ACTION_COLORS palette in ``ui/styles.py``.
_OUTLOOK_TO_ACTION: dict[str, str] = {
    "Bullish":  "Prioritize",
    "Positive": "Monitor",
    "Neutral":  "Watch",
    "Negative": "Caution",
    "Bearish":  "Avoid",
}


def _outlook_color(outlook: str) -> str:
    """Map an outlook label to a palette color (handles partial matches)."""
    for key, color in _OUTLOOK_COLOR.items():
        if key in outlook:
            return color
    return C_TEXT3


def _outlook_to_action(outlook: str) -> str:
    """Map an outlook label to a shared-design action label."""
    for key, action in _OUTLOOK_TO_ACTION.items():
        if key in outlook:
            return action
    return "Watch"


def _momentum_color(momentum: str) -> str:
    return _MOMENTUM_COLOR.get(momentum, C_TEXT3)


def _chg_color(val: float | None) -> str:
    if val is None:
        return C_TEXT3
    if val > 0:
        return C_HIGH
    if val < 0:
        return C_LOW
    return C_TEXT2


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "--"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def _ret_to_score(val: float | None) -> float:
    """Map a percent return to a 0..1 score for ``insight_card_html``.

    Anchors: -10% → 0.0, 0% → 0.5, +10% → 1.0. Clamped at the extremes.
    """
    if val is None:
        return 0.5
    return max(0.0, min(1.0, (val + 10.0) / 20.0))


# ── Cell formatters for WSJ market tables ───────────────────────────────────

def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sector_name_cell(name: str, description: str) -> str:
    """Two-line sector cell: serif name + sans description (content-only)."""
    desc = (description or "")[:50]
    return (
        f'<span style="font-family:var(--serif);font-size:0.84rem;'
        f'font-weight:700;color:{C_TEXT};display:block;">{name}</span>'
        f'<span style="font-family:var(--sans);font-size:0.68rem;'
        f'color:{C_TEXT3};display:block;margin-top:2px;">{desc}</span>'
    )


def _ticker_price_cell(ticker: str, price: float) -> str:
    """Single-line constituent cell: bold ticker + mono price (content-only)."""
    return (
        f'{_sans(ticker, color=C_TEXT, weight=700)}'
        f'<span style="margin:0 4px;color:{C_TEXT3};">·</span>'
        f'{_mono(f"${price:.2f}", color=C_TEXT2, weight=500)}'
    )


# ── Section renderers ───────────────────────────────────────────────────────

def _render_hero(sectors: list[dict], source: DataSource) -> None:
    """Top-of-page header + summary KPI strip."""
    page_header(
        title="Shipping Sector Dashboard",
        subtitle=(
            "Comparative performance across container, dry bulk, tanker, "
            "and LNG segments. Data reflects latest available market prices "
            "and freight indices."
        ),
        badge_text="SECTOR",
        badge_color=C_ACCENT,
    )

    # Aggregate roll-ups for the KPI strip.
    ret30_values = [s.get("avg_return_30d") for s in sectors
                    if s.get("avg_return_30d") is not None]
    avg_30d = sum(ret30_values) / len(ret30_values) if ret30_values else None

    bullish = sum(1 for s in sectors if "Bullish" in s.get("outlook", ""))
    bearish = sum(1 for s in sectors if "Bearish" in s.get("outlook", ""))
    strong_mom = sum(1 for s in sectors if s.get("momentum") == "Strong")

    leader = (
        max(sectors, key=lambda s: s.get("avg_return_30d") or -1e9)
        if sectors else None
    )
    leader_name = leader.get("name", "--") if leader else "--"
    leader_ret = leader.get("avg_return_30d") if leader else None

    metric_card_row(
        [
            {
                "label":  "Sectors Tracked",
                "value":  f"{len(sectors)}",
                "accent": C_ACCENT,
                "sublabel": "Container · Dry Bulk · Tanker · LNG",
            },
            {
                "label":  "Avg 30-Day Return",
                "value":  _fmt_pct(avg_30d),
                "accent": _chg_color(avg_30d),
            },
            {
                "label":  "Leader (30d)",
                "value":  leader_name,
                "accent": C_HIGH,
                "sublabel": _fmt_pct(leader_ret),
            },
            {
                "label":  "Outlook Mix",
                "value":  f"{bullish}B / {bearish}Br",
                "accent": C_HIGH if bullish >= bearish else C_LOW,
                "sublabel": f"{strong_mom} w/ strong momentum",
            },
        ],
        columns=4,
    )
    st.markdown(source_footer([source]), unsafe_allow_html=True)


def _render_performance_table(sectors: list[dict], source: DataSource) -> None:
    """Ranked sector comparison table."""
    section_header(
        "Sector Performance",
        subtitle="Ranked by trailing returns, index level, and forward outlook",
    )

    ranked = sorted(
        sectors,
        key=lambda s: (s.get("avg_return_30d") if s.get("avg_return_30d")
                       is not None else -1e9),
        reverse=True,
    )

    rows = []
    for s in ranked:
        ret1 = s.get("avg_return_1d")
        ret5 = s.get("avg_return_5d")
        ret30 = s.get("avg_return_30d")
        idx_val = s.get("index_current")
        idx_chg = s.get("index_chg_1d")
        momentum = s.get("momentum", "N/A")
        outlook = s.get("outlook", "N/A")

        idx_str = f"{idx_val:,.0f}" if idx_val else "--"

        rows.append([
            _sector_name_cell(s["name"], s.get("description", "")),
            _mono(_fmt_pct(ret1),  color=_chg_color(ret1),   weight=600),
            _mono(_fmt_pct(ret5),  color=_chg_color(ret5),   weight=600),
            _mono(_fmt_pct(ret30), color=_chg_color(ret30),  weight=600),
            _mono(idx_str,         color=C_TEXT,             weight=500),
            _mono(_fmt_pct(idx_chg), color=_chg_color(idx_chg), weight=600),
            _sans(momentum, color=_momentum_color(momentum), weight=600),
            badge(outlook,  color=_outlook_color(outlook)),
        ])

    wsj_market_table(
        headers=[
            "Sector", "1-Day", "5-Day", "30-Day",
            "Index", "Index Chg", "Momentum", "Outlook",
        ],
        rows=rows,
    )
    st.markdown(source_footer([source]), unsafe_allow_html=True)


def _render_sector_profiles(sectors: list[dict], source: DataSource) -> None:
    """Per-sector detail cards (constituents, 30d return, outlook)."""
    if not sectors:
        return

    section_header(
        "Sector Profiles",
        subtitle="Key drivers and constituent equities",
    )

    cols = st.columns(len(sectors))
    for col, s in zip(cols, sectors):
        with col:
            ret30 = s.get("avg_return_30d")
            outlook = s.get("outlook", "N/A")
            key_driver = s.get("key_driver", "")
            rationale = (
                f"{key_driver} 30-day return: {_fmt_pct(ret30)}."
                if key_driver else f"30-day return: {_fmt_pct(ret30)}."
            )

            st.markdown(
                insight_card_html(
                    title=s["name"],
                    score=_ret_to_score(ret30),
                    action=_outlook_to_action(outlook),
                    rationale=rationale,
                    category="SECTOR",
                ),
                unsafe_allow_html=True,
            )

            # Constituent equities — render as a compact WSJ table per column
            # so each price row reuses shared CSS instead of inline divs.
            stock_prices = s.get("stock_prices", []) or []
            if stock_prices:
                price_rows = [
                    [
                        _sans(p["ticker"], color=C_TEXT, weight=700),
                        _mono(f"${p['price']:.2f}", color=C_TEXT2, weight=500),
                    ]
                    for p in stock_prices
                ]
                wsj_market_table(["Ticker", "Price"], price_rows)
            else:
                st.markdown(
                    f'<div style="font-family:var(--sans);font-size:0.74rem;'
                    f'color:{C_TEXT3};padding:8px 0;border-top:1px solid var(--rule);'
                    f'margin-top:6px;">No tracked equities for this segment.</div>',
                    unsafe_allow_html=True,
                )

    st.markdown(source_footer([source]), unsafe_allow_html=True)


def _render_trade_flows(trade_data: dict, port_results: list,
                        source: DataSource) -> None:
    """Global trade flow regional breakdown (optional, only if port data)."""
    try:
        from processing.sector_dashboard import compute_trade_flow_summary
    except ImportError:
        return

    summary = compute_trade_flow_summary(trade_data or {}, port_results)

    section_header(
        "Global Trade Flows",
        subtitle="Regional breakdown of tracked trade volumes",
    )

    narrative = summary.get("narrative", "")
    if narrative:
        st.markdown(narrative)

    regions = summary.get("regions", {})
    active = [(k, v) for k, v in regions.items() if v.get("ports")]
    active.sort(key=lambda x: x[1].get("total_trade", 0), reverse=True)

    if not active:
        return

    rows = []
    for region_name, rd in active:
        trade_val = rd.get("total_trade", 0)
        share = rd.get("share_pct", 0)
        n_ports = len(rd.get("ports", []))

        if trade_val >= 1e9:
            trade_str = f"${trade_val / 1e9:.1f}B"
        elif trade_val >= 1e6:
            trade_str = f"${trade_val / 1e6:.0f}M"
        elif trade_val > 0:
            trade_str = f"${trade_val:,.0f}"
        else:
            trade_str = "--"

        # Inline share bar (content-only, table frame handled by wsj CSS).
        bar_width = min(share, 100)
        bar_html = (
            f'<span style="display:inline-flex;align-items:center;gap:6px;'
            f'justify-content:flex-end;">'
            f'<span style="width:60px;height:3px;'
            f'background:rgba(232,230,225,0.08);border-radius:2px;'
            f'overflow:hidden;display:inline-block;">'
            f'<span style="height:100%;width:{bar_width}%;'
            f'background:{C_ACCENT};border-radius:2px;display:block;">'
            f'</span></span>'
            f'<span style="font-family:var(--mono);font-size:0.78rem;'
            f'color:{C_TEXT2};min-width:36px;text-align:right;">'
            f'{share:.0f}%</span>'
            f'</span>'
        )

        rows.append([
            _sans(region_name, color=C_TEXT, weight=700),
            _mono(f"{n_ports}", color=C_TEXT2, weight=500),
            _mono(trade_str,    color=C_TEXT,  weight=600),
            bar_html,
        ])

    wsj_market_table(
        headers=["Region", "Ports", "Trade Volume", "Share"],
        rows=rows,
    )
    st.markdown(source_footer([source]), unsafe_allow_html=True)


# ── Public entry point ──────────────────────────────────────────────────────

def render(stock_data=None, freight_data=None, trade_data=None,
           port_results=None, **kwargs) -> None:
    """Render the Shipping Sector Dashboard."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('sector'):
        try:
            from processing.sector_dashboard import compute_sector_performance
        except ImportError as e:
            st.error(f"Sector dashboard module not available: {e}")
            return

        stock_data = stock_data or {}
        freight_data = freight_data or {}
        port_results = port_results or []

        try:
            sectors = compute_sector_performance(stock_data, freight_data)
        except Exception:
            logger.exception("compute_sector_performance failed")
            st.error("Sector performance computation failed.")
            return

        # Provenance: equity prices are live/scraped via yfinance; freight indices
        # are scraped from Baltic/SCFI feeds; the downstream composition is
        # therefore a modeled aggregate.
        equities_source = DataSource.scraped(
            "Equity prices (yfinance composite)",
            notes="Aggregated from per-ticker feeds",
        )
        sector_source = DataSource.modeled(
            "Sector performance composite",
            notes="Aggregated returns + freight indices",
        )
        trade_source = DataSource.modeled(
            "Port trade volumes",
            notes="Aggregated from port monitor results",
        )

        _render_hero(sectors, sector_source)

        section_divider("Comparative Performance")
        _render_performance_table(sectors, sector_source)

        section_divider("Segment Detail")
        _render_sector_profiles(sectors, equities_source)

        if port_results:
            section_divider("Trade Flows")
            _render_trade_flows(trade_data or {}, port_results, trade_source)
