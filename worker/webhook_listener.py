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
  current secret comes from the ``WEBHOOK_SECRET`` env variable. During a
  rotation window an operator can set ``WEBHOOK_SECRET_PREVIOUS`` to the
  old value — both then verify, exhausting the loop so timing doesn't
  reveal which one matched. ``hmac.compare_digest`` gives us constant-time
  comparison so we don't leak timing info. See ``_get_secrets`` and
  ``docs/DEPLOYMENT.md`` for the rotation playbook.
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
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

from loguru import logger


# Wall-clock anchor captured at module load. ``GET /health`` uses this
# to expose process uptime. Module-level rather than per-handler because
# every handler instance is created per-request — only a module global
# survives across requests.
_START_TIME: float = time.time()


# ─────────────────────────────────────────────────────────────────────────────
#  HMAC verification
# ─────────────────────────────────────────────────────────────────────────────

def _verify_hmac(body: bytes, signature: str, secrets) -> bool:
    """Polymorphic in ``secrets``: accepts either a ``str`` (legacy
    single-secret callers + existing tests) OR a ``list[str]`` (the
    two-secret rotation window). Internally normalizes to a list so
    the constant-time loop below is identical for both shapes.
    """
    if isinstance(secrets, str):
        secrets = [secrets] if secrets else []
    # Constant-time HMAC SHA256 verification against a LIST of secrets.
    #
    # Returns True iff ANY secret in ``secrets`` produces a digest that
    # matches ``signature`` via ``hmac.compare_digest``. The loop is
    # deliberately exhausted on EVERY call — we OR the per-secret result
    # into a single boolean accumulator rather than ``return True`` on
    # the first match. That keeps the wall-clock cost of a verification
    # constant in the number of configured secrets so timing doesn't
    # leak which secret matched (or even WHETHER one matched, modulo the
    # fixed work).
    #
    # The expected signature is the lowercase hex digest of
    # HMAC-SHA256(secret, body). We allow a "sha256=" prefix (GitHub /
    # Stripe convention) so callers using either convention work
    # without a per-source code path.
    #
    # The list shape is what enables the two-secret rotation window:
    # operators set the new value as WEBHOOK_SECRET and the old value
    # as WEBHOOK_SECRET_PREVIOUS during the transition, and requests
    # signed with EITHER verify. Once external systems have migrated,
    # dropping WEBHOOK_SECRET_PREVIOUS collapses back to single-secret
    # behaviour. See _get_secrets for the env-var resolution rules.
    #
    # Empty signature / empty secrets list / empty body-without-signature
    # all fail closed — never authenticate a request that hasn't carried
    # an explicit signature.
    if not signature or not secrets:
        return False
    # Strip the common ``sha256=`` prefix if the caller included it.
    if signature.lower().startswith("sha256="):
        signature = signature.split("=", 1)[1]
    provided = signature.lower()
    body_bytes = bytes(body)
    # Accumulate the OR into a single boolean. We MUST NOT short-circuit
    # on a match — exhausting the loop keeps the verification time
    # constant across the list and prevents a timing oracle from
    # distinguishing "matched the first secret" from "matched the
    # second" from "matched neither".
    matched = False
    for secret in secrets:
        if not secret:
            # Skip empties but still consume the loop iteration. An
            # empty secret can never produce a valid digest anyway.
            continue
        expected = hmac.new(
            secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()
        # hmac.compare_digest is constant-time and length-safe.
        # ``|=`` accumulates — never ``return True`` inside the loop.
        matched |= hmac.compare_digest(expected, provided)
    return matched


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


def _get_secrets() -> list[str]:
    """Resolve the active HMAC secrets from env, in verification order.

    Returns:
        * ``[WEBHOOK_SECRET]`` — when only the current secret is set
          (the steady-state, no-rotation path).
        * ``[WEBHOOK_SECRET, WEBHOOK_SECRET_PREVIOUS]`` — when both are
          set (rotation in progress). The CURRENT secret is tried
          first so the common case in a rotation window is still a
          first-pass match.
        * ``[]`` — when neither env var carries a non-empty value.
          The handler converts this to a 401 the same way an empty
          single secret used to.

    Empty / whitespace-only values are filtered. An accidentally-set
    but blank ``WEBHOOK_SECRET_PREVIOUS=`` does not count as "rotation
    is in progress" — it just collapses back to single-secret mode.

    A misconfigured deployment with ONLY ``WEBHOOK_SECRET_PREVIOUS``
    set (and ``WEBHOOK_SECRET`` empty) is intentionally rejected: the
    function returns ``[]`` so every request 401s. Allowing "previous
    only" would let an operator finish a rotation by deleting the
    NEW secret instead of the OLD one — exactly the wrong direction.
    """
    current = (os.environ.get("WEBHOOK_SECRET", "") or "").strip()
    previous = (os.environ.get("WEBHOOK_SECRET_PREVIOUS", "") or "").strip()
    if not current:
        # Without a CURRENT secret there's nothing to rotate INTO.
        # Treat the whole listener as unconfigured — fail closed.
        return []
    if previous:
        return [current, previous]
    return [current]


# ─────────────────────────────────────────────────────────────────────────────
#  Dispatching handler — single class so HTTPServer can hand every
#  request to the same callable and we route inside on path.
# ─────────────────────────────────────────────────────────────────────────────

class _DispatchHandler(BaseHTTPRequestHandler):
    """Routes by request path:

      POST /ack/{alert_id}        → AckWebhookHandler logic
      POST /ack-all               → AckWebhookHandler logic
      POST /webhooks/pagerduty    → PagerDutyEventHandler logic
      GET  /health                → _handle_health (public, no HMAC)
      POST other                  → 404
      GET  other                  → 404
      PUT/DELETE/PATCH/HEAD       → 405

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

    # Block every other method with 405. GET is split out below — the
    # /health liveness probe is the only allowed GET path, every other
    # GET is a 404. PUT / DELETE / PATCH / HEAD remain blanket 405s so
    # operators don't think they can scrape state from this listener.
    def _method_not_allowed(self) -> None:
        _send_json(self, HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        try:
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/health":
                self._handle_health()
                return
            # Any other GET path — 404, NOT 405. /health is the only
            # GET surface; everything else is just an unknown resource.
            _send_json(self, HTTPStatus.NOT_FOUND, {"error": "unknown path"})
        except Exception as exc:
            logger.exception(f"webhook GET handler crashed: {exc}")
            try:
                _send_json(self, HTTPStatus.INTERNAL_SERVER_ERROR,
                           {"error": "internal server error"})
            except Exception:
                pass

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_HEAD(self) -> None:  # noqa: N802
        self._method_not_allowed()

    # ── Endpoint: GET /health ──────────────────────────────────────

    def _handle_health(self) -> None:
        """Liveness + light system-health probe.

        Public on purpose — no HMAC. This is the endpoint Docker /
        load balancers / k8s readiness probes hit, and forcing a
        signed request on those callers would mean every operator
        ships a signing helper alongside the probe. The response
        carries no secrets; it's the same shape an unauthenticated
        observer could derive from public outputs anyway.

        Response keys:
            status                       'ok' | 'degraded' | 'down'
            schema_version               state.db.SCHEMA_VERSION
            users                        auth.users.count_users()
            now_utc                      ISO timestamp (UTC)
            up_seconds                   process uptime
            unacked_critical_count       count of unacked CRITICAL alerts (30d)
            recent_render_success_rate   from get_perf_summary(1h) or None
            current_outages              from get_health_summary() or []

        Status semantics:
            down      — count_users raised OR returned -1 (DB unreadable)
            degraded  — unacked_critical_count > 0
                        OR recent_render_success_rate < 0.95
                        OR current_outages non-empty
            ok        — none of the above

        HTTP status:
            200 — status in {'ok', 'degraded'} (load balancers can keep
                  the container in rotation; degraded is informational)
            503 — status == 'down' (LB should pull this instance out)

        Each underlying telemetry call sits in its own try/except so a
        single failing layer (e.g. perf telemetry tables not yet
        created) doesn't cascade into a 503. Only ``count_users``
        failure flips status to 'down', because count_users IS our
        proxy for "can we open the DB at all".
        """
        # The handler body is itself wrapped so a fully unexpected
        # exception (e.g. JSON-encode failure) still produces a 503
        # rather than a wire-level 500. The outer logger.exception
        # path is the last-resort fallback shared with do_POST.
        try:
            # ── DB liveness probe ────────────────────────────────
            # count_users itself swallows exceptions and returns 0 on
            # error, but in case a future refactor changes that contract
            # we still wrap it here. We also flag -1 as a DB-down
            # sentinel because the spec asks for it explicitly: if a
            # future variant uses -1 to distinguish "DB error" from
            # "no rows", health should treat that as down.
            db_down: bool = False
            db_error: str = ""
            users_n: int = 0
            try:
                from auth.users import count_users
                users_n = count_users()
                if users_n is None or users_n == -1:
                    db_down = True
                    db_error = "count_users returned -1"
            except Exception as exc:  # noqa: BLE001
                db_down = True
                db_error = f"{type(exc).__name__}: {exc}"
                logger.warning(f"webhook /health: count_users failed: {exc}")

            if db_down:
                _send_json(
                    self,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"status": "down", "error": db_error},
                )
                return

            # ── Schema version ───────────────────────────────────
            schema_version: int = 0
            try:
                from state.db import SCHEMA_VERSION
                schema_version = int(SCHEMA_VERSION)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"webhook /health: SCHEMA_VERSION read failed: {exc}")

            # ── Unacked CRITICAL count (last 30d) ─────────────────
            # Per spec — surfaces "we still owe someone an ack" as a
            # degraded signal. load_alerts already defaults to 30d.
            unacked_critical: int = 0
            try:
                from engine.alert_engine_v2 import load_alerts
                alerts = load_alerts(max_age_days=30)
                unacked_critical = sum(
                    1 for a in alerts
                    if getattr(a, "severity", "") == "CRITICAL"
                    and not getattr(a, "acknowledged", False)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"webhook /health: load_alerts failed: {exc}")

            # ── Recent render success rate (last hour) ───────────
            # None when the telemetry layer is empty / unconfigured —
            # we explicitly distinguish "no data" from 0.0 so health
            # doesn't degrade on a fresh deploy that hasn't rendered
            # anything yet.
            recent_success_rate: Optional[float] = None
            try:
                from engine.perf_telemetry import get_perf_summary
                perf = get_perf_summary(window_hours=1) or {}
                if perf.get("total_renders", 0) > 0:
                    raw = perf.get("success_rate")
                    if raw is not None:
                        recent_success_rate = float(raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"webhook /health: get_perf_summary failed: {exc}")

            # ── Current source-health outages ────────────────────
            current_outages: list = []
            try:
                from engine.source_health import get_health_summary
                health = get_health_summary() or {}
                outages = health.get("current_outages", [])
                if isinstance(outages, list):
                    current_outages = list(outages)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"webhook /health: get_health_summary failed: {exc}")

            # ── Aggregate to a single status verdict ─────────────
            degraded = (
                unacked_critical > 0
                or (recent_success_rate is not None and recent_success_rate < 0.95)
                or bool(current_outages)
            )
            status = "degraded" if degraded else "ok"

            payload = {
                "status": status,
                "schema_version": schema_version,
                "users": users_n,
                "now_utc": datetime.now(timezone.utc).isoformat(),
                "up_seconds": round(time.time() - _START_TIME, 3),
                "unacked_critical_count": unacked_critical,
                "recent_render_success_rate": recent_success_rate,
                "current_outages": current_outages,
            }
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            # Anything truly unexpected — JSON encode error, etc. We
            # surface it as 503/down so probes pull the container OUT
            # of rotation rather than treat it as healthy.
            logger.exception(f"webhook /health crashed: {exc}")
            try:
                _send_json(
                    self,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"status": "down", "error": f"{type(exc).__name__}: {exc}"},
                )
            except Exception:
                pass

    # ── Endpoint: POST /ack/{alert_id} ─────────────────────────────

    def _handle_ack_one(self, alert_id: str) -> None:
        try:
            body = _read_body(self)
            sig = self.headers.get("X-Signature-SHA256", "")
            if not _verify_hmac(body, sig, _get_secrets()):
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
            if not _verify_hmac(body, sig, _get_secrets()):
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
            if not _verify_hmac(body, sig, _get_secrets()):
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

    active_secrets = _get_secrets()
    if not active_secrets:
        logger.warning(
            "WEBHOOK_SECRET is unset — every request will fail HMAC "
            "verification with 401. Set WEBHOOK_SECRET in .env before "
            "going live."
        )
    elif len(active_secrets) > 1:
        # Two-secret window — the operator is mid-rotation. Surface it
        # in the log so a forgotten ``WEBHOOK_SECRET_PREVIOUS`` doesn't
        # silently linger past the transition.
        logger.info(
            "webhook listener running with TWO active secrets "
            "(WEBHOOK_SECRET + WEBHOOK_SECRET_PREVIOUS). Drop "
            "WEBHOOK_SECRET_PREVIOUS once external systems have "
            "migrated to the new secret."
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
