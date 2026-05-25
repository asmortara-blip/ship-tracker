"""Tests for ``utils.report_diff`` — the report-to-report diff helpers.

These tests cover the NEW diff layer (utils.report_diff). The existing
``processing.report_diff`` module has its own tests in
test_report_diff.py — the two diff modules coexist intentionally:
``processing.report_diff`` produces the briefing-tab "what changed"
widget over LIVE InvestorReport instances, ``utils.report_diff``
produces the per-entry structured diff for the report-history UI / API
/ CLI surface over PERSISTED report payloads.

The defining properties under test:

  * Identical payloads → empty entries + zero summary.
  * Added / removed / direction-flipped / confidence-shifted signals
    surface as the correct ``DiffEntry`` shape.
  * Confidence shifts at-or-below the threshold do NOT surface.
  * Route value moves > 5% surface, status flips surface, new/removed
    routes surface.
  * Metadata-only deltas (sentiment shift, risk-level change, schema
    version) surface as separate entries.
  * ``None`` / empty inputs degrade gracefully (no exception).
  * ``summary`` counts add / remove / change correctly.
  * HTML renderer escapes user-supplied content (XSS defence — signal
    titles can come from external news feeds).
  * Markdown renderer is utf-8 safe (accents / emoji / quotes survive).

The stand-in dataclasses (``_Sig``, ``_Route``, ``_Report``) mirror the
shape of ``InvestorReport`` / ``AlphaSignal`` / a freight route row
without coupling the test to the engine internals — same pattern as
``tests/test_report_diff.py`` uses for the legacy diff module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from utils.report_diff import (
    CATEGORY_METADATA,
    CATEGORY_RISK,
    CATEGORY_ROUTE,
    CATEGORY_SENTIMENT,
    CATEGORY_SIGNAL,
    CHANGE_ADDED,
    CHANGE_CHANGED,
    CHANGE_REMOVED,
    DiffEntry,
    ReportDiff,
    diff_report_metadata,
    diff_reports,
    diff_routes,
    diff_signals,
    diff_to_dict,
    render_diff_html,
    render_diff_markdown,
)


# ─── Stand-in payload classes ──────────────────────────────────────────────

@dataclass
class _Sig:
    """Stand-in for an AlphaSignal / snapshot signal row."""
    signal_name: str
    direction: str = "LONG"
    strength: float = 0.5  # mirrors AlphaSignal field

    # `confidence` alias so the duck-typed lookup tries that path too
    # — kept None on the base so tests can opt in explicitly.
    confidence: Any = None


@dataclass
class _Route:
    """Stand-in for a freight route row."""
    route_id: str
    rate: float = 1000.0
    status: str = "Stable"


@dataclass
class _Report:
    """Stand-in for an InvestorReport-shaped payload.

    Mirrors the duck-typed paths the diff helpers read:
    ``sentiment.overall_score``, ``sentiment.overall_label``,
    ``market.risk_level``, ``alpha.signals``, ``freight.routes``.
    """
    sentiment_score: float = 0.0
    sentiment_label: str = "NEUTRAL"
    risk_level: str = "MODERATE"
    signals: list = field(default_factory=list)
    routes: list = field(default_factory=list)
    schema_version: str = ""
    generated_at: str = ""

    @property
    def sentiment(self):
        return _SentimentBag(self.sentiment_score, self.sentiment_label)

    @property
    def market(self):
        return _MarketBag(self.risk_level)

    @property
    def alpha(self):
        return _AlphaBag(self.signals)

    @property
    def freight(self):
        return _FreightBag(self.routes)


@dataclass
class _SentimentBag:
    overall_score: float
    overall_label: str


@dataclass
class _MarketBag:
    risk_level: str


@dataclass
class _AlphaBag:
    signals: list


@dataclass
class _FreightBag:
    routes: list


# ─── Tests: diff_reports with identical payloads ──────────────────────────

def test_diff_reports_identical_payloads_yield_empty_entries() -> None:
    """Two structurally identical reports must produce zero entries
    AND a zeroed summary. This is the "no news" case — operators
    should see "no changes" rather than a noise of trivial deltas.
    """
    r = _Report(
        sentiment_score=0.3, sentiment_label="MIXED",
        risk_level="MODERATE",
        signals=[_Sig(signal_name="S1", direction="LONG", strength=0.6)],
        routes=[_Route(route_id="R1", rate=1500.0, status="Stable")],
    )
    diff = diff_reports(r, r, report_a_id="a", report_b_id="b")
    assert diff.entries == []
    assert diff.summary == {"added": 0, "removed": 0, "changed": 0}
    assert diff.report_a_id == "a"
    assert diff.report_b_id == "b"


# ─── Tests: signal diff ────────────────────────────────────────────────────

def test_diff_signals_new_signal_becomes_added_entry() -> None:
    """A signal in B but not A → one 'added' entry under category 'signal'."""
    a = [_Sig(signal_name="OLD", direction="LONG", strength=0.6)]
    b = [
        _Sig(signal_name="OLD", direction="LONG", strength=0.6),
        _Sig(signal_name="NEW", direction="SHORT", strength=0.8),
    ]
    entries = diff_signals(a, b)
    added = [e for e in entries if e.change_type == CHANGE_ADDED]
    assert len(added) == 1
    assert added[0].category == CATEGORY_SIGNAL
    assert added[0].key == "NEW"
    assert added[0].before is None
    assert added[0].after == {"direction": "SHORT", "confidence": 0.8}
    assert "NEW" in added[0].description


def test_diff_signals_removed_signal_becomes_removed_entry() -> None:
    """A signal in A but not B → one 'removed' entry."""
    a = [
        _Sig(signal_name="GONE", direction="LONG", strength=0.7),
        _Sig(signal_name="KEEP", direction="SHORT", strength=0.5),
    ]
    b = [_Sig(signal_name="KEEP", direction="SHORT", strength=0.5)]
    entries = diff_signals(a, b)
    removed = [e for e in entries if e.change_type == CHANGE_REMOVED]
    assert len(removed) == 1
    assert removed[0].category == CATEGORY_SIGNAL
    assert removed[0].key == "GONE"
    assert removed[0].before == {"direction": "LONG", "confidence": 0.7}
    assert removed[0].after is None


def test_diff_signals_direction_flipped_becomes_changed_with_direction_in_description() -> None:
    """Same signal name, different direction → 'changed' with the flip
    spelled out in the description (so operators don't have to re-read
    the before/after dicts to see what happened)."""
    a = [_Sig(signal_name="ZIM-mom", direction="LONG", strength=0.7)]
    b = [_Sig(signal_name="ZIM-mom", direction="SHORT", strength=0.7)]
    entries = diff_signals(a, b)
    changed = [e for e in entries if e.change_type == CHANGE_CHANGED]
    assert len(changed) == 1
    desc = changed[0].description
    assert "LONG" in desc
    assert "SHORT" in desc
    # The before/after dicts must carry the directions too.
    assert changed[0].before["direction"] == "LONG"
    assert changed[0].after["direction"] == "SHORT"


def test_diff_signals_confidence_shift_above_threshold_becomes_changed() -> None:
    """Same name + same direction but confidence moved by > 0.1 → 'changed'."""
    a = [_Sig(signal_name="MATX-mr", direction="NEUTRAL", strength=0.30)]
    b = [_Sig(signal_name="MATX-mr", direction="NEUTRAL", strength=0.55)]
    entries = diff_signals(a, b)
    changed = [e for e in entries if e.change_type == CHANGE_CHANGED]
    assert len(changed) == 1
    assert changed[0].key == "MATX-mr"
    # The description must include both confidence numbers so a
    # downstream reader (chat / email) doesn't need the JSON payload.
    assert "0.30" in changed[0].description
    assert "0.55" in changed[0].description


def test_diff_signals_confidence_shift_at_or_below_threshold_does_not_surface() -> None:
    """Exactly-threshold (0.10) or below-threshold confidence shifts
    must NOT produce an entry — operators would drown in noise from
    every retrained signal otherwise."""
    # At threshold (0.10) — strict > so this is below the firing line.
    a = [_Sig(signal_name="DAC-tech", direction="LONG", strength=0.50)]
    b = [_Sig(signal_name="DAC-tech", direction="LONG", strength=0.60)]
    entries = diff_signals(a, b)
    assert entries == []

    # Well below (0.05) — also no entry.
    b2 = [_Sig(signal_name="DAC-tech", direction="LONG", strength=0.55)]
    entries2 = diff_signals(a, b2)
    assert entries2 == []


# ─── Tests: route diff ────────────────────────────────────────────────────

def test_diff_routes_new_route_becomes_added_entry() -> None:
    """A route in B but not A → 'added' under category 'route'."""
    a = [_Route(route_id="R1", rate=2400.0)]
    b = [
        _Route(route_id="R1", rate=2400.0),
        _Route(route_id="R2_NEW", rate=1800.0, status="Accelerating"),
    ]
    entries = diff_routes(a, b)
    added = [e for e in entries if e.change_type == CHANGE_ADDED]
    assert len(added) == 1
    assert added[0].category == CATEGORY_ROUTE
    assert added[0].key == "R2_NEW"


def test_diff_routes_value_change_above_5pct_becomes_changed() -> None:
    """A route whose latest value moves > 5% → 'changed' with the
    percent delta in the description."""
    a = [_Route(route_id="FBX01", rate=2400.0, status="Stable")]
    b = [_Route(route_id="FBX01", rate=2580.0, status="Stable")]  # +7.5%
    entries = diff_routes(a, b)
    changed = [e for e in entries if e.change_type == CHANGE_CHANGED]
    assert len(changed) == 1
    assert changed[0].key == "FBX01"
    # 7.5% rounded to one decimal in the description.
    assert "7.5%" in changed[0].description


def test_diff_routes_value_change_at_or_below_5pct_does_not_surface() -> None:
    """Boundary check — 5% is the strict-greater threshold so a
    move EXACTLY at 5% must not produce a row."""
    a = [_Route(route_id="FBX02", rate=1000.0)]
    b = [_Route(route_id="FBX02", rate=1050.0)]  # exactly +5%
    entries = diff_routes(a, b)
    # No numeric entry (status unchanged → no status entry either).
    assert entries == []


def test_diff_routes_status_changed_becomes_changed_entry() -> None:
    """Same route, same value, but status flipped → 'changed' entry."""
    a = [_Route(route_id="FBX03", rate=2000.0, status="Stable")]
    b = [_Route(route_id="FBX03", rate=2000.0, status="Accelerating")]
    entries = diff_routes(a, b)
    changed = [e for e in entries if e.change_type == CHANGE_CHANGED]
    assert len(changed) == 1
    assert "Stable" in changed[0].description
    assert "Accelerating" in changed[0].description


# ─── Tests: metadata diff ──────────────────────────────────────────────────

def test_diff_metadata_sentiment_score_shift_surfaces_as_changed() -> None:
    """Sentiment-score delta > 0.05 → one 'changed' entry under category
    'sentiment'."""
    a = _Report(sentiment_score=0.12, sentiment_label="MIXED")
    b = _Report(sentiment_score=0.41, sentiment_label="MIXED")
    entries = diff_report_metadata(a, b)
    score_entries = [e for e in entries if e.key == "sentiment_score"]
    assert len(score_entries) == 1
    assert score_entries[0].category == CATEGORY_SENTIMENT
    assert score_entries[0].change_type == CHANGE_CHANGED


def test_diff_metadata_risk_level_change_surfaces_as_changed() -> None:
    """Risk-level flip → one 'changed' entry under category 'risk'."""
    a = _Report(risk_level="MODERATE")
    b = _Report(risk_level="HIGH")
    entries = diff_report_metadata(a, b)
    risk_entries = [e for e in entries if e.key == "risk_level"]
    assert len(risk_entries) == 1
    assert risk_entries[0].category == CATEGORY_RISK
    assert risk_entries[0].change_type == CHANGE_CHANGED
    assert "MODERATE" in risk_entries[0].description
    assert "HIGH" in risk_entries[0].description


def test_diff_metadata_schema_version_difference_surfaces_with_warning() -> None:
    """Differing schema_version stamps must produce an explicit
    metadata entry so a consumer knows subsequent rows may not be
    directly comparable. Pinned: a future feature change MUST keep
    this contract."""
    a = _Report(schema_version="v1")
    b = _Report(schema_version="v2")
    entries = diff_report_metadata(a, b)
    schema_entries = [e for e in entries if e.key == "schema_version"]
    assert len(schema_entries) == 1
    assert schema_entries[0].category == CATEGORY_METADATA
    # Description should warn that subsequent entries may not be
    # comparable — the whole point of this row.
    assert "not be directly comparable" in schema_entries[0].description.lower() or \
        "subsequent" in schema_entries[0].description.lower()


# ─── Tests: edge cases (None / empty inputs) ──────────────────────────────

def test_diff_reports_empty_a_and_populated_b_yields_all_added() -> None:
    """A → empty report, B → populated: every B signal/route surfaces
    as 'added'. No 'removed' or 'changed' rows from these categories."""
    a = _Report()
    b = _Report(
        signals=[
            _Sig(signal_name="S1", direction="LONG", strength=0.6),
            _Sig(signal_name="S2", direction="SHORT", strength=0.7),
        ],
        routes=[_Route(route_id="R1", rate=2000.0)],
    )
    diff = diff_reports(a, b, report_a_id="a", report_b_id="b")
    sig_entries = [e for e in diff.entries if e.category == CATEGORY_SIGNAL]
    route_entries = [e for e in diff.entries if e.category == CATEGORY_ROUTE]
    assert len(sig_entries) == 2
    assert len(route_entries) == 1
    assert all(e.change_type == CHANGE_ADDED for e in sig_entries)
    assert all(e.change_type == CHANGE_ADDED for e in route_entries)


def test_diff_reports_populated_a_and_empty_b_yields_all_removed() -> None:
    """Mirror of the above — A populated, B empty → only 'removed' rows
    in the signal + route categories."""
    a = _Report(
        signals=[_Sig(signal_name="S1", direction="LONG", strength=0.7)],
        routes=[_Route(route_id="R1", rate=2000.0)],
    )
    b = _Report()
    diff = diff_reports(a, b, report_a_id="a", report_b_id="b")
    sig_entries = [e for e in diff.entries if e.category == CATEGORY_SIGNAL]
    route_entries = [e for e in diff.entries if e.category == CATEGORY_ROUTE]
    assert len(sig_entries) == 1
    assert len(route_entries) == 1
    assert all(e.change_type == CHANGE_REMOVED for e in sig_entries)
    assert all(e.change_type == CHANGE_REMOVED for e in route_entries)


def test_diff_reports_handles_none_inputs_without_raising() -> None:
    """Defensive contract — both None, one None, malformed objects all
    return a sane ReportDiff. The whole diff pipeline must never crash
    the briefing tab."""
    # Both None
    d1 = diff_reports(None, None, report_a_id="x", report_b_id="y")
    assert isinstance(d1, ReportDiff)
    assert d1.entries == []
    assert d1.summary == {"added": 0, "removed": 0, "changed": 0}

    # A None, B populated — graceful empty-A semantics (signals in B
    # surface as 'added').
    d2 = diff_reports(
        None,
        _Report(signals=[_Sig(signal_name="X", direction="LONG", strength=0.6)]),
        report_a_id="", report_b_id="",
    )
    assert isinstance(d2, ReportDiff)
    # No raise; we don't pin specific entries here because the
    # implementation only short-circuits the both-None case.

    # Malformed payload — random object with no attributes the diff
    # cares about.
    class _Junk:
        pass
    d3 = diff_reports(_Junk(), _Junk(), report_a_id="a", report_b_id="b")
    assert isinstance(d3, ReportDiff)
    assert d3.entries == []


# ─── Tests: summary counts ────────────────────────────────────────────────

def test_report_diff_summary_counts_add_remove_change_correctly() -> None:
    """Hand-build a known-shape diff and verify the summary tallies
    match the entry counts by change_type. Pinned: the summary is the
    headline operators see first; it MUST stay in sync with the
    entries list."""
    a = _Report(
        sentiment_score=0.1, risk_level="LOW",
        signals=[_Sig(signal_name="S1", direction="LONG", strength=0.6)],
        routes=[_Route(route_id="R1", rate=2000.0, status="Stable")],
    )
    b = _Report(
        sentiment_score=0.5, risk_level="HIGH",
        signals=[
            _Sig(signal_name="S2", direction="SHORT", strength=0.7),  # added
            # S1 removed
        ],
        routes=[
            _Route(route_id="R1", rate=2400.0, status="Accelerating"),  # +20% AND status flip
            _Route(route_id="R2", rate=1500.0),  # added
        ],
    )
    diff = diff_reports(a, b, report_a_id="a", report_b_id="b")

    # Manual tally
    add_count = sum(1 for e in diff.entries if e.change_type == CHANGE_ADDED)
    rem_count = sum(1 for e in diff.entries if e.change_type == CHANGE_REMOVED)
    chg_count = sum(1 for e in diff.entries if e.change_type == CHANGE_CHANGED)

    assert diff.summary["added"] == add_count
    assert diff.summary["removed"] == rem_count
    assert diff.summary["changed"] == chg_count
    # And the headline categories were exercised — sanity check that
    # the synthesised diff really stressed both add/remove/change paths.
    assert diff.summary["added"] >= 2     # S2 + R2
    assert diff.summary["removed"] >= 1   # S1
    assert diff.summary["changed"] >= 3   # sentiment_score, risk_level, R1 value, R1 status


# ─── Tests: renderers ────────────────────────────────────────────────────

def test_render_diff_markdown_produces_non_empty_output() -> None:
    """Markdown renderer must produce something for every input shape
    — empty diffs get a "no changes" line so a download button always
    has content; populated diffs get category sections."""
    # Empty
    empty = ReportDiff(report_a_id="a", report_b_id="b")
    empty.summary = {"added": 0, "removed": 0, "changed": 0}
    md_empty = render_diff_markdown(empty)
    assert md_empty.strip()
    assert "a" in md_empty and "b" in md_empty
    assert "no meaningful" in md_empty.lower()

    # Populated
    diff = diff_reports(
        _Report(),
        _Report(signals=[_Sig(signal_name="ALPHA", direction="LONG", strength=0.8)]),
        report_a_id="r1", report_b_id="r2",
    )
    md = render_diff_markdown(diff)
    assert "ALPHA" in md
    assert "ADDED" in md
    # Section header
    assert "Signals" in md


def test_render_diff_html_escapes_user_supplied_content() -> None:
    """SECURITY: signal titles can come from external news feeds. A
    title containing `<script>` MUST render as inert text — the
    payload must NOT execute when the snippet is rendered in the UI."""
    nasty = "<script>alert('xss')</script>"
    diff = diff_reports(
        _Report(),
        _Report(signals=[_Sig(signal_name=nasty, direction="LONG", strength=0.7)]),
        report_a_id="a", report_b_id="b",
    )
    html_out = render_diff_html(diff)
    # The raw <script> opener must NOT appear — it should be escaped
    # to &lt;script&gt; on the way out.
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out
    # And no XSS execution payload should survive on its own:
    assert "alert('xss')" not in html_out or "alert(&#x27;xss&#x27;)" in html_out


def test_render_diff_markdown_is_utf8_safe() -> None:
    """utf-8 strings (accents, emoji, smart quotes) must pass through
    the markdown rendering without raising and without garbling."""
    diff = diff_reports(
        _Report(),
        _Report(signals=[
            _Sig(signal_name="ZIM — momentum ↑ café 🚢", direction="LONG", strength=0.7),
        ]),
        report_a_id="α", report_b_id="β",
    )
    md = render_diff_markdown(diff)
    # Round-trip via utf-8 encode/decode to confirm no surprise bytes.
    md.encode("utf-8").decode("utf-8")
    assert "🚢" in md
    assert "café" in md
    assert "α" in md and "β" in md


# ─── Tests: diff_to_dict serialisation ────────────────────────────────────

def test_diff_to_dict_round_trips_through_json() -> None:
    """The dict the API + CLI emit MUST be JSON-serialisable end to end
    — this is the contract external consumers rely on."""
    import json

    diff = diff_reports(
        _Report(signals=[_Sig(signal_name="S1", direction="LONG", strength=0.6)]),
        _Report(
            sentiment_score=0.4, risk_level="HIGH",
            signals=[
                _Sig(signal_name="S2", direction="SHORT", strength=0.8),
            ],
            routes=[_Route(route_id="R1", rate=2500.0)],
        ),
        report_a_id="ra", report_b_id="rb",
    )
    payload = diff_to_dict(diff)
    raw = json.dumps(payload)
    parsed = json.loads(raw)
    assert parsed["report_a_id"] == "ra"
    assert parsed["report_b_id"] == "rb"
    assert "summary" in parsed and "entries" in parsed
    assert isinstance(parsed["entries"], list)


# ─── Tests: load_report_payload (per-user scoping + missing snapshot) ─────

@pytest.fixture(autouse=True)
def _isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite isolation so the loader tests don't share state."""
    from state import db as state_db
    from utils import report_history as rh

    tmp_reports = tmp_path / "reports"
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_reports)
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


def _seed_report_row(
    *,
    report_id: str,
    user_id: str = "",
    sentiment_score: float = 0.3,
    risk_level: str = "MODERATE",
    sentiment_label: str = "MIXED",
    generated_at: str = "2026-05-22T12:00:00+00:00",
) -> None:
    """Drop a tiny HTML file + report_history row scoped to user_id."""
    from pathlib import Path

    from state.db import get_connection
    from utils import report_history as rh

    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    file_path = Path(rh.REPORT_DIR) / f"r_{report_id[:8]}.html"
    file_path.write_text(f"<html><body>{report_id}</body></html>", encoding="utf-8")

    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO report_history
              (report_id, generated_at, report_date, sentiment_label,
               sentiment_score, risk_level, signal_count, data_quality,
               file_path, file_size_kb, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id, generated_at, "May 22, 2026", sentiment_label,
                sentiment_score, risk_level, 0, "FULL",
                str(file_path.resolve()), 0.5, user_id,
            ),
        )


def test_load_report_payload_unknown_id_returns_none() -> None:
    """Unknown id in scope → None. No exception."""
    from utils.report_diff import load_report_payload
    assert load_report_payload("does-not-exist", user_id="alice") is None


def test_load_report_payload_cross_user_returns_none() -> None:
    """Bob's report id must collapse to the same None as an unknown
    id when alice asks — per-user scoping is enforced and there's no
    distinguishable permission-denied leak."""
    from utils.report_diff import load_report_payload

    _seed_report_row(report_id="bob-report-1", user_id="bob")
    # Alice can't load it.
    assert load_report_payload("bob-report-1", user_id="alice") is None
    # Bob can.
    payload = load_report_payload("bob-report-1", user_id="bob")
    assert payload is not None
    assert payload.report_id == "bob-report-1"


def test_load_report_payload_metadata_only_still_returns_payload() -> None:
    """When no snapshot is within the match window, the loader still
    returns a payload built from ReportMeta alone — sentiment / risk
    are present, signals + routes are empty. The diff can still
    surface metadata-level changes."""
    from utils.report_diff import diff_reports, load_report_payload

    _seed_report_row(
        report_id="rep-a", user_id="alice",
        sentiment_score=0.10, risk_level="LOW",
    )
    _seed_report_row(
        report_id="rep-b", user_id="alice",
        sentiment_score=0.40, risk_level="HIGH",
        generated_at="2026-05-23T12:00:00+00:00",
    )
    pa = load_report_payload("rep-a", user_id="alice")
    pb = load_report_payload("rep-b", user_id="alice")
    assert pa is not None and pb is not None
    assert pa.signals == [] and pb.signals == []  # no snapshot → empty
    assert pa.routes == [] and pb.routes == []

    diff = diff_reports(pa, pb, report_a_id="rep-a", report_b_id="rep-b")
    # Metadata-level entries should still surface: sentiment + risk.
    assert any(e.key == "sentiment_score" for e in diff.entries)
    assert any(e.key == "risk_level" for e in diff.entries)
