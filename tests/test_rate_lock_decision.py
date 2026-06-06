"""Contract LOCK/RIDE/SPLIT decision from the real forecast (rec R002)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from processing.rate_lock_decision import (
    RateLockDecision,
    decide_all_rate_locks,
    decide_rate_lock,
)


def _fc(direction, conf, current=2000.0, f30=2000.0, rid="transpacific_eb"):
    return SimpleNamespace(
        route_id=rid, route_name=rid, current_rate=current, forecast_30d=f30,
        direction=direction, direction_confidence=conf)


def test_rising_with_confidence_locks() -> None:
    d = decide_rate_lock(_fc("Rising", 0.8, current=2000, f30=2400))
    assert d.verdict == "LOCK" and d.lock_fraction == 1.0
    assert d.expected_savings_per_feu == pytest.approx(400.0)   # the upside avoided
    assert d.expected_move_pct == pytest.approx(0.2)
    assert d.breakeven_rate == 2000.0


def test_falling_with_confidence_rides() -> None:
    d = decide_rate_lock(_fc("Falling", 0.75, current=3000, f30=2400))
    assert d.verdict == "RIDE" and d.lock_fraction == 0.0
    assert d.expected_savings_per_feu == pytest.approx(600.0)    # the downside captured


def test_low_confidence_splits_even_when_directional() -> None:
    d = decide_rate_lock(_fc("Rising", 0.40, current=2000, f30=2400), confidence_floor=0.55)
    assert d.verdict == "SPLIT"
    assert 0.5 < d.lock_fraction <= 0.85          # tilts toward the (weak) rising signal
    assert "below the 55% floor" in d.rationale


def test_stable_splits_balanced() -> None:
    d = decide_rate_lock(_fc("Stable", 0.9, current=2000, f30=2010))
    assert d.verdict == "SPLIT" and d.lock_fraction == 0.5      # no directional tilt


def test_rising_but_forecast_not_above_current_does_not_lock() -> None:
    # Direction says Rising but the 30d point isn't above current -> no LOCK.
    d = decide_rate_lock(_fc("Rising", 0.9, current=2000, f30=1990))
    assert d.verdict == "SPLIT"


def test_zero_current_rate_is_safe() -> None:
    d = decide_rate_lock(_fc("Rising", 0.9, current=0.0, f30=100))
    assert isinstance(d, RateLockDecision) and d.expected_move_pct == 0.0


def test_duck_typed_and_missing_fields_default() -> None:
    d = decide_rate_lock(SimpleNamespace())          # nothing set
    assert d.verdict == "SPLIT" and d.direction == "Stable"


# ── decide_all_rate_locks (runs the real forecaster) ─────────────────────────

def test_decide_all_skips_routes_without_history(monkeypatch) -> None:
    import processing.rate_lock_decision as rld
    # Stub the ML forecaster so the test is deterministic + offline.
    monkeypatch.setattr(
        "processing.rate_forecaster.forecast_route",
        lambda rid, name, df, macro: _fc("Rising", 0.8, current=2000, f30=2300, rid=rid))
    import pandas as pd
    fd = {"transpacific_eb": pd.DataFrame({"rate_usd_per_feu": [2000, 2100]}),
          "asia_europe": pd.DataFrame()}     # empty -> skipped, no fabricated row
    out = rld.decide_all_rate_locks(fd, {})
    assert [d.route_id for d in out] == ["transpacific_eb"]
    assert out[0].verdict == "LOCK"


def test_decide_all_never_raises_on_bad_input() -> None:
    assert decide_all_rate_locks(None) == []
    assert decide_all_rate_locks({}) == []
