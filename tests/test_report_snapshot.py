"""Tests for processing.report_snapshot — durable briefing-tab diff state.

Covers the full lifecycle:

  * ReportSnapshot dataclass shape and JSON round-trip
  * extract_snapshot pulls the right fields from a stand-in
    InvestorReport (we don't depend on the real engine class)
  * save_snapshot persists into investor_report_snapshots
  * load_latest_snapshots returns the newest N, sorted desc
  * load_latest_snapshots honours user_id scoping
  * prune_old_snapshots keeps the newest keep_n, deletes the rest
  * Views (sentiment / alpha / market / freight) satisfy the
    compute_report_diff duck-typing contract — round-tripping two
    snapshots through compute_report_diff produces a non-empty diff
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest


# ─── DB isolation fixture (matches the pattern used elsewhere) ─────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite DB so no test touches the real cache file."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Stand-in InvestorReport shape ────────────────────────────────────────
#
# Deliberately mirrors the real InvestorReport dataclass attribute names
# but lives entirely in test-land so the snapshot module stays decoupled
# from engine internals.

@dataclass
class _Sentiment:
    overall_score: float = 0.0
    overall_label: str = "NEUTRAL"


@dataclass
class _Signal:
    title: str = ""
    score: float = 0.0


@dataclass
class _AlphaSignalRaw:
    """Mirrors the real engine.alpha_engine.AlphaSignal shape — uses
    ``signal_name`` / ``strength`` instead of ``title`` / ``score`` so the
    extractor's fallback paths are exercised."""
    signal_name: str = ""
    strength: float = 0.0


@dataclass
class _Alpha:
    signals: list = field(default_factory=list)


@dataclass
class _Market:
    risk_level: str = "LOW"


@dataclass
class _Freight:
    routes: list = field(default_factory=list)


@dataclass
class _Report:
    generated_at: str = ""
    report_date: str = ""
    sentiment: _Sentiment = field(default_factory=_Sentiment)
    alpha: _Alpha = field(default_factory=_Alpha)
    market: _Market = field(default_factory=_Market)
    freight: _Freight = field(default_factory=_Freight)


def _mk_report(
    *,
    generated_at: str = "2026-05-22T12:00:00+00:00",
    report_date: str = "May 22, 2026",
    overall_score: float = 0.3,
    overall_label: str = "BULLISH",
    risk_level: str = "MODERATE",
    signals: list[tuple[str, float]] | None = None,
    routes: list[tuple[str, float]] | None = None,
) -> _Report:
    """Build a stand-in InvestorReport for the extractor to chew on."""
    return _Report(
        generated_at=generated_at,
        report_date=report_date,
        sentiment=_Sentiment(overall_score=overall_score, overall_label=overall_label),
        alpha=_Alpha(signals=[_Signal(title=t, score=s) for t, s in (signals or [])]),
        market=_Market(risk_level=risk_level),
        freight=_Freight(routes=[
            {"route_id": rid, "rate": rate} for rid, rate in (routes or [])
        ]),
    )


# ═══════════════════════════════════════════════════════════════════════════
# ReportSnapshot dataclass shape + JSON round-trip
# ═══════════════════════════════════════════════════════════════════════════

def test_report_snapshot_dataclass_fields() -> None:
    """The dataclass exposes every field the task spec calls out."""
    from processing.report_snapshot import ReportSnapshot

    snap = ReportSnapshot(
        snapshot_id="s1",
        generated_at="2026-05-22T00:00:00+00:00",
        report_date="May 22, 2026",
        sentiment_overall_score=0.42,
        sentiment_label="BULLISH",
        risk_level="LOW",
        signals=[{"name": "Sig A", "score": 0.7}],
        routes=[{"route_id": "FBX01", "rate_usd_per_feu": 2890.0}],
    )
    assert snap.snapshot_id == "s1"
    assert snap.sentiment_overall_score == pytest.approx(0.42)
    assert snap.sentiment_label == "BULLISH"
    assert snap.risk_level == "LOW"
    assert len(snap.signals) == 1
    assert len(snap.routes) == 1


def test_report_snapshot_to_dict_returns_plain_dict() -> None:
    """to_dict is JSON-encodable — no pandas, no datetime objects."""
    import json

    from processing.report_snapshot import ReportSnapshot

    snap = ReportSnapshot(
        snapshot_id="s1",
        generated_at="2026-05-22T00:00:00+00:00",
        report_date="",
        sentiment_overall_score=0.0,
        sentiment_label="",
        risk_level="",
        signals=[{"name": "A", "score": 0.1}],
        routes=[{"route_id": "R", "rate_usd_per_feu": 1000.0}],
    )
    d = snap.to_dict()
    # Round-trip through json — must not raise.
    serialised = json.dumps(d)
    reloaded = json.loads(serialised)
    assert reloaded["snapshot_id"] == "s1"
    assert reloaded["signals"][0]["name"] == "A"


def test_report_snapshot_from_dict_round_trip() -> None:
    """to_dict → from_dict is a lossless round-trip."""
    from processing.report_snapshot import ReportSnapshot

    original = ReportSnapshot(
        snapshot_id="s-rt",
        generated_at="2026-05-22T00:00:00+00:00",
        report_date="May 22, 2026",
        sentiment_overall_score=0.5,
        sentiment_label="BULLISH",
        risk_level="HIGH",
        signals=[{"name": "X", "score": 0.9}, {"name": "Y", "score": -0.2}],
        routes=[{"route_id": "R1", "rate_usd_per_feu": 2000.0}],
    )
    payload = original.to_dict()
    restored = ReportSnapshot.from_dict(payload)
    assert restored.snapshot_id == original.snapshot_id
    assert restored.sentiment_overall_score == pytest.approx(0.5)
    assert restored.signals == original.signals
    assert restored.routes == original.routes


def test_report_snapshot_from_dict_handles_missing_keys() -> None:
    """A partial / older payload restores cleanly with sensible defaults."""
    from processing.report_snapshot import ReportSnapshot

    snap = ReportSnapshot.from_dict({"snapshot_id": "minimal"})
    assert snap.snapshot_id == "minimal"
    assert snap.sentiment_overall_score == 0.0
    assert snap.signals == []
    assert snap.routes == []


def test_report_snapshot_from_dict_skips_non_dict_signals() -> None:
    """Malformed signal entries are filtered out, not raised on."""
    from processing.report_snapshot import ReportSnapshot

    snap = ReportSnapshot.from_dict({
        "snapshot_id": "filter",
        "signals": [{"name": "good", "score": 0.5}, "not a dict", None],
        "routes": [{"route_id": "R", "rate_usd_per_feu": 100.0}, 12345],
    })
    assert len(snap.signals) == 1
    assert snap.signals[0]["name"] == "good"
    assert len(snap.routes) == 1


# ═══════════════════════════════════════════════════════════════════════════
# extract_snapshot
# ═══════════════════════════════════════════════════════════════════════════

def test_extract_snapshot_pulls_relevant_fields() -> None:
    """Every diff-relevant attribute lands in the snapshot."""
    from processing.report_snapshot import extract_snapshot

    rep = _mk_report(
        overall_score=0.6,
        overall_label="BULLISH",
        risk_level="HIGH",
        signals=[("Asia-EU Surge", 0.8), ("Tanker Squeeze", -0.4)],
        routes=[("FBX01", 2890.0), ("FBX03", 2180.0)],
    )
    snap = extract_snapshot(rep)

    assert snap.sentiment_overall_score == pytest.approx(0.6)
    assert snap.sentiment_label == "BULLISH"
    assert snap.risk_level == "HIGH"
    assert snap.report_date == "May 22, 2026"
    # Signals carry over both names and scores
    names = [s["name"] for s in snap.signals]
    assert names == ["Asia-EU Surge", "Tanker Squeeze"]
    scores = [s["score"] for s in snap.signals]
    assert scores == [pytest.approx(0.8), pytest.approx(-0.4)]
    # Routes map route_id + rate_usd_per_feu (engine uses ``rate``).
    rids = [r["route_id"] for r in snap.routes]
    assert rids == ["FBX01", "FBX03"]
    rates = [r["rate_usd_per_feu"] for r in snap.routes]
    assert rates == [pytest.approx(2890.0), pytest.approx(2180.0)]


def test_extract_snapshot_falls_back_to_signal_name_and_strength() -> None:
    """Real AlphaSignal uses ``signal_name`` and ``strength``; the
    extractor must fall back to those when ``title``/``score`` are
    absent."""
    from processing.report_snapshot import extract_snapshot

    rep = _mk_report()
    # Replace the title/score signals with engine-shaped ones.
    rep.alpha.signals = [
        _AlphaSignalRaw(signal_name="ZIM long", strength=0.72),
        _AlphaSignalRaw(signal_name="MATX short", strength=0.31),
    ]
    snap = extract_snapshot(rep)

    names = [s["name"] for s in snap.signals]
    assert "ZIM long" in names
    assert "MATX short" in names
    scores = {s["name"]: s["score"] for s in snap.signals}
    assert scores["ZIM long"] == pytest.approx(0.72)
    assert scores["MATX short"] == pytest.approx(0.31)


def test_extract_snapshot_defensive_on_malformed_report() -> None:
    """A garbage input yields a zeroed-but-valid snapshot, not an exception."""
    from processing.report_snapshot import ReportSnapshot, extract_snapshot

    # An object with NONE of the expected attributes — every getattr
    # must take the safe-fallback path.
    class _Bogus:
        pass

    snap = extract_snapshot(_Bogus())
    assert isinstance(snap, ReportSnapshot)
    assert snap.sentiment_overall_score == 0.0
    assert snap.signals == []
    assert snap.routes == []
    # generated_at should still be populated (current UTC time).
    assert snap.generated_at != ""


def test_extract_snapshot_skips_signals_without_a_name() -> None:
    """A signal with neither title nor signal_name is silently dropped."""
    from processing.report_snapshot import extract_snapshot

    rep = _mk_report(signals=[("Real Signal", 0.5)])
    # Add an "anonymous" signal that should be filtered out.
    rep.alpha.signals.append(_Signal(title="", score=0.9))
    snap = extract_snapshot(rep)

    names = [s["name"] for s in snap.signals]
    assert names == ["Real Signal"]


# ═══════════════════════════════════════════════════════════════════════════
# save_snapshot + load_latest_snapshots
# ═══════════════════════════════════════════════════════════════════════════

def test_save_snapshot_persists_row() -> None:
    """save_snapshot writes a row that comes back through load_latest."""
    from processing.report_snapshot import (
        extract_snapshot,
        load_latest_snapshots,
        save_snapshot,
    )

    rep = _mk_report(overall_score=0.7)
    snap = extract_snapshot(rep)
    assert save_snapshot(snap) is True

    loaded = load_latest_snapshots(n=2)
    assert len(loaded) == 1
    assert loaded[0].snapshot_id == snap.snapshot_id
    assert loaded[0].sentiment_overall_score == pytest.approx(0.7)


def test_save_snapshot_returns_false_on_none() -> None:
    """Passing None never raises; returns False."""
    from processing.report_snapshot import save_snapshot

    assert save_snapshot(None) is False  # type: ignore[arg-type]


def test_load_latest_snapshots_returns_newest_first() -> None:
    """load returns rows in desc generated_at order — newest at index 0."""
    from processing.report_snapshot import (
        ReportSnapshot,
        load_latest_snapshots,
        save_snapshot,
    )

    base = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    for i, delta_min in enumerate([0, 5, 10, 15]):
        snap = ReportSnapshot(
            snapshot_id=f"snap-{i}",
            generated_at=(base + timedelta(minutes=delta_min)).isoformat(),
            report_date="",
            sentiment_overall_score=float(i) / 10.0,
            sentiment_label="",
            risk_level="",
            signals=[],
            routes=[],
        )
        assert save_snapshot(snap) is True

    rows = load_latest_snapshots(n=2)
    assert len(rows) == 2
    # newest is index 0
    assert rows[0].snapshot_id == "snap-3"
    assert rows[1].snapshot_id == "snap-2"


def test_load_latest_snapshots_default_is_two() -> None:
    """The default n=2 matches the briefing-diff use case exactly."""
    from processing.report_snapshot import (
        ReportSnapshot,
        load_latest_snapshots,
        save_snapshot,
    )

    base = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(5):
        save_snapshot(ReportSnapshot(
            snapshot_id=f"d-{i}",
            generated_at=(base + timedelta(minutes=i)).isoformat(),
            report_date="",
            sentiment_overall_score=0.0,
            sentiment_label="",
            risk_level="",
            signals=[],
            routes=[],
        ))

    rows = load_latest_snapshots()  # no n → default 2
    assert len(rows) == 2


def test_load_latest_snapshots_empty_db_returns_empty_list() -> None:
    """Empty table → []; never raises."""
    from processing.report_snapshot import load_latest_snapshots

    rows = load_latest_snapshots(n=5)
    assert rows == []


def test_load_latest_snapshots_n_zero_returns_empty() -> None:
    """n=0 or negative returns [] without touching the DB."""
    from processing.report_snapshot import load_latest_snapshots

    assert load_latest_snapshots(n=0) == []
    assert load_latest_snapshots(n=-1) == []


# ═══════════════════════════════════════════════════════════════════════════
# user_id scoping
# ═══════════════════════════════════════════════════════════════════════════

def test_load_latest_snapshots_scopes_by_user_id() -> None:
    """alice's load must NOT see bob's snapshots."""
    from processing.report_snapshot import (
        ReportSnapshot,
        load_latest_snapshots,
        save_snapshot,
    )

    base = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    save_snapshot(
        ReportSnapshot(
            snapshot_id="alice-1",
            generated_at=(base + timedelta(minutes=1)).isoformat(),
            report_date="",
            sentiment_overall_score=0.5,
            sentiment_label="",
            risk_level="",
            signals=[],
            routes=[],
        ),
        user_id="alice",
    )
    save_snapshot(
        ReportSnapshot(
            snapshot_id="bob-1",
            generated_at=(base + timedelta(minutes=2)).isoformat(),
            report_date="",
            sentiment_overall_score=-0.5,
            sentiment_label="",
            risk_level="",
            signals=[],
            routes=[],
        ),
        user_id="bob",
    )

    alice_rows = load_latest_snapshots(n=5, user_id="alice")
    bob_rows = load_latest_snapshots(n=5, user_id="bob")

    assert {r.snapshot_id for r in alice_rows} == {"alice-1"}
    assert {r.snapshot_id for r in bob_rows} == {"bob-1"}


def test_legacy_user_id_empty_visible_to_authenticated_users() -> None:
    """Legacy user_id='' rows are visible under the dual-set semantics."""
    from processing.report_snapshot import (
        ReportSnapshot,
        load_latest_snapshots,
        save_snapshot,
    )

    base = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    save_snapshot(
        ReportSnapshot(
            snapshot_id="legacy",
            generated_at=base.isoformat(),
            report_date="",
            sentiment_overall_score=0.0,
            sentiment_label="",
            risk_level="",
            signals=[],
            routes=[],
        ),
        user_id="",
    )
    save_snapshot(
        ReportSnapshot(
            snapshot_id="alice-only",
            generated_at=(base + timedelta(minutes=1)).isoformat(),
            report_date="",
            sentiment_overall_score=0.0,
            sentiment_label="",
            risk_level="",
            signals=[],
            routes=[],
        ),
        user_id="alice",
    )

    rows = load_latest_snapshots(n=5, user_id="alice")
    ids = {r.snapshot_id for r in rows}
    # Dual-set: alice sees BOTH her own row AND the legacy '' row.
    assert ids == {"legacy", "alice-only"}


def test_empty_user_id_sees_every_row() -> None:
    """user_id='' (legacy code path) sees every snapshot in the table."""
    from processing.report_snapshot import (
        ReportSnapshot,
        load_latest_snapshots,
        save_snapshot,
    )

    base = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    for i, owner in enumerate(["alice", "bob", ""]):
        save_snapshot(
            ReportSnapshot(
                snapshot_id=f"row-{i}",
                generated_at=(base + timedelta(minutes=i)).isoformat(),
                report_date="",
                sentiment_overall_score=0.0,
                sentiment_label="",
                risk_level="",
                signals=[],
                routes=[],
            ),
            user_id=owner,
        )

    rows = load_latest_snapshots(n=10, user_id="")
    assert {r.snapshot_id for r in rows} == {"row-0", "row-1", "row-2"}


# ═══════════════════════════════════════════════════════════════════════════
# prune_old_snapshots
# ═══════════════════════════════════════════════════════════════════════════

def _seed_n_snapshots(n: int) -> list[str]:
    """Insert ``n`` snapshots with monotonically increasing generated_at;
    return the list of snapshot_ids in insertion order."""
    from processing.report_snapshot import ReportSnapshot, save_snapshot

    base = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    ids: list[str] = []
    for i in range(n):
        sid = f"p-{i:03d}"
        snap = ReportSnapshot(
            snapshot_id=sid,
            generated_at=(base + timedelta(minutes=i)).isoformat(),
            report_date="",
            sentiment_overall_score=0.0,
            sentiment_label="",
            risk_level="",
            signals=[],
            routes=[],
        )
        save_snapshot(snap)
        ids.append(sid)
    return ids


def test_prune_keeps_newest_n() -> None:
    """prune_old_snapshots(keep_n=3) keeps only the newest 3 rows."""
    from processing.report_snapshot import (
        load_latest_snapshots,
        prune_old_snapshots,
    )

    inserted = _seed_n_snapshots(10)
    deleted = prune_old_snapshots(keep_n=3)
    assert deleted == 7

    survivors = load_latest_snapshots(n=10)
    surviving_ids = {s.snapshot_id for s in survivors}
    # The three newest were the last three inserted.
    assert surviving_ids == set(inserted[-3:])


def test_prune_noop_when_below_limit() -> None:
    """When the table is already under the keep_n cap, prune returns 0."""
    from processing.report_snapshot import (
        load_latest_snapshots,
        prune_old_snapshots,
    )

    _seed_n_snapshots(2)
    assert prune_old_snapshots(keep_n=5) == 0
    assert len(load_latest_snapshots(n=10)) == 2


def test_prune_keep_zero_clears_table() -> None:
    """keep_n=0 deletes every row."""
    from processing.report_snapshot import (
        load_latest_snapshots,
        prune_old_snapshots,
    )

    _seed_n_snapshots(4)
    assert prune_old_snapshots(keep_n=0) == 4
    assert load_latest_snapshots(n=10) == []


# ═══════════════════════════════════════════════════════════════════════════
# Views satisfy compute_report_diff duck-typing contract
# ═══════════════════════════════════════════════════════════════════════════

def test_snapshot_views_expose_compute_report_diff_attributes() -> None:
    """The .sentiment / .alpha / .market / .freight properties expose
    exactly the attribute paths compute_report_diff looks up."""
    from processing.report_snapshot import ReportSnapshot

    snap = ReportSnapshot(
        snapshot_id="v1",
        generated_at="2026-05-22T00:00:00+00:00",
        report_date="",
        sentiment_overall_score=0.42,
        sentiment_label="BULLISH",
        risk_level="MODERATE",
        signals=[{"name": "S1", "score": 0.5}],
        routes=[{"route_id": "R1", "rate_usd_per_feu": 1500.0}],
    )

    # sentiment.overall_score / sentiment.overall_label
    assert snap.sentiment.overall_score == pytest.approx(0.42)
    assert snap.sentiment.overall_label == "BULLISH"
    # market.risk_level
    assert snap.market.risk_level == "MODERATE"
    # alpha.signals — each one has .title + .score (the AlphaSignal-y
    # contract compute_report_diff resolves first).
    sigs = snap.alpha.signals
    assert len(sigs) == 1
    assert sigs[0].title == "S1"
    assert sigs[0].score == pytest.approx(0.5)
    # freight.routes — preserved as dicts (compute_report_diff handles
    # dict input via _route_name / _route_rate)
    routes = snap.freight.routes
    assert routes[0]["route_id"] == "R1"
    assert routes[0]["rate_usd_per_feu"] == pytest.approx(1500.0)


def test_two_snapshots_through_compute_report_diff_produces_real_diff() -> None:
    """Round-trip two snapshots through compute_report_diff and assert
    the resulting ReportDiff carries non-empty sentiment shift, risk
    change, and signal/route deltas — i.e. the views satisfy the
    duck-typing contract end-to-end."""
    from processing.report_diff import compute_report_diff
    from processing.report_snapshot import ReportSnapshot

    prev = ReportSnapshot(
        snapshot_id="prev",
        generated_at="2026-05-21T00:00:00+00:00",
        report_date="May 21, 2026",
        sentiment_overall_score=0.1,
        sentiment_label="NEUTRAL",
        risk_level="LOW",
        signals=[
            {"name": "Sig A", "score": 0.5},
            {"name": "Sig B", "score": 0.2},
        ],
        routes=[
            {"route_id": "FBX01", "rate_usd_per_feu": 2000.0},
        ],
    )
    curr = ReportSnapshot(
        snapshot_id="curr",
        generated_at="2026-05-22T00:00:00+00:00",
        report_date="May 22, 2026",
        sentiment_overall_score=0.6,
        sentiment_label="BULLISH",
        risk_level="MODERATE",
        signals=[
            {"name": "Sig A", "score": 0.8},   # score changed
            {"name": "Sig C", "score": 0.4},    # new signal
            # Sig B dropped
        ],
        routes=[
            {"route_id": "FBX01", "rate_usd_per_feu": 2300.0},
        ],
    )

    diff = compute_report_diff(prev, curr)

    # Sentiment shift: curr - prev = 0.6 - 0.1 = 0.5
    assert diff.sentiment_shift == pytest.approx(0.5)
    # Risk change formatted
    assert diff.risk_level_change == "LOW -> MODERATE"
    # Sig C is new, Sig B dropped
    assert "Sig C" in diff.new_signals
    assert "Sig B" in diff.dropped_signals
    # Route rate change isn't empty
    assert any(
        d.name == "FBX01" and d.delta_pct > 0
        for d in diff.top_route_rate_changes
    )
    # Narrative is a real, non-default sentence
    assert diff.summary_narrative
    assert diff.summary_narrative != "No meaningful changes between reports."


def test_compute_report_diff_format_html_through_snapshots_non_empty() -> None:
    """The full diff → HTML render path through snapshot views is non-empty
    and free of the failure sentinel."""
    from processing.report_diff import compute_report_diff, format_diff_html
    from processing.report_snapshot import ReportSnapshot

    prev = ReportSnapshot(
        snapshot_id="p",
        generated_at="2026-05-21T00:00:00+00:00",
        report_date="May 21, 2026",
        sentiment_overall_score=0.0,
        sentiment_label="NEUTRAL",
        risk_level="LOW",
        signals=[{"name": "A", "score": 0.1}],
        routes=[],
    )
    curr = ReportSnapshot(
        snapshot_id="c",
        generated_at="2026-05-22T00:00:00+00:00",
        report_date="May 22, 2026",
        sentiment_overall_score=0.3,
        sentiment_label="BULLISH",
        risk_level="MODERATE",
        signals=[{"name": "A", "score": 0.4}],
        routes=[],
    )

    diff = compute_report_diff(prev, curr)
    html = format_diff_html(diff)
    assert html
    assert "Diff render failed" not in html
    # Some structural markers — the dates and the "What changed" label.
    assert "May 21" in html or "May 22" in html
    assert "What changed" in html
