"""processing/cargo_flow_anomaly.py — detect rare commodity surges per route.

A route's cargo mix is normally stable day to day — e.g. transpacific
EB averages ~38% electronics / ~22% machinery / ~18% apparel. When the
mix flips (electronics drops to 5% while agriculture jumps to 30%),
that's a signal: producers diverted volume, a typhoon shut down a
port, or a single bulk charter showed up in the trade-data sample.

This module compares a current cargo-mix snapshot against a trailing
window of historical mixes and flags categories whose share moved
materially. Two complementary signals:

  * **Per-category jump** — categories whose share rose by more than
    ``jump_threshold`` percentage points vs the trailing median.
  * **Distribution drift** — Jensen-Shannon divergence between today's
    mix and the trailing median mix. A single composite that captures
    "the whole pie reshuffled" even when no single category dominates
    the move.

Pure-function — no I/O. The caller passes in the trailing window of
mixes (e.g. from snapshot history). Tests inject synthetic mixes to
verify both the per-category jump detection and the JSD math.

Bands (drift_band):
  0.00 → 0.05  →  "stable"          (everyday noise)
  0.05 → 0.15  →  "elevated"        (worth a quick look)
  0.15 → 0.30  →  "anomalous"       (real shift)
  0.30 → 1.00  →  "shock"           (mix has been transformed)
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional


__all__ = [
    "CARGO_DRIFT_BANDS",
    "CategoryJump",
    "CargoFlowAnomalyReport",
    "compute_cargo_flow_anomaly",
    "jensen_shannon_divergence",
]


# Lower-bound inclusive bands. Tuple of (lower_bound, band_label).
CARGO_DRIFT_BANDS: list[tuple[float, str]] = [
    (0.00, "stable"),
    (0.05, "elevated"),
    (0.15, "anomalous"),
    (0.30, "shock"),
]


# Module-level defaults; callers override per-call to tune sensitivity.
DEFAULT_JUMP_THRESHOLD_PP: float = 10.0   # percentage points
DEFAULT_JSD_ELEVATED: float = 0.05
DEFAULT_TRAILING_WINDOW: int = 14         # days


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CategoryJump:
    """One category whose share moved materially vs the trailing baseline.

    ``delta_pp`` is signed — positive = the category SURGED (more share
    today than the trailing median); negative = the category COLLAPSED.
    Either direction is worth flagging since both signal a real shift
    in the underlying trade flow.
    """

    category: str
    today_share: float        # in [0, 1]
    trailing_median: float    # in [0, 1]
    delta_pp: float           # signed; in percentage points
    direction: str            # "surge" | "collapse"


@dataclass
class CargoFlowAnomalyReport:
    """Per-route anomaly assessment."""

    route_id: str
    today_mix: dict[str, float] = field(default_factory=dict)
    trailing_median_mix: dict[str, float] = field(default_factory=dict)
    jsd: float = 0.0                    # Jensen-Shannon divergence
    drift_band: str = "stable"
    surges: list[CategoryJump] = field(default_factory=list)
    collapses: list[CategoryJump] = field(default_factory=list)
    is_anomaly: bool = False
    summary: str = ""


# ---------------------------------------------------------------------------
# Distance metric — Jensen-Shannon divergence
# ---------------------------------------------------------------------------


def _normalize_to_distribution(d: dict[str, float]) -> dict[str, float]:
    """Re-scale a mapping of category → share so it sums to 1.0.

    Returns an empty dict if the input is empty or all-zero — caller
    short-circuits on empty distributions to avoid div-by-zero.
    """
    total = sum(max(0.0, float(v)) for v in d.values())
    if total <= 0:
        return {}
    return {k: max(0.0, float(v)) / total for k, v in d.items()}


def jensen_shannon_divergence(
    p: dict[str, float], q: dict[str, float],
) -> float:
    """Jensen-Shannon divergence between two categorical distributions.

    Returns a value in [0, 1] (log base 2 normalization). 0 means
    identical distributions; 1 means maximally different (disjoint
    supports). Symmetric and bounded — preferred over raw KL for this
    use case because we don't want infinities when categories appear
    in one distribution but not the other.

    Both inputs are normalized to sum to 1.0 first so the caller can
    pass raw share dicts without worrying about totals.
    """
    p_norm = _normalize_to_distribution(p)
    q_norm = _normalize_to_distribution(q)
    if not p_norm or not q_norm:
        return 0.0

    # Build the union support; missing categories contribute 0.
    keys = set(p_norm.keys()) | set(q_norm.keys())
    m = {k: 0.5 * (p_norm.get(k, 0.0) + q_norm.get(k, 0.0)) for k in keys}

    def _kl(a: dict[str, float], b: dict[str, float]) -> float:
        s = 0.0
        for k, pa in a.items():
            if pa <= 0:
                continue
            pb = b.get(k, 0.0)
            if pb <= 0:
                # Guard — should not happen since m is the average of
                # two non-negative distributions and includes every key
                # of a. Keep the guard for robustness against floating-
                # point edge cases.
                continue
            s += pa * math.log(pa / pb, 2)
        return s

    return 0.5 * _kl(p_norm, m) + 0.5 * _kl(q_norm, m)


def _drift_band(jsd: float) -> str:
    """Map a JSD value to its operator-facing band label."""
    label = CARGO_DRIFT_BANDS[0][1]
    for lower, candidate in CARGO_DRIFT_BANDS:
        if jsd >= lower:
            label = candidate
        else:
            break
    return label


# ---------------------------------------------------------------------------
# Per-category jump detection
# ---------------------------------------------------------------------------


def _trailing_median_mix(
    history: list[dict[str, float]],
) -> dict[str, float]:
    """Per-category median across the trailing window of mixes.

    Each mix is first normalized to a distribution so a single
    unusually large total day doesn't dominate the median. Categories
    missing from a given day contribute 0 for that day.
    """
    if not history:
        return {}
    normed = [_normalize_to_distribution(m) for m in history]
    keys: set[str] = set()
    for m in normed:
        keys.update(m.keys())
    out: dict[str, float] = {}
    for k in keys:
        values = [m.get(k, 0.0) for m in normed]
        out[k] = statistics.median(values)
    return out


def _detect_jumps(
    today: dict[str, float],
    trailing_median: dict[str, float],
    jump_threshold_pp: float,
) -> tuple[list[CategoryJump], list[CategoryJump]]:
    """Identify per-category surges + collapses crossing the threshold."""
    today_norm = _normalize_to_distribution(today)
    surges: list[CategoryJump] = []
    collapses: list[CategoryJump] = []
    threshold_share = jump_threshold_pp / 100.0
    all_keys = set(today_norm.keys()) | set(trailing_median.keys())
    for k in all_keys:
        ts = today_norm.get(k, 0.0)
        tm = trailing_median.get(k, 0.0)
        delta = ts - tm   # signed
        if delta >= threshold_share:
            surges.append(CategoryJump(
                category=k, today_share=ts, trailing_median=tm,
                delta_pp=delta * 100.0, direction="surge",
            ))
        elif -delta >= threshold_share:
            collapses.append(CategoryJump(
                category=k, today_share=ts, trailing_median=tm,
                delta_pp=delta * 100.0, direction="collapse",
            ))
    surges.sort(key=lambda j: j.delta_pp, reverse=True)
    collapses.sort(key=lambda j: j.delta_pp)   # most negative first
    return surges, collapses


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_cargo_flow_anomaly(
    *,
    route_id: str,
    today_mix: dict[str, float],
    history: list[dict[str, float]],
    jump_threshold_pp: float = DEFAULT_JUMP_THRESHOLD_PP,
    jsd_elevated_threshold: float = DEFAULT_JSD_ELEVATED,
    trailing_window: int = DEFAULT_TRAILING_WINDOW,
) -> CargoFlowAnomalyReport:
    """Detect rare commodity surges / collapses on the given route.

    ``history`` is a list of per-day cargo mixes (oldest-first or
    newest-first — order doesn't matter; the median is symmetric).
    Only the last ``trailing_window`` entries are used.

    ``is_anomaly`` flips True when either:
      * the JSD exceeds ``jsd_elevated_threshold``, OR
      * at least one per-category jump crosses ``jump_threshold_pp``

    Both signals are emitted independently in the report so the caller
    can decide which to surface.

    Edge cases (all return a valid report — never raises):
      * Empty history             → JSD=0, no jumps, is_anomaly=False
                                    (no baseline to compare against)
      * Empty today_mix           → JSD=0, no jumps, is_anomaly=False
      * History but all zero      → JSD=0, no jumps, is_anomaly=False
      * Negative trailing_window  → clamped to 1
    """
    window = max(1, int(trailing_window))
    trailing = list(history)[-window:]
    trailing_median = _trailing_median_mix(trailing)
    today_norm = _normalize_to_distribution(today_mix)

    # Short-circuit on degenerate input — keep the field shape stable
    # so the consumer never has to None-check.
    if not today_norm or not trailing_median:
        return CargoFlowAnomalyReport(
            route_id=route_id,
            today_mix=dict(today_norm),
            trailing_median_mix=dict(trailing_median),
            jsd=0.0,
            drift_band="stable",
            is_anomaly=False,
            summary=f"{route_id}: insufficient data for anomaly assessment",
        )

    jsd = jensen_shannon_divergence(today_norm, trailing_median)
    band = _drift_band(jsd)
    surges, collapses = _detect_jumps(
        today_norm, trailing_median, jump_threshold_pp=jump_threshold_pp,
    )
    is_anomaly = (
        jsd >= float(jsd_elevated_threshold)
        or bool(surges) or bool(collapses)
    )

    summary_pieces: list[str] = [
        f"{route_id}: JSD={jsd:.3f} ({band})",
    ]
    if surges:
        summary_pieces.append(
            f"surges: " + ", ".join(
                f"{j.category} +{j.delta_pp:.1f}pp" for j in surges[:3]
            )
        )
    if collapses:
        summary_pieces.append(
            f"collapses: " + ", ".join(
                f"{j.category} {j.delta_pp:.1f}pp" for j in collapses[:3]
            )
        )

    return CargoFlowAnomalyReport(
        route_id=route_id,
        today_mix=dict(today_norm),
        trailing_median_mix=dict(trailing_median),
        jsd=jsd,
        drift_band=band,
        surges=surges,
        collapses=collapses,
        is_anomaly=is_anomaly,
        summary="; ".join(summary_pieces),
    )
