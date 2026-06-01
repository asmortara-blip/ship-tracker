"""Tests for per-channel monthly delivery budgets (schema v25).

A ``DeliveryChannel`` now carries an integer ``monthly_budget`` cap.
``deliver_alert`` skips dispatch (and bumps a ``budget_suppressed_counter``
kv_state row) once the per-user-per-channel count for the current UTC
month reaches the cap. ``budget=0`` is the legacy "unlimited" sentinel
and preserves the pre-v25 behaviour exactly.

Counter storage uses a per-user-per-channel-per-month kv_state row
keyed ``channel_usage:<user_id>:<channel_id>:<YYYY-MM>`` so monthly
rollover is implicit and operator resets are a single DELETE.

Covers (18 cases):
  - get_channel_usage returns 0 for a brand-new channel
  - increment_channel_usage bumps the counter
  - increment_channel_usage always uses the CURRENT year_month
  - get_channel_usage with explicit year_month works
  - check_budget budget=0 → never over (unlimited)
  - check_budget usage < budget → not over
  - check_budget usage >= budget → over
  - check_budget per-user scoping (alice's usage does not affect bob)
  - reset_channel_usage zeros the counter
  - reset_channel_usage with explicit year_month
  - get_all_channel_usage returns one row per channel in scope
  - deliver_alert integration: over budget → skip + count suppression
  - deliver_alert integration: under budget → delivers + increments
  - Monthly boundary: budget exhausted in month N, fresh budget in N+1
  - Per-user isolation in deliver_alert
  - get_channel_usage NEVER raises on backend failure
  - check_budget NEVER raises on backend failure
  - reset_channel_usage NEVER raises on backend failure
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from engine import alert_delivery
from engine.alert_delivery import (
    DeliveryChannel,
    check_budget,
    deliver_alert,
    get_all_channel_usage,
    get_budget_suppressed_count,
    get_channel_usage,
    increment_channel_usage,
    reset_channel_usage,
    save_channel,
)
from engine.alert_engine_v2 import ShippingAlert


# ─── Fixture: isolate SQLite DB per test ──────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ──────────────────────────────────────────────────────────────


def _make_alert(severity: str = "HIGH", *, alert_id: str = "a1") -> ShippingAlert:
    return ShippingAlert(
        alert_id=alert_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        alert_type="STOCK_MOVE",
        severity=severity,
        title=f"{severity} alert",
        body="Body",
        ticker="ZIM",
        route_id="",
        port_locode="",
        value=1.0,
        threshold=0.5,
        change_pct=100.0,
        acknowledged=False,
    )


def _make_channel(
    *,
    channel_id: str = "ch-b1",
    name: str = "B channel",
    monthly_budget: int = 0,
) -> DeliveryChannel:
    return DeliveryChannel(
        channel_id=channel_id,
        name=name,
        kind="slack",
        target="https://hooks.slack.com/services/T000/B000/XXXX",
        severity_threshold="LOW",
        enabled=True,
        monthly_budget=monthly_budget,
    )


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text


# ─── get_channel_usage ────────────────────────────────────────────────────


def test_get_channel_usage_returns_zero_for_new_channel() -> None:
    """No kv_state row for the channel yet → 0."""
    assert get_channel_usage("never-seen", user_id="alice") == 0


def test_get_channel_usage_with_explicit_year_month() -> None:
    """Passing year_month explicitly lets the operator inspect an older
    month's counter (e.g. April when it's now May)."""
    increment_channel_usage("c1", user_id="alice")
    # Querying with the CURRENT year_month sees the bump.
    current = datetime.now(timezone.utc).strftime("%Y-%m")
    assert get_channel_usage("c1", user_id="alice", year_month=current) == 1
    # Querying a different (older) year_month sees zero.
    assert get_channel_usage("c1", user_id="alice", year_month="2020-01") == 0


# ─── increment_channel_usage ──────────────────────────────────────────────


def test_increment_channel_usage_bumps_the_counter() -> None:
    """Two increments → counter at 2."""
    increment_channel_usage("c2", user_id="alice")
    increment_channel_usage("c2", user_id="alice")
    assert get_channel_usage("c2", user_id="alice") == 2


def test_increment_channel_usage_uses_current_year_month(monkeypatch) -> None:
    """Increment always lands in the CURRENT month bucket (not a stale
    fixed string). Mock the wall clock to confirm the key matches."""
    fixed = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(alert_delivery, "datetime", _FrozenDatetime)
    increment_channel_usage("c3", user_id="alice")
    # The increment wrote into the 2026-07 bucket.
    assert get_channel_usage("c3", user_id="alice", year_month="2026-07") == 1
    # A different month is untouched.
    assert get_channel_usage("c3", user_id="alice", year_month="2026-06") == 0


# ─── check_budget ─────────────────────────────────────────────────────────


def test_check_budget_zero_means_unlimited() -> None:
    """budget=0 → over_budget is ALWAYS False regardless of usage. This
    is the legacy "no cap configured" behaviour."""
    ch = _make_channel(channel_id="c4", monthly_budget=0)
    # Even with a million synthetic bumps, the channel is not over.
    for _ in range(5):
        increment_channel_usage("c4", user_id="alice")
    over, usage, budget = check_budget(ch, user_id="alice")
    assert over is False
    assert budget == 0


def test_check_budget_under_budget_not_over() -> None:
    """budget=10, usage=3 → not over."""
    ch = _make_channel(channel_id="c5", monthly_budget=10)
    for _ in range(3):
        increment_channel_usage("c5", user_id="alice")
    over, usage, budget = check_budget(ch, user_id="alice")
    assert over is False
    assert usage == 3
    assert budget == 10


def test_check_budget_at_or_above_budget_is_over() -> None:
    """budget=3, usage=3 → over (>= check, not >). The next delivery
    must be suppressed."""
    ch = _make_channel(channel_id="c6", monthly_budget=3)
    for _ in range(3):
        increment_channel_usage("c6", user_id="alice")
    over, usage, budget = check_budget(ch, user_id="alice")
    assert over is True
    assert usage == 3
    assert budget == 3


def test_check_budget_is_per_user_scoped() -> None:
    """alice's usage on channel X does NOT affect bob's check on the
    same channel. The kv_state key embeds user_id so the counters are
    completely independent."""
    ch = _make_channel(channel_id="c7", monthly_budget=2)
    increment_channel_usage("c7", user_id="alice")
    increment_channel_usage("c7", user_id="alice")
    # alice is now at the cap, bob is untouched.
    over_a, usage_a, _ = check_budget(ch, user_id="alice")
    over_b, usage_b, _ = check_budget(ch, user_id="bob")
    assert over_a is True
    assert usage_a == 2
    assert over_b is False
    assert usage_b == 0


# ─── reset_channel_usage ──────────────────────────────────────────────────


def test_reset_channel_usage_zeros_the_counter() -> None:
    """After reset, get_channel_usage returns 0 again — the kv_state
    row is DELETED so the "row does not exist" branch fires."""
    increment_channel_usage("c8", user_id="alice")
    increment_channel_usage("c8", user_id="alice")
    assert get_channel_usage("c8", user_id="alice") == 2
    assert reset_channel_usage("c8", user_id="alice") is True
    assert get_channel_usage("c8", user_id="alice") == 0


def test_reset_channel_usage_with_explicit_year_month() -> None:
    """Resetting a specific month only zeros that bucket — the current
    month's counter is untouched."""
    # Manually plant an April row, then bump May.
    from state.db import get_connection
    conn = get_connection()
    now_iso = datetime.now(timezone.utc).isoformat()
    key_apr = "channel_usage:alice:c9:2026-04"
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (key_apr, "100", now_iso),
        )
    increment_channel_usage("c9", user_id="alice")  # current month
    assert get_channel_usage("c9", user_id="alice", year_month="2026-04") == 100
    # Reset April only.
    assert reset_channel_usage("c9", user_id="alice", year_month="2026-04") is True
    assert get_channel_usage("c9", user_id="alice", year_month="2026-04") == 0
    # Current month is unaffected.
    assert get_channel_usage("c9", user_id="alice") == 1


# ─── get_all_channel_usage ────────────────────────────────────────────────


def test_get_all_channel_usage_returns_one_row_per_channel() -> None:
    """Dashboard query — one row per persisted channel in scope, each
    carrying budget + usage + pct + over_budget."""
    save_channel(_make_channel(channel_id="ca-1", name="A", monthly_budget=10), user_id="alice")
    save_channel(_make_channel(channel_id="ca-2", name="B", monthly_budget=0), user_id="alice")
    for _ in range(7):
        increment_channel_usage("ca-1", user_id="alice")
    rows = get_all_channel_usage(user_id="alice")
    assert len(rows) == 2
    by_id = {r["channel_id"]: r for r in rows}
    assert by_id["ca-1"]["budget"] == 10
    assert by_id["ca-1"]["usage"] == 7
    assert by_id["ca-1"]["pct"] == 70.0
    assert by_id["ca-1"]["over_budget"] is False
    # The unlimited channel reports pct=None so the UI can render "—".
    assert by_id["ca-2"]["budget"] == 0
    assert by_id["ca-2"]["pct"] is None
    assert by_id["ca-2"]["over_budget"] is False


# ─── deliver_alert integration ────────────────────────────────────────────


def test_deliver_alert_over_budget_skips_and_counts_suppression(monkeypatch) -> None:
    """When the channel is at-or-above its monthly cap, deliver_alert
    must NOT call the transport AND must bump the
    budget_suppressed_counter telemetry row. The result carries
    success=False + status_code=429 + a clear error_msg."""
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    ch = _make_channel(channel_id="cb-1", monthly_budget=2)
    # Saturate the budget.
    increment_channel_usage("cb-1", user_id="")
    increment_channel_usage("cb-1", user_id="")
    before = get_budget_suppressed_count()
    result = deliver_alert(_make_alert("HIGH"), ch)
    assert result.success is False
    assert result.status_code == 429
    assert "budget" in result.error_msg.lower()
    assert called["n"] == 0
    assert get_budget_suppressed_count() == before + 1


def test_deliver_alert_under_budget_delivers_and_increments(monkeypatch) -> None:
    """When the channel is under its cap, deliver_alert dispatches as
    today AND bumps the per-channel counter on success."""
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    ch = _make_channel(channel_id="cb-2", monthly_budget=5)
    assert get_channel_usage("cb-2", user_id="") == 0
    result = deliver_alert(_make_alert("HIGH"), ch)
    assert result.success is True
    assert called["n"] == 1
    # Counter bumped exactly once on success.
    assert get_channel_usage("cb-2", user_id="") == 1


def test_deliver_alert_monthly_boundary_fresh_budget(monkeypatch) -> None:
    """Budget exhausted in month N must NOT carry over to month N+1.
    Mocking the wall clock changes the per-month key, so the counter
    bucket resets implicitly on the calendar rollover."""
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    # Pretend it's July.
    july = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)

    class _JulyDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return july if tz is None else july.astimezone(tz)

    monkeypatch.setattr(alert_delivery, "datetime", _JulyDatetime)
    ch = _make_channel(channel_id="cb-3", monthly_budget=1)
    r1 = deliver_alert(_make_alert("HIGH"), ch)
    assert r1.success is True
    # Second July delivery is suppressed — budget=1 reached.
    r2 = deliver_alert(_make_alert("HIGH", alert_id="a2"), ch)
    assert r2.success is False
    assert r2.status_code == 429
    assert called["n"] == 1
    # Now jump to August — fresh budget bucket, delivery succeeds.
    august = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)

    class _AugustDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return august if tz is None else august.astimezone(tz)

    monkeypatch.setattr(alert_delivery, "datetime", _AugustDatetime)
    r3 = deliver_alert(_make_alert("HIGH", alert_id="a3"), ch)
    assert r3.success is True
    assert called["n"] == 2


def test_deliver_alert_budget_per_user_isolation(monkeypatch) -> None:
    """alice's counter on channel X is independent of bob's. When the
    alert is owned by bob and alice already saturated the channel,
    bob's delivery must still succeed because the budget is checked
    against bob's bucket."""
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    # Force the prefs / lookup to return bob's user_id so the budget
    # check uses bob's bucket. ``_lookup_alert_user_id`` reads from the
    # alerts table by alert_id — we patch it directly here.
    monkeypatch.setattr(
        alert_delivery, "_lookup_alert_user_id", lambda alert: "bob"
    )
    ch = _make_channel(channel_id="cb-4", monthly_budget=1)
    # alice saturates her bucket — should NOT block bob.
    increment_channel_usage("cb-4", user_id="alice")
    # bob's bucket is empty, delivery succeeds.
    result = deliver_alert(_make_alert("HIGH"), ch)
    assert result.success is True
    assert called["n"] == 1
    # bob's bucket is now at 1.
    assert get_channel_usage("cb-4", user_id="bob") == 1
    # alice's bucket is unchanged.
    assert get_channel_usage("cb-4", user_id="alice") == 1


# ─── Persistence: round-trip + back-compat ────────────────────────────────


def test_save_channel_round_trips_monthly_budget() -> None:
    """save_channel + load_channels round-trip the monthly_budget
    column. The dataclass default (0) and a positive cap both
    survive the upsert."""
    ch = _make_channel(channel_id="c-rt-zero", monthly_budget=0)
    save_channel(ch, user_id="alice")
    ch2 = _make_channel(channel_id="c-rt-pos", monthly_budget=250)
    save_channel(ch2, user_id="alice")
    from engine.alert_delivery import load_channels
    loaded = {c.channel_id: c for c in load_channels(user_id="alice")}
    assert loaded["c-rt-zero"].monthly_budget == 0
    assert loaded["c-rt-pos"].monthly_budget == 250


def test_load_channels_defaults_for_pre_v25_rows() -> None:
    """A row inserted WITHOUT specifying monthly_budget must load as
    0 (the column DEFAULT), preserving the legacy "unlimited"
    behaviour for pre-v25 channels."""
    from state.db import get_connection
    from engine.alert_delivery import load_channels

    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO delivery_channels
              (channel_id, name, kind, target, severity_threshold,
               enabled, created_at)
            VALUES ('c-pre-v25', 'Legacy', 'slack',
                    'https://hooks.slack.com/services/x',
                    'HIGH', 1, '2026-05-21T00:00:00+00:00')
            """
        )
    loaded = load_channels()
    by_id = {c.channel_id: c for c in loaded}
    assert by_id["c-pre-v25"].monthly_budget == 0


# ─── Never-raise contract ─────────────────────────────────────────────────


def test_helpers_never_raise_on_backend_failure(monkeypatch) -> None:
    """Every helper must swallow internal errors — a broken DB never
    propagates a stack trace into the delivery hot path. We monkey-
    patch get_connection to always raise and confirm each helper
    collapses to its safe default."""
    def _boom():
        raise RuntimeError("synthetic db outage")

    from state import db as state_db
    monkeypatch.setattr(state_db, "get_connection", _boom)

    # get_channel_usage → 0
    assert get_channel_usage("x", user_id="alice") == 0

    # increment_channel_usage → no exception
    increment_channel_usage("x", user_id="alice")  # should not raise

    # reset_channel_usage → False (audit log path also swallows)
    assert reset_channel_usage("x", user_id="alice") is False

    # check_budget on a positive-cap channel falls back to a safe
    # (False, 0, budget) shape because the kv_state read fails — the
    # delivery is allowed through (failure-open on the cap).
    ch = _make_channel(channel_id="x", monthly_budget=5)
    over, usage, budget = check_budget(ch, user_id="alice")
    assert over is False
    assert usage == 0
    assert budget == 5

    # get_all_channel_usage → [] (load_channels itself failed)
    assert get_all_channel_usage(user_id="alice") == []
