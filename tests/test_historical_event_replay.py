"""Defining-property tests for processing.historical_event_replay.

The replay validator wires the historical-event registry
(:mod:`data.historical_events`) into the live alert engine
(:mod:`engine.alert_engine_v2`) so we can answer: for each registered
historical event, would the engine have fired the right alert kinds at
roughly the right severity?

Key invariants the suite locks in:

* ``replay_event`` on a fake event with NO expected kinds and clean
  inputs returns ``passed=True``.
* ``replay_event`` on a fake event expecting CONGESTION (and only
  CONGESTION) — with a hand-built congested-port input that DOES
  trip the engine's congestion threshold — returns ``passed=True``.
* ``replay_event`` on a fake event expecting RATE_SURGE but whose shape
  fires only CONGESTION → ``passed=False`` with RATE_SURGE in
  ``missing_kinds`` and CONGESTION in ``unexpected_kinds``.
* Severity ±1 band passes; ±2 bands fails.
* ``replay_all_events`` returns exactly one ReplayResult per registered
  event — count match locks the iteration contract.
* ``summarize_replay`` on empty input returns a fully-zeroed dict and
  does not raise.
* Pass-rate floor: at least 75% of registered events pass today (the
  current implementation reaches 100%, but 75% is the contract; below
  that the alert engine has lost its event-detection calibration).

These tests use hand-built fake events so they do not depend on the
registry's exact contents (which can grow / change). The
``replay_all_events`` test that DOES use the registry is the
calibration smoke test — see ``test_replay_all_events_count_matches_registry``
and ``test_replay_all_events_meets_pass_floor``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from data.historical_events import EVENTS
from processing.historical_event_replay import (
    EXPECTED_ALERT_KINDS_BY_EVENT,
    EXPECTED_SEVERITY_BY_EVENT,
    SEVERITY_BANDS,
    ReplayResult,
    replay_all_events,
    replay_event,
    summarize_replay,
)


# ── Fixture: isolate state DB ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db. The alert engine's
    check_* functions can do incidental persistence-layer reads (rule
    overrides) so we keep them on a throwaway DB."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ── Fake event stand-in ────────────────────────────────────────────────────


@dataclass
class _FakeEvent:
    """Duck-typed stand-in for HistoricalEvent.

    Keeps tests independent of the registry's growing list — we craft
    the conditions we need (empty routes, specific routes, etc.) per
    test.
    """
    event_id: str = "fake_event"
    name: str = "Fake Event"
    start_date: str = "2020-01-01"
    end_date: str = "2020-01-15"
    severity: str = "moderate"
    affected_routes: list[str] = field(default_factory=list)
    affected_chokepoints: list[str] = field(default_factory=list)
    description: str = ""
    expected_ssi_band: str = "Stressed"
    expected_lead_time_days: int = 7


# ── replay_event — degenerate cases ────────────────────────────────────────


def test_replay_event_none_input_returns_failed_result_no_raise() -> None:
    """``None`` event must not raise — yields an empty-but-valid
    ReplayResult with passed=False."""
    result = replay_event(None)
    assert isinstance(result, ReplayResult)
    assert result.event_id == ""
    assert result.passed is False
    assert result.fired_alert_kinds == []


def test_replay_event_unknown_id_no_expected_clean_inputs_passes() -> None:
    """An event id NOT in EXPECTED_ALERT_KINDS_BY_EVENT and with no
    affected routes/chokepoints fires nothing. With no expectations and
    no fires, the result passes vacuously (no missing kinds, severities
    are both empty so they match by definition)."""
    event = _FakeEvent(event_id="totally_unknown")
    result = replay_event(event)
    assert result.event_id == "totally_unknown"
    assert result.expected_alert_kinds == []
    assert result.missing_kinds == []
    assert result.severity_match is True
    assert result.passed is True


# ── replay_event — hand-built positive case (CONGESTION) ───────────────────


def test_replay_event_congestion_fires_when_route_dest_port_loaded(
    monkeypatch,
) -> None:
    """Pin the expected_alert_kinds map to expect CONGESTION-only for a
    new fake event id, then build an event whose affected_routes
    include a registered route — the synthesised port_results will give
    the dest port a 0.93 congestion score (above the 0.75 default
    threshold AND the 0.90 CRITICAL bar). The engine should fire
    CONGESTION at CRITICAL.

    With expected severity HIGH (one band lower than CRITICAL), the
    ±1-band check still passes."""
    fake_id = "fake_congestion"
    monkeypatch.setitem(
        EXPECTED_ALERT_KINDS_BY_EVENT, fake_id, ["CONGESTION"],
    )
    monkeypatch.setitem(
        EXPECTED_SEVERITY_BY_EVENT, fake_id, "HIGH",
    )
    event = _FakeEvent(
        event_id=fake_id,
        severity="severe",
        # transpacific_eb is in ROUTES_BY_ID — its dest port USLAX
        # will get a CRITICAL-band congestion score.
        affected_routes=["transpacific_eb"],
    )
    result = replay_event(event)
    assert "CONGESTION" in result.fired_alert_kinds, (
        f"Expected CONGESTION to fire; got {result.fired_alert_kinds}"
    )
    assert "CONGESTION" not in result.missing_kinds
    # Severity ±1 band → passed=True.
    assert result.severity_match is True


# ── replay_event — mismatch case (expected one, got another) ───────────────


def test_replay_event_mismatch_surfaces_missing_and_unexpected(
    monkeypatch,
) -> None:
    """Set up a fake event expecting STOCK_MOVE (which our synthesis
    never produces — we don't inject stock_data). The synthesised
    inputs still trip CONGESTION (because the affected route's dest
    port gets a high score) → missing=STOCK_MOVE,
    unexpected=CONGESTION, passed=False."""
    fake_id = "fake_mismatch"
    monkeypatch.setitem(
        EXPECTED_ALERT_KINDS_BY_EVENT, fake_id, ["STOCK_MOVE"],
    )
    monkeypatch.setitem(
        EXPECTED_SEVERITY_BY_EVENT, fake_id, "HIGH",
    )
    event = _FakeEvent(
        event_id=fake_id,
        severity="major",
        affected_routes=["transpacific_eb"],
    )
    result = replay_event(event)
    assert "STOCK_MOVE" in result.missing_kinds
    assert "CONGESTION" in result.unexpected_kinds
    assert result.passed is False, (
        f"Expected passed=False on missing kinds; got "
        f"missing={result.missing_kinds}, unexpected={result.unexpected_kinds}"
    )


# ── Severity band tolerance ────────────────────────────────────────────────


def test_severity_within_one_band_passes(monkeypatch) -> None:
    """A two-band gap fails, a one-band gap passes. Pin an event whose
    rate-spike severity is CRITICAL (severe → 2.00x mult → +100% move
    → 2x threshold = CRITICAL) and expect HIGH — one band gap, passes.
    """
    fake_id = "fake_band_one"
    monkeypatch.setitem(
        EXPECTED_ALERT_KINDS_BY_EVENT, fake_id, ["RATE_SURGE"],
    )
    monkeypatch.setitem(
        EXPECTED_SEVERITY_BY_EVENT, fake_id, "HIGH",
    )
    event = _FakeEvent(
        event_id=fake_id,
        severity="severe",
        affected_routes=["asia_europe"],
    )
    result = replay_event(event)
    assert "RATE_SURGE" in result.fired_alert_kinds
    assert result.fired_severity == "CRITICAL"
    assert result.severity_match is True
    assert result.passed is True


def test_severity_two_bands_off_fails(monkeypatch) -> None:
    """Two-band gap (CRITICAL fired vs LOW expected) trips
    severity_match=False and therefore passed=False."""
    fake_id = "fake_band_two"
    monkeypatch.setitem(
        EXPECTED_ALERT_KINDS_BY_EVENT, fake_id, ["RATE_SURGE"],
    )
    monkeypatch.setitem(
        EXPECTED_SEVERITY_BY_EVENT, fake_id, "LOW",
    )
    event = _FakeEvent(
        event_id=fake_id,
        severity="severe",
        affected_routes=["asia_europe"],
    )
    result = replay_event(event)
    assert result.fired_severity == "CRITICAL"
    assert result.severity_match is False
    assert result.passed is False


# ── replay_all_events ──────────────────────────────────────────────────────


def test_replay_all_events_count_matches_registry() -> None:
    """The list of ReplayResults returned by replay_all_events must be
    one-to-one with EVENTS — the iteration contract.

    If a new event is added to the registry, this test catches a
    silent skip immediately."""
    results = replay_all_events()
    assert len(results) == len(EVENTS), (
        f"Expected {len(EVENTS)} results (one per registered event); "
        f"got {len(results)}"
    )
    # Every event_id present in the registry must appear in the
    # results (order doesn't matter — sets compare).
    expected_ids = {e.event_id for e in EVENTS}
    actual_ids = {r.event_id for r in results}
    assert actual_ids == expected_ids


def test_replay_all_events_empty_override_returns_empty_list() -> None:
    """``events=[]`` is honoured — no registry pull."""
    results = replay_all_events(events=[])
    assert results == []


def test_replay_all_events_meets_pass_floor() -> None:
    """At least 75% of registered events must pass the replay — this is
    the "alert engine is calibrated to history" smoke test.

    The current implementation reaches 100% (every documented event
    fires the expected kind at a severity within ±1 band of the
    registry's coarse label). 75% is the floor — below that, the
    alert engine has materially lost its detection calibration for
    historical-shaped disruptions, and that's a real signal worth
    investigation.

    NOTE: 100% pass is the present steady state. If this floor needs
    raising in the future, do it deliberately as a tightening commit;
    don't auto-raise it on every passing run."""
    results = replay_all_events()
    n = len(results)
    assert n > 0, "Registry must not be empty for this floor check."
    passed = sum(1 for r in results if r.passed)
    pass_rate = passed / n
    assert pass_rate >= 0.75, (
        f"Pass rate {pass_rate:.1%} ({passed}/{n}) below the 75% floor — "
        f"the alert engine has likely lost its event-detection calibration. "
        f"Failed event ids: "
        f"{[r.event_id for r in results if not r.passed]}"
    )


# ── summarize_replay ───────────────────────────────────────────────────────


def test_summarize_replay_empty_input_returns_zeroed_dict() -> None:
    """``[]`` does not raise; returns a fully-zeroed dict with the same
    keys as a normal summary."""
    s = summarize_replay([])
    assert s["total"] == 0
    assert s["passed"] == 0
    assert s["failed"] == 0
    assert s["pass_rate"] == 0.0
    assert s["miss_rate"] == 0.0
    assert s["false_positive_rate"] == 0.0
    assert s["top_missing_kinds"] == []


def test_summarize_replay_handcrafted_results() -> None:
    """Build three ReplayResults by hand and check the roll-up arithmetic
    exactly. Avoids dependency on the registry state."""
    results = [
        ReplayResult(
            event_id="ok1", event_date="2020-01-01", event_label="OK 1",
            expected_alert_kinds=["RATE_SURGE"], fired_alert_kinds=["RATE_SURGE"],
            missing_kinds=[], unexpected_kinds=[],
            passed=True,
        ),
        ReplayResult(
            event_id="ok2", event_date="2020-02-01", event_label="OK 2",
            expected_alert_kinds=["CONGESTION"], fired_alert_kinds=["CONGESTION"],
            missing_kinds=[], unexpected_kinds=[],
            passed=True,
        ),
        ReplayResult(
            event_id="fail", event_date="2020-03-01", event_label="Fail",
            expected_alert_kinds=["RATE_SURGE"], fired_alert_kinds=["CONGESTION"],
            missing_kinds=["RATE_SURGE"], unexpected_kinds=["CONGESTION"],
            passed=False,
        ),
    ]
    s = summarize_replay(results)
    assert s["total"] == 3
    assert s["passed"] == 2
    assert s["failed"] == 1
    assert s["pass_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert s["miss_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert s["false_positive_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert s["top_missing_kinds"] == [("RATE_SURGE", 1)]


def test_summarize_replay_top_missing_kinds_orders_by_count() -> None:
    """``top_missing_kinds`` is sorted by count desc — the most-missed
    kind comes first."""
    results = [
        ReplayResult(
            event_id=f"r{i}", event_date="2020-01-01", event_label=f"R{i}",
            missing_kinds=["RATE_SURGE"], passed=False,
        )
        for i in range(3)
    ] + [
        ReplayResult(
            event_id="r3", event_date="2020-01-01", event_label="R3",
            missing_kinds=["CONGESTION"], passed=False,
        ),
    ]
    s = summarize_replay(results)
    assert s["top_missing_kinds"][0] == ("RATE_SURGE", 3)
    assert s["top_missing_kinds"][1] == ("CONGESTION", 1)


# ── SEVERITY_BANDS constant ────────────────────────────────────────────────


def test_severity_bands_is_engine_ordering() -> None:
    """Sanity check: the local SEVERITY_BANDS list matches the alert
    engine's ordering (CRITICAL most severe, LOW least severe). A
    refactor to the engine's vocabulary must update this constant in
    lockstep — the test catches drift."""
    assert SEVERITY_BANDS == ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
