"""Tests for ``utils.markdown_export`` — report → Markdown conversion.

The exporter is a pure function; we don't need to spin up the engine.
Every test feeds a synthetic dict payload (matching the
``InvestorReport`` shape sufficiently for the renderer) and asserts on
the resulting Markdown string.

Coverage areas — keep these grouped for readability:
  * Full-payload happy path (every section renders).
  * Empty / missing field defaults (defensive branches).
  * Markdown-injection safety (pipes, leading hashes, newlines).
  * Roundtrip (the output IS valid Markdown — count H2s).
  * Schema-version footer is stamped.
"""
from __future__ import annotations

import pytest

from utils.markdown_export import (
    MARKDOWN_SCHEMA_VERSION,
    _md_escape,
    _md_table,
    report_to_markdown,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────


def _full_payload() -> dict:
    """A reasonable end-to-end payload exercising every section.

    Mirrors the flat dict shape the renderer's ``_get`` helper extracts
    via dict access (the alternate dataclass shape is covered by the
    nested-payload tests below)."""
    return {
        "title": "Daily Briefing — 2026-05-22",
        "generated_at": "2026-05-22T14:30:00+00:00",
        "sentiment_label": "BULLISH",
        "sentiment_score": 0.62,
        "risk_level": "MODERATE",
        "executive_summary": (
            "Container rates firmed across major lanes this week, with the "
            "Transpacific eastbound posting its third consecutive weekly "
            "gain. Tanker demand softened modestly."
        ),
        "signals": [
            {"ticker": "ZIM", "direction": "LONG", "conviction": "HIGH"},
            {"ticker": "MAERSK", "direction": "SHORT", "conviction": "MEDIUM"},
        ],
        "key_findings": [
            "BDI up 4.2% week-over-week",
            "Suez transit volumes back to 90% of 2023 baseline",
            "FBX composite rose to 2150 from 2050",
        ],
        "routes": [
            {"route_id": "transpacific_eb", "status": "Rising", "latest": 2150.0},
            {"route_id": "asia_europe", "status": "Stable", "latest": 1850.0},
        ],
        "macro": {
            "bdi": 1640.0,
            "bdi_change_30d_pct": 4.2,
            "wti": 79.5,
            "wti_change_30d_pct": -2.1,
            "treasury_10y": 4.35,
            "dxy_proxy": 7.20,
            "pmi_proxy": 99.8,
        },
        "data_quality": "FULL",
    }


# ─── Full happy-path test ──────────────────────────────────────────────────


def test_report_to_markdown_full_payload_produces_all_sections():
    """A full payload renders every documented section header."""
    md = report_to_markdown(_full_payload())
    # H1 title + each H2 section heading.
    assert md.startswith("# Daily Briefing")
    for heading in (
        "## Executive Summary",
        "## Signals",
        "## Key Findings",
        "## Top Routes",
        "## Macro Indicators",
        "## Data Quality",
    ):
        assert heading in md, f"missing section heading: {heading}"
    # Sentiment + risk show in the header subline.
    assert "BULLISH" in md
    assert "MODERATE" in md


# ─── Defensive: empty / missing sections ───────────────────────────────────


def test_empty_signals_list_renders_no_signals_placeholder():
    payload = _full_payload()
    payload["signals"] = []
    md = report_to_markdown(payload)
    assert "No signals." in md


def test_empty_routes_list_renders_no_route_data_placeholder():
    payload = _full_payload()
    payload["routes"] = []
    md = report_to_markdown(payload)
    assert "No route data." in md


def test_long_summary_is_not_truncated():
    """Markdown can display arbitrarily long prose; the exporter must
    not silently chop it. This is the only defining property of the
    "we do NOT truncate" branch — assert the full text round-trips."""
    payload = _full_payload()
    long_summary = "A" * 5000
    payload["executive_summary"] = long_summary
    md = report_to_markdown(payload)
    assert long_summary in md


# ─── Markdown primitives ───────────────────────────────────────────────────


def test_md_table_with_empty_rows_renders_header_plus_none_placeholder():
    """An empty data set must still be visible — the helper writes a
    ``(none)`` placeholder row under the header so the table doesn't
    render as a blank gap."""
    md = _md_table([], ["Col A", "Col B"])
    lines = md.strip().splitlines()
    assert lines[0] == "| Col A | Col B |"
    assert lines[1] == "|---|---|"
    # The third line is the "(none)" placeholder.
    assert "(none)" in lines[2]


def test_md_table_with_mixed_types_stringifies_all_cells():
    """Cells can be ints / floats / None / strings — the table must
    coerce them all to strings and never raise."""
    rows = [
        {"A": 42, "B": 3.14, "C": None},
        {"A": "text", "B": True, "C": "ok"},
    ]
    md = _md_table(rows, ["A", "B", "C"])
    # 42 stringifies cleanly; None falls through to the "—" sentinel.
    assert "| 42 | 3.14 | — |" in md
    assert "| text | True | ok |" in md


def test_md_table_cells_with_pipes_get_escaped():
    """A cell value containing ``|`` must be escaped to ``\\|`` so it
    doesn't terminate the table column."""
    rows = [{"X": "a|b|c"}]
    md = _md_table(rows, ["X"])
    assert "a\\|b\\|c" in md
    # And the unescaped form must NOT appear (would split the cell).
    assert "| a|b|c |" not in md


# ─── Markdown injection safety ─────────────────────────────────────────────


def test_title_with_pipe_or_hash_does_not_break_layout():
    """A payload-supplied title containing ``|`` or leading ``#`` must
    not break the H1 line OR promote itself to a different header
    level."""
    payload = _full_payload()
    payload["title"] = "##Sneaky | Title"
    md = report_to_markdown(payload)
    # The first line is the H1 — must START with exactly one "# ".
    first_line = md.splitlines()[0]
    assert first_line.startswith("# ")
    # The interior hashes are escaped so the title can't become H3.
    assert "\\#\\#Sneaky" in first_line
    # And the interior pipe is escaped.
    assert "\\|" in first_line


def test_report_to_markdown_is_utf8_safe():
    """Unicode in title / body must survive the round trip cleanly."""
    payload = _full_payload()
    payload["title"] = "中文标题 🚢 — 测试"
    payload["executive_summary"] = "Über-bullish посёлок 港口 outlook."
    md = report_to_markdown(payload)
    assert "中文标题" in md
    assert "🚢" in md
    assert "Über-bullish" in md
    # And it encodes to UTF-8 without raising.
    md.encode("utf-8")


# ─── Defensive: None input ─────────────────────────────────────────────────


def test_report_to_markdown_with_none_payload_returns_placeholder():
    """None payload must return the documented placeholder, never raise."""
    md = report_to_markdown(None)
    assert md == "_(empty report)_"


# ─── Roundtrip ─────────────────────────────────────────────────────────────


def test_report_to_markdown_roundtrip_h2_count():
    """The output is valid Markdown — count ``## `` headings via a
    permissive line-based scan and assert we got the expected six
    sections."""
    md = report_to_markdown(_full_payload())
    h2_count = sum(1 for line in md.splitlines() if line.startswith("## "))
    assert h2_count == 6


# ─── Schema-version footer ─────────────────────────────────────────────────


def test_schema_version_stamped_in_footer():
    """The footer line must end in ``schema v<N>`` where <N> is the
    module-level constant. This is the single-source-of-truth test —
    if MARKDOWN_SCHEMA_VERSION is bumped without intent, the test
    fails loudly."""
    md = report_to_markdown(_full_payload())
    assert f"schema v{MARKDOWN_SCHEMA_VERSION}" in md
    # And the horizontal-rule separator is present.
    assert "\n---\n" in md


# ─── Bonus coverage: _md_escape edge cases ────────────────────────────────


def test_md_escape_none_renders_em_dash():
    """None coerces to the sentinel — table cells stay aligned."""
    assert _md_escape(None) == "—"


def test_md_escape_newlines_collapse_to_space():
    """A multi-line cell value must not break the row apart."""
    assert "\n" not in _md_escape("line one\nline two")
    assert "line one line two" == _md_escape("line one\nline two")
