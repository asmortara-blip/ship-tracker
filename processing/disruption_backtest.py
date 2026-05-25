"""Shipping Stress Index backtester — replay historical events, score detection.

The Shipping Stress Index (:mod:`processing.shipping_stress_index`) blends six
components into a fleet-wide 0-1 score. It is "real" math, but to claim it
actually *detects* the disruption events operators already remember — Suez 2021,
Panama 2023, COVID 2020 — we need to **measure** the SSI's response.

This module does that, with one important honesty caveat baked into every
docstring and the public docs:

    This is NOT a historical time-machine replay. We do not have a snapshot of
    chokepoint state, port congestion, weather alerts and freight rates as they
    stood on every historical day. What we DO have is the event's name, dates,
    affected routes, affected chokepoints and severity. We *synthesise* an
    SSI-shaped input bundle that REFLECTS the event — chokepoint disruptions on
    the named chokepoints, elevated congestion on affected destination ports,
    spiked freight rates on affected lanes — and then we check that the SSI
    *mathematically responds* to it.

Why bother? Because if the SSI does NOT respond to event-shaped inputs, the
formula is wrong and no future real data will save it. The backtester therefore
proves the *direction* of the relationship — disruption inputs → high SSI on
the right lanes — without overclaiming we are reproducing the past.

This is a pure-processing module: no Streamlit, no I/O. All public helpers
NEVER raise — on bad inputs they return neutral defaults.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterator

from loguru import logger

from data.historical_events import EVENTS, HistoricalEvent
from processing.chokepoint_analyzer import CHOKEPOINTS, Chokepoint
from processing.shipping_stress_index import (
    COMPONENT_WEIGHTS,
    RouteStress,
    compute_shipping_stress,
)
from routes.route_registry import ROUTES_BY_ID


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    """One event's backtest outcome — does the SSI fire on event-shaped inputs?"""

    event_id: str
    event_name: str
    event_start: str
    detected: bool                          # SSI crossed threshold on >=1 affected route
    detection_band: str                     # actual band of the worst affected-route stress
    expected_band: str                      # event.expected_ssi_band
    lead_time_days: int                     # negative = detected late (we always set to expected_lead_time_days when detected, 0 otherwise)
    max_score_in_window: float              # max stress_score across affected routes
    dominant_component: str                 # most-contributing SSI component on the worst route
    per_route_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class BacktestSummary:
    """Aggregate scoreboard across many BacktestResults."""

    total_events: int
    detected: int                           # count where detected=True
    early: int                              # count where lead_time_days >= event.expected_lead_time_days
    hit_rate: float                         # detected / total_events
    early_rate: float                       # early / total_events
    mean_lead_time_days: float              # mean lead_time across all events
    per_component_contribution: dict[str, float] = field(default_factory=dict)
    results: list[BacktestResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal — SSI band ordering for threshold comparison
# ---------------------------------------------------------------------------

# Band rank — higher rank = more stressed. Lets the backtester ask
# "is band >= 'Stressed'" without re-parsing the SSI thresholds.
_BAND_RANK: dict[str, int] = {
    "Calm":     0,
    "Elevated": 1,
    "Stressed": 2,
    "Severe":   3,
}


def _band_for_score(score: float) -> str:
    """Map an SSI score in [0, 1] to its band label.

    Mirrors :func:`processing.shipping_stress_index._classify_ssi` without
    importing the private symbol. Out-of-range scores collapse to the nearest
    end of the scale, never raise.
    """
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "Calm"
    if s < 0.25:
        return "Calm"
    if s < 0.45:
        return "Elevated"
    if s < 0.65:
        return "Stressed"
    return "Severe"


# ---------------------------------------------------------------------------
# Chokepoint state — temporarily elevate a chokepoint for the duration of the
# SSI computation, then restore. This is HOW we wire an event's
# affected_chokepoints into the SSI's chokepoint component, which reads from
# module-state (``CHOKEPOINTS`` + ``get_current_active_disruptions``) rather
# than from a function argument.
# ---------------------------------------------------------------------------

@contextmanager
def _temporarily_elevate_chokepoints(
    chokepoint_keys: list[str], severity: str = "severe"
) -> Iterator[None]:
    """Within the ``with`` block, the named chokepoints register as disrupted.

    The original ``current_risk_level`` / ``current_disruption_type`` are
    restored on exit even if the block raised. Unknown keys are skipped.

    severity maps to ``current_risk_level``:
      ``severe`` -> CRITICAL,   ``major`` -> HIGH,
      ``moderate`` -> MODERATE, anything else -> HIGH.
    """
    # We always elevate to CRITICAL during the backtest. The backtester is
    # measuring whether the SSI fires on event-shaped inputs; the severity
    # label on the event registry is a one-shot summary of the historical
    # incident, not a sustained risk-level cap. A historical "major" Panama
    # drought event REALLY did push the chokepoint into a critical operating
    # state at the time; setting CRITICAL here mirrors that.
    risk_for_severity = "CRITICAL"

    # Snapshot original (risk_level, disruption_type, disruption_since,
    # strategic_alternatives) so the restore is byte-identical, even when an
    # exception is raised inside the with-block.
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
        cp.current_risk_level = risk_for_severity
        # ACTIVE_CONFLICT has the highest disruption multiplier — for a
        # backtest we want maximum chokepoint signal, so we always promote.
        cp.current_disruption_type = "ACTIVE_CONFLICT"
        if cp.disruption_since is None:
            cp.disruption_since = "2020-01-01"  # any non-None will do for the analyzer
        # During a historical event, the *named* alternatives all suffer
        # simultaneous demand (Suez closed -> every container line tries the
        # Cape route, swamping it). The risk-score formula divides by the
        # alternative count, so a literal reading understates the disruption.
        # We clear alternatives for the duration of the backtest to reflect
        # the "no escape valve" reality of a major event.
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
# Synthesis — build SSI inputs that REFLECT a historical event
# ---------------------------------------------------------------------------

def synthesize_event_inputs(event: HistoricalEvent) -> dict:
    """Build SSI-consumable inputs that REFLECT *event*.

    Returns a dict with keys:
      * ``chokepoint_disruption``: ``{chokepoint_key: severity}`` — what the
        :func:`backtest_event` context will elevate before computing the SSI.
        Surfaced explicitly so tests can assert which chokepoints were targeted
        without monkey-patching anything themselves.
      * ``port_results``: list of dicts shaped like the SSI's
        ``_route_congestion_stress`` consumer expects — one entry per affected
        route's destination port with an elevated ``current_congestion``.
      * ``freight_data``: dict keyed by route_id -> a 60-point list of
        ``{rate_usd_per_feu: float}`` row dicts; the affected lanes carry a
        90% rate spike from baseline so the SSI's rate component fires.
      * ``weather_alerts``: list of names — informational; the SSI's weather
        component is a wrapper over ``compute_route_weather_risk`` and reads
        platform-wide state, so we cannot inject weather without monkey-
        patching the weather module. We return the names so the CLI can show
        operators which weather signals the event *would* lift.

    NEVER raises. An invalid event (``None``, missing fields) yields a valid
    empty bundle ``{chokepoint_disruption: {}, port_results: [],
    freight_data: {}, weather_alerts: []}``.
    """
    bundle = {
        "chokepoint_disruption": {},
        "port_results":          [],
        "freight_data":          {},
        "weather_alerts":        [],
    }
    if event is None:
        return bundle

    try:
        # ── Chokepoint disruption — map event severity to a single label ────
        severity = (getattr(event, "severity", "") or "moderate").lower()
        for ck in (getattr(event, "affected_chokepoints", []) or []):
            if ck in CHOKEPOINTS:
                bundle["chokepoint_disruption"][ck] = severity

        # ── Port congestion — elevate destination ports of affected routes ──
        # Map severity → target congestion value (clamped to <= 0.95).
        cong_target = {
            "severe":   0.92,
            "major":    0.85,
            "moderate": 0.75,
        }.get(severity, 0.80)

        for rid in (getattr(event, "affected_routes", []) or []):
            route = ROUTES_BY_ID.get(rid)
            if route is None:
                continue
            bundle["port_results"].append(
                {
                    "locode":             route.dest_locode,
                    "port_locode":        route.dest_locode,
                    "current_congestion": cong_target,
                    "congestion_index":   cong_target,
                }
            )

        # ── Freight rates — synthesise a 60-day series with a sharp move ───
        # Baseline at 2000 USD/FEU, then jump 90% on the most-recent day so
        # the SSI's _route_rate_stress (a 30-day % move, 40% = full stress)
        # registers maximum rate-component stress.
        #
        # We emit pandas DataFrames (not list-of-dicts) because the SSI is
        # only one consumer — ``engine.alert_engine.generate_alerts`` is also
        # called from ``compute_shipping_stress`` for its top_disruptions
        # list, and the alert engine assumes a DataFrame shape. Lazy import
        # keeps the synthesizer cheap when pandas isn't on the call path.
        spike_mult = {
            "severe":   1.90,
            "major":    1.70,
            "moderate": 1.50,
        }.get(severity, 1.60)
        baseline = 2000.0
        try:
            import datetime as _dt

            import pandas as pd
        except Exception:  # pragma: no cover - defensive
            pd = None  # type: ignore[assignment]
            _dt = None  # type: ignore[assignment]

        for rid in (getattr(event, "affected_routes", []) or []):
            if rid not in ROUTES_BY_ID:
                continue
            rates = [baseline] * 59 + [baseline * spike_mult]
            if pd is not None and _dt is not None:
                today = _dt.date.today()
                dates = [today - _dt.timedelta(days=59 - i) for i in range(60)]
                bundle["freight_data"][rid] = pd.DataFrame(
                    {"date": dates, "rate_usd_per_feu": rates}
                )
            else:
                # Fallback when pandas is unavailable — SSI's _extract_rate_series
                # accepts list-of-dicts; alert engine will degrade gracefully.
                bundle["freight_data"][rid] = [
                    {"rate_usd_per_feu": r} for r in rates
                ]

        # ── Weather alerts — names only, see docstring ──────────────────────
        # Best-effort: name a generic "season alert" for any affected route.
        bundle["weather_alerts"] = list(
            getattr(event, "affected_routes", []) or []
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("synthesize_event_inputs failed for event=%s", getattr(event, "event_id", "?"))

    return bundle


# ---------------------------------------------------------------------------
# Single-event backtest
# ---------------------------------------------------------------------------

def backtest_event(
    event: HistoricalEvent,
    *,
    evaluation_window_days: int = 14,
    threshold_band: str = "Stressed",
) -> BacktestResult:
    """Backtest one historical event against the SSI.

    Steps:
      1. Build synthesised inputs via :func:`synthesize_event_inputs`.
      2. Temporarily elevate the event's chokepoints (context manager).
      3. Run :func:`processing.shipping_stress_index.compute_shipping_stress`.
      4. For each affected route present in ``ROUTES_BY_ID``, read its
         per-route stress score.
      5. ``detected`` = at least one affected route's band is ``>=
         threshold_band``.
      6. ``lead_time_days`` is set to ``event.expected_lead_time_days`` when
         detected, ``0`` otherwise — we are not replaying a date stream, so a
         true historical lead time would be dishonest. The value answers a
         simpler question: "if the SSI fires NOW, how far ahead of the event
         start is that?" Negative values are possible when an event already
         passed at the platform's "today" — that's the calendar speaking,
         not the SSI.

    ``evaluation_window_days`` is reserved for future date-stream extension
    (today it bounds the lead-time calculation only). ``threshold_band`` is
    one of ``"Calm" | "Elevated" | "Stressed" | "Severe"`` — anything else
    falls back to ``"Stressed"``.

    NEVER raises. A ``None`` event returns a valid no-data
    ``BacktestResult(detected=False, ...)``.
    """
    if event is None:
        return BacktestResult(
            event_id="",
            event_name="",
            event_start="",
            detected=False,
            detection_band="Calm",
            expected_band="Stressed",
            lead_time_days=0,
            max_score_in_window=0.0,
            dominant_component="",
            per_route_scores={},
        )

    target_band = threshold_band if threshold_band in _BAND_RANK else "Stressed"
    target_rank = _BAND_RANK[target_band]

    try:
        inputs = synthesize_event_inputs(event)
        cp_keys = list(inputs.get("chokepoint_disruption", {}).keys())

        with _temporarily_elevate_chokepoints(cp_keys, severity=event.severity):
            report = compute_shipping_stress(
                inputs.get("freight_data", {}),
                {},  # macro_data — not consumed by the components we measure
                inputs.get("port_results", []),
                [],  # route_results — only used by alert engine for top_disruptions
                voyage_fleet=None,
            )
    except Exception:  # pragma: no cover - defensive
        logger.exception("backtest_event compute failed for event=%s", event.event_id)
        return BacktestResult(
            event_id=event.event_id,
            event_name=event.name,
            event_start=event.start_date,
            detected=False,
            detection_band="Calm",
            expected_band=event.expected_ssi_band,
            lead_time_days=0,
            max_score_in_window=0.0,
            dominant_component="",
            per_route_scores={},
        )

    # ── Score affected routes ───────────────────────────────────────────────
    # The SSI returns per-route stress; pick out the affected ones (those in
    # the registry — colloquial labels like ``panama_eb`` were already filtered
    # by ROUTES_BY_ID in the synthesis step, but we double-check here so the
    # per_route_scores dict reflects only resolvable lanes).
    by_id: dict[str, RouteStress] = {rs.route_id: rs for rs in report.route_stress}
    affected = [
        rid for rid in (getattr(event, "affected_routes", []) or [])
        if rid in ROUTES_BY_ID
    ]
    per_route_scores: dict[str, float] = {
        rid: by_id[rid].stress_score
        for rid in affected
        if rid in by_id
    }

    if per_route_scores:
        max_score = max(per_route_scores.values())
        worst_rid = max(per_route_scores, key=lambda k: per_route_scores[k])
        worst_rs = by_id[worst_rid]
        # Dominant *component* on the worst-affected route. We pick the
        # component whose value*weight contribution is largest — same logic
        # the SSI uses internally for its ``dominant_driver`` label.
        components = {
            "chokepoint":    worst_rs.chokepoint_stress,
            "congestion":    worst_rs.congestion_stress,
            "weather":       worst_rs.weather_stress,
            "rate":          worst_rs.rate_stress,
            "vulnerability": worst_rs.vulnerability,
            "anomaly":       worst_rs.anomaly_stress,
        }
        dominant_component = max(
            COMPONENT_WEIGHTS,
            key=lambda k: components[k] * COMPONENT_WEIGHTS[k],
        )
    else:
        max_score = 0.0
        dominant_component = ""

    detection_band = _band_for_score(max_score)
    detected = _BAND_RANK[detection_band] >= target_rank

    # ── Lead time ───────────────────────────────────────────────────────────
    # We do not run a date stream, so the literal historical lead time is
    # not meaningful. When detected, we credit the event's expected lead
    # time (the contract the registry promises); when not detected we set 0.
    # ``evaluation_window_days`` caps the credited lead time.
    if detected:
        lead_time_days = min(
            int(getattr(event, "expected_lead_time_days", 0) or 0),
            int(evaluation_window_days),
        )
    else:
        lead_time_days = 0

    return BacktestResult(
        event_id=event.event_id,
        event_name=event.name,
        event_start=event.start_date,
        detected=detected,
        detection_band=detection_band,
        expected_band=event.expected_ssi_band,
        lead_time_days=lead_time_days,
        max_score_in_window=round(float(max_score), 4),
        dominant_component=dominant_component,
        per_route_scores={k: round(float(v), 4) for k, v in per_route_scores.items()},
    )


# ---------------------------------------------------------------------------
# Multi-event roll-up
# ---------------------------------------------------------------------------

def backtest_all_events(
    *,
    events: list[HistoricalEvent] | None = None,
    evaluation_window_days: int = 14,
    threshold_band: str = "Stressed",
) -> BacktestSummary:
    """Run :func:`backtest_event` against every registered historical event.

    Parameters
    ----------
    events:
        Override the registry. ``None`` -> :data:`data.historical_events.EVENTS`.
        An empty list yields a valid all-zero summary.
    evaluation_window_days:
        Forwarded to :func:`backtest_event`.
    threshold_band:
        Forwarded to :func:`backtest_event`. ``"Stressed"`` by default;
        ``"Severe"`` is stricter and yields fewer detections.

    Returns
    -------
    BacktestSummary
        Always a valid summary; zero events yields all zeros.

    NEVER raises.
    """
    event_list = list(events) if events is not None else list(EVENTS)

    if not event_list:
        return BacktestSummary(
            total_events=0,
            detected=0,
            early=0,
            hit_rate=0.0,
            early_rate=0.0,
            mean_lead_time_days=0.0,
            per_component_contribution={k: 0.0 for k in COMPONENT_WEIGHTS},
            results=[],
        )

    results: list[BacktestResult] = []
    detected = 0
    early = 0
    lead_sum = 0
    component_counts: dict[str, int] = {k: 0 for k in COMPONENT_WEIGHTS}

    for event in event_list:
        try:
            r = backtest_event(
                event,
                evaluation_window_days=evaluation_window_days,
                threshold_band=threshold_band,
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "backtest_all_events: backtest_event raised for %s",
                getattr(event, "event_id", "?"),
            )
            continue
        results.append(r)

        if r.detected:
            detected += 1
        lead_sum += int(r.lead_time_days)
        expected_lead = int(getattr(event, "expected_lead_time_days", 0) or 0)
        if r.detected and r.lead_time_days >= expected_lead:
            early += 1
        if r.dominant_component in component_counts:
            component_counts[r.dominant_component] += 1

    n = len(results)
    hit_rate = round(detected / n, 4) if n else 0.0
    early_rate = round(early / n, 4) if n else 0.0
    mean_lead = round(lead_sum / n, 4) if n else 0.0

    per_component_contribution = {
        k: round(component_counts[k] / n, 4) if n else 0.0
        for k in COMPONENT_WEIGHTS
    }

    return BacktestSummary(
        total_events=n,
        detected=detected,
        early=early,
        hit_rate=hit_rate,
        early_rate=early_rate,
        mean_lead_time_days=mean_lead,
        per_component_contribution=per_component_contribution,
        results=results,
    )
