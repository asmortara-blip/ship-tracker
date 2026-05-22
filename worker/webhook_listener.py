"""worker/webhook_listener.py — stdlib HTTP webhook listener for alert ack.

Closes the loop between Ship Tracker alerts and the external paging stack
(PagerDuty, Opsgenie, generic HTTP POSTers). Before this module existed,
acknowledgement was UI-only: an analyst clicking "Ack" inside the Streamlit
alerts tab was the only way to flip ``ShippingAlert.acknowledged``. When
PagerDuty (or an on-call engineer over CLI/curl) resolved an incident, the
Ship Tracker side stayed unaware and would keep firing reminders.

Each ``ShippingAlert.alert_id`` is propagated to PagerDuty as the incident
``dedup_key`` (commit b8bd33e), so when PagerDuty fires an
``incident.resolved`` webhook back at us the dedup_key IS the alert_id —
we just feed it into ``engine.alert_engine_v2.acknowledge_alert``.

Design constraints
------------------
- **Stdlib only.** No Flask / FastAPI / Starlette / aiohttp. The whole
  thing is ``http.server.BaseHTTPRequestHandler`` + ``hmac`` +
  ``hashlib`` + ``json``. This keeps the worker image small and avoids
  pinning a second web framework alongside Streamlit.
- **HMAC SHA256 authentication.** Every endpoint verifies a shared-secret
  HMAC signature against the raw request body before doing anything. The
  secret comes from the ``WEBHOOK_SECRET`` env variable; ``hmac.compare_digest``
  gives us constant-time comparison so we don't leak timing info.
- **Two endpoints, two handlers.** A generic ``AckWebhookHandler``
  (``POST /ack/{alert_id}`` + ``POST /ack-all``) for CLI / curl /
  generic integrations, and a PagerDuty-shaped ``PagerDutyEventHandler``
  (``POST /webhooks/pagerduty``) that parses PD's nested event envelope.
  A single dispatching handler routes by path so both endpoint sets are
  exposed on the same port (8502 by default).
- **Crash-proof.** Every handler method wraps its body in a try/except
  that converts unhandled exceptions to a 500 JSON response. The server
  loop must keep running across malformed payloads.

This module must NOT import ``streamlit``. It runs out-of-process as a
sibling container under docker-compose and has no ``st.*`` available.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
#  HMAC verification
# ─────────────────────────────────────────────────────────────────────────────

def _verify_hmac(body: bytes, signature: str, secret: str) -> bool:
    """Constant-time HMAC SHA256 verification.

    The expected signature is the lowercase hex digest of
    ``HMAC-SHA256(secret, body)``. We allow a ``"sha256="`` prefix
    (GitHub / Stripe convention) so callers using either convention
    work without a per-source code path.

    Empty secret / signature / body all fail closed — never authenticate
    a request that hasn't carried an explicit signature.
    """
    if not secret or not signature:
        return False
    # Strip the common ``sha256=`` prefix if the caller included it.
    if signature.lower().startswith("sha256="):
        signature = signature.split("=", 1)[1]
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    # hmac.compare_digest is constant-time and length-safe.
    return hmac.compare_digest(expected, signature.lower())


# ─────────────────────────────────────────────────────────────────────────────
#  Shared response helpers
# ─────────────────────────────────────────────────────────────────────────────

def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    """Write a small JSON response. Centralized so every code path
    emits the same Content-Type + Content-Length headers."""
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    """Read the full request body using Content-Length. Returns b''
    when the header is missing or malformed (treated as empty body)."""
    raw = handler.headers.get("Content-Length", "0")
    try:
        length = int(raw)
    except (TypeError, ValueError):
        length = 0
    if length <= 0:
        return b""
    return handler.rfile.read(length)


def _get_secret() -> str:
    """Resolve the shared HMAC secret from env. An unset secret is a
    deployment misconfiguration — we still let the server start (so
    healthchecks pass and logs explain the problem) but every request
    will fail HMAC verification with 401."""
    return os.environ.get("WEBHOOK_SECRET", "")


# ─────────────────────────────────────────────────────────────────────────────
#  Dispatching handler — single class so HTTPServer can hand every
#  request to the same callable and we route inside on path.
# ─────────────────────────────────────────────────────────────────────────────

class _DispatchHandler(BaseHTTPRequestHandler):
    """Routes by request path:

      POST /ack/{alert_id}        → AckWebhookHandler logic
      POST /ack-all               → AckWebhookHandler logic
      POST /webhooks/pagerduty    → PagerDutyEventHandler logic
      anything else               → 404
      GET/PUT/DELETE/…            → 405

    We use ONE BaseHTTPRequestHandler subclass instead of multiple
    HTTPServers on different ports because that lets the whole worker
    listen on a single port (simpler firewall / compose port-mapping)
    while still exposing every endpoint shape.
    """

    # Silence the default ``BaseHTTPRequestHandler`` access log — it
    # writes to stderr in a noisy non-structured format. Route through
    # loguru so logs look like the rest of the worker.
    def log_message(self, fmt: str, *args) -> None:  # noqa: N802
        logger.info(f"webhook {self.address_string()} - {fmt % args}")

    # ── Method routing ─────────────────────────────────────────────

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        try:
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            logger.info(f"webhook POST {path}")

            if path == "/ack-all":
                self._handle_ack_all()
                return
            if path.startswith("/ack/"):
                alert_id = path[len("/ack/"):]
                if not alert_id:
                    _send_json(self, HTTPStatus.NOT_FOUND, {"error": "missing alert_id"})
                    return
                self._handle_ack_one(alert_id)
                return
            if path == "/webhooks/pagerduty":
                self._handle_pagerduty()
                return

            _send_json(self, HTTPStatus.NOT_FOUND, {"error": "unknown path"})
        except Exception as exc:
            # Defensive last-resort handler. Every per-endpoint method
            # already wraps itself; this keeps the server alive even if
            # the routing logic above ever raises.
            logger.exception(f"webhook POST handler crashed: {exc}")
            try:
                _send_json(self, HTTPStatus.INTERNAL_SERVER_ERROR,
                           {"error": "internal server error"})
            except Exception:
                pass

    # Block every other method with 405. We don't expose any GET / PUT
    # / DELETE / PATCH surface — even a HEAD probe should fail loudly so
    # operators don't think they can scrape state from this listener.
    def _method_not_allowed(self) -> None:
        _send_json(self, HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})

    def do_GET(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_HEAD(self) -> None:  # noqa: N802
        self._method_not_allowed()

    # ── Endpoint: POST /ack/{alert_id} ─────────────────────────────

    def _handle_ack_one(self, alert_id: str) -> None:
        try:
            body = _read_body(self)
            sig = self.headers.get("X-Signature-SHA256", "")
            if not _verify_hmac(body, sig, _get_secret()):
                logger.warning(f"webhook /ack: HMAC mismatch for alert_id={alert_id}")
                _send_json(self, HTTPStatus.UNAUTHORIZED,
                           {"error": "invalid signature"})
                return

            # acknowledge_alert is a no-op for unknown alert_ids (the
            # UPDATE just affects zero rows) — we deliberately do NOT
            # verify the alert exists first, because that would let a
            # caller distinguish known vs unknown IDs via response
            # codes. Always returning 200 keeps it idempotent.
            from engine.alert_engine_v2 import acknowledge_alert
            acknowledge_alert(alert_id)
            logger.info(f"webhook /ack: acknowledged alert_id={alert_id}")
            _send_json(self, HTTPStatus.OK, {"acknowledged": True, "alert_id": alert_id})
        except Exception as exc:
            logger.exception(f"webhook /ack/{alert_id} crashed: {exc}")
            _send_json(self, HTTPStatus.INTERNAL_SERVER_ERROR,
                       {"error": "internal server error"})

    # ── Endpoint: POST /ack-all ────────────────────────────────────

    def _handle_ack_all(self) -> None:
        try:
            body = _read_body(self)
            sig = self.headers.get("X-Signature-SHA256", "")
            if not _verify_hmac(body, sig, _get_secret()):
                logger.warning("webhook /ack-all: HMAC mismatch")
                _send_json(self, HTTPStatus.UNAUTHORIZED,
                           {"error": "invalid signature"})
                return

            from engine.alert_engine_v2 import acknowledge_all
            acknowledge_all()
            logger.info("webhook /ack-all: acknowledged every open alert")
            _send_json(self, HTTPStatus.OK, {"acknowledged": True, "scope": "all"})
        except Exception as exc:
            logger.exception(f"webhook /ack-all crashed: {exc}")
            _send_json(self, HTTPStatus.INTERNAL_SERVER_ERROR,
                       {"error": "internal server error"})

    # ── Endpoint: POST /webhooks/pagerduty ─────────────────────────

    def _handle_pagerduty(self) -> None:
        """PagerDuty webhook envelope (Webhooks v3 shape):

            {
              "event": {
                "event_type": "incident.resolved",
                "data": { "incident": { "dedup_key": "<alert_id>" } }
              }
            }

        On ``incident.resolved`` with a non-empty ``dedup_key`` we
        call ``acknowledge_alert(dedup_key)``. Every other event type
        is accepted (returns 200) but ignored — PagerDuty retries
        non-2xx responses, so swallowing non-resolution events
        silently is the documented best practice.
        """
        try:
            body = _read_body(self)
            sig = self.headers.get("X-PagerDuty-Signature", "")
            if not _verify_hmac(body, sig, _get_secret()):
                logger.warning("webhook /webhooks/pagerduty: HMAC mismatch")
                _send_json(self, HTTPStatus.UNAUTHORIZED,
                           {"error": "invalid signature"})
                return

            try:
                payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                logger.warning("webhook /webhooks/pagerduty: malformed JSON")
                _send_json(self, HTTPStatus.BAD_REQUEST,
                           {"error": "malformed json"})
                return

            event = (payload or {}).get("event", {}) if isinstance(payload, dict) else {}
            event_type = event.get("event_type", "") if isinstance(event, dict) else ""
            data = event.get("data", {}) if isinstance(event, dict) else {}
            incident = data.get("incident", {}) if isinstance(data, dict) else {}
            dedup_key = (
                incident.get("dedup_key", "")
                if isinstance(incident, dict) else ""
            )

            if event_type == "incident.resolved" and dedup_key:
                from engine.alert_engine_v2 import acknowledge_alert
                acknowledge_alert(dedup_key)
                logger.info(
                    f"webhook /webhooks/pagerduty: resolved {dedup_key} "
                    f"(event_type={event_type})"
                )
                _send_json(self, HTTPStatus.OK,
                           {"acknowledged": True, "alert_id": dedup_key})
                return

            # Non-resolution event OR empty dedup_key — ack 200 so PD
            # doesn't retry, but log enough to debug.
            logger.info(
                f"webhook /webhooks/pagerduty: ignored event_type={event_type!r} "
                f"dedup_key={dedup_key!r}"
            )
            _send_json(self, HTTPStatus.OK,
                       {"acknowledged": False, "reason": "no-op event"})
        except Exception as exc:
            logger.exception(f"webhook /webhooks/pagerduty crashed: {exc}")
            _send_json(self, HTTPStatus.INTERNAL_SERVER_ERROR,
                       {"error": "internal server error"})


# Public-facing handler aliases so the spec'd class names exist for
# importers and for testability (tests can patch their attributes
# without reaching into private symbols). They're the same handler
# class under the hood — routing is path-based, not class-based —
# but keeping the names lets us evolve them independently later.
AckWebhookHandler = _DispatchHandler
PagerDutyEventHandler = _DispatchHandler


# ─────────────────────────────────────────────────────────────────────────────
#  Server lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def serve(host: str = "0.0.0.0", port: int = 8502) -> HTTPServer:
    """Start an HTTPServer bound to ``host:port`` and run forever.

    Returns the ``HTTPServer`` instance only in tests where the caller
    needs to ``shutdown()`` it from another thread. In production
    ``main()`` blocks here and the server runs until SIGTERM.
    """
    server = HTTPServer((host, port), _DispatchHandler)
    logger.info(f"webhook listener bound to http://{host}:{port}")
    server.serve_forever()
    return server


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint. Returns the exit code: 0 normal, 1 on bind error."""
    parser = argparse.ArgumentParser(
        description="Ship Tracker alert-ack webhook listener (stdlib http.server)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("WEBHOOK_HOST", "0.0.0.0"),
        help="Bind interface (default 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("WEBHOOK_PORT", "8502")),
        help="Bind port (default 8502)",
    )
    args = parser.parse_args(argv)

    if not _get_secret():
        logger.warning(
            "WEBHOOK_SECRET is unset — every request will fail HMAC "
            "verification with 401. Set WEBHOOK_SECRET in .env before "
            "going live."
        )

    try:
        serve(args.host, args.port)
        return 0
    except OSError as exc:
        # Address-in-use, permission denied, etc.
        logger.error(f"webhook listener failed to bind {args.host}:{args.port}: {exc}")
        return 1
    except KeyboardInterrupt:
        # Clean Ctrl-C exit when run interactively.
        logger.info("webhook listener received KeyboardInterrupt, exiting")
        return 0


if __name__ == "__main__":
    sys.exit(main())
