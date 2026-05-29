"""Tests for delivery.briefing_tldr — ready-to-send daily-TLDR artifacts.

Defining properties: should_send gates out None / empty / no-signal
summaries; the rendered text carries the TLDR verbatim; the HTML is
inline-styled, escaped, and well-formed; and every renderer is defensive
over a duck-typed summary.
"""
from __future__ import annotations

import types
from dataclasses import dataclass

from delivery.briefing_tldr import (
    BRIEFING_CHANNEL_PREFIX,
    _structured_payload,
    build_subject_line,
    render_html,
    render_plain_text,
    send_briefing_tldr,
    should_send,
)
from engine.daily_briefing_tldr import _NO_SIGNAL


@dataclass
class _Summary:
    text: str
    source: str = "claude"


def _chan(kind="slack", target="https://hooks.slack.com/services/x",
          enabled=True, name="briefing-desk"):
    return types.SimpleNamespace(
        name=name, kind=kind, target=target, enabled=enabled,
    )


def _ok_post_spy(captured: dict):
    """A _post_json replacement that records its args and returns success."""
    def _post(target, payload, **kwargs):
        captured["target"] = target
        captured["payload"] = payload
        from engine.alert_delivery import DeliveryResult
        return DeliveryResult(success=True, status_code=200, error_msg="")
    return _post


# ─── should_send gating ──────────────────────────────────────────────────────

def test_should_send_true_for_real_signal() -> None:
    assert should_send(_Summary("Suez disruption lifts SSI to 0.62.")) is True


def test_should_send_false_for_none() -> None:
    assert should_send(None) is False


def test_should_send_false_for_empty_or_whitespace() -> None:
    assert should_send(_Summary("   ")) is False


def test_should_send_false_for_no_signal_placeholder() -> None:
    assert should_send(_Summary(_NO_SIGNAL, source="template")) is False


def test_should_send_false_for_attribute_light_object() -> None:
    assert should_send(object()) is False


# ─── subject line ────────────────────────────────────────────────────────────

def test_subject_line_includes_date() -> None:
    s = build_subject_line(_Summary("x"), "2026-05-29")
    assert "2026-05-29" in s and "TLDR" in s


def test_subject_line_without_date_has_no_parenthetical() -> None:
    s = build_subject_line(_Summary("x"))
    assert "TLDR" in s and "(" not in s


# ─── plain text ──────────────────────────────────────────────────────────────

def test_plain_text_contains_verbatim_tldr() -> None:
    txt = render_plain_text(
        _Summary("ZIM best positioned to the rate uplift."), "2026-05-29",
    )
    assert "ZIM best positioned to the rate uplift." in txt
    assert "Daily Shipping Briefing" in txt
    assert "2026-05-29" in txt


# ─── HTML ────────────────────────────────────────────────────────────────────

def test_html_is_escaped_and_well_formed() -> None:
    s = _Summary("Rates rise; <script>alert(1)</script> & co.", source="claude")
    html = render_html(s, "2026-05-29")
    assert "<script>alert(1)</script>" not in html      # raw tag escaped away
    assert "&lt;script&gt;" in html
    assert "&amp;" in html                                # & escaped
    assert "2026-05-29" in html
    assert html.startswith("<!DOCTYPE html>")
    assert "LLM" in html                                  # claude provenance chip


def test_html_template_source_shows_template_label() -> None:
    html = render_html(_Summary("x", source="template"))
    assert "Template" in html


# ─── defensive / duck-typed ──────────────────────────────────────────────────

def test_renderers_never_crash_on_attribute_light_object() -> None:
    assert render_plain_text(object()) is not None
    assert render_html(object()).startswith("<!DOCTYPE html>")
    assert build_subject_line(object(), "2026-05-29") != ""


# ─── send_briefing_tldr dispatch ─────────────────────────────────────────────

def test_prefix_constant_is_opt_in() -> None:
    assert BRIEFING_CHANNEL_PREFIX == "briefing-"


def test_send_disabled_channel_is_noop_success() -> None:
    res = send_briefing_tldr(_chan(enabled=False), _Summary("Suez lifts SSI."))
    assert res.success is True
    assert "disabled" in res.error_msg


def test_send_no_signal_is_noop_success() -> None:
    res = send_briefing_tldr(_chan(), _Summary(_NO_SIGNAL, source="template"))
    assert res.success is True
    assert "no material signal" in res.error_msg


def test_send_slack_posts_attachment_envelope(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr("engine.alert_delivery._post_json", _ok_post_spy(captured))
    res = send_briefing_tldr(
        _chan(kind="slack", target="https://hooks.slack.com/services/x"),
        _Summary("Suez lifts SSI to 0.62."), "2026-05-29",
    )
    assert res.success is True
    assert captured["target"] == "https://hooks.slack.com/services/x"
    assert captured["payload"]["text"]                       # subject line
    assert "Suez lifts SSI to 0.62." in captured["payload"]["attachments"][0]["text"]


def test_send_webhook_posts_flat_envelope(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr("engine.alert_delivery._post_json", _ok_post_spy(captured))
    send_briefing_tldr(
        _chan(kind="webhook", target="https://example.com/hook"),
        _Summary("Body text.", source="claude"), "2026-05-29",
    )
    p = captured["payload"]
    assert p["event_type"] == "briefing_tldr"
    assert p["source"] == "claude"
    assert p["text"] == "Body text."
    assert p["date"] == "2026-05-29"


def test_send_discord_rejects_non_discord_url() -> None:
    res = send_briefing_tldr(
        _chan(kind="discord", target="https://example.com/x"), _Summary("x"),
    )
    assert res.success is False
    assert "Discord webhook" in res.error_msg


def test_send_discord_valid_url_posts_embed(monkeypatch) -> None:
    from engine.alert_delivery import _DISCORD_WEBHOOK_PREFIXES
    captured: dict = {}
    monkeypatch.setattr("engine.alert_delivery._post_json", _ok_post_spy(captured))
    url = _DISCORD_WEBHOOK_PREFIXES[0] + "123/abc"
    res = send_briefing_tldr(
        _chan(kind="discord", target=url), _Summary("Body."), "2026-05-29",
    )
    assert res.success is True
    assert captured["payload"]["content"]
    assert captured["payload"]["embeds"][0]["description"] == "Body."


def test_send_email_without_smtp_config_fails(monkeypatch) -> None:
    monkeypatch.setattr("engine.alert_delivery._get_smtp_config", lambda: None)
    res = send_briefing_tldr(_chan(kind="email", target="ops@x.com"), _Summary("x"))
    assert res.success is False
    assert "SMTP" in res.error_msg


def test_send_email_with_smtp_delegates_to_transport(monkeypatch) -> None:
    monkeypatch.setattr("engine.alert_delivery._get_smtp_config", lambda: object())
    captured: dict = {}

    def _fake_deliver(channel, payload):
        captured["payload"] = payload
        from engine.alert_delivery import DeliveryResult
        return DeliveryResult(success=True, status_code=0, error_msg="")

    monkeypatch.setattr("engine.alert_delivery._deliver_digest_email", _fake_deliver)
    res = send_briefing_tldr(
        _chan(kind="email", target="ops@x.com"),
        _Summary("Body.", source="claude"), "2026-05-29",
    )
    assert res.success is True
    assert "subject" in captured["payload"]
    assert "html_body" in captured["payload"]
    assert "Body." in captured["payload"]["text_body"]


def test_send_unsupported_kind_fails() -> None:
    res = send_briefing_tldr(_chan(kind="sms"), _Summary("x"))
    assert res.success is False
    assert "unsupported" in res.error_msg


def test_structured_payload_per_kind_shapes() -> None:
    s = _Summary("T.", source="claude")
    slack = _structured_payload(s, "2026-05-29", "slack")
    assert slack["attachments"][0]["text"] == "T."
    disc = _structured_payload(s, "2026-05-29", "discord")
    assert disc["content"] and disc["embeds"][0]["description"] == "T."
    hook = _structured_payload(s, "2026-05-29", "webhook")
    assert hook["event_type"] == "briefing_tldr" and hook["text"] == "T."
