"""Defining-property tests for processing/news_sentiment_backtest.py."""
from __future__ import annotations

import pytest

from processing.news_sentiment_backtest import (
    SENTIMENT_LABELS,
    NewsSentimentBacktestReport,
    SentimentScorecard,
    backtest_news_sentiment,
    synthesize_news_history,
)


def test_sentiment_labels_constant() -> None:
    assert set(SENTIMENT_LABELS) == {"BULLISH", "BEARISH", "NEUTRAL"}


def test_synth_history_is_deterministic() -> None:
    a = synthesize_news_history(n_articles=30, seed=42)
    b = synthesize_news_history(n_articles=30, seed=42)
    assert a == b


def test_synth_history_row_count() -> None:
    rows = synthesize_news_history(n_articles=55)
    assert len(rows) == 55


def test_synth_history_required_keys() -> None:
    rows = synthesize_news_history(n_articles=20)
    required = {"sentiment_label", "sentiment_score",
                "realized_forward_rate_move_pct"}
    for row in rows:
        assert required <= set(row.keys())
        assert row["sentiment_label"] in SENTIMENT_LABELS
        assert -1.0 <= row["sentiment_score"] <= 1.0


def test_backtest_returns_one_scorecard_per_label() -> None:
    report = backtest_news_sentiment()
    assert isinstance(report, NewsSentimentBacktestReport)
    assert {sc.sentiment for sc in report.scorecards} == set(SENTIMENT_LABELS)


def test_backtest_uses_synth_when_history_empty() -> None:
    a = backtest_news_sentiment(history=None)
    b = backtest_news_sentiment(history=[])
    assert a.n_observations > 0
    assert b.n_observations > 0


def test_hit_rates_in_unit_interval() -> None:
    report = backtest_news_sentiment()
    for sc in report.scorecards:
        assert 0.0 <= sc.directional_hit_rate <= 1.0
        assert abs(sc.edge_vs_baseline - (sc.directional_hit_rate - 0.5)) < 1e-9


def test_neutral_pins_at_half() -> None:
    report = backtest_news_sentiment()
    neutral = next(sc for sc in report.scorecards if sc.sentiment == "NEUTRAL")
    assert neutral.directional_hit_rate == 0.5


def test_perfect_signal_quality_flips_calibrated_true() -> None:
    report = backtest_news_sentiment(signal_quality=1.0)
    assert report.sentiment_calibrated is True
    assert report.spread_bullish_vs_bearish > 0.01


def test_zero_signal_quality_pins_means_near_zero() -> None:
    report = backtest_news_sentiment(signal_quality=0.0)
    by_l = {sc.sentiment: sc for sc in report.scorecards}
    assert abs(by_l["BULLISH"].mean_forward_rate_move_pct) < 0.015
    assert abs(by_l["BEARISH"].mean_forward_rate_move_pct) < 0.015


def test_backtest_is_deterministic_across_runs() -> None:
    a = backtest_news_sentiment(seed=7)
    b = backtest_news_sentiment(seed=7)
    a_keys = {(sc.sentiment, round(sc.mean_forward_rate_move_pct, 6))
              for sc in a.scorecards}
    b_keys = {(sc.sentiment, round(sc.mean_forward_rate_move_pct, 6))
              for sc in b.scorecards}
    assert a_keys == b_keys
    assert a.summary == b.summary


def test_hand_built_history_yields_exact_arithmetic() -> None:
    history = [
        {"sentiment_label": "BULLISH",
         "realized_forward_rate_move_pct":  0.04},
        {"sentiment_label": "BULLISH",
         "realized_forward_rate_move_pct":  0.02},
        {"sentiment_label": "BEARISH",
         "realized_forward_rate_move_pct": -0.05},
        {"sentiment_label": "NEUTRAL",
         "realized_forward_rate_move_pct":  0.0},
    ]
    report = backtest_news_sentiment(history=history)
    by_l = {sc.sentiment: sc for sc in report.scorecards}
    assert abs(by_l["BULLISH"].mean_forward_rate_move_pct - 0.03) < 1e-9
    assert by_l["BULLISH"].directional_hit_rate == 1.0
    assert by_l["BEARISH"].directional_hit_rate == 1.0
    assert by_l["NEUTRAL"].directional_hit_rate == 0.5
    assert report.sentiment_calibrated is True
