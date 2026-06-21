"""Tests for processing.regime_conditional (does the edge survive in stress?)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from processing.regime_conditional import (
    evaluate_by_regime,
    survives_in_stress,
    volatility_regime,
)


def test_evaluate_by_regime_splits_and_counts() -> None:
    rets = pd.Series([0.01, 0.02, -0.05, 0.06, -0.04, 0.015])
    labels = pd.Series(["low", "low", "high", "high", "high", "low"])
    stats = evaluate_by_regime(rets, labels)
    assert set(stats) == {"low", "high"}
    assert stats["low"].n == 3 and stats["high"].n == 3


def test_volatility_regime_flags_the_storm_half() -> None:
    rng = np.random.default_rng(0)
    rets = pd.Series(np.concatenate([
        rng.normal(0, 0.005, 60),   # calm
        rng.normal(0, 0.04, 60),    # storm
    ]))
    labels = volatility_regime(rets, window=10, quantile=0.7)
    high_frac = (labels.dropna().iloc[-50:] == "high-vol").mean()
    assert high_frac > 0.5


def test_survives_in_stress_flags_calm_only_strategy() -> None:
    # Positive in calm, negative in stress → does not survive.
    rets = pd.Series([0.02] * 30 + [-0.02] * 30)
    labels = pd.Series(["low-vol"] * 30 + ["high-vol"] * 30)
    res = survives_in_stress(rets, labels)
    assert res["survives_in_stress"] is False
    assert "does NOT clear" in res["verdict"]
    assert res["by_regime"]["high-vol"].sharpe < 0


def test_survives_in_stress_passes_a_robust_edge() -> None:
    rets = pd.Series([0.01] * 30 + [0.015] * 30)
    labels = pd.Series(["low-vol"] * 30 + ["high-vol"] * 30)
    res = survives_in_stress(rets, labels)
    assert res["survives_in_stress"] is True
    assert "survives" in res["verdict"]


def test_survives_in_stress_derives_regime_when_none_given() -> None:
    rng = np.random.default_rng(1)
    rets = pd.Series(np.concatenate([
        rng.normal(0.001, 0.005, 60), rng.normal(0.0, 0.04, 60)]))
    res = survives_in_stress(rets)   # no labels → volatility_regime
    assert "by_regime" in res and "survives_in_stress" in res


def test_evaluate_empty_is_empty() -> None:
    assert evaluate_by_regime(pd.Series([], dtype=float), pd.Series([], dtype=object)) == {}
