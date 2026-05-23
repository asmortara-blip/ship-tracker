"""tab_alerts.py — Alert Center: threshold monitoring, rule management, and
notification routing for the Ship Tracker intelligence platform.

Sections
--------
0. Hero            — total alerts, active rules, last triggered timestamp
1. Configure       — form to create new alert rules
2. Active Alerts   — triggered alerts derived from live data
3. History         — last 20 alerts (mock + session state)
4. Notifications   — email, frequency, digest toggle
5. Rules Manager   — table of all configured rules with toggles

Active filter payload (``st.session_state.active_filter_payload``)
------------------------------------------------------------------
The Saved Filters panel persists an operator-chosen filter as a dict
in ``st.session_state.active_filter_payload``. The dict may be ``None``
or ``{}`` (no filter active). For this commit, the downstream consumers
(incidents panel, ack-analytics panel, main alert table) honor the
following standard keys via ``_apply_active_filter``:

  * ``severity`` (str) — one of ``CRITICAL`` / ``HIGH`` / ``MEDIUM`` /
    ``LOW``. Keeps alerts whose severity is AT LEAST as severe (i.e.
    ``severity=HIGH`` keeps both HIGH and CRITICAL).
  * ``window_hours`` (int) — keeps alerts created within the last N
    hours (uses ``created_at`` / ``triggered_at`` ISO timestamp).
  * ``ticker`` (str) — exact match against the alert's ticker field.
  * ``alert_type`` (str) — one of ``BDI_MOVE`` / ``MACRO`` / ``EVENT``
    / ``RATE_SURGE`` / ``STOCK_MOVE`` / ``CONGESTION`` / etc. Exact
    string match.
  * ``acknowledged`` (bool) — ``True`` keeps only ack'd rows; ``False``
    keeps only un-ack'd rows.

Unrecognized keys are silently ignored — operators may shove arbitrary
keys in via the API and the panel must remain forward-compatible.
Malformed values (a bad severity string, a non-int window, etc.) drop
the offending predicate silently; the rest of the filter still applies.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import streamlit as st
from loguru import logger

from data.quality import DataKind, DataQuality, DataSource
from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    badge,
    live_data_badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    wsj_market_table,
)


# ---------------------------------------------------------------------------
# Domain-specific mappings (kept local — not part of the shared palette)
# ---------------------------------------------------------------------------
_SEV_COLOR: dict[str, str] = {
    "Critical": C_LOW,
    "Warning":  C_MOD,
    "Info":     C_ACCENT,
}

_SEV_BADGE: dict[str, str] = {
    "Critical": C_LOW,
    "Warning":  C_MOD,
    "Info":     C_ACCENT,
}

_METRIC_ICONS: dict[str, str] = {
    "Freight Rate":     "FR",
    "Sentiment Score":  "SENT",
    "Stock Price":      "EQ",
    "Port Congestion":  "PORT",
    "Macro Indicator":  "MAC",
}


# ---------------------------------------------------------------------------
# Active-filter helper — consumes ``st.session_state.active_filter_payload``
# (set by the Saved Filters panel) and returns the filtered alerts list.
# Pure function, no Streamlit imports → trivially unit-testable. See the
# module docstring for the supported payload keys.
# ---------------------------------------------------------------------------
_FILTER_SEVERITY_RANK: dict[str, int] = {
    "CRITICAL": 4,
    "HIGH":     3,
    "MEDIUM":   2,
    "LOW":      1,
}


def _alert_field(alert: Any, name: str, default: Any = None) -> Any:
    """Return ``alert[name]`` for dicts or ``getattr(alert, name, default)``
    for dataclass instances. Lets one helper handle both the dict-based
    active-alerts shape AND the ShippingAlert dataclass without branching
    at every call site.
    """
    try:
        if isinstance(alert, dict):
            return alert.get(name, default)
        return getattr(alert, name, default)
    except Exception:
        return default


def _apply_active_filter(
    alerts: list, payload: dict | None
) -> list:
    """Return ``alerts`` filtered by ``payload`` — pure function.

    Each predicate is wrapped in its own try/except: a malformed value
    for one key drops THAT predicate, the rest of the filter still
    applies. Unrecognized keys are silently ignored (forward-compat).
    A ``None`` or empty payload returns the input list unchanged.

    Severity uses the ranking ``CRITICAL=4, HIGH=3, MEDIUM=2, LOW=1``;
    a row with rank ≥ payload-rank passes (i.e. ``severity=HIGH`` keeps
    HIGH and CRITICAL). Severity strings that don't appear in the
    ranking are treated as rank 0 — they fail unless the filter itself
    is below LOW (it can't be, so unknown severities are filtered out
    when a severity predicate is active). When the operator's chosen
    severity is itself unknown, the predicate silently drops.

    ``window_hours`` filters by ``created_at`` (ShippingAlert) or
    ``triggered_at`` (dict-based active-alerts) — whichever is present.
    Rows with no parseable timestamp are dropped when the predicate is
    active (a row with no timestamp can't be proven "within" the window).

    The helper is intentionally permissive about the alert shape so
    every call site (incidents/correlator, ack analytics, main table)
    can pass whichever list it already has.
    """
    if not alerts:
        return list(alerts) if alerts is not None else []
    if not payload or not isinstance(payload, dict):
        return list(alerts)

    out = list(alerts)

    # ── severity (>= rank) ─────────────────────────────────────────────────
    try:
        if "severity" in payload:
            min_rank = _FILTER_SEVERITY_RANK.get(
                str(payload["severity"]).upper()
            )
            if min_rank is not None:
                out = [
                    a for a in out
                    if _FILTER_SEVERITY_RANK.get(
                        str(_alert_field(a, "severity", "")).upper(), 0
                    ) >= min_rank
                ]
            # Unknown severity string (e.g. "BANANA") → silently drop the
            # predicate, leave the list as-is.
    except Exception:
        logger.exception("active_filter: severity predicate failed; dropping")

    # ── window_hours (recent N hours) ──────────────────────────────────────
    try:
        if "window_hours" in payload:
            hours = int(payload["window_hours"])
            if hours > 0:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
                kept = []
                for a in out:
                    ts_raw = (
                        _alert_field(a, "created_at", "")
                        or _alert_field(a, "triggered_at", "")
                    )
                    if not ts_raw:
                        continue
                    try:
                        dt = datetime.fromisoformat(
                            str(ts_raw).replace("Z", "+00:00")
                        )
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt >= cutoff:
                            kept.append(a)
                    except (ValueError, TypeError):
                        # Unparseable timestamp → cannot prove "within"
                        # the window; drop the row.
                        continue
                out = kept
    except Exception:
        logger.exception("active_filter: window_hours predicate failed; dropping")

    # ── ticker (exact match) ───────────────────────────────────────────────
    try:
        if "ticker" in payload:
            target = str(payload["ticker"])
            out = [a for a in out if str(_alert_field(a, "ticker", "")) == target]
    except Exception:
        logger.exception("active_filter: ticker predicate failed; dropping")

    # ── alert_type (exact match) ───────────────────────────────────────────
    try:
        if "alert_type" in payload:
            target = str(payload["alert_type"])
            out = [
                a for a in out
                if str(_alert_field(a, "alert_type", "")) == target
            ]
    except Exception:
        logger.exception("active_filter: alert_type predicate failed; dropping")

    # ── acknowledged (bool) ────────────────────────────────────────────────
    try:
        if "acknowledged" in payload:
            want = bool(payload["acknowledged"])
            out = [
                a for a in out
                if bool(_alert_field(a, "acknowledged", False)) == want
            ]
    except Exception:
        logger.exception("active_filter: acknowledged predicate failed; dropping")

    return out


def _metrics_from_alerts(alerts: list) -> Any:
    """Compute an ``AlertMetrics`` shape from an in-memory alerts list.

    Mirrors the aggregation in ``engine.alert_analytics.compute_alert_metrics``
    so the filtered ack-analytics panel can render the same KPI strip,
    severity-breakdown, daily-volume chart, and median-time-to-ack KPI
    without touching the engine module.

    Imports ``AlertMetrics`` lazily and returns the lazily-imported type
    so callers don't take the engine import unless they actually need
    filtered metrics. Never raises — on any error returns an empty
    ``AlertMetrics``.
    """
    try:
        import statistics

        from engine.alert_analytics import AlertMetrics

        if not alerts:
            return AlertMetrics()

        total = len(alerts)
        ack_count = sum(
            1 for a in alerts if bool(getattr(a, "acknowledged", False))
        )
        unack_count = total - ack_count
        ack_rate = (ack_count / total) if total > 0 else 0.0

        by_severity: dict[str, dict[str, Any]] = {}
        for a in alerts:
            sev = str(getattr(a, "severity", "")).upper() or "UNKNOWN"
            bucket = by_severity.setdefault(
                sev, {"total": 0, "ack_count": 0, "ack_rate": 0.0}
            )
            bucket["total"] += 1
            if bool(getattr(a, "acknowledged", False)):
                bucket["ack_count"] += 1
        for sev, stats in by_severity.items():
            t = stats["total"]
            stats["ack_rate"] = (stats["ack_count"] / t) if t > 0 else 0.0

        # Daily volume — group by YYYY-MM-DD prefix of created_at.
        by_day_dict: dict[str, dict[str, int]] = {}
        for a in alerts:
            ts = str(getattr(a, "created_at", ""))
            if not ts or len(ts) < 10:
                continue
            day = ts[:10]
            d = by_day_dict.setdefault(day, {"total": 0, "ack_count": 0})
            d["total"] += 1
            if bool(getattr(a, "acknowledged", False)):
                d["ack_count"] += 1
        by_day = [
            {"date": day, "total": d["total"], "ack_count": d["ack_count"]}
            for day, d in sorted(by_day_dict.items())
        ]

        # Median time-to-ack — only acked rows with both timestamps.
        durations: list[float] = []
        for a in alerts:
            if not bool(getattr(a, "acknowledged", False)):
                continue
            t0_raw = str(getattr(a, "created_at", "") or "")
            t1_raw = str(getattr(a, "acknowledged_at", "") or "")
            if not t0_raw or not t1_raw:
                continue
            try:
                t0 = datetime.fromisoformat(t0_raw.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(t1_raw.replace("Z", "+00:00"))
                durations.append((t1 - t0).total_seconds() / 3600.0)
            except (ValueError, TypeError):
                continue
        median_hours = (
            float(statistics.median(durations)) if durations else None
        )

        return AlertMetrics(
            total_alerts=total,
            acknowledged_count=ack_count,
            unacknowledged_count=unack_count,
            ack_rate=ack_rate,
            by_severity=by_severity,
            median_time_to_ack_hours=median_hours,
            by_day=by_day,
        )
    except Exception:
        logger.exception("metrics_from_alerts failed")
        try:
            from engine.alert_analytics import AlertMetrics
            return AlertMetrics()
        except Exception:
            # Final fallback — return an object that mimics the
            # zero-valued AlertMetrics so the render below never crashes.
            class _Empty:
                total_alerts = 0
                acknowledged_count = 0
                unacknowledged_count = 0
                ack_rate = 0.0
                by_severity: dict = {}
                median_time_to_ack_hours = None
                by_day: list = []
            return _Empty()


# ---------------------------------------------------------------------------
# Data source declarations
# ---------------------------------------------------------------------------
_ACTIVE_SRC  = DataSource.modeled(
    "Alert Engine",
    notes="Derived from live freight, sentiment, stock, and macro feeds",
)
_HISTORY_SRC = DataSource.demo("Alert History")
_RULES_SRC   = DataSource(
    name="User Rules",
    kind=DataKind.MANUAL,
    quality=DataQuality.GOOD,
    notes="User-configured rule set",
)


# ---------------------------------------------------------------------------
# Cell formatters for WSJ market tables
# ---------------------------------------------------------------------------
def _sans(value: str, color: str = C_TEXT, weight: int = 600) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};font-weight:{weight};">'
        f'{value}</span>'
    )


def _mono(value: str, color: str = C_TEXT, weight: int = 600) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};font-weight:{weight};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sev_badge(sev: str) -> str:
    return badge(sev, _SEV_BADGE.get(sev, C_ACCENT))


# ---------------------------------------------------------------------------
# Date / time helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_dt(iso: str) -> str:
    try:
        return iso[:16].replace("T", " ") + " UTC"
    except Exception:
        return iso


def _time_ago(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Default rules & mock history
# ---------------------------------------------------------------------------
def _default_rules() -> list[dict]:
    return [
        {
            "id": "rule_freight_spike",
            "name": "Freight Rate Spike",
            "metric": "Freight Rate",
            "threshold": 20.0,
            "condition": "Above",
            "severity": "Critical",
            "email_notify": True,
            "enabled": True,
        },
        {
            "id": "rule_sentiment_shift",
            "name": "Bearish Sentiment Signal",
            "metric": "Sentiment Score",
            "threshold": -0.5,
            "condition": "Below",
            "severity": "Warning",
            "email_notify": False,
            "enabled": True,
        },
        {
            "id": "rule_stock_drop",
            "name": "Shipping Stock Move >5%",
            "metric": "Stock Price",
            "threshold": 5.0,
            "condition": "Above",
            "severity": "Warning",
            "email_notify": True,
            "enabled": True,
        },
        {
            "id": "rule_port_congestion",
            "name": "Port Congestion Critical",
            "metric": "Port Congestion",
            "threshold": 0.80,
            "condition": "Above",
            "severity": "Warning",
            "email_notify": False,
            "enabled": True,
        },
        {
            "id": "rule_macro_flag",
            "name": "Macro Risk Indicator",
            "metric": "Macro Indicator",
            "threshold": 0.0,
            "condition": "Above",
            "severity": "Info",
            "email_notify": False,
            "enabled": False,
        },
    ]


def _mock_history() -> list[dict]:
    base = datetime.now(timezone.utc)
    return [
        {
            "name": "SCFI Spot Surge +23%",
            "metric": "Freight Rate",
            "severity": "Critical",
            "description": "Shanghai Containerized Freight Index weekly change exceeded 20% threshold.",
            "current_value": "+23.1%",
            "threshold": "+20.0%",
            "triggered_at": (base - timedelta(hours=2, minutes=14)).isoformat(),
            "dismissed": False,
        },
        {
            "name": "ZIM Daily Move -6.2%",
            "metric": "Stock Price",
            "severity": "Warning",
            "description": "ZIM Integrated Shipping (ZIM) fell 6.2% intraday, exceeding the 5% alert threshold.",
            "current_value": "-6.2%",
            "threshold": "+/-5.0%",
            "triggered_at": (base - timedelta(hours=5, minutes=40)).isoformat(),
            "dismissed": False,
        },
        {
            "name": "Shanghai Port Congestion High",
            "metric": "Port Congestion",
            "severity": "Warning",
            "description": "CNSHA congestion score reached 0.87, above the 0.80 configured threshold.",
            "current_value": "0.87",
            "threshold": "0.80",
            "triggered_at": (base - timedelta(hours=8)).isoformat(),
            "dismissed": True,
        },
        {
            "name": "Bearish Sentiment Cluster",
            "metric": "Sentiment Score",
            "severity": "Warning",
            "description": "Three consecutive bearish signals detected on Asia-Europe corridor.",
            "current_value": "-0.61",
            "threshold": "-0.50",
            "triggered_at": (base - timedelta(hours=11, minutes=5)).isoformat(),
            "dismissed": True,
        },
        {
            "name": "Baltic Dry Index Drop -8%",
            "metric": "Macro Indicator",
            "severity": "Info",
            "description": "BDI declined 8% week-over-week, signalling softening dry bulk demand.",
            "current_value": "1,843",
            "threshold": "BDI delta >5%",
            "triggered_at": (base - timedelta(days=1, hours=3)).isoformat(),
            "dismissed": True,
        },
        {
            "name": "MATX Equity Move +5.8%",
            "metric": "Stock Price",
            "severity": "Warning",
            "description": "Matson Inc. (MATX) rallied 5.8% on stronger-than-expected volume guidance.",
            "current_value": "+5.8%",
            "threshold": "+/-5.0%",
            "triggered_at": (base - timedelta(days=1, hours=9, minutes=22)).isoformat(),
            "dismissed": True,
        },
        {
            "name": "Trans-Pacific Rate Surge +18%",
            "metric": "Freight Rate",
            "severity": "Warning",
            "description": "Weekly spot rate on USWC lane up 18%, approaching critical threshold.",
            "current_value": "+18.4%",
            "threshold": "+20.0%",
            "triggered_at": (base - timedelta(days=2, hours=6)).isoformat(),
            "dismissed": True,
        },
        {
            "name": "Bullish Sentiment Breakout",
            "metric": "Sentiment Score",
            "severity": "Info",
            "description": "Sentiment model flipped strongly bullish on Transpacific Eastbound route.",
            "current_value": "+0.72",
            "threshold": "+0.50",
            "triggered_at": (base - timedelta(days=3, hours=1, minutes=45)).isoformat(),
            "dismissed": True,
        },
        {
            "name": "Los Angeles Port Congestion",
            "metric": "Port Congestion",
            "severity": "Critical",
            "description": "USLAX congestion index reached 0.93 - severe vessel queue buildup.",
            "current_value": "0.93",
            "threshold": "0.80",
            "triggered_at": (base - timedelta(days=4, hours=14)).isoformat(),
            "dismissed": True,
        },
        {
            "name": "SBLK Daily Drop -7.1%",
            "metric": "Stock Price",
            "severity": "Warning",
            "description": "Star Bulk Carriers (SBLK) fell 7.1% following BDI weakness.",
            "current_value": "-7.1%",
            "threshold": "+/-5.0%",
            "triggered_at": (base - timedelta(days=5, hours=8, minutes=30)).isoformat(),
            "dismissed": True,
        },
    ]


# ---------------------------------------------------------------------------
# Live alert scanner
# ---------------------------------------------------------------------------
def _scan_triggered_alerts(freight_data, insights, stock_data, macro_data) -> list[dict]:
    """Derive triggered alerts from live data; never raises."""
    triggered: list[dict] = []
    now = _now_iso()

    # Freight rate spike check (>20% weekly)
    try:
        if freight_data:
            for route, data in (freight_data if isinstance(freight_data, dict) else {}).items():
                try:
                    chg = None
                    if isinstance(data, dict):
                        chg = data.get("weekly_change") or data.get("pct_change_7d")
                    elif hasattr(data, "weekly_change"):
                        chg = data.weekly_change
                    if chg is not None and abs(float(chg)) > 20:
                        direction = "surge" if float(chg) > 0 else "drop"
                        triggered.append({
                            "name": f"Freight Rate {direction.title()} - {route}",
                            "metric": "Freight Rate",
                            "severity": "Critical",
                            "description": (
                                f"Weekly rate change on {route} reached {float(chg):+.1f}%, "
                                "exceeding the 20% threshold."
                            ),
                            "current_value": f"{float(chg):+.1f}%",
                            "threshold": "+/-20.0%",
                            "triggered_at": now,
                            "dismissed": False,
                        })
                except Exception:
                    pass
    except Exception:
        pass

    # Sentiment shift check
    try:
        insight_list = insights if isinstance(insights, list) else []
        for ins in insight_list[:20]:
            try:
                sig = None
                if isinstance(ins, dict):
                    sig = ins.get("signal") or ins.get("direction")
                elif hasattr(ins, "signal"):
                    sig = ins.signal
                elif hasattr(ins, "direction"):
                    sig = ins.direction
                if sig and str(sig).upper() in ("BEARISH", "STRONG_BEARISH"):
                    route_label = (
                        ins.get("route_id", "") if isinstance(ins, dict)
                        else getattr(ins, "route_id", "")
                    )
                    triggered.append({
                        "name": f"Bearish Sentiment - {route_label or 'Market'}",
                        "metric": "Sentiment Score",
                        "severity": "Warning",
                        "description": (
                            "Sentiment model flagged a strong bearish signal"
                            f"{f' on {route_label}' if route_label else ''}. "
                            "Monitor closely for rate deterioration."
                        ),
                        "current_value": str(sig),
                        "threshold": "Bearish threshold",
                        "triggered_at": now,
                        "dismissed": False,
                    })
                    if len(triggered) >= 4:
                        break
            except Exception:
                pass
    except Exception:
        pass

    # Stock price move check (>5%)
    try:
        sd = stock_data if isinstance(stock_data, dict) else {}
        for ticker, data in sd.items():
            try:
                chg = None
                if isinstance(data, dict):
                    chg = data.get("daily_change") or data.get("pct_change_1d") or data.get("change_pct")
                elif isinstance(data, (int, float)):
                    chg = data
                if chg is not None and abs(float(chg)) > 5:
                    direction = "rallied" if float(chg) > 0 else "dropped"
                    triggered.append({
                        "name": f"{ticker} {direction.title()} {float(chg):+.1f}%",
                        "metric": "Stock Price",
                        "severity": "Warning",
                        "description": (
                            f"{ticker} {direction} {abs(float(chg)):.1f}% intraday, "
                            "exceeding the 5% daily move threshold."
                        ),
                        "current_value": f"{float(chg):+.1f}%",
                        "threshold": "+/-5.0%",
                        "triggered_at": now,
                        "dismissed": False,
                    })
            except Exception:
                pass
    except Exception:
        pass

    # Macro data check (BDI / key indicators)
    try:
        md = macro_data if isinstance(macro_data, dict) else {}
        bdi_chg = md.get("bdi_weekly_change") or md.get("bdi_pct_change")
        if bdi_chg is not None and abs(float(bdi_chg)) > 8:
            triggered.append({
                "name": f"Baltic Dry Index Move {float(bdi_chg):+.1f}%",
                "metric": "Macro Indicator",
                "severity": "Info" if abs(float(bdi_chg)) < 12 else "Warning",
                "description": (
                    f"BDI moved {float(bdi_chg):+.1f}% week-over-week. "
                    "Watch for downstream freight rate implications."
                ),
                "current_value": f"{float(bdi_chg):+.1f}%",
                "threshold": "+/-8.0%",
                "triggered_at": now,
                "dismissed": False,
            })
    except Exception:
        pass

    return triggered


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
def _init_state() -> None:
    if "user_alerts" not in st.session_state:
        # Prefer persisted rules; fall back to defaults on first use.
        try:
            from engine.alert_engine_v2 import load_rules
            persisted = load_rules()
        except Exception:
            persisted = []
        st.session_state["user_alerts"] = persisted if persisted else _default_rules()
    if "alert_dismissed" not in st.session_state:
        st.session_state["alert_dismissed"] = set()
    if "alert_history" not in st.session_state:
        st.session_state["alert_history"] = _mock_history()
    if "notif_email" not in st.session_state:
        st.session_state["notif_email"] = ""
    if "notif_freq" not in st.session_state:
        st.session_state["notif_freq"] = "Immediate"
    if "notif_digest" not in st.session_state:
        st.session_state["notif_digest"] = False
    if "active_alerts_cache" not in st.session_state:
        st.session_state["active_alerts_cache"] = []


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------
def _render_hero(visible_alerts: list[dict]) -> None:
    try:
        page_header(
            title="Alert Center",
            subtitle="Real-time threshold monitoring and notifications",
            icon="AL",
            badge_text="LIVE",
            badge_color=C_HIGH,
        )

        rules          = st.session_state.get("user_alerts", [])
        active_count   = len(visible_alerts)
        rules_count    = sum(1 for r in rules if r.get("enabled", True))
        history        = st.session_state.get("alert_history", [])
        last_triggered = history[0].get("triggered_at", "") if history else ""

        last_ago = _time_ago(last_triggered) if last_triggered else "--"
        last_sub = _fmt_dt(last_triggered) if last_triggered else "No history yet"

        metric_card_row([
            {
                "label":    "Active Alerts",
                "value":    str(active_count),
                "accent":   C_LOW if active_count > 0 else C_HIGH,
                "sublabel": "Requires attention" if active_count else "All clear",
            },
            {
                "label":    "Active Rules",
                "value":    str(rules_count),
                "accent":   C_ACCENT,
                "sublabel": f"of {len(rules)} configured",
            },
            {
                "label":    "Last Triggered",
                "value":    last_ago,
                "accent":   C_MOD,
                "sublabel": last_sub,
            },
        ], columns=3)
    except Exception:
        logger.exception("Alert Center hero render failed")
        st.error("Hero section unavailable.")


def _render_saved_filters() -> None:
    """Render the Saved Filters panel for the Alert Center.

    Operators frequently re-apply the same filter combinations ("all
    CRITICAL last 24h", "transpacific only", "my watchlist"). This panel
    lets them name, recall, and delete those combinations via
    ``state.user_filters``.

    Layout:
      * Section header.
      * Three-column row: preset picker, Load button, Delete button.
      * Expander to save the currently-active payload under a new name.
      * Small caption summarising the active filter.

    The active payload is persisted in ``st.session_state.active_filter_payload``
    so downstream panels (incidents, ack analytics, alert table) can read
    it and decide what to honor — that integration is a follow-up; this
    commit only manages persistence and display.

    Wrapped in try/except + logger.exception so a presets-store failure
    never breaks the rest of the Alert Center tab.
    """
    try:
        from state.user_filters import (
            FilterPreset,
            delete_preset,
            get_preset,
            load_presets,
            save_preset,
        )

        section_divider("Saved Filters")

        presets = load_presets(scope="alerts") or []
        preset_names = sorted(p.name for p in presets)
        options = ["(none)"] + preset_names

        col_pick, col_load, col_delete = st.columns([3, 1, 1])
        with col_pick:
            chosen = st.selectbox(
                "Saved preset",
                options=options,
                index=0,
                key="saved_filter_select",
                help=(
                    "Pick a saved filter combination to load. Presets are "
                    "scoped to the Alert Center surface."
                ),
            )
        with col_load:
            st.write("")  # vertical alignment with the selectbox label
            load_clicked = st.button(
                "Load", key="saved_filter_load_btn", use_container_width=True
            )
        with col_delete:
            st.write("")
            delete_clicked = st.button(
                "Delete", key="saved_filter_delete_btn", use_container_width=True
            )

        if load_clicked and chosen and chosen != "(none)":
            try:
                preset = get_preset(chosen, scope="alerts")
                if preset is None:
                    st.warning(f"Preset '{chosen}' no longer exists.")
                else:
                    st.session_state["active_filter_payload"] = dict(preset.payload or {})
                    st.session_state["active_filter_name"] = preset.name
                    st.success(f"Loaded filter preset '{preset.name}'.")
                    st.rerun()
            except Exception:
                logger.exception("Saved filters: load failed")
                st.error("Could not load that preset.")

        if delete_clicked and chosen and chosen != "(none)":
            try:
                ok = delete_preset(chosen, scope="alerts")
                if ok:
                    if st.session_state.get("active_filter_name") == chosen:
                        st.session_state.pop("active_filter_payload", None)
                        st.session_state.pop("active_filter_name", None)
                    st.success(f"Deleted preset '{chosen}'.")
                    st.rerun()
                else:
                    st.warning(f"Could not delete '{chosen}' — preset not found.")
            except Exception:
                logger.exception("Saved filters: delete failed")
                st.error("Could not delete that preset.")

        with st.expander("Save current filter as preset…", expanded=False):
            new_name = st.text_input(
                "Preset name",
                key="saved_filter_name_input",
                help="A short label, e.g. 'critical-24h' or 'transpacific'.",
            )
            save_clicked = st.button("Save", key="saved_filter_save_btn")
            if save_clicked:
                try:
                    nm = (new_name or "").strip()
                    if not nm:
                        st.warning("Please enter a preset name before saving.")
                    else:
                        payload = dict(
                            st.session_state.get("active_filter_payload") or {}
                        )
                        ok = save_preset(
                            FilterPreset(name=nm, scope="alerts", payload=payload)
                        )
                        if ok:
                            st.session_state["active_filter_payload"] = payload
                            st.session_state["active_filter_name"] = nm
                            st.success(f"Saved preset '{nm}'.")
                            st.rerun()
                        else:
                            st.error("Save failed. Check logs for details.")
                except Exception:
                    logger.exception("Saved filters: save failed")
                    st.error("Could not save that preset.")

        active_name = st.session_state.get("active_filter_name")
        active_payload = st.session_state.get("active_filter_payload")
        if active_name and isinstance(active_payload, dict):
            keys = ", ".join(sorted(active_payload.keys())) or "(empty)"
            st.caption(f"Active filter: {active_name} — keys: {keys}")
            # Inline Clear link — wipes the active payload + name and
            # reruns so downstream panels (incidents, ack analytics,
            # alert table) re-render unfiltered.
            if st.button(
                "Clear filter",
                key="saved_filter_clear_btn",
                help="Remove the active filter — show all alerts again.",
            ):
                st.session_state.pop("active_filter_payload", None)
                st.session_state.pop("active_filter_name", None)
                st.rerun()
        else:
            st.caption("No active filter")
    except Exception:
        logger.exception("Saved filters panel render failed")
        st.error("Saved filters panel unavailable.")


def _render_configuration_form() -> None:
    try:
        with st.expander("Alert Configuration — Create New Rule", expanded=False):
            st.html(
                '<div class="sub-section-header">'
                'Define a new threshold rule. Alerts evaluate on each data refresh.'
                '</div>'
            )
            with st.form("create_alert_form", clear_on_submit=True):
                fc1, fc2 = st.columns(2, gap="medium")
                with fc1:
                    alert_name = st.text_input(
                        "Alert Name",
                        placeholder="e.g. Trans-Pacific Rate Spike",
                        help="A descriptive label shown in the alert feed.",
                    )
                    metric_type = st.selectbox(
                        "Metric Type",
                        options=["Freight Rate", "Sentiment Score", "Stock Price",
                                 "Port Congestion", "Macro Indicator"],
                        help="The data category this rule monitors.",
                    )
                    threshold_val = st.number_input(
                        "Threshold Value",
                        value=10.0,
                        step=0.5,
                        format="%.2f",
                        help="Numeric threshold. Units depend on the metric.",
                    )
                    # Per-rule cooldown (v18). 0 = no cooldown, fire on
                    # every evaluation that trips the rule (the legacy
                    # behaviour). A positive value suppresses repeat
                    # fires of this rule for N minutes after a
                    # successful fire — keeps a flaky condition from
                    # spamming the downstream channels.
                    cooldown_minutes = st.number_input(
                        "Cooldown (minutes)",
                        min_value=0,
                        value=0,
                        step=1,
                        help=(
                            "Suppress repeat fires of this rule for N "
                            "minutes after a successful fire. 0 = no "
                            "cooldown (fire every evaluation)."
                        ),
                    )
                with fc2:
                    condition = st.selectbox(
                        "Condition",
                        options=["Above", "Below"],
                        help="Trigger when the metric is above or below the threshold.",
                    )
                    severity = st.selectbox(
                        "Severity",
                        options=["Info", "Warning", "Critical"],
                        index=1,
                        help="Determines badge color and notification urgency.",
                    )
                    email_notify = st.toggle(
                        "Email Notification",
                        value=False,
                        help="Send an email when this rule fires.",
                    )

                submitted = st.form_submit_button(
                    "Create Alert",
                    use_container_width=True,
                    type="primary",
                )

            if submitted:
                try:
                    if not alert_name.strip():
                        st.warning("Please enter an alert name.")
                    else:
                        new_rule = {
                            "id": f"rule_{int(datetime.now().timestamp())}",
                            "name": alert_name.strip(),
                            "metric": metric_type,
                            "threshold": float(threshold_val),
                            "condition": condition,
                            "severity": severity,
                            "email_notify": email_notify,
                            "enabled": True,
                            # v18 per-rule cooldown. Clamped to a
                            # non-negative int by the st.number_input
                            # min_value=0; engine.normalize_rule is
                            # the second line of defence against
                            # hand-edited blobs.
                            "cooldown_minutes": int(cooldown_minutes),
                        }
                        st.session_state["user_alerts"].append(new_rule)
                        st.success(f'Rule "{alert_name.strip()}" created successfully.')
                except Exception as exc:
                    st.error(f"Could not create rule: {exc}")
    except Exception:
        logger.exception("Alert configuration render failed")


def _render_active_alerts(visible_alerts: list[dict]) -> None:
    try:
        # Apply the active filter (if any) BEFORE sorting/display so the
        # row counts in the header and the banner reflect the post-filter
        # list. Pure-function helper → never raises.
        active_payload = st.session_state.get("active_filter_payload")
        active_name = st.session_state.get("active_filter_name")
        original_count = len(visible_alerts)
        visible_alerts = _apply_active_filter(visible_alerts, active_payload)

        section_header(
            "Active Alerts",
            f"{len(visible_alerts)} triggered · evaluated from live feeds",
        )
        st.html(live_data_badge(_ACTIVE_SRC))

        if active_name and isinstance(active_payload, dict) and active_payload:
            st.info(
                f"⚙ Filter active: {active_name} — "
                f"{len(visible_alerts)} of {original_count} alerts shown"
            )

        if not visible_alerts:
            st.success("All clear. No alerts are currently active.")
            return

        headers = ["Severity", "Metric", "Alert", "Current", "Threshold", "Triggered"]
        rows = []
        for alert in visible_alerts:
            sev     = alert.get("severity", "Info")
            sev_col = _SEV_COLOR.get(sev, C_TEXT3)
            m_icon  = _METRIC_ICONS.get(alert.get("metric", ""), "--")
            ts_ago  = _time_ago(alert.get("triggered_at", _now_iso()))

            rows.append([
                _sev_badge(sev),
                _sans(
                    f"{m_icon} {alert.get('metric','')}",
                    color=C_TEXT2, weight=500,
                ),
                _sans(
                    alert.get("name", "Unnamed Alert"),
                    color=C_TEXT, weight=700,
                ),
                _mono(str(alert.get("current_value", "--")), color=sev_col, weight=700),
                _mono(str(alert.get("threshold", "--")), color=C_TEXT, weight=600),
                _sans(ts_ago, color=C_TEXT3, weight=400),
            ])

        wsj_market_table(headers, rows)

        # Dismissal controls
        st.caption("Dismiss an alert to move it to history.")
        cols = st.columns(min(4, len(visible_alerts)))
        for idx, alert in enumerate(visible_alerts):
            with cols[idx % len(cols)]:
                try:
                    if st.button(
                        f"Dismiss #{idx + 1}",
                        key=f"dismiss_alert_{idx}",
                        use_container_width=True,
                    ):
                        st.session_state["alert_dismissed"].add(idx)
                        alert_copy = dict(alert)
                        alert_copy["dismissed"] = True
                        hist = st.session_state.get("alert_history", [])
                        hist.insert(0, alert_copy)
                        st.session_state["alert_history"] = hist[:40]
                        st.rerun()
                except Exception:
                    pass
    except Exception:
        logger.exception("Active alerts render failed")
        st.error("Active alerts section unavailable.")


def _render_history() -> None:
    try:
        section_header(
            "Alert History",
            "Last 20 triggered alerts across all rules",
        )
        st.html(live_data_badge(_HISTORY_SRC))

        history = st.session_state.get("alert_history", [])
        if not history:
            history = _mock_history()
            st.session_state["alert_history"] = history

        display_hist = history[:20]
        if not display_hist:
            st.info("No alert history yet.")
            return

        headers = ["Severity", "Metric", "Alert", "Current", "Threshold", "Triggered", "Status"]
        rows = []
        for alert in display_hist:
            sev      = alert.get("severity", "Info")
            sev_col  = _SEV_COLOR.get(sev, C_TEXT3)
            m_icon   = _METRIC_ICONS.get(alert.get("metric", ""), "--")
            ts       = _fmt_dt(alert.get("triggered_at", ""))
            dismissed = alert.get("dismissed", False)
            status_badge = (
                badge("Dismissed", C_ACCENT) if dismissed else badge("Active", C_HIGH)
            )

            rows.append([
                _sev_badge(sev),
                _sans(
                    f"{m_icon} {alert.get('metric','')}",
                    color=C_TEXT2, weight=500,
                ),
                _sans(alert.get("name", ""), color=C_TEXT, weight=600),
                _mono(str(alert.get("current_value", "--")), color=sev_col, weight=600),
                _mono(str(alert.get("threshold", "--")), color=C_TEXT2, weight=500),
                _mono(ts, color=C_TEXT3, weight=400),
                status_badge,
            ])

        wsj_market_table(headers, rows)
    except Exception:
        logger.exception("Alert history render failed")
        st.error("Alert history unavailable.")


def _render_notifications() -> None:
    try:
        with st.expander("Notification Settings", expanded=False):
            nc1, nc2 = st.columns(2, gap="medium")

            with nc1:
                email_val = st.text_input(
                    "Notification Email",
                    value=st.session_state.get("notif_email", ""),
                    placeholder="you@example.com",
                    key="notif_email_input",
                    help="Receive alert digests and critical notifications here.",
                )
                freq_options = ["Immediate", "Hourly Digest", "Daily Digest", "Weekly Summary"]
                freq_val = st.selectbox(
                    "Notification Frequency",
                    options=freq_options,
                    index=freq_options.index(
                        st.session_state.get("notif_freq", "Immediate")
                    ),
                    key="notif_freq_select",
                    help="How often to bundle and send alert emails.",
                )

            with nc2:
                digest_val = st.toggle(
                    "Enable Alert Digest",
                    value=st.session_state.get("notif_digest", False),
                    key="notif_digest_toggle",
                    help="Bundle multiple alerts into a single email digest.",
                )
                st.html(
                    '<div class="sub-section-header">'
                    'Digest emails summarise all triggered alerts into a single '
                    'message at the configured frequency. Critical alerts are '
                    'always sent immediately when Immediate is selected.'
                    '</div>'
                )

            if st.button("Save Notification Settings", key="save_notif_btn", type="primary"):
                try:
                    st.session_state["notif_email"]  = email_val
                    st.session_state["notif_freq"]   = freq_val
                    st.session_state["notif_digest"] = digest_val
                    st.success("Notification settings saved.")
                except Exception as exc:
                    st.error(f"Could not save settings: {exc}")
    except Exception:
        logger.exception("Notification settings render failed")


def _render_rule_template_panel() -> None:
    """Quick-add panel that lets a cold-starting operator instantiate a
    pre-baked rule from the catalog at ``engine.rule_templates.TEMPLATES``
    without having to fill in the full editor form.

    The panel is wrapped in a collapsed expander so it does not dominate
    the page for power users who already have rules they care about —
    they can ignore it; new operators can find it as the first thing
    inside the Rules Management section.

    Save flow:
      1. Read selected template's ``slug`` from session state.
      2. Materialize to AlertRule via ``template_to_alert_rule``.
      3. Project the AlertRule back to the dict shape ``user_alerts``
         stores (the same shape ``_default_rules()`` emits — UI editor
         understands this; AlertRule itself is the engine's runtime
         contract and not what the editor consumes).
      4. Append to ``st.session_state['user_alerts']`` and call
         ``save_rules`` to persist immediately, mirroring the per-rule
         Save button's eager-persist behaviour.

    All paths wrapped in try/except + st.error / logger.exception so a
    template-import or DB-write failure cannot break the rules manager
    section.
    """
    try:
        # Lazy import so the engine module is loaded only when this
        # panel actually renders — keeps the tab_alerts import light.
        from engine.rule_templates import (
            ALLOWED_CATEGORIES,
            list_templates,
            template_to_alert_rule,
        )
        from engine.alert_engine_v2 import save_rules as engine_save_rules

        with st.expander("📋 Rule templates — quick-add from catalog",
                         expanded=False):
            section_divider("Quick-add from template")
            st.markdown(
                f'<div style="font-size:0.78rem;color:{C_TEXT2};line-height:1.5;'
                f'margin-bottom:10px">Pick a pre-baked rule template to '
                f'instantiate it in one click. Templates are a starting '
                f'point — edit the resulting rule below to tune thresholds, '
                f'severity, or delivery channels for your book.</div>',
                unsafe_allow_html=True,
            )

            # Two-column layout: category picker on the left, template
            # picker on the right. The "(all)" sentinel matches what
            # list_templates(category='(all)') expects so the helper
            # call below stays a clean one-liner.
            cat_options = ["(all)"] + sorted(ALLOWED_CATEGORIES)
            tc1, tc2 = st.columns([1, 2], gap="medium")
            with tc1:
                cat_choice = st.selectbox(
                    "Category",
                    options=cat_options,
                    index=0,
                    key="rule_template_category",
                    help="Narrow the template list to a single category.",
                )
            filtered = list_templates(
                category=cat_choice if cat_choice != "(all)" else None
            )
            with tc2:
                if not filtered:
                    st.info("No templates in this category.")
                    return
                # Map name → template so the selectbox shows operator-
                # facing labels but we still recover the full template
                # object (with slug) on click.
                name_to_tpl = {t.name: t for t in filtered}
                tpl_name = st.selectbox(
                    "Template",
                    options=list(name_to_tpl.keys()),
                    key="rule_template_choice",
                    help="A starter rule with sensible defaults — edit "
                         "after adding to tune for your book.",
                )

            tpl = name_to_tpl.get(tpl_name)
            if tpl is None:
                return

            # Description block — the only context the operator gets
            # before clicking Add.
            st.markdown(
                f'<div style="background:rgba(255,255,255,0.03);'
                f'border-left:3px solid {C_ACCENT};padding:10px 14px;'
                f'margin:8px 0;font-size:0.82rem;color:{C_TEXT};'
                f'line-height:1.5">{tpl.description}</div>',
                unsafe_allow_html=True,
            )

            # Compact summary row — at-a-glance metadata.
            meta = (
                f"<b>Metric:</b> {tpl.metric} &nbsp;·&nbsp; "
                f"<b>Threshold:</b> {tpl.threshold_pct} &nbsp;·&nbsp; "
                f"<b>Severity:</b> {tpl.severity} &nbsp;·&nbsp; "
                f"<b>Cooldown:</b> {tpl.cooldown_minutes} min"
            )
            st.markdown(
                f'<div style="font-size:0.74rem;color:{C_TEXT3};'
                f'margin-bottom:10px">{meta}</div>',
                unsafe_allow_html=True,
            )

            if st.button(
                "➕ Add this rule",
                key=f"rule_template_add_{tpl.slug}",
                use_container_width=True,
                type="primary",
            ):
                try:
                    rule = template_to_alert_rule(tpl)
                    # Project the AlertRule back to the dict shape the
                    # editor / save_rules consume. We carry through
                    # cooldown_minutes when it exists on the dataclass
                    # so the parallel cooldown commit gets full
                    # round-trip support without a coupling change here.
                    new_rule_dict: dict = {
                        "id": rule.rule_id,
                        "rule_id": rule.rule_id,
                        "name": rule.name,
                        "metric": rule.alert_type,
                        "threshold": float(rule.threshold),
                        "condition": "Above",
                        "severity": rule.severity,
                        "email_notify": False,
                        "enabled": bool(rule.enabled),
                        "target_channels": list(rule.target_channels),
                        # Template metadata stamped on the rule for
                        # future "rule was added from template X"
                        # surfacing in the editor / audit log.
                        "template_slug": tpl.slug,
                        "template_category": tpl.category,
                    }
                    cooldown = getattr(rule, "cooldown_minutes", None)
                    if cooldown is not None:
                        new_rule_dict["cooldown_minutes"] = int(cooldown)

                    current = st.session_state.get("user_alerts") or []
                    # Skip if a rule with this slug already exists —
                    # the operator probably double-clicked, and silent
                    # duplicates would confuse the editor count.
                    existing_ids = {
                        r.get("id") or r.get("rule_id") for r in current
                    }
                    if rule.rule_id in existing_ids:
                        st.warning(
                            f"A rule with id '{rule.rule_id}' already "
                            f"exists. Edit or delete it before re-adding."
                        )
                        return

                    current.append(new_rule_dict)
                    st.session_state["user_alerts"] = current
                    engine_save_rules(current)
                    st.success(
                        f"Added '{tpl.name}' from template. Scroll down "
                        f"to the rule editor to tune thresholds, channels, "
                        f"or delivery options."
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not add template rule: {exc}")
                    logger.exception("rule template add failed")

    except Exception:
        logger.exception("Rule template panel render failed")
        # Do not surface st.error here — the panel is supplementary, so
        # a silent failure that leaves the rest of the rules manager
        # operational is preferable to a red banner.


def _render_rules_manager() -> None:
    """Full rule editor: per-rule expander with editable fields + save / delete
    per rule, plus top-level "Save All to Disk" and "Reset to Defaults" actions.

    Persistence wires through ``engine.alert_engine_v2.save_rules`` /
    ``load_rules`` / ``reset_rules`` so user-authored rules survive Streamlit
    cold starts.
    """
    try:
        from engine.alert_engine_v2 import (
            load_rules as engine_load_rules,
            reset_rules as engine_reset_rules,
            save_rules as engine_save_rules,
        )

        rules: list[dict] = st.session_state.get("user_alerts") or _default_rules()
        st.session_state["user_alerts"] = rules

        enabled_count = sum(1 for r in rules if r.get("enabled", True))
        section_header(
            "Rules Management",
            f"{enabled_count} of {len(rules)} rules enabled — edit thresholds, "
            "toggle, delete, or persist to disk.",
        )
        st.html(live_data_badge(_RULES_SRC))

        # ── Quick-add from template ────────────────────────────────────────
        # Cold-start helper: lets a new operator instantiate a pre-baked
        # rule in one click. Collapsed by default so it does not dominate
        # the page for power users.
        _render_rule_template_panel()

        # ── Top-level actions: Save All / Reset / Reload ───────────────────
        action_cols = st.columns([1, 1, 1, 3], gap="small")
        with action_cols[0]:
            if st.button("💾 Save All to Disk", use_container_width=True,
                         key="rules_save_all"):
                try:
                    engine_save_rules(st.session_state["user_alerts"])
                    st.success(
                        f"Saved {len(st.session_state['user_alerts'])} rules."
                    )
                except Exception as exc:
                    st.error(f"Save failed: {exc}")
        with action_cols[1]:
            if st.button("↻ Reload from Disk", use_container_width=True,
                         key="rules_reload"):
                persisted = engine_load_rules()
                if persisted:
                    st.session_state["user_alerts"] = persisted
                    st.success(f"Loaded {len(persisted)} rules from disk.")
                    st.rerun()
                else:
                    st.info("No persisted rules on disk; keeping current session state.")
        with action_cols[2]:
            if st.button("🗘 Reset to Defaults", use_container_width=True,
                         key="rules_reset"):
                engine_reset_rules()
                st.session_state["user_alerts"] = _default_rules()
                st.success("Reset to default rules.")
                st.rerun()
        with action_cols[3]:
            st.markdown(
                f'<div style="font-size:0.72rem;color:{C_TEXT3};line-height:1.4;'
                f'padding-top:8px">Edits below stay in session until you click '
                f'<b>Save All to Disk</b>. Per-rule Save buttons persist immediately.</div>',
                unsafe_allow_html=True,
            )

        # ── Summary read-only table ────────────────────────────────────────
        headers = ["Rule Name", "Metric", "Threshold", "Condition", "Severity", "Email"]
        rows = []
        for rule in rules:
            sev     = rule.get("severity", "Info")
            m_icon  = _METRIC_ICONS.get(rule.get("metric", ""), "--")
            email_label = "On" if rule.get("email_notify") else "Off"
            email_color = C_HIGH if rule.get("email_notify") else C_TEXT3
            name_color = C_TEXT if rule.get("enabled", True) else C_TEXT3

            rows.append([
                _sans(rule.get("name", ""), color=name_color, weight=700),
                _sans(
                    f"{m_icon} {rule.get('metric','')}",
                    color=C_TEXT2, weight=500,
                ),
                _mono(str(rule.get("threshold", "--")), color=C_TEXT, weight=700),
                _sans(rule.get("condition", ""), color=C_TEXT2, weight=500),
                _sev_badge(sev),
                _sans(email_label, color=email_color, weight=600),
            ])
        wsj_market_table(headers, rows)

        # ── Per-rule editors ───────────────────────────────────────────────
        st.html('<div class="sub-section-header">Edit Rules</div>')

        metric_options = list(_METRIC_ICONS.keys()) if _METRIC_ICONS else [
            "Freight Rate", "Sentiment Score", "Stock Price",
            "Port Congestion", "Macro Indicator",
        ]
        severity_options = ["Info", "Warning", "Critical"]
        condition_options = ["Above", "Below"]

        for i, rule in enumerate(rules):
            rule_id = rule.get("id", f"rule_{i}")
            label = rule.get("name") or f"Rule {i+1}"
            status_dot = "🟢" if rule.get("enabled", True) else "⚪"
            with st.expander(f"{status_dot} {label}", expanded=False):
                ec1, ec2 = st.columns(2, gap="medium")
                with ec1:
                    new_name = st.text_input(
                        "Name", value=rule.get("name", ""),
                        key=f"edit_name_{rule_id}",
                    )
                    metric_value = rule.get("metric", metric_options[0])
                    if metric_value not in metric_options:
                        # Preserve a legacy metric value by adding it to the choices.
                        metric_options_local = [metric_value] + metric_options
                    else:
                        metric_options_local = metric_options
                    new_metric = st.selectbox(
                        "Metric", options=metric_options_local,
                        index=metric_options_local.index(metric_value),
                        key=f"edit_metric_{rule_id}",
                    )
                    new_threshold = st.number_input(
                        "Threshold", value=float(rule.get("threshold", 0.0)),
                        step=0.5, format="%.2f",
                        key=f"edit_threshold_{rule_id}",
                    )
                with ec2:
                    cond_value = rule.get("condition", condition_options[0])
                    new_condition = st.selectbox(
                        "Condition", options=condition_options,
                        index=condition_options.index(cond_value)
                          if cond_value in condition_options else 0,
                        key=f"edit_condition_{rule_id}",
                    )
                    sev_value = rule.get("severity", "Warning")
                    new_severity = st.selectbox(
                        "Severity", options=severity_options,
                        index=severity_options.index(sev_value)
                          if sev_value in severity_options else 1,
                        key=f"edit_severity_{rule_id}",
                    )
                    new_email = st.toggle(
                        "Email Notification", value=bool(rule.get("email_notify", False)),
                        key=f"edit_email_{rule_id}",
                    )
                    new_enabled = st.toggle(
                        "Enabled", value=bool(rule.get("enabled", True)),
                        key=f"edit_enabled_{rule_id}",
                    )

                btn_cols = st.columns([1, 1, 4], gap="small")
                with btn_cols[0]:
                    if st.button("💾 Save", key=f"save_btn_{rule_id}",
                                 use_container_width=True, type="primary"):
                        st.session_state["user_alerts"][i].update({
                            "name": new_name.strip() or label,
                            "metric": new_metric,
                            "threshold": float(new_threshold),
                            "condition": new_condition,
                            "severity": new_severity,
                            "email_notify": bool(new_email),
                            "enabled": bool(new_enabled),
                        })
                        try:
                            engine_save_rules(st.session_state["user_alerts"])
                            st.success(f"Saved '{new_name.strip() or label}'.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Save failed: {exc}")
                with btn_cols[1]:
                    if st.button("🗑 Delete", key=f"del_btn_{rule_id}",
                                 use_container_width=True):
                        deleted = st.session_state["user_alerts"].pop(i)
                        try:
                            engine_save_rules(st.session_state["user_alerts"])
                        except Exception as exc:
                            logger.debug(f"rules_manager: post-delete save failed: {exc}")
                        st.warning(f"Deleted '{deleted.get('name', label)}'.")
                        st.rerun()

    except Exception:
        logger.exception("Rules manager render failed")
        st.error("Rules management unavailable.")


def _render_route_thresholds() -> None:
    """Per-route alert threshold override editor.

    Surfaces ``engine.route_thresholds`` so operators can tighten or loosen the
    rate-surge alert threshold on a per-route basis (e.g. "transpacific_eb gets
    3%, asia_europe gets 6%") without editing SQLite by hand. Routes not
    explicitly enabled on save fall back to the global default (8%).

    Rows iterate ``routes.route_registry.ROUTES`` so the editor stays in sync
    with the catalog. Each row has 4 cols: name | threshold_pct | severity |
    enabled-checkbox. On save we only persist rows where Enabled is checked —
    everything else is dropped from the blob (returning that route to the
    global default).

    All paths wrapped in try/except + logger.exception so a kv_state outage
    cannot break the rest of the Alert Center tab.
    """
    try:
        from engine.route_thresholds import (
            RouteThreshold,
            load_route_thresholds,
            save_route_thresholds,
        )
        from routes.route_registry import ROUTES

        section_divider("Per-route Alert Thresholds")
        st.caption(
            "Override the default rate-alert threshold per route. Routes "
            "with no override fall back to the global default (8%)."
        )

        # Single SELECT — reuse the loaded dict to initialise every row.
        current = load_route_thresholds()
        severity_options = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]

        # Header row to anchor the columns visually.
        h1, h2, h3, h4 = st.columns([3, 2, 2, 1], gap="small")
        with h1:
            st.html(f'<div style="font-size:0.75rem;color:{C_TEXT3};font-weight:600;">Route</div>')
        with h2:
            st.html(f'<div style="font-size:0.75rem;color:{C_TEXT3};font-weight:600;">Threshold %</div>')
        with h3:
            st.html(f'<div style="font-size:0.75rem;color:{C_TEXT3};font-weight:600;">Severity</div>')
        with h4:
            st.html(f'<div style="font-size:0.75rem;color:{C_TEXT3};font-weight:600;">Enabled</div>')

        # Per-row inputs. Streamlit keys are route_id-scoped so reruns
        # preserve user edits across saves.
        row_inputs: dict[str, tuple[float, str, bool]] = {}
        for route in ROUTES:
            route_id = route.id
            label = getattr(route, "name", "") or route_id
            override = current.get(route_id)
            default_threshold = float(override.threshold_pct) if override else 8.0
            default_severity = override.severity if override else "HIGH"
            default_enabled = override is not None

            r1, r2, r3, r4 = st.columns([3, 2, 2, 1], gap="small")
            with r1:
                st.markdown(
                    f'<div style="padding-top:8px;color:{C_TEXT};font-weight:600;">'
                    f'{label}</div>',
                    unsafe_allow_html=True,
                )
            with r2:
                thr = st.number_input(
                    "Threshold %",
                    value=default_threshold,
                    min_value=0.5,
                    max_value=50.0,
                    step=0.5,
                    format="%.1f",
                    key=f"route_thr_pct_{route_id}",
                    label_visibility="collapsed",
                )
            with r3:
                sev_idx = (
                    severity_options.index(default_severity)
                    if default_severity in severity_options else 1
                )
                sev = st.selectbox(
                    "Severity",
                    options=severity_options,
                    index=sev_idx,
                    key=f"route_thr_sev_{route_id}",
                    label_visibility="collapsed",
                )
            with r4:
                enabled = st.checkbox(
                    "Enabled",
                    value=default_enabled,
                    key=f"route_thr_en_{route_id}",
                    label_visibility="collapsed",
                )
            row_inputs[route_id] = (float(thr), str(sev), bool(enabled))

        # ── Save / Reset action buttons ────────────────────────────────────
        bcols = st.columns([1, 1, 4], gap="small")
        with bcols[0]:
            if st.button("Save thresholds", key="route_thr_save",
                         use_container_width=True, type="primary"):
                try:
                    payload = {
                        rid: RouteThreshold(
                            route_id=rid,
                            threshold_pct=thr,
                            severity=sev,
                        )
                        for rid, (thr, sev, enabled) in row_inputs.items()
                        if enabled
                    }
                    if save_route_thresholds(payload):
                        st.success(f"Saved {len(payload)} overrides")
                    else:
                        st.error("Failed to save route thresholds.")
                except Exception as exc:
                    logger.exception("save route thresholds failed")
                    st.error(f"Save failed: {exc}")
        with bcols[1]:
            if st.button("Reset all", key="route_thr_reset",
                         use_container_width=True):
                try:
                    save_route_thresholds({})
                    st.success("Cleared all route overrides.")
                    st.rerun()
                except Exception as exc:
                    logger.exception("reset route thresholds failed")
                    st.error(f"Reset failed: {exc}")
    except Exception:
        logger.exception("Per-route alert thresholds render failed")
        st.error("Per-route thresholds section unavailable.")


def _render_incidents_panel() -> None:
    """Render the Active Incidents panel — alerts grouped into incidents.

    Reads from ``engine.alert_correlator.get_recent_incidents`` /
    ``get_incident_summary`` so operators see "3 alerts collapsed into one
    incident" rather than 3 separate rows in the alert table. The grouping
    is computed at READ time (see engine.alert_correlator docstring) so
    nothing here writes back to persisted alert rows.

    Slotted BEFORE the alert table because incidents are the higher-level
    view; the table is the drill-down. Wrapped in try/except + lazy import
    so a correlator failure cannot break the rest of the Alert Center tab.
    """
    try:
        from collections import Counter

        from engine.alert_correlator import (
            correlate_alerts,
            get_incident_summary,
            get_recent_incidents,
        )
        from engine.alert_engine_v2 import load_alerts
        from utils.tz import format_user_tz

        section_divider("Active Incidents")

        window = st.selectbox(
            "Incident window",
            options=[1, 3, 7, 14],
            index=2,
            format_func=lambda d: f"{d} day" if d == 1 else f"{d} days",
            key="incidents_window_days",
            help=(
                "Group alerts that fired within ~30 min of each other and "
                "share a ticker / route / port / type into incidents."
            ),
        )
        window_int = int(window)

        # Active filter — when present, load alerts directly so we can
        # filter BEFORE correlation (the prompt's contract: incidents
        # themselves reflect the active filter, not a post-correlation
        # filter on incidents).
        active_payload = st.session_state.get("active_filter_payload")
        active_name = st.session_state.get("active_filter_name")
        filter_active = bool(
            active_name
            and isinstance(active_payload, dict)
            and active_payload
        )

        if filter_active:
            try:
                raw_alerts = load_alerts(max_age_days=window_int) or []
            except Exception:
                logger.exception("incidents panel: load_alerts failed")
                raw_alerts = []
            original_alert_count = len(raw_alerts)
            filtered_alerts = _apply_active_filter(raw_alerts, active_payload)
            incidents = correlate_alerts(filtered_alerts)
            incidents.sort(key=lambda inc: inc.started_at, reverse=True)
            # Synthesize the same summary shape as get_incident_summary
            # so the KPI strip below works unchanged.
            n_inc = len(incidents)
            n_total = sum(inc.alert_count for inc in incidents)
            largest = max((inc.alert_count for inc in incidents), default=0)
            avg = (n_total / n_inc) if n_inc > 0 else 0.0
            breakdown = dict(
                sorted(
                    Counter(
                        inc.dominant_alert_type for inc in incidents
                    ).items()
                )
            )
            summary = {
                "n_incidents": n_inc,
                "n_total_alerts": n_total,
                "avg_alerts_per_incident": avg,
                "largest_incident_size": largest,
                "breakdown_by_dominant_type": breakdown,
            }
        else:
            summary = get_incident_summary(window_days=window_int)
            incidents = get_recent_incidents(window_days=window_int)
            original_alert_count = int(summary.get("n_total_alerts", 0))
            n_inc = int(summary.get("n_incidents", 0))
            n_total = int(summary.get("n_total_alerts", 0))
            largest = int(summary.get("largest_incident_size", 0))
            avg = float(summary.get("avg_alerts_per_incident", 0.0))
            breakdown = summary.get("breakdown_by_dominant_type") or {}

        if filter_active:
            st.info(
                f"⚙ Filter active: {active_name} — "
                f"{n_total} of {original_alert_count} alerts shown"
            )
        # Most common dominant_alert_type across incidents (ties broken by
        # alphabetical order to keep the KPI stable across runs).
        if breakdown:
            top_count = max(breakdown.values())
            dominant_type = sorted(
                k for k, v in breakdown.items() if v == top_count
            )[0]
        else:
            dominant_type = "—"

        metric_card_row([
            {
                "label":    "Incidents",
                "value":    str(n_inc),
                "accent":   C_ACCENT,
                "sublabel": f"last {window_int} day" + ("" if window_int == 1 else "s"),
            },
            {
                "label":    "Total Alerts in Incidents",
                "value":    str(n_total),
                "accent":   C_MOD,
                "sublabel": f"Avg {avg:.1f} per incident",
            },
            {
                "label":    "Largest Incident",
                "value":    str(largest),
                "accent":   C_LOW if largest >= 5 else C_HIGH,
                "sublabel": "alerts in the biggest group",
            },
            {
                "label":    "Dominant Type",
                "value":    dominant_type,
                "accent":   C_ACCENT,
                "sublabel": (
                    f"{breakdown.get(dominant_type, 0)} of {n_inc} incidents"
                    if dominant_type != "—" and n_inc else "no incidents yet"
                ),
            },
        ], columns=4)

        if not incidents:
            st.info(
                "No incidents in this window yet — alerts haven't clustered."
            )
            return

        headers = [
            "Started", "Severity", "Count", "Dominant Type", "Entities",
            "Incident ID",
        ]
        rows = []
        for inc in incidents:
            sev_label = inc.severity_max.title() if inc.severity_max else "—"
            sev_col = (
                C_LOW if inc.severity_max == "CRITICAL"
                else (C_MOD if inc.severity_max == "HIGH" else C_TEXT3)
            )
            ents = inc.entities_touched or {}
            entity_parts = (
                list(ents.get("tickers", []))
                + list(ents.get("routes", []))
                + list(ents.get("ports", []))
            )
            entity_str = ", ".join(entity_parts) if entity_parts else "—"
            started_local = (
                format_user_tz(inc.started_at) if inc.started_at else ""
            ) or inc.started_at[:16].replace("T", " ")

            rows.append([
                _mono(started_local, color=C_TEXT2, weight=500),
                _sev_badge(sev_label),
                _mono(str(inc.alert_count), color=sev_col, weight=700),
                _sans(inc.dominant_alert_type or "—",
                      color=C_TEXT, weight=600),
                _sans(entity_str, color=C_TEXT2, weight=500),
                _mono((inc.incident_id or "")[:8], color=C_TEXT3, weight=500),
            ])

        wsj_market_table(headers, rows)
    except Exception:
        logger.exception("Active incidents panel render failed")
        st.error("Active incidents panel unavailable.")


def _render_acknowledgment_analytics() -> None:
    """Render acknowledgment analytics for the persisted alerts table.

    Reads aggregates from ``engine.alert_analytics.compute_alert_metrics`` and
    surfaces a 4-KPI strip + severity-breakdown table + by-day line chart.
    Wrapped in try/except so an analytics failure cannot break the rest of
    the Alert Center tab. All engine imports are lazy to mirror the
    ``_render_delivery_channels`` pattern.
    """
    try:
        from engine.alert_analytics import (
            compute_alert_metrics,
            get_unacknowledged_critical,
        )

        section_divider("Acknowledgment Analytics")

        window = st.selectbox(
            "Window",
            options=[7, 14, 30, 60, 90],
            index=2,
            format_func=lambda d: f"{d} days",
            key="ack_analytics_window",
            help="Look-back window for ack metrics over the alerts table.",
        )

        # Active filter — when present, load alerts + filter, then
        # recompute metrics + unack-critical list LOCALLY from the
        # filtered set. The engine helpers run a SQL query that can't
        # be parameterized with arbitrary payload keys, so we replicate
        # the small aggregation here. Both paths produce the same shape
        # (AlertMetrics + list[ShippingAlert]) so the render below is
        # unchanged.
        active_payload = st.session_state.get("active_filter_payload")
        active_name = st.session_state.get("active_filter_name")
        filter_active = bool(
            active_name
            and isinstance(active_payload, dict)
            and active_payload
        )

        if filter_active:
            try:
                from engine.alert_engine_v2 import load_alerts
                raw_alerts = load_alerts(max_age_days=int(window)) or []
            except Exception:
                logger.exception("ack analytics: load_alerts failed")
                raw_alerts = []
            original_alert_count = len(raw_alerts)
            filtered_alerts = _apply_active_filter(raw_alerts, active_payload)
            metrics = _metrics_from_alerts(filtered_alerts)
            unack_critical = [
                a for a in filtered_alerts
                if not bool(getattr(a, "acknowledged", False))
                and str(getattr(a, "severity", "")).upper() == "CRITICAL"
            ]
        else:
            metrics = compute_alert_metrics(window_days=int(window))
            unack_critical = get_unacknowledged_critical(window_days=int(window))
            original_alert_count = metrics.total_alerts

        if filter_active:
            st.info(
                f"⚙ Filter active: {active_name} — "
                f"{metrics.total_alerts} of {original_alert_count} alerts shown"
            )

        if metrics.total_alerts == 0:
            st.info("No alerts yet in this window.")
            return

        # ── KPI strip ──────────────────────────────────────────────────────
        ack_pct = metrics.ack_rate * 100.0
        unack_crit_count = len(unack_critical)
        if metrics.median_time_to_ack_hours is None:
            ttack_value = "—"
        else:
            total_min = int(round(metrics.median_time_to_ack_hours * 60))
            h, m = divmod(max(total_min, 0), 60)
            ttack_value = f"{h}h {m}m"

        metric_card_row([
            {
                "label":    "Total Alerts",
                "value":    str(metrics.total_alerts),
                "accent":   C_ACCENT,
                "sublabel": f"last {int(window)} days",
            },
            {
                "label":    "Acknowledged Rate",
                "value":    f"{ack_pct:.1f}%",
                "accent":   C_HIGH if ack_pct >= 80 else (C_MOD if ack_pct >= 50 else C_LOW),
                "sublabel": f"{metrics.acknowledged_count} of {metrics.total_alerts}",
            },
            {
                "label":    "Unacknowledged Critical",
                "value":    str(unack_crit_count),
                "accent":   C_LOW if unack_crit_count > 0 else C_HIGH,
                "sublabel": "needs attention" if unack_crit_count else "all clear",
            },
            {
                "label":    "Median Time to Ack",
                "value":    ttack_value,
                "accent":   C_MOD,
                "sublabel": "from acked rows",
            },
        ], columns=4)

        # ── Severity breakdown + by-day chart ──────────────────────────────
        bc1, bc2 = st.columns(2, gap="medium")
        with bc1:
            st.html('<div class="sub-section-header">By Severity</div>')
            if metrics.by_severity:
                headers = ["Severity", "Total", "Acked", "Rate"]
                rows = []
                for sev_key in sorted(
                    metrics.by_severity.keys(),
                    key=lambda s: {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(s, 99),
                ):
                    stats = metrics.by_severity[sev_key]
                    rate_pct = stats.get("ack_rate", 0.0) * 100.0
                    rate_color = (
                        C_HIGH if rate_pct >= 80
                        else (C_MOD if rate_pct >= 50 else C_LOW)
                    )
                    rows.append([
                        _sev_badge(sev_key.title()),
                        _mono(str(stats.get("total", 0)), color=C_TEXT, weight=700),
                        _mono(str(stats.get("ack_count", 0)), color=C_TEXT2, weight=600),
                        _mono(f"{rate_pct:.0f}%", color=rate_color, weight=700),
                    ])
                wsj_market_table(headers, rows)
            else:
                st.caption("No severity data in this window.")

        with bc2:
            st.html('<div class="sub-section-header">Daily Volume</div>')
            if metrics.by_day:
                import pandas as pd
                df = pd.DataFrame(metrics.by_day)
                df = df.rename(columns={"total": "Total", "ack_count": "Acked"})
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")[["Total", "Acked"]]
                st.line_chart(df, height=220)
            else:
                st.caption("No daily data in this window.")

        # ── Unacknowledged Critical detail expander ────────────────────────
        if unack_critical:
            with st.expander(
                f"Unacknowledged Critical Alerts ({len(unack_critical)})",
                expanded=False,
            ):
                rows = []
                for a in unack_critical:
                    rows.append({
                        "Created":   _fmt_dt(getattr(a, "created_at", "")),
                        "Type":      getattr(a, "alert_type", ""),
                        "Title":     getattr(a, "title", ""),
                        "Value":     getattr(a, "value", 0.0),
                        "Threshold": getattr(a, "threshold", 0.0),
                        "Change %":  getattr(a, "change_pct", 0.0),
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception:
        logger.exception("Acknowledgment analytics render failed")
        st.error("Acknowledgment analytics unavailable.")


def _render_effectiveness_backtest() -> None:
    """Render Alert Effectiveness panel — backtest of persisted alerts vs realized moves.

    Reads the AlertBacktestReport from ``engine.alert_backtest.backtest_alerts`` and
    surfaces a 4-KPI strip + per-type / per-severity breakdowns. The realized-move
    scoring needs the underlying time-series dicts; we pull those from session_state
    (populated by the cached data loaders in app.py), falling back to empty dicts —
    the backtest engine tolerates that and simply reports nothing-to-score.
    """
    try:
        from engine.alert_backtest import backtest_alerts

        section_divider("Alert Effectiveness")

        c1, c2 = st.columns(2, gap="medium")
        with c1:
            window = st.selectbox(
                "Realized window",
                options=[7, 14, 30],
                index=0,
                format_func=lambda d: f"{d} days",
                key="backtest_window_days",
                help="How many days after each alert to measure the realized move.",
            )
        with c2:
            lookback = st.selectbox(
                "Lookback",
                options=[30, 60, 90],
                index=2,
                format_func=lambda d: f"{d} days",
                key="backtest_lookback_days",
                help="How far back to pull alerts whose realized window is fully in the past.",
            )

        stock_data   = st.session_state.get("stock_data", {}) or {}
        freight_data = st.session_state.get("freight_data", {}) or {}
        macro_data   = st.session_state.get("macro_data", {}) or {}

        report = backtest_alerts(
            stock_data, freight_data, macro_data,
            window_days=int(window),
            lookback_days=int(lookback),
        )

        if report.n_alerts_evaluated == 0:
            st.info(
                "No alerts to backtest yet — alerts need at least "
                f"{int(window)} days of post-fire data to score."
            )
            return

        # ── KPI strip ──────────────────────────────────────────────────────
        hit_pct = report.hit_rate * 100.0
        hit_accent = (
            C_HIGH if report.hit_rate >= 0.55
            else (C_MOD if report.hit_rate >= 0.45 else C_LOW)
        )
        hits = int(round(report.hit_rate * report.n_alerts_evaluated))

        metric_card_row([
            {
                "label":    "Alerts Evaluated",
                "value":    str(report.n_alerts_evaluated),
                "accent":   C_ACCENT,
                "sublabel": f"window {int(window)}d · lookback {int(lookback)}d",
            },
            {
                "label":    "Hit Rate",
                "value":    f"{hit_pct:.1f}%",
                "accent":   hit_accent,
                "sublabel": f"{hits} of {report.n_alerts_evaluated}",
            },
            {
                "label":    "Median Magnitude Ratio",
                "value":    f"{report.median_magnitude_ratio:.2f}",
                "accent":   C_MOD,
                "sublabel": "1.0 = exactly as predicted, >1 = overshoot",
            },
            {
                "label":    "Skipped",
                "value":    str(report.n_skipped),
                "accent":   C_TEXT3,
                "sublabel": "no realized data",
            },
        ], columns=4)

        # ── By-type / By-severity breakdown tables ─────────────────────────
        bc1, bc2 = st.columns(2, gap="medium")
        with bc1:
            st.html('<div class="sub-section-header">By Alert Type</div>')
            if report.by_alert_type:
                headers = ["Type", "Count", "Hits", "Hit Rate"]
                rows = []
                for atype in sorted(report.by_alert_type.keys()):
                    stats = report.by_alert_type[atype]
                    count = int(stats.get("count", 0))
                    hit_count = int(stats.get("hit_count", 0))
                    rate_pct = stats.get("hit_rate", 0.0) * 100.0
                    rate_color = (
                        C_HIGH if rate_pct >= 55
                        else (C_MOD if rate_pct >= 45 else C_LOW)
                    )
                    rows.append([
                        _sans(atype, color=C_TEXT, weight=700),
                        _mono(str(count), color=C_TEXT, weight=600),
                        _mono(str(hit_count), color=C_TEXT2, weight=600),
                        _mono(f"{rate_pct:.1f}%", color=rate_color, weight=700),
                    ])
                wsj_market_table(headers, rows)
            else:
                st.caption("No type breakdown in this window.")

        with bc2:
            st.html('<div class="sub-section-header">By Severity</div>')
            if report.by_severity:
                headers = ["Severity", "Count", "Hits", "Hit Rate"]
                rows = []
                for sev_key in sorted(
                    report.by_severity.keys(),
                    key=lambda s: {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(s, 99),
                ):
                    stats = report.by_severity[sev_key]
                    count = int(stats.get("count", 0))
                    hit_count = int(stats.get("hit_count", 0))
                    rate_pct = stats.get("hit_rate", 0.0) * 100.0
                    rate_color = (
                        C_HIGH if rate_pct >= 55
                        else (C_MOD if rate_pct >= 45 else C_LOW)
                    )
                    rows.append([
                        _sev_badge(sev_key.title()),
                        _mono(str(count), color=C_TEXT, weight=600),
                        _mono(str(hit_count), color=C_TEXT2, weight=600),
                        _mono(f"{rate_pct:.1f}%", color=rate_color, weight=700),
                    ])
                wsj_market_table(headers, rows)
            else:
                st.caption("No severity breakdown in this window.")

        # ── Avg predicted vs avg realized footer ───────────────────────────
        st.caption(
            f"Avg predicted {report.avg_predicted_pct:+.1f}% · "
            f"Avg realized {report.avg_realized_pct:+.1f}%"
        )
    except Exception:
        logger.exception("Alert effectiveness backtest render failed")
        st.error("Alert effectiveness panel unavailable.")


def _render_delivery_channels() -> None:
    """Manage outbound delivery channels and trigger pending deliveries.

    Supports all six channel kinds exposed by ``engine.alert_delivery``:
    slack, email, sms, webhook, discord, and pagerduty. Each channel can
    operate in ``immediate`` or ``daily`` digest mode.

    Wires through ``engine.alert_delivery`` for persistence + transport.
    Wrapped in a single try/except so a delivery outage cannot break the
    rest of the Alert Center tab.
    """
    try:
        import re
        from uuid import uuid4
        from engine.alert_delivery import (
            DeliveryChannel,
            delete_channel,
            deliver_alert,
            deliver_pending,
            load_channels,
            save_channel,
            send_test_ping,
        )
        from engine.alert_engine_v2 import _make as _make_alert

        # Per-channel rate limit for the "Send test ping" button. Purely
        # session-scoped (no DB row) — 30s between consecutive pings to
        # the same channel so an over-eager operator can't spam a Slack
        # workspace by clicking the button repeatedly.
        _TEST_PING_RATELIMIT_S = 30.0

        def _render_test_ping_section(ch: DeliveryChannel) -> None:
            """Render the "Send test ping" button + click handler for one
            channel. Rate-limited per-channel via st.session_state.

            On click: calls :func:`engine.alert_delivery.send_test_ping`
            and surfaces ``st.success`` / ``st.error`` with the returned
            message. The button itself is disabled while inside the
            rate-limit window so the operator gets immediate UI feedback
            rather than a silent reject.
            """
            ts_key = f"test_ping_ts::{ch.channel_id}"
            now_ts = datetime.now(timezone.utc).timestamp()
            last_ts = st.session_state.get(ts_key, 0.0)
            try:
                last_ts_f = float(last_ts)
            except (TypeError, ValueError):
                last_ts_f = 0.0
            remaining = _TEST_PING_RATELIMIT_S - (now_ts - last_ts_f)
            cooling = remaining > 0
            btn_label = (
                f"\U0001f9ea Send test ping ({int(remaining)}s)"
                if cooling
                else f"\U0001f9ea Send test ping ({ch.name})"
            )
            if st.button(
                btn_label,
                key=f"send_test_ping_{ch.channel_id}",
                use_container_width=True,
                disabled=cooling,
            ):
                # Re-check just before firing — the disabled flag is a UI
                # nicety; the timestamp gate is the actual rate limit.
                now_ts2 = datetime.now(timezone.utc).timestamp()
                last_ts2 = st.session_state.get(ts_key, 0.0)
                try:
                    last_ts2_f = float(last_ts2)
                except (TypeError, ValueError):
                    last_ts2_f = 0.0
                if (now_ts2 - last_ts2_f) < _TEST_PING_RATELIMIT_S:
                    st.warning(
                        "Please wait at least 30 seconds between test pings "
                        "to the same channel."
                    )
                else:
                    st.session_state[ts_key] = now_ts2
                    try:
                        ok, msg = send_test_ping(ch)
                    except Exception as exc:  # defence-in-depth
                        ok, msg = False, str(exc)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)

        # Forgiving validation patterns: case-insensitive checks where it
        # matters; pragmatic shape checks elsewhere. PagerDuty keys vary
        # slightly in format so a short key triggers a warning, not a block.
        _SLACK_URL_RE = re.compile(r"^https?://hooks\.slack\.com/", re.IGNORECASE)
        _DISCORD_URL_RE = re.compile(
            r"^https?://(discord\.com|discordapp\.com)/api/webhooks/",
            re.IGNORECASE,
        )
        _EMAIL_RE = re.compile(r"^\S+@\S+\.\S+$")
        _SMS_RE = re.compile(r"^\+[1-9]\d{1,14}$")
        _KIND_LABEL = {
            "slack": "slack",
            "email": "email",
            "sms": "sms",
            "webhook": "webhook",
            "discord": "discord",
            "pagerduty": "pagerduty",
        }

        section_divider("Delivery Channels")
        section_header(
            "Outbound Notifications",
            "Push triggered alerts to Slack, Email, SMS, generic webhooks, "
            "Discord, or PagerDuty.",
        )

        # ── Existing channels list ─────────────────────────────────────────
        try:
            channels = load_channels()
        except Exception as exc:
            logger.exception("load_channels failed")
            st.error(f"Could not load delivery channels: {exc}")
            channels = []

        if channels:
            headers = ["Name", "Kind", "Threshold", "Digest", "Quiet", "Enabled", "Created"]
            rows = []
            for ch in channels:
                kind_label = _KIND_LABEL.get(ch.kind, ch.kind)
                digest_label = getattr(ch, "digest_mode", "immediate") or "immediate"
                q_start = (getattr(ch, "quiet_start", "") or "").strip()
                q_end = (getattr(ch, "quiet_end", "") or "").strip()
                q_override = bool(getattr(ch, "quiet_override_critical", True))
                if q_start and q_end:
                    quiet_text = f"{q_start}→{q_end}"
                    if not q_override:
                        # When override=False, even CRITICAL is silenced.
                        quiet_text += " ⚠"
                    quiet_cell = _sans(quiet_text, color=C_ACCENT, weight=600)
                else:
                    quiet_cell = _sans("—", color=C_TEXT3, weight=400)
                rows.append([
                    _sans(ch.name, color=C_TEXT, weight=700),
                    _sans(kind_label, color=C_TEXT2, weight=600),
                    _sev_badge(ch.severity_threshold.title()
                               if ch.severity_threshold in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
                               else ch.severity_threshold),
                    _sans(digest_label,
                          color=C_ACCENT if digest_label == "daily" else C_TEXT2,
                          weight=600),
                    quiet_cell,
                    _sans("On" if ch.enabled else "Off",
                          color=C_HIGH if ch.enabled else C_TEXT3, weight=600),
                    _mono(_fmt_dt(ch.created_at), color=C_TEXT3, weight=400),
                ])
            wsj_market_table(headers, rows)

            # Per-channel action buttons — Test ping + Delete laid out side
            # by side. The test-ping button calls send_test_ping (synthetic
            # "TEST" alert + delivery_log row); the older "Test {name}"
            # button manufactured a real alert and is replaced here.
            for ch in channels:
                act_cols = st.columns([1, 1, 4], gap="small")
                with act_cols[0]:
                    _render_test_ping_section(ch)
                with act_cols[1]:
                    if st.button(
                        f"🗑 Delete {ch.name}",
                        key=f"del_channel_{ch.channel_id}",
                        use_container_width=True,
                    ):
                        try:
                            delete_channel(ch.channel_id)
                            st.success(f"Deleted channel '{ch.name}'.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Delete failed: {exc}")
        else:
            st.info("No delivery channels configured yet. Add one below.")

        # ── Add Channel form ───────────────────────────────────────────────
        # Per-kind copy for the target field + an informational block listing
        # any env-var prerequisites (auth via env vars vs auth in target).
        _KIND_COPY: dict[str, dict] = {
            "slack": {
                "label": "Webhook URL",
                "placeholder": "https://hooks.slack.com/services/...",
                "info": None,  # auth lives in the URL itself
            },
            "email": {
                "label": "Recipient Email",
                "placeholder": "alerts@example.com",
                "info": (
                    "Email delivery requires SMTP env vars: `SMTP_HOST`, "
                    "`SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, "
                    "`SMTP_FROM_ADDRESS`. See docs/AUTH.md for setup."
                ),
            },
            "sms": {
                "label": "Phone Number (E.164)",
                "placeholder": "+15551234567",
                "info": (
                    "SMS delivery requires Twilio env vars: "
                    "`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, "
                    "`TWILIO_FROM_NUMBER`."
                ),
            },
            "webhook": {
                "label": "Webhook URL",
                "placeholder": "https://your-endpoint.example.com/alerts",
                "info": None,  # auth handled by the receiver
            },
            "discord": {
                "label": "Discord Webhook URL",
                "placeholder": "https://discord.com/api/webhooks/...",
                "info": None,  # auth lives in the URL itself
            },
            "pagerduty": {
                "label": "Integration Key",
                "placeholder": "R0123ABCDEF...",
                "info": None,  # routing key in target, no env vars needed
            },
        }

        with st.expander("Add Channel", expanded=not channels):
            with st.form("add_channel_form", clear_on_submit=True):
                ch_name = st.text_input(
                    "Channel name",
                    placeholder="e.g. Trading desk Slack",
                )
                ch_kind = st.selectbox(
                    "Kind",
                    options=["slack", "email", "sms", "webhook", "discord", "pagerduty"],
                    help=(
                        "slack/discord → POST to webhook URL · email → SMTP "
                        "to recipient · sms → Twilio to E.164 number · "
                        "webhook → POST JSON to any URL · "
                        "pagerduty → trigger PagerDuty incident."
                    ),
                )
                _copy = _KIND_COPY.get(ch_kind, _KIND_COPY["slack"])
                ch_target = st.text_input(
                    _copy["label"],
                    placeholder=_copy["placeholder"],
                )
                if _copy["info"]:
                    st.info(_copy["info"])
                ch_threshold = st.selectbox(
                    "Severity threshold",
                    options=["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    index=2,
                    help="Channel delivers alerts at this severity or higher.",
                )
                ch_digest = st.selectbox(
                    "Digest mode",
                    options=["immediate", "daily"],
                    index=0,
                    help=(
                        "Immediate: send every alert as it fires. "
                        "Daily: batch alerts into a single daily message."
                    ),
                )
                ch_quiet_start = st.text_input(
                    "Quiet hours start (UTC HH:MM)",
                    placeholder="22:00",
                )
                ch_quiet_end = st.text_input(
                    "Quiet hours end (UTC HH:MM)",
                    placeholder="07:00",
                )
                ch_quiet_override = st.checkbox(
                    "CRITICAL alerts always deliver during quiet hours",
                    value=True,
                )
                st.caption(
                    "Leave both blank to disable quiet hours. Wraparound is "
                    "supported (e.g. 22:00 → 07:00 spans midnight)."
                )
                ch_enabled = st.checkbox("Enabled", value=True)
                submitted = st.form_submit_button(
                    "Save channel", use_container_width=True, type="primary",
                )

            if submitted:
                target_clean = ch_target.strip()
                pd_shape_warning = False
                # Quiet-hours validation: both set + HH:MM (0-23):(0-59), or both blank.
                _QUIET_RE = re.compile(r"^\d{1,2}:\d{2}$")
                q_start_clean = (ch_quiet_start or "").strip()
                q_end_clean = (ch_quiet_end or "").strip()

                def _valid_hhmm(s: str) -> bool:
                    if not _QUIET_RE.match(s):
                        return False
                    try:
                        h, m = s.split(":")
                        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
                    except Exception:
                        return False

                quiet_ok = (
                    (not q_start_clean and not q_end_clean)
                    or (
                        _valid_hhmm(q_start_clean)
                        and _valid_hhmm(q_end_clean)
                    )
                )

                if not ch_name.strip():
                    st.warning("Please enter a channel name.")
                elif not target_clean:
                    st.warning(f"Please enter a {_copy['label'].lower()}.")
                elif not quiet_ok:
                    st.error(
                        "Quiet hours must be both set with HH:MM (24h) — "
                        "leave both blank to disable."
                    )
                elif ch_kind == "slack" and not _SLACK_URL_RE.match(target_clean):
                    st.error(
                        "Slack webhook URLs must start with https://hooks.slack.com/"
                    )
                elif ch_kind == "discord" and not _DISCORD_URL_RE.match(target_clean):
                    st.error(
                        "Discord webhook URLs must start with "
                        "https://discord.com/api/webhooks/ or "
                        "https://discordapp.com/api/webhooks/"
                    )
                elif ch_kind == "email" and not _EMAIL_RE.match(target_clean):
                    st.error(
                        "Recipient must be a valid email address (e.g. you@example.com)."
                    )
                elif ch_kind == "sms" and not _SMS_RE.match(target_clean):
                    st.error(
                        "SMS recipient must be E.164 format "
                        "(e.g. +15551234567 — leading +, no spaces or dashes)."
                    )
                elif ch_kind == "webhook" and not (
                    target_clean.startswith("http://")
                    or target_clean.startswith("https://")
                ):
                    st.error("Webhook URL must start with http:// or https://")
                else:
                    # PagerDuty integration keys are usually 32 chars but the
                    # format varies slightly — surface a warning and still
                    # allow the save.
                    if ch_kind == "pagerduty" and len(target_clean) < 32:
                        st.warning(
                            "PagerDuty integration keys are typically 32 "
                            "characters. Saving anyway — verify the key if "
                            "deliveries fail."
                        )
                        pd_shape_warning = True
                    try:
                        save_channel(DeliveryChannel(
                            channel_id=str(uuid4()),
                            name=ch_name.strip(),
                            kind=ch_kind,
                            target=target_clean,
                            severity_threshold=ch_threshold,
                            enabled=bool(ch_enabled),
                            digest_mode=ch_digest,
                            quiet_start=q_start_clean,
                            quiet_end=q_end_clean,
                            quiet_override_critical=bool(ch_quiet_override),
                        ))
                        st.success(f"Saved channel '{ch_name.strip()}'.")
                        if not pd_shape_warning:
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Save failed: {exc}")

        # ── Deliver pending alerts ─────────────────────────────────────────
        enabled_channels = [c for c in channels if c.enabled]
        if st.button(
            f"📤 Deliver pending alerts ({len(enabled_channels)} enabled)",
            key="deliver_pending_btn",
            use_container_width=False,
            disabled=not enabled_channels,
        ):
            since = datetime.now(timezone.utc) - timedelta(hours=24)
            for ch in enabled_channels:
                try:
                    results = deliver_pending(ch, since=since)
                    sent = sum(1 for r in results if r.success and r.status_code != 0)
                    failed = sum(1 for r in results if not r.success)
                    if sent or failed:
                        st.info(
                            f"{ch.name}: sent {sent}, failed {failed} "
                            f"(of {len(results)} considered)."
                        )
                    else:
                        st.caption(f"{ch.name}: no alerts in the last 24h.")
                except Exception as exc:
                    logger.exception("deliver_pending failed for channel %s", ch.name)
                    st.error(f"{ch.name}: delivery error — {exc}")
    except Exception:
        logger.exception("Delivery channels render failed")
        st.error("Delivery channels section unavailable.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def render(
    port_results=None,
    route_results=None,
    insights=None,
    freight_data=None,
    macro_data=None,
    stock_data=None,
):
    """Render the Alert Center tab."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('alerts'):
        try:
            _init_state()
        except Exception:
            logger.exception("Alert Center state init failed")

        # Derive active alerts
        try:
            scanned = _scan_triggered_alerts(freight_data, insights, stock_data, macro_data)
            if scanned:
                st.session_state["active_alerts_cache"] = scanned
            active_alerts = st.session_state.get("active_alerts_cache", []) or []
            if not active_alerts:
                active_alerts = _mock_history()[:3]
        except Exception:
            logger.exception("Alert scan failed")
            active_alerts = _mock_history()[:3]

        dismissed_ids = st.session_state.get("alert_dismissed", set())
        visible_alerts = [
            a for i, a in enumerate(active_alerts)
            if i not in dismissed_ids and not a.get("dismissed", False)
        ]

        try:
            _render_hero(visible_alerts)
            _render_saved_filters()
            _render_configuration_form()

            _render_incidents_panel()

            section_divider("Live Monitoring")
            _render_active_alerts(visible_alerts)

            st.divider()
            _render_history()

            _render_acknowledgment_analytics()
            _render_effectiveness_backtest()

            section_divider("Configuration")
            _render_notifications()
            _render_rules_manager()
            _render_route_thresholds()

            _render_delivery_channels()
        except Exception:
            logger.exception("tab_alerts top-level render failed")
            st.error("Alert Center tab encountered an error.")


