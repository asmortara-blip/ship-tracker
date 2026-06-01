"""company_supply_risk_backtest.py — rank-stability check for the per-ticker roll-up.

The per-ticker score from ``processing.company_supply_risk`` is built on
the same port_supply_lines chain that ``port_supply_lines_backtest``
already perturbs. The interesting question here is one level up: when
the cargo mix wiggles, does the **top-N riskiest tickers** set stay
the same? An alert that flips its top names on every cargo-mix nudge
isn't a useful daily watchlist.

The check mirrors ``processing.port_supply_lines_backtest``:

  baseline_top_n  = set of top-N tickers from the unperturbed build
  perturbed_top_n = set of top-N tickers from a noise-injected build
  stability       = |baseline ∩ perturbed| / N        (in [0, 1])

Runs ``n_runs`` perturbations, averages the Jaccard. Deterministic —
same seed → same scorecard.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from data.quality import DataSource


__all__ = [
    "RiskRankScorecard",
    "RiskStabilityReport",
    "validate_risk_score_stability",
    "COMPANY_SUPPLY_RISK_BACKTEST_SOURCE",
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class RiskRankScorecard:
    """One perturbation run's top-N tickers + its Jaccard vs baseline."""

    run_index: int
    perturbed_top_n: list[str]
    jaccard_vs_baseline: float    # in [0, 1]


@dataclass
class RiskStabilityReport:
    scorecards: list[RiskRankScorecard] = field(default_factory=list)
    baseline_top_n: list[str] = field(default_factory=list)
    n_runs: int = 0
    noise: float = 0.0
    top_n: int = 5
    overall_mean_stability: float = 1.0
    overall_min_stability: float = 1.0
    stable: bool = True            # True when mean >= stability_threshold
    source: DataSource | None = None
    summary: str = ""


COMPANY_SUPPLY_RISK_BACKTEST_SOURCE = DataSource.modeled(
    "Company Supply Risk Backtest",
    notes=(
        "Rank-stability check for the per-ticker supply-risk roll-up. "
        "Perturbs the per-route cargo mix by ±noise, re-runs the score, "
        "and measures how consistent the top-N riskiest tickers set "
        "stays across runs."
    ),
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _jaccard_topn(a: list[str], b: list[str]) -> float:
    """Top-N Jaccard: ``|A ∩ B| / max(|A|, |B|)``. Both lists assumed
    pre-truncated to the same N. Empty inputs → 1.0 (vacuously stable)."""
    set_a, set_b = set(a), set(b)
    k = max(len(set_a), len(set_b))
    if k == 0:
        return 1.0
    return len(set_a & set_b) / k


def _baseline_top_n(top_n: int) -> list[str]:
    """Top-N tickers from the unperturbed roll-up."""
    from processing.company_supply_risk import compute_company_supply_risk
    scores = compute_company_supply_risk()
    return [s.ticker for s in scores[:top_n]]


def _perturbed_top_n(*, top_n: int, noise: float, seed: int) -> list[str]:
    """Top-N tickers from a noise-perturbed cargo-mix roll-up.

    Same monkey-patch pattern as ``port_supply_lines_backtest`` —
    multiplies each cargo weight by ``(1 + uniform(-noise, +noise))`` and
    re-clamps to ``[0, 1]``. The patch is scoped to a single build.
    """
    import processing.cargo_analyzer as ca
    from processing.cargo_analyzer import get_route_cargo_mix as real_mix
    from processing.company_supply_risk import compute_company_supply_risk

    rng = random.Random(seed)

    def _perturbed_mix(route_id: str, trade_data: dict) -> dict[str, float]:
        base = real_mix(route_id, trade_data) or {}
        out: dict[str, float] = {}
        for hs, weight in base.items():
            factor = 1.0 + rng.uniform(-noise, noise)
            out[hs] = max(0.0, min(1.0, float(weight) * factor))
        return out

    original = ca.get_route_cargo_mix
    ca.get_route_cargo_mix = _perturbed_mix
    try:
        scores = compute_company_supply_risk()
    finally:
        ca.get_route_cargo_mix = original

    return [s.ticker for s in scores[:top_n]]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_risk_score_stability(
    *,
    n_runs: int = 8,
    noise: float = 0.15,
    top_n: int = 5,
    seed: int = 20260526,
    stability_threshold: float = 0.65,
) -> RiskStabilityReport:
    """Run the per-ticker rank-stability backtest.

    Parameters
    ----------
    n_runs:
        Number of perturbation runs to average over.
    noise:
        Multiplicative noise band on each cargo weight. ``0.15`` = ±15%.
        ``0.0`` reproduces the baseline → stability collapses to 1.0
        every run (used as a sanity-check seed in tests).
    top_n:
        Size of the riskiest-tickers set to compare. Defaults to 5.
    seed:
        Master seed; each run uses ``seed + i`` so per-run draws don't
        collide.
    stability_threshold:
        Mean Jaccard above which the roll-up ``stable`` flag is True.
        Defaults to 0.65 — at least ~⅔ of the top-N survives the noise
        on average.

    Returns
    -------
    RiskStabilityReport
    """
    n_runs = max(1, int(n_runs))
    top_n = max(1, int(top_n))
    noise = max(0.0, float(noise))

    baseline = _baseline_top_n(top_n)

    scorecards: list[RiskRankScorecard] = []
    for i in range(n_runs):
        perturbed = _perturbed_top_n(top_n=top_n, noise=noise, seed=seed + i)
        scorecards.append(RiskRankScorecard(
            run_index=i,
            perturbed_top_n=perturbed,
            jaccard_vs_baseline=round(_jaccard_topn(baseline, perturbed), 4),
        ))

    if scorecards:
        overall_mean = sum(sc.jaccard_vs_baseline for sc in scorecards) / len(scorecards)
        overall_min = min(sc.jaccard_vs_baseline for sc in scorecards)
    else:
        overall_mean = 1.0
        overall_min = 1.0
    stable = overall_mean >= stability_threshold

    summary = (
        f"Top-{top_n} ticker rank-stability across {n_runs} run(s) at "
        f"±{noise * 100:.0f}% cargo-mix noise: mean = "
        f"{overall_mean * 100:.1f}%, worst run = {overall_min * 100:.1f}%; "
        f"stable (≥ {stability_threshold * 100:.0f}%): {stable}."
        if baseline else "No tickers in supply-line registry."
    )

    return RiskStabilityReport(
        scorecards=scorecards,
        baseline_top_n=baseline,
        n_runs=n_runs,
        noise=noise,
        top_n=top_n,
        overall_mean_stability=round(overall_mean, 4),
        overall_min_stability=round(overall_min, 4),
        stable=stable,
        source=COMPANY_SUPPLY_RISK_BACKTEST_SOURCE,
        summary=summary,
    )
