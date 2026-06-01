"""Tests for worker.webhook_listener — the stdlib HTTP ack endpoint.

These tests spin up a real ``HTTPServer`` on ``localhost:0`` (kernel-
assigned ephemeral port) in a daemon thread per test, fire ``requests``
at it from the test process, and tear it down in fixture teardown. We
deliberately avoid mocking out the HTTP layer because the value the
listener provides is in the network behaviour (status codes, header
parsing, HMAC verification) — mocking the wire would let regressions
slip in.

Each test isolates the SQLite state DB to a per-test ``tmp_path`` so
calls to ``engine.alert_engine_v2.acknowledge_alert`` don't touch the
real ``cache/ship_tracker.db``. The webhook calls into the alert
engine directly; the alert engine writes via ``state.db.get_connection``;
``state.db.DB_PATH`` is the single seam where we redirect everything.
"""
from __future__ import annotations

import hashlib
import hmac
import inspect
import io
import json
import socket
import threading
from http.server import HTTPServer
from typing import Optional

import pytest
import requests

from engine import alert_engine_v2 as engv2
from worker import webhook_listener


# ─── HMAC test secret (kept long enough to look real but not a real key) ───

_SECRET = "test-secret-do-not-use-in-prod" * 2


def _sign(body: bytes, secret: str = _SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite isolation. Mirrors the pattern in
    test_alert_engine_v2_rules.py — any ``state.db.get_connection``
    call lands in this tmp file, so ``acknowledge_alert`` writes do
    not leak across tests or touch the real cache DB."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


@pytest.fixture(autouse=True)
def set_webhook_secret(monkeypatch):
    """Every test runs against the same shared HMAC secret. We set the
    env var rather than monkeypatching ``_get_secret`` directly so the
    full ``os.environ`` → ``_get_secret`` → handler path is exercised."""
    monkeypatch.setenv("WEBHOOK_SECRET", _SECRET)


def _find_free_port() -> int:
    """Bind to port 0 and read the assigned port. We could pass
    ``HTTPServer(('', 0), …)`` directly but spinning up a throwaway
    socket lets us compute the URL BEFORE starting the server, which
    keeps the fixture API simple."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    """Spin up the dispatching handler on localhost:<ephemeral-port>
    in a daemon thread. Tear down cleanly in teardown — ``shutdown()``
    on the server unblocks ``serve_forever`` and the thread exits."""
    port = _find_free_port()
    httpd = HTTPServer(("127.0.0.1", port), webhook_listener._DispatchHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        yield base_url
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2.0)


# ─── _verify_hmac unit tests ───────────────────────────────────────────────


def test_verify_hmac_correct_signature_returns_true() -> None:
    body = b'{"hello": "world"}'
    sig = _sign(body)
    assert webhook_listener._verify_hmac(body, sig, _SECRET) is True


def test_verify_hmac_wrong_signature_returns_false() -> None:
    body = b'{"hello": "world"}'
    bad_sig = _sign(b'{"hello": "tampered"}')
    assert webhook_listener._verify_hmac(body, bad_sig, _SECRET) is False


def test_verify_hmac_accepts_sha256_prefix() -> None:
    """GitHub / Stripe-style ``sha256=<hex>`` prefix should be accepted
    transparently so the listener works with both header conventions."""
    body = b'{"x": 1}'
    sig = "sha256=" + _sign(body)
    assert webhook_listener._verify_hmac(body, sig, _SECRET) is True


def test_verify_hmac_empty_signature_returns_false() -> None:
    assert webhook_listener._verify_hmac(b"x", "", _SECRET) is False


def test_verify_hmac_empty_secret_returns_false() -> None:
    body = b"x"
    # An empty secret should fail closed even when the caller passes a
    # "valid" empty-key digest. This guards the deployment-misconfig
    # path where WEBHOOK_SECRET wasn't set.
    assert webhook_listener._verify_hmac(body, _sign(body, ""), "") is False


def test_verify_hmac_uses_constant_time_compare() -> None:
    """Source inspection — ``hmac.compare_digest`` is the only valid
    primitive here. Anything that resolves to ``==`` would leak timing
    info on a partial-match attack. This guards against a well-meaning
    refactor that swaps the primitive."""
    src = inspect.getsource(webhook_listener._verify_hmac)
    assert "hmac.compare_digest" in src


# ─── POST /ack/{alert_id} ──────────────────────────────────────────────────


def test_ack_one_with_correct_hmac_calls_acknowledge_alert(server, monkeypatch) -> None:
    calls = []

    def fake_ack(alert_id: str, **kwargs) -> None:
        calls.append(alert_id)

    monkeypatch.setattr(engv2, "acknowledge_alert", fake_ack)

    body = b""  # empty body is fine — alert_id is in the path
    sig = _sign(body)
    r = requests.post(
        f"{server}/ack/alert-123",
        data=body,
        headers={"X-Signature-SHA256": sig},
        timeout=5,
    )

    assert r.status_code == 200
    assert r.json() == {"acknowledged": True, "alert_id": "alert-123"}
    assert calls == ["alert-123"]


def test_ack_one_with_wrong_hmac_returns_401_and_does_not_ack(
    server, monkeypatch
) -> None:
    calls = []
    monkeypatch.setattr(engv2, "acknowledge_alert",
                        lambda aid, **kwargs: calls.append(aid))

    body = b""
    bad_sig = "deadbeef" * 8  # 64-char hex but wrong digest
    r = requests.post(
        f"{server}/ack/alert-123",
        data=body,
        headers={"X-Signature-SHA256": bad_sig},
        timeout=5,
    )

    assert r.status_code == 401
    assert calls == []


def test_ack_unknown_alert_id_still_returns_200(server) -> None:
    """``acknowledge_alert`` is a no-op for unknown IDs (the UPDATE
    affects zero rows). We verify it doesn't raise + the listener
    still answers 200 — important for idempotent replay from PagerDuty
    or curl scripts."""
    body = b""
    sig = _sign(body)
    r = requests.post(
        f"{server}/ack/totally-unknown-id-{'x' * 20}",
        data=body,
        headers={"X-Signature-SHA256": sig},
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json()["acknowledged"] is True


def test_ack_one_empty_id_returns_404(server) -> None:
    """``/ack/`` (no id) must not match the ack route."""
    body = b""
    sig = _sign(body)
    r = requests.post(
        f"{server}/ack/",
        data=body,
        headers={"X-Signature-SHA256": sig},
        timeout=5,
    )
    # The trailing slash gets stripped before routing → ``/ack`` →
    # which is not in the route table → 404.
    assert r.status_code == 404


# ─── POST /ack-all ─────────────────────────────────────────────────────────


def test_ack_all_with_correct_hmac_calls_acknowledge_all(server, monkeypatch) -> None:
    # /ack-all is now scoped to a resolved owner. Configure one so the call
    # resolves to a real user and acks ONLY that user's alerts.
    monkeypatch.setenv("WEBHOOK_INBOUND_USER_ID", "ops-user")
    calls = []
    monkeypatch.setattr(engv2, "acknowledge_all",
                        lambda **kwargs: calls.append(kwargs.get("user_id")))

    body = b""
    sig = _sign(body)
    r = requests.post(
        f"{server}/ack-all",
        data=body,
        headers={"X-Signature-SHA256": sig},
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json() == {"acknowledged": True, "scope": "user"}
    assert calls == ["ops-user"]   # scoped to the resolved owner, not global


def test_ack_passes_resolved_user_id_not_global(server, monkeypatch) -> None:
    """Regression for the cross-tenant ack bug: /ack must pass the RESOLVED
    owning user_id to acknowledge_alert (so the engine scopes the UPDATE to
    that user), instead of calling it bare — which disabled scoping out-of-
    process and let one shared-secret holder ack ANY user's alert."""
    monkeypatch.setenv("WEBHOOK_INBOUND_USER_ID", "ops-user")
    seen = []
    monkeypatch.setattr(
        engv2, "acknowledge_alert",
        lambda aid, **kw: seen.append((aid, kw.get("user_id"))),
    )
    body = b""
    sig = _sign(body)
    r = requests.post(
        f"{server}/ack/alert-xyz", data=body,
        headers={"X-Signature-SHA256": sig}, timeout=5,
    )
    assert r.status_code == 200
    assert seen == [("alert-xyz", "ops-user")]  # scoped, not global (None)


def test_ack_all_refuses_when_no_resolvable_user(server, monkeypatch) -> None:
    """/ack-all must NOT fall through to a global sweep when no owner can be
    resolved — it refuses (400) rather than acknowledging every user's open
    alerts."""
    monkeypatch.delenv("WEBHOOK_INBOUND_USER_ID", raising=False)
    # Force the no-owner case deterministically (no env, no admin user).
    monkeypatch.setattr(webhook_listener, "_resolve_hmac_user_id", lambda: "")
    called = []
    monkeypatch.setattr(engv2, "acknowledge_all",
                        lambda **kw: called.append(1))
    body = b""
    sig = _sign(body)
    r = requests.post(
        f"{server}/ack-all", data=body,
        headers={"X-Signature-SHA256": sig}, timeout=5,
    )
    assert r.status_code == 400
    assert called == []  # never swept globally


def test_ack_all_with_wrong_hmac_returns_401(server, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(engv2, "acknowledge_all", lambda **kwargs: calls.append(1))

    r = requests.post(
        f"{server}/ack-all",
        data=b"",
        headers={"X-Signature-SHA256": "0" * 64},
        timeout=5,
    )
    assert r.status_code == 401
    assert calls == []


# ─── POST /webhooks/pagerduty ─────────────────────────────────────────────


def test_pagerduty_incident_resolved_acks_dedup_key(server, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(engv2, "acknowledge_alert",
                        lambda aid, **kwargs: calls.append(aid))

    payload = {
        "event": {
            "event_type": "incident.resolved",
            "data": {"incident": {"dedup_key": "alert-abc-789"}},
        }
    }
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)
    r = requests.post(
        f"{server}/webhooks/pagerduty",
        data=body,
        headers={
            "X-PagerDuty-Signature": sig,
            "Content-Type": "application/json",
        },
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json()["acknowledged"] is True
    assert r.json()["alert_id"] == "alert-abc-789"
    assert calls == ["alert-abc-789"]


def test_pagerduty_incident_triggered_does_not_ack(server, monkeypatch) -> None:
    """``incident.triggered`` is informational — PagerDuty just told us
    it accepted our outgoing event. We must NOT ack on this path."""
    calls = []
    monkeypatch.setattr(engv2, "acknowledge_alert",
                        lambda aid, **kwargs: calls.append(aid))

    payload = {
        "event": {
            "event_type": "incident.triggered",
            "data": {"incident": {"dedup_key": "alert-abc-789"}},
        }
    }
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)
    r = requests.post(
        f"{server}/webhooks/pagerduty",
        data=body,
        headers={"X-PagerDuty-Signature": sig},
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json()["acknowledged"] is False
    assert calls == []


def test_pagerduty_resolved_with_empty_dedup_key_no_ops(server, monkeypatch) -> None:
    """Resolution event without a dedup_key → nothing to ack. We must
    not pass an empty string into ``acknowledge_alert`` because that
    would silently match no rows and look like success."""
    calls = []
    monkeypatch.setattr(engv2, "acknowledge_alert",
                        lambda aid, **kwargs: calls.append(aid))

    payload = {
        "event": {
            "event_type": "incident.resolved",
            "data": {"incident": {"dedup_key": ""}},
        }
    }
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)
    r = requests.post(
        f"{server}/webhooks/pagerduty",
        data=body,
        headers={"X-PagerDuty-Signature": sig},
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json()["acknowledged"] is False
    assert calls == []


def test_pagerduty_malformed_json_returns_400(server) -> None:
    body = b"{not valid json"
    sig = _sign(body)
    r = requests.post(
        f"{server}/webhooks/pagerduty",
        data=body,
        headers={"X-PagerDuty-Signature": sig},
        timeout=5,
    )
    assert r.status_code == 400


def test_pagerduty_wrong_hmac_returns_401(server, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(engv2, "acknowledge_alert",
                        lambda aid, **kwargs: calls.append(aid))

    payload = {"event": {"event_type": "incident.resolved",
                         "data": {"incident": {"dedup_key": "x"}}}}
    body = json.dumps(payload).encode("utf-8")
    r = requests.post(
        f"{server}/webhooks/pagerduty",
        data=body,
        headers={"X-PagerDuty-Signature": "0" * 64},
        timeout=5,
    )
    assert r.status_code == 401
    assert calls == []


# ─── Method + path routing ────────────────────────────────────────────────


def test_get_unknown_path_returns_404(server) -> None:
    """GET /<anything-but-/health> → 404 (not 405). The 405 surface is
    reserved for PUT/DELETE/PATCH/HEAD; GET is partially supported
    (/health) so unknown GET paths are just unknown resources."""
    r = requests.get(f"{server}/ack/anything", timeout=5)
    assert r.status_code == 404


def test_put_returns_405(server) -> None:
    """PUT remains a blanket 405 — we don't expose any PUT routes."""
    r = requests.put(f"{server}/anything", timeout=5)
    assert r.status_code == 405


def test_unknown_path_returns_404(server) -> None:
    body = b""
    sig = _sign(body)
    r = requests.post(
        f"{server}/totally/unknown/path",
        data=body,
        headers={"X-Signature-SHA256": sig},
        timeout=5,
    )
    assert r.status_code == 404


# ─── GET /health ──────────────────────────────────────────────────────────

# These tests monkeypatch the *imported* symbol locations the handler
# reaches into at call time. Because ``_handle_health`` does an
# ``from auth.users import count_users`` INSIDE the method body (lazy
# import keeps the listener startup fast and avoids importing the
# whole DB stack on a 401 path), the patch target is ``auth.users``
# module attribute — that's where Python resolves the name. Same for
# the other telemetry layers.


_HEALTH_KEYS = {
    "status",
    "schema_version",
    "users",
    "now_utc",
    "up_seconds",
    "unacked_critical_count",
    "recent_render_success_rate",
    "current_outages",
}


def test_health_returns_200_with_expected_keys(server) -> None:
    """Happy path — fresh DB, no outages, no critical alerts. Expect
    200 + the full key set + status='ok'."""
    r = requests.get(f"{server}/health", timeout=5)
    assert r.status_code == 200
    payload = r.json()
    # Key set MUST match exactly — extra keys are fine, missing keys
    # break monitoring dashboards that parse this shape.
    assert _HEALTH_KEYS.issubset(set(payload.keys()))
    # On a freshly isolated tmp DB count_users returns 0 (empty users
    # table). That's healthy, NOT 'down'.
    assert payload["status"] in ("ok", "degraded")
    assert isinstance(payload["users"], int)
    assert isinstance(payload["schema_version"], int)
    assert isinstance(payload["up_seconds"], (int, float))
    assert payload["up_seconds"] >= 0


def test_health_ok_path_when_everything_healthy(server, monkeypatch) -> None:
    """All telemetry layers green → status='ok'."""
    from auth import users as users_mod
    from engine import perf_telemetry as perf_mod
    from engine import source_health as sh_mod
    from engine import alert_engine_v2 as alert_mod

    monkeypatch.setattr(users_mod, "count_users", lambda: 3)
    monkeypatch.setattr(perf_mod, "get_perf_summary",
                        lambda window_hours=24: {
                            "window_hours": window_hours,
                            "total_renders": 50,
                            "success_rate": 0.99,
                        })
    monkeypatch.setattr(sh_mod, "get_health_summary",
                        lambda window_hours=24: {
                            "window_hours": window_hours,
                            "total_pings": 24,
                            "current_outages": [],
                        })
    monkeypatch.setattr(alert_mod, "load_alerts",
                        lambda max_age_days=30, user_id=None: [])

    r = requests.get(f"{server}/health", timeout=5)
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "ok"
    assert payload["users"] == 3
    assert payload["unacked_critical_count"] == 0
    assert payload["recent_render_success_rate"] == 0.99
    assert payload["current_outages"] == []


def test_health_returns_503_when_count_users_raises(server, monkeypatch) -> None:
    """count_users → exception → 503 + status='down' + error string."""
    from auth import users as users_mod

    def boom() -> int:
        raise RuntimeError("DB connection refused")

    monkeypatch.setattr(users_mod, "count_users", boom)

    r = requests.get(f"{server}/health", timeout=5)
    assert r.status_code == 503
    payload = r.json()
    assert payload["status"] == "down"
    assert "error" in payload
    assert "DB connection refused" in payload["error"]


def test_health_returns_503_when_count_users_returns_minus_one(
    server, monkeypatch,
) -> None:
    """A future count_users variant might return -1 as a DB-down
    sentinel (the spec asks for this explicitly). Health must treat
    that the same as a raised exception."""
    from auth import users as users_mod
    monkeypatch.setattr(users_mod, "count_users", lambda: -1)

    r = requests.get(f"{server}/health", timeout=5)
    assert r.status_code == 503
    payload = r.json()
    assert payload["status"] == "down"


def test_health_degraded_when_perf_below_threshold(server, monkeypatch) -> None:
    """recent_render_success_rate < 0.95 → degraded."""
    from engine import perf_telemetry as perf_mod
    from engine import source_health as sh_mod
    from engine import alert_engine_v2 as alert_mod

    monkeypatch.setattr(perf_mod, "get_perf_summary",
                        lambda window_hours=24: {
                            "window_hours": window_hours,
                            "total_renders": 50,
                            "success_rate": 0.80,   # below 0.95 threshold
                        })
    monkeypatch.setattr(sh_mod, "get_health_summary",
                        lambda window_hours=24: {"current_outages": []})
    monkeypatch.setattr(alert_mod, "load_alerts",
                        lambda max_age_days=30, user_id=None: [])

    r = requests.get(f"{server}/health", timeout=5)
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "degraded"
    assert payload["recent_render_success_rate"] == 0.80


def test_health_degraded_when_current_outages_non_empty(server, monkeypatch) -> None:
    """current_outages with any source → degraded."""
    from engine import perf_telemetry as perf_mod
    from engine import source_health as sh_mod
    from engine import alert_engine_v2 as alert_mod

    monkeypatch.setattr(perf_mod, "get_perf_summary",
                        lambda window_hours=24: {
                            "window_hours": window_hours,
                            "total_renders": 50,
                            "success_rate": 0.99,
                        })
    monkeypatch.setattr(sh_mod, "get_health_summary",
                        lambda window_hours=24: {
                            "current_outages": ["worldbank", "fred"],
                        })
    monkeypatch.setattr(alert_mod, "load_alerts",
                        lambda max_age_days=30, user_id=None: [])

    r = requests.get(f"{server}/health", timeout=5)
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "degraded"
    assert payload["current_outages"] == ["worldbank", "fred"]


def test_health_degraded_when_unacked_critical_present(server, monkeypatch) -> None:
    """Unacked CRITICAL alert in the last 30d → degraded."""
    from engine import alert_engine_v2 as alert_mod
    from engine.alert_engine_v2 import ShippingAlert

    crit_alert = ShippingAlert(
        alert_id="crit-1",
        created_at="2026-05-22T00:00:00+00:00",
        alert_type="BDI_MOVE",
        severity="CRITICAL",
        title="BDI plummeted",
        body="BDI fell 12% in a day.",
        ticker="",
        route_id="",
        port_locode="",
        value=1234.0,
        threshold=5.0,
        change_pct=-12.0,
        acknowledged=False,    # NOT acked → triggers degraded
    )
    monkeypatch.setattr(alert_mod, "load_alerts",
                        lambda max_age_days=30, user_id=None: [crit_alert])

    r = requests.get(f"{server}/health", timeout=5)
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "degraded"
    assert payload["unacked_critical_count"] == 1


def test_health_does_not_require_hmac(server) -> None:
    """No X-Signature-SHA256 header at all — must still return 200.
    /health is a public liveness probe; load balancers don't sign."""
    r = requests.get(f"{server}/health", timeout=5)
    assert r.status_code == 200
    # And conspicuously: no 401 anywhere in the response.
    payload = r.json()
    assert payload.get("status") in ("ok", "degraded")


def test_health_other_get_path_returns_404(server) -> None:
    """The split do_GET routes ONLY /health to a handler. Any other
    GET path returns 404 — NOT 405 (the old behaviour), because GET
    is partially supported."""
    r = requests.get(f"{server}/healthz", timeout=5)
    assert r.status_code == 404
    r = requests.get(f"{server}/status", timeout=5)
    assert r.status_code == 404


# ─── End-to-end (no monkeypatching of the engine) ─────────────────────────


def test_end_to_end_real_acknowledge_alert_does_not_raise(server) -> None:
    """The full path: HTTP → handler → real ``acknowledge_alert`` →
    isolated SQLite tmp DB. We don't assert on any DB state (there are
    no rows to ack — that's the unknown-id case) but we DO assert the
    server returns 200 and the alert engine module didn't blow up
    because of the empty alerts table. This catches regressions where
    e.g. a future ``acknowledge_alert`` refactor adds a required arg."""
    body = b""
    sig = _sign(body)
    r = requests.post(
        f"{server}/ack/some-real-id",
        data=body,
        headers={"X-Signature-SHA256": sig},
        timeout=5,
    )
    assert r.status_code == 200


# ─── HMAC per-IP bucket bounding (memory-exhaustion guard) ───────────────────

def test_hmac_bucket_evicts_idle_entries_when_over_cap(monkeypatch) -> None:
    """Adding a new IP over the cap drops fully-refilled (idle) entries —
    they carry no state, so the table stays bounded."""
    import time as _t

    import worker.webhook_listener as wl

    wl._clear_hmac_buckets()
    monkeypatch.setattr(wl, "_HMAC_BUCKET_MAX_IPS", 5)
    now = _t.monotonic()
    for i in range(5):  # idle: full capacity, last-seen long ago
        wl._HMAC_BUCKETS[f"ip-{i}"] = (float(wl._HMAC_BUCKET_CAPACITY), now - 10_000)

    allowed, _retry = wl._hmac_rate_limit("brand-new-ip")
    assert allowed is True
    assert len(wl._HMAC_BUCKETS) <= 5
    wl._clear_hmac_buckets()


def test_hmac_bucket_clears_on_active_flood(monkeypatch) -> None:
    """When the table is full of ACTIVE (non-idle) entries that can't be
    dropped, a new IP triggers a full clear — bounded memory, every bucket
    just resets to full capacity."""
    import time as _t

    import worker.webhook_listener as wl

    wl._clear_hmac_buckets()
    monkeypatch.setattr(wl, "_HMAC_BUCKET_MAX_IPS", 3)
    now = _t.monotonic()
    for i in range(3):  # active, near-empty, just seen → not idle
        wl._HMAC_BUCKETS[f"active-{i}"] = (0.5, now)

    wl._hmac_rate_limit("flood-ip")
    assert len(wl._HMAC_BUCKETS) <= 3
    wl._clear_hmac_buckets()


# ─── _read_body: oversize cap (unauth DoS guard) ──────────────────────────


class _FakeBodyHandler:
    """Minimal stand-in for a BaseHTTPRequestHandler that _read_body uses:
    just ``.headers`` (a dict supports .get) and ``.rfile`` (a BytesIO)."""

    def __init__(self, content_length, body=b""):
        self.headers = {"Content-Length": str(content_length)}
        self.rfile = io.BytesIO(body)


def test_read_body_rejects_oversized_content_length():
    """A Content-Length above the cap returns b'' WITHOUT reading the socket
    — preventing a huge allocation from an unauthenticated client."""
    from worker import webhook_listener as wl

    h = _FakeBodyHandler(wl.MAX_BODY_BYTES + 1, body=b"x" * 100)
    assert wl._read_body(h) == b""
    # The body was NOT consumed (we rejected before reading).
    assert h.rfile.tell() == 0


def test_read_body_reads_within_cap():
    """A within-cap Content-Length reads exactly that many bytes."""
    from worker import webhook_listener as wl

    h = _FakeBodyHandler(5, body=b"hello world")
    assert wl._read_body(h) == b"hello"


def test_read_body_empty_or_malformed_returns_empty():
    """Missing/zero/garbage Content-Length → empty body, no read."""
    from worker import webhook_listener as wl

    assert wl._read_body(_FakeBodyHandler(0)) == b""
    assert wl._read_body(_FakeBodyHandler("not-a-number")) == b""


def test_dispatch_handler_has_socket_timeout():
    """The handler sets a finite per-request timeout so a stalled/withheld
    body cannot pin the single-threaded server forever."""
    from worker import webhook_listener as wl

    assert isinstance(wl._DispatchHandler.timeout, (int, float))
    assert wl._DispatchHandler.timeout and wl._DispatchHandler.timeout > 0
