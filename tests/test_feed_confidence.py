"""Tests for processing.feed_confidence (per-feed confidence from provenance)."""
from __future__ import annotations

from processing.feed_confidence import (
    confidence_label,
    confidence_score,
    feed_confidence_report,
)


def test_real_and_fresh_is_full_confidence() -> None:
    assert confidence_score(1.0, 1.0) == 1.0


def test_real_but_stale_hits_the_floor() -> None:
    assert confidence_score(1.0, 0.0) == 0.6


def test_half_real_caps_at_half() -> None:
    assert confidence_score(0.5, 1.0) == 0.5


def test_confidence_score_clamps_inputs() -> None:
    assert confidence_score(-1.0, 2.0) == 0.0   # realness clamped to 0
    assert confidence_score(2.0, 2.0) == 1.0     # both clamped to 1


def test_confidence_label_buckets() -> None:
    assert confidence_label(0.9) == "high"
    assert confidence_label(0.5) == "medium"
    assert confidence_label(0.2) == "low"


def test_report_sorts_worst_first_with_overall() -> None:
    summary = {
        "n": 10, "realness_rate": 0.7, "freshness_rate": 0.8,
        "by_source": {
            "FRED": {"n": 5, "realness_rate": 1.0, "freshness_rate": 1.0,
                     "synthetic_rate": 0.0},
            "AIS": {"n": 5, "realness_rate": 0.2, "freshness_rate": 0.5,
                    "synthetic_rate": 0.8},
        },
    }
    rep = feed_confidence_report(summary)
    assert rep["by_source"][0].source == "AIS"      # worst confidence first
    assert rep["by_source"][0].label == "low"
    assert rep["by_source"][-1].source == "FRED"
    assert rep["by_source"][-1].label == "high"
    assert rep["overall"]["n"] == 10
    assert 0.0 <= rep["overall"]["confidence"] <= 1.0


def test_report_empty_is_honest() -> None:
    rep = feed_confidence_report({})
    assert rep["by_source"] == []
    assert rep["overall"]["confidence"] == 0.0
    assert rep["overall"]["label"] == "low"
