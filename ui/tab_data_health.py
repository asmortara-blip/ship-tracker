"""Data Quality & Health Monitoring tab.

render(port_results, route_results, freight_data, macro_data,
       stock_data, trade_data, ais_data=None)

Sections
--------
1. Data Source Status Grid (6 cards)
2. Coverage Matrix heatmap  (ports × sources)
3. Data Freshness Timeline  (horizontal bar chart)
4. Quality Score gauge
5. Fallback Usage Summary   (table)
6. Cache Management         (size, clear buttons, file listing)
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ── Colour palette ────────────────────────────────────────────────────────────
C_BG     = "#0a0f1a"
C_CARD   = "#1a2235"
C_BORDER = "rgba(255,255,255,0.08)"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_TEXT3  = "#64748b"
C_HIGH   = "#10b981"   # green
C_WARN   = "#f59e0b"   # amber
C_DANGER = "#ef4444"   # red
C_ACCENT = "#3b82f6"   # blue

# ── Static metadata for each data source ──────────────────────────────────────
_CACHE_DIR = Path(__file__).parent.parent / "cache"

_SOURCE_META: dict[str, dict] = {
    "yfinance": {
        "icon": "📈",
        "label": "Stock Prices",
        "pattern": "*stock*",
        "ttl_hours": 1,
        "vintage": "Intraday / daily",
        "entity_key": "stocks",
    },
    "FRED": {
        "icon": "🏦",
        "label": "Macro / FRED",
        "pattern": "*fred*",
        "ttl_hours": 24,
        "vintage": "Weekly releases",
        "entity_key": "macro",
    },
    "WorldBank": {
        "icon": "🌍",
        "label": "World Bank",
        "pattern": "*worldbank*",
        "ttl_hours": 168,
        "vintage": "2023 annual data",
        "entity_key": "wb",
    },
    "Trade/WITS": {
        "icon": "🔄",
        "label": "Trade (WITS/Comtrade)",
        "pattern": "*wits*",
        "ttl_hours": 168,
        "vintage": "Monthly releases",
        "entity_key": "trade",
    },
    "Freight/FBX": {
        "icon": "🚢",
        "label": "Freight (FBX)",
        "pattern": "*fbx*",
        "ttl_hours": 24,
        "vintage": "Daily index",
        "entity_key": "freight",
    },
    "AIS/Synthetic": {
        "icon": "📡",
        "label": "AIS Vessel Positions",
        "pattern": "*ais*",
        "ttl_hours": 6,
        "vintage": "Synthetic baselines",
        "entity_key": "ais",
    },
}

# Ports tracked across the platform
_TRACKED_PORTS: list[str] = [
    "Shanghai", "Singapore", "Rotterdam", "Los Angeles", "Hamburg",
    "Antwerp", "Ningbo", "Shenzhen", "Busan", "Hong Kong",
    "Qingdao", "Guangzhou", "Tianjin", "Xiamen", "Kaohsiung",
    "Port Klang", "Dubai", "Jeddah", "New York", "Savannah",
    "Long Beach", "Tokyo", "Jakarta", "Chennai", "Mumbai",
]

# Columns shown in the Coverage Matrix
_COV_SOURCES = ["Trade", "AIS", "WorldBank", "Freight"]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _cache_info(pattern: str) -> dict:
    """Return mtime age and file list for a glob pattern in the cache dir."""
    files = list(_CACHE_DIR.glob(pattern + ".parquet")) if _CACHE_DIR.exists() else []
    if not files:
        return {"files": [], "age_hours": None, "newest": None}
    newest = max(files, key=lambda f: f.stat().st_mtime)
    age_hours = (time.time() - newest.stat().st_mtime) / 3600
    return {"files": files, "age_hours": round(age_hours, 2), "newest": newest}


def _age_label(age_hours: float | None) -> str:
    if age_hours is None:
        return "—"
    if age_hours < 0.083:   # < 5 min
        return "Just now"
    if age_hours < 1.0:
        return str(int(age_hours * 60)) + "m ago"
    if age_hours < 24.0:
        return str(round(age_hours, 1)) + "h ago"
    return str(round(age_hours / 24, 1)) + "d ago"


def _record_count(data: Any) -> int:
    """Best-effort count of records in a data object."""
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


def _status_badge(status: str) -> str:
    """Return a coloured HTML badge string for a status label."""
    palette = {
        "LIVE":     (C_HIGH,   "rgba(16,185,129,0.15)"),
        "FRESH":    (C_HIGH,   "rgba(16,185,129,0.15)"),
        "STALE":    (C_WARN,   "rgba(245,158,11,0.15)"),
        "FALLBACK": (C_WARN,   "rgba(245,158,11,0.15)"),
        "ERROR":    (C_DANGER, "rgba(239,68,68,0.15)"),
        "NO DATA":  (C_DANGER, "rgba(239,68,68,0.15)"),
    }
    color, bg = palette.get(status.upper(), (C_TEXT2, "rgba(148,163,184,0.10)"))
    return (
        '<span style="background:' + bg + "; color:" + color
        + "; border:1px solid " + color + "33"
        + '; border-radius:4px; padding:1px 7px; font-size:0.65rem;'
        ' font-weight:700; letter-spacing:0.06em">'
        + status.upper() + "</span>"
    )


def _section_title(text: str, subtitle: str = "") -> None:
    sub_html = (
        '<div style="color:' + C_TEXT2 + '; font-size:0.83rem; margin-top:3px">'
        + subtitle + "</div>"
        if subtitle
        else ""
    )
    st.markdown(
        '<div style="margin-bottom:14px; margin-top:18px">'
        '<div style="font-size:1.05rem; font-weight:700; color:' + C_TEXT + '">'
        + text + "</div>" + sub_html + "</div>",
        unsafe_allow_html=True,
    )


# ── Section 1: Status Grid ────────────────────────────────────────────────────

def _render_status_grid(
    freight_data: Any,
    macro_data: Any,
    stock_data: Any,
    trade_data: Any,
    ais_data: Any,
) -> None:
    _section_title("Data Source Status", "Live health of each upstream data feed")

    data_map = {
        "yfinance":     stock_data,
        "FRED":         macro_data,
        "WorldBank":    None,       # comes from wb cache only
        "Trade/WITS":   trade_data,
        "Freight/FBX":  freight_data,
        "AIS/Synthetic": ais_data,
    }

    cols = st.columns(3)
    for idx, (src_key, meta) in enumerate(_SOURCE_META.items()):
        col = cols[idx % 3]
        info = _cache_info(meta["pattern"])
        data_obj = data_map[src_key]
        rec_count = _record_count(data_obj)
        age_h = info["age_hours"]

        # Determine status
        if age_h is None and rec_count == 0:
            badge = "NO DATA"
            age_str = "Not loaded"
        elif age_h is not None and age_h < meta["ttl_hours"]:
            badge = "LIVE"
            age_str = _age_label(age_h)
        elif age_h is not None:
            badge = "STALE"
            age_str = _age_label(age_h)
        else:
            badge = "FALLBACK"
            age_str = "In-memory"

        # Coverage: what fraction of tracked ports have data in this feed
        if isinstance(data_obj, dict) and data_obj:
            covered = min(len(data_obj), 25)
            cov_pct = int(covered / 25 * 100)
        elif rec_count > 0:
            cov_pct = 100
        else:
            cov_pct = 0

        cov_color = C_HIGH if cov_pct >= 70 else (C_WARN if cov_pct >= 30 else C_DANGER)

        card_html = (
            '<div style="background:' + C_CARD + "; border:1px solid " + C_BORDER
            + "; border-radius:12px; padding:16px 18px; margin-bottom:12px;"
            " min-height:148px\">"
            # header row
            '<div style="display:flex; justify-content:space-between; align-items:flex-start">'
            '<div><span style="font-size:1.3rem">' + meta["icon"] + "</span>"
            '<div style="font-size:0.82rem; font-weight:700; color:' + C_TEXT
            + '; margin-top:4px">' + meta["label"] + "</div></div>"
            + _status_badge(badge) + "</div>"
            # stats
            '<div style="margin-top:12px; display:grid; grid-template-columns:1fr 1fr; gap:6px">'
            '<div style="font-size:0.68rem; color:' + C_TEXT3 + '">Last updated</div>'
            '<div style="font-size:0.68rem; color:' + C_TEXT2 + '; text-align:right">'
            + age_str + "</div>"
            '<div style="font-size:0.68rem; color:' + C_TEXT3 + '">Records</div>'
            '<div style="font-size:0.68rem; color:' + C_TEXT2 + '; text-align:right">'
            + (str(rec_count) if rec_count else "—") + "</div>"
            '<div style="font-size:0.68rem; color:' + C_TEXT3 + '">Coverage</div>'
            '<div style="font-size:0.68rem; color:' + cov_color + '; text-align:right; font-weight:600">'
            + str(cov_pct) + "%</div>"
            '<div style="font-size:0.68rem; color:' + C_TEXT3 + '">Vintage</div>'
            '<div style="font-size:0.68rem; color:' + C_TEXT2 + '; text-align:right">'
            + meta["vintage"] + "</div>"
            "</div></div>"
        )
        col.markdown(card_html, unsafe_allow_html=True)


# ── Section 2: Coverage Matrix ────────────────────────────────────────────────

def _render_coverage_matrix(trade_data: Any, ais_data: Any, macro_data: Any, freight_data: Any) -> None:
    _section_title("Port Data Coverage Matrix", "Green = data available  ·  Red = missing")

    import pandas as pd

    def _has_port(data: Any, port: str) -> bool:
        if not data:
            return False
        if isinstance(data, dict):
            port_lower = port.lower()
            for k in data.keys():
                if port_lower in str(k).lower():
                    return True
            return False
        if isinstance(data, pd.DataFrame):
            for col in data.columns:
                if port.lower() in str(col).lower():
                    return True
            if hasattr(data, "index"):
                for idx in data.index:
                    if port.lower() in str(idx).lower():
                        return True
        return False

    source_data_map = {
        "Trade": trade_data,
        "AIS":   ais_data,
        "WB":    None,          # WorldBank typically returns aggregate frames
        "Freight": freight_data,
    }

    # Build z matrix: 1 = present, 0 = missing
    z_matrix: list[list[int]] = []
    for port in _TRACKED_PORTS:
        row: list[int] = []
        for src_label in _COV_SOURCES:
            dat = source_data_map[src_label]
            if dat is None:
                row.append(0)
            elif _has_port(dat, port):
                row.append(1)
            else:
                # For AIS and WorldBank, assume synthetic/global coverage
                row.append(1 if src_label in ("AIS", "WB") else 0)
        z_matrix.append(row)

    # Colorscale: 0 = red, 1 = green
    colorscale = [[0.0, C_DANGER], [1.0, C_HIGH]]

    fig = go.Figure(go.Heatmap(
        z=z_matrix,
        x=_COV_SOURCES,
        y=_TRACKED_PORTS,
        colorscale=colorscale,
        showscale=False,
        hoverongaps=False,
        hovertemplate="%{y} / %{x}: %{z}<extra></extra>",
        zmin=0,
        zmax=1,
    ))
    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font={"color": C_TEXT, "size": 11},
        height=620,
        margin={"l": 110, "r": 20, "t": 20, "b": 40},
        xaxis={"tickfont": {"color": C_TEXT2, "size": 11}},
        yaxis={"tickfont": {"color": C_TEXT2, "size": 10}, "autorange": "reversed"},
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ── Section 3: Freshness Timeline ─────────────────────────────────────────────

def _render_freshness_timeline() -> None:
    _section_title("Data Freshness Timeline", "How current is each cache relative to its TTL")

    source_labels: list[str] = []
    age_vals: list[float] = []
    ttl_vals: list[float] = []
    bar_colors: list[str] = []
    text_labels: list[str] = []

    for src_key, meta in _SOURCE_META.items():
        info = _cache_info(meta["pattern"])
        age_h = info["age_hours"]
        ttl_h = float(meta["ttl_hours"])
        source_labels.append(meta["label"])
        ttl_vals.append(ttl_h)

        if age_h is None:
            age_vals.append(ttl_h)       # fill full bar to indicate completely stale
            bar_colors.append(C_DANGER)
            text_labels.append("No cache")
        else:
            capped = min(age_h, ttl_h)
            age_vals.append(capped)
            frac = capped / ttl_h if ttl_h else 0.0
            if frac < 0.5:
                bar_colors.append(C_HIGH)
            elif frac < 0.85:
                bar_colors.append(C_WARN)
            else:
                bar_colors.append(C_DANGER)
            text_labels.append(_age_label(age_h))

    fig = go.Figure()

    # TTL background bar (grey)
    fig.add_trace(go.Bar(
        name="TTL Period",
        x=ttl_vals,
        y=source_labels,
        orientation="h",
        marker_color="rgba(255,255,255,0.06)",
        hoverinfo="skip",
    ))

    # Age-filled bar
    fig.add_trace(go.Bar(
        name="Cache Age",
        x=age_vals,
        y=source_labels,
        orientation="h",
        marker_color=bar_colors,
        text=text_labels,
        textposition="inside",
        insidetextanchor="start",
        textfont={"color": C_BG, "size": 11, "family": "monospace"},
        hovertemplate="%{y}: %{text}<extra></extra>",
    ))

    fig.update_layout(
        barmode="overlay",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font={"color": C_TEXT, "size": 12},
        height=260,
        margin={"l": 20, "r": 20, "t": 10, "b": 30},
        showlegend=False,
        xaxis={
            "title": "Hours",
            "gridcolor": "rgba(255,255,255,0.05)",
            "tickfont": {"color": C_TEXT3},
        },
        yaxis={"tickfont": {"color": C_TEXT2, "size": 11}},
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ── Section 4: Quality Score Gauge ────────────────────────────────────────────

def _compute_quality_score(
    freight_data: Any,
    macro_data: Any,
    stock_data: Any,
    trade_data: Any,
    ais_data: Any,
) -> float:
    """Compute a 0-100 data quality score."""
    data_map = {
        "yfinance":      stock_data,
        "FRED":          macro_data,
        "WorldBank":     None,
        "Trade/WITS":    trade_data,
        "Freight/FBX":   freight_data,
        "AIS/Synthetic": ais_data,
    }

    scores: list[float] = []
    for src_key, meta in _SOURCE_META.items():
        info = _cache_info(meta["pattern"])
        age_h = info["age_hours"]
        ttl_h = float(meta["ttl_hours"])
        dat = data_map[src_key]
        rec_count = _record_count(dat)

        # Freshness component (0–50)
        if age_h is None:
            fresh_score = 0.0
        else:
            frac_used = min(age_h / ttl_h, 1.0)
            fresh_score = (1.0 - frac_used) * 50.0

        # Coverage component (0–30): did we load any records?
        cov_score = 30.0 if rec_count > 0 else 0.0

        # Authenticity component (0–20): penalise synthetic / fallback sources
        # AIS and WorldBank are synthetic/proxied so score halved
        if src_key in ("AIS/Synthetic", "WorldBank"):
            auth_score = 10.0
        elif rec_count > 0:
            auth_score = 20.0
        else:
            auth_score = 0.0

        scores.append(fresh_score + cov_score + auth_score)

    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 1)


def _render_quality_gauge(score: float) -> None:
    _section_title("Overall Data Quality Score", "Based on freshness, coverage, and data authenticity")

    color = C_HIGH if score >= 70 else (C_WARN if score >= 45 else C_DANGER)

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number={"suffix": "/100", "font": {"size": 36, "color": color}},
        gauge={
            "axis": {
                "range": [0, 100],
                "tickfont": {"color": C_TEXT3, "size": 11},
                "tickwidth": 1,
                "tickcolor": C_BORDER,
            },
            "bar": {"color": color},
            "bgcolor": C_CARD,
            "borderwidth": 1,
            "bordercolor": C_BORDER,
            "steps": [
                {"range": [0, 45],  "color": "rgba(239,68,68,0.10)"},
                {"range": [45, 70], "color": "rgba(245,158,11,0.10)"},
                {"range": [70, 100],"color": "rgba(16,185,129,0.10)"},
            ],
            "threshold": {
                "line": {"color": C_TEXT, "width": 2},
                "thickness": 0.75,
                "value": score,
            },
        },
    ))
    fig.update_layout(
        paper_bgcolor=C_BG,
        font={"color": C_TEXT},
        height=260,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ── Section 5: Fallback Usage Summary ────────────────────────────────────────

def _render_fallback_summary(freight_data: Any, trade_data: Any, ais_data: Any) -> None:
    _section_title("Fallback & Synthetic Data Usage", "Which feeds are real vs estimated / synthetic")

    rows = [
        {
            "Source": "AIS / Vessel Positions",
            "Status": "Synthetic",
            "Detail": "100% synthetic baselines — real AISHub feed not yet connected",
            "Action": "Connect AISHub API key to switch to live positions",
        },
        {
            "Source": "Freight Rates (FBX)",
            "Status": "Real" if freight_data else "Fallback",
            "Detail": (
                "Real Freightos Baltic Index data via web scrape"
                if freight_data
                else "Static fallback rates used (FBX scrape failed)"
            ),
            "Action": "Monitor scrape endpoint; FBX charges for direct API",
        },
        {
            "Source": "Trade Flows (WITS)",
            "Status": "Real" if trade_data else "Fallback",
            "Detail": (
                "WITS / Comtrade API data loaded"
                if trade_data
                else "World Bank merchandise trade used as fallback"
            ),
            "Action": "Comtrade premium API improves timeliness",
        },
        {
            "Source": "Macro (FRED)",
            "Status": "Real",
            "Detail": "FRED API — free, covers 800k series",
            "Action": "Requires FRED_API_KEY env var",
        },
        {
            "Source": "Stock Prices (yfinance)",
            "Status": "Real",
            "Detail": "Yahoo Finance — no key required, rate-limited",
            "Action": "For intraday, upgrade to paid data provider",
        },
        {
            "Source": "Port Throughput (World Bank)",
            "Status": "Real",
            "Detail": "2023 annual container throughput (TEU) from World Bank Open Data",
            "Action": "Data lags ~18 months; UNCTAD provides more recent data",
        },
    ]

    status_color_map = {
        "Real":     C_HIGH,
        "Synthetic": C_WARN,
        "Fallback": C_DANGER,
    }

    for row in rows:
        sc = status_color_map.get(row["Status"], C_TEXT2)
        card_html = (
            '<div style="background:' + C_CARD + "; border:1px solid " + C_BORDER
            + "; border-radius:10px; padding:14px 18px; margin-bottom:8px;"
            ' display:flex; gap:16px; align-items:flex-start">'
            '<div style="min-width:110px">'
            '<div style="font-size:0.78rem; font-weight:700; color:' + C_TEXT + '">'
            + row["Source"] + "</div>"
            '<span style="background:' + sc + "22; color:" + sc
            + "; border:1px solid " + sc + "44"
            + '; border-radius:4px; padding:1px 7px; font-size:0.62rem;'
            ' font-weight:700">' + row["Status"] + "</span></div>"
            '<div style="flex:1">'
            '<div style="font-size:0.78rem; color:' + C_TEXT2 + '">'
            + row["Detail"] + "</div>"
            '<div style="font-size:0.70rem; color:' + C_TEXT3 + '; margin-top:4px">💡 '
            + row["Action"] + "</div></div></div>"
        )
        st.markdown(card_html, unsafe_allow_html=True)


# ── Section 6: Cache Management ───────────────────────────────────────────────

def _render_cache_management() -> None:
    _section_title("Cache Management", "Parquet file storage used by the data pipeline")

    cache_dir = _CACHE_DIR
    parquet_files: list[Path] = list(cache_dir.rglob("*.parquet")) if cache_dir.exists() else []
    total_bytes = sum(f.stat().st_size for f in parquet_files)
    total_mb = total_bytes / (1024 * 1024)

    col_info, col_btns = st.columns([2, 1])
    with col_info:
        st.markdown(
            '<div style="background:' + C_CARD + "; border:1px solid " + C_BORDER
            + '; border-radius:10px; padding:14px 18px">'
            '<div style="font-size:0.85rem; color:' + C_TEXT2 + '">'
            "Total parquet files: <b style=\"color:" + C_TEXT + '">'
            + str(len(parquet_files)) + "</b></div>"
            '<div style="font-size:0.85rem; color:' + C_TEXT2 + '; margin-top:4px">'
            "Total size: <b style=\"color:" + C_TEXT + '">'
            + str(round(total_mb, 2)) + " MB</b></div>"
            "</div>",
            unsafe_allow_html=True,
        )

    with col_btns:
        if st.button("🗑️ Clear Stale Cache", use_container_width=True, key="btn_clear_stale"):
            deleted = 0
            now_ts = time.time()
            # Map patterns to TTL from source meta
            for meta in _SOURCE_META.values():
                ttl_secs = meta["ttl_hours"] * 3600
                for f in list(cache_dir.glob(meta["pattern"] + ".parquet")) if cache_dir.exists() else []:
                    if (now_ts - f.stat().st_mtime) > ttl_secs:
                        try:
                            f.unlink()
                            deleted += 1
                            logger.info("Deleted stale cache file: {}", f.name)
                        except Exception as exc:
                            logger.warning("Could not delete {}: {}", f.name, exc)
            st.success(str(deleted) + " stale file(s) deleted. Reload the app to refresh data.")
            st.cache_data.clear()

        if st.button("🔥 Clear All Cache", use_container_width=True, key="btn_clear_all"):
            deleted = 0
            for f in parquet_files:
                try:
                    f.unlink()
                    deleted += 1
                    logger.info("Deleted cache file: {}", f.name)
                except Exception as exc:
                    logger.warning("Could not delete {}: {}", f.name, exc)
            st.warning(str(deleted) + " file(s) deleted. Reload the app to fetch fresh data.")
            st.cache_data.clear()

    # Expandable file listing
    if parquet_files:
        with st.expander("Cache file listing (" + str(len(parquet_files)) + " files)", expanded=False):
            now_ts = time.time()
            rows_html = ""
            for f in sorted(parquet_files, key=lambda x: x.stat().st_mtime, reverse=True):
                age_h = (now_ts - f.stat().st_mtime) / 3600
                size_kb = f.stat().st_size / 1024
                age_str = _age_label(age_h)
                color = C_HIGH if age_h < 24 else (C_WARN if age_h < 168 else C_DANGER)
                rows_html += (
                    '<tr>'
                    '<td style="padding:4px 10px; font-family:monospace; font-size:0.72rem;'
                    ' color:' + C_TEXT + '">' + f.name + "</td>"
                    '<td style="padding:4px 10px; font-size:0.72rem; color:' + color + '">'
                    + age_str + "</td>"
                    '<td style="padding:4px 10px; font-size:0.72rem; color:' + C_TEXT2 + '">'
                    + str(round(size_kb, 1)) + " KB</td></tr>"
                )
            st.markdown(
                '<table style="width:100%; border-collapse:collapse">'
                '<thead><tr>'
                '<th style="padding:4px 10px; font-size:0.72rem; color:' + C_TEXT3
                + '; text-align:left">File</th>'
                '<th style="padding:4px 10px; font-size:0.72rem; color:' + C_TEXT3
                + '; text-align:left">Age</th>'
                '<th style="padding:4px 10px; font-size:0.72rem; color:' + C_TEXT3
                + '; text-align:left">Size</th>'
                "</tr></thead><tbody>"
                + rows_html
                + "</tbody></table>",
                unsafe_allow_html=True,
            )
    else:
        st.info("No parquet cache files found. Run the app once to populate the cache.")


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
    logger.debug("Rendering tab_data_health")

    st.markdown(
        '<div style="font-size:1.5rem; font-weight:800; color:' + C_TEXT
        + '; margin-bottom:4px">Data Quality & Health Monitor</div>'
        '<div style="font-size:0.85rem; color:' + C_TEXT2
        + '; margin-bottom:20px">Real-time view of data pipeline health,'
        " cache status, coverage gaps, and fallback usage</div>",
        unsafe_allow_html=True,
    )

    # ── 1. Status grid
    try:
        _render_status_grid(freight_data, macro_data, stock_data, trade_data, ais_data)
    except Exception as exc:
        logger.warning("Status grid error: {}", exc)
        st.warning("Status grid unavailable: " + str(exc))

    st.divider()

    # ── 2. Coverage matrix + 3. Freshness timeline (side by side)
    left, right = st.columns([3, 2])
    with left:
        try:
            _render_coverage_matrix(trade_data, ais_data, macro_data, freight_data)
        except Exception as exc:
            logger.warning("Coverage matrix error: {}", exc)
            st.warning("Coverage matrix unavailable: " + str(exc))
    with right:
        try:
            _render_freshness_timeline()
        except Exception as exc:
            logger.warning("Freshness timeline error: {}", exc)
            st.warning("Freshness timeline unavailable: " + str(exc))

        st.markdown("<div style='margin-top:24px'></div>", unsafe_allow_html=True)

        # ── 4. Quality gauge (in the right column below timeline)
        try:
            score = _compute_quality_score(freight_data, macro_data, stock_data, trade_data, ais_data)
            _render_quality_gauge(score)
        except Exception as exc:
            logger.warning("Quality gauge error: {}", exc)
            st.warning("Quality gauge unavailable: " + str(exc))

    st.divider()

    # ── 5. Fallback summary
    try:
        _render_fallback_summary(freight_data, trade_data, ais_data)
    except Exception as exc:
        logger.warning("Fallback summary error: {}", exc)
        st.warning("Fallback summary unavailable: " + str(exc))

    st.divider()

    # ── 6. Cache management
    try:
        _render_cache_management()
    except Exception as exc:
        logger.warning("Cache management error: {}", exc)
        st.warning("Cache management unavailable: " + str(exc))
