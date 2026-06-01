"""ssi_port_correlation_backtest.py — does the analyzer recover a known lag?

Validates :mod:`processing.ssi_port_correlation`. The analyzer claims
"this is the lag at which SSI best predicts port deficits". That
claim is only credible if, given a synthetic series with a *known*
true lag, the analyzer actually recovers that lag.

Strategy (matches the shape of every other ``*_backtest.py`` in this
package):

  1. Generate ``n_runs`` paired (ssi, deficit) series via
     :func:`processing.ssi_port_correlation.generate_synthetic_paired_series`,
     each with the same ``true_lag_days`` and varying seeds.
  2. Ask :func:`analyze_leading_indicator_relationship` to find the
     best lag.
  3. Count a run as "recovered" if the detected best_lag is within
     ``±tolerance_days`` of the truth.

Defining empirical property:

  * **Zero noise** → 100% recovery rate (the analyzer is deterministic
    and the relationship is perfect, so detection must hit the truth on
    every run).
  * **High noise (noise=1.0)** → recovery rate falls below 50% (the
    signal-to-noise ratio is by construction too low for reliable
    inference, so the analyzer should NOT spuriously claim high
    recovery; that would indicate the metric is rigged).

Pure-function module: no Streamlit, no I/O. Public helper NEVER raises;
on degenerate inputs it returns a defensible neutral result.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from data.quality import DataSource

from processing.ssi_port_correlation import (
    analyze_leading_indicator_relationship,
    generate_synthetic_paired_series,
)


__all__ = [
    "validate_leading_indicator_recovery",
    "SSI_PORT_CORRELATION_BACKTEST_SOURCE",
]


# ---------------------------------------------------------------------------
# Provenance marker
# ---------------------------------------------------------------------------

SSI_PORT_CORRELATION_BACKTEST_SOURCE = DataSource.modeled(
    "SSI / Port-Deficit Lag Recovery Backtest",
    notes=(
        "Generates synthetic paired (SSI, deficit) series with a known "
        "lag, asks the analyzer to recover it, succeeds if the detected "
        "best_lag is within tolerance of the truth."
    ),
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_leading_indicator_recovery(
    *,
    n_runs: int = 10,
    noise: float = 0.0,
    true_lag_days: int = 3,
    seed: int = 20260526,
    tolerance_days: int = 2,
    n_days: int = 90,
    max_lag_days: int = 14,
) -> dict[str, Any]:
    """Run the lag-recovery backtest.

    Parameters
    ----------
    n_runs:
        Number of independent synthetic runs to average over. Each run
        uses ``seed + i`` so they don't collide.
    noise:
        Std-dev of the additive Gaussian noise on the deficit series
        passed through to ``generate_synthetic_paired_series``. ``0.0``
        gives perfect recovery; ``1.0`` decorrelates significantly.
    true_lag_days:
        The ground-truth lag baked into every synthetic pair. Must be
        non-negative; clamped to >= 0.
    seed:
        Master seed; per-run seeds are ``seed + i``.
    tolerance_days:
        A run counts as a recovery when ``|detected_lag - true_lag| <= tolerance_days``.
        Defaults to 2 days, matching the operator-tolerance convention
        elsewhere in this package (sub-week precision is enough).
    n_days:
        Length of each synthetic series. Default 90 = 3 months, which
        is enough to make the lag-correlation peak distinguishable from
        sampling noise.
    max_lag_days:
        Upper bound passed to the analyzer's sweep. Must be >= true_lag_days
        otherwise the recovery rate would be artificially zero. Clamped
        to ``max(true_lag_days + 2, max_lag_days)`` defensively.

    Returns
    -------
    dict[str, Any]
        Backtest result with the canonical shape used by ``tools.backtests``:

          * ``n_runs`` — runs executed
          * ``noise`` — input noise level
          * ``true_lag_days`` — ground-truth lag
          * ``tolerance_days`` — recovery window
          * ``recoveries`` — int, count of successful recoveries
          * ``recovery_rate`` — float in [0, 1]
          * ``mean_abs_lag_error`` — mean of |detected - truth| across runs
          * ``mean_best_r`` — mean of best_lag_r across runs
          * ``passed`` — bool roll-up: recovery_rate >= 0.7
          * ``per_run`` — list of per-run dicts with detected_lag + r
          * ``source`` — DataSource provenance marker
          * ``summary`` — one-line operator-facing description
    """
    n_runs = max(1, int(n_runs))
    true_lag = max(0, int(true_lag_days))
    tolerance = max(0, int(tolerance_days))
    n_days = max(10, int(n_days))
    requested_max_lag = max(true_lag + 2, int(max_lag_days))

    per_run: list[dict[str, Any]] = []
    abs_errors: list[int] = []
    best_rs: list[float] = []
    recoveries = 0

    for i in range(n_runs):
        ssi, deficit = generate_synthetic_paired_series(
            n_days=n_days,
            ssi_lead_days=true_lag,
            noise=float(noise),
            seed=int(seed) + i,
        )
        report = analyze_leading_indicator_relationship(
            ssi, deficit, max_lag_days=requested_max_lag,
        )
        detected = int(report.best_lag_days)
        abs_err = abs(detected - true_lag)
        abs_errors.append(abs_err)
        best_rs.append(float(report.best_lag_r))
        recovered = abs_err <= tolerance
        if recovered:
            recoveries += 1
        per_run.append({
            "run_index":    i,
            "true_lag":     true_lag,
            "detected_lag": detected,
            "abs_error":    abs_err,
            "best_r":       round(float(report.best_lag_r), 4),
            "recovered":    bool(recovered),
        })

    recovery_rate = recoveries / n_runs if n_runs else 0.0
    mean_abs_error = sum(abs_errors) / len(abs_errors) if abs_errors else 0.0
    mean_best_r = sum(best_rs) / len(best_rs) if best_rs else 0.0
    passed = recovery_rate >= 0.70

    summary = (
        f"Lag-recovery across {n_runs} run(s) at noise={noise:.2f}, "
        f"true_lag={true_lag}d, tolerance=±{tolerance}d: "
        f"recovery_rate={recovery_rate * 100:.1f}%, "
        f"mean |error|={mean_abs_error:.2f}d, mean best |r|={abs(mean_best_r):.2f}; "
        f"passed (>=70%): {passed}."
    )

    return {
        "n_runs":              n_runs,
        "noise":               float(noise),
        "true_lag_days":       true_lag,
        "tolerance_days":      tolerance,
        "n_days":              n_days,
        "max_lag_days":        requested_max_lag,
        "recoveries":          int(recoveries),
        "recovery_rate":       round(float(recovery_rate), 4),
        "mean_abs_lag_error":  round(float(mean_abs_error), 4),
        "mean_best_r":         round(float(mean_best_r), 4),
        "passed":              bool(passed),
        "per_run":             per_run,
        "source":              SSI_PORT_CORRELATION_BACKTEST_SOURCE,
        "summary":             summary,
    }
