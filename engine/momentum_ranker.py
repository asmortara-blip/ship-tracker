"""engine/momentum_ranker.py

Rank all shipping assets (routes, ports, stocks) by momentum across
multiple timeframes and produce a leaderboard for the Streamlit UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

if TYPE_CHECKING:
    pass

# ── Color palette (mirrors ui/components.py) ────────────────────────────────
_C_HIGH   = "#10b981"
_C_MOD    = "#f59e0b"
_C_LOW    = "#ef4444"
_C_CARD   = "#1a2235"
_C_TEXT   = "#f1f5f9"
_C_TEXT2  = "#94a3b8"
_C_ACCENT = "#3b82f6"
_C_CONV   = "#8b5cf6"
_C_MACRO  = "#06b6d4"


# ── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class MomentumRank:
    """Momentum ranking for a single shipping asset."""

    entity_id: str          # route_id | port LOCODE | ticker symbol
    entity_type: str        # "route" | "port" | "stock"
    entity_name: str

    momentum_7d: float      # % change over  7 days  (e.g. 0.08 = +8 %)
    momentum_30d: float     # % change over 30 days
    momentum_90d: float     # % change over 90 days
    momentum_composite: float  # 0.2*7d + 0.4*30d + 0.4*90d

    rank_overall: int       # 1 = strongest momentum across all types
    rank_in_category: int   # rank within entity_type

    regime: str             # "ACCELERATING" | "DECELERATING" | "SUSTAINED" | "REVERSING"
    signal: str             # "STRONG_BUY" | "BUY" | "NEUTRAL" | "SELL" | "STRONG_SELL"


# ── Private helpers ──────────────────────────────────────────────────────────

def _composite(m7: float, m30: float, m90: float) -> float:
    return 0.2 * m7 + 0.4 * m30 + 0.4 * m90


def _regime(m7: float, m30: float, m90: float) -> str:
    """Classify the momentum regime from three timeframe readings."""
    if m7 > m30 > m90:
        return "ACCELERATING"
    if m7 < m30 < m90:
        return "DECELERATING"
    # "close" means all three within 5 percentage-points of each other
    spread = max(m7, m30, m90) - min(m7, m30, m90)
    if spread <= 0.05:
        return "SUSTAINED"
    return "REVERSING"


def _signal(composite: float) -> str:
    if composite > 0.15:
        return "STRONG_BUY"
    if composite > 0.05:
        return "BUY"
    if composite > -0.05:
        return "NEUTRAL"
    if composite > -0.15:
        return "SELL"
    return "STRONG_SELL"


def _pct_change_from_df(df: pd.DataFrame, days: int) -> float:
    """Return % change of the 'close' column over `days` calendar days.

    Returns 0.0 if the DataFrame is too short or data is unavailable.
    """
    if df is None or df.empty or "close" not in df.columns:
        return 0.0
    closes = df["close"].dropna()
    if len(closes) < 2:
        return 0.0

    # Approximate: assume one row per trading day
    n = min(days, len(closes) - 1)
    if n <= 0:
        return 0.0
    old = closes.iloc[-(n + 1)]
    new = closes.iloc[-1]
    if old == 0:
        return 0.0
    return float((new - old) / abs(old))


# ── Route momentum extraction ────────────────────────────────────────────────

def _route_momentum(route) -> tuple[float, float, float]:
    """Extract 7d / 30d / 90d momentum from a RouteOpportunity object.

    Prefer explicit rate_pct_change fields; fall back to rate_momentum_component
    for an approximation of the shorter timeframes.
    """
    m30 = float(getattr(route, "rate_pct_change_30d", 0.0) or 0.0)
    m7  = float(getattr(route, "rate_pct_change_7d",  0.0) or 0.0)
    m90 = float(getattr(route, "rate_pct_change_90d", 0.0) or 0.0)

    # If 7d / 90d are missing, approximate from rate_momentum_component
    if m7 == 0.0:
        rmc = float(getattr(route, "rate_momentum_component", 0.5) or 0.5)
        m7  = (rmc - 0.5) * 0.4   # map [0,1] → approx ±20 %
    if m90 == 0.0:
        m90 = m30 * 0.6            # rough decay heuristic

    return m7, m30, m90


# ── Port momentum extraction ─────────────────────────────────────────────────

def _port_momentum(port) -> tuple[float, float, float]:
    """Approximate port momentum from demand_score and its sub-components.

    Ports don't carry explicit pct-change fields, so we construct a proxy
    from the composite demand_score relative to a neutral mid-point (0.5).
    The 7d / 30d / 90d differentiation is approximated by scaling.
    """
    ds = float(getattr(port, "demand_score", 0.5) or 0.5)
    trend = str(getattr(port, "demand_trend", "Stable")).lower()

    # Convert score to a rough % deviation from neutral
    m30 = (ds - 0.5) * 0.6   # maps [0,1] → [−30 %, +30 %]

    # Adjust 7d/90d based on stated trend direction
    if trend == "rising":
        m7  = m30 * 1.3
        m90 = m30 * 0.7
    elif trend == "falling":
        m7  = m30 * 0.7
        m90 = m30 * 1.3
    else:
        m7  = m30
        m90 = m30

    return float(m7), float(m30), float(m90)


# ── Public API ───────────────────────────────────────────────────────────────

def rank_all_momentum(
    route_results: list,
    port_results: list,
    stock_data: dict[str, pd.DataFrame],
    freight_data: dict[str, pd.DataFrame] | None = None,
) -> list[MomentumRank]:
    """Rank all shipping assets by composite momentum.

    Args:
        route_results:  list[RouteOpportunity] from routes.optimizer
        port_results:   list[PortDemandResult] from ports.demand_analyzer
        stock_data:     dict ticker → DataFrame (STOCK_COLS schema)
        freight_data:   optional; currently unused but accepted for future use

    Returns:
        list[MomentumRank] sorted by momentum_composite descending, with
        rank_overall and rank_in_category populated.
    """
    records: list[MomentumRank] = []

    # ── Routes ────────────────────────────────────────────────────────────────
    for route in route_results:
        m7, m30, m90 = _route_momentum(route)
        comp = _composite(m7, m30, m90)
        records.append(MomentumRank(
            entity_id=route.route_id,
            entity_type="route",
            entity_name=route.route_name,
            momentum_7d=m7,
            momentum_30d=m30,
            momentum_90d=m90,
            momentum_composite=comp,
            rank_overall=0,        # assigned below
            rank_in_category=0,    # assigned below
            regime=_regime(m7, m30, m90),
            signal=_signal(comp),
        ))

    # ── Ports ─────────────────────────────────────────────────────────────────
    for port in port_results:
        m7, m30, m90 = _port_momentum(port)
        comp = _composite(m7, m30, m90)
        records.append(MomentumRank(
            entity_id=port.locode,
            entity_type="port",
            entity_name=port.port_name,
            momentum_7d=m7,
            momentum_30d=m30,
            momentum_90d=m90,
            momentum_composite=comp,
            rank_overall=0,
            rank_in_category=0,
            regime=_regime(m7, m30, m90),
            signal=_signal(comp),
        ))

    # ── Stocks ────────────────────────────────────────────────────────────────
    for ticker, df in (stock_data or {}).items():
        m7  = _pct_change_from_df(df, 7)
        m30 = _pct_change_from_df(df, 30)
        m90 = _pct_change_from_df(df, 90)
        comp = _composite(m7, m30, m90)
        records.append(MomentumRank(
            entity_id=ticker,
            entity_type="stock",
            entity_name=ticker,
            momentum_7d=m7,
            momentum_30d=m30,
            momentum_90d=m90,
            momentum_composite=comp,
            rank_overall=0,
            rank_in_category=0,
            regime=_regime(m7, m30, m90),
            signal=_signal(comp),
        ))

    # ── Assign overall rank ───────────────────────────────────────────────────
    records.sort(key=lambda r: r.momentum_composite, reverse=True)
    for i, r in enumerate(records, start=1):
        r.rank_overall = i

    # ── Assign per-category rank ──────────────────────────────────────────────
    category_counts: dict[str, int] = {}
    for r in records:
        category_counts[r.entity_type] = category_counts.get(r.entity_type, 0) + 1

    # Walk in overall order; maintain a counter per category
    cat_rank: dict[str, int] = {}
    for r in records:
        cat_rank[r.entity_type] = cat_rank.get(r.entity_type, 0) + 1
        r.rank_in_category = cat_rank[r.entity_type]

    logger.info(
        f"MomentumRanker: {len(records)} entities ranked "
        f"({len(route_results)} routes, {len(port_results)} ports, "
        f"{len(stock_data or {})} stocks)"
    )
    return records


def get_top_momentum(
    ranks: list[MomentumRank],
    n: int = 5,
    entity_type: str | None = None,
) -> list[MomentumRank]:
    """Return top-n momentum entities, optionally filtered by entity_type.

    Args:
        ranks:       Full ranked list from rank_all_momentum.
        n:           Number of results to return.
        entity_type: "route" | "port" | "stock" | None (all types).

    Returns:
        list[MomentumRank] of length ≤ n, sorted by momentum_composite desc.
    """
    filtered = [r for r in ranks if entity_type is None or r.entity_type == entity_type]
    return filtered[:n]


# ── Streamlit renderer ───────────────────────────────────────────────────────

def render_momentum_leaderboard(ranks: list[MomentumRank]) -> None:
    """Render a Streamlit momentum leaderboard grouped by entity type.

    Displays colored momentum bars, signal badges, and gold/silver/bronze
    rank badges for the top three overall entries.
    """
    try:
        import streamlit as st
    except ImportError:
        logger.error("streamlit not installed — cannot render leaderboard")
        return

    if not ranks:
        st.info("No momentum data available.")
        return

    # ── CSS injection ─────────────────────────────────────────────────────────
    st.markdown(
        """
        <style>
        .mom-row {
            display:flex; align-items:center; gap:12px;
            background:#1a2235; border-radius:8px; padding:10px 14px;
            margin-bottom:6px; border:1px solid rgba(255,255,255,0.06);
        }
        .mom-rank  { font-size:1.1rem; font-weight:700; min-width:36px; text-align:center; }
        .mom-name  { flex:1; font-size:0.9rem; color:#f1f5f9; }
        .mom-badge {
            font-size:0.65rem; font-weight:700; padding:2px 7px;
            border-radius:99px; letter-spacing:0.05em;
        }
        .mom-pct   { font-size:0.8rem; font-weight:600; min-width:52px; text-align:right; }
        .mom-bar-wrap { width:80px; background:rgba(255,255,255,0.06);
                        border-radius:4px; height:8px; overflow:hidden; }
        .mom-bar   { height:100%; border-radius:4px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ── Helper: render one row ────────────────────────────────────────────────
    rank_badges = {1: ("🥇", "#fbbf24"), 2: ("🥈", "#94a3b8"), 3: ("🥉", "#d97706")}

    def _pct_color(v: float) -> str:
        if v > 0.02:
            return _C_HIGH
        if v < -0.02:
            return _C_LOW
        return _C_MOD

    def _signal_color(sig: str) -> str:
        return {
            "STRONG_BUY":  _C_HIGH,
            "BUY":         "#34d399",
            "NEUTRAL":     _C_MOD,
            "SELL":        "#f87171",
            "STRONG_SELL": _C_LOW,
        }.get(sig, _C_TEXT2)

    def _type_color(et: str) -> str:
        return {"route": _C_ACCENT, "port": _C_CONV, "stock": _C_MACRO}.get(et, _C_TEXT2)

    def _bar_html(composite: float) -> str:
        """Render a mini progress bar for composite momentum."""
        clamped = max(-0.30, min(0.30, composite))
        pct = int((clamped + 0.30) / 0.60 * 100)
        color = _pct_color(composite)
        return (
            f'<div class="mom-bar-wrap">'
            f'<div class="mom-bar" style="width:{pct}%;background:{color};"></div>'
            f'</div>'
        )

    def _row_html(r: MomentumRank) -> str:
        badge, _ = rank_badges.get(r.rank_overall, ("", ""))
        rank_str = f"{badge} {r.rank_overall}" if badge else str(r.rank_overall)

        type_clr  = _type_color(r.entity_type)
        sig_clr   = _signal_color(r.signal)
        m7_clr    = _pct_color(r.momentum_7d)
        m30_clr   = _pct_color(r.momentum_30d)
        m90_clr   = _pct_color(r.momentum_90d)

        return (
            f'<div class="mom-row">'
            f'  <span class="mom-rank">{rank_str}</span>'
            f'  <span class="mom-name">{r.entity_name}</span>'
            f'  <span class="mom-badge" style="background:{type_clr}22;color:{type_clr}">'
            f'    {r.entity_type.upper()}'
            f'  </span>'
            f'  <span class="mom-pct" style="color:{m7_clr}">{r.momentum_7d:+.1%}</span>'
            f'  <span class="mom-pct" style="color:{m30_clr}">{r.momentum_30d:+.1%}</span>'
            f'  <span class="mom-pct" style="color:{m90_clr}">{r.momentum_90d:+.1%}</span>'
            f'  {_bar_html(r.momentum_composite)}'
            f'  <span class="mom-badge" style="background:{sig_clr}22;color:{sig_clr}">'
            f'    {r.signal.replace("_", " ")}'
            f'  </span>'
            f'</div>'
        )

    # ── Header row ────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="display:flex;gap:12px;padding:4px 14px;'
        'font-size:0.7rem;color:#64748b;font-weight:600;letter-spacing:0.08em;">'
        '<span style="min-width:36px">RANK</span>'
        '<span style="flex:1">ENTITY</span>'
        '<span style="min-width:48px">TYPE</span>'
        '<span style="min-width:52px;text-align:right">7D</span>'
        '<span style="min-width:52px;text-align:right">30D</span>'
        '<span style="min-width:52px;text-align:right">90D</span>'
        '<span style="min-width:80px">COMPOSITE</span>'
        '<span>SIGNAL</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── Grouped sections ──────────────────────────────────────────────────────
    for group_type, label in [("route", "Routes"), ("port", "Ports"), ("stock", "Stocks")]:
        group = [r for r in ranks if r.entity_type == group_type]
        if not group:
            continue
        st.markdown(
            f'<div style="font-size:0.75rem;font-weight:700;color:#64748b;'
            f'letter-spacing:0.1em;margin:14px 0 6px 4px;">'
            f'{label.upper()}</div>',
            unsafe_allow_html=True,
        )
        html_rows = "".join(_row_html(r) for r in group)
        st.markdown(html_rows, unsafe_allow_html=True)
