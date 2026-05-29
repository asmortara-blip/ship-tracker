"""Tests for engine.daily_briefing_tldr — one-paragraph TLDR over a narration.

All tests are hermetic:
  * The Claude SDK call is monkeypatched (engine.narration_engine._call_claude);
    never hits the real API.
  * Key resolution is shimmed to read only the explicit arg + env var, so
    st.secrets / a configured dev machine can't leak a real key in.
  * llm_telemetry.record_call is replaced with an in-memory spy — no DB.
  * The day-cache is redirected to a per-test tmp dir, so the real
    cache/tldr/ folder is never touched.

Defining properties under test:
  * generate_tldr ALWAYS returns a TldrSummary and NEVER raises.
  * None / no-content narration → the no-signal placeholder (no LLM call).
  * No key → template summary; never cached (the slot stays open for the LLM).
  * Claude success → source="claude", real tokens, telemetry recorded once
    under source "daily_briefing_tldr", and the result is day-cached.
  * Claude failure / empty response → template summary; not cached; no
    telemetry (no billable usage).
  * The day-cache is content-fingerprinted: same narration → hit (no second
    call); force-refresh (use_cache=False) or changed content → miss.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from engine import daily_briefing_tldr as tldr_mod
from engine import narration_engine as ne
from engine.daily_briefing_tldr import (
    DEFAULT_TLDR_MODEL,
    TldrSummary,
    _NO_SIGNAL,
    generate_tldr,
    render_template_tldr,
)
from engine.narration_engine import DailyNarration, NarrationSection


# ─── Builders ────────────────────────────────────────────────────────────────

def _make_narration(
    *,
    headline: str = "SSI climbs to 0.62 on Suez disruption",
    body: str = "Stress broadened across chokepoints.\n\nRates firmed WoW.",
    sections=None,
    date: str = "2026-05-29",
    source: str = "claude",
) -> DailyNarration:
    if sections is None:
        sections = [
            NarrationSection(
                title="Chokepoints",
                bullets=["Suez — moderate disruption", "Panama — slots restricted"],
            ),
        ]
    return DailyNarration(
        date=date, headline=headline, body=body, sections=sections, source=source,
    )


_FAKE_TLDR_TEXT = (
    "Suez disruption pushed the stress index to 0.62 as Trans-Pacific rates "
    "firmed week-over-week. ZIM looks best-positioned to the rate uplift."
)


def _make_fake_call(counter: dict, *, text: str = _FAKE_TLDR_TEXT,
                    tokens=(120, 45)):
    """A monkeypatch replacement for narration_engine._call_claude.

    Signature matches how generate_tldr calls it (keyword system_prompt/
    user_prompt). Increments ``counter['n']`` so tests can assert the
    cache prevents re-invocation.
    """
    def _fake_call(system_prompt, user_prompt, *, model, api_key):
        counter["n"] += 1
        counter["last_model"] = model
        counter["last_system"] = system_prompt
        return text, tokens[0], tokens[1]
    return _fake_call


# ─── Per-test isolation ──────────────────────────────────────────────────────

@dataclass
class _Harness:
    cache_dir: Path
    telemetry: list
    calls: dict


@pytest.fixture(autouse=True)
def harness(monkeypatch, tmp_path) -> _Harness:
    """Hermetic env: no ambient key, key resolution = explicit arg ▶ env only,
    telemetry spied in-memory, day-cache redirected to tmp."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Bypass st.secrets so resolution depends only on the explicit arg + env.
    monkeypatch.setattr(
        ne, "_get_anthropic_key",
        lambda explicit: explicit or os.environ.get("ANTHROPIC_API_KEY", ""),
    )
    telem: list = []
    monkeypatch.setattr(
        "engine.llm_telemetry.record_call",
        lambda **kw: telem.append(kw),
    )
    cache_dir = tmp_path / "tldr"
    monkeypatch.setattr(tldr_mod, "TLDR_CACHE_DIR", cache_dir)
    return _Harness(cache_dir=cache_dir, telemetry=telem, calls={"n": 0})


def _cache_file(harness: _Harness, date: str = "2026-05-29") -> Path:
    return harness.cache_dir / f"{date}.json"


# ─── render_template_tldr ────────────────────────────────────────────────────

def test_render_template_uses_headline_and_first_bullet() -> None:
    narration = _make_narration()
    text = render_template_tldr(narration)
    assert "SSI climbs to 0.62" in text
    assert "Suez — moderate disruption" in text


def test_render_template_headline_only() -> None:
    narration = _make_narration(sections=[])
    assert render_template_tldr(narration) == "SSI climbs to 0.62 on Suez disruption"


def test_render_template_bullet_only_when_no_headline() -> None:
    narration = _make_narration(
        headline="",
        sections=[NarrationSection(title="X", bullets=["Only bullet"])],
    )
    assert render_template_tldr(narration) == "Only bullet"


def test_render_template_empty_returns_no_signal() -> None:
    narration = _make_narration(headline="", body="", sections=[])
    assert render_template_tldr(narration) == _NO_SIGNAL


def test_render_template_skips_empty_leading_sections() -> None:
    narration = _make_narration(
        sections=[
            NarrationSection(title="Empty", bullets=[]),
            NarrationSection(title="Real", bullets=["First real bullet"]),
        ],
    )
    assert "First real bullet" in render_template_tldr(narration)


# ─── Empty / None short-circuit ──────────────────────────────────────────────

def test_none_narration_returns_placeholder(harness: _Harness) -> None:
    out = generate_tldr(None)
    assert isinstance(out, TldrSummary)
    assert out.text == _NO_SIGNAL
    assert out.source == "template"
    assert harness.telemetry == []
    assert not _cache_file(harness).exists()


def test_empty_narration_returns_placeholder(monkeypatch, harness: _Harness) -> None:
    """No-content narration short-circuits — even with a key, no Claude call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    fake = _make_fake_call(harness.calls)
    monkeypatch.setattr(ne, "_call_claude", fake)

    narration = _make_narration(headline="", body="", sections=[])
    out = generate_tldr(narration)
    assert out.text == _NO_SIGNAL
    assert out.source == "template"
    assert harness.calls["n"] == 0          # short-circuited before the call
    assert harness.telemetry == []


# ─── No-key → template (not cached) ──────────────────────────────────────────

def test_no_key_uses_template_and_does_not_cache(harness: _Harness) -> None:
    narration = _make_narration()
    out = generate_tldr(narration)
    assert out.source == "template"
    assert out.text == render_template_tldr(narration)
    assert harness.telemetry == []
    assert not _cache_file(harness).exists()   # slot stays open for the LLM


# ─── Claude success path ─────────────────────────────────────────────────────

def test_claude_success_sets_source_tokens_and_model(monkeypatch, harness) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(ne, "_call_claude", _make_fake_call(harness.calls))

    out = generate_tldr(_make_narration())
    assert out.source == "claude"
    assert out.text == _FAKE_TLDR_TEXT
    assert out.tokens_in == 120 and out.tokens_out == 45
    assert out.model == DEFAULT_TLDR_MODEL
    assert harness.calls["n"] == 1
    # The TLDR system prompt — not the narration one — was used.
    assert "TLDR" in harness.calls["last_system"]


def test_claude_success_records_telemetry_once(monkeypatch, harness) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(ne, "_call_claude", _make_fake_call(harness.calls))

    generate_tldr(_make_narration())
    assert len(harness.telemetry) == 1
    rec = harness.telemetry[0]
    assert rec == {
        "source": "daily_briefing_tldr",
        "model": DEFAULT_TLDR_MODEL,
        "tokens_in": 120,
        "tokens_out": 45,
    }


def test_claude_success_writes_day_cache(monkeypatch, harness) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(ne, "_call_claude", _make_fake_call(harness.calls))

    generate_tldr(_make_narration())
    cache_file = _cache_file(harness)
    assert cache_file.exists()
    data = json.loads(cache_file.read_text())
    assert data["text"] == _FAKE_TLDR_TEXT
    assert data["source"] == "claude"
    assert data["narration_fingerprint"]      # fingerprint persisted


# ─── Day-cache behaviour ─────────────────────────────────────────────────────

def test_second_call_is_cache_hit_no_recall(monkeypatch, harness) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(ne, "_call_claude", _make_fake_call(harness.calls))

    narration = _make_narration()
    first = generate_tldr(narration)
    second = generate_tldr(narration)
    assert harness.calls["n"] == 1            # not re-invoked
    assert len(harness.telemetry) == 1        # telemetry not double-counted
    assert second.text == first.text == _FAKE_TLDR_TEXT
    assert second.source == "claude"


def test_use_cache_false_bypasses_cache(monkeypatch, harness) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(ne, "_call_claude", _make_fake_call(harness.calls))

    narration = _make_narration()
    generate_tldr(narration)                  # populates cache (n=1)
    generate_tldr(narration, use_cache=False)  # forced fresh (n=2)
    assert harness.calls["n"] == 2


def test_changed_narration_invalidates_cache(monkeypatch, harness) -> None:
    """Same date, different content → different fingerprint → cache miss."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(ne, "_call_claude", _make_fake_call(harness.calls))

    generate_tldr(_make_narration(headline="First headline"))
    generate_tldr(_make_narration(headline="Totally different headline"))
    assert harness.calls["n"] == 2            # second was a fingerprint miss


def test_template_path_leaves_cache_open_for_llm(monkeypatch, harness) -> None:
    """A no-key (template) call must not poison the cache; a later keyed
    call still reaches Claude and caches."""
    narration = _make_narration()
    first = generate_tldr(narration)          # no key → template, not cached
    assert first.source == "template"
    assert not _cache_file(harness).exists()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(ne, "_call_claude", _make_fake_call(harness.calls))
    second = generate_tldr(narration)         # cache miss → Claude
    assert second.source == "claude"
    assert harness.calls["n"] == 1
    assert _cache_file(harness).exists()


def test_corrupt_cache_file_is_ignored(monkeypatch, harness) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(ne, "_call_claude", _make_fake_call(harness.calls))

    cache_file = _cache_file(harness)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("{not valid json")
    out = generate_tldr(_make_narration())
    assert out.source == "claude"             # regenerated past the bad file
    assert harness.calls["n"] == 1


# ─── Failure paths → template (not cached, no telemetry) ─────────────────────

def test_claude_exception_falls_back_to_template(monkeypatch, harness) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    def _boom(*a, **k):
        raise RuntimeError("network error")
    monkeypatch.setattr(ne, "_call_claude", _boom)

    narration = _make_narration()
    out = generate_tldr(narration)
    assert out.source == "template"
    assert out.text == render_template_tldr(narration)
    assert harness.telemetry == []            # no billable usage → not recorded
    assert not _cache_file(harness).exists()


def test_claude_empty_response_falls_back_to_template(monkeypatch, harness) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(ne, "_call_claude", lambda *a, **k: ("   ", 10, 5))

    out = generate_tldr(_make_narration())
    assert out.source == "template"
    assert harness.telemetry == []
    assert not _cache_file(harness).exists()


# ─── Robustness / never-raises ───────────────────────────────────────────────

def test_never_raises_on_attribute_light_object(harness: _Harness) -> None:
    """A bare object with no narration attributes → placeholder, no raise."""
    out = generate_tldr(object())
    assert isinstance(out, TldrSummary)
    assert out.text == _NO_SIGNAL


def test_generated_at_is_iso_parseable_on_both_paths(monkeypatch, harness) -> None:
    # template path
    t = generate_tldr(_make_narration())
    datetime.fromisoformat(t.generated_at)    # raises if malformed

    # claude path
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(ne, "_call_claude", _make_fake_call(harness.calls))
    c = generate_tldr(_make_narration(date="2026-05-30"))
    datetime.fromisoformat(c.generated_at)
