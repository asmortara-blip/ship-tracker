"""port_supply_lines_backtest.py — rank-stability check for the supply-line join.

The supply-line joiner in ``processing.port_supply_lines`` computes per-port
company exposure as a chain of products: ``route_share × cargo_weight ×
company_weight``. Each input has its own uncertainty band — cargo mix
shifts with seasonality, company weights reflect quarterly trade
disclosures, route shares are approximations.

This module validates a load-bearing property of that chain: **does the
top-K exposed-companies list per port stay stable under small
perturbations to the cargo mix?** If it does, the methodology is
robust — an operator's daily view doesn't reshuffle from noise. If it
doesn't, the chain is fragile and downstream alerts will be too.

The check is a rank-Jaccard:

  baseline_top_k(port_i)  = set of top-K tickers from the unperturbed build
  perturbed_top_k(port_i) = set of top-K tickers from a noise-injected build
  stability(port_i)       = |baseline ∩ perturbed| / K   (in [0, 1])

Runs ``n_runs`` perturbations and reports per-port mean stability + an
overall ``stable`` roll-up. Deterministic — same seed → same scorecard.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable

from data.quality import DataSource


__all__ = [
    "PortStabilityScorecard",
    "StabilityReport",
    "validate_supply_chain_stability",
    "PORT_SUPPLY_LINES_BACKTEST_SOURCE",
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class PortStabilityScorecard:
    """One port's rank-stability across the perturbation runs."""

    locode: str
    port_name: str
    baseline_top_k: list[str]    # top-K tickers from the unperturbed build
    mean_stability: float        # mean Jaccard across runs; in [0, 1]
    min_stability: float         # worst-case run; in [0, 1]
    n_runs: int


@dataclass
class StabilityReport:
    scorecards: list[PortStabilityScorecard] = field(default_factory=list)
    n_runs: int = 0
    noise: float = 0.0
    top_k: int = 5
    overall_mean_stability: float = 1.0
    overall_min_stability: float = 1.0
    stable: bool = True          # roll-up: True when mean >= 0.65
    source: DataSource | None = None
    summary: str = ""


PORT_SUPPLY_LINES_BACKTEST_SOURCE = DataSource.modeled(
    "Port Supply Lines Backtest",
    notes=(
        "Rank-stability check for the port supply-line join. Perturbs the "
        "per-route cargo mix by ±noise, re-runs the join, and measures "
        "how consistent the top-K exposed-companies set stays per port."
    ),
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _jaccard_topk(a: list[str], b: list[str]) -> float:
    """Top-K Jaccard: |A ∩ B| / max(K, 1). Both lists are assumed
    pre-truncated to the same K. Empty inputs return 1.0 (vacuously
    stable — no companies to disagree about)."""
    set_a, set_b = set(a), set(b)
    k = max(len(set_a), len(set_b))
    if k == 0:
        return 1.0
    return len(set_a & set_b) / k


def _baseline_topk_by_port(top_k: int) -> dict[str, list[str]]:
    """Run the unperturbed join once and return ``{locode: [top-K tickers]}``."""
    from processing.port_supply_lines import build_port_supply_chains
    chains = build_port_supply_chains(top_n_companies=top_k)
    return {
        c.port.locode: [ce.ticker for ce in c.exposed_companies[:top_k]]
        for c in chains
    }


def _perturbed_topk_by_port(
    *, top_k: int, noise: float, seed: int,
) -> dict[str, list[str]]:
    """Run the join with a noise-perturbed cargo-mix layer.

    Implementation: monkey-patch ``processing.cargo_analyzer.get_route_cargo_mix``
    inside ``processing.port_supply_lines`` for the duration of one build,
    multiplying each cargo weight by ``(1 + uniform(-noise, +noise))``
    and re-clamping to ``[0, 1]``. The patch is local — no global state
    pollution beyond the duration of this function.
    """
    import processing.port_supply_lines as psl
    from processing.cargo_analyzer import get_route_cargo_mix as real_mix

    rng = random.Random(seed)

    def _perturbed_mix(route_id: str, trade_data: dict) -> dict[str, float]:
        base = real_mix(route_id, trade_data) or {}
        out: dict[str, float] = {}
        for hs, weight in base.items():
            factor = 1.0 + rng.uniform(-noise, noise)
            out[hs] = max(0.0, min(1.0, float(weight) * factor))
        return out

    # The supply-lines module imports get_route_cargo_mix lazily inside
    # build_port_supply_chains, so we patch the cargo_analyzer module
    # directly — the import inside the joiner sees our patched name.
    import processing.cargo_analyzer as ca
    original = ca.get_route_cargo_mix
    ca.get_route_cargo_mix = _perturbed_mix
    try:
        chains = psl.build_port_supply_chains(top_n_companies=top_k)
    finally:
        ca.get_route_cargo_mix = original

    return {
        c.port.locode: [ce.ticker for ce in c.exposed_companies[:top_k]]
        for c in chains
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_supply_chain_stability(
    *,
    n_runs: int = 8,
    noise: float = 0.15,
    top_k: int = 5,
    seed: int = 20260525,
    stability_threshold: float = 0.65,
) -> StabilityReport:
    """Run the rank-stability backtest.

    Parameters
    ----------
    n_runs:
        Number of perturbation runs to average over.
    noise:
        Multiplicative noise band applied to each cargo weight.
        ``0.15`` = ±15%. Setting to ``0.0`` produces a perfect-stability
        score (1.0 per port) — useful as a sanity-check seed.
    top_k:
        Size of the per-port top exposure list to compare. Defaults to 5
        (matches the operator-facing 5-ticker section of the alert body).
    seed:
        Master seed; each run uses ``seed + i`` so they don't collide.
    stability_threshold:
        Mean stability above which the roll-up ``stable`` flag is True.
        Defaults to 0.65 (Jaccard ≥ 0.65 means at least ~⅔ of the
        top-K survives the noise on average).

    Returns
    -------
    StabilityReport
    """
    n_runs = max(1, int(n_runs))
    top_k = max(1, int(top_k))
    noise = max(0.0, float(noise))

    baseline = _baseline_topk_by_port(top_k=top_k)

    # Per-port stability accumulators
    per_port_runs: dict[str, list[float]] = {
        locode: [] for locode in baseline.keys()
    }

    for i in range(n_runs):
        perturbed = _perturbed_topk_by_port(
            top_k=top_k, noise=noise, seed=seed + i,
        )
        for locode, baseline_topk in baseline.items():
            pert_topk = perturbed.get(locode, [])
            stability = _jaccard_topk(baseline_topk, pert_topk)
            per_port_runs[locode].append(stability)

    # Per-port summary
    scorecards: list[PortStabilityScorecard] = []
    # Map locode → port name for the report
    try:
        from ports.port_registry import PORTS
        port_name_map = {p.locode: p.name for p in PORTS}
    except Exception:
        port_name_map = {}

    for locode, runs in per_port_runs.items():
        if runs:
            mean_s = sum(runs) / len(runs)
            min_s = min(runs)
        else:
            mean_s = 1.0
            min_s = 1.0
        scorecards.append(PortStabilityScorecard(
            locode=locode,
            port_name=port_name_map.get(locode, locode),
            baseline_top_k=baseline[locode],
            mean_stability=round(mean_s, 4),
            min_stability=round(min_s, 4),
            n_runs=n_runs,
        ))

    # Overall roll-up — sort weakest-first so the report leads with the
    # ports most at risk of reshuffling under noise.
    scorecards.sort(key=lambda s: s.mean_stability)
    if scorecards:
        overall_mean = sum(s.mean_stability for s in scorecards) / len(scorecards)
        overall_min = min(s.min_stability for s in scorecards)
    else:
        overall_mean = 1.0
        overall_min = 1.0
    stable = overall_mean >= stability_threshold

    summary = (
        f"Rank-stability across {len(scorecards)} ports × "
        f"{n_runs} run(s) at ±{noise * 100:.0f}% cargo-mix noise: "
        f"overall mean = {overall_mean * 100:.1f}%, "
        f"worst per-port = {overall_min * 100:.1f}%; "
        f"stable (≥ {stability_threshold * 100:.0f}%): {stable}."
        if scorecards else "No ports in supply-line registry."
    )

    return StabilityReport(
        scorecards=scorecards,
        n_runs=n_runs,
        noise=noise,
        top_k=top_k,
        overall_mean_stability=round(overall_mean, 4),
        overall_min_stability=round(overall_min, 4),
        stable=stable,
        source=PORT_SUPPLY_LINES_BACKTEST_SOURCE,
        summary=summary,
    )
