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

import smtplib
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest
import requests

from engine import alert_delivery
from engine.alert_delivery import (
    DeliveryChannel,
    DeliveryResult,
    _deliver_discord,
    _deliver_email,
    _deliver_pagerduty,
    _deliver_sms,
    _deliver_webhook,
    _get_smtp_config,
    _get_twilio_config,
    _meets_threshold,
    _PAGERDUTY_EVENTS_URL,
    _SmtpConfig,
    _TwilioConfig,
    delete_channel,
    deliver_alert,
    deliver_pending,
    format_discord_payload,
    format_email_payload,
    format_pagerduty_payload,
    format_slack_payload,
    format_sms_payload,
    format_webhook_payload,
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
    # "made_up" is not a real backend; using it must fail so alerts
    # aren't silently dropped on a half-configured channel.
    channel = _make_channel("LOW", kind="made_up")
    result = deliver_alert(_make_alert("HIGH"), channel)
    assert result.success is False
    assert "unsupported channel kind" in result.error_msg
    assert "made_up" in result.error_msg


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


def test_save_channel_persists_digest_mode_daily() -> None:
    """v6 schema added digest_mode — saving a channel with mode='daily'
    must round-trip back as 'daily' (was silently dropped before the
    save_channel / load_channels CRUD wiring was completed)."""
    ch = _make_channel(channel_id="c1", name="Digest desk")
    ch.digest_mode = "daily"
    save_channel(ch)
    loaded = load_channels()
    assert len(loaded) == 1
    assert loaded[0].digest_mode == "daily"


def test_save_channel_invalid_digest_mode_falls_back_to_immediate() -> None:
    """A stale or invalid digest_mode string in the dataclass must not
    poison the DB — save_channel coerces back to 'immediate'."""
    ch = _make_channel(channel_id="c1")
    ch.digest_mode = "weekly"  # not a supported value
    save_channel(ch)
    assert load_channels()[0].digest_mode == "immediate"


def test_load_channels_defaults_digest_mode_to_immediate_for_pre_v6_rows() -> None:
    """Channels saved before the v6 column existed return digest_mode=
    'immediate' (the column default), preserving prior behaviour."""
    # Insert a row WITHOUT digest_mode — relying on the DEFAULT
    from state.db import get_connection

    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO delivery_channels
              (channel_id, name, kind, target, severity_threshold,
               enabled, created_at)
            VALUES ('c-pre-v6', 'Legacy', 'slack',
                    'https://hooks.slack.com/services/x',
                    'HIGH', 1, '2026-05-21T00:00:00+00:00')
            """
        )
    loaded = load_channels()
    assert len(loaded) == 1
    assert loaded[0].digest_mode == "immediate"


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


# ─── Email backend: helpers ───────────────────────────────────────────────


def _clear_smtp_env(monkeypatch) -> None:
    """Strip all SMTP_* env vars so _get_smtp_config sees a clean slate."""
    for key in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM_ADDRESS"):
        monkeypatch.delenv(key, raising=False)


def _set_smtp_env(monkeypatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "alerts@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "hunter2")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "alerts@example.com")


class _FakeSMTP:
    """Stand-in for ``smtplib.SMTP`` that records the call sequence and
    can be configured to raise on individual methods."""

    instances: list["_FakeSMTP"] = []  # populated when the constructor is called

    def __init__(
        self,
        host: str = "",
        port: int = 0,
        timeout: float | None = None,
        *,
        starttls_raises: BaseException | None = None,
        login_raises: BaseException | None = None,
        sendmail_raises: BaseException | None = None,
        ctor_raises: BaseException | None = None,
    ) -> None:
        if ctor_raises is not None:
            raise ctor_raises
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_raises = starttls_raises
        self.login_raises = login_raises
        self.sendmail_raises = sendmail_raises
        self.calls: list[tuple] = [("__init__", host, port, timeout)]
        _FakeSMTP.instances.append(self)

    def starttls(self) -> None:
        self.calls.append(("starttls",))
        if self.starttls_raises is not None:
            raise self.starttls_raises

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user, password))
        if self.login_raises is not None:
            raise self.login_raises

    def sendmail(self, from_addr: str, to_addrs, msg: str) -> None:
        self.calls.append(("sendmail", from_addr, tuple(to_addrs), msg))
        if self.sendmail_raises is not None:
            raise self.sendmail_raises

    def quit(self) -> None:
        self.calls.append(("quit",))


def _install_fake_smtp(monkeypatch, **kw):
    """Patch ``alert_delivery.smtplib.SMTP`` to construct a ``_FakeSMTP``
    pre-configured with the given kwargs. Returns the ``instances`` list
    so callers can inspect after delivery."""
    _FakeSMTP.instances = []

    def factory(host, port, timeout=None):
        return _FakeSMTP(host, port, timeout, **kw)

    monkeypatch.setattr(alert_delivery.smtplib, "SMTP", factory)
    return _FakeSMTP.instances


# ─── format_email_payload ──────────────────────────────────────────────────


def test_format_email_payload_subject_prefix_and_severity_color() -> None:
    cases = {
        "CRITICAL": "#d73a49",
        "HIGH":     "#f66a0a",
        "MEDIUM":   "#f1c40f",
        "LOW":      "#6a737d",
    }
    for sev, expected_color in cases.items():
        alert = _make_alert(severity=sev, title="BDI Surged 7.2% in 1 Day")
        payload = format_email_payload(alert)
        # Subject is "[SEVERITY] title"
        assert payload["subject"] == f"[{sev}] BDI Surged 7.2% in 1 Day"
        # HTML body carries the matching severity colour
        assert expected_color in payload["html_body"], (sev, expected_color)


def test_format_email_payload_html_includes_value_threshold_change() -> None:
    alert = _make_alert(
        severity="HIGH",
        value=2345.67,
        threshold=1000.0,
        change_pct=23.4,
        body="Drewry SCFI jumped sharply overnight.",
    )
    payload = format_email_payload(alert)
    html = payload["html_body"]
    # Subject + body content surface in the HTML
    assert "Drewry SCFI jumped sharply overnight." in html
    # Severity colour swatch
    assert "#f66a0a" in html
    # Formatted numeric fields (the same shape used in slack)
    assert "Value" in html
    assert "2,345.67" in html
    assert "Threshold" in html
    assert "1,000.00" in html
    assert "Change %" in html
    assert "23.40" in html or "+23.40" in html


def test_format_email_payload_text_body_is_plain_text() -> None:
    alert = _make_alert(severity="MEDIUM", title="Suez throughput dipped", body="See chart.")
    payload = format_email_payload(alert)
    text = payload["text_body"]
    # No HTML tags in the plain-text fallback
    assert "<html" not in text.lower()
    assert "<table" not in text.lower()
    assert "<div" not in text.lower()
    # But it still carries the key fields
    assert "Suez throughput dipped" in text
    assert "MEDIUM" in text
    assert "Value:" in text
    assert "Threshold:" in text
    assert "Change %" in text


def test_format_email_payload_omits_context_rows_when_empty() -> None:
    alert = _make_alert(ticker="", route_id="", port_locode="", created_at="")
    payload = format_email_payload(alert)
    # Without any context bits, none of the "Ticker"/"Route"/"Port"/"At"
    # labels should leak into the HTML.
    html = payload["html_body"]
    assert "Ticker" not in html
    assert "Route" not in html
    assert ">Port<" not in html  # avoid false positive on "Threshold"
    assert ">At<" not in html


# ─── _get_smtp_config ──────────────────────────────────────────────────────


def test_get_smtp_config_returns_none_when_env_missing(monkeypatch) -> None:
    _clear_smtp_env(monkeypatch)
    # _get_smtp_config tries st.secrets first (wrapped in try/except).
    # In this test env no secrets.toml is present, so the secrets read
    # raises and we fall through to env vars, which we just cleared.
    assert _get_smtp_config() is None


def test_get_smtp_config_returns_none_when_only_partial_env(monkeypatch) -> None:
    _clear_smtp_env(monkeypatch)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "alerts@example.com")
    # missing SMTP_PASSWORD + SMTP_FROM_ADDRESS
    assert _get_smtp_config() is None


def test_get_smtp_config_builds_from_env(monkeypatch) -> None:
    _clear_smtp_env(monkeypatch)
    _set_smtp_env(monkeypatch)
    cfg = _get_smtp_config()
    assert cfg is not None
    assert isinstance(cfg, _SmtpConfig)
    assert cfg.host == "smtp.example.com"
    assert cfg.port == 587
    assert cfg.user == "alerts@example.com"
    assert cfg.password == "hunter2"
    assert cfg.from_addr == "alerts@example.com"


def test_get_smtp_config_defaults_port_587_when_unset(monkeypatch) -> None:
    _clear_smtp_env(monkeypatch)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "alerts@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "hunter2")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "alerts@example.com")
    # No SMTP_PORT set
    cfg = _get_smtp_config()
    assert cfg is not None
    assert cfg.port == 587


def test_get_smtp_config_returns_none_on_unparsable_port(monkeypatch) -> None:
    _clear_smtp_env(monkeypatch)
    _set_smtp_env(monkeypatch)
    monkeypatch.setenv("SMTP_PORT", "not-a-number")
    assert _get_smtp_config() is None


# ─── _deliver_email (SMTP mocked end-to-end) ───────────────────────────────


def _smtp_cfg() -> _SmtpConfig:
    return _SmtpConfig(
        host="smtp.example.com",
        port=587,
        user="alerts@example.com",
        password="hunter2",
        from_addr="alerts@example.com",
    )


def test_deliver_email_success_runs_starttls_login_sendmail(monkeypatch) -> None:
    instances = _install_fake_smtp(monkeypatch)
    channel = _make_channel("LOW", kind="email", target="ops@example.com")
    alert = _make_alert(severity="HIGH", title="ZIM dropped 8%")

    result = _deliver_email(channel, alert, _smtp_cfg())
    assert result.success is True
    assert result.error_msg == ""

    # The connection sequence happened in order: ctor → starttls → login →
    # sendmail → quit.
    assert len(instances) == 1
    seq = [c[0] for c in instances[0].calls]
    assert seq == ["__init__", "starttls", "login", "sendmail", "quit"]

    # Login credentials forwarded from config
    login_call = next(c for c in instances[0].calls if c[0] == "login")
    assert login_call[1] == "alerts@example.com"
    assert login_call[2] == "hunter2"

    # sendmail addressed to the channel target
    send_call = next(c for c in instances[0].calls if c[0] == "sendmail")
    assert send_call[1] == "alerts@example.com"      # from
    assert send_call[2] == ("ops@example.com",)      # to
    raw_msg = send_call[3]
    assert "Subject: [HIGH] ZIM dropped 8%" in raw_msg


def test_deliver_email_uses_10s_timeout(monkeypatch) -> None:
    instances = _install_fake_smtp(monkeypatch)
    channel = _make_channel("LOW", kind="email", target="ops@example.com")
    _deliver_email(channel, _make_alert("HIGH"), _smtp_cfg())
    init_call = instances[0].calls[0]
    assert init_call[0] == "__init__"
    assert init_call[3] == 10.0


def test_deliver_email_auth_error_returns_failure(monkeypatch) -> None:
    _install_fake_smtp(
        monkeypatch,
        login_raises=smtplib.SMTPAuthenticationError(535, b"auth failed"),
    )
    channel = _make_channel("LOW", kind="email", target="ops@example.com")
    result = _deliver_email(channel, _make_alert("HIGH"), _smtp_cfg())
    assert result.success is False
    assert "smtp auth" in result.error_msg.lower()


def test_deliver_email_recipient_refused_returns_failure(monkeypatch) -> None:
    _install_fake_smtp(
        monkeypatch,
        sendmail_raises=smtplib.SMTPRecipientsRefused({"ops@example.com": (550, b"no such user")}),
    )
    channel = _make_channel("LOW", kind="email", target="ops@example.com")
    result = _deliver_email(channel, _make_alert("HIGH"), _smtp_cfg())
    assert result.success is False
    assert "recipient refused" in result.error_msg.lower()


def test_deliver_email_connection_error_returns_failure(monkeypatch) -> None:
    _install_fake_smtp(monkeypatch, ctor_raises=ConnectionError("no route to host"))
    channel = _make_channel("LOW", kind="email", target="ops@example.com")
    result = _deliver_email(channel, _make_alert("HIGH"), _smtp_cfg())
    assert result.success is False
    assert "connection error" in result.error_msg.lower()
    assert "no route to host" in result.error_msg


def test_deliver_email_generic_smtp_exception_returns_failure(monkeypatch) -> None:
    _install_fake_smtp(monkeypatch, sendmail_raises=smtplib.SMTPException("queue full"))
    channel = _make_channel("LOW", kind="email", target="ops@example.com")
    result = _deliver_email(channel, _make_alert("HIGH"), _smtp_cfg())
    assert result.success is False
    assert "smtp error" in result.error_msg.lower()


# ─── deliver_alert dispatch on kind="email" ────────────────────────────────


def test_deliver_alert_email_no_config_returns_failure(monkeypatch) -> None:
    """kind=email with no SMTP credentials → failure with explicit msg."""
    _clear_smtp_env(monkeypatch)
    # Belt-and-suspenders: also patch _get_smtp_config to return None so
    # any stray st.secrets in the test env can't satisfy it.
    monkeypatch.setattr(alert_delivery, "_get_smtp_config", lambda: None)

    channel = _make_channel("LOW", kind="email", target="ops@example.com")
    result = deliver_alert(_make_alert("HIGH"), channel)
    assert result.success is False
    assert result.error_msg == "SMTP not configured"


def test_deliver_alert_email_with_config_succeeds(monkeypatch) -> None:
    """kind=email with configured SMTP → routes through _deliver_email
    and returns success when the (mocked) connection round-trips."""
    _clear_smtp_env(monkeypatch)
    monkeypatch.setattr(alert_delivery, "_get_smtp_config", _smtp_cfg)
    instances = _install_fake_smtp(monkeypatch)

    channel = _make_channel("LOW", kind="email", target="ops@example.com")
    result = deliver_alert(_make_alert("HIGH"), channel)
    assert result.success is True
    # And the SMTP connection was actually exercised
    assert len(instances) == 1
    assert any(c[0] == "sendmail" for c in instances[0].calls)


def test_deliver_alert_email_below_threshold_skips_smtp(monkeypatch) -> None:
    """Severity gating runs before transport selection — a below-threshold
    email channel must not touch SMTP at all."""
    monkeypatch.setattr(alert_delivery, "_get_smtp_config", _smtp_cfg)
    instances = _install_fake_smtp(monkeypatch)

    channel = _make_channel("CRITICAL", kind="email", target="ops@example.com")
    result = deliver_alert(_make_alert("LOW"), channel)
    assert result.success is True
    assert "below threshold" in result.error_msg
    # No SMTP instance was constructed
    assert instances == []


def test_deliver_alert_email_disabled_skips_smtp(monkeypatch) -> None:
    monkeypatch.setattr(alert_delivery, "_get_smtp_config", _smtp_cfg)
    instances = _install_fake_smtp(monkeypatch)

    channel = _make_channel("LOW", kind="email", target="ops@example.com", enabled=False)
    result = deliver_alert(_make_alert("CRITICAL"), channel)
    assert result.success is True
    assert "disabled" in result.error_msg
    assert instances == []


# ─── SMS backend: helpers ──────────────────────────────────────────────────


def _clear_twilio_env(monkeypatch) -> None:
    """Strip all TWILIO_* env vars so _get_twilio_config sees a clean slate."""
    for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"):
        monkeypatch.delenv(key, raising=False)


def _set_twilio_env(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest1234567890abcdef1234567890ab")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret_token_value")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15559876543")


def _twilio_cfg() -> _TwilioConfig:
    return _TwilioConfig(
        account_sid="ACtest1234567890abcdef1234567890ab",
        auth_token="secret_token_value",
        from_number="+15559876543",
    )


class _FakeTwilioResponse:
    """Stand-in for ``requests.Response`` for Twilio calls — carries
    ``status_code``, ``text``, and a ``json()`` method that returns a
    pre-baked dict (or raises if ``json_payload`` is None)."""

    def __init__(
        self,
        status_code: int = 201,
        text: str = "",
        json_payload: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self._json_payload = json_payload

    def json(self) -> dict:
        if self._json_payload is None:
            raise ValueError("no json body")
        return self._json_payload


# ─── format_sms_payload ────────────────────────────────────────────────────


def test_format_sms_payload_includes_severity_prefix_and_title() -> None:
    a = _make_alert(severity="HIGH", title="BDI Surged 7.2% in 1 Day", body="Body text.")
    payload = format_sms_payload(a)
    body = payload["body"]
    assert body.startswith("[HIGH] BDI Surged 7.2% in 1 Day")
    # Body text follows the header on its own line
    assert "Body text." in body
    assert "\n" in body


def test_format_sms_payload_respects_280_char_body_cap() -> None:
    long_body = "x" * 1000
    a = _make_alert(severity="MEDIUM", title="t", body=long_body, value=0, threshold=0)
    payload = format_sms_payload(a)
    body = payload["body"]
    # The truncated body portion should not exceed 280 chars and ends with "..."
    # Pull the line that holds the original body.
    body_line = body.split("\n", 1)[1]  # everything after "[MEDIUM] t\n"
    assert len(body_line) <= 280
    assert body_line.endswith("...")


def test_format_sms_payload_includes_value_threshold_footer_when_nonzero() -> None:
    a = _make_alert(severity="HIGH", value=2345.67, threshold=1000.0)
    payload = format_sms_payload(a)
    body = payload["body"]
    assert "2,345.67" in body
    assert "1,000.00" in body
    assert "Value" in body
    assert "Threshold" in body


def test_format_sms_payload_omits_footer_when_value_and_threshold_zero() -> None:
    a = _make_alert(severity="LOW", value=0.0, threshold=0.0)
    payload = format_sms_payload(a)
    body = payload["body"]
    # No noisy "Value 0.00 / Threshold 0.00" tail
    assert "Value" not in body
    assert "Threshold" not in body


def test_format_sms_payload_returns_dict_with_body_key() -> None:
    payload = format_sms_payload(_make_alert(severity="HIGH"))
    assert isinstance(payload, dict)
    assert "body" in payload
    assert isinstance(payload["body"], str)


# ─── _get_twilio_config ────────────────────────────────────────────────────


def test_get_twilio_config_returns_none_when_env_missing(monkeypatch) -> None:
    _clear_twilio_env(monkeypatch)
    # st.secrets path returns "" or raises in the test harness, so missing
    # env vars => None.
    assert _get_twilio_config() is None


def test_get_twilio_config_returns_none_when_only_partial_env(monkeypatch) -> None:
    _clear_twilio_env(monkeypatch)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACxxx")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    # missing TWILIO_FROM_NUMBER
    assert _get_twilio_config() is None


def test_get_twilio_config_builds_from_env(monkeypatch) -> None:
    _clear_twilio_env(monkeypatch)
    _set_twilio_env(monkeypatch)
    cfg = _get_twilio_config()
    assert cfg is not None
    assert isinstance(cfg, _TwilioConfig)
    assert cfg.account_sid == "ACtest1234567890abcdef1234567890ab"
    assert cfg.auth_token == "secret_token_value"
    assert cfg.from_number == "+15559876543"


# ─── _deliver_sms (Twilio mocked end-to-end) ───────────────────────────────


def test_deliver_sms_success_201(monkeypatch) -> None:
    calls = {}

    def fake_post(url, auth=None, data=None, timeout=None, **kw):
        calls["url"] = url
        calls["auth"] = auth
        calls["data"] = data
        calls["timeout"] = timeout
        return _FakeTwilioResponse(
            status_code=201,
            json_payload={"sid": "SM_test_sid_123"},
        )

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel("LOW", kind="sms", target="+15551234567")
    alert = _make_alert(severity="HIGH", title="ZIM dropped 8%")

    result = _deliver_sms(channel, alert, _twilio_cfg())
    assert result.success is True
    assert result.status_code == 201
    assert result.error_msg == ""

    # Twilio URL embeds the AccountSid
    assert calls["url"].endswith(
        "/Accounts/ACtest1234567890abcdef1234567890ab/Messages.json"
    )
    assert calls["url"].startswith("https://api.twilio.com/2010-04-01/")
    # HTTP Basic auth tuple (sid, token)
    assert calls["auth"] == ("ACtest1234567890abcdef1234567890ab", "secret_token_value")
    # Form-encoded payload uses To/From/Body
    assert calls["data"]["To"] == "+15551234567"
    assert calls["data"]["From"] == "+15559876543"
    assert "[HIGH] ZIM dropped 8%" in calls["data"]["Body"]
    # 10s timeout matching the Slack/email pattern
    assert calls["timeout"] == 10.0


def test_deliver_sms_400_returns_failure_with_twilio_message(monkeypatch) -> None:
    def fake_post(url, auth=None, data=None, timeout=None, **kw):
        return _FakeTwilioResponse(
            status_code=400,
            text='{"code": 21211, "message": "Invalid number"}',
            json_payload={"code": 21211, "message": "Invalid number"},
        )

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel("LOW", kind="sms", target="+15551234567")
    result = _deliver_sms(channel, _make_alert("HIGH"), _twilio_cfg())
    assert result.success is False
    assert result.status_code == 400
    assert "Invalid number" in result.error_msg


def test_deliver_sms_connection_error_returns_failure(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.exceptions.ConnectionError("no route to twilio")

    monkeypatch.setattr(alert_delivery.requests, "post", boom)
    channel = _make_channel("LOW", kind="sms", target="+15551234567")
    result = _deliver_sms(channel, _make_alert("HIGH"), _twilio_cfg())
    assert result.success is False
    assert result.status_code == 0
    assert "connection error" in result.error_msg
    assert "no route to twilio" in result.error_msg


def test_deliver_sms_timeout_returns_failure(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.exceptions.Timeout("twilio read timed out")

    monkeypatch.setattr(alert_delivery.requests, "post", boom)
    channel = _make_channel("LOW", kind="sms", target="+15551234567")
    result = _deliver_sms(channel, _make_alert("HIGH"), _twilio_cfg())
    assert result.success is False
    assert result.status_code == 0
    assert "timeout" in result.error_msg.lower()


# ─── deliver_alert dispatch on kind="sms" ──────────────────────────────────


def test_deliver_alert_sms_no_config_returns_failure(monkeypatch) -> None:
    """kind=sms with no Twilio credentials → failure with explicit msg."""
    _clear_twilio_env(monkeypatch)
    # Belt-and-suspenders: also patch _get_twilio_config to return None so
    # any stray st.secrets in the test env can't satisfy it.
    monkeypatch.setattr(alert_delivery, "_get_twilio_config", lambda: None)

    channel = _make_channel("LOW", kind="sms", target="+15551234567")
    result = deliver_alert(_make_alert("HIGH"), channel)
    assert result.success is False
    assert result.error_msg == "Twilio not configured"


def test_deliver_alert_sms_with_config_succeeds(monkeypatch) -> None:
    """kind=sms with configured Twilio → routes through _deliver_sms and
    returns success when the (mocked) HTTP call returns 201."""
    _clear_twilio_env(monkeypatch)
    monkeypatch.setattr(alert_delivery, "_get_twilio_config", _twilio_cfg)

    calls = {"n": 0}

    def fake_post(url, auth=None, data=None, timeout=None, **kw):
        calls["n"] += 1
        return _FakeTwilioResponse(
            status_code=201,
            json_payload={"sid": "SM_test_sid_123"},
        )

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel("LOW", kind="sms", target="+15551234567")
    result = deliver_alert(_make_alert("HIGH"), channel)
    assert result.success is True
    assert result.status_code == 201
    assert calls["n"] == 1


def test_deliver_alert_sms_below_threshold_skips_twilio(monkeypatch) -> None:
    """Severity gating runs before transport selection — a below-threshold
    SMS channel must not touch Twilio at all."""
    monkeypatch.setattr(alert_delivery, "_get_twilio_config", _twilio_cfg)
    calls = {"n": 0}

    def fake_post(*a, **kw):
        calls["n"] += 1
        return _FakeTwilioResponse(201, json_payload={"sid": "x"})

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel("CRITICAL", kind="sms", target="+15551234567")
    result = deliver_alert(_make_alert("LOW"), channel)
    assert result.success is True
    assert "below threshold" in result.error_msg
    assert calls["n"] == 0


def test_deliver_alert_sms_disabled_skips_twilio(monkeypatch) -> None:
    monkeypatch.setattr(alert_delivery, "_get_twilio_config", _twilio_cfg)
    calls = {"n": 0}

    def fake_post(*a, **kw):
        calls["n"] += 1
        return _FakeTwilioResponse(201, json_payload={"sid": "x"})

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel("LOW", kind="sms", target="+15551234567", enabled=False)
    result = deliver_alert(_make_alert("CRITICAL"), channel)
    assert result.success is True
    assert "disabled" in result.error_msg
    assert calls["n"] == 0


# ─── format_webhook_payload ───────────────────────────────────────────────


def test_format_webhook_payload_includes_all_alert_fields() -> None:
    """The generic webhook envelope must carry every alert field plus the
    ``event_type`` discriminator so receivers can identify the schema."""
    alert = _make_alert(
        severity="HIGH",
        alert_id="abc-123",
        title="ZIM dropped 8%",
        body="ZIM intraday move triggered the stock rule.",
        ticker="ZIM",
        route_id="SHANGHAI-LA",
        port_locode="USLAX",
        value=2345.67,
        threshold=1000.0,
        change_pct=23.4,
        alert_type="STOCK_MOVE",
    )
    payload = format_webhook_payload(alert)
    expected_keys = {
        "event_type", "alert_id", "created_at", "alert_type", "severity",
        "title", "body", "ticker", "route_id", "port_locode", "value",
        "threshold", "change_pct", "acknowledged",
    }
    assert set(payload.keys()) >= expected_keys
    assert payload["event_type"] == "alert"
    assert payload["alert_id"] == "abc-123"
    assert payload["severity"] == "HIGH"
    assert payload["title"] == "ZIM dropped 8%"
    assert payload["body"] == "ZIM intraday move triggered the stock rule."
    assert payload["ticker"] == "ZIM"
    assert payload["route_id"] == "SHANGHAI-LA"
    assert payload["port_locode"] == "USLAX"
    assert payload["value"] == 2345.67
    assert payload["threshold"] == 1000.0
    assert payload["change_pct"] == 23.4
    assert payload["alert_type"] == "STOCK_MOVE"
    assert payload["acknowledged"] is False


def test_format_webhook_payload_is_json_serializable() -> None:
    """A receiver POSTed this envelope must be able to round-trip it
    through ``json.dumps`` / ``json.loads`` without losing data."""
    import json
    alert = _make_alert(severity="MEDIUM", value=1.23, threshold=4.56, change_pct=-7.89)
    payload = format_webhook_payload(alert)
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)
    assert decoded["event_type"] == "alert"
    assert decoded["severity"] == "MEDIUM"
    assert decoded["value"] == 1.23


# ─── format_discord_payload ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "severity,expected_color",
    [
        ("CRITICAL", 14104137),
        ("HIGH",     16148490),
        ("MEDIUM",   15844367),
        ("LOW",      6976125),
    ],
)
def test_format_discord_payload_severity_color(severity, expected_color) -> None:
    payload = format_discord_payload(_make_alert(severity=severity))
    embed = payload["embeds"][0]
    assert embed["color"] == expected_color


def test_format_discord_payload_includes_value_threshold_change_fields() -> None:
    alert = _make_alert(value=2345.67, threshold=1000.0, change_pct=23.4)
    payload = format_discord_payload(alert)
    embed = payload["embeds"][0]
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert "Value" in fields
    assert "2,345.67" in fields["Value"]
    assert "Threshold" in fields
    assert "1,000.00" in fields["Threshold"]
    assert "Change %" in fields
    assert "23.40" in fields["Change %"]


def test_format_discord_payload_content_and_embed_title_carry_severity() -> None:
    payload = format_discord_payload(_make_alert(severity="CRITICAL", title="BDI crashed"))
    assert "[CRITICAL]" in payload["content"]
    assert "BDI crashed" in payload["content"]
    embed = payload["embeds"][0]
    assert "[CRITICAL]" in embed["title"]
    assert "BDI crashed" in embed["title"]


def test_format_discord_payload_description_is_alert_body() -> None:
    alert = _make_alert(body="Drewry SCFI jumped sharply overnight.")
    payload = format_discord_payload(alert)
    assert payload["embeds"][0]["description"] == "Drewry SCFI jumped sharply overnight."


# ─── format_pagerduty_payload ─────────────────────────────────────────────


def test_format_pagerduty_payload_dedup_key_matches_alert_id() -> None:
    alert = _make_alert(alert_id="alert-uuid-42")
    payload = format_pagerduty_payload(alert, integration_key="key123")
    assert payload["dedup_key"] == "alert-uuid-42"


def test_format_pagerduty_payload_event_action_is_trigger() -> None:
    payload = format_pagerduty_payload(_make_alert(), integration_key="k")
    assert payload["event_action"] == "trigger"


def test_format_pagerduty_payload_routing_key_set_from_arg() -> None:
    payload = format_pagerduty_payload(_make_alert(), integration_key="my-integration-key-xyz")
    assert payload["routing_key"] == "my-integration-key-xyz"


@pytest.mark.parametrize(
    "severity,expected_pd",
    [
        ("CRITICAL", "critical"),
        ("HIGH",     "error"),
        ("MEDIUM",   "warning"),
        ("LOW",      "info"),
    ],
)
def test_format_pagerduty_payload_severity_mapping(severity, expected_pd) -> None:
    payload = format_pagerduty_payload(_make_alert(severity=severity), integration_key="k")
    assert payload["payload"]["severity"] == expected_pd


def test_format_pagerduty_payload_custom_details_include_value_threshold_change() -> None:
    alert = _make_alert(value=2345.67, threshold=1000.0, change_pct=23.4)
    payload = format_pagerduty_payload(alert, integration_key="k")
    custom = payload["payload"]["custom_details"]
    assert custom["value"] == 2345.67
    assert custom["threshold"] == 1000.0
    assert custom["change_pct"] == 23.4


def test_format_pagerduty_payload_source_and_summary() -> None:
    alert = _make_alert(severity="HIGH", title="ZIM dropped 8%")
    payload = format_pagerduty_payload(alert, integration_key="k")
    assert payload["payload"]["source"] == "ship-tracker"
    assert "[HIGH]" in payload["payload"]["summary"]
    assert "ZIM dropped 8%" in payload["payload"]["summary"]


# ─── _deliver_webhook ─────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [200, 201, 202, 204])
def test_deliver_webhook_2xx_success(monkeypatch, status) -> None:
    calls = {}

    def fake_post(url, json=None, timeout=None, **kw):
        calls["url"] = url
        calls["json"] = json
        calls["timeout"] = timeout
        return _FakeResponse(status_code=status)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel(
        "LOW", kind="webhook", target="https://example.com/hooks/alerts"
    )
    result = _deliver_webhook(channel, _make_alert("HIGH"))
    assert result.success is True
    assert result.status_code == status
    assert calls["url"] == "https://example.com/hooks/alerts"
    assert calls["timeout"] == 10.0
    # The body is the generic webhook envelope
    assert calls["json"]["event_type"] == "alert"


@pytest.mark.parametrize("status", [400, 401, 403, 500, 502, 503])
def test_deliver_webhook_non_2xx_failure(monkeypatch, status) -> None:
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(status_code=status, text="boom"),
    )
    channel = _make_channel(
        "LOW", kind="webhook", target="https://example.com/hooks/alerts"
    )
    result = _deliver_webhook(channel, _make_alert("HIGH"))
    assert result.success is False
    assert result.status_code == status
    assert str(status) in result.error_msg


def test_deliver_webhook_connection_error(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(alert_delivery.requests, "post", boom)
    channel = _make_channel("LOW", kind="webhook", target="https://example.com/h")
    result = _deliver_webhook(channel, _make_alert("HIGH"))
    assert result.success is False
    assert result.status_code == 0
    assert "connection error" in result.error_msg
    assert "no route to host" in result.error_msg


def test_deliver_webhook_timeout(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.exceptions.Timeout("read timed out")

    monkeypatch.setattr(alert_delivery.requests, "post", boom)
    channel = _make_channel("LOW", kind="webhook", target="https://example.com/h")
    result = _deliver_webhook(channel, _make_alert("HIGH"))
    assert result.success is False
    assert "timeout" in result.error_msg.lower()


# ─── _deliver_discord ─────────────────────────────────────────────────────


def test_deliver_discord_204_success(monkeypatch) -> None:
    """Discord returns 204 No Content on a successful webhook POST."""
    calls = {}

    def fake_post(url, json=None, timeout=None, **kw):
        calls["url"] = url
        calls["json"] = json
        calls["timeout"] = timeout
        return _FakeResponse(status_code=204, text="")

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    target = "https://discord.com/api/webhooks/123456789/abc-token-xyz"
    channel = _make_channel("LOW", kind="discord", target=target)
    result = _deliver_discord(channel, _make_alert("HIGH"))
    assert result.success is True
    assert result.status_code == 204
    assert calls["url"] == target
    assert calls["timeout"] == 10.0
    # Body is the Discord-shaped payload
    assert "embeds" in calls["json"]
    assert "content" in calls["json"]


def test_deliver_discord_accepts_discordapp_com_url(monkeypatch) -> None:
    """The legacy ``discordapp.com`` host is also a valid Discord webhook URL."""
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(status_code=204, text=""),
    )
    target = "https://discordapp.com/api/webhooks/123456789/abc-token-xyz"
    channel = _make_channel("LOW", kind="discord", target=target)
    result = _deliver_discord(channel, _make_alert("HIGH"))
    assert result.success is True


def test_deliver_discord_rejects_non_discord_target(monkeypatch) -> None:
    """A URL that isn't a Discord webhook must be rejected before the POST."""
    posted = {"n": 0}

    def fake_post(*a, **kw):
        posted["n"] += 1
        return _FakeResponse(status_code=204)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel(
        "LOW", kind="discord", target="https://example.com/not-discord"
    )
    result = _deliver_discord(channel, _make_alert("HIGH"))
    assert result.success is False
    assert "must be a Discord webhook URL" in result.error_msg
    assert posted["n"] == 0  # never POSTed


def test_deliver_discord_connection_error(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.exceptions.ConnectionError("no route to discord")

    monkeypatch.setattr(alert_delivery.requests, "post", boom)
    target = "https://discord.com/api/webhooks/1/t"
    channel = _make_channel("LOW", kind="discord", target=target)
    result = _deliver_discord(channel, _make_alert("HIGH"))
    assert result.success is False
    assert result.status_code == 0
    assert "connection error" in result.error_msg
    assert "no route to discord" in result.error_msg


def test_deliver_discord_400_returns_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(status_code=400, text="bad embed"),
    )
    target = "https://discord.com/api/webhooks/1/t"
    channel = _make_channel("LOW", kind="discord", target=target)
    result = _deliver_discord(channel, _make_alert("HIGH"))
    assert result.success is False
    assert result.status_code == 400
    assert "400" in result.error_msg


# ─── _deliver_pagerduty ───────────────────────────────────────────────────


class _FakePagerDutyResponse:
    """``requests.Response`` stand-in for PagerDuty: status + text +
    ``json()`` returning a pre-baked dict."""

    def __init__(
        self,
        status_code: int = 202,
        text: str = "",
        json_payload: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self._json_payload = json_payload

    def json(self) -> dict:
        if self._json_payload is None:
            raise ValueError("no json body")
        return self._json_payload


def test_deliver_pagerduty_202_success(monkeypatch) -> None:
    """PagerDuty returns 202 Accepted on a successful Events API enqueue."""
    calls = {}

    def fake_post(url, json=None, timeout=None, **kw):
        calls["url"] = url
        calls["json"] = json
        calls["timeout"] = timeout
        return _FakePagerDutyResponse(
            status_code=202,
            json_payload={"status": "success", "dedup_key": "x", "message": "Event processed"},
        )

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel("LOW", kind="pagerduty", target="my-integration-key")
    alert = _make_alert(severity="HIGH", title="ZIM dropped 8%")

    result = _deliver_pagerduty(channel, alert)
    assert result.success is True
    assert result.status_code == 202
    # Fixed events endpoint
    assert calls["url"] == _PAGERDUTY_EVENTS_URL
    assert calls["url"] == "https://events.pagerduty.com/v2/enqueue"
    # Routing key lives in the body
    assert calls["json"]["routing_key"] == "my-integration-key"
    assert calls["json"]["event_action"] == "trigger"
    assert "[HIGH] ZIM dropped 8%" in calls["json"]["payload"]["summary"]
    assert calls["timeout"] == 10.0


def test_deliver_pagerduty_400_surfaces_message(monkeypatch) -> None:
    """A 400 with a PagerDuty error body must surface the ``message`` field."""
    def fake_post(url, json=None, timeout=None, **kw):
        return _FakePagerDutyResponse(
            status_code=400,
            text='{"status":"invalid event","message":"Invalid routing key"}',
            json_payload={"status": "invalid event", "message": "Invalid routing key"},
        )

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel("LOW", kind="pagerduty", target="bad-key")
    result = _deliver_pagerduty(channel, _make_alert("HIGH"))
    assert result.success is False
    assert result.status_code == 400
    assert "Invalid routing key" in result.error_msg


def test_deliver_pagerduty_connection_error(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.exceptions.ConnectionError("no route to pagerduty")

    monkeypatch.setattr(alert_delivery.requests, "post", boom)
    channel = _make_channel("LOW", kind="pagerduty", target="key")
    result = _deliver_pagerduty(channel, _make_alert("HIGH"))
    assert result.success is False
    assert result.status_code == 0
    assert "connection error" in result.error_msg
    assert "no route to pagerduty" in result.error_msg


def test_deliver_pagerduty_timeout(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.exceptions.Timeout("pd read timed out")

    monkeypatch.setattr(alert_delivery.requests, "post", boom)
    channel = _make_channel("LOW", kind="pagerduty", target="key")
    result = _deliver_pagerduty(channel, _make_alert("HIGH"))
    assert result.success is False
    assert "timeout" in result.error_msg.lower()


# ─── deliver_alert dispatch on kind="webhook"/"discord"/"pagerduty" ───────


def test_deliver_alert_kind_webhook_dispatches(monkeypatch) -> None:
    """kind="webhook" must POST to ``target`` with the generic envelope."""
    calls = {}

    def fake_post(url, json=None, timeout=None, **kw):
        calls["url"] = url
        calls["json"] = json
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel(
        "LOW", kind="webhook", target="https://hooks.example.com/alerts"
    )
    result = deliver_alert(_make_alert("HIGH"), channel)
    assert result.success is True
    assert calls["url"] == "https://hooks.example.com/alerts"
    assert calls["json"]["event_type"] == "alert"


def test_deliver_alert_kind_discord_dispatches(monkeypatch) -> None:
    """kind="discord" must POST to the Discord webhook URL with the
    Discord-shaped body."""
    calls = {}

    def fake_post(url, json=None, timeout=None, **kw):
        calls["url"] = url
        calls["json"] = json
        return _FakeResponse(status_code=204, text="")

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    target = "https://discord.com/api/webhooks/123/token"
    channel = _make_channel("LOW", kind="discord", target=target)
    result = deliver_alert(_make_alert("HIGH"), channel)
    assert result.success is True
    assert calls["url"] == target
    assert "embeds" in calls["json"]


def test_deliver_alert_kind_pagerduty_dispatches(monkeypatch) -> None:
    """kind="pagerduty" must POST to the PagerDuty Events endpoint with
    the routing key in the body."""
    calls = {}

    def fake_post(url, json=None, timeout=None, **kw):
        calls["url"] = url
        calls["json"] = json
        return _FakePagerDutyResponse(
            status_code=202,
            json_payload={"status": "success", "dedup_key": "x", "message": "ok"},
        )

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel("LOW", kind="pagerduty", target="my-key-abc")
    result = deliver_alert(_make_alert("HIGH"), channel)
    assert result.success is True
    assert calls["url"] == _PAGERDUTY_EVENTS_URL
    assert calls["json"]["routing_key"] == "my-key-abc"


def test_deliver_alert_kind_webhook_below_threshold_skips(monkeypatch) -> None:
    """Severity gating runs before transport selection for webhook too."""
    posted = {"n": 0}

    def fake_post(*a, **kw):
        posted["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel(
        "CRITICAL", kind="webhook", target="https://example.com/h"
    )
    result = deliver_alert(_make_alert("LOW"), channel)
    assert result.success is True
    assert "below threshold" in result.error_msg
    assert posted["n"] == 0


def test_deliver_alert_kind_discord_disabled_skips(monkeypatch) -> None:
    posted = {"n": 0}

    def fake_post(*a, **kw):
        posted["n"] += 1
        return _FakeResponse(204)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    channel = _make_channel(
        "LOW",
        kind="discord",
        target="https://discord.com/api/webhooks/1/t",
        enabled=False,
    )
    result = deliver_alert(_make_alert("CRITICAL"), channel)
    assert result.success is True
    assert "disabled" in result.error_msg
    assert posted["n"] == 0
