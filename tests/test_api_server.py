"""Tests for ``worker.api_server`` — the stdlib read-only HTTP API.

Spin up a real ``HTTPServer`` bound to ``127.0.0.1:0`` (kernel-assigned
ephemeral port) in a daemon thread per test, fire ``requests`` at it
from the test process, tear it down in fixture teardown. The same
pattern as ``tests/test_webhook_listener.py``; we deliberately avoid
mocking the HTTP layer because the listener's value is in the wire
behaviour (status codes, header parsing, JSON payload shape, bearer
auth flow).

Each test isolates the SQLite state DB to a per-test ``tmp_path`` so
engine calls (``acknowledge_alert``, ``load_alerts``, …) don't touch
the real ``cache/ship_tracker.db``. We seed a user + a fresh API
token per test via the real ``auth.users.signup`` /
``auth.tokens.create_token`` so the verify path is exercised end-to-
end — no mocks past the bind socket.
"""
from __future__ import annotations

import socket
import threading
from http.server import HTTPServer
from typing import Optional

import pytest
import requests

from worker import api_server


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite isolation. Mirrors the pattern in
    test_webhook_listener.py — any ``state.db.get_connection`` call
    lands in this tmp file."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    """Spin up the API handler on localhost:<ephemeral-port> in a
    daemon thread. Tear down cleanly in teardown — ``shutdown()`` on
    the server unblocks ``serve_forever`` and the thread exits."""
    port = _find_free_port()
    httpd = HTTPServer(("127.0.0.1", port), api_server.APIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        yield base_url
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2.0)


def _make_user(username: str = "alice", password: str = "Hunter2!hunter") -> str:
    """Create a fresh user via the real signup path. Returns user_id."""
    from auth.users import signup
    u = signup(username, password)
    assert u is not None
    return u.user_id


def _mint_token(user_id: str, label: str = "ci") -> str:
    """Create an API token and return the raw secret."""
    from auth.tokens import create_token
    result = create_token(user_id, label)
    assert result is not None
    _meta, raw = result
    return raw


def _seed_alerts(
    user_id: str,
    *,
    n: int = 1,
    severity: str = "HIGH",
    alert_type: str = "BDI_MOVE",
    ticker_prefix: str = "T",
) -> list[str]:
    """Insert ``n`` distinct alerts for ``user_id`` and return their ids.

    Each alert gets a unique ticker so the engine's dedup_key logic
    treats them as separate rows — otherwise ``save_alerts`` would
    collapse them into one row with a higher fire_count."""
    from engine.alert_engine_v2 import ShippingAlert, save_alerts, _now_iso, _new_id

    alerts = []
    for i in range(n):
        alerts.append(ShippingAlert(
            alert_id=_new_id(),
            created_at=_now_iso(),
            alert_type=alert_type,
            severity=severity,
            title=f"alert #{i}",
            body="seeded",
            ticker=f"{ticker_prefix}{i:04d}",
            route_id="",
            port_locode="",
            value=float(i),
            threshold=0.0,
            change_pct=float(i),
            acknowledged=False,
        ))
    save_alerts(alerts, user_id=user_id)
    return [a.alert_id for a in alerts]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ─── Health endpoint: public ──────────────────────────────────────────────


def test_health_returns_200_without_auth(server):
    """``GET /api/v1/health`` is unauthenticated by design — load
    balancers must be able to probe without shipping a token."""
    r = requests.get(f"{server}/api/v1/health", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"ok", "degraded"}
    assert "schema_version" in body
    assert "up_seconds" in body
    assert "users" in body
    assert "unacked_critical_count" in body


def test_health_payload_carries_now_utc_iso(server):
    """The shape mirrors ``worker.webhook_listener.GET /health``."""
    r = requests.get(f"{server}/api/v1/health", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert "now_utc" in body
    # Must be parseable ISO-8601.
    from datetime import datetime
    datetime.fromisoformat(body["now_utc"])


def test_health_does_not_require_authorization_header(server):
    """Sanity check: a deliberately broken bearer header still gets a
    200 from /health — auth bypass is the whole point."""
    r = requests.get(
        f"{server}/api/v1/health",
        headers={"Authorization": "Bearer not-a-real-token"},
        timeout=5,
    )
    assert r.status_code == 200


# ─── Auth: missing / invalid / revoked token → 401 ────────────────────────


@pytest.mark.parametrize("path,method", [
    ("/api/v1/alerts", "GET"),
    ("/api/v1/alerts/some-id", "GET"),
    ("/api/v1/alerts/some-id/ack", "POST"),
    ("/api/v1/reports", "GET"),
    ("/api/v1/reports/some-id/html", "GET"),
    ("/api/v1/telemetry/llm", "GET"),
    ("/api/v1/telemetry/perf", "GET"),
])
def test_endpoint_returns_401_without_auth_header(server, path, method):
    """Every authenticated endpoint must return 401 when the
    Authorization header is absent — never 200/4xx/5xx leaking data."""
    r = requests.request(method, f"{server}{path}", timeout=5)
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


@pytest.mark.parametrize("path,method", [
    ("/api/v1/alerts", "GET"),
    ("/api/v1/alerts/some-id", "GET"),
    ("/api/v1/alerts/some-id/ack", "POST"),
    ("/api/v1/reports", "GET"),
    ("/api/v1/reports/some-id/html", "GET"),
    ("/api/v1/telemetry/llm", "GET"),
    ("/api/v1/telemetry/perf", "GET"),
])
def test_endpoint_returns_401_with_invalid_token(server, path, method):
    """A bearer header whose token is not in the api_tokens table must
    401 — never let a random string in."""
    headers = _bearer("invalid-token-that-does-not-match-anything")
    r = requests.request(method, f"{server}{path}", headers=headers, timeout=5)
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


@pytest.mark.parametrize("path,method", [
    ("/api/v1/alerts", "GET"),
    ("/api/v1/alerts/some-id", "GET"),
    ("/api/v1/alerts/some-id/ack", "POST"),
    ("/api/v1/reports", "GET"),
    ("/api/v1/reports/some-id/html", "GET"),
    ("/api/v1/telemetry/llm", "GET"),
    ("/api/v1/telemetry/perf", "GET"),
])
def test_endpoint_returns_401_with_revoked_token(server, path, method):
    """A revoked token must 401. We mint a token, revoke it, then
    re-issue the request — same secret, but the row's revoked=1 flag
    excludes it from ``verify_token``'s lookup."""
    from auth.tokens import create_token, revoke_token
    uid = _make_user("alice", "Hunter2!hunter")
    result = create_token(uid, "ci")
    assert result is not None
    meta, raw = result
    assert revoke_token(meta.token_id, user_id=uid) is True

    headers = _bearer(raw)
    r = requests.request(method, f"{server}{path}", headers=headers, timeout=5)
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


def test_malformed_authorization_header_returns_401(server):
    """An ``Authorization`` header that does not start with ``Bearer ``
    is treated as no auth — 401."""
    r = requests.get(
        f"{server}/api/v1/alerts",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
        timeout=5,
    )
    assert r.status_code == 401


def test_bearer_scheme_is_case_insensitive(server):
    """RFC 6750 says the auth-scheme name is case-insensitive — a
    lowercase ``bearer`` prefix must work."""
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.get(
        f"{server}/api/v1/alerts",
        headers={"Authorization": f"bearer {token}"},
        timeout=5,
    )
    assert r.status_code == 200


# ─── GET /api/v1/alerts ───────────────────────────────────────────────────


def test_list_alerts_returns_200_and_array(server):
    uid = _make_user()
    token = _mint_token(uid)
    _seed_alerts(uid, n=3)
    r = requests.get(f"{server}/api/v1/alerts", headers=_bearer(token), timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 3
    # Each row carries the dataclass field set.
    for row in body:
        assert {"alert_id", "severity", "alert_type", "title", "acknowledged"}.issubset(row)


def test_list_alerts_empty_for_user_with_no_alerts(server):
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.get(f"{server}/api/v1/alerts", headers=_bearer(token), timeout=5)
    assert r.status_code == 200
    assert r.json() == []


def test_list_alerts_filters_by_severity(server):
    uid = _make_user()
    token = _mint_token(uid)
    _seed_alerts(uid, n=2, severity="CRITICAL", ticker_prefix="C")
    _seed_alerts(uid, n=3, severity="HIGH", ticker_prefix="H")
    _seed_alerts(uid, n=1, severity="LOW", ticker_prefix="L")
    r = requests.get(
        f"{server}/api/v1/alerts",
        params={"severity": "HIGH"},
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 3
    assert all(row["severity"] == "HIGH" for row in rows)


def test_list_alerts_severity_filter_is_case_insensitive(server):
    """A lowercase query parameter still matches the engine's
    uppercase severity values — defensive UX so callers don't have
    to guess the casing."""
    uid = _make_user()
    token = _mint_token(uid)
    _seed_alerts(uid, n=2, severity="CRITICAL", ticker_prefix="C")
    r = requests.get(
        f"{server}/api/v1/alerts",
        params={"severity": "critical"},
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert all(row["severity"] == "CRITICAL" for row in rows)


def test_list_alerts_caps_at_500_even_with_large_window(server):
    """A 365-day window asking for 600 alerts must still return at
    most 500. Caps the JSON payload."""
    uid = _make_user()
    token = _mint_token(uid)
    _seed_alerts(uid, n=600, ticker_prefix="X")
    r = requests.get(
        f"{server}/api/v1/alerts",
        params={"window_days": 365},
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 500


def test_list_alerts_respects_window_days(server):
    """Asking for window_days=0 must return zero rows — the engine's
    cutoff excludes everything older than 'now'."""
    uid = _make_user()
    token = _mint_token(uid)
    _seed_alerts(uid, n=3)
    r = requests.get(
        f"{server}/api/v1/alerts",
        params={"window_days": 0},
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json() == []


def test_list_alerts_is_user_scoped(server):
    """Alice's token must NOT see Bob's alerts. Bob's alert is seeded
    under Bob's user_id; Alice's GET returns an empty list."""
    alice_uid = _make_user("alice", "Hunter2!hunter")
    bob_uid = _make_user("bob", "Hunter2!hunter")
    alice_token = _mint_token(alice_uid)
    _seed_alerts(bob_uid, n=4, ticker_prefix="B")
    r = requests.get(
        f"{server}/api/v1/alerts", headers=_bearer(alice_token), timeout=5,
    )
    assert r.status_code == 200
    assert r.json() == []


# ─── GET /api/v1/alerts/<id> ──────────────────────────────────────────────


def test_get_single_alert_returns_dict(server):
    uid = _make_user()
    token = _mint_token(uid)
    ids = _seed_alerts(uid, n=2)
    r = requests.get(
        f"{server}/api/v1/alerts/{ids[0]}",
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["alert_id"] == ids[0]
    assert "severity" in body
    assert "title" in body


def test_get_single_alert_unknown_id_returns_404(server):
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.get(
        f"{server}/api/v1/alerts/this-id-does-not-exist",
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 404
    assert r.json() == {"error": "not found"}


def test_get_single_alert_cross_user_returns_404(server):
    """Alice tries to read Bob's alert by id → 404 (NOT 403). The
    response must be indistinguishable from 'unknown id' so a token-
    holder can't enumerate other users' alert ids."""
    alice_uid = _make_user("alice", "Hunter2!hunter")
    bob_uid = _make_user("bob", "Hunter2!hunter")
    alice_token = _mint_token(alice_uid)
    bob_ids = _seed_alerts(bob_uid, n=1, ticker_prefix="B")
    r = requests.get(
        f"{server}/api/v1/alerts/{bob_ids[0]}",
        headers=_bearer(alice_token),
        timeout=5,
    )
    assert r.status_code == 404


# ─── POST /api/v1/alerts/<id>/ack ─────────────────────────────────────────


def test_ack_alert_marks_acknowledged(server):
    uid = _make_user()
    token = _mint_token(uid)
    ids = _seed_alerts(uid, n=1)

    r = requests.post(
        f"{server}/api/v1/alerts/{ids[0]}/ack",
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json()["acknowledged"] is True

    # Verify via the engine: load_alerts should return the alert with
    # acknowledged=True.
    from engine.alert_engine_v2 import load_alerts
    alerts = load_alerts(max_age_days=365, user_id=uid)
    match = next(a for a in alerts if a.alert_id == ids[0])
    assert match.acknowledged is True


def test_ack_alert_for_different_user_does_not_ack(server):
    """Alice's token MUST NOT be able to ack Bob's alert. The endpoint
    returns 200 (per-spec: no info leak about other users' alert ids)
    BUT the underlying engine UPDATE matches zero rows because the
    SQL scope filter excludes Bob's row from Alice's view."""
    alice_uid = _make_user("alice", "Hunter2!hunter")
    bob_uid = _make_user("bob", "Hunter2!hunter")
    alice_token = _mint_token(alice_uid)
    bob_ids = _seed_alerts(bob_uid, n=1, ticker_prefix="B")

    # Alice tries to ack Bob's alert.
    r = requests.post(
        f"{server}/api/v1/alerts/{bob_ids[0]}/ack",
        headers=_bearer(alice_token),
        timeout=5,
    )
    # The endpoint still returns 200 — we don't surface "no rows
    # updated" because that would let Alice probe for Bob's ids.
    assert r.status_code == 200

    # But Bob's alert MUST still be unacknowledged in the DB.
    from engine.alert_engine_v2 import load_alerts
    bob_alerts = load_alerts(max_age_days=365, user_id=bob_uid)
    bob_alert = next(a for a in bob_alerts if a.alert_id == bob_ids[0])
    assert bob_alert.acknowledged is False, \
        "Alice's token must not be able to ack Bob's alert"


def test_ack_alert_unknown_id_returns_200_idempotent(server):
    """Acking an unknown id is a no-op at the engine layer (UPDATE
    affects zero rows). The endpoint returns 200 idempotently so
    retries don't surface phantom 404s after an earlier ack."""
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.post(
        f"{server}/api/v1/alerts/never-existed/ack",
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200


# ─── GET /api/v1/reports ──────────────────────────────────────────────────


def _seed_report(user_id: str, label: str = "rep") -> str:
    """Drop a tiny HTML file in REPORT_DIR and insert a row scoped to
    ``user_id``. Returns the new report_id."""
    import uuid
    from pathlib import Path
    from utils import report_history as rh
    from state.db import get_connection

    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_id = str(uuid.uuid4())
    file_path = rh.REPORT_DIR / f"test_{report_id[:8]}_{label}.html"
    html = f"<html><body><h1>{label}</h1></body></html>"
    file_path.write_text(html, encoding="utf-8")

    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO report_history
              (report_id, generated_at, report_date, sentiment_label,
               sentiment_score, risk_level, signal_count, data_quality,
               file_path, file_size_kb, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id, "2026-05-22T00:00:00+00:00", "May 22, 2026",
                "NEUTRAL", 0.0, "MODERATE", 0, "FULL",
                str(file_path.resolve()), 0.5, user_id,
            ),
        )
    return report_id


def test_list_reports_returns_array(server, tmp_path, monkeypatch):
    # Redirect REPORT_DIR to tmp so we don't write into the real cache.
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")

    uid = _make_user()
    token = _mint_token(uid)
    rid = _seed_report(uid)
    r = requests.get(
        f"{server}/api/v1/reports", headers=_bearer(token), timeout=5,
    )
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert any(row["report_id"] == rid for row in rows)


def test_list_reports_empty_for_user_with_no_reports(server, tmp_path, monkeypatch):
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.get(
        f"{server}/api/v1/reports", headers=_bearer(token), timeout=5,
    )
    assert r.status_code == 200
    assert r.json() == []


def test_list_reports_respects_limit(server, tmp_path, monkeypatch):
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    uid = _make_user()
    token = _mint_token(uid)
    for i in range(5):
        _seed_report(uid, label=f"r{i}")
    r = requests.get(
        f"{server}/api/v1/reports",
        params={"limit": 2},
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_list_reports_is_user_scoped(server, tmp_path, monkeypatch):
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    alice_uid = _make_user("alice", "Hunter2!hunter")
    bob_uid = _make_user("bob", "Hunter2!hunter")
    alice_token = _mint_token(alice_uid)
    _seed_report(bob_uid, label="b1")
    r = requests.get(
        f"{server}/api/v1/reports", headers=_bearer(alice_token), timeout=5,
    )
    assert r.status_code == 200
    # Bob's report must not appear in Alice's listing.
    rows = r.json()
    assert all(row.get("report_id") != "b1" for row in rows)


# ─── GET /api/v1/reports/<id>/html ────────────────────────────────────────


def test_get_report_html_returns_raw_html(server, tmp_path, monkeypatch):
    """Reports endpoint returns raw HTML with text/html — NOT JSON."""
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    uid = _make_user()
    token = _mint_token(uid)
    rid = _seed_report(uid, label="raw")

    r = requests.get(
        f"{server}/api/v1/reports/{rid}/html",
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200
    ctype = r.headers.get("Content-Type", "")
    assert ctype.startswith("text/html")
    assert "<html>" in r.text
    assert "raw" in r.text


def test_get_report_html_unknown_id_returns_404(server, tmp_path, monkeypatch):
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.get(
        f"{server}/api/v1/reports/this-id-does-not-exist/html",
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 404
    # 404 from this endpoint IS JSON — only the success path emits HTML.
    assert r.json() == {"error": "not found"}


def test_get_report_html_cross_user_returns_404(server, tmp_path, monkeypatch):
    """Bob's report id, looked up with Alice's token → 404."""
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    alice_uid = _make_user("alice", "Hunter2!hunter")
    bob_uid = _make_user("bob", "Hunter2!hunter")
    alice_token = _mint_token(alice_uid)
    bob_rid = _seed_report(bob_uid, label="bobs")
    r = requests.get(
        f"{server}/api/v1/reports/{bob_rid}/html",
        headers=_bearer(alice_token),
        timeout=5,
    )
    assert r.status_code == 404


# ─── GET /api/v1/telemetry/llm ────────────────────────────────────────────


def test_telemetry_llm_returns_summary_shape(server):
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.get(
        f"{server}/api/v1/telemetry/llm",
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200
    body = r.json()
    # Empty DB → zeroed dict with the canonical keys.
    for key in ("window_days", "total_calls", "total_tokens_in",
                "total_tokens_out", "total_cost_usd",
                "by_source", "by_model", "by_day"):
        assert key in body


def test_telemetry_llm_respects_window_days(server):
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.get(
        f"{server}/api/v1/telemetry/llm",
        params={"window_days": 1},
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json()["window_days"] == 1


# ─── GET /api/v1/telemetry/perf ───────────────────────────────────────────


def test_telemetry_perf_returns_summary_shape(server):
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.get(
        f"{server}/api/v1/telemetry/perf",
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200
    body = r.json()
    for key in ("window_hours", "total_renders", "success_rate",
                "by_tab", "top_slow_tabs"):
        assert key in body


def test_telemetry_perf_respects_window_hours(server):
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.get(
        f"{server}/api/v1/telemetry/perf",
        params={"window_hours": 6},
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json()["window_hours"] == 6


# ─── Routing: 404 / 405 ──────────────────────────────────────────────────


def test_unknown_path_returns_404(server):
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.get(
        f"{server}/api/v1/no-such-thing",
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 404
    assert r.json() == {"error": "not found"}


def test_unknown_root_path_returns_404(server):
    """An authenticated request to '/' returns 404 (no such endpoint).
    Without auth it would 401 first — the auth check fires before
    path dispatch by design (no info leak about valid paths)."""
    uid = _make_user("alice", "Hunter2!hunter")
    token = _mint_token(uid)
    r = requests.get(f"{server}/", headers=_bearer(token), timeout=5)
    assert r.status_code == 404


def test_post_to_get_endpoint_returns_405(server):
    """POST /api/v1/alerts is not a defined route — the same path
    is defined under GET. Must return 405 (path known under another
    verb), not 404."""
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.post(
        f"{server}/api/v1/alerts",
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 405
    assert r.json() == {"error": "method not allowed"}


def test_get_on_ack_endpoint_returns_405(server):
    """GET on the ack endpoint (which is POST-only) → 405."""
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.get(
        f"{server}/api/v1/alerts/some-id/ack",
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 405


def test_put_returns_405_on_known_path(server):
    """PUT is unsupported — any known path should 405."""
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.put(
        f"{server}/api/v1/alerts",
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 405


# ─── Smoke: full request with response headers ───────────────────────────


def test_response_content_type_is_json_for_list_alerts(server):
    """JSON endpoints emit ``application/json`` — clients written
    against the spec need to be able to rely on the header."""
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.get(
        f"{server}/api/v1/alerts", headers=_bearer(token), timeout=5,
    )
    assert r.status_code == 200
    assert r.headers.get("Content-Type", "").startswith("application/json")


# ─── Write endpoints — auth on all new write paths ────────────────────────


@pytest.mark.parametrize("method,path", [
    ("POST",   "/api/v1/rules"),
    ("GET",    "/api/v1/rules"),
    ("DELETE", "/api/v1/rules"),
    ("POST",   "/api/v1/channels"),
    ("GET",    "/api/v1/channels"),
    ("DELETE", "/api/v1/channels/abc"),
    ("POST",   "/api/v1/reports/some-id/public"),
    ("DELETE", "/api/v1/reports/some-id/public"),
])
def test_write_endpoint_returns_401_without_auth_header(server, method, path):
    """Every WRITE endpoint must 401 with no Authorization header.
    Same contract as the read endpoints — never leak data on auth
    miss."""
    r = requests.request(method, f"{server}{path}", timeout=5)
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


# ─── POST /api/v1/rules ───────────────────────────────────────────────────


def _sample_rules() -> list[dict]:
    """A pair of well-formed rule dicts for the rule-write tests.

    Shape mirrors ``ui/tab_alerts.py``'s session-state rule entries —
    a ``rule_id`` is the only required field for ``save_rules`` to
    pick up the dict."""
    return [
        {"rule_id": "r1", "name": "BDI surge", "metric": "bdi",
         "threshold_pct": 5.0, "severity": "HIGH"},
        {"rule_id": "r2", "name": "Port congestion", "metric": "congestion",
         "threshold": 0.8, "severity": "MEDIUM"},
    ]


def test_post_rules_saves_and_get_retrieves(server):
    """POST /api/v1/rules persists the body; the subsequent GET returns
    the same set (modulo ordering — ``load_rules`` orders by rule_id)."""
    uid = _make_user()
    token = _mint_token(uid)
    rules = _sample_rules()
    r = requests.post(
        f"{server}/api/v1/rules",
        json=rules,
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"saved": True, "count": 2}

    r2 = requests.get(
        f"{server}/api/v1/rules", headers=_bearer(token), timeout=5,
    )
    assert r2.status_code == 200
    loaded = r2.json()
    assert isinstance(loaded, list)
    assert len(loaded) == 2
    loaded_ids = {x["rule_id"] for x in loaded}
    assert loaded_ids == {"r1", "r2"}


def test_post_rules_without_auth_returns_401(server):
    r = requests.post(
        f"{server}/api/v1/rules",
        json=_sample_rules(),
        timeout=5,
    )
    assert r.status_code == 401


def test_post_rules_with_non_json_content_type_returns_415(server):
    """Body present but ``Content-Type: text/plain`` → 415."""
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.post(
        f"{server}/api/v1/rules",
        data="not json",
        headers={**_bearer(token), "Content-Type": "text/plain"},
        timeout=5,
    )
    assert r.status_code == 415


def test_post_rules_with_malformed_json_returns_400(server):
    """``Content-Type: application/json`` but the body does not
    decode → 400."""
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.post(
        f"{server}/api/v1/rules",
        data="{not valid json",
        headers={**_bearer(token), "Content-Type": "application/json"},
        timeout=5,
    )
    assert r.status_code == 400


def test_post_rules_with_non_list_body_returns_400(server):
    """Body decodes as JSON but is a dict (not a list) → 400."""
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.post(
        f"{server}/api/v1/rules",
        json={"not": "a list"},
        headers=_bearer(token),
        timeout=5,
    )
    assert r.status_code == 400


def test_get_rules_empty_for_user_with_no_rules(server):
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.get(
        f"{server}/api/v1/rules", headers=_bearer(token), timeout=5,
    )
    assert r.status_code == 200
    assert r.json() == []


def test_post_rules_is_user_scoped(server):
    """Alice's POST must not appear in Bob's GET. The engine's per-user
    scope keeps them apart."""
    alice_uid = _make_user("alice", "Hunter2!hunter")
    bob_uid = _make_user("bob", "Hunter2!hunter")
    alice_token = _mint_token(alice_uid)
    bob_token = _mint_token(bob_uid)

    r = requests.post(
        f"{server}/api/v1/rules",
        json=_sample_rules(),
        headers=_bearer(alice_token),
        timeout=5,
    )
    assert r.status_code == 200

    # Bob still sees zero rules.
    r2 = requests.get(
        f"{server}/api/v1/rules", headers=_bearer(bob_token), timeout=5,
    )
    assert r2.status_code == 200
    assert r2.json() == []


# ─── DELETE /api/v1/rules ─────────────────────────────────────────────────


def test_delete_rules_wipes_users_rules(server):
    """After DELETE, GET returns []. The engine call routes through
    ``save_rules([], user_id=...)`` which is the per-user equivalent
    of ``reset_rules``."""
    uid = _make_user()
    token = _mint_token(uid)
    requests.post(
        f"{server}/api/v1/rules", json=_sample_rules(),
        headers=_bearer(token), timeout=5,
    )

    r = requests.delete(
        f"{server}/api/v1/rules", headers=_bearer(token), timeout=5,
    )
    assert r.status_code == 200
    assert r.json() == {"reset": True}

    r2 = requests.get(
        f"{server}/api/v1/rules", headers=_bearer(token), timeout=5,
    )
    assert r2.json() == []


def test_delete_rules_does_not_wipe_other_users_rules(server):
    """Alice's DELETE must NOT affect Bob's rules — per-user scope.

    ``alert_rules.rule_id`` is a global PRIMARY KEY (schema v1), so
    each user picks disjoint ids — this matches reality: rule ids are
    UUID-ish in the UI, never colliding across users."""
    alice_uid = _make_user("alice", "Hunter2!hunter")
    bob_uid = _make_user("bob", "Hunter2!hunter")
    alice_token = _mint_token(alice_uid)
    bob_token = _mint_token(bob_uid)

    alice_rules = [{"rule_id": "alice-r1", "name": "a-rule-1"},
                   {"rule_id": "alice-r2", "name": "a-rule-2"}]
    bob_rules = [{"rule_id": "bob-r1", "name": "b-rule-1"},
                 {"rule_id": "bob-r2", "name": "b-rule-2"}]

    requests.post(
        f"{server}/api/v1/rules", json=alice_rules,
        headers=_bearer(alice_token), timeout=5,
    )
    requests.post(
        f"{server}/api/v1/rules", json=bob_rules,
        headers=_bearer(bob_token), timeout=5,
    )

    # Alice nukes hers.
    r = requests.delete(
        f"{server}/api/v1/rules", headers=_bearer(alice_token), timeout=5,
    )
    assert r.status_code == 200

    # Bob's rules survive intact.
    r2 = requests.get(
        f"{server}/api/v1/rules", headers=_bearer(bob_token), timeout=5,
    )
    assert r2.status_code == 200
    bobs = r2.json()
    bob_ids = {x["rule_id"] for x in bobs}
    assert bob_ids == {"bob-r1", "bob-r2"}

    # Alice should now have an empty set.
    r3 = requests.get(
        f"{server}/api/v1/rules", headers=_bearer(alice_token), timeout=5,
    )
    assert r3.json() == []


# ─── POST /api/v1/channels ────────────────────────────────────────────────


def _sample_channel(channel_id: str = "ch-alpha") -> dict:
    return {
        "channel_id": channel_id,
        "name": "Trading desk Slack",
        "kind": "slack",
        "target": "https://hooks.slack.com/services/AAA/BBB/CCC",
        "severity_threshold": "HIGH",
        "enabled": True,
    }


def test_post_channel_saves_and_appears_in_get(server):
    """POST /api/v1/channels with a valid DeliveryChannel dict persists;
    GET /api/v1/channels lists it."""
    uid = _make_user()
    token = _mint_token(uid)
    ch = _sample_channel()

    r = requests.post(
        f"{server}/api/v1/channels", json=ch,
        headers=_bearer(token), timeout=5,
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"saved": True, "channel_id": "ch-alpha"}

    r2 = requests.get(
        f"{server}/api/v1/channels", headers=_bearer(token), timeout=5,
    )
    assert r2.status_code == 200
    listed = r2.json()
    assert isinstance(listed, list)
    assert len(listed) == 1
    assert listed[0]["channel_id"] == "ch-alpha"
    assert listed[0]["name"] == "Trading desk Slack"
    assert listed[0]["kind"] == "slack"
    # Defensive: the API list intentionally OMITS `target` to avoid
    # leaking the Slack webhook URL on a token compromise.
    assert "target" not in listed[0]


def test_post_channel_missing_channel_id_returns_400(server):
    uid = _make_user()
    token = _mint_token(uid)
    body = _sample_channel()
    body.pop("channel_id")
    r = requests.post(
        f"{server}/api/v1/channels", json=body,
        headers=_bearer(token), timeout=5,
    )
    assert r.status_code == 400


def test_post_channel_with_non_json_content_type_returns_415(server):
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.post(
        f"{server}/api/v1/channels",
        data="not json",
        headers={**_bearer(token), "Content-Type": "text/plain"},
        timeout=5,
    )
    assert r.status_code == 415


def test_post_channel_with_malformed_json_returns_400(server):
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.post(
        f"{server}/api/v1/channels",
        data="{also-bad",
        headers={**_bearer(token), "Content-Type": "application/json"},
        timeout=5,
    )
    assert r.status_code == 400


# ─── DELETE /api/v1/channels/<id> ─────────────────────────────────────────


def test_delete_channel_removes_only_that_channel(server):
    """Insert two channels, delete one, the other survives."""
    uid = _make_user()
    token = _mint_token(uid)
    requests.post(
        f"{server}/api/v1/channels", json=_sample_channel("ch-A"),
        headers=_bearer(token), timeout=5,
    )
    requests.post(
        f"{server}/api/v1/channels", json=_sample_channel("ch-B"),
        headers=_bearer(token), timeout=5,
    )

    r = requests.delete(
        f"{server}/api/v1/channels/ch-A",
        headers=_bearer(token), timeout=5,
    )
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "channel_id": "ch-A"}

    r2 = requests.get(
        f"{server}/api/v1/channels", headers=_bearer(token), timeout=5,
    )
    listed = r2.json()
    ids = {c["channel_id"] for c in listed}
    assert ids == {"ch-B"}, f"only ch-B should remain, got {ids}"


def test_delete_channel_as_different_user_does_not_delete(server):
    """Alice owns ch-X. Bob's DELETE with his token must NOT delete it
    — the engine's per-user scope filter excludes Alice's row from
    Bob's UPDATE/DELETE. Bob still gets 200 (no info leak about
    other users' channel ids), but Alice's row survives."""
    alice_uid = _make_user("alice", "Hunter2!hunter")
    bob_uid = _make_user("bob", "Hunter2!hunter")
    alice_token = _mint_token(alice_uid)
    bob_token = _mint_token(bob_uid)

    requests.post(
        f"{server}/api/v1/channels", json=_sample_channel("ch-X"),
        headers=_bearer(alice_token), timeout=5,
    )

    # Bob tries to delete ch-X.
    r = requests.delete(
        f"{server}/api/v1/channels/ch-X",
        headers=_bearer(bob_token), timeout=5,
    )
    assert r.status_code == 200  # no enumeration

    # Verify via engine: Alice's channel survives.
    from engine.alert_delivery import load_channels
    alice_chs = load_channels(user_id=alice_uid)
    alice_ids = {c.channel_id for c in alice_chs}
    assert "ch-X" in alice_ids, \
        "Bob's DELETE must not have removed Alice's channel"


# ─── POST + DELETE /api/v1/reports/<id>/public ────────────────────────────


def test_post_report_public_returns_slug_and_load_works(
    server, tmp_path, monkeypatch,
):
    """POST returns a slug; ``load_public_report(slug)`` then returns
    the report's HTML. Round-trip proves the slug actually wired up."""
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    uid = _make_user()
    token = _mint_token(uid)
    rid = _seed_report(uid, label="pub")

    r = requests.post(
        f"{server}/api/v1/reports/{rid}/public",
        json={"expires_in_days": 7},
        headers=_bearer(token), timeout=5,
    )
    assert r.status_code == 200
    body = r.json()
    assert "slug" in body
    slug = body["slug"]
    assert isinstance(slug, str) and len(slug) > 0

    from utils.report_history import load_public_report
    html = load_public_report(slug)
    assert html is not None
    assert "pub" in html


def test_post_report_public_unknown_id_returns_404(
    server, tmp_path, monkeypatch,
):
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.post(
        f"{server}/api/v1/reports/no-such-report/public",
        json={"expires_in_days": 30},
        headers=_bearer(token), timeout=5,
    )
    assert r.status_code == 404


def test_post_report_public_cross_user_returns_404(
    server, tmp_path, monkeypatch,
):
    """Alice cannot publish Bob's report — 404 (NOT 403), matching the
    no-info-leak contract used elsewhere."""
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    alice_uid = _make_user("alice", "Hunter2!hunter")
    bob_uid = _make_user("bob", "Hunter2!hunter")
    alice_token = _mint_token(alice_uid)
    bob_rid = _seed_report(bob_uid, label="bobs")
    r = requests.post(
        f"{server}/api/v1/reports/{bob_rid}/public",
        json={"expires_in_days": 30},
        headers=_bearer(alice_token), timeout=5,
    )
    assert r.status_code == 404


def test_post_report_public_with_empty_body_defaults_to_30_days(
    server, tmp_path, monkeypatch,
):
    """Body is optional — when absent, defaults to 30 days. The slug
    is still issued."""
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    uid = _make_user()
    token = _mint_token(uid)
    rid = _seed_report(uid, label="default")
    # No body, no Content-Type — should still succeed.
    r = requests.post(
        f"{server}/api/v1/reports/{rid}/public",
        headers=_bearer(token), timeout=5,
    )
    assert r.status_code == 200
    assert "slug" in r.json()


def test_delete_report_public_clears_slug(server, tmp_path, monkeypatch):
    """POST grants a slug, DELETE revokes it. After revoke
    ``load_public_report(old_slug)`` returns None."""
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    uid = _make_user()
    token = _mint_token(uid)
    rid = _seed_report(uid, label="revokeme")

    r = requests.post(
        f"{server}/api/v1/reports/{rid}/public",
        json={"expires_in_days": 30},
        headers=_bearer(token), timeout=5,
    )
    slug = r.json()["slug"]

    r2 = requests.delete(
        f"{server}/api/v1/reports/{rid}/public",
        headers=_bearer(token), timeout=5,
    )
    assert r2.status_code == 200
    assert r2.json() == {"revoked": True}

    from utils.report_history import load_public_report
    assert load_public_report(slug) is None, \
        "slug should be invalidated after revoke"


def test_delete_report_public_unknown_id_returns_404(
    server, tmp_path, monkeypatch,
):
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    uid = _make_user()
    token = _mint_token(uid)
    r = requests.delete(
        f"{server}/api/v1/reports/no-such-report/public",
        headers=_bearer(token), timeout=5,
    )
    assert r.status_code == 404
