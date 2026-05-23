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
* **READ-ONLY + one ack.** The endpoints map 1:1 to existing engine
  reads. The single exception is ``POST /api/v1/alerts/<id>/ack`` —
  acking an alert is the smallest write the API supports because
  there's no read equivalent (you can't observe "this alert was
  acked" without flipping the bit). Every other write surface is
  out of scope; deliberate gatekeeping so this endpoint doesn't
  accrue mutation creep.
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


# ─────────────────────────────────────────────────────────────────────────────
#  Path patterns — compiled once at import so per-request dispatch is
#  a single regex match, not a chain of string startswith checks.
# ─────────────────────────────────────────────────────────────────────────────

_RE_ALERTS_LIST = re.compile(r"^/api/v1/alerts/?$")
_RE_ALERT_ONE = re.compile(r"^/api/v1/alerts/([^/]+)/?$")
_RE_ALERT_ACK = re.compile(r"^/api/v1/alerts/([^/]+)/ack/?$")
_RE_REPORTS_LIST = re.compile(r"^/api/v1/reports/?$")
_RE_REPORT_HTML = re.compile(r"^/api/v1/reports/([^/]+)/html/?$")
_RE_TELEMETRY_LLM = re.compile(r"^/api/v1/telemetry/llm/?$")
_RE_TELEMETRY_PERF = re.compile(r"^/api/v1/telemetry/perf/?$")
_RE_HEALTH = re.compile(r"^/api/v1/health/?$")


# ─────────────────────────────────────────────────────────────────────────────
#  Dispatching handler — a single BaseHTTPRequestHandler subclass that
#  routes inside ``do_GET`` / ``do_POST`` by regex match on the path.
# ─────────────────────────────────────────────────────────────────────────────

class APIHandler(BaseHTTPRequestHandler):
    """Routes incoming requests to per-endpoint methods.

    Routing rules:

      GET  /api/v1/alerts                 → _list_alerts          (auth)
      GET  /api/v1/alerts/<id>            → _get_alert            (auth)
      POST /api/v1/alerts/<id>/ack        → _ack_alert            (auth)
      GET  /api/v1/reports                → _list_reports         (auth)
      GET  /api/v1/reports/<id>/html      → _get_report_html      (auth)
      GET  /api/v1/telemetry/llm          → _get_llm_telemetry    (auth)
      GET  /api/v1/telemetry/perf         → _get_perf_telemetry   (auth)
      GET  /api/v1/health                 → _health               (public)

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
                _RE_REPORTS_LIST, _RE_REPORT_HTML,
                _RE_TELEMETRY_LLM, _RE_TELEMETRY_PERF,
                _RE_HEALTH,
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

            if _RE_ALERTS_LIST.match(path):
                self._list_alerts(user_id, query)
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
                self._get_report_html(user_id, m.group(1))
                return
            if _RE_TELEMETRY_LLM.match(path):
                self._get_llm_telemetry(user_id, query)
                return
            if _RE_TELEMETRY_PERF.match(path):
                self._get_perf_telemetry(query)
                return

            # GET on /api/v1/alerts/<id>/ack — this IS a known route
            # but only under POST. 405 it.
            if _RE_ALERT_ACK.match(path):
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

            m = _RE_ALERT_ACK.match(path)
            if m:
                self._ack_alert(user_id, m.group(1))
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

    def _dispatch_unknown_method(self) -> None:
        """For PUT / DELETE / PATCH / HEAD: 405 if the path is known
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

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch_unknown_method()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch_unknown_method()

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

    def _get_report_html(self, user_id: str, report_id: str) -> None:
        """Return the raw HTML for a saved report. 404 when the
        report does not exist in the caller's scope (which collapses
        the unknown-id and cross-user cases into the same response,
        matching ``load_report_html``'s contract)."""
        try:
            from utils.report_history import load_report_html
            html = load_report_html(report_id, user_id=user_id)
            if html is None:
                _send_not_found(self)
                return
            _send_html(self, HTTPStatus.OK, html)
        except Exception as exc:
            logger.exception(f"api /reports/<id>/html crashed: {exc}")
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
