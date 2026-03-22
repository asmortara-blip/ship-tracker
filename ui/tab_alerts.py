"""Alert Center — intelligent shipping alert system with threshold monitoring & notifications.

Sections
--------
0.  Hero            — total alerts, active rules, last triggered timestamp
1.  Configure       — form to create new alert rules
2.  Active Alerts   — triggered alerts derived from live data
3.  History         — last 20 alerts (mock + session state)
4.  Notifications   — email, frequency, digest toggle
5.  Rules Manager   — table of all configured rules with toggles
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import streamlit as st

# ── Colour palette ─────────────────────────────────────────────────────────────
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

_SEV_COLOR = {"Critical": C_LOW, "Warning": C_MOD, "Info": C_ACCENT}
_SEV_BG    = {
    "Critical": "rgba(239,68,68,0.12)",
    "Warning":  "rgba(245,158,11,0.12)",
    "Info":     "rgba(59,130,246,0.12)",
}
_SEV_BORDER = {
    "Critical": "rgba(239,68,68,0.45)",
    "Warning":  "rgba(245,158,11,0.40)",
    "Info":     "rgba(59,130,246,0.40)",
}
_SEV_ICON = {"Critical": "🔴", "Warning": "🟡", "Info": "🔵"}

_METRIC_ICONS = {
    "Freight Rate":     "💹",
    "Sentiment Score":  "📡",
    "Stock Price":      "📈",
    "Port Congestion":  "🚧",
    "Macro Indicator":  "🌍",
}

# ── CSS ────────────────────────────────────────────────────────────────────────
_CSS = """
<style>
.alc-header-label {
    font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.15em;
    color: #475569; margin-bottom: 6px;
}
.alc-hero-title {
    font-size: 1.9rem; font-weight: 900; color: #f1f5f9;
    letter-spacing: -0.03em; line-height: 1.1;
}
.alc-hero-sub {
    font-size: 0.8rem; color: #64748b; margin-top: 5px;
}
.alc-stat-card {
    background: #1a2235;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 16px 18px;
    text-align: center;
}
.alc-stat-num  { font-size: 2rem; font-weight: 900; line-height: 1; }
.alc-stat-lbl  { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em;
                  color: #94a3b8; margin-top: 4px; }
.alc-stat-sub  { font-size: 0.7rem; color: #64748b; margin-top: 2px; }
.alc-alert-card {
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 10px;
    border-left: 4px solid;
    border-top: 1px solid;
    border-right: 1px solid;
    border-bottom: 1px solid;
}
.alc-sev-badge {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 10px; border-radius: 999px;
    font-size: 0.68rem; font-weight: 700; letter-spacing: 0.05em;
    border: 1px solid;
}
.alc-section-label {
    font-size: 0.75rem; font-weight: 700; color: #94a3b8;
    text-transform: uppercase; letter-spacing: 0.1em;
    margin-bottom: 14px;
}
.alc-rule-row {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 0;
    border-bottom: 1px solid rgba(255,255,255,0.05);
}
.alc-pill {
    display: inline-flex; align-items: center;
    padding: 2px 9px; border-radius: 999px;
    font-size: 0.67rem; font-weight: 600;
    background: rgba(59,130,246,0.12);
    color: #3b82f6; border: 1px solid rgba(59,130,246,0.3);
}
.alc-hist-row {
    display: flex; align-items: flex-start; gap: 10px;
    padding: 9px 0; border-bottom: 1px solid rgba(255,255,255,0.05);
}
.alc-hist-ts { font-size: 0.67rem; color: #64748b; white-space: nowrap; margin-top: 2px; }
@keyframes crit-pulse {
    0%,100% { box-shadow: 0 0 0 0 rgba(239,68,68,0); }
    50%      { box-shadow: 0 0 0 5px rgba(239,68,68,0.15); }
}
.alc-critical-pulse { animation: crit-pulse 2.2s ease-in-out infinite; }
</style>
"""

# ── Helpers ────────────────────────────────────────────────────────────────────

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


def _sev_badge(sev: str) -> str:
    color  = _SEV_COLOR.get(sev, C_TEXT3)
    bg     = _SEV_BG.get(sev, "rgba(100,116,139,0.1)")
    border = _SEV_BORDER.get(sev, C_BORDER)
    icon   = _SEV_ICON.get(sev, "⚪")
    return (
        f'<span class="alc-sev-badge" '
        f'style="color:{color};background:{bg};border-color:{border}">'
        f'{icon} {sev}</span>'
    )


def _alert_card_html(alert: dict) -> str:
    sev    = alert.get("severity", "Info")
    color  = _SEV_COLOR.get(sev, C_TEXT3)
    bg     = _SEV_BG.get(sev, "rgba(100,116,139,0.1)")
    border = _SEV_BORDER.get(sev, C_BORDER)
    icon   = _METRIC_ICONS.get(alert.get("metric", ""), "📋")
    pulse  = " alc-critical-pulse" if sev == "Critical" else ""
    val    = alert.get("current_value", "—")
    thr    = alert.get("threshold", "—")
    ts     = _time_ago(alert.get("triggered_at", _now_iso()))
    return (
        f'<div class="alc-alert-card{pulse}" '
        f'style="background:{bg};border-color:{border};border-left-color:{color}">'
        f'  <div style="display:flex;align-items:flex-start;gap:12px">'
        f'    <span style="font-size:1.35rem;margin-top:1px">{icon}</span>'
        f'    <div style="flex:1;min-width:0">'
        f'      <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;flex-wrap:wrap">'
        f'        {_sev_badge(sev)}'
        f'        <span style="font-size:0.7rem;color:{C_TEXT3}">{alert.get("metric","")}</span>'
        f'        <span style="font-size:0.67rem;color:{C_TEXT3};margin-left:auto">{ts}</span>'
        f'      </div>'
        f'      <div style="font-size:0.95rem;font-weight:700;color:{C_TEXT};margin-bottom:5px;line-height:1.3">'
        f'        {alert.get("name","Unnamed Alert")}'
        f'      </div>'
        f'      <div style="font-size:0.8rem;color:{C_TEXT2};line-height:1.5;margin-bottom:8px">'
        f'        {alert.get("description","")}'
        f'      </div>'
        f'      <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">'
        f'        <span style="font-size:0.75rem;color:{C_TEXT3}">Current: '
        f'          <strong style="color:{color}">{val}</strong>'
        f'        </span>'
        f'        <span style="font-size:0.75rem;color:{C_TEXT3}">Threshold: '
        f'          <strong style="color:{C_TEXT}">{thr}</strong>'
        f'        </span>'
        f'        <span style="font-size:0.67rem;color:{C_TEXT3}">Triggered: {_fmt_dt(alert.get("triggered_at",""))}</span>'
        f'      </div>'
        f'    </div>'
        f'  </div>'
        f'</div>'
    )


# ── Default rules ──────────────────────────────────────────────────────────────

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


# ── Mock history data ──────────────────────────────────────────────────────────

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
            "name": "ZIM Daily Move −6.2%",
            "metric": "Stock Price",
            "severity": "Warning",
            "description": "ZIM Integrated Shipping (ZIM) fell 6.2% intraday, exceeding the 5% alert threshold.",
            "current_value": "−6.2%",
            "threshold": "±5.0%",
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
            "description": "Three consecutive bearish signals detected on Asia–Europe corridor.",
            "current_value": "−0.61",
            "threshold": "−0.50",
            "triggered_at": (base - timedelta(hours=11, minutes=5)).isoformat(),
            "dismissed": True,
        },
        {
            "name": "Baltic Dry Index Drop −8%",
            "metric": "Macro Indicator",
            "severity": "Info",
            "description": "BDI declined 8% week-over-week, signalling softening dry bulk demand.",
            "current_value": "1,843",
            "threshold": "BDI Δ >5%",
            "triggered_at": (base - timedelta(days=1, hours=3)).isoformat(),
            "dismissed": True,
        },
        {
            "name": "MATX Equity Move +5.8%",
            "metric": "Stock Price",
            "severity": "Warning",
            "description": "Matson Inc. (MATX) rallied 5.8% on stronger-than-expected volume guidance.",
            "current_value": "+5.8%",
            "threshold": "±5.0%",
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
            "description": "USLAX congestion index reached 0.93 — severe vessel queue buildup.",
            "current_value": "0.93",
            "threshold": "0.80",
            "triggered_at": (base - timedelta(days=4, hours=14)).isoformat(),
            "dismissed": True,
        },
        {
            "name": "SBLK Daily Drop −7.1%",
            "metric": "Stock Price",
            "severity": "Warning",
            "description": "Star Bulk Carriers (SBLK) fell 7.1% following BDI weakness.",
            "current_value": "−7.1%",
            "threshold": "±5.0%",
            "triggered_at": (base - timedelta(days=5, hours=8, minutes=30)).isoformat(),
            "dismissed": True,
        },
    ]


# ── Scan real data for triggered alerts ───────────────────────────────────────

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
                            "name": f"Freight Rate {direction.title()} — {route}",
                            "metric": "Freight Rate",
                            "severity": "Critical",
                            "description": (
                                f"Weekly rate change on {route} reached {float(chg):+.1f}%, "
                                f"exceeding the 20% threshold."
                            ),
                            "current_value": f"{float(chg):+.1f}%",
                            "threshold": "±20.0%",
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
                        "name": f"Bearish Sentiment — {route_label or 'Market'}",
                        "metric": "Sentiment Score",
                        "severity": "Warning",
                        "description": (
                            f"Sentiment model flagged a strong bearish signal"
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
                        "threshold": "±5.0%",
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
                "threshold": "±8.0%",
                "triggered_at": now,
                "dismissed": False,
            })
    except Exception:
        pass

    return triggered


# ── Session state initialisation ──────────────────────────────────────────────

def _init_state():
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


# ── Main render ────────────────────────────────────────────────────────────────

def render(port_results, route_results, insights, freight_data, macro_data, stock_data):
    """Render the Alert Center tab."""
    try:
        st.markdown(_CSS, unsafe_allow_html=True)
        _init_state()
    except Exception:
        pass

    # ── Hero header ───────────────────────────────────────────────────────────
    try:
        st.markdown(
            '<div class="alc-header-label">INTELLIGENCE PLATFORM</div>'
            '<div class="alc-hero-title">Alert <span style="color:#ef4444">Center</span></div>'
            '<div class="alc-hero-sub">Real-time threshold monitoring &amp; notifications</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        st.subheader("Alert Center")

    st.markdown("<div style='margin-top:18px'></div>", unsafe_allow_html=True)

    # ── Derive active alerts ──────────────────────────────────────────────────
    try:
        scanned = _scan_triggered_alerts(freight_data, insights, stock_data, macro_data)
        if scanned:
            st.session_state["active_alerts_cache"] = scanned
        active_alerts = st.session_state["active_alerts_cache"]
        # Fall back to mock if nothing real was found
        if not active_alerts:
            active_alerts = _mock_history()[:3]
    except Exception:
        active_alerts = _mock_history()[:3]

    dismissed_ids = st.session_state.get("alert_dismissed", set())
    visible_alerts = [
        a for i, a in enumerate(active_alerts)
        if i not in dismissed_ids and not a.get("dismissed", False)
    ]

    # ── Stats row ─────────────────────────────────────────────────────────────
    try:
        rules          = st.session_state["user_alerts"]
        active_count   = len(visible_alerts)
        rules_count    = sum(1 for r in rules if r.get("enabled", True))
        history        = st.session_state.get("alert_history", [])
        last_triggered = history[0].get("triggered_at", "") if history else ""

        col1, col2, col3 = st.columns(3, gap="medium")

        with col1:
            crit_color = C_LOW if active_count > 0 else C_HIGH
            st.markdown(
                f'<div class="alc-stat-card">'
                f'  <div class="alc-stat-num" style="color:{crit_color}">{active_count}</div>'
                f'  <div class="alc-stat-lbl">Total Active Alerts</div>'
                f'  <div class="alc-stat-sub">{"Requires attention" if active_count else "All clear"}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with col2:
            st.markdown(
                f'<div class="alc-stat-card">'
                f'  <div class="alc-stat-num" style="color:{C_ACCENT}">{rules_count}</div>'
                f'  <div class="alc-stat-lbl">Active Rules</div>'
                f'  <div class="alc-stat-sub">of {len(rules)} configured</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with col3:
            last_ago = _time_ago(last_triggered) if last_triggered else "—"
            st.markdown(
                f'<div class="alc-stat-card">'
                f'  <div class="alc-stat-num" style="color:{C_MOD};font-size:1.35rem;padding-top:6px">'
                f'    {last_ago}'
                f'  </div>'
                f'  <div class="alc-stat-lbl">Last Triggered</div>'
                f'  <div class="alc-stat-sub">{_fmt_dt(last_triggered) if last_triggered else "No history yet"}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception:
        pass

    st.markdown("<div style='margin-top:24px'></div>", unsafe_allow_html=True)
    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    #  SECTION 1 — ALERT CONFIGURATION
    # ─────────────────────────────────────────────────────────────────────────
    try:
        with st.expander("⚙️  Alert Configuration — Create New Rule", expanded=False):
            st.markdown(
                '<div style="font-size:0.78rem;color:#94a3b8;margin-bottom:14px">'
                'Define a new threshold rule. Alerts are evaluated on each data refresh.'
                '</div>',
                unsafe_allow_html=True,
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
                        help="Determines card color and notification urgency.",
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
        pass

    st.markdown("<div style='margin-top:6px'></div>", unsafe_allow_html=True)

    # ─────────────────────────────────────────────────────────────────────────
    #  SECTION 2 — ACTIVE ALERTS PANEL
    # ─────────────────────────────────────────────────────────────────────────
    try:
        st.markdown(
            '<div class="alc-section-label">📡 Active Alerts</div>',
            unsafe_allow_html=True,
        )

        if not visible_alerts:
            st.markdown(
                f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};'
                f'border-radius:10px;padding:28px;text-align:center;'
                f'color:{C_TEXT3};font-size:0.85rem">'
                f'<span style="font-size:1.6rem">✅</span><br/>'
                f'<strong style="color:{C_HIGH}">All clear.</strong> No alerts are currently active.'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            for idx, alert in enumerate(visible_alerts):
                try:
                    st.markdown(_alert_card_html(alert), unsafe_allow_html=True)
                    dcol, _ = st.columns([1, 5])
                    with dcol:
                        if st.button(
                            "Dismiss",
                            key=f"dismiss_alert_{idx}",
                            use_container_width=True,
                        ):
                            st.session_state["alert_dismissed"].add(idx)
                            # Add to history
                            alert_copy = dict(alert)
                            alert_copy["dismissed"] = True
                            hist = st.session_state.get("alert_history", [])
                            hist.insert(0, alert_copy)
                            st.session_state["alert_history"] = hist[:40]
                            st.rerun()
                except Exception:
                    pass
    except Exception:
        pass

    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    #  SECTION 3 — ALERT HISTORY TABLE
    # ─────────────────────────────────────────────────────────────────────────
    try:
        st.markdown(
            '<div class="alc-section-label">🗂️ Alert History (Last 20)</div>',
            unsafe_allow_html=True,
        )

        history = st.session_state.get("alert_history", [])
        if not history:
            history = _mock_history()
            st.session_state["alert_history"] = history

        display_hist = history[:20]

        # Build rows HTML in one pass
        rows_html = ""
        for alert in display_hist:
            sev    = alert.get("severity", "Info")
            color  = _SEV_COLOR.get(sev, C_TEXT3)
            icon   = _SEV_ICON.get(sev, "⚪")
            m_icon = _METRIC_ICONS.get(alert.get("metric", ""), "📋")
            ts     = _fmt_dt(alert.get("triggered_at", ""))
            dis    = alert.get("dismissed", False)
            ack_html = (
                '<span style="font-size:0.65rem;color:#64748b">Dismissed</span>'
                if dis else
                '<span style="font-size:0.65rem;color:#10b981;font-weight:700">Active</span>'
            )
            rows_html += (
                f'<div class="alc-hist-row">'
                f'  <span style="font-size:1.1rem">{icon}</span>'
                f'  <div style="flex:1;min-width:0">'
                f'    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">'
                f'      <span style="font-size:0.72rem;font-weight:700;color:{color}">{sev}</span>'
                f'      <span style="font-size:0.68rem;color:{C_TEXT3}">{m_icon} {alert.get("metric","")}</span>'
                f'      {ack_html}'
                f'    </div>'
                f'    <div style="font-size:0.82rem;font-weight:600;color:{C_TEXT};margin:2px 0">'
                f'      {alert.get("name","")}'
                f'    </div>'
                f'    <div class="alc-hist-ts">{ts}</div>'
                f'  </div>'
                f'</div>'
            )

        st.markdown(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-radius:10px;padding:8px 14px">'
            f'{rows_html}'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass

    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    #  SECTION 4 — NOTIFICATION SETTINGS
    # ─────────────────────────────────────────────────────────────────────────
    try:
        with st.expander("📧  Notification Settings", expanded=False):
            nc1, nc2 = st.columns(2, gap="medium")

            with nc1:
                email_val = st.text_input(
                    "Notification Email",
                    value=st.session_state.get("notif_email", ""),
                    placeholder="you@example.com",
                    key="notif_email_input",
                    help="Receive alert digests and critical notifications here.",
                )
                freq_val = st.selectbox(
                    "Notification Frequency",
                    options=["Immediate", "Hourly Digest", "Daily Digest", "Weekly Summary"],
                    index=["Immediate", "Hourly Digest", "Daily Digest", "Weekly Summary"].index(
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
                st.markdown(
                    f'<div style="font-size:0.78rem;color:{C_TEXT3};margin-top:10px;line-height:1.6">'
                    f'Digest emails summarise all triggered alerts into a single message '
                    f'at the configured frequency. Critical alerts are always sent immediately '
                    f'when <em>Immediate</em> is selected.'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            save_notif = st.button(
                "Save Notification Settings",
                key="save_notif_btn",
                type="primary",
            )
            if save_notif:
                try:
                    st.session_state["notif_email"]  = email_val
                    st.session_state["notif_freq"]   = freq_val
                    st.session_state["notif_digest"] = digest_val
                    st.success("Notification settings saved.")
                except Exception as exc:
                    st.error(f"Could not save settings: {exc}")
    except Exception:
        pass

    st.markdown("<div style='margin-top:6px'></div>", unsafe_allow_html=True)

    # ─────────────────────────────────────────────────────────────────────────
    #  SECTION 5 — RULES MANAGEMENT
    # ─────────────────────────────────────────────────────────────────────────
    try:
        st.markdown(
            '<div class="alc-section-label">📋 Rules Management</div>',
            unsafe_allow_html=True,
        )

        rules = st.session_state.get("user_alerts", _default_rules())
        if not rules:
            rules = _default_rules()
            st.session_state["user_alerts"] = rules

        # Header row
        st.markdown(
            f'<div style="display:flex;gap:8px;padding:6px 10px;'
            f'font-size:0.65rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;'
            f'border-bottom:1px solid {C_BORDER}">'
            f'  <span style="flex:2">Rule Name</span>'
            f'  <span style="flex:1">Metric</span>'
            f'  <span style="flex:1">Threshold</span>'
            f'  <span style="flex:1">Condition</span>'
            f'  <span style="flex:1">Severity</span>'
            f'  <span style="width:90px;text-align:center">Email</span>'
            f'  <span style="width:80px;text-align:center">Enabled</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        for i, rule in enumerate(rules):
            try:
                sev    = rule.get("severity", "Info")
                color  = _SEV_COLOR.get(sev, C_TEXT3)
                m_icon = _METRIC_ICONS.get(rule.get("metric", ""), "📋")

                r1, r2, r3, r4, r5, r6, r7 = st.columns(
                    [2, 1, 1, 1, 1, 0.7, 0.6], gap="small"
                )

                with r1:
                    st.markdown(
                        f'<div style="font-size:0.83rem;font-weight:600;color:{C_TEXT};'
                        f'padding-top:6px">{rule.get("name","")}</div>',
                        unsafe_allow_html=True,
                    )
                with r2:
                    st.markdown(
                        f'<div style="font-size:0.75rem;color:{C_TEXT2};padding-top:6px">'
                        f'{m_icon} {rule.get("metric","")}</div>',
                        unsafe_allow_html=True,
                    )
                with r3:
                    st.markdown(
                        f'<div style="font-size:0.78rem;color:{C_TEXT};padding-top:6px;font-weight:600">'
                        f'{rule.get("threshold","—")}</div>',
                        unsafe_allow_html=True,
                    )
                with r4:
                    st.markdown(
                        f'<div style="font-size:0.75rem;color:{C_TEXT2};padding-top:6px">'
                        f'{rule.get("condition","")}</div>',
                        unsafe_allow_html=True,
                    )
                with r5:
                    st.markdown(
                        f'<div style="font-size:0.75rem;font-weight:700;color:{color};padding-top:6px">'
                        f'{sev}</div>',
                        unsafe_allow_html=True,
                    )
                with r6:
                    email_icon = "✉️" if rule.get("email_notify") else "—"
                    st.markdown(
                        f'<div style="text-align:center;font-size:0.8rem;padding-top:6px">'
                        f'{email_icon}</div>',
                        unsafe_allow_html=True,
                    )
                with r7:
                    enabled_new = st.toggle(
                        "On",
                        value=rule.get("enabled", True),
                        key=f"rule_toggle_{rule.get('id',i)}_{i}",
                        label_visibility="collapsed",
                    )
                    st.session_state["user_alerts"][i]["enabled"] = enabled_new

                st.markdown(
                    f'<div style="height:1px;background:{C_BORDER};margin:2px 0"></div>',
                    unsafe_allow_html=True,
                )
            except Exception:
                pass

    except Exception:
        pass
