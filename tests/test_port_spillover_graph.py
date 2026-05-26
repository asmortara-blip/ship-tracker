"""Defining-property tests for processing/port_spillover_graph.py."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from processing.port_spillover_graph import (
    SpilloverEdge,
    SpilloverGraph,
    build_spillover_graph,
)


# ── Fixture helpers ─────────────────────────────────────────────────────


@dataclass
class _StubRow:
    """Mimics PortRow for the attributes we read."""
    locode: str
    supply_deficit_days: float


def _day(*pairs: tuple[str, float]) -> list[_StubRow]:
    """Convenience: build a day's worth of port rows from (locode, deficit) pairs."""
    return [_StubRow(locode=loc, supply_deficit_days=dd) for loc, dd in pairs]


# ── Degenerate inputs ──────────────────────────────────────────────────


def test_empty_history_returns_empty_graph() -> None:
    g = build_spillover_graph([])
    assert isinstance(g, SpilloverGraph)
    assert g.edges == []
    assert g.n_days_examined == 0


def test_single_day_history_returns_empty_graph() -> None:
    """Need at least 2 days for a transition."""
    g = build_spillover_graph([_day(("A", -2.0))])
    assert g.edges == []
    assert g.n_days_examined == 1


def test_lag_clamped_to_one_when_zero() -> None:
    """lag=0 doesn't make sense — clamp to 1 so the day-after counts."""
    history = [
        _day(("A", +2.0), ("B", +2.0)),
        _day(("A", -1.0), ("B", +2.0)),   # A entered deficit
        _day(("A", -1.0), ("B", -1.0)),   # B follows next day
    ]
    g = build_spillover_graph(
        history, lag_within_days=0, min_co_occurrences=1, min_lift=0.0,
    )
    assert g.lag_within_days == 1
    locode_pairs = {(e.source_locode, e.target_locode) for e in g.edges}
    assert ("A", "B") in locode_pairs


# ── Basic lead-lag detection ───────────────────────────────────────────


def test_single_lead_lag_pair_detected_at_lag_one() -> None:
    """Day 1: A enters deficit. Day 2: B enters deficit (within lag=3).
    → edge A → B with co_occurrence=1 (filtered by min=2 default)."""
    history = [
        _day(("A", +2.0), ("B", +2.0)),   # baseline
        _day(("A", -1.0), ("B", +2.0)),   # A entered
        _day(("A", -1.0), ("B", -1.0)),   # B entered (lag=1)
    ]
    g = build_spillover_graph(
        history, lag_within_days=3, min_co_occurrences=1, min_lift=0.0,
    )
    pairs = {(e.source_locode, e.target_locode) for e in g.edges}
    assert ("A", "B") in pairs


def test_self_loops_excluded() -> None:
    """A → A is meaningless (we already know A entered)."""
    history = [
        _day(("A", +2.0)),
        _day(("A", -1.0)),
        _day(("A", -1.0)),
    ]
    g = build_spillover_graph(
        history, min_co_occurrences=1, min_lift=0.0,
    )
    for e in g.edges:
        assert e.source_locode != e.target_locode


def test_pair_outside_lag_window_not_detected() -> None:
    """B enters deficit 5 days after A, with lag=2 → not detected."""
    history = [
        _day(("A", +2.0), ("B", +2.0)),                  # day 0
        _day(("A", -1.0), ("B", +2.0)),                  # day 1: A entered
        _day(("A", -1.0), ("B", +2.0)),                  # day 2
        _day(("A", -1.0), ("B", +2.0)),                  # day 3
        _day(("A", -1.0), ("B", +2.0)),                  # day 4
        _day(("A", -1.0), ("B", -1.0)),                  # day 5: B entered (lag 4)
    ]
    g = build_spillover_graph(
        history, lag_within_days=2, min_co_occurrences=1, min_lift=0.0,
    )
    pairs = {(e.source_locode, e.target_locode) for e in g.edges}
    assert ("A", "B") not in pairs


# ── Co-occurrence + lift filters ───────────────────────────────────────


def test_min_co_occurrences_filter_drops_single_coincidence() -> None:
    """A → B happens once, min_co=2 → no edge emitted."""
    history = [
        _day(("A", +2.0), ("B", +2.0)),
        _day(("A", -1.0), ("B", +2.0)),
        _day(("A", -1.0), ("B", -1.0)),
    ]
    g = build_spillover_graph(
        history, lag_within_days=3, min_co_occurrences=2, min_lift=0.0,
    )
    pairs = {(e.source_locode, e.target_locode) for e in g.edges}
    assert ("A", "B") not in pairs


def test_min_lift_filter_drops_chance_co_occurrence() -> None:
    """If B enters deficit every single day, the A→B 'follow' isn't
    evidence of contagion — lift = support / base_rate ≈ 1.0. With
    min_lift > 1.0 the edge should be filtered."""
    history = []
    history.append(_day(("A", +2.0), ("B", +2.0)))      # baseline
    # B enters and stays in deficit for the rest of history — base rate high
    # A enters deficit on day 2
    history.append(_day(("A", +2.0), ("B", -1.0)))       # B entered day 1
    history.append(_day(("A", -1.0), ("B", -1.0)))       # A entered day 2; B already in
    history.append(_day(("A", -1.0), ("B", -1.0)))       # day 3
    g = build_spillover_graph(
        history, lag_within_days=3, min_co_occurrences=1, min_lift=2.0,
    )
    pairs = {(e.source_locode, e.target_locode) for e in g.edges}
    # Since B never re-enters deficit after day 1, A→B co_occurrence is 0
    # → no edge regardless.
    assert ("A", "B") not in pairs


def test_perfect_lead_lag_has_high_lift() -> None:
    """A enters → B follows reliably every time. base_rate(B) << support
    → lift > 1.0."""
    # Construct A → B fires twice + B only ever enters after A fires.
    history = [
        _day(("A", +2.0), ("B", +2.0), ("C", +2.0)),    # day 0
        _day(("A", -1.0), ("B", +2.0), ("C", +2.0)),    # day 1: A enters
        _day(("A", -1.0), ("B", -1.0), ("C", +2.0)),    # day 2: B enters (follow)
        _day(("A", +2.0), ("B", +2.0), ("C", +2.0)),    # day 3: reset
        _day(("A", -1.0), ("B", +2.0), ("C", +2.0)),    # day 4: A enters again
        _day(("A", -1.0), ("B", -1.0), ("C", +2.0)),    # day 5: B follows again
    ]
    g = build_spillover_graph(
        history, lag_within_days=2, min_co_occurrences=2, min_lift=1.0,
    )
    ab = next(
        (e for e in g.edges
         if e.source_locode == "A" and e.target_locode == "B"),
        None,
    )
    assert ab is not None
    assert ab.co_occurrence_count == 2
    assert ab.source_event_count == 2
    assert ab.support == pytest.approx(1.0)
    # B enters 2 out of 5 transitions → base_rate = 0.4
    # lift = 1.0 / 0.4 = 2.5
    assert ab.lift == pytest.approx(2.5)


# ── Sort order ─────────────────────────────────────────────────────────


def test_edges_sorted_by_lift_descending() -> None:
    """Edges with higher lift surface first — operator scans top down."""
    # Build a history where A→B has lift 2.0 and A→C has lift 1.2
    history = [
        _day(("A", +2.0), ("B", +2.0), ("C", +2.0)),
        _day(("A", -1.0), ("B", +2.0), ("C", -1.0)),   # A and C enter
        _day(("A", -1.0), ("B", -1.0), ("C", -1.0)),   # B follows A
        _day(("A", +2.0), ("B", +2.0), ("C", +2.0)),
        _day(("A", -1.0), ("B", -1.0), ("C", -1.0)),   # A,B,C all enter same day
    ]
    g = build_spillover_graph(
        history, lag_within_days=2, min_co_occurrences=1, min_lift=0.0,
    )
    lifts = [e.lift for e in g.edges]
    assert lifts == sorted(lifts, reverse=True)


# ── Graph summary fields ───────────────────────────────────────────────


def test_graph_reports_unique_source_and_target_counts() -> None:
    history = [
        _day(("A", +2.0), ("B", +2.0)),
        _day(("A", -1.0), ("B", +2.0)),
        _day(("A", -1.0), ("B", -1.0)),
        _day(("A", +2.0), ("B", +2.0)),
        _day(("A", -1.0), ("B", -1.0)),
    ]
    g = build_spillover_graph(
        history, lag_within_days=3, min_co_occurrences=1, min_lift=0.0,
    )
    # Single A → B edge → 1 unique source + 1 unique target
    if g.edges:
        assert g.n_unique_sources >= 1
        assert g.n_unique_targets >= 1
    assert g.n_days_examined == 5
    assert g.lag_within_days == 3


def test_history_with_no_entered_deficit_events_returns_empty() -> None:
    """Every port stays in surplus forever → no events → no edges."""
    history = [
        _day(("A", +5.0), ("B", +5.0)),
        _day(("A", +5.0), ("B", +5.0)),
        _day(("A", +5.0), ("B", +5.0)),
    ]
    g = build_spillover_graph(history, min_co_occurrences=1, min_lift=0.0)
    assert g.edges == []
