"""Tests for engine.insight — Insight dataclass + make_insight factory."""
from __future__ import annotations

import pytest

from engine.insight import INSIGHT_ACTIONS, INSIGHT_CATEGORIES, Insight, make_insight
from engine.signals import SignalComponent


def _sample_signals() -> list[SignalComponent]:
    return [
        SignalComponent(name="BDI", value=0.8, weight=0.3,
                        label="Above 90d avg", direction="bullish"),
        SignalComponent(name="PMI", value=0.6, weight=0.2,
                        label="Expanding", direction="bullish"),
    ]


# ─── Module-level constants ────────────────────────────────────────────────

def test_insight_categories_list() -> None:
    assert "PORT_DEMAND" in INSIGHT_CATEGORIES
    assert "ROUTE" in INSIGHT_CATEGORIES
    assert "MACRO" in INSIGHT_CATEGORIES
    assert "CONVERGENCE" in INSIGHT_CATEGORIES


def test_insight_actions_dict_has_expected_keys() -> None:
    for key in ("strong_bullish", "mild_bullish", "neutral",
                "mild_bearish", "strong_bearish"):
        assert key in INSIGHT_ACTIONS


# ─── make_insight: action verb mapping by score tier ───────────────────────

def test_make_insight_prioritize_at_high_score() -> None:
    ins = make_insight(
        title="t", category="MACRO", score=0.85,
        detail="d", signals=_sample_signals(),
    )
    assert ins.action == "Prioritize"


def test_make_insight_monitor_at_upper_mid_score() -> None:
    ins = make_insight("t", "MACRO", 0.65, "d", _sample_signals())
    assert ins.action == "Monitor"


def test_make_insight_watch_at_mid_score() -> None:
    ins = make_insight("t", "MACRO", 0.50, "d", _sample_signals())
    assert ins.action == "Watch"


def test_make_insight_caution_at_lower_mid_score() -> None:
    ins = make_insight("t", "MACRO", 0.35, "d", _sample_signals())
    assert ins.action == "Caution"


def test_make_insight_avoid_at_low_score() -> None:
    ins = make_insight("t", "MACRO", 0.15, "d", _sample_signals())
    assert ins.action == "Avoid"


def test_make_insight_action_at_exact_boundaries() -> None:
    # Boundaries per source: 0.75/0.60/0.45/0.30
    assert make_insight("t", "MACRO", 0.75, "d", []).action == "Prioritize"
    assert make_insight("t", "MACRO", 0.60, "d", []).action == "Monitor"
    assert make_insight("t", "MACRO", 0.45, "d", []).action == "Watch"
    assert make_insight("t", "MACRO", 0.30, "d", []).action == "Caution"
    # Anything below 0.30 → Avoid
    assert make_insight("t", "MACRO", 0.29, "d", []).action == "Avoid"


# ─── make_insight: well-formed Insight ─────────────────────────────────────

def test_make_insight_returns_well_formed_object() -> None:
    sigs = _sample_signals()
    ins = make_insight(
        title="Asia-Europe rate uplift",
        category="ROUTE",
        score=0.72,
        detail="Strong front-loading driving rates higher.",
        signals=sigs,
        ports=["CNSHA", "NLRTM"],
        routes=["asia_europe"],
        stocks=["ZIM", "MATX"],
    )
    assert isinstance(ins, Insight)
    assert ins.title == "Asia-Europe rate uplift"
    assert ins.category == "ROUTE"
    assert ins.score == 0.72
    assert ins.detail == "Strong front-loading driving rates higher."
    assert ins.supporting_signals == sigs
    assert ins.ports_involved == ["CNSHA", "NLRTM"]
    assert ins.routes_involved == ["asia_europe"]
    assert ins.stocks_potentially_affected == ["ZIM", "MATX"]
    assert ins.data_freshness_warning is False
    assert ins.insight_id      # non-empty short UUID
    assert ins.generated_at    # non-empty ISO timestamp


def test_make_insight_defaults_lists_to_empty() -> None:
    """Omitted port/route/stock args default to [], not None."""
    ins = make_insight("t", "MACRO", 0.50, "d", [])
    assert ins.ports_involved == []
    assert ins.routes_involved == []
    assert ins.stocks_potentially_affected == []


def test_make_insight_freshness_warning_propagated() -> None:
    ins = make_insight(
        "t", "MACRO", 0.50, "d", [], freshness_warning=True,
    )
    assert ins.data_freshness_warning is True


def test_make_insight_score_label_set_from_score() -> None:
    """The score_label comes from utils.helpers.score_to_label."""
    high = make_insight("t", "MACRO", 0.85, "d", [])
    mid = make_insight("t", "MACRO", 0.50, "d", [])
    low = make_insight("t", "MACRO", 0.10, "d", [])
    # score_to_label thresholds: 0.70 high, 0.35 low → ours: High/Moderate/Low
    assert high.score_label == "High"
    assert mid.score_label == "Moderate"
    assert low.score_label == "Low"


def test_make_insight_unique_ids() -> None:
    """Each call generates a fresh short-UUID."""
    a = make_insight("t", "MACRO", 0.5, "d", [])
    b = make_insight("t", "MACRO", 0.5, "d", [])
    assert a.insight_id != b.insight_id
    assert len(a.insight_id) == 8     # short UUID truncated to 8 chars


def test_make_insight_supports_empty_signals_list() -> None:
    ins = make_insight("t", "MACRO", 0.50, "d", [])
    assert ins.supporting_signals == []
