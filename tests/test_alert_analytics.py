"""Tests for engine.alert_analytics — acknowledgment metrics over alerts.

Covers:
  - AlertMetrics dataclass shape (defaults are zeroed, types match)
  - compute_alert_metrics on empty DB returns a zeroed AlertMetrics
  - compute_alert_metrics on a small synthetic dataset: totals + ack_rate
  - by_severity breakdown matches a manual aggregation
  - median_time_to_ack_hours: None when no acked alerts have a timestamp;
    a real number when they do; odd/even counts via statistics.median
  - window_days filter: rows older than the window are excluded
  - get_unacknowledged_critical: filters by severity + ack + window
  - compute_alert_metrics swallows exceptions (never raises)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.alert_analytics import (
    AlertMetrics,
    compute_alert_metrics,
    get_unacknowledged_critical,
)
from engine.alert_engine_v2 import ShippingAlert, save_alerts


# ─── Fixture: isolate SQLite per test ──────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ───────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mk_alert(
    alert_id: str,
    severity: str = "HIGH",
    created_at: str | None = None,
    acknowledged: bool = False,
) -> ShippingAlert:
    """Construct a ShippingAlert with a deterministic id + created_at.

    The ``ticker`` field is stamped with the alert_id so each row gets a
    unique v14 dedup_key — this helper is shared across analytics tests
    that need N distinct rows of the same severity, and the v14
    window-based dedup would otherwise collapse them. Tests that
    actually care about the ticker value override it after construction.
    """
    return ShippingAlert(
        alert_id=alert_id,
        created_at=created_at if created_at is not None else _now().isoformat(),
        alert_type="MACRO",
        severity=severity,
        title=f"title-{alert_id}",
        body=f"body-{alert_id}",
        ticker=alert_id,
        route_id="",
        port_locode="",
        value=0.0,
        threshold=0.0,
        change_pct=0.0,
        acknowledged=acknowledged,
    )


def _set_ack_timestamp(alert_id: str, ack_ts: str) -> None:
    """Force a specific acknowledged_at value into the DB so the test
    can compute a deterministic median time-to-ack."""
    from state.db import get_connection
    conn = get_connection()
    conn.execute(
        "UPDATE alerts SET acknowledged = 1, acknowledged_at = ? WHERE alert_id = ?",
        (ack_ts, alert_id),
    )


# ─── AlertMetrics dataclass shape ──────────────────────────────────────────

def test_alert_metrics_default_shape() -> None:
    m = AlertMetrics()
    assert m.total_alerts == 0
    assert m.acknowledged_count == 0
    assert m.unacknowledged_count == 0
    assert m.ack_rate == 0.0
    assert m.by_severity == {}
    assert m.median_time_to_ack_hours is None
    assert m.by_day == []


def test_alert_metrics_carries_three_breakdowns() -> None:
    """Sanity: the dataclass field names match the documented breakdowns."""
    m = AlertMetrics(
        total_alerts=1,
        acknowledged_count=1,
        unacknowledged_count=0,
        ack_rate=1.0,
        by_severity={"HIGH": {"total": 1, "ack_count": 1, "ack_rate": 1.0}},
        median_time_to_ack_hours=2.5,
        by_day=[{"date": "2026-05-21", "total": 1, "ack_count": 1}],
    )
    assert m.by_severity["HIGH"]["ack_rate"] == 1.0
    assert m.by_day[0]["date"] == "2026-05-21"


# ─── compute_alert_metrics ─────────────────────────────────────────────────

def test_compute_metrics_empty_db_returns_zeroed() -> None:
    m = compute_alert_metrics()
    assert m.total_alerts == 0
    assert m.acknowledged_count == 0
    assert m.unacknowledged_count == 0
    assert m.ack_rate == 0.0
    assert m.by_severity == {}
    assert m.median_time_to_ack_hours is None
    assert m.by_day == []


def test_compute_metrics_totals_and_ack_rate() -> None:
    """3 alerts: 2 ack'd + 1 unacked → total=3, ack=2, rate=2/3."""
    save_alerts([
        _mk_alert("a1", severity="CRITICAL", acknowledged=True),
        _mk_alert("a2", severity="HIGH", acknowledged=True),
        _mk_alert("a3", severity="LOW", acknowledged=False),
    ])
    m = compute_alert_metrics()
    assert m.total_alerts == 3
    assert m.acknowledged_count == 2
    assert m.unacknowledged_count == 1
    assert m.ack_rate == pytest.approx(2.0 / 3.0)


def test_compute_metrics_by_severity_breakdown() -> None:
    """Severity buckets match a manual aggregation."""
    save_alerts([
        _mk_alert("a1", severity="CRITICAL", acknowledged=True),
        _mk_alert("a2", severity="CRITICAL", acknowledged=False),
        _mk_alert("a3", severity="HIGH", acknowledged=True),
        _mk_alert("a4", severity="LOW", acknowledged=False),
    ])
    m = compute_alert_metrics()
    assert m.by_severity["CRITICAL"]["total"] == 2
    assert m.by_severity["CRITICAL"]["ack_count"] == 1
    assert m.by_severity["CRITICAL"]["ack_rate"] == pytest.approx(0.5)
    assert m.by_severity["HIGH"]["total"] == 1
    assert m.by_severity["HIGH"]["ack_count"] == 1
    assert m.by_severity["HIGH"]["ack_rate"] == pytest.approx(1.0)
    assert m.by_severity["LOW"]["total"] == 1
    assert m.by_severity["LOW"]["ack_count"] == 0
    assert m.by_severity["LOW"]["ack_rate"] == pytest.approx(0.0)


def test_compute_metrics_median_none_when_no_ack_timestamps() -> None:
    """Pre-v4 acked alerts (empty acknowledged_at) → median is None."""
    save_alerts([
        _mk_alert("a1", severity="HIGH", acknowledged=True),
        _mk_alert("a2", severity="HIGH", acknowledged=True),
    ])
    # save_alerts goes through INSERT (no ack_ts written by the save path
    # for already-acked alerts); leave acknowledged_at as '' to simulate
    # pre-v4 rows.
    m = compute_alert_metrics()
    assert m.acknowledged_count == 2
    assert m.median_time_to_ack_hours is None


def test_compute_metrics_median_real_value() -> None:
    """3 acked alerts with deltas of 1h / 2h / 3h → median = 2h."""
    base = _now() - timedelta(hours=10)
    save_alerts([
        _mk_alert("a1", severity="HIGH", created_at=base.isoformat()),
        _mk_alert("a2", severity="HIGH", created_at=base.isoformat()),
        _mk_alert("a3", severity="HIGH", created_at=base.isoformat()),
    ])
    _set_ack_timestamp("a1", (base + timedelta(hours=1)).isoformat())
    _set_ack_timestamp("a2", (base + timedelta(hours=2)).isoformat())
    _set_ack_timestamp("a3", (base + timedelta(hours=3)).isoformat())

    m = compute_alert_metrics()
    assert m.median_time_to_ack_hours == pytest.approx(2.0)


def test_compute_metrics_median_even_count() -> None:
    """Even count → statistics.median averages the two middle values.
    Deltas 1h / 3h → median = 2h."""
    base = _now() - timedelta(hours=10)
    save_alerts([
        _mk_alert("a1", severity="HIGH", created_at=base.isoformat()),
        _mk_alert("a2", severity="HIGH", created_at=base.isoformat()),
    ])
    _set_ack_timestamp("a1", (base + timedelta(hours=1)).isoformat())
    _set_ack_timestamp("a2", (base + timedelta(hours=3)).isoformat())

    m = compute_alert_metrics()
    assert m.median_time_to_ack_hours == pytest.approx(2.0)


def test_compute_metrics_window_filter_excludes_old_rows() -> None:
    """An alert older than the window is excluded from totals."""
    old = _mk_alert(
        "old",
        severity="HIGH",
        created_at=(_now() - timedelta(days=60)).isoformat(),
    )
    fresh = _mk_alert("fresh", severity="HIGH")
    save_alerts([old, fresh])
    m = compute_alert_metrics(window_days=30)
    assert m.total_alerts == 1  # only `fresh` is inside the window


def test_compute_metrics_by_day_ordered_ascending() -> None:
    """by_day entries appear once per day with at least one alert,
    sorted ascending."""
    day_a = _now() - timedelta(days=3)
    day_b = _now() - timedelta(days=1)
    save_alerts([
        _mk_alert("a1", severity="HIGH", created_at=day_a.isoformat()),
        _mk_alert("a2", severity="HIGH", created_at=day_a.isoformat()),
        _mk_alert("a3", severity="HIGH", created_at=day_b.isoformat()),
    ])
    m = compute_alert_metrics()
    assert len(m.by_day) == 2
    assert m.by_day[0]["date"] < m.by_day[1]["date"]
    # day_a saw 2 alerts; day_b saw 1
    assert m.by_day[0]["total"] == 2
    assert m.by_day[1]["total"] == 1


def test_compute_metrics_zero_window_returns_empty() -> None:
    save_alerts([_mk_alert("a1", severity="HIGH")])
    m = compute_alert_metrics(window_days=0)
    assert m.total_alerts == 0


def test_compute_metrics_never_raises_on_bad_window(monkeypatch) -> None:
    """A non-coercible window should fall back to the default, not crash."""
    save_alerts([_mk_alert("a1", severity="HIGH")])
    # Pass a value that int() can't coerce — function should default
    # to 30 days, NOT raise.
    m = compute_alert_metrics(window_days="not-a-number")  # type: ignore[arg-type]
    assert isinstance(m, AlertMetrics)
    assert m.total_alerts == 1


# ─── get_unacknowledged_critical ───────────────────────────────────────────

def test_get_unacknowledged_critical_filters_by_severity() -> None:
    """Only severity=CRITICAL + acknowledged=0 rows come back."""
    save_alerts([
        _mk_alert("c_unack", severity="CRITICAL", acknowledged=False),
        _mk_alert("c_ack", severity="CRITICAL", acknowledged=True),
        _mk_alert("h_unack", severity="HIGH", acknowledged=False),
    ])
    out = get_unacknowledged_critical()
    ids = {a.alert_id for a in out}
    assert ids == {"c_unack"}


def test_get_unacknowledged_critical_respects_window() -> None:
    """Old CRITICAL alerts beyond the window are excluded."""
    save_alerts([
        _mk_alert(
            "old",
            severity="CRITICAL",
            acknowledged=False,
            created_at=(_now() - timedelta(days=60)).isoformat(),
        ),
        _mk_alert("fresh", severity="CRITICAL", acknowledged=False),
    ])
    out = get_unacknowledged_critical(window_days=30)
    ids = {a.alert_id for a in out}
    assert ids == {"fresh"}


def test_get_unacknowledged_critical_empty_returns_empty_list() -> None:
    assert get_unacknowledged_critical() == []
