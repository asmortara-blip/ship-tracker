"""engine/operator_digest.py — daily Operator-Dashboard email digest.

Companion module to ``ui.tab_operator`` (commit 532ba21). Where that
module renders the four telemetry layers as a Streamlit tab, this
module rolls the same telemetry into a single HTML email so a CFO/CTO
who doesn't log in every day still sees the system summary in their
inbox.

Decision flow
-------------
1. ``worker.scheduler.run_operator_digest_job`` is invoked once per
   daily cron tick. It loads every ``DeliveryChannel`` whose ``name``
   starts with ``"ops-"`` (the convention for digest subscribers) and
   calls ``send_operator_digest`` for each.
2. ``send_operator_digest(channel)`` calls ``build_digest`` to
   assemble an ``OperatorDigest`` snapshot, then formats per
   ``channel.kind`` (html for email, plain text + structured payload
   otherwise) and dispatches via the existing transport in
   ``engine.alert_delivery`` (``_post_json`` for slack/webhook/discord,
   SMTP for email).
3. ``build_digest`` wraps every engine call in its own try/except so
   one failing telemetry layer (e.g. an empty source_health table)
   never blanks the rest of the digest.

The four telemetry layers
-------------------------
- ``engine.llm_telemetry.get_usage_summary``    — LLM cost + call count
- ``engine.alert_analytics.compute_alert_metrics`` — alert ack metrics
  + ``get_unacknowledged_critical`` for the unacked-critical count
- ``engine.perf_telemetry.get_perf_summary``    — tab render success +
  slowest-tab signal
- ``engine.source_health.get_health_summary``   — currently-down sources

summary_status
--------------
The digest carries a top-level ``summary_status`` field with three
values driving the color-coded HTML header:

  - ``"critical"``  — unacked CRITICAL > 0 (always wins)
  - ``"attention"`` — render_success_rate < 0.95 OR current_outages
                      non-empty
  - ``"healthy"``   — everything else

Reusing transport
-----------------
This module never opens its own HTTP / SMTP connection. It delegates
to ``engine.alert_delivery._post_json`` and ``_deliver_email`` so the
timeout, retry, and credential-resolution logic only lives in one
place. The ``ops-`` channel prefix is a convention enforced by
``run_operator_digest_job``, NOT by this module — ``send_operator_digest``
will happily dispatch to any channel the caller hands it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from engine.alert_delivery import DeliveryChannel, DeliveryResult


# ─── Constants ─────────────────────────────────────────────────────────────

# Channels whose ``name`` starts with this prefix are subscribers to the
# daily operator digest. Documented here so consumers (UI, docs, tests)
# share one source of truth.
OPERATOR_CHANNEL_PREFIX = "ops-"

# summary_status thresholds. Pulled into named constants so the test
# suite can pin the exact boundary (e.g. 95% render success → attention,
# 95.0% → still attention because of "< 0.95"). Matches the
# ``_success_accent`` thresholds in ``ui.tab_operator``.
_RENDER_SUCCESS_ATTENTION = 0.95


# ─── Dataclass ─────────────────────────────────────────────────────────────

@dataclass
class OperatorDigest:
    """Snapshot of the four telemetry layers, ready for rendering.

    Eight metric fields plus ``generated_at`` (ISO timestamp) and
    ``summary_status`` (color-coded header driver). The eight metrics
    line up 1:1 with the 8-KPI grid in ``ui.tab_operator``:

      - ``llm_cost_usd``       — total LLM spend in window
      - ``llm_calls``          — total LLM calls in window
      - ``alert_count``        — total alerts in window
      - ``ack_rate``           — 0.0..1.0 fraction acknowledged
      - ``unacked_critical``   — count of unacked CRITICAL alerts
      - ``render_success_rate`` — 0.0..1.0 fraction of successful renders
      - ``slowest_tab``        — tab name with highest median_ms (or "")
      - ``current_outages``    — list[str] of currently-down sources

    Sentinel values when a telemetry layer is empty or failing:
      - integer counts default to 0
      - rates default to 0.0
      - slowest_tab defaults to "" (empty string, not None)
      - current_outages defaults to [] (empty list)

    These match what the upstream engine helpers return for an empty
    DB so the digest stays "filled out" rather than full of placeholders.
    """
    generated_at: str = ""
    llm_cost_usd: float = 0.0
    llm_calls: int = 0
    alert_count: int = 0
    ack_rate: float = 0.0
    unacked_critical: int = 0
    render_success_rate: float = 0.0
    slowest_tab: str = ""
    current_outages: list[str] = field(default_factory=list)
    summary_status: str = "healthy"  # "healthy" | "attention" | "critical"


# ─── Internal helpers ──────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summary_status_from(
    *,
    unacked_critical: int,
    render_success_rate: float,
    current_outages: list[str],
    has_render_data: bool = True,
) -> str:
    """Derive ``summary_status`` from the driving metrics.

    Order matters: CRITICAL always wins. A site with unacked criticals
    AND a down source should still surface "critical", not "attention".

    ``has_render_data`` exists so an empty perf-telemetry table (where
    ``render_success_rate`` defaults to 0.0) does NOT trip "attention".
    Without renders we have no signal about render health, so the
    default position is "no news is good news" — healthy.
    """
    if unacked_critical > 0:
        return "critical"
    if has_render_data and render_success_rate < _RENDER_SUCCESS_ATTENTION:
        return "attention"
    if current_outages:
        return "attention"
    return "healthy"


def _slowest_tab_from(perf: dict) -> str:
    """Pull the slowest tab name from a ``get_perf_summary`` return.

    Uses ``top_slow_tabs[0]`` when present (already sorted desc by
    median_ms). Falls back to scanning ``by_tab`` when the top-slow
    list is missing (e.g. a stale perf dict shape). Returns "" when
    no render data exists.
    """
    top_slow = perf.get("top_slow_tabs") or []
    if top_slow:
        entry = top_slow[0]
        if isinstance(entry, dict):
            return str(entry.get("tab_name", "") or "")

    # Fallback path — derive from by_tab when top_slow_tabs is absent.
    by_tab = perf.get("by_tab") or {}
    if not by_tab:
        return ""
    try:
        best_name = ""
        best_median = -1
        for name, info in by_tab.items():
            median = int(info.get("median_ms", 0)) if isinstance(info, dict) else 0
            if median > best_median:
                best_median = median
                best_name = str(name)
        return best_name
    except Exception:
        return ""


# ─── Public API: assembly ──────────────────────────────────────────────────

def build_digest(
    *,
    alert_window_days: int = 30,
    llm_window_days: int = 7,
    perf_window_hours: int = 24,
) -> OperatorDigest:
    """Assemble an ``OperatorDigest`` snapshot from the four telemetry
    layers.

    Every engine call lives in its own ``try/except`` so a single
    failure (DB schema drift, missing table, transient lock) never
    blanks the whole digest — the failing layer degrades to sentinel
    defaults (0 / 0.0 / "" / []) and the rest of the digest still ships.

    Parameters
    ----------
    alert_window_days:
        Look-back window for alert analytics + the unacked-critical
        count. Default 30, matching ``ui.tab_operator``.
    llm_window_days:
        Look-back window for LLM telemetry. Default 7.
    perf_window_hours:
        Look-back window for tab-render telemetry + source-health.
        Default 24.

    Returns
    -------
    OperatorDigest
        Populated snapshot. ``generated_at`` is the assembly timestamp
        (NOT the data window start). ``summary_status`` is derived
        post-assembly via ``_summary_status_from``.
    """
    digest = OperatorDigest(generated_at=_now_iso())

    # LLM telemetry — cost + calls
    try:
        from engine.llm_telemetry import get_usage_summary
        llm = get_usage_summary(window_days=int(llm_window_days))
        digest.llm_cost_usd = float(llm.get("total_cost_usd", 0.0) or 0.0)
        digest.llm_calls = int(llm.get("total_calls", 0) or 0)
    except Exception as exc:
        logger.warning(f"operator_digest: LLM telemetry failed: {exc}")

    # Alert analytics — total + ack rate
    try:
        from engine.alert_analytics import compute_alert_metrics
        metrics = compute_alert_metrics(window_days=int(alert_window_days))
        digest.alert_count = int(getattr(metrics, "total_alerts", 0) or 0)
        digest.ack_rate = float(getattr(metrics, "ack_rate", 0.0) or 0.0)
    except Exception as exc:
        logger.warning(f"operator_digest: alert analytics failed: {exc}")

    # Unacked CRITICAL count — the operationally critical signal
    try:
        from engine.alert_analytics import get_unacknowledged_critical
        unack = get_unacknowledged_critical(window_days=int(alert_window_days))
        digest.unacked_critical = len(unack or [])
    except Exception as exc:
        logger.warning(f"operator_digest: unack-critical fetch failed: {exc}")

    # Tab render telemetry — success rate + slowest tab.
    # Track whether we actually saw renders so the status derivation
    # below can avoid tripping "attention" on an empty perf table
    # (success_rate=0.0 is the empty-shape sentinel, not a real signal).
    has_render_data = False
    try:
        from engine.perf_telemetry import get_perf_summary
        perf = get_perf_summary(window_hours=int(perf_window_hours))
        total_renders = int(perf.get("total_renders", 0) or 0)
        has_render_data = total_renders > 0
        digest.render_success_rate = float(perf.get("success_rate", 0.0) or 0.0)
        digest.slowest_tab = _slowest_tab_from(perf)
    except Exception as exc:
        logger.warning(f"operator_digest: perf telemetry failed: {exc}")

    # Source health — currently-down list
    try:
        from engine.source_health import get_health_summary
        health = get_health_summary(window_hours=int(perf_window_hours))
        outages = health.get("current_outages") or []
        # Defensive copy + str-coerce so downstream rendering can't
        # accidentally mutate the engine's cached list.
        digest.current_outages = [str(s) for s in outages]
    except Exception as exc:
        logger.warning(f"operator_digest: source health failed: {exc}")

    # Final status — single source of truth so renderers don't
    # re-derive it from the raw numbers and disagree.
    digest.summary_status = _summary_status_from(
        unacked_critical=digest.unacked_critical,
        render_success_rate=digest.render_success_rate,
        current_outages=digest.current_outages,
        has_render_data=has_render_data,
    )

    return digest


# ─── Public API: formatting ────────────────────────────────────────────────

# Severity palette borrowed from ``alert_delivery._SEVERITY_COLOR`` —
# critical=red, attention=amber, healthy=green. Inline so this module
# stays standalone (alert_delivery's palette is keyed by alert severity,
# not summary_status, so we can't reuse the dict directly).
_STATUS_COLOR = {
    "critical":  "#d73a49",  # red
    "attention": "#f97316",  # amber/orange
    "healthy":   "#22863a",  # green
}

_STATUS_HEADLINE = {
    "critical":  "System CRITICAL",
    "attention": "System needs attention",
    "healthy":   "System healthy",
}


def _format_usd_cents(value: float) -> str:
    """Compact USD formatter used in the KPI grid. Falls back to a
    plain f-string so a util-package outage can't blank the digest."""
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _format_pct(value: float) -> str:
    """0.0..1.0 → 'XX.X%'. Tolerates None / non-numeric input."""
    try:
        return f"{float(value) * 100.0:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def _kpi_cell_html(label: str, value: str, accent: str) -> str:
    """One KPI tile — label on top, value below, left-border accent.

    Inline-styled because email clients strip <style> blocks; the WSJ-ish
    palette mirrors ``ui.styles`` (steel blue text on white).
    """
    return (
        f"<td style='padding:8px 6px;vertical-align:top;width:25%;'>"
        f"<div style='border-left:3px solid {accent};padding:6px 10px;'>"
        f"<div style='font-size:10px;font-weight:700;letter-spacing:0.08em;"
        f"text-transform:uppercase;color:#586069;'>{label}</div>"
        f"<div style='font-size:18px;font-weight:700;color:#24292e;"
        f"margin-top:2px;'>{value}</div>"
        f"</div></td>"
    )


def format_digest_html(digest: OperatorDigest) -> str:
    """Render an ``OperatorDigest`` as a single-page inline-styled HTML
    email.

    Layout
    ------
    Top:    Status header — color-coded by ``summary_status``.
    Middle: 8-KPI grid in two rows of four cells each.
    Bottom: Outages list (only rendered when ``current_outages`` is
            non-empty).

    Inline styles only — every email client that matters (Gmail,
    Outlook, Apple Mail) strips ``<style>`` blocks, so the layout has
    to survive on per-element styling.
    """
    status = digest.summary_status if digest.summary_status in _STATUS_COLOR else "healthy"
    status_color = _STATUS_COLOR[status]
    headline = _STATUS_HEADLINE[status]

    # ── Header ────────────────────────────────────────────────────────
    header_html = (
        f"<div style='border-left:6px solid {status_color};padding:14px 18px;"
        f"background:#fafbfc;'>"
        f"<div style='font-size:11px;font-weight:700;letter-spacing:0.12em;"
        f"text-transform:uppercase;color:{status_color};'>"
        f"Operator Digest · {status.upper()}"
        f"</div>"
        f"<h2 style='margin:4px 0 0 0;font-size:20px;color:#24292e;'>{headline}</h2>"
        f"<div style='font-size:12px;color:#586069;margin-top:4px;'>"
        f"Generated {digest.generated_at}"
        f"</div>"
        f"</div>"
    )

    # ── KPI grid (2 rows × 4 cells) ───────────────────────────────────
    # Row 1: LLM calls / LLM cost / alerts / ack rate
    # Row 2: unacked critical / render success / slowest tab / outages
    # Accent on unacked-critical is red whenever > 0; render-success is
    # green ≥99%, amber ≥95%, else red. The remaining cells use the
    # neutral steel accent.
    neutral = "#2a3b4d"
    red = "#d73a49"
    green = "#22863a"
    amber = "#f97316"

    if digest.render_success_rate >= 0.99:
        render_accent = green
    elif digest.render_success_rate >= 0.95:
        render_accent = amber
    else:
        render_accent = red

    unack_accent = red if digest.unacked_critical > 0 else green
    outages_count = len(digest.current_outages)
    outages_accent = red if outages_count > 0 else green
    slowest_value = digest.slowest_tab if digest.slowest_tab else "—"

    row1 = (
        "<tr>"
        + _kpi_cell_html("LLM Calls", f"{digest.llm_calls:,}", neutral)
        + _kpi_cell_html("LLM Cost", _format_usd_cents(digest.llm_cost_usd), neutral)
        + _kpi_cell_html("Alerts", f"{digest.alert_count:,}", neutral)
        + _kpi_cell_html("Ack Rate", _format_pct(digest.ack_rate), neutral)
        + "</tr>"
    )
    row2 = (
        "<tr>"
        + _kpi_cell_html("Unacked Critical", f"{digest.unacked_critical}", unack_accent)
        + _kpi_cell_html(
            "Render Success",
            _format_pct(digest.render_success_rate),
            render_accent,
        )
        + _kpi_cell_html("Slowest Tab", slowest_value, neutral)
        + _kpi_cell_html("Outages", f"{outages_count}", outages_accent)
        + "</tr>"
    )

    kpi_html = (
        "<table style='width:100%;border-collapse:collapse;margin-top:16px;"
        "font-family:Helvetica,Arial,sans-serif;'>"
        f"{row1}{row2}"
        "</table>"
    )

    # ── Outages list (only when non-empty) ────────────────────────────
    outages_html = ""
    if digest.current_outages:
        items = "".join(
            f"<li style='margin:4px 0;color:#24292e;'>{src}</li>"
            for src in digest.current_outages
        )
        outages_html = (
            "<div style='margin-top:18px;padding:12px 14px;border-left:3px solid "
            f"{red};background:#fafbfc;'>"
            "<div style='font-size:11px;font-weight:700;letter-spacing:0.08em;"
            "text-transform:uppercase;color:#586069;'>Current Outages</div>"
            f"<ul style='padding-left:18px;margin:8px 0 0 0;font-size:13px;'>{items}</ul>"
            "</div>"
        )

    body_html = (
        "<html><body style='font-family:Helvetica,Arial,sans-serif;color:#24292e;"
        "background:#ffffff;margin:0;padding:0;'>"
        "<div style='max-width:680px;margin:0 auto;padding:24px;'>"
        f"{header_html}"
        f"{kpi_html}"
        f"{outages_html}"
        "</div></body></html>"
    )
    return body_html


def format_digest_text(digest: OperatorDigest) -> str:
    """Plain-text fallback for clients that don't render HTML.

    Same KPIs, same ordering, no markup. Used as the ``text/plain``
    part of the multipart email AND as the body for non-email
    channels (slack/webhook/etc) that consume a structured payload but
    want the human-readable text for fallback rendering.
    """
    status = (digest.summary_status or "healthy").upper()
    headline = _STATUS_HEADLINE.get(digest.summary_status, _STATUS_HEADLINE["healthy"])

    lines = [
        f"Operator Digest · {status}",
        "=" * 40,
        headline,
        f"Generated: {digest.generated_at}",
        "",
        f"LLM Calls:        {digest.llm_calls:,}",
        f"LLM Cost:         {_format_usd_cents(digest.llm_cost_usd)}",
        f"Alerts:           {digest.alert_count:,}",
        f"Ack Rate:         {_format_pct(digest.ack_rate)}",
        f"Unacked CRITICAL: {digest.unacked_critical}",
        f"Render Success:   {_format_pct(digest.render_success_rate)}",
        f"Slowest Tab:      {digest.slowest_tab or '—'}",
        f"Outages:          {len(digest.current_outages)}",
    ]
    if digest.current_outages:
        lines.append("")
        lines.append("Current outages:")
        for src in digest.current_outages:
            lines.append(f"  - {src}")
    return "\n".join(lines)


# ─── Public API: delivery ──────────────────────────────────────────────────

def _digest_subject(digest: OperatorDigest) -> str:
    """Build the email subject. Status-prefixed so a CRITICAL digest
    sorts and filters obviously in the recipient's inbox."""
    status = (digest.summary_status or "healthy").upper()
    return f"[{status}] Operator Digest · {digest.generated_at[:10]}"


def send_operator_digest(channel: DeliveryChannel) -> DeliveryResult:
    """Build a fresh digest and dispatch it to ``channel``.

    Dispatch on ``channel.kind``:
      - ``"email"``    → SMTP via ``alert_delivery._deliver_email``-shaped
                         payload (subject + html_body + text_body)
      - ``"slack"`` / ``"webhook"`` / ``"discord"`` → POST a structured
                         JSON envelope via ``alert_delivery._post_json``
      - anything else → explicit "unsupported kind" failure

    Always returns a ``DeliveryResult`` — network errors and assembly
    errors are caught and surfaced via ``success=False``. Reuses the
    existing transport in ``engine.alert_delivery`` so the timeout /
    retry / credential-resolution logic stays in one place.
    """
    if not channel.enabled:
        return DeliveryResult(
            success=True, status_code=0, error_msg="channel disabled"
        )

    # Assemble the snapshot. ``build_digest`` already wraps each engine
    # call in try/except so this should not raise — but we belt-and-
    # braces it so a regression there can't propagate.
    try:
        digest = build_digest()
    except Exception as exc:
        logger.warning(f"send_operator_digest: build_digest raised: {exc}")
        return DeliveryResult(
            success=False,
            status_code=0,
            error_msg=f"build failed: {exc}",
        )

    # ── Email path: SMTP via the existing transport ────────────────────
    if channel.kind == "email":
        from engine.alert_delivery import (
            _get_smtp_config,
            DeliveryResult as _DR,  # alias for static readers
        )
        config = _get_smtp_config()
        if config is None:
            return _DR(
                success=False,
                status_code=0,
                error_msg="SMTP not configured",
            )
        # Reuse the digest-email transport from alert_delivery — same
        # MIMEMultipart + STARTTLS + login + sendmail dance, just with
        # OUR pre-formatted payload.
        from engine.alert_delivery import _deliver_digest_email
        payload = {
            "subject":   _digest_subject(digest),
            "html_body": format_digest_html(digest),
            "text_body": format_digest_text(digest),
        }
        return _deliver_digest_email(channel, payload)

    # ── Slack / webhook / discord — POST a JSON envelope ────────────────
    # The envelope mirrors the operator-dashboard "what's in the box"
    # so a generic receiver can render the same KPIs without parsing
    # the HTML body.
    if channel.kind in ("slack", "webhook"):
        from engine.alert_delivery import _post_json
        payload = _structured_payload(digest, channel.kind)
        return _post_json(channel.target, payload)

    if channel.kind == "discord":
        from engine.alert_delivery import _post_json, _DISCORD_WEBHOOK_PREFIXES
        target = channel.target or ""
        if not any(target.startswith(prefix) for prefix in _DISCORD_WEBHOOK_PREFIXES):
            return DeliveryResult(
                success=False,
                status_code=0,
                error_msg="target must be a Discord webhook URL",
            )
        payload = _structured_payload(digest, "discord")
        return _post_json(target, payload)

    # Reserved for future backends (sms, pagerduty, opsgenie, teams,
    # ...). Surface as an explicit failure rather than silently dropping
    # so the operator sees the misconfiguration in the worker log.
    return DeliveryResult(
        success=False,
        status_code=0,
        error_msg=f"unsupported channel kind: {channel.kind}",
    )


def _structured_payload(digest: OperatorDigest, kind: str) -> dict:
    """Build the JSON envelope sent to slack/webhook/discord.

    Slack: ``{"text": str, "attachments": [...]}`` so the digest renders
       as a readable block in the channel even when the receiver isn't
       parsing the structured fields.
    Webhook: flat envelope with every KPI on top-level keys so generic
       receivers can route by field.
    Discord: ``{"content": str, "embeds": [...]}`` mirroring the
       ``format_discord_payload`` shape from ``alert_delivery``.
    """
    text_body = format_digest_text(digest)
    status = (digest.summary_status or "healthy").upper()
    summary_line = (
        f"Operator Digest · {status} — "
        f"{digest.llm_calls:,} LLM calls · "
        f"{_format_usd_cents(digest.llm_cost_usd)} · "
        f"{digest.alert_count:,} alerts · "
        f"{digest.unacked_critical} unacked CRITICAL · "
        f"render {_format_pct(digest.render_success_rate)}"
    )

    if kind == "slack":
        return {
            "text": summary_line,
            "attachments": [{"color": _STATUS_COLOR.get(digest.summary_status, "#e8e6e1"),
                             "text": text_body}],
        }

    if kind == "discord":
        return {
            "content": summary_line,
            "embeds": [{
                "title": _STATUS_HEADLINE.get(digest.summary_status, "Operator Digest"),
                "description": text_body,
            }],
        }

    # Default to the flat webhook envelope. Includes the raw OperatorDigest
    # fields so a generic receiver doesn't need to parse the text body.
    return {
        "event_type":          "operator_digest",
        "generated_at":        digest.generated_at,
        "summary_status":      digest.summary_status,
        "llm_cost_usd":        digest.llm_cost_usd,
        "llm_calls":           digest.llm_calls,
        "alert_count":         digest.alert_count,
        "ack_rate":            digest.ack_rate,
        "unacked_critical":    digest.unacked_critical,
        "render_success_rate": digest.render_success_rate,
        "slowest_tab":         digest.slowest_tab,
        "current_outages":     list(digest.current_outages),
        "summary_text":        text_body,
    }
