"""Live Intelligence Feed tab — real-time dashboard with auto-refreshing data streams."""
from __future__ import annotations

import math
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

# ── Constants ──────────────────────────────────────────────────────────────────

_CACHE_DIR = Path(__file__).parent.parent / "cache"

_TTL_HOURS: dict[str, int] = {
    "comtrade":  168,
    "fred":       24,
    "worldbank": 168,
    "freight":    24,
    "ais":         6,
    "stocks":      1,
}

_SHIPPING_TICKERS = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]

_COMPANY_NAMES: dict[str, str] = {
    "ZIM":  "ZIM Integrated",
    "MATX": "Matson Inc",
    "SBLK": "Star Bulk Carriers",
    "DAC":  "Danaos Corp",
    "CMRE": "Costamare Inc",
}

_TICKER_COLORS: dict[str, str] = {
    "ZIM":  "#3b82f6",
    "MATX": "#10b981",
    "SBLK": "#f59e0b",
    "DAC":  "#8b5cf6",
    "CMRE": "#06b6d4",
}

_PRIORITY_CRITICAL = 0
_PRIORITY_HIGH     = 1
_PRIORITY_MODERATE = 2
_PRIORITY_LOW      = 3

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

_MACRO_ITEMS = [
    ("BSXRLM",  "Baltic Dry Index",        "BDI",  C_MACRO),
    ("UMCSENT", "Consumer Sentiment",       "PMI",  "#8b5cf6"),
    ("WPU101",  "Fuel Price Index",         "OIL",  "#f59e0b"),
    ("PPIACO",  "PPI All Commodities",      "PPI",  C_ACCENT),
    ("MANEMP",  "Mfg Employment",           "MFG",  C_HIGH),
]

# ── Utility helpers ────────────────────────────────────────────────────────────


def _now_ts_str() -> str:
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
    dt = datetime.now(timezone.utc)
    return str(dt.hour).zfill(2) + ":" + str(dt.minute).zfill(2) + " UTC"


def _cache_mtimes() -> dict[str, float | None]:
    mtimes: dict[str, float | None] = {}
    if not _CACHE_DIR.exists():
        for k in _TTL_HOURS:
            mtimes[k] = None
        return mtimes
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
        for ext in ("*.parquet", "*.json", "*.csv"):
            for f in _CACHE_DIR.rglob(ext):
                if pat in f.name.lower():
                    try:
                        matched.append(f.stat().st_mtime)
                    except OSError:
                        pass
        mtimes[bucket] = max(matched) if matched else None
    return mtimes


def _source_age_str(mtime: float | None) -> str:
    if mtime is None:
        return "never"
    age_s = time.time() - mtime
    if age_s < 60:
        return "just now"
    if age_s < 3600:
        return str(int(age_s / 60)) + "m ago"
    if age_s < 86400:
        return str(int(age_s / 3600)) + "h ago"
    return str(int(age_s / 86400)) + "d ago"


def _source_freshness_pct(mtime: float | None, ttl_hours: int) -> float:
    """Return [0, 1] fraction of TTL remaining (1 = fully fresh, 0 = expired)."""
    if mtime is None:
        return 0.0
    age_s = time.time() - mtime
    ttl_s = ttl_hours * 3600
    remaining = ttl_s - age_s
    return max(0.0, min(1.0, remaining / ttl_s))


def _source_status_color(mtime: float | None, ttl_hours: int) -> str:
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
    best = 9999
    now = time.time()
    for bucket, mtime in mtimes.items():
        ttl_s = _TTL_HOURS[bucket] * 3600
        if mtime is None:
            best = min(best, 0)
            continue
        remaining_s = ttl_s - (now - mtime)
        best = min(best, max(0, int(remaining_s / 60)))
    return max(0, best)


def _pct_change_30d_df(df: pd.DataFrame) -> float | None:
    if df is None or df.empty:
        return None
    if not {"date", "value"}.issubset(df.columns):
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


def _mini_sparkline_svg(vals: list[float], color: str, width: int = 80, height: int = 28) -> str:
    if len(vals) < 2:
        return '<span style="color:' + C_TEXT3 + ';font-size:0.68rem">—</span>'
    mn, mx = min(vals), max(vals)
    rng = mx - mn if mx != mn else 1.0
    n = len(vals)
    pad = 2
    pts = []
    for i, v in enumerate(vals):
        x = pad + (i / (n - 1)) * (width - 2 * pad)
        y = pad + (1 - (v - mn) / rng) * (height - 2 * pad)
        pts.append(str(round(x, 1)) + "," + str(round(y, 1)))
    area_pts = pts + [str(width - pad) + "," + str(height), str(pad) + "," + str(height)]
    fill = _hex_to_rgba(color, 0.15)
    return (
        '<svg width="' + str(width) + '" height="' + str(height) + '" '
        'xmlns="http://www.w3.org/2000/svg" style="overflow:visible">'
        '<polygon points="' + " ".join(area_pts) + '" fill="' + fill + '" stroke="none"/>'
        '<polyline points="' + " ".join(pts) + '" fill="none" stroke="' + color + '" '
        'stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>'
        '<circle cx="' + pts[-1].split(",")[0] + '" cy="' + pts[-1].split(",")[1] + '" '
        'r="2.5" fill="' + color + '"/>'
        '</svg>'
    )


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


# ── Global CSS + Animations ────────────────────────────────────────────────────


def _inject_global_styles() -> None:
    st.markdown(
        """<style>
        @keyframes pulse-dot {
            0%,100% { opacity:1; transform:scale(1); }
            50%      { opacity:0.5; transform:scale(1.4); }
        }
        @keyframes pulse-ring {
            0%   { box-shadow:0 0 0 0 rgba(239,68,68,0.55); }
            70%  { box-shadow:0 0 0 8px rgba(239,68,68,0); }
            100% { box-shadow:0 0 0 0 rgba(239,68,68,0); }
        }
        @keyframes pulse-ring-amber {
            0%   { box-shadow:0 0 0 0 rgba(245,158,11,0.45); }
            70%  { box-shadow:0 0 0 8px rgba(245,158,11,0); }
            100% { box-shadow:0 0 0 0 rgba(245,158,11,0); }
        }
        @keyframes glow-green {
            0%,100% { box-shadow:0 0 4px rgba(16,185,129,0.4); }
            50%     { box-shadow:0 0 14px rgba(16,185,129,0.85); }
        }
        @keyframes countdown-tick {
            0%,100% { opacity:1; }
            50%     { opacity:0.55; }
        }
        @keyframes slide-in {
            from { opacity:0; transform:translateY(6px); }
            to   { opacity:1; transform:translateY(0); }
        }
        .lf-hero { animation: slide-in 0.4s ease-out; }
        </style>""",
        unsafe_allow_html=True,
    )


# ── Section 1: Animated Hero Status Bar ───────────────────────────────────────


def _render_hero_status_bar() -> None:
    """Animated hero bar: pulsing LIVE badge, timestamp, countdown, per-source freshness bars."""
    mtimes = _cache_mtimes()
    next_refresh = _next_refresh_minutes(mtimes)
    now_dt = datetime.now(timezone.utc)
    ts_full = (
        str(now_dt.year) + "-"
        + str(now_dt.month).zfill(2) + "-"
        + str(now_dt.day).zfill(2) + " "
        + str(now_dt.hour).zfill(2) + ":"
        + str(now_dt.minute).zfill(2) + ":"
        + str(now_dt.second).zfill(2) + " UTC"
    )
    cd_color = C_HIGH if next_refresh > 30 else ("#f59e0b" if next_refresh > 5 else C_LOW)
    cd_anim  = "animation:countdown-tick 1s infinite" if next_refresh <= 5 else ""

    source_display = {
        "stocks":    ("Equities",  "1h"),
        "freight":   ("Freight",   "24h"),
        "fred":      ("Macro",     "24h"),
        "ais":       ("AIS",       "6h"),
        "comtrade":  ("Trade",     "7d"),
        "worldbank": ("Ports",     "7d"),
    }

    # Build freshness progress bars
    bars_html = ""
    for bucket, (label, ttl_hint) in source_display.items():
        mtime   = mtimes.get(bucket)
        color   = _source_status_color(mtime, _TTL_HOURS[bucket])
        age     = _source_age_str(mtime)
        pct     = _source_freshness_pct(mtime, _TTL_HOURS[bucket])
        bar_w   = str(int(pct * 100)) + "%"
        bar_bg  = _hex_to_rgba(color, 0.15)
        bar_fg  = color
        dot_anim = "animation:pulse-dot 2s infinite" if mtime is not None else ""
        bars_html += (
            '<div style="flex:1;min-width:80px">'
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
            '<div style="display:flex;align-items:center;gap:5px">'
            '<span style="width:6px;height:6px;border-radius:50%;background:' + bar_fg + ';'
            'display:inline-block;flex-shrink:0;' + dot_anim + '"></span>'
            '<span style="font-size:0.62rem;font-weight:700;color:' + C_TEXT2 + '">' + label + '</span>'
            '</div>'
            '<span style="font-size:0.58rem;color:' + C_TEXT3 + '">' + age + '</span>'
            '</div>'
            '<div style="height:4px;border-radius:999px;background:rgba(255,255,255,0.06);overflow:hidden">'
            '<div style="height:100%;width:' + bar_w + ';border-radius:999px;background:' + bar_fg + ';'
            'transition:width 0.6s ease"></div>'
            '</div>'
            '<div style="font-size:0.55rem;color:' + C_TEXT3 + ';margin-top:3px">' + ttl_hint + ' TTL</div>'
            '</div>'
        )

    live_dot = (
        '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;'
        'background:' + C_HIGH + ';margin-right:7px;flex-shrink:0;'
        'animation:glow-green 1.8s infinite"></span>'
    )

    # Check stale sources
    stale = [k.upper() for k, m in mtimes.items() if m is None or (time.time() - m) > _TTL_HOURS[k] * 3600]
    stale_banner = ""
    if stale:
        stale_banner = (
            '<div style="margin-top:14px;background:rgba(245,158,11,0.08);'
            'border:1px solid rgba(245,158,11,0.25);border-radius:8px;'
            'padding:8px 14px;display:flex;align-items:center;gap:10px">'
            '<span style="font-size:1rem">&#9888;</span>'
            '<span style="font-size:0.72rem;color:#f59e0b;font-weight:600">'
            'Stale sources: <strong>' + ", ".join(stale) + '</strong>'
            ' — click Refresh Now below or re-run data pipeline.'
            '</span>'
            '</div>'
        )

    st.markdown(
        '<div class="lf-hero" style="background:linear-gradient(135deg,#0b1422 0%,#0d1520 60%,#111827 100%);'
        'border:1px solid rgba(16,185,129,0.22);border-radius:16px;'
        'padding:20px 26px;margin-bottom:22px;'
        'box-shadow:0 4px 40px rgba(16,185,129,0.07),0 1px 0 rgba(255,255,255,0.04) inset">'

        # Row 1: LIVE badge + title + countdown
        '<div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:18px">'

        '<div style="display:flex;align-items:center;background:rgba(16,185,129,0.12);'
        'border:1px solid rgba(16,185,129,0.32);border-radius:999px;'
        'padding:6px 18px;flex-shrink:0">'
        + live_dot +
        '<span style="font-size:0.82rem;font-weight:900;color:' + C_HIGH + ';letter-spacing:0.12em">LIVE</span>'
        '</div>'

        '<div style="flex:1;min-width:180px">'
        '<div style="font-size:1.18rem;font-weight:800;color:' + C_TEXT + ';letter-spacing:-0.02em;line-height:1.2">'
        'Shipping Intelligence Feed'
        '</div>'
        '<div style="font-size:0.68rem;color:' + C_TEXT3 + ';margin-top:3px">'
        'Real-time market signals · freight rate movements · port demand · macro pulse'
        '</div>'
        '</div>'

        '<div style="background:rgba(0,0,0,0.35);border:1px solid rgba(255,255,255,0.08);'
        'border-radius:12px;padding:10px 18px;text-align:center;flex-shrink:0">'
        '<div style="font-size:0.56rem;font-weight:700;color:' + C_TEXT3 + ';'
        'text-transform:uppercase;letter-spacing:0.10em;margin-bottom:2px">Next Refresh</div>'
        '<div style="font-size:1.5rem;font-weight:800;color:' + cd_color + ';'
        'font-family:monospace;' + cd_anim + '">'
        + str(next_refresh) + '<span style="font-size:0.72rem;font-weight:500;margin-left:2px">min</span>'
        '</div>'
        '</div>'

        '</div>'

        # Row 2: Timestamp chip
        '<div style="font-size:0.63rem;color:' + C_TEXT3 + ';font-family:monospace;'
        'background:rgba(0,0,0,0.22);padding:4px 12px;border-radius:6px;'
        'display:inline-block;margin-bottom:14px">'
        'As of: <span style="color:' + C_TEXT2 + '">' + ts_full + '</span>'
        '</div>'

        # Row 3: Per-source freshness bars
        '<div style="display:flex;gap:14px;flex-wrap:wrap">'
        + bars_html +
        '</div>'

        + stale_banner +

        '</div>',
        unsafe_allow_html=True,
    )


# ── Section 2: Three-Column Real-Time Stream ───────────────────────────────────


def _render_three_col_stream(
    port_results: list,
    freight_data: dict,
    insights: list,
) -> None:
    """Port demand score bars | Freight rise/fall indicators | Signal badge tiers."""
    section_header(
        "Real-Time Data Streams",
        "Port demand updates  ·  Freight rate movements  ·  Signal changes",
    )

    col1, col2, col3 = st.columns(3, gap="medium")

    # ── Column 1: Port Demand with score bars ─────────────────────────────
    with col1:
        try:
            col_label = (
                '<div style="font-size:0.68rem;font-weight:800;color:' + C_HIGH + ';'
                'text-transform:uppercase;letter-spacing:0.10em;margin-bottom:12px;'
                'display:flex;align-items:center;gap:7px">'
                '<span style="width:7px;height:7px;border-radius:50%;background:' + C_HIGH + ';'
                'display:inline-block;animation:pulse-dot 1.5s infinite"></span>'
                'Port Demand Updates'
                '</div>'
            )
            ports_sorted = sorted(
                [p for p in (port_results or []) if getattr(p, "has_real_data", False)],
                key=lambda p: getattr(p, "demand_score", 0.0),
                reverse=True,
            )
            rows = ""
            for pr in ports_sorted[:9]:
                sc    = getattr(pr, "demand_score", 0.0)
                name  = getattr(pr, "port_name", "?")
                trend = getattr(pr, "demand_trend", "Stable")
                col   = C_HIGH if sc >= 0.65 else (C_MOD if sc >= 0.40 else C_TEXT2)
                arrow = "\u25b2" if trend == "Rising" else ("\u25bc" if trend == "Falling" else "\u2014")
                a_col = C_HIGH if trend == "Rising" else (C_LOW if trend == "Falling" else C_TEXT3)
                bar_w = str(int(sc * 100)) + "%"
                rows += (
                    '<div style="padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.04)">'
                    '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">'
                    '<div>'
                    '<div style="font-size:0.75rem;font-weight:600;color:' + C_TEXT + '">' + name[:20] + '</div>'
                    '<div style="font-size:0.60rem;color:' + C_TEXT3 + '">' + getattr(pr, "region", "—") + '</div>'
                    '</div>'
                    '<div style="text-align:right">'
                    '<div style="font-size:0.82rem;font-weight:800;color:' + col + '">' + str(int(sc * 100)) + '%</div>'
                    '<div style="font-size:0.62rem;color:' + a_col + '">' + arrow + ' ' + trend + '</div>'
                    '</div>'
                    '</div>'
                    '<div style="height:3px;border-radius:999px;background:rgba(255,255,255,0.05)">'
                    '<div style="height:100%;width:' + bar_w + ';border-radius:999px;background:' + col + '"></div>'
                    '</div>'
                    '</div>'
                )
            if not rows:
                rows = '<div style="padding:22px;text-align:center;color:' + C_TEXT3 + ';font-size:0.78rem">No port data loaded</div>'
            st.markdown(
                col_label +
                '<div style="background:#080e18;border:1px solid rgba(255,255,255,0.07);'
                'border-radius:10px;overflow:hidden">' + rows + '</div>',
                unsafe_allow_html=True,
            )
        except Exception:
            st.caption("Port demand stream unavailable.")

    # ── Column 2: Freight Rate Movements ──────────────────────────────────
    with col2:
        try:
            col_label2 = (
                '<div style="font-size:0.68rem;font-weight:800;color:#f59e0b;'
                'text-transform:uppercase;letter-spacing:0.10em;margin-bottom:12px;'
                'display:flex;align-items:center;gap:7px">'
                '<span style="width:7px;height:7px;border-radius:50%;background:#f59e0b;'
                'display:inline-block;animation:pulse-dot 1.8s infinite"></span>'
                'Freight Rate Movements'
                '</div>'
            )
            rows2 = ""
            for route_name, df in (freight_data or {}).items():
                if not isinstance(df, pd.DataFrame) or df.empty:
                    continue
                val_col = "value" if "value" in df.columns else df.columns[0]
                df2     = df.sort_values("date") if "date" in df.columns else df.copy()
                vals    = df2[val_col].dropna()
                if vals.empty:
                    continue
                cur = float(vals.iloc[-1])
                pct30: float | None = None
                if "date" in df2.columns:
                    ref  = df2["date"].max() - pd.Timedelta(days=30)
                    mask = df2["date"] <= ref
                    if mask.any():
                        ago = float(df2.loc[mask, val_col].dropna().iloc[-1])
                        if ago != 0:
                            pct30 = (cur - ago) / abs(ago) * 100
                rate_str = ("$" + "{:,.0f}".format(int(cur))) if cur > 500 else str(round(cur, 1))
                if pct30 is None:
                    mv_col, arrow, pct_str = C_TEXT2, "\u2014", "n/a"
                elif pct30 > 3:
                    mv_col, arrow, pct_str = C_LOW, "\u25b2", "+" + str(round(pct30, 1)) + "%"
                elif pct30 < -3:
                    mv_col, arrow, pct_str = C_HIGH, "\u25bc", str(round(pct30, 1)) + "%"
                else:
                    mv_col, arrow, pct_str = C_TEXT2, "\u2014", str(round(pct30, 1)) + "%"

                bar_bg  = _hex_to_rgba(mv_col, 0.12)
                rows2 += (
                    '<div style="padding:8px 11px;border-bottom:1px solid rgba(255,255,255,0.04);'
                    'background:' + bar_bg + '">'
                    '<div style="display:flex;justify-content:space-between;align-items:center">'
                    '<div style="font-size:0.72rem;color:' + C_TEXT2 + ';font-weight:500;max-width:130px;'
                    'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + str(route_name)[:24] + '</div>'
                    '<div style="text-align:right">'
                    '<div style="font-size:0.84rem;font-weight:700;color:' + C_TEXT + '">' + rate_str + '</div>'
                    '<div style="font-size:0.64rem;font-weight:700;color:' + mv_col + '">'
                    + arrow + ' ' + pct_str + ' 30d</div>'
                    '</div>'
                    '</div>'
                    '</div>'
                )
            if not rows2:
                rows2 = '<div style="padding:22px;text-align:center;color:' + C_TEXT3 + ';font-size:0.78rem">No freight data loaded</div>'
            st.markdown(
                col_label2 +
                '<div style="background:#080e18;border:1px solid rgba(255,255,255,0.07);'
                'border-radius:10px;overflow:hidden">' + rows2 + '</div>',
                unsafe_allow_html=True,
            )
        except Exception:
            st.caption("Freight rate stream unavailable.")

    # ── Column 3: Signal Changes with badge tiers ──────────────────────────
    with col3:
        try:
            col_label3 = (
                '<div style="font-size:0.68rem;font-weight:800;color:' + C_ACCENT + ';'
                'text-transform:uppercase;letter-spacing:0.10em;margin-bottom:12px;'
                'display:flex;align-items:center;gap:7px">'
                '<span style="width:7px;height:7px;border-radius:50%;background:' + C_ACCENT + ';'
                'display:inline-block;animation:pulse-dot 2.1s infinite"></span>'
                'Signal Changes'
                '</div>'
            )
            rows3 = ""
            sorted_insights = sorted(
                (insights or []),
                key=lambda x: getattr(x, "score", 0.0),
                reverse=True,
            )
            for ins in sorted_insights[:9]:
                score  = getattr(ins, "score", 0.5)
                title  = getattr(ins, "title", "Insight")
                action = getattr(ins, "action", "Monitor")
                cat    = getattr(ins, "category", "ROUTE")
                if score >= 0.75:
                    tier, tier_col = "CRITICAL", C_LOW
                elif score >= 0.60:
                    tier, tier_col = "HIGH", "#f97316"
                elif score >= 0.45:
                    tier, tier_col = "MODERATE", C_MOD
                else:
                    tier, tier_col = "LOW", C_TEXT3
                cat_col = _CAT_COLORS.get(cat, C_ACCENT)
                rows3 += (
                    '<div style="padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.04)">'
                    '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px">'
                    '<div style="font-size:0.74rem;font-weight:600;color:' + C_TEXT + ';'
                    'max-width:130px;line-height:1.3">' + title[:36] + '</div>'
                    '<span style="font-size:0.56rem;font-weight:800;color:' + tier_col + ';'
                    'background:' + _hex_to_rgba(tier_col, 0.14) + ';'
                    'border:1px solid ' + _hex_to_rgba(tier_col, 0.30) + ';'
                    'padding:1px 7px;border-radius:999px;white-space:nowrap;flex-shrink:0;margin-left:6px">'
                    + tier + '</span>'
                    '</div>'
                    '<div style="display:flex;align-items:center;gap:6px">'
                    '<span style="font-size:0.60rem;font-weight:700;color:' + cat_col + ';'
                    'background:' + _hex_to_rgba(cat_col, 0.12) + ';padding:1px 7px;border-radius:999px">'
                    + _CAT_LABELS.get(cat, cat) + '</span>'
                    '<span style="font-size:0.60rem;color:' + C_TEXT3 + '">' + str(int(score * 100)) + '% · ' + action + '</span>'
                    '</div>'
                    '</div>'
                )
            if not rows3:
                rows3 = '<div style="padding:22px;text-align:center;color:' + C_TEXT3 + ';font-size:0.78rem">No signals available</div>'
            st.markdown(
                col_label3 +
                '<div style="background:#080e18;border:1px solid rgba(255,255,255,0.07);'
                'border-radius:10px;overflow:hidden">' + rows3 + '</div>',
                unsafe_allow_html=True,
            )
        except Exception:
            st.caption("Signal stream unavailable.")


# ── Section 3: Vertical Activity Timeline ─────────────────────────────────────


def _render_activity_timeline(
    port_results: list,
    freight_data: dict,
    stock_data: dict,
    macro_data: dict,
    insights: list,
) -> None:
    """Timestamped event nodes, glowing dots, colored left-border cards."""
    section_header(
        "Activity Timeline",
        "Chronological event log — glowing nodes mark the highest-priority signals",
    )

    try:
        events: list[dict[str, Any]] = []

        # Insights
        for ins in (insights or [])[:5]:
            score = getattr(ins, "score", 0.5)
            cat   = getattr(ins, "category", "ROUTE")
            events.append({
                "time":   _short_ts(),
                "type":   _CAT_LABELS.get(cat, cat),
                "color":  _CAT_COLORS.get(cat, C_ACCENT),
                "title":  getattr(ins, "title", "Signal"),
                "detail": getattr(ins, "detail", ""),
                "badge":  str(int(score * 100)) + "% confidence · " + getattr(ins, "action", "Monitor"),
                "glow":   score >= 0.75,
            })

        # Freight rate movements
        for route_name, df in list((freight_data or {}).items())[:4]:
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            val_col = "value" if "value" in df.columns else df.columns[0]
            df2  = df.sort_values("date") if "date" in df.columns else df.copy()
            vals = df2[val_col].dropna()
            if len(vals) < 2:
                continue
            cur, prev = float(vals.iloc[-1]), float(vals.iloc[-2])
            if prev != 0:
                chg = (cur - prev) / abs(prev) * 100
                if abs(chg) > 0.8:
                    rate_str = ("$" + "{:,.0f}".format(int(cur))) if cur > 500 else str(round(cur, 2))
                    events.append({
                        "time":   _short_ts(),
                        "type":   "RATE MOVE",
                        "color":  C_LOW if chg > 0 else C_HIGH,
                        "title":  str(route_name)[:32] + " rate " + ("up" if chg > 0 else "down"),
                        "detail": "Current: " + rate_str + " / FEU",
                        "badge":  ("+" if chg > 0 else "") + str(round(chg, 1)) + "% vs prev",
                        "glow":   abs(chg) > 5,
                    })

        # Port demand events
        for pr in sorted(
            (port_results or []),
            key=lambda p: getattr(p, "demand_score", 0.0),
            reverse=True,
        )[:4]:
            sc = getattr(pr, "demand_score", 0.0)
            if not getattr(pr, "has_real_data", False) or sc < 0.45:
                continue
            events.append({
                "time":   _short_ts(),
                "type":   "PORT DEMAND",
                "color":  C_HIGH if sc >= 0.65 else C_MOD,
                "title":  getattr(pr, "port_name", "?") + " demand signal",
                "detail": "Region: " + getattr(pr, "region", "—") + " · Trend: " + getattr(pr, "demand_trend", "Stable"),
                "badge":  str(int(sc * 100)) + "% score",
                "glow":   sc >= 0.75,
            })

        # BDI
        bdi_df = (macro_data or {}).get("BSXRLM")
        if bdi_df is not None and not bdi_df.empty and "value" in bdi_df.columns:
            bdi_vals = bdi_df.sort_values("date")["value"].dropna() if "date" in bdi_df.columns else bdi_df["value"].dropna()
            if not bdi_vals.empty:
                bdi_val = float(bdi_vals.iloc[-1])
                events.append({
                    "time":   _short_ts(),
                    "type":   "BDI",
                    "color":  C_MACRO,
                    "title":  "Baltic Dry Index: " + "{:,.0f}".format(int(bdi_val)),
                    "detail": "Global dry bulk shipping demand bellwether",
                    "badge":  "MACRO INDICATOR",
                    "glow":   False,
                })

        # Stock moves
        for ticker in _SHIPPING_TICKERS:
            df = (stock_data or {}).get(ticker)
            if df is None or df.empty or "close" not in df.columns:
                continue
            chg = _day_change_pct(df)
            if chg is not None and abs(chg) >= 2.0:
                tc = _TICKER_COLORS.get(ticker, C_ACCENT)
                events.append({
                    "time":   _short_ts(),
                    "type":   "EQUITY MOVE",
                    "color":  tc,
                    "title":  ticker + " (" + _COMPANY_NAMES.get(ticker, ticker) + ") " + ("rallied" if chg > 0 else "dropped"),
                    "detail": ("+" if chg > 0 else "") + str(round(chg, 2)) + "% day change",
                    "badge":  ("+" if chg > 0 else "") + str(round(chg, 2)) + "%",
                    "glow":   abs(chg) >= 4.0,
                })

        if not events:
            st.markdown(
                '<div style="background:' + C_CARD + ';border:1px solid ' + C_BORDER + ';'
                'border-radius:10px;padding:28px;text-align:center;color:' + C_TEXT3 + ';font-size:0.82rem">'
                'No timeline events — load data to populate.'
                '</div>',
                unsafe_allow_html=True,
            )
            return

        tl = '<div style="position:relative;padding-left:30px">'
        tl += (
            '<div style="position:absolute;left:11px;top:0;bottom:0;width:2px;'
            'background:linear-gradient(to bottom,'
            'rgba(16,185,129,0.30) 0%,rgba(59,130,246,0.15) 50%,rgba(255,255,255,0.04) 100%)">'
            '</div>'
        )
        for i, ev in enumerate(events):
            color    = ev["color"]
            glow     = ev.get("glow", False)
            dot_size = "13px" if glow else "9px"
            dot_top  = "11px" if glow else "13px"
            dot_left = "-24px" if glow else "-22px"
            glow_css = ("animation:glow-green 1.8s infinite;" if color == C_HIGH and glow else
                        "animation:pulse-ring 1.8s infinite;" if glow else "")
            is_last  = i == len(events) - 1
            tl += (
                '<div style="position:relative;margin-bottom:' + ("12px" if not is_last else "0") + '">'
                '<div style="position:absolute;left:' + dot_left + ';top:' + dot_top + ';'
                'width:' + dot_size + ';height:' + dot_size + ';border-radius:50%;background:' + color + ';'
                + glow_css + '"></div>'
                '<div style="background:' + _hex_to_rgba(color, 0.05) + ';'
                'border:1px solid ' + _hex_to_rgba(color, 0.20) + ';'
                'border-left:3px solid ' + color + ';border-radius:9px;padding:10px 14px">'
                '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">'
                '<div style="display:flex;align-items:center;gap:8px">'
                '<span style="font-size:0.58rem;font-weight:800;color:' + color + ';'
                'text-transform:uppercase;letter-spacing:0.08em;'
                'background:' + _hex_to_rgba(color, 0.16) + ';padding:2px 8px;border-radius:999px">'
                + ev["type"] + '</span>'
                '<span style="font-size:0.60rem;color:' + C_TEXT3 + ';font-family:monospace">' + ev["time"] + '</span>'
                '</div>'
                '<span style="font-size:0.60rem;color:' + C_TEXT2 + ';font-weight:600;'
                'background:rgba(255,255,255,0.06);padding:2px 9px;border-radius:999px">'
                + ev["badge"] + '</span>'
                '</div>'
                '<div style="font-size:0.80rem;font-weight:600;color:' + C_TEXT + ';line-height:1.35">'
                + ev["title"] + '</div>'
                + ('<div style="font-size:0.68rem;color:' + C_TEXT3 + ';margin-top:2px;line-height:1.4">'
                   + ev["detail"][:90] + '</div>' if ev["detail"] else "")
                + '</div></div>'
            )
        tl += '</div>'
        st.markdown(tl, unsafe_allow_html=True)

    except Exception:
        st.caption("Timeline unavailable.")


# ── Section 4: Shipping Equity Grid ───────────────────────────────────────────


def _render_equity_grid(stock_data: dict) -> None:
    """Ticker, company, price, day%, volume, and Plotly sparklines in a styled grid."""
    section_header(
        "Shipping Equity Monitor",
        "Live prices · day change · volume · 5-day Plotly sparklines",
    )

    try:
        th = (
            "padding:10px 14px;font-size:0.60rem;font-weight:700;color:" + C_TEXT3 + ";"
            "text-transform:uppercase;letter-spacing:0.09em;border-bottom:1px solid rgba(255,255,255,0.08)"
        )
        rows_html = ""
        for ticker in _SHIPPING_TICKERS:
            df    = (stock_data or {}).get(ticker)
            tc    = _TICKER_COLORS.get(ticker, C_ACCENT)
            co    = _COMPANY_NAMES.get(ticker, ticker)

            if df is None or df.empty or "close" not in df.columns:
                rows_html += (
                    '<tr><td style="padding:11px 14px">'
                    '<div style="font-size:0.82rem;font-weight:800;color:' + tc + '">' + ticker + '</div>'
                    '<div style="font-size:0.62rem;color:' + C_TEXT3 + '">' + co + '</div>'
                    '</td>'
                    + '<td style="padding:11px 14px;color:' + C_TEXT3 + ';text-align:center">—</td>' * 4
                    + '</tr>'
                )
                continue

            close_vals = df["close"].dropna()
            if close_vals.empty:
                continue

            price     = float(close_vals.iloc[-1])
            chg       = _day_change_pct(df)
            chg_val   = chg or 0.0
            chg_str   = ("+" if chg_val >= 0 else "") + str(round(chg_val, 2)) + "%"
            chg_col   = C_HIGH if chg_val > 0 else (C_LOW if chg_val < 0 else C_TEXT2)
            wk_vals   = close_vals.tail(5).tolist()
            wk_chg    = ((wk_vals[-1] - wk_vals[0]) / abs(wk_vals[0]) * 100) if len(wk_vals) > 1 and wk_vals[0] != 0 else 0.0
            wk_col    = C_HIGH if wk_chg >= 0 else C_LOW
            spark_svg = _mini_sparkline_svg(wk_vals, wk_col, width=82, height=30)

            vol_str = "—"
            if "volume" in df.columns:
                vol_vals = df["volume"].dropna()
                if not vol_vals.empty:
                    vol = float(vol_vals.iloc[-1])
                    vol_str = (str(round(vol / 1e6, 2)) + "M") if vol >= 1e6 else (str(int(vol / 1e3)) + "K") if vol >= 1e3 else str(int(vol))

            wk_str = ("+" if wk_chg >= 0 else "") + str(round(wk_chg, 1)) + "%"
            arrow  = "\u25b2" if chg_val > 0 else ("\u25bc" if chg_val < 0 else "\u2014")

            rows_html += (
                '<tr style="border-bottom:1px solid rgba(255,255,255,0.05);'
                'transition:background 0.15s" '
                'onmouseover="this.style.background=\'rgba(255,255,255,0.025)\'" '
                'onmouseout="this.style.background=\'transparent\'">'

                # Ticker + company
                '<td style="padding:11px 14px;white-space:nowrap">'
                '<div style="font-size:0.84rem;font-weight:800;color:' + tc + '">' + ticker + '</div>'
                '<div style="font-size:0.62rem;color:' + C_TEXT3 + '">' + co + '</div>'
                '</td>'

                # Price
                '<td style="padding:11px 14px;text-align:right;font-family:monospace">'
                '<span style="font-size:0.92rem;font-weight:700;color:' + C_TEXT + '">$' + str(round(price, 2)) + '</span>'
                '</td>'

                # Day %
                '<td style="padding:11px 14px;text-align:right">'
                '<span style="font-size:0.82rem;font-weight:700;color:' + chg_col + '">' + arrow + ' ' + chg_str + '</span>'
                '</td>'

                # Volume
                '<td style="padding:11px 14px;text-align:center;font-size:0.72rem;color:' + C_TEXT2 + '">' + vol_str + '</td>'

                # Sparkline
                '<td style="padding:8px 14px;text-align:center">' + spark_svg + '</td>'

                # 5D change
                '<td style="padding:11px 14px;text-align:right">'
                '<span style="font-size:0.75rem;font-weight:600;color:' + wk_col + '">' + wk_str + ' 5D</span>'
                '</td>'

                '</tr>'
            )

        st.markdown(
            '<div style="background:' + C_CARD + ';border:1px solid ' + C_BORDER + ';'
            'border-radius:14px;overflow:hidden">'
            '<table style="width:100%;border-collapse:collapse">'
            '<thead><tr style="background:rgba(59,130,246,0.04)">'
            '<th style="' + th + ';text-align:left">Ticker / Company</th>'
            '<th style="' + th + ';text-align:right">Price</th>'
            '<th style="' + th + ';text-align:right">1D Chg</th>'
            '<th style="' + th + ';text-align:center">Volume</th>'
            '<th style="' + th + ';text-align:center">5D Spark</th>'
            '<th style="' + th + ';text-align:right">5D Chg</th>'
            '</tr></thead>'
            '<tbody>' + rows_html + '</tbody>'
            '</table>'
            '</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        st.caption("Equity grid unavailable.")


# ── Section 5: Freight Rate Heat Strip ────────────────────────────────────────


def _render_freight_heat_strip(freight_data: dict) -> None:
    """Plotly heatmap of all routes + summary stats row."""
    section_header(
        "Freight Rate Heat Strip",
        "All tracked routes — cell color = rate level (blue=low, amber=mid, red=high)  ·  hover for 30d delta",
    )

    try:
        if not freight_data:
            st.markdown(
                '<div style="background:' + C_CARD + ';border:1px solid ' + C_BORDER + ';'
                'border-radius:10px;padding:22px;text-align:center;color:' + C_TEXT3 + ';font-size:0.82rem">'
                'Freight data not available.'
                '</div>',
                unsafe_allow_html=True,
            )
            return

        route_rates: list[tuple[str, float, float | None]] = []
        for route_name, df in (freight_data or {}).items():
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            val_col = "value" if "value" in df.columns else df.columns[0]
            df2     = df.sort_values("date") if "date" in df.columns else df.copy()
            vals    = df2[val_col].dropna()
            if vals.empty:
                continue
            cur = float(vals.iloc[-1])
            pct30: float | None = None
            if "date" in df2.columns:
                ref  = df2["date"].max() - pd.Timedelta(days=30)
                mask = df2["date"] <= ref
                if mask.any():
                    ago = float(df2.loc[mask, val_col].dropna().iloc[-1])
                    if ago != 0:
                        pct30 = (cur - ago) / abs(ago) * 100
            route_rates.append((str(route_name), cur, pct30))

        if not route_rates:
            st.markdown(
                '<div style="color:' + C_TEXT3 + ';font-size:0.82rem;padding:14px">No route data available.</div>',
                unsafe_allow_html=True,
            )
            return

        all_rates   = [r[1] for r in route_rates]
        mn_r, mx_r  = min(all_rates), max(all_rates)
        route_labels = [r[0][:20] for r in route_rates]
        z_vals      = [[r[1]] for r in route_rates]
        hover_text  = []
        for rn, rv, rp in route_rates:
            rate_str = ("$" + "{:,.0f}".format(int(rv))) if rv > 500 else str(round(rv, 1))
            pct_str  = (("+" if (rp or 0) > 0 else "") + str(round(rp, 1)) + "%") if rp is not None else "n/a"
            hover_text.append([rn + "<br>Rate: " + rate_str + "<br>30D: " + pct_str])

        fig = go.Figure(go.Heatmap(
            z=z_vals,
            x=["Rate ($/FEU)"],
            y=route_labels,
            text=hover_text,
            hovertemplate="%{text}<extra></extra>",
            colorscale=[
                [0.00, "#1e3a8a"],
                [0.30, "#3b82f6"],
                [0.55, "#10b981"],
                [0.75, "#f59e0b"],
                [1.00, "#ef4444"],
            ],
            zmin=mn_r,
            zmax=mx_r,
            showscale=True,
            colorbar=dict(
                title=dict(text="$/FEU", font=dict(color=C_TEXT2, size=10)),
                thickness=10,
                len=0.9,
                tickfont=dict(color=C_TEXT2, size=10),
                outlinewidth=0,
            ),
        ))
        fig.update_layout(
            template="plotly_dark",
            height=max(160, len(route_rates) * 38 + 60),
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            margin=dict(t=10, b=10, l=160, r=80),
            xaxis=dict(side="top", tickfont=dict(color=C_TEXT2, size=10)),
            yaxis=dict(tickfont=dict(color=C_TEXT2, size=10), autorange="reversed"),
            font=dict(family="Inter, sans-serif"),
            hoverlabel=dict(bgcolor="#1a2235", bordercolor="rgba(255,255,255,0.15)", font=dict(color=C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="livefeed_freight_heat")

        # Summary stats row
        avg_rate  = sum(all_rates) / len(all_rates)
        max_route = route_rates[all_rates.index(max(all_rates))]
        min_route = route_rates[all_rates.index(min(all_rates))]
        rising    = sum(1 for _, _, p in route_rates if p is not None and p > 3)
        falling   = sum(1 for _, _, p in route_rates if p is not None and p < -3)
        stats = [
            ("Avg Rate",   ("$" + "{:,.0f}".format(int(avg_rate))) if avg_rate > 500 else str(round(avg_rate, 1)), C_TEXT2),
            ("Highest",    max_route[0][:16] + " $" + "{:,.0f}".format(int(max_route[1])), C_LOW),
            ("Lowest",     min_route[0][:16] + " $" + "{:,.0f}".format(int(min_route[1])), C_HIGH),
            ("Rising 30d", str(rising) + " routes", C_MOD),
            ("Falling 30d", str(falling) + " routes", C_HIGH),
        ]
        stat_html = '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:12px">'
        for label, val, col in stats:
            stat_html += (
                '<div style="flex:1;min-width:110px;background:' + _hex_to_rgba(col, 0.07) + ';'
                'border:1px solid ' + _hex_to_rgba(col, 0.20) + ';border-radius:10px;padding:10px 14px">'
                '<div style="font-size:0.58rem;font-weight:700;color:' + C_TEXT3 + ';'
                'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px">' + label + '</div>'
                '<div style="font-size:0.82rem;font-weight:700;color:' + col + '">' + val + '</div>'
                '</div>'
            )
        stat_html += '</div>'
        st.markdown(stat_html, unsafe_allow_html=True)

    except Exception:
        st.caption("Freight heat strip unavailable.")


# ── Section 6: Live Alert Feed ─────────────────────────────────────────────────


def _build_feed_items(
    insights: list,
    freight_data: dict,
    port_results: list,
    macro_data: dict,
    stock_data: dict,
) -> list[dict[str, Any]]:
    """Collect and prioritize all alert feed items."""
    items: list[dict[str, Any]] = []

    # 1. Insights
    for ins in (insights or []):
        score = getattr(ins, "score", 0.5)
        if score >= 0.75:
            priority, badge_text = _PRIORITY_CRITICAL, "CRITICAL"
        elif score >= 0.60:
            priority, badge_text = _PRIORITY_HIGH, "HIGH"
        elif score >= 0.45:
            priority, badge_text = _PRIORITY_MODERATE, "MODERATE"
        else:
            priority, badge_text = _PRIORITY_LOW, "LOW"
        cat        = getattr(ins, "category", "ROUTE")
        detail_str = getattr(ins, "detail", "")
        if len(detail_str) > 120:
            detail_str = detail_str[:117] + "..."
        items.append(_make_feed_item(
            category=cat,
            title=getattr(ins, "title", "Insight"),
            detail=detail_str,
            priority=priority,
            badge_text=str(int(score * 100)) + "% · " + getattr(ins, "action", "Monitor"),
            badge_color=_CAT_COLORS.get(cat, C_ACCENT),
            source=_CAT_LABELS.get(cat, cat),
        ))

    # 2. Freight rate alerts (>10% 30d move)
    for route_name, df in (freight_data or {}).items():
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        val_col = "value" if "value" in df.columns else df.columns[0]
        df2     = df.copy()
        if "date" in df2.columns:
            df2 = df2.sort_values("date")
        vals = df2[val_col].dropna()
        if len(vals) < 2:
            continue
        cur   = float(vals.iloc[-1])
        pct30: float | None = None
        if "date" in df2.columns:
            ref  = df2["date"].max() - pd.Timedelta(days=30)
            mask = df2["date"] <= ref
            if mask.any():
                ago = float(df2.loc[mask, val_col].dropna().iloc[-1])
                if ago != 0:
                    pct30 = (cur - ago) / abs(ago) * 100
        if pct30 is None or abs(pct30) <= 10:
            continue
        direction  = "up" if pct30 > 0 else "down"
        badge_col  = C_LOW if pct30 > 0 else C_HIGH
        priority   = _PRIORITY_CRITICAL if abs(pct30) > 20 else _PRIORITY_HIGH
        pct_str    = ("+" if pct30 > 0 else "") + str(round(pct30, 1)) + "%"
        rate_str   = ("$" + "{:,.0f}".format(int(cur)) + "/FEU") if cur > 500 else str(round(cur, 1))
        items.append(_make_feed_item(
            category="RATE_ALERT",
            title="RATE ALERT: " + str(route_name)[:20] + " " + direction + " " + pct_str + " in 30d",
            detail="Current rate: " + rate_str + " · 30-day change: " + pct_str,
            priority=priority,
            badge_text=pct_str,
            badge_color=badge_col,
            source="FREIGHT",
        ))

    # 3. High-demand ports (score >= 0.65)
    for pr in (port_results or []):
        sc       = getattr(pr, "demand_score", 0.0)
        has_data = getattr(pr, "has_real_data", False)
        if not has_data or sc < 0.65:
            continue
        port_name = getattr(pr, "port_name", "Unknown")
        trend     = getattr(pr, "demand_trend", "Stable")
        priority  = _PRIORITY_HIGH if sc >= 0.75 else _PRIORITY_MODERATE
        items.append(_make_feed_item(
            category="PORT_DEMAND",
            title="HIGH DEMAND: " + port_name + " " + str(int(sc * 100)) + "% score",
            detail="Trend: " + trend + " · Region: " + getattr(pr, "region", "—"),
            priority=priority,
            badge_text=str(int(sc * 100)) + "% demand",
            badge_color=C_HIGH if sc >= 0.75 else C_MOD,
            source="PORT",
        ))

    # 4. BDI macro signal
    bdi_df = (macro_data or {}).get("BSXRLM")
    if bdi_df is not None and not bdi_df.empty and "value" in bdi_df.columns:
        bdi_sorted = bdi_df.sort_values("date") if "date" in bdi_df.columns else bdi_df
        bdi_vals   = bdi_sorted["value"].dropna()
        if not bdi_vals.empty:
            bdi_cur  = float(bdi_vals.iloc[-1])
            bdi_pct  = _pct_change_30d_df(bdi_sorted if "date" in bdi_sorted.columns else bdi_df)
            bdi_pstr = (("+" if (bdi_pct or 0) > 0 else "") + str(round(bdi_pct, 1)) + "% 30d") if bdi_pct else ""
            items.append(_make_feed_item(
                category="BDI",
                title="Baltic Dry Index: " + "{:,.0f}".format(int(bdi_cur)) + (" " + bdi_pstr if bdi_pstr else ""),
                detail="Global dry bulk shipping bellwether",
                priority=_PRIORITY_MODERATE,
                badge_text="BDI",
                badge_color=C_MACRO,
                source="MACRO",
            ))

    # 5. Stock moves >= 2%
    for ticker in _SHIPPING_TICKERS:
        df = (stock_data or {}).get(ticker)
        if df is None or df.empty or "close" not in df.columns:
            continue
        chg = _day_change_pct(df)
        if chg is None or abs(chg) < 2.0:
            continue
        direction = "up" if chg > 0 else "down"
        priority  = _PRIORITY_HIGH if abs(chg) >= 4 else _PRIORITY_MODERATE
        items.append(_make_feed_item(
            category="STOCK_MOVE",
            title=ticker + " (" + _COMPANY_NAMES.get(ticker, ticker) + ") " + direction + " " + str(round(abs(chg), 2)) + "%",
            detail="Day change: " + ("+" if chg > 0 else "") + str(round(chg, 2)) + "%",
            priority=priority,
            badge_text=("+" if chg > 0 else "") + str(round(chg, 2)) + "%",
            badge_color=C_HIGH if chg > 0 else C_LOW,
            source="EQUITY",
        ))

    return sorted(items, key=lambda x: x["priority"])


def _render_alert_feed(
    port_results: list,
    freight_data: dict,
    stock_data: dict,
    macro_data: dict,
    insights: list,
) -> None:
    """Live alert feed with animated pulse borders on critical alerts."""
    section_header(
        "Live Alert Feed",
        "Prioritized alerts — critical signals pulse in real time",
    )

    try:
        items = _build_feed_items(insights, freight_data, port_results, macro_data, stock_data)

        if not items:
            st.markdown(
                '<div style="background:' + C_CARD + ';border:1px solid ' + C_BORDER + ';'
                'border-radius:12px;padding:28px;text-align:center;'
                'color:' + C_TEXT3 + ';font-size:0.82rem">'
                'No alerts at this time — data pipeline may not have run yet.'
                '</div>',
                unsafe_allow_html=True,
            )
            return

        # Filter controls
        col_f1, col_f2 = st.columns([1, 3])
        with col_f1:
            priority_filter = st.selectbox(
                "Priority filter",
                ["All", "Critical", "High", "Moderate", "Low"],
                key="livefeed_alert_priority_filter",
            )
        pmap = {"All": -1, "Critical": 0, "High": 1, "Moderate": 2, "Low": 3}
        p_thresh = pmap.get(priority_filter, -1)
        filtered = items if p_thresh < 0 else [x for x in items if x["priority"] <= p_thresh]

        if not filtered:
            st.markdown(
                '<div style="color:' + C_TEXT3 + ';font-size:0.80rem;padding:14px">No alerts match the selected priority filter.</div>',
                unsafe_allow_html=True,
            )
            return

        feed_html = '<div style="display:flex;flex-direction:column;gap:8px">'
        for item in filtered[:20]:
            color      = item["color"]
            is_crit    = item["priority"] == _PRIORITY_CRITICAL
            is_high    = item["priority"] == _PRIORITY_HIGH
            pulse_anim = ("animation:pulse-ring 1.8s infinite;" if is_crit else
                          "animation:pulse-ring-amber 2.5s infinite;" if is_high else "")
            border_width = "2px" if is_crit or is_high else "1px"
            bg_alpha    = 0.09 if is_crit else (0.06 if is_high else 0.04)
            feed_html += (
                '<div style="background:' + _hex_to_rgba(color, bg_alpha) + ';'
                'border:' + border_width + ' solid ' + _hex_to_rgba(color, 0.35) + ';'
                'border-left:4px solid ' + color + ';border-radius:10px;padding:12px 16px;'
                + pulse_anim + '">'
                '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px">'
                '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">'
                '<span style="font-size:0.60rem;font-weight:800;color:' + color + ';'
                'text-transform:uppercase;letter-spacing:0.09em;'
                'background:' + _hex_to_rgba(color, 0.18) + ';padding:2px 9px;border-radius:999px">'
                + _CAT_LABELS.get(item["category"], item["category"]) + '</span>'
                '<span style="font-size:0.60rem;color:' + C_TEXT3 + ';font-family:monospace">'
                + item["ts"] + '</span>'
                + ('<span style="font-size:0.58rem;font-weight:900;color:' + C_LOW + ';'
                   'background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.30);'
                   'padding:1px 8px;border-radius:999px;letter-spacing:0.08em">CRITICAL</span>'
                   if is_crit else "")
                + '</div>'
                '<span style="font-size:0.70rem;font-weight:700;color:' + item["badge_color"] + ';'
                'background:' + _hex_to_rgba(item["badge_color"], 0.12) + ';'
                'padding:2px 10px;border-radius:999px;flex-shrink:0;margin-left:10px">'
                + item["badge_text"] + '</span>'
                '</div>'
                '<div style="font-size:0.82rem;font-weight:600;color:' + C_TEXT + ';line-height:1.35;margin-bottom:3px">'
                + item["title"] + '</div>'
                + ('<div style="font-size:0.70rem;color:' + C_TEXT3 + ';line-height:1.4">' + item["detail"][:100] + '</div>'
                   if item["detail"] else "")
                + '</div>'
            )
        feed_html += '</div>'
        st.markdown(feed_html, unsafe_allow_html=True)

        if len(filtered) > 20:
            st.caption("Showing 20 of " + str(len(filtered)) + " alerts. Adjust priority filter to narrow results.")

    except Exception:
        st.caption("Alert feed unavailable.")


# ── Section 7: Port Demand Heatmap (preserved) ────────────────────────────────


def _render_port_heatmap(port_results: list) -> None:
    """Plotly heatmap grid — one cell per port, color = demand score."""
    section_header(
        "Port Demand Heatmap",
        "Color intensity = demand score  ·  hover for detail  ·  expand to inspect a port",
    )

    try:
        if not port_results:
            st.markdown(
                '<div style="background:' + C_CARD + ';border:1px solid ' + C_BORDER + ';'
                'border-radius:12px;padding:26px;text-align:center">'
                '<div style="color:' + C_TEXT2 + ';font-size:0.88rem">Port data not available.</div>'
                '</div>',
                unsafe_allow_html=True,
            )
            return

        sorted_ports  = sorted(port_results, key=lambda p: getattr(p, "demand_score", 0.0), reverse=True)
        port_names    = [getattr(p, "port_name", "?")     for p in sorted_ports]
        demand_scores = [getattr(p, "demand_score", 0.0)  for p in sorted_ports]
        locodes       = [getattr(p, "locode", "")         for p in sorted_ports]

        n_cols = 5
        n = len(sorted_ports)
        n_rows = math.ceil(n / n_cols)
        pad    = n_rows * n_cols - n
        pnames = port_names  + [""] * pad
        pscores= demand_scores + [0.0] * pad

        z_rows, text_rows, hover_rows = [], [], []
        for r in range(n_rows):
            z_rows.append(pscores[r * n_cols:(r + 1) * n_cols])
            text_rows.append(pnames[r * n_cols:(r + 1) * n_cols])
            hr = []
            for c in range(n_cols):
                idx = r * n_cols + c
                if idx < n:
                    p  = sorted_ports[idx]
                    sc = getattr(p, "demand_score", 0.0)
                    hr.append(
                        getattr(p, "port_name", "?")
                        + "<br>Score: " + str(int(sc * 100)) + "%"
                        + "<br>Label: " + getattr(p, "demand_label", "—")
                        + "<br>Trend: " + getattr(p, "demand_trend", "—")
                        + "<br>LOCODE: " + getattr(p, "locode", "?")
                    )
                else:
                    hr.append("")
            hover_rows.append(hr)

        COLORSCALE = [
            [0.00, "#1e3a5f"], [0.25, "#3b82f6"],
            [0.50, "#10b981"], [0.75, "#f59e0b"],
            [1.00, "#ef4444"],
        ]
        fig = go.Figure(go.Heatmap(
            z=z_rows, text=text_rows, customdata=hover_rows,
            texttemplate="%{text}", textfont=dict(size=9, color="rgba(255,255,255,0.9)"),
            colorscale=COLORSCALE, zmin=0, zmax=1,
            hovertemplate="%{customdata}<extra></extra>",
            colorbar=dict(
                title=dict(text="Demand", font=dict(color=C_TEXT2, size=11)),
                thickness=12, len=0.8,
                tickfont=dict(color=C_TEXT2, size=10),
                tickformat=".0%", outlinewidth=0,
            ),
        ))
        fig.update_layout(
            template="plotly_dark",
            height=max(200, n_rows * 60 + 60),
            paper_bgcolor=C_BG, plot_bgcolor=C_BG,
            margin=dict(t=10, b=10, l=10, r=80),
            xaxis=dict(visible=False),
            yaxis=dict(visible=False, autorange="reversed"),
            font=dict(family="Inter, sans-serif"),
            hoverlabel=dict(bgcolor="#1a2235", bordercolor="rgba(255,255,255,0.15)", font=dict(color="#f1f5f9", size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="livefeed_port_heatmap")

        with st.expander("Inspect a port", expanded=False):
            port_display_names = [getattr(p, "port_name", "?") for p in sorted_ports]
            selected_name = st.selectbox("Select port", port_display_names, key="livefeed_port_select")
            match = next((p for p in sorted_ports if getattr(p, "port_name", "") == selected_name), None)
            if match is not None:
                sc     = getattr(match, "demand_score", 0.0)
                sc_col = C_HIGH if sc >= 0.65 else (C_MOD if sc >= 0.35 else C_LOW)
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    st.markdown(
                        '<div style="background:#0d1117;border:1px solid rgba(255,255,255,0.08);'
                        'border-top:3px solid ' + sc_col + ';border-radius:10px;padding:12px 14px">'
                        '<div style="font-size:0.62rem;color:' + C_TEXT3 + ';text-transform:uppercase;'
                        'letter-spacing:0.07em;margin-bottom:4px">Demand Score</div>'
                        '<div style="font-size:1.6rem;font-weight:800;color:' + C_TEXT + '">'
                        + str(int(sc * 100)) + '%</div>'
                        '<div style="font-size:0.70rem;color:' + C_TEXT2 + ';margin-top:2px">'
                        + getattr(match, "demand_label", "—") + " · " + getattr(match, "demand_trend", "—") + '</div>'
                        '</div>', unsafe_allow_html=True,
                    )
                with col_b:
                    vessels  = getattr(match, "vessel_count", None)
                    has_ais  = getattr(match, "has_real_data", False) and vessels is not None
                    vd_str   = str(vessels) if has_ais else "—"
                    ais_sub  = getattr(match, "region", "—") if has_ais else "&#128674; AIS data unavailable"
                    st.markdown(
                        '<div style="background:#0d1117;border:1px solid rgba(255,255,255,0.08);'
                        'border-radius:10px;padding:12px 14px">'
                        '<div style="font-size:0.62rem;color:' + C_TEXT3 + ';text-transform:uppercase;'
                        'letter-spacing:0.07em;margin-bottom:4px">Vessels (AIS)</div>'
                        '<div style="font-size:1.6rem;font-weight:800;color:' + C_TEXT + '">' + vd_str + '</div>'
                        '<div style="font-size:0.70rem;color:' + C_TEXT2 + ';margin-top:2px">' + ais_sub + '</div>'
                        '</div>', unsafe_allow_html=True,
                    )
                with col_c:
                    teu     = getattr(match, "throughput_teu_m", 0.0)
                    teu_str = str(round(teu, 1)) + "M TEU/yr" if teu > 0 else "—"
                    st.markdown(
                        '<div style="background:#0d1117;border:1px solid rgba(255,255,255,0.08);'
                        'border-radius:10px;padding:12px 14px">'
                        '<div style="font-size:0.62rem;color:' + C_TEXT3 + ';text-transform:uppercase;'
                        'letter-spacing:0.07em;margin-bottom:4px">Throughput</div>'
                        '<div style="font-size:1.6rem;font-weight:800;color:' + C_TEXT + '">' + teu_str + '</div>'
                        '<div style="font-size:0.70rem;color:' + C_TEXT2 + ';margin-top:2px">'
                        + getattr(match, "locode", "—") + " · " + getattr(match, "country_iso3", "—") + '</div>'
                        '</div>', unsafe_allow_html=True,
                    )
    except Exception:
        st.caption("Port heatmap unavailable.")


# ── Section 8: Macro Pulse Panel ──────────────────────────────────────────────


def _render_macro_pulse_panel(macro_data: dict) -> None:
    """Key macro indicators as large stat cards with trend arrows."""
    section_header(
        "Macro Pulse Panel",
        "Key economic indicators driving shipping demand — BDI · PMI · Fuel · PPI · Manufacturing",
    )

    try:
        if not macro_data:
            st.markdown(
                '<div style="background:' + C_CARD + ';border:1px solid ' + C_BORDER + ';'
                'border-radius:12px;padding:22px;text-align:center;color:' + C_TEXT3 + ';font-size:0.82rem">'
                'Macro data not available — run FRED data pipeline.'
                '</div>',
                unsafe_allow_html=True,
            )
            return

        cols = st.columns(len(_MACRO_ITEMS), gap="small")
        for idx, (series_id, label, abbr, color) in enumerate(_MACRO_ITEMS):
            df = (macro_data or {}).get(series_id)
            with cols[idx]:
                try:
                    if df is None or df.empty or "value" not in df.columns:
                        st.markdown(
                            '<div style="background:' + _hex_to_rgba(color, 0.07) + ';'
                            'border:1px solid ' + _hex_to_rgba(color, 0.18) + ';border-radius:12px;padding:14px 16px;height:110px;'
                            'display:flex;flex-direction:column;justify-content:center;align-items:center">'
                            '<div style="font-size:0.60rem;font-weight:800;color:' + color + ';letter-spacing:0.10em">' + abbr + '</div>'
                            '<div style="font-size:0.68rem;color:' + C_TEXT3 + ';text-align:center;margin-top:4px">' + label[:24] + '</div>'
                            '<div style="font-size:0.72rem;color:' + C_TEXT3 + ';margin-top:6px">No data</div>'
                            '</div>',
                            unsafe_allow_html=True,
                        )
                        continue

                    df2  = df.sort_values("date") if "date" in df.columns else df
                    vals = df2["value"].dropna()
                    cur  = float(vals.iloc[-1])
                    pct  = _pct_change_30d_df(df2 if "date" in df2.columns else df)

                    if abs(cur) >= 10000:
                        val_str = "{:,.0f}".format(int(cur))
                    elif abs(cur) >= 1000:
                        val_str = "{:,.0f}".format(int(cur))
                    elif abs(cur) >= 10:
                        val_str = str(round(cur, 1))
                    else:
                        val_str = str(round(cur, 2))

                    if pct is None:
                        arrow, pct_str, trend_col = "\u2014", "n/a", C_TEXT3
                    elif pct > 2:
                        arrow, pct_str, trend_col = "\u25b2", "+" + str(round(pct, 1)) + "%", C_HIGH
                    elif pct < -2:
                        arrow, pct_str, trend_col = "\u25bc", str(round(pct, 1)) + "%", C_LOW
                    else:
                        arrow, pct_str, trend_col = "\u2014", str(round(pct, 1)) + "%", C_TEXT2

                    st.markdown(
                        '<div style="background:' + _hex_to_rgba(color, 0.08) + ';'
                        'border:1px solid ' + _hex_to_rgba(color, 0.22) + ';'
                        'border-top:3px solid ' + color + ';border-radius:12px;padding:14px 16px">'
                        '<div style="font-size:0.58rem;font-weight:800;color:' + color + ';'
                        'text-transform:uppercase;letter-spacing:0.12em;margin-bottom:2px">' + abbr + '</div>'
                        '<div style="font-size:0.60rem;color:' + C_TEXT3 + ';margin-bottom:8px;line-height:1.3">'
                        + label + '</div>'
                        '<div style="font-size:1.6rem;font-weight:800;color:' + C_TEXT + ';'
                        'font-variant-numeric:tabular-nums;line-height:1.1">'
                        + val_str + '</div>'
                        '<div style="display:flex;align-items:center;gap:6px;margin-top:5px">'
                        '<span style="font-size:0.72rem;font-weight:700;color:' + trend_col + '">'
                        + arrow + ' ' + pct_str + '</span>'
                        '<span style="font-size:0.60rem;color:' + C_TEXT3 + '">30d</span>'
                        '</div>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                except Exception:
                    st.caption(label[:20] + " unavailable")
    except Exception:
        st.caption("Macro pulse panel unavailable.")


# ── Section 9: Refresh Controls + Freshness Detail Grid ───────────────────────


def _render_refresh_controls() -> None:
    """Refresh slider, manual trigger, and per-source freshness detail grid."""
    section_header(
        "Refresh Controls & Data Freshness",
        "Manual refresh trigger · auto-refresh cadence · per-source freshness detail",
    )

    try:
        col_slider, col_btn, col_status = st.columns([3, 1, 2])
        with col_slider:
            interval_min = st.slider(
                "Auto-refresh interval (minutes)",
                min_value=1, max_value=60, value=15, step=1,
                key="livefeed_refresh_interval",
                help="Target refresh cadence — use Refresh Now to trigger immediately.",
            )
        with col_btn:
            st.markdown("<div style='margin-top:28px'>", unsafe_allow_html=True)
            if st.button("Refresh Now", key="livefeed_manual_refresh", use_container_width=True):
                st.cache_data.clear()
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
        with col_status:
            nc = C_HIGH if interval_min > 15 else (C_MOD if interval_min > 5 else C_LOW)
            st.markdown(
                '<div style="margin-top:28px;background:#0d1117;border:1px solid rgba(255,255,255,0.07);'
                'border-radius:8px;padding:9px 14px;font-size:0.75rem">'
                '<div style="color:' + C_TEXT3 + ';font-size:0.62rem;margin-bottom:3px;'
                'text-transform:uppercase;letter-spacing:0.06em">Configured cadence</div>'
                '<div style="color:' + nc + ';font-weight:700;font-size:0.90rem">' + str(interval_min) + ' min</div>'
                '<div style="color:' + C_TEXT3 + ';font-size:0.62rem;margin-top:2px">Click Refresh Now to trigger now</div>'
                '</div>',
                unsafe_allow_html=True,
            )
    except Exception:
        pass

    # Freshness detail grid
    try:
        st.markdown("<div style='margin-top:16px'>", unsafe_allow_html=True)
        mtimes = _cache_mtimes()
        source_display = {
            "stocks":    ("Equities",       "1h",  _TICKER_COLORS["ZIM"]),
            "freight":   ("Freight Rates",  "24h", "#f59e0b"),
            "fred":      ("Macro (FRED)",   "24h", C_MACRO),
            "ais":       ("AIS Vessels",    "6h",  C_ACCENT),
            "comtrade":  ("Trade Data",     "7d",  C_CONV),
            "worldbank": ("Port Data",      "7d",  C_HIGH),
        }
        grid_html = (
            '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:4px">'
        )
        for bucket, (label, ttl_hint, color) in source_display.items():
            mtime   = mtimes.get(bucket)
            status  = _source_status_color(mtime, _TTL_HOURS[bucket])
            age     = _source_age_str(mtime)
            pct     = _source_freshness_pct(mtime, _TTL_HOURS[bucket])
            bar_w   = str(int(pct * 100)) + "%"
            ttl_s   = _TTL_HOURS[bucket] * 3600
            if mtime is not None:
                remaining_m = max(0, int((ttl_s - (time.time() - mtime)) / 60))
                next_str = str(remaining_m) + "m remaining"
            else:
                next_str = "not cached"
            grid_html += (
                '<div style="background:' + _hex_to_rgba(status, 0.06) + ';'
                'border:1px solid ' + _hex_to_rgba(status, 0.20) + ';border-radius:10px;padding:12px 14px">'
                '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
                '<div style="display:flex;align-items:center;gap:6px">'
                '<span style="width:8px;height:8px;border-radius:50%;background:' + status + ';display:inline-block"></span>'
                '<span style="font-size:0.72rem;font-weight:700;color:' + C_TEXT + '">' + label + '</span>'
                '</div>'
                '<span style="font-size:0.60rem;font-weight:600;color:' + status + ';'
                'background:' + _hex_to_rgba(status, 0.12) + ';padding:1px 7px;border-radius:999px">'
                + ttl_hint + ' TTL</span>'
                '</div>'
                '<div style="height:4px;border-radius:999px;background:rgba(255,255,255,0.06);margin-bottom:6px">'
                '<div style="height:100%;width:' + bar_w + ';border-radius:999px;background:' + status + '"></div>'
                '</div>'
                '<div style="display:flex;justify-content:space-between">'
                '<span style="font-size:0.62rem;color:' + C_TEXT3 + '">' + age + '</span>'
                '<span style="font-size:0.62rem;color:' + C_TEXT3 + '">' + next_str + '</span>'
                '</div>'
                '</div>'
            )
        grid_html += '</div>'
        st.markdown(grid_html, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
    except Exception:
        pass

    # Info note
    try:
        st.markdown(
            '<div style="background:rgba(59,130,246,0.06);border:1px solid rgba(59,130,246,0.16);'
            'border-radius:8px;padding:10px 14px;margin-top:12px;font-size:0.73rem;color:' + C_TEXT2 + '">'
            '<strong style="color:' + C_ACCENT + '">Note:</strong> '
            'Streamlit does not support background timers natively. Use the sidebar '
            '<em>Refresh All Data</em> button, or configure an external scheduler (cron + cache invalidation) '
            'to match the configured interval above.'
            '</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass


# ── Signal Stream (legacy, preserved) ─────────────────────────────────────────


def _render_signal_stream(
    insights: list,
    freight_data: dict,
    port_results: list,
    macro_data: dict,
    stock_data: dict,
) -> None:
    """Legacy compact signal stream table."""
    section_header(
        "Signal Stream",
        "All signals sorted by priority — compact view for quick scanning",
    )
    try:
        items = _build_feed_items(insights, freight_data, port_results, macro_data, stock_data)
        if not items:
            st.markdown(
                '<div style="color:' + C_TEXT3 + ';font-size:0.80rem;padding:14px">'
                'No signals — load data to populate.'
                '</div>',
                unsafe_allow_html=True,
            )
            return

        th = (
            "padding:8px 12px;font-size:0.58rem;font-weight:700;color:" + C_TEXT3 + ";"
            "text-transform:uppercase;letter-spacing:0.09em;border-bottom:1px solid rgba(255,255,255,0.07)"
        )
        rows = ""
        for item in items[:25]:
            color     = item["color"]
            badge_col = item["badge_color"]
            rows += (
                '<tr style="border-bottom:1px solid rgba(255,255,255,0.04)">'
                '<td style="padding:9px 12px;white-space:nowrap">'
                '<span style="font-size:0.62rem;font-weight:700;color:' + color + ';'
                'background:' + _hex_to_rgba(color, 0.14) + ';padding:2px 8px;border-radius:999px">'
                + _CAT_LABELS.get(item["category"], item["category"]) + '</span>'
                '</td>'
                '<td style="padding:9px 12px;font-size:0.76rem;color:' + C_TEXT + ';font-weight:600">'
                + item["title"][:60] + '</td>'
                '<td style="padding:9px 12px;font-size:0.68rem;color:' + C_TEXT3 + '">'
                + item["detail"][:50] + '</td>'
                '<td style="padding:9px 12px;text-align:right;white-space:nowrap">'
                '<span style="font-size:0.68rem;font-weight:700;color:' + badge_col + '">'
                + item["badge_text"] + '</span>'
                '</td>'
                '<td style="padding:9px 12px;font-size:0.62rem;color:' + C_TEXT3 + ';'
                'font-family:monospace;white-space:nowrap;text-align:right">'
                + item["ts"] + '</td>'
                '</tr>'
            )
        st.markdown(
            '<div style="background:' + C_CARD + ';border:1px solid ' + C_BORDER + ';border-radius:12px;overflow:hidden">'
            '<table style="width:100%;border-collapse:collapse">'
            '<thead><tr style="background:rgba(255,255,255,0.02)">'
            '<th style="' + th + ';text-align:left">Category</th>'
            '<th style="' + th + ';text-align:left">Signal</th>'
            '<th style="' + th + ';text-align:left">Detail</th>'
            '<th style="' + th + ';text-align:right">Badge</th>'
            '<th style="' + th + ';text-align:right">Time</th>'
            '</tr></thead>'
            '<tbody>' + rows + '</tbody>'
            '</table>'
            '</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        st.caption("Signal stream unavailable.")


# ── Divider helper ─────────────────────────────────────────────────────────────

def _hr() -> None:
    st.markdown(
        "<hr style='border:none;border-top:1px solid rgba(255,255,255,0.07);margin:26px 0'>",
        unsafe_allow_html=True,
    )


# ── Main render function ───────────────────────────────────────────────────────


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
    # Global animations and CSS
    try:
        _inject_global_styles()
    except Exception:
        pass

    # ── 1. Animated Hero Status Bar ───────────────────────────────────────
    try:
        _render_hero_status_bar()
    except Exception:
        st.caption("Live Intelligence Feed  ·  " + _now_ts_str())

    _hr()

    # ── 2. Three-Column Real-Time Stream ──────────────────────────────────
    try:
        _render_three_col_stream(port_results, freight_data, insights)
    except Exception:
        pass

    _hr()

    # ── 3. Vertical Activity Timeline ─────────────────────────────────────
    try:
        _render_activity_timeline(port_results, freight_data, stock_data, macro_data, insights)
    except Exception:
        pass

    _hr()

    # ── 4. Shipping Equity Grid ───────────────────────────────────────────
    try:
        _render_equity_grid(stock_data)
    except Exception:
        pass

    _hr()

    # ── 5. Freight Rate Heat Strip ────────────────────────────────────────
    try:
        _render_freight_heat_strip(freight_data)
    except Exception:
        pass

    _hr()

    # ── 6. Live Alert Feed ────────────────────────────────────────────────
    try:
        _render_alert_feed(port_results, freight_data, stock_data, macro_data, insights)
    except Exception:
        pass

    _hr()

    # ── 7. Port Demand Heatmap (preserved) ───────────────────────────────
    try:
        _render_port_heatmap(port_results)
    except Exception:
        pass

    _hr()

    # ── 8. Macro Pulse Panel ──────────────────────────────────────────────
    try:
        _render_macro_pulse_panel(macro_data)
    except Exception:
        pass

    _hr()

    # ── 9. Signal Stream (legacy compact table) ───────────────────────────
    try:
        _render_signal_stream(insights, freight_data, port_results, macro_data, stock_data)
    except Exception:
        pass

    _hr()

    # ── 10. Refresh Controls + Freshness Detail Grid ──────────────────────
    try:
        _render_refresh_controls()
    except Exception:
        pass
