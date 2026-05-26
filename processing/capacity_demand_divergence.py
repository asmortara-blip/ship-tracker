"""processing/capacity_demand_divergence.py — modeled capacity vs observed demand.

For each route, we have:
  * **Modeled capacity** — TEU/day the active fleet on that lane can
    carry (containers in flight × turn time).
  * **Observed demand** — value-weighted volume of containerized trade
    flowing through that lane's origin/destination ports, derived from
    the COMTRADE / WITS feeds + cargo mix.

When capacity sits well above demand, freight rates compress + idle
vessels accumulate (a bear signal for the carriers). When demand
exceeds capacity, rates spike + congestion + ETA slippage follow
(a bull signal AND a supply-chain risk for the cargo owners).

This module computes a per-route divergence ratio:

    divergence = (capacity - demand) / max(capacity, demand)

In ``[-1, +1]``. Positive means capacity surplus; negative means
demand surplus (under-supplied). The magnitude says how far apart
the two sides are.

A **persistent** divergence (signed sum across N days has the same
sign) is the actionable signal — a single day of mismatch is noise.

Bands (absolute divergence on the trailing-N-day mean):
  0.00 → 0.10  →  "balanced"
  0.10 → 0.25  →  "loose"          (mild surplus/deficit)
  0.25 → 0.50  →  "stretched"      (real imbalance)
  0.50 → 1.00  →  "broken"         (severe mismatch — alert-worthy)

Pure-function — no I/O. Tests inject synthetic histories so the
math is verifiable independently of the live registries.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional


__all__ = [
    "DIVERGENCE_BANDS",
    "RouteDivergencePoint",
    "RouteDivergenceReport",
    "compute_route_divergence",
    "summarize_persistent_divergence",
]


# Lower-bound inclusive bands on absolute divergence.
DIVERGENCE_BANDS: list[tuple[float, str]] = [
    (0.00, "balanced"),
    (0.10, "loose"),
    (0.25, "stretched"),
    (0.50, "broken"),
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class RouteDivergencePoint:
    """One day of capacity vs demand for a single route."""

    route_id: str
    date_iso: str
    capacity_teu: float
    demand_teu: float
    divergence: float    # signed; in [-1, +1]


@dataclass
class RouteDivergenceReport:
    """Per-route persistent divergence assessment over a window."""

    route_id: str
    window_days: int
    n_points: int
    mean_divergence: float          # signed; in [-1, +1]
    abs_mean_divergence: float      # in [0, 1]
    divergence_band: str            # see DIVERGENCE_BANDS
    persistence_rate: float         # fraction of days same-sign as mean
    direction: str                  # "capacity_surplus" | "demand_surplus" | "balanced"
    is_alert_worthy: bool           # band >= "stretched" AND persistence > 0.7
    summary: str = ""
    points: list[RouteDivergencePoint] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-day divergence math
# ---------------------------------------------------------------------------


def _safe_divergence(capacity: float, demand: float) -> float:
    """Signed (capacity - demand) / max(capacity, demand), in [-1, +1].

    Defensive against zeros and negatives:
      * Both zero        → 0.0 (no flow on either side)
      * Capacity zero    → -1.0 (pure demand)
      * Demand zero      → +1.0 (pure capacity sitting idle)
      * Negative inputs  → treated as 0 (sensor noise / dirty data)
    """
    cap = max(0.0, float(capacity))
    dem = max(0.0, float(demand))
    denom = max(cap, dem)
    if denom <= 0:
        return 0.0
    return (cap - dem) / denom


def _band_for(abs_div: float) -> str:
    """Map an absolute divergence to its operator-facing band label."""
    label = DIVERGENCE_BANDS[0][1]
    for lower, candidate in DIVERGENCE_BANDS:
        if abs_div >= lower:
            label = candidate
        else:
            break
    return label


# ---------------------------------------------------------------------------
# Public — per-day point construction
# ---------------------------------------------------------------------------


def compute_route_divergence(
    *,
    route_id: str,
    date_iso: str,
    capacity_teu: float,
    demand_teu: float,
) -> RouteDivergencePoint:
    """Build one ``RouteDivergencePoint`` from raw inputs.

    Pure helper — callers build a list of these (one per day) and pass
    to :func:`summarize_persistent_divergence`.
    """
    return RouteDivergencePoint(
        route_id=str(route_id),
        date_iso=str(date_iso),
        capacity_teu=max(0.0, float(capacity_teu)),
        demand_teu=max(0.0, float(demand_teu)),
        divergence=_safe_divergence(capacity_teu, demand_teu),
    )


# ---------------------------------------------------------------------------
# Public — persistent divergence summarisation
# ---------------------------------------------------------------------------


def summarize_persistent_divergence(
    points: list[RouteDivergencePoint],
    *,
    persistence_threshold: float = 0.70,
    alert_band_floor: str = "stretched",
) -> RouteDivergenceReport:
    """Roll up a window of per-day points into one persistent assessment.

    "Persistent" means: the same-sign fraction across the window is at
    least ``persistence_threshold`` (default 0.70 = 7 of 10 days). A
    high mean divergence with random sign-flipping is just noise; the
    persistence rate filters that out.

    ``is_alert_worthy`` flips True when BOTH:
      * the absolute mean divergence reaches the ``alert_band_floor``
        band (default "stretched", i.e. ≥ 0.25), AND
      * the persistence rate is at least ``persistence_threshold``

    Returns a defensible report even when ``points`` is empty —
    every field is populated with sensible defaults.
    """
    if not points:
        return RouteDivergenceReport(
            route_id="",
            window_days=0, n_points=0,
            mean_divergence=0.0, abs_mean_divergence=0.0,
            divergence_band="balanced",
            persistence_rate=0.0, direction="balanced",
            is_alert_worthy=False,
            summary="(no data)",
            points=[],
        )

    route_id = points[0].route_id   # callers feed homogeneous slices
    n = len(points)
    divs = [p.divergence for p in points]
    mean_div = statistics.fmean(divs)
    abs_mean = abs(mean_div)
    band = _band_for(abs_mean)

    # Persistence — fraction of days whose sign matches the mean's sign.
    # Days with divergence == 0 are neutral and don't count against
    # persistence (they don't contradict the prevailing direction).
    if mean_div > 0:
        same_sign = sum(1 for d in divs if d > 0)
    elif mean_div < 0:
        same_sign = sum(1 for d in divs if d < 0)
    else:
        # Mean is exactly zero — persistence undefined; report as 0.
        same_sign = 0
    nonzero = sum(1 for d in divs if d != 0)
    persistence = (same_sign / nonzero) if nonzero else 0.0

    direction = (
        "capacity_surplus" if mean_div > 0
        else "demand_surplus" if mean_div < 0
        else "balanced"
    )

    # Alert worthiness — both magnitude AND persistence required.
    alert_floor_idx = next(
        (i for i, (_lo, lbl) in enumerate(DIVERGENCE_BANDS) if lbl == alert_band_floor),
        2,   # default to "stretched"
    )
    current_band_idx = next(
        (i for i, (_lo, lbl) in enumerate(DIVERGENCE_BANDS) if lbl == band),
        0,
    )
    is_alert_worthy = bool(
        current_band_idx >= alert_floor_idx
        and persistence >= float(persistence_threshold)
    )

    summary = (
        f"{route_id}: window={n}d, mean_div={mean_div:+.3f} ({band}), "
        f"persistence={persistence * 100:.0f}%, direction={direction}, "
        f"alert={'YES' if is_alert_worthy else 'no'}"
    )

    return RouteDivergenceReport(
        route_id=route_id,
        window_days=n, n_points=n,
        mean_divergence=mean_div, abs_mean_divergence=abs_mean,
        divergence_band=band,
        persistence_rate=persistence, direction=direction,
        is_alert_worthy=is_alert_worthy,
        summary=summary,
        points=list(points),
    )
