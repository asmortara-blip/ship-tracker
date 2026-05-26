"""momentum_ranker_backtest.py — signal-class predictiveness backtest.

The ``engine.momentum_ranker`` module classifies entities into five
signal classes — ``STRONG_BUY`` → ``STRONG_SELL`` — based on a composite
of 7d / 30d / 90d momentum. Nothing in the platform currently asks the
question this module answers: **across a history of past signals, did
STRONG_BUY observations actually deliver higher forward returns than
NEUTRAL or SELL observations?**

For each signal class the scorecard reports:

  * **mean_forward_return**          — average signed forward return
  * **directional_hit_rate**         — fraction of windows where the
                                       return was in the signal's favour
                                       (positive for BUY-side, negative
                                       for SELL-side; always 0.5 for
                                       NEUTRAL — there's no directional
                                       claim to score)
  * **edge_vs_baseline**             — directional_hit_rate - 0.5
  * **n_observations**               — windows scored in this class

Plus an aggregate ``monotonic_by_signal`` flag — True when the mean
return is monotone non-decreasing from STRONG_SELL → STRONG_BUY, which
is what a well-calibrated signal ladder should look like.

A synthetic-history generator ships alongside so the backtest can be
exercised without external feeds. The generator accepts a
``signal_quality`` knob: 1.0 = perfect (signals predict the realized
return); 0.0 = noise (signals are random). The property tests use this
to pin behaviour: high-quality history must produce a monotone ladder.

Transparent, rule-based scorecard — no fitted ML, no opaque weights.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable

from data.quality import DataSource


__all__ = [
    "SIGNAL_CLASSES",
    "MomentumSignalScorecard",
    "MomentumBacktestReport",
    "synthesize_momentum_history",
    "backtest_momentum_signals",
    "MOMENTUM_BACKTEST_SOURCE",
]


# The five signal classes from engine.momentum_ranker._signal(), ordered
# weakest → strongest. Pinning the order here so the monotonicity check
# can iterate in a stable direction.
SIGNAL_CLASSES: tuple[str, ...] = (
    "STRONG_SELL",
    "SELL",
    "NEUTRAL",
    "BUY",
    "STRONG_BUY",
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class MomentumSignalScorecard:
    """Per-signal-class backtest scorecard."""

    signal: str                  # one of SIGNAL_CLASSES
    n_observations: int          # rows in this class
    mean_forward_return: float   # signed mean of the realized returns
    directional_hit_rate: float  # in [0, 1]; 0.5 for NEUTRAL by convention
    edge_vs_baseline: float      # directional_hit_rate - 0.5


@dataclass
class MomentumBacktestReport:
    """A run of the momentum-signal backtest across all five classes."""

    scorecards: list[MomentumSignalScorecard] = field(default_factory=list)
    n_observations: int = 0
    monotonic_by_signal: bool = False  # mean return monotone weak → strong
    spread_strong_vs_weak: float = 0.0  # mean(STRONG_BUY) - mean(STRONG_SELL)
    source: DataSource | None = None
    summary: str = ""


MOMENTUM_BACKTEST_SOURCE = DataSource.modeled(
    "Momentum Signal Backtest",
    notes=(
        "Per-signal-class scorecard for the momentum ranker's "
        "STRONG_SELL → STRONG_BUY ladder. Mean forward return + "
        "directional hit rate + monotonicity check across the five "
        "signal classes."
    ),
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _signal_from_composite(composite: float) -> str:
    """Mirror of engine.momentum_ranker._signal() — kept private to keep
    this module standalone (no import cycle when the ranker is missing)."""
    if composite > 0.15:
        return "STRONG_BUY"
    if composite > 0.05:
        return "BUY"
    if composite > -0.05:
        return "NEUTRAL"
    if composite > -0.15:
        return "SELL"
    return "STRONG_SELL"


def _directional_hit_rate(signal: str, returns: list[float]) -> float:
    """Fraction of returns that are *in the signal's favour*.

    * BUY-side signals (``BUY``, ``STRONG_BUY``) score a hit for positive
      forward returns.
    * SELL-side signals (``SELL``, ``STRONG_SELL``) score a hit for
      negative forward returns.
    * NEUTRAL has no directional claim — return 0.5 by convention.

    Zero returns are skipped (no directional content).
    """
    if not returns:
        return 0.5
    upper = signal.upper()
    if upper == "NEUTRAL":
        return 0.5
    is_buy = upper in ("BUY", "STRONG_BUY")
    hits = 0
    total = 0
    for r in returns:
        if r == 0.0:
            continue
        total += 1
        in_favor = (r > 0) if is_buy else (r < 0)
        if in_favor:
            hits += 1
    if total == 0:
        return 0.5
    return hits / total


# ---------------------------------------------------------------------------
# Synthetic history generator
# ---------------------------------------------------------------------------


def synthesize_momentum_history(
    *,
    n_periods: int = 60,
    n_entities: int = 8,
    signal_quality: float = 0.85,
    seed: int = 20260525,
) -> list[dict]:
    """Deterministic synthetic momentum history with seeded predictiveness.

    Each row is a dict with:
      * ``entity_id``     — synthetic entity key
      * ``momentum_7d``   — 7-day momentum reading (signed)
      * ``momentum_30d``  — 30-day momentum reading (signed)
      * ``momentum_90d``  — 90-day momentum reading (signed)
      * ``composite``     — same 0.2/0.4/0.4 blend the live ranker uses
      * ``realized_forward_return`` — forward return that materialised

    ``signal_quality`` ∈ [0, 1] controls how much the realized return
    actually follows the composite signal — 1.0 = perfectly predictive
    (return == composite plus tiny noise), 0.0 = pure noise (return
    is unrelated to the signal).
    """
    rng = random.Random(seed)
    quality = max(0.0, min(1.0, float(signal_quality)))
    rows: list[dict] = []
    for e_idx in range(n_entities):
        # Each entity has a slight directional bias (-0.05 to +0.05) on top
        # of random momentum readings — keeps the dataset from being uniform.
        bias = rng.uniform(-0.05, 0.05)
        for _ in range(n_periods):
            m7  = rng.uniform(-0.30, 0.30) + bias
            m30 = rng.uniform(-0.30, 0.30) + bias
            m90 = rng.uniform(-0.30, 0.30) + bias
            composite = 0.2 * m7 + 0.4 * m30 + 0.4 * m90
            # Realized return = signal_quality * composite + noise.
            signal_part = quality * composite
            noise_part = rng.gauss(0.0, 0.15) * (1.0 - quality * 0.7)
            realized = signal_part + noise_part
            rows.append({
                "entity_id":               f"E{e_idx}",
                "momentum_7d":             round(m7, 6),
                "momentum_30d":            round(m30, 6),
                "momentum_90d":            round(m90, 6),
                "composite":               round(composite, 6),
                "realized_forward_return": round(realized, 6),
            })
    return rows


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def backtest_momentum_signals(
    history: Iterable[dict] | None = None,
    *,
    seed: int = 20260525,
    signal_quality: float = 0.85,
) -> MomentumBacktestReport:
    """Group a momentum history by signal class and score per class.

    Parameters
    ----------
    history:
        Iterable of dicts with the keys produced by
        :func:`synthesize_momentum_history`. If ``None`` or empty, the
        synthetic generator is used so the backtest always returns.
    seed:
        Forwarded to the synthetic generator when ``history`` is empty.
    signal_quality:
        Forwarded to the synthetic generator when ``history`` is empty.

    Returns
    -------
    MomentumBacktestReport
    """
    rows = list(history or [])
    if not rows:
        rows = synthesize_momentum_history(
            seed=seed, signal_quality=signal_quality,
        )

    # Group returns by signal class. Rows without a composite get bucketed
    # into NEUTRAL — same as the live signal function does.
    by_signal: dict[str, list[float]] = {sig: [] for sig in SIGNAL_CLASSES}
    for row in rows:
        composite = float((row or {}).get("composite", 0.0) or 0.0)
        ret       = float((row or {}).get("realized_forward_return", 0.0) or 0.0)
        sig = _signal_from_composite(composite)
        by_signal[sig].append(ret)

    scorecards: list[MomentumSignalScorecard] = []
    for sig in SIGNAL_CLASSES:
        returns = by_signal[sig]
        mean_ret = (sum(returns) / len(returns)) if returns else 0.0
        hit_rate = _directional_hit_rate(sig, returns)
        scorecards.append(MomentumSignalScorecard(
            signal=sig,
            n_observations=len(returns),
            mean_forward_return=mean_ret,
            directional_hit_rate=hit_rate,
            edge_vs_baseline=hit_rate - 0.5,
        ))

    # Monotonicity check: does mean return rise monotonically as we walk
    # from STRONG_SELL → STRONG_BUY? Classes with zero obs are skipped
    # (they don't break monotonicity).
    populated = [sc for sc in scorecards if sc.n_observations > 0]
    monotonic = True
    for a, b in zip(populated, populated[1:]):
        if a.mean_forward_return > b.mean_forward_return:
            monotonic = False
            break

    by_sig_map = {sc.signal: sc for sc in scorecards}
    strong_buy  = by_sig_map["STRONG_BUY"].mean_forward_return
    strong_sell = by_sig_map["STRONG_SELL"].mean_forward_return
    spread = strong_buy - strong_sell

    summary = (
        f"Across {len(rows)} obs, STRONG_BUY mean = {strong_buy * 100:+.2f}% "
        f"vs STRONG_SELL mean = {strong_sell * 100:+.2f}% "
        f"(spread {spread * 100:+.2f}pp); monotonic ladder: {monotonic}."
        if rows else "No momentum history available."
    )

    return MomentumBacktestReport(
        scorecards=scorecards,
        n_observations=len(rows),
        monotonic_by_signal=monotonic,
        spread_strong_vs_weak=spread,
        source=MOMENTUM_BACKTEST_SOURCE,
        summary=summary,
    )
