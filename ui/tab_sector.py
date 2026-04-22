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
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_RULE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    badge,
    live_data_badge,
    metric_card_row,
    page_header,
    section_header,
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


def _outlook_color(outlook: str) -> str:
    """Map an outlook label to a palette color (handles partial matches)."""
    for key, color in _OUTLOOK_COLOR.items():
        if key in outlook:
            return color
    return C_TEXT3


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
    """Two-line sector cell: serif name + sans description."""
    desc = (description or "")[:50]
    return (
        f'<div>'
        f'<div style="font-family:var(--serif);font-size:0.84rem;'
        f'font-weight:700;color:{C_TEXT};">{name}</div>'
        f'<div style="font-family:var(--sans);font-size:0.68rem;'
        f'color:{C_TEXT3};margin-top:2px;">{desc}</div>'
        f'</div>'
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
    )

    st.html(
        f'<div style="margin-bottom:14px;">{live_data_badge(source)}</div>'
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


def _render_performance_table(sectors: list[dict], source: DataSource) -> None:
    """Ranked sector comparison table."""
    section_header(
        "Sector Performance",
        subtitle="Ranked by trailing returns, index level, and forward outlook",
    )
    st.html(
        f'<div style="margin-bottom:10px;">{live_data_badge(source)}</div>'
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


def _render_sector_profiles(sectors: list[dict], source: DataSource) -> None:
    """Per-sector detail cards (constituents, 30d return, outlook)."""
    if not sectors:
        return

    section_header(
        "Sector Profiles",
        subtitle="Key drivers and constituent equities",
    )
    st.html(
        f'<div style="margin-bottom:10px;">{live_data_badge(source)}</div>'
    )

    cols = st.columns(len(sectors))
    for col, s in zip(cols, sectors):
        with col:
            olc = _outlook_color(s.get("outlook", "N/A"))
            ret30 = s.get("avg_return_30d")
            ret30_color = _chg_color(ret30)

            # Constituent price rows.
            price_rows_html = ""
            for p in s.get("stock_prices", []):
                price_rows_html += (
                    f'<div style="display:flex;justify-content:space-between;'
                    f'padding:3px 0;border-bottom:1px dotted rgba(232,230,225,0.05);">'
                    f'<span style="font-family:var(--sans);font-size:0.76rem;'
                    f'font-weight:600;color:{C_TEXT};">{p["ticker"]}</span>'
                    f'<span style="font-family:var(--mono);font-size:0.76rem;'
                    f'color:{C_TEXT2};">${p["price"]:.2f}</span>'
                    f'</div>'
                )
            if not price_rows_html:
                price_rows_html = (
                    f'<div style="font-size:0.76rem;color:{C_TEXT3};'
                    f'padding:4px 0;font-family:var(--sans);">'
                    f'No tracked equities</div>'
                )

            # Card body.
            st.html(
                f'<div style="border:1px solid {C_RULE};'
                f'border-top:2px solid {olc};'
                f'border-radius:0 0 6px 6px;padding:16px;background:{C_CARD};">'
                f'<div style="font-family:var(--serif);font-size:0.95rem;'
                f'font-weight:700;color:{C_TEXT};margin-bottom:4px;">'
                f'{s["name"]}</div>'
                f'<div style="font-family:var(--sans);font-size:0.72rem;'
                f'color:{C_TEXT3};margin-bottom:10px;line-height:1.4;">'
                f'{s.get("key_driver", "")}</div>'
                f'<div class="sub-section-header" style="margin-bottom:6px;">'
                f'30d Return</div>'
                f'<div style="font-family:var(--mono);font-size:1.15rem;'
                f'font-weight:700;color:{ret30_color};margin-bottom:10px;">'
                f'{_fmt_pct(ret30)}</div>'
                f'{price_rows_html}'
                f'<div style="margin-top:10px;text-align:center;">'
                f'{badge(s.get("outlook", "N/A"), color=olc)}'
                f'</div>'
                f'</div>'
            )


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
    st.html(
        f'<div style="margin-bottom:10px;">{live_data_badge(source)}</div>'
    )

    narrative = summary.get("narrative", "")
    if narrative:
        st.html(
            f'<div style="font-family:var(--sans);font-size:0.88rem;'
            f'color:{C_TEXT2};line-height:1.65;margin-bottom:16px;'
            f'max-width:720px;">{narrative}</div>'
        )

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


# ── Public entry point ──────────────────────────────────────────────────────

def render(stock_data=None, freight_data=None, trade_data=None,
           port_results=None, **kwargs) -> None:
    """Render the Shipping Sector Dashboard."""
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
    _render_performance_table(sectors, sector_source)
    _render_sector_profiles(sectors, equities_source)

    if port_results:
        _render_trade_flows(trade_data or {}, port_results, trade_source)
