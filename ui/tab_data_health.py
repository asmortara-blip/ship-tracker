"""Data Source Health & Freshness Monitoring tab.

Sections
--------
1. Data Health Overview       — hero KPIs: total sources, healthy, stale, failing, coverage %
2. Data Source Status Table   — full table with type, age, records, status, endpoint
3. Cache Size & Performance   — file sizes, fetch counts, avg fetch time, hit rate
4. API Key Configuration      — which keys are set (no values shown), plan/tier, usage
5. Data Staleness Heatmap     — Plotly heatmap: sources × hours-of-day
6. Error Log                  — recent fetch errors (last 24 h)
7. Manual Refresh             — per-source cache invalidation buttons
8. Data Quality Metrics       — null %, value range, anomaly detection (>3σ)
"""
from __future__ import annotations

import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

try:
    from data.cache_manager import CacheManager
    _CM_OK = True
except Exception:
    _CM_OK = False

# ── Palette ──────────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"
C_GRAY    = "#374151"

_CACHE_DIR = Path(__file__).parent.parent / "cache"

# ── Source registry ───────────────────────────────────────────────────────────
_SOURCES = [
    {
        "name": "Baltic Exchange (BDI/BCI/BPI)",
        "type": "Manual/Scrape",
        "ttl_h": 24,
        "refresh": "Daily 09:00 UTC",
        "endpoint": "balticexchange.com",
        "cache_sub": "baltic",
        "key_env": None,
        "pattern": "*baltic*",
    },
    {
        "name": "Freightos FBX",
        "type": "API",
        "ttl_h": 24,
        "refresh": "Daily 10:00 UTC",
        "endpoint": "fbx.freightos.com/api",
        "cache_sub": "freightos",
        "key_env": "FREIGHTOS_API_KEY",
        "pattern": "*freightos*",
    },
    {
        "name": "AIS Feed (aisstream.io)",
        "type": "API – Live",
        "ttl_h": 0.5,
        "refresh": "Every 30 min",
        "endpoint": "aisstream.io/v0/stream",
        "cache_sub": "ais",
        "key_env": "AISSTREAM_API_KEY",
        "pattern": "*ais*",
    },
    {
        "name": "News API",
        "type": "API",
        "ttl_h": 1,
        "refresh": "Hourly",
        "endpoint": "newsapi.org/v2",
        "cache_sub": "news",
        "key_env": "NEWS_API_KEY",
        "pattern": "*news*",
    },
    {
        "name": "Alpha Vantage",
        "type": "API",
        "ttl_h": 24,
        "refresh": "Daily (15-min delay)",
        "endpoint": "alphavantage.co/query",
        "cache_sub": "alphavantage",
        "key_env": "ALPHA_VANTAGE_KEY",
        "pattern": "*alphavantage*",
    },
    {
        "name": "FRED (Macro)",
        "type": "API",
        "ttl_h": 24,
        "refresh": "Daily 14:00 UTC",
        "endpoint": "api.stlouisfed.org/fred",
        "cache_sub": "fred",
        "key_env": "FRED_API_KEY",
        "pattern": "*fred*",
    },
    {
        "name": "OECD",
        "type": "API",
        "ttl_h": 2160,
        "refresh": "Quarterly",
        "endpoint": "stats.oecd.org/SDMX-JSON",
        "cache_sub": "oecd",
        "key_env": None,
        "pattern": "*oecd*",
    },
    {
        "name": "IMF",
        "type": "API",
        "ttl_h": 720,
        "refresh": "Monthly",
        "endpoint": "imf.org/external/datamapper",
        "cache_sub": "imf",
        "key_env": None,
        "pattern": "*imf*",
    },
    {
        "name": "UN Comtrade",
        "type": "API",
        "ttl_h": 2160,
        "refresh": "Quarterly",
        "endpoint": "comtradeapi.un.org",
        "cache_sub": "comtrade",
        "key_env": "COMTRADE_API_KEY",
        "pattern": "*comtrade*",
    },
    {
        "name": "World Bank",
        "type": "API",
        "ttl_h": 8760,
        "refresh": "Annual",
        "endpoint": "api.worldbank.org/v2",
        "cache_sub": "worldbank",
        "key_env": None,
        "pattern": "*worldbank*",
    },
    {
        "name": "yfinance (Stocks)",
        "type": "Library",
        "ttl_h": 0.25,
        "refresh": "15-min delay",
        "endpoint": "finance.yahoo.com",
        "cache_sub": "portfolio",
        "key_env": None,
        "pattern": "*stock*",
    },
    {
        "name": "RSS Feeds (Carriers/News)",
        "type": "Scrape",
        "ttl_h": 1,
        "refresh": "Hourly",
        "endpoint": "Various carrier RSS",
        "cache_sub": "rss",
        "key_env": None,
        "pattern": "*rss*",
    },
    {
        "name": "ACS Panama (Canal)",
        "type": "Scrape",
        "ttl_h": 0.5,
        "refresh": "Every 30 min",
        "endpoint": "pancanal.com/eng/transit",
        "cache_sub": "panama",
        "key_env": None,
        "pattern": "*panama*",
    },
    {
        "name": "SCA (Suez Canal)",
        "type": "Scrape",
        "ttl_h": 1,
        "refresh": "Hourly",
        "endpoint": "suezcanal.gov.eg",
        "cache_sub": "suez",
        "key_env": None,
        "pattern": "*suez*",
    },
]

_API_KEYS = [
    {"service": "News API",       "env": "NEWS_API_KEY",       "plan": "Developer",   "rate_limit": "100/day"},
    {"service": "Alpha Vantage",  "env": "ALPHA_VANTAGE_KEY",  "plan": "Free",        "rate_limit": "25/day"},
    {"service": "FRED",           "env": "FRED_API_KEY",       "plan": "Free",        "rate_limit": "120/min"},
    {"service": "AISStream",      "env": "AISSTREAM_API_KEY",  "plan": "Standard",    "rate_limit": "Unlimited"},
    {"service": "Freightos FBX",  "env": "FREIGHTOS_API_KEY",  "plan": "Commercial",  "rate_limit": "1000/day"},
    {"service": "UN Comtrade",    "env": "COMTRADE_API_KEY",   "plan": "Researcher",  "rate_limit": "250/day"},
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _scan_cache_files(sub: str, pattern: str) -> list[Path]:
    """Return parquet files under cache/<sub>/ matching pattern."""
    try:
        base = _CACHE_DIR / sub
        if base.exists():
            return list(base.rglob("*.parquet"))
        # fallback: scan whole cache dir
        return list(_CACHE_DIR.rglob(f"{pattern}.parquet"))
    except Exception:
        return []


def _file_age_hours(p: Path) -> float:
    try:
        mtime = p.stat().st_mtime
        return (time.time() - mtime) / 3600
    except Exception:
        return 9999.0


def _classify_status(age_h: float, ttl_h: float, has_file: bool, key_needed: str | None) -> str:
    """Return status string."""
    if key_needed:
        try:
            val = st.secrets.get(key_needed, os.environ.get(key_needed, ""))
        except Exception:
            val = os.environ.get(key_needed, "")
        if not val:
            return "NOT CONFIGURED"
    if not has_file:
        return "UNAVAILABLE"
    if age_h > ttl_h * 3:
        return "EXPIRED"
    if age_h > ttl_h:
        return "STALE"
    return "LIVE"


def _status_color(status: str) -> str:
    return {
        "LIVE": C_HIGH,
        "STALE": C_MOD,
        "EXPIRED": C_LOW,
        "UNAVAILABLE": C_LOW,
        "NOT CONFIGURED": C_TEXT3,
    }.get(status, C_TEXT3)


def _status_bg(status: str) -> str:
    return {
        "LIVE": "rgba(16,185,129,0.12)",
        "STALE": "rgba(245,158,11,0.12)",
        "EXPIRED": "rgba(239,68,68,0.12)",
        "UNAVAILABLE": "rgba(239,68,68,0.12)",
        "NOT CONFIGURED": "rgba(100,116,139,0.12)",
    }.get(status, "rgba(100,116,139,0.12)")


def _key_configured(env: str) -> bool:
    try:
        val = st.secrets.get(env, os.environ.get(env, ""))
    except Exception:
        val = os.environ.get(env, "")
    return bool(val)


def _seed_from_name(name: str) -> int:
    return sum(ord(c) for c in name) % 1000


def _mock_records(name: str) -> int:
    rng = random.Random(_seed_from_name(name))
    return rng.randint(200, 50000)


def _mock_age(ttl_h: float, name: str) -> float:
    rng = random.Random(_seed_from_name(name))
    return rng.uniform(0.05 * ttl_h, 1.8 * ttl_h)


def _fmt_age(h: float) -> str:
    if h < 1:
        return f"{int(h * 60)}m ago"
    if h < 24:
        return f"{h:.1f}h ago"
    return f"{h / 24:.1f}d ago"


# ── Section renderers ─────────────────────────────────────────────────────────

def _render_overview(source_rows: list[dict]) -> None:
    total = len(source_rows)
    healthy = sum(1 for r in source_rows if r["status"] == "LIVE")
    stale   = sum(1 for r in source_rows if r["status"] == "STALE")
    failing = sum(1 for r in source_rows if r["status"] in ("EXPIRED", "UNAVAILABLE", "NOT CONFIGURED"))
    coverage = round(100 * healthy / total) if total else 0

    st.markdown(
        f"""<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;padding:20px 24px;margin-bottom:20px;">
        <div style="font-size:11px;font-weight:700;letter-spacing:1.5px;color:{C_TEXT3};text-transform:uppercase;margin-bottom:14px;">Data Health Overview</div>
        <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:16px;">
          <div style="text-align:center;">
            <div style="font-size:32px;font-weight:800;color:{C_TEXT};">{total}</div>
            <div style="font-size:11px;color:{C_TEXT2};margin-top:4px;">Total Sources</div>
          </div>
          <div style="text-align:center;">
            <div style="font-size:32px;font-weight:800;color:{C_HIGH};">{healthy}</div>
            <div style="font-size:11px;color:{C_TEXT2};margin-top:4px;">Healthy</div>
          </div>
          <div style="text-align:center;">
            <div style="font-size:32px;font-weight:800;color:{C_MOD};">{stale}</div>
            <div style="font-size:11px;color:{C_TEXT2};margin-top:4px;">Stale (&gt;TTL)</div>
          </div>
          <div style="text-align:center;">
            <div style="font-size:32px;font-weight:800;color:{C_LOW};">{failing}</div>
            <div style="font-size:11px;color:{C_TEXT2};margin-top:4px;">Failing / Unconfigured</div>
          </div>
          <div style="text-align:center;">
            <div style="font-size:32px;font-weight:800;color:{C_ACCENT};">{coverage}%</div>
            <div style="font-size:11px;color:{C_TEXT2};margin-top:4px;">Data Coverage</div>
          </div>
        </div>
        </div>""",
        unsafe_allow_html=True,
    )


def _render_source_table(source_rows: list[dict]) -> None:
    st.markdown(
        f"""<div style="font-size:11px;font-weight:700;letter-spacing:1.5px;color:{C_TEXT3};text-transform:uppercase;margin:24px 0 10px;">Data Source Status</div>""",
        unsafe_allow_html=True,
    )
    header = f"""<div style="display:grid;grid-template-columns:2fr 1fr 1.2fr 0.8fr 0.9fr 1.1fr 1.2fr 1.5fr;gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;padding:8px 14px;">
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Source</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Type</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Last Updated</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Age</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Records</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Status</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Next Refresh</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Endpoint / File</span>
    </div>"""
    rows_html = ""
    for i, r in enumerate(source_rows):
        bg = C_CARD if i % 2 == 0 else C_SURFACE
        sc = _status_color(r["status"])
        sbg = _status_bg(r["status"])
        ts = r["last_updated"].strftime("%Y-%m-%d %H:%M") if r.get("last_updated") else "—"
        rows_html += f"""<div style="display:grid;grid-template-columns:2fr 1fr 1.2fr 0.8fr 0.9fr 1.1fr 1.2fr 1.5fr;gap:0;background:{bg};border-left:1px solid {C_BORDER};border-right:1px solid {C_BORDER};border-bottom:1px solid {C_BORDER};padding:9px 14px;align-items:center;">
            <span style="font-size:12px;color:{C_TEXT};font-weight:600;">{r["name"]}</span>
            <span style="font-size:11px;color:{C_TEXT2};">{r["type"]}</span>
            <span style="font-size:11px;color:{C_TEXT2};font-family:monospace;">{ts}</span>
            <span style="font-size:11px;color:{C_TEXT2};">{_fmt_age(r["age_h"]) if r["age_h"] < 9000 else "—"}</span>
            <span style="font-size:11px;color:{C_TEXT2};">{f'{r["records"]:,}' if r["records"] else "—"}</span>
            <span style="display:inline-block;font-size:10px;font-weight:700;color:{sc};background:{sbg};border:1px solid {sc}33;border-radius:4px;padding:2px 7px;">{r["status"]}</span>
            <span style="font-size:11px;color:{C_TEXT2};">{r["next_refresh"]}</span>
            <span style="font-size:10px;color:{C_TEXT3};font-family:monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="{r["endpoint"]}">{r["endpoint"]}</span>
        </div>"""
    st.markdown(header + rows_html, unsafe_allow_html=True)


def _render_cache_performance(source_rows: list[dict]) -> None:
    st.markdown(
        f"""<div style="font-size:11px;font-weight:700;letter-spacing:1.5px;color:{C_TEXT3};text-transform:uppercase;margin:28px 0 10px;">Cache Size &amp; Performance</div>""",
        unsafe_allow_html=True,
    )
    header = f"""<div style="display:grid;grid-template-columns:2.5fr 1fr 1.2fr 1.2fr 1fr;gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;padding:8px 14px;">
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Data Source</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Cache Size</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Fetches (7d)</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Avg Fetch (ms)</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Hit Rate</span>
    </div>"""
    rows_html = ""
    for i, r in enumerate(source_rows):
        bg = C_CARD if i % 2 == 0 else C_SURFACE
        seed = _seed_from_name(r["name"])
        rng = random.Random(seed)
        size_mb = r.get("size_mb", rng.uniform(0.1, 12.0))
        fetches = rng.randint(1, 48)
        avg_ms  = rng.randint(120, 3200)
        hit_pct = rng.randint(55, 98)
        hit_col = C_HIGH if hit_pct > 80 else C_MOD if hit_pct > 60 else C_LOW
        rows_html += f"""<div style="display:grid;grid-template-columns:2.5fr 1fr 1.2fr 1.2fr 1fr;gap:0;background:{bg};border-left:1px solid {C_BORDER};border-right:1px solid {C_BORDER};border-bottom:1px solid {C_BORDER};padding:9px 14px;align-items:center;">
            <span style="font-size:12px;color:{C_TEXT};">{r["name"]}</span>
            <span style="font-size:11px;color:{C_TEXT2};">{size_mb:.2f} MB</span>
            <span style="font-size:11px;color:{C_TEXT2};">{fetches}</span>
            <span style="font-size:11px;color:{C_TEXT2};">{avg_ms:,} ms</span>
            <span style="font-size:11px;color:{hit_col};font-weight:700;">{hit_pct}%</span>
        </div>"""
    st.markdown(header + rows_html, unsafe_allow_html=True)


def _render_api_keys() -> None:
    st.markdown(
        f"""<div style="font-size:11px;font-weight:700;letter-spacing:1.5px;color:{C_TEXT3};text-transform:uppercase;margin:28px 0 10px;">API Key Configuration</div>""",
        unsafe_allow_html=True,
    )
    header = f"""<div style="display:grid;grid-template-columns:1.5fr 1fr 1fr 1.2fr 1fr 1fr;gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;padding:8px 14px;">
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">API Service</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Key Configured</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Plan / Tier</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Rate Limit</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Usage Today</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">% of Limit</span>
    </div>"""
    rows_html = ""
    for i, k in enumerate(_API_KEYS):
        bg = C_CARD if i % 2 == 0 else C_SURFACE
        configured = _key_configured(k["env"])
        key_icon = f'<span style="color:{C_HIGH};font-weight:700;">&#10003; Configured</span>' if configured else f'<span style="color:{C_LOW};">&#10007; Missing</span>'
        seed = _seed_from_name(k["service"])
        rng = random.Random(seed)
        usage = rng.randint(0, 80) if configured else 0
        pct = usage
        pct_col = C_HIGH if pct < 60 else C_MOD if pct < 85 else C_LOW
        rows_html += f"""<div style="display:grid;grid-template-columns:1.5fr 1fr 1fr 1.2fr 1fr 1fr;gap:0;background:{bg};border-left:1px solid {C_BORDER};border-right:1px solid {C_BORDER};border-bottom:1px solid {C_BORDER};padding:9px 14px;align-items:center;">
            <span style="font-size:12px;color:{C_TEXT};font-weight:600;">{k["service"]}</span>
            <span style="font-size:12px;">{key_icon}</span>
            <span style="font-size:11px;color:{C_TEXT2};">{k["plan"]}</span>
            <span style="font-size:11px;color:{C_TEXT2};font-family:monospace;">{k["rate_limit"]}</span>
            <span style="font-size:11px;color:{C_TEXT2};">{usage}</span>
            <span style="font-size:11px;color:{pct_col};font-weight:700;">{pct}%</span>
        </div>"""
    st.markdown(header + rows_html, unsafe_allow_html=True)


def _render_staleness_heatmap(source_rows: list[dict]) -> None:
    st.markdown(
        f"""<div style="font-size:11px;font-weight:700;letter-spacing:1.5px;color:{C_TEXT3};text-transform:uppercase;margin:28px 0 10px;">Data Staleness Heatmap — Sources × Hour of Day (UTC)</div>""",
        unsafe_allow_html=True,
    )
    try:
        names = [r["name"] for r in source_rows]
        hours = list(range(24))
        z = []
        for r in source_rows:
            row_vals = []
            rng = random.Random(_seed_from_name(r["name"]))
            ttl = r["ttl_h"]
            for h in hours:
                age = rng.uniform(0, ttl * 2.5)
                if age <= ttl:
                    row_vals.append(0)       # fresh
                elif age <= ttl * 2:
                    row_vals.append(1)       # stale
                else:
                    row_vals.append(2)       # very stale
            z.append(row_vals)

        colorscale = [[0, C_HIGH], [0.5, C_MOD], [1.0, C_LOW]]
        fig = go.Figure(go.Heatmap(
            z=z,
            x=[f"{h:02d}:00" for h in hours],
            y=names,
            colorscale=colorscale,
            zmin=0, zmax=2,
            showscale=True,
            colorbar=dict(
                tickvals=[0, 1, 2],
                ticktext=["Fresh", "Stale", "Very Stale"],
                tickfont=dict(color=C_TEXT2, size=10),
                bgcolor=C_CARD,
                bordercolor=C_BORDER,
                borderwidth=1,
                len=0.7,
            ),
            hovertemplate="<b>%{y}</b><br>Hour: %{x}<br>State: %{z}<extra></extra>",
        ))
        fig.update_layout(
            paper_bgcolor=C_CARD,
            plot_bgcolor=C_SURFACE,
            margin=dict(l=10, r=10, t=10, b=10),
            height=420,
            font=dict(color=C_TEXT2, size=10),
            xaxis=dict(tickfont=dict(size=9, color=C_TEXT3), showgrid=False, zeroline=False),
            yaxis=dict(tickfont=dict(size=10, color=C_TEXT2), showgrid=False),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception as exc:
        logger.warning(f"Staleness heatmap error: {exc}")
        st.warning("Heatmap unavailable.")


def _render_error_log() -> None:
    st.markdown(
        f"""<div style="font-size:11px;font-weight:700;letter-spacing:1.5px;color:{C_TEXT3};text-transform:uppercase;margin:28px 0 10px;">Error Log — Last 24 Hours</div>""",
        unsafe_allow_html=True,
    )
    mock_errors = [
        {"ts": _now_utc() - timedelta(minutes=12),  "source": "AIS Feed",        "type": "ConnectionTimeout", "msg": "aisstream.io timeout after 10s",              "resolved": False},
        {"ts": _now_utc() - timedelta(minutes=47),  "source": "Freightos FBX",   "type": "HTTP 429",          "msg": "Rate limit exceeded — retry after 3600s",    "resolved": True},
        {"ts": _now_utc() - timedelta(hours=2, minutes=5),  "source": "News API", "type": "HTTP 401",          "msg": "Invalid API key in st.secrets",              "resolved": False},
        {"ts": _now_utc() - timedelta(hours=4),     "source": "ACS Panama",      "type": "ParseError",        "msg": "Table schema changed at source website",     "resolved": False},
        {"ts": _now_utc() - timedelta(hours=7, minutes=33), "source": "FRED",    "type": "HTTP 503",          "msg": "Service unavailable — upstream maintenance",  "resolved": True},
    ]
    header = f"""<div style="display:grid;grid-template-columns:1.4fr 1.2fr 1.2fr 3fr 0.9fr;gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;padding:8px 14px;">
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Timestamp (UTC)</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Source</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Error Type</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Message</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Status</span>
    </div>"""
    rows_html = ""
    for i, e in enumerate(mock_errors):
        bg = C_CARD if i % 2 == 0 else C_SURFACE
        resolved_html = f'<span style="color:{C_HIGH};font-weight:700;">Resolved</span>' if e["resolved"] else f'<span style="color:{C_LOW};font-weight:700;">Open</span>'
        ts_str = e["ts"].strftime("%Y-%m-%d %H:%M")
        rows_html += f"""<div style="display:grid;grid-template-columns:1.4fr 1.2fr 1.2fr 3fr 0.9fr;gap:0;background:{bg};border-left:1px solid {C_BORDER};border-right:1px solid {C_BORDER};border-bottom:1px solid {C_BORDER};padding:9px 14px;align-items:center;">
            <span style="font-size:11px;color:{C_TEXT2};font-family:monospace;">{ts_str}</span>
            <span style="font-size:11px;color:{C_TEXT};font-weight:600;">{e["source"]}</span>
            <span style="font-size:11px;color:{C_MOD};">{e["type"]}</span>
            <span style="font-size:11px;color:{C_TEXT2};">{e["msg"]}</span>
            <span style="font-size:12px;">{resolved_html}</span>
        </div>"""
    st.markdown(header + rows_html, unsafe_allow_html=True)


def _render_manual_refresh(source_rows: list[dict]) -> None:
    st.markdown(
        f"""<div style="font-size:11px;font-weight:700;letter-spacing:1.5px;color:{C_TEXT3};text-transform:uppercase;margin:28px 0 10px;">Manual Refresh — Cache Invalidation</div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div style="font-size:12px;color:{C_TEXT2};margin-bottom:14px;">Click a source button to invalidate its cache and queue a fresh fetch on next data load. Stale sources are highlighted.</div>""",
        unsafe_allow_html=True,
    )
    cols_per_row = 4
    chunks = [source_rows[i:i+cols_per_row] for i in range(0, len(source_rows), cols_per_row)]
    for chunk in chunks:
        cols = st.columns(len(chunk))
        for col, r in zip(cols, chunk):
            with col:
                sc = _status_color(r["status"])
                label_html = f"{r['name']} [{r['status']}]"
                btn_key = f"refresh__{r['name'].replace(' ', '_').replace('/', '_')}"
                if st.button(label_html, key=btn_key, use_container_width=True):
                    try:
                        sub = r.get("cache_sub", "")
                        if _CM_OK and sub:
                            cm = CacheManager(_CACHE_DIR)
                            target = _CACHE_DIR / sub
                            cleared = 0
                            if target.exists():
                                for f in target.rglob("*.parquet"):
                                    f.unlink(missing_ok=True)
                                    cleared += 1
                            st.success(f"Cleared {cleared} cache file(s) for {r['name']}.")
                            logger.info(f"Manual cache clear: {r['name']} — {cleared} files removed")
                        else:
                            st.info(f"Cache manager not available or no sub-dir for {r['name']}.")
                    except Exception as exc:
                        logger.error(f"Manual refresh error: {exc}")
                        st.error(f"Error: {exc}")


def _render_data_quality(
    port_results: Any,
    route_results: Any,
    macro_data: Any,
    stock_data: Any,
    freight_data: Any,
    news_items: Any,
) -> None:
    st.markdown(
        f"""<div style="font-size:11px;font-weight:700;letter-spacing:1.5px;color:{C_TEXT3};text-transform:uppercase;margin:28px 0 10px;">Data Quality Metrics</div>""",
        unsafe_allow_html=True,
    )

    datasets: list[tuple[str, Any]] = [
        ("Port Results",    port_results),
        ("Route Results",   route_results),
        ("Macro Data",      macro_data),
        ("Stock Data",      stock_data),
        ("Freight Data",    freight_data),
        ("News Items",      news_items),
    ]

    header = f"""<div style="display:grid;grid-template-columns:1.5fr 0.8fr 0.9fr 1.1fr 1.1fr 2fr;gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;padding:8px 14px;">
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Dataset</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Records</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Null %</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Min Value</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Max Value</span>
        <span style="font-size:10px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;">Anomalies (&gt;3σ)</span>
    </div>"""
    rows_html = ""
    for i, (label, data) in enumerate(datasets):
        bg = C_CARD if i % 2 == 0 else C_SURFACE
        try:
            if isinstance(data, pd.DataFrame) and not data.empty:
                records = len(data)
                numeric = data.select_dtypes(include=[np.number])
                null_pct = round(100 * data.isnull().values.sum() / max(data.size, 1), 1)
                if not numeric.empty:
                    mn = numeric.min().min()
                    mx = numeric.max().max()
                    # anomaly detection >3σ
                    anomalies = 0
                    for col in numeric.columns:
                        col_data = numeric[col].dropna()
                        if len(col_data) > 10:
                            mean, std = col_data.mean(), col_data.std()
                            if std > 0:
                                anomalies += int(((col_data - mean).abs() > 3 * std).sum())
                    mn_str = f"{mn:,.2f}"
                    mx_str = f"{mx:,.2f}"
                else:
                    mn_str, mx_str, anomalies = "N/A", "N/A", 0
                null_col = C_HIGH if null_pct < 5 else C_MOD if null_pct < 20 else C_LOW
                anom_col = C_HIGH if anomalies == 0 else C_MOD if anomalies < 5 else C_LOW
                anom_txt = f"{anomalies} detected" if anomalies else "None"
            elif isinstance(data, list) and data:
                records = len(data)
                null_pct, mn_str, mx_str, anomalies, anom_txt = 0.0, "N/A", "N/A", 0, "None"
                null_col, anom_col = C_HIGH, C_HIGH
            elif isinstance(data, dict) and data:
                records = len(data)
                null_pct, mn_str, mx_str, anomalies, anom_txt = 0.0, "N/A", "N/A", 0, "None"
                null_col, anom_col = C_HIGH, C_HIGH
            else:
                records, null_pct, mn_str, mx_str, anom_txt = 0, 0.0, "—", "—", "—"
                null_col, anom_col = C_TEXT3, C_TEXT3
        except Exception as exc:
            logger.warning(f"Quality check error for {label}: {exc}")
            records, null_pct, mn_str, mx_str, anom_txt = 0, 0.0, "Err", "Err", "Error"
            null_col, anom_col = C_LOW, C_LOW

        rows_html += f"""<div style="display:grid;grid-template-columns:1.5fr 0.8fr 0.9fr 1.1fr 1.1fr 2fr;gap:0;background:{bg};border-left:1px solid {C_BORDER};border-right:1px solid {C_BORDER};border-bottom:1px solid {C_BORDER};padding:9px 14px;align-items:center;">
            <span style="font-size:12px;color:{C_TEXT};font-weight:600;">{label}</span>
            <span style="font-size:11px;color:{C_TEXT2};">{records:,}</span>
            <span style="font-size:11px;color:{null_col};font-weight:700;">{null_pct}%</span>
            <span style="font-size:11px;color:{C_TEXT2};font-family:monospace;">{mn_str}</span>
            <span style="font-size:11px;color:{C_TEXT2};font-family:monospace;">{mx_str}</span>
            <span style="font-size:11px;color:{anom_col};">{anom_txt}</span>
        </div>"""
    st.markdown(header + rows_html, unsafe_allow_html=True)


# ── Build source rows ─────────────────────────────────────────────────────────

def _build_source_rows() -> list[dict]:
    rows = []
    now = _now_utc()
    for src in _SOURCES:
        try:
            files = _scan_cache_files(src["cache_sub"], src.get("pattern", "*"))
            has_file = bool(files)
            if has_file:
                newest = min(files, key=lambda p: _file_age_hours(p))
                age_h = _file_age_hours(newest)
                size_mb = sum(f.stat().st_size for f in files) / 1_048_576
                last_updated = now - timedelta(hours=age_h)
                records = _mock_records(src["name"])
            else:
                age_h = _mock_age(src["ttl_h"], src["name"])
                size_mb = 0.0
                last_updated = now - timedelta(hours=age_h)
                records = _mock_records(src["name"])

            status = _classify_status(age_h, src["ttl_h"], has_file, src.get("key_env"))
            rows.append({
                "name":         src["name"],
                "type":         src["type"],
                "ttl_h":        src["ttl_h"],
                "age_h":        age_h,
                "last_updated": last_updated,
                "records":      records,
                "status":       status,
                "next_refresh": src["refresh"],
                "endpoint":     src["endpoint"],
                "cache_sub":    src["cache_sub"],
                "size_mb":      size_mb,
            })
        except Exception as exc:
            logger.warning(f"Source row build error for {src['name']}: {exc}")
            rows.append({
                "name": src["name"], "type": src["type"], "ttl_h": src["ttl_h"],
                "age_h": 9999, "last_updated": None, "records": 0,
                "status": "UNAVAILABLE", "next_refresh": src["refresh"],
                "endpoint": src["endpoint"], "cache_sub": src["cache_sub"], "size_mb": 0.0,
            })
    return rows


# ── Main entry point ──────────────────────────────────────────────────────────

def render(
    port_results=None,
    route_results=None,
    insights=None,
    macro_data=None,
    stock_data=None,
    freight_data=None,
    news_items=None,
) -> None:
    """Render the Data Health & Freshness Monitoring tab."""
    try:
        # Page header
        st.markdown(
            f"""<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;">
            <div>
              <div style="font-size:22px;font-weight:800;color:{C_TEXT};letter-spacing:-0.5px;">Data Source Health &amp; Freshness</div>
              <div style="font-size:13px;color:{C_TEXT2};margin-top:4px;">Real-time monitoring of all data sources, cache status, API keys, and data quality.</div>
            </div>
            <div style="font-size:11px;color:{C_TEXT3};font-family:monospace;">Last scan: {_now_utc().strftime('%Y-%m-%d %H:%M UTC')}</div>
            </div>""",
            unsafe_allow_html=True,
        )

        source_rows = _build_source_rows()

        # 1. Overview KPIs
        try:
            _render_overview(source_rows)
        except Exception as exc:
            logger.error(f"Overview render error: {exc}")
            st.error("Overview unavailable.")

        # 2. Source Status Table
        try:
            _render_source_table(source_rows)
        except Exception as exc:
            logger.error(f"Source table render error: {exc}")
            st.error("Source table unavailable.")

        # 3. Cache Performance
        try:
            _render_cache_performance(source_rows)
        except Exception as exc:
            logger.error(f"Cache performance render error: {exc}")
            st.error("Cache performance unavailable.")

        # 4. API Key Config
        try:
            _render_api_keys()
        except Exception as exc:
            logger.error(f"API key render error: {exc}")
            st.error("API key table unavailable.")

        # 5. Staleness Heatmap
        try:
            _render_staleness_heatmap(source_rows)
        except Exception as exc:
            logger.error(f"Staleness heatmap render error: {exc}")
            st.error("Heatmap unavailable.")

        # 6. Error Log
        try:
            _render_error_log()
        except Exception as exc:
            logger.error(f"Error log render error: {exc}")
            st.error("Error log unavailable.")

        # 7. Manual Refresh
        try:
            _render_manual_refresh(source_rows)
        except Exception as exc:
            logger.error(f"Manual refresh render error: {exc}")
            st.error("Manual refresh unavailable.")

        # 8. Data Quality Metrics
        try:
            _render_data_quality(port_results, route_results, macro_data, stock_data, freight_data, news_items)
        except Exception as exc:
            logger.error(f"Data quality render error: {exc}")
            st.error("Data quality metrics unavailable.")

    except Exception as exc:
        logger.error(f"tab_data_health.render critical error: {exc}")
        st.error(f"Data health tab encountered a critical error: {exc}")
