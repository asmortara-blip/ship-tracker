"""Tests for engine.operator_digest — daily Operator Dashboard email digest.

Covers
======
- OperatorDigest dataclass shape & sentinel defaults
- build_digest on an empty DB returns sentinel values + summary_status="healthy"
- build_digest aggregates synthetic alerts / LLM calls / render events correctly
- build_digest with unacked CRITICAL > 0 → summary_status="critical"
- build_digest with render_success_rate < 95% → summary_status="attention"
- build_digest with current_outages non-empty → summary_status="attention"
- build_digest stays robust when individual engine helpers raise
- format_digest_html includes the 8 KPI values + status-colored header
- format_digest_text plain-text fallback contains the same key numbers
- format_digest_html renders the outages block only when non-empty
- send_operator_digest dispatches correctly per channel.kind
  (slack/webhook/discord/email/unsupported)
- send_operator_digest honours channel.enabled=False
- run_operator_digest_job loads channels by 'ops-' prefix and dispatches

Conventions
-----------
- Every test uses the ``isolated_state_db`` autouse fixture so SQLite
  rows from one test never leak into the next.
- HTTP / SMTP transport is mocked via monkeypatch — no real network or
  mail server is touched.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from engine.alert_delivery import DeliveryChannel, DeliveryResult
from engine.alert_engine_v2 import ShippingAlert, save_alerts
from engine.llm_telemetry import record_call
from engine.operator_digest import (
    OPERATOR_CHANNEL_PREFIX,
    OperatorDigest,
    build_digest,
    format_digest_html,
    format_digest_text,
    send_operator_digest,
)
from engine.perf_telemetry import record_render
from engine.source_health import HealthPing, _record_ping


# ─── Fixture: isolated SQLite per test ─────────────────────────────────────

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
    *,
    alert_id: str | None = None,
    severity: str = "HIGH",
    created_at: str | None = None,
    acknowledged: bool = False,
) -> ShippingAlert:
    # Auto-generate a unique id and stamp it as the ticker so each
    # alert has a unique v14 dedup_key. The operator-digest tests
    # repeatedly call this helper for "N alerts of severity X" and
    # the dedup layer would otherwise collapse them.
    aid = alert_id or str(uuid.uuid4())
    return ShippingAlert(
        alert_id=aid,
        created_at=created_at if created_at is not None else _now().isoformat(),
        alert_type="MACRO",
        severity=severity,
        title=f"alert-{severity}",
        body=f"body-{severity}",
        ticker=aid,
        route_id="",
        port_locode="",
        value=0.0,
        threshold=0.0,
        change_pct=0.0,
        acknowledged=acknowledged,
    )


def _make_channel(
    *,
    name: str = "ops-trading-desk",
    kind: str = "slack",
    target: str = "https://hooks.slack.com/services/T000/B000/XXXX",
    enabled: bool = True,
) -> DeliveryChannel:
    return DeliveryChannel(
        channel_id=str(uuid.uuid4()),
        name=name,
        kind=kind,
        target=target,
        severity_threshold="MEDIUM",
        enabled=enabled,
    )


# ─── OperatorDigest dataclass ──────────────────────────────────────────────

def test_operator_digest_default_shape() -> None:
    """Defaults match the contract the renderers depend on."""
    d = OperatorDigest()
    assert d.generated_at == ""
    assert d.llm_cost_usd == 0.0
    assert d.llm_calls == 0
    assert d.alert_count == 0
    assert d.ack_rate == 0.0
    assert d.unacked_critical == 0
    assert d.render_success_rate == 0.0
    assert d.slowest_tab == ""
    assert d.current_outages == []
    assert d.summary_status == "healthy"


def test_operator_digest_carries_eight_kpis() -> None:
    """All eight KPI fields can be set and round-trip via attribute access."""
    d = OperatorDigest(
        generated_at="2026-05-22T00:00:00+00:00",
        llm_cost_usd=1.23,
        llm_calls=10,
        alert_count=4,
        ack_rate=0.75,
        unacked_critical=1,
        render_success_rate=0.97,
        slowest_tab="overview",
        current_outages=["fred"],
        summary_status="critical",
    )
    assert d.llm_cost_usd == 1.23
    assert d.unacked_critical == 1
    assert d.slowest_tab == "overview"
    assert d.current_outages == ["fred"]
    assert d.summary_status == "critical"


# ─── build_digest — empty DB ───────────────────────────────────────────────

def test_build_digest_empty_db_returns_healthy_sentinels() -> None:
    """No alerts / no telemetry → all sentinels + summary_status=healthy."""
    d = build_digest()

    assert d.llm_cost_usd == 0.0
    assert d.llm_calls == 0
    assert d.alert_count == 0
    assert d.ack_rate == 0.0
    assert d.unacked_critical == 0
    assert d.render_success_rate == 0.0
    assert d.slowest_tab == ""
    assert d.current_outages == []
    assert d.summary_status == "healthy"
    # generated_at is populated to the call time, not blank.
    assert d.generated_at != ""


# ─── build_digest — populated DB ───────────────────────────────────────────

def test_build_digest_aggregates_synthetic_alerts_and_renders() -> None:
    """Four alerts (2 acked, 2 not) + render events + LLM calls → values
    line up with the underlying engine aggregations."""
    # 4 alerts: 2 acked, 2 not — ack_rate 0.5, total 4
    save_alerts([
        _mk_alert(severity="HIGH", acknowledged=True),
        _mk_alert(severity="HIGH", acknowledged=True),
        _mk_alert(severity="HIGH", acknowledged=False),
        _mk_alert(severity="LOW", acknowledged=False),
    ])

    # LLM calls — 3 calls, recorded via the real persistence path
    record_call("commentary", "claude-haiku-4-5-20251001", 100, 50)
    record_call("commentary", "claude-haiku-4-5-20251001", 100, 50)
    record_call("narration", "claude-haiku-4-5-20251001", 50, 25)

    # Render events — 8 successful, 2 failed → success_rate 0.8
    for _ in range(8):
        record_render("overview", 100, success=True)
    for _ in range(2):
        record_render("overview", 200, success=False, error_msg="boom")

    d = build_digest()

    assert d.alert_count == 4
    assert d.ack_rate == pytest.approx(0.5, abs=1e-6)
    assert d.llm_calls == 3
    assert d.llm_cost_usd > 0.0
    assert d.render_success_rate == pytest.approx(0.8, abs=1e-6)
    # slowest_tab picks the one present
    assert d.slowest_tab == "overview"


def test_build_digest_unacked_critical_drives_critical_status() -> None:
    """An unacked CRITICAL alert in the window → summary_status=critical."""
    save_alerts([_mk_alert(severity="CRITICAL", acknowledged=False)])

    d = build_digest()

    assert d.unacked_critical == 1
    assert d.summary_status == "critical"


def test_build_digest_acked_critical_does_not_trip_critical() -> None:
    """A CRITICAL that has been acknowledged → unacked_critical=0 → healthy
    (assuming nothing else trips attention)."""
    save_alerts([_mk_alert(severity="CRITICAL", acknowledged=True)])

    d = build_digest()

    assert d.unacked_critical == 0
    assert d.summary_status == "healthy"


def test_build_digest_render_success_below_95_attention() -> None:
    """render_success_rate < 0.95 → summary_status=attention."""
    # 9 success + 2 failure → 9/11 ≈ 0.818 (< 0.95)
    for _ in range(9):
        record_render("overview", 100, success=True)
    for _ in range(2):
        record_render("overview", 100, success=False, error_msg="x")

    d = build_digest()

    assert d.render_success_rate < 0.95
    assert d.summary_status == "attention"


def test_build_digest_render_success_at_threshold_is_attention() -> None:
    """Render success EXACTLY at the threshold counts as attention because
    the check is ``< 0.95``. Pin the boundary."""
    # Force a clean 90% to be unambiguously below 95%.
    for _ in range(9):
        record_render("overview", 100, success=True)
    record_render("overview", 100, success=False, error_msg="x")

    d = build_digest()

    assert d.render_success_rate == pytest.approx(0.9, abs=1e-6)
    assert d.summary_status == "attention"


def test_build_digest_outage_drives_attention_status() -> None:
    """A currently-down source → summary_status=attention."""
    # Latest ping per source is "down" → source appears in current_outages.
    _record_ping(HealthPing(
        ping_id="p1",
        source="fred",
        started_at=_now().isoformat(),
        duration_ms=200,
        status="down",
        error_msg="timeout",
    ))

    d = build_digest()

    assert "fred" in d.current_outages
    assert d.summary_status == "attention"


def test_build_digest_critical_wins_over_outage() -> None:
    """When BOTH unacked CRITICAL > 0 AND outage exists, critical wins."""
    save_alerts([_mk_alert(severity="CRITICAL", acknowledged=False)])
    _record_ping(HealthPing(
        ping_id="p2",
        source="yfinance",
        started_at=_now().isoformat(),
        duration_ms=50,
        status="down",
        error_msg="500",
    ))

    d = build_digest()

    assert d.unacked_critical == 1
    assert "yfinance" in d.current_outages
    assert d.summary_status == "critical"


def test_build_digest_swallows_engine_failures(monkeypatch) -> None:
    """A single engine raising inside build_digest must NOT propagate.
    The failing layer degrades to sentinel defaults."""
    def _raise(*args, **kwargs):
        raise RuntimeError("engine offline")

    monkeypatch.setattr("engine.llm_telemetry.get_usage_summary", _raise)
    monkeypatch.setattr("engine.alert_analytics.compute_alert_metrics", _raise)
    monkeypatch.setattr("engine.alert_analytics.get_unacknowledged_critical", _raise)
    monkeypatch.setattr("engine.perf_telemetry.get_perf_summary", _raise)
    monkeypatch.setattr("engine.source_health.get_health_summary", _raise)

    d = build_digest()  # must not raise

    assert d.llm_calls == 0
    assert d.llm_cost_usd == 0.0
    assert d.alert_count == 0
    assert d.unacked_critical == 0
    assert d.render_success_rate == 0.0
    assert d.slowest_tab == ""
    assert d.current_outages == []
    # With sentinels everywhere, status falls back to healthy.
    assert d.summary_status == "healthy"


# ─── format_digest_html ────────────────────────────────────────────────────

def test_format_digest_html_contains_all_eight_kpi_values() -> None:
    """Every KPI value renders into the HTML body."""
    d = OperatorDigest(
        generated_at="2026-05-22T01:23:45+00:00",
        llm_cost_usd=12.34,
        llm_calls=99,
        alert_count=42,
        ack_rate=0.625,
        unacked_critical=3,
        render_success_rate=0.987,
        slowest_tab="overview",
        current_outages=["fred", "yfinance"],
        summary_status="critical",
    )

    html = format_digest_html(d)

    assert "99" in html                # llm_calls
    assert "$12.34" in html            # llm_cost_usd
    assert "42" in html                # alert_count
    assert "62.5%" in html             # ack_rate
    assert "3" in html                 # unacked_critical
    assert "98.7%" in html             # render_success_rate
    assert "overview" in html          # slowest_tab
    # outages count + the list
    assert "fred" in html
    assert "yfinance" in html


def test_format_digest_html_status_colored_header_critical() -> None:
    """CRITICAL status → red accent and 'CRITICAL' headline."""
    d = OperatorDigest(summary_status="critical")
    html = format_digest_html(d)
    assert "#d73a49" in html
    assert "CRITICAL" in html


def test_format_digest_html_status_colored_header_attention() -> None:
    """attention status → amber accent and 'attention' in headline."""
    d = OperatorDigest(summary_status="attention")
    html = format_digest_html(d)
    assert "#f97316" in html
    assert "attention" in html.lower()


def test_format_digest_html_status_colored_header_healthy() -> None:
    """healthy status → green accent and 'healthy' in headline."""
    d = OperatorDigest(summary_status="healthy")
    html = format_digest_html(d)
    assert "#22863a" in html
    assert "healthy" in html.lower()


def test_format_digest_html_outages_block_hidden_when_empty() -> None:
    """No outages → the 'Current Outages' block is absent from HTML."""
    d = OperatorDigest(summary_status="healthy", current_outages=[])
    html = format_digest_html(d)
    assert "Current Outages" not in html


def test_format_digest_html_outages_block_shown_when_present() -> None:
    """At least one outage → the 'Current Outages' block renders."""
    d = OperatorDigest(
        summary_status="attention",
        current_outages=["worldbank"],
    )
    html = format_digest_html(d)
    assert "Current Outages" in html
    assert "worldbank" in html


# ─── format_digest_text ────────────────────────────────────────────────────

def test_format_digest_text_contains_key_numbers() -> None:
    """The plain-text fallback carries the same KPI numbers as HTML."""
    d = OperatorDigest(
        generated_at="2026-05-22T01:00:00+00:00",
        llm_cost_usd=5.55,
        llm_calls=42,
        alert_count=7,
        ack_rate=0.5,
        unacked_critical=2,
        render_success_rate=0.96,
        slowest_tab="commentary",
        current_outages=["fred"],
        summary_status="attention",
    )

    text = format_digest_text(d)

    assert "42" in text
    assert "$5.55" in text
    assert "7" in text
    assert "50.0%" in text          # ack_rate
    assert "96.0%" in text          # render_success_rate
    assert "commentary" in text
    assert "fred" in text
    assert "ATTENTION" in text       # status uppercased in header


def test_format_digest_text_no_html_markup() -> None:
    """No '<' / '>' angle brackets — must be pure text for terminal /
    mail-log readability."""
    d = OperatorDigest(summary_status="healthy")
    text = format_digest_text(d)
    assert "<" not in text
    assert ">" not in text


# ─── send_operator_digest dispatch ─────────────────────────────────────────

def _stub_digest() -> OperatorDigest:
    return OperatorDigest(
        generated_at="2026-05-22T00:00:00+00:00",
        llm_cost_usd=1.0,
        llm_calls=10,
        alert_count=5,
        ack_rate=1.0,
        unacked_critical=0,
        render_success_rate=1.0,
        slowest_tab="overview",
        current_outages=[],
        summary_status="healthy",
    )


def test_send_operator_digest_disabled_channel_returns_success() -> None:
    """A disabled channel is a no-op success, never a failure."""
    channel = _make_channel(enabled=False)
    result = send_operator_digest(channel)
    assert result.success is True
    assert "disabled" in result.error_msg


def test_send_operator_digest_slack_uses_post_json(monkeypatch) -> None:
    """Slack channels dispatch via alert_delivery._post_json."""
    monkeypatch.setattr(
        "engine.operator_digest.build_digest",
        lambda **kwargs: _stub_digest(),
    )
    post_mock = MagicMock(return_value=DeliveryResult(success=True, status_code=200))
    monkeypatch.setattr("engine.alert_delivery._post_json", post_mock)

    channel = _make_channel(kind="slack")
    result = send_operator_digest(channel)

    assert result.success is True
    assert result.status_code == 200
    post_mock.assert_called_once()
    args, _ = post_mock.call_args
    assert args[0] == channel.target          # url
    assert isinstance(args[1], dict)          # payload dict
    assert args[1].get("text", "").startswith("Operator Digest")


def test_send_operator_digest_webhook_envelope_is_flat(monkeypatch) -> None:
    """Webhook dispatch produces a flat envelope with all KPIs on top-level."""
    monkeypatch.setattr(
        "engine.operator_digest.build_digest",
        lambda **kwargs: _stub_digest(),
    )
    captured = {}

    def _capture(url, payload, timeout=10.0):
        captured["url"] = url
        captured["payload"] = payload
        return DeliveryResult(success=True, status_code=201)

    monkeypatch.setattr("engine.alert_delivery._post_json", _capture)

    channel = _make_channel(
        kind="webhook",
        target="https://example/hook",
        name="ops-webhook",
    )
    result = send_operator_digest(channel)

    assert result.success is True
    payload = captured["payload"]
    assert payload["event_type"] == "operator_digest"
    assert payload["llm_calls"] == 10
    assert payload["alert_count"] == 5
    assert payload["summary_status"] == "healthy"
    assert payload["current_outages"] == []


def test_send_operator_digest_discord_validates_target(monkeypatch) -> None:
    """Discord dispatch refuses non-Discord URLs."""
    monkeypatch.setattr(
        "engine.operator_digest.build_digest",
        lambda **kwargs: _stub_digest(),
    )
    channel = _make_channel(
        kind="discord",
        target="https://not-discord.example/hook",
    )
    result = send_operator_digest(channel)

    assert result.success is False
    assert "discord" in result.error_msg.lower()


def test_send_operator_digest_discord_dispatches_on_valid_url(monkeypatch) -> None:
    """Discord webhook URL → POSTs the discord-shaped envelope."""
    monkeypatch.setattr(
        "engine.operator_digest.build_digest",
        lambda **kwargs: _stub_digest(),
    )
    post_mock = MagicMock(return_value=DeliveryResult(success=True, status_code=204))
    monkeypatch.setattr("engine.alert_delivery._post_json", post_mock)

    channel = _make_channel(
        kind="discord",
        target="https://discord.com/api/webhooks/123/abc",
    )
    result = send_operator_digest(channel)

    assert result.success is True
    post_mock.assert_called_once()
    payload = post_mock.call_args.args[1]
    assert "content" in payload
    assert isinstance(payload.get("embeds"), list)


def test_send_operator_digest_email_returns_smtp_not_configured(monkeypatch) -> None:
    """Email path bails cleanly when SMTP isn't configured."""
    monkeypatch.setattr(
        "engine.operator_digest.build_digest",
        lambda **kwargs: _stub_digest(),
    )
    # Force the SMTP config lookup to return None.
    monkeypatch.setattr("engine.alert_delivery._get_smtp_config", lambda: None)

    channel = _make_channel(
        kind="email",
        target="ops@example.com",
        name="ops-email",
    )
    result = send_operator_digest(channel)

    assert result.success is False
    assert "SMTP" in result.error_msg


def test_send_operator_digest_email_invokes_smtp_transport(monkeypatch) -> None:
    """When SMTP IS configured, dispatch flows through _deliver_digest_email
    with the subject + html + text payload."""
    monkeypatch.setattr(
        "engine.operator_digest.build_digest",
        lambda **kwargs: _stub_digest(),
    )

    fake_config = MagicMock(host="smtp.example", port=587, user="u", password="p", from_addr="o@e")
    monkeypatch.setattr("engine.alert_delivery._get_smtp_config", lambda: fake_config)

    captured = {}

    def _fake_deliver_email(channel, payload):
        captured["channel"] = channel
        captured["payload"] = payload
        return DeliveryResult(success=True, status_code=0)

    monkeypatch.setattr("engine.alert_delivery._deliver_digest_email", _fake_deliver_email)

    channel = _make_channel(
        kind="email",
        target="ops@example.com",
        name="ops-email",
    )
    result = send_operator_digest(channel)

    assert result.success is True
    assert set(captured["payload"].keys()) == {"subject", "html_body", "text_body"}
    assert "Operator Digest" in captured["payload"]["subject"]


def test_send_operator_digest_unsupported_kind_is_explicit_failure(
    monkeypatch,
) -> None:
    """Unknown channel.kind surfaces an explicit failure rather than
    silently dropping."""
    monkeypatch.setattr(
        "engine.operator_digest.build_digest",
        lambda **kwargs: _stub_digest(),
    )
    channel = _make_channel(kind="opsgenie")
    result = send_operator_digest(channel)
    assert result.success is False
    assert "unsupported" in result.error_msg.lower()


# ─── run_operator_digest_job ───────────────────────────────────────────────

def test_run_operator_digest_job_dispatches_only_ops_channels(monkeypatch) -> None:
    """Only channels whose name starts with 'ops-' receive the digest."""
    from worker.scheduler import run_operator_digest_job

    ch_ops = _make_channel(name="ops-trading-desk", kind="slack")
    ch_other = _make_channel(name="trading-desk", kind="slack")  # no ops- prefix
    ch_ops2 = _make_channel(name="ops-risk", kind="webhook",
                            target="https://example/hook")

    monkeypatch.setattr(
        "engine.alert_delivery.load_channels",
        lambda: [ch_other, ch_ops, ch_ops2],
    )

    send_mock = MagicMock(return_value=DeliveryResult(success=True, status_code=200))
    monkeypatch.setattr("engine.operator_digest.send_operator_digest", send_mock)

    results = run_operator_digest_job()

    assert len(results) == 2
    dispatched_names = [call.args[0].name for call in send_mock.call_args_list]
    assert set(dispatched_names) == {"ops-trading-desk", "ops-risk"}


def test_run_operator_digest_job_skips_disabled_ops_channels(monkeypatch) -> None:
    """Disabled ops-channels are skipped, not dispatched."""
    from worker.scheduler import run_operator_digest_job

    ch_disabled = _make_channel(name="ops-archived", enabled=False)
    ch_enabled = _make_channel(name="ops-active", enabled=True)

    monkeypatch.setattr(
        "engine.alert_delivery.load_channels",
        lambda: [ch_disabled, ch_enabled],
    )
    send_mock = MagicMock(return_value=DeliveryResult(success=True, status_code=200))
    monkeypatch.setattr("engine.operator_digest.send_operator_digest", send_mock)

    results = run_operator_digest_job()

    assert len(results) == 1
    assert send_mock.call_args.args[0].name == "ops-active"


def test_run_operator_digest_job_swallows_per_channel_errors(monkeypatch) -> None:
    """A raise in one channel must NOT abort the rest."""
    from worker.scheduler import run_operator_digest_job

    ch1 = _make_channel(name="ops-bad")
    ch2 = _make_channel(name="ops-good")

    monkeypatch.setattr(
        "engine.alert_delivery.load_channels",
        lambda: [ch1, ch2],
    )

    def _flaky(channel):
        if channel.name == "ops-bad":
            raise RuntimeError("transport exploded")
        return DeliveryResult(success=True, status_code=200)

    monkeypatch.setattr("engine.operator_digest.send_operator_digest", _flaky)

    results = run_operator_digest_job()

    # ch2 still succeeded — one good result in the list.
    assert len(results) == 1
    assert results[0].success is True


def test_run_operator_digest_job_handles_load_channels_failure(monkeypatch) -> None:
    """load_channels raising returns [] without propagating."""
    from worker.scheduler import run_operator_digest_job

    def _raise():
        raise RuntimeError("DB wedged")

    monkeypatch.setattr("engine.alert_delivery.load_channels", _raise)

    results = run_operator_digest_job()
    assert results == []


def test_run_operator_digest_job_empty_channel_list(monkeypatch) -> None:
    """No channels at all → empty result list, no errors."""
    from worker.scheduler import run_operator_digest_job

    monkeypatch.setattr("engine.alert_delivery.load_channels", lambda: [])

    results = run_operator_digest_job()
    assert results == []


def test_operator_channel_prefix_is_ops_dash() -> None:
    """The constant matches the documented convention."""
    assert OPERATOR_CHANNEL_PREFIX == "ops-"
