"""leading_indicators_backtest.py — lead-time + signal accuracy backtest.

The ``processing.leading_indicators`` catalogue carries 12+ indicators
(MANEMP, AMTMNO, IPMAN, UMCSENT, etc.) each tagged with:

  * a ``signal`` classification (BULLISH / BEARISH / NEUTRAL)
  * a stated ``lead_time_weeks`` for shipping demand

Nothing in the platform asks: **at each indicator's stated lead time,
does the BULLISH signal actually lead to higher realized shipping demand
than BEARISH?** This module fills that gap.

For each signal class, the scorecard reports:

  * **mean_forward_demand_pct**     — average signed forward demand
                                       move that materialized at the
                                       indicator's stated lead time
  * **directional_hit_rate**         — fraction of windows where the
                                       demand-move sign matched the
                                       signal class (NEUTRAL pinned to
                                       0.5 by convention)
  * **edge_vs_baseline**             — directional_hit_rate - 0.5

Plus a roll-up ``signals_calibrated`` flag — True when BULLISH has a
positive mean forward demand AND BEARISH has a negative mean.

A deterministic synthetic generator with a ``signal_quality`` knob
powers the tests: 1.0 yields a cleanly-calibrated classifier; 0.0
yields pure noise. The property tests pin both ends.

Transparent rule-based scorecard — no fitted ML, no opaque weights.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable

from data.quality import DataSource


__all__ = [
    "INDICATOR_SIGNALS",
    "IndicatorSignalScorecard",
    "LeadingIndicatorsBacktestReport",
    "synthesize_indicator_history",
    "backtest_leading_indicators",
    "LEADING_INDICATORS_BACKTEST_SOURCE",
]


INDICATOR_SIGNALS: tuple[str, ...] = ("BEARISH", "NEUTRAL", "BULLISH")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class IndicatorSignalScorecard:
    """Per-signal-class scorecard for the leading-indicator classifier."""

    signal: str
    n_observations: int
    mean_forward_demand_pct: float
    directional_hit_rate: float   # in [0, 1]; NEUTRAL pinned to 0.5
    edge_vs_baseline: float


@dataclass
class LeadingIndicatorsBacktestReport:
    scorecards: list[IndicatorSignalScorecard] = field(default_factory=list)
    n_observations: int = 0
    signals_calibrated: bool = False    # BULLISH mean > 0 AND BEARISH mean < 0
    spread_bullish_vs_bearish: float = 0.0
    source: DataSource | None = None
    summary: str = ""


LEADING_INDICATORS_BACKTEST_SOURCE = DataSource.modeled(
    "Leading Indicators Backtest",
    notes=(
        "Per-signal-class scorecard for processing.leading_indicators. "
        "Mean forward shipping demand at the indicator's stated lead "
        "time + directional hit rate per class."
    ),
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _directional_hit_rate(signal: str, returns: list[float]) -> float:
    """Fraction of returns in the signal's favour.

    BULLISH → positive return is a hit; BEARISH → negative return is a hit;
    NEUTRAL has no directional claim (pinned to 0.5).
    """
    if not returns:
        return 0.5
    upper = signal.upper()
    if upper == "NEUTRAL":
        return 0.5
    is_bull = upper == "BULLISH"
    hits = 0
    total = 0
    for r in returns:
        if r == 0.0:
            continue
        total += 1
        in_favor = (r > 0) if is_bull else (r < 0)
        if in_favor:
            hits += 1
    if total == 0:
        return 0.5
    return hits / total


# ---------------------------------------------------------------------------
# Synthetic generator
# ---------------------------------------------------------------------------


def synthesize_indicator_history(
    *,
    n_periods: int = 80,
    n_indicators: int = 6,
    signal_quality: float = 0.85,
    seed: int = 20260525,
) -> list[dict]:
    """Deterministic synthetic indicator history.

    Each row is a dict with:
      * ``series_id``                   — synthetic indicator key
      * ``signal``                      — one of INDICATOR_SIGNALS
      * ``realized_forward_demand_pct`` — signed shipping-demand move
                                          at the indicator's lead time

    ``signal_quality ∈ [0, 1]`` controls how cleanly each signal predicts
    the realized demand move. 1.0 = perfect; 0.0 = noise.
    """
    rng = random.Random(seed)
    quality = max(0.0, min(1.0, float(signal_quality)))
    rows: list[dict] = []
    for i_idx in range(n_indicators):
        for _ in range(n_periods):
            signal = rng.choice(INDICATOR_SIGNALS)
            # Signal contribution: BULLISH → +drift, BEARISH → -drift,
            # NEUTRAL → no drift.
            signal_drift = 0.0
            if signal == "BULLISH":
                signal_drift = quality * 0.025
            elif signal == "BEARISH":
                signal_drift = -quality * 0.025
            # Noise that fades with quality (so quality=1 → near-perfect signal)
            noise = rng.gauss(0.0, 0.02) * (1.0 - quality * 0.6)
            realized = signal_drift + noise
            rows.append({
                "series_id":                   f"IND_{i_idx}",
                "signal":                      signal,
                "realized_forward_demand_pct": round(realized, 6),
            })
    return rows


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def backtest_leading_indicators(
    history: Iterable[dict] | None = None,
    *,
    seed: int = 20260525,
    signal_quality: float = 0.85,
) -> LeadingIndicatorsBacktestReport:
    """Score leading-indicator signals against realized forward demand."""
    rows = list(history or [])
    if not rows:
        rows = synthesize_indicator_history(
            seed=seed, signal_quality=signal_quality,
        )

    by_signal: dict[str, list[float]] = {s: [] for s in INDICATOR_SIGNALS}
    for row in rows:
        ret = float((row or {}).get("realized_forward_demand_pct", 0.0) or 0.0)
        sig = str((row or {}).get("signal", "") or "")
        if sig in by_signal:
            by_signal[sig].append(ret)

    scorecards: list[IndicatorSignalScorecard] = []
    for sig in INDICATOR_SIGNALS:
        returns = by_signal[sig]
        mean_ret = (sum(returns) / len(returns)) if returns else 0.0
        hit = _directional_hit_rate(sig, returns)
        scorecards.append(IndicatorSignalScorecard(
            signal=sig,
            n_observations=len(returns),
            mean_forward_demand_pct=mean_ret,
            directional_hit_rate=hit,
            edge_vs_baseline=hit - 0.5,
        ))

    by_sig = {sc.signal: sc for sc in scorecards}
    signals_calibrated = (
        by_sig["BULLISH"].mean_forward_demand_pct > 0
        and by_sig["BEARISH"].mean_forward_demand_pct < 0
    )
    spread = (
        by_sig["BULLISH"].mean_forward_demand_pct
        - by_sig["BEARISH"].mean_forward_demand_pct
    )

    summary = (
        f"Across {len(rows)} obs: BULLISH mean = "
        f"{by_sig['BULLISH'].mean_forward_demand_pct * 100:+.2f}% vs "
        f"BEARISH mean = "
        f"{by_sig['BEARISH'].mean_forward_demand_pct * 100:+.2f}% "
        f"(spread {spread * 100:+.2f}pp); calibrated: {signals_calibrated}."
        if rows else "No leading-indicator history available."
    )

    return LeadingIndicatorsBacktestReport(
        scorecards=scorecards,
        n_observations=len(rows),
        signals_calibrated=signals_calibrated,
        spread_bullish_vs_bearish=spread,
        source=LEADING_INDICATORS_BACKTEST_SOURCE,
        summary=summary,
    )
