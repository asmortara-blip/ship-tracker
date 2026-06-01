"""Tests for the investor-report TLDR lede.

Covers the AIAnalysis->narration adapter + generation
(processing.investor_report_engine), the with_tldr gating, and the
HTML + markdown renderer insertions.
"""
from __future__ import annotations

import types

from engine.daily_briefing_tldr import TldrSummary
from processing import investor_report_engine as ire
from utils import investor_report_html as ihtml
from utils import markdown_export as mexport


def _fake_report(*, exec_summary="Exec summary text.", recs=None,
                 label="BULLISH", bdi=8.2, stress="MODERATE",
                 gen="2026-05-29T00:00:00+00:00"):
    ai = types.SimpleNamespace(
        executive_summary=exec_summary, top_recommendations=recs or [], tldr="",
    )
    return types.SimpleNamespace(
        ai=ai,
        sentiment=types.SimpleNamespace(overall_label=label),
        macro=types.SimpleNamespace(bdi_change_30d_pct=bdi, supply_chain_stress=stress),
        generated_at=gen,
    )


# ─── adapter + generation ────────────────────────────────────────────────────

def test_build_report_tldr_adapts_and_passes_to_generate_tldr(monkeypatch) -> None:
    captured: dict = {}

    def _fake_gen(narration, **kwargs):
        captured["narration"] = narration
        captured["cache_dir"] = kwargs.get("cache_dir")
        captured["source"] = kwargs.get("source")
        return TldrSummary(text="Distilled lede.", source="claude")

    monkeypatch.setattr("engine.daily_briefing_tldr.generate_tldr", _fake_gen)
    report = _fake_report(recs=[
        {"rank": 1, "title": "Long ZIM"},
        {"rank": 2, "title": "Short DAC"},
    ])

    out = ire._build_report_tldr(report)
    assert out == "Distilled lede."

    narr = captured["narration"]
    assert narr.body == "Exec summary text."
    assert "BULLISH" in narr.headline
    assert narr.sections[0].title == "Top Recommendations"
    assert "1. Long ZIM" in narr.sections[0].bullets
    assert "2. Short DAC" in narr.sections[0].bullets
    assert narr.date == "2026-05-29"
    # Separate cache namespace so it never collides with the briefing TLDR.
    assert captured["cache_dir"].name == "tldr_report"
    # Distinct telemetry source so cost reporting separates the two TLDRs.
    assert captured["source"] == "investor_report_tldr"


def test_build_report_tldr_handles_no_recommendations(monkeypatch) -> None:
    monkeypatch.setattr(
        "engine.daily_briefing_tldr.generate_tldr",
        lambda narration, **k: TldrSummary(text="x", source="template"),
    )
    out = ire._build_report_tldr(_fake_report(recs=[]))
    assert out == "x"


def test_synthesize_headline_includes_label_and_macro() -> None:
    h = ire._synthesize_headline(_fake_report(label="BEARISH", bdi=-3.4, stress="HIGH"))
    assert h.startswith("BEARISH")
    assert "BDI -3.4% 30d" in h
    assert "supply-chain stress HIGH" in h


# ─── with_tldr gating (full build, hermetic) ─────────────────────────────────

def test_build_investor_report_with_tldr_false_leaves_tldr_empty(monkeypatch) -> None:
    monkeypatch.setattr(ire, "_build_report_tldr", lambda report: "SHOULD-NOT-APPEAR")
    report = ire.build_investor_report(
        port_results=[], route_results=[], insights=[],
        freight_data={}, macro_data={}, stock_data={},
        news_items=[], with_tldr=False,
    )
    assert report.ai.tldr == ""


def test_build_investor_report_with_tldr_true_sets_tldr(monkeypatch) -> None:
    monkeypatch.setattr(ire, "_build_report_tldr", lambda report: "REPORT-TLDR-TEXT")
    report = ire.build_investor_report(
        port_results=[], route_results=[], insights=[],
        freight_data={}, macro_data={}, stock_data={},
        news_items=[], with_tldr=True,
    )
    assert report.ai.tldr == "REPORT-TLDR-TEXT"


# ─── HTML renderer ───────────────────────────────────────────────────────────

def test_html_lede_renders_escaped_when_present() -> None:
    report = types.SimpleNamespace(
        ai=types.SimpleNamespace(tldr="Suez lifts SSI; <b>watch</b> ZIM."),
    )
    out = ihtml._section_tldr_lede(report)
    assert "TL;DR" in out
    assert 'role="note"' in out              # matches the in-app lede a11y
    assert "Suez lifts SSI" in out
    assert "<b>watch</b>" not in out         # escaped
    assert "&lt;b&gt;" in out


def test_html_lede_empty_when_absent() -> None:
    assert ihtml._section_tldr_lede(
        types.SimpleNamespace(ai=types.SimpleNamespace(tldr=""))
    ) == ""
    assert ihtml._section_tldr_lede(types.SimpleNamespace(ai=None)) == ""


# ─── markdown renderer ───────────────────────────────────────────────────────

def test_markdown_includes_tldr_before_exec_summary_when_present() -> None:
    payload = {"ai": {"tldr": "Distilled lede.", "executive_summary": "Body."}}
    md = mexport.report_to_markdown(payload)
    assert "## TL;DR" in md
    assert "Distilled lede." in md
    assert md.index("## TL;DR") < md.index("## Executive Summary")


def test_markdown_omits_tldr_when_absent() -> None:
    payload = {"ai": {"tldr": "", "executive_summary": "Body."}}
    md = mexport.report_to_markdown(payload)
    assert "## TL;DR" not in md
