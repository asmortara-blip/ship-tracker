"""Tests for the inbound POST /events endpoint on worker.webhook_listener.

These tests exercise the real network path: each test spins up an
``HTTPServer`` on ``127.0.0.1:<ephemeral-port>`` inside a daemon
thread, fires ``requests`` at it, then tears the server down. We
share the SQLite-isolation + free-port + server fixtures with
``test_webhook_listener.py`` but redefine them here so this file is
runnable in isolation (``pytest tests/test_webhook_inbound_events.py``).

What we cover (the spec's 18-test surface):

* Auth — missing, valid HMAC, valid bearer, wrong HMAC, revoked
  token, both-supplied (both must pass).
* Content-Type — non-JSON → 415.
* Body shape — malformed JSON, missing required fields, bad
  severity, lowercase severity normalisation.
* Dedup — external_id round-trip within 24h, expiry past 24h.
* Audit trail — an ``inbound_alert`` row is recorded on success.
* user_id stamping — bearer mode uses the token's owner; HMAC mode
  uses the WEBHOOK_INBOUND_USER_ID env override.
* Method routing — GET /events → 405.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer
from typing import Optional

import pytest
import requests

from worker import webhook_listener


# ─── HMAC test secret ────────────────────────────────────────────────────

_SECRET = "test-secret-inbound-events" * 2


def _sign(body: bytes, secret: str = _SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite isolation. Anything ``state.db.get_connection``
    touches lands in the tmp file so the audit log + alerts table +
    kv_state dedup rows don't leak across tests or to the real DB."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    # Bootstrap the schema by opening one connection.
    state_db.get_connection()
    yield
    state_db.reset_for_tests()


@pytest.fixture(autouse=True)
def reset_rate_limit():
    """Both rate-limit buckets (the per-user one in auth.rate_limit
    AND the per-IP one local to webhook_listener) must be empty at
    the start of every test — otherwise a previous test's burst
    leaks into the next one's budget."""
    from auth.rate_limit import clear_buckets
    clear_buckets()
    webhook_listener._clear_hmac_buckets()
    yield
    clear_buckets()
    webhook_listener._clear_hmac_buckets()


@pytest.fixture(autouse=True)
def set_webhook_secret(monkeypatch):
    """Every test runs against the same shared HMAC secret. Setting
    the env var (not the resolver) means the full
    os.environ → _get_secrets → handler path is exercised."""
    monkeypatch.setenv("WEBHOOK_SECRET", _SECRET)
    # Default to NO explicit inbound user — tests that want a
    # specific owner set it themselves.
    monkeypatch.delenv("WEBHOOK_INBOUND_USER_ID", raising=False)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    """Spin up the dispatching handler on an ephemeral port in a
    daemon thread; tear down in fixture teardown."""
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


# ─── Helpers ─────────────────────────────────────────────────────────────


def _make_user(username: str = "alice") -> str:
    """Sign up a user and return the user_id. The DB is isolated by
    the autouse fixture so each test gets a fresh user table.

    Role doesn't matter for /events bearer auth — the endpoint only
    looks up the token's owner, not the owner's role. Default 'user'
    is what an un-invited signup gets.
    """
    from auth.users import signup
    user = signup(username, "Correct-Horse-Battery-Staple-1!")
    assert user is not None, f"signup({username}) failed"
    return user.user_id


def _mint_token(user_id: str, label: str = "test-token") -> str:
    """Create an API token for ``user_id`` and return the raw secret."""
    from auth.tokens import create_token
    pair = create_token(user_id, label)
    assert pair is not None, f"create_token({user_id}) failed"
    _meta, raw = pair
    return raw


_VALID_PAYLOAD = {
    "alert_type": "EXTERNAL_DATADOG",
    "severity": "HIGH",
    "title": "DB connections > 90%",
    "body": "Connection pool nearly full",
    "ticker": "AAPL",
    "value": 92.5,
    "threshold": 90.0,
    "change_pct": 5.2,
}


def _post(server: str, payload: dict, *,
          headers: Optional[dict] = None,
          content_type: str = "application/json") -> requests.Response:
    """Wrap requests.post so every call uses the same defaults."""
    raw = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": content_type}
    if headers:
        hdrs.update(headers)
    return requests.post(
        f"{server}/events", data=raw, headers=hdrs, timeout=5,
    )


def _post_raw(server: str, body: bytes, *,
              headers: Optional[dict] = None,
              content_type: str = "application/json") -> requests.Response:
    hdrs = {"Content-Type": content_type}
    if headers:
        hdrs.update(headers)
    return requests.post(
        f"{server}/events", data=body, headers=hdrs, timeout=5,
    )


def _load_alert_rows() -> list:
    """Read every row from the alerts table — used to verify the
    persistence side-effect happened."""
    from state.db import get_connection
    conn = get_connection()
    return conn.execute(
        "SELECT alert_id, alert_type, severity, title, user_id "
        "FROM alerts ORDER BY created_at DESC"
    ).fetchall()


# ─── 1. Auth: missing credentials → 401 ──────────────────────────────────


def test_no_auth_returns_401(server) -> None:
    r = _post(server, _VALID_PAYLOAD)
    assert r.status_code == 401
    assert _load_alert_rows() == []


# ─── 2. Auth: valid HMAC → 201 + alert persisted ────────────────────────


def test_valid_hmac_creates_alert(server, monkeypatch) -> None:
    # HMAC mode uses WEBHOOK_INBOUND_USER_ID (or first admin). Set
    # the env so the test is deterministic without seeding users.
    monkeypatch.setenv("WEBHOOK_INBOUND_USER_ID", "hmac-owner-id")
    raw = json.dumps(_VALID_PAYLOAD).encode("utf-8")
    r = _post_raw(
        server, raw,
        headers={"X-Hub-Signature-256": "sha256=" + _sign(raw)},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "created"
    assert isinstance(body["alert_id"], str) and len(body["alert_id"]) > 8

    rows = _load_alert_rows()
    assert len(rows) == 1
    assert rows[0]["alert_id"] == body["alert_id"]
    assert rows[0]["alert_type"] == "EXTERNAL_DATADOG"
    assert rows[0]["severity"] == "HIGH"
    assert rows[0]["user_id"] == "hmac-owner-id"


# ─── 3. Auth: valid bearer → 201 + alert persisted with token user ─────


def test_valid_bearer_creates_alert_with_token_user_id(server) -> None:
    user_id = _make_user("bearer-alice")
    raw_token = _mint_token(user_id)

    r = _post(
        server, _VALID_PAYLOAD,
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "created"

    rows = _load_alert_rows()
    assert len(rows) == 1
    # The owning user_id MUST be the token's owner — not "", not the
    # HMAC fallback, not the wrong user.
    assert rows[0]["user_id"] == user_id


# ─── 4. Auth: wrong HMAC → 401, no alert ────────────────────────────────


def test_wrong_hmac_returns_401(server) -> None:
    raw = json.dumps(_VALID_PAYLOAD).encode("utf-8")
    r = _post_raw(
        server, raw,
        headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert r.status_code == 401
    assert _load_alert_rows() == []


# ─── 5. Auth: revoked token → 401, no alert ─────────────────────────────


def test_revoked_token_returns_401(server) -> None:
    user_id = _make_user("revoke-alice")
    raw_token = _mint_token(user_id)
    # Revoke it before posting.
    from auth.tokens import list_tokens, revoke_token
    tokens = list_tokens(user_id)
    assert len(tokens) == 1
    assert revoke_token(tokens[0].token_id, user_id=user_id) is True

    r = _post(
        server, _VALID_PAYLOAD,
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert r.status_code == 401
    assert _load_alert_rows() == []


# ─── 6. Content-Type != application/json → 415 ─────────────────────────


def test_wrong_content_type_returns_415(server) -> None:
    raw = json.dumps(_VALID_PAYLOAD).encode("utf-8")
    r = _post_raw(
        server, raw,
        headers={"X-Hub-Signature-256": "sha256=" + _sign(raw)},
        content_type="text/plain",
    )
    assert r.status_code == 415
    assert _load_alert_rows() == []


# ─── 7. Malformed JSON → 400 ───────────────────────────────────────────


def test_malformed_json_returns_400(server) -> None:
    bad = b"{not valid json"
    r = _post_raw(
        server, bad,
        headers={"X-Hub-Signature-256": "sha256=" + _sign(bad)},
    )
    assert r.status_code == 400
    assert _load_alert_rows() == []


# ─── 8. Missing alert_type → 400 ───────────────────────────────────────


def test_missing_alert_type_returns_400(server) -> None:
    payload = dict(_VALID_PAYLOAD)
    del payload["alert_type"]
    raw = json.dumps(payload).encode("utf-8")
    r = _post_raw(
        server, raw,
        headers={"X-Hub-Signature-256": "sha256=" + _sign(raw)},
    )
    assert r.status_code == 400
    assert "alert_type" in r.json().get("error", "")
    assert _load_alert_rows() == []


# ─── 9. Missing severity → 400 ─────────────────────────────────────────


def test_missing_severity_returns_400(server) -> None:
    payload = dict(_VALID_PAYLOAD)
    del payload["severity"]
    raw = json.dumps(payload).encode("utf-8")
    r = _post_raw(
        server, raw,
        headers={"X-Hub-Signature-256": "sha256=" + _sign(raw)},
    )
    assert r.status_code == 400
    assert "severity" in r.json().get("error", "")
    assert _load_alert_rows() == []


# ─── 10. Missing title → 400 ───────────────────────────────────────────


def test_missing_title_returns_400(server) -> None:
    payload = dict(_VALID_PAYLOAD)
    del payload["title"]
    raw = json.dumps(payload).encode("utf-8")
    r = _post_raw(
        server, raw,
        headers={"X-Hub-Signature-256": "sha256=" + _sign(raw)},
    )
    assert r.status_code == 400
    assert "title" in r.json().get("error", "")
    assert _load_alert_rows() == []


# ─── 11. Bad severity ('banana') → 400 ─────────────────────────────────


def test_invalid_severity_returns_400(server) -> None:
    payload = dict(_VALID_PAYLOAD, severity="banana")
    raw = json.dumps(payload).encode("utf-8")
    r = _post_raw(
        server, raw,
        headers={"X-Hub-Signature-256": "sha256=" + _sign(raw)},
    )
    assert r.status_code == 400
    err = r.json().get("error", "")
    assert "CRITICAL" in err and "LOW" in err
    assert _load_alert_rows() == []


# ─── 12. Lowercase severity → 201, normalised to upper ─────────────────


def test_lowercase_severity_normalized_and_persisted(
    server, monkeypatch,
) -> None:
    monkeypatch.setenv("WEBHOOK_INBOUND_USER_ID", "norm-owner-id")
    payload = dict(_VALID_PAYLOAD, severity="high")
    raw = json.dumps(payload).encode("utf-8")
    r = _post_raw(
        server, raw,
        headers={"X-Hub-Signature-256": "sha256=" + _sign(raw)},
    )
    assert r.status_code == 201, r.text
    rows = _load_alert_rows()
    assert len(rows) == 1
    assert rows[0]["severity"] == "HIGH"


# ─── 13. external_id round-trip: second POST dedupes ───────────────────


def test_external_id_round_trip_dedupes(server, monkeypatch) -> None:
    monkeypatch.setenv("WEBHOOK_INBOUND_USER_ID", "dedup-owner-id")
    payload = dict(_VALID_PAYLOAD, external_id="datadog-event-12345")
    raw = json.dumps(payload).encode("utf-8")
    headers = {"X-Hub-Signature-256": "sha256=" + _sign(raw)}

    r1 = _post_raw(server, raw, headers=headers)
    assert r1.status_code == 201, r1.text
    alert_id_1 = r1.json()["alert_id"]

    r2 = _post_raw(server, raw, headers=headers)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == "deduped"
    assert body["alert_id"] == alert_id_1

    # Exactly ONE alert row total — the dedup path did NOT call save.
    assert len(_load_alert_rows()) == 1


# ─── 14. external_id older than 24h → fresh alert created ─────────────


def test_external_id_expiry_creates_new_alert(server, monkeypatch) -> None:
    monkeypatch.setenv("WEBHOOK_INBOUND_USER_ID", "expiry-owner-id")
    external_id = "datadog-event-stale"
    payload = dict(_VALID_PAYLOAD, external_id=external_id)
    raw = json.dumps(payload).encode("utf-8")
    headers = {"X-Hub-Signature-256": "sha256=" + _sign(raw)}

    # Hand-write a stale kv_state row (created 30h ago — past the 24h
    # window). Mirrors what the live handler would have written but
    # with a back-dated timestamp.
    from state.db import get_connection
    stale_ts = (
        datetime.now(timezone.utc) - timedelta(hours=30)
    ).isoformat()
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (
                webhook_listener._external_id_kv_key(external_id),
                json.dumps(
                    {"alert_id": "stale-alert-uuid",
                     "created_at": stale_ts},
                ),
                stale_ts,
            ),
        )

    r = _post_raw(server, raw, headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "created"
    assert body["alert_id"] != "stale-alert-uuid"

    # One row in the alerts table — the brand-new one. The stale
    # mapping in kv_state never produced an alerts row in the first
    # place (we hand-inserted only the kv_state entry).
    rows = _load_alert_rows()
    assert len(rows) == 1
    assert rows[0]["alert_id"] == body["alert_id"]


# ─── 15. HMAC + bearer both supplied: BOTH must pass ──────────────────


def test_hmac_and_bearer_both_required_when_both_present(server) -> None:
    user_id = _make_user("both-alice")
    raw_token = _mint_token(user_id)
    raw = json.dumps(_VALID_PAYLOAD).encode("utf-8")
    good_sig = "sha256=" + _sign(raw)
    bad_sig = "sha256=" + "0" * 64

    # 15a. Good bearer + BAD HMAC → 401 (HMAC must validate too).
    r = _post_raw(
        server, raw,
        headers={
            "Authorization": f"Bearer {raw_token}",
            "X-Hub-Signature-256": bad_sig,
        },
    )
    assert r.status_code == 401
    assert _load_alert_rows() == []

    # 15b. Good HMAC + BAD bearer → 401 (bearer must validate too).
    r = _post_raw(
        server, raw,
        headers={
            "Authorization": "Bearer not-a-real-token-aaaaaaaaaa",
            "X-Hub-Signature-256": good_sig,
        },
    )
    assert r.status_code == 401
    assert _load_alert_rows() == []

    # 15c. Good HMAC + Good bearer → 201, bearer's user_id wins.
    r = _post_raw(
        server, raw,
        headers={
            "Authorization": f"Bearer {raw_token}",
            "X-Hub-Signature-256": good_sig,
        },
    )
    assert r.status_code == 201, r.text
    rows = _load_alert_rows()
    assert len(rows) == 1
    assert rows[0]["user_id"] == user_id


# ─── 16. Audit event 'inbound_alert' is recorded ──────────────────────


def test_inbound_alert_audit_event_recorded(server, monkeypatch) -> None:
    monkeypatch.setenv("WEBHOOK_INBOUND_USER_ID", "audit-owner-id")
    raw = json.dumps(_VALID_PAYLOAD).encode("utf-8")
    r = _post_raw(
        server, raw,
        headers={"X-Hub-Signature-256": "sha256=" + _sign(raw)},
    )
    assert r.status_code == 201, r.text
    alert_id = r.json()["alert_id"]

    from auth.audit import query_audit
    events = query_audit(action="inbound_alert", limit=10)
    assert len(events) == 1
    ev = events[0]
    assert ev.action == "inbound_alert"
    assert ev.entity_type == "alert"
    assert ev.entity_id == alert_id
    assert ev.user_id == "audit-owner-id"
    assert ev.detail_json.get("auth_method") == "hmac"
    assert ev.detail_json.get("severity") == "HIGH"
    assert ev.detail_json.get("alert_type") == "EXTERNAL_DATADOG"


# ─── 17. user_id is set per auth method ───────────────────────────────


def test_user_id_resolution_per_auth_method(server, monkeypatch) -> None:
    # Two distinct posts: one HMAC (uses env override), one bearer
    # (uses token owner). They MUST land on different user_ids.
    monkeypatch.setenv("WEBHOOK_INBOUND_USER_ID", "the-hmac-user")

    raw1 = json.dumps(dict(_VALID_PAYLOAD, title="from hmac")).encode(
        "utf-8"
    )
    r1 = _post_raw(
        server, raw1,
        headers={"X-Hub-Signature-256": "sha256=" + _sign(raw1)},
    )
    assert r1.status_code == 201, r1.text

    user_id = _make_user("bearer-user-id-target")
    raw_token = _mint_token(user_id)
    raw2 = json.dumps(dict(_VALID_PAYLOAD, title="from bearer")).encode(
        "utf-8"
    )
    r2 = _post(
        server, dict(_VALID_PAYLOAD, title="from bearer"),
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert r2.status_code == 201, r2.text

    rows = _load_alert_rows()
    # Newest-first ordering — bearer post was second so it's row [0].
    assert len(rows) == 2
    bearer_row = next(r for r in rows if r["title"] == "from bearer")
    hmac_row = next(r for r in rows if r["title"] == "from hmac")
    assert bearer_row["user_id"] == user_id
    assert hmac_row["user_id"] == "the-hmac-user"
    assert bearer_row["user_id"] != hmac_row["user_id"]


# ─── 18. GET /events → 405 ────────────────────────────────────────────


def test_get_events_returns_405(server) -> None:
    r = requests.get(f"{server}/events", timeout=5)
    assert r.status_code == 405
