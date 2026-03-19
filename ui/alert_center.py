"""Reusable alert display panel for the Ship Tracker UI.

Integration note for tab_results.py
-------------------------------------
Another agent owns tab_results.py. When that agent is ready to integrate,
add the following near the top of the `render()` function:

    from ui.alert_center import render_alert_panel
    render_alert_panel(alerts, compact=True)

where `alerts` is the list[ShippingAlert] returned by engine.alert_engine.generate_alerts().
"""
from __future__ import annotations

import streamlit as st

from engine.alert_engine import ShippingAlert, get_alert_summary, group_alerts_by_severity


# ── Color constants (mirrors components.py palette) ──────────────────────────

C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

SEVERITY_ORDER = ["CRITICAL", "WARNING", "INFO"]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _hex_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _format_value(v: float) -> str:
    """Format a numeric value for display — rates as $, scores as %."""
    if v > 100:
        return f"${v:,.0f}"
    return f"{v:.1%}"


def _alert_card_html(alert: ShippingAlert, compact: bool = False) -> str:
    """Return HTML for a single alert card."""
    color     = alert.color
    bg        = _hex_rgba(color, 0.07)
    border_bg = _hex_rgba(color, 0.20)
    badge_bg  = _hex_rgba(color, 0.15)

    current_str   = _format_value(alert.current_value)  if alert.current_value   else "—"
    threshold_str = _format_value(alert.threshold_value) if alert.threshold_value else "—"
    dev_sign      = "+" if alert.pct_deviation >= 0 else ""
    dev_str       = f"{dev_sign}{alert.pct_deviation:.1f}%"

    # Truncate triggered_at to readable local time
    ts = alert.triggered_at
    try:
        ts = ts[:19].replace("T", " ") + " UTC"
    except Exception:
        pass

    if compact:
        # Mini card — title + message + entity badge only
        return f"""
        <div style="
            background:{bg};
            border:1px solid {border_bg};
            border-left:4px solid {color};
            border-radius:8px;
            padding:10px 14px;
            margin-bottom:8px;
        ">
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px">
            <span style="font-size:1rem">{alert.icon}</span>
            <span style="font-size:0.88rem; font-weight:700; color:{C_TEXT}">{alert.title}</span>
            <span style="
                margin-left:auto;
                background:{badge_bg};
                color:{color};
                border:1px solid {border_bg};
                border-radius:999px;
                font-size:0.65rem;
                font-weight:700;
                padding:2px 8px;
            ">{alert.entity_name}</span>
          </div>
          <div style="font-size:0.78rem; color:{C_TEXT2}; line-height:1.5; margin-left:28px">
            {alert.message}
          </div>
        </div>
        """

    # Full card
    return f"""
    <div style="
        background:{bg};
        border:1px solid {border_bg};
        border-left:4px solid {color};
        border-radius:10px;
        padding:16px 20px;
        margin-bottom:10px;
    ">
      <!-- Header row -->
      <div style="display:flex; align-items:flex-start; gap:10px; margin-bottom:8px">
        <span style="font-size:1.2rem; line-height:1.3">{alert.icon}</span>
        <div style="flex:1">
          <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap">
            <span style="font-size:0.94rem; font-weight:700; color:{C_TEXT}">{alert.title}</span>
            <span style="
                background:{badge_bg};
                color:{color};
                border:1px solid {_hex_rgba(color, 0.3)};
                border-radius:999px;
                font-size:0.65rem;
                font-weight:700;
                padding:2px 8px;
            ">{alert.entity_name}</span>
            <span style="
                background:rgba(255,255,255,0.05);
                color:{C_TEXT3};
                border-radius:4px;
                font-size:0.62rem;
                padding:2px 6px;
            ">{alert.alert_type}</span>
          </div>
          <div style="font-size:0.78rem; color:{C_TEXT2}; margin-top:5px; line-height:1.6">
            {alert.message}
          </div>
        </div>
      </div>

      <!-- Metrics row -->
      <div style="
          display:flex;
          gap:20px;
          background:rgba(255,255,255,0.03);
          border-radius:6px;
          padding:8px 12px;
          margin:8px 0;
          flex-wrap:wrap;
      ">
        <div>
          <div style="font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em">
            Current
          </div>
          <div style="font-size:0.88rem; font-weight:700; color:{color}">{current_str}</div>
        </div>
        <div>
          <div style="font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em">
            Threshold
          </div>
          <div style="font-size:0.88rem; font-weight:600; color:{C_TEXT2}">{threshold_str}</div>
        </div>
        <div>
          <div style="font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em">
            Deviation
          </div>
          <div style="font-size:0.88rem; font-weight:600; color:{color}">{dev_str}</div>
        </div>
        <div style="margin-left:auto; text-align:right">
          <div style="font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em">
            Triggered
          </div>
          <div style="font-size:0.72rem; color:{C_TEXT3}">{ts}</div>
        </div>
      </div>

      <!-- Suggested action -->
      <div style="
          display:flex;
          align-items:flex-start;
          gap:8px;
          background:{_hex_rgba(color, 0.05)};
          border:1px solid {_hex_rgba(color, 0.15)};
          border-radius:6px;
          padding:8px 12px;
          font-size:0.78rem;
          color:{C_TEXT2};
          line-height:1.5;
      ">
        <span style="color:{color}; font-weight:700; flex-shrink:0">Action:</span>
        <span>{alert.suggested_action}</span>
      </div>
    </div>
    """


def _summary_header_html(summary: dict) -> str:
    """Return HTML for the summary banner line."""
    critical_color = "#ef4444"
    warning_color  = "#f59e0b"
    info_color     = "#3b82f6"

    parts = []
    if summary["critical"]:
        parts.append(
            f'<span style="color:{critical_color}; font-weight:700">'
            f'🚨 {summary["critical"]} Critical</span>'
        )
    if summary["warning"]:
        parts.append(
            f'<span style="color:{warning_color}; font-weight:700">'
            f'⚠️ {summary["warning"]} Warning{"s" if summary["warning"] != 1 else ""}</span>'
        )
    if summary["info"]:
        parts.append(
            f'<span style="color:{info_color}; font-weight:700">'
            f'ℹ️ {summary["info"]} Info</span>'
        )

    if not parts:
        return (
            '<div style="font-size:0.85rem; color:#64748b; padding:4px 0">'
            'No active alerts.'
            '</div>'
        )

    separator = ' <span style="color:#475569"> · </span> '
    return (
        f'<div style="font-size:0.88rem; padding:4px 0">'
        f'{separator.join(parts)}'
        f'</div>'
    )


# ── Public render function ────────────────────────────────────────────────────

def render_alert_panel(alerts: list[ShippingAlert], compact: bool = False) -> None:
    """Render the alert panel inside the current Streamlit context.

    Parameters
    ----------
    alerts:
        List of ShippingAlert objects from engine.alert_engine.generate_alerts().
    compact:
        If True, render a collapsed summary row + top-3 mini cards.
        If False, render the full alert center grouped by severity.
    """
    if not alerts:
        if not compact:
            st.markdown(
                '<div style="font-size:0.85rem; color:#64748b; padding:8px 0">'
                'No active alerts at this time.'
                '</div>',
                unsafe_allow_html=True,
            )
        return

    summary = get_alert_summary(alerts)
    grouped = group_alerts_by_severity(alerts)

    # ── Compact view ──────────────────────────────────────────────────────────
    if compact:
        # Summary header
        st.markdown(_summary_header_html(summary), unsafe_allow_html=True)

        # Top 3 alerts across all severity levels
        top_alerts: list[ShippingAlert] = []
        for sev in SEVERITY_ORDER:
            top_alerts.extend(grouped.get(sev, []))
        top_alerts = top_alerts[:3]

        cards_html = "".join(_alert_card_html(a, compact=True) for a in top_alerts)
        if cards_html:
            st.markdown(cards_html, unsafe_allow_html=True)
        return

    # ── Full alert center ─────────────────────────────────────────────────────
    # Section title
    st.markdown(
        f"""
        <div style="
            display:flex;
            align-items:center;
            justify-content:space-between;
            margin-bottom:12px;
        ">
          <div style="font-size:1.05rem; font-weight:700; color:{C_TEXT}">
            Alert Center
          </div>
          {_summary_header_html(summary)}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Render each severity group in CRITICAL → WARNING → INFO order
    for severity in SEVERITY_ORDER:
        group = grouped.get(severity, [])
        if not group:
            continue

        color = {"CRITICAL": "#ef4444", "WARNING": "#f59e0b", "INFO": "#3b82f6"}.get(
            severity, "#94a3b8"
        )
        icon  = {"CRITICAL": "🚨", "WARNING": "⚠️", "INFO": "ℹ️"}.get(severity, "")
        label = f"{icon} {severity.title()} ({len(group)})"

        st.markdown(
            f'<div style="font-size:0.78rem; font-weight:700; color:{color}; '
            f'text-transform:uppercase; letter-spacing:0.08em; '
            f'margin: 16px 0 8px 0">{label}</div>',
            unsafe_allow_html=True,
        )

        cards_html = "".join(_alert_card_html(a, compact=False) for a in group)
        st.markdown(cards_html, unsafe_allow_html=True)
