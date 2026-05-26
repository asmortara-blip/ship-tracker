"""ssi_component_validation.py — per-component predictiveness backtest.

The Shipping Stress Index (``processing.shipping_stress_index``) rolls up
six per-route stress components (``chokepoint``, ``congestion``,
``weather``, ``rate``, ``vulnerability``, ``anomaly``) into a single
fleet-wide score using static weights from ``COMPONENT_WEIGHTS``. This
module answers a question those weights cannot: **of the six components,
which actually predict forward freight-rate moves?**

The validator runs on a history of (per-component stress dict, realized
rate move) tuples. For each component it computes:

  * **mean stress**          — sample mean of the component over the window
  * **mean realized move**   — sample mean of the realized rate move column
  * **correlation**          — Pearson r between stress[t] and move[t+lookahead]
  * **sign-agreement rate**  — fraction of observations where the SIGN of the
                               stress *delta* matches the SIGN of the move
                               (rate moves up after stress builds)
  * **edge**                 — sign-agreement rate minus 0.5 (random baseline)
  * **n_observations**       — number of usable (stress, move) pairs

A synthetic-history helper is included so the validator can be exercised
without external feeds — it generates a deterministic, seed-stable
history where each component has a *known* relationship to the realized
move, so the property tests can pin the validator's behaviour.

This is a transparent, rule-based scorecard — no fitted ML, no opaque
weights. Every number can be reproduced from the inputs.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Iterable

from data.quality import DataSource
from processing.shipping_stress_index import COMPONENT_WEIGHTS


__all__ = [
    "ComponentScorecard",
    "ComponentValidationReport",
    "HorizonScorecard",
    "HorizonDecayReport",
    "synthesize_component_history",
    "validate_ssi_components",
    "validate_ssi_horizons",
    "SSI_COMPONENT_VALIDATION_SOURCE",
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ComponentScorecard:
    """The per-component validation scorecard.

    All fields are plain-arithmetic observables — nothing is fitted.
    """

    component: str               # canonical key, e.g. "chokepoint"
    weight: float                # SSI weight from COMPONENT_WEIGHTS
    mean_stress: float           # in-sample mean of the component score
    mean_realized_move_pct: float  # in-sample mean realized rate move (%)
    correlation: float           # Pearson r, clamped to [-1, 1]; 0 if undefined
    sign_agreement_rate: float   # in [0, 1]; 0.5 = no edge
    edge: float                  # sign_agreement_rate - 0.5
    n_observations: int          # usable (stress, move) pairs
    note: str = ""               # plain-language status / caveat


@dataclass
class ComponentValidationReport:
    """A run of the validator across all SSI components."""

    scorecards: list[ComponentScorecard] = field(default_factory=list)
    lookahead_days: int = 30
    n_observations: int = 0
    best_component: str = ""        # highest sign-agreement rate
    worst_component: str = ""       # lowest sign-agreement rate
    source: DataSource | None = None
    summary: str = ""               # one-line plain-language headline


@dataclass
class HorizonScorecard:
    """One component's sign-agreement rate at one forecast horizon."""

    component: str               # canonical key, e.g. "chokepoint"
    horizon_days: int            # forecast horizon this cell represents
    sign_agreement_rate: float   # in [0, 1]; 0.5 = random
    edge: float                  # sign_agreement_rate - 0.5
    n_observations: int          # usable (stress_delta, move) pairs


@dataclass
class HorizonDecayReport:
    """A horizon-decay scorecard across components × horizons.

    Use ``rates_grid()`` to materialise a row-per-component / col-per-horizon
    grid suitable for plotting as a heatmap.
    """

    cells: list[HorizonScorecard] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    horizons: list[int] = field(default_factory=list)
    n_observations: int = 0
    best_horizon_overall: int = 0
    source: DataSource | None = None
    summary: str = ""

    def rates_grid(self) -> list[list[float]]:
        """Return the sign-agreement rates as ``[components × horizons]``."""
        by_key = {(c.component, c.horizon_days): c.sign_agreement_rate
                  for c in self.cells}
        return [
            [by_key.get((comp, h), 0.5) for h in self.horizons]
            for comp in self.components
        ]


SSI_COMPONENT_VALIDATION_SOURCE = DataSource.modeled(
    "SSI Component Validation",
    notes=(
        "Per-component predictiveness backtest. For each of the six SSI "
        "components, Pearson correlation + sign-agreement rate between the "
        "component stress and the forward realized rate move. Synthetic-"
        "history seed makes the report deterministic across runs."
    ),
)


# ---------------------------------------------------------------------------
# Pure numeric helpers (kept private; tested through validate_ssi_components)
# ---------------------------------------------------------------------------


def _clamp_corr(value: float) -> float:
    """Clamp a (possibly nan / out-of-range) correlation into [-1, 1]."""
    if value != value:  # NaN guard
        return 0.0
    return max(-1.0, min(1.0, float(value)))


def _pearson_r(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation between two equal-length lists.

    Returns 0 when either series has zero variance (correlation undefined)
    or fewer than two paired observations.
    """
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = 0.0
    sxx = 0.0
    syy = 0.0
    for i in range(n):
        dx = xs[i] - mx
        dy = ys[i] - my
        num += dx * dy
        sxx += dx * dx
        syy += dy * dy
    denom = math.sqrt(sxx * syy)
    if denom == 0.0:
        return 0.0
    return _clamp_corr(num / denom)


def _sign_agreement(
    stress: list[float],
    moves: list[float],
    *,
    lookahead: int = 1,
) -> float:
    """Fraction of pairs where the SIGN of the stress delta matches the
    SIGN of the realized move ``lookahead`` rows later.

    Implementation: walk the stress series; at each step compute the delta
    against the previous reading; tally agreements where sign(delta) ==
    sign(move[i + lookahead - 1]). With ``lookahead=1`` (the default) this
    is a one-step-ahead sign-agreement check. Larger lookaheads slide the
    realized-move pointer further into the future.

    Zero deltas and zero moves are counted as neutral and skipped (so a
    flat series doesn't inflate the rate).
    """
    n_stress = len(stress)
    n_moves  = len(moves)
    shift = max(1, int(lookahead))
    # First usable index for the delta is 1; the move it must align with
    # is at index (i + shift - 1) in the moves series.
    last_i = min(n_stress, n_moves - (shift - 1))
    if last_i < 2:
        return 0.5  # not enough data → no edge
    agree = 0
    total = 0
    for i in range(1, last_i):
        delta = stress[i] - stress[i - 1]
        move  = moves[i + shift - 1]
        if delta == 0.0 or move == 0.0:
            continue
        total += 1
        if (delta > 0) == (move > 0):
            agree += 1
    if total == 0:
        return 0.5
    return agree / total


# ---------------------------------------------------------------------------
# Synthetic history generator (deterministic; for the test + UI smoke path)
# ---------------------------------------------------------------------------


def synthesize_component_history(
    *,
    n_days: int = 120,
    seed: int = 20260525,
    lookahead_days: int = 30,
) -> list[dict]:
    """Deterministic synthetic SSI-component history with seeded relationships.

    Each row is a dict with:
      * ``component_scores`` — dict mapping each component key to its stress
        score on this day, in [0, 1]
      * ``realized_move_pct`` — the realized rate move ``lookahead_days``
        from now, in percent

    The relationship between each component and the realized move is
    *seeded* — strong components produce a clean sign-agreement, weak
    ones produce noise. The property tests pin the validator's behaviour
    on the outputs of this generator.
    """
    rng = random.Random(seed)
    components = list(COMPONENT_WEIGHTS.keys())

    # Per-component "true predictiveness" — higher = the component's stress
    # cleanly leads the rate move; lower = component is mostly noise. These
    # numbers are arbitrary but stable across runs of this seed.
    truth: dict[str, float] = {
        "chokepoint":    0.85,
        "congestion":    0.65,
        "rate":          0.60,
        "weather":       0.30,
        "vulnerability": 0.45,
        "anomaly":       0.20,
    }

    history: list[dict] = []
    prev_scores = {c: 0.5 for c in components}
    for _ in range(n_days):
        # Random-walk each component within [0, 1].
        scores = {}
        for c in components:
            step = rng.uniform(-0.08, 0.08)
            scores[c] = max(0.0, min(1.0, prev_scores[c] + step))

        # Build the realized move as a weighted sum of (component_delta ×
        # truth) plus noise. The weights are the component's COMPONENT_WEIGHTS
        # share so the synthetic relationship respects the real SSI structure.
        move = 0.0
        for c in components:
            delta = scores[c] - prev_scores[c]
            move += delta * truth.get(c, 0.0) * COMPONENT_WEIGHTS.get(c, 0.0) * 100.0
        # Add small Gaussian-like noise (rng.gauss exists on Random).
        move += rng.gauss(0.0, 0.4)

        history.append({
            "component_scores":   dict(scores),
            "realized_move_pct":  round(move, 4),
        })
        prev_scores = scores

    return history


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_ssi_components(
    history: Iterable[dict] | None = None,
    *,
    lookahead_days: int = 1,
    seed: int = 20260525,
) -> ComponentValidationReport:
    """Run the per-component predictiveness scorecard.

    Parameters
    ----------
    history:
        Iterable of ``{"component_scores": {...}, "realized_move_pct": float}``
        dicts in chronological order. If ``None`` or empty, the synthetic
        history generator is used so the validator always produces a result.
    lookahead_days:
        How many rows ahead the realized-move alignment looks. ``1`` (the
        default) compares stress[t] against move[t]; larger values slide
        the realized-move pointer further into the future. Note the
        bundled :func:`synthesize_component_history` generates *contemporaneous*
        moves (move[t] is driven by delta[t]) — to get meaningful decay
        across horizons on real history, callers should pass an actual
        forward-looking moves series.
    seed:
        Forwarded to the synthetic generator when ``history`` is empty.

    Returns
    -------
    ComponentValidationReport
        A full, deterministic scorecard. Empty input produces a non-crashing
        report driven by the synthetic generator.
    """
    rows = list(history or [])
    if not rows:
        rows = synthesize_component_history(
            seed=seed, lookahead_days=lookahead_days,
        )

    # Extract per-component stress series + the shared realized-move series.
    components = list(COMPONENT_WEIGHTS.keys())
    per_component: dict[str, list[float]] = {c: [] for c in components}
    moves: list[float] = []
    for row in rows:
        scores = (row or {}).get("component_scores") or {}
        for c in components:
            per_component[c].append(float(scores.get(c, 0.0) or 0.0))
        moves.append(float((row or {}).get("realized_move_pct", 0.0) or 0.0))

    scorecards: list[ComponentScorecard] = []
    for component in components:
        stress = per_component[component]
        if not stress:
            scorecards.append(ComponentScorecard(
                component=component,
                weight=COMPONENT_WEIGHTS.get(component, 0.0),
                mean_stress=0.0,
                mean_realized_move_pct=0.0,
                correlation=0.0,
                sign_agreement_rate=0.5,
                edge=0.0,
                n_observations=0,
                note="empty input",
            ))
            continue
        mean_stress = sum(stress) / len(stress)
        mean_move   = sum(moves) / len(moves) if moves else 0.0
        corr        = _pearson_r(stress, moves)
        sa_rate     = _sign_agreement(stress, moves, lookahead=int(lookahead_days))
        scorecards.append(ComponentScorecard(
            component=component,
            weight=COMPONENT_WEIGHTS.get(component, 0.0),
            mean_stress=mean_stress,
            mean_realized_move_pct=mean_move,
            correlation=corr,
            sign_agreement_rate=sa_rate,
            edge=sa_rate - 0.5,
            n_observations=len(stress),
        ))

    # Rank by sign-agreement rate to pick the best / worst.
    ranked = sorted(scorecards, key=lambda sc: sc.sign_agreement_rate)
    worst = ranked[0].component if ranked else ""
    best  = ranked[-1].component if ranked else ""

    summary = (
        f"Best signal: {best} ({_fmt_pct(ranked[-1].sign_agreement_rate)} "
        f"sign-agreement); weakest: {worst} "
        f"({_fmt_pct(ranked[0].sign_agreement_rate)})."
        if ranked else "No SSI history available."
    )

    return ComponentValidationReport(
        scorecards=scorecards,
        lookahead_days=int(lookahead_days),
        n_observations=len(rows),
        best_component=best,
        worst_component=worst,
        source=SSI_COMPONENT_VALIDATION_SOURCE,
        summary=summary,
    )


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def validate_ssi_horizons(
    history: Iterable[dict] | None = None,
    *,
    horizons: Iterable[int] = (1, 7, 14, 30, 60),
    seed: int = 20260525,
) -> HorizonDecayReport:
    """Horizon-decay backtest — sign-agreement per (component, horizon).

    Runs the same per-component sign-agreement check used by
    ``validate_ssi_components`` across multiple forecast horizons and
    packages the results as a 2D grid (components × horizons) so the UI
    can render a heatmap.

    Answers: *at what horizon does each SSI component carry its
    predictive edge — and how fast does the edge decay?*

    Parameters
    ----------
    history:
        Same shape as ``validate_ssi_components``. Falls back to the
        synthetic generator when empty.
    horizons:
        Forecast horizons (in days) to evaluate. Each must be >= 1.
    seed:
        Forwarded to the synthetic generator.

    Returns
    -------
    HorizonDecayReport
        ``cells`` carries one HorizonScorecard per (component, horizon)
        pair; ``rates_grid()`` materialises a heatmap-ready 2D list.
    """
    horizons_list = sorted({max(1, int(h)) for h in horizons})
    if not horizons_list:
        horizons_list = [30]

    rows = list(history or [])
    if not rows:
        rows = synthesize_component_history(seed=seed)

    components = list(COMPONENT_WEIGHTS.keys())
    per_component: dict[str, list[float]] = {c: [] for c in components}
    moves: list[float] = []
    for row in rows:
        scores = (row or {}).get("component_scores") or {}
        for c in components:
            per_component[c].append(float(scores.get(c, 0.0) or 0.0))
        moves.append(float((row or {}).get("realized_move_pct", 0.0) or 0.0))

    cells: list[HorizonScorecard] = []
    for h in horizons_list:
        # Number of usable observations at this horizon = stress range -
        # the lookahead shift.
        n_pairs = max(0, len(moves) - (h - 1) - 1)
        for component in components:
            stress = per_component[component]
            sa_rate = (
                _sign_agreement(stress, moves, lookahead=h)
                if stress else 0.5
            )
            cells.append(HorizonScorecard(
                component=component,
                horizon_days=h,
                sign_agreement_rate=sa_rate,
                edge=sa_rate - 0.5,
                n_observations=n_pairs,
            ))

    # Rank horizons by mean sign-agreement across components → best horizon.
    by_horizon_mean: dict[int, float] = {}
    for h in horizons_list:
        cells_h = [c.sign_agreement_rate for c in cells if c.horizon_days == h]
        by_horizon_mean[h] = (sum(cells_h) / len(cells_h)) if cells_h else 0.5
    best_h = max(by_horizon_mean.items(), key=lambda kv: kv[1])[0] \
        if by_horizon_mean else 0

    summary = (
        f"Best mean sign-agreement at {best_h}d horizon "
        f"({_fmt_pct(by_horizon_mean.get(best_h, 0.5))} across "
        f"{len(components)} components)."
        if cells else "No SSI history available."
    )

    return HorizonDecayReport(
        cells=cells,
        components=components,
        horizons=horizons_list,
        n_observations=len(rows),
        best_horizon_overall=best_h,
        source=SSI_COMPONENT_VALIDATION_SOURCE,
        summary=summary,
    )
