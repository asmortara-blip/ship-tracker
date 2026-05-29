"""Tests for tools.briefing_tldr_cli — print the day's TLDR to stdout.

Hermetic: the cached-narration read and generate_tldr are stubbed on the
CLI module, so no cache file or Claude call is needed.
"""
from __future__ import annotations

import argparse
import json

import pytest

from engine.daily_briefing_tldr import TldrSummary
from tools import briefing_tldr_cli as cli


class _FakeNarration:
    date = "2026-05-29"
    headline = "H"
    body = "B"
    sections: list = []


_SUMMARY = TldrSummary(
    text="Suez lifts SSI to 0.62.",
    source="claude",
    model="claude-haiku-4-5-20251001",
    tokens_in=120,
    tokens_out=45,
    generated_at="2026-05-29T00:00:00+00:00",
)


@pytest.fixture
def with_narration(monkeypatch):
    monkeypatch.setattr(cli, "_read_narration_cache", lambda path: _FakeNarration())
    monkeypatch.setattr(cli, "generate_tldr", lambda narration, **k: _SUMMARY)


def test_build_parser_is_introspectable() -> None:
    """cli_index live-introspects _build_parser — keep it a no-arg factory."""
    assert isinstance(cli._build_parser(), argparse.ArgumentParser)


def test_text_format_prints_raw_paragraph(with_narration, capsys) -> None:
    rc = cli.main(["--date", "2026-05-29"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "Suez lifts SSI to 0.62."


def test_json_format_carries_fields(with_narration, capsys) -> None:
    rc = cli.main(["--date", "2026-05-29", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["text"] == "Suez lifts SSI to 0.62."
    assert payload["source"] == "claude"
    assert payload["date"] == "2026-05-29"
    assert payload["tokens_in"] == 120


def test_subject_format(with_narration, capsys) -> None:
    rc = cli.main(["--date", "2026-05-29", "--format", "subject"])
    assert rc == 0
    assert "TLDR" in capsys.readouterr().out


def test_html_format(with_narration, capsys) -> None:
    rc = cli.main(["--date", "2026-05-29", "--format", "html"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("<!DOCTYPE html>")
    assert "Suez lifts SSI to 0.62." in out


def test_missing_narration_returns_1(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_read_narration_cache", lambda path: None)
    rc = cli.main(["--date", "2026-05-29"])
    assert rc == 1
    assert "no briefing narrated" in capsys.readouterr().err


def test_bad_date_returns_1(capsys) -> None:
    rc = cli.main(["--date", "not-a-date"])
    assert rc == 1
    assert "expected YYYY-MM-DD" in capsys.readouterr().err


def test_refresh_bypasses_cache(monkeypatch, capsys) -> None:
    captured: dict = {}
    monkeypatch.setattr(cli, "_read_narration_cache", lambda path: _FakeNarration())

    def _fake_tldr(narration, **k):
        captured["use_cache"] = k.get("use_cache")
        return _SUMMARY

    monkeypatch.setattr(cli, "generate_tldr", _fake_tldr)
    cli.main(["--date", "2026-05-29", "--refresh"])
    assert captured["use_cache"] is False
