"""Per-feed confidence score from the provenance ledger (AIS-research idea).

The AIS-intelligence literature is emphatic that uncertainty must be a
first-class output — every signal should carry how trustworthy its inputs were
(manually-entered AIS fields, spoofing, coverage gaps). Ship already records the
raw material: the per-fetch provenance ledger (R003,
``state.fetch_ledger.fetch_realness_summary``) gives, per source, the fraction of
fetches that were real (live/cache), fresh, and synthetic.

This collapses that into a single confidence score in [0, 1] per source + overall
— so the UI can downweight or flag a low-confidence feed instead of presenting it
as crisp fact. The blend weights are modeled assumptions (documented below); the
inputs are real.
"""
from __future__ import annotations

from dataclasses import dataclass


def _clamp01(x: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def confidence_score(realness_rate: float, freshness_rate: float) -> float:
    """Confidence in [0, 1] from a source's realness + freshness rates.

    ``realness_rate`` (fraction of fetches that were live/cache, i.e. NOT
    synthetic/empty/failed) dominates; freshness scales it down when the real
    data is stale. A fully-real, fully-fresh feed scores 1.0; fully-real but
    fully-stale scores 0.6; a feed that is half synthetic can't exceed 0.5.
    (Modeled weights — 0.6 floor on the freshness factor.)
    """
    real = _clamp01(realness_rate)
    fresh = _clamp01(freshness_rate)
    return round(real * (0.6 + 0.4 * fresh), 4)


def confidence_label(score: float) -> str:
    """Bucket a confidence score → "high" | "medium" | "low"."""
    s = _clamp01(score)
    return "high" if s >= 0.75 else ("medium" if s >= 0.4 else "low")


@dataclass(frozen=True)
class FeedConfidence:
    """One source's confidence read."""

    source: str
    confidence: float
    label: str
    realness_rate: float
    freshness_rate: float
    synthetic_rate: float
    n: int


def feed_confidence_report(summary: dict) -> dict:
    """Turn a ``fetch_realness_summary`` into per-source + overall confidence.

    ``summary`` is the dict from ``state.fetch_ledger.fetch_realness_summary``.
    Returns ``{"overall": {confidence, label, n}, "by_source": [FeedConfidence,
    …]}`` sorted by ascending confidence (worst feeds first — what an operator
    wants to see). An empty / shapeless summary yields a zeroed, honest report.
    """
    by_source_in = (summary or {}).get("by_source") or {}
    rows: list[FeedConfidence] = []
    for src, s in by_source_in.items():
        s = s or {}
        real = _clamp01(s.get("realness_rate", 0.0))
        fresh = _clamp01(s.get("freshness_rate", 0.0))
        synth = _clamp01(s.get("synthetic_rate", 0.0))
        score = confidence_score(real, fresh)
        rows.append(FeedConfidence(
            source=str(src), confidence=score, label=confidence_label(score),
            realness_rate=round(real, 4), freshness_rate=round(fresh, 4),
            synthetic_rate=round(synth, 4), n=int(s.get("n", 0) or 0)))
    rows.sort(key=lambda r: (r.confidence, r.source))

    overall_real = _clamp01((summary or {}).get("realness_rate", 0.0))
    overall_fresh = _clamp01((summary or {}).get("freshness_rate", 0.0))
    overall_score = confidence_score(overall_real, overall_fresh)
    return {
        "overall": {
            "confidence": overall_score,
            "label": confidence_label(overall_score),
            "n": int((summary or {}).get("n", 0) or 0),
        },
        "by_source": rows,
    }
