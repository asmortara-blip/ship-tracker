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
import uuid
from datetime import datetime, timedelta, timezone
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


# Hard cap on the request body we will read into memory. Webhook payloads
# (ack confirmations, PagerDuty events, alert events) are a few KB at most;
# 1 MiB is enormous headroom. The cap stops an unauthenticated client from
# declaring (or sending) a huge Content-Length and forcing a giant allocation
# on the single-threaded listener. The companion defense is the per-request
# socket timeout on _DispatchHandler, which bounds a STALLED/withheld body so
# rfile.read can't block the one thread forever.
MAX_BODY_BYTES = 1 * 1024 * 1024


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    """Read the request body using Content-Length. Returns b'' when the
    header is missing, malformed, or declares more than ``MAX_BODY_BYTES``
    (the oversize case is logged); callers treat b'' as an empty/invalid body
    and reject it. Reading is reached only AFTER this bound, and the handler's
    socket timeout guards against a body that is declared but never sent."""
    raw = handler.headers.get("Content-Length", "0")
    try:
        length = int(raw)
    except (TypeError, ValueError):
        length = 0
    if length <= 0:
        return b""
    if length > MAX_BODY_BYTES:
        logger.warning(
            f"webhook: rejecting oversized request body "
            f"Content-Length={length} (cap {MAX_BODY_BYTES})"
        )
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
#  /events — inbound alert ingestion from external monitoring tools
# ─────────────────────────────────────────────────────────────────────────────
#
# The /ack* endpoints close the OUTBOUND loop (Ship Tracker fired the
# alert, PagerDuty resolved it, we ack it back). /events closes the
# INBOUND loop: an external monitoring tool (Datadog, Sentry, Grafana,
# a custom shell script) wants to create a fresh ShippingAlert in
# Ship Tracker so the analyst sees a unified alerts surface.
#
# Auth: BOTH X-Hub-Signature-256 (HMAC, same shared secret as the rest
# of this listener) and Authorization: Bearer (a real auth.tokens row)
# are accepted. At least one must validate; if BOTH headers are
# present, BOTH must validate (no fallback). The bearer mode resolves
# a user_id; the HMAC mode falls back to WEBHOOK_INBOUND_USER_ID or
# the oldest admin if that env var is unset.

# Allowed severity values, upper-cased and validated against the
# normalized incoming value. Mirrors engine.alert_engine_v2's vocab.
_ALLOWED_SEVERITIES: frozenset[str] = frozenset(
    {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
)


# Dedup window for external_id replay. 24h is long enough to absorb a
# monitoring tool retrying through an outage (Datadog will keep
# retrying a webhook for hours), short enough that a recurring event
# fired the next day creates a fresh alert as expected.
_EXTERNAL_ID_DEDUP_HOURS: int = 24


def _external_id_kv_key(external_id: str) -> str:
    """kv_state row key for the external_id → alert_id mapping."""
    return f"external_alert_id:{external_id}"


def _read_external_id_alert(external_id: str) -> Optional[tuple[str, str]]:
    """Look up a previously-stored ``(alert_id, created_at_iso)`` pair
    for ``external_id``. Returns ``None`` when:

    * the kv_state row is missing,
    * the JSON does not parse,
    * the stored ``created_at`` is older than the dedup window.

    NEVER raises — a broken kv_state read must NOT block alert
    creation. On any failure the caller proceeds as if no dedup row
    existed (worst case: a duplicate alert lands, the second POST
    looks like a first POST).
    """
    if not external_id:
        return None
    try:
        from state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_external_id_kv_key(external_id),),
        ).fetchone()
        if row is None:
            return None
        raw = row["value"] if hasattr(row, "keys") else row[0]
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        alert_id = data.get("alert_id", "")
        created_at = data.get("created_at", "")
        if not isinstance(alert_id, str) or not alert_id:
            return None
        if not isinstance(created_at, str) or not created_at:
            return None
        # Age check — anything past the dedup window counts as "expired"
        # so a recurring monitor event on day N+1 lands as a fresh
        # alert. Tolerate timezone-naive timestamps by anchoring to UTC.
        try:
            ts = datetime.fromisoformat(created_at)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=_EXTERNAL_ID_DEDUP_HOURS
        )
        if ts < cutoff:
            return None
        return alert_id, created_at
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"webhook /events: kv_state lookup failed for "
            f"external_id={external_id!r}: {exc}"
        )
        return None


def _store_external_id_alert(external_id: str, alert_id: str,
                             created_at: str) -> None:
    """Persist the external_id → alert_id mapping in kv_state. NEVER
    raises — dedup is a nice-to-have, not a hard requirement, and a
    kv_state write hiccup must not break the create path.
    """
    if not external_id or not alert_id:
        return
    try:
        from state.db import get_connection
        payload = json.dumps(
            {"alert_id": alert_id, "created_at": created_at}
        )
        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (
                    _external_id_kv_key(external_id),
                    payload,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"webhook /events: kv_state write failed for "
            f"external_id={external_id!r}: {exc}"
        )


def _resolve_hmac_user_id() -> str:
    """Decide which user_id stamps an HMAC-authenticated /events alert.

    Resolution order:
      1. ``WEBHOOK_INBOUND_USER_ID`` env var (operator-supplied).
      2. The OLDEST registered admin (the deterministic "first admin"
         in created_at-ASC order). ``list_users`` returns newest-first
         so we reverse and filter to ``role == 'admin'``.
      3. Empty string — falls into the legacy global bucket. Better
         than refusing the alert: the alert is real, an analyst will
         still see it in the UI's global view.
    """
    explicit = (
        os.environ.get("WEBHOOK_INBOUND_USER_ID", "") or ""
    ).strip()
    if explicit:
        return explicit
    try:
        from auth.users import list_users
        users = list_users()
        # list_users sorts newest-first; reverse to get oldest-first
        # and pick the first admin so the chosen owner is stable
        # across deploys.
        for u in reversed(users):
            if getattr(u, "role", "") == "admin":
                return u.user_id
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"webhook /events: list_users failed during HMAC user "
            f"resolution: {exc}"
        )
    return ""


# Per-IP token-bucket for HMAC-authenticated /events requests. Bearer
# requests piggyback on auth.rate_limit (per-user). HMAC requests
# don't carry a user_id at the HTTP layer, so we bucket per remote
# address with a more generous capacity — external monitoring tools
# can burst (a single Datadog alarm can fan out a dozen webhook
# retries in a flaky-network minute). The shape mirrors
# auth.rate_limit.TokenBucket but is intentionally local to this
# module so it doesn't pollute the per-user registry.
_HMAC_BUCKETS: dict[str, tuple[float, float]] = {}
_HMAC_BUCKET_CAPACITY: int = 300
_HMAC_BUCKET_REFILL_PER_SEC: float = 5.0
# Cap the per-IP bucket table so varied / spoofed source addresses can't grow
# it without bound (memory exhaustion). Evicted only when adding a NEW IP.
_HMAC_BUCKET_MAX_IPS: int = 10_000


def _evict_hmac_buckets(now: float) -> None:
    """Bound ``_HMAC_BUCKETS`` against memory exhaustion from many distinct
    (possibly spoofed) source IPs.

    First drop fully-refilled (idle) entries — a bucket back at capacity
    carries no state, since a missing key defaults to full capacity, so the
    drop is lossless. If the table is STILL at the cap (a genuine flood of
    distinct active IPs), clear it: bounded memory wins, and every bucket
    simply resets to full capacity — a brief, safe rate-limit softening, not
    a correctness break.
    """
    if len(_HMAC_BUCKETS) < _HMAC_BUCKET_MAX_IPS:
        return
    cap = float(_HMAC_BUCKET_CAPACITY)
    for ip, (tokens, last) in list(_HMAC_BUCKETS.items()):
        refilled = min(cap, tokens + max(0.0, now - last) * _HMAC_BUCKET_REFILL_PER_SEC)
        if refilled >= cap:
            _HMAC_BUCKETS.pop(ip, None)
    if len(_HMAC_BUCKETS) >= _HMAC_BUCKET_MAX_IPS:
        _HMAC_BUCKETS.clear()


def _hmac_rate_limit(remote_ip: str) -> tuple[bool, float]:
    """Per-IP token-bucket for HMAC-authenticated /events POSTs.

    Returns ``(allowed, retry_after_seconds)``. Uses a plain dict +
    no lock — the BaseHTTPRequestHandler server is single-threaded
    (one request at a time on the same socket) so a non-atomic
    refill is fine here. We deliberately keep this OUT of
    ``auth.rate_limit`` so the per-user dict for bearer requests
    doesn't grow with random remote IPs and so the HMAC tuning can
    diverge from the bearer defaults without touching the shared
    module.
    """
    if not remote_ip:
        remote_ip = "unknown"
    now = time.monotonic()
    # Bound the table before inserting a previously-unseen IP.
    if remote_ip not in _HMAC_BUCKETS:
        _evict_hmac_buckets(now)
    tokens, last = _HMAC_BUCKETS.get(
        remote_ip, (float(_HMAC_BUCKET_CAPACITY), now),
    )
    elapsed = max(0.0, now - last)
    tokens = min(
        float(_HMAC_BUCKET_CAPACITY),
        tokens + elapsed * _HMAC_BUCKET_REFILL_PER_SEC,
    )
    if tokens >= 1.0:
        tokens -= 1.0
        _HMAC_BUCKETS[remote_ip] = (tokens, now)
        return True, 0.0
    # Compute the wait until 1 token is available given the refill
    # rate; floor at the bucket-window equivalent so a saturated
    # client always gets a sane Retry-After.
    deficit = 1.0 - tokens
    retry_after = deficit / _HMAC_BUCKET_REFILL_PER_SEC
    _HMAC_BUCKETS[remote_ip] = (tokens, now)
    return False, retry_after


def _clear_hmac_buckets() -> None:
    """Reset the per-IP HMAC bucket registry. For tests only — see
    auth.rate_limit.clear_buckets for the rationale."""
    _HMAC_BUCKETS.clear()


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

    # Per-request socket timeout (seconds). BaseHTTPRequestHandler honours
    # self.timeout via StreamRequestHandler.setup -> connection.settimeout, so
    # a client that opens a connection and then stalls — or declares a body it
    # never finishes sending — causes rfile.read to raise socket.timeout
    # instead of blocking the single-threaded server forever. do_POST's
    # try/except converts that into a clean 500 + connection close. Without
    # this, one withheld-body request is a trivial unauthenticated DoS.
    timeout = 15

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
            if path == "/events":
                self._handle_events()
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
            # GET on a POST-only known path → 405, not 404. /events is
            # the inbound POST surface; surfacing 405 makes the wrong-
            # method error explicit so a tool author can fix it.
            if path == "/events":
                self._method_not_allowed()
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

    def _bearer_or_hmac_user_id(self) -> str:
        """Owning user_id for an ack action: the bearer token's user when a
        valid token is present, else the HMAC fallback
        (``_resolve_hmac_user_id`` — ``WEBHOOK_INBOUND_USER_ID`` or the oldest
        admin). This MUST be passed to the engine's ``acknowledge_*`` helpers:
        out-of-process they would otherwise resolve user_id via the Streamlit
        session (empty here), which disables per-user scoping — i.e. a single
        shared-secret holder could acknowledge ANY user's alerts."""
        auth_header = self.headers.get("Authorization", "") or ""
        if auth_header.lower().startswith("bearer "):
            raw_token = auth_header[len("bearer "):].strip()
            if raw_token:
                try:
                    from auth.tokens import verify_token
                    uid = verify_token(raw_token)
                    if uid:
                        return uid
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"webhook ack: bearer verify failed: {exc}")
        return _resolve_hmac_user_id()

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
            # codes. Always returning 200 keeps it idempotent. Scope to the
            # resolved owner so an ack can only touch THAT user's alert
            # (cross-user acks were possible when this passed no user_id).
            uid = self._bearer_or_hmac_user_id()
            from engine.alert_engine_v2 import acknowledge_alert
            acknowledge_alert(alert_id, user_id=uid)
            logger.info(
                f"webhook /ack: acknowledged alert_id={alert_id} "
                f"(user_id={uid!r})"
            )
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

            # Scope to the resolved owner so /ack-all acks only THAT user's
            # open alerts — not every user's. Refuse rather than fall through
            # to a global sweep when no owner can be resolved (an empty
            # user_id disables scoping in the engine, the original bug).
            uid = self._bearer_or_hmac_user_id()
            if not uid:
                logger.warning(
                    "webhook /ack-all: no resolvable owner (set "
                    "WEBHOOK_INBOUND_USER_ID or use a bearer token); refusing "
                    "to ack globally"
                )
                _send_json(
                    self, HTTPStatus.BAD_REQUEST,
                    {"error": "no resolvable user for ack-all"},
                )
                return
            from engine.alert_engine_v2 import acknowledge_all
            acknowledge_all(user_id=uid)
            logger.info(
                f"webhook /ack-all: acknowledged every open alert "
                f"for user_id={uid!r}"
            )
            _send_json(
                self, HTTPStatus.OK, {"acknowledged": True, "scope": "user"},
            )
        except Exception as exc:
            logger.exception(f"webhook /ack-all crashed: {exc}")
            _send_json(self, HTTPStatus.INTERNAL_SERVER_ERROR,
                       {"error": "internal server error"})

    # ── Endpoint: POST /events ─────────────────────────────────────

    def _handle_events(self) -> None:
        """Ingest an external alert payload and persist it as a
        ShippingAlert. Authenticated with HMAC OR bearer token (both
        accepted, both validated if both present).

        Validation cascade (each step short-circuits with the
        listed status):

          1. Content-Type must be application/json  → 415
          2. Body must parse as JSON                → 400
          3. At least one auth header must validate → 401
             (and both must validate if both supplied)
          4. Required fields present + non-empty    → 400
             (alert_type, severity, title)
          5. severity normalizes to allowed value   → 400

        On success, builds a ShippingAlert, calls save_alerts with the
        resolved user_id, writes an audit_events row tagged
        ``inbound_alert``, and responds 201 with the alert_id.

        Dedup: if the caller supplied ``external_id`` AND we have a
        kv_state row for that id created within the last 24h, the
        function returns 200 + the stored alert_id + status="deduped"
        and does NOT call save_alerts. The dedup mapping is written
        AFTER save_alerts so a save failure doesn't poison the
        dedup table with a non-existent alert_id.
        """
        try:
            # ── 1. Content-Type ──────────────────────────────────
            raw_ctype = (
                self.headers.get("Content-Type", "") or ""
            ).strip().lower()
            if not raw_ctype.startswith("application/json"):
                _send_json(
                    self, HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"error": "content-type must be application/json"},
                )
                return

            body = _read_body(self)

            # ── 2. JSON parse ────────────────────────────────────
            if not body:
                # Empty body counts as malformed JSON for this
                # endpoint — there is no valid empty payload.
                _send_json(self, HTTPStatus.BAD_REQUEST,
                           {"error": "malformed json"})
                return
            try:
                payload = json.loads(body)
            except (TypeError, ValueError, json.JSONDecodeError):
                _send_json(self, HTTPStatus.BAD_REQUEST,
                           {"error": "malformed json"})
                return
            if not isinstance(payload, dict):
                _send_json(self, HTTPStatus.BAD_REQUEST,
                           {"error": "payload must be a JSON object"})
                return

            # ── 3. Auth — HMAC and/or bearer ────────────────────
            # Spec: at least one must validate; if BOTH are present
            # BOTH must validate. Build a tri-state for each
            # (missing / present-and-valid / present-and-invalid) so
            # the combination check is unambiguous.
            sig_header = self.headers.get(
                "X-Hub-Signature-256", "",
            ) or ""
            auth_header = self.headers.get("Authorization", "") or ""

            hmac_present = bool(sig_header)
            hmac_valid = False
            if hmac_present:
                hmac_valid = _verify_hmac(
                    body, sig_header, _get_secrets(),
                )

            bearer_present = False
            bearer_user_id: Optional[str] = None
            if auth_header:
                parts = auth_header.split(None, 1)
                if len(parts) == 2 and parts[0].lower() == "bearer":
                    raw_token = parts[1].strip()
                    if raw_token:
                        bearer_present = True
                        try:
                            from auth.tokens import verify_token
                            bearer_user_id = verify_token(raw_token)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                f"webhook /events: verify_token "
                                f"raised: {exc}"
                            )
                            bearer_user_id = None

            if not hmac_present and not bearer_present:
                # No credentials supplied at all.
                _send_json(self, HTTPStatus.UNAUTHORIZED,
                           {"error": "missing credentials"})
                return
            if hmac_present and not hmac_valid:
                # HMAC supplied but wrong — even if a valid bearer is
                # also supplied we reject so a stolen-bearer attacker
                # can't slip a forged HMAC alongside a real token.
                _send_json(self, HTTPStatus.UNAUTHORIZED,
                           {"error": "invalid signature"})
                return
            if bearer_present and bearer_user_id is None:
                # Same logic for the other direction.
                _send_json(self, HTTPStatus.UNAUTHORIZED,
                           {"error": "invalid token"})
                return

            # Resolve owning user_id. Bearer wins when present (it
            # carries an actual user identity); HMAC falls back to the
            # env-configured or first-admin user.
            if bearer_user_id:
                resolved_user_id = bearer_user_id
                auth_method = "bearer"
            else:
                resolved_user_id = _resolve_hmac_user_id()
                auth_method = "hmac"

            # ── Rate limit ───────────────────────────────────────
            # Bearer requests piggyback on the per-user bucket from
            # auth.rate_limit (same shape the API server uses, so a
            # script hammering /alerts + /events shares one budget).
            # HMAC requests use the more generous per-IP bucket so a
            # bursty monitoring tool can fire 50 events in a minute
            # without tripping over a per-user limit.
            if auth_method == "bearer":
                try:
                    from auth.rate_limit import check_rate_limit
                    allowed, retry_after = check_rate_limit(
                        resolved_user_id,
                        capacity=120,
                        refill_per_sec=2.0,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        f"webhook /events: bearer rate-limit raised, "
                        f"failing open: {exc}"
                    )
                    allowed, retry_after = True, 0.0
            else:
                allowed, retry_after = _hmac_rate_limit(
                    self.client_address[0]
                    if self.client_address else "unknown",
                )
            if not allowed:
                import math
                retry_int = max(1, int(math.ceil(retry_after)))
                resp = json.dumps(
                    {"error": "rate_limited",
                     "retry_after_seconds": retry_int},
                ).encode("utf-8")
                self.send_response(HTTPStatus.TOO_MANY_REQUESTS)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.send_header("Retry-After", str(retry_int))
                self.end_headers()
                self.wfile.write(resp)
                return

            # ── 4. Required fields ──────────────────────────────
            alert_type_raw = payload.get("alert_type", "")
            severity_raw = payload.get("severity", "")
            title_raw = payload.get("title", "")
            if (not isinstance(alert_type_raw, str)
                    or not alert_type_raw.strip()):
                _send_json(self, HTTPStatus.BAD_REQUEST,
                           {"error": "alert_type required"})
                return
            if (not isinstance(severity_raw, str)
                    or not severity_raw.strip()):
                _send_json(self, HTTPStatus.BAD_REQUEST,
                           {"error": "severity required"})
                return
            if (not isinstance(title_raw, str)
                    or not title_raw.strip()):
                _send_json(self, HTTPStatus.BAD_REQUEST,
                           {"error": "title required"})
                return

            # ── 5. severity normalization ───────────────────────
            severity_norm = severity_raw.strip().upper()
            if severity_norm not in _ALLOWED_SEVERITIES:
                _send_json(
                    self, HTTPStatus.BAD_REQUEST,
                    {"error": (
                        "severity must be one of "
                        "CRITICAL/HIGH/MEDIUM/LOW"
                    )},
                )
                return

            # ── Dedup lookup (external_id) ──────────────────────
            external_id_raw = payload.get("external_id", "")
            if (not isinstance(external_id_raw, str)
                    or not external_id_raw.strip()):
                external_id = ""
            else:
                external_id = external_id_raw.strip()

            if external_id:
                existing = _read_external_id_alert(external_id)
                if existing is not None:
                    existing_alert_id, _existing_ts = existing
                    logger.info(
                        f"webhook /events: deduped external_id="
                        f"{external_id!r} → existing alert_id="
                        f"{existing_alert_id}"
                    )
                    _send_json(
                        self, HTTPStatus.OK,
                        {
                            "alert_id": existing_alert_id,
                            "status": "deduped",
                        },
                    )
                    return

            # ── Optional field coercion ─────────────────────────
            def _opt_str(name: str) -> str:
                v = payload.get(name, "")
                return v.strip() if isinstance(v, str) else ""

            def _opt_float(name: str) -> float:
                v = payload.get(name, 0.0)
                try:
                    return float(v) if v is not None else 0.0
                except (TypeError, ValueError):
                    return 0.0

            body_str = _opt_str("body")
            ticker = _opt_str("ticker")
            route_id = _opt_str("route_id")
            port_locode = _opt_str("port_locode")
            value = _opt_float("value")
            threshold = _opt_float("threshold")
            change_pct = _opt_float("change_pct")

            # ── Build + persist ─────────────────────────────────
            alert_id = str(uuid.uuid4())
            created_at = datetime.now(timezone.utc).isoformat()

            from engine.alert_engine_v2 import (
                ShippingAlert,
                save_alerts,
            )
            alert = ShippingAlert(
                alert_id=alert_id,
                created_at=created_at,
                alert_type=alert_type_raw.strip(),
                severity=severity_norm,
                title=title_raw.strip(),
                body=body_str,
                ticker=ticker,
                route_id=route_id,
                port_locode=port_locode,
                value=value,
                threshold=threshold,
                change_pct=change_pct,
                acknowledged=False,
            )
            try:
                save_alerts([alert], user_id=resolved_user_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    f"webhook /events: save_alerts failed: {exc}"
                )
                _send_json(
                    self, HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "failed to persist alert"},
                )
                return

            # Dedup mapping AFTER save_alerts so we don't store an
            # alert_id that doesn't exist in the alerts table.
            if external_id:
                _store_external_id_alert(
                    external_id, alert_id, created_at,
                )

            # Audit trail. Detail intentionally excludes the raw body
            # (sanitised — title/body could contain anything an
            # operator put on the wire) but keeps the structured
            # provenance.
            try:
                from auth.audit import record_audit
                record_audit(
                    "inbound_alert",
                    entity_type="alert",
                    entity_id=alert_id,
                    detail={
                        "alert_type": alert_type_raw.strip(),
                        "severity": severity_norm,
                        "auth_method": auth_method,
                        "external_id": external_id or "",
                    },
                    user_id=resolved_user_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    f"webhook /events: audit write failed: {exc}"
                )

            logger.info(
                f"webhook /events: created alert_id={alert_id} "
                f"alert_type={alert_type_raw.strip()!r} "
                f"severity={severity_norm} "
                f"auth_method={auth_method} "
                f"user_id={resolved_user_id!r}"
            )
            _send_json(
                self, HTTPStatus.CREATED,
                {"alert_id": alert_id, "status": "created"},
            )
        except Exception as exc:
            logger.exception(f"webhook /events crashed: {exc}")
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
                # Scope to the HMAC-resolved owner (PagerDuty webhooks are
                # HMAC-only, no bearer) so a resolved incident acks only that
                # owner's alert — not every user's matching dedup_key.
                uid = _resolve_hmac_user_id()
                from engine.alert_engine_v2 import acknowledge_alert
                acknowledge_alert(dedup_key, user_id=uid)
                logger.info(
                    f"webhook /webhooks/pagerduty: resolved {dedup_key} "
                    f"(event_type={event_type}, user_id={uid!r})"
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
