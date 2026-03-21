"""Data Quality & Health Monitoring tab — enhanced.

render(port_results, route_results, freight_data, macro_data,
       stock_data, trade_data, ais_data=None)

Sections
--------
1.  Page header + refresh button
2.  Overview scorecard banner   — quality score, coverage %, freshness %, completeness %
3.  Data source cards           — per-source status badge, timestamp, records, freshness gauge, coverage
4.  Data pipeline flow diagram  — Sankey showing sources → processing → analysis layers
5.  Data quality metrics table  — null rate, outlier rate, schema compliance per source
6.  Historical availability     — calendar heatmap (date × source)
7.  Anomaly detector            — flags suspicious values / sudden changes
8.  Cache status table          — size, age, TTL remaining per dataset
9.  Data dependency graph       — which analyses depend on which sources
10. Cache management panel      — clear stale / clear all buttons + file listing

NEW sections (prepended)
-------------------------
A.  Data health KPI cards       — overall quality score, coverage %, freshness %, completeness %
B.  Source status card grid     — each source as a card with status badge, records, freshness gauge
C.  Pipeline flow HTML diagram  — simple HTML/CSS flow diagram (Sources → Validation → Cache → Analysis)
D.  Quality metrics table       — null rate, outlier rate per source
E.  Historical availability heatmap — dates × sources (green=complete, yellow=partial, red=missing)
F.  Cache status table          — size, age, TTL remaining per dataset
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ── Colour palette (shared with the rest of the platform) ────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_CARD2   = "#0f1929"
C_BORDER  = "rgba(255,255,255,0.08)"
C_BORDER2 = "rgba(255,255,255,0.04)"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"
C_HIGH    = "#10b981"   # green
C_WARN    = "#f59e0b"   # amber
C_DANGER  = "#ef4444"   # red
C_ACCENT  = "#3b82f6"   # blue
C_PURPLE  = "#8b5cf6"
C_CYAN    = "#06b6d4"
C_MONO    = "'SF Mono', 'Menlo', 'Courier New', monospace"

# ── Cache directory ───────────────────────────────────────────────────────────
_CACHE_DIR = Path(__file__).parent.parent / "cache"

# ── Source metadata ───────────────────────────────────────────────────────────
_SOURCE_META: dict[str, dict] = {
    "yfinance": {
        "icon": "📈",
        "label": "Stock Prices",
        "full_label": "yfinance / Yahoo Finance",
        "pattern": "*stock*",
        "ttl_hours": 1,
        "vintage": "Intraday / daily",
        "entity_key": "stocks",
        "color": C_ACCENT,
        "description": "Daily OHLCV data for shipping carrier equities (ZIM, MATX, DAC …)",
        "schema_fields": ["date", "open", "high", "low", "close", "volume"],
    },
    "FRED": {
        "icon": "🏦",
        "label": "Macro / FRED",
        "full_label": "Federal Reserve FRED",
        "pattern": "*fred*",
        "ttl_hours": 24,
        "vintage": "Weekly releases",
        "entity_key": "macro",
        "color": C_CYAN,
        "description": "800k+ macroeconomic series: BDI, ISM, CPI, retail sales …",
        "schema_fields": ["date", "value", "series_id"],
    },
    "WorldBank": {
        "icon": "🌍",
        "label": "World Bank",
        "full_label": "World Bank Open Data",
        "pattern": "*worldbank*",
        "ttl_hours": 168,
        "vintage": "2023 annual data",
        "entity_key": "wb",
        "color": C_HIGH,
        "description": "Annual container throughput (TEU) for 180+ ports / countries",
        "schema_fields": ["country", "year", "value", "indicator"],
    },
    "Trade/WITS": {
        "icon": "🔄",
        "label": "Trade (WITS)",
        "full_label": "WITS / Comtrade",
        "pattern": "*wits*",
        "ttl_hours": 168,
        "vintage": "Monthly releases",
        "entity_key": "trade",
        "color": C_PURPLE,
        "description": "Bilateral merchandise trade flows: HS-level import / export data",
        "schema_fields": ["reporter", "partner", "year", "value_usd", "hs_code"],
    },
    "Freight/FBX": {
        "icon": "🚢",
        "label": "Freight (FBX)",
        "full_label": "Freightos Baltic Index",
        "pattern": "*fbx*",
        "ttl_hours": 24,
        "vintage": "Daily index",
        "entity_key": "freight",
        "color": C_WARN,
        "description": "Global container spot rates: FBX01 Trans-Pac, FBX11 Asia-Eur …",
        "schema_fields": ["date", "route", "rate_usd_per_feu"],
    },
    "AIS/Synthetic": {
        "icon": "📡",
        "label": "AIS Positions",
        "full_label": "AIS Vessel Positions",
        "pattern": "*ais*",
        "ttl_hours": 6,
        "vintage": "Synthetic baselines",
        "entity_key": "ais",
        "color": C_DANGER,
        "description": "Vessel position / congestion data — currently synthetic baselines",
        "schema_fields": ["port", "vessels_waiting", "avg_wait_hours", "timestamp"],
    },
}

_TRACKED_PORTS: list[str] = [
    "Shanghai", "Singapore", "Rotterdam", "Los Angeles", "Hamburg",
    "Antwerp", "Ningbo", "Shenzhen", "Busan", "Hong Kong",
    "Qingdao", "Guangzhou", "Tianjin", "Xiamen", "Kaohsiung",
    "Port Klang", "Dubai", "Jeddah", "New York", "Savannah",
    "Long Beach", "Tokyo", "Jakarta", "Chennai", "Mumbai",
]

_COV_SOURCES = ["Trade", "AIS", "WorldBank", "Freight"]

# Which analyses depend on which sources
_DEPENDENCY_MAP: dict[str, list[str]] = {
    "Port Demand":      ["Trade/WITS", "WorldBank", "AIS/Synthetic"],
    "Route Optimizer":  ["Freight/FBX", "AIS/Synthetic"],
    "Freight Alpha":    ["Freight/FBX", "yfinance", "FRED"],
    "Market Analysis":  ["yfinance", "FRED"],
    "Trade War Risk":   ["Trade/WITS", "FRED"],
    "Geopolitical":     ["AIS/Synthetic", "Trade/WITS"],
    "Supply Chain":     ["Trade/WITS", "WorldBank", "Freight/FBX"],
    "Sustainability":   ["AIS/Synthetic", "WorldBank"],
}


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _cache_info(pattern: str) -> dict:
    files = list(_CACHE_DIR.glob(pattern + ".parquet")) if _CACHE_DIR.exists() else []
    if not files:
        return {"files": [], "age_hours": None, "newest": None, "size_bytes": 0}
    newest = max(files, key=lambda f: f.stat().st_mtime)
    age_hours = (time.time() - newest.stat().st_mtime) / 3600
    total_bytes = sum(f.stat().st_size for f in files)
    return {
        "files": files,
        "age_hours": round(age_hours, 3),
        "newest": newest,
        "size_bytes": total_bytes,
    }


def _age_label(age_hours: float | None) -> str:
    if age_hours is None:
        return "—"
    if age_hours < 0.083:
        return "Just now"
    if age_hours < 1.0:
        return f"{int(age_hours * 60)}m ago"
    if age_hours < 24.0:
        return f"{round(age_hours, 1)}h ago"
    return f"{round(age_hours / 24, 1)}d ago"


def _record_count(data: Any) -> int:
    if data is None:
        return 0
    if isinstance(data, dict):
        try:
            import pandas as pd
            total = 0
            for v in data.values():
                if isinstance(v, pd.DataFrame):
                    total += len(v)
                else:
                    total += 1
            return total
        except Exception:
            return len(data)
    try:
        import pandas as pd
        if isinstance(data, pd.DataFrame):
            return len(data)
    except Exception:
        pass
    if isinstance(data, (list, tuple)):
        return len(data)
    return 0


def _null_rate(data: Any) -> float | None:
    """Return fraction of null values across all DataFrame columns, or None."""
    try:
        import pandas as pd
        frames: list[pd.DataFrame] = []
        if isinstance(data, pd.DataFrame):
            frames = [data]
        elif isinstance(data, dict):
            frames = [v for v in data.values() if isinstance(v, pd.DataFrame)]
        if not frames:
            return None
        total_cells = sum(df.size for df in frames)
        null_cells = sum(int(df.isnull().sum().sum()) for df in frames)
        return null_cells / total_cells if total_cells else 0.0
    except Exception:
        return None


def _outlier_rate(data: Any) -> float | None:
    """Return fraction of numeric values > 3 σ from mean, or None."""
    try:
        import pandas as pd
        frames: list[pd.DataFrame] = []
        if isinstance(data, pd.DataFrame):
            frames = [data]
        elif isinstance(data, dict):
            frames = [v for v in data.values() if isinstance(v, pd.DataFrame)]
        if not frames:
            return None
        outlier_count = 0
        total_numeric = 0
        for df in frames:
            num_df = df.select_dtypes(include="number")
            if num_df.empty:
                continue
            total_numeric += num_df.count().sum()
            mu = num_df.mean()
            sd = num_df.std()
            mask = (num_df - mu).abs() > (3 * sd)
            outlier_count += int(mask.sum().sum())
        if total_numeric == 0:
            return None
        return outlier_count / total_numeric
    except Exception:
        return None


def _schema_compliance(data: Any, expected_fields: list[str]) -> float | None:
    """Return fraction of expected schema fields found in data."""
    try:
        import pandas as pd
        cols: set[str] = set()
        if isinstance(data, pd.DataFrame):
            cols = set(c.lower() for c in data.columns)
        elif isinstance(data, dict):
            for v in data.values():
                if isinstance(v, pd.DataFrame):
                    cols.update(c.lower() for c in v.columns)
                    break
        if not cols:
            return None
        found = sum(1 for f in expected_fields if f.lower() in cols)
        return found / len(expected_fields) if expected_fields else 1.0
    except Exception:
        return None


def _source_status(age_h: float | None, ttl_h: float, rec_count: int) -> str:
    if age_h is None and rec_count == 0:
        return "MISSING"
    if age_h is not None and age_h < ttl_h * 0.5:
        return "FRESH"
    if age_h is not None and age_h < ttl_h:
        return "AGING"
    if age_h is not None:
        return "STALE"
    return "FALLBACK"


def _status_palette(status: str) -> tuple[str, str]:
    """Return (text_color, bg_color) for a status string."""
    return {
        "FRESH":    (C_HIGH,   _rgba(C_HIGH, 0.15)),
        "AGING":    (C_WARN,   _rgba(C_WARN, 0.15)),
        "STALE":    (C_DANGER, _rgba(C_DANGER, 0.15)),
        "FALLBACK": (C_WARN,   _rgba(C_WARN, 0.12)),
        "MISSING":  (C_DANGER, _rgba(C_DANGER, 0.12)),
        "LIVE":     (C_HIGH,   _rgba(C_HIGH, 0.15)),
    }.get(status.upper(), (C_TEXT2, _rgba(C_TEXT3, 0.10)))


def _status_badge_html(status: str) -> str:
    color, bg = _status_palette(status)
    dot = (
        f'<span style="display:inline-block;width:6px;height:6px;border-radius:50%;'
        f'background:{color};margin-right:5px;vertical-align:middle;'
        + ("animation:pulse_dh 1.8s infinite;" if status in ("FRESH", "LIVE") else "")
        + f'"></span>'
    )
    return (
        f'<span style="background:{bg};color:{color};border:1px solid {_rgba(color,0.35)};'
        f'border-radius:4px;padding:2px 8px;font-size:0.62rem;font-weight:700;'
        f'letter-spacing:0.07em;white-space:nowrap">{dot}{status}</span>'
    )


def _pct_bar_html(pct: float, color: str, height: int = 5) -> str:
    """Render a thin horizontal progress bar."""
    bg = _rgba(color, 0.15)
    return (
        f'<div style="background:{bg};border-radius:99px;height:{height}px;'
        f'width:100%;margin-top:4px">'
        f'<div style="background:{color};width:{min(pct,100):.0f}%;height:100%;'
        f'border-radius:99px;transition:width 0.5s"></div></div>'
    )


def _section_header(text: str, subtitle: str = "", icon: str = "") -> None:
    icon_html = f'<span style="margin-right:8px">{icon}</span>' if icon else ""
    sub_html = (
        f'<div style="font-size:0.80rem;color:{C_TEXT3};margin-top:3px;font-weight:400">'
        f'{subtitle}</div>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="margin:28px 0 16px 0">'
        f'<div style="font-size:1.05rem;font-weight:700;color:{C_TEXT};'
        f'display:flex;align-items:center">{icon_html}{text}</div>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )


def _divider() -> None:
    st.markdown(
        f'<hr style="border:none;border-top:1px solid {C_BORDER};margin:20px 0">',
        unsafe_allow_html=True,
    )


def _inject_keyframes() -> None:
    st.markdown("""
    <style>
    @keyframes pulse_dh {
        0%,100% { opacity:1; transform:scale(1); }
        50%      { opacity:0.4; transform:scale(1.4); }
    }
    @keyframes fadein_dh {
        from { opacity:0; transform:translateY(6px); }
        to   { opacity:1; transform:translateY(0); }
    }
    .dh-fade { animation: fadein_dh 0.4s ease-out; }
    </style>
    """, unsafe_allow_html=True)


# ── Score computation ─────────────────────────────────────────────────────────

def _compute_scores(
    freight_data: Any,
    macro_data: Any,
    stock_data: Any,
    trade_data: Any,
    ais_data: Any,
) -> dict:
    """Return a dict of per-source and aggregate quality metrics."""
    data_map = {
        "yfinance":      stock_data,
        "FRED":          macro_data,
        "WorldBank":     None,
        "Trade/WITS":    trade_data,
        "Freight/FBX":   freight_data,
        "AIS/Synthetic": ais_data,
    }

    source_scores: dict[str, dict] = {}
    total_fresh_score = 0.0
    total_cov_score = 0.0
    total_complete_score = 0.0
    n = len(_SOURCE_META)

    for src_key, meta in _SOURCE_META.items():
        info = _cache_info(meta["pattern"])
        age_h = info["age_hours"]
        ttl_h = float(meta["ttl_hours"])
        dat = data_map[src_key]
        rec = _record_count(dat)
        status = _source_status(age_h, ttl_h, rec)

        # Freshness 0–100
        if age_h is None:
            fresh = 0.0
        else:
            fresh = max(0.0, (1.0 - min(age_h / ttl_h, 1.0)) * 100.0)

        # Coverage 0–100
        if isinstance(dat, dict) and dat:
            cov = min(len(dat) / 25 * 100, 100)
        elif rec > 0:
            cov = 100.0
        else:
            cov = 0.0

        # Completeness (schema + null)
        null_r = _null_rate(dat)
        schema_c = _schema_compliance(dat, meta.get("schema_fields", []))
        if null_r is not None and schema_c is not None:
            complete = (1.0 - null_r) * 50.0 + schema_c * 50.0
        elif null_r is not None:
            complete = (1.0 - null_r) * 100.0
        elif schema_c is not None:
            complete = schema_c * 100.0
        elif rec > 0:
            complete = 75.0   # data present, can't introspect
        else:
            complete = 0.0

        source_scores[src_key] = {
            "status": status,
            "age_h": age_h,
            "ttl_h": ttl_h,
            "rec": rec,
            "fresh": round(fresh, 1),
            "cov": round(cov, 1),
            "complete": round(complete, 1),
            "null_rate": null_r,
            "outlier_rate": _outlier_rate(dat),
            "schema_c": schema_c,
            "cache": info,
        }

        total_fresh_score += fresh
        total_cov_score += cov
        total_complete_score += complete

    avg_fresh = total_fresh_score / n
    avg_cov = total_cov_score / n
    avg_complete = total_complete_score / n
    overall = (avg_fresh * 0.40 + avg_cov * 0.35 + avg_complete * 0.25)

    return {
        "overall": round(overall, 1),
        "freshness": round(avg_fresh, 1),
        "coverage": round(avg_cov, 1),
        "completeness": round(avg_complete, 1),
        "sources": source_scores,
    }


# ══════════════════════════════════════════════════════════════════════════════
# NEW SECTIONS (A–F) — prepended before existing sections
# ══════════════════════════════════════════════════════════════════════════════

# ── NEW Section A: Data Health KPI Cards ──────────────────────────────────────

def _render_new_kpi_cards(scores: dict) -> None:
    """NEW: Overall quality score, coverage %, freshness %, completeness % as KPI cards."""
    overall = scores["overall"]
    freshness = scores["freshness"]
    coverage = scores["coverage"]
    completeness = scores["completeness"]

    def _kpi_color(val: float) -> str:
        return C_HIGH if val >= 70 else (C_WARN if val >= 45 else C_DANGER)

    def _kpi_label(val: float) -> str:
        return "Good" if val >= 70 else ("Fair" if val >= 45 else "Poor")

    def _ring_svg(val: float, color: str, size: int = 72) -> str:
        """SVG donut ring gauge."""
        r = 26
        cx = size // 2
        cy = size // 2
        circ = 2 * math.pi * r
        dash_val = circ * (val / 100.0)
        dash_bg = circ
        return (
            f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            # Background ring
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{_rgba(color, 0.12)}" stroke-width="7"/>'
            # Progress ring (start at top: -90deg rotation)
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{color}" stroke-width="7" stroke-linecap="round" '
            f'stroke-dasharray="{dash_val:.1f} {dash_bg:.1f}" '
            f'transform="rotate(-90 {cx} {cy})"/>'
            # Center text
            f'<text x="{cx}" y="{cy+1}" text-anchor="middle" dominant-baseline="middle" '
            f'fill="{color}" font-size="11" font-weight="700" '
            f'font-family="Inter,sans-serif">{val:.0f}</text>'
            f'</svg>'
        )

    metrics = [
        ("OVERALL QUALITY", overall, "Composite score: freshness + coverage + completeness"),
        ("FRESHNESS", freshness, "Cache age relative to TTL windows"),
        ("COVERAGE", coverage, "Sources with active records loaded"),
        ("COMPLETENESS", completeness, "Schema compliance + null-rate score"),
    ]

    cards_html = ""
    for i, (label, val, desc) in enumerate(metrics):
        color = _kpi_color(val)
        status_label = _kpi_label(val)
        is_main = i == 0
        border_right = f"border-right:1px solid {C_BORDER};" if i < len(metrics) - 1 else ""

        # Trend bar
        bar_bg = _rgba(color, 0.12)
        bar_fill = _rgba(color, 0.80)

        cards_html += f"""
        <div style="flex:1;min-width:160px;padding:20px 18px;{border_right}
                    display:flex;flex-direction:column;align-items:center;text-align:center">

            {_ring_svg(val, color, 84 if is_main else 72)}

            <div style="font-size:{'2.2rem' if is_main else '1.6rem'};font-weight:900;
                        color:{color};letter-spacing:-0.03em;margin-top:6px;line-height:1;
                        text-shadow:0 0 20px {_rgba(color, 0.35)}">
                {val:.0f}
                <span style="font-size:0.75rem;font-weight:500;color:{_rgba(color,0.65)}">/100</span>
            </div>

            <div style="font-size:0.58rem;font-weight:800;color:{C_TEXT3};
                        text-transform:uppercase;letter-spacing:0.12em;margin-top:8px">
                {label}
            </div>

            <div style="font-size:0.65rem;color:{color};font-weight:600;margin-top:3px;
                        background:{_rgba(color,0.12)};border-radius:999px;padding:1px 9px">
                {status_label}
            </div>

            <div style="font-size:0.62rem;color:{C_TEXT3};margin-top:5px;line-height:1.4">
                {desc}
            </div>

            <div style="width:80%;background:{bar_bg};border-radius:99px;height:3px;margin-top:8px">
                <div style="background:{bar_fill};width:{min(val,100):.0f}%;
                            height:100%;border-radius:99px"></div>
            </div>
        </div>"""

    overall_color = _kpi_color(overall)
    ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    st.markdown(f"""
    <div class="dh-fade" style="background:linear-gradient(135deg,{C_CARD} 0%,{C_CARD2} 100%);
         border:1px solid {C_BORDER};border-radius:16px;overflow:hidden;margin-bottom:8px">

        <div style="background:linear-gradient(90deg,{_rgba(overall_color,0.09)} 0%,transparent 55%);
             padding:14px 22px 10px;border-bottom:1px solid {C_BORDER};
             display:flex;justify-content:space-between;align-items:center">
            <div>
                <div style="font-size:0.72rem;font-weight:800;color:{C_TEXT};letter-spacing:0.02em">
                    DATA PIPELINE HEALTH REPORT
                </div>
                <div style="font-size:0.64rem;color:{C_TEXT3};margin-top:2px">
                    Composite quality assessment across all upstream data sources
                </div>
            </div>
            <div style="font-size:0.62rem;font-family:{C_MONO};color:{C_TEXT3}">
                {ts_str}
            </div>
        </div>

        <div style="display:flex;flex-wrap:wrap">{cards_html}</div>
    </div>
    """, unsafe_allow_html=True)


# ── NEW Section B: Data Source Cards ──────────────────────────────────────────

def _render_new_source_cards(scores: dict) -> None:
    """NEW: Each source as a detailed card with status badge, last update, record count,
    freshness gauge, and record count.
    """
    _section_header("Data Source Status", "Live health of each upstream pipeline feed", "🛰")

    source_scores = scores["sources"]
    cols = st.columns(3)

    for idx, (src_key, meta) in enumerate(_SOURCE_META.items()):
        col = cols[idx % 3]
        ss = source_scores[src_key]
        color = meta["color"]
        status = ss["status"]
        status_color, status_bg = _status_palette(status)
        age_h = ss["age_h"]
        ttl_h = ss["ttl_h"]
        fresh_pct = ss["fresh"]
        cov_pct = ss["cov"]
        complete_pct = ss["complete"]
        rec = ss["rec"]

        fresh_color = C_HIGH if fresh_pct >= 70 else (C_WARN if fresh_pct >= 40 else C_DANGER)
        cov_color = C_HIGH if cov_pct >= 70 else (C_WARN if cov_pct >= 40 else C_DANGER)
        complete_color = C_HIGH if complete_pct >= 70 else (C_WARN if complete_pct >= 45 else C_DANGER)

        ttl_label = f"{ttl_h:.0f}h TTL" if ttl_h < 48 else f"{ttl_h/24:.0f}d TTL"
        age_str = _age_label(age_h)
        rec_str = f"{rec:,}" if rec else "—"
        cache_size_kb = ss["cache"]["size_bytes"] / 1024
        size_str = f"{cache_size_kb:.1f} KB" if cache_size_kb > 0 else "—"
        file_count = len(ss["cache"]["files"])

        # Null rate mini display
        null_r = ss.get("null_rate")
        null_str = f"{null_r*100:.1f}%" if null_r is not None else "—"
        null_color = C_HIGH if (null_r or 0) < 0.05 else (C_WARN if (null_r or 0) < 0.15 else C_DANGER)

        # Schema compliance
        schema_c = ss.get("schema_c")
        schema_str = f"{schema_c*100:.0f}%" if schema_c is not None else "—"
        schema_color = C_HIGH if (schema_c or 0) >= 0.80 else (C_WARN if (schema_c or 0) >= 0.50 else C_DANGER)

        # Pulse animation dot in header for FRESH sources
        header_dot = ""
        if status == "FRESH":
            header_dot = (
                f'<span style="width:7px;height:7px;border-radius:50%;background:{C_HIGH};'
                f'display:inline-block;margin-right:6px;animation:pulse_dh 1.8s infinite;'
                f'vertical-align:middle"></span>'
            )

        card_html = f"""
        <div class="dh-fade" style="background:linear-gradient(145deg,{C_CARD} 0%,{C_CARD2} 100%);
             border:1px solid {C_BORDER};border-left:4px solid {color};
             border-radius:12px;margin-bottom:14px;overflow:hidden">

            <!-- Header -->
            <div style="padding:14px 16px 10px;
                        background:linear-gradient(90deg,{_rgba(color,0.09)} 0%,transparent 65%)">
                <div style="display:flex;justify-content:space-between;align-items:flex-start">
                    <div style="display:flex;align-items:center;gap:10px">
                        <span style="font-size:1.6rem">{meta['icon']}</span>
                        <div>
                            <div style="font-size:0.82rem;font-weight:800;color:{C_TEXT}">
                                {header_dot}{meta['label']}</div>
                            <div style="font-size:0.64rem;color:{C_TEXT3};margin-top:1px">
                                {meta['full_label']}</div>
                        </div>
                    </div>
                    {_status_badge_html(status)}
                </div>
            </div>

            <!-- Metrics grid -->
            <div style="padding:10px 16px 14px">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 8px;margin-bottom:12px">
                    <div style="font-size:0.63rem;color:{C_TEXT3}">Last update</div>
                    <div style="font-size:0.63rem;color:{C_TEXT2};text-align:right;
                                font-family:{C_MONO}">{age_str}</div>
                    <div style="font-size:0.63rem;color:{C_TEXT3}">Records loaded</div>
                    <div style="font-size:0.63rem;color:{C_TEXT2};text-align:right;
                                font-family:{C_MONO}">{rec_str}</div>
                    <div style="font-size:0.63rem;color:{C_TEXT3}">Cache size</div>
                    <div style="font-size:0.63rem;color:{C_TEXT2};text-align:right;
                                font-family:{C_MONO}">{size_str} ({file_count}f)</div>
                    <div style="font-size:0.63rem;color:{C_TEXT3}">Null rate</div>
                    <div style="font-size:0.63rem;color:{null_color};text-align:right;
                                font-family:{C_MONO};font-weight:600">{null_str}</div>
                    <div style="font-size:0.63rem;color:{C_TEXT3}">Schema match</div>
                    <div style="font-size:0.63rem;color:{schema_color};text-align:right;
                                font-family:{C_MONO};font-weight:600">{schema_str}</div>
                </div>

                <!-- Freshness gauge -->
                <div style="margin-bottom:7px">
                    <div style="display:flex;justify-content:space-between;margin-bottom:2px">
                        <span style="font-size:0.60rem;color:{C_TEXT3}">Freshness</span>
                        <span style="font-size:0.60rem;color:{fresh_color};font-weight:700">
                            {fresh_pct:.0f}% &nbsp;·&nbsp; {ttl_label}</span>
                    </div>
                    {_pct_bar_html(fresh_pct, fresh_color, 6)}
                </div>

                <!-- Coverage gauge -->
                <div style="margin-bottom:7px">
                    <div style="display:flex;justify-content:space-between;margin-bottom:2px">
                        <span style="font-size:0.60rem;color:{C_TEXT3}">Coverage</span>
                        <span style="font-size:0.60rem;color:{cov_color};font-weight:700">
                            {cov_pct:.0f}%</span>
                    </div>
                    {_pct_bar_html(cov_pct, cov_color, 6)}
                </div>

                <!-- Completeness gauge -->
                <div style="margin-bottom:10px">
                    <div style="display:flex;justify-content:space-between;margin-bottom:2px">
                        <span style="font-size:0.60rem;color:{C_TEXT3}">Completeness</span>
                        <span style="font-size:0.60rem;color:{complete_color};font-weight:700">
                            {complete_pct:.0f}%</span>
                    </div>
                    {_pct_bar_html(complete_pct, complete_color, 6)}
                </div>

                <!-- Description -->
                <div style="font-size:0.63rem;color:{C_TEXT3};line-height:1.4;
                            border-top:1px solid {C_BORDER2};padding-top:8px">
                    {meta['description']}
                </div>
            </div>
        </div>"""
        col.markdown(card_html, unsafe_allow_html=True)


# ── NEW Section C: Pipeline Flow HTML Diagram ─────────────────────────────────

def _render_new_pipeline_html(scores: dict) -> None:
    """NEW: Simple HTML/CSS flow diagram showing data pipeline stages."""
    _section_header(
        "Data Pipeline Architecture",
        "HTML flow diagram — raw sources through validation, cache, and analysis layers",
        "🔀",
    )

    source_scores = scores["sources"]

    def _node_html(
        label: str,
        sub: str,
        color: str,
        status: str = "",
        icon: str = "",
    ) -> str:
        status_dot = ""
        if status:
            s_color, _ = _status_palette(status)
            status_dot = (
                f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
                f'background:{s_color};margin-left:5px;vertical-align:middle;'
                + ("animation:pulse_dh 1.8s infinite;" if status == "FRESH" else "")
                + '"></span>'
            )
        return (
            f'<div style="background:{_rgba(color,0.10)};border:1px solid {_rgba(color,0.35)};'
            f'border-radius:8px;padding:8px 12px;text-align:center;min-width:90px;flex:1">'
            f'<div style="font-size:0.90rem;margin-bottom:2px">{icon}</div>'
            f'<div style="font-size:0.68rem;font-weight:700;color:{C_TEXT}">{label}{status_dot}</div>'
            f'<div style="font-size:0.58rem;color:{C_TEXT3};margin-top:1px">{sub}</div>'
            f'</div>'
        )

    def _arrow_html(vertical: bool = False) -> str:
        if vertical:
            return (
                f'<div style="display:flex;justify-content:center;margin:4px 0">'
                f'<div style="color:{C_TEXT3};font-size:1rem">&#8595;</div>'
                f'</div>'
            )
        return (
            f'<div style="display:flex;align-items:center;flex-shrink:0;padding:0 4px">'
            f'<div style="color:{C_TEXT3};font-size:1rem">&#8594;</div>'
            f'</div>'
        )

    # Build source row HTML
    source_nodes = ""
    for src_key, meta in _SOURCE_META.items():
        ss = source_scores[src_key]
        source_nodes += _node_html(
            meta["label"],
            meta["vintage"],
            meta["color"],
            ss["status"],
            meta["icon"],
        )
        source_nodes += '<div style="width:6px;flex-shrink:0"></div>'

    pipeline_html = f"""
    <div class="dh-fade" style="background:{C_CARD};border:1px solid {C_BORDER};
         border-radius:14px;padding:20px 22px;overflow-x:auto">

        <!-- Row 1: Source nodes -->
        <div style="font-size:0.60rem;font-weight:700;color:{C_TEXT3};
                    text-transform:uppercase;letter-spacing:0.10em;margin-bottom:8px">
            Upstream Sources
        </div>
        <div style="display:flex;gap:0;align-items:stretch;flex-wrap:nowrap;min-width:600px">
            {source_nodes}
        </div>

        <!-- Arrow down -->
        <div style="display:flex;justify-content:center;margin:10px 0">
            <div style="display:flex;flex-direction:column;align-items:center;gap:2px">
                <div style="width:2px;height:16px;background:{_rgba(C_ACCENT,0.40)};border-radius:1px"></div>
                <div style="color:{C_ACCENT};font-size:0.9rem">&#9660;</div>
            </div>
        </div>

        <!-- Row 2: Processing layer -->
        <div style="font-size:0.60rem;font-weight:700;color:{C_TEXT3};
                    text-transform:uppercase;letter-spacing:0.10em;margin-bottom:8px">
            Processing Layer
        </div>
        <div style="display:flex;gap:8px;align-items:stretch;flex-wrap:nowrap;min-width:600px">
            {_node_html("Ingestion", "HTTP / API fetch", C_ACCENT, icon="⬇️")}
            {_arrow_html()}
            {_node_html("Validation", "Schema + null checks", C_CYAN, icon="✅")}
            {_arrow_html()}
            {_node_html("Cache", "Parquet on disk", C_PURPLE, icon="💾")}
            {_arrow_html()}
            {_node_html("Feature Engine", "Normalization + scoring", "#f97316", icon="⚙️")}
        </div>

        <!-- Arrow down -->
        <div style="display:flex;justify-content:center;margin:10px 0">
            <div style="display:flex;flex-direction:column;align-items:center;gap:2px">
                <div style="width:2px;height:16px;background:{_rgba(C_PURPLE,0.40)};border-radius:1px"></div>
                <div style="color:{C_PURPLE};font-size:0.9rem">&#9660;</div>
            </div>
        </div>

        <!-- Row 3: Analysis outputs -->
        <div style="font-size:0.60rem;font-weight:700;color:{C_TEXT3};
                    text-transform:uppercase;letter-spacing:0.10em;margin-bottom:8px">
            Analytical Outputs
        </div>
        <div style="display:flex;gap:8px;align-items:stretch;flex-wrap:nowrap;min-width:600px">
            {_node_html("Port Analysis", "Demand scoring", C_HIGH, icon="🏗️")}
            {_node_html("Route Optimizer", "Alpha signals", C_ACCENT, icon="🚢")}
            {_node_html("Market Intel", "Equity signals", C_WARN, icon="📈")}
            {_node_html("Risk Models", "Trade war / geo", C_DANGER, icon="⚠️")}
            {_node_html("Supply Chain", "Flow analysis", C_PURPLE, icon="🔗")}
        </div>

    </div>
    """

    st.markdown(pipeline_html, unsafe_allow_html=True)


# ── NEW Section D: Data Quality Metrics Table ─────────────────────────────────

def _render_new_quality_table(scores: dict) -> None:
    """NEW: Null rate, outlier rate, schema compliance, completeness per source."""
    _section_header(
        "Data Quality Metrics",
        "Per-source null rate, outlier rate, schema compliance, and overall completeness",
        "🔬",
    )

    source_scores = scores["sources"]

    def _pct_cell(val: float | None, invert: bool = False, suffix: str = "%") -> str:
        if val is None:
            return f'<span style="color:{C_TEXT3}">—</span>'
        pct = val * 100
        if invert:
            c = C_DANGER if pct > 10 else (C_HIGH if pct < 3 else C_WARN)
        else:
            c = C_HIGH if pct >= 80 else (C_WARN if pct >= 50 else C_DANGER)
        return f'<span style="color:{c};font-family:{C_MONO};font-weight:700">{pct:.1f}{suffix}</span>'

    def _bar_cell(val: float, color: str) -> str:
        bar_bg = _rgba(color, 0.12)
        return (
            f'<div style="display:flex;align-items:center;gap:7px">'
            f'<div style="flex:1;background:{bar_bg};border-radius:99px;height:6px">'
            f'<div style="background:{color};width:{min(val,100):.0f}%;height:100%;border-radius:99px"></div>'
            f'</div>'
            f'<span style="font-size:0.67rem;color:{color};font-weight:700;font-family:{C_MONO};'
            f'min-width:30px;text-align:right">{val:.0f}%</span>'
            f'</div>'
        )

    rows_html = ""
    for src_key, meta in _SOURCE_META.items():
        ss = source_scores[src_key]
        color = meta["color"]
        null_r = ss.get("null_rate")
        outlier_r = ss.get("outlier_rate")
        schema_c = ss.get("schema_c")
        complete = ss["complete"]
        complete_color = C_HIGH if complete >= 70 else (C_WARN if complete >= 45 else C_DANGER)

        schema_str = "—"
        if schema_c is not None:
            fields_found = int(schema_c * len(meta.get("schema_fields", [])))
            total_fields = len(meta.get("schema_fields", []))
            sc_color = C_HIGH if schema_c >= 0.80 else (C_WARN if schema_c >= 0.50 else C_DANGER)
            schema_str = (
                f'<span style="color:{sc_color};font-family:{C_MONO};font-weight:700">'
                f'{schema_c*100:.0f}%</span>'
                f'<span style="color:{C_TEXT3};font-size:0.58rem"> ({fields_found}/{total_fields})</span>'
            )

        rows_html += f"""
        <tr style="border-bottom:1px solid {C_BORDER2}">
            <td style="padding:10px 14px;white-space:nowrap">
                <span style="font-size:1.05rem;margin-right:6px">{meta['icon']}</span>
                <span style="font-size:0.78rem;font-weight:700;color:{color}">{meta['label']}</span>
            </td>
            <td style="padding:10px 14px;text-align:center">{_status_badge_html(ss['status'])}</td>
            <td style="padding:10px 14px;text-align:center">{_pct_cell(null_r, invert=True)}</td>
            <td style="padding:10px 14px;text-align:center">{_pct_cell(outlier_r, invert=True)}</td>
            <td style="padding:10px 14px;text-align:center">{schema_str}</td>
            <td style="padding:10px 14px;min-width:150px">{_bar_cell(complete, complete_color)}</td>
        </tr>"""

    th_s = (
        f"padding:8px 14px;font-size:0.62rem;font-weight:700;color:{C_TEXT3};"
        f"text-transform:uppercase;letter-spacing:0.09em"
    )
    st.markdown(f"""
    <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden">
        <table style="width:100%;border-collapse:collapse">
            <thead>
                <tr style="background:{_rgba(C_ACCENT,0.06)};border-bottom:1px solid {C_BORDER}">
                    <th style="{th_s};text-align:left">Source</th>
                    <th style="{th_s};text-align:center">Status</th>
                    <th style="{th_s};text-align:center">Null Rate</th>
                    <th style="{th_s};text-align:center">Outlier Rate</th>
                    <th style="{th_s};text-align:center">Schema</th>
                    <th style="{th_s};text-align:left">Completeness</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)


# ── NEW Section E: Historical Availability Heatmap ────────────────────────────

def _render_new_availability_heatmap(
    freight_data: Any,
    macro_data: Any,
    stock_data: Any,
    trade_data: Any,
    ais_data: Any,
) -> None:
    """NEW: Dates vs sources heatmap (green=complete, yellow=partial, red=missing)."""
    import pandas as pd

    _section_header(
        "Historical Data Availability",
        "30-day calendar heatmap — green=complete, amber=partial, red=missing",
        "📅",
    )

    data_map = {
        "Stocks":  stock_data,
        "Macro":   macro_data,
        "Trade":   trade_data,
        "Freight": freight_data,
        "AIS":     ais_data,
    }

    today = pd.Timestamp.now().normalize()
    dates = [today - pd.Timedelta(days=i) for i in range(29, -1, -1)]
    date_strs = [d.strftime("%m/%d") for d in dates]

    z_rows: list[list[float]] = []
    source_labels: list[str] = []

    for src_label, dat in data_map.items():
        source_labels.append(src_label)
        row: list[float] = []
        for d in dates:
            if dat is None:
                row.append(0.0)
            elif isinstance(dat, dict):
                has_date = False
                try:
                    for v in dat.values():
                        if isinstance(v, pd.DataFrame) and not v.empty:
                            for date_col in ["date", "Date", "timestamp"]:
                                if date_col in v.columns:
                                    v_dates = pd.to_datetime(v[date_col], errors="coerce")
                                    if ((v_dates - d).abs().min() <= pd.Timedelta(days=3)):
                                        has_date = True
                                        break
                            if has_date:
                                break
                except Exception:
                    pass
                row.append(1.0 if has_date else 0.5)
            elif isinstance(dat, pd.DataFrame) and not dat.empty:
                row.append(0.8)
            else:
                row.append(0.0)
        z_rows.append(row)

    colorscale = [
        [0.0, _rgba(C_DANGER, 0.70)],
        [0.4, _rgba(C_WARN,   0.70)],
        [0.6, _rgba(C_WARN,   0.85)],
        [1.0, _rgba(C_HIGH,   0.85)],
    ]

    fig = go.Figure(go.Heatmap(
        z=z_rows,
        x=date_strs,
        y=source_labels,
        colorscale=colorscale,
        showscale=True,
        zmin=0.0,
        zmax=1.0,
        hoverongaps=False,
        hovertemplate="<b>%{y}</b><br>%{x}<br>Coverage: %{z:.0%}<extra></extra>",
        xgap=2,
        ygap=3,
        colorbar=dict(
            thickness=10,
            len=0.8,
            tickvals=[0.0, 0.5, 1.0],
            ticktext=["Missing", "Partial", "Complete"],
            tickfont=dict(color=C_TEXT3, size=9),
            outlinewidth=0,
        ),
    ))
    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font={"color": C_TEXT, "size": 11, "family": "Inter, sans-serif"},
        height=240,
        margin={"l": 80, "r": 60, "t": 10, "b": 44},
        xaxis={
            "tickfont": {"color": C_TEXT3, "size": 9},
            "tickangle": -30,
            "showgrid": False,
        },
        yaxis={
            "tickfont": {"color": C_TEXT2, "size": 11},
            "showgrid": False,
        },
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key="chart_new_availability_heatmap")

    # Legend
    st.markdown(
        f'<div style="display:flex;gap:16px;margin-top:4px;font-size:0.63rem;color:{C_TEXT3}">'
        f'<span><span style="color:{C_HIGH};font-weight:700">&#9632;</span> Complete (data within 3 days)</span>'
        f'<span><span style="color:{C_WARN};font-weight:700">&#9632;</span> Partial (data present, dates unconfirmed)</span>'
        f'<span><span style="color:{C_DANGER};font-weight:700">&#9632;</span> Missing (no data loaded)</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── NEW Section F: Cache Status Table ─────────────────────────────────────────

def _render_new_cache_status(scores: dict) -> None:
    """NEW: Each cached dataset with size, age, TTL remaining as styled table."""
    _section_header(
        "Cache Status",
        "Size, age, and TTL remaining for each cached pipeline dataset",
        "🗄",
    )

    source_scores = scores["sources"]

    rows_html = ""
    for src_key, meta in _SOURCE_META.items():
        ss = source_scores[src_key]
        cache = ss["cache"]
        age_h = ss["age_h"]
        ttl_h = ss["ttl_h"]
        color = meta["color"]

        size_kb = cache["size_bytes"] / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb >= 0.1 else "—"
        age_str = _age_label(age_h)
        file_count = len(cache["files"])

        if age_h is not None:
            ttl_rem = max(ttl_h - age_h, 0.0)
            ttl_used_pct = min((age_h / ttl_h) * 100, 100) if ttl_h else 100
            bar_color = C_HIGH if ttl_used_pct < 50 else (C_WARN if ttl_used_pct < 85 else C_DANGER)
            ttl_rem_str = _age_label(ttl_rem) if ttl_rem > 0.0833 else "Expired"
            # TTL fresh pct (inverse of used)
            ttl_fresh_pct = 100.0 - ttl_used_pct
        else:
            ttl_rem_str = "—"
            ttl_used_pct = 100.0
            ttl_fresh_pct = 0.0
            bar_color = C_DANGER

        # Status badge
        status_badge = _status_badge_html(ss["status"])

        rows_html += f"""
        <tr style="border-bottom:1px solid {C_BORDER2}">
            <td style="padding:11px 14px;white-space:nowrap">
                <span style="font-size:1.05rem;margin-right:7px">{meta['icon']}</span>
                <span style="font-size:0.78rem;font-weight:700;color:{color}">{meta['label']}</span>
            </td>
            <td style="padding:11px 14px;text-align:center">{status_badge}</td>
            <td style="padding:11px 14px;text-align:center;font-family:{C_MONO};
                       font-size:0.72rem;color:{C_TEXT2}">{size_str}</td>
            <td style="padding:11px 14px;text-align:center;font-family:{C_MONO};
                       font-size:0.72rem;color:{C_TEXT2}">{age_str}</td>
            <td style="padding:11px 14px;min-width:140px">
                <div style="display:flex;align-items:center;gap:8px">
                    <div style="flex:1;background:{_rgba(bar_color,0.12)};border-radius:99px;height:6px">
                        <div style="background:{bar_color};width:{ttl_fresh_pct:.0f}%;
                                    height:100%;border-radius:99px"></div>
                    </div>
                    <span style="font-size:0.65rem;color:{bar_color};font-family:{C_MONO};
                                 min-width:44px;text-align:right;font-weight:600">{ttl_rem_str}</span>
                </div>
            </td>
            <td style="padding:11px 14px;text-align:center;font-size:0.70rem;color:{C_TEXT3}">
                {file_count} file{'s' if file_count != 1 else ''}
            </td>
        </tr>"""

    th_s = (
        f"padding:8px 14px;font-size:0.62rem;font-weight:700;color:{C_TEXT3};"
        f"text-transform:uppercase;letter-spacing:0.09em"
    )
    st.markdown(f"""
    <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden">
        <table style="width:100%;border-collapse:collapse">
            <thead>
                <tr style="background:{_rgba(C_ACCENT,0.06)};border-bottom:1px solid {C_BORDER}">
                    <th style="{th_s};text-align:left">Dataset</th>
                    <th style="{th_s};text-align:center">Status</th>
                    <th style="{th_s};text-align:center">Size</th>
                    <th style="{th_s};text-align:center">Cache Age</th>
                    <th style="{th_s};text-align:left">TTL Remaining</th>
                    <th style="{th_s};text-align:center">Files</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# EXISTING SECTIONS (1–10) — preserved exactly
# ══════════════════════════════════════════════════════════════════════════════

# ── Section 1: Overview scorecard banner ─────────────────────────────────────

def _render_overview_banner(scores: dict) -> None:
    overall = scores["overall"]
    freshness = scores["freshness"]
    coverage = scores["coverage"]
    completeness = scores["completeness"]

    overall_color = C_HIGH if overall >= 70 else (C_WARN if overall >= 45 else C_DANGER)
    freshness_color = C_HIGH if freshness >= 70 else (C_WARN if freshness >= 45 else C_DANGER)
    coverage_color = C_HIGH if coverage >= 70 else (C_WARN if coverage >= 45 else C_DANGER)
    complete_color = C_HIGH if completeness >= 70 else (C_WARN if completeness >= 45 else C_DANGER)

    # Gauge arc SVG (semicircle indicator)
    def _arc_svg(value: float, color: str, size: int = 80) -> str:
        r = 32
        cx = size // 2
        cy = size // 2
        circumference = math.pi * r   # half circle
        dash = circumference * (value / 100.0)
        return (
            f'<svg width="{size}" height="{size//2 + 10}" viewBox="0 0 {size} {size//2 + 10}">'
            f'<path d="M{cx-r},{cy} A{r},{r} 0 0,1 {cx+r},{cy}" '
            f'fill="none" stroke="{_rgba(color, 0.15)}" stroke-width="8" stroke-linecap="round"/>'
            f'<path d="M{cx-r},{cy} A{r},{r} 0 0,1 {cx+r},{cy}" '
            f'fill="none" stroke="{color}" stroke-width="8" stroke-linecap="round" '
            f'stroke-dasharray="{dash:.1f} {circumference:.1f}" />'
            f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" '
            f'fill="{color}" font-size="13" font-weight="700" font-family="Inter,sans-serif">'
            f'{value:.0f}</text>'
            f'</svg>'
        )

    stats = [
        ("OVERALL QUALITY", overall, overall_color, "Composite score across all sources"),
        ("FRESHNESS", freshness, freshness_color, "Cache age vs TTL"),
        ("COVERAGE", coverage, coverage_color, "Sources with active records"),
        ("COMPLETENESS", completeness, complete_color, "Schema + null-rate compliance"),
    ]

    cols_html = ""
    for i, (label, val, color, desc) in enumerate(stats):
        border_right = f"border-right:1px solid {C_BORDER};" if i < len(stats) - 1 else ""
        is_main = i == 0
        font_size = "2.6rem" if is_main else "1.8rem"
        cols_html += f"""
        <div style="flex:1;min-width:130px;text-align:center;padding:20px 16px;{border_right}">
            {_arc_svg(val, color, 90 if is_main else 76)}
            <div style="font-size:{font_size};font-weight:800;color:{color};
                        letter-spacing:-0.02em;margin-top:2px;
                        text-shadow:0 0 24px {_rgba(color,0.4)}">{val:.0f}
                <span style="font-size:0.9rem;font-weight:500;color:{_rgba(color,0.7)}">/ 100</span>
            </div>
            <div style="font-size:0.60rem;font-weight:700;color:{C_TEXT3};
                        text-transform:uppercase;letter-spacing:0.10em;margin-top:6px">{label}</div>
            <div style="font-size:0.68rem;color:{C_TEXT3};margin-top:3px">{desc}</div>
        </div>"""

    st.markdown(f"""
    <div class="dh-fade" style="background:linear-gradient(135deg,{C_CARD} 0%,{C_CARD2} 100%);
         border:1px solid {C_BORDER};border-radius:16px;overflow:hidden;margin-bottom:6px">
        <div style="background:linear-gradient(90deg,{_rgba(overall_color,0.08)} 0%,transparent 60%);
             padding:18px 24px 10px;border-bottom:1px solid {C_BORDER}">
            <div style="font-size:0.65rem;font-weight:700;color:{C_TEXT3};
                        text-transform:uppercase;letter-spacing:0.12em">
                Data Pipeline Health Report &nbsp;·&nbsp;
                <span style="color:{C_TEXT2}">{datetime.now().strftime('%Y-%m-%d %H:%M UTC')}</span>
            </div>
        </div>
        <div style="display:flex;flex-wrap:wrap">{cols_html}</div>
    </div>
    """, unsafe_allow_html=True)


# ── Section 2: Data source cards ─────────────────────────────────────────────

def _render_source_cards(scores: dict) -> None:
    _section_header("Data Source Dashboard", "Live health of each upstream feed", "🛰")

    source_scores = scores["sources"]

    cols = st.columns(3)
    for idx, (src_key, meta) in enumerate(_SOURCE_META.items()):
        col = cols[idx % 3]
        ss = source_scores[src_key]
        color = meta["color"]
        status = ss["status"]
        status_color, status_bg = _status_palette(status)
        age_h = ss["age_h"]
        ttl_h = ss["ttl_h"]

        # TTL progress bar values
        fresh_pct = ss["fresh"]
        cov_pct = ss["cov"]
        fresh_color = C_HIGH if fresh_pct >= 70 else (C_WARN if fresh_pct >= 40 else C_DANGER)
        cov_color = C_HIGH if cov_pct >= 70 else (C_WARN if cov_pct >= 40 else C_DANGER)

        ttl_label = f"{ttl_h:.0f}h TTL" if ttl_h < 48 else f"{ttl_h/24:.0f}d TTL"

        rec_str = f"{ss['rec']:,}" if ss["rec"] else "—"
        age_str = _age_label(age_h)
        cache_size_kb = ss["cache"]["size_bytes"] / 1024
        size_str = f"{cache_size_kb:.1f} KB" if cache_size_kb > 0 else "—"

        card_html = f"""
        <div class="dh-fade" style="background:linear-gradient(145deg,{C_CARD} 0%,{C_CARD2} 100%);
             border:1px solid {C_BORDER};border-left:3px solid {color};
             border-radius:12px;padding:0;margin-bottom:14px;overflow:hidden">

            <!-- Card header -->
            <div style="padding:14px 16px 12px;
                        background:linear-gradient(90deg,{_rgba(color,0.07)} 0%,transparent 70%)">
                <div style="display:flex;justify-content:space-between;align-items:flex-start">
                    <div>
                        <span style="font-size:1.4rem">{meta['icon']}</span>
                        <div style="font-size:0.82rem;font-weight:700;color:{C_TEXT};margin-top:4px">
                            {meta['label']}</div>
                        <div style="font-size:0.66rem;color:{C_TEXT3};margin-top:1px">
                            {meta['full_label']}</div>
                    </div>
                    {_status_badge_html(status)}
                </div>
            </div>

            <!-- Metrics grid -->
            <div style="padding:10px 16px 14px">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 8px;
                            margin-bottom:10px">
                    <div style="font-size:0.64rem;color:{C_TEXT3}">Last update</div>
                    <div style="font-size:0.64rem;color:{C_TEXT2};text-align:right;
                                font-family:{C_MONO}">{age_str}</div>
                    <div style="font-size:0.64rem;color:{C_TEXT3}">Records</div>
                    <div style="font-size:0.64rem;color:{C_TEXT2};text-align:right;
                                font-family:{C_MONO}">{rec_str}</div>
                    <div style="font-size:0.64rem;color:{C_TEXT3}">Cache size</div>
                    <div style="font-size:0.64rem;color:{C_TEXT2};text-align:right;
                                font-family:{C_MONO}">{size_str}</div>
                    <div style="font-size:0.64rem;color:{C_TEXT3}">Vintage</div>
                    <div style="font-size:0.64rem;color:{C_TEXT2};text-align:right">{meta['vintage']}</div>
                </div>

                <!-- Freshness gauge -->
                <div style="margin-bottom:8px">
                    <div style="display:flex;justify-content:space-between;margin-bottom:2px">
                        <span style="font-size:0.60rem;color:{C_TEXT3}">Freshness</span>
                        <span style="font-size:0.60rem;color:{fresh_color};font-weight:600">
                            {fresh_pct:.0f}% &nbsp;·&nbsp; {ttl_label}</span>
                    </div>
                    {_pct_bar_html(fresh_pct, fresh_color, 6)}
                </div>

                <!-- Coverage gauge -->
                <div>
                    <div style="display:flex;justify-content:space-between;margin-bottom:2px">
                        <span style="font-size:0.60rem;color:{C_TEXT3}">Coverage</span>
                        <span style="font-size:0.60rem;color:{cov_color};font-weight:600">
                            {cov_pct:.0f}%</span>
                    </div>
                    {_pct_bar_html(cov_pct, cov_color, 6)}
                </div>

                <!-- Description -->
                <div style="margin-top:10px;font-size:0.63rem;color:{C_TEXT3};
                            line-height:1.4;border-top:1px solid {C_BORDER2};padding-top:8px">
                    {meta['description']}
                </div>
            </div>
        </div>"""
        col.markdown(card_html, unsafe_allow_html=True)


# ── Section 3: Data pipeline flow diagram (Sankey) ───────────────────────────

def _render_pipeline_flow(scores: dict) -> None:
    _section_header(
        "Data Pipeline Architecture",
        "How raw data flows from upstream sources through processing to analytical outputs",
        "🔀",
    )

    source_scores = scores["sources"]

    # Node definitions: [sources, processing, outputs]
    src_labels = [meta["label"] for meta in _SOURCE_META.values()]
    proc_labels = ["Ingestion Layer", "Validation", "Cache (Parquet)", "Feature Engine"]
    out_labels = ["Port Analysis", "Route Optimizer", "Market Intelligence", "Risk Models"]

    labels = src_labels + proc_labels + out_labels
    n_src = len(src_labels)
    n_proc = len(proc_labels)

    # Build color list per node
    src_colors = [_rgba(meta["color"], 0.7) for meta in _SOURCE_META.values()]
    proc_colors = [_rgba(C_ACCENT, 0.5)] * n_proc
    out_colors = [_rgba(C_PURPLE, 0.6)] * len(out_labels)
    node_colors = src_colors + proc_colors + out_colors

    # Indices
    ingestion_idx = n_src + 0
    validation_idx = n_src + 1
    cache_idx = n_src + 2
    feature_idx = n_src + 3

    src_indices = list(range(n_src))
    port_idx = n_src + n_proc + 0
    route_idx = n_src + n_proc + 1
    market_idx = n_src + n_proc + 2
    risk_idx = n_src + n_proc + 3

    sources_link = []
    targets_link = []
    values_link = []
    link_colors = []

    # Source → Ingestion
    for i, (src_key, meta) in enumerate(_SOURCE_META.items()):
        rec = source_scores[src_key]["rec"]
        val = max(rec, 10)
        sources_link.append(i)
        targets_link.append(ingestion_idx)
        values_link.append(val)
        link_colors.append(_rgba(meta["color"], 0.3))

    # Ingestion → Validation → Cache → Feature Engine
    for src_i, tgt_i in [
        (ingestion_idx, validation_idx),
        (validation_idx, cache_idx),
        (cache_idx, feature_idx),
    ]:
        sources_link.append(src_i)
        targets_link.append(tgt_i)
        values_link.append(300)
        link_colors.append(_rgba(C_ACCENT, 0.2))

    # Feature → Outputs
    for out_idx in [port_idx, route_idx, market_idx, risk_idx]:
        sources_link.append(feature_idx)
        targets_link.append(out_idx)
        values_link.append(75)
        link_colors.append(_rgba(C_PURPLE, 0.25))

    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            pad=18,
            thickness=18,
            line=dict(color=C_BORDER, width=0.5),
            label=labels,
            color=node_colors,
            hovertemplate="%{label}<extra></extra>",
        ),
        link=dict(
            source=sources_link,
            target=targets_link,
            value=values_link,
            color=link_colors,
            hovertemplate="%{source.label} → %{target.label}<extra></extra>",
        ),
    ))
    fig.update_layout(
        paper_bgcolor=C_BG,
        font={"color": C_TEXT, "size": 11, "family": "Inter, sans-serif"},
        height=340,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key="chart_pipeline_sankey")


# ── Section 4: Data quality metrics table ────────────────────────────────────

def _render_quality_metrics(scores: dict) -> None:
    _section_header("Data Quality Metrics", "Null rate, outlier detection, and schema compliance per source", "🔬")

    source_scores = scores["sources"]
    rows_html = ""

    for src_key, meta in _SOURCE_META.items():
        ss = source_scores[src_key]
        color = meta["color"]
        null_r = ss["null_rate"]
        outlier_r = ss["outlier_rate"]
        schema_c = ss["schema_c"]
        complete = ss["complete"]

        def _fmt_pct(v: float | None, invert: bool = False) -> str:
            if v is None:
                return f'<span style="color:{C_TEXT3}">—</span>'
            pct = v * 100
            if invert:
                bad = pct > 10
                ok = pct < 5
            else:
                bad = pct < 50
                ok = pct >= 80
            c = C_DANGER if bad else (C_HIGH if ok else C_WARN)
            return f'<span style="color:{c};font-family:{C_MONO};font-weight:600">{pct:.1f}%</span>'

        def _schema_badge(v: float | None) -> str:
            if v is None:
                return f'<span style="color:{C_TEXT3}">—</span>'
            pct = v * 100
            c = C_HIGH if pct >= 80 else (C_WARN if pct >= 50 else C_DANGER)
            fields_found = int(v * len(meta.get("schema_fields", [])))
            total_fields = len(meta.get("schema_fields", []))
            return (
                f'<span style="color:{c};font-family:{C_MONO};font-weight:600">'
                f'{pct:.0f}%</span>'
                f'<span style="color:{C_TEXT3};font-size:0.60rem"> ({fields_found}/{total_fields})</span>'
            )

        complete_color = C_HIGH if complete >= 70 else (C_WARN if complete >= 45 else C_DANGER)

        rows_html += f"""
        <tr style="border-bottom:1px solid {C_BORDER2}">
            <td style="padding:10px 14px;white-space:nowrap">
                <span style="font-size:1rem;margin-right:6px">{meta['icon']}</span>
                <span style="font-size:0.78rem;font-weight:600;color:{color}">{meta['label']}</span>
            </td>
            <td style="padding:10px 14px;text-align:center">{_status_badge_html(ss['status'])}</td>
            <td style="padding:10px 14px;text-align:center">{_fmt_pct(null_r, invert=True)}</td>
            <td style="padding:10px 14px;text-align:center">{_fmt_pct(outlier_r, invert=True)}</td>
            <td style="padding:10px 14px;text-align:center">{_schema_badge(schema_c)}</td>
            <td style="padding:10px 14px">
                <div style="display:flex;align-items:center;gap:8px">
                    <div style="flex:1;background:{_rgba(complete_color,0.12)};border-radius:99px;height:6px">
                        <div style="background:{complete_color};width:{min(complete,100):.0f}%;
                                    height:100%;border-radius:99px"></div>
                    </div>
                    <span style="font-size:0.70rem;color:{complete_color};font-weight:600;
                                 font-family:{C_MONO};min-width:32px">{complete:.0f}%</span>
                </div>
            </td>
        </tr>"""

    th_style = f"padding:8px 14px;font-size:0.62rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em"

    st.markdown(f"""
    <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden">
        <table style="width:100%;border-collapse:collapse">
            <thead>
                <tr style="background:{_rgba(C_ACCENT,0.06)};border-bottom:1px solid {C_BORDER}">
                    <th style="{th_style};text-align:left">Source</th>
                    <th style="{th_style};text-align:center">Status</th>
                    <th style="{th_style};text-align:center">Null Rate</th>
                    <th style="{th_style};text-align:center">Outlier Rate</th>
                    <th style="{th_style};text-align:center">Schema</th>
                    <th style="{th_style};text-align:left">Completeness</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)


# ── Section 5: Historical data availability heatmap ──────────────────────────

def _render_availability_heatmap(
    freight_data: Any,
    macro_data: Any,
    stock_data: Any,
    trade_data: Any,
    ais_data: Any,
) -> None:
    _section_header(
        "Historical Data Availability",
        "Coverage across the last 30 days — green = complete, amber = partial, red = missing",
        "📅",
    )

    import pandas as pd

    data_map = {
        "Stocks":  stock_data,
        "Macro":   macro_data,
        "Trade":   trade_data,
        "Freight": freight_data,
        "AIS":     ais_data,
    }

    today = pd.Timestamp.now().normalize()
    dates = [today - pd.Timedelta(days=i) for i in range(29, -1, -1)]
    date_strs = [d.strftime("%m/%d") for d in dates]

    z_rows: list[list[float]] = []
    source_labels: list[str] = []

    for src_label, dat in data_map.items():
        source_labels.append(src_label)
        row: list[float] = []
        for d in dates:
            if dat is None:
                row.append(0.0)
            elif isinstance(dat, dict):
                # Check if any DataFrame has rows near this date
                has_date = False
                try:
                    for v in dat.values():
                        if isinstance(v, pd.DataFrame) and not v.empty:
                            for date_col in ["date", "Date", "timestamp"]:
                                if date_col in v.columns:
                                    v_dates = pd.to_datetime(v[date_col], errors="coerce")
                                    if ((v_dates - d).abs().min() <= pd.Timedelta(days=3)):
                                        has_date = True
                                        break
                            if has_date:
                                break
                except Exception:
                    pass
                row.append(1.0 if has_date else 0.5)
            elif isinstance(dat, pd.DataFrame) and not dat.empty:
                row.append(0.8)  # data exists but we can't confirm date coverage
            else:
                row.append(0.0)
        z_rows.append(row)

    colorscale = [
        [0.0, _rgba(C_DANGER, 0.7)],
        [0.4, _rgba(C_WARN, 0.7)],
        [0.6, _rgba(C_WARN, 0.85)],
        [1.0, _rgba(C_HIGH, 0.85)],
    ]

    fig = go.Figure(go.Heatmap(
        z=z_rows,
        x=date_strs,
        y=source_labels,
        colorscale=colorscale,
        showscale=False,
        zmin=0.0,
        zmax=1.0,
        hoverongaps=False,
        hovertemplate="<b>%{y}</b><br>%{x}<br>Coverage: %{z:.0%}<extra></extra>",
        xgap=2,
        ygap=3,
    ))
    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font={"color": C_TEXT, "size": 11, "family": "Inter, sans-serif"},
        height=220,
        margin={"l": 80, "r": 10, "t": 10, "b": 40},
        xaxis={
            "tickfont": {"color": C_TEXT3, "size": 9},
            "tickangle": -30,
            "showgrid": False,
        },
        yaxis={
            "tickfont": {"color": C_TEXT2, "size": 11},
            "showgrid": False,
        },
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key="chart_availability_heatmap")


# ── Section 6: Anomaly detector ──────────────────────────────────────────────

def _render_anomaly_detector(
    freight_data: Any,
    macro_data: Any,
    stock_data: Any,
    trade_data: Any,
    ais_data: Any,
) -> None:
    _section_header("Data Anomaly Detector", "Suspicious values, sudden changes, or schema violations", "⚠️")

    import pandas as pd

    anomalies: list[dict] = []

    def _check_dataframe(df: pd.DataFrame, source: str, series_name: str) -> None:
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return
        num_df = df.select_dtypes(include="number")
        for col in num_df.columns:
            s = num_df[col].dropna()
            if len(s) < 4:
                continue
            mu, sd = s.mean(), s.std()
            if sd == 0:
                continue
            # Check last value
            last_val = s.iloc[-1]
            z = abs(last_val - mu) / sd
            if z > 3.0:
                anomalies.append({
                    "severity": "HIGH" if z > 4 else "MEDIUM",
                    "source": source,
                    "series": f"{series_name} › {col}",
                    "detail": f"Latest value {last_val:.2f} is {z:.1f}σ from mean ({mu:.2f} ± {sd:.2f})",
                })
            # Check for sudden change
            if len(s) >= 2:
                delta = abs(s.iloc[-1] - s.iloc[-2])
                if sd > 0 and delta / sd > 3.5:
                    anomalies.append({
                        "severity": "MEDIUM",
                        "source": source,
                        "series": f"{series_name} › {col}",
                        "detail": f"Single-step change of {delta:.2f} ({delta/sd:.1f}σ) detected",
                    })
            # Missing tail: last 5 values all null?
            tail_null = df[col].tail(5).isnull().all()
            if tail_null:
                anomalies.append({
                    "severity": "LOW",
                    "source": source,
                    "series": f"{series_name} › {col}",
                    "detail": "Last 5 observations are all null (possible stale feed)",
                })

    data_dict_map = {
        "Freight/FBX": freight_data,
        "Macro/FRED":  macro_data,
        "Stocks":      stock_data,
        "Trade/WITS":  trade_data,
        "AIS":         ais_data,
    }

    for src_label, dat in data_dict_map.items():
        try:
            if isinstance(dat, pd.DataFrame):
                _check_dataframe(dat, src_label, "main")
            elif isinstance(dat, dict):
                for k, v in list(dat.items())[:10]:
                    if isinstance(v, pd.DataFrame):
                        _check_dataframe(v, src_label, str(k))
        except Exception:
            pass

    if not anomalies:
        st.markdown(
            f'<div style="background:{_rgba(C_HIGH, 0.08)};border:1px solid {_rgba(C_HIGH, 0.25)};'
            f'border-radius:10px;padding:16px 20px;display:flex;align-items:center;gap:12px">'
            f'<span style="font-size:1.4rem">✅</span>'
            f'<div style="font-size:0.83rem;color:{C_HIGH};font-weight:600">No anomalies detected</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        return

    sev_color = {"HIGH": C_DANGER, "MEDIUM": C_WARN, "LOW": C_TEXT2}
    sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    anomalies.sort(key=lambda x: sev_order.get(x["severity"], 3))

    rows_html = ""
    for a in anomalies[:20]:
        color = sev_color.get(a["severity"], C_TEXT3)
        rows_html += f"""
        <tr style="border-bottom:1px solid {C_BORDER2}">
            <td style="padding:8px 12px;white-space:nowrap">
                {_status_badge_html(a['severity'])}
            </td>
            <td style="padding:8px 12px;font-size:0.72rem;color:{C_ACCENT};
                       font-weight:600;white-space:nowrap">{a['source']}</td>
            <td style="padding:8px 12px;font-size:0.71rem;color:{C_TEXT2};
                       font-family:{C_MONO}">{a['series']}</td>
            <td style="padding:8px 12px;font-size:0.71rem;color:{C_TEXT3}">{a['detail']}</td>
        </tr>"""

    th_s = f"padding:7px 12px;font-size:0.60rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em"
    st.markdown(f"""
    <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden">
        <div style="padding:10px 14px;background:{_rgba(C_DANGER, 0.06)};
                    border-bottom:1px solid {C_BORDER}">
            <span style="font-size:0.72rem;font-weight:700;color:{C_DANGER}">
                {len(anomalies)} anomaly(ies) detected</span>
            <span style="font-size:0.66rem;color:{C_TEXT3};margin-left:10px">
                Showing top {min(len(anomalies), 20)}</span>
        </div>
        <table style="width:100%;border-collapse:collapse">
            <thead>
                <tr style="border-bottom:1px solid {C_BORDER}">
                    <th style="{th_s};text-align:left">Severity</th>
                    <th style="{th_s};text-align:left">Source</th>
                    <th style="{th_s};text-align:left">Series</th>
                    <th style="{th_s};text-align:left">Detail</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)


# ── Section 7: Cache status table ────────────────────────────────────────────

def _render_cache_status(scores: dict) -> None:
    _section_header("Cache Status", "Size, age, and TTL remaining for each cached dataset", "🗄")

    source_scores = scores["sources"]
    now_ts = time.time()

    rows_html = ""
    for src_key, meta in _SOURCE_META.items():
        ss = source_scores[src_key]
        cache = ss["cache"]
        age_h = ss["age_h"]
        ttl_h = ss["ttl_h"]
        color = meta["color"]

        size_kb = cache["size_bytes"] / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb >= 0.1 else "—"
        age_str = _age_label(age_h)

        if age_h is not None:
            ttl_rem = max(ttl_h - age_h, 0.0)
            ttl_rem_str = _age_label(ttl_rem) if ttl_rem > 0.0833 else "Expired"
            ttl_pct = min((age_h / ttl_h) * 100, 100) if ttl_h else 100
            bar_color = C_HIGH if ttl_pct < 50 else (C_WARN if ttl_pct < 85 else C_DANGER)
        else:
            ttl_rem_str = "—"
            ttl_pct = 100.0
            bar_color = C_DANGER

        file_count = len(cache["files"])

        rows_html += f"""
        <tr style="border-bottom:1px solid {C_BORDER2}">
            <td style="padding:10px 14px;white-space:nowrap">
                <span style="font-size:1rem;margin-right:6px">{meta['icon']}</span>
                <span style="font-size:0.77rem;font-weight:600;color:{color}">{meta['label']}</span>
            </td>
            <td style="padding:10px 14px;text-align:center;font-family:{C_MONO};
                       font-size:0.72rem;color:{C_TEXT2}">{size_str}</td>
            <td style="padding:10px 14px;text-align:center;font-family:{C_MONO};
                       font-size:0.72rem;color:{C_TEXT2}">{age_str}</td>
            <td style="padding:10px 14px;min-width:120px">
                <div style="display:flex;align-items:center;gap:8px">
                    <div style="flex:1;background:{_rgba(bar_color,0.12)};border-radius:99px;height:5px">
                        <div style="background:{bar_color};width:{ttl_pct:.0f}%;
                                    height:100%;border-radius:99px"></div>
                    </div>
                    <span style="font-size:0.65rem;color:{bar_color};font-family:{C_MONO};
                                 min-width:40px;text-align:right">{ttl_rem_str}</span>
                </div>
            </td>
            <td style="padding:10px 14px;text-align:center;font-size:0.72rem;color:{C_TEXT3}">
                {file_count} file{'s' if file_count != 1 else ''}</td>
        </tr>"""

    th_s = f"padding:8px 14px;font-size:0.62rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em"
    st.markdown(f"""
    <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden">
        <table style="width:100%;border-collapse:collapse">
            <thead>
                <tr style="background:{_rgba(C_ACCENT,0.06)};border-bottom:1px solid {C_BORDER}">
                    <th style="{th_s};text-align:left">Dataset</th>
                    <th style="{th_s};text-align:center">Size</th>
                    <th style="{th_s};text-align:center">Cache Age</th>
                    <th style="{th_s};text-align:left">TTL Remaining</th>
                    <th style="{th_s};text-align:center">Files</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)


# ── Section 8: Data dependency graph ─────────────────────────────────────────

def _render_dependency_graph(scores: dict) -> None:
    _section_header(
        "Data Dependency Graph",
        "Which analytical modules depend on which data sources",
        "🕸",
    )

    source_scores = scores["sources"]
    all_sources = list(_SOURCE_META.keys())
    all_analyses = list(_DEPENDENCY_MAP.keys())

    # Build matrix: analyses × sources
    z_matrix: list[list[int]] = []
    hover_matrix: list[list[str]] = []
    for analysis in all_analyses:
        deps = _DEPENDENCY_MAP[analysis]
        row: list[int] = []
        hover_row: list[str] = []
        for src in all_sources:
            if src in deps:
                row.append(1)
                hover_row.append(f"<b>{analysis}</b> requires <b>{_SOURCE_META[src]['label']}</b>")
            else:
                row.append(0)
                hover_row.append(f"<b>{analysis}</b> does not use {_SOURCE_META[src]['label']}")
        z_matrix.append(row)
        hover_matrix.append(hover_row)

    src_labels = [meta["label"] for meta in _SOURCE_META.values()]

    colorscale = [
        [0.0, _rgba(C_ACCENT, 0.06)],
        [1.0, _rgba(C_ACCENT, 0.75)],
    ]

    fig = go.Figure(go.Heatmap(
        z=z_matrix,
        x=src_labels,
        y=all_analyses,
        colorscale=colorscale,
        showscale=False,
        zmin=0,
        zmax=1,
        hoverongaps=False,
        hovertemplate="%{customdata}<extra></extra>",
        customdata=hover_matrix,
        xgap=3,
        ygap=3,
    ))

    # Overlay dependency dots
    dot_x, dot_y = [], []
    for i, analysis in enumerate(all_analyses):
        for j, src in enumerate(all_sources):
            if src in _DEPENDENCY_MAP[analysis]:
                dot_x.append(src_labels[j])
                dot_y.append(analysis)

    if dot_x:
        fig.add_trace(go.Scatter(
            x=dot_x,
            y=dot_y,
            mode="markers",
            marker=dict(symbol="circle", size=10, color=C_ACCENT, opacity=0.9),
            hoverinfo="skip",
        ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font={"color": C_TEXT, "size": 11, "family": "Inter, sans-serif"},
        height=320,
        margin={"l": 130, "r": 20, "t": 10, "b": 80},
        xaxis={
            "tickfont": {"color": C_TEXT2, "size": 10},
            "tickangle": -30,
            "showgrid": False,
        },
        yaxis={
            "tickfont": {"color": C_TEXT2, "size": 10},
            "showgrid": False,
            "autorange": "reversed",
        },
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key="chart_dependency_graph")


# ── Section 9: Freshness timeline ────────────────────────────────────────────

def _render_freshness_timeline(scores: dict) -> None:
    _section_header("Cache Freshness Timeline", "Age of each cached feed relative to its TTL window", "⏱")

    source_scores = scores["sources"]
    source_labels: list[str] = []
    age_vals: list[float] = []
    ttl_vals: list[float] = []
    bar_colors: list[str] = []
    text_labels: list[str] = []

    for src_key, meta in _SOURCE_META.items():
        ss = source_scores[src_key]
        age_h = ss["age_h"]
        ttl_h = ss["ttl_h"]
        source_labels.append(meta["label"])
        ttl_vals.append(ttl_h)
        if age_h is None:
            age_vals.append(ttl_h)
            bar_colors.append(_rgba(C_DANGER, 0.8))
            text_labels.append("No cache")
        else:
            capped = min(age_h, ttl_h)
            age_vals.append(capped)
            frac = capped / ttl_h if ttl_h else 0.0
            bar_colors.append(
                _rgba(C_HIGH, 0.8) if frac < 0.5 else
                (_rgba(C_WARN, 0.8) if frac < 0.85 else _rgba(C_DANGER, 0.8))
            )
            text_labels.append(_age_label(age_h))

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="TTL Window",
        x=ttl_vals,
        y=source_labels,
        orientation="h",
        marker_color=f"rgba(255,255,255,0.04)",
        marker_line=dict(color=C_BORDER, width=1),
        hoverinfo="skip",
    ))
    fig.add_trace(go.Bar(
        name="Cache Age",
        x=age_vals,
        y=source_labels,
        orientation="h",
        marker_color=bar_colors,
        text=text_labels,
        textposition="inside",
        insidetextanchor="start",
        textfont={"color": "#0a0f1a", "size": 11, "family": C_MONO},
        hovertemplate="%{y}: %{text}<extra></extra>",
    ))
    fig.update_layout(
        barmode="overlay",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font={"color": C_TEXT, "size": 12},
        height=240,
        margin={"l": 20, "r": 30, "t": 10, "b": 40},
        showlegend=False,
        xaxis={
            "title": "Hours",
            "gridcolor": f"rgba(255,255,255,0.04)",
            "tickfont": {"color": C_TEXT3},
            "zerolinecolor": C_BORDER,
        },
        yaxis={"tickfont": {"color": C_TEXT2, "size": 11}},
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key="chart_freshness_timeline_v2")


# ── Section 10: Cache management panel ───────────────────────────────────────

def _render_cache_management() -> None:
    _section_header("Cache Management", "Clear stale or all cached datasets, view file inventory", "🗑")

    cache_dir = _CACHE_DIR
    parquet_files: list[Path] = sorted(
        cache_dir.rglob("*.parquet") if cache_dir.exists() else [],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    total_bytes = sum(f.stat().st_size for f in parquet_files)
    total_mb = total_bytes / (1024 * 1024)

    col_stats, col_stale, col_all = st.columns([3, 1, 1])

    with col_stats:
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
            f'padding:14px 18px;display:flex;gap:32px">'
            f'<div><div style="font-size:1.4rem;font-weight:800;color:{C_ACCENT}">'
            f'{len(parquet_files)}</div>'
            f'<div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.08em;margin-top:2px">Cached Files</div></div>'
            f'<div><div style="font-size:1.4rem;font-weight:800;color:{C_CYAN}">'
            f'{total_mb:.2f} MB</div>'
            f'<div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.08em;margin-top:2px">Total Size</div></div>'
            f'<div><div style="font-size:1.4rem;font-weight:800;color:{C_TEXT2}">'
            f'{_CACHE_DIR.name if _CACHE_DIR.exists() else "—"}</div>'
            f'<div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.08em;margin-top:2px">Cache Dir</div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with col_stale:
        if st.button("🧹 Clear Stale", use_container_width=True, key="btn_clear_stale"):
            deleted = 0
            now_ts = time.time()
            for meta in _SOURCE_META.values():
                ttl_secs = meta["ttl_hours"] * 3600
                for f in (list(cache_dir.glob(meta["pattern"] + ".parquet")) if cache_dir.exists() else []):
                    if (now_ts - f.stat().st_mtime) > ttl_secs:
                        try:
                            f.unlink()
                            deleted += 1
                            logger.info("Deleted stale cache: {}", f.name)
                        except Exception as exc:
                            logger.warning("Could not delete {}: {}", f.name, exc)
            st.success(f"{deleted} stale file(s) removed.")
            st.cache_data.clear()

    with col_all:
        if st.button("🔥 Clear All", use_container_width=True, key="btn_clear_all"):
            deleted = 0
            for f in parquet_files:
                try:
                    f.unlink()
                    deleted += 1
                    logger.info("Deleted cache file: {}", f.name)
                except Exception as exc:
                    logger.warning("Could not delete {}: {}", f.name, exc)
            st.warning(f"{deleted} file(s) removed. Reload to fetch fresh data.")
            st.cache_data.clear()

    if parquet_files:
        with st.expander(f"File inventory ({len(parquet_files)} files)", expanded=False,
                         key="dh_cache_file_listing_expander"):
            now_ts = time.time()
            rows_html = ""
            for f in parquet_files:
                age_h = (now_ts - f.stat().st_mtime) / 3600
                size_kb = f.stat().st_size / 1024
                age_str = _age_label(age_h)
                color = C_HIGH if age_h < 24 else (C_WARN if age_h < 168 else C_DANGER)
                rows_html += (
                    f'<tr style="border-bottom:1px solid {C_BORDER2}">'
                    f'<td style="padding:5px 12px;font-family:{C_MONO};font-size:0.69rem;'
                    f'color:{C_TEXT}">{f.name}</td>'
                    f'<td style="padding:5px 12px;font-size:0.69rem;color:{color};'
                    f'font-family:{C_MONO}">{age_str}</td>'
                    f'<td style="padding:5px 12px;font-size:0.69rem;color:{C_TEXT2};'
                    f'font-family:{C_MONO};text-align:right">{size_kb:.1f} KB</td>'
                    f'</tr>'
                )
            th_s = f"padding:6px 12px;font-size:0.60rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em"
            st.markdown(
                f'<div style="background:{C_CARD2};border-radius:8px;overflow:hidden">'
                f'<table style="width:100%;border-collapse:collapse">'
                f'<thead><tr style="border-bottom:1px solid {C_BORDER}">'
                f'<th style="{th_s};text-align:left">Filename</th>'
                f'<th style="{th_s};text-align:left">Age</th>'
                f'<th style="{th_s};text-align:right">Size</th>'
                f'</tr></thead><tbody>{rows_html}</tbody></table></div>',
                unsafe_allow_html=True,
            )
    else:
        st.info("No cached parquet files found. Run the pipeline once to populate the cache.")


# ── Public entry point ────────────────────────────────────────────────────────

def render(
    port_results: Any,
    route_results: Any,
    freight_data: Any,
    macro_data: Any,
    stock_data: Any,
    trade_data: Any,
    ais_data: Any = None,
) -> None:
    """Render the Data Quality & Health Monitoring dashboard."""
    logger.debug("Rendering tab_data_health (enhanced)")

    _inject_keyframes()

    # ── Page header
    hdr_col, btn_col = st.columns([5, 1])
    with hdr_col:
        st.markdown(
            f'<div class="dh-fade" style="margin-bottom:6px">'
            f'<div style="font-size:1.55rem;font-weight:800;color:{C_TEXT};'
            f'letter-spacing:-0.01em">Data Quality & Health Monitor</div>'
            f'<div style="font-size:0.82rem;color:{C_TEXT2};margin-top:3px">'
            f'Real-time pipeline health &nbsp;·&nbsp; Cache status &nbsp;·&nbsp; '
            f'Coverage gaps &nbsp;·&nbsp; Anomaly detection &nbsp;·&nbsp; '
            f'Data lineage</div></div>',
            unsafe_allow_html=True,
        )
    with btn_col:
        st.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)
        if st.button(
            "🔄 Refresh All",
            use_container_width=True,
            key="btn_refresh_all_dh",
            help="Clear all Streamlit cache and reload to fetch fresh data",
        ):
            st.cache_data.clear()
            st.success("Cache cleared — reloading…")
            st.rerun()

    # ── Pre-compute all quality scores once
    try:
        scores = _compute_scores(freight_data, macro_data, stock_data, trade_data, ais_data)
    except Exception as exc:
        logger.warning("Score computation error: {}", exc)
        scores = {
            "overall": 0.0, "freshness": 0.0, "coverage": 0.0, "completeness": 0.0,
            "sources": {k: {
                "status": "MISSING", "age_h": None, "ttl_h": float(v["ttl_hours"]),
                "rec": 0, "fresh": 0.0, "cov": 0.0, "complete": 0.0,
                "null_rate": None, "outlier_rate": None, "schema_c": None,
                "cache": {"files": [], "age_hours": None, "newest": None, "size_bytes": 0},
            } for k, v in _SOURCE_META.items()},
        }

    # ══════════════════════════════════════════════════════════════════════════
    # NEW SECTIONS — rendered first, above the existing sections
    # ══════════════════════════════════════════════════════════════════════════

    # ── NEW A. Data Health KPI Cards
    try:
        _render_new_kpi_cards(scores)
    except Exception as exc:
        logger.warning("New KPI cards error: {}", exc)
        st.warning(f"KPI cards unavailable: {exc}")

    _divider()

    # ── NEW B. Source Status Cards
    try:
        _render_new_source_cards(scores)
    except Exception as exc:
        logger.warning("New source cards error: {}", exc)
        st.warning(f"Source status cards unavailable: {exc}")

    _divider()

    # ── NEW C. Pipeline HTML Flow Diagram
    try:
        _render_new_pipeline_html(scores)
    except Exception as exc:
        logger.warning("New pipeline HTML error: {}", exc)
        st.warning(f"Pipeline diagram unavailable: {exc}")

    _divider()

    # ── NEW D. Quality Metrics Table
    try:
        _render_new_quality_table(scores)
    except Exception as exc:
        logger.warning("New quality table error: {}", exc)
        st.warning(f"Quality metrics table unavailable: {exc}")

    _divider()

    # ── NEW E. Historical Availability Heatmap
    try:
        _render_new_availability_heatmap(freight_data, macro_data, stock_data, trade_data, ais_data)
    except Exception as exc:
        logger.warning("New availability heatmap error: {}", exc)
        st.warning(f"Availability heatmap unavailable: {exc}")

    _divider()

    # ── NEW F. Cache Status Table
    try:
        _render_new_cache_status(scores)
    except Exception as exc:
        logger.warning("New cache status error: {}", exc)
        st.warning(f"Cache status unavailable: {exc}")

    _divider()

    # ══════════════════════════════════════════════════════════════════════════
    # EXISTING SECTIONS — preserved in original order below new sections
    # ══════════════════════════════════════════════════════════════════════════

    # ── 1. Overview scorecard banner
    try:
        _render_overview_banner(scores)
    except Exception as exc:
        logger.warning("Overview banner error: {}", exc)
        st.warning(f"Overview banner unavailable: {exc}")

    _divider()

    # ── 2. Data source cards
    try:
        _render_source_cards(scores)
    except Exception as exc:
        logger.warning("Source cards error: {}", exc)
        st.warning(f"Source cards unavailable: {exc}")

    _divider()

    # ── 3. Pipeline flow + Dependency graph (side by side)
    left_col, right_col = st.columns([3, 2])
    with left_col:
        try:
            _render_pipeline_flow(scores)
        except Exception as exc:
            logger.warning("Pipeline flow error: {}", exc)
            st.warning(f"Pipeline flow unavailable: {exc}")
    with right_col:
        try:
            _render_dependency_graph(scores)
        except Exception as exc:
            logger.warning("Dependency graph error: {}", exc)
            st.warning(f"Dependency graph unavailable: {exc}")

    _divider()

    # ── 4. Quality metrics table + Freshness timeline
    left2, right2 = st.columns([3, 2])
    with left2:
        try:
            _render_quality_metrics(scores)
        except Exception as exc:
            logger.warning("Quality metrics error: {}", exc)
            st.warning(f"Quality metrics unavailable: {exc}")
    with right2:
        try:
            _render_freshness_timeline(scores)
        except Exception as exc:
            logger.warning("Freshness timeline error: {}", exc)
            st.warning(f"Freshness timeline unavailable: {exc}")

    _divider()

    # ── 5. Historical availability heatmap
    try:
        _render_availability_heatmap(freight_data, macro_data, stock_data, trade_data, ais_data)
    except Exception as exc:
        logger.warning("Availability heatmap error: {}", exc)
        st.warning(f"Availability heatmap unavailable: {exc}")

    _divider()

    # ── 6. Anomaly detector
    try:
        _render_anomaly_detector(freight_data, macro_data, stock_data, trade_data, ais_data)
    except Exception as exc:
        logger.warning("Anomaly detector error: {}", exc)
        st.warning(f"Anomaly detector unavailable: {exc}")

    _divider()

    # ── 7. Cache status table
    try:
        _render_cache_status(scores)
    except Exception as exc:
        logger.warning("Cache status error: {}", exc)
        st.warning(f"Cache status unavailable: {exc}")

    _divider()

    # ── 8. Cache management panel
    try:
        _render_cache_management()
    except Exception as exc:
        logger.warning("Cache management error: {}", exc)
        st.warning(f"Cache management unavailable: {exc}")
