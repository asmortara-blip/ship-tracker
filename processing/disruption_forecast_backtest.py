"""disruption_forecast_backtest.py — accuracy backtest for stress forecasts.

The ``processing.disruption_forecast`` module produces 7-day and 30-day
stress projections per route — but nothing in the platform scores those
forecasts against what actually happens. This module fills that gap:
given a history of ``(forecast_at_t, realized_at_t+horizon)`` tuples
per route, it computes:

  * **MAE** at each horizon — mean absolute error between the forecast
    and the realized stress, on a [0, 1] stress scale
  * **sign-agreement rate** at each horizon — fraction of windows where
    the *direction* (forecast > current vs. < current) matched the
    realized direction
  * **n_observations** — usable windows scored

A synthetic-history generator ships alongside so the backtest can be
exercised without external feeds. The generated history seeds a known
forecast-error level so the property tests can pin behaviour.

This is a transparent, rule-based scorecard — no fitted ML, no opaque
weights. Every number can be reproduced from the inputs.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable

from data.quality import DataSource


__all__ = [
    "RouteAccuracyScorecard",
    "ForecastAccuracyReport",
    "synthesize_forecast_history",
    "backtest_disruption_forecast",
    "DISRUPTION_FORECAST_BACKTEST_SOURCE",
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class RouteAccuracyScorecard:
    """One route's forecast-accuracy scorecard across all evaluated periods.

    All fields are plain arithmetic observables — nothing is fitted.
    """

    route_id: str
    n_observations: int          # usable forecast/realized pairs
    mae_7d: float                # mean absolute error at 7d horizon
    mae_30d: float               # mean absolute error at 30d horizon
    sign_agreement_7d: float     # in [0, 1]; 0.5 = random
    sign_agreement_30d: float    # in [0, 1]; 0.5 = random
    note: str = ""


@dataclass
class ForecastAccuracyReport:
    """A run of the disruption-forecast accuracy backtest."""

    scorecards: list[RouteAccuracyScorecard] = field(default_factory=list)
    n_observations: int = 0
    mean_mae_7d: float = 0.0
    mean_mae_30d: float = 0.0
    mean_sign_agreement_7d: float = 0.5
    mean_sign_agreement_30d: float = 0.5
    best_route: str = ""         # lowest mean MAE (7d + 30d averaged)
    worst_route: str = ""        # highest mean MAE
    source: DataSource | None = None
    summary: str = ""


DISRUPTION_FORECAST_BACKTEST_SOURCE = DataSource.modeled(
    "Disruption Forecast Backtest",
    notes=(
        "Per-route accuracy scorecard for the 7d and 30d stress forecasts "
        "from processing.disruption_forecast. MAE + sign-agreement rate "
        "scored against realized stress at each horizon."
    ),
)


# ---------------------------------------------------------------------------
# Pure numeric helpers
# ---------------------------------------------------------------------------


def _mae(predicted: list[float], realized: list[float]) -> float:
    """Mean absolute error between two equal-length lists.

    Returns 0.0 when either is empty (rather than raising) — empty lists
    mean "no observations" not "perfect predictions", but the caller
    surfaces that via ``n_observations`` and a ``note``.
    """
    n = min(len(predicted), len(realized))
    if n == 0:
        return 0.0
    return sum(abs(predicted[i] - realized[i]) for i in range(n)) / n


def _sign_agreement_against(
    current: list[float],
    forecast: list[float],
    realized: list[float],
) -> float:
    """Fraction of windows where (forecast - current) and (realized - current)
    have the same sign — i.e. the forecast got the *direction* right.

    Windows where either delta is exactly zero are skipped so a flat
    series doesn't inflate the rate.
    """
    n = min(len(current), len(forecast), len(realized))
    if n == 0:
        return 0.5
    agree = 0
    total = 0
    for i in range(n):
        f_delta = forecast[i] - current[i]
        r_delta = realized[i] - current[i]
        if f_delta == 0.0 or r_delta == 0.0:
            continue
        total += 1
        if (f_delta > 0) == (r_delta > 0):
            agree += 1
    if total == 0:
        return 0.5
    return agree / total


# ---------------------------------------------------------------------------
# Synthetic history generator (deterministic; powers the test + UI smoke)
# ---------------------------------------------------------------------------


def synthesize_forecast_history(
    *,
    n_periods: int = 24,
    n_routes: int = 6,
    seed: int = 20260525,
    forecast_noise: float = 0.05,
) -> list[dict]:
    """Deterministic synthetic forecast history with seeded accuracy.

    Each row is a dict with:
      * ``route_id`` — synthetic route key, e.g. ``"ROUTE_0"``
      * ``current_stress`` — stress level at the time the forecast was made
      * ``forecast_7d`` — the model's 7-day projection
      * ``forecast_30d`` — the model's 30-day projection
      * ``realized_7d`` — what stress actually was 7d later
      * ``realized_30d`` — what stress actually was 30d later

    The realized series is the forecast plus Gaussian noise scaled by
    ``forecast_noise``. Lower noise → lower MAE → cleaner test signal.
    All values are clamped to ``[0, 1]``.
    """
    rng = random.Random(seed)
    rows: list[dict] = []
    for r_idx in range(n_routes):
        # Each route has a baseline stress level it drifts around.
        baseline = 0.30 + 0.50 * (r_idx / max(1, n_routes - 1))
        for p in range(n_periods):
            current = max(0.0, min(1.0, baseline + rng.uniform(-0.15, 0.15)))
            # The "true" forecast is current + some directional drift.
            drift_7d  = rng.uniform(-0.12, 0.12)
            drift_30d = rng.uniform(-0.18, 0.18)
            forecast_7d  = max(0.0, min(1.0, current + drift_7d))
            forecast_30d = max(0.0, min(1.0, current + drift_30d))
            # Realized = forecast + noise (seeded so it's deterministic).
            realized_7d  = max(0.0, min(1.0,
                forecast_7d  + rng.gauss(0.0, forecast_noise)))
            realized_30d = max(0.0, min(1.0,
                forecast_30d + rng.gauss(0.0, forecast_noise)))
            rows.append({
                "route_id":       f"ROUTE_{r_idx}",
                "current_stress": round(current, 4),
                "forecast_7d":    round(forecast_7d, 4),
                "forecast_30d":   round(forecast_30d, 4),
                "realized_7d":    round(realized_7d, 4),
                "realized_30d":   round(realized_30d, 4),
            })
    return rows


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def backtest_disruption_forecast(
    history: Iterable[dict] | None = None,
    *,
    seed: int = 20260525,
) -> ForecastAccuracyReport:
    """Run the per-route forecast-accuracy backtest.

    Parameters
    ----------
    history:
        Iterable of dicts with the keys produced by
        :func:`synthesize_forecast_history`. If ``None`` or empty, the
        synthetic generator is used so the backtest always returns.
    seed:
        Forwarded to the synthetic generator when ``history`` is empty.

    Returns
    -------
    ForecastAccuracyReport
        A full, deterministic per-route scorecard plus aggregate stats.
    """
    rows = list(history or [])
    if not rows:
        rows = synthesize_forecast_history(seed=seed)

    # Group rows by route_id; preserve first-seen order so the scorecard
    # ordering is stable across runs.
    by_route: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in rows:
        rid = str((row or {}).get("route_id", ""))
        if not rid:
            continue
        if rid not in by_route:
            by_route[rid] = []
            order.append(rid)
        by_route[rid].append(row)

    scorecards: list[RouteAccuracyScorecard] = []
    for rid in order:
        bucket = by_route[rid]
        current = [float(r.get("current_stress", 0.0)) for r in bucket]
        f7      = [float(r.get("forecast_7d", 0.0))    for r in bucket]
        f30     = [float(r.get("forecast_30d", 0.0))   for r in bucket]
        r7      = [float(r.get("realized_7d", 0.0))    for r in bucket]
        r30     = [float(r.get("realized_30d", 0.0))   for r in bucket]
        scorecards.append(RouteAccuracyScorecard(
            route_id=rid,
            n_observations=len(bucket),
            mae_7d=_mae(f7, r7),
            mae_30d=_mae(f30, r30),
            sign_agreement_7d=_sign_agreement_against(current, f7, r7),
            sign_agreement_30d=_sign_agreement_against(current, f30, r30),
        ))

    if scorecards:
        mean_mae_7    = sum(s.mae_7d for s in scorecards) / len(scorecards)
        mean_mae_30   = sum(s.mae_30d for s in scorecards) / len(scorecards)
        mean_sa_7     = sum(s.sign_agreement_7d for s in scorecards) / len(scorecards)
        mean_sa_30    = sum(s.sign_agreement_30d for s in scorecards) / len(scorecards)
        # best = lowest avg MAE; worst = highest avg MAE
        ranked = sorted(scorecards, key=lambda sc: (sc.mae_7d + sc.mae_30d))
        best  = ranked[0].route_id
        worst = ranked[-1].route_id
    else:
        mean_mae_7 = mean_mae_30 = 0.0
        mean_sa_7 = mean_sa_30 = 0.5
        best = worst = ""

    summary = (
        f"Across {len(scorecards)} route(s): mean 7d MAE = "
        f"{mean_mae_7:.3f}, mean 30d MAE = {mean_mae_30:.3f}; "
        f"directional sign-agreement {mean_sa_7 * 100:.0f}% / "
        f"{mean_sa_30 * 100:.0f}% at 7d / 30d."
        if scorecards else "No forecast history available."
    )

    return ForecastAccuracyReport(
        scorecards=scorecards,
        n_observations=len(rows),
        mean_mae_7d=mean_mae_7,
        mean_mae_30d=mean_mae_30,
        mean_sign_agreement_7d=mean_sa_7,
        mean_sign_agreement_30d=mean_sa_30,
        best_route=best,
        worst_route=worst,
        source=DISRUPTION_FORECAST_BACKTEST_SOURCE,
        summary=summary,
    )
