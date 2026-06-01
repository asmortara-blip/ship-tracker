"""schi_component_validation.py — per-dimension predictiveness backtest.

Symmetric companion to ``processing.ssi_component_validation`` — the
Supply Chain Health Index (``engine.supply_chain_health``) rolls up six
per-dimension health scores using static weights from ``_WEIGHTS``. This
module answers the same three operator-facing questions, but for SCHI:

  1. **Which dimensions actually predict forward market outcomes?**
     (``validate_schi_components`` → per-dimension sign-agreement + r)
  2. **At what horizon does the predictive edge sit?**
     (``validate_schi_horizons`` → dimensions × horizons grid)
  3. **Are any two dimensions secretly double-counting the same signal?**
     (``compute_schi_collinearity`` → pairwise correlation matrix)

The SCHI's six dimensions: ``port_capacity``, ``freight_cost_pressure``,
``macro_environment``, ``chokepoint_risk``, ``inventory_cycle``,
``seasonal_factors``. Weights live in ``engine.supply_chain_health._WEIGHTS``;
the validator re-declares the canonical list so we don't reach across
the public-API line into a private name. A property test pins that the
two lists stay in sync.

Same dataclass shapes as the SSI validator (``ComponentScorecard``,
``ValidationReport``, …) — intentional so consumers can template on
one and swap the other in. Deterministic, synth-backfilled, no fitted
ML, no opaque weights.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Iterable

from data.quality import DataSource


__all__ = [
    "SCHI_DIMENSIONS",
    "SCHI_DIMENSION_WEIGHTS",
    "REDUNDANCY_THRESHOLD",
    "ComponentScorecard",
    "ValidationReport",
    "HorizonScorecard",
    "HorizonDecayReport",
    "ComponentPair",
    "CollinearityReport",
    "synthesize_dimension_history",
    "validate_schi_components",
    "validate_schi_horizons",
    "compute_schi_collinearity",
    "SCHI_COMPONENT_VALIDATION_SOURCE",
]


# Canonical dimension list + weights. Pinned here rather than imported
# from engine.supply_chain_health._WEIGHTS — that name is private. The
# test_dimension_weights_match_supply_chain_health property test catches
# drift between the two.
SCHI_DIMENSIONS: tuple[str, ...] = (
    "port_capacity",
    "freight_cost_pressure",
    "macro_environment",
    "chokepoint_risk",
    "inventory_cycle",
    "seasonal_factors",
)

SCHI_DIMENSION_WEIGHTS: dict[str, float] = {
    "port_capacity":         0.22,
    "freight_cost_pressure": 0.20,
    "macro_environment":     0.18,
    "chokepoint_risk":       0.15,
    "inventory_cycle":       0.15,
    "seasonal_factors":      0.10,
}

REDUNDANCY_THRESHOLD: float = 0.70


# ---------------------------------------------------------------------------
# Result types (same shape as the SSI validator — kept identical so a
# downstream consumer can template across both)
# ---------------------------------------------------------------------------


@dataclass
class ComponentScorecard:
    component: str
    weight: float
    mean_stress: float
    mean_realized_move_pct: float
    correlation: float
    sign_agreement_rate: float
    edge: float
    n_observations: int
    note: str = ""


@dataclass
class ValidationReport:
    scorecards: list[ComponentScorecard] = field(default_factory=list)
    lookahead_days: int = 1
    n_observations: int = 0
    best_component: str = ""
    worst_component: str = ""
    source: DataSource | None = None
    summary: str = ""


@dataclass
class HorizonScorecard:
    component: str
    horizon_days: int
    sign_agreement_rate: float
    edge: float
    n_observations: int


@dataclass
class HorizonDecayReport:
    cells: list[HorizonScorecard] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    horizons: list[int] = field(default_factory=list)
    n_observations: int = 0
    best_horizon_overall: int = 0
    source: DataSource | None = None
    summary: str = ""

    def rates_grid(self) -> list[list[float]]:
        by_key = {(c.component, c.horizon_days): c.sign_agreement_rate
                  for c in self.cells}
        return [
            [by_key.get((comp, h), 0.5) for h in self.horizons]
            for comp in self.components
        ]


@dataclass
class ComponentPair:
    component_a: str
    component_b: str
    correlation: float
    n_observations: int
    redundant: bool = False


@dataclass
class CollinearityReport:
    components: list[str] = field(default_factory=list)
    pairs: list[ComponentPair] = field(default_factory=list)
    redundant_pairs: list[ComponentPair] = field(default_factory=list)
    n_observations: int = 0
    source: DataSource | None = None
    summary: str = ""

    def corr_matrix(self) -> list[list[float]]:
        n = len(self.components)
        by_pair = {(p.component_a, p.component_b): p.correlation
                   for p in self.pairs}
        for p in self.pairs:
            by_pair[(p.component_b, p.component_a)] = p.correlation
        rows: list[list[float]] = []
        for a in self.components:
            row: list[float] = []
            for b in self.components:
                if a == b:
                    row.append(1.0)
                else:
                    row.append(by_pair.get((a, b), 0.0))
            rows.append(row)
        return rows


SCHI_COMPONENT_VALIDATION_SOURCE = DataSource.modeled(
    "SCHI Component Validation",
    notes=(
        "Per-dimension predictiveness backtest for the Supply Chain "
        "Health Index. Pearson correlation + sign-agreement rate "
        "between dimension stress and the forward realized market move."
    ),
)


# ---------------------------------------------------------------------------
# Internal numeric helpers
# ---------------------------------------------------------------------------


def _clamp_corr(value: float) -> float:
    if value != value:
        return 0.0
    return max(-1.0, min(1.0, float(value)))


def _pearson_r(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sxx = syy = 0.0
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
    stress: list[float], moves: list[float], *, lookahead: int = 1,
) -> float:
    n_stress = len(stress)
    n_moves  = len(moves)
    shift = max(1, int(lookahead))
    last_i = min(n_stress, n_moves - (shift - 1))
    if last_i < 2:
        return 0.5
    agree = total = 0
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
# Synthetic history generator (deterministic; powers the test + UI smoke)
# ---------------------------------------------------------------------------


def synthesize_dimension_history(
    *,
    n_days: int = 120,
    seed: int = 20260525,
    lookahead_days: int = 30,
) -> list[dict]:
    """Deterministic synthetic SCHI-dimension history with seeded predictiveness.

    Each row carries ``dimension_scores`` (per-dimension stress in [0, 1])
    + ``realized_move_pct`` (% move in the SCHI-relevant market measure
    over the next ``lookahead_days``).

    The seeded "truth" coefficients mirror the SSI generator's design —
    port_capacity and freight_cost_pressure are seeded most predictive
    (they're the heaviest-weighted dimensions and the most directly
    market-coupled), while seasonal_factors is seeded weakest.
    """
    rng = random.Random(seed)
    truth: dict[str, float] = {
        "port_capacity":         0.80,
        "freight_cost_pressure": 0.70,
        "macro_environment":     0.55,
        "chokepoint_risk":       0.45,
        "inventory_cycle":       0.35,
        "seasonal_factors":      0.20,
    }

    history: list[dict] = []
    prev_scores = {c: 0.5 for c in SCHI_DIMENSIONS}
    for _ in range(n_days):
        scores = {}
        for c in SCHI_DIMENSIONS:
            step = rng.uniform(-0.08, 0.08)
            scores[c] = max(0.0, min(1.0, prev_scores[c] + step))
        move = 0.0
        for c in SCHI_DIMENSIONS:
            delta = scores[c] - prev_scores[c]
            move += (
                delta
                * truth.get(c, 0.0)
                * SCHI_DIMENSION_WEIGHTS.get(c, 0.0)
                * 100.0
            )
        move += rng.gauss(0.0, 0.4)
        history.append({
            "dimension_scores":  dict(scores),
            "realized_move_pct": round(move, 4),
        })
        prev_scores = scores
    return history


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def validate_schi_components(
    history: Iterable[dict] | None = None,
    *,
    lookahead_days: int = 1,
    seed: int = 20260525,
) -> ValidationReport:
    """Per-dimension predictiveness scorecard for the SCHI.

    See module docstring for design / determinism / synth-backfill notes.
    """
    rows = list(history or [])
    if not rows:
        rows = synthesize_dimension_history(
            seed=seed, lookahead_days=lookahead_days,
        )

    per_component: dict[str, list[float]] = {c: [] for c in SCHI_DIMENSIONS}
    moves: list[float] = []
    for row in rows:
        scores = (row or {}).get("dimension_scores") or {}
        for c in SCHI_DIMENSIONS:
            per_component[c].append(float(scores.get(c, 0.0) or 0.0))
        moves.append(float((row or {}).get("realized_move_pct", 0.0) or 0.0))

    scorecards: list[ComponentScorecard] = []
    for component in SCHI_DIMENSIONS:
        stress = per_component[component]
        if not stress:
            scorecards.append(ComponentScorecard(
                component=component,
                weight=SCHI_DIMENSION_WEIGHTS.get(component, 0.0),
                mean_stress=0.0,
                mean_realized_move_pct=0.0,
                correlation=0.0,
                sign_agreement_rate=0.5,
                edge=0.0,
                n_observations=0,
                note="empty input",
            ))
            continue
        scorecards.append(ComponentScorecard(
            component=component,
            weight=SCHI_DIMENSION_WEIGHTS.get(component, 0.0),
            mean_stress=sum(stress) / len(stress),
            mean_realized_move_pct=(sum(moves) / len(moves)) if moves else 0.0,
            correlation=_pearson_r(stress, moves),
            sign_agreement_rate=_sign_agreement(
                stress, moves, lookahead=int(lookahead_days),
            ),
            edge=_sign_agreement(stress, moves,
                                 lookahead=int(lookahead_days)) - 0.5,
            n_observations=len(stress),
        ))

    ranked = sorted(scorecards, key=lambda sc: sc.sign_agreement_rate)
    worst = ranked[0].component if ranked else ""
    best  = ranked[-1].component if ranked else ""
    summary = (
        f"Best signal: {best} ({_fmt_pct(ranked[-1].sign_agreement_rate)} "
        f"sign-agreement); weakest: {worst} "
        f"({_fmt_pct(ranked[0].sign_agreement_rate)})."
        if ranked else "No SCHI history available."
    )

    return ValidationReport(
        scorecards=scorecards,
        lookahead_days=int(lookahead_days),
        n_observations=len(rows),
        best_component=best,
        worst_component=worst,
        source=SCHI_COMPONENT_VALIDATION_SOURCE,
        summary=summary,
    )


def validate_schi_horizons(
    history: Iterable[dict] | None = None,
    *,
    horizons: Iterable[int] = (1, 7, 14, 30, 60),
    seed: int = 20260525,
) -> HorizonDecayReport:
    """Horizon-decay backtest — sign-agreement per (dimension, horizon)."""
    horizons_list = sorted({max(1, int(h)) for h in horizons})
    if not horizons_list:
        horizons_list = [30]

    rows = list(history or [])
    if not rows:
        rows = synthesize_dimension_history(seed=seed)

    per_component: dict[str, list[float]] = {c: [] for c in SCHI_DIMENSIONS}
    moves: list[float] = []
    for row in rows:
        scores = (row or {}).get("dimension_scores") or {}
        for c in SCHI_DIMENSIONS:
            per_component[c].append(float(scores.get(c, 0.0) or 0.0))
        moves.append(float((row or {}).get("realized_move_pct", 0.0) or 0.0))

    cells: list[HorizonScorecard] = []
    for h in horizons_list:
        n_pairs = max(0, len(moves) - (h - 1) - 1)
        for component in SCHI_DIMENSIONS:
            stress = per_component[component]
            sa = _sign_agreement(stress, moves, lookahead=h) if stress else 0.5
            cells.append(HorizonScorecard(
                component=component,
                horizon_days=h,
                sign_agreement_rate=sa,
                edge=sa - 0.5,
                n_observations=n_pairs,
            ))

    by_h_mean: dict[int, float] = {}
    for h in horizons_list:
        vals = [c.sign_agreement_rate for c in cells if c.horizon_days == h]
        by_h_mean[h] = (sum(vals) / len(vals)) if vals else 0.5
    best_h = max(by_h_mean.items(), key=lambda kv: kv[1])[0] \
        if by_h_mean else 0
    summary = (
        f"Best mean sign-agreement at {best_h}d horizon "
        f"({_fmt_pct(by_h_mean.get(best_h, 0.5))} across "
        f"{len(SCHI_DIMENSIONS)} dimensions)."
        if cells else "No SCHI history available."
    )

    return HorizonDecayReport(
        cells=cells,
        components=list(SCHI_DIMENSIONS),
        horizons=horizons_list,
        n_observations=len(rows),
        best_horizon_overall=best_h,
        source=SCHI_COMPONENT_VALIDATION_SOURCE,
        summary=summary,
    )


def compute_schi_collinearity(
    history: Iterable[dict] | None = None,
    *,
    seed: int = 20260525,
) -> CollinearityReport:
    """Pairwise collinearity scorecard across the SCHI dimensions."""
    rows = list(history or [])
    if not rows:
        rows = synthesize_dimension_history(seed=seed)

    series: dict[str, list[float]] = {c: [] for c in SCHI_DIMENSIONS}
    for row in rows:
        scores = (row or {}).get("dimension_scores") or {}
        for c in SCHI_DIMENSIONS:
            series[c].append(float(scores.get(c, 0.0) or 0.0))

    pairs: list[ComponentPair] = []
    redundant: list[ComponentPair] = []
    n_obs = len(rows)
    components = list(SCHI_DIMENSIONS)
    for i, a in enumerate(components):
        for b in components[i + 1:]:
            r = _pearson_r(series[a], series[b])
            pair = ComponentPair(
                component_a=a,
                component_b=b,
                correlation=r,
                n_observations=n_obs,
                redundant=abs(r) >= REDUNDANCY_THRESHOLD,
            )
            pairs.append(pair)
            if pair.redundant:
                redundant.append(pair)

    if redundant:
        worst = max(redundant, key=lambda p: abs(p.correlation))
        summary = (
            f"{len(redundant)} pair(s) above |r|={REDUNDANCY_THRESHOLD:.2f} "
            f"redundancy threshold; strongest: {worst.component_a} ↔ "
            f"{worst.component_b} at r={worst.correlation:+.2f}."
        )
    else:
        summary = (
            f"No pair above |r|={REDUNDANCY_THRESHOLD:.2f} — dimensions "
            "appear non-redundant on this history."
        )

    return CollinearityReport(
        components=components,
        pairs=pairs,
        redundant_pairs=redundant,
        n_observations=n_obs,
        source=SCHI_COMPONENT_VALIDATION_SOURCE,
        summary=summary,
    )
