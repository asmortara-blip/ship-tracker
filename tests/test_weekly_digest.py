"""Tests for engine.weekly_digest — automated weekly summary delivery.

Covers
======
- WeeklyDigest dataclass shape + default sentinels
- compute_digest with an empty DB returns the empty-shape summary
- compute_digest seeded alerts aggregate counts / severity / types
- compute_digest week_start auto-defaults to this Monday
- compute_digest with explicit week_start snaps to that week's Monday
- compute_digest aggregates top alert_types / tickers correctly
- compute_digest includes incident count (via the correlator)
- compute_digest includes ack_rate
- render_digest_markdown produces non-empty content with expected headers
- render_digest_email_html produces valid table-based HTML
- render_digest_email_html escapes XSS attempts in payload fields
- dispatch_digest with no enabled channels returns []
- dispatch_digest with one channel returns one result
- dispatch_digest filters SMS / PagerDuty (incompatible kinds)
- get_digest_config defaults to disabled for a new user
- enable_digest persists the config
- disable_digest clears the config
- run_weekly_digest_job NEVER raises (top-level guard)
- run_weekly_digest_job only fires when day_of_week + hour match
- run_weekly_digest_job honors the per-user idempotency lock
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


# ─── Fixture: isolated SQLite per test ─────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite — mirrors the pattern in every other test_*.py
    in this suite (see test_operator_digest, test_ops_cli)."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ───────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _monday_this_week() -> datetime:
    n = _now()
    return (n - timedelta(days=n.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _mk_alert(
    *,
    severity: str = "HIGH",
    alert_type: str = "MACRO",
    ticker: str = "",
    route_id: str = "",
    created_at: datetime | None = None,
    acknowledged: bool = False,
) -> "object":
    """Build a ShippingAlert with a unique id + tagging fields so the
    v14 dedup_key doesn't collapse multiple seeds in the same test."""
    from engine.alert_engine_v2 import ShippingAlert

    aid = str(uuid.uuid4())
    ts = (created_at or _monday_this_week() + timedelta(days=1)).isoformat()
    return ShippingAlert(
        alert_id=aid,
        created_at=ts,
        alert_type=alert_type,
        severity=severity,
        title=f"t-{aid[:6]}",
        body=f"b-{aid[:6]}",
        ticker=ticker or aid[:6],  # always unique to avoid dedup collapse
        route_id=route_id,
        port_locode="",
        value=0.0,
        threshold=0.0,
        change_pct=0.0,
        acknowledged=acknowledged,
    )


def _mk_channel(
    *,
    name: str = "ch-test",
    kind: str = "slack",
    target: str = "https://hooks.slack.com/services/T/B/X",
    enabled: bool = True,
    user_id: str = "",
) -> "object":
    from engine.alert_delivery import DeliveryChannel, save_channel

    ch = DeliveryChannel(
        channel_id=str(uuid.uuid4()),
        name=name,
        kind=kind,
        target=target,
        severity_threshold="LOW",
        enabled=enabled,
    )
    save_channel(ch, user_id=user_id)
    return ch


# ─── Dataclass shape ───────────────────────────────────────────────────────

def test_weekly_digest_default_shape() -> None:
    """Default values give the renderers something safe to read."""
    from engine.weekly_digest import WeeklyDigest

    d = WeeklyDigest(user_id="u1", week_start="2026-05-18", week_end="2026-05-24")
    assert d.user_id == "u1"
    assert d.week_start == "2026-05-18"
    assert d.week_end == "2026-05-24"
    assert d.summary == {}
    assert d.generated_at == ""


# ─── compute_digest — empty DB ─────────────────────────────────────────────

def test_compute_digest_empty_db_returns_empty_summary() -> None:
    """No alerts in the window → empty counts but every key present."""
    from engine.weekly_digest import compute_digest

    d = compute_digest(user_id="u1")
    assert d.summary["alerts_total"] == 0
    assert d.summary["alerts_by_severity"] == {}
    assert d.summary["incidents_total"] == 0
    assert d.summary["top_alert_types"] == []
    assert d.summary["top_routes"] == []
    assert d.summary["top_tickers"] == []
    assert d.summary["ack_rate"] == 0.0
    assert d.summary["budget_suppressed"] == 0
    # generated_at must be populated
    assert d.generated_at != ""


def test_compute_digest_never_raises_on_internal_failure(monkeypatch) -> None:
    """Even if every aggregator dies, the helper returns a valid digest."""
    from engine import weekly_digest

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(weekly_digest, "_load_alerts_in_window", _boom)
    monkeypatch.setattr(weekly_digest, "_aggregate_incidents", _boom)
    monkeypatch.setattr(weekly_digest, "_aggregate_source_health", _boom)
    monkeypatch.setattr(weekly_digest, "_aggregate_channel_usage", _boom)
    monkeypatch.setattr(weekly_digest, "_aggregate_budget_suppressed", _boom)

    # The aggregators called inside compute_digest are wrapped through
    # the underscore helpers — replace those so the helper degrades.
    # (compute_digest itself doesn't wrap in try/except — the inner
    # helpers do. So substituting them to raise should not propagate.)
    # NOTE: _load_alerts_in_window is called directly; the others go
    # through their own wrappers that return sentinels. So the only
    # raise the helper exposes is from _load_alerts_in_window; our
    # implementation catches that internally.
    try:
        weekly_digest.compute_digest(user_id="u1")
    except Exception as exc:
        pytest.fail(f"compute_digest must never raise: {exc}")


# ─── compute_digest — populated DB ─────────────────────────────────────────

def test_compute_digest_aggregates_seeded_alerts() -> None:
    """4 alerts (2 CRITICAL, 2 HIGH, 2 acked) → counts line up."""
    from engine.alert_engine_v2 import save_alerts
    from engine.weekly_digest import compute_digest

    monday = _monday_this_week()
    seeds = [
        _mk_alert(severity="CRITICAL", acknowledged=True, created_at=monday + timedelta(days=1)),
        _mk_alert(severity="CRITICAL", acknowledged=False, created_at=monday + timedelta(days=2)),
        _mk_alert(severity="HIGH", acknowledged=True, created_at=monday + timedelta(days=3)),
        _mk_alert(severity="HIGH", acknowledged=False, created_at=monday + timedelta(days=4)),
    ]
    save_alerts(seeds)

    d = compute_digest(user_id="")
    assert d.summary["alerts_total"] == 4
    assert d.summary["alerts_by_severity"]["CRITICAL"] == 2
    assert d.summary["alerts_by_severity"]["HIGH"] == 2
    assert d.summary["ack_rate"] == pytest.approx(0.5, abs=1e-6)


def test_compute_digest_defaults_to_monday_of_current_week() -> None:
    """``week_start=None`` snaps to the current week's Monday."""
    from engine.weekly_digest import compute_digest

    d = compute_digest(user_id="u1")
    monday_iso = _monday_this_week().date().isoformat()
    sunday_iso = (_monday_this_week() + timedelta(days=6)).date().isoformat()
    assert d.week_start == monday_iso
    assert d.week_end == sunday_iso


def test_compute_digest_explicit_week_start_snaps_to_monday() -> None:
    """An arbitrary mid-week ISO date snaps to that week's Monday."""
    from engine.weekly_digest import compute_digest

    # 2026-05-20 is a Wednesday. Its week's Monday is 2026-05-18.
    d = compute_digest(user_id="u1", week_start="2026-05-20")
    assert d.week_start == "2026-05-18"
    assert d.week_end == "2026-05-24"


def test_compute_digest_aggregates_top_alert_types() -> None:
    """Three BDI_MOVE + one RATE_SURGE → BDI_MOVE first, RATE_SURGE second."""
    from engine.alert_engine_v2 import save_alerts
    from engine.weekly_digest import compute_digest

    monday = _monday_this_week()
    seeds = [
        _mk_alert(alert_type="BDI_MOVE", created_at=monday + timedelta(days=1)),
        _mk_alert(alert_type="BDI_MOVE", created_at=monday + timedelta(days=2)),
        _mk_alert(alert_type="BDI_MOVE", created_at=monday + timedelta(days=3)),
        _mk_alert(alert_type="RATE_SURGE", created_at=monday + timedelta(days=4)),
    ]
    save_alerts(seeds)

    d = compute_digest(user_id="")
    top = d.summary["top_alert_types"]
    assert top[0]["alert_type"] == "BDI_MOVE"
    assert top[0]["count"] == 3
    assert top[1]["alert_type"] == "RATE_SURGE"
    assert top[1]["count"] == 1


def test_compute_digest_aggregates_top_tickers() -> None:
    """Tickers ranked by count desc, ties broken alphabetically.

    Vary severity across the same-ticker pair so the v14 dedup_key
    (alert_type|severity|ticker|route|port) doesn't collapse the second
    seed into a fire_count bump on the first — we want two distinct
    rows in the store for this aggregation.
    """
    from engine.alert_engine_v2 import save_alerts
    from engine.weekly_digest import compute_digest

    monday = _monday_this_week()
    seeds = [
        _mk_alert(severity="HIGH", ticker="MAERSK",
                  created_at=monday + timedelta(days=1)),
        _mk_alert(severity="CRITICAL", ticker="MAERSK",
                  created_at=monday + timedelta(days=2)),
        _mk_alert(severity="HIGH", ticker="ZIM",
                  created_at=monday + timedelta(days=3)),
    ]
    save_alerts(seeds)

    d = compute_digest(user_id="")
    tickers = d.summary["top_tickers"]
    # MAERSK first (count=2), ZIM second (count=1)
    assert tickers[0]["ticker"] == "MAERSK"
    assert tickers[0]["count"] == 2
    assert any(t["ticker"] == "ZIM" for t in tickers)


def test_compute_digest_includes_incident_count() -> None:
    """Alerts grouped via the correlator surface as incidents_total."""
    from engine.alert_engine_v2 import save_alerts
    from engine.weekly_digest import compute_digest

    monday = _monday_this_week()
    # Two clusters: alerts touching the same ticker within 30 min →
    # one incident. Spread the second cluster days later so the
    # correlator's time-bucket keeps them apart.
    seeds = [
        _mk_alert(ticker="A", created_at=monday + timedelta(days=1, minutes=0)),
        _mk_alert(ticker="A", created_at=monday + timedelta(days=1, minutes=5)),
        _mk_alert(ticker="B", created_at=monday + timedelta(days=3)),
    ]
    save_alerts(seeds)

    d = compute_digest(user_id="")
    # At minimum we expect a positive incident count (correlator output
    # depends on the entity-overlap rules — exact count is implementation
    # detail). Pin the lower bound that "the call succeeded and produced
    # at least one incident".
    assert d.summary["incidents_total"] >= 1


def test_compute_digest_includes_ack_rate() -> None:
    """ack_rate = acknowledged / total."""
    from engine.alert_engine_v2 import save_alerts
    from engine.weekly_digest import compute_digest

    monday = _monday_this_week()
    seeds = [
        _mk_alert(acknowledged=True, created_at=monday + timedelta(days=1)),
        _mk_alert(acknowledged=True, created_at=monday + timedelta(days=2)),
        _mk_alert(acknowledged=True, created_at=monday + timedelta(days=3)),
        _mk_alert(acknowledged=False, created_at=monday + timedelta(days=4)),
    ]
    save_alerts(seeds)

    d = compute_digest(user_id="")
    assert d.summary["ack_rate"] == pytest.approx(0.75, abs=1e-6)


# ─── render_digest_markdown ────────────────────────────────────────────────

def test_render_digest_markdown_produces_non_empty() -> None:
    from engine.weekly_digest import compute_digest, render_digest_markdown

    md = render_digest_markdown(compute_digest(user_id="u1"))
    assert isinstance(md, str)
    assert len(md) > 50
    assert "Weekly Digest" in md
    assert "Headline" in md


def test_render_digest_markdown_includes_severity_and_top_rows() -> None:
    """When the summary carries content, the renderer surfaces it."""
    from engine.alert_engine_v2 import save_alerts
    from engine.weekly_digest import compute_digest, render_digest_markdown

    monday = _monday_this_week()
    save_alerts([
        _mk_alert(severity="CRITICAL", alert_type="BDI_MOVE",
                  ticker="MAERSK", created_at=monday + timedelta(days=1)),
        _mk_alert(severity="HIGH", alert_type="BDI_MOVE",
                  ticker="MAERSK", created_at=monday + timedelta(days=2)),
    ])

    md = render_digest_markdown(compute_digest(user_id=""))
    assert "CRITICAL" in md
    assert "HIGH" in md
    assert "BDI_MOVE" in md
    assert "MAERSK" in md


# ─── render_digest_email_html ──────────────────────────────────────────────

def test_render_digest_email_html_table_based() -> None:
    from engine.weekly_digest import compute_digest, render_digest_email_html

    html = render_digest_email_html(compute_digest(user_id="u1"))
    # Email-safe layout: open/close <html><body>, table-based layout,
    # no external CSS.
    assert "<html>" in html and "</html>" in html
    assert "<body" in html and "</body>" in html
    assert "<table" in html
    assert "<style>" not in html
    assert "<script>" not in html
    assert "href=" not in html  # no surprise outbound links


def test_render_digest_email_html_escapes_xss_attempts() -> None:
    """Payload-supplied fields that contain markup must be escaped."""
    from engine.weekly_digest import WeeklyDigest, render_digest_email_html

    d = WeeklyDigest(
        user_id="u1",
        week_start="2026-05-18",
        week_end="2026-05-24",
        summary={
            "alerts_total": 1,
            "alerts_by_severity": {"HIGH": 1},
            "top_alert_types": [{"alert_type": "<script>alert(1)</script>", "count": 1}],
            "top_routes": [],
            "top_tickers": [{"ticker": "<img onerror=x>", "count": 1}],
            "incidents_total": 0,
            "source_health": {"current_outages": ["<b>fred</b>"]},
            "channel_usage": [{
                "channel_id": "c1",
                "name": "<svg/onload=alert>",
                "kind": "slack",
                "budget": 10,
                "usage": 5,
                "pct": 50.0,
                "over_budget": False,
            }],
            "ack_rate": 0.0,
            "budget_suppressed": 0,
        },
        generated_at="2026-05-22T00:00:00+00:00",
    )

    html = render_digest_email_html(d)
    # Raw payload values must not appear as live markup
    assert "<script>alert(1)</script>" not in html
    assert "<img onerror=x>" not in html
    assert "<svg/onload=alert>" not in html
    # The escaped forms should be present somewhere
    assert "&lt;script&gt;" in html
    assert "&lt;svg/onload=alert&gt;" in html


# ─── dispatch_digest ───────────────────────────────────────────────────────

def test_dispatch_digest_no_channels_returns_empty() -> None:
    """No channels persisted → empty result list."""
    from engine.weekly_digest import compute_digest, dispatch_digest

    d = compute_digest(user_id="u1")
    results = dispatch_digest(d)
    assert results == []


def test_dispatch_digest_one_channel_returns_one_result(monkeypatch) -> None:
    """A single enabled compatible channel produces a single result.

    The transport itself is short-circuited via a monkeypatch on
    ``deliver_alert`` so the test doesn't touch the network.
    """
    from engine import alert_delivery
    from engine.weekly_digest import compute_digest, dispatch_digest

    ch = _mk_channel(kind="webhook", target="https://example.com/hook",
                     name="primary")

    def _fake_deliver(alert, channel):
        return alert_delivery.DeliveryResult(success=True, status_code=200)

    monkeypatch.setattr(alert_delivery, "deliver_alert", _fake_deliver)

    results = dispatch_digest(compute_digest(user_id=""))
    assert len(results) == 1
    assert results[0]["success"] is True
    assert results[0]["channel_id"] == ch.channel_id


def test_dispatch_digest_filters_incompatible_kinds(monkeypatch) -> None:
    """SMS / PagerDuty channels are silently dropped from the fan-out."""
    from engine import alert_delivery
    from engine.weekly_digest import compute_digest, dispatch_digest

    _mk_channel(kind="sms", target="+15551234567", name="phone")
    _mk_channel(kind="pagerduty", target="abc123", name="pd")
    ok_ch = _mk_channel(kind="email", target="op@example.com", name="ops")

    def _fake_deliver(alert, channel):
        return alert_delivery.DeliveryResult(success=True, status_code=0)

    monkeypatch.setattr(alert_delivery, "deliver_alert", _fake_deliver)

    results = dispatch_digest(compute_digest(user_id=""))
    # Only the email channel should appear; sms + pagerduty filtered out
    assert len(results) == 1
    assert results[0]["channel_id"] == ok_ch.channel_id
    assert results[0]["kind"] == "email"


def test_dispatch_digest_channel_ids_filter(monkeypatch) -> None:
    """Explicit channel_ids filter overrides the default 'all eligible' fan-out."""
    from engine import alert_delivery
    from engine.weekly_digest import compute_digest, dispatch_digest

    ch1 = _mk_channel(kind="webhook", target="https://x/1", name="primary")
    ch2 = _mk_channel(kind="webhook", target="https://x/2", name="secondary")

    monkeypatch.setattr(
        alert_delivery,
        "deliver_alert",
        lambda a, c: alert_delivery.DeliveryResult(success=True, status_code=200),
    )

    results = dispatch_digest(compute_digest(user_id=""), channel_ids=[ch1.channel_id])
    assert len(results) == 1
    assert results[0]["channel_id"] == ch1.channel_id
    # ch2 must not appear
    assert all(r["channel_id"] != ch2.channel_id for r in results)


# ─── Config persistence ────────────────────────────────────────────────────

def test_get_digest_config_defaults_disabled_for_new_user() -> None:
    from engine.weekly_digest import get_digest_config

    cfg = get_digest_config(user_id="brand-new-user")
    assert cfg["enabled"] is False
    assert cfg["day_of_week"] == "monday"
    assert cfg["hour_utc"] == 14
    assert cfg["channel_ids"] == []


def test_enable_digest_persists() -> None:
    from engine.weekly_digest import enable_digest, get_digest_config

    ok = enable_digest(
        user_id="u1",
        channel_ids=["c1", "c2"],
        day_of_week="wednesday",
        hour_utc=9,
    )
    assert ok is True

    cfg = get_digest_config(user_id="u1")
    assert cfg["enabled"] is True
    assert cfg["day_of_week"] == "wednesday"
    assert cfg["hour_utc"] == 9
    assert cfg["channel_ids"] == ["c1", "c2"]


def test_disable_digest_clears() -> None:
    from engine.weekly_digest import disable_digest, enable_digest, get_digest_config

    enable_digest(user_id="u1", channel_ids=["c1"])
    assert get_digest_config(user_id="u1")["enabled"] is True

    assert disable_digest(user_id="u1") is True
    cfg = get_digest_config(user_id="u1")
    assert cfg["enabled"] is False
    assert cfg["channel_ids"] == []


def test_save_digest_config_normalizes_bad_input() -> None:
    """A malformed day name / negative hour falls back to defaults."""
    from engine.weekly_digest import get_digest_config, save_digest_config

    save_digest_config(
        {
            "enabled": True,
            "day_of_week": "not-a-day",
            "hour_utc": -5,
            "channel_ids": ["c1"],
        },
        user_id="u1",
    )
    cfg = get_digest_config(user_id="u1")
    assert cfg["day_of_week"] == "monday"  # bad day → default
    assert cfg["hour_utc"] == 14            # bad hour → default
    assert cfg["enabled"] is True
    assert cfg["channel_ids"] == ["c1"]


# ─── run_weekly_digest_job ─────────────────────────────────────────────────

def test_run_weekly_digest_job_never_raises(monkeypatch) -> None:
    """Even if every underlying helper raises, the worker entry returns
    a count dict — the worker loop must never lose a tick to this job."""
    from engine import weekly_digest

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(weekly_digest, "_list_users_with_digest", _boom)

    out = weekly_digest.run_weekly_digest_job()
    assert isinstance(out, dict)
    assert out == {"checked": 0, "fired": 0, "skipped": 0, "failed": 0}


def test_run_weekly_digest_job_skips_unconfigured_user() -> None:
    """A user with the default disabled config → skipped, not fired."""
    from engine.weekly_digest import run_weekly_digest_job, save_digest_config

    # save a disabled row
    save_digest_config({"enabled": False, "day_of_week": "monday",
                        "hour_utc": 14, "channel_ids": []}, user_id="u-off")

    out = run_weekly_digest_job(now=_monday_this_week().replace(hour=14))
    assert out["checked"] == 1
    assert out["fired"] == 0
    assert out["skipped"] == 1


def test_run_weekly_digest_job_only_fires_on_matching_day_and_hour(monkeypatch) -> None:
    """Enabled config but wrong hour → skipped. Right hour → fires."""
    from engine import weekly_digest
    from engine.weekly_digest import enable_digest, run_weekly_digest_job

    ch = _mk_channel(kind="webhook", target="https://x/y", name="primary")
    enable_digest(user_id="u1", channel_ids=[ch.channel_id],
                  day_of_week="monday", hour_utc=14)

    # Stub dispatch_digest so we can assert without touching the network.
    monkeypatch.setattr(
        weekly_digest, "dispatch_digest",
        lambda d, channel_ids=None: [{"channel_id": ch.channel_id,
                                       "name": "primary", "kind": "webhook",
                                       "success": True, "status_code": 200,
                                       "error_msg": ""}],
    )

    # Wrong hour (13 instead of 14) → skipped
    out1 = run_weekly_digest_job(now=_monday_this_week().replace(hour=13))
    assert out1["fired"] == 0
    assert out1["skipped"] == 1

    # Right hour → fired
    out2 = run_weekly_digest_job(now=_monday_this_week().replace(hour=14))
    assert out2["fired"] == 1


def test_run_weekly_digest_job_idempotency_lock_blocks_double_fire(monkeypatch) -> None:
    """Second tick inside the same hour must not re-dispatch."""
    from engine import weekly_digest
    from engine.weekly_digest import enable_digest, run_weekly_digest_job

    ch = _mk_channel(kind="webhook", target="https://x/y", name="primary")
    enable_digest(user_id="u1", channel_ids=[ch.channel_id],
                  day_of_week="monday", hour_utc=14)

    monkeypatch.setattr(
        weekly_digest, "dispatch_digest",
        lambda d, channel_ids=None: [{"channel_id": ch.channel_id,
                                       "name": "primary", "kind": "webhook",
                                       "success": True, "status_code": 200,
                                       "error_msg": ""}],
    )

    fire_time = _monday_this_week().replace(hour=14)
    out1 = run_weekly_digest_job(now=fire_time)
    assert out1["fired"] == 1

    # Second call in the same hour → the lock should keep it from
    # firing again. The user count stays at 1 (one configured user)
    # but fired stays at 0 this round.
    out2 = run_weekly_digest_job(now=fire_time + timedelta(minutes=10))
    assert out2["fired"] == 0
    assert out2["skipped"] == 1
