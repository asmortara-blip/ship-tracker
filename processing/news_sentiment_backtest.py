"""news_sentiment_backtest.py — keyword-based sentiment predictiveness backtest.

The ``processing.news_sentiment`` module scores each shipping article
with a bearish/bullish keyword-based ``sentiment_score`` and a
``sentiment_label`` (BULLISH / BEARISH / NEUTRAL). Nothing in the
platform asked: **do BULLISH articles actually lead to higher freight
rates than BEARISH ones in the days that follow?**

For each sentiment class the scorecard reports:

  * **mean_forward_rate_move_pct** — average signed forward freight-rate
                                     move that materialized
  * **directional_hit_rate**       — fraction of windows in the signal's
                                     favour (NEUTRAL pinned to 0.5)
  * **edge_vs_baseline**           — directional_hit_rate - 0.5

Plus a roll-up ``sentiment_calibrated`` flag — True when BULLISH has
positive mean forward move AND BEARISH has negative.

A deterministic synthetic generator with a ``signal_quality`` knob
powers the tests; the load-bearing property tests pin both ends.

Transparent rule-based scorecard — no fitted ML, no opaque weights.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable

from data.quality import DataSource


__all__ = [
    "SENTIMENT_LABELS",
    "SentimentScorecard",
    "NewsSentimentBacktestReport",
    "synthesize_news_history",
    "backtest_news_sentiment",
    "NEWS_SENTIMENT_BACKTEST_SOURCE",
]


SENTIMENT_LABELS: tuple[str, ...] = ("BEARISH", "NEUTRAL", "BULLISH")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SentimentScorecard:
    """Per-sentiment-class scorecard."""

    sentiment: str
    n_observations: int
    mean_forward_rate_move_pct: float
    directional_hit_rate: float  # in [0, 1]; NEUTRAL pinned to 0.5
    edge_vs_baseline: float


@dataclass
class NewsSentimentBacktestReport:
    scorecards: list[SentimentScorecard] = field(default_factory=list)
    n_observations: int = 0
    sentiment_calibrated: bool = False    # BULLISH mean > 0 AND BEARISH mean < 0
    spread_bullish_vs_bearish: float = 0.0
    source: DataSource | None = None
    summary: str = ""


NEWS_SENTIMENT_BACKTEST_SOURCE = DataSource.modeled(
    "News Sentiment Backtest",
    notes=(
        "Per-sentiment-class scorecard for processing.news_sentiment. "
        "Mean forward freight-rate move + directional hit rate per class."
    ),
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _directional_hit_rate(sentiment: str, returns: list[float]) -> float:
    if not returns:
        return 0.5
    upper = sentiment.upper()
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


def synthesize_news_history(
    *,
    n_articles: int = 240,
    signal_quality: float = 0.80,
    seed: int = 20260525,
) -> list[dict]:
    """Deterministic synthetic news-article history.

    Each row is a dict with:
      * ``sentiment_label``              — one of SENTIMENT_LABELS
      * ``sentiment_score``              — signed score in [-1, +1]
      * ``realized_forward_rate_move_pct`` — signed forward rate move

    The relationship between the sentiment and the realized move is
    seeded by ``signal_quality``. The label is drawn from the score
    (BULLISH if > +0.2, BEARISH if < -0.2, else NEUTRAL).
    """
    rng = random.Random(seed)
    q = max(0.0, min(1.0, float(signal_quality)))
    rows: list[dict] = []
    for _ in range(n_articles):
        # Sentiment score uniformly distributed over [-1, +1]
        score = rng.uniform(-1.0, 1.0)
        if score > 0.2:
            label = "BULLISH"
        elif score < -0.2:
            label = "BEARISH"
        else:
            label = "NEUTRAL"
        # Realized rate move = q * score * 0.04 + noise. q=1 → tight
        # alignment with sentiment; q=0 → pure noise.
        signal_part = q * score * 0.04
        noise = rng.gauss(0.0, 0.02) * (1.0 - q * 0.6)
        realized = signal_part + noise
        rows.append({
            "sentiment_label":                 label,
            "sentiment_score":                 round(score, 4),
            "realized_forward_rate_move_pct":  round(realized, 6),
        })
    return rows


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def backtest_news_sentiment(
    history: Iterable[dict] | None = None,
    *,
    seed: int = 20260525,
    signal_quality: float = 0.80,
) -> NewsSentimentBacktestReport:
    """Score news-sentiment labels against realized forward freight-rate moves."""
    rows = list(history or [])
    if not rows:
        rows = synthesize_news_history(seed=seed, signal_quality=signal_quality)

    by_label: dict[str, list[float]] = {s: [] for s in SENTIMENT_LABELS}
    for row in rows:
        label = str((row or {}).get("sentiment_label", "") or "")
        ret = float((row or {}).get("realized_forward_rate_move_pct", 0.0) or 0.0)
        if label in by_label:
            by_label[label].append(ret)

    scorecards: list[SentimentScorecard] = []
    for label in SENTIMENT_LABELS:
        returns = by_label[label]
        mean_ret = (sum(returns) / len(returns)) if returns else 0.0
        hit = _directional_hit_rate(label, returns)
        scorecards.append(SentimentScorecard(
            sentiment=label,
            n_observations=len(returns),
            mean_forward_rate_move_pct=mean_ret,
            directional_hit_rate=hit,
            edge_vs_baseline=hit - 0.5,
        ))

    by_l = {sc.sentiment: sc for sc in scorecards}
    calibrated = (
        by_l["BULLISH"].mean_forward_rate_move_pct > 0
        and by_l["BEARISH"].mean_forward_rate_move_pct < 0
    )
    spread = (
        by_l["BULLISH"].mean_forward_rate_move_pct
        - by_l["BEARISH"].mean_forward_rate_move_pct
    )

    summary = (
        f"Across {len(rows)} articles: BULLISH mean = "
        f"{by_l['BULLISH'].mean_forward_rate_move_pct * 100:+.2f}% vs "
        f"BEARISH mean = "
        f"{by_l['BEARISH'].mean_forward_rate_move_pct * 100:+.2f}% "
        f"(spread {spread * 100:+.2f}pp); calibrated: {calibrated}."
        if rows else "No news-sentiment history available."
    )

    return NewsSentimentBacktestReport(
        scorecards=scorecards,
        n_observations=len(rows),
        sentiment_calibrated=calibrated,
        spread_bullish_vs_bearish=spread,
        source=NEWS_SENTIMENT_BACKTEST_SOURCE,
        summary=summary,
    )
