"""delivery/briefing_tldr.py — ready-to-send artifacts for the daily TLDR.

Renders a ``TldrSummary`` (engine.daily_briefing_tldr.generate_tldr) into
three deliverable artifacts, mirroring delivery.port_supply_shock_digest:

  * ``render_plain_text`` — the TLDR verbatim under a one-line header.
                            The TLDR is already SMS/Slack-shaped (50-120
                            words, no markup), so this doubles as the
                            text/plain email part AND the Slack/webhook
                            body. For an SMS channel the raw
                            ``summary.text`` is the tightest form.
  * ``render_html``       — single-page inline-styled HTML lede box
                            (every style on the element, no <style> block)
                            so it survives Gmail / Outlook style stripping.
  * ``build_subject_line`` — short subject ("Daily Shipping Briefing —
                             TLDR (2026-05-29)").

Plus a gating helper:

  * ``should_send``       — False for a None summary, empty text, or the
                            canonical no-signal placeholder, so operators
                            don't get a "nothing happened" blast on a
                            quiet day (matches the shock-digest's
                            should_send contract).

Design constraints (same as the shock digest): pure rendering — no SMTP,
no HTTP, no file I/O; stdlib only; inline CSS; defensively duck-typed over
the summary so a shape change can't crash a delivery tick.
"""
from __future__ import annotations

from html import escape


__all__ = [
    "should_send",
    "build_subject_line",
    "render_plain_text",
    "render_html",
    "send_briefing_tldr",
    "BRIEFING_CHANNEL_PREFIX",
]


# Channel-naming opt-in convention (mirrors operator_digest's "ops-"):
# only DeliveryChannels whose name starts with this prefix receive the
# daily briefing TLDR. Nothing is dispatched by default.
BRIEFING_CHANNEL_PREFIX = "briefing-"


# Palette mirrors delivery.port_supply_shock_digest for a consistent
# platform email look.
_COLOR_TEXT = "#24292e"
_COLOR_MUTED = "#586069"
_COLOR_BG = "#ffffff"
_COLOR_ACCENT_BG = "#fafbfc"
_COLOR_STEEL = "#2a3b4d"
_COLOR_GREEN = "#22863a"


def _text_of(summary) -> str:
    return str(getattr(summary, "text", "") or "").strip()


def should_send(summary) -> bool:
    """True iff the TLDR carries material signal worth delivering.

    A ``None`` summary, an empty text, or the canonical no-signal
    placeholder all gate to False — quiet days produce no artifacts so
    downstream channels have nothing to dispatch.
    """
    text = _text_of(summary)
    if not text:
        return False
    # Compare against the engine's canonical sentinel (single source of
    # truth); fall back to the literal if the import is unavailable.
    try:
        from engine.daily_briefing_tldr import _NO_SIGNAL
    except Exception:
        _NO_SIGNAL = "No material shipping-stress signals today."
    return text != _NO_SIGNAL


def build_subject_line(summary, date_iso: str = "") -> str:
    """Short, scannable subject line for the daily briefing TLDR."""
    date_tail = f" ({date_iso})" if date_iso else ""
    return f"Daily Shipping Briefing — TLDR{date_tail}"


def render_plain_text(summary, date_iso: str = "") -> str:
    """Plain-text body: a one-line header + the TLDR verbatim.

    Used as the text/plain email part and the Slack / webhook body. For
    SMS, the raw ``summary.text`` is the tightest form.
    """
    text = _text_of(summary)
    header = "Daily Shipping Briefing — TLDR"
    if date_iso:
        header += f" ({date_iso})"
    return f"{header}\n{'=' * len(header)}\n\n{text}\n"


def render_html(summary, date_iso: str = "") -> str:
    """Render the TLDR as a complete inline-styled HTML lede box.

    A single accent-bordered card: an eyebrow (date + LLM/Template
    provenance) above the one-paragraph summary. Email-safe — every style
    attribute lives on its element.
    """
    text = escape(_text_of(summary))
    source = str(getattr(summary, "source", "") or "")
    src_label = "LLM" if source == "claude" else "Template"
    date_str = escape(date_iso) if date_iso else "—"

    eyebrow = (
        f"<div style=\"font-size:11px;font-weight:700;letter-spacing:0.12em;"
        f"text-transform:uppercase;color:{_COLOR_STEEL};\">"
        f"Daily Shipping Briefing &middot; TL;DR &middot; {date_str} "
        f"&middot; {src_label}"
        f"</div>"
    )
    body = (
        f"<p style=\"margin:8px 0 0 0;font-size:15px;line-height:1.55;"
        f"color:{_COLOR_TEXT};\">{text}</p>"
    )
    card = (
        f"<div style=\"border-left:6px solid {_COLOR_GREEN};padding:14px 18px;"
        f"background:{_COLOR_ACCENT_BG};\">{eyebrow}{body}</div>"
    )
    return (
        "<!DOCTYPE html>"
        "<html><head><meta charset=\"utf-8\">"
        "<title>Daily Shipping Briefing — TLDR</title></head>"
        f"<body style=\"font-family:Helvetica,Arial,sans-serif;"
        f"color:{_COLOR_TEXT};background:{_COLOR_BG};margin:0;padding:0;\">"
        f"<div style=\"max-width:680px;margin:0 auto;padding:24px;\">"
        f"{card}"
        f"</div></body></html>"
    )


# ─── Channel dispatch ────────────────────────────────────────────────────────


def _structured_payload(summary, date_iso: str, kind: str) -> dict:
    """JSON envelope for slack / webhook / discord.

    Mirrors engine.operator_digest._structured_payload's per-kind shapes:
      * slack   — ``{"text", "attachments":[{"color","text"}]}``
      * discord — ``{"content", "embeds":[{"title","description"}]}``
      * webhook — flat envelope with the raw fields on top-level keys
    """
    subject = build_subject_line(summary, date_iso)
    text = _text_of(summary)
    if kind == "slack":
        return {
            "text": subject,
            "attachments": [{"color": _COLOR_STEEL, "text": text}],
        }
    if kind == "discord":
        return {
            "content": subject,
            "embeds": [{"title": subject, "description": text}],
        }
    return {
        "event_type":   "briefing_tldr",
        "generated_at": str(getattr(summary, "generated_at", "") or ""),
        "date":         date_iso,
        "source":       str(getattr(summary, "source", "") or ""),
        "subject":      subject,
        "text":         text,
    }


def send_briefing_tldr(channel, summary, date_iso: str = ""):
    """Dispatch the day's TLDR to one ``DeliveryChannel``.

    Mirrors ``engine.operator_digest.send_operator_digest``: reuses the
    transports in ``engine.alert_delivery`` (so timeout / retry /
    credential-resolution logic stays in one place) and dispatches on
    ``channel.kind`` — email via ``_deliver_digest_email``,
    slack/webhook/discord via ``_post_json``. Always returns a
    ``DeliveryResult``; a disabled channel or a no-signal summary is a
    no-op success so the caller's loop stays clean.
    """
    from engine.alert_delivery import DeliveryResult

    if not getattr(channel, "enabled", False):
        return DeliveryResult(success=True, status_code=0, error_msg="channel disabled")
    if not should_send(summary):
        return DeliveryResult(
            success=True, status_code=0, error_msg="no material signal",
        )

    kind = getattr(channel, "kind", "") or ""

    if kind == "email":
        from engine.alert_delivery import _deliver_digest_email, _get_smtp_config
        if _get_smtp_config() is None:
            return DeliveryResult(
                success=False, status_code=0, error_msg="SMTP not configured",
            )
        payload = {
            "subject":   build_subject_line(summary, date_iso),
            "html_body": render_html(summary, date_iso),
            "text_body": render_plain_text(summary, date_iso),
        }
        return _deliver_digest_email(channel, payload)

    if kind in ("slack", "webhook"):
        from engine.alert_delivery import _post_json
        target = getattr(channel, "target", "") or ""
        return _post_json(target, _structured_payload(summary, date_iso, kind))

    if kind == "discord":
        from engine.alert_delivery import _DISCORD_WEBHOOK_PREFIXES, _post_json
        target = getattr(channel, "target", "") or ""
        if not any(target.startswith(p) for p in _DISCORD_WEBHOOK_PREFIXES):
            return DeliveryResult(
                success=False, status_code=0,
                error_msg="target must be a Discord webhook URL",
            )
        return _post_json(target, _structured_payload(summary, date_iso, "discord"))

    return DeliveryResult(
        success=False, status_code=0,
        error_msg=f"unsupported channel kind: {kind}",
    )
