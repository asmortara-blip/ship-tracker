"""Live Intelligence Feed tab — real-time dashboard with auto-refreshing data streams."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.styles import (
    C_BG, C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_MOD, C_LOW, C_ACCENT, C_CONV, C_MACRO,
    _hex_to_rgba, section_header,
)
from utils.helpers import now_iso

# ── Constants ─────────────────────────────────────────────────────────────────

_CACHE_DIR = Path(__file__).parent.parent / "cache"

# TTLs from config.yaml (hours converted to seconds)
_TTL_HOURS: dict[str, int] = {
    "comtrade":  168,
    "fred":       24,
    "worldbank": 168,
    "freight":    24,
    "ais":         6,
    "stocks":      1,
}

_SHIPPING_TICKERS = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]

# Priority ordering for feed items
_PRIORITY_CRITICAL = 0
_PRIORITY_HIGH     = 1
_PRIORITY_MODERATE = 2
_PRIORITY_LOW      = 3

# Color mapping for priority/category
_CAT_COLORS: dict[str, str] = {
    "CONVERGENCE": C_CONV,
    "ROUTE":       C_ACCENT,
    "PORT_DEMAND": C_HIGH,
    "MACRO":       C_MACRO,
    "RATE_ALERT":  C_LOW,
    "STOCK_MOVE":  "#f97316",
    "BDI":         C_MACRO,
}

_CAT_LABELS: dict[str, str] = {
    "CONVERGENCE": "CONVERGENCE",
    "ROUTE":       "ROUTE",
    "PORT_DEMAND": "PORT DEMAND",
    "MACRO":       "MACRO",
    "RATE_ALERT":  "RATE ALERT",
    "STOCK_MOVE":  "EQUITY",
    "BDI":         "BDI",
}

# ── Helpers ───────────────────────────────────────────────────────────────────


def _now_ts_str() -> str:
    """Current UTC timestamp for display, no backslash-dependent formatting."""
    dt = datetime.now(timezone.utc)
    return (
        str(dt.year) + "-"
        + str(dt.month).zfill(2) + "-"
        + str(dt.day).zfill(2) + "  "
        + str(dt.hour).zfill(2) + ":"
        + str(dt.minute).zfill(2) + ":"
        + str(dt.second).zfill(2)
        + " UTC"
    )


def _short_ts() -> str:
    """Short HH:MM UTC stamp."""
    dt = datetime.now(timezone.utc)
    return str(dt.hour).zfill(2) + ":" + str(dt.minute).zfill(2) + " UTC"


def _cache_mtimes() -> dict[str, float | None]:
    """Return mtime (unix seconds) for each data-source cache bucket, or None."""
    mtimes: dict[str, float | None] = {}
    if not _CACHE_DIR.exists():
        for k in _TTL_HOURS:
            mtimes[k] = None
        return mtimes

    # Map cache-bucket keywords to patterns
    patterns = {
        "comtrade":  "comtrade",
        "fred":      "fred",
        "worldbank": "worldbank",
        "freight":   "freight",
        "ais":       "ais",
        "stocks":    "stock",
    }
    for bucket, pat in patterns.items():
        matched: list[float] = []
        for f in _CACHE_DIR.rglob("*.parquet"):
            if pat in f.name.lower():
                try:
                    matched.append(f.stat().st_mtime)
                except OSError:
                    pass
        # Also check json/csv
        for ext in ("*.json", "*.csv"):
            for f in _CACHE_DIR.rglob(ext):
                if pat in f.name.lower():
                    try:
                        matched.append(f.stat().st_mtime)
                    except OSError:
                        pass
        mtimes[bucket] = max(matched) if matched else None
    return mtimes


def _source_age_str(mtime: float | None) -> str:
    """Human-readable age from mtime."""
    if mtime is None:
        return "never"
    age_s = time.time() - mtime
    if age_s < 60:
        return "just now"
    if age_s < 3600:
        mins = int(age_s / 60)
        return str(mins) + "m ago"
    if age_s < 86400:
        hrs = int(age_s / 3600)
        return str(hrs) + "h ago"
    days = int(age_s / 86400)
    return str(days) + "d ago"


def _source_status_color(mtime: float | None, ttl_hours: int) -> str:
    """Green if fresh, amber if approaching stale, red if overdue."""
    if mtime is None:
        return C_LOW
    age_s = time.time() - mtime
    ttl_s = ttl_hours * 3600
    if age_s <= ttl_s * 0.6:
        return C_HIGH
    if age_s <= ttl_s:
        return C_MOD
    return C_LOW


def _next_refresh_minutes(mtimes: dict[str, float | None]) -> int:
    """Minutes until the soonest cache TTL expires."""
    best = 9999
    now = time.time()
    for bucket, mtime in mtimes.items():
        ttl_s = _TTL_HOURS[bucket] * 3600
        if mtime is None:
            best = min(best, 0)
            continue
        remaining_s = ttl_s - (now - mtime)
        remaining_m = max(0, int(remaining_s / 60))
        best = min(best, remaining_m)
    return max(0, best)


def _pct_change_30d_df(df: pd.DataFrame) -> float | None:
    """30-day % change from a DataFrame with 'date' and 'value' columns."""
    if df is None or df.empty:
        return None
    needed = {"date", "value"}
    if not needed.issubset(df.columns):
        return None
    df2 = df.sort_values("date")
    vals = df2["value"].dropna()
    if len(vals) < 2:
        return None
    current = float(vals.iloc[-1])
    ref = df2["date"].max() - pd.Timedelta(days=30)
    mask = df2["date"] <= ref
    if not mask.any():
        return None
    ago = float(df2.loc[mask, "value"].dropna().iloc[-1])
    if ago == 0:
        return None
    return (current - ago) / abs(ago) * 100


def _day_change_pct(df: pd.DataFrame, col: str = "close") -> float | None:
    """Day-over-day % change from a stock DataFrame."""
    if df is None or df.empty or col not in df.columns:
        return None
    vals = df[col].dropna()
    if len(vals) < 2:
        return None
    cur = float(vals.iloc[-1])
    prev = float(vals.iloc[-2])
    if prev == 0:
        return None
    return (cur - prev) / abs(prev) * 100


def _demand_colorscale(score: float) -> str:
    """Map [0,1] demand score to a CSS hex color."""
    stops = [
        (0.00, (30,  58, 95)),
        (0.25, (59, 130, 246)),
        (0.50, (16, 185, 129)),
        (0.75, (245, 158, 11)),
        (1.00, (239, 68,  68)),
    ]
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t0 <= score <= t1:
            pct = (score - t0) / (t1 - t0)
            r = int(c0[0] + (c1[0] - c0[0]) * pct)
            g = int(c0[1] + (c1[1] - c0[1]) * pct)
            b = int(c0[2] + (c1[2] - c0[2]) * pct)
            return "#" + format(r, "02x") + format(g, "02x") + format(b, "02x")
    return C_HIGH


# ── Feed item dataclass (dict-based for simplicity) ───────────────────────────

def _make_feed_item(
    category: str,
    title: str,
    detail: str,
    priority: int,
    badge_text: str,
    badge_color: str,
    source: str,
    ts: str | None = None,
) -> dict[str, Any]:
    return {
        "category":    category,
        "title":       title,
        "detail":      detail,
        "priority":    priority,
        "badge_text":  badge_text,
        "badge_color": badge_color,
        "source":      source,
        "ts":          ts or _short_ts(),
        "color":       _CAT_COLORS.get(category, C_ACCENT),
    }


# ── Section 1: Live Status Bar ─────────────────────────────────────────────────

def _render_status_bar() -> None:
    """Full-width status bar with timestamp, source health, next refresh, and LIVE dot."""
    mtimes = _cache_mtimes()
    next_refresh = _next_refresh_minutes(mtimes)

    # Pulsing LIVE dot CSS (injected once — global CSS already has pulse-dot)
    live_dot = (
        '<span style="display:inline-block; width:9px; height:9px; border-radius:50%;'
        ' background:' + C_HIGH + '; margin-right:6px;'
        ' box-shadow:0 0 8px ' + C_HIGH + '; animation:pulse-dot 1.5s infinite">'
        '</span>'
    )

    # Source status chips
    source_chips = ""
    source_display = {
        "stocks":    "Stocks",
        "freight":   "Freight",
        "fred":      "Macro",
        "ais":       "AIS",
        "comtrade":  "Trade",
        "worldbank": "Ports",
    }
    for bucket, label in source_display.items():
        mtime = mtimes.get(bucket)
        color = _source_status_color(mtime, _TTL_HOURS[bucket])
        age   = _source_age_str(mtime)
        bg    = _hex_to_rgba(color, 0.12)
        border = _hex_to_rgba(color, 0.30)
        source_chips += (
            '<span style="display:inline-flex; align-items:center; gap:4px;'
            ' background:' + bg + '; color:' + color + ';'
            ' border:1px solid ' + border + ';'
            ' padding:2px 9px; border-radius:999px; font-size:0.65rem; font-weight:600;'
            ' white-space:nowrap">'
            '<span style="width:6px; height:6px; border-radius:50%; background:' + color + '; display:inline-block"></span>'
            + label + " " + age +
            '</span>'
        )

    refresh_color = C_HIGH if next_refresh > 30 else (C_MOD if next_refresh > 5 else C_LOW)

    st.markdown(
        '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
        ' border-radius:10px; padding:10px 18px; margin-bottom:18px;'
        ' display:flex; align-items:center; gap:14px; flex-wrap:wrap">'

        # LIVE badge
        '<div style="display:flex; align-items:center; background:rgba(16,185,129,0.10);'
        ' border:1px solid rgba(16,185,129,0.28); border-radius:999px;'
        ' padding:3px 12px; white-space:nowrap">'
        + live_dot +
        '<span style="font-size:0.72rem; font-weight:800; color:' + C_HIGH + '; letter-spacing:0.06em">LIVE</span>'
        '</div>'

        # Timestamp
        '<div style="font-size:0.68rem; color:' + C_TEXT3 + '; font-family:monospace; white-space:nowrap">'
        + _now_ts_str() +
        '</div>'

        # Divider
        '<div style="width:1px; height:20px; background:rgba(255,255,255,0.08); flex-shrink:0"></div>'

        # Source chips
        '<div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center">'
        + source_chips +
        '</div>'

        # Spacer
        '<div style="flex:1"></div>'

        # Next refresh
        '<div style="font-size:0.68rem; color:' + refresh_color + '; white-space:nowrap;'
        ' font-weight:600; font-family:monospace">'
        'Next refresh in: ' + str(next_refresh) + ' min'
        '</div>'

        '</div>',
        unsafe_allow_html=True,
    )


# ── Section 2: Signal Stream ───────────────────────────────────────────────────

def _build_feed_items(
    insights: list,
    freight_data: dict,
    port_results: list,
    macro_data: dict,
    stock_data: dict,
) -> list[dict[str, Any]]:
    """Collect all feed items from every data source."""
    items: list[dict[str, Any]] = []

    # 1. Insights
    for ins in (insights or []):
        score = getattr(ins, "score", 0.5)
        if score >= 0.75:
            priority = _PRIORITY_CRITICAL
            badge_text = "CRITICAL"
        elif score >= 0.60:
            priority = _PRIORITY_HIGH
            badge_text = "HIGH"
        elif score >= 0.45:
            priority = _PRIORITY_MODERATE
            badge_text = "MODERATE"
        else:
            priority = _PRIORITY_LOW
            badge_text = "LOW"

        score_pct = str(int(score * 100)) + "%"
        cat = getattr(ins, "category", "ROUTE")
        badge_color = _CAT_COLORS.get(cat, C_ACCENT)

        detail_str = getattr(ins, "detail", "")
        if len(detail_str) > 120:
            detail_str = detail_str[:117] + "..."

        items.append(_make_feed_item(
            category=cat,
            title=getattr(ins, "title", "Insight"),
            detail=detail_str,
            priority=priority,
            badge_text=score_pct + " · " + getattr(ins, "action", "Monitor"),
            badge_color=badge_color,
            source=_CAT_LABELS.get(cat, cat),
        ))

    # 2. Freight rate alerts (30d change > 10%)
    for route_name, df in (freight_data or {}).items():
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        # freight_data DataFrames may have a 'value' column or a named column
        val_col = "value" if "value" in df.columns else df.columns[0]
        df2 = df.copy()
        if "date" in df2.columns:
            df2 = df2.sort_values("date")
        vals = df2[val_col].dropna()
        if len(vals) < 2:
            continue
        current_rate = float(vals.iloc[-1])

        # 30-day change
        pct30: float | None = None
        if "date" in df2.columns:
            ref_dt = df2["date"].max() - pd.Timedelta(days=30)
            mask = df2["date"] <= ref_dt
            if mask.any():
                ago_val = float(df2.loc[mask, val_col].dropna().iloc[-1])
                if ago_val != 0:
                    pct30 = (current_rate - ago_val) / abs(ago_val) * 100

        if pct30 is None or abs(pct30) <= 10:
            continue

        direction = "up" if pct30 > 0 else "down"
        badge_color = C_LOW if pct30 > 0 else C_HIGH  # rising rates = alert (red), falling = green
        priority = _PRIORITY_CRITICAL if abs(pct30) > 20 else _PRIORITY_HIGH
        label = str(route_name)[:20]
        pct_str = ("+" if pct30 > 0 else "") + str(round(pct30, 1)) + "%"
        rate_str = "$" + "{:,.0f}".format(int(current_rate)) + "/FEU" if current_rate > 500 else str(round(current_rate, 1))

        items.append(_make_feed_item(
            category="RATE_ALERT",
            title="RATE ALERT: " + label + " " + direction + " " + pct_str + " in 30d",
            detail="Current rate: " + rate_str + " · 30-day change: " + pct_str,
            priority=priority,
            badge_text=pct_str,
            badge_color=badge_color,
            source="FREIGHT",
        ))

    # 3. High-demand ports (demand_score >= 0.65)
    for pr in (port_results or []):
        score = getattr(pr, "demand_score", 0.0)
        has_data = getattr(pr, "has_real_data", False)
        if not has_data or score < 0.65:
            continue
        port_name = getattr(pr, "port_name", "Unknown")
        score_pct = str(int(score * 100)) + "%"
        trend = getattr(pr, "demand_trend", "Stable")
        priority = _PRIORITY_HIGH if score >= 0.75 else _PRIORITY_MODERATE
        badge_color = C_HIGH if score >= 0.75 else C_MOD

        items.append(_make_feed_item(
            category="PORT_DEMAND",
            title="HIGH DEMAND: " + port_name + " " + score_pct + " demand score",
            detail="Trend: " + trend + " · Region: " + getattr(pr, "region", "—"),
            priority=priority,
            badge_text=score_pct + " demand",
            badge_color=badge_color,
            source="PORT",
        ))

    # 4. BDI value from macro_data
    bdi_df = (macro_data or {}).get("BSXRLM")
    if bdi_df is not None and not bdi_df.empty and "value" in bdi_df.columns:
        bdi_sorted = bdi_df.sort_values("date") if "date" in bdi_df.columns else bdi_df
        bdi_vals = bdi_sorted["value"].dropna()
        if not bdi_vals.empty:
            bdi_current = float(bdi_vals.iloc[-1])
            bdi_pct30 = _pct_change_30d_df(bdi_sorted if "date" in bdi_sorted.columns else bdi_df)
            bdi_pct_str = ""
            bdi_priority = _PRIORITY_MODERATE
            bdi_badge_color = C_MACRO
            if bdi_pct30 is not None:
                bdi_pct_str = (" +" if bdi_pct30 >= 0 else " ") + str(round(bdi_pct30, 1)) + "%"
                if abs(bdi_pct30) > 10:
                    bdi_priority = _PRIORITY_HIGH
                    bdi_badge_color = C_HIGH if bdi_pct30 > 0 else C_LOW
            bdi_val_str = "{:,.0f}".format(int(bdi_current))
            items.append(_make_feed_item(
                category="BDI",
                title="BDI: " + bdi_val_str + bdi_pct_str,
                detail="Baltic Dry Index — bellwether for global dry bulk shipping demand",
                priority=bdi_priority,
                badge_text="BDI " + bdi_val_str,
                badge_color=bdi_badge_color,
                source="FRED/MACRO",
            ))

    # 5. Stock moves > 2% day change
    for ticker, df in (stock_data or {}).items():
        if ticker not in _SHIPPING_TICKERS:
            continue
        chg = _day_change_pct(df)
        if chg is None or abs(chg) <= 2.0:
            continue
        direction_str = "up" if chg > 0 else "down"
        chg_str = ("+" if chg >= 0 else "") + str(round(chg, 1)) + "%"
        close_vals = df["close"].dropna() if "close" in df.columns else pd.Series(dtype=float)
        price_str = ""
        if not close_vals.empty:
            price_str = " · $" + str(round(float(close_vals.iloc[-1]), 2))
        badge_color = C_HIGH if chg > 0 else C_LOW
        priority = _PRIORITY_CRITICAL if abs(chg) > 5 else (_PRIORITY_HIGH if abs(chg) > 3 else _PRIORITY_MODERATE)

        items.append(_make_feed_item(
            category="STOCK_MOVE",
            title=ticker + " " + direction_str + " " + chg_str + " today" + price_str,
            detail="Day change: " + chg_str + " · Shipping equity signal",
            priority=priority,
            badge_text=chg_str,
            badge_color=badge_color,
            source="EQUITY",
        ))

    # Sort: priority asc (critical first), then by title for stable ordering
    items.sort(key=lambda x: (x["priority"], x["title"]))
    return items


def _render_feed_item_html(item: dict[str, Any], idx: int) -> str:
    """Build HTML for a single feed item row."""
    color = item["color"]
    badge_color = item["badge_color"]
    badge_bg = _hex_to_rgba(badge_color, 0.14)
    badge_border = _hex_to_rgba(badge_color, 0.30)
    row_bg = "rgba(255,255,255,0.015)" if idx % 2 == 0 else "transparent"
    source_label = _CAT_LABELS.get(item["category"], item["source"])

    return (
        '<div style="display:flex; align-items:flex-start; padding:12px 16px;'
        ' border-bottom:1px solid rgba(255,255,255,0.04); background:' + row_bg + '">'

        # Left accent bar
        '<div style="width:3px; background:' + color + '; border-radius:2px;'
        ' margin-right:12px; flex-shrink:0; align-self:stretch; min-height:40px"></div>'

        # Main content
        '<div style="flex:1; min-width:0">'
        '<div style="font-size:0.68rem; color:#475569; font-family:monospace; margin-bottom:2px">'
        + item["ts"] + " \xb7 " + source_label +
        '</div>'
        '<div style="font-size:0.88rem; font-weight:600; color:#f1f5f9; margin-top:2px;'
        ' line-height:1.35">'
        + item["title"] +
        '</div>'
        '<div style="font-size:0.78rem; color:#94a3b8; margin-top:2px; line-height:1.4">'
        + item["detail"] +
        '</div>'
        '</div>'

        # Badge
        '<div style="flex-shrink:0; margin-left:12px; padding-top:2px">'
        '<span style="background:' + badge_bg + '; color:' + badge_color + ';'
        ' border:1px solid ' + badge_border + ';'
        ' padding:2px 9px; border-radius:999px; font-size:0.65rem; font-weight:700;'
        ' white-space:nowrap; font-family:monospace">'
        + item["badge_text"] +
        '</span>'
        '</div>'

        '</div>'
    )


def _render_signal_stream(
    insights: list,
    freight_data: dict,
    port_results: list,
    macro_data: dict,
    stock_data: dict,
) -> None:
    """Section 2: Bloomberg-style vertically scrolling signal stream."""
    section_header(
        "Signal Stream",
        "All live signals, rate alerts, and market moves — sorted by priority",
    )

    items = _build_feed_items(insights, freight_data, port_results, macro_data, stock_data)

    if not items:
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
            ' border-radius:12px; padding:32px; text-align:center">'
            '<div style="font-size:0.9rem; color:' + C_TEXT2 + '">No signals yet \u2014 data loading or all within normal ranges.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # Count badge
    critical_count = sum(1 for i in items if i["priority"] == _PRIORITY_CRITICAL)
    high_count = sum(1 for i in items if i["priority"] == _PRIORITY_HIGH)
    count_html = (
        '<div style="display:flex; gap:8px; margin-bottom:10px; align-items:center">'
        '<div style="font-size:0.72rem; color:' + C_TEXT3 + '">' + str(len(items)) + ' signals</div>'
    )
    if critical_count:
        count_html += (
            '<span style="background:rgba(239,68,68,0.14); color:' + C_LOW + ';'
            ' border:1px solid rgba(239,68,68,0.30); padding:1px 8px; border-radius:999px;'
            ' font-size:0.64rem; font-weight:700">'
            + str(critical_count) + ' CRITICAL'
            '</span>'
        )
    if high_count:
        count_html += (
            '<span style="background:rgba(245,158,11,0.14); color:' + C_MOD + ';'
            ' border:1px solid rgba(245,158,11,0.30); padding:1px 8px; border-radius:999px;'
            ' font-size:0.64rem; font-weight:700">'
            + str(high_count) + ' HIGH'
            '</span>'
        )
    count_html += '</div>'
    st.markdown(count_html, unsafe_allow_html=True)

    rows_html = "".join(_render_feed_item_html(item, idx) for idx, item in enumerate(items))

    st.markdown(
        '<div style="height:500px; overflow-y:auto; background:#0a0f1a;'
        ' border:1px solid rgba(255,255,255,0.08); border-radius:12px; padding:0">'
        + rows_html +
        '</div>',
        unsafe_allow_html=True,
    )


# ── Section 3: Market Pulse Grid ──────────────────────────────────────────────

def _render_stock_mini_table(stock_data: dict) -> None:
    """Column 1: Shipping stocks mini table with sparklines."""
    st.markdown(
        '<div style="font-size:0.72rem; font-weight:700; color:' + C_TEXT3 + ';'
        ' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:10px">Shipping Stocks</div>',
        unsafe_allow_html=True,
    )

    COLORS_MAP = {
        "ZIM":  "#3b82f6",
        "MATX": "#10b981",
        "SBLK": "#f59e0b",
        "DAC":  "#8b5cf6",
        "CMRE": "#06b6d4",
    }

    for ticker in _SHIPPING_TICKERS:
        df = (stock_data or {}).get(ticker)
        if df is None or df.empty or "close" not in df.columns:
            st.markdown(
                '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.07);'
                ' border-radius:8px; padding:8px 12px; margin-bottom:6px;'
                ' display:flex; justify-content:space-between; align-items:center">'
                '<span style="font-size:0.8rem; font-weight:700; color:' + C_TEXT2 + '">' + ticker + '</span>'
                '<span style="font-size:0.72rem; color:' + C_TEXT3 + '">n/a</span>'
                '</div>',
                unsafe_allow_html=True,
            )
            continue

        close_vals = df["close"].dropna()
        if close_vals.empty:
            continue

        price = float(close_vals.iloc[-1])
        chg = _day_change_pct(df)
        chg_str = ("+" if chg and chg >= 0 else "") + (str(round(chg, 2)) + "%" if chg is not None else "—")
        chg_color = C_HIGH if (chg or 0) > 0 else (C_LOW if (chg or 0) < 0 else C_TEXT2)
        ticker_color = COLORS_MAP.get(ticker, C_ACCENT)

        # 5-day sparkline
        spark_vals = close_vals.tail(5).tolist()
        spark_x = list(range(len(spark_vals)))
        spark_color = C_HIGH if len(spark_vals) > 1 and spark_vals[-1] >= spark_vals[0] else C_LOW

        fig = go.Figure(go.Scatter(
            x=spark_x,
            y=spark_vals,
            mode="lines",
            line=dict(color=spark_color, width=1.5),
            fill="tozeroy",
            fillcolor=_hex_to_rgba(spark_color, 0.10),
            hoverinfo="skip",
        ))
        fig.update_layout(
            height=40,
            margin=dict(l=0, r=0, t=0, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            showlegend=False,
        )

        col_info, col_spark = st.columns([3, 2])
        with col_info:
            st.markdown(
                '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.07);'
                ' border-left:3px solid ' + ticker_color + ';'
                ' border-radius:8px; padding:7px 10px; margin-bottom:4px">'
                '<div style="display:flex; justify-content:space-between; align-items:center">'
                '<span style="font-size:0.8rem; font-weight:800; color:' + C_TEXT + '">' + ticker + '</span>'
                '<span style="font-size:0.78rem; font-weight:700; color:' + C_TEXT + '">'
                '$' + str(round(price, 2)) +
                '</span>'
                '</div>'
                '<div style="font-size:0.68rem; color:' + chg_color + '; font-weight:600; margin-top:2px">'
                + chg_str +
                '</div>'
                '</div>',
                unsafe_allow_html=True,
            )
        with col_spark:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=f"livefeed_spark_{ticker}")


def _render_freight_pulse(freight_data: dict) -> None:
    """Column 2: Top 5 route freight rates with 30d trend arrows."""
    st.markdown(
        '<div style="font-size:0.72rem; font-weight:700; color:' + C_TEXT3 + ';'
        ' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:10px">Freight Rates</div>',
        unsafe_allow_html=True,
    )

    if not freight_data:
        st.markdown(
            '<div style="color:' + C_TEXT2 + '; font-size:0.82rem; padding:12px">Freight data unavailable.</div>',
            unsafe_allow_html=True,
        )
        return

    shown = 0
    for route_name, df in (freight_data or {}).items():
        if shown >= 5:
            break
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        val_col = "value" if "value" in df.columns else df.columns[0]
        df2 = df.copy()
        if "date" in df2.columns:
            df2 = df2.sort_values("date")
        vals = df2[val_col].dropna()
        if vals.empty:
            continue

        current_rate = float(vals.iloc[-1])
        pct30: float | None = None
        if "date" in df2.columns:
            ref_dt = df2["date"].max() - pd.Timedelta(days=30)
            mask = df2["date"] <= ref_dt
            if mask.any():
                ago_val = float(df2.loc[mask, val_col].dropna().iloc[-1])
                if ago_val != 0:
                    pct30 = (current_rate - ago_val) / abs(ago_val) * 100

        if pct30 is not None:
            if pct30 > 2:
                arrow = "\u25b2"
                trend_color = C_HIGH
                pct_str = "+" + str(round(pct30, 1)) + "%"
            elif pct30 < -2:
                arrow = "\u25bc"
                trend_color = C_LOW
                pct_str = str(round(pct30, 1)) + "%"
            else:
                arrow = "\u2014"
                trend_color = C_TEXT2
                pct_str = str(round(pct30, 1)) + "%"
        else:
            arrow = "\u2014"
            trend_color = C_TEXT2
            pct_str = "n/a"

        rate_str = "$" + "{:,.0f}".format(int(current_rate)) if current_rate > 500 else str(round(current_rate, 1))
        route_label = str(route_name)[:22]

        st.markdown(
            '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.07);'
            ' border-radius:8px; padding:8px 12px; margin-bottom:6px">'
            '<div style="display:flex; justify-content:space-between; align-items:center">'
            '<span style="font-size:0.72rem; color:' + C_TEXT2 + '; font-weight:500">'
            + route_label +
            '</span>'
            '<span style="font-size:0.88rem; font-weight:700; color:' + C_TEXT + '">'
            + rate_str +
            '</span>'
            '</div>'
            '<div style="font-size:0.68rem; color:' + trend_color + '; font-weight:600; margin-top:3px">'
            + arrow + " " + pct_str + " (30d)"
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        shown += 1

    if shown == 0:
        st.markdown(
            '<div style="color:' + C_TEXT2 + '; font-size:0.82rem; padding:12px">No rate data.</div>',
            unsafe_allow_html=True,
        )


def _render_macro_pulse(macro_data: dict) -> None:
    """Column 3: BDI, PMI proxy, Oil price — current value + direction."""
    st.markdown(
        '<div style="font-size:0.72rem; font-weight:700; color:' + C_TEXT3 + ';'
        ' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:10px">Macro Pulse</div>',
        unsafe_allow_html=True,
    )

    MACRO_ITEMS = [
        ("BSXRLM", "Baltic Dry Index (BDI)", ""),
        ("UMCSENT", "Consumer Sentiment (PMI proxy)", ""),
        ("WPU101",  "Fuel PPI (Oil proxy)", ""),
        ("PPIACO",  "PPI — All Commodities", ""),
        ("MANEMP",  "Mfg Employment", ""),
    ]

    if not macro_data:
        st.markdown(
            '<div style="color:' + C_TEXT2 + '; font-size:0.82rem; padding:12px">Macro data unavailable.</div>',
            unsafe_allow_html=True,
        )
        return

    shown = 0
    for series_id, label, _unit in MACRO_ITEMS:
        df = (macro_data or {}).get(series_id)
        if df is None or df.empty or "value" not in df.columns:
            continue
        df2 = df.sort_values("date") if "date" in df.columns else df
        vals = df2["value"].dropna()
        if vals.empty:
            continue

        current = float(vals.iloc[-1])
        pct30 = _pct_change_30d_df(df2 if "date" in df2.columns else df)

        if abs(current) >= 1000:
            val_str = "{:,.0f}".format(int(current))
        elif abs(current) >= 10:
            val_str = str(round(current, 1))
        else:
            val_str = str(round(current, 2))

        if pct30 is not None:
            if pct30 > 2:
                arrow = "\u25b2"
                trend_color = C_HIGH
                pct_str = "+" + str(round(pct30, 1)) + "%"
                trend_desc = "Rising"
            elif pct30 < -2:
                arrow = "\u25bc"
                trend_color = C_LOW
                pct_str = str(round(pct30, 1)) + "%"
                trend_desc = "Falling"
            else:
                arrow = "\u2014"
                trend_color = C_TEXT2
                pct_str = str(round(pct30, 1)) + "%"
                trend_desc = "Stable"
        else:
            arrow = "\u2014"
            trend_color = C_TEXT2
            pct_str = "n/a"
            trend_desc = "Stable"

        st.markdown(
            '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.07);'
            ' border-radius:8px; padding:8px 12px; margin-bottom:6px">'
            '<div style="font-size:0.65rem; color:' + C_TEXT3 + '; font-weight:600;'
            ' text-transform:uppercase; letter-spacing:0.06em; margin-bottom:3px">'
            + label[:28] +
            '</div>'
            '<div style="display:flex; align-items:baseline; gap:8px">'
            '<span style="font-size:1.05rem; font-weight:800; color:' + C_TEXT + '; font-variant-numeric:tabular-nums">'
            + val_str +
            '</span>'
            '<span style="font-size:0.72rem; font-weight:600; color:' + trend_color + '">'
            + arrow + " " + pct_str +
            '</span>'
            '<span style="font-size:0.65rem; color:' + C_TEXT3 + '; margin-left:auto">'
            + trend_desc +
            '</span>'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        shown += 1
        if shown >= 5:
            break

    if shown == 0:
        st.markdown(
            '<div style="color:' + C_TEXT2 + '; font-size:0.82rem; padding:12px">No macro data loaded.</div>',
            unsafe_allow_html=True,
        )


def _render_market_pulse(stock_data: dict, freight_data: dict, macro_data: dict) -> None:
    """Section 3: 3-column market pulse grid."""
    section_header(
        "Market Pulse",
        "Stocks · Freight rates · Macro indicators — current snapshot",
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
            ' border-radius:12px; padding:14px 16px">'
            '</div>',
            unsafe_allow_html=True,
        )
        _render_stock_mini_table(stock_data)

    with col2:
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
            ' border-radius:12px; padding:14px 16px">'
            '</div>',
            unsafe_allow_html=True,
        )
        _render_freight_pulse(freight_data)

    with col3:
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
            ' border-radius:12px; padding:14px 16px">'
            '</div>',
            unsafe_allow_html=True,
        )
        _render_macro_pulse(macro_data)


# ── Section 4: Port Demand Heatmap Grid ───────────────────────────────────────

def _render_port_heatmap(port_results: list) -> None:
    """Section 4: Visual heatmap grid — one cell per port, color = demand score."""
    section_header(
        "Port Demand Heatmap",
        "Color intensity = demand score · Click a port to inspect",
    )

    if not port_results:
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
            ' border-radius:12px; padding:24px; text-align:center">'
            '<div style="color:' + C_TEXT2 + '; font-size:0.88rem">Port data not available.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # Sort ports by demand score descending for a cleaner grid layout
    sorted_ports = sorted(
        port_results,
        key=lambda p: getattr(p, "demand_score", 0.0),
        reverse=True,
    )

    port_names   = [getattr(p, "port_name", "?") for p in sorted_ports]
    demand_scores = [getattr(p, "demand_score", 0.0) for p in sorted_ports]
    locodes      = [getattr(p, "locode", "") for p in sorted_ports]

    # Build grid dimensions aiming for roughly 5 columns
    n = len(sorted_ports)
    n_cols = 5
    n_rows = (n + n_cols - 1) // n_cols

    # Pad to full grid
    pad = n_rows * n_cols - n
    padded_names  = port_names  + [""] * pad
    padded_scores = demand_scores + [0.0] * pad
    padded_locodes = locodes + [""] * pad

    # Reshape
    z_rows: list[list[float]] = []
    text_rows: list[list[str]] = []
    locode_rows: list[list[str]] = []
    for r in range(n_rows):
        z_rows.append(padded_scores[r * n_cols:(r + 1) * n_cols])
        text_rows.append(padded_names[r * n_cols:(r + 1) * n_cols])
        locode_rows.append(padded_locodes[r * n_cols:(r + 1) * n_cols])

    # Build hover text
    hover_rows: list[list[str]] = []
    for r in range(n_rows):
        hover_row = []
        for c in range(n_cols):
            idx = r * n_cols + c
            if idx < n:
                p = sorted_ports[idx]
                sc = getattr(p, "demand_score", 0.0)
                trend = getattr(p, "demand_trend", "—")
                label = getattr(p, "demand_label", "—")
                hover_row.append(
                    getattr(p, "port_name", "?")
                    + "<br>Score: " + str(int(sc * 100)) + "%"
                    + "<br>Label: " + label
                    + "<br>Trend: " + trend
                    + "<br>LOCODE: " + getattr(p, "locode", "?")
                )
            else:
                hover_row.append("")
        hover_rows.append(hover_row)

    COLORSCALE = [
        [0.00, "#1e3a5f"],
        [0.25, "#3b82f6"],
        [0.50, "#10b981"],
        [0.75, "#f59e0b"],
        [1.00, "#ef4444"],
    ]

    fig = go.Figure(go.Heatmap(
        z=z_rows,
        text=text_rows,
        customdata=hover_rows,
        texttemplate="%{text}",
        textfont=dict(size=9, color="rgba(255,255,255,0.9)"),
        colorscale=COLORSCALE,
        zmin=0,
        zmax=1,
        hovertemplate="%{customdata}<extra></extra>",
        colorbar=dict(
            title=dict(text="Demand", font=dict(color=C_TEXT2, size=11)),
            thickness=12,
            len=0.8,
            tickfont=dict(color=C_TEXT2, size=10),
            tickformat=".0%",
            outlinewidth=0,
        ),
        showscale=True,
    ))

    fig.update_layout(
        template="plotly_dark",
        height=max(200, n_rows * 60 + 60),
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        margin=dict(t=10, b=10, l=10, r=80),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False, autorange="reversed"),
        font=dict(family="Inter, sans-serif"),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color="#f1f5f9", size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Optional: port detail selector
    with st.expander("Inspect a port"):
        port_display_names = [getattr(p, "port_name", "?") for p in sorted_ports]
        selected_name = st.selectbox(
            "Select port",
            port_display_names,
            key="livefeed_port_select",
        )
        match = next(
            (p for p in sorted_ports if getattr(p, "port_name", "") == selected_name),
            None,
        )
        if match is not None:
            sc = getattr(match, "demand_score", 0.0)
            sc_color = C_HIGH if sc >= 0.65 else (C_MOD if sc >= 0.35 else C_LOW)
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.markdown(
                    '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
                    ' border-top:3px solid ' + sc_color + ';'
                    ' border-radius:10px; padding:12px 14px">'
                    '<div style="font-size:0.65rem; color:' + C_TEXT3 + '; text-transform:uppercase;'
                    ' letter-spacing:0.07em; margin-bottom:4px">Demand Score</div>'
                    '<div style="font-size:1.6rem; font-weight:800; color:' + C_TEXT + '">'
                    + str(int(sc * 100)) + '%'
                    '</div>'
                    '<div style="font-size:0.72rem; color:' + C_TEXT2 + '; margin-top:2px">'
                    + getattr(match, "demand_label", "—") + " · " + getattr(match, "demand_trend", "—")
                    + '</div>'
                    + '</div>',
                    unsafe_allow_html=True,
                )
            with col_b:
                vessels = getattr(match, "vessel_count", 0)
                st.markdown(
                    '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
                    ' border-radius:10px; padding:12px 14px">'
                    '<div style="font-size:0.65rem; color:' + C_TEXT3 + '; text-transform:uppercase;'
                    ' letter-spacing:0.07em; margin-bottom:4px">Vessels (AIS)</div>'
                    '<div style="font-size:1.6rem; font-weight:800; color:' + C_TEXT + '">'
                    + str(vessels) +
                    '</div>'
                    '<div style="font-size:0.72rem; color:' + C_TEXT2 + '; margin-top:2px">'
                    + getattr(match, "region", "—")
                    + '</div>'
                    + '</div>',
                    unsafe_allow_html=True,
                )
            with col_c:
                teu = getattr(match, "throughput_teu_m", 0.0)
                teu_str = str(round(teu, 1)) + "M TEU/yr" if teu > 0 else "—"
                st.markdown(
                    '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
                    ' border-radius:10px; padding:12px 14px">'
                    '<div style="font-size:0.65rem; color:' + C_TEXT3 + '; text-transform:uppercase;'
                    ' letter-spacing:0.07em; margin-bottom:4px">Throughput</div>'
                    '<div style="font-size:1.6rem; font-weight:800; color:' + C_TEXT + '">'
                    + teu_str +
                    '</div>'
                    '<div style="font-size:0.72rem; color:' + C_TEXT2 + '; margin-top:2px">'
                    + getattr(match, "locode", "—") + " · " + getattr(match, "country_iso3", "—")
                    + '</div>'
                    + '</div>',
                    unsafe_allow_html=True,
                )


# ── Section 5: Auto-refresh Controls ─────────────────────────────────────────

def _render_autorefresh_controls() -> None:
    """Section 5: Auto-refresh slider and manual rerun button."""
    section_header(
        "Auto-Refresh Controls",
        "Control dashboard refresh cadence",
    )

    col_slider, col_btn, col_status = st.columns([3, 1, 2])

    with col_slider:
        interval_min = st.slider(
            "Auto-refresh interval (minutes)",
            min_value=1,
            max_value=30,
            value=15,
            step=1,
            key="livefeed_refresh_interval",
            help="Set how often the dashboard should pull fresh data.",
        )

    with col_btn:
        st.markdown("<div style='margin-top:28px'>", unsafe_allow_html=True)
        if st.button("Refresh Now", key="livefeed_manual_refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    with col_status:
        next_min = interval_min
        next_color = C_HIGH if next_min > 10 else (C_MOD if next_min > 3 else C_LOW)
        st.markdown(
            '<div style="margin-top:28px; background:#0d1117;'
            ' border:1px solid rgba(255,255,255,0.07);'
            ' border-radius:8px; padding:8px 14px; font-size:0.75rem">'
            '<div style="color:' + C_TEXT3 + '; font-size:0.65rem; margin-bottom:3px;'
            ' text-transform:uppercase; letter-spacing:0.06em">Configured cadence</div>'
            '<div style="color:' + next_color + '; font-weight:700">'
            + str(interval_min) + ' min interval'
            '</div>'
            '<div style="color:' + C_TEXT3 + '; font-size:0.65rem; margin-top:3px">'
            'Click Refresh Now to trigger immediately'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

    # Informational note — actual timed loop would block Streamlit, so we show guidance
    st.markdown(
        '<div style="background:rgba(59,130,246,0.07); border:1px solid rgba(59,130,246,0.18);'
        ' border-radius:8px; padding:10px 14px; margin-top:8px;'
        ' font-size:0.75rem; color:' + C_TEXT2 + '">'
        '<strong style="color:' + C_ACCENT + '">Note:</strong> '
        'Streamlit does not support background timers natively. '
        'Use the sidebar "Refresh All Data" button or set Streamlit\'s '
        '<code style="background:rgba(255,255,255,0.06); padding:1px 5px; border-radius:4px">server.runOnSave</code> '
        'option, or use an external scheduler (e.g. cron + cache invalidation) '
        'to match the configured interval.'
        '</div>',
        unsafe_allow_html=True,
    )


# ── Main render ───────────────────────────────────────────────────────────────

def render(
    port_results: list,
    route_results: list,
    insights: list,
    freight_data: dict,
    stock_data: dict,
    macro_data: dict,
) -> None:
    """Render the Live Intelligence Feed tab.

    Args:
        port_results:  List[PortDemandResult] from ports.demand_analyzer
        route_results: List[RouteOpportunity] from routes.optimizer
        insights:      List[Insight] from engine.scorer
        freight_data:  dict[route_name, pd.DataFrame] from data.freight_scraper
        stock_data:    dict[ticker, pd.DataFrame] from data.stock_feed
        macro_data:    dict[series_id, pd.DataFrame] from data.fred_feed
    """
    # ── Pulse animation (injected once per render) ────────────────────────
    st.markdown(
        '<style>'
        '@keyframes pulse-dot {'
        '0%,100%{opacity:1;transform:scale(1)}'
        '50%{opacity:0.55;transform:scale(1.3)}'
        '}'
        '</style>',
        unsafe_allow_html=True,
    )

    # ── Section 1: Live Status Bar ────────────────────────────────────────
    _render_status_bar()
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')} • Refreshes every 6 hours (AIS vessel data)")

    # ── Section 2: Signal Stream ──────────────────────────────────────────
    with st.spinner("Loading live feed..."):
        _render_signal_stream(insights, freight_data, port_results, macro_data, stock_data)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )

    # ── Section 3: Market Pulse Grid ─────────────────────────────────────
    _render_market_pulse(stock_data, freight_data, macro_data)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )

    # ── Section 4: Port Demand Heatmap ───────────────────────────────────
    _render_port_heatmap(port_results)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )

    # ── Section 5: Auto-Refresh Controls ─────────────────────────────────
    _render_autorefresh_controls()


# ── Integration instructions (DO NOT add to app.py — read this comment) ───────
#
# To integrate this tab into app.py:
#
# 1. Add a new tab variable in the st.tabs() call, e.g.:
#
#       tab0, tab1, tab2, tab3, tab4, tab5, tab6, tab_live = st.tabs([
#           "🌍  Overview",
#           "🏗️  Port Demand",
#           "🚢  Routes",
#           "🔥  Results",
#           "📈  Markets",
#           "🏥  Supply Chain",
#           "🎭  Scenarios",
#           "⚡  Live Feed",      # <-- new
#       ])
#
# 2. Add a with-block at the bottom of the tabs section:
#
#       with tab_live:
#           from ui.tab_live_feed import render as render_live_feed
#           render_live_feed(
#               port_results=port_results,
#               route_results=route_results,
#               insights=insights,
#               freight_data=freight_data,
#               stock_data=stock_data,
#               macro_data=macro_data,
#           )
#
# All six arguments are already computed earlier in app.py:
#   - port_results   → from analyze_all_ports(...)
#   - route_results  → from optimize_all_routes(...)
#   - insights       → from InsightScorer(...).score_all(...)
#   - freight_data   → from get_freight_data(lookback + 30)
#   - stock_data     → from get_stock_data(lookback)
#   - macro_data     → from get_macro_data(lookback + 90)
#
# No other changes to app.py are required.
