"""processing/snapshot_diff_anomaly.py — diff-magnitude anomaly detector.

On a normal day, the per-day snapshot diff produced by
``tools.port_supply_diff.compare_snapshots`` carries a handful of small
entries per bucket (a couple of severity shifts, one or two deficit
moves, etc.). On a *shock day* — Suez closure, major typhoon, sudden
sanction regime change — the diff explodes: dozens of severity shifts,
multi-port deficit cascades, wholesale ticker reshuffles.

This module reduces a diff to a single composite magnitude and then
scores today's magnitude against the trailing N-day distribution. The
output is a banded signal ("normal" / "elevated" / "shock") plus a
plain-English explanation that the supply-shock digest agent can drop
straight into its HTML.

Why median + MAD instead of mean + stdev:
  Mean and stdev are catastrophically sensitive to the very events
  we're trying to detect. A single Suez-class shock in the trailing
  window would inflate both, pushing the threshold so high that the
  *next* shock looks normal. MAD (median absolute deviation) is robust
  — adding one outlier to a 30-day window barely moves the median or
  the MAD, so the detector keeps catching subsequent shocks.

  The scale factor 1.4826 converts MAD to a stdev-equivalent under
  the normal-distribution assumption, so the resulting z-score is
  directly comparable to a Gaussian z (|z|>3 ≈ 99.7th percentile).

Pure-function contract:
  * No I/O outside the existing snapshot helpers in
    ``processing.port_supply_history`` (save/load wrappers).
  * No new dependencies — ``statistics`` from stdlib only.
  * Wiring into delivery channels happens elsewhere.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


__all__ = [
    "DiffMagnitudeRecord",
    "AnomalyScore",
    "WEIGHT_SEVERITY_SHIFT",
    "WEIGHT_ENTERED_DEFICIT",
    "WEIGHT_EXITED_DEFICIT",
    "WEIGHT_DEFICIT_MOVE",
    "WEIGHT_TICKER_SHUFFLE",
    "MAD_TO_STDEV",
    "compute_diff_magnitude",
    "score_anomaly",
    "build_history_from_snapshots",
]


# ---------------------------------------------------------------------------
# Weighting constants — exposed for tests + downstream consumers
# ---------------------------------------------------------------------------

# Severity-band shifts are the most operationally material change a port
# can make overnight (e.g. "normal → critical"). Weight them highest.
WEIGHT_SEVERITY_SHIFT: float = 3.0

# Entering deficit (was > 0d, now <= 0d) is a meaningful watchlist event
# — a port just flipped into supply pressure. Mid-weight.
WEIGHT_ENTERED_DEFICIT: float = 2.0

# Exiting deficit is good news but still a state change worth counting.
WEIGHT_EXITED_DEFICIT: float = 1.0

# Generic deficit-day moves (above the comparator's --min-delta) — every
# such port shows up in the deficit_moves bucket. Lower weight because
# the bucket overlaps with severity shifts (a severity shift always
# implies a deficit move, but not vice versa).
WEIGHT_DEFICIT_MOVE: float = 1.0

# Top-ticker reshuffles are the noisiest signal — equity-side flows shift
# day-to-day and don't always imply a supply-side event. Lowest weight.
WEIGHT_TICKER_SHUFFLE: float = 0.5


# Scale factor to convert MAD to a stdev-equivalent under the normal
# distribution. Used in the z-score denominator so the resulting z is
# directly comparable to a Gaussian z (|z|>3 ≈ 99.7th percentile).
MAD_TO_STDEV: float = 1.4826


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class DiffMagnitudeRecord:
    """One day's compressed diff signature.

    Holds the raw per-bucket counts (useful for downstream attribution —
    "today's score was driven by 12 severity shifts") plus the single
    composite magnitude that the anomaly detector actually consumes.
    """

    date_iso: str
    severity_shifts: int
    entered_deficit: int
    exited_deficit: int
    deficit_moves: int
    ticker_shuffles: int
    composite_magnitude: float


@dataclass
class AnomalyScore:
    """Result of scoring one DiffMagnitudeRecord against history.

    ``mad_z_score`` is the canonical signal — bands are derived from it.
    ``explanation`` is a one-liner suitable for dropping into a digest
    email or HTML report ("today X, trailing median Y, ...").
    """

    date_iso: str
    composite_magnitude: float
    rolling_median: float
    rolling_mad: float
    mad_z_score: float
    is_anomaly: bool
    anomaly_band: str   # "normal" | "elevated" | "shock"
    explanation: str


# ---------------------------------------------------------------------------
# Composite magnitude — reduce a DiffReport to one number
# ---------------------------------------------------------------------------


def compute_diff_magnitude(diff: object) -> DiffMagnitudeRecord:
    """Reduce a DiffReport (from ``tools.port_supply_diff``) to a single
    composite magnitude using the module-level weights.

    The composite is a weighted sum of the five per-bucket counts:

        severity_shifts × 3.0
        entered_deficit × 2.0
        exited_deficit  × 1.0
        deficit_moves   × 1.0
        ticker_shuffles × 0.5

    The date_iso field is intentionally left empty here — the caller
    knows which day the diff belongs to (the snapshot loader passes the
    date in). Use :func:`build_history_from_snapshots` for the wired-up
    path where dates are populated.

    Defensive against missing/None buckets — a partial DiffReport (e.g.
    one with only severity_shifts populated) still yields a valid
    record with zeros for the absent buckets.
    """
    severity = _count(getattr(diff, "severity_shifts", None))
    entered = _count(getattr(diff, "entered_deficit", None))
    exited = _count(getattr(diff, "exited_deficit", None))
    moves = _count(getattr(diff, "deficit_moves", None))
    shuffles = _count(getattr(diff, "ticker_shuffles", None))

    composite = (
        severity * WEIGHT_SEVERITY_SHIFT
        + entered * WEIGHT_ENTERED_DEFICIT
        + exited * WEIGHT_EXITED_DEFICIT
        + moves * WEIGHT_DEFICIT_MOVE
        + shuffles * WEIGHT_TICKER_SHUFFLE
    )

    return DiffMagnitudeRecord(
        date_iso="",
        severity_shifts=severity,
        entered_deficit=entered,
        exited_deficit=exited,
        deficit_moves=moves,
        ticker_shuffles=shuffles,
        composite_magnitude=float(composite),
    )


def _count(bucket: object) -> int:
    """Safe len() for a possibly-None bucket. Returns 0 when bucket is
    missing or unlen-able."""
    if bucket is None:
        return 0
    try:
        return int(len(bucket))
    except TypeError:
        return 0


# ---------------------------------------------------------------------------
# MAD z-score + banding
# ---------------------------------------------------------------------------


def _median_absolute_deviation(
    values: list[float],
    median_value: float,
) -> float:
    """Median of absolute deviations from the median.

    Returns 0.0 for an empty or single-value list (no spread to measure).
    Caller is responsible for handling the zero-MAD case (the z-score
    denominator goes to zero — see :func:`score_anomaly`).
    """
    if len(values) < 2:
        return 0.0
    return statistics.median([abs(v - median_value) for v in values])


def _band_for(z: float, z_threshold: float) -> str:
    """Map an absolute z-score to a banded label.

    Bands:
      * |z| ≤ 1                       → "normal"
      * 1 < |z| ≤ z_threshold (3.0)   → "elevated"
      * |z| > z_threshold             → "shock"

    The 1.0 lower boundary is a fixed "still inside one stdev-equivalent"
    line; ``z_threshold`` is the configurable shock cutoff (default 3.0,
    which under Gaussian assumptions is the 99.7th percentile).
    """
    az = abs(z)
    if az <= 1.0:
        return "normal"
    if az <= z_threshold:
        return "elevated"
    return "shock"


def score_anomaly(
    today_record: DiffMagnitudeRecord,
    history: list[DiffMagnitudeRecord],
    *,
    window_days: int = 30,
    z_threshold: float = 3.0,
) -> AnomalyScore:
    """Score today's diff magnitude against a trailing-window history.

    Computes the rolling median + MAD over the last ``window_days``
    records (or the full history if shorter), then z-scores today's
    composite against that distribution. ``z = (today - median) /
    (1.4826 * MAD)``. The 1.4826 factor converts MAD to a stdev-
    equivalent so |z|>3 is roughly the Gaussian 99.7th percentile.

    Banded output:
      * |z| ≤ 1               → "normal"
      * 1 < |z| ≤ z_threshold → "elevated"
      * |z| > z_threshold     → "shock"

    Edge cases handled defensively:
      * Empty history          → z=0, band="normal", explanation says so
      * Window clamping        → only the last ``window_days`` used even
                                  if ``history`` is longer
      * MAD == 0 (flat history) → if today equals the median, z=0; if
                                   today differs, z=±inf-equivalent and
                                   the band promotes to "shock" outright
                                   (any deviation from a perfectly flat
                                   baseline IS the signal)

    The ``explanation`` field is a single sentence the digest agent can
    drop straight into HTML — no further formatting required.
    """
    # ── Empty-history short-circuit ───────────────────────────────────
    if not history:
        return AnomalyScore(
            date_iso=today_record.date_iso,
            composite_magnitude=today_record.composite_magnitude,
            rolling_median=0.0,
            rolling_mad=0.0,
            mad_z_score=0.0,
            is_anomaly=False,
            anomaly_band="normal",
            explanation=(
                "no history available — anomaly score deferred until "
                "the trailing window populates"
            ),
        )

    # ── Window clamping — only use the last N records ─────────────────
    window = max(1, int(window_days))
    trailing = list(history)[-window:]
    values = [float(r.composite_magnitude) for r in trailing]

    median_value = statistics.median(values)
    mad = _median_absolute_deviation(values, median_value)
    today_value = float(today_record.composite_magnitude)

    # ── Z-score with MAD == 0 safeguard ───────────────────────────────
    # When the trailing window is perfectly flat, MAD is 0 and the naive
    # denominator goes to zero. Two cases:
    #   * today exactly matches the flat baseline → z=0, band=normal
    #   * today differs at all → any deviation IS the signal; emit a
    #     sentinel "shock" band rather than dividing by zero.
    denom = MAD_TO_STDEV * mad
    if denom == 0.0:
        if today_value == median_value:
            z = 0.0
            band = "normal"
        else:
            # Any non-zero deviation from a flat baseline is anomalous.
            # Encode as a large finite z so downstream formatters work.
            sign = 1.0 if today_value > median_value else -1.0
            z = sign * 999.0
            band = "shock"
    else:
        z = (today_value - median_value) / denom
        band = _band_for(z, z_threshold)

    is_anomaly = band != "normal"

    explanation = _build_explanation(
        today_value=today_value,
        median_value=median_value,
        mad=mad,
        z=z,
        band=band,
        n_history=len(values),
    )

    return AnomalyScore(
        date_iso=today_record.date_iso,
        composite_magnitude=today_value,
        rolling_median=float(median_value),
        rolling_mad=float(mad),
        mad_z_score=float(z),
        is_anomaly=is_anomaly,
        anomaly_band=band,
        explanation=explanation,
    )


def _build_explanation(
    *,
    today_value: float,
    median_value: float,
    mad: float,
    z: float,
    band: str,
    n_history: int,
) -> str:
    """Compose the one-liner human explanation.

    Format: ``today X, trailing median Y over N days, MAD Z; X is N MADs
    above/below median — band``. Used verbatim in the supply-shock
    digest HTML."""
    if mad == 0.0:
        spread_clause = "trailing window is perfectly flat (MAD=0)"
    else:
        direction = "above" if today_value >= median_value else "below"
        spread_clause = (
            f"today is {abs(z):.2f} MADs {direction} median "
            f"(MAD={mad:.2f})"
        )

    return (
        f"today {today_value:.2f}, trailing median {median_value:.2f} "
        f"over {n_history} day(s); {spread_clause} — {band}"
    )


# ---------------------------------------------------------------------------
# History builder — walk snapshots in a date range
# ---------------------------------------------------------------------------


def build_history_from_snapshots(
    *,
    container_type: str = "40FT_DRY",
    root: Path | None = None,
    today: date,
    window_days: int = 30,
) -> list[DiffMagnitudeRecord]:
    """Walk snapshots in the trailing window + compute per-day diffs.

    For each day D in ``(today - window_days, today)``, compares the
    snapshot on D against the snapshot on D-1 and produces a
    DiffMagnitudeRecord with ``date_iso=D``. Days missing either side
    of the pair are silently skipped (the diff just isn't available
    for that day).

    Returns the resulting records in chronological order (oldest first),
    EXCLUDING today itself — the caller scores today separately via
    :func:`compute_diff_magnitude` + :func:`score_anomaly`.

    Pure-function contract: the only I/O is through
    :func:`processing.port_supply_history.load_snapshot`, which is the
    public load helper. No direct filesystem access.
    """
    # Lazy import — keeps this module import-cheap for callers that only
    # want compute_diff_magnitude + score_anomaly (the pure analytical
    # surface).
    from processing.port_supply_history import load_snapshot
    from tools.port_supply_diff import compare_snapshots

    window = max(1, int(window_days))
    records: list[DiffMagnitudeRecord] = []

    # Walk from oldest to newest, stopping BEFORE today. For each
    # candidate day D in [today - window, today), we need the snapshot
    # on D AND the snapshot on D-1 to compute the diff.
    for delta in range(window, 0, -1):
        target = today - timedelta(days=delta)
        prior = target - timedelta(days=1)
        try:
            before_rows = load_snapshot(
                prior, container_type=container_type, root=root,
            )
            after_rows = load_snapshot(
                target, container_type=container_type, root=root,
            )
        except FileNotFoundError:
            # Missing snapshot on either side — silently skip. The
            # caller's trailing window just gets fewer records.
            continue

        diff = compare_snapshots(before_rows, after_rows)
        rec = compute_diff_magnitude(diff)
        rec.date_iso = target.isoformat()
        records.append(rec)

    return records
