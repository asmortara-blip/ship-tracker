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

import json
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

from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    alert_banner,
    apply_dark_layout,
    badge,
    live_data_badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)
from data.quality import DataSource

try:
    from data.cache_manager import CacheManager
    _CM_OK = True
except Exception:
    _CM_OK = False

_CACHE_DIR = Path(__file__).parent.parent / "cache"

# ── Status maps ───────────────────────────────────────────────────────────────
# Keep domain-specific status → hex color mapping local to this tab.
# Pass hex values directly to badge(); named CSS colors are not accepted.
_STATUS_HEX: dict[str, str] = {
    "LIVE":           C_HIGH,
    "STALE":          C_MOD,
    "EXPIRED":        C_LOW,
    "UNAVAILABLE":    C_LOW,
    "NOT CONFIGURED": C_TEXT3,
}

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


# ── Cell formatters ──────────────────────────────────────────────────────────
def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};font-size:0.82rem;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};font-size:0.82rem;">{value}</span>'
    )


def _status_pill(status: str) -> str:
    return badge(status, _STATUS_HEX.get(status, C_TEXT3))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _scan_cache_files(sub: str, pattern: str) -> list[Path]:
    """Return parquet files under cache/<sub>/ matching pattern."""
    try:
        base = _CACHE_DIR / sub
        if base.exists():
            return list(base.rglob("*.parquet"))
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

    metric_card_row([
        {"label": "TOTAL SOURCES",    "value": f"{total}",      "accent": C_ACCENT},
        {"label": "HEALTHY",          "value": f"{healthy}",    "accent": C_HIGH,   "sublabel": "Fresh · within TTL"},
        {"label": "STALE (>TTL)",     "value": f"{stale}",      "accent": C_MOD,    "sublabel": "Past refresh window"},
        {"label": "FAILING / UNCFG",  "value": f"{failing}",    "accent": C_LOW,    "sublabel": "Expired / unavailable"},
        {"label": "DATA COVERAGE",    "value": f"{coverage}%",  "accent": C_ACCENT, "sublabel": "Healthy / total"},
    ], columns=5)
    # Status is derived from disk scan + seeded fallback for sources with no cache file.
    st.markdown(
        source_footer([DataSource.live("Cache Scan", notes="Disk + env"), DataSource.demo("Age / Records (fallback synthetic)")]),
        unsafe_allow_html=True,
    )


def _render_source_table(source_rows: list[dict]) -> None:
    section_header("Data Source Status", "Live catalog of every feed, its cache state, and refresh schedule.")
    headers = ["Source", "Type", "Last Updated", "Age", "Records", "Status", "Next Refresh", "Endpoint"]
    rows: list[list[str]] = []
    for r in source_rows:
        ts = r["last_updated"].strftime("%Y-%m-%d %H:%M") if r.get("last_updated") else "—"
        age = _fmt_age(r["age_h"]) if r["age_h"] < 9000 else "—"
        recs = f"{r['records']:,}" if r["records"] else "—"
        rows.append([
            _sans(r["name"], weight=600),
            _sans(r["type"], color=C_TEXT2),
            _mono(ts, color=C_TEXT2),
            _sans(age, color=C_TEXT2),
            _mono(recs, color=C_TEXT2),
            _status_pill(r["status"]),
            _sans(r["next_refresh"], color=C_TEXT2),
            _mono(r["endpoint"], color=C_TEXT3),
        ])
    wsj_market_table(headers, rows)
    # Record counts are synthetic fallback when no cache file is present.
    st.markdown(
        source_footer([DataSource.live("Cache Scan", notes="Disk + env"), DataSource.demo("Records (synthetic fallback)")]),
        unsafe_allow_html=True,
    )


def _render_sla_dashboard(source_rows: list[dict]) -> None:
    """SLA compliance view — actual cache age vs each source's TTL target.

    For each source we compute:
      margin_h         = ttl_h - age_h         (positive = within SLA)
      ratio            = age_h / ttl_h         (>1.0 = breach)
      breach_severity  = "OK" / "WARNING" / "BREACH"

    Tier thresholds match the existing status classifier:
      ratio <= 0.80           → OK
      0.80 < ratio <= 1.00    → WARNING (approaching breach)
      ratio > 1.00            → BREACH (past SLA target)
    """
    section_header(
        "SLA Compliance",
        "Each feed's age relative to its declared TTL (Time To Live). "
        "A ratio > 1.0 means the source is past its freshness SLA; > 1.5 is critical.",
    )

    # Per-source ratio + classification.
    sla_rows: list[dict] = []
    for row in source_rows:
        ttl = float(row.get("ttl_h", 0.0) or 0.0)
        age = float(row.get("age_h", 0.0) or 0.0)
        if ttl <= 0:
            ratio = 0.0
            tier = "n/a"
        else:
            ratio = age / ttl
            if ratio <= 0.80:
                tier = "OK"
            elif ratio <= 1.00:
                tier = "WARNING"
            elif ratio <= 1.50:
                tier = "BREACH"
            else:
                tier = "CRITICAL"
        sla_rows.append({
            **row,
            "margin_h": ttl - age,
            "ratio": ratio,
            "tier": tier,
        })

    # ── Compliance summary cards ──────────────────────────────────────────
    valid_rows = [r for r in sla_rows if r["tier"] != "n/a"]
    total = len(valid_rows)
    within = sum(1 for r in valid_rows if r["tier"] == "OK")
    warning = sum(1 for r in valid_rows if r["tier"] == "WARNING")
    breach = sum(1 for r in valid_rows if r["tier"] in ("BREACH", "CRITICAL"))
    compliance_pct = (within / total * 100) if total else 0.0
    avg_ratio = (sum(r["ratio"] for r in valid_rows) / total) if total else 0.0

    metric_card_row(
        [
            {"label": "Compliance Rate",
             "value": f"{compliance_pct:.0f}%",
             "accent": (
                 C_HIGH if compliance_pct >= 80 else
                 (C_MOD if compliance_pct >= 60 else C_LOW)
             ),
             "sublabel": f"{within} of {total} feeds within SLA"},
            {"label": "Warnings",
             "value": f"{warning}",
             "accent": C_MOD if warning > 0 else C_TEXT3,
             "sublabel": "Within 80-100% of TTL"},
            {"label": "Breaches",
             "value": f"{breach}",
             "accent": C_LOW if breach > 0 else C_HIGH,
             "sublabel": "Past SLA target"},
            {"label": "Avg Age/TTL Ratio",
             "value": f"{avg_ratio:.2f}",
             "accent": (
                 C_HIGH if avg_ratio < 0.7 else
                 (C_MOD if avg_ratio < 1.0 else C_LOW)
             ),
             "sublabel": "Across all feeds"},
        ],
        columns=4,
    )

    # ── Per-source ratio bar chart ────────────────────────────────────────
    if valid_rows:
        ranked = sorted(valid_rows, key=lambda r: r["ratio"], reverse=True)[:20]
        names = [r["name"] for r in ranked]
        ratios = [r["ratio"] for r in ranked]
        colors = [
            C_LOW if r["tier"] in ("BREACH", "CRITICAL") else
            (C_MOD if r["tier"] == "WARNING" else C_HIGH)
            for r in ranked
        ]
        fig = go.Figure(go.Bar(
            x=ratios, y=names, orientation="h",
            marker_color=colors,
            text=[f"{r:.2f}" for r in ratios],
            textposition="outside",
            hovertemplate=(
                "<b>%{y}</b><br>Age/TTL = %{x:.2f}<extra></extra>"
            ),
        ))
        # SLA threshold reference lines (0.8 = warn floor; 1.0 = breach floor).
        fig.add_shape(
            type="line", line=dict(color=C_MOD, dash="dot", width=1),
            x0=0.80, x1=0.80, y0=-0.5, y1=len(names) - 0.5,
        )
        fig.add_shape(
            type="line", line=dict(color=C_LOW, dash="dash", width=1.2),
            x0=1.00, x1=1.00, y0=-0.5, y1=len(names) - 0.5,
        )
        apply_dark_layout(
            fig, title="Age / TTL Ratio per Source (red dashed = SLA breach line)",
            height=max(280, 22 * len(names) + 80),
            margin=dict(l=12, r=80, t=46, b=30),
            xaxis=dict(
                title=dict(text="age / TTL (ratio)", font=dict(color=C_TEXT2, size=11)),
                range=[0, max(1.6, max(ratios) * 1.1)],
            ),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # ── Breach list ───────────────────────────────────────────────────────
    breaches = [r for r in sla_rows if r["tier"] in ("BREACH", "CRITICAL")]
    if breaches:
        breaches.sort(key=lambda r: r["ratio"], reverse=True)
        st.markdown(
            f'<div style="font-size:0.72rem;text-transform:uppercase;'
            f'letter-spacing:0.10em;color:{C_LOW};font-weight:700;'
            f'margin:14px 0 6px 0">Active SLA Breaches</div>',
            unsafe_allow_html=True,
        )
        bheaders = ["Source", "Type", "TTL (h)", "Age (h)", "Ratio", "Margin (h)", "Severity"]
        brows = []
        for r in breaches:
            sev_color = C_LOW if r["tier"] == "CRITICAL" else C_MOD
            brows.append([
                _sans(r["name"], color=C_TEXT, weight=600),
                _sans(r["type"], color=C_TEXT2),
                _mono(f"{r['ttl_h']:.1f}", color=C_TEXT2),
                _mono(f"{r['age_h']:.1f}", color=C_LOW),
                _mono(f"{r['ratio']:.2f}×", color=C_LOW),
                _mono(f"{r['margin_h']:+.1f}", color=C_LOW),
                _sans(r["tier"], color=sev_color, weight=700),
            ])
        wsj_market_table(bheaders, brows)
    else:
        st.markdown(
            f'<div style="font-size:0.78rem;color:{C_HIGH};margin-top:14px">'
            f'✓ No active SLA breaches. All {total} feeds within their TTL.'
            f'</div>',
            unsafe_allow_html=True,
        )


def _render_llm_usage() -> None:
    """LLM cost-telemetry panel — last 7 days of Anthropic API spend.

    Consumes ``engine.llm_telemetry`` aggregations directly (no
    re-aggregation). Wrapped in a single try/except so a telemetry
    DB outage cannot break the rest of the Data Health tab. Imports
    are lazy/defensive — same pattern as ``_render_delivery_channels``
    in ``ui/tab_alerts.py``.
    """
    try:
        from engine.llm_telemetry import get_recent_calls, get_usage_summary
        from utils.helpers import format_usd

        section_header(
            "LLM Usage",
            "Rolling 7-day Anthropic API spend across all platform LLM "
            "callers (commentary, narration). Source: llm_calls table.",
        )

        summary = get_usage_summary(window_days=7)
        total_calls = int(summary.get("total_calls", 0))

        if total_calls == 0:
            st.info("No LLM usage data yet.")
            return

        total_tokens_out = int(summary.get("total_tokens_out", 0))
        total_cost = float(summary.get("total_cost_usd", 0.0))
        avg_cost = total_cost / total_calls if total_calls else 0.0

        metric_card_row(
            [
                {"label": "TOTAL CALLS (7D)",
                 "value": f"{total_calls:,}",
                 "accent": C_ACCENT,
                 "sublabel": "Rolling 7-day window"},
                {"label": "TOKENS OUT (7D)",
                 "value": f"{total_tokens_out:,}",
                 "accent": C_TEXT,
                 "sublabel": "Anthropic completion tokens"},
                {"label": "COST (7D)",
                 "value": format_usd(total_cost, compact=False),
                 "accent": C_HIGH if total_cost < 5.0 else (
                     C_MOD if total_cost < 25.0 else C_LOW
                 ),
                 "sublabel": "Est. via public list pricing"},
                {"label": "COST PER CALL",
                 "value": format_usd(avg_cost, compact=False),
                 "accent": C_TEXT,
                 "sublabel": "Average over 7-day window"},
            ],
            columns=4,
        )

        by_source = summary.get("by_source", {}) or {}
        by_model = summary.get("by_model", {}) or {}

        col_l, col_r = st.columns(2, gap="small")
        with col_l:
            st.markdown(
                f'<div style="font-size:0.72rem;text-transform:uppercase;'
                f'letter-spacing:0.10em;color:{C_TEXT3};font-weight:700;'
                f'margin:6px 0 6px 0">By Source</div>',
                unsafe_allow_html=True,
            )
            if by_source:
                headers = ["Source", "Calls", "Cost"]
                rows: list[list[str]] = []
                for src in sorted(by_source.keys()):
                    info = by_source[src]
                    rows.append([
                        _sans(src, weight=600),
                        _mono(f"{int(info.get('calls', 0)):,}", color=C_TEXT2),
                        _mono(format_usd(float(info.get("cost", 0.0)), compact=False), color=C_TEXT),
                    ])
                wsj_market_table(headers, rows)
            else:
                st.info("No source breakdown available.")

        with col_r:
            st.markdown(
                f'<div style="font-size:0.72rem;text-transform:uppercase;'
                f'letter-spacing:0.10em;color:{C_TEXT3};font-weight:700;'
                f'margin:6px 0 6px 0">By Model</div>',
                unsafe_allow_html=True,
            )
            if by_model:
                headers = ["Model", "Calls", "Cost"]
                rows = []
                for mdl in sorted(by_model.keys()):
                    info = by_model[mdl]
                    rows.append([
                        _sans(mdl, weight=600),
                        _mono(f"{int(info.get('calls', 0)):,}", color=C_TEXT2),
                        _mono(format_usd(float(info.get("cost", 0.0)), compact=False), color=C_TEXT),
                    ])
                wsj_market_table(headers, rows)
            else:
                st.info("No model breakdown available.")

        with st.expander("Recent calls (latest 20)", expanded=False):
            recent = get_recent_calls(limit=20)
            if recent:
                st.dataframe(pd.DataFrame(recent), use_container_width=True)
            else:
                st.info("No recent calls recorded.")

        st.markdown(
            live_data_badge(DataSource.live("llm_calls table", notes="engine.llm_telemetry")),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.exception(f"LLM usage panel render error: {exc}")
        st.error("LLM usage panel unavailable.")


def _render_tab_perf() -> None:
    """Tab render-performance telemetry — median/p95 durations + error counts.

    Consumes ``engine.perf_telemetry.get_perf_summary`` directly (no
    re-aggregation). Wrapped in a single try/except so a telemetry DB
    outage cannot break the rest of the Data Health tab. Imports are
    lazy/defensive — same pattern as ``_render_llm_usage`` above.
    """
    try:
        from engine.perf_telemetry import get_perf_summary

        section_header(
            "Tab Performance",
            "Per-tab render latency (median + p95) and exception counts. "
            "Source: tab_render_events table via engine.perf_telemetry.",
        )

        window = st.selectbox(
            "Window",
            options=[1, 6, 24, 72],
            index=2,  # 24h default
            format_func=lambda h: f"Last {h}h",
            key="perf_telemetry_window_h",
        )

        summary = get_perf_summary(window_hours=int(window))
        total_renders = int(summary.get("total_renders", 0))

        if total_renders == 0:
            st.info("No render data yet.")
            st.caption(
                "Only tabs wrapped with `track_render` produce telemetry. "
                "Currently: tab_overview."
            )
            return

        by_tab: dict[str, dict] = summary.get("by_tab", {}) or {}
        top_slow: list[dict] = summary.get("top_slow_tabs", []) or []
        success_rate = float(summary.get("success_rate", 0.0))

        # ── KPI strip ─────────────────────────────────────────────────
        n_tabs = len(by_tab)
        total_errors = sum(int(v.get("error_count", 0)) for v in by_tab.values())
        sr_pct = success_rate * 100
        sr_color = C_HIGH if sr_pct >= 99 else (C_MOD if sr_pct >= 95 else C_LOW)

        if top_slow:
            slowest_name = top_slow[0].get("tab_name", "—")
            slowest_ms = int(top_slow[0].get("median_ms", 0))
            slowest_label = (
                f"{slowest_ms / 1000:.2f}s median"
                if slowest_ms >= 1000
                else f"{slowest_ms:,} ms median"
            )
        else:
            slowest_name = "—"
            slowest_label = "no data"

        metric_card_row(
            [
                {"label": "TOTAL RENDERS",
                 "value": f"{total_renders:,}",
                 "accent": C_ACCENT,
                 "sublabel": f"Last {int(window)}h window"},
                {"label": "SUCCESS RATE",
                 "value": f"{sr_pct:.2f}%",
                 "accent": sr_color,
                 "sublabel": f"across {n_tabs} tab{'s' if n_tabs != 1 else ''}"},
                {"label": "SLOWEST TAB",
                 "value": slowest_name,
                 "accent": C_TEXT,
                 "sublabel": slowest_label},
                {"label": "TOTAL ERRORS",
                 "value": f"{total_errors:,}",
                 "accent": C_HIGH if total_errors == 0 else (
                     C_MOD if total_errors < 5 else C_LOW
                 ),
                 "sublabel": "exceptions caught"},
            ],
            columns=4,
        )

        # ── Side-by-side tables ───────────────────────────────────────
        def _fmt_ms(ms: int) -> str:
            ms = int(ms)
            return f"{ms / 1000:.2f}s" if ms >= 1000 else f"{ms:,} ms"

        col_l, col_r = st.columns(2, gap="small")
        with col_l:
            st.markdown(
                f'<div style="font-size:0.72rem;text-transform:uppercase;'
                f'letter-spacing:0.10em;color:{C_TEXT3};font-weight:700;'
                f'margin:6px 0 6px 0">Slowest Tabs (by median)</div>',
                unsafe_allow_html=True,
            )
            if top_slow:
                headers = ["Tab", "Count", "Median", "P95"]
                rows: list[list[str]] = []
                for entry in top_slow[:10]:
                    rows.append([
                        _sans(str(entry.get("tab_name", "—")), weight=600),
                        _mono(f"{int(entry.get('count', 0)):,}", color=C_TEXT2),
                        _mono(_fmt_ms(entry.get("median_ms", 0)), color=C_TEXT),
                        _mono(_fmt_ms(entry.get("p95_ms", 0)), color=C_TEXT2),
                    ])
                wsj_market_table(headers, rows)
            else:
                st.info("No slow-tab data.")

        with col_r:
            st.markdown(
                f'<div style="font-size:0.72rem;text-transform:uppercase;'
                f'letter-spacing:0.10em;color:{C_TEXT3};font-weight:700;'
                f'margin:6px 0 6px 0">Most Errors</div>',
                unsafe_allow_html=True,
            )
            err_entries = sorted(
                (
                    {"tab_name": name, **payload}
                    for name, payload in by_tab.items()
                    if int(payload.get("error_count", 0)) > 0
                ),
                key=lambda e: int(e.get("error_count", 0)),
                reverse=True,
            )[:10]
            if err_entries:
                headers = ["Tab", "Errors", "Total Renders", "Error Rate"]
                rows = []
                for entry in err_entries:
                    errs = int(entry.get("error_count", 0))
                    cnt = int(entry.get("count", 0))
                    rate = (errs / cnt * 100) if cnt else 0.0
                    rate_col = C_HIGH if rate < 1 else (C_MOD if rate < 5 else C_LOW)
                    rows.append([
                        _sans(str(entry.get("tab_name", "—")), weight=600),
                        _mono(f"{errs:,}", color=C_LOW, weight=700),
                        _mono(f"{cnt:,}", color=C_TEXT2),
                        _mono(f"{rate:.2f}%", color=rate_col, weight=700),
                    ])
                wsj_market_table(headers, rows)
            else:
                st.info("No errors in this window.")

        st.caption(
            "Only tabs wrapped with `track_render` produce telemetry. "
            "Currently: tab_overview."
        )
        st.markdown(
            live_data_badge(DataSource.live(
                "tab_render_events table",
                notes="engine.perf_telemetry",
            )),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.exception(f"Tab perf panel render error: {exc}")
        st.error("Tab performance panel unavailable.")


def _render_audit_log() -> None:
    """Audit log panel — recent privileged actions with filter controls.

    Consumes ``auth.audit.query_audit`` directly (no re-aggregation).
    Wrapped in a single try/except so a DB outage cannot break the rest
    of the Data Health tab. Imports are lazy/defensive — same pattern
    as ``_render_tab_perf`` above.
    """
    try:
        from auth.audit import query_audit
        from auth import users as auth_users

        section_divider("Audit Log")
        section_header(
            "Audit Log",
            "Recent privileged actions across alert / channel / report / "
            "auth touchpoints. Source: audit_events table via auth.audit.",
        )

        # ── Filter controls ─────────────────────────────────────────────
        # Window options map directly to hour deltas so the cutoff math
        # stays trivial and the labels stay human-readable.
        window_options: dict[str, int] = {
            "1h": 1, "6h": 6, "24h": 24, "7d": 168,
        }
        # The eleven distinct actions emitted by the audit hooks today.
        action_options: list[str] = [
            "all",
            "ack_alert", "ack_all_alerts",
            "save_rules", "reset_rules",
            "save_channel", "delete_channel",
            "delete_report", "make_public", "revoke_public",
            "signup", "login",
        ]
        limit_options: list[int] = [50, 100, 250, 500]

        c1, c2, c3 = st.columns(3, gap="small")
        with c1:
            window_label = st.selectbox(
                "Window",
                options=list(window_options.keys()),
                index=2,  # 24h default
                key="audit_log_window",
            )
        with c2:
            action_filter = st.selectbox(
                "Action",
                options=action_options,
                index=0,  # all
                key="audit_log_action",
            )
        with c3:
            limit = st.selectbox(
                "Limit",
                options=limit_options,
                index=1,  # 100 default
                key="audit_log_limit",
            )

        window_h = window_options[window_label]
        since = (
            datetime.now(timezone.utc) - timedelta(hours=window_h)
        ).isoformat()

        events = query_audit(
            action=(action_filter if action_filter != "all" else None),
            since=since,
            limit=int(limit),
        )

        if not events:
            st.info("No audit events in this window yet.")
            return

        # ── KPI strip ───────────────────────────────────────────────────
        total = len(events)
        distinct_users = len({e.user_id for e in events if e.user_id})
        action_counts: dict[str, int] = {}
        for e in events:
            action_counts[e.action] = action_counts.get(e.action, 0) + 1
        if action_counts:
            top_action, top_count = max(
                action_counts.items(), key=lambda kv: kv[1],
            )
            top_action_label = top_action
            top_action_sub = f"{top_count} event{'s' if top_count != 1 else ''}"
        else:
            top_action_label = "—"
            top_action_sub = "no data"

        metric_card_row(
            [
                {"label": "TOTAL EVENTS",
                 "value": f"{total:,}",
                 "accent": C_ACCENT,
                 "sublabel": f"Last {window_label} window"},
                {"label": "DISTINCT USERS",
                 "value": f"{distinct_users:,}",
                 "accent": C_TEXT,
                 "sublabel": "Unique non-empty user_ids"},
                {"label": "MOST COMMON ACTION",
                 "value": top_action_label,
                 "accent": C_TEXT,
                 "sublabel": top_action_sub},
            ],
            columns=3,
        )

        # ── Relative time helper ────────────────────────────────────────
        def _relative_time(iso_str: str) -> str:
            """Format ``iso_str`` as 'Xm ago' / 'Xh ago' for events <24h
            old, otherwise return the original ISO string. Falls back to
            the raw string on any parse failure so a malformed row still
            renders something."""
            try:
                ts = datetime.fromisoformat(iso_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                delta = datetime.now(timezone.utc) - ts
                secs = delta.total_seconds()
                if secs < 0:
                    return iso_str
                if secs < 60:
                    return f"{int(secs)}s ago"
                if secs < 3600:
                    return f"{int(secs // 60)}m ago"
                if secs < 86400:
                    return f"{int(secs // 3600)}h ago"
                return iso_str
            except Exception:
                return iso_str

        # ── Events table ────────────────────────────────────────────────
        # Resolve usernames once per unique user_id so the table render
        # does not fan out to N lookups when the same user dominates the
        # window. Each lookup is individually try/except'd — a deleted
        # account must not break the row render.
        username_cache: dict[str, str] = {}
        for e in events:
            if not e.user_id or e.user_id in username_cache:
                continue
            try:
                u = auth_users.get_user(e.user_id)
                if u is not None and u.username:
                    username_cache[e.user_id] = u.username
                else:
                    username_cache[e.user_id] = e.user_id[:8]
            except Exception:
                username_cache[e.user_id] = e.user_id[:8]

        headers = ["Time", "User", "Action", "Entity", "Detail"]
        rows: list[list[str]] = []
        for e in events:
            time_str = _relative_time(e.created_at)
            if not e.user_id:
                user_str = "system"
                user_col = C_TEXT3
            else:
                user_str = username_cache.get(e.user_id, e.user_id[:8])
                user_col = C_TEXT
            # Entity is ``entity_type:entity_id`` when both present,
            # just one of them when partial, em-dash when neither.
            if e.entity_type and e.entity_id:
                entity_str = f"{e.entity_type}:{e.entity_id}"
            elif e.entity_type:
                entity_str = e.entity_type
            elif e.entity_id:
                entity_str = e.entity_id
            else:
                entity_str = "—"
            # Compact JSON serialization, truncated at 80 chars for
            # table readability. The full payload is in the DB for a
            # security reviewer who needs it.
            try:
                detail_raw = (
                    json.dumps(e.detail_json, default=str)
                    if e.detail_json else ""
                )
            except Exception:
                detail_raw = str(e.detail_json) if e.detail_json else ""
            detail_str = (
                detail_raw[:77] + "..." if len(detail_raw) > 80 else detail_raw
            ) or "—"
            rows.append([
                _mono(time_str, color=C_TEXT2),
                _sans(user_str, color=user_col, weight=600),
                _sans(e.action, color=C_TEXT, weight=600),
                _mono(entity_str, color=C_TEXT2),
                _mono(detail_str, color=C_TEXT3),
            ])
        wsj_market_table(headers, rows)

        st.markdown(
            live_data_badge(DataSource.live(
                "audit_events table",
                notes="auth.audit.query_audit",
            )),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.exception(f"Audit log panel render error: {exc}")
        st.error("Audit log panel unavailable.")


def _render_log_viewer() -> None:
    """In-app log viewer — tail the active log file with level/text filters.

    Reads from ``utils.logging_setup.get_active_log_file()``. Renders the
    last N lines (user-controllable), color-coded by level. Shows a
    helpful placeholder if logging hasn't been configured yet.
    """
    try:
        from utils.log_reader import (
            filter_log_lines, parse_log_line, read_recent_log_lines,
        )
        from utils.logging_setup import get_active_log_file
    except Exception as exc:
        st.error(f"Log viewer modules unavailable: {exc}")
        return

    section_header(
        "Log Viewer",
        "Tail the rotated log file with level and substring filters. "
        "Updates on rerun — use the refresh button or change a filter to "
        "pick up new lines.",
    )

    log_path = get_active_log_file()

    # Controls: tail size · min level · text filter · refresh.
    c1, c2, c3, c4 = st.columns([1, 1, 2, 0.7], gap="small")
    with c1:
        tail_n = st.select_slider(
            "Tail lines", options=[50, 100, 200, 500, 1000],
            value=200, key="log_viewer_tail_n",
        )
    with c2:
        min_level = st.selectbox(
            "Min level",
            options=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            index=2,  # INFO
            key="log_viewer_min_level",
        )
    with c3:
        contains = st.text_input(
            "Contains (substring)", value="",
            placeholder="optional — case-insensitive",
            key="log_viewer_contains",
        )
    with c4:
        st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
        refresh = st.button("↻", key="log_viewer_refresh", use_container_width=True)

    # Read + filter.
    raw_lines = read_recent_log_lines(int(tail_n), log_path=log_path)
    if not raw_lines:
        # Path-relative-to-CWD for the message so the user sees roughly where to look.
        try:
            display_path = log_path.relative_to(Path.cwd())
        except Exception:
            display_path = log_path
        st.info(
            f"No log lines yet at `{display_path}`. Configure logging at "
            f"app startup with `utils.logging_setup.configure_logging()` "
            f"to begin writing to disk."
        )
        return

    filtered = filter_log_lines(
        raw_lines, min_level=min_level, contains=contains.strip() or None,
    )

    # Header stats
    st.markdown(
        f'<div style="font-size:0.72rem;color:{C_TEXT3};margin-bottom:8px">'
        f'Showing <b style="color:{C_TEXT}">{len(filtered)}</b> of '
        f'<b style="color:{C_TEXT}">{len(raw_lines)}</b> lines · '
        f'log path: <code style="font-size:0.66rem">{log_path}</code>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if not filtered:
        st.info("No lines match the current filters. Loosen them to see more.")
        return

    # Color-coded log block.
    level_color = {
        "TRACE":    C_TEXT3,
        "DEBUG":    C_TEXT3,
        "INFO":     C_TEXT,
        "WARNING":  C_MOD,
        "ERROR":    C_LOW,
        "CRITICAL": C_LOW,
    }

    rendered_lines: list[str] = []
    for line in filtered:
        parsed = parse_log_line(line)
        color = level_color.get(parsed.level, C_TEXT2)
        # Escape HTML — we don't trust message content. Minimal escape pass.
        safe = (
            line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        weight = "700" if parsed.level in ("ERROR", "CRITICAL") else "400"
        rendered_lines.append(
            f'<div style="color:{color};font-weight:{weight};'
            f'white-space:pre-wrap">{safe}</div>'
        )
    st.markdown(
        f'<div style="font-family:JetBrains Mono,monospace;font-size:0.72rem;'
        f'line-height:1.45;max-height:480px;overflow-y:auto;'
        f'background:rgba(0,0,0,0.20);border:1px solid rgba(232,230,225,0.06);'
        f'border-radius:3px;padding:10px 12px">'
        + "".join(rendered_lines)
        + "</div>",
        unsafe_allow_html=True,
    )


def _render_cache_performance(source_rows: list[dict]) -> None:
    section_header("Cache Size & Performance", "Per-source footprint, fetch volume, and hit rate (illustrative where no live telemetry).")
    headers = ["Data Source", "Cache Size", "Fetches (7d)", "Avg Fetch (ms)", "Hit Rate"]
    rows: list[list[str]] = []
    for r in source_rows:
        rng = random.Random(_seed_from_name(r["name"]))
        size_mb = r.get("size_mb", rng.uniform(0.1, 12.0))
        fetches = rng.randint(1, 48)
        avg_ms  = rng.randint(120, 3200)
        hit_pct = rng.randint(55, 98)
        hit_col = C_HIGH if hit_pct > 80 else C_MOD if hit_pct > 60 else C_LOW
        rows.append([
            _sans(r["name"]),
            _mono(f"{size_mb:.2f} MB", color=C_TEXT2),
            _mono(f"{fetches}", color=C_TEXT2),
            _mono(f"{avg_ms:,} ms", color=C_TEXT2),
            _mono(f"{hit_pct}%", color=hit_col, weight=700),
        ])
    wsj_market_table(headers, rows)
    # Fetches, avg fetch time, and hit rate are synthetic (seeded random); cache size reflects disk.
    st.markdown(
        source_footer([DataSource.demo("Fetches / Avg ms / Hit Rate (synthetic)"), DataSource.live("Cache Size", notes="Disk scan")]),
        unsafe_allow_html=True,
    )


def _render_api_keys() -> None:
    section_header("API Key Configuration", "Credential presence and daily usage — no values are displayed.")
    headers = ["API Service", "Key Configured", "Plan / Tier", "Rate Limit", "Usage Today", "% of Limit"]
    rows: list[list[str]] = []
    for k in _API_KEYS:
        configured = _key_configured(k["env"])
        cfg_pill = badge("CONFIGURED", C_HIGH) if configured else badge("MISSING", C_LOW)
        rng = random.Random(_seed_from_name(k["service"]))
        usage = rng.randint(0, 80) if configured else 0
        pct = usage
        pct_col = C_HIGH if pct < 60 else C_MOD if pct < 85 else C_LOW
        rows.append([
            _sans(k["service"], weight=600),
            cfg_pill,
            _sans(k["plan"], color=C_TEXT2),
            _mono(k["rate_limit"], color=C_TEXT2),
            _mono(f"{usage}", color=C_TEXT2),
            _mono(f"{pct}%", color=pct_col, weight=700),
        ])
    wsj_market_table(headers, rows)
    # Usage-today column is synthetic (seeded random); key presence is live env check.
    st.markdown(
        source_footer([DataSource.demo("Usage Today (synthetic)"), DataSource.live("Key Presence", notes="os.environ / st.secrets")]),
        unsafe_allow_html=True,
    )


def _render_staleness_heatmap(source_rows: list[dict]) -> None:
    section_header("Data Staleness Heatmap", "Source × hour-of-day (UTC) — illustrative coverage pattern.")
    try:
        names = [r["name"] for r in source_rows]
        hours = list(range(24))
        z = []
        for r in source_rows:
            row_vals = []
            rng = random.Random(_seed_from_name(r["name"]))
            ttl = r["ttl_h"]
            for _ in hours:
                age = rng.uniform(0, ttl * 2.5)
                if age <= ttl:
                    row_vals.append(0)
                elif age <= ttl * 2:
                    row_vals.append(1)
                else:
                    row_vals.append(2)
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
        apply_dark_layout(
            fig,
            height=420,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(tickfont=dict(size=9, color=C_TEXT3), showgrid=False, zeroline=False),
            yaxis=dict(tickfont=dict(size=10, color=C_TEXT2), showgrid=False),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        st.markdown(
            live_data_badge(DataSource.demo("Staleness pattern (synthetic — awaiting live telemetry)")),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"Staleness heatmap error: {exc}")
        alert_banner("Staleness heatmap unavailable.", level="warning")


def _render_error_log() -> None:
    section_header("Error Log — Last 24 Hours", "Recent fetch failures with resolution state (demo data pending log wiring).")
    mock_errors = [
        {"ts": _now_utc() - timedelta(minutes=12),          "source": "AIS Feed",      "type": "ConnectionTimeout", "msg": "aisstream.io timeout after 10s",             "resolved": False},
        {"ts": _now_utc() - timedelta(minutes=47),          "source": "Freightos FBX", "type": "HTTP 429",          "msg": "Rate limit exceeded — retry after 3600s",    "resolved": True},
        {"ts": _now_utc() - timedelta(hours=2, minutes=5),  "source": "News API",      "type": "HTTP 401",          "msg": "Invalid API key in st.secrets",              "resolved": False},
        {"ts": _now_utc() - timedelta(hours=4),             "source": "ACS Panama",    "type": "ParseError",        "msg": "Table schema changed at source website",     "resolved": False},
        {"ts": _now_utc() - timedelta(hours=7, minutes=33), "source": "FRED",          "type": "HTTP 503",          "msg": "Service unavailable — upstream maintenance", "resolved": True},
    ]
    headers = ["Timestamp (UTC)", "Source", "Error Type", "Message", "Status"]
    rows: list[list[str]] = []
    for e in mock_errors:
        ts_str = e["ts"].strftime("%Y-%m-%d %H:%M")
        status_pill = badge("RESOLVED", C_HIGH) if e["resolved"] else badge("OPEN", C_LOW)
        rows.append([
            _mono(ts_str, color=C_TEXT2),
            _sans(e["source"], weight=600),
            _sans(e["type"], color=C_MOD),
            _sans(e["msg"], color=C_TEXT2),
            status_pill,
        ])
    wsj_market_table(headers, rows)
    st.markdown(
        live_data_badge(DataSource.demo("Error log (demo — awaiting log wiring)")),
        unsafe_allow_html=True,
    )


def _render_manual_refresh(source_rows: list[dict]) -> None:
    section_header("Manual Refresh — Cache Invalidation", "Click a source to clear its parquet cache and queue a fresh fetch on next load.")
    cols_per_row = 4
    chunks = [source_rows[i:i+cols_per_row] for i in range(0, len(source_rows), cols_per_row)]
    for chunk in chunks:
        cols = st.columns(len(chunk))
        for col, r in zip(cols, chunk):
            with col:
                btn_label = f"{r['name']}  ·  {r['status']}"
                btn_key = f"refresh__{r['name'].replace(' ', '_').replace('/', '_')}"
                if st.button(btn_label, key=btn_key, use_container_width=True):
                    try:
                        sub = r.get("cache_sub", "")
                        if _CM_OK and sub:
                            CacheManager(_CACHE_DIR)
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
    section_header("Data Quality Metrics", "Record counts, null %, value range, and 3σ anomalies per in-memory dataset.")
    datasets: list[tuple[str, Any]] = [
        ("Port Results",    port_results),
        ("Route Results",   route_results),
        ("Macro Data",      macro_data),
        ("Stock Data",      stock_data),
        ("Freight Data",    freight_data),
        ("News Items",      news_items),
    ]

    headers = ["Dataset", "Records", "Null %", "Min Value", "Max Value", "Anomalies (>3σ)"]
    rows: list[list[str]] = []
    for label, data in datasets:
        try:
            if isinstance(data, pd.DataFrame) and not data.empty:
                records = len(data)
                numeric = data.select_dtypes(include=[np.number])
                null_pct = round(100 * data.isnull().values.sum() / max(data.size, 1), 1)
                if not numeric.empty:
                    mn = numeric.min().min()
                    mx = numeric.max().max()
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

        rows.append([
            _sans(label, weight=600),
            _mono(f"{records:,}", color=C_TEXT2),
            _mono(f"{null_pct}%", color=null_col, weight=700),
            _mono(mn_str, color=C_TEXT2),
            _mono(mx_str, color=C_TEXT2),
            _sans(anom_txt, color=anom_col),
        ])
    wsj_market_table(headers, rows)
    st.markdown(
        live_data_badge(DataSource.live("In-Memory Datasets", notes="Null % / anomaly computed at render time")),
        unsafe_allow_html=True,
    )


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
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('data_health'):
        try:
            page_header(
                title="Data Source Health & Freshness",
                subtitle=(
                    "Cache state, credential coverage and quality checks across "
                    "every data feed · Last scan "
                    f"{_now_utc().strftime('%Y-%m-%d %H:%M UTC')}"
                ),
                badge_text="LIVE SCAN",
                badge_color=C_ACCENT,
            )

            source_rows = _build_source_rows()

            # ── Movement 1: feed catalog ────────────────────────────────────────
            try:
                _render_overview(source_rows)
            except Exception as exc:
                logger.error(f"Overview render error: {exc}")
                st.error("Overview unavailable.")

            section_divider("Source Catalog")
            try:
                _render_source_table(source_rows)
            except Exception as exc:
                logger.error(f"Source table render error: {exc}")
                st.error("Source table unavailable.")

            # ── Movement 1.5: SLA dashboard ────────────────────────────────────
            section_divider("SLA")
            try:
                _render_sla_dashboard(source_rows)
            except Exception as exc:
                logger.error(f"SLA dashboard render error: {exc}")
                st.error("SLA dashboard unavailable.")

            # ── Movement 1.6: LLM usage telemetry ──────────────────────────────
            section_divider("LLM Usage")
            try:
                _render_llm_usage()
            except Exception as exc:
                logger.error(f"LLM usage render error: {exc}")
                st.error("LLM usage panel unavailable.")

            # ── Movement 1.65: tab render performance ──────────────────────────
            section_divider("Tab Performance")
            try:
                _render_tab_perf()
            except Exception as exc:
                logger.error(f"Tab perf render error: {exc}")
                st.error("Tab performance panel unavailable.")

            # ── Movement 1.68: audit log ───────────────────────────────────────
            try:
                _render_audit_log()
            except Exception as exc:
                logger.error(f"Audit log render error: {exc}")
                st.error("Audit log panel unavailable.")

            # ── Movement 1.7: log viewer ───────────────────────────────────────
            section_divider("Logs")
            try:
                _render_log_viewer()
            except Exception as exc:
                logger.error(f"Log viewer render error: {exc}")
                st.error("Log viewer unavailable.")

            # ── Movement 2: performance & credentials ───────────────────────────
            section_divider("Cache & Credentials")
            try:
                _render_cache_performance(source_rows)
            except Exception as exc:
                logger.error(f"Cache performance render error: {exc}")
                st.error("Cache performance unavailable.")

            st.divider()
            try:
                _render_api_keys()
            except Exception as exc:
                logger.error(f"API key render error: {exc}")
                st.error("API key table unavailable.")

            # ── Movement 3: diagnostics ─────────────────────────────────────────
            section_divider("Diagnostics")
            try:
                _render_staleness_heatmap(source_rows)
            except Exception as exc:
                logger.error(f"Staleness heatmap render error: {exc}")
                st.error("Heatmap unavailable.")

            st.divider()
            try:
                _render_error_log()
            except Exception as exc:
                logger.error(f"Error log render error: {exc}")
                st.error("Error log unavailable.")

            st.divider()
            try:
                _render_manual_refresh(source_rows)
            except Exception as exc:
                logger.error(f"Manual refresh render error: {exc}")
                st.error("Manual refresh unavailable.")

            section_divider("Dataset Quality")
            try:
                _render_data_quality(port_results, route_results, macro_data, stock_data, freight_data, news_items)
            except Exception as exc:
                logger.error(f"Data quality render error: {exc}")
                st.error("Data quality metrics unavailable.")

            # ── Provenance footer ───────────────────────────────────────────────
            try:
                st.markdown(
                    source_footer([
                        DataSource.live(
                            "Cache Scan",
                            notes="Parquet disk scan + os.environ / st.secrets check",
                        ),
                        DataSource.demo(
                            "Telemetry Fallback (synthetic where no live feed)"
                        ),
                    ]),
                    unsafe_allow_html=True,
                )
            except Exception as exc:
                logger.warning(f"Source footer render error: {exc}")

        except Exception as exc:
            logger.error(f"tab_data_health.render critical error: {exc}")
            st.error(f"Data health tab encountered a critical error: {exc}")
