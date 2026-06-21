"""
Probability-weighted chokepoint-closure scenario  (rec R258).

Two halves of the closure picture exist in this codebase today and never meet:

  * :mod:`processing.escalation_ladder` computes the forward *state distribution*
    for a chokepoint — in particular ``P(reach CLOSURE within horizon)`` — but
    downstream consumers only ever read the blended ``expected_score``.
  * :func:`processing.chokepoint_analyzer.simulate_chokepoint_closure` computes a
    rich, *unconditional* point estimate of what a closure would do (rate impact,
    trade-at-risk, rerouting cost, extra transit days) — but it is an
    ``IF it closes`` severity with no probability attached, and is wired NOWHERE.

Nobody multiplies the two. A closure that is near-certain and a closure that is a
remote tail both surface the same severity number, which is the wrong thing to
rank, budget, or alert on. What a desk actually wants is the **expected** cost::

    expected_impact  =  P(closure)  ×  conditional_impact_if_closed

This module fuses them. From a chokepoint's current ladder rung it reads
``P(CLOSURE within horizon)`` off the MODELED Markov forward distribution and
multiplies it by the conditional ``simulate_chokepoint_closure`` payload to yield
a probability-weighted EXPECTED-cost view. A calm passage (``P≈0``) collapses to
~zero expected impact even though its conditional severity is non-trivial; a hot
one (Suez at the CLOSURE rung) carries most of its conditional severity through.

────────────────────────────────────────────────────────────────────────────
HONESTY
────────────────────────────────────────────────────────────────────────────
Both inputs are MODELED, so the product is MODELED — and it is stamped
``provenance="modeled"`` carrying the escalation ladder's :data:`PROVENANCE_NOTE`
on every result. ``P(closure)`` comes from the conservative, *published* (not
fitted, not observed) ladder transition matrix; the conditional impact comes from
the published closure-elasticity simulation. This module multiplies the two — it
fabricates no new feed and invents no live signal. An unknown chokepoint yields an
honest ZERO-impact scenario (``p_closure=0.0``, all expected fields 0.0), never a
guess and never an exception.

Pure, deterministic, never raises.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional

from loguru import logger

from processing.escalation_ladder import (
    CLOSURE,
    PROVENANCE_NOTE,
    current_state_for,
    state_distribution,
)


# Default conditional-closure duration (weeks) handed to the simulation when the
# caller does not specify one. The ladder horizon counts ~weekly review steps
# (see escalation_ladder), so a closure that materialises is sized, by default,
# to the same horizon — one weekly step ↔ one closed week. Overridable per call.
_DEFAULT_CLOSURE_WEEKS = 1


@dataclass(frozen=True)
class ClosureScenario:
    """Probability-weighted EXPECTED closure cost for one chokepoint (R258).

    Fuses the MODELED ladder ``P(closure)`` with the conditional
    ``simulate_chokepoint_closure`` severity into an expected-cost view. Every
    ``expected_*`` field is EXACTLY ``p_closure`` times the matching conditional
    field — the defining multiplication identity.

    Attributes
    ----------
    chokepoint_key
        Registry key the scenario was priced for (echoed back even when unknown).
    p_closure
        ``P(reach CLOSURE within horizon)`` read from the MODELED ladder forward
        state distribution at this chokepoint's current rung. In ``[0, 1]``.
    horizon
        Number of forward ladder steps priced for ``p_closure``.
    closure_weeks
        Conditional closure duration (weeks) handed to the simulation.
    current_state
        The ladder rung the chokepoint currently sits at.
    conditional_impact
        The raw ``simulate_chokepoint_closure`` payload — the unconditional
        ``IF it closes`` severity (rate impact %, trade-at-risk %, rerouting cost,
        extra days, routes, feasibility note). Empty for an unknown chokepoint.
    conditional_rate_impact_pct, conditional_trade_impact_pct,
    conditional_extra_days, conditional_rerouting_cost_usd
        The key conditional numbers lifted out of the payload for convenience.
    expected_rate_impact_pct
        ``p_closure × conditional_rate_impact_pct`` — the headline expected cost.
    expected_trade_impact_pct, expected_extra_days, expected_rerouting_cost_usd
        ``p_closure ×`` the matching conditional field.
    provenance
        Always ``"modeled"`` — a product of two modeled layers, not a feed.
    note
        Human-readable modeled-provenance note (the ladder's
        :data:`~processing.escalation_ladder.PROVENANCE_NOTE`).
    """

    chokepoint_key: str
    p_closure: float
    horizon: int
    closure_weeks: int
    current_state: str
    conditional_impact: Dict[str, object] = field(default_factory=dict)
    conditional_rate_impact_pct: float = 0.0
    conditional_trade_impact_pct: float = 0.0
    conditional_extra_days: float = 0.0
    conditional_rerouting_cost_usd: float = 0.0
    expected_rate_impact_pct: float = 0.0
    expected_trade_impact_pct: float = 0.0
    expected_extra_days: float = 0.0
    expected_rerouting_cost_usd: float = 0.0
    provenance: str = "modeled"
    note: str = PROVENANCE_NOTE


def _zero_scenario(chokepoint_key: str, horizon: int, closure_weeks: int,
                   current_state: str = "") -> ClosureScenario:
    """An honest all-zero scenario (unknown key / no closure path)."""
    return ClosureScenario(
        chokepoint_key=str(chokepoint_key),
        p_closure=0.0,
        horizon=int(max(0, int(horizon))),
        closure_weeks=int(closure_weeks),
        current_state=current_state,
        conditional_impact={},
    )


def _as_float(value: object, default: float = 0.0) -> float:
    """Coerce a sim payload field to float, degrading to ``default``."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def expected_closure_impact(
    chokepoint_key: str,
    *,
    horizon: int = 4,
    closure_weeks: Optional[int] = None,
) -> ClosureScenario:
    """Probability-weighted EXPECTED closure cost for one chokepoint (R258).

    Maps ``chokepoint_key`` to its current ladder rung, reads
    ``P(CLOSURE within horizon)`` from the MODELED forward state distribution
    (``state_distribution(state, horizon=horizon)[CLOSURE]``), and multiplies it
    by the conditional :func:`~processing.chokepoint_analyzer.simulate_chokepoint_closure`
    impact to yield a probability-weighted expected-cost :class:`ClosureScenario`.

    Parameters
    ----------
    chokepoint_key
        Registry key (e.g. ``"suez"``). Matched the same way the simulation
        matches — by key or chokepoint name, case-insensitively.
    horizon
        Forward ladder steps over which ``P(closure)`` is accumulated. Clamped
        ``>= 0`` by the ladder. Default 4 (~a monthly review window).
    closure_weeks
        Conditional closure duration handed to the simulation. Defaults to the
        ladder ``horizon`` (one weekly step ↔ one closed week), floored at 1.

    Returns
    -------
    ClosureScenario
        With ``expected_* == p_closure × conditional_*`` exactly, and provenance
        stamped ``"modeled"``. An unknown chokepoint (or any internal failure)
        degrades to an honest ZERO-impact scenario — never raises.
    """
    weeks = int(closure_weeks) if closure_weeks is not None else int(
        max(0, int(horizon))
    )
    weeks = max(_DEFAULT_CLOSURE_WEEKS, weeks)

    try:
        # Lazy import to avoid an import cycle (chokepoint_analyzer ↔ ladder).
        from processing.chokepoint_analyzer import (
            CHOKEPOINTS,
            simulate_chokepoint_closure,
        )

        cp = CHOKEPOINTS.get(chokepoint_key)
        if cp is None:
            # Honest zero for an unknown key — no guessed severity.
            return _zero_scenario(chokepoint_key, horizon, weeks)

        state = current_state_for(cp)
        dist = state_distribution(state, horizon=horizon)
        p_closure = float(dist.get(CLOSURE, 0.0))
        # Defensive clamp — the distribution is already in-range. Round ONCE to
        # the stored value so the multiplication identity holds against the
        # p_closure the caller actually reads back (expected == p_closure × cond).
        p_closure = round(min(1.0, max(0.0, p_closure)), 6)

        impact = simulate_chokepoint_closure(chokepoint_key, weeks)
        if not isinstance(impact, dict) or "error" in impact:
            return _zero_scenario(chokepoint_key, horizon, weeks, state)

        cond_rate = _as_float(impact.get("rate_impact_pct"))
        cond_trade = _as_float(impact.get("global_trade_impact_pct"))
        cond_days = _as_float(impact.get("extra_days_if_closed"))
        cond_reroute = _as_float(impact.get("rerouting_cost_total_usd"))

        return ClosureScenario(
            chokepoint_key=str(chokepoint_key),
            p_closure=p_closure,
            horizon=int(max(0, int(horizon))),
            closure_weeks=weeks,
            current_state=state,
            conditional_impact=dict(impact),
            conditional_rate_impact_pct=cond_rate,
            conditional_trade_impact_pct=cond_trade,
            conditional_extra_days=cond_days,
            conditional_rerouting_cost_usd=cond_reroute,
            # Derived from the SAME stored p_closure → identity is exact (mod float).
            expected_rate_impact_pct=p_closure * cond_rate,
            expected_trade_impact_pct=p_closure * cond_trade,
            expected_extra_days=p_closure * cond_days,
            expected_rerouting_cost_usd=p_closure * cond_reroute,
            provenance="modeled",
            note=PROVENANCE_NOTE,
        )
    except Exception:  # pragma: no cover - defensive; must never raise
        logger.debug("expected_closure_impact: degrading to zero scenario")
        return _zero_scenario(chokepoint_key, horizon, weeks)


def expected_closure_impacts(
    registry: Optional[Mapping[str, object]] = None,
    *,
    horizon: int = 4,
    closure_weeks: Optional[int] = None,
) -> Dict[str, ClosureScenario]:
    """Probability-weighted expected closure cost for every chokepoint.

    ``registry`` defaults to ``chokepoint_analyzer.CHOKEPOINTS`` (imported lazily
    to avoid an import cycle). Returns ``{key: ClosureScenario}``. An unknown /
    empty registry yields an empty dict. Never raises.
    """
    if registry is None:
        try:
            from processing.chokepoint_analyzer import CHOKEPOINTS
            registry = CHOKEPOINTS
        except Exception:  # pragma: no cover - defensive
            logger.debug("expected_closure_impacts: registry import failed")
            return {}
    out: Dict[str, ClosureScenario] = {}
    for key in dict(registry):
        out[key] = expected_closure_impact(
            key, horizon=horizon, closure_weeks=closure_weeks
        )
    return out
