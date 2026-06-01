"""processing/port_spillover_graph.py — port-to-port contagion graph.

When port A enters deficit on day N, do ports B, C follow within
N+1..N+lag days? If yes, A → B and A → C are spillover edges in the
contagion graph. The graph is built by walking historical snapshot
diffs and tallying, for each "entered deficit" event at port A, which
other ports also entered deficit within the lookahead window.

This is the operational signal an operator wants: "Shanghai just went
into deficit — historically, who's next?" The graph encodes that
question for every (A, B) pair.

Pure-function over snapshot history. The caller passes a list of
``PortRow`` lists (one per day, oldest-first); we walk them, compute
day-pair diffs internally, and tally co-occurrence + lagged
co-occurrence.

Edge metrics:
  * ``co_occurrence_count`` — how many times B followed A in the window
  * ``support`` — fraction of A's deficit events that B followed
                  (0 to 1; the "if A then B" probability)
  * ``lift`` — support divided by B's unconditional base rate.
              > 1 means B follows A more than chance.

Edges are filtered by ``min_co_occurrences`` (default 2) so a single
coincidence doesn't pollute the graph. Top edges are sorted by lift
DESC so the strongest predictive relationships surface first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


__all__ = [
    "SpilloverEdge",
    "SpilloverGraph",
    "build_spillover_graph",
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SpilloverEdge:
    """One directed edge in the contagion graph: source → target."""

    source_locode: str
    target_locode: str
    co_occurrence_count: int    # # times target entered deficit within window
                                # after source's entry
    source_event_count: int     # # times source itself entered deficit
    support: float              # co_occurrence_count / source_event_count
    target_base_rate: float     # target's unconditional entry frequency
    lift: float                 # support / target_base_rate
                                # > 1 = follow rate exceeds chance
    lag_within_days: int        # the lookahead window used


@dataclass
class SpilloverGraph:
    """Wrapper around a list of edges with summary stats."""

    edges: list[SpilloverEdge] = field(default_factory=list)
    n_days_examined: int = 0
    n_unique_sources: int = 0
    n_unique_targets: int = 0
    lag_within_days: int = 0


# ---------------------------------------------------------------------------
# Internal helpers — entered-deficit event detection
# ---------------------------------------------------------------------------


def _is_in_deficit(deficit_days: float) -> bool:
    """A port is "in deficit" when its supply_deficit_days <= 0.

    Matches the convention in tools/port_supply_diff.py — the
    deficit/surplus crossover point is 0d.
    """
    return float(deficit_days) <= 0


def _entered_deficit_set(
    yesterday_rows: list, today_rows: list,
) -> set[str]:
    """Locodes that crossed from surplus → deficit between two days.

    Both inputs are PortRow lists (or anything with ``locode`` and
    ``supply_deficit_days`` attributes). Defensive: rows missing
    either field are skipped.
    """
    y_map: dict[str, float] = {}
    for r in yesterday_rows:
        loc = getattr(r, "locode", None)
        dd = getattr(r, "supply_deficit_days", None)
        if loc and dd is not None:
            y_map[loc] = float(dd)

    entered: set[str] = set()
    for r in today_rows:
        loc = getattr(r, "locode", None)
        dd = getattr(r, "supply_deficit_days", None)
        if loc is None or dd is None:
            continue
        was_surplus = (loc in y_map) and (not _is_in_deficit(y_map[loc]))
        is_now_deficit = _is_in_deficit(float(dd))
        if was_surplus and is_now_deficit:
            entered.add(str(loc))
    return entered


# ---------------------------------------------------------------------------
# Public — graph builder
# ---------------------------------------------------------------------------


def build_spillover_graph(
    snapshot_history: list[list],
    *,
    lag_within_days: int = 3,
    min_co_occurrences: int = 2,
    min_lift: float = 1.0,
) -> SpilloverGraph:
    """Walk a snapshot history and tally lead-lag co-occurrence.

    ``snapshot_history`` is a list of PortRow lists, ONE PER DAY,
    oldest-first. The function:

      1. Walks consecutive day-pairs to detect entered-deficit events
         per day (a port crosses from surplus to deficit).
      2. For each event at port A on day N, looks ahead through days
         N+1 .. N+lag_within_days and records each port B that also
         entered deficit in that window. Each (A, B) pair gets one
         increment to ``co_occurrence_count`` per source event (so
         B following A twice across the history counts as 2).
      3. Computes per-port unconditional base rate = entries / day-pairs.
      4. Computes per-edge support + lift.
      5. Filters out edges with co_occurrence_count < min_co_occurrences
         (single coincidences) and lift < min_lift (random co-occurrence).
      6. Sorts surviving edges by lift DESC.

    Edge cases:
      * History with < 2 days        → no day-pairs, empty graph
      * History where no port ever
        enters deficit               → empty graph
      * lag_within_days <= 0         → clamped to 1
      * min_co_occurrences <= 0      → clamped to 1
    """
    lag = max(1, int(lag_within_days))
    min_co = max(1, int(min_co_occurrences))
    min_lift_v = max(0.0, float(min_lift))

    if len(snapshot_history) < 2:
        return SpilloverGraph(
            edges=[], n_days_examined=len(snapshot_history),
            n_unique_sources=0, n_unique_targets=0,
            lag_within_days=lag,
        )

    # ── Step 1: per-day entered_deficit sets ──────────────────────────
    # We need len(snapshot_history) - 1 transitions; entries_by_day[i]
    # holds the set of locodes that entered deficit between day i-1
    # and day i, indexed from 1.
    entries_by_day: list[set[str]] = [set()]   # day 0 has no transition
    for i in range(1, len(snapshot_history)):
        entries_by_day.append(
            _entered_deficit_set(snapshot_history[i - 1], snapshot_history[i])
        )

    # ── Step 2: tally co-occurrence within the lookahead window ─────
    # co_count[A][B] = # times B entered deficit within window AFTER A
    # source_event_count[A] = # of A's entered-deficit events
    co_count: dict[str, dict[str, int]] = {}
    source_event_count: dict[str, int] = {}
    n_transitions = len(entries_by_day) - 1   # exclude the day-0 padding

    for day_idx in range(1, len(entries_by_day)):
        source_locodes = entries_by_day[day_idx]
        for src in source_locodes:
            source_event_count[src] = source_event_count.get(src, 0) + 1
            # Collect the DISTINCT targets that entered deficit anywhere in
            # the lookahead window, then count each once for THIS source
            # event. Counting per-source-event (not once per window-day a
            # target re-enters) keeps co_occurrence_count <= source_event_count
            # so support stays a probability in [0, 1] — and a target that
            # merely oscillates across the 0d crossover inside one window
            # can't satisfy min_co_occurrences on a single source event.
            followers: set[str] = set()
            for ahead in range(1, lag + 1):
                target_idx = day_idx + ahead
                if target_idx >= len(entries_by_day):
                    break
                followers |= entries_by_day[target_idx]
            followers.discard(src)   # don't self-loop
            for tgt in followers:
                co_count.setdefault(src, {})
                co_count[src][tgt] = co_count[src].get(tgt, 0) + 1

    # ── Step 3: per-port base rate ──────────────────────────────────
    # base_rate[X] = total entries for X / total day-transitions
    base_rate: dict[str, float] = {}
    if n_transitions > 0:
        total_entries: dict[str, int] = {}
        for day_set in entries_by_day[1:]:
            for loc in day_set:
                total_entries[loc] = total_entries.get(loc, 0) + 1
        for loc, count in total_entries.items():
            base_rate[loc] = count / n_transitions

    # ── Step 4 + 5: build + filter edges ────────────────────────────
    edges: list[SpilloverEdge] = []
    for src, tgt_counts in co_count.items():
        src_events = source_event_count.get(src, 0)
        if src_events == 0:
            continue
        for tgt, co in tgt_counts.items():
            if co < min_co:
                continue
            support = co / src_events
            target_br = base_rate.get(tgt, 0.0)
            # If target's base rate is 0, lift is undefined — but we
            # only get here when target appeared in entries_by_day, so
            # base_rate[tgt] > 0 by construction. Defensive fallback
            # of 0.0 if division fails.
            lift = (support / target_br) if target_br > 0 else 0.0
            if lift < min_lift_v:
                continue
            edges.append(SpilloverEdge(
                source_locode=src, target_locode=tgt,
                co_occurrence_count=co,
                source_event_count=src_events,
                support=support,
                target_base_rate=target_br,
                lift=lift,
                lag_within_days=lag,
            ))

    # ── Step 6: sort by lift DESC ───────────────────────────────────
    edges.sort(key=lambda e: e.lift, reverse=True)

    return SpilloverGraph(
        edges=edges,
        n_days_examined=len(snapshot_history),
        n_unique_sources=len({e.source_locode for e in edges}),
        n_unique_targets=len({e.target_locode for e in edges}),
        lag_within_days=lag,
    )
