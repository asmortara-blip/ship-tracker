"""Defining-property tests for tools/backtests.py.

Pins the CLI's contract: every validator adapter must produce a
BacktestResult; the three formatters must each emit a non-empty string;
strict mode must exit 1 only when at least one validator reports
unhealthy. The CLI is the public, CI-facing entry point for the
backtest layer — these tests guard against drift in the adapter API
or formatter shape.
"""
from __future__ import annotations

import json

import pytest

from tools.backtests import (
    ADAPTERS,
    BacktestResult,
    format_json,
    format_markdown,
    format_text,
    main,
    run_all_backtests,
)


# ── 1. Adapter contract: every adapter returns a well-formed result ────────

def test_every_adapter_returns_a_backtestresult() -> None:
    """All 8 canonical adapters must be wired in and each must return
    a BacktestResult with the required string fields populated."""
    results = run_all_backtests()
    assert len(results) == len(ADAPTERS)
    assert len(results) == 8
    for r in results:
        assert isinstance(r, BacktestResult)
        assert isinstance(r.name, str) and r.name
        assert isinstance(r.headline_label, str) and r.headline_label
        assert isinstance(r.headline_value, str) and r.headline_value
        assert isinstance(r.healthy, bool)
        assert isinstance(r.summary, str) and r.summary


def test_validator_names_are_unique() -> None:
    """Two adapters must never share a name — the report ordering
    + the JSON output rely on uniqueness."""
    results = run_all_backtests()
    names = [r.name for r in results]
    assert len(names) == len(set(names))


# ── 2. Healthiness signal — synth defaults should all read healthy ────────

def test_all_validators_healthy_on_default_synth() -> None:
    """The bundled synthetic generators are tuned so each validator's
    primary calibration / monotonicity check passes at default quality.
    If a future refactor moves the noise floor without retuning the
    synth, this catches it."""
    results = run_all_backtests()
    unhealthy = [r.name for r in results if not r.healthy]
    assert not unhealthy, (
        f"{len(unhealthy)} validator(s) unhealthy on default synth: {unhealthy}"
    )


# ── 3. Formatters always return non-empty strings ─────────────────────────

def test_format_text_contains_validator_names() -> None:
    results = run_all_backtests()
    text = format_text(results)
    assert isinstance(text, str) and text
    for r in results:
        assert r.name in text


def test_format_json_is_valid_json_with_expected_keys() -> None:
    results = run_all_backtests()
    blob = format_json(results)
    payload = json.loads(blob)
    assert "validators" in payload
    assert "healthy_count" in payload
    assert "total" in payload
    assert payload["total"] == len(results)
    assert len(payload["validators"]) == len(results)
    for v in payload["validators"]:
        assert {"name", "headline_label", "headline_value",
                "healthy", "summary", "raw"} <= set(v.keys())


def test_format_markdown_renders_table_header_and_one_row_per_validator() -> None:
    results = run_all_backtests()
    md = format_markdown(results)
    assert md.startswith("| Validator | Status | Headline |")
    # One header row + one separator row + N validator rows + blank + footer
    lines = [l for l in md.splitlines() if l.startswith("|")]
    assert len(lines) == 2 + len(results)


# ── 4. CLI exit codes ─────────────────────────────────────────────────────

def test_cli_default_returns_zero_when_all_healthy(capsys) -> None:
    """Non-strict default mode exits 0 regardless of healthy flags."""
    code = main([])
    assert code == 0
    captured = capsys.readouterr().out
    assert "Ship Tracker — consolidated backtest report" in captured


def test_cli_strict_mode_returns_zero_on_all_healthy(capsys) -> None:
    code = main(["--strict"])
    assert code == 0


def test_cli_format_json_emits_valid_json(capsys) -> None:
    code = main(["--format", "json"])
    assert code == 0
    captured = capsys.readouterr().out
    payload = json.loads(captured)
    assert payload["total"] == 8


def test_cli_format_markdown_emits_table_header(capsys) -> None:
    code = main(["--format", "markdown"])
    assert code == 0
    captured = capsys.readouterr().out
    assert "| Validator | Status | Headline |" in captured


# ── 5. Strict mode flips to exit 1 when an adapter reports unhealthy ──────

def test_cli_strict_returns_one_when_an_adapter_is_unhealthy(monkeypatch, capsys) -> None:
    """Force one BacktestResult to be unhealthy and verify strict exits 1."""
    import tools.backtests as tb

    # Cache the original before monkeypatching to avoid recursion.
    _original = tb.run_all_backtests

    def _stub() -> list[BacktestResult]:
        results = _original()
        if results:
            results[0].healthy = False
        return results

    monkeypatch.setattr(tb, "run_all_backtests", _stub)
    code = tb.main(["--strict"])
    assert code == 1
