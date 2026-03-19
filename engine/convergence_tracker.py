"""engine/convergence_tracker.py

Detect when multiple independent shipping signals agree on the same
direction, amplifying conviction and surfacing the highest-quality trades.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pandas as pd
from loguru import logger


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

# Direction threshold defaults
_BULLISH_THRESHOLD = 0.60
_BEARISH_THRESHOLD = 0.40


# ── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class ConvergenceEvent:
    """A convergence event: multiple signals agreeing on the same direction."""

    event_id: str
    timestamp: str
    converging_signals: list[str]   # human-readable names of the agreeing signals
    n_signals: int
    consensus_direction: str        # "BULLISH" | "BEARISH"
    consensus_strength: float       # avg signal strength of agreeing signals [0, 1]
    affected_entity: str            # route_id or port LOCODE
    entity_name: str
    composite_score: float          # base score + convergence bonus
    conviction_level: str           # "VERY_HIGH" | "HIGH" | "MODERATE"
    time_to_act: str                # "IMMEDIATE" | "THIS_WEEK" | "THIS_MONTH"
    description: str                # human-readable explanation


# ── Private helpers ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _bdi_direction(macro_data: dict[str, pd.DataFrame]) -> tuple[float, str]:
    """Return (normalised BDI score [0,1], direction string)."""
    try:
        from data.fred_feed import compute_bdi_score
        score = float(compute_bdi_score(macro_data))
    except Exception:
        score = 0.5
    direction = (
        "bullish" if score >= _BULLISH_THRESHOLD
        else "bearish" if score <= _BEARISH_THRESHOLD
        else "neutral"
    )
    return score, direction


def _seasonal_score(macro_data: dict[str, pd.DataFrame]) -> tuple[float, str]:
    """Rough seasonal signal derived from retail sales trend."""
    df = macro_data.get("MRTSSM44000USS")
    if df is None or df.empty or "value" not in df.columns:
        return 0.5, "neutral"
    vals = df["value"].dropna()
    if len(vals) < 13:
        return 0.5, "neutral"
    current = float(vals.iloc[-1])
    year_ago = float(vals.iloc[-13])
    if year_ago == 0:
        return 0.5, "neutral"
    yoy = (current - year_ago) / abs(year_ago)
    score = min(1.0, max(0.0, 0.5 + yoy * 2.0))
    direction = (
        "bullish" if score >= _BULLISH_THRESHOLD
        else "bearish" if score <= _BEARISH_THRESHOLD
        else "neutral"
    )
    return score, direction


def _macro_score_from_data(macro_data: dict[str, pd.DataFrame]) -> tuple[float, str]:
    """Composite macro signal: industrial production + BDI + inventory."""
    try:
        from data.fred_feed import compute_bdi_score
        bdi = float(compute_bdi_score(macro_data))
    except Exception:
        bdi = 0.5

    ipman_df = macro_data.get("IPMAN")
    if ipman_df is not None and not ipman_df.empty and "value" in ipman_df.columns:
        vals = ipman_df["value"].dropna()
        if len(vals) >= 2:
            current = float(vals.iloc[-1])
            avg = float(vals.tail(90).mean())
            pmi_proxy = min(1.0, max(0.0, (current / avg - 0.9) / 0.2)) if avg > 0 else 0.5
        else:
            pmi_proxy = 0.5
    else:
        pmi_proxy = 0.5

    try:
        from processing.inventory_analyzer import get_inventory_score_for_engine
        inv = float(get_inventory_score_for_engine(macro_data))
    except Exception:
        inv = 0.5

    score = 0.40 * pmi_proxy + 0.35 * bdi + 0.25 * inv
    direction = (
        "bullish" if score >= _BULLISH_THRESHOLD
        else "bearish" if score <= _BEARISH_THRESHOLD
        else "neutral"
    )
    return score, direction


def _conviction(n_signals: int) -> str:
    if n_signals >= 5:
        return "VERY_HIGH"
    if n_signals >= 4:
        return "HIGH"
    return "MODERATE"


def _time_to_act(conviction: str, score: float) -> str:
    if conviction == "VERY_HIGH" or score > 0.80:
        return "IMMEDIATE"
    if conviction == "HIGH":
        return "THIS_WEEK"
    return "THIS_MONTH"


# ── Public API ───────────────────────────────────────────────────────────────

def detect_convergence(
    port_results: list,
    route_results: list,
    macro_data: dict[str, pd.DataFrame],
    freight_data: dict[str, pd.DataFrame] | None = None,
    min_signals: int = 3,
) -> list[ConvergenceEvent]:
    """Detect convergence events across routes and ports.

    For each route/port combination, collects available signal values and
    checks whether >= min_signals all point in the same direction.

    Args:
        port_results:  list[PortDemandResult] from ports.demand_analyzer
        route_results: list[RouteOpportunity] from routes.optimizer
        macro_data:    dict series_id → DataFrame from fred_feed
        freight_data:  optional freight DataFrames (reserved for future use)
        min_signals:   minimum number of agreeing signals required (default 3)

    Returns:
        list[ConvergenceEvent] sorted by composite_score descending.
    """
    events: list[ConvergenceEvent] = []
    now = _now_iso()

    # Pre-compute shared macro signals (same for every route/port)
    bdi_score, bdi_dir    = _bdi_direction(macro_data)
    macro_score, macro_dir = _macro_score_from_data(macro_data)
    seasonal_score, seasonal_dir = _seasonal_score(macro_data)

    # Build a fast port lookup
    port_by_locode = {p.locode: p for p in port_results}

    # ── Evaluate each route ───────────────────────────────────────────────────
    for route in route_results:
        dest_port = port_by_locode.get(route.dest_locode)
        origin_port = port_by_locode.get(route.origin_locode)

        # ── Collect candidate signals ─────────────────────────────────────────
        candidates: list[tuple[str, float, str]] = []
        # (signal_name, normalized_value [0,1], direction)

        # 1. Destination demand
        if dest_port is not None:
            ds = float(getattr(dest_port, "demand_score", 0.5))
            d_dir = (
                "bullish" if ds >= _BULLISH_THRESHOLD
                else "bearish" if ds <= _BEARISH_THRESHOLD
                else "neutral"
            )
            candidates.append((f"{dest_port.port_name} Demand", ds, d_dir))

        # 2. Origin congestion clearance (low congestion = bullish for departure)
        if origin_port is not None:
            orig_cong = float(getattr(origin_port, "congestion_index", 0.5))
            cong_clear = 1.0 - orig_cong   # inverted: clear port = bullish
            cc_dir = (
                "bullish" if cong_clear >= _BULLISH_THRESHOLD
                else "bearish" if cong_clear <= _BEARISH_THRESHOLD
                else "neutral"
            )
            candidates.append(("Origin Congestion Clearance", cong_clear, cc_dir))

        # 3. Route rate momentum component
        rmc = float(getattr(route, "rate_momentum_component", 0.5))
        rmc_dir = (
            "bullish" if rmc >= _BULLISH_THRESHOLD
            else "bearish" if rmc <= _BEARISH_THRESHOLD
            else "neutral"
        )
        candidates.append(("Rate Momentum", rmc, rmc_dir))

        # 4. BDI / macro direction
        candidates.append(("Baltic Dry Index", bdi_score, bdi_dir))

        # 5. Composite macro environment
        candidates.append(("Macro Environment", macro_score, macro_dir))

        # 6. Seasonal demand signal
        candidates.append(("Seasonal Demand", seasonal_score, seasonal_dir))

        # ── Determine dominant direction ──────────────────────────────────────
        bullish_sigs = [(n, v) for n, v, d in candidates if d == "bullish"]
        bearish_sigs = [(n, v) for n, v, d in candidates if d == "bearish"]

        for direction, agreeing in [("BULLISH", bullish_sigs), ("BEARISH", bearish_sigs)]:
            if len(agreeing) < min_signals:
                continue

            n_sig = len(agreeing)
            names = [n for n, _ in agreeing]
            avg_strength = float(sum(v for _, v in agreeing) / n_sig)

            # Base score from route opportunity + destination demand
            dest_demand = float(getattr(dest_port, "demand_score", 0.5)) if dest_port else 0.5
            route_score = float(getattr(route, "opportunity_score", 0.5))
            base_score  = 0.40 * dest_demand + 0.35 * route_score + 0.25 * macro_score

            # +0.08 bonus per agreeing signal beyond the minimum (3)
            bonus = 0.08 * max(0, n_sig - 3)
            composite = min(1.0, base_score + bonus)

            # For bearish events invert from opportunity perspective
            if direction == "BEARISH":
                composite = min(1.0, (1.0 - base_score) + bonus)

            conviction = _conviction(n_sig)
            tta        = _time_to_act(conviction, composite)

            direction_word = "bullish" if direction == "BULLISH" else "bearish"
            signal_list    = ", ".join(names)
            description = (
                f"{n_sig} independent signals are aligned {direction_word} on "
                f"{route.route_name}: [{signal_list}]. "
                f"Average signal strength: {avg_strength:.0%}. "
                f"Composite conviction score: {composite:.0%}. "
                f"Multi-signal confirmation significantly raises confidence — "
                f"independent data sources rarely agree by chance."
            )

            entity_id = route.route_id
            entity_name = route.route_name
            if dest_port is not None:
                entity_id   = f"{route.route_id}|{dest_port.locode}"
                entity_name = f"{route.route_name} → {dest_port.port_name}"

            event = ConvergenceEvent(
                event_id=str(uuid.uuid4())[:8],
                timestamp=now,
                converging_signals=names,
                n_signals=n_sig,
                consensus_direction=direction,
                consensus_strength=avg_strength,
                affected_entity=entity_id,
                entity_name=entity_name,
                composite_score=composite,
                conviction_level=conviction,
                time_to_act=tta,
                description=description,
            )
            events.append(event)
            logger.info(
                f"Convergence detected: {entity_name} | {direction} | "
                f"n={n_sig} | score={composite:.3f} | {conviction}"
            )
            break  # one event per route (dominant direction wins)

    # ── Also scan standalone port demand convergence ──────────────────────────
    for port in port_results:
        if not getattr(port, "has_real_data", True):
            continue

        ds  = float(getattr(port, "demand_score", 0.5))
        tf  = float(getattr(port, "trade_flow_component", 0.5))
        cng = float(getattr(port, "congestion_component", 0.5))
        tpt = float(getattr(port, "throughput_component", 0.5))

        port_candidates: list[tuple[str, float, str]] = [
            ("Trade Flow",   tf,  "bullish" if tf  >= _BULLISH_THRESHOLD else ("bearish" if tf  <= _BEARISH_THRESHOLD else "neutral")),
            ("Congestion",   cng, "bullish" if cng >= _BULLISH_THRESHOLD else ("bearish" if cng <= _BEARISH_THRESHOLD else "neutral")),
            ("Throughput",   tpt, "bullish" if tpt >= _BULLISH_THRESHOLD else ("bearish" if tpt <= _BEARISH_THRESHOLD else "neutral")),
            ("Macro Env",    macro_score, macro_dir),
            ("BDI",          bdi_score,   bdi_dir),
            ("Seasonal",     seasonal_score, seasonal_dir),
        ]

        for direction, filter_dir in [("BULLISH", "bullish"), ("BEARISH", "bearish")]:
            agreeing = [(n, v) for n, v, d in port_candidates if d == filter_dir]
            if len(agreeing) < min_signals:
                continue

            # Skip if this port is already covered by a route event
            already_covered = any(
                port.locode in e.affected_entity for e in events
            )
            if already_covered:
                break

            n_sig        = len(agreeing)
            names        = [n for n, _ in agreeing]
            avg_strength = float(sum(v for _, v in agreeing) / n_sig)

            base_score  = ds if direction == "BULLISH" else (1.0 - ds)
            bonus       = 0.08 * max(0, n_sig - 3)
            composite   = min(1.0, base_score + 0.10 + bonus)
            conviction  = _conviction(n_sig)
            tta         = _time_to_act(conviction, composite)

            direction_word = "bullish" if direction == "BULLISH" else "bearish"
            signal_list    = ", ".join(names)
            description = (
                f"{n_sig} independent signals are aligned {direction_word} on "
                f"{port.port_name} ({port.locode}): [{signal_list}]. "
                f"Average signal strength: {avg_strength:.0%}. "
                f"Port demand score: {ds:.0%}. "
                f"Composite conviction: {composite:.0%}."
            )

            events.append(ConvergenceEvent(
                event_id=str(uuid.uuid4())[:8],
                timestamp=now,
                converging_signals=names,
                n_signals=n_sig,
                consensus_direction=direction,
                consensus_strength=avg_strength,
                affected_entity=port.locode,
                entity_name=port.port_name,
                composite_score=composite,
                conviction_level=conviction,
                time_to_act=tta,
                description=description,
            ))
            logger.info(
                f"Port convergence: {port.port_name} | {direction} | "
                f"n={n_sig} | score={composite:.3f}"
            )
            break

    events.sort(key=lambda e: e.composite_score, reverse=True)
    logger.info(f"ConvergenceTracker: {len(events)} events detected")
    return events


def get_highest_conviction_trades(
    events: list[ConvergenceEvent],
    n: int = 3,
) -> list[ConvergenceEvent]:
    """Return the top-n convergence events by composite_score.

    Args:
        events: Full list from detect_convergence (already sorted desc).
        n:      Number of top events to return.

    Returns:
        list[ConvergenceEvent] of length ≤ n.
    """
    return sorted(events, key=lambda e: e.composite_score, reverse=True)[:n]


# ── Streamlit renderer ───────────────────────────────────────────────────────

def render_convergence_dashboard(events: list[ConvergenceEvent]) -> None:
    """Render a dramatic convergence dashboard in Streamlit.

    Empty state: neutral advisory message.
    Events: full conviction cards with signal icons, badges, and time-to-act.
    """
    try:
        import streamlit as st
    except ImportError:
        logger.error("streamlit not installed — cannot render dashboard")
        return

    # ── CSS ───────────────────────────────────────────────────────────────────
    st.markdown(
        """
        <style>
        @keyframes pulse-border {
            0%   { box-shadow: 0 0 0 0   rgba(239,68,68,0.6); }
            70%  { box-shadow: 0 0 0 10px rgba(239,68,68,0);   }
            100% { box-shadow: 0 0 0 0   rgba(239,68,68,0);   }
        }
        .conv-card {
            background: #1a2235;
            border-radius: 12px;
            padding: 20px 24px;
            margin-bottom: 18px;
            border: 1px solid rgba(255,255,255,0.08);
        }
        .conv-card-immediate {
            border: 2px solid #ef4444;
            animation: pulse-border 2s ease-in-out infinite;
        }
        .conv-header {
            font-size: 0.65rem; font-weight: 800;
            letter-spacing: 0.14em; color: #8b5cf6;
            margin-bottom: 6px;
        }
        .conv-title {
            font-size: 1.15rem; font-weight: 700;
            color: #f1f5f9; margin-bottom: 10px;
        }
        .conv-badge {
            display:inline-block; font-size:0.65rem; font-weight:700;
            padding:3px 9px; border-radius:99px; letter-spacing:0.06em;
            margin-right:6px;
        }
        .conv-signals {
            display:flex; flex-wrap:wrap; gap:6px; margin:10px 0;
        }
        .conv-signal-chip {
            font-size:0.68rem; padding:3px 8px; border-radius:6px;
            background:rgba(255,255,255,0.06); color:#94a3b8;
        }
        .conv-desc {
            font-size:0.82rem; color:#94a3b8; line-height:1.55;
            margin-top:10px; border-top:1px solid rgba(255,255,255,0.06);
            padding-top:10px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ── Empty state ───────────────────────────────────────────────────────────
    if not events:
        st.markdown(
            '<div class="conv-card" style="text-align:center;padding:36px;">'
            '<div style="font-size:2rem;margin-bottom:12px;">📡</div>'
            '<div style="font-size:1rem;font-weight:600;color:#f1f5f9;margin-bottom:8px;">'
            'No convergence signals detected.'
            '</div>'
            '<div style="font-size:0.85rem;color:#94a3b8;">'
            'Markets are mixed — wait for clearer directional signals.'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # ── Signal icons ──────────────────────────────────────────────────────────
    _SIGNAL_ICONS: dict[str, str] = {
        "Rate Momentum":              "📈",
        "Baltic Dry Index":           "🚢",
        "Macro Environment":          "🌐",
        "Seasonal Demand":            "📅",
        "Trade Flow":                 "💹",
        "Congestion":                 "⚓",
        "Throughput":                 "📦",
        "Origin Congestion Clearance":"🟢",
        "BDI":                        "🚢",
        "Macro Env":                  "🌐",
        "Seasonal":                   "📅",
    }

    def _icon(name: str) -> str:
        for key, icon in _SIGNAL_ICONS.items():
            if key.lower() in name.lower():
                return icon
        return "🔹"

    # ── Conviction badge colors ───────────────────────────────────────────────
    _CONVICTION_COLORS = {
        "VERY_HIGH": (_C_HIGH,   "#d1fae5"),
        "HIGH":      (_C_ACCENT, "#dbeafe"),
        "MODERATE":  (_C_MOD,    "#fef3c7"),
    }
    _DIRECTION_COLORS = {
        "BULLISH": (_C_HIGH, "BULLISH ↑"),
        "BEARISH": (_C_LOW,  "BEARISH ↓"),
    }
    _TTA_COLORS = {
        "IMMEDIATE":  (_C_LOW,   "IMMEDIATE"),
        "THIS_WEEK":  (_C_MOD,   "THIS WEEK"),
        "THIS_MONTH": (_C_ACCENT, "THIS MONTH"),
    }

    # ── Render each event ─────────────────────────────────────────────────────
    for event in events:
        is_immediate = event.time_to_act == "IMMEDIATE"
        extra_class  = "conv-card-immediate" if is_immediate else ""

        dir_color, dir_label = _DIRECTION_COLORS.get(
            event.consensus_direction, (_C_TEXT2, event.consensus_direction)
        )
        conv_color, _ = _CONVICTION_COLORS.get(event.conviction_level, (_C_TEXT2, ""))
        tta_color, tta_label = _TTA_COLORS.get(event.time_to_act, (_C_ACCENT, event.time_to_act))

        signal_chips = "".join(
            f'<span class="conv-signal-chip">{_icon(s)} {s}</span>'
            for s in event.converging_signals
        )

        score_pct = f"{event.composite_score:.0%}"
        strength_pct = f"{event.consensus_strength:.0%}"

        card_html = (
            f'<div class="conv-card {extra_class}">'
            # Header line
            f'  <div class="conv-header">'
            f'    ⚡ CONVERGENCE DETECTED &nbsp;|&nbsp; '
            f'    {event.n_signals} SIGNALS ALIGNED'
            f'  </div>'
            # Entity name
            f'  <div class="conv-title">{event.entity_name}</div>'
            # Badges row
            f'  <div>'
            f'    <span class="conv-badge" '
            f'          style="background:{dir_color}22;color:{dir_color};">'
            f'      {dir_label}'
            f'    </span>'
            f'    <span class="conv-badge" '
            f'          style="background:{conv_color}22;color:{conv_color};">'
            f'      {event.conviction_level.replace("_"," ")} CONVICTION'
            f'    </span>'
            f'    <span class="conv-badge" '
            f'          style="background:{tta_color}22;color:{tta_color};">'
            f'      {tta_label}'
            f'    </span>'
            f'    <span class="conv-badge" '
            f'          style="background:rgba(255,255,255,0.05);color:#94a3b8;">'
            f'      Score: {score_pct} &nbsp;|&nbsp; Avg strength: {strength_pct}'
            f'    </span>'
            f'  </div>'
            # Signal chips
            f'  <div class="conv-signals">{signal_chips}</div>'
            # Description
            f'  <div class="conv-desc">{event.description}</div>'
            f'</div>'
        )
        st.markdown(card_html, unsafe_allow_html=True)
