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
"""
from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

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
    """
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
        f"<td style='padding:4px 0;'><b>{alert.alert_type}</b></td></tr>"
    )

    context_html = ""
    if context_lines:
        rows = "".join(
            f"<tr><td style='padding:2px 12px 2px 0;color:#586069;'>{label}</td>"
            f"<td style='padding:2px 0;'>{value}</td></tr>"
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
        f"{alert.severity}</div>"
        f"<h2 style='margin:4px 0 0 0;font-size:20px;color:#24292e;'>{alert.title}</h2>"
        "</div>"
        f"<p style='font-size:14px;line-height:1.5;margin:16px 0;'>{alert.body}</p>"
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


def _classify_request_exc(exc: Exception) -> str:
    """Map a request-side exception onto the ``error_msg`` prefix the rest
    of the module uses ("timeout" / "connection error" / "request error" /
    "unexpected"). Keeps the wording identical to the existing Slack /
    Twilio paths."""
    if isinstance(exc, requests.exceptions.Timeout):
        return f"timeout: {exc}"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"connection error: {exc}"
    if isinstance(exc, requests.exceptions.RequestException):
        return f"request error: {exc}"
    return f"unexpected: {exc}"


def _deliver_webhook(channel: DeliveryChannel, alert: ShippingAlert) -> DeliveryResult:
    """POST the alert as a generic JSON envelope to ``channel.target``.

    Success on any 2xx. Anything else (or a transport-level exception) is
    a failure; the response body (when available) is surfaced in
    ``error_msg`` so debugging doesn't require a re-run.
    """
    payload = format_webhook_payload(alert)
    status, body, exc = _http_post_json(channel.target, payload)
    if exc is not None:
        return DeliveryResult(success=False, status_code=0, error_msg=_classify_request_exc(exc))
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
        return DeliveryResult(success=False, status_code=0, error_msg=_classify_request_exc(exc))
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


def deliver_alert(alert: ShippingAlert, channel: DeliveryChannel) -> DeliveryResult:
    """Push a single alert to ``channel``. Never raises — network errors
    are caught and returned in the ``DeliveryResult``.

    Severity gating + ``enabled`` are enforced here so callers can fire
    every alert through ``deliver_alert`` without pre-filtering. Below
    threshold or disabled → ``success=True`` with status_code=0 and
    error_msg explaining the skip; this matches "delivery succeeded by
    being a no-op" rather than "delivery failed".

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

    payload = format_slack_payload(alert)
    try:
        resp = requests.post(channel.target, json=payload, timeout=_REQUEST_TIMEOUT_S)
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


def deliver_pending(channel: DeliveryChannel, since: datetime) -> list[DeliveryResult]:
    """Pull every alert created after ``since`` whose severity meets
    ``channel.severity_threshold``, and deliver each one.

    Returns one ``DeliveryResult`` per attempted delivery. An empty list
    means no alerts matched the filters (not a failure).
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

    results: list[DeliveryResult] = []
    for row in rows:
        alert = _row_to_alert(row)
        if not _meets_threshold(alert.severity, channel.severity_threshold):
            continue
        results.append(deliver_alert(alert, channel))
    return results


# ─────────────────────────────────────────────────────────────────────────────
#  Channel persistence
# ─────────────────────────────────────────────────────────────────────────────

def save_channel(channel: DeliveryChannel) -> None:
    """Insert or update a delivery channel in SQLite. ``created_at`` is
    populated server-side if blank so callers can construct
    ``DeliveryChannel`` without providing it."""
    from datetime import datetime, timezone
    from state.db import get_connection

    created_at = channel.created_at or datetime.now(timezone.utc).isoformat()
    # Normalize digest_mode to one of the two supported values; any
    # other string falls back to "immediate" so a stale/invalid value
    # in the dataclass can't poison the DB.
    digest_mode = channel.digest_mode if channel.digest_mode in ("immediate", "daily") else "immediate"
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO delivery_channels
                  (channel_id, name, kind, target, severity_threshold,
                   enabled, created_at, digest_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                  name = excluded.name,
                  kind = excluded.kind,
                  target = excluded.target,
                  severity_threshold = excluded.severity_threshold,
                  enabled = excluded.enabled,
                  digest_mode = excluded.digest_mode
                """,
                (
                    channel.channel_id,
                    channel.name,
                    channel.kind,
                    channel.target,
                    channel.severity_threshold,
                    1 if channel.enabled else 0,
                    created_at,
                    digest_mode,
                ),
            )
        # Mirror created_at back onto the dataclass so the caller can
        # observe it post-save.
        channel.created_at = created_at
    except Exception as exc:
        logger.warning(f"save_channel: SQLite write failed: {exc}")


def load_channels() -> list[DeliveryChannel]:
    """Return every persisted channel, ordered by created_at ASC (oldest
    first — matches the order they were added)."""
    from state.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM delivery_channels ORDER BY created_at ASC, channel_id ASC"
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

    return [
        DeliveryChannel(
            channel_id=r["channel_id"],
            name=r["name"],
            kind=r["kind"],
            target=r["target"],
            severity_threshold=r["severity_threshold"],
            enabled=bool(r["enabled"]),
            created_at=r["created_at"],
            digest_mode=_digest_mode_of(r),
        )
        for r in rows
    ]


def delete_channel(channel_id: str) -> None:
    """Remove a channel by id. No-op if the id doesn't exist."""
    from state.db import get_connection

    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "DELETE FROM delivery_channels WHERE channel_id = ?",
                (channel_id,),
            )
    except Exception as exc:
        logger.warning(f"delete_channel: SQLite delete failed: {exc}")
