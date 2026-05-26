"""freight_volatility_backtest.py — regime + mean-reversion predictiveness.

The ``processing.freight_volatility`` module classifies each route into
a regime (BREAKOUT / TRENDING_UP / TRENDING_DOWN / RANGING) and a
mean-reversion signal (OVERBOUGHT / OVERSOLD / NEUTRAL) per snapshot.
This module asks the question the live classifier cannot:

  1. **Do TRENDING_UP regimes actually continue trending?**
     (positive mean forward return → momentum works)
  2. **Do OVERBOUGHT readings actually mean-revert?**
     (negative mean forward return → reversion works)

The scorecard reports a per-class mean forward return + a directional
hit rate (in-favour: positive for "should rise", negative for "should
fall", NEUTRAL pinned to 0.5). Plus two roll-up flags:

  * ``momentum_works``       — TRENDING_UP has positive mean fwd return
                               AND TRENDING_DOWN has negative
  * ``mean_reversion_works`` — OVERSOLD has positive mean fwd return
                               AND OVERBOUGHT has negative

A deterministic synthetic generator with tunable ``momentum_strength``
and ``reversion_strength`` knobs powers the tests — flipping either
knob from "perfect" to "noise" must visibly flip the corresponding
flag, which is the load-bearing property test.

Transparent rule-based scorecard — no fitted ML, no opaque weights.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable

from data.quality import DataSource


__all__ = [
    "REGIMES",
    "MEAN_REVERSION_SIGNALS",
    "RegimeScorecard",
    "MeanReversionScorecard",
    "FreightVolatilityBacktestReport",
    "synthesize_regime_history",
    "backtest_freight_volatility",
    "FREIGHT_VOLATILITY_BACKTEST_SOURCE",
]


REGIMES: tuple[str, ...] = (
    "TRENDING_UP",
    "TRENDING_DOWN",
    "BREAKOUT",
    "RANGING",
)
MEAN_REVERSION_SIGNALS: tuple[str, ...] = (
    "OVERSOLD",
    "NEUTRAL",
    "OVERBOUGHT",
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class RegimeScorecard:
    """Per-regime scorecard for the freight volatility classifier."""

    regime: str
    n_observations: int
    mean_forward_return: float
    directional_hit_rate: float   # in [0, 1]; 0.5 for ambiguous regimes
    edge_vs_baseline: float       # directional_hit_rate - 0.5


@dataclass
class MeanReversionScorecard:
    """Per-mean-reversion-signal scorecard."""

    signal: str
    n_observations: int
    mean_forward_return: float
    directional_hit_rate: float   # in [0, 1]
    edge_vs_baseline: float


@dataclass
class FreightVolatilityBacktestReport:
    regimes: list[RegimeScorecard] = field(default_factory=list)
    mean_reversion: list[MeanReversionScorecard] = field(default_factory=list)
    n_observations: int = 0
    momentum_works: bool = False
    mean_reversion_works: bool = False
    source: DataSource | None = None
    summary: str = ""


FREIGHT_VOLATILITY_BACKTEST_SOURCE = DataSource.modeled(
    "Freight Volatility Backtest",
    notes=(
        "Per-regime + per-mean-reversion-signal predictiveness scorecard "
        "for processing.freight_volatility. Mean forward return + "
        "directional hit rate per class; momentum_works and "
        "mean_reversion_works roll-up flags."
    ),
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _regime_hit_rate(regime: str, returns: list[float]) -> float:
    """Fraction of returns that are *in the regime's favour*.

    Conventions:
      * TRENDING_UP   — should keep rising → positive return is a hit
      * TRENDING_DOWN — should keep falling → negative return is a hit
      * BREAKOUT      — directionless (volatility expansion) → 0.5
      * RANGING       — should stay near current → small |return| is a hit
                        (in-favour if |return| < 0.01 = 1pp band)
    """
    if not returns:
        return 0.5
    upper = regime.upper()
    if upper == "TRENDING_UP":
        hits = sum(1 for r in returns if r > 0)
        skipped = sum(1 for r in returns if r == 0)
        usable = len(returns) - skipped
        return (hits / usable) if usable > 0 else 0.5
    if upper == "TRENDING_DOWN":
        hits = sum(1 for r in returns if r < 0)
        skipped = sum(1 for r in returns if r == 0)
        usable = len(returns) - skipped
        return (hits / usable) if usable > 0 else 0.5
    if upper == "RANGING":
        hits = sum(1 for r in returns if abs(r) < 0.01)
        return hits / len(returns)
    # BREAKOUT carries no directional claim — vol expansion can resolve up
    # or down. By convention return 0.5 just like NEUTRAL.
    return 0.5


def _reversion_hit_rate(signal: str, returns: list[float]) -> float:
    """Fraction of returns in the mean-reversion signal's favour.

    Conventions:
      * OVERSOLD   — should mean-revert UP → positive return is a hit
      * OVERBOUGHT — should mean-revert DOWN → negative return is a hit
      * NEUTRAL    — no directional claim → 0.5
    """
    if not returns:
        return 0.5
    upper = signal.upper()
    if upper == "OVERSOLD":
        hits = sum(1 for r in returns if r > 0)
        skipped = sum(1 for r in returns if r == 0)
        usable = len(returns) - skipped
        return (hits / usable) if usable > 0 else 0.5
    if upper == "OVERBOUGHT":
        hits = sum(1 for r in returns if r < 0)
        skipped = sum(1 for r in returns if r == 0)
        usable = len(returns) - skipped
        return (hits / usable) if usable > 0 else 0.5
    return 0.5


# ---------------------------------------------------------------------------
# Synthetic history generator
# ---------------------------------------------------------------------------


def synthesize_regime_history(
    *,
    n_periods: int = 80,
    n_routes: int = 5,
    momentum_strength: float = 0.85,
    reversion_strength: float = 0.85,
    seed: int = 20260525,
) -> list[dict]:
    """Deterministic synthetic regime history.

    Each row is a dict with:
      * ``route_id``                — synthetic route key
      * ``regime``                  — one of REGIMES (uniformly sampled)
      * ``mean_reversion_signal``   — one of MEAN_REVERSION_SIGNALS
      * ``realized_forward_return`` — signed forward return that materialised

    ``momentum_strength`` ∈ [0, 1] controls how cleanly TRENDING_* regimes
    keep going in their stated direction. ``reversion_strength`` controls
    how cleanly OVERSOLD / OVERBOUGHT signals revert. Both at 1.0 = perfect
    classifier; both at 0.0 = pure noise.
    """
    rng = random.Random(seed)
    m_q = max(0.0, min(1.0, float(momentum_strength)))
    r_q = max(0.0, min(1.0, float(reversion_strength)))

    rows: list[dict] = []
    for r_idx in range(n_routes):
        for _ in range(n_periods):
            regime = rng.choice(REGIMES)
            signal = rng.choice(MEAN_REVERSION_SIGNALS)

            # Base noise — the part of the return unrelated to the classifier
            noise = rng.gauss(0.0, 0.02)

            # Momentum contribution per regime
            mom_contrib = 0.0
            if regime == "TRENDING_UP":
                mom_contrib = m_q * 0.03  # +3% drift when momentum works
            elif regime == "TRENDING_DOWN":
                mom_contrib = -m_q * 0.03
            elif regime == "RANGING":
                # Smaller magnitude noise to model "range-bound"
                noise *= (1.0 - 0.5 * m_q)
            # BREAKOUT — pass through; classifier doesn't claim direction

            # Reversion contribution per signal
            rev_contrib = 0.0
            if signal == "OVERSOLD":
                rev_contrib = r_q * 0.03  # reverts UP
            elif signal == "OVERBOUGHT":
                rev_contrib = -r_q * 0.03  # reverts DOWN

            realized = mom_contrib + rev_contrib + noise
            rows.append({
                "route_id":                f"ROUTE_{r_idx}",
                "regime":                  regime,
                "mean_reversion_signal":   signal,
                "realized_forward_return": round(realized, 6),
            })
    return rows


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def backtest_freight_volatility(
    history: Iterable[dict] | None = None,
    *,
    seed: int = 20260525,
    momentum_strength: float = 0.85,
    reversion_strength: float = 0.85,
) -> FreightVolatilityBacktestReport:
    """Score the freight-volatility classifier against forward returns."""
    rows = list(history or [])
    if not rows:
        rows = synthesize_regime_history(
            seed=seed,
            momentum_strength=momentum_strength,
            reversion_strength=reversion_strength,
        )

    # Bucket returns by regime
    by_regime: dict[str, list[float]] = {r: [] for r in REGIMES}
    by_signal: dict[str, list[float]] = {s: [] for s in MEAN_REVERSION_SIGNALS}
    for row in rows:
        ret = float((row or {}).get("realized_forward_return", 0.0) or 0.0)
        regime = str((row or {}).get("regime", "") or "")
        signal = str((row or {}).get("mean_reversion_signal", "") or "")
        if regime in by_regime:
            by_regime[regime].append(ret)
        if signal in by_signal:
            by_signal[signal].append(ret)

    regime_cards: list[RegimeScorecard] = []
    for regime in REGIMES:
        returns = by_regime[regime]
        mean_ret = (sum(returns) / len(returns)) if returns else 0.0
        hit = _regime_hit_rate(regime, returns)
        regime_cards.append(RegimeScorecard(
            regime=regime,
            n_observations=len(returns),
            mean_forward_return=mean_ret,
            directional_hit_rate=hit,
            edge_vs_baseline=hit - 0.5,
        ))

    rev_cards: list[MeanReversionScorecard] = []
    for signal in MEAN_REVERSION_SIGNALS:
        returns = by_signal[signal]
        mean_ret = (sum(returns) / len(returns)) if returns else 0.0
        hit = _reversion_hit_rate(signal, returns)
        rev_cards.append(MeanReversionScorecard(
            signal=signal,
            n_observations=len(returns),
            mean_forward_return=mean_ret,
            directional_hit_rate=hit,
            edge_vs_baseline=hit - 0.5,
        ))

    # Roll-up flags
    by_regime_card = {sc.regime: sc for sc in regime_cards}
    momentum_works = (
        by_regime_card["TRENDING_UP"].mean_forward_return > 0
        and by_regime_card["TRENDING_DOWN"].mean_forward_return < 0
    )
    by_sig_card = {sc.signal: sc for sc in rev_cards}
    mean_reversion_works = (
        by_sig_card["OVERSOLD"].mean_forward_return > 0
        and by_sig_card["OVERBOUGHT"].mean_forward_return < 0
    )

    summary = (
        f"Across {len(rows)} obs: TRENDING_UP mean = "
        f"{by_regime_card['TRENDING_UP'].mean_forward_return * 100:+.2f}% · "
        f"TRENDING_DOWN mean = "
        f"{by_regime_card['TRENDING_DOWN'].mean_forward_return * 100:+.2f}% · "
        f"momentum_works: {momentum_works} · "
        f"mean_reversion_works: {mean_reversion_works}."
        if rows else "No freight-volatility history available."
    )

    return FreightVolatilityBacktestReport(
        regimes=regime_cards,
        mean_reversion=rev_cards,
        n_observations=len(rows),
        momentum_works=momentum_works,
        mean_reversion_works=mean_reversion_works,
        source=FREIGHT_VOLATILITY_BACKTEST_SOURCE,
        summary=summary,
    )
