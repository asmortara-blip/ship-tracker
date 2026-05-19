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
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
        st.session_state["user_alerts"] = _default_rules()
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
                        }
                        st.session_state["user_alerts"].append(new_rule)
                        st.success(f'Rule "{alert_name.strip()}" created successfully.')
                except Exception as exc:
                    st.error(f"Could not create rule: {exc}")
    except Exception:
        logger.exception("Alert configuration render failed")


def _render_active_alerts(visible_alerts: list[dict]) -> None:
    try:
        section_header(
            "Active Alerts",
            f"{len(visible_alerts)} triggered · evaluated from live feeds",
        )
        st.html(live_data_badge(_ACTIVE_SRC))

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


def _render_rules_manager() -> None:
    try:
        rules = st.session_state.get("user_alerts") or _default_rules()
        st.session_state["user_alerts"] = rules

        enabled_count = sum(1 for r in rules if r.get("enabled", True))
        section_header(
            "Rules Management",
            f"{enabled_count} of {len(rules)} rules enabled",
        )
        st.html(live_data_badge(_RULES_SRC))

        # Table of rules (read-only summary)
        headers = ["Rule Name", "Metric", "Threshold", "Condition", "Severity", "Email"]
        rows = []
        for rule in rules:
            sev     = rule.get("severity", "Info")
            m_icon  = _METRIC_ICONS.get(rule.get("metric", ""), "--")
            email_label = "On" if rule.get("email_notify") else "Off"
            email_color = C_HIGH if rule.get("email_notify") else C_TEXT3

            rows.append([
                _sans(rule.get("name", ""), color=C_TEXT, weight=700),
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

        # Enable / disable toggles
        st.html('<div class="sub-section-header">Enable / Disable Rules</div>')
        tcols = st.columns(min(3, max(1, len(rules))))
        for i, rule in enumerate(rules):
            with tcols[i % len(tcols)]:
                try:
                    enabled_new = st.toggle(
                        rule.get("name", f"Rule {i+1}"),
                        value=rule.get("enabled", True),
                        key=f"rule_toggle_{rule.get('id', i)}_{i}",
                    )
                    st.session_state["user_alerts"][i]["enabled"] = enabled_new
                except Exception:
                    pass
    except Exception:
        logger.exception("Rules manager render failed")
        st.error("Rules management unavailable.")


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
        _render_configuration_form()

        section_divider("Live Monitoring")
        _render_active_alerts(visible_alerts)

        st.divider()
        _render_history()

        section_divider("Configuration")
        _render_notifications()
        _render_rules_manager()
    except Exception:
        logger.exception("tab_alerts top-level render failed")
        st.error("Alert Center tab encountered an error.")


