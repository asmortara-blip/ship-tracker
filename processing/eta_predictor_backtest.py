"""eta_predictor_backtest.py — ETA-prediction accuracy + risk-label monotonicity.

The ``processing.eta_predictor`` module predicts a route-level ETA with
two outputs nobody validates today:

  1. A scalar ``predicted_delay_days`` (signed)
  2. A categorical ``congestion_risk`` label
     (``LOW`` / ``MODERATE`` / ``HIGH`` / ``SEVERE``)

This module scores both:

  * **delay_mae**             — mean absolute error between predicted
                               and realized delay days, in days
  * **delay_sign_agreement**  — fraction of windows where the *sign*
                               of the predicted delay matched the
                               realized direction
  * **per-label realized delays** — average realized delay per
                               congestion-risk label; the ladder
                               LOW → MODERATE → HIGH → SEVERE should
                               rise monotonically if the classifier
                               is calibrated (``monotonic_by_label``
                               roll-up flag)

A deterministic synth with a ``prediction_quality`` knob exercises
both ends of the property tests. At quality=1.0 the MAE collapses
to near zero and the label ladder is strict; at quality=0.0 the
prediction is uncorrelated with realized delay and the ladder
collapses onto a single baseline.

Transparent rule-based scorecard — no fitted ML, no opaque weights.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable

from data.quality import DataSource


__all__ = [
    "CONGESTION_RISK_LABELS",
    "EtaLabelScorecard",
    "EtaAccuracyReport",
    "synthesize_eta_history",
    "backtest_eta_predictor",
    "ETA_PREDICTOR_BACKTEST_SOURCE",
]


CONGESTION_RISK_LABELS: tuple[str, ...] = (
    "LOW", "MODERATE", "HIGH", "SEVERE",
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class EtaLabelScorecard:
    """Per-congestion-risk-label scorecard."""

    label: str
    n_observations: int
    mean_realized_delay_days: float


@dataclass
class EtaAccuracyReport:
    """A run of the ETA-prediction backtest."""

    label_scorecards: list[EtaLabelScorecard] = field(default_factory=list)
    n_observations: int = 0
    delay_mae: float = 0.0
    delay_sign_agreement: float = 0.5
    monotonic_by_label: bool = False
    spread_severe_vs_low: float = 0.0
    source: DataSource | None = None
    summary: str = ""


ETA_PREDICTOR_BACKTEST_SOURCE = DataSource.modeled(
    "ETA Predictor Backtest",
    notes=(
        "Predicted-vs-realized delay-day scorecard for "
        "processing.eta_predictor. MAE + sign-agreement on the scalar "
        "delay prediction + monotonicity check on the LOW → SEVERE "
        "congestion-risk-label ladder."
    ),
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _mae(predicted: list[float], realized: list[float]) -> float:
    n = min(len(predicted), len(realized))
    if n == 0:
        return 0.0
    return sum(abs(predicted[i] - realized[i]) for i in range(n)) / n


def _sign_agreement(predicted: list[float], realized: list[float]) -> float:
    """Fraction of pairs where sign(predicted) == sign(realized).

    Pairs where either is exactly zero are skipped — no directional content.
    """
    n = min(len(predicted), len(realized))
    if n == 0:
        return 0.5
    hits = total = 0
    for i in range(n):
        if predicted[i] == 0.0 or realized[i] == 0.0:
            continue
        total += 1
        if (predicted[i] > 0) == (realized[i] > 0):
            hits += 1
    if total == 0:
        return 0.5
    return hits / total


# ---------------------------------------------------------------------------
# Synthetic history generator
# ---------------------------------------------------------------------------


def synthesize_eta_history(
    *,
    n_observations: int = 240,
    prediction_quality: float = 0.85,
    seed: int = 20260525,
) -> list[dict]:
    """Deterministic synthetic ETA history.

    Each row is a dict with:
      * ``congestion_risk_label`` — one of CONGESTION_RISK_LABELS
      * ``predicted_delay_days``  — model's predicted delay (signed)
      * ``realized_delay_days``   — what actually materialized (signed)

    ``prediction_quality`` ∈ [0, 1] controls how cleanly predicted
    matches realized. The per-label "true" mean realized delay rises
    monotonically LOW → SEVERE when quality > 0, and collapses onto a
    single baseline at quality=0.
    """
    rng = random.Random(seed)
    q = max(0.0, min(1.0, float(prediction_quality)))
    # Per-label "true" mean realized delay (days). At quality=1.0 we hit
    # these exact means; at quality=0.0 all labels collapse to baseline.
    rates_full: dict[str, float] = {
        "LOW":      0.5,
        "MODERATE": 2.5,
        "HIGH":     5.5,
        "SEVERE":   9.0,
    }
    baseline = 3.0
    label_means: dict[str, float] = {
        label: baseline + q * (mean - baseline)
        for label, mean in rates_full.items()
    }

    rows: list[dict] = []
    for _ in range(n_observations):
        label = rng.choice(CONGESTION_RISK_LABELS)
        # Realized delay = label mean + small noise
        realized = label_means[label] + rng.gauss(0.0, 1.0)
        # Predicted delay = realized * quality + noise * (1 - quality)
        predicted = q * realized + rng.gauss(0.0, 1.5) * (1.0 - q)
        rows.append({
            "congestion_risk_label":  label,
            "predicted_delay_days":   round(predicted, 4),
            "realized_delay_days":    round(realized, 4),
        })
    return rows


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def backtest_eta_predictor(
    history: Iterable[dict] | None = None,
    *,
    seed: int = 20260525,
    prediction_quality: float = 0.85,
) -> EtaAccuracyReport:
    """Score ETA predictions against realized delays."""
    rows = list(history or [])
    if not rows:
        rows = synthesize_eta_history(
            seed=seed, prediction_quality=prediction_quality,
        )

    by_label: dict[str, list[float]] = {l: [] for l in CONGESTION_RISK_LABELS}
    predicted: list[float] = []
    realized:  list[float] = []
    for row in rows:
        label = str((row or {}).get("congestion_risk_label", "") or "")
        p = float((row or {}).get("predicted_delay_days", 0.0) or 0.0)
        r = float((row or {}).get("realized_delay_days", 0.0) or 0.0)
        if label in by_label:
            by_label[label].append(r)
        predicted.append(p)
        realized.append(r)

    label_cards: list[EtaLabelScorecard] = []
    for label in CONGESTION_RISK_LABELS:
        realized_for_label = by_label[label]
        mean_realized = (
            sum(realized_for_label) / len(realized_for_label)
            if realized_for_label else 0.0
        )
        label_cards.append(EtaLabelScorecard(
            label=label,
            n_observations=len(realized_for_label),
            mean_realized_delay_days=mean_realized,
        ))

    # Monotonicity: per-label realized delay should rise LOW → SEVERE
    populated = [sc for sc in label_cards if sc.n_observations > 0]
    monotonic = True
    for a, b in zip(populated, populated[1:]):
        if a.mean_realized_delay_days > b.mean_realized_delay_days:
            monotonic = False
            break

    by_lbl = {sc.label: sc for sc in label_cards}
    spread = (
        by_lbl["SEVERE"].mean_realized_delay_days
        - by_lbl["LOW"].mean_realized_delay_days
    )

    mae = _mae(predicted, realized)
    sa  = _sign_agreement(predicted, realized)

    summary = (
        f"Across {len(rows)} obs: delay MAE = {mae:.2f}d · "
        f"sign-agreement = {sa * 100:.1f}% · "
        f"SEVERE mean = {by_lbl['SEVERE'].mean_realized_delay_days:.1f}d "
        f"vs LOW mean = {by_lbl['LOW'].mean_realized_delay_days:.1f}d "
        f"(spread {spread:+.1f}d); monotonic ladder: {monotonic}."
        if rows else "No ETA history available."
    )

    return EtaAccuracyReport(
        label_scorecards=label_cards,
        n_observations=len(rows),
        delay_mae=mae,
        delay_sign_agreement=sa,
        monotonic_by_label=monotonic,
        spread_severe_vs_low=spread,
        source=ETA_PREDICTOR_BACKTEST_SOURCE,
        summary=summary,
    )
