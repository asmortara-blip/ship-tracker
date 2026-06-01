"""Tests for engine.alert_backtest — alert effectiveness scoring.

Pins the contract:
  - STOCK_MOVE / BDI_MOVE / RATE_SURGE → scored
  - SIGNAL_FIRE / MACRO / CONGESTION / unknown → skipped (None)
  - sign-based hit definition
  - safe handling of missing data (returns None, never raises)
  - aggregate report: hit_rate, by_alert_type, by_severity,
    median_magnitude_ratio
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from engine.alert_backtest import (
    AlertBacktestReport,
    AlertOutcome,
    backtest_alerts,
    score_alert,
)


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite isolation."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Stand-in alert shape ───────────────────────────────────────────────────

@dataclass
class _FakeAlert:
    alert_id: str
    created_at: str
    alert_type: str
    severity: str = "HIGH"
    title: str = "t"
    body: str = "b"
    ticker: str = ""
    route_id: str = ""
    port_locode: str = ""
    value: float = 0.0
    threshold: float = 0.0
    change_pct: float = 0.0
    acknowledged: bool = False


def _ts(days_ago: int) -> str:
    """ISO timestamp for N days before now (UTC)."""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _stock_df(base_date: datetime, prices: list[float]) -> pd.DataFrame:
    """Daily price series from base_date forward."""
    return pd.DataFrame({
        "date": pd.date_range(base_date, periods=len(prices), freq="D"),
        "close": prices,
    })


def _macro_df(base_date: datetime, values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range(base_date, periods=len(values), freq="D"),
        "value": values,
    })


def _freight_df(base_date: datetime, rates: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range(base_date, periods=len(rates), freq="D"),
        "rate_usd_per_feu": rates,
    })


# ─── Dataclass shapes ──────────────────────────────────────────────────────

def test_alert_outcome_shape() -> None:
    o = AlertOutcome(
        alert_id="x", alert_type="STOCK_MOVE", severity="HIGH",
        created_at="2026-05-01T00:00:00+00:00",
        predicted_change_pct=5.0, realized_change_pct=7.0,
        realized_window_days=7, hit=True, magnitude_ratio=1.4,
    )
    assert o.hit is True


def test_alert_backtest_report_shape() -> None:
    r = AlertBacktestReport(n_alerts_evaluated=0, n_skipped=0, hit_rate=0.0)
    assert r.by_alert_type == {}


# ─── score_alert — STOCK_MOVE ──────────────────────────────────────────────

def test_score_alert_stock_move_hit() -> None:
    """Alert predicted +5%, realized +7% → hit=True, magnitude_ratio=1.4."""
    fire_date = datetime(2026, 4, 1, tzinfo=timezone.utc)
    prices = [100.0] + [107.0] * 10  # +7% on day 1 then flat
    df = _stock_df(fire_date, prices)
    alert = _FakeAlert(
        alert_id="a1", created_at=fire_date.isoformat(),
        alert_type="STOCK_MOVE", severity="HIGH", ticker="ZIM",
        change_pct=5.0,
    )
    outcome = score_alert(alert, {"ZIM": df}, {}, {}, window_days=7)
    assert outcome is not None
    assert outcome.hit is True
    assert outcome.realized_change_pct == pytest.approx(7.0)
    assert outcome.magnitude_ratio == pytest.approx(1.4)


def test_score_alert_stock_move_sign_mismatch_no_hit() -> None:
    """Predicted +5%, realized -2% → hit=False."""
    fire_date = datetime(2026, 4, 1, tzinfo=timezone.utc)
    prices = [100.0] + [98.0] * 10
    df = _stock_df(fire_date, prices)
    alert = _FakeAlert(
        alert_id="a1", created_at=fire_date.isoformat(),
        alert_type="STOCK_MOVE", ticker="ZIM", change_pct=5.0,
    )
    outcome = score_alert(alert, {"ZIM": df}, {}, {}, window_days=7)
    assert outcome is not None
    assert outcome.hit is False


def test_score_alert_stock_move_missing_ticker_returns_none() -> None:
    alert = _FakeAlert(
        alert_id="a1", created_at="2026-04-01T00:00:00+00:00",
        alert_type="STOCK_MOVE", ticker="NOPE", change_pct=5.0,
    )
    outcome = score_alert(alert, {"ZIM": _stock_df(datetime(2026, 4, 1, tzinfo=timezone.utc), [100.0]*10)}, {}, {})
    assert outcome is None


def test_score_alert_stock_move_window_past_end_of_data_returns_none() -> None:
    """If the realized window extends past the last data row, return None."""
    fire_date = datetime(2026, 4, 1, tzinfo=timezone.utc)
    # Only 3 days of data — 7-day window can't resolve
    df = _stock_df(fire_date, [100.0, 101.0, 102.0])
    alert = _FakeAlert(
        alert_id="a1", created_at=fire_date.isoformat(),
        alert_type="STOCK_MOVE", ticker="ZIM", change_pct=5.0,
    )
    outcome = score_alert(alert, {"ZIM": df}, {}, {}, window_days=7)
    assert outcome is None


# ─── score_alert — BDI_MOVE ────────────────────────────────────────────────

def test_score_alert_bdi_move_bsxrlm_key() -> None:
    """BDI lookup uses BSXRLM key first."""
    fire_date = datetime(2026, 4, 1, tzinfo=timezone.utc)
    df = _macro_df(fire_date, [1000.0] + [1100.0] * 10)
    alert = _FakeAlert(
        alert_id="a1", created_at=fire_date.isoformat(),
        alert_type="BDI_MOVE", change_pct=8.0,
    )
    outcome = score_alert(alert, {}, {}, {"BSXRLM": df}, window_days=7)
    assert outcome is not None
    assert outcome.hit is True
    assert outcome.realized_change_pct == pytest.approx(10.0)


def test_score_alert_bdi_move_bdiy_legacy_key_fallback() -> None:
    """Old callers may still pass 'BDIY' — fallback chain catches it."""
    fire_date = datetime(2026, 4, 1, tzinfo=timezone.utc)
    df = _macro_df(fire_date, [1000.0] + [1100.0] * 10)
    alert = _FakeAlert(
        alert_id="a1", created_at=fire_date.isoformat(),
        alert_type="BDI_MOVE", change_pct=8.0,
    )
    outcome = score_alert(alert, {}, {}, {"BDIY": df}, window_days=7)
    assert outcome is not None
    assert outcome.hit is True


def test_score_alert_bdi_move_missing_series_returns_none() -> None:
    alert = _FakeAlert(
        alert_id="a1", created_at="2026-04-01T00:00:00+00:00",
        alert_type="BDI_MOVE", change_pct=8.0,
    )
    assert score_alert(alert, {}, {}, {}) is None


# ─── score_alert — RATE_SURGE ───────────────────────────────────────────────

def test_score_alert_rate_surge_happy() -> None:
    fire_date = datetime(2026, 4, 1, tzinfo=timezone.utc)
    df = _freight_df(fire_date, [2000.0] + [2300.0] * 10)
    alert = _FakeAlert(
        alert_id="a1", created_at=fire_date.isoformat(),
        alert_type="RATE_SURGE", route_id="transpacific_eb", change_pct=10.0,
    )
    outcome = score_alert(
        alert, {}, {"transpacific_eb": df}, {}, window_days=7,
    )
    assert outcome is not None
    assert outcome.hit is True
    assert outcome.realized_change_pct == pytest.approx(15.0)


def test_score_alert_rate_surge_missing_route_returns_none() -> None:
    alert = _FakeAlert(
        alert_id="a1", created_at="2026-04-01T00:00:00+00:00",
        alert_type="RATE_SURGE", route_id="ghost_route", change_pct=10.0,
    )
    assert score_alert(alert, {}, {}, {}) is None


# ─── score_alert — Skipped alert types ─────────────────────────────────────

@pytest.mark.parametrize("alert_type", ["SIGNAL_FIRE", "MACRO", "CONGESTION", "UNKNOWN_TYPE"])
def test_score_alert_unsupported_types_return_none(alert_type) -> None:
    alert = _FakeAlert(
        alert_id="a1", created_at="2026-04-01T00:00:00+00:00",
        alert_type=alert_type, change_pct=5.0,
    )
    assert score_alert(alert, {}, {}, {}) is None


def test_score_alert_zero_predicted_returns_none() -> None:
    """change_pct == 0 means no direction to compare — skip."""
    alert = _FakeAlert(
        alert_id="a1", created_at="2026-04-01T00:00:00+00:00",
        alert_type="STOCK_MOVE", ticker="ZIM", change_pct=0.0,
    )
    assert score_alert(alert, {"ZIM": _stock_df(datetime(2026, 4, 1, tzinfo=timezone.utc), [100.0]*10)}, {}, {}) is None


# ─── score_alert — Defensive ───────────────────────────────────────────────

def test_score_alert_malformed_created_at_returns_none() -> None:
    alert = _FakeAlert(
        alert_id="a1", created_at="not a real timestamp",
        alert_type="STOCK_MOVE", ticker="ZIM", change_pct=5.0,
    )
    assert score_alert(alert, {"ZIM": _stock_df(datetime(2026, 4, 1, tzinfo=timezone.utc), [100.0]*10)}, {}, {}) is None


# ─── backtest_alerts — empty + populated ──────────────────────────────────

def test_backtest_alerts_empty_db_returns_zeroed_report() -> None:
    report = backtest_alerts({}, {}, {})
    assert report.n_alerts_evaluated == 0
    assert report.n_skipped == 0
    assert report.hit_rate == 0.0
    assert report.by_alert_type == {}


def _insert_alert(alert: _FakeAlert) -> None:
    """Insert a synthetic alert into the SQLite alerts table."""
    from state.db import get_connection
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO alerts
              (alert_id, created_at, alert_type, severity, title, body,
               ticker, route_id, port_locode, value, threshold, change_pct,
               acknowledged)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert.alert_id, alert.created_at, alert.alert_type,
                alert.severity, alert.title, alert.body,
                alert.ticker, alert.route_id, alert.port_locode,
                alert.value, alert.threshold, alert.change_pct,
                1 if alert.acknowledged else 0,
            ),
        )


def test_backtest_alerts_aggregates_correctly() -> None:
    """3 hits + 2 misses across STOCK_MOVE + BDI_MOVE → hit_rate=0.6,
    by_alert_type counts correct."""
    base = datetime.now(timezone.utc) - timedelta(days=30)
    # Data starts 5 days BEFORE the alert fires so the alert's
    # 'as-of' value is the pre-jump price; the jump happens during
    # the realized 7-day window. Layout: 5 days at 100 → alert →
    # then 30 days at 108. The 7-day-later value is 108 → realized +8%.
    stock_df = _stock_df(base - timedelta(days=5),
                         [100.0] * 6 + [108.0] * 30)
    bdi_df = _macro_df(base - timedelta(days=5),
                       [1000.0] * 6 + [1100.0] * 30)  # +10% realized

    # 3 STOCK_MOVE alerts all predicting +5% (realized ≈ +8% → hits)
    for i in range(3):
        _insert_alert(_FakeAlert(
            alert_id=f"s{i}", created_at=base.isoformat(),
            alert_type="STOCK_MOVE", severity="HIGH",
            ticker="ZIM", change_pct=5.0,
        ))
    # 2 BDI_MOVE alerts predicting -3% (realized = +10% → misses)
    for i in range(2):
        _insert_alert(_FakeAlert(
            alert_id=f"b{i}", created_at=base.isoformat(),
            alert_type="BDI_MOVE", severity="CRITICAL",
            change_pct=-3.0,
        ))

    report = backtest_alerts(
        {"ZIM": stock_df}, {}, {"BSXRLM": bdi_df},
        window_days=7, lookback_days=90,
    )

    assert report.n_alerts_evaluated == 5
    assert report.hit_rate == pytest.approx(0.6)
    assert report.by_alert_type["STOCK_MOVE"]["count"] == 3
    assert report.by_alert_type["STOCK_MOVE"]["hit_count"] == 3
    assert report.by_alert_type["STOCK_MOVE"]["hit_rate"] == pytest.approx(1.0)
    assert report.by_alert_type["BDI_MOVE"]["count"] == 2
    assert report.by_alert_type["BDI_MOVE"]["hit_count"] == 0
    assert report.by_alert_type["BDI_MOVE"]["hit_rate"] == pytest.approx(0.0)
    assert report.by_severity["HIGH"]["count"] == 3
    assert report.by_severity["CRITICAL"]["count"] == 2


def test_backtest_alerts_skipped_count_separate_from_evaluated() -> None:
    """SIGNAL_FIRE alerts are skipped — counted in n_skipped, not in
    hit-rate denominator."""
    base = datetime.now(timezone.utc) - timedelta(days=30)
    stock_df = _stock_df(base - timedelta(days=5),
                         [100.0] * 6 + [108.0] * 30)

    # 1 STOCK_MOVE (will hit) + 1 SIGNAL_FIRE (will skip)
    _insert_alert(_FakeAlert(
        alert_id="s1", created_at=base.isoformat(),
        alert_type="STOCK_MOVE", ticker="ZIM", change_pct=5.0,
    ))
    _insert_alert(_FakeAlert(
        alert_id="sf1", created_at=base.isoformat(),
        alert_type="SIGNAL_FIRE", change_pct=5.0,
    ))

    report = backtest_alerts({"ZIM": stock_df}, {}, {}, window_days=7)
    assert report.n_alerts_evaluated == 1
    assert report.n_skipped == 1
    assert report.hit_rate == pytest.approx(1.0)


def test_backtest_alerts_filters_alerts_in_future_window() -> None:
    """Alerts that fired too recently (within the realized window) are
    excluded — the realized move hasn't happened yet."""
    # Alert that fired YESTERDAY — realized 7-day window is in the future
    _insert_alert(_FakeAlert(
        alert_id="recent", created_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        alert_type="STOCK_MOVE", ticker="ZIM", change_pct=5.0,
    ))
    # Alert from 30 days ago — realized window is well in the past
    _insert_alert(_FakeAlert(
        alert_id="old", created_at=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        alert_type="STOCK_MOVE", ticker="ZIM", change_pct=5.0,
    ))

    stock_df = _stock_df(datetime.now(timezone.utc) - timedelta(days=35),
                         [100.0] + [108.0] * 35)

    report = backtest_alerts({"ZIM": stock_df}, {}, {}, window_days=7)
    # Only the 30-day-old alert qualifies for evaluation
    assert report.n_alerts_evaluated == 1


def test_backtest_alerts_median_magnitude_ratio_excludes_zeros() -> None:
    """median_magnitude_ratio uses only non-zero ratios."""
    base = datetime.now(timezone.utc) - timedelta(days=30)
    stock_df = _stock_df(base - timedelta(days=5),
                         [100.0] * 6 + [110.0] * 30)  # +10% realized

    # 1 alert predicting +5% (ratio = 10/5 = 2)
    _insert_alert(_FakeAlert(
        alert_id="s1", created_at=base.isoformat(),
        alert_type="STOCK_MOVE", ticker="ZIM", change_pct=5.0,
    ))
    # 1 alert predicting +20% (ratio = 10/20 = 0.5)
    _insert_alert(_FakeAlert(
        alert_id="s2", created_at=base.isoformat(),
        alert_type="STOCK_MOVE", ticker="ZIM", change_pct=20.0,
    ))

    report = backtest_alerts({"ZIM": stock_df}, {}, {}, window_days=7)
    # median([2.0, 0.5]) = 1.25
    assert report.median_magnitude_ratio == pytest.approx(1.25, abs=0.05)


def test_backtest_alerts_lookback_excludes_too_old() -> None:
    """An alert older than lookback_days is excluded entirely."""
    very_old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    _insert_alert(_FakeAlert(
        alert_id="ancient", created_at=very_old,
        alert_type="STOCK_MOVE", ticker="ZIM", change_pct=5.0,
    ))
    stock_df = _stock_df(datetime.now(timezone.utc) - timedelta(days=210),
                         [100.0] + [108.0] * 30)

    report = backtest_alerts({"ZIM": stock_df}, {}, {}, lookback_days=90)
    assert report.n_alerts_evaluated == 0
    assert report.n_skipped == 0
