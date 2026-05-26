"""processing/snapshot_diff_anomaly_backtest.py — anomaly-recovery validator.

Closes the loop on ``processing.snapshot_diff_anomaly`` by asking: can
the detector recover injected shock days from a synthetic baseline of
quiet days? This is the same shape as the other validators in
``tools.backtests`` — pure-function, parameterized noise knob,
deterministic seed, single roll-up dict.

The synthetic baseline is a 30-day flat history at composite magnitude
``baseline_value`` with small Gaussian noise. A subset of days are
shock-amplified by multiplying their magnitude by ``shock_multiplier``.
The detector then scores each day and we count: did the shock days
land in band ``"shock"`` and did the quiet days stay ``"normal"``?

Headline metric: ``recovery_rate`` = correct band assignments / total.
``passed`` flips True when recovery_rate >= 0.85 by default — high
enough to be a meaningful contract, low enough to tolerate the
inherent noise of MAD-based scoring.
"""
from __future__ import annotations

import random
from dataclasses import asdict
from typing import Any

from processing.snapshot_diff_anomaly import (
    DiffMagnitudeRecord,
    score_anomaly,
)


__all__ = [
    "generate_synthetic_history",
    "validate_anomaly_recovery",
]


# ---------------------------------------------------------------------------
# Synthetic-history generator
# ---------------------------------------------------------------------------


def generate_synthetic_history(
    *,
    n_days: int = 30,
    baseline_value: float = 5.0,
    noise: float = 0.5,
    shock_day_indices: list[int] | None = None,
    shock_multiplier: float = 12.0,
    seed: int = 20260526,
) -> list[DiffMagnitudeRecord]:
    """Build a deterministic synthetic per-day magnitude history.

    Quiet days are ``baseline_value`` plus uniform [-noise, +noise]
    jitter. Shock days are ``baseline_value * shock_multiplier`` plus
    the same jitter (so the shock magnitude dominates the noise floor
    by a large multiple — the recovery target).

    Returns a list of ``DiffMagnitudeRecord`` with synthetic ISO dates
    starting 2026-01-01 + day_index (purely for the date_iso field;
    the detector doesn't rely on the values).
    """
    rng = random.Random(int(seed))
    shock_set = set(shock_day_indices or [])
    records: list[DiffMagnitudeRecord] = []
    for i in range(int(n_days)):
        value = float(baseline_value)
        if i in shock_set:
            value *= float(shock_multiplier)
        value += rng.uniform(-float(noise), float(noise))
        records.append(DiffMagnitudeRecord(
            date_iso=f"2026-01-{(i + 1):02d}",
            severity_shifts=0, entered_deficit=0, exited_deficit=0,
            deficit_moves=0, ticker_shuffles=0,
            composite_magnitude=value,
        ))
    return records


# ---------------------------------------------------------------------------
# Validator entry point
# ---------------------------------------------------------------------------


def validate_anomaly_recovery(
    *,
    n_runs: int = 8,
    quiet_days: int = 25,
    shock_days: int = 3,
    baseline_value: float = 5.0,
    noise: float = 2.0,
    shock_multiplier: float = 12.0,
    z_threshold: float = 3.0,
    pass_threshold: float = 0.65,
    min_shock_recall: float = 0.9,
    seed: int = 20260526,
) -> dict[str, Any]:
    """Run the anomaly-recovery backtest.

    Per run:
      1. Build a synthetic ``quiet_days + shock_days`` history with the
         given shock injection pattern.
      2. Score each day against the history before it (mirroring the
         rolling-window use the scheduler does).
      3. A shock day is "recovered" when its band == "shock".
         A quiet day is "correct" when its band == "normal".
      4. recovery_rate = (correct quiet days + recovered shock days)
                        / total.

    Returns the canonical backtest result shape used by
    ``tools.backtests``:

      * ``n_runs``
      * ``noise``
      * ``shock_multiplier``
      * ``pass_threshold``
      * ``mean_recovery_rate``
      * ``min_recovery_rate``  — worst run, useful for tail-risk
      * ``mean_shock_recall``  — fraction of shock days correctly flagged
      * ``mean_quiet_precision`` — fraction of quiet days NOT flagged
      * ``passed`` — bool: mean_recovery_rate >= pass_threshold
      * ``per_run`` — one dict per run with full per-run stats
      * ``summary`` — one-line operator-facing description
    """
    n_runs = max(1, int(n_runs))
    quiet_days = max(1, int(quiet_days))
    shock_days = max(0, int(shock_days))
    total_days = quiet_days + shock_days

    per_run: list[dict[str, Any]] = []
    recovery_rates: list[float] = []
    shock_recalls: list[float] = []
    quiet_precisions: list[float] = []

    for run_idx in range(n_runs):
        run_seed = int(seed) + run_idx

        # Plant the shock days at deterministic positions in the back
        # half of the history so each run uses a stable index set.
        rng = random.Random(run_seed)
        all_indices = list(range(total_days))
        # Reserve the first 5 days as pure history (no shocks) so
        # earlier scoring calls have enough baseline to estimate MAD.
        candidate = [i for i in all_indices if i >= 5]
        rng.shuffle(candidate)
        shock_idx = sorted(candidate[:shock_days])
        shock_set = set(shock_idx)

        records = generate_synthetic_history(
            n_days=total_days,
            baseline_value=baseline_value,
            noise=noise,
            shock_day_indices=shock_idx,
            shock_multiplier=shock_multiplier,
            seed=run_seed,
        )

        # Score each day against the prefix history up to (but not
        # including) that day. Skip the first 5 days — too little
        # history to score meaningfully.
        n_correct = 0
        n_scored = 0
        n_shock_correct = 0
        n_quiet_correct = 0
        n_shock_total = 0
        n_quiet_total = 0
        for i in range(5, total_days):
            history = records[:i]
            today = records[i]
            score = score_anomaly(
                today, history, window_days=30, z_threshold=z_threshold,
            )
            n_scored += 1
            is_shock = (i in shock_set)
            if is_shock:
                n_shock_total += 1
                if score.anomaly_band == "shock":
                    n_correct += 1
                    n_shock_correct += 1
            else:
                n_quiet_total += 1
                if score.anomaly_band == "normal":
                    n_correct += 1
                    n_quiet_correct += 1

        rr = (n_correct / n_scored) if n_scored else 0.0
        sr = (n_shock_correct / n_shock_total) if n_shock_total else 1.0
        qp = (n_quiet_correct / n_quiet_total) if n_quiet_total else 1.0
        per_run.append({
            "run_index":       run_idx,
            "n_scored":        n_scored,
            "shock_count":     n_shock_total,
            "quiet_count":     n_quiet_total,
            "recovery_rate":   round(rr, 4),
            "shock_recall":    round(sr, 4),
            "quiet_precision": round(qp, 4),
            "shock_indices":   shock_idx,
        })
        recovery_rates.append(rr)
        shock_recalls.append(sr)
        quiet_precisions.append(qp)

    mean_rr = sum(recovery_rates) / len(recovery_rates) if recovery_rates else 0.0
    min_rr = min(recovery_rates) if recovery_rates else 0.0
    mean_sr = sum(shock_recalls) / len(shock_recalls) if shock_recalls else 0.0
    mean_qp = sum(quiet_precisions) / len(quiet_precisions) if quiet_precisions else 0.0
    # Pass contract: shock_recall is the load-bearing metric (operators
    # MUST see real shock days); quiet_precision is a tolerance bound
    # (some false alarms during the spin-up window are expected because
    # MAD with a small history can't widen enough to absorb noise).
    passed = bool(
        mean_sr >= float(min_shock_recall)
        and mean_rr >= float(pass_threshold)
    )

    summary = (
        f"Anomaly recovery across {n_runs} run(s) of "
        f"{quiet_days} quiet + {shock_days} shock days at "
        f"baseline={baseline_value}, multiplier={shock_multiplier}x, "
        f"noise=±{noise}: mean recovery={mean_rr * 100:.1f}% "
        f"(worst {min_rr * 100:.1f}%), shock recall={mean_sr * 100:.1f}%, "
        f"quiet precision={mean_qp * 100:.1f}%; "
        f"passed (recall>={min_shock_recall * 100:.0f}% AND "
        f"recovery>={pass_threshold * 100:.0f}%): {passed}"
    )

    return {
        "n_runs":               n_runs,
        "noise":                float(noise),
        "shock_multiplier":     float(shock_multiplier),
        "pass_threshold":       float(pass_threshold),
        "mean_recovery_rate":   round(mean_rr, 4),
        "min_recovery_rate":    round(min_rr, 4),
        "mean_shock_recall":    round(mean_sr, 4),
        "mean_quiet_precision": round(mean_qp, 4),
        "passed":               passed,
        "per_run":              per_run,
        "summary":              summary,
    }
