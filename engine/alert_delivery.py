"""External alert delivery — push ShippingAlerts out of the app.

The alert engine in ``engine.alert_engine_v2`` persists alerts to SQLite
and surfaces them in the UI. This module adds an outbound channel so
alerts can also be pushed to Slack, Email, SMS (via Twilio), a generic
HTTP webhook, Discord, or PagerDuty.

Design notes
------------
* ``DeliveryChannel`` is a typed config row. ``kind`` is a free-form
  string — "slack", "email", "sms", "webhook", "discord", and
  "pagerduty" are supported. ``target`` is the Slack incoming-webhook
  URL for slack channels, the recipient email address for email
  channels, the E.164 phone number (e.g. ``+15551234567``) for sms
  channels, the destination URL for webhook channels, the Discord
  webhook URL for discord channels, and the PagerDuty integration
  key for pagerduty channels.
* ``severity_threshold`` uses ``alert_engine_v2._SEVERITY_ORDER`` —
  CRITICAL (0) < HIGH (1) < MEDIUM (2) < LOW (3). A channel with
  threshold "MEDIUM" delivers MEDIUM/HIGH/CRITICAL alerts and skips
  LOW.
* ``format_slack_payload`` / ``format_email_payload`` /
  ``format_sms_payload`` are pure functions so they can be tested
  without touching the network. ``deliver_alert`` opens the transport
  (HTTPS for slack + Twilio, SMTP for email) with a 10s timeout and
  always returns a ``DeliveryResult`` — it never raises so callers can
  iterate over many alerts without one outage breaking the whole batch.
* SMTP + Twilio credentials are read from environment variables (or
  Streamlit secrets) at delivery time via ``_get_smtp_config`` /
  ``_get_twilio_config``. They never live in the ``delivery_channels``
  row.
* Channel persistence lives in the SQLite ``delivery_channels`` table
  (schema v2). Channel configs are user-authored config, parallel to
  ``alert_rules``.
* ``digest_mode`` (schema v6) lets a channel batch the eligible alerts
  from a single ``deliver_pending`` window into ONE digest delivery
  instead of POSTing one message per alert. Default is ``"immediate"``
  (legacy behaviour, one-per-alert). ``"daily"`` collapses everything
  into a single ``deliver_digest`` call. The cron/worker that calls
  ``deliver_pending`` decides the cadence — this module only chooses
  between per-alert and batched dispatch.
* ``quiet_start`` / ``quiet_end`` / ``quiet_override_critical`` (schema
  v13) let an operator silence a channel during a daily HH:MM window —
  e.g. "no deliveries between 22:00 and 07:00 UTC". When the time-of-
  day check at delivery time falls inside the window, the alert is
  suppressed with a ``"channel in quiet hours"`` failure result; the
  alert remains in SQLite, this is suppress-only with no queue-and-
  drain. ``quiet_override_critical=True`` (the default) lets CRITICAL
  alerts page through anyway.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import smtplib
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from urllib.parse import urlparse

import requests
from loguru import logger

from engine.alert_engine_v2 import ShippingAlert, _SEVERITY_ORDER, _row_to_alert


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DeliveryChannel:
    channel_id: str          # UUID-ish identifier
    name: str                # human-readable label, e.g. "Trading desk Slack"
    kind: str                # "slack" | "email" | "sms" | "webhook" | "discord" | "pagerduty"
    target: str              # webhook URL / email addr / E.164 phone / generic URL / Discord webhook URL / PagerDuty integration key
    severity_threshold: str  # "CRITICAL"|"HIGH"|"MEDIUM"|"LOW"
    enabled: bool = True
    created_at: str = ""     # ISO timestamp, populated by save_channel
    # Delivery cadence. "immediate" (default) sends one delivery per alert
    # — preserves the original behaviour exactly. "daily" batches every
    # eligible alert since ``since`` into ONE digest delivery; the
    # caller's cron / worker decides when ``deliver_pending`` runs, this
    # field only changes how it dispatches the alerts it finds.
    digest_mode: str = "immediate"  # "immediate" | "daily"
    # Quiet-hours window (v13). When ``quiet_start`` and ``quiet_end``
    # are both non-empty HH:MM UTC strings, ``deliver_alert`` suppresses
    # deliveries whose wall-clock time-of-day falls inside the window.
    # Empty strings disable the check (the legacy default — "no quiet
    # hours configured"). ``quiet_override_critical`` lets CRITICAL
    # alerts page through anyway; flip it to False to mute even those.
    # The check is purely time-of-day — no per-day calendar; that's a
    # follow-up.
    quiet_start: str = ""                 # "HH:MM" UTC, "" = disabled
    quiet_end: str = ""                   # "HH:MM" UTC, "" = disabled
    quiet_override_critical: bool = True  # CRITICAL bypasses the window
    # Per-channel monthly delivery budget (v25). 0 = unlimited (the
    # legacy default — pre-v25 channels behave EXACTLY as today). A
    # positive cap suppresses further deliveries once the channel's
    # per-calendar-month counter reaches it; the counter resets on the
    # first of every month or via ``reset_channel_usage``. Counter
    # state lives in kv_state, not on the channel row, so monthly
    # rollover is cheap (a new key) and operator reset is one DELETE.
    monthly_budget: int = 0               # 0 = unlimited


@dataclass
class DeliveryResult:
    success: bool
    status_code: int = 0     # 0 when no HTTP exchange happened
    error_msg: str = ""      # empty when success=True


# ─────────────────────────────────────────────────────────────────────────────
#  Severity gating
# ─────────────────────────────────────────────────────────────────────────────

def _meets_threshold(alert_severity: str, channel_threshold: str) -> bool:
    """True iff ``alert_severity`` is at least as severe as
    ``channel_threshold`` per ``_SEVERITY_ORDER``.

    Recall: lower number = more severe. CRITICAL=0, LOW=3. So a channel
    with threshold MEDIUM (2) accepts alerts whose order index is <= 2,
    i.e. CRITICAL, HIGH, MEDIUM.
    """
    a = _SEVERITY_ORDER.get(alert_severity, 99)
    t = _SEVERITY_ORDER.get(channel_threshold, 99)
    return a <= t


# ─────────────────────────────────────────────────────────────────────────────
#  Quiet-hours gating (schema v13)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_hhmm_to_minutes(hhmm: str) -> Optional[int]:
    """Parse an ``"HH:MM"`` string into total minutes-since-midnight, or
    return ``None`` on any failure. Never raises.

    Accepts the canonical zero-padded form (``"22:00"``) plus the
    unpadded form (``"7:30"``); rejects anything outside 0-23 hours /
    0-59 minutes so a typo doesn't silently match a different window.
    """
    if not isinstance(hhmm, str) or not hhmm:
        return None
    try:
        # ``strptime`` would also accept multi-line / surrounding spaces;
        # split-and-int is stricter and gives clean error handling.
        parts = hhmm.split(":")
        if len(parts) != 2:
            return None
        hours = int(parts[0])
        minutes = int(parts[1])
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            return None
        return hours * 60 + minutes
    except (TypeError, ValueError):
        return None


def _is_in_quiet_window(channel: DeliveryChannel, now_utc: datetime) -> bool:
    """True when ``now_utc`` falls inside ``channel``'s quiet window.

    Behaviour:
      * Returns ``False`` when either ``quiet_start`` or ``quiet_end`` is
        empty (i.e. no window configured).
      * Parses both as ``HH:MM`` UTC. Returns ``False`` on any parse
        failure (defensive — a malformed string must never silently
        suppress deliveries).
      * Normal window (``quiet_start < quiet_end``, e.g. 10:00 → 12:00):
        returns ``True`` for ``quiet_start <= now < quiet_end``.
      * Wraparound window (``quiet_start > quiet_end``, e.g. 22:00 →
        07:00 spanning midnight): returns ``True`` when ``now >=
        quiet_start`` OR ``now < quiet_end``.
      * Degenerate window (``quiet_start == quiet_end``): returns
        ``False`` — treats the window as having zero duration rather
        than a 24h silence, which is the safer default for a config
        someone might have left half-edited.

    Never raises — returns ``False`` on any unexpected error so a delivery
    can't be silently swallowed by a config bug.
    """
    try:
        start_raw = getattr(channel, "quiet_start", "") or ""
        end_raw = getattr(channel, "quiet_end", "") or ""
        if not start_raw or not end_raw:
            return False

        start_min = _parse_hhmm_to_minutes(start_raw)
        end_min = _parse_hhmm_to_minutes(end_raw)
        if start_min is None or end_min is None:
            return False

        if start_min == end_min:
            # Zero-duration window — treat as "not configured" so a
            # half-edited config doesn't silence everything 24/7.
            return False

        now_min = now_utc.hour * 60 + now_utc.minute

        if start_min < end_min:
            # Normal same-day window: [start, end).
            return start_min <= now_min < end_min
        # Wraparound (e.g. 22:00 → 07:00): we are in the window if we
        # are at or past start OR before end.
        return now_min >= start_min or now_min < end_min
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Slack payload formatting
# ─────────────────────────────────────────────────────────────────────────────

_SEVERITY_COLOR = {
    "CRITICAL": "#d73a49",  # red
    "HIGH":     "#f66a0a",  # orange
    "MEDIUM":   "#f1c40f",  # yellow
    "LOW":      "#6a737d",  # gray
}


def format_slack_payload(alert: ShippingAlert) -> dict:
    """Build the Slack incoming-webhook payload for a ShippingAlert.

    Returns a dict matching Slack's message JSON shape:
      - ``text``: short fallback string (for push notifications and
        non-block-rendering clients)
      - ``attachments``: a single attachment colored by severity, with
        ``blocks`` carrying the alert title (header), the body
        (section), and a fields block of value / threshold / change_pct
    """
    color = _SEVERITY_COLOR.get(alert.severity, _SEVERITY_COLOR["LOW"])
    title = f"[{alert.severity}] {alert.title}"

    fields: list[dict] = []
    fields.append({"type": "mrkdwn", "text": f"*Value*\n{alert.value:,.2f}"})
    fields.append({"type": "mrkdwn", "text": f"*Threshold*\n{alert.threshold:,.2f}"})
    fields.append({"type": "mrkdwn", "text": f"*Change %*\n{alert.change_pct:+.2f}%"})
    fields.append({"type": "mrkdwn", "text": f"*Type*\n{alert.alert_type}"})

    # Optional context fields (ticker / route / port) — only included when set.
    context_bits: list[str] = []
    if alert.ticker:
        context_bits.append(f"Ticker: `{alert.ticker}`")
    if alert.route_id:
        context_bits.append(f"Route: `{alert.route_id}`")
    if alert.port_locode:
        context_bits.append(f"Port: `{alert.port_locode}`")
    if alert.created_at:
        context_bits.append(f"At: {alert.created_at}")

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title, "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": alert.body},
        },
        {"type": "section", "fields": fields},
    ]
    if context_bits:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": " | ".join(context_bits)}],
        })

    return {
        "text": title,  # fallback summary
        "attachments": [
            {
                "color": color,
                "blocks": blocks,
            }
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Email payload formatting
# ─────────────────────────────────────────────────────────────────────────────

def format_email_payload(alert: ShippingAlert) -> dict:
    """Build the email payload (subject + html_body + text_body) for a
    ``ShippingAlert``.

    Returns a dict with three string keys:
      - ``subject``: ``"[SEVERITY] title"``
      - ``html_body``: inline-styled HTML using the same severity colour
        palette as the Slack payload (``_SEVERITY_COLOR``)
      - ``text_body``: plain-text fallback for clients that don't render
        HTML (or for sanity in mail logs)

    Every alert-derived field interpolated into ``html_body`` is HTML-
    escaped: title/body/alert_type/ticker/route/port originate from upstream
    feeds (port names, tickers) and must not inject markup into the
    recipient's mail client. The plain-text body and the subject (an SMTP
    header — the email lib rejects embedded CRLF) need no escaping.
    """
    from html import escape

    color = _SEVERITY_COLOR.get(alert.severity, _SEVERITY_COLOR["LOW"])
    subject = f"[{alert.severity}] {alert.title}"

    # ── Optional context bits ────────────────────────────────────────
    context_lines: list[tuple[str, str]] = []
    if alert.ticker:
        context_lines.append(("Ticker", alert.ticker))
    if alert.route_id:
        context_lines.append(("Route", alert.route_id))
    if alert.port_locode:
        context_lines.append(("Port", alert.port_locode))
    if alert.created_at:
        context_lines.append(("At", alert.created_at))

    # ── HTML body ────────────────────────────────────────────────────
    fields_html = (
        f"<tr><td style='padding:4px 12px 4px 0;color:#586069;'>Value</td>"
        f"<td style='padding:4px 0;'><b>{alert.value:,.2f}</b></td></tr>"
        f"<tr><td style='padding:4px 12px 4px 0;color:#586069;'>Threshold</td>"
        f"<td style='padding:4px 0;'><b>{alert.threshold:,.2f}</b></td></tr>"
        f"<tr><td style='padding:4px 12px 4px 0;color:#586069;'>Change %</td>"
        f"<td style='padding:4px 0;'><b>{alert.change_pct:+.2f}%</b></td></tr>"
        f"<tr><td style='padding:4px 12px 4px 0;color:#586069;'>Type</td>"
        f"<td style='padding:4px 0;'><b>{escape(str(alert.alert_type))}</b></td></tr>"
    )

    context_html = ""
    if context_lines:
        rows = "".join(
            f"<tr><td style='padding:2px 12px 2px 0;color:#586069;'>{escape(str(label))}</td>"
            f"<td style='padding:2px 0;'>{escape(str(value))}</td></tr>"
            for label, value in context_lines
        )
        context_html = (
            "<table style='border-collapse:collapse;margin-top:12px;font-size:13px;color:#24292e;'>"
            f"{rows}</table>"
        )

    html_body = (
        "<html><body style='font-family:Helvetica,Arial,sans-serif;color:#24292e;"
        "background:#ffffff;margin:0;padding:0;'>"
        "<div style='max-width:640px;margin:0 auto;padding:24px;'>"
        f"<div style='border-left:6px solid {color};padding:12px 16px;background:#fafbfc;'>"
        f"<div style='font-size:12px;font-weight:bold;letter-spacing:1px;color:{color};'>"
        f"{escape(str(alert.severity))}</div>"
        f"<h2 style='margin:4px 0 0 0;font-size:20px;color:#24292e;'>{escape(str(alert.title))}</h2>"
        "</div>"
        f"<p style='font-size:14px;line-height:1.5;margin:16px 0;'>{escape(str(alert.body))}</p>"
        "<table style='border-collapse:collapse;font-size:13px;color:#24292e;'>"
        f"{fields_html}</table>"
        f"{context_html}"
        "</div></body></html>"
    )

    # ── Plain-text body ──────────────────────────────────────────────
    text_lines = [
        subject,
        "=" * len(subject),
        "",
        alert.body,
        "",
        f"Value:     {alert.value:,.2f}",
        f"Threshold: {alert.threshold:,.2f}",
        f"Change %:  {alert.change_pct:+.2f}%",
        f"Type:      {alert.alert_type}",
    ]
    if context_lines:
        text_lines.append("")
        for label, value in context_lines:
            text_lines.append(f"{label}: {value}")
    text_body = "\n".join(text_lines)

    return {
        "subject": subject,
        "html_body": html_body,
        "text_body": text_body,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  SMS payload formatting
# ─────────────────────────────────────────────────────────────────────────────

# Twilio concatenates up to 1600 chars, but each 160-char segment costs a
# message. Cap the body at ~280 chars (2 segments) to keep costs predictable
# while still leaving room for the severity-prefixed title.
_SMS_BODY_MAX_CHARS = 280


def format_sms_payload(alert: ShippingAlert) -> dict:
    """Build the Twilio SMS body for a ``ShippingAlert``.

    SMS has a 160-char per-segment limit but Twilio concatenates up to
    1600 chars. We format as:

        [SEVERITY] {title}
        {body truncated to ~280 chars}
        {optional footer with value/threshold}

    and return ``{"body": str}`` for symmetry with ``format_slack_payload``
    and ``format_email_payload``.
    """
    header = f"[{alert.severity}] {alert.title}"

    # Truncate the body to keep total length close to 2 segments.
    body_text = alert.body or ""
    if len(body_text) > _SMS_BODY_MAX_CHARS:
        # Reserve 3 chars for the ellipsis so the truncated text + "..."
        # fits inside the cap.
        body_text = body_text[: _SMS_BODY_MAX_CHARS - 3] + "..."

    parts: list[str] = [header]
    if body_text:
        parts.append(body_text)

    # Footer carries value/threshold only when at least one is non-zero so
    # alerts that don't trade in numeric thresholds don't get a noisy
    # "Value 0.00 / Threshold 0.00" tail.
    if alert.value != 0 or alert.threshold != 0:
        parts.append(f"Value {alert.value:,.2f} / Threshold {alert.threshold:,.2f}")

    return {"body": "\n".join(parts)}


# ─────────────────────────────────────────────────────────────────────────────
#  Generic webhook payload formatting
# ─────────────────────────────────────────────────────────────────────────────

def format_webhook_payload(alert: ShippingAlert) -> dict:
    """Build a generic HTTP-webhook payload for a ``ShippingAlert``.

    Returns a JSON-serializable dict carrying every field on the alert plus
    a top-level ``event_type: "alert"`` discriminator so receivers can
    distinguish this schema from other event shapes they might receive.

    Unlike the Slack / Discord payloads, this format is intentionally flat
    and uncosmetic — it's meant to be consumed by automation, not displayed
    to a human.
    """
    return {
        "event_type": "alert",
        "alert_id": alert.alert_id,
        "created_at": alert.created_at,
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "title": alert.title,
        "body": alert.body,
        "ticker": alert.ticker,
        "route_id": alert.route_id,
        "port_locode": alert.port_locode,
        "value": alert.value,
        "threshold": alert.threshold,
        "change_pct": alert.change_pct,
        "acknowledged": alert.acknowledged,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Discord payload formatting
# ─────────────────────────────────────────────────────────────────────────────

# Discord uses integer-RGB for embed colors. These integers map to the
# same severity palette as ``_SEVERITY_COLOR`` (with minor rounding so
# the values stay easy to recognise across our own dashboards and the
# embed renderer).
_DISCORD_SEVERITY_COLOR = {
    "CRITICAL": 14104137,  # red
    "HIGH":     16148490,  # orange
    "MEDIUM":   15844367,  # yellow
    "LOW":      6976125,   # gray
}

_DISCORD_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
)


def format_discord_payload(alert: ShippingAlert) -> dict:
    """Build the Discord-webhook payload for a ``ShippingAlert``.

    Returns a dict matching Discord's webhook message JSON shape:
      - ``content``: short fallback string (severity-prefixed title) for
        clients that don't render embeds
      - ``embeds``: a single embed with title, description (alert body),
        an integer color matching the severity, and a ``fields`` array
        carrying Value / Threshold / Change %
    """
    color = _DISCORD_SEVERITY_COLOR.get(alert.severity, _DISCORD_SEVERITY_COLOR["LOW"])
    title = f"[{alert.severity}] {alert.title}"

    fields: list[dict] = [
        {"name": "Value", "value": f"{alert.value:,.2f}", "inline": True},
        {"name": "Threshold", "value": f"{alert.threshold:,.2f}", "inline": True},
        {"name": "Change %", "value": f"{alert.change_pct:+.2f}%", "inline": True},
        {"name": "Type", "value": alert.alert_type, "inline": True},
    ]

    # Optional context fields — only included when set.
    if alert.ticker:
        fields.append({"name": "Ticker", "value": alert.ticker, "inline": True})
    if alert.route_id:
        fields.append({"name": "Route", "value": alert.route_id, "inline": True})
    if alert.port_locode:
        fields.append({"name": "Port", "value": alert.port_locode, "inline": True})

    embed: dict = {
        "title": title,
        "description": alert.body,
        "color": color,
        "fields": fields,
    }
    if alert.created_at:
        embed["timestamp"] = alert.created_at

    return {
        "content": title,
        "embeds": [embed],
    }


# ─────────────────────────────────────────────────────────────────────────────
#  PagerDuty payload formatting
# ─────────────────────────────────────────────────────────────────────────────

# Mapping from our severity names to PagerDuty Events API v2 severities.
# PagerDuty only accepts: critical | error | warning | info.
_PAGERDUTY_SEVERITY = {
    "CRITICAL": "critical",
    "HIGH":     "error",
    "MEDIUM":   "warning",
    "LOW":      "info",
}

_PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"


def format_pagerduty_payload(alert: ShippingAlert, integration_key: str) -> dict:
    """Build the PagerDuty Events API v2 payload for a ``ShippingAlert``.

    The Events API expects:
      - ``routing_key``: the integration key (lives in the body, not the URL)
      - ``event_action``: ``"trigger"`` for new incidents
      - ``dedup_key``: stable id used to deduplicate retries (we use the
        alert UUID)
      - ``payload.summary`` / ``severity`` / ``source`` / ``component``
      - ``payload.custom_details``: free-form dict for extra context

    Severity is mapped via ``_PAGERDUTY_SEVERITY`` because PagerDuty only
    accepts ``critical | error | warning | info``.
    """
    pd_severity = _PAGERDUTY_SEVERITY.get(alert.severity, "info")
    component = alert.alert_type or "ship-tracker"

    custom_details: dict = {
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "body": alert.body,
        "value": alert.value,
        "threshold": alert.threshold,
        "change_pct": alert.change_pct,
    }
    if alert.ticker:
        custom_details["ticker"] = alert.ticker
    if alert.route_id:
        custom_details["route_id"] = alert.route_id
    if alert.port_locode:
        custom_details["port_locode"] = alert.port_locode
    if alert.created_at:
        custom_details["created_at"] = alert.created_at

    return {
        "routing_key": integration_key,
        "event_action": "trigger",
        "dedup_key": alert.alert_id,
        "payload": {
            "summary": f"[{alert.severity}] {alert.title}",
            "severity": pd_severity,
            "source": "ship-tracker",
            "component": component,
            "custom_details": custom_details,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  SMTP configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_addr: str


def _get_smtp_config() -> Optional[_SmtpConfig]:
    """Read SMTP credentials from Streamlit secrets (preferred) or env
    vars. Returns ``None`` if any required field is missing or unparsable.

    Required keys / env vars (all five must be present):
      - ``SMTP_HOST``
      - ``SMTP_PORT`` (defaults to 587 if unset; must parse as int when set)
      - ``SMTP_USER``
      - ``SMTP_PASSWORD``
      - ``SMTP_FROM_ADDRESS``

    Never raises. Same pattern as ``narration_engine._get_anthropic_key``
    (st.secrets first, then os.environ).
    """
    try:
        getters: list = []

        # st.secrets takes precedence so a deployed Streamlit app can
        # carry credentials without touching the OS environment.
        try:
            import streamlit as st
            getters.append(lambda k: str(st.secrets.get(k, "")) if st.secrets else "")
        except Exception:
            pass

        getters.append(lambda k: os.environ.get(k, ""))

        def lookup(key: str) -> str:
            for getter in getters:
                try:
                    val = getter(key)
                except Exception:
                    val = ""
                if val:
                    return str(val)
            return ""

        host = lookup("SMTP_HOST")
        user = lookup("SMTP_USER")
        password = lookup("SMTP_PASSWORD")
        from_addr = lookup("SMTP_FROM_ADDRESS")

        port_raw = lookup("SMTP_PORT")
        if port_raw:
            try:
                port = int(port_raw)
            except (TypeError, ValueError):
                return None
        else:
            port = 587  # STARTTLS default

        if not (host and user and password and from_addr):
            return None
        return _SmtpConfig(
            host=host, port=port, user=user, password=password, from_addr=from_addr
        )
    except Exception:
        # _get_smtp_config must never raise.
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Twilio configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _TwilioConfig:
    account_sid: str
    auth_token: str
    from_number: str


def _get_twilio_config() -> Optional[_TwilioConfig]:
    """Read Twilio credentials from Streamlit secrets (preferred) or env
    vars. Returns ``None`` if any required field is missing.

    Required keys / env vars (all three must be present):
      - ``TWILIO_ACCOUNT_SID``
      - ``TWILIO_AUTH_TOKEN``
      - ``TWILIO_FROM_NUMBER`` (E.164, e.g. ``+15559876543``)

    Never raises. Same pattern as ``_get_smtp_config``.
    """
    try:
        getters: list = []

        # st.secrets takes precedence so a deployed Streamlit app can
        # carry credentials without touching the OS environment.
        try:
            import streamlit as st
            getters.append(lambda k: str(st.secrets.get(k, "")) if st.secrets else "")
        except Exception:
            pass

        getters.append(lambda k: os.environ.get(k, ""))

        def lookup(key: str) -> str:
            for getter in getters:
                try:
                    val = getter(key)
                except Exception:
                    val = ""
                if val:
                    return str(val)
            return ""

        account_sid = lookup("TWILIO_ACCOUNT_SID")
        auth_token = lookup("TWILIO_AUTH_TOKEN")
        from_number = lookup("TWILIO_FROM_NUMBER")

        if not (account_sid and auth_token and from_number):
            return None
        return _TwilioConfig(
            account_sid=account_sid,
            auth_token=auth_token,
            from_number=from_number,
        )
    except Exception:
        # _get_twilio_config must never raise.
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Delivery
# ─────────────────────────────────────────────────────────────────────────────

_REQUEST_TIMEOUT_S = 10.0
_SMTP_TIMEOUT_S = 10.0
_TWILIO_API_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def _deliver_email(
    channel: DeliveryChannel,
    alert: ShippingAlert,
    config: _SmtpConfig,
) -> DeliveryResult:
    """Send ``alert`` to ``channel.target`` (recipient email) via SMTP.

    Opens an ``smtplib.SMTP`` connection, runs ``starttls()`` + ``login()``,
    sends a ``MIMEMultipart('alternative')`` carrying text + html parts.
    Returns ``DeliveryResult(success=...)`` — never raises.
    """
    payload = format_email_payload(alert)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = payload["subject"]
    msg["From"] = config.from_addr
    msg["To"] = channel.target
    msg.attach(MIMEText(payload["text_body"], "plain", "utf-8"))
    msg.attach(MIMEText(payload["html_body"], "html", "utf-8"))

    try:
        smtp = smtplib.SMTP(config.host, config.port, timeout=_SMTP_TIMEOUT_S)
    except smtplib.SMTPException as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"smtp connect: {exc}")
    except OSError as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"connection error: {exc}")
    except Exception as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"unexpected: {exc}")

    try:
        try:
            smtp.starttls()
            smtp.login(config.user, config.password)
            smtp.sendmail(config.from_addr, [channel.target], msg.as_string())
        except smtplib.SMTPAuthenticationError as exc:
            return DeliveryResult(success=False, status_code=0, error_msg=f"smtp auth: {exc}")
        except smtplib.SMTPRecipientsRefused as exc:
            return DeliveryResult(
                success=False, status_code=0, error_msg=f"recipient refused: {exc}"
            )
        except smtplib.SMTPException as exc:
            return DeliveryResult(success=False, status_code=0, error_msg=f"smtp error: {exc}")
        except OSError as exc:
            return DeliveryResult(success=False, status_code=0, error_msg=f"connection error: {exc}")
        except Exception as exc:
            return DeliveryResult(success=False, status_code=0, error_msg=f"unexpected: {exc}")
    finally:
        try:
            smtp.quit()
        except Exception:
            pass

    return DeliveryResult(success=True, status_code=0)


def _deliver_sms(
    channel: DeliveryChannel,
    alert: ShippingAlert,
    config: _TwilioConfig,
) -> DeliveryResult:
    """Send ``alert`` to ``channel.target`` (E.164 phone number) via the
    Twilio REST API.

    POSTs form-encoded ``To/From/Body`` to
    ``https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json``
    with HTTP Basic auth (sid as user, auth token as password). Returns
    ``DeliveryResult(success=True, status_code=201)`` on Twilio's
    standard 201 response. Twilio errors typically come back as 400 with
    a JSON body ``{"code", "message"}`` — we surface the ``message``
    field in ``error_msg`` when present, falling back to the raw text.
    """
    payload = format_sms_payload(alert)
    url = _TWILIO_API_URL.format(sid=config.account_sid)

    try:
        resp = requests.post(
            url,
            auth=(config.account_sid, config.auth_token),
            data={
                "To": channel.target,
                "From": config.from_number,
                "Body": payload["body"],
            },
            timeout=_REQUEST_TIMEOUT_S,
        )
    except requests.exceptions.Timeout as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"timeout: {exc}")
    except requests.exceptions.ConnectionError as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"connection error: {exc}")
    except requests.exceptions.RequestException as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"request error: {exc}")
    except Exception as exc:
        # Defence-in-depth — never let an unexpected exception propagate.
        return DeliveryResult(success=False, status_code=0, error_msg=f"unexpected: {exc}")

    status = getattr(resp, "status_code", 0)
    if status == 201:
        return DeliveryResult(success=True, status_code=201)

    # Try to surface Twilio's structured error message when the response
    # body parses as JSON; fall back to the raw text otherwise.
    error_text = ""
    try:
        data = resp.json()
        if isinstance(data, dict) and data.get("message"):
            error_text = str(data["message"])
    except Exception:
        pass
    if not error_text:
        try:
            error_text = (resp.text or "")[:500]
        except Exception:
            error_text = ""
    return DeliveryResult(
        success=False,
        status_code=status,
        error_msg=f"HTTP {status}: {error_text}" if error_text else f"HTTP {status}",
    )


# ── SSRF guard for operator-supplied webhook / slack target URLs ────────────
#
# A channel.target for kind webhook/slack is an arbitrary operator-supplied
# URL that the SERVER then POSTs to. Without a guard, an authed user can point
# it at an internal address (cloud metadata 169.254.169.254, localhost,
# RFC1918, …) and use the captured response body as an SSRF read primitive.
# We block any target that is (or resolves to) a private / loopback /
# link-local / CGNAT / reserved / unspecified / multicast address, with an
# explicit operator allowlist for the rare legitimate internal endpoint.

_SSRF_ALLOW_HOSTS_ENV = "SHIP_WEBHOOK_ALLOW_HOSTS"
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


def _allowlisted_hosts() -> set:
    """Hostnames an operator has explicitly allowed (comma-separated env)."""
    raw = os.environ.get(_SSRF_ALLOW_HOSTS_ENV, "") or ""
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _ip_is_blocked(ip_str: str) -> bool:
    """True if ``ip_str`` is in a range a server-side fetch must never reach."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → block defensively
    if (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_unspecified or ip.is_multicast
    ):
        return True
    # CGNAT (100.64/10) isn't flagged is_private on older Pythons.
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NET:
        return True
    return False


def validate_target_url(url: str, *, resolve: bool = True) -> Optional[str]:
    """Return a reason string if ``url`` is an unsafe SSRF target, else None.

    Checks: http/https scheme + a host; host not on the operator allowlist;
    literal-IP not in a blocked range; and (when ``resolve``) every resolved
    address is public. ``resolve=False`` is a hermetic save-time check
    (scheme + literal IP + localhost, no DNS); ``resolve=True`` is the
    delivery-time guard that also defends DNS-name-to-internal + rebinding.
    """
    u = str(url or "").strip()
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        return f"scheme must be http(s), got {parsed.scheme or '(none)'!r}"
    host = (parsed.hostname or "").strip()
    if not host:
        return "missing host"
    if host.lower() in _allowlisted_hosts():
        return None
    if host.lower() in ("localhost", "localhost.localdomain"):
        return "host is a loopback/internal address"
    try:
        ipaddress.ip_address(host)            # literal IP?
        return "host is a blocked internal address" if _ip_is_blocked(host) else None
    except ValueError:
        pass                                   # it's a hostname
    if not resolve:
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return None  # unresolvable → not an SSRF risk (the POST just fails)
    for info in infos:
        if _ip_is_blocked(info[4][0]):
            return "host resolves to a blocked internal address"
    return None


def _http_post_json(url: str, payload: dict) -> tuple[int, str, Optional[Exception]]:
    """Shared HTTP POST helper used by the webhook / discord / pagerduty
    delivery paths. Returns ``(status_code, body_text, exception)``. On
    success ``exception`` is ``None``. On failure ``status_code`` is 0 and
    ``exception`` carries the underlying error.
    """
    try:
        resp = requests.post(url, json=payload, timeout=_REQUEST_TIMEOUT_S)
    except requests.exceptions.Timeout as exc:
        return 0, "", exc
    except requests.exceptions.ConnectionError as exc:
        return 0, "", exc
    except requests.exceptions.RequestException as exc:
        return 0, "", exc
    except Exception as exc:
        return 0, "", exc

    status = getattr(resp, "status_code", 0)
    body = ""
    try:
        body = (resp.text or "")[:500]
    except Exception:
        pass
    return status, body, None


def _redact(text: str, *secrets: str) -> str:
    """Strip secret substrings from ``text`` before it lands in a persisted
    ``error_msg``.

    For slack/discord/webhook channels the secret IS ``channel.target`` (the
    webhook token lives in the URL path). A transport exception's ``str()``
    embeds that full URL; ``_is_retriable`` then persists the message
    verbatim to ``delivery_retry_queue.last_error`` and the UI/CLI/bulk-
    export surface it — leaking a secret the operator may have encrypted at
    rest. Replacing the target with a placeholder closes that leak.
    """
    out = str(text)
    for secret in secrets:
        s = str(secret or "")
        if s:
            out = out.replace(s, "<redacted>")
    return out


def _classify_request_exc(exc: Exception, *secrets: str) -> str:
    """Map a request-side exception onto the ``error_msg`` prefix the rest
    of the module uses ("timeout" / "connection error" / "request error" /
    "unexpected"). Keeps the wording identical to the existing Slack /
    Twilio paths.

    Any ``secrets`` (e.g. ``channel.target``) are redacted from the
    exception text so a secret URL never leaks into a persisted error
    message — the classification prefix that ``_is_retriable`` keys on is
    kept intact."""
    msg = _redact(str(exc), *secrets) if secrets else str(exc)
    if isinstance(exc, requests.exceptions.Timeout):
        return f"timeout: {msg}"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"connection error: {msg}"
    if isinstance(exc, requests.exceptions.RequestException):
        return f"request error: {msg}"
    return f"unexpected: {msg}"


def _deliver_webhook(channel: DeliveryChannel, alert: ShippingAlert) -> DeliveryResult:
    """POST the alert as a generic JSON envelope to ``channel.target``.

    Success on any 2xx. Anything else (or a transport-level exception) is
    a failure; the response body (when available) is surfaced in
    ``error_msg`` so debugging doesn't require a re-run.
    """
    blocked = validate_target_url(channel.target)
    if blocked:
        return DeliveryResult(
            success=False, status_code=0,
            error_msg=f"blocked target (SSRF guard): {blocked}",
        )
    payload = format_webhook_payload(alert)
    status, body, exc = _http_post_json(channel.target, payload)
    if exc is not None:
        return DeliveryResult(success=False, status_code=0, error_msg=_classify_request_exc(exc, channel.target))
    if 200 <= status < 300:
        return DeliveryResult(success=True, status_code=status)
    return DeliveryResult(
        success=False,
        status_code=status,
        error_msg=f"HTTP {status}: {body}" if body else f"HTTP {status}",
    )


def _deliver_discord(channel: DeliveryChannel, alert: ShippingAlert) -> DeliveryResult:
    """POST the alert as a Discord-shaped JSON to ``channel.target``.

    Validates that ``channel.target`` is actually a Discord webhook URL —
    a generic URL configured under ``kind="discord"`` would silently
    succeed against many endpoints, which would be a footgun.

    Discord returns 204 (No Content) on success; we accept any 2xx to be
    consistent with the rest of the module.
    """
    target = channel.target or ""
    if not any(target.startswith(prefix) for prefix in _DISCORD_WEBHOOK_PREFIXES):
        return DeliveryResult(
            success=False,
            status_code=0,
            error_msg="target must be a Discord webhook URL",
        )

    payload = format_discord_payload(alert)
    status, body, exc = _http_post_json(target, payload)
    if exc is not None:
        return DeliveryResult(success=False, status_code=0, error_msg=_classify_request_exc(exc, target))
    if 200 <= status < 300:
        return DeliveryResult(success=True, status_code=status)
    return DeliveryResult(
        success=False,
        status_code=status,
        error_msg=f"HTTP {status}: {body}" if body else f"HTTP {status}",
    )


def _deliver_pagerduty(channel: DeliveryChannel, alert: ShippingAlert) -> DeliveryResult:
    """POST the alert as a PagerDuty Events API v2 trigger event.

    ``channel.target`` carries the integration key (the routing key lives
    in the request body, not the URL). The POST goes to a fixed events
    endpoint; PagerDuty returns 202 (Accepted) on success.

    On non-2xx, we try to surface PagerDuty's ``message`` field from the
    JSON error body so the caller sees ``"Invalid routing key"`` rather
    than just ``HTTP 400``.
    """
    integration_key = channel.target or ""
    payload = format_pagerduty_payload(alert, integration_key=integration_key)

    try:
        resp = requests.post(_PAGERDUTY_EVENTS_URL, json=payload, timeout=_REQUEST_TIMEOUT_S)
    except requests.exceptions.Timeout as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"timeout: {exc}")
    except requests.exceptions.ConnectionError as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"connection error: {exc}")
    except requests.exceptions.RequestException as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"request error: {exc}")
    except Exception as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"unexpected: {exc}")

    status = getattr(resp, "status_code", 0)
    if 200 <= status < 300:
        return DeliveryResult(success=True, status_code=status)

    # Surface PagerDuty's structured error message when the response body
    # parses as JSON; fall back to the raw text otherwise.
    error_text = ""
    try:
        data = resp.json()
        if isinstance(data, dict) and data.get("message"):
            error_text = str(data["message"])
    except Exception:
        pass
    if not error_text:
        try:
            error_text = (resp.text or "")[:500]
        except Exception:
            error_text = ""
    return DeliveryResult(
        success=False,
        status_code=status,
        error_msg=f"HTTP {status}: {error_text}" if error_text else f"HTTP {status}",
    )


def _lookup_alert_user_id(alert: ShippingAlert) -> str:
    """Resolve the owning user_id for a ShippingAlert.

    ShippingAlert itself does not carry ``user_id`` (the v7 column lives
    on the SQLite row, not the dataclass). The user-level prefs filter
    needs an owner to scope the lookup, so we read it back from the
    ``alerts`` table by ``alert_id``. Returns ``""`` (the legacy
    "no user" bucket) when:

      * The alert_id is missing or unknown in the DB.
      * The SQLite read fails.
      * The row is from a pre-v7 schema and the user_id column is NULL.

    NEVER raises. The empty-string return is treated by
    :func:`auth.notification_prefs.get_prefs` as the legacy / default
    bucket — i.e. unconfigured users get the default prefs (everything
    enabled), so a missing user_id never accidentally suppresses
    deliveries.
    """
    try:
        alert_id = getattr(alert, "alert_id", "") or ""
        if not alert_id:
            return ""
        from engine.alert_engine_v2 import get_alert_with_fire_count
        row = get_alert_with_fire_count(alert_id)
        if row is None:
            return ""
        uid = row.get("user_id", "")
        return uid if isinstance(uid, str) else ""
    except Exception:
        return ""


def deliver_alert(alert: ShippingAlert, channel: DeliveryChannel) -> DeliveryResult:
    """Push a single alert to ``channel``. Never raises — network errors
    are caught and returned in the ``DeliveryResult``.

    Severity gating + ``enabled`` are enforced here so callers can fire
    every alert through ``deliver_alert`` without pre-filtering. Below
    threshold or disabled → ``success=True`` with status_code=0 and
    error_msg explaining the skip; this matches "delivery succeeded by
    being a no-op" rather than "delivery failed".

    Per-user notification prefs (``auth.notification_prefs``) are
    enforced as the FINAL gate, AFTER the existing channel-enabled +
    per-channel severity_threshold + per-channel quiet-hours checks.
    When the alert's owning user has prefs that drop this channel
    (severity floor / alert_type filter / quiet hours / per-severity
    channel allow-list), the delivery is recorded as a no-op success
    (``status_code=0``, ``error_msg="suppressed by user prefs"``) and
    the cumulative ``alerts_suppressed_by_prefs`` kv_state counter is
    bumped. The legacy "no prefs configured for this user" path
    (empty kv_state row) is byte-identical to today's behaviour — the
    default ``NotificationPrefs`` returns ``[channel]`` unchanged.

    Dispatch on ``channel.kind``:
      - ``"slack"`` → POST to the incoming-webhook URL in ``target``
      - ``"email"`` → send via SMTP using ``_get_smtp_config`` (env /
        st.secrets) to the recipient address in ``target``
      - ``"sms"`` → POST to Twilio's REST API using ``_get_twilio_config``
        (env / st.secrets) to the E.164 phone number in ``target``
      - ``"webhook"`` → POST a generic JSON envelope to ``target`` (any
        HTTP endpoint)
      - ``"discord"`` → POST a Discord-shaped JSON to the Discord webhook
        URL in ``target``
      - ``"pagerduty"`` → POST a PagerDuty Events API v2 trigger to
        ``https://events.pagerduty.com/v2/enqueue``; ``target`` is the
        integration key
      - anything else → explicit "unsupported kind" failure
    """
    if not channel.enabled:
        return DeliveryResult(success=True, status_code=0, error_msg="channel disabled")
    if not _meets_threshold(alert.severity, channel.severity_threshold):
        return DeliveryResult(
            success=True,
            status_code=0,
            error_msg=f"below threshold ({alert.severity} < {channel.severity_threshold})",
        )

    # Quiet-hours gating (v13). Suppress non-CRITICAL alerts (and even
    # CRITICAL alerts when ``quiet_override_critical`` is False) when the
    # current time-of-day falls inside the channel's quiet window. Pre-
    # v13 channels have empty quiet_start/quiet_end strings, so
    # ``_is_in_quiet_window`` short-circuits to False and the legacy
    # behaviour is preserved untouched.
    if _is_in_quiet_window(channel, datetime.now(timezone.utc)):
        if not (channel.quiet_override_critical and alert.severity == "CRITICAL"):
            return DeliveryResult(
                success=False,
                status_code=0,
                error_msg="channel in quiet hours",
            )

    # ── Per-user notification-prefs gate (v25) ────────────────────────
    # FINAL filter — runs AFTER channel.enabled, AFTER per-channel
    # severity_threshold, AFTER per-channel quiet-hours. This is the
    # operator's personal subscription preference layer — see
    # ``auth.notification_prefs`` for the rules. Resolves the alert's
    # owning user_id from the alerts table; a missing user_id (or any
    # internal error) collapses to the default-prefs path, which is
    # byte-identical to the legacy behaviour.
    owner_uid = _lookup_alert_user_id(alert)
    try:
        from auth.notification_prefs import (
            filter_channels_by_prefs,
            _bump_suppressed_counter,
        )
        kept = filter_channels_by_prefs([channel], alert, user_id=owner_uid)
        if not kept:
            _bump_suppressed_counter(1)
            return DeliveryResult(
                success=True,
                status_code=0,
                error_msg="suppressed by user prefs",
            )
    except Exception:
        # Defence-in-depth — a prefs-module failure must NEVER block
        # a delivery. Fall through to the legacy dispatch path.
        pass

    # ── Per-channel monthly budget gate (v25) ─────────────────────────
    # Runs AFTER every other suppression so the budget only counts
    # deliveries the operator actually intended to send. When the
    # channel has a positive cap AND the per-user month-to-date count
    # already reached it, skip dispatch + bump the cumulative
    # ``budget_suppressed_counter`` telemetry row. The result mirrors
    # the existing "below threshold" / "user prefs" suppress patterns:
    # success=False so the caller can distinguish from a legitimate
    # delivery, status_code=429 (the HTTP idiom for "too many
    # requests" / quota exceeded), error_msg surfaces the cap.
    try:
        over_budget, current_usage, budget_cap = check_budget(
            channel, user_id=owner_uid
        )
        if over_budget:
            _bump_budget_suppressed_counter(1)
            return DeliveryResult(
                success=False,
                status_code=429,
                error_msg=(
                    f"budget exceeded ({current_usage}/{budget_cap} this month)"
                ),
            )
    except Exception:
        # Defence-in-depth — a budget-check failure must NEVER block a
        # delivery. Fall through to the legacy dispatch path.
        pass

    result = _dispatch_alert(channel, alert)

    # ── Post-dispatch budget increment ────────────────────────────────
    # Only count SUCCESSFUL deliveries — a transient transport failure
    # shouldn't burn the operator's budget. We re-use the same
    # ``owner_uid`` we computed for the prefs gate so the counter
    # bucket matches the check above (per-user-per-channel-per-month).
    if result.success:
        try:
            increment_channel_usage(channel.channel_id, user_id=owner_uid)
        except Exception:
            # Best-effort telemetry — must never propagate.
            pass

    # ── Consecutive-failure tracking + auto-disable (circuit breaker) ─
    # Track per-channel-per-user consecutive failures. On success: zero
    # the counter. On failure (retriable OR not — a stale URL returning
    # 404 should trip the breaker just as readily as a series of 502s):
    # bump the counter and call ``check_and_auto_disable`` which flips
    # enabled=False + fires a CHANNEL_AUTO_DISABLED alert when the
    # threshold is crossed. We deliberately skip the synthetic
    # CHANNEL_AUTO_DISABLED alert itself so a failed delivery of the
    # auto-disable notification can't trigger a recursive auto-disable
    # on a backup channel. All paths are wrapped — auto-disable is
    # operator hygiene, never correctness.
    if getattr(alert, "alert_type", "") != "CHANNEL_AUTO_DISABLED":
        try:
            if result.success:
                record_delivery_success(channel.channel_id, user_id=owner_uid)
            else:
                record_delivery_failure(channel.channel_id, user_id=owner_uid)
                check_and_auto_disable(
                    channel,
                    user_id=owner_uid,
                    last_error=result.error_msg or "",
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                f"deliver_alert: auto-disable tracking failed: {exc}"
            )

    # ── Failure → delivery-retry queue (v26) ──────────────────────────
    # Only RETRIABLE transport failures get enqueued. 4xx client errors,
    # budget-exceeded suppressions, quiet-hours suppressions, and "below
    # threshold" no-ops are NOT retried — those are intentional or
    # operator-config issues that won't self-heal. ``_is_retriable``
    # encapsulates the classification.
    if not result.success and _is_retriable(result):
        try:
            from engine.delivery_retry import enqueue_for_retry

            enqueue_for_retry(
                getattr(alert, "alert_id", "") or "",
                channel.channel_id,
                user_id=owner_uid,
                error_message=result.error_msg or "",
            )
        except Exception as exc:
            # Best-effort telemetry — a retry-queue failure must NEVER
            # propagate or mutate the dispatch result.
            logger.debug(f"deliver_alert: enqueue_for_retry failed: {exc}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Retry classification (v26)
# ─────────────────────────────────────────────────────────────────────────────

# HTTP status codes that ARE retriable. 408 Request Timeout and 429 Too
# Many Requests count because a backoff-then-retry is exactly what they
# ask for. All 5xx are transient by definition.
_RETRIABLE_STATUS_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})


# Substrings inside ``error_msg`` that signal a network-layer transient
# failure (status_code is 0 for these because no HTTP exchange happened).
# Matched case-insensitively. Keep this list small + idiomatic — false
# positives mean we burn retries on a permanent failure; false negatives
# mean we drop a transient blip. The current set covers the failure
# strings emitted by ``_dispatch_alert`` (timeout / connection error /
# request error) plus a generic "temporary" catch-all.
_RETRIABLE_ERROR_SUBSTRINGS: tuple[str, ...] = (
    "timeout",
    "connection",
    "temporary",
)


def _is_retriable(result: DeliveryResult) -> bool:
    """Classify a failed DeliveryResult as retriable (transport blip) or
    permanent (operator misconfig / intentional suppression).

    True iff:
      * ``result.status_code`` is in ``_RETRIABLE_STATUS_CODES`` (5xx /
        408 / 429), OR
      * ``result.error_msg`` contains one of the network-layer
        substrings ("timeout", "connection", "temporary").

    Explicitly NOT retriable:
      * Any 4xx other than 408 / 429 (operator misconfig — won't
        self-heal).
      * Budget-exceeded suppressions (the budget is intentionally
        throttling — retrying defeats the cap). The budget path uses
        status_code=429 BUT the error_msg starts with "budget exceeded"
        so we filter that out explicitly.
      * "channel in quiet hours" (intentional).
      * "below threshold" / "channel disabled" / "suppressed by user
        prefs" (intentional or no-op).
      * "unsupported channel kind" / "SMTP not configured" / "Twilio
        not configured" (operator config — needs a fix, not a retry).

    Returns False on a successful DeliveryResult — only failures get
    classified. NEVER raises.
    """
    try:
        if getattr(result, "success", False):
            return False
        msg = (getattr(result, "error_msg", "") or "").lower()
        # Explicit non-retriable exclusions FIRST so a budget-exceeded
        # 429 doesn't slip through the status-code check below.
        if msg.startswith("budget exceeded"):
            return False
        if "quiet hours" in msg:
            return False
        if "below threshold" in msg:
            return False
        if "channel disabled" in msg:
            return False
        if "suppressed by user prefs" in msg:
            return False
        if "unsupported channel kind" in msg:
            return False
        if "not configured" in msg:
            # SMTP / Twilio config gap — operator must add credentials.
            return False
        status = int(getattr(result, "status_code", 0) or 0)
        if status in _RETRIABLE_STATUS_CODES:
            return True
        if status >= 400:
            # A definitive HTTP error status (e.g. 400/403/404) won't
            # self-heal. Do NOT fall through to the body-substring check —
            # a "connection"/"timeout"/"temporary" token in the response
            # BODY (e.g. an error payload) must not trigger wasted retries
            # of a guaranteed-permanent failure. The substring check below
            # is only for transport-level failures (status_code == 0, where
            # error_msg is the exception text).
            return False
        return any(sub in msg for sub in _RETRIABLE_ERROR_SUBSTRINGS)
    except Exception:
        # Defence-in-depth — never let the classifier raise. Default
        # to False so a malformed result doesn't pollute the queue.
        return False


def _dispatch_alert(channel: DeliveryChannel, alert: ShippingAlert) -> DeliveryResult:
    """Per-kind dispatch helper, split out of ``deliver_alert`` so the
    pre-dispatch (budget check) and post-dispatch (budget increment)
    bookkeeping can wrap a single call. Behaviour matches the legacy
    inline dispatch byte-for-byte.
    """
    if channel.kind == "email":
        config = _get_smtp_config()
        if config is None:
            return DeliveryResult(
                success=False,
                status_code=0,
                error_msg="SMTP not configured",
            )
        return _deliver_email(channel, alert, config)

    if channel.kind == "sms":
        twilio_cfg = _get_twilio_config()
        if twilio_cfg is None:
            return DeliveryResult(
                success=False,
                status_code=0,
                error_msg="Twilio not configured",
            )
        return _deliver_sms(channel, alert, twilio_cfg)

    if channel.kind == "webhook":
        return _deliver_webhook(channel, alert)

    if channel.kind == "discord":
        return _deliver_discord(channel, alert)

    if channel.kind == "pagerduty":
        return _deliver_pagerduty(channel, alert)

    if channel.kind != "slack":
        # Reserved for future backends (opsgenie / teams / ...); surface
        # as an explicit failure so callers don't silently drop alerts
        # when someone adds a channel before its backend exists.
        return DeliveryResult(
            success=False,
            status_code=0,
            error_msg=f"unsupported channel kind: {channel.kind}",
        )

    blocked = validate_target_url(channel.target)
    if blocked:
        return DeliveryResult(
            success=False, status_code=0,
            error_msg=f"blocked target (SSRF guard): {blocked}",
        )
    payload = format_slack_payload(alert)
    try:
        resp = requests.post(channel.target, json=payload, timeout=_REQUEST_TIMEOUT_S)
    except Exception as exc:
        # Defence-in-depth — never let an exception propagate. The Slack
        # webhook secret IS channel.target, so classify+redact it out of
        # error_msg (which is persisted to the retry queue + shown in UI).
        return DeliveryResult(
            success=False, status_code=0,
            error_msg=_classify_request_exc(exc, channel.target),
        )

    status = getattr(resp, "status_code", 0)
    if 200 <= status < 300:
        return DeliveryResult(success=True, status_code=status)
    # Capture the response body when available so debugging is possible
    # without re-running the request.
    body = ""
    try:
        body = (resp.text or "")[:500]
    except Exception:
        pass
    return DeliveryResult(success=False, status_code=status, error_msg=f"HTTP {status}: {body}")


# ─────────────────────────────────────────────────────────────────────────────
#  Digest formatting + delivery
# ─────────────────────────────────────────────────────────────────────────────

# Maximum number of alerts to embed in the "top alerts" section of a digest
# payload. Larger windows can still be captured in the by_severity counts.
_DIGEST_TOP_N = 5
_DIGEST_SMS_MAX_CHARS = 280


def _sorted_top_alerts(alerts: list[ShippingAlert], n: int = _DIGEST_TOP_N) -> list[ShippingAlert]:
    """Sort ``alerts`` by severity (CRITICAL → HIGH → MEDIUM → LOW) then by
    ``created_at`` descending within the same severity, and return the top
    ``n``. Alerts with an unknown severity sort to the end."""
    return sorted(
        alerts,
        key=lambda a: (_SEVERITY_ORDER.get(a.severity, 99), _NegStr(a.created_at or "")),
    )[:n]


class _NegStr:
    """Wrapper that inverts string comparison so ``sorted`` produces
    descending order for that field. Used inside the tuple key in
    ``_sorted_top_alerts`` so we can mix ascending and descending sort
    directions without a lambda for each field."""

    __slots__ = ("_s",)

    def __init__(self, s: str) -> None:
        self._s = s

    def __lt__(self, other: "_NegStr") -> bool:
        return self._s > other._s

    def __eq__(self, other: object) -> bool:  # pragma: no cover - trivial
        return isinstance(other, _NegStr) and self._s == other._s


def _severity_counts(alerts: list[ShippingAlert]) -> dict:
    """Return a ``{"CRITICAL": n, "HIGH": n, "MEDIUM": n, "LOW": n}`` dict.
    Every key is present even when the count is zero so receivers can rely
    on the shape."""
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for a in alerts:
        if a.severity in counts:
            counts[a.severity] += 1
    return counts


def _highest_severity(alerts: list[ShippingAlert]) -> Optional[str]:
    """Return the highest severity present in ``alerts`` (CRITICAL is the
    highest) or ``None`` if the list is empty."""
    if not alerts:
        return None
    return min(
        (a.severity for a in alerts if a.severity in _SEVERITY_ORDER),
        key=lambda s: _SEVERITY_ORDER[s],
        default=None,
    )


def _summary_line(counts: dict, total: int) -> str:
    """Build the human-readable summary line shared by the slack / email /
    discord / pagerduty payloads. Always lists severities in CRITICAL →
    HIGH → MEDIUM → LOW order."""
    parts = [f"{counts[s]} {s}" for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")]
    return f"{total} alerts in the last 24h: " + ", ".join(parts)


def format_digest_payload(alerts: list[ShippingAlert], kind: str) -> dict:
    """Build the digest payload for ``kind`` covering ``alerts``.

    Returns a per-platform dict ready to be POSTed (slack/discord/webhook/
    pagerduty), handed to SMTP (email), or sent through Twilio (sms).
    When ``alerts`` is empty, a per-kind "all-clear" placeholder is
    returned (NOT an empty dict) so the delivery still has a meaningful
    body. When ``len(alerts) > _DIGEST_TOP_N`` (5), the "top alerts" list
    is capped at the highest-severity / most-recent first.

    Per-kind shapes:
      - **slack**: ``{"text": "Daily Alert Digest", "attachments": [{...}]}``
      - **email**: ``{"subject", "html_body", "text_body"}``
      - **sms**: ``{"body": str}`` — capped at 280 chars
      - **webhook**: flat envelope with ``event_type="digest"``,
        ``alert_count``, ``by_severity``, ``top_alerts`` (full alert
        dicts), ``generated_at``
      - **discord**: ``{"embeds": [...]}`` (no top-level content)
      - **pagerduty**: a single Events API v2 trigger with a stable
        ``dedup_key`` hashed from the sorted alert_id list
    """
    total = len(alerts)
    counts = _severity_counts(alerts)
    top = _sorted_top_alerts(alerts)
    generated_at = datetime.now(timezone.utc).isoformat()

    if kind == "slack":
        return _format_digest_slack(alerts, total, counts, top)
    if kind == "email":
        return _format_digest_email(alerts, total, counts, top)
    if kind == "sms":
        return _format_digest_sms(alerts, total, counts, top)
    if kind == "webhook":
        return _format_digest_webhook(alerts, total, counts, top, generated_at)
    if kind == "discord":
        return _format_digest_discord(alerts, total, counts, top)
    if kind == "pagerduty":
        return _format_digest_pagerduty(alerts, total, counts, top)
    # Fallback for any unsupported kind — return a generic-ish webhook
    # envelope so callers can still see what would have been sent.
    return _format_digest_webhook(alerts, total, counts, top, generated_at)


# ── Per-kind digest formatters ────────────────────────────────────────────

def _format_digest_slack(
    alerts: list[ShippingAlert],
    total: int,
    counts: dict,
    top: list[ShippingAlert],
) -> dict:
    """Slack-shaped digest payload. Uses a single attachment with the
    Refined Steel colour ``#e8e6e1`` so digests visually separate from
    immediate alerts (which use the severity palette)."""
    if total == 0:
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "Daily Alert Digest", "emoji": True}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "No alerts to digest in the last 24h."}},
        ]
        return {
            "text": "Daily Alert Digest — no alerts",
            "attachments": [{"color": "#e8e6e1", "blocks": blocks}],
        }

    summary = _summary_line(counts, total)
    bullets = "\n".join(
        f"• *[{a.severity}]* {a.title}" for a in top
    )
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Daily Alert Digest", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
        {"type": "section", "text": {"type": "mrkdwn", "text": bullets}},
    ]
    return {
        "text": "Daily Alert Digest",
        "attachments": [{"color": "#e8e6e1", "blocks": blocks}],
    }


def _format_digest_email(
    alerts: list[ShippingAlert],
    total: int,
    counts: dict,
    top: list[ShippingAlert],
) -> dict:
    """Email digest — HTML body with a summary table + styled top-5 list,
    plus a plain-text fallback for non-HTML clients."""
    if total == 0:
        subject = "Daily Alert Digest — 0 alerts"
        html_body = (
            "<html><body style='font-family:Helvetica,Arial,sans-serif;color:#24292e;"
            "background:#ffffff;margin:0;padding:0;'>"
            "<div style='max-width:640px;margin:0 auto;padding:24px;'>"
            "<h2 style='margin:0 0 16px 0;font-size:20px;color:#24292e;'>Daily Alert Digest</h2>"
            "<p style='font-size:14px;color:#586069;'>No alerts to digest in the last 24h.</p>"
            "</div></body></html>"
        )
        text_body = "Daily Alert Digest\n==================\n\nNo alerts to digest in the last 24h."
        return {"subject": subject, "html_body": html_body, "text_body": text_body}

    subject = f"Daily Alert Digest — {total} alerts"
    summary = _summary_line(counts, total)

    # ── Summary table ───────────────────────────────────────────────
    summary_rows = "".join(
        f"<tr><td style='padding:4px 12px 4px 0;color:#586069;'>{sev}</td>"
        f"<td style='padding:4px 0;'><b>{counts[sev]}</b></td></tr>"
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
    )

    # ── Top-5 list ──────────────────────────────────────────────────
    top_items = "".join(
        f"<li style='margin:4px 0;'>"
        f"<span style='display:inline-block;min-width:64px;font-weight:bold;"
        f"color:{_SEVERITY_COLOR.get(a.severity, _SEVERITY_COLOR['LOW'])};'>"
        f"[{a.severity}]</span> {a.title}</li>"
        for a in top
    )

    html_body = (
        "<html><body style='font-family:Helvetica,Arial,sans-serif;color:#24292e;"
        "background:#ffffff;margin:0;padding:0;'>"
        "<div style='max-width:640px;margin:0 auto;padding:24px;'>"
        "<h2 style='margin:0 0 8px 0;font-size:20px;color:#24292e;'>Daily Alert Digest</h2>"
        f"<p style='font-size:14px;color:#586069;margin:0 0 16px 0;'>{summary}</p>"
        "<table style='border-collapse:collapse;font-size:13px;color:#24292e;margin-bottom:16px;'>"
        f"{summary_rows}</table>"
        "<h3 style='margin:8px 0;font-size:15px;color:#24292e;'>Top alerts</h3>"
        f"<ul style='padding-left:18px;margin:0;font-size:13px;'>{top_items}</ul>"
        "</div></body></html>"
    )

    text_lines = [
        f"Daily Alert Digest — {total} alerts",
        "=" * 40,
        "",
        summary,
        "",
        "Top alerts:",
    ]
    for a in top:
        text_lines.append(f"- [{a.severity}] {a.title}")
    text_body = "\n".join(text_lines)

    return {"subject": subject, "html_body": html_body, "text_body": text_body}


def _format_digest_sms(
    alerts: list[ShippingAlert],
    total: int,
    counts: dict,
    top: list[ShippingAlert],
) -> dict:
    """SMS digest — single line, capped at 280 chars total to match the
    immediate-mode SMS cost ceiling."""
    if total == 0:
        body = "Daily Digest: no alerts in the last 24h."
        return {"body": body[:_DIGEST_SMS_MAX_CHARS]}

    crit = counts["CRITICAL"]
    # The "top" alert in SMS context is the most-severe / newest one, i.e.
    # the first element of ``top``.
    top_title = top[0].title if top else ""
    head = f"Daily Digest: {total} alerts, {crit} CRITICAL."
    suffix = f" Top: {top_title}" if top_title else ""
    body = head + suffix
    if len(body) > _DIGEST_SMS_MAX_CHARS:
        # Reserve 3 chars for the ellipsis so the truncated text + "..."
        # fits inside the cap.
        body = body[: _DIGEST_SMS_MAX_CHARS - 3] + "..."
    return {"body": body}


def _format_digest_webhook(
    alerts: list[ShippingAlert],
    total: int,
    counts: dict,
    top: list[ShippingAlert],
    generated_at: str,
) -> dict:
    """Generic webhook digest — flat JSON envelope carrying the by-severity
    counts and the top-5 alerts as full dicts (same shape the immediate
    webhook uses, minus the discriminator)."""
    return {
        "event_type": "digest",
        "alert_count": total,
        "by_severity": counts,
        "top_alerts": [format_webhook_payload(a) for a in top],
        "generated_at": generated_at,
    }


def _format_digest_discord(
    alerts: list[ShippingAlert],
    total: int,
    counts: dict,
    top: list[ShippingAlert],
) -> dict:
    """Discord digest — a single embed with title, summary description,
    severity counts as fields, and the top-3 alerts as fields. No
    top-level ``content`` so Discord renders the embed cleanly."""
    if total == 0:
        embed = {
            "title": "Daily Alert Digest",
            "description": "No alerts to digest in the last 24h.",
            "color": _DISCORD_SEVERITY_COLOR["LOW"],
            "fields": [],
        }
        return {"embeds": [embed]}

    summary = _summary_line(counts, total)
    highest = _highest_severity(alerts) or "LOW"
    color = _DISCORD_SEVERITY_COLOR.get(highest, _DISCORD_SEVERITY_COLOR["LOW"])

    fields: list[dict] = []
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        fields.append({"name": sev, "value": str(counts[sev]), "inline": True})

    # Top 3 alerts as their own fields (Discord embeds get visually
    # cluttered past ~6 fields, so we stop at 3 here even though the
    # cross-platform "top" cap is 5).
    for a in top[:3]:
        fields.append({
            "name": f"[{a.severity}] {a.title}",
            "value": (a.body or " ")[:200],
            "inline": False,
        })

    embed = {
        "title": "Daily Alert Digest",
        "description": summary,
        "color": color,
        "fields": fields,
    }
    return {"embeds": [embed]}


def _format_digest_pagerduty(
    alerts: list[ShippingAlert],
    total: int,
    counts: dict,
    top: list[ShippingAlert],
) -> dict:
    """PagerDuty digest — collapses all eligible alerts into a single
    Events API v2 trigger. ``dedup_key`` is a 16-char blake2b digest of
    the sorted ``alert_id`` list so re-sending the same digest collapses
    on PagerDuty's side instead of creating a duplicate incident.

    Severity maps from the highest-severity alert in the input (falling
    back to ``"info"`` when ``alerts`` is empty)."""
    if total == 0:
        # An empty digest still emits a PagerDuty event so an operator
        # who relies on the heartbeat doesn't see a missing run as an
        # outage. severity="info" keeps it from paging.
        dedup_key = "ship-tracker-digest-empty"
        return {
            "event_action": "trigger",
            "dedup_key": dedup_key,
            "payload": {
                "summary": "Daily alert digest: 0 alerts",
                "severity": "info",
                "source": "ship-tracker",
                "component": "digest",
                "custom_details": {
                    "alert_count": 0,
                    "by_severity": counts,
                    "top_alert_titles": [],
                },
            },
        }

    highest = _highest_severity(alerts) or "LOW"
    pd_severity = _PAGERDUTY_SEVERITY.get(highest, "info")

    sorted_ids = sorted(a.alert_id for a in alerts)
    digest = hashlib.blake2b(",".join(sorted_ids).encode("utf-8")).hexdigest()[:16]
    dedup_key = f"ship-tracker-digest-{digest}"

    crit = counts["CRITICAL"]
    high = counts["HIGH"]
    summary = f"Daily alert digest: {total} alerts ({crit} CRITICAL, {high} HIGH)"

    return {
        "event_action": "trigger",
        "dedup_key": dedup_key,
        "payload": {
            "summary": summary,
            "severity": pd_severity,
            "source": "ship-tracker",
            "component": "digest",
            "custom_details": {
                "alert_count": total,
                "by_severity": counts,
                "top_alert_titles": [a.title for a in top],
            },
        },
    }


# ── Digest delivery ───────────────────────────────────────────────────────

def _post_json(url: str, payload: dict, timeout: float = _REQUEST_TIMEOUT_S) -> DeliveryResult:
    """Shared HTTP POST → ``DeliveryResult`` helper used by the digest
    delivery paths for slack/webhook/discord/pagerduty. Returns success
    on any 2xx, surfaces the response body in ``error_msg`` otherwise."""
    blocked = validate_target_url(url)
    if blocked:
        return DeliveryResult(
            success=False, status_code=0,
            error_msg=f"blocked target (SSRF guard): {blocked}",
        )
    status, body, exc = _http_post_json(url, payload)
    if exc is not None:
        return DeliveryResult(success=False, status_code=0, error_msg=_classify_request_exc(exc, url))
    if 200 <= status < 300:
        return DeliveryResult(success=True, status_code=status)
    return DeliveryResult(
        success=False,
        status_code=status,
        error_msg=f"HTTP {status}: {body}" if body else f"HTTP {status}",
    )


def _deliver_digest_email(channel: DeliveryChannel, payload: dict) -> DeliveryResult:
    """Send a pre-formatted digest email via SMTP. Mirrors the structure
    of ``_deliver_email`` but takes the formatted payload directly so it
    can be tested in isolation."""
    config = _get_smtp_config()
    if config is None:
        return DeliveryResult(
            success=False, status_code=0, error_msg="SMTP not configured"
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = payload["subject"]
    msg["From"] = config.from_addr
    msg["To"] = channel.target
    msg.attach(MIMEText(payload["text_body"], "plain", "utf-8"))
    msg.attach(MIMEText(payload["html_body"], "html", "utf-8"))

    try:
        smtp = smtplib.SMTP(config.host, config.port, timeout=_SMTP_TIMEOUT_S)
    except smtplib.SMTPException as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"smtp connect: {exc}")
    except OSError as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"connection error: {exc}")
    except Exception as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"unexpected: {exc}")

    try:
        try:
            smtp.starttls()
            smtp.login(config.user, config.password)
            smtp.sendmail(config.from_addr, [channel.target], msg.as_string())
        except smtplib.SMTPAuthenticationError as exc:
            return DeliveryResult(success=False, status_code=0, error_msg=f"smtp auth: {exc}")
        except smtplib.SMTPRecipientsRefused as exc:
            return DeliveryResult(
                success=False, status_code=0, error_msg=f"recipient refused: {exc}"
            )
        except smtplib.SMTPException as exc:
            return DeliveryResult(success=False, status_code=0, error_msg=f"smtp error: {exc}")
        except OSError as exc:
            return DeliveryResult(success=False, status_code=0, error_msg=f"connection error: {exc}")
        except Exception as exc:
            return DeliveryResult(success=False, status_code=0, error_msg=f"unexpected: {exc}")
    finally:
        try:
            smtp.quit()
        except Exception:
            pass

    return DeliveryResult(success=True, status_code=0)


def _deliver_digest_sms(channel: DeliveryChannel, payload: dict) -> DeliveryResult:
    """Send a pre-formatted digest SMS via Twilio. Mirrors ``_deliver_sms``
    but takes the formatted body directly."""
    config = _get_twilio_config()
    if config is None:
        return DeliveryResult(
            success=False, status_code=0, error_msg="Twilio not configured"
        )

    url = _TWILIO_API_URL.format(sid=config.account_sid)
    try:
        resp = requests.post(
            url,
            auth=(config.account_sid, config.auth_token),
            data={
                "To": channel.target,
                "From": config.from_number,
                "Body": payload["body"],
            },
            timeout=_REQUEST_TIMEOUT_S,
        )
    except requests.exceptions.Timeout as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"timeout: {exc}")
    except requests.exceptions.ConnectionError as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"connection error: {exc}")
    except requests.exceptions.RequestException as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"request error: {exc}")
    except Exception as exc:
        return DeliveryResult(success=False, status_code=0, error_msg=f"unexpected: {exc}")

    status = getattr(resp, "status_code", 0)
    if status == 201:
        return DeliveryResult(success=True, status_code=201)

    error_text = ""
    try:
        data = resp.json()
        if isinstance(data, dict) and data.get("message"):
            error_text = str(data["message"])
    except Exception:
        pass
    if not error_text:
        try:
            error_text = (resp.text or "")[:500]
        except Exception:
            error_text = ""
    return DeliveryResult(
        success=False,
        status_code=status,
        error_msg=f"HTTP {status}: {error_text}" if error_text else f"HTTP {status}",
    )


def deliver_digest(channel: DeliveryChannel, alerts: list[ShippingAlert]) -> DeliveryResult:
    """Format and deliver a single digest message covering ``alerts`` to
    ``channel``. Returns one ``DeliveryResult`` for the digest as a whole.

    Dispatch on ``channel.kind``:
      - ``"slack"`` / ``"webhook"`` → ``_post_json(channel.target, payload)``
      - ``"discord"`` → same, but validates ``channel.target`` is a
        Discord webhook URL first (mirrors ``_deliver_discord``)
      - ``"pagerduty"`` → ``_post_json(_PAGERDUTY_EVENTS_URL, payload)``,
        injecting ``channel.target`` as the ``routing_key``
      - ``"email"`` → SMTP via ``_deliver_digest_email``
      - ``"sms"`` → Twilio via ``_deliver_digest_sms``
      - anything else → explicit "unsupported kind" failure
    """
    if not channel.enabled:
        return DeliveryResult(success=True, status_code=0, error_msg="channel disabled")

    payload = format_digest_payload(alerts, channel.kind)

    if channel.kind == "slack":
        return _post_json(channel.target, payload)

    if channel.kind == "webhook":
        return _post_json(channel.target, payload)

    if channel.kind == "discord":
        target = channel.target or ""
        if not any(target.startswith(prefix) for prefix in _DISCORD_WEBHOOK_PREFIXES):
            return DeliveryResult(
                success=False,
                status_code=0,
                error_msg="target must be a Discord webhook URL",
            )
        return _post_json(target, payload)

    if channel.kind == "pagerduty":
        # The PagerDuty digest payload doesn't include routing_key (so the
        # formatter is testable without a key); inject channel.target here.
        wire_payload = dict(payload)
        wire_payload["routing_key"] = channel.target or ""
        return _post_json(_PAGERDUTY_EVENTS_URL, wire_payload)

    if channel.kind == "email":
        return _deliver_digest_email(channel, payload)

    if channel.kind == "sms":
        return _deliver_digest_sms(channel, payload)

    return DeliveryResult(
        success=False,
        status_code=0,
        error_msg=f"unsupported channel kind: {channel.kind}",
    )


def deliver_pending(channel: DeliveryChannel, since: datetime) -> list[DeliveryResult]:
    """Pull every alert created after ``since`` whose severity meets
    ``channel.severity_threshold`` and deliver them.

    Dispatch on ``channel.digest_mode``:
      - ``"immediate"`` (default) → call ``deliver_alert`` per matching
        alert; returns one ``DeliveryResult`` per attempted delivery.
      - ``"daily"`` → call ``deliver_digest`` once for the whole eligible
        batch; returns a one-element list. If zero alerts match, returns
        an empty list (no delivery attempt).

    A disabled channel always returns ``[]`` regardless of mode.
    """
    if not channel.enabled:
        return []

    from state.db import get_connection

    # SQLite stores severity as a string; we can't sort/filter by the
    # _SEVERITY_ORDER mapping directly, so we pull everything since the
    # cutoff and gate in Python. Volumes are small (≤ _MAX_STORED=500).
    cutoff_iso = since.isoformat() if isinstance(since, datetime) else str(since)

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM alerts WHERE created_at > ? ORDER BY created_at ASC",
            (cutoff_iso,),
        ).fetchall()
    except Exception as exc:
        logger.warning(f"deliver_pending: SQLite read failed: {exc}")
        return []

    eligible: list[ShippingAlert] = []
    for row in rows:
        alert = _row_to_alert(row)
        if _meets_threshold(alert.severity, channel.severity_threshold):
            eligible.append(alert)

    if channel.digest_mode == "daily":
        if not eligible:
            return []
        return [deliver_digest(channel, eligible)]

    # Default / "immediate" behaviour — one delivery per matching alert.
    return [deliver_alert(alert, channel) for alert in eligible]


# ─────────────────────────────────────────────────────────────────────────────
#  Rule → channel routing
# ─────────────────────────────────────────────────────────────────────────────

def filter_channels_by_rule(rule, channels: list[DeliveryChannel]) -> list[DeliveryChannel]:
    """Return the subset of ``channels`` eligible for this ``rule``.

    The rule can be a dict (the persisted JSON shape from ``load_rules``)
    or any object with a ``target_channels`` attribute. The behaviour:

      - When ``target_channels`` is empty (or missing): every enabled
        channel is eligible — preserves the legacy 'broadcast to all'
        behaviour for rules saved before routing landed.
      - When ``target_channels`` is non-empty: only enabled channels
        whose ``name`` matches one of the listed strings are eligible.

    Disabled channels are always filtered out — the channel.enabled
    flag wins over any rule-level targeting.
    """
    enabled = [c for c in channels if c.enabled]
    if isinstance(rule, dict):
        targets = rule.get("target_channels") or []
    else:
        targets = getattr(rule, "target_channels", None) or []
    if not targets:
        return enabled
    target_set = {str(t) for t in targets if isinstance(t, str)}
    return [c for c in enabled if c.name in target_set]


def deliver_pending_for_rule(
    rule,
    alerts: list[ShippingAlert],
    all_channels: list[DeliveryChannel],
) -> list[DeliveryResult]:
    """Deliver ``alerts`` to every channel eligible under ``rule``.

    Caller supplies the alert list explicitly (vs. ``deliver_pending``
    which pulls from SQLite by time cutoff) so this is composable with
    custom queries — e.g. "alerts from rule X in the last 6 hours".

    For each eligible channel, dispatches on the channel's digest_mode
    just like ``deliver_pending``:
      - ``"immediate"`` → one ``deliver_alert`` per alert, per channel
      - ``"daily"``     → one ``deliver_digest`` per channel covering
                          the whole batch (or no call if alerts is empty)

    Returns the flat list of every ``DeliveryResult`` produced.
    """
    out: list[DeliveryResult] = []
    eligible_channels = filter_channels_by_rule(rule, all_channels)
    for channel in eligible_channels:
        # Within-channel severity gating — same rule deliver_pending uses.
        passing = [
            a for a in alerts
            if _meets_threshold(a.severity, channel.severity_threshold)
        ]
        if channel.digest_mode == "daily":
            if passing:
                out.append(deliver_digest(channel, passing))
            continue
        out.extend(deliver_alert(a, channel) for a in passing)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Channel persistence
# ─────────────────────────────────────────────────────────────────────────────

def save_channel(
    channel: DeliveryChannel,
    *,
    user_id: Optional[str] = None,
    encrypt_target: bool = False,
) -> None:
    """Insert or update a delivery channel in SQLite. ``created_at`` is
    populated server-side if blank so callers can construct
    ``DeliveryChannel`` without providing it.

    Honours per-user scoping: when ``user_id`` is ``None`` (default),
    the active Streamlit user's id is resolved via
    ``state.user_scope.current_user_id`` and stamped onto the row.
    Outside a session that resolves to ``""`` and the channel joins
    the legacy global bucket — pre-multi-user behaviour. On an UPSERT
    (channel_id collision) the user_id column is also updated so a
    channel migrated from legacy into a user's scope sticks.

    When ``encrypt_target=True``, ``channel.target`` is wrapped via
    :func:`state.vault.encrypt` before being persisted. The dataclass
    instance is NOT mutated — only the persisted value is encrypted —
    so the caller keeps the plaintext target on the in-memory object
    (useful when they immediately deliver an alert with this channel
    after a save). ``load_channels`` transparently decrypts on read.
    The default ``encrypt_target=False`` preserves today's behaviour
    exactly: the target is persisted as plain text.
    """
    from datetime import datetime, timezone
    from state.db import get_connection
    from state.user_scope import current_user_id

    uid = current_user_id() if user_id is None else user_id
    created_at = channel.created_at or datetime.now(timezone.utc).isoformat()
    # Opt-in field-level encryption — see state/vault.py for the threat
    # model + envelope format. encrypt() never raises and returns the
    # plaintext on internal failure, so the worst case is "encryption
    # silently skipped" which matches today's behaviour exactly.
    if encrypt_target:
        try:
            from state import vault
            target_to_persist = vault.encrypt(channel.target)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"save_channel: vault.encrypt failed: {exc}; persisting "
                f"plaintext target"
            )
            target_to_persist = channel.target
    else:
        target_to_persist = channel.target
    # Normalize digest_mode to one of the two supported values; any
    # other string falls back to "immediate" so a stale/invalid value
    # in the dataclass can't poison the DB.
    digest_mode = channel.digest_mode if channel.digest_mode in ("immediate", "daily") else "immediate"
    # Normalize quiet_start/quiet_end (v13). Empty strings disable the
    # window; anything that fails to parse falls back to empty so a
    # malformed value can't silently create a 24h silence on disk.
    quiet_start = channel.quiet_start if isinstance(channel.quiet_start, str) else ""
    quiet_end = channel.quiet_end if isinstance(channel.quiet_end, str) else ""
    if quiet_start and _parse_hhmm_to_minutes(quiet_start) is None:
        quiet_start = ""
    if quiet_end and _parse_hhmm_to_minutes(quiet_end) is None:
        quiet_end = ""
    quiet_override_critical = 1 if channel.quiet_override_critical else 0
    # Normalize monthly_budget (v25). Negative values are nonsensical
    # ("budget of -5") — clamp to 0 so a stray operator typo never
    # accidentally silences a channel. Non-int values (e.g. someone
    # constructed the dataclass with a string) collapse to 0 for the
    # same reason.
    try:
        monthly_budget = int(channel.monthly_budget)
    except (TypeError, ValueError):
        monthly_budget = 0
    if monthly_budget < 0:
        monthly_budget = 0
    # Normalize severity_threshold to a canonical band. An empty / unknown
    # value (e.g. a typo) would otherwise make _meets_threshold compare
    # against order 99 and deliver EVERY severity unpredictably. Default to
    # "LOW" — the explicit deliver-all band — so the stored value is always
    # canonical. We fail LOUD (deliver) rather than closed: for an alerting
    # channel, extra alerts are far safer than silently missing them.
    severity_threshold = (
        channel.severity_threshold
        if channel.severity_threshold in _SEVERITY_ORDER
        else "LOW"
    )
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO delivery_channels
                  (channel_id, name, kind, target, severity_threshold,
                   enabled, created_at, digest_mode, user_id,
                   quiet_start, quiet_end, quiet_override_critical,
                   monthly_budget)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                  name = excluded.name,
                  kind = excluded.kind,
                  target = excluded.target,
                  severity_threshold = excluded.severity_threshold,
                  enabled = excluded.enabled,
                  digest_mode = excluded.digest_mode,
                  user_id = excluded.user_id,
                  quiet_start = excluded.quiet_start,
                  quiet_end = excluded.quiet_end,
                  quiet_override_critical = excluded.quiet_override_critical,
                  monthly_budget = excluded.monthly_budget
                """,
                (
                    channel.channel_id,
                    channel.name,
                    channel.kind,
                    target_to_persist,
                    severity_threshold,
                    1 if channel.enabled else 0,
                    created_at,
                    digest_mode,
                    uid,
                    quiet_start,
                    quiet_end,
                    quiet_override_critical,
                    monthly_budget,
                ),
            )
        # Mirror created_at back onto the dataclass so the caller can
        # observe it post-save.
        channel.created_at = created_at
    except Exception as exc:
        logger.warning(f"save_channel: SQLite write failed: {exc}")
    # Audit-log the channel save (INSERT or UPDATE — we can't easily
    # tell which from the upsert without a pre-query, and a single
    # 'save_channel' verb is fine for security review). target /
    # webhook URL deliberately NOT logged — that's the secret we are
    # protecting.
    try:
        from auth.audit import record_audit
        record_audit(
            "save_channel",
            entity_type="channel",
            entity_id=channel.channel_id,
            detail={"name": channel.name, "kind": channel.kind},
            user_id=user_id,
        )
    except Exception:  # noqa: BLE001
        pass


def load_channels(*, user_id: Optional[str] = None) -> list[DeliveryChannel]:
    """Return every persisted channel, ordered by created_at ASC (oldest
    first — matches the order they were added).

    Honours per-user scoping with dual-set semantics: when ``user_id``
    resolves to a non-empty string, rows belonging to that user PLUS
    legacy ``user_id=''`` rows are returned together. The empty-string
    case returns every channel (legacy behaviour).
    """
    from state.db import get_connection
    from state.user_scope import current_user_id, scope_filter_sql

    uid = current_user_id() if user_id is None else user_id
    scope_sql, scope_params = scope_filter_sql(uid)

    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM delivery_channels WHERE 1=1 {scope_sql} "
            f"ORDER BY created_at ASC, channel_id ASC",
            scope_params,
        ).fetchall()
    except Exception as exc:
        logger.warning(f"load_channels: SQLite read failed: {exc}")
        return []
    # Tolerate older SQLite Row objects that may not expose the v6
    # ``digest_mode`` column yet (e.g. when this code runs against a
    # cache file that hasn't been re-opened since the schema bump).
    def _digest_mode_of(row) -> str:
        try:
            val = row["digest_mode"]
        except (KeyError, IndexError):
            return "immediate"
        return val if val in ("immediate", "daily") else "immediate"

    # Same defensive read pattern for the v13 quiet-hours columns. A
    # pre-v13 row (or a Row whose schema cache predates the bump) falls
    # back to the dataclass defaults ("" / "" / True) so the legacy
    # behaviour ("no quiet hours configured") is preserved.
    def _quiet_start_of(row) -> str:
        try:
            val = row["quiet_start"]
        except (KeyError, IndexError):
            return ""
        return val if isinstance(val, str) else ""

    def _quiet_end_of(row) -> str:
        try:
            val = row["quiet_end"]
        except (KeyError, IndexError):
            return ""
        return val if isinstance(val, str) else ""

    def _quiet_override_of(row) -> bool:
        try:
            val = row["quiet_override_critical"]
        except (KeyError, IndexError):
            return True
        # SQLite stores BOOL as INTEGER. Treat anything truthy as True.
        try:
            return bool(int(val))
        except (TypeError, ValueError):
            return bool(val)

    # Defensive read for the v25 ``monthly_budget`` column. A pre-v25
    # row (or a Row whose schema cache predates the bump) falls back
    # to 0 so the "unlimited / legacy behaviour" default is preserved.
    def _monthly_budget_of(row) -> int:
        try:
            val = row["monthly_budget"]
        except (KeyError, IndexError):
            return 0
        try:
            return max(0, int(val))
        except (TypeError, ValueError):
            return 0

    # Opt-in field-level decryption — a row whose target was persisted
    # via save_channel(..., encrypt_target=True) is wrapped in a
    # ``vault:v1:`` envelope. We unwrap it here so the rest of the
    # delivery pipeline (deliver_alert, format_*_payload) sees the
    # plaintext target it expects. A bad envelope / HMAC mismatch
    # / missing key falls back to the raw stored value with a WARNING
    # so the operator can act — the alternative ("silently drop the
    # row") would hide misconfiguration from the alert-routing UI.
    def _maybe_decrypt(stored: str) -> str:
        try:
            from state import vault
            if not vault.is_encrypted(stored):
                return stored
            pt = vault.decrypt(stored)
            if pt is None:
                logger.warning(
                    "load_channels: vault.decrypt returned None for an "
                    "encrypted target; falling back to the raw envelope "
                    "(channel will likely fail to deliver until re-saved)"
                )
                return stored
            return pt
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"load_channels: vault decrypt failed: {exc}")
            return stored

    return [
        DeliveryChannel(
            channel_id=r["channel_id"],
            name=r["name"],
            kind=r["kind"],
            target=_maybe_decrypt(r["target"]),
            severity_threshold=r["severity_threshold"],
            enabled=bool(r["enabled"]),
            created_at=r["created_at"],
            digest_mode=_digest_mode_of(r),
            quiet_start=_quiet_start_of(r),
            quiet_end=_quiet_end_of(r),
            quiet_override_critical=_quiet_override_of(r),
            monthly_budget=_monthly_budget_of(r),
        )
        for r in rows
    ]


def delete_channel(channel_id: str, *, user_id: Optional[str] = None) -> None:
    """Remove a channel by id. No-op if the id doesn't exist.

    Honours per-user scoping: when ``user_id`` resolves to a non-empty
    string, only channels in the user's scope (own + legacy) can be
    deleted. A cross-user delete attempt silently no-ops — same
    visible outcome as deleting an unknown id, so callers cannot
    enumerate other users' channel ids.
    """
    from state.db import get_connection
    from state.user_scope import current_user_id, scope_filter_sql

    uid = current_user_id() if user_id is None else user_id
    scope_sql, scope_params = scope_filter_sql(uid)

    conn = get_connection()
    try:
        with conn:
            conn.execute(
                f"DELETE FROM delivery_channels WHERE channel_id = ? "
                f"{scope_sql}",
                (channel_id, *scope_params),
            )
    except Exception as exc:
        logger.warning(f"delete_channel: SQLite delete failed: {exc}")
    # Audit-log the deletion. We don't pre-check whether the row
    # actually existed — the audit row reflects the user's INTENT to
    # delete (which is what a security review wants to see), not
    # whether the DB ultimately removed anything.
    try:
        from auth.audit import record_audit
        record_audit(
            "delete_channel",
            entity_type="channel",
            entity_id=channel_id,
            user_id=user_id,
        )
    except Exception:  # noqa: BLE001
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Per-channel monthly delivery budgets (schema v25)
# ─────────────────────────────────────────────────────────────────────────────
#
# Operators want to cap noisy channels with a per-channel monthly alert
# budget: "Slack #trading-desk gets max 200 alerts/month; PagerDuty gets
# max 50". When the budget is exceeded, further deliveries are suppressed
# until the next calendar month starts.
#
# Storage:
#   * The cap lives in ``delivery_channels.monthly_budget`` (the v25
#     column). 0 = unlimited (preserves the legacy behaviour for every
#     channel that hasn't opted in).
#   * The counter lives in ``kv_state`` under a per-user-per-channel-per-
#     month key:
#       ``channel_usage:<user_id>:<channel_id>:<YYYY-MM>``
#     Storing the year-month in the key (rather than as a column) gives
#     us free monthly rollover (each new month writes a fresh row) and
#     keeps per-month audit-style introspection trivially queryable by
#     prefix.
#   * The cumulative "how many deliveries did we suppress?" telemetry is
#     a single counter at ``budget_suppressed_counter`` (matches the
#     ``alerts_suppressed_by_prefs`` shape from auth.notification_prefs).
#
# The increment ONLY fires on a SUCCESSFUL delivery — transient failures
# don't burn the budget, which keeps the cap meaningful when an upstream
# outage triggers retries.

_BUDGET_USAGE_KEY_PREFIX: str = "channel_usage"
_BUDGET_SUPPRESSED_KEY: str = "budget_suppressed_counter"


def _current_year_month() -> str:
    """Return the current UTC year-month as a ``"YYYY-MM"`` string —
    the canonical bucket label for the per-channel monthly counter."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _channel_usage_key(channel_id: str, user_id: str, year_month: str) -> str:
    """Build the kv_state key for a per-channel monthly counter.

    Shape: ``channel_usage:<user_id>:<channel_id>:<YYYY-MM>``.

    ``user_id`` may be the empty string for the legacy / pre-multi-user
    bucket — see ``state.user_scope`` for the convention. Channel ids
    are caller-supplied UUIDs and can be arbitrary opaque strings; we
    do NOT defensively sanitize them here because the kv_state key is
    bound via parameter substitution everywhere it's read or written.
    """
    return f"{_BUDGET_USAGE_KEY_PREFIX}:{user_id}:{channel_id}:{year_month}"


def get_channel_usage(
    channel_id: str,
    *,
    user_id: str,
    year_month: Optional[str] = None,
) -> int:
    """Return the per-channel-per-user delivery count for ``year_month``
    (defaults to the current UTC month). Returns ``0`` for a brand-new
    channel / month / user combination — the kv_state row just doesn't
    exist yet — and ALSO returns ``0`` on any internal failure so a
    backend hiccup never accidentally suppresses deliveries.

    NEVER raises.
    """
    ym = year_month or _current_year_month()
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_channel_usage_key(channel_id, user_id, ym),),
        ).fetchone()
        if row is None:
            return 0
        try:
            val = int(row["value"])
        except (TypeError, ValueError):
            return 0
        return max(0, val)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"alert_delivery.get_channel_usage: kv_state read failed: {exc}"
        )
        return 0


def check_budget(
    channel: DeliveryChannel,
    *,
    user_id: str,
) -> tuple[bool, int, int]:
    """Return ``(over_budget, current_usage, budget)`` for ``channel`` in
    the current UTC month.

    Semantics:
      * ``budget == 0`` ⇒ unlimited; ``over_budget`` is ALWAYS ``False``
        regardless of usage. Preserves the legacy "no cap" behaviour for
        channels that never opted in.
      * ``budget > 0``  ⇒ ``over_budget`` is ``True`` iff
        ``current_usage >= budget``. The check is ``>=`` (not ``>``) so
        a channel with budget=200 dispatches alerts 1-200 and starts
        suppressing at 201.

    NEVER raises. On any internal failure the call collapses to
    ``(False, 0, budget)`` — the delivery is allowed through (failure-
    open on the cap) rather than silently swallowed.
    """
    try:
        budget = max(0, int(channel.monthly_budget))
    except (TypeError, ValueError):
        budget = 0
    if budget <= 0:
        # Unlimited path. Avoid the kv_state read entirely so a pre-v25
        # channel pays zero cost.
        return False, 0, 0
    usage = get_channel_usage(channel.channel_id, user_id=user_id)
    return usage >= budget, usage, budget


def increment_channel_usage(channel_id: str, *, user_id: str) -> None:
    """Bump the per-channel-per-user counter for the CURRENT UTC month
    by 1.

    Called from ``deliver_alert`` AFTER a successful dispatch only —
    never on a failure, so a transient outage doesn't burn the budget.

    Best-effort: a kv_state write failure is swallowed (the budget
    machinery is operator telemetry, not correctness). NEVER raises.
    """
    ym = _current_year_month()
    key = _channel_usage_key(channel_id, user_id, ym)
    try:
        from state.db import get_connection

        conn = get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        # Atomic single-statement increment — two concurrent deliveries
        # can no longer both read the same value and lose an increment
        # (kv_state.key is the PRIMARY KEY, so ON CONFLICT(key) fires).
        with conn:
            conn.execute(
                "INSERT INTO kv_state (key, value, updated_at) "
                "VALUES (?, '1', ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value = CAST(value AS INTEGER) + 1, "
                "updated_at = excluded.updated_at",
                (key, now_iso),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"alert_delivery.increment_channel_usage: kv_state write "
            f"failed: {exc}"
        )


def reset_channel_usage(
    channel_id: str,
    *,
    user_id: str,
    year_month: Optional[str] = None,
) -> bool:
    """Zero the per-channel-per-user counter for ``year_month`` (default
    current UTC month).

    Operator-triggered manual reset — used after a noisy week ended and
    the operator wants the channel to start delivering again before the
    natural month-boundary reset. Implementation is a DELETE on the
    kv_state row; ``get_channel_usage`` then returns 0 (the "row does
    not exist" branch).

    Returns ``True`` on success, ``False`` on any internal failure.
    NEVER raises.

    Also records an audit row (``action='reset_channel_usage'``) so
    the security review trail captures operator intent.
    """
    ym = year_month or _current_year_month()
    key = _channel_usage_key(channel_id, user_id, ym)
    ok = False
    try:
        from state.db import get_connection

        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM kv_state WHERE key = ?", (key,))
        ok = True
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"alert_delivery.reset_channel_usage: kv_state delete "
            f"failed: {exc}"
        )
        ok = False
    # Audit-log the reset regardless of whether a row actually existed.
    # The audit captures the operator's INTENT to reset, which is the
    # security-relevant fact (matches the delete_channel pattern).
    try:
        from auth.audit import record_audit
        record_audit(
            "reset_channel_usage",
            entity_type="channel",
            entity_id=channel_id,
            detail={"year_month": ym, "success": ok},
            user_id=user_id,
        )
    except Exception:  # noqa: BLE001
        pass
    return ok


def get_all_channel_usage(*, user_id: str) -> list[dict]:
    """Return per-channel usage for every channel in ``user_id``'s scope,
    suitable for a dashboard table.

    Each row is a dict:
      * ``channel_id`` — UUID identifier
      * ``name``       — human-readable label
      * ``kind``       — slack / email / sms / webhook / discord / pagerduty
      * ``budget``     — the configured cap (0 = unlimited)
      * ``usage``      — the current month's counter
      * ``pct``        — ``usage / budget * 100`` rounded to one decimal,
                        or ``None`` when budget==0 (the "% of unlimited"
                        is meaningless — render as "—" in the UI).
      * ``over_budget``— bool, True iff usage >= budget > 0

    NEVER raises. Returns ``[]`` on any internal failure.
    """
    try:
        channels = load_channels(user_id=user_id)
    except Exception:
        return []
    out: list[dict] = []
    for ch in channels:
        try:
            over, usage, budget = check_budget(ch, user_id=user_id)
        except Exception:
            # Defence-in-depth — never let a single bad row break the
            # whole dashboard query.
            over, usage, budget = False, 0, 0
        if budget > 0:
            pct: Optional[float] = round(usage / budget * 100.0, 1)
        else:
            pct = None
        out.append({
            "channel_id": ch.channel_id,
            "name": ch.name,
            "kind": ch.kind,
            "budget": budget,
            "usage": usage,
            "pct": pct,
            "over_budget": bool(over),
        })
    return out


def _bump_budget_suppressed_counter(amount: int = 1) -> None:
    """Increment the cumulative ``budget_suppressed_counter`` kv_state
    row. Mirrors the shape of
    ``auth.notification_prefs._bump_suppressed_counter`` — best-effort
    telemetry; a write failure is swallowed because the counter is
    operator instrumentation, not correctness. NEVER raises.
    """
    if amount <= 0:
        return
    try:
        from state.db import get_connection

        conn = get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        amt = int(amount)
        # Atomic single-statement increment by ``amount`` — no lost updates.
        with conn:
            conn.execute(
                "INSERT INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value = CAST(value AS INTEGER) + ?, "
                "updated_at = excluded.updated_at",
                (_BUDGET_SUPPRESSED_KEY, str(amt), now_iso, amt),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"alert_delivery._bump_budget_suppressed_counter: "
            f"kv_state write failed: {exc}"
        )


def get_budget_suppressed_count() -> int:
    """Return the cumulative count of deliveries skipped because their
    channel's monthly budget was exhausted, since the counter started
    (or since a manual reset of the kv_state row). Returns ``0`` when
    the row does not yet exist. NEVER raises."""
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_BUDGET_SUPPRESSED_KEY,),
        ).fetchone()
        if row is None:
            return 0
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return 0
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
#  Channel auto-disable (consecutive-failure circuit breaker)
# ─────────────────────────────────────────────────────────────────────────────
#
# Goal: a stale webhook URL (or any other "this channel is permanently
# broken now") should stop wasting deliveries + alert noise. We track
# consecutive failures per (user, channel) in kv_state; once the count
# reaches ``AUTO_DISABLE_THRESHOLD`` we flip ``channel.enabled = False``
# via ``save_channel`` AND fire a CHANNEL_AUTO_DISABLED alert so the
# operator notices.
#
# Storage:
#   * Per-channel-per-user counter:
#       ``channel_consecutive_failures:<user_id>:<channel_id>`` → int
#   * Per-channel-per-user "this channel was auto-disabled" flag:
#       ``channel_auto_disabled_flag:<user_id>:<channel_id>`` → "1"
#     The flag lets the UI tell "auto-disabled, investigate then
#     re-enable" apart from "operator manually flipped enabled=False".
#     Cleared by ``reset_consecutive_failures`` and by the operator
#     re-enable path in the UI.
#
# Semantics:
#   * Both retriable AND non-retriable failures count — a stale URL
#     producing 404s every time should ALSO disable, not just outages.
#   * On a successful dispatch the counter resets to 0.
#   * On auto-disable the counter is PRESERVED at threshold so the
#     operator can see exactly what tripped it. ``reset_consecutive_
#     failures`` (operator-triggered) clears it.
#   * The CHANNEL_AUTO_DISABLED alert is persisted via the normal
#     ``save_alerts`` pipeline so it routes through any OTHER configured
#     channels — explicitly NOT through the channel that just got
#     disabled (infinite-loop guard, also enforced by ``channel.enabled
#     == False`` filtering on the next dispatch).
#   * All helpers are NEVER-raise — auto-disable is operator hygiene,
#     not correctness. A backend hiccup must never let a transient
#     failure cascade into a propagated exception that kills the
#     delivery pass.

# Number of consecutive failures before a channel is auto-disabled.
# Defaults to 10 — high enough that a flaky network blip doesn't trip
# the breaker, low enough that a stale URL doesn't waste an hour of
# deliveries on a typical 5-min retry cadence.
AUTO_DISABLE_THRESHOLD: int = 10
# Reset the counter on success? Always True today; the constant lives
# here so a future "I want to require N consecutive successes before
# resetting" tweak has one knob to turn.
AUTO_DISABLE_RESET_ON_SUCCESS: bool = True

_FAILURE_COUNTER_KEY_PREFIX: str = "channel_consecutive_failures"
_AUTO_DISABLED_FLAG_KEY_PREFIX: str = "channel_auto_disabled_flag"


def _failure_counter_key(channel_id: str, user_id: str) -> str:
    """Build the kv_state key for the per-channel-per-user consecutive
    failure counter. Shape:
    ``channel_consecutive_failures:<user_id>:<channel_id>``.
    """
    return f"{_FAILURE_COUNTER_KEY_PREFIX}:{user_id}:{channel_id}"


def _auto_disabled_flag_key(channel_id: str, user_id: str) -> str:
    """Build the kv_state key for the per-channel-per-user "this channel
    was auto-disabled" flag. Shape:
    ``channel_auto_disabled_flag:<user_id>:<channel_id>``.
    """
    return f"{_AUTO_DISABLED_FLAG_KEY_PREFIX}:{user_id}:{channel_id}"


def get_consecutive_failures(channel_id: str, *, user_id: str) -> int:
    """Return the current consecutive-failure count for ``channel_id``
    in ``user_id``'s scope. Returns ``0`` for a brand-new channel (no
    row yet) AND on any internal failure — defensive so a backend
    hiccup never accidentally trips the breaker via a stale read.

    NEVER raises.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_failure_counter_key(channel_id or "", user_id or ""),),
        ).fetchone()
        if row is None:
            return 0
        try:
            val = int(row["value"])
        except (TypeError, ValueError):
            # Corrupted row (someone wrote a non-int) → behave as zero
            # so the breaker stays armed but doesn't auto-trip on garbage.
            return 0
        return max(0, val)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"alert_delivery.get_consecutive_failures: kv_state read "
            f"failed: {exc}"
        )
        return 0


def record_delivery_failure(channel_id: str, *, user_id: str) -> int:
    """Increment the consecutive-failure counter by 1 and return the
    new count. Returns ``0`` on any internal failure (the counter could
    not be persisted) — caller decides whether to act on the new value.

    NEVER raises.
    """
    key = _failure_counter_key(channel_id or "", user_id or "")
    try:
        from state.db import get_connection

        conn = get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        # Atomic single-statement increment (no lost updates under
        # concurrent dispatch), then read the post-increment value back.
        with conn:
            conn.execute(
                "INSERT INTO kv_state (key, value, updated_at) "
                "VALUES (?, '1', ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value = CAST(value AS INTEGER) + 1, "
                "updated_at = excluded.updated_at",
                (key, now_iso),
            )
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?", (key,)
        ).fetchone()
        try:
            return int(row["value"]) if row else 0
        except (TypeError, ValueError):
            return 0
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"alert_delivery.record_delivery_failure: kv_state write "
            f"failed: {exc}"
        )
        return 0


def record_delivery_success(channel_id: str, *, user_id: str) -> None:
    """Reset the consecutive-failure counter to 0 after a successful
    dispatch. Best-effort: a DELETE failure is swallowed (the channel
    will simply retain its old counter until the next save / reset).

    NEVER raises.
    """
    if not AUTO_DISABLE_RESET_ON_SUCCESS:
        return
    key = _failure_counter_key(channel_id or "", user_id or "")
    try:
        from state.db import get_connection

        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM kv_state WHERE key = ?", (key,))
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"alert_delivery.record_delivery_success: kv_state delete "
            f"failed: {exc}"
        )


def reset_consecutive_failures(channel_id: str, *, user_id: str) -> bool:
    """Operator-triggered counter reset — used after manually re-enabling
    a previously auto-disabled channel. Clears BOTH the counter AND the
    "this channel was auto-disabled" flag so the UI stops nagging.

    Returns ``True`` on success, ``False`` on any internal failure.
    NEVER raises. Records an audit event with action
    ``'reset_consecutive_failures'`` so the security trail captures
    operator intent.
    """
    ok = False
    counter_key = _failure_counter_key(channel_id or "", user_id or "")
    flag_key = _auto_disabled_flag_key(channel_id or "", user_id or "")
    try:
        from state.db import get_connection

        conn = get_connection()
        with conn:
            conn.execute(
                "DELETE FROM kv_state WHERE key IN (?, ?)",
                (counter_key, flag_key),
            )
        ok = True
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"alert_delivery.reset_consecutive_failures: kv_state delete "
            f"failed: {exc}"
        )
        ok = False
    try:
        from auth.audit import record_audit
        record_audit(
            "reset_consecutive_failures",
            entity_type="channel",
            entity_id=channel_id or "",
            detail={"success": ok},
            user_id=user_id,
        )
    except Exception:  # noqa: BLE001
        pass
    return ok


def is_auto_disabled(channel_id: str, *, user_id: str) -> bool:
    """True iff the per-channel-per-user "this channel was auto-disabled"
    kv_state flag is set. Used by the UI to distinguish "operator turned
    this off intentionally" from "the circuit breaker tripped". NEVER
    raises — returns False on any internal failure.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_auto_disabled_flag_key(channel_id or "", user_id or ""),),
        ).fetchone()
        return row is not None and str(row["value"]) == "1"
    except Exception:
        return False


def _build_auto_disabled_alert(
    channel: "DeliveryChannel",
    *,
    threshold: int,
    last_error: str,
) -> "ShippingAlert":
    """Build the CHANNEL_AUTO_DISABLED ShippingAlert payload. Severity
    is HIGH (not CRITICAL — channel breakage is operator hygiene, not
    a market event), and the body carries the most recent error so the
    operator can triage without log-diving."""
    from engine.alert_engine_v2 import _make as _make_alert

    safe_name = getattr(channel, "name", "") or "(unnamed)"
    safe_kind = getattr(channel, "kind", "") or "unknown"
    safe_id = getattr(channel, "channel_id", "") or ""
    title = (
        f"Channel {safe_name} auto-disabled after {threshold} consecutive failures"
    )
    # Truncate the last_error so a 50KB stack trace doesn't blow up the
    # SMS / Slack body — 500 chars is plenty for triage.
    err_excerpt = (last_error or "")[:500]
    body = (
        f"Delivery channel '{safe_name}' (kind={safe_kind}, id={safe_id}) was "
        f"auto-disabled after {threshold} consecutive delivery failures. The "
        f"most recent error was: {err_excerpt or '(no error message)'}. "
        f"Investigate the channel config (URL / credentials / quotas) and "
        f"re-enable manually from the Alert Center → Delivery Channels panel, "
        f"or via the ops CLI."
    )
    return _make_alert(
        alert_type="CHANNEL_AUTO_DISABLED",
        severity="HIGH",
        title=title,
        body=body,
        port_locode=safe_id[:32],  # piggy-back as entity key for dedup
    )


def check_and_auto_disable(
    channel: "DeliveryChannel",
    *,
    user_id: str,
    last_error: str = "",
) -> bool:
    """If the per-channel-per-user consecutive-failure counter has
    reached :data:`AUTO_DISABLE_THRESHOLD`, flip
    ``channel.enabled = False`` via :func:`save_channel`, set the
    "auto-disabled" flag in kv_state, fire a CHANNEL_AUTO_DISABLED
    alert through the standard pipeline (which routes to OTHER
    configured channels — never through the one just disabled, because
    the next dispatch will see ``enabled=False`` and skip it), and
    record an audit row.

    Returns ``True`` iff the channel was auto-disabled by THIS call.
    Returns ``False`` when:
      * The counter is below threshold.
      * The channel was ALREADY disabled (no-op — the flag may not be
        set, but flipping enabled=False again would be pointless).
      * Any internal failure (defence-in-depth — the breaker is
        operator hygiene, NOT a correctness invariant).

    NEVER raises. All side effects (save_channel, save_alerts,
    record_audit) are wrapped — a failure in one does not block the
    others, and a top-level failure returns False rather than
    propagating.
    """
    try:
        # Already disabled? Nothing to do. We deliberately do NOT
        # auto-flip the flag here — operator manual disable is a
        # different verb from auto-disable.
        if not getattr(channel, "enabled", True):
            return False
        count = get_consecutive_failures(
            getattr(channel, "channel_id", "") or "", user_id=user_id or "",
        )
        if count < AUTO_DISABLE_THRESHOLD:
            return False

        # ── 1. Flip enabled=False + persist ──────────────────────────────
        # Mutate the in-memory dataclass first so the caller observes the
        # flip (a downstream guard could check `channel.enabled`).
        #
        # Persist via a SCOPED, enabled-only UPDATE keyed on the immutable
        # channel_id — NOT save_channel. A full-row UPSERT from this
        # (possibly stale) dataclass would (a) reassign the channel's owner
        # to the alert owner's user_id [cross-user hijack], (b) rewrite a
        # vault-encrypted target back to plaintext, and (c) clobber any
        # concurrent operator edit to target/budget/quiet-hours. So touch
        # only the `enabled` column.
        channel.enabled = False
        try:
            from state.db import get_connection

            conn = get_connection()
            with conn:
                conn.execute(
                    "UPDATE delivery_channels SET enabled = 0 "
                    "WHERE channel_id = ?",
                    (getattr(channel, "channel_id", "") or "",),
                )
        except Exception as exc:  # noqa: BLE001
            # defence-in-depth — the breaker is operator hygiene, not a
            # correctness invariant.
            logger.debug(
                f"alert_delivery.check_and_auto_disable: enabled-flag "
                f"UPDATE failed: {exc}"
            )

        # ── 2. Set the "auto-disabled" kv_state flag ─────────────────────
        # Lets the UI render "Auto-disabled — investigate then re-enable"
        # caption + Re-enable button instead of the operator wondering
        # who turned it off.
        try:
            from state.db import get_connection

            conn = get_connection()
            now_iso = datetime.now(timezone.utc).isoformat()
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                    "VALUES (?, ?, ?)",
                    (
                        _auto_disabled_flag_key(
                            getattr(channel, "channel_id", "") or "",
                            user_id or "",
                        ),
                        "1",
                        now_iso,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                f"alert_delivery.check_and_auto_disable: flag write "
                f"failed: {exc}"
            )

        # ── 3. Fire the CHANNEL_AUTO_DISABLED alert ──────────────────────
        # Persisted through the normal save_alerts pipeline so any other
        # delivery worker / digest / push channel picks it up. The
        # disabled channel itself won't re-dispatch — the enabled=False
        # gate inside ``deliver_alert`` blocks it (infinite-loop guard).
        try:
            from engine.alert_engine_v2 import save_alerts

            alert = _build_auto_disabled_alert(
                channel,
                threshold=AUTO_DISABLE_THRESHOLD,
                last_error=last_error or "",
            )
            save_alerts([alert], user_id=user_id or "")
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                f"alert_delivery.check_and_auto_disable: save_alerts "
                f"failed: {exc}"
            )

        # ── 4. Audit-log the auto-disable ────────────────────────────────
        try:
            from auth.audit import record_audit
            record_audit(
                "channel_auto_disabled",
                entity_type="channel",
                entity_id=getattr(channel, "channel_id", "") or "",
                detail={
                    "name": getattr(channel, "name", ""),
                    "kind": getattr(channel, "kind", ""),
                    "threshold": int(AUTO_DISABLE_THRESHOLD),
                    "last_error": (last_error or "")[:500],
                },
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass

        return True
    except Exception as exc:  # noqa: BLE001 — never raise
        logger.debug(
            f"alert_delivery.check_and_auto_disable: top-level failure: {exc}"
        )
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Test ping — operator-driven channel verification
# ─────────────────────────────────────────────────────────────────────────────

_TEST_PING_BODY_PREFIX = "\U0001f9ea Test ping from Ship Tracker"


def _build_test_ping_alert(channel: "DeliveryChannel") -> "ShippingAlert":
    """Build the synthetic alert used by ``send_test_ping``.

    Carries a clear ``"TEST"`` prefix in the title + an unambiguous body
    referencing the channel so the receiving operator can confirm which
    channel was being verified. The alert is constructed via
    :func:`engine.alert_engine_v2._make` so it gets a fresh UUID +
    ``created_at`` timestamp identical in shape to a real alert.
    """
    from engine.alert_engine_v2 import _make as _make_alert
    now_iso = datetime.now(timezone.utc).isoformat()
    body = (
        f"{_TEST_PING_BODY_PREFIX} — channel {channel.name} "
        f"(id={channel.channel_id}). If you see this, delivery is working. "
        f"Sent at {now_iso}."
    )
    return _make_alert(
        alert_type="TEST_PING",
        severity="LOW",
        title=f"[TEST] Channel verification — {channel.name}",
        body=body,
    )


def send_test_ping(channel: "DeliveryChannel") -> tuple[bool, str]:
    """Send a synthetic "test ping" delivery to ``channel`` so an operator
    can verify the channel works WITHOUT manufacturing a real alert.

    Behaviour:
      * Constructs a synthetic ``ShippingAlert`` via
        :func:`_build_test_ping_alert` with a clear "TEST" prefix.
      * Dispatches on ``channel.kind`` to the same per-kind helper
        ``deliver_alert`` would use (slack inline, email/sms/webhook/
        discord/pagerduty via their ``_deliver_*`` helpers).
      * Bypasses ``channel.enabled`` and ``channel.severity_threshold`` —
        the operator might be testing a channel they just configured and
        haven't enabled yet, and severity gating would mask the synthetic
        LOW alert behind a HIGH/CRITICAL threshold.
      * NEVER raises. Every path is wrapped in try/except; any unexpected
        exception is returned in the failure tuple.
      * Records an audit row with ``action='test_ping'`` and
        ``detail={'kind': channel.kind, ...}`` so the operator can
        confirm what got sent.

    Returns:
        Tuple of ``(success, message)``. On success ``message`` is
        ``"Test ping delivered"``. On failure ``message`` carries the
        underlying error text (HTTP status, exception text, or
        ``"unsupported kind: <kind>"``).
    """
    success = False
    message = ""
    try:
        alert = _build_test_ping_alert(channel)
        kind = channel.kind

        if kind == "slack":
            payload = format_slack_payload(alert)
            try:
                resp = requests.post(channel.target, json=payload, timeout=_REQUEST_TIMEOUT_S)
                status = getattr(resp, "status_code", 0)
                if 200 <= status < 300:
                    success = True
                    message = "Test ping delivered"
                else:
                    body = ""
                    try:
                        body = (resp.text or "")[:500]
                    except Exception:
                        pass
                    message = f"HTTP {status}: {body}" if body else f"HTTP {status}"
            except Exception as exc:  # noqa: BLE001
                message = str(exc) or exc.__class__.__name__

        elif kind == "email":
            cfg = _get_smtp_config()
            if cfg is None:
                message = "SMTP not configured"
            else:
                result = _deliver_email(channel, alert, cfg)
                success = bool(result.success)
                message = "Test ping delivered" if success else (result.error_msg or "delivery failed")

        elif kind == "sms":
            cfg = _get_twilio_config()
            if cfg is None:
                message = "Twilio not configured"
            else:
                result = _deliver_sms(channel, alert, cfg)
                success = bool(result.success)
                message = "Test ping delivered" if success else (result.error_msg or "delivery failed")

        elif kind == "webhook":
            result = _deliver_webhook(channel, alert)
            success = bool(result.success)
            message = "Test ping delivered" if success else (result.error_msg or "delivery failed")

        elif kind == "discord":
            result = _deliver_discord(channel, alert)
            success = bool(result.success)
            message = "Test ping delivered" if success else (result.error_msg or "delivery failed")

        elif kind == "pagerduty":
            result = _deliver_pagerduty(channel, alert)
            success = bool(result.success)
            message = "Test ping delivered" if success else (result.error_msg or "delivery failed")

        else:
            message = f"unsupported kind: {kind}"

    except Exception as exc:  # noqa: BLE001 — never raise
        success = False
        message = str(exc) or exc.__class__.__name__

    # Audit-log the test ping. ``record_audit`` is itself never-raise,
    # but wrap defensively so a stray import error can't turn into a
    # propagated exception. target / webhook URL deliberately NOT logged
    # — matches the save_channel audit shape.
    try:
        from auth.audit import record_audit
        record_audit(
            "test_ping",
            entity_type="channel",
            entity_id=getattr(channel, "channel_id", ""),
            detail={
                "kind": getattr(channel, "kind", ""),
                "name": getattr(channel, "name", ""),
                "success": bool(success),
                "message": message,
            },
        )
    except Exception:  # noqa: BLE001
        pass

    return bool(success), message
