"""External alert delivery — push ShippingAlerts out of the app.

The alert engine in ``engine.alert_engine_v2`` persists alerts to SQLite
and surfaces them in the UI. This module adds an outbound channel so
alerts can also be pushed to Slack (today) or Email / SMS (future).

Design notes
------------
* ``DeliveryChannel`` is a typed config row. ``kind`` is a free-form
  string today ("slack") with "email" / "sms" reserved for future
  PRs. ``target`` is the Slack incoming-webhook URL.
* ``severity_threshold`` uses ``alert_engine_v2._SEVERITY_ORDER`` —
  CRITICAL (0) < HIGH (1) < MEDIUM (2) < LOW (3). A channel with
  threshold "MEDIUM" delivers MEDIUM/HIGH/CRITICAL alerts and skips
  LOW.
* ``format_slack_payload`` is a pure function so it can be tested
  without touching the network. ``deliver_alert`` POSTs the payload
  with a 10s timeout and always returns a ``DeliveryResult`` — it
  never raises so callers can iterate over many alerts without one
  Slack outage breaking the whole batch.
* Channel persistence lives in the SQLite ``delivery_channels`` table
  (schema v2). Channel configs are user-authored config, parallel to
  ``alert_rules``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
    kind: str                # "slack" today; "email"/"sms" reserved
    target: str              # webhook URL for slack
    severity_threshold: str  # "CRITICAL"|"HIGH"|"MEDIUM"|"LOW"
    enabled: bool = True
    created_at: str = ""     # ISO timestamp, populated by save_channel


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
#  Delivery
# ─────────────────────────────────────────────────────────────────────────────

_REQUEST_TIMEOUT_S = 10.0


def deliver_alert(alert: ShippingAlert, channel: DeliveryChannel) -> DeliveryResult:
    """POST a single alert to ``channel``. Never raises — network errors
    are caught and returned in the ``DeliveryResult``.

    Severity gating + ``enabled`` are enforced here so callers can fire
    every alert through ``deliver_alert`` without pre-filtering. Below
    threshold or disabled → ``success=True`` with status_code=0 and
    error_msg explaining the skip; this matches "delivery succeeded by
    being a no-op" rather than "delivery failed".
    """
    if not channel.enabled:
        return DeliveryResult(success=True, status_code=0, error_msg="channel disabled")
    if not _meets_threshold(alert.severity, channel.severity_threshold):
        return DeliveryResult(
            success=True,
            status_code=0,
            error_msg=f"below threshold ({alert.severity} < {channel.severity_threshold})",
        )

    if channel.kind != "slack":
        # Reserved for future backends; surface as an explicit failure so
        # callers don't silently drop alerts when someone adds an
        # email/sms channel before the backend exists.
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
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO delivery_channels
                  (channel_id, name, kind, target, severity_threshold,
                   enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                  name = excluded.name,
                  kind = excluded.kind,
                  target = excluded.target,
                  severity_threshold = excluded.severity_threshold,
                  enabled = excluded.enabled
                """,
                (
                    channel.channel_id,
                    channel.name,
                    channel.kind,
                    channel.target,
                    channel.severity_threshold,
                    1 if channel.enabled else 0,
                    created_at,
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
    return [
        DeliveryChannel(
            channel_id=r["channel_id"],
            name=r["name"],
            kind=r["kind"],
            target=r["target"],
            severity_threshold=r["severity_threshold"],
            enabled=bool(r["enabled"]),
            created_at=r["created_at"],
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
