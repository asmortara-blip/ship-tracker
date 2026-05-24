"""Tests for engine.alert_replay — operator-driven replay of historical alerts.

Defining properties under test (the API contract callers depend on):

  - replay_alert dispatches the alert via the per-kind helper
    (_dispatch_alert) so the existing wire protocol is reused.
  - The dispatched alert's title is prefixed with "[REPLAY] " so the
    channel recipient can tell it's not a live event.
  - replay does NOT consume the channel's monthly budget — the existing
    kv_state counter is untouched even after a successful replay.
  - An audit row with action='alert_replay' is written for every
    attempt (success or failure), carrying alert_id + channel_id +
    success in the detail payload.
  - Per-user scoping is enforced on BOTH the alert_id AND the channel_id.
    A cross-user replay returns ReplayResult(success=False,
    message='... not found or not owned') — same observable outcome as
    an unknown id, so an attacker cannot enumerate ids by probing.
  - replay_alert NEVER raises. Even when the underlying dispatch helper
    raises an exception, the failure surfaces in ReplayResult.message.
  - Bulk variants stop NOTHING at the first failure — every input id
    gets a result, and the order is preserved.
  - The by_filter helper respects severity / alert_type / since / until /
    limit and clamps limit to MAX_REPLAY_LIMIT.

Per-test SQLite isolation mirrors test_alert_delivery — each test gets
its own tmp_path-backed DB so no test touches the real cache file.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine import alert_delivery, alert_replay
from engine.alert_delivery import (
    DeliveryChannel,
    DeliveryResult,
    increment_channel_usage,
    get_channel_usage,
    save_channel,
)
from engine.alert_engine_v2 import ShippingAlert, save_alerts
from engine.alert_replay import (
    DEFAULT_REPLAY_LIMIT,
    MAX_REPLAY_LIMIT,
    REPLAY_TITLE_PREFIX,
    ReplayResult,
    parse_relative_since,
    replay_alert,
    replay_alerts,
    replay_alerts_by_filter,
)


# ─── Fixture: isolate SQLite DB per test ──────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches cache/ship_tracker.db."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ───────────────────────────────────────────────────────────────

def _make_alert(
    alert_id: str = "alert-1",
    severity: str = "HIGH",
    alert_type: str = "BDI_MOVE",
    title: str = "BDI spiked",
    body: str = "BDI moved by 7.5% — above threshold.",
    created_at: str | None = None,
    ticker: str = "",
    route_id: str = "",
    port_locode: str = "",
    value: float = 1500.0,
    threshold: float = 1400.0,
    change_pct: float = 7.5,
) -> ShippingAlert:
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    return ShippingAlert(
        alert_id=alert_id,
        created_at=created_at,
        alert_type=alert_type,
        severity=severity,
        title=title,
        body=body,
        ticker=ticker,
        route_id=route_id,
        port_locode=port_locode,
        value=value,
        threshold=threshold,
        change_pct=change_pct,
        acknowledged=False,
    )


def _make_channel(
    channel_id: str = "ch-1",
    name: str = "Replay test",
    kind: str = "slack",
    target: str = "https://hooks.slack.com/services/T/B/X",
    severity_threshold: str = "LOW",
    enabled: bool = True,
    monthly_budget: int = 0,
) -> DeliveryChannel:
    return DeliveryChannel(
        channel_id=channel_id,
        name=name,
        kind=kind,
        target=target,
        severity_threshold=severity_threshold,
        enabled=enabled,
        monthly_budget=monthly_budget,
    )


class _FakeResponse:
    """Stand-in for requests.Response — only status_code + text needed."""

    def __init__(self, status_code: int = 200, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text


def _seed_alert_and_channel(
    *,
    alert_user: str = "alice",
    channel_user: str = "alice",
    alert_id: str = "alert-1",
    channel_id: str = "ch-1",
    severity: str = "HIGH",
    title: str = "BDI spiked",
    monthly_budget: int = 0,
) -> tuple[ShippingAlert, DeliveryChannel]:
    """Persist one alert for ``alert_user`` and one channel for
    ``channel_user``. Returns the in-memory dataclass instances."""
    alert = _make_alert(alert_id=alert_id, severity=severity, title=title)
    save_alerts([alert], user_id=alert_user)
    channel = _make_channel(channel_id=channel_id, monthly_budget=monthly_budget)
    save_channel(channel, user_id=channel_user)
    return alert, channel


# ─── ReplayResult dataclass ────────────────────────────────────────────────

def test_replay_result_shape_success() -> None:
    r = ReplayResult(alert_id="a", channel_id="c", success=True, message="delivered")
    assert r.alert_id == "a"
    assert r.channel_id == "c"
    assert r.success is True
    assert r.message == "delivered"


def test_replay_result_shape_failure() -> None:
    r = ReplayResult(alert_id="a", channel_id="c", success=False, message="x")
    assert r.success is False
    assert r.message == "x"


# ─── replay_alert: happy path ─────────────────────────────────────────────

def test_replay_alert_happy_path(monkeypatch) -> None:
    _seed_alert_and_channel()
    posted = {}

    def fake_post(url, json=None, timeout=None, **kw):
        posted["url"] = url
        posted["json"] = json
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    result = replay_alert("alert-1", "ch-1", user_id="alice")
    assert isinstance(result, ReplayResult)
    assert result.success is True
    assert result.alert_id == "alert-1"
    assert result.channel_id == "ch-1"
    assert result.message == "delivered"
    # Slack POST was invoked exactly once.
    assert posted["url"].startswith("https://hooks.slack.com/")


# ─── replay_alert: unknown alert_id ───────────────────────────────────────

def test_replay_alert_unknown_alert_id(monkeypatch) -> None:
    save_channel(_make_channel(channel_id="ch-1"), user_id="alice")

    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    result = replay_alert("does-not-exist", "ch-1", user_id="alice")
    assert result.success is False
    assert "not found" in result.message or "not owned" in result.message
    assert called["n"] == 0  # never reached the wire


# ─── replay_alert: cross-user alert ───────────────────────────────────────

def test_replay_alert_cross_user_alert_rejected(monkeypatch) -> None:
    """bob cannot replay alice's alert; same observable outcome as unknown."""
    save_alerts([_make_alert(alert_id="alice-alert")], user_id="alice")
    save_channel(_make_channel(channel_id="bob-ch"), user_id="bob")

    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    result = replay_alert("alice-alert", "bob-ch", user_id="bob")
    assert result.success is False
    assert "not found" in result.message or "not owned" in result.message
    assert called["n"] == 0


# ─── replay_alert: unknown channel ────────────────────────────────────────

def test_replay_alert_unknown_channel(monkeypatch) -> None:
    save_alerts([_make_alert(alert_id="alert-1")], user_id="alice")

    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    result = replay_alert("alert-1", "no-such-channel", user_id="alice")
    assert result.success is False
    assert "channel" in result.message.lower()
    assert called["n"] == 0


# ─── replay_alert: cross-user channel ─────────────────────────────────────

def test_replay_alert_cross_user_channel_rejected(monkeypatch) -> None:
    """alice cannot replay her alert TO bob's channel — channel ownership
    is checked independently of alert ownership."""
    save_alerts([_make_alert(alert_id="alice-alert")], user_id="alice")
    save_channel(_make_channel(channel_id="bob-ch"), user_id="bob")

    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    result = replay_alert("alice-alert", "bob-ch", user_id="alice")
    assert result.success is False
    assert "channel" in result.message.lower()
    assert called["n"] == 0


# ─── replay_alert: [REPLAY] prefix on dispatched payload ──────────────────

def test_replay_alert_prefixes_title_in_dispatched_payload(monkeypatch) -> None:
    """The Slack payload sent to the wire carries [REPLAY] in the alert
    title — the channel recipient can tell it's not a live event."""
    _seed_alert_and_channel(title="BDI moved 7.5% — above 5% threshold")
    captured = {}

    def fake_post(url, json=None, timeout=None, **kw):
        captured["json"] = json
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    replay_alert("alert-1", "ch-1", user_id="alice")
    # The Slack payload nests title-ish text into 'attachments'; we just
    # need to assert the [REPLAY] prefix appears somewhere in the JSON.
    payload_str = str(captured.get("json", {}))
    assert REPLAY_TITLE_PREFIX.strip() in payload_str


def test_replay_alert_does_not_mutate_stored_alert(monkeypatch) -> None:
    """Replaying must NOT re-write the historical title in the DB."""
    _seed_alert_and_channel(title="original title")

    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    replay_alert("alert-1", "ch-1", user_id="alice")

    # Re-read the alert from the DB; the stored title must remain
    # untouched even though the dispatched payload was prefixed.
    from engine.alert_engine_v2 import load_alerts
    alerts = load_alerts(user_id="alice")
    stored = next(a for a in alerts if a.alert_id == "alert-1")
    assert stored.title == "original title"
    assert not stored.title.startswith(REPLAY_TITLE_PREFIX)


# ─── replay_alert: audit event recorded ───────────────────────────────────

def test_replay_alert_records_audit_event(monkeypatch) -> None:
    _seed_alert_and_channel()
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    replay_alert("alert-1", "ch-1", user_id="alice")

    from auth.audit import query_audit
    events = query_audit(action="alert_replay", user_id="alice")
    assert len(events) >= 1
    e = events[0]
    assert e.action == "alert_replay"
    assert e.entity_type == "alert"
    assert e.entity_id == "alert-1"
    # The detail payload carries alert_id + channel_id + success.
    assert e.detail_json.get("alert_id") == "alert-1"
    assert e.detail_json.get("channel_id") == "ch-1"
    assert e.detail_json.get("success") is True


def test_replay_alert_records_audit_event_on_failure(monkeypatch) -> None:
    """Even a failed replay (cross-user / unknown id) writes one audit row."""
    save_channel(_make_channel(channel_id="ch-1"), user_id="alice")
    # No alert seeded — replay fails at the alert lookup.
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    replay_alert("nope", "ch-1", user_id="alice")

    from auth.audit import query_audit
    events = query_audit(action="alert_replay", user_id="alice")
    assert len(events) == 1
    assert events[0].detail_json.get("success") is False


# ─── replay_alert: does NOT consume budget ────────────────────────────────

def test_replay_alert_does_not_increment_channel_budget_usage(monkeypatch) -> None:
    """A successful replay must NOT bump the channel's monthly counter."""
    # Channel has a positive monthly budget. Pre-seed the usage counter
    # so we can assert it stays exactly where it was after a replay.
    alert, channel = _seed_alert_and_channel(monthly_budget=10)
    increment_channel_usage("ch-1", user_id="alice")
    before = get_channel_usage("ch-1", user_id="alice")
    assert before == 1

    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    result = replay_alert("alert-1", "ch-1", user_id="alice")
    assert result.success is True

    after = get_channel_usage("ch-1", user_id="alice")
    assert after == before  # untouched — replay did NOT count against budget


def test_replay_alert_succeeds_even_when_budget_exhausted(monkeypatch) -> None:
    """Budget gate is bypassed — even at-the-cap channels still replay."""
    # Set a budget of 5 and pre-burn all 5; a real fire would now be
    # suppressed. Replay must still go through.
    alert, channel = _seed_alert_and_channel(monthly_budget=5)
    for _ in range(5):
        increment_channel_usage("ch-1", user_id="alice")
    assert get_channel_usage("ch-1", user_id="alice") == 5

    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    result = replay_alert("alert-1", "ch-1", user_id="alice")
    assert result.success is True
    # Still 5 — neither bumped by the replay nor decremented.
    assert get_channel_usage("ch-1", user_id="alice") == 5


# ─── replay_alert: never raises ───────────────────────────────────────────

def test_replay_alert_never_raises_when_dispatch_throws(monkeypatch) -> None:
    """A monkeypatched dispatch that itself raises collapses to
    ReplayResult(success=False) — the caller never sees the exception."""
    _seed_alert_and_channel()

    def boom(*a, **kw):
        raise RuntimeError("synthetic dispatch failure")

    monkeypatch.setattr(alert_replay, "_dispatch_alert", boom)
    result = replay_alert("alert-1", "ch-1", user_id="alice")
    assert result.success is False
    assert "synthetic dispatch failure" in result.message


def test_replay_alert_never_raises_on_http_error(monkeypatch) -> None:
    """A non-2xx HTTP response surfaces as success=False, not an exception."""
    _seed_alert_and_channel()
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(500, text="internal error"),
    )
    result = replay_alert("alert-1", "ch-1", user_id="alice")
    assert result.success is False
    assert "500" in result.message


# ─── replay_alerts (bulk) ─────────────────────────────────────────────────

def test_replay_alerts_returns_one_result_per_id(monkeypatch) -> None:
    # Vary `ticker` so each alert has a distinct dedup_key (the v14
    # window otherwise collapses three identical-keyed alerts into one
    # row, and a1/a2 would silently vanish).
    save_alerts([
        _make_alert(alert_id="a1", ticker="T1"),
        _make_alert(alert_id="a2", ticker="T2"),
        _make_alert(alert_id="a3", ticker="T3"),
    ], user_id="alice")
    save_channel(_make_channel(channel_id="ch-1"), user_id="alice")
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    results = replay_alerts(["a1", "a2", "a3"], "ch-1", user_id="alice")
    assert len(results) == 3
    assert [r.alert_id for r in results] == ["a1", "a2", "a3"]
    assert all(r.success for r in results)


def test_replay_alerts_continues_past_failures(monkeypatch) -> None:
    """One failure in the middle does not abort subsequent replays."""
    save_alerts([
        _make_alert(alert_id="a1", ticker="T1"),
        _make_alert(alert_id="a3", ticker="T3"),  # a2 deliberately missing
    ], user_id="alice")
    save_channel(_make_channel(channel_id="ch-1"), user_id="alice")
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    results = replay_alerts(["a1", "a2", "a3"], "ch-1", user_id="alice")
    assert len(results) == 3
    assert results[0].success is True
    assert results[1].success is False
    assert results[2].success is True


def test_replay_alerts_empty_input_returns_empty_list() -> None:
    assert replay_alerts([], "ch-1", user_id="alice") == []


# ─── replay_alerts_by_filter ──────────────────────────────────────────────

def test_replay_alerts_by_filter_severity(monkeypatch) -> None:
    save_alerts([
        _make_alert(alert_id="a-high",   severity="HIGH"),
        _make_alert(alert_id="a-low",    severity="LOW"),
        _make_alert(alert_id="a-crit",   severity="CRITICAL"),
    ], user_id="alice")
    save_channel(_make_channel(channel_id="ch-1"), user_id="alice")
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    results = replay_alerts_by_filter(
        channel_id="ch-1", user_id="alice", severity="HIGH",
    )
    assert len(results) == 1
    assert results[0].alert_id == "a-high"


def test_replay_alerts_by_filter_alert_type(monkeypatch) -> None:
    save_alerts([
        _make_alert(alert_id="a-bdi", alert_type="BDI_MOVE"),
        _make_alert(alert_id="a-cng", alert_type="CONGESTION"),
    ], user_id="alice")
    save_channel(_make_channel(channel_id="ch-1"), user_id="alice")
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    results = replay_alerts_by_filter(
        channel_id="ch-1", user_id="alice", alert_type="BDI_MOVE",
    )
    assert len(results) == 1
    assert results[0].alert_id == "a-bdi"


def test_replay_alerts_by_filter_since_until(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    save_alerts([
        _make_alert(
            alert_id="a-old",
            created_at=(now - timedelta(days=10)).isoformat(),
        ),
        _make_alert(
            alert_id="a-recent",
            created_at=(now - timedelta(days=1)).isoformat(),
        ),
    ], user_id="alice")
    save_channel(_make_channel(channel_id="ch-1"), user_id="alice")
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    since_iso = (now - timedelta(days=5)).isoformat()
    results = replay_alerts_by_filter(
        channel_id="ch-1", user_id="alice", since=since_iso,
    )
    assert len(results) == 1
    assert results[0].alert_id == "a-recent"


def test_replay_alerts_by_filter_caps_at_limit(monkeypatch) -> None:
    """``limit`` controls the number of alerts replayed; the helper
    NEVER fires more than the cap."""
    save_alerts([
        _make_alert(alert_id=f"a-{i}", ticker=f"T{i}")
        for i in range(10)
    ], user_id="alice")
    save_channel(_make_channel(channel_id="ch-1"), user_id="alice")
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    results = replay_alerts_by_filter(
        channel_id="ch-1", user_id="alice", limit=3,
    )
    assert len(results) == 3


def test_replay_alerts_by_filter_clamps_to_max(monkeypatch) -> None:
    """A limit larger than MAX_REPLAY_LIMIT is silently clamped down."""
    save_alerts([
        _make_alert(alert_id=f"a-{i}", ticker=f"T{i}")
        for i in range(5)
    ], user_id="alice")
    save_channel(_make_channel(channel_id="ch-1"), user_id="alice")
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    # The cap is 200 — asking for 10**6 must not even attempt that many.
    # With only 5 alerts seeded we just confirm the call succeeds and the
    # result count matches available alerts (i.e. the clamp didn't fail
    # the call). The defining property is "no exception, all 5 replay".
    results = replay_alerts_by_filter(
        channel_id="ch-1", user_id="alice", limit=10**6,
    )
    assert len(results) == 5
    assert MAX_REPLAY_LIMIT < 10**6  # sanity check


def test_replay_alerts_by_filter_per_user_scoping(monkeypatch) -> None:
    """alice's by-filter call must only see alice's alerts, NEVER bob's."""
    save_alerts([_make_alert(alert_id="alice-1")], user_id="alice")
    save_alerts([_make_alert(alert_id="bob-1",  ticker="BOB")], user_id="bob")
    save_channel(_make_channel(channel_id="ch-1"), user_id="alice")
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    results = replay_alerts_by_filter(channel_id="ch-1", user_id="alice")
    assert len(results) == 1
    assert results[0].alert_id == "alice-1"


def test_replay_alerts_by_filter_zero_limit_returns_empty() -> None:
    """A non-positive limit short-circuits to [] without any DB work."""
    assert replay_alerts_by_filter(
        channel_id="ch-1", user_id="alice", limit=0,
    ) == []
    assert replay_alerts_by_filter(
        channel_id="ch-1", user_id="alice", limit=-5,
    ) == []


# ─── parse_relative_since ─────────────────────────────────────────────────

def test_parse_relative_since_days() -> None:
    iso = parse_relative_since("7d")
    assert iso is not None
    parsed = datetime.fromisoformat(iso)
    expected = datetime.now(timezone.utc) - timedelta(days=7)
    # Within 60 seconds of expected — the call itself takes some time.
    assert abs((parsed - expected).total_seconds()) < 60


def test_parse_relative_since_hours() -> None:
    iso = parse_relative_since("24h")
    assert iso is not None


def test_parse_relative_since_minutes() -> None:
    iso = parse_relative_since("30m")
    assert iso is not None


def test_parse_relative_since_rejects_malformed() -> None:
    for bad in ["", "abc", "7", "7x", "-3d", "0d", None, 7]:
        assert parse_relative_since(bad) is None


# ─── Headline property check ──────────────────────────────────────────────

def test_replay_alert_default_limit_is_safe() -> None:
    """DEFAULT_REPLAY_LIMIT must be small enough to be operator-safe but
    large enough to be useful — the contract a future contributor must
    not weaken without thinking."""
    assert 1 < DEFAULT_REPLAY_LIMIT <= 100
    assert MAX_REPLAY_LIMIT >= DEFAULT_REPLAY_LIMIT
    assert MAX_REPLAY_LIMIT <= 1000  # a 1000-alert blast is the upper bound
