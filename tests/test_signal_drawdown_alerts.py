"""Per-tier drawdown kill-switch alerter (B2, on R004's signal ledger)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


class _Idea:
    def __init__(self, ticker, direction, price=100.0, conviction_label="High"):
        self.ticker = ticker
        self.direction = direction
        self.price = price
        self.conviction_score = 0.7
        self.conviction_label = conviction_label
        self.conviction_weight_set = "default"


def _stock(last_closes: dict):
    idx = pd.date_range("2026-07-01", periods=3, freq="B")  # forward of the 2026-06 issue dates (causal)
    return {t: pd.DataFrame({"close": [p * 0.9, p * 0.95, p]}, index=idx)
            for t, p in last_closes.items()}


def _freeze_cratered_tier(label="High"):
    """Freeze a tier whose realized edge has cratered: 5 signals each -20% ->
    20% underwater from cost, 0% hit-rate -> trips the kill-switch."""
    from state.signal_ledger import freeze_ideas
    freeze_ideas([_Idea(f"L{i}", "Bullish", conviction_label=label) for i in range(5)],
                 issue_date="2026-06-01")
    return _stock({f"L{i}": 80.0 for i in range(5)})


def test_no_stand_down_tier_fires_nothing() -> None:
    from engine.alert_engine_v2 import load_alerts
    from engine.signal_drawdown_alerts import check_and_alert_drawdown
    from state.signal_ledger import freeze_ideas
    freeze_ideas([_Idea(f"T{i}", "Bullish") for i in range(5)], issue_date="2026-06-01")
    counts = check_and_alert_drawdown(_stock({f"T{i}": 110.0 for i in range(5)}),
                                      user_id="u1")
    assert counts["checked"] == 1 and counts["stand_down"] == 0
    assert counts["alerted"] == 0
    assert load_alerts(user_id="u1") == []


def test_stand_down_tier_fires_one_high_alert() -> None:
    from engine.alert_engine_v2 import load_alerts
    from engine.signal_drawdown_alerts import check_and_alert_drawdown
    stock = _freeze_cratered_tier("High")
    counts = check_and_alert_drawdown(stock, user_id="u1")
    assert counts["stand_down"] == 1 and counts["alerted"] == 1
    alerts = load_alerts(user_id="u1")
    assert len(alerts) == 1
    a = alerts[0]
    assert a.alert_type == "SIGNAL_DRAWDOWN" and a.severity == "HIGH"
    assert a.port_locode == "High"          # tier rides in the dedup entity key
    assert a.value > 15.0                    # current drawdown carried in value
    assert "drawdown" in a.body.lower()


def test_cooldown_suppresses_refire_then_fires_after_window() -> None:
    from engine.signal_drawdown_alerts import check_and_alert_drawdown
    stock = _freeze_cratered_tier("High")
    t0 = datetime(2026, 6, 3, tzinfo=timezone.utc)
    first = check_and_alert_drawdown(stock, user_id="u1", now=t0)
    assert first["alerted"] == 1
    # second pass minutes later -> within the 24h cooldown -> suppressed
    second = check_and_alert_drawdown(
        stock, user_id="u1", now=t0 + timedelta(minutes=5))
    assert second["stand_down"] == 1 and second["skipped_cooldown"] == 1
    assert second["alerted"] == 0
    # a day later -> cooldown expired -> fires again
    third = check_and_alert_drawdown(
        stock, user_id="u1", now=t0 + timedelta(hours=25))
    assert third["alerted"] == 1


def test_hit_floor_trigger_named_in_body() -> None:
    # 1 small win + 4 small losses -> hit 0.2 < floor, shallow drawdown.
    from engine.alert_engine_v2 import load_alerts
    from engine.signal_drawdown_alerts import check_and_alert_drawdown
    from state.signal_ledger import freeze_ideas
    freeze_ideas([_Idea("WIN", "Bullish", conviction_label="Low")],
                 issue_date="2026-06-01")
    freeze_ideas([_Idea(f"L{i}", "Bullish", conviction_label="Low") for i in range(4)],
                 issue_date="2026-06-02")
    stock = _stock({"WIN": 101.0, **{f"L{i}": 99.0 for i in range(4)}})
    counts = check_and_alert_drawdown(stock, user_id="u1")
    assert counts["alerted"] == 1
    body = load_alerts(user_id="u1")[0].body.lower()
    assert "hit-rate" in body


def test_never_raises_on_bad_input() -> None:
    from engine.signal_drawdown_alerts import check_and_alert_drawdown
    counts = check_and_alert_drawdown(None, user_id="u1")
    assert set(counts) == {"checked", "stand_down", "alerted", "skipped_cooldown"}
    assert counts["alerted"] == 0


def test_scheduler_job_fires_and_returns_shape() -> None:
    import worker.scheduler as sch
    stock = _freeze_cratered_tier("High")
    res = sch.run_signal_drawdown_job({"stock_data": stock})
    assert set(res) >= {"checked", "stand_down", "alerted", "skipped_cooldown"}
    assert res["stand_down"] == 1 and res["alerted"] == 1


def test_scheduler_job_never_raises_on_bad_bundle() -> None:
    import worker.scheduler as sch
    res = sch.run_signal_drawdown_job(None)
    assert isinstance(res, dict) and res["alerted"] == 0
