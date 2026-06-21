"""Calibrate conviction scores to realized hit probabilities (rec R033).

``disruption_cascade`` assigns a ``conviction_label`` (High/Moderate/Low/Watch)
from STATIC ``_CONVICTION_BANDS`` thresholds on a ``conviction_score`` in [0,1].
The score is a transparent weighted sum — but it has never been mapped to a
*measured* probability, so "High" carries no honest P(hit) behind it.

This fits a monotone calibration from the FROZEN, look-ahead-free ledger: each
marked signal contributes one (conviction_score, won?) pair, and an isotonic
regression maps raw score → realized P(hit). It exposes a reliability table
(binned score vs realized hit-rate), a per-label calibrated probability, and a
Brier score — the honest "what does this conviction actually mean" read.

It is a measurement LAYER: it does NOT mutate the cascade's band thresholds
(that would change live output). It quantifies them. Honesty-gated: below
``min_n`` marks it returns ``fitted=False`` and fabricates no curve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ReliabilityBin:
    """One row of the reliability table: a conviction-score band's realized hit-rate."""

    score_lo: float
    score_hi: float
    n: int
    mean_conviction: float
    realized_hit_rate: float
    calibrated_prob: float       # isotonic fit evaluated at mean_conviction


@dataclass
class ConvictionCalibration:
    """Realized calibration of conviction scores from the frozen ledger."""

    n: int
    fitted: bool
    bins: list = field(default_factory=list)        # list[ReliabilityBin]
    by_label: dict = field(default_factory=dict)    # label -> {n, realized_hit_rate, calibrated_prob}
    brier_score: float = 0.0                         # mean (calibrated - won)^2; lower better
    iso_x: list = field(default_factory=list)        # isotonic breakpoint scores
    iso_y: list = field(default_factory=list)        # calibrated prob at each iso_x
    generated_at: str = ""
    summary: str = ""


def _fit_isotonic(scores, wins):
    """Fit P(win | score), monotone non-decreasing. Returns (predict, iso_x, iso_y)."""
    from sklearn.isotonic import IsotonicRegression
    ir = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip", increasing=True)
    ir.fit(scores, wins)
    xs = sorted(set(float(s) for s in scores))
    ys = [float(v) for v in ir.predict(xs)]

    def _predict(value: float) -> float:
        return float(ir.predict([float(value)])[0])

    return _predict, xs, ys


def fit_calibration(
    scores: list,
    wins: list,
    labels: list | None = None,
    *,
    min_n: int = 20,
    n_bins: int = 5,
) -> ConvictionCalibration:
    """Fit the conviction→P(hit) calibration from (score, won?) pairs.

    Pure: takes the raw arrays so the math is testable without a ledger.
    ``wins`` entries are truthy/0-1. ``labels`` (optional, same length) drives
    the per-label breakdown. Returns ``fitted=False`` below ``min_n``.
    """
    # Clamp scores into the documented [0, 1] domain so an out-of-range score
    # can't silently drop out of every reliability bin (review LOW [8]).
    scores = [min(1.0, max(0.0, float(s))) for s in (scores or [])]
    wins = [1 if w else 0 for w in (wins or [])]
    n = min(len(scores), len(wins))
    scores, wins = scores[:n], wins[:n]
    labels = (list(labels)[:n] if labels else ["?"] * n)
    nb = max(1, int(n_bins))   # guards lo/hi division when n_bins <= 0 (review [14])

    now = datetime.now(timezone.utc).isoformat()
    if n < min_n:
        return ConvictionCalibration(
            n=n, fitted=False, generated_at=now,
            summary=(f"Only {n} marked signal(s) — need >= {min_n} to calibrate "
                     "conviction to a realized probability."),
        )

    predict, iso_x, iso_y = _fit_isotonic(scores, wins)
    cal = [predict(s) for s in scores]
    brier = sum((c - w) ** 2 for c, w in zip(cal, wins)) / n

    bins: list = []
    for i in range(nb):
        lo, hi = i / nb, (i + 1) / nb
        last = i == nb - 1
        idxs = [j for j, s in enumerate(scores)
                if s >= lo and (s < hi or (last and s <= hi))]
        if not idxs:
            continue
        bn = len(idxs)
        mc = sum(scores[j] for j in idxs) / bn
        hr = sum(wins[j] for j in idxs) / bn
        bins.append(ReliabilityBin(
            score_lo=round(lo, 4), score_hi=round(hi, 4), n=bn,
            mean_conviction=round(mc, 4), realized_hit_rate=round(hr, 4),
            calibrated_prob=round(predict(mc), 4),
        ))

    by_label: dict = {}
    grouped: dict = {}
    for s, w, lab in zip(scores, wins, labels):
        grouped.setdefault(str(lab), []).append((s, w))
    for lab, sw in grouped.items():
        bn = len(sw)
        hr = sum(w for _, w in sw) / bn
        mc = sum(s for s, _ in sw) / bn
        by_label[lab] = {
            "n": bn, "realized_hit_rate": round(hr, 4),
            "mean_conviction": round(mc, 4),
            "calibrated_prob": round(predict(mc), 4),
        }

    overall_hr = sum(wins) / n
    # The isotonic fit is monotone non-decreasing, but it can be DEGENERATE
    # (constant) when conviction carries no realized signal — don't claim a
    # mapping that isn't there (review MED [7]). And the Brier is IN-SAMPLE
    # (scored on the isotonic's own training data), so it is optimistic — say so
    # rather than presenting it as a clean predictive number (review MED [6]).
    degenerate = (max(iso_y) - min(iso_y)) < 1e-9 if iso_y else True
    mapping_clause = (
        "conviction does NOT separate hits (calibration is ~flat — the labels "
        "carry no realized signal yet)" if degenerate else
        "conviction maps monotonically to measured P(hit) — labels are quantified"
    )
    return ConvictionCalibration(
        n=n, fitted=True, bins=bins, by_label=by_label,
        brier_score=round(brier, 4), iso_x=[round(x, 4) for x in iso_x],
        iso_y=[round(y, 4) for y in iso_y], generated_at=now,
        summary=(
            f"Calibrated over {n} look-ahead-free marks: realized hit-rate "
            f"{overall_hr * 100:.0f}%, in-sample Brier {brier:.3f}. "
            f"{mapping_clause}."
        ),
    )


def calibrate_conviction(
    stock_data, *, min_n: int = 20, n_bins: int = 5,
) -> ConvictionCalibration:
    """Read the frozen, marked ledger and fit the conviction→P(hit) calibration.

    Each marked signal (frozen at issue, marked on strictly-later closes) is one
    causal (conviction_score, won?) observation. Never raises — an empty/unread
    ledger yields ``fitted=False``.
    """
    try:
        from state.signal_ledger import mark_ledger
        marks = mark_ledger(stock_data)
    except Exception:  # pragma: no cover - defensive
        from loguru import logger
        logger.exception("calibrate_conviction: ledger read failed")
        marks = []

    scores = [float(m.get("conviction_score") or 0.0) for m in marks]
    wins = [1 if (m.get("signed_return_pct") or 0.0) > 0 else 0 for m in marks]
    labels = [str(m.get("conviction_label") or "?") for m in marks]
    return fit_calibration(scores, wins, labels, min_n=min_n, n_bins=n_bins)
