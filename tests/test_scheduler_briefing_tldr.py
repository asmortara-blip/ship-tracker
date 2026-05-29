"""Defining-property tests for worker.scheduler.run_briefing_tldr_job.

Hermetic: build_narration_context / generate_daily_narration / generate_tldr
are stubbed (no real compute, no Claude), and the artifact dir is redirected
to tmp_path. Verifies the job persists ready-to-send artifacts for a material
TLDR, skips on a no-signal day, and NEVER raises.
"""
from __future__ import annotations

import pytest

import worker.scheduler as sched
from engine.daily_briefing_tldr import TldrSummary, _NO_SIGNAL


class _FakeNarration:
    date = "2026-05-29"
    headline = "SSI elevated to 0.62"
    body = "Body."
    sections: list = []


@pytest.fixture(autouse=True)
def patched(monkeypatch, tmp_path):
    monkeypatch.setattr(sched, "_BRIEFING_TLDR_DIR", tmp_path / "briefing_tldr")
    monkeypatch.setattr(
        "engine.narration_engine.build_narration_context",
        lambda *a, **k: _FakeNarration(),
    )
    monkeypatch.setattr(
        "engine.narration_engine.generate_daily_narration",
        lambda ctx, **k: _FakeNarration(),
    )
    yield tmp_path


def _set_tldr(monkeypatch, summary) -> None:
    monkeypatch.setattr(
        "engine.daily_briefing_tldr.generate_tldr",
        lambda narration, **k: summary,
    )


def test_persists_artifacts_for_material_tldr(monkeypatch, patched) -> None:
    _set_tldr(
        monkeypatch,
        TldrSummary(text="Suez lifts SSI to 0.62; ZIM exposed.", source="claude"),
    )
    out = sched.run_briefing_tldr_job({"port_results": []})

    assert out["ok"] is True
    assert out["source"] == "claude"
    assert out["persisted"] is True

    d = patched / "briefing_tldr" / "2026-05-29"
    assert (d / "tldr.html").exists()
    assert (d / "tldr.txt").exists()
    assert (d / "tldr.subject.txt").exists()
    assert "Suez lifts SSI to 0.62; ZIM exposed." in (d / "tldr.txt").read_text()
    assert "2026-05-29" in (d / "tldr.subject.txt").read_text()


def test_skips_persistence_for_no_signal(monkeypatch, patched) -> None:
    _set_tldr(monkeypatch, TldrSummary(text=_NO_SIGNAL, source="template"))
    out = sched.run_briefing_tldr_job({})

    assert out["ok"] is True
    assert out["persisted"] is False
    assert not (patched / "briefing_tldr" / "2026-05-29").exists()


def test_never_raises_when_generation_fails(monkeypatch, patched) -> None:
    def _boom(*a, **k):
        raise RuntimeError("claude exploded")
    monkeypatch.setattr("engine.daily_briefing_tldr.generate_tldr", _boom)

    out = sched.run_briefing_tldr_job({})   # must not raise
    assert out["ok"] is False
    assert out["persisted"] is False
    assert out["paths"] == {}
