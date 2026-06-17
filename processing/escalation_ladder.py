"""
Probabilistic conflict-escalation ladder per chokepoint  (rec R034).

Risk in ``chokepoint_analyzer`` is *binary* today: a chokepoint is at some
discrete ``current_risk_level`` (LOW / MODERATE / HIGH / CRITICAL) and either
disrupted or not. But markets do not price a binary state — they price the
probability **path**. A chokepoint sitting at TENSION with a plausible road to
CLOSURE is worth more risk premium than a calm LOW passage, *even before* a
closure happens, because the expected severity over the next step(s) is higher.

This module overlays a small, fully-specified **Markov ladder** onto each
chokepoint:

    DE_ESCALATING  →  TENSION  →  INCIDENT  →  PARTIAL  →  CLOSURE

with a per-state transition-probability matrix. From a chokepoint's current
ladder state we roll the chain forward ``horizon`` steps and compute a
probability-weighted **expected risk score**::

    expected = Σ_s'  P(reach state s' within horizon)  ×  severity(s')

so a chokepoint at TENSION whose chain has a real (if small) path to CLOSURE
scores ABOVE its current deterministic severity — the model prices the tail.

────────────────────────────────────────────────────────────────────────────
HONESTY  (this is the whole point of R034)
────────────────────────────────────────────────────────────────────────────
The transition probabilities below are a **MODELED layer**, not a fitted or
observed feed. They are conservative, published constants chosen so that:

  * escalation is *slow* (most mass stays put or de-escalates each step),
  * a state always has a non-trivial probability of cooling off,
  * the chain is "sticky" (closures persist, calm persists),

and they are documented inline. They are NOT derived from any historical
transition count, news feed, or market price — calling this a fitted hazard
model would be a lie. The ladder **refines** the existing deterministic
chokepoint risk *probabilistically*; it does **not** fabricate a data feed and
it never invents a live signal. Its provenance is stamped ``"modeled"`` on every
result so downstream consumers can label it honestly.

The ladder composes *on top of* the R007/R014 live overlay: that overlay first
escalates ``current_risk_level`` from REAL signal, and the ladder then prices
the forward escalation path off whatever (baseline or live-escalated) level the
chokepoint currently sits at. The two never fight — the ladder reads the level,
it does not mutate the registry.

Pure, deterministic, never raises.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

from loguru import logger


# ---------------------------------------------------------------------------
# Ladder states  (ordered low → high severity)
# ---------------------------------------------------------------------------
#
# The five rungs of the escalation ladder, in increasing order of disruption.
# These are the canonical state labels; ``LADDER_STATES`` pins their order so
# the transition matrix rows/cols and the severity vector stay aligned.

DE_ESCALATING = "DE_ESCALATING"   # tension easing / returning to normal flow
TENSION = "TENSION"               # elevated geopolitical / diplomatic friction
INCIDENT = "INCIDENT"             # a discrete event (attack, seizure, grounding)
PARTIAL = "PARTIAL"               # partial closure / heavy rerouting / draft cut
CLOSURE = "CLOSURE"               # effective full closure of the passage

LADDER_STATES: Tuple[str, ...] = (
    DE_ESCALATING,
    TENSION,
    INCIDENT,
    PARTIAL,
    CLOSURE,
)

_STATE_INDEX: Dict[str, int] = {s: i for i, s in enumerate(LADDER_STATES)}


# ---------------------------------------------------------------------------
# Severity vector  (modeled, in [0, 1] — aligned to the deterministic base)
# ---------------------------------------------------------------------------
#
# severity(state) maps each ladder rung to a [0, 1] severity, deliberately on
# the SAME scale as ``chokepoint_analyzer._RISK_SCORE`` so the expected score is
# directly comparable to (and blendable with) the deterministic base:
#
#     _RISK_SCORE = {CRITICAL: 1.0, HIGH: 0.75, MODERATE: 0.45, LOW: 0.10}
#
# CLOSURE == 1.0 (a CRITICAL-equivalent realised outcome); DE_ESCALATING is just
# above the LOW floor (calm but not zero — a chokepoint is never risk-free).
# These are MODELED constants, not observed.

_SEVERITY: Dict[str, float] = {
    DE_ESCALATING: 0.12,
    TENSION:       0.40,
    INCIDENT:      0.62,
    PARTIAL:       0.82,
    CLOSURE:       1.00,
}


def severity(state: str) -> float:
    """Modeled [0, 1] severity for a ladder *state* (unknown → DE_ESCALATING)."""
    return _SEVERITY.get(state, _SEVERITY[DE_ESCALATING])


# ---------------------------------------------------------------------------
# MODELED transition-probability matrix  (single step)
# ---------------------------------------------------------------------------
#
# TRANSITION[s] gives P(next_state | current_state = s) for ONE step (think:
# one ~weekly review horizon). Each row is a probability distribution over
# ``LADDER_STATES`` and MUST sum to 1 (validated below at import).
#
# Design principles (all MODELED / conservative / published — NOT fitted):
#   * Sticky: the largest mass on every row is "stay put".
#   * Slow escalation: forward moves are small (≤ ~0.18 to the next rung) and
#     skipping rungs upward is rarer still.
#   * Always coolable: every elevated state keeps a real probability of
#     de-escalating (never trapped), so the chain is not absorbing except by
#     design — CLOSURE is "sticky but not absorbing" (closures eventually clear).
#   * Monotone tail: higher rungs put MORE mass on staying high, so a higher
#     starting state yields a higher forward expected score (priced tail).
#
# Rows are ordered DE_ESCALATING, TENSION, INCIDENT, PARTIAL, CLOSURE and the
# columns follow the same order.

TRANSITION: Dict[str, Dict[str, float]] = {
    # DE_ESCALATING: mostly stays calm, small chance of fresh tension.
    DE_ESCALATING: {
        DE_ESCALATING: 0.80,
        TENSION:       0.16,
        INCIDENT:      0.03,
        PARTIAL:       0.01,
        CLOSURE:       0.00,
    },
    # TENSION: elevated friction — can cool, hold, or spark an incident.
    # NOTE: escalation pressure (toward INCIDENT/PARTIAL/CLOSURE) is set so the
    # one-step expectation from TENSION sits AT-OR-ABOVE the TENSION severity —
    # i.e. an elevated chokepoint prices the escalation tail rather than mean-
    # reverting straight back to calm. Still sticky (largest mass on "hold") and
    # still always coolable (a real DE_ESCALATING mass).
    TENSION: {
        DE_ESCALATING: 0.16,
        TENSION:       0.58,
        INCIDENT:      0.18,
        PARTIAL:       0.06,
        CLOSURE:       0.02,
    },
    # INCIDENT: a discrete event — may be contained or harden into rerouting.
    INCIDENT: {
        DE_ESCALATING: 0.10,
        TENSION:       0.20,
        INCIDENT:      0.48,
        PARTIAL:       0.18,
        CLOSURE:       0.04,
    },
    # PARTIAL: partial closure — most likely persists; can tip into closure.
    PARTIAL: {
        DE_ESCALATING: 0.04,
        TENSION:       0.10,
        INCIDENT:      0.16,
        PARTIAL:       0.58,
        CLOSURE:       0.12,
    },
    # CLOSURE: sticky-but-not-absorbing — closures eventually clear to partial.
    CLOSURE: {
        DE_ESCALATING: 0.01,
        TENSION:       0.03,
        INCIDENT:      0.06,
        PARTIAL:       0.20,
        CLOSURE:       0.70,
    },
}

# Provenance stamp travelled on every result so callers label the layer honestly.
PROVENANCE_NOTE: str = (
    "MODELED escalation ladder (R034): conservative, published per-state "
    "transition probabilities — NOT a fitted hazard model and NOT an observed "
    "feed. Refines the deterministic chokepoint risk probabilistically."
)


# ---------------------------------------------------------------------------
# Matrix validity check  (valid Markov: every row sums to 1)
# ---------------------------------------------------------------------------

_ROW_SUM_TOL = 1e-9


def _validate_matrix() -> None:
    """Assert TRANSITION is a valid Markov matrix (rows over LADDER_STATES → 1).

    Run once at import so a hand-edit that breaks the stochastic property fails
    loudly here rather than silently skewing every expected score.
    """
    for state in LADDER_STATES:
        row = TRANSITION.get(state, {})
        # Every column present, no stray keys.
        assert set(row) == set(LADDER_STATES), (
            f"TRANSITION[{state}] columns {set(row)} != {set(LADDER_STATES)}"
        )
        total = sum(row.values())
        assert abs(total - 1.0) <= _ROW_SUM_TOL, (
            f"TRANSITION[{state}] sums to {total!r}, not 1.0"
        )
        assert all(p >= 0.0 for p in row.values()), (
            f"TRANSITION[{state}] has a negative probability"
        )


_validate_matrix()


# ---------------------------------------------------------------------------
# current_state_for — map a chokepoint onto a ladder rung
# ---------------------------------------------------------------------------
#
# A chokepoint carries ``current_risk_level`` (LOW/MODERATE/HIGH/CRITICAL) and a
# ``current_disruption_type`` (NONE/ACTIVE_CONFLICT/WEATHER/DIPLOMATIC/
# CONGESTION). We collapse those onto a single ladder rung. The disruption_type
# refines what an otherwise-ambiguous level means:
#
#   * ACTIVE_CONFLICT at CRITICAL  → CLOSURE   (a critical active conflict IS the
#                                               closure case — Suez/Bab-el-Mandeb)
#   * ACTIVE_CONFLICT at HIGH      → PARTIAL   (conflict forcing heavy rerouting)
#   * ACTIVE_CONFLICT below HIGH   → INCIDENT  (a discrete conflict event)
#   * CRITICAL (any cause)         → PARTIAL   (a critical non-conflict closure-ish)
#   * HIGH                         → INCIDENT
#   * MODERATE                     → TENSION
#   * LOW / NONE                   → DE_ESCALATING
#
# This is a documented MODELED mapping; it is intentionally conservative (it does
# not jump a calm passage up the ladder). Unknown labels fall back to the level
# alone, and unknown level → DE_ESCALATING.

# Level → default rung when disruption_type is not conflict-specific.
_LEVEL_TO_STATE: Dict[str, str] = {
    "CRITICAL": PARTIAL,
    "HIGH":     INCIDENT,
    "MODERATE": TENSION,
    "LOW":      DE_ESCALATING,
}


def current_state_for(chokepoint) -> str:
    """Map a ``Chokepoint`` (or risk-level string) to its current ladder state.

    Accepts either a ``Chokepoint``-like object exposing ``current_risk_level``
    and (optionally) ``current_disruption_type``, OR a bare risk-level string.
    Returns one of :data:`LADDER_STATES`. Never raises — an unrecognised input
    degrades to ``DE_ESCALATING`` (the calm floor).
    """
    try:
        if isinstance(chokepoint, str):
            level, dtype = chokepoint, "NONE"
        else:
            level = getattr(chokepoint, "current_risk_level", None)
            dtype = getattr(chokepoint, "current_disruption_type", "NONE")
        level = (level or "").upper()
        dtype = (dtype or "NONE").upper()

        if dtype == "ACTIVE_CONFLICT":
            if level == "CRITICAL":
                return CLOSURE
            if level == "HIGH":
                return PARTIAL
            # A discrete conflict event without a high standing level.
            return INCIDENT

        return _LEVEL_TO_STATE.get(level, DE_ESCALATING)
    except Exception:  # pragma: no cover - defensive; must never raise
        logger.debug("current_state_for: degrading to DE_ESCALATING")
        return DE_ESCALATING


# ---------------------------------------------------------------------------
# Forward state distribution — roll the chain ``horizon`` steps
# ---------------------------------------------------------------------------


def _step(dist: Dict[str, float]) -> Dict[str, float]:
    """Advance a state distribution one step through TRANSITION."""
    nxt: Dict[str, float] = {s: 0.0 for s in LADDER_STATES}
    for s, p in dist.items():
        if p <= 0.0:
            continue
        row = TRANSITION[s]
        for s2, p2 in row.items():
            nxt[s2] += p * p2
    return nxt


def state_distribution(state: str, *, horizon: int = 1) -> Dict[str, float]:
    """P(state after ``horizon`` steps | start = ``state``) over LADDER_STATES.

    ``horizon`` is clamped to ``>= 0``. ``horizon == 0`` returns the point mass
    on the starting state. An unknown start state is treated as
    ``DE_ESCALATING``. The returned dict sums to 1 (modulo float error).
    """
    start = state if state in _STATE_INDEX else DE_ESCALATING
    h = max(0, int(horizon))
    dist: Dict[str, float] = {s: 0.0 for s in LADDER_STATES}
    dist[start] = 1.0
    for _ in range(h):
        dist = _step(dist)
    return dist


# ---------------------------------------------------------------------------
# expected_risk_score — the probability-weighted forward severity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LadderResult:
    """Result of pricing the forward escalation path for one chokepoint.

    Attributes
    ----------
    expected_score
        Probability-weighted [0, 1] severity over the next ``horizon`` step(s):
        ``Σ_s' P(reach s' within horizon) × severity(s')``.
    current_state
        The ladder rung the chokepoint currently sits at.
    horizon
        Number of forward steps priced.
    distribution
        ``{state: P(state after horizon steps)}`` — the forward state mixture.
    provenance
        Always ``"modeled"`` — this is a modeled layer, not a feed.
    note
        Human-readable modeled-provenance note (:data:`PROVENANCE_NOTE`).
    """

    expected_score: float
    current_state: str
    horizon: int
    distribution: Dict[str, float]
    provenance: str
    note: str


def expected_risk_score(state: str, *, horizon: int = 1) -> LadderResult:
    """Probability-weighted forward risk for a chokepoint at ladder *state*.

    Rolls the MODELED Markov chain ``horizon`` steps from ``state`` and returns
    the severity-weighted expectation plus the forward state distribution and a
    modeled-provenance stamp.

    Because every elevated state retains a real probability mass on the higher
    rungs (and CLOSURE is sticky), a chokepoint at TENSION whose chain can reach
    CLOSURE scores ABOVE its current deterministic severity — the model prices
    the escalation tail. A DE_ESCALATING (calm) chokepoint, by contrast, prices
    only a small forward drift: its expected score sits modestly above the bare
    calm severity (fresh tension is always possible) yet far below every elevated
    rung, so a calm passage never scores like a hot one. The expected score is
    monotonically increasing in the starting rung. Deterministic; never raises.
    """
    start = state if state in _STATE_INDEX else DE_ESCALATING
    h = max(0, int(horizon))
    dist = state_distribution(start, horizon=h)
    expected = sum(dist[s] * _SEVERITY[s] for s in LADDER_STATES)
    # Clamp defensively to [0, 1] (severities are already in-range, but guard
    # against any future severity edit drifting out of bounds).
    expected = min(1.0, max(0.0, expected))
    return LadderResult(
        expected_score=round(expected, 6),
        current_state=start,
        horizon=h,
        distribution=dist,
        provenance="modeled",
        note=PROVENANCE_NOTE,
    )


# ---------------------------------------------------------------------------
# expected_risk_for_chokepoint — convenience: map + price in one call
# ---------------------------------------------------------------------------


def expected_risk_for_chokepoint(chokepoint, *, horizon: int = 1) -> LadderResult:
    """Map a ``Chokepoint`` to its ladder state and price the forward path.

    Thin convenience over :func:`current_state_for` + :func:`expected_risk_score`.
    Never raises.
    """
    return expected_risk_score(current_state_for(chokepoint), horizon=horizon)


# ---------------------------------------------------------------------------
# ladder_expected_scores — batch over a chokepoint registry
# ---------------------------------------------------------------------------


def ladder_expected_scores(
    registry: Optional[Mapping[str, object]] = None,
    *,
    horizon: int = 1,
) -> Dict[str, LadderResult]:
    """Expected forward risk for every chokepoint in *registry*.

    ``registry`` defaults to ``chokepoint_analyzer.CHOKEPOINTS`` (imported
    lazily to avoid an import cycle). Returns ``{key: LadderResult}``. A
    chokepoint that cannot be mapped degrades to a DE_ESCALATING result rather
    than raising. An unknown / empty registry yields an empty dict.
    """
    if registry is None:
        try:
            from processing.chokepoint_analyzer import CHOKEPOINTS
            registry = CHOKEPOINTS
        except Exception:  # pragma: no cover - defensive
            logger.debug("ladder_expected_scores: registry import failed")
            return {}
    out: Dict[str, LadderResult] = {}
    for key, cp in dict(registry).items():
        out[key] = expected_risk_for_chokepoint(cp, horizon=horizon)
    return out


# ---------------------------------------------------------------------------
# escalation_alert_signals — early-warning signals from the forward path
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EscalationSignal:
    """One chokepoint whose MODELED forward escalation warrants an early warning.

    Attributes
    ----------
    key            chokepoint registry key.
    current_state  ladder rung the chokepoint currently sits at.
    forward_score  modeled forward expected severity (the ladder).
    current_score  deterministic composite score (escalation OFF).
    delta          ``forward_score - current_score`` — the priced escalation tail.
    severity       ``"CRITICAL"`` at/above the critical threshold, else ``"HIGH"``.
    horizon        forward steps priced.
    """

    key: str
    current_state: str
    forward_score: float
    current_score: float
    delta: float
    severity: str
    horizon: int


def escalation_alert_signals(
    current_scores: Mapping[str, float],
    ladder_results: Mapping[str, "LadderResult"],
    *,
    forward_floor: float = 0.40,
    min_escalation_delta: float = 0.05,
    critical_threshold: float = 0.70,
) -> list:
    """Early-warning signals from the MODELED forward-escalation ladder.

    A signal fires for a chokepoint that is **all three** of:

      (a) genuinely ELEVATED — its ladder rung is past DE_ESCALATING (not a calm
          passage),
      (b) modeled forward expected severity clears ``forward_floor``, and
      (c) sits at least ``min_escalation_delta`` ABOVE its current deterministic
          composite (``forward - current``).

    In words: the passage is not calm, and the modeled forward path prices real
    escalation the deterministic snapshot hasn't caught yet — the closure-is-
    coming early warning, *before* the closure. A calm (DE_ESCALATING) passage
    never fires, even though its ladder floor sits above its heavily-penalised
    composite. A passage already deterministically hot (delta ≤ 0 — the ladder
    can't add) is left to the existing risk alerts, not double-fired here.

    Pure, deterministic, never raises. Severity is CRITICAL at/above
    ``critical_threshold``, else HIGH. Returned hottest-forward-first.
    """
    out: list = []
    try:
        for key, res in dict(ladder_results).items():
            state = getattr(res, "current_state", DE_ESCALATING)
            if state == DE_ESCALATING:
                continue
            fwd = float(getattr(res, "expected_score", 0.0) or 0.0)
            cur = float(current_scores.get(key, 0.0) or 0.0)
            delta = fwd - cur
            if fwd < forward_floor or delta < min_escalation_delta:
                continue
            sev = "CRITICAL" if fwd >= critical_threshold else "HIGH"
            out.append(EscalationSignal(
                key=str(key),
                current_state=state,
                forward_score=round(fwd, 6),
                current_score=round(cur, 6),
                delta=round(delta, 6),
                severity=sev,
                horizon=int(getattr(res, "horizon", 1)),
            ))
    except Exception:  # pragma: no cover - defensive; must never raise
        logger.debug("escalation_alert_signals: degrading to []")
        return []
    out.sort(key=lambda s: s.forward_score, reverse=True)
    return out
