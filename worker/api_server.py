"""worker/api_server.py — stdlib read-only HTTP API gated by API tokens.

Companion to ``worker/webhook_listener.py`` (port 8502, INBOUND ack /
webhooks). This module exposes the OUTBOUND read surface: external
scripts that want to ``GET`` alerts / reports / telemetry from the
Ship Tracker store without reaching for SQL or the Streamlit UI.

Design constraints — kept deliberately tight so the cost surface of
"now we have a public-ish API" stays small:

* **Stdlib only.** ``http.server.BaseHTTPRequestHandler`` + ``json``.
  No Flask / FastAPI / Starlette / aiohttp — adding a framework here
  would also pull in a transitive dependency graph the worker image
  doesn't need.
* **Bearer tokens.** Every authenticated endpoint requires an
  ``Authorization: Bearer <raw_token>`` header. The raw token is
  passed straight to :func:`auth.tokens.verify_token` which returns
  the owning ``user_id`` (or ``None`` on miss). On miss → 401.
  Missing header → 401. NO query-string auth fallback — the only
  authentication channel is the standard Bearer header so a token
  cannot end up in a server access log via a URL parameter.
* **Per-user scoping.** Every endpoint that touches user-scoped data
  threads the resolved ``user_id`` into the engine call. Cross-user
  access (e.g. acking bob's alert with alice's token) silently no-ops
  because the engine UPDATE matches zero rows; that mirrors the
  Streamlit UI's contract exactly.
* **Read + scoped write.** The read endpoints map 1:1 to existing
  engine reads. The write surface is intentionally narrow and per-
  user: rule management (POST/GET/DELETE ``/api/v1/rules``),
  delivery-channel management (POST ``/api/v1/channels``, DELETE
  ``/api/v1/channels/<id>``), report-public-share state (POST /
  DELETE ``/api/v1/reports/<id>/public``), and the historical
  acknowledge endpoint (POST ``/api/v1/alerts/<id>/ack``). Every
  write threads the resolved ``user_id`` through to the engine call
  so cross-user writes cannot succeed (alice's token cannot delete
  bob's channel; the SQL scope filter excludes bob's row).
* **Crash-proof.** Every endpoint method wraps its body in a
  try/except → 500 with ``{"error": "internal server error"}``.
  An exception in one request must never kill the serve loop.
* **Public health.** ``GET /api/v1/health`` is unauthenticated and
  emits the same shape as ``worker/webhook_listener.py``'s health
  probe so load-balancer configs can point at either port
  interchangeably.

This module must NOT import ``streamlit``. It runs out-of-process as
a sibling container under docker-compose and has no ``st.*`` available.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

from loguru import logger


# Module-load wall-clock anchor — ``GET /api/v1/health`` exposes
# process uptime against this. Per-handler instances are created per
# request so a module global is the only place this can live.
_START_TIME: float = time.time()


# Cap on the number of alerts returned by the list endpoint. Even when
# the caller asks for a 365-day window, we never emit more than this
# many rows in a single response — keeps the JSON payload bounded and
# also protects against accidental DoS from an unconfigured client
# polling the endpoint in a tight loop.
_MAX_ALERTS_RESPONSE = 500


# Default ``limit`` value on ``GET /api/v1/reports`` when the query
# parameter is missing or unparseable. Matches the UI's default page
# size so the API and the Streamlit tab agree on "recent reports".
_DEFAULT_REPORTS_LIMIT = 50


# Default + hard cap for the audit-events endpoint. The default matches
# ``auth.audit.query_audit``'s default; the cap protects against a
# caller asking for ``limit=10**9`` and blowing the JSON payload — the
# engine call itself enforces the SQL ``LIMIT`` clause but we double-
# clamp on the API layer so the contract is visible at the wire.
_DEFAULT_AUDIT_LIMIT = 100
_MAX_AUDIT_LIMIT = 1000


# ─────────────────────────────────────────────────────────────────────────────
#  Rate-limit configuration (env-driven, evaluated lazily per request so a
#  test can monkey-patch ``os.environ`` without forcing a process restart)
# ─────────────────────────────────────────────────────────────────────────────

# Defaults: capacity 120 / refill 2 per sec. That allows a burst of 120
# requests followed by a steady 2 req/sec — comfortable headroom for a
# polling script that fetches /alerts every 0.5s, and tight enough to
# make a tight-loop hammer trip a 429 within 60s of sustained abuse.
_DEFAULT_RATE_LIMIT_CAPACITY = 120
_DEFAULT_RATE_LIMIT_REFILL_PER_SEC = 2.0


def _rate_limit_capacity() -> int:
    """Read ``RATE_LIMIT_CAPACITY`` env var, falling back to the default.

    A malformed value (non-int, ``<= 0``) falls back to the default
    rather than raising — a typo in the env shouldn't crash the
    server at request time.
    """
    raw = os.environ.get("RATE_LIMIT_CAPACITY", "").strip()
    if not raw:
        return _DEFAULT_RATE_LIMIT_CAPACITY
    try:
        n = int(raw)
        return n if n > 0 else _DEFAULT_RATE_LIMIT_CAPACITY
    except (TypeError, ValueError):
        return _DEFAULT_RATE_LIMIT_CAPACITY


def _rate_limit_refill_per_sec() -> float:
    """Read ``RATE_LIMIT_REFILL_PER_SEC`` env var, falling back to default.

    Same lenient parsing as ``_rate_limit_capacity``.
    """
    raw = os.environ.get("RATE_LIMIT_REFILL_PER_SEC", "").strip()
    if not raw:
        return _DEFAULT_RATE_LIMIT_REFILL_PER_SEC
    try:
        v = float(raw)
        return v if v > 0 else _DEFAULT_RATE_LIMIT_REFILL_PER_SEC
    except (TypeError, ValueError):
        return _DEFAULT_RATE_LIMIT_REFILL_PER_SEC


# ─────────────────────────────────────────────────────────────────────────────
#  Shared response helpers
# ─────────────────────────────────────────────────────────────────────────────

def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    """Write a JSON response. Centralized so every code path emits the
    same Content-Type + Content-Length headers. ``payload`` can be a
    dict OR a list — the alerts list endpoint returns a top-level
    array so we don't wrap it in an envelope."""
    try:
        body = json.dumps(payload, default=str).encode("utf-8")
    except (TypeError, ValueError) as exc:
        # Last-resort fallback so a non-serialisable payload turns
        # into a 500 instead of a wire-level exception that kills the
        # connection mid-stream.
        logger.warning(f"api: JSON encode failed: {exc}")
        body = json.dumps({"error": "internal server error"}).encode("utf-8")
        status = HTTPStatus.INTERNAL_SERVER_ERROR
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_html(handler: BaseHTTPRequestHandler, status: int, html: str) -> None:
    """Write an HTML response — used exclusively by the report-HTML
    endpoint. Reports are HTML in the DB and would be unusable
    wrapped in a JSON string field (the consumer would have to decode
    a JSON-escaped HTML blob and re-serialise headers), so we just
    flip the Content-Type and stream the raw bytes."""
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_markdown(
    handler: BaseHTTPRequestHandler, status: int, markdown: str,
) -> None:
    """Write a Markdown response — same wire-shape as ``_send_html``
    but with ``text/markdown; charset=utf-8`` so downstream tools
    (curl, fetch, etc.) treat the body as Markdown source rather than
    rendered HTML or a JSON envelope."""
    body = markdown.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/markdown; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _extract_bearer_token(handler: BaseHTTPRequestHandler) -> Optional[str]:
    """Extract the raw token from the ``Authorization`` header.

    Returns the raw token string on a well-formed
    ``Authorization: Bearer <token>`` header, ``None`` otherwise.
    The match on ``Bearer`` is case-insensitive so curl examples
    that lowercase the scheme name still work — RFC 6750 specifies
    the auth-scheme name is case-insensitive.
    """
    raw = handler.headers.get("Authorization", "")
    if not raw:
        return None
    parts = raw.split(None, 1)
    if len(parts) != 2:
        return None
    scheme, token = parts[0], parts[1].strip()
    if scheme.lower() != "bearer":
        return None
    if not token:
        return None
    return token


def _authenticate(handler: BaseHTTPRequestHandler) -> Optional[str]:
    """Resolve ``Authorization: Bearer …`` to a ``user_id`` or ``None``.

    A ``None`` return means the request should respond with 401. We
    intentionally do NOT distinguish "no Authorization header" from
    "header present but bad token" in the response — both look like
    ``{"error": "unauthorized"}`` so a probing caller can't enumerate
    token prefixes by header presence."""
    raw_token = _extract_bearer_token(handler)
    if not raw_token:
        return None
    try:
        from auth.tokens import verify_token
        return verify_token(raw_token)
    except Exception as exc:  # noqa: BLE001
        # verify_token already swallows internal errors but we wrap
        # the import + call belt-and-braces — a broken token table
        # must not crash the server.
        logger.warning(f"api: verify_token raised: {exc}")
        return None


def _send_unauthorized(handler: BaseHTTPRequestHandler) -> None:
    _send_json(handler, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})


def _send_rate_limited(
    handler: BaseHTTPRequestHandler, retry_after_seconds: float,
) -> None:
    """Emit a 429 with both a JSON body and the standard ``Retry-After``
    header (RFC 7231 §7.1.3). The header is an integer-seconds value
    rounded UP — ``Retry-After: 0`` would mean "retry immediately"
    which contradicts the 429, so we floor at 1.

    We deliberately do NOT include the token / user_id in the body —
    a 429 response that echoes back the bearer credential would leak
    it into any access log that captures response bodies.
    """
    # Round up so the wait is never under-stated. A retry_after of
    # 0.2s rounds to 1s; 1.4s rounds to 2s.
    import math
    retry_int = max(1, int(math.ceil(retry_after_seconds)))
    body = json.dumps(
        {"error": "rate_limited", "retry_after_seconds": retry_int},
    ).encode("utf-8")
    handler.send_response(HTTPStatus.TOO_MANY_REQUESTS)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    # RFC 7231 §7.1.3 — integer seconds is the simpler of the two
    # accepted formats (the other being an HTTP-date).
    handler.send_header("Retry-After", str(retry_int))
    handler.end_headers()
    handler.wfile.write(body)


def _enforce_rate_limit(
    handler: BaseHTTPRequestHandler, user_id: str,
) -> bool:
    """Run the per-user rate-limit check; return True when the request
    is allowed to proceed. On deny, emits a 429 with the appropriate
    ``Retry-After`` header and returns False — the caller must early-
    exit without invoking the endpoint handler.

    We import lazily so a test that monkeypatches ``auth.rate_limit``
    (e.g. with ``clear_buckets``) sees the patched module.
    """
    try:
        from auth.rate_limit import check_rate_limit
        allowed, retry_after = check_rate_limit(
            user_id,
            capacity=_rate_limit_capacity(),
            refill_per_sec=_rate_limit_refill_per_sec(),
        )
    except Exception as exc:  # noqa: BLE001
        # A broken rate-limit module must NOT take down auth'd traffic
        # — fail open with a warning log so the symptom is visible.
        logger.warning(f"api: rate-limit check raised, failing open: {exc}")
        return True
    if not allowed:
        # NB: user_id ONLY, no token, in the log line.
        logger.info(
            f"api: rate-limited user_id={user_id!r}, "
            f"retry_after={retry_after:.2f}s"
        )
        _send_rate_limited(handler, retry_after)
        return False
    return True


def _send_not_found(handler: BaseHTTPRequestHandler) -> None:
    _send_json(handler, HTTPStatus.NOT_FOUND, {"error": "not found"})


def _send_method_not_allowed(handler: BaseHTTPRequestHandler) -> None:
    _send_json(handler, HTTPStatus.METHOD_NOT_ALLOWED,
               {"error": "method not allowed"})


def _send_internal_error(handler: BaseHTTPRequestHandler) -> None:
    _send_json(handler, HTTPStatus.INTERNAL_SERVER_ERROR,
               {"error": "internal server error"})


def _parse_int(raw: Optional[str], default: int) -> int:
    """Best-effort ``int(raw)``. Returns ``default`` on any failure —
    a malformed query parameter must not 500 the endpoint."""
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# Sentinel for "read the request body but the Content-Type was wrong"
# — the read helper returns this so callers can distinguish "415" from
# "valid empty body" cleanly without juggling Optional[Optional[…]].
_BODY_BAD_CTYPE = object()
# Sentinel for "Content-Type was JSON but the body did not parse as
# JSON" → 400.
_BODY_BAD_JSON = object()


def _read_json_body(handler: BaseHTTPRequestHandler) -> Any:
    """Read the request body and parse it as JSON.

    Returns one of:
      * The parsed value (dict / list / scalar) on success.
      * ``_BODY_BAD_CTYPE`` when ``Content-Type`` is set and is NOT
        ``application/json`` (caller → 415).
      * ``_BODY_BAD_JSON`` when the body is present but does not parse
        as JSON (caller → 400).
      * ``None`` when ``Content-Length`` is missing / zero (caller
        decides whether an empty body is acceptable).

    The Content-Type check accepts ``application/json`` and any
    parameterised variant (e.g. ``application/json; charset=utf-8``).
    A *missing* Content-Type is tolerated when the body is empty
    (so e.g. ``DELETE /api/v1/rules`` with no body doesn't 415); a
    missing Content-Type WITH a non-empty body is treated as bad
    because the wire shape is ambiguous.
    """
    raw_ctype = (handler.headers.get("Content-Type", "") or "").strip().lower()
    raw_len = handler.headers.get("Content-Length", "") or "0"
    try:
        clen = int(raw_len)
    except (TypeError, ValueError):
        clen = 0
    if clen <= 0:
        # No body. If a Content-Type was set anyway we still enforce
        # it must be JSON — otherwise the caller is mis-signalling
        # their intent.
        if raw_ctype and not raw_ctype.startswith("application/json"):
            return _BODY_BAD_CTYPE
        return None
    # Body present — Content-Type MUST be application/json. We do
    # NOT try to sniff JSON out of an unmarked body because callers
    # have to opt-in to the contract.
    if not raw_ctype.startswith("application/json"):
        return _BODY_BAD_CTYPE
    try:
        # Cap the read at Content-Length so a malicious client can't
        # stream an unbounded body. The HTTP layer already enforces
        # Content-Length on the framing side; this is belt-and-braces.
        raw = handler.rfile.read(clen)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"api: rfile.read failed: {exc}")
        return _BODY_BAD_JSON
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning(f"api: json decode failed: {exc}")
        return _BODY_BAD_JSON


def _send_bad_request(handler: BaseHTTPRequestHandler, msg: str = "bad request") -> None:
    _send_json(handler, HTTPStatus.BAD_REQUEST, {"error": msg})


def _send_unsupported_media(handler: BaseHTTPRequestHandler) -> None:
    _send_json(handler, HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
               {"error": "content-type must be application/json"})


# ─────────────────────────────────────────────────────────────────────────────
#  Path patterns — compiled once at import so per-request dispatch is
#  a single regex match, not a chain of string startswith checks.
# ─────────────────────────────────────────────────────────────────────────────

_RE_ALERTS_LIST = re.compile(r"^/api/v1/alerts/?$")
_RE_ALERT_ONE = re.compile(r"^/api/v1/alerts/([^/]+)/?$")
_RE_ALERT_ACK = re.compile(r"^/api/v1/alerts/([^/]+)/ack/?$")
_RE_REPORTS_LIST = re.compile(r"^/api/v1/reports/?$")
_RE_REPORT_HTML = re.compile(r"^/api/v1/reports/([^/]+)/html/?$")
_RE_REPORT_MARKDOWN = re.compile(r"^/api/v1/reports/([^/]+)/markdown/?$")
_RE_REPORT_PUBLIC = re.compile(r"^/api/v1/reports/([^/]+)/public/?$")
_RE_TELEMETRY_LLM = re.compile(r"^/api/v1/telemetry/llm/?$")
_RE_TELEMETRY_PERF = re.compile(r"^/api/v1/telemetry/perf/?$")
_RE_HEALTH = re.compile(r"^/api/v1/health/?$")
# WRITE endpoints (v2 — rule + channel + report-public management).
_RE_RULES = re.compile(r"^/api/v1/rules/?$")
_RE_CHANNELS = re.compile(r"^/api/v1/channels/?$")
_RE_CHANNEL_ONE = re.compile(r"^/api/v1/channels/([^/]+)/?$")
# OBSERVABILITY endpoints (v3 — mirrors what tab_data_health +
# tab_operator_overview render, so external monitoring scripts don't
# have to scrape the Streamlit UI).
_RE_AUDIT = re.compile(r"^/api/v1/audit/?$")
_RE_AUDIT_EXPORT = re.compile(r"^/api/v1/audit/export/?$")
_RE_INCIDENTS = re.compile(r"^/api/v1/incidents/?$")
_RE_SOURCE_HEALTH = re.compile(r"^/api/v1/source-health/?$")
# Report-schedule endpoints (schema v20). The list/create surface is
# the bare path; the per-id endpoints (PATCH / DELETE) carry a
# ``schedule_id`` capture group.
_RE_SCHEDULES = re.compile(r"^/api/v1/schedules/?$")
_RE_SCHEDULE_ONE = re.compile(r"^/api/v1/schedules/([^/]+)/?$")
# Alert-silence endpoints (schema v22). The list/create surface is
# the bare path; the per-id endpoint (DELETE only — there is no edit
# semantics, a silence is created once and either expires or is
# cancelled) carries a ``silence_id`` capture group.
_RE_SILENCES = re.compile(r"^/api/v1/silences/?$")
_RE_SILENCE_ONE = re.compile(r"^/api/v1/silences/([^/]+)/?$")
# Alert-annotation endpoints (schema v23). The thread for one alert
# lives under /api/v1/alerts/<alert_id>/annotations — list (GET) +
# add (POST). Per-annotation mutations live under /api/v1/annotations/<id>
# — edit (PATCH) + delete (DELETE). Splitting the routes this way
# keeps the dispatch regex shallow (one capture group per shape) and
# matches the convention of /api/v1/alerts/<id>/ack.
_RE_ALERT_ANNOTATIONS = re.compile(
    r"^/api/v1/alerts/([^/]+)/annotations/?$"
)
_RE_ANNOTATION_ONE = re.compile(r"^/api/v1/annotations/([^/]+)/?$")
# Per-rule escalation-chain endpoints (schema v24). The chain for one
# rule lives under /api/v1/rules/<rule_id>/escalations — list (GET) +
# add (POST) + clear-whole-chain (DELETE). Per-step mutations live
# under /api/v1/escalations/<chain_id> — delete one step. Same routing
# split as alerts/<id>/annotations + /annotations/<id> so the dispatch
# regexes stay shallow and operators can model the surface in the
# same way they already do for annotations.
_RE_RULE_ESCALATIONS = re.compile(
    r"^/api/v1/rules/([^/]+)/escalations/?$"
)
_RE_ESCALATION_ONE = re.compile(r"^/api/v1/escalations/([^/]+)/?$")
# Per-user notification-prefs endpoints. GET returns the caller's
# current prefs (defaults if none saved); PATCH applies a partial
# update. There is no per-id route — the prefs row is implicitly
# keyed by the caller's authenticated user_id, so a token-holder
# can only read / mutate their own prefs.
_RE_NOTIFICATION_PREFS = re.compile(r"^/api/v1/notification-prefs/?$")


# ─────────────────────────────────────────────────────────────────────────────
#  Dispatching handler — a single BaseHTTPRequestHandler subclass that
#  routes inside ``do_GET`` / ``do_POST`` by regex match on the path.
# ─────────────────────────────────────────────────────────────────────────────

class APIHandler(BaseHTTPRequestHandler):
    """Routes incoming requests to per-endpoint methods.

    Routing rules:

      GET    /api/v1/alerts                 → _list_alerts          (auth)
      GET    /api/v1/alerts/<id>            → _get_alert            (auth)
      POST   /api/v1/alerts/<id>/ack        → _ack_alert            (auth)
      GET    /api/v1/reports                → _list_reports         (auth)
      GET    /api/v1/reports/<id>/html      → _get_report_html      (auth)
      GET    /api/v1/reports/<id>/markdown  → _get_report_markdown  (auth)
      POST   /api/v1/reports/<id>/public    → _make_report_public   (auth, write)
      DELETE /api/v1/reports/<id>/public    → _revoke_report_public (auth, write)
      GET    /api/v1/rules                  → _list_rules           (auth)
      POST   /api/v1/rules                  → _save_rules           (auth, write)
      DELETE /api/v1/rules                  → _reset_rules          (auth, write)
      GET    /api/v1/channels               → _list_channels        (auth)
      POST   /api/v1/channels               → _save_channel         (auth, write)
      DELETE /api/v1/channels/<id>          → _delete_channel       (auth, write)
      GET    /api/v1/telemetry/llm          → _get_llm_telemetry    (auth)
      GET    /api/v1/telemetry/perf         → _get_perf_telemetry   (auth)
      GET    /api/v1/audit                  → _list_audit           (auth)
      GET    /api/v1/audit/export           → _export_audit         (auth)
      GET    /api/v1/incidents              → _list_incidents       (auth)
      GET    /api/v1/source-health          → _get_source_health    (auth)
      GET    /api/v1/silences               → _list_silences        (auth)
      POST   /api/v1/silences               → _create_silence       (auth, write)
      DELETE /api/v1/silences/<id>          → _delete_silence       (auth, write)
      GET    /api/v1/alerts/<id>/annotations → _list_annotations    (auth)
      POST   /api/v1/alerts/<id>/annotations → _create_annotation   (auth, write)
      PATCH  /api/v1/annotations/<id>       → _edit_annotation      (auth, write)
      DELETE /api/v1/annotations/<id>       → _delete_annotation    (auth, write)
      GET    /api/v1/notification-prefs     → _get_notification_prefs   (auth)
      PATCH  /api/v1/notification-prefs     → _patch_notification_prefs (auth, write)
      GET    /api/v1/rules/<id>/escalations  → _list_escalations    (auth)
      POST   /api/v1/rules/<id>/escalations  → _create_escalation_step (auth, write)
      DELETE /api/v1/rules/<id>/escalations  → _delete_chain        (auth, write)
      DELETE /api/v1/escalations/<chain_id>  → _delete_escalation_step (auth, write)
      GET    /api/v1/health                 → _health               (public)

    Any other path → 404. Any wrong-method on a known path → 405. The
    spec explicitly calls out 405 for "wrong method on a known
    endpoint" — that's done in ``_dispatch_unknown_method`` which
    checks if the path matches a known route under a different verb.
    """

    # ── Quiet down BaseHTTPRequestHandler's default access log ────
    # Routing through loguru keeps API logs visually consistent with
    # the rest of the worker.
    def log_message(self, fmt: str, *args) -> None:  # noqa: N802
        logger.info(f"api {self.address_string()} - {fmt % args}")

    # ── Helpers shared across methods ─────────────────────────────

    def _path_and_query(self) -> tuple[str, dict[str, list[str]]]:
        """Split ``self.path`` into a path (no query string) and a
        parsed query-string dict. ``parse_qs`` returns a list per key
        because the same key may legally repeat — callers that only
        want the first value do ``q.get("k", [""])[0]``."""
        parts = urlsplit(self.path)
        path = parts.path or "/"
        query = parse_qs(parts.query, keep_blank_values=True)
        return path, query

    def _path_matches_any_route(self, path: str) -> bool:
        """Does ``path`` match ANY of the known endpoint regexes (under
        any method)? Used by ``_dispatch_unknown_method`` to decide
        between 405 (path known, wrong verb) and 404 (path unknown)."""
        return any(
            r.match(path) for r in (
                _RE_ALERTS_LIST, _RE_ALERT_ONE, _RE_ALERT_ACK,
                _RE_REPORTS_LIST, _RE_REPORT_HTML, _RE_REPORT_MARKDOWN,
                _RE_REPORT_PUBLIC,
                _RE_TELEMETRY_LLM, _RE_TELEMETRY_PERF,
                _RE_HEALTH,
                _RE_RULES, _RE_CHANNELS, _RE_CHANNEL_ONE,
                _RE_AUDIT, _RE_AUDIT_EXPORT, _RE_INCIDENTS, _RE_SOURCE_HEALTH,
                _RE_SCHEDULES, _RE_SCHEDULE_ONE,
                _RE_SILENCES, _RE_SILENCE_ONE,
                _RE_ALERT_ANNOTATIONS, _RE_ANNOTATION_ONE,
                _RE_RULE_ESCALATIONS, _RE_ESCALATION_ONE,
                _RE_NOTIFICATION_PREFS,
            )
        )

    # ── Method dispatch ──────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        try:
            path, query = self._path_and_query()
            logger.info(f"api GET {path}")

            # Health is public — checked BEFORE auth so an
            # unauthenticated probe gets the real status, not a 401.
            if _RE_HEALTH.match(path):
                self._health()
                return

            # Every other GET requires auth.
            user_id = _authenticate(self)
            if user_id is None:
                _send_unauthorized(self)
                return

            # Per-user rate limit. Health is already short-circuited
            # ABOVE the auth gate so the limiter never sees probes.
            if not _enforce_rate_limit(self, user_id):
                return

            if _RE_ALERTS_LIST.match(path):
                self._list_alerts(user_id, query)
                return
            # /alerts/<id>/annotations is more specific than /alerts/<id>;
            # check the annotations regex BEFORE the single-alert one so
            # GET on the thread does not get accidentally handled as a
            # single-alert load. (The regexes are anchored so they do
            # not actually overlap, but the ordering keeps intent
            # explicit.)
            m = _RE_ALERT_ANNOTATIONS.match(path)
            if m:
                self._list_annotations(user_id, m.group(1))
                return
            m = _RE_ALERT_ONE.match(path)
            if m:
                # Exclude the /ack sub-path — that's a POST endpoint,
                # GET-ing it lands here, but ".../ack" is a different
                # alert_id semantically and we don't want a GET to
                # accidentally treat "ack" as a valid alert id. Let
                # the 405 path below handle it.
                alert_id = m.group(1)
                if alert_id == "ack":
                    # /api/v1/alerts/ack with no further segment is
                    # nonsense — fall through to method-not-allowed.
                    pass
                else:
                    self._get_alert(user_id, alert_id)
                    return
            if _RE_REPORTS_LIST.match(path):
                self._list_reports(user_id, query)
                return
            m = _RE_REPORT_HTML.match(path)
            if m:
                self._get_report_html(user_id, m.group(1), query)
                return
            m = _RE_REPORT_MARKDOWN.match(path)
            if m:
                self._get_report_markdown(user_id, m.group(1), query)
                return
            if _RE_TELEMETRY_LLM.match(path):
                self._get_llm_telemetry(user_id, query)
                return
            if _RE_TELEMETRY_PERF.match(path):
                self._get_perf_telemetry(query)
                return
            # /api/v1/rules/<rule_id>/escalations is more specific than
            # /api/v1/rules — check the escalations regex first so a
            # GET on the chain does NOT accidentally route through the
            # rule-list handler. The anchored regex prevents real
            # overlap; the ordering keeps intent explicit.
            m = _RE_RULE_ESCALATIONS.match(path)
            if m:
                self._list_escalations(user_id, m.group(1))
                return
            if _RE_RULES.match(path):
                self._list_rules(user_id)
                return
            if _RE_CHANNELS.match(path):
                self._list_channels(user_id)
                return
            # NB: check the more-specific /audit/export route BEFORE
            # the /audit one. The regexes are anchored so they don't
            # actually overlap, but listing the specific route first
            # keeps the dispatch order readable and matches the
            # convention used for /reports/<id>/html vs /reports.
            if _RE_AUDIT_EXPORT.match(path):
                self._export_audit(user_id, query)
                return
            if _RE_AUDIT.match(path):
                self._list_audit(user_id, query)
                return
            if _RE_INCIDENTS.match(path):
                self._list_incidents(user_id, query)
                return
            if _RE_SOURCE_HEALTH.match(path):
                self._get_source_health(query)
                return
            if _RE_SCHEDULES.match(path):
                self._list_schedules(user_id)
                return
            if _RE_SILENCES.match(path):
                self._list_silences(user_id, query)
                return
            if _RE_NOTIFICATION_PREFS.match(path):
                self._get_notification_prefs(user_id)
                return

            # GET on /api/v1/alerts/<id>/ack — this IS a known route
            # but only under POST. 405 it.
            if _RE_ALERT_ACK.match(path):
                _send_method_not_allowed(self)
                return
            # GET on routes that exist only under write verbs → 405.
            if (_RE_CHANNEL_ONE.match(path)
                    or _RE_REPORT_PUBLIC.match(path)
                    or _RE_SCHEDULE_ONE.match(path)
                    or _RE_SILENCE_ONE.match(path)
                    or _RE_ANNOTATION_ONE.match(path)
                    or _RE_ESCALATION_ONE.match(path)):
                _send_method_not_allowed(self)
                return

            _send_not_found(self)
        except Exception as exc:
            logger.exception(f"api GET handler crashed: {exc}")
            try:
                _send_internal_error(self)
            except Exception:
                pass

    def do_POST(self) -> None:  # noqa: N802
        try:
            path, _query = self._path_and_query()
            logger.info(f"api POST {path}")

            user_id = _authenticate(self)
            if user_id is None:
                _send_unauthorized(self)
                return

            # Per-user rate limit. POST endpoints all touch the DB and
            # the audit log so a hammered POST is more expensive than
            # a hammered GET — the same bucket gates both verbs.
            if not _enforce_rate_limit(self, user_id):
                return

            m = _RE_ALERT_ACK.match(path)
            if m:
                self._ack_alert(user_id, m.group(1))
                return
            # /alerts/<id>/annotations POST — same ordering note as the
            # GET dispatch: check the more-specific annotations regex
            # before any /alerts/<id> sub-routes.
            m = _RE_ALERT_ANNOTATIONS.match(path)
            if m:
                self._create_annotation(user_id, m.group(1))
                return
            # /api/v1/rules/<rule_id>/escalations POST — check the
            # more-specific escalations regex BEFORE the bare /rules
            # POST handler. The regexes are anchored so the actual
            # overlap is zero; the ordering keeps intent explicit.
            m = _RE_RULE_ESCALATIONS.match(path)
            if m:
                self._create_escalation_step(user_id, m.group(1))
                return
            if _RE_RULES.match(path):
                self._save_rules(user_id)
                return
            if _RE_CHANNELS.match(path):
                self._save_channel(user_id)
                return
            m = _RE_REPORT_PUBLIC.match(path)
            if m:
                self._make_report_public(user_id, m.group(1))
                return
            if _RE_SCHEDULES.match(path):
                self._create_schedule(user_id)
                return
            if _RE_SILENCES.match(path):
                self._create_silence(user_id)
                return

            # POST on any other known route → 405. Unknown path → 404.
            if self._path_matches_any_route(path):
                _send_method_not_allowed(self)
                return
            _send_not_found(self)
        except Exception as exc:
            logger.exception(f"api POST handler crashed: {exc}")
            try:
                _send_internal_error(self)
            except Exception:
                pass

    def do_DELETE(self) -> None:  # noqa: N802
        """DELETE dispatch. The three DELETE endpoints are:
          /api/v1/rules                       → wipe the caller's rules
          /api/v1/channels/<channel_id>       → delete one channel
          /api/v1/reports/<report_id>/public  → revoke a public slug
        Any other known path → 405; unknown → 404."""
        try:
            path, _ = self._path_and_query()
            logger.info(f"api DELETE {path}")

            user_id = _authenticate(self)
            if user_id is None:
                _send_unauthorized(self)
                return

            # Per-user rate limit. Same bucket as GET / POST.
            if not _enforce_rate_limit(self, user_id):
                return

            # /api/v1/rules/<rule_id>/escalations DELETE — bulk-clear a
            # whole chain. Check BEFORE the bare /api/v1/rules handler
            # (which DELETEs all of the user's rules) so the more
            # specific path wins. The regexes are anchored so the
            # actual overlap is zero, but the ordering keeps intent
            # explicit and matches the same convention used for
            # /alerts/<id>/annotations vs /alerts.
            m = _RE_RULE_ESCALATIONS.match(path)
            if m:
                self._delete_chain(user_id, m.group(1))
                return
            if _RE_RULES.match(path):
                self._reset_rules(user_id)
                return
            m = _RE_CHANNEL_ONE.match(path)
            if m:
                self._delete_channel(user_id, m.group(1))
                return
            m = _RE_REPORT_PUBLIC.match(path)
            if m:
                self._revoke_report_public(user_id, m.group(1))
                return
            m = _RE_SCHEDULE_ONE.match(path)
            if m:
                self._delete_schedule(user_id, m.group(1))
                return
            m = _RE_SILENCE_ONE.match(path)
            if m:
                self._delete_silence(user_id, m.group(1))
                return
            m = _RE_ANNOTATION_ONE.match(path)
            if m:
                self._delete_annotation(user_id, m.group(1))
                return
            m = _RE_ESCALATION_ONE.match(path)
            if m:
                self._delete_escalation_step(user_id, m.group(1))
                return

            if self._path_matches_any_route(path):
                _send_method_not_allowed(self)
                return
            _send_not_found(self)
        except Exception as exc:
            logger.exception(f"api DELETE handler crashed: {exc}")
            try:
                _send_internal_error(self)
            except Exception:
                pass

    def _dispatch_unknown_method(self) -> None:
        """For PUT / PATCH / HEAD: 405 if the path is known
        under any verb, 404 otherwise. Matches the do_POST behaviour
        for non-ack paths."""
        try:
            path, _ = self._path_and_query()
            if self._path_matches_any_route(path):
                _send_method_not_allowed(self)
            else:
                _send_not_found(self)
        except Exception as exc:
            logger.exception(f"api unknown-method handler crashed: {exc}")
            try:
                _send_internal_error(self)
            except Exception:
                pass

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch_unknown_method()

    def do_PATCH(self) -> None:  # noqa: N802
        """PATCH dispatch — ``/api/v1/schedules/<id>`` (cron-schedule
        edits) and ``/api/v1/annotations/<id>`` (body edits) accept
        PATCH. Every other known path → 405; unknown → 404."""
        try:
            path, _ = self._path_and_query()
            logger.info(f"api PATCH {path}")

            user_id = _authenticate(self)
            if user_id is None:
                _send_unauthorized(self)
                return

            # Per-user rate limit. Same bucket as GET / POST / DELETE.
            if not _enforce_rate_limit(self, user_id):
                return

            m = _RE_SCHEDULE_ONE.match(path)
            if m:
                self._patch_schedule(user_id, m.group(1))
                return
            m = _RE_ANNOTATION_ONE.match(path)
            if m:
                self._edit_annotation(user_id, m.group(1))
                return
            if _RE_NOTIFICATION_PREFS.match(path):
                self._patch_notification_prefs(user_id)
                return

            if self._path_matches_any_route(path):
                _send_method_not_allowed(self)
                return
            _send_not_found(self)
        except Exception as exc:
            logger.exception(f"api PATCH handler crashed: {exc}")
            try:
                _send_internal_error(self)
            except Exception:
                pass

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch_unknown_method()

    # ── Endpoint: GET /api/v1/alerts ──────────────────────────────

    def _list_alerts(self, user_id: str, query: dict[str, list[str]]) -> None:
        """Return up to ``_MAX_ALERTS_RESPONSE`` alerts for ``user_id``
        created within the last ``window_days`` (default 30). Optional
        ``severity`` filter does case-insensitive match against the
        alert's severity field after the engine call — we filter in
        Python instead of pushing into ``load_alerts`` so the cap
        applies AFTER filtering (asking for ``severity=CRITICAL`` with
        a 365-day window must not silently miss criticals just because
        the unfiltered window contained 500 non-criticals first)."""
        try:
            window_days = _parse_int(
                query.get("window_days", [None])[0], default=30,
            )
            severity_raw = (query.get("severity", [""])[0] or "").strip()
            severity_filter = severity_raw.upper() if severity_raw else ""

            from engine.alert_engine_v2 import load_alerts
            alerts = load_alerts(max_age_days=window_days, user_id=user_id)
            if severity_filter:
                alerts = [a for a in alerts if a.severity == severity_filter]

            # Cap AFTER filtering. The dataclass-asdict path uses
            # vars() which works for the @dataclass shape and gives
            # us a plain dict per row without pulling in dataclasses
            # at import time.
            payload = [
                {
                    "alert_id":     a.alert_id,
                    "created_at":   a.created_at,
                    "alert_type":   a.alert_type,
                    "severity":     a.severity,
                    "title":        a.title,
                    "body":         a.body,
                    "ticker":       a.ticker,
                    "route_id":     a.route_id,
                    "port_locode":  a.port_locode,
                    "value":        a.value,
                    "threshold":    a.threshold,
                    "change_pct":   a.change_pct,
                    "acknowledged": a.acknowledged,
                }
                for a in alerts[:_MAX_ALERTS_RESPONSE]
            ]
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(f"api /alerts crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/alerts/<id> ─────────────────────────

    def _get_alert(self, user_id: str, alert_id: str) -> None:
        """Return a single alert as a dict, or 404 if not found in the
        user's scope. We re-use ``load_alerts`` with a long window and
        linear-scan for the id rather than reaching into
        ``get_alert_with_fire_count`` because that helper is NOT
        user-scoped and would let a token-holder read any alert by id
        — which contradicts the per-user contract."""
        try:
            from engine.alert_engine_v2 import load_alerts
            # 365 days is large enough to cover anything the UI would
            # surface; older alerts have been pruned by save_alerts.
            alerts = load_alerts(max_age_days=365, user_id=user_id)
            match = next((a for a in alerts if a.alert_id == alert_id), None)
            if match is None:
                _send_not_found(self)
                return
            payload = {
                "alert_id":     match.alert_id,
                "created_at":   match.created_at,
                "alert_type":   match.alert_type,
                "severity":     match.severity,
                "title":        match.title,
                "body":         match.body,
                "ticker":       match.ticker,
                "route_id":     match.route_id,
                "port_locode":  match.port_locode,
                "value":        match.value,
                "threshold":    match.threshold,
                "change_pct":   match.change_pct,
                "acknowledged": match.acknowledged,
            }
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(f"api /alerts/<id> crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: POST /api/v1/alerts/<id>/ack ────────────────────

    def _ack_alert(self, user_id: str, alert_id: str) -> None:
        """Acknowledge an alert in the caller's scope.

        The engine's ``acknowledge_alert`` UPDATE filters by user_id
        via ``scope_filter_sql`` — calling it with bob's token and
        alice's alert_id silently no-ops (zero rows updated). We
        return 200 unconditionally because surfacing "did this
        actually flip a row" would let a caller probe for the
        existence of another user's alerts.
        """
        try:
            from engine.alert_engine_v2 import acknowledge_alert
            acknowledge_alert(alert_id, user_id=user_id)
            _send_json(self, HTTPStatus.OK,
                       {"acknowledged": True, "alert_id": alert_id})
        except Exception as exc:
            logger.exception(f"api /alerts/<id>/ack crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/reports ─────────────────────────────

    def _list_reports(self, user_id: str, query: dict[str, list[str]]) -> None:
        """Return the caller's saved reports (metadata only — no HTML).

        ``limit`` truncates the response to the most-recent N entries.
        ``list_reports`` already sorts newest-first so the truncation
        respects that order."""
        try:
            limit = _parse_int(
                query.get("limit", [None])[0],
                default=_DEFAULT_REPORTS_LIMIT,
            )
            if limit < 0:
                limit = _DEFAULT_REPORTS_LIMIT
            from utils.report_history import list_reports
            entries = list_reports(user_id=user_id)
            payload = [
                {
                    "report_id":         e.report_id,
                    "generated_at":      e.generated_at,
                    "report_date":       e.report_date,
                    "sentiment_label":   e.sentiment_label,
                    "sentiment_score":   e.sentiment_score,
                    "risk_level":        e.risk_level,
                    "signal_count":      e.signal_count,
                    "data_quality":      e.data_quality,
                    "file_size_kb":      e.file_size_kb,
                    "public_slug":       e.public_slug,
                    "public_expires_at": e.public_expires_at,
                }
                for e in entries[:limit]
            ]
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(f"api /reports crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/reports/<id>/html ───────────────────

    def _get_report_html(
        self,
        user_id: str,
        report_id: str,
        query: dict[str, list[str]],
    ) -> None:
        """Return the raw HTML for a saved report. 404 when the
        report does not exist in the caller's scope (which collapses
        the unknown-id and cross-user cases into the same response,
        matching ``load_report_html``'s contract).

        When the report has been published with a password (v17 —
        ``public_password_hash`` set on the row), the caller must
        additionally supply the password via either the
        ``X-Report-Password`` request header OR the ``password`` query
        string parameter:

          * missing  → 401 with ``{"error": "password required"}``
          * wrong    → 401 with ``{"error": "wrong password"}``
          * correct  → 200 + HTML body, behaviour as today

        For an unprotected report the password input (if supplied) is
        ignored and the behaviour matches the pre-v17 path exactly.
        """
        try:
            from utils.report_history import load_report_html
            # Resolve the password state for this report before we
            # touch the file system. We deliberately query directly
            # rather than going through ``load_report_html`` so the
            # password check can short-circuit BEFORE any disk read.
            # The lookup is user-scoped via the same SQL helper so a
            # cross-user request still collapses to 404 (not 401) —
            # we do NOT want the password layer to leak the existence
            # of another user's report id.
            from state.db import get_connection
            from state.user_scope import scope_filter_sql

            scope_sql, scope_params = scope_filter_sql(user_id)
            conn = get_connection()
            row = conn.execute(
                f"SELECT public_password_hash, public_password_salt "
                f"FROM report_history WHERE report_id = ? {scope_sql}",
                (report_id, *scope_params),
            ).fetchone()
            if row is None:
                # Unknown id OR cross-user — collapse to 404 to match
                # the no-info-leak contract used by the v5/v17 surface.
                _send_not_found(self)
                return

            try:
                stored_hash = row["public_password_hash"]
            except (IndexError, KeyError):
                stored_hash = None
            try:
                stored_salt = row["public_password_salt"]
            except (IndexError, KeyError):
                stored_salt = None
            if stored_hash and stored_salt:
                supplied_pw = self.headers.get("X-Report-Password", "") or ""
                if not supplied_pw:
                    supplied_pw = (query.get("password", [""])[0] or "")
                if not supplied_pw:
                    _send_json(
                        self,
                        HTTPStatus.UNAUTHORIZED,
                        {"error": "password required"},
                    )
                    return
                from utils.report_history import _verify_public_password
                if not _verify_public_password(
                    supplied_pw, stored_hash, stored_salt,
                ):
                    _send_json(
                        self,
                        HTTPStatus.UNAUTHORIZED,
                        {"error": "wrong password"},
                    )
                    return

            html = load_report_html(report_id, user_id=user_id)
            if html is None:
                _send_not_found(self)
                return
            _send_html(self, HTTPStatus.OK, html)
        except Exception as exc:
            logger.exception(f"api /reports/<id>/html crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/reports/<id>/markdown ───────────────

    def _get_report_markdown(
        self,
        user_id: str,
        report_id: str,
        query: dict[str, list[str]],
    ) -> None:
        """Return a Markdown rendering of a saved report.

        Same auth + password contract as :meth:`_get_report_html`:

          * Bearer token resolves a user; the lookup is scoped to that
            user, so a cross-user id collapses to 404.
          * When the report row carries a non-empty
            ``public_password_hash``, the caller must supply the
            password via ``X-Report-Password`` or ``?password=``.
            Missing → 401 ``password required``; wrong → 401 ``wrong
            password``; correct → 200 + Markdown body. An unprotected
            report ignores any password input.

        The on-disk file is HTML — Markdown is reconstructed at
        request time from the ``ReportMeta`` row (the structured
        metadata: sentiment label/score, risk level, data quality,
        report_date, generated_at). We deliberately do NOT include
        the rendered HTML body in the Markdown — embedding HTML as a
        code block in Markdown produces an unreadable wall of tags
        that defeats the purpose of the export. Instead the Markdown
        carries the structured fields the renderer can format
        natively. This matches the spec's "Markdown is shareable on
        GitHub/Notion/Slack" use case — the audience wants the
        summary, not the source.

        Content-Type: ``text/markdown; charset=utf-8``.
        """
        try:
            # Auth + password gate — identical structure to the HTML
            # endpoint so a permission change here doesn't drift from
            # the existing surface.
            from state.db import get_connection
            from state.user_scope import scope_filter_sql

            scope_sql, scope_params = scope_filter_sql(user_id)
            conn = get_connection()
            row = conn.execute(
                f"SELECT report_id, generated_at, report_date, "
                f"sentiment_label, sentiment_score, risk_level, "
                f"signal_count, data_quality, "
                f"public_password_hash, public_password_salt "
                f"FROM report_history WHERE report_id = ? {scope_sql}",
                (report_id, *scope_params),
            ).fetchone()
            if row is None:
                _send_not_found(self)
                return

            try:
                stored_hash = row["public_password_hash"]
            except (IndexError, KeyError):
                stored_hash = None
            try:
                stored_salt = row["public_password_salt"]
            except (IndexError, KeyError):
                stored_salt = None
            if stored_hash and stored_salt:
                supplied_pw = self.headers.get("X-Report-Password", "") or ""
                if not supplied_pw:
                    supplied_pw = (query.get("password", [""])[0] or "")
                if not supplied_pw:
                    _send_json(
                        self,
                        HTTPStatus.UNAUTHORIZED,
                        {"error": "password required"},
                    )
                    return
                from utils.report_history import _verify_public_password
                if not _verify_public_password(
                    supplied_pw, stored_hash, stored_salt,
                ):
                    _send_json(
                        self,
                        HTTPStatus.UNAUTHORIZED,
                        {"error": "wrong password"},
                    )
                    return

            # Assemble a Markdown-renderable payload from the row.
            # Vault-encrypted fields are NOT in the report_history
            # table — this construction touches only the metadata
            # columns we explicitly select above, so by construction
            # nothing sensitive can leak into the Markdown.
            payload = {
                "title": (
                    f"Investor Report — {row['report_date']}"
                    if row["report_date"] else "Investor Report"
                ),
                "generated_at": row["generated_at"] or "",
                "sentiment_label": row["sentiment_label"] or "—",
                "sentiment_score": row["sentiment_score"],
                "risk_level": row["risk_level"] or "—",
                "signal_count": int(row["signal_count"] or 0),
                "data_quality": row["data_quality"] or "—",
                # The HTML body is intentionally NOT embedded — see the
                # docstring for the rationale.
                "executive_summary": (
                    f"Report generated {row['generated_at']}. "
                    f"{int(row['signal_count'] or 0)} alpha signals "
                    f"detected. Data quality: "
                    f"{row['data_quality'] or 'unknown'}. "
                    f"Open the HTML or PDF export for full prose."
                ),
                "signals": [],
                "routes": [],
                "macro": {},
                "key_findings": [],
            }
            from utils.markdown_export import report_to_markdown
            md_body = report_to_markdown(payload)
            _send_markdown(self, HTTPStatus.OK, md_body)
        except Exception as exc:
            logger.exception(f"api /reports/<id>/markdown crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/telemetry/llm ───────────────────────

    def _get_llm_telemetry(self, user_id: str, query: dict[str, list[str]]) -> None:
        """LLM-call telemetry summary for the caller's window."""
        try:
            window_days = _parse_int(
                query.get("window_days", [None])[0], default=7,
            )
            from engine.llm_telemetry import get_usage_summary
            summary = get_usage_summary(
                window_days=window_days, user_id=user_id,
            )
            _send_json(self, HTTPStatus.OK, summary)
        except Exception as exc:
            logger.exception(f"api /telemetry/llm crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/telemetry/perf ──────────────────────

    def _get_perf_telemetry(self, query: dict[str, list[str]]) -> None:
        """Render-performance telemetry summary. NOT user-scoped — the
        engine call itself takes no user_id (render telemetry is
        process-wide). We still require auth (it's behind the bearer
        check in do_GET) so the data is at least gated."""
        try:
            window_hours = _parse_int(
                query.get("window_hours", [None])[0], default=24,
            )
            from engine.perf_telemetry import get_perf_summary
            summary = get_perf_summary(window_hours=window_hours)
            _send_json(self, HTTPStatus.OK, summary)
        except Exception as exc:
            logger.exception(f"api /telemetry/perf crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/audit ───────────────────────────────

    def _list_audit(self, user_id: str, query: dict[str, list[str]]) -> None:
        """Return audit-log rows scoped to the caller.

        Query parameters:
          * ``limit``  — cap on rows returned. Default 100, hard max
            ``_MAX_AUDIT_LIMIT`` (1000). Values <= 0 fall back to the
            default. We clamp at the API layer so a malicious caller
            asking ``limit=10**9`` never gets past us into the engine.
          * ``action`` — optional filter on the action verb (e.g.
            ``login_success``, ``save_rules``). Forwarded into
            ``query_audit``'s native ``action`` parameter so the
            filter applies BEFORE the SQL LIMIT — critical because a
            user with thousands of rows shouldn't get an empty result
            when their last 100 happen to be ``save_rules`` events.

        Per-user: ``user_id`` is taken from the bearer token and
        passed verbatim into ``query_audit`` — Alice cannot see Bob's
        rows. ``detail_json`` is whatever the recorder stored; channel
        ``target`` (Slack webhook URL / PagerDuty key / email) is
        already suppressed at the recording site in
        ``engine.alert_delivery.save_channel`` so we don't need to
        re-redact here.
        """
        try:
            limit = _parse_int(
                query.get("limit", [None])[0], default=_DEFAULT_AUDIT_LIMIT,
            )
            if limit <= 0:
                limit = _DEFAULT_AUDIT_LIMIT
            if limit > _MAX_AUDIT_LIMIT:
                limit = _MAX_AUDIT_LIMIT
            action_raw = (query.get("action", [""])[0] or "").strip()
            action_filter: Optional[str] = action_raw or None

            from auth.audit import query_audit
            events = query_audit(
                user_id=user_id, action=action_filter, limit=limit,
            )
            payload = {
                "items": [
                    {
                        "event_id":    e.event_id,
                        "created_at":  e.created_at,
                        "user_id":     e.user_id,
                        "action":      e.action,
                        "entity_type": e.entity_type,
                        "entity_id":   e.entity_id,
                        "detail_json": e.detail_json,
                    }
                    for e in events
                ],
                "count": len(events),
            }
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(f"api /audit crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/audit/export ────────────────────────

    def _export_audit(self, user_id: str, query: dict[str, list[str]]) -> None:
        """SIEM-friendly export of the caller's audit-log rows.

        Same query-param surface as ``/audit`` (``action``, ``limit``)
        plus ``since`` / ``until`` (ISO-8601 strings) and a ``format``
        switch:

          * ``format=jsonl`` (default) — line-delimited JSON, one
            event per line, ``Content-Type: application/x-ndjson;
            charset=utf-8``. The wire shape Splunk / Vector / Loki
            scrapers consume natively.
          * ``format=json`` — the existing ``{items: [...], count: N}``
            envelope with ``Content-Type: application/json``. Same
            shape as ``/audit`` but with the additional ``since`` /
            ``until`` filtering applied.

        Per-user scoping is enforced by passing the bearer-resolved
        ``user_id`` straight through to ``export_audit_to_jsonl`` /
        ``query_audit`` — Alice cannot pull Bob's rows by hitting
        this endpoint.

        Streaming: for very large bodies (over 100 KB) we use chunked
        Transfer-Encoding via ``http.server`` defaults — the
        ``Content-Length`` header is omitted and the body is written
        directly to ``wfile`` in a single ``write`` call. Below the
        threshold we set ``Content-Length`` so a misbehaving proxy
        doesn't have to negotiate the chunked encoding for a small
        payload.
        """
        try:
            # Parse + clamp filters. We deliberately reuse the same
            # default / max as /audit so the two endpoints stay
            # consistent under the same query knobs.
            limit = _parse_int(
                query.get("limit", [None])[0],
                default=_DEFAULT_AUDIT_LIMIT,
            )
            if limit <= 0:
                limit = _DEFAULT_AUDIT_LIMIT
            if limit > _MAX_AUDIT_LIMIT:
                limit = _MAX_AUDIT_LIMIT

            action_raw = (query.get("action", [""])[0] or "").strip()
            action_filter: Optional[str] = action_raw or None

            since_raw = (query.get("since", [""])[0] or "").strip()
            since_filter: Optional[str] = since_raw or None

            until_raw = (query.get("until", [""])[0] or "").strip()
            until_filter: Optional[str] = until_raw or None

            fmt = (query.get("format", [""])[0] or "jsonl").strip().lower()
            if fmt not in ("jsonl", "json"):
                _send_bad_request(
                    self,
                    "format must be one of: jsonl, json",
                )
                return

            from utils.audit_export import export_audit_to_jsonl

            if fmt == "jsonl":
                body = export_audit_to_jsonl(
                    user_id=user_id,
                    action=action_filter,
                    since=since_filter,
                    until=until_filter,
                    limit=limit,
                )
                # 100 KB threshold for chunked vs Content-Length —
                # below this it's cheap to include Content-Length so
                # naive clients see the full size up front; above
                # it we go chunked to avoid buffering the whole body.
                # The framing is the same to the consumer in both
                # cases because we always write the body in one
                # wfile.write call regardless.
                self.send_response(HTTPStatus.OK)
                self.send_header(
                    "Content-Type",
                    "application/x-ndjson; charset=utf-8",
                )
                if len(body) > 100_000:
                    # Chunked Transfer-Encoding for large bodies.
                    # We frame the body ourselves (one chunk + the
                    # terminating zero-length chunk) so the stdlib
                    # http.server doesn't need an explicit
                    # Content-Length.
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    if body:
                        # One chunk for the whole payload — the
                        # downstream scrapers process the JSONL
                        # line-by-line regardless of chunk framing.
                        self.wfile.write(f"{len(body):X}\r\n".encode("ascii"))
                        self.wfile.write(body)
                        self.wfile.write(b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                return

            # fmt == "json" → JSON envelope. We re-run the query so
            # we get AuditEvent objects rather than parsing the JSONL
            # back (which would lose the dataclass typing).
            from auth.audit import query_audit
            events = query_audit(
                user_id=user_id,
                action=action_filter,
                since=since_filter,
                limit=limit,
            )
            if until_filter:
                events = [e for e in events if e.created_at < until_filter]
            payload = {
                "items": [
                    {
                        "event_id":    e.event_id,
                        "created_at":  e.created_at,
                        "user_id":     e.user_id,
                        "action":      e.action,
                        "entity_type": e.entity_type,
                        "entity_id":   e.entity_id,
                        "detail_json": e.detail_json,
                    }
                    for e in events
                ],
                "count": len(events),
            }
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(f"api /audit/export crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/incidents ───────────────────────────

    def _list_incidents(self, user_id: str, query: dict[str, list[str]]) -> None:
        """Return correlated alert-incidents for the caller's window.

        Query parameters:
          * ``window`` — look-back in DAYS (default 7). The underlying
            ``get_recent_incidents`` takes ``window_days``; the API
            param is unsuffixed ``window`` to match the spec.

        Per-user: ``user_id`` is threaded into ``get_recent_incidents``
        which forwards it to ``load_alerts`` — alice's incidents view
        cannot include bob's alerts.

        The ``alerts`` list inside each incident is intentionally
        emitted as a list of dicts (not the raw dataclass) so the JSON
        payload is stable across engine refactors.
        """
        try:
            window_days = _parse_int(
                query.get("window", [None])[0], default=7,
            )

            from engine.alert_correlator import get_recent_incidents
            incidents = get_recent_incidents(
                window_days=window_days, user_id=user_id,
            )
            payload = {
                "items": [
                    {
                        "incident_id":         inc.incident_id,
                        "started_at":          inc.started_at,
                        "severity_max":        inc.severity_max,
                        "alert_count":         inc.alert_count,
                        "dominant_alert_type": inc.dominant_alert_type,
                        "entities_touched":    inc.entities_touched,
                        "alert_ids":           [a.alert_id for a in inc.alerts],
                    }
                    for inc in incidents
                ],
                "count": len(incidents),
            }
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(f"api /incidents crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/source-health ───────────────────────

    def _get_source_health(self, query: dict[str, list[str]]) -> None:
        """Return per-source liveness/freshness telemetry.

        Query parameters:
          * ``window_hours`` — look-back in hours (default 24).

        NOT user-scoped: source-health is global platform telemetry
        (every user looks at the same FRED / yfinance / canal feeds),
        so different users see the same response. We still require
        auth — the endpoint is behind the bearer check in ``do_GET``.

        Returned shape: ``{"items": [...], "count": N}`` where each
        item is one source with its bucketed counters. This is a
        deliberate flattening of the engine's ``by_source`` dict
        (which keys by source name) into a stable list shape that
        matches the rest of the API's envelope contract; ``source``
        is added as a top-level field on each item.
        """
        try:
            window_hours = _parse_int(
                query.get("window_hours", [None])[0], default=24,
            )

            from engine.source_health import get_health_summary
            summary = get_health_summary(window_hours=window_hours) or {}
            by_source = summary.get("by_source") or {}
            current_outages = summary.get("current_outages") or []

            items: list[dict[str, Any]] = []
            for src, row in by_source.items():
                if not isinstance(row, dict):
                    continue
                items.append({
                    "source":           src,
                    "count":            row.get("count", 0),
                    "up_count":         row.get("up_count", 0),
                    "degraded_count":   row.get("degraded_count", 0),
                    "down_count":       row.get("down_count", 0),
                    "avg_duration_ms":  row.get("avg_duration_ms", 0.0),
                    "last_status":      row.get("last_status", ""),
                    "last_started_at":  row.get("last_started_at", ""),
                    "is_outage":        src in current_outages,
                })
            # Stable ordering — alphabetical by source so dashboards
            # diff cleanly across polls.
            items.sort(key=lambda r: str(r.get("source", "")))

            payload = {
                "items":           items,
                "count":           len(items),
                "window_hours":    summary.get("window_hours", window_hours),
                "total_pings":     summary.get("total_pings", 0),
                "current_outages": list(current_outages),
            }
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(f"api /source-health crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/health ──────────────────────────────

    def _health(self) -> None:
        """Public liveness + system-health probe.

        Mirrors the shape of ``worker.webhook_listener``'s ``/health``
        so a single LB / k8s probe template can point at either port.
        Each underlying telemetry call is wrapped so a single failing
        layer (e.g. perf tables not yet created) doesn't cascade into
        a 503 — only ``count_users`` failure flips status to 'down'.
        """
        try:
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
                logger.warning(f"api /health: count_users failed: {exc}")

            if db_down:
                _send_json(
                    self,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"status": "down", "error": db_error},
                )
                return

            schema_version: int = 0
            try:
                from state.db import SCHEMA_VERSION
                schema_version = int(SCHEMA_VERSION)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"api /health: SCHEMA_VERSION read failed: {exc}")

            unacked_critical: int = 0
            try:
                from engine.alert_engine_v2 import load_alerts
                # Global (user_id=None → resolved to "") since this
                # is a SYSTEM health probe, not a user view. The
                # webhook listener does the same thing here.
                alerts = load_alerts(max_age_days=30)
                unacked_critical = sum(
                    1 for a in alerts
                    if getattr(a, "severity", "") == "CRITICAL"
                    and not getattr(a, "acknowledged", False)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"api /health: load_alerts failed: {exc}")

            recent_success_rate: Optional[float] = None
            try:
                from engine.perf_telemetry import get_perf_summary
                perf = get_perf_summary(window_hours=1) or {}
                if perf.get("total_renders", 0) > 0:
                    raw = perf.get("success_rate")
                    if raw is not None:
                        recent_success_rate = float(raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"api /health: get_perf_summary failed: {exc}")

            current_outages: list = []
            try:
                from engine.source_health import get_health_summary
                health = get_health_summary() or {}
                outages = health.get("current_outages", [])
                if isinstance(outages, list):
                    current_outages = list(outages)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"api /health: get_health_summary failed: {exc}")

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
            logger.exception(f"api /health crashed: {exc}")
            try:
                _send_json(
                    self,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"status": "down", "error": f"{type(exc).__name__}: {exc}"},
                )
            except Exception:
                pass


    # ── Endpoint: GET /api/v1/rules ───────────────────────────────

    def _list_rules(self, user_id: str) -> None:
        """Return the caller's persisted alert rules as a JSON list.
        Empty list when the user has no rules (matches ``load_rules``)."""
        try:
            from engine.alert_engine_v2 import load_rules
            rules = load_rules(user_id=user_id)
            _send_json(self, HTTPStatus.OK, rules)
        except Exception as exc:
            logger.exception(f"api GET /rules crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: POST /api/v1/rules ──────────────────────────────

    def _save_rules(self, user_id: str) -> None:
        """Replace the caller's rule set with the posted list.

        Body must be ``application/json`` and decode to a list of
        rule dicts; anything else → 415 / 400. The engine call is
        per-user (only wipes + rewrites rows in this user's scope —
        legacy rules belonging to ``user_id=""`` are also adopted by
        this call, matching ``save_rules``'s contract).
        """
        try:
            body = _read_json_body(self)
            if body is _BODY_BAD_CTYPE:
                _send_unsupported_media(self)
                return
            if body is _BODY_BAD_JSON:
                _send_bad_request(self, "malformed json")
                return
            if not isinstance(body, list):
                _send_bad_request(self, "body must be a JSON array of rule dicts")
                return
            from engine.alert_engine_v2 import save_rules
            save_rules(body, user_id=user_id)
            _send_json(self, HTTPStatus.OK,
                       {"saved": True, "count": len(body)})
        except Exception as exc:
            logger.exception(f"api POST /rules crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: DELETE /api/v1/rules ────────────────────────────

    def _reset_rules(self, user_id: str) -> None:
        """Wipe the caller's rules.

        We deliberately route this through ``save_rules([],
        user_id=user_id)`` rather than ``reset_rules()`` — the latter
        is a global, non-user-scoped wipe (per its docstring), which
        would let any token-holder erase every user's rules. The
        per-user save-with-empty-list achieves the same observable
        outcome for the caller WITHOUT crossing the scope boundary.
        """
        try:
            from engine.alert_engine_v2 import save_rules
            save_rules([], user_id=user_id)
            _send_json(self, HTTPStatus.OK, {"reset": True})
        except Exception as exc:
            logger.exception(f"api DELETE /rules crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/channels ────────────────────────────

    def _list_channels(self, user_id: str) -> None:
        """Return the caller's delivery channels as a JSON list.

        Each row carries the same fields the UI sees, except the
        ``target`` field — that's a Slack webhook URL / PagerDuty
        integration key / email address and is the secret we are
        protecting. Withholding it from the API response means a
        compromised token can enumerate WHAT channels exist (so
        users can list / delete them) without leaking the delivery
        secret to the attacker.
        """
        try:
            from engine.alert_delivery import load_channels
            channels = load_channels(user_id=user_id)
            payload = [
                {
                    "channel_id":              c.channel_id,
                    "name":                    c.name,
                    "kind":                    c.kind,
                    "severity_threshold":      c.severity_threshold,
                    "enabled":                 c.enabled,
                    "created_at":              c.created_at,
                    "digest_mode":             c.digest_mode,
                    "quiet_start":             c.quiet_start,
                    "quiet_end":               c.quiet_end,
                    "quiet_override_critical": c.quiet_override_critical,
                }
                for c in channels
            ]
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(f"api GET /channels crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: POST /api/v1/channels ───────────────────────────

    def _save_channel(self, user_id: str) -> None:
        """Insert / upsert a channel from a posted ``DeliveryChannel``
        dict. ``channel_id`` and ``target`` are required; everything
        else falls back to the dataclass defaults.
        """
        try:
            body = _read_json_body(self)
            if body is _BODY_BAD_CTYPE:
                _send_unsupported_media(self)
                return
            if body is _BODY_BAD_JSON:
                _send_bad_request(self, "malformed json")
                return
            if not isinstance(body, dict):
                _send_bad_request(self, "body must be a JSON object")
                return
            channel_id = (body.get("channel_id") or "").strip()
            if not channel_id:
                _send_bad_request(self, "channel_id is required")
                return
            from engine.alert_delivery import DeliveryChannel, save_channel
            # Build the dataclass with all defaults — fields the caller
            # didn't supply fall back to the DeliveryChannel defaults
            # (``digest_mode='immediate'``, ``enabled=True``, …) which
            # matches how the UI persists a fresh row.
            channel = DeliveryChannel(
                channel_id=channel_id,
                name=str(body.get("name") or ""),
                kind=str(body.get("kind") or ""),
                target=str(body.get("target") or ""),
                severity_threshold=str(body.get("severity_threshold") or "LOW"),
                enabled=bool(body.get("enabled", True)),
                created_at=str(body.get("created_at") or ""),
                digest_mode=str(body.get("digest_mode") or "immediate"),
                quiet_start=str(body.get("quiet_start") or ""),
                quiet_end=str(body.get("quiet_end") or ""),
                quiet_override_critical=bool(body.get("quiet_override_critical", True)),
            )
            save_channel(channel, user_id=user_id)
            _send_json(self, HTTPStatus.OK,
                       {"saved": True, "channel_id": channel_id})
        except Exception as exc:
            logger.exception(f"api POST /channels crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: DELETE /api/v1/channels/<id> ────────────────────

    def _delete_channel(self, user_id: str, channel_id: str) -> None:
        """Remove a channel by id, scoped to the caller. Cross-user
        deletes silently no-op (engine's ``scope_filter_sql``); same
        200 response either way so a caller can't probe for other
        users' channel ids by status code."""
        try:
            from engine.alert_delivery import delete_channel
            delete_channel(channel_id, user_id=user_id)
            _send_json(self, HTTPStatus.OK,
                       {"deleted": True, "channel_id": channel_id})
        except Exception as exc:
            logger.exception(f"api DELETE /channels/<id> crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: POST /api/v1/reports/<id>/public ────────────────

    def _make_report_public(self, user_id: str, report_id: str) -> None:
        """Generate a public-share slug for one of the caller's reports.

        Body is OPTIONAL — when absent or empty, defaults to 30 days
        and no password. When present, must be valid JSON dict with
        optional ``expires_in_days`` (int) and optional ``password``
        (str) fields. When ``password`` is a non-empty string, the
        share link additionally requires the password before the
        report can be viewed via ``GET /api/v1/reports/<id>/html``.
        Returns 404 when the report doesn't belong to the caller
        (matches ``make_public``'s scope check).
        """
        try:
            body = _read_json_body(self)
            if body is _BODY_BAD_CTYPE:
                _send_unsupported_media(self)
                return
            if body is _BODY_BAD_JSON:
                _send_bad_request(self, "malformed json")
                return
            expires_in_days = 30
            password: Optional[str] = None
            if isinstance(body, dict):
                raw = body.get("expires_in_days", 30)
                try:
                    expires_in_days = int(raw)
                except (TypeError, ValueError):
                    _send_bad_request(self, "expires_in_days must be an int")
                    return
                raw_pw = body.get("password")
                if raw_pw is not None:
                    if not isinstance(raw_pw, str):
                        _send_bad_request(self, "password must be a string")
                        return
                    # Treat empty-string as "no password" so callers
                    # don't accidentally lock the link with an unusable
                    # blank password.
                    password = raw_pw if raw_pw else None
            from utils.report_history import make_public
            slug = make_public(
                report_id,
                expires_in_days=expires_in_days,
                password=password,
                user_id=user_id,
            )
            if slug is None:
                # Could be: unknown id, cross-user, expires<=0,
                # internal error. All collapse to 404 (preserves the
                # "no info leak about other users' report ids"
                # contract that the GET-html endpoint uses).
                _send_not_found(self)
                return
            _send_json(self, HTTPStatus.OK, {"slug": slug})
        except Exception as exc:
            logger.exception(f"api POST /reports/<id>/public crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: DELETE /api/v1/reports/<id>/public ──────────────

    def _revoke_report_public(self, user_id: str, report_id: str) -> None:
        """Clear the public-share slug + expiry for one of the
        caller's reports. 404 when the report doesn't belong to the
        caller (mirror of ``make_public``'s 404 path)."""
        try:
            from utils.report_history import revoke_public
            ok = revoke_public(report_id, user_id=user_id)
            if not ok:
                _send_not_found(self)
                return
            _send_json(self, HTTPStatus.OK, {"revoked": True})
        except Exception as exc:
            logger.exception(f"api DELETE /reports/<id>/public crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/schedules ───────────────────────────

    def _schedule_to_dict(self, sched) -> dict:
        """Project a ReportSchedule onto the wire shape used by every
        schedules endpoint. Kept as a helper because the list / create
        / patch endpoints all need the same projection — copying it
        would invite drift the next time a field is added."""
        return {
            "schedule_id":      sched.schedule_id,
            "user_id":          sched.user_id,
            "name":             sched.name,
            "cron_expr":        sched.cron_expr,
            "enabled":          bool(sched.enabled),
            "last_run_at":      sched.last_run_at,
            "last_run_status":  sched.last_run_status,
            "last_run_message": sched.last_run_message,
            "next_run_at":      sched.next_run_at,
            "created_at":       sched.created_at,
            "updated_at":       sched.updated_at,
        }

    def _list_schedules(self, user_id: str) -> None:
        """Return the caller's report schedules as a JSON list."""
        try:
            from engine.report_scheduler import load_schedules
            schedules = load_schedules(user_id=user_id)
            payload = [self._schedule_to_dict(s) for s in schedules]
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(f"api GET /schedules crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: POST /api/v1/schedules ──────────────────────────

    def _create_schedule(self, user_id: str) -> None:
        """Create one schedule from a posted JSON dict.

        Body fields:
          * ``name``       (str, required) — operator-facing label.
          * ``cron_expr``  (str, required) — 5-field cron string.
          * ``enabled``    (bool, optional, defaults to True).

        Returns ``{"saved": True, "schedule_id": "..."}`` on success.
        Invalid cron expression → 400 with the parser's error string.
        Missing name / cron_expr → 400.
        """
        try:
            body = _read_json_body(self)
            if body is _BODY_BAD_CTYPE:
                _send_unsupported_media(self)
                return
            if body is _BODY_BAD_JSON:
                _send_bad_request(self, "malformed json")
                return
            if not isinstance(body, dict):
                _send_bad_request(self, "body must be a JSON object")
                return
            name = (body.get("name") or "").strip()
            cron_expr = (body.get("cron_expr") or "").strip()
            if not name:
                _send_bad_request(self, "name is required")
                return
            if not cron_expr:
                _send_bad_request(self, "cron_expr is required")
                return
            enabled = bool(body.get("enabled", True))

            from engine.report_scheduler import (
                ReportSchedule,
                get_schedule,
                new_schedule_id,
                save_schedule,
                validate_cron_expr,
            )
            ok, err = validate_cron_expr(cron_expr)
            if not ok:
                _send_bad_request(self, f"invalid cron_expr: {err}")
                return

            sched = ReportSchedule(
                schedule_id=new_schedule_id(),
                user_id=user_id,
                name=name,
                cron_expr=cron_expr,
                enabled=enabled,
            )
            if not save_schedule(sched):
                _send_internal_error(self)
                return
            # Re-load so the returned row carries the computed
            # next_run_at + created_at that save_schedule populated.
            persisted = get_schedule(sched.schedule_id, user_id=user_id)
            _send_json(
                self,
                HTTPStatus.OK,
                {
                    "saved": True,
                    "schedule_id": sched.schedule_id,
                    "schedule": self._schedule_to_dict(persisted) if persisted else None,
                },
            )
        except Exception as exc:
            logger.exception(f"api POST /schedules crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: DELETE /api/v1/schedules/<id> ───────────────────

    def _delete_schedule(self, user_id: str, schedule_id: str) -> None:
        """Delete one schedule by id, scoped to the caller.

        Cross-user deletes return 404 (the schedule does not exist in
        the caller's scope). The 200 path returns ``{"deleted": True,
        "schedule_id": "..."}``.
        """
        try:
            from engine.report_scheduler import delete_schedule
            ok = delete_schedule(schedule_id, user_id=user_id)
            if not ok:
                _send_not_found(self)
                return
            _send_json(self, HTTPStatus.OK,
                       {"deleted": True, "schedule_id": schedule_id})
        except Exception as exc:
            logger.exception(f"api DELETE /schedules/<id> crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: PATCH /api/v1/schedules/<id> ────────────────────

    def _patch_schedule(self, user_id: str, schedule_id: str) -> None:
        """Update name / cron_expr / enabled on one schedule.

        Body fields are all OPTIONAL — only the supplied ones are
        updated; the rest are preserved. An empty / null body → 400.
        Invalid cron_expr → 400. Unknown / cross-user id → 404.
        """
        try:
            body = _read_json_body(self)
            if body is _BODY_BAD_CTYPE:
                _send_unsupported_media(self)
                return
            if body is _BODY_BAD_JSON:
                _send_bad_request(self, "malformed json")
                return
            if not isinstance(body, dict):
                _send_bad_request(self, "body must be a JSON object")
                return

            from engine.report_scheduler import (
                get_schedule,
                save_schedule,
                validate_cron_expr,
            )
            existing = get_schedule(schedule_id, user_id=user_id)
            if existing is None:
                _send_not_found(self)
                return

            if "name" in body:
                name = body.get("name")
                if not isinstance(name, str) or not name.strip():
                    _send_bad_request(self, "name must be a non-empty string")
                    return
                existing.name = name.strip()
            if "cron_expr" in body:
                cron_expr = body.get("cron_expr")
                if not isinstance(cron_expr, str):
                    _send_bad_request(self, "cron_expr must be a string")
                    return
                cron_expr = cron_expr.strip()
                ok, err = validate_cron_expr(cron_expr)
                if not ok:
                    _send_bad_request(self, f"invalid cron_expr: {err}")
                    return
                existing.cron_expr = cron_expr
            if "enabled" in body:
                existing.enabled = bool(body.get("enabled"))

            if not save_schedule(existing):
                _send_internal_error(self)
                return
            persisted = get_schedule(schedule_id, user_id=user_id)
            _send_json(
                self,
                HTTPStatus.OK,
                {
                    "updated": True,
                    "schedule_id": schedule_id,
                    "schedule": self._schedule_to_dict(persisted) if persisted else None,
                },
            )
        except Exception as exc:
            logger.exception(f"api PATCH /schedules/<id> crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/silences ────────────────────────────

    def _silence_to_dict(self, silence) -> dict:
        """Project an AlertSilence onto the wire shape. Kept as a
        helper because both ``_list_silences`` and ``_create_silence``
        need the same projection."""
        return {
            "silence_id":          silence.silence_id,
            "user_id":             silence.user_id,
            "rule_id":             silence.rule_id,
            "ticker":              silence.ticker,
            "severity":            silence.severity,
            "reason":              silence.reason,
            "starts_at":           silence.starts_at,
            "expires_at":          silence.expires_at,
            "created_at":          silence.created_at,
            "created_by_user_id":  silence.created_by_user_id,
        }

    def _list_silences(self, user_id: str, query: dict[str, list[str]]) -> None:
        """Return the caller's silences as a JSON list.

        Optional ``include_expired=true`` query parameter surfaces the
        audit-retention tail (silences whose expires_at has already
        passed but cleanup_expired_silences has not yet swept them
        out). Default is active-only.
        """
        try:
            from engine.alert_silences import list_silences

            include_expired_raw = query.get("include_expired", [""])[0].lower()
            include_expired = include_expired_raw in ("1", "true", "yes", "on")

            silences = list_silences(
                user_id=user_id, include_expired=include_expired,
            )
            payload = [self._silence_to_dict(s) for s in silences]
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(f"api GET /silences crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: POST /api/v1/silences ───────────────────────────

    def _create_silence(self, user_id: str) -> None:
        """Create one silence from a posted JSON dict.

        Body fields:
          * ``duration_minutes`` (int, required) — silence lifetime.
            Non-positive values are clamped to 1 by the engine layer.
          * ``rule_id``  (str, optional) — match key (omit = all rules).
          * ``ticker``   (str, optional) — match key (omit = all).
          * ``severity`` (str, optional) — match key (omit = all).
          * ``reason``   (str, optional) — operator note.

        Returns ``{"saved": True, "silence_id": "...", "silence": {...}}``
        on success. Missing ``duration_minutes`` → 400. Engine failure
        → 500. The caller's ``user_id`` is stamped as both the
        silence owner AND ``created_by_user_id`` — there is no admin
        path to create a silence on someone else's behalf via the API.
        """
        try:
            body = _read_json_body(self)
            if body is _BODY_BAD_CTYPE:
                _send_unsupported_media(self)
                return
            if body is _BODY_BAD_JSON:
                _send_bad_request(self, "malformed json")
                return
            if not isinstance(body, dict):
                _send_bad_request(self, "body must be a JSON object")
                return

            raw_duration = body.get("duration_minutes")
            if raw_duration is None:
                _send_bad_request(self, "duration_minutes is required")
                return
            try:
                duration_minutes = int(raw_duration)
            except (TypeError, ValueError):
                _send_bad_request(self, "duration_minutes must be an int")
                return

            rule_id = body.get("rule_id") or None
            ticker = body.get("ticker") or None
            severity = body.get("severity") or None
            reason = body.get("reason") or None

            from engine.alert_silences import create_silence
            silence = create_silence(
                user_id=user_id,
                rule_id=rule_id,
                ticker=ticker,
                severity=severity,
                reason=reason,
                duration_minutes=duration_minutes,
                created_by_user_id=user_id,
            )
            if silence is None:
                _send_internal_error(self)
                return
            _send_json(
                self,
                HTTPStatus.OK,
                {
                    "saved": True,
                    "silence_id": silence.silence_id,
                    "silence": self._silence_to_dict(silence),
                },
            )
        except Exception as exc:
            logger.exception(f"api POST /silences crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: DELETE /api/v1/silences/<id> ────────────────────

    def _delete_silence(self, user_id: str, silence_id: str) -> None:
        """Cancel a silence early. Per-user scoped — alice cannot
        delete bob's silence. Cross-user attempts return 404 so a
        probing caller cannot enumerate other users' silence ids by
        404 vs 403."""
        try:
            from engine.alert_silences import delete_silence
            ok = delete_silence(silence_id, user_id=user_id)
            if not ok:
                _send_not_found(self)
                return
            _send_json(self, HTTPStatus.OK,
                       {"deleted": True, "silence_id": silence_id})
        except Exception as exc:
            logger.exception(f"api DELETE /silences/<id> crashed: {exc}")
            _send_internal_error(self)

    # ── Endpoint: GET /api/v1/alerts/<id>/annotations ─────────────

    def _annotation_to_dict(self, annotation) -> dict:
        """Project an AlertAnnotation onto the wire shape. Centralised
        because list / create / edit all need the same projection."""
        return {
            "annotation_id":   annotation.annotation_id,
            "alert_id":        annotation.alert_id,
            "user_id":         annotation.user_id,
            "author_user_id":  annotation.author_user_id,
            "body":            annotation.body,
            "created_at":      annotation.created_at,
            "edited_at":       annotation.edited_at,
        }

    def _list_annotations(self, user_id: str, alert_id: str) -> None:
        """Return the annotation thread for one alert in created_at
        ASC order. Per-user scoped — alice cannot see annotations
        on bob's alerts even by guessing the alert_id."""
        try:
            from engine.alert_annotations import list_annotations
            annotations = list_annotations(alert_id, user_id=user_id)
            payload = [self._annotation_to_dict(a) for a in annotations]
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(
                f"api GET /alerts/<id>/annotations crashed: {exc}"
            )
            _send_internal_error(self)

    # ── Endpoint: POST /api/v1/alerts/<id>/annotations ────────────

    def _create_annotation(self, user_id: str, alert_id: str) -> None:
        """Add one annotation to an alert. The caller's user_id is
        stamped as BOTH the alert owner AND the author — there is no
        admin path via the API to annotate on someone else's
        behalf.

        Body fields:
          * ``body`` (str, required) — the annotation text. Silently
            truncated at 4000 chars by the engine layer.

        Returns ``{"saved": True, "annotation_id": "...",
        "annotation": {...}}`` on success. Missing ``body`` → 400.
        Empty / whitespace-only body → 400 (an empty note carries no
        signal). Engine failure → 500. The body itself is NEVER
        logged.
        """
        try:
            body = _read_json_body(self)
            if body is _BODY_BAD_CTYPE:
                _send_unsupported_media(self)
                return
            if body is _BODY_BAD_JSON:
                _send_bad_request(self, "malformed json")
                return
            if not isinstance(body, dict):
                _send_bad_request(self, "body must be a JSON object")
                return

            raw_body = body.get("body")
            if raw_body is None:
                _send_bad_request(self, "body is required")
                return
            if not isinstance(raw_body, str):
                _send_bad_request(self, "body must be a string")
                return
            if not raw_body.strip():
                _send_bad_request(self, "body must not be empty")
                return

            from engine.alert_annotations import add_annotation
            saved = add_annotation(
                alert_id,
                raw_body,
                user_id=user_id,
                author_user_id=user_id,
            )
            if saved is None:
                _send_internal_error(self)
                return
            _send_json(
                self,
                HTTPStatus.OK,
                {
                    "saved": True,
                    "annotation_id": saved.annotation_id,
                    "annotation": self._annotation_to_dict(saved),
                },
            )
        except Exception as exc:
            # Body is NOT included in the log message.
            logger.exception(
                f"api POST /alerts/<id>/annotations crashed: {exc}"
            )
            _send_internal_error(self)

    # ── Endpoint: PATCH /api/v1/annotations/<id> ──────────────────

    def _edit_annotation(self, user_id: str, annotation_id: str) -> None:
        """Replace the body of one annotation. Author-only — the
        caller must be the original author. Cross-author / cross-user
        attempts return 404 (no-leak contract identical to silences /
        schedules).

        Body fields:
          * ``body`` (str, required) — the new annotation text.
            Silently truncated at 4000 chars; empty / whitespace
            rejected as 400 (delete the row instead).

        Returns ``{"updated": True, "annotation_id": "..."}`` on
        success. The body itself is NEVER logged.
        """
        try:
            body = _read_json_body(self)
            if body is _BODY_BAD_CTYPE:
                _send_unsupported_media(self)
                return
            if body is _BODY_BAD_JSON:
                _send_bad_request(self, "malformed json")
                return
            if not isinstance(body, dict):
                _send_bad_request(self, "body must be a JSON object")
                return

            raw_body = body.get("body")
            if raw_body is None:
                _send_bad_request(self, "body is required")
                return
            if not isinstance(raw_body, str):
                _send_bad_request(self, "body must be a string")
                return
            if not raw_body.strip():
                _send_bad_request(self, "body must not be empty")
                return

            from engine.alert_annotations import edit_annotation
            ok = edit_annotation(
                annotation_id,
                raw_body,
                user_id=user_id,
                author_user_id=user_id,
            )
            if not ok:
                _send_not_found(self)
                return
            _send_json(
                self,
                HTTPStatus.OK,
                {"updated": True, "annotation_id": annotation_id},
            )
        except Exception as exc:
            logger.exception(
                f"api PATCH /annotations/<id> crashed: {exc}"
            )
            _send_internal_error(self)

    # ── Endpoint: DELETE /api/v1/annotations/<id> ─────────────────

    def _delete_annotation(self, user_id: str, annotation_id: str) -> None:
        """Delete one annotation. Author-only — same scope contract
        as edit. Cross-author / cross-user attempts return 404."""
        try:
            from engine.alert_annotations import delete_annotation
            ok = delete_annotation(
                annotation_id,
                user_id=user_id,
                author_user_id=user_id,
            )
            if not ok:
                _send_not_found(self)
                return
            _send_json(
                self,
                HTTPStatus.OK,
                {"deleted": True, "annotation_id": annotation_id},
            )
        except Exception as exc:
            logger.exception(
                f"api DELETE /annotations/<id> crashed: {exc}"
            )
            _send_internal_error(self)

    # ── Endpoints: /api/v1/rules/<id>/escalations + /escalations/<id> ─
    # Per-rule alert-escalation chains (schema v24). Four endpoints:
    #
    #   GET    /api/v1/rules/<rule_id>/escalations  → list the chain
    #   POST   /api/v1/rules/<rule_id>/escalations  → add/replace step
    #   DELETE /api/v1/rules/<rule_id>/escalations  → bulk-clear chain
    #   DELETE /api/v1/escalations/<chain_id>       → delete one step
    #
    # Per-user scoped via the bearer token's user_id — alice cannot
    # read / mutate / delete bob's chain even by guessing the rule_id
    # or chain_id. The POST handler ALSO validates the supplied
    # channel_id exists in the caller's delivery-channel set so a
    # mistyped id (or one pointing at another user's channel) is
    # rejected at the API boundary instead of failing silently at
    # dispatch time inside ``escalate_alert``.

    def _step_to_dict(self, step) -> dict:
        """Project an EscalationStep onto the wire shape. Centralised
        because list / add both need the same projection."""
        return {
            "chain_id":      step.chain_id,
            "rule_id":       step.rule_id,
            "user_id":       step.user_id,
            "step_number":   step.step_number,
            "after_minutes": step.after_minutes,
            "channel_id":    step.channel_id,
            "created_at":    step.created_at,
        }

    def _list_escalations(self, user_id: str, rule_id: str) -> None:
        """Return the chain for ``rule_id`` ordered by step_number ASC.
        Per-user scoped via bearer token — alice's chain on rule X is
        invisible to bob. An empty / unknown chain returns ``[]``
        (200), not 404 — the rule simply has no chain configured."""
        try:
            from engine.alert_escalation import get_escalation_chain
            chain = get_escalation_chain(rule_id, user_id=user_id)
            payload = [self._step_to_dict(s) for s in chain]
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(
                f"api GET /rules/<id>/escalations crashed: {exc}"
            )
            _send_internal_error(self)

    def _create_escalation_step(self, user_id: str, rule_id: str) -> None:
        """Persist (or replace) one step in a rule's chain.

        Body fields:
          * ``step_number``   (int, required) — 1-indexed.
          * ``after_minutes`` (int, required) — minutes from anchor.
          * ``channel_id``    (str, required) — delivery channel.

        Returns ``{"saved": True, "chain_id": "...", "step": {...}}``
        on success. Missing any required field → 400. A channel_id
        that does not exist in the caller's channel set → 400 (better
        UX than letting ``escalate_alert`` silently miss at runtime
        — a stale chain step is worse than a rejected POST). Engine
        failure → 500.
        """
        try:
            body = _read_json_body(self)
            if body is _BODY_BAD_CTYPE:
                _send_unsupported_media(self)
                return
            if body is _BODY_BAD_JSON:
                _send_bad_request(self, "malformed json")
                return
            if not isinstance(body, dict):
                _send_bad_request(self, "body must be a JSON object")
                return

            raw_step = body.get("step_number")
            if raw_step is None:
                _send_bad_request(self, "step_number is required")
                return
            try:
                step_number = int(raw_step)
            except (TypeError, ValueError):
                _send_bad_request(self, "step_number must be an int")
                return

            raw_after = body.get("after_minutes")
            if raw_after is None:
                _send_bad_request(self, "after_minutes is required")
                return
            try:
                after_minutes = int(raw_after)
            except (TypeError, ValueError):
                _send_bad_request(self, "after_minutes must be an int")
                return

            channel_id = body.get("channel_id")
            if not channel_id or not isinstance(channel_id, str):
                _send_bad_request(self, "channel_id is required")
                return

            # Validate the channel belongs to this user. The engine
            # would happily persist the chain row pointing at a
            # nonexistent channel — escalate_alert later finds the
            # mismatch and logs "channel not found" without bumping
            # the state machine. That's correct but the UX is worse
            # than rejecting the bogus reference at write time.
            from engine.alert_delivery import load_channels
            channels = load_channels(user_id=user_id)
            if not any(c.channel_id == channel_id for c in channels):
                _send_bad_request(
                    self,
                    f"channel_id not found in caller's channel set",
                )
                return

            from engine.alert_escalation import add_escalation_step
            step = add_escalation_step(
                rule_id=rule_id,
                user_id=user_id,
                step_number=step_number,
                after_minutes=after_minutes,
                channel_id=channel_id,
            )
            if step is None:
                _send_internal_error(self)
                return
            _send_json(
                self,
                HTTPStatus.OK,
                {
                    "saved":    True,
                    "chain_id": step.chain_id,
                    "step":     self._step_to_dict(step),
                },
            )
        except Exception as exc:
            logger.exception(
                f"api POST /rules/<id>/escalations crashed: {exc}"
            )
            _send_internal_error(self)

    def _delete_escalation_step(
        self, user_id: str, chain_id: str,
    ) -> None:
        """Delete one step from a chain by chain_id. Per-user scoped
        — cross-user attempts return 404 (no-leak contract identical
        to silences / annotations)."""
        try:
            from engine.alert_escalation import delete_escalation_step
            ok = delete_escalation_step(chain_id, user_id=user_id)
            if not ok:
                _send_not_found(self)
                return
            _send_json(
                self,
                HTTPStatus.OK,
                {"deleted": True, "chain_id": chain_id},
            )
        except Exception as exc:
            logger.exception(
                f"api DELETE /escalations/<id> crashed: {exc}"
            )
            _send_internal_error(self)

    def _delete_chain(self, user_id: str, rule_id: str) -> None:
        """Bulk-delete every step in a rule's chain. Per-user scoped.
        Returns ``{"deleted_steps": N}`` — 0 when the rule had no
        chain to start with (still 200; no-op DELETEs are idempotent
        and the caller can confirm by re-GETting the chain)."""
        try:
            from engine.alert_escalation import delete_chain
            removed = delete_chain(rule_id, user_id=user_id)
            _send_json(
                self,
                HTTPStatus.OK,
                {"deleted_steps": int(removed), "rule_id": rule_id},
            )
        except Exception as exc:
            logger.exception(
                f"api DELETE /rules/<id>/escalations crashed: {exc}"
            )
            _send_internal_error(self)

    # ── Endpoint: GET / PATCH /api/v1/notification-prefs ──────────

    def _get_notification_prefs(self, user_id: str) -> None:
        """Return the caller's notification prefs as JSON.

        The shape mirrors :class:`auth.notification_prefs.NotificationPrefs`.
        A user with no saved prefs gets the defaults (every field at
        its default) — the client cannot tell the difference between
        "never saved" and "saved-as-defaults", which matches the rest
        of the kv_state JSON-blob endpoints in this server.
        """
        try:
            from auth.notification_prefs import get_prefs
            prefs = get_prefs(user_id=user_id)
            payload = {
                "user_id":              prefs.user_id,
                "enabled":              prefs.enabled,
                "min_severity":         prefs.min_severity,
                "alert_type_filter":    prefs.alert_type_filter,
                "severity_channel_map": prefs.severity_channel_map,
                "quiet_during_hours":   (
                    list(prefs.quiet_during_hours)
                    if prefs.quiet_during_hours is not None
                    else None
                ),
            }
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(
                f"api GET /notification-prefs crashed: {exc}"
            )
            _send_internal_error(self)

    def _patch_notification_prefs(self, user_id: str) -> None:
        """Partial-update the caller's notification prefs.

        Body is a JSON object whose keys are any subset of the
        :class:`NotificationPrefs` field names. Unknown keys are
        silently ignored — same forward-compat shape as
        ``update_pref``. Returns the FULL updated prefs object so the
        client doesn't have to round-trip to GET to see the post-patch
        state.

        ``quiet_during_hours`` accepts ``null`` (clears the window) or
        a two-element list ``[start_hour, end_hour]``. Anything else
        falls back to ``null`` via the normalize helper inside the
        prefs module.
        """
        try:
            from auth.notification_prefs import (
                get_prefs,
                update_pref,
            )
            body = _read_json_body(self)
            if body is _BODY_BAD_CTYPE:
                _send_unsupported_media(self)
                return
            if body is _BODY_BAD_JSON:
                _send_bad_request(self, "malformed json")
                return
            if body is None:
                # Empty body — treat as no-op partial update (still
                # returns the current prefs so the client sees state).
                body = {}
            if not isinstance(body, dict):
                _send_bad_request(self, "body must be a JSON object")
                return

            # Forward only known keys — anything else gets silently
            # dropped at the update_pref boundary anyway, but skipping
            # them here keeps the audit-log detail clean.
            forwarded: dict[str, object] = {}
            for k in (
                "enabled",
                "min_severity",
                "alert_type_filter",
                "severity_channel_map",
                "quiet_during_hours",
            ):
                if k in body:
                    forwarded[k] = body[k]

            ok = update_pref(user_id=user_id, **forwarded)
            if not ok:
                _send_internal_error(self)
                return

            prefs = get_prefs(user_id=user_id)
            payload = {
                "user_id":              prefs.user_id,
                "enabled":              prefs.enabled,
                "min_severity":         prefs.min_severity,
                "alert_type_filter":    prefs.alert_type_filter,
                "severity_channel_map": prefs.severity_channel_map,
                "quiet_during_hours":   (
                    list(prefs.quiet_during_hours)
                    if prefs.quiet_during_hours is not None
                    else None
                ),
            }
            _send_json(self, HTTPStatus.OK, payload)
        except Exception as exc:
            logger.exception(
                f"api PATCH /notification-prefs crashed: {exc}"
            )
            _send_internal_error(self)


# ─────────────────────────────────────────────────────────────────────────────
#  Server lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def serve(host: str = "0.0.0.0", port: int = 8503) -> HTTPServer:
    """Start an HTTPServer bound to ``host:port`` and run forever.

    Returns the ``HTTPServer`` instance only in tests where the caller
    needs to ``shutdown()`` it from another thread. In production
    ``main()`` blocks here and the server runs until SIGTERM.
    """
    server = HTTPServer((host, port), APIHandler)
    logger.info(f"api server bound to http://{host}:{port}")
    server.serve_forever()
    return server


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint. Returns the exit code: 0 normal, 1 on bind error."""
    parser = argparse.ArgumentParser(
        description="Ship Tracker read-only API server (stdlib http.server)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("API_HOST", "0.0.0.0"),
        help="Bind interface (default 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("API_PORT", "8503")),
        help="Bind port (default 8503)",
    )
    args = parser.parse_args(argv)

    try:
        serve(args.host, args.port)
        return 0
    except OSError as exc:
        logger.error(f"api server failed to bind {args.host}:{args.port}: {exc}")
        return 1
    except KeyboardInterrupt:
        logger.info("api server received KeyboardInterrupt, exiting")
        return 0


if __name__ == "__main__":
    sys.exit(main())
