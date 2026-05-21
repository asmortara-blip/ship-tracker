"""Tests for engine.alert_delivery — outbound Slack-webhook delivery.

Covers:
  - DeliveryChannel + DeliveryResult dataclass shapes
  - format_slack_payload: title, body, severity color, value/threshold/
    change_pct fields, contextual ticker/route/port bits
  - deliver_alert: success path (HTTP 200), HTTP 400 failure path,
    ConnectionError, Timeout, channel-disabled skip, below-threshold
    skip, unsupported-kind failure
  - save_channel + load_channels + delete_channel round-trip via SQLite
  - deliver_pending: severity threshold filtering (LOW sees everything,
    CRITICAL sees only CRITICAL), ``since`` cutoff filtering, disabled
    channel returns []
  - Severity-threshold ordering matches alert_engine_v2._SEVERITY_ORDER
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest
import requests

from engine import alert_delivery
from engine.alert_delivery import (
    DeliveryChannel,
    DeliveryResult,
    _meets_threshold,
    delete_channel,
    deliver_alert,
    deliver_pending,
    format_slack_payload,
    load_channels,
    save_channel,
)
from engine.alert_engine_v2 import (
    _SEVERITY_ORDER,
    ShippingAlert,
    save_alerts,
)


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


# ─── Helpers ───────────────────────────────────────────────────────────────

def _make_alert(
    severity: str = "HIGH",
    *,
    alert_id: str = "a1",
    created_at: str | None = None,
    title: str = "Test alert",
    body: str = "Test body description with enough text to render.",
    ticker: str = "ZIM",
    route_id: str = "",
    port_locode: str = "",
    value: float = 1234.56,
    threshold: float = 1000.0,
    change_pct: float = 12.5,
    alert_type: str = "STOCK_MOVE",
) -> ShippingAlert:
    # Treat the sentinel ``None`` as "auto-now" but honor an explicit
    # empty string (used by the context-omission test).
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
    severity_threshold: str = "LOW",
    *,
    channel_id: str = "ch1",
    name: str = "Test channel",
    kind: str = "slack",
    target: str = "https://hooks.slack.com/services/T000/B000/XXXX",
    enabled: bool = True,
) -> DeliveryChannel:
    return DeliveryChannel(
        channel_id=channel_id,
        name=name,
        kind=kind,
        target=target,
        severity_threshold=severity_threshold,
        enabled=enabled,
    )


class _FakeResponse:
    """Stand-in for ``requests.Response`` carrying just ``status_code``
    and ``text``."""

    def __init__(self, status_code: int = 200, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text


# ─── Dataclass shapes ──────────────────────────────────────────────────────

def test_delivery_channel_shape() -> None:
    ch = DeliveryChannel(
        channel_id="cid",
        name="n",
        kind="slack",
        target="https://example.com",
        severity_threshold="HIGH",
        enabled=True,
    )
    assert ch.kind == "slack"
    assert ch.severity_threshold == "HIGH"
    assert ch.enabled is True
    assert ch.created_at == ""


def test_delivery_result_shape_success() -> None:
    r = DeliveryResult(success=True, status_code=200)
    assert r.success is True
    assert r.status_code == 200
    assert r.error_msg == ""


def test_delivery_result_shape_failure() -> None:
    r = DeliveryResult(success=False, status_code=400, error_msg="bad")
    assert r.success is False
    assert r.error_msg == "bad"


# ─── Severity threshold ordering ──────────────────────────────────────────

def test_severity_order_critical_lowest_index() -> None:
    """CRITICAL < HIGH < MEDIUM < LOW per the existing alert engine."""
    assert _SEVERITY_ORDER["CRITICAL"] < _SEVERITY_ORDER["HIGH"]
    assert _SEVERITY_ORDER["HIGH"] < _SEVERITY_ORDER["MEDIUM"]
    assert _SEVERITY_ORDER["MEDIUM"] < _SEVERITY_ORDER["LOW"]


def test_meets_threshold_low_sees_all() -> None:
    assert _meets_threshold("CRITICAL", "LOW")
    assert _meets_threshold("HIGH", "LOW")
    assert _meets_threshold("MEDIUM", "LOW")
    assert _meets_threshold("LOW", "LOW")


def test_meets_threshold_critical_sees_only_critical() -> None:
    assert _meets_threshold("CRITICAL", "CRITICAL")
    assert not _meets_threshold("HIGH", "CRITICAL")
    assert not _meets_threshold("MEDIUM", "CRITICAL")
    assert not _meets_threshold("LOW", "CRITICAL")


def test_meets_threshold_medium_sees_critical_high_medium() -> None:
    assert _meets_threshold("CRITICAL", "MEDIUM")
    assert _meets_threshold("HIGH", "MEDIUM")
    assert _meets_threshold("MEDIUM", "MEDIUM")
    assert not _meets_threshold("LOW", "MEDIUM")


# ─── format_slack_payload ─────────────────────────────────────────────────

def test_format_slack_payload_includes_title_and_body() -> None:
    a = _make_alert(severity="HIGH", title="BDI Surged 7.2% in 1 Day", body="Body text.")
    payload = format_slack_payload(a)
    # Fallback text carries the severity-prefixed title
    assert "BDI Surged 7.2% in 1 Day" in payload["text"]
    assert "HIGH" in payload["text"]
    # Body lives in one of the blocks
    blocks = payload["attachments"][0]["blocks"]
    block_text = " ".join(
        str(b.get("text", {}).get("text", "") if isinstance(b.get("text"), dict) else b.get("text", ""))
        for b in blocks
    )
    assert "Body text." in block_text


def test_format_slack_payload_severity_colors() -> None:
    cases = {
        "CRITICAL": "#d73a49",
        "HIGH":     "#f66a0a",
        "MEDIUM":   "#f1c40f",
        "LOW":      "#6a737d",
    }
    for sev, expected_color in cases.items():
        payload = format_slack_payload(_make_alert(severity=sev))
        assert payload["attachments"][0]["color"] == expected_color, sev


def test_format_slack_payload_includes_value_threshold_change_fields() -> None:
    a = _make_alert(value=2345.67, threshold=1000.0, change_pct=23.4)
    payload = format_slack_payload(a)
    blocks = payload["attachments"][0]["blocks"]
    # The fields block carries Value / Threshold / Change %
    field_blocks = [b for b in blocks if b["type"] == "section" and "fields" in b]
    assert field_blocks, "expected at least one fields-style section block"
    all_field_text = " ".join(f["text"] for f in field_blocks[0]["fields"])
    assert "Value" in all_field_text
    assert "2,345.67" in all_field_text
    assert "Threshold" in all_field_text
    assert "1,000.00" in all_field_text
    assert "Change %" in all_field_text
    assert "23.40" in all_field_text


def test_format_slack_payload_context_omitted_when_empty() -> None:
    a = _make_alert(ticker="", route_id="", port_locode="", created_at="")
    payload = format_slack_payload(a)
    blocks = payload["attachments"][0]["blocks"]
    assert not any(b["type"] == "context" for b in blocks)


def test_format_slack_payload_context_present_when_ticker_set() -> None:
    a = _make_alert(ticker="ZIM")
    payload = format_slack_payload(a)
    blocks = payload["attachments"][0]["blocks"]
    ctx_blocks = [b for b in blocks if b["type"] == "context"]
    assert ctx_blocks
    assert any("ZIM" in el["text"] for el in ctx_blocks[0]["elements"])


# ─── deliver_alert ────────────────────────────────────────────────────────

def test_deliver_alert_success_200(monkeypatch) -> None:
    calls = {}

    def fake_post(url, json=None, timeout=None, **kw):
        calls["url"] = url
        calls["json"] = json
        calls["timeout"] = timeout
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    result = deliver_alert(_make_alert("HIGH"), _make_channel("LOW"))
    assert result.success is True
    assert result.status_code == 200
    assert result.error_msg == ""
    assert calls["url"].startswith("https://hooks.slack.com/")
    assert calls["timeout"] == 10.0
    assert "attachments" in calls["json"]


def test_deliver_alert_http_400_returns_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(status_code=400, text="invalid_payload"),
    )
    result = deliver_alert(_make_alert("HIGH"), _make_channel("LOW"))
    assert result.success is False
    assert result.status_code == 400
    assert "400" in result.error_msg
    assert "invalid_payload" in result.error_msg


def test_deliver_alert_connection_error(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(alert_delivery.requests, "post", boom)
    result = deliver_alert(_make_alert("HIGH"), _make_channel("LOW"))
    assert result.success is False
    assert result.status_code == 0
    assert "connection error" in result.error_msg
    assert "no route to host" in result.error_msg


def test_deliver_alert_timeout(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.exceptions.Timeout("read timed out")

    monkeypatch.setattr(alert_delivery.requests, "post", boom)
    result = deliver_alert(_make_alert("HIGH"), _make_channel("LOW"))
    assert result.success is False
    assert result.status_code == 0
    assert "timeout" in result.error_msg.lower()


def test_deliver_alert_disabled_channel_skips(monkeypatch) -> None:
    """A disabled channel returns success=True (no-op) and never POSTs."""
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel("LOW", enabled=False)
    result = deliver_alert(_make_alert("CRITICAL"), channel)
    assert result.success is True
    assert called["n"] == 0
    assert "disabled" in result.error_msg


def test_deliver_alert_below_threshold_skips(monkeypatch) -> None:
    """A LOW alert against a CRITICAL channel never POSTs."""
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel("CRITICAL")
    result = deliver_alert(_make_alert("LOW"), channel)
    assert result.success is True
    assert called["n"] == 0
    assert "below threshold" in result.error_msg


def test_deliver_alert_unsupported_kind_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    channel = _make_channel("LOW", kind="email")
    result = deliver_alert(_make_alert("HIGH"), channel)
    assert result.success is False
    assert "unsupported channel kind" in result.error_msg
    assert "email" in result.error_msg


# ─── Channel persistence ──────────────────────────────────────────────────

def test_save_channel_and_load_channels_round_trip() -> None:
    ch = _make_channel(channel_id="c1", name="Trading desk", severity_threshold="HIGH")
    save_channel(ch)
    loaded = load_channels()
    assert len(loaded) == 1
    got = loaded[0]
    assert got.channel_id == "c1"
    assert got.name == "Trading desk"
    assert got.kind == "slack"
    assert got.severity_threshold == "HIGH"
    assert got.enabled is True
    assert got.created_at  # populated server-side


def test_save_channel_populates_created_at_on_dataclass() -> None:
    ch = _make_channel()
    assert ch.created_at == ""
    save_channel(ch)
    assert ch.created_at  # mirrored back onto the input


def test_save_channel_upsert_overwrites_existing() -> None:
    ch = _make_channel(channel_id="c1", name="Original")
    save_channel(ch)
    ch2 = _make_channel(channel_id="c1", name="Renamed", severity_threshold="CRITICAL")
    save_channel(ch2)
    loaded = load_channels()
    assert len(loaded) == 1
    assert loaded[0].name == "Renamed"
    assert loaded[0].severity_threshold == "CRITICAL"


def test_load_channels_returns_empty_when_table_empty() -> None:
    assert load_channels() == []


def test_delete_channel_removes_only_specified() -> None:
    save_channel(_make_channel(channel_id="c1", name="A"))
    save_channel(_make_channel(channel_id="c2", name="B"))
    delete_channel("c1")
    loaded = load_channels()
    assert len(loaded) == 1
    assert loaded[0].channel_id == "c2"


def test_delete_channel_missing_is_no_op() -> None:
    save_channel(_make_channel(channel_id="c1"))
    delete_channel("does-not-exist")
    assert len(load_channels()) == 1


def test_save_channel_preserves_enabled_false() -> None:
    save_channel(_make_channel(channel_id="c1", enabled=False))
    loaded = load_channels()
    assert loaded[0].enabled is False


# ─── deliver_pending ──────────────────────────────────────────────────────

def _seed_alerts() -> tuple[datetime, list[ShippingAlert]]:
    """Persist four alerts (one per severity) and return the cutoff
    (older than every alert) plus the alert list."""
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    alerts: list[ShippingAlert] = []
    for i, sev in enumerate(["CRITICAL", "HIGH", "MEDIUM", "LOW"]):
        ts = (base + timedelta(minutes=i)).isoformat()
        alerts.append(_make_alert(
            severity=sev,
            alert_id=f"a-{sev.lower()}",
            created_at=ts,
            title=f"{sev} alert",
        ))
    save_alerts(alerts)
    cutoff = base - timedelta(minutes=1)  # older than every alert
    return cutoff, alerts


def test_deliver_pending_low_threshold_sees_everything(monkeypatch) -> None:
    cutoff, _ = _seed_alerts()
    calls = []

    def fake_post(url, json=None, timeout=None, **kw):
        calls.append(json)
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    results = deliver_pending(_make_channel("LOW"), cutoff)
    assert len(results) == 4
    assert all(r.success for r in results)
    assert len(calls) == 4


def test_deliver_pending_critical_threshold_sees_only_critical(monkeypatch) -> None:
    cutoff, _ = _seed_alerts()
    calls = []

    def fake_post(url, json=None, timeout=None, **kw):
        calls.append(json)
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    results = deliver_pending(_make_channel("CRITICAL"), cutoff)
    assert len(results) == 1
    assert results[0].success
    # The one POSTed payload was the CRITICAL alert
    assert "CRITICAL" in calls[0]["text"]


def test_deliver_pending_high_threshold_sees_high_and_critical(monkeypatch) -> None:
    cutoff, _ = _seed_alerts()
    posted_severities: list[str] = []

    def fake_post(url, json=None, timeout=None, **kw):
        # The fallback text starts with "[SEVERITY] ..."
        posted_severities.append(json["text"].split("]")[0].strip("["))
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    results = deliver_pending(_make_channel("HIGH"), cutoff)
    assert len(results) == 2
    assert set(posted_severities) == {"CRITICAL", "HIGH"}


def test_deliver_pending_since_cutoff_filters(monkeypatch) -> None:
    """An alert older than ``since`` is skipped even when severity matches."""
    older = datetime.now(timezone.utc) - timedelta(days=2)
    newer = datetime.now(timezone.utc) - timedelta(minutes=5)
    save_alerts([
        _make_alert(severity="HIGH", alert_id="old", created_at=older.isoformat()),
        _make_alert(severity="HIGH", alert_id="new", created_at=newer.isoformat()),
    ])

    calls = []

    def fake_post(url, json=None, timeout=None, **kw):
        calls.append(json["text"])
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    # since = 1 day ago → only the newer alert qualifies
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    results = deliver_pending(_make_channel("LOW"), cutoff)
    assert len(results) == 1
    assert len(calls) == 1


def test_deliver_pending_disabled_channel_returns_empty(monkeypatch) -> None:
    cutoff, _ = _seed_alerts()
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    results = deliver_pending(_make_channel("LOW", enabled=False), cutoff)
    assert results == []
    assert called["n"] == 0


def test_deliver_pending_no_alerts_returns_empty(monkeypatch) -> None:
    """Empty DB → empty result list, no POST."""
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    results = deliver_pending(_make_channel("LOW"), cutoff)
    assert results == []
    assert called["n"] == 0


def test_deliver_pending_propagates_http_failure(monkeypatch) -> None:
    """When the webhook returns 500, deliver_pending still returns the
    failed result rather than dropping it."""
    cutoff, _ = _seed_alerts()

    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(status_code=500, text="oops"),
    )
    results = deliver_pending(_make_channel("LOW"), cutoff)
    assert len(results) == 4
    assert all(not r.success for r in results)
    assert all(r.status_code == 500 for r in results)
