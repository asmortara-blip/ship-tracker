"""Tests for delivery.briefing_tldr — ready-to-send daily-TLDR artifacts.

Defining properties: should_send gates out None / empty / no-signal
summaries; the rendered text carries the TLDR verbatim; the HTML is
inline-styled, escaped, and well-formed; and every renderer is defensive
over a duck-typed summary.
"""
from __future__ import annotations

from dataclasses import dataclass

from delivery.briefing_tldr import (
    build_subject_line,
    render_html,
    render_plain_text,
    should_send,
)
from engine.daily_briefing_tldr import _NO_SIGNAL


@dataclass
class _Summary:
    text: str
    source: str = "claude"


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
