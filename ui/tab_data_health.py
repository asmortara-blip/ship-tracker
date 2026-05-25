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
from typing import Any, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_BG,
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


def _render_anomaly_panel() -> None:
    """Time-series anomaly detection panel — current hits + per-metric settings.

    Renders just below the perf-budgets panel inside the Tab Performance
    section. Shows operators which tracked metrics have drifted out of
    tolerance and lets them edit each metric's lookback / threshold /
    method inline via an expander. Wrapped in a single try/except so a
    detection-engine outage cannot break the rest of the Data Health tab.
    """
    try:
        from engine.anomaly_detect import (
            AnomalyConfig,
            check_and_alert_anomalies,
            detect_all_anomalies,
            get_anomaly_configs,
            save_anomaly_configs,
            _VALID_METHODS,
        )

        st.markdown(
            f'<div style="font-size:0.72rem;text-transform:uppercase;'
            f'letter-spacing:0.10em;color:{C_TEXT3};font-weight:700;'
            f'margin:14px 0 6px 0">Anomaly Detection</div>',
            unsafe_allow_html=True,
        )

        configs = get_anomaly_configs()
        results = detect_all_anomalies(configs=configs)

        if results:
            sev_col_map = {"CRITICAL": C_LOW, "HIGH": C_LOW, "MEDIUM": C_MOD}
            headers = ["Metric", "Observed", "Baseline", "Z / Drift", "Severity"]
            rows: list[list[str]] = []
            for r in results:
                sev_col = sev_col_map.get(r.severity, C_TEXT2)
                rows.append([
                    _sans(r.metric_id, weight=600),
                    _mono(f"{r.observed_value:.4g}", color=C_TEXT),
                    _mono(
                        f"{r.baseline_mean:.4g} ± {r.baseline_std:.4g}",
                        color=C_TEXT2,
                    ),
                    _mono(
                        f"z={r.z_score:+.2f} / {r.drift_pct:+.2f}%",
                        color=C_TEXT2,
                    ),
                    _sans(r.severity, weight=700, color=sev_col),
                ])
            wsj_market_table(headers, rows)
        else:
            st.success("No anomalies detected across tracked metrics.")

        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("Run detection now", key="anomaly_run_now"):
                counts = check_and_alert_anomalies()
                st.info(
                    f"Checked {counts.get('checked', 0)} · detected "
                    f"{counts.get('detected', 0)} · alerted "
                    f"{counts.get('alerted', 0)} · skipped (cooldown) "
                    f"{counts.get('skipped_cooldown', 0)}"
                )

        with st.expander("Anomaly detection settings", expanded=False):
            st.caption(
                "Per-metric configuration. Each metric has a lookback "
                "window (the baseline), a z-threshold (the test "
                "statistic that trips a fire), and a method. "
                "Disable a row to silence it without deleting the "
                "config."
            )
            edited: dict[str, dict[str, Any]] = {}
            for cfg in configs:
                st.markdown(
                    f"<div style='margin-top:6px;font-weight:600;color:{C_TEXT};'>"
                    f"{cfg.metric_id}</div>",
                    unsafe_allow_html=True,
                )
                c_enabled, c_lookback, c_thresh, c_method = st.columns(
                    [1, 1, 1, 2]
                )
                with c_enabled:
                    e = st.checkbox(
                        "Enabled",
                        value=bool(cfg.enabled),
                        key=f"anomaly_enabled_{cfg.metric_id}",
                    )
                with c_lookback:
                    lb = st.number_input(
                        "Lookback (obs)",
                        min_value=2,
                        max_value=365,
                        value=int(cfg.lookback_days),
                        step=1,
                        key=f"anomaly_lookback_{cfg.metric_id}",
                    )
                with c_thresh:
                    zt = st.number_input(
                        "Threshold",
                        min_value=0.1,
                        max_value=100.0,
                        value=float(cfg.z_threshold),
                        step=0.1,
                        format="%.2f",
                        key=f"anomaly_zthresh_{cfg.metric_id}",
                    )
                with c_method:
                    options = list(_VALID_METHODS)
                    idx = options.index(cfg.method) if cfg.method in options else 0
                    m = st.selectbox(
                        "Method",
                        options=options,
                        index=idx,
                        key=f"anomaly_method_{cfg.metric_id}",
                    )
                edited[cfg.metric_id] = {
                    "enabled": e,
                    "lookback_days": int(lb),
                    "z_threshold": float(zt),
                    "method": str(m),
                    "min_samples": int(cfg.min_samples),
                }

            if st.button("Save anomaly configs", key="anomaly_save"):
                new_configs = [
                    AnomalyConfig(
                        metric_id=cfg.metric_id,
                        lookback_days=edited[cfg.metric_id]["lookback_days"],
                        z_threshold=edited[cfg.metric_id]["z_threshold"],
                        method=edited[cfg.metric_id]["method"],
                        min_samples=edited[cfg.metric_id]["min_samples"],
                        enabled=edited[cfg.metric_id]["enabled"],
                    )
                    for cfg in configs
                ]
                ok = save_anomaly_configs(new_configs)
                if ok:
                    st.success("Anomaly configs saved.")
                else:
                    st.error("Couldn't save anomaly configs — see logs.")
    except Exception as exc:
        logger.exception(f"Anomaly detection panel render error: {exc}")
        st.error("Anomaly detection panel unavailable.")


def _render_perf_budgets() -> None:
    """Per-tab performance budgets panel — budget vs observed p95 + status.

    Sits above the per-tab perf table; shows operators which tabs are
    inside / outside budget at a glance and lets them edit the budgets
    inline via an expander. Wrapped in a single try/except so a
    perf_budgets DB outage cannot break the rest of the Tab
    Performance section.
    """
    try:
        from engine.perf_budgets import (
            load_budgets,
            save_budgets,
            check_budgets,
            PerfBudget,
        )
        from engine.perf_telemetry import get_perf_summary

        st.markdown(
            f'<div style="font-size:0.72rem;text-transform:uppercase;'
            f'letter-spacing:0.10em;color:{C_TEXT3};font-weight:700;'
            f'margin:14px 0 6px 0">Performance Budgets</div>',
            unsafe_allow_html=True,
        )

        budgets = load_budgets()
        breaches = {b.tab_module: b for b in check_budgets()}

        # Pull one perf summary per window the budgets reference so
        # the observed p95 is available for OK tabs too.
        windows = sorted({int(b.window_hours) for b in budgets})
        summaries: dict[int, dict] = {}
        for w in windows:
            summaries[w] = get_perf_summary(window_hours=w) or {}

        headers = ["Tab", "Budget P95", "Observed P95", "Samples", "Status"]
        rows: list[list[str]] = []
        for b in budgets:
            summary = summaries.get(int(b.window_hours), {})
            by_tab = summary.get("by_tab", {}) if isinstance(summary, dict) else {}
            stats = by_tab.get(b.tab_module) if isinstance(by_tab, dict) else None
            if isinstance(stats, dict):
                cnt = int(stats.get("count", 0) or 0)
                obs_p95 = int(stats.get("p95_ms", 0) or 0) / 1000.0
            else:
                cnt = 0
                obs_p95 = 0.0

            breach = breaches.get(b.tab_module)
            if breach is not None:
                if breach.severity == "critical":
                    status_str = "critical"
                    status_col = C_LOW
                else:
                    status_str = "warn"
                    status_col = C_MOD
            elif cnt == 0:
                status_str = "no-data"
                status_col = C_TEXT3
            else:
                status_str = "within budget"
                status_col = C_HIGH

            rows.append([
                _sans(b.tab_module, weight=600),
                _mono(f"{b.max_p95_seconds:.2f}s", color=C_TEXT2),
                _mono(f"{obs_p95:.2f}s", color=C_TEXT),
                _mono(f"{cnt:,}", color=C_TEXT2),
                _sans(status_str, weight=700, color=status_col),
            ])
        if rows:
            wsj_market_table(headers, rows)
        else:
            st.info("No budgets configured.")

        with st.expander("Edit budgets…", expanded=False):
            st.caption(
                "Set the maximum acceptable p95 render time per tab. "
                "When the observed p95 exceeds the budget over the "
                "window, a PERF_BUDGET alert fires."
            )
            edited: dict[str, float] = {}
            for b in budgets:
                edited[b.tab_module] = st.number_input(
                    label=b.tab_module,
                    min_value=0.1,
                    max_value=120.0,
                    value=float(b.max_p95_seconds),
                    step=0.5,
                    format="%.2f",
                    key=f"perf_budget_input_{b.tab_module}",
                )
            if st.button("Save budgets", key="perf_budgets_save"):
                new_budgets = [
                    PerfBudget(
                        tab_module=b.tab_module,
                        max_p95_seconds=float(edited.get(b.tab_module, b.max_p95_seconds)),
                        max_mean_seconds=b.max_mean_seconds,
                        window_hours=b.window_hours,
                    )
                    for b in budgets
                ]
                ok = save_budgets(new_budgets)
                if ok:
                    st.success("Budgets saved.")
                else:
                    st.error("Couldn't save budgets — see logs.")
    except Exception as exc:
        logger.exception(f"Perf budgets panel render error: {exc}")
        st.error("Perf budgets panel unavailable.")


def _build_tab_perf_scatter(by_tab: dict) -> go.Figure:
    """Per-tab latency scatter: x = median ms, y = p95 ms.

    Captures the two dimensions the existing "slowest tabs" table can't
    show together: how slow a tab typically renders (median) AND how
    inconsistent that latency is (p95 vs. median). Marker size scales
    with render count; colour bands by success rate (>=99% green,
    95–99% amber, <95% red). A y=x reference diagonal marks the ideal
    flat-distribution case — points well above it have a heavy tail.

    Pure builder — no ``st.*`` calls — so the lock-in tests exercise it
    directly. Empty / None input returns an annotated-empty figure.
    """
    fig = go.Figure()
    items = list((by_tab or {}).items())
    if not items:
        fig.add_annotation(
            text="No render telemetry yet",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="Per-tab latency", height=280)
        return fig

    names:   list[str]   = []
    medians: list[float] = []
    p95s:    list[float] = []
    counts:  list[int]   = []
    success: list[float] = []
    for name, stats in items:
        s = stats or {}
        names.append(str(name))
        medians.append(float(s.get("median_ms", 0) or 0))
        p95s.append(float(s.get("p95_ms", 0) or 0))
        counts.append(int(s.get("count", 0) or 0))
        # success_rate may be missing on stats; fall back to 1.0.
        success.append(float(s.get("success_rate", 1.0) or 0.0))

    # Marker size: scale render count into a 10–32 px range so the
    # least-used tab still shows up and the most-used doesn't dominate.
    max_n = max(counts) if counts else 1
    sizes = [10 + 22 * (c / max_n if max_n else 0.0) for c in counts]

    def _success_color(rate: float) -> str:
        if rate >= 0.99:
            return C_HIGH
        if rate >= 0.95:
            return C_MOD
        return C_LOW
    colors = [_success_color(r) for r in success]

    fig.add_trace(go.Scatter(
        x=medians,
        y=p95s,
        mode="markers+text",
        marker={
            "size": sizes,
            "color": colors,
            "line": {"color": C_BG, "width": 1.5},
            "opacity": 0.85,
        },
        text=names,
        textposition="top center",
        textfont={"color": C_TEXT3, "size": 10},
        customdata=list(zip(counts, [r * 100 for r in success])),
        hovertemplate=(
            "<b>%{text}</b><br>"
            "Median: %{x:.0f} ms<br>"
            "P95: %{y:.0f} ms<br>"
            "Renders: %{customdata[0]}<br>"
            "Success: %{customdata[1]:.2f}%<extra></extra>"
        ),
        showlegend=False,
    ))

    # y = x diagonal. Anything above means p95 > median → heavy-tail
    # latency on that tab.
    if medians and p95s:
        axis_max = max(max(medians), max(p95s)) * 1.10
        fig.add_shape(
            type="line",
            x0=0, y0=0, x1=axis_max, y1=axis_max,
            line={"color": "rgba(255,255,255,0.10)", "width": 1, "dash": "dot"},
            layer="below",
        )

    apply_dark_layout(
        fig,
        title="Per-tab latency — median × p95 (size = render count)",
        height=320,
    )
    fig.update_layout(
        xaxis={"title": "Median latency (ms)",
               "gridcolor": "rgba(255,255,255,0.04)"},
        yaxis={"title": "P95 latency (ms)",
               "gridcolor": "rgba(255,255,255,0.04)"},
        margin={"l": 70, "r": 24, "t": 44, "b": 50},
    )
    return fig


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

        # ── Performance budgets panel (above the per-tab table) ──────
        try:
            _render_perf_budgets()
        except Exception as exc:
            logger.exception(f"Perf budgets sub-panel error: {exc}")
            st.error("Perf budgets panel unavailable.")

        # ── Anomaly detection panel (sits next to perf budgets) ───────
        try:
            _render_anomaly_panel()
        except Exception as exc:
            logger.exception(f"Anomaly panel sub-panel error: {exc}")
            st.error("Anomaly detection panel unavailable.")

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

        # Median × p95 scatter — captures latency AND variance per tab.
        # Sits above the slow-tabs table so the operator scans the visual
        # cross-section first, then drills into the sorted list.
        try:
            st.plotly_chart(
                _build_tab_perf_scatter(by_tab),
                use_container_width=True,
                config={"displayModeBar": False},
                key="data_health_tab_perf_scatter",
            )
        except Exception:
            logger.exception("Tab perf scatter render failed")

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


def _render_audit_search_panel() -> None:
    """Audit log search panel — multi-filter search over ``audit_events``.

    Sits next to the legacy "Recent audit events" panel above and offers
    operators a richer query surface:

    * user_id / action / action_prefix / entity_type / entity_id exact-match
    * since / until ISO-bracketed time window
    * free-text grep over ``detail_json`` + ``action`` + ``entity_id``
    * limit cap with "N matches (of M total)" caption

    Default scope is the CURRENT user — an operator without admin scope
    should never see another user's audit events. The "All users (admin)"
    toggle widens scope but is opt-in (and the operator's user_id is
    still echoed in the caption so they cannot accidentally believe
    they are viewing a per-user slice).

    Imports are lazy (matches the surrounding panel pattern) so a
    missing helper does not break the rest of the Data Health tab. The
    full body is wrapped in try/except — same defensive contract as
    every other ``_render_*`` panel in this file.
    """
    try:
        from engine.audit_search import (
            AuditSearchQuery,
            get_distinct_actions,
            get_distinct_entity_types,
            search_audit,
        )
        from state.user_scope import current_user_id

        section_divider("Audit Log Search")
        section_header(
            "Audit Log Search",
            "Multi-filter search over the audit_events table. Default "
            "scope is your own user; toggle 'All users' for an admin "
            "view. Source: engine.audit_search.",
        )

        # ── Action + entity_type dropdowns (DB-populated) ────────────────
        # "(any)" sentinel is mapped to ``None`` before the query runs
        # so the user can return to the "no filter" state without
        # clearing the text inputs.
        try:
            actions_db = get_distinct_actions()
        except Exception:
            actions_db = []
        try:
            entity_types_db = get_distinct_entity_types()
        except Exception:
            entity_types_db = []
        action_options = ["(any)"] + actions_db
        entity_type_options = ["(any)"] + entity_types_db

        # ── Row 1: user_id | action | action_prefix ──────────────────────
        # ``user_id_input`` defaults to empty so the "All users" checkbox
        # below can fill in the current user's id when toggled OFF.
        r1c1, r1c2, r1c3 = st.columns(3, gap="small")
        with r1c1:
            user_id_input = st.text_input(
                "User ID",
                value="",
                key="audit_search_user_id",
                help="Exact match. Leave blank to filter to your own "
                     "user (unless 'All users' is enabled).",
            )
        with r1c2:
            action_pick = st.selectbox(
                "Action",
                options=action_options,
                index=0,
                key="audit_search_action",
            )
        with r1c3:
            action_prefix_input = st.text_input(
                "Action prefix",
                value="",
                key="audit_search_action_prefix",
                help="e.g. 'login_' matches login_success AND login_failure.",
            )

        # ── Row 2: entity_type | entity_id ───────────────────────────────
        r2c1, r2c2 = st.columns(2, gap="small")
        with r2c1:
            entity_type_pick = st.selectbox(
                "Entity type",
                options=entity_type_options,
                index=0,
                key="audit_search_entity_type",
            )
        with r2c2:
            entity_id_input = st.text_input(
                "Entity ID",
                value="",
                key="audit_search_entity_id",
            )

        # ── Row 3: since / until date pickers ────────────────────────────
        # The date pickers cover the most common operator query
        # ("everything yesterday") without forcing them to hand-write an
        # ISO timestamp. The day boundary is set at 00:00:00 UTC for
        # both ends; ``until`` is half-open so "today only" is
        # ``since=today, until=tomorrow``.
        today = datetime.now(timezone.utc).date()
        default_since = today - timedelta(days=7)
        r3c1, r3c2 = st.columns(2, gap="small")
        with r3c1:
            since_date = st.date_input(
                "Since (UTC)",
                value=default_since,
                key="audit_search_since",
            )
        with r3c2:
            until_date = st.date_input(
                "Until (UTC, exclusive)",
                value=today + timedelta(days=1),
                key="audit_search_until",
            )

        # ── Row 4: free-text grep | limit | admin toggle ────────────────
        r4c1, r4c2, r4c3 = st.columns([2, 1, 1], gap="small")
        with r4c1:
            text_input = st.text_input(
                "Free-text search",
                value="",
                key="audit_search_text",
                help="Case-insensitive substring match over detail_json, "
                     "action, and entity_id.",
            )
        with r4c2:
            limit_input = st.number_input(
                "Limit",
                min_value=1,
                max_value=10_000,
                value=100,
                step=50,
                key="audit_search_limit",
            )
        with r4c3:
            admin_scope = st.checkbox(
                "All users (admin)",
                value=False,
                key="audit_search_admin_scope",
                help="Show events across every user. Default is your "
                     "own user only.",
            )

        # ── Resolve the per-user scope. The current user's id is always
        # resolved (even when the operator typed something into the
        # user_id box) so the "scoped to" caption can tell them what is
        # actually being applied.
        self_uid = current_user_id()
        # If the operator typed a user_id, honour it verbatim. Otherwise
        # default to their own unless "All users" is checked.
        if user_id_input.strip():
            effective_user_id: Optional[str] = user_id_input.strip()
        elif admin_scope:
            effective_user_id = None
        else:
            # No typed value and not admin → scope to self. An empty
            # self_uid (no session) falls through to "no scope" which
            # is intentional — the local CLI / test path should not
            # be silently filtered to a non-existent user.
            effective_user_id = self_uid or None

        # ── Search button drives the query so the panel does not refetch
        # on every keystroke. The result lives in a ``st.session_state``
        # slot so the download buttons stay populated after a rerun.
        search_clicked = st.button(
            "Search",
            key="audit_search_run",
            type="primary",
        )

        result = None
        if search_clicked:
            query = AuditSearchQuery(
                user_id=effective_user_id,
                action=(action_pick if action_pick != "(any)" else None),
                action_prefix=action_prefix_input.strip() or None,
                entity_type=(entity_type_pick
                             if entity_type_pick != "(any)" else None),
                entity_id=entity_id_input.strip() or None,
                # date_input returns a datetime.date — format as ISO at
                # 00:00:00 UTC so the SQL string compare is unambiguous.
                since=(datetime.combine(
                    since_date, datetime.min.time(), tzinfo=timezone.utc,
                ).isoformat() if since_date else None),
                until=(datetime.combine(
                    until_date, datetime.min.time(), tzinfo=timezone.utc,
                ).isoformat() if until_date else None),
                text=text_input.strip() or None,
                limit=int(limit_input),
            )
            result = search_audit(query)
            st.session_state["_audit_search_result"] = result
        else:
            # Pull the last result back so the download buttons keep
            # serving the most recent search across non-search reruns.
            result = st.session_state.get("_audit_search_result")

        # ── Result rendering ─────────────────────────────────────────────
        if result is None:
            st.caption("Set filters and click Search to query the audit log.")
            return

        scope_label = (
            "all users" if effective_user_id is None
            else f"user_id={effective_user_id}"
        )
        if result.total_matched == 0:
            st.info(f"No matches (scoped to {scope_label}).")
            return

        shown = len(result.events)
        if result.total_matched > shown:
            st.caption(
                f"Showing {shown:,} of {result.total_matched:,} matches "
                f"(scoped to {scope_label}). Raise the limit to see more."
            )
        else:
            st.caption(
                f"{result.total_matched:,} match"
                f"{'es' if result.total_matched != 1 else ''} "
                f"(scoped to {scope_label})."
            )

        # ── Render as a DataFrame so the operator can sort / column-pick
        # in the Streamlit grid widget. ``detail_json`` is reflected as
        # a compact JSON string for table readability — the full dict
        # is in the JSONL download for a reviewer who wants the
        # structured payload.
        try:
            display_rows: list[dict] = []
            for ev in result.events:
                try:
                    detail_str = json.dumps(
                        ev.get("detail_json") or {}, default=str,
                    )
                except Exception:
                    detail_str = ""
                display_rows.append({
                    "created_at": ev.get("created_at", ""),
                    "user_id": ev.get("user_id", ""),
                    "action": ev.get("action", ""),
                    "entity_type": ev.get("entity_type", ""),
                    "entity_id": ev.get("entity_id", ""),
                    "detail_json": detail_str,
                    "event_id": ev.get("event_id", ""),
                })
            df = pd.DataFrame(display_rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
        except Exception as exc:
            logger.warning(f"Audit search dataframe render failed: {exc}")
            st.error("Result table unavailable.")

        # ── Download buttons (lazy import). Two formats: CSV for Excel,
        # JSONL for a SIEM sidecar / downstream JSON consumer.
        dl_c1, dl_c2 = st.columns(2, gap="small")
        with dl_c1:
            try:
                from utils.csv_export import (
                    rows_to_csv_bytes, safe_filename,
                )
                csv_rows = []
                for ev in result.events:
                    try:
                        detail_str = json.dumps(
                            ev.get("detail_json") or {}, default=str,
                        )
                    except Exception:
                        detail_str = ""
                    csv_rows.append({
                        "event_id": ev.get("event_id", ""),
                        "created_at": ev.get("created_at", ""),
                        "user_id": ev.get("user_id", ""),
                        "action": ev.get("action", ""),
                        "entity_type": ev.get("entity_type", ""),
                        "entity_id": ev.get("entity_id", ""),
                        "detail_json": detail_str,
                    })
                csv_bytes = rows_to_csv_bytes(csv_rows)
                st.download_button(
                    label="Download as CSV",
                    data=csv_bytes,
                    file_name=safe_filename("audit_search", ext="csv"),
                    mime="text/csv",
                    key="audit_search_dl_csv",
                )
            except Exception as exc:
                logger.warning(f"Audit search CSV download failed: {exc}")
                st.caption("CSV download unavailable.")
        with dl_c2:
            try:
                from utils.audit_export import rows_to_jsonl
                from utils.csv_export import safe_filename

                jsonl_bytes = rows_to_jsonl(result.events)
                st.download_button(
                    label="Download as JSONL",
                    data=jsonl_bytes,
                    file_name=safe_filename("audit_search", ext="jsonl"),
                    mime="application/x-ndjson",
                    key="audit_search_dl_jsonl",
                )
            except Exception as exc:
                logger.warning(f"Audit search JSONL download failed: {exc}")
                st.caption("JSONL download unavailable.")

        st.markdown(
            live_data_badge(DataSource.live(
                "audit_events table",
                notes="engine.audit_search.search_audit",
            )),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.exception(f"Audit search panel render error: {exc}")
        st.error("Audit search panel unavailable.")


def _render_vault_panel() -> None:
    """Render the secrets-vault management panel — rotate the master
    key + see how many channels carry encrypted targets."""
    try:
        from state.vault import (
            rotate_key,
            is_encrypted,
            encrypt_all_channel_targets,
            decrypt_all_channel_targets,
        )
        from engine.alert_delivery import load_channels

        section_divider("Secrets Vault")

        st.caption(
            "Channel targets (Slack webhook URLs, integration keys) can be "
            "stored encrypted-at-rest in SQLite. Protects against casual DB "
            "leaks; not against attackers with process access. "
            "See docs/DEPLOYMENT.md → Vault for the threat model."
        )

        try:
            channels = load_channels()
        except Exception as exc:
            logger.exception(f"Vault panel: load_channels failed: {exc}")
            channels = []

        # Inspect the persisted form of each channel.target rather than
        # the decrypted runtime value — load_channels transparently
        # decrypts, so we need a separate SQLite peek to count encrypted
        # rows. Use the cheap raw-SELECT path.
        encrypted_count = 0
        plaintext_count = 0
        try:
            from state.db import get_connection
            conn = get_connection()
            rows = conn.execute("SELECT target FROM delivery_channels").fetchall()
            for r in rows:
                if is_encrypted(r["target"]):
                    encrypted_count += 1
                else:
                    plaintext_count += 1
        except Exception as exc:
            logger.debug(f"Vault panel: raw-target SELECT failed: {exc}")

        total = encrypted_count + plaintext_count

        metric_card_row(
            [
                {
                    "label": "Total Channels", "value": str(total),
                    "accent": C_ACCENT,
                    "sublabel": "across all delivery kinds",
                },
                {
                    "label": "Encrypted", "value": str(encrypted_count),
                    "accent": C_HIGH if encrypted_count > 0 else C_TEXT3,
                    "sublabel": "vault:v1: prefix on target",
                },
                {
                    "label": "Plaintext", "value": str(plaintext_count),
                    "accent": C_MOD if plaintext_count > 0 else C_HIGH,
                    "sublabel": "consider re-saving with encrypt_target=True",
                },
            ],
            columns=3,
        )

        # ─── Rotate master key ─────────────────────────────────────────────
        st.markdown(
            "<div style='margin-top:10px'>"
            "<strong>Rotate master key</strong> — generates a fresh key + "
            "re-encrypts every currently-encrypted channel target with it. "
            "Old envelopes become unreadable after rotation. Audit-logged."
            "</div>",
            unsafe_allow_html=True,
        )

        rot_left, rot_right = st.columns([2, 6])
        with rot_left:
            confirm = st.checkbox(
                "I understand", value=False, key="vault_rotate_confirm",
                help=(
                    "The old key is overwritten. Re-encrypted channels are "
                    "re-readable; any unwrapped backups of the old key "
                    "become useless."
                ),
            )
        with rot_right:
            if st.button("Rotate now", disabled=not confirm,
                         key="vault_rotate_btn"):
                try:
                    ok = rotate_key()
                except Exception as exc:
                    logger.exception(f"Vault rotate_key failed: {exc}")
                    ok = False
                if ok:
                    st.success(
                        f"Rotated. {encrypted_count} channel target(s) "
                        "re-encrypted with the new key."
                    )
                else:
                    st.error(
                        "Rotation reported partial failure — check the logs. "
                        "Some channels may now have unreadable targets."
                    )

        # ─── Bulk encrypt every plaintext target ───────────────────────────
        st.markdown(
            "<div style='margin-top:14px'>"
            "<strong>Encrypt all plaintext targets</strong> — sweeps every "
            "channel and wraps any plaintext target in a vault envelope. "
            "Already-encrypted rows are left alone. Audit-logged."
            "</div>",
            unsafe_allow_html=True,
        )

        enc_left, enc_right = st.columns([2, 6])
        with enc_left:
            enc_confirm = st.checkbox(
                "I understand", value=False,
                key="vault_encrypt_all_confirm",
                help=(
                    "Every plaintext channel target is encrypted in place. "
                    "load_channels transparently decrypts on read, so the "
                    "delivery pipeline is unaffected."
                ),
            )
        with enc_right:
            if st.button("Encrypt all plaintext targets",
                         disabled=not enc_confirm,
                         key="vault_encrypt_all_btn"):
                try:
                    enc_result = encrypt_all_channel_targets()
                except Exception as exc:
                    logger.exception(
                        f"Vault encrypt_all_channel_targets failed: {exc}"
                    )
                    enc_result = {
                        "total": 0, "already_encrypted": 0,
                        "newly_encrypted": 0, "failed": 0,
                    }
                st.success(
                    f"Encrypted {enc_result.get('newly_encrypted', 0)} of "
                    f"{enc_result.get('total', 0)} channel targets."
                )
                if enc_result.get("failed", 0) > 0:
                    st.warning(
                        f"{enc_result['failed']} channel(s) failed to "
                        "encrypt — check the logs."
                    )

        # ─── Bulk decrypt (rollback) ────────────────────────────────────────
        st.markdown(
            "<div style='margin-top:14px'>"
            "<strong>Decrypt all (rollback)</strong> — inverse of the bulk "
            "encrypt. Restores every encrypted target to plaintext in the "
            "SQLite table. Use before disabling encryption / migrating to "
            "a non-vault setup. Audit-logged."
            "</div>",
            unsafe_allow_html=True,
        )
        st.warning(
            "This re-exposes every channel target as plaintext in SQLite."
        )

        dec_left, dec_right = st.columns([2, 6])
        with dec_left:
            dec_confirm = st.checkbox(
                "I understand", value=False,
                key="vault_decrypt_all_confirm",
                help=(
                    "Every encrypted channel target becomes plaintext on "
                    "disk. Anyone with read access to the SQLite file can "
                    "see the webhook URLs / integration keys."
                ),
            )
        with dec_right:
            if st.button("Decrypt all (rollback)",
                         disabled=not dec_confirm,
                         key="vault_decrypt_all_btn"):
                try:
                    dec_result = decrypt_all_channel_targets()
                except Exception as exc:
                    logger.exception(
                        f"Vault decrypt_all_channel_targets failed: {exc}"
                    )
                    dec_result = {
                        "total": 0, "already_encrypted": 0,
                        "newly_decrypted": 0, "failed": 0,
                    }
                st.success(
                    f"Decrypted {dec_result.get('newly_decrypted', 0)} of "
                    f"{dec_result.get('total', 0)} channel targets."
                )
                if dec_result.get("failed", 0) > 0:
                    st.warning(
                        f"{dec_result['failed']} channel(s) failed to "
                        "decrypt — they remain encrypted. Check the logs."
                    )

    except Exception as exc:
        logger.exception(f"Vault panel render error: {exc}")
        st.error("Vault panel unavailable.")


def _render_security_panel() -> None:
    """Render the per-user Security panel — MFA enrollment + API tokens.

    Surfaces ``auth.mfa`` and ``auth.tokens`` for the currently-logged-in
    user (taken from ``st.session_state.current_user``). The panel is
    purely user-scoped: it never reveals another account's MFA secret or
    tokens, and it never persists raw secrets/tokens beyond the
    single-render display in an ``st.code`` block.

    Sub-sections:
      * MFA — enable (with TOTP-code confirmation) / disable (with a
        confirm checkbox so a stray click cannot lock out an account).
      * API Tokens — list / create / revoke. The raw token is shown
        ONCE at create time and never re-derivable from the DB.
    """
    try:
        # Lazy imports keep this panel off the tab-load critical path
        # AND keep the auth modules optional at module-import time —
        # tests that stub out auth do not need to provide these.
        from auth.mfa import (
            disable_mfa,
            enable_mfa,
            generate_secret,
            is_mfa_enabled,
            provisioning_uri,
            verify_totp,
        )
        from auth.tokens import create_token, list_tokens, revoke_token

        section_divider("My Security")

        current_user = st.session_state.get("current_user")
        if current_user is None:
            st.info("Log in to manage your security settings.")
            return

        user_id = getattr(current_user, "user_id", None)
        username = getattr(current_user, "username", "")
        if not isinstance(user_id, str) or not user_id:
            st.info("Log in to manage your security settings.")
            return

        # ─── MFA sub-section ──────────────────────────────────────────────
        st.markdown(
            "<div style='margin-top:6px'><strong>Two-Factor Authentication "
            "(TOTP)</strong></div>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Add a 6-digit code from an authenticator app (Google "
            "Authenticator, 1Password, Authy, …) to your login. Strictly "
            "opt-in per account."
        )

        # v21: if a previous enable_mfa flow stashed recovery codes in
        # session_state, surface them ONCE here. We pop the key after
        # rendering so a page reload does not silently re-show codes
        # the user may have already saved.
        sec_pending_codes = st.session_state.pop("security_mfa_show_codes", None)
        if sec_pending_codes:
            st.warning(
                "Save these recovery codes NOW. Each is single-use; they "
                "let you sign in if you lose your authenticator. They "
                "will NEVER be shown again."
            )
            st.code("\n".join(sec_pending_codes), language="text")

        mfa_on = bool(is_mfa_enabled(user_id))
        if mfa_on:
            st.success("MFA is ON for this account.")
            confirm_disable = st.checkbox(
                "I understand this removes my second factor",
                value=False,
                key="security_disable_mfa_confirm",
                help=(
                    "Disabling MFA returns the account to password-only "
                    "login. The current secret is wiped — re-enabling "
                    "later requires fresh enrollment."
                ),
            )
            if st.button(
                "Disable MFA",
                disabled=not confirm_disable,
                key="security_disable_mfa",
            ):
                if disable_mfa(user_id):
                    st.success("MFA disabled.")
                    st.rerun()
                else:
                    st.error("Disable failed — check the logs.")
        else:
            st.info("MFA is OFF for this account.")
            pending = st.session_state.get("pending_mfa_secret")
            if not pending:
                if st.button("Enable MFA", key="security_enable_mfa"):
                    st.session_state["pending_mfa_secret"] = generate_secret()
                    st.rerun()
            else:
                # Mid-enrollment: show provisioning URI + raw secret + a
                # code input. The user types a code from their app to
                # prove the secret is correctly loaded BEFORE we commit
                # to the DB — protects against a bad-QR-scan lockout.
                uri = provisioning_uri(
                    pending,
                    account=username or user_id,
                    issuer="Ship Tracker",
                )
                st.markdown(
                    "<strong>Step 1.</strong> Scan this URI with your "
                    "authenticator app (or paste the raw secret manually).",
                    unsafe_allow_html=True,
                )
                st.code(uri, language="text")
                st.markdown(
                    "<strong>Raw secret</strong> (for manual entry):",
                    unsafe_allow_html=True,
                )
                st.code(pending, language="text")
                st.markdown(
                    "<strong>Step 2.</strong> Enter the current 6-digit "
                    "code from the app to confirm.",
                    unsafe_allow_html=True,
                )
                code = st.text_input(
                    "6-digit code from your authenticator app",
                    max_chars=6,
                    key="security_mfa_code",
                )
                act_col1, act_col2 = st.columns([2, 2])
                with act_col1:
                    if st.button(
                        "Confirm and enable",
                        key="security_mfa_confirm",
                    ):
                        if verify_totp(pending, code or ""):
                            # v21: enable_mfa now returns
                            # ``(ok, recovery_codes)``. Stash the
                            # codes in session_state so the next
                            # render can surface them ONCE in a
                            # copyable st.code block — the plaintext
                            # is unrecoverable after the user
                            # navigates away.
                            ok, codes = enable_mfa(user_id, pending)
                            if ok:
                                st.session_state.pop(
                                    "pending_mfa_secret", None
                                )
                                st.session_state.pop(
                                    "security_mfa_code", None
                                )
                                if codes:
                                    st.session_state[
                                        "security_mfa_show_codes"
                                    ] = codes
                                st.success("MFA enabled.")
                                st.rerun()
                            else:
                                st.error(
                                    "Enable failed — check the logs."
                                )
                        else:
                            st.error(
                                "Code didn't verify — try again."
                            )
                with act_col2:
                    if st.button("Cancel setup", key="security_mfa_cancel"):
                        st.session_state.pop("pending_mfa_secret", None)
                        st.session_state.pop("security_mfa_code", None)
                        st.rerun()

        st.divider()

        # ─── API tokens sub-section ──────────────────────────────────────
        st.markdown(
            "<div><strong>API Tokens</strong></div>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Long-lived per-user secrets for scripts / CI to authenticate "
            "without your password. The raw token is shown ONCE at "
            "creation — copy it immediately."
        )

        try:
            tokens = list_tokens(user_id)
        except Exception as exc:
            logger.exception(
                f"Security panel: list_tokens failed: {exc}"
            )
            tokens = []

        with st.expander("New token", expanded=False):
            label = st.text_input(
                "Label (so you can tell tokens apart)",
                key="security_new_token_label",
                placeholder="e.g. CI bot, Personal laptop",
            )
            if st.button("Create", key="security_new_token_create"):
                if not label or not label.strip():
                    st.error("Label is required.")
                else:
                    result = create_token(user_id, label.strip())
                    if result is None:
                        st.error(
                            "Token creation failed — check the logs."
                        )
                    else:
                        _meta, raw_token = result
                        st.warning(
                            "Copy this token NOW. It will not be shown "
                            "again."
                        )
                        st.code(raw_token, language="text")
                        st.session_state.pop(
                            "security_new_token_label", None
                        )

        if not tokens:
            st.caption("No API tokens for this account yet.")
        else:
            for tok in tokens:
                col_label, col_created, col_used, col_action = st.columns(
                    [3, 3, 3, 2]
                )
                with col_label:
                    suffix = " (revoked)" if tok.revoked else ""
                    st.markdown(
                        f"<div>{_sans(tok.label + suffix, weight=600)}"
                        f"<br><span style='color:{C_TEXT3};font-size:11px'>"
                        f"prefix {tok.token_prefix}…</span></div>",
                        unsafe_allow_html=True,
                    )
                with col_created:
                    st.markdown(
                        f"<div style='color:{C_TEXT2};font-size:12px'>"
                        f"created<br>{tok.created_at[:19]}</div>",
                        unsafe_allow_html=True,
                    )
                with col_used:
                    last = tok.last_used_at or "never"
                    if last != "never":
                        last = last[:19]
                    st.markdown(
                        f"<div style='color:{C_TEXT2};font-size:12px'>"
                        f"last used<br>{last}</div>",
                        unsafe_allow_html=True,
                    )
                with col_action:
                    if tok.revoked:
                        st.caption("—")
                    else:
                        if st.button(
                            "Revoke",
                            key=f"security_token_revoke_{tok.token_id}",
                        ):
                            if revoke_token(
                                tok.token_id, user_id=user_id
                            ):
                                st.success(
                                    f"Revoked '{tok.label}'."
                                )
                                st.rerun()
                            else:
                                st.error(
                                    "Revoke failed — check the logs."
                                )

        # ─── Invitations sub-section (v21) ────────────────────────────────
        st.divider()
        st.markdown(
            "<div><strong>Invitations</strong></div>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Mint a signup link that pre-authorizes a specific email + "
            "role. The token is shown ONCE — share it out-of-band with "
            "the recipient. Use the URL template "
            "`/?invite=<token>` for sharing."
        )

        try:
            from auth.invitations import (
                create_invitation,
                list_invitations,
                revoke_invitation,
            )

            with st.expander("New invitation", expanded=False):
                inv_email = st.text_input(
                    "Email (optional — pins the invite to a specific recipient)",
                    key="security_new_invite_email",
                    placeholder="alice@example.com",
                )
                inv_role = st.selectbox(
                    "Role",
                    options=["user", "admin"],
                    index=0,
                    key="security_new_invite_role",
                    help=(
                        "Role granted on consumption. Pick 'admin' only "
                        "when you mean it — invites cannot silently grant "
                        "elevated access."
                    ),
                )
                inv_expires = st.number_input(
                    "Expires in (days)",
                    min_value=1,
                    max_value=90,
                    value=7,
                    step=1,
                    key="security_new_invite_expires",
                )
                if st.button("Create invitation", key="security_new_invite_create"):
                    invitation = create_invitation(
                        user_id,
                        email=(inv_email.strip() or None),
                        role=inv_role,
                        expires_in_days=int(inv_expires),
                    )
                    if invitation is None:
                        st.error(
                            "Invitation create failed — check the logs."
                        )
                    else:
                        st.warning(
                            "Copy this invitation URL NOW. The token "
                            "will not be shown again."
                        )
                        invite_url = f"/?invite={invitation.invite_token}"
                        st.code(invite_url, language="text")
                        # Clear the form inputs for the next mint.
                        st.session_state.pop(
                            "security_new_invite_email", None
                        )

            try:
                invitations = list_invitations(
                    invited_by_user_id=user_id,
                    include_consumed=False,
                )
            except Exception as exc:
                logger.exception(
                    f"Security panel: list_invitations failed: {exc}"
                )
                invitations = []

            if not invitations:
                st.caption("No pending invitations.")
            else:
                for inv in invitations:
                    inv_c1, inv_c2, inv_c3, inv_c4 = st.columns([3, 3, 3, 2])
                    with inv_c1:
                        label = inv.email or "(any email)"
                        st.markdown(
                            f"<div>{_sans(label, weight=600)}"
                            f"<br><span style='color:{C_TEXT3};font-size:11px'>"
                            f"role {inv.role}</span></div>",
                            unsafe_allow_html=True,
                        )
                    with inv_c2:
                        st.markdown(
                            f"<div style='color:{C_TEXT2};font-size:12px'>"
                            f"expires<br>{inv.expires_at[:19]}</div>",
                            unsafe_allow_html=True,
                        )
                    with inv_c3:
                        st.markdown(
                            f"<div style='color:{C_TEXT2};font-size:12px'>"
                            f"created<br>{inv.created_at[:19]}</div>",
                            unsafe_allow_html=True,
                        )
                    with inv_c4:
                        if st.button(
                            "Revoke",
                            key=f"security_invite_revoke_{inv.invite_id}",
                        ):
                            if revoke_invitation(inv.invite_id):
                                st.success("Invitation revoked.")
                                st.rerun()
                            else:
                                st.error(
                                    "Revoke failed — check the logs."
                                )
        except Exception as exc:
            logger.exception(
                f"Security panel: invitations sub-section error: {exc}"
            )
            st.caption("Invitations unavailable.")

    except Exception as exc:
        logger.exception(f"Security panel render error: {exc}")
        st.error("Security panel unavailable.")


def _render_mfa_panel(user_id: str) -> None:
    """Self-service Multi-factor Authentication setup panel.

    A dedicated, single-purpose panel for TOTP MFA enrollment. Branches
    on the current ``mfa_enabled`` flag:

      * **Disabled** → "Enable MFA" button mints a fresh secret (held in
        ``st.session_state.mfa_pending_secret``), shows it in monospace
        ``st.code`` plus a "Show as QR" expander, and gates the actual
        enable behind a TOTP-code verification step so a bad-QR-scan
        cannot lock the user out.
      * **Enabled** → "Disable MFA" plus a confirmation second click
        (``st.session_state.mfa_confirm_disable``) before we wipe the
        secret. Returns the account to password-only login.

    Sibling panel to ``_render_security_panel`` (which surfaces API
    tokens). All imports are lazy so the tab-load critical path stays
    auth-free. The whole helper is wrapped in try/except so an MFA DB
    outage cannot break the rest of Data Health.

    Args:
        user_id: The currently logged-in user's id. Caller is
                 responsible for sourcing this (typically
                 ``state.user_scope.current_user_id()``). An empty
                 ``user_id`` renders a "log in to manage" hint and
                 returns without raising.
    """
    try:
        from auth.mfa import (
            count_unused_recovery_codes,
            disable_mfa,
            enable_mfa,
            generate_secret,
            is_mfa_enabled,
            provisioning_uri,
            regenerate_recovery_codes,
            verify_totp,
        )

        section_divider("Multi-factor Authentication")

        if not isinstance(user_id, str) or not user_id:
            st.info("Log in to manage MFA.")
            return

        current_user = st.session_state.get("current_user")
        username = getattr(current_user, "username", "") or user_id

        # v21: if a previous enable_mfa call stashed recovery codes in
        # session_state (the one-shot reveal pattern), surface them
        # NOW in a copyable code block with a strong warning. We
        # ``pop`` the key after the render so a page reload does not
        # silently re-show codes the user may have already saved.
        pending_codes = st.session_state.pop("mfa_pending_codes", None)
        if pending_codes:
            st.warning(
                "Save these recovery codes NOW. Each is single-use; "
                "they let you sign in if you lose your authenticator. "
                "They will NEVER be shown again."
            )
            st.code("\n".join(pending_codes), language="text")

        if bool(is_mfa_enabled(user_id)):
            st.success("MFA active — required at next sign-in.")
            unused = int(count_unused_recovery_codes(user_id))
            st.caption(
                f"{unused} unused recovery codes remaining. Disabling "
                "MFA returns the account to password-only login and "
                "wipes the recovery codes. Re-enabling later requires "
                "fresh enrollment."
            )

            # ── Regenerate-codes sub-action (two-click confirm) ────────
            regen_confirm = bool(
                st.session_state.get("mfa_regen_confirm", False)
            )
            if not regen_confirm:
                if st.button(
                    "Regenerate recovery codes",
                    key="mfa_panel_regen_btn",
                    help=(
                        "Wipes the current batch and issues a fresh one. "
                        "The new codes are shown once."
                    ),
                ):
                    st.session_state["mfa_regen_confirm"] = True
                    st.rerun()
            else:
                st.warning(
                    "Click again to confirm — this WIPES every existing "
                    "recovery code and issues a fresh batch. Any saved "
                    "codes will stop working."
                )
                regen_col1, regen_col2 = st.columns([2, 2])
                with regen_col1:
                    if st.button(
                        "Confirm regenerate",
                        key="mfa_panel_regen_confirm_btn",
                    ):
                        try:
                            new_codes = regenerate_recovery_codes(user_id)
                            st.session_state.pop("mfa_regen_confirm", None)
                            if new_codes:
                                st.session_state["mfa_pending_codes"] = (
                                    new_codes
                                )
                                st.success(
                                    "Recovery codes regenerated."
                                )
                            else:
                                st.error(
                                    "Regenerate failed — check the logs."
                                )
                            st.rerun()
                        except Exception as exc:
                            logger.exception(
                                f"MFA panel regenerate failed: {exc}"
                            )
                            st.error(
                                "Regenerate failed — check the logs."
                            )
                with regen_col2:
                    if st.button(
                        "Cancel",
                        key="mfa_panel_regen_cancel_btn",
                    ):
                        st.session_state.pop("mfa_regen_confirm", None)
                        st.rerun()
            confirm = bool(st.session_state.get("mfa_confirm_disable", False))
            if not confirm:
                if st.button("Disable MFA", key="mfa_panel_disable_btn"):
                    st.session_state["mfa_confirm_disable"] = True
                    st.rerun()
            else:
                st.warning(
                    "Click again to confirm — this wipes the current "
                    "TOTP secret."
                )
                act_col1, act_col2 = st.columns([2, 2])
                with act_col1:
                    if st.button(
                        "Confirm disable",
                        key="mfa_panel_disable_confirm_btn",
                    ):
                        try:
                            if disable_mfa(user_id):
                                st.session_state.pop(
                                    "mfa_confirm_disable", None
                                )
                                st.warning("MFA disabled.")
                                st.rerun()
                            else:
                                st.error(
                                    "Disable failed — check the logs."
                                )
                        except Exception as exc:
                            logger.exception(
                                f"MFA panel disable failed: {exc}"
                            )
                            st.error("Disable failed — check the logs.")
                with act_col2:
                    if st.button(
                        "Cancel",
                        key="mfa_panel_disable_cancel_btn",
                    ):
                        st.session_state.pop("mfa_confirm_disable", None)
                        st.rerun()
            return

        # ─── Disabled branch ─────────────────────────────────────────────
        st.caption(
            "Add a 6-digit code from an authenticator app (Google "
            "Authenticator, 1Password, Authy, …) to your login. The "
            "second factor blunts password-leak risk: an attacker with "
            "your password still cannot sign in without the rotating "
            "code on your phone."
        )

        pending = st.session_state.get("mfa_pending_secret")
        if not pending:
            if st.button("Enable MFA", key="mfa_panel_enable_btn"):
                try:
                    st.session_state["mfa_pending_secret"] = generate_secret()
                    st.rerun()
                except Exception as exc:
                    logger.exception(f"MFA panel enable mint failed: {exc}")
                    st.error("Could not generate secret — check the logs.")
            return

        # Mid-enrollment: show the secret + optional QR + verify input.
        st.markdown(
            "<strong>Step 1.</strong> Add this secret to your "
            "authenticator app — either by scanning the QR code or by "
            "typing the raw secret manually.",
            unsafe_allow_html=True,
        )
        # Secret displayed via st.code (NOT st.write) — st.code preserves
        # exact whitespace and skips markdown parsing, so a stray ``*`` in
        # a base32 secret cannot accidentally italicize the display.
        st.code(pending, language="text")

        try:
            uri = provisioning_uri(
                pending,
                account=username,
                issuer="Ship Tracker",
            )
        except Exception as exc:
            logger.exception(f"MFA panel provisioning_uri failed: {exc}")
            uri = ""

        with st.expander("Show as QR", expanded=False):
            st.markdown(
                "<strong>Provisioning URI</strong> (paste into any QR "
                "generator, or import directly):",
                unsafe_allow_html=True,
            )
            st.code(uri or "(unavailable)", language="text")
            try:
                import qrcode  # type: ignore[import-not-found]
                import io as _io

                qr = qrcode.QRCode(box_size=6, border=2)
                qr.add_data(uri)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                buf = _io.BytesIO()
                img.save(buf, format="PNG")
                st.image(
                    buf.getvalue(),
                    caption="Scan with your authenticator app",
                )
            except ImportError:
                st.caption(
                    "QR rendering unavailable — the `qrcode` library "
                    "is not installed. Scan the URI text above with any "
                    "QR generator, or run `pip install qrcode[pil]` to "
                    "render inline. The raw secret above also works for "
                    "manual entry."
                )
            except Exception as exc:
                logger.exception(f"MFA panel QR render failed: {exc}")
                st.caption(
                    "QR rendering failed — use the URI text above."
                )

        st.markdown(
            "<strong>Step 2.</strong> Enter the current 6-digit code "
            "from the app to confirm the secret is correctly loaded.",
            unsafe_allow_html=True,
        )
        code = st.text_input(
            "6-digit code",
            max_chars=6,
            key="mfa_panel_code_input",
        )
        act_col1, act_col2 = st.columns([2, 2])
        with act_col1:
            if st.button(
                "Verify and enable",
                key="mfa_panel_verify_btn",
            ):
                try:
                    if verify_totp(pending, code or ""):
                        # v21: enable_mfa returns (ok, recovery_codes).
                        # Stash the codes in session_state so the next
                        # render lands in the enabled branch with
                        # ``mfa_pending_codes`` set, and the dedicated
                        # one-shot reveal block surfaces them ONCE.
                        ok, new_codes = enable_mfa(user_id, pending)
                        if ok:
                            st.session_state.pop("mfa_pending_secret", None)
                            st.session_state.pop("mfa_panel_code_input", None)
                            if new_codes:
                                st.session_state["mfa_pending_codes"] = (
                                    new_codes
                                )
                            st.success("MFA enabled.")
                            st.rerun()
                        else:
                            st.error("Enable failed — check the logs.")
                    else:
                        st.error("Code didn't verify — try again.")
                except Exception as exc:
                    logger.exception(f"MFA panel verify failed: {exc}")
                    st.error("Verification failed — check the logs.")
        with act_col2:
            if st.button("Cancel setup", key="mfa_panel_cancel_btn"):
                st.session_state.pop("mfa_pending_secret", None)
                st.session_state.pop("mfa_panel_code_input", None)
                st.rerun()

    except Exception as exc:
        logger.exception(f"MFA panel render error: {exc}")
        st.error("MFA panel unavailable.")


def _render_source_health() -> None:
    """Render the data-source health panel — periodic liveness checks
    against each external feed. Sourced from ``engine.source_health``."""
    try:
        from engine.source_health import get_health_summary, ping_all_sources

        section_divider("Data Source Health")

        col_a, col_b = st.columns([6, 2])
        with col_a:
            st.caption(
                "Periodic liveness probes against each external data feed. "
                "Status reflects the latest ping per source."
            )
        with col_b:
            run_now = st.button("Ping now", key="src_health_ping_now")

        if run_now:
            try:
                ping_all_sources()
                st.success("Probes complete — table refreshes on next render.")
            except Exception as exc:
                logger.exception(f"Source-health on-demand ping failed: {exc}")
                st.error(f"Ping failed: {exc}")

        try:
            summary = get_health_summary(window_hours=24)
        except Exception as exc:
            logger.exception(f"get_health_summary failed: {exc}")
            st.error("Couldn't load source-health summary.")
            return

        total = int(summary.get("total_pings", 0) or 0)
        by_source = summary.get("by_source", {}) or {}
        outages = summary.get("current_outages", []) or []

        # ─── 3 KPIs ────────────────────────────────────────────────────────
        sources_tracked = len(by_source)
        ok_count = sum(1 for s in by_source.values()
                       if s.get("last_status") == "up")
        outage_count = len(outages)

        metric_card_row(
            [
                {
                    "label": "Sources Tracked", "value": str(sources_tracked),
                    "accent": C_ACCENT,
                    "sublabel": f"{total:,} pings (24h)",
                },
                {
                    "label": "Currently OK", "value": str(ok_count),
                    "accent": C_HIGH if ok_count == sources_tracked and sources_tracked > 0 else C_MOD,
                    "sublabel": "latest ping = up",
                },
                {
                    "label": "Active Outages", "value": str(outage_count),
                    "accent": C_LOW if outage_count > 0 else C_HIGH,
                    "sublabel": ", ".join(outages[:3]) if outages else "all clear",
                },
            ],
            columns=3,
        )

        # ─── by-source table ──────────────────────────────────────────────
        if not by_source:
            st.info("No source health data yet — run a ping to populate.")
            return

        # Sort by last_status (down → degraded → up) then alphabetically
        _status_order = {"down": 0, "degraded": 1, "up": 2}
        rows_sorted = sorted(
            by_source.items(),
            key=lambda kv: (_status_order.get(kv[1].get("last_status", ""), 9), kv[0]),
        )

        rows = []
        for source, stats in rows_sorted:
            status = stats.get("last_status", "—")
            badge_color = (
                C_HIGH if status == "up"
                else C_MOD if status == "degraded"
                else C_LOW if status == "down"
                else C_TEXT3
            )
            count = int(stats.get("count", 0))
            up = int(stats.get("up_count", 0))
            down = int(stats.get("down_count", 0))
            avg_ms = float(stats.get("avg_duration_ms", 0.0))
            last_at = str(stats.get("last_started_at", ""))[:19].replace("T", " ")

            rows.append([
                source,
                f"<span style='color:{badge_color};font-weight:600'>{status.upper()}</span>",
                f"{up}/{count}",
                str(down),
                f"{avg_ms:,.0f} ms" if avg_ms < 1000 else f"{avg_ms/1000:.2f} s",
                last_at,
            ])

        st.markdown(
            wsj_market_table(
                headers=["Source", "Status", "Up / Total", "Down", "Avg Latency", "Last Ping"],
                rows=rows,
                title="Per-source health (last 24h)",
            ),
            unsafe_allow_html=True,
        )

        _render_source_health_alert_config()

    except Exception as exc:
        logger.exception(f"Source health panel render error: {exc}")
        st.error("Source health panel unavailable.")


def _render_source_health_alert_config() -> None:
    """Render the auto-alerting sub-expander inside the source-health panel.

    Lets the operator toggle whether auto-alerting is on, tune the
    red/yellow staleness thresholds + per-source cooldown, and see how
    many auto-alerts have fired in the last hour. Everything is
    persisted via ``engine.source_health_alerts.save_config`` so the
    next scheduler tick picks up the change without a restart.
    """
    try:
        from engine.source_health_alerts import (
            SourceHealthAlertConfig,
            get_recent_fire_count,
            load_config,
            save_config,
        )

        cfg = load_config()
        recent = get_recent_fire_count()
        status_word = "ENABLED" if cfg.enabled else "DISABLED"
        status_color = C_HIGH if cfg.enabled else C_TEXT3

        st.markdown(
            f'<div style="font-size:0.78rem;color:{C_TEXT2};margin:8px 0">'
            f'Auto-alerting: <b style="color:{status_color}">{status_word}</b>'
            f' — fired <b style="color:{C_TEXT}">{recent}</b> alert(s) in the last hour.'
            f'</div>',
            unsafe_allow_html=True,
        )

        with st.expander("Auto-alerting settings", expanded=False):
            st.caption(
                "When enabled, the scheduler auto-fires CRITICAL/HIGH "
                "ShippingAlerts for sources whose latest ping is "
                "missing, degraded, or stale beyond the thresholds "
                "below. Per-source cooldown stops a flapping feed "
                "from filling the alert table."
            )

            enabled = st.checkbox(
                "Auto-alerting enabled",
                value=bool(cfg.enabled),
                key="src_health_alert_enabled",
            )
            c1, c2, c3 = st.columns(3, gap="small")
            with c1:
                red = st.number_input(
                    "Red threshold (minutes)",
                    min_value=1,
                    max_value=10080,  # one week
                    value=int(cfg.red_threshold_minutes),
                    step=5,
                    help="Stale beyond this → CRITICAL.",
                    key="src_health_alert_red",
                )
            with c2:
                yellow = st.number_input(
                    "Yellow threshold (minutes)",
                    min_value=0,
                    max_value=10080,
                    value=int(cfg.yellow_threshold_minutes),
                    step=5,
                    help="Stale beyond this (but under Red) → HIGH.",
                    key="src_health_alert_yellow",
                )
            with c3:
                cooldown = st.number_input(
                    "Cooldown (minutes)",
                    min_value=0,
                    max_value=10080,
                    value=int(cfg.cooldown_minutes),
                    step=5,
                    help="Minimum gap between two fires for the same source.",
                    key="src_health_alert_cooldown",
                )

            if st.button("Save", key="src_health_alert_save"):
                new_cfg = SourceHealthAlertConfig(
                    enabled=bool(enabled),
                    red_threshold_minutes=int(red),
                    yellow_threshold_minutes=int(yellow),
                    cooldown_minutes=int(cooldown),
                )
                ok = save_config(new_cfg)
                if ok:
                    st.success("Settings saved. Scheduler will pick them up on the next tick.")
                else:
                    st.error("Couldn't persist settings — check the log.")

    except Exception as exc:
        logger.exception(f"Source-health alert config render error: {exc}")
        st.caption("Auto-alerting controls unavailable.")


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


def _render_delivery_retry_panel() -> None:
    """Render the delivery retry queue panel — failed dispatches awaiting retry.

    Shows three sections per current user: Pending (with per-row
    Retry/Cancel buttons), Recently failed (after MAX_RETRIES
    exhaustion), Recently succeeded. Whole panel try/except + lazy
    imports so a retry-table outage cannot break Data Health.
    """
    try:
        from engine.delivery_retry import (
            cancel_retry,
            cleanup_completed,
            list_failed,
            list_pending,
            list_succeeded_recent,
            manual_retry,
        )
        from state.user_scope import current_user_id

        section_divider("Delivery Retry Queue")
        uid = current_user_id()
        if not uid:
            st.info("Log in to see your retry queue.")
            return

        pending = list_pending(user_id=uid, limit=50)
        failed = list_failed(user_id=uid, limit=20)
        succeeded = list_succeeded_recent(user_id=uid, limit=10)

        st.caption(
            f"Pending: {len(pending)} · Failed (exhausted): {len(failed)} · "
            f"Recently succeeded: {len(succeeded)}"
        )

        # Pending — actionable
        if pending:
            st.markdown("**Pending retries**")
            for entry in pending:
                cols = st.columns([2, 2, 1, 1, 2, 1, 1])
                cols[0].write(f"`{entry.alert_id[:8]}`")
                cols[1].write(f"`{entry.channel_id}`")
                cols[2].write(f"#{entry.attempt_count}")
                cols[3].write((entry.last_error or "")[:24])
                cols[4].write(entry.next_attempt_at[:19])
                if cols[5].button("Retry", key=f"rqr_{entry.queue_id}"):
                    try:
                        manual_retry(entry.queue_id, user_id=uid)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Retry failed: {exc}")
                if cols[6].button("Cancel", key=f"rqc_{entry.queue_id}"):
                    try:
                        cancel_retry(entry.queue_id, user_id=uid)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Cancel failed: {exc}")
        else:
            st.caption("No pending retries.")

        # Recently failed — read-only
        if failed:
            with st.expander(f"Recently failed ({len(failed)})"):
                import pandas as pd
                df = pd.DataFrame([
                    {
                        "alert_id": e.alert_id[:8],
                        "channel_id": e.channel_id,
                        "attempts": e.attempt_count,
                        "last_error": (e.last_error or "")[:48],
                        "final_at": (e.final_at or "")[:19],
                    } for e in failed
                ])
                st.dataframe(df, use_container_width=True, hide_index=True)

        # Recently succeeded — read-only
        if succeeded:
            with st.expander(f"Recently succeeded ({len(succeeded)})"):
                import pandas as pd
                df = pd.DataFrame([
                    {
                        "alert_id": e.alert_id[:8],
                        "channel_id": e.channel_id,
                        "attempts": e.attempt_count + 1,  # +1 for the success
                        "final_at": (e.final_at or "")[:19],
                    } for e in succeeded
                ])
                st.dataframe(df, use_container_width=True, hide_index=True)

        # Maintenance
        with st.expander("Maintenance"):
            if st.button("Cleanup completed > 14d old", key="rq_cleanup"):
                try:
                    deleted = cleanup_completed(retention_days=14)
                    st.success(f"Deleted {deleted} old completed rows.")
                except Exception as exc:
                    st.error(f"Cleanup failed: {exc}")
    except Exception as exc:
        logger.exception(f"_render_delivery_retry_panel: {exc}")
        st.warning("Delivery retry panel unavailable.")


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

            # ── Movement 1.685: audit log search ───────────────────────────────
            try:
                _render_audit_search_panel()
            except Exception as exc:
                logger.error(f"Audit search panel render error: {exc}")
                st.error("Audit search panel unavailable.")

            # ── Movement 1.69: data source health ──────────────────────────────
            try:
                _render_source_health()
            except Exception as exc:
                logger.error(f"Source health render error: {exc}")
                st.error("Source health panel unavailable.")

            # ── Movement 1.695: secrets vault ──────────────────────────────────
            try:
                _render_vault_panel()
            except Exception as exc:
                logger.error(f"Vault panel render error: {exc}")
                st.error("Vault panel unavailable.")

            # ── Movement 1.696: per-user security (MFA + API tokens) ───────────
            try:
                _render_security_panel()
            except Exception as exc:
                logger.error(f"Security panel render error: {exc}")
                st.error("Security panel unavailable.")

            # ── Movement 1.697: dedicated MFA self-service panel ───────────────
            try:
                from state.user_scope import current_user_id
                _render_mfa_panel(current_user_id())
            except Exception as exc:
                logger.error(f"MFA panel render error: {exc}")
                st.error("MFA panel unavailable.")

            # ── Movement 1.698: delivery retry queue ───────────────────────────
            try:
                _render_delivery_retry_panel()
            except Exception as exc:
                logger.error(f"Delivery retry panel render error: {exc}")
                st.error("Delivery retry panel unavailable.")

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
