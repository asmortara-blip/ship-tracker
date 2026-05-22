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

    def fake_ack(alert_id: str) -> None:
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
                        lambda aid: calls.append(aid))

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
    calls = []
    monkeypatch.setattr(engv2, "acknowledge_all", lambda: calls.append(1))

    body = b""
    sig = _sign(body)
    r = requests.post(
        f"{server}/ack-all",
        data=body,
        headers={"X-Signature-SHA256": sig},
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json() == {"acknowledged": True, "scope": "all"}
    assert calls == [1]


def test_ack_all_with_wrong_hmac_returns_401(server, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(engv2, "acknowledge_all", lambda: calls.append(1))

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
                        lambda aid: calls.append(aid))

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
                        lambda aid: calls.append(aid))

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
                        lambda aid: calls.append(aid))

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
                        lambda aid: calls.append(aid))

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


def test_get_returns_405(server) -> None:
    r = requests.get(f"{server}/ack/anything", timeout=5)
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
