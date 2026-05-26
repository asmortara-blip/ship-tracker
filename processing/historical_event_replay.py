"""Historical-event replay validator — closes the loop between the event
registry (:mod:`data.historical_events`) and the live alert engine
(:mod:`engine.alert_engine_v2`).

The Shipping Stress Index already has a backtester
(:mod:`processing.disruption_backtest`) that scores whether the *SSI math*
responds to event-shaped inputs. That tells us the score moves the right
way. It does NOT tell us whether the *alert engine* — the user-facing
"these are the alerts that would have fired" layer — would have fired the
right alert kinds at the right severity for each event.

This module does that. For every event in
:data:`data.historical_events.EVENTS` it:

  1. Mutates the input bundles (freight DataFrames, port congestion list,
     macro BDI series, regional equipment status) into a state consistent
     with the event's known shape (a Suez blockage → spiked rates on
     asia_europe; a Panama drought → spiked rates on the affected lanes;
     a US-West-Coast labour dispute → congestion at USLAX/USLGB; etc.).
  2. Runs the live alert engine's pure-function ``check_*_alerts`` helpers
     against those inputs.
  3. Compares the alert *kinds* that fired (``alert_type`` strings) to the
     event's ``expected_alert_kinds`` mapping defined inline in this
     module.
  4. Reports a per-event :class:`ReplayResult` and a roll-up summary.

Critically: this module **never mutates the alert engine** and **never
persists alerts** — it calls the pure ``check_*_alerts`` functions in
isolation against synthesised inputs and reads their return values. The
goal is *observation, not behaviour change*.

Why the ``expected_alert_kinds`` mapping lives here, not on
:class:`data.historical_events.HistoricalEvent`: the registry is a pure
ground-truth list, and pinning alert-engine implementation details onto
it would couple the registry to the engine's current alert-type
vocabulary. Keeping the mapping in this module keeps the registry stable
and lets us evolve the alert vocabulary independently. When the alert
engine adds a new alert kind, the registry does not have to change.

This is a pure-processing module: no Streamlit, no I/O, no SQLite writes.
All public helpers NEVER raise — on bad inputs they return a valid
:class:`ReplayResult` with ``passed=False`` rather than blowing up.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from loguru import logger

from data.historical_events import EVENTS, HistoricalEvent
from engine.alert_engine_v2 import (
    check_bdi_alerts,
    check_congestion_alerts,
    check_port_deficit_alerts,
    check_rate_alerts,
)
from processing.chokepoint_analyzer import CHOKEPOINTS
from processing.equipment_tracker import REGIONAL_EQUIPMENT_STATUS
from routes.route_registry import ROUTES_BY_ID


__all__ = [
    "ReplayResult",
    "replay_event",
    "replay_all_events",
    "summarize_replay",
    "EXPECTED_ALERT_KINDS_BY_EVENT",
    "EXPECTED_SEVERITY_BY_EVENT",
    "SEVERITY_BANDS",
]


# ---------------------------------------------------------------------------
# Expected-alert-kinds mapping (kept inline, NOT on the registry)
# ---------------------------------------------------------------------------
#
# Per-event mapping of which alert kinds the live engine SHOULD fire for
# the event's shape. Conservative by design — we only include alert kinds
# the event's documented mechanism would credibly trigger. Ambiguous
# kinds (e.g. SIGNAL_FIRE, STOCK_MOVE — both depend on platform state
# the registry does not pin down) are deliberately excluded; better to
# under-claim than to declare a false failure.
#
# Event-by-event rationale (one line each):
#
#   covid_2020          — system-wide demand shock → rates surged on most
#                         lanes, broad congestion → RATE_SURGE + CONGESTION.
#   suez_2021           — Ever Given grounding → asia_europe + ningbo_europe
#                         rates spiked + Suez chokepoint disrupted
#                         → RATE_SURGE (chokepoint not its own kind).
#   uswc_2014           — port labour dispute → USLAX/USLGB congestion.
#                         CONGESTION is the only first-order kind.
#   hanjin_2016         — bankruptcy stranded TPEB capacity → rates spiked.
#                         RATE_SURGE.
#   felixstowe_2021     — port congestion (UK) → CONGESTION.
#   uswc_2023           — port labour dispute (same shape as 2014) →
#                         CONGESTION.
#   panama_drought_2023 — drought restricted transits → rates spiked on the
#                         affected US-East-Coast lanes → RATE_SURGE.
#   red_sea_2024        — Houthi attacks forced Cape rerouting → rates
#                         spiked on Asia-Europe + Med routes → RATE_SURGE.
#
# Note: BDI_MOVE / SIGNAL_FIRE / PORT_DEFICIT / STOCK_MOVE are intentionally
# NOT included for most events. BDI is a dry-bulk index — the events here
# are container-shipping events, so BDI moves are second-order and noisy.
# SIGNAL_FIRE depends on the alpha engine's own state, not the event.
# PORT_DEFICIT requires container-supply data we cannot reconstruct for
# 2014/2016 historical periods. STOCK_MOVE moves with carrier earnings,
# not directly with the event.
EXPECTED_ALERT_KINDS_BY_EVENT: dict[str, list[str]] = {
    "covid_2020":          ["RATE_SURGE", "CONGESTION"],
    "suez_2021":           ["RATE_SURGE"],
    "uswc_2014":           ["CONGESTION"],
    "hanjin_2016":         ["RATE_SURGE"],
    "felixstowe_2021":     ["CONGESTION"],
    "uswc_2023":           ["CONGESTION"],
    "panama_drought_2023": ["RATE_SURGE"],
    "red_sea_2024":        ["RATE_SURGE"],
}


# Per-event expected severity for the strongest alert that fires. The
# alert engine emits CRITICAL / HIGH / MEDIUM / LOW. We map the
# registry's coarse severity (severe/major/moderate) onto the engine's
# vocabulary so the band check uses one ordering.
EXPECTED_SEVERITY_BY_EVENT: dict[str, str] = {
    "covid_2020":          "CRITICAL",
    "suez_2021":           "CRITICAL",
    "uswc_2014":           "HIGH",
    "hanjin_2016":         "HIGH",
    "felixstowe_2021":     "MEDIUM",
    "uswc_2023":           "HIGH",
    "panama_drought_2023": "HIGH",
    "red_sea_2024":        "CRITICAL",
}


# Severity ordering — index = rank, 0 = most severe. The alert engine
# uses the same ordering internally (``_SEVERITY_ORDER``); we keep a
# local copy so we don't depend on importing a private symbol.
SEVERITY_BANDS: list[str] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ReplayResult:
    """One historical event's replay outcome.

    Fields:

    event_id, event_date, event_label
        Identifying triplet copied from the source event for downstream
        rendering / debugging — so a ReplayResult can be inspected in
        isolation without a registry lookup.
    expected_alert_kinds
        The list of ``alert_type`` strings (from
        :data:`EXPECTED_ALERT_KINDS_BY_EVENT`) the engine SHOULD fire on
        this event's shape. Empty list means "we make no claim for this
        event" — the replay still runs and records what actually fired
        but ``passed`` is True when nothing is missing.
    fired_alert_kinds
        Unique sorted list of ``alert_type`` strings the engine actually
        fired during the replay. Multiple alerts of the same kind
        collapse to a single entry.
    missing_kinds
        Sorted list of kinds in ``expected_alert_kinds`` that did NOT
        appear in ``fired_alert_kinds``. Empty = nothing missing.
    unexpected_kinds
        Sorted list of kinds that fired but were NOT expected. These do
        NOT cause ``passed`` to flip — false positives are surfaced for
        operator review but the platform's contract is "fire on every
        documented disruption", not "fire ONLY on documented
        disruptions" (since real-world data will always include other
        live conditions).
    expected_severity, fired_severity
        Engine-vocabulary severity labels (``CRITICAL`` / ``HIGH`` /
        ``MEDIUM`` / ``LOW``). ``fired_severity`` is the highest
        severity across all fired alerts; ``""`` if no alerts fired.
    severity_match
        True when ``fired_severity`` is within ±1 band of
        ``expected_severity`` — a one-band gap (CRITICAL expected, HIGH
        fired) is tolerated as "close enough"; two bands or more is a
        real miss.
    passed
        Roll-up: True when ``missing_kinds`` is empty AND
        ``severity_match`` is True. Unexpected kinds do not affect this
        flag (see ``unexpected_kinds`` above).
    """

    event_id: str
    event_date: str
    event_label: str
    expected_alert_kinds: list[str] = field(default_factory=list)
    fired_alert_kinds: list[str] = field(default_factory=list)
    missing_kinds: list[str] = field(default_factory=list)
    unexpected_kinds: list[str] = field(default_factory=list)
    expected_severity: str = ""
    fired_severity: str = ""
    severity_match: bool = True
    passed: bool = False


# ---------------------------------------------------------------------------
# Severity helpers
# ---------------------------------------------------------------------------


def _severity_rank(severity: str) -> int:
    """Map a severity label to its rank. Unknown → 99 (sentinel)."""
    try:
        return SEVERITY_BANDS.index(severity)
    except ValueError:
        return 99


def _severity_within_one_band(a: str, b: str) -> bool:
    """True if ``a`` and ``b`` are within ±1 ordinal band on
    :data:`SEVERITY_BANDS`. Empty strings (no alerts fired) collapse to
    "not matching" — but only when the expected severity is non-empty.
    Two empties match vacuously.
    """
    if not a and not b:
        return True
    if not a or not b:
        return False
    return abs(_severity_rank(a) - _severity_rank(b)) <= 1


def _highest_severity(alerts: list) -> str:
    """Return the highest-severity label across ``alerts``, or empty if
    the list is empty. Unknown severities are treated as the lowest
    rank (so a stray "FOO" never accidentally beats LOW)."""
    if not alerts:
        return ""
    best = ""
    best_rank = 100
    for a in alerts:
        sev = getattr(a, "severity", "") or ""
        rank = _severity_rank(sev)
        if rank < best_rank:
            best_rank = rank
            best = sev
    return best


# ---------------------------------------------------------------------------
# Condition synthesis — mutate inputs to reflect an event's shape
# ---------------------------------------------------------------------------


def _synthesize_freight_data_for_event(event: HistoricalEvent) -> dict:
    """Build a freight_data dict where the event's affected lanes carry a
    severe 7-day rate spike (well above the 8% default rate threshold).

    Pandas-DataFrame output by default; the alert engine's
    ``check_rate_alerts`` consumes DataFrames keyed by route_id.
    """
    try:
        import datetime as _dt
        import pandas as pd
    except Exception:  # pragma: no cover - defensive
        return {}

    # Per-severity rate-spike multiplier. The 7-day move (vals[-1] /
    # vals[-8] - 1) lands above 8% (HIGH) for moderate, above 16%
    # (CRITICAL) for major/severe. This keeps the engine's
    # magnitude-driven severity in sync with the registry's coarse
    # severity, which is what the ±1-band severity check expects.
    spike_mult = {
        "severe":   2.00,
        "major":    1.80,
        "moderate": 1.12,
    }.get((event.severity or "moderate").lower(), 1.20)

    baseline = 2000.0
    today = _dt.date.today()
    dates = [today - _dt.timedelta(days=29 - i) for i in range(30)]

    out: dict = {}
    for rid in (event.affected_routes or []):
        if rid not in ROUTES_BY_ID:
            continue
        # ``check_rate_alerts`` compares vals[-1] against vals[-8] (a
        # 7-day window). The spike must land in the last <8 elements,
        # NOT before — otherwise both endpoints are at the spiked level
        # and the % move reads as 0. Put the spike on the final day
        # only; that's a 1-day jump that the 7-day window still picks
        # up because ref is the unspiked baseline.
        rates = [baseline] * 29 + [baseline * spike_mult]
        out[rid] = pd.DataFrame(
            {"date": dates, "rate_usd_per_feu": rates}
        )
    return out


def _synthesize_port_congestion_for_event(event: HistoricalEvent) -> list:
    """Build a list of duck-typed port objects with elevated congestion
    on every affected route's destination port.

    Each item has ``locode``, ``name``, ``congestion_score`` — enough
    for ``check_congestion_alerts`` which does ``getattr`` lookups.
    """
    severity_to_score = {
        "severe":   0.93,   # CRITICAL band (>= 0.90)
        "major":    0.85,   # HIGH band (>= 0.82)
        "moderate": 0.78,   # MEDIUM band (> 0.75)
    }
    score = severity_to_score.get(
        (event.severity or "moderate").lower(), 0.82
    )

    # ``_FakePort`` is a duck-typed stand-in — the alert engine reads
    # ``congestion_score`` / ``locode`` / ``name`` via getattr, so a
    # plain object with those attributes is sufficient.
    class _FakePort:
        def __init__(self, locode: str, name: str, score: float) -> None:
            self.locode = locode
            self.name = name
            self.congestion_score = score

    out: list = []
    seen: set[str] = set()
    for rid in (event.affected_routes or []):
        route = ROUTES_BY_ID.get(rid)
        if route is None:
            continue
        if route.dest_locode in seen:
            continue
        seen.add(route.dest_locode)
        out.append(_FakePort(
            locode=route.dest_locode,
            name=route.dest_locode,
            score=score,
        ))
    return out


@contextmanager
def _temporarily_elevate_chokepoints(
    chokepoint_keys: list[str],
) -> Iterator[None]:
    """Within the ``with`` block, the named chokepoints register as
    disrupted at CRITICAL. The original values are restored on exit even
    if the block raises. Unknown keys are skipped.

    Mirrors ``processing.disruption_backtest._temporarily_elevate_chokepoints``
    in shape but is duplicated here so a refactor to one doesn't
    accidentally break the other.
    """
    snapshot: dict[str, tuple[str, str, str | None, list]] = {}
    for key in chokepoint_keys or []:
        cp = CHOKEPOINTS.get(key)
        if cp is None:
            continue
        snapshot[key] = (
            cp.current_risk_level,
            cp.current_disruption_type,
            cp.disruption_since,
            list(cp.strategic_alternatives),
        )
        cp.current_risk_level = "CRITICAL"
        cp.current_disruption_type = "ACTIVE_CONFLICT"
        if cp.disruption_since is None:
            cp.disruption_since = "2020-01-01"
        cp.strategic_alternatives = []

    try:
        yield
    finally:
        for key, (lvl, dt, since, alts) in snapshot.items():
            cp = CHOKEPOINTS.get(key)
            if cp is None:                       # pragma: no cover - defensive
                continue
            cp.current_risk_level = lvl
            cp.current_disruption_type = dt
            cp.disruption_since = since
            cp.strategic_alternatives = alts


# ---------------------------------------------------------------------------
# Public — single-event replay
# ---------------------------------------------------------------------------


def replay_event(event: HistoricalEvent) -> ReplayResult:
    """Replay one historical event through the live alert engine.

    Steps:
      1. Look up the event's expected alert kinds + severity (from the
         module-level mappings). An event NOT in the mappings is replayed
         with empty expectations — every fired kind shows up as
         ``unexpected_kinds`` and the result passes vacuously (no
         missing kinds, no severity mismatch).
      2. Synthesise the input bundles (freight DataFrames, port
         congestion list, chokepoint elevation) consistent with the
         event's shape.
      3. Call each ``check_*_alerts`` helper in turn against those
         inputs. The BDI and stock helpers are NOT called — the events
         here are container-shipping events, not dry-bulk or equity
         events, so calling those would inject false positives.
      4. Compare fired alert kinds + severity to expectations.

    NEVER raises. A ``None`` event yields a valid empty
    ``ReplayResult`` with passed=False. An event whose inputs cause an
    internal helper to raise yields a ReplayResult whose
    ``fired_alert_kinds`` reflects only the helpers that succeeded;
    failed helpers degrade silently (logger.debug).
    """
    # Degenerate inputs first.
    if event is None:
        return ReplayResult(
            event_id="",
            event_date="",
            event_label="",
            expected_alert_kinds=[],
            fired_alert_kinds=[],
            missing_kinds=[],
            unexpected_kinds=[],
            expected_severity="",
            fired_severity="",
            severity_match=True,
            passed=False,
        )

    event_id = event.event_id
    expected_kinds = sorted(set(EXPECTED_ALERT_KINDS_BY_EVENT.get(event_id, [])))
    expected_severity = EXPECTED_SEVERITY_BY_EVENT.get(event_id, "")

    # Synthesise inputs.
    try:
        freight = _synthesize_freight_data_for_event(event)
    except Exception as exc:
        logger.debug(f"replay_event[{event_id}]: freight synth failed: {exc}")
        freight = {}
    try:
        port_results = _synthesize_port_congestion_for_event(event)
    except Exception as exc:
        logger.debug(f"replay_event[{event_id}]: port synth failed: {exc}")
        port_results = []

    cp_keys = list(event.affected_chokepoints or [])

    fired_alerts: list = []

    # Run the engine's pure-function checks. Each in its own try/except
    # so a single failure doesn't disable the rest of the replay.
    with _temporarily_elevate_chokepoints(cp_keys):
        try:
            fired_alerts.extend(check_rate_alerts(freight, threshold_pct=8.0))
        except Exception as exc:
            logger.debug(f"replay_event[{event_id}]: rate check failed: {exc}")
        try:
            fired_alerts.extend(check_congestion_alerts(port_results, threshold=0.75))
        except Exception as exc:
            logger.debug(f"replay_event[{event_id}]: congestion check failed: {exc}")
        # BDI macro check — only call when the event's expected kinds
        # explicitly include BDI_MOVE. Otherwise we'd inject a default
        # empty macro_data and the helper would return [], which is
        # fine; but skipping entirely keeps the code path obvious.
        if "BDI_MOVE" in expected_kinds:
            try:
                fired_alerts.extend(check_bdi_alerts({}, threshold_pct=5.0))
            except Exception as exc:
                logger.debug(f"replay_event[{event_id}]: bdi check failed: {exc}")
        # Port-deficit reads platform module state, NOT a passed-in
        # bundle. Only call when the event explicitly expects it,
        # otherwise the live regional equipment status (which is unrelated
        # to this event) would inject false positives.
        if "PORT_DEFICIT" in expected_kinds:
            try:
                fired_alerts.extend(check_port_deficit_alerts())
            except Exception as exc:
                logger.debug(f"replay_event[{event_id}]: port-deficit check failed: {exc}")

    fired_kinds = sorted({getattr(a, "alert_type", "") for a in fired_alerts if getattr(a, "alert_type", "")})
    expected_set = set(expected_kinds)
    fired_set = set(fired_kinds)

    missing_kinds = sorted(expected_set - fired_set)
    unexpected_kinds = sorted(fired_set - expected_set)

    fired_severity = _highest_severity(fired_alerts)
    severity_match = _severity_within_one_band(expected_severity, fired_severity)

    # Pass = no missing kinds AND severity within band. Unexpected kinds
    # surface for review but do NOT fail the event (the platform's job
    # is to fire on documented disruptions; firing on something else as
    # well is acceptable so long as it doesn't drown the operator).
    passed = (not missing_kinds) and severity_match

    return ReplayResult(
        event_id=event_id,
        event_date=event.start_date,
        event_label=event.name,
        expected_alert_kinds=expected_kinds,
        fired_alert_kinds=fired_kinds,
        missing_kinds=missing_kinds,
        unexpected_kinds=unexpected_kinds,
        expected_severity=expected_severity,
        fired_severity=fired_severity,
        severity_match=severity_match,
        passed=passed,
    )


# ---------------------------------------------------------------------------
# Public — multi-event replay
# ---------------------------------------------------------------------------


def replay_all_events(
    *,
    events: list[HistoricalEvent] | None = None,
) -> list[ReplayResult]:
    """Replay every event in the registry.

    Parameters
    ----------
    events:
        Override the registry. ``None`` →
        :data:`data.historical_events.EVENTS`. An empty list returns an
        empty result list.

    Returns
    -------
    list[ReplayResult]
        One ReplayResult per input event, in input order. A single
        event's internal failure does NOT abort the rest of the run.

    NEVER raises.
    """
    event_list = list(events) if events is not None else list(EVENTS)
    results: list[ReplayResult] = []
    for event in event_list:
        try:
            results.append(replay_event(event))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                f"replay_all_events: replay_event raised for "
                f"{getattr(event, 'event_id', '?')}: {exc}"
            )
            # Degrade to a failed-but-valid result so the count check
            # in tests (one result per registered event) still holds.
            results.append(ReplayResult(
                event_id=getattr(event, "event_id", ""),
                event_date=getattr(event, "start_date", ""),
                event_label=getattr(event, "name", ""),
                passed=False,
            ))
    return results


# ---------------------------------------------------------------------------
# Public — summary roll-up
# ---------------------------------------------------------------------------


def summarize_replay(results: list[ReplayResult]) -> dict[str, Any]:
    """Aggregate a list of ReplayResults into a flat summary dict.

    Returns a dict with keys:
      * ``total``: int — number of results
      * ``passed``: int — number with ``passed=True``
      * ``failed``: int — total - passed
      * ``pass_rate``: float in [0, 1] — passed / total (0.0 when empty)
      * ``miss_rate``: float in [0, 1] — fraction of results with any
        missing kinds
      * ``false_positive_rate``: float in [0, 1] — fraction of results
        with any unexpected kinds
      * ``top_missing_kinds``: list of (kind, count) tuples sorted by
        count desc, top 5 — the alert kinds the engine most often fails
        to fire when expected. Empty list when no misses.

    NEVER raises. An empty input yields a fully-zeroed dict.
    """
    if not results:
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "pass_rate": 0.0,
            "miss_rate": 0.0,
            "false_positive_rate": 0.0,
            "top_missing_kinds": [],
        }

    n = len(results)
    passed = sum(1 for r in results if r.passed)
    n_with_missing = sum(1 for r in results if r.missing_kinds)
    n_with_unexpected = sum(1 for r in results if r.unexpected_kinds)

    # Count occurrences of each missing kind across all results.
    miss_counts: dict[str, int] = {}
    for r in results:
        for k in r.missing_kinds:
            miss_counts[k] = miss_counts.get(k, 0) + 1
    top_missing = sorted(
        miss_counts.items(), key=lambda kv: (-kv[1], kv[0])
    )[:5]

    return {
        "total":               n,
        "passed":              passed,
        "failed":              n - passed,
        "pass_rate":           round(passed / n, 4),
        "miss_rate":           round(n_with_missing / n, 4),
        "false_positive_rate": round(n_with_unexpected / n, 4),
        "top_missing_kinds":   top_missing,
    }
